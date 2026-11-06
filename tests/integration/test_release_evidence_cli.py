from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_release_evidence import make_candidate
from tools.release_evidence import PROJECT_ROOT


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tools.release_evidence", *arguments],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_schema_cli_uses_current_future_old_and_invalid_contract_fixtures() -> None:
    result = _cli("schemas")
    assert result.returncode == 0
    assert (
        "resolved dependency-stage schema, current and rejected fixtures are valid" in result.stdout
    )
    assert result.stderr == ""


@pytest.mark.parametrize("command", ["validate", "freeze"])
def test_cli_without_required_inputs_fails_with_closed_message(command: str) -> None:
    result = _cli(command)
    assert result.returncode == 1
    assert result.stdout == ""
    assert (
        result.stderr
        == "ERROR [RELEASE_ARGUMENTS] Dependency staging evidence could not be verified.\n"
    )


def test_validate_and_freeze_cli_bind_actual_local_synthetic_inputs(tmp_path: Path) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    path = tmp_path / "resolved-inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    arguments = ("--inventory", str(path), "--stage", str(stage), "--artifacts", str(artifacts))
    result = _cli("validate", *arguments)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == "pass"
    output = tmp_path / "frozen"
    result = _cli("freeze", *arguments, "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "dependency-stage.zip" in json.loads(result.stdout)["artifacts"]
    assert (output / "SHA256SUMS").is_file()
    repeated = _cli("freeze", *arguments, "--output", str(output))
    assert repeated.returncode == 1
    assert repeated.stdout == ""
    assert "RELEASE_INPUT" in repeated.stderr
