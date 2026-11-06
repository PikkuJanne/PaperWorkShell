"""Native imports remain explicit without resembling personal contact data."""

from __future__ import annotations

import re
from pathlib import Path

from tools import validate_synthetic_fixtures as validator

SCRIPT = Path(__file__).resolve().parents[2] / "packaging/windows/paperworkshell.iss"


def test_installer_source_passes_ordinary_repository_privacy_inspection() -> None:
    validator._repository_inspector(frozenset())(SCRIPT.read_bytes())


def test_installer_kernel_imports_preserve_exact_native_bindings() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    if '#define PWSKernel32 "kernel32.dll"' in source:
        source = source.replace("{#PWSKernel32}", "kernel32.dll")
    bindings = re.findall(r"external '([^']+)';", source)
    assert sorted(bindings) == sorted(
        name + "@" + "kernel32.dll stdcall"
        for name in (
            "GetFileAttributesW",
            "CreateFileW",
            "GetFileInformationByHandle",
            "CloseHandle",
            "CreateMutexW",
            "GetLastError",
            "ReleaseMutex",
        )
    )
