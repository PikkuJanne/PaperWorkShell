"""Offline native-build recipe and extraction behavior; no compiler download in tests."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from tools.build_libtiff_no_jbig import (
    LibtiffBuildError,
    build_commands,
    exported_symbols,
    extract_tar,
    load_recipe,
    owned_output_directory,
    verify_artifact,
)


def archive_fixture(
    directory: Path,
    files: dict[str, bytes] | None = None,
    *,
    extra: tuple[tarfile.TarInfo, ...] = (),
) -> Path:
    path = directory / "synthetic-native-input.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in (
            files or {"ucrt64/bin/synthetic.dll": b"synthetic-code-free-data"}
        ).items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        for item in extra:
            archive.addfile(item, io.BytesIO(b"x" * item.size) if item.isfile() else None)
    return path


def test_frozen_recipe_records_exact_build_inputs_and_replacement() -> None:
    recipe = load_recipe()
    assert len(recipe["build_inputs"]) == 19
    assert len(recipe["codec_inputs"]) == 7
    assert recipe["source"]["sha256"] == (
        "672bd7d10aee4606171afb864f3570b83340f6a33e2c186dc0512f7145ffdf6a"
    )
    assert recipe["configuration"]["jbig"] is False
    assert recipe["configuration"]["win32_io"] is True
    assert recipe["configuration"]["source_patches"] == []
    expected = recipe["expected_output"]
    assert len(expected["exports"]) == 206
    assert "libjbig-0.dll" not in expected["normal_imports"]
    assert expected["delay_imports"] == []
    assert expected["license_expression"] == "LicenseRef-PWS-LibTIFF-4.7.2-Notice"
    notice = recipe["license_refs"][0]
    assert notice["identifier"] == expected["license_expression"]
    assert hashlib.sha256(notice["text"].encode()).hexdigest() == notice["sha256"]


def test_build_plan_disables_jbig_and_preserves_required_dynamic_codecs(tmp_path: Path) -> None:
    configure, compile_command, ctest = build_commands(tmp_path)
    for setting in ("jbig=OFF", "win32-io=ON", "BUILD_SHARED_LIBS=ON", "tiff-tests=ON"):
        assert "-D" + setting in configure
    for codec in ("jpeg", "zlib", "libdeflate", "lerc", "lzma", "webp", "zstd"):
        assert "-D" + codec + "=ON" in configure
    assert "-DCMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH=OFF" in configure
    assert "-DCMAKE_FIND_USE_CMAKE_SYSTEM_PATH=OFF" in configure
    assert compile_command[-2:] == ["--parallel", "2"]
    assert ctest[-2:] == ["--parallel", "2"]


def test_hash_verification_rejects_changed_bytes_before_processing(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.archive"
    path.write_bytes(b"safe")
    verify_artifact(path, expected_size=4, expected_sha256=hashlib.sha256(b"safe").hexdigest())
    path.write_bytes(b"evil")
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_HASH_MISMATCH$"):
        verify_artifact(path, expected_size=4, expected_sha256=hashlib.sha256(b"safe").hexdigest())


def test_regular_files_and_hardlinks_become_independent_regular_copies(tmp_path: Path) -> None:
    link = tarfile.TarInfo("ucrt64/bin/alias.dll")
    link.type = tarfile.LNKTYPE
    link.linkname = "ucrt64/bin/synthetic.dll"
    path = archive_fixture(tmp_path, extra=(link,))
    output = tmp_path / "unpacked"
    assert extract_tar(path, output, prefix="ucrt64/") == 2
    original = output / "ucrt64/bin/synthetic.dll"
    alias = output / "ucrt64/bin/alias.dll"
    assert original.read_bytes() == alias.read_bytes() == b"synthetic-code-free-data"
    assert not alias.is_symlink()
    assert not original.samefile(alias)


def test_package_install_scripts_are_never_selected_or_executed(tmp_path: Path) -> None:
    path = archive_fixture(
        tmp_path, {".INSTALL": b"do-not-execute", "ucrt64/include/synthetic.h": b"header"}
    )
    output = tmp_path / "unpacked"
    assert extract_tar(path, output, prefix="ucrt64/") == 1
    assert not (output / ".INSTALL").exists()
    assert (output / "ucrt64/include/synthetic.h").read_bytes() == b"header"


def test_existing_different_file_is_preserved(tmp_path: Path) -> None:
    path = archive_fixture(tmp_path)
    output = tmp_path / "unpacked"
    target = output / "ucrt64/bin/synthetic.dll"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"retain-existing")
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_EXISTING_FILE$"):
        extract_tar(path, output, prefix="ucrt64/")
    assert target.read_bytes() == b"retain-existing"


def test_build_output_stays_in_its_owned_ignored_subdirectory(tmp_path: Path) -> None:
    owned = tmp_path / "output/pws0801/native-rebuild/repeat"
    assert owned_output_directory(owned, root=tmp_path) == owned.resolve()
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_OUTPUT_SCOPE$"):
        owned_output_directory(tmp_path / "src", root=tmp_path)
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_OUTPUT_SCOPE$"):
        owned_output_directory(tmp_path / "output/pws0801/native-rebuild", root=tmp_path)


def test_pinned_binutils_exports_are_parsed_without_address_assumptions() -> None:
    text = """[Ordinal/Name Pointer] Table -- Ordinal Base 1
    [   0] +base[   1]  0000 TIFFOpen
    [   1] +base[   2]  0001 TIFFClose
The Function Table
    """
    assert exported_symbols(text) == ("TIFFClose", "TIFFOpen")
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_EXPORTS_INVALID$"):
        exported_symbols(text.replace("TIFFClose", "TIFFOpen"))
    with pytest.raises(LibtiffBuildError, match="^LIBTIFF_BUILD_EXPORTS_INVALID$"):
        exported_symbols("unrecognized upstream output")
