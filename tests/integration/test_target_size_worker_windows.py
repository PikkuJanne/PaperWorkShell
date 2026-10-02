"""Target-size package search through real constrained Windows workers and storage."""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path
from uuid import UUID

import pikepdf
import pytest

from paperworkshell.app.package_pdf_export import PackagePdfProgressSink
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    DeadlineProbe,
    SourceIngestService,
)
from paperworkshell.app.target_size_pdf_export import (
    TargetSizePackageCandidatePort,
    TargetSizePdfCandidateProducer,
)
from paperworkshell.domain.checklist import TemplateBinding
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PORTAL_PDF_EXPORT_PROFILE,
    PdfExportCheckCode,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageEligibility,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageRequest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.target_size_search import TargetSizeSearchTerminal
from paperworkshell.export.package_export import PackageExportProgress, PackageExportStatus
from paperworkshell.export.target_size_export import export_target_size_package
from paperworkshell.preflight.facts import (
    OutputItemPreflightFact,
    OutputOverlayFlatteningAvailability,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
)
from paperworkshell.preflight.package_planner import (
    ActivePackageConstraints,
    PackageExportPlan,
    PackagePlanAuthority,
    PlannedPackageArchive,
    PlannedPackagePdf,
)
from paperworkshell.preflight.portal_constraints import (
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalOutputMode,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_destination import WindowsExportDestination
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
    os.name != "nt", reason="PWS-0504 requires the supported Windows AppContainer worker boundary"
)


class _UnexpectedFallback:
    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink,
    ) -> TargetSizePackageCandidatePort:
        raise AssertionError("the ordinary synthetic PDF must use the real image-stage worker")


def _package_plan(
    snapshot: ManifestSnapshot,
    page_ids: tuple[UUID, ...],
    mode: PortalOutputMode,
    target_bytes: int,
) -> PackageExportPlan:
    assignments = (
        UUID("35400000-0000-4000-8000-000000000001"),
        UUID("35400000-0000-4000-8000-000000000002"),
    )
    pdfs: tuple[PlannedPackagePdf, ...]
    if mode is PortalOutputMode.COMBINED_PDF:
        pdfs = (PlannedPackagePdf("package", 1, None, assignments, page_ids, "packet.pdf", 1),)
    else:
        pdfs = tuple(
            PlannedPackagePdf(
                f"part-{index}",
                index,
                f"part-{index}",
                (assignments[index - 1],),
                (page_id,),
                f"part-{index}.pdf",
                1,
            )
            for index, page_id in enumerate(page_ids, 1)
        )
    archive = (
        PlannedPackageArchive("archive", "packet.zip", 1, tuple(pdf.output_key for pdf in pdfs))
        if mode is PortalOutputMode.ZIP
        else None
    )
    return PackageExportPlan(
        authority=PackagePlanAuthority(
            snapshot.case_id,
            snapshot.revision,
            snapshot.sha256,
            snapshot.cas_token,
            TemplateBinding("synthetic-target-size", "1.0.0", "a" * 64),
        ),
        mode=mode,
        active_constraints=ActivePackageConstraints(
            PortalConstraints(
                (mode,),
                PortalEncryptionPolicy.MUST_BE_UNENCRYPTED,
                maximum_file_count=2,
                maximum_total_bytes=target_bytes,
                maximum_file_bytes=target_bytes,
                maximum_pages_per_file=2,
            ),
            (),
        ),
        pdfs=pdfs,
        archive=archive,
        output_facts=OutputPlanPreflightFacts(
            snapshot.case_id,
            snapshot.revision,
            OutputPlanAvailability.CURRENT,
            mode.value,
            tuple(
                OutputItemPreflightFact(pdf.output_key, pdf.position, pdf.page_ids) for pdf in pdfs
            ),
            OutputOverlayFlatteningAvailability.CURRENT,
            (),
        ),
    )


