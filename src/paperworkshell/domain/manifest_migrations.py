"""Pure, copy-only case-manifest migrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    INTERMEDIATE_MANIFEST_SCHEMA_VERSION,
    LEGACY_MANIFEST_SCHEMA_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestRepositoryErrorCode,
    copy_manifest_document,
    reject_manifest_repository,
)


def migrate_manifest_v1_to_v2(document: Mapping[str, object]) -> dict[str, object]:
    """Return the unique lossless v1-to-v2 candidate without mutating input.

    Validation of the complete frozen v1 source and complete v2 result belongs
    to the repository boundary. This pure step changes only the structural
    dispatch version and the monotonic persistence revision. It never invents
    scan-cleanup intent.
    """

    copied = copy_manifest_document(document, context="manifest.migration_document")
    version = copied.get("schema_version")
    if type(version) is not int:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID,
            "manifest.migration_version",
        )
    if version != LEGACY_MANIFEST_SCHEMA_VERSION:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.VERSION_UNSUPPORTED,
            "manifest.migration_version",
        )
    revision = copied.get("revision")
    if type(revision) is not int or not 0 <= revision < MAX_SAFE_MANIFEST_INTEGER:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID,
            "manifest.migration_revision",
        )
    copied["schema_version"] = INTERMEDIATE_MANIFEST_SCHEMA_VERSION
    copied["revision"] = revision + 1
    return copied


def migrate_manifest_v2_to_v3(document: Mapping[str, object]) -> dict[str, object]:
    """Copy validated v2 intent and add an empty permanent-removal collection.

    Existing ordinary shapes and highlights are preserved byte-for-value and
    never promoted into removal marks. The repository validates both versions
    before publishing one atomic migration transaction.
    """

    copied = copy_manifest_document(document, context="manifest.migration_document")
    version = copied.get("schema_version")
    if type(version) is not int:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_version"
        )
    if version != INTERMEDIATE_MANIFEST_SCHEMA_VERSION:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.migration_version"
        )
    revision = copied.get("revision")
    if type(revision) is not int or not 0 <= revision < MAX_SAFE_MANIFEST_INTEGER:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_revision"
        )
    if "redaction_marks" in copied:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_marks"
        )
    copied["schema_version"] = CURRENT_MANIFEST_SCHEMA_VERSION
    copied["revision"] = revision + 1
    copied["redaction_marks"] = []
    return copied


@dataclass(frozen=True, slots=True)
class ManifestMigrationStep:
    """One closed, reviewed transition; never executable template/plugin data."""

    source_version: int
    target_version: int


MANIFEST_MIGRATION_REGISTRY: Final = (
    ManifestMigrationStep(LEGACY_MANIFEST_SCHEMA_VERSION, INTERMEDIATE_MANIFEST_SCHEMA_VERSION),
    ManifestMigrationStep(INTERMEDIATE_MANIFEST_SCHEMA_VERSION, CURRENT_MANIFEST_SCHEMA_VERSION),
)


def manifest_migration_path(source_version: int) -> tuple[ManifestMigrationStep, ...]:
    """Return a bounded route to current, refusing unknown/future dispatch first."""

    if type(source_version) is not int:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_version"
        )
    if source_version == CURRENT_MANIFEST_SCHEMA_VERSION:
        return ()
    for index, step in enumerate(MANIFEST_MIGRATION_REGISTRY):
        if step.source_version == source_version:
            return MANIFEST_MIGRATION_REGISTRY[index:]
    reject_manifest_repository(
        ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.migration_version"
    )


def migrate_manifest_to_current(document: Mapping[str, object]) -> dict[str, object]:
    """Apply the closed route to a copy as one durable persistence revision.

    Complete frozen-source and result validation remains the storage boundary's
    responsibility. Current data is copied without advancing its revision.
    Historical intermediate versions are never published or used as defaults.
    """

    candidate = copy_manifest_document(document, context="manifest.migration_document")
    version = candidate.get("schema_version")
    if type(version) is not int:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_version"
        )
    path = manifest_migration_path(version)
    if not path:
        return candidate
    revision = candidate.get("revision")
    if type(revision) is not int or not 0 <= revision < MAX_SAFE_MANIFEST_INTEGER:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_revision"
        )
    for step in path:
        candidate["revision"] = revision
        if step.source_version == LEGACY_MANIFEST_SCHEMA_VERSION:
            candidate = migrate_manifest_v1_to_v2(candidate)
        else:
            candidate = migrate_manifest_v2_to_v3(candidate)
    return candidate
