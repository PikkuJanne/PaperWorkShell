from __future__ import annotations

import logging
from uuid import UUID

import pytest

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendWorker, UiFailure, sanitize_ui_failure
from paperworkshell.ui.window import (
    _PREVIEW_PIXMAP_BUDGET,
    _PREVIEW_REQUEST_CAP,
    _bounded_preview_rows,
)

PRIVATE_CANARY = "PRIVATE_APPLICANT_C:/records/original-name.pdf"


def _raise_private_error() -> OrganizerBackend:
    raise RuntimeError(PRIVATE_CANARY)


def test_unknown_backend_failure_never_crosses_ui_boundary_or_enters_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    worker = OrganizerBackendWorker(_raise_private_error)
    failures: list[UiFailure] = []
    worker.failed.connect(failures.append)

    worker.initialize()

    assert failures == [
        UiFailure(
            "PWS_UI_UNEXPECTED",
            "PaperWorkShell could not complete that action. Try again or restart the application.",
            True,
        )
    ]
    assert PRIVATE_CANARY not in repr(failures)
    assert PRIVATE_CANARY not in caplog.text


def test_path_free_organizer_state_repr_contains_only_internal_page_facts() -> None:
    state = OrganizerViewState(
        case_id=UUID("73000000-0000-4000-8000-000000000001"),
        revision=4,
        pages=(
            OrganizerPageView(
                UUID("73000000-0000-4000-8000-000000000002"),
                1,
                90,
                True,
            ),
        ),
        can_undo=True,
        can_redo=False,
        autosave_pending=True,
    )

    rendered = repr(state)
    assert PRIVATE_CANARY not in rendered
    assert "sha256" not in rendered.lower()
    assert "source_path" not in rendered.lower()


def test_sanitizer_does_not_invoke_unknown_exception_stringification() -> None:
    class _HostileError(RuntimeError):
        def __str__(self) -> str:
            raise AssertionError("unknown exceptions must never be stringified")

        def __repr__(self) -> str:
            raise AssertionError("unknown exceptions must never be represented")

    failure = sanitize_ui_failure(_HostileError(PRIVATE_CANARY))

    assert failure.code == "PWS_UI_UNEXPECTED"
    assert PRIVATE_CANARY not in failure.message


def test_hostile_large_page_view_cannot_expand_preview_memory_or_request_policy() -> None:
    selected_row = 999
    rows = _bounded_preview_rows(
        1_000,
        tuple(range(1_000)),
        selected_row,
    )

    assert rows == tuple(sorted(rows))
    assert selected_row in rows
    assert len(rows) <= _PREVIEW_PIXMAP_BUDGET
    assert _PREVIEW_REQUEST_CAP <= _PREVIEW_PIXMAP_BUDGET <= 16
