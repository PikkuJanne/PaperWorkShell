from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.app.searchable_pdf_export import (
    SearchablePdfDestination,
    SearchablePdfExportBroker,
    SearchablePdfExportProgressSink,
    SearchablePdfExportService,
    SearchablePdfHistoryRecorder,
    VerifiedSearchablePdfCandidate,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedSearchablePdfSourceBatchLease,
    CommittedSearchablePdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestSaveResult,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportProgress,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryPlanBinding,
    PdfExportHistoryRecord,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PdfExportRetryRepository,
    PreparedPdfExportRetry,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportOutcome,
    SearchablePdfExportPlan,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
    build_searchable_pdf_export_plan,
)

ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000099")
DESTINATION = Path("C:/synthetic/searchable-output.pdf")
PAYLOAD = b"%PDF-1.7\n% searchable synthetic\n%%EOF\n"
RECORDED_AT = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _snapshot() -> ManifestSnapshot:
    document = json.loads(
        (ROOT / "schemas/examples/case-manifest-v2-representative.json").read_text("utf-8")
    )
    document["overlays"] = []
    return ManifestSnapshot(
        case_id=CASE_ID,
        schema_version=2,
        revision=document["revision"],
        document=document,
        sha256="f" * 64,
        cas_token="manifest-cas-token-v2",
    )


def _plan() -> SearchablePdfExportPlan:
    return build_searchable_pdf_export_plan(_snapshot())


def _report(plan: SearchablePdfExportPlan) -> SearchablePdfVerificationReport:
    image, signed_pdf = plan.pages
    return SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        output_byte_size=len(PAYLOAD),
        page_outcomes=(
            SearchablePdfPageOutcome(
                SearchablePdfPageEligibility(
                    image.output_page_index,
                    image.page_id,
                    SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
                ),
                SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
                SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
            ),
            SearchablePdfPageOutcome(
                SearchablePdfPageEligibility(
                    signed_pdf.output_page_index,
                    signed_pdf.page_id,
                    SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE,
                ),
                SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
                SearchablePdfPageSearchability.NOT_VERIFIED,
                SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED,
            ),
        ),
    )


class _Manifests:
    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, _snapshot())

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        del (
            case_id,
            candidate,
            expected_revision,
            expected_sha256,
            expected_cas_token,
        )
        raise AssertionError("searchable export must not save the manifest directly")


@dataclass(slots=True)
class _LeasedSource:
    source_id: UUID
    media_type: SearchablePdfSourceMediaType
    byte_size: int
    page_count: int
    sha256: str
    signature_state: SearchablePdfSourceSignatureState
    encryption_state: SearchablePdfSourceEncryptionState
    handle_value: int


class _Batch:
    def __init__(self, plan: SearchablePdfExportPlan) -> None:
        self.case_id = plan.case_id
        self.manifest_revision = plan.manifest_revision
        self.manifest_sha256 = plan.manifest_sha256
        self.manifest_cas_token = plan.manifest_cas_token
        self.sources = tuple(
            _LeasedSource(
                source.source_id,
                source.media_type,
                source.byte_size,
                source.page_count,
                source.sha256,
                source.signature_state,
                source.encryption_state,
                100 + index,
            )
            for index, source in enumerate(plan.sources)
        )
        self.revalidated = False
        self.closed = False

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
        return None

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        del deadline, cancellation
        self.revalidated = True


