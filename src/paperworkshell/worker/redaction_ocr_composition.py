"""Explicit private worker composition, unavailable through the desktop export path."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import Self

from paperworkshell.domain.resource_policy import select_resource_policy
from paperworkshell.worker._windows_broker import (
    drain_windows_bounded_output_cleanup,
    drain_windows_result_pipe_cleanup,
)
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    reject_client,
)
from paperworkshell.worker.client.models import WorkerPrivateTempSnapshot, WorkerResourceSnapshot
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    _require_heavy_operations,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.ocr_composition import OCR_WORKER_MAXIMUM_PROCESSES
from paperworkshell.worker.protocol.codec import OperationRegistry
from paperworkshell.worker.protocol.models import (
    REDACTION_OCR_CAPABILITY,
    CapabilityDescriptor,
)
from paperworkshell.worker.protocol.operation_scope import (
    QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS,
    WorkerOperationScope,
)
from paperworkshell.worker.redaction_ocr_broker import BrokeredRedactionOcrProcessor


class RedactionOcrOperationRegistry(OperationRegistry):
    @property
    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (*super().capabilities, REDACTION_OCR_CAPABILITY)


class RedactionOcrWorkerComposition(AbstractContextManager["RedactionOcrWorkerComposition"]):
    def __init__(self, worker: WorkerClient, processor: BrokeredRedactionOcrProcessor) -> None:
        self._worker = worker
        self.processor = processor
        self._owner = threading.get_ident()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner:
            reject_client(WorkerClientErrorCode.OWNER_THREAD_REQUIRED, WorkerClientContext.OWNER)

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        self._require_owner()
        return self._worker.resource_snapshot()

    def private_temp_snapshot(self) -> WorkerPrivateTempSnapshot | None:
        self._require_owner()
        return self._worker.private_temp_snapshot()

    def last_cancellation_acknowledgement_ms(self) -> int | None:
        self._require_owner()
        return self._worker.query().last_cancellation_acknowledgement_ms

    def close(self) -> None:
        self._require_owner()
        if self._closed:
            return
        self.processor._revoke_permanently()
        pending: BaseException | None = None
        for cleanup in (
            self.processor._close_candidate,
            self._worker.terminate,
            drain_windows_bounded_output_cleanup,
            drain_windows_result_pipe_cleanup,
        ):
            try:
                cleanup()
            except BaseException as error:
                if pending is None:
                    pending = error
        if pending is not None:
            raise pending
        self._closed = True

    def __enter__(self) -> Self:
        self._require_owner()
        if self._closed or self.processor.revoked:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def create_redaction_ocr_worker_composition(
    config: PdfExportWorkerCompositionConfig,
    *,
    tesseract_executable: Path,
    tessdata_root: Path,
    heavy_operations: HeavyOperationCoordinator,
) -> RedactionOcrWorkerComposition:
    """Reuse admitted runtime resources with an exact private operation allowlist.

    The resulting page has no export/history/state-promotion authority. Ordinary
    workers never advertise this capability, including with saved active marks.
    """

    if (
        type(config) is not PdfExportWorkerCompositionConfig
        or config.resource_policy.maximum_processes != OCR_WORKER_MAXIMUM_PROCESSES
        or config.maximum_heavy_operation_concurrency != 1
        or config.timeout_policy.handshake_seconds
        <= QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS
    ):
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID, WorkerClientContext.CONFIGURATION
        )
    _require_heavy_operations(heavy_operations, config.maximum_heavy_operation_concurrency)
    launch = replace(
        config._launch_config,
        operation_scope=WorkerOperationScope.REDACTION_OCR,
        qpdf_executable=None,
        tesseract_executable=tesseract_executable,
        tessdata_root=tessdata_root,
    )
    worker = WorkerClient(
        WindowsProcessLauncher(launch),
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=(REDACTION_OCR_CAPABILITY,),
        operation_registry=RedactionOcrOperationRegistry(),
        resource_sampling_interval_ms=select_resource_policy().resource_sampling_interval_ms,
    )
    return RedactionOcrWorkerComposition(
        worker,
        BrokeredRedactionOcrProcessor(
            worker, config.export_policy, heavy_operations=heavy_operations
        ),
    )
