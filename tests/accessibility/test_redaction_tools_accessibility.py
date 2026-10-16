from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QAccessibleEvent,
    QColor,
    QFont,
    QPixmap,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QSpinBox, QWidget

from paperworkshell.app.redaction_tools import MAX_REDACTION_EDIT_STEPS, RedactionToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
)
from paperworkshell.domain.redaction import MAX_REDACTION_MARKS, RedactionMark
from paperworkshell.ui.redaction_canvas import RedactionIntentCanvas
from paperworkshell.ui.redaction_tools import RedactionToolsDialog
from tests.helpers.redaction_editor_manual_probe import RedactionEditorManualProbe

ROOT = Path(__file__).resolve().parents[2]


def _app() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state() -> RedactionToolsState:
    document = cast(
        dict[str, object],
        json.loads(
            (ROOT / "schemas/examples/case-manifest-v3-representative.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    document["redaction_marks"] = []
    return RedactionToolsState.from_snapshot(
        ManifestSnapshot(
            UUID(cast(str, document["case_id"])),
            3,
            cast(int, document["revision"]),
            document,
            "c" * 64,
            "redaction_accessibility",
        ),
        UUID(cast(list[str], document["page_sequence"])[0]),
    )


@contextmanager
def _dialog(
    *,
    state: RedactionToolsState | None = None,
    transform: PageCoordinateTransform | None = None,
    pointer: bool = True,
) -> Iterator[RedactionToolsDialog]:
    app = _app()
    preview = QPixmap(600, 780)
    preview.fill(QColor("white"))
    dialog = RedactionToolsDialog(
        state or _state(),
        page_transform=transform,
        page_preview=preview,
        pointer_mapping_enabled=pointer,
    )
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: RedactionToolsDialog, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def _click(dialog: RedactionToolsDialog, name: str) -> None:
    _widget(dialog, QPushButton, name).click()
    _app().processEvents()


def test_all_controls_have_native_roles_names_descriptions_and_unapplied_status() -> None:
    with _dialog() as dialog:
        assert "Permanently remove information" in dialog.accessibleName()
        expected = (
            (QListWidget, "redactionRegionList", QAccessible.Role.List),
            (QSpinBox, "redactionLeftSpin", QAccessible.Role.SpinBox),
            (QSpinBox, "redactionTopSpin", QAccessible.Role.SpinBox),
            (QSpinBox, "redactionWidthSpin", QAccessible.Role.SpinBox),
            (QSpinBox, "redactionHeightSpin", QAccessible.Role.SpinBox),
            (QPushButton, "addRedactionRegionButton", QAccessible.Role.Button),
            (QPushButton, "updateRedactionRegionButton", QAccessible.Role.Button),
            (QPushButton, "removeRedactionRegionButton", QAccessible.Role.Button),
            (QPushButton, "undoRedactionRegionButton", QAccessible.Role.Button),
            (QPushButton, "redoRedactionRegionButton", QAccessible.Role.Button),
            (QPushButton, "saveRedactionMarksButton", QAccessible.Role.Button),
            (QPushButton, "cancelRedactionMarksButton", QAccessible.Role.Button),
            (QLabel, "redactionToolsStatus", QAccessible.Role.StaticText),
        )
        for kind, name, role in expected:
            widget = _widget(dialog, kind, name)
            assert widget.accessibleName()
            assert widget.accessibleDescription()
            interface = QAccessible.queryAccessibleInterface(widget)
            assert interface is not None and interface.role() is role
        _click(dialog, "addRedactionRegionButton")
        region_list = _widget(dialog, QListWidget, "redactionRegionList")
        assert "Unapplied" in region_list.item(0).text()
        assert "Information is still present" in region_list.item(0).text()
        assert (
            "No removal has been verified"
            in _widget(dialog, QLabel, "redactionIntentWarning").text()
        )
        warnings = _widget(dialog, QLabel, "redactionDerivativeWarning").text()
        for fact in (
            "rasterized",
            "accessibility structure",
            "interactive forms",
            "unsigned derivative",
            "Originals stay unchanged",
        ):
            assert fact in warnings
        assert not dialog.findChildren(QPushButton, "applyRedactionButton")


def test_keyboard_only_numeric_list_undo_redo_and_save_is_intent_only() -> None:
    with _dialog() as dialog:
        requests: list[object] = []
        dialog.save_requested.connect(requests.append)
        QTest.keyClick(dialog, Qt.Key.Key_A, Qt.KeyboardModifier.AltModifier)
        fields = [
            _widget(dialog, QSpinBox, f"redaction{name}Spin")
            for name in ("Left", "Top", "Width", "Height")
        ]
        values = (100_000, 200_000, 300_000, 100_000)
        for field, value in zip(fields, values, strict=True):
            field.setFocus()
            QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(field, str(value))
            QTest.keyClick(field, Qt.Key.Key_Tab)
        _click(dialog, "updateRedactionRegionButton")
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box == NormalizedPageBox(*values)
        _click(dialog, "undoRedactionRegionButton")
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box != NormalizedPageBox(*values)
        _click(dialog, "redoRedactionRegionButton")
        _click(dialog, "saveRedactionMarksButton")
        assert requests == [dialog.state] and dialog.save_pending
        assert (
            "Information is still present" in _widget(dialog, QLabel, "redactionToolsStatus").text()
        )
        assert dialog.finish_saved()
        assert dialog.result() == dialog.DialogCode.Accepted


def test_invalid_pending_fields_block_save_and_focus_recovery_without_changing_draft() -> None:
    with _dialog(state=_state().create()) as dialog:
        requests: list[object] = []
        dialog.save_requested.connect(requests.append)
        before = dialog.state
        _widget(dialog, QSpinBox, "redactionLeftSpin").setValue(999_999)
        _click(dialog, "saveRedactionMarksButton")
        assert requests == [] and dialog.state is before and not dialog.save_pending
        assert QApplication.focusWidget() is _widget(dialog, QSpinBox, "redactionLeftSpin")
        assert dialog.latest_state_for_shutdown() is None


def test_failed_save_retains_draft_blocks_discard_and_announces_only_fixed_status() -> None:
    with _dialog(state=_state().create()) as dialog:
        before = dialog.state
        _click(dialog, "saveRedactionMarksButton")
        assert dialog.save_pending and dialog.commit_started
        dialog.reject()
        assert dialog.isVisible()
        assert dialog.save_failed("PRIVATE-CODE", "PRIVATE-TEXT C:/PRIVATE/PATH")
        assert dialog.state is before and not bool(dialog.save_pending)
        assert not _widget(dialog, QPushButton, "cancelRedactionMarksButton").isEnabled()
        status = _widget(dialog, QLabel, "redactionToolsStatus")
        assert "PRIVATE" not in status.text() + status.accessibleDescription()
        assert "retained for retry" in status.text()
        assert QApplication.focusWidget() is status
        dialog.reject()
        assert dialog.isVisible()
        _click(dialog, "saveRedactionMarksButton")
        assert dialog.finish_saved()


def test_unstarted_cancel_discards_local_draft_without_save_request() -> None:
    with _dialog(state=_state().create()) as dialog:
        requests: list[object] = []
        dialog.save_requested.connect(requests.append)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and not requests


def test_scaling_and_tab_cycle_keep_native_controls_and_fixed_save_cancel_reachable() -> None:
    with _dialog(state=_state().create()) as dialog:
        font = QFont(dialog.font())
        font.setPointSizeF(max(8, font.pointSizeF()) * 2)
        dialog.setFont(font)
        dialog.resize(760, 550)
        _app().processEvents()
        save = _widget(dialog, QPushButton, "saveRedactionMarksButton")
        cancel = _widget(dialog, QPushButton, "cancelRedactionMarksButton")
        assert dialog.rect().contains(save.mapTo(dialog, save.rect().bottomRight()))
        assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().bottomRight()))
        region_list = _widget(dialog, QListWidget, "redactionRegionList")
        region_list.setFocus()
        seen: set[QWidget] = set()
        for _ in range(20):
            focused = QApplication.focusWidget()
            if focused is not None:
                seen.add(focused)
            QTest.keyClick(dialog, Qt.Key.Key_Tab)
        assert {region_list, save, cancel, _widget(dialog, QSpinBox, "redactionLeftSpin")} <= seen


def test_crop_rotation_and_outside_regions_remain_selectable_without_geometry_mutation() -> None:
    state = _state().create(NormalizedPageBox(10_000, 20_000, 100_000, 100_000))
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 200_000, 600_000, 600_000),
        manifest_rotation=ClockwiseRotation.DEGREES_90,
    )
    with _dialog(state=state, transform=transform) as dialog:
        assert (
            "outside active crop"
            in _widget(dialog, QListWidget, "redactionRegionList").item(0).text()
        )
        assert dialog.state is state
        assert _widget(dialog, QSpinBox, "redactionLeftSpin").value() == 10_000


