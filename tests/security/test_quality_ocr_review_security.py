from __future__ import annotations

import io
from io import BytesIO
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest

import paperworkshell.worker.quality_ocr_review_operation as operation_module
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    QualityOcrReviewStatus,
    encode_quality_ocr_review_report,
)
from paperworkshell.ocr.tesseract_runner import (
    TesseractRunnerError,
    TesseractRunnerErrorCode,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import ResourceLimits
from paperworkshell.worker.quality_ocr_review_broker import _operation_payload
from paperworkshell.worker.quality_ocr_review_composition import (
    QualityOcrReviewWorkerComposition,
)
from paperworkshell.worker.quality_ocr_review_operation import (
    QualityOcrReviewOperationRequest,
    WindowsQualityOcrReviewAdapter,
    summarize_quality_ocr_tsv,
)

PRIVATE_TEXT = b"APPLICANT_SECRET C:/records/private-source.pdf"
PRIVATE_GEOMETRY = b"PRIVATE_LEFT_BOX"
PRIVATE_STDERR = b"TESSERACT_STDERR C:/records/private-input.ppm"
HEADER = (
    b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
    b"height\tconf\ttext\n"
)


class _Process:
    def __init__(self, output: bytes, error_output: bytes, return_code: int = 0) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO(error_output)
        self.return_code: int | None = return_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 1

    def kill(self) -> None:
        self.killed = True
        self.return_code = 1

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        assert self.return_code is not None
        return self.return_code


def _operation_fixture(
    tmp_path: Path,
) -> tuple[
    WindowsQualityOcrReviewAdapter,
    OperationContext,
    QualityOcrReviewOperationRequest,
    bytes,
    Path,
]:
    private_temp = (tmp_path / "private-canary-temp").resolve()
    executable = (tmp_path / "runtime-canary" / "bin" / "tesseract.exe").resolve()
    tessdata = (tmp_path / "runtime-canary" / "share" / "tessdata").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable")
    tessdata.mkdir(parents=True)
    private_temp.mkdir()
    adapter = WindowsQualityOcrReviewAdapter(
        executable=executable,
        tessdata_root=tessdata,
        installed_languages=frozenset({"eng"}),
        private_temp_storage=OsWorkerPrivateTempStorage(
            available_bytes_probe=lambda _root: 16 * 1024 * 1024,
            minimum_free_space_reserve_bytes=0,
        ),
    )
    context = OperationContext(
        operation_id=UUID("98000000-0000-4000-8000-000000000010"),
        case_id=UUID("98000000-0000-4000-8000-000000000011"),
        limits=ResourceLimits(5_000, 4_000, 1_048_576, 4_194_304, 4_096, 1, 16),
        cancel_requested=Event(),
        report_progress=lambda _progress: None,
        deadline_expired=lambda: False,
    )
    sample = bytes((65, 80, 80, 255)) * 16
    request = QualityOcrReviewOperationRequest(
        sample_handle=101,
        result_handle=102,
        page_id=UUID("98000000-0000-4000-8000-000000000012"),
        manifest_revision=9,
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        sample_byte_size=len(sample),
        sample_sha256="d" * 64,
        presentation_sha256="e" * 64,
        language_set_id="latin-default-v1",
        profile_id="quality-ocr-review-v1",
    )
    return adapter, context, request, sample, private_temp


def test_content_free_report_has_no_text_geometry_path_or_source_name() -> None:
    report = QualityOcrReviewReport(
        operation_id=UUID("98000000-0000-4000-8000-000000000001"),
        case_id=UUID("98000000-0000-4000-8000-000000000002"),
        manifest_revision=7,
        page_id=UUID("98000000-0000-4000-8000-000000000003"),
        sample_sha256="a" * 64,
        presentation_sha256="b" * 64,
        language_set_id="latin-default-v1",
        profile_id="quality-ocr-review-v1",
        status=QualityOcrReviewStatus.SUCCEEDED,
        recognized_word_count=1,
        mean_confidence_milli=82_000,
    )

    rendered = encode_quality_ocr_review_report(report).lower()

    assert PRIVATE_TEXT.lower() not in rendered
    for prohibited in (b"text", b"geometry", b"left", b"top", b"path", b"filename"):
        assert prohibited not in rendered


def test_request_and_report_reprs_hide_pixels_and_correlation_digests(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pixels = bytes((65, 80, 80, 255)) * 16
    request = QualityOcrReviewRequest(
        case_id=UUID("98000000-0000-4000-8000-000000000004"),
        manifest_revision=8,
        page_id=UUID("98000000-0000-4000-8000-000000000005"),
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        pixels=pixels,
        presentation_sha256="c" * 64,
        language_set_id="latin-default-v1",
    )
    report = QualityOcrReviewReport(
        operation_id=UUID("98000000-0000-4000-8000-000000000006"),
        case_id=request.case_id,
        manifest_revision=request.manifest_revision,
        page_id=request.page_id,
        sample_sha256=request.sample_sha256,
        presentation_sha256=request.presentation_sha256,
        language_set_id=request.language_set_id,
        profile_id=request.profile_id,
        status=QualityOcrReviewStatus.NO_TEXT,
        recognized_word_count=0,
        mean_confidence_milli=None,
    )

    exposed = f"{request!r} {report!r} {caplog.text}"
    assert pixels.hex() not in exposed
    assert request.sample_sha256 not in exposed
    assert request.presentation_sha256 not in exposed
    assert "private-source.pdf" not in exposed


def test_handle_only_rpc_payload_repr_redacts_required_correlation_digests() -> None:
    pixels = bytes((65, 80, 80, 255)) * 16
    request = QualityOcrReviewRequest(
        case_id=UUID("98000000-0000-4000-8000-000000000020"),
        manifest_revision=8,
        page_id=UUID("98000000-0000-4000-8000-000000000021"),
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        pixels=pixels,
        presentation_sha256="f" * 64,
        language_set_id="latin-default-v1",
    )
    payload = _operation_payload(
        request,
        sample_handle=101,
        result_handle=102,
        limits=ResourceLimits(5_000, 4_000, 1_048_576, 8_192, 4_096, 1, 16),
    )

    exposed = repr(payload)

    assert request.sample_sha256 not in exposed
    assert request.presentation_sha256 not in exposed
    assert pixels.hex() not in exposed
    assert all(request.sample_sha256 not in repr(parameter) for parameter in payload.parameters)


def test_malformed_private_tsv_never_reaches_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = HEADER + b"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\tNaN\t" + PRIVATE_TEXT + b"\n"

    with pytest.raises(QualityOcrReviewError) as rejected:
        summarize_quality_ocr_tsv(BytesIO(payload))

    exposed = f"{rejected.value!r} {rejected.value} {caplog.text}".encode()
    assert PRIVATE_TEXT not in exposed
    assert b"private-source.pdf" not in exposed
    assert rejected.value.__cause__ is None


def test_private_tsv_and_stderr_are_consumed_without_public_or_log_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, context, request, sample, private_temp = _operation_fixture(tmp_path)
    monkeypatch.setattr(
        operation_module,
        "_materialize_worker_private_temp",
        lambda: private_temp,
    )
    tsv = (
        HEADER
        + b"\t".join(
            (
                b"5",
                b"1",
                b"1",
                b"1",
                b"1",
                b"1",
                PRIVATE_GEOMETRY,
                b"PRIVATE_TOP_BOX",
                b"PRIVATE_WIDTH_BOX",
                b"PRIVATE_HEIGHT_BOX",
                b"91.250000",
                PRIVATE_TEXT,
            )
        )
        + b"\n"
    )
    invocation: dict[str, object] = {}

    def popen(arguments: tuple[str, ...], **kwargs: object) -> _Process:
        invocation["arguments"] = arguments
        invocation.update(kwargs)
        return _Process(tsv, PRIVATE_STDERR)

    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        popen,
    )

    report = adapter._review_sample(context, request, sample)

    assert report.status is QualityOcrReviewStatus.SUCCEEDED
    assert report.recognized_word_count == 1
    assert report.mean_confidence_milli == 91_250
    arguments = invocation["arguments"]
    assert isinstance(arguments, tuple)
    private_input = Path(arguments[1])
    assert private_input.parent == private_temp
    assert arguments[2:] == (
        "stdout",
        "-l",
        "eng",
        "--psm",
        "6",
        "-c",
        "tessedit_create_tsv=1",
    )
    assert not private_input.exists()
    assert tuple(private_temp.iterdir()) == ()
    exposed = (f"{request!r} {adapter!r} {report!r} {caplog.text}").encode()
    for prohibited in (
        sample.hex().encode("ascii"),
        request.sample_sha256.encode("ascii"),
        request.presentation_sha256.encode("ascii"),
        PRIVATE_TEXT,
        PRIVATE_GEOMETRY,
        PRIVATE_STDERR,
        str(private_temp).encode("utf-8"),
        str(private_input).encode("utf-8"),
    ):
        assert prohibited not in exposed


@pytest.mark.parametrize(
    ("runner_code", "expected_code"),
    [
        (
            TesseractRunnerErrorCode.OUTPUT_LIMIT,
            QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
        ),
        (
            TesseractRunnerErrorCode.CANCELLED,
            QualityOcrReviewErrorCode.CANCELLED,
        ),
        (
            TesseractRunnerErrorCode.TIMED_OUT,
            QualityOcrReviewErrorCode.TIMED_OUT,
        ),
        (
            TesseractRunnerErrorCode.FAILED,
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
        ),
    ],
)
def test_limit_cancel_timeout_and_engine_failure_leave_no_private_artifact_or_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    runner_code: TesseractRunnerErrorCode,
    expected_code: QualityOcrReviewErrorCode,
) -> None:
    adapter, context, request, sample, private_temp = _operation_fixture(tmp_path)
    monkeypatch.setattr(
        operation_module,
        "_materialize_worker_private_temp",
        lambda: private_temp,
    )
    observed_input: list[Path] = []

    class _FailingRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, arguments: tuple[object, ...], *, check: bool) -> object:
            assert check is False
            input_argument = arguments[1]
            assert isinstance(input_argument, (str, Path))
            selected_input = Path(input_argument)
            assert selected_input.is_file()
            observed_input.append(selected_input)
            raise TesseractRunnerError(runner_code, PRIVATE_STDERR.decode("ascii"))

    monkeypatch.setattr(operation_module, "TesseractCommandRunner", _FailingRunner)

    error: QualityOcrReviewError | None = None
    report: QualityOcrReviewReport | None = None
    try:
        report = adapter._review_sample(context, request, sample)
    except QualityOcrReviewError as rejected:
        error = rejected

    assert len(observed_input) == 1
    assert not observed_input[0].exists()
    assert tuple(private_temp.iterdir()) == ()
    assert report is None
    assert error is not None
    assert error.code is expected_code
    exposed = f"{request!r} {adapter!r} {report!r} {error!r} {error} {caplog.text}".encode()
    for prohibited in (
        sample.hex().encode("ascii"),
        request.sample_sha256.encode("ascii"),
        request.presentation_sha256.encode("ascii"),
        PRIVATE_STDERR,
        str(private_temp).encode("utf-8"),
        str(observed_input[0]).encode("utf-8"),
    ):
        assert prohibited not in exposed


def test_composition_repr_hides_runtime_authority_and_enforces_shared_gate() -> None:
    runtime_authority_canary = "runtime-authority-private-path-canary"

    class _Worker:
        def terminate(self) -> None:
            pass

    class _Reviewer:
        revoked = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    gate = HeavyOperationCoordinator(1)
    composition = QualityOcrReviewWorkerComposition(
        _Worker(),  # type: ignore[arg-type]
        _Reviewer(),  # type: ignore[arg-type]
        gate,
        runtime_authority_canary,
    )

    rendered = repr(composition)

    assert runtime_authority_canary not in rendered
    assert composition.heavy_operations is gate
    assert composition.heavy_operation_snapshot().maximum_concurrency == 1
