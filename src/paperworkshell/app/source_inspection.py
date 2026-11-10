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


class PreparedPngPreviewInspector(Protocol):
    def __call__(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection: ...


class SourceInspectorRouter:
    """Route every supported source to its isolated worker decoder."""

    __slots__ = ("_image", "_pdf", "_png_preview")

    def __init__(
        self,
        pdf: PdfSourceInspector,
        image: ImageSourceInspector,
        *,
        png_preview: PreparedPngPreviewInspector | None = None,
    ) -> None:
        self._pdf = pdf
        self._image = image
        self._png_preview = png_preview

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
            inspect = self._image.inspect
            if source.media_type is DetectedMediaType.PNG and self._png_preview is not None:
                inspect = self._png_preview
            return inspect(
                source,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
            )
        reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.media_type")
