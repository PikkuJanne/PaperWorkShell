"""Production handoff CLI stays fail-closed without approvals; no installer runs."""

from __future__ import annotations

import subprocess
import sys

from tools.sign_windows_installer import PROJECT_ROOT


def test_production_help_and_missing_approval_cli() -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "tools.sign_windows_installer", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    assert help_result.returncode == 0
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.sign_windows_installer",
            "build",
            "--private-key",
            "private-canary",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR [SIGNING_ARGUMENTS] Installer signing could not be verified.\n"
