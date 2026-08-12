from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest

from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    RESOURCE_PROFILE_ENVIRONMENT_VARIABLE,
)
from paperworkshell.storage._windows import WindowsHandle, open_handle
from tools.m1_vertical_slice_benchmark import (
    FailedRunEvidence,
    FailureStage,
    M1BenchmarkError,
    M1BenchmarkReport,
    M1Phase,
    PhaseMeasurement,
    PhaseOutcome,
    VerticalSliceRunStatus,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
    _LiveM1ReportWriter,
)
from tools.m1_vertical_slice_live_runner import (
    _QPDF_BIN_FILE_SHA256,
    LIVE_CHILD_DERIVATIVE_FILENAME,
    LIVE_CHILD_RESULT_FILENAME,
    LIVE_DERIVATIVE_FILENAME,
    LIVE_ENV_HMAC_KEY,
    LIVE_ENV_NAMES,
    LIVE_ENV_PROTOCOL,
    LIVE_ENV_RUN_ID,
    LIVE_ENV_STAGE,
    LIVE_PROTOCOL_VERSION,
    LIVE_REPORT_FILENAME,
    LIVE_REQUIRED_TEST_NODES,
    MAX_CHILD_RESULT_BYTES,
    PROJECT_ROOT,
    _child_environment,
    _cleanup_run_directory,
    _complete_process_tree_rss,
    _decode_child_result,
    _descendant_process_ids,
    _failure_stage_for_code,
    _git_identity,
    _open_qpdf_distribution_authority,
    _process_snapshot_enumeration_completed,
    _publish_derivative,
    _publish_fixed_bundle,
    _qpdf_bin_names,
    _read_authenticated_file,
    _require_fixed_outputs_absent,
    _require_live_qpdf_trusted_root,
    _windows_process_tree_rss_bytes,
    emit_live_child_artifacts,
    production_qpdf_version_identity,
    run_live_m1_vertical_slice,
)

_RUN_ID = UUID("50000000-0000-4000-8000-000000000114")
_KEY = bytes(range(32))


class _FakeDerivativeHandle:
    def __init__(self) -> None:
        self.closed = False
        self.deleted = False

    def close(self) -> None:
        self.closed = True

    def mark_for_deletion(self) -> None:
        self.deleted = True


class _PostcommitInterruptWriter:
    def __init__(self, *, committed: bool) -> None:
        self._directory_handle = cast(WindowsHandle, object())
        self._published = False
        self._committed = committed

    def _publish_completed_report(self, _report: M1BenchmarkReport) -> Path:
        self._published = self._committed
        raise KeyboardInterrupt


