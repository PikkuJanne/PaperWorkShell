from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import pytest

from paperworkshell.app.signed_source_policy import (
    SignedSourceConfirmationChoice,
    SignedSourceConfirmationOutcomeStatus,
    SignedSourceConfirmationService,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.signed_source_policy import (
    SIGNED_SOURCE_CONFIRMATION_CODE,
    SignedSourceModificationSource,
    SignedSourcePolicyError,
    SignedSourcePolicyErrorCode,
    SignedSourceReviewStatus,
    SignedSourceSignatureState,
    build_signed_source_modification_review,
    require_signed_source_modification_confirmation,
)

CASE_ID = UUID("27000000-0000-4000-8000-000000000001")
MARKER_SOURCE_ID = UUID("27000000-0000-4000-8000-000000000002")
UNKNOWN_SOURCE_ID = UUID("27000000-0000-4000-8000-000000000003")
PLAIN_SOURCE_ID = UUID("27000000-0000-4000-8000-000000000004")
CONFIRMATION_ID = UUID("27000000-0000-4000-8000-000000000005")
UNKNOWN_CONFIRMATION_ID = UUID("27000000-0000-4000-8000-000000000006")
NEW_CONFIRMATION_ID = UUID("27000000-0000-4000-8000-000000000007")
SECOND_NEW_CONFIRMATION_ID = UUID("27000000-0000-4000-8000-000000000008")
RECORDED_AT = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _confirmation(
    source_id: UUID,
    *,
    confirmation_id: UUID = CONFIRMATION_ID,
) -> dict[str, object]:
    return {
        "confirmation_id": str(confirmation_id),
        "confirmation_version": 1,
        "confirmation_code": SIGNED_SOURCE_CONFIRMATION_CODE,
        "statement_version": 1,
        "state": "confirmed",
        "actor": "user",
        "scope": {"kind": "source", "source_id": str(source_id)},
        "recorded_at": "2026-08-13T10:00:00Z",
    }


def _snapshot(
    confirmations: list[object],
    *,
    revision: int = 7,
    sha256: str = "a" * 64,
    cas_token: str = "signed-source-cas-0001",
) -> ManifestSnapshot:
    document: dict[str, object] = {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": revision,
        "updated_at": "2026-08-13T10:00:00Z",
        "confirmations": confirmations,
    }
    return ManifestSnapshot(
        CASE_ID,
        2,
        revision,
        document,
        sha256,
        cas_token,
    )


def _source(
    source_id: UUID,
    state: SignedSourceSignatureState,
) -> SignedSourceModificationSource:
    return SignedSourceModificationSource(source_id, state)


def _risky_sources() -> tuple[SignedSourceModificationSource, ...]:
    return (
        _source(MARKER_SOURCE_ID, SignedSourceSignatureState.MARKERS_DETECTED),
        _source(UNKNOWN_SOURCE_ID, SignedSourceSignatureState.UNKNOWN),
    )


def _invalid_record(kind: str) -> list[object]:
    record = _confirmation(MARKER_SOURCE_ID)
    if kind == "missing":
        return []
    if kind == "withdrawn":
        record["state"] = "withdrawn"
    elif kind == "wrong_statement":
        record["statement_version"] = 2
    elif kind == "wrong_code":
        record["confirmation_code"] = "another-user-confirmation"
    elif kind == "wrong_scope":
        record["scope"] = {"kind": "case"}
    elif kind == "wrong_actor":
        record["actor"] = "automation"
    elif kind == "extra_member":
        record["private_detail"] = "must not grant authority"
    elif kind == "boolean_version":
        record["confirmation_version"] = True
    elif kind == "invalid_timestamp":
        record["recorded_at"] = "2026-99-13T10:00:00Z"
    else:
        raise AssertionError(kind)
    return [record]


def test_review_is_not_required_for_sources_without_signature_risk() -> None:
    review = build_signed_source_modification_review(
        _snapshot([]),
        (
            _source(PLAIN_SOURCE_ID, SignedSourceSignatureState.NONE_DETECTED),
            _source(UNKNOWN_SOURCE_ID, SignedSourceSignatureState.NOT_APPLICABLE),
        ),
    )

    assert review.status is SignedSourceReviewStatus.NOT_REQUIRED
    assert review.pending_source_ids == ()


@pytest.mark.parametrize(
    "kind",
    (
        "missing",
        "withdrawn",
        "wrong_statement",
        "wrong_code",
        "wrong_scope",
        "wrong_actor",
        "extra_member",
        "boolean_version",
        "invalid_timestamp",
    ),
)
def test_missing_withdrawn_or_nonexact_confirmation_fails_closed(kind: str) -> None:
    snapshot = _snapshot(_invalid_record(kind))
    sources = (_source(MARKER_SOURCE_ID, SignedSourceSignatureState.MARKERS_DETECTED),)

    review = build_signed_source_modification_review(snapshot, sources)

    assert review.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED
    assert review.pending_source_ids == (MARKER_SOURCE_ID,)
    with pytest.raises(SignedSourcePolicyError) as raised:
        require_signed_source_modification_confirmation(snapshot, sources)
    assert raised.value.code is SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED


def test_every_marker_or_unknown_source_requires_its_own_exact_record() -> None:
    marker_only = _snapshot([_confirmation(MARKER_SOURCE_ID)])

    incomplete = build_signed_source_modification_review(marker_only, _risky_sources())

    assert incomplete.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED
    assert incomplete.pending_source_ids == (UNKNOWN_SOURCE_ID,)

    complete = _snapshot(
        [
            _confirmation(MARKER_SOURCE_ID),
            _confirmation(
                UNKNOWN_SOURCE_ID,
                confirmation_id=UNKNOWN_CONFIRMATION_ID,
            ),
        ]
    )
    review = require_signed_source_modification_confirmation(complete, _risky_sources())

    assert review.status is SignedSourceReviewStatus.CONFIRMED
    assert review.pending_source_ids == ()
    assert review.warning_title == "Create an unsigned derivative?"
    assert review.confirm_action == "Create unsigned derivative"
    assert review.cancel_action == "Cancel"
    assert review.result_label == "Unsigned derivative"
    assert "contains digital-signature markers" in review.warning_message
    assert "could not confirm" in review.warning_message
    assert "can invalidate that signature" in review.warning_message
    assert "does not validate digital signatures" in review.warning_message
    assert "original remains unchanged" in review.warning_message


def test_duplicate_source_confirmation_is_ambiguous_and_never_grants_authority() -> None:
    snapshot = _snapshot(
        [
            _confirmation(MARKER_SOURCE_ID),
            _confirmation(
                MARKER_SOURCE_ID,
                confirmation_id=UNKNOWN_CONFIRMATION_ID,
            ),
        ]
    )

    with pytest.raises(SignedSourcePolicyError) as raised:
        build_signed_source_modification_review(snapshot, _risky_sources()[:1])

    assert raised.value.code is SignedSourcePolicyErrorCode.CONFIRMATION_AMBIGUOUS


class _Repository:
    def __init__(
        self,
        snapshot: ManifestSnapshot,
        *,
        save_failure: str | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.save_failure = save_failure
        self.load_calls = 0
        self.save_calls = 0
        self.expected: tuple[int, str, str] | None = None

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.load_calls += 1
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, self.snapshot)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == CASE_ID
        self.save_calls += 1
        self.expected = (expected_revision, expected_sha256, expected_cas_token)
        if self.save_failure == "conflict":
            raise ManifestRepositoryError(
                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                "manifest.save",
            )
        if self.save_failure == "raw":
            raise RuntimeError("PRIVATE C:/cases/applicant.pdf")
        revision = expected_revision + 1
        self.snapshot = ManifestSnapshot(
            CASE_ID,
            2,
            revision,
            candidate,
            "b" * 64,
            "signed-source-cas-0002",
        )
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.snapshot)


