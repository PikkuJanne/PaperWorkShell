"""Accessible exact local support preview and one cancelable publication thread."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell import __version__
from paperworkshell.app.diagnostic_bundle import DiagnosticBundleService
from paperworkshell.diagnostics.bundle import DiagnosticBundlePreview
from paperworkshell.diagnostics.errors import (
    DiagnosticError,
    DiagnosticErrorCode,
    DiagnosticErrorContext,
)
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticEventCode,
    DiagnosticRecord,
    DiagnosticVersion,
    DiagnosticVersionKind,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.localization.catalog import tr
from paperworkshell.storage.diagnostic_bundle import WindowsDiagnosticBundleWriter
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.localization_layout import adapt_localized_content


def session_bundle_service(policy: ResourcePolicy) -> DiagnosticBundleService:
    """Default desktop scope: numeric session-start versions, no worker collection."""

    logger = SanitizedDiagnosticLogger()
    logger.emit(
        DiagnosticRecord(
            occurred_at=datetime.now(UTC),
            event=DiagnosticEventCode.APPLICATION_STARTED,
            versions=(
                DiagnosticVersion(
                    DiagnosticVersionKind.APPLICATION,
                    tuple(int(part) for part in __version__.split(".")),
                ),
                DiagnosticVersion(
                    DiagnosticVersionKind.PYTHON_RUNTIME, tuple(sys.version_info[:3])
                ),
            ),
        )
    )
    return DiagnosticBundleService(logger, WindowsDiagnosticBundleWriter(policy))


class _BundleThread(QThread):
    def __init__(
        self,
        service: DiagnosticBundleService,
        preview: DiagnosticBundlePreview,
        destination: Path,
        cancellation: Event,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._preview = preview
        self._destination = destination
        self._cancellation = cancellation
        self.error: DiagnosticErrorCode | None = None

    def run(self) -> None:
        try:
            self._service.save(
                self._preview,
                self._destination,
                consent=True,
                cancellation=self._cancellation.is_set,
            )
        except DiagnosticError as error:
            self.error = error.code
        except Exception:
            self.error = DiagnosticErrorCode.EXPORT_FAILED


class DiagnosticBundleDialog(QDialog):
    """No creation on opening; unchecked consent and a separate Save gate export."""

    def __init__(self, service: DiagnosticBundleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: _BundleThread | None = None
        self._preview: DiagnosticBundlePreview | None = None
        self._cancellation = Event()
        self._close_requested = False
        self._announcer = ScreenReaderAnnouncer(self)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._reveal_current_focus)
        self.setObjectName("diagnosticBundleDialog")
        self.setWindowTitle(tr("Create support bundle"))
        self.setAccessibleName(tr("Review the exact local support bundle before saving"))
        self.setModal(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        guidance = QLabel(
            tr(
                "Review every included file below. Only approved recorded fields are included. The default desktop session records numeric application/runtime versions. This dialog does not scan cases, worker activity or ordinary logs. Nothing is uploaded. Saved support bundles remain in your chosen folder until you delete them."
            )
        )
        guidance.setWordWrap(True)
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        rows.addWidget(guidance)
        self._inventory = QPlainTextEdit()
        self._inventory.setObjectName("diagnosticBundleInventory")
        self._inventory.setAccessibleName(
            tr("Exact support bundle files, hashes and complete contents")
        )
        self._inventory.setReadOnly(True)
        self._inventory.setTabChangesFocus(True)
        self._inventory.setMinimumHeight(140)
        rows.addWidget(self._inventory, 1)
        self._consent = QCheckBox(
            tr("I reviewed these exact files and choose to save them locally.")
        )
        self._consent.setObjectName("diagnosticBundleConsent")
        self._consent.setAccessibleName(self._consent.text())
        rows.addWidget(self._consent)
        self._save = QPushButton(tr("Choose location and save"))
        self._save.setObjectName("diagnosticBundleSave")
        self._save.setAccessibleName(
            tr("Choose a new local ZIP location and save reviewed support bundle")
        )
        self._save.setEnabled(False)
        self._save.setAutoDefault(False)
        self._save.clicked.connect(self._choose_save)
        rows.addWidget(self._save)
        self._status = QLabel(tr("Review only. No file has been created."))
        self._status.setObjectName("diagnosticBundleStatus")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        rows.addWidget(self._status)
        self._cancel = QPushButton(tr("Close without saving"))
        self._cancel.setObjectName("diagnosticBundleClose")
        self._cancel.setAccessibleName(self._cancel.text())
        self._cancel.setAutoDefault(False)
        self._cancel.clicked.connect(self.reject)
        rows.addWidget(self._cancel)
        self._consent.toggled.connect(self._consent_changed)
        QWidget.setTabOrder(self._inventory, self._consent)
        QWidget.setTabOrder(self._consent, self._save)
        QWidget.setTabOrder(self._save, self._status)
        QWidget.setTabOrder(self._status, self._cancel)
        try:
            self._preview = service.preview()
            self._inventory.setPlainText(self._preview.render())
        except Exception:
            self._inventory.setPlainText(
                tr("A safe support preview could not be collected. Nothing was saved.")
            )
            self._consent.setEnabled(False)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(content)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        QVBoxLayout(self).addWidget(self._scroll)
        self.resize(720, 560)
        adapt_localized_content(self._scroll)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._reveal_focus)
        self._inventory.setFocus()

    def _reveal_focus(self, _previous: QWidget | None, current: QWidget | None) -> None:
        if current is not None and self.isAncestorOf(current):
            self._focus_timer.start(0)

    def _reveal_current_focus(self) -> None:
        current = QApplication.focusWidget()
        if current is not None and self.isAncestorOf(current):
            content = self._scroll.widget()
            if content is None:
                return
            # Text edits expose an input-method cursor rectangle; ensuring only
            # that rectangle can leave the reviewed inventory below the screen.
            center = current.mapTo(content, current.rect().center())
            margin = min(current.height() // 2 + 4, self._scroll.viewport().height() // 2 - 4)
            self._scroll.ensureVisible(center.x(), center.y(), 0, max(0, margin))

    @property
    def busy(self) -> bool:
        """Keep application ownership alive until the publication thread finishes."""

        return self._thread is not None

    def _consent_changed(self, _checked: bool) -> None:
        self._save.setEnabled(
            self._preview is not None and self._thread is None and self._consent.isChecked()
        )

    def _choose_save(self) -> None:
        if self._preview is None or self._thread is not None or not self._consent.isChecked():
            return
        # A prefilled filename can override an accessibility edit in the Windows picker.
        destination, _selected = QFileDialog.getSaveFileName(
            self,
            tr("Save reviewed support bundle"),
            str(Path.home()),
            tr("ZIP archives (*.zip)"),
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not destination:
            return
        self._start_save(Path(destination))

    def _start_save(self, destination: Path) -> None:
        if self._preview is None or self._thread is not None or not self._consent.isChecked():
            return
        self._cancellation.clear()
        self._close_requested = False
        self._consent.setEnabled(False)
        self._save.setEnabled(False)
        self._cancel.setText(tr("Cancel saving"))
        self._cancel.setAccessibleName(self._cancel.text())
        self._status.setText(
            tr("Saving the reviewed bundle locally. You can cancel before publication.")
        )
        self._announcer.announce(self._status.text())
        self._thread = _BundleThread(
            self._service, self._preview, destination, self._cancellation, self
        )
        self._thread.finished.connect(self._saved)
        self._thread.start()

    def _saved(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        code = thread.error
        thread.deleteLater()
        message = (
            tr("Support bundle saved locally. No information was sent.")
            if code is None
            else DiagnosticError(code, DiagnosticErrorContext.BUNDLE).safe_message
        )
        self._status.setText(message)
        self._announcer.announce(message)
        self._cancel.setText(tr("Close"))
        self._cancel.setAccessibleName(self._cancel.text())
        self._consent.setEnabled(True)
        self._consent_changed(self._consent.isChecked())
        self._status.setFocus()
        if self._close_requested:
            super().reject()

    def reject(self) -> None:
        if self._thread is not None:
            self._close_requested = True
            self._cancellation.set()
            self._status.setText(tr("Cancellation requested. Waiting for safe cleanup."))
            self._announcer.announce(self._status.text())
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)
