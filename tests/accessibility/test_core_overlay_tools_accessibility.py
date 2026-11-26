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
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox, PageCoordinateTransform
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PRIVATE_VALUE = "PRIVATE-FORM-VALUE-ACCESSIBILITY-0304"
PAGE_ONE = UUID("30000000-0000-4000-8000-000000000001")
PAGE_THREE = UUID("30000000-0000-4000-8000-000000000003")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state_with_private_text() -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8")),
    )
    document["assets"] = []
    document["overlays"] = []
    case_id = UUID(cast(str, document["case_id"]))
    snapshot = ManifestSnapshot(
        case_id,
        2,
        cast(int, document["revision"]),
        document,
        "e" * 64,
        "core_overlay_accessibility_cas",
    )
    page_id = UUID(cast(list[str], document["page_sequence"])[0])
    return CoreOverlayToolsState.from_snapshot(snapshot, page_id).create(
        OverlayKind.TEXT,
        value=PRIVATE_VALUE,
    )


def _representative_state(page_id: UUID) -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8")),
    )
    snapshot = ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "d" * 64,
        "overlay_editor_accessibility_representative",
    )
    return CoreOverlayToolsState.from_snapshot(snapshot, page_id)


@contextmanager
def _dialog(
    state: CoreOverlayToolsState | None = None,
    *,
    page_transform: PageCoordinateTransform | None = None,
    pointer_mapping_enabled: bool = True,
) -> Iterator[CoreOverlayToolsDialog]:
    app = _application()
    dialog = CoreOverlayToolsDialog(
        state or _state_with_private_text(),
        page_transform=page_transform,
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


def _assert_role(widget: QWidget, role: QAccessible.Role) -> None:
    interface = QAccessible.queryAccessibleInterface(widget)
    assert interface is not None
    assert interface.role() is role


def test_dialog_exposes_named_roles_descriptions_and_exact_coordinate_units() -> None:
    with _dialog() as dialog:
        assert dialog.accessibleName() == "Edit visual overlays for selected page"
        assert "No pointer is required" in dialog.accessibleDescription()

        named_controls = (
            (QComboBox, "overlayEditorZoomCombo"),
            (QPushButton, "overlayEditorFitPageButton"),
            (QCheckBox, "overlayEditorShowGuidesCheck"),
            (QCheckBox, "overlayEditorSnapCheck"),
            (QComboBox, "overlayEditorGuideSpacingCombo"),
            (QPushButton, "overlayEditorSnapSelectedButton"),
            (QPushButton, "overlayEditorPanLeftButton"),
            (QPushButton, "overlayEditorPanRightButton"),
            (QPushButton, "overlayEditorPanUpButton"),
            (QPushButton, "overlayEditorPanDownButton"),
            (QPushButton, "overlayEditorPanCenterButton"),
            (QComboBox, "newOverlayTypeCombo"),
            (QPushButton, "addCoreOverlayButton"),
            (QListWidget, "coreOverlayList"),
            (QLineEdit, "coreOverlayValueEdit"),
            (QSpinBox, "coreOverlayFontSizeSpin"),
            (QComboBox, "coreOverlayColorCombo"),
            (QDoubleSpinBox, "coreOverlayLeftSpin"),
            (QDoubleSpinBox, "coreOverlayTopSpin"),
            (QDoubleSpinBox, "coreOverlayWidthSpin"),
            (QDoubleSpinBox, "coreOverlayHeightSpin"),
            (QCheckBox, "overlayEditorFineStepCheck"),
            (QPushButton, "overlayEditorMoveLeftButton"),
            (QPushButton, "overlayEditorGrowWidthButton"),
            (QPushButton, "removeCoreOverlayButton"),
            (QPushButton, "undoCoreOverlayButton"),
            (QPushButton, "redoCoreOverlayButton"),
            (QPushButton, "applyFormMarksButton"),
            (QPushButton, "cancelFormMarksButton"),
            (QLabel, "coreOverlayToolsStatus"),
            (QScrollArea, "coreOverlayToolsScrollArea"),
        )
        for widget_type, name in named_controls:
            control = _widget(dialog, widget_type, name)
            assert control.accessibleName()
            assert control.accessibleDescription()

        _assert_role(dialog, QAccessible.Role.Dialog)
        _assert_role(
            _widget(dialog, QComboBox, "newOverlayTypeCombo"),
            QAccessible.Role.ComboBox,
        )
        _assert_role(
            _widget(dialog, QListWidget, "coreOverlayList"),
            QAccessible.Role.List,
        )
        _assert_role(
            _widget(dialog, QLineEdit, "coreOverlayValueEdit"),
            QAccessible.Role.EditableText,
        )
        _assert_role(
            _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin"),
            QAccessible.Role.SpinBox,
        )
        _assert_role(
            _widget(dialog, QCheckBox, "overlayEditorSnapCheck"),
            QAccessible.Role.CheckBox,
        )
        _assert_role(
            _widget(dialog, QPushButton, "applyFormMarksButton"),
            QAccessible.Role.Button,
        )

        for name, minimum in (
            ("coreOverlayLeftSpin", 0),
            ("coreOverlayTopSpin", 0),
            ("coreOverlayWidthSpin", 1),
            ("coreOverlayHeightSpin", 1),
        ):
            coordinate = _widget(dialog, QDoubleSpinBox, name)
            assert coordinate.minimum() == minimum / 10_000
            assert coordinate.maximum() == 100
            assert "percent" in coordinate.accessibleName()


def test_native_selection_and_value_interfaces_expose_state_and_exact_ranges() -> None:
    with _dialog() as dialog:
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        list_interface = QAccessible.queryAccessibleInterface(overlay_list)
        assert list_interface is not None
        selection = list_interface.selectionInterface()
        assert selection is not None
        assert selection.selectedItemCount() == 1
        selected_item = selection.selectedItem(0)
        assert selected_item is not None
        assert selected_item.state().selected

        left = _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin")
        left_interface = QAccessible.queryAccessibleInterface(left)
        assert left_interface is not None
        value = left_interface.valueInterface()
        assert value is not None
        assert value.minimumValue() == 0
        assert value.maximumValue() == 100
        assert value.minimumStepSize() == 0.1
        assert value.currentValue() == left.value()
        assert "Current value:" in left.accessibleDescription()
        assert "percent" in left.accessibleDescription()

        snap = _widget(dialog, QCheckBox, "overlayEditorSnapCheck")
        snap.setChecked(True)
        snap_interface = QAccessible.queryAccessibleInterface(snap)
        assert snap_interface is not None
        assert snap_interface.state().checked

        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        assert canvas.focusPolicy() is Qt.FocusPolicy.NoFocus
        assert "Visual preview only" in canvas.accessibleDescription()
        _assert_role(canvas, QAccessible.Role.Graphic)


def test_list_rows_and_announced_status_remain_content_free_without_dragging() -> None:
    app = _application()
    with _dialog() as dialog:
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        value = _widget(dialog, QLineEdit, "coreOverlayValueEdit")
        status = _widget(dialog, QLabel, "coreOverlayToolsStatus")

        assert value.text() == PRIVATE_VALUE
        assert overlay_list.count() == 1
        assert not overlay_list.dragEnabled()
        assert not overlay_list.acceptDrops()
        item = overlay_list.item(0)
        row_surfaces = (
            item.text(),
            cast(str, item.data(Qt.ItemDataRole.AccessibleTextRole)),
            cast(str, item.data(Qt.ItemDataRole.AccessibleDescriptionRole)),
        )
        assert all(PRIVATE_VALUE not in surface for surface in row_surfaces)

        overlay_list.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(
            overlay_list,
            Qt.Key.Key_Right,
            Qt.KeyboardModifier.AltModifier,
        )
        app.processEvents()

        assert status.text() == "Text visual mark moved right."
        assert status.accessibleDescription() == status.text()
        assert PRIVATE_VALUE not in status.text()
        assert PRIVATE_VALUE not in overlay_list.item(0).text()


def test_tab_order_completes_two_forward_and_reverse_cycles_with_wrap() -> None:
    app = _application()
    with _dialog() as dialog:
        zoom = _widget(dialog, QComboBox, "overlayEditorZoomCombo")
        zoom.setCurrentIndex(zoom.findData(300))
        canvas_scroll = _widget(dialog, QScrollArea, "overlayEditorCanvasScroll")
        for bar in (canvas_scroll.horizontalScrollBar(), canvas_scroll.verticalScrollBar()):
            bar.setValue((bar.minimum() + bar.maximum()) // 2)
        app.processEvents()

        ordered = (
            _widget(dialog, QPushButton, "addTextButton"),
            _widget(dialog, QPushButton, "addCheckmarkButton"),
            _widget(dialog, QLineEdit, "coreOverlayValueEdit"),
            zoom,
            _widget(dialog, QPushButton, "overlayEditorZoomOutButton"),
            _widget(dialog, QPushButton, "overlayEditorZoomInButton"),
            _widget(dialog, QPushButton, "overlayEditorZoom100Button"),
            _widget(dialog, QPushButton, "overlayEditorFitPageButton"),
            _widget(dialog, QCheckBox, "overlayEditorShowGuidesCheck"),
            _widget(dialog, QCheckBox, "overlayEditorSnapCheck"),
            _widget(dialog, QComboBox, "overlayEditorGuideSpacingCombo"),
            _widget(dialog, QPushButton, "overlayEditorSnapSelectedButton"),
            _widget(dialog, QPushButton, "overlayEditorPanLeftButton"),
            _widget(dialog, QPushButton, "overlayEditorPanRightButton"),
            _widget(dialog, QPushButton, "overlayEditorPanUpButton"),
            _widget(dialog, QPushButton, "overlayEditorPanDownButton"),
            _widget(dialog, QPushButton, "overlayEditorPanCenterButton"),
            _widget(dialog, QComboBox, "newOverlayTypeCombo"),
            _widget(dialog, QPushButton, "addCoreOverlayButton"),
            _widget(dialog, QListWidget, "coreOverlayList"),
            _widget(dialog, QSpinBox, "coreOverlayFontSizeSpin"),
            _widget(dialog, QComboBox, "coreOverlayColorCombo"),
            _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin"),
            _widget(dialog, QDoubleSpinBox, "coreOverlayTopSpin"),
            _widget(dialog, QDoubleSpinBox, "coreOverlayWidthSpin"),
            _widget(dialog, QDoubleSpinBox, "coreOverlayHeightSpin"),
            _widget(dialog, QCheckBox, "overlayEditorFineStepCheck"),
            _widget(dialog, QPushButton, "overlayEditorMoveLeftButton"),
            _widget(dialog, QPushButton, "overlayEditorMoveRightButton"),
            _widget(dialog, QPushButton, "overlayEditorMoveUpButton"),
            _widget(dialog, QPushButton, "overlayEditorMoveDownButton"),
            _widget(dialog, QPushButton, "overlayEditorShrinkWidthButton"),
            _widget(dialog, QPushButton, "overlayEditorGrowWidthButton"),
            _widget(dialog, QPushButton, "overlayEditorShrinkHeightButton"),
            _widget(dialog, QPushButton, "overlayEditorGrowHeightButton"),
            _widget(dialog, QPushButton, "removeCoreOverlayButton"),
            _widget(dialog, QPushButton, "undoCoreOverlayButton"),
            _widget(dialog, QPushButton, "redoCoreOverlayButton"),
            _widget(dialog, QPushButton, "applyFormMarksButton"),
            _widget(dialog, QPushButton, "cancelFormMarksButton"),
            _widget(dialog, QLabel, "coreOverlayToolsStatus"),
        )
        active = tuple(
            widget
            for widget in ordered
            if widget.isVisible()
            and widget.isEnabled()
            and widget.focusPolicy() is not Qt.FocusPolicy.NoFocus
        )
        assert active[0].objectName() == "addTextButton"
        assert active[-1].objectName() == "coreOverlayToolsStatus"
        for required_name in (
            "overlayEditorFitPageButton",
            "overlayEditorShowGuidesCheck",
            "overlayEditorSnapCheck",
            "overlayEditorGuideSpacingCombo",
            "overlayEditorSnapSelectedButton",
            "overlayEditorPanLeftButton",
            "overlayEditorPanRightButton",
            "overlayEditorPanUpButton",
            "overlayEditorPanDownButton",
            "overlayEditorPanCenterButton",
            "newOverlayTypeCombo",
            "coreOverlayList",
            "applyFormMarksButton",
            "cancelFormMarksButton",
        ):
            assert any(widget.objectName() == required_name for widget in active)

        active[0].setFocus(Qt.FocusReason.TabFocusReason)
        for step in range(len(active) * 2):
            expected = active[step % len(active)]
            assert expected.hasFocus(), (step, expected.objectName(), app.focusWidget())
            focused = app.focusWidget()
            assert focused is not None
            QTest.keyClick(focused, Qt.Key.Key_Tab)
            app.processEvents()
        assert active[0].hasFocus()

        for step in range(len(active) * 2):
            expected = active[(-step) % len(active)]
            assert expected.hasFocus(), (step, expected.objectName(), app.focusWidget())
            focused = app.focusWidget()
            assert focused is not None
            QTest.keyClick(focused, Qt.Key.Key_Backtab)
            app.processEvents()
        assert active[0].hasFocus()


def test_dynamic_property_and_row_accessibility_matches_each_overlay_capability() -> None:
    app = _application()
    page_one = _representative_state(PAGE_ONE)
    with _dialog(page_one) as dialog:
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        properties = _widget(dialog, QGroupBox, "coreOverlayPropertiesGroup")
        value = _widget(dialog, QLineEdit, "coreOverlayValueEdit")
        color = _widget(dialog, QComboBox, "coreOverlayColorCombo")
        apply = _widget(dialog, QPushButton, "applyCoreOverlayPropertiesButton")
        remove = _widget(dialog, QPushButton, "removeCoreOverlayButton")
        capability = _widget(dialog, QLabel, "overlayEditorSelectionCapability")

        rows = {record.kind: row for row, record in enumerate(page_one.editor_records)}
        overlay_list.setCurrentRow(rows[OverlayKind.TEXT])
        app.processEvents()
        text_item = overlay_list.currentItem()
        assert text_item is not None
        assert "Value, formatting, position, and size" in cast(
            str,
            text_item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
        )
        assert "value, formatting" in properties.accessibleDescription()
        assert "value, formatting, position, and size" in apply.accessibleDescription()
        assert value.isVisible()
        assert color.isVisible()
        assert remove.isEnabled()
        assert "Remove the selected core visual mark" in remove.accessibleDescription()

        value.setFocus(Qt.FocusReason.TabFocusReason)
        overlay_list.setCurrentRow(rows[OverlayKind.SIGNATURE_IMAGE])
        app.processEvents()
        signature_item = overlay_list.currentItem()
        assert signature_item is not None
        assert not value.isVisible()
        assert not color.isVisible()
        focused = app.focusWidget()
        assert focused is not None and focused.isVisible() and focused.isEnabled()
        assert "not a digital signature" in cast(
            str,
            signature_item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
        )
        assert "not a digital or qualified electronic signature" in capability.text()
        assert "only" in properties.accessibleDescription()
        assert "visual content properties are read-only" in properties.accessibleDescription()
        assert apply.text() == "Apply position and size"
        assert apply.accessibleName() == "Apply selected visual-mark position and size"
        assert "Apply only the exact position and size" in apply.accessibleDescription()
        assert not remove.isEnabled()
        assert remove.accessibleName() == "Signature image removal unavailable in this editor"
        assert "explicit" in remove.accessibleDescription()

        overlay_list.setCurrentRow(rows[OverlayKind.CHECKMARK])
        app.processEvents()
        checkmark_item = overlay_list.currentItem()
        assert checkmark_item is not None
        assert "Color, position, and size" in cast(
            str,
            checkmark_item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
        )
        assert "color" in properties.accessibleDescription()
        assert "color, position, and size" in apply.accessibleDescription()
        assert not value.isVisible()
        assert color.isVisible()
        assert remove.isEnabled()

    page_three = _representative_state(PAGE_THREE)
    with _dialog(page_three) as dialog:
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        properties = _widget(dialog, QGroupBox, "coreOverlayPropertiesGroup")
        apply = _widget(dialog, QPushButton, "applyCoreOverlayPropertiesButton")
        remove = _widget(dialog, QPushButton, "removeCoreOverlayButton")
        capability = _widget(dialog, QLabel, "overlayEditorSelectionCapability")
        rows = {record.kind: row for row, record in enumerate(page_three.editor_records)}

        for kind in (OverlayKind.HIGHLIGHT, OverlayKind.SHAPE):
            overlay_list.setCurrentRow(rows[kind])
            app.processEvents()
            item = overlay_list.currentItem()
            assert item is not None
            row_text = cast(str, item.data(Qt.ItemDataRole.AccessibleTextRole))
            row_description = cast(
                str,
                item.data(Qt.ItemDataRole.AccessibleDescriptionRole),
            )
            assert "not permanent removal" in row_description
            assert "not permanent removal" in capability.text()
            assert "visual content properties are read-only" in properties.accessibleDescription()
            assert apply.text() == "Apply position and size"
            assert not remove.isEnabled()
            assert "removal unavailable in this editor" in remove.accessibleName()
            if kind is OverlayKind.SHAPE:
                assert "Ellipse shape visual mark" in row_text
                assert "Ellipse shape visual mark" in capability.text()


def test_rows_and_selection_expose_full_partial_and_outside_crop_states() -> None:
    app = _application()
    state = _representative_state(PAGE_ONE)
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 50_000, 600_000, 500_000)
    )
    with _dialog(state, page_transform=transform) as dialog:
        overlay_list = _widget(dialog, QListWidget, "coreOverlayList")
        capability = _widget(dialog, QLabel, "overlayEditorSelectionCapability")
        rows = {record.kind: row for row, record in enumerate(state.editor_records)}
        expected = {
            OverlayKind.TEXT: "Partially shown in the preview",
            OverlayKind.DATE: "Fully shown inside the active crop",
            OverlayKind.SIGNATURE_IMAGE: "Outside the active crop and not shown in the preview",
        }
        for kind, visibility in expected.items():
            overlay_list.setCurrentRow(rows[kind])
            app.processEvents()
            item = overlay_list.currentItem()
            assert item is not None
            assert visibility in item.text()
            assert visibility in cast(str, item.data(Qt.ItemDataRole.AccessibleTextRole))
            assert visibility in capability.accessibleDescription()


