"""Adversarial storage evidence for the PWS-0112 export destination boundary."""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
from io import BytesIO
from pathlib import Path

import pytest

from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationDependencies,
    ExportDestinationError,
    ExportDestinationErrorCode,
    WindowsExportDestination,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0112 targets the supported Windows storage boundary"
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


def _create_junction(link: Path, target: Path) -> None:
    """Create one real NTFS junction through structured Win32 arguments."""

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


def _commit(destination: Path, payload: bytes = b"synthetic-export") -> ExportCommitState:
    return (
        WindowsExportDestination()
        .commit(
            destination,
            BytesIO(payload),
            expected_byte_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            cancellation=lambda: False,
        )
        .state
    )


def test_raw_destination_and_os_failure_canaries_never_escape(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "SYNTHETIC-PRIVATE-DESTINATION-CANARY"
    destination = tmp_path / f"{canary}.pdf"

    def raw_failure(_path: Path) -> object:
        raise OSError(5, f"{canary}:{destination}:raw-system-message")

    dependencies = ExportDestinationDependencies(
        create_stage=raw_failure,  # type: ignore[arg-type]
    )
    with pytest.raises(ExportDestinationError) as raised:
        WindowsExportDestination(dependencies).commit(
            destination,
            BytesIO(b"private-source"),
            expected_byte_size=14,
            expected_sha256=hashlib.sha256(b"private-source").hexdigest(),
            cancellation=lambda: False,
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}\n{caplog.text}"
    assert raised.value.code is ExportDestinationErrorCode.DESTINATION_DENIED
    assert canary not in rendered
    assert str(destination) not in rendered
    assert "raw-system-message" not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_raw_reopen_failure_is_sanitized_and_preserves_committed_identity(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "SYNTHETIC-PRIVATE-REOPEN-CANARY"
    destination = tmp_path / "reopen-failure.pdf"
    payload = b"committed-before-reopen-failure"

    def fail_reopen(path: Path) -> object:
        raise OSError(5, f"{canary}:{path}:raw-reopen-message")

    result = WindowsExportDestination(
        ExportDestinationDependencies(
            open_committed=fail_reopen,  # type: ignore[arg-type]
        )
    ).commit(
        destination,
        BytesIO(payload),
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        cancellation=lambda: False,
    )

    rendered = f"{result!s}\n{result!r}\n{caplog.text}"
    assert result.state is ExportCommitState.UNKNOWN
    assert result.recovered_outcome
    assert canary not in rendered
    assert str(destination) not in rendered
    assert "raw-reopen-message" not in rendered
    assert destination.read_bytes() == payload
    assert tuple(tmp_path.glob(".*.pws-export.tmp")) == ()


def test_reparse_destination_parent_is_rejected_before_stage_creation(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    linked_parent = tmp_path / "linked-parent"
    real_parent.mkdir()
    _create_junction(linked_parent, real_parent)
    try:
        with pytest.raises(ExportDestinationError) as raised:
            _commit(linked_parent / "packet.pdf")

        assert raised.value.code is ExportDestinationErrorCode.DESTINATION_UNSAFE
        assert tuple(real_parent.iterdir()) == ()
    finally:
        _remove_junction(linked_parent)


def test_reparse_final_is_rejected_without_changing_its_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-sentinel")
    destination = tmp_path / "linked-final.pdf"
    _create_junction(destination, outside)
    try:
        with pytest.raises(ExportDestinationError) as raised:
            _commit(destination)

        assert raised.value.code is ExportDestinationErrorCode.DESTINATION_UNSAFE
        assert sentinel.read_bytes() == b"outside-sentinel"
        assert os.path.isjunction(destination)
    finally:
        _remove_junction(destination)


def test_hard_linked_existing_final_is_reported_without_mutating_alias(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-hard-link-sentinel.pdf"
    outside.write_bytes(b"outside-hard-link-sentinel")
    destination = tmp_path / "existing-hard-link.pdf"
    try:
        os.link(outside, destination)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {type(error).__name__}")

    state = _commit(destination, b"replacement-attempt")

    assert state is ExportCommitState.DESTINATION_EXISTS
    assert outside.read_bytes() == b"outside-hard-link-sentinel"
    assert destination.read_bytes() == b"outside-hard-link-sentinel"


def test_alternate_stream_destination_is_rejected_before_touching_base_file(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.pdf"
    base.write_bytes(b"base-sentinel")
    destination = Path(f"{base}:private-stream")

    with pytest.raises(ExportDestinationError) as raised:
        _commit(destination)

    assert raised.value.code is ExportDestinationErrorCode.PATH_INVALID
    assert base.read_bytes() == b"base-sentinel"
