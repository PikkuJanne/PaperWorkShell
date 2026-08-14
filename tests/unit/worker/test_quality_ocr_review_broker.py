from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.ocr_languages import OCR_LANGUAGE_SET_DEFINITIONS
from paperworkshell.domain.pdf_inspection import (
    PdfInspectionError,
    PdfInspectionErrorCode,
)
from paperworkshell.domain.quality_ocr_review import (
    QUALITY_OCR_REVIEW_PROFILE_ID,
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    QualityOcrReviewStatus,
    encode_quality_ocr_review_report,
)
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
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    QUALITY_OCR_REVIEW_CAPABILITY,
    QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
    QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_VALUES,
    QUALITY_OCR_REVIEW_PROFILE_ID_VALUE,
    QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX,
    CancelReason,
    CapabilityDescriptor,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ResourceLimits,
    validate_quality_ocr_review_start,
)
from paperworkshell.worker.quality_ocr_review_broker import (
    BrokeredQualityOcrReviewer,
    QualityOcrReviewBrokerPolicy,
)


def _uuid(number: int) -> UUID:
    return UUID(f"97000000-0000-4000-8000-{number:012x}")


def _request() -> QualityOcrReviewRequest:
    return QualityOcrReviewRequest(
        case_id=_uuid(1),
        manifest_revision=7,
        page_id=_uuid(2),
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        pixels=bytes((12, 34, 56, 255)) * 16,
        presentation_sha256="a" * 64,
        language_set_id="german-fast-v1",
    )


def _policy() -> QualityOcrReviewBrokerPolicy:
    return QualityOcrReviewBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=1_048_576,
            temporary_storage_bytes=8_192,
            output_bytes=4_096,
            page_count=1,
            pixel_count=65_536,
        )
    )


def test_wire_profile_and_language_registry_match_domain_authority() -> None:
    assert QUALITY_OCR_REVIEW_PROFILE_ID_VALUE == QUALITY_OCR_REVIEW_PROFILE_ID
    assert QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_VALUES == frozenset(
        definition.language_set_id for definition in OCR_LANGUAGE_SET_DEFINITIONS
    )


class _Input:
    def __init__(self, payload: bytes) -> None:
        self.payload_size = len(payload)
        self.read_handle_value = 101
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _Input:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class _Pipe:
    def __init__(self, worker: _Worker, request: QualityOcrReviewRequest) -> None:
        self.worker = worker
        self.request = request
        self.write_handle_value = 202
        self.writer_closed = False
        self.closed = False
        self.malformed = False
        self.mutation: str | None = None

    def close_local_writer(self) -> None:
        self.writer_closed = True

    def read_complete_frame(self) -> bytes:
        if self.malformed:
            return b"private OCR text at C:/private/report.tsv"
        assert self.worker.operation_id is not None
        report = QualityOcrReviewReport(
            operation_id=self.worker.operation_id,
            case_id=self.request.case_id,
            manifest_revision=self.request.manifest_revision,
            page_id=self.request.page_id,
            sample_sha256=self.request.sample_sha256,
            presentation_sha256=self.request.presentation_sha256,
            language_set_id=self.request.language_set_id,
            profile_id=self.request.profile_id,
            status=QualityOcrReviewStatus.SUCCEEDED,
            recognized_word_count=4,
            mean_confidence_milli=82_500,
        )
        if self.mutation == "sample":
            report = replace(report, sample_sha256="b" * 64)
        return encode_quality_ocr_review_report(report)

    def __enter__(self) -> _Pipe:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True