def test_live_runner_uses_enforced_constrained_policy() -> None:
    assert CONSTRAINED_RESOURCE_POLICY_V1.maximum_heavy_operation_concurrency == 1
    assert CONSTRAINED_RESOURCE_POLICY_V1.maximum_cache_bytes == 64 * 1024 * 1024
    assert CONSTRAINED_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes == 64 * 1024 * 1024
    assert CONSTRAINED_RESOURCE_POLICY_V1.canonical_process_tree_rss_bytes == 1_280 * 1024 * 1024
    assert len(LIVE_REQUIRED_TEST_NODES) == 14
    assert any("disk_full_during_import" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("slow_storage_import" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("slow_storage_read" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("slow_directory_scan" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("termination_during_render" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("capacity_preflight" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("mid_render_private_disk_full" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("mid_export_private_disk_full" in node for node in LIVE_REQUIRED_TEST_NODES)
    assert any("cancel_restart_and_shutdown" in node for node in LIVE_REQUIRED_TEST_NODES)


def test_failure_stage_distinguishes_setup_from_started_workload() -> None:
    assert _failure_stage_for_code("M1_LIVE_CONFIG_INVALID") is FailureStage.SETUP
    assert _failure_stage_for_code("M1_LIVE_WORK_INVALID") is FailureStage.SETUP
    assert _failure_stage_for_code("M1_LIVE_CHILD_FAILED") is FailureStage.WORKLOAD
    assert _failure_stage_for_code("M1_RESOURCE_MEASUREMENT_FAILED") is FailureStage.WORKLOAD


def test_live_runner_requests_a_sanitized_failed_report_on_workload_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    published: list[Mapping[str, object]] = []

    def fail_workload(**_kwargs: object) -> None:
        raise M1BenchmarkError("M1_LIVE_CHILD_FAILED", "The canonical child failed.")

    def publish_failure(**kwargs: object) -> Path:
        published.append(kwargs)
        return tmp_path / LIVE_REPORT_FILENAME

    monkeypatch.setattr("tools.m1_vertical_slice_live_runner.LIVE_OUTPUT_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._git_identity",
        lambda: ("a" * 40, "b" * 40),
    )
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._run_live_m1_vertical_slice",
        fail_workload,
    )
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._publish_failed_live_report",
        publish_failure,
    )

    with pytest.raises(M1BenchmarkError) as raised:
        run_live_m1_vertical_slice(work_directory=tmp_path / "work")

    assert raised.value.code == "M1_LIVE_CHILD_FAILED"
    assert len(published) == 1
    failed = cast(FailedRunEvidence, published[0]["failed_evidence"])
    assert failed.failure_stage is FailureStage.WORKLOAD
    assert published[0]["result_code"] == "M1_LIVE_CHILD_FAILED"


def _phases() -> tuple[PhaseMeasurement, ...]:
    return tuple(
        PhaseMeasurement(phase, PhaseOutcome.COMPLETED, index + 1)
        for index, phase in enumerate(M1Phase)
    )


def _workers() -> tuple[WorkerResourceMeasurement, ...]:
    return tuple(
        WorkerResourceMeasurement(operation, 100, 200, 25, 50) for operation in WorkerOperation
    )


def _workspaces() -> tuple[WorkspaceUsageMeasurement, ...]:
    return tuple(
        WorkspaceUsageMeasurement(checkpoint, 100, 20, 10, 3, 2)
        for checkpoint in WorkspaceCheckpoint
    )


def test_process_tree_snapshot_closure_includes_overlapping_worker_descendants() -> None:
    assert _descendant_process_ids(
        {100: 1, 110: 100, 120: 100, 111: 110, 121: 120, 999: 1},
        100,
    ) == frozenset({100, 110, 111, 120, 121})


def test_process_tree_snapshot_rejects_one_exited_or_unreadable_pid() -> None:
    expected = frozenset({100, 110, 120})

    assert _complete_process_tree_rss(expected, {100: 20, 110: 30}) is None
    assert _complete_process_tree_rss(expected, {100: 20, 110: 30, 120: 40}) == 90


@pytest.mark.parametrize("error_code", (0, 5, 87, None))
def test_process_snapshot_rejects_nonterminal_enumeration_errors(error_code: object) -> None:
    assert not _process_snapshot_enumeration_completed(error_code)
    assert _process_snapshot_enumeration_completed(18)


def test_process_tree_measurement_retries_the_whole_snapshot_after_a_pid_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = iter((None, None, 1234))
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._windows_process_tree_rss_sample",
        lambda _process: next(samples),
    )

    assert _windows_process_tree_rss_bytes(cast(Any, object())) == 1234


def test_process_tree_measurement_fails_when_every_whole_snapshot_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._windows_process_tree_rss_sample",
        lambda _process: None,
    )

    with pytest.raises(M1BenchmarkError) as raised:
        _windows_process_tree_rss_bytes(cast(Any, object()))

    assert raised.value.code == "M1_RESOURCE_MEASUREMENT_FAILED"


def _evidence(derivative: bytes) -> dict[str, object]:
    import hashlib

    return {
        "fixture_set_sha256": "a" * 64,
        "source_set_sha256": "b" * 64,
        "python_version": "3.14.6",
        "pikepdf_version": "10.10.0",
        "qpdf_version": "12.3.2",
        "pdfium_version": "145.0.7632.0",
        "qt_version": "6.11.1",
        "worker_config_sha256": "c" * 64,
        "renderer_profile_sha256": "d" * 64,
        "manifest_schema_version": 1,
        "worker_protocol_version": 1,
        "export_profile_version": 1,
        "workload_version": 1,
        "total_elapsed_ns": 100,
        "source_count": 2,
        "input_bytes": 200,
        "source_page_count": 3,
        "active_page_count": 2,
        "preview_count": 3,
        "output_count": 2,
        "output_page_count": 2,
        "output_bytes": len(derivative),
        "output_sha256": hashlib.sha256(derivative).hexdigest(),
        "semantic_fingerprint": "e" * 64,
        "deterministic_semantic_equal": True,
        "deterministic_output_equal": True,
        "verifier_codes": (
            "PDF_REOPENED",
            "STRUCTURE_CLEAN",
            "PAGE_COUNT_MATCHED",
            "UNENCRYPTED",
            "ALL_PAGES_RENDERED",
            "ORDER_ROTATION_MATCHED",
            "OUTPUT_COMMIT_REOPENED",
        ),
        "recovery_status": "current",
        "case_reopened": True,
        "reopened_page_count": 2,
        "reopened_sequence_equal": True,
        "privacy_scan_status": "passed",
        "privacy_canary_count": 5,
        "background_export_completed": True,
        "heartbeat_count": 10,
        "maximum_heartbeat_gap_ns": 50_000_000,
        "peak_cache_bytes": 20,
        "peak_temp_bytes": 10,
        "max_heavy_operation_concurrency": 1,
        "cancellation_acknowledgement_ms": 100,
        "workers_terminated": True,
        "worker_recycle_policy": "one-shot",
    }


def _set_handshake(monkeypatch: pytest.MonkeyPatch, stage: Path) -> None:
    monkeypatch.setenv(LIVE_ENV_PROTOCOL, str(LIVE_PROTOCOL_VERSION))
    monkeypatch.setenv(LIVE_ENV_RUN_ID, str(_RUN_ID))
    monkeypatch.setenv(LIVE_ENV_STAGE, str(stage))
    monkeypatch.setenv(LIVE_ENV_HMAC_KEY, _KEY.hex())


def test_ordinary_integration_run_emits_no_private_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (LIVE_ENV_PROTOCOL, LIVE_ENV_RUN_ID, LIVE_ENV_STAGE, LIVE_ENV_HMAC_KEY):
        monkeypatch.delenv(name, raising=False)
    derivative = tmp_path / "derivative.pdf"
    derivative.write_bytes(b"%PDF-1.4\n%%EOF\n")

    emit_live_child_artifacts(
        phases=_phases(),
        workers=_workers(),
        workspaces=_workspaces(),
        evidence=_evidence(derivative.read_bytes()),
        derivative_source=derivative,
    )

    assert set(tmp_path.iterdir()) == {derivative}


def test_complete_handshake_emits_canonical_authenticated_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / str(_RUN_ID)
    stage = run_root / "artifacts"
    stage.mkdir(parents=True)
    _set_handshake(monkeypatch, stage)
    derivative = tmp_path / "source.pdf"
    derivative_bytes = b"%PDF-1.4\n%%EOF\n"
    derivative.write_bytes(derivative_bytes)

    emit_live_child_artifacts(
        phases=_phases(),
        workers=_workers(),
        workspaces=_workspaces(),
        evidence=_evidence(derivative_bytes),
        derivative_source=derivative,
    )

    payload = (stage / LIVE_CHILD_RESULT_FILENAME).read_bytes()
    decoded = _decode_child_result(payload, run_id=_RUN_ID, key=_KEY)
    assert decoded.phases == _phases()
    assert decoded.workers == _workers()
    assert decoded.workspaces == _workspaces()
    assert (stage / LIVE_CHILD_DERIVATIVE_FILENAME).read_bytes() == derivative_bytes
    assert payload == (
        json.dumps(
            json.loads(payload),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def test_partial_handshake_fails_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_ENV_PROTOCOL, str(LIVE_PROTOCOL_VERSION))
    derivative = tmp_path / "source.pdf"
    derivative.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(M1BenchmarkError) as raised:
        emit_live_child_artifacts(
            phases=_phases(),
            workers=_workers(),
            workspaces=_workspaces(),
            evidence=_evidence(derivative.read_bytes()),
            derivative_source=derivative,
        )

    assert raised.value.code == "M1_LIVE_HANDSHAKE_INVALID"
    assert set(tmp_path.iterdir()) == {derivative}


def test_tampered_result_hmac_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / str(_RUN_ID)
    stage = run_root / "artifacts"
    stage.mkdir(parents=True)
    _set_handshake(monkeypatch, stage)
    derivative = tmp_path / "source.pdf"
    derivative_bytes = b"%PDF-1.4\n%%EOF\n"
    derivative.write_bytes(derivative_bytes)
    emit_live_child_artifacts(
        phases=_phases(),
        workers=_workers(),
        workspaces=_workspaces(),
        evidence=_evidence(derivative_bytes),
        derivative_source=derivative,
    )
    payload = json.loads((stage / LIVE_CHILD_RESULT_FILENAME).read_bytes())
    payload["evidence"]["heartbeat_count"] = 11
    tampered = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")

    with pytest.raises(M1BenchmarkError) as raised:
        _decode_child_result(tampered, run_id=_RUN_ID, key=_KEY)

    assert raised.value.code == "M1_LIVE_RESULT_INVALID"


def test_dirty_git_state_is_rejected_without_echoing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = "PRIVATE-DIRTY-PATH-CANARY"

    def git_text(*arguments: str) -> str:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(PROJECT_ROOT)
        if arguments[0] == "ls-files":
            return "H tracked-file"
        if arguments[0] == "status":
            return private
        return "a" * 40

    monkeypatch.setattr("tools.m1_vertical_slice_live_runner._git_text", git_text)

    with pytest.raises(M1BenchmarkError) as raised:
        _git_identity()

    assert raised.value.code == "M1_LIVE_GIT_DIRTY"
    assert private not in raised.value.safe_message
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_child_environment_is_minimal_and_disables_external_pytest_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=tests")
    monkeypatch.setenv("PYTEST_PLUGINS", "private_plugin")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other-git"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "private-python"))
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", str(tmp_path / "qpdf.exe"))

    environment = _child_environment(run_id=_RUN_ID, stage=tmp_path, key=_KEY)

    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["PWS_QPDF_EXECUTABLE"] == str(tmp_path / "qpdf.exe")
    assert environment[LIVE_ENV_RUN_ID] == str(_RUN_ID)
    assert environment[LIVE_ENV_HMAC_KEY] == _KEY.hex()
    assert environment[RESOURCE_PROFILE_ENVIRONMENT_VARIABLE] == "constrained"
    assert "PYTEST_ADDOPTS" not in environment
    assert "PYTEST_PLUGINS" not in environment
    assert "GIT_DIR" not in environment
    assert "PYTHONPATH" not in environment


def test_qpdf_identity_probe_never_receives_live_signing_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (LIVE_ENV_PROTOCOL, LIVE_ENV_RUN_ID, LIVE_ENV_STAGE, LIVE_ENV_HMAC_KEY):
        monkeypatch.setenv(name, "PRIVATE-SIGNING-AUTHORITY")
    with patch(
        "tools.m1_vertical_slice_live_runner.subprocess.Popen",
        side_effect=AssertionError("the version identity must not execute qpdf"),
    ):
        assert production_qpdf_version_identity() == "12.3.2"
    assert all(name in os.environ for name in LIVE_ENV_NAMES)


def test_qpdf_identity_rejects_an_unapproved_override_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_token = "PRIVATE-QPDF-OVERRIDE-CANARY"
    qpdf = tmp_path / private_token / "qpdf.exe"
    qpdf.parent.mkdir()
    qpdf.write_bytes(b"not the reviewed qpdf executable")
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))

    with pytest.raises(M1BenchmarkError) as raised:
        production_qpdf_version_identity()

    assert raised.value.code == "M1_LIVE_CONFIG_INVALID"
    rendered = repr(raised.value)
    assert private_token not in rendered
    assert os.fspath(qpdf) not in rendered


@pytest.mark.parametrize("substituted_name", ("qpdf30.dll", "vcruntime140.dll"))
def test_qpdf_identity_rejects_a_substituted_runtime_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    substituted_name: str,
) -> None:
    qpdf = tmp_path / "reviewed-qpdf" / "qpdf.exe"
    qpdf.parent.mkdir()
    approved: list[tuple[str, str]] = []
    for name, _digest in _QPDF_BIN_FILE_SHA256:
        payload = f"reviewed:{name}".encode("ascii")
        (qpdf.parent / name).write_bytes(
            b"substituted runtime" if name == substituted_name else payload
        )
        approved.append((name, hashlib.sha256(payload).hexdigest()))
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._QPDF_BIN_FILE_SHA256",
        tuple(approved),
    )

    with pytest.raises(M1BenchmarkError) as raised:
        production_qpdf_version_identity()

    assert raised.value.code == "M1_LIVE_CONFIG_INVALID"


