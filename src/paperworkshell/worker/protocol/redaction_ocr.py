"""Closed authority for the private, unverified page redaction OCR operation."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    MAX_PDF_EXPORT_PLAN_BYTES,
    REDACTION_OCR_OUTPUT_CONTENT_KIND,
    REDACTION_OCR_PLAN_CONTENT_KIND,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
)

HANDLE_NAMES = ("source_handle", "plan_handle", "output_handle", "result_handle")
PARAMETER_NAMES = frozenset((*HANDLE_NAMES, "output_object_id"))


def redaction_ocr_binding(payload: OperationStartPayload) -> UUID:
    """Reject paths, extra handles, unauthenticated inputs and storage escalation."""

    if len(payload.inputs) != 2:
        reject("RPC_INVALID_OPERATION", "payload.inputs", "redaction OCR requires plan and source")
    plan, source = payload.inputs
    if (
        plan.area is not ReferenceArea.SOURCE_READ
        or source.area is not ReferenceArea.SOURCE_READ
        or plan.operation_id is not None
        or source.operation_id is not None
        or plan.content_kind != REDACTION_OCR_PLAN_CONTENT_KIND
        or source.content_kind != "pdf"
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or plan.expected_sha256 is None
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.inputs", "redaction OCR inputs must be authenticated"
        )
    values = {item.name: item for item in payload.parameters}
    if len(payload.parameters) != len(PARAMETER_NAMES) or set(values) != PARAMETER_NAMES:
        reject("RPC_INVALID_OPERATION", "payload.parameters", "redaction OCR parameters are closed")
    if any(
        values[name].value_type is not ScalarValueType.INTEGER
        or type(values[name].value) is not int
        or cast(int, values[name].value) < 1
        for name in HANDLE_NAMES
    ) or len({values[name].value for name in HANDLE_NAMES}) != len(HANDLE_NAMES):
        reject("RPC_INVALID_OPERATION", "payload.parameters", "redaction OCR handles are invalid")
    identifier = values["output_object_id"]
    if identifier.value_type is not ScalarValueType.UUID or type(identifier.value) is not UUID:
        reject("RPC_INVALID_OPERATION", "payload.parameters", "redaction OCR output ID is invalid")
    if len({plan.object_id, source.object_id, identifier.value}) != 3:
        reject("RPC_INVALID_OPERATION", "payload.parameters", "redaction OCR IDs must be distinct")
    if (
        source.expected_byte_size
        + plan.expected_byte_size
        + 2 * payload.requested_limits.output_bytes
        > payload.requested_limits.temporary_storage_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "redaction OCR storage is insufficient",
        )
    return identifier.value


def validate_redaction_ocr_result(
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
                "failed redaction OCR cannot publish output",
            )
        return
    if len(payload.outputs) != 1:
        reject("RPC_INVALID_RESULT", "payload.outputs", "redaction OCR requires one exact output")
    output = payload.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.operation_id != operation_id
        or output.object_id != output_object_id
        or output.content_kind != REDACTION_OCR_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject("RPC_INVALID_RESULT", "payload.outputs", "redaction OCR output binding is invalid")
