"""Prepare an owned, offline dependency builder without executing its inputs.

Only standard-library modules and the stdlib-only artifact/staging helpers may
be imported here: jsonschema is one of the inputs this recipe bootstraps.
If publication is busy, let ordinary native lease cleanup finish, then retry
with a new output path. This helper does not change ACLs or native leases.
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import io
import json
import os
import platform
import re
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Final, NoReturn, cast
from urllib.parse import unquote, urlsplit

from tools.audit_tesseract_redistribution import pe_imports
from tools.release_artifacts import (
    CHUNK_BYTES,
    Artifact,
    ReleaseArtifactError,
    extract_selected_zip,
    file_hashes,
    plain_path,
    safe_relative,
    verify_artifact,
)
from tools.release_staging import (
    canonical_json,
    inventory_files,
    stage_wheel,
    verify_files,
    wheel_selection,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
REVIEW_PATHS: Final = (
    "packaging/release-reviews/cpython-inputs-v1.json",
    "packaging/release-reviews/validation-build-components-v1.json",
    "packaging/release-reviews/python-inputs-v1.json",
    "packaging/release-reviews/gpg-build-components-v1.json",
)
RECIPE_PATHS: Final = (
    "tools/prepare_dependency_builder.py",
    "tools/release_artifacts.py",
    "tools/release_staging.py",
    "tools/audit_tesseract_redistribution.py",
)
VALIDATORS: Final = frozenset(
    {"attrs", "jsonschema", "jsonschema-specifications", "referencing", "rpds-py"}
)
ISOLATED_PATH: Final = b"python314.zip\n.\nLib/site-packages\n"
MAX_REVIEW_BYTES: Final = 32 * 1024 * 1024
MAX_BUILDER_FILES: Final = 4096
MAX_BUILDER_BYTES: Final = 512 * 1024 * 1024
MAX_TAR_MEMBERS: Final = 50_000
MAX_TAR_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_TAR_METADATA_BYTES: Final = 8 * 1024 * 1024
MAX_TAR_METADATA_DEPTH: Final = 32
MAX_TOOL_BYTES: Final = 32 * 1024 * 1024
MAX_TAR_SECONDS: Final = 600
_CRT: Final = re.compile(
    r"(?:concrt140|vcruntime140(?:_[0-9]+)?|msvcp140(?:_[0-9]+|_atomic_wait|_codecvt_ids)?)\.dll\Z",
    re.I,
)
_SYSTEM_GPG_IMPORTS: Final = frozenset({"kernel32.dll", "ntdll.dll", "user32.dll"})


def _fail(code: str) -> NoReturn:
    raise ReleaseArtifactError(code)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _fail("RELEASE_BUILDER_JSON")
        result[key] = value
    return result


def _number(_value: str) -> NoReturn:
    _fail("RELEASE_BUILDER_JSON")


def _load(path: Path) -> dict[str, Any]:
    plain_path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_REVIEW_BYTES + 1)
    if not raw or len(raw) > MAX_REVIEW_BYTES:
        _fail("RELEASE_BUILDER_LIMIT")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_number, parse_float=_number
    )
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        _fail("RELEASE_BUILDER_REVIEW")
    return cast(dict[str, Any], value)


def _output_path(path: Path, root: Path) -> Path:
    plain_path(path)
    resolved, output = path.absolute().resolve(), (root / "output").resolve()
    if resolved == output or not resolved.is_relative_to(output):
        _fail("RELEASE_BUILDER_OUTPUT_SCOPE")
    return resolved


def _cache_path(path: Path, root: Path) -> Path:
    plain_path(path)
    resolved = path.absolute().resolve()
    if not resolved.is_relative_to((root / "output").resolve()) or not resolved.is_dir():
        _fail("RELEASE_BUILDER_CACHE_SCOPE")
    return resolved


def _artifact(item: Mapping[str, Any]) -> Artifact:
    name, url, size, digest = (item[key] for key in ("name", "url", "size_bytes", "sha256"))
    if (
        not isinstance(name, str)
        or safe_relative(name) != Path(name).name
        or type(size) is not int
        or not isinstance(digest, str)
        or not isinstance(url, str)
    ):
        _fail("RELEASE_BUILDER_INPUT")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(c) < 32 for c in url)
        or re.search(
            r"(?<![A-Za-z0-9])[A-Za-z]:[/\\]|file://|\\\\|/(?:Users|home)/", unquote(url), re.I
        )
    ):
        _fail("RELEASE_BUILDER_INPUT")
    return Artifact(name, url, size, digest)


def _locate(expected: Artifact, directories: Sequence[Path]) -> Path:
    for directory in directories:
        path = directory / expected.name
        plain_path(path)
        if path.exists():
            verify_artifact(path, expected)
            return path
    _fail("RELEASE_BUILDER_INPUT_MISSING")


def _record(expected: Artifact) -> dict[str, object]:
    return {
        "name": expected.name,
        "url": expected.url,
        "size_bytes": expected.size_bytes,
        "sha256": expected.sha256,
    }


def _review_hashes(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in (*REVIEW_PATHS, *RECIPE_PATHS):
        size, digest, _ = file_hashes(root / relative, MAX_REVIEW_BYTES)
        records.append({"path": relative, "size_bytes": size, "sha256": digest})
    return records


def _cleanup(directory: Path, root: Path) -> None:
    """Delete only this call's checked temporary tree, never follow redirection."""
    _output_path(directory, root)
    plain_path(directory)
    if not directory.name.startswith("pws-builder-"):
        _fail("RELEASE_BUILDER_CLEANUP_SCOPE")
    pending, directories = [directory], []
    files: list[Path] = []
    while pending:
        current = pending.pop()
        plain_path(current)
        directories.append(current)
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                plain_path(path)
                if len(files) + len(directories) + len(pending) > 250_000:
                    _fail("RELEASE_BUILDER_LIMIT")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    files.append(path)
    for path in files:
        path.unlink()
    for path in reversed(directories):
        path.rmdir()


