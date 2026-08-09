"""Adversarial evidence for the bounded PWS-0103 source precheck."""

from __future__ import annotations

import ast
import ctypes
import hashlib
import os
import struct
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

import paperworkshell.storage._source_windows as source_windows_module
import paperworkshell.storage.source_precheck as source_precheck_module
from paperworkshell.domain.import_precheck import (
    DeclaredMediaType,
    DetectedMediaType,
    ImportLimits,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
)
from paperworkshell.storage._source_windows import SourceHandleFailure
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_precheck import (
    WindowsSourcePrechecker,
    inspect_bounded_source,
)
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0103 targets the supported Windows source boundary"
)

_GENERIC_WRITE = 0x4000_0000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_FILE_SHARE_DELETE = 0x4
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS = 0x0200_0000
_FSCTL_SET_REPARSE_POINT = 0x0009_00A4
_IO_REPARSE_TAG_MOUNT_POINT = 0xA000_0003
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _limits(**overrides: int) -> ImportLimits:
    values = {
        "maximum_source_bytes": 2_000_000,
        "maximum_pdf_pages": 100,
        "maximum_image_width": 10_000,
        "maximum_image_height": 10_000,
        "maximum_image_pixels": 50_000_000,
        "maximum_decompressed_bytes": 200_000_000,
        "decompression_warning_ratio": 100,
        "maximum_decompression_ratio": 2_000,
        "maximum_header_bytes": 4_096,
        "maximum_structure_records": 64,
    }
    values.update(overrides)
    return ImportLimits(**values)


def _snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda value: str(value).casefold()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append((relative, "directory", ""))
        else:
            entries.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


