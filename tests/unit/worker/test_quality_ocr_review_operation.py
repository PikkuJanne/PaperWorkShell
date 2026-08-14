from __future__ import annotations

import subprocess
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event
from typing import BinaryIO
from uuid import UUID

import pytest

import paperworkshell.worker.quality_ocr_review_operation as operation_module
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewStatus,
)
from paperworkshell.ocr.tesseract_runner import (
    TesseractRunnerError,
    TesseractRunnerErrorCode,
)
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import ResourceLimits
from paperworkshell.worker.quality_ocr_review_operation import (
    QualityOcrReviewOperationRequest,
    WindowsQualityOcrReviewAdapter,
    summarize_quality_ocr_tsv,
)

HEADER = (
    b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
    b"height\tconf\ttext\n"
)
PRIVATE_WORD = b"private-canary-word"


def _row(
    *,
    level: bytes = b"5",
    word_number: bytes = b"1",
    confidence: bytes = b"96.700508",
    text: bytes = b"word",
) -> bytes:
    return (
        b"\t".join(
            (
                level,
                b"1",
                b"1",
                b"1",
                b"1",
                word_number,
                b"0",
                b"0",
                b"10",
                b"10",
                confidence,
                text,
            )
        )
        + b"\n"
    )


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled = cancelled
        self.expired = expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _operation_context() -> OperationContext:
    return OperationContext(
        operation_id=UUID("96000000-0000-4000-8000-000000000001"),
        case_id=UUID("96000000-0000-4000-8000-000000000002"),
        limits=ResourceLimits(5_000, 4_000, 1_048_576, 4_194_304, 4_096, 1, 16),
        cancel_requested=Event(),
        report_progress=lambda _progress: None,
        deadline_expired=lambda: False,
    )


def _operation_request(sample: bytes) -> QualityOcrReviewOperationRequest:
    return QualityOcrReviewOperationRequest(
        sample_handle=101,
        result_handle=102,
        page_id=UUID("96000000-0000-4000-8000-000000000003"),
        manifest_revision=7,
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        sample_byte_size=len(sample),
        sample_sha256=sha256(sample).hexdigest(),
        presentation_sha256="b" * 64,
        language_set_id="latin-default-v1",
        profile_id="quality-ocr-review-v1",
    )


def test_tsv_summary_accepts_tesseract_decimals_and_rounds_to_milli_without_float() -> None:
    payload = HEADER + _row(confidence=b"96.700508") + _row(confidence=b"50.1234")

    status, words, mean = summarize_quality_ocr_tsv(BytesIO(payload))

    assert status is QualityOcrReviewStatus.SUCCEEDED
    assert words == 2
    assert mean == 73_412


def test_nonword_minus_one_rows_and_empty_or_whitespace_words_are_not_counted() -> None:
    payload = (
        HEADER
        + _row(level=b"1", word_number=b"0", confidence=b"-1", text=b"")
        + _row(confidence=b"88.0", text=b"")
        + _row(confidence=b"77.0", text=b"   \r")
    )

    assert summarize_quality_ocr_tsv(BytesIO(payload)) == (
        QualityOcrReviewStatus.NO_TEXT,
        0,
        None,
    )


@pytest.mark.parametrize(
    "confidence",
    [
        b"1e2",
        b"NaN",
        b"inf",
        b"96.1234567",
        b"+96",
        b"100.000001",
        b"100.0005",
        b"100.1",
        b"-2",
    ],
)
def test_tsv_summary_rejects_malformed_or_out_of_range_confidence(
    confidence: bytes,
) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        summarize_quality_ocr_tsv(BytesIO(HEADER + _row(confidence=confidence)))

    assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (b"100", 100_000),
        (b"100.0", 100_000),
        (b"100.000000", 100_000),
        (b"99.999999", 100_000),
    ],
)
def test_tsv_summary_accepts_exact_confidence_boundaries(
    confidence: bytes,
    expected: int,
) -> None:
    assert summarize_quality_ocr_tsv(BytesIO(HEADER + _row(confidence=confidence))) == (
        QualityOcrReviewStatus.SUCCEEDED,
        1,
        expected,
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"wrong header\n",
        HEADER + b"5\ttoo\tfew\tcolumns\n",
        HEADER + _row(level=b"6"),
        HEADER + _row(word_number=b"-1"),
    ],
)
def test_tsv_summary_rejects_malformed_header_rows_and_columns(payload: bytes) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        summarize_quality_ocr_tsv(BytesIO(payload))

    assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID


