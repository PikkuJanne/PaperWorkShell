"""Strict bounded JSON codec and operation registry for worker RPC protocol v1."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    MAX_JSON_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_CODEPOINTS,
    MAX_MESSAGE_BYTES,
    MAX_SAFE_INTEGER,
    MESSAGE_VERSION,
    NOOP_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    CancelDisposition,
    CancelReason,
    CapabilityDescriptor,
    CaseObjectReference,
    ClosedReason,
    Direction,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    MessagePayload,
    MessageType,
    OperationAcceptedPayload,
    OperationCancelAckPayload,
    OperationCancelPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ProtocolEnvelope,
    ProtocolErrorPayload,
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    ScalarValueType,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
    TaggedScalar,
    WorkerFaultPayload,
    validate_pdf_inspect_start,
)

_ENVELOPE_FIELDS: Final = frozenset(
    {
        "protocol",
        "protocol_version",
        "message_version",
        "message_type",
        "direction",
        "session_id",
        "worker_instance_id",
        "session_capability",
        "case_id",
        "message_id",
        "sender_sequence",
        "request_id",
        "operation_id",
        "payload",
    }
)


class _DuplicateFieldError(Exception):
    pass


class _NumberError(Exception):
    pass


class _StructureError(Exception):
    pass


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len(pairs) > MAX_JSON_COLLECTION_ITEMS:
        raise _StructureError
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError
        result[key] = value
    return result


def _reject_float(_value: str) -> float:
    raise _NumberError


def _parse_bounded_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 16:
        raise _NumberError
    parsed = int(value, 10)
    if not -MAX_SAFE_INTEGER <= parsed <= MAX_SAFE_INTEGER:
        raise _NumberError
    return parsed


def _reject_constant(_value: str) -> object:
    raise _NumberError


def _decode_json(raw: bytes) -> object:
    if type(raw) is not bytes:
        reject("RPC_INVALID_TYPE", "$", "the encoded message must be bytes")
    if len(raw) > MAX_MESSAGE_BYTES:
        reject("RPC_MESSAGE_TOO_LARGE", "$", "the encoded message exceeds the byte limit")
    if not raw:
        reject("RPC_MALFORMED_JSON", "$", "the encoded message is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        reject("RPC_INVALID_UTF8", "$", "the encoded message is not valid UTF-8")
    try:
        value: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_float=_reject_float,
            parse_int=_parse_bounded_integer,
            parse_constant=_reject_constant,
        )
    except _DuplicateFieldError:
        reject("RPC_DUPLICATE_FIELD", "$", "duplicate object fields are not permitted")
    except _NumberError:
        reject(
            "RPC_INVALID_NUMBER", "$", "floating-point or out-of-range numbers are not permitted"
        )
    except _StructureError:
        reject("RPC_STRUCTURE_LIMIT", "$", "a JSON object exceeds the item limit")
    except json.JSONDecodeError, RecursionError, UnicodeError:
        reject("RPC_MALFORMED_JSON", "$", "the encoded message is not valid JSON")
    _validate_json_bounds(value)
    return value


def _validate_json_bounds(root: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(root, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            reject("RPC_STRUCTURE_LIMIT", "$", "the JSON structure exceeds protocol limits")
        if type(value) is dict:
            mapping = cast(dict[str, object], value)
            if len(mapping) > MAX_JSON_COLLECTION_ITEMS:
                reject("RPC_STRUCTURE_LIMIT", "$", "a JSON object exceeds the item limit")
            for key, member in mapping.items():
                _validate_json_string(key)
                nodes += 1
                if nodes > MAX_JSON_NODES:
                    reject("RPC_STRUCTURE_LIMIT", "$", "the JSON structure exceeds protocol limits")
                stack.append((member, depth + 1))
        elif type(value) is list:
            members = cast(list[object], value)
            if len(members) > MAX_JSON_COLLECTION_ITEMS:
                reject("RPC_STRUCTURE_LIMIT", "$", "a JSON array exceeds the item limit")
            stack.extend((member, depth + 1) for member in members)
        elif type(value) is str:
            _validate_json_string(value)
        elif value is not None and type(value) not in {bool, int}:
            reject("RPC_MALFORMED_JSON", "$", "the JSON structure contains an invalid value")


def _validate_json_string(value: str) -> None:
    if len(value) > MAX_JSON_STRING_CODEPOINTS:
        reject("RPC_STRUCTURE_LIMIT", "$", "a JSON string exceeds the character limit")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        reject(
            "RPC_INVALID_STRING",
            "$",
            "control, formatting, and surrogate characters are not permitted",
        )


def _object(value: object, location: str, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        reject("RPC_INVALID_OBJECT", location, "a JSON object is required")
    result = cast(dict[str, object], value)
    actual = frozenset(result)
    if fields - actual:
        reject("RPC_MISSING_FIELD", location, "a required field is missing")
    if actual - fields:
        reject("RPC_UNKNOWN_FIELD", location, "unknown fields are not permitted")
    return result


def _array(value: object, location: str) -> list[object]:
    if type(value) is not list:
        reject("RPC_INVALID_COLLECTION", location, "a JSON array is required")
    return cast(list[object], value)


def _string(value: object, location: str) -> str:
    if type(value) is not str:
        reject("RPC_INVALID_TYPE", location, "a string is required")
    return value


def _integer(
    value: object,
    location: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        reject("RPC_INVALID_INTEGER", location, "an integer within the permitted range is required")
    return value


def _boolean(value: object, location: str) -> bool:
    if type(value) is not bool:
        reject("RPC_INVALID_BOOLEAN", location, "a boolean is required")
    return value


def _uuid4(value: object, location: str) -> UUID:
    candidate = _string(value, location)
    try:
        parsed = UUID(candidate)
    except ValueError, AttributeError:
        reject("RPC_INVALID_IDENTIFIER", location, "a canonical UUIDv4 is required")
    if parsed.version != 4 or str(parsed) != candidate:
        reject("RPC_INVALID_IDENTIFIER", location, "a canonical UUIDv4 is required")
    return parsed


def _nullable_uuid4(value: object, location: str) -> UUID | None:
    if value is None:
        return None
    return _uuid4(value, location)


def _enum[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
    location: str,
    code: str = "RPC_INVALID_ENUM",
) -> EnumT:
    candidate = _string(value, location)
    try:
        return enum_type(candidate)
    except ValueError:
        reject(code, location, "the value is not supported by protocol version 1")


def _decode_capability(value: object, location: str) -> SessionCapability:
    return SessionCapability(_string(value, location))


def _decode_capability_descriptor(value: object, location: str) -> CapabilityDescriptor:
    obj = _object(value, location, frozenset({"capability_id", "capability_version"}))
    return CapabilityDescriptor(
        capability_id=_string(obj["capability_id"], f"{location}.capability_id"),
        capability_version=_integer(
            obj["capability_version"], f"{location}.capability_version", minimum=1
        ),
    )


def _decode_tagged_scalar(value: object, location: str) -> TaggedScalar:
    obj = _object(value, location, frozenset({"name", "value_type", "value"}))
    value_type = _enum(obj["value_type"], ScalarValueType, f"{location}.value_type")
    scalar: bool | int | str | UUID
    if value_type is ScalarValueType.BOOLEAN:
        scalar = _boolean(obj["value"], f"{location}.value")
    elif value_type is ScalarValueType.INTEGER:
        scalar = _integer(
            obj["value"],
            f"{location}.value",
            minimum=-MAX_SAFE_INTEGER,
        )
    elif value_type is ScalarValueType.TOKEN:
        scalar = _string(obj["value"], f"{location}.value")
    else:
        scalar = _uuid4(obj["value"], f"{location}.value")
    return TaggedScalar(
        name=_string(obj["name"], f"{location}.name"),
        value_type=value_type,
        value=scalar,
    )


_LIMIT_FIELDS: Final = frozenset(
    {
        "limits_version",
        "wall_time_ms",
        "cpu_time_ms",
        "memory_bytes",
        "temporary_storage_bytes",
        "output_bytes",
        "page_count",
        "pixel_count",
    }
)


def _decode_limits(value: object, location: str) -> ResourceLimits:
    obj = _object(value, location, _LIMIT_FIELDS)
    return ResourceLimits(
        limits_version=_integer(obj["limits_version"], f"{location}.limits_version"),
        wall_time_ms=_integer(obj["wall_time_ms"], f"{location}.wall_time_ms", minimum=1),
        cpu_time_ms=_integer(obj["cpu_time_ms"], f"{location}.cpu_time_ms", minimum=1),
        memory_bytes=_integer(obj["memory_bytes"], f"{location}.memory_bytes", minimum=1),
        temporary_storage_bytes=_integer(
            obj["temporary_storage_bytes"],
            f"{location}.temporary_storage_bytes",
            minimum=1,
        ),
        output_bytes=_integer(obj["output_bytes"], f"{location}.output_bytes", minimum=1),
        page_count=_integer(obj["page_count"], f"{location}.page_count", minimum=1),
        pixel_count=_integer(obj["pixel_count"], f"{location}.pixel_count", minimum=1),
    )


_REFERENCE_FIELDS: Final = frozenset(
    {
        "reference_version",
        "area",
        "object_id",
        "operation_id",
        "content_kind",
        "expected_byte_size",
        "expected_sha256",
    }
)


def _decode_reference(value: object, location: str) -> CaseObjectReference:
    obj = _object(value, location, _REFERENCE_FIELDS)
    expected_size_value = obj["expected_byte_size"]
    expected_size = (
        None
        if expected_size_value is None
        else _integer(expected_size_value, f"{location}.expected_byte_size")
    )
    expected_digest_value = obj["expected_sha256"]
    expected_digest = (
        None
        if expected_digest_value is None
        else _string(expected_digest_value, f"{location}.expected_sha256")
    )
    return CaseObjectReference(
        reference_version=_integer(obj["reference_version"], f"{location}.reference_version"),
        area=_enum(obj["area"], ReferenceArea, f"{location}.area"),
        object_id=_uuid4(obj["object_id"], f"{location}.object_id"),
        operation_id=_nullable_uuid4(obj["operation_id"], f"{location}.operation_id"),
        content_kind=_string(obj["content_kind"], f"{location}.content_kind"),
        expected_byte_size=expected_size,
        expected_sha256=expected_digest,
    )


_ERROR_FIELDS: Final = frozenset(
    {
        "error_version",
        "code",
        "category",
        "retry_action",
        "restart_required",
        "parameters",
    }
)


def _decode_error(value: object, location: str) -> ErrorPayload:
    obj = _object(value, location, _ERROR_FIELDS)
    parameters = _array(obj["parameters"], f"{location}.parameters")
    if parameters:
        reject(
            "RPC_INVALID_ERROR_PARAMETER",
            f"{location}.parameters",
            "public protocol-v1 errors cannot carry context values",
        )
    return ErrorPayload(
        error_version=_integer(obj["error_version"], f"{location}.error_version"),
        code=_enum(obj["code"], ErrorCode, f"{location}.code", code="RPC_INVALID_ERROR"),
        category=_enum(obj["category"], ErrorCategory, f"{location}.category"),
        retry_action=_enum(obj["retry_action"], RetryAction, f"{location}.retry_action"),
        restart_required=_boolean(obj["restart_required"], f"{location}.restart_required"),
        parameters=(),
    )


def _decode_payload(message_type: MessageType, value: object) -> MessagePayload:
    location = "payload"
    if message_type is MessageType.SESSION_HELLO:
        obj = _object(
            value,
            location,
            frozenset({"supported_protocol_versions", "required_capabilities"}),
        )
        versions = _array(obj["supported_protocol_versions"], "payload.supported_protocol_versions")
        capabilities = _array(obj["required_capabilities"], "payload.required_capabilities")
        return SessionHelloPayload(
            supported_protocol_versions=tuple(
                _integer(version, f"payload.supported_protocol_versions[{index}]", minimum=1)
                for index, version in enumerate(versions)
            ),
            required_capabilities=tuple(
                _decode_capability_descriptor(capability, f"payload.required_capabilities[{index}]")
                for index, capability in enumerate(capabilities)
            ),
        )
    if message_type is MessageType.SESSION_READY:
        obj = _object(
            value,
            location,
            frozenset(
                {
                    "selected_protocol_version",
                    "capabilities",
                    "max_message_bytes",
                    "max_in_flight_operations",
                }
            ),
        )
        capabilities = _array(obj["capabilities"], "payload.capabilities")
        return SessionReadyPayload(
            selected_protocol_version=_integer(
                obj["selected_protocol_version"],
                "payload.selected_protocol_version",
            ),
            capabilities=tuple(
                _decode_capability_descriptor(capability, f"payload.capabilities[{index}]")
                for index, capability in enumerate(capabilities)
            ),
            max_message_bytes=_integer(
                obj["max_message_bytes"],
                "payload.max_message_bytes",
                minimum=1,
            ),
            max_in_flight_operations=_integer(
                obj["max_in_flight_operations"],
                "payload.max_in_flight_operations",
                minimum=1,
            ),
        )
    if message_type is MessageType.OPERATION_START:
        obj = _object(
            value,
            location,
            frozenset(
                {
                    "operation_kind",
                    "operation_version",
                    "inputs",
                    "parameters",
                    "requested_limits",
                }
            ),
        )
        inputs = _array(obj["inputs"], "payload.inputs")
        parameters = _array(obj["parameters"], "payload.parameters")
        return OperationStartPayload(
            operation_kind=_string(obj["operation_kind"], "payload.operation_kind"),
            operation_version=_integer(
                obj["operation_version"], "payload.operation_version", minimum=1
            ),
            inputs=tuple(
                _decode_reference(reference, f"payload.inputs[{index}]")
                for index, reference in enumerate(inputs)
            ),
            parameters=tuple(
                _decode_tagged_scalar(parameter, f"payload.parameters[{index}]")
                for index, parameter in enumerate(parameters)
            ),
            requested_limits=_decode_limits(obj["requested_limits"], "payload.requested_limits"),
        )
    if message_type is MessageType.OPERATION_ACCEPTED:
        obj = _object(
            value,
            location,
            frozenset({"operation_kind", "operation_version", "effective_limits"}),
        )
        return OperationAcceptedPayload(
            operation_kind=_string(obj["operation_kind"], "payload.operation_kind"),
            operation_version=_integer(
                obj["operation_version"], "payload.operation_version", minimum=1
            ),
            effective_limits=_decode_limits(obj["effective_limits"], "payload.effective_limits"),
        )
    if message_type is MessageType.OPERATION_PROGRESS:
        obj = _object(
            value,
            location,
            frozenset({"stage_code", "completed_units", "total_units", "unit"}),
        )
        total_value = obj["total_units"]
        total = None if total_value is None else _integer(total_value, "payload.total_units")
        return OperationProgressPayload(
            stage_code=_enum(obj["stage_code"], ProgressStage, "payload.stage_code"),
            completed_units=_integer(obj["completed_units"], "payload.completed_units"),
            total_units=total,
            unit=_enum(obj["unit"], ProgressUnit, "payload.unit"),
        )
    if message_type is MessageType.OPERATION_CANCEL:
        obj = _object(value, location, frozenset({"reason"}))
        return OperationCancelPayload(reason=_enum(obj["reason"], CancelReason, "payload.reason"))
    if message_type is MessageType.OPERATION_CANCEL_ACK:
        obj = _object(value, location, frozenset({"disposition"}))
        return OperationCancelAckPayload(
            disposition=_enum(obj["disposition"], CancelDisposition, "payload.disposition")
        )
    if message_type is MessageType.OPERATION_RESULT:
        obj = _object(value, location, frozenset({"outcome", "outputs", "error"}))
        outputs = _array(obj["outputs"], "payload.outputs")
        error_value = obj["error"]
        return OperationResultPayload(
            outcome=_enum(obj["outcome"], OperationOutcome, "payload.outcome"),
            outputs=tuple(
                _decode_reference(reference, f"payload.outputs[{index}]")
                for index, reference in enumerate(outputs)
            ),
            error=None if error_value is None else _decode_error(error_value, "payload.error"),
        )
    if message_type is MessageType.SESSION_SHUTDOWN:
        obj = _object(value, location, frozenset({"reason"}))
        return SessionShutdownPayload(reason=_enum(obj["reason"], ShutdownReason, "payload.reason"))
    if message_type is MessageType.SESSION_CLOSED:
        obj = _object(value, location, frozenset({"reason"}))
        return SessionClosedPayload(reason=_enum(obj["reason"], ClosedReason, "payload.reason"))
    if message_type is MessageType.PROTOCOL_ERROR:
        obj = _object(value, location, frozenset({"error"}))
        return ProtocolErrorPayload(error=_decode_error(obj["error"], "payload.error"))
    obj = _object(value, location, frozenset({"error", "invalidated_operation_ids"}))
    invalidated = _array(obj["invalidated_operation_ids"], "payload.invalidated_operation_ids")
    return WorkerFaultPayload(
        error=_decode_error(obj["error"], "payload.error"),
        invalidated_operation_ids=tuple(
            _uuid4(operation_id, f"payload.invalidated_operation_ids[{index}]")
            for index, operation_id in enumerate(invalidated)
        ),
    )


@dataclass(frozen=True, slots=True)
class OperationRegistry:
    """The explicit protocol-v1 operation allowlist."""

    @property
    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (NOOP_CAPABILITY, PDF_INSPECT_CAPABILITY)

    def validate_descriptor(self, operation_kind: str, operation_version: int) -> None:
        """Reject an operation descriptor absent from the protocol-v1 registry."""

        descriptor = CapabilityDescriptor(operation_kind, operation_version)
        if descriptor not in self.capabilities:
            reject(
                "RPC_UNSUPPORTED_OPERATION",
                "payload.operation_kind",
                "the operation kind or version is not registered",
            )

    def validate_start(self, payload: OperationStartPayload) -> None:
        """Validate the descriptor and closed input contract for an operation start."""

        self.validate_descriptor(payload.operation_kind, payload.operation_version)
        if payload.operation_kind == NOOP_CAPABILITY.capability_id and (
            payload.inputs or payload.parameters
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload",
                "protocol.noop version 1 accepts no inputs or parameters",
            )
        if payload.operation_kind == PDF_INSPECT_CAPABILITY.capability_id:
            validate_pdf_inspect_start(payload)

    def validate_capabilities(self, capabilities: tuple[CapabilityDescriptor, ...]) -> None:
        """Reject worker-advertised capabilities absent from the local registry."""

        for capability in capabilities:
            self.validate_descriptor(
                capability.capability_id,
                capability.capability_version,
            )


DEFAULT_OPERATION_REGISTRY: Final = OperationRegistry()


def decode_message(
    raw: bytes,
    *,
    expected_capability: SessionCapability | None = None,
    operation_registry: OperationRegistry = DEFAULT_OPERATION_REGISTRY,
) -> ProtocolEnvelope:
    """Decode and validate one complete UTF-8 JSON message without performing I/O."""

    root = _object(_decode_json(raw), "$", _ENVELOPE_FIELDS)
    protocol = _string(root["protocol"], "protocol")
    if protocol != PROTOCOL_NAME:
        reject("RPC_UNSUPPORTED_PROTOCOL", "protocol", "protocol identifier is unsupported")
    protocol_version = _integer(root["protocol_version"], "protocol_version")
    if protocol_version != PROTOCOL_VERSION:
        reject("RPC_UNSUPPORTED_VERSION", "protocol_version", "protocol version is unsupported")
    message_version = _integer(root["message_version"], "message_version")
    if message_version != MESSAGE_VERSION:
        reject("RPC_UNSUPPORTED_VERSION", "message_version", "message version is unsupported")
    message_type = _enum(
        root["message_type"],
        MessageType,
        "message_type",
        code="RPC_UNKNOWN_MESSAGE",
    )
    capability = _decode_capability(root["session_capability"], "session_capability")
    if expected_capability is not None and not capability.matches(expected_capability):
        reject(
            "RPC_CAPABILITY_MISMATCH",
            "session_capability",
            "message capability does not match the active worker launch",
        )
    payload = _decode_payload(message_type, root["payload"])
    if type(payload) is OperationStartPayload:
        operation_registry.validate_start(payload)
    elif type(payload) is SessionReadyPayload:
        operation_registry.validate_capabilities(payload.capabilities)
    elif type(payload) is OperationAcceptedPayload:
        operation_registry.validate_descriptor(payload.operation_kind, payload.operation_version)
    return ProtocolEnvelope(
        protocol=protocol,
        protocol_version=protocol_version,
        message_version=message_version,
        message_type=message_type,
        direction=_enum(root["direction"], Direction, "direction"),
        session_id=_uuid4(root["session_id"], "session_id"),
        worker_instance_id=_uuid4(root["worker_instance_id"], "worker_instance_id"),
        session_capability=capability,
        case_id=_uuid4(root["case_id"], "case_id"),
        message_id=_uuid4(root["message_id"], "message_id"),
        sender_sequence=_integer(root["sender_sequence"], "sender_sequence"),
        request_id=_nullable_uuid4(root["request_id"], "request_id"),
        operation_id=_nullable_uuid4(root["operation_id"], "operation_id"),
        payload=payload,
    )


def _encode_capability_descriptor(value: CapabilityDescriptor) -> dict[str, object]:
    return {
        "capability_id": value.capability_id,
        "capability_version": value.capability_version,
    }


def _encode_scalar(value: TaggedScalar) -> dict[str, object]:
    scalar_value: object = value.value
    if type(scalar_value) is UUID:
        scalar_value = str(scalar_value)
    return {"name": value.name, "value_type": value.value_type.value, "value": scalar_value}


def _encode_limits(value: ResourceLimits) -> dict[str, object]:
    return {
        "limits_version": value.limits_version,
        "wall_time_ms": value.wall_time_ms,
        "cpu_time_ms": value.cpu_time_ms,
        "memory_bytes": value.memory_bytes,
        "temporary_storage_bytes": value.temporary_storage_bytes,
        "output_bytes": value.output_bytes,
        "page_count": value.page_count,
        "pixel_count": value.pixel_count,
    }


def _encode_reference(value: CaseObjectReference) -> dict[str, object]:
    return {
        "reference_version": value.reference_version,
        "area": value.area.value,
        "object_id": str(value.object_id),
        "operation_id": None if value.operation_id is None else str(value.operation_id),
        "content_kind": value.content_kind,
        "expected_byte_size": value.expected_byte_size,
        "expected_sha256": value.expected_sha256,
    }


def _encode_error(value: ErrorPayload) -> dict[str, object]:
    return {
        "error_version": value.error_version,
        "code": value.code.value,
        "category": value.category.value,
        "retry_action": value.retry_action.value,
        "restart_required": value.restart_required,
        "parameters": [],
    }


def _encode_payload(payload: object) -> dict[str, object]:
    if isinstance(payload, SessionHelloPayload):
        return {
            "supported_protocol_versions": list(payload.supported_protocol_versions),
            "required_capabilities": [
                _encode_capability_descriptor(capability)
                for capability in payload.required_capabilities
            ],
        }
    if isinstance(payload, SessionReadyPayload):
        return {
            "selected_protocol_version": payload.selected_protocol_version,
            "capabilities": [
                _encode_capability_descriptor(capability) for capability in payload.capabilities
            ],
            "max_message_bytes": payload.max_message_bytes,
            "max_in_flight_operations": payload.max_in_flight_operations,
        }
    if isinstance(payload, OperationStartPayload):
        return {
            "operation_kind": payload.operation_kind,
            "operation_version": payload.operation_version,
            "inputs": [_encode_reference(reference) for reference in payload.inputs],
            "parameters": [_encode_scalar(parameter) for parameter in payload.parameters],
            "requested_limits": _encode_limits(payload.requested_limits),
        }
    if isinstance(payload, OperationAcceptedPayload):
        return {
            "operation_kind": payload.operation_kind,
            "operation_version": payload.operation_version,
            "effective_limits": _encode_limits(payload.effective_limits),
        }
    if isinstance(payload, OperationProgressPayload):
        return {
            "stage_code": payload.stage_code.value,
            "completed_units": payload.completed_units,
            "total_units": payload.total_units,
            "unit": payload.unit.value,
        }
    if isinstance(payload, OperationCancelPayload):
        return {"reason": payload.reason.value}
    if isinstance(payload, OperationCancelAckPayload):
        return {"disposition": payload.disposition.value}
    if isinstance(payload, OperationResultPayload):
        return {
            "outcome": payload.outcome.value,
            "outputs": [_encode_reference(reference) for reference in payload.outputs],
            "error": None if payload.error is None else _encode_error(payload.error),
        }
    if isinstance(payload, SessionShutdownPayload):
        return {"reason": payload.reason.value}
    if isinstance(payload, SessionClosedPayload):
        return {"reason": payload.reason.value}
    if isinstance(payload, ProtocolErrorPayload):
        return {"error": _encode_error(payload.error)}
    if isinstance(payload, WorkerFaultPayload):
        return {
            "error": _encode_error(payload.error),
            "invalidated_operation_ids": [
                str(operation_id) for operation_id in payload.invalidated_operation_ids
            ],
        }
    reject("RPC_PAYLOAD_MISMATCH", "payload", "payload type is not supported")


def encode_message(
    message: ProtocolEnvelope,
    *,
    operation_registry: OperationRegistry = DEFAULT_OPERATION_REGISTRY,
) -> bytes:
    """Validate and canonically encode one complete protocol message."""

    if type(message) is not ProtocolEnvelope:
        reject("RPC_INVALID_TYPE", "$", "a protocol envelope is required")
    if type(message.payload) is OperationStartPayload:
        operation_registry.validate_start(message.payload)
    elif type(message.payload) is SessionReadyPayload:
        operation_registry.validate_capabilities(message.payload.capabilities)
    elif type(message.payload) is OperationAcceptedPayload:
        operation_registry.validate_descriptor(
            message.payload.operation_kind,
            message.payload.operation_version,
        )
    wire: dict[str, object] = {
        "protocol": message.protocol,
        "protocol_version": message.protocol_version,
        "message_version": message.message_version,
        "message_type": message.message_type.value,
        "direction": message.direction.value,
        "session_id": str(message.session_id),
        "worker_instance_id": str(message.worker_instance_id),
        "session_capability": message.session_capability.value,
        "case_id": str(message.case_id),
        "message_id": str(message.message_id),
        "sender_sequence": message.sender_sequence,
        "request_id": None if message.request_id is None else str(message.request_id),
        "operation_id": None if message.operation_id is None else str(message.operation_id),
        "payload": _encode_payload(message.payload),
    }
    try:
        encoded = json.dumps(
            wire,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        reject("RPC_ENCODING_FAILURE", "$", "the protocol message cannot be encoded")
    if len(encoded) > MAX_MESSAGE_BYTES:
        reject("RPC_MESSAGE_TOO_LARGE", "$", "the encoded message exceeds the byte limit")
    return encoded
