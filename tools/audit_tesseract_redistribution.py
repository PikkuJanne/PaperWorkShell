"""Inspect the exact development OCR archives without extracting or executing them.

This is evidence of a redistribution blocker, not an SBOM or shipment approval.
Only reviewed public artifact identifiers can enter the report. No network access,
installed-environment discovery, document access, or output-file mutation occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import struct
import sys
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn, cast

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
CLOSURE_PATH: Final = Path("packaging/tesseract-runtime-closure-v1.json")
CLOSURE_SHA256: Final = "1ffb85724770b4c30aa4d982564d548dadf466ebd5861cd827d2836198119c0a"
MAX_CLOSURE_BYTES: Final = 64 * 1024
MAX_ARCHIVE_BYTES: Final = 8 * 1024 * 1024
MAX_MEMBER_BYTES: Final = 32 * 1024 * 1024
MAX_EXPANDED_BYTES: Final = 64 * 1024 * 1024
MAX_MEMBERS: Final = 1024
MAX_METADATA_BYTES: Final = 64 * 1024
PREFIX: Final = "mingw-w64-ucrt-x86_64-"
JBIG_PACKAGE: Final = PREFIX + "jbigkit"
JBIG_METADATA_SHA256: Final = "82789498b6237b5fdedb5eaa854d0bb139f4688b4252087dc607b46512932634"
JBIG_LICENSE_PATH: Final = "ucrt64/share/licenses/jbigkit/COPYING"
JBIG_LICENSE_SHA256: Final = "91df39d1816bfb17a4dda2d3d2c83b1f6f2d38d53e53e41e8f97ad5ac46a0cad"
JBIG_LICENSE_BYTES: Final = 17_982
BLOCKER_CODE: Final = "RELEASE_DISTRIBUTION_DECISION_REQUIRED"
REVIEWED_LICENSES: Final[dict[str, tuple[str, ...]]] = {
    "brotli": ("spdx:MIT",),
    "bzip2": ("custom",),
    "curl-winssl": ("spdx:MIT",),
    "expat": ("spdx:MIT",),
    "gcc-libs": ("spdx:GPL-3.0-or-later WITH GCC-exception-3.1 AND LGPL-2.1-or-later",),
    "gettext-runtime": ("spdx:GPL-3.0-or-later AND LGPL-2.1-or-later",),
    "giflib": ("spdx:MIT",),
    "jbigkit": ("spdx:GPL-2.0",),
    "leptonica": ("spdx:BSD-2-Clause",),
    "lerc": ("spdx:Apache-2.0",),
    "libarchive": ("spdx:BSD-2-Clause",),
    "libb2": ("custom:CC0",),
    "libdeflate": ("spdx:MIT",),
    "libiconv": ("spdx:LGPL-2.1-or-later", "documentation:spdx:GPL-3.0-or-later"),
    "libidn2": ("spdx:GPL-2.0-or-later", "spdx:LGPL-3.0-or-later"),
    "libjpeg-turbo": ("custom:BSD-like",),
    "libpng": ("custom",),
    "libpsl": ("spdx:MIT",),
    "libssh2-wincng": ("spdx:BSD-3-Clause",),
    "libtiff": ("spdx:MIT",),
    "libunistring": ("spdx:LGPL-3.0-or-later OR GPL-3.0-or-later",),
    "libwebp": ("spdx:BSD-3-Clause",),
    "libwinpthread": ("spdx:MIT AND BSD-3-Clause-Clear",),
    "lz4": ("BSD", "GPL2"),
    "openjpeg2": ("spdx:BSD-2-Clause",),
    "tesseract-data-eng": ("spdx:Apache-2.0",),
    "tesseract-ocr": ("spdx:Apache-2.0",),
    "xz": ("spdx:0BSD AND LGPL-2.1-or-later AND GPL-2.0-or-later",),
    "zlib-ng-compat": ("spdx:Zlib",),
    "zstd": ("spdx:BSD-3-Clause OR GPL-2.0-or-later",),
}
PE_EVIDENCE_PATHS: Final = frozenset(
    {
        "bin/tesseract.exe",
        "bin/libtesseract-5.5.dll",
        "bin/libleptonica-6.dll",
        "bin/libtiff-6.dll",
        "bin/libjbig-0.dll",
    }
)
REQUIRED_IMPORT_EDGES: Final = frozenset(
    {
        ("bin/tesseract.exe", "bin/libtiff-6.dll"),
        ("bin/tesseract.exe", "bin/libtesseract-5.5.dll"),
        ("bin/libtesseract-5.5.dll", "bin/libleptonica-6.dll"),
        ("bin/libleptonica-6.dll", "bin/libtiff-6.dll"),
        ("bin/libtiff-6.dll", "bin/libjbig-0.dll"),
    }
)


class RedistributionAuditError(ValueError):
    """A closed diagnostic; never includes filesystem paths or archive contents."""


@dataclass(frozen=True)
class SelectedFile:
    path: str
    package_id: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArchiveSpec:
    package_id: str
    version: str
    artifact: str
    size_bytes: int
    sha256: str
    license_labels: tuple[str, ...]
    files: tuple[SelectedFile, ...]


def _fail(code: str) -> NoReturn:
    raise RedistributionAuditError(code)


def _plain_path(path: Path) -> None:
    """Reject redirection at every existing component, including Windows junctions."""
    for candidate in (*path.absolute().parents, path.absolute()):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            _fail("OCR_AUDIT_UNSAFE_PATH")


def _read_file(path: Path, limit: int) -> bytes:
    _plain_path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        _fail("OCR_AUDIT_UNSAFE_PATH")
    with path.open("rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            _fail("OCR_AUDIT_UNSAFE_PATH")
        data = source.read(limit + 1)
    if len(data) > limit:
        _fail("OCR_AUDIT_LIMIT")
    return data


def load_specs(root: Path = PROJECT_ROOT) -> tuple[ArchiveSpec, ...]:
    """Use the frozen repository closure, never a caller-provided substitute."""
    raw = _read_file(root / CLOSURE_PATH, MAX_CLOSURE_BYTES)
    if hashlib.sha256(raw).hexdigest() != CLOSURE_SHA256:
        _fail("OCR_AUDIT_CLOSURE")
    payload = cast(dict[str, Any], json.loads(raw))
    selected = tuple(
        SelectedFile(**{key: item[key] for key in SelectedFile.__annotations__})
        for item in payload["files"]
    )
    specs = tuple(
        ArchiveSpec(
            package_id=item["package_id"],
            version=item["version"],
            artifact=item["artifact"],
            size_bytes=item["size_bytes"],
            sha256=item["sha256"],
            license_labels=REVIEWED_LICENSES[item["package_id"].removeprefix(PREFIX)],
            files=tuple(file for file in selected if file.package_id == item["package_id"]),
        )
        for item in payload["packages"]
    )
    if len(specs) != 30 or len(selected) != 39:
        _fail("OCR_AUDIT_CLOSURE")
    return specs


def _safe_member_name(value: str) -> str:
    normalized = value.rstrip("/")
    if (
        not normalized
        or len(normalized) > 512
        or "\\" in normalized
        or ":" in normalized
        or any(ord(char) < 32 or ord(char) > 126 for char in normalized)
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or PurePosixPath(normalized).as_posix() != normalized
    ):
        _fail("OCR_AUDIT_UNSAFE_MEMBER")
    return normalized


def _metadata(data: bytes, spec: ArchiveSpec) -> None:
    if len(data) > MAX_METADATA_BYTES:
        _fail("OCR_AUDIT_LIMIT")
    lines = data.decode("utf-8").splitlines()
    values: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("#") or not line:
            continue
        key, separator, value = line.partition(" = ")
        if not separator:
            _fail("OCR_AUDIT_METADATA")
        values.setdefault(key, []).append(value)
    if (
        values.get("pkgname") != [spec.package_id]
        or values.get("pkgver") != [spec.version]
        or tuple(values.get("license", [])) != spec.license_labels
    ):
        _fail("OCR_AUDIT_METADATA")
    if spec.package_id == JBIG_PACKAGE and hashlib.sha256(data).hexdigest() != JBIG_METADATA_SHA256:
        _fail("OCR_AUDIT_JBIG_METADATA")


def inspect_archive(path: Path, spec: ArchiveSpec) -> dict[str, bytes]:
    """Hash first; stream bounded members and return only the five reviewed PE files."""
    if not 0 < spec.size_bytes <= MAX_ARCHIVE_BYTES:
        _fail("OCR_AUDIT_LIMIT")
    data = _read_file(path, spec.size_bytes)
    if len(data) != spec.size_bytes or hashlib.sha256(data).hexdigest() != spec.sha256:
        _fail("OCR_AUDIT_ARCHIVE_HASH")
    wanted = {"ucrt64/" + item.path: item for item in spec.files}
    seen: set[str] = set()
    matched: set[str] = set()
    retained: dict[str, bytes] = {}
    expanded = 0
    metadata_seen = False
    license_seen = False
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|zst") as archive:
        for member in archive:
            name = _safe_member_name(member.name)
            if name.casefold() in seen:
                _fail("OCR_AUDIT_DUPLICATE_MEMBER")
            seen.add(name.casefold())
            expanded += member.size
            if (
                len(seen) > MAX_MEMBERS
                or not 0 <= member.size <= MAX_MEMBER_BYTES
                or expanded > MAX_EXPANDED_BYTES
            ):
                _fail("OCR_AUDIT_LIMIT")
            # The pinned libdeflate package has this one unselected utility hard
            # link. Inspect its closed tuple only; never extract or follow it.
            # Every selected member and every metadata/license member is regular.
            if (
                spec.package_id == PREFIX + "libdeflate"
                and name == "ucrt64/bin/libdeflate-gzip.exe"
                and member.islnk()
                and member.linkname == "ucrt64/bin/libdeflate-gunzip.exe"
                and member.size == 0
                and name not in wanted
            ):
                continue
            if not (member.isfile() or member.isdir()):
                _fail("OCR_AUDIT_UNSAFE_MEMBER")
            if member.isdir():
                continue
            required = (
                name in wanted
                or name == ".PKGINFO"
                or (spec.package_id == JBIG_PACKAGE and name == JBIG_LICENSE_PATH)
            )
            if not required:
                continue
            if name == ".PKGINFO" and member.size > MAX_METADATA_BYTES:
                _fail("OCR_AUDIT_LIMIT")
            source = archive.extractfile(member)
            if source is None:
                _fail("OCR_AUDIT_MEMBER")
            content = source.read(member.size + 1)
            if len(content) != member.size:
                _fail("OCR_AUDIT_MEMBER")
            if name == ".PKGINFO":
                _metadata(content, spec)
                metadata_seen = True
            elif name == JBIG_LICENSE_PATH:
                if (
                    len(content) != JBIG_LICENSE_BYTES
                    or hashlib.sha256(content).hexdigest() != JBIG_LICENSE_SHA256
                ):
                    _fail("OCR_AUDIT_JBIG_LICENSE")
                license_seen = True
            else:
                record = wanted[name]
                if (
                    len(content) != record.size_bytes
                    or hashlib.sha256(content).hexdigest() != record.sha256
                ):
                    _fail("OCR_AUDIT_MEMBER_HASH")
                matched.add(name)
                if record.path in PE_EVIDENCE_PATHS:
                    retained[record.path] = content
    if (
        matched != set(wanted)
        or not metadata_seen
        or (spec.package_id == JBIG_PACKAGE and not license_seen)
    ):
        _fail("OCR_AUDIT_MISSING_MEMBER")
    return retained


def pe_imports(data: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read PE32+ import names without loading code; fail closed on unsupported data."""

    def integer(offset: int, width: int = 4) -> int:
        if offset < 0 or offset + width > len(data):
            _fail("OCR_AUDIT_PE")
        return int.from_bytes(data[offset : offset + width], "little")

    if len(data) < 64 or data[:2] != b"MZ":
        _fail("OCR_AUDIT_PE")
    pe = integer(60)
    if data[pe : pe + 4] != b"PE\0\0" or integer(pe + 4, 2) != 0x8664:
        _fail("OCR_AUDIT_PE")
    count = integer(pe + 6, 2)
    optional_size = integer(pe + 20, 2)
    optional = pe + 24
    if (
        not 0 < count <= 96
        or optional_size < 240
        or integer(optional, 2) != 0x20B
        or integer(optional + 108) < 14
    ):
        _fail("OCR_AUDIT_PE")
    image_base = integer(optional + 24, 8)
    sections: list[tuple[int, int, int, int]] = []
    for index in range(count):
        offset = optional + optional_size + index * 40
        virtual_size, virtual, raw_size, raw = struct.unpack_from("<IIII", data, offset + 8)
        if raw + raw_size > len(data):
            _fail("OCR_AUDIT_PE")
        sections.append((virtual, max(virtual_size, raw_size), raw, raw_size))

    def rva_offset(rva: int, size: int) -> int:
        matches = [
            raw + rva - virtual
            for virtual, length, raw, raw_size in sections
            if virtual <= rva and rva + size <= virtual + min(length, raw_size)
        ]
        if len(matches) != 1:
            _fail("OCR_AUDIT_PE")
        return matches[0]

    def import_name(rva: int) -> str:
        start = rva_offset(rva, 1)
        end = data.find(b"\0", start, min(start + 257, len(data)))
        if end < 0:
            _fail("OCR_AUDIT_PE")
        rva_offset(rva, end - start + 1)
        text = data[start:end].decode("ascii")
        if not text or any(not (char.isalnum() or char in "-_.+") for char in text):
            _fail("OCR_AUDIT_PE")
        return text.lower()

    results: list[tuple[str, ...]] = []
    for directory, descriptor_size in ((1, 20), (13, 32)):
        entry = optional + 112 + directory * 8
        rva, size = integer(entry), integer(entry + 4)
        if not rva and not size:
            results.append(())
            continue
        if not rva or not descriptor_size <= size <= 256 * 1024:
            _fail("OCR_AUDIT_PE")
        start = rva_offset(rva, size)
        imports = []
        terminated = False
        for relative in range(
            0, min(size - descriptor_size + 1, descriptor_size * 257), descriptor_size
        ):
            offset = start + relative
            if not any(data[offset : offset + descriptor_size]):
                terminated = True
                break
            name_rva = integer(offset + (12 if directory == 1 else 4))
            if directory == 13 and integer(offset) != 1:
                if integer(offset) != 0:
                    _fail("OCR_AUDIT_PE")
                name_rva -= image_base
            imports.append(import_name(name_rva))
        if not terminated or len(set(imports)) != len(imports):
            _fail("OCR_AUDIT_PE")
        results.append(tuple(sorted(imports)))
    return results[0], results[1]