class _TarInput(io.BufferedIOBase):
    """Bound decompressed seeks and individual tar metadata reads before allocation."""

    def __init__(self, path: Path) -> None:
        self.source = bz2.BZ2File(path, "rb")
        self.started = time.monotonic()

    def _check(self) -> None:
        if self.source.tell() > MAX_TAR_BYTES or time.monotonic() - self.started > MAX_TAR_SECONDS:
            _fail("RELEASE_BUILDER_LIMIT")

    def read(self, size: int | None = -1) -> bytes:
        if size is None or not 0 <= size <= CHUNK_BYTES:
            _fail("RELEASE_BUILDER_LIMIT")
        self._check()
        data = self.source.read(min(size, MAX_TAR_BYTES - self.source.tell() + 1))
        self._check()
        return data

    def tell(self) -> int:
        return self.source.tell()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence != os.SEEK_SET or not self.source.tell() <= offset <= MAX_TAR_BYTES:
            _fail("RELEASE_BUILDER_LIMIT")
        self._check()
        actual = self.source.seek(offset)
        self._check()
        if actual != offset:
            _fail("RELEASE_ARCHIVE_MEMBER")
        return actual

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self.source.close()
        super().close()


class _TarArchive(tarfile.TarFile):
    _pws_header_count: int = 0
    _pws_metadata_bytes: int = 0
    _pws_header_depth: int = 0


