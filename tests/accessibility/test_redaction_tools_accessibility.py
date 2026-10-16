from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QColor, QFont, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QSpinBox, QWidget

from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
)
from paperworkshell.ui.redaction_canvas import RedactionIntentCanvas
from paperworkshell.ui.redaction_tools import RedactionToolsDialog

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
