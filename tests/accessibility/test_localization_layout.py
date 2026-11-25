"""PWS-0704 pseudo text, RTL reading order and physical editor coordinates."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QLocale, QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.localization.catalog import is_rtl, localized
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.assisted_use import AssistedConsentDialog
from paperworkshell.ui.compression_review import CompressionReviewDialog
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.presentation import install_presentation_policy
from paperworkshell.ui.redaction_canvas import RedactionIntentCanvas
from paperworkshell.ui.scan_review import ScanReviewDialog
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
)
from tests.accessibility.test_accessibility_baseline import _application, _state, _window
from tests.accessibility.test_checklist_accessibility import _dialog as _checklist
from tests.accessibility.test_checklist_accessibility import _empty_state as _checklist_state
from tests.accessibility.test_constraint_editor_accessibility import _dialog as _constraints
from tests.accessibility.test_core_overlay_tools_accessibility import PRIVATE_VALUE
from tests.accessibility.test_core_overlay_tools_accessibility import _dialog as _overlays
from tests.accessibility.test_display_preferences import _assert_revealed
from tests.accessibility.test_redaction_tools_accessibility import _dialog as _removal
from tests.accessibility.test_signature_image_accessibility import _choice
from tests.accessibility.test_signature_image_accessibility import _draft as _signature
from tests.helpers.compression_review import compression_review
from tests.unit.test_scan_review_dialog import _draft as _scan

type _DialogFactory = Callable[[], AbstractContextManager[QDialog]]


def _settle() -> None:
    # Font, label-wrap and focus-reveal updates are coalesced after layout.
    _application().processEvents()
    _application().processEvents()


@contextmanager
def _display_locale(locale: str, point_size: int = 9) -> Iterator[None]:
    application = _application()
    install_presentation_policy(application)
    original_font = QFont(application.font())
    original_direction = application.layoutDirection()
    original_native_locale = QLocale()
    font = QFont(original_font)
    # Windows' offscreen QPA has no font database on the development host.
    # Admit the installed system font for this synthetic rendering context;
    # otherwise a geometry pass can consist entirely of missing-glyph boxes.
    admitted_font: int | None = None
    installed = Path("C:/Windows/Fonts/segoeui.ttf")
    if installed.is_file():
        # Canvas content registers a Latin export font. Use the same known
        # Latin/Arabic UI face for both comparison locales; nonempty families
        # alone neither guarantee Arabic coverage nor isolate RTL geometry.
        admitted_font = QFontDatabase.addApplicationFont(str(installed))
        assert admitted_font >= 0, "A real font is required for localized widget rendering"
        families = QFontDatabase.applicationFontFamilies(admitted_font)
        assert families
        font.setFamily(families[0])
    font.setPointSize(point_size)
    metrics = QFontMetrics(font)
    application.setFont(font)
    with localized(locale):
        QLocale.setDefault(QLocale("ar" if locale == "ar-XB" else "en"))
        application.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )
        try:
            assert metrics.inFontUcs4(0x00E9), "Pseudo text must render actual Latin glyphs"
            if locale == "ar-XB":
                assert metrics.inFontUcs4(0x0627), "RTL text must render actual Arabic glyphs"
            yield
        finally:
            application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            application.setFont(original_font)
            application.setLayoutDirection(original_direction)
            QLocale.setDefault(original_native_locale)
            if admitted_font is not None:
                QFontDatabase.removeApplicationFont(admitted_font)
            application.processEvents()


@contextmanager
def _shown(factory: Callable[[], QDialog]) -> Iterator[QDialog]:
    dialog = factory()
    dialog.show()
    _settle()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        _application().processEvents()


def _package() -> QDialog:
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    dialog.show_review(
        PackageSummary(PackageSummaryStatus.PLANNED),
        export_allowed=True,
        human_review_required=True,
        signed_source_confirmation_required=True,
    )
    return dialog


def _compression() -> QDialog:
    dialog = CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1)
    dialog.show_review(compression_review(grayscale=True, floor=True))
    return dialog


_DIALOGS: dict[str, tuple[_DialogFactory, str]] = {
    "overlays": (_overlays, "coreOverlayToolsScrollArea"),
    "checklist": (lambda: _checklist(_checklist_state()), "checklistEditorScrollArea"),
    "constraints": (_constraints, "constraintEditorScrollArea"),
    "removal": (_removal, "redactionToolsScroll"),
    "consent": (lambda: _shown(AssistedConsentDialog), "assistedConsentScroll"),
    "ocr": (lambda: _shown(lambda: OcrLanguageDialog(lambda: 1)), "ocrLanguageScrollArea"),
    "scan": (lambda: _shown(lambda: ScanReviewDialog(_scan())), "scanReviewScrollArea"),
    "signature-place": (
        lambda: _shown(lambda: SignatureImagePlacementDialog(_signature())),
        "signatureImagePlacementScrollArea",
    ),
    "signature-remove": (
        lambda: _shown(lambda: SignatureImageDeleteDialog((_choice(),))),
        "signatureImageDeleteScrollArea",
    ),
    "package": (lambda: _shown(_package), "packageExportScrollArea"),
    "compression": (lambda: _shown(_compression), "compressionReviewScrollArea"),
}


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
@pytest.mark.parametrize("point_size", (9, 18))
@pytest.mark.parametrize("kind", tuple(_DIALOGS))
def test_pseudo_workflow_dialogs_keep_critical_controls_reachable(
    locale: str, point_size: int, kind: str
) -> None:
    """Doubled nine-point text and expanded messages use bounded scrolling."""
    with _display_locale(locale, point_size):
        factory, scroll_name = _DIALOGS[kind]
        with factory() as dialog:
            dialog.resize(640, 420)
            _settle()
            assert dialog.width() <= 640, (kind, dialog.width())
            assert dialog.height() <= 420, (kind, dialog.height())
            assert dialog.layoutDirection() is (
                Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
            )
            area = dialog.findChild(QScrollArea, scroll_name)
            assert area is not None, (kind, scroll_name)
            assert area.widgetResizable()
            assert area.horizontalScrollBar().maximum() == 0, kind
            for button in dialog.findChildren(QPushButton):
                if not button.isVisible() or not button.objectName():
                    continue
                if area.isAncestorOf(button):
                    area.ensureWidgetVisible(button, 4, 4)
                    _settle()
                    area.ensureWidgetVisible(button, 4, 4)
                    _settle()
                    viewport = area.viewport()
                    assert viewport.rect().contains(
                        button.mapTo(viewport, button.rect().center())
                    ), (kind, button.objectName())
                else:
                    assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft())), (
                        kind,
                        button.objectName(),
                    )
                    assert dialog.rect().contains(
                        button.mapTo(dialog, button.rect().bottomRight())
                    ), (kind, button.objectName())
                # A reachable button whose action is clipped is not sufficient.
                assert (
                    button.width()
                    >= max(
                        button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                        for line in button.text().splitlines()
                    )
                    + 12
                ), (kind, button.objectName(), button.text())
            for footer in dialog.findChildren(QDialogButtonBox):
                visible = [action for action in footer.buttons() if action.isVisible()]
                for index, action in enumerate(visible):
                    assert footer.rect().contains(action.geometry())
                    for other in visible[index + 1 :]:
                        assert not action.geometry().intersects(other.geometry())


def _tab_names(window: QWidget) -> tuple[str, ...]:
    window.activateWindow()
    first = window.findChild(QWidget, "pageList")
    assert first is not None
    first.setFocus()
    _application().processEvents()
    seen: list[QWidget] = []
    for _step in range(80):
        focus = QApplication.focusWidget()
        assert isinstance(focus, QWidget)
        if focus in seen:
            assert focus is first
            break
        seen.append(focus)
        QTest.keyClick(focus, Qt.Key.Key_Tab)
    else:
        raise AssertionError("The workflow focus chain did not close")
    return tuple(widget.objectName() for widget in seen)


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
@pytest.mark.parametrize("point_size", (9, 18))
@pytest.mark.parametrize("page", ("chooser", "organizer"))
def test_pseudo_main_workflow_actions_and_recovery_remain_reachable(
    locale: str, point_size: int, page: str
) -> None:
    with _display_locale(locale, point_size):
        with _window() as (window, _commands, worker):
            worker.initialized.emit()
            if page == "organizer":
                worker.state_changed.emit(_state())
            window.resize(800, 560)
            window.activateWindow()
            window._error_label.setText("Synthetic recovery target")
            window._error_label.setVisible(True)
            window._retry_save_button.setVisible(True)
            window._retry_save_button.setEnabled(True)
            _application().processEvents()
            _application().processEvents()
            for button in window.findChildren(QPushButton):
                if button.isVisible() and button.isEnabled():
                    assert button.width() >= button.sizeHint().width(), button.objectName()
                    assert button.height() >= button.sizeHint().height(), button.objectName()
                    _assert_revealed(button)
            _assert_revealed(window._error_label)


def test_rtl_main_workflow_keeps_logical_tab_order_and_mirrors_layout() -> None:
    orders: list[tuple[str, ...]] = []
    positions: list[tuple[int, int]] = []
    for locale in ("en", "ar-XB"):
        with _display_locale(locale):
            with _window() as (window, _commands, worker):
                worker.initialized.emit()
                worker.state_changed.emit(_state())
                window.resize(1200, 800)
                _application().processEvents()
                orders.append(
                    tuple(
                        name
                        for name in _tab_names(window)
                        if name not in {"ScrollLeftButton", "ScrollRightButton"}
                    )
                )
                fill = window.findChild(QPushButton, "traceWorkflow-fill")
                protect = window.findChild(QPushButton, "traceWorkflow-protect")
                assert fill is not None and protect is not None
                assert fill.isVisible() and protect.isVisible()
                positions.append(
                    (fill.mapTo(window, QPoint()).x(), protect.mapTo(window, QPoint()).x())
                )
    assert orders[0] == orders[1]
    assert "importButton" in orders[1]
    assert "traceWorkflow-fill" in orders[1]
    assert "cancelButton" not in orders[1]  # No operation is running.
    # Workflow actions mirror within each row while tab order remains explicit.
    assert positions[0][0] < positions[0][1]
    assert positions[1][0] > positions[1][1]


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_rtl_removal_pointer_mapping_keeps_physical_page_coordinates(
    rotation: ClockwiseRotation,
) -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 200_000, 600_000, 600_000),
        manifest_rotation=rotation,
    )
    observations: list[NormalizedPageBox] = []
    for locale in ("en", "ar-XB"):
        with _display_locale(locale):
            with _removal(transform=transform) as dialog:
                canvas = dialog.findChild(RedactionIntentCanvas, "redactionIntentCanvas")
                assert canvas is not None
                assert canvas.layoutDirection() is Qt.LayoutDirection.LeftToRight
                page = canvas._page_rect()
                start = QPoint(
                    round(page.left() + page.width() * 0.2),
                    round(page.top() + page.height() * 0.2),
                )
                end = QPoint(
                    round(page.left() + page.width() * 0.8),
                    round(page.top() + page.height() * 0.6),
                )
                QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(canvas, end)
                QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
                assert len(dialog.state.records) == 1
                observations.append(dialog.state.records[0].box)
    first, second = observations
    for actual, expected in zip(
        (first.x, first.y, first.width, first.height),
        (second.x, second.y, second.width, second.height),
        strict=True,
    ):
        # Text expansion can change the preview's size; retain one-pixel tolerance.
        assert abs(actual - expected) < 5_000


def test_rtl_overlay_keyboard_right_moves_content_right_without_translating_user_text() -> None:
    with _display_locale("ar-XB"):
        with _overlays() as dialog:
            canvas = dialog.findChild(OverlayEditorCanvas, "overlayEditorCanvas")
            assert canvas is not None
            assert canvas.layoutDirection() is Qt.LayoutDirection.LeftToRight
            original = dialog.state.selected_record
            assert original is not None
            value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
            assert value is not None and value.text() == PRIVATE_VALUE
            button = dialog.findChild(QPushButton, "overlayEditorMoveRightButton")
            assert button is not None
            button.click()
            current = dialog.state.selected_record
            assert current is not None
            assert current.box.x > original.box.x
            assert current.box.y == original.box.y
            assert current.payload == original.payload


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_rtl_overlay_pointer_edit_keeps_crop_and_rotation_mapping(
    rotation: ClockwiseRotation,
) -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(50_000, 50_000, 900_000, 900_000),
        manifest_rotation=rotation,
    )
    boxes: list[NormalizedPageBox] = []
    for locale in ("en", "ar-XB"):
        with _display_locale(locale):
            with _overlays(page_transform=transform) as dialog:
                canvas = dialog.findChild(OverlayEditorCanvas, "overlayEditorCanvas")
                assert canvas is not None
                original = dialog.state.selected_record
                assert original is not None
                screen = canvas._screen_box_for(original)
                assert screen is not None
                start = QPoint(
                    round((screen.left + screen.right) / 2),
                    round((screen.top + screen.bottom) / 2),
                )
                page = canvas._page_rect()
                end = start + QPoint(round(page.width() * 0.04), round(page.height() * 0.02))
                QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(canvas, end)
                QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
                current = dialog.state.selected_record
                assert current is not None and current.box != original.box
                assert current.payload == original.payload
                boxes.append(current.box)
    assert boxes[0] == boxes[1]
