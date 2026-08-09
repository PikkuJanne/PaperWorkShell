"""Unit evidence for the pure runtime case-manifest v1 validator."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.domain.manifest_validation import (
    CANONICAL_SCHEMA_FINGERPRINT,
    ManifestValidationIssue,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "case-manifest-v1.schema.json"
EXAMPLE_DIRECTORY = ROOT / "schemas" / "examples"
INVALID_DIRECTORY = ROOT / "tests" / "fixtures" / "manifests" / "v1" / "invalid"


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def _schema() -> dict[str, Any]:
    return _load_json(SCHEMA_PATH)


def _minimal() -> dict[str, Any]:
    return _load_json(EXAMPLE_DIRECTORY / "case-manifest-v1-minimal.json")


def _representative() -> dict[str, Any]:
    return _load_json(EXAMPLE_DIRECTORY / "case-manifest-v1-representative.json")


def _issues(manifest: dict[str, Any]) -> tuple[ManifestValidationIssue, ...]:
    return validate_manifest(manifest, _schema())


def _codes(manifest: dict[str, Any]) -> set[str]:
    return {issue.code for issue in _issues(manifest)}


def _set_nested(document: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    current: object = document
    for part in path[:-1]:
        if type(part) is int:
            assert isinstance(current, list)
            current = current[part]
        else:
            assert isinstance(current, dict)
            current = current[part]
    final = path[-1]
    if type(final) is int:
        assert isinstance(current, list)
        current[final] = value
    else:
        assert isinstance(current, dict)
        current[final] = value


def test_canonical_examples_validate_without_mutation() -> None:
    for path in sorted(EXAMPLE_DIRECTORY.glob("case-manifest-v1-*.json")):
        manifest = _load_json(path)
        before = copy.deepcopy(manifest)

        assert validate_manifest(manifest, _schema()) == ()
        assert manifest == before


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("duplicate-key.json", "MANIFEST_SCHEMA_VALIDATION"),
        ("duplicate-source-id.json", "MANIFEST_SOURCE_ID_DUPLICATE"),
        ("executable-operation-field.json", "MANIFEST_SCHEMA_VALIDATION"),
        ("future-version.json", "MANIFEST_VERSION_UNSUPPORTED"),
        ("invalid-hash.json", "MANIFEST_SCHEMA_VALIDATION"),
        ("legacy-version.json", "MANIFEST_VERSION_UNSUPPORTED"),
        ("malformed-normalized-box.json", "MANIFEST_NORMALIZED_BOX_INVALID"),
        ("out-of-range-source-page.json", "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE"),
        ("unknown-page-reference.json", "MANIFEST_PAGE_REFERENCE_UNKNOWN"),
        ("unknown-source-reference.json", "MANIFEST_SOURCE_REFERENCE_UNKNOWN"),
        ("unsafe-storage-name.json", "MANIFEST_SCHEMA_VALIDATION"),
    ],
)
def test_decoded_negative_fixtures_remain_rejected(filename: str, expected_code: str) -> None:
    manifest = _load_json(INVALID_DIRECTORY / filename)

    assert expected_code in _codes(manifest)


@pytest.mark.parametrize(
    ("version", "expected_code"),
    [
        (None, "MANIFEST_VERSION_INVALID"),
        (True, "MANIFEST_VERSION_INVALID"),
        ("1", "MANIFEST_VERSION_INVALID"),
        (0, "MANIFEST_VERSION_UNSUPPORTED"),
        (2, "MANIFEST_VERSION_UNSUPPORTED"),
    ],
)
def test_version_dispatch_precedes_schema_execution(version: object, expected_code: str) -> None:
    manifest = _minimal()
    if version is None:
        del manifest["schema_version"]
    else:
        manifest["schema_version"] = version

    issues = validate_manifest(manifest, {})

    assert {issue.code for issue in issues} == {expected_code}


def test_schema_identity_is_bound_to_the_reviewed_contract() -> None:
    schema = _schema()
    assert CANONICAL_SCHEMA_FINGERPRINT == (
        "a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695"
    )
    schema["description"] = "weakened synthetic substitute"

    issues = validate_manifest(_minimal(), schema)

    assert {issue.code for issue in issues} == {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"}


@pytest.mark.parametrize(
    ("path", "expected_path"),
    [
        (("case_id",), "$.case_id"),
        (("sources", 0, "sha256"), "$.sources[0].sha256"),
        (("template_ref", "template_id"), "$.template_ref.template_id"),
        (("template_ref", "template_version"), "$.template_ref.template_version"),
        (("sources", 0, "storage_name"), "$.sources[0].storage_name"),
        (("overlays", 0, "payload", "color"), "$.overlays[0].payload.color"),
        (("page_sequence", 0), "$.page_sequence[0]"),
    ],
)
def test_exact_lexical_tokens_reject_final_line_feed(
    path: tuple[str | int, ...],
    expected_path: str,
) -> None:
    manifest = _representative()
    current: object = manifest
    for part in path:
        if type(part) is int:
            assert isinstance(current, list)
            current = current[part]
        else:
            assert isinstance(current, dict)
            current = current[part]
    assert type(current) is str
    _set_nested(manifest, path, f"{current}\n")

    issues = _issues(manifest)

    assert any(
        issue.code == "MANIFEST_EXACT_TOKEN_INVALID" and issue.path == expected_path
        for issue in issues
    )


def test_every_required_top_level_property_is_enforced() -> None:
    schema = _schema()
    required = cast(list[str], schema["required"])
    assert required

    for member in required:
        manifest = _minimal()
        del manifest[member]

        issues = validate_manifest(manifest, schema)

        assert issues
        if member == "schema_version":
            assert {issue.code for issue in issues} == {"MANIFEST_VERSION_INVALID"}
        else:
            assert issues[0].code == "MANIFEST_SCHEMA_VALIDATION"
            assert any(issue.message == "a required member is missing" for issue in issues)


@pytest.mark.parametrize(
    ("name", "path", "value", "expected_path", "expected_message"),
    [
        (
            "type-bool-is-not-integer",
            ("revision",),
            True,
            "$.revision",
            "value has an invalid JSON type",
        ),
        (
            "const",
            ("constraints", "constraints_version"),
            2,
            "$.constraints.constraints_version",
            "value is not supported",
        ),
        (
            "enum",
            ("constraints", "encryption_policy"),
            "prohibited",
            "$.constraints.encryption_policy",
            "value is not supported",
        ),
        (
            "pattern",
            ("case_id",),
            "not-a-uuid",
            "$.case_id",
            "value has an invalid format",
        ),
        (
            "minimum",
            ("revision",),
            -1,
            "$.revision",
            "number is outside the allowed range",
        ),
        (
            "maximum",
            ("sources", 0, "byte_size"),
            9_007_199_254_740_992,
            "$.sources[0].byte_size",
            "number is outside the allowed range",
        ),
        (
            "minLength",
            ("assignments", 0, "requirement_id"),
            "",
            "$.assignments[0].requirement_id",
            "text length is outside the allowed range",
        ),
        (
            "maxLength",
            ("assignments", 0, "requirement_id"),
            "a" * 129,
            "$.assignments[0].requirement_id",
            "text length is outside the allowed range",
        ),
        (
            "minItems",
            ("constraints", "allowed_output_modes"),
            [],
            "$.constraints.allowed_output_modes",
            "array length is outside the allowed range",
        ),
        (
            "maxItems",
            ("constraints", "allowed_output_modes"),
            ["combined_pdf", "separate_pdfs", "zip", "combined_pdf"],
            "$.constraints.allowed_output_modes",
            "array length is outside the allowed range",
        ),
        (
            "uniqueItems",
            ("page_sequence_base",),
            [
                "30000000-0000-4000-8000-000000000001",
                "30000000-0000-4000-8000-000000000001",
            ],
            "$.page_sequence_base",
            "array members must be unique",
        ),
        (
            "oneOf",
            ("page_operations", 0, "payload"),
            {"kind": "execute"},
            "$.page_operations[0].payload",
            "value does not match exactly one supported shape",
        ),
        (
            "allOf",
            ("constraints", "requested_output_names"),
            ["trailing-dot."],
            "$.constraints.requested_output_names[0]",
            "value has an invalid format",
        ),
        (
            "not",
            ("sources", 0, "display_name"),
            ".",
            "$.sources[0].display_name",
            "value is explicitly prohibited",
        ),
        (
            "if-then",
            ("assignments", 0, "status"),
            "suggested",
            "$.assignments[0].confirmation_id",
            "value has an invalid JSON type",
        ),
        (
            "if-else",
            ("assignments", 1, "status"),
            "user_confirmed",
            "$.assignments[1].confirmation_id",
            "value has an invalid JSON type",
        ),
        (
            "date-time-format",
            ("created_at",),
            "2026-13-01T10:00:00Z",
            "$.created_at",
            "value has an invalid format",
        ),
    ],
)
def test_schema_keyword_mutations_fail_at_stable_paths(
    name: str,
    path: tuple[str | int, ...],
    value: object,
    expected_path: str,
    expected_message: str,
) -> None:
    del name
    manifest = _representative()
    _set_nested(manifest, path, value)

    issues = _issues(manifest)

    assert any(
        issue.code == "MANIFEST_SCHEMA_VALIDATION"
        and issue.path == expected_path
        and issue.message == expected_message
        for issue in issues
    )


def test_properties_items_refs_and_additional_properties_are_enforced() -> None:
    manifest = _representative()
    cast(dict[str, Any], manifest["sources"][0])["CANARY-PRIVATE-FIELD"] = "CANARY-VALUE"

    issues = _issues(manifest)
    rendered = "\n".join(f"{issue.code}|{issue.path}|{issue.message}" for issue in issues)

    assert any(
        issue.path == "$.sources[0]" and issue.message == "unknown members are not permitted"
        for issue in issues
    )
    assert "CANARY-PRIVATE-FIELD" not in rendered
    assert "CANARY-VALUE" not in rendered


@pytest.mark.parametrize(
    ("timestamp", "expected_code"),
    [
        ("0000-01-01T00:00:00Z", "MANIFEST_TIMESTAMP_INVALID"),
        ("2016-12-31T23:59:60Z", "MANIFEST_TIMESTAMP_INVALID"),
    ],
)
def test_schema_accepted_timestamps_still_use_canonical_semantics(
    timestamp: str,
    expected_code: str,
) -> None:
    manifest = _minimal()
    manifest["created_at"] = timestamp
    manifest["updated_at"] = timestamp

    assert expected_code in _codes(manifest)


@pytest.mark.parametrize(
    ("name", "mutation", "expected_code"),
    [
        ("namespace", "namespace", "MANIFEST_ID_NAMESPACE_COLLISION"),
        ("storage-name", "storage-name", "MANIFEST_STORAGE_NAME_ID_MISMATCH"),
        ("media-state", "media-state", "MANIFEST_MEDIA_INSPECTION_STATE_INVALID"),
        ("source-page", "source-page", "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE"),
        ("operation-order", "operation-order", "MANIFEST_OPERATION_SEQUENCE_INVALID"),
        ("sequence-replay", "sequence-replay", "MANIFEST_SEQUENCE_REPLAY_MISMATCH"),
        ("overlay-box", "overlay-box", "MANIFEST_NORMALIZED_BOX_INVALID"),
        ("overlay-z", "overlay-z", "MANIFEST_OVERLAY_Z_INDEX_COLLISION"),
        ("asset-reference", "asset-reference", "MANIFEST_ASSET_REFERENCE_UNKNOWN"),
        ("assignment-page", "assignment-page", "MANIFEST_PAGE_REFERENCE_UNKNOWN"),
        ("confirmation-scope", "confirmation-scope", "MANIFEST_CONFIRMATION_SCOPE_UNKNOWN"),
        (
            "assignment-confirmation",
            "assignment-confirmation",
            "MANIFEST_ASSIGNMENT_CONFIRMATION_MISMATCH",
        ),
        ("output-names", "output-names", "MANIFEST_OUTPUT_NAME_COLLISION"),
    ],
)
def test_semantic_graph_mutations_are_rejected(
    name: str,
    mutation: str,
    expected_code: str,
) -> None:
    del name
    manifest = _representative()
    sources = cast(list[dict[str, Any]], manifest["sources"])
    pages = cast(list[dict[str, Any]], manifest["pages"])
    operations = cast(list[dict[str, Any]], manifest["page_operations"])
    overlays = cast(list[dict[str, Any]], manifest["overlays"])
    assignments = cast(list[dict[str, Any]], manifest["assignments"])
    confirmations = cast(list[dict[str, Any]], manifest["confirmations"])
    if mutation == "namespace":
        sources[0]["source_id"] = manifest["case_id"]
        sources[0]["storage_name"] = f"{manifest['case_id']}.bin"
        for page in pages[:2]:
            page["source_id"] = manifest["case_id"]
    elif mutation == "storage-name":
        sources[0]["storage_name"] = "20000000-0000-4000-8000-000000000099.bin"
    elif mutation == "media-state":
        sources[1]["signature_state"] = "unknown"
    elif mutation == "source-page":
        pages[1]["source_page_index"] = 2
    elif mutation == "operation-order":
        operations[1]["sequence"] = 3
    elif mutation == "sequence-replay":
        manifest["page_sequence"] = list(reversed(cast(list[str], manifest["page_sequence"])))
    elif mutation == "overlay-box":
        cast(dict[str, Any], overlays[0]["box"])["width"] = 950_000
    elif mutation == "overlay-z":
        overlays[1]["z_index"] = overlays[0]["z_index"]
    elif mutation == "asset-reference":
        cast(dict[str, Any], overlays[1]["payload"])["asset_id"] = (
            "40000000-0000-4000-8000-000000000099"
        )
    elif mutation == "assignment-page":
        assignments[0]["page_ids"] = ["30000000-0000-4000-8000-000000000099"]
    elif mutation == "confirmation-scope":
        cast(dict[str, Any], confirmations[0]["scope"])["source_id"] = (
            "20000000-0000-4000-8000-000000000099"
        )
    elif mutation == "assignment-confirmation":
        assignments[0]["confirmation_id"] = confirmations[0]["confirmation_id"]
    elif mutation == "output-names":
        constraints = cast(dict[str, Any], manifest["constraints"])
        constraints["requested_output_names"] = ["CON.pdf", "con.PDF"]
    else:
        raise AssertionError(mutation)

    assert expected_code in _codes(manifest)


def test_schema_diagnostics_never_include_unknown_members_or_values() -> None:
    canary_member = "private_applicant_name"
    canary_value = "CANARY APPLICANT FULL NAME"
    manifest = _minimal()
    manifest[canary_member] = canary_value

    issues = _issues(manifest)
    rendered = repr(issues)

    assert issues
    assert canary_member not in rendered
    assert canary_value not in rendered
    assert all(issue.path == "$" for issue in issues)
