"""Private reconstruction transport rejects escalation and preserves closed gating."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.worker import redaction_reconstruction_composition as composition
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.host import _build_operation_registry
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.pdf_export_broker import (
    PdfExportBrokerPolicy,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_thumbnail_broker import CommittedPdfPageLease
from paperworkshell.worker.private_temp import (
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    REDACTION_RECONSTRUCTION_CAPABILITY,
    REDACTION_RECONSTRUCTION_OUTPUT_CONTENT_KIND,
    REDACTION_RECONSTRUCTION_PLAN_CONTENT_KIND,
    CaseObjectReference,
    ErrorCategory,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from paperworkshell.worker.protocol.redaction_reconstruction import reconstruction_binding
from paperworkshell.worker.redaction_reconstruction_broker import (
    BrokeredPageReconstructor,
    UnverifiedReconstructionCandidate,
)
from paperworkshell.worker.redaction_reconstruction_operation import (
    ReconstructionOperationHandler,
    WindowsReconstructionOperationAdapter,
)
from paperworkshell.worker.redaction_reconstruction_wire import (
    ReconstructionWorkerReport,
    decode_reconstruction_plan,
    decode_reconstruction_report,
    encode_reconstruction_plan,
    encode_reconstruction_report,
)
from tests.unit.test_redaction_reconstruction_models import _id, report, request
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
    value: UnverifiedReconstructionCandidate | composition.ReconstructionWorkerComposition,
) -> bool:
    return value.closed


def _payload() -> OperationStartPayload:
    intent = request()
    raw = encode_reconstruction_plan(intent)
    return OperationStartPayload(
        REDACTION_RECONSTRUCTION_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _id(10),
                None,
                REDACTION_RECONSTRUCTION_PLAN_CONTENT_KIND,
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
    raw = encode_reconstruction_plan(value)
    assert decode_reconstruction_plan(raw) == value
    result = ReconstructionWorkerReport(_id(20), _id(21), report(value))
    assert decode_reconstruction_report(encode_reconstruction_report(result)) == result
    for invalid in (
        b'{"request_version":1,"request_version":1}',
        raw.replace(b'"request_version":1', b'"request_version":2'),
        b" " + raw,
        b"x" * (8 * 1024 * 1024),
        b'{"x":NaN}',
    ):
        with pytest.raises(PdfExportError):
            decode_reconstruction_plan(invalid)


@pytest.mark.parametrize("change", ["extra", "alias", "unauthenticated", "kind", "storage", "id"])
def test_protocol_rejects_extra_authority_and_unbound_inputs(change: str) -> None:
    payload = _payload()
    assert reconstruction_binding(payload) == _id(11)
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
        reconstruction_binding(payload)


def test_private_host_scope_is_not_advertised_by_default_or_ocr_scope() -> None:
    assert REDACTION_RECONSTRUCTION_CAPABILITY not in _build_operation_registry().capabilities
    assert (
        REDACTION_RECONSTRUCTION_CAPABILITY
        not in _build_operation_registry(WorkerOperationScope.QUALITY_OCR_REVIEW).capabilities
    )
    assert (
        REDACTION_RECONSTRUCTION_CAPABILITY
        in _build_operation_registry(WorkerOperationScope.REDACTION_RECONSTRUCTION).capabilities
    )


def test_composition_selects_private_scope_and_revokes_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def create_fake(*args: object, **kwargs: object) -> _FakeWorkerClient:
        kwargs.pop("operation_registry")
        kwargs.pop("resource_sampling_interval_ms")
        return _FakeWorkerClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(composition, "WorkerClient", create_fake)
    with composition.create_reconstruction_worker_composition(
        _config(tmp_path), heavy_operations=HeavyOperationCoordinator(1)
    ) as runtime:
        worker = cast(_FakeWorkerClient, runtime._worker)
        assert worker.required_capabilities == (REDACTION_RECONSTRUCTION_CAPABILITY,)
        assert (
            cast(WindowsProcessLauncher, worker.launcher)._config.operation_scope
            is WorkerOperationScope.REDACTION_RECONSTRUCTION
        )
        reconstructor = runtime.reconstructor
        buffer = _OutputBuffer(b"synthetic", 1024, [])
        candidate = UnverifiedReconstructionCandidate(buffer, report(request()))
        reconstructor._candidate = candidate
    assert reconstructor.revoked and runtime.closed
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
        required_capabilities=(REDACTION_RECONSTRUCTION_CAPABILITY,),
    )
    reconstructor = BrokeredPageReconstructor(
        cast(PdfExportWorkerCoordinator, worker),
        config.export_policy,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    buffer = FailingBuffer(b"synthetic", 1024, [])
    candidate = UnverifiedReconstructionCandidate(buffer, report(request()))
    reconstructor._candidate = candidate
    runtime = composition.ReconstructionWorkerComposition(cast(WorkerClient, worker), reconstructor)
    with pytest.raises(OSError):
        candidate.close()
    with pytest.raises(PdfExportError, match="candidate_capacity"):
        reconstructor.reconstruct(
            request(),
            cast(CommittedPdfPageLease, object()),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    with pytest.raises(OSError):
        runtime.close()
    assert not _is_closed(candidate) and not _is_closed(runtime) and reconstructor.revoked
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
    result = ReconstructionOperationHandler(WindowsReconstructionOperationAdapter()).execute(
        context
    )
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
        "paperworkshell.worker.redaction_reconstruction_operation.read_pdf_export_plan_section",
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
        ReconstructionOperationHandler(WindowsReconstructionOperationAdapter()).execute(context)
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
        return encode_reconstruction_plan(request())

    def no_space(_root: Path, _capacity: int) -> None:
        raise WorkerPrivateTempStorageError

    monkeypatch.setattr(
        "paperworkshell.worker.redaction_reconstruction_operation.read_pdf_export_plan_section",
        read_plan,
    )
    monkeypatch.setattr(
        "paperworkshell.worker.redaction_reconstruction_operation._materialize_worker_private_temp",
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
    result = ReconstructionOperationHandler(
        WindowsReconstructionOperationAdapter(private_temp_storage=storage)
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
        (REDACTION_RECONSTRUCTION_CAPABILITY,)
        if failure != "capability"
        else (_Worker(_plan(), output, events).capabilities[0],)
    )
    worker.output_content_kind = REDACTION_RECONSTRUCTION_OUTPUT_CONTENT_KIND
    worker.progress_total = 1
    source = SimpleNamespace(
        **{
            name: getattr(intent, name)
            for name in (
                "case_id",
                "source_id",
                "page_id",
                "source_sha256",
                "source_byte_size",
                "source_page_index",
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
        return encode_reconstruction_report(
            ReconstructionWorkerReport(worker.operation_id, worker.output_object_id, value)
        )

    pipe.read_complete_frame = read_frame  # type: ignore[method-assign]
    policy = PdfExportBrokerPolicy(
        replace(_policy().limits, temporary_storage_bytes=10 * 1024 * 1024)
    )
    reconstructor = BrokeredPageReconstructor(
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
            reconstructor.reconstruct(
                intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
            )
        if failure != "source":
            assert output.close_calls == 1 and worker.terminated
    else:
        with reconstructor.reconstruct(
            intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
        ) as candidate:
            assert candidate.report.status == "awaiting_verification"
            with pytest.raises(PdfExportError, match="candidate_capacity"):
                reconstructor.reconstruct(
                    intent,
                    cast(CommittedPdfPageLease, source),
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
        with reconstructor.reconstruct(
            intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
        ) as retry:
            assert retry.report == candidate.report
        assert output.close_calls == 2
        assert events.index("worker.terminate") < events.index("output.iter.1")
