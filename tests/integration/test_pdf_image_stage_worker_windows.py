"""Explicit image stages through real constrained AppContainer workers and leases."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from threading import Event

import pikepdf
import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageEligibility,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageRequest,
)
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
from paperworkshell.worker.pdf_image_stage_broker import VerifiedPdfImageStageCandidate
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
    os.name != "nt", reason="PWS-0503 requires the supported Windows AppContainer worker boundary"
)


def _read_candidate(candidate: VerifiedPdfImageStageCandidate) -> bytes:
    chunks: list[bytes] = []
    while chunk := candidate.read(65_536):
        chunks.append(chunk)
    payload = b"".join(chunks)
    assert len(payload) == candidate.report.output_byte_size
    assert hashlib.sha256(payload).hexdigest() == candidate.report.output_sha256
    return payload


def test_live_constrained_stage_analysis_reduction_failure_cancel_and_recovery(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    _stage_pdf_engines(worker_test_resources)
    original = tmp_path / "synthetic-image.pdf"
    # A high-quality JPEG retains measurable reduction after the lossless baseline.
    make_image_baseline(original, encoding="jpeg")
    original_bytes = original.read_bytes()
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
        id_factory=_SequentialUuidFactory(0x25300000),
        temp_id_factory=_SequentialUuidFactory(0x35300000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID, original, "PRIVATE-IMAGE-STAGE-CANARY.pdf"
    )
    layout = manager.layout(_CASE_ID)
    committed = layout.originals / f"{imported.source_id}.bin"
    committed_bytes = committed.read_bytes()
    manifest_before = layout.manifest.read_bytes()
    files_before = {path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")}
    plan = build_pdf_export_plan(WindowsManifestRepository(manager).load(_CASE_ID).snapshot)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
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
        maximum_heavy_operation_concurrency=1,
        export_limits=_broker_policy().limits,
        pump_interval_seconds=0.01,
    )
    with create_pdf_image_stage_worker_composition(
        config, heavy_operations=HeavyOperationCoordinator(1)
    ) as runtime:
        processor = runtime.image_stage_processor
        observed: list[PdfExportProgress] = []
        with (
            sources.borrow_committed_pdf_sources(
                plan, deadline=lambda: False, cancellation=lambda: False
            ) as lease,
            processor.process(
                plan,
                lease,
                PdfImageStageRequest(),
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=observed.append,
            ) as analyzed,
        ):
            assert analyzed.report.outcome is PdfImageStageOutcome.UNCHANGED
            assert analyzed.baseline_report.optimization is not None
            assert analyzed.analysis.assets[0].eligibility is PdfImageEligibility.ELIGIBLE
            baseline = _read_candidate(analyzed)
            request = PdfImageStageRequest(
                PdfImageStageProfile.PRESERVE_300_DPI_Q90,
                (0,),
                analyzed.analysis.baseline_sha256,
                plan.semantic_fingerprint,
            )
        assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
        assert observed and observed[-1].completed_pages == plan.page_count
        staged_progress: list[PdfExportProgress] = []
        with (
            sources.borrow_committed_pdf_sources(
                plan, deadline=lambda: False, cancellation=lambda: False
            ) as lease,
            processor.process(
                plan,
                lease,
                request,
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=staged_progress.append,
            ) as staged,
        ):
            assert staged.report.outcome is PdfImageStageOutcome.REENCODED
            assert staged.report.output_byte_size < len(baseline)
            assert staged.report.assets[0].output_effective_dpi_x_milli == 300_000
            assert staged.report.assets[0].output_width_pixels == 300
            assert staged.report.verified_page_count == 2
            selected = _read_candidate(staged)
        assert staged_progress[-1].completed_pages == plan.page_count
        assert any(0 < item.completed_pages < plan.page_count for item in staged_progress)
        snapshot = runtime.resource_snapshot()
        assert snapshot is not None
        assert snapshot.process_memory_limit_bytes == policy.worker_process_memory_bytes
        assert snapshot.job_memory_limit_bytes == policy.worker_job_memory_bytes
        assert snapshot.peak_process_memory_used_bytes <= snapshot.process_memory_limit_bytes
        assert snapshot.peak_job_memory_used_bytes <= snapshot.job_memory_limit_bytes

        # A changed baseline must fail closed inside the restricted parser process.
        with (
            sources.borrow_committed_pdf_sources(
                plan, deadline=lambda: False, cancellation=lambda: False
            ) as lease,
            pytest.raises(PdfExportError) as rejected,
        ):
            processor.process(
                plan,
                lease,
                replace(request, baseline_sha256="f" * 64),
                deadline=lambda: False,
                cancellation=lambda: False,
            )
        assert rejected.value.code is PdfExportErrorCode.WORKER_FAILED
        assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED

        cancelled = Event()

        def cancel_active(value: PdfExportProgress) -> None:
            if value.completed_pages > 0:
                cancelled.set()

        with (
            sources.borrow_committed_pdf_sources(
                plan, deadline=lambda: False, cancellation=lambda: False
            ) as lease,
            pytest.raises(PdfExportError) as stopped,
        ):
            processor.process(
                plan,
                lease,
                request,
                deadline=lambda: False,
                cancellation=cancelled.is_set,
                progress=cancel_active,
            )
        assert cancelled.is_set() and stopped.value.code is PdfExportErrorCode.CANCELLED
        assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
        with sources.borrow_committed_pdf_sources(
            plan, deadline=lambda: False, cancellation=lambda: False
        ) as lease:
            with pytest.raises(PdfExportError) as expired:
                processor.process(
                    plan,
                    lease,
                    request,
                    deadline=lambda: True,
                    cancellation=lambda: False,
                )
            assert expired.value.code is PdfExportErrorCode.TIMED_OUT
            with processor.process(
                plan,
                lease,
                request,
                deadline=lambda: False,
                cancellation=lambda: False,
            ) as retry:
                assert retry.report.outcome is PdfImageStageOutcome.REENCODED
                assert _read_candidate(retry) == selected

    baseline_path, selected_path = tmp_path / "baseline.pdf", tmp_path / "selected.pdf"
    baseline_path.write_bytes(baseline)
    selected_path.write_bytes(selected)
    with pikepdf.Pdf.open(baseline_path) as before, pikepdf.Pdf.open(selected_path) as after:
        assert len(after.pages) == len(before.pages) == 2
        assert before.docinfo == after.docinfo
        for left, right in zip(before.pages, after.pages, strict=True):
            assert left.obj.Contents.read_bytes() == right.obj.Contents.read_bytes()
        assert after.pages[0].obj.Resources.XObject.Pixels.ColorSpace == pikepdf.Name.DeviceRGB
    assert original.read_bytes() == original_bytes
    assert committed.read_bytes() == committed_bytes
    assert hashlib.sha256(committed_bytes).hexdigest() == imported.sha256
    assert layout.manifest.read_bytes() == manifest_before
    assert {
        path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")
    } == files_before
