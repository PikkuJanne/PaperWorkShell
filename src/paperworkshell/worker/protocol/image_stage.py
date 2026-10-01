"""Closed authority and output binding for an explicit PDF image stage."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    MAX_PDF_EXPORT_PAGES,
    MAX_PDF_EXPORT_PLAN_BYTES,
    MAX_PDF_EXPORT_SOURCES,
    MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
    PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
    PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
    PDF_IMAGE_STAGE_PLAN_CONTENT_KIND,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ScalarValueType,
)

HANDLE_NAMES = ("plan_handle", "output_handle", "evidence_handle", "result_handle")
ID_NAMES = ("output_object_id", "evidence_object_id")
PARAMETER_NAMES = frozenset((*HANDLE_NAMES, *ID_NAMES, "evidence_capacity_bytes"))


def validate_pdf_image_stage_start(payload: OperationStartPayload) -> None:
    """Reject extra authority and insufficient admitted storage before dispatch."""

    if not 2 <= len(payload.inputs) <= MAX_PDF_EXPORT_SOURCES + 1:
        reject("RPC_INVALID_OPERATION", "payload.inputs", "image stage requires a plan and sources")
    plan, *sources = payload.inputs
    if (
        plan.area is not ReferenceArea.SOURCE_READ
        or plan.operation_id is not None
        or plan.content_kind != PDF_IMAGE_STAGE_PLAN_CONTENT_KIND
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or plan.expected_sha256 is None
        or any(
            source.area is not ReferenceArea.SOURCE_READ
            or source.operation_id is not None
            or source.content_kind not in {"pdf", "jpeg", "png"}
            or source.expected_byte_size is None
            or source.expected_byte_size < 1
            or source.expected_sha256 is None
            for source in sources
        )
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.inputs", "image stage inputs must be authenticated"
        )
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(PARAMETER_NAMES) or set(parameters) != PARAMETER_NAMES:
        reject("RPC_INVALID_OPERATION", "payload.parameters", "image stage parameters are closed")
    if any(
        parameters[name].value_type is not ScalarValueType.INTEGER
        or type(parameters[name].value) is not int
        or cast(int, parameters[name].value) <= 0
        for name in (*HANDLE_NAMES, "evidence_capacity_bytes")
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "image stage handles must be positive"
        )
    handles = tuple(parameters[name].value for name in HANDLE_NAMES)
    if len(set(handles)) != len(handles):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "image stage handles must be distinct"
        )
    if any(
        parameters[name].value_type is not ScalarValueType.UUID
        or type(parameters[name].value) is not UUID
        for name in ID_NAMES
    ):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "image stage output IDs must be UUIDs"
        )
    identifiers = (
        *[reference.object_id for reference in payload.inputs],
        *[parameters[name].value for name in ID_NAMES],
    )
    if len(set(identifiers)) != len(identifiers):
        reject(
            "RPC_INVALID_OPERATION", "payload.parameters", "image stage object IDs must be distinct"
        )
    capacity = cast(int, parameters["evidence_capacity_bytes"].value)
    source_total = sum(source.expected_byte_size or 0 for source in sources)
    limits = payload.requested_limits
    if (
        capacity > MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES
        or capacity > limits.memory_bytes // 8
        or limits.page_count > MAX_PDF_EXPORT_PAGES
        or source_total + 2 * limits.output_bytes + capacity > limits.temporary_storage_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "image stage storage is insufficient",
        )


def pdf_image_stage_binding(payload: OperationStartPayload) -> tuple[UUID, UUID, int]:
    validate_pdf_image_stage_start(payload)
    values = {parameter.name: parameter.value for parameter in payload.parameters}
    return (
        cast(UUID, values["output_object_id"]),
        cast(UUID, values["evidence_object_id"]),
        cast(int, values["evidence_capacity_bytes"]),
    )


def validate_pdf_image_stage_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    evidence_object_id: UUID,
    maximum_output_bytes: int,
    maximum_evidence_bytes: int,
) -> None:
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT", "payload.outputs", "failed image stage cannot publish outputs"
            )
        return
    if len(payload.outputs) != 2:
        reject("RPC_INVALID_RESULT", "payload.outputs", "image stage requires two exact outputs")
    for reference, identifier, kind, capacity in zip(
        payload.outputs,
        (output_object_id, evidence_object_id),
        (PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND, PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND),
        (maximum_output_bytes, maximum_evidence_bytes),
        strict=True,
    ):
        if (
            reference.area is not ReferenceArea.OPERATION_STAGING
            or reference.operation_id != operation_id
            or reference.object_id != identifier
            or reference.content_kind != kind
            or reference.expected_byte_size is None
            or not 1 <= reference.expected_byte_size <= capacity
            or reference.expected_sha256 is None
        ):
            reject("RPC_INVALID_RESULT", "payload.outputs", "image stage output binding is invalid")
