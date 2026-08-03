"""Typed and self-validating models for worker RPC protocol version 1."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject

PROTOCOL_NAME: Final = "paperworkshell.worker-rpc"
PROTOCOL_VERSION: Final = 1
MESSAGE_VERSION: Final = 1
MAX_MESSAGE_BYTES: Final = 65_536
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 4_096
MAX_JSON_STRING_CODEPOINTS: Final = 4_096
MAX_JSON_COLLECTION_ITEMS: Final = 256
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_PARAMETERS: Final = 64

_TOKEN_RE: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_CAPABILITY_RE: Final = re.compile(r"[0-9a-f]{64}\Z")


class Direction(StrEnum):
    """Allowed directions across the client/worker trust boundary."""

    CLIENT_TO_WORKER = "client_to_worker"
    WORKER_TO_CLIENT = "worker_to_client"


class MessageType(StrEnum):
    """Closed set of protocol-v1 messages."""

    SESSION_HELLO = "session.hello"
    SESSION_READY = "session.ready"
    OPERATION_START = "operation.start"
    OPERATION_ACCEPTED = "operation.accepted"
    OPERATION_PROGRESS = "operation.progress"
    OPERATION_CANCEL = "operation.cancel"
    OPERATION_CANCEL_ACK = "operation.cancel_ack"
    OPERATION_RESULT = "operation.result"
    SESSION_SHUTDOWN = "session.shutdown"
    SESSION_CLOSED = "session.closed"
    PROTOCOL_ERROR = "protocol.error"
    WORKER_FAULT = "worker.fault"


class ReferenceArea(StrEnum):
    """Logical case-workspace areas; these are never filesystem paths."""

    SOURCE_READ = "source_read"
    ASSET_READ = "asset_read"
    OPERATION_CACHE = "operation_cache"
    OPERATION_TEMP = "operation_temp"
    OPERATION_STAGING = "operation_staging"


class ScalarValueType(StrEnum):
    """Allowlisted scalar parameter encodings."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    TOKEN = "token"
    UUID = "uuid"


class ProgressUnit(StrEnum):
    """Units allowed in progress events."""

    PAGES = "pages"
    ITEMS = "items"
    BYTES = "bytes"
    STEPS = "steps"


class ProgressStage(StrEnum):
    """Closed progress-stage registry for the protocol.noop v1 operation."""

    PROCESSING = "processing"


class CancelReason(StrEnum):
    """Why an operation cancellation was requested."""

    USER_REQUESTED = "user_requested"
    SHUTDOWN = "shutdown"
    SUPERSEDED = "superseded"


class CancelDisposition(StrEnum):
    """Worker disposition of a cancellation request."""

    REQUESTED = "requested"
    ALREADY_REQUESTED = "already_requested"
    ALREADY_TERMINAL = "already_terminal"


class OperationOutcome(StrEnum):
    """Terminal operation outcomes."""

    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ShutdownReason(StrEnum):
    """Why the client is closing the worker session."""

    USER_REQUESTED = "user_requested"
    APPLICATION_EXIT = "application_exit"
    PROTOCOL_FAULT = "protocol_fault"


class ClosedReason(StrEnum):
    """Why the worker reports the session closed."""

    SHUTDOWN_COMPLETE = "shutdown_complete"


class ErrorCategory(StrEnum):
    """Stable, non-sensitive error categories."""

    PROTOCOL = "protocol"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED = "unsupported"
    RESOURCE_LIMIT = "resource_limit"
    DEPENDENCY = "dependency"
    OPERATION = "operation"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """Closed public error-code registry for protocol version 1."""

    RPC_MESSAGE_INVALID = "RPC_MESSAGE_INVALID"
    WORKER_OPERATION_FAILED = "WORKER_OPERATION_FAILED"
    WORKER_OPERATION_TIMED_OUT = "WORKER_OPERATION_TIMED_OUT"
    WORKER_INTERNAL_FAULT = "WORKER_INTERNAL_FAULT"