def test_deskew_schematic_suppresses_pixels_and_pointer_but_numeric_selection_remains() -> None:
    with _dialog(pointer=False) as dialog:
        canvas = _widget(dialog, RedactionIntentCanvas, "redactionIntentCanvas")
        assert canvas._preview is None
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(80, 80))
        QTest.mouseMove(canvas, QPoint(180, 130))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(180, 130))
        assert not dialog.state.records
        _click(dialog, "addRedactionRegionButton")
        assert len(dialog.state.records) == 1


def test_pointer_drag_adds_one_outward_region_and_hide_cleans_capture() -> None:
    with _dialog() as dialog:
        canvas = _widget(dialog, RedactionIntentCanvas, "redactionIntentCanvas")
        page = canvas._page_rect()
        start = QPoint(
            round(page.left() + page.width() * 0.2), round(page.top() + page.height() * 0.2)
        )
        end = QPoint(
            round(page.left() + page.width() * 0.8), round(page.top() + page.height() * 0.6)
        )
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        assert len(dialog.state.records) == 1
        assert dialog.state.records[0].box.width > 500_000
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        canvas.hide()
        assert canvas._start is None and QWidget.mouseGrabber() is not canvas


def test_rejected_list_selection_keeps_original_mark_and_invalid_numeric_values_coherent() -> None:
    state = (
        _state()
        .create(NormalizedPageBox(100_000, 100_000, 200_000, 200_000))
        .create(NormalizedPageBox(400_000, 400_000, 200_000, 200_000))
    )
    with _dialog(state=state) as dialog:
        regions = _widget(dialog, QListWidget, "redactionRegionList")
        left = _widget(dialog, QSpinBox, "redactionLeftSpin")
        left.setValue(950_000)
        regions.setCurrentRow(0)
        assert regions.currentRow() == 1
        assert dialog.state is state
        assert left.value() == 950_000
        assert QApplication.focusWidget() is left


