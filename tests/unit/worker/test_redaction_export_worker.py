"""Composed private export evidence cannot be forged into publication authority."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_export_candidate import RedactionExportWorkerReport
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationInventory,
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
)
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    run_verification_methods,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.host import _build_operation_registry
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.pdf_export_broker import (
    PdfExportBrokerPolicy,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_thumbnail_broker import CommittedPdfPageLease
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    REDACTION_EXPORT_CAPABILITY,
    REDACTION_EXPORT_OUTPUT_CONTENT_KIND,
    REDACTION_EXPORT_PLAN_CONTENT_KIND,
    CaseObjectReference,
    Direction,
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
from paperworkshell.worker.protocol.redaction_export import (
    redaction_export_binding,
)
from paperworkshell.worker.redaction_export_broker import (
    BrokeredRedactionExporter,
    PreparedRedactionCandidate,
)
from paperworkshell.worker.redaction_export_composition import RedactionExportOperationRegistry
from paperworkshell.worker.redaction_export_operation import (
    RedactionExportOperationHandler,
    WindowsRedactionExportOperationAdapter,
)
from paperworkshell.worker.redaction_export_wire import (
    decode_redaction_export_plan,
    decode_redaction_export_report,
    encode_redaction_export_plan,
    encode_redaction_export_report,
)
from tests.unit.test_redaction_reconstruction_models import _id, request
from tests.unit.test_redaction_reconstruction_models import report as reconstruction_report
from tests.unit.worker.test_pdf_export_broker import (
    _OutputBuffer,
    _plan,
    _PlanBuffer,
    _policy,
    _ResultPipe,
    _Worker,
)


def report(
    intent: RedactionReconstructionRequest,
    operation_id: object = None,
    output_id: object = None,
    payload: bytes = b"synthetic",
) -> RedactionExportWorkerReport:
    reconstruction = reconstruction_report(intent)
    sanitization_request = RedactionSanitizationRequest(
        intent.case_id, _id(90), reconstruction.output_sha256, reconstruction.output_byte_size, 1
    )
    sanitization = RedactionSanitizationReport(
        sanitization_request.fingerprint,
        reconstruction.output_sha256,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        1,
        RedactionSanitizationInventory(),
        RedactionSanitizationInventory(),
    )
    verification_request = RedactionVerificationRequest(
        sanitization.output_sha256,
        len(payload),
        reconstruction.output_sha256,
        reconstruction.output_byte_size,
        1,
        (0,),
        intent.fingerprint,
    )
    return RedactionExportWorkerReport(
        _id(30) if operation_id is None else operation_id,  # type: ignore[arg-type]
        _id(11) if output_id is None else output_id,  # type: ignore[arg-type]
        reconstruction,
        sanitization_request,
        sanitization,
        verification_request,
        run_verification_methods(verification_request, {}),
    )


def _payload() -> OperationStartPayload:
    intent = request()
    raw = encode_redaction_export_plan(intent)
    return OperationStartPayload(
        REDACTION_EXPORT_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _id(10),
                None,
                REDACTION_EXPORT_PLAN_CONTENT_KIND,
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


def test_wire_preserves_exact_chain_and_rejects_changed_bindings() -> None:
    intent = request()
    assert decode_redaction_export_plan(encode_redaction_export_plan(intent)) == intent
    result = report(intent)
    raw = encode_redaction_export_report(result)
    assert decode_redaction_export_report(raw) == result
    for before, after in (
        (b'"worker_report_version":1', b'"worker_report_version":2'),
        (b'"affected_pages":[0]', b'"affected_pages":[]'),
        (b'"reference_byte_size":400', b'"reference_byte_size":401'),
    ):
        with pytest.raises(PdfExportError):
            decode_redaction_export_report(raw.replace(before, after))
    with pytest.raises(PdfExportError):
        decode_redaction_export_plan(b'{"x":1,"x":2}')


@pytest.mark.parametrize("change", ["extra", "alias", "unauthenticated", "kind", "storage", "id"])
def test_protocol_rejects_extra_authority_and_unbound_inputs(change: str) -> None:
    payload = _payload()
    assert redaction_export_binding(payload) == _id(11)
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
        redaction_export_binding(payload)


def test_private_scope_remains_absent_from_ordinary_workers() -> None:
    assert REDACTION_EXPORT_CAPABILITY not in _build_operation_registry().capabilities
    assert set(_build_operation_registry(WorkerOperationScope.REDACTION_EXPORT).capabilities) == {
        NOOP_CAPABILITY,
        REDACTION_EXPORT_CAPABILITY,
    }


@pytest.mark.parametrize("timed_out", [False, True])
def test_terminal_operation_consumes_all_handle_authorities(
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
    result = RedactionExportOperationHandler(WindowsRedactionExportOperationAdapter()).execute(
        context
    )
    assert result.outcome is (
        OperationOutcome.TIMED_OUT if timed_out else OperationOutcome.CANCELLED
    )
    assert not result.outputs and set(discarded) == {20, 21, 22, 23}


@pytest.mark.parametrize(
    "failure",
    [None, "source", "tamper", "report", "cancel", "timeout", "capability", "external_pass"],
)
def test_broker_binds_private_candidate_and_retries_without_reusing_authority(
    failure: str | None,
) -> None:
    base = _plan()
    intent = replace(request(), case_id=base.case_id)
    events: list[str] = []
    output = _OutputBuffer(b"synthetic-private-page", 2 * 1024 * 1024, events)
    worker = _Worker(base, output, events)
    worker.capabilities = (
        (REDACTION_EXPORT_CAPABILITY,) if failure != "capability" else worker.capabilities
    )
    worker.output_content_kind = REDACTION_EXPORT_OUTPUT_CONTENT_KIND
    worker.progress_total = 1
    worker.progress_unit = ProgressUnit.STEPS
    source = SimpleNamespace(
        **{
            name: getattr(intent, name)
            for name in (
                "case_id",
                "source_id",
                "page_id",
                "source_page_index",
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
    pipe = _ResultPipe(base, output, events)
    pipe.worker = worker

    def read_frame() -> bytes:
        assert worker.terminated
        value = report(intent, worker.operation_id, worker.output_object_id, output.payload)
        if failure == "report":
            object.__setattr__(value.reconstruction, "request_sha256", "f" * 64)
        if failure == "external_pass":
            value = replace(
                value,
                verification=replace(
                    value.verification,
                    methods=tuple(
                        VerificationMethodResult(
                            item.method, VerificationStatus.PASS, VerificationCode.CLEAR
                        )
                        if item.method in {VerificationMethod.OCR, VerificationMethod.VIEWERS}
                        else item
                        for item in value.verification.methods
                    ),
                ),
            )
        raw = encode_redaction_export_report(value)
        if failure == "tamper":
            output.payload = b"X" + output.payload[1:]
        return raw

    pipe.read_complete_frame = read_frame  # type: ignore[method-assign]
    broker = BrokeredRedactionExporter(
        cast(PdfExportWorkerCoordinator, worker),
        PdfExportBrokerPolicy(replace(_policy().limits, temporary_storage_bytes=10 * 1024 * 1024)),
        heavy_operations=HeavyOperationCoordinator(1),
        pipe_factory=lambda: pipe,
        plan_buffer_factory=lambda raw: _PlanBuffer(raw, events),
        output_buffer_factory=lambda _: output,
    )

    def deadline() -> bool:
        return failure == "timeout" and worker.payload is not None

    def cancellation() -> bool:
        return failure == "cancel" and worker.payload is not None

    if failure is not None:
        with pytest.raises(PdfExportError):
            broker.prepare(
                intent,
                cast(CommittedPdfPageLease, source),
                deadline=deadline,
                cancellation=cancellation,
            )
        if failure != "source":
            assert output.close_calls == 1 and worker.terminated
        return
    with broker.prepare(
        intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancellation
    ) as candidate:
        assert not candidate.report.verification.release_ready
        broker.validate_candidate(candidate, intent, deadline=deadline, cancellation=cancellation)
        with pytest.raises(PdfExportError):
            broker.validate_candidate(
                object(), intent, deadline=deadline, cancellation=cancellation
            )
        original_payload = output.payload
        output.payload = b"X" + original_payload[1:]
        with pytest.raises(PdfExportError):
            broker.validate_candidate(
                candidate, intent, deadline=deadline, cancellation=cancellation
            )
        output.payload = original_payload
        assert candidate.read(3) + candidate.read(65536) == output.payload
        with pytest.raises(PdfExportError):
            broker.validate_candidate(
                candidate, intent, deadline=deadline, cancellation=cancellation
            )
    assert output.close_calls == 1


def test_candidate_constructor_requires_broker_authority() -> None:
    with pytest.raises(PdfExportError):
        PreparedRedactionCandidate(
            _OutputBuffer(b"synthetic", 1024, []), report(request()), _token=object()
        )


def _started_peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_id(100), _id(101), request().case_id, SessionCapability("d" * 64))
    registry = RedactionExportOperationRegistry()
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER, operation_registry=registry)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT, operation_registry=registry)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (REDACTION_EXPORT_CAPABILITY,)),
        request_id=_id(102),
        operation_id=None,
    )
    worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (REDACTION_EXPORT_CAPABILITY,), MAX_MESSAGE_BYTES),
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
            REDACTION_EXPORT_CAPABILITY.capability_id, 1, payload.requested_limits
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
        "pdf" if change == "output_kind" else REDACTION_EXPORT_OUTPUT_CONTENT_KIND,
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


def test_scratch_ancestry_permission_failure_has_closed_content_free_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO
    from pathlib import Path

    from paperworkshell.domain.redaction_verification import VerificationCode
    from paperworkshell.pdf.redaction_verification import verify_redaction
    from tests.unit.test_redaction_sanitization_adapter import _Control, _limits
    from tests.unit.test_redaction_verification import _candidate, _verification_request

    content = _candidate(tmp_path)

    def denied(_path: Path) -> None:
        raise PermissionError("PRIVATE-SCRATCH-PATH-CANARY")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", denied)
        result = verify_redaction(
            BytesIO(content),
            BytesIO(content),
            _verification_request(content, content),
            tmp_path,
            _limits(),
            _Control(),
        )
    assert all(item.code is VerificationCode.SCRATCH_ACCESS_DENIED for item in result.methods)
    assert "PRIVATE-SCRATCH-PATH-CANARY" not in repr(result.to_record())
    (tmp_path / "candidate.pdf").unlink()


def test_sealed_scratch_authority_rechecks_all_method_and_cleanup_boundaries(
    tmp_path: Path,
) -> None:
    from io import BytesIO

    from paperworkshell.pdf.redaction_verification import (
        _bind_verification_scratch,
        _VerificationScratchAuthority,
        verify_redaction,
    )
    from tests.unit.test_redaction_sanitization_adapter import _Control, _limits
    from tests.unit.test_redaction_verification import _candidate, _verification_request

    content = _candidate(tmp_path)
    checks: list[int] = []

    def validate() -> None:
        checks.append(1)

    with pytest.raises(ValueError):
        _VerificationScratchAuthority(tmp_path, validate, object())
    authority = _bind_verification_scratch(tmp_path, validate)
    with pytest.raises(ValueError):
        authority.require_directory(tmp_path / "other")
    result = verify_redaction(
        BytesIO(content),
        BytesIO(content),
        _verification_request(content, content),
        tmp_path,
        _limits(),
        _Control(),
        _scratch_authority=authority,
    )
    assert result.automated_passed
    assert len(checks) >= 7
    (tmp_path / "candidate.pdf").unlink()


def test_scratch_pin_rejects_path_replacement_and_expires_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperworkshell.worker import redaction_export_scratch as scratch

    directory = tmp_path / "operation"
    directory.mkdir()
    current = [b"original"]
    closed: list[int] = []
    removed: list[int] = []
    monkeypatch.setattr(
        scratch, "_create_worker_private_operation_temp", lambda root: (directory, 10)
    )
    monkeypatch.setattr(
        scratch, "_identity", lambda handle: (1, b"original" if handle == 10 else current[0])
    )
    monkeypatch.setattr(scratch, "_open_identity_probe", lambda path: 20)

    def close(handle: int) -> bool:
        closed.append(handle)
        return True

    def remove(path: Path, handle: int) -> bool:
        removed.append(handle)
        return True

    monkeypatch.setattr(scratch, "_close_native_handle_checked", close)
    monkeypatch.setattr(scratch, "_remove_pinned_operation_temp", remove)
    pinned = scratch.PinnedVerificationScratch(tmp_path)
    pinned.require_current()
    current[0] = b"replacement"
    with pytest.raises(PdfExportError):
        pinned.require_current()
    with pytest.raises(PdfExportError):
        pinned.__exit__()
    assert removed == [] and 10 in closed
    with pytest.raises(PdfExportError):
        pinned.authority.require_directory(directory)
    directory.rmdir()


@pytest.mark.parametrize("failure", ["constructor", "closed_pin", "cleanup", "close_failure"])
def test_scratch_authority_failure_closes_or_defers_without_uncertain_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from paperworkshell.domain.pdf_export import PdfExportErrorCode
    from paperworkshell.worker import redaction_export_scratch as scratch

    directory = tmp_path / "operation"
    directory.mkdir()
    closed: list[int] = []
    removed: list[int] = []
    damaged = [failure in {"constructor", "close_failure"}]

    def identity(handle: int) -> tuple[int, bytes]:
        if damaged[0] and handle == 10:
            raise PdfExportError(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_pin")
        return (1, b"identity")

    def close(handle: int) -> bool:
        closed.append(handle)
        return failure != "close_failure"

    def remove(path: Path, handle: int) -> bool:
        removed.append(handle)
        return False

    monkeypatch.setattr(
        scratch, "_create_worker_private_operation_temp", lambda root: (directory, 10)
    )
    monkeypatch.setattr(scratch, "_identity", identity)
    monkeypatch.setattr(scratch, "_open_identity_probe", lambda path: 20)
    monkeypatch.setattr(scratch, "_close_native_handle_checked", close)
    monkeypatch.setattr(scratch, "_remove_pinned_operation_temp", remove)
    if failure in {"constructor", "close_failure"}:
        with pytest.raises(PdfExportError):
            scratch.PinnedVerificationScratch(tmp_path)
        assert closed == [10] and removed == []
    else:
        pinned = scratch.PinnedVerificationScratch(tmp_path)
        with pytest.raises(ValueError):
            pinned.authority.require_directory(directory / "other")
        damaged[0] = failure == "closed_pin"
        with pytest.raises(PdfExportError):
            pinned.__exit__()
        assert removed == ([10] if failure == "cleanup" else [])
        settled = (tuple(closed), tuple(removed))
        pinned.__exit__()
        with pytest.raises(PdfExportError):
            pinned.__enter__()
        assert (tuple(closed), tuple(removed)) == settled
        with pytest.raises(PdfExportError):
            pinned.authority.require_directory(directory)
    directory.rmdir()


def test_new_composed_operation_low_disk_fails_before_parsing_and_consumes_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperworkshell.worker import redaction_export_operation as operation
    from paperworkshell.worker.private_temp import (
        WorkerPrivateTempStorage,
        WorkerPrivateTempStorageError,
    )
    from paperworkshell.worker.protocol.models import ErrorCategory

    consumed: list[int] = []
    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._close_native_handle_no_raise", consumed.append
    )

    def read_plan(handle: int, **kwargs: object) -> bytes:
        consumed.append(handle)
        return encode_redaction_export_plan(request())

    def no_space(root: Path, amount: int) -> None:
        assert root == tmp_path and amount == _payload().requested_limits.temporary_storage_bytes
        raise WorkerPrivateTempStorageError

    monkeypatch.setattr(operation, "read_pdf_export_plan_section", read_plan)
    monkeypatch.setattr(operation, "_materialize_worker_private_temp", lambda: tmp_path)
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
    result = RedactionExportOperationHandler(
        WindowsRedactionExportOperationAdapter(private_temp_storage=storage)
    ).execute(context)
    assert result.outcome is OperationOutcome.FAILED and not result.outputs
    assert result.error is not None and result.error.category is ErrorCategory.RESOURCE_LIMIT
    assert set(consumed) == {20, 21, 22, 23}
    assert not list(tmp_path.iterdir())


def test_scratch_exit_is_idempotent_and_reentry_never_recloses_consumed_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperworkshell.worker import redaction_export_scratch as scratch

    directory = tmp_path / "operation"
    directory.mkdir()
    removed: list[int] = []
    closed: list[int] = []

    def close(handle: int) -> bool:
        closed.append(handle)
        return True

    def remove(path: Path, handle: int) -> bool:
        removed.append(handle)
        path.rmdir()
        return True

    monkeypatch.setattr(
        scratch, "_create_worker_private_operation_temp", lambda root: (directory, 10)
    )
    monkeypatch.setattr(scratch, "_identity", lambda handle: (1, b"identity"))
    monkeypatch.setattr(scratch, "_open_identity_probe", lambda path: 20)
    monkeypatch.setattr(scratch, "_close_native_handle_checked", close)
    monkeypatch.setattr(scratch, "_remove_pinned_operation_temp", remove)
    pinned = scratch.PinnedVerificationScratch(tmp_path)
    pinned.__exit__()
    settled = tuple(closed)
    pinned.__exit__()
    pinned.__exit__()
    with pytest.raises(PdfExportError):
        pinned.__enter__()
    assert removed == [10] and tuple(closed) == settled and pinned._pin == 0
    assert not directory.exists()
