"""PWS-0408 package runtime admission, policy, and deterministic cleanup."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

import paperworkshell.__main__ as desktop
import paperworkshell.export.package_service as service_module
from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
    PackagePdfCandidateProducer,
)
from paperworkshell.domain import package_export_errors
from paperworkshell.domain.package_summary import PackageSummaryStatus
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.export.package_export import (
    PackageExportError,
    PackageExportErrorCode,
    PackageExportProgress,
    PackageExportProgressPhase,
    PackagePdfProducerPort,
    reject_package_export,
)
from paperworkshell.export.package_service import WindowsPackageExportService
from paperworkshell.export.package_summary import build_package_summary
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.desktop_composition import DesktopWorkerComposition
from tests.unit.test_package_summary import SummaryInputs, summary_inputs, summary_outcome


def test_package_export_error_contract_preserves_public_aliases() -> None:
    assert PackageExportError is package_export_errors.PackageExportError
    assert PackageExportErrorCode is package_export_errors.PackageExportErrorCode
    assert reject_package_export is package_export_errors.reject_package_export
    with pytest.raises(package_export_errors.PackageExportError) as caught:
        reject_package_export(PackageExportErrorCode.PDF_FAILED, "package.contract")
    assert caught.value.code is PackageExportErrorCode.PDF_FAILED
    assert caught.value.context == "package.contract"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class _Sources:
    def borrow_committed_pdf_sources(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("no source may be opened during runtime admission")

    def borrow_committed_searchable_pdf_sources(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("no source may be opened during runtime admission")


class _Rasterizer:
    def rasterize_committed(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("no signature image may be read during runtime admission")


class _Exporter:
    def __init__(self) -> None:
        self.calls = 0
        self.arguments: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.failure: Exception | None = None

    def export(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        self.arguments = (args, kwargs)
        if self.failure is not None:
            raise self.failure
        return self


class _Composition:
    def __init__(self, resources: desktop.DesktopApplicationResourceContext) -> None:
        self.closed = False
        self.close_calls = 0
        self.heavy_operations = resources.heavy_operations
        self.ocr_runtime_authority_id = resources._ocr_runtime_authority_id
        self.exporter = _Exporter()

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _resources(
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> desktop.DesktopApplicationResourceContext:
    return desktop.create_desktop_application_resource_context(policy)


def _invoke(broker: object, *, searchable: bool, cancellation: object = None) -> object:
    selected = cast(Any, broker)
    arguments = (object(), object()) if searchable else (object(), object(), {})
    return selected.export(
        *arguments,
        deadline=lambda: False,
        cancellation=(lambda: False) if cancellation is None else cancellation,
        progress=lambda _: None,
    )


@pytest.mark.parametrize("policy", [NORMAL_RESOURCE_POLICY_V1, CONSTRAINED_RESOURCE_POLICY_V1])
def test_producer_context_is_lazy_and_preserves_app_policy(
    monkeypatch: pytest.MonkeyPatch, policy: ResourcePolicy
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a worker was admitted before an export pipeline was selected")

    monkeypatch.setattr(desktop, "build_windows_flattened_pdf_export_worker_composition", forbidden)
    monkeypatch.setattr(
        desktop, "build_windows_searchable_pdf_export_worker_composition", forbidden
    )
    resources = _resources(policy)
    with desktop._package_pdf_producer_context(
        cast(Any, _Sources()), cast(Any, _Rasterizer()), resources
    ) as producer:
        assert type(producer) is PackagePdfCandidateProducer
        assert cast(Any, producer)._policy is policy
        assert cast(Any, producer)._ocr_languages is resources.ocr_languages
        assert cast(Any, producer)._flattened_broker.maximum_output_bytes == (
            desktop.build_windows_flattened_pdf_export_limits(policy).output_bytes
        )
        assert cast(Any, producer)._searchable_broker.maximum_output_bytes == (
            desktop.build_windows_searchable_pdf_export_limits(policy).output_bytes
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop composition")
@pytest.mark.parametrize("target_size", [False, True])
def test_desktop_package_flattened_export_shares_the_ordinary_case_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target_size: bool
) -> None:
    resources = _resources()
    authority = object()
    monkeypatch.setattr(
        DesktopWorkerComposition, "runtime_access_authority", property(lambda _: authority)
    )
    manager = CaseWorkspaceManager(
        profile_root=tmp_path, quota=desktop._workspace_quota(resources.resource_policy)
    )
    backend = desktop._build_backend(manager, resources=resources)
    admitted: list[object] = []
    composition = _Composition(resources)

    def create(selected: desktop.DesktopApplicationResourceContext, **options: object) -> object:
        assert selected is resources
        admitted.append(options.get("runtime_access"))
        return composition

    monkeypatch.setattr(desktop, "build_windows_flattened_pdf_export_worker_composition", create)
    try:
        workflow = cast(Any, backend)._package_workflow
        service = workflow._compression if target_size else workflow._exporter
        with service._producer_factory() as producer:
            broker = (producer._fallback if target_size else producer)._flattened_broker
            assert _invoke(broker, searchable=False) is composition.exporter
            assert _invoke(broker, searchable=False) is composition.exporter
            assert admitted == [authority]
        assert composition.close_calls == 1
    finally:
        backend.shutdown()


@pytest.mark.parametrize("searchable", [False, True])
def test_each_selected_pipeline_is_reused_then_closed_once(
    monkeypatch: pytest.MonkeyPatch, searchable: bool
) -> None:
    resources = _resources()
    composition = _Composition(resources)
    calls: list[desktop.DesktopApplicationResourceContext] = []

    def create(selected: desktop.DesktopApplicationResourceContext) -> object:
        calls.append(selected)
        return composition

    factory = (
        "build_windows_searchable_pdf_export_worker_composition"
        if searchable
        else "build_windows_flattened_pdf_export_worker_composition"
    )
    monkeypatch.setattr(desktop, factory, create)
    with ExitStack() as stack:
        broker = (
            desktop._LazyPackageSearchableBroker(resources, stack)
            if searchable
            else desktop._LazyPackageFlattenedBroker(resources, stack)
        )
        assert _invoke(broker, searchable=searchable) is composition.exporter
        assert _invoke(broker, searchable=searchable) is composition.exporter
        assert calls == [resources]
        assert composition.close_calls == 0
    assert composition.close_calls == 1


@pytest.mark.parametrize("searchable", [False, True])
def test_pipeline_failure_closes_admitted_composition(
    monkeypatch: pytest.MonkeyPatch, searchable: bool
) -> None:
    resources = _resources()
    composition = _Composition(resources)
    composition.exporter.failure = RuntimeError("synthetic candidate failure")
    factory = (
        "build_windows_searchable_pdf_export_worker_composition"
        if searchable
        else "build_windows_flattened_pdf_export_worker_composition"
    )
    monkeypatch.setattr(desktop, factory, lambda _: composition)
    with pytest.raises(RuntimeError, match="synthetic candidate failure"), ExitStack() as stack:
        broker = (
            desktop._LazyPackageSearchableBroker(resources, stack)
            if searchable
            else desktop._LazyPackageFlattenedBroker(resources, stack)
        )
        _invoke(broker, searchable=searchable)
    assert composition.close_calls == 1


@pytest.mark.parametrize("searchable", [False, True])
@pytest.mark.parametrize("when", ["before", "after"])
def test_cancellation_around_admission_does_not_start_candidate(
    monkeypatch: pytest.MonkeyPatch, searchable: bool, when: str
) -> None:
    resources = _resources()
    composition = _Composition(resources)
    admitted = False

    def create(_: object) -> object:
        nonlocal admitted
        admitted = True
        return composition

    factory = (
        "build_windows_searchable_pdf_export_worker_composition"
        if searchable
        else "build_windows_flattened_pdf_export_worker_composition"
    )
    monkeypatch.setattr(desktop, factory, create)
    with pytest.raises(PackagePdfCandidateError) as caught, ExitStack() as stack:
        broker = (
            desktop._LazyPackageSearchableBroker(resources, stack)
            if searchable
            else desktop._LazyPackageFlattenedBroker(resources, stack)
        )
        _invoke(broker, searchable=searchable, cancellation=lambda: when == "before" or admitted)
    assert caught.value.code is PackagePdfCandidateErrorCode.CANCELLED
    assert composition.exporter.calls == 0
    assert admitted == (when == "after")
    assert composition.close_calls == (1 if when == "after" else 0)


@pytest.mark.parametrize("mismatch", ["gate", "runtime", "closed"])
def test_mismatched_searchable_runtime_is_rejected_and_closed(
    monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    resources = _resources()
    composition = _Composition(resources)
    if mismatch == "gate":
        composition.heavy_operations = _resources().heavy_operations
    elif mismatch == "runtime":
        composition.ocr_runtime_authority_id = "different-runtime"
    else:
        composition.closed = True
    monkeypatch.setattr(
        desktop, "build_windows_searchable_pdf_export_worker_composition", lambda _: composition
    )
    with pytest.raises(PackagePdfCandidateError) as caught, ExitStack() as stack:
        _invoke(desktop._LazyPackageSearchableBroker(resources, stack), searchable=True)
    assert caught.value.code is PackagePdfCandidateErrorCode.MODEL_INVALID
    assert composition.exporter.calls == 0
    assert composition.close_calls == 1


@pytest.mark.parametrize(
    ("deadline", "cancellation", "code"),
    [
        (lambda: True, lambda: False, PackagePdfCandidateErrorCode.TIMED_OUT),
        (lambda: False, lambda: 1, PackagePdfCandidateErrorCode.INTERNAL_ERROR),
        (lambda: None, lambda: False, PackagePdfCandidateErrorCode.INTERNAL_ERROR),
    ],
)
def test_runtime_control_is_exact_and_sanitized(
    deadline: object, cancellation: object, code: PackagePdfCandidateErrorCode
) -> None:
    with pytest.raises(PackagePdfCandidateError) as caught:
        desktop._require_package_runtime_active(cast(Any, deadline), cast(Any, cancellation))
    assert caught.value.code is code
    assert caught.value.__context__ is None


def _service_call(
    service: WindowsPackageExportService,
    inputs: SummaryInputs,
    destination: Path,
    *,
    confirmed: bool,
    progress: Any = lambda _done, _total: None,
) -> object:
    assert inputs.plan is not None
    return service.export(
        inputs.snapshot,
        inputs.template,
        inputs.definition,
        inputs.preflight,
        inputs.plan,
        destination,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress,
        human_review_confirmed=confirmed,
    )


@pytest.mark.parametrize("cleanup_failure", [False, True])
@pytest.mark.parametrize("summary_failure", [False, True])
def test_service_retains_exact_publication_even_if_later_worker_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cleanup_failure: bool,
    summary_failure: bool,
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF)
    outcome = summary_outcome(inputs)
    events: list[str] = []
    progress: list[tuple[int, int]] = []

    @contextmanager
    def factory() -> Iterator[PackagePdfProducerPort]:
        events.append("open")
        try:
            yield cast(PackagePdfProducerPort, object())
        finally:
            events.append("close")
            if cleanup_failure:
                raise RuntimeError("PRIVATE cleanup diagnostic")

    def export(*args: object, **kwargs: Any) -> object:
        assert args[0] is inputs.snapshot and args[1] is inputs.plan
        assert args[2] == tmp_path
        kwargs["progress"](PackageExportProgress(PackageExportProgressPhase.COMPLETE, 1, 1, 4, 4))
        events.append("export")
        return outcome

    monkeypatch.setattr(service_module, "export_package_plan", export)
    original_summary = build_package_summary

    def summarize(*args: Any, **kwargs: Any) -> Any:
        if summary_failure and kwargs.get("outcome") is not None:
            raise RuntimeError("PRIVATE final summary failure")
        return original_summary(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_package_summary", summarize)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    service = WindowsPackageExportService(factory, WindowsExportDestination(), policy)
    result = _service_call(
        service, inputs, tmp_path, confirmed=True, progress=lambda a, b: progress.append((a, b))
    )
    assert cast(Any, result).status is PackageSummaryStatus.COMMITTED
    assert cast(Any, result).saved_file_count == 1
    text = cast(Any, result).to_plain_text()
    assert ("Worker cleanup could not be confirmed" in text) == cleanup_failure
    assert ("Detailed package summary unavailable" in text) == summary_failure
    assert "PRIVATE" not in text
    assert events == ["open", "export", "close"]
    assert progress == [(4, 4)]


def test_service_refuses_unacknowledged_human_review_without_opening_workers(
    tmp_path: Path,
) -> None:
    inputs = summary_inputs(warning_heavy=True)

    def forbidden() -> Any:
        raise AssertionError("a human review requirement did not block worker admission")

    service = WindowsPackageExportService(
        forbidden, WindowsExportDestination(), CONSTRAINED_RESOURCE_POLICY_V1
    )
    result = _service_call(service, inputs, tmp_path, confirmed=False)
    assert cast(Any, result).status is PackageSummaryStatus.NEEDS_REVIEW
    assert cast(Any, result).saved_file_count == 0


def test_service_rejects_changed_runtime_plan_before_any_producer_is_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF)
    changed = summary_inputs(PortalOutputMode.SEPARATE_PDFS)
    monkeypatch.setattr(
        service_module, "build_package_export_plan", lambda *_a, **_kw: changed.plan
    )

    def forbidden() -> Any:
        raise AssertionError("changed reviewed output plan reached document workers")

    service = WindowsPackageExportService(
        forbidden, WindowsExportDestination(), CONSTRAINED_RESOURCE_POLICY_V1
    )
    with pytest.raises(PackageExportError) as caught:
        _service_call(service, inputs, tmp_path, confirmed=True)
    assert caught.value.code is PackageExportErrorCode.PLAN_STALE
