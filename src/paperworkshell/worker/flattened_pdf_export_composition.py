"""Owner-thread composition for the separately privileged flattened exporter."""

from __future__ import annotations

import math
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
from paperworkshell.worker.client.models import (
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
from paperworkshell.worker.flattened_pdf_export_buffers import (
    drain_flattened_pdf_raster_cleanup,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationSnapshot
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    MAX_SAFE_INTEGER,
    ResourceLimits,
)

FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES = 2


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportWorkerCompositionConfig:
    """Fixed Python/qpdf authorities plus versioned worker and broker limits."""

    python_executable: Path = field(repr=False)
    bootstrap_script: Path = field(repr=False)
    runtime_roots: tuple[Path, ...] = field(repr=False)
    code_roots: tuple[Path, ...] = field(repr=False)
    working_directory: Path = field(repr=False)
    qpdf_executable: Path = field(repr=False)
    resource_policy: WorkerResourcePolicy
    timeout_policy: WorkerTimeoutPolicy
    maximum_heavy_operation_concurrency: int
    broker_policy: FlattenedPdfExportBrokerPolicy
    resource_sampling_interval_ms: int = 50
    venv_launcher: Path | None = field(default=None, repr=False)
    recovery_authority_roots: tuple[Path, ...] = field(default=(), repr=False)
    _launch_config: WindowsLaunchConfig = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.runtime_roots) is not tuple
            or type(self.code_roots) is not tuple
            or type(self.resource_policy) is not WorkerResourcePolicy
            or self.resource_policy.maximum_processes
            != FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES
            or type(self.timeout_policy) is not WorkerTimeoutPolicy
            or type(self.maximum_heavy_operation_concurrency) is not int
            or self.maximum_heavy_operation_concurrency != 1
            or type(self.broker_policy) is not FlattenedPdfExportBrokerPolicy
            or type(self.resource_sampling_interval_ms) is not int
            or not 1 <= self.resource_sampling_interval_ms <= MAX_SAFE_INTEGER
            or not math.isclose(
                self.broker_policy.pump_interval_seconds * 1_000,
                self.resource_sampling_interval_ms,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        object.__setattr__(
            self,
            "_launch_config",
            WindowsLaunchConfig(
                python_executable=self.python_executable,
                bootstrap_script=self.bootstrap_script,
                runtime_roots=self.runtime_roots,
                code_roots=self.code_roots,
                working_directory=self.working_directory,
                venv_launcher=self.venv_launcher,
                recovery_authority_roots=self.recovery_authority_roots,
                qpdf_executable=self.qpdf_executable,
            ),
        )

    def __repr__(self) -> str:
        return "FlattenedPdfExportWorkerCompositionConfig(<validated fixed resources>)"


class FlattenedPdfExportWorkerComposition(
    AbstractContextManager["FlattenedPdfExportWorkerComposition"]
):
    """Own one one-shot client and its handle-only flattened-export broker."""

    __slots__ = (
        "_closed",
        "_exporter",
        "_heavy_operations",
        "_owner_thread_id",
        "_worker",
    )

    def __init__(
        self,
        worker: WorkerClient,
        exporter: BrokeredFlattenedPdfExporter,
        heavy_operations: HeavyOperationCoordinator,
    ) -> None:
        self._worker = worker
        self._exporter = exporter
        self._heavy_operations = heavy_operations
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    @property
    def exporter(self) -> BrokeredFlattenedPdfExporter:
        return self._exporter

    @property
    def heavy_operations(self) -> HeavyOperationCoordinator:
        return self._heavy_operations

    @property
    def closed(self) -> bool:
        return self._closed

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        self._require_owner()
        return self._worker.resource_snapshot()

    def private_temp_snapshot(self) -> WorkerPrivateTempSnapshot | None:
        self._require_owner()
        return self._worker.private_temp_snapshot()

    def last_operation_result(self) -> WorkerOperationResultSnapshot | None:
        self._require_owner()
        return self._worker.query().last_result

    def worker_tree_termination_confirmed(self) -> bool:
        self._require_owner()
        snapshot = self._worker.query()
        return bool(
            snapshot.generation > 0
            and snapshot.phase is WorkerLifecyclePhase.STOPPED
            and snapshot.pid is None
            and snapshot.active_operation is None
        )

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
            self._exporter._revoke_permanently()
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
            drain_flattened_pdf_raster_cleanup,
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
        if self._closed or self._exporter.revoked:
            reject_client(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"FlattenedPdfExportWorkerComposition(closed={self._closed})"

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )


def create_flattened_pdf_export_worker_composition(
    config: FlattenedPdfExportWorkerCompositionConfig,
    *,
    heavy_operations: HeavyOperationCoordinator,
) -> FlattenedPdfExportWorkerComposition:
    """Build the fixed qpdf launcher, one-shot client, and flattened broker."""

    if (
        type(config) is not FlattenedPdfExportWorkerCompositionConfig
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
        required_capabilities=(FLATTENED_PDF_EXPORT_CAPABILITY,),
        resource_sampling_interval_ms=config.resource_sampling_interval_ms,
    )
    return FlattenedPdfExportWorkerComposition(
        worker,
        BrokeredFlattenedPdfExporter(
            worker,
            config.broker_policy,
            heavy_operations=heavy_operations,
        ),
        heavy_operations,
    )


def create_flattened_pdf_export_worker_composition_config(
    *,
    python_executable: Path,
    bootstrap_script: Path,
    runtime_roots: tuple[Path, ...],
    code_roots: tuple[Path, ...],
    working_directory: Path,
    qpdf_executable: Path,
    resource_policy: WorkerResourcePolicy,
    timeout_policy: WorkerTimeoutPolicy,
    maximum_heavy_operation_concurrency: int,
    limits: ResourceLimits,
    resource_sampling_interval_ms: int = 50,
    pump_interval_seconds: float = 0.050,
    venv_launcher: Path | None = None,
    recovery_authority_roots: tuple[Path, ...] = (),
) -> FlattenedPdfExportWorkerCompositionConfig:
    """Create the flattened-export policy behind one fixed production seam."""

    return FlattenedPdfExportWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=qpdf_executable,
        resource_policy=resource_policy,
        timeout_policy=timeout_policy,
        maximum_heavy_operation_concurrency=maximum_heavy_operation_concurrency,
        resource_sampling_interval_ms=resource_sampling_interval_ms,
        broker_policy=FlattenedPdfExportBrokerPolicy(
            limits=limits,
            pump_interval_seconds=pump_interval_seconds,
        ),
        venv_launcher=venv_launcher,
        recovery_authority_roots=recovery_authority_roots,
    )