def test_enlarged_text_keeps_fixed_actions_visible_and_editor_scrollable() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSizeF(max(24.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged_font)
    try:
        with _dialog() as dialog:
            dialog.resize(430, 300)
            app.processEvents()
            scroll = _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea")
            heading = _widget(dialog, QLabel, "coreOverlayToolsHeading")
            apply_button = _widget(dialog, QPushButton, "applyFormMarksButton")
            cancel_button = _widget(dialog, QPushButton, "cancelFormMarksButton")

            assert scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            assert heading.font().pointSizeF() >= enlarged_font.pointSizeF()
            for button in (apply_button, cancel_button):
                assert button.isVisible()
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))

            before = scroll.verticalScrollBar().value()
            combo = _widget(dialog, QComboBox, "newOverlayTypeCombo")
            bottom_control = _widget(
                dialog,
                QPushButton,
                "overlayEditorGrowHeightButton",
            )
            combo.setFocus(Qt.FocusReason.TabFocusReason)
            for _ in range(50):
                if bottom_control.hasFocus():
                    break
                current = app.focusWidget()
                assert current is not None
                QTest.keyClick(current, Qt.Key.Key_Tab)
                app.processEvents()
            assert bottom_control.hasFocus()
            assert scroll.verticalScrollBar().value() > before
    finally:
        app.setFont(original_font)


