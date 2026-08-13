"""Focused tests for the separately privileged searchable-export composition."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

import pytest

import paperworkshell.worker.client._windows as windows_launcher
import paperworkshell.worker.searchable_pdf_export_composition as composition_module
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import WorkerResourceSnapshot, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    CapabilityDescriptor,
    ResourceLimits,
)
from paperworkshell.worker.searchable_pdf_export_broker import (
    BrokeredSearchablePdfExporter,
    SearchablePdfExportBrokerPolicy,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    SearchablePdfExportWorkerCompositionConfig,
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)


@pytest.fixture(autouse=True)
def _admit_synthetic_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    def admit(executable: Path, tessdata_root: Path) -> TesseractRuntimeAdmission:
        root = executable.parent.parent
        return TesseractRuntimeAdmission(
            root=root,
            executable=executable,
            tessdata_root=tessdata_root,
            resources=(root, executable.parent, executable, root / "share", tessdata_root),
        )

    monkeypatch.setattr(windows_launcher, "admit_packaged_tesseract_runtime", admit)


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
        self.snapshot = WorkerResourceSnapshot(
            resource_policy.process_memory_bytes,
            resource_policy.job_memory_bytes,
            1,
            2,
        )

    def terminate(self) -> None:
        self.terminate_calls += 1

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        return self.snapshot


def _paths(tmp_path: Path) -> dict[str, Any]:
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    qpdf_root = tmp_path / "qpdf"
    tesseract_root = tmp_path / "tesseract"
    tessdata = tesseract_root / "share" / "tessdata"
    for path in (runtime, code, qpdf_root, tesseract_root / "bin", tessdata):
        path.mkdir(parents=True)
    python = runtime / "python.exe"
    bootstrap = code / "host.py"
    qpdf = qpdf_root / "qpdf.exe"
    tesseract = tesseract_root / "bin" / "tesseract.exe"
    python.write_bytes(b"python")
    bootstrap.write_text("# host\n", encoding="utf-8")
    qpdf.write_bytes(b"qpdf")
    tesseract.write_bytes(b"tesseract")
    (tessdata / "eng.traineddata").write_bytes(b"eng")
    return {
        "python_executable": python,
        "bootstrap_script": bootstrap,
        "runtime_roots": (runtime,),
        "code_roots": (code,),
        "working_directory": code,
        "qpdf_executable": qpdf,
        "tesseract_executable": tesseract,
        "tessdata_root": tessdata,
    }


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=180_000,
        cpu_time_ms=120_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=512 * 1024 * 1024,
        output_bytes=128 * 1024 * 1024,
        page_count=100,
        pixel_count=100_000_000,
    )


def _config(tmp_path: Path) -> SearchablePdfExportWorkerCompositionConfig:
    return create_searchable_pdf_export_worker_composition_config(
        **_paths(tmp_path),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=120_000,
            maximum_processes=SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
        maximum_heavy_operation_concurrency=1,
        limits=_limits(),
        status_capacity_bytes=2 * 1024 * 1024,
    )


def test_config_binds_qpdf_tesseract_tessdata_and_bounded_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launch = config._launch_config

    assert launch.qpdf_executable == config.qpdf_executable
    assert launch.tesseract_executable == config.tesseract_executable
    assert launch.tessdata_root == config.tessdata_root
    assert config.qpdf_executable in launch.qpdf_access_resources
    assert config.tesseract_executable in launch.tesseract_access_resources
    assert config.tessdata_root in launch.tesseract_access_resources
    assert config.resource_policy.maximum_processes == 2
    assert config.broker_policy == SearchablePdfExportBrokerPolicy(
        _limits(),
        status_capacity_bytes=2 * 1024 * 1024,
    )
    rendered = repr(config)
    assert all(
        str(path) not in rendered
        for path in (
            config.qpdf_executable,
            config.tesseract_executable,
            config.tessdata_root,
        )
    )


def test_composition_registers_only_searchable_capability_and_shares_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    gate = HeavyOperationCoordinator(1)
    composition = create_searchable_pdf_export_worker_composition(
        _config(tmp_path),
        heavy_operations=gate,
    )
    worker = cast(_FakeWorkerClient, cast(Any, composition)._worker)

    assert isinstance(worker.launcher, WindowsProcessLauncher)
    assert worker.required_capabilities == (SEARCHABLE_PDF_EXPORT_CAPABILITY,)
    assert isinstance(composition.exporter, BrokeredSearchablePdfExporter)
    assert cast(Any, composition.exporter)._worker is worker
    assert composition.heavy_operations is gate
    assert cast(Any, composition.exporter)._heavy_operations is gate
    assert composition.exporter.maximum_output_bytes == _limits().output_bytes
    assert worker.constructed_thread == threading.get_ident()
    assert composition.resource_snapshot() == worker.snapshot

    exporter = composition.exporter
    composition.close()
    composition.close()
    assert composition.closed
    assert exporter.revoked
    assert worker.terminate_calls == 1


def test_composition_rejects_a_different_application_gate(tmp_path: Path) -> None:
    with pytest.raises(WorkerClientError) as raised:
        create_searchable_pdf_export_worker_composition(
            _config(tmp_path),
            heavy_operations=HeavyOperationCoordinator(2),
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


@pytest.mark.parametrize("maximum_processes", (1, 3))
def test_config_rejects_process_count_other_than_worker_plus_tesseract(
    tmp_path: Path,
    maximum_processes: int,
) -> None:
    values = _paths(tmp_path)
    with pytest.raises(WorkerClientError) as raised:
        create_searchable_pdf_export_worker_composition_config(
            **values,
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=512 * 1024 * 1024,
                job_memory_bytes=768 * 1024 * 1024,
                cpu_time_ms=120_000,
                maximum_processes=maximum_processes,
            ),
            timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
            maximum_heavy_operation_concurrency=1,
            limits=_limits(),
            status_capacity_bytes=2 * 1024 * 1024,
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