def test_tsv_summary_enforces_byte_and_row_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = HEADER + _row() + _row()
    with pytest.raises(QualityOcrReviewError) as oversized:
        summarize_quality_ocr_tsv(BytesIO(payload), maximum_bytes=len(payload) - 1)
    assert oversized.value.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED

    monkeypatch.setattr(operation_module, "MAX_QUALITY_OCR_REVIEW_TSV_ROWS", 2)
    with pytest.raises(QualityOcrReviewError) as too_many_rows:
        summarize_quality_ocr_tsv(BytesIO(payload))
    assert too_many_rows.value.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        (_Control(cancelled=True), QualityOcrReviewErrorCode.CANCELLED),
        (_Control(expired=True), QualityOcrReviewErrorCode.TIMED_OUT),
    ],
)
def test_tsv_summary_honors_cancellation_and_deadline(
    control: _Control,
    expected: QualityOcrReviewErrorCode,
) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        summarize_quality_ocr_tsv(
            BytesIO(HEADER + _row()),
            control=control,  # type: ignore[arg-type]
        )

    assert rejected.value.code is expected


def test_tsv_parser_error_never_retains_text_or_path_canaries() -> None:
    payload = HEADER + _row(confidence=b"NaN", text=PRIVATE_WORD + b"-C:/private/file.tsv")

    with pytest.raises(QualityOcrReviewError) as rejected:
        summarize_quality_ocr_tsv(BytesIO(payload))

    rendered = f"{rejected.value!r} {rejected.value}"
    assert PRIVATE_WORD.decode("ascii") not in rendered
    assert "file.tsv" not in rendered
    assert rejected.value.__cause__ is None


def test_mapped_sample_close_attempts_handle_cleanup_after_unmap_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def fail_unmap(view: int) -> bool:
        calls.append(("unmap", view))
        return False

    def close_handle(handle: int) -> bool:
        calls.append(("close", handle))
        return True

    monkeypatch.setattr(
        operation_module,
        "_unmap_view",
        fail_unmap,
    )
    monkeypatch.setattr(
        operation_module,
        "_close_native_handle",
        close_handle,
    )
    mapped = operation_module._MappedInputSection(101, 202, 64)

    with pytest.raises(QualityOcrReviewError) as rejected:
        mapped.close()

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert calls == [("unmap", 202), ("close", 101)]


@pytest.mark.parametrize(
    ("stage", "failure", "expected"),
    [
        ("write", OSError("disk full"), QualityOcrReviewErrorCode.LIMIT_EXCEEDED),
        ("flush", OSError("disk full"), QualityOcrReviewErrorCode.LIMIT_EXCEEDED),
        ("write", RuntimeError("private"), QualityOcrReviewErrorCode.INTERNAL_ERROR),
        ("flush", RuntimeError("private"), QualityOcrReviewErrorCode.INTERNAL_ERROR),
    ],
)
def test_ppm_write_and_flush_classify_only_os_errors_as_resource_limits(
    stage: str,
    failure: Exception,
    expected: QualityOcrReviewErrorCode,
) -> None:
    sample = bytes((10, 20, 30, 255)) * 16

    class _Stream:
        def write(self, payload: bytes) -> int:
            if stage == "write":
                raise failure
            return len(payload)

        def flush(self) -> None:
            if stage == "flush":
                raise failure

    with pytest.raises(QualityOcrReviewError) as rejected:
        operation_module._write_ppm(
            _Stream(),  # type: ignore[arg-type]
            _operation_request(sample),
            sample,
            operation_module._OperationControl(_operation_context()),
        )

    assert rejected.value.code is expected
    assert "private" not in f"{rejected.value!r} {rejected.value}".lower()


def test_ppm_oserror_cleans_private_artifact_and_allows_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_temp = (tmp_path / "Temp").resolve()
    private_temp.mkdir()
    monkeypatch.setattr(operation_module, "_materialize_worker_private_temp", lambda: private_temp)

    class _FailingStream:
        def __init__(self, stream: BinaryIO) -> None:
            self._stream = stream

        def __enter__(self) -> _FailingStream:
            return self

        def __exit__(self, *_exc: object) -> None:
            self._stream.close()

        def write(self, _payload: bytes) -> int:
            raise OSError("private disk detail")

        def flush(self) -> None:
            self._stream.flush()

    class _Storage:
        def __init__(self) -> None:
            self.calls = 0

        def require_capacity(self, _root: Path, _required_bytes: int) -> None:
            return None

        def open_anonymous(self, _root: Path) -> BinaryIO:
            raise AssertionError("quality OCR review requires a named direct input")

        def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
            self.calls += 1
            path = root / f"sample-{self.calls}{suffix}"
            stream = path.open("xb")
            if self.calls == 1:
                return _FailingStream(stream), path  # type: ignore[return-value]
            return stream, path

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def run(self, _arguments: tuple[object, ...], *, check: bool) -> object:
            assert check is False
            return subprocess.CompletedProcess(
                ("<approved-tesseract>",),
                0,
                stdout=HEADER + _row(confidence=b"88.125", text=PRIVATE_WORD),
                stderr=None,
            )

    monkeypatch.setattr(operation_module, "TesseractCommandRunner", _Runner)
    storage = _Storage()
    adapter = WindowsQualityOcrReviewAdapter(
        executable=(tmp_path / "tesseract.exe").resolve(),
        tessdata_root=(tmp_path / "tessdata").resolve(),
        installed_languages=frozenset({"eng"}),
        private_temp_storage=storage,
    )
    sample = bytes((10, 20, 30, 255)) * 16

    with pytest.raises(QualityOcrReviewError) as rejected:
        adapter._review_sample(_operation_context(), _operation_request(sample), sample)

    assert rejected.value.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED
    assert tuple(private_temp.iterdir()) == ()

    report = adapter._review_sample(_operation_context(), _operation_request(sample), sample)
    assert report.status is QualityOcrReviewStatus.SUCCEEDED
    assert tuple(private_temp.iterdir()) == ()


