"""Closed version-1 derivative-to-text worker operation.

Only byte counts, hashes and object identities cross RPC; document text travels
in the separately bounded broker-owned output section.
"""

from __future__ import annotations

from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    CapabilityDescriptor,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
)

TEXT_COMPANION_CAPABILITY = CapabilityDescriptor("pdf.text_companion", 1)
TEXT_COMPANION_CONTENT_KIND = "export-text-companion-utf8-v1"
TEXT_COMPANION_SOURCE_KIND = "verified-export-pdf-v1"
TEXT_COMPANION_ARCHIVE_KIND = "verified-export-zip-v1"


def text_companion_binding(payload: OperationStartPayload) -> UUID:
    if len(payload.inputs) != 1:
        reject("RPC_INVALID_OPERATION", "payload.inputs", "one verified derivative is required")
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.operation_id is not None
        or source.content_kind not in {TEXT_COMPANION_SOURCE_KIND, TEXT_COMPANION_ARCHIVE_KIND}
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_byte_size > payload.requested_limits.temporary_storage_bytes
        or source.expected_sha256 is None
    ):
        reject("RPC_INVALID_OPERATION", "payload.inputs", "the derivative identity is invalid")
    if (
        payload.requested_limits.page_count > 1_000
        or payload.requested_limits.output_bytes > payload.requested_limits.memory_bytes // 8
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "text companion resource bounds are invalid",
        )
    parameters = {value.name: value for value in payload.parameters}
    expected_names = {"source_handle", "output_handle", "output_object_id", "page_count"}
    if source.content_kind == TEXT_COMPANION_ARCHIVE_KIND:
        expected_names |= {"member_position", "member_byte_size", "member_sha256"}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "the exact text companion parameter set is required",
        )
    handles = tuple(parameters[name] for name in ("source_handle", "output_handle"))
    if (
        any(
            value.value_type is not ScalarValueType.INTEGER
            or type(value.value) is not int
            or value.value <= 0
            for value in handles
        )
        or handles[0].value == handles[1].value
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "distinct brokered handles are required"
        )
    output = parameters["output_object_id"]
    pages = parameters["page_count"]
    if (
        output.value_type is not ScalarValueType.UUID
        or type(output.value) is not UUID
        or output.value == source.object_id
        or pages.value_type is not ScalarValueType.INTEGER
        or type(pages.value) is not int
        or not 1 <= pages.value <= payload.requested_limits.page_count
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "the output or page binding is invalid"
        )
    if source.content_kind == TEXT_COMPANION_ARCHIVE_KIND:
        position, size, digest = (
            parameters[name] for name in ("member_position", "member_byte_size", "member_sha256")
        )
        if (
            position.value_type is not ScalarValueType.INTEGER
            or type(position.value) is not int
            or not 0 <= position.value < 128
            or size.value_type is not ScalarValueType.INTEGER
            or type(size.value) is not int
            or not 1 <= size.value <= source.expected_byte_size
            or digest.value_type is not ScalarValueType.TOKEN
            or type(digest.value) is not str
            or len(digest.value) != 71
            or not digest.value.startswith("sha256-")
            or any(char not in "0123456789abcdef" for char in digest.value[7:])
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "the archive member binding is invalid",
            )
    return output.value


def validate_text_companion_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful text extraction cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject("RPC_INVALID_RESULT", "payload.outputs", "one bounded text output is required")
    output = payload.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != TEXT_COMPANION_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject("RPC_INVALID_RESULT", "payload.outputs", "the text output binding is invalid")
