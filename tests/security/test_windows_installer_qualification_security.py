"""Host qualification does not adopt aliases or traverse production case contents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tools import qualify_windows_installer as qualify
from tools.release_artifacts import file_hashes


def test_owned_uninstaller_cannot_be_a_hardlink_to_external_bytes(tmp_path: Path) -> None:
    installation = tmp_path / "synthetic-product"
    installation.mkdir()
    outside = tmp_path / "external-original"
    sentinel = b"synthetic external bytes must remain intact"
    outside.write_bytes(sentinel)
    os.link(outside, installation / "unins000.exe")
    (installation / "unins000.dat").write_bytes(b"synthetic")
    with pytest.raises(qualify.QualificationError, match="INSTALLED_OBJECT"):
        qualify._installed_files(installation, [])
    assert outside.read_bytes() == sentinel


def test_unregistered_extra_executable_refuses_owned_inventory(tmp_path: Path) -> None:
    installation = tmp_path / "synthetic-product"
    installation.mkdir()
    for name in ("unins000.exe", "unins000.dat", "extra.exe"):
        (installation / name).write_bytes(b"synthetic")
    with pytest.raises(qualify.QualificationError, match="INSTALLED_EXTRA"):
        qualify._installed_files(installation, [])


def test_production_count_never_reads_nested_names_or_document_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = tmp_path / "synthetic-cases"
    for index in range(10):
        child = cases / f"case-{index}" / "private-document"
        child.parent.mkdir(parents=True)
        child.write_bytes(b"synthetic prohibited content")
    original_scandir = os.scandir

    def immediate_only(path: object) -> Any:
        assert path == cases
        return original_scandir(cases)

    def forbidden_read(_path: Path) -> bytes:
        pytest.fail("Production case counting must never read document bytes")

    monkeypatch.setattr(os, "scandir", immediate_only)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    assert qualify.case_entry_count(cases) == 10


def test_installed_payload_tamper_fails_before_any_execution(tmp_path: Path) -> None:
    installation = tmp_path / "synthetic-product"
    installation.mkdir()
    payload = installation / "python.exe"
    payload.write_bytes(b"verified original")
    size, digest, _sha1 = file_hashes(payload)
    expected = [{"path": "python.exe", "size_bytes": size, "sha256": digest}]
    for name in ("unins000.exe", "unins000.dat"):
        (installation / name).write_bytes(b"synthetic")
    payload.write_bytes(b"replacement")
    with pytest.raises(qualify.QualificationError, match="INSTALLED_CHANGED"):
        qualify._installed_files(installation, expected)
