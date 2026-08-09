"""Live PWS-0112 round trip through source storage, worker, destination, and history."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pypdfium2_raw  # type: ignore[import-untyped]
import pytest

from paperworkshell.app.export_history import PdfExportHistoryService
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.recovery import build_windows_startup_recovery_service
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_export import (
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.recovery import CaseRecoveryStatus, StartupRecoveryStatus
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.pdf_export_broker import (
    BrokeredPdfExporter,
    PdfExportBrokerPolicy,
)
from paperworkshell.worker.protocol.models import PDF_EXPORT_CAPABILITY, ResourceLimits
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0112 uses the supported Windows AppContainer worker boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = (
    _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "render-calibration.pdf"
)
_CASE_ID = UUID("11200000-0000-4000-8000-000000000112")
_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
_PACKAGE_DIRECTORIES = (
    "pikepdf",
    "pikepdf.libs",
    "lxml",
    "PIL",
    "packaging",
    "pypdfium2",
    "pypdfium2_cfg",
    "pypdfium2_raw",
)


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


@dataclass(frozen=True, slots=True)
class _PdfInspector:
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> CompletedSourceInspection:
        assert callable(deadline) and not deadline()
        assert callable(cancellation) and not cancellation()
        assert callable(progress)
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=2,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )


def _stage_pdf_engines(resources: WorkerTestResources) -> None:
    roots = {
        Path(pikepdf.__file__).resolve().parent.parent,
        Path(pypdfium2.__file__).resolve().parent.parent,
        Path(pypdfium2_raw.__file__).resolve().parent.parent,
    }
    assert len(roots) == 1
    source_root = roots.pop()
    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    for name in _PACKAGE_DIRECTORIES:
        source = source_root / name
        destination = destination_root / name
        assert source.is_dir()
        if destination.is_dir():
            continue
        assert not destination.exists()
        shutil.copytree(
            source,
            destination,
            copy_function=shutil.copyfile,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def _launch_config(
    resources: WorkerTestResources,
    qpdf_executable: Path,
) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
        qpdf_executable=qpdf_executable,
    )


def _workspace_quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=96_000_000,
        maximum_cache_bytes=16_000_000,
        maximum_temp_bytes=48_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=3_000_000,
        maximum_pdf_pages=100,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=50_000_000,
        maximum_decompressed_bytes=200_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


def _worker(resources: WorkerTestResources, qpdf_executable: Path) -> WorkerClient:
    return WorkerClient(
        WindowsProcessLauncher(_launch_config(resources, qpdf_executable)),
        WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=60_000,
            maximum_processes=2,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(PDF_EXPORT_CAPABILITY,),
    )


def _broker_policy() -> PdfExportBrokerPolicy:
    return PdfExportBrokerPolicy(
        ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=25_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=32 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            page_count=100,
            pixel_count=1_048_576,
        ),
        pump_interval_seconds=0.01,
    )


def test_live_export_is_repeatable_verified_recorded_and_preserves_source(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    source_bytes = _FIXTURE.read_bytes()
    qpdf_executable = _qpdf_executable()
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_workspace_quota(),
        id_factory=lambda: _CASE_ID,
    )
    case = WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case()
    case.close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x21200000),
        temp_id_factory=_SequentialUuidFactory(0x31200000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID,
        _FIXTURE,
        "PRIVATE-EXPORT-CANARY.pdf",
    )
    assert len(imported.page_ids) == 2
    committed_source = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    committed_source_bytes = committed_source.read_bytes()
    assert hashlib.sha256(committed_source_bytes).hexdigest() == imported.sha256
    worker = _worker(worker_test_resources, qpdf_executable)
    broker = BrokeredPdfExporter(
        worker,
        _broker_policy(),
        id_factory=_SequentialUuidFactory(0x41200000),
    )
    manifests = WindowsManifestRepository(
        manager,
        id_factory=_SequentialUuidFactory(0x51200000),
    )
    history_repository = WindowsPdfExportHistoryRepository(
        manager,
        id_factory=_SequentialUuidFactory(0x61200000),
    )
    service = PdfExportService(
        manifests,
        sources,
        broker,
        WindowsVerifiedPdfDestination(),
        PdfExportHistoryService(history_repository, manifests),
        WindowsPdfExportRetryRepository(
            manager,
            id_factory=_SequentialUuidFactory(0x71210000),
        ),
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x71200000),
    )
    progress: list[PdfExportProgress] = []
    first_path = tmp_path / "verified-first.pdf"
    second_path = tmp_path / "verified-second.pdf"

    first = service.export_pdf(
        _CASE_ID,
        first_path,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )
    second = service.export_pdf(
        _CASE_ID,
        second_path,
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert first.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert second.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert first.report.semantic_fingerprint == second.report.semantic_fingerprint
    assert first.report.output_sha256 == second.report.output_sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    assert _FIXTURE.read_bytes() == source_bytes
    assert committed_source.read_bytes() == committed_source_bytes
    assert hashlib.sha256(committed_source.read_bytes()).hexdigest() == imported.sha256
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert tuple(check.code for check in first.checks) == (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
        PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,
    )
    with pikepdf.Pdf.open(first_path, attempt_recovery=False, password="") as output:
        assert len(output.pages) == 2
        assert not output.is_encrypted
    assert progress[0].phase is PdfExportProgressPhase.PREPARING
    assert progress[-1].phase is PdfExportProgressPhase.COMPLETE
    history = history_repository.load(_CASE_ID).document
    assert len(history.records) == 2
    assert {record.export_id for record in history.records} == {
        first.export_id,
        second.export_id,
    }
    linked = manifests.load(_CASE_ID).snapshot.document["export_history"]
    assert type(linked) is dict
    assert len(linked["entries"]) == 2


def test_live_worker_termination_never_publishes_destination(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    """Terminating a running real worker fails before destination publication."""

    _stage_pdf_engines(worker_test_resources)
    source_bytes = _FIXTURE.read_bytes()
    qpdf_executable = _qpdf_executable()
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_workspace_quota(),
        id_factory=lambda: _CASE_ID,
    )
    case = WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case()
    case.close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x81200000),
        temp_id_factory=_SequentialUuidFactory(0x91200000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID,
        _FIXTURE,
        "PRIVATE-EXPORT-FAILURE-CANARY.pdf",
    )
    committed_source = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    committed_source_bytes = committed_source.read_bytes()
    assert hashlib.sha256(committed_source_bytes).hexdigest() == imported.sha256
    worker = _worker(worker_test_resources, qpdf_executable)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    manifest_before = manager.layout(_CASE_ID).manifest.read_bytes()
    service = PdfExportService(
        manifests,
        sources,
        BrokeredPdfExporter(worker, _broker_policy()),
        WindowsVerifiedPdfDestination(),
        PdfExportHistoryService(history_repository, manifests),
        WindowsPdfExportRetryRepository(manager),
    )
    destination = tmp_path / "must-not-exist.pdf"
    terminated = False

    def terminate_running_worker(value: PdfExportProgress) -> None:
        nonlocal terminated
        if value.phase is PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING and not terminated:
            terminated = True
            worker.terminate()

    with pytest.raises(PdfExportError) as raised:
        service.export_pdf(
            _CASE_ID,
            destination,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=terminate_running_worker,
        )

    assert terminated
    assert raised.value.code is PdfExportErrorCode.WORKER_FAILED
    assert not destination.exists()
    assert not history_repository.load(_CASE_ID).document.records
    assert manager.layout(_CASE_ID).manifest.read_bytes() == manifest_before
    assert _FIXTURE.read_bytes() == source_bytes
    assert committed_source.read_bytes() == committed_source_bytes
    assert hashlib.sha256(committed_source.read_bytes()).hexdigest() == imported.sha256
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED

    recovery = build_windows_startup_recovery_service(manager).recover_startup()
    assert recovery.status is StartupRecoveryStatus.COMPLETE
    assert recovery.cases[0].status is CaseRecoveryStatus.CURRENT
    assert manager.layout(_CASE_ID).manifest.read_bytes() == manifest_before

    restarted_destination = tmp_path / "verified-after-worker-restart.pdf"
    restarted = service.export_pdf(
        _CASE_ID,
        restarted_destination,
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert restarted.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert restarted_destination.is_file()
    assert len(history_repository.load(_CASE_ID).document.records) == 1
    assert _FIXTURE.read_bytes() == source_bytes
    assert committed_source.read_bytes() == committed_source_bytes
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