def test_valid_dirty_list_selection_commits_old_mark_once_then_selects_intended_mark() -> None:
    state = (
        _state()
        .create(NormalizedPageBox(100_000, 100_000, 200_000, 200_000))
        .create(NormalizedPageBox(400_000, 400_000, 200_000, 200_000))
    )
    with _dialog(state=state) as dialog:
        regions = _widget(dialog, QListWidget, "redactionRegionList")
        left = _widget(dialog, QSpinBox, "redactionLeftSpin")
        left.setValue(500_000)
        regions.setCurrentRow(0)
        assert regions.currentRow() == 0
        assert dialog.state.selected_mark_id == state.records[0].mark_id
        assert dialog.state.records[1].box.x == 500_000
        assert dialog.state.cursor == state.cursor + 1
        assert left.value() == state.records[0].box.x


def test_shutdown_collects_uncommitted_keyboard_text_from_focused_numeric_field() -> None:
    state = _state().create(NormalizedPageBox(100_000, 100_000, 200_000, 200_000))
    with _dialog(state=state) as dialog:
        left = _widget(dialog, QSpinBox, "redactionLeftSpin")
        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(left, "150000")
        latest = dialog.latest_state_for_shutdown()
        assert latest is not None and latest.selected_record is not None
        assert latest.selected_record.box.x == 150_000


