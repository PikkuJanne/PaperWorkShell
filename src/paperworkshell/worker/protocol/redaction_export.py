"""Closed authority for private, unverified derivative redaction_export."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    MAX_PDF_EXPORT_PLAN_BYTES,
    REDACTION_EXPORT_OUTPUT_CONTENT_KIND,
    REDACTION_EXPORT_PLAN_CONTENT_KIND,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
)

HANDLE_NAMES = ("source_handle", "plan_handle", "output_handle", "result_handle")
PARAMETER_NAMES = frozenset((*HANDLE_NAMES, "output_object_id"))


def redaction_export_binding(payload: OperationStartPayload) -> UUID:
    """Reject paths, extra handles, unauthenticated inputs and storage escalation."""

    payload.__post_init__()
    if len(payload.inputs) != 2:
        reject(
            "RPC_INVALID_OPERATION", "payload.inputs", "redaction_export requires plan and source"
        )
    plan, source = payload.inputs
    plan.__post_init__()
    source.__post_init__()
    if (
        plan.area is not ReferenceArea.SOURCE_READ
        or source.area is not ReferenceArea.SOURCE_READ
        or plan.operation_id is not None
        or source.operation_id is not None
        or plan.content_kind != REDACTION_EXPORT_PLAN_CONTENT_KIND
        or source.content_kind != "pdf"
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or plan.expected_sha256 is None
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "redaction_export inputs must be authenticated",
        )
    values = {item.name: item for item in payload.parameters}
    if len(payload.parameters) != len(PARAMETER_NAMES) or set(values) != PARAMETER_NAMES:
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "redaction_export parameters are closed"
        )
    if any(
        values[name].value_type is not ScalarValueType.INTEGER
        or type(values[name].value) is not int
        or cast(int, values[name].value) < 1
        for name in HANDLE_NAMES
    ) or len({values[name].value for name in HANDLE_NAMES}) != len(HANDLE_NAMES):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "redaction_export handles are invalid"
        )
    identifier = values["output_object_id"]
    if identifier.value_type is not ScalarValueType.UUID or type(identifier.value) is not UUID:
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "redaction_export output ID is invalid"
        )
    if len({plan.object_id, source.object_id, identifier.value}) != 3:
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "redaction_export IDs must be distinct"
        )
    if (
        source.expected_byte_size
        + plan.expected_byte_size
        + 3 * payload.requested_limits.output_bytes
        > payload.requested_limits.temporary_storage_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "redaction_export storage is insufficient",
        )
    return identifier.value


def validate_redaction_export_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    payload.__post_init__()
    for output in payload.outputs:
        output.__post_init__()
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "failed redaction_export cannot publish output",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT", "payload.outputs", "redaction_export requires one exact output"
        )
    output = payload.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.operation_id != operation_id
        or output.object_id != output_object_id
        or output.content_kind != REDACTION_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT", "payload.outputs", "redaction_export output binding is invalid"
        )