def test_qpdf_distribution_authority_blocks_post_hash_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qpdf = tmp_path / "reviewed-qpdf" / "qpdf.exe"
    qpdf.parent.mkdir()
    approved: list[tuple[str, str]] = []
    for name, _digest in _QPDF_BIN_FILE_SHA256:
        payload = f"reviewed:{name}".encode("ascii")
        (qpdf.parent / name).write_bytes(payload)
        approved.append((name, hashlib.sha256(payload).hexdigest()))
    monkeypatch.setattr(
        "tools.m1_vertical_slice_live_runner._QPDF_BIN_FILE_SHA256",
        tuple(approved),
    )
    replacement = tmp_path / "replacement.dll"
    replacement.write_bytes(b"substituted after hashing")
    target = qpdf.parent / "vcruntime140.dll"
    original = target.read_bytes()

    with _open_qpdf_distribution_authority(qpdf):
        with pytest.raises(OSError):
            os.replace(replacement, target)
        with pytest.raises(OSError):
            target.write_bytes(b"mutated after hashing")

    assert target.read_bytes() == original


def test_qpdf_directory_scan_rejects_the_thirteenth_entry(tmp_path: Path) -> None:
    qpdf_root = tmp_path / "too-many-qpdf-files"
    qpdf_root.mkdir()
    for index in range(len(_QPDF_BIN_FILE_SHA256) + 1):
        (qpdf_root / f"entry-{index:02}.dll").write_bytes(b"bounded")

    with pytest.raises(M1BenchmarkError) as raised:
        _qpdf_bin_names(qpdf_root, maximum_entries=len(_QPDF_BIN_FILE_SHA256))

    assert raised.value.code == "M1_LIVE_CONFIG_INVALID"