def test_enlarged_text_deskew_notice_uses_its_measured_wrapped_height() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSizeF(max(24.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged_font)
    canvas = OverlayEditorCanvas(
        transform=PageCoordinateTransform(),
        pointer_mapping_enabled=False,
    )
    canvas.set_guide_options(visible=False, snap=False, step_millionths=50_000)
    try:
        canvas.show()
        app.processEvents()
        page = canvas._page_rect()
        notice = (
            "Deskewed preview: use the exact fields or keyboard controls; pointer editing is off."
        )
        available_width = max(1, round(page.width() - 32))
        measured = canvas.fontMetrics().boundingRect(
            0,
            0,
            available_width,
            max(1, round(page.height() - 16)),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            notice,
        )
        assert measured.height() > 52

        image = canvas.grab().toImage()
        banner_height = min(max(1.0, page.height() - 16), measured.height() + 12)
        expected_bottom = round(page.top() + 8 + banner_height)
        left = round(page.left() + 8)
        right = round(page.right() - 8)

        def dark_runs(y: int) -> list[int]:
            runs: list[int] = []
            start: int | None = None
            for offset, x in enumerate(range(left, right + 1)):
                color = image.pixelColor(x, y)
                dark = color.red() < 80 and color.green() < 80 and color.blue() < 80
                if dark and start is None:
                    start = offset
                elif not dark and start is not None:
                    runs.append(offset - start)
                    start = None
            if start is not None:
                runs.append(right - left + 1 - start)
            return runs

        candidate_rows = (
            dark_runs(y)
            for y in range(max(0, expected_bottom - 2), min(image.height(), expected_bottom + 3))
        )
        assert any(len(runs) >= 25 and max(runs, default=0) <= 14 for runs in candidate_rows)
    finally:
        canvas.hide()
        canvas.deleteLater()
        app.processEvents()
        app.setFont(original_font)
