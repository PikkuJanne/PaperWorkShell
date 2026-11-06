"""Exact protected review for the supported desktop removal-export profile."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import PageCommandHistory
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction import RedactionMarkCollection
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationReview,
    SignedSourceModificationSource,
    SignedSourceReviewStatus,
    SignedSourceSignatureState,
    build_signed_source_modification_review,
)


@dataclass(frozen=True, slots=True, repr=False)
class RemovalExportReview:
    """Review facts bound to exact manifest state, never release authority."""

    snapshot: ManifestSnapshot = field(repr=False)
    signature_review: SignedSourceModificationReview = field(repr=False)
    affected_page_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ManifestSnapshot:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.desktop_review")
        pages, signature = _review_facts(self.snapshot)
        if self.affected_page_ids != pages or self.signature_review != signature:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "removal.desktop_binding")

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot) -> RemovalExportReview:
        pages, signature = _review_facts(snapshot)
        return cls(snapshot, signature, pages)

    @property
    def case_id(self) -> UUID:
        return self.snapshot.case_id

    @property
    def manifest_revision(self) -> int:
        return self.snapshot.revision

    @property
    def signed_source_confirmation_required(self) -> bool:
        return self.signature_review.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED

    def __repr__(self) -> str:
        return "RemovalExportReview(<protected intent>)"


def _review_facts(
    snapshot: ManifestSnapshot,
) -> tuple[tuple[UUID, ...], SignedSourceModificationReview]:
    if type(snapshot) is not ManifestSnapshot:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.desktop_snapshot")
    projection = PageCommandHistory.from_snapshot(snapshot).projection
    if len(projection.page_sequence) != 1:
        reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "removal.desktop_page_scope")
    page_id = projection.page_sequence[0]
    state = projection.state_for(page_id)
    document = snapshot.document
    marks = RedactionMarkCollection.from_manifest_document(document).marks
    if not any(mark.page_id == page_id for mark in marks):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.desktop_marks")
    overlays = cast(list[dict[str, object]], document["overlays"])
    if (
        any(record["page_id"] == str(page_id) for record in overlays)
        or state.deskew_angle_millidegrees_clockwise != 0
        or state.contrast_factor_millionths != 1_000_000
        or state.background_cleanup_strength_millionths != 0
    ):
        reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "removal.desktop_processing")
    source = next(
        record
        for record in cast(list[dict[str, object]], document["sources"])
        if record["source_id"] == str(state.page.source_id)
    )
    if (
        source["detected_media_type"] != "application/pdf"
        or source["import_state"] != "complete"
        or source["encryption_state"] != "unencrypted"
        or source["signature_state"] not in {"none_detected", "markers_detected"}
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "removal.desktop_source")
    signature = build_signed_source_modification_review(
        snapshot,
        (
            SignedSourceModificationSource(
                state.page.source_id,
                SignedSourceSignatureState(source["signature_state"]),
            ),
        ),
    )
    return (page_id,), signature
