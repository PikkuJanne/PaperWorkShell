from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.pdf_export import (
    PdfExportProgressSink,
    PdfExportService,
    VerifiedPdfCandidate,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PreparedPdfExportRetry,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourcePolicyError,
    SignedSourcePolicyErrorCode,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode

CASE_ID = UUID("10000000-0000-4000-8000-000000000812")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000812")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000812")
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000812")
NEXT_EXPORT_ID = UUID("50000000-0000-4000-8000-000000000813")
DESTINATION = Path("C:/synthetic/verified-export.pdf")
RECORDED_AT = datetime(2026, 8, 6, 14, 15, 16, tzinfo=UTC)
PAYLOAD = b"%PDF-1.7\n% verified candidate\n%%EOF\n"


def _manifest_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-06T12:00:00Z",
        "updated_at": "2026-08-06T13:00:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "Synthetic source.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 91,
                "page_count": 1,
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-06T13:00:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(PAGE_ID),
                "source_id": str(SOURCE_ID),
                "source_page_index": 0,
            }
        ],
        "page_sequence_base": [str(PAGE_ID)],
        "page_sequence": [str(PAGE_ID)],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot() -> ManifestSnapshot:
    document = _manifest_document()
    return ManifestSnapshot(
        CASE_ID,
        1,
        7,
        document,
        hashlib.sha256(repr(document).encode("utf-8")).hexdigest(),
        "export_service_manifest_cas_0001",
    )


def _plan() -> PdfExportPlan:
    return build_pdf_export_plan(_snapshot())


def _report(plan: PdfExportPlan | None = None) -> PdfExportVerificationReport:
    selected = _plan() if plan is None else plan
    return PdfExportVerificationReport.for_verified_plan(
        selected,
        output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        output_byte_size=len(PAYLOAD),
    )


@dataclass(slots=True)
class _LeasedSource:
    source_id: UUID
    media_type: PdfExportSourceMediaType
    byte_size: int
    page_count: int
    sha256: str
    signature_state: PdfExportSourceSignatureState
    encryption_state: PdfExportSourceEncryptionState
    handle_value: int


