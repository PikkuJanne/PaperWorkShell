"""Production composition-root tests for overlay-flattened PDF export."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

import paperworkshell.__main__ as entrypoint
import paperworkshell.worker.flattened_pdf_export_composition as composition_module
from paperworkshell.app.flattened_pdf_export import (
    FlattenedPdfExportService,
    FlattenedPdfRasterizer,
)
from paperworkshell.app.source_ingest import CommittedPdfSourceBatchStore
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.flattened_pdf_export_composition import (
    FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    create_flattened_pdf_export_worker_composition,
    create_flattened_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import CapabilityDescriptor, ResourceLimits


class _Sources:
    def borrow_committed_pdf_sources(self, *_args: object, **_kwargs: object) -> None:
        return None


class _Rasterizer:
    def rasterize_committed(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeWorkerClient:
    def __init__(
        self,
        launcher: object,
        resource_policy: WorkerResourcePolicy,
        timeout_policy: WorkerTimeoutPolicy,
        *,
        required_capabilities: tuple[CapabilityDescriptor, ...],
        resource_sampling_interval_ms: int,
    ) -> None:
        self.launcher = launcher
        self.resource_policy = resource_policy
        self.timeout_policy = timeout_policy
        self.required_capabilities = required_capabilities
        self.resource_sampling_interval_ms = resource_sampling_interval_ms
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1


def _runtime(
    tmp_path: Path,
) -> tuple[Path, Path, Path, tuple[Path, ...], tuple[Path, ...], Path]:
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    runtime.mkdir(parents=True)
    code.mkdir(parents=True)
    python = runtime / "python.exe"
    venv = runtime / "venv-python.exe"
    bootstrap = code / "host.py"
    python.write_bytes(b"python")
    venv.write_bytes(b"venv")
    bootstrap.write_text("# host\n", encoding="utf-8")
    return python, venv, bootstrap, (runtime,), (code,), code


def _qpdf(tmp_path: Path) -> Path:
    qpdf = tmp_path / "qpdf" / "qpdf.exe"
    qpdf.parent.mkdir(parents=True)
    qpdf.write_bytes(b"qpdf")
    return qpdf


def _synthetic_composition_config(tmp_path: Path) -> object:
    python, venv, bootstrap, runtime_roots, code_roots, working_directory = _runtime(tmp_path)
    return create_flattened_pdf_export_worker_composition_config(
        python_executable=python,
        venv_launcher=venv,
        bootstrap_script=bootstrap,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=_qpdf(tmp_path),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=90_000,
            maximum_processes=FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
        maximum_heavy_operation_concurrency=1,
        limits=ResourceLimits(
            wall_time_ms=120_000,
            cpu_time_ms=90_000,
            memory_bytes=512 * 1024 * 1024,
            temporary_storage_bytes=512 * 1024 * 1024,
            output_bytes=128 * 1024 * 1024,
            page_count=100,
            pixel_count=4_000_000,
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


def test_flattened_limits_bind_candidate_raster_and_verification_budgets() -> None:
    limits = entrypoint.build_windows_flattened_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)

    assert limits == ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=512 * 1024 * 1024,
        output_bytes=256 * 1024 * 1024,
        page_count=1_000,
        pixel_count=4_000_000,
    )
    assert limits.output_bytes < limits.temporary_storage_bytes


def test_flattened_config_uses_exact_qpdf_authority_and_central_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entrypoint, "_worker_runtime_resources", lambda: _runtime(tmp_path))
    qpdf = _qpdf(tmp_path)

    config = entrypoint.build_windows_flattened_pdf_export_worker_config(
        qpdf,
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1,
    )
    limits = entrypoint.build_windows_flattened_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)

    assert config.qpdf_executable == qpdf
    assert config.resource_policy.maximum_processes == (
        FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES
    )
    assert config.resource_policy.process_memory_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
    )
    assert config.resource_policy.job_memory_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_job_memory_bytes
    )
    assert config.broker_policy.limits == limits
    assert config.resource_sampling_interval_ms == (
        CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms
    )
    assert config.broker_policy.pump_interval_seconds == (
        CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms / 1_000
    )


def test_flattened_composition_receives_exact_application_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qpdf = _qpdf(tmp_path)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1
    )
    config = object()
    composition = object()
    observed: dict[str, object] = {}

    def build_config(
        qpdf_arg: Path | None,
        *,
        resource_policy: object,
    ) -> object:
        observed.update(qpdf=qpdf_arg, resource_policy=resource_policy)
        return config

    def compose(config_arg: object, *, heavy_operations: object) -> object:
        observed.update(config=config_arg, heavy_operations=heavy_operations)
        return composition

    monkeypatch.setattr(
        entrypoint,
        "build_windows_flattened_pdf_export_worker_config",
        build_config,
    )
    monkeypatch.setattr(
        entrypoint,
        "create_flattened_pdf_export_worker_composition",
        compose,
    )

    result = entrypoint.build_windows_flattened_pdf_export_worker_composition(resources, qpdf)

    assert result is composition
    assert observed == {
        "qpdf": qpdf,
        "resource_policy": resources.resource_policy,
        "config": config,
        "heavy_operations": resources.heavy_operations,
    }


def test_service_factory_wires_case_assets_destination_history_and_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1
    )
    config = cast(Any, _synthetic_composition_config(tmp_path))
    composition = create_flattened_pdf_export_worker_composition(
        config,
        heavy_operations=resources.heavy_operations,
    )
    sources = cast(CommittedPdfSourceBatchStore, _Sources())
    rasterizer = cast(FlattenedPdfRasterizer, _Rasterizer())
    try:
        service = entrypoint.build_windows_flattened_pdf_export_service(
            _manager(tmp_path),
            sources,
            rasterizer,
            composition,
            resources=resources,
        )
        selected = cast(Any, service)

        assert type(service) is FlattenedPdfExportService
        assert type(selected._manifests) is WindowsManifestRepository
        assert selected._sources is sources
        assert selected._rasterizer is rasterizer
        assert selected._broker is composition.exporter
        assert type(selected._destination) is WindowsVerifiedPdfDestination
        assert (
            selected._destination.destination.minimum_free_space_reserve_bytes
            == CONSTRAINED_RESOURCE_POLICY_V1.minimum_export_free_space_reserve_bytes
        )
        assert type(cast(Any, selected._history)._history) is WindowsPdfExportHistoryRepository
        assert type(selected._retries) is WindowsPdfExportRetryRepository
        assert selected._overlay_limits.maximum_raster_pixels == 4_000_000
        assert selected._overlay_limits.maximum_total_raster_bytes == 16 * 1024 * 1024
    finally:
        composition.close()


def test_service_factory_rejects_closed_or_different_gate_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1
    )
    manager = _manager(tmp_path)
    sources = cast(CommittedPdfSourceBatchStore, _Sources())
    rasterizer = cast(FlattenedPdfRasterizer, _Rasterizer())

    closed = create_flattened_pdf_export_worker_composition(
        cast(Any, _synthetic_composition_config(tmp_path / "closed")),
        heavy_operations=resources.heavy_operations,
    )
    closed.close()
    with pytest.raises(FlattenedPdfExportError) as closed_error:
        entrypoint.build_windows_flattened_pdf_export_service(
            manager,
            sources,
            rasterizer,
            closed,
            resources=resources,
        )
    assert closed_error.value.code is FlattenedPdfExportErrorCode.MODEL_INVALID
    assert closed_error.value.context == "flattened.composition"

    mismatched = create_flattened_pdf_export_worker_composition(
        cast(Any, _synthetic_composition_config(tmp_path / "mismatched")),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    try:
        with pytest.raises(FlattenedPdfExportError) as gate_error:
            entrypoint.build_windows_flattened_pdf_export_service(
                manager,
                sources,
                rasterizer,
                mismatched,
                resources=resources,
            )
        assert gate_error.value.code is FlattenedPdfExportErrorCode.MODEL_INVALID
        assert gate_error.value.context == "flattened.composition"
    finally:
        mismatched.close()
