"""Hostile archive metadata must never be extracted, executed or disclosed."""

from __future__ import annotations

import json
import os
import stat
import tarfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.unit.test_audit_tesseract_redistribution import synthetic_archive, synthetic_pe
from tools import audit_tesseract_redistribution as audit_module
from tools.audit_tesseract_redistribution import (
    PREFIX,
    RedistributionAuditError,
    inspect_archive,
    main,
    pe_imports,
)


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "C:/device", "a\\b", "a/../b", "a//b", "a/./b"]
)
def test_traversal_and_windows_aliases_fail_before_extraction(tmp_path: Path, name: str) -> None:
    member = tarfile.TarInfo(name)
    path, spec = synthetic_archive(tmp_path, extra=(member,))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_UNSAFE_MEMBER$"):
        inspect_archive(path, spec)
    assert sorted(file.name for file in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE]
)
def test_unreviewed_links_and_special_members_are_rejected(tmp_path: Path, kind: bytes) -> None:
    member = tarfile.TarInfo("ucrt64/bin/unreviewed")
    member.type = kind
    member.linkname = "../private-value"
    path, spec = synthetic_archive(tmp_path, extra=(member,))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_UNSAFE_MEMBER$"):
        inspect_archive(path, spec)


def test_exact_unselected_libdeflate_utility_hardlink_is_never_followed(tmp_path: Path) -> None:
    member = tarfile.TarInfo("ucrt64/bin/libdeflate-gzip.exe")
    member.type = tarfile.LNKTYPE
    member.linkname = "ucrt64/bin/libdeflate-gunzip.exe"
    path, spec = synthetic_archive(tmp_path, package=PREFIX + "libdeflate", extra=(member,))
    assert inspect_archive(path, spec) == {}
    assert not (tmp_path / "ucrt64").exists()


def test_libdeflate_exception_never_permits_selected_hardlink(tmp_path: Path) -> None:
    member = tarfile.TarInfo("ucrt64/bin/libdeflate-gzip.exe")
    member.type = tarfile.LNKTYPE
    member.linkname = "ucrt64/bin/libdeflate-gunzip.exe"
    path, spec = synthetic_archive(tmp_path, package=PREFIX + "libdeflate", extra=(member,))
    selected = replace(spec.files[0], path="bin/libdeflate-gzip.exe")
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_UNSAFE_MEMBER$"):
        inspect_archive(path, replace(spec, files=(selected,)))


def test_duplicate_metadata_is_rejected(tmp_path: Path) -> None:
    member = tarfile.TarInfo(".PKGINFO")
    path, spec = synthetic_archive(tmp_path, extra=(member,))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_DUPLICATE_MEMBER$"):
        inspect_archive(path, spec)


def test_member_expansion_budget_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, spec = synthetic_archive(tmp_path)
    monkeypatch.setattr(audit_module, "MAX_EXPANDED_BYTES", 1)
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_LIMIT$"):
        inspect_archive(path, spec)


def test_archive_and_metadata_count_budgets_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, spec = synthetic_archive(tmp_path)
    monkeypatch.setattr(audit_module, "MAX_MEMBERS", 1)
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_LIMIT$"):
        inspect_archive(path, spec)


def test_no_subprocess_network_or_archive_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import subprocess

    def prohibited(*args: object, **kwargs: object) -> None:
        pytest.fail("prohibited operation")

    monkeypatch.setattr(subprocess, "Popen", prohibited)
    monkeypatch.setattr(socket, "socket", prohibited)
    monkeypatch.setattr(tarfile.TarFile, "extract", prohibited)
    monkeypatch.setattr(tarfile.TarFile, "extractall", prohibited)
    path, spec = synthetic_archive(tmp_path)
    assert inspect_archive(path, spec) == {}


def test_invalid_private_metadata_and_paths_are_not_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, spec = synthetic_archive(tmp_path, metadata_labels=("PRIVATE_DOCUMENT_TOKEN",))
    monkeypatch.setattr(audit_module, "load_specs", lambda root: (spec,))
    assert main(["--artifacts", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "OCR_AUDIT_METADATA"
    assert "PRIVATE_DOCUMENT_TOKEN" not in output
    assert str(tmp_path) not in output


def test_pe_import_name_cannot_be_a_path() -> None:
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_PE$"):
        pe_imports(synthetic_pe(("../private.dll",)))


def test_metadata_body_size_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, spec = synthetic_archive(tmp_path)
    monkeypatch.setattr(audit_module, "MAX_METADATA_BYTES", 1)
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_LIMIT$"):
        inspect_archive(path, spec)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_filesystem_redirection_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, spec = synthetic_archive(tmp_path)
    real_lstat = Path.lstat

    def lstat(candidate: Path, *args: object, **kwargs: object) -> os.stat_result:
        if candidate == path:
            return cast(
                os.stat_result,
                SimpleNamespace(
                    st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFREG,
                    st_file_attributes=0x400 if kind == "reparse" else 0,
                ),
            )
        return real_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_UNSAFE_PATH$"):
        inspect_archive(path, spec)
