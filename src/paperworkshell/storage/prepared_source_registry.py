"""Path-free registry for borrowing exact sealed pending-source handles."""

from __future__ import annotations

import hmac
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from threading import RLock
from typing import Final, Self
from uuid import UUID

from paperworkshell.domain.source_ingest import (
    PreparedSourceSummary,
    SourceIngestErrorCode,
    reject_source_ingest,
)
from paperworkshell.storage._windows import (
    WindowsHandle,
    WindowsPendingSourceFile,
    reopen_read_only_file,
)

_MAX_REGISTRATIONS: Final = 256


@dataclass(slots=True, repr=False)
class _Entry:
    summary: PreparedSourceSummary = field(repr=False)
    pending: WindowsPendingSourceFile = field(repr=False)
    revoked: bool = False


class PreparedSourceReadLease(AbstractContextManager["PreparedSourceReadLease"]):
    """One independently positioned, exact, read-only source authority."""

    __slots__ = ("_closed", "_handle", "_summary")

    def __init__(self, summary: PreparedSourceSummary, handle: WindowsHandle) -> None:
        self._summary = summary
        self._handle = handle
        self._closed = False

    def __repr__(self) -> str:
        return "PreparedSourceReadLease(<redacted>)"

    @property
    def summary(self) -> PreparedSourceSummary:
        return self._summary

    @property
    def handle_value(self) -> int:
        """Return the local HANDLE solely for reduced-right worker duplication."""

        if self._closed or self._handle.value == 0:
            reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.borrow_closed")
        return self._handle.value

    def __enter__(self) -> Self:
        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.borrow_closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handle.close()
        except BaseException:
            reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.borrow_close")


class PreparedSourceRegistration(AbstractContextManager["PreparedSourceRegistration"]):
    """Revocation authority owned by one prepared-source lease."""

    __slots__ = ("_closed", "_pending_id", "_registry")

    def __init__(self, registry: PreparedSourceReadRegistry, pending_id: UUID) -> None:
        self._registry = registry
        self._pending_id = pending_id
        self._closed = False

    def __repr__(self) -> str:
        return "PreparedSourceRegistration(<redacted>)"

    def __enter__(self) -> Self:
        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "source.registration_closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._registry._revoke(self._pending_id)


class PreparedSourceReadRegistry:
    """Authenticate summaries and lend handles without resolving a lexical path."""

    __slots__ = ("_entries", "_lock")

    def __init__(self) -> None:
        self._entries: dict[UUID, _Entry] = {}
        self._lock = RLock()

    def register(
        self,
        summary: PreparedSourceSummary,
        pending: WindowsPendingSourceFile,
    ) -> PreparedSourceRegistration:
        """Register only an already sealed and independently verified pending source."""

        if (
            type(summary) is not PreparedSourceSummary
            or type(pending) is not WindowsPendingSourceFile
        ):
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "source.registration")
        if not pending.is_sealed or pending.handle.value == 0:
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "source.registration_state")
        info = pending.handle.refresh_info()
        if (
            info.logical_size != summary.byte_size
            or info.is_directory
            or info.is_reparse_point
            or info.is_sparse
            or info.link_count != 1
        ):
            reject_source_ingest(
                SourceIngestErrorCode.PREPARE_FAILED, "source.registration_identity"
            )
        with self._lock:
            if len(self._entries) >= _MAX_REGISTRATIONS or summary.pending_id in self._entries:
                reject_source_ingest(
                    SourceIngestErrorCode.PREPARE_FAILED, "source.registration_capacity"
                )
            self._entries[summary.pending_id] = _Entry(summary, pending)
        return PreparedSourceRegistration(self, summary.pending_id)

    def borrow(self, summary: PreparedSourceSummary) -> PreparedSourceReadLease:
        """Borrow an independently positioned read handle for the exact full summary."""

        if type(summary) is not PreparedSourceSummary:
            reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.summary")
        with self._lock:
            entry = self._entries.get(summary.pending_id)
            if entry is None or entry.revoked or not _summaries_match(entry.summary, summary):
                reject_source_ingest(
                    SourceIngestErrorCode.INSPECT_FAILED, "inspection.registration"
                )
            try:
                handle = reopen_read_only_file(entry.pending.handle)
            except BaseException:
                reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.borrow")
        return PreparedSourceReadLease(summary, handle)

    def _revoke(self, pending_id: UUID) -> None:
        with self._lock:
            entry = self._entries.pop(pending_id, None)
            if entry is not None:
                entry.revoked = True


def _summaries_match(expected: PreparedSourceSummary, supplied: PreparedSourceSummary) -> bool:
    return (
        expected.pending_id == supplied.pending_id
        and expected.case_id == supplied.case_id
        and expected.source_id == supplied.source_id
        and expected.media_type is supplied.media_type
        and expected.byte_size == supplied.byte_size
        and hmac.compare_digest(expected.sha256.encode("ascii"), supplied.sha256.encode("ascii"))
    )
