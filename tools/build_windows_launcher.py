"""Build the project-owned CRT-free launcher from the reviewed MSYS2 closure.

Offline build-only tooling. No compiler discovery, downloads, machine installs,
user documents, application data or signing material are consumed.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import struct
import sys
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NoReturn

from tools import build_libtiff_no_jbig as native
from tools.audit_tesseract_redistribution import pe_imports

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
SOURCE: Final = Path("packaging/windows/launcher.c")
RESOURCE: Final = Path("packaging/windows/launcher.rc")
TOOL: Final = Path("tools/build_windows_launcher.py")
ARTIFACTS: Final = Path("output/pws0801/native-rebuild/artifacts")
ALLOWED_IMPORTS: Final = frozenset({"kernel32.dll", "user32.dll", "shell32.dll", "ole32.dll"})
MAX_TOOLCHAIN_FILES: Final = 30_000
MAX_TOOLCHAIN_BYTES: Final = 1_200_000_000
MAX_SOURCE_BYTES: Final = 128 * 1024
QUALIFICATION: Final = (
    "not yet qualified on a clean Windows environment; not a signed public release"
)


class LauncherBuildError(ValueError):
    """Closed diagnostic without a raw compiler message or private local path."""


def _fail(code: str) -> NoReturn:
    raise LauncherBuildError(code)


def _regular_file(path: Path, *, maximum: int = native.MAX_MEMBER_BYTES) -> os.stat_result:
    native.reject_redirected_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= maximum:
        _fail("LAUNCHER_BUILD_INPUT_INVALID")
    return info


def owned_output_directory(path: Path, *, root: Path = PROJECT_ROOT) -> Path:
    native.reject_redirected_path(root)
    native.reject_redirected_path(path)
    candidate = path.absolute()
    boundary = root.absolute() / "output/pws0802"
    if not candidate.is_relative_to(boundary) or candidate == boundary or ".." in candidate.parts:
        _fail("LAUNCHER_BUILD_OUTPUT_SCOPE")
    if candidate.exists():
        _fail("LAUNCHER_BUILD_OUTPUT_EXISTS")
    return candidate


def _archive_files(path: Path) -> dict[str, tuple[int, str]]:
    """Read a bounded, already authenticated archive without extracting code."""
    records: dict[str, tuple[int, str]] = {}
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    expanded = 0
    with tarfile.open(path, mode="r|*") as archive:
        for member in archive:
            name = native.safe_member_name(member.name).as_posix()
            key = name.casefold()
            expanded += member.size
            if (
                key in seen
                or len(seen) >= native.MAX_MEMBERS
                or not 0 <= member.size <= native.MAX_MEMBER_BYTES
                or expanded > native.MAX_EXPANDED_BYTES
                or not (member.isdir() or member.isfile() or member.islnk())
            ):
                _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
            seen.add(key)
            if not name.startswith("ucrt64/") or member.isdir():
                continue
            relative = name.removeprefix("ucrt64/")
            if member.islnk():
                target = native.safe_member_name(member.linkname).as_posix()
                if not target.startswith("ucrt64/"):
                    _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
                links.append((relative, target.removeprefix("ucrt64/")))
                continue
            source = archive.extractfile(member)
            if source is None:
                _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
            digest = hashlib.sha256()
            size = 0
            with source:
                while chunk := source.read(1_048_576):
                    size += len(chunk)
                    if size > member.size:
                        _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
                    digest.update(chunk)
            if size != member.size:
                _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
            records[relative] = (size, digest.hexdigest())
    for name, target in links:
        if target not in records:
            _fail("LAUNCHER_BUILD_ARCHIVE_INVALID")
        records[name] = records[target]
    return records


def expected_toolchain(
    *, root: Path = PROJECT_ROOT
) -> tuple[dict[str, tuple[int, str]], list[dict[str, object]]]:
    """The frozen prior recipe anchors every original MSYS2 archive's size/hash."""
    recipe = native.load_recipe(root)
    expected: dict[str, tuple[int, str]] = {}
    inputs: list[dict[str, object]] = []
    for item in recipe["build_inputs"]:
        if item["kind"] != "msys2-toolchain":
            continue
        path = root / ARTIFACTS / item["filename"]
        _regular_file(path)
        native.verify_artifact(
            path, expected_size=item["size_bytes"], expected_sha256=item["sha256"]
        )
        for name, record in _archive_files(path).items():
            if name in expected and expected[name] != record:
                _fail("LAUNCHER_BUILD_TOOLCHAIN_COLLISION")
            expected[name] = record
        inputs.append(
            {
                "component_id": item["component_id"],
                "path": path.relative_to(root).as_posix(),
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
        )
    if (
        not inputs
        or len(expected) > MAX_TOOLCHAIN_FILES
        or sum(size for size, _ in expected.values()) > MAX_TOOLCHAIN_BYTES
    ):
        _fail("LAUNCHER_BUILD_TOOLCHAIN_LIMIT")
    return expected, inputs


def verify_toolchain(compiler_root: Path, expected: dict[str, tuple[int, str]]) -> str:
    """Authenticate the complete extracted closure, including headers/import libs."""
    native.reject_redirected_path(compiler_root)
    if not compiler_root.is_dir() or compiler_root.name != "ucrt64":
        _fail("LAUNCHER_BUILD_TOOLCHAIN_INVALID")
    actual: dict[str, tuple[int, str]] = {}
    for current, directories, files in os.walk(compiler_root, followlinks=False):
        for directory in directories:
            native.reject_redirected_path(Path(current) / directory)
        for name in files:
            path = Path(current) / name
            info = _regular_file(path)
            relative = path.relative_to(compiler_root).as_posix()
            if (
                len(actual) >= MAX_TOOLCHAIN_FILES
                or relative not in expected
                or info.st_size != expected[relative][0]
            ):
                _fail("LAUNCHER_BUILD_TOOLCHAIN_MISMATCH")
            digest = native.sha256_file(path)
            if digest != expected[relative][1]:
                _fail("LAUNCHER_BUILD_TOOLCHAIN_MISMATCH")
            actual[relative] = (info.st_size, digest)
    if actual != expected:
        _fail("LAUNCHER_BUILD_TOOLCHAIN_MISMATCH")
    encoded = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_commands(
    output: Path, compiler_root: Path, *, root: Path = PROJECT_ROOT
) -> tuple[list[str], list[str]]:
    resource = output / "launcher-resource.o"
    executable = output / "runtime/PaperWorkShell.exe"
    return (
        [
            str(compiler_root / "bin/windres.exe"),
            "--preprocessor=" + str(compiler_root / "bin/gcc.exe"),
            "--preprocessor-arg=-E",
            "--preprocessor-arg=-xc",
            "--preprocessor-arg=-DRC_INVOKED",
            "--input",
            str(root / RESOURCE),
            "--output",
            str(resource),
            "--input-format=rc",
            "--output-format=coff",
            "--target=pe-x86-64",
        ],
        [
            str(compiler_root / "bin/gcc.exe"),
            "-std=c11",
            "-Os",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wfatal-errors",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-stack-protector",
            "-fno-tree-loop-distribute-patterns",
            "-fno-ident",
            "-nostdlib",
            "-Wl,--entry,pws_entry,--subsystem,windows,--no-insert-timestamp,--dynamicbase,--nxcompat,--high-entropy-va,--major-os-version,10,--minor-os-version,0,--major-subsystem-version,10,--minor-subsystem-version,0",
            str(root / SOURCE),
            str(resource),
            "-o",
            str(executable),
            "-lkernel32",
            "-luser32",
            "-lshell32",
            "-lole32",
        ],
    )


def inspect_launcher(data: bytes) -> dict[str, object]:
    normal, delayed = pe_imports(data)
    if delayed or {name.casefold() for name in normal} != ALLOWED_IMPORTS:
        _fail("LAUNCHER_BUILD_PE_IMPORTS")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    optional = offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    subsystem, flags = struct.unpack_from("<HH", data, optional + 68)
    os_version = struct.unpack_from("<HH", data, optional + 40)
    subsystem_version = struct.unpack_from("<HH", data, optional + 48)
    relocation_rva, relocation_size = struct.unpack_from("<II", data, optional + 152)
    load_rva, load_size = struct.unpack_from("<II", data, optional + 192)
    entry = struct.unpack_from("<I", data, optional + 16)[0]
    if (
        machine != 0x8664
        or magic != 0x20B
        or subsystem != 2
        or not entry
        or flags & 0x160 != 0x160
        or os_version != (10, 0)
        or subsystem_version != (10, 0)
        or not relocation_rva
        or not 12 <= relocation_size <= 1024 * 1024
        or not load_rva
        or not 148 <= load_size <= 4096
    ):
        _fail("LAUNCHER_BUILD_PE_POLICY")
    section_count = struct.unpack_from("<H", data, offset + 6)[0]
    optional_size = struct.unpack_from("<H", data, offset + 20)[0]

    def file_offset(rva: int, length: int) -> int:
        matches = []
        for index in range(section_count):
            section = optional + optional_size + index * 40
            virtual_size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
            if address <= rva and rva + length <= address + min(virtual_size, raw_size):
                matches.append(raw + rva - address)
        if len(matches) != 1:
            _fail("LAUNCHER_BUILD_PE_POLICY")
        return int(matches[0])

    load = file_offset(load_rva, load_size)
    declared_size = struct.unpack_from("<I", data, load)[0]
    cookie_address = struct.unpack_from("<Q", data, load + 88)[0]
    guard_flags = struct.unpack_from("<I", data, load + 144)[0]
    image_base = struct.unpack_from("<Q", data, optional + 24)[0]
    if declared_size != load_size or guard_flags != 0x800 or cookie_address < image_base:
        _fail("LAUNCHER_BUILD_PE_POLICY")
    cookie = file_offset(cookie_address - image_base, 8)
    if struct.unpack_from("<Q", data, cookie)[0] != 0x00002B992DDFA232:
        _fail("LAUNCHER_BUILD_PE_POLICY")
    return {
        "normal_imports": list(normal),
        "delay_imports": list(delayed),
        "machine": "amd64",
        "subsystem": "windows-gui",
        "crt_runtime": "none",
        "entrypoint_rva": entry,
        "dll_characteristics": flags,
        "os_version": list(os_version),
        "subsystem_version": list(subsystem_version),
        "relocation_size_bytes": relocation_size,
        "load_config_size_bytes": load_size,
        "security_cookie_policy": "loader-cookie-unused-by-compiler; no GS claim",
    }


def _windows_directory() -> Path:
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    function = library.GetWindowsDirectoryW
    function.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    function.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    count = int(function(buffer, len(buffer)))
    if not count or count >= len(buffer):
        _fail("LAUNCHER_BUILD_PLATFORM")
    return Path(buffer.value)


def _build_launcher_checked(
    output_directory: Path, compiler_root: Path, *, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    """Create a new owned output, authenticate before execution and verify after."""
    if os.name != "nt":
        _fail("LAUNCHER_BUILD_PLATFORM")
    root = root.absolute()
    output = owned_output_directory(output_directory, root=root)
    native.reject_redirected_path(compiler_root)
    compiler_root = compiler_root.absolute()
    if not compiler_root.is_relative_to(root / "output"):
        _fail("LAUNCHER_BUILD_TOOLCHAIN_SCOPE")
    expected, inputs = expected_toolchain(root=root)
    closure = verify_toolchain(compiler_root, expected)
    sources: dict[str, str] = {}
    for relative in (SOURCE, RESOURCE, TOOL):
        _regular_file(root / relative, maximum=MAX_SOURCE_BYTES)
        sources[relative.as_posix()] = native.sha256_file(root / relative)
    output.mkdir(parents=True, exist_ok=False)
    (output / "runtime").mkdir()
    (output / "temp").mkdir()
    windows = _windows_directory()
    environment = {
        "SystemRoot": str(windows),
        "PATH": os.pathsep.join((str(compiler_root / "bin"), str(windows / "System32"))),
        "TEMP": str(output / "temp"),
        "TMP": str(output / "temp"),
        "LANG": "C",
        "LC_ALL": "C",
    }
    commands = []
    for label, command in zip(
        ("resource", "compile"), build_commands(output, compiler_root, root=root), strict=True
    ):
        commands.append(
            native._run(command, work=output, label=label, environment=environment, root=root)
        )
    executable = output / "runtime/PaperWorkShell.exe"
    _regular_file(executable, maximum=1024 * 1024)
    pe = inspect_launcher(executable.read_bytes())
    if verify_toolchain(compiler_root, expected) != closure or any(
        native.sha256_file(root / path) != digest for path, digest in sources.items()
    ):
        _fail("LAUNCHER_BUILD_INPUT_CHANGED")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "PWS-0802",
        "status": "built-unsigned-internal",
        "executable": executable.relative_to(root).as_posix(),
        "size_bytes": executable.stat().st_size,
        "sha256": native.sha256_file(executable),
        "source_hashes": sources,
        "toolchain_recipe": native.RECIPE_PATH.as_posix(),
        "toolchain_recipe_sha256": native.RECIPE_SHA256,
        "toolchain_archives": inputs,
        "compiler_root": compiler_root.relative_to(root).as_posix(),
        "toolchain_closure_sha256": closure,
        "toolchain_file_count": len(expected),
        "toolchain_verified_before_and_after": True,
        "commands": commands,
        "pe": pe,
        "qualification": QUALIFICATION,
    }
    receipt_path = output / "launcher-receipt.json"
    receipt["receipt_path"] = receipt_path.relative_to(root).as_posix()
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {**receipt, "receipt_sha256": native.sha256_file(receipt_path)}


def build_launcher(
    output_directory: Path, compiler_root: Path, *, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    """Return a controlled build's receipt plus its out-of-band receipt digest."""
    try:
        return _build_launcher_checked(output_directory, compiler_root, root=root)
    except LauncherBuildError:
        raise
    except Exception:
        raise LauncherBuildError("LAUNCHER_BUILD_FAILED") from None


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
        result[key] = value
    return result


def _verify_launcher_checked(
    executable: Path,
    receipt_path: Path,
    *,
    root: Path,
    expected_receipt_sha256: str | None,
) -> dict[str, Any]:
    if (
        type(expected_receipt_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_receipt_sha256) is None
    ):
        _fail("LAUNCHER_BUILD_RECEIPT_ANCHOR_REQUIRED")
    root = root.absolute()
    executable = executable.absolute()
    receipt_path = receipt_path.absolute()
    for path in (executable, receipt_path):
        if not path.is_relative_to(root / "output/pws0802"):
            _fail("LAUNCHER_BUILD_RECEIPT_SCOPE")
        _regular_file(path, maximum=1024 * 1024)
    if native.sha256_file(receipt_path) != expected_receipt_sha256:
        _fail("LAUNCHER_BUILD_RECEIPT_HASH")
    payload = json.loads(receipt_path.read_bytes(), object_pairs_hook=_closed_object)
    keys = {
        "schema_version",
        "task_id",
        "status",
        "executable",
        "size_bytes",
        "sha256",
        "source_hashes",
        "toolchain_recipe",
        "toolchain_recipe_sha256",
        "toolchain_archives",
        "compiler_root",
        "toolchain_closure_sha256",
        "toolchain_file_count",
        "toolchain_verified_before_and_after",
        "commands",
        "pe",
        "qualification",
        "receipt_path",
    }
    if (
        type(payload) is not dict
        or set(payload) != keys
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["task_id"] != "PWS-0802"
        or payload["status"] != "built-unsigned-internal"
        or payload["qualification"] != QUALIFICATION
        or payload["executable"] != executable.relative_to(root).as_posix()
        or payload["receipt_path"] != receipt_path.relative_to(root).as_posix()
        or payload["size_bytes"] != executable.stat().st_size
        or payload["sha256"] != native.sha256_file(executable)
        or payload["toolchain_recipe"] != native.RECIPE_PATH.as_posix()
        or payload["toolchain_recipe_sha256"] != native.RECIPE_SHA256
        or payload["toolchain_verified_before_and_after"] is not True
    ):
        _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
    source_hashes = {}
    for relative in (SOURCE, RESOURCE, TOOL):
        _regular_file(root / relative, maximum=MAX_SOURCE_BYTES)
        source_hashes[relative.as_posix()] = native.sha256_file(root / relative)
    if payload["source_hashes"] != source_hashes or payload["pe"] != inspect_launcher(
        executable.read_bytes()
    ):
        _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
    expected, inputs = expected_toolchain(root=root)
    compiler_root = root / str(payload["compiler_root"])
    if not compiler_root.absolute().is_relative_to(root / "output") or ".." in compiler_root.parts:
        _fail("LAUNCHER_BUILD_TOOLCHAIN_SCOPE")
    closure = verify_toolchain(compiler_root, expected)
    if (
        payload["toolchain_archives"] != inputs
        or payload["toolchain_file_count"] != len(expected)
        or payload["toolchain_closure_sha256"] != closure
    ):
        _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
    work = receipt_path.parent
    commands = payload["commands"]
    if type(commands) is not list or len(commands) != 2:
        _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
    windows = _windows_directory()
    for command, label, argv in zip(
        commands,
        ("resource", "compile"),
        build_commands(work, compiler_root, root=root),
        strict=True,
    ):
        log = work / (label + ".txt")
        _regular_file(log, maximum=native.MAX_LOG_BYTES)
        if (
            type(command) is not dict
            or set(command) != {"label", "argv", "returncode", "log", "log_sha256"}
            or command["label"] != label
            or type(command["returncode"]) is not int
            or command["returncode"] != 0
            or command["argv"]
            != [native._sanitized(arg, root=root, system=windows) for arg in argv]
            or command["log"] != log.relative_to(root).as_posix()
            or command["log_sha256"] != native.sha256_file(log)
        ):
            _fail("LAUNCHER_BUILD_RECEIPT_INVALID")
    return {**payload, "receipt_sha256": expected_receipt_sha256}


def verify_launcher(
    executable: Path,
    receipt_path: Path,
    *,
    root: Path = PROJECT_ROOT,
    expected_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Reverify a controlled build; caller must independently anchor its receipt.

    Merely hashing an untrusted receipt and passing that same hash back is not
    provenance. Use the digest returned by the controlled build, or an already
    trusted review/signing record. Hash/source/PE inspection alone cannot prove
    that an arbitrary native program was compiled from the claimed source.
    """
    try:
        return _verify_launcher_checked(
            executable, receipt_path, root=root, expected_receipt_sha256=expected_receipt_sha256
        )
    except LauncherBuildError:
        raise
    except Exception:
        raise LauncherBuildError("LAUNCHER_BUILD_RECEIPT_INVALID") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_launcher(args.output, args.compiler_root)
    except LauncherBuildError as error:
        print(f"ERROR [{error}] Native launcher build did not complete.", file=sys.stderr)
        return 1
    except Exception:
        print(
            "ERROR [LAUNCHER_BUILD_FAILED] Native launcher build did not complete.", file=sys.stderr
        )
        return 1
    print("Native launcher built and inspected: " + str(result["sha256"]))
    print("Controlled build receipt SHA-256: " + str(result["receipt_sha256"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
