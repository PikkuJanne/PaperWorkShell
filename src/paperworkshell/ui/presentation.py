"""Native-palette presentation with scalable targets and no decorative motion.

All dimensions are Qt logical pixels. The native style still owns painting,
fonts and palette, including Windows contrast themes; no application theme is
substituted. Motion is reduced unconditionally, so an OS request for less motion
never needs a second application setting or a platform-preference poller.
"""

from __future__ import annotations

from typing import Final, cast

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QLabel,
    QLayout,
    QLayoutItem,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleHintReturn,
    QStyleOption,
    QStyleOptionButton,
    QStyleOptionMenuItem,
    QWidget,
)

from paperworkshell.localization.catalog import mark, tr

MINIMUM_CONTROL_TARGET: Final = 32
MINIMUM_INDICATOR_TARGET: Final = 24
_STANDARD_BUTTON_TEXT: Final = {
    QDialogButtonBox.StandardButton.Ok: mark("OK"),
    QDialogButtonBox.StandardButton.Cancel: mark("Cancel"),
    QDialogButtonBox.StandardButton.Close: mark("Close"),
    QDialogButtonBox.StandardButton.Apply: mark("Apply"),
    QDialogButtonBox.StandardButton.Save: mark("Save"),
    QDialogButtonBox.StandardButton.Discard: mark("Discard"),
    QDialogButtonBox.StandardButton.Yes: mark("Yes"),
    QDialogButtonBox.StandardButton.No: mark("No"),
    QDialogButtonBox.StandardButton.Reset: mark("Reset"),
    QDialogButtonBox.StandardButton.Retry: mark("Retry"),
    QDialogButtonBox.StandardButton.Help: mark("Help"),
}
_MENU_PALETTE_STYLE: Final = """
QMenuBar, QMenu {
    color: palette(window-text);
    background-color: palette(window);
}
QMenuBar::item {
    color: palette(window-text);
    background-color: transparent;
}
QMenuBar::item:selected, QMenu::item:selected {
    color: palette(highlighted-text);
    background-color: palette(highlight);
}
"""


class ElidingTextLabel(QLabel):
    """Keep one visible line and the complete native accessible text."""

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, self.fontMetrics().height() + 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.fontMetrics().height() + 8)

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self.fontMetrics().height() + 8

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(
            self.contentsRect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(
                self.text(), Qt.TextElideMode.ElideMiddle, self.contentsRect().width()
            ),
        )
        painter.end()


