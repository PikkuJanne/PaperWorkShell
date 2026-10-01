"""Bounded per-output PDF candidates for package export.

The package orchestrator owns grouping, destination publication, and aggregate
constraint reporting.  This module owns only one exact planned PDF at a time:
it selects an existing verified worker pipeline without dropping manifest
intent, retains authenticated sources until the candidate context closes, and
returns the worker's concrete verification evidence.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from types import TracebackType
from typing import Final, Literal, Never, Protocol, Self, runtime_checkable
from uuid import UUID

from paperworkshell.app.flattened_pdf_export import (
    FlattenedPdfExportBroker,
    FlattenedPdfRasterizer,
    VerifiedFlattenedPdfCandidate,
)
from paperworkshell.app.ocr import (
    OcrLanguageAvailability,
    built_in_ocr_language_availability,
)
from paperworkshell.app.searchable_pdf_export import (
    SearchablePdfExportBroker,
    VerifiedSearchablePdfCandidate,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchStore,
    CommittedSearchablePdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportProgress,
    PdfExportVerificationReport,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.searchable_pdf_export import (
    DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfVerificationReport,
    build_searchable_pdf_export_plan_for_pages,
)
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterRequest,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourcePolicyError,
    SignedSourceSignatureState,
    require_signed_source_modification_confirmation,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    build_flattened_pdf_export_plan_for_pages,
    required_visible_signature_assets_for_pages,
)
from paperworkshell.overlays.models import OverlayAssetRecord
from paperworkshell.overlays.rendering import OverlayRasterAsset, OverlayRenderLimits
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.preflight.package_planner import PlannedPackagePdf

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_OUTPUT_KEY: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_PLANNING_PIXEL_SHA256: Final = hashlib.sha256(b"\0\0\0\0").hexdigest()
_FLATTENED_FALLBACK_CODES: Final = frozenset(
    {
        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        FlattenedPdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
        FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED,
    }
)


class PackagePdfPipeline(StrEnum):
    """Existing verified worker pipeline selected for one planned PDF."""

    FLATTENED = "flattened"
    SEARCHABLE = "searchable"


class PackagePdfCandidateErrorCode(StrEnum):
    """Closed registry of content-free per-output candidate failures."""

    MODEL_INVALID = "PACKAGE_PDF_CANDIDATE_MODEL_INVALID"
    UNSUPPORTED_OVERLAY_TRANSFORM = "PACKAGE_PDF_UNSUPPORTED_OVERLAY_TRANSFORM"
    OCR_LANGUAGE_UNAVAILABLE = "PACKAGE_PDF_OCR_LANGUAGE_UNAVAILABLE"
    LIMIT_EXCEEDED = "PACKAGE_PDF_CANDIDATE_LIMIT_EXCEEDED"
    SOURCE_CHANGED = "PACKAGE_PDF_CANDIDATE_SOURCE_CHANGED"
    CANCELLED = "PACKAGE_PDF_CANDIDATE_CANCELLED"
    TIMED_OUT = "PACKAGE_PDF_CANDIDATE_TIMED_OUT"
    RESULT_INVALID = "PACKAGE_PDF_CANDIDATE_RESULT_INVALID"
    SESSION_STATE_INVALID = "PACKAGE_PDF_CANDIDATE_SESSION_STATE_INVALID"
    INTERNAL_ERROR = "PACKAGE_PDF_CANDIDATE_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[PackagePdfCandidateErrorCode, str]] = {
    PackagePdfCandidateErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid package-PDF candidate data."
    ),
    PackagePdfCandidateErrorCode.UNSUPPORTED_OVERLAY_TRANSFORM: (
        "The selected pages combine active visual overlays with source or scan processing "
        "that this export version cannot safely perform."
    ),
    PackagePdfCandidateErrorCode.OCR_LANGUAGE_UNAVAILABLE: (
        "The selected OCR languages are not installed and verified. Review OCR language "
        "settings before retrying."
    ),
    PackagePdfCandidateErrorCode.LIMIT_EXCEEDED: (
        "This package PDF exceeds a configured processing limit."
    ),
    PackagePdfCandidateErrorCode.SOURCE_CHANGED: (
        "A selected source or placed signature image changed before export completed."
    ),
    PackagePdfCandidateErrorCode.CANCELLED: (
        "Package PDF creation was cancelled before completion."
    ),
    PackagePdfCandidateErrorCode.TIMED_OUT: (
        "Package PDF creation exceeded its configured time limit."
    ),
    PackagePdfCandidateErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid verified package-PDF candidate."
    ),
    PackagePdfCandidateErrorCode.SESSION_STATE_INVALID: (
        "The package-PDF candidate is not active or has already been used."
    ),
    PackagePdfCandidateErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not create the package PDF safely."
    ),
}


@dataclass(slots=True, repr=False)
class PackagePdfCandidateError(ValueError):
    """Stable failure retaining no names, paths, identifiers, digests, or content."""

    code: PackagePdfCandidateErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackagePdfCandidateErrorCode:
            self.code = PackagePdfCandidateErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "package.pdf_candidate"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PackagePdfCandidateError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_package_pdf_candidate(
    code: PackagePdfCandidateErrorCode,
    context: str,
) -> Never:
    """Raise one sanitized per-output failure without an exception cause chain."""

    error = PackagePdfCandidateError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class PackagePdfCandidateReport:
    """One output's worker-verified PDF facts bound to its package position."""

    output_key: str = field(repr=False)
    position: int
    page_ids: tuple[UUID, ...] = field(repr=False)
    pipeline: PackagePdfPipeline
    base_report: PdfExportVerificationReport = field(repr=False)
    pipeline_report: FlattenedPdfExportVerificationReport | SearchablePdfVerificationReport = field(
        repr=False
    )

    def __post_init__(self) -> None:
        flattened = (
            self.pipeline is PackagePdfPipeline.FLATTENED
            and type(self.pipeline_report) is FlattenedPdfExportVerificationReport
            and self.pipeline_report.base_report == self.base_report
            and tuple(page.page_id for page in self.pipeline_report.plan.pages) == self.page_ids
        )
        searchable = (
            self.pipeline is PackagePdfPipeline.SEARCHABLE
            and type(self.pipeline_report) is SearchablePdfVerificationReport
            and self.pipeline_report.base_report == self.base_report
            and tuple(page.page_id for page in self.pipeline_report.plan.pages) == self.page_ids
        )
        if (
            type(self.output_key) is not str
            or not 1 <= len(self.output_key) <= 128
            or _OUTPUT_KEY.fullmatch(self.output_key) is None
            or type(self.position) is not int
            or self.position < 1
            or type(self.page_ids) is not tuple
            or not self.page_ids
            or any(type(value) is not UUID or value.version != 4 for value in self.page_ids)
            or len(set(self.page_ids)) != len(self.page_ids)
            or type(self.pipeline) is not PackagePdfPipeline
            or type(self.base_report) is not PdfExportVerificationReport
            or not (flattened or searchable)
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID,
                "package.candidate_report",
            )

    @property
    def page_count(self) -> int:
        return self.base_report.page_count

    def __repr__(self) -> str:
        return (
            "PackagePdfCandidateReport("
            f"position={self.position!r}, pipeline={self.pipeline.value!r}, "
            f"page_count={self.page_count!r}, "
            f"output_byte_size={self.base_report.output_byte_size!r})"
        )


