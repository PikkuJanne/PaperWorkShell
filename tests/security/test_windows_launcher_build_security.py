"""Archive aliases, output scope and closure injection are rejected offline."""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

from tests.unit.test_build_libtiff_no_jbig import archive_fixture
from tools.build_libtiff_no_jbig import LibtiffBuildError
from tools.build_windows_launcher import (
    LauncherBuildError,
    _archive_files,
    owned_output_directory,
    verify_toolchain,
)


@pytest.mark.parametrize(
    "relative",
    ["src", "output", "output/pws0802", "output/pws0801/launcher", "output/pws0802/../escape"],
)
def test_launcher_output_cannot_reach_unowned_locations(tmp_path: Path, relative: str) -> None:
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_OUTPUT_SCOPE"):
        owned_output_directory(tmp_path / relative, root=tmp_path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_compiler_archive_special_members_are_never_admitted(tmp_path: Path, kind: bytes) -> None:
    entry = tarfile.TarInfo("ucrt64/bin/injected")
    entry.type = kind
    entry.linkname = "../outside"
    archive = archive_fixture(tmp_path, extra=(entry,))
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_ARCHIVE_INVALID"):
        _archive_files(archive)


def test_compiler_archive_traversal_is_rejected_before_native_execution(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path, {"../outside": b"synthetic rejected bytes"})
    with pytest.raises(LibtiffBuildError, match="LIBTIFF_BUILD_UNSAFE_ARCHIVE"):
        _archive_files(archive)


def test_toolchain_alias_cannot_replace_an_authenticated_compiler(tmp_path: Path) -> None:
    root = tmp_path / "ucrt64"
    root.mkdir()
    outside = tmp_path / "synthetic.bin"
    outside.write_bytes(b"synthetic rejected bytes")
    alias = root / "compiler.exe"
    try:
        os.link(outside, alias)
    except OSError:
        pytest.skip("Test volume does not support hard links")
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_INPUT_INVALID"):
        verify_toolchain(root, {"compiler.exe": (outside.stat().st_size, "a" * 64)})
    assert outside.read_bytes() == b"synthetic rejected bytes"
