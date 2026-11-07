"""GUI-safe broker for exact derivative text; no document parser is imported."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.text_companion import (
    TextCompanionError,
    TextCompanionErrorCode,
    TextCompanionLimits,
    reject_text_companion,
    require_companion_active,
    sanitize_companion_text,
)
from paperworkshell.worker._windows_broker import (
    WindowsBoundedOutputBuffer,
    retire_runtime_on_failure,
)
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.protocol.text_companion import (
    TEXT_COMPANION_ARCHIVE_KIND,
    TEXT_COMPANION_CAPABILITY,
    TEXT_COMPANION_CONTENT_KIND,
    TEXT_COMPANION_SOURCE_KIND,
)
from paperworkshell.worker.signature_image_raster_broker import (
    BoundedSignatureImageOutput,
    SignatureImageWorkerCoordinator,
)


class BrokeredTextCompanionExtractor:
    def __init__(
        self,
        worker: SignatureImageWorkerCoordinator,
        policy: ResourcePolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        output_buffer_factory: Callable[
            [int], BoundedSignatureImageOutput
        ] = WindowsBoundedOutputBuffer.create,
    ) -> None:
        self._worker, self._policy, self._heavy = worker, policy, heavy_operations
        self._buffer_factory = output_buffer_factory

    @retire_runtime_on_failure
    def extract_handle(
        self,
        case_id: UUID,
        source_handle: int,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        page_count: int,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        member_position: int | None = None,
        member_byte_size: int | None = None,
        member_sha256: str | None = None,
    ) -> bytes:
        """Authenticate worker output only after worker termination and cancellation checks."""
        policy = TextCompanionLimits.from_resource_policy(self._policy)
        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(source_handle) is not int
            or source_handle < 1
            or type(expected_byte_size) is not int
            or not 1 <= expected_byte_size <= policy.maximum_source_bytes
            or type(page_count) is not int
            or not 1 <= page_count <= policy.maximum_pages
        ):
            reject_text_companion(TextCompanionErrorCode.INVALID)
        require_companion_active(deadline, cancellation)
        limits = ResourceLimits(
            self._policy.worker_cpu_time_ms,
            self._policy.worker_cpu_time_ms,
            self._policy.worker_process_memory_bytes,
            self._policy.maximum_temporary_storage_bytes,
            policy.maximum_output_bytes,
            self._policy.maximum_page_count,
            1,
        )
        try:
            while (lease := self._heavy.try_acquire(0.05)) is None:
                require_companion_active(deadline, cancellation)
            with lease:
                return self._extract(
                    case_id,
                    source_handle,
                    expected_byte_size,
                    expected_sha256,
                    page_count,
                    limits,
                    deadline,
                    cancellation,
                    member_position,
                    member_byte_size,
                    member_sha256,
                )
        except TextCompanionError:
            raise
        except Exception:
            reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)

    def _extract(
        self,
        case_id: UUID,
        source_handle: int,
        byte_size: int,
        digest: str,
        page_count: int,
        limits: ResourceLimits,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        member_position: int | None,
        member_byte_size: int | None,
        member_sha256: str | None,
    ) -> bytes:
        operation_id, source_id, output_id = uuid4(), uuid4(), uuid4()
        with self._buffer_factory(limits.output_bytes) as output:
            if output.capacity != limits.output_bytes:
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            try:
                self._worker.start(case_id)
                while self._worker.query().phase is not WorkerLifecyclePhase.READY:
                    require_companion_active(deadline, cancellation)
                    if self._worker.query().phase in {
                        WorkerLifecyclePhase.STOPPED,
                        WorkerLifecyclePhase.FAULTED,
                    }:
                        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                    self._worker.pump(0.05)
                if TEXT_COMPANION_CAPABILITY not in self._worker.query().capabilities:
                    reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                input_handle = self._worker.duplicate_handle(
                    source_handle, BrokeredHandlePurpose.READ_SOURCE
                )
                output_handle = self._worker.duplicate_handle(
                    output.write_handle_value, BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION
                )
                parameters: tuple[TaggedScalar, ...] = (
                    TaggedScalar("source_handle", ScalarValueType.INTEGER, input_handle),
                    TaggedScalar("output_handle", ScalarValueType.INTEGER, output_handle),
                    TaggedScalar("output_object_id", ScalarValueType.UUID, output_id),
                    TaggedScalar("page_count", ScalarValueType.INTEGER, page_count),
                )
                if member_position is not None:
                    parameters += (
                        TaggedScalar("member_position", ScalarValueType.INTEGER, member_position),
                        TaggedScalar(
                            "member_byte_size", ScalarValueType.INTEGER, cast(int, member_byte_size)
                        ),
                        TaggedScalar(
                            "member_sha256",
                            ScalarValueType.TOKEN,
                            "sha256-" + cast(str, member_sha256),
                        ),
                    )
                payload = OperationStartPayload(
                    TEXT_COMPANION_CAPABILITY.capability_id,
                    1,
                    (
                        CaseObjectReference(
                            ReferenceArea.SOURCE_READ,
                            source_id,
                            None,
                            TEXT_COMPANION_SOURCE_KIND
                            if member_position is None
                            else TEXT_COMPANION_ARCHIVE_KIND,
                            byte_size,
                            digest,
                        ),
                    ),
                    parameters,
                    limits,
                )
                if self._worker.start_operation(payload, operation_id=operation_id) != operation_id:
                    reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                cancelled = False
                while True:
                    snapshot = self._worker.query()
                    if snapshot.last_result is not None:
                        result = snapshot.last_result
                        break
                    if snapshot.phase in {
                        WorkerLifecyclePhase.READY,
                        WorkerLifecyclePhase.STOPPED,
                        WorkerLifecyclePhase.FAULTED,
                    }:
                        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                    if deadline():
                        self._worker.cancel(CancelReason.SHUTDOWN)
                        reject_text_companion(TextCompanionErrorCode.TIMED_OUT)
                    if cancellation() and not cancelled:
                        self._worker.cancel(CancelReason.USER_REQUESTED)
                        cancelled = True
                    self._worker.pump(0.05)
            finally:
                self._worker.terminate()
            require_companion_active(deadline, cancellation)
            if cancelled and result.outcome is OperationOutcome.SUCCEEDED:
                reject_text_companion(TextCompanionErrorCode.CANCELLED)
            if result.operation_id != operation_id:
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            if result.outcome is OperationOutcome.CANCELLED:
                reject_text_companion(TextCompanionErrorCode.CANCELLED)
            if result.outcome is OperationOutcome.TIMED_OUT:
                reject_text_companion(TextCompanionErrorCode.TIMED_OUT)
            if result.error_category is ErrorCategory.RESOURCE_LIMIT:
                reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)
            if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            reference = result.outputs[0]
            if (
                reference.area is not ReferenceArea.OPERATION_STAGING
                or reference.object_id != output_id
                or reference.operation_id != operation_id
                or reference.content_kind != TEXT_COMPANION_CONTENT_KIND
                or reference.expected_byte_size is None
                or not 1 <= reference.expected_byte_size <= limits.output_bytes
                or reference.expected_sha256 is None
            ):
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            encoded = output.read_exact(reference.expected_byte_size)
            if not hmac.compare_digest(
                hashlib.sha256(encoded).hexdigest(), reference.expected_sha256
            ):
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            try:
                decoded = encoded.decode("utf-8", errors="strict")
                if sanitize_companion_text(decoded).encode("utf-8") != encoded:
                    reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            except UnicodeError:
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            return encoded


def bind_text_companion_extractor(
    worker: WorkerClient, policy: ResourcePolicy, heavy_operations: HeavyOperationCoordinator
) -> BrokeredTextCompanionExtractor:
    return BrokeredTextCompanionExtractor(worker, policy, heavy_operations=heavy_operations)