def test_incomplete_focused_numeric_text_blocks_shutdown_without_reverting_visible_input() -> None:
    state = _state().create(NormalizedPageBox(100_000, 100_000, 200_000, 200_000))
    with _dialog(state=state) as dialog:
        left = _widget(dialog, QSpinBox, "redactionLeftSpin")
        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(left, Qt.Key.Key_Backspace)
        assert dialog.latest_state_for_shutdown() is None
        assert dialog.state is state
        assert left.text() == ""
        assert QApplication.focusWidget() is left


@pytest.mark.parametrize(
    ("rotation", "expected"),
    (
        (ClockwiseRotation.DEGREES_0, (320_000, 320_000, 360_000, 240_000)),
        (ClockwiseRotation.DEGREES_90, (320_000, 320_000, 240_000, 360_000)),
        (ClockwiseRotation.DEGREES_180, (320_000, 440_000, 360_000, 240_000)),
        (ClockwiseRotation.DEGREES_270, (440_000, 320_000, 240_000, 360_000)),
    ),
)
def test_pointer_regions_invert_active_crop_and_each_quarter_turn(
    rotation: ClockwiseRotation, expected: tuple[int, int, int, int]
) -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 200_000, 600_000, 600_000),
        manifest_rotation=rotation,
    )
    with _dialog(transform=transform) as dialog:
        canvas = _widget(dialog, RedactionIntentCanvas, "redactionIntentCanvas")
        page = canvas._page_rect()
        start = QPoint(
            round(page.left() + page.width() * 0.2), round(page.top() + page.height() * 0.2)
        )
        end = QPoint(
            round(page.left() + page.width() * 0.8), round(page.top() + page.height() * 0.6)
        )
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        assert len(dialog.state.records) == 1
        box = dialog.state.records[0].box
        # Integer widget positions introduce at most one preview-pixel error.
        for actual, wanted in zip((box.x, box.y, box.width, box.height), expected, strict=True):
            assert abs(actual - wanted) < 5_000


def test_refresh_scans_record_and_selection_facades_a_constant_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _state()
    marks = tuple(
        RedactionMark(
            UUID(int=10_000 + index, version=4),
            base.page_id,
            NormalizedPageBox(0, 0, 10_000, 10_000),
        )
        for index in range(64)
    )
    state = RedactionToolsState(
        base.case_id,
        base.manifest_revision,
        base.page_id,
        base.editor_session_id,
        marks,
        selected_mark_id=marks[-1].mark_id,
    )
    with _dialog(state=state) as dialog:
        record_reads = 0
        selected_reads = 0
        original_records = cast(
            Callable[[RedactionToolsState], tuple[RedactionMark, ...]],
            cast(property, RedactionToolsState.__dict__["records"]).fget,
        )
        original_selected = cast(
            Callable[[RedactionToolsState], RedactionMark | None],
            cast(property, RedactionToolsState.__dict__["selected_record"]).fget,
        )

        def records(value: RedactionToolsState) -> tuple[RedactionMark, ...]:
            nonlocal record_reads
            record_reads += 1
            return original_records(value)

        def selected(value: RedactionToolsState) -> RedactionMark | None:
            nonlocal selected_reads
            selected_reads += 1
            return original_selected(value)

        monkeypatch.setattr(RedactionToolsState, "records", property(records))
        monkeypatch.setattr(RedactionToolsState, "selected_record", property(selected))
        dialog._refresh()
        assert record_reads == 2 and selected_reads == 1


