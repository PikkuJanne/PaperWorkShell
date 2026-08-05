"""Qt application lifecycle for the thread-confined organizer backend."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QEventLoop,
    Qt,
    QThread,
    QTimer,
)
from PySide6.QtWidgets import QApplication

from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendFactory,
    OrganizerBackendWorker,
)
from paperworkshell.ui.window import PaperWorkShellWindow

_FALLBACK_SHUTDOWN_WAIT_MS = 10_000


class _ShutdownWaitOutcome(StrEnum):
    COMPLETE = "complete"
    REFUSED = "refused"
    TIMEOUT = "timeout"


def _wait_for_backend_shutdown(
    backend_thread: QThread,
    worker: OrganizerBackendWorker,
    *,
    timeout_ms: int,
) -> _ShutdownWaitOutcome:
    """Process main-thread signals while retaining the backend event loop on timeout."""

    if not backend_thread.isRunning():
        return _ShutdownWaitOutcome.COMPLETE
    outcome = _ShutdownWaitOutcome.TIMEOUT
    wait_loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)

    def completed() -> None:
        nonlocal outcome
        outcome = _ShutdownWaitOutcome.COMPLETE
        wait_loop.quit()

    def refused() -> None:
        nonlocal outcome
        outcome = _ShutdownWaitOutcome.REFUSED
        wait_loop.quit()

    backend_thread.finished.connect(completed)
    worker.shutdown_refused.connect(refused)
    timer.timeout.connect(wait_loop.quit)
    timer.start(timeout_ms)
    wait_loop.exec()
    timer.stop()
    backend_thread.finished.disconnect(completed)
    try:
        worker.shutdown_refused.disconnect(refused)
    except RuntimeError:
        # A successful shutdown may already have processed worker.deleteLater
        # on the owner thread, which also removes this temporary connection.
        pass
    if not backend_thread.isRunning():
        return _ShutdownWaitOutcome.COMPLETE
    return outcome


def run_desktop_application(
    factory: OrganizerBackendFactory,
    argv: Sequence[str] | None = None,
) -> int:
    """Run the accessible desktop shell until its backend closes cleanly."""

    arguments = list(argv) if argv is not None else []
    application = QApplication(arguments)
    application.setApplicationDisplayName("PaperWorkShell")
    application.setApplicationName("PaperWorkShell")
    application.setOrganizationName("PaperWorkShell")
    application.setQuitOnLastWindowClosed(False)

    commands = OrganizerBackendCommands()
    backend_thread = QThread()
    backend_thread.setObjectName("PaperWorkShellBackendThread")
    worker = OrganizerBackendWorker(factory)
    worker.moveToThread(backend_thread)
    commands.connect_worker(worker)
    backend_thread.started.connect(worker.initialize)
    worker.shutdown_complete.connect(worker.deleteLater)
    worker.shutdown_complete.connect(
        backend_thread.quit,
        Qt.ConnectionType.DirectConnection,
    )
    backend_thread.finished.connect(application.quit)

    window = PaperWorkShellWindow(commands, worker)
    window.show()
    backend_thread.start()
    exit_code = application.exec()

    while backend_thread.isRunning():
        window.request_shutdown()
        outcome = _wait_for_backend_shutdown(
            backend_thread,
            worker,
            timeout_ms=_FALLBACK_SHUTDOWN_WAIT_MS,
        )
        if outcome is _ShutdownWaitOutcome.COMPLETE:
            break
        if outcome is _ShutdownWaitOutcome.TIMEOUT:
            window.shutdown_wait_timed_out()
        # A refused save needs user recovery; a timed-out bounded operation
        # must retain its backend event loop until it completes or refuses.
        exit_code = application.exec()

    backend_thread.wait()
    window.deleteLater()
    backend_thread.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    return exit_code
