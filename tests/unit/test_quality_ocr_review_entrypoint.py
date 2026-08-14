from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest

import paperworkshell.__main__ as desktop
import paperworkshell.worker.quality_ocr_review_operation as operation_module
from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageRuntimeStatus,
)
from paperworkshell.app.quality_analysis import (
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisRequest,
    QualityAnalysisService,
    QualityPageReviewKind,
    QualityPageReviewRequest,
)
from paperworkshell.app.quality_ocr_review import (
    CancellationProbe,
    DeadlineProbe,
    QualityOcrReviewService,
)
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.quality_analysis import (
    OcrReviewStatus,
    QualityPageKind,
    QualityPageSample,
)
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    QualityOcrReviewStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
)
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.protocol.models import ResourceLimits
from paperworkshell.worker.quality_ocr_review_operation import (
    QualityOcrReviewOperationRequest,
    WindowsQualityOcrReviewAdapter,
)


def _request() -> QualityOcrReviewRequest:
    return QualityOcrReviewRequest(
        case_id=UUID("99000000-0000-4000-8000-000000000001"),
        manifest_revision=7,
        page_id=UUID("99000000-0000-4000-8000-000000000002"),
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        pixels=bytes((12, 34, 56, 255)) * 16,
        presentation_sha256="a" * 64,
        language_set_id="latin-default-v1",
    )


def _sample(request: QualityOcrReviewRequest) -> QualityPageSample:
    return QualityPageSample(
        page_id=request.page_id,
        page_position=1,
        page_kind=QualityPageKind.PDF,
        width_pixels=request.width_pixels,
        height_pixels=request.height_pixels,
        row_stride_bytes=request.row_stride_bytes,
        pixels=request.pixels,
    )


class _Reviewer:
    def __init__(self) -> None:
        self.worker_generations = 0

    def review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: object,
        cancellation: object,
    ) -> QualityOcrReviewReport:
        assert callable(deadline)
        assert callable(cancellation)
        self.worker_generations += 1
        return QualityOcrReviewReport(
            operation_id=UUID(f"99000000-0000-4000-8000-{100 + self.worker_generations:012x}"),
            case_id=request.case_id,
            manifest_revision=request.manifest_revision,
            page_id=request.page_id,
            sample_sha256=request.sample_sha256,
            presentation_sha256=request.presentation_sha256,
            language_set_id=request.language_set_id,
            profile_id=request.profile_id,
            status=QualityOcrReviewStatus.SUCCEEDED,
            recognized_word_count=1,
            mean_confidence_milli=82_000,
        )


class _Composition:
    def __init__(self, authority_id: str | None) -> None:
        self.runtime_authority_id = authority_id
        self.reviewer = _Reviewer()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_backend_resource_closer_retries_all_idempotent_closers_after_failure() -> None:
    events: list[str] = []
    quality_attempts = 0

    def close_quality() -> None:
        nonlocal quality_attempts
        quality_attempts += 1
        events.append("quality")
        if quality_attempts == 1:
            raise RuntimeError("sanitized close failure")

    def close_desktop() -> None:
        events.append("desktop")

    resources = desktop._BackendWorkerResources(close_desktop, close_quality)

    with pytest.raises(RuntimeError, match="sanitized close failure"):
        resources.close()
    assert not resources.closed
    resources.close()
    resources.close()

    assert events == ["quality", "desktop", "quality", "desktop"]
    assert resources.closed


