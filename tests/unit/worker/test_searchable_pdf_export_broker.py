"""Focused tests for the trusted searchable mixed-output broker."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from threading import Event
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
    WorkerOperationSnapshot,
    WorkerProgressSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import (
    MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
    PDF_EXPORT_VERIFICATION_PIXELS,
    CancellationProbe,
    DeadlineProbe,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_export_report import PdfExportWorkerReport
from paperworkshell.worker.protocol.models import (
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    CancelReason,
    CapabilityDescriptor,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    validate_searchable_pdf_export_start,
)
from paperworkshell.worker.searchable_pdf_export_broker import (
    BrokeredSearchablePdfExportCandidate,
    BrokeredSearchablePdfExporter,
    SearchablePdfExportBrokerPolicy,
    SearchablePdfExportSourceBatchLease,
)
from paperworkshell.worker.searchable_pdf_export_plan import (
    decode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    SearchablePdfWorkerReport,
    encode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    SearchablePdfPageStatusDocument,
    encode_searchable_pdf_page_status,
)


def _uuid(value: int) -> UUID:
    return UUID(f"93000000-0000-4000-8000-{value:012x}")


def _plan() -> SearchablePdfExportPlan:
    image = SearchablePdfSource(
        _uuid(10),
        SearchablePdfSourceMediaType.PNG,
        100,
        1,
        "a" * 64,
        SearchablePdfSourceSignatureState.NOT_APPLICABLE,
        SearchablePdfSourceEncryptionState.NOT_APPLICABLE,
    )
    pdf = SearchablePdfSource(
        _uuid(11),
        SearchablePdfSourceMediaType.PDF,
        200,
        1,
        "b" * 64,
        SearchablePdfSourceSignatureState.MARKERS_DETECTED,
        SearchablePdfSourceEncryptionState.UNENCRYPTED,
    )
    return SearchablePdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=7,
        manifest_sha256="c" * 64,
        manifest_cas_token="synthetic_searchable_broker_cas_001",
        sources=(image, pdf),
        pages=(
            SearchablePdfPage(0, _uuid(20), image.source_id, 0, 1, None, 0, 1_000_000, 0),
            SearchablePdfPage(1, _uuid(21), pdf.source_id, 0, 2, None, 0, 1_000_000, 0),
        ),
    )


def _page_outcomes(plan: SearchablePdfExportPlan) -> tuple[SearchablePdfPageOutcome, ...]:
    return (
        SearchablePdfPageOutcome(
            SearchablePdfPageEligibility(
                0,
                plan.pages[0].page_id,
                SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            ),
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        ),
        SearchablePdfPageOutcome(
            SearchablePdfPageEligibility(
                1,
                plan.pages[1].page_id,
                SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE,
            ),
            SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
            SearchablePdfPageSearchability.NOT_VERIFIED,
            SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED,
        ),
    )


def _policy(
    *,
    page_count: int = 10,
    temporary_storage_bytes: int = 8_192,
    output_bytes: int = 2_048,
    status_bytes: int = 1_024,
    pixel_count: int = PDF_EXPORT_VERIFICATION_PIXELS,
) -> SearchablePdfExportBrokerPolicy:
    return SearchablePdfExportBrokerPolicy(
        ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=8 * 1024 * 1024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=output_bytes,
            page_count=page_count,
            pixel_count=pixel_count,
        ),
        status_capacity_bytes=status_bytes,
    )


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


class _SourceBatch:
    def __init__(self, plan: SearchablePdfExportPlan, events: list[str]) -> None:
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


class _Buffer:
    def __init__(
        self,
        payload: bytes,
        capacity: int,
        handle: int,
        label: str,
        events: list[str],
    ) -> None:
        self.payload = payload
        self.capacity = capacity
        self.write_handle_value = handle
        self.label = label
        self.events = events
        self.iter_calls: list[tuple[int, int]] = []
        self.close_calls = 0
        self.close_error: BaseException | None = None
        self.on_iter: Callable[[], None] | None = None
        self.short = False

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]:
        self.iter_calls.append((byte_count, chunk_bytes))
        self.events.append(f"{self.label}.iter.{len(self.iter_calls)}")
        payload = self.payload[:byte_count]

        def chunks() -> Iterator[bytes]:
            selected = payload[:-1] if self.short else payload
            position = 0
            while position < len(selected):
                end = min(len(selected), position + 4)
                if self.on_iter is not None:
                    self.on_iter()
                yield selected[position:end]
                position = end

        return chunks()

    def close(self) -> None:
        self.close_calls += 1
        self.events.append(f"{self.label}.close")
        if self.close_error is not None:
            raise self.close_error


class _PlanBuffer:
    def __init__(self, payload: bytes, events: list[str]) -> None:
        self.payload = payload
        self.payload_size = len(payload)
        self.read_handle_value = 201
        self.events = events
        self.exited = False

    def __enter__(self) -> Self:
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
    def __init__(self, system: _System) -> None:
        self.system = system
        self.write_handle_value = 203
        self.read_calls = 0
        self.writer_closed = False
        self.raw_frame: bytes | None = None
        self.report_operation_override: UUID | None = None
        self.report_output_override: UUID | None = None
        self.report_status_override: UUID | None = None
        self.report_status_digest_override: str | None = None
        self.report_output_digest_override: str | None = None

    def __enter__(self) -> Self:
        self.system.events.append("pipe.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.system.events.append("pipe.exit")

    def close_local_writer(self) -> None:
        self.writer_closed = True
        self.system.events.append("pipe.close_writer")

    def read_complete_frame(self) -> bytes:
        self.read_calls += 1
        self.system.events.append("pipe.read")
        worker = self.system.worker
        assert worker.terminated
        if self.raw_frame is not None:
            return self.raw_frame
        assert worker.operation_id is not None
        assert worker.output_object_id is not None
        assert worker.status_object_id is not None
        report = SearchablePdfVerificationReport.for_verified_plan(
            self.system.plan,
            output_sha256=(
                hashlib.sha256(self.system.output.payload).hexdigest()
                if self.report_output_digest_override is None
                else self.report_output_digest_override
            ),
            output_byte_size=len(self.system.output.payload),
            page_outcomes=self.system.outcomes,
        )
        operation_id = (
            worker.operation_id
            if self.report_operation_override is None
            else self.report_operation_override
        )
        output_id = (
            worker.output_object_id
            if self.report_output_override is None
            else self.report_output_override
        )
        status_id = (
            worker.status_object_id
            if self.report_status_override is None
            else self.report_status_override
        )
        return encode_searchable_pdf_worker_report(
            SearchablePdfWorkerReport(
                operation_id,
                output_id,
                status_id,
                len(self.system.status.payload),
                (
                    hashlib.sha256(self.system.status.payload).hexdigest()
                    if self.report_status_digest_override is None
                    else self.report_status_digest_override
                ),
                PdfExportWorkerReport(operation_id, output_id, report.base_report),
            )
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
        generation,
        phase,
        case_id,
        _uuid(70),
        _uuid(71),
        321,
        capabilities,
        active,
        result,
        None,
    )


class _Worker:
    def __init__(self, system: _System) -> None:
        self.system = system
        self.generation = 1
        self.terminated = False
        self.payload: OperationStartPayload | None = None
        self.operation_id: UUID | None = None
        self.output_object_id: UUID | None = None
        self.status_object_id: UUID | None = None
        self.terminal_result: WorkerOperationResultSnapshot | None = None
        self.ready_queries = 0
        self.result_queries = 0
        self.duplicate_calls: list[tuple[int, BrokeredHandlePurpose]] = []
        self.cancel_calls: list[CancelReason] = []
        self.capabilities = (SEARCHABLE_PDF_EXPORT_CAPABILITY,)
        self.withhold_result = False
        self.outcome = OperationOutcome.SUCCEEDED
        self.error_code: ErrorCode | None = None
        self.error_category: ErrorCategory | None = None
        self.reverse_outputs = False
        self.status_object_override: UUID | None = None
        self.status_digest_override: str | None = None
        self.status_size_override: int | None = None
        self.progress_unit = ProgressUnit.PAGES
        self.progress_total: int | None = system.plan.page_count
        self.progress_completed = 1
        self.duplicate_collision = False

    def start(self, case_id: UUID) -> int:
        assert case_id == self.system.plan.case_id
        self.system.events.append("worker.start")
        return self.generation

    def query(self) -> WorkerClientSnapshot:
        self.system.events.append("worker.query")
        if self.payload is None:
            self.ready_queries += 1
            return _snapshot(
                generation=self.generation,
                phase=(
                    WorkerLifecyclePhase.STARTING
                    if self.ready_queries == 1
                    else WorkerLifecyclePhase.READY
                ),
                case_id=self.system.plan.case_id,
                capabilities=self.capabilities,
            )
        self.result_queries += 1
        available = (
            self.terminal_result if not self.withhold_result and self.result_queries >= 2 else None
        )
        active = None
        if available is None:
            assert self.operation_id is not None
            active = WorkerOperationSnapshot(
                self.operation_id,
                _uuid(72),
                SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_id,
                SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_version,
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
            case_id=self.system.plan.case_id,
            capabilities=self.capabilities,
            active=active,
            result=available,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.system.events.append("worker.pump")
        return _snapshot(
            generation=self.generation,
            phase=(
                WorkerLifecyclePhase.STARTING if self.payload is None else WorkerLifecyclePhase.BUSY
            ),
            case_id=self.system.plan.case_id,
            capabilities=self.capabilities,
        )

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        self.duplicate_calls.append((source_handle, purpose))
        if self.duplicate_collision and len(self.duplicate_calls) == 6:
            return 1_001
        return 1_000 + len(self.duplicate_calls)

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        validate_searchable_pdf_export_start(payload)
        self.system.events.append("worker.operation")
        self.payload = payload
        self.operation_id = operation_id
        parameters = {parameter.name: parameter for parameter in payload.parameters}
        self.output_object_id = cast(
            UUID,
            parameters[SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER].value,
        )
        self.status_object_id = cast(
            UUID,
            parameters[SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER].value,
        )
        status_document = SearchablePdfPageStatusDocument(
            operation_id,
            self.status_object_id,
            self.system.plan.semantic_fingerprint,
            self.system.outcomes,
        )
        self.system.status.payload = encode_searchable_pdf_page_status(status_document)
        output = CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            self.output_object_id,
            operation_id,
            SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
            len(self.system.output.payload),
            hashlib.sha256(self.system.output.payload).hexdigest(),
        )
        status = CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            (
                self.status_object_id
                if self.status_object_override is None
                else self.status_object_override
            ),
            operation_id,
            SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
            (
                len(self.system.status.payload)
                if self.status_size_override is None
                else self.status_size_override
            ),
            (
                hashlib.sha256(self.system.status.payload).hexdigest()
                if self.status_digest_override is None
                else self.status_digest_override
            ),
        )
        outputs = (status, output) if self.reverse_outputs else (output, status)
        self.terminal_result = WorkerOperationResultSnapshot(
            self.generation,
            operation_id,
            self.outcome,
            outputs if self.outcome is OperationOutcome.SUCCEEDED else (),
            self.error_code,
            self.error_category,
        )
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.cancel_calls.append(reason)
        assert self.operation_id is not None
        self.terminal_result = WorkerOperationResultSnapshot(
            self.generation,
            self.operation_id,
            OperationOutcome.CANCELLED,
            (),
            None,
        )
        self.withhold_result = False
        self.result_queries = 2
        # Match WorkerClient: cancel returns the cancellation request ID, not
        # the active operation ID.
        return _uuid(73)

    def terminate(self) -> None:
        self.system.events.append("worker.terminate")
        self.terminated = True


class _System:
    def __init__(
        self,
        *,
        policy: SearchablePdfExportBrokerPolicy | None = None,
        heavy: HeavyOperationCoordinator | None = None,
    ) -> None:
        self.events: list[str] = []
        self.plan = _plan()
        self.outcomes = _page_outcomes(self.plan)
        self.policy = _policy() if policy is None else policy
        self.output = _Buffer(
            b"%PDF-1.7\nsearchable synthetic output\n%%EOF\n",
            self.policy.limits.output_bytes,
            202,
            "output",
            self.events,
        )
        self.status = _Buffer(
            b"pending",
            self.policy.status_capacity_bytes,
            204,
            "status",
            self.events,
        )
        self.worker = _Worker(self)
        self.sources = _SourceBatch(self.plan, self.events)
        self.sources.worker = self.worker
        self.pipe = _ResultPipe(self)
        self.plan_buffer: _PlanBuffer | None = None
        self.buffer_factory_calls = 0
        self.alias_status_buffer = False
        identifiers = iter((_uuid(90), _uuid(91), _uuid(92), _uuid(93)))

        def buffer_factory(capacity: int) -> _Buffer:
            self.buffer_factory_calls += 1
            return (
                self.output
                if self.buffer_factory_calls == 1 or self.alias_status_buffer
                else self.status
            )

        def plan_factory(payload: bytes) -> _PlanBuffer:
            self.plan_buffer = _PlanBuffer(payload, self.events)
            return self.plan_buffer

        self.heavy = HeavyOperationCoordinator(1) if heavy is None else heavy
        self.exporter = BrokeredSearchablePdfExporter(
            cast(PdfExportWorkerCoordinator, self.worker),
            self.policy,
            heavy_operations=self.heavy,
            pipe_factory=lambda: self.pipe,
            plan_buffer_factory=plan_factory,
            output_buffer_factory=buffer_factory,
            status_buffer_factory=buffer_factory,
            id_factory=lambda: next(identifiers),
        )

    def export(
        self,
        *,
        deadline: DeadlineProbe = lambda: False,
        cancellation: CancellationProbe = lambda: False,
        progress: Callable[[PdfExportProgress], None] = lambda _value: None,
    ) -> BrokeredSearchablePdfExportCandidate:
        return self.exporter.export(
            self.plan,
            cast(SearchablePdfExportSourceBatchLease, self.sources),
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )


def _error(
    system: _System,
    *,
    deadline: DeadlineProbe = lambda: False,
    cancellation: CancellationProbe = lambda: False,
) -> PdfExportError:
    with pytest.raises(PdfExportError) as raised:
        system.export(deadline=deadline, cancellation=cancellation)
    return raised.value


def test_success_terminates_revalidates_authenticates_status_then_streams_pdf() -> None:
    system = _System()
    progress: list[PdfExportProgress] = []

    candidate = system.export(progress=progress.append)

    assert candidate.output_object_id == _uuid(92)
    assert candidate.status_object_id == _uuid(93)
    assert candidate.maximum_output_bytes == system.policy.limits.output_bytes
    assert system.exporter.maximum_output_bytes == system.policy.limits.output_bytes
    assert candidate.report.plan is system.plan
    assert candidate.report.ocr_text_page_count == 1
    assert candidate.report.needs_review_page_count == 1
    assert candidate.report.base_report.unsigned_derivative
    assert progress == [PdfExportProgress(PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 1, 2)]
    assert system.events.index("worker.terminate") < system.events.index("source.revalidate")
    assert system.events.index("source.revalidate") < system.events.index("pipe.read")
    assert system.events.index("pipe.read") < system.events.index("output.iter.1")
    assert system.events.index("output.iter.1") < system.events.index("status.iter.1")
    assert system.status.close_calls == 1
    assert system.output.close_calls == 0

    with candidate:
        parts = [candidate.read(3), candidate.read(5)]
        parts.append(candidate.read(MAX_PDF_EXPORT_CANDIDATE_READ_BYTES))
        assert b"".join(parts) == system.output.payload
        assert candidate.read(1) == b""
    assert system.output.close_calls == 1
    assert "93000000" not in repr(candidate)


def test_payload_has_mixed_authenticated_inputs_four_handles_and_two_outputs() -> None:
    transform_pixels = 20_000_000
    system = _System(policy=_policy(pixel_count=transform_pixels))

    system.export().close()

    payload = system.worker.payload
    plan_buffer = system.plan_buffer
    assert payload is not None and plan_buffer is not None
    validate_searchable_pdf_export_start(payload)
    assert payload.inputs[0] == CaseObjectReference(
        ReferenceArea.SOURCE_READ,
        _uuid(91),
        None,
        SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
        len(plan_buffer.payload),
        hashlib.sha256(plan_buffer.payload).hexdigest(),
    )
    assert tuple(reference.content_kind for reference in payload.inputs[1:]) == (
        "png",
        "pdf",
    )
    assert tuple(reference.object_id for reference in payload.inputs[1:]) == (
        _uuid(10),
        _uuid(11),
    )
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    assert parameters[SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER].value == 1_003
    assert parameters[SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER].value == 1_004
    assert parameters[SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER].value == 1_005
    assert parameters[SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER].value == 1_006
    assert parameters[SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER].value == _uuid(92)
    assert parameters[SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER].value == _uuid(93)
    assert parameters[SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER].value == 1_024
    assert payload.requested_limits.pixel_count == transform_pixels
    assert payload.requested_limits.pixel_count > PDF_EXPORT_VERIFICATION_PIXELS
    assert system.worker.duplicate_calls == [
        (101, BrokeredHandlePurpose.READ_SOURCE),
        (102, BrokeredHandlePurpose.READ_SOURCE),
        (201, BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION),
        (202, BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION),
        (204, BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION),
        (203, BrokeredHandlePurpose.WRITE_RESULT_PIPE),
    ]
    decoded = decode_searchable_pdf_worker_plan(plan_buffer.payload)
    assert decoded.plan == system.plan
    assert tuple(item.worker_handle for item in decoded.sources) == (1_001, 1_002)
    assert b"path" not in plan_buffer.payload.lower()
    assert b"filename" not in plan_buffer.payload.lower()


@pytest.mark.parametrize(
    "mutation",
    (
        "report_operation",
        "report_output",
        "report_status",
        "report_output_digest",
        "report_status_digest",
        "reference_status_digest",
        "reference_status_size",
        "reference_status_object",
        "reverse_outputs",
    ),
)
def test_two_output_report_and_status_correlations_fail_closed(mutation: str) -> None:
    system = _System()
    if mutation == "report_operation":
        system.pipe.report_operation_override = _uuid(98)
    elif mutation == "report_output":
        system.pipe.report_output_override = _uuid(98)
    elif mutation == "report_status":
        system.pipe.report_status_override = _uuid(98)
    elif mutation == "report_output_digest":
        system.pipe.report_output_digest_override = "f" * 64
    elif mutation == "report_status_digest":
        system.pipe.report_status_digest_override = "f" * 64
    elif mutation == "reference_status_digest":
        system.worker.status_digest_override = "f" * 64
    elif mutation == "reference_status_size":
        system.worker.status_size_override = 1
    elif mutation == "reference_status_object":
        system.worker.status_object_override = _uuid(98)
    else:
        system.worker.reverse_outputs = True

    error = _error(system)

    assert error.code in {PdfExportErrorCode.REPORT_INVALID, PdfExportErrorCode.RESULT_INVALID}
    assert system.worker.terminated
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1


def test_status_semantics_must_correlate_to_exact_plan_page_order() -> None:
    system = _System()
    system.outcomes = tuple(reversed(system.outcomes))

    error = _error(system)

    assert error.code is PdfExportErrorCode.REPORT_INVALID
    assert system.worker.terminated
    assert system.output.close_calls == 1


def test_sources_are_revalidated_after_termination_and_rebound_before_reads() -> None:
    system = _System()

    def mutate() -> None:
        system.sources.sources = (
            replace(system.sources.sources[0], sha256="f" * 64),
            system.sources.sources[1],
        )

    system.sources.after_revalidate = mutate

    error = _error(system)

    assert error.code is PdfExportErrorCode.SOURCE_CHANGED
    assert system.sources.revalidate_calls == 1
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []
    assert system.status.iter_calls == []


def test_source_revalidation_error_is_sanitized_before_output_authentication() -> None:
    system = _System()
    system.sources.revalidate_error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "private applicant.pdf",
    )

    error = _error(system)

    assert error.code is PdfExportErrorCode.SOURCE_CHANGED
    assert "applicant" not in str(error)
    assert system.pipe.read_calls == 0
    assert system.output.iter_calls == []


@pytest.mark.parametrize(
    ("target", "terminal", "expected"),
    (
        ("output", "cancel", PdfExportErrorCode.CANCELLED),
        ("output", "deadline", PdfExportErrorCode.TIMED_OUT),
        ("status", "cancel", PdfExportErrorCode.CANCELLED),
        ("status", "deadline", PdfExportErrorCode.TIMED_OUT),
    ),
)
def test_output_authentication_is_bounded_cancelable_and_deadline_aware(
    target: str,
    terminal: str,
    expected: PdfExportErrorCode,
) -> None:
    system = _System()
    signal = Event()
    selected = system.output if target == "output" else system.status
    selected.on_iter = signal.set

    error = _error(
        system,
        deadline=(signal.is_set if terminal == "deadline" else lambda: False),
        cancellation=(signal.is_set if terminal == "cancel" else lambda: False),
    )

    assert error.code is expected
    assert system.worker.terminated
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1


def test_shared_heavy_gate_cancellation_prevents_allocation_and_launch() -> None:
    gate = HeavyOperationCoordinator(1)
    lease = gate.try_acquire(0.01)
    assert lease is not None
    system = _System(heavy=gate)
    try:
        error = _error(system, cancellation=lambda: True)
    finally:
        lease.__exit__()

    assert error.code is PdfExportErrorCode.CANCELLED
    assert system.buffer_factory_calls == 0
    assert system.worker.payload is None


@pytest.mark.parametrize(
    ("terminal", "expected", "reason"),
    (
        ("cancel", PdfExportErrorCode.CANCELLED, CancelReason.USER_REQUESTED),
        ("deadline", PdfExportErrorCode.TIMED_OUT, CancelReason.SHUTDOWN),
    ),
)
def test_waiting_worker_terminal_request_cancels_then_terminates(
    terminal: str,
    expected: PdfExportErrorCode,
    reason: CancelReason,
) -> None:
    system = _System()
    system.worker.withhold_result = True

    error = _error(
        system,
        deadline=(lambda: system.worker.payload is not None)
        if terminal == "deadline"
        else lambda: False,
        cancellation=(lambda: system.worker.payload is not None)
        if terminal == "cancel"
        else lambda: False,
    )

    assert error.code is expected
    assert system.worker.cancel_calls == [reason]
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 0


def test_duplicate_worker_handle_fails_before_operation_dispatch() -> None:
    system = _System()
    system.worker.duplicate_collision = True

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.worker.payload is None
    assert system.worker.terminated
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1


def test_output_and_status_sections_must_be_distinct_before_launch() -> None:
    system = _System()
    system.alias_status_buffer = True

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.worker.payload is None
    assert system.output.close_calls == 2


def test_local_plan_source_output_status_and_result_handles_must_be_distinct() -> None:
    system = _System()
    system.status.write_handle_value = 203

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.worker.payload is None
    assert system.worker.terminated


@pytest.mark.parametrize("short_target", ("output", "status"))
def test_short_authenticated_section_never_exposes_candidate(short_target: str) -> None:
    system = _System()
    (system.output if short_target == "output" else system.status).short = True

    error = _error(system)

    assert error.code is PdfExportErrorCode.RESULT_INVALID
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1


def test_persistent_status_cleanup_failure_still_closes_sensitive_pdf_section() -> None:
    system = _System()
    system.status.close_error = PdfExportError(
        PdfExportErrorCode.INTERNAL_ERROR,
        "searchable.status_cleanup",
    )

    error = _error(system)

    assert error.code is PdfExportErrorCode.INTERNAL_ERROR
    assert error.context == "searchable.status_cleanup"
    # The first status close is the success-path handoff gate and the second is
    # the best-effort final cleanup. Neither may prevent closing the PDF bytes.
    assert system.status.close_calls == 2
    assert system.output.close_calls == 1


def test_policy_and_operation_limits_reserve_both_staging_sections() -> None:
    with pytest.raises(PdfExportError) as bad_policy:
        _policy(temporary_storage_bytes=3_000, output_bytes=2_048, status_bytes=1_024)
    assert bad_policy.value.code is PdfExportErrorCode.MODEL_INVALID

    system = _System(
        policy=_policy(
            temporary_storage_bytes=3_299,
            output_bytes=2_000,
            status_bytes=1_000,
        )
    )
    error = _error(system)
    assert error.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert system.buffer_factory_calls == 0


def test_correlated_worker_resource_limit_maps_to_retryable_export_limit() -> None:
    system = _System()
    system.worker.outcome = OperationOutcome.FAILED
    system.worker.error_code = ErrorCode.WORKER_OPERATION_FAILED
    system.worker.error_category = ErrorCategory.RESOURCE_LIMIT

    error = _error(system)

    assert error.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert error.context == "searchable.worker_resource_limit"
    assert system.worker.terminated
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1


def test_permanent_revocation_fails_before_allocating_or_launching() -> None:
    system = _System()
    system.exporter._revoke_permanently()

    error = _error(system)

    assert system.exporter.revoked
    assert error.code is PdfExportErrorCode.WORKER_FAILED
    assert system.events == []
