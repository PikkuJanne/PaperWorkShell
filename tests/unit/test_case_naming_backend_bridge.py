"""The desktop receives unsaved rename intent before a recoverable save error."""

from __future__ import annotations

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker, UiFailure
from tests.accessibility.test_accessibility_baseline import _application
from tests.unit.test_assisted_organizer import _PASSPHRASE, _harness
from tests.unit.test_organizer_backend import _backend, _document


def test_failed_rename_publishes_pending_state_then_error_and_can_retry() -> None:
    _application()
    backend, repository, _lifecycle, _ingest, _thumbnails = _backend(_document())
    backend.create_case("Old synthetic name")
    worker = OrganizerBackendWorker(lambda: backend)
    worker._backend = backend
    states: list[OrganizerViewState] = []
    failures: list[UiFailure] = []
    events: list[str] = []

    def received_state(state: OrganizerViewState) -> None:
        states.append(state)
        events.append("state")

    def received_failure(failure: UiFailure) -> None:
        failures.append(failure)
        events.append("failure")

    worker.state_changed.connect(received_state)
    worker.failed.connect(received_failure)
    repository.fail_saves = 1

    worker.rename_case("New synthetic name")

    assert events == ["state", "failure"]
    assert states[-1].case_name == "New synthetic name"
    assert states[-1].autosave_pending
    assert failures[-1].code == "MANIFEST_SAVE_FAILED"
    assert repository.current.document["case_name"] == "Old synthetic name"
    worker.retry_autosave()
    saved_state = states.pop()
    assert saved_state.case_name == "New synthetic name"
    assert not saved_state.autosave_pending
    saved_document = repository.current.document
    assert saved_document["case_name"] == "New synthetic name"
    backend.close_case()


def test_named_assisted_signal_preserves_the_fourth_argument_through_qt() -> None:
    _application()
    backend, service, _store, repository, _lifecycle, _ingest = _harness(page_count=0)
    worker = OrganizerBackendWorker(lambda: backend)
    worker._backend = backend
    commands = OrganizerBackendCommands()
    commands.connect_worker(worker)
    states: list[OrganizerViewState] = []
    failures: list[UiFailure] = []
    worker.state_changed.connect(states.append)
    worker.failed.connect(failures.append)
    commands.create_named_assisted_case_requested.emit(7, _PASSPHRASE, True, "Named assistance")
    assert failures == []
    assert states[-1].case_name == "Named assistance"
    assert repository.current.document["case_name"] == "Named assistance"
    assert service.snapshot is not None
    backend.close_case()