class RetryAction(StrEnum):
    """Closed set of safe recovery instructions."""

    NEVER = "never"
    RETRY = "retry"
    RESTART_WORKER = "restart_worker"
    USER_ACTION = "user_action"


def _require_uuid4(value: object, location: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject("RPC_INVALID_IDENTIFIER", location, "a canonical UUIDv4 is required")
    return value


def _require_int(
    value: object,
    location: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        reject("RPC_INVALID_INTEGER", location, "an integer within the permitted range is required")
    return value


def _require_token(value: object, location: str, *, max_length: int = 128) -> str:
    if type(value) is not str or len(value) > max_length or _TOKEN_RE.fullmatch(value) is None:
        reject("RPC_INVALID_TOKEN", location, "a lowercase protocol token is required")
    return value


def _require_tuple(value: object, location: str, *, maximum: int) -> tuple[object, ...]:
    if type(value) is not tuple or len(value) > maximum:
        reject("RPC_INVALID_COLLECTION", location, "a bounded tuple is required")
    return cast(tuple[object, ...], value)


def _require_bool(value: object, location: str) -> bool:
    if type(value) is not bool:
        reject("RPC_INVALID_BOOLEAN", location, "a boolean is required")
    return value


def _require_unique(values: tuple[object, ...], location: str) -> None:
    try:
        unique_count = len(set(values))
    except TypeError:
        reject("RPC_INVALID_COLLECTION", location, "collection members must be hashable")
    if unique_count != len(values):
        reject("RPC_DUPLICATE_VALUE", location, "duplicate collection members are not permitted")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class SessionCapability:
    """Per-launch capability secret, with redacted representations and constant-time comparison."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CAPABILITY_RE.fullmatch(self.value) is None:
            reject(
                "RPC_INVALID_CAPABILITY",
                "session_capability",
                "a lowercase 256-bit hexadecimal capability is required",
            )

    def matches(self, other: object) -> bool:
        """Compare two launch capabilities without content-dependent early exit."""

        if type(other) is not SessionCapability:
            return False
        return hmac.compare_digest(self.value.encode("ascii"), other.value.encode("ascii"))

    def __eq__(self, other: object) -> bool:
        # Defining equality without a hash deliberately makes this secret unhashable.
        return self.matches(other)

    def __repr__(self) -> str:
        return "SessionCapability(<redacted>)"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """A named, versioned worker capability."""

    capability_id: str
    capability_version: int

    def __post_init__(self) -> None:
        _require_token(self.capability_id, "payload.capability_id")
        _require_int(self.capability_version, "payload.capability_version", minimum=1)


NOOP_CAPABILITY: Final = CapabilityDescriptor("protocol.noop", 1)
PDF_INSPECT_CAPABILITY: Final = CapabilityDescriptor("pdf.inspect", 1)

PDF_INSPECT_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
PDF_INSPECT_RESULT_HANDLE_PARAMETER: Final = "result_handle"


@dataclass(frozen=True, slots=True)
class TaggedScalar:
    """A closed, explicitly tagged scalar used for parameters and safe error context."""

    name: str
    value_type: ScalarValueType
    value: bool | int | str | UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.name, "payload.parameters.name", max_length=64)
        if type(self.value_type) is not ScalarValueType:
            reject(
                "RPC_INVALID_ENUM",
                "payload.parameters.value_type",
                "an allowed value type is required",
            )
        if self.value_type is ScalarValueType.BOOLEAN:
            if type(self.value) is not bool:
                reject(
                    "RPC_INVALID_SCALAR", "payload.parameters.value", "a boolean value is required"
                )
        elif self.value_type is ScalarValueType.INTEGER:
            _require_int(
                self.value,
                "payload.parameters.value",
                minimum=-MAX_SAFE_INTEGER,
            )
        elif self.value_type is ScalarValueType.TOKEN:
            _require_token(self.value, "payload.parameters.value")
        elif self.value_type is ScalarValueType.UUID:
            _require_uuid4(self.value, "payload.parameters.value")


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Integer limits applied to one worker operation."""

    wall_time_ms: int
    cpu_time_ms: int
    memory_bytes: int
    temporary_storage_bytes: int
    output_bytes: int
    page_count: int
    pixel_count: int
    limits_version: int = 1

    def __post_init__(self) -> None:
        if self.limits_version != 1 or type(self.limits_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.limits_version",
                "resource limits version is unsupported",
            )
        for field_name in (
            "wall_time_ms",
            "cpu_time_ms",
            "memory_bytes",
            "temporary_storage_bytes",
            "output_bytes",
            "page_count",
            "pixel_count",
        ):
            _require_int(getattr(self, field_name), f"payload.{field_name}", minimum=1)


@dataclass(frozen=True, slots=True)
class CaseObjectReference:
    """A logical case object resolved by the trusted workspace broker."""

    area: ReferenceArea
    object_id: UUID
    operation_id: UUID | None
    content_kind: str
    expected_byte_size: int | None
    expected_sha256: str | None
    reference_version: int = 1

    def __post_init__(self) -> None:
        if self.reference_version != 1 or type(self.reference_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.reference_version",
                "reference version is unsupported",
            )
        if type(self.area) is not ReferenceArea:
            reject("RPC_INVALID_ENUM", "payload.area", "an allowed case-object area is required")
        _require_uuid4(self.object_id, "payload.object_id")
        _require_token(self.content_kind, "payload.content_kind")
        if self.expected_byte_size is not None:
            _require_int(self.expected_byte_size, "payload.expected_byte_size")
        if self.expected_sha256 is not None and (
            type(self.expected_sha256) is not str
            or _SHA256_RE.fullmatch(self.expected_sha256) is None
        ):
            reject(
                "RPC_INVALID_DIGEST",
                "payload.expected_sha256",
                "a lowercase SHA-256 digest is required",
            )
        if self.area in {ReferenceArea.SOURCE_READ, ReferenceArea.ASSET_READ}:
            if self.operation_id is not None:
                reject(
                    "RPC_REFERENCE_SCOPE",
                    "payload.operation_id",
                    "read-only source references cannot be operation-scoped",
                )
        else:
            _require_uuid4(self.operation_id, "payload.operation_id")


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """Sanitized public error data with no worker-controlled context values."""

    code: ErrorCode
    category: ErrorCategory
    retry_action: RetryAction
    restart_required: bool
    parameters: tuple[()]
    error_version: int = 1

    def __post_init__(self) -> None:
        if self.error_version != 1 or type(self.error_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.error_version",
                "error payload version is unsupported",
            )
        if type(self.code) is not ErrorCode:
            reject(
                "RPC_INVALID_ERROR", "payload.code", "a registered public error code is required"
            )
        if type(self.category) is not ErrorCategory:
            reject("RPC_INVALID_ENUM", "payload.category", "an allowed error category is required")
        if type(self.retry_action) is not RetryAction:
            reject(
                "RPC_INVALID_ENUM", "payload.retry_action", "an allowed retry action is required"
            )
        restart_required = _require_bool(self.restart_required, "payload.restart_required")
        if restart_required is not (self.retry_action is RetryAction.RESTART_WORKER):
            reject(
                "RPC_INVALID_ERROR",
                "payload.restart_required",
                "restart requirement must match the retry action",
            )
        parameters = _require_tuple(self.parameters, "payload.parameters", maximum=MAX_PARAMETERS)
        if parameters:
            reject(
                "RPC_INVALID_ERROR_PARAMETER",
                "payload.parameters",
                "public protocol-v1 errors cannot carry context values",
            )


@dataclass(frozen=True, slots=True)
class SessionHelloPayload:
    supported_protocol_versions: tuple[int, ...]
    required_capabilities: tuple[CapabilityDescriptor, ...]

    def __post_init__(self) -> None:
        versions = _require_tuple(
            self.supported_protocol_versions,
            "payload.supported_protocol_versions",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if not versions:
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.supported_protocol_versions",
                "at least one version is required",
            )
        for version in versions:
            _require_int(version, "payload.supported_protocol_versions", minimum=1)
        _require_unique(versions, "payload.supported_protocol_versions")
        if PROTOCOL_VERSION not in versions:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.supported_protocol_versions",
                "the current protocol version must be supported",
            )
        capabilities = _require_tuple(
            self.required_capabilities,
            "payload.required_capabilities",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if any(type(capability) is not CapabilityDescriptor for capability in capabilities):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.required_capabilities",
                "capability descriptors are required",
            )
        _require_unique(capabilities, "payload.required_capabilities")


@dataclass(frozen=True, slots=True)
class SessionReadyPayload:
    selected_protocol_version: int
    capabilities: tuple[CapabilityDescriptor, ...]
    max_message_bytes: int
    max_in_flight_operations: int = 1

    def __post_init__(self) -> None:
        if (
            self.selected_protocol_version != PROTOCOL_VERSION
            or type(self.selected_protocol_version) is not int
        ):
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.selected_protocol_version",
                "selected protocol version is unsupported",
            )
        capabilities = _require_tuple(
            self.capabilities,
            "payload.capabilities",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if any(type(capability) is not CapabilityDescriptor for capability in capabilities):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.capabilities",
                "capability descriptors are required",
            )
        _require_unique(capabilities, "payload.capabilities")
        _require_int(
            self.max_message_bytes,
            "payload.max_message_bytes",
            minimum=1,
            maximum=MAX_MESSAGE_BYTES,
        )
        if self.max_in_flight_operations != 1 or type(self.max_in_flight_operations) is not int:
            reject(
                "RPC_UNSUPPORTED_LIMIT",
                "payload.max_in_flight_operations",
                "only one in-flight operation is supported",
            )


