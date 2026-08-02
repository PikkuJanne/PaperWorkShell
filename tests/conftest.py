"""Shared deterministic pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.worker_resources import (
    WorkerTestResources,
    cleanup_worker_test_resources,
    create_worker_test_stage_root,
    stage_worker_test_resources,
)


@pytest.fixture(scope="session")
def worker_test_resources() -> Iterator[WorkerTestResources]:
    """Provide physical resource trees for live Windows containment tests."""

    resources = stage_worker_test_resources(create_worker_test_stage_root())
    try:
        yield resources
    finally:
        cleanup_worker_test_resources(resources)
