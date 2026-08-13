"""Immutable conversation-state validation for worker RPC protocol version 1."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    MAX_SAFE_INTEGER,
    NOOP_CAPABILITY,
    OCR_PROCESS_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    CancelDisposition,
    CapabilityDescriptor,
    ClosedReason,
    Direction,
    MessageType,
    OperationAcceptedPayload,
    OperationCancelAckPayload,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressUnit,
    ProtocolEnvelope,
    ProtocolErrorPayload,
    ResourceLimits,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    WorkerFaultPayload,
    image_normalize_bound_object_ids,
    image_transform_bound_object_ids,
    ocr_process_bound_object_ids,
    pdf_export_bound_object_ids,
    pdf_thumbnail_bound_object_ids,
    validate_image_normalize_result,
    validate_image_normalize_start,
    validate_image_transform_result,
    validate_image_transform_start,
    validate_ocr_process_result,
    validate_ocr_process_start,
    validate_pdf_export_result,
    validate_pdf_export_start,
    validate_pdf_inspect_start,
    validate_pdf_thumbnail_result,
    validate_pdf_thumbnail_start,
)

MAX_TRACKED_MESSAGES: Final = 4_096
MAX_TRACKED_OPERATIONS: Final = 1_024


class ConversationPhase(StrEnum):
    """Lifecycle phases for a bound protocol conversation."""

    NEW = "new"
    NEGOTIATING = "negotiating"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class _ActiveOperation:
    operation_id: UUID
    start_request_id: UUID
    operation_kind: str
    operation_version: int
    requested_limits: ResourceLimits
    image_normalize_output_object_id: UUID | None = None
    image_transform_output_object_id: UUID | None = None
    ocr_output_object_id: UUID | None = None
    thumbnail_cache_object_id: UUID | None = None
    export_output_object_id: UUID | None = None
    accepted: bool = False
    pending_cancel_request_ids: frozenset[UUID] = field(default_factory=frozenset)
    progress_seen: bool = False
    completed_units: int = 0
    total_units: int | None = None
    progress_unit: str | None = None


@dataclass(frozen=True, slots=True)
class _TerminalOperation:
    operation_id: UUID
    start_request_id: UUID
    pending_cancel_request_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class ConversationState:
    """Pure validator for one session, worker launch, capability, and active case."""

    session_id: UUID
    worker_instance_id: UUID
    session_capability: SessionCapability = field(repr=False)
    case_id: UUID
    registered_capabilities: tuple[CapabilityDescriptor, ...] = (NOOP_CAPABILITY,)
    phase: ConversationPhase = ConversationPhase.NEW
    next_client_sequence: int = 0
    next_worker_sequence: int = 0
    seen_message_ids: frozenset[UUID] = field(default_factory=frozenset)
    seen_request_ids: frozenset[UUID] = field(default_factory=frozenset)
    hello_request_id: UUID | None = None
    supported_protocol_versions: tuple[int, ...] = ()
    required_capabilities: tuple[CapabilityDescriptor, ...] = ()
    negotiated_capabilities: tuple[CapabilityDescriptor, ...] = ()
    active_operation: _ActiveOperation | None = None
    terminal_operations: tuple[_TerminalOperation, ...] = ()
    shutdown_request_id: UUID | None = None

    def __post_init__(self) -> None:
        for value in (self.session_id, self.worker_instance_id, self.case_id):
            if type(value) is not UUID or value.version != 4:
                reject("RPC_INVALID_IDENTIFIER", "conversation", "conversation IDs must be UUIDv4")
        if type(self.session_capability) is not SessionCapability:
            reject("RPC_INVALID_CAPABILITY", "conversation", "a session capability is required")
        if type(self.phase) is not ConversationPhase:
            reject("RPC_INVALID_STATE", "conversation", "conversation phase is invalid")
        if (
            type(self.registered_capabilities) is not tuple
            or any(
                type(capability) is not CapabilityDescriptor
                for capability in self.registered_capabilities
            )
            or len(set(self.registered_capabilities)) != len(self.registered_capabilities)
        ):
            reject("RPC_INVALID_STATE", "conversation", "registered capabilities are invalid")
        for sequence_value in (self.next_client_sequence, self.next_worker_sequence):
            if type(sequence_value) is not int or not 0 <= sequence_value <= MAX_SAFE_INTEGER:
                reject("RPC_INVALID_SEQUENCE", "conversation", "conversation sequence is invalid")

    @property
    def active_operation_id(self) -> UUID | None:
        """Return the only active operation identifier, if any."""

        return None if self.active_operation is None else self.active_operation.operation_id

    def accept(self, message: ProtocolEnvelope) -> ConversationState:
        """Return the state after one valid message; reject without mutating this state."""

        if type(message) is not ProtocolEnvelope:
            reject("RPC_INVALID_TYPE", "$", "a protocol envelope is required")
        if self.phase in {ConversationPhase.CLOSED, ConversationPhase.FAULTED}:
            reject("RPC_SESSION_TERMINAL", "message_type", "the session is already terminal")
        self._validate_binding(message)
        if message.message_id in self.seen_message_ids:
            reject(
                "RPC_REPLAYED_MESSAGE", "message_id", "the message identifier was already accepted"
            )
        if len(self.seen_message_ids) >= MAX_TRACKED_MESSAGES:
            reject(
                "RPC_STATE_RESOURCE_LIMIT", "message_id", "the session message limit was reached"
            )
        expected_sequence = (
            self.next_client_sequence
            if message.direction is Direction.CLIENT_TO_WORKER
            else self.next_worker_sequence
        )
        if message.sender_sequence != expected_sequence:
            reject(
                "RPC_SEQUENCE_MISMATCH",
                "sender_sequence",
                "the sender sequence is not the next value",
            )
        if expected_sequence == MAX_SAFE_INTEGER:
            reject(
                "RPC_STATE_RESOURCE_LIMIT", "sender_sequence", "the sender sequence is exhausted"
            )
        advanced = replace(
            self,
            next_client_sequence=(
                expected_sequence + 1
                if message.direction is Direction.CLIENT_TO_WORKER
                else self.next_client_sequence
            ),
            next_worker_sequence=(
                expected_sequence + 1
                if message.direction is Direction.WORKER_TO_CLIENT
                else self.next_worker_sequence
            ),
            seen_message_ids=self.seen_message_ids | {message.message_id},
        )
        return advanced._transition(message)

    def _validate_binding(self, message: ProtocolEnvelope) -> None:
        if (
            message.session_id != self.session_id
            or message.worker_instance_id != self.worker_instance_id
            or message.case_id != self.case_id
        ):
            reject("RPC_BINDING_MISMATCH", "$", "message identity does not match this conversation")
        if not message.session_capability.matches(self.session_capability):
            reject(
                "RPC_CAPABILITY_MISMATCH",
                "session_capability",
                "message capability does not match this conversation",
            )

    def _transition(self, message: ProtocolEnvelope) -> ConversationState:
        if message.message_type is MessageType.PROTOCOL_ERROR:
            return self._accept_protocol_error(message)
        if message.message_type is MessageType.WORKER_FAULT:
            return self._accept_worker_fault(message)
        if self.phase is ConversationPhase.NEW:
            return self._accept_hello(message)
        if self.phase is ConversationPhase.NEGOTIATING:
            return self._accept_ready(message)
        if self.phase is ConversationPhase.CLOSING:
            return self._accept_closed(message)
        if self.phase is not ConversationPhase.READY:
            reject(
                "RPC_INVALID_STATE", "message_type", "the message is invalid in the current state"
            )
        if message.message_type is MessageType.OPERATION_START:
            return self._accept_operation_start(message)
        if message.message_type is MessageType.OPERATION_ACCEPTED:
            return self._accept_operation_accepted(message)
        if message.message_type is MessageType.OPERATION_PROGRESS:
            return self._accept_operation_progress(message)
        if message.message_type is MessageType.OPERATION_CANCEL:
            return self._accept_operation_cancel(message)
        if message.message_type is MessageType.OPERATION_CANCEL_ACK:
            return self._accept_operation_cancel_ack(message)
        if message.message_type is MessageType.OPERATION_RESULT:
            return self._accept_operation_result(message)
        if message.message_type is MessageType.SESSION_SHUTDOWN:
            return self._accept_shutdown(message)
        reject("RPC_INVALID_STATE", "message_type", "the message is invalid in the current state")

    def _claim_request(self, request_id: UUID) -> ConversationState:
        if request_id in self.seen_request_ids:
            reject("RPC_REPLAYED_REQUEST", "request_id", "the request identifier was already used")
        return replace(self, seen_request_ids=self.seen_request_ids | {request_id})

    def _accept_hello(self, message: ProtocolEnvelope) -> ConversationState:
        if message.message_type is not MessageType.SESSION_HELLO:
            reject(
                "RPC_NEGOTIATION_REQUIRED",
                "message_type",
                "session hello must be the first message",
            )
        payload = message.payload
        if type(payload) is not SessionHelloPayload or message.request_id is None:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "session hello payload is required")
        claimed = self._claim_request(message.request_id)
        return replace(
            claimed,
            phase=ConversationPhase.NEGOTIATING,
            hello_request_id=message.request_id,
            supported_protocol_versions=payload.supported_protocol_versions,
            required_capabilities=payload.required_capabilities,
        )

    def _accept_ready(self, message: ProtocolEnvelope) -> ConversationState:
        if message.message_type is not MessageType.SESSION_READY:
            reject(
                "RPC_NEGOTIATION_REQUIRED",
                "message_type",
                "session ready must complete negotiation",
            )
        payload = message.payload
        if type(payload) is not SessionReadyPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "session ready payload is required")
        if message.request_id != self.hello_request_id:
            reject(
                "RPC_REQUEST_MISMATCH", "request_id", "session ready does not match session hello"
            )
        if payload.selected_protocol_version not in self.supported_protocol_versions:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.selected_protocol_version",
                "protocol negotiation failed",
            )
        if any(capability not in payload.capabilities for capability in self.required_capabilities):
            reject(
                "RPC_CAPABILITY_UNAVAILABLE",
                "payload.capabilities",
                "a required capability is unavailable",
            )
        if any(
            capability not in self.registered_capabilities for capability in payload.capabilities
        ):
            reject(
                "RPC_CAPABILITY_UNAVAILABLE",
                "payload.capabilities",
                "the worker advertised an unregistered capability",
            )
        return replace(
            self,
            phase=ConversationPhase.READY,
            negotiated_capabilities=payload.capabilities,
        )

    def _accept_operation_start(self, message: ProtocolEnvelope) -> ConversationState:
        if self.active_operation is not None:
            reject("RPC_OPERATION_IN_FLIGHT", "operation_id", "only one operation may be active")
        if len(self.terminal_operations) >= MAX_TRACKED_OPERATIONS:
            reject(
                "RPC_STATE_RESOURCE_LIMIT",
                "operation_id",
                "the operation history limit was reached",
            )
        payload = message.payload
        if (
            type(payload) is not OperationStartPayload
            or message.request_id is None
            or message.operation_id is None
        ):
            reject("RPC_PAYLOAD_MISMATCH", "payload", "operation start payload is required")
        descriptor = CapabilityDescriptor(payload.operation_kind, payload.operation_version)
        if descriptor not in self.negotiated_capabilities:
            reject(
                "RPC_CAPABILITY_UNAVAILABLE",
                "payload.operation_kind",
                "the operation was not negotiated",
            )
        if descriptor not in self.registered_capabilities:
            reject(
                "RPC_UNSUPPORTED_OPERATION",
                "payload.operation_kind",
                "the operation is not registered",
            )
        if descriptor == NOOP_CAPABILITY and (payload.inputs or payload.parameters):
            reject(
                "RPC_INVALID_OPERATION", "payload", "protocol.noop accepts no inputs or parameters"
            )
        if descriptor == PDF_INSPECT_CAPABILITY:
            validate_pdf_inspect_start(payload)
        image_normalize_output_object_id: UUID | None = None
        image_transform_output_object_id: UUID | None = None
        ocr_output_object_id: UUID | None = None
        thumbnail_cache_object_id: UUID | None = None
        export_output_object_id: UUID | None = None
        if descriptor == IMAGE_NORMALIZE_CAPABILITY:
            validate_image_normalize_start(payload)
            image_bound_object_ids = image_normalize_bound_object_ids(payload)
            normalize_correlated_ids = (
                self.case_id,
                message.operation_id,
                *image_bound_object_ids,
            )
            if len(set(normalize_correlated_ids)) != len(normalize_correlated_ids):
                reject(
                    "RPC_INVALID_OPERATION",
                    "operation_id",
                    "image.normalize case, operation, and object identifiers must be distinct",
                )
            image_normalize_output_object_id = image_bound_object_ids[1]
        if descriptor == IMAGE_TRANSFORM_CAPABILITY:
            validate_image_transform_start(payload)
            transform_bound_object_ids = image_transform_bound_object_ids(payload)
            transform_correlated_ids = (
                self.case_id,
                message.operation_id,
                *transform_bound_object_ids,
            )
            if len(set(transform_correlated_ids)) != len(transform_correlated_ids):
                reject(
                    "RPC_INVALID_OPERATION",
                    "operation_id",
                    "image.transform case, operation, and object identifiers must be distinct",
                )
            image_transform_output_object_id = transform_bound_object_ids[2]
        if descriptor == OCR_PROCESS_CAPABILITY:
            validate_ocr_process_start(payload)
            ocr_bound_object_ids = ocr_process_bound_object_ids(payload)
            ocr_correlated_ids = (
                self.case_id,
                message.operation_id,
                *ocr_bound_object_ids,
            )
            if len(set(ocr_correlated_ids)) != len(ocr_correlated_ids):
                reject(
                    "RPC_INVALID_OPERATION",
                    "operation_id",
                    "ocr.process case, operation, and object identifiers must be distinct",
                )
            ocr_output_object_id = ocr_bound_object_ids[1]
        if descriptor == PDF_THUMBNAIL_CAPABILITY:
            validate_pdf_thumbnail_start(payload)
            thumbnail_bound_object_ids = pdf_thumbnail_bound_object_ids(payload)
            if message.operation_id in thumbnail_bound_object_ids:
                reject(
                    "RPC_INVALID_OPERATION",
                    "operation_id",
                    "pdf.thumbnail operation and object identifiers must be distinct",
                )
            thumbnail_cache_object_id = thumbnail_bound_object_ids[2]
        if descriptor == PDF_EXPORT_CAPABILITY:
            validate_pdf_export_start(payload)
            export_bound_object_ids = pdf_export_bound_object_ids(payload)
            if payload.inputs[0].operation_id != message.operation_id:
                reject(
                    "RPC_INVALID_OPERATION",
                    "payload.inputs",
                    "pdf.export plan scope must match its operation",
                )
            if message.operation_id in export_bound_object_ids:
                reject(
                    "RPC_INVALID_OPERATION",
                    "operation_id",
                    "pdf.export operation and object identifiers must be distinct",
                )
            export_output_object_id = export_bound_object_ids[-1]
        if any(record.operation_id == message.operation_id for record in self.terminal_operations):
            reject(
                "RPC_REPLAYED_OPERATION",
                "operation_id",
                "the operation identifier was already used",
            )
        claimed = self._claim_request(message.request_id)
        return replace(
            claimed,
            active_operation=_ActiveOperation(
                operation_id=message.operation_id,
                start_request_id=message.request_id,
                operation_kind=payload.operation_kind,
                operation_version=payload.operation_version,
                requested_limits=payload.requested_limits,
                image_normalize_output_object_id=image_normalize_output_object_id,
                image_transform_output_object_id=image_transform_output_object_id,
                ocr_output_object_id=ocr_output_object_id,
                thumbnail_cache_object_id=thumbnail_cache_object_id,
                export_output_object_id=export_output_object_id,
            ),
        )

    def _require_active(self, message: ProtocolEnvelope) -> _ActiveOperation:
        active = self.active_operation
        if active is None or message.operation_id != active.operation_id:
            if any(
                terminal.operation_id == message.operation_id
                for terminal in self.terminal_operations
            ):
                reject(
                    "RPC_OPERATION_TERMINAL", "operation_id", "the operation is already terminal"
                )
            reject(
                "RPC_UNKNOWN_OPERATION",
                "operation_id",
                "the message does not match the active operation",
            )
        return active

    def _accept_operation_accepted(self, message: ProtocolEnvelope) -> ConversationState:
        active = self._require_active(message)
        payload = message.payload
        if type(payload) is not OperationAcceptedPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "operation accepted payload is required")
        if active.accepted:
            reject("RPC_INVALID_STATE", "message_type", "the operation was already accepted")
        if message.request_id != active.start_request_id:
            reject(
                "RPC_REQUEST_MISMATCH",
                "request_id",
                "operation response does not match its start request",
            )
        if (
            payload.operation_kind != active.operation_kind
            or payload.operation_version != active.operation_version
        ):
            reject(
                "RPC_OPERATION_MISMATCH",
                "payload",
                "accepted operation descriptor does not match its request",
            )
        if not _limits_within(payload.effective_limits, active.requested_limits):
            reject(
                "RPC_LIMIT_ESCALATION",
                "payload.effective_limits",
                "effective limits exceed requested limits",
            )
        return replace(self, active_operation=replace(active, accepted=True))

    def _accept_operation_progress(self, message: ProtocolEnvelope) -> ConversationState:
        active = self._require_active(message)
        payload = message.payload
        if type(payload) is not OperationProgressPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "operation progress payload is required")
        if not active.accepted:
            reject(
                "RPC_INVALID_STATE", "message_type", "progress cannot precede operation acceptance"
            )
        if message.request_id != active.start_request_id:
            reject(
                "RPC_REQUEST_MISMATCH", "request_id", "progress does not match the start request"
            )
        if active.operation_kind == OCR_PROCESS_CAPABILITY.capability_id and (
            payload.unit is not ProgressUnit.PAGES
            or payload.total_units != active.requested_limits.page_count
        ):
            reject(
                "RPC_INVALID_PROGRESS",
                "payload",
                "ocr.process progress must use its exact numeric page total",
            )
        if active.progress_seen:
            if payload.completed_units < active.completed_units:
                reject(
                    "RPC_PROGRESS_REGRESSION",
                    "payload.completed_units",
                    "progress cannot move backwards",
                )
            if active.progress_unit != payload.unit.value:
                reject("RPC_PROGRESS_REGRESSION", "payload.unit", "progress units cannot change")
            if active.total_units is not None and payload.total_units != active.total_units:
                reject(
                    "RPC_PROGRESS_REGRESSION",
                    "payload.total_units",
                    "a known progress total cannot change",
                )
        return replace(
            self,
            active_operation=replace(
                active,
                progress_seen=True,
                completed_units=payload.completed_units,
                total_units=payload.total_units,
                progress_unit=payload.unit.value,
            ),
        )

    def _accept_operation_cancel(self, message: ProtocolEnvelope) -> ConversationState:
        if message.request_id is None or message.operation_id is None:
            reject("RPC_INVALID_IDENTIFIER", "request_id", "cancellation identifiers are required")
        claimed = self._claim_request(message.request_id)
        active = claimed.active_operation
        if active is not None and active.operation_id == message.operation_id:
            return replace(
                claimed,
                active_operation=replace(
                    active,
                    pending_cancel_request_ids=(
                        active.pending_cancel_request_ids | {message.request_id}
                    ),
                ),
            )
        terminal_index = claimed._terminal_index(message.operation_id)
        if terminal_index is None:
            reject(
                "RPC_UNKNOWN_OPERATION",
                "operation_id",
                "cancellation does not match a known operation",
            )
        terminal = claimed.terminal_operations[terminal_index]
        updated_terminal = replace(
            terminal,
            pending_cancel_request_ids=terminal.pending_cancel_request_ids | {message.request_id},
        )
        return claimed._replace_terminal(terminal_index, updated_terminal)

    def _accept_operation_cancel_ack(self, message: ProtocolEnvelope) -> ConversationState:
        if message.request_id is None or message.operation_id is None:
            reject("RPC_INVALID_IDENTIFIER", "request_id", "cancellation identifiers are required")
        payload = message.payload
        if type(payload) is not OperationCancelAckPayload:
            reject(
                "RPC_PAYLOAD_MISMATCH",
                "payload",
                "cancellation acknowledgement payload is required",
            )
        active = self.active_operation
        if active is not None and active.operation_id == message.operation_id:
            if message.request_id not in active.pending_cancel_request_ids:
                reject(
                    "RPC_REQUEST_MISMATCH",
                    "request_id",
                    "acknowledgement does not match a pending cancellation",
                )
            if payload.disposition not in {
                CancelDisposition.REQUESTED,
                CancelDisposition.ALREADY_REQUESTED,
            }:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.disposition",
                    "disposition is invalid for an active operation",
                )
            return replace(
                self,
                active_operation=replace(
                    active,
                    pending_cancel_request_ids=(
                        active.pending_cancel_request_ids - {message.request_id}
                    ),
                ),
            )
        terminal_index = self._terminal_index(message.operation_id)
        if terminal_index is None:
            reject(
                "RPC_UNKNOWN_OPERATION",
                "operation_id",
                "acknowledgement does not match a known operation",
            )
        terminal = self.terminal_operations[terminal_index]
        if message.request_id not in terminal.pending_cancel_request_ids:
            reject(
                "RPC_REQUEST_MISMATCH",
                "request_id",
                "acknowledgement does not match a pending cancellation",
            )
        if payload.disposition is not CancelDisposition.ALREADY_TERMINAL:
            reject(
                "RPC_INVALID_STATE",
                "payload.disposition",
                "a terminal operation requires already_terminal",
            )
        return self._replace_terminal(
            terminal_index,
            replace(
                terminal,
                pending_cancel_request_ids=terminal.pending_cancel_request_ids
                - {message.request_id},
            ),
        )

    def _accept_operation_result(self, message: ProtocolEnvelope) -> ConversationState:
        active = self._require_active(message)
        payload = message.payload
        if type(payload) is not OperationResultPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "operation result payload is required")
        if not active.accepted:
            reject(
                "RPC_INVALID_STATE", "message_type", "a result cannot precede operation acceptance"
            )
        if message.request_id != active.start_request_id:
            reject("RPC_REQUEST_MISMATCH", "request_id", "result does not match the start request")
        if (
            active.operation_kind
            in {
                NOOP_CAPABILITY.capability_id,
                PDF_INSPECT_CAPABILITY.capability_id,
            }
            and payload.outputs
        ):
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "this operation cannot produce RPC object outputs",
            )
        if active.operation_kind == PDF_THUMBNAIL_CAPABILITY.capability_id:
            if active.thumbnail_cache_object_id is None:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.outputs",
                    "pdf.thumbnail result binding is unavailable",
                )
            validate_pdf_thumbnail_result(
                payload,
                operation_id=active.operation_id,
                cache_object_id=active.thumbnail_cache_object_id,
                maximum_output_bytes=active.requested_limits.output_bytes,
            )
        if (
            CapabilityDescriptor(active.operation_kind, active.operation_version)
            == IMAGE_NORMALIZE_CAPABILITY
        ):
            if active.image_normalize_output_object_id is None:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.outputs",
                    "image.normalize result binding is unavailable",
                )
            validate_image_normalize_result(
                payload,
                operation_id=active.operation_id,
                output_object_id=active.image_normalize_output_object_id,
                maximum_output_bytes=active.requested_limits.output_bytes,
            )
        if (
            CapabilityDescriptor(active.operation_kind, active.operation_version)
            == IMAGE_TRANSFORM_CAPABILITY
        ):
            if active.image_transform_output_object_id is None:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.outputs",
                    "image.transform result binding is unavailable",
                )
            validate_image_transform_result(
                payload,
                operation_id=active.operation_id,
                output_object_id=active.image_transform_output_object_id,
                maximum_output_bytes=active.requested_limits.output_bytes,
            )
        if (
            CapabilityDescriptor(active.operation_kind, active.operation_version)
            == OCR_PROCESS_CAPABILITY
        ):
            if active.ocr_output_object_id is None:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.outputs",
                    "ocr.process result binding is unavailable",
                )
            validate_ocr_process_result(
                payload,
                operation_id=active.operation_id,
                output_object_id=active.ocr_output_object_id,
                maximum_output_bytes=active.requested_limits.output_bytes,
            )
        if active.operation_kind == PDF_EXPORT_CAPABILITY.capability_id:
            if active.export_output_object_id is None:
                reject(
                    "RPC_INVALID_STATE",
                    "payload.outputs",
                    "pdf.export result binding is unavailable",
                )
            validate_pdf_export_result(
                payload,
                operation_id=active.operation_id,
                output_object_id=active.export_output_object_id,
                maximum_output_bytes=active.requested_limits.output_bytes,
            )
        terminal = _TerminalOperation(
            operation_id=active.operation_id,
            start_request_id=active.start_request_id,
            pending_cancel_request_ids=active.pending_cancel_request_ids,
        )
        return replace(
            self,
            phase=(
                ConversationPhase.FAULTED
                if payload.error is not None and payload.error.restart_required
                else self.phase
            ),
            active_operation=None,
            terminal_operations=(*self.terminal_operations, terminal),
        )

    def _accept_shutdown(self, message: ProtocolEnvelope) -> ConversationState:
        if self.active_operation is not None or any(
            terminal.pending_cancel_request_ids for terminal in self.terminal_operations
        ):
            reject(
                "RPC_OPERATION_IN_FLIGHT", "message_type", "operations must settle before shutdown"
            )
        if message.request_id is None:
            reject(
                "RPC_INVALID_IDENTIFIER", "request_id", "shutdown request identifier is required"
            )
        claimed = self._claim_request(message.request_id)
        return replace(
            claimed,
            phase=ConversationPhase.CLOSING,
            shutdown_request_id=message.request_id,
        )

    def _accept_closed(self, message: ProtocolEnvelope) -> ConversationState:
        if message.message_type is not MessageType.SESSION_CLOSED:
            reject("RPC_INVALID_STATE", "message_type", "session closed must complete shutdown")
        payload = message.payload
        if type(payload) is not SessionClosedPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "session closed payload is required")
        if message.request_id != self.shutdown_request_id:
            reject("RPC_REQUEST_MISMATCH", "request_id", "session closed does not match shutdown")
        if payload.reason is not ClosedReason.SHUTDOWN_COMPLETE:
            reject(
                "RPC_INVALID_STATE",
                "payload.reason",
                "normal shutdown must report shutdown_complete",
            )
        return replace(self, phase=ConversationPhase.CLOSED)

    def _accept_protocol_error(self, message: ProtocolEnvelope) -> ConversationState:
        if type(message.payload) is not ProtocolErrorPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "protocol error payload is required")
        self._validate_optional_correlation(message)
        return replace(self, phase=ConversationPhase.FAULTED, active_operation=None)

    def _accept_worker_fault(self, message: ProtocolEnvelope) -> ConversationState:
        payload = message.payload
        if type(payload) is not WorkerFaultPayload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "worker fault payload is required")
        self._validate_optional_correlation(message)
        expected_invalidated = (
            frozenset()
            if self.active_operation is None
            else frozenset({self.active_operation.operation_id})
        )
        if frozenset(payload.invalidated_operation_ids) != expected_invalidated:
            reject(
                "RPC_OPERATION_MISMATCH",
                "payload.invalidated_operation_ids",
                "worker fault invalidation does not match active operations",
            )
        return replace(self, phase=ConversationPhase.FAULTED, active_operation=None)

    def _validate_optional_correlation(self, message: ProtocolEnvelope) -> None:
        if message.request_id is not None and message.request_id not in self.seen_request_ids:
            reject("RPC_REQUEST_MISMATCH", "request_id", "error does not match a known request")
        if message.operation_id is not None:
            known = (
                self.active_operation is not None
                and self.active_operation.operation_id == message.operation_id
            ) or any(
                terminal.operation_id == message.operation_id
                for terminal in self.terminal_operations
            )
            if not known:
                reject(
                    "RPC_UNKNOWN_OPERATION",
                    "operation_id",
                    "error does not match a known operation",
                )

    def _terminal_index(self, operation_id: UUID) -> int | None:
        for index, terminal in enumerate(self.terminal_operations):
            if terminal.operation_id == operation_id:
                return index
        return None

    def _replace_terminal(
        self,
        index: int,
        terminal: _TerminalOperation,
    ) -> ConversationState:
        operations = list(self.terminal_operations)
        operations[index] = terminal
        return replace(self, terminal_operations=tuple(operations))


def _limits_within(effective: ResourceLimits, requested: ResourceLimits) -> bool:
    return all(
        getattr(effective, field_name) <= getattr(requested, field_name)
        for field_name in (
            "wall_time_ms",
            "cpu_time_ms",
            "memory_bytes",
            "temporary_storage_bytes",
            "output_bytes",
            "page_count",
            "pixel_count",
        )
    )