class _Batch:
    def __init__(self, plan: PdfExportPlan, events: list[str]) -> None:
        self._events = events
        self.exit_error: BaseException | None = None
        self.suppress = False
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
                101 + index,
            )
            for index, source in enumerate(plan.sources)
        )

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        del deadline, cancellation
        self._events.append("source.revalidate")

    def __enter__(self) -> Self:
        self._events.append("source.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exception_type, exception, traceback
        self._events.append("source.exit")
        if self.exit_error is not None:
            raise self.exit_error
        return True if self.suppress else None


class _Manifests:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.result: object = ManifestLoadResult(ManifestLoadStatus.CURRENT, _snapshot())
        self.error: BaseException | None = None

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.events.append("manifest.load")
        if self.error is not None:
            raise self.error
        return cast(ManifestLoadResult, self.result)

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
        raise AssertionError("export orchestration must not save the manifest directly")


class _Sources:
    def __init__(self, plan: PdfExportPlan, events: list[str]) -> None:
        self.events = events
        self.batch: object = _Batch(plan, events)
        self.error: BaseException | None = None
        self.calls = 0

    def borrow_committed_pdf_sources(
        self,
        plan: PdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> _Batch:
        del deadline, cancellation
        assert plan.case_id == CASE_ID
        self.events.append("source.borrow")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return cast(_Batch, self.batch)


class _Candidate:
    def __init__(
        self,
        report: object,
        events: list[str],
        *,
        payload: bytes = PAYLOAD,
    ) -> None:
        self._report = report
        self._events = events
        self._payload = payload
        self._offset = 0
        self.exit_error: BaseException | None = None
        self.suppress = False

    @property
    def report(self) -> PdfExportVerificationReport:
        if isinstance(self._report, BaseException):
            raise self._report
        return cast(PdfExportVerificationReport, self._report)

    def read(self, size: int, /) -> bytes:
        end = min(len(self._payload), self._offset + size)
        selected = self._payload[self._offset : end]
        self._offset = end
        return selected

    def __enter__(self) -> Self:
        self._events.append("candidate.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exception_type, exception, traceback
        self._events.append("candidate.exit")
        if self.exit_error is not None:
            raise self.exit_error
        return True if self.suppress else None


class _Broker:
    def __init__(self, candidate: _Candidate, events: list[str]) -> None:
        self.events = events
        self.candidate: object = candidate
        self.error: BaseException | None = None

    def export(
        self,
        plan: PdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> VerifiedPdfCandidate:
        del deadline, cancellation
        assert plan.case_id == CASE_ID
        assert sources.case_id == CASE_ID
        self.events.append("broker.export")
        if self.error is not None:
            raise self.error
        progress(
            PdfExportProgress(
                PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
                plan.page_count,
                plan.page_count,
            )
        )
        return cast(VerifiedPdfCandidate, self.candidate)


class _Destination:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.error: BaseException | None = None
        self.calls = 0
        self.cancellation: CancellationProbe | None = None
        self.deadline: DeadlineProbe | None = None

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        assert destination == DESTINATION
        assert estimated_byte_size == 91

    def commit(
        self,
        destination: Path,
        source: VerifiedPdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> None:
        assert destination == DESTINATION
        self.events.append("destination.commit")
        self.calls += 1
        self.cancellation = cancellation
        self.deadline = deadline
        if self.error is not None:
            raise self.error
        assert source.read(expected_byte_size) == PAYLOAD
        assert source.read(1) == b""
        assert expected_byte_size == len(PAYLOAD)
        assert expected_sha256 == hashlib.sha256(PAYLOAD).hexdigest()


class _CapacityDestination(_Destination):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.capacity_error: BaseException | None = None

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        assert destination == DESTINATION
        assert estimated_byte_size == 91
        self.events.append("destination.capacity")
        if self.capacity_error is not None:
            raise self.capacity_error


class _CommitOnlyDestination:
    def commit(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _History:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.mode = "recorded"
        self.identifiers: frozenset[UUID] = frozenset()
        self.identifiers_result: object | None = None
        self.calls = 0
        self.record: PdfExportHistoryRecord | None = None

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]:
        assert case_id == CASE_ID
        self.events.append("history.ids")
        if isinstance(self.identifiers_result, BaseException):
            raise self.identifiers_result
        if self.identifiers_result is not None:
            return cast(frozenset[UUID], self.identifiers_result)
        return self.identifiers

    def record_and_link(
        self,
        plan: PdfExportPlan,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        assert plan.case_id == CASE_ID
        self.events.append("history.record")
        self.calls += 1
        self.record = record
        if self.mode == "closed_error":
            raise PdfExportHistoryError(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.test",
            )
        if self.mode == "raw_error":
            raise RuntimeError("private history dependency detail")
        if self.mode == "base_error":
            raise KeyboardInterrupt
        if self.mode == "hostile_result":
            return cast(PdfExportOutcome, object())
        if self.mode == "pending":
            return outcome
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
        self.mark_error: BaseException | None = None
        self.mark_result: object = True
        self.resolve_error: BaseException | None = None
        self.resolve_result = True
        self.discard_error: BaseException | None = None
        self.discard_result = True
        self.mark_calls = 0
        self.resolve_calls = 0
        self.discard_calls = 0
        self.close_calls = 0
        self.close_error: BaseException | None = None

    def mark_committed(self) -> bool:
        self.mark_calls += 1
        if self.mark_error is not None:
            raise self.mark_error
        return cast(bool, self.mark_result)

    def resolve(self) -> bool:
        self.resolve_calls += 1
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.resolve_result

    def discard_prepared(self) -> bool:
        self.discard_calls += 1
        if self.discard_error is not None:
            raise self.discard_error
        return self.discard_result

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _Retries:
    def __init__(self) -> None:
        self.inventory_result: object = PdfExportRetryInventory()
        self.prepare_error: BaseException | None = None
        self.lease_factory: Callable[[PdfExportHistoryRetryReceipt], _RetryLease] = _RetryLease
        self.lease: _RetryLease | None = None
        self.prepare_calls = 0
        self.inventory_calls = 0
        self.discard_committed_calls = 0
        self.discard_prepared_calls = 0

    def prepare(
        self,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PreparedPdfExportRetry:
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error
        self.lease = self.lease_factory(receipt)
        return self.lease

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        assert case_id == CASE_ID
        self.inventory_calls += 1
        if isinstance(self.inventory_result, BaseException):
            raise self.inventory_result
        return cast(PdfExportRetryInventory, self.inventory_result)

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del record_sha256
        assert case_id == CASE_ID
        assert export_id == EXPORT_ID
        self.discard_committed_calls += 1
        return True

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del record_sha256
        assert case_id == CASE_ID
        assert export_id == EXPORT_ID
        self.discard_prepared_calls += 1
        return True


def _retry_receipt(
    export_id: UUID = EXPORT_ID,
) -> PdfExportHistoryRetryReceipt:
    plan = _plan()
    report = _report(plan)
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=export_id,
        report=report,
        recorded_at=RECORDED_AT,
    )
    return build_pdf_export_history_retry_receipt(plan, record, report)


class _Clock:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.value: object = RECORDED_AT
        self.calls = 0

    def __call__(self) -> datetime:
        self.events.append("clock")
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return cast(datetime, self.value)


@dataclass(slots=True)
class _Stack:
    service: PdfExportService
    manifests: _Manifests
    sources: _Sources
    batch: _Batch
    broker: _Broker
    candidate: _Candidate
    destination: _Destination
    history: _History
    retries: _Retries
    clock: _Clock
    events: list[str]


def _stack() -> _Stack:
    events: list[str] = []
    plan = _plan()
    manifests = _Manifests(events)
    sources = _Sources(plan, events)
    batch = cast(_Batch, sources.batch)
    candidate = _Candidate(_report(plan), events)
    broker = _Broker(candidate, events)
    destination = _Destination(events)
    history = _History(events)
    retries = _Retries()
    clock = _Clock(events)
    service = PdfExportService(
        manifests,
        sources,
        broker,
        destination,
        history,
        retries,
        clock=clock,
        id_factory=lambda: EXPORT_ID,
    )
    return _Stack(
        service,
        manifests,
        sources,
        batch,
        broker,
        candidate,
        destination,
        history,
        retries,
        clock,
        events,
    )


def _export(
    stack: _Stack,
    *,
    deadline: DeadlineProbe = lambda: False,
    cancellation: CancellationProbe = lambda: False,
    progress: PdfExportProgressSink | None = None,
) -> PdfExportOutcome:
    sink: PdfExportProgressSink = (lambda _value: None) if progress is None else progress
    return stack.service.export_pdf(
        CASE_ID,
        DESTINATION,
        deadline=deadline,
        cancellation=cancellation,
        progress=sink,
    )


def test_destination_without_capacity_contract_is_rejected_at_composition() -> None:
    stack = _stack()

    with pytest.raises(PdfExportError) as raised:
        PdfExportService(
            stack.manifests,
            stack.sources,
            stack.broker,
            cast(Any, _CommitOnlyDestination()),
            stack.history,
            stack.retries,
        )

    assert raised.value.code is PdfExportErrorCode.MODEL_INVALID


def test_capacity_preflight_runs_before_history_source_or_worker_activity() -> None:
    stack = _stack()
    destination = _CapacityDestination(stack.events)
    stack.destination = destination
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: EXPORT_ID,
    )
    destination.capacity_error = PdfExportError(
        PdfExportErrorCode.DESTINATION_CAPACITY_EXCEEDED,
        "export.destination_capacity",
    )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.DESTINATION_CAPACITY_EXCEEDED
    assert stack.events == ["manifest.load", "destination.capacity"]
    assert stack.sources.calls == 0


def test_marker_source_without_exact_confirmation_stops_before_all_export_side_effects() -> None:
    stack = _stack()
    document = _manifest_document()
    source = cast(dict[str, object], cast(list[object], document["sources"])[0])
    source["signature_state"] = "markers_detected"
    snapshot = ManifestSnapshot(
        CASE_ID,
        1,
        7,
        document,
        "d" * 64,
        "export_service_manifest_cas_0208",
    )
    stack.manifests.result = ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot)

    with pytest.raises(SignedSourcePolicyError) as raised:
        _export(stack)

    assert raised.value.code is SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED
    assert stack.events == ["manifest.load"]
    assert stack.sources.calls == 0
    assert stack.history.calls == 0
    assert stack.retries.lease is None


def test_capacity_preflight_preserves_process_control_exceptions() -> None:
    stack = _stack()
    destination = _CapacityDestination(stack.events)
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: EXPORT_ID,
    )
    destination.capacity_error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _export(stack)

    assert stack.events == ["manifest.load", "destination.capacity"]


def test_full_success_orders_progress_cleanup_destination_and_history() -> None:
    stack = _stack()

    def deadline() -> bool:
        return False

    def cancellation() -> bool:
        return False

    def progress(value: PdfExportProgress) -> None:
        stack.events.append(f"progress.{value.phase.value}")

    outcome = _export(
        stack,
        deadline=deadline,
        cancellation=cancellation,
        progress=progress,
    )

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.export_id == EXPORT_ID
    assert outcome.destination_reopened
    assert not outcome.export_retry_required
    assert stack.history.record is not None
    assert stack.history.record.export_id == EXPORT_ID
    assert stack.destination.deadline is deadline
    assert stack.destination.cancellation is cancellation
    assert stack.retries.inventory_calls == 1
    assert stack.retries.prepare_calls == 1
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 1
    assert stack.retries.lease.discard_calls == 0
    assert stack.retries.lease.close_calls == 1
    assert stack.events == [
        "manifest.load",
        "history.ids",
        "progress.preparing",
        "source.borrow",
        "source.enter",
        "broker.export",
        "progress.assembling_and_verifying",
        "candidate.enter",
        "clock",
        "progress.saving",
        "destination.commit",
        "candidate.exit",
        "source.exit",
        "progress.recording",
        "history.record",
        "progress.complete",
    ]


@pytest.mark.parametrize("failure", ("history_closed", "history_raw", "history_result"))
def test_post_commit_history_failures_return_exact_retry_receipt(failure: str) -> None:
    stack = _stack()
    if failure == "history_closed":
        stack.history.mode = "closed_error"
    elif failure == "history_raw":
        stack.history.mode = "raw_error"
    else:
        stack.history.mode = "hostile_result"

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert outcome.destination_reopened
    assert not outcome.export_retry_required
    assert stack.history.record is not None
    assert outcome.history_retry.record_sha256 == stack.history.record.sha256
    assert stack.destination.calls == 1
    assert stack.history.calls == 1
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 0
    assert stack.retries.lease.discard_calls == 0
    assert stack.retries.lease.close_calls == 1


@pytest.mark.parametrize(
    "failure",
    (
        PdfExportHistoryError(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.test",
        ),
        RuntimeError("private retry storage detail"),
        KeyboardInterrupt(),
    ),
)
def test_retry_prepare_failure_blocks_destination_and_is_sanitized(
    failure: BaseException,
) -> None:
    stack = _stack()
    stack.retries.prepare_error = failure

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED
    assert "private retry" not in str(raised.value)
    assert stack.destination.calls == 0
    assert stack.history.calls == 0


def test_committed_marker_failure_uses_durable_history_as_independent_proof() -> None:
    stack = _stack()

    def lease_factory(receipt: PdfExportHistoryRetryReceipt) -> _RetryLease:
        lease = _RetryLease(receipt)
        lease.mark_error = RuntimeError("private marker publication detail")
        return lease

    stack.retries.lease_factory = lease_factory

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 0
    assert stack.retries.discard_prepared_calls == 1
    assert stack.retries.lease.close_calls == 1


@pytest.mark.parametrize("mark_result", (False, object()))
def test_unconfirmed_marker_result_uses_recorded_history_as_proof(mark_result: object) -> None:
    stack = _stack()

    def lease_factory(receipt: PdfExportHistoryRetryReceipt) -> _RetryLease:
        lease = _RetryLease(receipt)
        lease.mark_result = mark_result
        return lease

    stack.retries.lease_factory = lease_factory

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert stack.retries.lease is not None
    assert stack.retries.lease.resolve_calls == 0
    assert stack.retries.discard_prepared_calls == 1


def test_retry_lease_close_failure_cannot_relabel_committed_output() -> None:
    stack = _stack()

    def lease_factory(receipt: PdfExportHistoryRetryReceipt) -> _RetryLease:
        lease = _RetryLease(receipt)
        lease.close_error = KeyboardInterrupt()
        return lease

    stack.retries.lease_factory = lease_factory

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert stack.retries.lease is not None
    assert stack.retries.lease.close_calls == 1


def test_committed_marker_and_history_failure_preserve_prepared_receipt() -> None:
    stack = _stack()
    stack.history.mode = "closed_error"

    def lease_factory(receipt: PdfExportHistoryRetryReceipt) -> _RetryLease:
        lease = _RetryLease(receipt)
        lease.mark_result = False
        return lease

    stack.retries.lease_factory = lease_factory

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 0
    assert stack.retries.lease.discard_calls == 0
    assert stack.retries.discard_prepared_calls == 0
    assert stack.retries.lease.close_calls == 1


@pytest.mark.parametrize(
    "value",
    (
        RuntimeError("private clock detail"),
        KeyboardInterrupt(),
        "not a timestamp",
        datetime(2026, 8, 6, 14, 15, 16),
    ),
)
def test_clock_failure_is_sanitized_before_destination_commit(value: object) -> None:
    stack = _stack()
    stack.clock.value = value

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert stack.destination.calls == 0
    assert stack.history.calls == 0
    assert stack.events[-3:] == [
        "clock",
        "candidate.exit",
        "source.exit",
    ]


@pytest.mark.parametrize(
    "failed_phase",
    (PdfExportProgressPhase.RECORDING, PdfExportProgressPhase.COMPLETE),
)
def test_post_commit_progress_failure_is_nonfatal_and_does_not_change_history(
    failed_phase: PdfExportProgressPhase,
) -> None:
    stack = _stack()

    def progress(value: PdfExportProgress) -> None:
        if value.phase is failed_phase:
            raise RuntimeError("private post-commit progress detail")

    outcome = _export(stack, progress=progress)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert stack.destination.calls == 1
    assert stack.history.calls == 1


@pytest.mark.parametrize("failure", ("history", "recording", "complete"))
def test_post_commit_base_exceptions_cannot_relabel_committed_output(
    failure: str,
) -> None:
    stack = _stack()
    if failure == "history":
        stack.history.mode = "base_error"

    def progress(value: PdfExportProgress) -> None:
        if (failure == "recording" and value.phase is PdfExportProgressPhase.RECORDING) or (
            failure == "complete" and value.phase is PdfExportProgressPhase.COMPLETE
        ):
            raise KeyboardInterrupt

    outcome = _export(stack, progress=progress)

    assert outcome.destination_reopened
    assert outcome.status is (
        PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
        if failure == "history"
        else PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    assert stack.destination.calls == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (
            PdfExportError(PdfExportErrorCode.DESTINATION_FAILED, "export.test"),
            PdfExportErrorCode.DESTINATION_FAILED,
        ),
        (RuntimeError("private destination detail"), PdfExportErrorCode.INTERNAL_ERROR),
        (KeyboardInterrupt(), PdfExportErrorCode.INTERNAL_ERROR),
    ),
)
def test_destination_failure_closes_candidate_then_sources_and_never_records_history(
    failure: BaseException,
    expected: PdfExportErrorCode,
) -> None:
    stack = _stack()
    stack.destination.error = failure

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is expected
    assert stack.events[-3:] == ["destination.commit", "candidate.exit", "source.exit"]
    assert stack.clock.calls == 1
    assert stack.history.calls == 0


@pytest.mark.parametrize(
    ("failure", "expected", "discarded"),
    (
        (
            PdfExportError(PdfExportErrorCode.DESTINATION_FAILED, "export.test"),
            PdfExportErrorCode.DESTINATION_FAILED,
            True,
        ),
        (
            PdfExportError(
                PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN,
                "export.test",
            ),
            PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN,
            False,
        ),
        (RuntimeError("private destination detail"), PdfExportErrorCode.INTERNAL_ERROR, False),
        (KeyboardInterrupt(), PdfExportErrorCode.INTERNAL_ERROR, False),
    ),
)
def test_destination_commit_certainty_controls_prepared_receipt_cleanup(
    failure: BaseException,
    expected: PdfExportErrorCode,
    discarded: bool,
) -> None:
    stack = _stack()
    stack.destination.error = failure

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is expected
    assert stack.retries.lease is not None
    assert stack.retries.lease.discard_calls == (1 if discarded else 0)
    assert stack.retries.lease.mark_calls == 0
    assert stack.retries.lease.close_calls == 1
    assert stack.history.calls == 0


@pytest.mark.parametrize(
    ("source_code", "expected"),
    (
        (SourceIngestErrorCode.CANCELLED, PdfExportErrorCode.CANCELLED),
        (SourceIngestErrorCode.TIMED_OUT, PdfExportErrorCode.TIMED_OUT),
        (SourceIngestErrorCode.MANIFEST_CONFLICT, PdfExportErrorCode.PLAN_STALE),
        (SourceIngestErrorCode.SOURCE_UNAVAILABLE, PdfExportErrorCode.SOURCE_CHANGED),
        (SourceIngestErrorCode.SOURCE_CHANGED, PdfExportErrorCode.SOURCE_CHANGED),
        (SourceIngestErrorCode.CAPACITY_EXCEEDED, PdfExportErrorCode.LIMIT_EXCEEDED),
        (SourceIngestErrorCode.MANIFEST_INVALID, PdfExportErrorCode.MANIFEST_INVALID),
        (
            SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED,
            PdfExportErrorCode.MANIFEST_INVALID,
        ),
    ),
)
def test_source_failures_map_to_closed_export_errors(
    source_code: SourceIngestErrorCode,
    expected: PdfExportErrorCode,
) -> None:
    stack = _stack()
    stack.sources.error = SourceIngestError(source_code, "source.test")

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is expected
    assert stack.history.calls == 0


def test_cancellation_and_stale_manifest_stop_before_later_dependencies() -> None:
    cancelled = _stack()
    with pytest.raises(PdfExportError) as cancelled_error:
        _export(cancelled, cancellation=lambda: True)
    assert cancelled_error.value.code is PdfExportErrorCode.CANCELLED
    assert cancelled.events == []

    stale = _stack()
    stale.manifests.error = ManifestRepositoryError(
        ManifestRepositoryErrorCode.REVISION_CONFLICT,
        "manifest.test",
    )
    with pytest.raises(PdfExportError) as stale_error:
        _export(stale)
    assert stale_error.value.code is PdfExportErrorCode.PLAN_STALE
    assert stale.events == ["manifest.load"]


@pytest.mark.parametrize("candidate_failure", ("report", "mismatch"))
def test_hostile_candidate_is_rejected_inside_both_cleanup_contexts(
    candidate_failure: str,
) -> None:
    stack = _stack()
    if candidate_failure == "report":
        stack.candidate._report = RuntimeError("private candidate detail")
    else:
        stack.candidate._report = replace(
            _report(),
            semantic_fingerprint="f" * 64,
        )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID
    assert stack.events[-2:] == ["candidate.exit", "source.exit"]
    assert stack.destination.calls == 0
    assert stack.history.calls == 0
    assert "private candidate" not in str(raised.value)


@pytest.mark.parametrize("boundary", ("candidate", "source"))
def test_suppressing_context_cannot_turn_precommit_failure_into_success(
    boundary: str,
) -> None:
    stack = _stack()
    if boundary == "candidate":
        stack.destination.error = PdfExportError(
            PdfExportErrorCode.DESTINATION_FAILED,
            "export.test",
        )
        stack.candidate.suppress = True
    else:
        stack.broker.error = PdfExportError(
            PdfExportErrorCode.WORKER_FAILED,
            "export.test",
        )
        stack.batch.suppress = True

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.DESTINATION_FAILED
    assert stack.history.calls == 0
    assert stack.destination.calls == (1 if boundary == "candidate" else 0)


@pytest.mark.parametrize("boundary", ("candidate", "source"))
def test_cleanup_base_exception_after_verified_commit_keeps_committed_outcome(
    boundary: str,
) -> None:
    stack = _stack()
    if boundary == "candidate":
        stack.candidate.exit_error = KeyboardInterrupt()
    else:
        stack.batch.exit_error = KeyboardInterrupt()

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert stack.destination.calls == 1
    assert stack.history.calls == 1


def test_hostile_dependency_results_are_sanitized_or_conservatively_pending() -> None:
    bad_manifest = _stack()
    bad_manifest.manifests.result = object()
    with pytest.raises(PdfExportError) as manifest_error:
        _export(bad_manifest)
    assert manifest_error.value.code is PdfExportErrorCode.MANIFEST_INVALID

    bad_sources = _stack()
    bad_sources.sources.batch = object()
    with pytest.raises(PdfExportError) as source_error:
        _export(bad_sources)
    assert source_error.value.code is PdfExportErrorCode.INTERNAL_ERROR

    bad_broker = _stack()
    bad_broker.broker.candidate = object()
    with pytest.raises(PdfExportError) as broker_error:
        _export(bad_broker)
    assert broker_error.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert bad_broker.events[-1] == "source.exit"

    bad_clock = _stack()
    bad_clock.clock.value = "not a timestamp"
    with pytest.raises(PdfExportError) as clock_error:
        _export(bad_clock)
    assert clock_error.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert bad_clock.destination.calls == 0
    assert bad_clock.history.calls == 0


def test_invalid_probe_and_identifier_dependencies_are_sanitized() -> None:
    bad_probe = _stack()
    with pytest.raises(PdfExportError) as probe_error:
        _export(bad_probe, deadline=cast(DeadlineProbe, lambda: "false"))
    assert probe_error.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert bad_probe.events == []

    stack = _stack()
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        stack.destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: CASE_ID,
    )
    with pytest.raises(PdfExportError) as identifier_error:
        _export(stack)
    assert identifier_error.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert stack.sources.calls == 0


def test_existing_manifest_export_id_is_retried_before_worker_or_destination() -> None:
    stack = _stack()
    document = _manifest_document()
    history = cast(dict[str, object], document["export_history"])
    cast(list[object], history["entries"]).append(
        {
            "export_id": str(EXPORT_ID),
            "record_version": 1,
            "sha256": "f" * 64,
        }
    )
    snapshot = ManifestSnapshot(
        CASE_ID,
        1,
        7,
        document,
        hashlib.sha256(repr(document).encode("utf-8")).hexdigest(),
        "export_service_manifest_cas_0002",
    )
    stack.manifests.result = ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot)
    identifiers = iter((EXPORT_ID, NEXT_EXPORT_ID))
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        stack.destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: next(identifiers),
    )

    outcome = _export(stack)

    assert outcome.export_id == NEXT_EXPORT_ID
    assert stack.history.record is not None
    assert stack.history.record.export_id == NEXT_EXPORT_ID
    assert stack.destination.calls == 1


