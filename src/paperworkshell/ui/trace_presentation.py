"""Shared native TRACE colors and surfaces for the installed application."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

INK = "#0B0E11"
PANEL = "#141A1E"
RAISED = "#1C2529"
STAGE = "#1A2225"
RULE = "#354347"
SELECTION = "#232D26"
INPUT_BORDER = "#61706D"
TEXT = "#EAF0E7"
MUTED = "#A8B5B0"
LIME = "#C4F36B"
LIME_HOVER = "#D4FA93"
AMBER = "#F1BA69"
ERROR = "#FF8A7A"
DISABLED = "#88918C"

TRACE_STYLE = f"""
QMainWindow, QDialog, QMessageBox, #applicationSurface {{ background: {INK}; }}
QFrame[tracePanel="true"], QGroupBox {{ background: {PANEL}; }}
QGroupBox {{ border: 1px solid {RULE}; margin-top: 1em; padding: 5px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QLabel[traceRole="muted"] {{ color: {MUTED}; }}
QLabel[traceRole="brand"] {{ color: {LIME}; font-weight: 700; }}
QLabel[traceRole="attention"] {{ color: {AMBER}; }}
QLabel[traceRole="error"] {{ color: {ERROR}; }}
#traceDocumentStage, #traceDocumentStage > QWidget > QWidget {{ background: {STAGE}; }}
QAbstractItemView {{ selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QAbstractItemView::item:selected {{
  background-color: {SELECTION}; color: {TEXT}; border-left: 3px solid {LIME};
}}
QPushButton, QToolButton {{
  background: {PANEL}; color: {TEXT}; border: 2px solid {INPUT_BORDER};
  border-radius: 3px; padding: 2px 8px; min-height: 30px;
}}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; border-color: {MUTED}; }}
QPushButton:checked, QToolButton:checked {{ background: {RAISED}; color: {LIME}; }}
QPushButton[tracePrimary="true"] {{ background: {LIME}; color: {INK}; font-weight: 650; }}
QPushButton[tracePrimary="true"]:hover {{ background: {LIME_HOVER}; }}
QPushButton:disabled, QToolButton:disabled {{
  background: {PANEL}; color: {DISABLED}; border-color: {RULE};
}}
QPushButton:focus, QToolButton:focus, QLineEdit:focus {{ border-color: {LIME}; }}
QPushButton[tracePrimary="true"]:focus {{ border-color: {TEXT}; }}
QLineEdit, QPlainTextEdit, QTextEdit {{
  color: {TEXT}; background: {INK}; border: 2px solid {INPUT_BORDER}; padding: 3px 6px;
}}
QLineEdit {{ min-height: 30px; }}
QTabWidget::pane {{ border: 1px solid {RULE}; }}
QTabBar::tab {{ background: {PANEL}; padding: 7px 9px; border: 1px solid {RULE}; }}
QTabBar::tab:selected {{ background: {RAISED}; color: {LIME}; border-bottom-color: {LIME}; }}
QSplitter::handle {{ background: {RULE}; }}
"""


class _HighContrast(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.UINT),
        ("flags", wintypes.DWORD),
        ("scheme", wintypes.LPWSTR),
    ]


def _windows_contrast_theme() -> bool:
    if sys.platform != "win32":
        return False
    value = _HighContrast()
    value.size = ctypes.sizeof(value)
    query = ctypes.WinDLL("user32", use_last_error=True).SystemParametersInfoW
    query.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
    query.restype = wintypes.BOOL
    # SPI_GETHIGHCONTRAST, HCF_HIGHCONTRASTON
    if not query(0x0042, value.size, ctypes.byref(value), 0):
        return True
    return bool(value.flags & 1)


def trace_palette(native: QPalette) -> QPalette:
    palette = QPalette(native)
    role = QPalette.ColorRole
    for key, value in (
        (role.Window, INK),
        (role.WindowText, TEXT),
        (role.Base, PANEL),
        (role.AlternateBase, RAISED),
        (role.Text, TEXT),
        (role.Button, PANEL),
        (role.ButtonText, TEXT),
        (role.Highlight, LIME),
        (role.HighlightedText, INK),
        (role.Mid, INPUT_BORDER),
        (role.Shadow, RULE),
        (role.PlaceholderText, MUTED),
        (role.ToolTipBase, RAISED),
        (role.ToolTipText, TEXT),
        (role.Link, LIME),
        (role.LinkVisited, MUTED),
    ):
        palette.setColor(key, QColor(value))
    for key in (role.ButtonText, role.Text, role.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, key, QColor(DISABLED))
    return palette


class _TracePresentation(QObject):
    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._application = application
        self._active: bool | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.refresh)
        application.styleHints().colorSchemeChanged.connect(self._queue_refresh)
        application.focusWindowChanged.connect(self._queue_refresh)
        application.paletteChanged.connect(self._queue_refresh)
        self.refresh()

    def _queue_refresh(self, _value: object) -> None:
        self._timer.start(0)

    def refresh(self) -> None:
        use_trace = not _windows_contrast_theme()
        if self._active is use_trace:
            return
        previous = self._active
        self._active = use_trace
        app = self._application
        base_style = app.styleSheet().replace(TRACE_STYLE, "")
        native = app.style().standardPalette()
        if use_trace:
            app.setPalette(trace_palette(native))
            font = app.font()
            if 0 < font.pointSizeF() < 10.5:
                font.setPointSizeF(10.5)
                app.setFont(font)
        elif previous is not None and app.palette() == trace_palette(native):
            app.setPalette(native)
        app.setStyleSheet(base_style + (TRACE_STYLE if use_trace else ""))


def install_trace_presentation(application: QApplication) -> None:
    if application.findChild(_TracePresentation) is None:
        _TracePresentation(application)
