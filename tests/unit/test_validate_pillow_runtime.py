"""Admission regressions for exact native bytes, RECORD and reviewed closure."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from importlib.metadata import PathDistribution
from pathlib import Path

import pytest

from tools.validate_pillow_runtime import (
    MANIFEST,
    PROJECT_ROOT,
    FileEvidence,
    PillowEvidenceError,
    validate_installed_files,
    validate_repository,
)


@pytest.fixture
def evidence_repository(tmp_path: Path) -> Path:
    for name in (MANIFEST, Path("packaging/dependency-inventory-v1.json"), Path("uv.lock")):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / name, target)
    return tmp_path


def _synthetic_install(root: Path) -> tuple[tuple[FileEvidence, ...], PathDistribution]:
    info = root / "pillow-12.3.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: pillow\nVersion: 12.3.0\n", encoding="utf-8")
    member = root / "PIL" / "_imaging.cp314-win_amd64.pyd"
    member.parent.mkdir()
    content = b"synthetic-native-evidence-not-executable"
    member.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    encoded = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode()
    relative = member.relative_to(root).as_posix()
    (info / "RECORD").write_text(f"{relative},sha256={encoded},{len(content)}\n", encoding="utf-8")
    return (FileEvidence(relative, len(content), digest),), PathDistribution(info)


def test_reviewed_manifest_binds_current_lock_and_inventory(evidence_repository: Path) -> None:
    validate_repository(evidence_repository, inspect_install=False)


@pytest.mark.parametrize("field", ["version_or_revision", "license_route", "evidence"])
def test_component_provenance_cannot_drift(evidence_repository: Path, field: str) -> None:
    path = evidence_repository / MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["embedded_components"][0][field] = "unreviewed"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_MANIFEST$"):
        validate_repository(evidence_repository, inspect_install=False)


def test_lock_wheel_substitution_is_rejected(evidence_repository: Path) -> None:
    path = evidence_repository / "uv.lock"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "fdafc9cce40277e0f7a0feabce0ee50dd2fa1800f3b38015e51296b5e814048d", "0" * 64
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_LOCK$"):
        validate_repository(evidence_repository, inspect_install=False)


def test_inventory_cannot_move_decoder_to_gui(evidence_repository: Path) -> None:
    path = evidence_repository / "packaging/dependency-inventory-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    pillow = next(item for item in payload["components"] if item["component_id"] == "pillow")
    pillow["security"]["boundary"] = "gui-process"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_INVENTORY$"):
        validate_repository(evidence_repository, inspect_install=False)


def test_record_and_actual_native_bytes_must_both_match(tmp_path: Path) -> None:
    expected, installed = _synthetic_install(tmp_path)
    validate_installed_files(expected, installed)
    member = tmp_path / expected[0].path
    member.write_bytes(b"x" * expected[0].size_bytes)
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_MEMBER$"):
        validate_installed_files(expected, installed)


def test_forged_record_cannot_bless_changed_native_bytes(tmp_path: Path) -> None:
    expected, installed = _synthetic_install(tmp_path)
    record = tmp_path / "pillow-12.3.0.dist-info" / "RECORD"
    record.write_text(
        f"{expected[0].path},sha256={'A' * 43},{expected[0].size_bytes}\n", encoding="utf-8"
    )
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_RECORD$"):
        validate_installed_files(expected, installed)


@pytest.mark.parametrize("extra", ["PIL/unreviewed.dll", "PIL/unreviewed.pyd"])
def test_extra_native_members_are_rejected(tmp_path: Path, extra: str) -> None:
    expected, installed = _synthetic_install(tmp_path)
    (tmp_path / extra).write_bytes(b"x")
    record = tmp_path / "pillow-12.3.0.dist-info" / "RECORD"
    with record.open("a", encoding="utf-8") as destination:
        destination.write(f"{extra},,1\n")
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_MEMBERS$"):
        validate_installed_files(expected, installed)


def test_duplicate_record_member_is_rejected(tmp_path: Path) -> None:
    expected, installed = _synthetic_install(tmp_path)
    record = tmp_path / "pillow-12.3.0.dist-info" / "RECORD"
    text = record.read_text(encoding="utf-8")
    record.write_text(text + text, encoding="utf-8")
    with pytest.raises(PillowEvidenceError, match="^PILLOW_EVIDENCE_RECORD$"):
        validate_installed_files(expected, installed)
