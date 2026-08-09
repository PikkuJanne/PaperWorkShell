"""Pure contracts for immutable source ingest and integrity verification."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.import_precheck import DetectedMediaType

MAX_SAFE_SOURCE_INTEGER: Final = 9_007_199_254_740_991
MAX_SOURCE_PAGES: Final = 4_096
MAX_SOURCE_DISPLAY_NAME_CODEPOINTS: Final = 255
MAX_SOURCE_OPERATION_TIMEOUT_SECONDS: Final = 86_400.0

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")


class SourceIngestErrorCode(StrEnum):
    """Closed registry of safe immutable-ingest failures."""

    IDENTIFIER_INVALID = "SOURCE_IDENTIFIER_INVALID"
    SOURCE_PATH_INVALID = "SOURCE_PATH_INVALID"
    DISPLAY_NAME_INVALID = "SOURCE_DISPLAY_NAME_INVALID"
    MODEL_INVALID = "SOURCE_MODEL_INVALID"
    INSPECTION_INVALID = "SOURCE_INSPECTION_INVALID"
    INSPECTION_MISMATCH = "SOURCE_INSPECTION_MISMATCH"
    CANCELLED = "SOURCE_IMPORT_CANCELLED"
    TIMED_OUT = "SOURCE_OPERATION_TIMED_OUT"
    CANCELLATION_FAILED = "SOURCE_CANCELLATION_CHECK_FAILED"
    PROGRESS_FAILED = "SOURCE_PROGRESS_FAILED"
    CASE_UNAVAILABLE = "SOURCE_CASE_UNAVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    CAPACITY_EXCEEDED = "SOURCE_CAPACITY_EXCEEDED"
    COPY_FAILED = "SOURCE_COPY_FAILED"
    MANIFEST_INVALID = "SOURCE_MANIFEST_INVALID"
    MANIFEST_VERSION_UNSUPPORTED = "SOURCE_MANIFEST_VERSION_UNSUPPORTED"
    MANIFEST_CONFLICT = "SOURCE_MANIFEST_CONFLICT"
    PUBLISH_FAILED = "SOURCE_PUBLISH_FAILED"
    PREPARE_FAILED = "SOURCE_PREPARE_FAILED"
    INSPECT_FAILED = "SOURCE_INSPECT_FAILED"
    COMMIT_FAILED = "SOURCE_COMMIT_FAILED"
    VERIFY_FAILED = "SOURCE_VERIFY_FAILED"
    RESULT_INVALID = "SOURCE_RESULT_INVALID"
    INTERNAL_ERROR = "SOURCE_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[SourceIngestErrorCode, str]] = {
    SourceIngestErrorCode.IDENTIFIER_INVALID: (
        "The source operation does not have a valid internal identifier."
    ),
    SourceIngestErrorCode.SOURCE_PATH_INVALID: (
        "Select a supported local source file and try again."
    ),
    SourceIngestErrorCode.DISPLAY_NAME_INVALID: (
        "The source display name contains unsupported characters or is too long."
    ),
    SourceIngestErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid source-operation data."),
    SourceIngestErrorCode.INSPECTION_INVALID: (
        "Document inspection did not provide complete supported source metadata."
    ),
    SourceIngestErrorCode.INSPECTION_MISMATCH: (
        "Document inspection did not match the prepared source."
    ),
    SourceIngestErrorCode.CANCELLED: "The source operation was cancelled before completion.",
    SourceIngestErrorCode.TIMED_OUT: (
        "The source operation exceeded its configured time limit before completion."
    ),
    SourceIngestErrorCode.CANCELLATION_FAILED: (
        "PaperWorkShell could not read the source-operation cancellation state safely."
    ),
    SourceIngestErrorCode.PROGRESS_FAILED: (
        "PaperWorkShell could not report source-operation progress safely."
    ),
    SourceIngestErrorCode.CASE_UNAVAILABLE: "The selected case is not available for import.",
    SourceIngestErrorCode.SOURCE_UNAVAILABLE: "The selected source file is not available.",
    SourceIngestErrorCode.SOURCE_CHANGED: (
        "The selected source changed before import could complete."
    ),
    SourceIngestErrorCode.CAPACITY_EXCEEDED: (
        "The selected case does not have enough configured storage capacity."
    ),
    SourceIngestErrorCode.COPY_FAILED: (
        "PaperWorkShell could not create and verify the immutable case copy."
    ),
    SourceIngestErrorCode.MANIFEST_INVALID: (
        "The case record is invalid and the source was not committed."
    ),
    SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED: (
        "This case uses an unsupported record version and was not changed."
    ),
    SourceIngestErrorCode.MANIFEST_CONFLICT: (
        "The case changed during import. Try the source operation again."
    ),
    SourceIngestErrorCode.PUBLISH_FAILED: (
        "PaperWorkShell could not publish the verified case copy safely."
    ),
    SourceIngestErrorCode.PREPARE_FAILED: (
        "PaperWorkShell could not prepare the selected source safely."
    ),
    SourceIngestErrorCode.INSPECT_FAILED: (
        "PaperWorkShell could not inspect the prepared source safely."
    ),
    SourceIngestErrorCode.COMMIT_FAILED: (
        "PaperWorkShell could not commit the verified source safely."
    ),
    SourceIngestErrorCode.VERIFY_FAILED: (
        "PaperWorkShell could not verify the immutable case copy safely."
    ),
    SourceIngestErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid source-operation result."
    ),
    SourceIngestErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete the source operation safely."
    ),
}


@dataclass(slots=True, repr=False)
class SourceIngestError(ValueError):
    """Stable failure with no path, display name, digest, or raw exception text."""

    code: SourceIngestErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not SourceIngestErrorCode:
            self.code = SourceIngestErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "source.operation"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"SourceIngestError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_source_ingest(code: SourceIngestErrorCode, context: str) -> Never:
    """Raise one closed, sanitized immutable-ingest failure."""

    raise SourceIngestError(code=code, context=context)


def require_internal_id(value: object, *, context: str) -> UUID:
    """Require an opaque RFC-4122 UUIDv4 without accepting path or display text."""

    if type(value) is not UUID or value.version != 4:
        reject_source_ingest(SourceIngestErrorCode.IDENTIFIER_INVALID, context)
    return value


def require_source_display_name(value: object) -> str:
    """Validate sensitive presentation text without retaining it in public result models."""

    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_SOURCE_DISPLAY_NAME_CODEPOINTS
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        reject_source_ingest(
            SourceIngestErrorCode.DISPLAY_NAME_INVALID,
            "source.display_name",
        )
    return value


def require_source_timeout_seconds(value: object, *, context: str) -> float:
    """Return one finite positive timeout within the source-operation hard limit."""

    if type(value) is int:
        if 0 < value <= MAX_SOURCE_OPERATION_TIMEOUT_SECONDS:
            return float(value)
    elif (
        type(value) is float
        and math.isfinite(value)
        and 0 < value <= MAX_SOURCE_OPERATION_TIMEOUT_SECONDS
    ):
        return value
    reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, context)


class SourceSignatureState(StrEnum):
    """Authoritative, conservative signature-marker inspection state."""

    NONE_DETECTED = "none_detected"
    MARKERS_DETECTED = "markers_detected"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class SourceEncryptionState(StrEnum):
    """Authoritative, conservative encryption inspection state."""

    UNENCRYPTED = "unencrypted"
    ENCRYPTED = "encrypted"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class PreparedSourceSummary:
    """Path-free identity and verified byte facts for one pending case copy."""

    pending_id: UUID
    case_id: UUID
    source_id: UUID
    media_type: DetectedMediaType
    byte_size: int
    sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        identifiers = (self.pending_id, self.case_id, self.source_id)
        contexts = ("source.pending_id", "source.case_id", "source.source_id")
        for identifier, context in zip(identifiers, contexts, strict=True):
            require_internal_id(identifier, context=context)
        if len(set(identifiers)) != len(identifiers):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.identifiers")
        _require_media_type(self.media_type, context="source.media_type")
        _require_positive_integer(self.byte_size, context="source.byte_size")
        _require_sha256(self.sha256, context="source.sha256")


@dataclass(frozen=True, slots=True)
class CompletedSourceInspection:
    """Complete worker-owned metadata bound to one prepared source."""

    pending_id: UUID
    media_type: DetectedMediaType
    page_count: int
    signature_state: SourceSignatureState
    encryption_state: SourceEncryptionState
    inspection_version: int = 1

    def __post_init__(self) -> None:
        require_internal_id(self.pending_id, context="inspection.pending_id")
        _require_media_type(self.media_type, context="inspection.media_type")
        if type(self.page_count) is not int or not 1 <= self.page_count <= MAX_SOURCE_PAGES:
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.page_count",
            )
        if type(self.inspection_version) is not int or self.inspection_version != 1:
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.version",
            )
        if (
            type(self.signature_state) is not SourceSignatureState
            or type(self.encryption_state) is not SourceEncryptionState
        ):
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.states",
            )
        if self.media_type is DetectedMediaType.PDF:
            if (
                self.signature_state is SourceSignatureState.NOT_APPLICABLE
                or self.encryption_state is SourceEncryptionState.NOT_APPLICABLE
            ):
                reject_source_ingest(
                    SourceIngestErrorCode.INSPECTION_INVALID,
                    "inspection.pdf_states",
                )
        elif (
            self.page_count != 1
            or self.signature_state is not SourceSignatureState.NOT_APPLICABLE
            or self.encryption_state is not SourceEncryptionState.NOT_APPLICABLE
        ):
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.image_states",
            )


@dataclass(frozen=True, slots=True)
class DuplicateContentSignal:
    """Advisory matches against previously committed source digests."""

    matching_source_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if type(self.matching_source_ids) is not tuple:
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "duplicate.matches")
        for source_id in self.matching_source_ids:
            require_internal_id(source_id, context="duplicate.source_id")
        if len(set(self.matching_source_ids)) != len(self.matching_source_ids):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "duplicate.matches")
        object.__setattr__(
            self,
            "matching_source_ids",
            tuple(sorted(self.matching_source_ids, key=str)),
        )

    @property
    def detected(self) -> bool:
        """Return whether at least one prior source has matching recorded content."""

        return bool(self.matching_source_ids)


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    """Committed path-free source identity and advisory duplicate information."""

    case_id: UUID
    source_id: UUID
    page_ids: tuple[UUID, ...]
    media_type: DetectedMediaType
    byte_size: int
    sha256: str = field(repr=False)
    manifest_revision: int
    duplicate: DuplicateContentSignal = field(default_factory=DuplicateContentSignal)

    def __post_init__(self) -> None:
        require_internal_id(self.case_id, context="result.case_id")
        require_internal_id(self.source_id, context="result.source_id")
        if self.case_id == self.source_id:
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "result.identifiers")
        if type(self.page_ids) is not tuple or not self.page_ids:
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "result.page_ids")
        for page_id in self.page_ids:
            require_internal_id(page_id, context="result.page_id")
        if (
            len(self.page_ids) > MAX_SOURCE_PAGES
            or len(set(self.page_ids)) != len(self.page_ids)
            or not set(self.page_ids).isdisjoint({self.case_id, self.source_id})
        ):
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "result.page_ids")
        _require_media_type(self.media_type, context="result.media_type")
        _require_positive_integer(self.byte_size, context="result.byte_size")
        _require_sha256(self.sha256, context="result.sha256")
        if (
            type(self.manifest_revision) is not int
            or not 1 <= self.manifest_revision <= MAX_SAFE_SOURCE_INTEGER
        ):
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "result.revision")
        if type(self.duplicate) is not DuplicateContentSignal or self.source_id in (
            self.duplicate.matching_source_ids
        ):
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "result.duplicate")


class SourceIntegrityStatus(StrEnum):
    """Concrete outcome of reading and hashing one committed case copy."""

    VERIFIED = "verified"
    MISSING = "missing"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class SourceIntegrityResult:
    """Path- and digest-free integrity outcome for one source record."""

    case_id: UUID
    source_id: UUID
    status: SourceIntegrityStatus

    def __post_init__(self) -> None:
        require_internal_id(self.case_id, context="integrity.case_id")
        require_internal_id(self.source_id, context="integrity.source_id")
        if self.case_id == self.source_id or type(self.status) is not SourceIntegrityStatus:
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "integrity.result")

    @property
    def safe_message(self) -> str:
        """Describe only checks actually performed, without storage details."""

        if self.status is SourceIntegrityStatus.VERIFIED:
            return "The case copy size and SHA-256 match its source record."
        if self.status is SourceIntegrityStatus.MISSING:
            return "The case copy is missing."
        return "The case copy no longer matches its source record."


class SourceProgressPhase(StrEnum):
    """Stable phases for cancelable ingest and integrity work."""

    COPYING = "copying"
    VERIFYING_COPY = "verifying_copy"
    INSPECTING = "inspecting"
    COMMITTING = "committing"
    VERIFYING_INTEGRITY = "verifying_integrity"


@dataclass(frozen=True, slots=True)
class SourceImportProgress:
    """Path-free monotonic progress within one operation phase."""

    operation_id: UUID
    phase: SourceProgressPhase
    completed_units: int
    total_units: int

    def __post_init__(self) -> None:
        require_internal_id(self.operation_id, context="progress.operation_id")
        if type(self.phase) is not SourceProgressPhase:
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "progress.phase")
        if (
            type(self.completed_units) is not int
            or type(self.total_units) is not int
            or not 1 <= self.total_units <= MAX_SAFE_SOURCE_INTEGER
            or not 0 <= self.completed_units <= self.total_units
        ):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "progress.units")


def _require_media_type(value: object, *, context: str) -> DetectedMediaType:
    if type(value) is not DetectedMediaType:
        reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, context)
    return value


def _require_positive_integer(value: object, *, context: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_SOURCE_INTEGER:
        reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, context)
    return value


def _require_sha256(value: object, *, context: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, context)
    return value