def audit(artifact_directory: Path, *, root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Return the deterministic confirmed-blocker report or raise a closed error."""
    _plain_path(artifact_directory)
    if not artifact_directory.is_dir():
        _fail("OCR_AUDIT_UNSAFE_PATH")
    specs = load_specs(root)
    retained: dict[str, bytes] = {}
    for spec in specs:
        retained.update(inspect_archive(artifact_directory / spec.artifact, spec))
    if set(retained) != PE_EVIDENCE_PATHS:
        _fail("OCR_AUDIT_PE")
    owned = {
        PurePosixPath(file.path).name.lower(): file.path for spec in specs for file in spec.files
    }
    edges: set[tuple[str, str]] = set()
    for path, content in sorted(retained.items()):
        normal, delay = pe_imports(content)
        if delay:
            _fail("OCR_AUDIT_PE")
        edges.update((path, owned[name]) for name in normal if name in owned)
    if not REQUIRED_IMPORT_EDGES <= edges:
        _fail("OCR_AUDIT_PE")
    return {
        "schema_version": 1,
        "task_id": "PWS-0801",
        "audit_id": "pws0801-tesseract-redistribution-blocker-v1",
        "status": "blocked",
        "code": BLOCKER_CODE,
        "release_approved": False,
        "closure_sha256": CLOSURE_SHA256,
        "archive_count": len(specs),
        "selected_file_count": sum(len(spec.files) for spec in specs),
        "archives": [
            {
                "artifact": spec.artifact,
                "package_id": spec.package_id,
                "version": spec.version,
                "size_bytes": spec.size_bytes,
                "sha256": spec.sha256,
                "package_license_labels": list(spec.license_labels),
            }
            for spec in specs
        ],
        "selected_files": [
            {
                "path": file.path,
                "package_id": file.package_id,
                "size_bytes": file.size_bytes,
                "sha256": file.sha256,
            }
            for spec in specs
            for file in spec.files
        ],
        "normal_import_edges": [{"from": start, "to": end} for start, end in sorted(edges)],
        "delay_imports_in_evidence_files": [],
        "jbig_metadata_sha256": JBIG_METADATA_SHA256,
        "jbig_license_sha256": JBIG_LICENSE_SHA256,
        "jbig_license_bytes": JBIG_LICENSE_BYTES,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = audit(arguments.artifacts)
    except RedistributionAuditError as error:
        report = {
            "schema_version": 1,
            "task_id": "PWS-0801",
            "status": "invalid",
            "code": str(error),
            "release_approved": False,
        }
    except OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError, struct.error:
        report = {
            "schema_version": 1,
            "task_id": "PWS-0801",
            "status": "invalid",
            "code": "OCR_AUDIT_INPUT",
            "release_approved": False,
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    sys.exit(main())