def _create_junction(link: Path, target: Path) -> None:
    """Create a real NTFS junction through structured Win32 arguments only."""

    link.mkdir()
    substitute = f"\\??\\{target.resolve()}"
    printable = str(target.resolve())
    substitute_bytes = substitute.encode("utf-16-le")
    printable_bytes = printable.encode("utf-16-le")
    path_buffer = substitute_bytes + b"\0\0" + printable_bytes + b"\0\0"
    payload = (
        struct.pack(
            "<IHHHHHH",
            _IO_REPARSE_TAG_MOUNT_POINT,
            8 + len(path_buffer),
            0,
            0,
            len(substitute_bytes),
            len(substitute_bytes) + 2,
            len(printable_bytes),
        )
        + path_buffer
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    device_io_control = kernel32.DeviceIoControl
    device_io_control.argtypes = (
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    )
    device_io_control.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    raw_handle = create_file(
        str(link),
        _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    handle = int(raw_handle) if raw_handle else 0
    if not handle or handle == _INVALID_HANDLE_VALUE:
        pytest.fail("required Windows junction fixture could not be opened")
    try:
        returned = ctypes.c_ulong()
        buffer = ctypes.create_string_buffer(payload)
        if not device_io_control(
            ctypes.c_void_p(handle),
            _FSCTL_SET_REPARSE_POINT,
            buffer,
            len(payload),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            pytest.fail("required Windows junction fixture could not be created")
    finally:
        close_handle(ctypes.c_void_p(handle))
    assert os.path.isjunction(link)


def _remove_junction(path: Path) -> None:
    if os.path.lexists(path):
        os.rmdir(path)


def _assert_no_shell_calls(module: ModuleType) -> None:
    source_path = Path(module.__file__ or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            assert not any(name == "subprocess" or name.startswith("subprocess.") for name in names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess"
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id == "os":
            assert node.func.attr not in {"system", "popen", "startfile"}
        if isinstance(owner, ast.Name) and owner.id == "subprocess":
            pytest.fail("import prechecks must never invoke a subprocess")


def test_import_precheck_modules_contain_no_shell_or_subprocess_invocation() -> None:
    _assert_no_shell_calls(source_precheck_module)
    _assert_no_shell_calls(source_windows_module)


@pytest.mark.parametrize(
    "filename",
    [
        "selected_$(noop);&echo_'quoted'.pdf",
        "selected_%COMSPEC%_`noop`_^!.pdf",
        "(selected);echo&noop.pdf",
    ],
)
def test_shell_metacharacters_remain_inert_filename_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    path = (tmp_path / filename).resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    path.write_bytes(value)
    sentinel = (tmp_path / "must-not-exist").resolve()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a source filename reached a process-launch API")

    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    monkeypatch.setattr(os, "startfile", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "call", forbidden)
    monkeypatch.setattr(subprocess, "check_call", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.result.media_type is DetectedMediaType.PDF
        assert source.read_at(0, len(value)) == value

    assert not sentinel.exists()
    assert path.read_bytes() == value


@pytest.mark.parametrize("filename", ["line\nbreak.pdf", "line\rbreak.pdf", "safe\u202ename.pdf"])
def test_control_and_bidi_filename_content_is_rejected_before_open(
    tmp_path: Path,
    filename: str,
) -> None:
    path = tmp_path / filename

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path):
            pytest.fail("control-bearing filename content cannot yield a source lease")

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_PATH_INVALID
    assert filename not in str(raised.value)


def test_source_path_depth_is_bounded_before_any_native_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = 0

    def stop_at_first_open(
        _selected: Path,
        _access: int,
        _flags: int,
        _operation: str,
    ) -> int:
        nonlocal opened
        opened += 1
        raise SourceHandleFailure("source.open")

    monkeypatch.setattr(source_windows_module, "_open_raw_handle", stop_at_first_open)
    exact = Path(tmp_path.anchor).joinpath(*("a" for _ in range(63)), "source.pdf")
    with pytest.raises(ImportPrecheckError) as exact_error:
        with WindowsSourcePrechecker(_limits()).open(exact):
            pytest.fail("the native-open sentinel cannot yield a source lease")
    assert exact_error.value.code is ImportPrecheckErrorCode.SOURCE_UNAVAILABLE
    assert opened == 1

    excessive = Path(tmp_path.anchor).joinpath(*("a" for _ in range(64)), "source.pdf")
    with pytest.raises(ImportPrecheckError) as excessive_error:
        with WindowsSourcePrechecker(_limits()).open(excessive):
            pytest.fail("an excessive-depth source path cannot yield a source lease")
    assert excessive_error.value.code is ImportPrecheckErrorCode.SOURCE_PATH_INVALID
    assert opened == 1


class _FailingSource:
    byte_size = 8

    def __init__(self, private_token: str) -> None:
        self._private_token = private_token

    def read_at(self, _offset: int, _count: int) -> bytes:
        raise RuntimeError(f"raw parser failure: {self._private_token}")


class _FailingSizeSource:
    def __init__(self, private_token: str) -> None:
        self._private_token = private_token

    @property
    def byte_size(self) -> int:
        raise ValueError(f"raw size failure: {self._private_token}")

    def read_at(self, _offset: int, _count: int) -> bytes:
        pytest.fail("a failed size observation must stop before a read")


def test_raw_read_failure_and_logs_do_not_disclose_source_details(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_token = f"PRIVATE-{tmp_path.name}-applicant-address"

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(_FailingSource(private_token), DeclaredMediaType.PDF, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_READ_FAILED
    assert private_token not in str(raised.value)
    assert private_token not in repr(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert private_token not in caplog.text


def test_raw_size_failure_does_not_disclose_source_details(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_token = f"PRIVATE-{tmp_path.name}-original-filename"

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(_FailingSizeSource(private_token), DeclaredMediaType.PDF, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_READ_FAILED
    assert private_token not in str(raised.value)
    assert private_token not in repr(raised.value)
    assert private_token not in caplog.text


def test_rejected_import_does_not_create_partial_case_state(tmp_path: Path) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(1_000_000, 100_000, 100_000, 100, 10),
    )
    manager.provision()
    before = _snapshot(profile)
    hostile = (tmp_path / "unsupported.pdf").resolve()
    hostile.write_bytes(b"MZ\x90\x00synthetic-executable")
    hostile_digest = hashlib.sha256(hostile.read_bytes()).hexdigest()

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(hostile):
            pytest.fail("unsupported content cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.MEDIA_UNSUPPORTED
    assert _snapshot(profile) == before
    assert hashlib.sha256(hostile.read_bytes()).hexdigest() == hostile_digest


def test_parent_junction_stops_before_target_source_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    source = outside / "source.pdf"
    value = b"%PDF-1.7\n%%EOF\n"
    source.write_bytes(value)
    sentinel = outside / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    link = (tmp_path / "source-link").resolve()
    _create_junction(link, outside)
    delegate = source_windows_module._open_raw_handle
    opened_paths: list[Path] = []

    def record_open(selected: Path, access: int, flags: int, operation: str) -> int:
        opened_paths.append(selected)
        return delegate(selected, access, flags, operation)

    monkeypatch.setattr(source_windows_module, "_open_raw_handle", record_open)
    try:
        with pytest.raises(ImportPrecheckError) as raised:
            with WindowsSourcePrechecker(_limits()).open(link / "source.pdf"):
                pytest.fail("a parent-junction alias cannot yield a source lease")

        assert raised.value.code is ImportPrecheckErrorCode.SOURCE_REPARSE_POINT
        assert source not in opened_paths
        assert link / "source.pdf" not in opened_paths
        assert source.read_bytes() == value
        assert sentinel.read_bytes() == b"outside"
    finally:
        _remove_junction(link)


def test_only_default_stream_is_read_and_named_stream_remains_untouched(tmp_path: Path) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    stream_path = f"{path}:synthetic-private"
    path.write_bytes(value)
    with open(stream_path, "wb") as stream:
        stream.write(b"never-document-content")

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.result.media_type is DetectedMediaType.PDF
        assert source.read_at(0, len(value)) == value

    assert path.read_bytes() == value
    with open(stream_path, "rb") as stream:
        assert stream.read() == b"never-document-content"