def test_lazy_processor_constructs_once_reuses_one_shot_reviewer_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    desktop_close_calls: list[None] = []
    owned = desktop._BackendWorkerResources(lambda: desktop_close_calls.append(None))
    composition = _Composition(resources.ocr_languages.runtime_authority_id)
    construction_calls: list[object] = []

    def construct(selected: object) -> _Composition:
        construction_calls.append(selected)
        return composition

    monkeypatch.setattr(desktop, "_optional_quality_ocr_review_composition", construct)
    processor = desktop._LazyQualityOcrReviewProcessor(resources, owned)
    request = _request()

    for _ in range(2):
        report = processor.review(
            request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        assert report.page_id == request.page_id

    assert construction_calls == [resources]
    assert composition.reviewer.worker_generations == 2
    assert composition.close_calls == 0
    owned.close()
    owned.close()
    assert composition.close_calls == 1
    assert desktop_close_calls == [None]


def test_lazy_processor_close_before_construction_is_idempotently_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    construction_calls: list[object] = []
    desktop_close_calls: list[None] = []
    owned = desktop._BackendWorkerResources(lambda: desktop_close_calls.append(None))
    processor = desktop._LazyQualityOcrReviewProcessor(resources, owned)
    monkeypatch.setattr(
        desktop,
        "_optional_quality_ocr_review_composition",
        lambda selected: construction_calls.append(selected),
    )

    owned.close()
    owned.close()
    with pytest.raises(QualityOcrReviewError) as rejected:
        processor.review(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE
    assert construction_calls == []
    assert desktop_close_calls == [None]


@pytest.mark.parametrize("cancel_after_construction", [False, True])
def test_lazy_processor_cancellation_before_or_immediately_after_construction_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    cancel_after_construction: bool,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    owned = desktop._BackendWorkerResources(lambda: None)
    composition = _Composition(resources.ocr_languages.runtime_authority_id)
    construction_calls = 0

    def construct(_selected: object) -> _Composition:
        nonlocal construction_calls
        construction_calls += 1
        return composition

    monkeypatch.setattr(desktop, "_optional_quality_ocr_review_composition", construct)
    processor = desktop._LazyQualityOcrReviewProcessor(resources, owned)
    cancellation_checks = 0

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= (2 if cancel_after_construction else 1)

    with pytest.raises(QualityOcrReviewError) as rejected:
        processor.review(
            _request(),
            deadline=lambda: False,
            cancellation=cancelled,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.CANCELLED
    assert construction_calls == (1 if cancel_after_construction else 0)
    assert composition.reviewer.worker_generations == 0
    assert composition.close_calls == (1 if cancel_after_construction else 0)
    owned.close()
    assert composition.close_calls == (1 if cancel_after_construction else 0)


def test_lazy_admission_failure_becomes_failed_fact_not_visual_review_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    admission_calls = 0

    def unavailable(_resources: object) -> None:
        nonlocal admission_calls
        admission_calls += 1
        return None

    monkeypatch.setattr(desktop, "_optional_quality_ocr_review_composition", unavailable)
    owned = desktop._BackendWorkerResources(lambda: None)
    service = QualityOcrReviewService(
        desktop._LazyQualityOcrReviewProcessor(resources, owned),
        language_sets=resources.ocr_languages,
    )
    request = _request()

    facts = tuple(
        service.review_sample(
            request.case_id,
            request.manifest_revision,
            request.presentation_sha256,
            _sample(request),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        for _ in range(2)
    )

    assert all(fact.status is OcrReviewStatus.FAILED for fact in facts)
    assert all(fact.recognized_word_count == 0 for fact in facts)
    assert admission_calls == 1
    assert owned.closed is False


def test_constrained_quality_worker_config_preserves_central_resource_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def build_config(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        desktop, "create_quality_ocr_review_worker_composition_config", build_config
    )
    result = desktop._quality_ocr_review_worker_config(
        CONSTRAINED_RESOURCE_POLICY_V1,
        Path("C:/approved/tesseract.exe"),
        Path("C:/approved/tessdata"),
    )

    assert result is not None
    policy = cast(Any, captured["resource_policy"])
    limits = cast(ResourceLimits, captured["limits"])
    assert policy.maximum_processes == 2
    assert policy.process_memory_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
    )
    assert limits.page_count == 1
    assert limits.pixel_count == 256 * 256
    assert limits.output_bytes == 1024 * 1024
    assert limits.temporary_storage_bytes <= (
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
    )
    assert captured["maximum_heavy_operation_concurrency"] == 1


def test_optional_composition_is_absent_when_runtime_is_not_configured() -> None:
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)

    assert desktop._optional_quality_ocr_review_composition(resources) is None


def test_optional_composition_uses_same_language_runtime_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("C:/approved/tesseract/bin/tesseract.exe")
    tessdata = Path("C:/approved/tesseract/share/tessdata")
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=executable,
        tessdata_root=tessdata,
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    resources.ocr_languages.refresh()
    observed: dict[str, object] = {}

    class _Composition:
        runtime_authority_id = resources.ocr_languages.runtime_authority_id

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    composition = _Composition()
    monkeypatch.setattr(desktop, "_quality_ocr_review_worker_config", lambda *_args: object())

    def compose(_config: object, *, heavy_operations: object) -> _Composition:
        observed["heavy_operations"] = heavy_operations
        return composition

    monkeypatch.setattr(desktop, "create_quality_ocr_review_worker_composition", compose)

    selected = cast(Any, desktop._optional_quality_ocr_review_composition(resources))

    assert selected is composition
    assert observed["heavy_operations"] is resources.heavy_operations
    assert selected.runtime_authority_id == resources.ocr_languages.runtime_authority_id


def test_optional_composition_closes_and_rejects_split_runtime_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    resources.ocr_languages.refresh()

    class _Composition:
        runtime_authority_id = "f" * 64

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    composition = _Composition()
    monkeypatch.setattr(desktop, "_quality_ocr_review_worker_config", lambda *_args: object())
    monkeypatch.setattr(
        desktop,
        "create_quality_ocr_review_worker_composition",
        lambda *_args, **_kwargs: composition,
    )

    with pytest.raises(OcrError) as rejected:
        desktop._optional_quality_ocr_review_composition(resources)

    assert rejected.value.code is OcrErrorCode.MODEL_INVALID
    assert composition.close_calls == 1


def test_runtime_authority_mismatch_aborts_service_analysis_without_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    resources.ocr_languages.refresh()

    class _MismatchedComposition:
        runtime_authority_id = "f" * 64

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    composition = _MismatchedComposition()
    monkeypatch.setattr(desktop, "_quality_ocr_review_worker_config", lambda *_args: object())
    monkeypatch.setattr(
        desktop,
        "create_quality_ocr_review_worker_composition",
        lambda *_args, **_kwargs: composition,
    )

    class _UnusedPreviews:
        def get_previews(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
            raise AssertionError("the bound synthetic sample bypasses preview in this regression")

    request = _request()
    monkeypatch.setattr(
        QualityAnalysisService,
        "_sample_for_page",
        lambda *_args, **_kwargs: _sample(request),
    )
    desktop_closes: list[None] = []
    owned = desktop._BackendWorkerResources(lambda: desktop_closes.append(None))
    producer = QualityOcrReviewService(
        desktop._LazyQualityOcrReviewProcessor(resources, owned),
        language_sets=resources.ocr_languages,
    )
    analysis = QualityAnalysisService(
        _UnusedPreviews(),  # type: ignore[arg-type]
        ocr_review=producer,
    )

    with pytest.raises(QualityAnalysisError) as rejected:
        analysis.analyze(
            QualityAnalysisRequest(
                request.case_id,
                request.manifest_revision,
                (
                    QualityPageReviewRequest(
                        request.page_id,
                        QualityPageReviewKind.PDF,
                        pdf_rotation=PdfRenderRotation.DEGREES_0,
                    ),
                ),
            )
        )

    assert rejected.value.code is QualityAnalysisErrorCode.INTERNAL_ERROR
    assert composition.close_calls == 1
    assert not owned.closed
    owned.close()
    assert desktop_closes == [None]


@pytest.mark.parametrize("failure_kind", ["windows-launch", "worker-client-launch"])
def test_runtime_construction_failure_aborts_service_analysis_without_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=Path("C:/approved/tesseract/bin/tesseract.exe"),
        tessdata_root=Path("C:/approved/tesseract/share/tessdata"),
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    resources.ocr_languages.refresh()
    failure: Exception
    if failure_kind == "windows-launch":
        failure = WindowsLaunchError(
            WindowsLaunchErrorCode.PROCESS_FAILED,
            "worker.process",
            "Synthetic sanitized launch failure.",
        )
    else:
        failure = WorkerClientError(
            WorkerClientErrorCode.LAUNCH_FAILED,
            WorkerClientContext.LAUNCH,
        )

    monkeypatch.setattr(desktop, "_quality_ocr_review_worker_config", lambda *_args: object())

    def fail_construction(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(
        desktop,
        "create_quality_ocr_review_worker_composition",
        fail_construction,
    )

    class _UnusedPreviews:
        def get_previews(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
            raise AssertionError("the bound synthetic sample bypasses preview in this regression")

    request = _request()
    monkeypatch.setattr(
        QualityAnalysisService,
        "_sample_for_page",
        lambda *_args, **_kwargs: _sample(request),
    )
    desktop_closes: list[None] = []
    owned = desktop._BackendWorkerResources(lambda: desktop_closes.append(None))
    producer = QualityOcrReviewService(
        desktop._LazyQualityOcrReviewProcessor(resources, owned),
        language_sets=resources.ocr_languages,
    )

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(
            _UnusedPreviews(),  # type: ignore[arg-type]
            ocr_review=producer,
        ).analyze(
            QualityAnalysisRequest(
                request.case_id,
                request.manifest_revision,
                (
                    QualityPageReviewRequest(
                        request.page_id,
                        QualityPageReviewKind.PDF,
                        pdf_rotation=PdfRenderRotation.DEGREES_0,
                    ),
                ),
            )
        )

    assert rejected.value.code is QualityAnalysisErrorCode.INTERNAL_ERROR
    assert not owned.closed
    owned.close()
    assert desktop_closes == [None]


def test_post_preflight_tesseract_launch_oserror_aborts_analysis_without_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "tesseract.exe").resolve()
    tessdata = (tmp_path / "tessdata").resolve()
    private_temp = (tmp_path / "Temp").resolve()
    executable.write_bytes(b"synthetic executable")
    tessdata.mkdir()
    private_temp.mkdir()
    monkeypatch.setattr(
        operation_module,
        "_materialize_worker_private_temp",
        lambda: private_temp,
    )
    private_canary = "private ACL, quota, or Job launch detail"

    def fail_launch(*_args: object, **_kwargs: object) -> None:
        raise OSError(private_canary)

    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        fail_launch,
    )
    adapter = WindowsQualityOcrReviewAdapter(
        executable,
        tessdata,
        frozenset({"eng"}),
    )

    class _AdapterProcessor:
        def review(
            self,
            request: QualityOcrReviewRequest,
            *,
            deadline: DeadlineProbe,
            cancellation: CancellationProbe,
        ) -> QualityOcrReviewReport:
            cancelled = Event()
            if cancellation():
                cancelled.set()
            context = OperationContext(
                operation_id=UUID("99000000-0000-4000-8000-000000000099"),
                case_id=request.case_id,
                limits=ResourceLimits(
                    5_000,
                    4_000,
                    1_048_576,
                    4_194_304,
                    4_096,
                    1,
                    65_536,
                ),
                cancel_requested=cancelled,
                report_progress=lambda _progress: None,
                deadline_expired=deadline,
            )
            return adapter._review_sample(
                context,
                QualityOcrReviewOperationRequest(
                    sample_handle=101,
                    result_handle=102,
                    page_id=request.page_id,
                    manifest_revision=request.manifest_revision,
                    width_pixels=request.width_pixels,
                    height_pixels=request.height_pixels,
                    row_stride_bytes=request.row_stride_bytes,
                    sample_byte_size=request.sample_byte_size,
                    sample_sha256=request.sample_sha256,
                    presentation_sha256=request.presentation_sha256,
                    language_set_id=request.language_set_id,
                    profile_id=request.profile_id,
                ),
                request.pixels,
            )

    resources = desktop.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=executable,
        tessdata_root=tessdata,
    )
    monkeypatch.setattr(
        desktop._PackagedOcrLanguageDiscovery,
        "discover_ocr_languages",
        lambda _self: OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        ),
    )
    resources.ocr_languages.refresh()
    request = _request()
    monkeypatch.setattr(
        QualityAnalysisService,
        "_sample_for_page",
        lambda *_args, **_kwargs: _sample(request),
    )

    class _UnusedPreviews:
        def get_previews(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
            raise AssertionError("the bound synthetic sample bypasses preview in this regression")

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(
            _UnusedPreviews(),  # type: ignore[arg-type]
            ocr_review=QualityOcrReviewService(
                _AdapterProcessor(),
                language_sets=resources.ocr_languages,
            ),
        ).analyze(
            QualityAnalysisRequest(
                request.case_id,
                request.manifest_revision,
                (
                    QualityPageReviewRequest(
                        request.page_id,
                        QualityPageReviewKind.PDF,
                        pdf_rotation=PdfRenderRotation.DEGREES_0,
                    ),
                ),
            )
        )

    assert rejected.value.code is QualityAnalysisErrorCode.INTERNAL_ERROR
    assert private_canary not in f"{rejected.value!r} {rejected.value}"
    assert tuple(private_temp.iterdir()) == ()
