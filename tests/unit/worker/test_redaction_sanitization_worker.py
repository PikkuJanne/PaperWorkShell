"""Private sanitization transport rejects escalation and preserves closed gating."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.worker import redaction_sanitization_composition as composition
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import WorkerClientError
from paperworkshell.worker.client.models import WorkerClientSnapshot, WorkerLifecyclePhase
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.host import _build_operation_registry
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.pdf_export_broker import (
    PdfExportBrokerPolicy,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.private_temp import (
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    REDACTION_SANITIZATION_CAPABILITY,
    REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND,
    REDACTION_SANITIZATION_PLAN_CONTENT_KIND,
    CaseObjectReference,
    Direction,
    ErrorCategory,
    MessageType,
    OperationAcceptedPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
)
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer
from paperworkshell.worker.protocol.redaction_sanitization import (
    sanitization_binding,
    validate_sanitization_result,
)
from paperworkshell.worker.redaction_sanitization_broker import (
    BrokeredRedactionSanitizer,
    RetainedSanitizationSourceLease,
    UnverifiedSanitizationCandidate,
)
from paperworkshell.worker.redaction_sanitization_operation import (
    SanitizationOperationHandler,
    WindowsSanitizationOperationAdapter,
)
from paperworkshell.worker.redaction_sanitization_wire import (
    SanitizationWorkerReport,
    decode_sanitization_plan,
    decode_sanitization_report,
    encode_sanitization_plan,
    encode_sanitization_report,
)
from tests.unit.test_redaction_sanitization_models import _id, report, request
from tests.unit.worker.test_pdf_export_broker import (
    _OutputBuffer,
    _plan,
    _PlanBuffer,
    _policy,
    _ResultPipe,
    _Worker,
)
from tests.unit.worker.test_pdf_export_composition import _config, _FakeWorkerClient


def _is_closed(
    value: UnverifiedSanitizationCandidate | composition.SanitizationWorkerComposition,
) -> bool:
    return value.closed


def _payload() -> OperationStartPayload:
    intent = request()
    raw = encode_sanitization_plan(intent)
    return OperationStartPayload(
        REDACTION_SANITIZATION_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _id(10),
                None,
                REDACTION_SANITIZATION_PLAN_CONTENT_KIND,
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            ),
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                intent.source_id,
                None,
                "pdf",
                intent.source_byte_size,
                intent.source_sha256,
            ),
        ),
        tuple(
            TaggedScalar(name, ScalarValueType.INTEGER, index + 20)
            for index, name in enumerate(
                ("source_handle", "plan_handle", "output_handle", "result_handle")
            )
        )
        + (TaggedScalar("output_object_id", ScalarValueType.UUID, _id(11)),),
        replace(_policy().limits, temporary_storage_bytes=10 * 1024 * 1024),
    )


def test_closed_wire_roundtrip_duplicate_future_tamper_and_size_failures() -> None:
    value = request()
    raw = encode_sanitization_plan(value)
    assert decode_sanitization_plan(raw) == value
    result = SanitizationWorkerReport(_id(20), _id(21), report(value))
    assert decode_sanitization_report(encode_sanitization_report(result)) == result
    for invalid in (
        b'{"request_version":1,"request_version":1}',
        raw.replace(b'"request_version":1', b'"request_version":2'),
        b" " + raw,
        b"x" * (8 * 1024 * 1024),
        b'{"x":NaN}',
    ):
        with pytest.raises(PdfExportError):
            decode_sanitization_plan(invalid)

    encoded = encode_sanitization_report(result)
    for invalid in (
        encoded.replace(b'"worker_report_version":1', b'"worker_report_version":2'),
        encoded.replace(b'"awaiting_verification"', b'"verified"'),
        encoded.replace(b'"report_version":1', b'"report_version":true'),
        b" " + encoded,
        encoded[:-1] + b',"extra":1}',
    ):
        with pytest.raises(PdfExportError):
            decode_sanitization_report(invalid)


@pytest.mark.parametrize(
    "change",
    ["area", "operation", "object", "kind", "size", "digest", "missing", "duplicate", "failure"],
)
def test_result_rejects_uncorrelated_or_failed_candidate(change: str) -> None:
    output = CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        _id(11),
        _id(30),
        REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND,
        100,
        "a" * 64,
    )
    changes: dict[str, dict[str, object]] = {
        "area": {"area": ReferenceArea.SOURCE_READ},
        "operation": {"operation_id": _id(31)},
        "object": {"object_id": _id(12)},
        "kind": {"content_kind": "pdf"},
        "size": {"expected_byte_size": 1001},
        "digest": {"expected_sha256": None},
    }
    if change in changes:
        for name, value in changes[change].items():
            object.__setattr__(output, name, value)
    outputs = () if change == "missing" else (output,) * (2 if change == "duplicate" else 1)
    payload = OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None)
    if change == "failure":
        object.__setattr__(payload, "outcome", OperationOutcome.CANCELLED)
    with pytest.raises(ProtocolError):
        validate_sanitization_result(
            payload, operation_id=_id(30), output_object_id=_id(11), maximum_output_bytes=1000
        )


def _started_peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_id(100), _id(101), request().case_id, SessionCapability("d" * 64))
    registry = composition.SanitizationOperationRegistry()
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER, operation_registry=registry)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT, operation_registry=registry)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (REDACTION_SANITIZATION_CAPABILITY,)),
        request_id=_id(102),
        operation_id=None,
    )
    worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (REDACTION_SANITIZATION_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=_id(102),
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
    payload = _payload()
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_id(103),
        operation_id=_id(30),
    )
    worker.accept_inbound(start.encoded)
    accepted = worker.prepare_outbound(
        MessageType.OPERATION_ACCEPTED,
        OperationAcceptedPayload(
            REDACTION_SANITIZATION_CAPABILITY.capability_id, 1, payload.requested_limits
        ),
        request_id=_id(103),
        operation_id=_id(30),
    )
    client.accept_inbound(accepted.encoded)
    return client, worker


@pytest.mark.parametrize(
    "change", ["progress_unit", "progress_total", "output_id", "output_kind", None]
)
def test_protocol_state_enforces_private_progress_and_output_binding(change: str | None) -> None:
    client, worker = _started_peers()
    if change in {"progress_unit", "progress_total"}:
        with pytest.raises(ProtocolError):
            worker.prepare_outbound(
                MessageType.OPERATION_PROGRESS,
                OperationProgressPayload(
                    ProgressStage.PROCESSING,
                    0,
                    2 if change == "progress_total" else 1,
                    ProgressUnit.PAGES if change == "progress_unit" else ProgressUnit.STEPS,
                ),
                request_id=_id(103),
                operation_id=_id(30),
            )
        return
    output = CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        _id(12) if change == "output_id" else _id(11),
        _id(30),
        "pdf" if change == "output_kind" else REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND,
        100,
        "b" * 64,
    )
    payload = OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)
    if change is not None:
        with pytest.raises(ProtocolError):
            worker.prepare_outbound(
                MessageType.OPERATION_RESULT, payload, request_id=_id(103), operation_id=_id(30)
            )
    else:
        terminal = worker.prepare_outbound(
            MessageType.OPERATION_RESULT, payload, request_id=_id(103), operation_id=_id(30)
        )
        assert client.accept_inbound(terminal.encoded).payload == payload


@pytest.mark.parametrize("change", ["extra", "alias", "unauthenticated", "kind", "storage", "id"])
def test_protocol_rejects_extra_authority_and_unbound_inputs(change: str) -> None:
    payload = _payload()
    assert sanitization_binding(payload) == _id(11)
    if change == "extra":
        payload = replace(
            payload,
            parameters=payload.parameters + (TaggedScalar("path", ScalarValueType.INTEGER, 10),),
        )
    elif change == "alias":
        payload = replace(
            payload, parameters=(replace(payload.parameters[0], value=21), *payload.parameters[1:])
        )
    elif change == "unauthenticated":
        payload = replace(
            payload, inputs=(payload.inputs[0], replace(payload.inputs[1], expected_sha256=None))
        )
    elif change == "kind":
        payload = replace(
            payload, inputs=(payload.inputs[0], replace(payload.inputs[1], content_kind="jpeg"))
        )
    elif change == "id":
        payload = replace(
            payload,
            parameters=(
                *payload.parameters[:-1],
                replace(payload.parameters[-1], value=payload.inputs[1].object_id),
            ),
        )
    else:
        payload = replace(
            payload,
            requested_limits=replace(
                payload.requested_limits,
                temporary_storage_bytes=payload.requested_limits.output_bytes,
            ),
        )
    with pytest.raises(ProtocolError):
        sanitization_binding(payload)


def test_private_host_scope_is_not_advertised_by_default_or_ocr_scope() -> None:
    assert REDACTION_SANITIZATION_CAPABILITY not in _build_operation_registry().capabilities
    assert (
        REDACTION_SANITIZATION_CAPABILITY
        not in _build_operation_registry(WorkerOperationScope.QUALITY_OCR_REVIEW).capabilities
    )
    assert set(
        _build_operation_registry(WorkerOperationScope.REDACTION_SANITIZATION).capabilities
    ) == {NOOP_CAPABILITY, REDACTION_SANITIZATION_CAPABILITY}


def test_private_composition_rejects_subprocess_allowance(tmp_path: Path) -> None:
    with pytest.raises(WorkerClientError):
        composition.create_sanitization_worker_composition(
            _config(tmp_path), heavy_operations=HeavyOperationCoordinator(1)
        )


def test_composition_selects_private_scope_and_revokes_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def create_fake(*args: object, **kwargs: object) -> _FakeWorkerClient:
        kwargs.pop("operation_registry")
        kwargs.pop("resource_sampling_interval_ms")
        return _FakeWorkerClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(composition, "WorkerClient", create_fake)
    config = _config(tmp_path)
    with composition.create_sanitization_worker_composition(
        replace(
            config,
            resource_policy=replace(config.resource_policy, maximum_processes=1),
        ),
        heavy_operations=HeavyOperationCoordinator(1),
    ) as runtime:
        worker = cast(_FakeWorkerClient, runtime._worker)
        assert worker.required_capabilities == (REDACTION_SANITIZATION_CAPABILITY,)
        assert (
            cast(WindowsProcessLauncher, worker.launcher)._config.operation_scope
            is WorkerOperationScope.REDACTION_SANITIZATION
        )
        sanitizer = runtime.sanitizer
        buffer = _OutputBuffer(b"synthetic", 1024, [])
        candidate = UnverifiedSanitizationCandidate(buffer, report(request()))
        sanitizer._candidate = candidate
    assert sanitizer.revoked and runtime.closed
    assert candidate.closed and buffer.close_calls == 1
    assert worker.terminate_calls == 1


def test_candidate_close_failure_denies_new_work_and_composition_cleanup_can_retry(
    tmp_path: Path,
) -> None:
    class FailingBuffer(_OutputBuffer):
        attempts = 0

        def close(self) -> None:
            self.attempts += 1
            if self.attempts <= 2:
                raise OSError
            super().close()

    config = _config(tmp_path)
    worker = _FakeWorkerClient(
        object(),
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=(REDACTION_SANITIZATION_CAPABILITY,),
    )
    sanitizer = BrokeredRedactionSanitizer(
        cast(PdfExportWorkerCoordinator, worker),
        config.export_policy,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    buffer = FailingBuffer(b"synthetic", 1024, [])
    candidate = UnverifiedSanitizationCandidate(buffer, report(request()))
    sanitizer._candidate = candidate
    runtime = composition.SanitizationWorkerComposition(cast(WorkerClient, worker), sanitizer)
    with pytest.raises(OSError):
        candidate.close()
    with pytest.raises(PdfExportError, match="candidate_capacity"):
        sanitizer.sanitize(
            request(),
            cast(RetainedSanitizationSourceLease, object()),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    with pytest.raises(OSError):
        runtime.close()
    assert not _is_closed(candidate) and not _is_closed(runtime) and sanitizer.revoked
    assert worker.terminate_calls == 1
    runtime.close()
    assert _is_closed(candidate) and _is_closed(runtime) and buffer.close_calls == 1
    assert worker.terminate_calls == 2


@pytest.mark.parametrize("timed_out", [False, True])
def test_early_terminal_operation_consumes_all_four_authorities(
    timed_out: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    discarded: list[int] = []
    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._close_native_handle_no_raise", discarded.append
    )
    payload = _payload()
    event = Event()
    if not timed_out:
        event.set()
    context = OperationContext(
        _id(30),
        request().case_id,
        payload.requested_limits,
        event,
        lambda _: None,
        lambda: timed_out,
        payload.inputs,
        payload.parameters,
    )
    result = SanitizationOperationHandler(WindowsSanitizationOperationAdapter()).execute(context)
    assert result.outcome is (
        OperationOutcome.TIMED_OUT if timed_out else OperationOutcome.CANCELLED
    )
    assert not result.outputs
    assert set(discarded) == {20, 21, 22, 23}


def test_invalid_authenticated_plan_consumes_authorities_without_parsing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discarded: list[int] = []
    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._close_native_handle_no_raise",
        discarded.append,
    )

    def read_invalid(handle: int, **_kwargs: object) -> bytes:
        discarded.append(handle)
        return b"{}"

    monkeypatch.setattr(
        "paperworkshell.worker.redaction_sanitization_operation.read_pdf_export_plan_section",
        read_invalid,
    )
    payload = _payload()
    context = OperationContext(
        _id(30),
        request().case_id,
        payload.requested_limits,
        Event(),
        lambda _: None,
        lambda: False,
        payload.inputs,
        payload.parameters,
    )
    with pytest.raises(PdfExportError):
        SanitizationOperationHandler(WindowsSanitizationOperationAdapter()).execute(context)
    assert set(discarded) == {20, 21, 22, 23}


def test_low_disk_capacity_failure_publishes_nothing_and_consumes_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discarded: list[int] = []
    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._close_native_handle_no_raise",
        discarded.append,
    )

    def read_plan(handle: int, **_kwargs: object) -> bytes:
        discarded.append(handle)
        return encode_sanitization_plan(request())

    def no_space(_root: Path, _capacity: int) -> None:
        raise WorkerPrivateTempStorageError

    monkeypatch.setattr(
        "paperworkshell.worker.redaction_sanitization_operation.read_pdf_export_plan_section",
        read_plan,
    )
    monkeypatch.setattr(
        "paperworkshell.worker.redaction_sanitization_operation._materialize_worker_private_temp",
        lambda: tmp_path,
    )
    storage = cast(WorkerPrivateTempStorage, SimpleNamespace(require_capacity=no_space))
    payload = _payload()
    context = OperationContext(
        _id(30),
        request().case_id,
        payload.requested_limits,
        Event(),
        lambda _: None,
        lambda: False,
        payload.inputs,
        payload.parameters,
    )
    result = SanitizationOperationHandler(
        WindowsSanitizationOperationAdapter(private_temp_storage=storage)
    ).execute(context)
    assert result.outcome is OperationOutcome.FAILED and not result.outputs
    assert result.error is not None and result.error.category is ErrorCategory.RESOURCE_LIMIT
    assert set(discarded) == {20, 21, 22, 23}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "failure", [None, "source", "tamper", "report", "cancel", "timeout", "capability"]
)
def test_broker_authenticates_candidate_and_closes_all_failed_attempts(failure: str | None) -> None:
    base = _plan()
    intent = replace(request(), case_id=base.case_id)
    events: list[str] = []
    output = _OutputBuffer(b"synthetic-private-page", 2 * 1024 * 1024, events)
    worker = _Worker(base, output, events)
    worker.capabilities = (
        (REDACTION_SANITIZATION_CAPABILITY,)
        if failure != "capability"
        else (_Worker(_plan(), output, events).capabilities[0],)
    )
    worker.output_content_kind = REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND
    worker.progress_total = 1
    worker.progress_unit = ProgressUnit.STEPS
    source = SimpleNamespace(
        **{
            name: getattr(intent, name)
            for name in (
                "case_id",
                "source_id",
                "source_sha256",
                "source_byte_size",
                "source_page_count",
            )
        },
        handle=SimpleNamespace(value=101),
    )
    if failure == "source":
        source.source_sha256 = "f" * 64
    if failure in {"cancel", "timeout"}:
        worker.withhold_result = True
    if failure == "cancel":
        worker.cancel_return_override = _id(997)
    pipe = _ResultPipe(base, output, events)
    pipe.worker = worker

    def read_frame() -> bytes:
        assert worker.terminated
        assert worker.operation_id is not None and worker.output_object_id is not None
        value = replace(
            report(intent),
            output_sha256=hashlib.sha256(output.payload).hexdigest(),
            output_byte_size=len(output.payload),
        )
        if failure == "report":
            value = replace(value, request_sha256="f" * 64)
        if failure == "tamper":
            output.payload = b"X" + output.payload[1:]
        return encode_sanitization_report(
            SanitizationWorkerReport(worker.operation_id, worker.output_object_id, value)
        )

    pipe.read_complete_frame = read_frame  # type: ignore[method-assign]
    policy = PdfExportBrokerPolicy(
        replace(_policy().limits, temporary_storage_bytes=10 * 1024 * 1024)
    )
    sanitizer = BrokeredRedactionSanitizer(
        cast(PdfExportWorkerCoordinator, worker),
        policy,
        heavy_operations=HeavyOperationCoordinator(1),
        pipe_factory=lambda: pipe,
        plan_buffer_factory=lambda raw: _PlanBuffer(raw, events),
        output_buffer_factory=lambda _: output,
    )

    def cancel() -> bool:
        return failure == "cancel" and worker.payload is not None

    def deadline() -> bool:
        return failure == "timeout" and worker.payload is not None

    if failure is not None:
        with pytest.raises(PdfExportError):
            sanitizer.sanitize(
                intent,
                cast(RetainedSanitizationSourceLease, source),
                deadline=deadline,
                cancellation=cancel,
            )
        if failure != "source":
            assert output.close_calls == 1 and worker.terminated
    else:
        with sanitizer.sanitize(
            intent,
            cast(RetainedSanitizationSourceLease, source),
            deadline=deadline,
            cancellation=cancel,
        ) as candidate:
            assert candidate.report.status == "awaiting_verification"
            with pytest.raises(PdfExportError, match="candidate_capacity"):
                sanitizer.sanitize(
                    intent,
                    cast(RetainedSanitizationSourceLease, source),
                    deadline=deadline,
                    cancellation=cancel,
                )
            assert candidate.read(3) + candidate.read(65536) == output.payload
            assert candidate.read(1) == b""
        assert output.close_calls == 1 and worker.terminated
        worker.payload = None
        worker.terminal_result = None
        worker.ready_queries = 0
        worker.result_queries = 0
        worker.terminated = False
        with sanitizer.sanitize(
            intent,
            cast(RetainedSanitizationSourceLease, source),
            deadline=deadline,
            cancellation=cancel,
        ) as retry:
            assert retry.report == candidate.report
        assert output.close_calls == 2
        assert events.index("worker.terminate") < events.index("output.iter.1")


def test_terminal_result_waits_for_pending_cancellation_acknowledgement() -> None:
    class DelayedAckWorker(_Worker):
        acknowledgement_received = False
        cancellation_pumps = 0

        def query(self) -> WorkerClientSnapshot:
            snapshot = super().query()
            if self.cancel_calls and snapshot.last_result is not None:
                return replace(
                    snapshot,
                    phase=(
                        WorkerLifecyclePhase.READY
                        if self.acknowledgement_received
                        else WorkerLifecyclePhase.CANCELLING
                    ),
                    last_cancellation_acknowledgement_ms=(
                        1 if self.acknowledgement_received else None
                    ),
                )
            return snapshot

        def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
            snapshot = super().pump(timeout_seconds)
            if self.cancel_calls:
                self.cancellation_pumps += 1
                # The first post-cancel pump delivers only the terminal result.
                # The second delivers the correlated ALREADY_TERMINAL ack.
                if self.cancellation_pumps >= 2:
                    self.acknowledgement_received = True
            return snapshot

    events: list[str] = []
    worker = DelayedAckWorker(_plan(), _OutputBuffer(b"synthetic", 1024, events), events)
    worker.progress_total = 1
    worker.progress_unit = ProgressUnit.STEPS
    worker.cancel_returns_success = True
    generation = worker.start(worker.plan.case_id)
    worker.start_operation(_payload(), operation_id=_id(30))
    sanitizer = BrokeredRedactionSanitizer(
        cast(PdfExportWorkerCoordinator, worker),
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
    )
    with pytest.raises(PdfExportError) as caught:
        sanitizer._wait_result(
            _id(30),
            generation,
            deadline=lambda: False,
            cancellation=lambda: True,
            progress=lambda _: None,
        )
    assert caught.value.code is PdfExportErrorCode.CANCELLED
    assert worker.acknowledgement_received
    assert worker.query().last_cancellation_acknowledgement_ms == 1
    assert worker.cancellation_pumps == 2
