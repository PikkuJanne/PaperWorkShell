"""Opt-in, bounded bridge from package search to verified PDF image stages.

Only the ordinary-PDF subset is eligible. Existing package pipelines retain all
unsupported overlay, image, crop and scan-processing intent. Cached baseline
facts are scoped to one immutable package search and never contain PDF bytes.
"""

from __future__ import annotations

import hashlib
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field, fields, is_dataclass
from threading import Lock
from types import TracebackType
from typing import Final, Protocol, Self, runtime_checkable
from uuid import UUID

from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
    PackagePdfProgressSink,
    _raise_source_error,
    _require_active,
    reject_package_pdf_candidate,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportVerificationReport,
    build_pdf_export_plan_for_pages,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageEligibility,
    PdfImageStageProfile,
    PdfImageStageReport,
    PdfImageStageRequest,
    validate_pdf_image_stage_report,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourcePolicyError,
    SignedSourceSignatureState,
    require_signed_source_modification_confirmation,
)
from paperworkshell.domain.source_ingest import SourceIngestError
from paperworkshell.preflight.package_planner import PlannedPackagePdf

_MAX_OUTPUTS: Final = 128
# The existing candidate-stream transport accepts requests up to one MiB;
# actual reads can be smaller under the active ResourcePolicy.
_MAX_CANDIDATE_READ_BYTES: Final = 1024 * 1024
_FALLBACK_CODES: Final = frozenset(
    {
        PdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
    }
)


class TargetSizePackageCandidateReportPort(Protocol):
    @property
    def output_key(self) -> str: ...

    @property
    def position(self) -> int: ...

    @property
    def page_ids(self) -> tuple[UUID, ...]: ...

    @property
    def pipeline(self) -> object: ...

    @property
    def base_report(self) -> PdfExportVerificationReport: ...


class TargetSizePackageCandidatePort(Protocol):
    @property
    def report(self) -> TargetSizePackageCandidateReportPort: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class TargetSizeFallbackProducer(Protocol):
    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink,
    ) -> TargetSizePackageCandidatePort: ...


@runtime_checkable
class _TemporaryReservationPort(Protocol):
    def reserve_temporary_bytes(self, byte_count: int) -> None: ...


class PdfImageStageCandidatePort(Protocol):
    @property
    def analysis(self) -> PdfImageAnalysis: ...

    @property
    def report(self) -> PdfImageStageReport: ...

    @property
    def baseline_report(self) -> PdfExportVerificationReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PdfImageStageProcessorPort(Protocol):
    """Worker-owned implementation; application code imports only this port."""

    def process(
        self,
        base_plan: PdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        request: PdfImageStageRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink,
        reserved_temporary_bytes: int = 0,
    ) -> PdfImageStageCandidatePort: ...


