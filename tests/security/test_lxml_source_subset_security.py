"""Source packaging must not interpret members or expose rejected contents."""

from __future__ import annotations

import json
import socket
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.unit.test_stage_lxml_source import synthetic_inputs
from tools import stage_lxml_source as stage


@pytest.mark.parametrize(
    "name",
    ["../private", "/absolute", "lxml-6.1.1/../escape", "lxml-6.1.1/a\\b", "lxml-6.1.1/C:device"],
)
def test_unsafe_members_are_rejected_without_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    extra = tarfile.TarInfo(name)
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch, extra=extra)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_MEMBER$"):
        stage.derive(source, wheel, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_links_and_special_files_are_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: bytes
) -> None:
    member = tarfile.TarInfo("lxml-6.1.1/unreviewed")
    member.type = kind
    member.linkname = "../private"
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch, extra=member)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_MEMBER$"):
        stage.derive(source, wheel, output)


def test_no_commands_network_or_archive_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)

    def prohibited(*args: object, **kwargs: object) -> None:
        pytest.fail("prohibited operation")

    monkeypatch.setattr(socket, "socket", prohibited)
    monkeypatch.setattr(subprocess, "Popen", prohibited)
    monkeypatch.setattr(tarfile.TarFile, "extract", prohibited)
    monkeypatch.setattr(tarfile.TarFile, "extractall", prohibited)
    stage.derive(source, wheel, output)


def test_source_expansion_budget_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(stage, "MAX_EXPANDED_BYTES", 1)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_LIMIT$"):
        stage.derive(source, wheel, output)


def test_cli_error_does_not_disclose_path_or_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    value = tmp_path / "PRIVATE_PATH_TOKEN"
    code = stage.main(["--source", str(value), "--wheel", str(value), "--output", str(value)])
    assert code == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "rejected"
    assert "PRIVATE_PATH_TOKEN" not in output and str(tmp_path) not in output
