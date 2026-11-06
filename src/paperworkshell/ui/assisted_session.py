"""GUI-thread privacy curtain and bounded scheduler for assisted case access."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.domain.assisted_use import (
    ASSISTED_USE_IDLE_LOCK_SECONDS,
    AssistedUseErrorCode,
    AssistedUseSnapshot,
    AssistedUseStatus,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.ui.assisted_use import (
    AssistedConsentDialog,
    AssistedHandoverDialog,
    AssistedLockScreen,
    AssistedUnlockDialog,
)
from paperworkshell.ui.backend import OperationCancellation

if TYPE_CHECKING:
    from paperworkshell.ui.window import PaperWorkShellWindow


class _PrivacyCurtain(QDialog):
    """Escape and the window close button must never uncover case content."""

    def reject(self) -> None:
        return


class AssistedSessionController(QObject):
    """Hide every owned document surface before waiting on any backend work.

    The GUI uses only a content-free snapshot and local clocks. Filesystem checks
    and passphrase derivation stay on the existing serialized backend thread.
    There is at most one outstanding scheduler request and one unlock dialog.
    """

    def __init__(self, window: PaperWorkShellWindow) -> None:
        super().__init__(window)
        self._window = window
        self._snapshot: AssistedUseSnapshot | None = None
        self._last_input = monotonic()
        self._last_wall = datetime.now(UTC)
        self._check_pending = False
        self._curtain: _PrivacyCurtain | None = None
        self._unlock_dialog: AssistedUnlockDialog | None = None
        self._unlock_request_id: int | None = None
        self._unlock_cancellation: OperationCancellation | None = None
        self._hidden: list[QWidget] = []
        self._privacy_epoch = 0
        self.locked = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        worker = window._worker
        worker.assisted_use_changed.connect(self.changed)
        worker.assisted_check_finished.connect(self._check_finished)
        worker.assisted_unlock_failed.connect(self._unlock_failed)
        worker.assisted_unlocked.connect(self._unlocked)
        worker.assisted_unlock_finished.connect(self._unlock_finished)
        worker.assisted_handover.connect(self._handover)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.installEventFilter(self)

    @property
    def active(self) -> bool:
        return self._snapshot is not None

    def start(self) -> None:
        """Complete local consent before queuing any new assisted case."""

        window = self._window
        if (
            self.active
            or self._unlock_request_id is not None
            or not window._controls_enabled
            or window._closing_requested
        ):
            return
        if window._state is not None and window._state.case_id is not None:
            return
        dialog = AssistedConsentDialog(window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            window._commands.create_assisted_case_requested.emit(
                dialog.retention_days, dialog.take_passphrase(), dialog.consent_confirmed
            )
        dialog.deleteLater()

    @Slot(object)
    def changed(self, snapshot: object) -> None:
        if snapshot is None:
            self._snapshot = None
            self._timer.stop()
            self._uncover(restore_content=False)
            self._window._assisted_lock_button.hide()
            return
        if type(snapshot) is not AssistedUseSnapshot:
            return
        previous = self._snapshot
        self._snapshot = snapshot
        if snapshot.status is AssistedUseStatus.ORDINARY:
            self._snapshot = None
            self._timer.stop()
            self._uncover(restore_content=False)
            return
        self._window._case_list.clear()
        self._window._assisted_lock_button.show()
        self._timer.start()
        if snapshot.status is AssistedUseStatus.UNLOCKED:
            if self.locked:
                # An old queued checkpoint cannot authorize an unlock.
                return
            # Periodic checkpoints must not count as user input.
            if previous is None or previous.status is not AssistedUseStatus.UNLOCKED or self.locked:
                self._last_input = monotonic()
                self._last_wall = datetime.now(UTC)
            self._uncover()
        else:
            self.cover(revoke=False)

    @Slot()
    def _check_finished(self) -> None:
        """Only the scheduler request's acknowledgement releases its queue slot."""

        self._check_pending = False

    @Slot()
    def tick(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        now = datetime.now(UTC)
        expired = snapshot.expires_at is not None and now >= snapshot.expires_at
        backwards = now < self._last_wall
        if monotonic() - self._last_input >= ASSISTED_USE_IDLE_LOCK_SECONDS or expired or backwards:
            self.cover(revoke=expired or backwards, clock_review=backwards)
        self._last_wall = max(self._last_wall, now)
        if not self._check_pending:
            self._check_pending = True
            self._window._commands.assisted_check_requested.emit(snapshot.case_id)

    def cover(self, *, revoke: bool = True, clock_review: bool = False) -> None:
        """Hide first, then deny further commands and request cooperative cancellation."""

        if self._snapshot is None:
            return
        window = self._window
        must_revoke = revoke or not self.locked
        self.locked = True
        window._application_scroll.hide()
        window.menuBar().hide()
        window.setAccessibleName(tr("PaperWorkShell assisted case locked"))
        for shortcut in window._shortcuts:
            shortcut.setEnabled(False)
        if must_revoke:
            self._privacy_epoch += 1
            if self._unlock_cancellation is not None:
                self._unlock_cancellation.cancel()
            window._worker.lock_assisted_immediately()
            if self._unlock_dialog is not None:
                self._unlock_dialog.reject()
        if self._curtain is None:
            self._curtain = _PrivacyCurtain(window)
            self._curtain.setObjectName("assistedPrivacyCurtain")
            self._curtain.setWindowTitle(tr("Assisted case locked"))
            self._curtain.setAccessibleName(tr("Assisted case locked"))
            self._curtain.setWindowModality(Qt.WindowModality.ApplicationModal)
            self._curtain.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
            new_screen = AssistedLockScreen(self._curtain)
            new_screen.unlock_requested.connect(self.unlock)
            new_screen.end_requested.connect(window.request_shutdown)
            layout = QVBoxLayout(self._curtain)
            layout.addWidget(new_screen)
        for widget in QApplication.topLevelWidgets():
            if self._is_content_window(widget) and widget.isVisible():
                self._remember_hidden(widget)
        screen = self._curtain.findChild(AssistedLockScreen)
        if screen is not None:
            expired = self._snapshot.status is AssistedUseStatus.EXPIRED or (
                self._snapshot.expires_at is not None
                and datetime.now(UTC) >= self._snapshot.expires_at
            )
            screen.set_expired(expired)
            if not expired:
                screen.set_clock_review(
                    clock_review or self._snapshot.status is AssistedUseStatus.CLOCK_REVIEW
                )
            screen.set_unlock_pending(self._unlock_request_id is not None)
        if not self._curtain.isVisible():
            self._curtain.show()
            if screen is not None:
                screen.focus_recovery()
        window._cancel_active_operation_silently()
        window._cancel_package_operation()
        window._discard_post_export_deletion_for_privacy()
        window._cancel_scan_review_preview()

    def _is_content_window(self, widget: QWidget) -> bool:
        if not widget.isWindow():
            return False
        if widget is self._window or widget is self._curtain or widget is self._unlock_dialog:
            return False
        return self._window._owns_ui_widget(widget)

    def _remember_hidden(self, widget: QWidget) -> None:
        if widget not in self._hidden:
            self._hidden.append(widget)
        widget.hide()

    def discard_hidden_window(self, widget: QWidget) -> None:
        """Do not restore a content dialog whose review authority was discarded."""
        self._hidden = [hidden for hidden in self._hidden if hidden is not widget]

    def _uncover(self, *, restore_content: bool = True) -> None:
        if not self.locked:
            return
        self.locked = False
        if not restore_content:
            self._privacy_epoch += 1
            if self._unlock_cancellation is not None:
                self._unlock_cancellation.cancel()
                self._window._worker.lock_assisted_immediately()
            self._window._discard_post_export_content_for_closed_privacy()
            # Clear old page/preview semantics before revealing the case chooser.
            self._window._state_changed(OrganizerViewState.empty())
        if self._unlock_dialog is not None:
            self._unlock_dialog.finished.disconnect(self._unlock_submitted)
            self._unlock_dialog.done(QDialog.DialogCode.Accepted)
            self._unlock_dialog.deleteLater()
            self._unlock_dialog = None
        if self._curtain is not None:
            self._curtain.done(QDialog.DialogCode.Accepted)
            self._curtain.deleteLater()
            self._curtain = None
        window = self._window
        window._application_scroll.show()
        window.menuBar().show()
        window.setAccessibleName(tr("PaperWorkShell guided packet builder"))
        for shortcut in window._shortcuts:
            shortcut.setEnabled(True)
        hidden, self._hidden = self._hidden, []
        for widget in hidden:
            try:
                if restore_content:
                    widget.show()
                elif isinstance(widget, QDialog):
                    widget.reject()
            except RuntimeError:
                # A canceled operation may have destroyed its dialog while hidden.
                pass
        window._resume_post_export_feedback()

    @Slot()
    def unlock(self) -> None:
        if (
            not self.locked
            or self._snapshot is None
            or self._unlock_dialog is not None
            or self._unlock_request_id is not None
        ):
            return
        dialog = AssistedUnlockDialog(self._curtain)
        self._unlock_dialog = dialog
        dialog.finished.connect(self._unlock_submitted)
        dialog.open()

    @Slot(int)
    def _unlock_submitted(self, result: int) -> None:
        dialog = self._unlock_dialog
        if dialog is None:
            return
        if result == QDialog.DialogCode.Accepted and self._snapshot is not None:
            if self._unlock_request_id is not None:
                dialog.show()
                dialog.set_pending(True)
                return
            secret = dialog.take_passphrase()
            self._privacy_epoch += 1
            self._unlock_request_id = self._privacy_epoch
            self._unlock_cancellation = OperationCancellation()
            # Retain the one masked recovery dialog until the correlated result.
            dialog.show()
            dialog.set_pending(True)
            self._update_unlock_pending()
            self._window._commands.unlock_assisted_case_requested.emit(
                self._privacy_epoch, self._snapshot.case_id, secret, self._unlock_cancellation
            )
        else:
            self._privacy_epoch += 1
            if self._unlock_cancellation is not None:
                self._unlock_cancellation.cancel()
            self._window._worker.lock_assisted_immediately()
            dialog.deleteLater()
            self._unlock_dialog = None
            self._update_unlock_pending()
            self._focus_unlock_recovery()

    @Slot(int, object)
    def _unlock_failed(self, request_id: int, code: object = None) -> None:
        if (
            request_id == self._privacy_epoch
            and request_id == self._unlock_request_id
            and self._unlock_dialog is not None
        ):
            self._unlock_dialog.set_pending(False)
            self._unlock_dialog.show_failure(code if type(code) is AssistedUseErrorCode else None)

    @Slot(int)
    def _unlock_finished(self, request_id: int) -> None:
        if request_id != self._unlock_request_id:
            return
        cancellation = self._unlock_cancellation
        self._unlock_request_id = None
        self._unlock_cancellation = None
        if self._unlock_dialog is not None:
            self._unlock_dialog.set_pending(False)
        self._update_unlock_pending()
        if self.locked and self._unlock_dialog is None:
            self._focus_unlock_recovery()
        window = self._window
        state = window._state
        snapshot = self._snapshot
        current = window._worker.current_assisted_snapshot()
        now = datetime.now(UTC)
        if (
            request_id == self._privacy_epoch
            and cancellation is not None
            and not cancellation.cancelled()
            and not self.locked
            and not window._case_close_pending
            and not window._closing_requested
            and state is not None
            and snapshot is not None
            and snapshot.status is AssistedUseStatus.UNLOCKED
            and state.case_id == snapshot.case_id
            and current is not None
            and current.case_id == snapshot.case_id
            and current.status is AssistedUseStatus.UNLOCKED
            and snapshot.expires_at is not None
            and self._last_wall <= now < snapshot.expires_at
        ):
            if state.recovery_review_required:
                window._review_required(
                    tr(
                        "Case recovered from the validated save journal. Review the page organizer before continuing."
                    )
                )
            else:
                window._announce(
                    tr("Case opened with {value1} active pages.", value1=len(state.pages))
                )

    def _update_unlock_pending(self) -> None:
        if self._curtain is not None:
            screen = self._curtain.findChild(AssistedLockScreen)
            if screen is not None:
                screen.set_unlock_pending(self._unlock_request_id is not None)

    def _focus_unlock_recovery(self) -> None:
        if self._curtain is not None:
            screen = self._curtain.findChild(AssistedLockScreen)
            if screen is not None:
                screen.focus_recovery()

    @Slot(int, object)
    def _unlocked(self, request_id: int, snapshot: object) -> None:
        if request_id != self._privacy_epoch or request_id != self._unlock_request_id:
            return
        current = self._window._worker.current_assisted_snapshot()
        if (
            current is not None
            and self._snapshot is not None
            and current.case_id == self._snapshot.case_id
            and current.status in (AssistedUseStatus.EXPIRED, AssistedUseStatus.CLOCK_REVIEW)
        ):
            self._snapshot = current
            self.cover(clock_review=current.status is AssistedUseStatus.CLOCK_REVIEW)
            return
        if (
            self._unlock_dialog is None
            or self._unlock_cancellation is None
            or self._unlock_cancellation.cancelled()
            or type(snapshot) is not AssistedUseSnapshot
            or snapshot.status is not AssistedUseStatus.UNLOCKED
            or self._snapshot is None
            or snapshot.case_id != self._snapshot.case_id
            or current is None
            or current.case_id != snapshot.case_id
            or current.status is not AssistedUseStatus.UNLOCKED
        ):
            self._window._worker.lock_assisted_immediately()
            self._unlock_failed(request_id, AssistedUseErrorCode.LOCKED)
            return
        now = datetime.now(UTC)
        if snapshot.expires_at is None or now >= snapshot.expires_at or now < self._last_wall:
            self.cover(clock_review=now < self._last_wall)
            return
        self._uncover()
        self.changed(snapshot)

    @Slot(object)
    def _handover(self, snapshot: object) -> None:
        if type(snapshot) is AssistedUseSnapshot and snapshot.expires_at is not None:
            dialog = AssistedHandoverDialog(snapshot.case_id, snapshot.expires_at, self._window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.open()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._snapshot is None:
            return False
        if event.type() is QEvent.Type.ApplicationStateChange:
            application = QApplication.instance()
            if isinstance(application, QApplication) and (
                application.applicationState() is not Qt.ApplicationState.ApplicationActive
            ):
                self.cover()
        if not isinstance(watched, QWidget) or not self._window._owns_ui_widget(watched):
            return False
        if self.locked:
            if event.type() is QEvent.Type.Show and self._is_content_window(watched):
                self._remember_hidden(watched)
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress):
                owner = watched.window()
                return owner is not self._curtain and owner is not self._unlock_dialog
        elif event.type() in (
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.MouseMove,
        ):
            # Input arriving after the deadline cannot revive the session.
            self.tick()
            if not self.locked:
                self._last_input = monotonic()
                self._window._worker.assisted_activity()
        return False
