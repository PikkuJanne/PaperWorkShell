"""Exercise the offline audit CLI without machine-specific native archives."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.audit_tesseract_redistribution import PROJECT_ROOT


@pytest.mark.parametrize("exists", [False, True])
def test_missing_or_empty_artifacts_fail_closed_without_path_disclosure(
    tmp_path: Path, exists: bool
) -> None:
    artifacts = tmp_path / "PRIVATE_PATH_TOKEN"
    if exists:
        artifacts.mkdir()
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.audit_tesseract_redistribution",
            "--artifacts",
            str(artifacts),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "invalid"
    assert payload["release_approved"] is False
    assert "PRIVATE_PATH_TOKEN" not in completed.stdout
    assert str(tmp_path) not in completed.stdout
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before


def test_help_needs_no_archive_or_external_engine() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tools.audit_tesseract_redistribution", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0
    assert "--artifacts" in completed.stdout
    assert "not an SBOM or shipment approval" in " ".join(completed.stdout.split())
    assert completed.stderr == ""
