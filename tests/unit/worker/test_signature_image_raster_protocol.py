"""Focused protocol tests for ``image.signature_raster`` version 1."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
    SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
    signature_image_raster_bound_object_ids,
    validate_signature_image_raster_result,
    validate_signature_image_raster_start,
)


def _uuid(number: int) -> UUID:
    return UUID(f"7e000000-0000-4000-8000-{number:012d}")


def _limits(*, output_bytes: int = 4_096, pixel_count: int = 4_096) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=2_000,
        cpu_time_ms=1_500,
        memory_bytes=8 * 1024 * 1024,
        temporary_storage_bytes=4_096,
        output_bytes=output_bytes,
        page_count=1,
        pixel_count=pixel_count,
    )


def _asset(**changes: object) -> CaseObjectReference:
    values: dict[str, object] = {
        "area": ReferenceArea.ASSET_READ,
        "object_id": _uuid(2),
        "operation_id": None,
        "content_kind": "png",
        "expected_byte_size": 128,
        "expected_sha256": "a" * 64,
    }
    values.update(changes)
    return CaseObjectReference(**values)  # type: ignore[arg-type]


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(
            SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(3),
        ),
        TaggedScalar(
            SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
            ScalarValueType.INTEGER,
            32,
        ),
        TaggedScalar(
            SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
            ScalarValueType.INTEGER,
            16,
        ),
    )


def _payload(
    *,
    source: CaseObjectReference | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    limits: ResourceLimits | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=SIGNATURE_IMAGE_RASTER_CAPABILITY.capability_id,
        operation_version=SIGNATURE_IMAGE_RASTER_CAPABILITY.capability_version,
        inputs=(_asset() if source is None else source,),
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits() if limits is None else limits,
    )


def _replace_parameter(replacement: TaggedScalar) -> tuple[TaggedScalar, ...]:
    return tuple(
        replacement if parameter.name == replacement.name else parameter
        for parameter in _parameters()
    )


def _output(**changes: object) -> CaseObjectReference:
    values: dict[str, object] = {
        "area": ReferenceArea.OPERATION_STAGING,
        "object_id": _uuid(3),
        "operation_id": _uuid(10),
        "content_kind": SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
        "expected_byte_size": 512,
        "expected_sha256": "b" * 64,
    }
    values.update(changes)
    return CaseObjectReference(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("content_kind", ("jpeg", "png"))
def test_signature_raster_is_registered_and_accepts_only_asset_read(
    content_kind: str,
) -> None:
    payload = _payload(source=_asset(content_kind=content_kind))

    DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert SIGNATURE_IMAGE_RASTER_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    assert signature_image_raster_bound_object_ids(payload) == (_uuid(2), _uuid(3))
    assert "a" * 64 not in repr(payload.inputs[0])


@pytest.mark.parametrize(
    "payload",
    (
        _payload(source=_asset(area=ReferenceArea.SOURCE_READ)),
        _payload(source=_asset(content_kind="pdf")),
        _payload(source=_asset(expected_sha256=None)),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
                    ScalarValueType.UUID,
                    _uuid(2),
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
                    ScalarValueType.INTEGER,
                    501,
                )
            )
        ),
        _payload(limits=_limits(pixel_count=511)),
    ),
)
def test_signature_raster_rejects_wrong_scope_binding_and_bounds(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as caught:
        validate_signature_image_raster_start(payload)

    assert caught.value.code == "RPC_INVALID_OPERATION"
    assert caught.value.__cause__ is None


def test_signature_raster_result_is_bound_to_exact_operation_output_and_limit() -> None:
    success = OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None)

    validate_signature_image_raster_result(
        success,
        operation_id=_uuid(10),
        output_object_id=_uuid(3),
        maximum_output_bytes=512,
    )

    for output in (
        _output(area=ReferenceArea.OPERATION_CACHE),
        _output(object_id=_uuid(4)),
        _output(operation_id=_uuid(11)),
        _output(content_kind="image-normalized-rgba-v1"),
        _output(expected_byte_size=513),
        _output(expected_sha256=None),
    ):
        with pytest.raises(ProtocolError) as caught:
            validate_signature_image_raster_result(
                replace(success, outputs=(output,)),
                operation_id=_uuid(10),
                output_object_id=_uuid(3),
                maximum_output_bytes=512,
            )
        assert caught.value.code == "RPC_INVALID_RESULT"


def test_unsuccessful_signature_result_cannot_smuggle_an_output() -> None:
    payload = OperationResultPayload(OperationOutcome.CANCELLED, (), None)
    validate_signature_image_raster_result(
        payload,
        operation_id=_uuid(10),
        output_object_id=_uuid(3),
        maximum_output_bytes=512,
    )

    malformed = object.__new__(OperationResultPayload)
    object.__setattr__(malformed, "outcome", OperationOutcome.CANCELLED)
    object.__setattr__(malformed, "outputs", (_output(),))
    object.__setattr__(malformed, "error", None)
    with pytest.raises(ProtocolError) as caught:
        validate_signature_image_raster_result(
            malformed,
            operation_id=_uuid(10),
            output_object_id=_uuid(3),
            maximum_output_bytes=512,
        )
    assert caught.value.code == "RPC_INVALID_RESULT"
