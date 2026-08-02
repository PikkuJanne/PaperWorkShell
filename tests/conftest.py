"""Shared deterministic pytest fixtures."""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest

from tests.worker_resources import WorkerTestResources, stage_worker_test_resources


@pytest.fixture(scope="session")
def worker_test_resources(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[WorkerTestResources]:
    """Provide physical resource trees for live Windows containment tests."""

    resources = stage_worker_test_resources(tmp_path_factory.mktemp("pws-worker-resources"))
    try:
        yield resources
    finally:
        shutil.rmtree(resources.stage_root)
