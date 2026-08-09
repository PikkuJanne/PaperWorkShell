"""Path-free contracts for validated manifest persistence and autosave."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Protocol, runtime_checkable
from uuid import UUID

CURRENT_MANIFEST_SCHEMA_VERSION: Final = 1
MAX_SAFE_MANIFEST_INTEGER: Final = 9_007_199_254_740_991
MAX_MANIFEST_DEPTH: Final = 64
MAX_MANIFEST_NODES: Final = 100_000
MAX_MANIFEST_CONTAINER_MEMBERS: Final = 4_096

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_CAS_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


class ManifestRepositoryErrorCode(StrEnum):
    """Closed registry of safe manifest repository and autosave failures."""

    MODEL_INVALID = "MANIFEST_REPOSITORY_MODEL_INVALID"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "MANIFEST_VERSION_UNSUPPORTED"
    CASE_MISMATCH = "MANIFEST_CASE_MISMATCH"
    REVISION_CONFLICT = "MANIFEST_REVISION_CONFLICT"
    CAPACITY_EXCEEDED = "MANIFEST_CAPACITY_EXCEEDED"
    SAVE_FAILED = "MANIFEST_SAVE_FAILED"
    COMMIT_UNCERTAIN = "MANIFEST_COMMIT_UNCERTAIN"
    RECOVERY_FAILED = "MANIFEST_RECOVERY_FAILED"
    STORAGE_FAILED = "MANIFEST_STORAGE_FAILED"
    AUTOSAVE_FAILED = "MANIFEST_AUTOSAVE_FAILED"
    AUTOSAVE_CLOSED = "MANIFEST_AUTOSAVE_CLOSED"
    CALLBACK_FAILED = "MANIFEST_AUTOSAVE_CALLBACK_FAILED"


_SAFE_ERROR_MESSAGES: Final[dict[ManifestRepositoryErrorCode, str]] = {
    ManifestRepositoryErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid manifest repository data."
    ),
    ManifestRepositoryErrorCode.MANIFEST_INVALID: (
        "The case record is invalid and was not changed."
    ),
    ManifestRepositoryErrorCode.VERSION_UNSUPPORTED: (
        "This case uses an unsupported record version and was not changed."
    ),
    ManifestRepositoryErrorCode.CASE_MISMATCH: (
        "The case record does not belong to the selected case and was not changed."
    ),
    ManifestRepositoryErrorCode.REVISION_CONFLICT: (
        "The case changed before this edit could be saved. Try the edit again."
    ),
    ManifestRepositoryErrorCode.CAPACITY_EXCEEDED: (
        "The selected case does not have enough configured storage capacity."
    ),
    ManifestRepositoryErrorCode.SAVE_FAILED: (
        "PaperWorkShell could not save the case record safely."
    ),
    ManifestRepositoryErrorCode.COMMIT_UNCERTAIN: (
        "PaperWorkShell could not confirm whether the case record was saved."
    ),
    ManifestRepositoryErrorCode.RECOVERY_FAILED: (
        "PaperWorkShell could not recover a valid case record safely."
    ),
    ManifestRepositoryErrorCode.STORAGE_FAILED: (
        "PaperWorkShell could not access the case record safely."
    ),
    ManifestRepositoryErrorCode.AUTOSAVE_FAILED: (
        "PaperWorkShell could not autosave the latest case edit safely."
    ),
    ManifestRepositoryErrorCode.AUTOSAVE_CLOSED: (
        "Autosave is closed and cannot accept another edit."
    ),
    ManifestRepositoryErrorCode.CALLBACK_FAILED: (
        "PaperWorkShell saved the case but could not report autosave completion."
    ),
}


@dataclass(slots=True, repr=False)
class ManifestRepositoryError(ValueError):
    """Stable failure that never includes manifest content, paths, or digests."""

    code: ManifestRepositoryErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ManifestRepositoryErrorCode:
            self.code = ManifestRepositoryErrorCode.STORAGE_FAILED
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "manifest.repository"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ManifestRepositoryError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_manifest_repository(code: ManifestRepositoryErrorCode, context: str) -> Never:
    """Raise one closed, sanitized manifest repository failure."""

    raise ManifestRepositoryError(code=code, context=context)


class ManifestLoadStatus(StrEnum):
    """Authoritative origin of a validated loaded manifest."""

    CURRENT = "current"
    RECOVERED = "recovered"


class ManifestSaveStatus(StrEnum):
    """Closed durable-save outcome."""

    SAVED = "saved"


class ManifestAutosaveStatus(StrEnum):
    """Closed outcome of an explicit autosave flush or close."""

    NO_PENDING = "no_pending"
    SAVED = "saved"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True, init=False)
class ManifestSnapshot:
    """Validated path-free manifest state with defensive document access."""

    case_id: UUID
    schema_version: int
    revision: int
    _document: dict[str, object] = field(repr=False)
    sha256: str = field(repr=False)
    cas_token: str = field(repr=False)

    def __init__(
        self,
        case_id: UUID,
        schema_version: int,
        revision: int,
        document: Mapping[str, object],
        sha256: str,
        cas_token: str,
    ) -> None:
        selected_case = _require_case_id(case_id, context="snapshot.case_id")
        if type(schema_version) is not int or schema_version != CURRENT_MANIFEST_SCHEMA_VERSION:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "snapshot.schema_version",
            )
        _require_revision(revision, context="snapshot.revision")
        manifest_document = copy_manifest_document(document, context="snapshot.document")
        _require_sha256(sha256, context="snapshot.sha256")
        _require_cas_token(cas_token, context="snapshot.cas_token")
        if manifest_document.get("schema_version") != schema_version:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "snapshot.document_version",
            )
        if manifest_document.get("case_id") != str(selected_case):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "snapshot.document_case",
            )
        if manifest_document.get("revision") != revision:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "snapshot.document_revision",
            )
        object.__setattr__(self, "case_id", selected_case)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "_document", manifest_document)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "cas_token", cas_token)

    @property
    def document(self) -> dict[str, object]:
        """Return a fresh JSON-compatible copy of the validated document."""

        return copy_manifest_document(self._document, context="snapshot.document")


@dataclass(frozen=True, slots=True)
class ManifestLoadResult:
    """Validated load outcome, including whether journal recovery was required."""

    status: ManifestLoadStatus
    snapshot: ManifestSnapshot

    def __post_init__(self) -> None:
        if (
            type(self.status) is not ManifestLoadStatus
            or type(self.snapshot) is not ManifestSnapshot
        ):
            reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, "load.result")


@dataclass(frozen=True, slots=True)
class ManifestSaveResult:
    """Validated result of one durable compare-and-swap manifest save."""

    status: ManifestSaveStatus
    snapshot: ManifestSnapshot

    def __post_init__(self) -> None:
        if (
            self.status is not ManifestSaveStatus.SAVED
            or type(self.snapshot) is not ManifestSnapshot
        ):
            reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, "save.result")


@dataclass(frozen=True, slots=True)
class ManifestAutosaveResult:
    """Path-free outcome of coalescing and explicitly flushing queued edits."""

    status: ManifestAutosaveStatus
    snapshot: ManifestSnapshot | None = None
    saved_generation: int | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not ManifestAutosaveStatus:
            reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, "autosave.result")
        if self.snapshot is not None and type(self.snapshot) is not ManifestSnapshot:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID, "autosave.snapshot"
            )
        if self.status is ManifestAutosaveStatus.SAVED:
            if self.snapshot is None or not _is_positive_safe_integer(self.saved_generation):
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.MODEL_INVALID,
                    "autosave.saved_result",
                )
        elif self.saved_generation is not None:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "autosave.generation",
            )


@runtime_checkable
class ManifestRepository(Protocol):
    """Storage port for validated, journaled compare-and-swap manifest state."""

    def load(self, case_id: UUID) -> ManifestLoadResult:
        """Load current valid state or atomically recover the last valid revision."""

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        """Validate and durably publish only if every expected current value matches."""


def copy_manifest_document(
    document: Mapping[str, object],
    *,
    context: str = "manifest.document",
) -> dict[str, object]:
    """Copy one bounded JSON object without retaining caller-owned containers."""

    if not isinstance(document, Mapping):
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    budget = [MAX_MANIFEST_NODES]
    active: set[int] = set()
    try:
        copied = _copy_json_value(document, depth=0, budget=budget, active=active)
    except ManifestRepositoryError:
        raise
    except BaseException:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    if type(copied) is not dict:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return copied


def _copy_json_value(
    value: object,
    *,
    depth: int,
    budget: list[int],
    active: set[int],
) -> object:
    if depth > MAX_MANIFEST_DEPTH or budget[0] <= 0:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MODEL_INVALID,
            "manifest.document_limits",
        )
    budget[0] -= 1
    if value is None or type(value) in {bool, str}:
        return value
    if type(value) is int:
        if not -MAX_SAFE_MANIFEST_INTEGER <= value <= MAX_SAFE_MANIFEST_INTEGER:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "manifest.document_integer",
            )
        return value
    if type(value) is float:
        if not math.isfinite(value):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "manifest.document_number",
            )
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active or len(value) > MAX_MANIFEST_CONTAINER_MEMBERS:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "manifest.document_container",
            )
        active.add(identity)
        try:
            copied_mapping: dict[str, object] = {}
            for key, member in value.items():
                if type(key) is not str:
                    reject_manifest_repository(
                        ManifestRepositoryErrorCode.MODEL_INVALID,
                        "manifest.document_key",
                    )
                copied_mapping[key] = _copy_json_value(
                    member,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
            return copied_mapping
        finally:
            active.remove(identity)
    if type(value) is list:
        identity = id(value)
        if identity in active or len(value) > MAX_MANIFEST_CONTAINER_MEMBERS:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "manifest.document_container",
            )
        active.add(identity)
        try:
            return [
                _copy_json_value(
                    member,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
                for member in value
            ]
        finally:
            active.remove(identity)
    reject_manifest_repository(
        ManifestRepositoryErrorCode.MODEL_INVALID,
        "manifest.document_value",
    )


def _require_case_id(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return value


def _require_revision(value: object, *, context: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return value


def _require_sha256(value: object, *, context: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return value


def _require_cas_token(value: object, *, context: str) -> str:
    if type(value) is not str or _CAS_TOKEN_RE.fullmatch(value) is None:
        reject_manifest_repository(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return value


def _is_positive_safe_integer(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_SAFE_MANIFEST_INTEGER
