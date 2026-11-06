"""NVDA evidence is closed, resumable, byte-bound and separate from a task pass."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import pws0702_screen_reader as recorder


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    files = (
        "src/paperworkshell/ui/window.py",
        "src/paperworkshell/domain/resource_policy.py",
        "pyproject.toml",
        "uv.lock",
        "tools/pws0702_screen_reader.py",
        *recorder._INPUTS,
    )
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"SYNTHETIC_BOUND_BYTES\n")
    return tmp_path


@pytest.fixture
def environment() -> dict[str, object]:
    return {
        "os": "Windows",
        "nvda_file_version": "synthetic_version",
        "actual_nvda_speech_observed": False,
    }


def test_prepare_and_status_cannot_infer_a_pass_or_speech(
    project_root: Path, environment: dict[str, object]
) -> None:
    receipt = recorder.prepare(project_root, "v1", environment)
    result = recorder.status(project_root, "v1", environment)
    assert result["status"] == "incomplete"
    assert result["record_count"] == 0
    assert result["pending"] == list(recorder.PHASES)
    assert result["task_completion_inferred"] is False
    assert result["human_assessment_claimed"] is False
    assert receipt["processing_success_inferred"] is False
    assert receipt["bindings"]["environment"]["actual_nvda_speech_observed"] is False  # type: ignore[index]
    assert str(project_root) not in json.dumps(receipt)
    assert recorder.prepare(project_root, "v1", environment) == receipt


def test_record_checkpoints_immediately_retries_keep_problem_and_resume(
    project_root: Path, environment: dict[str, object]
) -> None:
    recorder.prepare(project_root, "v1", environment)
    recorder.record(
        project_root, "v1", environment, phase="case-pages", outcome="problem", input_actions=3
    )
    previous = project_root / "output/pws0702-screen-reader/v1/observation-0001.json"
    original = previous.read_bytes()
    result = recorder.record(
        project_root, "v1", environment, phase="case-pages", outcome="observed", input_actions=2
    )
    assert previous.read_bytes() == original
    assert result["problems_retained"] == 1
    assert result["record_count"] == 2
    assert "case-pages" not in result["pending"]  # type: ignore[operator]
    assert recorder.status(project_root, "v1", environment) == result
    result = recorder.record(
        project_root, "v1", environment, phase="fill-protect", outcome="later", input_actions=0
    )
    assert "fill-protect" in result["pending"]  # type: ignore[operator]


def test_complete_observations_still_require_separate_verification(
    project_root: Path, environment: dict[str, object]
) -> None:
    recorder.prepare(project_root, "v1", environment)
    for phase in recorder.PHASES:
        result = recorder.record(
            project_root, "v1", environment, phase=phase, outcome="observed", input_actions=1
        )
    assert result["status"] == "observations_recorded"
    assert result["task_completion_inferred"] is False
    assert result["automated_verification"] == "separate_current_receipts_required"


@pytest.mark.parametrize("mutation", ["source", "added_source", "fixture", "helper", "environment"])
def test_changed_bindings_fail_without_replacing_existing_evidence(
    project_root: Path, environment: dict[str, object], mutation: str
) -> None:
    recorder.prepare(project_root, "v1", environment)
    before = (project_root / "output/pws0702-screen-reader/v1/attempt.json").read_bytes()
    if mutation == "environment":
        environment["nvda_file_version"] = "changed"
    else:
        relative = {
            "source": "src/paperworkshell/ui/window.py",
            "added_source": "src/paperworkshell/ui/new.py",
            "fixture": recorder._INPUTS[0],
            "helper": "tools/pws0702_screen_reader.py",
        }[mutation]
        (project_root / relative).write_bytes(b"PRIVATE_CHANGED_BYTES")
    with pytest.raises(recorder.ScreenReaderEvidenceError) as error:
        recorder.status(project_root, "v1", environment)
    assert "PRIVATE_CHANGED_BYTES" not in str(error.value)
    assert (project_root / "output/pws0702-screen-reader/v1/attempt.json").read_bytes() == before
    # A separate epoch preserves earlier evidence rather than rewriting it.
    recorder.prepare(project_root, "v2", environment)
    assert (project_root / "output/pws0702-screen-reader/v1/attempt.json").read_bytes() == before


@pytest.mark.parametrize("attempt", ["../escape", "UPPER", "a/b", "", "c:\\private", "x" * 41])
def test_unsafe_attempts_fail_before_writing(
    project_root: Path, environment: dict[str, object], attempt: str
) -> None:
    with pytest.raises(recorder.ScreenReaderEvidenceError):
        recorder.prepare(project_root, attempt, environment)
    assert not (project_root / "output").exists()


def test_artifact_hash_binding_detects_changed_export_without_reading_content_into_evidence(
    project_root: Path, environment: dict[str, object]
) -> None:
    artifact = project_root / "output/pws0702-synthetic/verified.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"SYNTHETIC_DOCUMENT_CONTENT")
    recorder.prepare(project_root, "v1", environment)
    result = recorder.record(
        project_root,
        "v1",
        environment,
        phase="export-result",
        outcome="observed",
        input_actions=4,
        artifacts=("output/pws0702-synthetic/verified.pdf",),
    )
    assert result["record_count"] == 1
    record_path = project_root / "output/pws0702-screen-reader/v1/observation-0001.json"
    assert b"SYNTHETIC_DOCUMENT_CONTENT" not in record_path.read_bytes()
    artifact.write_bytes(b"CHANGED")
    with pytest.raises(recorder.ScreenReaderEvidenceError):
        recorder.status(project_root, "v1", environment)


@pytest.mark.parametrize(
    "artifact",
    ["../private", "src/private", "output/other/doc.pdf", "/abs", "output/pws0702/../private"],
)
def test_unapproved_artifact_paths_are_refused(
    project_root: Path, environment: dict[str, object], artifact: str
) -> None:
    recorder.prepare(project_root, "v1", environment)
    with pytest.raises(recorder.ScreenReaderEvidenceError):
        recorder.record(
            project_root,
            "v1",
            environment,
            phase="export-result",
            outcome="observed",
            input_actions=1,
            artifacts=(artifact,),
        )
    assert recorder.status(project_root, "v1", environment)["record_count"] == 0


def test_reparse_parent_is_rejected_before_creation(
    project_root: Path, environment: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.lstat

    def lstat(path: Path) -> Any:
        if path == project_root / "output":
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(recorder.ScreenReaderEvidenceError):
        recorder.prepare(project_root, "v1", environment)
    assert not (project_root / "output").exists()


def test_tampered_or_truncated_observations_remain_unknown(
    project_root: Path, environment: dict[str, object]
) -> None:
    recorder.prepare(project_root, "v1", environment)
    recorder.record(
        project_root, "v1", environment, phase="case-pages", outcome="observed", input_actions=1
    )
    path = project_root / "output/pws0702-screen-reader/v1/observation-0001.json"
    row = json.loads(path.read_bytes())
    row["previous_sha256"] = "0" * 64
    path.write_bytes(recorder._json_bytes(row))
    with pytest.raises(recorder.ScreenReaderEvidenceError):
        recorder.status(project_root, "v1", environment)


def test_interrupted_trailing_stage_keeps_valid_observations_and_can_resume(
    project_root: Path, environment: dict[str, object]
) -> None:
    recorder.prepare(project_root, "v1", environment)
    recorder.record(
        project_root, "v1", environment, phase="case-pages", outcome="observed", input_actions=1
    )
    destination = project_root / "output/pws0702-screen-reader/v1"
    original = (destination / "observation-0001.json").read_bytes()
    pending = destination / f".observation-0002.json.{'a' * 32}.pending"
    pending.write_bytes(b'{"partial')
    assert recorder.status(project_root, "v1", environment)["record_count"] == 1
    result = recorder.record(
        project_root, "v1", environment, phase="fill-protect", outcome="later", input_actions=0
    )
    assert result["record_count"] == 2
    assert (destination / "observation-0001.json").read_bytes() == original
    assert pending.read_bytes() == b'{"partial'


def test_exclusive_publication_never_overwrites_an_observation(project_root: Path) -> None:
    target = project_root / "observation-0001.json"
    target.write_bytes(b"ORIGINAL")
    with pytest.raises(FileExistsError):
        recorder._write_once(project_root, target, b"REPLACEMENT")
    assert target.read_bytes() == b"ORIGINAL"


def test_cli_is_path_free_and_invalid_input_retains_progress(
    project_root: Path,
    environment: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(recorder, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(recorder, "capture_environment", lambda: environment)
    assert recorder.main(("prepare",)) == 0
    assert "incomplete" in capsys.readouterr().out
    assert recorder.main(("record", "--phase", "case-pages", "--outcome", "observed")) == 1
    assert "PWS0702_EVIDENCE_UNAVAILABLE" in capsys.readouterr().err
    assert (
        recorder.main(
            ("record", "--phase", "case-pages", "--outcome", "observed", "--input-actions", "2")
        )
        == 0
    )
    capsys.readouterr()
    assert recorder.main(("--PRIVATE_ARGUMENT",)) == 1
    captured = capsys.readouterr()
    assert "PRIVATE_ARGUMENT" not in captured.err
    assert str(project_root) not in captured.err
    assert recorder.status(project_root, "v1", environment)["record_count"] == 1
