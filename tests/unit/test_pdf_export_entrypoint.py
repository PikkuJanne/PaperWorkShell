"""Composition-root policy and wiring tests for verified PDF export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.__main__ import (
    DesktopApplicationResourceContext,
    _configured_qpdf_executable,
    _desktop_worker_config,
    build_windows_pdf_export_limits,
    build_windows_pdf_export_service,
    build_windows_pdf_export_worker_composition,
    build_windows_pdf_export_worker_config,
    create_desktop_application_resource_context,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.source_ingest import CommittedPdfSourceBatchStore
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker import desktop_composition as composition_module
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
)
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    create_pdf_export_worker_composition,
    create_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import CapabilityDescriptor, ResourceLimits


class _Sources:
    def borrow_committed_pdf_sources(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeWorkerClient:
    def __init__(
        self,
        launcher: object,
        resource_policy: WorkerResourcePolicy,
        timeout_policy: WorkerTimeoutPolicy,
        *,
        required_capabilities: tuple[CapabilityDescriptor, ...],
    ) -> None:
        self.launcher = launcher
        self.resource_policy = resource_policy
        self.timeout_policy = timeout_policy
        self.required_capabilities = required_capabilities
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1


def _synthetic_export_config(tmp_path: Path) -> PdfExportWorkerCompositionConfig:
    runtime_root = tmp_path / "runtime"
    code_root = tmp_path / "code"
    qpdf_root = tmp_path / "qpdf"
    runtime_root.mkdir()
    code_root.mkdir()
    qpdf_root.mkdir()
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    qpdf_executable = qpdf_root / "qpdf.exe"
    python_executable.write_bytes(b"synthetic executable")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    qpdf_executable.write_bytes(b"synthetic qpdf")
    return create_pdf_export_worker_composition_config(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=(runtime_root,),
        code_roots=(code_root,),
        working_directory=code_root,
        qpdf_executable=qpdf_executable,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=2,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=1,
        export_limits=ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=25_000,
            memory_bytes=256 * 1024 * 1024,
            temporary_storage_bytes=64 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            page_count=100,
            pixel_count=1_048_576,
        ),
    )


def _manager(tmp_path: Path) -> CaseWorkspaceManager:
    profile = tmp_path / "profile"
    profile.mkdir()
    return CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=96_000_000,
            maximum_cache_bytes=16_000_000,
            maximum_temp_bytes=48_000_000,
            maximum_entries=1_024,
            maximum_depth=16,
        ),
        id_factory=lambda: UUID("11400000-0000-4000-8000-000000000001"),
    )


def test_production_export_policy_is_closed_and_qpdf_capable() -> None:
    limits = build_windows_pdf_export_limits()

    assert limits == ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=1024 * 1024 * 1024,
        output_bytes=512 * 1024 * 1024,
        page_count=1_000,
        pixel_count=1_048_576,
    )


def test_production_export_uses_the_pinned_qpdf_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_QPDF_EXECUTABLE", raising=False)

    assert _configured_qpdf_executable(None) == DEFAULT_QPDF_EXECUTABLE


def test_production_export_config_reuses_runtime_paths_and_honors_qpdf_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qpdf = tmp_path / "private-qpdf" / "qpdf.exe"
    qpdf.parent.mkdir()
    qpdf.write_bytes(b"synthetic qpdf")
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))

    export_config = build_windows_pdf_export_worker_config()
    desktop_config = _desktop_worker_config()

    assert export_config.qpdf_executable == qpdf
    assert export_config.python_executable == desktop_config.python_executable
    assert export_config.bootstrap_script == desktop_config.bootstrap_script
    assert export_config.runtime_roots == desktop_config.runtime_roots
    assert export_config.code_roots == desktop_config.code_roots
    assert export_config.working_directory == desktop_config.working_directory
    assert export_config.venv_launcher == desktop_config.venv_launcher
    assert export_config.resource_policy.maximum_processes == 2
    assert export_config.timeout_policy.cancellation_grace_seconds == 2.0
    assert desktop_config.timeout_policy.cancellation_grace_seconds == 2.0
    assert export_config.export_policy.limits == build_windows_pdf_export_limits()


def test_production_export_config_rejects_invalid_qpdf_without_disclosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "private-missing-qpdf" / "qpdf.exe"
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(missing))

    with pytest.raises(WindowsLaunchError) as caught:
        build_windows_pdf_export_worker_config()

    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert str(missing) not in str(caught.value)


def test_main_binds_one_selected_policy_identity_to_backend_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    backend = cast(OrganizerBackend, object())
    observed: dict[str, object] = {}

    def build(selected: DesktopApplicationResourceContext | None = None) -> OrganizerBackend:
        observed["backend_resources"] = selected
        return backend

    def run(
        factory: object,
        argv: object,
        *,
        resource_policy: object,
    ) -> int:
        observed["argv"] = argv
        observed["ui_policy"] = resource_policy
        observed["backend"] = cast(Any, factory)()
        return 17

    monkeypatch.setattr(
        desktop,
        "create_desktop_application_resource_context",
        lambda: resources,
    )
    monkeypatch.setattr(desktop, "build_backend", build)
    monkeypatch.setattr(desktop, "run_desktop_application", run)

    assert desktop.main(("paperworkshell", "--synthetic")) == 17
    assert observed == {
        "argv": ("paperworkshell", "--synthetic"),
        "ui_policy": resources.resource_policy,
        "backend_resources": resources,
        "backend": backend,
    }


def test_application_resource_context_rejects_a_mismatched_gate() -> None:
    with pytest.raises(ValueError, match="application resource context is invalid"):
        DesktopApplicationResourceContext(
            CONSTRAINED_RESOURCE_POLICY_V1,
            HeavyOperationCoordinator(2),
        )


def test_production_export_factory_reuses_the_app_scoped_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    qpdf = tmp_path / "qpdf.exe"
    qpdf.write_bytes(b"synthetic qpdf")
    resources = create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)

    composition = build_windows_pdf_export_worker_composition(resources, qpdf)
    try:
        assert composition.heavy_operations is resources.heavy_operations
        assert composition.heavy_operation_snapshot().maximum_concurrency == 1
        worker = cast(Any, composition)._worker
        assert worker.resource_policy.process_memory_bytes == 512 * 1024 * 1024
        assert worker.resource_policy.job_memory_bytes == 768 * 1024 * 1024
    finally:
        composition.close()


def test_service_factory_wires_concrete_storage_destination_and_exporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    resources = create_desktop_application_resource_context()
    composition = create_pdf_export_worker_composition(
        _synthetic_export_config(tmp_path),
        heavy_operations=resources.heavy_operations,
    )
    sources = cast(CommittedPdfSourceBatchStore, _Sources())
    try:
        service = build_windows_pdf_export_service(
            _manager(tmp_path),
            sources,
            composition,
            resources=resources,
        )
        selected = cast(Any, service)

        assert type(service) is PdfExportService
        assert type(selected._manifests) is WindowsManifestRepository
        assert selected._sources is sources
        assert selected._broker is composition.pdf_exporter
        assert type(selected._destination) is WindowsVerifiedPdfDestination
        assert (
            selected._destination.destination.minimum_free_space_reserve_bytes == 256 * 1024 * 1024
        )
        assert type(cast(Any, selected._history)._history) is WindowsPdfExportHistoryRepository
        assert type(selected._retries) is WindowsPdfExportRetryRepository
    finally:
        composition.close()


def test_service_factory_rejects_a_closed_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    resources = create_desktop_application_resource_context()
    composition = create_pdf_export_worker_composition(
        _synthetic_export_config(tmp_path),
        heavy_operations=resources.heavy_operations,
    )
    composition.close()

    with pytest.raises(PdfExportError) as caught:
        build_windows_pdf_export_service(
            _manager(tmp_path),
            cast(CommittedPdfSourceBatchStore, _Sources()),
            composition,
            resources=resources,
        )

    assert caught.value.code is PdfExportErrorCode.MODEL_INVALID


def test_service_factory_rejects_a_different_app_scoped_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    resources = create_desktop_application_resource_context()
    composition = create_pdf_export_worker_composition(
        _synthetic_export_config(tmp_path),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    try:
        with pytest.raises(PdfExportError) as caught:
            build_windows_pdf_export_service(
                _manager(tmp_path),
                cast(CommittedPdfSourceBatchStore, _Sources()),
                composition,
                resources=resources,
            )
        assert caught.value.code is PdfExportErrorCode.MODEL_INVALID
    finally:
        composition.close()