@dataclass(frozen=True, slots=True, repr=False)
class TargetSizePdfCandidateReport:
    """Selected bytes plus separate baseline and image-stage evidence."""

    output_key: str = field(repr=False)
    position: int
    page_ids: tuple[UUID, ...] = field(repr=False)
    base_report: PdfExportVerificationReport = field(repr=False)
    baseline_report: PdfExportVerificationReport = field(repr=False)
    stage_report: PdfImageStageReport = field(repr=False)
    pipeline: str = field(default="image_stage", init=False)

    def __repr__(self) -> str:
        return (
            "TargetSizePdfCandidateReport("
            f"position={self.position}, page_count={self.base_report.page_count}, "
            f"output_byte_size={self.base_report.output_byte_size})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _Baseline:
    output_key: str
    page_ids: tuple[UUID, ...]
    semantic_fingerprint: str
    analysis: PdfImageAnalysis


def _ignore_progress(_progress: PdfExportProgress) -> None:
    pass


class TargetSizePdfCandidateProducer:
    """One search session with at most one active candidate and 128 outputs.

    ``begin_attempt(None)`` starts a new search and drops prior evidence.
    Subsequent explicit profiles use the exact baseline digest and plan facts.
    The caller supplies the same operation-wide deadline to every attempt.
    """

    def __init__(
        self,
        source_store: CommittedPdfSourceBatchStore,
        processor: PdfImageStageProcessorPort,
        fallback_producer: TargetSizeFallbackProducer,
        resource_policy: ResourcePolicy,
    ) -> None:
        if (
            not isinstance(source_store, CommittedPdfSourceBatchStore)
            or not isinstance(processor, PdfImageStageProcessorPort)
            or not isinstance(fallback_producer, TargetSizeFallbackProducer)
            or type(resource_policy) is not ResourcePolicy
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID, "target.producer"
            )
        self._sources = source_store
        self._processor = processor
        self._fallback = fallback_producer
        self._policy = resource_policy
        self._lock = Lock()
        self._active = False
        self._generation = 0
        self._profile: PdfImageStageProfile | None = None
        self._baselines: dict[int, _Baseline] = {}
        self._binding: tuple[UUID, int, str, str] | None = None
        self._reports: list[PdfImageStageReport] = []
        self._unsupported: list[int] = []
        self._seen: set[int] = set()
        self._reserved_bytes = 0
        self._retained_bytes = 0
        self._attempt_bytes = 0

    @property
    def stage_reports(self) -> tuple[PdfImageStageReport, ...]:
        return tuple(self._reports)

    @property
    def baseline_analyses(self) -> tuple[tuple[int, PdfImageAnalysis], ...]:
        """Expose already bounded original facts for the explicit compression review."""

        return tuple(
            (position, value.analysis) for position, value in sorted(self._baselines.items())
        )

    @property
    def unsupported_output_positions(self) -> tuple[int, ...]:
        return tuple(self._unsupported)

    def begin_attempt(self, profile: PdfImageStageProfile | None) -> None:
        """Reset per-attempt evidence, retaining only bound baseline analyses."""

        if profile is not None and type(profile) is not PdfImageStageProfile:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID, "target.profile"
            )
        with self._lock:
            if self._active or (profile is not None and self._generation == 0):
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.attempt"
                )
            if profile is None:
                self._baselines.clear()
                self._binding = None
                self._retained_bytes = 0
            self._profile = profile
            self._generation += 1
            self._reports.clear()
            self._unsupported.clear()
            self._seen.clear()
            self._attempt_bytes = 0
            self._reserved_bytes = 0

    def select_profile(self, profile: PdfImageStageProfile | None) -> None:
        self.begin_attempt(profile)

    def reserve_temporary_bytes(self, byte_count: int) -> None:
        """Reserve already-staged package bytes before opening another worker."""

        if (
            type(byte_count) is not int
            or not 0 <= byte_count < self._policy.maximum_temporary_storage_bytes
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED, "target.temporary_reservation"
            )
        with self._lock:
            if self._active:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.active_reservation"
                )
            self._reserved_bytes = byte_count

    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink = _ignore_progress,
    ) -> TargetSizePackageCandidatePort:
        if (
            type(snapshot) is not ManifestSnapshot
            or type(planned_pdf) is not PlannedPackagePdf
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
            or not 1 <= planned_pdf.position <= _MAX_OUTPUTS
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.MODEL_INVALID, "target.produce"
            )
        _require_active(deadline, cancellation)
        if self._generation == 0:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.attempt_required"
            )
        binding = (snapshot.case_id, snapshot.revision, snapshot.sha256, snapshot.cas_token)
        if self._binding is None:
            self._binding = binding
        elif self._binding != binding:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "target.snapshot_binding")
        plan: PdfExportPlan | None
        try:
            plan = build_pdf_export_plan_for_pages(snapshot, planned_pdf.page_ids)
        except PdfExportError as error:
            if error.code not in _FALLBACK_CODES:
                raise
            plan = None
        if plan is not None:
            require_signed_source_modification_confirmation(
                snapshot,
                tuple(
                    SignedSourceModificationSource(
                        source.source_id, SignedSourceSignatureState(source.signature_state.value)
                    )
                    for source in plan.sources
                ),
            )
        return _TargetCandidateSession(
            self, snapshot, planned_pdf, plan, deadline, cancellation, progress
        )

    def _claim(self, generation: int, position: int) -> int:
        with self._lock:
            if self._active:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.LIMIT_EXCEEDED, "target.active_candidate"
                )
            if generation != self._generation or position in self._seen:
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.candidate_binding"
                )
            self._active = True
            self._seen.add(position)
            return self._reserved_bytes

    def _request(self, planned: PlannedPackagePdf, plan: PdfExportPlan) -> PdfImageStageRequest:
        if self._profile is None:
            return PdfImageStageRequest()
        baseline = self._baselines.get(planned.position)
        if (
            baseline is None
            or baseline.output_key != planned.output_key
            or baseline.page_ids != planned.page_ids
            or baseline.semantic_fingerprint != plan.semantic_fingerprint
        ):
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "target.baseline_binding")
        return PdfImageStageRequest(
            profile=self._profile,
            selected_asset_ids=tuple(
                asset.asset_id
                for asset in baseline.analysis.assets
                if asset.eligibility is PdfImageEligibility.ELIGIBLE
            ),
            baseline_sha256=baseline.analysis.baseline_sha256,
            semantic_fingerprint=plan.semantic_fingerprint,
        )

    def _remember(
        self,
        planned: PlannedPackagePdf,
        plan: PdfExportPlan,
        analysis: PdfImageAnalysis,
        report: PdfImageStageReport,
    ) -> None:
        report_bytes = _evidence_size(report)
        baseline = _Baseline(
            planned.output_key, planned.page_ids, plan.semantic_fingerprint, analysis
        )
        baseline_bytes = _evidence_size(baseline) if self._profile is None else 0
        if (
            len(self._reports) >= _MAX_OUTPUTS
            or self._retained_bytes + self._attempt_bytes + baseline_bytes + report_bytes
            > self._policy.maximum_cache_bytes
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED, "target.evidence_capacity"
            )
        if self._profile is None:
            self._baselines[planned.position] = baseline
            self._retained_bytes += baseline_bytes
        elif self._baselines[planned.position].analysis != analysis:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "target.analysis_binding")
        self._attempt_bytes += report_bytes
        self._reports.append(report)


