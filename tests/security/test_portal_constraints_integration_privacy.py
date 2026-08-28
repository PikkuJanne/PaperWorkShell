"""Privacy checks for portal-constraint failures crossing the Qt bridge."""

from __future__ import annotations

from typing import cast

from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.portal_constraints import (
    ConstraintEditorError,
    ConstraintEditorErrorCode,
)
from paperworkshell.preflight.models import PreflightError, PreflightErrorCode
from paperworkshell.ui.backend import OrganizerBackendWorker, UiFailure, sanitize_ui_failure

PRIVATE_CANARY = "PRIVATE_APPLICANT_PATH_C:\\Cases\\identity-document.pdf"


class _FailingConstraintBackend:
    def __init__(self, error: ConstraintEditorError) -> None:
        self._error = error

    def begin_portal_constraints(self) -> object:
        raise self._error


def test_constraint_worker_rebuilds_hostile_failure_before_emitting_it() -> None:
    error = ConstraintEditorError(
        ConstraintEditorErrorCode.DRAFT_INVALID,
        "constraints.begin",
    )
    error.context = PRIVATE_CANARY
    error.safe_message = PRIVATE_CANARY
    backend = _FailingConstraintBackend(error)
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    worker._backend = cast(OrganizerBackend, backend)
    failures: list[tuple[int, UiFailure]] = []
    worker.portal_constraints_start_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    worker.begin_portal_constraints(701)

    assert len(failures) == 1
    operation_id, failure = failures[0]
    assert operation_id == 701
    assert type(failure) is UiFailure
    assert failure.code == ConstraintEditorErrorCode.DRAFT_INVALID.value
    assert failure.operation_id == 701
    assert PRIVATE_CANARY not in failure.message
    assert PRIVATE_CANARY not in repr(failure)


def test_preflight_failure_is_rebuilt_before_ui_presentation() -> None:
    error = PreflightError(PreflightErrorCode.RULE_FAILED, "preflight.rule")
    error.safe_message = PRIVATE_CANARY

    failure = sanitize_ui_failure(error, operation_id=702)

    assert failure.code == PreflightErrorCode.RULE_FAILED.value
    assert failure.operation_id == 702
    assert PRIVATE_CANARY not in failure.message
    assert PRIVATE_CANARY not in repr(failure)
