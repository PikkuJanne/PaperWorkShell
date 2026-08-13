"""Content-type routing for source inspection application ports."""

from __future__ import annotations

from typing import Protocol

from paperworkshell.app.source_ingest import CancellationProbe, DeadlineProbe, ProgressSink
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceIngestErrorCode,
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


class ImageSourceInspector(Protocol):
    """Decode and inspect one prechecked JPEG/PNG through the worker port."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection: ...


class SourceInspectorRouter:
    """Route every supported source to its isolated worker decoder."""

    __slots__ = ("_image", "_pdf")

    def __init__(self, pdf: PdfSourceInspector, image: ImageSourceInspector) -> None:
        self._pdf = pdf
        self._image = image

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
        if source.media_type in {DetectedMediaType.JPEG, DetectedMediaType.PNG}:
            return self._image.inspect(
                source,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
            )
        reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.media_type")
