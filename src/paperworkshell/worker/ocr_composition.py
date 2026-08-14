"""Owner-thread composition for the separately privileged one-shot OCR worker."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from paperworkshell.worker._windows_broker import (
    drain_windows_bounded_output_cleanup,
    drain_windows_result_pipe_cleanup,
)
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    reject_client,
)
from paperworkshell.worker.client.models import WorkerResourceSnapshot, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import tesseract_runtime_authority_id
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationSnapshot
from paperworkshell.worker.ocr_broker import BrokeredOcrProcessor, OcrBrokerPolicy
from paperworkshell.worker.protocol.models import OCR_PROCESS_CAPABILITY, ResourceLimits

OCR_WORKER_MAXIMUM_PROCESSES = 2


@dataclass(frozen=True, slots=True, repr=False)
class OcrWorkerCompositionConfig:
    """Fixed Python, Tesseract, tessdata, and broker policy for one OCR client."""

    python_executable: Path = field(repr=False)
    bootstrap_script: Path = field(repr=False)
    runtime_roots: tuple[Path, ...] = field(repr=False)
    code_roots: tuple[Path, ...] = field(repr=False)
    working_directory: Path = field(repr=False)
    tesseract_executable: Path = field(repr=False)
    tessdata_root: Path = field(repr=False)
    resource_policy: WorkerResourcePolicy
    timeout_policy: WorkerTimeoutPolicy
    maximum_heavy_operation_concurrency: int
    broker_policy: OcrBrokerPolicy
    venv_launcher: Path | None = field(default=None, repr=False)
    _launch_config: WindowsLaunchConfig = field(init=False, repr=False, compare=False)
    _ocr_runtime_authority_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.runtime_roots) is not tuple
            or type(self.code_roots) is not tuple
            or type(self.resource_policy) is not WorkerResourcePolicy
            or self.resource_policy.maximum_processes != OCR_WORKER_MAXIMUM_PROCESSES
            or type(self.timeout_policy) is not WorkerTimeoutPolicy
            or type(self.maximum_heavy_operation_concurrency) is not int
            or self.maximum_heavy_operation_concurrency != 1
            or type(self.broker_policy) is not OcrBrokerPolicy
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        launch_config = WindowsLaunchConfig(
            python_executable=self.python_executable,
            bootstrap_script=self.bootstrap_script,
            runtime_roots=self.runtime_roots,
            code_roots=self.code_roots,
            working_directory=self.working_directory,
            venv_launcher=self.venv_launcher,
            tesseract_executable=self.tesseract_executable,
            tessdata_root=self.tessdata_root,
        )
        object.__setattr__(self, "_launch_config", launch_config)
        object.__setattr__(
            self,
            "_ocr_runtime_authority_id",
            tesseract_runtime_authority_id(self.tesseract_executable, self.tessdata_root),
        )

    def __repr__(self) -> str:
        return "OcrWorkerCompositionConfig(<validated fixed resources>)"


class OcrWorkerComposition(AbstractContextManager["OcrWorkerComposition"]):
    """Own one OCR-only client and its volatile-candidate broker port."""

    __slots__ = (
        "_closed",
        "_heavy_operations",
        "_ocr_processor",
        "_ocr_runtime_authority_id",
        "_owner_thread_id",
        "_worker",
    )

    def __init__(
        self,
        worker: WorkerClient,
        ocr_processor: BrokeredOcrProcessor,
        heavy_operations: HeavyOperationCoordinator,
        ocr_runtime_authority_id: str,
    ) -> None:
        self._worker = worker
        self._ocr_processor = ocr_processor
        self._heavy_operations = heavy_operations
        self._ocr_runtime_authority_id = ocr_runtime_authority_id
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    @property
    def ocr_processor(self) -> BrokeredOcrProcessor:
        """Expose only the handle-bound OCR application port."""

        return self._ocr_processor

    @property
    def heavy_operations(self) -> HeavyOperationCoordinator:
        """Expose the exact app-scoped heavy-work gate identity."""

        return self._heavy_operations

    @property
    def ocr_runtime_authority_id(self) -> str:
        """Expose only the path-free runtime binding for app-root correlation."""

        return self._ocr_runtime_authority_id

    @property
    def closed(self) -> bool:
        return self._closed

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        self._require_owner()
        return self._worker.resource_snapshot()

    def last_cancellation_acknowledgement_ms(self) -> int | None:
        self._require_owner()
        return self._worker.query().last_cancellation_acknowledgement_ms

    def heavy_operation_snapshot(self) -> HeavyOperationSnapshot:
        return self._heavy_operations.snapshot()

    def close(self) -> None:
        self._require_owner()
        if self._closed:
            return
        pending: BaseException | None = None
        try:
            self._ocr_processor._revoke_permanently()
        except BaseException as error:
            pending = error
        try:
            self._worker.terminate()
        except BaseException as error:
            if pending is None:
                pending = error
        for drain in (
            drain_windows_bounded_output_cleanup,
            drain_windows_result_pipe_cleanup,
        ):
            try:
                drain()
            except BaseException as error:
                if pending is None:
                    pending = error
        if pending is not None:
            raise pending
        self._closed = True

    def __enter__(self) -> Self:
        self._require_owner()
        if self._closed or self._ocr_processor.revoked:
            reject_client(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"OcrWorkerComposition(closed={self._closed})"

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )


def create_ocr_worker_composition(
    config: OcrWorkerCompositionConfig,
    *,
    heavy_operations: HeavyOperationCoordinator,
) -> OcrWorkerComposition:
    """Build the least-privilege launcher, client, and OCR broker."""

    if (
        type(config) is not OcrWorkerCompositionConfig
        or type(heavy_operations) is not HeavyOperationCoordinator
        or heavy_operations.snapshot().maximum_concurrency
        != config.maximum_heavy_operation_concurrency
    ):
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )
    worker = WorkerClient(
        WindowsProcessLauncher(config._launch_config),
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=(OCR_PROCESS_CAPABILITY,),
    )
    return OcrWorkerComposition(
        worker,
        BrokeredOcrProcessor(
            worker,
            config.broker_policy,
            heavy_operations=heavy_operations,
        ),
        heavy_operations,
        config._ocr_runtime_authority_id,
    )


def create_ocr_worker_composition_config(
    *,
    python_executable: Path,
    bootstrap_script: Path,
    runtime_roots: tuple[Path, ...],
    code_roots: tuple[Path, ...],
    working_directory: Path,
    tesseract_executable: Path,
    tessdata_root: Path,
    resource_policy: WorkerResourcePolicy,
    timeout_policy: WorkerTimeoutPolicy,
    maximum_heavy_operation_concurrency: int,
    limits: ResourceLimits,
    pump_interval_seconds: float = 0.050,
    venv_launcher: Path | None = None,
) -> OcrWorkerCompositionConfig:
    """Create the OCR-only composition policy behind one fixed seam."""

    return OcrWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        tesseract_executable=tesseract_executable,
        tessdata_root=tessdata_root,
        resource_policy=resource_policy,
        timeout_policy=timeout_policy,
        maximum_heavy_operation_concurrency=maximum_heavy_operation_concurrency,
        broker_policy=OcrBrokerPolicy(
            limits,
            pump_interval_seconds=pump_interval_seconds,
        ),
        venv_launcher=venv_launcher,
    )
