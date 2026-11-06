"""Qualification's deliberate removal authority never follows aliases or unknown bytes."""

from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack
from pathlib import Path

import pytest

from tools import qualify_windows_installer as qualifier


@pytest.mark.skipif(os.name != "nt", reason="Windows authenticated file handles")
def test_interruption_file_pin_refuses_an_external_hardlink_without_mutation(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-original.txt"
    external.write_bytes(b"external synthetic original must remain")
    installed = tmp_path / "owned-product/notice.txt"
    installed.parent.mkdir()
    os.link(external, installed)
    with ExitStack() as pins:
        with pytest.raises(qualifier.QualificationError, match="FILE_ALIAS"):
            qualifier._pin_file(pins, installed, delete=True)
    assert external.read_bytes() == b"external synthetic original must remain"
    assert installed.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate-stream authentication")
def test_interruption_file_pin_refuses_alternate_stream_without_deleting_primary(
    tmp_path: Path,
) -> None:
    notice = tmp_path / "owned-notice.txt"
    notice.write_bytes(b"primary synthetic bytes")
    Path(str(notice) + ":unexpected").write_bytes(b"unexpected synthetic stream")
    with ExitStack() as pins:
        with pytest.raises(ValueError):
            qualifier._pin_file(pins, notice, delete=True)
    assert notice.read_bytes() == b"primary synthetic bytes"


def test_partial_stage_bytes_are_retained_and_never_deleted_by_recovery(tmp_path: Path) -> None:
    installation = tmp_path / "owned-product"
    installation.mkdir()
    stage = installation / "is-ABCDEFGH01.tmp"
    stage.write_bytes(b"partial")
    state = {
        "candidate": {"files": [{"path": qualifier._INTERRUPTION_BARRIER}]},
        "installed_files": {
            "unins000.exe": {"size_bytes": 8, "sha256": hashlib.sha256(b"complete").hexdigest()}
        },
    }
    if os.name != "nt":
        pytest.skip("Windows exact opened-file recovery")
    with pytest.raises(qualifier.QualificationError, match="INSTALLED_CHANGED"):
        qualifier._recover_owned_repair(installation, state, tmp_path / "recovery", root=tmp_path)
    assert stage.read_bytes() == b"partial"
    assert not (tmp_path / "recovery/retained-stage-1.bin").exists()