def test_live_qpdf_requires_the_trusted_program_files_root(tmp_path: Path) -> None:
    with pytest.raises(M1BenchmarkError) as raised:
        _require_live_qpdf_trusted_root(tmp_path / "qpdf.exe")

    assert raised.value.code == "M1_LIVE_CONFIG_INVALID"
    _require_live_qpdf_trusted_root(Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe"))


def test_qpdf_distribution_hashes_match_the_dependency_inventory() -> None:
    inventory = json.loads(
        (PROJECT_ROOT / "packaging" / "dependency-inventory-v1.json").read_text(encoding="utf-8")
    )
    qpdf = next(
        component for component in inventory["components"] if component["component_id"] == "qpdf"
    )
    hashes = {item["artifact"]: item["value"] for item in qpdf["integrity"]["hashes"]}

    assert {
        name: hashes[f"qpdf-12.3.2-msvc64-bin-{name}"] for name, _digest in _QPDF_BIN_FILE_SHA256
    } == dict(_QPDF_BIN_FILE_SHA256)


@pytest.mark.parametrize("index_state", ("s hidden-file", "S skipped-file"))
def test_git_identity_rejects_hidden_index_state(
    monkeypatch: pytest.MonkeyPatch,
    index_state: str,
) -> None:
    def git_text(*arguments: str) -> str:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(PROJECT_ROOT)
        if arguments[0] == "ls-files":
            return index_state
        return ""

    monkeypatch.setattr("tools.m1_vertical_slice_live_runner._git_text", git_text)

    with pytest.raises(M1BenchmarkError) as raised:
        _git_identity()

    assert raised.value.code == "M1_LIVE_GIT_INVALID"


def test_git_subprocess_does_not_inherit_git_routing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, str]] = []
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **kwargs: object) -> object:
        commands.append(command)
        observed.append(dict(cast(Mapping[str, str], kwargs["env"])))
        return type("Completed", (), {"returncode": 0, "stdout": str(PROJECT_ROOT)})()

    monkeypatch.setenv("GIT_DIR", "PRIVATE-GIT-DIR")
    monkeypatch.setenv("GIT_INDEX_FILE", "PRIVATE-GIT-INDEX")
    with patch("tools.m1_vertical_slice_live_runner.subprocess.run", side_effect=run):
        from tools.m1_vertical_slice_live_runner import _git_text

        _git_text("rev-parse", "--show-toplevel")

    assert observed
    assert commands[0][0] == r"C:\Program Files\Git\cmd\git.exe"
    assert "--no-replace-objects" in commands[0]
    assert "core.fsmonitor=false" in commands[0]
    assert observed[0]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert observed[0]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert observed[0]["GIT_OPTIONAL_LOCKS"] == "0"
    assert "PRIVATE-GIT-DIR" not in observed[0].values()
    assert "PRIVATE-GIT-INDEX" not in observed[0].values()


