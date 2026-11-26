"""Exercise the bounded update ZIP and the verified retained-slot boundary."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import UpdateMetadata
from paperworkshell.storage.update_payload import (
    INSTALLED_MARKER_NAME,
    MANIFEST_NAME,
    UpdatePayloadError,
    UpdatePayloadErrorCode,
    extract_update_payload,
    validate_installed_update_payload,
)
from tools import build_update_payload as builder
from tools.release_staging import canonical_json, inventory_files

_MARKER = "runtime/" + INSTALLED_MARKER_NAME
_TERMS = "notices/Microsoft-runtime-recipient-terms.txt"
_TARGET = "runtime/engines/tesseract/bin/tesseract.exe"
_IDENTITY = {
    "application_id": INSTALLED_APPLICATION_ID,
    "channel": "engineering",
    "version": "1.2.3",
    "candidate_id": "pws0803-fixture",
}


def _file_bytes() -> dict[str, bytes]:
    return {
        _MARKER: canonical_json({"schema_version": 1, **_IDENTITY}),
        "runtime/PaperWorkShell.exe": b"synthetic launcher",
        "runtime/PaperWorkShellDesktop.exe": b"synthetic desktop",
        "runtime/python.exe": b"synthetic interpreter",
        "runtime/python314._pth": b"synthetic runtime configuration",
        _TARGET: b"synthetic tesseract",
        _TERMS: b"synthetic recipient terms",
        "notices/COMPONENTS.txt": b"synthetic licenses",
        "sources/PaperWorkShell.tar.gz": b"synthetic corresponding source",
        "evidence/source-receipt.json": b"synthetic release receipt",
    }


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _component_id(path: str) -> str:
    return "tesseract" if path == _TARGET else "paperworkshell"


def _component_versions() -> list[dict[str, str]]:
    return [
        {"component_id": "paperworkshell", "version": "1.2.3"},
        {"component_id": "tesseract", "version": "5.5.0"},
    ]


def _manifest(files: dict[str, bytes] | None = None) -> dict[str, Any]:
    actual = _file_bytes() if files is None else files
    return {
        "schema_version": 1,
        **_IDENTITY,
        "case_schema_version": 4,
        "inventory_sha256": "a" * 64,
        "components": _component_versions(),
        "files": [
            {
                "path": path,
                "size_bytes": len(data),
                "sha256": _sha(data),
                "component_id": _component_id(path),
            }
            for path, data in actual.items()
        ],
    }


def _archive(
    *,
    files: dict[str, bytes] | None = None,
    manifest: bytes | None = None,
    extra: tuple[zipfile.ZipInfo, bytes] | None = None,
) -> bytes:
    actual = _file_bytes() if files is None else files
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            MANIFEST_NAME, manifest if manifest is not None else canonical_json(_manifest(actual))
        )
        for path, data in actual.items():
            archive.writestr(path, data)
        if extra is not None:
            archive.writestr(*extra)
    return raw.getvalue()


def _metadata(raw: bytes, *, candidate_id: str = _IDENTITY["candidate_id"]) -> UpdateMetadata:
    return UpdateMetadata(
        channel=_IDENTITY["channel"],
        version=_IDENTITY["version"],
        candidate_id=candidate_id,
        sequence=3,
        issued_at=datetime(2026, 11, 26, 10, tzinfo=UTC),
        expires_at=datetime(2026, 11, 26, 16, tzinfo=UTC),
        package_size=len(raw),
        package_sha256=_sha(raw),
    )


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "slot"
    stage.mkdir()
    return stage


def test_exact_payload_roundtrip_and_retained_slot(tmp_path: Path) -> None:
    raw = _archive()
    stage = _stage(tmp_path)
    metadata = _metadata(raw)
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    payload = extract_update_payload(
        io.BytesIO(raw), stage, metadata, installed_case_schema_version=4, checkpoint=checkpoint
    )
    manifest_copy = (stage / MANIFEST_NAME).read_bytes()
    assert payload.manifest_sha256 == _sha(manifest_copy)
    assert payload.inventory_sha256 == "a" * 64
    assert payload.recipient_terms_sha256 == _sha(_file_bytes()[_TERMS])
    assert dict(payload.components) == {"paperworkshell": "1.2.3", "tesseract": "5.5.0"}
    assert {item.path: (stage / item.path).read_bytes() for item in payload.files} == _file_bytes()
    assert checkpoints > len(payload.files)
    checkpoints = 0
    assert (
        validate_installed_update_payload(
            stage, expected_manifest_sha256=payload.manifest_sha256, checkpoint=checkpoint
        )
        == payload
    )
    assert checkpoints > len(payload.files)


@pytest.mark.parametrize(
    "entry",
    [
        "runtime/../private-canary",
        "runtime/App.exe:private-canary",
        "runtime/nul .txt",
        "runtime/CONIN$.txt",
        "runtime/dir./private-canary",
        "runtime//private-canary",
        "/runtime/private-canary",
        "evidence\\private-canary",
        "cases/private-canary",
        "runtime/PAPERWORKSHELL.EXE",
        "runtime/Engines/private-canary",
        "runtime/python.exe/private-canary",
    ],
)
def test_unsafe_alias_or_path_rejected_before_staging(tmp_path: Path, entry: str) -> None:
    files = _file_bytes() | {entry: b"synthetic data"}
    raw = _archive(files=files)
    stage = _stage(tmp_path)
    with pytest.raises(UpdatePayloadError) as caught:
        extract_update_payload(
            io.BytesIO(raw), stage, _metadata(raw), installed_case_schema_version=4
        )
    assert caught.value.code == UpdatePayloadErrorCode.INVALID
    assert "private-canary" not in str(caught.value)
    assert not list(stage.iterdir())


@pytest.mark.parametrize("change", ["extra", "missing", "symlink", "trailer", "owner"])
def test_closed_archive_inventory_and_member_formats(tmp_path: Path, change: str) -> None:
    files = _file_bytes()
    manifest = _manifest(files)
    extra = None
    suffix = b""
    if change == "extra":
        files["runtime/uninventoried.exe"] = b"extra"
    elif change == "missing":
        files.pop(_TARGET)
    elif change == "symlink":
        info = zipfile.ZipInfo("runtime/symlink.exe")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        extra = (info, b"../../outside")
    elif change == "trailer":
        suffix = b"unexpected trailer"
    else:
        manifest["files"][0]["component_id"] = "unknown-owner"
    raw = _archive(files=files, manifest=canonical_json(manifest), extra=extra) + suffix
    stage = _stage(tmp_path)
    with pytest.raises(UpdatePayloadError, match="payload is invalid"):
        extract_update_payload(
            io.BytesIO(raw), stage, _metadata(raw), installed_case_schema_version=4
        )
    assert not list(stage.iterdir())


@pytest.mark.parametrize(
    ("case_epoch", "change", "code"),
    [
        (3, "none", UpdatePayloadErrorCode.SCHEMA_MISMATCH),
        (4, "metadata", UpdatePayloadErrorCode.IDENTITY_MISMATCH),
        (4, "marker", UpdatePayloadErrorCode.IDENTITY_MISMATCH),
    ],
)
def test_case_compatibility_and_identity_checked_before_copy(
    tmp_path: Path, case_epoch: int, change: str, code: UpdatePayloadErrorCode
) -> None:
    files = _file_bytes()
    if change == "marker":
        files[_MARKER] = canonical_json({"schema_version": 1, **_IDENTITY, "version": "1.2.4"})
    raw = _archive(files=files)
    metadata = _metadata(
        raw, candidate_id="wrong" if change == "metadata" else _IDENTITY["candidate_id"]
    )
    stage = _stage(tmp_path)
    with pytest.raises(UpdatePayloadError) as caught:
        extract_update_payload(
            io.BytesIO(raw), stage, metadata, installed_case_schema_version=case_epoch
        )
    assert caught.value.code == code
    assert not list(stage.iterdir())


def test_member_hash_failure_preserves_partial_stage_without_manifest(tmp_path: Path) -> None:
    files = _file_bytes()
    manifest = canonical_json(_manifest(files))
    files[_TARGET] = b"?" + files[_TARGET][1:]
    raw = _archive(files=files, manifest=manifest)
    stage = _stage(tmp_path)
    with pytest.raises(UpdatePayloadError, match="payload is invalid"):
        extract_update_payload(
            io.BytesIO(raw), stage, _metadata(raw), installed_case_schema_version=4
        )
    assert any(stage.iterdir())
    assert not (stage / MANIFEST_NAME).exists()


def test_cancelled_and_disk_failure_leave_no_commit_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _archive()
    stage = _stage(tmp_path)
    with pytest.raises(UpdatePayloadError) as cancelled:
        extract_update_payload(
            io.BytesIO(raw),
            stage,
            _metadata(raw),
            installed_case_schema_version=4,
            cancelled=lambda: True,
        )
    assert cancelled.value.code == UpdatePayloadErrorCode.CANCELLED
    assert not (stage / MANIFEST_NAME).exists()
    second = tmp_path / "second"
    second.mkdir()
    original = Path.open

    def out_of_space(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if path.is_relative_to(second) and mode == "xb":
            raise OSError("private-path-canary")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", out_of_space)
    with pytest.raises(UpdatePayloadError) as failure:
        extract_update_payload(
            io.BytesIO(raw), second, _metadata(raw), installed_case_schema_version=4
        )
    assert failure.value.code == UpdatePayloadErrorCode.STAGE_UNAVAILABLE
    assert "private-path-canary" not in str(failure.value)
    assert not (second / MANIFEST_NAME).exists()


@pytest.mark.parametrize("change", ["manifest", "file", "extra", "hardlink"])
def test_retained_slot_rejects_tampering_or_extra_members(tmp_path: Path, change: str) -> None:
    raw = _archive()
    stage = _stage(tmp_path)
    payload = extract_update_payload(
        io.BytesIO(raw), stage, _metadata(raw), installed_case_schema_version=4
    )
    if change == "manifest":
        manifest = stage / MANIFEST_NAME
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    elif change == "file":
        target = stage / _TARGET
        target.write_bytes(b"?" + target.read_bytes()[1:])
    elif change == "extra":
        (stage / "runtime" / "extra.py").write_bytes(b"extra")
    else:
        (tmp_path / "alias.txt").hardlink_to(stage / _TARGET)
    with pytest.raises(UpdatePayloadError, match="payload is invalid"):
        validate_installed_update_payload(stage, expected_manifest_sha256=payload.manifest_sha256)


def test_builder_emits_deterministic_reader_compatible_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path)
    files = _file_bytes()
    for name, raw in files.items():
        at = stage / name
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_bytes(raw)
    inventory_path = tmp_path / "resolved-inventory.json"
    inventory_path.write_bytes(
        canonical_json(
            {
                "candidate": {"id": _IDENTITY["candidate_id"], "version": _IDENTITY["version"]},
                "components": _component_versions(),
                "files": inventory_files(stage, {path: _component_id(path) for path in files}),
            }
        )
    )
    # Only the release inventory schema and provenance are outside this synthetic fixture.
    monkeypatch.setattr(builder, "validate_data", lambda inventory: None)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_receipt = builder.build_update_payload(
        inventory_path, stage, first, channel=_IDENTITY["channel"], case_schema_version=4
    )
    second_receipt = builder.build_update_payload(
        inventory_path, stage, second, channel=_IDENTITY["channel"], case_schema_version=4
    )
    assert first_receipt == second_receipt
    raw = first.read_bytes()
    assert second.read_bytes() == raw
    new_stage = tmp_path / "other-slot"
    new_stage.mkdir()
    payload = extract_update_payload(
        io.BytesIO(raw), new_stage, _metadata(raw), installed_case_schema_version=4
    )
    assert payload.inventory_sha256 == first_receipt["inventory_sha256"]
    assert payload.manifest_sha256 == first_receipt["manifest_sha256"]
    assert _sha(raw) == first_receipt["package_sha256"]
    assert json.loads((new_stage / MANIFEST_NAME).read_bytes())["case_schema_version"] == 4