class _TarInfo(tarfile.TarInfo):
    """Bound metadata before CPython's recursive tar-header processing.

    This recipe targets pinned CPython 3.14. Metadata handlers are checked before
    they can recursively retain buffers; this Git subset never needs sparse maps.
    """

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        state = cast(_TarArchive, archive)
        count = state._pws_header_count + 1
        depth = state._pws_header_depth + 1
        metadata = state._pws_metadata_bytes
        if self.type in {
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
        }:
            metadata += self.size
        if (
            self.size < 0
            or count > MAX_TAR_MEMBERS
            or depth > MAX_TAR_METADATA_DEPTH
            or metadata > MAX_TAR_METADATA_BYTES
        ):
            _fail("RELEASE_BUILDER_LIMIT")
        if self.type == tarfile.GNUTYPE_SPARSE:
            _fail("RELEASE_BUILDER_TAR_SPARSE")
        state._pws_header_count = count
        state._pws_metadata_bytes = metadata
        state._pws_header_depth = depth
        try:
            # These CPython hooks are intentionally absent from public type stubs.
            return cast(tarfile.TarInfo, cast(Any, super())._proc_member(archive))
        finally:
            state._pws_header_depth = depth - 1

    def _reject_sparse(self, *args: object, **kwargs: object) -> NoReturn:
        _fail("RELEASE_BUILDER_TAR_SPARSE")

    _proc_sparse = _reject_sparse
    _proc_gnusparse_00 = _reject_sparse
    _proc_gnusparse_01 = _reject_sparse
    _proc_gnusparse_10 = _reject_sparse