class AccessibleNativeStyle(QProxyStyle):
    """Retain native contrast painting while enlarging interactive geometry."""

    def sizeFromContents(  # noqa: N802 - Qt override
        self,
        type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        /,
        *args: QWidget | None,
        **keywords: QWidget | None,
    ) -> QSize:
        # QStyle names this optional keyword "w"; QCommonStyle calls it
        # "widget" and QProxyStyle's stub makes it mandatory. Preserve both
        # base contracts and ask the native style for its original geometry.
        widget = args[0] if args else keywords.get("widget", keywords.get("w"))
        result = self.baseStyle().sizeFromContents(type, option, size, widget)
        if type == QStyle.ContentsType.CT_PushButton and isinstance(option, QStyleOptionButton):
            # Some Windows native button hints reserve only four pixels around
            # translated labels. Keep the whole action readable in a fitted row.
            text_width = max(
                (
                    option.fontMetrics.horizontalAdvance(line.replace("&", ""))
                    for line in option.text.splitlines()
                ),
                default=0,
            )
            result.setWidth(max(result.width(), text_width + 12))
        if type == QStyle.ContentsType.CT_MenuItem and isinstance(option, QStyleOptionMenuItem):
            label, _, shortcut = option.text.partition("\t")
            metrics = option.fontMetrics
            # Native Windows menu sizing behind a palette stylesheet can omit
            # the shortcut column. Reserve both columns from actual font metrics.
            width = metrics.horizontalAdvance(label.replace("&", ""))
            shortcut_width = max(option.reservedShortcutWidth, metrics.horizontalAdvance(shortcut))
            result.setWidth(max(result.width(), width + shortcut_width + 72))
        if type in (
            QStyle.ContentsType.CT_PushButton,
            QStyle.ContentsType.CT_ToolButton,
            QStyle.ContentsType.CT_ComboBox,
            QStyle.ContentsType.CT_LineEdit,
            QStyle.ContentsType.CT_SpinBox,
            QStyle.ContentsType.CT_CheckBox,
            QStyle.ContentsType.CT_RadioButton,
            QStyle.ContentsType.CT_ItemViewItem,
        ):
            return result.expandedTo(QSize(MINIMUM_CONTROL_TARGET, MINIMUM_CONTROL_TARGET))
        return result

    def pixelMetric(  # noqa: N802 - Qt override
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> int:
        result = super().pixelMetric(metric, option, widget)
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
            QStyle.PixelMetric.PM_ExclusiveIndicatorWidth,
            QStyle.PixelMetric.PM_ExclusiveIndicatorHeight,
        ):
            return max(MINIMUM_INDICATOR_TARGET, result)
        if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
            return max(MINIMUM_INDICATOR_TARGET, result)
        return result

    def styleHint(  # noqa: N802 - Qt override
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        if hint in (
            QStyle.StyleHint.SH_Widget_Animate,
            QStyle.StyleHint.SH_Widget_Animation_Duration,
            QStyle.StyleHint.SH_SpinBox_AnimateButton,
        ):
            return 0
        return super().styleHint(hint, option, widget, returnData)


class _PaletteRefresh(QObject):
    """Re-resolve stylesheet palette roles when Windows changes its theme."""

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._application = application
        self._refreshing = False
        application.paletteChanged.connect(self.refresh)

    @Slot(QPalette)
    def refresh(self, _palette: QPalette) -> None:
        # Qt stylesheet widgets cache resolved palette roles. Updating only
        # QApplication's palette leaves existing menus and controls stale.
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._application.setStyleSheet(self._application.styleSheet())
        finally:
            self._refreshing = False


def install_presentation_policy(application: QApplication) -> None:
    """Install once, without changing the user's native font, palette or theme."""
    # A Qt stylesheet exposes a private wrapper as application.style(). Keep
    # the application-owned proxy discoverable instead of stacking another
    # proxy every time an owned window is constructed.
    if application.findChild(AccessibleNativeStyle) is None:
        application.setStyle(AccessibleNativeStyle(application.style()))
    # Windows' native menu painting can otherwise draw system black text on an
    # application contrast palette's black background. Use paired Qt roles.
    if _MENU_PALETTE_STYLE not in application.styleSheet():
        application.setStyleSheet(application.styleSheet() + "\n" + _MENU_PALETTE_STYLE)
    if application.findChild(_PaletteRefresh) is None:
        _PaletteRefresh(application)
    for effect in (
        Qt.UIEffect.UI_AnimateMenu,
        Qt.UIEffect.UI_FadeMenu,
        Qt.UIEffect.UI_AnimateCombo,
        Qt.UIEffect.UI_AnimateTooltip,
        Qt.UIEffect.UI_FadeTooltip,
        Qt.UIEffect.UI_AnimateToolBox,
    ):
        application.setEffectEnabled(effect, False)


def adapt_dialog_button_box(button_box: QDialogButtonBox) -> None:
    """Keep verbose acceptance/recovery actions inside a narrow dialog footer."""
    button_box.setOrientation(Qt.Orientation.Vertical)
    # Child buttons get measured text minima below. Let the outer footer shrink
    # first so a previously fitted row can switch to a stack on a narrow resize.
    button_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    # QWidget* is nullable in Qt; the PySide6 stub omits that annotation.
    button_box.setFocusProxy(cast(QWidget, None))
    _FooterTextWrap(button_box)


class _FooterTextWrap(QObject):
    """Keep translated native button labels complete at enlarged text sizes.

    Buttons retain their accessible names and focus identities. The native
    style paints each line; the footer can shrink horizontally and grows only
    enough to fit the wrapped label. No copy of document data is retained.
    """

    def __init__(self, box: QDialogButtonBox) -> None:
        super().__init__(box)
        self._box = box
        self._refreshing = False
        self._text: dict[QPushButton, tuple[str, str]] = {}
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)
        box.installEventFilter(self)
        self._refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (
            QEvent.Type.ChildPolished,
            QEvent.Type.LayoutRequest,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            # Changing native QDialogButtonBox orientation rebuilds its layout;
            # do that between events, never while Qt is laying out a child.
            self._refresh_timer.start()
        return False

    def _refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            geometry_changed = False
            complete_widths: list[int] = []
            measured_heights: list[int] = []
            for button in self._box.buttons():
                if not isinstance(button, QPushButton) or button.isHidden():
                    continue
                if button not in self._text:
                    standard_text = _STANDARD_BUTTON_TEXT.get(self._box.standardButton(button))
                    if standard_text is not None:
                        button.setText(tr(standard_text))
                    button.installEventFilter(self)
                    button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                    button.setMinimumWidth(MINIMUM_CONTROL_TARGET)
                    self._text[button] = (button.text(), button.text())
                source, applied = self._text[button]
                if button.text() != applied:
                    source = button.text()
                metrics = button.fontMetrics()
                complete_widths.append(
                    max(
                        (
                            metrics.horizontalAdvance(row.replace("&", ""))
                            for row in source.split("\n")
                        ),
                        default=0,
                    )
                    + 28
                )
                width = max(80, self._box.width() - 28)
                rows: list[str] = []
                for paragraph in source.split("\n"):
                    row = ""
                    for word in paragraph.split(" "):
                        candidate = row + " " + word if row else word
                        if row and metrics.horizontalAdvance(candidate.replace("&", "")) > width:
                            rows.append(row)
                            row = word
                        else:
                            row = candidate
                    rows.append(row)
                rendered = "\n".join(rows)
                self._text[button] = (source, rendered)
                if button.text() != rendered:
                    button.setText(rendered)
                    geometry_changed = True
                minimum_width = max(
                    MINIMUM_CONTROL_TARGET,
                    max(
                        (metrics.horizontalAdvance(row.replace("&", "")) for row in rows),
                        default=0,
                    )
                    + 12,
                )
                if button.minimumWidth() != minimum_width:
                    button.setMinimumWidth(minimum_width)
                    geometry_changed = True
                minimum_height = max(
                    MINIMUM_CONTROL_TARGET,
                    len(rows) * metrics.lineSpacing() + 16,
                    button.sizeHint().height(),
                    button.minimumSizeHint().height(),
                )
                measured_heights.append(minimum_height)
                if button.minimumHeight() != minimum_height:
                    button.setMinimumHeight(minimum_height)
                    geometry_changed = True
            layout = self._box.layout()
            if layout is not None:
                margins = layout.contentsMargins()
                required_width = (
                    sum(complete_widths)
                    + max(0, layout.spacing()) * max(0, len(complete_widths) - 1)
                    + margins.left()
                    + margins.right()
                )
                orientation = (
                    Qt.Orientation.Horizontal
                    if required_width <= self._box.width()
                    else Qt.Orientation.Vertical
                )
                if self._box.orientation() is not orientation:
                    self._box.setOrientation(orientation)
                    geometry_changed = True
            if geometry_changed:
                # Native QDialogButtonBox can keep its earlier row allocation
                # after a wrapped button's minimum changes. Recompute the
                # shared footer before the parent allocates its final height.
                layout = self._box.layout()
                if layout is not None:
                    layout.invalidate()
                    layout.activate()
                self._box.updateGeometry()
            layout = self._box.layout()
            if layout is not None:
                # The footer cannot borrow space from its own button targets
                # when a neighboring wrapped label needs extra height.
                margins = layout.contentsMargins()
                measured_height = (
                    max(measured_heights, default=0)
                    if self._box.orientation() is Qt.Orientation.Horizontal
                    else sum(measured_heights)
                    + max(0, layout.spacing()) * max(0, len(measured_heights) - 1)
                )
                # Native layout caches can lag behind a freshly measured
                # child for one event. Its current target must already fit.
                minimum_height = max(
                    layout.minimumSize().height(),
                    measured_height + margins.top() + margins.bottom(),
                )
                if self._box.minimumHeight() != minimum_height:
                    # Fixed vertical policies prevent surplus-height stretching
                    # while native size hints can still grow immediately when
                    # a newly added button is polished or inherits a new font.
                    self._box.setMinimumHeight(minimum_height)
                    parent = self._box.parentWidget()
                    parent_layout = parent.layout() if parent is not None else None
                    if parent_layout is not None:
                        # Reallocate the surrounding scroll/body area in this
                        # same refresh, before a taller footer can be painted
                        # at its previous position below a narrowed window.
                        parent_layout.invalidate()
                        parent_layout.activate()
        finally:
            self._refreshing = False


def set_static_busy(progress: QProgressBar) -> None:
    """Keep unknown-length work perceivable without an animated busy marquee."""
    progress.setRange(0, 1)
    progress.setValue(0)
    progress.setFormat(tr("Working…"))


class WrappingActionLayout(QLayout):
    """Wrap native action targets in reading order rather than shrinking text.

    Items stay in construction/tab order. A narrow viewport gains rows; its
    surrounding scroll area owns vertical navigation and focused-target reveal.
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if not 0 <= index < len(self._items):
            return None
        item = self._items.pop(index)
        self.invalidate()
        return item

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rectangle: QRect) -> None:  # noqa: N802
        super().setGeometry(rectangle)
        self._arrange(rectangle, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rectangle: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        bounds = rectangle.marginsRemoved(margins)
        parent = self.parentWidget()
        rtl = parent is not None and parent.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x, y, row_height = bounds.x(), bounds.y(), 0
        spacing = self.spacing()
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if row_height and x + size.width() > bounds.right() + 1:
                x = bounds.x()
                y += row_height + spacing
                row_height = 0
            if apply:
                # Mirror controls in each row while retaining construction and
                # focus order. Document pixels use their own physical geometry.
                visual_x = bounds.right() + 1 - (x - bounds.x()) - size.width() if rtl else x
                item.setGeometry(QRect(visual_x, y, size.width(), size.height()))
            x += size.width() + spacing
            row_height = max(row_height, size.height())
        return y + row_height - rectangle.y() + margins.bottom()
