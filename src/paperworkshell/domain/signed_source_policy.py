"""Pure policy for modifying sources with digital-signature markers.

The policy consumes only path-free manifest facts.  It does not validate a
cryptographic signature and it never grants permission to OCR or rasterize a
marker-bearing page; it records only that the user accepted creation of an
explicitly unsigned derivative while the imported original remains unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)

SIGNED_SOURCE_CONFIRMATION_CODE: Final = "signed-source-unsigned-derivative"
SIGNED_SOURCE_CONFIRMATION_VERSION: Final = 1
SIGNED_SOURCE_STATEMENT_VERSION: Final = 1
SIGNED_SOURCE_WARNING_TITLE: Final = "Create an unsigned derivative?"
SIGNED_SOURCE_CONFIRM_ACTION: Final = "Create unsigned derivative"
SIGNED_SOURCE_CANCEL_ACTION: Final = "Cancel"
SIGNED_SOURCE_RESULT_LABEL: Final = "Unsigned derivative"

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_CAS_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_UTC_TIMESTAMP: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_CONFIRMATION_MEMBERS: Final = frozenset(
    {
        "confirmation_id",
        "confirmation_version",
        "confirmation_code",
        "statement_version",
        "state",
        "actor",
        "scope",
        "recorded_at",
    }
)


class SignedSourcePolicyErrorCode(StrEnum):
    """Closed registry of path-free signed-source policy failures."""

    MODEL_INVALID = "SIGNED_SOURCE_POLICY_MODEL_INVALID"
    MANIFEST_INVALID = "SIGNED_SOURCE_POLICY_MANIFEST_INVALID"
    CONFIRMATION_REQUIRED = "SIGNED_SOURCE_CONFIRMATION_REQUIRED"
    CONFIRMATION_AMBIGUOUS = "SIGNED_SOURCE_CONFIRMATION_AMBIGUOUS"
    REVIEW_STALE = "SIGNED_SOURCE_REVIEW_STALE"
    SAVE_FAILED = "SIGNED_SOURCE_CONFIRMATION_SAVE_FAILED"
    INTERNAL_ERROR = "SIGNED_SOURCE_POLICY_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[SignedSourcePolicyErrorCode, str]] = {
    SignedSourcePolicyErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid signed-source review data."
    ),
    SignedSourcePolicyErrorCode.MANIFEST_INVALID: (
        "The case record cannot be used for signed-source review."
    ),
    SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED: (
        "Review the digital-signature warning and confirm creation of an unsigned "
        "derivative before continuing."
    ),
    SignedSourcePolicyErrorCode.CONFIRMATION_AMBIGUOUS: (
        "The case contains conflicting signed-source confirmations and needs review."
    ),
    SignedSourcePolicyErrorCode.REVIEW_STALE: (
        "The case changed after the signed-source warning was shown. Review it again."
    ),
    SignedSourcePolicyErrorCode.SAVE_FAILED: (
        "PaperWorkShell could not save the signed-source confirmation safely."
    ),
    SignedSourcePolicyErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete signed-source review safely."
    ),
}


@dataclass(slots=True, repr=False)
class SignedSourcePolicyError(ValueError):
    """Stable failure with no filename, path, digest, or document content."""

    code: SignedSourcePolicyErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not SignedSourcePolicyErrorCode:
            self.code = SignedSourcePolicyErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "signed_source.policy"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"SignedSourcePolicyError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_signed_source_policy(code: SignedSourcePolicyErrorCode, context: str) -> Never:
    """Raise one sanitized failure without retaining an active raw exception."""

    error = SignedSourcePolicyError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class SignedSourceSignatureState(StrEnum):
    """Conservative signature-marker facts relevant to modification review."""

    NOT_APPLICABLE = "not_applicable"
    NONE_DETECTED = "none_detected"
    MARKERS_DETECTED = "markers_detected"
    UNKNOWN = "unknown"


class SignedSourceReviewStatus(StrEnum):
    """Whether selected source intent needs a user decision."""

    NOT_REQUIRED = "not_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class SignedSourceModificationSource:
    """One selected source's path-free signature-marker state."""

    source_id: UUID
    signature_state: SignedSourceSignatureState

    def __post_init__(self) -> None:
        if (
            type(self.source_id) is not UUID
            or self.source_id.version != 4
            or type(self.signature_state) is not SignedSourceSignatureState
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.source",
            )

    @property
    def requires_review(self) -> bool:
        return self.signature_state in {
            SignedSourceSignatureState.MARKERS_DETECTED,
            SignedSourceSignatureState.UNKNOWN,
        }


