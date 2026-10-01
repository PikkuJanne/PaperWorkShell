"""PWS-0504 application bridge preserves intent, evidence and bounded ownership."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import TracebackType
from typing import Self, cast

import pytest

from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
    PackagePdfProgressSink,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.app.target_size_pdf_export import (
    PdfImageStageProcessorPort,
    TargetSizeFallbackProducer,
    TargetSizePackageCandidatePort,
    TargetSizePdfCandidateProducer,
    TargetSizePdfCandidateReport,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
    PdfImageStageRequest,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.signed_source_policy import (
    SIGNED_SOURCE_CONFIRMATION_CODE,
    SignedSourcePolicyError,
)
from paperworkshell.preflight.package_planner import PlannedPackagePdf
from tests.unit.test_package_pdf_export import (
    IMAGE_PAGE_ID,
    PDF_PAGE_ID,
    _document,
    _planned,
    _snapshot,
    _Stack,
)
from tests.unit.test_pdf_image_stages import _analysis, _report

BASELINE_BYTES = b"b" * 90_000
SELECTED_BYTES = b"s" * 35_000
PROFILE = PdfImageStageProfile.PRESERVE_300_DPI_Q90


class _StageCandidate:
    def __init__(self, plan: PdfExportPlan, request: PdfImageStageRequest) -> None:
        self.closed = False
        self.position = 0
        self.read_sizes: list[int] = []
        self.payload = BASELINE_BYTES if request.analysis_only else SELECTED_BYTES
        baseline_digest = hashlib.sha256(BASELINE_BYTES).hexdigest()
        self.analysis = replace(
            _analysis(), baseline_sha256=baseline_digest, page_count=1, pages=_analysis().pages[:1]
        )
        self.baseline_report = replace(
            PdfExportVerificationReport.for_verified_plan(
                plan, output_sha256=baseline_digest, output_byte_size=len(BASELINE_BYTES)
            ),
            report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
            optimization=PdfOptimizationReport(
                PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
                PdfOptimizationOutcome.OPTIMIZED,
                100_000,
                len(BASELINE_BYTES),
                len(BASELINE_BYTES),
                1,
            ),
        )
        if request.analysis_only:
            self.report = PdfImageStageReport(
                None,
                PdfImageStageOutcome.UNCHANGED,
                plan.semantic_fingerprint,
                baseline_digest,
                len(BASELINE_BYTES),
                None,
                baseline_digest,
                len(BASELINE_BYTES),
                1,
                (),
                (PdfImagePageImpact(0, (), 80_000, 80_000, 80_000),),
                1,
            )
        else:
            self.report = replace(
                _report(),
                semantic_fingerprint=plan.semantic_fingerprint,
                baseline_sha256=baseline_digest,
                output_sha256=hashlib.sha256(SELECTED_BYTES).hexdigest(),
                page_count=1,
                pages=_report().pages[:1],
                verified_page_count=1,
            )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    def read(self, size: int, /) -> bytes:
        self.read_sizes.append(size)
        chunk = self.payload[self.position : self.position + size]
        self.position += len(chunk)
        return chunk


class _Processor:
    def __init__(self) -> None:
        self.requests: list[PdfImageStageRequest] = []
        self.reservations: list[int] = []
        self.candidates: list[_StageCandidate] = []
        self.error: BaseException | None = None
        self.override_analysis: PdfImageAnalysis | None = None
        self.override_report: PdfImageStageReport | None = None

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
    ) -> _StageCandidate:
        self.requests.append(request)
        self.reservations.append(reserved_temporary_bytes)
        if self.error is not None:
            raise self.error
        candidate = _StageCandidate(base_plan, request)
        if self.override_analysis is not None:
            candidate.analysis = self.override_analysis
        if self.override_report is not None:
            candidate.report = self.override_report
        self.candidates.append(candidate)
        return candidate


class _TargetStack:
    def __init__(self) -> None:
        self.fallback = _Stack()
        self.processor = _Processor()
        self.producer = TargetSizePdfCandidateProducer(
            cast(CommittedPdfSourceBatchStore, self.fallback.sources),
            self.processor,
            self.fallback.producer,
            CONSTRAINED_RESOURCE_POLICY_V1,
        )
        self.producer.begin_attempt(None)

    def candidate(
        self,
        snapshot: ManifestSnapshot | None = None,
        planned: PlannedPackagePdf | None = None,
        *,
        deadline: DeadlineProbe = lambda: False,
        cancellation: CancellationProbe = lambda: False,
    ) -> TargetSizePackageCandidatePort:
        return self.producer.produce(
            snapshot or _snapshot(_document(overlays=False)),
            planned or _planned(PDF_PAGE_ID),
            deadline=deadline,
            cancellation=cancellation,
        )


def test_target_size_pdf_export_baseline_then_bound_stage_reports_selected_bytes() -> None:
    stack = _TargetStack()
    with stack.candidate() as baseline:
        report = baseline.report
        assert isinstance(report, TargetSizePdfCandidateReport)
        assert report.pipeline == "image_stage"
        assert report.base_report.output_byte_size == len(BASELINE_BYTES)
        assert report.baseline_report.optimization is not None
        assert report.base_report.optimization is None
        assert baseline.read(len(BASELINE_BYTES)) == BASELINE_BYTES
        assert baseline.read(1) == b""
        assert not bool(stack.fallback.sources.batches[0].closed)
    assert stack.fallback.sources.batches[0].closed
    assert stack.processor.candidates[0].closed
    stack.producer.begin_attempt(PROFILE)
    stack.producer.reserve_temporary_bytes(123_456)
    with stack.candidate() as selected:
        report = selected.report
        assert isinstance(report, TargetSizePdfCandidateReport)
        assert report.base_report.output_byte_size == len(SELECTED_BYTES)
        assert report.base_report.output_sha256 == hashlib.sha256(SELECTED_BYTES).hexdigest()
        assert report.baseline_report.output_byte_size == len(BASELINE_BYTES)
        assert report.base_report.optimization is None
        assert selected.read(len(SELECTED_BYTES)) == SELECTED_BYTES
        assert selected.read(1) == b""
        assert report.base_report.unsigned_derivative
        assert stack.producer.stage_reports == (report.stage_report,)
    request = stack.processor.requests[-1]
    assert request.profile is PROFILE
    assert request.selected_asset_ids == (0,)
    assert request.baseline_sha256 == hashlib.sha256(BASELINE_BYTES).hexdigest()
    assert stack.processor.reservations == [0, 123_456]
    assert stack.producer.unsupported_output_positions == ()


@pytest.mark.parametrize("image", [False, True])
def test_target_size_pdf_export_fallback_preserves_unsupported_manifest_intent(image: bool) -> None:
    stack = _TargetStack()
    stack.producer.reserve_temporary_bytes(12_345)
    snapshot = _snapshot(_document(overlays=not image))
    planned = _planned(IMAGE_PAGE_ID if image else PDF_PAGE_ID)
    with stack.candidate(snapshot, planned) as candidate:
        assert candidate.report.pipeline == ("searchable" if image else "flattened")
    assert stack.processor.requests == []
    assert stack.producer.unsupported_output_positions == (1,)
    broker = stack.fallback.searchable if image else stack.fallback.flattened
    assert broker.reserved_temporary_bytes == 12_345


def test_target_size_pdf_export_signed_confirmation_precedes_borrow_and_decode() -> None:
    document = _document(overlays=False)
    confirmations = cast(list[dict[str, object]], document["confirmations"])
    document["confirmations"] = [
        item
        for item in confirmations
        if item["confirmation_code"] != SIGNED_SOURCE_CONFIRMATION_CODE
    ]
    stack = _TargetStack()
    with pytest.raises(SignedSourcePolicyError):
        stack.candidate(_snapshot(document))
    assert stack.fallback.sources.pdf_calls == 0
    assert stack.processor.requests == []


def test_target_size_pdf_export_stale_snapshot_and_changed_output_fail_before_processing() -> None:
    stack = _TargetStack()
    with stack.candidate():
        pass
    stack.producer.begin_attempt(PROFILE)
    current = _snapshot(_document(overlays=False))
    changed = ManifestSnapshot(
        case_id=current.case_id,
        schema_version=current.schema_version,
        revision=current.revision,
        document=current.document,
        sha256="c" * 64,
        cas_token=current.cas_token,
    )
    with pytest.raises(PdfExportError) as stale:
        stack.candidate(changed)
    assert stale.value.code is PdfExportErrorCode.PLAN_STALE
    with pytest.raises(PdfExportError) as output_stale:
        with stack.candidate(planned=replace(_planned(PDF_PAGE_ID), output_key="changed")):
            pass
    assert output_stale.value.code is PdfExportErrorCode.PLAN_STALE
    assert len(stack.processor.requests) == 1


def test_target_size_pdf_export_new_search_clears_baseline_and_old_lazy_context() -> None:
    stack = _TargetStack()
    old = stack.candidate()
    stack.producer.begin_attempt(None)
    with pytest.raises(PackagePdfCandidateError) as stale:
        with old:
            pass
    assert stale.value.code is PackagePdfCandidateErrorCode.SESSION_STATE_INVALID
    stack.producer.begin_attempt(PROFILE)
    with pytest.raises(PdfExportError) as missing:
        with stack.candidate():
            pass
    assert missing.value.code is PdfExportErrorCode.PLAN_STALE
    assert stack.processor.requests == []


def test_target_size_pdf_export_single_active_candidate_and_reset_after_close() -> None:
    stack = _TargetStack()
    first, second = stack.candidate(), stack.candidate(planned=_planned(PDF_PAGE_ID, position=2))
    with first:
        with pytest.raises(PackagePdfCandidateError) as active:
            second.__enter__()
        assert active.value.code is PackagePdfCandidateErrorCode.LIMIT_EXCEEDED
        with pytest.raises(PackagePdfCandidateError):
            stack.producer.begin_attempt(PROFILE)
        with pytest.raises(PackagePdfCandidateError):
            stack.producer.reserve_temporary_bytes(0)
    with second:
        pass
    assert len(stack.processor.requests) == 2
    assert all(candidate.closed for candidate in stack.processor.candidates)


@pytest.mark.parametrize("cancelled", [False, True])
def test_target_size_pdf_export_cancellation_and_deadline_apply_during_reads(
    cancelled: bool,
) -> None:
    stack = _TargetStack()
    expired = False
    with stack.candidate(
        deadline=lambda: expired and not cancelled,
        cancellation=lambda: expired and cancelled,
    ) as candidate:
        expired = True
        with pytest.raises(PackagePdfCandidateError) as raised:
            candidate.read(1)
        assert raised.value.code is (
            PackagePdfCandidateErrorCode.CANCELLED
            if cancelled
            else PackagePdfCandidateErrorCode.TIMED_OUT
        )
    assert stack.processor.candidates[0].closed
    assert stack.fallback.sources.batches[0].closed


def test_target_size_pdf_export_budget_covers_all_retained_evidence() -> None:
    stack = _TargetStack()
    stack.producer = TargetSizePdfCandidateProducer(
        cast(CommittedPdfSourceBatchStore, stack.fallback.sources),
        stack.processor,
        stack.fallback.producer,
        replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_cache_bytes=1),
    )
    stack.producer.begin_attempt(None)
    with pytest.raises(PackagePdfCandidateError) as raised:
        with stack.candidate():
            pass
    assert raised.value.code is PackagePdfCandidateErrorCode.LIMIT_EXCEEDED
    assert stack.producer.stage_reports == ()
    assert stack.processor.candidates[0].closed
    assert stack.fallback.sources.batches[0].closed


def test_target_size_pdf_export_invalid_data_never_triggers_intent_fallback() -> None:
    stack = _TargetStack()
    with pytest.raises(PdfExportError) as raised:
        stack.candidate(
            planned=replace(
                _planned(PDF_PAGE_ID), page_ids=(_planned(IMAGE_PAGE_ID).assignment_ids[0],)
            )
        )
    assert raised.value.code not in {
        PdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
    }
    assert stack.fallback.flattened.calls == stack.fallback.searchable.calls == 0
    assert stack.processor.requests == []


def test_target_size_pdf_export_eof_rejects_changed_candidate_bytes() -> None:
    stack = _TargetStack()
    with stack.candidate() as candidate:
        stack.processor.candidates[0].payload = b"x" * len(BASELINE_BYTES)
        candidate.read(len(BASELINE_BYTES))
        with pytest.raises(PdfExportError) as raised:
            candidate.read(1)
        assert raised.value.code is PdfExportErrorCode.RESULT_INVALID


def test_target_size_pdf_export_ports_are_structurally_compatible() -> None:
    stack = _TargetStack()
    assert isinstance(stack.processor, PdfImageStageProcessorPort)
    assert isinstance(stack.fallback.producer, TargetSizeFallbackProducer)


def test_target_size_pdf_export_smaller_io_policy_returns_bounded_short_read() -> None:
    stack = _TargetStack()
    stack.producer = TargetSizePdfCandidateProducer(
        cast(CommittedPdfSourceBatchStore, stack.fallback.sources),
        stack.processor,
        stack.fallback.producer,
        replace(CONSTRAINED_RESOURCE_POLICY_V1, import_io_chunk_bytes=256 * 1024),
    )
    stack.producer.begin_attempt(None)
    with stack.candidate() as candidate:
        assert candidate.read(1024 * 1024) == BASELINE_BYTES
        assert stack.processor.candidates[0].read_sizes == [256 * 1024]
        assert candidate.read(1024 * 1024) == b""


def test_target_size_pdf_export_lazy_candidate_uses_latest_reservation_at_entry() -> None:
    stack = _TargetStack()
    candidate = stack.candidate()
    stack.producer.reserve_temporary_bytes(987_654)
    with candidate:
        assert stack.processor.reservations == [987_654]
