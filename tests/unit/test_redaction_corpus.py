"""The M6 corpus is a closed recipe and inventory, not a removal implementation.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    PROJECT_ROOT,
    FixtureGenerationError,
    build_index,
    write_committed_corpus,
)
from tools.redaction_corpus import (
    CANARY_INVENTORY_PATH,
    build_canary_inventory,
    build_redaction_fixtures,
    canonical_canary_inventory_bytes,
    redaction_cases,
)
from tools.validate_synthetic_fixtures import validate_redaction_canary_inventory

_EXPECTED_IDS = {
    "pdf-redaction-born-digital",
    "pdf-redaction-scan",
    "pdf-redaction-embedded-image",
    "pdf-redaction-annotation",
    "pdf-redaction-form",
    "pdf-redaction-metadata",
    "pdf-redaction-attachment",
    "pdf-redaction-hidden-layer",
    "pdf-redaction-incremental",
    "pdf-redaction-geometry",
    "pdf-redaction-hidden-ocr",
    "pdf-redaction-malformed",
    "pdf-redaction-visual-cover",
    "png-redaction-scan",
}


def test_redaction_generation_has_no_external_documents_fonts_tools_or_network() -> None:
    failure = AssertionError("a closed synthetic recipe must not read or execute externally")
    with (
        patch("builtins.open", side_effect=failure),
        patch.object(Path, "read_bytes", side_effect=failure),
        patch.object(Path, "read_text", side_effect=failure),
        patch.object(Path, "open", side_effect=failure),
        patch.object(subprocess, "Popen", side_effect=failure),
        patch.object(socket, "socket", side_effect=failure),
    ):
        first = build_redaction_fixtures()
        second = build_redaction_fixtures()
        first_inventory = canonical_canary_inventory_bytes()
        second_inventory = canonical_canary_inventory_bytes()

    assert first == second, "REDACTION_SOURCE_ORACLE_FAILED"
    assert first_inventory == second_inventory, "REDACTION_SOURCE_ORACLE_FAILED"
    assert {fixture.fixture_id for fixture in first} == _EXPECTED_IDS, (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    assert len(first) == len(_EXPECTED_IDS), "REDACTION_SOURCE_ORACLE_FAILED"
    for fixture in first:
        assert len(fixture.content) < 1_048_576, "REDACTION_SOURCE_ORACLE_FAILED"
        assert (
            PROJECT_ROOT / CORPUS_DIRECTORY / fixture.relative_path
        ).read_bytes() == fixture.content, "REDACTION_SOURCE_ORACLE_FAILED"


def test_canary_inventory_has_unique_closed_synthetic_tokens_and_matches_committed_bytes() -> None:
    cases = redaction_cases()
    assert {case.fixture_id for case in cases} == _EXPECTED_IDS, "REDACTION_SOURCE_ORACLE_FAILED"
    canaries = [canary for case in cases for canary in case.canaries]
    assert len(canaries) >= len(cases), "REDACTION_SOURCE_ORACLE_FAILED"
    assert len({canary.canary_id for canary in canaries}) == len(canaries), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    assert len({canary.token for canary in canaries}) == len(canaries), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    for canary in canaries:
        assert canary.token.isascii(), "REDACTION_SOURCE_ORACLE_FAILED"
        assert len(canary.token) >= 12, "REDACTION_SOURCE_ORACLE_FAILED"
        assert canary.extraction_methods, "REDACTION_SOURCE_ORACLE_FAILED"
        assert canary.carrier, "REDACTION_SOURCE_ORACLE_FAILED"

    inventory_bytes = canonical_canary_inventory_bytes()
    inventory = build_canary_inventory()
    assert json.loads(inventory_bytes) == inventory, "REDACTION_SOURCE_ORACLE_FAILED"
    assert inventory["redaction_feature_enabled"] is False, "REDACTION_SOURCE_ORACLE_FAILED"
    assert inventory["successful_removal_claimed"] is False, "REDACTION_SOURCE_ORACLE_FAILED"
    assert inventory["external_source_material"] is False, "REDACTION_SOURCE_ORACLE_FAILED"
    assert (PROJECT_ROOT / CANARY_INVENTORY_PATH).read_bytes() == inventory_bytes, (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    assert validate_redaction_canary_inventory(PROJECT_ROOT) == [], "REDACTION_SOURCE_ORACLE_FAILED"


def test_redaction_fixture_index_preserves_algorithmic_provenance() -> None:
    index = build_index(build_redaction_fixtures())
    fixtures = index["fixtures"]
    assert isinstance(fixtures, list), "REDACTION_SOURCE_ORACLE_FAILED"
    for fixture in fixtures:
        assert isinstance(fixture, dict), "REDACTION_SOURCE_ORACLE_FAILED"
        provenance = fixture["provenance"]
        assert isinstance(provenance, dict), "REDACTION_SOURCE_ORACLE_FAILED"
        assert provenance["kind"] == "algorithmic", "REDACTION_SOURCE_ORACLE_FAILED"
        assert provenance["source_material"] == "none", "REDACTION_SOURCE_ORACLE_FAILED"
        assert provenance["contains_personal_data"] is False, "REDACTION_SOURCE_ORACLE_FAILED"
        assert provenance["review_decision"] == "PWS-D035", "REDACTION_SOURCE_ORACLE_FAILED"


@pytest.mark.parametrize("mutation", ["mutated", "missing", "extra", "invented-canary"])
def test_canary_inventory_mutation_is_rejected(tmp_path: Path, mutation: str) -> None:
    destination = tmp_path / CANARY_INVENTORY_PATH
    destination.parent.mkdir(parents=True)
    destination.write_bytes(canonical_canary_inventory_bytes())
    if mutation == "mutated":
        destination.write_bytes(destination.read_bytes() + b" ")
    elif mutation == "missing":
        destination.unlink()
    elif mutation == "extra":
        destination.with_name("extra.json").write_text("{}", encoding="utf-8")
    else:
        canary = redaction_cases()[0].canaries[0].token.encode("ascii")
        content = destination.read_bytes()
        assert canary in content, "REDACTION_SOURCE_ORACLE_FAILED"
        destination.write_bytes(content.replace(canary, b"PWS-INVENTED-SYNTHETIC-CANARY"))

    issues = validate_redaction_canary_inventory(tmp_path)
    assert issues, "REDACTION_SOURCE_ORACLE_FAILED"
    assert all(issue.code.startswith("REDACTION_CANARY") for issue in issues), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )


@pytest.mark.parametrize(
    "mutation", ["old-version", "future-version", "duplicate-key", "unknown-field"]
)
def test_inventory_versions_and_ambiguous_or_unrecognized_fields_are_rejected(
    tmp_path: Path, mutation: str
) -> None:
    destination = tmp_path / CANARY_INVENTORY_PATH
    destination.parent.mkdir(parents=True)
    original = canonical_canary_inventory_bytes()
    version_entry = b'"schema_version": 1'
    replacements = {
        "old-version": b'"schema_version": 0',
        "future-version": b'"schema_version": 2',
        "duplicate-key": b'"schema_version": 1, "schema_version": 1',
        "unknown-field": b'"schema_version": 1, "unrecognized": false',
    }
    assert version_entry in original, "REDACTION_INVENTORY_VERSION_MISSING"
    destination.write_bytes(original.replace(version_entry, replacements[mutation], 1))

    issues = validate_redaction_canary_inventory(tmp_path)

    assert {issue.code for issue in issues} == {"REDACTION_CANARY_CONTENT_MISMATCH"}, (
        "REDACTION_INVENTORY_TAMPER_ACCEPTED"
    )


def test_canary_inventory_rejects_a_hardlinked_file(tmp_path: Path) -> None:
    destination = tmp_path / CANARY_INVENTORY_PATH
    destination.parent.mkdir(parents=True)
    destination.write_bytes(canonical_canary_inventory_bytes())
    os.link(destination, tmp_path / "linked-inventory.json")

    issues = validate_redaction_canary_inventory(tmp_path)

    assert {issue.code for issue in issues} == {"REDACTION_CANARY_PATH_UNSAFE"}, (
        "REDACTION_INVENTORY_HARDLINK_ACCEPTED"
    )


def test_canary_inventory_rejects_a_linked_ancestor_before_reading(tmp_path: Path) -> None:
    destination = tmp_path / CANARY_INVENTORY_PATH
    destination.parent.mkdir(parents=True)
    destination.write_bytes(canonical_canary_inventory_bytes())
    with patch(
        "tools.validate_synthetic_fixtures._path_is_link_or_reparse",
        side_effect=lambda path: path == destination.parent,
    ):
        issues = validate_redaction_canary_inventory(tmp_path)

    assert {issue.code for issue in issues} == {"REDACTION_CANARY_PATH_UNSAFE"}, (
        "REDACTION_INVENTORY_LINKED_ANCESTOR_ACCEPTED"
    )


def test_generator_rejects_an_extra_canary_inventory_before_writing_sources(tmp_path: Path) -> None:
    destination = tmp_path / CANARY_INVENTORY_PATH
    destination.parent.mkdir(parents=True)
    destination.with_name("extra.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FixtureGenerationError) as raised:
        write_committed_corpus(tmp_path)

    assert raised.value.code == "FIXTURE_REDACTION_INVENTORY_MISMATCH", (
        "REDACTION_GENERATION_INVENTORY_ACCEPTED"
    )
    assert not (tmp_path / CORPUS_DIRECTORY).exists(), "REDACTION_GENERATION_WROTE_BEFORE_REJECTION"
