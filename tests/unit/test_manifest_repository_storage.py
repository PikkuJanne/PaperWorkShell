"""Focused unit evidence for PWS-0109 storage validation and commit inputs."""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage._windows import FileIdentity
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    canonical_manifest_bytes,
    validate_manifest_payload,
)
from paperworkshell.storage.workspace import CaseWorkspaceManager

ROOT = Path(__file__).resolve().parents[2]
MINIMAL_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-minimal.json"
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000109")
OTHER_CASE_ID = UUID("20000000-0000-4000-8000-000000000209")


def _document(*, case_id: UUID = CASE_ID, revision: int = 0) -> dict[str, Any]:
    loaded = json.loads(MINIMAL_MANIFEST.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    document = cast(dict[str, Any], loaded)
    document["case_id"] = str(case_id)
    document["revision"] = revision
    document["created_at"] = "2026-08-03T09:00:00Z"
    document["updated_at"] = f"2026-08-03T09:00:{revision:02d}Z"
    return document


class _NoStorageManager:
    """Record any unexpected storage access by an invalid pre-I/O request."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.calls.append(name)
        raise AssertionError(f"unexpected storage access: {name}")


def _repository_without_storage() -> tuple[WindowsManifestRepository, _NoStorageManager]:
    manager = _NoStorageManager()
    repository = WindowsManifestRepository(cast(CaseWorkspaceManager, manager))
    return repository, manager


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


def test_canonical_bytes_are_order_independent_and_do_not_mutate_input() -> None:
    document = _document()
    before = copy.deepcopy(document)
    reordered = dict(reversed(tuple(document.items())))

    first = canonical_manifest_bytes(document)
    second = canonical_manifest_bytes(reordered)

    assert first == second
    assert first.endswith(b"\n")
    assert document == before
    validate_manifest_payload(first, CASE_ID)


def test_packaged_runtime_schema_is_the_exact_canonical_contract() -> None:
    legacy = json.loads(
        (ROOT / "schemas" / "case-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    packaged_legacy = json.loads(
        files("paperworkshell.storage")
        .joinpath("case-manifest-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    current = json.loads(
        (ROOT / "schemas" / "case-manifest-v2.schema.json").read_text(encoding="utf-8")
    )
    packaged_current = json.loads(
        files("paperworkshell.storage")
        .joinpath("case-manifest-v2.schema.json")
        .read_text(encoding="utf-8")
    )

    assert packaged_legacy == legacy
    assert packaged_current == current
    assert repository_storage._runtime_schema(1) == legacy
    assert repository_storage._runtime_schema() == current


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("future-version", ManifestRepositoryErrorCode.VERSION_UNSUPPORTED),
        ("invalid-version", ManifestRepositoryErrorCode.MANIFEST_INVALID),
        ("case-mismatch", ManifestRepositoryErrorCode.CASE_MISMATCH),
        ("schema-invalid", ManifestRepositoryErrorCode.MANIFEST_INVALID),
        ("revision-gap", ManifestRepositoryErrorCode.REVISION_CONFLICT),
    ],
)
def test_invalid_save_target_is_rejected_before_any_storage_access(
    mutation: str,
    expected_code: ManifestRepositoryErrorCode,
) -> None:
    repository, manager = _repository_without_storage()
    candidate = _document(revision=1)
    if mutation == "future-version":
        candidate["schema_version"] = 3
    elif mutation == "invalid-version":
        candidate["schema_version"] = "2"
    elif mutation == "case-mismatch":
        candidate["case_id"] = str(OTHER_CASE_ID)
    elif mutation == "schema-invalid":
        constraints = cast(dict[str, Any], candidate["constraints"])
        constraints["maximum_file_count"] = 0
    elif mutation == "revision-gap":
        candidate["revision"] = 2
    else:  # pragma: no cover - closed parametrization
        raise AssertionError(mutation)

    with pytest.raises(ManifestRepositoryError) as raised:
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=0,
            expected_sha256="0" * 64,
            expected_cas_token="1" * 64,
        )

    assert raised.value.code is expected_code
    assert manager.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"value":1.5}',
        b"\xef\xbb\xbf{}",
        b"[]",
        b"{",
    ],
)
def test_strict_payload_failures_are_sanitized_and_never_echo_bytes(payload: bytes) -> None:
    with pytest.raises(ManifestRepositoryError) as raised:
        validate_manifest_payload(payload, CASE_ID)

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    decoded = payload.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in rendered


def test_future_version_dispatch_precedes_current_version_float_rules() -> None:
    future = _document()
    future["schema_version"] = 3
    future["future_shape"] = {"fraction": 1.25, "members": ["unknown"]}
    payload = json.dumps(future, separators=(",", ":"), sort_keys=True).encode() + b"\n"

    with pytest.raises(ManifestRepositoryError) as raised:
        validate_manifest_payload(payload, CASE_ID)

    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED


def test_cas_token_is_deterministic_and_bound_to_identity_and_payload_digest() -> None:
    first_identity = FileIdentity(11, b"a" * 16)
    second_identity = FileIdentity(11, b"b" * 16)
    first_digest = "0" * 64
    second_digest = "1" * 64

    token = repository_storage._cas_token(first_identity, first_digest)

    assert token == repository_storage._cas_token(first_identity, first_digest)
    assert token != repository_storage._cas_token(second_identity, first_digest)
    assert token != repository_storage._cas_token(first_identity, second_digest)
    assert len(token) == 64
    int(token, 16)


def test_expected_revision_above_json_safe_range_is_invalid_before_storage() -> None:
    repository, manager = _repository_without_storage()

    with pytest.raises(ManifestRepositoryError) as raised:
        repository.save(
            CASE_ID,
            _document(revision=1),
            expected_revision=MAX_SAFE_MANIFEST_INTEGER + 1,
            expected_sha256="0" * 64,
            expected_cas_token="1" * 64,
        )

    assert raised.value.code is ManifestRepositoryErrorCode.MODEL_INVALID
    assert manager.calls == []


@pytest.mark.parametrize(
    "path",
    [
        ("case_id",),
        ("sources", 0, "sha256"),
        ("template_ref", "template_id"),
        ("template_ref", "template_version"),
        ("sources", 0, "storage_name"),
        ("overlays", 0, "payload", "color"),
        ("page_sequence", 0),
    ],
)
def test_newline_suffixed_exact_tokens_fail_before_storage_access(
    path: tuple[str | int, ...],
) -> None:
    repository, manager = _repository_without_storage()
    loaded = json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    candidate = cast(dict[str, Any], loaded)
    candidate["case_id"] = str(CASE_ID)
    candidate["revision"] = 1
    current: object = candidate
    for part in path:
        if type(part) is int:
            assert isinstance(current, list)
            current = current[part]
        else:
            assert isinstance(current, dict)
            current = current[part]
    assert type(current) is str
    _set_nested(candidate, path, f"{current}\n")

    with pytest.raises(ManifestRepositoryError) as raised:
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=0,
            expected_sha256="0" * 64,
            expected_cas_token="1" * 64,
        )

    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    assert manager.calls == []
