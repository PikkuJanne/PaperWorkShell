"""Private grayscale review, native page comparison, and exact Windows commit."""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

import paperworkshell.__main__ as desktop
import paperworkshell.worker.compression_preview as preview_module
import paperworkshell.worker.pdf_image_stage_composition as stage_module
from paperworkshell.app.checklist import checklist_definition_from_template
from paperworkshell.app.package_workflow import evaluate_package_review
from paperworkshell.app.source_ingest import CancellationProbe, DeadlineProbe, SourceIngestService
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.package_summary import PackageSummaryStatus
from paperworkshell.domain.pdf_image_stages import PdfImageColorMode, PdfImageStageProfile
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.signature_images import SignatureImageRasterRequest
from paperworkshell.domain.target_size_search import TargetSizeColorChoice, TargetSizeSearchTerminal
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.preflight.package_planner import build_package_export_plan
from paperworkshell.preflight.portal_constraints import (
    PortalOutputMode,
    portal_constraint_profile_from_template,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import ProcessLauncher, WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import CapabilityDescriptor
from tests.integration.test_pdf_export_worker_windows import (
    _CASE_ID,
    _NOW,
    _import_limits,
    _PdfInspector,
    _qpdf_executable,
    _SequentialUuidFactory,
    _stage_pdf_engines,
    _workspace_quota,
)
from tests.unit.test_package_planner import _assignment, _confirmation
from tests.unit.test_pdf_image_stage_adapter import make_image_baseline
from tests.unit.test_template_loader import (
    CANONICAL_KEY,
    _byte_snapshot,
    _Repository,
    _template_bytes,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0505 requires the supported Windows AppContainer worker boundary"
)


class _UnusedRasterizer:
    def rasterize_committed(
        self,
        request: SignatureImageRasterRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> SignatureImageOverlayRasterSource:
        raise AssertionError("the image-stage review must not invoke the flattening fallback")


def _single_requirement(document: dict[str, object]) -> None:
    document["requirements"] = cast(list[object], document["requirements"])[:1]
    document["requirement_order"] = ["application-form"]
    document["warnings"] = []
    document["confirmations"] = []
    filename = cast(
        dict[str, object], cast(dict[str, object], document["output"])["filename_policy"]
    )
    filename["requirement_stems"] = cast(list[object], filename["requirement_stems"])[:1]


def _raster_review_target(
    document: pikepdf.Pdf, stream: pikepdf.Stream, page: pikepdf.Page
) -> None:
    """Add readable raster text, detail, colored labels, and invented ink marks."""

    with (
        Image.open(io.BytesIO(stream.read_raw_bytes())) as decoded,
        decoded.convert("RGB") as image,
    ):
        draw = ImageDraw.Draw(image)
        draw.rectangle((16, 16, 584, 285), fill="white", outline="black", width=3)
        for position, text, size in (
            ((28, 32), "SIMULATED RECORD", 36),
            ((28, 92), "Reference: DEMO-2048", 28),
            ((28, 140), "Amount: 1234.56 units", 28),
            ((28, 195), "Small print: Synthetic sample 0123456789", 18),
            ((28, 236), "No personal data. Review every detail.", 20),
        ):
            draw.text(position, text, fill="black", font=ImageFont.load_default(size=size))
        draw.rectangle((18, 306, 282, 367), fill=(25, 70, 190))
        draw.rectangle((316, 306, 582, 367), fill=(20, 135, 70))
        draw.text((36, 319), "OPTION A", fill="white", font=ImageFont.load_default(size=32))
        draw.text((334, 319), "OPTION B", fill="white", font=ImageFont.load_default(size=32))
        draw.rectangle((16, 383, 584, 466), fill="white")
        draw.line(
            (30, 435, 58, 401, 73, 442, 111, 417, 134, 443, 168, 400, 177, 439, 241, 418, 285, 433),
            fill=(90, 25, 100),
            width=4,
        )
        draw.line((25, 450, 302, 447), fill=(90, 25, 100), width=2)
        draw.ellipse((397, 390, 573, 458), outline=(165, 20, 40), width=4)
        draw.text((442, 411), "DEMO", fill=(165, 20, 40), font=ImageFont.load_default(size=24))
        with io.BytesIO() as encoded:
            image.save(encoded, format="JPEG", quality=98, subsampling=0)
            stream.write(encoded.getvalue(), filter=pikepdf.Name.DCTDecode)


@dataclass(frozen=True, slots=True, repr=False)
class CompressionReviewEvidence:
    """Synthetic bytes and validated pixels; no retained worker or file authority."""

    state: CompressionReviewState
    comparison: CompressionPagePreview
    original_pdf: bytes
    proposed_pdf: bytes


def test_live_grayscale_review_renders_private_candidate_and_commits_exact_bytes(
    tmp_path: Path, worker_test_resources: WorkerTestResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_live_compression_review(tmp_path, worker_test_resources, monkeypatch)


def run_live_compression_review(
    tmp_path: Path, worker_test_resources: WorkerTestResources, monkeypatch: pytest.MonkeyPatch
) -> CompressionReviewEvidence:
    """Reusable native proof for the test and an explicit synthetic visual harness."""

    _stage_pdf_engines(worker_test_resources)
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", str(_qpdf_executable()))
    monkeypatch.setattr(
        desktop,
        "_worker_runtime_resources",
        lambda: (
            worker_test_resources.python_executable,
            None,
            worker_test_resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
            (worker_test_resources.runtime_root,),
            (worker_test_resources.source_root,),
            worker_test_resources.source_root,
        ),
    )
    workers: list[WorkerClient] = []

    def tracked_client(
        launcher: ProcessLauncher,
        resource_policy: WorkerResourcePolicy,
        timeout_policy: WorkerTimeoutPolicy,
        *,
        required_capabilities: tuple[CapabilityDescriptor, ...],
    ) -> WorkerClient:
        client = WorkerClient(
            launcher, resource_policy, timeout_policy, required_capabilities=required_capabilities
        )
        workers.append(client)
        return client

    monkeypatch.setattr(stage_module, "WorkerClient", tracked_client)
    monkeypatch.setattr(preview_module, "WorkerClient", tracked_client)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    gate = HeavyOperationCoordinator(policy.maximum_heavy_operation_concurrency)
    resources = desktop.DesktopApplicationResourceContext(policy, gate)
    original = tmp_path / "synthetic-compression-review.pdf"
    make_image_baseline(original, encoding="jpeg", mutate=_raster_review_target)
    original_bytes = original.read_bytes()
    original_digest = hashlib.sha256(original_bytes).hexdigest()
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
        id_factory=_SequentialUuidFactory(0x25500000),
        temp_id_factory=_SequentialUuidFactory(0x35500000),
    )
    imported = SourceIngestService(sources, _PdfInspector()).import_source(
        _CASE_ID, original, "PRIVATE-COMPRESSION-REVIEW-CANARY.pdf"
    )
    template = SafeTemplateLoader(
        _Repository(_byte_snapshot(_template_bytes(_single_requirement)))
    ).load(CANONICAL_KEY, as_of=date(2026, 9, 5))
    definition = checklist_definition_from_template(template)
    repository = WindowsManifestRepository(manager)
    loaded = repository.load(_CASE_ID).snapshot
    document = loaded.document
    target = len(original_bytes) // 2
    document.update(
        {
            "revision": loaded.revision + 1,
            "updated_at": "2026-09-05T10:03:00Z",
            "template_ref": definition.binding.to_manifest(),
            "assignments": [_assignment(0, "application-form", imported.page_ids)],
            "confirmations": [_confirmation(0)],
            "constraints": {
                "constraints_version": 1,
                "allowed_output_modes": ["combined_pdf"],
                "encryption_policy": "must_be_unencrypted",
                "maximum_file_bytes": target,
                "maximum_total_bytes": target,
                "maximum_pages_per_file": 2,
            },
        }
    )
    snapshot = repository.save(
        _CASE_ID,
        document,
        expected_revision=loaded.revision,
        expected_sha256=loaded.sha256,
        expected_cas_token=loaded.cas_token,
    ).snapshot
    mode = PortalOutputMode.COMBINED_PDF
    plan = build_package_export_plan(
        snapshot, definition, portal_constraint_profile_from_template(template), mode
    )
    preflight = evaluate_package_review(snapshot, template, definition, mode, plan)
    assert not preflight.report.blocking_errors
    destination = tmp_path / "destination"
    destination.mkdir()
    layout = manager.layout(_CASE_ID)
    manifest_before = layout.manifest.read_bytes()
    case_files_before = {path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")}
    committed_source = layout.originals / f"{imported.source_id}.bin"
    service = desktop.build_windows_compression_review_service(
        sources,
        _UnusedRasterizer(),
        resources=resources,
        preview_config=desktop._desktop_worker_config(policy),
    )
    progress: list[tuple[int, int]] = []
    try:
        state = service.prepare(
            snapshot,
            template,
            definition,
            preflight,
            plan,
            destination,
            color_choice=TargetSizeColorChoice.ALLOW_GRAYSCALE,
            human_review_confirmed=True,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda done, total: progress.append((done, total)),
        )
        assert state.export_allowed
        assert state.decision.terminal is TargetSizeSearchTerminal.SATISFIED
        assert state.decision.selected_profile is PdfImageStageProfile.GRAYSCALE_300_DPI_Q90
        assert state.current_pdf_byte_sizes[0] > target >= state.proposed_pdf_byte_sizes[0]
        assert state.required_file_bytes == (target,) and state.required_total_bytes == target
        assert len(state.affected_pages) == 1
        page = state.affected_pages[0]
        assert page.images[0].original_color_mode is PdfImageColorMode.RGB
        assert page.images[0].proposed_color_mode is PdfImageColorMode.GRAYSCALE
        assert page.images[0].proposed_effective_dpi_x_milli == 300_000
        assert page.images[0].jpeg_quality == 90
        assert workers and all(
            worker.query().phase is WorkerLifecyclePhase.STOPPED for worker in workers
        )
        assert gate.snapshot().active_operations == 0
        assert not (destination / plan.pdfs[0].filename).exists()
        active = service._active
        assert active is not None
        report = active.prepared.package.report.pdfs[0].base_report
        with active.prepared.package.open_pdf(
            page.output_key, deadline=lambda: False, cancellation=lambda: False
        ) as reader:
            assert reader.readable() and not reader.writable()
            candidate_digest = hashlib.sha256(reader.read()).hexdigest()
        assert candidate_digest == report.output_sha256
        prepared_worker_count = len(workers)
        comparison = service.preview_page(
            state.review_id,
            page.output_key,
            page.page_index,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        assert len(workers) == prepared_worker_count + 2
        assert all(worker.query().phase is WorkerLifecyclePhase.STOPPED for worker in workers)
        assert gate.snapshot().active_operations == 0
        assert not (destination / plan.pdfs[0].filename).exists()
        assert (comparison.before.width_pixels, comparison.before.height_pixels) == (
            comparison.proposed.width_pixels,
            comparison.proposed.height_pixels,
        )
        assert comparison.before.pixels != comparison.proposed.pixels
        assert comparison.before.width_pixels >= 800
        assert comparison.before.width_pixels * comparison.before.height_pixels <= min(
            policy.maximum_preview_pixels_per_page, policy.thumbnail_preview_cache_bytes // 32
        )
        # The embedded image changes color; the blue vector border deliberately stays blue.
        with Image.frombytes(
            "RGBA",
            (comparison.proposed.width_pixels, comparison.proposed.height_pixels),
            comparison.proposed.pixels,
        ) as proposed_image:
            rgb = proposed_image.getpixel((proposed_image.width // 3, proposed_image.height // 2))
            assert isinstance(rgb, tuple) and rgb[0] == rgb[1] == rgb[2]
        summary = service.commit(
            state.review_id,
            repository.load(_CASE_ID).snapshot,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda done, total: progress.append((done, total)),
        )
        assert summary.status is PackageSummaryStatus.COMMITTED
        output = destination / plan.pdfs[0].filename
        payload = output.read_bytes()
        assert len(payload) == report.output_byte_size
        assert hashlib.sha256(payload).hexdigest() == candidate_digest
        assert {path.name for path in destination.iterdir()} == {output.name}
        assert service._active is None and len(workers) == prepared_worker_count + 2
        with pikepdf.Pdf.open(output) as result, pikepdf.Pdf.open(original) as source:
            assert not result.check_pdf_syntax()
            assert (
                result.pages[0].obj.Resources.XObject.Pixels.ColorSpace == pikepdf.Name.DeviceGray
            )
            assert len(result.pages) == 2
            for actual, expected in zip(result.pages, source.pages, strict=True):
                assert actual.obj.Contents.read_bytes() == expected.obj.Contents.read_bytes()
                assert tuple(actual.obj.MediaBox) == tuple(expected.obj.MediaBox)
        for worker in workers:
            accounting = worker.resource_snapshot()
            assert accounting is not None
            assert accounting.process_memory_limit_bytes == policy.worker_process_memory_bytes
            assert accounting.job_memory_limit_bytes == policy.worker_job_memory_bytes
            assert (
                accounting.peak_process_memory_used_bytes <= accounting.process_memory_limit_bytes
            )
            assert accounting.peak_job_memory_used_bytes <= accounting.job_memory_limit_bytes
        assert gate.snapshot().maximum_observed_concurrency == 1
        assert hashlib.sha256(original.read_bytes()).hexdigest() == original_digest
        assert hashlib.sha256(committed_source.read_bytes()).hexdigest() == original_digest
        assert layout.manifest.read_bytes() == manifest_before
        assert {
            path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")
        } == case_files_before
        return CompressionReviewEvidence(state, comparison, original_bytes, payload)
    finally:
        service.discard()
        for worker in workers:
            worker.terminate()
