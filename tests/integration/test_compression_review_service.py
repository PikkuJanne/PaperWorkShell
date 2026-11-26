"""Bound private compression review through synthetic verified producer ports."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest

import paperworkshell.export.compression_review as review_module
from paperworkshell.domain.compression_review import CompressionPageChange, CompressionReviewState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportVerificationReport,
    build_pdf_export_plan_for_pages,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetAnalysis,
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImagePageAnalysis,
    PdfImageStageOutcome,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheStatus,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
    ResolvedPdfThumbnailRequest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1, ResourcePolicy
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.export.compression_review import (
    CompressionProducerPort,
    WindowsCompressionReviewService,
)
from paperworkshell.export.package_export import PackageExportError, PackageExportErrorCode
from paperworkshell.export.target_size_export import PreparedTargetSizePackage
from paperworkshell.preflight.package_planner import PackageExportPlan, PlannedPackagePdf
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.export_destination import WindowsExportDestination
from tests.integration.test_package_export import _Candidate
from tests.integration.test_target_size_package_export import _Stages
from tests.unit.test_package_summary import SummaryInputs, summary_inputs


class ReviewProducer(_Stages):
    @property
    def baseline_analyses(self) -> tuple[tuple[int, PdfImageAnalysis], ...]:
        return tuple(
            (
                pdf.position,
                PdfImageAnalysis(
                    hashlib.sha256(b"x" * self.sizes[0]).hexdigest(),
                    self.sizes[0],
                    len(pdf.page_ids),
                    (
                        PdfImageAssetAnalysis(
                            0,
                            (0,),
                            600,
                            480,
                            600_000,
                            600_000,
                            PdfImageColorMode.RGB,
                            self.sizes[0] - 64,
                            PdfImageEligibility.ELIGIBLE,
                        ),
                    ),
                    tuple(
                        PdfImagePageAnalysis(
                            n,
                            (0,) if n == 0 else (),
                            self.sizes[0] - 64 if n == 0 else 0,
                            1_000_000 if n == 0 else 0,
                        )
                        for n in range(len(pdf.page_ids))
                    ),
                ),
            )
            for pdf in self.plan.pdfs
        )

    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> _Candidate:
        candidate = super().produce(
            snapshot, planned_pdf, deadline=deadline, cancellation=cancellation, progress=progress
        )
        profile = self.profiles[-1]
        if profile is not None and profile.allows_grayscale:
            self._stage_reports = tuple(
                replace(
                    report,
                    assets=tuple(
                        replace(asset, color_mode=PdfImageColorMode.GRAYSCALE)
                        if asset.outcome is PdfImageStageOutcome.REENCODED
                        else asset
                        for asset in report.assets
                    ),
                )
                for report in self._stage_reports
            )
        return candidate


class Renderer:
    def __init__(self) -> None:
        self.reservations: list[int] = []
        self.wrong_digest = False
        self.fail = False

    def render_comparison(
        self,
        snapshot: ManifestSnapshot,
        change: CompressionPageChange,
        proposed_source: BinaryIO,
        proposed_report: PdfExportVerificationReport,
        *,
        reserved_staged_bytes: int,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> tuple[PdfThumbnailResult, PdfThumbnailResult]:
        self.reservations.append(reserved_staged_bytes)
        if self.fail:
            raise RuntimeError("PRIVATE DOCUMENT NAME AND TEXT")
        assert hashlib.sha256(proposed_source.read()).hexdigest() == proposed_report.output_sha256
        plan = build_pdf_export_plan_for_pages(snapshot, (change.page_id,))
        original = plan.sources[0]
        before = _preview(
            snapshot.case_id,
            change.page_id,
            original.source_id,
            original.sha256,
            original.byte_size,
            plan.pages[0].source_page_index,
            PdfRenderRotation(int(plan.pages[0].additional_rotation)),
            maximum_width_pixels,
            maximum_height_pixels,
        )
        proposed = _preview(
            snapshot.case_id,
            change.page_id,
            uuid4(),
            "f" * 64 if self.wrong_digest else proposed_report.output_sha256,
            proposed_report.output_byte_size,
            change.page_index,
            PdfRenderRotation.DEGREES_0,
            maximum_width_pixels,
            maximum_height_pixels,
        )
        return before, proposed


def _preview(
    case_id: UUID,
    page_id: UUID,
    source_id: UUID,
    digest: str,
    size: int,
    page_index: int,
    rotation: PdfRenderRotation,
    width: int,
    height: int,
) -> PdfThumbnailResult:
    request = PdfThumbnailRequest(case_id, page_id, width, height, rotation)
    resolved = ResolvedPdfThumbnailRequest(
        request, source_id, page_index, 4, size, digest, "e" * 64
    )
    snapshot = PdfThumbnailSnapshot(
        case_id, page_id, 2, 2, 8, b"\xff" * 16, PdfRenderRotation.DEGREES_0, rotation
    )
    return PdfThumbnailResult(
        request, resolved.cache_key, snapshot, PdfThumbnailCacheStatus.GENERATED
    )


def _service(
    inputs: SummaryInputs, renderer: Renderer, *, policy: ResourcePolicy | None = None
) -> WindowsCompressionReviewService:
    assert inputs.plan is not None
    plan: PackageExportPlan = inputs.plan

    @contextmanager
    def producer() -> Iterator[CompressionProducerPort]:
        yield ReviewProducer(plan, (1000, 600, 500, 400))

    if policy is None:
        policy = replace(CONSTRAINED_RESOURCE_POLICY_V1, minimum_export_free_space_reserve_bytes=1)
    return WindowsCompressionReviewService(
        producer, WindowsExportDestination(minimum_free_space_reserve_bytes=1), policy, renderer
    )


def _prepare(
    service: WindowsCompressionReviewService,
    inputs: SummaryInputs,
    path: Path,
    color_choice: TargetSizeColorChoice = TargetSizeColorChoice.PRESERVE,
) -> CompressionReviewState:
    assert inputs.plan is not None
    return service.prepare(
        inputs.snapshot,
        inputs.template,
        inputs.definition,
        inputs.preflight,
        inputs.plan,
        path,
        color_choice=color_choice,
        human_review_confirmed=True,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _a, _b: None,
    )


def test_review_exposes_actual_measurements_and_commit_saves_exact_previewed_candidate(
    tmp_path: Path,
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    renderer = Renderer()
    service = _service(inputs, renderer)
    state = _prepare(service, inputs, tmp_path)
    assert state.current_pdf_byte_sizes == (1000,) and state.proposed_pdf_byte_sizes == (600,)
    assert state.required_file_bytes == (600,) and state.export_allowed
    assert len(state.affected_pages) == 1
    page = state.affected_pages[0]
    assert page.images[0].original_effective_dpi_x_milli == 600_000
    assert page.images[0].proposed_effective_dpi_x_milli == 300_000
    preview = service.preview_page(
        state.review_id,
        page.output_key,
        page.page_index,
        deadline=lambda: False,
        cancellation=lambda: False,
    )
    assert preview.before.page_id == page.page_id == preview.proposed.page_id
    assert renderer.reservations == [600]
    assert inputs.plan is not None and not (tmp_path / inputs.plan.pdfs[0].filename).exists()
    summary = service.commit(
        state.review_id,
        inputs.snapshot,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _a, _b: None,
    )
    assert summary.saved_file_count == 1
    assert (tmp_path / inputs.plan.pdfs[0].filename).read_bytes() == b"x" * 600
    with pytest.raises(PackageExportError):
        service.commit(
            state.review_id,
            inputs.snapshot,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _a, _b: None,
        )


def test_grayscale_is_explicit_and_reprepare_invalidates_previous_candidate(tmp_path: Path) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    service = _service(inputs, Renderer())
    preserved = _prepare(service, inputs, tmp_path)
    assert preserved.affected_pages[0].images[0].proposed_color_mode is PdfImageColorMode.RGB
    gray = _prepare(service, inputs, tmp_path, TargetSizeColorChoice.ALLOW_GRAYSCALE)
    assert gray.review_id != preserved.review_id
    image = gray.affected_pages[0].images[0]
    assert image.original_color_mode is PdfImageColorMode.RGB
    assert image.proposed_color_mode is PdfImageColorMode.GRAYSCALE
    with pytest.raises(PackageExportError) as captured:
        service.preview_page(
            preserved.review_id,
            preserved.affected_pages[0].output_key,
            0,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert captured.value.code is PackageExportErrorCode.PLAN_STALE
    service.discard()
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize("wrong_digest", [True, False])
def test_preview_rejects_unbound_bytes_and_sanitizes_renderer_failure(
    tmp_path: Path, wrong_digest: bool
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    renderer = Renderer()
    renderer.wrong_digest = wrong_digest
    renderer.fail = not wrong_digest
    service = _service(inputs, renderer)
    state = _prepare(service, inputs, tmp_path)
    with pytest.raises(PackageExportError) as captured:
        service.preview_page(
            state.review_id,
            state.affected_pages[0].output_key,
            0,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert "PRIVATE" not in str(captured.value)
    assert captured.value.__context__ is None and captured.value.__cause__ is None
    service.discard()
    assert tuple(tmp_path.iterdir()) == ()


def test_unattainable_target_is_reviewable_but_cannot_commit(tmp_path: Path) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=399)
    service = _service(inputs, Renderer())
    state = _prepare(service, inputs, tmp_path)
    assert not state.export_allowed
    assert state.proposed_total_bytes == 400
    assert state.floor_dpi == 200 and state.floor_jpeg_quality == 80
    with pytest.raises(PackageExportError) as captured:
        service.commit(
            state.review_id,
            inputs.snapshot,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _a, _b: None,
        )
    assert captured.value.code is PackageExportErrorCode.PLAN_INVALID
    service.discard()
    assert tuple(tmp_path.iterdir()) == ()


def test_failed_review_presentation_retains_cleanup_owner_until_discard_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    service = _service(inputs, Renderer())
    calls: list[PreparedTargetSizePackage] = []
    real_close = PreparedTargetSizePackage.close

    def failed_presentation(*_args: object) -> None:
        raise RuntimeError("PRIVATE EVIDENCE FAILURE")

    def fail_first_close(prepared: PreparedTargetSizePackage) -> None:
        calls.append(prepared)
        if len(calls) == 1:
            raise RuntimeError("PRIVATE DESTINATION CLEANUP FAILURE")
        real_close(prepared)

    monkeypatch.setattr(review_module, "_review_state", failed_presentation)
    monkeypatch.setattr(PreparedTargetSizePackage, "close", fail_first_close)
    with pytest.raises(PackageExportError) as captured:
        _prepare(service, inputs, tmp_path)
    assert "PRIVATE" not in str(captured.value)
    assert captured.value.__context__ is None
    assert tuple(tmp_path.iterdir())
    assert inputs.plan is not None and not (tmp_path / inputs.plan.pdfs[0].filename).exists()
    service.discard()
    assert len(calls) == 2 and calls[0] is calls[1]
    assert tuple(tmp_path.iterdir()) == ()


def test_review_admits_combined_live_evidence_before_building_presentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    measured: list[tuple[int, int, int]] = []
    real_review_state = review_module._review_state

    def measured_presentation(
        snapshot: ManifestSnapshot,
        prepared: PreparedTargetSizePackage,
        analyses: tuple[tuple[int, PdfImageAnalysis], ...],
        review_id: UUID,
    ) -> CompressionReviewState:
        state = real_review_state(snapshot, prepared, analyses, review_id)
        shared = (prepared.decision, prepared.image_stage_reports, prepared.package.report)
        presentation = (state, shared)
        producer_evidence = (analyses, shared)
        measured.append(
            (
                review_module._retained_bytes(presentation),
                review_module._retained_bytes(producer_evidence),
                review_module._retained_bytes((presentation, producer_evidence)),
            )
        )
        return state

    monkeypatch.setattr(review_module, "_review_state", measured_presentation)
    service = _service(inputs, Renderer())
    _prepare(service, inputs, tmp_path)
    service.discard()
    presentation_bytes, producer_bytes, combined_bytes = measured[0]
    cache_limit = max(presentation_bytes, producer_bytes)
    assert presentation_bytes <= cache_limit and producer_bytes <= cache_limit < combined_bytes
    constrained = replace(
        CONSTRAINED_RESOURCE_POLICY_V1,
        maximum_cache_bytes=cache_limit,
        minimum_export_free_space_reserve_bytes=1,
    )
    limited = _service(inputs, Renderer(), policy=constrained)
    try:
        with pytest.raises(PackageExportError) as captured:
            _prepare(limited, inputs, tmp_path)
    finally:
        limited.discard()
    assert captured.value.code is PackageExportErrorCode.LIMIT_EXCEEDED
    assert captured.value.context == "compression_review.evidence"
    assert captured.value.__context__ is None and captured.value.__cause__ is None
    assert len(measured) == 1  # The second presentation was rejected before construction.
    assert tuple(tmp_path.iterdir()) == ()
