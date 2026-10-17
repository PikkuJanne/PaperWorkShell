"""Private masked-page OCR rejects escalation and preserves closed gating."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_ocr import RedactionOcrReport, RedactionOcrRequest
from paperworkshell.worker import redaction_ocr_composition as composition
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission
from paperworkshell.worker.desktop_composition import PdfExportWorkerCompositionConfig
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
    NOOP_CAPABILITY,
    REDACTION_OCR_CAPABILITY,
    REDACTION_OCR_OUTPUT_CONTENT_KIND,
    REDACTION_OCR_PLAN_CONTENT_KIND,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from paperworkshell.worker.protocol.redaction_ocr import (
    redaction_ocr_binding,
    validate_redaction_ocr_result,
)
from paperworkshell.worker.redaction_ocr_broker import (
    BrokeredRedactionOcrProcessor,
    UnverifiedRedactionOcrCandidate,
)
from paperworkshell.worker.redaction_ocr_operation import (
    RedactionOcrOperationHandler,
    RedactionOcrRegenerator,
    WindowsRedactionOcrOperationAdapter,
    build_redaction_ocr_operation_handler,
)
from paperworkshell.worker.redaction_ocr_wire import (
    RedactionOcrWorkerReport,
    decode_redaction_ocr_plan,
    decode_redaction_ocr_report,
    encode_redaction_ocr_plan,
    encode_redaction_ocr_report,
)
from tests.unit.test_redaction_reconstruction_models import _id
from tests.unit.test_redaction_reconstruction_models import report as reconstruction_report
from tests.unit.test_redaction_reconstruction_models import request as reconstruction_request
from tests.unit.worker.test_ocr_composition import _paths
from tests.unit.worker.test_pdf_export_broker import (
    _OutputBuffer,
    _plan,
    _PlanBuffer,
    _policy,
    _ResultPipe,
    _Worker,
)
from tests.unit.worker.test_pdf_export_composition import _config as pdf_config
from tests.unit.worker.test_pdf_export_composition import _FakeWorkerClient


@pytest.fixture(autouse=True)
def _synthetic_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    def admit(executable: Path, tessdata_root: Path) -> TesseractRuntimeAdmission:
        root = executable.parent.parent
        return TesseractRuntimeAdmission(
            root=root,
            executable=executable,
            tessdata_root=tessdata_root,
            resources=(root, executable.parent, executable, root / "share", tessdata_root),
        )

    monkeypatch.setattr(
        "paperworkshell.worker.client._windows.admit_packaged_tesseract_runtime", admit
    )


def _config(tmp_path: Path) -> PdfExportWorkerCompositionConfig:
    return replace(pdf_config(tmp_path), timeout_policy=WorkerTimeoutPolicy(20.0, 2.0, 10.0))


def request() -> RedactionOcrRequest:
    return RedactionOcrRequest(reconstruction_request())


def report(value: RedactionOcrRequest) -> RedactionOcrReport:
    return RedactionOcrReport(
        request_sha256=value.fingerprint,
        reconstruction=reconstruction_report(value.reconstruction),
        output_sha256="b" * 64,
        output_byte_size=400,
        language_set_id=value.language_set_id,
        ocrmypdf_version="17.10.0",
        tesseract_version="5.5.3",
    )


def _adapter(**kwargs: object) -> WindowsRedactionOcrOperationAdapter:
    return WindowsRedactionOcrOperationAdapter(
        regenerator=cast(RedactionOcrRegenerator, object()),
        **kwargs,  # type: ignore[arg-type]
    )


def _is_closed(
    value: UnverifiedRedactionOcrCandidate | composition.RedactionOcrWorkerComposition,
) -> bool:
    return value.closed


def _payload() -> OperationStartPayload:
    intent = request()
    raw = encode_redaction_ocr_plan(intent)
    return OperationStartPayload(
        REDACTION_OCR_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _id(10),
                None,
                REDACTION_OCR_PLAN_CONTENT_KIND,
                len(raw),
                hashlib.sha256(raw).hexdigest(),
            ),
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                intent.reconstruction.source_id,
                None,
                "pdf",
                intent.reconstruction.source_byte_size,
                intent.reconstruction.source_sha256,
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
    raw = encode_redaction_ocr_plan(value)
    assert decode_redaction_ocr_plan(raw) == value
    result = RedactionOcrWorkerReport(_id(20), _id(21), report(value))
    assert decode_redaction_ocr_report(encode_redaction_ocr_report(result)) == result
    for invalid in (
        b'{"request_version":1,"request_version":1}',
        raw.replace(b'"request_version":1', b'"request_version":2'),
        b" " + raw,
        b"x" * (8 * 1024 * 1024),
        b'{"x":NaN}',
    ):
        with pytest.raises(PdfExportError):
            decode_redaction_ocr_plan(invalid)


@pytest.mark.parametrize("change", ["extra", "alias", "unauthenticated", "kind", "storage", "id"])
def test_protocol_rejects_extra_authority_and_unbound_inputs(change: str) -> None:
    payload = _payload()
    assert redaction_ocr_binding(payload) == _id(11)
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
        redaction_ocr_binding(payload)


def test_private_host_scope_is_not_advertised_by_default_or_ocr_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.redaction_ocr_operation.build_redaction_ocr_operation_handler",
        lambda: RedactionOcrOperationHandler(_adapter()),
    )
    assert REDACTION_OCR_CAPABILITY not in _build_operation_registry().capabilities
    assert (
        REDACTION_OCR_CAPABILITY
        not in _build_operation_registry(WorkerOperationScope.QUALITY_OCR_REVIEW).capabilities
    )
    assert _build_operation_registry(WorkerOperationScope.REDACTION_OCR).capabilities == (
        NOOP_CAPABILITY,
        REDACTION_OCR_CAPABILITY,
    )


def test_composition_selects_private_scope_and_revokes_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def create_fake(*args: object, **kwargs: object) -> _FakeWorkerClient:
        kwargs.pop("operation_registry")
        kwargs.pop("resource_sampling_interval_ms")
        return _FakeWorkerClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(composition, "WorkerClient", create_fake)
    with composition.create_redaction_ocr_worker_composition(
        _config(tmp_path),
        heavy_operations=HeavyOperationCoordinator(1),
        tesseract_executable=_paths(tmp_path / "ocr")["tesseract_executable"],
        tessdata_root=tmp_path / "ocr" / "tesseract" / "share" / "tessdata",
    ) as runtime:
        worker = cast(_FakeWorkerClient, runtime._worker)
        assert worker.required_capabilities == (REDACTION_OCR_CAPABILITY,)
        assert (
            cast(WindowsProcessLauncher, worker.launcher)._config.operation_scope
            is WorkerOperationScope.REDACTION_OCR
        )
        reconstructor = runtime.processor
        buffer = _OutputBuffer(b"synthetic", 1024, [])
        candidate = UnverifiedRedactionOcrCandidate(buffer, report(request()))
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
        required_capabilities=(REDACTION_OCR_CAPABILITY,),
    )
    reconstructor = BrokeredRedactionOcrProcessor(
        cast(PdfExportWorkerCoordinator, worker),
        config.export_policy,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    buffer = FailingBuffer(b"synthetic", 1024, [])
    candidate = UnverifiedRedactionOcrCandidate(buffer, report(request()))
    reconstructor._candidate = candidate
    runtime = composition.RedactionOcrWorkerComposition(cast(WorkerClient, worker), reconstructor)
    with pytest.raises(OSError):
        candidate.close()
    with pytest.raises(PdfExportError, match="candidate_capacity"):
        reconstructor.regenerate(
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
        request().reconstruction.case_id,
        payload.requested_limits,
        event,
        lambda _: None,
        lambda: timed_out,
        payload.inputs,
        payload.parameters,
    )
    result = RedactionOcrOperationHandler(_adapter()).execute(context)
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
        "paperworkshell.worker.redaction_ocr_operation.read_pdf_export_plan_section",
        read_invalid,
    )
    payload = _payload()
    context = OperationContext(
        _id(30),
        request().reconstruction.case_id,
        payload.requested_limits,
        Event(),
        lambda _: None,
        lambda: False,
        payload.inputs,
        payload.parameters,
    )
    with pytest.raises(PdfExportError):
        RedactionOcrOperationHandler(_adapter()).execute(context)
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
        return encode_redaction_ocr_plan(request())

    def no_space(_root: Path, _capacity: int) -> None:
        raise WorkerPrivateTempStorageError

    monkeypatch.setattr(
        "paperworkshell.worker.redaction_ocr_operation.read_pdf_export_plan_section",
        read_plan,
    )
    monkeypatch.setattr(
        "paperworkshell.worker.redaction_ocr_operation._materialize_worker_private_temp",
        lambda: tmp_path,
    )
    storage = cast(WorkerPrivateTempStorage, SimpleNamespace(require_capacity=no_space))
    payload = _payload()
    context = OperationContext(
        _id(30),
        request().reconstruction.case_id,
        payload.requested_limits,
        Event(),
        lambda _: None,
        lambda: False,
        payload.inputs,
        payload.parameters,
    )
    result = RedactionOcrOperationHandler(_adapter(private_temp_storage=storage)).execute(context)
    assert result.outcome is OperationOutcome.FAILED and not result.outputs
    assert result.error is not None and result.error.category is ErrorCategory.RESOURCE_LIMIT
    assert set(discarded) == {20, 21, 22, 23}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "failure", [None, "source", "tamper", "report", "cancel", "timeout", "capability", "language"]
)
def test_broker_authenticates_candidate_and_closes_all_failed_attempts(failure: str | None) -> None:
    base = _plan()
    intent = replace(
        request(), reconstruction=replace(request().reconstruction, case_id=base.case_id)
    )
    events: list[str] = []
    output = _OutputBuffer(b"synthetic-private-page", 2 * 1024 * 1024, events)

    class Worker(_Worker):
        def start_operation(
            self, payload: OperationStartPayload, *, operation_id: UUID | None = None
        ) -> UUID:
            identifier = super().start_operation(payload, operation_id=operation_id)
            if failure == "language":
                assert self.terminal_result is not None
                self.terminal_result = replace(
                    self.terminal_result,
                    outcome=OperationOutcome.FAILED,
                    outputs=(),
                    error_code=ErrorCode.WORKER_OPERATION_FAILED,
                    error_category=ErrorCategory.DEPENDENCY,
                )
            return identifier

    worker = Worker(base, output, events)
    worker.capabilities = (
        (REDACTION_OCR_CAPABILITY,)
        if failure != "capability"
        else (_Worker(_plan(), output, events).capabilities[0],)
    )
    worker.output_content_kind = REDACTION_OCR_OUTPUT_CONTENT_KIND
    worker.progress_total = 1
    source = SimpleNamespace(
        **{
            name: getattr(intent.reconstruction, name)
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
        return encode_redaction_ocr_report(
            RedactionOcrWorkerReport(worker.operation_id, worker.output_object_id, value)
        )

    pipe.read_complete_frame = read_frame  # type: ignore[method-assign]
    policy = PdfExportBrokerPolicy(
        replace(_policy().limits, temporary_storage_bytes=10 * 1024 * 1024)
    )
    reconstructor = BrokeredRedactionOcrProcessor(
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
        with pytest.raises(PdfExportError) as caught:
            reconstructor.regenerate(
                intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
            )
        if failure == "language":
            assert caught.value.code is PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE
        if failure != "source":
            assert output.close_calls == 1 and worker.terminated
    else:
        with reconstructor.regenerate(
            intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
        ) as candidate:
            assert candidate.report.status == "awaiting_verification"
            with pytest.raises(PdfExportError, match="candidate_capacity"):
                reconstructor.regenerate(
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
        with reconstructor.regenerate(
            intent, cast(CommittedPdfPageLease, source), deadline=deadline, cancellation=cancel
        ) as retry:
            assert retry.report == candidate.report
        assert output.close_calls == 2
        assert events.index("worker.terminate") < events.index("output.iter.1")


@pytest.mark.parametrize(
    "invalid", ["kind", "area", "operation", "object", "size", "hash", "count"]
)
def test_result_cannot_escape_private_staging_or_change_binding(invalid: str) -> None:
    result = CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        _id(40),
        _id(41),
        REDACTION_OCR_OUTPUT_CONTENT_KIND,
        200,
        "a" * 64,
    )
    changes: dict[str, dict[str, object]] = {
        "kind": {"content_kind": "pdf"},
        "area": {"area": ReferenceArea.SOURCE_READ, "operation_id": None},
        "operation": {"operation_id": _id(42)},
        "object": {"object_id": _id(42)},
        "size": {"expected_byte_size": 1001},
        "hash": {"expected_sha256": None},
    }
    outputs = (result, result) if invalid == "count" else (replace(result, **changes[invalid]),)  # type: ignore[arg-type]
    with pytest.raises(ProtocolError):
        validate_redaction_ocr_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_id(41),
            output_object_id=_id(40),
            maximum_output_bytes=1000,
        )


def test_private_handler_is_absent_when_ocr_runtime_is_not_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.build_ocr_process_operation_handler",
        lambda: None,
    )
    assert build_redaction_ocr_operation_handler() is None
    assert _build_operation_registry(WorkerOperationScope.REDACTION_OCR).capabilities == (
        NOOP_CAPABILITY,
    )


def test_missing_language_is_actionable_and_publishes_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object) -> CaseObjectReference:
        reject_pdf_export(PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE, "redaction_ocr.languages")

    monkeypatch.setattr(WindowsRedactionOcrOperationAdapter, "regenerate", unavailable)
    payload = _payload()
    context = OperationContext(
        _id(30),
        request().reconstruction.case_id,
        payload.requested_limits,
        Event(),
        lambda _: None,
        lambda: False,
        payload.inputs,
        payload.parameters,
    )
    result = RedactionOcrOperationHandler(_adapter()).execute(context)
    assert result.outcome is OperationOutcome.FAILED and not result.outputs
    assert result.error is not None and result.error.category is ErrorCategory.DEPENDENCY
