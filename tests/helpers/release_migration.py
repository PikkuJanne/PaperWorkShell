"""Synthetic release-case setup for native persistence tests; no document parsing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1, ResourcePolicy
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import canonical_manifest_bytes
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000804")


def release_quota(policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1) -> WorkspaceQuota:
    return WorkspaceQuota(
        policy.maximum_case_bytes,
        policy.maximum_cache_bytes,
        policy.maximum_temporary_storage_bytes,
        256,
        16,
    )


def release_case(
    tmp_path: Path,
    version: int = 1,
    *,
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths, dict[str, object], bytes]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=release_quota(policy),
        id_factory=lambda: CASE_ID,
    )
    WindowsCaseLifecycleStore(manager).create_case().close()
    layout = manager.layout(CASE_ID)
    document = cast(
        dict[str, object],
        json.loads(
            (
                ROOT / "schemas" / "examples" / f"case-manifest-v{version}-representative.json"
            ).read_text(encoding="utf-8")
        ),
    )
    document["case_id"] = str(CASE_ID)
    for index, item in enumerate(cast(list[dict[str, object]], document["sources"])):
        # Only immutable-storage identity is exercised here. No parser consumes
        # these declared synthetic source copies.
        payload = f"PWS0804 synthetic immutable source {index}\n".encode("ascii")
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        item["byte_size"] = len(payload)
        (layout.originals / cast(str, item["storage_name"])).write_bytes(payload)
    payload = canonical_manifest_bytes(document)
    layout.manifest.write_bytes(payload)
    return manager, layout, document, payload


def file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
