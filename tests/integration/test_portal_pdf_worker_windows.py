"""Portal policy survives real constrained-worker transport and candidate cleanup."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pikepdf
import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.pdf_export import (
    PORTAL_PDF_EXPORT_PROFILE,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_image_stages import PdfImageStageRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import create_pdf_export_worker_composition_config
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_image_stage_composition import (
    create_pdf_image_stage_worker_composition,
)
from tests.integration.test_pdf_export_worker_windows import (
    _CASE_ID,
    _NOW,
    _broker_policy,
    _import_limits,
    _PdfInspector,
    _qpdf_executable,
    _SequentialUuidFactory,
    _stage_pdf_engines,
    _workspace_quota,
)
from tests.unit.test_pdf_image_stage_adapter import make_image_baseline
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0506 requires the supported Windows AppContainer boundary"
)


def _nested_metadata(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
    page.obj.Resources.Private = pikepdf.Dictionary(
        Metadata=document.make_stream(b"PRIVATE-PORTAL-METADATA-CANARY")
    )


@pytest.mark.parametrize("prohibited", (False, True))
def test_portal_worker_verifies_or_rejects_nested_feature_without_source_mutation(
    tmp_path: Path, worker_test_resources: WorkerTestResources, prohibited: bool
) -> None:
    _stage_pdf_engines(worker_test_resources)
    original = tmp_path / "synthetic-portal.pdf"
    make_image_baseline(original, encoding="jpeg", mutate=_nested_metadata if prohibited else None)
    original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile, quota=_workspace_quota(), id_factory=lambda: _CASE_ID
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x25600000),
        temp_id_factory=_SequentialUuidFactory(0x35600000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID, original, "PRIVATE-PORTAL-WORKER-CANARY.pdf"
    )
    snapshot = WindowsManifestRepository(manager).load(_CASE_ID).snapshot
    plan = build_pdf_export_plan(snapshot, profile=PORTAL_PDF_EXPORT_PROFILE)
    layout = manager.layout(_CASE_ID)
    committed = layout.originals / f"{imported.source_id}.bin"
    manifest_before = layout.manifest.read_bytes()
    files_before = {path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")}
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    gate = HeavyOperationCoordinator(policy.maximum_heavy_operation_concurrency)
    config = create_pdf_export_worker_composition_config(
        python_executable=worker_test_resources.python_executable,
        bootstrap_script=worker_test_resources.source_root
        / "paperworkshell"
        / "worker"
        / "host_bootstrap.py",
        runtime_roots=(worker_test_resources.runtime_root,),
        code_roots=(worker_test_resources.source_root,),
        working_directory=worker_test_resources.source_root,
        qpdf_executable=_qpdf_executable(),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=min(60_000, policy.worker_cpu_time_ms),
            maximum_processes=policy.worker_maximum_processes,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0, cancellation_grace_seconds=5.0, shutdown_grace_seconds=10.0
        ),
        maximum_heavy_operation_concurrency=policy.maximum_heavy_operation_concurrency,
        export_limits=_broker_policy().limits,
        pump_interval_seconds=0.01,
    )
    with create_pdf_image_stage_worker_composition(config, heavy_operations=gate) as runtime:
        with sources.borrow_committed_pdf_sources(
            plan, deadline=lambda: False, cancellation=lambda: False
        ) as lease:
            if prohibited:
                with pytest.raises(PdfExportError) as caught:
                    runtime.image_stage_processor.process(
                        plan,
                        lease,
                        PdfImageStageRequest(),
                        deadline=lambda: False,
                        cancellation=lambda: False,
                        progress=lambda _progress: None,
                    )
                # Native policy tests assert the detailed structure code. The
                # existing worker boundary deliberately returns a closed generic
                # failure instead of exposing parser or document feature details.
                assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
                assert "CANARY" not in str(caught.value)
            else:
                with runtime.image_stage_processor.process(
                    plan,
                    lease,
                    PdfImageStageRequest(),
                    deadline=lambda: False,
                    cancellation=lambda: False,
                    progress=lambda _progress: None,
                ) as candidate:
                    assert PdfExportCheckCode.PORTAL_PROFILE_VERIFIED in {
                        check.code for check in candidate.baseline_report.checks
                    }
                    chunks: list[bytes] = []
                    measured = 0
                    while chunk := candidate.read(65_536):
                        measured += len(chunk)
                        assert measured <= candidate.report.output_byte_size
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    assert measured == candidate.report.output_byte_size
                    assert hashlib.sha256(payload).hexdigest() == candidate.report.output_sha256
        assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
        accounting = runtime.resource_snapshot()
        assert accounting is not None
        assert accounting.peak_process_memory_used_bytes <= policy.worker_process_memory_bytes
        assert accounting.peak_job_memory_used_bytes <= policy.worker_job_memory_bytes
        assert gate.snapshot().active_operations == 0
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_digest
    assert hashlib.sha256(committed.read_bytes()).hexdigest() == original_digest
    assert layout.manifest.read_bytes() == manifest_before
    assert {
        path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")
    } == files_before
