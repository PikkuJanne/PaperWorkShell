"""Focused UI and display-state evidence for the PWS-0306 overlay editor."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QListWidget,
    QPushButton,
    QWidget,
)

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
    RenderedImageSize,
)
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog
from tests.helpers.qt_layout import wait_for_geometry

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PAGE_ONE = UUID("30000000-0000-4000-8000-000000000001")
PAGE_THREE = UUID("30000000-0000-4000-8000-000000000003")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _snapshot() -> ManifestSnapshot:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE.read_text(encoding="utf-8")),
    )
    return ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "f" * 64,
        "overlay_editor_ui_synthetic",
    )


def _state(page_id: UUID = PAGE_ONE) -> CoreOverlayToolsState:
    return CoreOverlayToolsState.from_snapshot(_snapshot(), page_id)


@contextmanager
def _dialog(
    state: CoreOverlayToolsState,
    *,
    pointer_mapping_enabled: bool = True,
) -> Iterator[CoreOverlayToolsDialog]:
    app = _application()
    dialog = CoreOverlayToolsDialog(
        state,
        page_transform=PageCoordinateTransform(),
        pointer_mapping_enabled=pointer_mapping_enabled,
    )
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[WidgetT: QWidget](
    dialog: CoreOverlayToolsDialog,
    widget_type: type[WidgetT],
    name: str,
) -> WidgetT:
    widget = dialog.findChild(widget_type, name)
    assert widget is not None
    return widget


def test_every_released_overlay_kind_is_listed_selected_and_geometry_exposed() -> None:
    observed: set[OverlayKind] = set()
    for page_id in (PAGE_ONE, PAGE_THREE):
        state = _state(page_id)
        with _dialog(state) as dialog:
            overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
            left = _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin")
            top = _widget(dialog, QDoubleSpinBox, "coreOverlayTopSpin")
            width = _widget(dialog, QDoubleSpinBox, "coreOverlayWidthSpin")
            height = _widget(dialog, QDoubleSpinBox, "coreOverlayHeightSpin")
            capability = _widget(dialog, QLabel, "overlayEditorSelectionCapability")
            remove = _widget(dialog, QPushButton, "removeCoreOverlayButton")
            assert overlay_list.count() == len(state.editor_records)
            for row, expected in enumerate(state.editor_records):
                overlay_list.setCurrentRow(row)
                _application().processEvents()
                selected = dialog.state.selected_record
                assert selected is not None and selected.overlay_id == expected.overlay_id
                observed.add(selected.kind)
                assert (left.value(), top.value(), width.value(), height.value()) == (
                    expected.box.x / 10_000,
                    expected.box.y / 10_000,
                    expected.box.width / 10_000,
                    expected.box.height / 10_000,
                )
                assert expected.kind.value.replace("_", " ").split()[0] in (
                    overlay_list.item(row).text().casefold()
                )
                if expected.kind is OverlayKind.SIGNATURE_IMAGE:
                    assert "case-asset cleanup stays explicit" in capability.text()
                    assert not remove.isEnabled()
                elif expected.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
                    assert "ordinary visual mark" in capability.text()
                    assert "not permanent removal" in capability.text()
                    assert not remove.isEnabled()
                else:
                    assert remove.isEnabled()
    assert observed == set(OverlayKind)


def test_every_released_overlay_kind_has_keyboard_move_and_resize_buttons() -> None:
    observed: set[OverlayKind] = set()
    for page_id in (PAGE_ONE, PAGE_THREE):
        base = _state(page_id)
        for record in base.editor_records:
            with _dialog(base.select(record.overlay_id)) as dialog:
                fine = _widget(dialog, QCheckBox, "overlayEditorFineStepCheck")
                fine.setFocus(Qt.FocusReason.OtherFocusReason)
                QTest.keyClick(fine, Qt.Key.Key_Space)
                assert fine.isChecked()
                move = _widget(dialog, QPushButton, "overlayEditorMoveRightButton")
                move.setFocus(Qt.FocusReason.OtherFocusReason)
                QTest.keyClick(move, Qt.Key.Key_Space)
                resize = _widget(dialog, QPushButton, "overlayEditorGrowHeightButton")
                resize.setFocus(Qt.FocusReason.OtherFocusReason)
                QTest.keyClick(resize, Qt.Key.Key_Space)
                _application().processEvents()
                selected = dialog.state.selected_record
                assert selected is not None
                assert selected.kind is record.kind
                assert selected.box.x == record.box.x + 1_000
                assert selected.box.height == record.box.height + 1_000
                assert selected.payload == record.payload
                observed.add(selected.kind)
    assert observed == set(OverlayKind)


def test_zoom_pan_and_guide_visibility_never_change_normalized_intent() -> None:
    state = _state().select(_state().editor_records[0].overlay_id)
    with _dialog(state) as dialog:
        before_history = dialog.state.history
        before_box = dialog.state.selected_record.box if dialog.state.selected_record else None
        changes: list[object] = []
        dialog.draft_changed.connect(changes.append)
        zoom = _widget(dialog, QComboBox, "overlayEditorZoomCombo")
        zoom.setCurrentIndex(zoom.findData(400))
        _widget(dialog, QPushButton, "overlayEditorPanRightButton").click()
        _widget(dialog, QPushButton, "overlayEditorPanDownButton").click()
        _widget(dialog, QPushButton, "overlayEditorPanCenterButton").click()
        _widget(dialog, QPushButton, "overlayEditorFitPageButton").click()
        show_guides = _widget(dialog, QCheckBox, "overlayEditorShowGuidesCheck")
        show_guides.setChecked(False)
        show_guides.setChecked(True)
        _application().processEvents()

        assert dialog.state.history is before_history
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box == before_box
        assert changes == []
        status = _widget(dialog, QLabel, "coreOverlayToolsStatus")
        assert "Saved overlay positions" in status.text()


def test_named_fine_controls_snap_and_undo_preserve_exact_history() -> None:
    initial = _state().select(_state().editor_records[0].overlay_id)
    initial = initial.replace_selected_box(NormalizedPageBox(123_456, 234_567, 300_000, 100_000))
    with _dialog(initial) as dialog:
        spacing = _widget(dialog, QComboBox, "overlayEditorGuideSpacingCombo")
        spacing.setCurrentIndex(spacing.findData(50_000))
        _widget(dialog, QPushButton, "overlayEditorSnapSelectedButton").click()
        snapped = dialog.state.selected_record
        assert snapped is not None
        assert snapped.box == NormalizedPageBox(100_000, 250_000, 300_000, 100_000)
        feedback = _widget(dialog, QLabel, "overlayEditorSnapFeedback")
        assert "5 percent dashed guides" in feedback.text()

        fine = _widget(dialog, QCheckBox, "overlayEditorFineStepCheck")
        fine.setChecked(True)
        _widget(dialog, QPushButton, "overlayEditorMoveRightButton").click()
        moved = dialog.state.selected_record
        assert moved is not None and moved.box.x == 101_000
        _widget(dialog, QPushButton, "undoCoreOverlayButton").click()
        restored = dialog.state.selected_record
        assert restored is not None and restored.box.x == 100_000


def test_deskewed_schematic_refuses_pointer_editing_but_keeps_exact_controls() -> None:
    state = _state().select(_state().editor_records[0].overlay_id)
    with _dialog(state, pointer_mapping_enabled=False) as dialog:
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        before = dialog.state.history
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
        _application().processEvents()
        status = _widget(dialog, QLabel, "coreOverlayToolsStatus")
        assert "deskewed preview" in status.text()
        assert dialog.state.history is before
        assert canvas.focusPolicy() is Qt.FocusPolicy.NoFocus
        assert "complete keyboard path" in canvas.accessibleDescription()
        assert _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin").isEnabled()


def test_pointer_drag_commits_one_geometry_command_and_list_selection_stays_native() -> None:
    base = _state()
    record = base.editor_records[0]
    selected = base.select(record.overlay_id)
    with _dialog(selected) as dialog:
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        screen = canvas._screen_box_for(record)
        assert screen is not None
        start = QPoint(
            round((screen.left + screen.right) / 2), round((screen.top + screen.bottom) / 2)
        )
        end = QPoint(start.x() + 24, start.y() + 18)
        before = dialog.state.history.overlays
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end, delay=1)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        _application().processEvents()

        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box != record.box
        assert overlay_list.currentItem() is not None
        assert overlay_list.currentItem().data(Qt.ItemDataRole.UserRole) == record.overlay_id
        undone = dialog.state.undo()
        assert undone.history.overlays == before


@pytest.mark.parametrize("drag_y", (0, 18), ids=("stationary-click", "vertical-drag"))
def test_selecting_saved_text_keeps_pointer_delta_stable_through_editor_reflow(
    drag_y: int,
) -> None:
    base = _state()
    record = base.editor_records[0]
    assert base.selected_overlay_id is None and record.kind is OverlayKind.TEXT
    with _dialog(base) as dialog:
        dialog.resize(1_002, 704)
        _application().processEvents()
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        text_row = _widget(dialog, QWidget, "overlayEditorValueRow")
        screen = canvas._screen_box_for(record)
        assert screen is not None and not text_row.isVisible()
        start = QPoint(
            round((screen.left + screen.right) / 2), round((screen.top + screen.bottom) / 2)
        )
        assert canvas.visibleRegion().contains(start)
        initial_origin = canvas.mapToGlobal(QPoint())
        initial_page = canvas._page_rect()
        press_global = canvas.mapToGlobal(start)
        end_global = press_global + QPoint(0, drag_y)
        start_normalized = canvas._normalized_point(QPointF(start))
        end_normalized = canvas._normalized_point(QPointF(start + QPoint(0, drag_y)))
        expected = NormalizedPageBox(
            record.box.x,
            record.box.y + end_normalized.y - start_normalized.y,
            record.box.width,
            record.box.height,
        )

        def send_pointer(
            kind: QEvent.Type,
            global_point: QPoint,
            button: Qt.MouseButton,
            buttons: Qt.MouseButton,
        ) -> None:
            event = QMouseEvent(
                kind,
                QPointF(canvas.mapFromGlobal(global_point)),
                QPointF(global_point),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(canvas, event)

        left = Qt.MouseButton.LeftButton
        none = Qt.MouseButton.NoButton
        send_pointer(QEvent.Type.MouseButtonPress, press_global, left, left)
        wait_for_geometry(
            lambda: text_row.isVisible() and canvas.mapToGlobal(QPoint()).y() > initial_origin.y()
        )
        assert canvas._page_rect() == initial_page
        assert dialog.state.selected_overlay_id == record.overlay_id
        assert dialog.state.history == base.history
        if drag_y:
            send_pointer(QEvent.Type.MouseMove, end_global, none, left)
        send_pointer(QEvent.Type.MouseButtonRelease, end_global, left, none)

        selected = dialog.state.selected_record
        assert selected is not None
        assert selected.box == expected
        assert selected.payload == record.payload
        assert QWidget.mouseGrabber() is not canvas
        if drag_y:
            assert dialog.state.undo().history.overlays == base.history.overlays
        else:
            assert dialog.state.history == base.history


def test_resize_handle_click_without_drag_is_a_no_op_at_every_zoom_and_rotation() -> None:
    app = _application()
    base = _state()
    initial = base.select(base.editor_records[0].overlay_id).replace_selected_box(
        NormalizedPageBox(200_000, 200_000, 1_000, 1_000)
    )
    record = initial.selected_record
    assert record is not None
    transforms = (
        PageCoordinateTransform(),
        PageCoordinateTransform(manifest_rotation=ClockwiseRotation.DEGREES_90),
    )

    for transform in transforms:
        for zoom in (25, 100, 400):
            canvas = OverlayEditorCanvas(transform=transform)
            changes: list[tuple[object, object, bool]] = []
            canvas.box_change_requested.connect(
                lambda overlay_id, box, snapped, target=changes: target.append(
                    (overlay_id, box, snapped)
                )
            )
            canvas.set_zoom_percent(zoom)
            canvas.set_records((record,), record.overlay_id)
            canvas.show()
            app.processEvents()
            try:
                screen = canvas._screen_box_for(record)
                assert screen is not None
                handle = canvas._resize_handle_rect(screen)
                point = QPoint(round(handle.left() + 2), round(handle.top() + 2))
                QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
                started_drag = canvas._drag
                assert started_drag is not None and started_drag.resize
                QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point)
                app.processEvents()

                assert changes == []
                assert canvas._drag is None
                assert QWidget.mouseGrabber() is not canvas
            finally:
                if QWidget.mouseGrabber() is canvas:
                    canvas.releaseMouse()
                canvas.hide()
                canvas.deleteLater()
                app.processEvents()


def test_snap_enabled_pointer_click_without_drag_does_not_change_history() -> None:
    base = _state()
    initial = base.select(base.editor_records[0].overlay_id).replace_selected_box(
        NormalizedPageBox(123_456, 234_567, 300_000, 100_000)
    )
    with _dialog(initial) as dialog:
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        snap = _widget(dialog, QCheckBox, "overlayEditorSnapCheck")
        snap.setChecked(True)
        record = dialog.state.selected_record
        assert record is not None
        screen = canvas._screen_box_for(record)
        assert screen is not None
        point = QPoint(
            round((screen.left + screen.right) / 2),
            round((screen.top + screen.bottom) / 2),
        )
        before_history = dialog.state.history
        emitted: list[tuple[object, object, bool]] = []
        canvas.box_change_requested.connect(
            lambda overlay_id, box, snapped: emitted.append((overlay_id, box, snapped))
        )

        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        _application().processEvents()

        assert dialog.state.history is before_history
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box == record.box
        assert emitted == []


def test_state_refresh_and_zoom_cancel_drag_and_release_mouse_grab() -> None:
    app = _application()
    base = _state()
    record = base.editor_records[0]

    for cancellation in ("state", "zoom"):
        canvas = OverlayEditorCanvas(transform=PageCoordinateTransform())
        canvas.set_records((record,), record.overlay_id)
        canvas.show()
        app.processEvents()
        try:
            screen = canvas._screen_box_for(record)
            assert screen is not None
            point = QPoint(
                round((screen.left + screen.right) / 2),
                round((screen.top + screen.bottom) / 2),
            )
            QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
            started_drag = canvas._drag
            assert started_drag is not None
            assert QWidget.mouseGrabber() is canvas

            if cancellation == "state":
                canvas.set_records((record,), record.overlay_id)
            else:
                canvas.set_zoom_percent(200)
            app.processEvents()

            assert canvas._drag is None
            assert QWidget.mouseGrabber() is not canvas
            QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point)
        finally:
            if QWidget.mouseGrabber() is canvas:
                canvas.releaseMouse()
            canvas.hide()
            canvas.deleteLater()
            app.processEvents()


def test_extreme_preview_and_schematic_aspects_are_preserved() -> None:
    app = _application()
    dimensions = (
        ((1_000, 100), (680, 68)),
        ((100, 1_000), (68, 680)),
    )

    for (width, height), expected_page_size in dimensions:
        preview = QPixmap(width, height)
        preview.fill(Qt.GlobalColor.white)
        for canvas in (
            OverlayEditorCanvas(transform=PageCoordinateTransform(), preview=preview),
            OverlayEditorCanvas(
                transform=PageCoordinateTransform(),
                display_size=RenderedImageSize(width, height),
                pointer_mapping_enabled=False,
            ),
        ):
            try:
                page = canvas._page_rect()
                assert (round(page.width()), round(page.height())) == expected_page_size
            finally:
                canvas.deleteLater()
                app.processEvents()


def test_partial_and_outside_crop_projection_never_replace_saved_box_with_visible_clip() -> None:
    base = _state()
    record = base.editor_records[0]
    partial_box = NormalizedPageBox(100_000, 100_000, 350_000, 100_000)
    selected = base.select(record.overlay_id).replace_selected_box(partial_box)
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 50_000, 600_000, 500_000)
    )
    app = _application()
    dialog = CoreOverlayToolsDialog(selected, page_transform=transform)
    dialog.show()
    app.processEvents()
    try:
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        current = dialog.state.selected_record
        assert current is not None
        screen = canvas._screen_box_for(current)
        assert screen is not None
        start = QPoint(
            round((screen.left + screen.right) / 2), round((screen.top + screen.bottom) / 2)
        )
        end = QPoint(start.x() + 20, start.y())
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end, delay=1)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        moved = dialog.state.selected_record
        assert moved is not None
        assert moved.box.width == partial_box.width
        assert moved.box.height == partial_box.height
        assert moved.box.x != transform.manifest_crop.x

        outside = dialog.state.replace_selected_box(
            NormalizedPageBox(850_000, 700_000, 100_000, 100_000)
        )
        dialog._publish_state(outside)
        outside_record = outside.selected_record
        assert outside_record is not None
        assert canvas._screen_box_for(outside_record) is None
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        assert overlay_list.currentItem() is not None
        assert "left 85.0000 percent" in overlay_list.currentItem().text()
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()