class _Sources:
    def __init__(self, plan: SearchablePdfExportPlan) -> None:
        self.batch = _Batch(plan)
        self.borrow_calls = 0

    def borrow_committed_searchable_pdf_sources(
        self,
        plan: SearchablePdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> _Batch:
        del deadline, cancellation
        assert plan == _plan()
        self.borrow_calls += 1
        return self.batch


class _Candidate:
    def __init__(self, report: SearchablePdfVerificationReport) -> None:
        self.report = report
        self.position = 0
        self.closed = False

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


class _Broker:
    maximum_output_bytes = 16 * 1024 * 1024

    def __init__(self, plan: SearchablePdfExportPlan) -> None:
        self.candidate = _Candidate(_report(plan))
        self.export_calls = 0

    def export(
        self,
        plan: SearchablePdfExportPlan,
        sources: CommittedSearchablePdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink,
    ) -> _Candidate:
        del sources, deadline, cancellation, progress
        assert plan == _plan()
        self.export_calls += 1
        return self.candidate


class _Destination:
    def __init__(self, failure: PdfExportError | None = None) -> None:
        self.committed = False
        self.failure = failure
        self.capacity_calls = 0
        self.commit_calls = 0

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        assert destination == DESTINATION
        assert estimated_byte_size == _Broker.maximum_output_bytes
        self.capacity_calls += 1

    def commit(
        self,
        destination: Path,
        source: VerifiedSearchablePdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> None:
        del cancellation, deadline
        assert destination == DESTINATION
        self.commit_calls += 1
        if self.failure is not None:
            raise self.failure
        assert source.read(expected_byte_size) == PAYLOAD
        assert source.read(1) == b""
        assert expected_sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        self.committed = True


class _History:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.bindings: list[PdfExportHistoryPlanBinding] = []
        self.reserved_id_calls = 0

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]:
        assert case_id == CASE_ID
        self.reserved_id_calls += 1
        return frozenset()

    def record_and_link_binding(
        self,
        binding: PdfExportHistoryPlanBinding,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        self.bindings.append(binding)
        assert binding.semantic_fingerprint == record.semantic_fingerprint
        if self.fail:
            raise RuntimeError("synthetic history failure")
        return PdfExportOutcome(
            export_id=outcome.export_id,
            status=PdfExportOutcomeStatus.COMMITTED_RECORDED,
            report=outcome.report,
            destination_reopened=True,
            history_retry=outcome.history_retry,
        )


class _RetryLease:
    def __init__(self, receipt: PdfExportHistoryRetryReceipt) -> None:
        self.receipt = receipt
        self.committed = False
        self.resolved = False
        self.discarded = False
        self.closed = False

    def mark_committed(self) -> bool:
        self.committed = True
        return True

    def resolve(self) -> bool:
        self.resolved = True
        return True

    def discard_prepared(self) -> bool:
        self.discarded = True
        return True

    def close(self) -> None:
        self.closed = True
        return None


class _Retries:
    def __init__(self) -> None:
        self.lease: _RetryLease | None = None

    def prepare(self, receipt: PdfExportHistoryRetryReceipt) -> PreparedPdfExportRetry:
        self.lease = _RetryLease(receipt)
        return self.lease

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        assert case_id == CASE_ID
        return PdfExportRetryInventory()

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del case_id, export_id, record_sha256
        return True

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del case_id, export_id, record_sha256
        return True


def _service(
    *,
    destination_failure: PdfExportError | None = None,
    history_failure: bool = False,
) -> tuple[
    SearchablePdfExportService,
    _Sources,
    _Broker,
    _Destination,
    _History,
    _Retries,
]:
    plan = _plan()
    sources = _Sources(plan)
    broker = _Broker(plan)
    destination = _Destination(destination_failure)
    history = _History(fail=history_failure)
    retries = _Retries()
    manifests = _Manifests()
    assert isinstance(manifests, ManifestRepository)
    assert isinstance(sources, CommittedSearchablePdfSourceBatchStore)
    assert isinstance(broker, SearchablePdfExportBroker)
    assert isinstance(destination, SearchablePdfDestination)
    assert isinstance(history, SearchablePdfHistoryRecorder)
    assert isinstance(retries, PdfExportRetryRepository)
    return (
        SearchablePdfExportService(
            manifests,
            sources,
            broker,
            destination,
            history,
            retries,
            clock=lambda: RECORDED_AT,
            id_factory=lambda: EXPORT_ID,
        ),
        sources,
        broker,
        destination,
        history,
        retries,
    )


def test_service_commits_verified_mixed_candidate_and_reuses_history_v1() -> None:
    service, sources, broker, destination, history, retries = _service()
    progress: list[PdfExportProgress] = []

    outcome = service.export_pdf(
        CASE_ID,
        DESTINATION,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert type(outcome) is SearchablePdfExportOutcome
    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.report.ocr_text_page_count == 1
    assert outcome.report.needs_review_page_count == 1
    assert destination.committed
    assert broker.candidate.closed
    assert sources.batch.closed
    assert history.bindings == [
        PdfExportHistoryPlanBinding(
            case_id=outcome.report.plan.case_id,
            manifest_revision=outcome.report.plan.manifest_revision,
            manifest_sha256=outcome.report.plan.manifest_sha256,
            semantic_fingerprint=outcome.report.plan.semantic_fingerprint,
            page_count=outcome.report.plan.page_count,
            unsigned_derivative=True,
        )
    ]
    assert retries.lease is not None
    assert retries.lease.committed
    assert retries.lease.resolved
    assert retries.lease.closed
    assert progress[0].completed_pages == 0
    assert progress[-1].completed_pages == outcome.report.plan.page_count


def test_low_disk_preflight_stops_before_authoritative_work_and_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sources, broker, destination, history, retries = _service()
    private_canary = "PRIVATE-LOW-DISK C:/cases/applicant.pdf"
    capacity_attempts = 0
    ordinary_capacity = destination.require_capacity

    def fail_once(destination_path: Path, *, estimated_byte_size: int) -> None:
        nonlocal capacity_attempts
        capacity_attempts += 1
        if capacity_attempts == 1:
            raise OSError(private_canary)
        ordinary_capacity(destination_path, estimated_byte_size=estimated_byte_size)

    monkeypatch.setattr(destination, "require_capacity", fail_once)

    with pytest.raises(PdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    rendered = f"{raised.value!s} {raised.value!r} {raised.value.args!r}"
    assert raised.value.code is PdfExportErrorCode.DESTINATION_FAILED
    assert raised.value.context == "export.capacity_preflight"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert private_canary not in rendered
    assert capacity_attempts == 1
    assert destination.capacity_calls == 0
    assert destination.commit_calls == 0
    assert sources.borrow_calls == 0
    assert broker.export_calls == 0
    assert history.reserved_id_calls == 0
    assert history.bindings == []
    lease_before_retry = retries.lease
    assert lease_before_retry is None
    assert not sources.batch.closed
    assert not broker.candidate.closed

    outcome = service.export_pdf(
        CASE_ID,
        DESTINATION,
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert capacity_attempts == 2
    assert destination.capacity_calls == 1
    assert destination.commit_calls == 1
    assert destination.committed
    assert sources.borrow_calls == 1
    assert broker.export_calls == 1
    assert history.reserved_id_calls == 1
    assert len(history.bindings) == 1
    assert retries.lease is not None
    assert retries.lease.committed
    assert retries.lease.resolved


@pytest.mark.parametrize(
    ("deadline", "cancellation", "code"),
    [
        (lambda: True, lambda: False, PdfExportErrorCode.TIMED_OUT),
        (lambda: False, lambda: True, PdfExportErrorCode.CANCELLED),
    ],
)
def test_service_stops_before_worker_and_publication_for_terminal_request(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    code: PdfExportErrorCode,
) -> None:
    service, _sources, _broker, destination, _history, _retries = _service()

    with pytest.raises(PdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=deadline,
            cancellation=cancellation,
        )

    assert raised.value.code is code
    assert not destination.committed


def test_service_rejects_mismatched_candidate_before_destination() -> None:
    service, sources, broker, destination, _history, retries = _service()
    object.__setattr__(broker.candidate.report, "plan", object())

    with pytest.raises(SearchablePdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is SearchablePdfExportErrorCode.RESULT_INVALID
    assert not destination.committed
    assert broker.candidate.closed
    assert sources.batch.closed
    assert retries.lease is None


def test_service_discards_prepared_retry_when_destination_definitely_fails() -> None:
    failure = PdfExportError(PdfExportErrorCode.DESTINATION_FAILED, "export.test")
    service, sources, broker, destination, history, retries = _service(destination_failure=failure)

    with pytest.raises(PdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value is failure
    assert not destination.committed
    assert history.bindings == []
    assert broker.candidate.closed
    assert sources.batch.closed
    assert retries.lease is not None
    assert retries.lease.discarded
    assert not retries.lease.committed
    assert retries.lease.closed


def test_untyped_destination_failure_closes_but_preserves_prepared_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, sources, broker, destination, history, retries = _service()
    private_canary = "PRIVATE-COMMIT C:/cases/applicant.pdf"

    def fail_uncertain_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(private_canary)

    monkeypatch.setattr(destination, "commit", fail_uncertain_commit)

    with pytest.raises(SearchablePdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    rendered = f"{raised.value!s} {raised.value!r} {raised.value.args!r}"
    assert raised.value.code is SearchablePdfExportErrorCode.INTERNAL_ERROR
    assert raised.value.context == "searchable.operation"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert private_canary not in rendered
    assert not destination.committed
    assert history.bindings == []
    assert broker.candidate.closed
    assert sources.batch.closed
    assert retries.lease is not None
    assert not retries.lease.discarded
    assert not retries.lease.committed
    assert retries.lease.closed


def test_service_returns_committed_history_pending_without_losing_retry_receipt() -> None:
    service, sources, broker, destination, history, retries = _service(history_failure=True)

    outcome = service.export_pdf(
        CASE_ID,
        DESTINATION,
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert destination.committed
    assert history.bindings
    assert broker.candidate.closed
    assert sources.batch.closed
    assert retries.lease is not None
    assert retries.lease.committed
    assert not retries.lease.resolved
    assert retries.lease.closed
    assert outcome.history_retry == retries.lease.receipt
