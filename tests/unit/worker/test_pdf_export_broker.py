"""Unit tests for the trusted, handle-only PDF export broker."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
    WorkerOperationSnapshot,
    WorkerProgressSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.pdf_export_broker import (
    MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
    PDF_EXPORT_VERIFICATION_PIXELS,
    BrokeredPdfExportCandidate,
    BrokeredPdfExporter,
    CancellationProbe,
    DeadlineProbe,
    PdfExportBrokerPolicy,
    PdfExportSourceBatchLease,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_export_plan import decode_pdf_export_worker_plan
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    encode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.models import (
    PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    CancelReason,
    CapabilityDescriptor,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    validate_pdf_export_start,
)


def _uuid(value: int) -> UUID:
    return UUID(f"92000000-0000-4000-8000-{value:012x}")


def _plan() -> PdfExportPlan:
    first = PdfExportSource(
        source_id=_uuid(10),
        byte_size=100,
        page_count=2,
        sha256="a" * 64,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
    )
    second = PdfExportSource(
        source_id=_uuid(11),
        byte_size=200,
        page_count=1,
        sha256="b" * 64,
        signature_state=PdfExportSourceSignatureState.MARKERS_DETECTED,
    )
    return PdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=7,
        manifest_sha256="c" * 64,
        manifest_cas_token="synthetic_export_broker_cas_001",
        sources=(first, second),
        pages=(
            PdfExportPage(0, _uuid(20), first.source_id, 1, PdfExportRotation.DEGREES_270),
            PdfExportPage(1, _uuid(21), second.source_id, 0, PdfExportRotation.DEGREES_90),
        ),
    )


def _policy(
    *,
    page_count: int = 10,
    temporary_storage_bytes: int = 4 * 1024 * 1024,
    output_bytes: int = 2 * 1024 * 1024,
) -> PdfExportBrokerPolicy:
    return PdfExportBrokerPolicy(
        ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=8 * 1024 * 1024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=output_bytes,
            page_count=page_count,
            pixel_count=PDF_EXPORT_VERIFICATION_PIXELS,
        )
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


class _SourceBatch:
    def __init__(self, plan: PdfExportPlan, events: list[str]) -> None:
        self.events = events
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
        self.worker: _Worker | None = None
        self.revalidate_calls = 0
        self.revalidate_error: SourceIngestError | None = None
        self.after_revalidate: Callable[[], None] | None = None

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        del deadline, cancellation
        self.events.append("source.revalidate")
        self.revalidate_calls += 1
        assert self.worker is not None and self.worker.terminated
        if self.revalidate_error is not None:
            raise self.revalidate_error
        if self.after_revalidate is not None:
            self.after_revalidate()

    def __enter__(self) -> Self:
        self.events.append("source.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.events.append("source.exit")


class _OutputBuffer:
    def __init__(self, payload: bytes, capacity: int, events: list[str]) -> None:
        self.payload = payload
        self.capacity = capacity
        self.write_handle_value = 202
        self.events = events
        self.iter_calls: list[tuple[int, int]] = []
        self.close_calls = 0
        self.fail_iter_call: int | None = None
        self.short_iter_call: int | None = None
        self.invalid_chunk_call: int | None = None

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]:
        call = len(self.iter_calls) + 1
        self.iter_calls.append((byte_count, chunk_bytes))
        self.events.append(f"output.iter.{call}")
        payload = self.payload[:byte_count]

        def chunks() -> Iterator[bytes]:
            if self.fail_iter_call == call:
                raise RuntimeError("private output read failure")
            if self.invalid_chunk_call == call:
                yield b""
                return
            selected = payload[:-1] if self.short_iter_call == call else payload
            offsets = (4, 3, len(selected))
            position = 0
            for size in offsets:
                if position >= len(selected):
                    break
                end = min(len(selected), position + size)
                yield selected[position:end]
                position = end

        return chunks()

    def close(self) -> None:
        self.close_calls += 1
        self.events.append("output.close")


class _PlanBuffer:
    def __init__(self, payload: bytes, events: list[str]) -> None:
        self.payload = payload
        self.payload_size = len(payload)
        self.read_handle_value = 201
        self.events = events
        self.entered = False
        self.exited = False

    def __enter__(self) -> Self:
        self.entered = True
        self.events.append("plan.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.exited = True
        self.events.append("plan.exit")


class _ResultPipe:
    def __init__(
        self,
        plan: PdfExportPlan,
        output: _OutputBuffer,
        events: list[str],
    ) -> None:
        self.plan = plan
        self.output = output
        self.events = events
        self.worker: _Worker | None = None
        self.write_handle_value = 203
        self.writer_closed = False
        self.entered = False
        self.exited = False
        self.read_calls = 0
        self.raw_frame: bytes | None = None
        self.operation_id_override: UUID | None = None
        self.output_object_id_override: UUID | None = None
        self.report_digest_override: str | None = None
        self.report_size_override: int | None = None
        self.semantic_fingerprint_override: str | None = None

    def __enter__(self) -> Self:
        self.entered = True
        self.events.append("pipe.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.exited = True
        self.events.append("pipe.exit")

    def close_local_writer(self) -> None:
        self.writer_closed = True
        self.events.append("pipe.close_writer")

    def read_complete_frame(self) -> bytes:
        self.events.append("pipe.read")
        self.read_calls += 1
        assert self.worker is not None and self.worker.terminated
        if self.raw_frame is not None:
            return self.raw_frame
        operation_id = (
            self.worker.operation_id
            if self.operation_id_override is None
            else self.operation_id_override
        )
        output_object_id = (
            self.worker.output_object_id
            if self.output_object_id_override is None
            else self.output_object_id_override
        )
        assert operation_id is not None and output_object_id is not None
        report = PdfExportVerificationReport.for_verified_plan(
            self.plan,
            output_sha256=(
                hashlib.sha256(self.output.payload).hexdigest()
                if self.report_digest_override is None
                else self.report_digest_override
            ),
            output_byte_size=(
                len(self.output.payload)
                if self.report_size_override is None
                else self.report_size_override
            ),
        )
        if self.semantic_fingerprint_override is not None:
            report = replace(
                report,
                semantic_fingerprint=self.semantic_fingerprint_override,
            )
        return encode_pdf_export_worker_report(
            PdfExportWorkerReport(operation_id, output_object_id, report)
        )


def _snapshot(
    *,
    generation: int,
    phase: WorkerLifecyclePhase,
    case_id: UUID,
    capabilities: tuple[CapabilityDescriptor, ...],
    active: WorkerOperationSnapshot | None = None,
    result: WorkerOperationResultSnapshot | None = None,
) -> WorkerClientSnapshot:
    return WorkerClientSnapshot(
        generation=generation,
        phase=phase,
        case_id=case_id,
        session_id=_uuid(70),
        worker_instance_id=_uuid(71),
        pid=321,
        capabilities=capabilities,
        active_operation=active,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(self, plan: PdfExportPlan, output: _OutputBuffer, events: list[str]) -> None:
        self.plan = plan
        self.output = output
        self.events = events
        self.generation = 1
        self.started = False
        self.terminated = False
        self.ready_queries = 0
        self.result_queries = 0
        self.payload: OperationStartPayload | None = None
        self.operation_id: UUID | None = None
        self.output_object_id: UUID | None = None
        self.terminal_result: WorkerOperationResultSnapshot | None = None
        self.duplicate_calls: list[tuple[int, BrokeredHandlePurpose]] = []
        self.cancel_calls: list[CancelReason] = []
        self.outcome = OperationOutcome.SUCCEEDED
        self.withhold_result = False
        self.fault_after_start = False
        self.cancel_returns_success = False
        self.cancel_return_override: UUID | None = None
        self.start_return_override: UUID | None = None
        self.termination_error: WorkerClientError | None = None
        self.capabilities = (PDF_EXPORT_CAPABILITY,)
        self.case_id_override: UUID | None = None
        self.duplicate_collision_call: int | None = None
        self.output_area = ReferenceArea.OPERATION_STAGING
        self.output_object_override: UUID | None = None
        self.output_operation_override: UUID | None = None
        self.output_content_kind = PDF_EXPORT_OUTPUT_CONTENT_KIND
        self.output_size_override: int | None = None
        self.output_digest_override: str | None = None
        self.omit_output_digest = False
        self.result_operation_override: UUID | None = None
        self.result_generation_override: int | None = None
        self.progress_completed = 1
        self.progress_total: int | None = self.plan.page_count
        self.progress_unit = ProgressUnit.PAGES

    def start(self, case_id: UUID) -> int:
        assert case_id == self.plan.case_id
        self.events.append("worker.start")
        self.started = True
        return self.generation

    def query(self) -> WorkerClientSnapshot:
        self.events.append("worker.query")
        selected_case = (
            self.plan.case_id if self.case_id_override is None else self.case_id_override
        )
        if self.payload is None:
            self.ready_queries += 1
            return _snapshot(
                generation=self.generation,
                phase=(
                    WorkerLifecyclePhase.STARTING
                    if self.ready_queries == 1
                    else WorkerLifecyclePhase.READY
                ),
                case_id=selected_case,
                capabilities=self.capabilities,
            )
        self.result_queries += 1
        if self.fault_after_start:
            return _snapshot(
                generation=self.generation,
                phase=WorkerLifecyclePhase.FAULTED,
                case_id=selected_case,
                capabilities=self.capabilities,
            )
        available = (
            self.terminal_result if not self.withhold_result and self.result_queries >= 2 else None
        )
        active: WorkerOperationSnapshot | None = None
        if available is None:
            assert self.operation_id is not None
            active = WorkerOperationSnapshot(
                self.operation_id,
                _uuid(72),
                PDF_EXPORT_CAPABILITY.capability_id,
                PDF_EXPORT_CAPABILITY.capability_version,
                True,
                bool(self.cancel_calls),
                WorkerProgressSnapshot(
                    ProgressStage.PROCESSING,
                    self.progress_completed,
                    self.progress_total,
                    self.progress_unit,
                ),
            )
        return _snapshot(
            generation=self.generation,
            phase=WorkerLifecyclePhase.BUSY,
            case_id=selected_case,
            capabilities=self.capabilities,
            active=active,
            result=available,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.events.append("worker.pump")
        return _snapshot(
            generation=self.generation,
            phase=(
                WorkerLifecyclePhase.STARTING if self.payload is None else WorkerLifecyclePhase.BUSY
            ),
            case_id=self.plan.case_id,
            capabilities=self.capabilities,
        )

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        self.duplicate_calls.append((source_handle, purpose))
        self.events.append(f"worker.duplicate.{purpose.value}")
        if self.duplicate_collision_call == len(self.duplicate_calls):
            return 1_001
        return 1_001 + len(self.duplicate_calls) - 1

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
        parameters = {parameter.name: parameter for parameter in payload.parameters}
        output_value = parameters[PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER]
        assert output_value.value_type is ScalarValueType.UUID
        self.output_object_id = cast(UUID, output_value.value)
        output = CaseObjectReference(
            area=self.output_area,
            object_id=(
                self.output_object_id
                if self.output_object_override is None
                else self.output_object_override
            ),
            operation_id=(
                operation_id
                if self.output_operation_override is None
                else self.output_operation_override
            ),
            content_kind=self.output_content_kind,
            expected_byte_size=(
                len(self.output.payload)
                if self.output_size_override is None
                else self.output_size_override
            ),
            expected_sha256=(
                None
                if self.omit_output_digest
                else (
                    hashlib.sha256(self.output.payload).hexdigest()
                    if self.output_digest_override is None
                    else self.output_digest_override
                )
            ),
        )
        result_operation = (
            operation_id
            if self.result_operation_override is None
            else self.result_operation_override
        )
        self.terminal_result = WorkerOperationResultSnapshot(
            self.generation
            if self.result_generation_override is None
            else self.result_generation_override,
            result_operation,
            self.outcome,
            (output,) if self.outcome is OperationOutcome.SUCCEEDED else (),
            None,
        )
        return operation_id if self.start_return_override is None else self.start_return_override

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.events.append(f"worker.cancel.{reason.value}")
        self.cancel_calls.append(reason)
        assert self.operation_id is not None
        if self.cancel_returns_success:
            assert self.terminal_result is not None
            self.withhold_result = False
            self.result_queries = 2
        else:
            self.terminal_result = WorkerOperationResultSnapshot(
                self.generation,
                self.operation_id,
                OperationOutcome.CANCELLED,
                (),
                None,
            )
            self.withhold_result = False
            self.result_queries = 2
        return (
            self.operation_id
            if self.cancel_return_override is None
            else self.cancel_return_override
        )

    def terminate(self) -> None:
        self.events.append("worker.terminate")
        self.terminated = True
        if self.termination_error is not None:
            raise self.termination_error


class _System:
    def __init__(
        self,
        *,
        policy: PdfExportBrokerPolicy | None = None,
        payload: bytes = b"%PDF-1.7\nsynthetic broker output\n%%EOF\n",
    ) -> None:
        self.events: list[str] = []
        self.plan = _plan()
        self.policy = _policy() if policy is None else policy
        self.output = _OutputBuffer(payload, self.policy.limits.output_bytes, self.events)
        self.worker = _Worker(self.plan, self.output, self.events)
        self.sources = _SourceBatch(self.plan, self.events)
        self.sources.worker = self.worker
        self.pipe = _ResultPipe(self.plan, self.output, self.events)
        self.pipe.worker = self.worker
        self.plan_buffer: _PlanBuffer | None = None
        self.plan_payload_size_delta = 0
        self.output_factory_calls = 0
        self.pipe_factory_calls = 0
        identifiers = iter((_uuid(90), _uuid(91), _uuid(92)))

        def output_factory(capacity: int) -> _OutputBuffer:
            self.events.append("output.create")
            self.output_factory_calls += 1
            assert capacity == self.policy.limits.output_bytes
            return self.output

        def plan_factory(payload_bytes: bytes) -> _PlanBuffer:
            self.events.append("plan.create")
            self.plan_buffer = _PlanBuffer(payload_bytes, self.events)
            self.plan_buffer.payload_size += self.plan_payload_size_delta
            return self.plan_buffer

        def pipe_factory() -> _ResultPipe:
            self.events.append("pipe.create")
            self.pipe_factory_calls += 1
            return self.pipe

        self.exporter = BrokeredPdfExporter(
            cast(PdfExportWorkerCoordinator, self.worker),
            self.policy,
            pipe_factory=pipe_factory,
            plan_buffer_factory=plan_factory,
            output_buffer_factory=output_factory,
            id_factory=lambda: next(identifiers),
        )

    def export(
        self,
        *,
        deadline: DeadlineProbe = lambda: False,
        cancellation: CancellationProbe = lambda: False,
        progress: Callable[[PdfExportProgress], None] = lambda _progress: None,
    ) -> BrokeredPdfExportCandidate:
        return self.exporter.export(
            self.plan,
            cast(PdfExportSourceBatchLease, self.sources),
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )


def _error(
    system: _System,
    *,
    deadline: DeadlineProbe = lambda: False,
    cancellation: CancellationProbe = lambda: False,
    progress: Callable[[PdfExportProgress], None] = lambda _progress: None,
) -> PdfExportError:
    with pytest.raises(PdfExportError) as raised:
        system.export(
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )
    return raised.value


def test_permanent_revocation_fails_before_allocating_or_launching() -> None:
    system = _System()

    system.exporter._revoke_permanently()
    error = _error(system)

    assert system.exporter.revoked
    assert error.code is PdfExportErrorCode.WORKER_FAILED
    assert system.events == []
    assert system.output_factory_calls == 0
    assert system.pipe_factory_calls == 0


def test_success_terminates_revalidates_authenticates_then_streams_candidate() -> None:
    system = _System()
    progress: list[PdfExportProgress] = []

    candidate = system.export(progress=progress.append)

    assert candidate.output_object_id == _uuid(92)
    assert candidate.report.output_sha256 == hashlib.sha256(system.output.payload).hexdigest()
    assert candidate.report.unsigned_derivative
    assert progress == [
        PdfExportProgress(
            PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
            1,
            system.plan.page_count,
        )
    ]
    assert system.events.index("worker.terminate") < system.events.index("source.revalidate")
    assert system.events.index("source.revalidate") < system.events.index("pipe.read")
    assert system.events.index("pipe.read") < system.events.index("output.iter.1")
    assert system.output.close_calls == 0

    with candidate:
        first = candidate.read(3)
        second = candidate.read(5)
        remainder = candidate.read(MAX_PDF_EXPORT_CANDIDATE_READ_BYTES)
        assert first + second + remainder == system.output.payload
        assert candidate.read(1) == b""

    assert system.output.iter_calls == [
        (len(system.output.payload), MAX_PDF_EXPORT_CANDIDATE_READ_BYTES),
        (len(system.output.payload), MAX_PDF_EXPORT_CANDIDATE_READ_BYTES),
    ]
    assert system.output.close_calls == 1
    candidate.close()
    assert system.output.close_calls == 1
    with pytest.raises(PdfExportError) as closed:
        candidate.read(1)
    assert closed.value.code is PdfExportErrorCode.INTERNAL_ERROR


def test_payload_and_handle_purposes_are_exact_path_free_and_ordered() -> None:
    system = _System()

    candidate = system.export()
    candidate.close()

    assert system.worker.duplicate_calls == [
        (101, BrokeredHandlePurpose.READ_SOURCE),
        (102, BrokeredHandlePurpose.READ_SOURCE),
        (201, BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION),
        (202, BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION),
        (203, BrokeredHandlePurpose.WRITE_RESULT_PIPE),
    ]
    payload = system.worker.payload
    plan_buffer = system.plan_buffer
    assert payload is not None and plan_buffer is not None
    validate_pdf_export_start(payload)
    assert payload.operation_kind == PDF_EXPORT_CAPABILITY.capability_id
    assert payload.operation_version == PDF_EXPORT_CAPABILITY.capability_version
    assert payload.inputs[0] == CaseObjectReference(
        ReferenceArea.OPERATION_TEMP,
        _uuid(91),
        _uuid(90),
        PDF_EXPORT_PLAN_CONTENT_KIND,
        len(plan_buffer.payload),
        hashlib.sha256(plan_buffer.payload).hexdigest(),
    )
    assert tuple(reference.object_id for reference in payload.inputs[1:]) == (
        _uuid(10),
        _uuid(11),
    )
    assert tuple(reference.expected_byte_size for reference in payload.inputs[1:]) == (100, 200)
    assert tuple(reference.expected_sha256 for reference in payload.inputs[1:]) == (
        "a" * 64,
        "b" * 64,
    )
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    assert parameters[PDF_EXPORT_PLAN_HANDLE_PARAMETER].value == 1_003
    assert parameters[PDF_EXPORT_OUTPUT_HANDLE_PARAMETER].value == 1_004
    assert parameters[PDF_EXPORT_RESULT_HANDLE_PARAMETER].value == 1_005
    assert parameters[PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER].value == _uuid(92)
    assert payload.requested_limits.page_count == system.plan.page_count
    assert payload.requested_limits.pixel_count == PDF_EXPORT_VERIFICATION_PIXELS
    assert payload.requested_limits.output_bytes == system.policy.limits.output_bytes
    decoded = decode_pdf_export_worker_plan(plan_buffer.payload)
    assert decoded.plan == system.plan
    assert decoded.handle_for(_uuid(10)) == 1_001
    assert decoded.handle_for(_uuid(11)) == 1_002
    assert b"path" not in plan_buffer.payload.lower()
    assert b"filename" not in plan_buffer.payload.lower()
    assert system.events.index("pipe.close_writer") < system.events.index("worker.operation")
    assert system.events.index("plan.exit") < system.events.index("worker.terminate")


def test_termination_failure_prevents_source_report_and_output_reads() -> None:
    system = _System()
    system.worker.termination_error = WorkerClientError(
        WorkerClientErrorCode.TERMINATION_FAILED,
        WorkerClientContext.PROCESS,
    )

    error = _error(system)

    assert error.code is PdfExportErrorCode.WORKER_FAILED
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1
    assert system.pipe.exited
    assert system.plan_buffer is not None and system.plan_buffer.exited


def test_cancellation_requests_worker_cancel_then_terminates_and_cleans() -> None:
    system = _System()
    system.worker.withhold_result = True

    error = _error(system, cancellation=lambda: system.worker.payload is not None)

    assert error.code is PdfExportErrorCode.CANCELLED
    assert system.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


def test_success_reported_after_cancellation_is_never_exposed() -> None:
    system = _System()
    system.worker.withhold_result = True
    system.worker.cancel_returns_success = True

    error = _error(system, cancellation=lambda: system.worker.payload is not None)

    assert error.code is PdfExportErrorCode.CANCELLED
    assert system.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


def test_cancel_acknowledgement_must_bind_to_the_exact_operation() -> None:
    system = _System()
    system.worker.withhold_result = True
    system.worker.cancel_return_override = _uuid(98)

    error = _error(system, cancellation=lambda: system.worker.payload is not None)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.close_calls == 1


def test_deadline_requests_shutdown_cancel_then_terminates_and_cleans() -> None:
    system = _System()
    system.worker.withhold_result = True

    error = _error(system, deadline=lambda: system.worker.payload is not None)

    assert error.code is PdfExportErrorCode.TIMED_OUT
    assert system.worker.cancel_calls == [CancelReason.SHUTDOWN]
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.close_calls == 1


def test_worker_fault_terminates_and_never_exposes_volatile_output() -> None:
    system = _System()
    system.worker.fault_after_start = True

    error = _error(system)

    assert error.code is PdfExportErrorCode.WORKER_FAILED
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize("mutation", ("unit", "total", "completed"))
def test_worker_progress_must_match_exact_export_page_contract(mutation: str) -> None:
    system = _System()
    if mutation == "unit":
        system.worker.progress_unit = ProgressUnit.ITEMS
    elif mutation == "total":
        system.worker.progress_total = system.plan.page_count + 1
    else:
        system.worker.progress_completed = system.plan.page_count + 1
        system.worker.progress_total = system.plan.page_count + 1

    error = _error(system)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize(
    ("outcome", "expected"),
    (
        (OperationOutcome.CANCELLED, PdfExportErrorCode.CANCELLED),
        (OperationOutcome.TIMED_OUT, PdfExportErrorCode.TIMED_OUT),
        (OperationOutcome.FAILED, PdfExportErrorCode.WORKER_FAILED),
    ),
)
def test_terminal_worker_outcomes_never_revalidate_or_read_output(
    outcome: OperationOutcome,
    expected: PdfExportErrorCode,
) -> None:
    system = _System()
    system.worker.outcome = outcome

    error = _error(system)

    assert error.code is expected
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize("mutation", ("start", "result_operation", "result_generation"))
def test_operation_and_generation_identity_mismatches_fail_before_output_read(
    mutation: str,
) -> None:
    system = _System()
    if mutation == "start":
        system.worker.start_return_override = _uuid(98)
    elif mutation == "result_operation":
        system.worker.result_operation_override = _uuid(98)
    else:
        system.worker.result_generation_override = 2

    error = _error(system)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize(
    "mutation",
    ("operation", "output_object", "semantic", "size", "reference_digest"),
)
def test_report_and_reference_correlation_fail_closed_after_revalidation(
    mutation: str,
) -> None:
    system = _System()
    if mutation == "operation":
        system.pipe.operation_id_override = _uuid(98)
    elif mutation == "output_object":
        system.pipe.output_object_id_override = _uuid(98)
    elif mutation == "semantic":
        system.pipe.semantic_fingerprint_override = "d" * 64
    elif mutation == "size":
        system.pipe.report_size_override = len(system.output.payload) + 1
    else:
        system.worker.output_digest_override = "e" * 64

    error = _error(system)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 1
    assert system.pipe.read_calls == 1
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize("mutation", ("object", "operation", "kind", "size", "digest"))
def test_worker_output_reference_must_match_before_source_revalidation(mutation: str) -> None:
    system = _System()
    if mutation == "object":
        system.worker.output_object_override = _uuid(98)
    elif mutation == "operation":
        system.worker.output_operation_override = _uuid(98)
    elif mutation == "kind":
        system.worker.output_content_kind = "pdf.untrusted"
    elif mutation == "size":
        system.worker.output_size_override = system.policy.limits.output_bytes + 1
    else:
        system.worker.omit_output_digest = True

    error = _error(system)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


@pytest.mark.parametrize("failure", ("digest", "short", "empty_chunk", "read_error"))
def test_output_authentication_rejects_digest_and_stream_failures(failure: str) -> None:
    system = _System()
    if failure == "digest":
        system.worker.output_digest_override = "0" * 64
        system.pipe.report_digest_override = "0" * 64
    elif failure == "short":
        system.output.short_iter_call = 1
    elif failure == "empty_chunk":
        system.output.invalid_chunk_call = 1
    else:
        system.output.fail_iter_call = 1

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 1
    assert system.pipe.read_calls == 1
    assert system.output.iter_calls == [
        (len(system.output.payload), MAX_PDF_EXPORT_CANDIDATE_READ_BYTES)
    ]
    assert system.output.close_calls == 1


def test_candidate_read_failure_still_closes_transferred_output_once() -> None:
    system = _System()
    system.output.short_iter_call = 2
    candidate = system.export()

    with pytest.raises(PdfExportError) as raised:
        with candidate:
            candidate.read(MAX_PDF_EXPORT_CANDIDATE_READ_BYTES)

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID
    assert system.output.close_calls == 1


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("manifest", PdfExportErrorCode.PLAN_STALE),
        ("source", PdfExportErrorCode.SOURCE_CHANGED),
        ("handles", PdfExportErrorCode.SOURCE_CHANGED),
    ),
)
def test_source_binding_failures_precede_output_allocation_and_worker_start(
    mutation: str,
    expected: PdfExportErrorCode,
) -> None:
    system = _System()
    if mutation == "manifest":
        system.sources.manifest_revision += 1
    elif mutation == "source":
        system.sources.sources[0].sha256 = "f" * 64
    else:
        system.sources.sources[1].handle_value = system.sources.sources[0].handle_value

    error = _error(system)

    assert error.code is expected
    assert system.output_factory_calls == 0
    assert not system.worker.started
    assert system.output.close_calls == 0


@pytest.mark.parametrize(
    "policy",
    (
        _policy(page_count=1),
        _policy(temporary_storage_bytes=450, output_bytes=200),
    ),
    ids=("page_count", "temporary_storage"),
)
def test_operation_limit_failures_precede_allocation_and_worker(
    policy: PdfExportBrokerPolicy,
) -> None:
    system = _System(policy=policy)

    error = _error(system)

    assert error.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert system.output_factory_calls == 0
    assert not system.worker.started
    assert system.output.close_calls == 0


@pytest.mark.parametrize(
    ("source_code", "expected"),
    (
        (SourceIngestErrorCode.SOURCE_CHANGED, PdfExportErrorCode.SOURCE_CHANGED),
        (SourceIngestErrorCode.CANCELLED, PdfExportErrorCode.CANCELLED),
        (SourceIngestErrorCode.TIMED_OUT, PdfExportErrorCode.TIMED_OUT),
        (SourceIngestErrorCode.CAPACITY_EXCEEDED, PdfExportErrorCode.LIMIT_EXCEEDED),
    ),
)
def test_post_termination_source_revalidation_errors_are_sanitized_and_cleanup(
    source_code: SourceIngestErrorCode,
    expected: PdfExportErrorCode,
) -> None:
    system = _System()
    system.sources.revalidate_error = SourceIngestError(source_code, "source.private_path")

    error = _error(system)

    assert error.code is expected
    assert "private" not in str(error)
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 1
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.output.close_calls == 1


def test_source_binding_is_checked_again_after_revalidation() -> None:
    system = _System()

    def mutate_source() -> None:
        system.sources.sources[0].byte_size += 1

    system.sources.after_revalidate = mutate_source

    error = _error(system)

    assert error.code is PdfExportErrorCode.SOURCE_CHANGED
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 1
    assert system.pipe.read_calls == 0
    assert system.output.close_calls == 1


@pytest.mark.parametrize("failure", ("output", "plan", "duplicate_handles"))
def test_invalid_local_authorities_terminate_when_needed_and_cleanup(failure: str) -> None:
    system = _System()
    if failure == "output":
        system.output.capacity -= 1
    elif failure == "duplicate_handles":
        system.worker.duplicate_collision_call = 3
    else:
        system.plan_payload_size_delta = 1

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.output.close_calls == 1
    if failure == "output":
        assert not system.worker.started
        assert system.pipe_factory_calls == 0
    else:
        assert system.worker.terminated
        assert system.pipe.exited
        assert system.plan_buffer is not None and system.plan_buffer.exited


def test_progress_and_probe_failures_are_sanitized_and_cleanup() -> None:
    progress_system = _System()

    def bad_progress(_progress: PdfExportProgress) -> None:
        raise KeyboardInterrupt("private progress")

    progress_error = _error(progress_system, progress=bad_progress)
    assert progress_error.code is PdfExportErrorCode.INTERNAL_ERROR
    assert "private" not in str(progress_error)
    assert progress_system.worker.terminated
    assert progress_system.output.close_calls == 1

    probe_system = _System()
    probe_error = _error(
        probe_system,
        deadline=cast(DeadlineProbe, lambda: "private deadline"),
    )
    assert probe_error.code is PdfExportErrorCode.INTERNAL_ERROR
    assert "private" not in str(probe_error)
    assert probe_system.output_factory_calls == 0