def test_repeated_set_group_identifier_remains_a_valid_manifest_namespace() -> None:
    stack = _stack()
    document = _manifest_document()
    group_id = UUID("90000000-0000-4000-8000-000000000812")
    document["page_operations"] = [
        {
            "operation_id": "40000000-0000-4000-8000-000000000812",
            "operation_version": 1,
            "sequence": 1,
            "payload": {
                "kind": "set_group",
                "group_id": str(group_id),
                "page_ids": [str(PAGE_ID)],
            },
        },
        {
            "operation_id": "40000000-0000-4000-8000-000000000813",
            "operation_version": 1,
            "sequence": 2,
            "payload": {
                "kind": "set_group",
                "group_id": str(group_id),
                "page_ids": [str(PAGE_ID)],
            },
        },
    ]
    snapshot = ManifestSnapshot(
        CASE_ID,
        1,
        7,
        document,
        hashlib.sha256(repr(document).encode("utf-8")).hexdigest(),
        "export_service_manifest_cas_0004",
    )
    stack.manifests.result = ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot)

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert stack.destination.calls == 1


def test_durable_orphan_history_id_is_retried_before_destination_commit() -> None:
    stack = _stack()
    stack.history.identifiers = frozenset({EXPORT_ID})
    identifiers = iter((EXPORT_ID, NEXT_EXPORT_ID))
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        stack.destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: next(identifiers),
    )

    outcome = _export(stack)

    assert outcome.export_id == NEXT_EXPORT_ID
    assert stack.history.record is not None
    assert stack.history.record.export_id == NEXT_EXPORT_ID
    assert stack.events.index("history.ids") < stack.events.index("source.borrow")


