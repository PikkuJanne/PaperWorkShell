"""Privacy regressions for the PWS-0304 core visual-form-mark workflow."""

from __future__ import annotations

import json
import logging
import os
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QListWidget

from paperworkshell.app.core_overlay_tools import (
    CoreOverlayToolsState,
    core_overlay_record_summary,
)
from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
)
from paperworkshell.overlays.commands import OverlayCommandHistory
from paperworkshell.overlays.models import (
    MAX_OVERLAY_TEXT_CODE_POINTS,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    TextualOverlayPayload,
)
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"

_PRIVATE_VALUES: dict[OverlayKind, str] = {
    OverlayKind.TEXT: (
        "TEXT-FORM-CANARY-0304 $(synthetic-command); & calc.exe | "
        "<script>privateText()</script> C:\\Private Applicant\\text-form.pdf "
        "https://private.invalid/text?applicant=0304"
    ),
    OverlayKind.DATE: (
        "DATE-FORM-CANARY-0304 $(synthetic-command); & calc.exe | "
        "<script>privateDate()</script> C:\\Private Applicant\\date-form.pdf "
        "https://private.invalid/date?applicant=0304"
    ),
    OverlayKind.INITIALS: (
        "INITIALS-FORM-CANARY-0304 $(synthetic-command); & calc.exe | "
        "<script>privateInitials()</script> C:\\Private Applicant\\initials-form.pdf "
        "https://private.invalid/initials?applicant=0304"
    ),
}
_PRIVATE_FRAGMENTS = (
    "TEXT-FORM-CANARY-0304",
    "DATE-FORM-CANARY-0304",
    "INITIALS-FORM-CANARY-0304",
    "synthetic-command",
    "calc.exe",
    "<script>",
    "Private Applicant",
    "private.invalid",
    "applicant=0304",
)
_CREATED_IDS: dict[OverlayKind, UUID] = {
    OverlayKind.TEXT: UUID("6f040000-0000-4000-8000-000000000001"),
    OverlayKind.DATE: UUID("6f040000-0000-4000-8000-000000000002"),
    OverlayKind.INITIALS: UUID("6f040000-0000-4000-8000-000000000003"),
}


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    document = cast(dict[str, object], loaded)
    document["assets"] = []
    document["overlays"] = []
    return document


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    case_id = selected["case_id"]
    revision = selected["revision"]
    assert type(case_id) is str and type(revision) is int
    return ManifestSnapshot(
        UUID(case_id),
        2,
        revision,
        selected,
        "c" * 64,
        "synthetic-core-overlay-privacy-token",
    )


def _page_id(snapshot: ManifestSnapshot) -> UUID:
    sequence = snapshot.document["page_sequence"]
    assert type(sequence) is list and sequence and type(sequence[0]) is str
    return UUID(sequence[0])


def _created_state(kind: OverlayKind) -> tuple[ManifestSnapshot, CoreOverlayToolsState]:
    snapshot = _snapshot()
    value = _PRIVATE_VALUES[kind]
    created = CoreOverlayToolsState.from_snapshot(snapshot, _page_id(snapshot)).create(
        kind,
        value=value,
        id_factory=lambda: _CREATED_IDS[kind],
    )
    return snapshot, created


def _assert_private_absent(rendered: str) -> None:
    for fragment in _PRIVATE_FRAGMENTS:
        assert fragment not in rendered


def _render_error(error: OverlayError) -> str:
    return "\n".join(
        (
            repr(error),
            str(error),
            repr(error.args),
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        )
    )


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog(state: CoreOverlayToolsState) -> Iterator[CoreOverlayToolsDialog]:
    app = _application()
    dialog = CoreOverlayToolsDialog(state)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("kind", tuple(_PRIVATE_VALUES))
