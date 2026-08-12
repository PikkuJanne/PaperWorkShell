"""Live AppContainer evidence for handle-brokered PDF structural inspection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from uuid import UUID, uuid4

import pikepdf
import pytest

from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfOpenState,
)
from paperworkshell.storage._windows import open_handle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import (
    BrokeredHandlePurpose,
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_source_inspector import (
    BrokeredPdfSourceInspector,
    PdfInspectionBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    PDF_INSPECT_CAPABILITY,
    ResourceLimits,
    SessionCapability,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0105 uses the supported Windows AppContainer worker boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CASE_ID = UUID("10000000-0000-4000-8000-000000000105")
_CREATED_AT = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
_PACKAGE_DIRECTORIES = ("pikepdf", "pikepdf.libs", "lxml", "PIL", "packaging")


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


def _stage_pdf_engine(resources: WorkerTestResources) -> None:
    """Copy only pinned runtime packages into the already disposable worker stage."""

    source_root = Path(pikepdf.__file__).resolve().parent.parent
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


def _launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    source_root = resources.source_root
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(source_root,),
        working_directory=source_root,
    )


def _probe_launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=(resources.helper_root / "pdf_inspection_adapter_probe.py"),
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root, resources.helper_root),
        working_directory=resources.helper_root,
    )


def _workspace_quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=24_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=512,
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


def _source_store(tmp_path: Path) -> tuple[WindowsSourceIngestStore, Path]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_workspace_quota(),
        id_factory=lambda: _CASE_ID,
    )
    lifecycle = WindowsCaseLifecycleStore(manager, clock=lambda: _CREATED_AT)
    case_lease = lifecycle.create_case()
    assert case_lease.summary.case_id == _CASE_ID
    case_lease.close()
    store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _CREATED_AT,
        id_factory=_SequentialUuidFactory(0x41000000),
        temp_id_factory=_SequentialUuidFactory(0x51000000),
    )
    return store, manager.layout(_CASE_ID).temp


def _worker(resources: WorkerTestResources) -> WorkerClient:
    return WorkerClient(
        WindowsProcessLauncher(_launch_config(resources)),
        WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(PDF_INSPECT_CAPABILITY,),
    )


def _launch_request() -> WorkerLaunchRequest:
    return WorkerLaunchRequest(
        case_id=_CASE_ID,
        session_id=uuid4(),
        worker_instance_id=uuid4(),
        session_capability=SessionCapability(token_hex(32)),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
    )


def _inspection_policy() -> PdfInspectionBrokerPolicy:
    return PdfInspectionBrokerPolicy(
        ResourceLimits(
            wall_time_ms=15_000,
            cpu_time_ms=10_000,
            memory_bytes=384 * 1024 * 1024,
            temporary_storage_bytes=8 * 1024 * 1024,
            output_bytes=4_096 + 36,
            page_count=100,
            pixel_count=1,
        )
    )


def test_production_adapter_runs_with_a_reduced_source_handle_inside_app_container(
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engine(worker_test_resources)
    source = (
        _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "born-digital.pdf"
    ).resolve()
    payload = source.read_bytes()
    source_authority = open_handle(source, read=True, share_delete=True)
    process = WindowsProcessLauncher(_probe_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    try:
        worker_handle = process.duplicate_handle(
            source_authority.value,
            BrokeredHandlePurpose.READ_SOURCE,
        )
        process.transport.send_payload(
            json.dumps(
                {
                    "source_handle": worker_handle,
                    "source_sha256": hashlib.sha256(payload).hexdigest(),
                    "source_size": len(payload),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        received = process.transport.receive_payload(15.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        response = json.loads(received.payload)
    finally:
        try:
            process.close()
        finally:
            source_authority.close()

    assert response == {"open_state": "opened", "page_count": 1, "status": "ok"}


def test_real_pdf_inspection_uses_only_brokered_handles_inside_app_container(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engine(worker_test_resources)
    store, temp_root = _source_store(tmp_path)
    source = (
        _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "born-digital.pdf"
    ).resolve()
    pending = store.prepare(
        _CASE_ID,
        source,
        "Public synthetic.pdf",
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _progress: None,
    )
    worker = _worker(worker_test_resources)
    inspector = BrokeredPdfSourceInspector(
        worker,
        store,
        _inspection_policy(),
        heavy_operations=HeavyOperationCoordinator(1),
    )

    try:
        result = inspector.inspect_structural(
            pending.summary,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    finally:
        try:
            worker.terminate()
        finally:
            pending.abort()

    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert result.open_state is PdfOpenState.OPENED
    assert result.encryption_state is PdfEncryptionState.UNENCRYPTED
    assert result.page_count == 1
    assert result.features.signature_markers is PdfFeatureState.NOT_DETECTED
    assert result.features.attachments is PdfFeatureState.NOT_DETECTED
    assert result.features.actions is PdfFeatureState.NOT_DETECTED
    assert result.features.scripts is PdfFeatureState.NOT_DETECTED
    assert list(temp_root.iterdir()) == []