@dataclass(frozen=True, slots=True)
class OperationStartPayload:
    operation_kind: str
    operation_version: int
    inputs: tuple[CaseObjectReference, ...]
    parameters: tuple[TaggedScalar, ...]
    requested_limits: ResourceLimits

    def __post_init__(self) -> None:
        _require_token(self.operation_kind, "payload.operation_kind")
        _require_int(self.operation_version, "payload.operation_version", minimum=1)
        inputs = _require_tuple(self.inputs, "payload.inputs", maximum=MAX_JSON_COLLECTION_ITEMS)
        if any(type(reference) is not CaseObjectReference for reference in inputs):
            reject(
                "RPC_INVALID_COLLECTION", "payload.inputs", "case-object references are required"
            )
        parameters = _require_tuple(self.parameters, "payload.parameters", maximum=MAX_PARAMETERS)
        if any(type(parameter) is not TaggedScalar for parameter in parameters):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.parameters",
                "tagged scalar parameters are required",
            )
        _require_unique(
            tuple(parameter.name for parameter in self.parameters), "payload.parameters"
        )
        if type(self.requested_limits) is not ResourceLimits:
            reject("RPC_INVALID_OBJECT", "payload.requested_limits", "resource limits are required")


def validate_pdf_inspect_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``pdf.inspect`` version-1 input contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.inspect version 1 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.content_kind != "pdf"
        or source.expected_byte_size is None
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.inspect version 1 requires one authenticated PDF source reference",
        )

    expected_names = {
        PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
        PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    }
    if (
        len(payload.parameters) != 2
        or {parameter.name for parameter in payload.parameters} != expected_names
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.inspect version 1 requires its two brokered handles",
        )
    handle_values = tuple(parameter.value for parameter in payload.parameters)
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in payload.parameters
        )
        or len(set(handle_values)) != 2
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.inspect brokered handles must be distinct positive integers",
        )


