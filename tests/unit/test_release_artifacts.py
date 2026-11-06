from __future__ import annotations

import hashlib
import io
import stat
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tools import release_artifacts as artifacts
from tools import release_staging as staging
from tools.release_artifacts import Artifact, ReleaseArtifactError
from tools.release_staging import inventory_files, write_package


def _identity(path: Path, raw: bytes) -> Artifact:
    return Artifact(
        path.name, "https://example.invalid/input.zip", len(raw), hashlib.sha256(raw).hexdigest()
    )


def _zip(
    tmp_path: Path, members: list[tuple[str | zipfile.ZipInfo, bytes]]
) -> tuple[Path, Artifact]:
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in members:
            archive.writestr(name, raw)
    return path, _identity(path, path.read_bytes())


class _Response(io.BytesIO):
    url = "https://example.invalid/input.zip"


@pytest.mark.parametrize(
    "name",
    ["../x", "/x", "a\\b", "a//b", "a/./b", "NUL.txt", "aux/x", "a:stream", "x.", "x ", "x/../y"],
)
def test_paths_are_portable_windows_relative_names(name: str) -> None:
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_PATH$"):
        artifacts.safe_relative(name)


def test_hash_verification_is_exact_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.zip"
    path.write_bytes(b"synthetic")
    artifacts.verify_artifact(path, _identity(path, b"synthetic"))
    with pytest.raises(ReleaseArtifactError, match="RELEASE_ARTIFACT_HASH"):
        artifacts.verify_artifact(path, _identity(path, b"different"))
    with pytest.raises(ReleaseArtifactError, match="RELEASE_LIMIT"):
        artifacts.file_hashes(path, maximum=3)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/x",
        "file:///secret",
        "https://u:p@example.invalid/x",
        "https://example.invalid/x#f",
    ],
)
def test_unsafe_sources_fail_before_any_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unsafe artifact started network retrieval")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_SOURCE"):
        artifacts.acquire(Artifact("input.zip", url, 1, "a" * 64), tmp_path)


def test_acquire_authenticates_and_reuses_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b"synthetic authenticated bytes"
    expected = _identity(Path("input.zip"), raw)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response(raw))
    result = artifacts.acquire(expected, tmp_path)
    assert result.read_bytes() == raw

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("existing input was retrieved again")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    assert artifacts.acquire(expected, tmp_path) == result
    result.write_bytes(b"wrong retained input")
    with pytest.raises(ReleaseArtifactError):
        artifacts.acquire(expected, tmp_path)
    assert result.read_bytes() == b"wrong retained input"


@pytest.mark.parametrize("failure", ["hash", "size", "redirect", "time", "cancel"])
def test_failed_acquire_removes_only_owned_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    raw = b"synthetic input"
    expected = _identity(Path("input.zip"), raw)

    class Response(_Response):
        def read(self, size: int | None = -1) -> bytes:
            if failure == "cancel":
                raise KeyboardInterrupt
            return super().read(size)

    response = Response(
        b"wrong bytes!!!"
        if failure == "hash"
        else raw + (b"overflow" if failure == "size" else b"")
    )
    if failure == "redirect":
        response.url = "http://example.invalid/downgraded"
    if failure == "time":
        ticks = iter([0.0, artifacts.MAX_ARTIFACT_SECONDS + 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: response)
    prior = tmp_path / "prior.zip"
    prior.write_bytes(b"prior verified bytes")
    with pytest.raises(KeyboardInterrupt if failure == "cancel" else ReleaseArtifactError):
        artifacts.acquire(expected, tmp_path)
    assert not (tmp_path / expected.name).exists()
    assert prior.read_bytes() == b"prior verified bytes"


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "a\\b", "CON.txt"])
def test_even_unselected_unsafe_zip_members_fail_before_writes(tmp_path: Path, unsafe: str) -> None:
    # ZipInfo's constructor normalizes native separators on Windows. Retain the
    # hostile archive header spelling, rather than testing its normalized form.
    member = zipfile.ZipInfo("placeholder")
    member.filename = unsafe
    path, expected = _zip(tmp_path, [("good.txt", b"safe"), (member, b"unsafe")])
    destination = tmp_path / "stage"
    with pytest.raises(ReleaseArtifactError, match="RELEASE_PATH"):
        artifacts.extract_selected_zip(
            path, expected, destination, {"good.txt": "runtime/good.txt"}
        )
    assert not destination.exists()


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_zip_special_members_are_rejected_even_unselected(tmp_path: Path, mode: int) -> None:
    member = zipfile.ZipInfo("special")
    member.create_system = 3
    member.external_attr = (mode | 0o644) << 16
    path, expected = _zip(tmp_path, [("good.txt", b"safe"), (member, b"stand-in")])
    with pytest.raises(ReleaseArtifactError, match="RELEASE_ARCHIVE_MEMBER"):
        artifacts.extract_selected_zip(
            path, expected, tmp_path / "stage", {"good.txt": "runtime/good.txt"}
        )
    assert not (tmp_path / "stage").exists()


def test_selected_directory_mode_cannot_be_laundered_as_regular_file(tmp_path: Path) -> None:
    member = zipfile.ZipInfo("directory-without-slash")
    member.create_system = 3
    member.external_attr = (stat.S_IFDIR | 0o755) << 16
    path, expected = _zip(tmp_path, [(member, b"synthetic directory payload")])
    with pytest.raises(ReleaseArtifactError):
        artifacts.extract_selected_zip(
            path, expected, tmp_path / "stage", {member.filename: "runtime/payload"}
        )
    assert not (tmp_path / "stage/runtime/payload").exists()


