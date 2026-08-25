"""Focused tests for the separately privileged flattened-export composition."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

import paperworkshell.worker.flattened_pdf_export_composition as composition_module
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
    WorkerPrivateTempSnapshot,
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.flattened_pdf_export_broker import (
    BrokeredFlattenedPdfExporter,
    FlattenedPdfExportBrokerPolicy,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    FlattenedPdfExportWorkerCompositionConfig,
    create_flattened_pdf_export_worker_composition,
    create_flattened_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    CapabilityDescriptor,
    OperationOutcome,
    ResourceLimits,
)


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
        self.constructed_thread = threading.get_ident()
        self.terminate_calls = 0
        self.snapshot = WorkerResourceSnapshot(
            resource_policy.process_memory_bytes,
            resource_policy.job_memory_bytes,
            1,
            2,
        )
        self.temp_snapshot = WorkerPrivateTempSnapshot(7, 2, resource_sampling_interval_ms, True)
        self.result_snapshot = WorkerOperationResultSnapshot(
            1,
            uuid4(),
            OperationOutcome.CANCELLED,
            (),
            None,
        )

    def terminate(self) -> None:
        self.terminate_calls += 1

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        return self.snapshot

    def private_temp_snapshot(self) -> WorkerPrivateTempSnapshot:
        return self.temp_snapshot

    def query(self) -> WorkerClientSnapshot:
        return WorkerClientSnapshot(
            generation=1,
            phase=WorkerLifecyclePhase.STOPPED,
            case_id=None,
            session_id=None,
            worker_instance_id=None,
            pid=None,
            capabilities=(),
            active_operation=None,
            last_result=self.result_snapshot,
            last_failure=None,
            last_resource_snapshot=self.snapshot,
            last_private_temp_snapshot=self.temp_snapshot,
            last_cancellation_acknowledgement_ms=17,
        )


def _paths(tmp_path: Path) -> dict[str, Any]:
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    qpdf_root = tmp_path / "qpdf"
    for path in (runtime, code, qpdf_root):
        path.mkdir(parents=True)
    python = runtime / "python.exe"
    bootstrap = code / "host.py"
    qpdf = qpdf_root / "qpdf.exe"
    python.write_bytes(b"python")
    bootstrap.write_text("# host\n", encoding="utf-8")
    qpdf.write_bytes(b"qpdf")
    return {
        "python_executable": python,
        "bootstrap_script": bootstrap,
        "runtime_roots": (runtime,),
        "code_roots": (code,),
        "working_directory": code,
        "qpdf_executable": qpdf,
    }


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=512 * 1024 * 1024,
        output_bytes=128 * 1024 * 1024,
        page_count=100,
        pixel_count=4_000_000,
    )


def _config(tmp_path: Path) -> FlattenedPdfExportWorkerCompositionConfig:
    return create_flattened_pdf_export_worker_composition_config(
        **_paths(tmp_path),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=90_000,
            maximum_processes=FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
        maximum_heavy_operation_concurrency=1,
        limits=_limits(),
    )


def test_config_binds_qpdf_and_central_bounded_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config._launch_config.qpdf_executable == config.qpdf_executable
    assert config.qpdf_executable in config._launch_config.qpdf_access_resources
    assert config.resource_policy.maximum_processes == 2
    assert config.maximum_heavy_operation_concurrency == 1
    assert config.resource_sampling_interval_ms == 50
    assert config.broker_policy == FlattenedPdfExportBrokerPolicy(_limits())
    assert config.broker_policy.pump_interval_seconds == 0.05
    rendered = repr(config)
    assert str(config.qpdf_executable) not in rendered
    assert str(config.python_executable) not in rendered


def test_composition_registers_only_flattened_capability_and_shares_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain_calls: list[str] = []
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
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
    monkeypatch.setattr(
        composition_module,
        "drain_flattened_pdf_raster_cleanup",
        lambda: drain_calls.append("raster"),
    )
    gate = HeavyOperationCoordinator(1)
    composition = create_flattened_pdf_export_worker_composition(
        _config(tmp_path),
        heavy_operations=gate,
    )
    worker = cast(_FakeWorkerClient, cast(Any, composition)._worker)

    assert isinstance(worker.launcher, WindowsProcessLauncher)
    assert worker.required_capabilities == (FLATTENED_PDF_EXPORT_CAPABILITY,)
    assert isinstance(composition.exporter, BrokeredFlattenedPdfExporter)
    assert cast(Any, composition.exporter)._worker is worker
    assert composition.heavy_operations is gate
    assert cast(Any, composition.exporter)._heavy_operations is gate
    assert composition.exporter.maximum_output_bytes == _limits().output_bytes
    assert worker.constructed_thread == threading.get_ident()
    assert worker.resource_sampling_interval_ms == 50
    assert composition.resource_snapshot() == worker.snapshot
    assert composition.private_temp_snapshot() == worker.temp_snapshot
    assert composition.last_operation_result() == worker.result_snapshot
    assert composition.worker_tree_termination_confirmed()
    assert composition.last_cancellation_acknowledgement_ms() == 17

    exporter = composition.exporter
    composition.close()
    composition.close()

    assert composition.closed
    assert exporter.revoked
    assert worker.terminate_calls == 1
    assert drain_calls == ["output", "pipe", "raster"]
    with pytest.raises(WorkerClientError) as raised:
        composition.__enter__()
    assert raised.value.code is WorkerClientErrorCode.STATE_INVALID


def test_composition_rejects_a_different_application_gate(tmp_path: Path) -> None:
    with pytest.raises(WorkerClientError) as raised:
        create_flattened_pdf_export_worker_composition(
            _config(tmp_path),
            heavy_operations=HeavyOperationCoordinator(2),
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


@pytest.mark.parametrize(
    "accessor",
    (
        "resource_snapshot",
        "private_temp_snapshot",
        "last_operation_result",
        "worker_tree_termination_confirmed",
        "last_cancellation_acknowledgement_ms",
    ),
)
def test_resource_evidence_accessors_require_owner_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    accessor: str,
) -> None:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    composition = create_flattened_pdf_export_worker_composition(
        _config(tmp_path),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    observed: list[BaseException] = []

    def read() -> None:
        try:
            getattr(composition, accessor)()
        except BaseException as error:
            observed.append(error)

    thread = threading.Thread(target=read)
    thread.start()
    thread.join()

    assert len(observed) == 1
    assert isinstance(observed[0], WorkerClientError)
    assert observed[0].code is WorkerClientErrorCode.OWNER_THREAD_REQUIRED
    composition.close()


@pytest.mark.parametrize("maximum_processes", (1, 3))
def test_config_rejects_process_count_other_than_worker_plus_qpdf(
    tmp_path: Path,
    maximum_processes: int,
) -> None:
    with pytest.raises(WorkerClientError) as raised:
        create_flattened_pdf_export_worker_composition_config(
            **_paths(tmp_path),
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=512 * 1024 * 1024,
                job_memory_bytes=768 * 1024 * 1024,
                cpu_time_ms=90_000,
                maximum_processes=maximum_processes,
            ),
            timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
            maximum_heavy_operation_concurrency=1,
            limits=_limits(),
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


@pytest.mark.parametrize(
    ("maximum_concurrency", "sampling_interval_ms", "pump_interval_seconds"),
    ((2, 50, 0.05), (1, 25, 0.05), (1, 50, 0.025), (1, 0, 0.05)),
)
def test_config_rejects_unbound_application_resource_policy(
    tmp_path: Path,
    maximum_concurrency: int,
    sampling_interval_ms: int,
    pump_interval_seconds: float,
) -> None:
    with pytest.raises(WorkerClientError) as raised:
        create_flattened_pdf_export_worker_composition_config(
            **_paths(tmp_path),
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=512 * 1024 * 1024,
                job_memory_bytes=768 * 1024 * 1024,
                cpu_time_ms=90_000,
                maximum_processes=FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
            ),
            timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
            maximum_heavy_operation_concurrency=maximum_concurrency,
            limits=_limits(),
            resource_sampling_interval_ms=sampling_interval_ms,
            pump_interval_seconds=pump_interval_seconds,
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
