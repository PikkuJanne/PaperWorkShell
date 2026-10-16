"""Pure, copy-only case-manifest migrations."""

from __future__ import annotations

from collections.abc import Mapping

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
