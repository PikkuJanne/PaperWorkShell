"""Fixed, staged bootstrap for the isolated PWS-0106 renderer benchmark worker."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NoReturn

_CODE_ROOT = Path(__file__).resolve().parents[1]
_STAGE_ROOT = _CODE_ROOT.parent
_SOURCE_ROOT = _CODE_ROOT / "src"
_DATA_ROOT = _STAGE_ROOT / "data"
_SITE_PACKAGES = _STAGE_ROOT / "runtime" / "Lib" / "site-packages"
_MANIFEST_PATH = _CODE_ROOT / "benchmark-stage-manifest-v1.json"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_STAGE_ENTRIES = 12_000
_MAX_STAGE_DEPTH = 64
_MAX_STAGE_BYTES = 768 * 1024 * 1024
_MAX_STAGE_FILE_BYTES = 256 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
sys.path.insert(0, str(_SOURCE_ROOT))
sys.path.insert(1, str(_CODE_ROOT))
sys.path.insert(2, str(_SITE_PACKAGES))

from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import read_frame, write_frame  # noqa: E402


class _StageInvalid(RuntimeError):
    pass


class _StreamContext:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __enter__(self) -> Any:
        return self._stream

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> Literal[False]:
        try:
            self._stream.close()
        except OSError:
            if exception_type is None:
                _fail()
        return False


def _fail() -> NoReturn:
    raise _StageInvalid from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    _fail()


def _is_reparse(path: Path) -> bool:
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        _fail()
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _handle_matches_path(opened: os.stat_result, current: os.stat_result) -> bool:
    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
    ) == (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )


def _read_and_hash(path: Path, maximum_bytes: int) -> tuple[bytes, str]:
    try:
        stream = path.open("rb")
    except OSError:
        _fail()
    with _StreamContext(stream):
        try:
            opened = os.fstat(stream.fileno())
            current = path.lstat()
        except OSError:
            _fail()
        if (
            _is_reparse(path)
            or not stat.S_ISREG(opened.st_mode)
            or not _handle_matches_path(opened, current)
            or not 0 <= opened.st_size <= maximum_bytes
        ):
            _fail()
        output = bytearray()
        digest = hashlib.sha256()
        observed = 0
        while True:
            try:
                chunk = stream.read(min(1024 * 1024, opened.st_size - observed + 1))
            except OSError:
                _fail()
            if not chunk:
                break
            observed += len(chunk)
            if observed > opened.st_size or observed > maximum_bytes:
                _fail()
            digest.update(chunk)
            output.extend(chunk)
        try:
            after = os.fstat(stream.fileno())
            path_after = path.lstat()
        except OSError:
            _fail()
        if (
            observed != opened.st_size
            or not _same_identity(opened, after)
            or not _handle_matches_path(opened, path_after)
        ):
            _fail()
    return bytes(output), digest.hexdigest()


def _relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail()
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not relative.parts
        or relative.parts[0] not in {"runtime", "code", "data"}
    ):
        _fail()
    return relative


def _verify_stage_manifest(expected_sha256: str) -> None:
    raw, observed_manifest_sha256 = _read_and_hash(_MANIFEST_PATH, _MAX_MANIFEST_BYTES)
    if observed_manifest_sha256 != expected_sha256:
        _fail()
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, _StageInvalid:
        _fail()
    if not isinstance(decoded, dict) or set(decoded) != {
        "schema_version",
        "repository_revision",
        "files",
    }:
        _fail()
    files = decoded.get("files")
    repository_revision = decoded.get("repository_revision")
    if (
        decoded.get("schema_version") != 1
        or not isinstance(repository_revision, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", repository_revision) is None
        or not isinstance(files, list)
    ):
        _fail()
    if not 1 <= len(files) <= _MAX_STAGE_ENTRIES:
        _fail()

    expected: dict[PurePosixPath, tuple[int, str]] = {}
    ordered_paths: list[str] = []
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            _fail()
        relative = _relative_path(item.get("path"))
        size = item.get("size")
        sha256 = item.get("sha256")
        if (
            type(size) is not int
            or not 0 <= size <= _MAX_STAGE_FILE_BYTES
            or not isinstance(sha256, str)
            or _SHA256_PATTERN.fullmatch(sha256) is None
            or relative in expected
        ):
            _fail()
        total_bytes += size
        if total_bytes > _MAX_STAGE_BYTES:
            _fail()
        expected[relative] = (size, sha256)
        ordered_paths.append(relative.as_posix())
    if ordered_paths != sorted(ordered_paths):
        _fail()

    observed: set[PurePosixPath] = set()
    pending: list[tuple[Path, int]] = [(_STAGE_ROOT, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_STAGE_DEPTH or _is_reparse(current):
            _fail()
        try:
            current_stat = current.lstat()
        except OSError:
            _fail()
        if stat.S_ISREG(current_stat.st_mode):
            relative = PurePosixPath(current.relative_to(_STAGE_ROOT).as_posix())
            observed.add(relative)
            continue
        if not stat.S_ISDIR(current_stat.st_mode):
            _fail()
        try:
            children = tuple(current.iterdir())
        except OSError:
            _fail()
        if len(observed) + len(pending) + len(children) > _MAX_STAGE_ENTRIES * 2:
            _fail()
        pending.extend((child, depth + 1) for child in children)
    manifest_relative = PurePosixPath(_MANIFEST_PATH.relative_to(_STAGE_ROOT).as_posix())
    if observed != set(expected) | {manifest_relative}:
        _fail()
    for relative, (size, sha256) in expected.items():
        content, observed_sha256 = _read_and_hash(
            _STAGE_ROOT.joinpath(*relative.parts),
            size,
        )
        if len(content) != size or observed_sha256 != sha256:
            _fail()


def _stage_auth(reader: Any, correlation_id: str) -> str:
    raw = read_frame(reader)
    if raw is None:
        _fail()
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, _StageInvalid:
        _fail()
    manifest_sha256 = decoded.get("manifest_sha256") if isinstance(decoded, dict) else None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"protocol_version", "event", "correlation_id", "manifest_sha256"}
        or decoded.get("protocol_version") != 1
        or decoded.get("event") != "stage-auth"
        or decoded.get("correlation_id") != correlation_id
        or not isinstance(manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(manifest_sha256) is None
    ):
        _fail()
    return manifest_sha256


def _write_boundary_error(
    writer: Any,
    correlation_id: str,
    code: str,
    message: str,
) -> None:
    try:
        payload = json.dumps(
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": code,
                "message": message,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        write_frame(writer, payload)
    except BaseException:
        return


def main() -> int:
    try:
        grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
        correlation_id = grant.worker_instance_id.hex
        del grant
        reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
        writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    except BaseException:
        return 2
    try:
        isolation = verify_runtime_isolation()
    except BaseException:
        _write_boundary_error(
            writer,
            correlation_id,
            "PDF_RENDER_BENCHMARK_ISOLATION_INVALID",
            "the isolated renderer boundary is invalid",
        )
        return 2
    try:
        _verify_stage_manifest(_stage_auth(reader, correlation_id))
    except BaseException:
        _write_boundary_error(
            writer,
            correlation_id,
            "PDF_RENDER_BENCHMARK_STAGE_INVALID",
            "the staged benchmark resources are invalid",
        )
        return 2
    try:
        # No ambient parent PATH survives launch.  Keep only a stage-derived, scoped
        # DLL search handle for the duration of renderer imports and execution.
        dll_directory = os.add_dll_directory(str(_SITE_PACKAGES / "PySide6"))
        from tools import pdf_renderer_benchmark as benchmark
    except BaseException:
        _write_boundary_error(
            writer,
            correlation_id,
            "PDF_RENDER_BENCHMARK_RUNTIME_INVALID",
            "the staged benchmark runtime is invalid",
        )
        return 2
    result = 2
    try:
        result = benchmark._isolated_child_main(
            _DATA_ROOT,
            isolation,
            correlation_id,
            reader,
            writer,
        )
    except BaseException:
        _write_boundary_error(
            writer,
            correlation_id,
            "PDF_RENDER_BENCHMARK_HARNESS_INVALID",
            "the staged benchmark harness is invalid",
        )
    try:
        dll_directory.close()
    except BaseException:
        if result == 0:
            _write_boundary_error(
                writer,
                correlation_id,
                "PDF_RENDER_BENCHMARK_RUNTIME_INVALID",
                "the staged benchmark runtime is invalid",
            )
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