@dataclass(frozen=True, slots=True)
class OperationAcceptedPayload:
    operation_kind: str
    operation_version: int
    effective_limits: ResourceLimits

    def __post_init__(self) -> None:
        _require_token(self.operation_kind, "payload.operation_kind")
        _require_int(self.operation_version, "payload.operation_version", minimum=1)
        if type(self.effective_limits) is not ResourceLimits:
            reject("RPC_INVALID_OBJECT", "payload.effective_limits", "resource limits are required")


@dataclass(frozen=True, slots=True)
class OperationProgressPayload:
    stage_code: ProgressStage = field(repr=False)
    completed_units: int
    total_units: int | None
    unit: ProgressUnit

    def __post_init__(self) -> None:
        if type(self.stage_code) is not ProgressStage:
            reject(
                "RPC_INVALID_ENUM",
                "payload.stage_code",
                "a registered progress stage is required",
            )
        completed = _require_int(self.completed_units, "payload.completed_units")
        if self.total_units is not None:
            total = _require_int(self.total_units, "payload.total_units")
            if completed > total:
                reject(
                    "RPC_INVALID_PROGRESS",
                    "payload.completed_units",
                    "completed units cannot exceed total units",
                )
        if type(self.unit) is not ProgressUnit:
            reject("RPC_INVALID_ENUM", "payload.unit", "an allowed progress unit is required")


