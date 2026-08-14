from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import paperworkshell.worker._windows_broker as broker_module
import paperworkshell.worker.client._windows as windows_launcher
import paperworkshell.worker.quality_ocr_review_buffers as buffers_module
import paperworkshell.worker.quality_ocr_review_composition as composition_module
from paperworkshell.domain.pdf_inspection import PdfInspectionError
from paperworkshell.domain.quality_ocr_review import QualityOcrReviewError
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import (
    TesseractRuntimeAdmission,
    tesseract_runtime_authority_id,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    QUALITY_OCR_REVIEW_CAPABILITY,
    ResourceLimits,
)
from paperworkshell.worker.protocol.operation_scope import (
    QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS,
    WorkerOperationScope,
)
from paperworkshell.worker.quality_ocr_review_broker import QualityOcrReviewBrokerPolicy
from paperworkshell.worker.quality_ocr_review_buffers import WindowsQualityOcrInputBuffer
from paperworkshell.worker.quality_ocr_review_composition import (
    QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
    QualityOcrReviewWorkerComposition,
    QualityOcrReviewWorkerCompositionConfig,
    create_quality_ocr_review_worker_composition,
    create_quality_ocr_review_worker_composition_config,
)


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


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=60_000,
        cpu_time_ms=45_000,
        memory_bytes=256 * 1024 * 1024,
        temporary_storage_bytes=4 * 1024 * 1024,
        output_bytes=1024 * 1024,
        page_count=1,
        pixel_count=256 * 256,
    )


def _config(
    tmp_path: Path,
    *,
    maximum_processes: int = QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
    handshake_seconds: float = 20.0,
) -> QualityOcrReviewWorkerCompositionConfig:
    return create_quality_ocr_review_worker_composition_config(
        **_paths(tmp_path),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=256 * 1024 * 1024,
            job_memory_bytes=512 * 1024 * 1024,
            cpu_time_ms=120_000,
            maximum_processes=maximum_processes,
        ),
        timeout_policy=WorkerTimeoutPolicy(handshake_seconds, 2.0, 10.0),
        maximum_heavy_operation_concurrency=1,
        limits=_limits(),
    )


