from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestAutosaveResult,
    ManifestAutosaveStatus,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
    copy_manifest_document,
)


def _case_id() -> UUID:
    return UUID("91000000-0000-4000-8000-000000000001")


def _document(*, revision: int = 0, edit: str = "initial") -> dict[str, object]:
    return {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "case_id": str(_case_id()),
        "revision": revision,
        "created_at": "2026-08-03T10:00:00.000000Z",
        "updated_at": "2026-08-03T10:00:00.000000Z",
        "edit": edit,
        "nested": {"values": [edit]},
    }


def _snapshot(document: Mapping[str, object]) -> ManifestSnapshot:
    revision = cast(int, document["revision"])
    return ManifestSnapshot(
        _case_id(),
        CURRENT_MANIFEST_SCHEMA_VERSION,
        revision,
        document,
        f"{revision + 1:064x}",
        f"cas_token_{revision:016d}",
    )


class _Repository:
    def __init__(self) -> None:
        self.current = _snapshot(_document())
        self.load_calls = 0
        self.save_calls: list[dict[str, object]] = []
        self.fail_next: ManifestRepositoryError | None = None
        self.load_error: ManifestRepositoryError | None = None
        self.first_save_started: Event | None = None
        self.release_first_save: Event | None = None

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == _case_id()
        self.load_calls += 1
        if self.load_error is not None:
            raise self.load_error
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, self.current)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == _case_id()
        assert expected_revision == self.current.revision
        assert expected_sha256 == self.current.sha256
        assert expected_cas_token == self.current.cas_token
        if self.first_save_started is not None and not self.save_calls:
            self.first_save_started.set()
            assert self.release_first_save is not None
            assert self.release_first_save.wait(timeout=5)
        frozen = copy_manifest_document(candidate)
        self.save_calls.append(frozen)
        if self.fail_next is not None:
            error = self.fail_next
            self.fail_next = None
            raise error
        self.current = _snapshot(frozen)
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.current)


class _UtcSequence:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> datetime:
        value = self._values.pop(0) if self._values else self._last
        self._last = value
        return value


def test_many_queued_edits_coalesce_to_one_final_durable_save() -> None:
    repository = _Repository()
    service = ManifestAutosaveService(
        repository,
        _case_id(),
        utc_now=lambda: datetime(2026, 8, 3, 10, 1, tzinfo=UTC),
    )

    for number in range(1, 101):
        generation = service.queue(_document(edit=f"edit-{number}"))
    result = service.flush()

    assert generation == 100
    assert result.status is ManifestAutosaveStatus.SAVED
    assert result.saved_generation == 100
    assert len(repository.save_calls) == 1
    assert repository.save_calls[0]["edit"] == "edit-100"
    assert repository.save_calls[0]["revision"] == 1
    assert not service.pending


def test_caller_mutation_after_queue_cannot_change_frozen_candidate() -> None:
    repository = _Repository()
    service = ManifestAutosaveService(repository, _case_id())
    candidate = _document(edit="frozen")

    service.queue(candidate)
    candidate["edit"] = "mutated"
    cast(dict[str, object], candidate["nested"])["values"] = ["mutated"]
    service.flush()

    saved = repository.save_calls[0]
    assert saved["edit"] == "frozen"
    assert saved["nested"] == {"values": ["frozen"]}


def test_edit_arriving_during_blocked_save_is_saved_by_same_flush() -> None:
    repository = _Repository()
    repository.first_save_started = Event()
    repository.release_first_save = Event()
    service = ManifestAutosaveService(repository, _case_id())
    service.queue(_document(edit="first"))
    outcome: list[ManifestAutosaveResult] = []
    errors: list[BaseException] = []

    def flush() -> None:
        try:
            outcome.append(service.flush())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=flush)
    thread.start()
    assert repository.first_save_started.wait(timeout=5)
    second_generation = service.queue(_document(edit="second"))
    repository.release_first_save.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert not errors
    assert [saved["edit"] for saved in repository.save_calls] == ["first", "second"]
    assert outcome[0].saved_generation == second_generation
    assert repository.current.document["edit"] == "second"


