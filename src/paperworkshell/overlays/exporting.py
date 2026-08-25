"""Pure, path-free contracts for overlay-aware flattened PDF export.

This module extends the frozen PDF-export-v1 facts without changing their
meaning.  It owns no paths, streams, raster bytes, native PDF objects, worker
transport, or filesystem behavior.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import (
    MAX_PAGE_COUNT,
    NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
    OrganizerProjection,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
)
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    MAX_PDF_EXPORT_PAGES,
    MAX_PDF_EXPORT_SOURCES,
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPage,
    PdfExportPlan,
    PdfExportProfile,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.overlays.models import (
    MAX_OVERLAYS,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayRecord,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset

FLATTENED_PDF_EXPORT_PLAN_VERSION: Final = 1
FLATTENED_PDF_EXPORT_PAGE_VERSION: Final = 1
FLATTENED_PDF_EXPORT_ASSET_VERSION: Final = 1
FLATTENED_PDF_EXPORT_REPORT_VERSION: Final = 1
FLATTENED_PDF_EXPORT_OUTCOME_VERSION: Final = 1
FLATTENED_PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION: Final = 1

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_MEMBERS: Final = frozenset(
    {
        "source_id",
        "display_name",
        "storage_name",
        "detected_media_type",
        "byte_size",
        "page_count",
        "sha256",
        "signature_state",
        "encryption_state",
        "import_state",
        "imported_at",
    }
)
_KNOWN_MEDIA_TYPES: Final = frozenset({"application/pdf", "image/jpeg", "image/png"})
_KNOWN_SIGNATURE_STATES: Final = frozenset(
    {"not_checked", "not_applicable", "none_detected", "markers_detected", "unknown"}
)
_KNOWN_ENCRYPTION_STATES: Final = frozenset(
    {"not_checked", "not_applicable", "unencrypted", "encrypted", "unknown"}
)


class FlattenedPdfExportErrorCode(StrEnum):
    """Closed, non-disclosing flattened-export failures."""

    MODEL_INVALID = "FLATTENED_PDF_EXPORT_MODEL_INVALID"
    MANIFEST_INVALID = "FLATTENED_PDF_EXPORT_MANIFEST_INVALID"
    PLAN_EMPTY = "FLATTENED_PDF_EXPORT_PLAN_EMPTY"
    SOURCE_UNSUPPORTED = "FLATTENED_PDF_EXPORT_SOURCE_UNSUPPORTED"
    SOURCE_ENCRYPTED = "FLATTENED_PDF_EXPORT_SOURCE_ENCRYPTED"
    SOURCE_ENCRYPTION_UNKNOWN = "FLATTENED_PDF_EXPORT_SOURCE_ENCRYPTION_UNKNOWN"
    SOURCE_SIGNATURE_UNKNOWN = "FLATTENED_PDF_EXPORT_SOURCE_SIGNATURE_UNKNOWN"
    SOURCE_INCONSISTENT = "FLATTENED_PDF_EXPORT_SOURCE_INCONSISTENT"
    SOURCE_CHANGED = "FLATTENED_PDF_EXPORT_SOURCE_CHANGED"
    TRANSFORM_UNSUPPORTED = "FLATTENED_PDF_EXPORT_TRANSFORM_UNSUPPORTED"
    RASTER_METADATA_INVALID = "FLATTENED_PDF_EXPORT_RASTER_METADATA_INVALID"
    RASTER_METADATA_MISSING = "FLATTENED_PDF_EXPORT_RASTER_METADATA_MISSING"
    RASTER_METADATA_EXTRA = "FLATTENED_PDF_EXPORT_RASTER_METADATA_EXTRA"
    ASSET_CHANGED = "FLATTENED_PDF_EXPORT_ASSET_CHANGED"
    LIMIT_EXCEEDED = "FLATTENED_PDF_EXPORT_LIMIT_EXCEEDED"
    CANCELLED = "FLATTENED_PDF_EXPORT_CANCELLED"
    TIMED_OUT = "FLATTENED_PDF_EXPORT_TIMED_OUT"
    ASSEMBLY_FAILED = "FLATTENED_PDF_EXPORT_ASSEMBLY_FAILED"
    VERIFICATION_REOPEN_FAILED = "FLATTENED_PDF_EXPORT_VERIFICATION_REOPEN_FAILED"
    VERIFICATION_STRUCTURE_FAILED = "FLATTENED_PDF_EXPORT_VERIFICATION_STRUCTURE_FAILED"
    VERIFICATION_PAGE_COUNT_MISMATCH = "FLATTENED_PDF_EXPORT_VERIFICATION_PAGE_COUNT_MISMATCH"
    VERIFICATION_ENCRYPTION_FAILED = "FLATTENED_PDF_EXPORT_VERIFICATION_ENCRYPTION_FAILED"
    VERIFICATION_RENDER_FAILED = "FLATTENED_PDF_EXPORT_VERIFICATION_RENDER_FAILED"
    VERIFICATION_SEMANTIC_MISMATCH = "FLATTENED_PDF_EXPORT_VERIFICATION_SEMANTIC_MISMATCH"
    VERIFICATION_OVERLAY_PRESENCE_FAILED = (
        "FLATTENED_PDF_EXPORT_VERIFICATION_OVERLAY_PRESENCE_FAILED"
    )
    DESTINATION_FAILED = "FLATTENED_PDF_EXPORT_DESTINATION_FAILED"
    REPORT_INVALID = "FLATTENED_PDF_EXPORT_REPORT_INVALID"
    RESULT_INVALID = "FLATTENED_PDF_EXPORT_RESULT_INVALID"
    INTERNAL_ERROR = "FLATTENED_PDF_EXPORT_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[FlattenedPdfExportErrorCode, str]] = {
    code: "PaperWorkShell could not flatten visual overlays into the PDF safely."
    for code in FlattenedPdfExportErrorCode
}
_SAFE_ERROR_MESSAGES.update(
    {
        FlattenedPdfExportErrorCode.MODEL_INVALID: (
            "PaperWorkShell received invalid flattened-PDF export data."
        ),
        FlattenedPdfExportErrorCode.MANIFEST_INVALID: (
            "The case record cannot be used to create a flattened-PDF export plan."
        ),
        FlattenedPdfExportErrorCode.PLAN_EMPTY: (
            "Select at least one page before exporting a flattened PDF."
        ),
        FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID: (
            "A placed signature image has invalid raster metadata."
        ),
        FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING: (
            "A required placed-signature-image raster is not available."
        ),
        FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA: (
            "Unexpected placed-signature-image raster metadata was supplied."
        ),
        FlattenedPdfExportErrorCode.REPORT_INVALID: (
            "PaperWorkShell received an invalid flattened-PDF verification report."
        ),
    }
)


@dataclass(slots=True, repr=False)
class FlattenedPdfExportError(ValueError):
    code: FlattenedPdfExportErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not FlattenedPdfExportErrorCode:
            self.code = FlattenedPdfExportErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "flattened.export"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"FlattenedPdfExportError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_flattened_pdf_export(
    code: FlattenedPdfExportErrorCode,
    context: str,
) -> Never:
    error = FlattenedPdfExportError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportAsset:
    """Encoded asset identity bound to authenticated RGBA8 metadata."""

    encoded_asset: OverlayAssetRecord
    raster_asset: OverlayRasterAsset
    asset_version: int = FLATTENED_PDF_EXPORT_ASSET_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.encoded_asset) is not OverlayAssetRecord
            or type(self.raster_asset) is not OverlayRasterAsset
            or self.encoded_asset.asset_id != self.raster_asset.asset_id
            or type(self.asset_version) is not int
            or self.asset_version != FLATTENED_PDF_EXPORT_ASSET_VERSION
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.asset",
            )

    @property
    def asset_id(self) -> UUID:
        return self.encoded_asset.asset_id

    def __repr__(self) -> str:
        return "FlattenedPdfExportAsset(kind='signature_image', details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportPage:
    """One base page plus active crop and visible low-to-high overlay records."""

    base_page: PdfExportPage
    active_crop: NormalizedPageBox
    overlays: tuple[OverlayRecord, ...]
    page_version: int = FLATTENED_PDF_EXPORT_PAGE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.base_page) is not PdfExportPage
            or type(self.active_crop) is not NormalizedPageBox
            or type(self.overlays) is not tuple
            or any(type(record) is not OverlayRecord for record in self.overlays)
            or type(self.page_version) is not int
            or self.page_version != FLATTENED_PDF_EXPORT_PAGE_VERSION
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.page",
            )
        prior_z = -1
        for record in self.overlays:
            if (
                record.page_id != self.base_page.page_id
                or record.z_index <= prior_z
                or self.active_crop.intersection(record.box) is None
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.MODEL_INVALID,
                    "flattened.page_overlays",
                )
            prior_z = record.z_index

    @property
    def output_page_index(self) -> int:
        return self.base_page.output_page_index

    @property
    def page_id(self) -> UUID:
        return self.base_page.page_id

    def __repr__(self) -> str:
        return (
            "FlattenedPdfExportPage("
            f"output_page_index={self.output_page_index}, overlay_count={len(self.overlays)})"
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class FlattenedPdfExportPlan:
    """Versioned executable overlay intent wrapping the PDF-export-v1 plan."""

    base_plan: PdfExportPlan
    pages: tuple[FlattenedPdfExportPage, ...]
    assets: tuple[FlattenedPdfExportAsset, ...]
    semantic_fingerprint: str = field(repr=False)
    plan_version: int

    def __init__(
        self,
        *,
        base_plan: PdfExportPlan,
        pages: tuple[FlattenedPdfExportPage, ...],
        assets: tuple[FlattenedPdfExportAsset, ...],
        plan_version: int = FLATTENED_PDF_EXPORT_PLAN_VERSION,
    ) -> None:
        _initialize_plan(self, base_plan, pages, assets, plan_version)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def visible_overlay_count(self) -> int:
        return sum(len(page.overlays) for page in self.pages)

    @property
    def overlay_page_count(self) -> int:
        return sum(bool(page.overlays) for page in self.pages)

    @property
    def text_overlay_count(self) -> int:
        return sum(
            type(record.payload) is TextualOverlayPayload
            for page in self.pages
            for record in page.overlays
        )

    @property
    def placed_signature_image_count(self) -> int:
        return sum(
            type(record.payload) is SignatureImageOverlayPayload
            for page in self.pages
            for record in page.overlays
        )

    @property
    def contains_signature_markers(self) -> bool:
        return self.base_plan.contains_signature_markers

    def __repr__(self) -> str:
        return (
            "FlattenedPdfExportPlan("
            f"page_count={self.page_count}, overlay_count={self.visible_overlay_count}, "
            f"asset_count={len(self.assets)}, plan_version={self.plan_version})"
        )


class FlattenedPdfExportCheckCode(StrEnum):
    VISIBLE_OVERLAYS_FLATTENED = "VISIBLE_OVERLAYS_FLATTENED"
    OVERLAY_PRESENCE_MATCHED = "OVERLAY_PRESENCE_MATCHED"
    TEXT_FONT_POLICY_MATCHED = "TEXT_FONT_POLICY_MATCHED"
    SIGNATURE_IMAGE_TERMINOLOGY_PRESERVED = "SIGNATURE_IMAGE_TERMINOLOGY_PRESERVED"


@dataclass(frozen=True, slots=True)
class FlattenedPdfExportCheck:
    code: FlattenedPdfExportCheckCode
    checked_count: int

    def __post_init__(self) -> None:
        if (
            type(self.code) is not FlattenedPdfExportCheckCode
            or type(self.checked_count) is not int
            or not 0 <= self.checked_count <= MAX_OVERLAYS
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.REPORT_INVALID,
                "flattened.report_check",
            )

    @property
    def claim(self) -> str:
        return _check_claim(self.code, self.checked_count)


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportVerificationReport:
    plan: FlattenedPdfExportPlan = field(repr=False)
    base_report: PdfExportVerificationReport
    checks: tuple[FlattenedPdfExportCheck, ...]
    report_version: int = FLATTENED_PDF_EXPORT_REPORT_VERSION

    def __post_init__(self) -> None:
        _validate_report(self)

    @classmethod
    def for_verified_plan(
        cls,
        plan: FlattenedPdfExportPlan,
        *,
        output_sha256: str,
        output_byte_size: int,
    ) -> FlattenedPdfExportVerificationReport:
        return _verified_report(plan, output_sha256, output_byte_size)

    @property
    def claims(self) -> tuple[str, ...]:
        return (*self.base_report.claims, *(check.claim for check in self.checks))

    def __repr__(self) -> str:
        return (
            "FlattenedPdfExportVerificationReport("
            f"page_count={self.plan.page_count}, check_count={len(self.checks)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportOutcome:
    """Committed output retaining the full transient flattened-export evidence."""

    base_outcome: PdfExportOutcome = field(repr=False)
    report: FlattenedPdfExportVerificationReport = field(repr=False)
    outcome_version: int = FLATTENED_PDF_EXPORT_OUTCOME_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.base_outcome) is not PdfExportOutcome
            or type(self.report) is not FlattenedPdfExportVerificationReport
            or self.base_outcome.report != self.report.base_report
            or type(self.outcome_version) is not int
            or self.outcome_version != FLATTENED_PDF_EXPORT_OUTCOME_VERSION
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.outcome",
            )

    @property
    def export_id(self) -> UUID:
        return self.base_outcome.export_id

    @property
    def status(self) -> PdfExportOutcomeStatus:
        return self.base_outcome.status

    @property
    def destination_reopened(self) -> bool:
        return self.base_outcome.destination_reopened

    @property
    def history_retry(self) -> PdfExportHistoryRetryReceipt:
        return self.base_outcome.history_retry

    @property
    def history_retry_required(self) -> bool:
        return self.base_outcome.history_retry_required

    @property
    def export_retry_required(self) -> bool:
        return self.base_outcome.export_retry_required

    @property
    def checks(
        self,
    ) -> tuple[PdfExportCheck | FlattenedPdfExportCheck, ...]:
        return (
            *self.base_outcome.report.checks,
            *self.report.checks,
            self.base_outcome.checks[-1],
        )

    @property
    def claims(self) -> tuple[str, ...]:
        return tuple(check.claim for check in self.checks)

    @property
    def safe_message(self) -> str:
        prefix = "Unsigned derivative: " if self.report.base_report.unsigned_derivative else ""
        if self.status is PdfExportOutcomeStatus.COMMITTED_RECORDED:
            return f"{prefix}The verified flattened PDF was saved and recorded in case history."
        return (
            f"{prefix}The verified flattened PDF was saved, but PaperWorkShell could not "
            "record it in case history. The output does not need to be exported again."
        )

    def __repr__(self) -> str:
        return (
            "FlattenedPdfExportOutcome("
            f"status={self.status!r}, page_count={self.report.plan.page_count}, "
            f"outcome_version={self.outcome_version})"
        )


def required_visible_signature_assets(
    snapshot: ManifestSnapshot,
) -> tuple[OverlayAssetRecord, ...]:
    """Enumerate exact visible placed-signature-image assets in first-use order."""

    return _required_visible_signature_assets(snapshot)


def build_flattened_pdf_export_plan(
    snapshot: ManifestSnapshot,
    raster_assets: Mapping[UUID, OverlayRasterAsset],
    *,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> FlattenedPdfExportPlan:
    """Build one executable plan from one immutable snapshot and exact rasters."""

    return _build_plan(snapshot, raster_assets, profile)


# Implementations are kept below the public contract so worker/adapters can
# import the stable type surface without depending on construction internals.
def _initialize_plan(
    target: FlattenedPdfExportPlan,
    base_plan: PdfExportPlan,
    pages: tuple[FlattenedPdfExportPage, ...],
    assets: tuple[FlattenedPdfExportAsset, ...],
    plan_version: int,
) -> None:
    if (
        type(base_plan) is not PdfExportPlan
        or type(pages) is not tuple
        or type(assets) is not tuple
        or type(plan_version) is not int
        or plan_version != FLATTENED_PDF_EXPORT_PLAN_VERSION
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.plan",
        )
    if not pages:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.PLAN_EMPTY,
            "flattened.plan_pages",
        )
    if (
        len(pages) > MAX_PDF_EXPORT_PAGES
        or len(assets) > MAX_OVERLAYS
        or any(type(page) is not FlattenedPdfExportPage for page in pages)
        or any(type(asset) is not FlattenedPdfExportAsset for asset in assets)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.plan_collections",
        )
    if tuple(page.base_page for page in pages) != base_plan.pages:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.plan_pages",
        )
    overlay_records = tuple(record for page in pages for record in page.overlays)
    if len(overlay_records) > MAX_OVERLAYS:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.plan_overlays",
        )
    overlay_ids = tuple(record.overlay_id for record in overlay_records)
    asset_ids = tuple(asset.asset_id for asset in assets)
    base_identity_ids = (
        base_plan.case_id,
        *(source.source_id for source in base_plan.sources),
        *(page.page_id for page in base_plan.pages),
    )
    all_identity_ids = (*base_identity_ids, *overlay_ids, *asset_ids)
    if len(set(all_identity_ids)) != len(all_identity_ids):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.plan_identities",
        )
    required_asset_ids = tuple(
        dict.fromkeys(
            record.payload.asset_id
            for record in overlay_records
            if type(record.payload) is SignatureImageOverlayPayload
        )
    )
    if asset_ids != required_asset_ids:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.plan_assets",
        )
    semantic_fingerprint = _calculate_semantic_fingerprint(
        base_plan=base_plan,
        pages=pages,
        assets=assets,
        plan_version=plan_version,
    )
    object.__setattr__(target, "base_plan", base_plan)
    object.__setattr__(target, "pages", pages)
    object.__setattr__(target, "assets", assets)
    object.__setattr__(target, "semantic_fingerprint", semantic_fingerprint)
    object.__setattr__(target, "plan_version", plan_version)


def _check_claim(code: FlattenedPdfExportCheckCode, checked_count: int) -> str:
    if code is FlattenedPdfExportCheckCode.VISIBLE_OVERLAYS_FLATTENED:
        return (
            f"The exported PDF flattened {checked_count} visible visual overlays into page content."
        )
    if code is FlattenedPdfExportCheckCode.OVERLAY_PRESENCE_MATCHED:
        return (
            "The verified rendering pipeline matched planned placement metadata for "
            f"{checked_count} visible visual overlays, and the reopened output retained "
            "the expected non-interactive Forms."
        )
    if code is FlattenedPdfExportCheckCode.TEXT_FONT_POLICY_MATCHED:
        return (
            "The approved embedded-font or converted-outline strategy was checked for "
            f"{checked_count} text visual overlays."
        )
    return (
        f"The export flattened {checked_count} placed signature images as visual marks; "
        "placing an image did not create a digital signature."
    )


def _validate_report(report: FlattenedPdfExportVerificationReport) -> None:
    if (
        type(report.plan) is not FlattenedPdfExportPlan
        or type(report.base_report) is not PdfExportVerificationReport
        or type(report.checks) is not tuple
        or any(type(check) is not FlattenedPdfExportCheck for check in report.checks)
        or type(report.report_version) is not int
        or report.report_version != FLATTENED_PDF_EXPORT_REPORT_VERSION
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report",
        )
    if (
        report.base_report.semantic_fingerprint != report.plan.semantic_fingerprint
        or report.base_report.page_count != report.plan.page_count
        or report.base_report.unsigned_derivative != report.plan.contains_signature_markers
        or report.checks != _successful_checks(report.plan)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_binding",
        )


def _verified_report(
    plan: FlattenedPdfExportPlan,
    output_sha256: str,
    output_byte_size: int,
) -> FlattenedPdfExportVerificationReport:
    if type(plan) is not FlattenedPdfExportPlan:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_plan",
        )
    counted_codes = {
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    }
    codes: tuple[PdfExportCheckCode, ...] = (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    )
    if plan.contains_signature_markers:
        codes += (PdfExportCheckCode.UNSIGNED_DERIVATIVE,)
    try:
        base_report = PdfExportVerificationReport(
            semantic_fingerprint=plan.semantic_fingerprint,
            output_sha256=output_sha256,
            output_byte_size=output_byte_size,
            page_count=plan.page_count,
            checks=tuple(
                PdfExportCheck(
                    code,
                    plan.page_count if code in counted_codes else None,
                )
                for code in codes
            ),
            unsigned_derivative=plan.contains_signature_markers,
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_output",
        )
    return FlattenedPdfExportVerificationReport(
        plan=plan,
        base_report=base_report,
        checks=_successful_checks(plan),
    )


def _required_visible_signature_assets(
    snapshot: ManifestSnapshot,
) -> tuple[OverlayAssetRecord, ...]:
    _, projection, collection = _read_snapshot(snapshot)
    return _required_assets(projection, collection)


def _build_plan(
    snapshot: ManifestSnapshot,
    raster_assets: Mapping[UUID, OverlayRasterAsset],
    profile: PdfExportProfile,
) -> FlattenedPdfExportPlan:
    if type(profile) is not PdfExportProfile:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.profile",
        )
    document, projection, collection = _read_snapshot(snapshot)
    if not projection.page_sequence:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.PLAN_EMPTY,
            "flattened.plan_pages",
        )
    metadata = _copy_raster_metadata(raster_assets)
    required_assets = _required_assets(projection, collection)
    required_ids = tuple(asset.asset_id for asset in required_assets)
    missing = set(required_ids) - set(metadata)
    if missing:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
            "flattened.raster_metadata",
        )
    if set(metadata) - set(required_ids):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA,
            "flattened.raster_metadata",
        )
    bindings = tuple(
        FlattenedPdfExportAsset(asset, metadata[asset.asset_id]) for asset in required_assets
    )
    manifest_sources = _parse_manifest_sources(document.get("sources"))
    source_by_id = {source.source_id: source for source in manifest_sources}
    state_by_id = {state.page_id: state for state in projection.pages}
    export_sources: list[PdfExportSource] = []
    export_source_by_id: dict[UUID, PdfExportSource] = {}
    base_pages: list[PdfExportPage] = []
    pages: list[FlattenedPdfExportPage] = []
    try:
        for output_page_index, page_id in enumerate(projection.page_sequence):
            state = state_by_id.get(page_id)
            if state is None or not state.active:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
                    "flattened.page_state",
                )
            if (
                state.deskew_angle_millidegrees_clockwise != 0
                or state.contrast_factor_millionths != NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
                or state.background_cleanup_strength_millionths != 0
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED,
                    "flattened.scan_transform",
                )
            source = source_by_id.get(state.reference.source_id)
            if source is None or state.reference.source_page_index >= source.page_count:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
                    "flattened.page_source",
                )
            selected_source = export_source_by_id.get(source.source_id)
            if selected_source is None:
                if len(export_sources) >= MAX_PDF_EXPORT_SOURCES:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.sources",
                    )
                selected_source = _select_export_source(source)
                export_source_by_id[source.source_id] = selected_source
                export_sources.append(selected_source)
            base_page = PdfExportPage(
                output_page_index=output_page_index,
                page_id=state.page_id,
                source_id=source.source_id,
                source_page_index=state.reference.source_page_index,
                additional_rotation=PdfExportRotation(state.quarter_turns_clockwise * 90),
            )
            active_crop = state.crop or NormalizedPageBox.full_page()
            visible = collection.visible_stack(page_id, active_crop=active_crop)
            base_pages.append(base_page)
            pages.append(
                FlattenedPdfExportPage(
                    base_page=base_page,
                    active_crop=active_crop,
                    overlays=tuple(item.record for item in visible),
                )
            )
        base_plan = PdfExportPlan(
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            manifest_sha256=snapshot.sha256,
            manifest_cas_token=snapshot.cas_token,
            sources=tuple(export_sources),
            pages=tuple(base_pages),
            profile=profile,
        )
    except PdfExportError as error:
        _translate_pdf_export_error(error)
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.plan",
        )
    return FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=tuple(pages),
        assets=bindings,
    )


@dataclass(frozen=True, slots=True)
class _ManifestSource:
    source_id: UUID
    media_type: str
    byte_size: int
    page_count: int
    sha256: str
    signature_state: str
    encryption_state: str


def _read_snapshot(
    snapshot: ManifestSnapshot,
) -> tuple[dict[str, object], OrganizerProjection, OverlayCollection]:
    if type(snapshot) is not ManifestSnapshot:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.snapshot",
        )
    try:
        document = snapshot.document
        projection = PageCommandHistory.from_snapshot(snapshot).projection
        collection = OverlayCollection.from_manifest_document(document)
    except PageOperationError as error:
        code = (
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
            if error.code is PageOperationErrorCode.CAPACITY_EXCEEDED
            else FlattenedPdfExportErrorCode.MANIFEST_INVALID
        )
        reject_flattened_pdf_export(code, "flattened.manifest")
    except OverlayError as error:
        code = (
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
            if error.code is OverlayErrorCode.CAPACITY_EXCEEDED
            else FlattenedPdfExportErrorCode.MANIFEST_INVALID
        )
        reject_flattened_pdf_export(code, "flattened.manifest")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.manifest",
        )
    return document, projection, collection


def _required_assets(
    projection: OrganizerProjection,
    collection: OverlayCollection,
) -> tuple[OverlayAssetRecord, ...]:
    state_by_id = {state.page_id: state for state in projection.pages}
    asset_by_id = {asset.asset_id: asset for asset in collection.assets}
    selected: list[OverlayAssetRecord] = []
    seen: set[UUID] = set()
    for page_id in projection.page_sequence:
        state = state_by_id.get(page_id)
        if state is None or not state.active:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                "flattened.page_state",
            )
        active_crop = state.crop or NormalizedPageBox.full_page()
        for projection_item in collection.visible_stack(page_id, active_crop=active_crop):
            payload = projection_item.record.payload
            if type(payload) is SignatureImageOverlayPayload and payload.asset_id not in seen:
                asset = asset_by_id.get(payload.asset_id)
                if asset is None:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                        "flattened.signature_asset",
                    )
                seen.add(payload.asset_id)
                selected.append(asset)
    return tuple(selected)


def _copy_raster_metadata(
    value: Mapping[UUID, OverlayRasterAsset],
) -> dict[UUID, OverlayRasterAsset]:
    if not isinstance(value, Mapping):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
            "flattened.raster_metadata",
        )
    try:
        items = tuple(value.items())
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
            "flattened.raster_metadata",
        )
    if len(items) > MAX_OVERLAYS:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.raster_metadata",
        )
    copied: dict[UUID, OverlayRasterAsset] = {}
    for key, metadata in items:
        if (
            type(key) is not UUID
            or key.version != 4
            or type(metadata) is not OverlayRasterAsset
            or metadata.asset_id != key
            or key in copied
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.raster_metadata",
            )
        copied[key] = metadata
    return copied


def _parse_manifest_sources(value: object) -> tuple[_ManifestSource, ...]:
    if type(value) is not list:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.sources",
        )
    if len(value) > MAX_PAGE_COUNT:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.sources",
        )
    parsed: list[_ManifestSource] = []
    identifiers: set[UUID] = set()
    for member in value:
        if type(member) is not dict or frozenset(member) != _SOURCE_MEMBERS:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                "flattened.source",
            )
        record = cast(Mapping[str, object], member)
        source_id = _parse_uuid(record["source_id"])
        media_type = _known_string(record["detected_media_type"], _KNOWN_MEDIA_TYPES)
        signature_state = _known_string(
            record["signature_state"],
            _KNOWN_SIGNATURE_STATES,
        )
        encryption_state = _known_string(
            record["encryption_state"],
            _KNOWN_ENCRYPTION_STATES,
        )
        byte_size = _positive_integer(record["byte_size"])
        page_count = _positive_integer(record["page_count"])
        sha256 = record["sha256"]
        if (
            source_id in identifiers
            or type(sha256) is not str
            or _SHA256.fullmatch(sha256) is None
            or record["import_state"] != "complete"
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                "flattened.source",
            )
        identifiers.add(source_id)
        parsed.append(
            _ManifestSource(
                source_id,
                media_type,
                byte_size,
                page_count,
                sha256,
                signature_state,
                encryption_state,
            )
        )
    return tuple(parsed)


def _select_export_source(source: _ManifestSource) -> PdfExportSource:
    if source.media_type != "application/pdf":
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_media",
        )
    if source.page_count > MAX_PDF_EXPORT_PAGES:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_pages",
        )
    if source.encryption_state == "encrypted":
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
            "flattened.source_encryption",
        )
    if source.encryption_state in {"unknown", "not_checked"}:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN,
            "flattened.source_encryption",
        )
    if source.encryption_state != "unencrypted":
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            "flattened.source_encryption",
        )
    if source.signature_state in {"unknown", "not_checked"}:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
            "flattened.source_signature",
        )
    try:
        selected_signature = PdfExportSourceSignatureState(source.signature_state)
    except ValueError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            "flattened.source_signature",
        )
    return PdfExportSource(
        source_id=source.source_id,
        byte_size=source.byte_size,
        page_count=source.page_count,
        signature_state=selected_signature,
        sha256=source.sha256,
    )


def _successful_checks(
    plan: FlattenedPdfExportPlan,
) -> tuple[FlattenedPdfExportCheck, ...]:
    checks: tuple[FlattenedPdfExportCheck, ...] = (
        FlattenedPdfExportCheck(
            FlattenedPdfExportCheckCode.VISIBLE_OVERLAYS_FLATTENED,
            plan.visible_overlay_count,
        ),
        FlattenedPdfExportCheck(
            FlattenedPdfExportCheckCode.OVERLAY_PRESENCE_MATCHED,
            plan.visible_overlay_count,
        ),
        FlattenedPdfExportCheck(
            FlattenedPdfExportCheckCode.TEXT_FONT_POLICY_MATCHED,
            plan.text_overlay_count,
        ),
    )
    if plan.placed_signature_image_count:
        checks += (
            FlattenedPdfExportCheck(
                FlattenedPdfExportCheckCode.SIGNATURE_IMAGE_TERMINOLOGY_PRESERVED,
                plan.placed_signature_image_count,
            ),
        )
    return checks


def _calculate_semantic_fingerprint(
    *,
    base_plan: PdfExportPlan,
    pages: tuple[FlattenedPdfExportPage, ...],
    assets: tuple[FlattenedPdfExportAsset, ...],
    plan_version: int,
) -> str:
    asset_position = {asset.asset_id: index for index, asset in enumerate(assets)}
    document = {
        "fingerprint_version": FLATTENED_PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION,
        "plan_version": plan_version,
        "base_pdf_semantic_fingerprint": base_plan.semantic_fingerprint,
        "pages": [
            {
                "active_crop": page.active_crop.to_manifest(),
                "overlays": [
                    {
                        "overlay_version": record.overlay_version,
                        "z_index": record.z_index,
                        "box": record.box.to_manifest(),
                        "payload": _semantic_payload(record, asset_position),
                    }
                    for record in page.overlays
                ],
            }
            for page in pages
        ],
        "signature_assets": [
            {
                "kind": asset.encoded_asset.kind.value,
                "media_type": asset.encoded_asset.media_type.value,
                "encoded_byte_size": asset.encoded_asset.byte_size,
                "encoded_sha256": asset.encoded_asset.sha256,
                "raster_version": asset.raster_asset.asset_version,
                "width_pixels": asset.raster_asset.width_pixels,
                "height_pixels": asset.raster_asset.height_pixels,
                "row_stride_bytes": asset.raster_asset.row_stride_bytes,
                "raster_byte_count": asset.raster_asset.byte_count,
                "pixel_sha256": asset.raster_asset.pixel_sha256,
            }
            for asset in assets
        ],
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_payload(
    record: OverlayRecord,
    asset_position: Mapping[UUID, int],
) -> dict[str, object]:
    payload = record.payload
    if type(payload) is SignatureImageOverlayPayload:
        return {
            "kind": payload.kind.value,
            "asset_index": asset_position[payload.asset_id],
            "terminology": payload.terminology,
        }
    return payload.to_manifest()


def _translate_pdf_export_error(error: PdfExportError) -> Never:
    mapping = {
        PdfExportErrorCode.PLAN_EMPTY: FlattenedPdfExportErrorCode.PLAN_EMPTY,
        PdfExportErrorCode.SOURCE_UNSUPPORTED: FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.SOURCE_ENCRYPTED: FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
        PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_INCONSISTENT: (FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT),
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED: (
            FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED
        ),
        PdfExportErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
    }
    reject_flattened_pdf_export(
        mapping.get(error.code, FlattenedPdfExportErrorCode.MODEL_INVALID),
        "flattened.base_plan",
    )


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.source_id",
        )
    try:
        selected = UUID(value)
    except ValueError, AttributeError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.source_id",
        )
    if selected.version != 4 or str(selected) != value:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.source_id",
        )
    return selected


def _positive_integer(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.source_integer",
        )
    return value


def _known_string(value: object, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MANIFEST_INVALID,
            "flattened.source_state",
        )
    return value
