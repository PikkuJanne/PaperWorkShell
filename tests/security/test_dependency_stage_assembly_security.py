"""The assembler must not discover private inputs or publish failed candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools import build_dependency_stage as builder
from tools.build_libtiff_no_jbig import REQUIRED_SIGNER
from tools.release_artifacts import ReleaseArtifactError


def make_assembly(root: Path) -> builder.Assembly:
    stage, inputs = root / "output/payload", root / "output/inputs"
    stage.mkdir(parents=True)
    inputs.mkdir()
    return builder.Assembly(root, stage, inputs, [], fetch=False)


@pytest.mark.parametrize(
    "relative", ["../private.txt", "C:/private.txt", "folder/../private.txt", "folder\\private.txt"]
)
def test_repo_input_rejects_traversal(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_PATH$"):
        builder.repository_path(relative, tmp_path)


def test_explicit_output_scope_rejects_existing_private_directory(tmp_path: Path) -> None:
    private = tmp_path / "private-token"
    private.mkdir()
    document = private / "sensitive-document.txt"
    document.write_bytes(b"SENSITIVE_FIXTURE_TOKEN")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_OUTPUT_SCOPE$"):
        builder.output_path(private, tmp_path)
    assert document.read_bytes() == b"SENSITIVE_FIXTURE_TOKEN"


def test_corrupt_notice_cannot_enter_stage(tmp_path: Path) -> None:
    assembly = make_assembly(tmp_path)
    source = tmp_path / "notice.txt"
    source.write_bytes(b"unreviewed synthetic text")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_NOTICE_HASH$"):
        assembly.text(
            {"asset_path": "notice.txt", "path": "notices/approved.txt", "sha256": "0" * 64},
            "synthetic",
        )
    assert not list(assembly.stage.iterdir())


@pytest.mark.parametrize("payload", [{"schema_version": 999}, {"schema_version": 0}])
def test_future_and_old_unknown_review_fail_before_stage(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    assembly = make_assembly(tmp_path)
    review = tmp_path / "review.json"
    review.write_text(json.dumps(payload), encoding="utf8")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_REVIEW_VERSION$"):
        assembly.review("review.json")
    assert not assembly.review_inputs
    assert not assembly.owners


def test_aliasing_names_do_not_overwrite_owned_file(tmp_path: Path) -> None:
    assembly = make_assembly(tmp_path)
    assembly.bytes("notices/Fixed.txt", b"first", "synthetic")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_COLLISION$"):
        assembly.bytes("notices/fixed.txt", b"second", "synthetic")
    assert (assembly.stage / "notices/Fixed.txt").read_bytes() == b"first"


def test_local_generated_input_needs_hash_bound_recipe(tmp_path: Path) -> None:
    assembly = make_assembly(tmp_path)
    source = tmp_path / "output/local.zip"
    data = b"Synthetic local build"
    source.write_bytes(data)
    item = {
        "name": source.name,
        "url": None,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "local_path": "output/local.zip",
    }
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_INPUT_SOURCE$"):
        assembly.input(item, "runtime", recipe="unreviewed.py")
    assert not list(assembly.artifacts.iterdir())


def test_only_exact_reviewed_german_archive_can_inherit_pinned_signer() -> None:
    keyring = {"required_signer_fingerprint": REQUIRED_SIGNER}
    german = {"package_id": builder.GERMAN_PACKAGE, "sha256": builder.GERMAN_ARCHIVE_SHA256}
    builder.verify_signer_metadata(german, keyring)
    for package in [
        dict(german, sha256="f" * 64),
        dict(german, package_id="unreviewed"),
        dict(german, expected_signer_fingerprint="f" * 40),
    ]:
        with pytest.raises(ReleaseArtifactError, match="^RELEASE_SIGNER_IDENTITY$"):
            builder.verify_signer_metadata(package, keyring)
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_SIGNER_IDENTITY$"):
        builder.verify_signer_metadata(german, {"required_signer_fingerprint": "f" * 40})


def test_gpg_and_all_thirteen_dlls_are_authenticated_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "tool"
    directory.mkdir()
    files = []
    for name in ["gpg.exe", *(f"library{index}.dll" for index in range(13))]:
        data = ("Synthetic external tool fixture: " + name).encode("ascii")
        (directory / name).write_bytes(data)
        files.append(
            {
                "name": name,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "local_dependencies": [],
            }
        )
    executable = directory / "gpg.exe"
    builder.verify_gpg_tool(executable, {"tool_files": files})
    (directory / "library7.dll").write_bytes(b"changed")

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Unverified GPG was about to execute")

    monkeypatch.setattr(builder, "verify_msys_signatures", forbidden)
    assembly = make_assembly(tmp_path)
    with pytest.raises(ReleaseArtifactError):
        builder.stage_tesseract(assembly, {}, {}, {}, tmp_path, executable, {"tool_files": files})
    assert not assembly.owners


def test_gpg_review_missing_or_unclosed_vendor_object_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_GPG_IDENTITY$"):
        builder.verify_gpg_tool(tmp_path / "gpg.exe", {"tool_files": []})


def test_cli_unexpected_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(**_kwargs: Any) -> dict[str, object]:
        raise OSError("C:/private/SENSITIVE_FIXTURE_TOKEN document title")

    monkeypatch.setattr(builder, "assemble", fail)
    code = builder.main(
        [
            "--artifacts",
            "unused",
            "--destination",
            "unused-stage",
            "--inventory-output",
            "unused-json",
            "--app-review",
            "unused-review",
            "--qpdf-review",
            "unused-qpdf",
            "--gpg-executable",
            "unused-gpg",
            "--candidate-id",
            "synthetic",
            "--created-utc",
            "2026-11-03T00:00:00Z",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert json.loads(captured.out) == {"status": "invalid", "code": "RELEASE_STAGE_INVALID"}
    assert captured.err == ""
    assert "SENSITIVE" not in captured.out
    assert "private" not in captured.out
