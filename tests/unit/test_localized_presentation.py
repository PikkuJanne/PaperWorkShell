"""Display language cannot change packet authority, user data or physical order."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QLocale, QRect, Qt
from PySide6.QtWidgets import QDialogButtonBox, QListWidget, QWidget

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportCheck, PdfExportCheckCode
from paperworkshell.localization.catalog import configure_locale, current_locale, localized, tr
from paperworkshell.ui.application import configure_ui_locale
from paperworkshell.ui.presentation import WrappingActionLayout, adapt_dialog_button_box
from paperworkshell.ui.signature_images import SignatureImageDeleteDialog
from tests.accessibility.test_signature_image_accessibility import _choice
from tests.unit.test_package_summary import summary_inputs
from tests.unit.test_presentation import _action_surface, _app


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_summary_localization_preserves_exact_packet_and_template_authority(locale: str) -> None:
    inputs = summary_inputs(warning_heavy=True)
    original_manifest = inputs.snapshot.document
    original_template = inputs.template.document
    original_binding = inputs.definition.binding
    english = inputs.build()
    with localized(locale):
        translated = inputs.build()
        text = translated.to_plain_text()
        assert text != english.to_plain_text()
        assert translated.status is english.status
        assert translated.file_count == english.file_count
        assert translated.active_page_count == english.active_page_count
        assert [item.filename for item in translated.outputs] == [
            item.filename for item in english.outputs
        ]
        assert [item.page_count for item in translated.outputs] == [
            item.page_count for item in english.outputs
        ]
        assert all(item.filename in text for item in translated.outputs)
        assert tr("PaperWorkShell package summary") in text
    assert inputs.snapshot.document == original_manifest
    assert inputs.template.document == original_template
    assert inputs.definition.binding == original_binding


def test_performed_check_counts_localize_without_changing_check_facts() -> None:
    check = PdfExportCheck(PdfExportCheckCode.PAGE_COUNT_MATCHED, 24)
    with localized("ar-XB"):
        assert "٢٤" in check.claim
    assert check.page_count == 24
    assert check.code is PdfExportCheckCode.PAGE_COUNT_MATCHED
    assert "24" in check.claim


@pytest.mark.parametrize(
    ("requested", "expected", "direction"),
    (
        ("en_XA", "en-XA", Qt.LayoutDirection.LeftToRight),
        ("ar-XB", "ar-XB", Qt.LayoutDirection.RightToLeft),
        ("zz-ZZ", "en", Qt.LayoutDirection.LeftToRight),
        ("../invalid", "en", Qt.LayoutDirection.LeftToRight),
        ("C", "en", Qt.LayoutDirection.LeftToRight),
    ),
)
def test_startup_locale_override_selects_packaged_text_and_direction(
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    expected: str,
    direction: Qt.LayoutDirection,
) -> None:
    application = _app()
    previous = current_locale()
    previous_native = QLocale()
    previous_direction = application.layoutDirection()
    monkeypatch.setenv("PWS_UI_LOCALE", requested)
    try:
        assert configure_ui_locale(application) == expected
        assert application.layoutDirection() == direction
        assert current_locale() == expected
    finally:
        configure_locale(previous)
        QLocale.setDefault(previous_native)
        application.setLayoutDirection(previous_direction)


def test_rtl_action_rows_mirror_in_place_without_reversing_focus_order() -> None:
    with _action_surface(1.0) as (surface, layout, buttons):
        height = layout.heightForWidth(360)
        layout.setGeometry(QRect(0, 0, 360, height))
        normal = [button.geometry() for button in buttons]
        chain = [button.nextInFocusChain() for button in buttons]
        surface.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        layout.setGeometry(QRect(0, 0, 360, height))
        mirrored = [button.geometry() for button in buttons]
        assert [button.nextInFocusChain() for button in buttons] == chain
        for before, after in zip(normal, mirrored, strict=True):
            assert before.y() == after.y()
            assert before.size() == after.size()
            assert before.x() + after.x() + before.width() == 360
        assert isinstance(layout, WrappingActionLayout)


def test_standard_native_footer_buttons_use_selected_catalog() -> None:
    _app()
    with localized("en-XA"):
        surface = QWidget()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, surface)
        adapt_dialog_button_box(box)
        try:
            button = box.button(QDialogButtonBox.StandardButton.Cancel)
            assert button is not None
            assert button.text().replace("\n", " ") == tr("Cancel")
        finally:
            surface.deleteLater()


def test_signature_placement_inventory_localizes_geometry_and_retains_identity() -> None:
    from dataclasses import replace

    _app()
    choice = replace(_choice(), box=NormalizedPageBox(123456, 100, 100000, 100000))
    with localized("ar-XB"):
        dialog = SignatureImageDeleteDialog((choice,))
        try:
            inventory = dialog.findChild(QListWidget)
            assert inventory is not None
            item = inventory.item(0)
            assert "١٢٫٣٤٥٦" in item.text()
            assert "٠٫٠١٠٠" in item.text()
            assert "Signature image" not in item.text()
            assert item.data(Qt.ItemDataRole.AccessibleTextRole) == item.text()
            assert item.data(Qt.ItemDataRole.UserRole) == choice.overlay_id
        finally:
            dialog.deleteLater()
    assert choice.box.x == 123456
