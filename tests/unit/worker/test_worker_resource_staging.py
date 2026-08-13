"""Failure-path coverage for Windows live-worker test-resource staging."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import worker_resources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 resource staging targets supported Windows hosts"
)


def test_runtime_audit_failure_removes_random_fixed_drive_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_root = worker_resources.create_worker_test_stage_root()

    def fail_audit(_root: Path, _budget: object) -> tuple[Path, ...]:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(worker_resources, "_audit_runtime_source", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            worker_resources.stage_worker_test_resources(stage_root)
        assert not os.path.lexists(stage_root)
    finally:
        if os.path.lexists(stage_root):
            worker_resources._remove_stage_root(stage_root)


def test_pillow_stage_source_requires_the_exact_admitted_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = worker_resources._pillow_package_source()
    assert package_root.name == "PIL"
    assert (package_root / "__init__.py").is_file()

    monkeypatch.setattr("tests.worker_resources.PIL.__version__", "12.3.1")
    with pytest.raises(RuntimeError, match="admitted Windows Pillow test runtime"):
        worker_resources._pillow_package_source()
