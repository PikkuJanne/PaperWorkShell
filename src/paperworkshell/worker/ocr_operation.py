"""Production worker operation for bounded, handle-brokered local OCR."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol, cast
from uuid import uuid4

from paperworkshell.domain.image_pages import ImagePageError
from paperworkshell.domain.ocr import (
    MAX_OCR_WORKER_REPORT_BYTES,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
    OcrErrorCode,
    OcrSourceKind,
    OcrWorkerReport,
    encode_ocr_worker_report,
    ocr_engine_profile_sha256,
    reject_ocr,
)
from paperworkshell.ocr.ocrmypdf_adapter import (
    OcrAdapterProgressPhase,
    OcrAdapterResult,
    OcrMyPdfAdapter,
    loaded_ocrmypdf_version,
)
from paperworkshell.ocr.tesseract_runner import (
    TesseractCommandRunner,
    TesseractRunnerError,
)
from paperworkshell.worker.image_normalize_operation import (
    _close_descriptor_no_raise,
    _close_native_handle_no_raise,
    _MappedOutputSection,
    _require_non_inheritable,
)
from paperworkshell.worker.operations import (
    OcrProcessOperationHandler,
    OcrProcessOperationRequest,
    OperationContext,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    OCR_PROCESS_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    OperationProgressPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

PWS_TESSERACT_EXECUTABLE_ENVIRONMENT: Final = "PWS_TESSERACT_EXECUTABLE"
PWS_TESSDATA_ROOT_ENVIRONMENT: Final = "PWS_TESSDATA_ROOT"
OCR_DEFAULT_LANGUAGE_SET_ID: Final = "latin-default-v1"
OCR_OPERATION_MAX_RESULT_BYTES: Final = 4_096
OCRMY_PDF_ENGINE_VERSION: Final = TRUSTED_OCRMYPDF_VERSION
TESSERACT_ENGINE_VERSION: Final = TRUSTED_TESSERACT_VERSION

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_DELETE: Final = 0x0001_0000
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_SYNCHRONIZE: Final = 0x0010_0000
_FILE_SHARE_READ: Final = 0x0000_0001
_OPEN_EXISTING: Final = 3
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x0200_0000
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_OPERATION_TEMP_CREATE_ATTEMPTS: Final = 4
_MAXIMUM_TEMP_ENTRIES: Final = 100_000
_ENGINE_ADMISSION_TIMEOUT_SECONDS: Final = 10.0


class OcrCandidateProcessor(Protocol):
    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        temporary_directory: Path,
        languages: tuple[str, ...],
        profile_id: str,
        expected_page_count: int,
        maximum_output_bytes: int,
        maximum_image_pixels: int,
        maximum_temporary_storage_bytes: int,
        control: _OperationControl,
        progress: _ProgressReporter,
    ) -> OcrAdapterResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class _OperationControl:
    context: OperationContext = field(repr=False)

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        try:
            value = self.context.deadline_expired()
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.deadline_probe")
        if type(value) is not bool:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.deadline_probe")
        return value


@dataclass(slots=True, repr=False)
class _ProgressReporter:
    context: OperationContext = field(repr=False)
    total_pages: int
    completed_pages: int = 0

    def __call__(
        self,
        phase: OcrAdapterProgressPhase,
        completed_pages: int,
        total_pages: int,
    ) -> None:
        if (
            type(phase) is not OcrAdapterProgressPhase
            or type(completed_pages) is not int
            or type(total_pages) is not int
            or total_pages != self.total_pages
            or not self.completed_pages <= completed_pages <= total_pages
        ):
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.progress")
        self.completed_pages = completed_pages
        try:
            self.context.report_progress(
                OperationProgressPayload(
                    ProgressStage.PROCESSING,
                    completed_pages,
                    total_pages,
                    ProgressUnit.PAGES,
                )
            )
        except OcrError:
            raise
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.progress_report")


@dataclass(slots=True, repr=False)
class _OwnedHandles:
    values: set[int] = field(repr=False)

    @classmethod
    def initial(cls, request: OcrProcessOperationRequest) -> _OwnedHandles:
        return cls({request.source_handle, request.output_handle, request.result_handle})

    def transfer(self, value: int) -> int:
        if value not in self.values:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.handle_ownership")
        self.values.remove(value)
        return value

    def close_all(self) -> None:
        values = tuple(self.values)
        self.values.clear()
        for value in values:
            _close_native_handle_no_raise(value)


@dataclass(frozen=True, slots=True, repr=False)
class WindowsOcrProcessAdapter:
    """Stage one immutable source, run private OCR, and publish a verified candidate."""

    candidate_processor: OcrCandidateProcessor = field(repr=False)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.candidate_processor, "process", None)) or any(
            not callable(getattr(self.private_temp_storage, member, None))
            for member in ("require_capacity", "open_named")
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.worker_adapter")

    def discard_handles(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> None:
        del context
        _OwnedHandles.initial(request).close_all()

    def process(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> CaseObjectReference:
        handles = _OwnedHandles.initial(request)
        source_descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.platform")
            for value, suffix in (
                (request.source_handle, "source"),
                (request.output_handle, "output"),
                (request.result_handle, "result"),
            ):
                _require_ocr_non_inheritable(value, f"ocr.{suffix}_handle")
            reference = _source_reference(context, request)
            source_handle = handles.transfer(request.source_handle)
            try:
                source_descriptor = msvcrt.open_osfhandle(
                    source_handle,
                    os.O_RDONLY | os.O_BINARY,
                )
            except OSError, OverflowError, ValueError:
                _close_native_handle_no_raise(source_handle)
                reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_open")
            with os.fdopen(source_descriptor, "rb", buffering=0, closefd=True) as source:
                source_descriptor = None
                return self._process_open_source(
                    context,
                    request,
                    reference,
                    cast(BinaryIO, source),
                    handles,
                )
        except OcrError:
            raise
        except ImagePageError:
            reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_section")
        except MemoryError, RecursionError:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.worker_resources")
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.worker_adapter")
        finally:
            if source_descriptor is not None:
                _close_descriptor_no_raise(source_descriptor)
            handles.close_all()

    def _process_open_source(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
        reference: CaseObjectReference,
        source: BinaryIO,
        handles: _OwnedHandles,
    ) -> CaseObjectReference:
        expected_size = reference.expected_byte_size
        expected_digest = reference.expected_sha256
        if expected_size is None or expected_digest is None:
            reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_reference")
        try:
            source_info = os.fstat(source.fileno())
        except OSError, OverflowError, ValueError:
            reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_identity")
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
            reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_identity")
        control = _OperationControl(context)
        _require_active(control)
        root = _materialize_private_temp()
        try:
            self.private_temp_storage.require_capacity(
                root,
                context.limits.temporary_storage_bytes,
            )
        except WorkerPrivateTempStorageError:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_capacity")
        with _private_operation_temp(root) as operation_root:
            source_path = operation_root / f"source-{uuid4().hex}{_source_suffix(request)}"
            candidate_path = operation_root / f"candidate-{uuid4().hex}.pdf"
            _stage_source(
                source,
                source_path,
                expected_size=expected_size,
                expected_digest=expected_digest,
                control=control,
            )
            progress = _ProgressReporter(context, request.page_count)
            result = self.candidate_processor.process(
                source_path,
                candidate_path,
                temporary_directory=operation_root,
                languages=_languages_for_set(request.language_set_id),
                profile_id=request.profile_id,
                expected_page_count=request.page_count,
                maximum_output_bytes=context.limits.output_bytes,
                maximum_image_pixels=context.limits.pixel_count,
                maximum_temporary_storage_bytes=context.limits.temporary_storage_bytes,
                control=control,
                progress=progress,
            )
            _require_operation_tree_bound(
                operation_root,
                context.limits.temporary_storage_bytes,
            )
            _require_active(control)
            output_handle = handles.transfer(request.output_handle)
            section_result = _copy_candidate_to_section(
                candidate_path,
                output_handle,
                capacity=context.limits.output_bytes,
                expected=result,
                control=control,
            )
            report = OcrWorkerReport(
                operation_id=context.operation_id,
                source_id=reference.object_id,
                output_object_id=request.output_object_id,
                source_kind=OcrSourceKind(request.source_content_kind),
                page_count=request.page_count,
                processed_page_count=result.page_count,
                language_set_id=request.language_set_id,
                profile_id=request.profile_id,
                ocrmypdf_version=result.ocrmypdf_version,
                tesseract_version=result.tesseract_version,
                engine_profile_sha256=ocr_engine_profile_sha256(
                    result.ocrmypdf_version,
                    result.tesseract_version,
                ),
                output_byte_size=section_result[0],
                output_sha256=section_result[1],
            )
            payload = encode_ocr_worker_report(report)
            if len(payload) > min(
                OCR_OPERATION_MAX_RESULT_BYTES,
                MAX_OCR_WORKER_REPORT_BYTES,
            ):
                reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.report_size")
            _require_active(control)
            _write_result_pipe(handles.transfer(request.result_handle), payload)
            return CaseObjectReference(
                area=ReferenceArea.OPERATION_STAGING,
                object_id=request.output_object_id,
                operation_id=context.operation_id,
                content_kind=OCR_PROCESS_OUTPUT_CONTENT_KIND,
                expected_byte_size=section_result[0],
                expected_sha256=section_result[1],
            )


OCR_ENGINE_PROFILE_SHA256: Final = ocr_engine_profile_sha256(
    OCRMY_PDF_ENGINE_VERSION,
    TESSERACT_ENGINE_VERSION,
)


def _source_reference(
    context: OperationContext,
    request: OcrProcessOperationRequest,
) -> CaseObjectReference:
    if len(context.inputs) != 1 or type(context.inputs[0]) is not CaseObjectReference:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_reference")
    reference = context.inputs[0]
    if (
        reference.area is not ReferenceArea.SOURCE_READ
        or reference.operation_id is not None
        or reference.content_kind != request.source_content_kind
        or reference.expected_byte_size is None
        or reference.expected_sha256 is None
    ):
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_reference")
    return reference


def _source_suffix(request: OcrProcessOperationRequest) -> str:
    mapping = {"pdf": ".pdf", "jpeg": ".jpg", "png": ".png"}
    try:
        return mapping[request.source_content_kind]
    except KeyError:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_kind")


def _languages_for_set(language_set_id: str) -> tuple[str, ...]:
    if language_set_id != OCR_DEFAULT_LANGUAGE_SET_ID:
        reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_set")
    return ("eng",)


def _stage_source(
    source: BinaryIO,
    path: Path,
    *,
    expected_size: int,
    expected_digest: str,
    control: _OperationControl,
) -> None:
    digest = hashlib.sha256()
    copied = 0
    try:
        destination = path.open("xb", buffering=0)
    except OSError:
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.source_stage_create")
    try:
        with destination:
            while copied < expected_size:
                _require_active(control)
                wanted = min(_COPY_CHUNK_BYTES, expected_size - copied)
                block = source.read(wanted)
                if type(block) is not bytes or not block or len(block) > wanted:
                    reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_read")
                _write_stream_exact(cast(BinaryIO, destination), block, "ocr.source_write")
                digest.update(block)
                copied += len(block)
            if source.read(1) != b"":
                reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_eof")
            destination.flush()
    except OcrError:
        raise
    except OSError:
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.source_stage")
    if copied != expected_size or not hmac.compare_digest(digest.hexdigest(), expected_digest):
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_digest")


def _copy_candidate_to_section(
    path: Path,
    handle: int,
    *,
    capacity: int,
    expected: OcrAdapterResult,
    control: _OperationControl,
) -> tuple[int, str]:
    section: _MappedOutputSection | None = None
    try:
        section = _MappedOutputSection.adopt(handle, capacity)
        with section:
            digest = hashlib.sha256()
            copied = 0
            try:
                source = path.open("rb", buffering=0)
            except OSError:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.candidate_open")
            with source:
                while copied < expected.byte_size:
                    _require_active(control)
                    wanted = min(_COPY_CHUNK_BYTES, expected.byte_size - copied)
                    block = source.read(wanted)
                    if type(block) is not bytes or not block or len(block) > wanted:
                        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.candidate_read")
                    _write_section_exact(section, block)
                    digest.update(block)
                    copied += len(block)
                if source.read(1) != b"":
                    reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.candidate_eof")
            actual = digest.hexdigest()
            if copied != expected.byte_size or not hmac.compare_digest(actual, expected.sha256):
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.candidate_digest")
            section.flush()
            if section.tell() != copied:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.candidate_size")
            _require_active(control)
            return copied, actual
    except OcrError:
        raise
    except ImagePageError:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_section")


def _write_section_exact(section: _MappedOutputSection, payload: bytes) -> None:
    cursor = 0
    try:
        while cursor < len(payload):
            written = section.write(payload[cursor:])
            if type(written) is not int or not 1 <= written <= len(payload) - cursor:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_write")
            cursor += written
    except OcrError:
        raise
    except Exception:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_write")


def _write_stream_exact(destination: BinaryIO, payload: bytes, context: str) -> None:
    cursor = 0
    while cursor < len(payload):
        try:
            written = destination.write(payload[cursor:])
        except OSError:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, context)
        if type(written) is not int or not 1 <= written <= len(payload) - cursor:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, context)
        cursor += written


def _require_active(control: _OperationControl) -> None:
    if control.cancellation_requested():
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.operation")
    if control.deadline_expired():
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.operation")


def _require_ocr_non_inheritable(handle: int, context: str) -> None:
    try:
        _require_non_inheritable(handle, context=context)
    except ImagePageError:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.handle_inheritance")


def _materialize_private_temp() -> Path:
    try:
        local_value = os.environ.get("LOCALAPPDATA")
        temp_value = os.environ.get("TEMP")
        tmp_value = os.environ.get("TMP")
        if any(
            type(value) is not str or not value or "\x00" in value
            for value in (
                local_value,
                temp_value,
                tmp_value,
            )
        ):
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_environment")
        local_root = Path(cast(str, local_value))
        temp_root = Path(cast(str, temp_value))
        tmp_root = Path(cast(str, tmp_value))
        if (
            any(not path.is_absolute() for path in (local_root, temp_root, tmp_root))
            or temp_root.name.casefold() != "temp"
            or tmp_root.name.casefold() != "temp"
            or any(
                os.fspath(path).startswith(("\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
                for path in (local_root, temp_root, tmp_root)
            )
        ):
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_scope")
        parent_spelling_matches = _same_absolute_spelling(
            temp_root.parent, local_root
        ) and _same_absolute_spelling(tmp_root.parent, local_root)
        parent_before: tuple[os.stat_result, os.stat_result, os.stat_result] | None = None
        if not parent_spelling_matches:
            parent_before = (
                _private_directory_info(local_root),
                _private_directory_info(temp_root.parent),
                _private_directory_info(tmp_root.parent),
            )
            if not (
                os.path.samestat(parent_before[0], parent_before[1])
                and os.path.samestat(parent_before[0], parent_before[2])
            ):
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_scope")
        temp_root.mkdir(parents=parent_spelling_matches, exist_ok=True)
        local_info = _private_directory_info(local_root)
        if parent_before is not None:
            temp_parent = _private_directory_info(temp_root.parent)
            tmp_parent = _private_directory_info(tmp_root.parent)
            if not (
                os.path.samestat(parent_before[0], local_info)
                and os.path.samestat(parent_before[1], temp_parent)
                and os.path.samestat(parent_before[2], tmp_parent)
                and os.path.samestat(local_info, temp_parent)
                and os.path.samestat(local_info, tmp_parent)
            ):
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_identity")
        temp_info = _private_directory_info(temp_root)
        tmp_info = _private_directory_info(tmp_root)
        if not (
            _same_absolute_spelling(temp_root, tmp_root) or os.path.samestat(temp_info, tmp_info)
        ):
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_scope")
        return temp_root
    except OcrError:
        raise
    except OSError, TypeError, ValueError:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_unavailable")


def _same_absolute_spelling(first: Path, second: Path) -> bool:
    return os.path.abspath(first) == os.path.abspath(second)


def _private_directory_info(path: Path) -> os.stat_result:
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_identity")
    return info


@contextmanager
def _private_operation_temp(root: Path) -> Iterator[Path]:
    operation_root, pin = _create_private_operation_temp(root)
    try:
        yield operation_root
    finally:
        cleaned = _remove_pinned_operation_temp(operation_root, pin)
        if not cleaned:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_cleanup")


def _create_private_operation_temp(root: Path) -> tuple[Path, int]:
    for _attempt in range(_OPERATION_TEMP_CREATE_ATTEMPTS):
        candidate = root / f"pws-ocr-{uuid4().hex}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_create")
        pin = 0
        try:
            before = _private_directory_info(candidate)
            pin = _open_directory_pin(candidate)
            after = _private_directory_info(candidate)
            if not os.path.samestat(before, after) or os.get_handle_inheritable(pin):
                raise OSError
            return candidate, pin
        except BaseException:
            _close_native_handle_no_raise(pin)
            try:
                candidate.rmdir()
            except BaseException:
                pass
    reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_identity")


def _open_directory_pin(path: Path) -> int:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        os.fspath(path),
        _DELETE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    value = int(raw) if raw else 0
    if value in {0, _INVALID_HANDLE_VALUE}:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _remove_pinned_operation_temp(root: Path, pin: int) -> bool:
    counter = [0]
    contents_removed = _remove_tree_contents(root, counter)
    pin_closed = _close_handle_checked(pin)
    if not contents_removed or not pin_closed:
        return False
    try:
        root.rmdir()
        os.lstat(root)
    except FileNotFoundError:
        return True
    except BaseException:
        return False
    return False


def _remove_tree_contents(root: Path, counter: list[int]) -> bool:
    try:
        with os.scandir(root) as iterator:
            entries = tuple(iterator)
    except BaseException:
        return False
    for entry in entries:
        counter[0] += 1
        if counter[0] > _MAXIMUM_TEMP_ENTRIES:
            return False
        path = Path(entry.path)
        try:
            info = os.lstat(path)
        except BaseException:
            return False
        try:
            if stat.S_ISDIR(info.st_mode) and not _is_reparse(info):
                if not _remove_tree_contents(path, counter):
                    return False
                path.rmdir()
            elif stat.S_ISDIR(info.st_mode):
                path.rmdir()
            else:
                path.unlink()
        except BaseException:
            return False
    return True


def _require_operation_tree_bound(root: Path, maximum_bytes: int) -> None:
    consumed = 0
    count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = tuple(iterator)
        except OSError:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_inventory")
        for entry in entries:
            count += 1
            if count > _MAXIMUM_TEMP_ENTRIES:
                reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_entries")
            path = Path(entry.path)
            try:
                info = os.lstat(path)
            except OSError:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_inventory")
            if _is_reparse(info):
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_reparse")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                consumed += info.st_size
                if consumed > maximum_bytes:
                    reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_bytes")
            else:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_entry")


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return type(attributes) is int and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _close_handle_checked(handle: int) -> bool:
    if type(handle) is not int or handle <= 0 or os.name != "nt":
        return False
    try:
        close_handle = _kernel32().CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        return bool(close_handle(wintypes.HANDLE(handle)))
    except BaseException:
        return False


def _write_result_pipe(handle: int, payload: bytes) -> None:
    try:
        _require_ocr_non_inheritable(handle, "ocr.result_handle")
        write_file = _kernel32().WriteFile
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
            written = wintypes.DWORD()
            remaining = len(payload) - cursor
            if not write_file(
                wintypes.HANDLE(handle),
                ctypes.byref(storage, cursor),
                remaining,
                ctypes.byref(written),
                None,
            ):
                reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.result_write")
            count = int(written.value)
            if not 1 <= count <= remaining:
                reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.result_write")
            cursor += count
    except OcrError:
        raise
    except Exception:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.result_write")
    finally:
        _close_native_handle_no_raise(handle)


def _kernel32() -> Any:
    if os.name != "nt":
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


@dataclass(frozen=True, slots=True)
class _EngineAdmissionControl:
    deadline: float

    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return time.monotonic() >= self.deadline


def build_ocr_process_operation_handler() -> OcrProcessOperationHandler | None:
    """Return a handler only after fixed resources and exact engines are admitted."""

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
        resources_available = (
            os.name == "nt"
            and executable_path.is_absolute()
            and tessdata_path.is_absolute()
            and executable_path.is_file()
            and tessdata_path.is_dir()
        )
    except OSError:
        resources_available = False
    if not resources_available:
        return None
    try:
        # A fresh AppContainer profile may expose its authenticated TEMP/TMP
        # namespace before Windows materializes the leaf directory. Use the
        # same scope-and-identity check as operation execution before probing
        # the engine; never treat a merely absent private leaf as unavailability.
        temporary_path = _materialize_private_temp()
    except OcrError:
        return None
    try:
        if distribution_version("ocrmypdf") != OCRMY_PDF_ENGINE_VERSION:
            return None
        if loaded_ocrmypdf_version() != OCRMY_PDF_ENGINE_VERSION:
            return None
        admission = TesseractCommandRunner(
            executable=executable_path,
            tessdata_directory=tessdata_path,
            temporary_directory=temporary_path,
            control=_EngineAdmissionControl(time.monotonic() + _ENGINE_ADMISSION_TIMEOUT_SECONDS),
        )
        if admission.version() != TESSERACT_ENGINE_VERSION or "eng" not in admission.languages():
            return None
    except ImportError, PackageNotFoundError, TesseractRunnerError, OSError, ValueError:
        return None
    return OcrProcessOperationHandler(
        WindowsOcrProcessAdapter(
            candidate_processor=OcrMyPdfAdapter(
                tesseract_executable=executable_path,
                tessdata_directory=tessdata_path,
            )
        )
    )
