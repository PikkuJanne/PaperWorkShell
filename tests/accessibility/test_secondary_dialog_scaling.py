"""PWS-0703 narrow dialog reflow and bounded, undistorted scan previews."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QRadioButton, QScrollArea

from paperworkshell.ui.assisted_use import AssistedConsentDialog
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.ui.presentation import install_presentation_policy
from paperworkshell.ui.scan_review import ScanReviewDialog
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
)
from tests.accessibility.test_accessibility_baseline import _application, _window
from tests.accessibility.test_signature_image_accessibility import _choice
from tests.accessibility.test_signature_image_accessibility import _draft as _signature
from tests.unit.test_scan_review_dialog import _draft as _scan


@contextmanager
def _large_dialog(factory: Callable[[], QDialog]) -> Iterator[QDialog]:
    application = _application()
    install_presentation_policy(application)
    original_font = QFont(application.font())
    enlarged = QFont(original_font)
    enlarged.setPointSize(18)
    application.setFont(enlarged)
    dialog = factory()
    dialog.resize(640, 420)
    dialog.show()
    application.processEvents()
    application.processEvents()
    try:
        yield dialog
    finally:
        dialog.deleteLater()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.setFont(original_font)
        application.processEvents()


@pytest.mark.parametrize(
    "factory",
    (
        AssistedConsentDialog,
        lambda: OcrLanguageDialog(lambda: 1),
        lambda: ScanReviewDialog(_scan()),
        lambda: SignatureImagePlacementDialog(_signature()),
        lambda: SignatureImageDeleteDialog((_choice(),)),
    ),
    ids=("consent", "ocr", "scan", "signature-place", "signature-remove"),
)
def test_secondary_dialogs_reflow_at_enlarged_text_without_horizontal_review(
    factory: Callable[[], QDialog],
) -> None:
    with _large_dialog(factory) as dialog:
        assert dialog.width() <= 640
        assert dialog.height() <= 420
        areas = dialog.findChildren(QScrollArea)
        assert areas
        for area in areas:
            assert area.horizontalScrollBar().maximum() == 0
            content = area.widget()
            assert content is not None and content.width() <= area.viewport().width()
            assert area.verticalScrollBar().maximum() > 0


def test_wrapped_signature_asset_choices_remain_mutually_exclusive() -> None:
    with _large_dialog(lambda: SignatureImageDeleteDialog((_choice(),))) as dialog:
        keep = dialog.findChild(QRadioButton, "keepSignatureImageAssetRadio")
        remove = dialog.findChild(QRadioButton, "removeUnusedSignatureImageAssetRadio")
        assert keep is not None and remove is not None
        assert keep.isChecked() and not remove.isChecked()
        remove.setChecked(True)
        assert remove.isChecked() and not keep.isChecked()
        for choice in (keep, remove):
            label = dialog.findChild(QLabel, f"{choice.objectName()}Label")
            assert label is not None and label.buddy() is choice and label.wordWrap()
            assert label.text() == choice.accessibleName()


def test_loaded_scan_preview_fits_narrow_view_and_preserves_aspect_ratio() -> None:
    draft = _scan()
    with _large_dialog(lambda: ScanReviewDialog(draft)) as surface:
        assert isinstance(surface, ScanReviewDialog)
        image = QImage(600, 800, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.red)
        assert surface.set_preview_loading(draft)
        assert surface.set_original_image(draft, image)
        assert surface.set_adjusted_image(draft, image)
        _application().processEvents()
        for area in surface.findChildren(QScrollArea):
            assert area.horizontalScrollBar().maximum() == 0
        preview = surface.findChild(QLabel, "scanReviewOriginalPreview")
        assert preview is not None
        assert preview.pixmap().size() == image.size()
        # A wide viewport must letterbox the portrait raster rather than crop
        # or stretch it, while retaining only the original bounded pixmap.
        preview.resize(300, 160)
        rendered = preview.grab().toImage()
        assert rendered.pixelColor(150, 80) == Qt.GlobalColor.red
        assert rendered.pixelColor(10, 80) == preview.palette().base().color()
        assert preview.pixmap().size() == image.size()


def test_ocr_recovery_enters_refresh_after_application_focus_styles_are_installed() -> None:
    # The real main window installs a focus stylesheet. Under that style Qt's
    # vertical footer otherwise redirects entry to the default Cancel button,
    # even when the individual widgets have the right nextInFocusChain order.
    with _window() as (window, _commands, _worker):
        dialog = OcrLanguageDialog(lambda: 1, window)
        dialog.show()
        _application().processEvents()
        try:
            dialog.request_refresh()
            assert dialog.set_failure(1, "OCR_INTERNAL_ERROR", "Try Refresh again.")
            _application().processEvents()
            error = dialog.findChild(QLabel, "ocrLanguageError")
            scroll = dialog.findChild(QScrollArea, "ocrLanguageScrollArea")
            refresh = dialog.findChild(QPushButton, "ocrLanguageRefreshButton")
            assert error is not None and scroll is not None and refresh is not None
            error.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.keyClick(error, Qt.Key.Key_Tab)
            assert scroll.hasFocus()
            QTest.keyClick(scroll, Qt.Key.Key_Tab)
            assert refresh.hasFocus()
            QTest.keyClick(refresh, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
            assert scroll.hasFocus()
            QTest.keyClick(scroll, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
            assert error.hasFocus()
        finally:
            dialog.deleteLater()
