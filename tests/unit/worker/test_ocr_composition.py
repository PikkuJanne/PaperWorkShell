"""PWS-0204 tests for the separate OCR composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import paperworkshell.worker.client._windows as windows_launcher
import paperworkshell.worker.ocr_composition as composition_module
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.ocr_broker import OcrBrokerPolicy
from paperworkshell.worker.ocr_composition import (
    OCR_WORKER_MAXIMUM_PROCESSES,
    OcrWorkerCompositionConfig,
    create_ocr_worker_composition,
    create_ocr_worker_composition_config,
)
from paperworkshell.worker.protocol.models import OCR_PROCESS_CAPABILITY, ResourceLimits


@pytest.fixture(autouse=True)
def _admit_synthetic_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def admit(executable: Path, tessdata_root: Path) -> TesseractRuntimeAdmission:
        root = executable.parent.parent
        return TesseractRuntimeAdmission(
            root=root,
            executable=executable,
            tessdata_root=tessdata_root,
            resources=(root, executable.parent, executable, root / "share", tessdata_root),
        )

    monkeypatch.setattr(windows_launcher, "admit_packaged_tesseract_runtime", admit)


def _paths(tmp_path: Path) -> dict[str, Any]:
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    tool = tmp_path / "tesseract" / "bin"
    tessdata = tmp_path / "tesseract" / "share" / "tessdata"
    for path in (runtime, code, tool, tessdata):
        path.mkdir(parents=True)
    python = runtime / "python.exe"
    bootstrap = code / "host.py"
    tesseract = tool / "tesseract.exe"
    python.write_bytes(b"python")
    bootstrap.write_text("# host\n", encoding="utf-8")
    tesseract.write_bytes(b"tesseract")
    (tessdata / "eng.traineddata").write_bytes(b"eng")
    return {
        "python_executable": python,
        "bootstrap_script": bootstrap,
        "runtime_roots": (runtime,),
        "code_roots": (code,),
        "working_directory": code,
        "tesseract_executable": tesseract,
        "tessdata_root": tessdata,
    }


def _resource_policy(*, maximum_processes: int = 2) -> WorkerResourcePolicy:
    return WorkerResourcePolicy(
        process_memory_bytes=256 * 1024 * 1024,
        job_memory_bytes=512 * 1024 * 1024,
        cpu_time_ms=120_000,
        maximum_processes=maximum_processes,
    )


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=180_000,
        cpu_time_ms=120_000,
        memory_bytes=256 * 1024 * 1024,
        temporary_storage_bytes=512 * 1024 * 1024,
        output_bytes=128 * 1024 * 1024,
        page_count=100,
        pixel_count=100_000_000,
    )


def _config(tmp_path: Path, *, maximum_processes: int = 2) -> OcrWorkerCompositionConfig:
    return create_ocr_worker_composition_config(
        **_paths(tmp_path),
        resource_policy=_resource_policy(maximum_processes=maximum_processes),
        timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0),
        maximum_heavy_operation_concurrency=1,
        limits=_limits(),
    )


def test_config_binds_exact_tesseract_resources_and_two_process_job(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config.resource_policy.maximum_processes == OCR_WORKER_MAXIMUM_PROCESSES
    assert config._launch_config.tesseract_executable == config.tesseract_executable
    assert config._launch_config.tessdata_root == config.tessdata_root
    assert config.broker_policy == OcrBrokerPolicy(_limits())
    assert str(config.tesseract_executable) not in repr(config)


@pytest.mark.parametrize("maximum_processes", [1, 3])
def test_config_rejects_any_job_process_count_other_than_worker_plus_tesseract(
    tmp_path: Path,
    maximum_processes: int,
) -> None:
    with pytest.raises(WorkerClientError) as raised:
        _config(tmp_path, maximum_processes=maximum_processes)

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_composition_shares_exact_app_gate_and_requires_ocr_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    gate = HeavyOperationCoordinator(1)
    observed: dict[str, object] = {}
    drain_calls: list[str] = []

    class FakeLauncher:
        def __init__(self, launch_config: object) -> None:
            observed["launch_config"] = launch_config

    class FakeWorker:
        def __init__(
            self,
            launcher: object,
            resource_policy: object,
            timeout_policy: object,
            *,
            required_capabilities: object,
        ) -> None:
            observed.update(
                launcher=launcher,
                resource_policy=resource_policy,
                timeout_policy=timeout_policy,
                required_capabilities=required_capabilities,
            )
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

    class FakeProcessor:
        def __init__(self, worker: object, policy: object, *, heavy_operations: object) -> None:
            observed.update(worker=worker, policy=policy, heavy_operations=heavy_operations)
            self.revoked = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    monkeypatch.setattr(composition_module, "WindowsProcessLauncher", FakeLauncher)
    monkeypatch.setattr(composition_module, "WorkerClient", FakeWorker)
    monkeypatch.setattr(composition_module, "BrokeredOcrProcessor", FakeProcessor)
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

    composition = create_ocr_worker_composition(config, heavy_operations=gate)

    assert composition.heavy_operations is gate
    assert observed["launch_config"] is config._launch_config
    assert observed["resource_policy"] is config.resource_policy
    assert observed["required_capabilities"] == (OCR_PROCESS_CAPABILITY,)
    assert observed["heavy_operations"] is gate
    processor = composition.ocr_processor
    composition.close()
    assert processor.revoked
    assert cast_fake_worker(observed["worker"]).terminated
    assert composition.closed
    assert drain_calls == ["output", "pipe"]


def cast_fake_worker(value: object) -> Any:
    return value


def test_composition_rejects_a_numerically_equal_but_distinct_gate(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(WorkerClientError) as raised:
        create_ocr_worker_composition(
            config,
            heavy_operations=HeavyOperationCoordinator(2),
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