@dataclass(frozen=True, slots=True, repr=False)
class SignedSourceModificationReview:
    """A warning decision bound to one exact manifest snapshot."""

    case_id: UUID
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    manifest_cas_token: str = field(repr=False)
    sources: tuple[SignedSourceModificationSource, ...]
    pending_source_ids: tuple[UUID, ...]
    status: SignedSourceReviewStatus

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.manifest_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256) is None
            or type(self.manifest_cas_token) is not str
            or _CAS_TOKEN.fullmatch(self.manifest_cas_token) is None
            or type(self.sources) is not tuple
            or any(type(source) is not SignedSourceModificationSource for source in self.sources)
            or type(self.pending_source_ids) is not tuple
            or any(
                type(value) is not UUID or value.version != 4 for value in self.pending_source_ids
            )
            or type(self.status) is not SignedSourceReviewStatus
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.review",
            )
        source_ids = tuple(source.source_id for source in self.sources)
        risky_ids = tuple(source.source_id for source in self.sources if source.requires_review)
        if (
            len(set(source_ids)) != len(source_ids)
            or len(set(self.pending_source_ids)) != len(self.pending_source_ids)
            or any(value not in risky_ids for value in self.pending_source_ids)
            or (
                self.status is SignedSourceReviewStatus.NOT_REQUIRED
                and (risky_ids or self.pending_source_ids)
            )
            or (
                self.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED
                and not self.pending_source_ids
            )
            or (
                self.status is SignedSourceReviewStatus.CONFIRMED
                and (not risky_ids or self.pending_source_ids)
            )
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.review_state",
            )

    @property
    def warning_title(self) -> str:
        return SIGNED_SOURCE_WARNING_TITLE

    @property
    def confirm_action(self) -> str:
        return SIGNED_SOURCE_CONFIRM_ACTION

    @property
    def cancel_action(self) -> str:
        return SIGNED_SOURCE_CANCEL_ACTION

    @property
    def result_label(self) -> str:
        return SIGNED_SOURCE_RESULT_LABEL

    @property
    def warning_message(self) -> str:
        """Return exact limited wording for the authenticated marker facts."""

        marker = any(
            source.signature_state is SignedSourceSignatureState.MARKERS_DETECTED
            for source in self.sources
        )
        unknown = any(
            source.signature_state is SignedSourceSignatureState.UNKNOWN for source in self.sources
        )
        if marker and unknown:
            reason = (
                "At least one selected PDF contains digital-signature markers, and "
                "PaperWorkShell could not confirm the digital-signature-marker state of "
                "another selected PDF."
            )
        elif marker:
            reason = "At least one selected PDF contains digital-signature markers."
        else:
            reason = (
                "PaperWorkShell could not confirm the digital-signature-marker state of "
                "at least one selected PDF."
            )
        return (
            f"{reason} If a selected PDF has a digital signature, modifying or combining "
            "it can invalidate that signature. The result is labeled Unsigned derivative. "
            "PaperWorkShell does not validate digital signatures. The imported original "
            "remains unchanged and available."
        )

    def __repr__(self) -> str:
        return (
            "SignedSourceModificationReview("
            f"case_id={self.case_id!r}, manifest_revision={self.manifest_revision}, "
            f"source_count={len(self.sources)}, pending_count={len(self.pending_source_ids)}, "
            f"status={self.status!r})"
        )


