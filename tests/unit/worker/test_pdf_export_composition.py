"""Focused tests for the separate least-privilege PDF-export composition."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.app.pdf_export import PdfExportBroker
from paperworkshell.worker import desktop_composition as composition_module
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsProcessLauncher,
)
from paperworkshell.worker.client.errors import (
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    create_pdf_export_worker_composition,
    create_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import PdfExportBrokerPolicy
from paperworkshell.worker.protocol.models import (
    IMAGE_PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PORTAL_PDF_EXPORT_CAPABILITY,
    CapabilityDescriptor,
    ResourceLimits,
)
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope


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
        self.constructed_thread = threading.get_ident()
        self.terminate_calls = 0
        self.resource_snapshot_calls = 0
        self.snapshot = WorkerResourceSnapshot(
            process_memory_limit_bytes=resource_policy.process_memory_bytes,
            job_memory_limit_bytes=resource_policy.job_memory_bytes,
            peak_process_memory_used_bytes=1,
            peak_job_memory_used_bytes=2,
        )

    def terminate(self) -> None:
        self.terminate_calls += 1

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        self.resource_snapshot_calls += 1
        return self.snapshot


def _fixed_resources(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    runtime_root = tmp_path / "synthetic-runtime"
    code_root = tmp_path / "synthetic-code"
    qpdf_root = tmp_path / "synthetic-qpdf"
    runtime_root.mkdir()
    code_root.mkdir()
    qpdf_root.mkdir()
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    qpdf_executable = qpdf_root / "qpdf.exe"
    python_executable.write_bytes(b"synthetic executable")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    qpdf_executable.write_bytes(b"synthetic qpdf")
    return (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        qpdf_executable,
    )


def _operation_limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=30_000,
        cpu_time_ms=25_000,
        memory_bytes=256 * 1024 * 1024,
        temporary_storage_bytes=64 * 1024 * 1024,
        output_bytes=4 * 1024 * 1024,
        page_count=100,
        pixel_count=1_048_576,
    )


def _config(tmp_path: Path) -> PdfExportWorkerCompositionConfig:
    (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        qpdf_executable,
    ) = _fixed_resources(tmp_path)
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
        export_limits=_operation_limits(),
        pump_interval_seconds=0.01,
    )


def _compose_with_fake_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, _FakeWorkerClient]:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    composition = create_pdf_export_worker_composition(
        _config(tmp_path),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    worker = cast(_FakeWorkerClient, cast(Any, composition)._worker)
    return composition, worker


def test_factory_builds_a_separate_export_only_client_and_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)

    export_port: PdfExportBroker = composition.pdf_exporter
    assert isinstance(export_port, PdfExportBroker)
    assert isinstance(worker.launcher, WindowsProcessLauncher)
    assert worker.required_capabilities == (PDF_EXPORT_CAPABILITY,)
    assert cast(Any, export_port)._worker is worker
    assert type(cast(Any, export_port)._policy) is PdfExportBrokerPolicy
    launch_config = cast(Any, worker.launcher)._config
    assert launch_config.operation_scope is WorkerOperationScope.PDF_EXPORT
    assert launch_config.qpdf_executable.is_file()
    assert launch_config.qpdf_executable.name == "qpdf.exe"
    assert worker.resource_policy.maximum_processes == 2
    assert worker.constructed_thread == threading.get_ident()
    assert composition.resource_snapshot() == worker.snapshot
    assert worker.resource_snapshot_calls == 1
    assert cast(Any, export_port)._heavy_operations is cast(Any, composition)._heavy_operations
    assert composition.heavy_operation_snapshot().maximum_concurrency == 1
    composition.close()


def test_export_scope_preserves_the_shared_fixed_runtime_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    owner = WindowsProcessLauncher(config._launch_config, retain_runtime_access=True)
    authority = owner.runtime_access_authority
    assert authority is not None
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)

    with create_pdf_export_worker_composition(
        config, heavy_operations=HeavyOperationCoordinator(1), runtime_access=authority
    ) as composition:
        worker = cast(_FakeWorkerClient, composition._worker)
        launcher = cast(WindowsProcessLauncher, worker.launcher)
        assert launcher.runtime_access_authority is authority
        assert launcher._config.operation_scope is WorkerOperationScope.PDF_EXPORT
        assert replace(launcher._config, operation_scope=WorkerOperationScope.ALL) == (
            config._launch_config
        )
    assert config._launch_config.operation_scope is WorkerOperationScope.ALL


def test_factory_rejects_a_coordinator_whose_limit_differs_from_config(
    tmp_path: Path,
) -> None:
    with pytest.raises(WorkerClientError) as caught:
        create_pdf_export_worker_composition(
            _config(tmp_path),
            heavy_operations=HeavyOperationCoordinator(2),
        )

    assert caught.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


@pytest.mark.parametrize(
    ("version", "capability"),
    ((2, IMAGE_PDF_EXPORT_CAPABILITY), (3, PORTAL_PDF_EXPORT_CAPABILITY)),
)
def test_export_composition_negotiates_explicit_image_or_portal_version_without_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
    capability: CapabilityDescriptor,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    composition = create_pdf_export_worker_composition(
        replace(_config(tmp_path), operation_version=version),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    worker = cast(_FakeWorkerClient, cast(Any, composition)._worker)
    assert worker.required_capabilities == (capability,)
    launch = cast(Any, worker.launcher)._config
    assert launch.operation_scope is WorkerOperationScope.PDF_EXPORT
    assert launch.tesseract_executable is None
    assert launch.tessdata_root is None
    composition.close()


@pytest.mark.parametrize("version", [0, 4, True])
def test_export_composition_rejects_unsupported_version(tmp_path: Path, version: int) -> None:
    with pytest.raises(WorkerClientError):
        replace(_config(tmp_path), operation_version=version)


def test_close_is_idempotent_and_rejects_a_non_owner_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain_calls: list[str] = []
    monkeypatch.setattr(
        composition_module,
        "drain_windows_bounded_output_cleanup",
        lambda: drain_calls.append("output"),
    )
    monkeypatch.setattr(
        composition_module,
        "drain_windows_result_pipe_cleanup",
        lambda: drain_calls.append("pipe"),
    )
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)
    exporter = composition.pdf_exporter
    failures: list[BaseException] = []

    def close_from_other_thread() -> None:
        try:
            composition.close()
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=close_from_other_thread)
    thread.start()
    thread.join()

    assert len(failures) == 1
    assert isinstance(failures[0], WorkerClientError)
    assert failures[0].code is WorkerClientErrorCode.OWNER_THREAD_REQUIRED
    assert not composition.closed
    assert not exporter.revoked
    assert worker.terminate_calls == 0

    composition.close()
    composition.close()
    assert composition.closed
    assert exporter.revoked
    assert worker.terminate_calls == 1
    assert drain_calls == ["output", "pipe"]


def test_context_manager_terminates_the_export_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)

    with composition as entered:
        assert entered is composition

    assert composition.closed
    assert worker.terminate_calls == 1


def test_failed_termination_still_revokes_the_exposed_exporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)
    exporter = composition.pdf_exporter
    original_terminate = worker.terminate

    def fail_termination() -> None:
        worker.terminate_calls += 1
        raise RuntimeError("synthetic termination failure")

    monkeypatch.setattr(worker, "terminate", fail_termination)
    with pytest.raises(RuntimeError, match="synthetic termination failure"):
        composition.close()

    assert not composition.closed
    assert exporter.revoked
    with pytest.raises(WorkerClientError) as caught:
        composition.__enter__()
    assert caught.value.code is WorkerClientErrorCode.STATE_INVALID

    monkeypatch.setattr(worker, "terminate", original_terminate)
    composition.close()
    assert composition.closed


def test_configuration_and_composition_representations_never_disclose_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    composition = create_pdf_export_worker_composition(
        config,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    try:
        rendered = f"{config!r} {composition!r}"
        assert str(config.python_executable) not in rendered
        assert str(config.bootstrap_script) not in rendered
        assert str(config.working_directory) not in rendered
        assert str(config.qpdf_executable) not in rendered
        assert "validated fixed resources" in rendered
    finally:
        composition.close()


def test_configuration_rejects_a_missing_qpdf_without_disclosing_its_path(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    missing = tmp_path / "private-missing-qpdf" / "qpdf.exe"

    with pytest.raises(WindowsLaunchError) as caught:
        create_pdf_export_worker_composition_config(
            python_executable=config.python_executable,
            bootstrap_script=config.bootstrap_script,
            runtime_roots=config.runtime_roots,
            code_roots=config.code_roots,
            working_directory=config.working_directory,
            qpdf_executable=missing,
            resource_policy=config.resource_policy,
            timeout_policy=config.timeout_policy,
            maximum_heavy_operation_concurrency=1,
            export_limits=config.export_policy.limits,
        )

    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert str(missing) not in str(caught.value)
