"""Execute the native setup entry point with synthetic inputs that never install."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or platform.machine().upper() != "AMD64",
    reason="Native Windows x64 setup entry point",
)
SOURCE = Path(__file__).resolve().parents[2] / "packaging/windows/Setup-PaperWorkShell.cs"
CANDIDATE = "pws-setup-fixture"
TERMS = "notices/Microsoft-runtime-recipient-terms.txt"
MEMBERS = (
    TERMS,
    "runtime/paperworkshell-installed-v1.json",
    "runtime/PaperWorkShell.exe",
    "runtime/PaperWorkShellDesktop.exe",
    "runtime/python.exe",
    "runtime/engines/qpdf/bin/qpdf.exe",
    "runtime/engines/tesseract/bin/tesseract.exe",
    "runtime/engines/tesseract/share/tessdata/eng.traineddata",
    "runtime/vcruntime140.dll",
    "runtime/vcruntime140_1.dll",
    "runtime/msvcp140.dll",
    "runtime/msvcp140_1.dll",
    "runtime/msvcp140_2.dll",
    "runtime/engines/qpdf/bin/msvcp140.dll",
    "runtime/engines/qpdf/bin/vcruntime140.dll",
    "runtime/engines/qpdf/bin/vcruntime140_1.dll",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def native_setup(tmp_path_factory: pytest.TempPathFactory) -> Path:
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.is_file():
        pytest.skip("Windows framework compiler unavailable; setup is built during preparation")
    executable = tmp_path_factory.mktemp("native-setup") / "Setup-PaperWorkShell.exe"
    subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/warnaserror",
            "/target:exe",
            "/platform:x64",
            "/r:System.Web.Extensions.dll",
            "/r:System.Management.dll",
            f"/r:{framework / 'WPF/UIAutomationClient.dll'}",
            f"/r:{framework / 'WPF/UIAutomationTypes.dll'}",
            f"/out:{executable}",
            str(SOURCE),
        ],
        check=True,
        timeout=30,
    )
    return executable


def _artifact(folder: Path, *, extra_member: str | None = None) -> list[str]:
    installer = folder / "Synthetic Setup.exe"
    installer.write_bytes(b"Synthetic inert Setup fixture")
    members = MEMBERS if extra_member is None else (*MEMBERS, extra_member)
    inventory = folder / "resolved-inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate": {"id": CANDIDATE},
                "target": {"architecture": "x86_64", "os": "Windows"},
                "files": [
                    {"path": member, "size_bytes": 0, "sha256": _digest(b"")} for member in members
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = folder / "installer-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "candidate_id": CANDIDATE,
                "installer": installer.name,
                "installer_sha256": _digest(installer.read_bytes()),
                "installer_size_bytes": installer.stat().st_size,
                "inventory_sha256": _digest(inventory.read_bytes()),
                "microsoft_recipient_terms_sha256": _digest(b""),
                "microsoft_runtime_delivery": "app-local-community2022",
                "payload_expanded_bytes": 0,
                "payload_file_count": len(members),
                "public_distribution_allowed": False,
                "public_trusted_signature": False,
                "schema_version": 1,
                "status": "unsigned-internal-built",
            }
        ),
        encoding="utf-8",
    )
    return [
        "--installer",
        str(installer),
        "--receipt",
        str(receipt),
        "--receipt-sha256",
        _digest(receipt.read_bytes()),
    ]


def _failure(executable: Path, arguments: list[str], expected: str) -> dict[str, object]:
    result = subprocess.run(
        [str(executable), *arguments], capture_output=True, text=True, timeout=30
    )
    data: dict[str, object] = json.loads(result.stdout)
    if data.get("code") == "PWS_SETUP_PLATFORM":
        pytest.skip("This Windows host is outside the supported Windows 11 x64 scope")
    assert result.returncode == 1
    assert data["status"] == "incomplete"
    assert data["code"] == expected
    assert "operation" not in data and "installerExitCode" not in data
    return data


def test_rejects_unknown_switch_before_opening_inputs(native_setup: Path) -> None:
    _failure(native_setup, ["--allusers"], "PWS_SETUP_ARGUMENTS")


@pytest.mark.parametrize(
    "changed", ["installer-receipt.json", "resolved-inventory.json", "Synthetic Setup.exe"]
)
def test_refuses_changed_artifact_chain(native_setup: Path, tmp_path: Path, changed: str) -> None:
    arguments = _artifact(tmp_path)
    candidate_file = tmp_path / changed
    raw = candidate_file.read_bytes()
    candidate_file.write_bytes(raw[:-1] + b"X")
    _failure(native_setup, arguments, "PWS_SETUP_ARTIFACT")


@pytest.mark.parametrize(
    "member", ["runtime/../outside.dll", "runtime/PAPERWORKSHELL.EXE", "runtime/AUX.txt"]
)
def test_refuses_unsafe_inventory_paths(native_setup: Path, tmp_path: Path, member: str) -> None:
    _failure(native_setup, _artifact(tmp_path, extra_member=member), "PWS_SETUP_ARTIFACT")


def test_requires_exact_consent_and_records_failure(native_setup: Path, tmp_path: Path) -> None:
    receipt = tmp_path / "new result.json"
    arguments = _artifact(tmp_path) + [
        "--accept-microsoft-terms",
        "0" * 64,
        "--result",
        str(receipt),
    ]
    result = _failure(native_setup, arguments, "PWS_SETUP_TERMS")
    assert result["candidate"] == CANDIDATE
    assert result == json.loads(receipt.read_bytes())
    assert str(tmp_path) not in receipt.read_text(encoding="utf-8")
    _failure(native_setup, arguments, "PWS_SETUP_RESULT")
    assert result == json.loads(receipt.read_bytes())
