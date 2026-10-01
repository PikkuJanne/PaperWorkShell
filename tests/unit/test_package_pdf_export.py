"""Focused PWS-0407 evidence for bounded per-output PDF production."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.flattened_pdf_export import (
    FlattenedPdfExportBroker,
    FlattenedPdfExportProgressSink,
    FlattenedPdfRasterizer,
)
from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidate,
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
    PackagePdfCandidateProducer,
    PackagePdfCandidateReport,
    PackagePdfPipeline,
    PackagePdfSourceStore,
)
from paperworkshell.app.searchable_pdf_export import (
    SearchablePdfExportBroker,
    SearchablePdfExportProgressSink,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedSearchablePdfSourceBatchLease,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_export import PdfExportPlan, PdfExportProgress
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.domain.signature_images import SignatureImageRasterRequest
from paperworkshell.domain.signed_source_policy import (
    SIGNED_SOURCE_CONFIRMATION_CODE,
    SignedSourcePolicyError,
    SignedSourcePolicyErrorCode,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.preflight.package_planner import PlannedPackagePdf

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas/examples/case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PDF_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
IMAGE_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000002")
PDF_PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
IMAGE_PAGE_ID = UUID("30000000-0000-4000-8000-000000000003")
SIGNATURE_ASSET_ID = UUID("40000000-0000-4000-8000-000000000001")
ASSIGNMENT_ID = UUID("70000000-0000-4000-8000-000000000001")
PAYLOAD = b"%PDF-1.7\n% synthetic package candidate\n%%EOF\n"
PRIVATE_OUTPUT_KEY = "application-form"
PRIVATE_FILENAME = "private-portal-filename.pdf"
PRIVATE_TOKEN = "PRIVATE PACKAGE CANDIDATE TOKEN"


def _document(*, overlays: bool = True) -> dict[str, object]:
    value = cast(dict[str, object], json.loads(REPRESENTATIVE.read_text("utf-8")))
    if not overlays:
        value["overlays"] = []
        value["assets"] = []
    return value


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        case_id=CASE_ID,
        schema_version=cast(int, selected["schema_version"]),
        revision=cast(int, selected["revision"]),
        document=selected,
        sha256="f" * 64,
        cas_token="package-pdf-candidate-cas-v1",
    )


def _planned(page_id: UUID, *, position: int = 1) -> PlannedPackagePdf:
    return PlannedPackagePdf(
        output_key=PRIVATE_OUTPUT_KEY,
        position=position,
        requirement_id="application-form",
        assignment_ids=(ASSIGNMENT_ID,),
        page_ids=(page_id,),
        filename=PRIVATE_FILENAME,
        collision_index=1,
    )


class _Batch:
    def __init__(self) -> None:
        self.closed: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True


class _Sources:
    def __init__(self) -> None:
        self.pdf_calls = 0
        self.searchable_calls = 0
        self.error: SourceIngestError | None = None
        self.batches: list[_Batch] = []

    def borrow_committed_pdf_sources(
        self,
        plan: PdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> _Batch:
        del plan, deadline, cancellation
        self.pdf_calls += 1
        if self.error is not None:
            raise self.error
        batch = _Batch()
        self.batches.append(batch)
        return batch

    def borrow_committed_searchable_pdf_sources(
        self,
        plan: SearchablePdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> _Batch:
        del plan, deadline, cancellation
        self.searchable_calls += 1
        if self.error is not None:
            raise self.error
        batch = _Batch()
        self.batches.append(batch)
        return batch


class _Candidate:
    def __init__(self, report: object) -> None:
        self._report = report
        self.position = 0
        self.closed: bool = False

    @property
    def report(self) -> object:
        if isinstance(self._report, BaseException):
            raise self._report
        return self._report

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True

    def read(self, size: int, /) -> bytes:
        selected = PAYLOAD[self.position : self.position + size]
        self.position += len(selected)
        return selected


class _FlattenedBroker:
    maximum_output_bytes: object = 8 * 1024 * 1024
    maximum_input_references: object = 32

    def __init__(self) -> None:
        self.calls = 0
        self.plan: FlattenedPdfExportPlan | None = None
        self.candidate: _Candidate | None = None
        self.report_override: object | None = None
        self.reserved_temporary_bytes = 0

    def export(
        self,
        plan: FlattenedPdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        rasters: dict[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink,
        reserved_temporary_bytes: int = 0,
    ) -> _Candidate:
        del sources, rasters, deadline, cancellation, progress
        self.calls += 1
        self.reserved_temporary_bytes = reserved_temporary_bytes
        self.plan = plan
        report = self.report_override or FlattenedPdfExportVerificationReport.for_verified_plan(
            plan,
            output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            output_byte_size=len(PAYLOAD),
        )
        self.candidate = _Candidate(report)
        return self.candidate


def _searchable_report(plan: SearchablePdfExportPlan) -> SearchablePdfVerificationReport:
    source_by_id = {source.source_id: source for source in plan.sources}
    outcomes: list[SearchablePdfPageOutcome] = []
    for page in plan.pages:
        source = source_by_id[page.source_id]
        if source.media_type is not SearchablePdfSourceMediaType.PDF:
            eligibility_code = SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE
            outcome_code = SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
            searchability = SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR
            failure = None
        elif source.signature_state in {
            SearchablePdfSourceSignatureState.MARKERS_DETECTED,
            SearchablePdfSourceSignatureState.UNKNOWN,
        }:
            eligibility_code = SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE
            outcome_code = SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED
            searchability = SearchablePdfPageSearchability.NOT_VERIFIED
            failure = SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED
        else:
            eligibility_code = SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT
            outcome_code = SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED
            searchability = SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING
            failure = None
        outcomes.append(
            SearchablePdfPageOutcome(
                SearchablePdfPageEligibility(
                    page.output_page_index,
                    page.page_id,
                    eligibility_code,
                ),
                outcome_code,
                searchability,
                failure,
            )
        )
    return SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        output_byte_size=len(PAYLOAD),
        page_outcomes=tuple(outcomes),
    )


class _SearchableBroker:
    maximum_output_bytes: object = 16 * 1024 * 1024

    def __init__(self) -> None:
        self.calls = 0
        self.plan: SearchablePdfExportPlan | None = None
        self.candidate: _Candidate | None = None
        self.report_override: object | None = None
        self.reserved_temporary_bytes = 0

    def export(
        self,
        plan: SearchablePdfExportPlan,
        sources: CommittedSearchablePdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink,
        reserved_temporary_bytes: int = 0,
    ) -> _Candidate:
        del sources, deadline, cancellation, progress
        self.calls += 1
        self.reserved_temporary_bytes = reserved_temporary_bytes
        self.plan = plan
        report = self.report_override or _searchable_report(plan)
        self.candidate = _Candidate(report)
        return self.candidate


class _Rasterizer:
    def __init__(self) -> None:
        self.requests: list[SignatureImageRasterRequest] = []
        self.replacement_asset_id: UUID | None = None

    def rasterize_committed(
        self,
        request: SignatureImageRasterRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> SignatureImageOverlayRasterSource:
        del deadline, cancellation
        self.requests.append(request)
        pixels = b"\x10\x20\x30\xff" * 4
        metadata = OverlayRasterAsset(
            asset_id=self.replacement_asset_id or request.asset_id,
            width_pixels=2,
            height_pixels=2,
            row_stride_bytes=8,
            byte_count=len(pixels),
            pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        )
        return SignatureImageOverlayRasterSource(metadata, pixels)


class _Languages:
    def __init__(self, selected: str = "eng-v1", *, unavailable: bool = False) -> None:
        self.selected = selected
        self.unavailable = unavailable
        self.calls = 0

    def require_available(self, language_set_id: str) -> None:
        if language_set_id != self.selected or self.unavailable:
            raise OcrError(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_preflight")

    def require_selected_available(self) -> str:
        self.calls += 1
        self.require_available(self.selected)
        return self.selected


class _Stack:
    def __init__(self, *, languages: _Languages | None = None) -> None:
        self.sources = _Sources()
        self.rasterizer = _Rasterizer()
        self.flattened = _FlattenedBroker()
        self.searchable = _SearchableBroker()
        self.languages = languages or _Languages()
        self.producer = PackagePdfCandidateProducer(
            cast(PackagePdfSourceStore, self.sources),
            cast(FlattenedPdfRasterizer, self.rasterizer),
            cast(FlattenedPdfExportBroker, self.flattened),
            cast(SearchablePdfExportBroker, self.searchable),
            CONSTRAINED_RESOURCE_POLICY_V1,
            ocr_languages=self.languages,
        )


def _assert_package_error(
    code: PackagePdfCandidateErrorCode,
    action: object,
) -> PackagePdfCandidateError:
    assert callable(action)
    with pytest.raises(PackagePdfCandidateError) as raised:
        action()
    assert raised.value.code is code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    return raised.value


@pytest.mark.parametrize("page_id", [PDF_PAGE_ID, IMAGE_PAGE_ID])
def test_package_reservation_reaches_selected_worker_and_can_reset_after_close(
    page_id: UUID,
) -> None:
    stack = _Stack()
    snapshot = _snapshot(_document(overlays=page_id == PDF_PAGE_ID))
    stack.producer.reserve_temporary_bytes(12_345)
    with stack.producer.produce(
        snapshot,
        _planned(page_id),
        deadline=lambda: False,
        cancellation=lambda: False,
    ):
        broker = stack.flattened if page_id == PDF_PAGE_ID else stack.searchable
        assert broker.reserved_temporary_bytes == 12_345
        _assert_package_error(
            PackagePdfCandidateErrorCode.SESSION_STATE_INVALID,
            lambda: stack.producer.reserve_temporary_bytes(0),
        )
    stack.producer.reserve_temporary_bytes(0)
    with stack.producer.produce(
        snapshot,
        _planned(page_id),
        deadline=lambda: False,
        cancellation=lambda: False,
    ):
        assert broker.reserved_temporary_bytes == 0


@pytest.mark.parametrize("reserved", [-1, True, "1024"])
def test_package_rejects_invalid_external_stage_reservation(reserved: object) -> None:
    stack = _Stack()
    _assert_package_error(
        PackagePdfCandidateErrorCode.MODEL_INVALID,
        lambda: stack.producer.reserve_temporary_bytes(cast(int, reserved)),
    )
    assert stack.flattened.calls == stack.searchable.calls == 0


def test_package_rejects_external_stage_reservation_exhausting_resource_policy() -> None:
    stack = _Stack()
    _assert_package_error(
        PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
        lambda: stack.producer.reserve_temporary_bytes(
            CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
        ),
    )
    assert stack.flattened.calls == stack.searchable.calls == 0


def test_flattened_candidate_binds_subset_report_and_retains_resources_until_close() -> None:
    stack = _Stack()
    progress: list[PdfExportProgress] = []
    session = stack.producer.produce(
        _snapshot(),
        _planned(PDF_PAGE_ID, position=2),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert isinstance(session, PackagePdfCandidate)
    assert stack.rasterizer.requests == []
    with session as candidate:
        report = candidate.report
        assert type(report) is PackagePdfCandidateReport
        assert report.output_key == PRIVATE_OUTPUT_KEY
        assert report.position == 2
        assert report.page_ids == (PDF_PAGE_ID,)
        assert report.pipeline is PackagePdfPipeline.FLATTENED
        assert report.page_count == 1
        assert candidate.read(len(PAYLOAD)) == PAYLOAD
        assert candidate.read(1) == b""
        assert stack.sources.batches and not bool(stack.sources.batches[0].closed)

    assert stack.sources.pdf_calls == 1
    assert stack.sources.searchable_calls == 0
    assert bool(stack.sources.batches[0].closed)
    assert stack.flattened.candidate is not None and bool(stack.flattened.candidate.closed)
    assert stack.searchable.calls == 0
    assert len(stack.rasterizer.requests) == 1
    request = stack.rasterizer.requests[0]
    assert request.asset_id == SIGNATURE_ASSET_ID
    assert request.maximum_width_pixels > 0
    assert request.maximum_height_pixels > 0
    assert report.base_report.output_byte_size == len(PAYLOAD)


def test_image_and_scan_transform_without_overlays_route_to_searchable_pipeline() -> None:
    stack = _Stack()
    session = stack.producer.produce(
        _snapshot(_document(overlays=False)),
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    with session as candidate:
        assert candidate.report.pipeline is PackagePdfPipeline.SEARCHABLE
        assert candidate.report.page_ids == (IMAGE_PAGE_ID,)
        assert candidate.read(len(PAYLOAD)) == PAYLOAD

    assert stack.flattened.calls == 0
    assert stack.searchable.calls == 1
    assert stack.searchable.plan is not None
    assert tuple(page.page_id for page in stack.searchable.plan.pages) == (IMAGE_PAGE_ID,)
    assert stack.searchable.plan.pages[0].has_pixel_transform_intent
    assert stack.sources.pdf_calls == 0
    assert stack.sources.searchable_calls == 1
    assert stack.rasterizer.requests == []
    assert stack.languages.calls == 1


def test_unknown_signature_without_overlays_routes_to_searchable_after_confirmation() -> None:
    document = _document(overlays=False)
    sources = cast(list[dict[str, object]], document["sources"])
    pdf = next(source for source in sources if source["source_id"] == str(PDF_SOURCE_ID))
    pdf["signature_state"] = "unknown"
    stack = _Stack()

    with stack.producer.produce(
        _snapshot(document),
        _planned(PDF_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as candidate:
        assert candidate.report.pipeline is PackagePdfPipeline.SEARCHABLE
        assert candidate.report.base_report.unsigned_derivative

    assert stack.flattened.calls == 0
    assert stack.searchable.calls == 1


def test_active_overlay_with_image_or_scan_processing_fails_without_dropping_intent() -> None:
    stack = _Stack()

    error = _assert_package_error(
        PackagePdfCandidateErrorCode.UNSUPPORTED_OVERLAY_TRANSFORM,
        lambda: stack.producer.produce(
            _snapshot(),
            _planned(IMAGE_PAGE_ID),
            deadline=lambda: False,
            cancellation=lambda: False,
        ),
    )

    assert stack.rasterizer.requests == []
    assert stack.sources.pdf_calls == stack.sources.searchable_calls == 0
    assert stack.flattened.calls == stack.searchable.calls == 0
    representation = repr(error) + str(error)
    assert PRIVATE_FILENAME not in representation
    assert PRIVATE_OUTPUT_KEY not in representation
    assert PRIVATE_TOKEN not in representation


def test_signed_source_confirmation_is_required_before_raster_or_source_borrow() -> None:
    document = _document()
    confirmations = cast(list[dict[str, object]], document["confirmations"])
    document["confirmations"] = [
        value
        for value in confirmations
        if value["confirmation_code"] != SIGNED_SOURCE_CONFIRMATION_CODE
    ]
    stack = _Stack()

    with pytest.raises(SignedSourcePolicyError) as raised:
        stack.producer.produce(
            _snapshot(document),
            _planned(PDF_PAGE_ID),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED
    assert stack.rasterizer.requests == []
    assert stack.sources.pdf_calls == stack.sources.searchable_calls == 0


def test_candidate_is_single_use_and_producer_allows_only_one_active_heavy_candidate() -> None:
    stack = _Stack()
    snapshot = _snapshot(_document(overlays=False))
    first = stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )
    second = stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    with first:
        _assert_package_error(PackagePdfCandidateErrorCode.LIMIT_EXCEEDED, second.__enter__)

    _assert_package_error(PackagePdfCandidateErrorCode.SESSION_STATE_INVALID, first.__enter__)
    with stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    ):
        pass


def test_invalid_worker_report_closes_resources_and_releases_producer() -> None:
    stack = _Stack()
    stack.searchable.report_override = object()
    snapshot = _snapshot(_document(overlays=False))
    session = stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    _assert_package_error(PackagePdfCandidateErrorCode.RESULT_INVALID, session.__enter__)

    assert stack.sources.batches[0].closed
    assert stack.searchable.candidate is not None and stack.searchable.candidate.closed
    stack.searchable.report_override = None
    with stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    ):
        pass


def test_source_failure_is_sanitized_and_releases_the_active_slot() -> None:
    stack = _Stack()
    raw = f"{PRIVATE_TOKEN} C:/private/source.pdf"
    error = SourceIngestError(SourceIngestErrorCode.SOURCE_CHANGED, raw)
    stack.sources.error = error
    snapshot = _snapshot(_document(overlays=False))
    session = stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    mapped = _assert_package_error(PackagePdfCandidateErrorCode.SOURCE_CHANGED, session.__enter__)

    assert raw not in repr(mapped) + str(mapped)
    stack.sources.error = None
    with stack.producer.produce(
        snapshot,
        _planned(IMAGE_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    ):
        pass


def test_unavailable_selected_ocr_language_fails_before_source_borrow() -> None:
    languages = _Languages(unavailable=True)
    stack = _Stack(languages=languages)

    _assert_package_error(
        PackagePdfCandidateErrorCode.OCR_LANGUAGE_UNAVAILABLE,
        lambda: stack.producer.produce(
            _snapshot(_document(overlays=False)),
            _planned(IMAGE_PAGE_ID),
            deadline=lambda: False,
            cancellation=lambda: False,
        ),
    )

    assert languages.calls == 1
    assert stack.sources.pdf_calls == stack.sources.searchable_calls == 0


def test_flattened_input_reference_limit_fails_before_signature_decode() -> None:
    stack = _Stack()
    stack.flattened.maximum_input_references = 2

    _assert_package_error(
        PackagePdfCandidateErrorCode.LIMIT_EXCEEDED,
        lambda: stack.producer.produce(
            _snapshot(),
            _planned(PDF_PAGE_ID),
            deadline=lambda: False,
            cancellation=lambda: False,
        ),
    )

    assert stack.rasterizer.requests == []
    assert stack.sources.pdf_calls == 0


@pytest.mark.parametrize(
    ("deadline", "cancellation", "code"),
    (
        (lambda: False, lambda: True, PackagePdfCandidateErrorCode.CANCELLED),
        (lambda: True, lambda: False, PackagePdfCandidateErrorCode.TIMED_OUT),
        (lambda: 0, lambda: False, PackagePdfCandidateErrorCode.INTERNAL_ERROR),
    ),
)
def test_control_probes_fail_closed_before_planning(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    code: PackagePdfCandidateErrorCode,
) -> None:
    stack = _Stack()

    _assert_package_error(
        code,
        lambda: stack.producer.produce(
            _snapshot(),
            _planned(PDF_PAGE_ID),
            deadline=deadline,
            cancellation=cancellation,
        ),
    )

    assert stack.rasterizer.requests == []
    assert stack.sources.pdf_calls == stack.sources.searchable_calls == 0


def test_report_and_error_representations_hide_package_authority_and_content() -> None:
    stack = _Stack()
    snapshot = _snapshot()
    session = stack.producer.produce(
        snapshot,
        _planned(PDF_PAGE_ID),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    with session as candidate:
        representation = repr(candidate.report)

    error = PackagePdfCandidateError(
        PackagePdfCandidateErrorCode.RESULT_INVALID,
        f"{PRIVATE_TOKEN} C:/private/path",
    )
    combined = representation + repr(error) + str(error)
    for private in (
        PRIVATE_OUTPUT_KEY,
        PRIVATE_FILENAME,
        PRIVATE_TOKEN,
        str(PDF_PAGE_ID),
        str(PDF_SOURCE_ID),
        "f" * 64,
    ):
        assert private not in combined
