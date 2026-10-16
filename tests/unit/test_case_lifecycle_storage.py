"""Unit evidence for bounded lifecycle manifests and the concrete store contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleStore
from paperworkshell.domain.case_lifecycle import CaseLifecycleError, CaseLifecycleErrorCode
from paperworkshell.storage.case_lifecycle import (
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_MANIFEST_COLLECTION_ITEMS,
    WindowsCaseLifecycleStore,
    _decode_manifest,
    _summary_from_manifest,
    build_initial_manifest_bytes,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tools.validate_manifests import load_current_manifest_schema, validate_manifest_data

CASE_ID = UUID("10000000-0000-4000-8000-000000000123")
TIMESTAMP = datetime(2026, 8, 2, 10, 11, 12, 123456, tzinfo=UTC)


def _initial_document() -> dict[str, Any]:
    loaded = json.loads(build_initial_manifest_bytes(CASE_ID, TIMESTAMP))
    return cast(dict[str, Any], loaded)


def test_initial_manifest_is_canonical_schema_valid_empty_v3() -> None:
    payload = build_initial_manifest_bytes(CASE_ID, TIMESTAMP)
    document = _initial_document()
    schema, schema_issues = load_current_manifest_schema()

    assert schema is not None, [issue.render() for issue in schema_issues]
    assert validate_manifest_data(document, schema, document="generated-initial.json") == []
    assert payload.endswith(b"\n")
    assert payload == build_initial_manifest_bytes(CASE_ID, TIMESTAMP)
    assert document["schema_version"] == 3
    assert document["redaction_marks"] == []
    assert document["case_id"] == str(CASE_ID)
    assert document["revision"] == 0
    assert document["created_at"] == "2026-08-02T10:11:12.123456Z"
    assert document["updated_at"] == document["created_at"]
    assert document["constraints"] == {
        "allowed_output_modes": ["combined_pdf"],
        "constraints_version": 1,
        "encryption_policy": "must_be_unencrypted",
    }
    assert document["export_history"] == {
        "entries": [],
        "storage_name": "export-history.json",
    }
    assert "display_name" not in document
    assert "path" not in document


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 2),
        datetime(2026, 8, 2, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_initial_manifest_requires_an_exact_utc_clock(timestamp: datetime) -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        build_initial_manifest_bytes(CASE_ID, timestamp)

    assert raised.value.code is CaseLifecycleErrorCode.CREATE_FAILED


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b'{"schema_version":1.5}',
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"[]",
    ],
)
def test_strict_manifest_decoder_rejects_ambiguous_or_nonstandard_json(payload: bytes) -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        _decode_manifest(payload)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID
    rendered_input = payload.decode("utf-8", errors="ignore")
    if rendered_input:
        assert rendered_input not in str(raised.value)


def test_future_version_dispatch_happens_before_other_envelope_validation() -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        _summary_from_manifest({"schema_version": 4}, CASE_ID)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED


def test_current_manifest_lifecycle_envelope_is_parsed_without_document_content() -> None:
    summary = _summary_from_manifest(_initial_document(), CASE_ID)

    assert summary.case_id == CASE_ID
    assert summary.manifest_version == 3
    assert summary.revision == 0
    assert summary.created_at == TIMESTAMP
    assert summary.updated_at == TIMESTAMP
    assert "path" not in repr(summary).casefold()


def test_lifecycle_envelope_rejects_oversized_collection() -> None:
    document = _initial_document()
    document["sources"] = [None] * (MAX_MANIFEST_COLLECTION_ITEMS + 1)

    with pytest.raises(CaseLifecycleError) as raised:
        _summary_from_manifest(document, CASE_ID)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID


def test_json_resource_walk_rejects_excessive_nesting() -> None:
    value = "0"
    for _ in range(MAX_JSON_DEPTH + 1):
        value = f"[{value}]"
    payload = f'{{"schema_version":1,"nested":{value}}}'.encode()

    with pytest.raises(CaseLifecycleError) as raised:
        _decode_manifest(payload)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID


def test_json_resource_walk_rejects_container_before_unbounded_stack_growth() -> None:
    payload = json.dumps({"items": [0] * MAX_JSON_NODES}, separators=(",", ":")).encode()

    with pytest.raises(CaseLifecycleError) as raised:
        _decode_manifest(payload)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID


def test_windows_store_satisfies_application_protocol_without_importing_app() -> None:
    manager = CaseWorkspaceManager(
        profile_root=Path.cwd(),
        cases_root=Path.cwd() / "nonexistent-lifecycle-protocol-cases",
        quota=WorkspaceQuota(1_000_000, 100_000, 100_000, 100, 10),
    )
    store = WindowsCaseLifecycleStore(manager)

    assert isinstance(store, CaseLifecycleStore)
