"""Real Windows authority transfer around injected deterministic native adapters."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import BinaryIO

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetAnalysis,
    PdfImageAssetImpact,
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImagePageAnalysis,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
    PdfImageStageRequest,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl
from paperworkshell.pdf.image_stage_adapter import (
    PdfImageStageAdapterLimits,
    PdfImageStageAdapterResult,
)
from paperworkshell.worker import pdf_image_stage_operation as module
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.operations import OperationContext
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_image_stage_broker import _payload
from paperworkshell.worker.pdf_image_stage_operation import (
    PdfImageStageOperationHandler,
    WindowsPdfImageStageOperationAdapter,
)
from paperworkshell.worker.pdf_image_stage_wire import (
    decode_pdf_image_stage_evidence,
    decode_pdf_image_stage_result,
    encode_pdf_image_stage_plan,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    OperationOutcome,
    OperationProgressPayload,
    ReferenceArea,
    ResourceLimits,
)
from tests.unit.worker.pdf_optimization_fixtures import OptimizationStub
from tests.unit.worker.test_pdf_export_worker_adapter import (
    _FILE_MAP_READ,
    _FILE_MAP_WRITE,
    PDF_FIXTURE,
    _close_if_open,
    _duplicate_local_handle,
    _handle_is_open,
    _open_read_authority,
    _plan,
    _RecordingCandidateAdapter,
    _uuid,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows section authorities are required")


class StageAdapter:
    def __init__(self, failure: PdfExportErrorCode | None = None) -> None:
        self.failure = failure
        self.limits: PdfImageStageAdapterLimits | None = None
        self.temp: Path | None = None

    def process(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        request: PdfImageStageRequest,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        progress: Callable[[int, int], None],
    ) -> PdfImageStageAdapterResult:
        self.limits = limits
        self.temp = baseline_path.parent
        assert not control.cancellation_requested()
        assert not control.deadline_expired()
        total = 3 + len(request.selected_asset_ids)
        progress(1, total)
        if self.failure is not None:
            raise PdfExportError(code=self.failure, context="image_stage.synthetic_failure")
        asset = PdfImageAssetAnalysis(
            0,
            (0,),
            300,
            300,
            300_000,
            300_000,
            PdfImageColorMode.RGB,
            1000,
            PdfImageEligibility.ELIGIBLE,
        )
        analysis = PdfImageAnalysis(
            baseline_report.output_sha256,
            baseline_report.output_byte_size,
            1,
            (asset,),
            (PdfImagePageAnalysis(0, (0,), 1000, 1_000_000),),
        )
        changed = request.profile is not None and bool(request.selected_asset_ids)
        payload = baseline_path.read_bytes()
        output_payload = payload[:-100] if changed else payload
        digest = hashlib.sha256(output_payload).hexdigest()
        impacts = (
            (
                PdfImageAssetImpact(
                    0,
                    (0,),
                    300,
                    300,
                    300,
                    300,
                    300_000,
                    300_000,
                    300_000,
                    300_000,
                    PdfImageColorMode.RGB,
                    1000,
                    1000,
                    500,
                    500,
                    PdfImageStageOutcome.REENCODED,
                ),
            )
            if changed
            else ()
        )
        report = PdfImageStageReport(
            request.profile,
            PdfImageStageOutcome.REENCODED if changed else PdfImageStageOutcome.UNCHANGED,
            baseline_report.semantic_fingerprint,
            baseline_report.output_sha256,
            baseline_report.output_byte_size,
            len(output_payload) if changed else None,
            digest,
            len(output_payload),
            1,
            impacts,
            (PdfImagePageImpact(0, (0,) if changed else (), 1000, 1000, 500 if changed else 1000),),
            1,
        )
        if changed:
            candidate_path.write_bytes(output_payload)
        progress(total, total)
        return PdfImageStageAdapterResult(analysis, report, digest if changed else None)


def _run_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    storage: WorkerPrivateTempStorage | None = None,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_payload = PDF_FIXTURE.read_bytes()
    source_path.write_bytes(source_payload)
    original_hash = hashlib.sha256(source_payload).hexdigest()
    source_worker = _open_read_authority(source_path)
    plan = _plan(source_path)
    intent = (
        PdfImageStageRequest()
        if mode not in {"stage", "disk_initial", "disk_candidate"}
        else PdfImageStageRequest(
            PdfImageStageProfile.PRESERVE_300_DPI_Q90,
            (0,),
            original_hash,
            plan.semantic_fingerprint,
        )
    )
    baseline = _RecordingCandidateAdapter(source_path, source_payload)
    stage = StageAdapter(
        {
            "cancel": PdfExportErrorCode.CANCELLED,
            "timeout": PdfExportErrorCode.TIMED_OUT,
            "limit": PdfExportErrorCode.LIMIT_EXCEEDED,
        }.get(mode)
    )
    optimizer = OptimizationStub()
    monkeypatch.setattr(module, "optimize_verified_pdf", optimizer)
    limits = ResourceLimits(
        30_000, 20_000, 32 * 1024 * 1024, 8 * 1024 * 1024, 1024 * 1024, 10, 1024 * 1024
    )
    evidence_capacity = 65536
    progress: list[OperationProgressPayload] = []
    payload = encode_pdf_image_stage_plan(plan, {plan.sources[0].source_id: source_worker}, intent)
    with (
        WindowsPdfExportOutputBuffer.create(limits.output_bytes) as output,
        WindowsPdfExportOutputBuffer.create(evidence_capacity) as evidence,
        WindowsResultPipe.create() as pipe,
        WindowsPdfExportPlanBuffer.create(payload) as plan_buffer,
    ):
        handles = (
            _duplicate_local_handle(plan_buffer.read_handle_value, access=_FILE_MAP_READ),
            _duplicate_local_handle(output.write_handle_value, access=_FILE_MAP_WRITE),
            _duplicate_local_handle(evidence.write_handle_value, access=_FILE_MAP_WRITE),
            _duplicate_local_handle(pipe.write_handle_value),
        )
        pipe.close_local_writer()
        start = _payload(
            plan,
            _uuid(3),
            _uuid(4),
            _uuid(20),
            _uuid(21),
            payload,
            handles,
            evidence_capacity,
            limits,
        )
        context = OperationContext(
            _uuid(3),
            plan.case_id,
            limits,
            Event(),
            progress.append,
            lambda: False,
            start.inputs,
            start.parameters,
        )
        try:
            result = PdfImageStageOperationHandler(
                WindowsPdfImageStageOperationAdapter(
                    baseline, stage, storage or OsWorkerPrivateTempStorage()
                )
            ).execute(context)
            assert not any(_handle_is_open(handle) for handle in (*handles, source_worker))
        finally:
            for handle in (*handles, source_worker):
                _close_if_open(handle)
        if mode in {"analyze", "stage"}:
            assert result.outcome is OperationOutcome.SUCCEEDED
            header = decode_pdf_image_stage_result(pipe.read_complete_frame())
            actual = b"".join(output.iter_chunks(header.output_byte_size))
            assert actual == (source_payload[:-100] if mode == "stage" else source_payload)
            analysis, report = decode_pdf_image_stage_evidence(
                b"".join(evidence.iter_chunks(header.evidence_byte_size))
            )
            assert analysis.baseline_sha256 == original_hash
            assert report.output_sha256 == header.output_sha256
            assert len(progress) >= 3
            assert tuple(item.completed_units for item in progress) == tuple(
                sorted(item.completed_units for item in progress)
            )
        else:
            assert result.outputs == ()
            assert (
                result.outcome
                is {
                    "cancel": OperationOutcome.CANCELLED,
                    "timeout": OperationOutcome.TIMED_OUT,
                    "limit": OperationOutcome.FAILED,
                    "disk_initial": OperationOutcome.FAILED,
                    "disk_candidate": OperationOutcome.FAILED,
                }[mode]
            )
    assert source_path.read_bytes() == source_payload
    if mode.startswith("disk_"):
        assert stage.temp is None and stage.limits is None
        assert bool(optimizer.calls) is (mode == "disk_candidate")
        return
    assert stage.temp is not None and not stage.temp.exists()
    assert stage.limits is not None
    assert stage.limits.maximum_memory_bytes == limits.memory_bytes // 2
    assert (
        stage.limits.maximum_temporary_bytes
        == limits.temporary_storage_bytes
        - 2 * len(source_payload)
        - limits.output_bytes
        - evidence_capacity
    )
    assert optimizer.calls[0].reserved_temporary_bytes == evidence_capacity


@pytest.mark.parametrize("mode", ("analyze", "stage", "cancel", "timeout", "limit"))
def test_operation_consumes_handles_bounds_live_storage_and_cleans_on_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _run_operation(tmp_path, monkeypatch, mode)


class CapacityStorage:
    def __init__(self, fail_call: int) -> None:
        self.fail_call = fail_call
        self.calls: list[tuple[Path, int]] = []
        self.operation_roots: set[Path] = set()
        self.delegate = OsWorkerPrivateTempStorage()

    def require_capacity(self, root: Path, byte_count: int) -> None:
        self.calls.append((root, byte_count))
        if len(self.calls) == self.fail_call:
            raise WorkerPrivateTempStorageError
        self.delegate.require_capacity(root, byte_count)

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        self.operation_roots.add(root)
        return self.delegate.open_named(root, suffix=suffix)

    def open_anonymous(self, root: Path) -> BinaryIO:
        self.operation_roots.add(root)
        return self.delegate.open_anonymous(root)


@pytest.mark.parametrize("phase,fail_call", (("initial", 1), ("candidate", 2)))
def test_storage_capacity_failure_before_stage_cleans_all_authority_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, fail_call: int
) -> None:
    storage = CapacityStorage(fail_call)
    _run_operation(tmp_path, monkeypatch, f"disk_{phase}", storage)
    assert len(storage.calls) == fail_call
    assert not any(path.exists() for path in storage.operation_roots)
    # The same storage adapter recovers when capacity is available; fresh handles
    # and a fresh private operation directory cannot reuse any failed publication.
    _run_operation(tmp_path, monkeypatch, "stage", storage)
    assert not any(path.exists() for path in storage.operation_roots)
    assert storage.calls[0][1] == 8 * 1024 * 1024


def test_handler_cancelled_before_dispatch_consumes_outer_authority(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    intent = PdfImageStageRequest()
    source_worker = _open_read_authority(source_path)
    payload = encode_pdf_image_stage_plan(plan, {plan.sources[0].source_id: source_worker}, intent)
    limits = ResourceLimits(
        30_000, 20_000, 32 * 1024 * 1024, 8 * 1024 * 1024, 1024 * 1024, 10, 1024 * 1024
    )
    with (
        WindowsPdfExportOutputBuffer.create(limits.output_bytes) as output,
        WindowsPdfExportOutputBuffer.create(65536) as evidence,
        WindowsResultPipe.create() as pipe,
        WindowsPdfExportPlanBuffer.create(payload) as plan_buffer,
    ):
        handles = (
            _duplicate_local_handle(plan_buffer.read_handle_value, access=_FILE_MAP_READ),
            _duplicate_local_handle(output.write_handle_value, access=_FILE_MAP_WRITE),
            _duplicate_local_handle(evidence.write_handle_value, access=_FILE_MAP_WRITE),
            _duplicate_local_handle(pipe.write_handle_value),
        )
        pipe.close_local_writer()
        start = _payload(
            plan, _uuid(3), _uuid(4), _uuid(20), _uuid(21), payload, handles, 65536, limits
        )
        cancel = Event()
        cancel.set()
        context = OperationContext(
            _uuid(3),
            plan.case_id,
            limits,
            cancel,
            lambda _value: None,
            lambda: False,
            start.inputs,
            start.parameters,
        )
        try:
            result = PdfImageStageOperationHandler(WindowsPdfImageStageOperationAdapter()).execute(
                context
            )
            assert result.outcome is OperationOutcome.CANCELLED and result.outputs == ()
            assert not any(_handle_is_open(handle) for handle in handles)
            # Undecoded source handles are still closed by one-shot process termination.
        finally:
            for handle in (*handles, source_worker):
                _close_if_open(handle)


@pytest.mark.parametrize("scope", ("foreign_plan", "case_alias", "operation_alias"))
def test_direct_operation_request_requires_exact_nonaliased_scope(scope: str) -> None:
    plan = _plan(PDF_FIXTURE)
    limits = ResourceLimits(
        30_000, 20_000, 32 * 1024 * 1024, 8 * 1024 * 1024, 1024 * 1024, 10, 1024 * 1024
    )
    payload = encode_pdf_image_stage_plan(
        plan, {plan.sources[0].source_id: 10}, PdfImageStageRequest()
    )
    start = _payload(
        plan, _uuid(3), _uuid(4), _uuid(20), _uuid(21), payload, (11, 12, 13, 14), 65536, limits
    )
    context = OperationContext(
        _uuid(3),
        plan.case_id,
        limits,
        Event(),
        lambda _value: None,
        lambda: False,
        start.inputs,
        start.parameters,
    )
    if scope == "foreign_plan":
        context = replace(
            context,
            inputs=(
                replace(start.inputs[0], area=ReferenceArea.OPERATION_TEMP, operation_id=_uuid(99)),
                *start.inputs[1:],
            ),
        )
    elif scope == "case_alias":
        context = replace(context, case_id=_uuid(20))
    else:
        context = replace(context, operation_id=_uuid(20))
    with pytest.raises(PdfExportError) as caught:
        module._request(context)
    assert caught.value.code is PdfExportErrorCode.RESULT_INVALID