def test_failed_save_keeps_latest_edit_pending_for_explicit_retry() -> None:
    repository = _Repository()
    repository.fail_next = ManifestRepositoryError(
        ManifestRepositoryErrorCode.SAVE_FAILED,
        "repository.write",
    )
    service = ManifestAutosaveService(repository, _case_id())
    generation = service.queue(_document(edit="retry-me"))

    with pytest.raises(ManifestRepositoryError) as raised:
        service.flush()

    assert raised.value.code is ManifestRepositoryErrorCode.SAVE_FAILED
    assert service.pending
    result = service.flush()
    assert result.saved_generation == generation
    assert repository.current.document["edit"] == "retry-me"
    assert not service.pending


def test_future_candidate_version_fails_before_repository_mutation() -> None:
    repository = _Repository()
    service = ManifestAutosaveService(repository, _case_id())
    future = _document(edit="future")
    future["schema_version"] = CURRENT_MANIFEST_SCHEMA_VERSION + 1
    service.queue(future)

    with pytest.raises(ManifestRepositoryError) as raised:
        service.flush()

    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert repository.save_calls == []
    assert repository.current.revision == 0
    assert service.pending


def test_future_version_load_error_never_attempts_overwrite() -> None:
    repository = _Repository()
    repository.load_error = ManifestRepositoryError(
        ManifestRepositoryErrorCode.VERSION_UNSUPPORTED,
        "repository.manifest_version",
    )
    service = ManifestAutosaveService(repository, _case_id())
    service.queue(_document(edit="local-edit"))

    with pytest.raises(ManifestRepositoryError) as raised:
        service.flush()

    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert repository.save_calls == []
    assert service.pending


def test_first_flush_rejects_stale_full_document_instead_of_erasing_newer_state() -> None:
    repository = _Repository()
    service = ManifestAutosaveService(repository, _case_id())
    service.queue(_document(revision=0, edit="local-edit"))
    repository.current = _snapshot(_document(revision=1, edit="external-edit"))

    with pytest.raises(ManifestRepositoryError) as raised:
        service.flush()

    assert raised.value.code is ManifestRepositoryErrorCode.REVISION_CONFLICT
    assert repository.save_calls == []
    assert repository.current.document["edit"] == "external-edit"
    assert service.pending


def test_metadata_is_rebased_and_updated_at_remains_monotonic_when_clock_regresses() -> None:
    repository = _Repository()
    clock = _UtcSequence(
        datetime(2026, 8, 3, 10, 2, tzinfo=UTC),
        datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
    )
    service = ManifestAutosaveService(repository, _case_id(), utc_now=clock)
    first = _document(edit="first")
    first.update(
        {
            "case_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "revision": 0,
            "created_at": "2099-01-01T00:00:00Z",
            "updated_at": "2099-01-01T00:00:00Z",
        }
    )
    service.queue(first)
    service.flush()
    service.queue(_document(revision=1, edit="second"))
    service.flush()

    first_saved, second_saved = repository.save_calls
    assert first_saved["case_id"] == str(_case_id())
    assert first_saved["revision"] == 1
    assert first_saved["created_at"] == "2026-08-03T10:00:00.000000Z"
    assert first_saved["updated_at"] == "2026-08-03T10:02:00.000000Z"
    assert second_saved["revision"] == 2
    assert second_saved["updated_at"] == "2026-08-03T10:02:00.000001Z"


def test_callbacks_run_after_locks_are_released() -> None:
    repository = _Repository()
    callback_observations: list[ManifestAutosaveStatus] = []
    service: ManifestAutosaveService

    def on_saved(result: ManifestAutosaveResult) -> None:
        callback_observations.append(result.status)
        nested = service.flush()
        callback_observations.append(nested.status)

    service = ManifestAutosaveService(repository, _case_id(), on_saved=on_saved)
    service.queue(_document(edit="callback"))

    result = service.flush()

    assert result.status is ManifestAutosaveStatus.SAVED
    assert callback_observations == [
        ManifestAutosaveStatus.SAVED,
        ManifestAutosaveStatus.NO_PENDING,
    ]


