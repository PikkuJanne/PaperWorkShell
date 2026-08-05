from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import (
    ManifestAutosaveResult,
    ManifestAutosaveStatus,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
    copy_manifest_document,
)


def _case_id() -> UUID:
    return UUID("90000000-0000-4000-8000-000000000001")


def _document(*, revision: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": str(_case_id()),
        "revision": revision,
        "created_at": "2026-08-03T10:00:00Z",
        "updated_at": "2026-08-03T10:00:00Z",
        "page_sequence": ["synthetic-page"],
    }


def _snapshot(*, revision: int = 0) -> ManifestSnapshot:
    return ManifestSnapshot(
        _case_id(),
        1,
        revision,
        _document(revision=revision),
        f"{revision + 1:064x}",
        f"cas_token_{revision:016d}",
    )


def test_snapshot_is_path_free_hides_sensitive_state_and_returns_defensive_copies() -> None:
    document = _document()
    pages = cast(list[object], document["page_sequence"])
    snapshot = ManifestSnapshot(
        _case_id(),
        1,
        0,
        document,
        "1" * 64,
        "cas_token_0000000000000000",
    )

    assert "synthetic-page" not in repr(snapshot)
    assert snapshot.sha256 not in repr(snapshot)
    assert snapshot.cas_token not in repr(snapshot)
    assert not hasattr(snapshot, "path")
    first = snapshot.document
    cast(list[object], first["page_sequence"]).append("caller-mutation")
    pages.append("source-mutation")

    assert snapshot.document["page_sequence"] == ["synthetic-page"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("case_id", UUID("90000000-0000-3000-8000-000000000001")),
        ("schema_version", 2),
        ("revision", True),
        ("revision", -1),
        ("sha256", "SENSITIVE-invalid-digest"),
        ("cas_token", "short"),
    ),
)
def test_snapshot_rejects_invalid_models_without_echoing_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "case_id": _case_id(),
        "schema_version": 1,
        "revision": 0,
        "document": _document(),
        "sha256": "1" * 64,
        "cas_token": "cas_token_0000000000000000",
    }
    values[field] = value

    with pytest.raises(ManifestRepositoryError) as raised:
        ManifestSnapshot(**values)  # type: ignore[arg-type]

    assert raised.value.code is ManifestRepositoryErrorCode.MODEL_INVALID
    assert str(value) not in repr(raised.value)
    assert str(value) not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", 2),
        ("case_id", "90000000-0000-4000-8000-000000000002"),
        ("revision", 1),
    ),
)
def test_snapshot_rejects_document_metadata_mismatch(field: str, value: object) -> None:
    document = _document()
    document[field] = value

    with pytest.raises(ManifestRepositoryError):
        ManifestSnapshot(
            _case_id(),
            1,
            0,
            document,
            "1" * 64,
            "cas_token_0000000000000000",
        )


def test_document_copy_rejects_cycles_non_json_values_and_nonfinite_numbers() -> None:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    invalid_documents: tuple[object, ...] = (
        cycle,
        {"tuple": (1, 2)},
        {"bytes": b"private"},
        {"nan": float("nan")},
        {1: "non-string-key"},
    )

    for document in invalid_documents:
        with pytest.raises(ManifestRepositoryError) as raised:
            copy_manifest_document(cast(dict[str, object], document))
        assert raised.value.code is ManifestRepositoryErrorCode.MODEL_INVALID
        assert "private" not in repr(raised.value)


def test_closed_result_types_reject_inconsistent_status_payloads() -> None:
    snapshot = _snapshot()

    assert ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot).snapshot is snapshot
    assert ManifestSaveResult(ManifestSaveStatus.SAVED, snapshot).snapshot is snapshot
    assert ManifestAutosaveResult(ManifestAutosaveStatus.SAVED, snapshot, 1).saved_generation == 1
    with pytest.raises(ManifestRepositoryError):
        ManifestAutosaveResult(ManifestAutosaveStatus.SAVED, snapshot, None)
    with pytest.raises(ManifestRepositoryError):
        ManifestAutosaveResult(ManifestAutosaveStatus.NO_PENDING, snapshot, 1)
    with pytest.raises(ManifestRepositoryError):
        ManifestLoadResult("current", snapshot)  # type: ignore[arg-type]


def test_error_constructor_closes_code_context_message_and_exception_args() -> None:
    canary = "private-path-and-manifest-value"
    error = ManifestRepositoryError(
        code=canary,  # type: ignore[arg-type]
        context=canary,
    )

    assert error.code is ManifestRepositoryErrorCode.STORAGE_FAILED
    assert error.context == "manifest.repository"
    assert canary not in repr(error)
    assert canary not in str(error)
    assert canary not in repr(error.args)


def test_repository_protocol_is_structural_and_path_free() -> None:
    class Repository:
        def load(self, case_id: UUID) -> ManifestLoadResult:
            del case_id
            return ManifestLoadResult(ManifestLoadStatus.CURRENT, _snapshot())

        def save(
            self,
            case_id: UUID,
            candidate: object,
            *,
            expected_revision: int,
            expected_sha256: str,
            expected_cas_token: str,
        ) -> ManifestSaveResult:
            del (
                case_id,
                candidate,
                expected_revision,
                expected_sha256,
                expected_cas_token,
            )
            return ManifestSaveResult(ManifestSaveStatus.SAVED, _snapshot(revision=1))

    assert isinstance(Repository(), ManifestRepository)
