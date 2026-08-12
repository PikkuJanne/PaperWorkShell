from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionEnvelope,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    encode_pdf_inspection_frame,
)
from paperworkshell.domain.source_ingest import (
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceSignatureState,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_source_inspector import (
    BrokeredPdfSourceInspector,
    PdfInspectionBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    PDF_INSPECT_CAPABILITY,
    PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
    CancelReason,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-{value:012x}")


def _source(*, byte_size: int = 100) -> PreparedSourceSummary:
    return PreparedSourceSummary(
        pending_id=_uuid(1),
        case_id=_uuid(2),
        source_id=_uuid(3),
        media_type=DetectedMediaType.PDF,
        byte_size=byte_size,
        sha256="a" * 64,
    )


def _limits(*, temporary_storage_bytes: int = 1_000_000) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=10_000,
        cpu_time_ms=5_000,
        memory_bytes=128 * 1024 * 1024,
        temporary_storage_bytes=temporary_storage_bytes,
        output_bytes=8_192,
        page_count=100,
        pixel_count=1,
    )


def _opened_result(*, signed: bool = True) -> PdfInspectionResult:
    absent = PdfFeatureState.NOT_DETECTED
    return PdfInspectionResult(
        open_state=PdfOpenState.OPENED,
        page_count=2,
        object_count=9,
        encryption_state=PdfEncryptionState.UNENCRYPTED,
        structural_state=PdfStructuralState.CLEAN,
        features=PdfFeatureSummary(
            signature_markers=PdfFeatureState.DETECTED if signed else absent,
            attachments=absent,
            interactive_content=absent,
            actions=absent,
            scripts=absent,
            xfa=absent,
        ),
    )


def _encrypted_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.ENCRYPTED,
        page_count=None,
        object_count=None,
        encryption_state=PdfEncryptionState.ENCRYPTED,
        structural_state=PdfStructuralState.FAILED,
        features=PdfFeatureSummary.unknown(),
    )