def test_authenticated_child_read_rejects_oversized_file(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized-result.json"
    oversized.write_bytes(b"x" * (MAX_CHILD_RESULT_BYTES + 1))

    with pytest.raises(M1BenchmarkError) as raised:
        _read_authenticated_file(
            oversized,
            maximum_bytes=MAX_CHILD_RESULT_BYTES,
            unavailable_code="M1_LIVE_RESULT_INVALID",
            unavailable_message="The result is unavailable.",
        )

    assert raised.value.code == "M1_LIVE_RESULT_INVALID"


def test_authenticated_child_read_rejects_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "result.json"
    alias = tmp_path / "alias.json"
    source.write_bytes(b"{}\n")
    os.link(source, alias)

    with pytest.raises(M1BenchmarkError) as raised:
        _read_authenticated_file(
            source,
            maximum_bytes=MAX_CHILD_RESULT_BYTES,
            unavailable_code="M1_LIVE_RESULT_INVALID",
            unavailable_message="The result is unavailable.",
        )

    assert raised.value.code == "M1_LIVE_RESULT_INVALID"


@pytest.mark.parametrize("name", (LIVE_DERIVATIVE_FILENAME, LIVE_REPORT_FILENAME))
def test_fixed_output_preflight_preserves_existing_leaf(tmp_path: Path, name: str) -> None:
    existing = tmp_path / name
    existing.write_bytes(b"existing")

    with pytest.raises(M1BenchmarkError) as raised:
        _require_fixed_outputs_absent(tmp_path)

    assert raised.value.code == "M1_LIVE_OUTPUT_EXISTS"
    assert existing.read_bytes() == b"existing"


def test_derivative_publication_refreshes_retained_path_before_validation(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    directory = open_handle(tmp_path, traverse=True)
    derivative: WindowsHandle | None = None

    try:
        destination, derivative = _publish_derivative(
            tmp_path,
            payload,
            directory_handle=directory,
        )

        assert destination == tmp_path / LIVE_DERIVATIVE_FILENAME
        assert derivative.refresh_info().final_path == destination
        derivative.close()
        derivative = None
        assert destination.read_bytes() == payload
    finally:
        if derivative is not None:
            derivative.close()
        directory.close()


def test_private_run_cleanup_removes_only_exact_child(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    run = work / str(_RUN_ID)
    (run / "nested").mkdir(parents=True)
    (run / "nested" / "result.json").write_bytes(b"private")
    sibling = work / "sibling.txt"
    sibling.write_bytes(b"keep")
    authority = open_handle(run, delete=True, traverse=True)

    _cleanup_run_directory(run, work, authority)

    assert not run.exists()
    assert sibling.read_bytes() == b"keep"


@pytest.mark.parametrize("committed", (False, True))
def test_bundle_interrupt_preserves_derivative_only_after_report_commit(
    tmp_path: Path,
    committed: bool,
) -> None:
    derivative_path = tmp_path / LIVE_DERIVATIVE_FILENAME
    handle = _FakeDerivativeHandle()
    writer = _PostcommitInterruptWriter(committed=committed)
    report = cast(
        M1BenchmarkReport,
        SimpleNamespace(
            vertical_slice=SimpleNamespace(run_status=VerticalSliceRunStatus.COMPLETED)
        ),
    )

    with (
        patch(
            "tools.m1_vertical_slice_live_runner._publish_derivative",
            return_value=(derivative_path, cast(WindowsHandle, handle)),
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        _publish_fixed_bundle(
            output_directory=tmp_path,
            derivative=b"%PDF-1.4\n%%EOF\n",
            writer=cast(_LiveM1ReportWriter, writer),
            report=report,
        )

    assert handle.closed
    assert handle.deleted is (not committed)