def build_signed_source_modification_review(
    snapshot: ManifestSnapshot,
    sources: tuple[SignedSourceModificationSource, ...],
) -> SignedSourceModificationReview:
    """Evaluate exact source-scoped confirmation records in one manifest snapshot."""

    if (
        type(snapshot) is not ManifestSnapshot
        or type(sources) is not tuple
        or any(type(source) is not SignedSourceModificationSource for source in sources)
    ):
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.MODEL_INVALID,
            "signed_source.review_input",
        )
    source_ids = tuple(source.source_id for source in sources)
    if len(set(source_ids)) != len(source_ids):
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.MODEL_INVALID,
            "signed_source.review_sources",
        )
    risky = tuple(source for source in sources if source.requires_review)
    pending: list[UUID] = []
    if risky:
        confirmations = snapshot.document.get("confirmations")
        if type(confirmations) is not list:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmations",
            )
        for source in risky:
            matches = _matching_confirmation_records(confirmations, source.source_id)
            if len(matches) > 1:
                reject_signed_source_policy(
                    SignedSourcePolicyErrorCode.CONFIRMATION_AMBIGUOUS,
                    "signed_source.confirmation_duplicate",
                )
            if not matches or not _is_exact_confirmed_record(matches[0], source.source_id):
                pending.append(source.source_id)
    status = (
        SignedSourceReviewStatus.NOT_REQUIRED
        if not risky
        else (
            SignedSourceReviewStatus.CONFIRMATION_REQUIRED
            if pending
            else SignedSourceReviewStatus.CONFIRMED
        )
    )
    return SignedSourceModificationReview(
        case_id=snapshot.case_id,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        sources=sources,
        pending_source_ids=tuple(pending),
        status=status,
    )


def require_signed_source_modification_confirmation(
    snapshot: ManifestSnapshot,
    sources: tuple[SignedSourceModificationSource, ...],
) -> SignedSourceModificationReview:
    """Return the bound review only when all selected risky sources are confirmed."""

    review = build_signed_source_modification_review(snapshot, sources)
    if review.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED:
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED,
            "signed_source.confirmation_required",
        )
    return review


def _matching_confirmation_records(
    confirmations: list[object],
    source_id: UUID,
) -> tuple[Mapping[str, object], ...]:
    matches: list[Mapping[str, object]] = []
    for value in confirmations:
        if not isinstance(value, Mapping):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmation_record",
            )
        record = cast(Mapping[str, object], value)
        if record.get("confirmation_code") != SIGNED_SOURCE_CONFIRMATION_CODE:
            continue
        scope = record.get("scope")
        if not isinstance(scope, Mapping):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmation_scope",
            )
        if scope.get("kind") == "source" and scope.get("source_id") == str(source_id):
            matches.append(record)
    return tuple(matches)


def _is_exact_confirmed_record(record: Mapping[str, object], source_id: UUID) -> bool:
    scope = record.get("scope")
    confirmation_id = record.get("confirmation_id")
    recorded_at = record.get("recorded_at")
    try:
        parsed_confirmation_id = UUID(confirmation_id) if type(confirmation_id) is str else None
    except ValueError:
        parsed_confirmation_id = None
    return (
        frozenset(record) == _CONFIRMATION_MEMBERS
        and parsed_confirmation_id is not None
        and parsed_confirmation_id.version == 4
        and str(parsed_confirmation_id) == confirmation_id
        and _is_utc_timestamp(recorded_at)
        and type(record.get("confirmation_version")) is int
        and record.get("confirmation_version") == SIGNED_SOURCE_CONFIRMATION_VERSION
        and type(record.get("statement_version")) is int
        and record.get("statement_version") == SIGNED_SOURCE_STATEMENT_VERSION
        and record.get("state") == "confirmed"
        and record.get("actor") == "user"
        and isinstance(scope, Mapping)
        and set(scope) == {"kind", "source_id"}
        and scope.get("kind") == "source"
        and scope.get("source_id") == str(source_id)
    )


def _is_utc_timestamp(value: object) -> bool:
    if type(value) is not str or _UTC_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)
