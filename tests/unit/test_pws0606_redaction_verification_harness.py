"""Guided human observations, immutable upgrades and exact saved-byte receipts."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.domain.redaction_verification import (
    AUTOMATED_METHODS,
    RedactionVerificationReport,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from tests.security.test_redaction_verification_corpus import _verification_request
from tools import pws0606_redaction_verification as harness


def _proof(
    content: bytes, reference: bytes, evidence: dict[str, Any], _scratch: Path
) -> dict[str, object]:
    request = _verification_request(
        content,
        reference,
        tuple(evidence["affected_pages"]),
        evidence["page_count"],
        harness._TOKENS,
        tuple(evidence["forbidden_stream_sha256"]),
    )
    report = RedactionVerificationReport(
        request.fingerprint,
        request.candidate_sha256,
        tuple(
            VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)
            if method in AUTOMATED_METHODS
            else VerificationMethodResult(
                method, VerificationStatus.UNKNOWN, VerificationCode.MISSING
            )
            for method in VerificationMethod
        ),
    )
    return {
        "automated_passed": report.automated_passed,
        "release_ready": report.release_ready,
        "report": report.to_record(),
    }


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(harness, "WORKSPACE", tmp_path)
    original_environment = harness.ENVIRONMENT.read_bytes()
    environment = tmp_path / "environment.json"
    environment.write_bytes(original_environment)
    monkeypatch.setattr(harness, "ENVIRONMENT", environment)
    monkeypatch.setattr(
        harness,
        "_provenance",
        lambda: {
            "synthetic-processing": "a" * 64,
            harness.HELPER: harness._LEGACY_HELPER,
        },
    )
    artifacts = {
        "packet.pdf": b"SYNTHETIC CANDIDATE",
        "reference.pdf": b"SYNTHETIC REFERENCE",
        "unsafe-cover.pdf": b"SYNTHETIC UNSAFE CONTROL",
    }
    for name, content in artifacts.items():
        (tmp_path / name).write_bytes(content)
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "PWS-0606",
        "synthetic_only": True,
        "feature_enabled": False,
        "page_count": 16,
        "affected_pages": [],
        "forbidden_stream_sha256": [],
        "implementation": harness._provenance(),
        "artifacts": {
            name: {"sha256": sha256(content).hexdigest(), "byte_size": len(content)}
            for name, content in artifacts.items()
        },
    }
    evidence["verification"] = _proof(
        artifacts["packet.pdf"], artifacts["reference.pdf"], evidence, tmp_path
    )
    harness._write(tmp_path / "evidence.json", evidence)
    (tmp_path / "attempts").mkdir()
    (tmp_path / "resaves").mkdir()
    monkeypatch.setattr(harness, "_verify", _proof)
    harness.prepare()
    return tmp_path


def _files(workspace: Path, slot: str = "W1") -> None:
    (workspace / "resaves" / f"{slot}.pdf").write_bytes(b"SYNTHETIC FIRST VIEWER SAVE")
    (workspace / "resaves" / f"{slot}-resaved.pdf").write_bytes(b"SYNTHETIC SECOND VIEWER SAVE")


def _input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    values = iter(answers)
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr("builtins.input", answer)
    return prompts


def _record(workspace: Path, monkeypatch: pytest.MonkeyPatch, slot: str = "W1") -> dict[str, Any]:
    _files(workspace, slot)
    prompts = _input(monkeypatch, [""] * 9)
    harness._record(slot)
    assert not any(
        "numeric" in prompt.casefold() or "version" in prompt.casefold() for prompt in prompts
    )
    return harness._read(workspace / "attempts" / f"{slot}-attempt-01.json")


def test_guidance_upgrade_preserves_original_artifacts_evidence_and_attempts(
    workspace: Path,
) -> None:
    paths = [
        workspace / name
        for name in ("packet.pdf", "reference.pdf", "unsafe-cover.pdf", "evidence.json")
    ]
    before = {path: path.read_bytes() for path in paths}
    assert list((workspace / "guidance").glob("*.json"))
    harness.prepare()
    assert {path: path.read_bytes() for path in paths} == before
    assert len(list((workspace / "guidance").glob("*.json"))) == 1


def test_recording_uses_confirmed_versions_and_does_not_run_native_checks(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        harness, "_verify", lambda *_args: pytest.fail("native checks during human recording")
    )
    attempt = _record(workspace, monkeypatch)
    assert attempt["result"] == "OBSERVED"
    assert attempt["reader_version"] == "2026.002"
    assert (
        attempt["environment_source"]["source_attempt_sha256"]
        == "114b83241d83c0a5c836b2dfccd1631debb97dd3270cf8baba40e3b4bf024c9a"
    )
    assert attempt["environment_basis"] == "maintainer_confirmed_unchanged_from_last_accepted_test"
    assert set(attempt["canary_ids"]) == {
        canary.canary_id for case in harness._ACCEPTED for canary in case.canaries
    }
    assert all(token not in str(attempt) for token in harness._TOKENS)
    assert all(attempt["checks"].values())


def test_typo_retries_and_later_resumes_without_repeating_completed_steps(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _files(workspace)
    prompts = _input(monkeypatch, ["typo", "", "", "L"])
    harness._record("W1")
    assert len(prompts) == 4
    progress = harness._read(workspace / "progress/W1.json")
    assert progress["steps"][:2] == ["passed", "passed"]
    assert progress["cursor"] == 2
    assert not list((workspace / "attempts").glob("*.json"))
    capsys.readouterr()
    prompts = _input(monkeypatch, [""] * 7)
    harness._record("W1")
    output = capsys.readouterr().out
    assert len(prompts) == 7 and "Step 3 of 9" in output and "Step 1 of 9" not in output
    assert harness._read(workspace / "attempts/W1-attempt-01.json")["result"] == "OBSERVED"


def test_problem_is_preserved_and_a_retry_records_a_new_attempt(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _input(monkeypatch, ["P"])
    harness._record("W1")
    failed = (workspace / "attempts/W1-attempt-01.json").read_bytes()
    _files(workspace)
    _input(monkeypatch, [""] * 9)
    harness._record("W1")
    assert (workspace / "attempts/W1-attempt-01.json").read_bytes() == failed
    assert harness._read(workspace / "attempts/W1-attempt-02.json")["result"] == "OBSERVED"


def test_copy_actions_are_explicit_and_unavailable_during_mask_copy(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied: list[str] = []
    folders: list[bool] = []
    monkeypatch.setattr(harness, "_clipboard_write", copied.append)
    monkeypatch.setattr(harness, "_open_folder", lambda: folders.append(True))
    _files(workspace)
    _input(monkeypatch, ["C", "F", "", "C", "K", "", "C", "L"])
    harness._record("W1")
    assert copied == ["PWSRED", "PWSRED", "PWSKEEP"]
    assert folders == [True]
    assert harness._read(workspace / "progress/W1.json")["cursor"] == 2
    for step in (2, 5, 8):
        card, copy_text, positive = harness._card(step, "W1")
        assert copy_text is None and positive is False and "clipboard" in card
        assert ("packet.pdf" if step == 2 else "W1.pdf" if step == 5 else "W1-resaved.pdf") in card


def test_save_path_copy_and_missing_save_recovery_keep_the_current_step(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied: list[str] = []
    monkeypatch.setattr(harness, "_clipboard_write", copied.append)
    _input(monkeypatch, ["", "", "", "C", "", "L"])
    harness._record("W1")
    progress = harness._read(workspace / "progress/W1.json")
    assert progress["cursor"] == 3 and progress["steps"][:3] == ["passed"] * 3
    assert copied == [str(workspace / "resaves/W1.pdf")]
    assert not list((workspace / "attempts").glob("*.json"))


@pytest.mark.parametrize("changed, first_pending", [("W1.pdf", 3), ("W1-resaved.pdf", 6)])
def test_copy_changes_invalidate_only_dependent_reviews(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
    first_pending: int,
) -> None:
    _files(workspace)
    _input(monkeypatch, [""] * 8 + ["L"])
    harness._record("W1")
    (workspace / "resaves" / changed).write_bytes(b"CHANGED SYNTHETIC SAVED COPY")
    evidence, binding, environment, _digest = harness._session()
    progress = harness._draft("W1", evidence, binding, environment)
    assert progress["steps"][:first_pending] == ["passed"] * first_pending
    assert progress["steps"][first_pending:] == ["pending"] * (9 - first_pending)


def test_fast_status_uses_fresh_bound_receipts_and_verify_does_each_copy_once(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _record(workspace, monkeypatch)
    calls: list[bytes] = []

    def verify(
        content: bytes, reference: bytes, evidence: dict[str, Any], scratch: Path
    ) -> dict[str, object]:
        calls.append(content)
        return _proof(content, reference, evidence, scratch)

    monkeypatch.setattr(harness, "_verify", verify)
    harness.status()
    assert calls == []
    assert "RECORDED — automated checks pending" in capsys.readouterr().out
    harness.verify()
    assert len(calls) == 2
    assert "W1): PASS" in capsys.readouterr().out
    harness.verify()
    harness.status()
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert "independent" not in output or "remain" in output
    assert "release_ready=True" not in output


@pytest.mark.parametrize("changed", ["W1.pdf", "W1-resaved.pdf"])
def test_changed_copies_cannot_reuse_a_previous_pass(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed: str,
) -> None:
    _record(workspace, monkeypatch)
    harness.verify()
    capsys.readouterr()
    (workspace / "resaves" / changed).write_bytes(b"CHANGED COPY")
    monkeypatch.setattr(harness, "_verify", lambda *_args: pytest.fail("status parsed PDF"))
    harness.status()
    assert "W1): PASS" not in capsys.readouterr().out


def test_changed_copy_during_receipt_lookup_cannot_be_reported_as_pass(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _record(workspace, monkeypatch)
    harness.verify()
    capsys.readouterr()
    original = harness._receipt
    calls = 0

    def mutate(*args: Any, **kwargs: Any) -> bool | None:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            (workspace / "resaves/W1.pdf").write_bytes(b"CHANGED DURING STATUS")
        return result

    monkeypatch.setattr(harness, "_receipt", mutate)
    harness.status()
    assert "W1): PASS" not in capsys.readouterr().out


def test_legacy_completed_observation_is_preserved_without_repeating_human_work(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _files(workspace)
    evidence, binding, environment, _hash = harness._session()
    known = environment["slots"]["W1"]
    attempt = {
        "schema_version": 1,
        "operator": "human",
        "slot": "W1",
        "result": "PASS",
        "evidence_sha256": binding,
        "reader_version": known["reader_version"],
        "os_version": known["os_version"],
        "checks": dict.fromkeys(harness.CHECKS, True),
        "saved_sha256": sha256((workspace / "resaves/W1.pdf").read_bytes()).hexdigest(),
        "resaved_sha256": sha256((workspace / "resaves/W1-resaved.pdf").read_bytes()).hexdigest(),
    }
    path = workspace / "attempts/W1-attempt-01.json"
    harness._write(path, attempt)
    before = path.read_bytes()
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("legacy observations repeated")
    )
    harness._record("W1")
    harness.prepare()
    harness.verify()
    assert path.read_bytes() == before
    assert len(list((workspace / "attempts").glob("*.json"))) == 1
    assert harness._observation("W1", evidence, binding, environment) is not None


@pytest.mark.parametrize("change", ["processing", "packet", "reference", "pipeline", "probe-plan"])
def test_guidance_upgrade_fails_closed_on_meaningful_changes(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    if change == "processing":
        monkeypatch.setattr(
            harness,
            "_provenance",
            lambda: {
                "synthetic-processing": "b" * 64,
                harness.HELPER: harness._LEGACY_HELPER,
            },
        )
    elif change in {"packet", "reference"}:
        (workspace / f"{change}.pdf").write_bytes(b"CHANGED ARTIFACT")
    elif change == "pipeline":
        monkeypatch.setattr(harness, "_pipeline", lambda: "b" * 64)
    else:
        monkeypatch.setattr(harness, "_TOKENS", ("DIFFERENT-TARGET",))
    with pytest.raises(ValueError):
        harness.prepare()


def test_one_environment_change_does_not_invalidate_other_reader_observations(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record(workspace, monkeypatch, "W1")
    metadata = harness._read(harness.ENVIRONMENT)
    metadata["slots"]["W2"]["reader_version"] = "999.1"
    harness.ENVIRONMENT.write_text(json.dumps(metadata), encoding="utf-8")
    harness.prepare()
    evidence, binding, environment, _hash = harness._session()
    assert harness._observation("W1", evidence, binding, environment) is not None
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("unaffected reader repeated"))
    harness._record("W1")


@pytest.mark.parametrize(
    "change", ["schema", "missing-slot", "bad-version", "source-path", "unknown-field"]
)
def test_environment_metadata_is_closed_and_bounded(
    workspace: Path,
    change: str,
) -> None:
    value = harness._read(harness.ENVIRONMENT)
    if change == "schema":
        value["schema_version"] = 2
    elif change == "missing-slot":
        value["slots"].pop("A1")
    elif change == "bad-version":
        value["slots"]["W1"]["reader_version"] = "private account text"
    elif change == "source-path":
        value["slots"]["W1"]["source_attempt"] = "../private.json"
    else:
        value["extra"] = True
    harness.ENVIRONMENT.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="PWS0606_ENVIRONMENT_INVALID"):
        harness._environment()


@pytest.mark.parametrize("change", ["future", "unknown", "method", "candidate", "claim"])
def test_verification_receipts_reject_forged_or_unknown_results(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    _record(workspace, monkeypatch)
    harness.verify()
    path = sorted((workspace / "verification").glob("W1-saved-*.json"))[-1]
    value = harness._read(path)
    if change == "future":
        value["schema_version"] = 99
    elif change == "unknown":
        value["extra"] = True
    elif change == "method":
        value["proof"]["report"]["methods"][0]["method"] = "unknown"
    elif change == "candidate":
        value["proof"]["report"]["candidate_sha256"] = "f" * 64
    else:
        value["proof"]["release_ready"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        harness.status()


def test_numbered_menu_and_legacy_slot_aliases_need_no_numeric_version_input(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots: list[str] = []
    monkeypatch.setattr(harness, "_record", slots.append)
    _input(monkeypatch, ["1", "W2", "3", "W4", "5", "nonsense", "Q"])
    assert harness.main(["guided"]) == 0
    assert slots == ["W1", "W2", "W3", "W4", "A1"]


def test_shared_query_guard_rejects_a_future_nonmatching_canary(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert harness._query() == "PWSRED"
    monkeypatch.setattr(harness, "_TOKENS", (*harness._TOKENS, "UNRELATED"))
    with pytest.raises(ValueError, match="PWS0606_QUERY_PLAN_CHANGED"):
        harness._query()


def test_step_cards_keep_search_priming_and_mask_copy_in_separate_stages(workspace: Path) -> None:
    for stage in (1, 4, 7):
        text, query, positive = harness._card(stage, "W1")
        assert text.index("FIRST search PWSRED") < text.index("THEN search PWSKEEP")
        assert "Whole-word" in text and "Copy actual" not in text
        assert query == "PWSRED" and positive
    for stage in (2, 5, 8):
        text, query, positive = harness._card(stage, "W1")
        assert "all 16 pages" in text and "comfortable" in text
        assert query is None and not positive
        assert "200%" not in text
        assert text.index("Re-prime") < text.index("Attempt mask selection")
        assert "FROM THIS PDF" in text


def test_optional_actions_failure_is_recoverable_without_recording_a_failure(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_text: str) -> None:
        raise OSError("synthetic unavailable clipboard")

    monkeypatch.setattr(harness, "_clipboard_write", unavailable)
    _input(monkeypatch, ["C", "L"])
    harness._record("W1")
    assert not list((workspace / "attempts").glob("*.json"))
    assert harness._read(workspace / "progress/W1.json")["cursor"] == 0


def test_full_guided_run_accepts_files_only_at_actual_save_checkpoints(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages: list[int] = []

    def answer(_prompt: str) -> str:
        stage = len(stages)
        stages.append(stage)
        if stage in {3, 6}:
            name = "W1.pdf" if stage == 3 else "W1-resaved.pdf"
            (workspace / "resaves" / name).write_bytes(f"ACTUAL SYNTHETIC SAVE {stage}".encode())
        return ""

    monkeypatch.setattr("builtins.input", answer)
    harness._record("W1")
    assert stages == list(range(9))
    assert harness._read(workspace / "attempts/W1-attempt-01.json")["result"] == "OBSERVED"
    assert len(list((workspace / "preserved-saves").glob("*.pdf"))) == 2


def test_back_and_interrupted_copy_review_resume_with_benign_priming(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _files(workspace)
    _input(monkeypatch, ["", "", "B", "", "L"])
    harness._record("W1")
    assert harness._read(workspace / "progress/W1.json")["cursor"] == 2
    capsys.readouterr()
    _input(monkeypatch, ["L"])
    harness._record("W1")
    text = capsys.readouterr().out
    assert "Keep packet.pdf open" in text and "Re-prime" in text
    assert "Step 1 of 9" not in text


def test_changed_copy_draft_and_prior_saves_are_preserved_before_replacement(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _files(workspace)
    original = (workspace / "resaves/W1.pdf").read_bytes()
    _input(monkeypatch, [""] * 8 + ["L"])
    harness._record("W1")
    prior_draft = harness._read(workspace / "progress/W1.json")
    (workspace / "resaves/W1.pdf").write_bytes(b"NEW VIEWER FILE")
    _input(monkeypatch, ["L"])
    harness._record("W1")
    assert harness._read(workspace / "progress-history/W1-progress-01.json") == prior_draft
    assert harness._read(workspace / "progress/W1.json")["cursor"] == 3
    assert (
        workspace / "preserved-saves" / (sha256(original).hexdigest() + ".pdf")
    ).read_bytes() == original
    assert (workspace / "resaves/W1.pdf").read_bytes() == b"NEW VIEWER FILE"


@pytest.mark.parametrize("bound", ["count", "bytes"])
def test_preserved_saves_have_finite_count_and_central_storage_bounds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    _files(workspace)
    first = workspace / "resaves/W1.pdf"
    second = workspace / "resaves/W1-resaved.pdf"
    harness._preserve_copy(first)
    before = first.read_bytes(), second.read_bytes()
    if bound == "count":
        monkeypatch.setattr(harness, "MAX_RECORDS", 1)
    else:
        monkeypatch.setattr(
            harness,
            "CONSTRAINED_RESOURCE_POLICY_V1",
            replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_temporary_storage_bytes=1),
        )
    with pytest.raises(ValueError, match="PWS0606_PRESERVATION_LIMIT"):
        harness._preserve_copy(second)
    assert (first.read_bytes(), second.read_bytes()) == before
    assert len(list((workspace / "preserved-saves").glob("*.pdf"))) == 1


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_archive_disk_full_cannot_overwrite_live_file_draft_or_attest_pass(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool
) -> None:
    _input(monkeypatch, ["", "", "", "L"])
    harness._record("W1")
    progress = (workspace / "progress/W1.json").read_bytes()
    _files(workspace)
    live = (workspace / "resaves/W1.pdf").read_bytes()
    original_open, original_unlink = Path.open, Path.unlink

    class FullDisk:
        def __init__(self, file: Any) -> None:
            self.file = file

        def __enter__(self) -> FullDisk:
            return self

        def __exit__(self, *_args: Any) -> None:
            self.file.close()

        def write(self, block: bytes) -> None:
            self.file.write(block[:3])
            raise OSError("synthetic disk full")

    def opening(path: Path, *args: Any, **kwargs: Any) -> Any:
        file = original_open(path, *args, **kwargs)
        return FullDisk(file) if path.parent.name == "preserved-saves" and args == ("xb",) else file

    def unlinking(path: Path, *args: Any, **kwargs: Any) -> None:
        if cleanup_fails and path.parent.name == "preserved-saves":
            raise OSError("synthetic cleanup unavailable")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opening)
    monkeypatch.setattr(Path, "unlink", unlinking)
    with pytest.raises(OSError):
        harness._record("W1")
    assert (workspace / "resaves/W1.pdf").read_bytes() == live
    assert (workspace / "progress/W1.json").read_bytes() == progress
    assert not list((workspace / "attempts").glob("*.json"))
    assert len(list((workspace / "preserved-saves").glob("*.pdf"))) == int(cleanup_fails)


def test_presentation_only_upgrade_appends_fresh_proof_without_rewriting_prior_evidence(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(workspace, monkeypatch)
    originals = {path: path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    original_root = harness.ROOT
    replacement_root = workspace / "guidance-source"
    helper = replacement_root / harness.HELPER
    helper.parent.mkdir(parents=True)
    helper.write_bytes((original_root / harness.HELPER).read_bytes() + b"\n# guidance only\n")
    monkeypatch.setattr(harness, "ROOT", replacement_root)
    calls: list[bool] = []

    def verify(*args: Any) -> dict[str, object]:
        calls.append(True)
        return _proof(*args)

    monkeypatch.setattr(harness, "_verify", verify)
    harness.prepare()
    assert calls == [True]
    assert len(list((workspace / "guidance").glob("*.json"))) == 2
    assert all(path.read_bytes() == content for path, content in originals.items())
