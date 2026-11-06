"""Hash-addressed admission for the packaged Windows Tesseract closure.

The dependency inventory is evidence for maintainers.  This module is the
runtime enforcement point: only the byte-identical reviewed manifest and the
exact file closure it describes may become worker launch authority.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Never

from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    OCR_LANGUAGE_SET_DEFINITIONS,
)

TESSERACT_RUNTIME_MANIFEST_SHA256: Final = (
    "11358f37de440ba738b4c834f8841647bbb25daccf4eba12d205e4a4d6073ba6"
)
TESSERACT_RUNTIME_FILE_COUNT: Final = 38
TESSERACT_LANGUAGE_PACK_CATALOG_SHA256: Final = (
    "7b77404b22bdc7cec72c6af918e7bc4c700399c0dbcb650e7e4562fe1d94ce45"
)
TESSERACT_RUNTIME_ENTRYPOINT: Final = PurePosixPath("bin/tesseract.exe")
TESSERACT_RUNTIME_TESSDATA_DIRECTORY: Final = PurePosixPath("share/tessdata")
_TESSERACT_RUNTIME_MANIFEST_NAME: Final = "tesseract-runtime-closure-v2.json"
_TESSERACT_LANGUAGE_PACK_CATALOG_NAME: Final = "tesseract-language-pack-catalog-v1.json"
_MAX_MANIFEST_BYTES: Final = 1_048_576
_MAX_LANGUAGE_PACKS: Final = 32
_MAX_RUNTIME_DEPTH: Final = 16
_MAX_RUNTIME_ENTRIES: Final = 160
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_FILE_ATTRIBUTE_OFFLINE: Final = 0x0000_1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN: Final = 0x0004_0000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: Final = 0x0040_0000
_UNSUPPORTED_RUNTIME_ATTRIBUTES: Final = (
    _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
_FILE_PERSISTENT_ACLS: Final = 0x0000_0008
_INVALID_FILE_ATTRIBUTES: Final = 0xFFFF_FFFF
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_DRIVE_FIXED: Final = 3
_ERROR_SUCCESS: Final = 0
_ERROR_FILE_NOT_FOUND: Final = 2
_ERROR_PATH_NOT_FOUND: Final = 3
_ERROR_HANDLE_EOF: Final = 38
_PINNED_MSYS2_SIGNER_FINGERPRINT: Final = "5F944B027F7FE2091985AA2EFA11531AA0AA7F57"
_UPSTREAM_SIGNATURE_REQUIREMENT: Final = (
    "The detached MSYS2 package signature must validate against the pinned "
    "reviewed MSYS2 key before extraction."
)
_SIGNATURE_EVIDENCE_STATUS: Final = "required-upstream-not-reverified-for-pws-0205"


@dataclass(slots=True)
class TesseractRuntimeAdmissionError(RuntimeError):
    """Path-free reason that an OCR runtime was not admitted."""

    context: str

    def __str__(self) -> str:
        return f"Tesseract runtime admission failed at {self.context}."


@dataclass(frozen=True, slots=True)
class TesseractRuntimeAdmission:
    """Authenticated fixed paths safe to pass to the Windows launch boundary."""

    root: Path
    executable: Path
    tessdata_root: Path
    resources: tuple[Path, ...]
    installed_language_codes: tuple[str, ...] = ("eng",)
    available_language_set_ids: tuple[str, ...] = (DEFAULT_OCR_LANGUAGE_SET_ID,)


@dataclass(frozen=True, slots=True)
class _ManifestFile:
    path: PurePosixPath
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _LanguagePack:
    language_code: str
    file: _ManifestFile


class _Win32FindStreamData(ctypes.Structure):
    _fields_ = [
        ("stream_size", ctypes.c_longlong),
        ("stream_name", wintypes.WCHAR * 296),
    ]


def packaged_tesseract_runtime_manifest() -> Path:
    """Return the code-owned manifest location; callers cannot replace it."""

    return Path(__file__).absolute().with_name(_TESSERACT_RUNTIME_MANIFEST_NAME)


def packaged_tesseract_language_pack_catalog() -> Path:
    """Return the code-owned optional-language catalogue location."""

    return Path(__file__).absolute().with_name(_TESSERACT_LANGUAGE_PACK_CATALOG_NAME)


def tesseract_runtime_authority_id(executable: Path, tessdata_root: Path) -> str:
    """Return a path-free identity binding one worker config to one local runtime root."""

    if not isinstance(executable, Path) or not isinstance(tessdata_root, Path):
        _reject("runtime_authority")
    try:
        normalized = (
            os.path.normcase(os.path.abspath(os.fspath(executable))),
            os.path.normcase(os.path.abspath(os.fspath(tessdata_root))),
        )
        payload = (
            "paperworkshell-tesseract-runtime-authority-v1\0" + "\0".join(normalized)
        ).encode("utf-8")
    except OSError, TypeError, UnicodeError, ValueError:
        _reject("runtime_authority")
    return hashlib.sha256(payload).hexdigest()


def admit_packaged_tesseract_runtime(
    executable: Path,
    tessdata_root: Path,
) -> TesseractRuntimeAdmission:
    """Admit only the closure pinned by the packaged, reviewed manifest."""

    manifest_bytes = _read_packaged_resource(
        packaged_tesseract_runtime_manifest(),
        "manifest_resource",
    )
    language_catalog_bytes = _read_packaged_resource(
        packaged_tesseract_language_pack_catalog(),
        "language_catalog_resource",
    )
    return admit_tesseract_runtime(
        executable,
        tessdata_root,
        manifest_bytes=manifest_bytes,
        expected_manifest_sha256=TESSERACT_RUNTIME_MANIFEST_SHA256,
        language_catalog_bytes=language_catalog_bytes,
        expected_language_catalog_sha256=TESSERACT_LANGUAGE_PACK_CATALOG_SHA256,
    )


def admit_tesseract_runtime(
    executable: Path,
    tessdata_root: Path,
    *,
    manifest_bytes: bytes,
    expected_manifest_sha256: str,
    language_catalog_bytes: bytes | None = None,
    expected_language_catalog_sha256: str | None = None,
) -> TesseractRuntimeAdmission:
    """Authenticate one closure; the injectable bytes are a pure test seam."""

    if (
        not isinstance(executable, Path)
        or not isinstance(tessdata_root, Path)
        or type(manifest_bytes) is not bytes
        or not manifest_bytes
        or len(manifest_bytes) > _MAX_MANIFEST_BYTES
        or type(expected_manifest_sha256) is not str
        or len(expected_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_manifest_sha256)
        or (language_catalog_bytes is None) != (expected_language_catalog_sha256 is None)
    ):
        _reject("configuration")
    root = preflight_tesseract_runtime_paths(executable, tessdata_root)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        _reject("manifest_hash")

    files = _parse_manifest(manifest_bytes)
    language_packs: tuple[_LanguagePack, ...] = ()
    if language_catalog_bytes is not None:
        if (
            type(language_catalog_bytes) is not bytes
            or not language_catalog_bytes
            or len(language_catalog_bytes) > _MAX_MANIFEST_BYTES
            or type(expected_language_catalog_sha256) is not str
            or len(expected_language_catalog_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_language_catalog_sha256
            )
        ):
            _reject("configuration")
        if hashlib.sha256(language_catalog_bytes).hexdigest() != expected_language_catalog_sha256:
            _reject("language_catalog_hash")
        language_packs = _parse_language_catalog(language_catalog_bytes)
    expected_executable = root.joinpath(*TESSERACT_RUNTIME_ENTRYPOINT.parts)
    expected_tessdata = root.joinpath(*TESSERACT_RUNTIME_TESSDATA_DIRECTORY.parts)
    if _normalized(executable) != _normalized(expected_executable) or _normalized(
        tessdata_root
    ) != _normalized(expected_tessdata):
        _reject("layout")

    expected_files = {entry.path.as_posix(): entry for entry in files}
    catalog_files = {pack.file.path.as_posix(): pack for pack in language_packs}
    if set(expected_files).intersection(catalog_files):
        _reject("language_catalog_schema")

    base_directories = _expected_directories(files)
    catalog_directories = _expected_directories(tuple(pack.file for pack in language_packs))
    observed_files, observed_directories = _inventory_runtime(root)
    _verify_observed_case(
        observed_files,
        set(expected_files).union(catalog_files),
    )
    _verify_observed_case(
        observed_directories,
        base_directories.union(catalog_directories),
    )
    present_catalog_paths = observed_files.intersection(catalog_files)
    present_catalog_files = tuple(catalog_files[path] for path in sorted(present_catalog_paths))
    expected_directories = base_directories.union(
        _expected_directories(tuple(pack.file for pack in present_catalog_files))
    )
    if (
        observed_files - expected_files.keys() - catalog_files.keys()
        or observed_directories - expected_directories
    ):
        _reject("unlisted")
    if expected_files.keys() - observed_files or base_directories - observed_directories:
        _reject("missing")

    for entry in files:
        _verify_file(root.joinpath(*entry.path.parts), entry)
    for pack in present_catalog_files:
        _verify_file(root.joinpath(*pack.file.path.parts), pack.file)

    directories = tuple(
        root.joinpath(*PurePosixPath(relative).parts)
        for relative in sorted(expected_directories, key=lambda item: (item.count("/"), item))
    )
    resources = (
        root,
        *directories,
        *(root.joinpath(*entry.path.parts) for entry in files),
        *(root.joinpath(*pack.file.path.parts) for pack in present_catalog_files),
    )
    installed_language_codes = tuple(
        sorted({"eng", *(pack.language_code for pack in present_catalog_files)})
    )
    installed = frozenset(installed_language_codes)
    available_language_set_ids = tuple(
        sorted(
            definition.language_set_id
            for definition in OCR_LANGUAGE_SET_DEFINITIONS
            if frozenset(definition.language_codes).issubset(installed)
        )
    )
    return TesseractRuntimeAdmission(
        root=root,
        executable=expected_executable,
        tessdata_root=expected_tessdata,
        resources=resources,
        installed_language_codes=installed_language_codes,
        available_language_set_ids=available_language_set_ids,
    )


def preflight_tesseract_runtime_paths(executable: Path, tessdata_root: Path) -> Path:
    """Authenticate one paired Windows-visible fixed-volume runtime before content I/O."""

    if not isinstance(executable, Path) or not isinstance(tessdata_root, Path):
        _reject("configuration")
    paths = (executable, tessdata_root)
    drives = {_lexical_runtime_drive(path) for path in paths}
    root = executable.parent.parent
    expected_executable = root.joinpath(*TESSERACT_RUNTIME_ENTRYPOINT.parts)
    expected_tessdata = root.joinpath(*TESSERACT_RUNTIME_TESSDATA_DIRECTORY.parts)
    if _normalized(executable) != _normalized(expected_executable) or _normalized(
        tessdata_root
    ) != _normalized(expected_tessdata):
        _reject("layout")
    for drive in sorted(drives, key=str.casefold):
        _require_supported_local_volume(drive)
    for path in paths:
        _require_safe_runtime_path_attributes(path)
    return root


def _lexical_runtime_drive(path: Path) -> str:
    rendered = os.fspath(path)
    drive, tail = os.path.splitdrive(rendered)
    if (
        not path.is_absolute()
        or "\x00" in rendered
        or rendered.startswith("\\\\")
        or rendered.startswith("\\??\\")
        or len(drive) != 2
        or not drive[0].isalpha()
        or drive[1] != ":"
        or not tail.startswith(("\\", "/"))
        or ":" in tail
        or any(part in {".", ".."} or part.endswith((" ", ".")) for part in path.parts[1:])
    ):
        _reject("location")
    return drive.upper()


def _require_supported_local_volume(drive: str) -> None:
    root = f"{drive}\\"
    if _windows_drive_type(root) != _DRIVE_FIXED:
        _reject("location")
    drive_targets = _windows_dos_device_targets(drive)
    if len(drive_targets) != 1 or not _physical_volume_device(drive_targets[0]):
        _reject("location")
    volume_name = _windows_volume_name(root)
    if (
        not volume_name.startswith("\\\\?\\Volume{")
        or not volume_name.endswith("}\\")
        or "\x00" in volume_name
    ):
        _reject("location")
    volume_targets = _windows_dos_device_targets(volume_name[4:-1])
    if len(volume_targets) != 1 or volume_targets[0].casefold() != drive_targets[0].casefold():
        _reject("location")
    registered_paths = _windows_volume_paths(volume_name)
    if root.casefold() not in {path.casefold() for path in registered_paths}:
        _reject("location")
    flags, filesystem = _windows_volume_information(root)
    if not flags & _FILE_PERSISTENT_ACLS or filesystem.casefold() not in {"ntfs", "refs"}:
        _reject("location")


def _physical_volume_device(value: str) -> bool:
    prefix = "\\device\\harddiskvolume"
    normalized = value.casefold()
    return normalized.startswith(prefix) and normalized[len(prefix) :].isdigit()


def _require_safe_runtime_path_attributes(path: Path) -> None:
    normalized = os.path.normcase(os.path.abspath(os.fspath(path)))
    drive, _tail = os.path.splitdrive(normalized)
    root = Path(f"{drive}\\")
    try:
        relative = Path(normalized).relative_to(root)
    except ValueError:
        _reject("location")
    current = root
    for component in (None, *relative.parts):
        if component is not None:
            current /= component
        attributes = _windows_file_attributes(current)
        if attributes == _INVALID_FILE_ATTRIBUTES:
            _reject("location")
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _reject("reparse")
        if attributes & _UNSUPPORTED_RUNTIME_ATTRIBUTES:
            _reject("location")


def _windows_kernel32() -> Any:
    if os.name != "nt":
        _reject("location")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_drive_type(root: str) -> int:
    get_drive_type = _windows_kernel32().GetDriveTypeW
    get_drive_type.argtypes = (wintypes.LPCWSTR,)
    get_drive_type.restype = wintypes.UINT
    return int(get_drive_type(root))


def _windows_dos_device_targets(name: str) -> tuple[str, ...]:
    query = _windows_kernel32().QueryDosDeviceW
    query.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    query.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(query(name, buffer, len(buffer)))
    if length <= 0:
        return ()
    rendered = "".join(buffer[:length])
    return tuple(part for part in rendered.split("\x00") if part)


def _windows_volume_name(root: str) -> str:
    get_volume_name = _windows_kernel32().GetVolumeNameForVolumeMountPointW
    get_volume_name.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_volume_name.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(64)
    if not get_volume_name(root, buffer, len(buffer)):
        return ""
    return buffer.value


def _windows_volume_information(root: str) -> tuple[int, str]:
    get_volume_information = _windows_kernel32().GetVolumeInformationW
    get_volume_information.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_volume_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    filesystem = ctypes.create_unicode_buffer(32)
    if not get_volume_information(
        root,
        None,
        0,
        None,
        None,
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    ):
        _reject("location")
    return int(flags.value), filesystem.value


def _windows_volume_paths(volume_name: str) -> tuple[str, ...]:
    get_volume_paths = _windows_kernel32().GetVolumePathNamesForVolumeNameW
    get_volume_paths.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_volume_paths.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32_768)
    required = wintypes.DWORD()
    if not get_volume_paths(
        volume_name,
        buffer,
        len(buffer),
        ctypes.byref(required),
    ):
        return ()
    length = min(int(required.value), len(buffer))
    rendered = "".join(buffer[:length])
    return tuple(part for part in rendered.split("\x00") if part)


def _windows_file_attributes(path: Path) -> int:
    get_attributes = _windows_kernel32().GetFileAttributesW
    get_attributes.argtypes = (wintypes.LPCWSTR,)
    get_attributes.restype = wintypes.DWORD
    return int(get_attributes(os.fspath(path)))


def _require_only_unnamed_data_stream(
    path: Path,
    *,
    is_directory: bool,
    expected_size: int = 0,
) -> None:
    find_first_stream = _windows_kernel32().FindFirstStreamW
    find_first_stream.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(_Win32FindStreamData),
        wintypes.DWORD,
    )
    find_first_stream.restype = wintypes.HANDLE
    find_next_stream = _windows_kernel32().FindNextStreamW
    find_next_stream.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_Win32FindStreamData),
    )
    find_next_stream.restype = wintypes.BOOL
    stream = _Win32FindStreamData()
    ctypes.set_last_error(_ERROR_SUCCESS)
    raw_handle = find_first_stream(os.fspath(path), 0, ctypes.byref(stream), 0)
    value = int(raw_handle) if raw_handle else 0
    if not value or value == _INVALID_HANDLE_VALUE:
        failure = ctypes.get_last_error()
        if failure == _ERROR_HANDLE_EOF and is_directory:
            return
        _reject("stream")
    try:
        if (
            is_directory
            or stream.stream_name != "::$DATA"
            or int(stream.stream_size) != expected_size
        ):
            _reject("stream")
        ctypes.set_last_error(_ERROR_SUCCESS)
        if find_next_stream(wintypes.HANDLE(value), ctypes.byref(stream)):
            _reject("stream")
        if ctypes.get_last_error() != _ERROR_HANDLE_EOF:
            _reject("stream")
    finally:
        find_close = _windows_kernel32().FindClose
        find_close.argtypes = (wintypes.HANDLE,)
        find_close.restype = wintypes.BOOL
        if not find_close(wintypes.HANDLE(value)):
            _reject("stream")


def _read_packaged_resource(path: Path, context: str) -> bytes:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_reparse(metadata)
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_MANIFEST_BYTES
            or metadata.st_nlink != 1
        ):
            _reject(context)
        remaining = metadata.st_size
        content = bytearray()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(metadata) != _file_identity(opened):
                _reject(context)
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block or len(block) > remaining:
                    _reject(context)
                content.extend(block)
                remaining -= len(block)
            if stream.read(1):
                _reject(context)
        after = path.lstat()
    except OSError, ValueError:
        _reject(context)
    if _file_identity(metadata) != _file_identity(after):
        _reject(context)
    return bytes(content)


def _parse_manifest(content: bytes) -> tuple[_ManifestFile, ...]:
    try:
        decoded = content.decode("utf-8")
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TesseractRuntimeAdmissionError:
        _reject("manifest_schema")
    root = _mapping(document)
    layout = _mapping(root.get("layout"))
    closure = _mapping(root.get("closure_evidence"))
    raw_files = root.get("files")
    if (
        root.get("schema_version") != 2
        or root.get("manifest_id") != "paperworkshell-tesseract-runtime-windows-x86_64"
        or root.get("task_id") != "PWS-0801"
        or layout.get("entrypoint") != TESSERACT_RUNTIME_ENTRYPOINT.as_posix()
        or layout.get("tessdata_directory") != TESSERACT_RUNTIME_TESSDATA_DIRECTORY.as_posix()
        or layout.get("allow_unlisted_files") is not False
        or layout.get("allow_reparse_points") is not False
        or closure.get("selected_file_count") != TESSERACT_RUNTIME_FILE_COUNT
        or type(raw_files) is not list
        or len(raw_files) != TESSERACT_RUNTIME_FILE_COUNT
    ):
        _reject("manifest_schema")

    files: list[_ManifestFile] = []
    seen: set[str] = set()
    for raw_entry in raw_files:
        entry = _mapping(raw_entry)
        raw_path = entry.get("path")
        size_bytes = entry.get("size_bytes")
        digest = entry.get("sha256")
        if (
            type(raw_path) is not str
            or type(size_bytes) is not int
            or size_bytes < 0
            or type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _reject("manifest_schema")
        parsed_path = PurePosixPath(raw_path)
        if (
            not raw_path
            or "\\" in raw_path
            or "\x00" in raw_path
            or ":" in raw_path
            or parsed_path.is_absolute()
            or parsed_path.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in parsed_path.parts)
            or len(parsed_path.parts) > _MAX_RUNTIME_DEPTH
        ):
            _reject("manifest_schema")
        key = raw_path.casefold()
        if key in seen:
            _reject("manifest_schema")
        seen.add(key)
        files.append(_ManifestFile(parsed_path, size_bytes, digest))

    files.sort(key=lambda item: item.path.as_posix())
    if (
        TESSERACT_RUNTIME_ENTRYPOINT.as_posix() not in seen
        or not any(
            entry.path == TESSERACT_RUNTIME_TESSDATA_DIRECTORY / "eng.traineddata"
            for entry in files
        )
        or sum(entry.size_bytes for entry in files) != closure.get("selected_file_bytes")
    ):
        _reject("manifest_schema")
    return tuple(files)


def _parse_language_catalog(content: bytes) -> tuple[_LanguagePack, ...]:
    try:
        decoded = content.decode("utf-8")
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_catalog_object,
            parse_constant=_reject_catalog_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TesseractRuntimeAdmissionError:
        _reject("language_catalog_schema")

    root = _catalog_mapping(document)
    _require_catalog_keys(
        root,
        {
            "schema_version",
            "catalog_id",
            "reviewed_on",
            "task_id",
            "target",
            "runtime_policy",
            "trust",
            "packs",
        },
    )
    target = _catalog_mapping(root["target"])
    policy = _catalog_mapping(root["runtime_policy"])
    trust = _catalog_mapping(root["trust"])
    _require_catalog_keys(target, {"os", "architecture", "abi"})
    _require_catalog_keys(
        policy,
        {
            "model_family",
            "model_version",
            "installation_mode",
            "network_download_allowed",
            "allow_unlisted_files",
            "allow_reparse_points",
        },
    )
    _require_catalog_keys(
        trust,
        {
            "runtime_authority",
            "upstream_signature_requirement",
            "signature_evidence_status",
            "required_key_fingerprint",
        },
    )
    raw_packs = root["packs"]
    if (
        root["schema_version"] != 1
        or root["catalog_id"] != "paperworkshell-tesseract-language-packs-windows-x86_64"
        or root["task_id"] != "PWS-0205"
        or type(root["reviewed_on"]) is not str
        or target != {"os": "Windows", "architecture": "x86_64", "abi": "MSYS2 UCRT64"}
        or policy
        != {
            "model_family": "tessdata_fast",
            "model_version": "4.1.0",
            "installation_mode": "offline-prestaged-exact-file",
            "network_download_allowed": False,
            "allow_unlisted_files": False,
            "allow_reparse_points": False,
        }
        or trust
        != {
            "runtime_authority": "catalog-file-size-and-sha256",
            "upstream_signature_requirement": _UPSTREAM_SIGNATURE_REQUIREMENT,
            "signature_evidence_status": _SIGNATURE_EVIDENCE_STATUS,
            "required_key_fingerprint": _PINNED_MSYS2_SIGNER_FINGERPRINT,
        }
        or type(raw_packs) is not list
        or not 1 <= len(raw_packs) <= _MAX_LANGUAGE_PACKS
    ):
        _reject("language_catalog_schema")

    supported_optional_codes = {
        language_code
        for definition in OCR_LANGUAGE_SET_DEFINITIONS
        for language_code in definition.language_codes
        if language_code != "eng"
    }
    required_pack_keys = {
        "language_code",
        "display_name",
        "relative_path",
        "size_bytes",
        "sha256",
        "license_expression",
        "distribution_package_id",
        "distribution_package_version",
        "archive",
        "archive_url",
        "archive_size_bytes",
        "archive_sha256",
        "signature_artifact",
        "signature_url",
        "signature_size_bytes",
        "signature_sha256",
        "expected_signer_fingerprint",
    }
    packs: list[_LanguagePack] = []
    seen_codes: set[str] = set()
    seen_paths: set[str] = set()
    for raw_pack in raw_packs:
        pack = _catalog_mapping(raw_pack)
        _require_catalog_keys(pack, required_pack_keys)
        language_code = pack["language_code"]
        display_name = pack["display_name"]
        raw_path = pack["relative_path"]
        package_id = pack["distribution_package_id"]
        package_version = pack["distribution_package_version"]
        archive = pack["archive"]
        signature_artifact = pack["signature_artifact"]
        expected_path = (
            f"{TESSERACT_RUNTIME_TESSDATA_DIRECTORY.as_posix()}/{language_code}.traineddata"
        )
        expected_package_id = f"mingw-w64-ucrt-x86_64-tesseract-data-{language_code}"
        expected_archive = f"{expected_package_id}-{package_version}-any.pkg.tar.zst"
        if (
            type(language_code) is not str
            or language_code not in supported_optional_codes
            or language_code in seen_codes
            or type(display_name) is not str
            or not display_name
            or len(display_name) > 64
            or any(character in display_name for character in "\r\n\x00")
            or type(raw_path) is not str
            or raw_path != expected_path
            or raw_path.casefold() in seen_paths
            or not _positive_int(pack["size_bytes"])
            or not _sha256_digest(pack["sha256"])
            or type(pack["license_expression"]) is not str
            or not pack["license_expression"]
            or package_id != expected_package_id
            or type(package_version) is not str
            or not package_version
            or archive != expected_archive
            or pack["archive_url"] != f"https://mirror.msys2.org/mingw/ucrt64/{expected_archive}"
            or not _positive_int(pack["archive_size_bytes"])
            or not _sha256_digest(pack["archive_sha256"])
            or signature_artifact != f"{expected_archive}.sig"
            or pack["signature_url"]
            != f"https://mirror.msys2.org/mingw/ucrt64/{expected_archive}.sig"
            or not _positive_int(pack["signature_size_bytes"])
            or not _sha256_digest(pack["signature_sha256"])
            or pack["expected_signer_fingerprint"] != _PINNED_MSYS2_SIGNER_FINGERPRINT
        ):
            _reject("language_catalog_schema")
        seen_codes.add(language_code)
        seen_paths.add(raw_path.casefold())
        packs.append(
            _LanguagePack(
                language_code=language_code,
                file=_ManifestFile(
                    path=PurePosixPath(raw_path),
                    size_bytes=pack["size_bytes"],
                    sha256=pack["sha256"],
                ),
            )
        )
    if seen_codes != supported_optional_codes:
        _reject("language_catalog_schema")
    packs.sort(key=lambda item: item.language_code)
    return tuple(packs)


def _expected_directories(files: tuple[_ManifestFile, ...]) -> set[str]:
    directories: set[str] = set()
    for entry in files:
        parent = entry.path.parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _inventory_runtime(root: Path) -> tuple[set[str], set[str]]:
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    observed_by_case: dict[str, str] = {}
    pending: list[tuple[Path, PurePosixPath, int]] = [(root, PurePosixPath("."), 0)]
    entry_count = 0
    try:
        root_metadata = root.lstat()
        if _has_unsupported_runtime_attributes(root_metadata):
            _reject("attributes")
        if not stat.S_ISDIR(root_metadata.st_mode) or _is_reparse(root_metadata):
            _reject("reparse")
        _require_only_unnamed_data_stream(root, is_directory=True)
        while pending:
            directory, relative_directory, depth = pending.pop()
            if depth > _MAX_RUNTIME_DEPTH:
                _reject("inventory")
            remaining_entries = _MAX_RUNTIME_ENTRIES - entry_count
            entries: list[os.DirEntry[str]] = []
            with os.scandir(directory) as children:
                for child in children:
                    if len(entries) >= remaining_entries:
                        _reject("inventory")
                    entries.append(child)
            entry_count += len(entries)
            entries.sort(key=lambda child: child.name.casefold())
            for child in entries:
                metadata = child.stat(follow_symlinks=False)
                if _has_unsupported_runtime_attributes(metadata):
                    _reject("attributes")
                if child.is_symlink() or _is_reparse(metadata):
                    _reject("reparse")
                relative = (
                    PurePosixPath(child.name)
                    if relative_directory == PurePosixPath(".")
                    else relative_directory / child.name
                )
                rendered = relative.as_posix()
                case_key = rendered.casefold()
                prior = observed_by_case.get(case_key)
                if prior is not None and prior != rendered:
                    _reject("case_collision")
                observed_by_case[case_key] = rendered
                if stat.S_ISDIR(metadata.st_mode):
                    _require_only_unnamed_data_stream(Path(child.path), is_directory=True)
                    observed_directories.add(rendered)
                    pending.append((Path(child.path), relative, depth + 1))
                elif stat.S_ISREG(metadata.st_mode):
                    observed_files.add(rendered)
                else:
                    _reject("type")
    except TesseractRuntimeAdmissionError:
        raise
    except OSError, ValueError:
        _reject("inventory")
    return observed_files, observed_directories


def _verify_observed_case(observed: set[str], allowed: set[str]) -> None:
    allowed_by_case = {path.casefold(): path for path in allowed}
    if len(allowed_by_case) != len(allowed):
        _reject("case_collision")
    for path in observed:
        expected = allowed_by_case.get(path.casefold())
        if expected is not None and path != expected:
            _reject("case_collision")


def _verify_file(path: Path, expected: _ManifestFile) -> None:
    try:
        before = path.lstat()
        if _has_unsupported_runtime_attributes(before):
            _reject("attributes")
        if _is_reparse(before):
            _reject("reparse")
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _reject("type")
        if before.st_size != expected.size_bytes:
            _reject("size")
        _require_only_unnamed_data_stream(
            path,
            is_directory=False,
            expected_size=expected.size_bytes,
        )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(before) != _file_identity(opened):
                _reject("changed")
            remaining = expected.size_bytes
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block or len(block) > remaining:
                    _reject("size")
                digest.update(block)
                remaining -= len(block)
            if stream.read(1):
                _reject("size")
        after = path.lstat()
    except TesseractRuntimeAdmissionError:
        raise
    except OSError, ValueError:
        _reject("missing")
    if _file_identity(before) != _file_identity(after):
        _reject("changed")
    if _has_unsupported_runtime_attributes(after):
        _reject("attributes")
    _require_only_unnamed_data_stream(
        path,
        is_directory=False,
        expected_size=expected.size_bytes,
    )
    if digest.hexdigest() != expected.sha256:
        _reject("hash")


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _has_unsupported_runtime_attributes(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & _UNSUPPORTED_RUNTIME_ATTRIBUTES)


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("manifest_schema")
        result[key] = value
    return result


def _unique_catalog_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("language_catalog_schema")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    _reject("manifest_schema")


def _reject_catalog_json_constant(_value: str) -> Never:
    _reject("language_catalog_schema")


def _mapping(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        _reject("manifest_schema")
    return value


def _catalog_mapping(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        _reject("language_catalog_schema")
    return value


def _require_catalog_keys(value: dict[str, Any], keys: set[str]) -> None:
    if set(value) != keys:
        _reject("language_catalog_schema")


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _sha256_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reject(context: str) -> Never:
    error = TesseractRuntimeAdmissionError(context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None
