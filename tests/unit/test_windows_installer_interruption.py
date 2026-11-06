"""Interruption observations cannot become passes through stale or incomplete evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tools import qualify_windows_installer as qualifier
from tools import windows_signing
from tools.release_evidence import load_json


def _session(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    work = root / "output/pws0802/qualification/synthetic"
    work.mkdir(parents=True)
    installation = root / "synthetic-product"
    installation.mkdir()
    candidate: dict[str, Any] = {
        "installer": "synthetic.exe",
        "build_receipt": "synthetic.json",
        "installer_sha256": "1" * 64,
        "build_receipt_sha256": "2" * 64,
    }
    state = {
        "schema_version": 1,
        "scope": "HOST_NOT_CLEAN",
        "candidate": candidate,
        "phase": "installed",
        "owned_installation": True,
        "initial_case_entry_count": 10,
        "observations": [],
        "installed_files": {},
    }
    digest = qualifier._save(work, state)
    monkeypatch.setattr(qualifier, "inspect_candidate", lambda *_a, **_kw: candidate)
    monkeypatch.setattr(qualifier, "_host_paths", lambda: (installation, root / "DO-NOT-READ"))
    monkeypatch.setattr(qualifier, "case_entry_count", lambda _p: 10)
    monkeypatch.setattr(qualifier, "_token_elevated", lambda: False)
    monkeypatch.setattr(qualifier, "_check_owned", lambda *_a: None)
    return work, digest


def test_missing_cutpoint_is_not_a_pass_and_recovery_is_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, digest = _session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        qualifier,
        "_interrupt_owned_repair",
        lambda *_a, **_kw: {"cutpoint_observed": False, "status": "NOT_OBSERVED"},
    )
    state = qualifier.run_action(
        "interrupt-repair", work, expected_session_sha256=digest, root=tmp_path
    )
    assert state["phase"] == "interrupted"
    assert state["observations"][-1]["status"] == "NOT_OBSERVED"
    assert state["observations"][-1]["case_contents"] == "NOT_INSPECTED"
    assert not (work / "operation.lock").exists()
    assert load_json(work / qualifier.SESSION)["interruption"]["cutpoint_observed"] is False


def test_observer_failure_retains_durable_interrupted_authority_for_explicit_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, digest = _session(tmp_path, monkeypatch)

    def failed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        state = load_json(work / qualifier.SESSION)
        assert state["phase"] == "interrupted"
        assert state["pending_action"] == "interrupt-repair"
        raise qualifier.QualificationError("QUALIFIER_BARRIER_NOT_BLOCKING")

    monkeypatch.setattr(qualifier, "_interrupt_owned_repair", failed)
    with pytest.raises(qualifier.QualificationError, match="NOT_BLOCKING"):
        qualifier.run_action(
            "interrupt-repair", work, expected_session_sha256=digest, root=tmp_path
        )
    retained = qualifier.run_action("status", work, root=tmp_path)
    assert retained["phase"] == "interrupted"
    assert retained["observations"][-1]["status"] == "failed"
    with pytest.raises(qualifier.QualificationError, match="PREVIOUS_ACTION_INCOMPLETE"):
        qualifier.run_action(
            "install", work, expected_session_sha256=retained["session_sha256"], root=tmp_path
        )
    retained = qualifier.run_action("status", work, root=tmp_path)
    monkeypatch.setattr(qualifier, "_recover_owned_repair", lambda *_a, **_kw: {"verified": {}})
    recovered = qualifier.run_action(
        "recover-repair", work, expected_session_sha256=retained["session_sha256"], root=tmp_path
    )
    assert recovered["phase"] == "installed"
    assert recovered["installed_files"] == {"verified": {}}
    assert recovered["observations"][0]["status"] == "failed"
    assert recovered["observations"][-1]["action"] == "recover-repair"


@pytest.mark.parametrize("action", ["interrupt-repair", "recover-repair"])
def test_interruption_actions_need_independently_retained_session_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    work, _digest = _session(tmp_path, monkeypatch)
    before = (work / qualifier.SESSION).read_bytes()
    with pytest.raises(qualifier.QualificationError, match="ANCHOR_REQUIRED"):
        qualifier.run_action(action, work, root=tmp_path)
    assert (work / qualifier.SESSION).read_bytes() == before


def test_accepted_asynchronous_job_termination_is_issued_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def terminate(job: int, _code: int) -> bool:
        calls.append(job)
        return True

    process = object.__new__(qualifier._InterruptionProcess)
    process.job, process.terminated = 123, False
    process.kernel = SimpleNamespace(TerminateJobObject=terminate)
    waits: list[float] = []
    process.process = cast(
        subprocess.Popen[bytes], SimpleNamespace(wait=lambda timeout: waits.append(timeout))
    )
    monkeypatch.setattr(windows_signing, "_job_active_process_count", lambda _job: 0)
    process.stop()
    process.stop()
    assert calls == [123]
    assert process.terminated
    assert len(waits) == 2


@pytest.mark.parametrize("listing", ["missing", "reversed", "duplicate"])
def test_wrong_generated_copy_order_refuses_before_any_product_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listing: str
) -> None:
    notice = "\\" + qualifier._INTERRUPTION_NOTICE.replace("/", "\\") + '";'
    barrier = "\\" + qualifier._INTERRUPTION_BARRIER.replace("/", "\\") + '";'
    contents = {
        "missing": notice,
        "reversed": barrier + notice,
        "duplicate": notice + notice + barrier,
    }[listing]
    (tmp_path / "files.iss.inc").write_text(contents, encoding="utf-8")
    candidate = {
        "files": [
            {"path": qualifier._INTERRUPTION_NOTICE},
            {"path": qualifier._INTERRUPTION_BARRIER},
        ],
        "build_receipt": "receipt.json",
    }
    monkeypatch.setattr(
        qualifier, "_pin_file", lambda *_a, **_kw: pytest.fail("No file may be opened")
    )
    with pytest.raises(qualifier.QualificationError, match="FILE_ORDER"):
        qualifier._interrupt_owned_repair(
            tmp_path / "untouched-product",
            {"candidate": candidate},
            tmp_path / "step",
            root=tmp_path,
        )


def test_unknown_residue_stops_recovery_without_running_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = tmp_path / "owned-product"
    installation.mkdir()
    (installation / "unknown.tmp").write_bytes(b"must remain")
    state = {
        "candidate": {"files": [{"path": qualifier._INTERRUPTION_BARRIER}]},
        "installed_files": {"unins000.exe": {"sha256": "0" * 64}},
    }
    monkeypatch.setattr(
        qualifier,
        "_pin_file",
        lambda *_a, **_kw: pytest.fail("Unknown file must not be acquired for deletion"),
    )
    monkeypatch.setattr(qualifier, "_execute", lambda *_a, **_kw: pytest.fail("Recovery must stop"))
    with pytest.raises(qualifier.QualificationError, match="UNKNOWN_RESIDUE"):
        qualifier._recover_owned_repair(installation, state, tmp_path / "step", root=tmp_path)
    assert (installation / "unknown.tmp").read_bytes() == b"must remain"


def test_interruption_receipts_stay_inside_strict_integer_json(tmp_path: Path) -> None:
    report = {"scope": "HOST_NOT_CLEAN", "creation_filetime": 123, "cutpoint_observed": False}
    qualifier._write_interruption_report(tmp_path, report)
    assert load_json(tmp_path / "interruption-result.json") == report
    assert json.loads((tmp_path / "interruption-result.json").read_bytes()) == report