def _service(repository: _Repository) -> SignedSourceConfirmationService:
    assert isinstance(repository, ManifestRepository)
    generated_ids = iter((NEW_CONFIRMATION_ID, SECOND_NEW_CONFIRMATION_ID))
    return SignedSourceConfirmationService(
        repository,
        clock=lambda: RECORDED_AT,
        id_factory=lambda: next(generated_ids),
    )


def test_confirmation_service_persists_exact_source_records_with_manifest_cas() -> None:
    repository = _Repository(_snapshot([]))
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources())

    outcome = service.decide(
        review,
        SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )

    assert outcome.status is SignedSourceConfirmationOutcomeStatus.CONFIRMED
    assert outcome.manifest_revision == 8
    assert repository.save_calls == 1
    assert repository.expected == (7, "a" * 64, "signed-source-cas-0001")
    records = repository.snapshot.document["confirmations"]
    assert type(records) is list
    assert len(records) == 2
    assert all(
        type(record) is dict
        and frozenset(record)
        == {
            "confirmation_id",
            "confirmation_version",
            "confirmation_code",
            "statement_version",
            "state",
            "actor",
            "scope",
            "recorded_at",
        }
        for record in records
    )
    assert service.review(CASE_ID, _risky_sources()).status is SignedSourceReviewStatus.CONFIRMED


def test_confirmation_service_replaces_a_withdrawn_record_after_explicit_choice() -> None:
    record = _confirmation(MARKER_SOURCE_ID)
    record["state"] = "withdrawn"
    repository = _Repository(_snapshot([record]))
    service = _service(repository)
    sources = _risky_sources()[:1]

    review = service.review(CASE_ID, sources)
    outcome = service.decide(
        review,
        SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )

    assert outcome.status is SignedSourceConfirmationOutcomeStatus.CONFIRMED
    records = repository.snapshot.document["confirmations"]
    assert type(records) is list
    assert len(records) == 1
    assert isinstance(records[0], Mapping)
    assert records[0]["confirmation_id"] == str(CONFIRMATION_ID)
    assert records[0]["state"] == "confirmed"


