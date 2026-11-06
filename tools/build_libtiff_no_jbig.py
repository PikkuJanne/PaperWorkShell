"""Build the reviewed dynamic libtiff subset without the optional JBIG codec.

This is an offline release-build tool, never an application runtime dependency.
It consumes exact, already acquired archives; it does not discover libraries from
the machine, install software, or alter the existing OCR runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn, cast

from tools.audit_tesseract_redistribution import pe_imports

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
RECIPE_PATH: Final = Path("packaging/libtiff-no-jbig-build-v1.json")
RECIPE_SHA256: Final = "af49ae37ab7b0f4e8c9db71a6ae5b46002ef30cf0b4d9beb9de8513ace873550"
REQUIRED_SIGNER: Final = "5F944B027F7FE2091985AA2EFA11531AA0AA7F57"
MAX_MEMBERS: Final = 12_000
MAX_MEMBER_BYTES: Final = 200_000_000
MAX_EXPANDED_BYTES: Final = 600_000_000
MAX_LOG_BYTES: Final = 1_048_576
_RESERVED: Final = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


class LibtiffBuildError(ValueError):
    """Closed build diagnostic without paths or raw upstream output."""


def _fail(code: str) -> NoReturn:
    raise LibtiffBuildError(code)


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1_048_576):
            value.update(block)
    return value.hexdigest()


def reject_redirected_path(path: Path) -> None:
    """Reject symlinks/junctions at all existing ancestors before file access."""
    for candidate in (*path.absolute().parents, path.absolute()):
        if not candidate.exists() and not candidate.is_symlink():
            continue
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("LIBTIFF_BUILD_UNSAFE_PATH")


def safe_member_name(name: str) -> PurePosixPath:
    """Use the Windows target's path rules even when validating elsewhere."""
    trimmed = name.rstrip("/")
    parts = trimmed.split("/")
    if (
        not trimmed
        or len(trimmed) > 512
        or "\\" in trimmed
        or ":" in trimmed
        or trimmed.startswith("/")
        or any(
            not part
            or part in {".", ".."}
            or part.endswith((" ", "."))
            or _RESERVED.fullmatch(part)
            or any(ord(char) < 32 for char in part)
            for part in parts
        )
    ):
        _fail("LIBTIFF_BUILD_UNSAFE_ARCHIVE")
    return PurePosixPath(trimmed)


def verify_artifact(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    reject_redirected_path(path)
    if (
        not path.is_file()
        or path.stat().st_size != expected_size
        or sha256_file(path) != expected_sha256
    ):
        _fail("LIBTIFF_BUILD_HASH_MISMATCH")


def _write_new_or_identical(path: Path, data: bytes) -> None:
    reject_redirected_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            _fail("LIBTIFF_BUILD_EXISTING_FILE")
    else:
        with path.open("xb") as target:
            target.write(data)


def extract_tar(path: Path, destination: Path, *, prefix: str | None = None) -> int:
    """Extract bounded regular files; materialize verified intra-archive hardlinks.

    No archive link is installed as a filesystem link. Package metadata and
    install scripts outside the explicitly selected prefix remain unexecuted.
    Callers authenticate the enclosing artifact before invoking this function.
    """
    reject_redirected_path(path)
    reject_redirected_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode="r:*") as archive:
        members: list[tarfile.TarInfo] = []
        seen: set[str] = set()
        expanded = 0
        for member in archive:
            relative = safe_member_name(member.name)
            key = relative.as_posix().casefold()
            if key in seen:
                _fail("LIBTIFF_BUILD_ARCHIVE_COLLISION")
            seen.add(key)
            if not (member.isfile() or member.isdir() or member.islnk()):
                _fail("LIBTIFF_BUILD_UNSAFE_ARCHIVE")
            if not 0 <= member.size <= MAX_MEMBER_BYTES:
                _fail("LIBTIFF_BUILD_ARCHIVE_LIMIT")
            expanded += member.size
            if len(members) >= MAX_MEMBERS or expanded > MAX_EXPANDED_BYTES:
                _fail("LIBTIFF_BUILD_ARCHIVE_LIMIT")
            members.append(member)
        regular = {member.name: member for member in members if member.isfile()}
        selected = [
            member for member in members if prefix is None or member.name.startswith(prefix)
        ]
        written = 0
        for member in selected:
            relative = safe_member_name(member.name)
            target = destination.joinpath(*relative.parts)
            reject_redirected_path(target)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source_member = member
            if member.islnk():
                link = safe_member_name(member.linkname)
                linked_member = regular.get(link.as_posix())
                if linked_member is None or (
                    prefix is not None and not member.linkname.startswith(prefix)
                ):
                    _fail("LIBTIFF_BUILD_UNSAFE_ARCHIVE")
                source_member = linked_member
            source = archive.extractfile(source_member)
            if source is None:
                _fail("LIBTIFF_BUILD_UNSAFE_ARCHIVE")
            with source:
                data = source.read(MAX_MEMBER_BYTES + 1)
            if len(data) != source_member.size:
                _fail("LIBTIFF_BUILD_ARCHIVE_LIMIT")
            _write_new_or_identical(target, data)
            written += 1
    return written


