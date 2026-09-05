"""Application service for review-bound signed-source confirmations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestLoadResult,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.signed_source_policy import (
    SIGNED_SOURCE_CONFIRMATION_CODE,
    SIGNED_SOURCE_CONFIRMATION_VERSION,
    SIGNED_SOURCE_STATEMENT_VERSION,
    SignedSourceModificationReview,
    SignedSourceModificationSource,
    SignedSourcePolicyErrorCode,
    SignedSourceReviewStatus,
    build_signed_source_modification_review,
    reject_signed_source_policy,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32


class SignedSourceConfirmationChoice(StrEnum):
    """Exact user action after the signed-source warning is presented."""

    CREATE_UNSIGNED_DERIVATIVE = "create_unsigned_derivative"
    CANCEL = "cancel"


class SignedSourceConfirmationOutcomeStatus(StrEnum):
    """Closed result of a review-bound user decision."""

    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SignedSourceConfirmationOutcome:
    """Path-free result that never disguises cancellation as confirmation."""

    status: SignedSourceConfirmationOutcomeStatus
    manifest_revision: int

    def __post_init__(self) -> None:
        if (
            type(self.status) is not SignedSourceConfirmationOutcomeStatus
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.outcome",
            )


class UtcClock(Protocol):
    def __call__(self) -> datetime: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SignedSourceConfirmationService:
    """Load, review, and atomically persist one explicit user decision."""

    __slots__ = ("_clock", "_id_factory", "_manifests")

    def __init__(
        self,
        manifests: ManifestRepository,
        *,
        clock: UtcClock = _utc_now,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(manifests, ManifestRepository)
            or not callable(clock)
            or not callable(id_factory)
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.service",
            )
        self._manifests = manifests
        self._clock = clock
        self._id_factory = id_factory

    def review(
        self,
        case_id: UUID,
        sources: tuple[SignedSourceModificationSource, ...],
    ) -> SignedSourceModificationReview:
        """Return a warning decision bound to the current manifest authority."""

        snapshot = self._load(case_id)
        return build_signed_source_modification_review(snapshot, sources)

    def decide(
        self,
        review: SignedSourceModificationReview,
        choice: SignedSourceConfirmationChoice,
    ) -> SignedSourceConfirmationOutcome:
        """Confirm by CAS or cancel without mutation after freshness validation."""

        outcome, _snapshot = self.decide_with_snapshot(review, choice)
        return outcome

    def decide_with_snapshot(
        self,
        review: SignedSourceModificationReview,
        choice: SignedSourceConfirmationChoice,
    ) -> tuple[SignedSourceConfirmationOutcome, ManifestSnapshot]:
        """Return the exact reviewed or committed snapshot without a second load.

        A workflow can rebind to its own consent-only commit, then check that
        authority is still current before any document processing. A later
        unrelated edit must never become silently authorized by the old review.
        """

        if (
            type(review) is not SignedSourceModificationReview
            or type(choice) is not SignedSourceConfirmationChoice
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.decision",
            )
        current = self._load(review.case_id)
        _require_review_binding(review, current)
        refreshed = build_signed_source_modification_review(current, review.sources)
        if choice is SignedSourceConfirmationChoice.CANCEL:
            return (
                SignedSourceConfirmationOutcome(
                    SignedSourceConfirmationOutcomeStatus.CANCELLED, current.revision
                ),
                current,
            )
        if refreshed.status is SignedSourceReviewStatus.NOT_REQUIRED:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.decision_not_required",
            )
        if refreshed.status is SignedSourceReviewStatus.CONFIRMED:
            return (
                SignedSourceConfirmationOutcome(
                    SignedSourceConfirmationOutcomeStatus.ALREADY_CONFIRMED, current.revision
                ),
                current,
            )
        if current.revision >= MAX_SAFE_MANIFEST_INTEGER:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.SAVE_FAILED,
                "signed_source.revision",
            )
        candidate = current.document
        confirmations = candidate.get("confirmations")
        if type(confirmations) is not list:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmations",
            )
        recorded_at = _next_recorded_at(candidate.get("updated_at"), self._clock)
        reserved = _collect_uuid_values(candidate)
        for source_id in refreshed.pending_source_ids:
            indexes = _matching_indexes(confirmations, source_id)
            if len(indexes) > 1:
                reject_signed_source_policy(
                    SignedSourcePolicyErrorCode.CONFIRMATION_AMBIGUOUS,
                    "signed_source.confirmation_duplicate",
                )
            if indexes:
                existing = confirmations[indexes[0]]
                if not isinstance(existing, Mapping):
                    reject_signed_source_policy(
                        SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                        "signed_source.confirmation_record",
                    )
                confirmation_id = existing.get("confirmation_id")
                if type(confirmation_id) is not str:
                    reject_signed_source_policy(
                        SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                        "signed_source.confirmation_id",
                    )
                confirmations[indexes[0]] = _confirmation_record(
                    confirmation_id,
                    source_id,
                    recorded_at,
                )
            else:
                confirmation_id = self._new_id(reserved)
                reserved.add(confirmation_id)
                confirmations.append(
                    _confirmation_record(str(confirmation_id), source_id, recorded_at)
                )
        candidate["revision"] = current.revision + 1
        candidate["updated_at"] = recorded_at
        saved = self._save(current, candidate)
        verified = build_signed_source_modification_review(saved, review.sources)
        if verified.status is not SignedSourceReviewStatus.CONFIRMED:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.SAVE_FAILED,
                "signed_source.save_result",
            )
        return (
            SignedSourceConfirmationOutcome(
                SignedSourceConfirmationOutcomeStatus.CONFIRMED, saved.revision
            ),
            saved,
        )

    def _load(self, case_id: UUID) -> ManifestSnapshot:
        if type(case_id) is not UUID or case_id.version != 4:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MODEL_INVALID,
                "signed_source.case_id",
            )
        try:
            loaded = self._manifests.load(case_id)
        except ManifestRepositoryError:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.load",
            )
        except BaseException:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.load",
            )
        if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.load_result",
            )
        return loaded.snapshot

    def _save(
        self,
        current: ManifestSnapshot,
        candidate: Mapping[str, object],
    ) -> ManifestSnapshot:
        try:
            result = self._manifests.save(
                current.case_id,
                candidate,
                expected_revision=current.revision,
                expected_sha256=current.sha256,
                expected_cas_token=current.cas_token,
            )
        except ManifestRepositoryError as error:
            if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
                reject_signed_source_policy(
                    SignedSourcePolicyErrorCode.REVIEW_STALE,
                    "signed_source.save_conflict",
                )
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.SAVE_FAILED,
                "signed_source.save",
            )
        except BaseException:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.SAVE_FAILED,
                "signed_source.save",
            )
        if (
            type(result) is not ManifestSaveResult
            or result.status is not ManifestSaveStatus.SAVED
            or result.snapshot.case_id != current.case_id
            or result.snapshot.revision != current.revision + 1
        ):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.SAVE_FAILED,
                "signed_source.save_result",
            )
        return result.snapshot

    def _new_id(self, reserved: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_signed_source_policy(
                    SignedSourcePolicyErrorCode.INTERNAL_ERROR,
                    "signed_source.identifier",
                )
            if type(value) is UUID and value.version == 4 and value not in reserved:
                return value
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.INTERNAL_ERROR,
            "signed_source.identifier",
        )


def _require_review_binding(
    review: SignedSourceModificationReview,
    snapshot: ManifestSnapshot,
) -> None:
    if (
        snapshot.case_id != review.case_id
        or snapshot.revision != review.manifest_revision
        or snapshot.sha256 != review.manifest_sha256
        or snapshot.cas_token != review.manifest_cas_token
    ):
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.REVIEW_STALE,
            "signed_source.review_binding",
        )


def _matching_indexes(confirmations: list[object], source_id: UUID) -> tuple[int, ...]:
    matches: list[int] = []
    for index, value in enumerate(confirmations):
        if not isinstance(value, Mapping):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmation_record",
            )
        if value.get("confirmation_code") != SIGNED_SOURCE_CONFIRMATION_CODE:
            continue
        scope = value.get("scope")
        if not isinstance(scope, Mapping):
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.MANIFEST_INVALID,
                "signed_source.confirmation_scope",
            )
        if scope.get("kind") == "source" and scope.get("source_id") == str(source_id):
            matches.append(index)
    return tuple(matches)


def _confirmation_record(
    confirmation_id: str,
    source_id: UUID,
    recorded_at: str,
) -> dict[str, object]:
    return {
        "confirmation_id": confirmation_id,
        "confirmation_version": SIGNED_SOURCE_CONFIRMATION_VERSION,
        "confirmation_code": SIGNED_SOURCE_CONFIRMATION_CODE,
        "statement_version": SIGNED_SOURCE_STATEMENT_VERSION,
        "state": "confirmed",
        "actor": "user",
        "scope": {"kind": "source", "source_id": str(source_id)},
        "recorded_at": recorded_at,
    }


def _next_recorded_at(value: object, clock: UtcClock) -> str:
    if type(value) is not str or not value.endswith("Z"):
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.MANIFEST_INVALID,
            "signed_source.updated_at",
        )
    try:
        prior = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.MANIFEST_INVALID,
            "signed_source.updated_at",
        )
    try:
        observed = clock()
    except BaseException:
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.INTERNAL_ERROR,
            "signed_source.clock",
        )
    if (
        prior.tzinfo is None
        or prior.utcoffset() is None
        or type(observed) is not datetime
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        reject_signed_source_policy(
            SignedSourcePolicyErrorCode.INTERNAL_ERROR,
            "signed_source.clock",
        )
    selected = observed.astimezone(UTC)
    if selected <= prior:
        try:
            selected = prior + timedelta(microseconds=1)
        except OverflowError:
            reject_signed_source_policy(
                SignedSourcePolicyErrorCode.INTERNAL_ERROR,
                "signed_source.clock",
            )
    return selected.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _collect_uuid_values(document: Mapping[str, object]) -> set[UUID]:
    result: set[UUID] = set()
    pending: list[object] = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            pending.extend(value.values())
        elif type(value) is list:
            pending.extend(value)
        elif type(value) is str:
            try:
                parsed = UUID(value)
            except ValueError:
                continue
            if parsed.version == 4 and str(parsed) == value:
                result.add(parsed)
    return result
