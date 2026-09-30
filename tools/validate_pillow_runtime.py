"""Offline admission check for the reviewed Pillow Windows wheel and native members.

This developer check never imports Pillow or loads optional native extensions. It
does not claim that the development closure is a final packaged release inventory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import Distribution, distribution
from pathlib import Path
from typing import Any, Final, cast

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST: Final = Path("packaging/pillow-runtime-closure-v1.json")
MANIFEST_SHA256: Final = "46e57b7f8b08f146257406d267ec75a8c553d15d0b8f521967fbdaba85fa0d8d"
MAX_MANIFEST_BYTES: Final = 32_768
MAX_SUPPORT_BYTES: Final = 2_000_000
VERSION: Final = "12.3.0"


class PillowEvidenceError(ValueError):
    """One closed developer diagnostic; never includes paths or native messages."""


@dataclass(frozen=True)
class FileEvidence:
    path: str
    size_bytes: int
    sha256: str


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise PillowEvidenceError("PILLOW_EVIDENCE_LIMIT")
    return value


def validate_installed_files(expected: tuple[FileEvidence, ...], installed: Distribution) -> None:
    """Bind each reviewed native/notice member to RECORD and actual bytes."""
    if installed.version != VERSION:
        raise PillowEvidenceError("PILLOW_EVIDENCE_VERSION")
    paths = installed.files
    if paths is None or len(paths) > 512:
        raise PillowEvidenceError("PILLOW_EVIDENCE_RECORD")
    observed = {
        str(path): path
        for path in paths
        if str(path).lower().endswith((".pyd", ".dll")) or str(path).endswith("/licenses/LICENSE")
    }
    wanted = {entry.path for entry in expected}
    if len(wanted) != len(expected) or set(observed) != wanted:
        raise PillowEvidenceError("PILLOW_EVIDENCE_MEMBERS")
    if sum(str(path) in wanted for path in paths) != len(expected):
        raise PillowEvidenceError("PILLOW_EVIDENCE_RECORD")
    for entry in expected:
        record = observed[entry.path]
        digest = record.hash
        encoded = base64.urlsafe_b64encode(bytes.fromhex(entry.sha256)).rstrip(b"=").decode()
        if (
            record.size != entry.size_bytes
            or digest is None
            or digest.mode != "sha256"
            or digest.value != encoded
        ):
            raise PillowEvidenceError("PILLOW_EVIDENCE_RECORD")
        actual = Path(str(installed.locate_file(record)))
        if actual.is_symlink():
            raise PillowEvidenceError("PILLOW_EVIDENCE_MEMBER")
        data = _read_bounded(actual, entry.size_bytes)
        if len(data) != entry.size_bytes or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise PillowEvidenceError("PILLOW_EVIDENCE_MEMBER")


def validate_repository(root: Path, *, inspect_install: bool = True) -> None:
    """Validate the exact reviewed record, lock binding and installed Windows wheel."""
    data = _read_bounded(root / MANIFEST, MAX_MANIFEST_BYTES)
    # Pin the complete reviewed record, including component versions, licences,
    # evidence qualifications and release exclusions. Any change needs review.
    if hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise PillowEvidenceError("PILLOW_EVIDENCE_MANIFEST")
    payload = cast(dict[str, Any], json.loads(data))
    artifact = payload["artifact"]
    inventory = cast(
        dict[str, Any],
        json.loads(
            _read_bounded(root / "packaging/dependency-inventory-v1.json", MAX_SUPPORT_BYTES)
        ),
    )
    components = [item for item in inventory["components"] if item["component_id"] == "pillow"]
    if len(components) != 1:
        raise PillowEvidenceError("PILLOW_EVIDENCE_INVENTORY")
    component = components[0]
    expected_artifact = {
        "artifact": artifact["name"],
        "url": artifact["url"],
        "target_platform": "windows-x64",
        "size_bytes": artifact["size_bytes"],
        "sha256": artifact["sha256"],
    }
    if (
        component["artifact_evidence"] != expected_artifact
        or component["version"]["selected"] != VERSION
        or component["active_scopes"] != ["runtime", "worker"]
        or component["security"]["boundary"] != "worker-process"
    ):
        raise PillowEvidenceError("PILLOW_EVIDENCE_INVENTORY")
    lock = tomllib.loads(_read_bounded(root / "uv.lock", MAX_SUPPORT_BYTES).decode("utf-8"))
    packages = [package for package in lock["package"] if package["name"] == "pillow"]
    if len(packages) != 1 or packages[0]["version"] != VERSION:
        raise PillowEvidenceError("PILLOW_EVIDENCE_LOCK")
    package = packages[0]
    wheels = [wheel for wheel in package["wheels"] if wheel["url"] == artifact["url"]]
    if (
        len(wheels) != 1
        or wheels[0]["size"] != artifact["size_bytes"]
        or wheels[0]["hash"] != "sha256:" + artifact["sha256"]
        or package["sdist"]["hash"] != "sha256:" + payload["source_archive"]["sha256"]
    ):
        raise PillowEvidenceError("PILLOW_EVIDENCE_LOCK")
    if inspect_install:
        if os.name != "nt":
            raise PillowEvidenceError("PILLOW_EVIDENCE_WINDOWS_REQUIRED")
        expected = tuple(
            FileEvidence(item["path"], item["size_bytes"], item["sha256"])
            for item in payload["files"]
        )
        validate_installed_files(expected, distribution("pillow"))


def main() -> int:
    try:
        validate_repository(PROJECT_ROOT)
    except PillowEvidenceError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("PILLOW_EVIDENCE_UNAVAILABLE", file=sys.stderr)
        return 1
    print(
        "Pillow runtime evidence valid: locked wheel, 8 native members, licence and component record."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
