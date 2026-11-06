"""Retained native directory authority for the scoped removal verifier."""

from __future__ import annotations

import ctypes
import os
from contextlib import AbstractContextManager
from ctypes import wintypes
from pathlib import Path
from typing import Self

from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.pdf.redaction_verification import (
    _bind_verification_scratch,
    _VerificationScratchAuthority,
)
from paperworkshell.worker.client._windows import _query_retained_resource_info
from paperworkshell.worker.pdf_export_operation import (
    _close_native_handle_checked,
    _create_worker_private_operation_temp,
    _kernel32,
    _remove_pinned_operation_temp,
)
from paperworkshell.worker.pdf_thumbnail_operation import _require_non_inheritable


def _identity(handle: int) -> tuple[int, bytes]:
    _require_non_inheritable(handle, context="redaction_export.scratch_handle")
    info = _query_retained_resource_info(handle)
    if not info.is_directory or info.is_reparse_point or info.delete_pending:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_identity")
    return info.identity


def _open_identity_probe(path: Path) -> int:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    # The original pin denies write/delete sharing. A temporary metadata reader
    # shares its existing delete authority without weakening that original pin.
    raw = create_file(os.fspath(path), 0x80, 0x7, None, 3, 0x02200000, None)
    handle = int(raw) if raw else 0
    if handle <= 0 or handle == ctypes.c_void_p(-1).value:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_probe")
    return handle


class PinnedVerificationScratch(AbstractContextManager["PinnedVerificationScratch"]):
    """Create and hold the exact worker-private directory through all cleanup.

    The creator supplies only its authenticated private TEMP root. No request,
    path record or report can supply a pin or manufacture this capability.
    """

    def __init__(self, private_temp_root: Path) -> None:
        self.path, self._pin = _create_worker_private_operation_temp(private_temp_root)
        self._active = True
        try:
            self._identity = _identity(self._pin)
            self.authority = _bind_verification_scratch(self.path, self.require_current)
        except BaseException:
            self._active = False
            pin, self._pin = self._pin, 0
            if not _close_native_handle_checked(pin):
                reject_pdf_export(
                    PdfExportErrorCode.WORKER_FAILED, "redaction_export.scratch_pin_close"
                )
            raise

    authority: _VerificationScratchAuthority

    def require_current(self) -> None:
        if not self._active or _identity(self._pin) != self._identity:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_pin")
        probe = _open_identity_probe(self.path)
        try:
            current = _identity(probe)
        finally:
            if not _close_native_handle_checked(probe):
                reject_pdf_export(
                    PdfExportErrorCode.WORKER_FAILED, "redaction_export.scratch_probe_close"
                )
        if current != self._identity:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_path")

    def __enter__(self) -> Self:
        if not self._active:
            reject_pdf_export(
                PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.scratch_inactive"
            )
        try:
            self.require_current()
        except BaseException:
            self._active = False
            pin, self._pin = self._pin, 0
            if not _close_native_handle_checked(pin):
                reject_pdf_export(
                    PdfExportErrorCode.WORKER_FAILED, "redaction_export.scratch_pin_close"
                )
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        if not self._active:
            return
        try:
            self.require_current()
        except BaseException:
            self._active = False
            pin, self._pin = self._pin, 0
            # Do not delete a path whose exact pin binding is now uncertain.
            # The broker's authenticated private-root cleanup owns recovery.
            if not _close_native_handle_checked(pin):
                reject_pdf_export(
                    PdfExportErrorCode.WORKER_FAILED, "redaction_export.scratch_pin_close"
                )
            raise
        self._active = False
        pin, self._pin = self._pin, 0
        if not _remove_pinned_operation_temp(self.path, pin):
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_export.scratch_cleanup")

    def __repr__(self) -> str:
        return "PinnedVerificationScratch(<private>)"
