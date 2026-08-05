"""Content-type routing for source inspection application ports."""

from __future__ import annotations

from typing import Protocol

from paperworkshell.app.source_ingest import CancellationProbe, DeadlineProbe, ProgressSink
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestErrorCode,
    SourceProgressPhase,
    SourceSignatureState,
    reject_source_ingest,
)


class PdfSourceInspector(Protocol):
    """Inspect a prechecked PDF through the isolated document-worker port."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection: ...


class SourceInspectorRouter:
    """Route PDFs to the worker and finalize already bounded image prechecks."""

    __slots__ = ("_pdf",)

    def __init__(self, pdf: PdfSourceInspector) -> None:
        self._pdf = pdf

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        """Return typed metadata without decoding image content in the GUI process."""

        if type(source) is not PreparedSourceSummary:
            reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.source")
        if source.media_type is DetectedMediaType.PDF:
            return self._pdf.inspect(
                source,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
            )
        if source.media_type not in {DetectedMediaType.JPEG, DetectedMediaType.PNG}:
            reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.media_type")
        if deadline():
            reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, "inspection.image_deadline")
        if cancellation():
            reject_source_ingest(SourceIngestErrorCode.CANCELLED, "inspection.image_cancelled")
        progress(
            SourceImportProgress(
                operation_id=source.pending_id,
                phase=SourceProgressPhase.INSPECTING,
                completed_units=1,
                total_units=1,
            )
        )
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NOT_APPLICABLE,
            encryption_state=SourceEncryptionState.NOT_APPLICABLE,
        )
