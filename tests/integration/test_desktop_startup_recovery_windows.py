from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import DetectedMediaType, ImportLimits
from paperworkshell.domain.recovery import (
    RecoveryError,
    RecoveryErrorCode,
    StartupRecoveryResult,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager

CASE_ID = UUID("86000000-0000-4000-8000-000000000001")
TEMP_ID = UUID("87000000-0000-4000-8000-000000000001")
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "render-calibration.pdf"
AUTOSAVE_RESTART_PROBE = ROOT / "tests" / "helpers" / "desktop_autosave_restart_probe.py"
PROBE_PYTHON = Path(sys.base_prefix) / "python.exe"


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


def _limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=4_000_000,
        maximum_pdf_pages=16,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=50_000_000,
        maximum_decompressed_bytes=200_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


class _TwoPagePdfInspector:
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> CompletedSourceInspection:
        del deadline, cancellation, progress
        return CompletedSourceInspection(
            source.pending_id,
            DetectedMediaType.PDF,
            2,
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
        )


class _UnusedPreviews:
    def get_previews(self, *_args: object, **_kwargs: object) -> tuple[()]:
        raise AssertionError("preview service is not used by this recovery fixture")


def _seed_backend(profile: Path) -> tuple[OrganizerBackend, CaseWorkspaceManager]:
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        maximum_page_count=16,
    )
    return (
        OrganizerBackend(
            CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
            WindowsManifestRepository(manager),
            SourceIngestService(source_store, _TwoPagePdfInspector()),
            cast(Any, _UnusedPreviews()),
        ),
        manager,
    )