def test_runner_infrastructure_failure_aborts_without_report_and_cleans_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_temp = (tmp_path / "Temp").resolve()
    private_temp.mkdir()
    monkeypatch.setattr(operation_module, "_materialize_worker_private_temp", lambda: private_temp)

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def run(self, _arguments: tuple[object, ...], *, check: bool) -> object:
            assert check is False
            raise TesseractRunnerError(TesseractRunnerErrorCode.FAILED, "tesseract.run")

    monkeypatch.setattr(operation_module, "TesseractCommandRunner", _Runner)
    adapter = WindowsQualityOcrReviewAdapter(
        executable=(tmp_path / "tesseract.exe").resolve(),
        tessdata_root=(tmp_path / "tessdata").resolve(),
        installed_languages=frozenset({"eng"}),
    )
    sample = bytes((10, 20, 30, 255)) * 16

    with pytest.raises(QualityOcrReviewError) as rejected:
        adapter._review_sample(_operation_context(), _operation_request(sample), sample)

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert tuple(private_temp.iterdir()) == ()


def test_temp_capacity_and_stdout_limit_cleanup_allow_fresh_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_temp = (tmp_path / "Temp").resolve()
    private_temp.mkdir()
    monkeypatch.setattr(operation_module, "_materialize_worker_private_temp", lambda: private_temp)
    calls = 0
    capacity_checks = 0

    def available_bytes(_root: Path) -> int:
        nonlocal capacity_checks
        capacity_checks += 1
        return 0 if capacity_checks == 1 else 16 * 1024 * 1024

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def run(self, arguments: tuple[object, ...], *, check: bool) -> object:
            nonlocal calls
            calls += 1
            assert arguments[2] == "stdout"
            assert arguments[-1] == "tsv"
            assert check is False
            if calls == 1:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.OUTPUT_LIMIT,
                    "tesseract.output_limit",
                )
            return subprocess.CompletedProcess(
                ("<approved-tesseract>",),
                0,
                stdout=HEADER + _row(confidence=b"88.125", text=PRIVATE_WORD),
                stderr=None,
            )

    monkeypatch.setattr(operation_module, "TesseractCommandRunner", _Runner)
    adapter = WindowsQualityOcrReviewAdapter(
        executable=(tmp_path / "tesseract.exe").resolve(),
        tessdata_root=(tmp_path / "tessdata").resolve(),
        installed_languages=frozenset({"eng"}),
        private_temp_storage=OsWorkerPrivateTempStorage(
            available_bytes_probe=available_bytes,
            minimum_free_space_reserve_bytes=0,
        ),
    )
    context = OperationContext(
        operation_id=UUID("96000000-0000-4000-8000-000000000001"),
        case_id=UUID("96000000-0000-4000-8000-000000000002"),
        limits=ResourceLimits(5_000, 4_000, 1_048_576, 4_194_304, 4_096, 1, 16),
        cancel_requested=Event(),
        report_progress=lambda _progress: None,
        deadline_expired=lambda: False,
    )
    sample = bytes((10, 20, 30, 255)) * 16
    request = QualityOcrReviewOperationRequest(
        sample_handle=101,
        result_handle=102,
        page_id=UUID("96000000-0000-4000-8000-000000000003"),
        manifest_revision=7,
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        sample_byte_size=len(sample),
        sample_sha256="a" * 64,
        presentation_sha256="b" * 64,
        language_set_id="latin-default-v1",
        profile_id="quality-ocr-review-v1",
    )

    with pytest.raises(QualityOcrReviewError) as no_space:
        adapter._review_sample(context, request, sample)
    assert no_space.value.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED
    assert no_space.value.context == "quality_ocr.temp_capacity"
    assert calls == 0
    assert tuple(private_temp.iterdir()) == ()

    with pytest.raises(QualityOcrReviewError) as limited:
        adapter._review_sample(context, request, sample)
    assert limited.value.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED
    assert calls == 1
    assert tuple(private_temp.iterdir()) == ()

    report = adapter._review_sample(context, request, sample)

    assert report.status is QualityOcrReviewStatus.SUCCEEDED
    assert report.recognized_word_count == 1
    assert report.mean_confidence_milli == 88_125
    assert PRIVATE_WORD.decode("ascii") not in repr(report)
    assert tuple(private_temp.iterdir()) == ()
