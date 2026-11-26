"""Focused Qt bridge contracts for the revision-bound constraint editor."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.portal_constraints import (
    ConstraintDraftInput,
    ConstraintEditorState,
    PortalConstraintWorkflowService,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestSnapshot,
)
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)

CASE_ID = UUID("74000000-0000-4000-8000-000000000001")
EDITOR_ID = UUID("75000000-0000-4000-8000-000000000001")


def _opening_state() -> ConstraintEditorState:
    document: dict[str, object] = {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:01:00Z",
        "template_ref": None,
        "sources": [],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "redaction_marks": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    snapshot = ManifestSnapshot(
        CASE_ID, CURRENT_MANIFEST_SCHEMA_VERSION, 7, document, "a" * 64, "b" * 64
    )
    workflow = PortalConstraintWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=lambda: EDITOR_ID,
    )
    return workflow.begin(snapshot)


def _edited(opening: ConstraintEditorState) -> ConstraintEditorState:
    constraints = opening.constraints
    return opening.replace(
        ConstraintDraftInput(
            (PortalOutputMode.COMBINED_PDF,),
            constraints.encryption_policy,
            1,
            None,
            None,
            20,
            ("packet",),
        )
    )


class _Backend:
    startup_recovery_result = StartupRecoveryResult()

    def __init__(self) -> None:
        self.opening = _opening_state()
        self.state = OrganizerViewState(CASE_ID, 7, (), False, False, False)
        self.begin_result: object = self.opening
        self.apply_result = OrganizerViewState(CASE_ID, 8, (), False, False, False)
        self.begin_error: BaseException | None = None
        self.apply_error: BaseException | None = None
        self.cancel_error: BaseException | None = None
        self.calls: list[tuple[str, object | None]] = []

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def begin_portal_constraints(self) -> ConstraintEditorState:
        self.calls.append(("begin", None))
        if self.begin_error is not None:
            raise self.begin_error
        return cast(ConstraintEditorState, self.begin_result)

    def apply_portal_constraints(self, state: ConstraintEditorState) -> OrganizerViewState:
        self.calls.append(("apply", state))
        if self.apply_error is not None:
            raise self.apply_error
        self.state = self.apply_result
        return self.apply_result

    def cancel_portal_constraints(self, state: ConstraintEditorState) -> None:
        self.calls.append(("cancel", state))
        if self.cancel_error is not None:
            raise self.cancel_error

    def abort_portal_constraints(self) -> None:
        self.calls.append(("abort", None))


def _bridge(backend: _Backend) -> tuple[OrganizerBackendCommands, OrganizerBackendWorker]:
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    worker.initialize()
    return commands, worker


def test_bridge_routes_correlated_begin_apply_cancel_and_abort() -> None:
    backend = _Backend()
    commands, worker = _bridge(backend)
    events: list[tuple[str, int | OrganizerViewState]] = []
    cancelled: list[int] = []
    worker.state_changed.connect(lambda state: events.append(("state", state)))
    worker.portal_constraints_started.connect(
        lambda operation_id, _state: events.append(("started", operation_id))
    )
    worker.portal_constraints_applied.connect(
        lambda operation_id, _state: events.append(("applied", operation_id))
    )
    worker.portal_constraints_cancelled.connect(cancelled.append)

    edited = _edited(backend.opening)
    commands.portal_constraints_begin_requested.emit(31)
    commands.portal_constraints_apply_requested.emit(32, edited)
    commands.portal_constraints_cancel_requested.emit(33, edited)
    commands.portal_constraints_abort_requested.emit(34)

    assert events == [
        ("state", OrganizerViewState(CASE_ID, 7, (), False, False, False)),
        ("started", 31),
        ("state", OrganizerViewState(CASE_ID, 8, (), False, False, False)),
        ("applied", 32),
    ]
    assert cancelled == [33, 34]
    assert backend.calls == [
        ("begin", None),
        ("apply", edited),
        ("cancel", edited),
        ("abort", None),
    ]


def test_bridge_rejects_uncorrelated_backend_result_without_publishing_state() -> None:
    backend = _Backend()
    backend.apply_result = OrganizerViewState(CASE_ID, 9, (), False, False, False)
    commands, worker = _bridge(backend)
    published: list[OrganizerViewState] = []
    applied: list[object] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.state_changed.connect(published.append)
    worker.portal_constraints_applied.connect(lambda *values: applied.append(values))
    worker.portal_constraints_apply_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.portal_constraints_apply_requested.emit(35, _edited(backend.opening))

    assert published == []
    assert applied == []
    assert len(failures) == 1
    assert failures[0][0] == 35
    assert failures[0][1].code == "PWS_UI_UNEXPECTED"
    assert failures[0][1].operation_id == 35


def test_bridge_rejects_wrong_request_types_before_backend_dispatch() -> None:
    backend = _Backend()
    commands, worker = _bridge(backend)
    failures: list[tuple[int, UiFailure]] = []
    worker.portal_constraints_apply_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.portal_constraints_apply_requested.emit(36, "private-untrusted-draft")

    assert backend.calls == []
    assert len(failures) == 1
    assert failures[0][1].code == "PWS_UI_REQUEST_INVALID"
    assert "private-untrusted-draft" not in repr(failures)
