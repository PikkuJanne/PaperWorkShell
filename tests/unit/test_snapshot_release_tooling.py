"""Synthetic source-only coverage for the release tooling snapshot recipe."""

from __future__ import annotations

import errno
import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tools import snapshot_release_tooling as tooling
from tools.release_artifacts import ReleaseArtifactError


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-project"
    files = {
        ".gitattributes": b"* text=auto\n",
        "pyproject.toml": b"# synthetic project\n",
        "uv.lock": b"# synthetic lock\n",
        "tools/recipe.py": b"# synthetic recipe; never executed\n",
        "schemas/synthetic.json": b'{"synthetic":true}\n',
        "packaging/release-reviews/synthetic.json": b'{"review":"synthetic"}\n',
        "packaging/licenses/synthetic/LICENSE": b"Synthetic legal text.\n\f\nSecond page.\n",
        "tests/fixtures/release-inventory/v1/current.json": b'{"fixture":"synthetic"}\n',
        "tests/fixtures/release-sbom/schema/README.md": b"Synthetic schema provenance.\n",
        "packaging/release-reviews/app-inputs-v1.json": b"excluded circular candidate review",
        "packaging/README.md": b"excluded final report link",
        "docs/final-result.md": b"excluded final report",
        "src/synthetic.py": b"excluded application source",
    }
    for relative, raw in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return root


def test_snapshot_is_deterministic_reopened_and_has_exact_source_manifest(
    source_root: Path,
) -> None:
    first = source_root / "output/first.tar.xz"
    second = source_root / "output/second.tar.xz"
    original = (source_root / "tools/recipe.py").read_bytes()
    receipt = tooling.snapshot(first, root=source_root)
    (source_root / "tools/recipe.py").touch()
    assert tooling.snapshot(second, root=source_root) == receipt
    assert first.read_bytes() == second.read_bytes()
    assert receipt["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert receipt["size_bytes"] == first.stat().st_size
    with tarfile.open(first, "r:xz") as archive:
        members = archive.getmembers()
        assert len(members) == receipt["files"]
        assert all(
            member.isfile() and member.mode == 0o644 and member.mtime == 0 for member in members
        )
        assert all(
            member.uid == member.gid == 0 and member.uname == member.gname == ""
            for member in members
        )
        names = archive.getnames()
        assert names[-1] == "release-tooling/SOURCE-MANIFEST.json"
        manifest_stream = archive.extractfile(names[-1])
        assert manifest_stream is not None
        manifest = json.loads(manifest_stream.read())
        assert manifest["schema_version"] == 1
        assert ["release-tooling/" + item["path"] for item in manifest["files"]] == names[:-1]
        assert [item["path"] for item in manifest["files"]] == sorted(
            item["path"] for item in manifest["files"]
        )
        for item in manifest["files"]:
            stream = archive.extractfile("release-tooling/" + item["path"])
            assert stream is not None
            raw = stream.read()
            assert raw == (source_root / item["path"]).read_bytes()
            assert len(raw) == item["size_bytes"]
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert "release-tooling/tools/recipe.py" in names
        assert "release-tooling/packaging/licenses/synthetic/LICENSE" in names
        assert not any(
            "app-inputs-v1.json" in name or name.endswith("packaging/README.md") for name in names
        )
        assert not any(
            name.startswith(
                ("release-tooling/output/", "release-tooling/docs/", "release-tooling/src/")
            )
            for name in names
        )
    assert (source_root / "tools/recipe.py").read_bytes() == original


def test_existing_snapshot_is_preserved(source_root: Path) -> None:
    output = source_root / "output/prior.tar.xz"
    output.parent.mkdir()
    output.write_bytes(b"prior accepted snapshot")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_SNAPSHOT_DESTINATION$"):
        tooling.snapshot(output, root=source_root)
    assert output.read_bytes() == b"prior accepted snapshot"


@pytest.mark.parametrize("destination", ["outside", "output_root"])
def test_destination_must_be_strictly_inside_repository_output(
    source_root: Path, destination: str
) -> None:
    output = (
        source_root.parent / "outside.tar.xz"
        if destination == "outside"
        else source_root / "output"
    )
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_SNAPSHOT_DESTINATION$"):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()


@pytest.mark.parametrize("bound", ["files", "bytes"])
def test_source_bounds_fail_before_destination_creation(
    source_root: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    monkeypatch.setattr(
        tooling, "MAX_FILES" if bound == "files" else "MAX_BYTES", 3 if bound == "files" else 12
    )
    output = source_root / "output/new.tar.xz"
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_SNAPSHOT_LIMIT$"):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()
    assert not output.parent.exists()


@pytest.mark.parametrize("failure", ["disk_full", "cancel"])
def test_snapshot_failure_cleans_only_owned_partial(
    source_root: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    prior = source_root / "output/prior.tar.xz"
    prior.parent.mkdir()
    prior.write_bytes(b"prior good snapshot")
    output = prior.with_name("new.tar.xz")

    def interrupted(*args: Any, **kwargs: Any) -> None:
        if failure == "cancel":
            raise KeyboardInterrupt
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(tarfile.TarFile, "addfile", interrupted)
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else OSError):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()
    assert prior.read_bytes() == b"prior good snapshot"
    assert list(prior.parent.iterdir()) == [prior]


def test_reopen_verification_failure_removes_new_archive(
    source_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tarfile.TarFile, "getnames", lambda self: ["unexpected member"])
    output = source_root / "output/new.tar.xz"
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_SNAPSHOT_VERIFY$"):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()


def test_missing_required_source_never_creates_destination(source_root: Path) -> None:
    (source_root / "uv.lock").unlink()
    output = source_root / "output/new.tar.xz"
    with pytest.raises(FileNotFoundError):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()


def test_source_symlink_is_rejected_without_following_target(source_root: Path) -> None:
    target = source_root / "synthetic-private.txt"
    target.write_bytes(b"PRIVATE_SYNTHETIC_TOKEN")
    link = source_root / "tools/linked.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit creating a synthetic symlink")
    output = source_root / "output/new.tar.xz"
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_REPARSE_POINT$"):
        tooling.snapshot(output, root=source_root)
    assert not output.exists()
    assert target.read_bytes() == b"PRIVATE_SYNTHETIC_TOKEN"
