"""Synthetic source/closure/PE checks for the CRT-free launcher build."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import cast

import pytest

from tests.unit.test_audit_tesseract_redistribution import synthetic_pe
from tests.unit.test_build_libtiff_no_jbig import archive_fixture
from tools.build_windows_launcher import (
    ALLOWED_IMPORTS,
    LauncherBuildError,
    _archive_files,
    build_commands,
    inspect_launcher,
    owned_output_directory,
    verify_toolchain,
)


def launcher_image(imports: tuple[str, ...] = tuple(sorted(ALLOWED_IMPORTS))) -> bytes:
    data = bytearray(synthetic_pe(imports))
    struct.pack_into("<I", data, 152 + 16, 4096)
    struct.pack_into("<HH", data, 152 + 68, 2, 0x160)
    struct.pack_into("<HH", data, 152 + 40, 10, 0)
    struct.pack_into("<HH", data, 152 + 48, 10, 0)
    struct.pack_into("<II", data, 152 + 152, 4096 + 3000, 12)
    struct.pack_into("<IIHH", data, 512 + 3000, 4096, 12, 0xA000, 0)
    struct.pack_into("<II", data, 152 + 192, 4096 + 3040, 152)
    struct.pack_into("<I", data, 512 + 3040, 152)
    struct.pack_into("<Q", data, 512 + 3040 + 88, 0x140000000 + 4096 + 3240)
    struct.pack_into("<I", data, 512 + 3040 + 144, 0x800)
    struct.pack_into("<Q", data, 512 + 3240, 0x00002B992DDFA232)
    return bytes(data)


def test_crt_free_plan_has_explicit_entry_and_only_windows_import_libraries(tmp_path: Path) -> None:
    resource, compiler = build_commands(tmp_path / "output", tmp_path / "ucrt64", root=tmp_path)
    assert resource[0].endswith("windres.exe")
    assert "--preprocessor-arg=-E" in resource and "--preprocessor-arg=-xc" in resource
    assert "-nostdlib" in compiler and "-ffreestanding" in compiler
    assert any("--entry,pws_entry" in value for value in compiler)
    assert [value for value in compiler if value.startswith("-l")] == [
        "-lkernel32",
        "-luser32",
        "-lshell32",
        "-lole32",
    ]
    assert all("-static" not in value and "-lgcc" not in value for value in compiler)


def test_output_is_new_owned_repository_child(tmp_path: Path) -> None:
    output = tmp_path / "output/pws0802/fresh-launcher"
    assert owned_output_directory(output, root=tmp_path) == output
    output.mkdir(parents=True)
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_OUTPUT_EXISTS"):
        owned_output_directory(output, root=tmp_path)


def test_archive_proof_matches_content_without_extracting_code(tmp_path: Path) -> None:
    path = archive_fixture(tmp_path, {"ucrt64/bin/gcc.exe": b"synthetic compiler data"})
    assert _archive_files(path) == {
        "bin/gcc.exe": (23, hashlib.sha256(b"synthetic compiler data").hexdigest())
    }


def test_exact_toolchain_closure_rejects_changed_missing_and_extra_files(tmp_path: Path) -> None:
    root = tmp_path / "ucrt64"
    (root / "bin").mkdir(parents=True)
    compiler = root / "bin/gcc.exe"
    compiler.write_bytes(b"synthetic compiler data")
    expected = {
        "bin/gcc.exe": (compiler.stat().st_size, hashlib.sha256(compiler.read_bytes()).hexdigest())
    }
    assert len(verify_toolchain(root, expected)) == 64
    compiler.write_bytes(b"changed compiler bytes")
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_TOOLCHAIN_MISMATCH"):
        verify_toolchain(root, expected)
    compiler.unlink()
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_TOOLCHAIN_MISMATCH"):
        verify_toolchain(root, expected)
    compiler.write_bytes(b"synthetic compiler data")
    (root / "bin/unreviewed.dll").write_bytes(b"synthetic injected data")
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_TOOLCHAIN_MISMATCH"):
        verify_toolchain(root, expected)


def test_pe_policy_accepts_gui_aslr_nx_without_crt() -> None:
    proof = inspect_launcher(launcher_image())
    assert set(cast(list[str], proof["normal_imports"])) == ALLOWED_IMPORTS
    assert proof["crt_runtime"] == "none" and proof["delay_imports"] == []


def test_loader_policy_rejects_inherited_future_minor_subsystem_version() -> None:
    data = bytearray(launcher_image())
    struct.pack_into("<HH", data, 152 + 48, 10, 2)
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_PE_POLICY"):
        inspect_launcher(bytes(data))


def test_aslr_requires_a_real_relocation_directory() -> None:
    data = bytearray(launcher_image())
    struct.pack_into("<II", data, 152 + 152, 0, 0)
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_PE_POLICY"):
        inspect_launcher(bytes(data))


def test_modern_loader_policy_requires_honest_unused_cookie_configuration() -> None:
    data = bytearray(launcher_image())
    struct.pack_into("<II", data, 152 + 192, 0, 0)
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_PE_POLICY"):
        inspect_launcher(bytes(data))


@pytest.mark.parametrize(
    "dependency", ["ucrtbase.dll", "libgcc_s_seh-1.dll", "msvcrt.dll", "unreviewed.dll"]
)
def test_pe_policy_rejects_extra_runtime_dependencies(dependency: str) -> None:
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_PE_IMPORTS"):
        inspect_launcher(launcher_image(tuple(sorted(ALLOWED_IMPORTS)) + (dependency,)))


@pytest.mark.parametrize("subsystem,flags", [(3, 0x160), (2, 0x140), (2, 0x60)])
def test_pe_policy_rejects_console_or_missing_hardening(subsystem: int, flags: int) -> None:
    data = bytearray(launcher_image())
    struct.pack_into("<HH", data, 152 + 68, subsystem, flags)
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_PE_POLICY"):
        inspect_launcher(bytes(data))