def _snapshot(
    worker: _Worker,
    phase: WorkerLifecyclePhase,
    *,
    active: WorkerOperationSnapshot | None = None,
    result: WorkerOperationResultSnapshot | None = None,
) -> WorkerClientSnapshot:
    return WorkerClientSnapshot(
        generation=worker.generation,
        phase=phase,
        case_id=worker.request.case_id,
        session_id=_uuid(40),
        worker_instance_id=_uuid(41),
        pid=123,
        capabilities=worker.capabilities,
        active_operation=active,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(self, request: QualityOcrReviewRequest) -> None:
        self.request = request
        self.generation = 0
        self.operation_id: UUID | None = None
        self.payload: OperationStartPayload | None = None
        self.result: WorkerOperationResultSnapshot | None = None
        self.result_queries = 0
        self.withhold_result = False
        self.outcome = OperationOutcome.SUCCEEDED
        self.error_category: ErrorCategory | None = None
        self.query_error: WorkerClientError | None = None
        self.ready_phase = WorkerLifecyclePhase.READY
        self.termination_error: WorkerClientError | None = None
        self.capabilities: tuple[CapabilityDescriptor, ...] = (QUALITY_OCR_REVIEW_CAPABILITY,)
        self.cancel_calls: list[CancelReason] = []
        self.start_calls = 0
        self.terminate_calls = 0

    def start(self, case_id: UUID) -> int:
        assert case_id == self.request.case_id
        self.generation += 1
        self.operation_id = None
        self.payload = None
        self.result = None
        self.result_queries = 0
        self.cancel_calls = []
        self.start_calls += 1
        return self.generation

    def query(self) -> WorkerClientSnapshot:
        if self.query_error is not None:
            raise self.query_error
        if self.payload is None:
            return _snapshot(self, self.ready_phase)
        self.result_queries += 1
        available = self.result if not self.withhold_result and self.result_queries >= 2 else None
        active = None
        if available is None:
            assert self.operation_id is not None
            active = WorkerOperationSnapshot(
                self.operation_id,
                _uuid(42),
                QUALITY_OCR_REVIEW_CAPABILITY.capability_id,
                QUALITY_OCR_REVIEW_CAPABILITY.capability_version,
                True,
                bool(self.cancel_calls),
                WorkerProgressSnapshot(
                    ProgressStage.PROCESSING,
                    0,
                    1,
                    ProgressUnit.PAGES,
                ),
            )
        return _snapshot(
            self,
            WorkerLifecyclePhase.BUSY,
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
            BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION: 101,
            BrokeredHandlePurpose.WRITE_RESULT_PIPE: 202,
        }
        assert source_handle == expected[purpose]
        return {
            BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION: 1001,
            BrokeredHandlePurpose.WRITE_RESULT_PIPE: 1002,
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
        self.result = WorkerOperationResultSnapshot(
            self.generation,
            operation_id,
            self.outcome,
            (),
            (
                ErrorCode.WORKER_OPERATION_FAILED
                if self.outcome is OperationOutcome.FAILED
                else None
            ),
            self.error_category,
        )
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        assert self.operation_id is not None
        self.cancel_calls.append(reason)
        self.withhold_result = False
        self.result_queries = 2
        self.result = WorkerOperationResultSnapshot(
            self.generation,
            self.operation_id,
            OperationOutcome.CANCELLED,
            (),
            None,
        )
        return self.operation_id

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.termination_error is not None:
            raise self.termination_error


class _System:
    def __init__(self) -> None:
        self.request = _request()
        self.worker = _Worker(self.request)
        self.inputs: list[_Input] = []
        self.pipes: list[_Pipe] = []
        self.heavy = HeavyOperationCoordinator(1)
        identifiers = iter(_uuid(number) for number in range(3, 35))
        self.reviewer = BrokeredQualityOcrReviewer(
            self.worker,
            _policy(),
            heavy_operations=self.heavy,
            input_buffer_factory=self._input,
            pipe_factory=self._pipe,
            id_factory=lambda: next(identifiers),
        )

    def _input(self, payload: bytes) -> _Input:
        assert payload == self.request.pixels
        selected = _Input(payload)
        self.inputs.append(selected)
        return selected

    def _pipe(self) -> _Pipe:
        selected = _Pipe(self.worker, self.request)
        self.pipes.append(selected)
        return selected


def test_success_is_exactly_bound_handle_only_and_one_shot() -> None:
    system = _System()

    report = system.reviewer.review(
        system.request,
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert report.page_id == system.request.page_id
    assert report.sample_sha256 == system.request.sample_sha256
    assert system.worker.terminate_calls == 1
    assert system.inputs[0].closed
    assert system.pipes[0].writer_closed and system.pipes[0].closed
    assert system.worker.payload is not None
    assert system.worker.payload.inputs == ()
    parameters = {item.name: item.value for item in system.worker.payload.parameters}
    assert parameters[QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER] == "german-fast-v1"
    assert parameters[QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER] == (
        f"{QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX}{system.request.sample_sha256}"
    )
    validate_quality_ocr_review_start(system.worker.payload)
    assert system.heavy.snapshot().active_operations == 0


def test_reviewer_is_sequential_and_terminates_each_worker_generation() -> None:
    system = _System()

    for _ in range(2):
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert system.worker.start_calls == 2
    assert system.worker.terminate_calls == 2
    assert len(system.inputs) == len(system.pipes) == 2
    assert all(item.closed for item in system.inputs)
    assert all(item.closed for item in system.pipes)
    assert system.heavy.snapshot().maximum_observed_concurrency == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER, "a" * 64),
        (QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER, "future-language-v9"),
        ("profile_id", "future-profile-v9"),
    ],
)
def test_protocol_rejects_noncanonical_digest_and_open_configuration_tokens(
    name: str,
    value: str,
) -> None:
    system = _System()
    system.reviewer.review(
        system.request,
        deadline=lambda: False,
        cancellation=lambda: False,
    )
    assert system.worker.payload is not None
    malformed = replace(
        system.worker.payload,
        parameters=tuple(
            replace(parameter, value=value) if parameter.name == name else parameter
            for parameter in system.worker.payload.parameters
        ),
    )

    with pytest.raises(ProtocolError):
        validate_quality_ocr_review_start(malformed)


