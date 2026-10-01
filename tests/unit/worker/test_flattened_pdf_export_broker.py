"""Focused trusted-broker evidence for PWS-0307 flattened PDF export."""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from io import BytesIO
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell import __main__ as desktop
from paperworkshell.domain.pdf_export import (
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportAsset,
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.overlays.models import OverlayRecord, SignatureImageOverlayPayload
from paperworkshell.overlays.rendering import OverlayRasterAsset
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
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
from paperworkshell.worker.flattened_pdf_export_broker import (
    BrokeredFlattenedPdfExportCandidate,
    BrokeredFlattenedPdfExporter,
    FlattenedPdfExportBrokerPolicy,
)
from paperworkshell.worker.flattened_pdf_export_plan import (
    decode_flattened_pdf_worker_plan,
)
from paperworkshell.worker.flattened_pdf_export_report import (
    FlattenedPdfExportWorkerReport,
    encode_flattened_pdf_worker_report,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import (
    CancellationProbe,
    DeadlineProbe,
    PdfExportSourceBatchLease,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES,
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
    ScalarValueType,
    validate_flattened_pdf_export_start,
)
from tests.unit.worker.flattened_pdf_export_fixtures import (
    synthetic_flattened_plan,
    uuid4,
)


def _plan_and_rasters() -> tuple[
    FlattenedPdfExportPlan,
    dict[UUID, SignatureImageOverlayRasterSource],
    bytes,
]:
    template = synthetic_flattened_plan()
    pixels = bytes(range(16))
    metadata = OverlayRasterAsset(
        asset_id=template.assets[0].asset_id,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    plan = FlattenedPdfExportPlan(
        base_plan=template.base_plan,
        pages=template.pages,
        assets=(FlattenedPdfExportAsset(template.assets[0].encoded_asset, metadata),),
    )
    source = SignatureImageOverlayRasterSource(metadata, pixels)
    source.seek(7)
    return plan, {metadata.asset_id: source}, pixels


def _plan_with_raster_asset_count(asset_count: int) -> FlattenedPdfExportPlan:
    template = synthetic_flattened_plan()
    assets: list[FlattenedPdfExportAsset] = []
    overlays: list[OverlayRecord] = []
    for index in range(asset_count):
        asset_id = uuid4(1_000 + index)
        assets.append(
            FlattenedPdfExportAsset(
                replace(
                    template.assets[0].encoded_asset,
                    asset_id=asset_id,
                    storage_name=f"{asset_id}.bin",
                ),
                replace(template.assets[0].raster_asset, asset_id=asset_id),
            )
        )
        overlays.append(
            replace(
                template.pages[0].overlays[0],
                overlay_id=uuid4(2_000 + index),
                z_index=index,
                payload=SignatureImageOverlayPayload(asset_id),
            )
        )
    return FlattenedPdfExportPlan(
        base_plan=template.base_plan,
        pages=(replace(template.pages[0], overlays=tuple(overlays)),),
        assets=tuple(assets),
    )


def _policy(
    *,
    temporary_storage_bytes: int = 4 * 1024 * 1024,
    output_bytes: int = 1024 * 1024,
) -> FlattenedPdfExportBrokerPolicy:
    return FlattenedPdfExportBrokerPolicy(
        ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=8 * 1024 * 1024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=output_bytes,
            page_count=10,
            pixel_count=1024 * 1024,
        )
    )


@dataclass(slots=True)
class _LeasedSource:
    source_id: UUID
    media_type: PdfExportSourceMediaType
    byte_size: int
    page_count: int
    sha256: str
    signature_state: object
    encryption_state: PdfExportSourceEncryptionState
    handle_value: int


class _SourceBatch:
    def __init__(self, plan: FlattenedPdfExportPlan, events: list[str]) -> None:
        base = plan.base_plan
        self.case_id = base.case_id
        self.manifest_revision = base.manifest_revision
        self.manifest_sha256 = base.manifest_sha256
        self.manifest_cas_token = base.manifest_cas_token
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
            for index, source in enumerate(base.sources)
        )
        self.events = events
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


class _OutputBuffer:
    def __init__(self, payload: bytes, capacity: int, events: list[str]) -> None:
        self.payload = payload
        self.capacity = capacity
        self.write_handle_value = 202
        self.events = events
        self.iter_calls: list[tuple[int, int]] = []
        self.close_calls = 0
        self.short = False
        self.fail = False
        self.close_error: BaseException | None = None

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]:
        self.iter_calls.append((byte_count, chunk_bytes))
        self.events.append(f"output.iter.{len(self.iter_calls)}")
        selected = self.payload[:byte_count]

        def chunks() -> Iterator[bytes]:
            if self.fail:
                raise RuntimeError("PRIVATE OUTPUT CANARY")
            value = selected[:-1] if self.short else selected
            for offset in range(0, len(value), 5):
                yield value[offset : offset + 5]

        return chunks()

    def close(self) -> None:
        self.close_calls += 1
        self.events.append("output.close")
        if self.close_error is not None:
            raise self.close_error