def _evidence_size(value: object) -> int:
    """Conservative retained-Python-byte count; shared values are charged twice.

    Inputs are the closed, shallow domain records above. No user containers or
    document bytes enter this traversal; object/reference counts are validated.
    """

    size = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        return size + sum(_evidence_size(getattr(value, item.name)) for item in fields(value))
    if isinstance(value, tuple):
        return size + sum(_evidence_size(item) for item in value)
    return size


class _TargetCandidateSession:
    def __init__(
        self,
        producer: TargetSizePdfCandidateProducer,
        snapshot: ManifestSnapshot,
        planned: PlannedPackagePdf,
        plan: PdfExportPlan | None,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PackagePdfProgressSink,
    ) -> None:
        self._producer = producer
        self._snapshot = snapshot
        self._planned = planned
        self._plan = plan
        self._deadline = deadline
        self._cancellation = cancellation
        self._progress = progress
        self._generation = producer._generation
        self._reserved_bytes = 0
        self._stack = ExitStack()
        self._candidate: TargetSizePackageCandidatePort | PdfImageStageCandidatePort | None = None
        self._report: TargetSizePackageCandidateReportPort | None = None
        self._claimed = False
        self._closed = False
        self._count = 0
        self._digest = hashlib.sha256()

    @property
    def report(self) -> TargetSizePackageCandidateReportPort:
        if self._report is None:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.report"
            )
        return self._report

    def __enter__(self) -> Self:
        if self._closed or self._claimed:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.candidate_enter"
            )
        self._reserved_bytes = self._producer._claim(self._generation, self._planned.position)
        self._claimed = True
        try:
            _require_active(self._deadline, self._cancellation)
            if self._plan is None:
                self._enter_fallback()
            else:
                self._enter_image_stage(self._plan)
            _require_active(self._deadline, self._cancellation)
            return self
        except PdfExportError, PackagePdfCandidateError, SignedSourcePolicyError:
            self._close()
            raise
        except SourceIngestError as error:
            self._close()
            _raise_source_error(error)
        except BaseException:
            self._close()
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR, "target.candidate_enter"
            )

    def _enter_fallback(self) -> None:
        fallback = self._producer._fallback
        if isinstance(fallback, _TemporaryReservationPort):
            fallback.reserve_temporary_bytes(self._reserved_bytes)
        elif self._reserved_bytes:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.LIMIT_EXCEEDED, "target.fallback_reservation"
            )
        candidate = self._stack.enter_context(
            fallback.produce(
                self._snapshot,
                self._planned,
                deadline=self._deadline,
                cancellation=self._cancellation,
                progress=self._progress,
            )
        )
        self._candidate = candidate
        self._report = candidate.report
        self._producer._unsupported.append(self._planned.position)

    def _enter_image_stage(self, plan: PdfExportPlan) -> None:
        request = self._producer._request(self._planned, plan)
        sources = self._stack.enter_context(
            self._producer._sources.borrow_committed_pdf_sources(
                plan, deadline=self._deadline, cancellation=self._cancellation
            )
        )
        candidate = self._stack.enter_context(
            self._producer._processor.process(
                plan,
                sources,
                request,
                deadline=self._deadline,
                cancellation=self._cancellation,
                progress=self._progress,
                reserved_temporary_bytes=self._reserved_bytes,
            )
        )
        analysis = candidate.analysis
        report = candidate.report
        baseline = candidate.baseline_report
        if (
            type(analysis) is not PdfImageAnalysis
            or type(baseline) is not PdfExportVerificationReport
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "target.candidate_evidence")
        validate_pdf_image_stage_report(request, analysis, report)
        if (
            baseline.semantic_fingerprint != plan.semantic_fingerprint
            or report.semantic_fingerprint != plan.semantic_fingerprint
            or baseline.page_count != plan.page_count
            or analysis.page_count != plan.page_count
            or report.page_count != plan.page_count
            or baseline.output_sha256 != analysis.baseline_sha256
            or baseline.output_byte_size != analysis.baseline_byte_size
            or baseline.optimization is None
            or baseline.unsigned_derivative != plan.contains_signature_markers
            or report.output_byte_size + self._reserved_bytes
            > self._producer._policy.maximum_temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "target.candidate_binding")
        self._producer._remember(self._planned, plan, analysis, report)
        # Image-stage verification reopens, checks structure/encryption/count,
        # renders every output page and preserves page geometry/order. Its lossy
        # output has different bytes: baseline optimization size facts stay in
        # baseline_report, never in the selected output's generic report.
        selected = PdfExportVerificationReport.for_verified_plan(
            plan, output_sha256=report.output_sha256, output_byte_size=report.output_byte_size
        )
        self._report = TargetSizePdfCandidateReport(
            self._planned.output_key,
            self._planned.position,
            self._planned.page_ids,
            selected,
            baseline,
            report,
        )
        self._candidate = candidate

    def read(self, size: int, /) -> bytes:
        if (
            self._closed
            or self._candidate is None
            or type(size) is not int
            or not 1 <= size <= _MAX_CANDIDATE_READ_BYTES
        ):
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, "target.read"
            )
        _require_active(self._deadline, self._cancellation)
        try:
            bounded_size = min(size, self._producer._policy.import_io_chunk_bytes)
            chunk = self._candidate.read(bounded_size)
        except PdfExportError, PackagePdfCandidateError:
            raise
        except BaseException:
            reject_package_pdf_candidate(PackagePdfCandidateErrorCode.INTERNAL_ERROR, "target.read")
        if type(chunk) is not bytes or len(chunk) > bounded_size:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "target.read_chunk")
        self._count += len(chunk)
        self._digest.update(chunk)
        if self._count > self.report.base_report.output_byte_size or (
            not chunk
            and (
                self._count != self.report.base_report.output_byte_size
                or self._digest.hexdigest() != self.report.base_report.output_sha256
            )
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "target.read_binding")
        _require_active(self._deadline, self._cancellation)
        return chunk

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        try:
            self._stack.close()
        except BaseException:
            reject_package_pdf_candidate(
                PackagePdfCandidateErrorCode.INTERNAL_ERROR, "target.cleanup"
            )
        finally:
            self._closed = True
            self._candidate = None
            if self._claimed:
                with self._producer._lock:
                    self._producer._active = False
                self._claimed = False