@pytest.mark.parametrize(
    ("probe", "expected", "reason"),
    [
        ("cancellation", QualityOcrReviewErrorCode.CANCELLED, CancelReason.USER_REQUESTED),
        ("deadline", QualityOcrReviewErrorCode.TIMED_OUT, CancelReason.SHUTDOWN),
    ],
)
def test_cancel_and_timeout_send_protocol_cancel_and_cleanup(
    probe: str,
    expected: QualityOcrReviewErrorCode,
    reason: CancelReason,
) -> None:
    system = _System()
    system.worker.withhold_result = True

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: probe == "deadline" and system.worker.payload is not None,
            cancellation=lambda: probe == "cancellation" and system.worker.payload is not None,
        )

    assert rejected.value.code is expected
    assert system.worker.cancel_calls == [reason]
    assert system.worker.terminate_calls == 1
    assert system.inputs[0].closed and system.pipes[0].closed


def test_missing_capability_fails_before_operation_and_cleans_up() -> None:
    system = _System()
    system.worker.capabilities = ()

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE
    assert system.worker.payload is None
    assert system.worker.terminate_calls == 1


def test_malformed_or_mismatched_report_fails_closed_without_text_leakage() -> None:
    system = _System()
    original_factory = cast(Callable[[], _Pipe], system.reviewer._pipe_factory)

    def malformed_pipe() -> _Pipe:
        pipe = original_factory()
        pipe.malformed = len(system.pipes) == 1
        if not pipe.malformed:
            pipe.mutation = "sample"
        return pipe

    system.reviewer._pipe_factory = malformed_pipe
    for expected_context in ("quality_ocr.report_decode", "quality_ocr.report_binding"):
        with pytest.raises(QualityOcrReviewError) as rejected:
            system.reviewer.review(
                system.request,
                deadline=lambda: False,
                cancellation=lambda: False,
            )
        assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID
        assert "private" not in f"{rejected.value!r} {rejected.value}".lower()
        assert rejected.value.context == expected_context
    assert system.worker.terminate_calls == 2


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (ErrorCategory.RESOURCE_LIMIT, QualityOcrReviewErrorCode.LIMIT_EXCEEDED),
        (ErrorCategory.OPERATION, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (ErrorCategory.PROTOCOL, QualityOcrReviewErrorCode.RESULT_INVALID),
    ],
)
def test_failed_worker_results_never_become_advisory_engine_failures(
    category: ErrorCategory,
    expected: QualityOcrReviewErrorCode,
) -> None:
    system = _System()
    system.worker.outcome = OperationOutcome.FAILED
    system.worker.error_category = category

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is expected
    assert rejected.value.code is not QualityOcrReviewErrorCode.ENGINE_FAILED
    assert system.worker.terminate_calls == 1
    assert system.inputs[0].closed and system.pipes[0].closed


