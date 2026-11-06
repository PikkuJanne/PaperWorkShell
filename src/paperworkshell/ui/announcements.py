"""Small, non-queued screen-reader announcements for GUI presentation only."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from PySide6.QtCore import QObject
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent


class ScreenReaderAnnouncer:
    """Keep current outcomes audible without flooding speech with numeric callbacks.

    There is no message queue or retained history. Progress speaks at most once
    every two seconds, and only on a changed phase or quarter checkpoint. An
    operation's actual completion, cancellation or error uses ``announce`` and
    therefore never waits for the progress interval. Numeric completion alone
    does not establish a successful operation.
    """

    def __init__(self, target: QObject, *, clock: Callable[[], float] = monotonic) -> None:
        self._target = target
        self._clock = clock
        self._last_message: tuple[str, bool] | None = None
        self._progress_checkpoint: tuple[str, int, int] | None = None
        self._progress_at: float | None = None

    def reset_progress(self) -> None:
        """Begin a new operation without carrying the old progress checkpoint."""
        self._progress_checkpoint = None
        self._progress_at = None
        self._last_message = None

    def announce(self, message: str, *, assertive: bool = False) -> bool:
        """Speak a changed current message; never log or persist its contents."""
        if not message or self._last_message == (message, assertive):
            return False
        self._last_message = (message, assertive)
        event = QAccessibleAnnouncementEvent(self._target, message)
        event.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(event)
        return True

    def progress(self, message: str, *, key: str, completed: int, total: int) -> bool:
        """Sample meaningful progress, retaining only one checkpoint and time."""
        if (
            type(completed) is not int
            or type(total) is not int
            or total <= 0
            or not 0 <= completed <= total
        ):
            return False
        checkpoint = (key, total, completed * 4 // total)
        now = self._clock()
        if self._progress_checkpoint == checkpoint or (
            self._progress_at is not None and now - self._progress_at < 2.0
        ):
            return False
        self._progress_checkpoint = checkpoint
        self._progress_at = now
        return self.announce(message)