def extract_zip(path: Path, destination: Path) -> int:
    reject_redirected_path(path)
    reject_redirected_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (
            len(entries) > MAX_MEMBERS
            or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES
        ):
            _fail("LIBTIFF_BUILD_ARCHIVE_LIMIT")
        seen: set[str] = set()
        for entry in entries:
            relative = safe_member_name(entry.filename)
            key = relative.as_posix().casefold()
            if key in seen:
                _fail("LIBTIFF_BUILD_ARCHIVE_COLLISION")
            seen.add(key)
            if stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > MAX_MEMBER_BYTES:
                _fail("LIBTIFF_BUILD_UNSAFE_ARCHIVE")
            target = destination.joinpath(*relative.parts)
            reject_redirected_path(target)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                _write_new_or_identical(target, archive.read(entry))
        return len(entries)


def verify_signature(
    executable: Path,
    home: Path,
    signature: Path,
    artifact: Path,
    *,
    root: Path = PROJECT_ROOT,
    keyring_name: str = "pubring.kbx",
) -> None:
    """Use a supplied local public keyring; never retrieve a key or invoke a shell."""
    for path in (executable, home, signature, artifact):
        reject_redirected_path(path)
    args = [
        str(executable),
        "--batch",
        "--no-auto-key-retrieve",
        "--no-auto-check-trustdb",
        "--homedir",
        home.relative_to(root).as_posix(),
        "--no-default-keyring",
        "--keyring",
        keyring_name,
        "--status-fd",
        "1",
        "--verify",
        signature.relative_to(root).as_posix(),
        artifact.relative_to(root).as_posix(),
    ]
    process = subprocess.run(args, cwd=root, capture_output=True, timeout=60, check=False)
    expected = b"[GNUPG:] VALIDSIG " + REQUIRED_SIGNER.encode("ascii") + b" "
    if process.returncode or expected not in process.stdout:
        _fail("LIBTIFF_BUILD_SIGNATURE_INVALID")


