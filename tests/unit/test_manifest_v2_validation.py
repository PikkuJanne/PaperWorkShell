"""Focused contract evidence for the released case-manifest v2 transition."""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.domain.manifest_validation import (
    CANONICAL_SCHEMA_FINGERPRINT,
    V2_SCHEMA_FINGERPRINT,
    ManifestValidationIssue,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V1_PATH = ROOT / "schemas" / "case-manifest-v1.schema.json"
SCHEMA_V2_PATH = ROOT / "schemas" / "case-manifest-v2.schema.json"
EXAMPLE_DIRECTORY = ROOT / "schemas" / "examples"
INVALID_DIRECTORY = ROOT / "tests" / "fixtures" / "manifests" / "v2" / "invalid"


def _load(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    return cast(dict[str, Any], loaded)


def _schema(version: int) -> dict[str, Any]:
    return _load(SCHEMA_V1_PATH if version == 1 else SCHEMA_V2_PATH)


def _representative() -> dict[str, Any]:
    return _load(EXAMPLE_DIRECTORY / "case-manifest-v2-representative.json")


def _issues(document: dict[str, Any]) -> tuple[ManifestValidationIssue, ...]:
    return validate_manifest(document, _schema(2))


def _operation(document: dict[str, Any], kind: str) -> dict[str, Any]:
    operations = cast(list[dict[str, Any]], document["page_operations"])
    return next(
        operation
        for operation in operations
        if cast(dict[str, Any], operation["payload"])["kind"] == kind
    )


def test_v2_examples_validate_without_mutation() -> None:
    for path in sorted(EXAMPLE_DIRECTORY.glob("case-manifest-v2-*.json")):
        manifest = _load(path)
        before = copy.deepcopy(manifest)

        assert _issues(manifest) == ()
        assert manifest == before


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("future-version.json", "MANIFEST_VERSION_UNSUPPORTED"),
        ("invalid-deskew-angle.json", "MANIFEST_SCHEMA_VALIDATION"),
        ("legacy-version.json", "MANIFEST_SCHEMA_IDENTITY_MISMATCH"),
        ("mismatched-operation-version.json", "MANIFEST_SCHEMA_VALIDATION"),
    ],
)
def test_v2_negative_fixtures_fail_closed(filename: str, expected_code: str) -> None:
    assert expected_code in {issue.code for issue in _issues(_load(INVALID_DIRECTORY / filename))}


@pytest.mark.parametrize(
    ("kind", "member", "accepted"),
    [
        ("deskew", "angle_millidegrees_clockwise", -15_000),
        ("deskew", "angle_millidegrees_clockwise", 15_000),
        ("contrast", "factor_millionths", 0),
        ("contrast", "factor_millionths", 4_000_000),
        ("background_cleanup", "strength_millionths", 0),
        ("background_cleanup", "strength_millionths", 1_000_000),
    ],
)
def test_v2_scan_transform_integer_bounds_are_inclusive(
    kind: str,
    member: str,
    accepted: int,
) -> None:
    manifest = _representative()
    payload = cast(dict[str, Any], _operation(manifest, kind)["payload"])
    payload[member] = accepted

    assert _issues(manifest) == ()


@pytest.mark.parametrize(
    ("kind", "member", "invalid"),
    [
        ("deskew", "angle_millidegrees_clockwise", -15_001),
        ("deskew", "angle_millidegrees_clockwise", 15_001),
        ("contrast", "factor_millionths", -1),
        ("contrast", "factor_millionths", 4_000_001),
        ("background_cleanup", "strength_millionths", -1),
        ("background_cleanup", "strength_millionths", 1_000_001),
    ],
)
def test_v2_scan_transform_out_of_range_values_are_rejected(
    kind: str,
    member: str,
    invalid: int,
) -> None:
    manifest = _representative()
    payload = cast(dict[str, Any], _operation(manifest, kind)["payload"])
    payload[member] = invalid

    assert "MANIFEST_SCHEMA_VALIDATION" in {issue.code for issue in _issues(manifest)}


def test_released_schema_identities_are_distinct_and_v1_packaged_bytes_stay_frozen() -> None:
    canonical_v1 = SCHEMA_V1_PATH.read_bytes()
    packaged_v1 = (
        files("paperworkshell.storage").joinpath("case-manifest-v1.schema.json").read_bytes()
    )

    assert canonical_v1 == packaged_v1
    assert CANONICAL_SCHEMA_FINGERPRINT == (
        "a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695"
    )
    assert V2_SCHEMA_FINGERPRINT == (
        "af27d3d3569d660638371d840a35b754335b074021c39d20cc4f282df4c7e599"
    )


def test_version_and_schema_identity_dispatch_before_schema_execution() -> None:
    v1 = _load(EXAMPLE_DIRECTORY / "case-manifest-v1-minimal.json")
    v2 = _load(EXAMPLE_DIRECTORY / "case-manifest-v2-minimal.json")

    assert validate_manifest(v1, _schema(1)) == ()
    assert validate_manifest(v2, _schema(2)) == ()
    assert {issue.code for issue in validate_manifest(v1, _schema(2))} == {
        "MANIFEST_SCHEMA_IDENTITY_MISMATCH"
    }
    assert {issue.code for issue in validate_manifest(v2, _schema(1))} == {
        "MANIFEST_SCHEMA_IDENTITY_MISMATCH"
    }
    assert {issue.code for issue in validate_manifest({"schema_version": 4}, {})} == {
        "MANIFEST_VERSION_UNSUPPORTED"
    }