def test_private_textual_values_round_trip_as_inert_manifest_data_only(
    kind: OverlayKind,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    snapshot, state = _created_state(kind)
    value = _PRIVATE_VALUES[kind]
    record = state.selected_record
    assert record is not None and type(record.payload) is TextualOverlayPayload
    assert record.payload.value == value

    undone = state.undo()
    assert undone.records == ()
    redone = undone.redo()
    restored = redone.records[0]
    assert type(restored.payload) is TextualOverlayPayload
    assert restored.payload.value == value

    history_candidate = redone.history.to_candidate(snapshot)
    state_candidate = redone.to_candidate(snapshot)
    assert history_candidate == state_candidate
    overlays = state_candidate["overlays"]
    assert type(overlays) is list and len(overlays) == 1
    wire_payload = overlays[0]["payload"]
    assert type(wire_payload) is dict
    assert wire_payload["value"] == value

    reopened_history = OverlayCommandHistory.from_snapshot(_snapshot(state_candidate))
    reopened_payload = reopened_history.overlays[0].payload
    assert type(reopened_payload) is TextualOverlayPayload
    assert reopened_payload.value == value

    with pytest.raises(OverlayError) as raised:
        redone.select(UUID("6f040000-0000-4000-8000-000000000099"))
    assert raised.value.code is OverlayErrorCode.RECORD_UNKNOWN
    summary = core_overlay_record_summary(restored, 1, 1)
    safe_surfaces = "\n".join(
        (
            repr(state),
            str(state),
            repr(redone),
            str(redone),
            repr(redone.history),
            str(redone.history),
            repr(redone.history.overlays),
            repr(restored),
            str(restored),
            summary,
            redone.announcement,
            _render_error(raised.value),
        )
    )
    _assert_private_absent(safe_surfaces)

    logging.getLogger("paperworkshell.tests.core_overlay_privacy").debug(
        "state=%r history=%r record=%r summary=%s announcement=%s error=%r",
        redone,
        redone.history,
        restored,
        summary,
        redone.announcement,
        raised.value,
    )
    captured = capsys.readouterr()
    _assert_private_absent(f"{caplog.text}\n{captured.out}\n{captured.err}")


@pytest.mark.parametrize("kind", tuple(_PRIVATE_VALUES))
def test_dialog_summaries_status_and_accessibility_announcements_hide_values(
    kind: OverlayKind,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    _snapshot_value, state = _created_state(kind)
    announcements: list[str] = []
    original_set_status = CoreOverlayToolsDialog._set_status

    def capture_status(
        dialog: CoreOverlayToolsDialog,
        message: str,
        *,
        announce: bool,
        assertive: bool = False,
    ) -> None:
        if announce:
            announcements.append(message)
        original_set_status(
            dialog,
            message,
            announce=announce,
            assertive=assertive,
        )

    monkeypatch.setattr(CoreOverlayToolsDialog, "_set_status", capture_status)
    with _dialog(state) as dialog:
        # Publishing the value-bearing state announces only its fixed operation summary.
        dialog._publish_state(state)
        overlay_list = dialog.findChild(QListWidget, "coreOverlayList")
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert overlay_list is not None and status is not None
        assert overlay_list.count() == 1
        item = overlay_list.item(0)
        exposed_summary = " ".join(
            (
                item.text(),
                str(item.data(Qt.ItemDataRole.AccessibleTextRole)),
                str(item.data(Qt.ItemDataRole.AccessibleDescriptionRole)),
            )
        )

        # A hostile value supplied to a mark is rejected with a closed status/announcement.
        dialog._transition(
            lambda current: current.create(
                OverlayKind.CHECKMARK,
                value=_PRIVATE_VALUES[kind],
            )
        )
        exposed_status = " ".join(
            (
                status.text(),
                status.accessibleName(),
                status.accessibleDescription(),
            )
        )
        logging.getLogger("paperworkshell.tests.core_overlay_dialog_privacy").debug(
            "dialog-state=%r summary=%s status=%s announcements=%r",
            dialog.state,
            exposed_summary,
            exposed_status,
            announcements,
        )

    captured = capsys.readouterr()
    rendered = "\n".join(
        (
            exposed_summary,
            exposed_status,
            repr(announcements),
            caplog.text,
            captured.out,
            captured.err,
        )
    )
    _assert_private_absent(rendered)


def test_oversize_and_wrong_kind_failures_are_stable_and_content_free(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    snapshot = _snapshot()
    state = CoreOverlayToolsState.from_snapshot(snapshot, _page_id(snapshot))
    private = _PRIVATE_VALUES[OverlayKind.TEXT]
    oversize = private + ("X" * (MAX_OVERLAY_TEXT_CODE_POINTS + 1))
    failures: list[OverlayError] = []

    checks: tuple[tuple[OverlayErrorCode, Callable[[], CoreOverlayToolsState]], ...] = (
        (
            OverlayErrorCode.TEXT_INVALID,
            lambda: state.create(OverlayKind.TEXT, value=oversize),
        ),
        (
            OverlayErrorCode.EDIT_KIND_MISMATCH,
            lambda: state.create(OverlayKind.CHECKMARK, value=private),
        ),
        (
            OverlayErrorCode.CORE_TOOL_UNSUPPORTED,
            lambda: state.create(OverlayKind.SIGNATURE_IMAGE, value=private),
        ),
    )
    for expected, operation in checks:
        with pytest.raises(OverlayError) as raised:
            operation()
        assert raised.value.code is expected
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        failures.append(raised.value)

    rendered = "\n".join(_render_error(error) for error in failures)
    _assert_private_absent(rendered)
    assert "X" * 128 not in rendered
    logging.getLogger("paperworkshell.tests.core_overlay_error_privacy").debug(
        "failures=%r",
        failures,
    )
    captured = capsys.readouterr()
    _assert_private_absent(f"{caplog.text}\n{captured.out}\n{captured.err}")


def test_ui_sanitizer_rebuilds_a_mutated_exact_overlay_error(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    private = _PRIVATE_VALUES[OverlayKind.INITIALS]
    error = OverlayError(OverlayErrorCode.TEXT_INVALID, "overlay.command_value")
    error.safe_message = private
    error.args = (private,)

    sanitized = sanitize_ui_failure(error, operation_id=304)

    assert sanitized.code == OverlayErrorCode.TEXT_INVALID.value
    assert sanitized.message == "The visual-overlay text value is too large."
    assert sanitized.operation_id == 304
    _assert_private_absent(repr(sanitized))
    logging.getLogger("paperworkshell.tests.core_overlay_sanitizer_privacy").debug(
        "failure=%r",
        sanitized,
    )
    captured = capsys.readouterr()
    _assert_private_absent(f"{caplog.text}\n{captured.out}\n{captured.err}")


def test_ui_sanitizer_rebuilds_repository_errors_and_rejects_hostile_subclasses() -> None:
    private = _PRIVATE_VALUES[OverlayKind.TEXT]
    repository_error = ManifestRepositoryError(
        ManifestRepositoryErrorCode.SAVE_FAILED,
        "manifest.autosave",
    )
    repository_error.safe_message = private
    repository_error.args = (private,)

    repository_failure = sanitize_ui_failure(repository_error, operation_id=305)

    assert repository_failure.code == ManifestRepositoryErrorCode.SAVE_FAILED.value
    assert repository_failure.message == "PaperWorkShell could not save the case record safely."
    _assert_private_absent(repr(repository_failure))

    class HostileOverlayError(OverlayError):
        pass

    hostile = HostileOverlayError(OverlayErrorCode.TEXT_INVALID, "overlay.command_value")
    hostile.safe_message = private
    hostile.args = (private,)

    hostile_failure = sanitize_ui_failure(hostile, operation_id=306)

    assert hostile_failure.code == "PWS_UI_UNEXPECTED"
    _assert_private_absent(repr(hostile_failure))


def test_valid_failure_code_and_forged_announcement_cannot_reach_dialog_surfaces() -> None:
    private = _PRIVATE_VALUES[OverlayKind.DATE]
    _snapshot_value, state = _created_state(OverlayKind.DATE)
    with _dialog(state) as dialog:
        dialog._request_apply()
        assert dialog.set_apply_failed(ManifestRepositoryErrorCode.SAVE_FAILED.value, private)
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert status is not None
        _assert_private_absent(f"{status.text()}\n{status.accessibleDescription()}")
        dialog._request_apply()
        assert dialog.set_apply_failed("PRIVATE_APPLICANT_NAME", private)
        assert "PRIVATE_APPLICANT_NAME" not in status.text()

    forged = replace(state)
    object.__setattr__(forged, "announcement", private)
    with pytest.raises(ValueError) as rejected:
        CoreOverlayToolsDialog(forged)
    _assert_private_absent(str(rejected.value))
