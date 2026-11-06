"""Adversarial archive paths stay inside the isolated build, without native execution."""

from __future__ import annotations

import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.unit.test_build_libtiff_no_jbig import archive_fixture
from tools import build_libtiff_no_jbig as native
from tools.build_libtiff_no_jbig import (
    LibtiffBuildError,
    extract_tar,
    extract_zip,
    safe_member_name,
)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/device",
        "a\\b",
        "a/../b",
        "a//b",
        "a/./b",
        "CON",
        "nul.txt",
        "file.",
        "file ",
        "a\x00b",
    ],
)
def test_windows_unsafe_member_names_fail_closed(name: str) -> None:
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_UNSAFE_ARCHIVE$"):
        safe_member_name(name)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_links_and_special_files_are_not_installed(tmp_path: Path, kind: bytes) -> None:
    member = tarfile.TarInfo("ucrt64/bin/untrusted")
    member.type = kind
    member.linkname = "../outside"
    path = archive_fixture(tmp_path, extra=(member,))
    output = tmp_path / "unpacked"
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_UNSAFE_ARCHIVE$"):
        extract_tar(path, output)
    assert not any(output.rglob("*"))


def test_hardlink_cannot_reach_unselected_package_metadata(tmp_path: Path) -> None:
    member = tarfile.TarInfo("ucrt64/bin/untrusted")
    member.type = tarfile.LNKTYPE
    member.linkname = ".PKGINFO"
    path = archive_fixture(tmp_path, {".PKGINFO": b"metadata"}, extra=(member,))
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_UNSAFE_ARCHIVE$"):
        extract_tar(path, tmp_path / "unpacked", prefix="ucrt64/")


def test_case_collision_is_rejected_before_any_member_is_written(tmp_path: Path) -> None:
    path = archive_fixture(tmp_path, {"ucrt64/bin/A.dll": b"a", "ucrt64/bin/a.dll": b"b"})
    output = tmp_path / "unpacked"
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_ARCHIVE_COLLISION$"):
        extract_tar(path, output)
    assert not any(output.rglob("*"))


def test_expansion_limit_is_checked_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = archive_fixture(tmp_path, {"ucrt64/bin/a": b"12345678"})
    monkeypatch.setattr(native, "MAX_EXPANDED_BYTES", 7)
    output = tmp_path / "unpacked"
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_ARCHIVE_LIMIT$"):
        extract_tar(path, output)
    assert not any(output.rglob("*"))


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        member = zipfile.ZipInfo("untrusted")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "../outside")
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_UNSAFE_ARCHIVE$"):
        extract_zip(path, tmp_path / "unpacked")


def test_recipe_drift_fails_before_any_native_program_is_called(tmp_path: Path) -> None:
    path = tmp_path / native.RECIPE_PATH
    path.parent.mkdir()
    path.write_text('{"schema_version":999,"recipe_id":"future"}')
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_HASH_MISMATCH$"):
        native.load_recipe(tmp_path)
