"""Exact artifacts, closed reader evidence, and offline upload boundaries."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pikepdf
import pytest

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import PORTAL_PDF_EXPORT_PROFILE
from tools import pws0506_portal_compatibility as harness


@pytest.fixture
def frozen_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        harness,
        "provenance",
        lambda: {
            "base_revision": "a" * 40,
            "implementation_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        harness,
        "EXPECTED_ARTIFACT_HASHES",
        {name: hashlib.sha256(payload).hexdigest() for name, payload in _fake_payloads().items()},
    )


def _fake_payloads() -> dict[str, bytes]:
    payloads = {
        name: (f"bounded synthetic test artifact {index}\n" * 40).encode()
        for index, name in enumerate(harness.PDF_NAMES)
    }
    with io.BytesIO() as memory:
        with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_STORED) as bundle:
            for name in harness.PDF_NAMES[3:]:
                bundle.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), payloads[name])
        payloads["portal-parts.zip"] = memory.getvalue()
    return payloads


def _fake_generator(workspace: Path) -> dict[str, object]:
    inventory: dict[str, Any] = {}
    for name, payload in _fake_payloads().items():
        (workspace / name).write_bytes(payload)
        inventory[name] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
            "page_count": 1 if name in harness.PDF_NAMES[3:] else 3,
        }
    return {
        "artifacts": inventory,
        "compression": {},
        "local_upload_checks": harness.local_upload_checks(workspace, inventory),
    }


def _prepare(tmp_path: Path) -> Path:
    return harness.prepare(tmp_path / "corpus", allowed_root=tmp_path, generator=_fake_generator)


def test_full_size_source_and_portal_plan_are_deterministic() -> None:
    payload = harness._synthetic_source()
    assert payload == harness._synthetic_source()
    with pikepdf.Pdf.open(io.BytesIO(payload)) as document:
        assert len(document.pages) == 3
        assert all(
            tuple(float(value) for value in page.obj.MediaBox) == (0, 0, 612, 792)
            for page in document.pages
        )
        assert document.pages[1].obj.Rotate == 180
        assert document.pages[2].obj.Resources.XObject.Scan.Width == 1800
    plan = harness.build_corpus_plan(payload)
    assert plan.base_plan.profile == PORTAL_PDF_EXPORT_PROFILE
    assert plan.base_plan.plan_version == 3
    assert len(plan.pages) == 3
    assert len(plan.assets) == 1
    assert plan.pages[1].active_crop != NormalizedPageBox.full_page()


def test_prepare_is_resumable_and_does_not_record_reader_passes(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    workspace = _prepare(tmp_path)
    before = {path.name: path.read_bytes() for path in workspace.iterdir()}
    assert _prepare(tmp_path) == workspace
    assert before == {path.name: path.read_bytes() for path in workspace.iterdir()}
    assert set(harness.status(workspace, allowed_root=tmp_path).values()) == {"NOT RUN"}
    evidence = harness.verify(workspace, allowed_root=tmp_path)
    assert all(item["result"] == "pass" for item in evidence["local_upload_checks"].values())
    assert evidence["claim_boundaries"]["portal_acceptance"] is False


def test_local_upload_matrix_checks_actual_sizes_and_rejection_codes(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    evidence = harness.verify(_prepare(tmp_path), allowed_root=tmp_path)
    checks = evidence["local_upload_checks"]
    assert len(checks) == 22
    for mode in ("combined_pdf", "separate_pdfs", "zip"):
        assert checks[f"{mode}.exact"]["finding_codes"] == []
        assert (
            "PORTAL_TOTAL_SIZE_EXCEEDED" in checks[f"{mode}.total_one_byte_lower"]["finding_codes"]
        )
        assert "PORTAL_FILE_SIZE_EXCEEDED" in checks[f"{mode}.file_one_byte_lower"]["finding_codes"]
        assert (
            "PORTAL_ENCRYPTION_DETECTED" in checks[f"{mode}.encrypted_observation"]["finding_codes"]
        )
        assert "PORTAL_EXTENSION_INVALID" in checks[f"{mode}.wrong_extension"]["finding_codes"]
        assert (
            "PORTAL_OUTPUT_MODE_NOT_ALLOWED" in checks[f"{mode}.disallowed_mode"]["finding_codes"]
        )
    assert checks["separate_pdfs.count_one_lower"]["finding_codes"] == [
        "PORTAL_FILE_COUNT_EXCEEDED"
    ]
    assert checks["combined_pdf.pages_one_lower"]["finding_codes"] == ["PORTAL_PAGE_COUNT_EXCEEDED"]


def test_partial_workspace_and_modified_artifact_fail_without_overwrite(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "keep.txt").write_bytes(b"preserve unrelated bytes")
    with pytest.raises(harness.CompatibilityError, match="WORKSPACE_INCOMPLETE"):
        harness.prepare(partial, allowed_root=tmp_path, generator=_fake_generator)
    assert (partial / "keep.txt").read_bytes() == b"preserve unrelated bytes"
    workspace = _prepare(tmp_path)
    target = workspace / harness.PDF_NAMES[0]
    target.write_bytes(b"changed")
    with pytest.raises(harness.CompatibilityError, match="ARTIFACT_MISMATCH"):
        _prepare(tmp_path)
    assert target.read_bytes() == b"changed"


def test_changed_producer_bytes_invalidates_reuse(
    tmp_path: Path,
    frozen_provenance: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _prepare(tmp_path)
    monkeypatch.setattr(
        harness,
        "provenance",
        lambda: {
            "base_revision": "a" * 40,
            "implementation_sha256": "c" * 64,
        },
    )
    with pytest.raises(harness.CompatibilityError, match="EVIDENCE_MISMATCH"):
        harness.verify(workspace, allowed_root=tmp_path)


def test_self_consistent_replacement_cannot_change_the_fixed_corpus(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    workspace = _prepare(tmp_path)
    target = workspace / harness.PDF_NAMES[0]
    payload = b"self-consistent replacement"
    target.write_bytes(payload)
    evidence_path = workspace / "evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence["artifacts"][target.name].update(
        {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        }
    )
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(harness.CompatibilityError, match="ARTIFACT_MISMATCH"):
        harness.verify(workspace, allowed_root=tmp_path)


def test_computer_observations_have_explicit_operator_and_closed_values(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    workspace = _prepare(tmp_path)
    target = harness.record(
        workspace,
        "W2",
        "152.0.1",
        "10.0.26200",
        dict.fromkeys(harness.CHECKS, True),
        allowed_root=tmp_path,
        operator="codex_computer_use",
    )
    assert json.loads(target.read_bytes())["operator"] == "codex_computer_use"
    with pytest.raises(harness.CompatibilityError, match="READER_RECORD_INVALID"):
        harness.record(
            workspace,
            "W2",
            "152.0.1",
            "10.0.26200",
            dict.fromkeys(harness.CHECKS, True),
            allowed_root=tmp_path,
            operator="unobserved_simulation",
        )


def test_no_escape_or_link_workspace(tmp_path: Path, frozen_provenance: None) -> None:
    with pytest.raises(harness.CompatibilityError, match="WORKSPACE_INVALID"):
        harness.prepare(
            tmp_path / "a" / ".." / ".." / "escape",
            allowed_root=tmp_path,
            generator=_fake_generator,
        )


def test_reader_attempts_are_bound_exclusive_and_latest_failure_wins(
    tmp_path: Path,
    frozen_provenance: None,
) -> None:
    workspace = _prepare(tmp_path)
    answers = dict.fromkeys(harness.CHECKS, True)
    first = harness.record(workspace, "W1", "26.1.1", "10.0.26200", answers, allowed_root=tmp_path)
    assert harness.status(workspace, allowed_root=tmp_path)["W1"] == "PASS"
    answers["close_and_reopen"] = False
    second = harness.record(workspace, "W1", "26.1.1", "10.0.26200", answers, allowed_root=tmp_path)
    assert first != second and first.exists()
    assert harness.status(workspace, allowed_root=tmp_path)["W1"] == "FAIL"
    recorded = json.loads(second.read_bytes())
    assert recorded["checks"]["close_and_reopen"] is False
    assert (
        recorded["evidence_sha256"]
        == hashlib.sha256((workspace / "evidence.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("mutation", ["extra", "version", "checks", "forged_pass", "binding"])
def test_reader_records_reject_unclosed_or_forged_evidence(
    tmp_path: Path,
    frozen_provenance: None,
    mutation: str,
) -> None:
    workspace = _prepare(tmp_path)
    target = harness.record(
        workspace, "A1", "26.8.1", "16", dict.fromkeys(harness.CHECKS, False), allowed_root=tmp_path
    )
    record = json.loads(target.read_bytes())
    if mutation == "extra":
        record["private_path"] = "PRIVATE_CANARY"
    elif mutation == "version":
        record["viewer_version"] = "PRIVATE_CANARY"
    elif mutation == "checks":
        record["checks"]["correct_viewer"] = 1
    elif mutation == "forged_pass":
        record["result"] = "PASS"
    else:
        record["evidence_sha256"] = "c" * 64
    target.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(harness.CompatibilityError, match="READER_RECORD_INVALID") as caught:
        harness.status(workspace, allowed_root=tmp_path)
    assert "PRIVATE_CANARY" not in str(caught.value)


def test_invalid_version_never_creates_attempt(tmp_path: Path, frozen_provenance: None) -> None:
    workspace = _prepare(tmp_path)
    with pytest.raises(harness.CompatibilityError, match="READER_RECORD_INVALID"):
        harness.record(
            workspace,
            "W3",
            "Private browser profile",
            "10.0.26200",
            dict.fromkeys(harness.CHECKS, True),
            allowed_root=tmp_path,
        )
    assert not list(workspace.glob("*-attempt-*.json"))


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(harness.CompatibilityError, match="EVIDENCE_INVALID"):
        harness._load(path)
