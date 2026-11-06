from __future__ import annotations

import bz2
import errno
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tools import prepare_dependency_builder as builder
from tools.release_artifacts import CHUNK_BYTES, ReleaseArtifactError


def artifact(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "name": path.name,
        "url": "https://example.invalid/" + path.name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_review(root: Path, index: int, value: dict[str, object]) -> None:
    path = root / builder.REVIEW_PATHS[index]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, **value}), encoding="utf-8")


def make_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[dict[str, Any]]]:
    root, cache = tmp_path / "project", tmp_path / "project/output/cache"
    cache.mkdir(parents=True)
    for relative in builder.RECIPE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# Synthetic recipe fixture; not executed.\n")
    embed = cache / "python-3.14.6-embed-amd64.zip"
    with zipfile.ZipFile(embed, "w") as archive:
        for name in ("python.exe", "python314.dll", "python314.zip", "_ctypes.pyd"):
            archive.writestr(name, b"Synthetic Python input; never executed.")
        archive.writestr("python314._pth", b"import site\nC:/SYNTHETIC_UNTRUSTED_PATH\n")
        archive.writestr("vcruntime140.dll", b"Synthetic omitted canonical CRT")
        archive.writestr("msvcp140_atomic_wait.dll", b"Synthetic omitted canonical CRT")
    write_review(root, 0, {"artifacts": [artifact(embed)]})
    components = []
    for cid in sorted(builder.VALIDATORS | {"typing-extensions"}):
        wheel = cache / (cid.replace("-", "_") + "-1-py3-none-any.whl")
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                cid.replace("-", "_") + "/__init__.py",
                b"raise RuntimeError('Synthetic input must not execute')\n",
            )
            archive.writestr(cid + "-1.dist-info/METADATA", "Synthetic metadata\n")
            archive.writestr(cid + "-1.dist-info/RECORD", "Synthetic stale record\n")
            if cid == "rpds-py":
                archive.writestr("rpds_py/vcruntime140_1.dll", b"Synthetic omitted CRT")
        if cid == "typing-extensions":
            write_review(root, 2, {"components": [{"component_id": cid, "wheel": artifact(wheel)}]})
        else:
            components.append({"component_id": cid, "input_artifact": artifact(wheel)})
    write_review(root, 1, {"components": components})
    tools: list[dict[str, Any]] = []
    for index in range(14):
        name = "gpg.exe" if index == 0 else f"msys-synthetic-{index}.dll"
        raw = ("Synthetic tool " + name).encode()
        imports = (
            ["kernel32.dll", *[f"msys-synthetic-{i}.dll" for i in range(1, 14)]]
            if index == 0
            else ["kernel32.dll"]
        )
        tools.append(
            {
                "name": name,
                "member": "usr/bin/" + name,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "normal_imports": imports,
                "delay_imports": [],
                "local_dependencies": imports[1:],
            }
        )
    git = cache / "Git-synthetic.tar.bz2"
    with tarfile.open(git, "w:bz2") as archive:
        for item in tools:
            raw = ("Synthetic tool " + item["name"]).encode()
            info = tarfile.TarInfo(item["member"])
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    write_review(root, 3, {"artifacts": [artifact(git)], "tool_files": tools})
    by_content = {
        ("Synthetic tool " + item["name"]).encode(): item["normal_imports"] for item in tools
    }
    monkeypatch.setattr(builder, "pe_imports", lambda raw: (tuple(by_content[raw]), ()))
    return root, cache, tools


def test_clean_builder_prepares_only_reviewed_inputs_and_verifies_before_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, tools = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/clean"
    before = {path.name: path.read_bytes() for path in cache.iterdir()}
    receipt = builder.prepare_builder(destination, [cache], root=root)
    assert receipt["status"] == "prepared"
    assert receipt["inputs_before"] == receipt["inputs_after"]
    assert receipt["recipes_before"] == receipt["recipes_after"]
    assert (destination / "builder/python/python314._pth").read_bytes() == builder.ISOLATED_PATH
    assert b"import site" not in builder.ISOLATED_PATH
    assert not list((destination / "builder").rglob("vcruntime140*.dll"))
    assert not list((destination / "builder").rglob("msvcp140*.dll"))
    assert {p.name for p in (destination / "builder/gpg").iterdir()} == {
        item["name"] for item in tools
    }
    assert not list((destination / "builder").rglob("RECORD"))
    assert not list((destination / "builder").rglob("preparation.json"))
    assert not list((destination / "builder").rglob("builder-inventory.json"))
    assert not (destination / "wheels").exists()
    assert not list(destination.parent.glob("pws-builder-*"))
    assert before == {path.name: path.read_bytes() for path in cache.iterdir()}
    assert builder.verify_builder(destination, root=root)["status"] == "verified"
    assert len(json.loads((destination / "builder-inventory.json").read_bytes())["files"]) == 31