@pytest.mark.parametrize(
    ("pipe_code", "expected"),
    [
        (PdfInspectionErrorCode.REPORT_INVALID, QualityOcrReviewErrorCode.RESULT_INVALID),
        (PdfInspectionErrorCode.HANDLE_INVALID, QualityOcrReviewErrorCode.RESULT_INVALID),
        (PdfInspectionErrorCode.INTERNAL_ERROR, QualityOcrReviewErrorCode.INTERNAL_ERROR),
    ],
)
def test_report_pipe_faults_preserve_data_vs_local_infrastructure_classification(
    pipe_code: PdfInspectionErrorCode,
    expected: QualityOcrReviewErrorCode,
) -> None:
    system = _System()

    def fail_pipe() -> _Pipe:
        raise PdfInspectionError(pipe_code, "broker.pipe_create")

    system.reviewer._pipe_factory = fail_pipe

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is expected
    assert system.inputs[0].closed
    assert system.worker.start_calls == 0


@pytest.mark.parametrize(
    ("worker_code", "expected"),
    [
        (WorkerClientErrorCode.PROTOCOL_FAILED, QualityOcrReviewErrorCode.RESULT_INVALID),
        (WorkerClientErrorCode.LAUNCH_FAILED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.HANDSHAKE_TIMED_OUT, QualityOcrReviewErrorCode.TIMED_OUT),
        (WorkerClientErrorCode.OPERATION_TIMED_OUT, QualityOcrReviewErrorCode.TIMED_OUT),
        (WorkerClientErrorCode.CANCELLATION_TIMED_OUT, QualityOcrReviewErrorCode.TIMED_OUT),
        (WorkerClientErrorCode.STATE_INVALID, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.OWNER_THREAD_REQUIRED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.GENERATION_STALE, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.CONFIGURATION_INVALID, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.ISOLATION_INSUFFICIENT, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.PROCESS_EXITED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.TRANSPORT_FAILED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.HANDLE_TRANSFER_FAILED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.RESOURCE_REPORT_FAILED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.SHUTDOWN_TIMED_OUT, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.RESTART_REQUIRED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
        (WorkerClientErrorCode.TERMINATION_FAILED, QualityOcrReviewErrorCode.INTERNAL_ERROR),
    ],
)
def test_worker_client_faults_preserve_safety_classification(
    worker_code: WorkerClientErrorCode,
    expected: QualityOcrReviewErrorCode,
) -> None:
    system = _System()
    system.worker.query_error = WorkerClientError(worker_code, WorkerClientContext.PROTOCOL)

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is expected
    assert system.worker.terminate_calls == 1


@pytest.mark.parametrize(
    "phase",
    [WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED],
)
def test_stopped_or_faulted_handshake_is_an_internal_safety_failure(
    phase: WorkerLifecyclePhase,
) -> None:
    system = _System()
    system.worker.ready_phase = phase

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert system.worker.terminate_calls == 1


def test_termination_failure_after_success_refuses_the_report() -> None:
    system = _System()
    system.worker.termination_error = WorkerClientError(
        WorkerClientErrorCode.TERMINATION_FAILED,
        WorkerClientContext.SHUTDOWN,
    )

    with pytest.raises(QualityOcrReviewError) as rejected:
        system.reviewer.review(
            system.request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert system.worker.terminate_calls == 1