@pytest.mark.parametrize("phase", ("prepared", "committed"))
def test_retry_journal_id_is_reserved_before_destination_commit(phase: str) -> None:
    stack = _stack()
    receipt = _retry_receipt()
    stack.retries.inventory_result = PdfExportRetryInventory(
        prepared=(receipt,) if phase == "prepared" else (),
        committed=(receipt,) if phase == "committed" else (),
    )
    identifiers = iter((EXPORT_ID, NEXT_EXPORT_ID))
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        stack.destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: next(identifiers),
    )

    outcome = _export(stack)

    assert outcome.export_id == NEXT_EXPORT_ID
    assert stack.history.record is not None
    assert stack.history.record.export_id == NEXT_EXPORT_ID


def test_invalid_retry_journal_blocks_new_export_before_worker() -> None:
    stack = _stack()
    stack.retries.inventory_result = PdfExportRetryInventory(
        invalid_export_ids=(EXPORT_ID,),
        invalid_entries=1,
    )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED
    assert stack.sources.calls == 0
    assert stack.destination.calls == 0
    assert stack.history.calls == 0


@pytest.mark.parametrize(
    "result",
    (
        PdfExportHistoryError(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.test",
        ),
        RuntimeError("private retry inventory detail"),
        object(),
    ),
)
def test_retry_inventory_failure_is_sanitized_before_worker(result: object) -> None:
    stack = _stack()
    stack.retries.inventory_result = result

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED
    assert "private retry" not in str(raised.value)
    assert stack.sources.calls == 0
    assert stack.destination.calls == 0