def test_config_binds_exact_authenticated_runtime_authority_and_limits(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config.resource_policy.maximum_processes == 2
    assert config._launch_config.tesseract_executable == config.tesseract_executable
    assert config._launch_config.tessdata_root == config.tessdata_root
    assert config._launch_config.operation_scope is WorkerOperationScope.QUALITY_OCR_REVIEW
    assert QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS == 15.0
    assert config.timeout_policy.handshake_seconds == 20.0
    assert config._runtime_authority_id == tesseract_runtime_authority_id(
        config.tesseract_executable,
        config.tessdata_root,
    )
    assert config.broker_policy == QualityOcrReviewBrokerPolicy(_limits())
    assert str(config.tesseract_executable) not in repr(config)


@pytest.mark.parametrize("maximum_processes", [1, 3])
def test_config_requires_exact_worker_plus_tesseract_process_bound(
    tmp_path: Path,
    maximum_processes: int,
) -> None:
    with pytest.raises(WorkerClientError) as rejected:
        _config(tmp_path, maximum_processes=maximum_processes)
    assert rejected.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_config_requires_handshake_to_exceed_the_shared_admission_deadline(
    tmp_path: Path,
) -> None:
    with pytest.raises(WorkerClientError) as rejected:
        _config(
            tmp_path,
            handshake_seconds=QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS,
        )

    assert rejected.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_composition_uses_shared_gate_requires_capability_and_closes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    gate = HeavyOperationCoordinator(1)
    observed: dict[str, object] = {}

    class _Launcher:
        def __init__(self, launch_config: object) -> None:
            observed["launch_config"] = launch_config

    class _Worker:
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
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    class _Reviewer:
        def __init__(self, worker: object, policy: object, *, heavy_operations: object) -> None:
            observed.update(worker=worker, policy=policy, heavy_operations=heavy_operations)
            self.revoked = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    monkeypatch.setattr(composition_module, "WindowsProcessLauncher", _Launcher)
    monkeypatch.setattr(composition_module, "WorkerClient", _Worker)
    monkeypatch.setattr(composition_module, "BrokeredQualityOcrReviewer", _Reviewer)

    composition = create_quality_ocr_review_worker_composition(
        config,
        heavy_operations=gate,
    )
    reviewer = composition.reviewer
    composition.close()
    composition.close()

    assert composition.heavy_operations is gate
    assert composition.runtime_authority_id == config._runtime_authority_id
    assert observed["launch_config"] is config._launch_config
    assert observed["required_capabilities"] == (QUALITY_OCR_REVIEW_CAPABILITY,)
    assert observed["heavy_operations"] is gate
    assert cast(Any, reviewer).revoked
    assert cast_worker(observed["worker"]).terminate_calls == 1
    assert composition.closed


def cast_worker(value: object) -> Any:
    return value


def test_composition_rejects_distinct_or_mismatched_heavy_gate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(WorkerClientError) as rejected:
        create_quality_ocr_review_worker_composition(
            config,
            heavy_operations=HeavyOperationCoordinator(2),
        )
    assert rejected.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_composition_close_retries_retained_last_page_input_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter((False, False, True))
    monkeypatch.setattr(buffers_module, "_close_handle", lambda _handle: next(outcomes))
    retained = WindowsQualityOcrInputBuffer(123, 64)
    with pytest.raises(QualityOcrReviewError):
        retained.close()

    class _Worker:
        def __init__(self) -> None:
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    class _Reviewer:
        def __init__(self) -> None:
            self.revoked: bool = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    worker = _Worker()
    reviewer = _Reviewer()
    composition = QualityOcrReviewWorkerComposition(
        cast_worker(worker),
        cast_worker(reviewer),
        HeavyOperationCoordinator(1),
        "a" * 64,
    )

    with pytest.raises(QualityOcrReviewError):
        composition.close()

    assert cast(Any, composition).closed is False
    assert retained.read_handle_value == 123
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == [retained]

    composition.close()

    assert composition.closed
    assert cast(Any, reviewer).revoked
    assert worker.terminate_calls == 2
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == []


def test_composition_close_refuses_persistent_retained_input_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(buffers_module, "_close_handle", lambda _handle: False)
    retained = WindowsQualityOcrInputBuffer(123, 64)
    with pytest.raises(QualityOcrReviewError):
        retained.close()

    class _Worker:
        def terminate(self) -> None:
            return None

    class _Reviewer:
        def __init__(self) -> None:
            self.revoked: bool = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    composition = QualityOcrReviewWorkerComposition(
        cast_worker(_Worker()),
        cast_worker(_Reviewer()),
        HeavyOperationCoordinator(1),
        "a" * 64,
    )

    for _ in range(2):
        with pytest.raises(QualityOcrReviewError):
            composition.close()
        assert not composition.closed
        assert retained.read_handle_value == 123
        assert buffers_module._INPUT_CLEANUP_QUARANTINE == [retained]

    monkeypatch.setattr(buffers_module, "_close_handle", lambda _handle: True)
    buffers_module.drain_quality_ocr_input_cleanup()
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == []
    composition.close()
    assert composition.closed


def test_composition_close_retries_retained_last_page_result_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer_outcomes = iter((False, False, True))

    def close_handle(handle: int) -> bool:
        return next(writer_outcomes) if handle == 22 else True

    monkeypatch.setattr(broker_module, "_close_handle", close_handle)
    retained = WindowsResultPipe(11, 22)
    with pytest.raises(PdfInspectionError):
        retained.close()

    class _Worker:
        def __init__(self) -> None:
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    class _Reviewer:
        def __init__(self) -> None:
            self.revoked: bool = False

        def _revoke_permanently(self) -> None:
            self.revoked = True

    worker = _Worker()
    reviewer = _Reviewer()
    composition = QualityOcrReviewWorkerComposition(
        cast_worker(worker),
        cast_worker(reviewer),
        HeavyOperationCoordinator(1),
        "a" * 64,
    )

    with pytest.raises(PdfInspectionError):
        composition.close()

    assert cast(Any, composition).closed is False
    assert retained._write_handle == 22
    assert broker_module._PIPE_CLEANUP_QUARANTINE == [retained]

    composition.close()

    assert composition.closed
    assert cast(Any, reviewer).revoked
    assert worker.terminate_calls == 2
    assert broker_module._PIPE_CLEANUP_QUARANTINE == []