def test_existing_output_and_inputs_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/prior"
    destination.mkdir()
    (destination / "prior.json").write_bytes(b"Prior accepted evidence")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_OUTPUT_EXISTS"):
        builder.prepare_builder(destination, [cache], root=root)
    assert (destination / "prior.json").read_bytes() == b"Prior accepted evidence"


@pytest.mark.parametrize("location", ["outside", "output_root", "cache_outside"])
def test_output_and_cache_scope_is_checked_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/new"
    if location == "outside":
        destination = tmp_path / "outside"
    elif location == "output_root":
        destination = root / "output"
    else:
        cache = tmp_path
    with pytest.raises(ReleaseArtifactError, match="SCOPE"):
        builder.prepare_builder(destination, [cache], root=root)
    assert not (root / "output/new").exists()


@pytest.mark.parametrize(
    "change",
    ["missing", "hash", "duplicate_wheel", "missing_validator", "gpg_dependency", "recipe_version"],
)
def test_incomplete_or_changed_pinned_inputs_fail_before_payload_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    if change == "missing":
        (cache / "attrs-1-py3-none-any.whl").unlink()
    elif change == "hash":
        (cache / "attrs-1-py3-none-any.whl").write_bytes(b"changed")
    else:
        index = 3 if change == "gpg_dependency" else 1
        path = root / builder.REVIEW_PATHS[index]
        review = json.loads(path.read_bytes())
        if change == "duplicate_wheel":
            review["components"].append(review["components"][0])
        elif change == "missing_validator":
            review["components"].pop()
        elif change == "gpg_dependency":
            review["tool_files"][0]["local_dependencies"].append("missing.dll")
        else:
            review["schema_version"] = 2
        path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ReleaseArtifactError):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not (root / "output/new").exists()
    assert not list((root / "output").glob("pws-builder-*"))


@pytest.mark.parametrize("budget", ["files", "bytes"])
def test_aggregate_selection_budget_precedes_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        builder, "MAX_BUILDER_FILES" if budget == "files" else "MAX_BUILDER_BYTES", 2
    )
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not list((root / "output").glob("pws-builder-*"))


def test_omitted_wheel_crt_still_counts_toward_temporary_expansion_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    wheel = cache / "rpds_py-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("rpds_py/msvcp140_atomic_wait.dll", b"x" * 100_000)
    path = root / builder.REVIEW_PATHS[1]
    review = json.loads(path.read_bytes())
    next(c for c in review["components"] if c["component_id"] == "rpds-py")["input_artifact"] = (
        artifact(wheel)
    )
    path.write_text(json.dumps(review), encoding="utf-8")
    monkeypatch.setattr(builder, "MAX_BUILDER_BYTES", 50_000)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not list((root / "output").glob("pws-builder-*"))


def test_selected_parent_directories_are_in_the_preflight_entry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    wheel = cache / "attrs-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("deep/" * 45 + "module.py", b"Synthetic")
    path = root / builder.REVIEW_PATHS[1]
    review = json.loads(path.read_bytes())
    next(c for c in review["components"] if c["component_id"] == "attrs")["input_artifact"] = (
        artifact(wheel)
    )
    path.write_text(json.dumps(review), encoding="utf-8")
    monkeypatch.setattr(builder, "MAX_BUILDER_FILES", 40)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not list((root / "output").glob("pws-builder-*"))


