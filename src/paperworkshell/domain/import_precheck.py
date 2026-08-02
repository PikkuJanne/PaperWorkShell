"""Pure contracts for bounded, preliminary import checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Never

MAX_SAFE_IMPORT_INTEGER: Final = 9_007_199_254_740_991
MINIMUM_HEADER_BUDGET: Final = 2_048
MAXIMUM_HEADER_BUDGET: Final = 1_048_576
MAXIMUM_STRUCTURE_RECORDS: Final = 256


class DetectedMediaType(StrEnum):
    """Content-derived media types admitted by PWS-D007."""

    PDF = "application/pdf"
    JPEG = "image/jpeg"
    PNG = "image/png"


class DeclaredMediaType(StrEnum):
    """Sanitized classification of the final filename suffix."""

    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    NONE = "none"
    OTHER = "other"


class ImportPreliminaryState(StrEnum):
    """Conservative state before worker-side document inspection."""

    NEEDS_WORKER_INSPECTION = "needs_worker_inspection"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    LIMIT_EXCEEDED = "limit_exceeded"
    SOURCE_UNAVAILABLE = "source_unavailable"


class FeatureCheckState(StrEnum):
    """Whether a feature was within the narrow precheck's authority."""

    NOT_CHECKED = "not_checked"
    NOT_APPLICABLE = "not_applicable"


class PdfEncryptionState(StrEnum):
    """Typed PDF encryption state populated only by an owning structural adapter."""

    NOT_CHECKED = "not_checked"
    ENCRYPTED = "encrypted"
    UNENCRYPTED = "unencrypted"
    NOT_APPLICABLE = "not_applicable"


class PendingImportCheck(StrEnum):
    """Checks explicitly deferred to an owning worker adapter task."""

    PDF_STRUCTURE = "pdf_structure"
    PDF_PAGE_COUNT = "pdf_page_count"
    PDF_ENCRYPTION = "pdf_encryption"
    PDF_SIGNATURE = "pdf_signature"
    IMAGE_DECODE = "image_decode"
    IMAGE_METADATA = "image_metadata"


class ImportPrecheckWarningCode(StrEnum):
    """Stable review signals that do not claim a document defect."""

    DECOMPRESSION_NEEDS_REVIEW = "IMPORT_DECOMPRESSION_NEEDS_REVIEW"


class ImportPrecheckErrorCode(StrEnum):
    """Closed, sanitized error-code registry for import prechecks."""

    LIMIT_INVALID = "IMPORT_LIMIT_INVALID"
    SOURCE_PATH_INVALID = "IMPORT_SOURCE_PATH_INVALID"
    SOURCE_LOCATION_UNSUPPORTED = "IMPORT_SOURCE_LOCATION_UNSUPPORTED"
    SOURCE_UNAVAILABLE = "IMPORT_SOURCE_UNAVAILABLE"
    SOURCE_NOT_REGULAR = "IMPORT_SOURCE_NOT_REGULAR"
    SOURCE_REPARSE_POINT = "IMPORT_SOURCE_REPARSE_POINT"
    SOURCE_ALIAS_DETECTED = "IMPORT_SOURCE_ALIAS_DETECTED"
    SOURCE_CHANGED = "IMPORT_SOURCE_CHANGED"
    SOURCE_READ_FAILED = "IMPORT_SOURCE_READ_FAILED"
    SOURCE_EMPTY = "IMPORT_SOURCE_EMPTY"
    SOURCE_TOO_LARGE = "IMPORT_SOURCE_TOO_LARGE"
    MEDIA_UNSUPPORTED = "IMPORT_MEDIA_UNSUPPORTED"
    EXTENSION_MISMATCH = "IMPORT_EXTENSION_MISMATCH"
    HEADER_TRUNCATED = "IMPORT_HEADER_TRUNCATED"
    HEADER_MALFORMED = "IMPORT_HEADER_MALFORMED"
    HEADER_LIMIT_EXCEEDED = "IMPORT_HEADER_LIMIT_EXCEEDED"
    IMAGE_DIMENSION_LIMIT_EXCEEDED = "IMPORT_IMAGE_DIMENSION_LIMIT_EXCEEDED"
    IMAGE_DIMENSIONS_UNAVAILABLE = "IMPORT_IMAGE_DIMENSIONS_UNAVAILABLE"
    IMAGE_PIXEL_LIMIT_EXCEEDED = "IMPORT_IMAGE_PIXEL_LIMIT_EXCEEDED"
    DECOMPRESSION_LIMIT_EXCEEDED = "IMPORT_DECOMPRESSION_LIMIT_EXCEEDED"
    PAGE_LIMIT_EXCEEDED = "IMPORT_PAGE_LIMIT_EXCEEDED"


