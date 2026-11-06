from __future__ import annotations

import bz2
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_prepare_dependency_builder import artifact, make_inputs, write_review
from tools import prepare_dependency_builder as builder
from tools.release_artifacts import CHUNK_BYTES, ReleaseArtifactError


def test_bootstrap_imports_without_any_third_party_packages(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    code = "\n".join(
        [
            "import importlib.abc, sys",
            f"sys.path.insert(0, {str(root)!r})",
            "class Block(importlib.abc.MetaPathFinder):",
            " def find_spec(self, fullname, path=None, target=None):",
            "  if fullname.split('.')[0] in {'jsonschema','referencing','rpds','attrs','PySide6','pikepdf','pytest','yaml'}: raise AssertionError('third-party bootstrap import')",
            "sys.meta_path.insert(0, Block())",
            "import tools.prepare_dependency_builder",
            "assert 'tools.release_evidence' not in sys.modules",
            "assert 'tools.build_dependency_stage' not in sys.modules",
            "print('stdlib-only bootstrap import passed')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "stdlib-only bootstrap import passed\n"


def test_preparation_has_no_network_or_input_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Offline bootstrap attempted network or execution")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    destination = root / "output/new"
    report = builder.prepare_builder(destination, [cache], root=root)
    assert (
        builder.verify_builder(
            destination, expected_preparation_sha256=str(report["preparation_sha256"]), root=root
        )["status"]
        == "verified"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "selected_symlink",
        "selected_hardlink",
        "unselected_traversal",
        "duplicate",
        "missing",
        "digest",
        "oversized_pax",
        "expanded_limit",
    ],
)
def test_gpg_tar_rejects_unsafe_or_unbound_selected_content_without_surviving_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, cache, tools = make_inputs(tmp_path, monkeypatch)
    source = cache / "Git-synthetic.tar.bz2"
    if kind in {"oversized_pax", "expanded_limit"}:
        member = tarfile.TarInfo("synthetic")
        member.size = CHUNK_BYTES * 2 if kind == "oversized_pax" else builder.MAX_TAR_BYTES + 1
        member.type = tarfile.XHDTYPE if kind == "oversized_pax" else tarfile.REGTYPE
        source.write_bytes(bz2.compress(member.tobuf() + b"\0" * 1024))
    else:
        with tarfile.open(source, "w:bz2") as archive:
            for index, item in enumerate(tools):
                if kind == "missing" and index == 13:
                    continue
                raw = ("Synthetic tool " + item["name"]).encode()
                info = tarfile.TarInfo(item["member"])
                info.size = len(raw)
                if index == 0 and kind in {"selected_symlink", "selected_hardlink"}:
                    info.type = tarfile.SYMTYPE if kind == "selected_symlink" else tarfile.LNKTYPE
                    info.linkname = "../../outside-private-target"
                    info.size = 0
                    archive.addfile(info)
                else:
                    archive.addfile(info, io.BytesIO(raw))
                if kind == "duplicate" and index == 0:
                    archive.addfile(info, io.BytesIO(raw))
            if kind == "unselected_traversal":
                info = tarfile.TarInfo("../outside-private-target")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
        if kind == "digest":
            tools[0]["sha256"] = "0" * 64
    write_review(root, 3, {"artifacts": [artifact(source)], "tool_files": tools})
    with pytest.raises(ReleaseArtifactError):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not (root / "output/new").exists()
    assert not list((root / "output").glob("pws-builder-*"))
    assert not (root / "output/outside-private-target").exists()


def test_unselected_tar_link_is_never_followed_or_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, tools = make_inputs(tmp_path, monkeypatch)
    source = cache / "Git-synthetic.tar.bz2"
    with tarfile.open(source, "w:bz2") as archive:
        for item in tools:
            raw = ("Synthetic tool " + item["name"]).encode()
            info = tarfile.TarInfo(item["member"])
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        info = tarfile.TarInfo("usr/bin/unselected-link")
        info.type, info.linkname = tarfile.SYMTYPE, "../../private-target"
        archive.addfile(info)
    write_review(root, 3, {"artifacts": [artifact(source)], "tool_files": tools})
    destination = root / "output/new"
    builder.prepare_builder(destination, [cache], root=root)
    assert not (destination / "builder/gpg/unselected-link").exists()


@pytest.mark.parametrize("variant", ["gnu", "pax00", "pax01", "pax10"])
def test_sparse_metadata_is_rejected_before_stdlib_index_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    root, cache, tools = make_inputs(tmp_path, monkeypatch)
    source = cache / "Git-synthetic.tar.bz2"
    info = tarfile.TarInfo("usr/bin/gpg.exe")
    raw = b"Synthetic tool gpg.exe"
    info.size = len(raw)
    if variant == "gnu":
        info.type = tarfile.GNUTYPE_SPARSE
    else:
        info.pax_headers = {
            "pax00": {"GNU.sparse.size": "1"},
            "pax01": {"GNU.sparse.map": "0,1"},
            "pax10": {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
        }[variant]
    with tarfile.open(
        source, "w:bz2", format=tarfile.GNU_FORMAT if variant == "gnu" else tarfile.PAX_FORMAT
    ) as archive:
        archive.addfile(info, io.BytesIO(raw))
    write_review(root, 3, {"artifacts": [artifact(source)], "tool_files": tools})

    def unsafe_handler(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Sparse index was delegated to an accumulating stdlib handler")

    for name in ("_proc_sparse", "_proc_gnusparse_00", "_proc_gnusparse_01", "_proc_gnusparse_10"):
        monkeypatch.setattr(tarfile.TarInfo, name, unsafe_handler)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_TAR_SPARSE"):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not list((root / "output").glob("pws-builder-*"))


@pytest.mark.parametrize(
    "variant", ["pax_depth", "gnu_depth", "metadata_bytes", "metadata_headers"]
)
def test_recursive_tar_metadata_limits_apply_before_any_member_is_yielded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    root, cache, tools = make_inputs(tmp_path, monkeypatch)
    source = cache / "Git-synthetic.tar.bz2"
    block = tarfile.TarInfo("metadata")
    block.type = tarfile.GNUTYPE_LONGNAME if variant == "gnu_depth" else tarfile.XHDTYPE
    raw = b"usr/bin/gpg.exe\0" if variant == "gnu_depth" else b"13 comment=x\n"
    block.size = len(raw)
    header = block.tobuf(format=tarfile.GNU_FORMAT) + raw + b"\0" * ((-len(raw)) % 512)
    source.write_bytes(bz2.compress(header * 40 + b"\0" * 1024))
    if variant == "metadata_bytes":
        monkeypatch.setattr(builder, "MAX_TAR_METADATA_BYTES", 40)
    elif variant == "metadata_headers":
        monkeypatch.setattr(builder, "MAX_TAR_MEMBERS", 2)
    write_review(root, 3, {"artifacts": [artifact(source)], "tool_files": tools})
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not list((root / "output").glob("pws-builder-*"))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"x":NaN}',
        b'{"schema_version":1,"x":Infinity}',
        b'{"schema_version":1,"x":1.5}',
        b'{"schema_version":1,"schema_version":1}',
    ],
)
def test_review_json_rejects_duplicate_and_untyped_numbers(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "review.json"
    path.write_bytes(raw)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_JSON"):
        builder._load(path)


@pytest.mark.parametrize(
    "url",
    [
        "https://private:SECRET@example.invalid/input",
        "file:///C:/Users/private/document",
        "https://example.invalid/C:/Users/private/document",
        "https://example.invalid/input?token=SECRET",
    ],
)
def test_receipt_cannot_publish_secret_bearing_or_private_input_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    path = root / builder.REVIEW_PATHS[1]
    review = json.loads(path.read_bytes())
    review["components"][0]["input_artifact"]["url"] = url
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_INPUT"):
        builder.prepare_builder(root / "output/new", [cache], root=root)


def test_editable_receipt_cannot_drop_a_reviewed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/new"
    builder.prepare_builder(destination, [cache], root=root)
    path = destination / "preparation.json"
    receipt = json.loads(path.read_bytes())
    receipt["inputs_before"] = receipt["inputs_after"] = []
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_INPUT"):
        builder.verify_builder(destination, root=root)


def test_caller_held_receipt_hash_detects_rewritten_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/new"
    receipt = builder.prepare_builder(destination, [cache], root=root)
    path = destination / "preparation.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_MANIFEST_CHANGED"):
        builder.verify_builder(
            destination, expected_preparation_sha256=str(receipt["preparation_sha256"]), root=root
        )


def test_cli_does_not_expose_filesystem_or_parse_exception_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise OSError("C:/Users/private/PRIVATE_APPLICANT_DOCUMENT_TOKEN.pdf")

    monkeypatch.setattr(builder, "prepare_builder", forbidden)
    assert (
        builder.main(["prepare", "--output", "output/synthetic", "--artifacts", "output/cache"])
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR [RELEASE_BUILDER_INPUT] Dependency builder could not be read safely.\n"
    )