@dataclass(frozen=True, slots=True)
class OperationCancelPayload:
    reason: CancelReason

    def __post_init__(self) -> None:
        if type(self.reason) is not CancelReason:
            reject(
                "RPC_INVALID_ENUM", "payload.reason", "an allowed cancellation reason is required"
            )


@dataclass(frozen=True, slots=True)
class OperationCancelAckPayload:
    disposition: CancelDisposition

    def __post_init__(self) -> None:
        if type(self.disposition) is not CancelDisposition:
            reject(
                "RPC_INVALID_ENUM",
                "payload.disposition",
                "an allowed cancellation disposition is required",
            )


@dataclass(frozen=True, slots=True)
class OperationResultPayload:
    outcome: OperationOutcome
    outputs: tuple[CaseObjectReference, ...]
    error: ErrorPayload | None

    def __post_init__(self) -> None:
        if type(self.outcome) is not OperationOutcome:
            reject(
                "RPC_INVALID_ENUM", "payload.outcome", "an allowed operation outcome is required"
            )
        outputs = _require_tuple(self.outputs, "payload.outputs", maximum=MAX_JSON_COLLECTION_ITEMS)
        if any(type(reference) is not CaseObjectReference for reference in outputs):
            reject(
                "RPC_INVALID_COLLECTION", "payload.outputs", "case-object references are required"
            )
        if self.outcome is OperationOutcome.SUCCEEDED:
            if self.error is not None:
                reject(
                    "RPC_INVALID_RESULT",
                    "payload.error",
                    "successful results cannot carry an error",
                )
        elif self.outcome is OperationOutcome.CANCELLED:
            if outputs or self.error is not None:
                reject(
                    "RPC_INVALID_RESULT",
                    "payload",
                    "cancelled results cannot carry outputs or an error",
                )
        elif outputs or type(self.error) is not ErrorPayload:
            reject(
                "RPC_INVALID_RESULT",
                "payload",
                "failed and timed-out results require one error and no outputs",
            )
        elif self.outcome is OperationOutcome.FAILED:
            if self.error.code is not ErrorCode.WORKER_OPERATION_FAILED:
                reject(
                    "RPC_INVALID_ERROR",
                    "payload.error.code",
                    "failed results require the registered failure code",
                )
        elif self.error.code is not ErrorCode.WORKER_OPERATION_TIMED_OUT:
            reject(
                "RPC_INVALID_ERROR",
                "payload.error.code",
                "timed-out results require the registered timeout code",
            )


@dataclass(frozen=True, slots=True)
class SessionShutdownPayload:
    reason: ShutdownReason

    def __post_init__(self) -> None:
        if type(self.reason) is not ShutdownReason:
            reject("RPC_INVALID_ENUM", "payload.reason", "an allowed shutdown reason is required")


@dataclass(frozen=True, slots=True)
class SessionClosedPayload:
    reason: ClosedReason

    def __post_init__(self) -> None:
        if type(self.reason) is not ClosedReason:
            reject("RPC_INVALID_ENUM", "payload.reason", "an allowed close reason is required")