@dataclass(slots=True)
class ImportPrecheckError(ValueError):
    """A stable import failure containing no path, filename, or source bytes."""

    code: ImportPrecheckErrorCode
    state: ImportPreliminaryState
    context: str
    safe_message: str

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_import(
    code: ImportPrecheckErrorCode,
    state: ImportPreliminaryState,
    context: str,
    safe_message: str,
) -> Never:
    """Raise a sanitized preliminary import failure."""

    raise ImportPrecheckError(code, state, context, safe_message)


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Caller-supplied limits with no unevidenced product defaults."""

    maximum_source_bytes: int
    maximum_pdf_pages: int
    maximum_image_width: int
    maximum_image_height: int
    maximum_image_pixels: int
    maximum_decompressed_bytes: int
    decompression_warning_ratio: int
    maximum_decompression_ratio: int
    maximum_header_bytes: int
    maximum_structure_records: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_source_bytes,
            self.maximum_pdf_pages,
            self.maximum_image_width,
            self.maximum_image_height,
            self.maximum_image_pixels,
            self.maximum_decompressed_bytes,
            self.decompression_warning_ratio,
            self.maximum_decompression_ratio,
            self.maximum_header_bytes,
            self.maximum_structure_records,
        )
        if any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_IMPORT_INTEGER for value in values
        ):
            reject_import(
                ImportPrecheckErrorCode.LIMIT_INVALID,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.configuration",
                "Import limits must be positive bounded integers.",
            )
        if not MINIMUM_HEADER_BUDGET <= self.maximum_header_bytes <= MAXIMUM_HEADER_BUDGET:
            reject_import(
                ImportPrecheckErrorCode.LIMIT_INVALID,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.header",
                "The header budget is outside the supported safe range.",
            )
        if self.decompression_warning_ratio > self.maximum_decompression_ratio:
            reject_import(
                ImportPrecheckErrorCode.LIMIT_INVALID,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.decompression",
                "The decompression warning threshold cannot exceed the hard limit.",
            )
        if self.maximum_structure_records > MAXIMUM_STRUCTURE_RECORDS:
            reject_import(
                ImportPrecheckErrorCode.LIMIT_INVALID,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.structure_records",
                "The structure-record budget exceeds the supported safe maximum.",
            )


@dataclass(frozen=True, slots=True)
class PreliminaryImageInfo:
    """Dimensions and expansion derived without decoding image content."""

    width: int
    height: int
    pixel_count: int
    estimated_decompressed_bytes: int
    component_count: int
    bits_per_component: int
    interlaced: bool


@dataclass(frozen=True, slots=True)
class ImportPrecheckResult:
    """Quarantine eligibility, never an authoritative source record."""

    media_type: DetectedMediaType
    byte_size: int
    page_count: int | None
    preliminary_state: ImportPreliminaryState
    encryption_state: PdfEncryptionState
    signature_state: FeatureCheckState
    image_info: PreliminaryImageInfo | None
    pending_checks: tuple[PendingImportCheck, ...]
    warnings: tuple[ImportPrecheckWarningCode, ...]
    precheck_version: int = 1


def require_observed_page_count(limits: ImportLimits, page_count: int) -> None:
    """Validate a later trusted worker observation against the same policy."""

    if type(page_count) is not int or page_count < 1:
        reject_import(
            ImportPrecheckErrorCode.HEADER_MALFORMED,
            ImportPreliminaryState.MALFORMED,
            "observation.page_count",
            "Document inspection returned an invalid page count.",
        )
    if page_count > limits.maximum_pdf_pages:
        reject_import(
            ImportPrecheckErrorCode.PAGE_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.pages",
            "The selected PDF exceeds the configured page limit.",
        )
