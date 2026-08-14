"""One-shot worker adapter for private, bounded Tesseract quality review."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import stat
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol, cast
from uuid import UUID

from paperworkshell.domain.ocr import TRUSTED_TESSERACT_VERSION
from paperworkshell.domain.ocr_languages import resolve_ocr_language_set
from paperworkshell.domain.quality_ocr_review import (
    MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
    MAX_QUALITY_OCR_REVIEW_TSV_ROWS,
    MAX_QUALITY_OCR_REVIEW_WORDS,
    QUALITY_OCR_REVIEW_TSV_COLUMNS,
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewStatus,
    encode_quality_ocr_review_report,
    reject_quality_ocr_review,
)
from paperworkshell.ocr.tesseract_runner import (
    TesseractCommandRunner,
    TesseractRunnerError,
    TesseractRunnerErrorCode,
    is_tesseract_completed_process,
)
from paperworkshell.worker.operations import (
    OperationContext,
    OperationHandler,
    resource_limited_operation_result,
    timed_out_operation_result,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    QUALITY_OCR_REVIEW_CAPABILITY,
    QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
    QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
    QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
    QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX,
    QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
    CapabilityDescriptor,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    ProgressStage,
    ProgressUnit,
    ScalarValueType,
)
from paperworkshell.worker.protocol.operation_scope import (
    QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS,
)

PWS_TESSERACT_EXECUTABLE_ENVIRONMENT: Final = "PWS_TESSERACT_EXECUTABLE"
PWS_TESSDATA_ROOT_ENVIRONMENT: Final = "PWS_TESSDATA_ROOT"
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_FILE_MAP_READ: Final = 0x0000_0004
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_MAX_TSV_LINE_BYTES: Final = 8_192
_PPM_HEADER_MAX_BYTES: Final = 32
_CONFIDENCE: Final = re.compile(rb"(?:-1|(?:0|[1-9][0-9]?|100)(?:\.[0-9]{1,6})?)\Z")
_INTEGER: Final = re.compile(rb"-?[0-9]{1,10}\Z")
_TSV_HEADER: Final = (
    b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


@dataclass(frozen=True, slots=True, repr=False)
class QualityOcrReviewOperationRequest:
    """Already protocol-validated handles and exact sample correlation."""

    sample_handle: int = field(repr=False)
    result_handle: int = field(repr=False)
    page_id: UUID
    manifest_revision: int
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    sample_byte_size: int
    sample_sha256: str = field(repr=False)
    presentation_sha256: str = field(repr=False)
    language_set_id: str
    profile_id: str


class QualityOcrReviewAdapter(Protocol):
    """Injected adapter that consumes both transferred authorities."""

    def discard_handles(
        self,
        context: OperationContext,
        request: QualityOcrReviewOperationRequest,
    ) -> None: ...

    def review(
        self,
        context: OperationContext,
        request: QualityOcrReviewOperationRequest,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class QualityOcrReviewOperationHandler(OperationHandler):
    """Coordinate one page and publish only a content-free result frame."""

    adapter: QualityOcrReviewAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return QUALITY_OCR_REVIEW_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _operation_request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()
        try:
            self.adapter.review(context, request)
        except QualityOcrReviewError as error:
            if error.code is QualityOcrReviewErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is QualityOcrReviewErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (), None)


@dataclass(frozen=True, slots=True)
class _OperationControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        try:
            value = self.context.deadline_expired()
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.deadline",
            )
        if type(value) is not bool:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.deadline",
            )
        return value


@dataclass(slots=True, repr=False)
class _MappedInputSection:
    """Own one exact read-only view of the broker-created sample section."""

    _handle: int
    _view: int
    byte_count: int
    _closed: bool = False

    @classmethod
    def adopt(cls, handle: int, byte_count: int) -> _MappedInputSection:
        if (
            type(handle) is not int
            or handle <= 0
            or type(byte_count) is not int
            or not 1 <= byte_count <= _MAX_WINDOWS_SIZE_T
        ):
            _close_native_handle_no_raise(handle)
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.sample_handle",
            )
        try:
            _require_non_inheritable(handle)
            view = _map_read_section(handle, byte_count)
        except QualityOcrReviewError:
            _close_native_handle_no_raise(handle)
            raise
        except Exception:
            _close_native_handle_no_raise(handle)
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.sample_map",
            )
        if view <= 0:
            _close_native_handle_no_raise(handle)
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.sample_map",
            )
        return cls(handle, view, byte_count)

    def read_exact(self) -> bytes:
        if self._closed or self._view <= 0:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.sample_state",
            )
        try:
            return ctypes.string_at(self._view, self.byte_count)
        except MemoryError:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                "quality_ocr.sample_read",
            )
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.sample_read",
            )

    def __enter__(self) -> _MappedInputSection:
        if self._closed:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.sample_state",
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        view = self._view
        handle = self._handle
        self._view = 0
        self._handle = 0
        self._closed = True
        unmapped = _unmap_view(view)
        handle_closed = _close_native_handle(handle)
        if not unmapped or not handle_closed:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.sample_cleanup",
            )


@dataclass(frozen=True, slots=True, repr=False)
class WindowsQualityOcrReviewAdapter:
    """Inspect one bounded RGBA8 sample using only admitted local Tesseract."""

    executable: Path = field(repr=False)
    tessdata_root: Path = field(repr=False)
    installed_languages: frozenset[str]
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executable, Path)
            or not self.executable.is_absolute()
            or not isinstance(self.tessdata_root, Path)
            or not self.tessdata_root.is_absolute()
            or type(self.installed_languages) is not frozenset
            or not self.installed_languages
            or any(type(code) is not str for code in self.installed_languages)
            or not callable(getattr(self.private_temp_storage, "require_capacity", None))
            or not callable(getattr(self.private_temp_storage, "open_named", None))
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.adapter",
            )

    def discard_handles(
        self,
        context: OperationContext,
        request: QualityOcrReviewOperationRequest,
    ) -> None:
        del context
        try:
            _close_native_handle_no_raise(request.sample_handle)
        finally:
            _close_native_handle_no_raise(request.result_handle)

    def review(
        self,
        context: OperationContext,
        request: QualityOcrReviewOperationRequest,
    ) -> None:
        result_handle = request.result_handle
        report: QualityOcrReviewReport | None = None
        try:
            with _MappedInputSection.adopt(
                request.sample_handle,
                request.sample_byte_size,
            ) as sample_section:
                sample = sample_section.read_exact()
                if _sha256(sample) != request.sample_sha256:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.sample_digest",
                    )
                report = self._review_sample(context, request, sample)
                del sample
            _require_active(_OperationControl(context))
            payload = encode_quality_ocr_review_report(report)
            selected_result_handle = result_handle
            result_handle = 0
            _write_result_pipe(selected_result_handle, payload)
        except QualityOcrReviewError:
            raise
        except MemoryError, RecursionError:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                "quality_ocr.resources",
            )
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.adapter",
            )
        finally:
            del report
            if result_handle > 0:
                _close_native_handle_no_raise(result_handle)

    def _review_sample(
        self,
        context: OperationContext,
        request: QualityOcrReviewOperationRequest,
        sample: bytes,
    ) -> QualityOcrReviewReport:
        control = _OperationControl(context)
        _require_active(control)
        language_codes = resolve_ocr_language_set(request.language_set_id)
        if not set(language_codes).issubset(self.installed_languages):
            return _report(context, request, QualityOcrReviewStatus.FAILED, 0, None)
        temp_root = _materialize_worker_private_temp()
        try:
            self.private_temp_storage.require_capacity(
                temp_root,
                context.limits.temporary_storage_bytes,
            )
        except WorkerPrivateTempStorageError:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                "quality_ocr.temp_capacity",
            )
        input_path: Path | None = None
        cleanup_failed = False
        try:
            try:
                input_stream, input_path = self.private_temp_storage.open_named(
                    temp_root,
                    suffix=".ppm",
                )
            except WorkerPrivateTempStorageError:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                    "quality_ocr.temp_create",
                )
            with input_stream:
                _write_ppm(input_stream, request, sample, control)
            runner = TesseractCommandRunner(
                executable=self.executable,
                tessdata_directory=self.tessdata_root,
                temporary_directory=temp_root,
                control=control,
                maximum_output_bytes=context.limits.output_bytes,
            )
            try:
                completed = runner.run(
                    (
                        "tesseract",
                        input_path,
                        "stdout",
                        "-l",
                        "+".join(language_codes),
                        "--psm",
                        "6",
                        "tsv",
                    ),
                    check=False,
                )
            except TesseractRunnerError as error:
                if error.code is TesseractRunnerErrorCode.CANCELLED:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.CANCELLED,
                        "quality_ocr.engine",
                    )
                if error.code is TesseractRunnerErrorCode.TIMED_OUT:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.TIMED_OUT,
                        "quality_ocr.engine",
                    )
                if error.code is TesseractRunnerErrorCode.OUTPUT_LIMIT:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                        "quality_ocr.engine_output",
                    )
                if error.code in {
                    TesseractRunnerErrorCode.UNAVAILABLE,
                }:
                    return _report(
                        context,
                        request,
                        QualityOcrReviewStatus.FAILED,
                        0,
                        None,
                    )
                if error.code is TesseractRunnerErrorCode.FAILED:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.INTERNAL_ERROR,
                        "quality_ocr.engine",
                    )
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.engine",
                )
            if (
                not is_tesseract_completed_process(completed)
                or type(completed.returncode) is not int
            ):
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.engine_result",
                )
            if completed.returncode != 0:
                del completed
                return _report(
                    context,
                    request,
                    QualityOcrReviewStatus.FAILED,
                    0,
                    None,
                )
            _require_active(control)
            raw_tsv = completed.stdout
            del completed
            if type(raw_tsv) is not bytes:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.tsv_output",
                )
            try:
                status, word_count, mean_confidence = _summarize_private_tsv_payload(
                    raw_tsv,
                    maximum_bytes=context.limits.output_bytes,
                    control=control,
                )
            finally:
                del raw_tsv
            return _report(context, request, status, word_count, mean_confidence)
        finally:
            for path in (input_path,):
                if path is None:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.temp_cleanup",
                )


def summarize_quality_ocr_tsv(
    stream: BinaryIO,
    *,
    maximum_bytes: int = MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
    control: _OperationControl | None = None,
) -> tuple[QualityOcrReviewStatus, int, int | None]:
    """Parse only word-level numeric confidence while discarding every text field."""

    if (
        not callable(getattr(stream, "readline", None))
        or type(maximum_bytes) is not int
        or not 1 <= maximum_bytes <= MAX_QUALITY_OCR_REVIEW_TSV_BYTES
    ):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.MODEL_INVALID,
            "quality_ocr.tsv_input",
        )
    consumed = 0
    row_count = 0
    word_count = 0
    confidence_total_milli = 0
    while True:
        if control is not None:
            _require_active(control)
        try:
            line = stream.readline(_MAX_TSV_LINE_BYTES + 1)
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.tsv_read",
            )
        if type(line) is not bytes:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.tsv_read",
            )
        if not line:
            break
        consumed += len(line)
        row_count += 1
        if (
            consumed > maximum_bytes
            or len(line) > _MAX_TSV_LINE_BYTES
            or row_count > MAX_QUALITY_OCR_REVIEW_TSV_ROWS
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                "quality_ocr.tsv_limit",
            )
        stripped = line.rstrip(b"\r\n")
        if row_count == 1:
            if stripped != _TSV_HEADER:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.tsv_header",
                )
            continue
        columns = stripped.split(b"\t")
        if len(columns) != QUALITY_OCR_REVIEW_TSV_COLUMNS:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.tsv_columns",
            )
        level = _parse_integer(columns[0], minimum=1, maximum=5)
        word_number = _parse_integer(columns[5], minimum=0, maximum=MAX_QUALITY_OCR_REVIEW_WORDS)
        confidence_milli = _parse_confidence_milli(columns[10])
        # Inspect the final field only for bounded non-whitespace presence. It
        # is never decoded, retained across iterations, returned, or logged.
        word_present = bool(columns[11].strip())
        if level == 5 and word_number > 0 and confidence_milli >= 0 and word_present:
            word_count += 1
            if word_count > MAX_QUALITY_OCR_REVIEW_WORDS:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                    "quality_ocr.tsv_words",
                )
            confidence_total_milli += confidence_milli
        del columns, stripped, line
    if row_count < 1:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_empty",
        )
    if word_count == 0:
        return QualityOcrReviewStatus.NO_TEXT, 0, None
    return (
        QualityOcrReviewStatus.SUCCEEDED,
        word_count,
        confidence_total_milli // word_count,
    )


def _summarize_private_tsv_payload(
    payload: bytes,
    *,
    maximum_bytes: int,
    control: _OperationControl,
) -> tuple[QualityOcrReviewStatus, int, int | None]:
    if type(payload) is not bytes or not payload:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_output",
        )
    if len(payload) > maximum_bytes:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
            "quality_ocr.tsv_limit",
        )
    stream = BytesIO(payload)
    del payload
    try:
        return summarize_quality_ocr_tsv(
            cast(BinaryIO, stream),
            maximum_bytes=maximum_bytes,
            control=control,
        )
    finally:
        stream.close()


def _write_ppm(
    destination: BinaryIO,
    request: QualityOcrReviewOperationRequest,
    sample: bytes,
    control: _OperationControl,
) -> None:
    header = f"P6\n{request.width_pixels} {request.height_pixels}\n255\n".encode("ascii")
    if len(header) > _PPM_HEADER_MAX_BYTES:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.ppm_header",
        )
    _write_exact(destination, header)
    packed_row_bytes = request.width_pixels * 4
    for row in range(request.height_pixels):
        _require_active(control)
        start = row * request.row_stride_bytes
        rgba = sample[start : start + packed_row_bytes]
        if len(rgba) != packed_row_bytes or rgba[3::4].count(255) != request.width_pixels:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.sample_alpha",
            )
        rgb = bytearray(request.width_pixels * 3)
        rgb[0::3] = rgba[0::4]
        rgb[1::3] = rgba[1::4]
        rgb[2::3] = rgba[2::4]
        _write_exact(destination, bytes(rgb))
        del rgb, rgba
    try:
        destination.flush()
    except OSError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
            "quality_ocr.ppm_flush",
        )
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.ppm_flush",
        )
    _require_active(control)


def _write_exact(destination: BinaryIO, payload: bytes) -> None:
    cursor = 0
    try:
        while cursor < len(payload):
            written = destination.write(payload[cursor:])
            if type(written) is not int or not 1 <= written <= len(payload) - cursor:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.temp_write",
                )
            cursor += written
    except QualityOcrReviewError:
        raise
    except OSError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
            "quality_ocr.temp_write",
        )
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.temp_write",
        )


def _parse_integer(value: bytes, *, minimum: int, maximum: int) -> int:
    if _INTEGER.fullmatch(value) is None:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_integer",
        )
    try:
        selected = int(value)
    except ValueError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_integer",
        )
    if not minimum <= selected <= maximum:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_integer",
        )
    return selected


def _parse_confidence_milli(value: bytes) -> int:
    if _CONFIDENCE.fullmatch(value) is None:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_confidence",
        )
    if value == b"-1":
        return -1
    whole, separator, fraction = value.partition(b".")
    whole_value = int(whole)
    if not separator:
        return whole_value * 1_000
    if whole_value == 100 and any(digit != ord("0") for digit in fraction):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_confidence",
        )
    # Tesseract v5 emits six decimals. Round to a content-free milli-integer.
    padded = fraction.ljust(4, b"0")
    milli = int(padded[:3])
    if padded[3:4] >= b"5":
        milli += 1
    result = whole_value * 1_000 + milli
    if result > 100_000:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.tsv_confidence",
        )
    return result


def _report(
    context: OperationContext,
    request: QualityOcrReviewOperationRequest,
    status: QualityOcrReviewStatus,
    recognized_word_count: int,
    mean_confidence_milli: int | None,
) -> QualityOcrReviewReport:
    return QualityOcrReviewReport(
        operation_id=context.operation_id,
        case_id=context.case_id,
        manifest_revision=request.manifest_revision,
        page_id=request.page_id,
        sample_sha256=request.sample_sha256,
        presentation_sha256=request.presentation_sha256,
        language_set_id=request.language_set_id,
        profile_id=request.profile_id,
        status=status,
        recognized_word_count=recognized_word_count,
        mean_confidence_milli=mean_confidence_milli,
    )


def _operation_request(context: OperationContext) -> QualityOcrReviewOperationRequest:
    values = {parameter.name: parameter for parameter in context.parameters}

    def integer(name: str) -> int:
        parameter = values[name]
        if parameter.value_type is not ScalarValueType.INTEGER or type(parameter.value) is not int:
            raise ValueError("validated OCR-review integer is unavailable")
        return parameter.value

    def token(name: str) -> str:
        parameter = values[name]
        if parameter.value_type is not ScalarValueType.TOKEN or type(parameter.value) is not str:
            raise ValueError("validated OCR-review token is unavailable")
        return parameter.value

    def digest(name: str) -> str:
        value = token(name)
        if (
            not value.startswith(QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                value[len(QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX) :],
            )
            is None
        ):
            raise ValueError("validated OCR-review digest is unavailable")
        return value[len(QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX) :]

    page = values[QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER]
    if page.value_type is not ScalarValueType.UUID or type(page.value) is not UUID:
        raise ValueError("validated OCR-review page is unavailable")
    return QualityOcrReviewOperationRequest(
        sample_handle=integer(QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER),
        result_handle=integer(QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER),
        page_id=page.value,
        manifest_revision=integer(QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER),
        width_pixels=integer(QUALITY_OCR_REVIEW_WIDTH_PARAMETER),
        height_pixels=integer(QUALITY_OCR_REVIEW_HEIGHT_PARAMETER),
        row_stride_bytes=integer(QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER),
        sample_byte_size=integer(QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER),
        sample_sha256=digest(QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER),
        presentation_sha256=digest(QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER),
        language_set_id=token(QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER),
        profile_id=token(QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER),
    )


def _require_active(control: _OperationControl) -> None:
    if control.cancellation_requested():
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.CANCELLED,
            "quality_ocr.operation",
        )
    if control.deadline_expired():
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.operation",
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_result_pipe(handle: int, payload: bytes) -> None:
    if type(handle) is not int or handle <= 0 or type(payload) is not bytes or not payload:
        _close_native_handle_no_raise(handle)
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.result_pipe",
        )
    try:
        _require_non_inheritable(handle)
        kernel32 = _kernel32()
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        storage = ctypes.create_string_buffer(payload)
        cursor = 0
        while cursor < len(payload):
            remaining = len(payload) - cursor
            written = wintypes.DWORD()
            if not write_file(
                wintypes.HANDLE(handle),
                ctypes.byref(storage, cursor),
                remaining,
                ctypes.byref(written),
                None,
            ):
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.result_write",
                )
            count = int(written.value)
            if not 1 <= count <= remaining:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.result_write",
                )
            cursor += count
    except QualityOcrReviewError:
        raise
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.result_write",
        )
    finally:
        _close_native_handle_no_raise(handle)


def _materialize_worker_private_temp() -> Path:
    try:
        local_value = os.environ.get("LOCALAPPDATA")
        temp_value = os.environ.get("TEMP")
        tmp_value = os.environ.get("TMP")
        if (
            type(local_value) is not str
            or type(temp_value) is not str
            or type(tmp_value) is not str
            or not local_value
            or not temp_value
            or not tmp_value
            or "\x00" in local_value
            or "\x00" in temp_value
            or "\x00" in tmp_value
        ):
            raise ValueError
        local_root = Path(local_value)
        temp_root = Path(temp_value)
        tmp_root = Path(tmp_value)
        if (
            not local_root.is_absolute()
            or not temp_root.is_absolute()
            or not tmp_root.is_absolute()
            or os.path.normcase(os.path.abspath(temp_root))
            != os.path.normcase(os.path.abspath(tmp_root))
            or os.path.normcase(os.path.abspath(temp_root.parent))
            != os.path.normcase(os.path.abspath(local_root))
            or temp_root.name.casefold() != "temp"
            or os.fspath(local_root).startswith(("\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
        ):
            raise ValueError
        temp_root.mkdir(parents=True, exist_ok=True)
        for candidate in (local_root, temp_root):
            information = os.lstat(candidate)
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError
        return temp_root
    except OSError, TypeError, ValueError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.temp_scope",
        )


def _require_non_inheritable(handle: int) -> None:
    try:
        inheritable = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.handle_identity",
        )
    if inheritable:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.handle_identity",
        )


def _kernel32() -> Any:
    if os.name != "nt":
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
            "quality_ocr.platform",
        )
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _map_read_section(handle: int, byte_count: int) -> int:
    kernel32 = _kernel32()
    map_view = kernel32.MapViewOfFile
    map_view.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    map_view.restype = ctypes.c_void_p
    mapped = map_view(wintypes.HANDLE(handle), _FILE_MAP_READ, 0, 0, byte_count)
    return int(mapped or 0)


def _unmap_view(view: int) -> bool:
    if view <= 0:
        return True
    try:
        kernel32 = _kernel32()
        unmap = kernel32.UnmapViewOfFile
        unmap.argtypes = (ctypes.c_void_p,)
        unmap.restype = wintypes.BOOL
        return bool(unmap(ctypes.c_void_p(view)))
    except BaseException:
        return False


def _close_native_handle(handle: int) -> bool:
    if handle <= 0:
        return True
    kernel32 = _kernel32()
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    return bool(close(wintypes.HANDLE(handle)))


def _close_native_handle_no_raise(handle: int) -> None:
    if type(handle) is not int or handle <= 0 or os.name != "nt":
        return
    try:
        _close_native_handle(handle)
    except BaseException:
        pass


@dataclass(frozen=True, slots=True)
class _EngineAdmissionControl:
    deadline: float

    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return time.monotonic() >= self.deadline


def build_quality_ocr_review_operation_handler() -> QualityOcrReviewOperationHandler | None:
    """Advertise OCR review only after the exact local Tesseract closure is usable."""

    executable = os.environ.get(PWS_TESSERACT_EXECUTABLE_ENVIRONMENT)
    tessdata = os.environ.get(PWS_TESSDATA_ROOT_ENVIRONMENT)
    if (
        type(executable) is not str
        or type(tessdata) is not str
        or not executable
        or not tessdata
        or "\x00" in executable
        or "\x00" in tessdata
    ):
        return None
    executable_path = Path(executable)
    tessdata_path = Path(tessdata)
    try:
        if (
            os.name != "nt"
            or not executable_path.is_absolute()
            or not tessdata_path.is_absolute()
            or not executable_path.is_file()
            or not tessdata_path.is_dir()
        ):
            return None
        temp_root = _materialize_worker_private_temp()
        runner = TesseractCommandRunner(
            executable_path,
            tessdata_path,
            temp_root,
            _EngineAdmissionControl(
                time.monotonic() + QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS
            ),
        )
        if runner.version() != TRUSTED_TESSERACT_VERSION:
            return None
        installed = runner.languages()
        if "eng" not in installed:
            return None
    except QualityOcrReviewError, TesseractRunnerError, OSError, ValueError:
        return None
    return QualityOcrReviewOperationHandler(
        WindowsQualityOcrReviewAdapter(
            executable_path,
            tessdata_path,
            installed,
        )
    )
