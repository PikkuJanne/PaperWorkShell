"""Prepare a new app-local CRT overlay only after explicit reviewed rights inputs.

No download, licence acceptance, installation, executable loading, process launch,
System32 copying or existing-candidate mutation is implemented here. Receipts are
approved coordinator inputs, not a way for software to manufacture legal rights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Never

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
AGREEMENT_URL: Final = "https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Community-License-EN.docx"
AGREEMENT_SHA256: Final = "41a207b10c8ab91d0d2f10a854715f73dca54509581692d2fe179aa3ffcb8540"
REFERENCE_SHA256 = "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b"
REFERENCE_SIZE = 25_635_768
REFERENCE_URL: Final = "https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe"
PRODUCT_ID: Final = "Microsoft.VisualStudio.Product.Community"
COMPONENT_ID: Final = "Microsoft.VisualStudio.Component.VC.Redist.14.Latest"
VERSION: Final = "14.44.35211.0"
PIKEPDF_ALIAS: Final = (
    "runtime/Lib/site-packages/pikepdf.libs/msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll"
)
COMMUNITY_ALIAS_DISPOSITION: Final = "resourced-unmodified-from-authenticated-community-copy"
QPDF_APP_LOCAL_NAMES: Final = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
_BOUND_INPUTS: Final = (
    "inspection_root",
    "reference",
    "owner_acceptance",
    "community_copy_receipt",
    "community_copy_root",
    "recipient_terms_review",
    "recipient_terms_file",
)
MAX_JSON_BYTES: Final = 1024 * 1024
MAX_COPY_ARTIFACTS: Final = 512
MAX_COPY_BYTES: Final = 2 * 1024 * 1024 * 1024
_SHA: Final = re.compile(r"[0-9a-f]{64}\Z")
_UNSAFE_ATTRIBUTES: Final = 0x400 | 0x1000 | 0x40000 | 0x400000


@dataclass(frozen=True, slots=True)
class Member:
    name: str
    source: str
    size: int
    sha256: str


MEMBERS = (
    Member(
        "msvcp140.dll",
        "canonical-five-data/object-001.bin",
        557728,
        "0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9",
    ),
    Member(
        "msvcp140_1.dll",
        "canonical-five-data/object-002.bin",
        35952,
        "bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d",
    ),
    Member(
        "msvcp140_2.dll",
        "canonical-five-data/object-003.bin",
        280200,
        "3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9",
    ),
    Member(
        "vcruntime140.dll",
        "canonical-five-data/object-009.bin",
        124544,
        "d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066",
    ),
    Member(
        "vcruntime140_1.dll",
        "canonical-five-data/object-010.bin",
        49792,
        "1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f",
    ),
)
SOURCE_CONTAINERS = (
    Member(
        "minimum-x64-msi",
        "container-01-data/member-009.bin",
        212992,
        "fe5af8f2efc9bbdd2c23f22f2d3daf480a4047acf3b1edc6ecda81147c1ec14f",
    ),
    Member(
        "minimum-x64-cab",
        "container-01-data/member-012.bin",
        987836,
        "640aa6c516c72444523b8fbe034db46ff4e118ed02705340e3ccb62d426ff040",
    ),
)


class PreparationError(RuntimeError):
    """Only a stable code is printable; paths and raw exceptions are discarded."""


def fail(code: str) -> Never:
    error = PreparationError(code)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True)
class Inputs:
    inspection_root: Path
    reference: Path
    output: Path
    owner_acceptance: Path | None = None
    owner_acceptance_sha256: str | None = None
    community_copy_receipt: Path | None = None
    community_copy_receipt_sha256: str | None = None
    community_copy_root: Path | None = None
    recipient_terms_review: Path | None = None
    recipient_terms_review_sha256: str | None = None
    recipient_terms_file: Path | None = None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _plain(path: Path, *, directory: bool) -> os.stat_result:
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(WORKSPACE_ROOT):
        fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    if any(":" in part for part in path.parts[1:]):
        fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    info = path.lstat()
    for component in (*reversed(path.parents), path):
        current = component.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or getattr(current, "st_file_attributes", 0) & _UNSAFE_ATTRIBUTES
        ):
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
        if component != path and not stat.S_ISDIR(current.st_mode):
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    return info


def _read(path: Path, *, maximum: int) -> bytes:
    before = _plain(path, directory=False)
    if not 0 < before.st_size <= maximum:
        fail("MICROSOFT_RUNTIME_INPUT_LIMIT")
    with path.open("rb") as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            fail("MICROSOFT_RUNTIME_INPUT_CHANGED")
        payload = stream.read(maximum + 1)
        after_open = os.fstat(stream.fileno())
    after = _plain(path, directory=False)
    if (
        len(payload) != before.st_size
        or _identity(after) != _identity(before)
        or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
        or (after_open.st_size, after_open.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
    ):
        fail("MICROSOFT_RUNTIME_INPUT_CHANGED")
    return payload


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
        result[key] = value
    return result


def _json(path: Path, expected: str | None) -> dict[str, Any]:
    if type(expected) is not str or _SHA.fullmatch(expected) is None:
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    payload = _read(path, maximum=MAX_JSON_BYTES)
    if _digest(payload) != expected:
        fail("MICROSOFT_RUNTIME_RECEIPT_CHANGED")
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
    if type(value) is not dict:
        fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
    return value


def _keys(value: dict[str, Any], keys: str, *, version: int = 1) -> None:
    if (
        set(value) != set(keys.split())
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != version
    ):
        fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")


def _relative(value: object) -> str:
    if type(value) is not str or len(value) > 240 or "\\" in value:
        fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.as_posix() != value:
        fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    for part in path.parts:
        if (
            part in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_+.,=~ -]+", part)
            or part.endswith((" ", "."))
        ):
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
        if part.split(".")[0].casefold() in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(10)),
            *(f"lpt{i}" for i in range(10)),
        }:
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
    return value


def _rights(inputs: Inputs) -> dict[str, str]:
    if any(
        value is None
        for value in (
            inputs.owner_acceptance,
            inputs.community_copy_receipt,
            inputs.community_copy_root,
            inputs.recipient_terms_review,
            inputs.recipient_terms_file,
        )
    ):
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    assert inputs.owner_acceptance is not None and inputs.community_copy_receipt is not None
    assert inputs.community_copy_root is not None and inputs.recipient_terms_review is not None
    assert inputs.recipient_terms_file is not None
    owner = _json(inputs.owner_acceptance, inputs.owner_acceptance_sha256)
    _keys(
        owner,
        "schema_version kind owner accepted_as agreement_url agreement_sha256 accepted agent_acquisition_authorized evidence_reference",
    )
    if (
        owner["kind"] != "owner-acceptance"
        or owner["owner"] != "Janne Vuorela"
        or owner["accepted_as"] != "individual-developer"
        or owner["agreement_url"] != AGREEMENT_URL
        or owner["agreement_sha256"] != AGREEMENT_SHA256
        or owner["accepted"] is not True
        or owner["agent_acquisition_authorized"] is not True
        or type(owner["evidence_reference"]) is not str
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner["evidence_reference"]) is None
    ):
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    acquisition = _json(inputs.community_copy_receipt, inputs.community_copy_receipt_sha256)
    _keys(
        acquisition,
        "schema_version kind status product_id product_version channel component_id agreement_sha256 owner_acceptance_sha256 licensed_copy_review artifacts",
    )
    if (
        acquisition["kind"] != "community-product-copy"
        or acquisition["status"] != "complete"
        or acquisition["product_id"] != PRODUCT_ID
        or acquisition["channel"] != "VisualStudio.17.Release"
        or type(acquisition["product_version"]) is not str
        or re.fullmatch(r"17\.14\.[0-9]+(?:\.[0-9]+)?", acquisition["product_version"]) is None
        or acquisition["component_id"] != COMPONENT_ID
        or acquisition["agreement_sha256"] != AGREEMENT_SHA256
        or acquisition["owner_acceptance_sha256"] != inputs.owner_acceptance_sha256
        or acquisition["licensed_copy_review"] != "approved-valid-community-copy"
    ):
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    artifacts = acquisition["artifacts"]
    roles = {
        "bootstrapper",
        "product-catalog",
        "layout-manifest",
        "product-package",
        "redistributable-package",
    }
    if type(artifacts) is not list or not 5 <= len(artifacts) <= MAX_COPY_ARTIFACTS:
        fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
    total = 0
    names: set[str] = set()
    found: set[str] = set()
    _plain(inputs.community_copy_root, directory=True)
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {"path", "role", "size_bytes", "sha256"}:
            fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
        name = _relative(artifact["path"])
        size = artifact["size_bytes"]
        if (
            name.casefold() in names
            or artifact["role"] not in roles
            or type(size) is not int
            or not 0 < size <= MAX_COPY_BYTES
        ):
            fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
        total += size
        if (
            total > MAX_COPY_BYTES
            or type(artifact["sha256"]) is not str
            or _SHA.fullmatch(artifact["sha256"]) is None
        ):
            fail("MICROSOFT_RUNTIME_INPUT_LIMIT")
        # Stream large legitimate product-copy artifacts instead of materializing them.
        path = inputs.community_copy_root / name
        before = _plain(path, directory=False)
        if before.st_size != size:
            fail("MICROSOFT_RUNTIME_RECEIPT_CHANGED")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(before):
                fail("MICROSOFT_RUNTIME_INPUT_CHANGED")
            observed = 0
            while block := stream.read(min(1024 * 1024, size - observed + 1)):
                observed += len(block)
                if observed > size:
                    fail("MICROSOFT_RUNTIME_INPUT_CHANGED")
                digest.update(block)
        after = _plain(path, directory=False)
        if (
            observed != size
            or _identity(after) != _identity(before)
            or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
            or digest.hexdigest() != artifact["sha256"]
        ):
            fail("MICROSOFT_RUNTIME_RECEIPT_CHANGED")
        names.add(name.casefold())
        found.add(artifact["role"])
    if found != roles:
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    review = _json(inputs.recipient_terms_review, inputs.recipient_terms_review_sha256)
    review_keys = "schema_version kind status agreement_sha256 community_copy_receipt_sha256 first_party_license microsoft_scope qt_lgpl_rights_preserved runtime_version runtime_architecture member_sha256 terms_sha256 pikepdf_alias_disposition"
    review_version = review.get("schema_version")
    if type(review_version) is not int or review_version not in {1, 2}:
        fail("MICROSOFT_RUNTIME_RECEIPT_INVALID")
    _keys(
        review,
        review_keys
        + (" pikepdf_alias_source qpdf_app_local_members" if review_version == 2 else ""),
        version=review_version,
    )
    expected_disposition = (
        COMMUNITY_ALIAS_DISPOSITION
        if review_version == 2
        else "unchanged-existing-downstream-route"
    )
    if (
        review["kind"] != "recipient-terms-review"
        or review["status"] != "approved"
        or review["agreement_sha256"] != AGREEMENT_SHA256
        or review["community_copy_receipt_sha256"] != inputs.community_copy_receipt_sha256
        or review["first_party_license"] != "MIT"
        or review["microsoft_scope"] != "component-only"
        or review["qt_lgpl_rights_preserved"] is not True
        or review["runtime_version"] != VERSION
        or review["runtime_architecture"] != "AMD64"
        or review["member_sha256"] != {member.name: member.sha256 for member in MEMBERS}
        or review["pikepdf_alias_disposition"] != expected_disposition
        or _digest(_read(inputs.recipient_terms_file, maximum=MAX_JSON_BYTES))
        != review["terms_sha256"]
    ):
        fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    if review_version == 2:
        alias_source = review["pikepdf_alias_source"]
        if (
            type(alias_source) is not dict
            or alias_source
            != {
                "path": PIKEPDF_ALIAS,
                "canonical_member": "msvcp140.dll",
                "sha256": MEMBERS[0].sha256,
                "filename_alias_only": True,
                "historical_notices_preserved": True,
            }
            or alias_source["filename_alias_only"] is not True
            or alias_source["historical_notices_preserved"] is not True
        ):
            fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
        if review["qpdf_app_local_members"] != _qpdf_members():
            fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
    return {
        "owner_acceptance_sha256": str(inputs.owner_acceptance_sha256),
        "community_copy_receipt_sha256": str(inputs.community_copy_receipt_sha256),
        "recipient_terms_review_sha256": str(inputs.recipient_terms_review_sha256),
        "recipient_terms_sha256": review["terms_sha256"],
        "pikepdf_alias_disposition": expected_disposition,
    }


@contextmanager
def _pin_directories(paths: Sequence[Path]) -> Iterator[None]:
    with ExitStack() as stack:
        if os.name == "nt":
            from paperworkshell.storage._windows import open_handle, require_expected_handle

            for path in paths:
                handle = stack.enter_context(open_handle(path, traverse=True))
                require_expected_handle(handle, path, directory=True)
        yield


def _write(path: Path, payload: bytes, created: dict[Path, tuple[int, int]]) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        created[path] = _identity(os.fstat(descriptor))
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view[: 1024 * 1024])
            if count <= 0:
                fail("MICROSOFT_RUNTIME_WRITE_FAILED")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if _digest(_read(path, maximum=max(len(payload), 1))) != _digest(payload):
        fail("MICROSOFT_RUNTIME_WRITE_FAILED")


def _qpdf_members() -> list[dict[str, Any]]:
    by_name = {member.name: member for member in MEMBERS}
    return [
        {
            "path": "runtime/engines/qpdf/bin/" + name,
            "canonical_member": name,
            "sha256": by_name[name].sha256,
        }
        for name in QPDF_APP_LOCAL_NAMES
    ]


def _source_payloads(inputs: Inputs) -> list[tuple[Member, bytes]]:
    reference = _read(inputs.reference, maximum=REFERENCE_SIZE)
    if len(reference) != REFERENCE_SIZE or _digest(reference) != REFERENCE_SHA256:
        fail("MICROSOFT_RUNTIME_REFERENCE_CHANGED")
    _plain(inputs.inspection_root, directory=True)
    for source in SOURCE_CONTAINERS:
        raw = _read(inputs.inspection_root / source.source, maximum=source.size)
        if len(raw) != source.size or _digest(raw) != source.sha256:
            fail("MICROSOFT_RUNTIME_SOURCE_CHANGED")
    payloads: list[tuple[Member, bytes]] = []
    for member in MEMBERS:
        raw = _read(inputs.inspection_root / member.source, maximum=member.size)
        if len(raw) != member.size or _digest(raw) != member.sha256:
            fail("MICROSOFT_RUNTIME_MEMBER_CHANGED")
        if len(raw) < 64 or raw[:2] != b"MZ":
            fail("MICROSOFT_RUNTIME_ARCHITECTURE_INVALID")
        pe = int.from_bytes(raw[60:64], "little")
        if pe + 6 > len(raw) or raw[pe : pe + 6] != b"PE\0\0\x64\x86":
            fail("MICROSOFT_RUNTIME_ARCHITECTURE_INVALID")
        payloads.append((member, raw))
    return payloads


def _overlay_report(inputs: Inputs, rights: dict[str, str]) -> dict[str, Any]:
    include_alias = rights["pikepdf_alias_disposition"] == COMMUNITY_ALIAS_DISPOSITION
    report: dict[str, Any] = {
        "schema_version": 2 if include_alias else 1,
        "task_id": "PWS-0802",
        "kind": "microsoft-runtime-overlay",
        "scope": "New app-local overlay only; no existing candidate changed, target code executed, installation, acquisition or legal acceptance performed by this tool.",
        "reference_sha256": REFERENCE_SHA256,
        "reference_url": REFERENCE_URL,
        "approved_receipt_inputs": rights,
        "runtime_version": VERSION,
        "architecture": "AMD64",
        "members": [
            {
                "path": "runtime/" + member.name,
                "size_bytes": member.size,
                "sha256": member.sha256,
                "cabinet_member": member.name + "_amd64",
            }
            for member in MEMBERS
        ],
        "PWSCompleteRuntime": False,
        "next_gate": "Exact inventory/notices/SBOM integration plus authorized app-local GUI/worker load proof and separate clean-Windows qualification; this receipt alone cannot enable the installer flag.",
        "pikepdf_alias_disposition": rights["pikepdf_alias_disposition"],
    }
    if include_alias:
        report["preparation_inputs"] = {}
        for name in _BOUND_INPUTS:
            path = getattr(inputs, name)
            if not isinstance(path, Path):
                fail("MICROSOFT_RUNTIME_RIGHTS_REQUIRED")
            absolute = Path(os.path.abspath(path))
            if not absolute.is_relative_to(WORKSPACE_ROOT):
                fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
            report["preparation_inputs"][name] = _relative(
                absolute.relative_to(WORKSPACE_ROOT).as_posix()
            )
        report["members"].append(
            {
                "path": PIKEPDF_ALIAS,
                "size_bytes": MEMBERS[0].size,
                "sha256": MEMBERS[0].sha256,
                "cabinet_member": "msvcp140.dll_amd64",
                "canonical_member": "msvcp140.dll",
                "filename_alias_only": True,
            }
        )
        by_name = {member.name: member for member in MEMBERS}
        for row in _qpdf_members():
            report["members"].append(
                {
                    **row,
                    "size_bytes": by_name[row["canonical_member"]].size,
                    "cabinet_member": row["canonical_member"] + "_amd64",
                }
            )
    return report


def prepare(inputs: Inputs) -> dict[str, Any]:
    """Validate every gate/input before creating only one new owned overlay."""

    created: dict[Path, tuple[int, int]] = {}
    directories: dict[Path, tuple[int, int]] = {}
    try:
        rights = _rights(inputs)
        payloads = _source_payloads(inputs)
        output = Path(os.path.abspath(inputs.output))
        if not output.is_relative_to(WORKSPACE_ROOT / "output"):
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
        _plain(output.parent, directory=True)
        if os.path.lexists(output):
            fail("MICROSOFT_RUNTIME_OUTPUT_EXISTS")
        report = _overlay_report(inputs, rights)
        include_alias = report["schema_version"] == 2
        with _pin_directories((output.parent,)):
            output.mkdir()
            directories[output] = _identity(output.lstat())
            with _pin_directories((output,)):
                runtime = output / "runtime"
                runtime.mkdir()
                directories[runtime] = _identity(runtime.lstat())
                with _pin_directories((runtime,)):
                    for member, raw in payloads:
                        _write(runtime / member.name, raw, created)
                    if include_alias:
                        with ExitStack() as alias_pins:
                            by_name = {member.name: raw for member, raw in payloads}
                            for extra in report["members"][len(MEMBERS) :]:
                                parent = runtime
                                for name in PurePosixPath(extra["path"]).parts[1:-1]:
                                    parent = parent / name
                                    if parent not in directories:
                                        parent.mkdir()
                                        directories[parent] = _identity(parent.lstat())
                                        alias_pins.enter_context(_pin_directories((parent,)))
                                _write(
                                    output / extra["path"],
                                    by_name[extra["canonical_member"]],
                                    created,
                                )
                    _write(
                        output / "overlay-receipt.json",
                        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode(),
                        created,
                    )
        return report
    except BaseException as error:
        # No recursive deletion: remove only exact new identities, never unknown data.
        for path, identity in reversed(tuple(created.items())):
            try:
                if _identity(_plain(path, directory=False)) == identity:
                    path.unlink()
            except OSError, PreparationError:
                pass
        for path, identity in reversed(tuple(directories.items())):
            try:
                if _identity(_plain(path, directory=True)) == identity:
                    path.rmdir()
            except OSError, PreparationError:
                pass
        if isinstance(error, PreparationError):
            raise
        if not isinstance(error, Exception):
            raise
        fail("MICROSOFT_RUNTIME_PREPARATION_FAILED")


def verify_overlay(directory: Path, expected_receipt_sha256: str) -> dict[str, Any]:
    """Read-only builder gate for the exact new-route overlay and all bound inputs.

    Acceptance/acquisition/recipient reviews remain explicit coordinator trust
    anchors. Their bytes and real product-copy artifacts are rechecked; this
    function does not manufacture rights or assert clean-machine load evidence.
    """

    try:
        directory = Path(os.path.abspath(directory))
        if not directory.is_relative_to(WORKSPACE_ROOT / "output"):
            fail("MICROSOFT_RUNTIME_PATH_UNSAFE")
        _plain(directory, directory=True)
        report = _json(directory / "overlay-receipt.json", expected_receipt_sha256)
        if type(report.get("schema_version")) is not int or report["schema_version"] != 2:
            fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")
        bound = report.get("preparation_inputs")
        approved = report.get("approved_receipt_inputs")
        if (
            type(bound) is not dict
            or set(bound) != set(_BOUND_INPUTS)
            or type(approved) is not dict
        ):
            fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")
        paths = {name: WORKSPACE_ROOT / _relative(bound[name]) for name in _BOUND_INPUTS}
        inputs = Inputs(
            output=directory,
            **paths,
            owner_acceptance_sha256=approved.get("owner_acceptance_sha256"),
            community_copy_receipt_sha256=approved.get("community_copy_receipt_sha256"),
            recipient_terms_review_sha256=approved.get("recipient_terms_review_sha256"),
        )
        rights = _rights(inputs)
        _source_payloads(inputs)
        expected = _overlay_report(inputs, rights)
        if json.dumps(report, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False
        ):
            fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")
        files = {"overlay-receipt.json", *(member["path"] for member in expected["members"])}
        directories = {
            parent.as_posix()
            for name in files
            for parent in PurePosixPath(name).parents
            if parent != PurePosixPath(".")
        }
        objects = files | directories
        observed: set[str] = set()
        # Enumerate only the fixed expected directories; never recurse into an
        # unknown object or permit attacker-controlled enumeration growth.
        for name in (".", *sorted(directories)):
            parent = directory / name
            _plain(parent, directory=True)
            with os.scandir(parent) as children:
                for child in children:
                    path = Path(child.path)
                    relative = path.relative_to(directory).as_posix()
                    if relative not in objects or relative in observed:
                        fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")
                    _plain(path, directory=relative in directories)
                    observed.add(relative)
        if observed != objects:
            fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")
        for member in expected["members"]:
            raw = _read(directory / member["path"], maximum=member["size_bytes"])
            if len(raw) != member["size_bytes"] or _digest(raw) != member["sha256"]:
                fail("MICROSOFT_RUNTIME_MEMBER_CHANGED")
        return report
    except PreparationError:
        raise
    except Exception:
        fail("MICROSOFT_RUNTIME_OVERLAY_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inspection-root", "reference", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "owner-acceptance",
        "community-copy-receipt",
        "recipient-terms-review",
        "community-copy-root",
        "recipient-terms-file",
    ):
        parser.add_argument("--" + name, type=Path)
    for name in ("owner-acceptance", "community-copy-receipt", "recipient-terms-review"):
        parser.add_argument("--" + name + "-sha256")
    arguments = parser.parse_args(argv)
    try:
        prepare(Inputs(**vars(arguments)))
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 2
    print("MICROSOFT_RUNTIME_OVERLAY_PREPARED_INTEGRATION_NOT_QUALIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
