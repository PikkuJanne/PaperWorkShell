"""PWS-0705 application-boundary checks independent of the Qt privacy curtain."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.assisted_use import AssistedUseService
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.package_workflow import PackageWorkflowService
from paperworkshell.domain.assisted_use import (
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseRecord,
    AssistedUseStatus,
)
from paperworkshell.domain.case_lifecycle import CaseLifecycleError, CaseLifecycleErrorCode
from tests.unit.test_organizer_backend import (
    CASE_ID,
    PAGE_IDS,
    _document,
    _Lifecycle,
    _Repository,
    _SourceIngest,
    _Thumbnails,
)

_PASSPHRASE = "synthetic local case phrase"


class _PolicyStore:
    def __init__(self) -> None:
        self.records: dict[UUID, AssistedUseRecord] = {}
        self.fail_save = False

    def load(self, case_id: UUID) -> AssistedUseRecord | None:
        return self.records.get(case_id)

    def save(self, record: AssistedUseRecord) -> None:
        if self.fail_save:
            raise OSError("synthetic private value must not cross the boundary")
        self.records[record.case_id] = record


def _harness(
    *,
    page_count: int = 3,
    close_resources: Callable[[], None] = lambda: None,
) -> tuple[
    OrganizerBackend, AssistedUseService, _PolicyStore, _Repository, _Lifecycle, _SourceIngest
]:
    repository = _Repository(_document(page_count=page_count))
    lifecycle = _Lifecycle(repository)
    ingest = _SourceIngest(repository)
    store = _PolicyStore()
    service = AssistedUseService(
        store,
        clock=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC),
        monotonic_clock=lambda: 100.0,
    )
    backend = OrganizerBackend(
        lifecycle,
        repository,
        ingest,
        _Thumbnails(),
        assisted_use=service,
        close_resources=close_resources,
    )
    return backend, service, store, repository, lifecycle, ingest


def test_direct_open_cannot_bypass_restart_unlock_or_load_manifest() -> None:
    backend, service, _store, repository, lifecycle, _ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    service.release()
    before = repository.load_calls
    with pytest.raises(AssistedUseError) as caught:
        backend.open_case(CASE_ID)
    assert caught.value.code is AssistedUseErrorCode.LOCKED
    assert repository.load_calls == before
    assert lifecycle.active_case is None
    assert backend.state.case_id is None
    service.unlock(CASE_ID, _PASSPHRASE)
    assert backend.open_case(CASE_ID).case_id == CASE_ID
    backend.close_case()


@pytest.mark.parametrize(
    "operation",
    (
        lambda backend: backend.open_case(CASE_ID),
        lambda backend: backend.rotate_clockwise(PAGE_IDS[0]),
        lambda backend: backend.remove_page(PAGE_IDS[0]),
        lambda backend: backend.move_page(PAGE_IDS[0], 2),
        lambda backend: backend.undo(),
        lambda backend: backend.redo(),
        lambda backend: backend.import_source(Path("synthetic.pdf"), "synthetic.pdf"),
        lambda backend: backend.get_thumbnails(100, 100, page_ids=(PAGE_IDS[0],)),
        lambda backend: backend.begin_package_review(),
        lambda backend: backend.begin_core_overlay_tools(PAGE_IDS[0]),
        lambda backend: backend.begin_redaction_tools(PAGE_IDS[0]),
    ),
)
def test_locked_document_operations_are_denied_before_work(
    operation: Callable[[OrganizerBackend], object],
) -> None:
    backend, service, _store, repository, _lifecycle, ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    service.lock()
    saved = repository.save_calls
    loaded = repository.load_calls
    with pytest.raises(AssistedUseError) as caught:
        operation(backend)
    assert caught.value.code is AssistedUseErrorCode.LOCKED
    assert repository.save_calls == saved
    assert repository.load_calls == loaded
    assert ingest.calls == 0
    backend.close_case()


def test_assistance_never_lists_unrelated_cases_even_while_locked() -> None:
    backend, service, _store, _repository, _lifecycle, _ingest = _harness()
    assert backend.list_cases().cases
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    assert backend.list_cases().cases == ()
    service.lock()
    assert backend.list_cases().cases == ()
    backend.close_case()
    assert service.snapshot is None
    assert backend.list_cases().cases


def test_locked_reopen_selection_stays_isolated_until_successful_close() -> None:
    backend, service, store, repository, _lifecycle, _ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    service.release()
    snapshot = service.inspect(CASE_ID)
    assert snapshot.status is AssistedUseStatus.LOCKED
    assert backend.list_cases().cases == ()
    before = repository.load_calls
    other = UUID("60000000-0000-4000-8000-000000000099")
    assert other not in store.records
    with pytest.raises(AssistedUseError) as caught:
        service.inspect(other)
    assert caught.value.code is AssistedUseErrorCode.CASE_CONFLICT
    assert service.snapshot == snapshot
    with pytest.raises(AssistedUseError) as caught:
        backend.open_case(other)
    assert caught.value.code is AssistedUseErrorCode.CASE_CONFLICT
    assert repository.load_calls == before
    store.fail_save = True
    with pytest.raises(AssistedUseError) as caught:
        backend.close_case()
    assert caught.value.code is AssistedUseErrorCode.STORAGE_FAILED
    assert service.snapshot == snapshot
    assert backend.list_cases().cases == ()
    store.fail_save = False
    backend.close_case()
    assert service.check() is None
    assert backend.list_cases().cases


@pytest.mark.parametrize("shutdown", (False, True))
def test_locked_close_and_shutdown_preserve_already_queued_autosave(shutdown: bool) -> None:
    resources_closed: list[bool] = []
    backend, service, _store, repository, lifecycle, _ingest = _harness(
        close_resources=lambda: resources_closed.append(True)
    )
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    edited = backend.rotate_clockwise(PAGE_IDS[0])
    assert edited.autosave_pending
    service.lock()
    if shutdown:
        backend.shutdown()
    else:
        backend.close_case()
    assert repository.save_calls == 1
    assert repository.current.revision == 1
    assert lifecycle.active_case is None
    assert service.snapshot is None
    assert backend.state.case_id is None
    assert resources_closed == ([True] if shutdown else [])


@pytest.mark.parametrize("consent,storage_failure", ((False, False), (True, True)))
def test_failed_enrollment_leaves_no_active_import_capability(
    consent: bool, storage_failure: bool
) -> None:
    backend, service, store, _repository, lifecycle, ingest = _harness(page_count=0)
    store.fail_save = storage_failure
    with pytest.raises(AssistedUseError) as caught:
        backend.create_assisted_case(7, _PASSPHRASE, consent=consent)
    assert caught.value.code is (
        AssistedUseErrorCode.STORAGE_FAILED
        if storage_failure
        else AssistedUseErrorCode.CONSENT_REQUIRED
    )
    assert "private" not in str(caught.value)
    assert service.snapshot is None
    assert lifecycle.active_case is None
    assert backend.state.case_id is None
    with pytest.raises(CaseLifecycleError) as inactive:
        backend.import_source(Path("synthetic.pdf"), "synthetic.pdf")
    assert inactive.value.code is CaseLifecycleErrorCode.NO_ACTIVE_CASE
    assert ingest.calls == 0


def test_named_assisted_case_keeps_its_name_and_identity_after_reopen() -> None:
    backend, service, _store, repository, _lifecycle, _ingest = _harness(page_count=0)
    state = backend.create_assisted_case(
        7, _PASSPHRASE, consent=True, case_name="Äänekoski assistance"
    )
    assert state.case_name == "Äänekoski assistance"
    assert repository.current.document["case_name"] == state.case_name
    case_id = state.case_id
    assert case_id is not None
    backend.close_case()
    service.unlock(case_id, _PASSPHRASE)
    reopened = backend.open_case(case_id)
    assert reopened.case_id == case_id
    assert reopened.case_name == state.case_name
    backend.close_case()


def test_locked_case_cannot_save_a_retained_package_summary() -> None:
    class _SavedSummary:
        calls = 0

        def save_summary(self, destination: Path, *, cancellation: object = None) -> None:
            del destination, cancellation
            self.calls += 1

    backend, service, _store, _repository, _lifecycle, _ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    retained = _SavedSummary()
    # Preserve the real organizer boundary while avoiding document output in this guard regression.
    backend._package_workflow = cast(PackageWorkflowService, retained)
    service.lock()
    with pytest.raises(AssistedUseError) as caught:
        backend.save_package_summary(Path("synthetic-summary.txt"))
    assert caught.value.code is AssistedUseErrorCode.LOCKED
    assert retained.calls == 0
    backend._package_workflow = None
    backend.close_case()


def test_failed_retention_checkpoint_cannot_select_or_expose_a_new_case() -> None:
    backend, service, store, _repository, _lifecycle, _ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    store.fail_save = True
    with pytest.raises(AssistedUseError):
        backend.close_case()
    assert service.snapshot is not None
    assert service.snapshot.status is AssistedUseStatus.LOCKED
    with pytest.raises(AssistedUseError) as caught:
        backend.create_case()
    assert caught.value.code in (AssistedUseErrorCode.CASE_CONFLICT, AssistedUseErrorCode.LOCKED)
    store.fail_save = False
    backend.close_case()