def test_already_confirmed_current_review_is_idempotent_without_save() -> None:
    repository = _Repository(_snapshot([_confirmation(MARKER_SOURCE_ID)]))
    service = _service(repository)
    sources = _risky_sources()[:1]
    review = service.review(CASE_ID, sources)

    outcome = service.decide(
        review,
        SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )

    assert review.status is SignedSourceReviewStatus.CONFIRMED
    assert outcome.status is SignedSourceConfirmationOutcomeStatus.ALREADY_CONFIRMED
    assert outcome.manifest_revision == 7
    assert repository.save_calls == 0


def test_replaying_presave_review_after_success_is_stale() -> None:
    repository = _Repository(_snapshot([]))
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])
    first = service.decide(
        review,
        SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )

    with pytest.raises(SignedSourcePolicyError) as raised:
        service.decide(
            review,
            SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
        )

    assert first.status is SignedSourceConfirmationOutcomeStatus.CONFIRMED
    assert raised.value.code is SignedSourcePolicyErrorCode.REVIEW_STALE
    assert repository.save_calls == 1


def test_cancel_is_review_bound_and_never_saves_confirmation() -> None:
    repository = _Repository(_snapshot([]))
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])

    outcome = service.decide(review, SignedSourceConfirmationChoice.CANCEL)

    assert outcome.status is SignedSourceConfirmationOutcomeStatus.CANCELLED
    assert outcome.manifest_revision == 7
    assert repository.save_calls == 0
    assert repository.snapshot.document["confirmations"] == []


def test_consent_returns_exact_save_snapshot_without_reloading_later_edits() -> None:
    class _ConcurrentRepository(_Repository):
        def save(
            self,
            case_id: UUID,
            candidate: Mapping[str, object],
            *,
            expected_revision: int,
            expected_sha256: str,
            expected_cas_token: str,
        ) -> ManifestSaveResult:
            committed = super().save(
                case_id,
                candidate,
                expected_revision=expected_revision,
                expected_sha256=expected_sha256,
                expected_cas_token=expected_cas_token,
            )
            newer = committed.snapshot.document
            newer["revision"] = committed.snapshot.revision + 1
            newer["unrelated_edit"] = "SYNTHETIC_CHANGE_AFTER_CONSENT"
            self.snapshot = ManifestSnapshot(
                CASE_ID, 2, 9, newer, "c" * 64, "signed-source-cas-0003"
            )
            return committed

    repository = _ConcurrentRepository(_snapshot([]))
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])
    outcome, saved = service.decide_with_snapshot(
        review, SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE
    )
    assert outcome.status is SignedSourceConfirmationOutcomeStatus.CONFIRMED
    assert outcome.manifest_revision == saved.revision == 8
    assert saved.sha256 == "b" * 64
    assert "unrelated_edit" not in saved.document
    assert repository.snapshot.revision == 9
    assert repository.load_calls == 2  # Initial review and pre-save authority only.


@pytest.mark.parametrize("cancel", (False, True))
def test_no_save_consent_paths_return_the_exact_current_snapshot(cancel: bool) -> None:
    original = _snapshot([] if cancel else [_confirmation(MARKER_SOURCE_ID)])
    repository = _Repository(original)
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])
    outcome, returned = service.decide_with_snapshot(
        review,
        SignedSourceConfirmationChoice.CANCEL
        if cancel
        else SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )
    assert returned is original
    assert repository.save_calls == 0
    assert outcome.status is (
        SignedSourceConfirmationOutcomeStatus.CANCELLED
        if cancel
        else SignedSourceConfirmationOutcomeStatus.ALREADY_CONFIRMED
    )


@pytest.mark.parametrize("choice", tuple(SignedSourceConfirmationChoice))
def test_changed_manifest_rejects_stale_confirm_or_cancel_before_save(
    choice: SignedSourceConfirmationChoice,
) -> None:
    repository = _Repository(_snapshot([]))
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])
    repository.snapshot = _snapshot(
        [],
        revision=8,
        sha256="c" * 64,
        cas_token="signed-source-cas-0003",
    )

    with pytest.raises(SignedSourcePolicyError) as raised:
        service.decide(review, choice)

    assert raised.value.code is SignedSourcePolicyErrorCode.REVIEW_STALE
    assert repository.save_calls == 0


def test_confirmation_cas_conflict_is_reported_as_a_stale_review() -> None:
    repository = _Repository(_snapshot([]), save_failure="conflict")
    service = _service(repository)
    review = service.review(CASE_ID, _risky_sources()[:1])

    with pytest.raises(SignedSourcePolicyError) as raised:
        service.decide(
            review,
            SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
        )

    assert raised.value.code is SignedSourcePolicyErrorCode.REVIEW_STALE
    assert repository.save_calls == 1