def _one_edit_below_limit() -> RedactionToolsState:
    state = (
        _state()
        .create(
            NormalizedPageBox(100_000, 100_000, 100_000, 100_000),
            id_factory=lambda: UUID(int=500, version=4),
        )
        .create(
            NormalizedPageBox(400_000, 400_000, 100_000, 100_000),
            id_factory=lambda: UUID(int=501, version=4),
        )
    )
    for position in range(2, MAX_REDACTION_EDIT_STEPS - 1):
        state = state.replace_selected_box(
            NormalizedPageBox(100_000 + position, 100_000, 100_000, 100_000)
        )
    assert state.cursor == MAX_REDACTION_EDIT_STEPS - 1
    return state


def _reach_limit_through_numeric_controls(dialog: RedactionToolsDialog) -> None:
    _widget(dialog, QSpinBox, "redactionLeftSpin").setValue(200_000)
    _click(dialog, "updateRedactionRegionButton")
    assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS


def test_edit_limit_disables_mutation_controls_but_keeps_review_and_save_recovery() -> None:
    with _dialog(state=_one_edit_below_limit()) as dialog:
        requests: list[object] = []
        dialog.save_requested.connect(requests.append)
        _reach_limit_through_numeric_controls(dialog)
        for name in ("Left", "Top", "Width", "Height"):
            field = _widget(dialog, QSpinBox, f"redaction{name}Spin")
            assert not field.isEnabled()
            interface = QAccessible.queryAccessibleInterface(field)
            assert interface is not None and interface.state().disabled
        for name in (
            "updateRedactionRegionButton",
            "addRedactionRegionButton",
            "removeRedactionRegionButton",
        ):
            assert not _widget(dialog, QPushButton, name).isEnabled()
        assert not dialog._canvas.isEnabled()
        for name in (
            "undoRedactionRegionButton",
            "saveRedactionMarksButton",
            "cancelRedactionMarksButton",
        ):
            assert _widget(dialog, QPushButton, name).isEnabled()
        regions = _widget(dialog, QListWidget, "redactionRegionList")
        assert regions.isEnabled()
        regions.setCurrentRow(0)
        assert dialog.state.selected_mark_id == dialog.state.records[0].mark_id
        assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS
        status = _widget(dialog, QLabel, "redactionToolsStatus").text()
        assert "edit limit" in status and all(
            action in status for action in ("Save", "Undo", "Cancel")
        )
        before = dialog.state
        dialog._canvas.region_requested.emit(NormalizedPageBox(0, 0, 10_000, 10_000))
        assert dialog.state is before
        _click(dialog, "saveRedactionMarksButton")
        assert requests == [before] and dialog.save_pending
        assert dialog.finish_saved()


def test_edit_limit_undo_reenables_new_branch_and_failed_save_does_not_enable_discard() -> None:
    with _dialog(state=_one_edit_below_limit()) as dialog:
        _reach_limit_through_numeric_controls(dialog)
        _click(dialog, "saveRedactionMarksButton")
        assert dialog.save_failed("MANIFEST_SAVE_FAILED", "Try saving again.")
        assert not _widget(dialog, QPushButton, "cancelRedactionMarksButton").isEnabled()
        assert not _widget(dialog, QSpinBox, "redactionLeftSpin").isEnabled()
        assert _widget(dialog, QPushButton, "undoRedactionRegionButton").isEnabled()
        _click(dialog, "undoRedactionRegionButton")
        assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS - 1
        for name in ("Left", "Top", "Width", "Height"):
            assert _widget(dialog, QSpinBox, f"redaction{name}Spin").isEnabled()
        assert _widget(dialog, QPushButton, "addRedactionRegionButton").isEnabled()
        assert _widget(dialog, QPushButton, "removeRedactionRegionButton").isEnabled()
        assert dialog._canvas.isEnabled()
        _widget(dialog, QSpinBox, "redactionLeftSpin").setValue(300_000)
        _click(dialog, "updateRedactionRegionButton")
        assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS and not dialog.state.can_redo
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.x == 300_000
        status = _widget(dialog, QLabel, "redactionToolsStatus").text()
        assert "Retry saving" in status and "Cancel" not in status


