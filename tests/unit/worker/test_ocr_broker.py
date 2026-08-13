"""PWS-0204 focused tests for the one-shot OCR broker."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.ocr import (
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrProgress,
    OcrProgressPhase,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
    encode_ocr_worker_report,
    ocr_engine_profile_sha256,
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
from paperworkshell.worker.ocr_broker import (
    BrokeredOcrProcessor,
    OcrBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    OCR_PROCESS_CAPABILITY,
    OCR_PROCESS_OUTPUT_CONTENT_KIND,
    OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
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
)


def _uuid(number: int) -> UUID:
    return UUID(f"84000000-0000-4000-8000-{number:012x}")


def _request() -> OcrProcessRequest:
    return OcrProcessRequest(
        case_id=_uuid(1),
        source_id=_uuid(2),
        source_kind=OcrSourceKind.PDF,
        source_byte_size=64,
        source_page_count=2,
        signature_state=OcrSourceSignatureState.NONE_DETECTED,
        encryption_state=OcrSourceEncryptionState.UNENCRYPTED,
        language_set_id="eng-v1",
        source_sha256="a" * 64,
    )


def _policy() -> OcrBrokerPolicy:
    return OcrBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=8 * 1024 * 1024,
            temporary_storage_bytes=4 * 1024 * 1024,
            output_bytes=1024 * 1024,
            page_count=10,
            pixel_count=4_000_000,
        )
    )


class _Source:
    def __init__(self, request: OcrProcessRequest, worker: _Worker) -> None:
        self.case_id = request.case_id
        self.source_id = request.source_id
        self.source_kind = request.source_kind
        self.byte_size = request.source_byte_size
        self.page_count = request.source_page_count
        self.sha256 = request.source_sha256
        self.signature_state = request.signature_state
        self.encryption_state = request.encryption_state
        self.handle_value = 101
        self.worker = worker
        self.revalidate_calls = 0
        self.revalidate_error: SourceIngestError | None = None

    def revalidate(self, *, deadline: object, cancellation: object) -> None:
        del deadline, cancellation
        assert self.worker.terminated
        self.revalidate_calls += 1
        if self.revalidate_error is not None:
            raise self.revalidate_error


class _Output:
    def __init__(self, payload: bytes, capacity: int) -> None:
        self.payload = payload
        self.capacity = capacity
        self.write_handle_value = 202
        self.closed = False
        self.bad_digest_bytes = False
        self.chunk_hook: Callable[[], None] | None = None
        self.close_failure: Exception | None = None

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]:
        assert chunk_bytes == 1024 * 1024
        selected = self.payload[:byte_count]
        if self.bad_digest_bytes:
            selected = b"X" + selected[1:]
        midpoint = max(1, len(selected) // 2)
        yield selected[:midpoint]
        if self.chunk_hook is not None:
            self.chunk_hook()
        if midpoint < len(selected):
            yield selected[midpoint:]

    def close(self) -> None:
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


class _Pipe:
    def __init__(self, worker: _Worker) -> None:
        self.worker = worker
        self.write_handle_value = 203
        self.writer_closed = False
        self.malformed = False
        self.report_override: OcrWorkerReport | None = None

    def __enter__(self) -> _Pipe:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def close_local_writer(self) -> None:
        self.writer_closed = True

    def read_complete_frame(self) -> bytes:
        if self.malformed:
            return b"private malformed report"
        if self.report_override is not None:
            return encode_ocr_worker_report(self.report_override)
        assert self.worker.operation_id is not None
        assert self.worker.output_id is not None
        return encode_ocr_worker_report(
            OcrWorkerReport(
                operation_id=self.worker.operation_id,
                source_id=_uuid(2),
                output_object_id=self.worker.output_id,
                source_kind=OcrSourceKind.PDF,
                page_count=2,
                processed_page_count=2,
                language_set_id="eng-v1",
                profile_id="searchable-pdf-v1",
                ocrmypdf_version="17.10.0",
                tesseract_version="5.5.3",
                engine_profile_sha256=ocr_engine_profile_sha256("17.10.0", "5.5.3"),
                output_byte_size=len(self.worker.output.payload),
                output_sha256=hashlib.sha256(self.worker.output.payload).hexdigest(),
            )
        )


def _snapshot(
    phase: WorkerLifecyclePhase,
    *,
    capabilities: tuple[CapabilityDescriptor, ...],
    active: WorkerOperationSnapshot | None = None,
    result: WorkerOperationResultSnapshot | None = None,
) -> WorkerClientSnapshot:
    return WorkerClientSnapshot(
        generation=1,
        phase=phase,
        case_id=_uuid(1),
        session_id=_uuid(20),
        worker_instance_id=_uuid(21),
        pid=123,
        capabilities=capabilities,
        active_operation=active,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(self, output: _Output) -> None:
        self.output = output
        self.operation_id: UUID | None = None
        self.output_id: UUID | None = None
        self.payload: OperationStartPayload | None = None
        self.result: WorkerOperationResultSnapshot | None = None
        self.terminated = False
        self.result_queries = 0
        self.capabilities: tuple[CapabilityDescriptor, ...] = (OCR_PROCESS_CAPABILITY,)
        self.withhold_result = False
        self.outcome = OperationOutcome.SUCCEEDED
        self.cancel_calls: list[CancelReason] = []

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(1)
        return 1

    def query(self) -> WorkerClientSnapshot:
        if self.payload is None:
            return _snapshot(WorkerLifecyclePhase.READY, capabilities=self.capabilities)
        self.result_queries += 1
        available = self.result if not self.withhold_result and self.result_queries >= 2 else None
        active = None
        if available is None:
            assert self.operation_id is not None
            active = WorkerOperationSnapshot(
                self.operation_id,
                _uuid(22),
                OCR_PROCESS_CAPABILITY.capability_id,
                OCR_PROCESS_CAPABILITY.capability_version,
                True,
                bool(self.cancel_calls),
                WorkerProgressSnapshot(
                    ProgressStage.PROCESSING,
                    1,
                    2,
                    ProgressUnit.PAGES,
                ),
            )
        return _snapshot(
            WorkerLifecyclePhase.BUSY,
            capabilities=self.capabilities,
            active=active,
            result=available,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        return self.query()

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        expected = {
            BrokeredHandlePurpose.READ_SOURCE: 101,
            BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION: 202,
            BrokeredHandlePurpose.WRITE_RESULT_PIPE: 203,
        }
        assert source_handle == expected[purpose]
        return {
            BrokeredHandlePurpose.READ_SOURCE: 1001,
            BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION: 1002,
            BrokeredHandlePurpose.WRITE_RESULT_PIPE: 1003,
        }[purpose]

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        self.payload = payload
        self.operation_id = operation_id
        parameter = next(
            item
            for item in payload.parameters
            if item.name == OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER
        )
        assert parameter.value_type is ScalarValueType.UUID
        self.output_id = cast(UUID, parameter.value)
        output = CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            self.output_id,
            operation_id,
            OCR_PROCESS_OUTPUT_CONTENT_KIND,
            len(self.output.payload),
            hashlib.sha256(self.output.payload).hexdigest(),
        )
        self.result = WorkerOperationResultSnapshot(
            1,
            operation_id,
            self.outcome,
            (output,) if self.outcome is OperationOutcome.SUCCEEDED else (),
            None,
        )
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        assert self.operation_id is not None
        self.cancel_calls.append(reason)
        self.withhold_result = False
        self.result_queries = 2
        self.result = WorkerOperationResultSnapshot(
            1,
            self.operation_id,
            OperationOutcome.CANCELLED,
            (),
            None,
        )
        return self.operation_id

    def terminate(self) -> None:
        self.terminated = True


class _System:
    def __init__(self) -> None:
        self.request = _request()
        self.output = _Output(b"%PDF-1.7\nsynthetic OCR candidate\n%%EOF\n", 1024 * 1024)
        self.worker = _Worker(self.output)
        self.source = _Source(self.request, self.worker)
        self.pipe = _Pipe(self.worker)
        identifiers = iter((_uuid(3), _uuid(4)))
        self.processor = BrokeredOcrProcessor(
            self.worker,
            _policy(),
            heavy_operations=HeavyOperationCoordinator(1),
            pipe_factory=lambda: self.pipe,
            output_buffer_factory=lambda capacity: self._output(capacity),
            id_factory=lambda: next(identifiers),
        )

    def _output(self, capacity: int) -> _Output:
        assert capacity == self.output.capacity
        return self.output


def test_success_terminates_revalidates_authenticates_and_streams_candidate() -> None:
    system = _System()
    progress: list[OcrProgress] = []

    with system.processor.process(
        system.request,
        system.source,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda value: progress.append(value),
    ) as candidate:
        first = candidate.read(7)
        remainder = candidate.read(1024)
        assert first + remainder == system.output.payload
        assert candidate.read(1) == b""

    assert system.worker.terminated
    assert system.source.revalidate_calls == 1
    assert system.output.closed
    assert [item.phase for item in progress] == [
        OcrProgressPhase.PREPARING,
        OcrProgressPhase.RECOGNIZING,
        OcrProgressPhase.VALIDATING,
        OcrProgressPhase.COMPLETE,
    ]
    assert system.worker.payload is not None
    assert system.worker.payload.inputs[0].expected_sha256 == system.request.source_sha256
    assert system.pipe.writer_closed


def test_cancellation_sends_protocol_cancel_and_publishes_nothing() -> None:
    system = _System()
    system.worker.withhold_result = True
    checks = iter((False, False, True, True))

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: next(checks, True),
        )

    assert raised.value.code is OcrErrorCode.CANCELLED
    assert system.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert system.worker.terminated
    assert system.source.revalidate_calls == 0
    assert system.output.closed


def test_deadline_sends_shutdown_cancel_and_publishes_nothing() -> None:
    system = _System()
    system.worker.withhold_result = True
    checks = iter((False, False, True, True))

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: next(checks, True),
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.TIMED_OUT
    assert system.worker.cancel_calls == [CancelReason.SHUTDOWN]
    assert system.worker.terminated
    assert system.output.closed


def test_missing_capability_fails_before_any_operation() -> None:
    system = _System()
    system.worker.capabilities = ()

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.ENGINE_UNAVAILABLE
    assert system.worker.payload is None
    assert system.worker.terminated


def test_malformed_report_is_rejected_after_source_revalidation() -> None:
    system = _System()
    system.pipe.malformed = True

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert system.source.revalidate_calls == 1
    assert system.output.closed


def test_worker_crash_publishes_no_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    system = _System()
    original_query = system.worker.query

    def query() -> WorkerClientSnapshot:
        if system.worker.payload is None:
            return original_query()
        return _snapshot(
            WorkerLifecyclePhase.FAULTED,
            capabilities=(OCR_PROCESS_CAPABILITY,),
        )

    monkeypatch.setattr(system.worker, "query", query)

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.ENGINE_FAILED
    assert raised.value.context == "ocr.worker_exit"
    assert system.worker.terminated
    assert system.output.closed


def test_report_correlation_mismatch_is_rejected() -> None:
    system = _System()
    report = OcrWorkerReport(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_object_id=_uuid(4),
        source_kind=OcrSourceKind.PDF,
        page_count=2,
        processed_page_count=2,
        language_set_id="eng-v1",
        profile_id="searchable-pdf-v1",
        ocrmypdf_version="17.10.0",
        tesseract_version="5.5.3",
        engine_profile_sha256=ocr_engine_profile_sha256("17.10.0", "5.5.3"),
        output_byte_size=len(system.output.payload),
        output_sha256=hashlib.sha256(system.output.payload).hexdigest(),
    )
    system.pipe.report_override = replace(report, language_set_id="fin-v1")

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert raised.value.context == "ocr.report_binding"


def test_report_engine_profile_mismatch_is_rejected() -> None:
    system = _System()
    assert system.worker.operation_id is None
    report = OcrWorkerReport(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_object_id=_uuid(4),
        source_kind=OcrSourceKind.PDF,
        page_count=2,
        processed_page_count=2,
        language_set_id="eng-v1",
        profile_id="searchable-pdf-v1",
        ocrmypdf_version="17.10.0",
        tesseract_version="5.5.3",
        engine_profile_sha256="f" * 64,
        output_byte_size=len(system.output.payload),
        output_sha256=hashlib.sha256(system.output.payload).hexdigest(),
    )
    system.pipe.report_override = report

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert raised.value.context == "ocr.report_binding"


def test_self_consistent_but_untrusted_engine_versions_are_rejected() -> None:
    system = _System()
    report = OcrWorkerReport(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_object_id=_uuid(4),
        source_kind=OcrSourceKind.PDF,
        page_count=2,
        processed_page_count=2,
        language_set_id="eng-v1",
        profile_id="searchable-pdf-v1",
        ocrmypdf_version="17.10.1",
        tesseract_version="5.5.4",
        engine_profile_sha256=ocr_engine_profile_sha256("17.10.1", "5.5.4"),
        output_byte_size=len(system.output.payload),
        output_sha256=hashlib.sha256(system.output.payload).hexdigest(),
    )
    system.pipe.report_override = report

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert raised.value.context == "ocr.report_binding"


def test_changed_source_after_worker_termination_blocks_candidate() -> None:
    system = _System()
    system.source.revalidate_error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "private.source.changed",
    )

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.SOURCE_INVALID
    assert system.worker.terminated
    assert system.output.closed


def test_output_digest_mismatch_blocks_candidate() -> None:
    system = _System()
    system.output.bad_digest_bytes = True

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.OUTPUT_INVALID
    assert raised.value.context == "ocr.output_digest"


@pytest.mark.parametrize(
    ("terminal_kind", "expected_code"),
    (
        ("cancelled", OcrErrorCode.CANCELLED),
        ("timed_out", OcrErrorCode.TIMED_OUT),
    ),
)
def test_terminal_request_during_output_authentication_publishes_nothing(
    terminal_kind: str,
    expected_code: OcrErrorCode,
) -> None:
    system = _System()
    terminal = {"requested": False}
    system.output.chunk_hook = lambda: terminal.__setitem__("requested", True)

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: terminal_kind == "timed_out" and terminal["requested"],
            cancellation=lambda: terminal_kind == "cancelled" and terminal["requested"],
        )

    assert raised.value.code is expected_code
    assert system.worker.terminated
    assert system.source.revalidate_calls == 1
    assert system.output.closed


def test_failed_path_output_cleanup_failure_is_surfaced_safely() -> None:
    system = _System()
    system.pipe.malformed = True
    system.output.close_failure = RuntimeError("private output cleanup detail")

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.INTERNAL_ERROR
    assert raised.value.context == "ocr.output_cleanup"
    assert "private output cleanup detail" not in repr(raised.value)
    assert "private output cleanup detail" not in str(raised.value)
    assert system.output.closed


def test_terminal_request_from_completion_callback_prevents_candidate_transfer() -> None:
    system = _System()
    cancelled = {"requested": False}

    def progress(value: OcrProgress) -> None:
        if value.phase is OcrProgressPhase.COMPLETE:
            cancelled["requested"] = True

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: cancelled["requested"],
            progress=progress,
        )

    assert raised.value.code is OcrErrorCode.CANCELLED
    assert system.worker.terminated
    assert system.output.closed


def test_request_and_source_mismatch_fails_before_launch() -> None:
    system = _System()
    system.source.sha256 = "f" * 64

    with pytest.raises(OcrError) as raised:
        system.processor.process(
            system.request,
            system.source,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert raised.value.code is OcrErrorCode.SOURCE_INVALID
    assert system.worker.operation_id is None