@dataclass(frozen=True, slots=True)
class ProtocolErrorPayload:
    error: ErrorPayload

    def __post_init__(self) -> None:
        if (
            type(self.error) is not ErrorPayload
            or self.error.code is not ErrorCode.RPC_MESSAGE_INVALID
            or self.error.category is not ErrorCategory.PROTOCOL
            or not self.error.restart_required
        ):
            reject(
                "RPC_INVALID_ERROR",
                "payload.error",
                "a restart-required protocol-category error is required",
            )


@dataclass(frozen=True, slots=True)
class WorkerFaultPayload:
    error: ErrorPayload
    invalidated_operation_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.error) is not ErrorPayload
            or self.error.code is not ErrorCode.WORKER_INTERNAL_FAULT
            or self.error.category is not ErrorCategory.INTERNAL
            or not self.error.restart_required
        ):
            reject(
                "RPC_INVALID_ERROR", "payload.error", "a restart-required worker error is required"
            )
        operation_ids = _require_tuple(
            self.invalidated_operation_ids,
            "payload.invalidated_operation_ids",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        for operation_id in operation_ids:
            _require_uuid4(operation_id, "payload.invalidated_operation_ids")
        _require_unique(operation_ids, "payload.invalidated_operation_ids")


type MessagePayload = (
    SessionHelloPayload
    | SessionReadyPayload
    | OperationStartPayload
    | OperationAcceptedPayload
    | OperationProgressPayload
    | OperationCancelPayload
    | OperationCancelAckPayload
    | OperationResultPayload
    | SessionShutdownPayload
    | SessionClosedPayload
    | ProtocolErrorPayload
    | WorkerFaultPayload
)

_PAYLOAD_TYPES: Final = MappingProxyType(
    {
        MessageType.SESSION_HELLO: SessionHelloPayload,
        MessageType.SESSION_READY: SessionReadyPayload,
        MessageType.OPERATION_START: OperationStartPayload,
        MessageType.OPERATION_ACCEPTED: OperationAcceptedPayload,
        MessageType.OPERATION_PROGRESS: OperationProgressPayload,
        MessageType.OPERATION_CANCEL: OperationCancelPayload,
        MessageType.OPERATION_CANCEL_ACK: OperationCancelAckPayload,
        MessageType.OPERATION_RESULT: OperationResultPayload,
        MessageType.SESSION_SHUTDOWN: SessionShutdownPayload,
        MessageType.SESSION_CLOSED: SessionClosedPayload,
        MessageType.PROTOCOL_ERROR: ProtocolErrorPayload,
        MessageType.WORKER_FAULT: WorkerFaultPayload,
    }
)

_FIXED_DIRECTIONS: Final = MappingProxyType(
    {
        MessageType.SESSION_HELLO: Direction.CLIENT_TO_WORKER,
        MessageType.SESSION_READY: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_START: Direction.CLIENT_TO_WORKER,
        MessageType.OPERATION_ACCEPTED: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_PROGRESS: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_CANCEL: Direction.CLIENT_TO_WORKER,
        MessageType.OPERATION_CANCEL_ACK: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_RESULT: Direction.WORKER_TO_CLIENT,
        MessageType.SESSION_SHUTDOWN: Direction.CLIENT_TO_WORKER,
        MessageType.SESSION_CLOSED: Direction.WORKER_TO_CLIENT,
        MessageType.WORKER_FAULT: Direction.WORKER_TO_CLIENT,
    }
)

_SESSION_MESSAGES: Final = frozenset(
    {
        MessageType.SESSION_HELLO,
        MessageType.SESSION_READY,
        MessageType.SESSION_SHUTDOWN,
        MessageType.SESSION_CLOSED,
    }
)
_OPERATION_MESSAGES: Final = frozenset(
    {
        MessageType.OPERATION_START,
        MessageType.OPERATION_ACCEPTED,
        MessageType.OPERATION_PROGRESS,
        MessageType.OPERATION_CANCEL,
        MessageType.OPERATION_CANCEL_ACK,
        MessageType.OPERATION_RESULT,
    }
)


@dataclass(frozen=True, slots=True)
class ProtocolEnvelope:
    """Closed protocol-v1 envelope bound to one launch, case, request, and sequence."""

    protocol: str
    protocol_version: int
    message_version: int
    message_type: MessageType
    direction: Direction
    session_id: UUID
    worker_instance_id: UUID
    session_capability: SessionCapability
    case_id: UUID
    message_id: UUID
    sender_sequence: int
    request_id: UUID | None
    operation_id: UUID | None
    payload: MessagePayload

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL_NAME or type(self.protocol) is not str:
            reject("RPC_UNSUPPORTED_PROTOCOL", "protocol", "protocol identifier is unsupported")
        if self.protocol_version != PROTOCOL_VERSION or type(self.protocol_version) is not int:
            reject("RPC_UNSUPPORTED_VERSION", "protocol_version", "protocol version is unsupported")
        if self.message_version != MESSAGE_VERSION or type(self.message_version) is not int:
            reject("RPC_UNSUPPORTED_VERSION", "message_version", "message version is unsupported")
        if type(self.message_type) is not MessageType:
            reject("RPC_UNKNOWN_MESSAGE", "message_type", "message type is unsupported")
        if type(self.direction) is not Direction:
            reject("RPC_INVALID_ENUM", "direction", "message direction is unsupported")
        for field_name in ("session_id", "worker_instance_id", "case_id", "message_id"):
            _require_uuid4(getattr(self, field_name), field_name)
        if type(self.session_capability) is not SessionCapability:
            reject(
                "RPC_INVALID_CAPABILITY", "session_capability", "a session capability is required"
            )
        _require_int(self.sender_sequence, "sender_sequence")

        expected_payload = _PAYLOAD_TYPES[self.message_type]
        if type(self.payload) is not expected_payload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "payload does not match the message type")
        expected_direction = _FIXED_DIRECTIONS.get(self.message_type)
        if expected_direction is not None and self.direction is not expected_direction:
            reject(
                "RPC_DIRECTION_MISMATCH", "direction", "message direction is invalid for its type"
            )

        if self.message_type in _SESSION_MESSAGES:
            _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                reject(
                    "RPC_INVALID_IDENTIFIER",
                    "operation_id",
                    "session messages cannot name an operation",
                )
        elif self.message_type in _OPERATION_MESSAGES:
            _require_uuid4(self.request_id, "request_id")
            operation_id = _require_uuid4(self.operation_id, "operation_id")
            self._validate_reference_scope(operation_id)
        elif self.message_type is MessageType.WORKER_FAULT:
            if self.request_id is not None:
                _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                reject(
                    "RPC_INVALID_IDENTIFIER",
                    "operation_id",
                    "worker faults cannot name one operation",
                )
        else:
            if self.request_id is not None:
                _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                _require_uuid4(self.operation_id, "operation_id")

    def _validate_reference_scope(self, operation_id: UUID) -> None:
        if type(self.payload) is OperationStartPayload:
            for reference in self.payload.inputs:
                if reference.area not in {ReferenceArea.SOURCE_READ, ReferenceArea.ASSET_READ}:
                    reject(
                        "RPC_REFERENCE_SCOPE",
                        "payload.inputs",
                        "operation inputs must be read-only source or asset references",
                    )
        elif type(self.payload) is OperationResultPayload:
            for reference in self.payload.outputs:
                if (
                    reference.area is not ReferenceArea.OPERATION_STAGING
                    or reference.operation_id != operation_id
                    or reference.expected_byte_size is None
                    or reference.expected_sha256 is None
                ):
                    reject(
                        "RPC_REFERENCE_SCOPE",
                        "payload.outputs",
                        "operation outputs must be verified staging references for this operation",
                    )


type ProtocolMessage = ProtocolEnvelope