def test_queued_capacity_failure_restores_accepted_fields_and_never_reports_bad_geometry() -> None:
    with _dialog(state=_one_edit_below_limit()) as dialog:
        _reach_limit_through_numeric_controls(dialog)
        left = _widget(dialog, QSpinBox, "redactionLeftSpin")
        # Simulate an already queued value signal after the controls became disabled.
        left.setValue(300_000)
        _click(dialog, "saveRedactionMarksButton")
        status = _widget(dialog, QLabel, "redactionToolsStatus").text()
        assert (
            "edit limit" in status and "positive width" not in status and "fit inside" not in status
        )
        assert left.value() == 200_000
        assert not dialog.save_pending
        _click(dialog, "undoRedactionRegionButton")
        assert left.isEnabled() and dialog.state.cursor == MAX_REDACTION_EDIT_STEPS - 1


def test_last_pending_numeric_edit_is_retained_when_original_add_action_reaches_capacity() -> None:
    with _dialog(state=_one_edit_below_limit()) as dialog:
        _widget(dialog, QSpinBox, "redactionLeftSpin").setValue(250_000)
        _click(dialog, "addRedactionRegionButton")
        assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS
        assert len(dialog.state.records) == 2 and dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.x == 250_000
        status = _widget(dialog, QLabel, "redactionToolsStatus").text()
        assert "edit limit" in status and "positive width" not in status
        assert QApplication.focusWidget() is _widget(
            dialog, QPushButton, "undoRedactionRegionButton"
        )
        _click(dialog, "undoRedactionRegionButton")
        assert dialog.state.cursor == MAX_REDACTION_EDIT_STEPS - 1
        assert _widget(dialog, QSpinBox, "redactionLeftSpin").isEnabled()


def test_mark_capacity_after_failed_save_never_advertises_unavailable_cancel() -> None:
    base = _state()
    marks = tuple(
        RedactionMark(
            UUID(int=100_000 + index, version=4),
            base.page_id,
            NormalizedPageBox(0, 0, 10_000, 10_000),
        )
        for index in range(MAX_REDACTION_MARKS)
    )
    state = RedactionToolsState(
        base.case_id,
        base.manifest_revision,
        base.page_id,
        base.editor_session_id,
        marks,
        selected_mark_id=marks[0].mark_id,
    ).replace_selected_box(NormalizedPageBox(20_000, 20_000, 10_000, 10_000))
    with _dialog(state=state) as dialog:
        _click(dialog, "saveRedactionMarksButton")
        assert dialog.save_failed("MANIFEST_SAVE_FAILED", "Try saving again.")
        _click(dialog, "addRedactionRegionButton")
        assert len(dialog.state.records) == MAX_REDACTION_MARKS
        status = _widget(dialog, QLabel, "redactionToolsStatus").text()
        assert "removal-mark limit" in status and "retry saving" in status
        assert "cancel" not in status.lower()
        assert not _widget(dialog, QPushButton, "cancelRedactionMarksButton").isEnabled()
        _click(dialog, "removeRedactionRegionButton")
        assert len(dialog.state.records) == MAX_REDACTION_MARKS - 1
        assert _widget(dialog, QPushButton, "saveRedactionMarksButton").isEnabled()


