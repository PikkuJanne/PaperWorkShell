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
    QComboBox,
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
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PRIVATE_VALUE = "PRIVATE-FORM-VALUE-ACCESSIBILITY-0304"


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


@contextmanager
def _dialog() -> Iterator[CoreOverlayToolsDialog]:
    app = _application()
    dialog = CoreOverlayToolsDialog(_state_with_private_text())
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
        assert dialog.accessibleName() == "Edit visual form marks for selected page"
        assert "No pointer is required" in dialog.accessibleDescription()

        named_controls = (
            (QComboBox, "newOverlayTypeCombo"),
            (QPushButton, "addCoreOverlayButton"),
            (QListWidget, "coreOverlayList"),
            (QLineEdit, "coreOverlayValueEdit"),
            (QSpinBox, "coreOverlayFontSizeSpin"),
            (QComboBox, "coreOverlayColorCombo"),
            (QSpinBox, "coreOverlayLeftSpin"),
            (QSpinBox, "coreOverlayTopSpin"),
            (QSpinBox, "coreOverlayWidthSpin"),
            (QSpinBox, "coreOverlayHeightSpin"),
            (QPushButton, "applyCoreOverlayPropertiesButton"),
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
            _widget(dialog, QSpinBox, "coreOverlayLeftSpin"),
            QAccessible.Role.SpinBox,
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
            coordinate = _widget(dialog, QSpinBox, name)
            assert coordinate.minimum() == minimum
            assert coordinate.maximum() == 1_000_000
            assert "millionths" in coordinate.accessibleName()


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


def test_tab_order_reaches_add_list_properties_and_edit_actions() -> None:
    app = _application()
    with _dialog() as dialog:
        combo = _widget(dialog, QComboBox, "newOverlayTypeCombo")
        expected = (
            _widget(dialog, QPushButton, "addCoreOverlayButton"),
            _widget(dialog, QListWidget, "coreOverlayList"),
            _widget(dialog, QLineEdit, "coreOverlayValueEdit"),
            _widget(dialog, QSpinBox, "coreOverlayFontSizeSpin"),
            _widget(dialog, QComboBox, "coreOverlayColorCombo"),
            _widget(dialog, QSpinBox, "coreOverlayLeftSpin"),
            _widget(dialog, QSpinBox, "coreOverlayTopSpin"),
            _widget(dialog, QSpinBox, "coreOverlayWidthSpin"),
            _widget(dialog, QSpinBox, "coreOverlayHeightSpin"),
            _widget(dialog, QPushButton, "applyCoreOverlayPropertiesButton"),
            _widget(dialog, QPushButton, "removeCoreOverlayButton"),
            _widget(dialog, QPushButton, "undoCoreOverlayButton"),
            _widget(dialog, QPushButton, "applyFormMarksButton"),
            _widget(dialog, QPushButton, "cancelFormMarksButton"),
        )
        current: QWidget = combo
        current.setFocus(Qt.FocusReason.TabFocusReason)
        for following in expected:
            QTest.keyClick(current, Qt.Key.Key_Tab)
            app.processEvents()
            assert following.hasFocus()
            current = following


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
                "applyCoreOverlayPropertiesButton",
            )
            combo.setFocus(Qt.FocusReason.TabFocusReason)
            for _ in range(10):
                current = app.focusWidget()
                assert current is not None
                QTest.keyClick(current, Qt.Key.Key_Tab)
                app.processEvents()
            assert bottom_control.hasFocus()
            assert scroll.verticalScrollBar().value() > before
    finally:
        app.setFont(original_font)
