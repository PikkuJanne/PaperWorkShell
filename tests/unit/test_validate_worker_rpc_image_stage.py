"""Offline admission follows the additive image-stage runtime without schema edits."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.worker.protocol.codec import decode_message
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.image_stage import validate_pdf_image_stage_start
from paperworkshell.worker.protocol.models import OperationStartPayload
from tools.validate_worker_rpc import (
    RPC_EXAMPLE_DIRECTORY,
    load_worker_rpc_schema,
    validate_worker_rpc_data,
)

_ROOT = Path(__file__).resolve().parents[2]


def _example(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (_ROOT / RPC_EXAMPLE_DIRECTORY / f"pdf-image-stage-{name}.json").read_text(
                encoding="utf-8"
            )
        ),
    )


@pytest.fixture(scope="module")
def schema() -> Mapping[str, Any]:
    result, issues = load_worker_rpc_schema()
    assert result is not None and not issues
    return result


@pytest.mark.parametrize("name", ("hello", "ready", "start", "accepted", "result"))
def test_new_examples_pass_existing_envelope_schema_and_runtime_decode(
    name: str, schema: Mapping[str, Any]
) -> None:
    value = _example(name)
    assert validate_worker_rpc_data(value, schema) == []
    decoded = decode_message(json.dumps(value).encode())
    if name == "start":
        assert isinstance(decoded.payload, OperationStartPayload)
        validate_pdf_image_stage_start(decoded.payload)


@pytest.mark.parametrize("name", ("ready", "start", "accepted"))
@pytest.mark.parametrize("version", (0, 2))
def test_only_exact_new_operation_version_is_admitted(
    name: str, version: int, schema: Mapping[str, Any]
) -> None:
    value = _example(name)
    if name == "ready":
        value["payload"]["capabilities"][0]["capability_version"] = version
    else:
        value["payload"]["operation_version"] = version
    assert "RPC_OPERATION_UNSUPPORTED" in {
        issue.code for issue in validate_worker_rpc_data(value, schema)
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "temporary_plan",
        "plan_operation",
        "plan_asset",
        "plan_kind",
        "plan_digest",
        "plan_oversized",
        "source_area",
        "source_kind",
        "source_digest",
        "source_operation",
        "plan_source_alias",
        "output_source_alias",
        "output_pair_alias",
        "handle_alias",
        "zero_handle",
        "bool_handle",
        "missing_parameter",
        "extra_parameter",
        "wrong_parameter_type",
        "evidence_memory",
        "evidence_capacity",
        "storage_one_byte_short",
        "page_limit",
    ),
)
def test_offline_and_runtime_reject_same_image_stage_authority_mutations(
    mutation: str, schema: Mapping[str, Any]
) -> None:
    value = _example("start")
    payload = value["payload"]
    plan, source = payload["inputs"][:2]
    parameters = payload["parameters"]
    limits = payload["requested_limits"]
    if mutation == "temporary_plan":
        plan.update(area="operation_temp", operation_id=value["operation_id"])
    elif mutation == "plan_operation":
        plan["operation_id"] = value["operation_id"]
    elif mutation == "plan_asset":
        plan["area"] = "asset_read"
    elif mutation == "plan_kind":
        plan["content_kind"] = "pdf-export-plan-v1"
    elif mutation == "plan_digest":
        plan["expected_sha256"] = None
    elif mutation == "plan_oversized":
        plan["expected_byte_size"] = 2 * 1024 * 1024 + 1
    elif mutation == "source_area":
        source["area"] = "asset_read"
    elif mutation == "source_kind":
        source["content_kind"] = "tiff"
    elif mutation == "source_digest":
        source["expected_sha256"] = None
    elif mutation == "source_operation":
        source["operation_id"] = value["operation_id"]
    elif mutation == "plan_source_alias":
        source["object_id"] = plan["object_id"]
    elif mutation == "output_source_alias":
        parameters[4]["value"] = source["object_id"]
    elif mutation == "output_pair_alias":
        parameters[5]["value"] = parameters[4]["value"]
    elif mutation == "handle_alias":
        parameters[2]["value"] = parameters[0]["value"]
    elif mutation == "zero_handle":
        parameters[0]["value"] = 0
    elif mutation == "bool_handle":
        parameters[0]["value"] = True
    elif mutation == "missing_parameter":
        parameters.pop()
    elif mutation == "extra_parameter":
        parameters.append(
            {"name": "private_profile", "value_type": "token", "value": "private_value_canary"}
        )
    elif mutation == "wrong_parameter_type":
        parameters[0]["value_type"] = "token"
    elif mutation == "evidence_memory":
        limits["memory_bytes"] = 8_191
    elif mutation == "evidence_capacity":
        parameters[6]["value"] = 2 * 1024 * 1024 + 1
    elif mutation == "storage_one_byte_short":
        limits["temporary_storage_bytes"] -= 1
    else:
        limits["page_count"] = 4_097
    issues = validate_worker_rpc_data(value, schema)
    assert "RPC_OPERATION_INPUT_INVALID" in {issue.code for issue in issues}
    assert "private_value_canary" not in "\n".join(issue.render() for issue in issues)
    with pytest.raises(ProtocolError):
        decoded = decode_message(json.dumps(value).encode())
        validate_pdf_image_stage_start(cast(OperationStartPayload, decoded.payload))


@pytest.mark.parametrize(
    "mutation", ("missing", "reverse", "extra", "aliased", "zero", "oversized", "cancelled")
)
def test_recognizable_result_pair_has_exact_order_and_bounded_evidence(
    mutation: str, schema: Mapping[str, Any]
) -> None:
    value = _example("result")
    payload = value["payload"]
    outputs = payload["outputs"]
    if mutation == "missing":
        outputs.pop()
    elif mutation == "reverse":
        outputs.reverse()
    elif mutation == "extra":
        outputs.append(outputs[0])
    elif mutation == "aliased":
        outputs[1]["object_id"] = outputs[0]["object_id"]
    elif mutation == "zero":
        outputs[0]["expected_byte_size"] = 0
    elif mutation == "oversized":
        outputs[1]["expected_byte_size"] = 2 * 1024 * 1024 + 1
    else:
        payload["outcome"] = "cancelled"
    assert "RPC_RESULT_INVALID" in {issue.code for issue in validate_worker_rpc_data(value, schema)}


def test_new_plan_does_not_widen_existing_read_reference_schema(schema: Mapping[str, Any]) -> None:
    value = _example("start")
    value["payload"]["inputs"][0].update(area="operation_temp", operation_id=value["operation_id"])
    codes = {issue.code for issue in validate_worker_rpc_data(value, schema)}
    assert {"RPC_SCHEMA_VALIDATION", "RPC_REFERENCE_AREA_INVALID"} <= codes