class PackagePdfProgressSink(Protocol):
    """Receive only existing bounded numeric PDF progress records."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


@runtime_checkable
class PackagePdfCandidate(Protocol):
    """Single-use candidate retained with authenticated sources until close."""

    @property
    def report(self) -> PackagePdfCandidateReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PackagePdfSourceStore(
    CommittedPdfSourceBatchStore,
    CommittedSearchablePdfSourceBatchStore,
    Protocol,
):
    """One case store exposing the two existing authenticated batch views."""


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


class PackagePdfCandidateProducer:
    """Prepare one exact output at a time through an existing verified pipeline."""

    __slots__ = (
        "_active",
        "_active_lock",
        "_flattened_broker",
        "_ocr_languages",
        "_overlay_limits",
        "_policy",
        "_rasterizer",
        "_reserved_temporary_bytes",
        "_searchable_broker",
        "_sources",
    )

    def __init__(
        self,
        sources: PackagePdfSourceStore,
        rasterizer: FlattenedPdfRasterizer,
        flattened_broker: FlattenedPdfExportBroker,
        searchable_broker: SearchablePdfExportBroker,
        resource_policy: ResourcePolicy,
        *,
        ocr_languages: OcrLanguageAvailability | None = None,
    ) -> None:
        if (
            not isinstance(sources, PackagePdfSourceStore)
            or not isinstance(rasterizer, FlattenedPdfRasterizer)
            or not isinstance(flattened_broker, FlattenedPdfExportBroker)
            or not isinstance(searchable_broker, SearchablePdfExportBroker)
            or type(resource_policy) is not ResourcePolicy
            or (
                ocr_languages is not None and not isinstance(ocr_languages, OcrLanguageAvailability)
            )
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID,
                "package.producer",
            )
        self._sources = sources
        self._rasterizer = rasterizer
        self._flattened_broker = flattened_broker
        self._searchable_broker = searchable_broker
        self._policy = resource_policy
        self._overlay_limits = OverlayRenderLimits.from_resource_policy(resource_policy)
        self._ocr_languages = ocr_languages or built_in_ocr_language_availability()
        self._active_lock = Lock()
        self._active = False
        self._reserved_temporary_bytes = 0

    def reserve_temporary_bytes(self, byte_count: int) -> None:
        """Account for private package stages retained outside the next worker."""

        if type(byte_count) is not int or byte_count < 0:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID,
                "package.temp_reservation",
            )
        if byte_count >= self._policy.maximum_temporary_storage_bytes:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
                "package.temp_reservation",
            )
        with self._active_lock:
            if self._active:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.SESSION_STATE_INVALID,
                    "package.active_reservation",
                )
            self._reserved_temporary_bytes = byte_count

    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink = _ignore_progress,
    ) -> PackagePdfCandidate:
        """Return a lazy single-use context for one exact ordered PDF subset."""

        if (
            type(snapshot) is not ManifestSnapshot
            or type(planned_pdf) is not PlannedPackagePdf
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID,
                "package.produce",
            )
        _require_active(deadline, cancellation)
        assets = required_visible_signature_assets_for_pages(
            snapshot,
            planned_pdf.page_ids,
        )
        flattened_plan = self._try_flattened_plan(snapshot, planned_pdf, assets)
        if flattened_plan is not None:
            _confirm_flattened_sources(snapshot, flattened_plan)
            maximum_output_bytes = self._require_flattened_capacity(
                flattened_plan,
                len(assets),
            )
            return _PackagePdfCandidateSession(
                producer=self,
                snapshot=snapshot,
                planned_pdf=planned_pdf,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
                maximum_output_bytes=maximum_output_bytes,
                flattened_plan=flattened_plan,
                assets=assets,
            )

        searchable_plan = self._build_searchable_plan(snapshot, planned_pdf)
        _confirm_searchable_sources(snapshot, searchable_plan)
        maximum_output_bytes = self._require_searchable_capacity()
        return _PackagePdfCandidateSession(
            producer=self,
            snapshot=snapshot,
            planned_pdf=planned_pdf,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
            maximum_output_bytes=maximum_output_bytes,
            searchable_plan=searchable_plan,
        )

    def _try_flattened_plan(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        assets: tuple[OverlayAssetRecord, ...],
    ) -> FlattenedPdfExportPlan | None:
        try:
            return build_flattened_pdf_export_plan_for_pages(
                snapshot,
                planned_pdf.page_ids,
                {
                    asset.asset_id: OverlayRasterAsset(
                        asset_id=asset.asset_id,
                        width_pixels=1,
                        height_pixels=1,
                        row_stride_bytes=4,
                        byte_count=4,
                        pixel_sha256=_PLANNING_PIXEL_SHA256,
                    )
                    for asset in assets
                },
            )
        except FlattenedPdfExportError as error:
            if error.code not in _FLATTENED_FALLBACK_CODES:
                raise
            return None

    def _build_searchable_plan(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
    ) -> SearchablePdfExportPlan:
        try:
            capability_plan = build_searchable_pdf_export_plan_for_pages(
                snapshot,
                planned_pdf.page_ids,
                profile=DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
            )
        except SearchablePdfExportError as error:
            if (
                error.code is SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED
                and error.context == "searchable.overlay"
            ):
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.UNSUPPORTED_OVERLAY_TRANSFORM,
                    "package.pipeline_selection",
                )
            raise
        profile = self._select_searchable_profile()
        if profile == DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE:
            return capability_plan
        return build_searchable_pdf_export_plan_for_pages(
            snapshot,
            planned_pdf.page_ids,
            profile=profile,
        )

    def _select_searchable_profile(self) -> SearchablePdfExportProfile:
        try:
            selected = self._ocr_languages.require_selected_available()
        except OcrError as error:
            if type(error) is OcrError and error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.OCR_LANGUAGE_UNAVAILABLE,
                    "package.ocr_language",
                )
            if type(error) is OcrError and error.code is OcrErrorCode.MODEL_INVALID:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.MODEL_INVALID,
                    "package.ocr_language",
                )
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.ocr_language",
            )
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.ocr_language",
            )
        if type(selected) is not str:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.ocr_language",
            )
        try:
            return SearchablePdfExportProfile(ocr_language_set_id=selected)
        except SearchablePdfExportError:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.ocr_language",
            )

    def _require_flattened_capacity(
        self,
        plan: FlattenedPdfExportPlan,
        asset_count: int,
    ) -> int:
        try:
            maximum_output_bytes = self._flattened_broker.maximum_output_bytes
            maximum_input_references = self._flattened_broker.maximum_input_references
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.flattened_limits",
            )
        if (
            type(maximum_output_bytes) is not int
            or maximum_output_bytes < 1
            or maximum_output_bytes > self._policy.maximum_temporary_storage_bytes
            or type(maximum_input_references) is not int
            or maximum_input_references < 2
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.flattened_limits",
            )
        if 1 + len(plan.base_plan.sources) + asset_count > maximum_input_references:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
                "package.flattened_references",
            )
        return maximum_output_bytes

    def _require_searchable_capacity(self) -> int:
        try:
            maximum_output_bytes = self._searchable_broker.maximum_output_bytes
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.searchable_limits",
            )
        if (
            type(maximum_output_bytes) is not int
            or maximum_output_bytes < 1
            or maximum_output_bytes > self._policy.maximum_temporary_storage_bytes
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.searchable_limits",
            )
        return maximum_output_bytes

    def _claim(self) -> None:
        with self._active_lock:
            if self._active:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
                    "package.active_candidate",
                )
            self._active = True

    def _release(self) -> None:
        with self._active_lock:
            self._active = False

    def _rasterize_assets(
        self,
        case_id: UUID,
        assets: tuple[OverlayAssetRecord, ...],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> dict[UUID, SignatureImageOverlayRasterSource]:
        edge = min(
            self._overlay_limits.maximum_raster_width_pixels,
            self._overlay_limits.maximum_raster_height_pixels,
            math.isqrt(self._overlay_limits.maximum_raster_pixels),
            math.isqrt(self._overlay_limits.maximum_raster_bytes // 4),
        )
        if edge < 1:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
                "package.raster_limits",
            )
        selected: dict[UUID, SignatureImageOverlayRasterSource] = {}
        total_bytes = 0
        for asset in assets:
            _require_active(deadline, cancellation)
            source = self._rasterize_asset(
                case_id,
                asset,
                edge=edge,
                deadline=deadline,
                cancellation=cancellation,
            )
            total_bytes += source.metadata.byte_count
            if total_bytes > self._overlay_limits.maximum_total_raster_bytes:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
                    "package.raster_total",
                )
            selected[asset.asset_id] = source
        return selected

    def _rasterize_asset(
        self,
        case_id: UUID,
        asset: OverlayAssetRecord,
        *,
        edge: int,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> SignatureImageOverlayRasterSource:
        try:
            request = SignatureImageRasterRequest(
                case_id=case_id,
                asset_id=asset.asset_id,
                media_type=SignatureImageMediaType(asset.media_type.value),
                source_byte_count=asset.byte_size,
                source_sha256=asset.sha256,
                maximum_width_pixels=edge,
                maximum_height_pixels=edge,
            )
            source = self._rasterizer.rasterize_committed(
                request,
                deadline=deadline,
                cancellation=cancellation,
            )
            if type(source) is not SignatureImageOverlayRasterSource:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.RESULT_INVALID,
                    "package.raster_result",
                )
            metadata = source.metadata
            if (
                type(metadata) is not OverlayRasterAsset
                or metadata.asset_id != asset.asset_id
                or metadata.width_pixels > request.maximum_width_pixels
                or metadata.height_pixels > request.maximum_height_pixels
                or metadata.width_pixels * metadata.height_pixels
                > self._overlay_limits.maximum_raster_pixels
                or metadata.row_stride_bytes != metadata.width_pixels * 4
                or metadata.byte_count != metadata.row_stride_bytes * metadata.height_pixels
                or metadata.byte_count > self._overlay_limits.maximum_raster_bytes
            ):
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.RESULT_INVALID,
                    "package.raster_result",
                )
            return source
        except PackagePdfCandidateError:
            raise
        except SignatureImageError as error:
            _raise_signature_error(error)
        except ValueError:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID,
                "package.raster_request",
            )
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.rasterize",
            )


class _PackagePdfCandidateSession:
    """Lazy owner for exactly one source lease and one worker candidate."""

    __slots__ = (
        "_assets",
        "_cancellation",
        "_claimed",
        "_closed",
        "_deadline",
        "_entered",
        "_flattened_plan",
        "_maximum_output_bytes",
        "_planned_pdf",
        "_producer",
        "_progress",
        "_rasters",
        "_reader",
        "_report",
        "_searchable_plan",
        "_snapshot",
        "_stack",
        "_streamed_bytes",
    )

    def __init__(
        self,
        *,
        producer: PackagePdfCandidateProducer,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink,
        maximum_output_bytes: int,
        flattened_plan: FlattenedPdfExportPlan | None = None,
        assets: tuple[OverlayAssetRecord, ...] = (),
        searchable_plan: SearchablePdfExportPlan | None = None,
    ) -> None:
        if (flattened_plan is None) == (searchable_plan is None):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.session_plan",
            )
        self._producer = producer
        self._snapshot = snapshot
        self._planned_pdf = planned_pdf
        self._deadline = deadline
        self._cancellation = cancellation
        self._progress = progress
        self._maximum_output_bytes = maximum_output_bytes
        self._flattened_plan = flattened_plan
        self._searchable_plan = searchable_plan
        self._assets = assets
        self._rasters: dict[UUID, SignatureImageOverlayRasterSource] = {}
        self._stack = ExitStack()
        self._reader: Callable[[int], bytes] | None = None
        self._report: PackagePdfCandidateReport | None = None
        self._streamed_bytes = 0
        self._entered = False
        self._closed = False
        self._claimed = False

    @property
    def report(self) -> PackagePdfCandidateReport:
        if self._report is None:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID,
                "package.candidate_report_state",
            )
        return self._report

    def __enter__(self) -> Self:
        if self._entered or self._closed:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID,
                "package.candidate_enter",
            )
        self._producer._claim()
        self._claimed = True
        try:
            _require_active(self._deadline, self._cancellation)
            if self._flattened_plan is not None:
                self._enter_flattened(self._flattened_plan)
            elif self._searchable_plan is not None:
                self._enter_searchable(self._searchable_plan)
            else:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                    "package.session_plan",
                )
            self._entered = True
            return self
        except (
            PackagePdfCandidateError,
            PdfExportError,
            FlattenedPdfExportError,
            SearchablePdfExportError,
            SignedSourcePolicyError,
        ):
            self._abort_entry()
            raise
        except SourceIngestError as error:
            self._abort_entry()
            _raise_source_error(error)
        except BaseException:
            self._abort_entry()
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.candidate_enter",
            )

    def _enter_flattened(self, planning_plan: FlattenedPdfExportPlan) -> None:
        self._rasters = self._producer._rasterize_assets(
            self._snapshot.case_id,
            self._assets,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )
        plan = build_flattened_pdf_export_plan_for_pages(
            self._snapshot,
            self._planned_pdf.page_ids,
            {asset_id: source.metadata for asset_id, source in self._rasters.items()},
        )
        if plan.base_plan != planning_plan.base_plan or plan.pages != planning_plan.pages:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.flattened_plan_binding",
            )
        batch_context = self._producer._sources.borrow_committed_pdf_sources(
            plan.base_plan,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )
        retained_sources = self._stack.enter_context(batch_context)
        candidate_context = self._producer._flattened_broker.export(
            plan,
            retained_sources,
            self._rasters,
            deadline=self._deadline,
            cancellation=self._cancellation,
            progress=self._progress,
            **(
                {"reserved_temporary_bytes": self._producer._reserved_temporary_bytes}
                if self._producer._reserved_temporary_bytes
                else {}
            ),
        )
        candidate = self._stack.enter_context(candidate_context)
        report, reader = _require_flattened_candidate(
            plan,
            candidate,
            maximum_output_bytes=self._maximum_output_bytes,
        )
        self._reader = reader
        self._report = PackagePdfCandidateReport(
            output_key=self._planned_pdf.output_key,
            position=self._planned_pdf.position,
            page_ids=self._planned_pdf.page_ids,
            pipeline=PackagePdfPipeline.FLATTENED,
            base_report=report.base_report,
            pipeline_report=report,
        )

    def _enter_searchable(self, plan: SearchablePdfExportPlan) -> None:
        batch_context = self._producer._sources.borrow_committed_searchable_pdf_sources(
            plan,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )
        retained_sources = self._stack.enter_context(batch_context)
        candidate_context = self._producer._searchable_broker.export(
            plan,
            retained_sources,
            deadline=self._deadline,
            cancellation=self._cancellation,
            progress=self._progress,
            **(
                {"reserved_temporary_bytes": self._producer._reserved_temporary_bytes}
                if self._producer._reserved_temporary_bytes
                else {}
            ),
        )
        candidate = self._stack.enter_context(candidate_context)
        report, reader = _require_searchable_candidate(
            plan,
            candidate,
            maximum_output_bytes=self._maximum_output_bytes,
        )
        self._reader = reader
        self._report = PackagePdfCandidateReport(
            output_key=self._planned_pdf.output_key,
            position=self._planned_pdf.position,
            page_ids=self._planned_pdf.page_ids,
            pipeline=PackagePdfPipeline.SEARCHABLE,
            base_report=report.base_report,
            pipeline_report=report,
        )

    def read(self, size: int, /) -> bytes:
        if (
            type(size) is not int
            or size < 1
            or not self._entered
            or self._closed
            or self._reader is None
            or self._report is None
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID,
                "package.candidate_read",
            )
        try:
            payload = self._reader(size)
        except PackagePdfCandidateError, PdfExportError:
            raise
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.RESULT_INVALID,
                "package.candidate_read",
            )
        if type(payload) is not bytes or len(payload) > size:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.RESULT_INVALID,
                "package.candidate_read",
            )
        selected_total = self._streamed_bytes + len(payload)
        if selected_total > self._report.base_report.output_byte_size or (
            not payload and selected_total < self._report.base_report.output_byte_size
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.RESULT_INVALID,
                "package.candidate_stream",
            )
        self._streamed_bytes = selected_total
        return payload

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        cleanup_failed = False
        try:
            self._stack.__exit__(exception_type, exception, traceback)
        except BaseException:
            cleanup_failed = True
        finally:
            self._rasters = {}
            self._reader = None
            self._closed = True
            if self._claimed:
                self._producer._release()
                self._claimed = False
        if cleanup_failed and exception_type is None:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR,
                "package.candidate_close",
            )
        return False

    def _abort_entry(self) -> None:
        try:
            self._stack.close()
        except BaseException:
            pass
        self._rasters = {}
        self._reader = None
        self._closed = True
        if self._claimed:
            self._producer._release()
            self._claimed = False


def _confirm_flattened_sources(
    snapshot: ManifestSnapshot,
    plan: FlattenedPdfExportPlan,
) -> None:
    require_signed_source_modification_confirmation(
        snapshot,
        tuple(
            SignedSourceModificationSource(
                source.source_id,
                SignedSourceSignatureState(source.signature_state.value),
            )
            for source in plan.base_plan.sources
        ),
    )


def _confirm_searchable_sources(
    snapshot: ManifestSnapshot,
    plan: SearchablePdfExportPlan,
) -> None:
    require_signed_source_modification_confirmation(
        snapshot,
        tuple(
            SignedSourceModificationSource(
                source.source_id,
                SignedSourceSignatureState(source.signature_state.value),
            )
            for source in plan.sources
        ),
    )


def _require_flattened_candidate(
    plan: FlattenedPdfExportPlan,
    candidate: VerifiedFlattenedPdfCandidate,
    *,
    maximum_output_bytes: int,
) -> tuple[FlattenedPdfExportVerificationReport, Callable[[int], bytes]]:
    try:
        report = candidate.report
        reader = candidate.read
    except BaseException:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.RESULT_INVALID,
            "package.flattened_candidate",
        )
    if (
        type(report) is not FlattenedPdfExportVerificationReport
        or report.plan != plan
        or report.base_report.output_byte_size > maximum_output_bytes
        or not callable(reader)
    ):
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.RESULT_INVALID,
            "package.flattened_candidate",
        )
    return report, reader


def _require_searchable_candidate(
    plan: SearchablePdfExportPlan,
    candidate: VerifiedSearchablePdfCandidate,
    *,
    maximum_output_bytes: int,
) -> tuple[SearchablePdfVerificationReport, Callable[[int], bytes]]:
    try:
        report = candidate.report
        reader = candidate.read
    except BaseException:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.RESULT_INVALID,
            "package.searchable_candidate",
        )
    if (
        type(report) is not SearchablePdfVerificationReport
        or report.plan != plan
        or report.base_report.output_byte_size > maximum_output_bytes
        or not callable(reader)
    ):
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.RESULT_INVALID,
            "package.searchable_candidate",
        )
    return report, reader


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.INTERNAL_ERROR,
            "package.callback",
        )
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.INTERNAL_ERROR,
            "package.callback",
        )
    if cancelled:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.CANCELLED,
            "package.cancelled",
        )
    if expired:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.TIMED_OUT,
            "package.deadline",
        )


def _raise_signature_error(error: SignatureImageError) -> Never:
    if error.code in {
        SignatureImageErrorCode.SOURCE_CHANGED,
        SignatureImageErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.ASSET_UNKNOWN,
        SignatureImageErrorCode.CASE_CHANGED,
    }:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.SOURCE_CHANGED,
            "package.signature_raster",
        )
    if error.code in {
        SignatureImageErrorCode.LIMIT_EXCEEDED,
        SignatureImageErrorCode.CAPACITY_EXCEEDED,
    }:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
            "package.signature_raster",
        )
    if error.code is SignatureImageErrorCode.CANCELLED:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.CANCELLED,
            "package.signature_raster",
        )
    if error.code is SignatureImageErrorCode.TIMED_OUT:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.TIMED_OUT,
            "package.signature_raster",
        )
    if error.code in {
        SignatureImageErrorCode.MODEL_INVALID,
        SignatureImageErrorCode.RESULT_INVALID,
    }:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.RESULT_INVALID,
            "package.signature_raster",
        )
    reject_package_pdf_candidate(
        PackagePdfCandidateErrorCode.INTERNAL_ERROR,
        "package.signature_raster",
    )


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.CANCELLED,
            "package.source",
        )
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.TIMED_OUT,
            "package.source",
        )
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
            "package.source",
        )
    reject_package_pdf_candidate(
        PackagePdfCandidateErrorCode.SOURCE_CHANGED,
        "package.source",
    )
