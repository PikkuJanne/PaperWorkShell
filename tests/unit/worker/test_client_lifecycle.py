"""Focused PWS-0107 tests for the GUI-safe worker client coordinator."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientError,
    WorkerClientErrorCode,
    WorkerRecoveryAction,
)
from paperworkshell.worker.client.models import (
    WorkerLifecyclePhase,
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import (
    BrokeredHandlePurpose,
    IsolationEvidence,
    TransportReceive,
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    OCR_PROCESS_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    QUALITY_OCR_REVIEW_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    CancelDisposition,
    ClosedReason,
    Direction,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    MessageType,
    OperationAcceptedPayload,
    OperationCancelAckPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ProtocolEnvelope,
    ResourceLimits,
    RetryAction,
    SessionClosedPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
)
from paperworkshell.worker.protocol.peer import PeerBinding, PreparedMessage, ProtocolPeer

CASE_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class SwitchableClock(FakeClock):
    def __init__(self, value: float = 10.0) -> None:
        super().__init__(value)
        self.invalid = False

    def __call__(self) -> float:
        if self.invalid:
            return cast(float, object())
        return super().__call__()


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.incoming: deque[TransportReceive] = deque()
        self.receive_calls = 0
        self.close_calls = 0
        self.fail_send = False

    def send_payload(self, payload: bytes) -> None:
        if self.fail_send:
            raise OSError("C:\\private\\brokered-send-canary.pdf")
        self.sent.append(payload)

    def receive_payload(self, _timeout_seconds: float) -> TransportReceive:
        self.receive_calls += 1
        if self.incoming:
            return self.incoming.popleft()
        return TransportReceive(TransportReceiveKind.TIMED_OUT)

    def close(self) -> None:
        self.close_calls += 1

    def queue(self, payload: bytes) -> None:
        self.incoming.append(TransportReceive(TransportReceiveKind.PAYLOAD, payload))


class FakeProcess:
    def __init__(self, pid: int, isolation: IsolationEvidence) -> None:
        self.pid = pid
        self.transport = FakeTransport()
        self.isolation = isolation
        self.exit_status: int | None = None
        self.poll_calls = 0
        self.terminate_calls = 0
        self.close_calls = 0
        self.fail_termination = False
        self.fail_close = False
        self.fail_duplicate = False
        self.duplicate_calls: list[tuple[int, BrokeredHandlePurpose]] = []
        self.resource_snapshot_calls = 0
        self.resource_observation = WorkerResourceSnapshot(
            process_memory_limit_bytes=64 * 1024 * 1024,
            job_memory_limit_bytes=128 * 1024 * 1024,
            peak_process_memory_used_bytes=1_024,
            peak_job_memory_used_bytes=2_048,
        )

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self.exit_status

    def wait(self, _timeout_seconds: float | None) -> int | None:
        return self.exit_status

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        self.duplicate_calls.append((source_handle, purpose))
        if self.fail_duplicate:
            raise OSError("C:\\private\\brokered-handle-canary.pdf")
        return source_handle + 10_000

    def terminate_tree(self) -> None:
        self.terminate_calls += 1
        if self.fail_termination:
            raise OSError("C:\\private\\unconfirmed-worker-tree")

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("C:\\private\\unconfirmed-worker-tree")

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        self.resource_snapshot_calls += 1
        return self.resource_observation


@dataclass(slots=True)
class LaunchRecord:
    request: WorkerLaunchRequest
    process: FakeProcess
    worker_peer: ProtocolPeer


class FakeLauncher:
    def __init__(self, isolation: IsolationEvidence | None = None) -> None:
        self.isolation = isolation or _isolation()
        self.records: list[LaunchRecord] = []

    def launch(self, request: WorkerLaunchRequest) -> FakeProcess:
        process = FakeProcess(4_000 + len(self.records), self.isolation)
        peer = ProtocolPeer(
            PeerBinding(
                request.session_id,
                request.worker_instance_id,
                request.case_id,
                request.session_capability,
            ),
            Direction.WORKER_TO_CLIENT,
        )
        self.records.append(LaunchRecord(request, process, peer))
        return process


class FailingLauncher:
    def launch(self, _request: WorkerLaunchRequest) -> FakeProcess:
        raise OSError("C:\\private\\applicant-name.pdf")


class AdvancingLauncher(FakeLauncher):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock

    def launch(self, request: WorkerLaunchRequest) -> FakeProcess:
        self.clock.value += 20.0
        return super().launch(request)


class NullTransportLauncher(FakeLauncher):
    def launch(self, request: WorkerLaunchRequest) -> FakeProcess:
        process = super().launch(request)
        process.transport = cast(FakeTransport, None)
        return process


class FailingSendTransport(FakeTransport):
    def send_payload(self, payload: bytes) -> None:
        del payload
        raise OSError("C:\\private\\post-launch-transport-canary.pdf")


class FailingSendTransportLauncher(FakeLauncher):
    def launch(self, request: WorkerLaunchRequest) -> FakeProcess:
        process = super().launch(request)
        process.transport = FailingSendTransport()
        return process


class InvalidHelloIdFactory:
    def __init__(self) -> None:
        self._values: deque[object] = deque(
            (
                UUID("22222222-2222-4222-8222-222222222222"),
                UUID("33333333-3333-4333-8333-333333333333"),
                object(),
            )
        )

    def __call__(self) -> UUID:
        return cast(UUID, self._values.popleft())


class InvalidPostLaunchClock:
    def __call__(self) -> float:
        return cast(float, object())


def _isolation(**overrides: bool | int) -> IsolationEvidence:
    values: dict[str, bool | int] = {
        "app_container": True,
        "network_denied": True,
        "job_object_assigned": True,
        "kill_on_job_close": True,
        "case_paths_absent": True,
        "minimal_environment": True,
        "explicit_handle_list": True,
        "shell_disabled": True,
        "inherited_handle_count": 3,
    }
    values.update(overrides)
    return IsolationEvidence(**values)  # type: ignore[arg-type]


def _resource_policy() -> WorkerResourcePolicy:
    return WorkerResourcePolicy(
        process_memory_bytes=64 * 1024 * 1024,
        job_memory_bytes=128 * 1024 * 1024,
        cpu_time_ms=20_000,
        maximum_processes=4,
    )


def _timeout_policy() -> WorkerTimeoutPolicy:
    return WorkerTimeoutPolicy(
        handshake_seconds=2.0,
        cancellation_grace_seconds=1.0,
        shutdown_grace_seconds=1.0,
    )


def _limits(*, wall_time_ms: int = 1_000) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=wall_time_ms,
        cpu_time_ms=500,
        memory_bytes=16 * 1024 * 1024,
        temporary_storage_bytes=8 * 1024 * 1024,
        output_bytes=4 * 1024 * 1024,
        page_count=1,
        pixel_count=1,
    )


def _client(
    launcher: FakeLauncher,
    clock: FakeClock | None = None,
) -> WorkerClient:
    return WorkerClient(
        launcher,
        _resource_policy(),
        _timeout_policy(),
        clock=clock or FakeClock(),
    )


def _consume(record: LaunchRecord) -> ProtocolEnvelope:
    return record.worker_peer.accept_inbound(record.process.transport.sent.pop(0))


def _queue(record: LaunchRecord, prepared: PreparedMessage) -> None:
    record.process.transport.queue(prepared.encoded)


def _ready_client(
    *,
    clock: FakeClock | None = None,
    launcher: FakeLauncher | None = None,
) -> tuple[WorkerClient, FakeLauncher, LaunchRecord, int]:
    selected_launcher = launcher or FakeLauncher()
    client = _client(selected_launcher, clock)
    generation = client.start(CASE_ID)
    record = selected_launcher.records[-1]
    hello = _consume(record)
    assert hello.message_type is MessageType.SESSION_HELLO
    ready = record.worker_peer.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(
            selected_protocol_version=1,
            capabilities=(
                NOOP_CAPABILITY,
                PDF_INSPECT_CAPABILITY,
                PDF_THUMBNAIL_CAPABILITY,
                PDF_EXPORT_CAPABILITY,
                IMAGE_NORMALIZE_CAPABILITY,
                IMAGE_TRANSFORM_CAPABILITY,
                OCR_PROCESS_CAPABILITY,
                QUALITY_OCR_REVIEW_CAPABILITY,
                SEARCHABLE_PDF_EXPORT_CAPABILITY,
            ),
            max_message_bytes=MAX_MESSAGE_BYTES,
        ),
        request_id=hello.request_id,
        operation_id=None,
    )
    _queue(record, ready)
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.READY
    return client, selected_launcher, record, generation


def _start_and_accept(
    client: WorkerClient,
    record: LaunchRecord,
    *,
    wall_time_ms: int = 1_000,
) -> tuple[UUID, ProtocolEnvelope]:
    limits = _limits(wall_time_ms=wall_time_ms)
    operation_id = client.start_operation(
        OperationStartPayload(
            operation_kind=NOOP_CAPABILITY.capability_id,
            operation_version=NOOP_CAPABILITY.capability_version,
            inputs=(),
            parameters=(),
            requested_limits=limits,
        )
    )
    start = _consume(record)
    accepted = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_ACCEPTED,
        OperationAcceptedPayload(
            operation_kind=NOOP_CAPABILITY.capability_id,
            operation_version=NOOP_CAPABILITY.capability_version,
            effective_limits=limits,
        ),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, accepted)
    client.pump()
    active = client.query().active_operation
    assert active is not None
    assert active.accepted
    return operation_id, start


def test_start_negotiates_and_query_is_local_and_secret_free() -> None:
    client, _launcher, record, generation = _ready_client()

    before_receive = record.process.transport.receive_calls
    before_poll = record.process.poll_calls
    snapshot = client.query()

    assert generation == 1
    assert snapshot.phase is WorkerLifecyclePhase.READY
    assert snapshot.pid == record.process.pid
    assert snapshot.capabilities == (
        NOOP_CAPABILITY,
        PDF_INSPECT_CAPABILITY,
        PDF_THUMBNAIL_CAPABILITY,
        PDF_EXPORT_CAPABILITY,
        IMAGE_NORMALIZE_CAPABILITY,
        IMAGE_TRANSFORM_CAPABILITY,
        OCR_PROCESS_CAPABILITY,
        QUALITY_OCR_REVIEW_CAPABILITY,
        SEARCHABLE_PDF_EXPORT_CAPABILITY,
    )
    assert record.process.transport.receive_calls == before_receive
    assert record.process.poll_calls == before_poll
    secret = record.request.session_capability.value
    assert secret not in repr(record.request)
    assert secret not in repr(snapshot)
    assert "path" not in " ".join(snapshot.__dataclass_fields__)
    assert "capability" not in " ".join(snapshot.__dataclass_fields__)


def test_handshake_deadline_starts_after_synchronous_containment_launch() -> None:
    clock = FakeClock()
    launcher = AdvancingLauncher(clock)
    client = _client(launcher, clock)

    client.start(CASE_ID)
    client.check_deadlines(clock.value)

    assert clock.value == 30.0
    assert client.query().phase is WorkerLifecyclePhase.STARTING
    clock.value = 32.0
    client.check_deadlines()
    failure = client.query().last_failure
    assert failure is not None
    assert failure.code is WorkerClientErrorCode.HANDSHAKE_TIMED_OUT


@pytest.mark.parametrize(
    ("launcher", "expected_code"),
    (
        (NullTransportLauncher(), WorkerClientErrorCode.LAUNCH_FAILED),
        (FailingSendTransportLauncher(), WorkerClientErrorCode.TRANSPORT_FAILED),
    ),
)
def test_every_post_launch_transport_failure_disposes_the_process(
    launcher: FakeLauncher,
    expected_code: WorkerClientErrorCode,
) -> None:
    client = _client(launcher)

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    process = launcher.records[0].process
    snapshot = client.query()
    assert raised.value.code is expected_code
    assert snapshot.phase is WorkerLifecyclePhase.FAULTED
    assert snapshot.pid is None
    assert snapshot.last_failure is not None
    assert snapshot.last_failure.code is expected_code
    assert process.terminate_calls == 1
    assert process.close_calls == 1


def test_invalid_post_launch_hello_id_disposes_the_process() -> None:
    launcher = FakeLauncher()
    client = WorkerClient(
        launcher,
        _resource_policy(),
        _timeout_policy(),
        id_factory=InvalidHelloIdFactory(),
    )

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    process = launcher.records[0].process
    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    failure = client.query().last_failure
    assert failure is not None
    assert failure.code is raised.value.code
    assert process.terminate_calls == 1
    assert process.close_calls == 1


def test_invalid_post_launch_clock_disposes_the_process() -> None:
    launcher = FakeLauncher()
    client = WorkerClient(
        launcher,
        _resource_policy(),
        _timeout_policy(),
        clock=InvalidPostLaunchClock(),
    )

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    process = launcher.records[0].process
    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    failure = client.query().last_failure
    assert failure is not None
    assert failure.code is raised.value.code
    assert process.terminate_calls == 1
    assert process.close_calls == 1


def test_invalid_clock_cannot_send_an_operation_without_arming_its_deadline() -> None:
    clock = SwitchableClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    clock.invalid = True

    with pytest.raises(WorkerClientError) as raised:
        client.start_operation(
            OperationStartPayload(
                operation_kind=NOOP_CAPABILITY.capability_id,
                operation_version=NOOP_CAPABILITY.capability_version,
                inputs=(),
                parameters=(),
                requested_limits=_limits(),
            )
        )

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert client.query().phase is WorkerLifecyclePhase.READY
    assert client.query().active_operation is None
    assert record.process.transport.sent == []
    assert record.process.terminate_calls == 0
    assert record.process.close_calls == 0


def test_ready_owner_can_transfer_reduced_handles_and_choose_operation_id() -> None:
    client, _launcher, record, _generation = _ready_client()
    chosen_operation_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    source_target = client.duplicate_handle(71, BrokeredHandlePurpose.READ_SOURCE)
    result_target = client.duplicate_handle(73, BrokeredHandlePurpose.WRITE_RESULT_PIPE)
    returned_operation_id = client.start_operation(
        OperationStartPayload(
            operation_kind=NOOP_CAPABILITY.capability_id,
            operation_version=NOOP_CAPABILITY.capability_version,
            inputs=(),
            parameters=(),
            requested_limits=_limits(),
        ),
        operation_id=chosen_operation_id,
    )
    start = _consume(record)

    assert source_target == 10_071
    assert result_target == 10_073
    assert record.process.duplicate_calls == [
        (71, BrokeredHandlePurpose.READ_SOURCE),
        (73, BrokeredHandlePurpose.WRITE_RESULT_PIPE),
    ]
    assert returned_operation_id == chosen_operation_id
    assert start.operation_id == chosen_operation_id


def test_handle_transfer_requires_owner_thread_and_ready_phase() -> None:
    client, _launcher, record, _generation = _ready_client()
    raised: list[WorkerClientError] = []

    def transfer_from_wrong_thread() -> None:
        try:
            client.duplicate_handle(79, BrokeredHandlePurpose.READ_SOURCE)
        except WorkerClientError as error:
            raised.append(error)

    thread = threading.Thread(target=transfer_from_wrong_thread)
    thread.start()
    thread.join(timeout=1.0)

    assert len(raised) == 1
    assert raised[0].code is WorkerClientErrorCode.OWNER_THREAD_REQUIRED
    assert record.process.duplicate_calls == []
    _start_and_accept(client, record)
    with pytest.raises(WorkerClientError) as busy:
        client.duplicate_handle(79, BrokeredHandlePurpose.READ_SOURCE)
    assert busy.value.code is WorkerClientErrorCode.STATE_INVALID
    assert record.process.duplicate_calls == []


def test_duplicate_or_following_send_failure_terminates_the_generation() -> None:
    client, _launcher, record, _generation = _ready_client()
    record.process.fail_duplicate = True

    with pytest.raises(WorkerClientError) as duplicate_failure:
        client.duplicate_handle(83, BrokeredHandlePurpose.READ_SOURCE)

    assert duplicate_failure.value.code is WorkerClientErrorCode.HANDLE_TRANSFER_FAILED
    assert "brokered-handle-canary" not in repr(duplicate_failure.value)
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    assert record.process.terminate_calls == 1
    assert record.process.close_calls == 1

    client, _launcher, record, _generation = _ready_client()
    client.duplicate_handle(89, BrokeredHandlePurpose.READ_SOURCE)
    record.process.transport.fail_send = True
    with pytest.raises(WorkerClientError) as send_failure:
        client.start_operation(
            OperationStartPayload(
                operation_kind=NOOP_CAPABILITY.capability_id,
                operation_version=NOOP_CAPABILITY.capability_version,
                inputs=(),
                parameters=(),
                requested_limits=_limits(),
            )
        )

    assert send_failure.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    assert record.process.terminate_calls == 1
    assert record.process.close_calls == 1


def test_invalid_clock_cannot_send_cancellation_without_arming_its_deadline() -> None:
    clock = SwitchableClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    _start_and_accept(client, record)
    clock.invalid = True

    with pytest.raises(WorkerClientError) as raised:
        client.cancel()

    snapshot = client.query()
    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert snapshot.phase is WorkerLifecyclePhase.BUSY
    assert snapshot.active_operation is not None
    assert not snapshot.active_operation.cancellation_requested
    assert record.process.transport.sent == []
    assert record.process.terminate_calls == 0
    assert record.process.close_calls == 0


def test_invalid_clock_cannot_send_shutdown_without_arming_its_deadline() -> None:
    clock = SwitchableClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    clock.invalid = True

    with pytest.raises(WorkerClientError) as raised:
        client.shutdown()

    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert client.query().phase is WorkerLifecyclePhase.READY
    assert record.process.transport.sent == []
    assert record.process.terminate_calls == 0
    assert record.process.close_calls == 0


def test_operation_accept_progress_and_success_are_protocol_authoritative() -> None:
    client, _launcher, record, generation = _ready_client()
    operation_id, start = _start_and_accept(client, record)

    progress = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_PROGRESS,
        OperationProgressPayload(
            stage_code=ProgressStage.PROCESSING,
            completed_units=1,
            total_units=2,
            unit=ProgressUnit.STEPS,
        ),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, progress)
    client.pump()
    active = client.query().active_operation
    assert active is not None
    assert active.progress is not None
    assert active.progress.completed_units == 1

    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, result)
    client.pump()

    snapshot = client.query()
    assert snapshot.phase is WorkerLifecyclePhase.READY
    assert snapshot.active_operation is None
    assert snapshot.last_result is not None
    assert snapshot.last_result.generation == generation
    assert snapshot.last_result.outcome is OperationOutcome.SUCCEEDED
    assert snapshot.last_resource_snapshot == record.process.resource_observation
    assert record.process.resource_snapshot_calls == 1


def test_operation_result_snapshot_preserves_protocol_resource_category() -> None:
    client, _launcher, record, _generation = _ready_client()
    operation_id, start = _start_and_accept(client, record)
    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(
            OperationOutcome.FAILED,
            (),
            ErrorPayload(
                ErrorCode.WORKER_OPERATION_FAILED,
                ErrorCategory.RESOURCE_LIMIT,
                RetryAction.RETRY,
                False,
                (),
            ),
        ),
        request_id=start.request_id,
        operation_id=operation_id,
    )

    _queue(record, result)
    client.pump()

    snapshot = client.query().last_result
    assert snapshot is not None
    assert snapshot.operation_id == operation_id
    assert snapshot.error_code is ErrorCode.WORKER_OPERATION_FAILED
    assert snapshot.error_category is ErrorCategory.RESOURCE_LIMIT


def test_cancel_ack_is_nonterminal_and_success_may_win_the_race() -> None:
    clock = FakeClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    operation_id, start = _start_and_accept(client, record)

    cancel_request = client.cancel()
    cancel = _consume(record)
    assert cancel.request_id == cancel_request
    acknowledgement = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_CANCEL_ACK,
        OperationCancelAckPayload(CancelDisposition.REQUESTED),
        request_id=cancel_request,
        operation_id=operation_id,
    )
    _queue(record, acknowledgement)
    clock.value += 0.125
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.CANCELLING
    assert client.query().last_cancellation_acknowledgement_ms == 125

    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, result)
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.READY
    result_snapshot = client.query().last_result
    assert result_snapshot is not None
    assert result_snapshot.outcome is OperationOutcome.SUCCEEDED


def test_terminal_payload_before_ordered_deadline_event_wins() -> None:
    clock = FakeClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    operation_id, start = _start_and_accept(client, record, wall_time_ms=1_000)
    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, result)
    clock.value = 11.0

    client.pump(0.0)
    client.check_deadlines(11.0)

    assert client.query().phase is WorkerLifecyclePhase.READY
    assert client.query().last_result is not None


def test_deadline_event_first_faults_once_and_late_payload_cannot_replace_it() -> None:
    clock = FakeClock()
    client, _launcher, record, generation = _ready_client(clock=clock)
    operation_id, start = _start_and_accept(client, record, wall_time_ms=1_000)
    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    clock.value = 11.0

    client.check_deadlines()
    failure = client.query().last_failure
    with pytest.raises(WorkerClientError) as raised:
        client.accept_payload(generation, result.encoded)

    assert raised.value.code is WorkerClientErrorCode.STATE_INVALID
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    assert client.query().last_failure == failure
    assert failure is not None
    assert failure.code is WorkerClientErrorCode.OPERATION_TIMED_OUT
    assert record.process.terminate_calls == 1


def test_nonterminal_progress_cannot_starve_an_expired_operation_deadline() -> None:
    clock = FakeClock()
    client, _launcher, record, _generation = _ready_client(clock=clock)
    operation_id, start = _start_and_accept(client, record, wall_time_ms=1_000)
    progress = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_PROGRESS,
        OperationProgressPayload(
            stage_code=ProgressStage.PROCESSING,
            completed_units=1,
            total_units=2,
            unit=ProgressUnit.STEPS,
        ),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, progress)
    clock.value = 11.0

    client.pump(0.0)

    failure = client.query().last_failure
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    assert failure is not None
    assert failure.code is WorkerClientErrorCode.OPERATION_TIMED_OUT


def test_shutdown_waits_for_terminal_cancel_ack_and_preserves_reason() -> None:
    client, _launcher, record, _generation = _ready_client()
    operation_id, start = _start_and_accept(client, record)
    client.shutdown(ShutdownReason.APPLICATION_EXIT)
    cancel = _consume(record)

    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, result)
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.CANCELLING
    assert client.query().active_operation is None

    acknowledgement = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_CANCEL_ACK,
        OperationCancelAckPayload(CancelDisposition.ALREADY_TERMINAL),
        request_id=cancel.request_id,
        operation_id=operation_id,
    )
    _queue(record, acknowledgement)
    client.pump()
    shutdown = _consume(record)
    assert shutdown.message_type is MessageType.SESSION_SHUTDOWN
    assert type(shutdown.payload) is SessionShutdownPayload
    assert shutdown.payload.reason is ShutdownReason.APPLICATION_EXIT
    assert client.query().phase is WorkerLifecyclePhase.STOPPING

    closed = record.worker_peer.prepare_outbound(
        MessageType.SESSION_CLOSED,
        SessionClosedPayload(ClosedReason.SHUTDOWN_COMPLETE),
        request_id=shutdown.request_id,
        operation_id=None,
    )
    _queue(record, closed)
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.STOPPED
    assert record.process.terminate_calls == 1


def test_shutdown_after_cancel_result_waits_for_the_terminal_ack() -> None:
    client, _launcher, record, _generation = _ready_client()
    operation_id, start = _start_and_accept(client, record)
    cancel_request = client.cancel()
    cancel = _consume(record)
    assert cancel.request_id == cancel_request

    result = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        request_id=start.request_id,
        operation_id=operation_id,
    )
    _queue(record, result)
    client.pump()
    assert client.query().phase is WorkerLifecyclePhase.CANCELLING
    assert client.query().active_operation is None
    assert record.process.transport.sent == []

    client.shutdown(ShutdownReason.APPLICATION_EXIT)
    assert client.query().phase is WorkerLifecyclePhase.CANCELLING
    assert record.process.transport.sent == []

    acknowledgement = record.worker_peer.prepare_outbound(
        MessageType.OPERATION_CANCEL_ACK,
        OperationCancelAckPayload(CancelDisposition.ALREADY_TERMINAL),
        request_id=cancel_request,
        operation_id=operation_id,
    )
    _queue(record, acknowledgement)
    client.pump()
    shutdown = _consume(record)

    assert shutdown.message_type is MessageType.SESSION_SHUTDOWN
    assert type(shutdown.payload) is SessionShutdownPayload
    assert shutdown.payload.reason is ShutdownReason.APPLICATION_EXIT
    assert client.query().phase is WorkerLifecyclePhase.STOPPING


@pytest.mark.parametrize("result_first", (True, False))
def test_invalid_auto_shutdown_deadline_faults_after_either_terminal_order(
    result_first: bool,
) -> None:
    clock = SwitchableClock()
    client, _launcher, record, generation = _ready_client(clock=clock)
    operation_id, start = _start_and_accept(client, record)
    client.shutdown(ShutdownReason.APPLICATION_EXIT)
    cancel = _consume(record)

    if result_first:
        first = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_RESULT,
            OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
            request_id=start.request_id,
            operation_id=operation_id,
        )
        client.accept_payload(generation, first.encoded)
        assert client.query().active_operation is None
        terminal = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_CANCEL_ACK,
            OperationCancelAckPayload(CancelDisposition.ALREADY_TERMINAL),
            request_id=cancel.request_id,
            operation_id=operation_id,
        )
    else:
        first = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_CANCEL_ACK,
            OperationCancelAckPayload(CancelDisposition.REQUESTED),
            request_id=cancel.request_id,
            operation_id=operation_id,
        )
        client.accept_payload(generation, first.encoded)
        assert client.query().active_operation is not None
        terminal = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_RESULT,
            OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
            request_id=start.request_id,
            operation_id=operation_id,
        )

    clock.invalid = True
    with pytest.raises(WorkerClientError) as raised:
        client.accept_payload(generation, terminal.encoded)

    snapshot = client.query()
    assert raised.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert snapshot.phase is WorkerLifecyclePhase.FAULTED
    assert snapshot.pid is None
    assert snapshot.active_operation is None
    assert record.process.transport.sent == []
    assert record.process.terminate_calls == 1
    assert record.process.close_calls == 1


def test_crash_is_stable_and_restart_rejects_the_old_generation() -> None:
    client, launcher, old_record, old_generation = _ready_client()
    old_capability = old_record.request.session_capability.value
    client.notify_process_exit(old_generation)

    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    failure = client.query().last_failure
    assert failure is not None
    assert failure.code is WorkerClientErrorCode.PROCESS_EXITED

    new_generation = client.restart()
    new_record = launcher.records[-1]
    before = client.query()
    with pytest.raises(WorkerClientError) as raised:
        client.accept_payload(old_generation, b"not-a-message")

    assert raised.value.code is WorkerClientErrorCode.GENERATION_STALE
    assert client.query() == before
    assert new_generation == old_generation + 1
    assert new_record.request.session_id != old_record.request.session_id
    assert new_record.request.worker_instance_id != old_record.request.worker_instance_id
    assert new_record.request.session_capability.value != old_capability


def test_unconfirmed_cleanup_is_sticky_and_blocks_a_second_generation() -> None:
    client, launcher, record, _generation = _ready_client()
    record.process.fail_termination = True
    record.process.fail_close = True

    with pytest.raises(WorkerClientError) as termination_error:
        client.terminate()
    with pytest.raises(WorkerClientError) as start_error:
        client.start(CASE_ID)
    with pytest.raises(WorkerClientError) as restart_error:
        client.restart()

    assert termination_error.value.code is WorkerClientErrorCode.TERMINATION_FAILED
    assert termination_error.value.recovery_action is WorkerRecoveryAction.NONE
    assert start_error.value.code is WorkerClientErrorCode.TERMINATION_FAILED
    assert restart_error.value.code is WorkerClientErrorCode.TERMINATION_FAILED
    assert len(launcher.records) == 1
    assert record.process.terminate_calls == 1
    assert record.process.close_calls == 1
    assert client.query().phase is WorkerLifecyclePhase.FAULTED


def test_successful_final_close_recovers_from_an_initial_terminate_failure() -> None:
    client, launcher, record, _generation = _ready_client()
    record.process.fail_termination = True

    client.terminate()
    next_generation = client.start(CASE_ID)

    assert client.query().phase is WorkerLifecyclePhase.STARTING
    assert next_generation == 2
    assert len(launcher.records) == 2
    assert record.process.terminate_calls == 1
    assert record.process.close_calls == 1


def test_cleanup_failure_overrides_the_raised_protocol_failure() -> None:
    client, _launcher, record, generation = _ready_client()
    record.process.fail_termination = True
    record.process.fail_close = True

    with pytest.raises(WorkerClientError) as raised:
        client.accept_payload(generation, b"malformed")

    failure = client.query().last_failure
    assert failure is not None
    assert raised.value.code is failure.code is WorkerClientErrorCode.TERMINATION_FAILED
    assert raised.value.context is failure.context
    assert raised.value.recovery_action is failure.recovery_action is WorkerRecoveryAction.NONE


class CleanupFailingLauncher(FakeLauncher):
    def launch(self, request: WorkerLaunchRequest) -> FakeProcess:
        process = super().launch(request)
        process.fail_termination = True
        process.fail_close = True
        return process


def test_cleanup_failure_overrides_the_raised_isolation_failure() -> None:
    launcher = CleanupFailingLauncher(_isolation(network_denied=False))
    client = _client(launcher)

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    failure = client.query().last_failure
    assert failure is not None
    assert raised.value.code is failure.code is WorkerClientErrorCode.TERMINATION_FAILED
    assert raised.value.context is failure.context


def test_incomplete_isolation_fails_closed_and_terminates_the_process_tree() -> None:
    launcher = FakeLauncher(_isolation(network_denied=False))
    client = _client(launcher)

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    assert raised.value.code is WorkerClientErrorCode.ISOLATION_INSUFFICIENT
    assert client.query().phase is WorkerLifecyclePhase.FAULTED
    assert launcher.records[0].process.terminate_calls == 1


def test_launch_exception_is_mapped_without_retaining_private_text() -> None:
    client = WorkerClient(FailingLauncher(), _resource_policy(), _timeout_policy())

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    rendered = f"{raised.value!s} {raised.value!r}"
    assert raised.value.code is WorkerClientErrorCode.LAUNCH_FAILED
    assert "applicant-name" not in rendered
    assert raised.value.__context__ is None
    assert client.query().last_failure is not None


def test_unexpected_extra_inherited_handle_fails_the_isolation_boundary() -> None:
    launcher = FakeLauncher(_isolation(inherited_handle_count=4))
    client = _client(launcher)

    with pytest.raises(WorkerClientError) as raised:
        client.start(CASE_ID)

    assert raised.value.code is WorkerClientErrorCode.ISOLATION_INSUFFICIENT
    assert launcher.records[0].process.terminate_calls == 1


def test_malformed_payload_fault_is_sanitized() -> None:
    client, _launcher, record, generation = _ready_client()
    prohibited = "C:\\private\\applicant-name.pdf"

    with pytest.raises(WorkerClientError) as raised:
        client.accept_payload(generation, prohibited.encode("utf-8"))

    rendered = f"{raised.value!s} {raised.value!r} {client.query().last_failure!r}"
    assert raised.value.code is WorkerClientErrorCode.PROTOCOL_FAILED
    assert prohibited not in rendered
    assert "applicant-name" not in rendered
    assert record.process.terminate_calls == 1


def test_coordinator_rejects_cross_thread_use_without_process_access() -> None:
    client, _launcher, record, _generation = _ready_client()
    poll_calls = record.process.poll_calls
    received: list[WorkerClientError] = []

    def query_from_other_thread() -> None:
        try:
            client.query()
        except WorkerClientError as error:
            received.append(error)

    thread = threading.Thread(target=query_from_other_thread)
    thread.start()
    thread.join()

    assert len(received) == 1
    assert received[0].code is WorkerClientErrorCode.OWNER_THREAD_REQUIRED
    assert record.process.poll_calls == poll_calls


def test_process_and_timeout_configuration_reject_boolean_or_incoherent_limits() -> None:
    with pytest.raises(WorkerClientError) as memory_error:
        WorkerResourcePolicy(True, 2, 1, 1)
    with pytest.raises(WorkerClientError) as job_error:
        WorkerResourcePolicy(3, 2, 1, 1)
    with pytest.raises(WorkerClientError) as timeout_error:
        WorkerTimeoutPolicy(1.0, 0.0, 1.0)
    with pytest.raises(WorkerClientError) as huge_policy_error:
        WorkerTimeoutPolicy(10**1_000, 1.0, 1.0)
    client = _client(FakeLauncher())
    with pytest.raises(WorkerClientError) as huge_wait_error:
        client.pump(10**1_000)
    with pytest.raises(WorkerClientError) as huge_observed_time_error:
        client.check_deadlines(10**1_000)

    assert memory_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert job_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert timeout_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert huge_policy_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert huge_wait_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert huge_observed_time_error.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_ids_are_uuid4_and_no_path_or_command_is_launch_authority() -> None:
    client, _launcher, record, _generation = _ready_client()

    assert record.request.case_id.version == 4
    assert record.request.session_id.version == 4
    assert record.request.worker_instance_id.version == 4
    fields = set(record.request.__dataclass_fields__)
    assert fields == {
        "case_id",
        "session_id",
        "worker_instance_id",
        "session_capability",
        "resource_policy",
    }
    assert not {"path", "argv", "command", "environment", "handle"} & fields
    assert client.query().case_id == CASE_ID
