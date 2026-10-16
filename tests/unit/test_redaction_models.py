"""PWS-0602 inert intent, closed state transitions, migration, and schema evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_migrations import (
    migrate_manifest_v1_to_v2,
    migrate_manifest_v2_to_v3,
)
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestRepositoryError,
    ManifestSnapshot,
)
from paperworkshell.domain.manifest_validation import (
    CURRENT_SCHEMA_FINGERPRINT,
    validate_manifest,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import PageCommandHistory, PageOperationError
from paperworkshell.domain.redaction import (
    MAX_REDACTION_MARKS,
    PERMANENT_REDACTION_ENABLED,
    RedactionError,
    RedactionErrorCode,
    RedactionMark,
    RedactionMarkCollection,
    RedactionMarkState,
    require_redaction_export_allowed,
    transition_redaction_state,
)
from paperworkshell.overlays.commands import OverlayCommandHistory
from paperworkshell.overlays.models import OverlayError
from paperworkshell.storage.manifest_codec import (
    probe_supported_manifest_schema_version,
    summary_from_manifest,
)
from tools.validate_manifests import validate_manifest_data

ROOT = Path(__file__).resolve().parents[2]
INVALID = ROOT / "tests/fixtures/manifests/v3/invalid"


def _load(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _document() -> dict[str, Any]:
    return _load("schemas/examples/case-manifest-v3-representative.json")


def _schema(version: int = 3) -> dict[str, Any]:
    return _load(f"schemas/case-manifest-v{version}.schema.json")


def _snapshot(document: dict[str, Any]) -> ManifestSnapshot:
    return ManifestSnapshot(
        UUID(document["case_id"]), 3, document["revision"], document, "a" * 64, "b" * 24
    )


def test_current_schema_copies_identity_and_both_examples_are_valid() -> None:
    canonical = (ROOT / "schemas/case-manifest-v3.schema.json").read_bytes()
    assert (
        canonical == (ROOT / "src/paperworkshell/storage/case-manifest-v3.schema.json").read_bytes()
    )
    assert (
        hashlib.sha256(
            json.dumps(_schema(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        == CURRENT_SCHEMA_FINGERPRINT
    )
    for kind in ("minimal", "representative"):
        document = _load(f"schemas/examples/case-manifest-v3-{kind}.json")
        before = copy.deepcopy(document)
        assert validate_manifest(document, _schema()) == ()
        assert validate_manifest_data(document, _schema()) == []
        assert document == before
        assert probe_supported_manifest_schema_version(json.dumps(document).encode()) == 3
        assert summary_from_manifest(document, UUID(document["case_id"])).manifest_version == 3


@pytest.mark.parametrize("path", sorted(INVALID.glob("*.json")), ids=lambda path: path.stem)
def test_invalid_current_future_and_forged_state_fixtures_fail_without_mutation(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    before = copy.deepcopy(document)
    assert validate_manifest(document, _schema())
    assert validate_manifest_data(document, _schema())
    assert document == before


@pytest.mark.parametrize("version", [1, 2])
def test_old_schema_bytes_remain_frozen_and_old_graphs_migrate_losslessly(version: int) -> None:
    document = _load(f"schemas/examples/case-manifest-v{version}-representative.json")
    before = copy.deepcopy(document)
    assert validate_manifest(document, _schema(version)) == ()
    if version == 1:
        document = migrate_manifest_v1_to_v2(document)
        assert document["schema_version"] == 2
    migrated = migrate_manifest_v2_to_v3(document)
    assert migrated["schema_version"] == 3
    assert migrated["redaction_marks"] == []
    assert validate_manifest(migrated, _schema()) == ()
    for key, value in before.items():
        if key not in {"schema_version", "revision"}:
            assert migrated[key] == value
            if isinstance(value, dict | list):
                assert migrated[key] is not value
    assert migrated["overlays"] == before["overlays"]
    assert _load(f"schemas/examples/case-manifest-v{version}-representative.json") == before
    assert (ROOT / f"schemas/case-manifest-v{version}.schema.json").read_bytes() == (
        ROOT / f"src/paperworkshell/storage/case-manifest-v{version}.schema.json"
    ).read_bytes()


@pytest.mark.parametrize("version", [True, "2", 1, 3, 4])
def test_v2_migration_rejects_wrong_dispatch_without_mutation(version: object) -> None:
    document = _load("schemas/examples/case-manifest-v2-minimal.json")
    document["schema_version"] = version
    before = copy.deepcopy(document)
    with pytest.raises(ManifestRepositoryError):
        migrate_manifest_v2_to_v3(document)
    assert document == before


def test_v2_migration_rejects_revision_exhaustion_and_injected_marks() -> None:
    document = _load("schemas/examples/case-manifest-v2-minimal.json")
    document["revision"] = MAX_SAFE_MANIFEST_INTEGER
    with pytest.raises(ManifestRepositoryError):
        migrate_manifest_v2_to_v3(document)
    document["revision"] = 0
    document["redaction_marks"] = []
    with pytest.raises(ManifestRepositoryError):
        migrate_manifest_v2_to_v3(document)


def test_marks_roundtrip_in_full_page_normalized_space_and_keep_order() -> None:
    document = _document()
    before = copy.deepcopy(document)
    collection = RedactionMarkCollection.from_manifest_document(document)
    mark = collection.marks[0]
    assert mark.state is RedactionMarkState.UNAPPLIED
    assert mark.box == NormalizedPageBox(100000, 100000, 300000, 100000)
    assert mark.to_manifest_record() == document["redaction_marks"][0]
    result = collection.to_manifest_document(document)
    assert result == document == before
    assert result is not document
    assert result["redaction_marks"] is not document["redaction_marks"]
    assert "d1000000" not in repr(mark)
    assert "100000" not in repr(mark)


@pytest.mark.parametrize("state", list(RedactionMarkState))
def test_each_review_state_has_closed_content_free_text(state: RedactionMarkState) -> None:
    assert state.display_label
    assert state.description
    assert state.display_label.split(" — ")[0].lower() == state.value


@pytest.mark.parametrize("target", list(RedactionMarkState))
def test_unapplied_state_can_never_be_promoted_while_gate_is_closed(
    target: RedactionMarkState,
) -> None:
    assert PERMANENT_REDACTION_ENABLED is False
    if target is RedactionMarkState.UNAPPLIED:
        assert transition_redaction_state(RedactionMarkState.UNAPPLIED, target) is target
    else:
        with pytest.raises(RedactionError) as raised:
            transition_redaction_state(RedactionMarkState.UNAPPLIED, target)
        assert raised.value.code is RedactionErrorCode.PIPELINE_DISABLED


@pytest.mark.parametrize(
    "current", [state for state in RedactionMarkState if state.value != "unapplied"]
)
@pytest.mark.parametrize("target", list(RedactionMarkState))
def test_authored_pipeline_states_cannot_transition_back_into_trusted_intent(
    current: RedactionMarkState, target: RedactionMarkState
) -> None:
    with pytest.raises(RedactionError) as raised:
        transition_redaction_state(current, target)
    assert raised.value.code is RedactionErrorCode.PIPELINE_DISABLED


@pytest.mark.parametrize("state", ["applied", "verified", "failed", "unapplied\n", True, None])
def test_serialized_pipeline_claims_never_create_a_verified_mark(state: object) -> None:
    document = _document()
    document["redaction_marks"][0]["state"] = state
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection.from_manifest_document(document)
    assert raised.value.code is RedactionErrorCode.STATE_INVALID


def test_ordinary_shapes_cannot_be_decoded_as_removal_marks() -> None:
    shape = next(
        record for record in _document()["overlays"] if record["payload"]["kind"] == "shape"
    )
    with pytest.raises(RedactionError) as raised:
        RedactionMark.from_manifest_record(shape)
    assert raised.value.code is RedactionErrorCode.MANIFEST_INVALID


def test_reflected_coordinate_and_identity_mutation_still_fail_with_closed_errors() -> None:
    document = _document()
    mark = RedactionMarkCollection.from_manifest_document(document).marks[0]
    object.__setattr__(mark.box, "x", -1)
    with pytest.raises(RedactionError) as raised:
        RedactionMark(mark.mark_id, mark.page_id, mark.box)
    assert raised.value.code is RedactionErrorCode.BOX_INVALID
    with pytest.raises(RedactionError) as raised:
        mark.to_manifest_record()
    assert raised.value.code is RedactionErrorCode.BOX_INVALID
    mark = RedactionMarkCollection.from_manifest_document(document).marks[0]
    object.__setattr__(mark, "mark_id", [])
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection((mark,))
    assert raised.value.code is RedactionErrorCode.IDENTIFIER_INVALID


@pytest.mark.parametrize("field", ["verified", "pipeline_passed", "derivative_path", "text"])
def test_extra_authored_evidence_and_content_fields_are_rejected(field: str) -> None:
    document = _document()
    document["redaction_marks"][0][field] = "PRIVATE_CANARY"
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection.from_manifest_document(document)
    assert "PRIVATE_CANARY" not in str(raised.value)
    assert "PRIVATE_CANARY" not in repr(raised.value)
    assert validate_manifest(document, _schema())


def test_duplicate_unknown_page_out_of_bounds_and_global_collision_are_rejected() -> None:
    document = _document()
    document["redaction_marks"].append(copy.deepcopy(document["redaction_marks"][0]))
    assert "MANIFEST_REDACTION_MARK_ID_DUPLICATE" in {
        issue.code for issue in validate_manifest(document, _schema())
    }
    with pytest.raises(RedactionError):
        RedactionMarkCollection.from_manifest_document(document)
    document = _document()
    document["redaction_marks"][0]["mark_id"] = document["overlays"][0]["overlay_id"]
    assert "MANIFEST_ID_NAMESPACE_COLLISION" in {
        issue.code for issue in validate_manifest(document, _schema())
    }
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection.from_manifest_document(document)
    assert raised.value.code is RedactionErrorCode.IDENTIFIER_COLLISION
    document = _document()
    document["redaction_marks"][0]["page_id"] = "d2000000-0000-4000-8000-000000000001"
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection.from_manifest_document(document)
    assert raised.value.code is RedactionErrorCode.PAGE_UNKNOWN
    document = _document()
    document["redaction_marks"][0]["box"]["width"] = 1000000
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection.from_manifest_document(document)
    assert raised.value.code is RedactionErrorCode.BOX_INVALID


def test_collection_and_manifest_bounds_reject_excess_marks() -> None:
    document = _document()
    record = document["redaction_marks"][0]
    document["redaction_marks"] = [record] * (MAX_REDACTION_MARKS + 1)
    with pytest.raises(RedactionError):
        RedactionMarkCollection.from_manifest_document(document)
    mark = RedactionMark.from_manifest_record(record)
    with pytest.raises(RedactionError) as raised:
        RedactionMarkCollection((mark,) * (MAX_REDACTION_MARKS + 1))
    assert raised.value.code is RedactionErrorCode.CAPACITY_EXCEEDED


def test_configured_maximum_mark_count_roundtrips_without_changing_sources() -> None:
    document = _document()
    before_sources = copy.deepcopy(document["sources"])
    prototype = RedactionMarkCollection.from_manifest_document(document).marks[0]
    collection = RedactionMarkCollection(
        tuple(
            RedactionMark(
                UUID(f"d1000000-0000-4000-8000-{index:012x}"),
                prototype.page_id,
                prototype.box,
            )
            for index in range(1, MAX_REDACTION_MARKS + 1)
        )
    )
    candidate = collection.to_manifest_document(document)
    assert (
        len(RedactionMarkCollection.from_manifest_document(candidate).marks) == MAX_REDACTION_MARKS
    )
    assert candidate["sources"] == before_sources
    assert validate_manifest(candidate, _schema()) == ()


def test_export_refuses_active_intent_and_preserves_removed_page_intent() -> None:
    document = _document()
    document["page_sequence"] = [document["redaction_marks"][0]["page_id"]]
    with pytest.raises(RedactionError) as raised:
        require_redaction_export_allowed(document)
    assert raised.value.code is RedactionErrorCode.PIPELINE_DISABLED
    document["page_sequence"] = []
    require_redaction_export_allowed(document)
    assert RedactionMarkCollection.from_manifest_document(document).marks


@pytest.mark.parametrize("version", [1, 2])
def test_old_exports_pass_only_without_a_forged_removal_collection(version: int) -> None:
    document = _load(f"schemas/examples/case-manifest-v{version}-representative.json")
    require_redaction_export_allowed(document)
    document["redaction_marks"] = []
    with pytest.raises(RedactionError):
        require_redaction_export_allowed(document)


def test_existing_page_and_overlay_histories_reject_mark_namespace_collisions() -> None:
    document = _document()
    document["redaction_marks"][0]["mark_id"] = document["overlays"][0]["overlay_id"]
    with pytest.raises(PageOperationError):
        PageCommandHistory.from_snapshot(_snapshot(document))
    with pytest.raises(OverlayError):
        OverlayCommandHistory.from_snapshot(_snapshot(document))
