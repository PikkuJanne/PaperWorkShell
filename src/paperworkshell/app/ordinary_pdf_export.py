"""Choose existing verified PDF pipelines without imposing a checklist."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import (
    NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
    NormalizedPageBox,
    PageCommandHistory,
)
from paperworkshell.domain.redaction import require_redaction_export_allowed
from paperworkshell.overlays.models import OverlayCollection


class OrdinaryPdfPipeline(StrEnum):
    STRUCTURAL = "structural"
    FLATTENED = "flattened"
    SEARCHABLE = "searchable"


class OrdinaryPdfExportErrorCode(StrEnum):
    COMBINATION_UNSUPPORTED = "ORDINARY_PDF_COMBINATION_UNSUPPORTED"
    SEARCHABLE_REQUIRED = "ORDINARY_PDF_SEARCHABLE_REQUIRED"


_MESSAGES: Final = {
    OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED: (
        "This version cannot combine text or marks with searchable scans, scan improvements, "
        "or picture files in one export. Nothing was exported. Export the marked PDF pages "
        "in a separate case without OCR, or keep this case and cancel. Saved edits are preserved."
    ),
    OrdinaryPdfExportErrorCode.SEARCHABLE_REQUIRED: (
        "These scan improvements require the searchable PDF pipeline in this version. "
        "Nothing was exported. Select Make scanned text searchable, or cancel and undo "
        "the scan improvements before exporting without OCR. Saved edits are preserved."
    ),
}


@dataclass(slots=True, repr=False)
class OrdinaryPdfExportError(ValueError):
    """Closed failure with no document values or native exception text."""

    code: OrdinaryPdfExportErrorCode
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        self.safe_message = _MESSAGES[self.code]
        self.args = (self.code.value, self.safe_message)


def _reject(code: OrdinaryPdfExportErrorCode) -> Never:
    raise OrdinaryPdfExportError(code) from None


def select_ordinary_pdf_pipeline(
    snapshot: ManifestSnapshot, *, make_searchable: bool
) -> OrdinaryPdfPipeline:
    """Inspect validated intent only; never parse source documents in the GUI."""

    require_redaction_export_allowed(snapshot.document)
    projection = PageCommandHistory.from_snapshot(snapshot).projection
    active = tuple(projection.state_for(page_id) for page_id in projection.page_sequence)
    collection = OverlayCollection.from_manifest_document(snapshot.document)
    has_marks = any(
        collection.visible_stack(
            page.page_id, active_crop=page.crop or NormalizedPageBox.full_page()
        )
        for page in active
    )
    has_scan_changes = any(
        page.deskew_angle_millidegrees_clockwise
        or page.contrast_factor_millionths != NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
        or page.background_cleanup_strength_millionths
        for page in active
    )
    sources = cast(list[dict[str, object]], snapshot.document["sources"])
    media = {source["source_id"]: source["detected_media_type"] for source in sources}
    has_pictures = any(media[str(page.reference.source_id)] != "application/pdf" for page in active)
    if has_marks:
        if make_searchable or has_scan_changes or has_pictures:
            _reject(OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED)
        return OrdinaryPdfPipeline.FLATTENED
    if make_searchable:
        return OrdinaryPdfPipeline.SEARCHABLE
    if has_scan_changes or (has_pictures and any(page.crop is not None for page in active)):
        _reject(OrdinaryPdfExportErrorCode.SEARCHABLE_REQUIRED)
    if any(page.crop is not None for page in active):
        return OrdinaryPdfPipeline.FLATTENED
    return OrdinaryPdfPipeline.STRUCTURAL