def load_recipe(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    path = root / RECIPE_PATH
    verify_artifact(path, expected_size=path.stat().st_size, expected_sha256=RECIPE_SHA256)
    value = cast(dict[str, Any], json.loads(path.read_bytes()))
    if (
        value.get("schema_version") != 1
        or value.get("recipe_id") != "paperworkshell-libtiff-no-jbig-windows-ucrt64-v1"
    ):
        _fail("LIBTIFF_BUILD_RECIPE_INVALID")
    return value


def owned_output_directory(path: Path, *, root: Path = PROJECT_ROOT) -> Path:
    """Keep every generated file in the repository's explicit ignored build area."""
    reject_redirected_path(path)
    resolved = path.resolve()
    allowed = (root / "output/pws0801/native-rebuild").resolve()
    if not resolved.is_relative_to(allowed) or resolved == allowed:
        _fail("LIBTIFF_BUILD_OUTPUT_SCOPE")
    return resolved


def build_commands(work: Path) -> tuple[list[str], list[str], list[str]]:
    """Explicit paths and native argument arrays; no ambient executable discovery."""
    compiler = work / "toolchain/ucrt64"
    cmake = work / "tool-cmake/cmake-4.4.5-windows-x86_64/bin/cmake.exe"
    ninja = work / "tool-ninja/ninja.exe"
    source = work / "source/tiff-4.7.2"
    deps = work / "dependencies/ucrt64"
    build = work / "build"
    configure = [
        str(cmake),
        "-S",
        str(source),
        "-B",
        str(build),
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-Dtiff-static=OFF",
        "-Dtiff-tools=OFF",
        "-Dtiff-contrib=OFF",
        "-Dtiff-docs=OFF",
        "-Dtiff-tests=ON",
        "-Dtiff-install=OFF",
        "-Dtiff-cxx=OFF",
        "-Dwin32-io=ON",
        "-Djbig=OFF",
        "-Djpeg=ON",
        "-Dzlib=ON",
        "-Dlibdeflate=ON",
        "-Dlerc=ON",
        "-Dlzma=ON",
        "-Dwebp=ON",
        "-Dzstd=ON",
        "-DCMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH=OFF",
        "-DCMAKE_FIND_USE_CMAKE_SYSTEM_PATH=OFF",
        "-DCMAKE_MAKE_PROGRAM=" + ninja.as_posix(),
        "-DCMAKE_C_COMPILER=" + (compiler / "bin/gcc.exe").as_posix(),
        "-DCMAKE_CXX_COMPILER=" + (compiler / "bin/g++.exe").as_posix(),
        "-DCMAKE_RC_COMPILER=" + (compiler / "bin/windres.exe").as_posix(),
        "-DCMAKE_PREFIX_PATH=" + deps.as_posix(),
        "-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG -fno-strict-aliasing -ffile-prefix-map="
        + work.as_posix()
        + "=.",
        "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,--no-insert-timestamp",
    ]
    for variable, filename in (
        ("ZLIB_LIBRARY", "libz.dll.a"),
        ("JPEG_LIBRARY", "libjpeg.dll.a"),
        ("LERC_LIBRARY", "libLerc.dll.a"),
        ("LIBLZMA_LIBRARY", "liblzma.dll.a"),
        ("WebP_LIBRARY", "libwebp.dll.a"),
        ("ZSTD_LIBRARY", "libzstd.dll.a"),
        ("Deflate_LIBRARY", "libdeflate.dll.a"),
    ):
        configure.append("-D" + variable + "=" + (deps / "lib" / filename).as_posix())
    for variable in (
        "ZLIB_INCLUDE_DIR",
        "JPEG_INCLUDE_DIR",
        "LERC_INCLUDE_DIR",
        "LIBLZMA_INCLUDE_DIR",
        "WebP_INCLUDE_DIR",
        "ZSTD_INCLUDE_DIR",
        "Deflate_INCLUDE_DIR",
    ):
        configure.append("-D" + variable + "=" + (deps / "include").as_posix())
    compile_command = [str(cmake), "--build", str(build), "--parallel", "2"]
    ctest = [
        str(cmake.with_name("ctest.exe")),
        "--test-dir",
        str(build),
        "--output-on-failure",
        "--parallel",
        "2",
    ]
    return configure, compile_command, ctest


def _sanitized(value: str, *, root: Path, system: Path) -> str:
    for path, replacement in ((root, "[REPOSITORY]"), (system, "[WINDOWS]")):
        value = value.replace(str(path), replacement).replace(path.as_posix(), replacement)
    return value


def _run(
    args: list[str],
    *,
    work: Path,
    label: str,
    environment: dict[str, str],
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Bound a trusted compiler invocation and terminate its owned process tree on failure."""
    process = subprocess.Popen(
        args,
        cwd=work,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
    )
    try:
        stdout, _ = process.communicate(timeout=240)
    except subprocess.TimeoutExpired, KeyboardInterrupt:
        terminator = Path(environment["SystemRoot"]) / "System32/taskkill.exe"
        subprocess.run(
            [str(terminator), "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        process.kill()
        process.communicate(timeout=30)
        _fail("LIBTIFF_BUILD_CANCELLED_OR_TIMED_OUT")
    if len(stdout) > MAX_LOG_BYTES:
        _fail("LIBTIFF_BUILD_LOG_LIMIT")
    system = Path(environment["SystemRoot"])
    text = _sanitized(stdout.decode("utf-8", errors="replace"), root=root, system=system)
    path = work / (label + ".txt")
    _write_new_or_identical(path, text.encode("utf-8"))
    result = {
        "label": label,
        "argv": [_sanitized(arg, root=root, system=system) for arg in args],
        "returncode": process.returncode,
        "log": path.relative_to(root).as_posix(),
        "log_sha256": sha256_file(path),
    }
    if process.returncode:
        _fail("LIBTIFF_BUILD_COMMAND_FAILED")
    return result


def exported_symbols(output: str) -> tuple[str, ...]:
    """Parse the pinned binutils 2.47 name/ordinal table, not free-form diagnostics."""
    marker = "[Ordinal/Name Pointer] Table"
    if output.count(marker) != 1 or len(output) > MAX_LOG_BYTES:
        _fail("LIBTIFF_BUILD_EXPORTS_INVALID")
    section = output.split(marker, 1)[1].split("The Function Table", 1)[0]
    names = re.findall(
        r"^\s*\[\s*\d+\]\s+\+base\[\s*\d+\]\s+[0-9a-f]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*$",
        section,
        re.MULTILINE,
    )
    if not names or len(set(names)) != len(names):
        _fail("LIBTIFF_BUILD_EXPORTS_INVALID")
    return tuple(sorted(names))


def build(
    artifacts: Path,
    codec_artifacts: Path,
    output: Path,
    gpg_executable: Path,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Rebuild from authenticated inputs and prove the exact configured native result."""
    if os.name != "nt":
        _fail("LIBTIFF_BUILD_WINDOWS_REQUIRED")
    helper_source = Path(__file__).resolve()
    helper_sha256 = sha256_file(helper_source)
    recipe = load_recipe(root)
    work = owned_output_directory(output, root=root)
    if work.exists():
        _fail("LIBTIFF_BUILD_OUTPUT_EXISTS")
    for directory in (artifacts, codec_artifacts):
        reject_redirected_path(directory)
    all_records = [(item, artifacts) for item in recipe["build_inputs"]]
    all_records += [(item, codec_artifacts) for item in recipe["codec_inputs"]]
    all_records.append((recipe["source"], artifacts))
    all_records.append((recipe["keyring"], codec_artifacts))
    # Complete hash validation precedes extraction and execution of any new tool.
    for item, directory in all_records:
        verify_artifact(
            directory / item["filename"],
            expected_size=item["size_bytes"],
            expected_sha256=item["sha256"],
        )
        if "signature_sha256" in item:
            verify_artifact(
                directory / (item["filename"] + ".sig"),
                expected_size=item["signature_size_bytes"],
                expected_sha256=item["signature_sha256"],
            )
    work.mkdir(parents=True)
    keyring = recipe["keyring"]
    keyring_stage = work / "keyring"
    extract_tar(codec_artifacts / keyring["filename"], keyring_stage)
    public_key = keyring_stage / keyring["public_key_path"]
    verify_artifact(
        public_key,
        expected_size=keyring["public_key_size_bytes"],
        expected_sha256=keyring["public_key_sha256"],
    )
    home = work / "gpg-home"
    home.mkdir()
    dearmor = subprocess.run(
        [
            str(gpg_executable),
            "--batch",
            "--homedir",
            home.relative_to(root).as_posix(),
            "--dearmor",
            "--output",
            (home / "pubring.gpg").relative_to(root).as_posix(),
            public_key.relative_to(root).as_posix(),
        ],
        cwd=root,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if dearmor.returncode:
        _fail("LIBTIFF_BUILD_KEYRING_INVALID")
    for item, directory in all_records:
        if "signature_sha256" in item:
            path = directory / item["filename"]
            verify_signature(
                gpg_executable,
                home,
                path.with_name(path.name + ".sig"),
                path,
                root=root,
                keyring_name="pubring.gpg",
            )
    for item in recipe["build_inputs"]:
        path = artifacts / item["filename"]
        if item["kind"] == "msys2-toolchain":
            extract_tar(path, work / "toolchain", prefix="ucrt64/")
        else:
            extract_zip(path, work / ("tool-" + item["component_id"]))
    for item in recipe["codec_inputs"]:
        extract_tar(codec_artifacts / item["filename"], work / "dependencies", prefix="ucrt64/")
    extract_tar(artifacts / recipe["source"]["filename"], work / "source")
    system = Path(os.environ["SystemRoot"])
    temp = work / "temp"
    temp.mkdir()
    environment = {
        "SystemRoot": str(system),
        "WINDIR": str(system),
        "PATH": os.pathsep.join(
            str(path)
            for path in (
                work / "build/libtiff",
                work / "dependencies/ucrt64/bin",
                work / "toolchain/ucrt64/bin",
                work / "tool-cmake/cmake-4.4.5-windows-x86_64/bin",
                work / "tool-ninja",
                system / "System32",
            )
        ),
        "TEMP": str(temp),
        "TMP": str(temp),
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": "1786579200",
    }
    commands = []
    for args, label in zip(build_commands(work), ("configure", "compile", "ctest"), strict=True):
        commands.append(_run(args, work=work, label=label, environment=environment, root=root))
    final = work / "rebuilt/libtiff-6.dll"
    final.parent.mkdir()
    shutil.copyfile(work / "build/libtiff/libtiff-6.dll", final)
    strip = work / "toolchain/ucrt64/bin/strip.exe"
    commands.append(
        _run(
            [str(strip), "--strip-unneeded", str(final)],
            work=work,
            label="strip",
            environment=environment,
            root=root,
        )
    )
    expected = recipe["expected_output"]
    verify_artifact(final, expected_size=expected["size_bytes"], expected_sha256=expected["sha256"])
    normal, delayed = pe_imports(final.read_bytes())
    if list(normal) != expected["normal_imports"] or list(delayed) != expected["delay_imports"]:
        _fail("LIBTIFF_BUILD_NATIVE_CLOSURE_CHANGED")
    dump = _run(
        [str(work / "toolchain/ucrt64/bin/objdump.exe"), "-p", str(final)],
        work=work,
        label="exports",
        environment=environment,
        root=root,
    )
    commands.append(dump)
    names = exported_symbols((work / "exports.txt").read_text(encoding="utf-8"))
    if list(names) != expected["exports"]:
        _fail("LIBTIFF_BUILD_EXPORTS_CHANGED")
    if sha256_file(helper_source) != helper_sha256:
        _fail("LIBTIFF_BUILD_SOURCE_CHANGED")
    receipt = {
        "schema_version": 1,
        "task_id": "PWS-0801",
        "status": "pass",
        "recipe": RECIPE_PATH.as_posix(),
        "recipe_sha256": RECIPE_SHA256,
        "helper": "tools/build_libtiff_no_jbig.py",
        "helper_sha256": helper_sha256,
        "helper_unchanged_during_build": True,
        "commands": commands,
        "artifact": final.relative_to(root).as_posix(),
        "size_bytes": final.stat().st_size,
        "sha256": sha256_file(final),
        "normal_imports": normal,
        "delay_imports": delayed,
        "export_count": len(names),
        "scope": "Offline release build; not a product runtime, installer, or hardware claim.",
    }
    _write_new_or_identical(work / "receipt.json", (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--codec-artifacts", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--gpg-executable", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build(
            args.artifacts.resolve(),
            args.codec_artifacts.resolve(),
            args.output_directory.resolve(),
            args.gpg_executable.resolve(),
        )
    except LibtiffBuildError as error:
        print(str(error) + ": The reviewed offline native build did not complete.", file=sys.stderr)
        return 1
    except OSError, ValueError, tarfile.TarError, zipfile.BadZipFile:
        print(
            "LIBTIFF_BUILD_FAILED: The reviewed offline native build did not complete.",
            file=sys.stderr,
        )
        return 1
    print("Native libtiff build verified: " + cast(str, receipt["sha256"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