def _assert_pdf_content(
    payload: bytes, original: Path, page_indexes: tuple[int, ...], *, expect_image: bool
) -> None:
    with pikepdf.Pdf.open(io.BytesIO(payload)) as output, pikepdf.Pdf.open(original) as source:
        assert len(output.pages) == len(page_indexes)
        assert not output.check_pdf_syntax()
        assert not output.is_encrypted
        for page, source_index in zip(output.pages, page_indexes, strict=True):
            assert (
                page.obj.Contents.read_bytes()
                == source.pages[source_index].obj.Contents.read_bytes()
            )
            assert tuple(page.obj.MediaBox) == tuple(source.pages[source_index].obj.MediaBox)
        if expect_image:
            pixels = output.pages[0].obj.Resources.XObject.Pixels
            assert pixels.ColorSpace == pikepdf.Name.DeviceRGB
            assert (int(pixels.Width), int(pixels.Height)) == (300, 240)


def test_live_target_search_combined_separate_and_zip_preserves_sources_and_cleans_staging(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    _stage_pdf_engines(worker_test_resources)
    original = tmp_path / "synthetic-target-size.pdf"
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
        id_factory=_SequentialUuidFactory(0x25400000),
        temp_id_factory=_SequentialUuidFactory(0x35400000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID, original, "PRIVATE-TARGET-SIZE-CANARY.pdf"
    )
    layout = manager.layout(_CASE_ID)
    committed_source = layout.originals / f"{imported.source_id}.bin"
    committed_bytes = committed_source.read_bytes()
    manifest_before = layout.manifest.read_bytes()
    files_before = {path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")}
    snapshot = WindowsManifestRepository(manager).load(_CASE_ID).snapshot
    pdf_plan = build_pdf_export_plan(snapshot, profile=PORTAL_PDF_EXPORT_PROFILE)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    heavy_operations = HeavyOperationCoordinator(policy.maximum_heavy_operation_concurrency)
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
    with create_pdf_image_stage_worker_composition(
        config, heavy_operations=heavy_operations
    ) as runtime:
        processor = runtime.image_stage_processor
        with (
            sources.borrow_committed_pdf_sources(
                pdf_plan, deadline=lambda: False, cancellation=lambda: False
            ) as lease,
            processor.process(
                pdf_plan,
                lease,
                PdfImageStageRequest(),
                deadline=lambda: False,
                cancellation=lambda: False,
            ) as analyzed,
        ):
            baseline_size = analyzed.report.output_byte_size
            baseline_digest = analyzed.analysis.baseline_sha256
            assert analyzed.report.outcome is PdfImageStageOutcome.UNCHANGED
            assert analyzed.baseline_report.optimization is not None
            assert analyzed.baseline_report.portal_profile_verified
            assert analyzed.analysis.assets[0].eligibility is PdfImageEligibility.ELIGIBLE
        assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED

        # One imported two-page source supports every mode. The image-heavy first
        # page exceeds half the combined baseline; the text/vector page stays exact.
        for mode in PortalOutputMode:
            target = (
                baseline_size - 1 if mode is PortalOutputMode.COMBINED_PDF else baseline_size // 2
            )
            plan = _package_plan(snapshot, imported.page_ids, mode, target)
            destination = tmp_path / mode.value
            destination.mkdir()
            producer = TargetSizePdfCandidateProducer(
                sources, processor, _UnexpectedFallback(), policy
            )
            progress: list[PackageExportProgress] = []
            result = export_target_size_package(
                snapshot,
                plan,
                destination,
                producer,
                WindowsExportDestination(
                    minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes
                ),
                policy,
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=progress.append,
            )
            assert result.successful and result.package.status is PackageExportStatus.COMMITTED
            assert result.decision.terminal is TargetSizeSearchTerminal.SATISFIED
            assert result.decision.semantic_fingerprint == plan.semantic_fingerprint
            assert result.decision.policy.resource_policy == policy
            assert result.decision.selected_profile is PdfImageStageProfile.PRESERVE_300_DPI_Q90
            assert len(result.decision.attempts) == 2
            assert not result.decision.attempts[0].constraints_satisfied
            assert result.decision.attempts[1].constraints_satisfied
            assert result.decision.attempts[0].pdf_byte_sizes[0] > target
            assert all(size <= target for size in result.decision.attempts[1].pdf_byte_sizes)
            assert not producer.unsupported_output_positions
            assert len(result.image_stage_reports) == len(plan.pdfs)
            image_stage = result.image_stage_reports[0]
            assert image_stage.outcome is PdfImageStageOutcome.REENCODED
            assert image_stage.assets[0].output_effective_dpi_x_milli == 300_000
            assert image_stage.assets[0].output_effective_dpi_y_milli == 300_000
            if mode is PortalOutputMode.COMBINED_PDF:
                assert image_stage.baseline_sha256 == baseline_digest
                assert result.decision.attempts[0].pdf_byte_sizes == (baseline_size,)
            assert result.package.destination is not None
            assert all(
                artifact.destination_reopened for artifact in result.package.destination.artifacts
            )
            for verified, stage in zip(
                result.package.report.pdfs, result.image_stage_reports, strict=True
            ):
                assert stage.verified_page_count == verified.base_report.page_count
                assert stage.output_sha256 == verified.base_report.output_sha256
                assert stage.output_byte_size == verified.base_report.output_byte_size
                assert {
                    PdfExportCheckCode.PDF_REOPENED,
                    PdfExportCheckCode.STRUCTURE_CLEAN,
                    PdfExportCheckCode.ALL_PAGES_RENDERED,
                    PdfExportCheckCode.PAGE_COUNT_MATCHED,
                    PdfExportCheckCode.PORTAL_PROFILE_VERIFIED,
                }.issubset({check.code for check in verified.base_report.checks})
            assert progress[-1].phase.value == "complete"
            assert progress[-1].completed_pages == 2

            if mode is PortalOutputMode.ZIP:
                archive_path = destination / "packet.zip"
                assert {path.name for path in destination.iterdir()} == {"packet.zip"}
                assert result.package.report.archive is not None
                assert archive_path.stat().st_size == result.decision.attempts[-1].archive_byte_size
                assert archive_path.stat().st_size <= target
                with zipfile.ZipFile(archive_path) as archive:
                    assert archive.namelist() == [pdf.filename for pdf in plan.pdfs]
                    assert archive.testzip() is None
                    payloads = tuple(archive.read(pdf.filename) for pdf in plan.pdfs)
            else:
                assert {path.name for path in destination.iterdir()} == {
                    pdf.filename for pdf in plan.pdfs
                }
                payloads = tuple((destination / pdf.filename).read_bytes() for pdf in plan.pdfs)
                assert sum(len(payload) for payload in payloads) <= target
            for index, (payload, verified) in enumerate(
                zip(payloads, result.package.report.pdfs, strict=True)
            ):
                assert hashlib.sha256(payload).hexdigest() == verified.base_report.output_sha256
                page_indexes = (0, 1) if mode is PortalOutputMode.COMBINED_PDF else (index,)
                _assert_pdf_content(payload, original, page_indexes, expect_image=index == 0)

            resources = runtime.resource_snapshot()
            assert resources is not None
            assert resources.process_memory_limit_bytes == policy.worker_process_memory_bytes
            assert resources.job_memory_limit_bytes == policy.worker_job_memory_bytes
            assert resources.peak_process_memory_used_bytes <= resources.process_memory_limit_bytes
            assert resources.peak_job_memory_used_bytes <= resources.job_memory_limit_bytes
            assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
            assert heavy_operations.snapshot().active_operations == 0
            assert heavy_operations.snapshot().maximum_observed_concurrency == 1
            assert {
                path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")
            } == files_before
            assert original.read_bytes() == original_bytes
            assert committed_source.read_bytes() == committed_bytes
            assert hashlib.sha256(committed_bytes).hexdigest() == imported.sha256
            assert layout.manifest.read_bytes() == manifest_before