def _gpg_records(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = review["tool_files"]
    if not isinstance(records, list) or len(records) != 14:
        _fail("RELEASE_BUILDER_GPG")
    names, members = set(), set()
    for item in records:
        name, member = safe_relative(item["name"]), safe_relative(item["member"])
        if (
            name != Path(name).name
            or member != "usr/bin/" + name
            or name.casefold() in names
            or member.casefold() in members
        ):
            _fail("RELEASE_BUILDER_GPG")
        names.add(name.casefold())
        members.add(member.casefold())
    if "gpg.exe" not in names:
        _fail("RELEASE_BUILDER_GPG")
    for item in records:
        imports = set(item["normal_imports"]) | set(item["delay_imports"])
        if (
            imports - names - _SYSTEM_GPG_IMPORTS
            or set(item["local_dependencies"]) != imports & names
        ):
            _fail("RELEASE_BUILDER_GPG")
    return cast(list[dict[str, Any]], records)


def _stage_gpg(
    source: Path, expected: Artifact, destination: Path, records: Sequence[Mapping[str, Any]]
) -> list[str]:
    verify_artifact(source, expected)
    wanted = {item["member"]: item for item in records}
    seen: set[str] = set()
    found: set[str] = set()
    expanded = metadata = 0
    with (
        _TarInput(source) as stream,
        _TarArchive.open(fileobj=cast(BinaryIO, stream), mode="r:", tarinfo=_TarInfo) as archive,
    ):
        for index, member in enumerate(archive):
            name = safe_relative(member.name.rstrip("/"))
            if index >= MAX_TAR_MEMBERS or name.casefold() in seen:
                _fail("RELEASE_BUILDER_LIMIT")
            seen.add(name.casefold())
            expanded += member.size
            metadata += sum(
                len(k.encode()) + len(v.encode()) for k, v in member.pax_headers.items()
            )
            if expanded > MAX_TAR_BYTES or metadata > MAX_TAR_METADATA_BYTES:
                _fail("RELEASE_BUILDER_LIMIT")
            item = wanted.get(name)
            if item is None:
                continue
            if (
                not member.isfile()
                or member.size != item["size_bytes"]
                or member.size > MAX_TOOL_BYTES
            ):
                _fail("RELEASE_BUILDER_GPG")
            extracted = archive.extractfile(member)
            if extracted is None:
                _fail("RELEASE_BUILDER_GPG")
            target = destination / item["name"]
            plain_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with extracted, target.open("xb") as output:
                remaining = member.size
                while remaining:
                    chunk = extracted.read(min(remaining, CHUNK_BYTES))
                    if not chunk:
                        _fail("RELEASE_BUILDER_GPG")
                    output.write(chunk)
                    remaining -= len(chunk)
            verify_artifact(
                target, Artifact(item["name"], expected.url, item["size_bytes"], item["sha256"])
            )
            normal, delayed = pe_imports(target.read_bytes())
            if set(normal) != set(item["normal_imports"]) or set(delayed) != set(
                item["delay_imports"]
            ):
                _fail("RELEASE_BUILDER_GPG")
            found.add(name)
    if found != set(wanted):
        _fail("RELEASE_BUILDER_GPG")
    return sorted("gpg/" + item["name"] for item in records)


def _copy(source: Path, target: Path) -> None:
    plain_path(source)
    plain_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream, target.open("xb") as output:
        total = 0
        while chunk := stream.read(CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_BUILDER_BYTES:
                _fail("RELEASE_BUILDER_LIMIT")
            output.write(chunk)


def _preflight_budget(
    embed: Artifact,
    wheels: Sequence[tuple[str, Artifact]],
    sources: Mapping[str, Path],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Admit the complete selected payload before creating any output files."""
    tree_entries: set[str] = set()

    def admit(relative: str) -> None:
        path = PurePosixPath(safe_relative(relative))
        if len(path.parts) > 64:
            _fail("RELEASE_BUILDER_LIMIT")
        tree_entries.add(relative.casefold())
        tree_entries.update(
            parent.as_posix().casefold() for parent in path.parents if parent.as_posix() != "."
        )
        if len(tree_entries) > MAX_BUILDER_FILES * 2:
            _fail("RELEASE_BUILDER_LIMIT")

    with zipfile.ZipFile(sources[embed.name]) as archive:
        selection = {
            name: "python/" + name
            for name in archive.namelist()
            if not name.endswith("/")
            and Path(name).name.casefold() != "python314._pth"
            and not _CRT.fullmatch(Path(name).name)
        }
        if not {"python.exe", "python314.dll", "python314.zip"} <= set(selection):
            _fail("RELEASE_BUILDER_REVIEW")
        count = len(selection) + 1 + len(records)
        size = (
            sum(archive.getinfo(name).file_size for name in selection)
            + len(ISOLATED_PATH)
            + sum(item["size_bytes"] for item in records)
        )
        for relative in [
            *selection.values(),
            "python/python314._pth",
            *("gpg/" + item["name"] for item in records),
        ]:
            admit(relative)
    for cid, wheel in wheels:
        selected = wheel_selection(sources[wheel.name], wheel, cid, {})
        with zipfile.ZipFile(sources[wheel.name]) as archive:
            # stage_wheel expands its complete selected subset into scratch.
            # Count omitted CRT objects too: their temporary cost is real.
            names = list(selected)
            count += len(names)
            size += sum(archive.getinfo(name).file_size for name in names)
        for relative in selected.values():
            admit("python/" + PurePosixPath(relative).relative_to("runtime").as_posix())
        if count > MAX_BUILDER_FILES or size > MAX_BUILDER_BYTES:
            _fail("RELEASE_BUILDER_LIMIT")
    if count > MAX_BUILDER_FILES or size > MAX_BUILDER_BYTES:
        _fail("RELEASE_BUILDER_LIMIT")
    return selection


def _selected_inputs(
    root: Path,
) -> tuple[Artifact, list[tuple[str, Artifact]], Artifact, list[dict[str, Any]]]:
    cpython, validation, python_inputs, gpg = [_load(root / relative) for relative in REVIEW_PATHS]
    embed = _artifact(
        next(
            item for item in cpython["artifacts"] if item["name"] == "python-3.14.6-embed-amd64.zip"
        )
    )
    selected = [item for item in validation["components"] if item["component_id"] in VALIDATORS]
    if len(selected) != 5 or {item["component_id"] for item in selected} != VALIDATORS:
        _fail("RELEASE_BUILDER_REVIEW")
    wheels = [(item["component_id"], _artifact(item["input_artifact"])) for item in selected]
    typing = next(
        item for item in python_inputs["components"] if item["component_id"] == "typing-extensions"
    )
    wheels.append(("typing-extensions", _artifact(typing["wheel"])))
    git = _artifact(gpg["artifacts"][0])
    records = _gpg_records(gpg)
    return embed, wheels, git, records


def _publish(temporary: Path, destination: Path) -> None:
    """Retry only short Windows sharing locks after every owned stream is closed."""
    for attempt in range(11):
        if destination.exists():
            _fail("RELEASE_BUILDER_OUTPUT_EXISTS")
        try:
            temporary.rename(destination)
            return
        except PermissionError as error:
            status = getattr(error, "winerror", None)
            if status not in {5, 32, 33}:
                raise
            if status == 5 or attempt == 10:
                _fail("RELEASE_BUILDER_OUTPUT_BUSY")
            time.sleep(0.1)


def prepare_builder(
    destination: Path, artifact_directories: Sequence[Path], *, root: Path = PROJECT_ROOT
) -> dict[str, object]:
    """Publish one new atomic container with payload and independent sidecars."""
    destination = _output_path(destination, root)
    if destination.exists():
        _fail("RELEASE_BUILDER_OUTPUT_EXISTS")
    if not 1 <= len(artifact_directories) <= 16:
        _fail("RELEASE_BUILDER_CACHE_SCOPE")
    caches = [_cache_path(path, root) for path in artifact_directories]
    before = _review_hashes(root)
    embed, wheels, git, records = _selected_inputs(root)
    inputs = [embed, *(wheel for _cid, wheel in wheels), git]
    if len({item.name.casefold() for item in inputs}) != len(inputs):
        _fail("RELEASE_INPUT_COLLISION")
    sources = {item.name: _locate(item, caches) for item in inputs}
    selection = _preflight_budget(embed, wheels, sources, records)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="pws-builder-", dir=destination.parent))
    try:
        payload, scratch = temporary / "builder", temporary / "pws-builder-wheels"
        payload.mkdir()
        owners: dict[str, str] = {}
        for relative in extract_selected_zip(sources[embed.name], embed, payload, selection):
            owners[relative] = "cpython"
        with (payload / "python/python314._pth").open("xb") as output:
            output.write(ISOLATED_PATH)
        owners["python/python314._pth"] = "cpython"
        omissions = {}
        for cid, wheel in wheels:
            wheel_stage = scratch / cid
            written, omitted = stage_wheel(sources[wheel.name], wheel, wheel_stage, cid)
            for relative in written:
                source = wheel_stage / relative
                if _CRT.fullmatch(source.name):
                    omitted.append(relative)
                    continue
                output_name = "python/" + Path(relative).relative_to("runtime").as_posix()
                _copy(source, payload / output_name)
                owners[output_name] = cid
            omissions[cid] = sorted(omitted)
        if scratch.exists():
            _cleanup(scratch, root)
        for relative in _stage_gpg(sources[git.name], git, payload / "gpg", records):
            owners[relative] = "gpg-build-verifier"
        files = inventory_files(payload, owners)
        if (
            len(files) > MAX_BUILDER_FILES
            or sum(cast(int, item["size_bytes"]) for item in files) > MAX_BUILDER_BYTES
        ):
            _fail("RELEASE_BUILDER_LIMIT")
        manifest = {"schema_version": 1, "files": files}
        manifest_bytes = canonical_json(manifest)
        with (temporary / "builder-inventory.json").open("xb") as output:
            output.write(manifest_bytes)
        for item in inputs:
            verify_artifact(sources[item.name], item)
        if before != _review_hashes(root):
            _fail("RELEASE_BUILDER_RECIPE_CHANGED")
        receipt = {
            "schema_version": 1,
            "status": "prepared",
            "scope": "Owned offline dependency builder; no input binaries executed",
            "payload": "builder",
            "python": "builder/python/python.exe",
            "gpg": "builder/gpg/gpg.exe",
            "isolated_path_sha256": hashlib.sha256(ISOLATED_PATH).hexdigest(),
            "inventory": "builder-inventory.json",
            "inventory_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "preparation_host": {
                "python_version": platform.python_version(),
                "operating_system": platform.system(),
                "architecture": platform.machine(),
            },
            "cache_directories": [path.relative_to(root.resolve()).as_posix() for path in caches],
            "recipes_before": before,
            "recipes_after": before,
            "inputs_before": [_record(item) for item in inputs],
            "inputs_after": [_record(item) for item in inputs],
            "wheel_omissions": omissions,
            "external_runtime": "Canonical Microsoft VC runtime is an existing host prerequisite; this preparation neither installs nor tests it.",
        }
        preparation_bytes = canonical_json(receipt)
        with (temporary / "preparation.json").open("xb") as output:
            output.write(preparation_bytes)
        verify_files(payload, files)
        _publish(temporary, destination)
        return {**receipt, "preparation_sha256": hashlib.sha256(preparation_bytes).hexdigest()}
    except BaseException:
        _cleanup(temporary, root)
        raise


def verify_builder(
    destination: Path, *, expected_preparation_sha256: str | None = None, root: Path = PROJECT_ROOT
) -> dict[str, object]:
    """Check the same prepared files and pinned inputs before/after builder use."""
    destination = _output_path(destination, root)
    preparation_hash = file_hashes(destination / "preparation.json", MAX_REVIEW_BYTES)[1]
    if expected_preparation_sha256 is not None and preparation_hash != expected_preparation_sha256:
        _fail("RELEASE_BUILDER_MANIFEST_CHANGED")
    receipt = _load(destination / "preparation.json")
    embed, wheels, git, _records = _selected_inputs(root)
    inputs = [embed, *(wheel for _cid, wheel in wheels), git]
    if receipt["inputs_before"] != [_record(item) for item in inputs]:
        _fail("RELEASE_BUILDER_INPUT")
    manifest_path = destination / "builder-inventory.json"
    if (
        file_hashes(manifest_path, MAX_REVIEW_BYTES)[1] != receipt["inventory_sha256"]
        or receipt["payload"] != "builder"
    ):
        _fail("RELEASE_BUILDER_MANIFEST_CHANGED")
    manifest = _load(manifest_path)
    verify_files(destination / "builder", manifest["files"])
    if (
        receipt["recipes_before"] != receipt["recipes_after"]
        or receipt["recipes_after"] != _review_hashes(root)
        or receipt["inputs_before"] != receipt["inputs_after"]
    ):
        _fail("RELEASE_BUILDER_RECIPE_CHANGED")
    caches = [
        _cache_path(root / safe_relative(path), root) for path in receipt["cache_directories"]
    ]
    for item in receipt["inputs_before"]:
        _locate(_artifact(item), caches)
    if (destination / "builder/python/python314._pth").read_bytes() != ISOLATED_PATH:
        _fail("RELEASE_BUILDER_MANIFEST_CHANGED")
    return {
        "schema_version": 1,
        "status": "verified",
        "files": len(manifest["files"]),
        "inventory_sha256": receipt["inventory_sha256"],
        "preparation_sha256": preparation_hash,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, action="append", default=[])
    parser.add_argument("--cache", type=Path, action="append", default=[])
    parser.add_argument("--expected-preparation-sha256")
    args = parser.parse_args(argv)
    try:
        result = (
            prepare_builder(args.output, [*args.artifacts, *args.cache])
            if args.command == "prepare"
            else verify_builder(
                args.output, expected_preparation_sha256=args.expected_preparation_sha256
            )
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except ReleaseArtifactError as error:
        print(
            f"ERROR [{error}] Dependency builder could not be prepared or verified.",
            file=sys.stderr,
        )
    except Exception:
        print(
            "ERROR [RELEASE_BUILDER_INPUT] Dependency builder could not be read safely.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
