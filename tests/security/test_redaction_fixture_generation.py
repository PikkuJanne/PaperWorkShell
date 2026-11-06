"""Real Windows ancestry checks for the PWS-0601 source inventory writer.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.security.test_export_destination_security import _create_junction, _remove_junction
from tools import generate_synthetic_fixtures as generator
from tools import validate_synthetic_fixtures as validator
from tools.generate_synthetic_fixtures import FixtureGenerationError, GeneratedFixture
from tools.redaction_corpus import CANARY_INVENTORY_PATH, canonical_canary_inventory_bytes
from tools.validate_synthetic_fixtures import FixtureIssue, validate_redaction_canary_inventory

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0601 junction evidence targets the supported Windows platform"
)


def test_inventory_writer_reauthenticates_ancestor_after_source_and_golden_generation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside_version = outside / "v1"
    outside_version.mkdir(parents=True)
    sentinel = outside_version / "sentinel.bin"
    sentinel.write_bytes(b"outside-sentinel")
    ancestor = repository / CANARY_INVENTORY_PATH.parent.parent
    parked = ancestor.with_name("redaction-original")
    original_write_goldens = generator._write_renderer_goldens
    swapped = False

    def redirect_inventory_ancestor(root: Path, fixtures: Sequence[GeneratedFixture]) -> int:
        nonlocal swapped
        count = original_write_goldens(root, fixtures)
        ancestor.rename(parked)
        _create_junction(ancestor, outside)
        swapped = True
        return count

    failure_code: str | None = None
    try:
        with patch.object(generator, "_write_renderer_goldens", redirect_inventory_ancestor):
            try:
                generator.write_committed_corpus(repository)
            except FixtureGenerationError as error:
                failure_code = error.code

        assert swapped, "REDACTION_INVENTORY_ANCESTOR_SWAP_NOT_EXERCISED"
        assert failure_code == "FIXTURE_OUTPUT_UNSAFE", "REDACTION_INVENTORY_ANCESTOR_SWAP_ACCEPTED"
        assert not (outside_version / CANARY_INVENTORY_PATH.name).exists(), (
            "REDACTION_INVENTORY_WRITE_ESCAPED_REPOSITORY"
        )
        assert sentinel.read_bytes() == b"outside-sentinel", "REDACTION_OUTSIDE_SENTINEL_CHANGED"
        assert {path.name for path in outside_version.iterdir()} == {"sentinel.bin"}, (
            "REDACTION_INVENTORY_LEFT_OUTSIDE_ARTIFACTS"
        )
    finally:
        if os.path.isjunction(ancestor):
            _remove_junction(ancestor)
        if parked.exists() and not ancestor.exists():
            parked.rename(ancestor)


def test_inventory_validator_rejects_a_real_junction_ancestor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    ancestor = repository / CANARY_INVENTORY_PATH.parent.parent
    ancestor.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside_version = outside / "v1"
    outside_version.mkdir(parents=True)
    outside_inventory = outside_version / CANARY_INVENTORY_PATH.name
    outside_inventory.write_bytes(b"outside-inventory-sentinel")
    _create_junction(ancestor, outside)
    try:
        issues = validate_redaction_canary_inventory(repository)

        assert {issue.code for issue in issues} == {"REDACTION_CANARY_PATH_UNSAFE"}, (
            "REDACTION_INVENTORY_JUNCTION_ANCESTOR_ACCEPTED"
        )
        assert outside_inventory.read_bytes() == b"outside-inventory-sentinel", (
            "REDACTION_OUTSIDE_INVENTORY_CHANGED"
        )
    finally:
        _remove_junction(ancestor)


def test_inventory_validator_reauthenticates_ancestor_after_directory_enumeration(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    inventory = repository / CANARY_INVENTORY_PATH
    inventory.parent.mkdir(parents=True)
    inventory.write_bytes(canonical_canary_inventory_bytes())
    ancestor = inventory.parent.parent
    parked = ancestor.with_name("redaction-original")
    outside = tmp_path / "outside"
    outside_version = outside / "v1"
    outside_version.mkdir(parents=True)
    outside_inventory = outside_version / CANARY_INVENTORY_PATH.name
    outside_inventory.write_bytes(b"{}")
    original_enumerate = validator._enumerate_committed_files
    swapped = False

    def redirect_inventory_ancestor(
        directory_root: Path, *, document: str
    ) -> tuple[set[Path] | None, list[FixtureIssue]]:
        nonlocal swapped
        ancestor.rename(parked)
        _create_junction(ancestor, outside)
        swapped = True
        return original_enumerate(directory_root, document=document)

    try:
        with patch.object(validator, "_enumerate_committed_files", redirect_inventory_ancestor):
            issues = validate_redaction_canary_inventory(repository)

        assert swapped, "REDACTION_INVENTORY_VALIDATION_SWAP_NOT_EXERCISED"
        assert {issue.code for issue in issues} == {"REDACTION_CANARY_PATH_UNSAFE"}, (
            "REDACTION_INVENTORY_VALIDATION_SWAP_ACCEPTED"
        )
        assert outside_inventory.read_bytes() == b"{}", "REDACTION_OUTSIDE_INVENTORY_CHANGED"
    finally:
        if os.path.isjunction(ancestor):
            _remove_junction(ancestor)
        if parked.exists() and not ancestor.exists():
            parked.rename(ancestor)
