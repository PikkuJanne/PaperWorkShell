"""Pure, path-free contracts for deterministic verified PDF export.

The module turns one validated manifest snapshot into immutable export intent.
It owns no paths, file handles, native PDF objects, clocks, or random-number
generation.  Manifest values that could identify document content are either
excluded entirely or hidden from representations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.page_operations import (
    MAX_PAGE_COUNT,
    NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
    OrganizerProjection,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationReport,
)
from paperworkshell.domain.redaction import require_redaction_export_allowed
from paperworkshell.localization.catalog import tr

PDF_EXPORT_PLAN_VERSION: Final = 1
PDF_EXPORT_PROFILE_VERSION: Final = 1
IMAGE_PDF_EXPORT_VERSION: Final = 2
PORTAL_PDF_EXPORT_VERSION: Final = 3
PDF_EXPORT_REPORT_VERSION: Final = 1
OPTIMIZED_PDF_EXPORT_REPORT_VERSION: Final = 2
PORTAL_PDF_EXPORT_REPORT_VERSION: Final = 3
OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION: Final = 4
PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION: Final = 1
# One export source consumes one out-of-band worker handle.  Keeping the batch
# ceiling in the pure contract lets worker/protocol code import it without the
# domain importing a transport implementation.
MAX_PDF_EXPORT_SOURCES: Final = 255
MAX_PDF_EXPORT_PAGES: Final = MAX_PAGE_COUNT
MAX_PDF_EXPORT_CHECKS: Final = 8
MAX_PDF_EXPORT_HISTORY_RETRY_RECORD_BYTES: Final = 4 * 1024
PDF_EXPORT_HISTORY_RETRY_RECEIPT_VERSION: Final = 1

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
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
_KNOWN_MEDIA_TYPES: Final = frozenset({"application/pdf", "image/jpeg", "image/png"})
_KNOWN_SIGNATURE_STATES: Final = frozenset(
    {"not_checked", "not_applicable", "none_detected", "markers_detected", "unknown"}
)
_KNOWN_ENCRYPTION_STATES: Final = frozenset(
    {"not_checked", "not_applicable", "unencrypted", "encrypted", "unknown"}
)


class PdfExportErrorCode(StrEnum):
    """Closed registry of sanitized export-domain failures."""

    MODEL_INVALID = "PDF_EXPORT_MODEL_INVALID"
    MANIFEST_INVALID = "PDF_EXPORT_MANIFEST_INVALID"
    PLAN_EMPTY = "PDF_EXPORT_PLAN_EMPTY"
    PLAN_STALE = "PDF_EXPORT_PLAN_STALE"
    SOURCE_UNSUPPORTED = "PDF_EXPORT_SOURCE_UNSUPPORTED"
    SOURCE_ENCRYPTED = "PDF_EXPORT_SOURCE_ENCRYPTED"
    SOURCE_ENCRYPTION_UNKNOWN = "PDF_EXPORT_SOURCE_ENCRYPTION_UNKNOWN"
    SOURCE_SIGNATURE_UNKNOWN = "PDF_EXPORT_SOURCE_SIGNATURE_UNKNOWN"
    SOURCE_INCONSISTENT = "PDF_EXPORT_SOURCE_INCONSISTENT"
    SOURCE_CHANGED = "PDF_EXPORT_SOURCE_CHANGED"
    TRANSFORM_UNSUPPORTED = "PDF_EXPORT_TRANSFORM_UNSUPPORTED"
    LIMIT_EXCEEDED = "PDF_EXPORT_LIMIT_EXCEEDED"
    CANCELLED = "PDF_EXPORT_CANCELLED"
    TIMED_OUT = "PDF_EXPORT_TIMED_OUT"
    WORKER_FAILED = "PDF_EXPORT_WORKER_FAILED"
    ASSEMBLY_FAILED = "PDF_EXPORT_ASSEMBLY_FAILED"
    OCR_LANGUAGE_UNAVAILABLE = "PDF_EXPORT_OCR_LANGUAGE_UNAVAILABLE"
    VERIFICATION_REOPEN_FAILED = "PDF_EXPORT_VERIFICATION_REOPEN_FAILED"
    VERIFICATION_STRUCTURE_FAILED = "PDF_EXPORT_VERIFICATION_STRUCTURE_FAILED"
    VERIFICATION_PAGE_COUNT_MISMATCH = "PDF_EXPORT_VERIFICATION_PAGE_COUNT_MISMATCH"
    VERIFICATION_ENCRYPTION_FAILED = "PDF_EXPORT_VERIFICATION_ENCRYPTION_FAILED"
    VERIFICATION_RENDER_FAILED = "PDF_EXPORT_VERIFICATION_RENDER_FAILED"
    VERIFICATION_SEMANTIC_MISMATCH = "PDF_EXPORT_VERIFICATION_SEMANTIC_MISMATCH"
    DESTINATION_INVALID = "PDF_EXPORT_DESTINATION_INVALID"
    DESTINATION_EXISTS = "PDF_EXPORT_DESTINATION_EXISTS"
    DESTINATION_CAPACITY_EXCEEDED = "PDF_EXPORT_DESTINATION_CAPACITY_EXCEEDED"
    DESTINATION_FAILED = "PDF_EXPORT_DESTINATION_FAILED"
    DESTINATION_COMMIT_UNCERTAIN = "PDF_EXPORT_DESTINATION_COMMIT_UNCERTAIN"
    HISTORY_RECOVERY_REQUIRED = "PDF_EXPORT_HISTORY_RECOVERY_REQUIRED"
    REPORT_INVALID = "PDF_EXPORT_REPORT_INVALID"
    OUTCOME_INVALID = "PDF_EXPORT_OUTCOME_INVALID"
    RESULT_INVALID = "PDF_EXPORT_RESULT_INVALID"
    INTERNAL_ERROR = "PDF_EXPORT_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[PdfExportErrorCode, str]] = {
    PdfExportErrorCode.MODEL_INVALID: "PaperWorkShell received invalid PDF-export data.",
    PdfExportErrorCode.MANIFEST_INVALID: (
        "The case record cannot be used to create a PDF export plan."
    ),
    PdfExportErrorCode.PLAN_EMPTY: "Select at least one page before exporting a PDF.",
    PdfExportErrorCode.PLAN_STALE: (
        "The case changed before export could be committed. Start the export again."
    ),
    PdfExportErrorCode.SOURCE_UNSUPPORTED: (
        "The selected pages include a source that is not a supported PDF."
    ),
    PdfExportErrorCode.SOURCE_ENCRYPTED: (
        "The selected pages include an encrypted PDF that cannot be exported."
    ),
    PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
        "PaperWorkShell could not confirm that every selected PDF is unencrypted."
    ),
    PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN: (
        "PaperWorkShell could not confirm the digital-signature-marker state of every "
        "selected PDF. Use searchable PDF export after explicit review."
    ),
    PdfExportErrorCode.SOURCE_INCONSISTENT: ("The case source and page records do not agree."),
    PdfExportErrorCode.SOURCE_CHANGED: "A source PDF changed before export completed.",
    PdfExportErrorCode.TRANSFORM_UNSUPPORTED: (
        "The selected pages include an edit that this export version does not support."
    ),
    PdfExportErrorCode.LIMIT_EXCEEDED: (
        "The requested PDF export exceeds a configured safety limit."
    ),
    PdfExportErrorCode.CANCELLED: "PDF export was cancelled before completion.",
    PdfExportErrorCode.TIMED_OUT: "PDF export exceeded its configured time limit.",
    PdfExportErrorCode.WORKER_FAILED: "The document worker could not export the PDF safely.",
    PdfExportErrorCode.ASSEMBLY_FAILED: "PaperWorkShell could not assemble the PDF safely.",
    PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE: (
        "The selected OCR languages are not installed and verified. "
        "Review OCR language settings before retrying. The removal candidate remains unverified."
    ),
    PdfExportErrorCode.VERIFICATION_REOPEN_FAILED: (
        "The exported PDF could not be reopened for verification."
    ),
    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED: (
        "The exported PDF did not pass the configured structural check."
    ),
    PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH: (
        "The exported PDF does not contain the expected number of pages."
    ),
    PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED: (
        "PaperWorkShell could not confirm that the exported PDF is unencrypted."
    ),
    PdfExportErrorCode.VERIFICATION_RENDER_FAILED: (
        "At least one exported page could not be rendered for verification."
    ),
    PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH: (
        "Rendered page order or rotation does not match the export plan."
    ),
    PdfExportErrorCode.DESTINATION_INVALID: "Choose a supported PDF export destination.",
    PdfExportErrorCode.DESTINATION_EXISTS: (
        "A file already exists at the selected destination. Choose another name."
    ),
    PdfExportErrorCode.DESTINATION_CAPACITY_EXCEEDED: (
        "The selected destination does not have enough available storage for a safe export."
    ),
    PdfExportErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not save the verified PDF at the selected destination."
    ),
    PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN: (
        "PaperWorkShell could not confirm whether the verified PDF was saved. "
        "Check the selected destination before trying again."
    ),
    PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED: (
        "Export history recovery needs attention before another PDF can be exported."
    ),
    PdfExportErrorCode.REPORT_INVALID: (
        "PaperWorkShell received an invalid PDF-export verification report."
    ),
    PdfExportErrorCode.OUTCOME_INVALID: (
        "PaperWorkShell received an invalid PDF-export completion result."
    ),
    PdfExportErrorCode.RESULT_INVALID: "PaperWorkShell received an invalid PDF-export result.",
    PdfExportErrorCode.INTERNAL_ERROR: "PaperWorkShell could not export the PDF safely.",
}


@dataclass(slots=True, repr=False)
class PdfExportError(ValueError):
    """Stable failure containing no path, filename, digest, or manifest value."""

    code: PdfExportErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PdfExportErrorCode:
            self.code = PdfExportErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "pdf.export"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PdfExportError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_pdf_export(code: PdfExportErrorCode, context: str) -> Never:
    """Raise one closed export failure without retaining an active raw exception."""

    error = PdfExportError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PdfExportSourceMediaType(StrEnum):
    """Media type accepted by the first combined-PDF export profile."""

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"


class PdfExportSourceSignatureState(StrEnum):
    """Conservative signature-marker state copied from source inspection."""

    NONE_DETECTED = "none_detected"
    MARKERS_DETECTED = "markers_detected"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class PdfExportSourceEncryptionState(StrEnum):
    """Encryption state permitted in an executable export plan."""

    UNENCRYPTED = "unencrypted"
    NOT_APPLICABLE = "not_applicable"


class PdfExportEncryptionPolicy(StrEnum):
    """Output encryption policy for the current export profile."""

    MUST_BE_UNENCRYPTED = "must_be_unencrypted"


class PdfExportAssemblyProfile(StrEnum):
    """Deterministic page-copy semantics expected from the assembly adapter."""

    PIKEPDF_PAGE_COPY_V1 = "pikepdf_page_copy_v1"
    PIKEPDF_IMAGE_PAGES_V2 = "pikepdf_image_pages_v2"
    PIKEPDF_PORTAL_V3 = "pikepdf_portal_v3"


class PdfExportRenderProfile(StrEnum):
    """Fixed source/output raster-comparison semantics."""

    PDFIUM_RGBA8_CROP_WHITE_STORED_APPEARANCES_V1 = "pdfium_rgba8_crop_white_stored_appearances_v1"


class PdfExportQpdfProfile(StrEnum):
    """Fixed structural check semantics for generated output."""

    CHECK_NO_RECOVERY_NO_WARNINGS_V1 = "qpdf_check_no_recovery_no_warnings_v1"


class PdfExportRotation(IntEnum):
    """Clockwise rotation added to the copied source page."""

    DEGREES_0 = 0
    DEGREES_90 = 90
    DEGREES_180 = 180
    DEGREES_270 = 270


class PdfExportProgressPhase(StrEnum):
    """Closed, path-free phases for one application-level export attempt."""

    PREPARING = "preparing"
    ASSEMBLING_AND_VERIFYING = "assembling_and_verifying"
    SAVING = "saving"
    RECORDING = "recording"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PdfExportProgress:
    """Bounded page progress suitable for UI announcements."""

    phase: PdfExportProgressPhase
    completed_pages: int
    total_pages: int

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not PdfExportProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= MAX_PDF_EXPORT_PAGES
            or not 0 <= self.completed_pages <= self.total_pages
            or (self.phase is PdfExportProgressPhase.PREPARING and self.completed_pages != 0)
            or (
                self.phase
                in {
                    PdfExportProgressPhase.SAVING,
                    PdfExportProgressPhase.RECORDING,
                    PdfExportProgressPhase.COMPLETE,
                }
                and self.completed_pages != self.total_pages
            )
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "progress.model")


@dataclass(frozen=True, slots=True)
class PdfExportProfile:
    """Closed, versioned execution and verification semantics."""

    assembly: PdfExportAssemblyProfile = PdfExportAssemblyProfile.PIKEPDF_PAGE_COPY_V1
    render: PdfExportRenderProfile = (
        PdfExportRenderProfile.PDFIUM_RGBA8_CROP_WHITE_STORED_APPEARANCES_V1
    )
    qpdf: PdfExportQpdfProfile = PdfExportQpdfProfile.CHECK_NO_RECOVERY_NO_WARNINGS_V1
    encryption_policy: PdfExportEncryptionPolicy = PdfExportEncryptionPolicy.MUST_BE_UNENCRYPTED
    profile_version: int = PDF_EXPORT_PROFILE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.assembly) is not PdfExportAssemblyProfile
            or type(self.profile_version) is not int
            or (self.assembly, self.profile_version)
            not in {
                (PdfExportAssemblyProfile.PIKEPDF_PAGE_COPY_V1, PDF_EXPORT_PROFILE_VERSION),
                (PdfExportAssemblyProfile.PIKEPDF_IMAGE_PAGES_V2, IMAGE_PDF_EXPORT_VERSION),
                (PdfExportAssemblyProfile.PIKEPDF_PORTAL_V3, PORTAL_PDF_EXPORT_VERSION),
            }
            or self.render
            is not PdfExportRenderProfile.PDFIUM_RGBA8_CROP_WHITE_STORED_APPEARANCES_V1
            or self.qpdf is not PdfExportQpdfProfile.CHECK_NO_RECOVERY_NO_WARNINGS_V1
            or self.encryption_policy is not PdfExportEncryptionPolicy.MUST_BE_UNENCRYPTED
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "profile.model")

    @property
    def is_portal(self) -> bool:
        """Whether the conservative portal feature policy is required."""

        return self.assembly is PdfExportAssemblyProfile.PIKEPDF_PORTAL_V3


DEFAULT_PDF_EXPORT_PROFILE: Final = PdfExportProfile()
IMAGE_PDF_EXPORT_PROFILE: Final = PdfExportProfile(
    assembly=PdfExportAssemblyProfile.PIKEPDF_IMAGE_PAGES_V2,
    profile_version=IMAGE_PDF_EXPORT_VERSION,
)
PORTAL_PDF_EXPORT_PROFILE: Final = PdfExportProfile(
    assembly=PdfExportAssemblyProfile.PIKEPDF_PORTAL_V3,
    profile_version=PORTAL_PDF_EXPORT_VERSION,
)


@dataclass(frozen=True, slots=True)
class PdfExportSource:
    """Path-free immutable facts for one source required by an export plan."""

    source_id: UUID
    byte_size: int
    page_count: int
    signature_state: PdfExportSourceSignatureState
    sha256: str = field(repr=False)
    media_type: PdfExportSourceMediaType = PdfExportSourceMediaType.PDF
    encryption_state: PdfExportSourceEncryptionState = PdfExportSourceEncryptionState.UNENCRYPTED

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.source_id)
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_PDF_EXPORT_PAGES
            or type(self.signature_state) is not PdfExportSourceSignatureState
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
            or type(self.media_type) is not PdfExportSourceMediaType
            or type(self.encryption_state) is not PdfExportSourceEncryptionState
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "source.model")
        if self.media_type is PdfExportSourceMediaType.PDF:
            if (
                self.encryption_state is not PdfExportSourceEncryptionState.UNENCRYPTED
                or self.signature_state is PdfExportSourceSignatureState.NOT_APPLICABLE
            ):
                reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "source.pdf_state")
        elif (
            self.page_count != 1
            or self.signature_state is not PdfExportSourceSignatureState.NOT_APPLICABLE
            or self.encryption_state is not PdfExportSourceEncryptionState.NOT_APPLICABLE
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "source.image_state")

    @property
    def contains_signature_markers(self) -> bool:
        return self.signature_state is PdfExportSourceSignatureState.MARKERS_DETECTED


@dataclass(frozen=True, slots=True)
class PdfExportPage:
    """One exact source page at one deterministic output position."""

    output_page_index: int
    page_id: UUID
    source_id: UUID
    source_page_index: int
    additional_rotation: PdfExportRotation

    def __post_init__(self) -> None:
        if (
            type(self.output_page_index) is not int
            or not 0 <= self.output_page_index < MAX_PDF_EXPORT_PAGES
            or not _is_uuid4(self.page_id)
            or not _is_uuid4(self.source_id)
            or self.page_id == self.source_id
            or type(self.source_page_index) is not int
            or not 0 <= self.source_page_index < MAX_PDF_EXPORT_PAGES
            or type(self.additional_rotation) is not PdfExportRotation
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "page.model")


@dataclass(frozen=True, slots=True, init=False)
class PdfExportPlan:
    """Immutable export intent bound to one manifest state and semantic digest."""

    case_id: UUID
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    manifest_cas_token: str = field(repr=False)
    sources: tuple[PdfExportSource, ...]
    pages: tuple[PdfExportPage, ...]
    profile: PdfExportProfile
    semantic_fingerprint: str = field(repr=False)
    plan_version: int

    def __init__(
        self,
        *,
        case_id: UUID,
        manifest_revision: int,
        manifest_sha256: str,
        manifest_cas_token: str,
        sources: tuple[PdfExportSource, ...],
        pages: tuple[PdfExportPage, ...],
        profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
        plan_version: int = PDF_EXPORT_PLAN_VERSION,
    ) -> None:
        if (
            not _is_uuid4(case_id)
            or type(manifest_revision) is not int
            or not 0 <= manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(manifest_sha256) is not str
            or _SHA256.fullmatch(manifest_sha256) is None
            or type(manifest_cas_token) is not str
            or _CAS_TOKEN.fullmatch(manifest_cas_token) is None
            or type(sources) is not tuple
            or type(pages) is not tuple
            or type(profile) is not PdfExportProfile
            or type(plan_version) is not int
            or plan_version != profile.profile_version
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.model")
        if not pages:
            reject_pdf_export(PdfExportErrorCode.PLAN_EMPTY, "plan.pages")
        if (
            len(sources) > MAX_PDF_EXPORT_SOURCES
            or len(pages) > MAX_PDF_EXPORT_PAGES
            or any(type(source) is not PdfExportSource for source in sources)
            or any(type(page) is not PdfExportPage for page in pages)
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.collections")
        if any(
            source.signature_state is PdfExportSourceSignatureState.UNKNOWN for source in sources
        ):
            reject_pdf_export(
                PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
                "plan.source_signature",
            )
        if plan_version == PDF_EXPORT_PLAN_VERSION and any(
            source.media_type is not PdfExportSourceMediaType.PDF for source in sources
        ):
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "plan.source_media")
        source_ids = tuple(source.source_id for source in sources)
        page_ids = tuple(page.page_id for page in pages)
        source_positions = tuple((page.source_id, page.source_page_index) for page in pages)
        if (
            len(set(source_ids)) != len(source_ids)
            or len(set(page_ids)) != len(page_ids)
            or len(set(source_positions)) != len(source_positions)
            or tuple(page.output_page_index for page in pages) != tuple(range(len(pages)))
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.identities")
        source_by_id = {source.source_id: source for source in sources}
        first_use_order = tuple(dict.fromkeys(page.source_id for page in pages))
        if source_ids != first_use_order:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.source_order")
        for page in pages:
            source = source_by_id.get(page.source_id)
            if source is None or page.source_page_index >= source.page_count:
                reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.page_source")
        semantic_fingerprint = _calculate_semantic_fingerprint(
            pages=pages,
            source_by_id=source_by_id,
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


class PdfExportCheckCode(StrEnum):
    """Closed successful checks; each code maps to one concrete claim."""

    PDF_REOPENED = "PDF_REOPENED"
    STRUCTURE_CLEAN = "STRUCTURE_CLEAN"
    PAGE_COUNT_MATCHED = "PAGE_COUNT_MATCHED"
    UNENCRYPTED = "UNENCRYPTED"
    ALL_PAGES_RENDERED = "ALL_PAGES_RENDERED"
    ORDER_ROTATION_MATCHED = "ORDER_ROTATION_MATCHED"
    OUTPUT_COMMIT_REOPENED = "OUTPUT_COMMIT_REOPENED"
    UNSIGNED_DERIVATIVE = "UNSIGNED_DERIVATIVE"
    PORTAL_PROFILE_VERIFIED = "PORTAL_PROFILE_VERIFIED"


_COUNTED_CHECKS: Final = frozenset(
    {
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    }
)
_BASE_SUCCESSFUL_CHECKS: Final = (
    PdfExportCheckCode.PDF_REOPENED,
    PdfExportCheckCode.STRUCTURE_CLEAN,
    PdfExportCheckCode.PAGE_COUNT_MATCHED,
    PdfExportCheckCode.UNENCRYPTED,
    PdfExportCheckCode.ALL_PAGES_RENDERED,
    PdfExportCheckCode.ORDER_ROTATION_MATCHED,
)


@dataclass(frozen=True, slots=True)
class PdfExportCheck:
    """One performed successful check with no caller-provided wording."""

    code: PdfExportCheckCode
    page_count: int | None = None

    def __post_init__(self) -> None:
        if type(self.code) is not PdfExportCheckCode:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.check")
        if self.code in _COUNTED_CHECKS:
            if type(self.page_count) is not int or not 1 <= self.page_count <= MAX_PDF_EXPORT_PAGES:
                reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.check_count")
        elif self.page_count is not None:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.check_count")

    @property
    def claim(self) -> str:
        """Return exact plain-language wording for this performed check."""

        if self.code is PdfExportCheckCode.PDF_REOPENED:
            return tr("The exported PDF reopened successfully.")
        if self.code is PdfExportCheckCode.STRUCTURE_CLEAN:
            return tr("The qpdf structural check completed without warnings.")
        if self.code is PdfExportCheckCode.PAGE_COUNT_MATCHED:
            return tr(
                "The exported PDF contains the expected {value_0} pages.", value_0=self.page_count
            )
        if self.code is PdfExportCheckCode.UNENCRYPTED:
            return tr("The exported PDF is not encrypted.")
        if self.code is PdfExportCheckCode.ALL_PAGES_RENDERED:
            return tr(
                "All {value_0} pages rendered with the verification profile.",
                value_0=self.page_count,
            )
        if self.code is PdfExportCheckCode.ORDER_ROTATION_MATCHED:
            return tr(
                "Rendered page order and rotations match the export plan for all {value_0} pages.",
                value_0=self.page_count,
            )
        if self.code is PdfExportCheckCode.OUTPUT_COMMIT_REOPENED:
            return tr("PaperWorkShell reopened the committed output successfully.")
        if self.code is PdfExportCheckCode.PORTAL_PROFILE_VERIFIED:
            return tr(
                "The exported PDF passed the conservative portal feature checks. "
                "Portal acceptance still depends on the receiving service."
            )
        return tr(
            "The exported PDF is an unsigned derivative because at least one source "
            "contained digital-signature markers or its marker state could not be confirmed."
        )


@dataclass(frozen=True, slots=True)
class PdfExportVerificationReport:
    """Successful worker verification facts for one uncommitted candidate PDF."""

    semantic_fingerprint: str = field(repr=False)
    output_sha256: str = field(repr=False)
    output_byte_size: int
    page_count: int
    checks: tuple[PdfExportCheck, ...]
    unsigned_derivative: bool
    report_version: int = PDF_EXPORT_REPORT_VERSION
    optimization: PdfOptimizationReport | None = None

    def __post_init__(self) -> None:
        if (
            type(self.semantic_fingerprint) is not str
            or _SHA256.fullmatch(self.semantic_fingerprint) is None
            or type(self.output_sha256) is not str
            or _SHA256.fullmatch(self.output_sha256) is None
            or type(self.output_byte_size) is not int
            or not 1 <= self.output_byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_PDF_EXPORT_PAGES
            or type(self.checks) is not tuple
            or len(self.checks) > MAX_PDF_EXPORT_CHECKS
            or any(type(check) is not PdfExportCheck for check in self.checks)
            or type(self.unsigned_derivative) is not bool
            or type(self.report_version) is not int
            or self.report_version
            not in {
                PDF_EXPORT_REPORT_VERSION,
                OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
                PORTAL_PDF_EXPORT_REPORT_VERSION,
                OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION,
            }
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.model")
        if self.report_version in {PDF_EXPORT_REPORT_VERSION, PORTAL_PDF_EXPORT_REPORT_VERSION}:
            if self.optimization is not None:
                reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.optimization")
        elif (
            type(self.optimization) is not PdfOptimizationReport
            or self.optimization.selected_byte_size != self.output_byte_size
            or self.optimization.verified_page_count
            != (
                self.page_count
                if self.optimization.outcome is PdfOptimizationOutcome.OPTIMIZED
                else 0
            )
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.optimization")
        expected_codes = _BASE_SUCCESSFUL_CHECKS + (
            (PdfExportCheckCode.UNSIGNED_DERIVATIVE,) if self.unsigned_derivative else ()
        )
        if self.portal_profile_verified:
            expected_codes += (PdfExportCheckCode.PORTAL_PROFILE_VERIFIED,)
        if tuple(check.code for check in self.checks) != expected_codes:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.checks")
        if any(
            check.page_count != self.page_count
            for check in self.checks
            if check.code in _COUNTED_CHECKS
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.page_count")

    @classmethod
    def for_verified_plan(
        cls,
        plan: PdfExportPlan,
        *,
        output_sha256: str,
        output_byte_size: int,
    ) -> PdfExportVerificationReport:
        """Construct the closed report after every named check has succeeded."""

        if type(plan) is not PdfExportPlan:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "report.plan")
        codes = _BASE_SUCCESSFUL_CHECKS + (
            (PdfExportCheckCode.UNSIGNED_DERIVATIVE,) if plan.contains_signature_markers else ()
        )
        if plan.profile.is_portal:
            codes += (PdfExportCheckCode.PORTAL_PROFILE_VERIFIED,)
        checks = tuple(
            PdfExportCheck(
                code=code,
                page_count=plan.page_count if code in _COUNTED_CHECKS else None,
            )
            for code in codes
        )
        return cls(
            semantic_fingerprint=plan.semantic_fingerprint,
            output_sha256=output_sha256,
            output_byte_size=output_byte_size,
            page_count=plan.page_count,
            checks=checks,
            unsigned_derivative=plan.contains_signature_markers,
            report_version=(
                PORTAL_PDF_EXPORT_REPORT_VERSION
                if plan.profile.is_portal
                else PDF_EXPORT_REPORT_VERSION
            ),
        )

    @property
    def portal_profile_verified(self) -> bool:
        """Whether this version requires the successful portal-policy check."""

        return self.report_version in {
            PORTAL_PDF_EXPORT_REPORT_VERSION,
            OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION,
        }

    @property
    def claims(self) -> tuple[str, ...]:
        optimization_claims = () if self.optimization is None else (self.optimization.claim,)
        return tuple(check.claim for check in self.checks) + optimization_claims


class PdfExportOutcomeStatus(StrEnum):
    """Post-commit state without ever describing a saved output as failed."""

    COMMITTED_RECORDED = "committed_recorded"
    COMMITTED_HISTORY_PENDING = "committed_history_pending"


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportHistoryRetryReceipt:
    """Bounded canonical record retained for a history-only retry."""

    case_id: UUID = field(repr=False)
    export_id: UUID = field(repr=False)
    report: PdfExportVerificationReport = field(repr=False)
    record_sha256: str = field(repr=False)
    record_payload: bytes = field(repr=False)
    receipt_version: int = PDF_EXPORT_HISTORY_RETRY_RECEIPT_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.case_id)
            or not _is_uuid4(self.export_id)
            or type(self.report) is not PdfExportVerificationReport
            or type(self.record_sha256) is not str
            or _SHA256.fullmatch(self.record_sha256) is None
            or type(self.record_payload) is not bytes
            or not 1 <= len(self.record_payload) <= MAX_PDF_EXPORT_HISTORY_RETRY_RECORD_BYTES
            or hashlib.sha256(self.record_payload).hexdigest() != self.record_sha256
            or type(self.receipt_version) is not int
            or self.receipt_version != PDF_EXPORT_HISTORY_RETRY_RECEIPT_VERSION
        ):
            reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "outcome.history_retry")

    def __repr__(self) -> str:
        return (
            "PdfExportHistoryRetryReceipt("
            f"receipt_version={self.receipt_version}, byte_size={len(self.record_payload)})"
        )


@dataclass(frozen=True, slots=True)
class PdfExportOutcome:
    """Final save result created only after the destination was reopened."""

    export_id: UUID = field(repr=False)
    status: PdfExportOutcomeStatus
    report: PdfExportVerificationReport
    destination_reopened: bool = field(repr=False)
    history_retry: PdfExportHistoryRetryReceipt = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.export_id)
            or type(self.status) is not PdfExportOutcomeStatus
            or type(self.report) is not PdfExportVerificationReport
            or self.destination_reopened is not True
            or type(self.history_retry) is not PdfExportHistoryRetryReceipt
            or self.history_retry.export_id != self.export_id
            or self.history_retry.report != self.report
        ):
            reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "outcome.model")

    @property
    def checks(self) -> tuple[PdfExportCheck, ...]:
        """Combine worker facts with the trusted destination-layer reopen fact."""

        return self.report.checks + (PdfExportCheck(PdfExportCheckCode.OUTPUT_COMMIT_REOPENED),)

    @property
    def claims(self) -> tuple[str, ...]:
        return self.report.claims + (
            PdfExportCheck(PdfExportCheckCode.OUTPUT_COMMIT_REOPENED).claim,
        )

    @property
    def history_retry_required(self) -> bool:
        return self.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING

    @property
    def export_retry_required(self) -> bool:
        """A committed verified output must never be recreated to repair history."""

        return False

    @property
    def safe_message(self) -> str:
        prefix = tr("Unsigned derivative: ") if self.report.unsigned_derivative else ""
        if self.status is PdfExportOutcomeStatus.COMMITTED_RECORDED:
            return tr(
                "{value_0}The verified PDF was saved and recorded in case history.", value_0=prefix
            )
        message = tr(
            "The verified PDF was saved, but PaperWorkShell could not record it in case "
            "history. The output does not need to be exported again."
        )
        return tr("{value_0}{value_1}", value_0=prefix, value_1=message)


@dataclass(frozen=True, slots=True)
class _ManifestSource:
    source_id: UUID
    media_type: str
    byte_size: int
    page_count: int
    sha256: str
    signature_state: str
    encryption_state: str


def build_pdf_export_plan(
    snapshot: ManifestSnapshot,
    *,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> PdfExportPlan:
    """Resolve exact active page order and rotations from one manifest snapshot."""

    document, projection = _read_pdf_export_snapshot(snapshot, profile)
    return _build_pdf_export_plan_for_pages(
        snapshot,
        document,
        projection,
        projection.page_sequence,
        profile,
    )


def build_pdf_export_plan_for_pages(
    snapshot: ManifestSnapshot,
    ordered_page_ids: tuple[UUID, ...],
    *,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> PdfExportPlan:
    """Resolve one exact nonempty ordered subset of active manifest pages."""

    document, projection = _read_pdf_export_snapshot(snapshot, profile)
    return _build_pdf_export_plan_for_pages(
        snapshot,
        document,
        projection,
        ordered_page_ids,
        profile,
    )


def _read_pdf_export_snapshot(
    snapshot: ManifestSnapshot,
    profile: PdfExportProfile,
) -> tuple[dict[str, object], OrganizerProjection]:
    if type(snapshot) is not ManifestSnapshot or type(profile) is not PdfExportProfile:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.input")
    try:
        projection = PageCommandHistory.from_snapshot(snapshot).projection
        document = snapshot.document
    except PageOperationError as error:
        code = (
            PdfExportErrorCode.LIMIT_EXCEEDED
            if error.code is PageOperationErrorCode.CAPACITY_EXCEEDED
            else PdfExportErrorCode.MANIFEST_INVALID
        )
        reject_pdf_export(code, "plan.manifest")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.manifest")
    require_redaction_export_allowed(document)
    return document, projection


def _build_pdf_export_plan_for_pages(
    snapshot: ManifestSnapshot,
    document: dict[str, object],
    projection: OrganizerProjection,
    ordered_page_ids: tuple[UUID, ...],
    profile: PdfExportProfile,
) -> PdfExportPlan:
    _validate_ordered_page_ids(ordered_page_ids)
    state_by_id = {state.page_id: state for state in projection.pages}
    active_page_ids = frozenset(projection.page_sequence)
    for page_id in ordered_page_ids:
        state = state_by_id.get(page_id)
        if state is None or not state.active or page_id not in active_page_ids:
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "plan.page_state")
    manifest_sources = _parse_manifest_sources(document.get("sources"))
    source_by_id = {source.source_id: source for source in manifest_sources}
    _reject_active_overlays(
        document.get("overlays"),
        known_page_ids=frozenset(state_by_id),
        active_page_ids=frozenset(ordered_page_ids),
    )
    export_sources: list[PdfExportSource] = []
    export_source_by_id: dict[UUID, PdfExportSource] = {}
    export_pages: list[PdfExportPage] = []
    for output_page_index, page_id in enumerate(ordered_page_ids):
        state = state_by_id[page_id]
        if (
            state.crop is not None
            or state.deskew_angle_millidegrees_clockwise != 0
            or state.contrast_factor_millionths != NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
            or state.background_cleanup_strength_millionths != 0
        ):
            reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "plan.scan_transform")
        source = source_by_id.get(state.reference.source_id)
        if source is None or state.reference.source_page_index >= source.page_count:
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "plan.page_source")
        export_source = export_source_by_id.get(source.source_id)
        if export_source is None:
            if len(export_sources) >= MAX_PDF_EXPORT_SOURCES:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.sources")
            export_source = _select_export_source(source, profile)
            export_source_by_id[source.source_id] = export_source
            export_sources.append(export_source)
        export_pages.append(
            PdfExportPage(
                output_page_index=output_page_index,
                page_id=state.page_id,
                source_id=source.source_id,
                source_page_index=state.reference.source_page_index,
                additional_rotation=PdfExportRotation(state.quarter_turns_clockwise * 90),
            )
        )
    return PdfExportPlan(
        case_id=snapshot.case_id,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        sources=tuple(export_sources),
        pages=tuple(export_pages),
        profile=profile,
        plan_version=profile.profile_version,
    )


def _validate_ordered_page_ids(ordered_page_ids: tuple[UUID, ...]) -> None:
    if type(ordered_page_ids) is not tuple:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.page_ids")
    if not ordered_page_ids:
        reject_pdf_export(PdfExportErrorCode.PLAN_EMPTY, "plan.pages")
    if len(ordered_page_ids) > MAX_PDF_EXPORT_PAGES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.page_ids")
    if any(not _is_uuid4(page_id) for page_id in ordered_page_ids):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.page_ids")
    if len(set(ordered_page_ids)) != len(ordered_page_ids):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "plan.page_ids")


def _parse_manifest_sources(value: object) -> tuple[_ManifestSource, ...]:
    if type(value) is not list:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.sources")
    if len(value) > MAX_PAGE_COUNT:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.sources")
    parsed: list[_ManifestSource] = []
    identifiers: set[UUID] = set()
    for member in value:
        if type(member) is not dict or frozenset(member) != _SOURCE_MEMBERS:
            reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.source")
        record = cast(Mapping[str, object], member)
        source_id = _parse_uuid(record["source_id"], context="plan.source_id")
        media_type = _parse_known_string(
            record["detected_media_type"], _KNOWN_MEDIA_TYPES, context="plan.media_type"
        )
        signature_state = _parse_known_string(
            record["signature_state"],
            _KNOWN_SIGNATURE_STATES,
            context="plan.signature_state",
        )
        encryption_state = _parse_known_string(
            record["encryption_state"],
            _KNOWN_ENCRYPTION_STATES,
            context="plan.encryption_state",
        )
        byte_size = _parse_positive_integer(record["byte_size"], context="plan.byte_size")
        page_count = _parse_positive_integer(record["page_count"], context="plan.page_count")
        sha256 = record["sha256"]
        if (
            source_id in identifiers
            or type(sha256) is not str
            or _SHA256.fullmatch(sha256) is None
            or record["import_state"] != "complete"
        ):
            reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.source")
        identifiers.add(source_id)
        parsed.append(
            _ManifestSource(
                source_id=source_id,
                media_type=media_type,
                byte_size=byte_size,
                page_count=page_count,
                sha256=sha256,
                signature_state=signature_state,
                encryption_state=encryption_state,
            )
        )
    return tuple(parsed)


def _reject_active_overlays(
    value: object,
    *,
    known_page_ids: frozenset[UUID],
    active_page_ids: frozenset[UUID],
) -> None:
    """Fail closed on overlay intent that would otherwise be dropped from output.

    Overlay intent on a removed page has no effect on the current output
    sequence.  It remains in the case for a later restore and therefore does
    not block export while that page is inactive.
    """

    if type(value) is not list:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.overlays")
    if len(value) > MAX_PAGE_COUNT:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.overlays")
    for member in value:
        if type(member) is not dict or frozenset(member) != _OVERLAY_MEMBERS:
            reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.overlay")
        page_id = _parse_uuid(member["page_id"], context="plan.overlay_page")
        if page_id not in known_page_ids:
            reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "plan.overlay_page")
        if page_id in active_page_ids:
            reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "plan.overlay")


def _select_export_source(source: _ManifestSource, profile: PdfExportProfile) -> PdfExportSource:
    if source.media_type != PdfExportSourceMediaType.PDF.value:
        if profile not in {IMAGE_PDF_EXPORT_PROFILE, PORTAL_PDF_EXPORT_PROFILE}:
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "plan.source_media")
        try:
            return PdfExportSource(
                source_id=source.source_id,
                byte_size=source.byte_size,
                page_count=source.page_count,
                sha256=source.sha256,
                media_type=PdfExportSourceMediaType(source.media_type),
                signature_state=PdfExportSourceSignatureState(source.signature_state),
                encryption_state=PdfExportSourceEncryptionState(source.encryption_state),
            )
        except ValueError:
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "plan.image_state")
    if source.page_count > MAX_PDF_EXPORT_PAGES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "plan.source_pages")
    if source.encryption_state == "encrypted":
        reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "plan.source_encryption")
    if source.encryption_state in {"unknown", "not_checked"}:
        reject_pdf_export(
            PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN,
            "plan.source_encryption",
        )
    if source.encryption_state != PdfExportSourceEncryptionState.UNENCRYPTED.value:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "plan.source_encryption")
    if source.signature_state in {"unknown", "not_checked"}:
        reject_pdf_export(
            PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
            "plan.source_signature",
        )
    try:
        signature_state = PdfExportSourceSignatureState(source.signature_state)
    except ValueError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "plan.source_signature")
    return PdfExportSource(
        source_id=source.source_id,
        byte_size=source.byte_size,
        page_count=source.page_count,
        signature_state=signature_state,
        sha256=source.sha256,
    )


def _calculate_semantic_fingerprint(
    *,
    pages: tuple[PdfExportPage, ...],
    source_by_id: Mapping[UUID, PdfExportSource],
    profile: PdfExportProfile,
    plan_version: int,
) -> str:
    document = {
        "fingerprint_version": PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION,
        "plan_version": plan_version,
        "profile": {
            "profile_version": profile.profile_version,
            "assembly": profile.assembly.value,
            "render": profile.render.value,
            "qpdf": profile.qpdf.value,
            "encryption_policy": profile.encryption_policy.value,
        },
        "pages": [
            {
                **(
                    {"source_media_type": source_by_id[page.source_id].media_type.value}
                    if plan_version in {IMAGE_PDF_EXPORT_VERSION, PORTAL_PDF_EXPORT_VERSION}
                    else {}
                ),
                "source_sha256": source_by_id[page.source_id].sha256,
                "source_byte_size": source_by_id[page.source_id].byte_size,
                "source_page_count": source_by_id[page.source_id].page_count,
                "source_page_index": page.source_page_index,
                "additional_rotation_degrees": int(page.additional_rotation),
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


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _parse_uuid(value: object, *, context: str) -> UUID:
    if type(value) is not str:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, context)
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, context)
    if parsed.version != 4 or str(parsed) != value:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, context)
    return parsed


def _parse_positive_integer(value: object, *, context: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, context)
    return value


def _parse_known_string(
    value: object,
    allowed: frozenset[str],
    *,
    context: str,
) -> str:
    if type(value) is not str or value not in allowed:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, context)
    return value
