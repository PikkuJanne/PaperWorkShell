"""Real signing CLI handoff and unsigned-native rejection, without worker use."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.unit.test_windows_signing import unsigned_pe, write_policy
from tools import windows_signing as signing


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tools.windows_signing", *arguments],
        cwd=signing.PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )


def test_help_and_rejected_arguments_have_no_signing_or_credential_side_effects() -> None:
    assert _cli("--help").returncode == 0
    result = _cli("verify", "--private-key", "private-secret-canary")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ERROR [SIGNING_ARGUMENTS] Windows signing could not be verified.\n"


def test_real_cli_prepares_exact_request_then_rejects_unsigned_pe() -> None:
    parent = signing.PROJECT_ROOT / "output/pws0802"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="signing-cli-", dir=parent) as temporary:
        candidate = Path(temporary)
        policy, digest = write_policy(candidate / "policy.json")
        target = candidate / "synthetic.exe"
        target.write_bytes(unsigned_pe())
        common = [
            "--candidate-root",
            str(candidate),
            "--policy",
            str(policy),
            "--policy-sha256",
            digest,
            "--file",
            target.name,
        ]
        prepared = _cli("prepare", *common, "--output", str(candidate / "request.json"))
        assert prepared.returncode == 0, prepared.stderr
        receipt = json.loads(prepared.stdout)
        assert (
            receipt["request_sha256"]
            == hashlib.sha256((candidate / "request.json").read_bytes()).hexdigest()
        )
        rejected = _cli("verify", *common)
        assert rejected.returncode == 1
        assert (
            rejected.stderr == "ERROR [SIGNING_NOT_SIGNED] Windows signing could not be verified.\n"
        )
        assert target.read_bytes() == unsigned_pe()


@pytest.mark.skipif(os.name != "nt", reason="Windows native input")
def test_actual_reviewed_unsigned_tesseract_executable_is_rejected_without_execution() -> None:
    source = (
        signing.PROJECT_ROOT
        / "output/pws0801/resume-20261103/resolved-stage-r3/runtime/engines/tesseract/bin/tesseract.exe"
    )
    if not source.is_file():
        pytest.skip("optional retained PWS-0801 native stage is absent")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    parent = signing.PROJECT_ROOT / "output/pws0802"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="signing-native-", dir=parent) as temporary:
        candidate = Path(temporary)
        policy, digest = write_policy(candidate / "synthetic-public-policy.json")
        target = candidate / "native-unsigned.exe"
        shutil.copyfile(source, target)
        result = _cli(
            "verify",
            "--candidate-root",
            str(candidate),
            "--policy",
            str(policy),
            "--policy-sha256",
            digest,
            "--file",
            target.name,
        )
        assert result.returncode == 1
        assert (
            result.stderr == "ERROR [SIGNING_NOT_SIGNED] Windows signing could not be verified.\n"
        )
        inspected = signing.inspect_authenticode(target)
        assert inspected == {
            "status": "not_signed",
            "subject": None,
            "certificate_sha256": None,
            "thumbprint": None,
            "timestamp_present": False,
        }
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
