"""Production worker seams for verified, handle-brokered PDF inspection."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, cast

from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_OBJECTS,
    PdfInspectionEnvelope,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfInspectionResult,
    encode_pdf_inspection_frame,
    reject_pdf_inspection,
)
from paperworkshell.pdf.pikepdf_adapter import PikePdfAdapter
from paperworkshell.worker.operations import (
    NoopOperationHandler,
    OperationContext,
    PdfInspectionOperationHandler,
    RuntimeOperationRegistry,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_MINIMUM_OBJECT_LIMIT: Final = 4_096
_OBJECTS_PER_PAGE_LIMIT: Final = 1_024
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400


def _close_native_handle_no_raise(handle: int) -> None:
    if handle <= 0 or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))
    except BaseException:
        pass


def _close_descriptor_no_raise(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _require_non_inheritable(handle: int, *, context: str) -> None:
    try:
        inheritable = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, context)
    if inheritable:
        reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, context)


@dataclass(frozen=True, slots=True)
class WindowsPdfInspectionAdapter:
    """Verify a reduced source handle, close it, then inspect an ephemeral copy."""

    adapter: PikePdfAdapter = PikePdfAdapter()

    def discard_source(self, source_handle: int) -> None:
        """Consume unused source authority without exposing close diagnostics."""

        _close_native_handle_no_raise(source_handle)

    def inspect(self, context: OperationContext, source_handle: int) -> PdfInspectionResult:
        owned_handle = source_handle
        descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_pdf_inspection(
                    PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
                    "worker.platform",
                )
            source_reference = context.inputs[0]
            expected_size = source_reference.expected_byte_size
            expected_digest = source_reference.expected_sha256
            if (
                expected_size is None
                or expected_digest is None
                or expected_size < 1
                or expected_size > context.limits.temporary_storage_bytes
            ):
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "worker.source_size")
            _require_non_inheritable(source_handle, context="worker.source_handle")
            try:
                descriptor = msvcrt.open_osfhandle(source_handle, os.O_RDONLY | os.O_BINARY)
            except OSError, OverflowError, ValueError:
                reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, "worker.source_handle")
            owned_handle = 0

            deadline = time.monotonic() + (context.limits.wall_time_ms / 1_000.0)
            temp_root = _materialize_worker_private_temp()
            try:
                staged_context = tempfile.TemporaryFile(mode="w+b", dir=temp_root)
            except OSError:
                reject_pdf_inspection(
                    PdfInspectionErrorCode.LIMIT_EXCEEDED,
                    "worker.temp_create",
                )
            with staged_context as staged:
                staged_stream = cast(BinaryIO, staged)
                with os.fdopen(descriptor, "rb", closefd=True) as source:
                    descriptor = None
                    try:
                        source_stat = os.fstat(source.fileno())
                    except OSError:
                        reject_pdf_inspection(
                            PdfInspectionErrorCode.HANDLE_INVALID,
                            "worker.source_stat",
                        )
                    if (
                        not stat.S_ISREG(source_stat.st_mode)
                        or source_stat.st_size != expected_size
                    ):
                        reject_pdf_inspection(
                            PdfInspectionErrorCode.SOURCE_CHANGED,
                            "worker.source_stat",
                        )
                    self._copy_verified(
                        source,
                        staged_stream,
                        expected_size=expected_size,
                        expected_digest=expected_digest,
                        context=context,
                        deadline=deadline,
                    )
                # The exact case handle is now closed.  Native parsing sees only
                # the independently verified, worker-private temporary stream.
                staged_stream.flush()
                staged_stream.seek(0)
                return self._inspect_staged(staged_stream, context=context, deadline=deadline)
        finally:
            if descriptor is not None:
                _close_descriptor_no_raise(descriptor)
            elif owned_handle > 0:
                _close_native_handle_no_raise(owned_handle)

    def _copy_verified(
        self,
        source: BinaryIO,
        staged: BinaryIO,
        *,
        expected_size: int,
        expected_digest: str,
        context: OperationContext,
        deadline: float,
    ) -> None:
        digest = hashlib.sha256()
        copied = 0
        while copied < expected_size:
            _require_active(context, deadline=deadline)
            requested = min(_COPY_CHUNK_BYTES, expected_size - copied)
            try:
                block = source.read(requested)
            except OSError:
                reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_CHANGED, "worker.source_read")
            if type(block) is not bytes or not block or len(block) > requested:
                reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_CHANGED, "worker.source_read")
            try:
                completed = staged.write(block)
            except OSError:
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "worker.temp_write")
            if completed != len(block):
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "worker.temp_write")
            digest.update(block)
            copied += len(block)
        try:
            surplus = source.read(1)
        except OSError:
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_CHANGED, "worker.source_eof")
        if surplus != b"" or not hmac.compare_digest(digest.hexdigest(), expected_digest):
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_CHANGED, "worker.source_verify")
        _require_active(context, deadline=deadline)

    def _inspect_staged(
        self,
        staged: BinaryIO,
        *,
        context: OperationContext,
        deadline: float,
    ) -> PdfInspectionResult:
        object_limit = min(
            MAX_PDF_INSPECTION_OBJECTS,
            max(
                _MINIMUM_OBJECT_LIMIT,
                context.limits.page_count * _OBJECTS_PER_PAGE_LIMIT,
            ),
        )
        limits = PdfInspectionLimits(
            maximum_pages=context.limits.page_count,
            maximum_objects=object_limit,
            maximum_input_bytes=context.limits.temporary_storage_bytes,
            maximum_temporary_bytes=context.limits.temporary_storage_bytes,
            maximum_output_bytes=context.limits.output_bytes,
            maximum_tool_output_bytes=context.limits.output_bytes,
        )
        return self.adapter.inspect(
            staged,
            source_bytes=context.inputs[0].expected_byte_size or 0,
            limits=limits,
            deadline=deadline,
            cancellation_probe=context.cancel_requested.is_set,
        )


@dataclass(frozen=True, slots=True)
class WindowsPdfInspectionResultWriter:
    """Write and close one bounded correlated frame to an exact pipe handle."""

    def discard_result(self, result_handle: int) -> None:
        """Consume unused result authority without exposing close diagnostics."""

        _close_native_handle_no_raise(result_handle)

    def write(
        self,
        context: OperationContext,
        result_handle: int,
        inspection: PdfInspectionResult,
    ) -> None:
        try:
            if os.name != "nt":
                reject_pdf_inspection(
                    PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
                    "worker.platform",
                )
            _require_non_inheritable(result_handle, context="worker.result_handle")
            frame = encode_pdf_inspection_frame(
                PdfInspectionEnvelope(
                    operation_id=context.operation_id,
                    source_object_id=context.inputs[0].object_id,
                    result=inspection,
                )
            )
            if len(frame) > context.limits.output_bytes:
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "worker.result_size")
            _write_pipe_frame(result_handle, frame)
        finally:
            _close_native_handle_no_raise(result_handle)


def _write_pipe_frame(handle: int, frame: bytes) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        storage = ctypes.create_string_buffer(frame)
        cursor = 0
        while cursor < len(frame):
            written = wintypes.DWORD()
            remaining = len(frame) - cursor
            if not write_file(
                wintypes.HANDLE(handle),
                ctypes.byref(storage, cursor),
                remaining,
                ctypes.byref(written),
                None,
            ):
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "worker.result_write")
            count = int(written.value)
            if not 1 <= count <= remaining:
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "worker.result_write")
            cursor += count
    except PdfInspectionError:
        raise
    except Exception:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "worker.result_write")


def _materialize_worker_private_temp() -> Path:
    """Return the launch-profile temp root after in-container identity checks."""

    try:
        local_value = os.environ.get("LOCALAPPDATA")
        temp_value = os.environ.get("TEMP")
        tmp_value = os.environ.get("TMP")
        if (
            type(local_value) is not str
            or type(temp_value) is not str
            or type(tmp_value) is not str
            or not local_value
            or not temp_value
            or not tmp_value
            or "\x00" in local_value
            or "\x00" in temp_value
            or "\x00" in tmp_value
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "worker.temp_environment")
        local_root = Path(local_value)
        temp_root = Path(temp_value)
        tmp_root = Path(tmp_value)
        if (
            not local_root.is_absolute()
            or not temp_root.is_absolute()
            or not tmp_root.is_absolute()
            or os.path.normcase(os.path.abspath(temp_root))
            != os.path.normcase(os.path.abspath(tmp_root))
            or os.path.normcase(os.path.abspath(temp_root.parent))
            != os.path.normcase(os.path.abspath(local_root))
            or temp_root.name.casefold() != "temp"
            or os.fspath(local_root).startswith(("\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "worker.temp_scope")
        temp_root.mkdir(parents=True, exist_ok=True)
        for candidate in (local_root, temp_root):
            information = os.lstat(candidate)
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "worker.temp_identity")
        return temp_root
    except PdfInspectionError:
        raise
    except OSError, TypeError, ValueError:
        reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "worker.temp_unavailable")


def _require_active(context: OperationContext, *, deadline: float) -> None:
    if context.cancel_requested.is_set():
        reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "worker.operation")
    if time.monotonic() >= deadline:
        reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "worker.operation")


def build_pdf_runtime_registry() -> RuntimeOperationRegistry:
    """Compose the production parser and result writer behind the fixed handler."""

    return RuntimeOperationRegistry(
        (
            NoopOperationHandler(),
            build_pdf_inspection_operation_handler(),
        )
    )


def build_pdf_inspection_operation_handler() -> PdfInspectionOperationHandler[PdfInspectionResult]:
    """Compose only structural inspection so optional render loading is independent."""

    return PdfInspectionOperationHandler(
        WindowsPdfInspectionAdapter(),
        WindowsPdfInspectionResultWriter(),
    )
