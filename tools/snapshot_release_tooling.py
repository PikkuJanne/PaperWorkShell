"""Snapshot the local release recipes and reviewed inputs, without circular outputs."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

from tools.release_artifacts import ReleaseArtifactError, plain_path, safe_relative

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024


def snapshot(destination: Path, *, root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Create a new deterministic source archive from a closed repository selection."""
    plain_path(destination)
    output = (root / "output").resolve()
    resolved = destination.absolute().resolve()
    if resolved == output or not resolved.is_relative_to(output) or destination.exists():
        raise ReleaseArtifactError("RELEASE_SNAPSHOT_DESTINATION")
    paths = {root / name for name in (".gitattributes", "pyproject.toml", "uv.lock")}
    for directory, pattern in (
        ("tools", "*.py"),
        ("schemas", "*.json"),
        ("packaging", "*"),
        ("tests/fixtures/release-inventory", "*.json"),
        ("tests/fixtures/release-sbom", "*"),
    ):
        for path in (root / directory).rglob(pattern):
            plain_path(path)
            if path.is_file():
                paths.add(path)
                if len(paths) > MAX_FILES:
                    raise ReleaseArtifactError("RELEASE_SNAPSHOT_LIMIT")
    paths.discard(root / "packaging/release-reviews/app-inputs-v1.json")
    paths.discard(root / "packaging/README.md")
    payloads: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        plain_path(path)
        name = safe_relative(path.relative_to(root).as_posix())
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES - total + 1)
        total += len(raw)
        if total > MAX_BYTES:
            raise ReleaseArtifactError("RELEASE_SNAPSHOT_LIMIT")
        payloads.append((name, raw))
    manifest = {
        "schema_version": 1,
        "scope": "Release tooling and reviewed inputs; runtime application source is a separate sdist. Candidate review, final reports, task state and bundle manifest are excluded to avoid circular identities.",
        "files": [
            {"path": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in payloads
        ],
    }
    payloads.append(("SOURCE-MANIFEST.json", (json.dumps(manifest, indent=2) + "\n").encode()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as output_stream:
            created = True
            with tarfile.open(
                fileobj=output_stream, mode="w:xz", format=tarfile.PAX_FORMAT
            ) as archive:
                for name, raw in payloads:
                    info = tarfile.TarInfo("release-tooling/" + name)
                    info.size, info.mtime, info.mode = len(raw), 0, 0o644
                    archive.addfile(info, io.BytesIO(raw))
        with tarfile.open(destination, mode="r:xz") as archive:
            if archive.getnames() != ["release-tooling/" + name for name, _raw in payloads]:
                raise ReleaseArtifactError("RELEASE_SNAPSHOT_VERIFY")
            for member, (_name, expected) in zip(archive.getmembers(), payloads, strict=True):
                extracted = archive.extractfile(member)
                if extracted is None or extracted.read(len(expected) + 1) != expected:
                    raise ReleaseArtifactError("RELEASE_SNAPSHOT_VERIFY")
        raw_output = destination.read_bytes()
        return {
            "files": len(payloads),
            "size_bytes": len(raw_output),
            "sha256": hashlib.sha256(raw_output).hexdigest(),
        }
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(snapshot(args.output), sort_keys=True))
        return 0
    except Exception:
        print("RELEASE_SNAPSHOT_INVALID", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
