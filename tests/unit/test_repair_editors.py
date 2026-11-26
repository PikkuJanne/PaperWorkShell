"""PWS-0811 visible editing journeys and accidental-scroll regressions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.overlays.models import OverlayRecord, TextualOverlayPayload
from paperworkshell.ui.checklist import ChecklistEditorDialog
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog
from paperworkshell.ui.presentation import install_presentation_policy
from tests.accessibility.test_checklist_accessibility import _empty_state
from tests.accessibility.test_scan_review_accessibility import _dialog as scan_dialog
from tests.unit.test_overlay_tools_dialog import _application, _dialog, _state


def _wheel(widget: QWidget) -> None:
    center = widget.rect().center()
    event = QWheelEvent(
        QPointF(center),
        QPointF(widget.mapToGlobal(center)),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_add_text_reveals_focused_field_live_preview_and_one_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog() as dialog:
        dialog.resize(1000, 650)
        app = _application()
        app.processEvents()
        add = dialog.findChild(QPushButton, "addTextButton")
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        scroll = dialog.findChild(QScrollArea, "coreOverlayToolsScrollArea")
        canvas = dialog.findChild(OverlayEditorCanvas, "overlayEditorCanvas")
        save = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert add is not None and add.isVisible()
        assert value is not None and scroll is not None and canvas is not None and save is not None
        captured: list[tuple[OverlayRecord, ...]] = []
        original = canvas.set_records

        def capture(records: tuple[OverlayRecord, ...], selected: UUID | None) -> None:
            captured.append(records)
            original(records, selected)

        monkeypatch.setattr(canvas, "set_records", capture)
        QTest.mouseClick(add, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert value.isVisible() and value.hasFocus()
        assert dialog.rect().contains(value.mapTo(dialog, value.rect().topLeft()))
        assert dialog.rect().contains(value.mapTo(dialog, value.rect().bottomRight()))
        assert not scroll.isAncestorOf(value)
        assert value.accessibleName() == "Text"
        QTest.keyClicks(value, "Internal trial")
        app.processEvents()
        latest = captured[-1]
        assert len(latest) == 1
        payload = latest[0].payload
        assert type(payload) is TextualOverlayPayload
        assert payload.value == "Internal trial"
        # Preview changes do not create a command for every typed character.
        assert dialog.state.selected_record is not None
        assert type(dialog.state.selected_record.payload) is TextualOverlayPayload
        assert dialog.state.selected_record.payload.value == ""
        saved: list[CoreOverlayToolsState] = []
        dialog.apply_requested.connect(saved.append)
        assert save.text().replace("\n", " ") == "Save changes"
        QTest.mouseClick(save, Qt.MouseButton.LeftButton)
        assert len(saved) == 1
        record = saved[0].selected_record
        assert record is not None and type(record.payload) is TextualOverlayPayload
        assert record.payload.value == "Internal trial"


def test_percent_fields_preserve_exact_existing_geometry_and_keyboard_editing() -> None:
    with _dialog() as dialog:
        add = dialog.findChild(QPushButton, "addTextButton")
        assert add is not None
        add.click()
        left = dialog.findChild(QDoubleSpinBox, "coreOverlayLeftSpin")
        top = dialog.findChild(QDoubleSpinBox, "coreOverlayTopSpin")
        save = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert left is not None and top is not None and save is not None
        left.setValue(12.3457)
        top.setValue(20)
        assert left.text().endswith(" %")
        assert left.maximum() == 100
        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_Up)
        assert left.value() == pytest.approx(12.4457)
        save.click()
        record = dialog.state.selected_record
        assert record is not None
        assert (record.box.x, record.box.y) == (124457, 200000)


def test_enlarged_editor_reveals_the_whole_new_text_field_and_bounded_footer() -> None:
    app = _application()
    install_presentation_policy(app)
    previous_font = QFont(app.font())
    enlarged = QFont(previous_font)
    enlarged.setPointSizeF(max(24, previous_font.pointSizeF() * 2))
    app.setFont(enlarged)
    page_label = "Synthetic long Ää source name " * 8 + "— source page 1, output page 1"
    dialog: CoreOverlayToolsDialog | None = None
    try:
        dialog = CoreOverlayToolsDialog(_state(), page_label=page_label)
        dialog.resize(520, 360)
        dialog.show()
        QTest.qWait(20)
        add = dialog.findChild(QPushButton, "addTextButton")
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        scroll = dialog.findChild(QScrollArea, "coreOverlayToolsScrollArea")
        identity = dialog.findChild(QLabel, "overlayEditorPageLabel")
        assert add is not None and value is not None and scroll is not None and identity is not None
        add.click()
        QTest.qWait(20)
        assert value.hasFocus()
        assert not scroll.isAncestorOf(value)
        assert dialog.rect().contains(value.mapTo(dialog, value.rect().topLeft()))
        assert dialog.rect().contains(value.mapTo(dialog, value.rect().bottomRight()))
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        assert dialog.rect().contains(value.mapTo(dialog, value.rect().bottomRight()))
        assert identity.accessibleName() == page_label and identity.text() == page_label
        assert identity.height() <= identity.fontMetrics().height() + 8
        save = dialog.findChild(QPushButton, "applyFormMarksButton")
        cancel = dialog.findChild(QPushButton, "cancelFormMarksButton")
        assert save is not None and cancel is not None
        for button in (save, cancel):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert not save.geometry().intersects(cancel.geometry())
        QTest.keyClicks(value, "Visible draft")
        assert value.text() == "Visible draft"
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        app.processEvents()
        app.setFont(previous_font)


def test_scrolling_across_every_editable_overlay_control_preserves_values() -> None:
    with _dialog() as dialog:
        add = dialog.findChild(QPushButton, "addTextButton")
        assert add is not None
        add.click()
        controls: tuple[QSpinBox | QDoubleSpinBox | QComboBox, ...] = (
            *dialog.findChildren(QSpinBox),
            *dialog.findChildren(QDoubleSpinBox),
            *dialog.findChildren(QComboBox),
        )
        assert len(controls) >= 8
        for control in controls:
            before = control.currentIndex() if isinstance(control, QComboBox) else control.value()
            for focused in (False, True):
                control.setFocus() if focused else add.setFocus()
                _wheel(control)
                after = (
                    control.currentIndex() if isinstance(control, QComboBox) else control.value()
                )
                assert after == before, control.objectName()


def test_related_scan_fields_ignore_wheel_but_retain_deliberate_editing() -> None:
    with scan_dialog() as dialog:
        controls: tuple[QDoubleSpinBox | QComboBox, ...] = (
            *dialog.findChildren(QDoubleSpinBox),
            *dialog.findChildren(QComboBox),
        )
        assert controls
        for control in controls:
            before = control.currentIndex() if isinstance(control, QComboBox) else control.value()
            _wheel(control)
            after = control.currentIndex() if isinstance(control, QComboBox) else control.value()
            assert before == after, control.objectName()


@contextmanager
def _named_checklist() -> Iterator[ChecklistEditorDialog]:
    app = _application()
    state = _empty_state()
    page = state.pages[0]
    label = "Äänekoski scan.pdf — source page 1, output page 1"
    dialog = ChecklistEditorDialog(state, page_labels={page.page_id: label})
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_checklist_shows_names_and_purpose_while_confirmation_stays_substantive() -> None:
    with _named_checklist() as dialog:
        available = dialog.findChild(QListView, "checklistAvailablePageList")
        assigned = dialog.findChild(QListView, "checklistAssignmentList")
        add = dialog.findChild(QPushButton, "checklistAddPagesButton")
        confirm = dialog.findChild(QPushButton, "checklistConfirmButton")
        guidance = dialog.findChild(QLabel, "checklistEditorGuidance")
        reason = dialog.findChild(QLabel, "checklistConfirmationPurpose")
        assert available is not None and assigned is not None and add is not None
        assert confirm is not None and guidance is not None and reason is not None
        assert "Ordinary PDF export" in guidance.text()
        assert "reviewed this document" in reason.text()
        index = available.model().index(0, 0)
        assert "Äänekoski scan.pdf" in available.model().data(index)
        available.setCurrentIndex(index)
        add.click()
        assert dialog.state.assignments
        assert "confirmation required" in assigned.model().data(assigned.model().index(0, 0))
        assert "Äänekoski scan.pdf" in assigned.model().data(assigned.model().index(0, 0))
        assert confirm.isEnabled()
        # An incomplete saved draft must never claim a completed requirement.
        assert any(requirement.status.value != "ready" for requirement in dialog.state.requirements)
        target = dialog.findChild(QComboBox, "checklistMoveTargetCombo")
        assert target is not None
        before = target.currentIndex()
        _wheel(target)
        assert target.currentIndex() == before