@pytest.mark.parametrize("phase", ["wheel", "gpg", "sidecar"])
@pytest.mark.parametrize("failure", ["disk_full", "cancel"])
def test_partial_preparation_is_cleaned_and_prior_evidence_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, failure: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    before = {path.name: path.read_bytes() for path in cache.iterdir()}
    prior = root / "output/prior.json"
    prior.write_bytes(b"Prior accepted evidence")

    def fail() -> None:
        if failure == "cancel":
            raise KeyboardInterrupt
        raise OSError(errno.ENOSPC, "Synthetic disk full")

    if phase == "wheel":

        def interrupted_copy(source: Path, target: Path) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"Owned partial")
            fail()

        monkeypatch.setattr(builder, "_copy", interrupted_copy)
    elif phase == "gpg":

        def interrupted_gpg(
            source: Path, expected: object, destination: Path, records: object
        ) -> None:
            destination.mkdir(parents=True)
            (destination / "gpg.exe").write_bytes(b"Owned partial")
            fail()

        monkeypatch.setattr(builder, "_stage_gpg", interrupted_gpg)
    else:
        original = Path.open

        def interrupted_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if path.name == "preparation.json" and mode == "xb":
                with original(path, mode, *args, **kwargs) as stream:
                    stream.write(b"Owned partial")
                fail()
            return original(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", interrupted_open)
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else OSError):
        builder.prepare_builder(root / "output/new", [cache], root=root)
    assert not (root / "output/new").exists()
    assert not list((root / "output").glob("pws-builder-*"))
    assert prior.read_bytes() == b"Prior accepted evidence"
    assert before == {path.name: path.read_bytes() for path in cache.iterdir()}


@pytest.mark.parametrize("mutation", ["extra", "missing", "changed", "recipe", "input", "manifest"])
def test_after_use_verification_detects_payload_and_provenance_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root, cache, _ = make_inputs(tmp_path, monkeypatch)
    destination = root / "output/new"
    builder.prepare_builder(destination, [cache], root=root)
    target = destination / "builder/python/python.exe"
    if mutation == "extra":
        (target.parent / "unexpected.py").write_bytes(b"Unexpected")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "changed":
        target.write_bytes(b"Changed")
    elif mutation == "recipe":
        (root / builder.RECIPE_PATHS[0]).write_bytes(b"Changed recipe")
    elif mutation == "input":
        (cache / "attrs-1-py3-none-any.whl").write_bytes(b"Changed input")
    else:
        (destination / "builder-inventory.json").write_bytes(b"{}")
    with pytest.raises(ReleaseArtifactError):
        builder.verify_builder(destination, root=root)
    assert destination.is_dir()


def test_tar_reader_rejects_large_metadata_requests_and_expansion_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "synthetic.bz2"
    source.write_bytes(bz2.compress(b"synthetic data"))
    with builder._TarInput(source) as stream:
        with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
            stream.read(CHUNK_BYTES + 1)
    monkeypatch.setattr(builder, "MAX_TAR_BYTES", 4)
    with builder._TarInput(source) as stream:
        with pytest.raises(ReleaseArtifactError, match="RELEASE_BUILDER_LIMIT"):
            stream.read(5)


def test_publish_retries_only_bounded_windows_sharing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary, destination = tmp_path / "temporary", tmp_path / "published"
    temporary.mkdir()
    original = Path.rename
    attempts: list[Path] = []
    delays: list[float] = []

    def rename(path: Path, target: Path) -> Path:
        attempts.append(path)
        if len(attempts) < 3:
            error = PermissionError(errno.EACCES, "Synthetic sharing violation")
            error.winerror = 32
            raise error
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr("time.sleep", delays.append)
    builder._publish(temporary, destination)
    assert destination.is_dir() and len(attempts) == 3 and delays == [0.1, 0.1]


def test_publish_permanent_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*args: Any, **kwargs: Any) -> None:
        raise PermissionError(errno.EACCES, "Synthetic permanent permission failure")

    def sleep(seconds: float) -> None:
        pytest.fail("Permanent error was retried")

    monkeypatch.setattr(Path, "rename", failed)
    monkeypatch.setattr("time.sleep", sleep)
    with pytest.raises(PermissionError):
        builder._publish(tmp_path / "source", tmp_path / "destination")


@pytest.mark.parametrize("status", [5, 32, 33])
def test_unavailable_publication_has_closed_busy_code_and_bounded_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    delays: list[float] = []
    attempts = []

    def failed(*args: Any, **kwargs: Any) -> None:
        attempts.append(1)
        error = PermissionError(errno.EACCES, "Synthetic unavailable output")
        error.winerror = status
        raise error

    monkeypatch.setattr(Path, "rename", failed)
    monkeypatch.setattr("time.sleep", delays.append)
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_BUILDER_OUTPUT_BUSY$"):
        builder._publish(tmp_path / "source", tmp_path / "destination")
    assert len(delays) <= 10 and all(delay == 0.1 for delay in delays)
    assert len(attempts) == (1 if status == 5 else 11)
