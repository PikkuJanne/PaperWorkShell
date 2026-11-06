"""Shared deterministic pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from tests.worker_resources import (
    WorkerTestResources,
    cleanup_worker_test_resources,
    create_worker_test_stage_root,
    stage_worker_test_resources,
)


@pytest.fixture(autouse=True)
def dispatch_scheduled_qt_deletions() -> Iterator[None]:
    """Complete UI fixture deleteLater calls as the real application loop would.

    processEvents alone does not deliver DeferredDelete outside app.exec(). A
    pending-save dialog can reject close(), so leaving its scheduled deletion
    undelivered lets an obsolete modal suppress shortcuts in the next test.
    This only destroys objects the owning fixture already scheduled for deletion.
    """
    yield
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session")
def worker_test_resources() -> Iterator[WorkerTestResources]:
    """Provide physical resource trees for live Windows containment tests."""

    resources = stage_worker_test_resources(create_worker_test_stage_root())
    try:
        yield resources
    finally:
        cleanup_worker_test_resources(resources)