@pytest.mark.parametrize("assertive", (False, True))
def test_status_is_visible_before_dialog_root_announcement_without_moving_keyboard_focus(
    assertive: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog(state=_state().create()) as dialog:
        action = _widget(dialog, QPushButton, "updateRedactionRegionButton")
        status = _widget(dialog, QLabel, "redactionToolsStatus")
        action.setFocus()
        dialog._scroll.verticalScrollBar().setValue(0)
        viewport = dialog._scroll.viewport()

        def visible_status_rect() -> QRect:
            return QRect(status.mapTo(viewport, QPoint()), status.size())

        assert not viewport.rect().intersects(visible_status_rect())
        sequence: list[str] = []
        original_ensure = dialog._scroll.ensureWidgetVisible
        expected = "Removal-mark edit redone. Information is still present."

        def ensure_visible(widget: QWidget, xmargin: int = 50, ymargin: int = 50) -> None:
            assert widget is status
            sequence.append("visible")
            original_ensure(widget, xmargin, ymargin)

        def announce(event: QAccessibleEvent) -> None:
            assert type(event) is QAccessibleAnnouncementEvent
            assert sequence == ["visible"]
            assert event.object() is dialog
            assert event.message() == expected
            assert event.politeness() is (
                QAccessible.AnnouncementPoliteness.Assertive
                if assertive
                else QAccessible.AnnouncementPoliteness.Polite
            )
            assert status.text() == expected and status.accessibleDescription() == expected
            assert viewport.rect().intersects(visible_status_rect())
            assert QApplication.focusWidget() is action
            sequence.append("announcement")

        monkeypatch.setattr(dialog._scroll, "ensureWidgetVisible", ensure_visible)
        monkeypatch.setattr(QAccessible, "updateAccessibility", announce)
        dialog._set_status(expected, assertive)
        assert sequence == ["visible", "announcement"]
        assert QApplication.focusWidget() is action


def test_numeric_update_undo_redo_announce_from_dialog_and_keep_action_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog(state=_state().create()) as dialog:
        action = _widget(dialog, QPushButton, "updateRedactionRegionButton")
        action.setFocus()
        messages: list[str] = []

        def announce(event: QAccessibleEvent) -> None:
            if type(event) is QAccessibleAnnouncementEvent:
                assert event.object() is dialog
                assert QApplication.focusWidget() is action
                messages.append(event.message())

        monkeypatch.setattr(QAccessible, "updateAccessibility", announce)
        _widget(dialog, QSpinBox, "redactionLeftSpin").setValue(150_000)
        _click(dialog, "updateRedactionRegionButton")
        QTest.keyClick(dialog, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(dialog, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert len(messages) == 3
        assert "position and size updated" in messages[0]
        assert "edit undone" in messages[1] and "edit redone" in messages[2]
        assert QApplication.focusWidget() is action


def test_manual_probe_uses_real_parent_modal_context_with_preview_retry_and_cancel() -> None:
    app = _app()
    probe = RedactionEditorManualProbe()
    probe.start()
    app.processEvents()
    window = probe.window
    try:
        assert probe.signals._backend is None
        assert window.isVisible() and window._active_operation is None
        assert "effective DPR" in window.windowTitle()
        window._request_redaction_tools()
        app.processEvents()
        dialog = window._redaction_tools_dialog
        assert dialog is not None and dialog.parentWidget() is window
        assert dialog.windowModality() is Qt.WindowModality.WindowModal
        assert QApplication.activeModalWidget() is dialog
        assert "effective DPR" in dialog.windowTitle()
        preview = dialog._canvas._preview
        assert preview is not None and (preview.width(), preview.height()) == (600, 780)
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box == NormalizedPageBox(
            150_000, 250_000, 700_000, 150_000
        )
        _click(dialog, "addRedactionRegionButton")
        _click(dialog, "saveRedactionMarksButton")
        assert window._redaction_tools_dialog is dialog and not dialog.save_pending
        assert "retained for retry" in dialog._status.text()
        assert not _widget(dialog, QPushButton, "cancelRedactionMarksButton").isEnabled()
        assert len(tuple(probe.marks)) == 1
        _click(dialog, "saveRedactionMarksButton")
        assert cast(object, window._redaction_tools_dialog) is None and window.isVisible()
        assert (
            "Redaction marks saved. No information has been removed." in window._status_label.text()
        )
        assert len(probe.marks) == 2 and probe.revision == 2
        window._request_redaction_tools()
        app.processEvents()
        reopened = window._redaction_tools_dialog
        assert reopened is not None and len(reopened.state.records) == 2
        QTest.keyClick(reopened, Qt.Key.Key_Escape)
        app.processEvents()
        assert cast(object, window._redaction_tools_dialog) is None and window.isVisible()
        assert "changes cancelled. No information has been removed." in window._status_label.text()
        assert probe.signals._backend is None and window._active_operation is None
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()
