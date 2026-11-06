"""Deterministic source derivation uses synthetic archives, never licensed fixtures."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools import stage_lxml_source as stage


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_inputs(
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra: tarfile.TarInfo | None = None,
    bad_source_resource: bool = False,
    approval_version: int = 1,
) -> tuple[Path, Path, Path]:
    source = directory / "source.tar.gz"
    wheel = directory / "wheel.whl"
    approval = directory / "approval.json"
    output = directory / "derived.tar.xz"
    path = "lxml/isoschematron/resources/xsl/Synthetic.xsl"
    resource = b"synthetic resource\nsecond line\n"
    wheel_resource = resource.replace(b"\n", b"\r\n")
    members = {
        "lxml-6.1.1/setup.py": b"# synthetic source; never executed\n",
        "lxml-6.1.1/tools/build.sh": b"# synthetic executable-intent source\n",
        stage.SOURCE_PREFIX + path: b"changed resource" if bad_source_resource else resource,
    }
    with tarfile.open(source, "w:gz") as archive:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mtime = 123456
            member.uid = 1001
            member.uname = "synthetic-upstream-owner"
            member.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(member, io.BytesIO(data))
        if extra is not None:
            archive.addfile(extra)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(path, wheel_resource)
    approval.write_text(
        json.dumps(
            {
                "schema_version": approval_version,
                "exclusions": [
                    {
                        "path": path,
                        "size_bytes": len(wheel_resource),
                        "sha256": _hash(wheel_resource),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(stage, "APPROVAL", approval)
    monkeypatch.setattr(stage, "APPROVAL_SHA256", _hash(approval.read_bytes()))
    monkeypatch.setattr(stage, "SOURCE_SHA256", _hash(source.read_bytes()))
    monkeypatch.setattr(stage, "SOURCE_SIZE", source.stat().st_size)
    monkeypatch.setattr(stage, "WHEEL_SHA256", _hash(wheel.read_bytes()))
    monkeypatch.setattr(stage, "WHEEL_SIZE", wheel.stat().st_size)
    monkeypatch.setattr(
        stage,
        "EXCLUSIONS",
        {path: (len(wheel_resource), _hash(wheel_resource), len(resource), _hash(resource))},
    )
    return source, wheel, output


def test_only_bound_resource_is_removed_and_remaining_bytes_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)
    original = (source.read_bytes(), wheel.read_bytes())
    receipt = stage.derive(source, wheel, output)
    assert receipt["status"] == "derived-source-subset-verified"
    assert receipt["redistribution_rights_granted"] is False
    assert receipt["exclusions"] == [
        {
            "source_path": stage.SOURCE_PREFIX + "lxml/isoschematron/resources/xsl/Synthetic.xsl",
            "source_size_bytes": 31,
            "source_sha256": _hash(b"synthetic resource\nsecond line\n"),
            "approved_wheel_path": "lxml/isoschematron/resources/xsl/Synthetic.xsl",
            "approved_wheel_size_bytes": 33,
            "approved_wheel_sha256": _hash(b"synthetic resource\r\nsecond line\r\n"),
            "raw_bytes_equal": False,
            "lf_normalized_bytes_equal": True,
        }
    ]
    with tarfile.open(output, "r:xz") as archive:
        assert sorted(archive.getnames()) == ["lxml-6.1.1/setup.py", "lxml-6.1.1/tools/build.sh"]
        for member in archive.getmembers():
            assert member.uid == member.gid == member.mtime == 0
            assert member.uname == member.gname == ""
        assert archive.getmember("lxml-6.1.1/tools/build.sh").mode == 0o755
        result = archive.extractfile("lxml-6.1.1/setup.py")
        assert result is not None and result.read() == b"# synthetic source; never executed\n"
    assert original == (source.read_bytes(), wheel.read_bytes())


def test_repeated_derivation_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)
    first = stage.derive(source, wheel, output)
    other = tmp_path / "another-name.tar.xz"
    second = stage.derive(source, wheel, other)
    assert output.read_bytes() == other.read_bytes()
    assert first["output_sha256"] == second["output_sha256"]


@pytest.mark.parametrize("selected", ["source", "wheel"])
def test_changed_input_fails_before_output_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)
    (source if selected == "source" else wheel).write_bytes(b"changed")
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_INPUT_HASH$"):
        stage.derive(source, wheel, output)
    assert not output.exists()


def test_known_source_archive_cannot_hide_changed_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch, bad_source_resource=True)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_EXCLUSION$"):
        stage.derive(source, wheel, output)
    assert not output.exists()


def test_future_approval_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch, approval_version=999)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_APPROVAL$"):
        stage.derive(source, wheel, output)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)
    output.write_bytes(b"previous verified source bundle")
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_WRITE$"):
        stage.derive(source, wheel, output)
    assert output.read_bytes() == b"previous verified source bundle"


def test_write_failure_removes_only_new_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, wheel, output = synthetic_inputs(tmp_path, monkeypatch)

    def disk_full(_descriptor: int) -> None:
        raise OSError("private-path-value")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(stage.LxmlSourceError, match="^LXML_SOURCE_WRITE$"):
        stage.derive(source, wheel, output)
    assert not output.exists()
    assert source.exists() and wheel.exists()
