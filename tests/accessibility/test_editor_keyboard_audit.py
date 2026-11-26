"""PWS-0701 regression checks for editor focus and ordinary text keys."""

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

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
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
    QScrollArea,
    QSpinBox,
    QWidget,
)

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.app.scan_review import ScanReviewDraft
from paperworkshell.app.signature_images import SignatureImagePlacementDraft
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.overlays.models import OverlayKind, TextualOverlayPayload
from paperworkshell.overlays.signature_rasters import SignatureImagePreview
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog
from paperworkshell.ui.redaction_tools import RedactionToolsDialog
from paperworkshell.ui.scan_review import ScanReviewDialog
from paperworkshell.ui.signature_images import SignatureImagePlacementDialog

CASE_ID = UUID("70100000-0000-4000-8000-000000000001")
PAGE_ID = UUID("70100000-0000-4000-8000-000000000002")
SESSION_ID = UUID("70100000-0000-4000-8000-000000000003")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _shown[T: QDialog](dialog: T) -> Iterator[T]:
    app = _application()
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: QDialog, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def _overlay_state() -> CoreOverlayToolsState:
    root = Path(__file__).resolve().parents[2]
    document = cast(
        dict[str, object],
        json.loads(
            (root / "schemas/examples/case-manifest-v2-representative.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    document["assets"] = []
    document["overlays"] = []
    return CoreOverlayToolsState.from_snapshot(
        ManifestSnapshot(
            UUID(cast(str, document["case_id"])),
            2,
            cast(int, document["revision"]),
            document,
            "e" * 64,
            "keyboard_audit_manifest_snapshot_token",
        ),
        UUID(cast(list[str], document["page_sequence"])[0]),
    ).create(OverlayKind.TEXT, value="Synthetic keyboard text")


def test_pending_removal_mark_save_keeps_visible_focus_until_failure_or_success() -> None:
    app = _application()
    state = RedactionToolsState(CASE_ID, 1, PAGE_ID, SESSION_ID, ()).create()
    with _shown(RedactionToolsDialog(state)) as dialog:
        save = _widget(dialog, QPushButton, "saveRedactionMarksButton")
        status = _widget(dialog, QLabel, "redactionToolsStatus")
        save.setFocus()
        QTest.keyClick(save, Qt.Key.Key_Space)
        app.processEvents()
        assert dialog.save_pending
        assert status.isEnabled() and status.isVisible() and status.hasFocus()
        assert "Saving" in status.text()
        assert not _widget(dialog, QListWidget, "redactionRegionList").isEnabled()
        for key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape):
            QTest.keyClick(status, key)
            app.processEvents()
            assert status.hasFocus() and dialog.isVisible()
        assert dialog.save_failed("MANIFEST_SAVE_FAILED", "Synthetic failure")
        assert save.hasFocus() and save.isEnabled()
        QTest.keyClick(save, Qt.Key.Key_Space)
        app.processEvents()
        assert status.hasFocus() and dialog.save_pending
        assert dialog.finish_saved()
        assert dialog.result() == QDialog.DialogCode.Accepted


def test_visual_overlay_zoom_accepts_the_advertised_control_plus_shortcut() -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        overlays = _widget(dialog, QListWidget, "coreOverlayList")
        overlays.setFocus()
        before = canvas.zoom_percent
        QTest.keyClick(overlays, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert canvas.zoom_percent > before


def test_overlay_apply_keeps_focus_on_pending_status_then_reaches_retry() -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        apply = _widget(dialog, QPushButton, "applyFormMarksButton")
        status = _widget(dialog, QLabel, "coreOverlayToolsStatus")
        apply.setFocus()
        QTest.keyClick(apply, Qt.Key.Key_Space)
        app.processEvents()
        assert dialog.apply_pending and status.hasFocus()
        QTest.keyClick(status, Qt.Key.Key_Tab)
        app.processEvents()
        assert status.hasFocus()
        assert dialog.set_apply_failed("MANIFEST_SAVE_FAILED", "Synthetic failure")
        assert apply.hasFocus()
        assert "Changes are retained for retry" in status.accessibleName()


def test_scan_preview_completion_restores_focus_before_hiding_cancel_action() -> None:
    app = _application()
    intent = ImageTransformIntent()
    draft = ScanReviewDraft(CASE_ID, PAGE_ID, 1, intent, intent)
    with _shown(ScanReviewDialog(draft)) as dialog:
        assert dialog.set_preview_loading(draft)
        cancel = _widget(dialog, QPushButton, "scanReviewCancelPreviewButton")
        update = _widget(dialog, QPushButton, "scanReviewUpdatePreviewButton")
        cancel.setFocus()
        image = QImage(32, 24, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.white)
        assert dialog.set_original_image(draft, image)
        assert dialog.set_adjusted_image(draft, image)
        app.processEvents()
        assert not cancel.isVisible()
        assert update.isEnabled() and update.hasFocus()


def test_delete_and_text_undo_never_remove_or_undo_the_selected_visual_mark() -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        value = _widget(dialog, QLineEdit, "coreOverlayValueEdit")
        value.setFocus()
        value.selectAll()
        QTest.keyClicks(value, "ABC")
        QTest.keyClick(value, Qt.Key.Key_Home)
        QTest.keyClick(value, Qt.Key.Key_Delete)
        app.processEvents()
        assert value.text() == "BC"
        assert len(dialog.state.records) == 1
        QTest.keyClick(value, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert value.text() == "ABC"
        assert len(dialog.state.records) == 1


def _assert_focused_control_is_in_view(dialog: CoreOverlayToolsDialog, control: QWidget) -> None:
    scroll = _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea")
    viewport = scroll.viewport()
    content = scroll.widget()
    assert content is not None
    assert control.hasFocus()
    if not content.isAncestorOf(control):
        # Text/value entry stays fixed above the scrolling settings.
        assert dialog.rect().contains(control.mapTo(dialog, control.rect().center()))
        return
    if isinstance(control, QListWidget) and control.currentItem() is not None:
        position = control.viewport().mapTo(
            viewport, control.visualItemRect(control.currentItem()).center()
        )
    else:
        position = control.mapTo(viewport, control.rect().center())
    assert viewport.rect().contains(position)


@pytest.mark.parametrize("kind", (OverlayKind.TEXT, OverlayKind.CHECKMARK))
def test_add_mark_scrolls_its_focused_value_or_list_into_a_short_viewport(
    kind: OverlayKind,
) -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        dialog.resize(650, 360)
        app.processEvents()
        scroll = _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea")
        choices = _widget(dialog, QComboBox, "newOverlayTypeCombo")
        choices.setCurrentIndex(choices.findData(kind.value))
        add = _widget(dialog, QPushButton, "addCoreOverlayButton")
        scroll.ensureWidgetVisible(add)
        add.setFocus()
        QTest.keyClick(add, Qt.Key.Key_Space)
        app.processEvents()
        target = (
            _widget(dialog, QLineEdit, "coreOverlayValueEdit")
            if kind is OverlayKind.TEXT
            else _widget(dialog, QListWidget, "coreOverlayList")
        )
        _assert_focused_control_is_in_view(dialog, target)


@pytest.mark.parametrize("keep_another_mark", (False, True))
def test_remove_mark_scrolls_the_next_focus_target_into_view(keep_another_mark: bool) -> None:
    app = _application()
    state = _overlay_state()
    if keep_another_mark:
        state = state.create(OverlayKind.CHECKMARK)
    with _shown(CoreOverlayToolsDialog(state)) as dialog:
        dialog.resize(650, 360)
        app.processEvents()
        scroll = _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea")
        remove = _widget(dialog, QPushButton, "removeCoreOverlayButton")
        scroll.ensureWidgetVisible(remove)
        remove.setFocus()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        app.processEvents()
        target = (
            _widget(dialog, QListWidget, "coreOverlayList")
            if keep_another_mark
            else _widget(dialog, QComboBox, "newOverlayTypeCombo")
        )
        _assert_focused_control_is_in_view(dialog, target)


@pytest.mark.parametrize("use_field_return", (False, True))
def test_property_error_scrolls_the_focused_recovery_status_into_view(
    use_field_return: bool,
) -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        dialog.resize(650, 360)
        app.processEvents()
        left = _widget(dialog, QDoubleSpinBox, "coreOverlayLeftSpin")
        left.setValue(99)
        apply = _widget(dialog, QPushButton, "applyFormMarksButton")
        if use_field_return:
            left.setFocus()
            QTest.keyClick(left, Qt.Key.Key_Return)
        else:
            apply.setFocus()
            QTest.keyClick(apply, Qt.Key.Key_Space)
        app.processEvents()
        _assert_focused_control_is_in_view(
            dialog, _widget(dialog, QLabel, "coreOverlayToolsStatus")
        )
        assert not dialog.apply_pending


@pytest.mark.parametrize("key", (Qt.Key.Key_Return, Qt.Key.Key_Enter))
@pytest.mark.parametrize("field", ("value", "font", "left", "color"))
def test_return_in_property_fields_applies_only_local_properties_without_zoom_or_save(
    field: str, key: Qt.Key
) -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        requests: list[CoreOverlayToolsState] = []
        dialog.apply_requested.connect(requests.append)
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        zoom = canvas.zoom_percent
        # A formerly focused auto-default toolbar button must not own field Return.
        _widget(dialog, QPushButton, "overlayEditorZoomOutButton").setFocus()
        app.processEvents()
        if field == "value":
            target: QWidget = _widget(dialog, QLineEdit, "coreOverlayValueEdit")
            target.setFocus()
            QTest.keyClick(target, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(target, "Updated local text")
        elif field in {"font", "left"}:
            spin = _widget(
                dialog,
                QSpinBox if field == "font" else QDoubleSpinBox,
                "coreOverlayFontSizeSpin" if field == "font" else "coreOverlayLeftSpin",
            )
            editor = spin.findChild(QLineEdit)
            assert editor is not None
            target = editor
            target.setFocus()
            QTest.keyClick(target, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(target, "18" if field == "font" else "20")
        else:
            color = _widget(dialog, QComboBox, "coreOverlayColorCombo")
            target = color
            target.setFocus()
            QTest.keyClick(target, Qt.Key.Key_Down)
        QTest.keyClick(target, key)
        app.processEvents()
        record = dialog.state.selected_record
        assert record is not None and isinstance(record.payload, TextualOverlayPayload)
        if field == "value":
            assert record.payload.value == "Updated local text"
        elif field == "font":
            assert record.payload.font_size_points == 18
        elif field == "left":
            assert record.box.x == 200_000
        else:
            assert record.payload.color.value == "#203080ff"
        assert canvas.zoom_percent == zoom
        assert requests == [] and not dialog.apply_pending


def test_color_popup_return_selects_its_choice_without_applying_or_saving() -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        before = dialog.state
        color = _widget(dialog, QComboBox, "coreOverlayColorCombo")
        _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea").ensureWidgetVisible(color)
        color.setFocus()
        QTest.keyClick(color, Qt.Key.Key_Space)
        app.processEvents()
        assert color.view().isVisible()
        QTest.keyClick(color.view(), Qt.Key.Key_Down)
        QTest.keyClick(color.view(), Qt.Key.Key_Return)
        app.processEvents()
        assert not color.view().isVisible()
        assert dialog.isVisible() and dialog.state is before
        assert not dialog.apply_pending


@pytest.mark.parametrize(
    ("name", "editable"),
    (
        ("coreOverlayColorCombo", False),
        ("coreOverlayColorCombo", True),
        ("newOverlayTypeCombo", False),
        ("overlayEditorZoomCombo", False),
        ("overlayEditorGuideSpacingCombo", False),
    ),
)
def test_alt_down_opens_focused_combo_without_moving_or_applying_the_mark(
    name: str, editable: bool
) -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        before = dialog.state
        combo = _widget(dialog, QComboBox, name)
        if editable:
            combo.setEditable(True)
            editor = combo.lineEdit()
            assert editor is not None
            target: QWidget = editor
        else:
            target = combo
        _widget(dialog, QScrollArea, "coreOverlayToolsScrollArea").ensureWidgetVisible(combo)
        target.setFocus()
        QTest.keyClick(target, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        app.processEvents()
        assert combo.view().isVisible()
        assert dialog.state is before and not dialog.apply_pending
        QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
        app.processEvents()
        assert not combo.view().isVisible()
        assert dialog.isVisible() and dialog.state is before


@pytest.mark.parametrize("on_button", (False, True))
def test_alt_down_still_moves_the_selected_mark_from_list_or_button(on_button: bool) -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        before = dialog.state.selected_record
        assert before is not None
        target: QWidget = (
            _widget(dialog, QPushButton, "overlayEditorMoveDownButton")
            if on_button
            else _widget(dialog, QListWidget, "coreOverlayList")
        )
        target.setFocus()
        QTest.keyClick(target, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        app.processEvents()
        after = dialog.state.selected_record
        assert after is not None and after.overlay_id == before.overlay_id
        assert after.box.x == before.box.x and after.box.y == before.box.y + 10_000
        assert after.box.width == before.box.width and after.box.height == before.box.height
        assert not dialog.apply_pending


def test_return_on_focused_zoom_button_retains_ordinary_button_activation() -> None:
    app = _application()
    with _shown(CoreOverlayToolsDialog(_overlay_state())) as dialog:
        before = dialog.state
        canvas = _widget(dialog, OverlayEditorCanvas, "overlayEditorCanvas")
        # Fit-to-page can start at the minimum zoom. Deliberately choose a
        # larger zoom before checking that Return activates the focused button.
        combo = _widget(dialog, QComboBox, "overlayEditorZoomCombo")
        combo.setCurrentIndex(combo.findData(100))
        zoom = canvas.zoom_percent
        zoom_out = _widget(dialog, QPushButton, "overlayEditorZoomOutButton")
        zoom_out.setFocus()
        QTest.keyClick(zoom_out, Qt.Key.Key_Return)
        app.processEvents()
        assert canvas.zoom_percent < zoom
        assert dialog.state is before and not dialog.apply_pending


@pytest.mark.parametrize(
    "name",
    (
        "signatureImageDisclaimer",
        "signatureImageCropBackgroundReview",
        "signatureImagePlacementStatus",
    ),
)
def test_signature_review_text_has_a_rendered_visible_focus_indicator(name: str) -> None:
    app = _application()
    preview = SignatureImagePreview(
        UUID("70100000-0000-4000-8000-000000000004"),
        6,
        4,
        24,
        bytes((20, 80, 140, 96)) * 24,
        True,
        False,
        False,
        1_200,
        800,
    )
    draft = SignatureImagePlacementDraft(SESSION_ID, CASE_ID, 1, PAGE_ID, preview)
    with _shown(SignatureImagePlacementDialog(draft)) as dialog:
        label = _widget(dialog, QLabel, name)
        other = _widget(dialog, QPushButton, "cancelSignatureImageButton")
        other.setFocus()
        app.processEvents()
        unfocused = label.grab().toImage()
        label.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        assert label.hasFocus()
        focused = label.grab().toImage()
        assert focused != unfocused