def _wait_for_probe(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            pytest.fail("the desktop autosave probe exited before reaching its checkpoint")
        time.sleep(0.05)
    pytest.fail("the desktop autosave probe did not reach its checkpoint")


def _read_after_process_exit(path: Path) -> bytes:
    deadline = time.monotonic() + 5.0
    while True:
        try:
            return path.read_bytes()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class _UnusedWorkerPort:
    def inspect(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("the startup-recovery test must not inspect a source")

    def get_or_generate(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("the startup-recovery test must not generate a preview")

    def transform(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("the startup-recovery test must not transform a preview")

    def preview_prepared(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("the startup-recovery test must not rasterize a signature image")


class _Composition:
    source_inspector = _UnusedWorkerPort()
    image_source_inspector = _UnusedWorkerPort()
    thumbnail_generator = _UnusedWorkerPort()
    image_page_generator = _UnusedWorkerPort()
    image_page_transformer = _UnusedWorkerPort()
    signature_image_rasterizer = _UnusedWorkerPort()

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_recovery_failure_prevents_worker_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_composed: list[bool] = []

    class _FailedRecovery:
        def recover_startup(self) -> StartupRecoveryResult:
            raise RecoveryError(RecoveryErrorCode.SCAN_FAILED, "recovery.test")

    monkeypatch.setattr(
        desktop,
        "build_windows_startup_recovery_service",
        lambda _manager: _FailedRecovery(),
    )
    monkeypatch.setattr(
        desktop,
        "create_desktop_worker_composition",
        lambda *_args, **_kwargs: worker_composed.append(True),
    )

    with pytest.raises(RecoveryError) as raised:
        desktop._build_backend(cast(CaseWorkspaceManager, object()))

    assert raised.value.code is RecoveryErrorCode.SCAN_FAILED
    assert worker_composed == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows startup recovery integration")
def test_cold_desktop_restart_recovers_before_worker_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    first_manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(first_manager).create_case()
    lease.close()
    stale_temp = first_manager.layout(CASE_ID).temp / f"{TEMP_ID}.tmp"
    stale_temp.write_bytes(b"synthetic-leftover-work")

    fresh_manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    composition = _Composition()
    composition_observations: list[bool] = []
    resources = desktop.create_desktop_application_resource_context()
    policy_observations: list[object] = []

    def compose(*_args: object, **_kwargs: object) -> _Composition:
        composition_observations.append(stale_temp.exists())
        assert _kwargs["heavy_operations"] is resources.heavy_operations
        return composition

    def worker_config(policy: object) -> object:
        policy_observations.append(policy)
        return object()

    monkeypatch.setattr(desktop, "_desktop_worker_config", worker_config)
    monkeypatch.setattr(desktop, "create_desktop_worker_composition", compose)
    monkeypatch.setattr(
        desktop,
        "_optional_quality_ocr_review_composition",
        lambda _resources: pytest.fail("optional OCR admission ran during backend initialization"),
    )

    backend = desktop._build_backend(fresh_manager, resources=resources)
    try:
        result = backend.startup_recovery_result
        assert result.scanned_cases == 1
        assert result.cases[0].case_id == CASE_ID
        assert result.cases[0].removed_temp_files == 1
        assert result.cases[0].released_bytes == len(b"synthetic-leftover-work")
        assert not stale_temp.exists()
        assert composition_observations == [False]
        assert policy_observations == [resources.resource_policy]
    finally:
        backend.shutdown()

    assert composition.closed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop process integration")
def test_forced_ui_exit_during_autosave_recovers_in_fresh_desktop_process(
    tmp_path: Path,
) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    external_before = FIXTURE.read_bytes()
    backend, manager = _seed_backend(profile)
    try:
        backend.create_case()
        imported = backend.import_source(FIXTURE, "Synthetic autosave restart source.pdf")
        assert len(imported.state.pages) == 2
    finally:
        backend.shutdown()

    layout = manager.layout(CASE_ID)
    manifest_before = layout.manifest.read_bytes()
    checkpoint_path = layout.journal / "latest.json"
    checkpoint_before = checkpoint_path.read_bytes()
    originals_before = tuple(
        (path.name, path.read_bytes()) for path in sorted(layout.originals.iterdir())
    )
    outside_sentinel = tmp_path / "outside-case-root.bin"
    outside_sentinel.write_bytes(b"outside-case-root-must-remain")
    checkpoint_marker = tmp_path / "autosave-checkpoint-ready"
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["__PYVENV_LAUNCHER__"] = sys.executable
    assert PROBE_PYTHON.is_file()
    command = (
        os.fspath(PROBE_PYTHON),
        "-I",
        "-B",
        os.fspath(AUTOSAVE_RESTART_PROBE),
        "crash",
        os.fspath(profile),
        str(CASE_ID),
        os.fspath(checkpoint_marker),
    )
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and repository helper
        command,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        _wait_for_probe(checkpoint_marker, process)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
    assert process.returncode != 0
    checkpoint_after_crash = _read_after_process_exit(checkpoint_path)
    assert checkpoint_after_crash != checkpoint_before
    assert layout.manifest.read_bytes() == manifest_before

    result_path = tmp_path / "fresh-desktop-result.json"
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and repository helper
        (
            os.fspath(PROBE_PYTHON),
            "-I",
            "-B",
            os.fspath(AUTOSAVE_RESTART_PROBE),
            "verify",
            os.fspath(profile),
            str(CASE_ID),
            os.fspath(result_path),
        ),
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=40,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0
    result_raw = result_path.read_bytes()
    assert 1 <= len(result_raw) <= 4_096
    result = json.loads(result_raw)
    assert result == {
        "case_id": str(CASE_ID),
        "issues": [],
        "manifest_revision": 2,
        "page_rotations": [90, 0],
        "recovered_cases": 1,
        "recovery_status": "recovered",
        "startup_status": "complete",
        "state_revision": 2,
    }
    assert layout.manifest.read_bytes() == checkpoint_after_crash
    assert tuple((path.name, path.read_bytes()) for path in sorted(layout.originals.iterdir())) == (
        originals_before
    )
    assert FIXTURE.read_bytes() == external_before
    assert tuple(layout.temp.iterdir()) == ()
    assert tuple(path for path in layout.case_root.rglob("*.tmp") if path.is_file()) == ()
    assert {path.name for path in layout.journal.iterdir()} == {"latest.json"}
    assert outside_sentinel.read_bytes() == b"outside-case-root-must-remain"
    for prohibited in (
        os.fspath(profile).encode(),
        FIXTURE.name.encode(),
        b"Synthetic autosave restart source.pdf",
    ):
        assert prohibited not in result_raw