def test_edit_during_post_save_notification_advances_only_across_owned_revision() -> None:
    repository = _Repository()
    callback_started = Event()
    release_callback = Event()

    def on_saved(_result: ManifestAutosaveResult) -> None:
        callback_started.set()
        assert release_callback.wait(timeout=5)

    service = ManifestAutosaveService(repository, _case_id(), on_saved=on_saved)
    service.queue(_document(revision=0, edit="first"))
    outcomes: list[ManifestAutosaveResult] = []
    errors: list[BaseException] = []

    def flush() -> None:
        try:
            outcomes.append(service.flush())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=flush)
    thread.start()
    assert callback_started.wait(timeout=5)
    generation = service.queue(_document(revision=0, edit="after-save"))
    release_callback.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert not errors
    assert outcomes[0].status is ManifestAutosaveStatus.SAVED
    second = service.flush()
    assert second.saved_generation == generation
    assert repository.current.revision == 2
    assert repository.current.document["edit"] == "after-save"

    with pytest.raises(ManifestRepositoryError) as stale:
        service.queue(_document(revision=0, edit="stale-after-notification"))
    assert stale.value.code is ManifestRepositoryErrorCode.REVISION_CONFLICT


def test_stale_edit_cannot_jump_across_overlapping_owned_save_transitions() -> None:
    repository = _Repository()
    first_callback_started = Event()
    second_callback_started = Event()
    release_callbacks = Event()
    callback_lock = Lock()
    callback_count = 0

    def on_saved(_result: ManifestAutosaveResult) -> None:
        nonlocal callback_count
        with callback_lock:
            callback_count += 1
            callback_number = callback_count
        (first_callback_started if callback_number == 1 else second_callback_started).set()
        assert release_callbacks.wait(timeout=5)

    service = ManifestAutosaveService(repository, _case_id(), on_saved=on_saved)
    service.queue(_document(revision=0, edit="first"))
    errors: list[BaseException] = []

    def flush() -> None:
        try:
            service.flush()
        except BaseException as error:
            errors.append(error)

    first_flush = Thread(target=flush)
    first_flush.start()
    assert first_callback_started.wait(timeout=5)
    service.queue(_document(revision=0, edit="second"))

    second_flush = Thread(target=flush)
    second_flush.start()
    assert second_callback_started.wait(timeout=5)
    with pytest.raises(ManifestRepositoryError) as stale:
        service.queue(_document(revision=0, edit="stale-jump"))

    release_callbacks.set()
    first_flush.join(timeout=5)
    second_flush.join(timeout=5)
    assert not first_flush.is_alive()
    assert not second_flush.is_alive()
    assert not errors
    assert stale.value.code is ManifestRepositoryErrorCode.REVISION_CONFLICT
    assert repository.current.revision == 2
    assert repository.current.document["edit"] == "second"


def test_close_drains_latest_edit_then_rejects_new_queue_without_echoing_candidate() -> None:
    repository = _Repository()
    service = ManifestAutosaveService(repository, _case_id())
    service.queue(_document(edit="one"))
    service.queue(_document(edit="final"))

    result = service.close()

    assert result.status is ManifestAutosaveStatus.CLOSED
    assert result.snapshot is repository.current
    assert repository.current.document["edit"] == "final"
    assert service.closed
    assert not service.pending
    canary = "private-final-edit"
    with pytest.raises(ManifestRepositoryError) as raised:
        service.queue(_document(edit=canary))
    assert raised.value.code is ManifestRepositoryErrorCode.AUTOSAVE_CLOSED
    assert canary not in repr(raised.value)
    assert service.close().status is ManifestAutosaveStatus.CLOSED


def test_close_failure_reopens_service_with_pending_edit_for_retry() -> None:
    repository = _Repository()
    repository.fail_next = ManifestRepositoryError(
        ManifestRepositoryErrorCode.CAPACITY_EXCEEDED,
        "repository.capacity",
    )
    service = ManifestAutosaveService(repository, _case_id())
    service.queue(_document(edit="retry-close"))

    with pytest.raises(ManifestRepositoryError):
        service.close()

    assert not service.closed
    assert service.pending
    assert service.close().status is ManifestAutosaveStatus.CLOSED
    assert repository.current.document["edit"] == "retry-close"
