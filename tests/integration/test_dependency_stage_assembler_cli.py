"""Exercise the real entry point without private documents or archive availability."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_assembler_help_needs_no_inputs() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "tools.build_dependency_stage", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0
    assert "--fetch" in process.stdout
    assert "--cache-directory" in process.stdout
    assert process.stderr == ""


def test_assembler_rejects_private_output_scope_without_mutation(tmp_path: Path) -> None:
    private = tmp_path / "SENSITIVE_FIXTURE_TOKEN"
    private.mkdir()
    source = private / "synthetic-document.txt"
    source.write_bytes(b"Synthetic protected original")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.build_dependency_stage",
            "--artifacts",
            str(private / "inputs"),
            "--destination",
            str(private / "stage"),
            "--inventory-output",
            str(private / "inventory.json"),
            "--app-review",
            "unused",
            "--qpdf-review",
            "unused",
            "--gpg-executable",
            "unused",
            "--candidate-id",
            "synthetic",
            "--created-utc",
            "2026-11-03T00:00:00Z",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 1
    assert json.loads(process.stdout) == {"status": "invalid", "code": "RELEASE_STAGE_INVALID"}
    assert process.stderr == ""
    assert "SENSITIVE_FIXTURE_TOKEN" not in process.stdout
    assert str(tmp_path) not in process.stdout
    assert list(private.iterdir()) == [source]
    assert source.read_bytes() == b"Synthetic protected original"
