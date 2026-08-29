"""Pure contracts for verified searchable mixed-document PDF output.

The models in this module contain no paths, filenames, document text, native
objects, or worker handles.  They extend the accepted PDF-export contract with
versioned mixed-source intent and exact per-page text-presence outcomes without
changing the released case-manifest v2 meaning.
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
    CURRENT_MANIFEST_SCHEMA_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    TRUSTED_OCR_ENGINE_PROFILE_SHA256,
)
from paperworkshell.domain.ocr_languages import DEFAULT_OCR_LANGUAGE_SET_ID
from paperworkshell.domain.page_operations import (
    MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
    MAX_CONTRAST_FACTOR_MILLIONTHS,
    MAX_DESKEW_ANGLE_MILLIDEGREES,
    MAX_PAGE_COUNT,
    NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
    NormalizedPageBox,
    OrganizerProjection,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
)
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportProfile,
    PdfExportVerificationReport,
)

SEARCHABLE_PDF_SOURCE_VERSION: Final = 1
SEARCHABLE_PDF_PAGE_VERSION: Final = 1
SEARCHABLE_PDF_PROFILE_VERSION: Final = 1
SEARCHABLE_PDF_PLAN_VERSION: Final = 1
SEARCHABLE_PDF_ELIGIBILITY_VERSION: Final = 1
SEARCHABLE_PDF_PAGE_OUTCOME_VERSION: Final = 1
SEARCHABLE_PDF_REPORT_VERSION: Final = 1
SEARCHABLE_PDF_OUTCOME_VERSION: Final = 1
SEARCHABLE_PDF_FINGERPRINT_VERSION: Final = 1
OCR_ELIGIBILITY_POLICY_VERSION: Final = 1
TEXT_PRESENCE_POLICY_VERSION: Final = 1
DEFAULT_OCR_ELIGIBILITY_POLICY_ID: Final = "mixed-page-ocr-eligibility-v1"
DEFAULT_TEXT_PRESENCE_PROFILE_ID: Final = "pdfium-non-whitespace-text-v1"
MAX_SEARCHABLE_PDF_SOURCES: Final = 255
MAX_SEARCHABLE_PDF_PAGES: Final = MAX_PAGE_COUNT

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_OPAQUE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_CAS_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
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
_OVERLAY_MEMBERS: Final = frozenset(
    {"overlay_id", "overlay_version", "page_id", "box", "z_index", "payload"}
)


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_opaque_token(value: object) -> bool:
    return type(value) is str and len(value) <= 128 and _OPAQUE_TOKEN.fullmatch(value) is not None


class SearchablePdfExportErrorCode(StrEnum):
    """Closed registry of sanitized searchable-export failures."""

    MODEL_INVALID = "SEARCHABLE_PDF_MODEL_INVALID"
    MANIFEST_INVALID = "SEARCHABLE_PDF_MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "SEARCHABLE_PDF_VERSION_UNSUPPORTED"
    PLAN_EMPTY = "SEARCHABLE_PDF_PLAN_EMPTY"
    SOURCE_UNSUPPORTED = "SEARCHABLE_PDF_SOURCE_UNSUPPORTED"
    SOURCE_ENCRYPTED = "SEARCHABLE_PDF_SOURCE_ENCRYPTED"
    SOURCE_ENCRYPTION_UNKNOWN = "SEARCHABLE_PDF_SOURCE_ENCRYPTION_UNKNOWN"
    SOURCE_INCONSISTENT = "SEARCHABLE_PDF_SOURCE_INCONSISTENT"
    TRANSFORM_UNSUPPORTED = "SEARCHABLE_PDF_TRANSFORM_UNSUPPORTED"
    OCR_LANGUAGE_UNAVAILABLE = "SEARCHABLE_PDF_OCR_LANGUAGE_UNAVAILABLE"
    LIMIT_EXCEEDED = "SEARCHABLE_PDF_LIMIT_EXCEEDED"
    REPORT_INVALID = "SEARCHABLE_PDF_REPORT_INVALID"
    RESULT_INVALID = "SEARCHABLE_PDF_RESULT_INVALID"
    INTERNAL_ERROR = "SEARCHABLE_PDF_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[SearchablePdfExportErrorCode, str]] = {
    SearchablePdfExportErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid searchable-PDF data."
    ),
    SearchablePdfExportErrorCode.MANIFEST_INVALID: (
        "The case record cannot be used to create a searchable-PDF plan."
    ),
    SearchablePdfExportErrorCode.VERSION_UNSUPPORTED: (
        "This case uses an unsupported searchable-PDF contract version."
    ),
    SearchablePdfExportErrorCode.PLAN_EMPTY: (
        "Select at least one page before creating a searchable PDF."
    ),
    SearchablePdfExportErrorCode.SOURCE_UNSUPPORTED: (
        "The selected pages include an unsupported source type."
    ),
    SearchablePdfExportErrorCode.SOURCE_ENCRYPTED: (
        "The selected pages include an encrypted PDF that cannot be processed."
    ),
    SearchablePdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
        "PaperWorkShell could not confirm that every selected PDF is unencrypted."
    ),
    SearchablePdfExportErrorCode.SOURCE_INCONSISTENT: (
        "The case source and page records do not agree."
    ),
    SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED: (
        "The selected pages include unsupported output intent."
    ),
    SearchablePdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE: (
        "The selected OCR languages are not installed and verified. "
        "Review OCR language settings before creating the searchable PDF."
    ),
    SearchablePdfExportErrorCode.LIMIT_EXCEEDED: (
        "The searchable-PDF request exceeds a configured safety limit."
    ),
    SearchablePdfExportErrorCode.REPORT_INVALID: (
        "PaperWorkShell received an invalid searchable-PDF verification report."
    ),
    SearchablePdfExportErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid searchable-PDF result."
    ),
    SearchablePdfExportErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not create the searchable PDF safely."
    ),
}


@dataclass(slots=True, repr=False)
class SearchablePdfExportError(ValueError):
    """Stable failure containing no path, filename, text, digest, or raw cause."""

    code: SearchablePdfExportErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not SearchablePdfExportErrorCode:
            self.code = SearchablePdfExportErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "searchable_pdf.export"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"SearchablePdfExportError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_searchable_pdf_export(
    code: SearchablePdfExportErrorCode,
    context: str,
) -> Never:
    """Raise one closed failure without retaining an active source exception."""

    error = SearchablePdfExportError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class SearchablePdfSourceMediaType(StrEnum):
    """Closed source types accepted by searchable mixed output."""

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"


class SearchablePdfSourceSignatureState(StrEnum):
    """Conservative signature-marker state authenticated from the manifest."""

    NOT_APPLICABLE = "not_applicable"
    NONE_DETECTED = "none_detected"
    MARKERS_DETECTED = "markers_detected"
    UNKNOWN = "unknown"


class SearchablePdfSourceEncryptionState(StrEnum):
    """Encryption state admitted into an executable mixed-output plan."""

    NOT_APPLICABLE = "not_applicable"
    UNENCRYPTED = "unencrypted"


class SearchablePdfPageEligibilityCode(StrEnum):
    """One worker-derived reason for preserving, OCRing, or blocking a page."""

    PRESERVE_EXTRACTABLE_TEXT = "preserve_extractable_text"
    OCR_REQUIRED_IMAGE_SOURCE = "ocr_required_image_source"
    OCR_REQUIRED_IMAGE_ONLY_PDF = "ocr_required_image_only_pdf"
    OCR_REQUIRED_PIXEL_TRANSFORM = "ocr_required_pixel_transform"
    OCR_BLOCKED_SIGNATURE_STATE = "ocr_blocked_signature_state"


class SearchablePdfPageOutcomeCode(StrEnum):
    """One exact terminal page action in a verified mixed derivative."""

    EXISTING_TEXT_PRESERVED = "existing_text_preserved"
    OCR_TEXT_VERIFIED = "ocr_text_verified"
    OCR_NO_TEXT_RETAINED = "ocr_no_text_retained"
    OCR_FAILED_RETAINED = "ocr_failed_retained"
    OCR_BLOCKED_SIGNED_SOURCE_RETAINED = "ocr_blocked_signed_source_retained"


class SearchablePdfPageSearchability(StrEnum):
    """Limited text-presence fact; never a correctness or PDF/UA claim."""

    EXTRACTABLE_TEXT_PRESENT_EXISTING = "extractable_text_present_existing"
    EXTRACTABLE_TEXT_PRESENT_OCR = "extractable_text_present_ocr"
    NOT_VERIFIED = "not_verified"


class SearchablePdfPageFailureCode(StrEnum):
    """Closed page-local review reasons that may accompany a verified output."""

    NO_EXTRACTABLE_TEXT = "no_extractable_text"
    OCR_ENGINE_FAILED = "ocr_engine_failed"
    SIGNATURE_STATE_BLOCKED = "signature_state_blocked"


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfExportProfile:
    """Closed execution, eligibility, OCR, and text-presence semantics."""

    ocr_language_set_id: str = DEFAULT_OCR_LANGUAGE_SET_ID
    ocr_profile_id: str = DEFAULT_OCR_PROFILE_ID
    ocr_engine_profile_sha256: str = field(
        default=TRUSTED_OCR_ENGINE_PROFILE_SHA256,
        repr=False,
    )
    eligibility_policy_id: str = DEFAULT_OCR_ELIGIBILITY_POLICY_ID
    text_presence_profile_id: str = DEFAULT_TEXT_PRESENCE_PROFILE_ID
    pdf_profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE
    eligibility_policy_version: int = OCR_ELIGIBILITY_POLICY_VERSION
    text_presence_policy_version: int = TEXT_PRESENCE_POLICY_VERSION
    profile_version: int = SEARCHABLE_PDF_PROFILE_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_opaque_token(self.ocr_language_set_id)
            or not _is_opaque_token(self.ocr_profile_id)
            or not _is_sha256(self.ocr_engine_profile_sha256)
            or not _is_opaque_token(self.eligibility_policy_id)
            or not _is_opaque_token(self.text_presence_profile_id)
            or type(self.pdf_profile) is not PdfExportProfile
            or type(self.eligibility_policy_version) is not int
            or self.eligibility_policy_version != OCR_ELIGIBILITY_POLICY_VERSION
            or type(self.text_presence_policy_version) is not int
            or self.text_presence_policy_version != TEXT_PRESENCE_POLICY_VERSION
            or type(self.profile_version) is not int
            or self.profile_version != SEARCHABLE_PDF_PROFILE_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.profile",
            )

    def __repr__(self) -> str:
        return (
            "SearchablePdfExportProfile("
            f"profile_version={self.profile_version}, "
            f"eligibility_policy_version={self.eligibility_policy_version}, "
            f"text_presence_policy_version={self.text_presence_policy_version})"
        )


DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE: Final = SearchablePdfExportProfile()


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfSource:
    """Path-free immutable facts for one mixed-output source."""

    source_id: UUID
    media_type: SearchablePdfSourceMediaType
    byte_size: int
    page_count: int
    sha256: str = field(repr=False)
    signature_state: SearchablePdfSourceSignatureState
    encryption_state: SearchablePdfSourceEncryptionState
    source_version: int = SEARCHABLE_PDF_SOURCE_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.source_id)
            or type(self.media_type) is not SearchablePdfSourceMediaType
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_SEARCHABLE_PDF_PAGES
            or not _is_sha256(self.sha256)
            or type(self.signature_state) is not SearchablePdfSourceSignatureState
            or type(self.encryption_state) is not SearchablePdfSourceEncryptionState
            or type(self.source_version) is not int
            or self.source_version != SEARCHABLE_PDF_SOURCE_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.source",
            )
        if self.media_type is SearchablePdfSourceMediaType.PDF:
            if (
                self.signature_state is SearchablePdfSourceSignatureState.NOT_APPLICABLE
                or self.encryption_state is not SearchablePdfSourceEncryptionState.UNENCRYPTED
            ):
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.MODEL_INVALID,
                    "searchable.source_pdf",
                )
        elif (
            self.page_count != 1
            or self.signature_state is not SearchablePdfSourceSignatureState.NOT_APPLICABLE
            or self.encryption_state is not SearchablePdfSourceEncryptionState.NOT_APPLICABLE
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.source_image",
            )

    @property
    def contains_signature_markers(self) -> bool:
        return self.signature_state is SearchablePdfSourceSignatureState.MARKERS_DETECTED

    def __repr__(self) -> str:
        return (
            "SearchablePdfSource("
            f"source_id={self.source_id!r}, media_type={self.media_type!r}, "
            f"byte_size={self.byte_size}, page_count={self.page_count}, "
            f"signature_state={self.signature_state!r}, "
            f"encryption_state={self.encryption_state!r}, "
            f"source_version={self.source_version})"
        )


@dataclass(frozen=True, slots=True)
class SearchablePdfPage:
    """One exact projected page and all v2 scan-transform intent."""

    output_page_index: int
    page_id: UUID
    source_id: UUID
    source_page_index: int
    quarter_turns_clockwise: int
    crop: NormalizedPageBox | None
    deskew_angle_millidegrees_clockwise: int
    contrast_factor_millionths: int
    background_cleanup_strength_millionths: int
    page_version: int = SEARCHABLE_PDF_PAGE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.output_page_index) is not int
            or not 0 <= self.output_page_index < MAX_SEARCHABLE_PDF_PAGES
            or not _is_uuid4(self.page_id)
            or not _is_uuid4(self.source_id)
            or self.page_id == self.source_id
            or type(self.source_page_index) is not int
            or not 0 <= self.source_page_index < MAX_SEARCHABLE_PDF_PAGES
            or type(self.quarter_turns_clockwise) is not int
            or not 0 <= self.quarter_turns_clockwise <= 3
            or (self.crop is not None and type(self.crop) is not NormalizedPageBox)
            or type(self.deskew_angle_millidegrees_clockwise) is not int
            or not -MAX_DESKEW_ANGLE_MILLIDEGREES
            <= self.deskew_angle_millidegrees_clockwise
            <= MAX_DESKEW_ANGLE_MILLIDEGREES
            or type(self.contrast_factor_millionths) is not int
            or not 0 <= self.contrast_factor_millionths <= MAX_CONTRAST_FACTOR_MILLIONTHS
            or type(self.background_cleanup_strength_millionths) is not int
            or not 0
            <= self.background_cleanup_strength_millionths
            <= MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
            or type(self.page_version) is not int
            or self.page_version != SEARCHABLE_PDF_PAGE_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.page",
            )

    @property
    def has_pixel_transform_intent(self) -> bool:
        """Return whether output requires more than lossless quarter-turn handling."""

        return (
            self.crop is not None
            or self.deskew_angle_millidegrees_clockwise != 0
            or self.contrast_factor_millionths != NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
            or self.background_cleanup_strength_millionths != 0
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class SearchablePdfExportPlan:
    """Immutable mixed-output intent bound to one current manifest snapshot."""

    case_id: UUID
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    manifest_cas_token: str = field(repr=False)
    sources: tuple[SearchablePdfSource, ...]
    pages: tuple[SearchablePdfPage, ...]
    profile: SearchablePdfExportProfile
    semantic_fingerprint: str = field(repr=False)
    plan_version: int

    def __init__(
        self,
        *,
        case_id: UUID,
        manifest_revision: int,
        manifest_sha256: str,
        manifest_cas_token: str,
        sources: tuple[SearchablePdfSource, ...],
        pages: tuple[SearchablePdfPage, ...],
        profile: SearchablePdfExportProfile = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
        plan_version: int = SEARCHABLE_PDF_PLAN_VERSION,
    ) -> None:
        if (
            not _is_uuid4(case_id)
            or type(manifest_revision) is not int
            or not 0 <= manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or not _is_sha256(manifest_sha256)
            or type(manifest_cas_token) is not str
            or _CAS_TOKEN.fullmatch(manifest_cas_token) is None
            or type(sources) is not tuple
            or type(pages) is not tuple
            or type(profile) is not SearchablePdfExportProfile
            or type(plan_version) is not int
            or plan_version != SEARCHABLE_PDF_PLAN_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.plan",
            )
        if not pages:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.PLAN_EMPTY,
                "searchable.plan_pages",
            )
        if (
            not 1 <= len(sources) <= MAX_SEARCHABLE_PDF_SOURCES
            or len(pages) > MAX_SEARCHABLE_PDF_PAGES
            or any(type(source) is not SearchablePdfSource for source in sources)
            or any(type(page) is not SearchablePdfPage for page in pages)
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
                "searchable.plan_collections",
            )
        source_ids = tuple(source.source_id for source in sources)
        page_ids = tuple(page.page_id for page in pages)
        source_positions = tuple((page.source_id, page.source_page_index) for page in pages)
        if (
            len(set(source_ids)) != len(source_ids)
            or len(set(page_ids)) != len(page_ids)
            or len(set(source_positions)) != len(source_positions)
            or tuple(page.output_page_index for page in pages) != tuple(range(len(pages)))
            or bool(set(source_ids) & set(page_ids))
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.plan_identities",
            )
        source_by_id = {source.source_id: source for source in sources}
        if source_ids != tuple(dict.fromkeys(page.source_id for page in pages)):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.plan_source_order",
            )
        for page in pages:
            source = source_by_id.get(page.source_id)
            if source is None or page.source_page_index >= source.page_count:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.MODEL_INVALID,
                    "searchable.plan_page_source",
                )
            if (
                source.signature_state
                in {
                    SearchablePdfSourceSignatureState.MARKERS_DETECTED,
                    SearchablePdfSourceSignatureState.UNKNOWN,
                }
                and page.has_pixel_transform_intent
            ):
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED,
                    "searchable.signed_pixel_transform",
                )
        semantic_fingerprint = _calculate_semantic_fingerprint(
            sources=sources,
            pages=pages,
            profile=profile,
            plan_version=plan_version,
        )
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "manifest_revision", manifest_revision)
        object.__setattr__(self, "manifest_sha256", manifest_sha256)
        object.__setattr__(self, "manifest_cas_token", manifest_cas_token)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "semantic_fingerprint", semantic_fingerprint)
        object.__setattr__(self, "plan_version", plan_version)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def contains_signature_markers(self) -> bool:
        return any(source.contains_signature_markers for source in self.sources)

    @property
    def contains_unknown_signature_state(self) -> bool:
        return any(
            source.signature_state is SearchablePdfSourceSignatureState.UNKNOWN
            for source in self.sources
        )

    @property
    def contains_signature_uncertainty(self) -> bool:
        """Return whether any selected PDF has an unknown signature state."""

        return self.contains_unknown_signature_state

    @property
    def requires_unsigned_derivative(self) -> bool:
        """Conservatively label marker-bearing and uninspected PDFs unsigned."""

        return self.contains_signature_markers or self.contains_signature_uncertainty

    def __repr__(self) -> str:
        return (
            "SearchablePdfExportPlan("
            f"case_id={self.case_id!r}, manifest_revision={self.manifest_revision}, "
            f"source_count={len(self.sources)}, page_count={self.page_count}, "
            f"plan_version={self.plan_version})"
        )


@dataclass(frozen=True, slots=True)
class SearchablePdfPageEligibility:
    """Worker-derived eligibility decision bound to one planned output page."""

    output_page_index: int
    page_id: UUID
    code: SearchablePdfPageEligibilityCode
    eligibility_version: int = SEARCHABLE_PDF_ELIGIBILITY_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.output_page_index) is not int
            or not 0 <= self.output_page_index < MAX_SEARCHABLE_PDF_PAGES
            or not _is_uuid4(self.page_id)
            or type(self.code) is not SearchablePdfPageEligibilityCode
            or type(self.eligibility_version) is not int
            or self.eligibility_version != SEARCHABLE_PDF_ELIGIBILITY_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.eligibility",
            )


@dataclass(frozen=True, slots=True)
class SearchablePdfPageOutcome:
    """One content-free terminal page outcome and limited text-presence fact."""

    eligibility: SearchablePdfPageEligibility
    outcome: SearchablePdfPageOutcomeCode
    searchability: SearchablePdfPageSearchability
    failure_code: SearchablePdfPageFailureCode | None = None
    outcome_version: int = SEARCHABLE_PDF_PAGE_OUTCOME_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.eligibility) is not SearchablePdfPageEligibility
            or type(self.outcome) is not SearchablePdfPageOutcomeCode
            or type(self.searchability) is not SearchablePdfPageSearchability
            or (
                self.failure_code is not None
                and type(self.failure_code) is not SearchablePdfPageFailureCode
            )
            or type(self.outcome_version) is not int
            or self.outcome_version != SEARCHABLE_PDF_PAGE_OUTCOME_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.page_outcome",
            )
        expected = _OUTCOME_FACTS.get(self.outcome)
        if expected != (self.searchability, self.failure_code):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.page_outcome_facts",
            )
        if not _outcome_matches_eligibility(self.eligibility.code, self.outcome):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.page_outcome_eligibility",
            )

    @property
    def output_page_index(self) -> int:
        return self.eligibility.output_page_index

    @property
    def page_id(self) -> UUID:
        return self.eligibility.page_id

    @property
    def has_extractable_text(self) -> bool:
        return self.searchability is not SearchablePdfPageSearchability.NOT_VERIFIED


_OUTCOME_FACTS: Final[
    dict[
        SearchablePdfPageOutcomeCode,
        tuple[SearchablePdfPageSearchability, SearchablePdfPageFailureCode | None],
    ]
] = {
    SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED: (
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
        None,
    ),
    SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED: (
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        None,
    ),
    SearchablePdfPageOutcomeCode.OCR_NO_TEXT_RETAINED: (
        SearchablePdfPageSearchability.NOT_VERIFIED,
        SearchablePdfPageFailureCode.NO_EXTRACTABLE_TEXT,
    ),
    SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED: (
        SearchablePdfPageSearchability.NOT_VERIFIED,
        SearchablePdfPageFailureCode.OCR_ENGINE_FAILED,
    ),
    SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED: (
        SearchablePdfPageSearchability.NOT_VERIFIED,
        SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED,
    ),
}


@dataclass(frozen=True, slots=True)
class SearchablePdfVerificationReport:
    """Aggregate base-PDF checks plus one exact outcome per output page."""

    plan: SearchablePdfExportPlan = field(repr=False)
    base_report: PdfExportVerificationReport
    page_outcomes: tuple[SearchablePdfPageOutcome, ...]
    report_version: int = SEARCHABLE_PDF_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not SearchablePdfExportPlan
            or type(self.base_report) is not PdfExportVerificationReport
            or type(self.page_outcomes) is not tuple
            or any(type(outcome) is not SearchablePdfPageOutcome for outcome in self.page_outcomes)
            or type(self.report_version) is not int
            or self.report_version != SEARCHABLE_PDF_REPORT_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.report",
            )
        if (
            self.base_report.semantic_fingerprint != self.plan.semantic_fingerprint
            or self.base_report.page_count != self.plan.page_count
            or self.base_report.unsigned_derivative != self.plan.requires_unsigned_derivative
            or len(self.page_outcomes) != self.plan.page_count
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.report_binding",
            )
        source_by_id = {source.source_id: source for source in self.plan.sources}
        for page, outcome in zip(self.plan.pages, self.page_outcomes, strict=True):
            if (
                outcome.output_page_index != page.output_page_index
                or outcome.page_id != page.page_id
            ):
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.REPORT_INVALID,
                    "searchable.report_page_order",
                )
            _validate_page_eligibility(
                page,
                source_by_id[page.source_id],
                outcome.eligibility.code,
            )

    @classmethod
    def for_verified_plan(
        cls,
        plan: SearchablePdfExportPlan,
        *,
        output_sha256: str,
        output_byte_size: int,
        page_outcomes: tuple[SearchablePdfPageOutcome, ...],
    ) -> SearchablePdfVerificationReport:
        """Build the closed base report after all named adapter checks pass."""

        if type(plan) is not SearchablePdfExportPlan:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.report_plan",
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
        if plan.requires_unsigned_derivative:
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
                unsigned_derivative=plan.requires_unsigned_derivative,
            )
        except Exception:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.report_output",
            )
        return cls(
            plan=plan,
            base_report=base_report,
            page_outcomes=page_outcomes,
        )

    @property
    def all_pages_have_extractable_text(self) -> bool:
        return all(outcome.has_extractable_text for outcome in self.page_outcomes)

    @property
    def existing_text_page_count(self) -> int:
        return sum(
            outcome.searchability
            is SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING
            for outcome in self.page_outcomes
        )

    @property
    def ocr_text_page_count(self) -> int:
        return sum(
            outcome.searchability is SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR
            for outcome in self.page_outcomes
        )

    @property
    def needs_review_page_count(self) -> int:
        return self.plan.page_count - self.existing_text_page_count - self.ocr_text_page_count

    @property
    def claims(self) -> tuple[str, ...]:
        """Return concrete limited claims without asserting OCR correctness."""

        text_count = self.existing_text_page_count + self.ocr_text_page_count
        base_claims = self.base_report.claims
        if self.plan.requires_unsigned_derivative:
            base_claims = (
                *base_claims[:-1],
                _unsigned_derivative_claim(self.plan),
            )
        claims = (
            *base_claims,
            (
                "The supported text-presence profile found non-whitespace extractable "
                f"text on {text_count} of {self.plan.page_count} pages."
            ),
        )
        if self.needs_review_page_count:
            page_word = "page" if self.needs_review_page_count == 1 else "pages"
            result_verb = "has" if self.needs_review_page_count == 1 else "have"
            verb = "needs" if self.needs_review_page_count == 1 else "need"
            claims += (
                f"{self.needs_review_page_count} {page_word} {result_verb} no verified "
                "extractable-text "
                f"result and {verb} review.",
            )
        return claims


def _unsigned_derivative_claim(plan: SearchablePdfExportPlan) -> str:
    """Describe only the signature reason authenticated by the searchable plan."""

    if plan.contains_signature_markers and plan.contains_signature_uncertainty:
        return (
            "The exported PDF is an unsigned derivative because at least one source "
            "contained digital-signature markers; PaperWorkShell also could not confirm "
            "the digital-signature-marker state of every selected PDF source."
        )
    if plan.contains_signature_markers:
        return (
            "The exported PDF is an unsigned derivative because at least one source "
            "contained digital-signature markers."
        )
    return (
        "The exported PDF is conservatively labeled an unsigned derivative because "
        "PaperWorkShell could not confirm the digital-signature-marker state of every "
        "selected PDF source."
    )


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfExportOutcome:
    """Committed mixed-output result exactly correlated with its text report."""

    base_outcome: PdfExportOutcome = field(repr=False)
    report: SearchablePdfVerificationReport = field(repr=False)
    outcome_version: int = SEARCHABLE_PDF_OUTCOME_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.base_outcome) is not PdfExportOutcome
            or type(self.report) is not SearchablePdfVerificationReport
            or self.base_outcome.report != self.report.base_report
            or type(self.outcome_version) is not int
            or self.outcome_version != SEARCHABLE_PDF_OUTCOME_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.outcome",
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
    def checks(self) -> tuple[PdfExportCheck, ...]:
        return self.base_outcome.checks

    @property
    def claims(self) -> tuple[str, ...]:
        return self.report.claims + (self.base_outcome.checks[-1].claim,)

    @property
    def safe_message(self) -> str:
        return self.base_outcome.safe_message

    def __repr__(self) -> str:
        return (
            "SearchablePdfExportOutcome("
            f"status={self.status!r}, page_count={self.report.plan.page_count}, "
            f"outcome_version={self.outcome_version})"
        )


def build_searchable_pdf_export_plan(
    snapshot: ManifestSnapshot,
    *,
    profile: SearchablePdfExportProfile = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
) -> SearchablePdfExportPlan:
    """Resolve exact manifest-v2 page order and transform intent for mixed output."""

    document, projection = _read_searchable_pdf_export_snapshot(snapshot, profile)
    return _build_searchable_pdf_export_plan_for_pages(
        snapshot,
        document,
        projection,
        projection.page_sequence,
        profile,
    )


def build_searchable_pdf_export_plan_for_pages(
    snapshot: ManifestSnapshot,
    ordered_page_ids: tuple[UUID, ...],
    *,
    profile: SearchablePdfExportProfile = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
) -> SearchablePdfExportPlan:
    """Resolve one exact nonempty ordered subset of active mixed-source pages."""

    document, projection = _read_searchable_pdf_export_snapshot(snapshot, profile)
    return _build_searchable_pdf_export_plan_for_pages(
        snapshot,
        document,
        projection,
        ordered_page_ids,
        profile,
    )


def _read_searchable_pdf_export_snapshot(
    snapshot: ManifestSnapshot,
    profile: SearchablePdfExportProfile,
) -> tuple[dict[str, object], OrganizerProjection]:
    if type(snapshot) is not ManifestSnapshot or type(profile) is not SearchablePdfExportProfile:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.plan_input",
        )
    if snapshot.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.VERSION_UNSUPPORTED,
            "searchable.manifest_version",
        )
    try:
        projection = PageCommandHistory.from_snapshot(snapshot).projection
        document = snapshot.document
    except PageOperationError as error:
        if error.code is PageOperationErrorCode.CAPACITY_EXCEEDED:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
                "searchable.manifest",
            )
        if error.code is PageOperationErrorCode.VERSION_UNSUPPORTED:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.VERSION_UNSUPPORTED,
                "searchable.manifest",
            )
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MANIFEST_INVALID,
            "searchable.manifest",
        )
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MANIFEST_INVALID,
            "searchable.manifest",
        )
    return document, projection


def _build_searchable_pdf_export_plan_for_pages(
    snapshot: ManifestSnapshot,
    document: dict[str, object],
    projection: OrganizerProjection,
    ordered_page_ids: tuple[UUID, ...],
    profile: SearchablePdfExportProfile,
) -> SearchablePdfExportPlan:
    _validate_ordered_page_ids(ordered_page_ids)
    state_by_id = {state.page_id: state for state in projection.pages}
    active_page_ids = frozenset(projection.page_sequence)
    for page_id in ordered_page_ids:
        state = state_by_id.get(page_id)
        if state is None or not state.active or page_id not in active_page_ids:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
                "searchable.page_state",
            )
    source_catalogue = _parse_manifest_sources(document.get("sources"))
    source_by_id = {source.source_id: source for source in source_catalogue}
    _reject_active_overlays(
        document.get("overlays"),
        known_page_ids=frozenset(state_by_id),
        active_page_ids=frozenset(ordered_page_ids),
    )
    selected_sources: list[SearchablePdfSource] = []
    selected_source_ids: set[UUID] = set()
    pages: list[SearchablePdfPage] = []
    for output_page_index, page_id in enumerate(ordered_page_ids):
        state = state_by_id[page_id]
        source = source_by_id.get(state.reference.source_id)
        if source is None or state.reference.source_page_index >= source.page_count:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
                "searchable.page_source",
            )
        if source.source_id not in selected_source_ids:
            if len(selected_sources) >= MAX_SEARCHABLE_PDF_SOURCES:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
                    "searchable.plan_sources",
                )
            selected_sources.append(source)
            selected_source_ids.add(source.source_id)
        pages.append(
            SearchablePdfPage(
                output_page_index=output_page_index,
                page_id=state.page_id,
                source_id=source.source_id,
                source_page_index=state.reference.source_page_index,
                quarter_turns_clockwise=state.quarter_turns_clockwise,
                crop=state.crop,
                deskew_angle_millidegrees_clockwise=(state.deskew_angle_millidegrees_clockwise),
                contrast_factor_millionths=state.contrast_factor_millionths,
                background_cleanup_strength_millionths=(
                    state.background_cleanup_strength_millionths
                ),
            )
        )
    return SearchablePdfExportPlan(
        case_id=snapshot.case_id,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        sources=tuple(selected_sources),
        pages=tuple(pages),
        profile=profile,
    )


def _validate_ordered_page_ids(ordered_page_ids: tuple[UUID, ...]) -> None:
    if type(ordered_page_ids) is not tuple:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.page_ids",
        )
    if not ordered_page_ids:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.PLAN_EMPTY,
            "searchable.plan_pages",
        )
    if len(ordered_page_ids) > MAX_SEARCHABLE_PDF_PAGES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.page_ids",
        )
    if any(not _is_uuid4(page_id) for page_id in ordered_page_ids):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.page_ids",
        )
    if len(set(ordered_page_ids)) != len(ordered_page_ids):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.page_ids",
        )


def _parse_manifest_sources(value: object) -> tuple[SearchablePdfSource, ...]:
    if type(value) is not list:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MANIFEST_INVALID,
            "searchable.sources",
        )
    if len(value) > MAX_SEARCHABLE_PDF_PAGES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.sources",
        )
    sources: list[SearchablePdfSource] = []
    identifiers: set[UUID] = set()
    for member in value:
        if type(member) is not dict or frozenset(member) != _SOURCE_MEMBERS:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.source_record",
            )
        record = cast(Mapping[str, object], member)
        source_id = _parse_uuid(record["source_id"], "searchable.source_id")
        if source_id in identifiers or record["import_state"] != "complete":
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.source_record",
            )
        media_value = record["detected_media_type"]
        if type(media_value) is not str:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_UNSUPPORTED,
                "searchable.source_media",
            )
        try:
            media_type = SearchablePdfSourceMediaType(media_value)
        except ValueError, TypeError:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_UNSUPPORTED,
                "searchable.source_media",
            )
        byte_size = _parse_positive_integer(record["byte_size"], "searchable.source_size")
        page_count = _parse_positive_integer(record["page_count"], "searchable.source_pages")
        digest = record["sha256"]
        if not _is_sha256(digest):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.source_digest",
            )
        signature, encryption = _parse_source_states(
            media_type,
            record["signature_state"],
            record["encryption_state"],
        )
        source = SearchablePdfSource(
            source_id=source_id,
            media_type=media_type,
            byte_size=byte_size,
            page_count=page_count,
            sha256=cast(str, digest),
            signature_state=signature,
            encryption_state=encryption,
        )
        identifiers.add(source_id)
        sources.append(source)
    return tuple(sources)


def _parse_source_states(
    media_type: SearchablePdfSourceMediaType,
    signature_value: object,
    encryption_value: object,
) -> tuple[SearchablePdfSourceSignatureState, SearchablePdfSourceEncryptionState]:
    if media_type is SearchablePdfSourceMediaType.PDF:
        if encryption_value == "encrypted":
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_ENCRYPTED,
                "searchable.source_encryption",
            )
        if encryption_value in {"unknown", "not_checked"}:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN,
                "searchable.source_encryption",
            )
        if encryption_value != "unencrypted":
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
                "searchable.source_encryption",
            )
        if signature_value == "none_detected":
            signature = SearchablePdfSourceSignatureState.NONE_DETECTED
        elif signature_value == "markers_detected":
            signature = SearchablePdfSourceSignatureState.MARKERS_DETECTED
        elif signature_value in {"unknown", "not_checked"}:
            signature = SearchablePdfSourceSignatureState.UNKNOWN
        else:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
                "searchable.source_signature",
            )
        return signature, SearchablePdfSourceEncryptionState.UNENCRYPTED
    if signature_value != "not_applicable" or encryption_value != "not_applicable":
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
            "searchable.source_image_state",
        )
    return (
        SearchablePdfSourceSignatureState.NOT_APPLICABLE,
        SearchablePdfSourceEncryptionState.NOT_APPLICABLE,
    )


def _reject_active_overlays(
    value: object,
    *,
    known_page_ids: frozenset[UUID],
    active_page_ids: frozenset[UUID],
) -> None:
    if type(value) is not list:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MANIFEST_INVALID,
            "searchable.overlays",
        )
    if len(value) > MAX_SEARCHABLE_PDF_PAGES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.overlays",
        )
    for member in value:
        if type(member) is not dict or frozenset(member) != _OVERLAY_MEMBERS:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.overlay",
            )
        page_id = _parse_uuid(member["page_id"], "searchable.overlay_page")
        if page_id not in known_page_ids:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.overlay_page",
            )
        if page_id in active_page_ids:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED,
                "searchable.overlay",
            )


def _calculate_semantic_fingerprint(
    *,
    sources: tuple[SearchablePdfSource, ...],
    pages: tuple[SearchablePdfPage, ...],
    profile: SearchablePdfExportProfile,
    plan_version: int,
) -> str:
    source_positions = {source.source_id: index for index, source in enumerate(sources)}
    pdf_profile = profile.pdf_profile
    document = {
        "fingerprint_version": SEARCHABLE_PDF_FINGERPRINT_VERSION,
        "plan_version": plan_version,
        "profile": {
            "profile_version": profile.profile_version,
            "eligibility_policy_id": profile.eligibility_policy_id,
            "eligibility_policy_version": profile.eligibility_policy_version,
            "text_presence_profile_id": profile.text_presence_profile_id,
            "text_presence_policy_version": profile.text_presence_policy_version,
            "ocr_language_set_id": profile.ocr_language_set_id,
            "ocr_profile_id": profile.ocr_profile_id,
            "ocr_engine_profile_sha256": profile.ocr_engine_profile_sha256,
            "pdf_profile": {
                "profile_version": pdf_profile.profile_version,
                "assembly": pdf_profile.assembly.value,
                "render": pdf_profile.render.value,
                "qpdf": pdf_profile.qpdf.value,
                "encryption_policy": pdf_profile.encryption_policy.value,
            },
        },
        "sources": [
            {
                "media_type": source.media_type.value,
                "byte_size": source.byte_size,
                "page_count": source.page_count,
                "sha256": source.sha256,
                "signature_state": source.signature_state.value,
                "encryption_state": source.encryption_state.value,
                "source_version": source.source_version,
            }
            for source in sources
        ],
        "pages": [
            {
                "source_position": source_positions[page.source_id],
                "source_page_index": page.source_page_index,
                "quarter_turns_clockwise": page.quarter_turns_clockwise,
                "crop": None if page.crop is None else page.crop.to_manifest(),
                "deskew_angle_millidegrees_clockwise": (page.deskew_angle_millidegrees_clockwise),
                "contrast_factor_millionths": page.contrast_factor_millionths,
                "background_cleanup_strength_millionths": (
                    page.background_cleanup_strength_millionths
                ),
                "page_version": page.page_version,
            }
            for page in pages
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


def _outcome_matches_eligibility(
    eligibility: SearchablePdfPageEligibilityCode,
    outcome: SearchablePdfPageOutcomeCode,
) -> bool:
    if eligibility is SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT:
        return outcome is SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED
    if eligibility is SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE:
        return outcome is SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED
    return outcome in {
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        SearchablePdfPageOutcomeCode.OCR_NO_TEXT_RETAINED,
        SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
    }


def _validate_page_eligibility(
    page: SearchablePdfPage,
    source: SearchablePdfSource,
    eligibility: SearchablePdfPageEligibilityCode,
) -> None:
    is_pdf = source.media_type is SearchablePdfSourceMediaType.PDF
    signature_clear = source.signature_state in {
        SearchablePdfSourceSignatureState.NONE_DETECTED,
        SearchablePdfSourceSignatureState.NOT_APPLICABLE,
    }
    valid = False
    if eligibility is SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT:
        valid = is_pdf and not page.has_pixel_transform_intent
    elif eligibility is SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE:
        valid = not is_pdf
    elif eligibility is SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_ONLY_PDF:
        valid = is_pdf and not page.has_pixel_transform_intent and signature_clear
    elif eligibility is SearchablePdfPageEligibilityCode.OCR_REQUIRED_PIXEL_TRANSFORM:
        valid = is_pdf and page.has_pixel_transform_intent and signature_clear
    elif eligibility is SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE:
        valid = is_pdf and not signature_clear
    if not valid:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.report_eligibility",
        )


def _parse_uuid(value: object, context: str) -> UUID:
    if type(value) is not str:
        reject_searchable_pdf_export(SearchablePdfExportErrorCode.MANIFEST_INVALID, context)
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        reject_searchable_pdf_export(SearchablePdfExportErrorCode.MANIFEST_INVALID, context)
    if parsed.version != 4 or str(parsed) != value:
        reject_searchable_pdf_export(SearchablePdfExportErrorCode.MANIFEST_INVALID, context)
    return parsed


def _parse_positive_integer(value: object, context: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_searchable_pdf_export(SearchablePdfExportErrorCode.MANIFEST_INVALID, context)
    return value