class _RasterBuffer:
    def __init__(self, payload: bytes, handle: int, events: list[str]) -> None:
        self.payload = payload
        self.payload_size = len(payload)
        self.payload_sha256 = hashlib.sha256(payload).hexdigest()
        self.read_handle_value = handle
        self.events = events
        self.close_calls = 0
        self.enter_result: object = self

    def __enter__(self) -> Self:
        self.events.append("raster.enter")
        return cast(Self, self.enter_result)

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback_value: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback_value
        self.close()

    def close(self) -> None:
        self.close_calls += 1
        self.events.append("raster.close")


class _PlanBuffer:
    def __init__(self, payload: bytes, events: list[str]) -> None:
        self.payload = payload
        self.payload_size = len(payload)
        self.read_handle_value = 201
        self.events = events
        self.close_calls = 0

    def __enter__(self) -> Self:
        self.events.append("plan.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback_value: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback_value
        self.close_calls += 1
        self.events.append("plan.exit")


class _ResultPipe:
    def __init__(
        self,
        plan: FlattenedPdfExportPlan,
        output: _OutputBuffer,
        events: list[str],
    ) -> None:
        self.plan = plan
        self.output = output
        self.events = events
        self.worker: _Worker | None = None
        self.write_handle_value = 203
        self.raw_frame: bytes | None = None
        self.operation_override: UUID | None = None
        self.output_override: UUID | None = None
        self.digest_override: str | None = None
        self.size_override: int | None = None

    def __enter__(self) -> Self:
        self.events.append("pipe.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback_value: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback_value
        self.events.append("pipe.exit")

    def close_local_writer(self) -> None:
        self.events.append("pipe.close_writer")

    def read_complete_frame(self) -> bytes:
        self.events.append("pipe.read")
        assert self.worker is not None and self.worker.terminated
        if self.raw_frame is not None:
            return self.raw_frame
        operation_id = self.operation_override or self.worker.operation_id
        output_object_id = self.output_override or self.worker.output_object_id
        assert operation_id is not None and output_object_id is not None
        report = FlattenedPdfExportVerificationReport.for_verified_plan(
            self.plan,
            output_sha256=(
                hashlib.sha256(self.output.payload).hexdigest()
                if self.digest_override is None
                else self.digest_override
            ),
            output_byte_size=(
                len(self.output.payload) if self.size_override is None else self.size_override
            ),
        )
        return encode_flattened_pdf_worker_report(
            FlattenedPdfExportWorkerReport(operation_id, output_object_id, report)
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
        session_id=uuid4(70),
        worker_instance_id=uuid4(71),
        pid=321,
        capabilities=capabilities,
        active_operation=active,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(
        self, plan: FlattenedPdfExportPlan, output: _OutputBuffer, events: list[str]
    ) -> None:
        self.plan = plan
        self.output = output
        self.events = events
        self.generation = 1
        self.payload: OperationStartPayload | None = None
        self.operation_id: UUID | None = None
        self.output_object_id: UUID | None = None
        self.terminal_result: WorkerOperationResultSnapshot | None = None
        self.terminated = False
        self.ready_queries = 0
        self.result_queries = 0
        self.duplicate_calls: list[tuple[int, BrokeredHandlePurpose]] = []
        self.cancel_calls: list[CancelReason] = []
        self.capabilities = (FLATTENED_PDF_EXPORT_CAPABILITY,)
        self.fault_after_start = False
        self.withhold_result = False
        self.start_return_override: UUID | None = None
        self.duplicate_collision_call: int | None = None
        self.output_object_override: UUID | None = None
        self.output_operation_override: UUID | None = None
        self.output_digest_override: str | None = None
        self.output_size_override: int | None = None
        self.result_operation_override: UUID | None = None
        self.outcome = OperationOutcome.SUCCEEDED
        self.error_category: ErrorCategory | None = None
        self.termination_error: WorkerClientError | None = None
        self.cancellation_pump_error: WorkerClientError | None = None
        self.active_operation_override: UUID | None = None

    def start(self, case_id: UUID) -> int:
        assert case_id == self.plan.base_plan.case_id
        self.events.append("worker.start")
        return self.generation

    def query(self) -> WorkerClientSnapshot:
        self.events.append("worker.query")
        if self.payload is None:
            self.ready_queries += 1
            return _snapshot(
                generation=self.generation,
                phase=(
                    WorkerLifecyclePhase.STARTING
                    if self.ready_queries == 1
                    else WorkerLifecyclePhase.READY
                ),
                case_id=self.plan.base_plan.case_id,
                capabilities=self.capabilities,
            )
        self.result_queries += 1
        if self.fault_after_start:
            return _snapshot(
                generation=self.generation,
                phase=WorkerLifecyclePhase.FAULTED,
                case_id=self.plan.base_plan.case_id,
                capabilities=self.capabilities,
            )
        result = (
            self.terminal_result if not self.withhold_result and self.result_queries >= 3 else None
        )
        active: WorkerOperationSnapshot | None = None
        if result is None:
            assert self.operation_id is not None
            active = WorkerOperationSnapshot(
                self.active_operation_override or self.operation_id,
                uuid4(72),
                FLATTENED_PDF_EXPORT_CAPABILITY.capability_id,
                FLATTENED_PDF_EXPORT_CAPABILITY.capability_version,
                True,
                bool(self.cancel_calls),
                WorkerProgressSnapshot(
                    ProgressStage.PROCESSING,
                    0 if self.result_queries == 1 else self.plan.page_count,
                    self.plan.page_count,
                    ProgressUnit.PAGES,
                ),
            )
        return _snapshot(
            generation=self.generation,
            phase=WorkerLifecyclePhase.BUSY,
            case_id=self.plan.base_plan.case_id,
            capabilities=self.capabilities,
            active=active,
            result=result,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.events.append("worker.pump")
        if self.cancel_calls and self.cancellation_pump_error is not None:
            raise self.cancellation_pump_error
        return _snapshot(
            generation=self.generation,
            phase=(
                WorkerLifecyclePhase.STARTING if self.payload is None else WorkerLifecyclePhase.BUSY
            ),
            case_id=self.plan.base_plan.case_id,
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
            return 1001
        return 1000 + len(self.duplicate_calls)

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        validate_flattened_pdf_export_start(payload)
        self.events.append("worker.operation")
        self.payload = payload
        self.operation_id = operation_id
        parameters = {parameter.name: parameter for parameter in payload.parameters}
        selected = parameters[FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER]
        assert selected.value_type is ScalarValueType.UUID
        self.output_object_id = cast(UUID, selected.value)
        result_operation = self.result_operation_override or operation_id
        output = CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=self.output_object_override or self.output_object_id,
            operation_id=self.output_operation_override or operation_id,
            content_kind=FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
            expected_byte_size=(
                len(self.output.payload)
                if self.output_size_override is None
                else self.output_size_override
            ),
            expected_sha256=(
                hashlib.sha256(self.output.payload).hexdigest()
                if self.output_digest_override is None
                else self.output_digest_override
            ),
        )
        error_code = (
            ErrorCode.WORKER_OPERATION_FAILED if self.outcome is OperationOutcome.FAILED else None
        )
        self.terminal_result = WorkerOperationResultSnapshot(
            self.generation,
            result_operation,
            self.outcome,
            (output,) if self.outcome is OperationOutcome.SUCCEEDED else (),
            error_code,
            self.error_category,
        )
        return operation_id if self.start_return_override is None else self.start_return_override

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.events.append(f"worker.cancel.{reason.value}")
        self.cancel_calls.append(reason)
        assert self.operation_id is not None
        self.withhold_result = False
        self.result_queries = 3
        self.terminal_result = WorkerOperationResultSnapshot(
            self.generation,
            self.operation_id,
            OperationOutcome.CANCELLED,
            (),
            None,
        )
        return uuid4(73)

    def terminate(self) -> None:
        self.events.append("worker.terminate")
        self.terminated = True
        if self.termination_error is not None:
            raise self.termination_error


class _System:
    def __init__(
        self,
        *,
        policy: FlattenedPdfExportBrokerPolicy | None = None,
        heavy: HeavyOperationCoordinator | None = None,
        output_payload: bytes = b"%PDF-1.7\nflattened synthetic output\n%%EOF\n",
    ) -> None:
        self.events: list[str] = []
        self.plan, self.rasters, self.pixels = _plan_and_rasters()
        self.policy = _policy() if policy is None else policy
        self.output = _OutputBuffer(output_payload, self.policy.limits.output_bytes, self.events)
        self.worker = _Worker(self.plan, self.output, self.events)
        self.sources = _SourceBatch(self.plan, self.events)
        self.sources.worker = self.worker
        self.pipe = _ResultPipe(self.plan, self.output, self.events)
        self.pipe.worker = self.worker
        self.raster_buffers: list[_RasterBuffer] = []
        self.plan_buffer: _PlanBuffer | None = None
        self.raster_handle_override: int | None = None
        self.raster_factory_error: BaseException | None = None
        self.plan_size_delta = 0
        self.output_factory_calls = 0
        self.raster_factory_calls = 0
        identifiers = iter((uuid4(90), uuid4(91), uuid4(92)))

        def output_factory(capacity: int) -> _OutputBuffer:
            self.events.append("output.create")
            self.output_factory_calls += 1
            assert 1 <= capacity <= self.policy.limits.output_bytes
            self.output.capacity = capacity
            return self.output

        def raster_factory(payload: bytes) -> _RasterBuffer:
            self.events.append("raster.create")
            self.raster_factory_calls += 1
            if self.raster_factory_error is not None:
                raise self.raster_factory_error
            selected = _RasterBuffer(
                payload,
                301 if self.raster_handle_override is None else self.raster_handle_override,
                self.events,
            )
            self.raster_buffers.append(selected)
            return selected

        def plan_factory(payload: bytes) -> _PlanBuffer:
            self.events.append("plan.create")
            self.plan_buffer = _PlanBuffer(payload, self.events)
            self.plan_buffer.payload_size += self.plan_size_delta
            return self.plan_buffer

        def pipe_factory() -> _ResultPipe:
            self.events.append("pipe.create")
            return self.pipe

        self.heavy = HeavyOperationCoordinator(1) if heavy is None else heavy
        self.exporter = BrokeredFlattenedPdfExporter(
            cast(PdfExportWorkerCoordinator, self.worker),
            self.policy,
            heavy_operations=self.heavy,
            pipe_factory=pipe_factory,
            plan_buffer_factory=plan_factory,
            output_buffer_factory=output_factory,
            raster_buffer_factory=raster_factory,
            id_factory=lambda: next(identifiers),
        )

    def export(
        self,
        *,
        deadline: DeadlineProbe = lambda: False,
        cancellation: CancellationProbe = lambda: False,
        progress: Callable[[PdfExportProgress], None] = lambda _value: None,
        reserved_temporary_bytes: int = 0,
    ) -> BrokeredFlattenedPdfExportCandidate:
        return self.exporter.export(
            self.plan,
            cast(PdfExportSourceBatchLease, self.sources),
            self.rasters,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
            reserved_temporary_bytes=reserved_temporary_bytes,
        )


def _error(
    system: _System,
    *,
    deadline: DeadlineProbe = lambda: False,
    cancellation: CancellationProbe = lambda: False,
) -> FlattenedPdfExportError:
    with pytest.raises(FlattenedPdfExportError) as raised:
        system.export(deadline=deadline, cancellation=cancellation)
    return raised.value


def test_retained_package_bytes_reduce_worker_quota_without_raising_other_limits() -> None:
    system = _System()
    reserved = 100_000
    with system.export(reserved_temporary_bytes=reserved):
        assert system.worker.payload is not None
        assert system.worker.payload.requested_limits == replace(
            system.policy.limits,
            temporary_storage_bytes=system.policy.limits.temporary_storage_bytes - reserved,
            page_count=system.plan.page_count,
        )
    assert system.output.close_calls == 1


@pytest.mark.parametrize("reserved", [-1, True, "1024", 4 * 1024 * 1024])
def test_bad_package_reservation_fails_before_allocating(reserved: object) -> None:
    system = _System()
    with pytest.raises(FlattenedPdfExportError) as error:
        system.export(reserved_temporary_bytes=cast(int, reserved))
    assert error.value.code is (
        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
        if reserved == 4 * 1024 * 1024
        else FlattenedPdfExportErrorCode.MODEL_INVALID
    )
    assert system.output_factory_calls == system.raster_factory_calls == 0
    assert not system.events


def test_package_reservation_counts_sources_and_rasters_before_allocating() -> None:
    system = _System()
    source_bytes = sum(source.byte_size for source in system.plan.base_plan.sources)
    reserved = system.policy.limits.temporary_storage_bytes - source_bytes - len(system.pixels) + 1
    with pytest.raises(FlattenedPdfExportError) as error:
        system.export(reserved_temporary_bytes=reserved)
    assert error.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert system.output_factory_calls == system.raster_factory_calls == 0


@pytest.mark.parametrize("reserved", [0, 1024 * 1024, 300 * 1024 * 1024])
def test_desktop_constrained_capacity_fits_sources_plan_and_two_outputs(reserved: int) -> None:
    policy = FlattenedPdfExportBrokerPolicy(
        desktop.build_windows_flattened_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)
    )
    system = _System(policy=policy)
    with system.export(reserved_temporary_bytes=reserved) as candidate:
        assert system.worker.payload is not None
        assert system.plan_buffer is not None
        limits = system.worker.payload.requested_limits
        source_bytes = sum(source.byte_size for source in system.plan.base_plan.sources)
        raster_bytes = sum(source.metadata.byte_count for source in system.rasters.values())
        assert source_bytes > 0 and raster_bytes > 0
        assert limits.temporary_storage_bytes == policy.limits.temporary_storage_bytes - reserved
        assert 0 < limits.output_bytes < policy.limits.output_bytes
        assert limits.output_bytes == system.output.capacity == candidate.maximum_output_bytes
        assert (
            source_bytes + raster_bytes + system.plan_buffer.payload_size + 2 * limits.output_bytes
            <= limits.temporary_storage_bytes
        )
        assert limits == replace(
            policy.limits,
            output_bytes=limits.output_bytes,
            temporary_storage_bytes=limits.temporary_storage_bytes,
            page_count=system.plan.page_count,
        )
        assert candidate.read(1024) == system.output.payload
    assert system.output.close_calls == 1


def test_success_binds_exact_authorities_restores_raster_and_streams_candidate() -> None:
    system = _System()
    progress: list[PdfExportProgress] = []

    candidate = system.export(progress=progress.append)

    raster_source = next(iter(system.rasters.values()))
    assert raster_source.tell() == 7
    assert system.raster_buffers[0].payload == system.pixels
    assert candidate.maximum_output_bytes == system.policy.limits.output_bytes
    assert system.exporter.maximum_input_references == MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES
    assert candidate.report.plan is system.plan
    assert candidate.output_object_id == uuid4(92)
    assert progress == [
        PdfExportProgress(PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 0, 1),
        PdfExportProgress(PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 1, 1),
    ]
    assert system.events.index("worker.terminate") < system.events.index("source.revalidate")
    assert system.events.index("source.revalidate") < system.events.index("pipe.read")
    assert system.events.index("pipe.read") < system.events.index("output.iter.1")
    assert system.worker.duplicate_calls == [
        (101, BrokeredHandlePurpose.READ_SOURCE),
        (301, BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION),
        (201, BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION),
        (202, BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION),
        (203, BrokeredHandlePurpose.WRITE_RESULT_PIPE),
    ]

    payload = system.worker.payload
    plan_buffer = system.plan_buffer
    assert payload is not None and plan_buffer is not None
    assert payload.inputs[0] == CaseObjectReference(
        ReferenceArea.OPERATION_TEMP,
        uuid4(91),
        uuid4(90),
        FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
        len(plan_buffer.payload),
        hashlib.sha256(plan_buffer.payload).hexdigest(),
    )
    assert payload.inputs[1].area is ReferenceArea.SOURCE_READ
    assert payload.inputs[2] == CaseObjectReference(
        ReferenceArea.OPERATION_TEMP,
        system.plan.assets[0].asset_id,
        uuid4(90),
        FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
        len(system.pixels),
        hashlib.sha256(system.pixels).hexdigest(),
    )
    decoded = decode_flattened_pdf_worker_plan(plan_buffer.payload)
    assert decoded.plan == system.plan
    assert decoded.sources[0].worker_handle == 1001
    assert decoded.assets[0].worker_handle == 1002

    with candidate:
        parts = [candidate.read(3), candidate.read(7), candidate.read(1024)]
        assert b"".join(parts) == system.output.payload
        assert candidate.read(1) == b""
    assert system.output.close_calls == 1


def test_revocation_and_shared_heavy_gate_fail_before_allocating() -> None:
    revoked = _System()
    assert revoked.exporter.maximum_output_bytes == revoked.policy.limits.output_bytes
    assert revoked.exporter.maximum_input_references == MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES
    revoked.exporter._revoke_permanently()
    error = _error(revoked)
    assert revoked.exporter.revoked
    assert error.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert revoked.events == []

    gate = HeavyOperationCoordinator(1)
    lease = gate.try_acquire(0.001)
    assert lease is not None
    blocked = _System(heavy=gate)
    try:
        error = _error(blocked, cancellation=lambda: True)
    finally:
        lease.__exit__(None, None, None)
    assert error.code is FlattenedPdfExportErrorCode.CANCELLED
    assert blocked.output_factory_calls == 0
    assert blocked.raster_factory_calls == 0


def test_exact_raster_mapping_and_changed_pixels_fail_with_position_restored() -> None:
    system = _System()
    asset_id = system.plan.assets[0].asset_id
    source = system.rasters[asset_id]

    missing = dict(system.rasters)
    del missing[asset_id]
    with pytest.raises(FlattenedPdfExportError) as missing_error:
        system.exporter.export(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            missing,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert missing_error.value.code is FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING

    extra = dict(system.rasters)
    extra[uuid4(99)] = source
    with pytest.raises(FlattenedPdfExportError) as extra_error:
        system.exporter.export(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            extra,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert extra_error.value.code is FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA

    source._stream = BytesIO(b"x" * 16)
    source.seek(6)
    changed = _error(system)
    assert changed.code is FlattenedPdfExportErrorCode.ASSET_CHANGED
    assert source.tell() == 6
    assert system.output.close_calls == 1


def test_input_reference_capacity_rejects_before_binding_or_allocation() -> None:
    accepted_boundary = _System()
    accepted_boundary.plan = _plan_with_raster_asset_count(254)
    accepted_boundary.sources = _SourceBatch(accepted_boundary.plan, accepted_boundary.events)
    accepted_boundary.rasters = {}

    accepted_error = _error(accepted_boundary)

    assert accepted_error.code is FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING
    assert accepted_boundary.output_factory_calls == 0
    assert "worker.start" not in accepted_boundary.events

    rejected_boundary = _System()
    rejected_boundary.plan = _plan_with_raster_asset_count(255)
    rejected_boundary.sources = _SourceBatch(rejected_boundary.plan, rejected_boundary.events)
    rejected_boundary.rasters = {}

    rejected_error = _error(rejected_boundary)

    assert rejected_error.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert rejected_error.context == "flattened.input_references"
    assert rejected_boundary.output_factory_calls == 0
    assert rejected_boundary.raster_factory_calls == 0
    assert rejected_boundary.worker.duplicate_calls == []
    assert "worker.start" not in rejected_boundary.events


@pytest.mark.parametrize("fault", ("local", "worker", "active", "output"))
def test_alias_and_correlation_faults_fail_closed(fault: str) -> None:
    system = _System()
    if fault == "local":
        system.raster_handle_override = 101
    elif fault == "worker":
        system.worker.duplicate_collision_call = 2
    elif fault == "active":
        system.worker.active_operation_override = uuid4(98)
    else:
        system.worker.output_object_override = uuid4(98)

    error = _error(system)

    assert error.code in {
        FlattenedPdfExportErrorCode.MODEL_INVALID,
        FlattenedPdfExportErrorCode.REPORT_INVALID,
        FlattenedPdfExportErrorCode.RESULT_INVALID,
    }
    assert system.output.close_calls == 1
    assert all(buffer.close_calls >= 1 for buffer in system.raster_buffers)


@pytest.mark.parametrize("fault", ("report", "report_digest", "output_digest", "short"))
def test_malformed_report_and_output_are_rejected_after_termination(fault: str) -> None:
    system = _System()
    if fault == "report":
        system.pipe.raw_frame = b'{"PRIVATE":"REPORT"}'
    elif fault == "report_digest":
        system.pipe.digest_override = "f" * 64
    elif fault == "output_digest":
        system.worker.output_digest_override = "e" * 64
    else:
        system.output.short = True

    error = _error(system)

    assert system.worker.terminated
    assert error.code in {
        FlattenedPdfExportErrorCode.REPORT_INVALID,
        FlattenedPdfExportErrorCode.RESULT_INVALID,
    }
    assert system.output.close_calls == 1


def test_cancellation_timeout_crash_termination_and_source_change_are_closed() -> None:
    cancelled = _System()
    cancelled.worker.withhold_result = True
    error = _error(
        cancelled,
        cancellation=lambda: cancelled.worker.result_queries >= 1,
    )
    assert error.code is FlattenedPdfExportErrorCode.CANCELLED
    assert cancelled.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert cancelled.worker.terminated

    timed_out = _System()
    timed_out.worker.withhold_result = True
    error = _error(timed_out, deadline=lambda: timed_out.worker.result_queries >= 1)
    assert error.code is FlattenedPdfExportErrorCode.TIMED_OUT
    assert timed_out.worker.cancel_calls == [CancelReason.SHUTDOWN]
    assert timed_out.worker.terminated

    cancellation_timed_out = _System()
    cancellation_timed_out.worker.withhold_result = True
    cancellation_timed_out.worker.cancellation_pump_error = WorkerClientError(
        WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
        WorkerClientContext.CANCELLATION,
    )
    error = _error(
        cancellation_timed_out,
        cancellation=lambda: cancellation_timed_out.worker.result_queries >= 1,
    )
    assert error.code is FlattenedPdfExportErrorCode.TIMED_OUT
    assert cancellation_timed_out.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert cancellation_timed_out.worker.terminated

    crashed = _System()
    crashed.worker.fault_after_start = True
    assert _error(crashed).code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert crashed.worker.terminated

    termination = _System()
    termination.worker.termination_error = WorkerClientError(
        WorkerClientErrorCode.TERMINATION_FAILED,
        WorkerClientContext.SHUTDOWN,
    )
    assert _error(termination).code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert "pipe.read" not in termination.events
    assert not termination.output.iter_calls

    changed = _System()
    changed.sources.revalidate_error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "source.verify",
    )
    assert _error(changed).code is FlattenedPdfExportErrorCode.SOURCE_CHANGED
    assert changed.worker.terminated
    assert "pipe.read" not in changed.events


def test_exact_plan_limit_and_resource_limited_worker_cleanup_all_sections() -> None:
    limited = _System(policy=_policy(temporary_storage_bytes=1_500, output_bytes=1_000))
    error = _error(limited)
    assert error.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert limited.output_factory_calls == 0
    assert all(buffer.close_calls >= 1 for buffer in limited.raster_buffers)

    worker_limited = _System()
    worker_limited.worker.outcome = OperationOutcome.FAILED
    worker_limited.worker.error_category = ErrorCategory.RESOURCE_LIMIT
    error = _error(worker_limited)
    assert error.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert worker_limited.worker.terminated
    assert worker_limited.output.close_calls == 1


def test_candidate_short_read_cleanup_failure_and_private_failures_are_sanitized() -> None:
    short = _System()
    candidate = short.export()
    short.output.short = True
    with pytest.raises(FlattenedPdfExportError) as short_error:
        candidate.read(1024)
    assert short_error.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    candidate.close()

    cleanup = _System()
    cleanup.output.close_error = RuntimeError("PRIVATE CLEANUP CANARY")
    cleanup.pipe.raw_frame = b"bad"
    error = _error(cleanup)
    assert error.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert "PRIVATE CLEANUP CANARY" not in formatted
    assert error.__cause__ is None and error.__context__ is None

    private = _System()
    private.raster_factory_error = RuntimeError("PRIVATE RASTER CANARY")
    error = _error(private)
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert error.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE RASTER CANARY" not in formatted
    assert error.__cause__ is None and error.__context__ is None
