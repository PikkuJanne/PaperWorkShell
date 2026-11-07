"""Bounded waits for measured Qt geometry after an explicit test resize."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QElapsedTimer
from PySide6.QtTest import QTest


def wait_for_geometry(predicate: Callable[[], bool]) -> None:
    """Drain posted native-layout work until the actual geometry satisfies a check."""

    elapsed = QElapsedTimer()
    elapsed.start()
    while elapsed.elapsed() < 1_000:
        QCoreApplication.processEvents()
        # A native footer can rebuild its row between layout events. Yield to
        # its coalesced callback, then check the real target, never a time alone.
        QTest.qWait(1)
        QCoreApplication.processEvents()
        if predicate():
            return
    raise AssertionError("The requested widget geometry did not settle within one second.")
