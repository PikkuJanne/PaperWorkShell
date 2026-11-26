"""Build a deterministic update ZIP from an already resolved Windows stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import MAX_PACKAGE_BYTES
from paperworkshell.storage.update_payload import (
    INSTALLED_MARKER_NAME,
    MANIFEST_NAME,
    MAX_INSTALLED_MARKER_BYTES,
    UpdatePayloadError,
    parse_payload_manifest,
    verify_payload_marker,
)
from tools.release_artifacts import ReleaseArtifactError, file_hashes, plain_path
from tools.release_evidence import MAX_INVENTORY_BYTES, ReleaseEvidenceError, validate_data
from tools.release_staging import canonical_json, verify_files

_CHUNK_BYTES: Final = 1024 * 1024


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.compress_level = 6
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_update_payload(
    inventory_path: Path, stage: Path, output: Path, *, channel: str, case_schema_version: int
) -> dict[str, object]:
    """Bind every staged runtime, notice, source and evidence file to its owner."""
    plain_path(inventory_path)
    with inventory_path.open("rb") as stream:
        inventory_bytes = stream.read(MAX_INVENTORY_BYTES + 1)
    if not 0 < len(inventory_bytes) <= MAX_INVENTORY_BYTES:
        raise ReleaseEvidenceError("UPDATE_INVENTORY_SIZE")
    inventory = json.loads(inventory_bytes)
    if not isinstance(inventory, dict) or canonical_json(inventory) != inventory_bytes:
        raise ReleaseEvidenceError("UPDATE_INVENTORY_ENCODING")
    validate_data(inventory)
    verify_files(stage, inventory["files"])

    inventory_sha256 = hashlib.sha256(inventory_bytes).hexdigest()
    file_fields = ("path", "size_bytes", "sha256", "component_id")
    component_fields = ("component_id", "version")
    manifest_bytes = canonical_json(
        {
            "schema_version": 1,
            "application_id": INSTALLED_APPLICATION_ID,
            "channel": channel,
            "version": inventory["candidate"]["version"],
            "candidate_id": inventory["candidate"]["id"],
            "case_schema_version": case_schema_version,
            "inventory_sha256": inventory_sha256,
            "components": [
                {key: item[key] for key in component_fields}
                for item in sorted(inventory["components"], key=lambda item: item["component_id"])
            ],
            "files": [
                {key: item[key] for key in file_fields}
                for item in sorted(inventory["files"], key=lambda item: item["path"])
            ],
        }
    )
    manifest = parse_payload_manifest(manifest_bytes)
    with (stage / "runtime" / INSTALLED_MARKER_NAME).open("rb") as stream:
        verify_payload_marker(stream.read(MAX_INSTALLED_MARKER_BYTES + 1), manifest)

    plain_path(output)
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            with zipfile.ZipFile(stream, "w", allowZip64=False) as archive:
                archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
                for item in manifest.files:
                    digest = hashlib.sha256()
                    remaining = item.size_bytes
                    with (
                        (stage / item.path).open("rb") as source,
                        archive.open(_zip_info(item.path), "w") as target,
                    ):
                        while chunk := source.read(min(_CHUNK_BYTES, remaining + 1)):
                            if len(chunk) > remaining:
                                raise ReleaseArtifactError("UPDATE_STAGE_CHANGED")
                            remaining -= len(chunk)
                            digest.update(chunk)
                            target.write(chunk)
                    if remaining or digest.hexdigest() != item.sha256:
                        raise ReleaseArtifactError("UPDATE_STAGE_CHANGED")
        package_size, package_sha256, _ = file_hashes(output, MAX_PACKAGE_BYTES)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {
        "schema_version": 1,
        "application_id": INSTALLED_APPLICATION_ID,
        "platform": "win-x64",
        "channel": manifest.channel,
        "version": manifest.version,
        "candidate_id": manifest.candidate_id,
        "case_schema_version": manifest.case_schema_version,
        "file_count": len(manifest.files),
        "package_size": package_size,
        "package_sha256": package_sha256,
        "inventory_sha256": inventory_sha256,
        "manifest_sha256": manifest.manifest_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--case-schema-version", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_update_payload(
            cast(Path, args.inventory),
            cast(Path, args.stage),
            cast(Path, args.output),
            channel=cast(str, args.channel),
            case_schema_version=cast(int, args.case_schema_version),
        )
    except UpdatePayloadError as error:
        print(error.code, file=sys.stderr)
        return 1
    except (ReleaseEvidenceError, ReleaseArtifactError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError, ValueError, zipfile.LargeZipFile:
        print("UPDATE_PAYLOAD_BUILD_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