@pytest.mark.parametrize(
    "result",
    (
        PdfExportHistoryError(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_history.test",
        ),
        RuntimeError("private history ID detail"),
        {EXPORT_ID},
    ),
)
def test_history_id_query_failure_is_sanitized_before_worker(
    result: object,
) -> None:
    stack = _stack()
    stack.history.identifiers_result = result

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert stack.sources.calls == 0
    assert stack.destination.calls == 0


def test_exhausted_existing_export_id_fails_before_worker_or_destination() -> None:
    stack = _stack()
    document = _manifest_document()
    history = cast(dict[str, object], document["export_history"])
    cast(list[object], history["entries"]).append(
        {
            "export_id": str(EXPORT_ID),
            "record_version": 1,
            "sha256": "f" * 64,
        }
    )
    snapshot = ManifestSnapshot(
        CASE_ID,
        1,
        7,
        document,
        hashlib.sha256(repr(document).encode("utf-8")).hexdigest(),
        "export_service_manifest_cas_0003",
    )
    stack.manifests.result = ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot)
    stack.service = PdfExportService(
        stack.manifests,
        stack.sources,
        stack.broker,
        stack.destination,
        stack.history,
        stack.retries,
        clock=stack.clock,
        id_factory=lambda: EXPORT_ID,
    )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert stack.sources.calls == 0
    assert stack.destination.calls == 0
    assert stack.history.calls == 0