@dataclass
class _Lease:
    source: PreparedSourceSummary
    events: list[str]
    worker: _Worker | None = None

    @property
    def summary(self) -> PreparedSourceSummary:
        return self.source

    @property
    def handle_value(self) -> int:
        self.events.append("lease.handle")
        return 501

    def __enter__(self) -> Self:
        self.events.append("lease.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        assert self.worker is not None
        assert self.worker.terminated
        self.events.append("lease.exit")


@dataclass
class _Broker:
    source: PreparedSourceSummary
    events: list[str]
    lease: _Lease

    def borrow_prepared_source(self, source: PreparedSourceSummary) -> _Lease:
        assert source == self.source
        self.events.append("broker.borrow")
        return self.lease


class _Pipe:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.frame = b""
        self.worker: _Worker | None = None
        self.writer_closed = False
        self.read_called = False

    @property
    def write_handle_value(self) -> int:
        self.events.append("pipe.handle")
        return 503

    def close_local_writer(self) -> None:
        self.writer_closed = True
        self.events.append("pipe.writer_closed")

    def read_complete_frame(self) -> bytes:
        assert self.worker is not None
        assert self.worker.terminated
        assert self.writer_closed
        self.read_called = True
        self.events.append("pipe.read")
        return self.frame

    def __enter__(self) -> Self:
        self.events.append("pipe.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        assert self.worker is not None
        assert self.worker.terminated
        self.events.append("pipe.exit")


class _Worker:
    def __init__(
        self,
        events: list[str],
        pipe: _Pipe,
        result: PdfInspectionResult,
        *,
        terminal_outcome: OperationOutcome = OperationOutcome.SUCCEEDED,
        corrupt_frame: bool = False,
        mismatched_source: bool = False,
    ) -> None:
        self.events = events
        self.pipe = pipe
        self.result = result
        self.terminal_outcome = terminal_outcome
        self.corrupt_frame = corrupt_frame
        self.mismatched_source = mismatched_source
        self.phase = WorkerLifecyclePhase.STOPPED
        self.operation_id: UUID | None = None
        self.payload: OperationStartPayload | None = None
        self.terminated = False
        self.cancel_reason: CancelReason | None = None
        self.last_result: WorkerOperationResultSnapshot | None = None
        self.duplicates: list[tuple[int, BrokeredHandlePurpose]] = []
        pipe.worker = self

    def _snapshot(self) -> WorkerClientSnapshot:
        live = self.phase not in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}
        return WorkerClientSnapshot(
            generation=1,
            phase=self.phase,
            case_id=_uuid(2) if live else None,
            session_id=_uuid(10) if live else None,
            worker_instance_id=_uuid(11) if live else None,
            pid=123 if live else None,
            capabilities=(PDF_INSPECT_CAPABILITY,)
            if self.phase is not WorkerLifecyclePhase.STARTING
            else (),
            active_operation=None,
            last_result=self.last_result,
            last_failure=None,
        )

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(2)
        self.events.append("worker.start")
        self.phase = WorkerLifecyclePhase.STARTING
        return 1

    def query(self) -> WorkerClientSnapshot:
        return self._snapshot()

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.events.append("worker.pump")
        if self.phase is WorkerLifecyclePhase.STARTING:
            self.phase = WorkerLifecyclePhase.READY
        elif self.phase in {WorkerLifecyclePhase.BUSY, WorkerLifecyclePhase.CANCELLING}:
            assert self.operation_id is not None
            outcome = (
                OperationOutcome.CANCELLED
                if self.cancel_reason is not None
                else self.terminal_outcome
            )
            self.last_result = WorkerOperationResultSnapshot(
                generation=1,
                operation_id=self.operation_id,
                outcome=outcome,
                outputs=(),
                error_code=None,
            )
            if outcome is OperationOutcome.SUCCEEDED:
                assert self.payload is not None
                source_id = self.payload.inputs[0].object_id
                if self.mismatched_source:
                    source_id = _uuid(99)
                self.pipe.frame = (
                    b"not-a-report"
                    if self.corrupt_frame
                    else encode_pdf_inspection_frame(
                        PdfInspectionEnvelope(self.operation_id, source_id, self.result)
                    )
                )
            self.phase = WorkerLifecyclePhase.READY
        return self._snapshot()

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        self.duplicates.append((source_handle, purpose))
        self.events.append(f"worker.duplicate.{purpose.value}")
        return 701 if purpose is BrokeredHandlePurpose.READ_SOURCE else 703

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        self.events.append("worker.operation")
        self.payload = payload
        self.operation_id = operation_id
        self.phase = WorkerLifecyclePhase.BUSY
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.events.append(f"worker.cancel.{reason.value}")
        self.cancel_reason = reason
        self.phase = WorkerLifecyclePhase.CANCELLING
        return _uuid(30)

    def terminate(self) -> None:
        self.events.append("worker.terminate")
        self.terminated = True
        self.phase = WorkerLifecyclePhase.STOPPED


def _stack(
    *,
    result: PdfInspectionResult | None = None,
    terminal_outcome: OperationOutcome = OperationOutcome.SUCCEEDED,
    corrupt_frame: bool = False,
    mismatched_source: bool = False,
    limits: ResourceLimits | None = None,
) -> tuple[BrokeredPdfSourceInspector, _Worker, _Pipe, _Lease, list[str]]:
    events: list[str] = []
    source = _source()
    pipe = _Pipe(events)
    worker = _Worker(
        events,
        pipe,
        _opened_result() if result is None else result,
        terminal_outcome=terminal_outcome,
        corrupt_frame=corrupt_frame,
        mismatched_source=mismatched_source,
    )
    lease = _Lease(source, events, worker)
    broker = _Broker(source, events, lease)
    inspector = BrokeredPdfSourceInspector(
        worker,
        broker,
        PdfInspectionBrokerPolicy(_limits() if limits is None else limits),
        heavy_operations=HeavyOperationCoordinator(1),
        pipe_factory=lambda: pipe,
        id_factory=lambda: _uuid(20),
    )
    return inspector, worker, pipe, lease, events


def test_success_binds_exact_summary_handles_report_and_termination_order() -> None:
    inspector, worker, pipe, _lease, events = _stack()
    progress: list[SourceImportProgress] = []

    completed = inspector.inspect(
        _source(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert completed.page_count == 2
    assert completed.signature_state is SourceSignatureState.MARKERS_DETECTED
    assert completed.encryption_state is SourceEncryptionState.UNENCRYPTED
    assert [item.completed_units for item in progress] == [0, 1]
    assert worker.duplicates == [
        (501, BrokeredHandlePurpose.READ_SOURCE),
        (503, BrokeredHandlePurpose.WRITE_RESULT_PIPE),
    ]
    assert worker.payload is not None
    assert worker.payload.inputs[0].area is ReferenceArea.SOURCE_READ
    assert worker.payload.inputs[0].object_id == _uuid(1)
    assert worker.payload.inputs[0].expected_byte_size == 100
    assert worker.payload.inputs[0].expected_sha256 == "a" * 64
    parameters = {value.name: value.value for value in worker.payload.parameters}
    assert parameters == {
        PDF_INSPECT_SOURCE_HANDLE_PARAMETER: 701,
        PDF_INSPECT_RESULT_HANDLE_PARAMETER: 703,
    }
    assert events.index("worker.terminate") < events.index("pipe.read")
    assert events.index("worker.terminate") < events.index("lease.exit")
    assert events.index("worker.terminate") < events.index("pipe.exit")
    assert pipe.read_called


def test_encrypted_result_is_distinct_and_rejected_before_source_commit() -> None:
    inspector, worker, pipe, _lease, _events = _stack(result=_encrypted_result())

    with pytest.raises(SourceIngestError) as raised:
        inspector.inspect(
            _source(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value.code is SourceIngestErrorCode.INSPECTION_INVALID
    assert raised.value.context == "inspection.pdf_encrypted"
    assert worker.terminated
    assert pipe.read_called


@pytest.mark.parametrize("mismatched_source", [False, True])
def test_invalid_or_mismatched_report_fails_closed(mismatched_source: bool) -> None:
    inspector, worker, _pipe, _lease, _events = _stack(
        corrupt_frame=not mismatched_source,
        mismatched_source=mismatched_source,
    )

    with pytest.raises(PdfInspectionError) as raised:
        inspector.inspect_structural(
            _source(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is PdfInspectionErrorCode.REPORT_INVALID
    assert worker.terminated


def test_worker_failure_never_attempts_to_trust_an_empty_result_pipe() -> None:
    inspector, worker, pipe, _lease, _events = _stack(terminal_outcome=OperationOutcome.FAILED)

    with pytest.raises(PdfInspectionError) as raised:
        inspector.inspect_structural(
            _source(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is PdfInspectionErrorCode.WORKER_FAILED
    assert worker.terminated
    assert not pipe.read_called


def test_cancellation_is_sent_then_worker_is_terminated_without_reading_report() -> None:
    inspector, worker, pipe, _lease, events = _stack()
    probes = iter((False, False, False, True, True))

    with pytest.raises(PdfInspectionError) as raised:
        inspector.inspect_structural(
            _source(),
            deadline=lambda: False,
            cancellation=lambda: next(probes, True),
        )

    assert raised.value.code is PdfInspectionErrorCode.CANCELLED
    assert "worker.cancel.user_requested" in events
    assert worker.terminated
    assert not pipe.read_called


def test_external_deadline_requests_shutdown_cancellation_and_terminates() -> None:
    inspector, worker, pipe, _lease, events = _stack()
    probes = iter((False, False, False, True))

    with pytest.raises(PdfInspectionError) as raised:
        inspector.inspect_structural(
            _source(),
            deadline=lambda: next(probes, True),
            cancellation=lambda: False,
        )

    assert raised.value.code is PdfInspectionErrorCode.TIMED_OUT
    assert "worker.cancel.shutdown" in events
    assert worker.terminated
    assert not pipe.read_called


def test_input_limit_rejects_before_any_authority_or_worker_is_created() -> None:
    inspector, worker, pipe, _lease, events = _stack(limits=_limits(temporary_storage_bytes=99))

    with pytest.raises(PdfInspectionError) as raised:
        inspector.inspect_structural(
            _source(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert not worker.terminated
    assert not pipe.read_called
    assert events == []


def test_policy_requires_enough_output_for_one_bounded_correlated_frame() -> None:
    limits = ResourceLimits(1, 1, 1, 1, 4_096, 1, 1)

    with pytest.raises(PdfInspectionError) as raised:
        PdfInspectionBrokerPolicy(limits)

    assert raised.value.code is PdfInspectionErrorCode.MODEL_INVALID
