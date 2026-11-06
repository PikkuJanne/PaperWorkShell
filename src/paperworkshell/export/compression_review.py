"""Private prepare, worker preview, and explicit commit for compression review."""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import BinaryIO, Never, Protocol, runtime_checkable
from uuid import UUID, uuid4

from paperworkshell.domain.checklist import ChecklistDefinition
from paperworkshell.domain.compression_review import (
    CompressionImageChange,
    CompressionPageChange,
    CompressionPagePreview,
    CompressionReviewState,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.package_summary import PackageSummary
from paperworkshell.domain.pdf_export import (
    PdfExportVerificationReport,
    build_pdf_export_plan_for_pages,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageStageOutcome,
    PdfImageStageRequest,
    validate_pdf_image_stage_report,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.target_size_search import (
    TargetSizeColorChoice,
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)
from paperworkshell.export.package_export import (
    PackageExportError,
    PackageExportErrorCode,
    PackageExportOutcome,
    PackageExportProgress,
    PackagePdfPipeline,
    reject_package_export,
)
from paperworkshell.export.package_summary import (
    build_package_summary,
    build_package_summary_recovery,
)
from paperworkshell.export.target_size_export import (
    PreparedTargetSizePackage,
    TargetSizePackageProducerPort,
    prepare_target_size_package,
)
from paperworkshell.preflight.engine import PreflightEvaluation
from paperworkshell.preflight.package_planner import PackageExportPlan, build_package_export_plan
from paperworkshell.preflight.portal_constraints import (
    PortalOutputMode,
    portal_constraint_profile_from_template,
)
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.templates.models import TemplateSnapshot

_SIZE_CODES = frozenset({"PORTAL_TOTAL_SIZE_EXCEEDED", "PORTAL_FILE_SIZE_EXCEEDED"})


@runtime_checkable
class CompressionProducerPort(TargetSizePackageProducerPort, Protocol):
    @property
    def baseline_analyses(self) -> tuple[tuple[int, PdfImageAnalysis], ...]: ...


class CompressionPreviewRenderer(Protocol):
    """Worker composition authenticates sources and returns validated pixel artifacts."""

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
    ) -> tuple[PdfThumbnailResult, PdfThumbnailResult]: ...


type CompressionProducerFactory = Callable[[], AbstractContextManager[CompressionProducerPort]]


@dataclass(slots=True, repr=False)
class _Review:
    state: CompressionReviewState
    snapshot: ManifestSnapshot
    template: TemplateSnapshot
    definition: ChecklistDefinition
    preflight: PreflightEvaluation
    prepared: PreparedTargetSizePackage
    destination_parent: Path


class WindowsCompressionReviewService:
    """Own one private candidate until approval, replacement, or explicit discard."""

    def __init__(
        self,
        producer_factory: CompressionProducerFactory,
        destination: WindowsExportDestination,
        policy: ResourcePolicy,
        renderer: CompressionPreviewRenderer,
        *,
        verified_export_observer: Callable[[ManifestSnapshot, PackageExportOutcome, Path], None]
        | None = None,
    ) -> None:
        if (
            not callable(producer_factory)
            or type(destination) is not WindowsExportDestination
            or type(policy) is not ResourcePolicy
            or destination.minimum_free_space_reserve_bytes
            != policy.minimum_export_free_space_reserve_bytes
            or not callable(getattr(renderer, "render_comparison", None))
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID, "compression_review.compose"
            )
        self._producer_factory = producer_factory
        self._destination = destination
        self._policy = policy
        self._renderer = renderer
        self._active: _Review | None = None
        self._pending_cleanup: PreparedTargetSizePackage | None = None
        self._verified_export_observer = verified_export_observer

    def prepare(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        plan: PackageExportPlan,
        destination_parent: Path,
        *,
        color_choice: TargetSizeColorChoice,
        human_review_confirmed: bool,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> CompressionReviewState:
        """Measure the bounded search without publishing any destination file."""

        self.discard()
        prepared: PreparedTargetSizePackage | None = None
        try:
            build_package_summary(snapshot, template, definition, preflight, plan=plan)
            if (
                type(color_choice) is not TargetSizeColorChoice
                or human_review_confirmed is not True
                or any(
                    finding.code not in _SIZE_CODES for finding in preflight.report.blocking_errors
                )
                or not isinstance(destination_parent, Path)
                or not destination_parent.is_absolute()
            ):
                reject_package_export(
                    PackageExportErrorCode.PLAN_INVALID, "compression_review.request"
                )
            rebuilt = build_package_export_plan(
                snapshot,
                definition,
                portal_constraint_profile_from_template(template),
                plan.mode,
                destination_parent_utf16_units=len(str(destination_parent).encode("utf-16-le"))
                // 2,
            )
            if rebuilt != plan:
                reject_package_export(PackageExportErrorCode.PLAN_STALE, "compression_review.plan")
            search = TargetSizeSearchPolicy.for_resource_policy(
                self._policy, color_choice=color_choice
            )

            def report_progress(value: PackageExportProgress) -> None:
                progress(value.completed_pages, value.total_pages)

            with self._producer_factory() as producer:
                if not isinstance(producer, CompressionProducerPort):
                    reject_package_export(
                        PackageExportErrorCode.MODEL_INVALID, "compression_review.producer"
                    )
                prepared = prepare_target_size_package(
                    snapshot,
                    plan,
                    destination_parent,
                    producer,
                    self._destination,
                    self._policy,
                    search_policy=search,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=report_progress,
                )
                # Take cleanup ownership before evidence presentation or worker
                # runtime shutdown can fail. This slot never authorizes export.
                self._pending_cleanup = prepared
                analyses = producer.baseline_analyses
                review_id = uuid4()
                evidence = (
                    analyses,
                    prepared.decision,
                    prepared.image_stage_reports,
                    prepared.package.report,
                    review_id,
                )
                # Original analysis remains live in the producer while the
                # presentation is built. Admit their combined footprint first.
                if (
                    _retained_bytes(evidence) + _presentation_bytes(prepared)
                    > self._policy.maximum_cache_bytes
                ):
                    reject_package_export(
                        PackageExportErrorCode.LIMIT_EXCEEDED, "compression_review.evidence"
                    )
                state = _review_state(snapshot, prepared, analyses, review_id)
                if _retained_bytes((evidence, state)) > self._policy.maximum_cache_bytes:
                    reject_package_export(
                        PackageExportErrorCode.LIMIT_EXCEEDED, "compression_review.evidence"
                    )
            self._active = _Review(
                state, snapshot, template, definition, preflight, prepared, destination_parent
            )
            self._pending_cleanup = None
            return state
        except BaseException:
            self.discard()
            _sanitize_failure("compression_review.prepare")

    def preview_page(
        self,
        review_id: UUID,
        output_key: str,
        page_index: int,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> CompressionPagePreview:
        """Render one original/proposed pair, bound to the exact retained candidate."""

        try:
            review = self._require_review(review_id)
            change = next(
                (
                    page
                    for page in review.state.affected_pages
                    if page.output_key == output_key and page.page_index == page_index
                ),
                None,
            )
            if change is None:
                reject_package_export(
                    PackageExportErrorCode.MODEL_INVALID, "compression_review.page"
                )
            report = review.prepared.package.report.pdfs[change.output_position - 1]
            maximum_pixels = min(
                self._policy.maximum_preview_pixels_per_page,
                self._policy.thumbnail_preview_cache_bytes // 32,
            )
            scale = min(1.0, math.sqrt(maximum_pixels / (1600 * 2000)))
            width, height = max(1, int(1600 * scale)), max(1, int(2000 * scale))
            with review.prepared.package.open_pdf(
                output_key, deadline=deadline, cancellation=cancellation
            ) as selected:
                results = self._renderer.render_comparison(
                    review.snapshot,
                    change,
                    selected,
                    report.base_report,
                    reserved_staged_bytes=review.prepared.package.staged_byte_size,
                    maximum_width_pixels=width,
                    maximum_height_pixels=height,
                    deadline=deadline,
                    cancellation=cancellation,
                )
            if (
                type(results) is not tuple
                or len(results) != 2
                or any(type(value) is not PdfThumbnailResult for value in results)
            ):
                reject_package_export(
                    PackageExportErrorCode.PDF_FAILED, "compression_review.preview_result"
                )
            before, proposed = results
            original_plan = build_pdf_export_plan_for_pages(review.snapshot, (change.page_id,))
            original_page = original_plan.pages[0]
            original_source = next(
                source
                for source in original_plan.sources
                if source.source_id == original_page.source_id
            )
            for result in results:
                if (
                    result.request.case_id != review.snapshot.case_id
                    or result.request.page_id != change.page_id
                    or result.request.maximum_width_pixels > width
                    or result.request.maximum_height_pixels > height
                    or result.snapshot.width_pixels * result.snapshot.height_pixels > maximum_pixels
                ):
                    reject_package_export(
                        PackageExportErrorCode.PDF_FAILED, "compression_review.preview_binding"
                    )
            if (
                before.cache_key.source_id != original_source.source_id
                or before.cache_key.source_sha256 != original_source.sha256
                or before.cache_key.source_byte_size != original_source.byte_size
                or before.cache_key.source_page_index != original_page.source_page_index
                or int(before.cache_key.additional_rotation)
                != int(original_page.additional_rotation)
                or proposed.cache_key.source_sha256 != report.base_report.output_sha256
                or proposed.cache_key.source_byte_size != report.base_report.output_byte_size
                or proposed.cache_key.source_page_index != change.page_index
                or proposed.cache_key.additional_rotation is not PdfRenderRotation.DEGREES_0
                or sum(len(value.snapshot.pixels) for value in results)
                > self._policy.thumbnail_preview_cache_bytes // 2
            ):
                reject_package_export(
                    PackageExportErrorCode.PDF_FAILED, "compression_review.preview_binding"
                )
            return CompressionPagePreview(
                review_id, output_key, page_index, before.snapshot, proposed.snapshot
            )
        except BaseException:
            _sanitize_failure("compression_review.preview")

    def commit(
        self,
        review_id: UUID,
        snapshot: ManifestSnapshot,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> PackageSummary:
        """Commit the approved candidate; preserve exact partial-publication facts."""

        review = self._require_review(review_id)
        if not review.state.export_allowed:
            reject_package_export(PackageExportErrorCode.PLAN_INVALID, "compression_review.blocked")
        outcome = None
        try:
            outcome = review.prepared.commit(
                snapshot,
                deadline=deadline,
                cancellation=cancellation,
                progress=lambda value: progress(value.completed_pages, value.total_pages),
            ).package
        except BaseException:
            self.discard()
            _sanitize_failure("compression_review.commit")
        try:
            self.discard()
            if outcome.successful and self._verified_export_observer is not None:
                self._verified_export_observer(review.snapshot, outcome, review.destination_parent)
            return build_package_summary(
                review.snapshot,
                review.template,
                review.definition,
                review.preflight,
                plan=outcome.report.plan,
                outcome=outcome,
            )
        except BaseException:
            return build_package_summary_recovery(outcome)

    def discard(self) -> None:
        """Release the single private review on replacement, case close, or cancellation."""

        try:
            active = self._active
            if active is not None:
                active.prepared.close()
                self._active = None
            pending = self._pending_cleanup
            if pending is not None:
                pending.close()
                self._pending_cleanup = None
        except BaseException:
            _sanitize_failure("compression_review.cleanup")

    def _require_review(self, review_id: UUID) -> _Review:
        review = self._active
        if type(review_id) is not UUID or review is None or review.state.review_id != review_id:
            reject_package_export(PackageExportErrorCode.PLAN_STALE, "compression_review.identity")
        return review

    def __repr__(self) -> str:
        return f"WindowsCompressionReviewService(active={self._active is not None})"


def _review_state(
    snapshot: ManifestSnapshot,
    prepared: PreparedTargetSizePackage,
    analyses: tuple[tuple[int, PdfImageAnalysis], ...],
    review_id: UUID,
) -> CompressionReviewState:
    package = prepared.package.report
    stage_pdfs = tuple(
        pdf for pdf in package.pdfs if pdf.pipeline is PackagePdfPipeline.IMAGE_STAGE
    )
    if (
        type(analyses) is not tuple
        or len(analyses) != len(stage_pdfs)
        or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not int
            or type(item[1]) is not PdfImageAnalysis
            for item in analyses
        )
        or tuple(item[0] for item in analyses) != tuple(pdf.position for pdf in stage_pdfs)
    ):
        reject_package_export(PackageExportErrorCode.PDF_FAILED, "compression_review.analyses")
    pages: list[CompressionPageChange] = []
    for pdf, report, (_, analysis) in zip(
        stage_pdfs, prepared.image_stage_reports, analyses, strict=True
    ):
        request = (
            PdfImageStageRequest()
            if report.profile is None
            else PdfImageStageRequest(
                report.profile,
                tuple(asset.asset_id for asset in report.assets),
                report.baseline_sha256,
                report.semantic_fingerprint,
            )
        )
        validate_pdf_image_stage_report(request, analysis, report)
        impacts = {asset.asset_id: asset for asset in report.assets}
        for page in report.pages:
            changed = tuple(
                impacts[asset_id]
                for asset_id in page.selected_asset_ids
                if impacts[asset_id].outcome is PdfImageStageOutcome.REENCODED
            )
            if not changed:
                continue
            assert report.profile is not None
            images = tuple(
                CompressionImageChange(
                    asset.original_width_pixels,
                    asset.original_height_pixels,
                    asset.output_width_pixels,
                    asset.output_height_pixels,
                    asset.original_effective_dpi_x_milli,
                    asset.original_effective_dpi_y_milli,
                    asset.output_effective_dpi_x_milli,
                    asset.output_effective_dpi_y_milli,
                    analysis.assets[asset.asset_id].color_mode,
                    asset.color_mode,
                    report.profile.jpeg_quality,
                )
                for asset in changed
            )
            pages.append(
                CompressionPageChange(
                    pdf.output_key,
                    pdf.position,
                    pdf.page_ids[page.page_index],
                    page.page_index,
                    images,
                )
            )
    first, last = prepared.decision.attempts[0], prepared.decision.attempts[-1]
    constraints = package.plan.active_constraints.values
    archive = package.plan.mode is PortalOutputMode.ZIP
    return CompressionReviewState(
        review_id,
        snapshot.case_id,
        snapshot.revision,
        prepared.decision.policy.color_choice,
        first.pdf_byte_sizes,
        last.pdf_byte_sizes,
        first.archive_byte_size,
        last.archive_byte_size,
        tuple(constraints.maximum_file_bytes for _ in package.pdfs),
        constraints.maximum_total_bytes,
        tuple(pages),
        prepared.decision,
        prepared.decision.terminal is TargetSizeSearchTerminal.SATISFIED,
        constraints.maximum_file_bytes if archive else None,
    )


def _presentation_bytes(prepared: PreparedTargetSizePackage) -> int:
    """Admit new record/container storage before building the presentation.

    Property values reference the already-counted reports and analysis. Charge
    the actual slotted record layouts, tuple slots, and the temporary page list;
    the review UUID is already included in the combined evidence graph.
    """

    image_count = 0
    page_count = 0
    for report in prepared.image_stage_reports:
        changed_pages: set[int] = set()
        for asset in report.assets:
            if asset.outcome is PdfImageStageOutcome.REENCODED:
                image_count += len(asset.page_indexes)
                changed_pages.update(asset.page_indexes)
        page_count += len(changed_pages)
    tuple_base = sys.getsizeof(())
    tuple_slot = sys.getsizeof((None,)) - tuple_base
    return (
        sys.getsizeof(object.__new__(CompressionReviewState))
        + page_count * sys.getsizeof(object.__new__(CompressionPageChange))
        + image_count * sys.getsizeof(object.__new__(CompressionImageChange))
        + (page_count + 2) * tuple_base  # Image, affected-page, and file-limit tuples.
        + (image_count + page_count + len(prepared.package.report.pdfs)) * tuple_slot
        + sys.getsizeof([None] * page_count) * 2  # Also cover list growth before conversion.
        + sys.getsizeof((None, None))  # Combined-graph accounting wrapper.
    )


def _retained_bytes(value: object, seen: set[int] | None = None) -> int:
    identities = set() if seen is None else seen
    if id(value) in identities:
        return 0
    identities.add(id(value))
    size = sys.getsizeof(value)
    if type(value) is UUID:
        return size + _retained_bytes(value.int, identities)
    if isinstance(value, tuple):
        return size + sum(_retained_bytes(item, identities) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return size + sum(
            _retained_bytes(getattr(value, member.name), identities) for member in fields(value)
        )
    return size


def _sanitize_failure(context: str) -> Never:
    error = sys.exception()
    if type(error) is PackageExportError:
        reject_package_export(error.code, error.context)
    code = getattr(getattr(error, "code", None), "value", "")
    if type(code) is str and code.endswith("CANCELLED"):
        reject_package_export(PackageExportErrorCode.CANCELLED, context)
    if type(code) is str and code.endswith("TIMED_OUT"):
        reject_package_export(PackageExportErrorCode.TIMED_OUT, context)
    if type(code) is str and code.endswith("LIMIT_EXCEEDED"):
        reject_package_export(PackageExportErrorCode.LIMIT_EXCEEDED, context)
    if type(code) is str and code.endswith("PLAN_STALE"):
        reject_package_export(PackageExportErrorCode.PLAN_STALE, context)
    reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, context)
