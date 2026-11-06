"""Derive the lxml source bundle with only the two approved resources omitted.

The source copies are bound to the exact approved wheel resources, including
the documented LF/CRLF representation difference. No source is executed and
no redistribution right is granted. The original archives remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
APPROVAL: Final = PROJECT_ROOT / "packaging/release-reviews/lxml-exclusions-v1.json"
APPROVAL_SHA256: Final = "cc20dddf92d55f9062ef4d47d96e481904ad4c9d38b49a1c5d6178f0af66ec15"
SOURCE_SHA256: Final = "ba96ae44888e0185281e937633a743ea90d5a196c6000f82565ebb0580012d40"
SOURCE_SIZE: Final = 4_197_430
WHEEL_SHA256: Final = "b2d444f2e66624d68e9c6b211e28a76e22fff5fcabcfff4deac18b529b7d4137"
WHEEL_SIZE: Final = 4_064_497
MAX_MEMBERS: Final = 1024
MAX_MEMBER_BYTES: Final = 16 * 1024 * 1024
MAX_EXPANDED_BYTES: Final = 64 * 1024 * 1024
MAX_OUTPUT_BYTES: Final = 8 * 1024 * 1024
SOURCE_PREFIX: Final = "lxml-6.1.1/src/"
EXCLUSIONS: Final[dict[str, tuple[int, str, int, str]]] = {
    "lxml/isoschematron/resources/xsl/RNG2Schtrn.xsl": (
        3247,
        "832f84eb1476a77e522bdc1715386b68f24423d35da084cd1dde319aacbba316",
        3172,
        "39b79bb01f16b7e777b8803f539354f394e99d0dcfc4f5f7f1374e02aa2c31a7",
    ),
    "lxml/isoschematron/resources/xsl/XSD2Schtrn.xsl": (
        4563,
        "430791ac820cfb315c820f7c19703609a59f21b8151345ca11e6127efbfaec0d",
        4563,
        "430791ac820cfb315c820f7c19703609a59f21b8151345ca11e6127efbfaec0d",
    ),
}


class LxmlSourceError(ValueError):
    """A closed diagnostic without archive text, filenames or system paths."""


def _read_pinned(path: Path, size: int, expected: str) -> bytes:
    for item in (*path.absolute().parents, path.absolute()):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise LxmlSourceError("LXML_SOURCE_UNSAFE_PATH")
    if not path.is_file():
        raise LxmlSourceError("LXML_SOURCE_INPUT")
    with path.open("rb") as source:
        data = source.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
        raise LxmlSourceError("LXML_SOURCE_INPUT_HASH")
    return data


def _name(value: str) -> str:
    name = value.rstrip("/")
    if (
        not name.startswith("lxml-6.1.1/")
        and name != "lxml-6.1.1"
        or len(name) > 512
        or "\\" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(ord(char) < 32 for char in name)
    ):
        raise LxmlSourceError("LXML_SOURCE_MEMBER")
    return name


def _source_members(
    source_bytes: bytes, *, executable_names: set[str] | None = None
) -> dict[str, bytes | None]:
    result: dict[str, bytes | None] = {}
    seen: set[str] = set()
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(source_bytes), mode="r|gz") as archive:
        for member in archive:
            name = _name(member.name)
            if name.casefold() in seen or not (member.isfile() or member.isdir()):
                raise LxmlSourceError("LXML_SOURCE_MEMBER")
            seen.add(name.casefold())
            expanded += member.size
            if (
                len(seen) > MAX_MEMBERS
                or not 0 <= member.size <= MAX_MEMBER_BYTES
                or expanded > MAX_EXPANDED_BYTES
            ):
                raise LxmlSourceError("LXML_SOURCE_LIMIT")
            if member.isdir():
                result[name] = None
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise LxmlSourceError("LXML_SOURCE_MEMBER")
            data = stream.read(member.size + 1)
            if len(data) != member.size:
                raise LxmlSourceError("LXML_SOURCE_MEMBER")
            result[name] = data
            if member.mode & 0o111 and executable_names is not None:
                executable_names.add(name)
    return result


def _pack(members: dict[str, bytes | None], executable_names: set[str]) -> bytes:
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:xz", format=tarfile.PAX_FORMAT, preset=6) as archive:
        for name, body in sorted(members.items()):
            member = tarfile.TarInfo(name)
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            if body is None:
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
            else:
                member.mode = 0o755 if name in executable_names else 0o644
                member.size = len(body)
            archive.addfile(member, None if body is None else io.BytesIO(body))
    data = target.getvalue()
    if len(data) > MAX_OUTPUT_BYTES:
        raise LxmlSourceError("LXML_SOURCE_LIMIT")
    return data


def derive(source: Path, wheel: Path, output: Path) -> dict[str, object]:
    with APPROVAL.open("rb") as approval_file:
        approval_data = approval_file.read(64 * 1024 + 1)
    if (
        len(approval_data) > 64 * 1024
        or hashlib.sha256(approval_data).hexdigest() != APPROVAL_SHA256
    ):
        raise LxmlSourceError("LXML_SOURCE_APPROVAL")
    approval = cast(dict[str, Any], json.loads(approval_data))
    expected = {
        path: {"path": path, "size_bytes": values[0], "sha256": values[1]}
        for path, values in EXCLUSIONS.items()
    }
    if (
        approval.get("schema_version") != 1
        or {row["path"]: row for row in approval["exclusions"]} != expected
        or len(approval["exclusions"]) != len(expected)
    ):
        raise LxmlSourceError("LXML_SOURCE_APPROVAL")
    source_bytes = _read_pinned(source, SOURCE_SIZE, SOURCE_SHA256)
    wheel_bytes = _read_pinned(wheel, WHEEL_SIZE, WHEEL_SHA256)
    executable_names: set[str] = set()
    members = _source_members(source_bytes, executable_names=executable_names)
    omitted = []
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as approved_wheel:
        for name, (wheel_size, wheel_hash, source_size, source_hash) in EXCLUSIONS.items():
            wheel_body = approved_wheel.read(name)
            source_body = members.pop(SOURCE_PREFIX + name, None)
            if (
                not isinstance(source_body, bytes)
                or len(source_body) != source_size
                or hashlib.sha256(source_body).hexdigest() != source_hash
                or len(wheel_body) != wheel_size
                or hashlib.sha256(wheel_body).hexdigest() != wheel_hash
                or source_body.replace(b"\r\n", b"\n") != wheel_body.replace(b"\r\n", b"\n")
            ):
                raise LxmlSourceError("LXML_SOURCE_EXCLUSION")
            omitted.append(
                {
                    "source_path": SOURCE_PREFIX + name,
                    "source_size_bytes": source_size,
                    "source_sha256": source_hash,
                    "approved_wheel_path": name,
                    "approved_wheel_size_bytes": wheel_size,
                    "approved_wheel_sha256": wheel_hash,
                    "raw_bytes_equal": source_body == wheel_body,
                    "lf_normalized_bytes_equal": True,
                }
            )
    packed = _pack(members, executable_names)
    output.parent.mkdir(parents=True, exist_ok=True)
    for path in (*output.absolute().parents, output.absolute()):
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise LxmlSourceError("LXML_SOURCE_UNSAFE_PATH")
    created = False
    try:
        with output.open("xb") as target:
            created = True
            target.write(packed)
            target.flush()
            os.fsync(target.fileno())
    except OSError:
        if created:
            output.unlink(missing_ok=True)
        raise LxmlSourceError("LXML_SOURCE_WRITE") from None
    return {
        "schema_version": 1,
        "task_id": "PWS-0801",
        "status": "derived-source-subset-verified",
        "input_source_sha256": SOURCE_SHA256,
        "input_wheel_sha256": WHEEL_SHA256,
        "approval_sha256": APPROVAL_SHA256,
        "output_name": output.name,
        "output_size_bytes": len(packed),
        "output_sha256": hashlib.sha256(packed).hexdigest(),
        "exclusions": omitted,
        "all_remaining_source_file_bytes_preserved": True,
        "archive_metadata": "Sorted paths; zero uid/gid/mtime; empty owner names; executable intent preserved with modes0644/0755; XZ preset6.",
        "included_files": [
            {"path": name, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            for name, body in sorted(members.items())
            if body is not None
        ],
        "redistribution_rights_granted": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        receipt = derive(arguments.source, arguments.wheel, arguments.output)
    except LxmlSourceError as error:
        print(json.dumps({"status": "rejected", "code": str(error)}, sort_keys=True))
        return 1
    except OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError, zipfile.BadZipFile:
        print(json.dumps({"status": "rejected", "code": "LXML_SOURCE_INPUT"}, sort_keys=True))
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