def test_zip_case_collisions_and_destination_collisions_are_rejected(tmp_path: Path) -> None:
    path, expected = _zip(tmp_path, [("A.txt", b"a"), ("a.txt", b"b")])
    with pytest.raises(ReleaseArtifactError, match="RELEASE_COLLISION"):
        artifacts.extract_selected_zip(path, expected, tmp_path / "stage", {"A.txt": "runtime/a"})
    path, expected = _zip(tmp_path, [("a", b"a"), ("b", b"b")])
    with pytest.raises(ReleaseArtifactError, match="RELEASE_COLLISION"):
        artifacts.extract_selected_zip(
            path, expected, tmp_path / "stage", {"a": "runtime/X", "b": "runtime/x"}
        )


@pytest.mark.parametrize("budget", ["count", "expanded"])
def test_zip_metadata_limits_apply_to_whole_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    path, expected = _zip(tmp_path, [("a", b"aaa"), ("b", b"bbb")])
    monkeypatch.setattr(
        artifacts,
        "MAX_ZIP_FILES" if budget == "count" else "MAX_STAGE_BYTES",
        1 if budget == "count" else 5,
    )
    with pytest.raises(ReleaseArtifactError, match="RELEASE_LIMIT"):
        artifacts.extract_selected_zip(path, expected, tmp_path / "stage", {"a": "runtime/a"})


def test_subset_copy_never_uses_extract_or_runs_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, expected = _zip(
        tmp_path, [("payload.py", b"raise RuntimeError('must not execute')"), ("unused", b"unused")]
    )

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unsafe zip convenience extraction was invoked")

    monkeypatch.setattr(zipfile.ZipFile, "extract", unexpected)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", unexpected)
    assert artifacts.extract_selected_zip(
        path, expected, tmp_path / "stage", {"payload.py": "runtime/payload.py"}
    ) == ["runtime/payload.py"]
    assert not (tmp_path / "stage/unused").exists()


def test_zip_authentication_precedes_any_selection_or_write(tmp_path: Path) -> None:
    path, expected = _zip(tmp_path, [("a", b"synthetic")])
    path.write_bytes(path.read_bytes() + b"unapproved additional bytes")
    with pytest.raises(ReleaseArtifactError):
        artifacts.extract_selected_zip(path, expected, tmp_path / "stage", {"a": "runtime/a"})
    assert not (tmp_path / "stage").exists()


def test_existing_destination_rolls_back_only_new_subset_files(tmp_path: Path) -> None:
    path, expected = _zip(tmp_path, [("a", b"new-a"), ("b", b"new-b")])
    destination = tmp_path / "stage"
    (destination / "runtime").mkdir(parents=True)
    (destination / "runtime/b").write_bytes(b"existing-b")
    with pytest.raises(FileExistsError):
        artifacts.extract_selected_zip(
            path, expected, destination, {"a": "runtime/a", "b": "runtime/b"}
        )
    assert not (destination / "runtime/a").exists()
    assert (destination / "runtime/b").read_bytes() == b"existing-b"


def test_cancelled_zip_copy_removes_owned_files_preserves_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, expected = _zip(tmp_path, [("a", b"aaa"), ("b", b"bbb")])
    destination = tmp_path / "stage"
    destination.mkdir()
    (destination / "prior").write_bytes(b"prior")
    original = zipfile.ZipExtFile.read

    def interrupted(self: zipfile.ZipExtFile, *args: Any, **kwargs: Any) -> bytes:
        if self.name == "b":
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", interrupted)
    with pytest.raises(KeyboardInterrupt):
        artifacts.extract_selected_zip(
            path, expected, destination, {"a": "runtime/a", "b": "runtime/b"}
        )
    assert (destination / "prior").read_bytes() == b"prior"
    assert not (destination / "runtime/a").exists()
    assert not (destination / "runtime/b").exists()


def test_cancelled_package_write_removes_owned_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "a").write_bytes(b"synthetic")
    records = inventory_files(stage, {"a": "synthetic"})
    output = tmp_path / "new.zip"

    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(zipfile.ZipFile, "open", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_package(stage, records, output)
    assert not output.exists()


def test_native_dependency_lookup_rejects_ambiguous_basename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _zip(
        tmp_path,
        [
            ("binding.pyd", b"synthetic root"),
            ("first/shared.dll", b"synthetic first"),
            ("second/SHARED.dll", b"synthetic second"),
        ],
    )

    def imports(raw: bytes) -> tuple[list[str], list[str]]:
        return (["shared.dll"], []) if raw == b"synthetic root" else ([], [])

    monkeypatch.setattr(staging, "pe_imports", imports)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ReleaseArtifactError, match="RELEASE_COLLISION"):
            staging._native_dependencies(archive, {"binding.pyd"})


def test_schema_validation_short_circuits_first_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import release_evidence

    class Validator:
        @staticmethod
        def check_schema(schema: object) -> None:
            pass

        def __init__(self, schema: object) -> None:
            pass

        def iter_errors(self, data: object) -> Any:
            yield object()
            pytest.fail("validation enumerated every invalid field after a known failure")

    monkeypatch.setattr(release_evidence, "Draft202012Validator", Validator)
    with pytest.raises(release_evidence.ReleaseEvidenceError, match="RELEASE_INVENTORY_SCHEMA"):
        release_evidence.validate_data({})
