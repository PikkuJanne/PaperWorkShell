"""One synthetic-only, parented PWS-0602 NVDA entry point with save/retry recovery.

Run from the repository with its configured Python runtime. Use the real
Permanently remove information button or Ctrl+Shift+P to open the editor. The
first Save fails; Retry acknowledges intent in memory. Reopen and Cancel to
review the main-window completion speech and restored action focus. No backend,
worker process/thread, case storage, or external document is opened.
"""

from __future__ import annotations

import sys
from uuid import UUID, uuid4

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker, UiFailure
from paperworkshell.ui.window import PaperWorkShellWindow


def _never_construct_backend() -> OrganizerBackend:
    raise AssertionError("The synthetic manual probe must never construct a backend.")


class RedactionEditorManualProbe:
    """Use production widgets and handlers with correlated, in-memory Qt replies."""

    def __init__(self) -> None:
        self.case_id = UUID("d6c64c7a-41d6-4f93-811e-9e0b1cff7410")
        self.page_id = UUID("ee4c4a7d-b73f-4c9c-8d80-374bec984a06")
        self.revision = 1
        self.marks: tuple[RedactionMark, ...] = (
            RedactionMark(
                UUID("d7e02887-a66d-419c-8ff8-51a7490f1926"),
                self.page_id,
                NormalizedPageBox(150_000, 250_000, 700_000, 150_000),
            ),
        )
        self.active_editor: RedactionToolsState | None = None
        self.fail_next_save = True
        self.commands = OrganizerBackendCommands()
        # This QObject supplies the real signal contract only. Never connect_worker,
        # initialize it, move it to a thread, or construct its backend factory.
        self.signals = OrganizerBackendWorker(_never_construct_backend)
        self.window = PaperWorkShellWindow(self.commands, self.signals)
        self.commands.redaction_tools_begin_requested.connect(self.begin)
        self.commands.redaction_tools_save_requested.connect(self.save)
        self.commands.redaction_tools_cancel_requested.connect(self.cancel)
        self.commands.redaction_tools_abort_requested.connect(self.abort)
        self.commands.shutdown_requested.connect(self.signals.shutdown_complete.emit)
        self.signals.redaction_tools_started.connect(self._editor_opened)

    def start(self) -> None:
        """Show the real main window; the human opens its modal editor normally."""
        self.window.show()
        self.window.setWindowTitle(
            f"PWS-0602 synthetic NVDA review — effective DPR {self.window.devicePixelRatioF():.2f}"
        )
        QTimer.singleShot(0, self.initialize)

    def organizer_state(self) -> OrganizerViewState:
        return OrganizerViewState(
            self.case_id,
            self.revision,
            (OrganizerPageView(self.page_id, 1, 0, True),),
            False,
            False,
            False,
        )

    def initialize(self) -> None:
        self.signals.initialized.emit()
        self.signals.state_changed.emit(self.organizer_state())
        # Known pixels generated here need no document adapter or worker request.
        preview = QPixmap(600, 780)
        preview.fill(QColor("white"))
        painter = QPainter(preview)
        painter.setFont(QFont("Arial", 22))
        painter.drawText(50, 90, "SYNTHETIC REVIEW PAGE")
        painter.drawText(110, 255, "LARGE REMOVAL TARGET")
        painter.drawText(110, 500, "RETAINED SENTINEL")
        painter.end()
        self.window._model.set_preview(self.page_id, preview)

    def _editor_opened(self, _operation_id: int, _state: object) -> None:
        dialog = self.window._redaction_tools_dialog
        if dialog is not None:
            dialog.setWindowTitle(
                f"PWS-0602 synthetic NVDA review — effective DPR {dialog.devicePixelRatioF():.2f}"
            )

    def begin(self, operation_id: int, page_id: object) -> None:
        if page_id != self.page_id or self.active_editor is not None:
            self.signals.redaction_tools_start_failed.emit(*self._failure_id(operation_id))
            return
        self.active_editor = RedactionToolsState(
            self.case_id,
            self.revision,
            self.page_id,
            uuid4(),
            self.marks,
            selected_mark_id=self.marks[0].mark_id if self.marks else None,
        )
        self.signals.state_changed.emit(self.organizer_state())
        self.signals.redaction_tools_started.emit(operation_id, self.active_editor)

    @staticmethod
    def _failure_id(operation_id: int) -> tuple[int, UiFailure]:
        return operation_id, UiFailure("PWS_UI_UNEXPECTED", "Try again.", True, operation_id)

    def save(self, operation_id: int, draft: object) -> None:
        opening = self.active_editor
        if (
            type(draft) is not RedactionToolsState
            or opening is None
            or draft.case_id != self.case_id
            or draft.manifest_revision != self.revision
            or draft.page_id != self.page_id
            or draft.editor_session_id != opening.editor_session_id
        ):
            self.signals.redaction_tools_save_failed.emit(*self._failure_id(operation_id))
            return
        if self.fail_next_save:
            self.fail_next_save = False
            QTimer.singleShot(
                0,
                lambda: self.signals.redaction_tools_save_failed.emit(
                    *self._failure_id(operation_id)
                ),
            )
            return
        self.marks = draft.records
        self.revision += 1
        self.active_editor = None
        result = self.organizer_state()

        def confirm() -> None:
            self.signals.state_changed.emit(result)
            self.signals.redaction_tools_saved.emit(operation_id, result)

        QTimer.singleShot(0, confirm)

    def cancel(self, operation_id: int, draft: object) -> None:
        if (
            type(draft) is RedactionToolsState
            and self.active_editor is not None
            and draft.editor_session_id == self.active_editor.editor_session_id
        ):
            self.active_editor = None
            self.signals.redaction_tools_cancelled.emit(operation_id)

    def abort(self, operation_id: int) -> None:
        self.active_editor = None
        self.signals.redaction_tools_cancelled.emit(operation_id)


def main() -> int:
    app = QApplication(sys.argv)
    probe = RedactionEditorManualProbe()
    probe.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
