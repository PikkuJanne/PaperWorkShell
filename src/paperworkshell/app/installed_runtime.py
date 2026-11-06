"""Discover the fixed installed layout without consulting machine configuration.

The small marker identifies a layout, not a trusted signature or an enabled
feature. Worker resource authentication and exact OCR admission remain separate.
No document or case content is opened by this module.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Never

INSTALLED_APPLICATION_ID: Final = "io.github.pikkujanne.paperworkshell"
INSTALLED_MARKER_NAME: Final = "paperworkshell-installed-v1.json"
INSTALLED_GUI_EXECUTABLE_NAME: Final = "PaperWorkShellDesktop.exe"
MAX_INSTALLED_MARKER_BYTES: Final = 4096
_MARKER_KEYS: Final = frozenset(
    {"schema_version", "application_id", "version", "channel", "candidate_id"}
)
_VERSION: Final = re.compile(r"(?:0|[1-9][0-9]{0,3})(?:\.(?:0|[1-9][0-9]{0,3})){2}\Z")
_CANDIDATE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,95}\Z")
_UNSUPPORTED_ATTRIBUTES: Final = 0x00000400 | 0x00001000 | 0x00040000 | 0x00400000
_MAX_PATH_PARTS: Final = 64


class InstalledRuntimeError(RuntimeError):
    """A stable, path-free installation failure suitable for startup display."""

    code: Final = "INSTALLED_RUNTIME_INVALID"

    def __init__(self) -> None:
        super().__init__(
            "PaperWorkShell's installation is incomplete or changed. "
            "Reinstall the same PaperWorkShell version, then try again. "
            "Your saved cases are kept separately. (INSTALLED_RUNTIME_INVALID)"
        )


@dataclass(frozen=True, slots=True)
class InstalledRuntime:
    """Code-relative resources; marker values can never choose an executable."""

    version: str
    candidate_id: str
    runtime_root: Path = field(repr=False)
    package_root: Path = field(repr=False)
    python_executable: Path = field(repr=False)
    qpdf_executable: Path = field(repr=False)
    tesseract_executable: Path = field(repr=False)
    tessdata_root: Path = field(repr=False)

    @property
    def gui_executable(self) -> Path:
        """The fixed, separately inventoried CPython GUI filename copy."""

        return self.runtime_root / INSTALLED_GUI_EXECUTABLE_NAME


def _reject() -> Never:
    raise InstalledRuntimeError()


def _absolute(path: Path) -> Path:
    if not isinstance(path, Path):
        _reject()
    try:
        result = Path(os.path.abspath(path))
    except OSError, ValueError:
        result = None
    if result is None or len(result.parts) > _MAX_PATH_PARTS:
        _reject()
    return result


def _plain_metadata(path: Path, *, directory: bool) -> os.stat_result:
    """Refuse redirected ancestors and files; do not resolve away their spelling."""

    for item in (*reversed(path.parents), path):
        try:
            metadata = item.lstat()
        except OSError, ValueError:
            metadata = None
        if metadata is None or (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & _UNSUPPORTED_ATTRIBUTES
        ):
            _reject()
        is_directory = item != path or directory
        if is_directory:
            if not stat.S_ISDIR(metadata.st_mode):
                _reject()
        elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            _reject()
    if metadata is None:
        _reject()
    return metadata


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        getattr(metadata, "st_file_attributes", 0),
    )


def _read_marker(path: Path) -> bytes:
    before = _plain_metadata(path, directory=False)
    if not 0 < before.st_size <= MAX_INSTALLED_MARKER_BYTES:
        _reject()
    content: bytes | None = None
    try:
        with path.open("rb") as stream:
            if _identity(os.fstat(stream.fileno())) != _identity(before):
                _reject()
            content = stream.read(MAX_INSTALLED_MARKER_BYTES + 1)
            if _identity(os.fstat(stream.fileno())) != _identity(before):
                _reject()
    except OSError, ValueError:
        content = None
    if (
        content is None
        or len(content) != before.st_size
        or _identity(_plain_metadata(path, directory=False)) != _identity(before)
    ):
        _reject()
    return content


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate installation field")
        result[key] = value
    return result


def _metadata(content: bytes) -> tuple[str, str]:
    payload: object = None
    try:
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeError, ValueError, RecursionError:
        pass
    if not isinstance(payload, dict) or set(payload) != _MARKER_KEYS:
        _reject()
    version, candidate = payload["version"], payload["candidate_id"]
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["application_id"] != INSTALLED_APPLICATION_ID
        or payload["channel"] != "engineering"
        or type(version) is not str
        or _VERSION.fullmatch(version) is None
        or type(candidate) is not str
        or _CANDIDATE.fullmatch(candidate) is None
    ):
        _reject()
    return version, candidate


def discover_installed_runtime(
    *,
    package_root: Path,
    executable: Path,
    prefix: Path,
    base_prefix: Path,
) -> InstalledRuntime | None:
    """Select an embedded installed runtime, or leave development unchanged.

    The source checkout and a normal virtual environment need no marker. An
    embedded runtime or the fixed installation layout must have a valid marker;
    absence cannot silently fall back to environment-selected document engines.
    """

    runtime = _absolute(prefix)
    package = _absolute(package_root)
    interpreter = _absolute(executable)
    base = _absolute(base_prefix)
    expected_package = runtime / "Lib" / "site-packages" / "paperworkshell"
    marker = runtime / INSTALLED_MARKER_NAME
    try:
        marker.lstat()
        has_marker = True
    except FileNotFoundError:
        has_marker = False
    except OSError, ValueError:
        _reject()
    if not has_marker:
        embedded = (runtime / "python314._pth").exists()
        installed_layout = runtime.name.lower() == "runtime" and package == expected_package
        if runtime == base and (embedded or installed_layout):
            _reject()
        return None
    if (
        runtime != base
        or package != expected_package
        or interpreter
        not in (
            runtime / "python.exe",
            runtime / "pythonw.exe",
            runtime / INSTALLED_GUI_EXECUTABLE_NAME,
        )
    ):
        _reject()
    version, candidate = _metadata(_read_marker(marker))
    qpdf = runtime / "engines" / "qpdf" / "bin" / "qpdf.exe"
    tesseract = runtime / "engines" / "tesseract" / "bin" / "tesseract.exe"
    tessdata = runtime / "engines" / "tesseract" / "share" / "tessdata"
    for file in (
        runtime / "python.exe",
        runtime / "pythonw.exe",
        runtime / INSTALLED_GUI_EXECUTABLE_NAME,
        runtime / "python314._pth",
        package / "__main__.py",
        package / "worker" / "host_bootstrap.py",
        qpdf,
        tesseract,
    ):
        _plain_metadata(file, directory=False)
    _plain_metadata(tessdata, directory=True)
    return InstalledRuntime(
        version, candidate, runtime, package, runtime / "python.exe", qpdf, tesseract, tessdata
    )
