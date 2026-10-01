"""Opt-in composition for transient image-stage candidates using export authority."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from typing import Self

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
from paperworkshell.worker.client.models import WorkerResourceSnapshot
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    _require_heavy_operations,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_image_stage_broker import (
    BrokeredPdfImageStageProcessor,
    PdfImageStageBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
    PDF_IMAGE_STAGE_CAPABILITY,
)


class PdfImageStageWorkerComposition(AbstractContextManager["PdfImageStageWorkerComposition"]):
    def __init__(self, worker: WorkerClient, processor: BrokeredPdfImageStageProcessor) -> None:
        self._worker = worker
        self._processor = processor
        self._owner = threading.get_ident()
        self._closed = False

    @property
    def image_stage_processor(self) -> BrokeredPdfImageStageProcessor:
        return self._processor

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner:
            reject_client(WorkerClientErrorCode.OWNER_THREAD_REQUIRED, WorkerClientContext.OWNER)

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        self._require_owner()
        return self._worker.resource_snapshot()

    def last_cancellation_acknowledgement_ms(self) -> int | None:
        self._require_owner()
        return self._worker.query().last_cancellation_acknowledgement_ms

    def close(self) -> None:
        self._require_owner()
        if self._closed:
            return
        self._processor._revoke_permanently()
        pending: BaseException | None = None
        for cleanup in (
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
        if self._closed or self._processor.revoked:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"PdfImageStageWorkerComposition(closed={self._closed})"


def create_pdf_image_stage_worker_composition(
    config: PdfExportWorkerCompositionConfig,
    *,
    heavy_operations: HeavyOperationCoordinator,
    evidence_capacity_bytes: int = MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
) -> PdfImageStageWorkerComposition:
    """Reuse validated qpdf/runtime authority, requesting only the explicit capability.

    The existing export configuration describes the same admitted executables and
    limits. Its ordinary-export operation version does not select an image stage.
    Every stage still needs a separate typed request at the broker boundary.
    """

    if type(config) is not PdfExportWorkerCompositionConfig:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID, WorkerClientContext.CONFIGURATION
        )
    _require_heavy_operations(heavy_operations, config.maximum_heavy_operation_concurrency)
    policy = PdfImageStageBrokerPolicy(
        config.export_policy.limits,
        evidence_capacity_bytes,
        config.export_policy.pump_interval_seconds,
    )
    worker = WorkerClient(
        WindowsProcessLauncher(config._launch_config),
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=(PDF_IMAGE_STAGE_CAPABILITY,),
    )
    return PdfImageStageWorkerComposition(
        worker, BrokeredPdfImageStageProcessor(worker, policy, heavy_operations)
    )
