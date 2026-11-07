from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
)

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.overlays.models import (
    MAX_OVERLAY_TEXT_CODE_POINTS,
    MarkOverlayPayload,
    OverlayKind,
    TextualOverlayPayload,
)
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PRIVATE_VALUE = "PRIVATE-FORM-VALUE-0304"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state() -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8")),
    )
    document["assets"] = []
    document["overlays"] = []
    case_id = UUID(cast(str, document["case_id"]))
    revision = cast(int, document["revision"])
    snapshot = ManifestSnapshot(
        case_id,
        2,
        revision,
        document,
        "f" * 64,
        "core_overlay_test_cas",
    )
    page_id = UUID(cast(list[str], document["page_sequence"])[0])
    return CoreOverlayToolsState.from_snapshot(snapshot, page_id)


def _loaded_state_without_local_history() -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8")),
    )
    snapshot = ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "a" * 64,
        "core_overlay_loaded_test_cas",
    )
    page_id = UUID(cast(list[str], document["page_sequence"])[0])
    state = CoreOverlayToolsState.from_snapshot(snapshot, page_id)
    return state.select(state.records[0].overlay_id)


@contextmanager
def _dialog(state: CoreOverlayToolsState | None = None) -> Iterator[CoreOverlayToolsDialog]:
    app = _application()
    dialog = CoreOverlayToolsDialog(_state() if state is None else state)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        if dialog.apply_pending:
            dialog.set_apply_failed("PWS_TEST_CLEANUP", "The test apply was cancelled.")
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _activate(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    _application().processEvents()


def _add_kind(dialog: CoreOverlayToolsDialog, kind: OverlayKind) -> None:
    combo = dialog.findChild(QComboBox, "newOverlayTypeCombo")
    button = dialog.findChild(QPushButton, "addCoreOverlayButton")
    assert combo is not None
    assert button is not None
    index = combo.findData(kind.value)
    assert index >= 0
    combo.setCurrentIndex(index)
    _activate(button)


def test_keyboard_adds_selects_and_removes_every_core_kind_without_disclosing_values() -> None:
    with _dialog() as dialog:
        for kind in (
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.CHECKMARK,
            OverlayKind.CROSS,
            OverlayKind.INITIALS,
        ):
            _add_kind(dialog, kind)

        assert tuple(record.kind for record in dialog.state.records) == (
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.CHECKMARK,
            OverlayKind.CROSS,
            OverlayKind.INITIALS,
        )
        overlay_list = dialog.findChild(QListWidget, "coreOverlayList")
        assert overlay_list is not None
        assert overlay_list.count() == 5
        assert overlay_list.currentRow() == 4
        assert not overlay_list.dragEnabled()
        assert not overlay_list.acceptDrops()

        # Arrow-key selection is ordinary list navigation and does not enter history.
        overlay_list.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(overlay_list, Qt.Key.Key_Up)
        _application().processEvents()
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.kind is OverlayKind.CROSS

        QTest.keyClick(overlay_list, Qt.Key.Key_Delete)
        _application().processEvents()
        assert tuple(record.kind for record in dialog.state.records) == (
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.CHECKMARK,
            OverlayKind.INITIALS,
        )
        assert PRIVATE_VALUE not in " ".join(
            overlay_list.item(row).text() for row in range(overlay_list.count())
        )


def test_value_geometry_move_resize_and_history_shortcuts_preserve_exact_state() -> None:
    with _dialog() as dialog:
        _add_kind(dialog, OverlayKind.DATE)
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        width = dialog.findChild(QDoubleSpinBox, "coreOverlayWidthSpin")
        apply_properties = dialog.findChild(
            QPushButton,
            "applyCoreOverlayPropertiesButton",
        )
        overlay_list = dialog.findChild(QListWidget, "coreOverlayList")
        assert value is not None
        assert left is not None
        assert width is not None
        assert apply_properties is not None
        assert overlay_list is not None

        value.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClicks(value, " 03/04/2030 ")
        left.setValue(12.3457)
        width.setValue(23.4569)
        QTest.keyClick(value, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        _application().processEvents()

        selected = dialog.state.selected_record
        assert selected is not None
        assert type(selected.payload) is TextualOverlayPayload
        assert selected.payload.value == " 03/04/2030 "
        assert selected.box.x == 123_457
        assert selected.box.width == 234_569
        before_move = selected

        overlay_list.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(
            overlay_list,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.AltModifier,
        )
        QTest.keyClick(
            overlay_list,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        _application().processEvents()
        moved = dialog.state.selected_record
        assert moved is not None
        assert moved.box.x == before_move.box.x + 10_000
        assert moved.box.width == before_move.box.width + 10_000

        QTest.keyClick(overlay_list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        _application().processEvents()
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.width == before_move.box.width

        QTest.keyClick(overlay_list, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        _application().processEvents()
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.width == before_move.box.width + 10_000


def test_mark_color_is_editable_and_invalid_box_fails_without_partial_mutation() -> None:
    with _dialog() as dialog:
        _add_kind(dialog, OverlayKind.CHECKMARK)
        color = dialog.findChild(QComboBox, "coreOverlayColorCombo")
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        width = dialog.findChild(QDoubleSpinBox, "coreOverlayWidthSpin")
        apply_properties = dialog.findChild(
            QPushButton,
            "applyCoreOverlayPropertiesButton",
        )
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert color is not None
        assert left is not None
        assert width is not None
        assert apply_properties is not None
        assert status is not None

        color.setCurrentIndex(color.findData("#008040ff"))
        _activate(apply_properties)
        selected = dialog.state.selected_record
        assert selected is not None
        assert type(selected.payload) is MarkOverlayPayload
        assert selected.payload.color.value == "#008040ff"

        before = selected
        left.setValue(90)
        width.setValue(20)
        _activate(apply_properties)
        assert dialog.state.selected_record == before
        assert "outside" in status.text().casefold()


def test_apply_request_stays_open_until_sanitized_success_or_failure() -> None:
    app = _application()
    with _dialog() as dialog:
        _add_kind(dialog, OverlayKind.TEXT)
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        cancel_button = dialog.findChild(QPushButton, "cancelFormMarksButton")
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert apply_button is not None
        assert cancel_button is not None
        assert status is not None
        requests: list[CoreOverlayToolsState] = []
        dialog.apply_requested.connect(requests.append)

        _activate(apply_button)
        assert dialog.apply_pending
        assert requests == [dialog.state]
        assert not apply_button.isEnabled()
        assert not cancel_button.isEnabled()
        assert dialog.commit_started
        applying_status = status.text()
        zoom = dialog.findChild(QComboBox, "overlayEditorZoomCombo")
        fit = dialog.findChild(QPushButton, "overlayEditorFitPageButton")
        pan = dialog.findChild(QPushButton, "overlayEditorPanCenterButton")
        assert zoom is not None and fit is not None and pan is not None
        assert not zoom.isEnabled() and not fit.isEnabled() and not pan.isEnabled()
        QTest.keyClick(
            dialog,
            Qt.Key.Key_Equal,
            Qt.KeyboardModifier.ControlModifier,
        )
        QTest.keyClick(
            dialog,
            Qt.Key.Key_G,
            Qt.KeyboardModifier.ControlModifier,
        )
        app.processEvents()
        assert status.text() == applying_status
        assert dialog.set_apply_succeeded()
        app.processEvents()
        assert dialog.result() == int(QDialog.DialogCode.Accepted)

    with _dialog() as dialog:
        _add_kind(dialog, OverlayKind.INITIALS)
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert apply_button is not None
        assert status is not None
        _activate(apply_button)
        assert dialog.set_apply_failed("not safe", PRIVATE_VALUE)
        pending_after_failure = dialog.apply_pending
        assert not pending_after_failure
        assert dialog.commit_started
        assert status.text() == (
            "PWS_UI_REQUEST_INVALID: PaperWorkShell could not confirm that visual "
            "overlay change safely. Changes are retained for retry."
        )
        assert PRIVATE_VALUE not in status.text()
        assert apply_button.isEnabled()
        cancel_button = dialog.findChild(QPushButton, "cancelFormMarksButton")
        assert cancel_button is not None
        assert not cancel_button.isEnabled()
        assert "retained for retry" in cancel_button.accessibleDescription()

        _add_kind(dialog, OverlayKind.CROSS)
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.kind is OverlayKind.CROSS
        assert not cancel_button.isEnabled()

        dialog.reject()
        assert dialog.result() == 0
        assert "retained for retry" in status.text()

        _activate(apply_button)
        assert dialog.apply_pending
        assert dialog.set_apply_succeeded()


def test_unavailable_undo_and_redo_never_desynchronize_dirty_fields_from_draft() -> None:
    initial = _loaded_state_without_local_history()
    assert not initial.can_undo and not initial.can_redo
    with _dialog(initial) as dialog:
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert left is not None and apply_button is not None
        original_left = left.value()

        left.setValue(original_left + 0.1234)
        QTest.keyClick(dialog, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        _application().processEvents()
        assert left.value() == original_left
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.x == round(original_left * 10_000)

        left.setValue(original_left + 0.2345)
        QTest.keyClick(dialog, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        _application().processEvents()
        assert left.value() == original_left + 0.2345
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.x == round(left.value() * 10_000)

        requests: list[CoreOverlayToolsState] = []
        dialog.apply_requested.connect(requests.append)
        _activate(apply_button)
        assert requests == [dialog.state]
        assert requests[0].selected_record is not None
        assert requests[0].selected_record.box.x == round(left.value() * 10_000)


def test_supplementary_unicode_limit_preserves_4096_code_points_and_rejects_4097() -> None:
    with _dialog() as dialog:
        _add_kind(dialog, OverlayKind.TEXT)
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        apply_properties = dialog.findChild(
            QPushButton,
            "applyCoreOverlayPropertiesButton",
        )
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert value is not None
        assert left is not None
        assert apply_properties is not None
        assert status is not None
        maximum = "😀" * MAX_OVERLAY_TEXT_CODE_POINTS
        value.setText(maximum)
        assert value.text() == maximum
        left.setValue(left.value() + 0.0001)
        _activate(apply_properties)
        accepted = dialog.state.selected_record
        assert accepted is not None and type(accepted.payload) is TextualOverlayPayload
        assert accepted.payload.value == maximum

        over_limit = maximum + "😀"
        value.setText(over_limit)
        assert value.text() == over_limit
        left.setValue(left.value() + 0.0001)
        _activate(apply_properties)

        assert dialog.state.selected_record == accepted
        assert "too large" in status.text().casefold()


def test_unrepresentable_loaded_value_cannot_be_silently_replaced_by_other_edits() -> None:
    original_value = "A\ud800B"
    state = _state().create(OverlayKind.TEXT, value=original_value)
    before = state.selected_record
    assert before is not None
    with _dialog(state) as dialog:
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        apply_properties = dialog.findChild(
            QPushButton,
            "applyCoreOverlayPropertiesButton",
        )
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert value is not None
        assert left is not None
        assert apply_properties is not None
        assert status is not None
        assert value.text() != original_value
        assert "cannot be represented" in status.text()

        left.setValue(left.value() + 0.0001)
        _activate(apply_properties)

        assert dialog.state.selected_record == before
        assert "replace the value explicitly" in status.text().casefold()

        value.setFocus(Qt.FocusReason.OtherFocusReason)
        value.selectAll()
        QTest.keyClicks(value, "replacement")
        _activate(apply_properties)
        replaced = dialog.state.selected_record
        assert replaced is not None and type(replaced.payload) is TextualOverlayPayload
        assert replaced.payload.value == "replacement"
