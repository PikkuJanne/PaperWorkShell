"""Authenticated Windows journal for restart-safe PDF export-history retries."""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Never
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import PdfExportHistoryRetryReceipt
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    reject_pdf_export_history,
)
from paperworkshell.domain.pdf_export_retry import (
    MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES,
    MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES,
    PdfExportRetryInventory,
    PdfExportRetryJournalEntry,
    PdfExportRetryPhase,
    PreparedPdfExportRetry,
    canonical_pdf_export_retry_entry_bytes,
    decode_pdf_export_retry_entry,
)
from paperworkshell.storage._windows import (
    WindowsHandle,
    open_handle,
    rename_open_file_no_replace_at,
    reopen_read_only_file,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    WorkspaceCapacityReservation,
)

_PREPARED_SUFFIX: Final = ".prepared.json"
_COMMITTED_SUFFIX: Final = ".committed.json"
_NAME_PREFIX: Final = "export-retry-"
_STAGE_SUFFIX: Final = ".export-retry.tmp"
_COMMITTED_STAGE_SUFFIX: Final = ".export-retry-committed.tmp"
_MAXIMUM_STAGE_ID_ATTEMPTS: Final = 16
_RETRY_NAME: Final = re.compile(
    r"export-retry-(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})\."
    r"(?P<phase>prepared|committed)\.json\Z"
)
_OWNED_ID_PREFIX: Final = re.compile(
    r"export-retry-(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    re.IGNORECASE,
)


@dataclass(slots=True, repr=False)
class WindowsPreparedPdfExportRetry:
    """Retain exact receipt authority across the external destination commit."""

    _receipt: PdfExportHistoryRetryReceipt = field(repr=False)
    _authority: AuthenticatedCase = field(repr=False)
    _prepared_handle: WindowsHandle | None = field(repr=False)
    _committed_stage_handle: WindowsHandle | None = field(repr=False)
    _journal_handle: WindowsHandle = field(repr=False)
    _prepared_path: Path = field(repr=False)
    _committed_stage_path: Path = field(repr=False)
    _committed_path: Path = field(repr=False)
    _prepared_payload: bytes = field(repr=False)
    _committed_payload: bytes = field(repr=False)
    _prepared_reservation: WorkspaceCapacityReservation = field(repr=False)
    _committed_reservation: WorkspaceCapacityReservation = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)

    @property
    def receipt(self) -> PdfExportHistoryRetryReceipt:
        return self._receipt

    def mark_committed(self) -> bool:
        """Publish and verify the replayable phase after exact destination success."""

        if self._closed:
            return False
        if self._committed:
            return True
        stage = self._committed_stage_handle
        if stage is None:
            return False
        if not _rename_to_exact_target(
            stage,
            self._journal_handle,
            initial_path=self._committed_stage_path,
            destination=self._committed_path,
        ):
            return False
        try:
            _authenticate_payload_handle(
                stage,
                self._committed_path,
                self._committed_payload,
                expected_case_id=self._receipt.case_id,
                expected_export_id=self._receipt.export_id,
                expected_phase=PdfExportRetryPhase.COMMITTED,
            )
        except BaseException:
            return False
        self._committed = True
        _delete_exact_handle(self._prepared_handle)
        self._prepared_handle = None
        return True

    def resolve(self) -> bool:
        """Delete only the exact committed marker after history repair succeeds."""

        if self._closed or not self._committed:
            return False
        handle = self._committed_stage_handle
        if handle is None:
            return False
        if os.path.lexists(self._prepared_path):
            try:
                prepared = _open_exact_entry_for_delete(
                    self._prepared_path,
                    self._receipt.case_id,
                    self._receipt.export_id,
                    PdfExportRetryPhase.PREPARED,
                    self._receipt.record_sha256,
                )
            except PdfExportHistoryError, WorkspaceError:
                return False
            if not _delete_exact_handle(prepared):
                return False
        if not _delete_exact_handle(handle):
            return False
        self._committed_stage_handle = None
        return True

    def discard_prepared(self) -> bool:
        """Delete both inert stages after a definitely non-committed destination."""

        if self._closed or self._committed:
            return False
        prepared_removed = _delete_exact_handle(self._prepared_handle)
        committed_stage_removed = _delete_exact_handle(self._committed_stage_handle)
        self._prepared_handle = None
        self._committed_stage_handle = None
        return prepared_removed and committed_stage_removed

    def close(self) -> None:
        """Release handles and quota claims without guessing the destination outcome."""

        if self._closed:
            return
        _close_handle(self._prepared_handle)
        _close_handle(self._committed_stage_handle)
        _close_handle(self._journal_handle)
        self._prepared_handle = None
        self._committed_stage_handle = None
        committed_released = _release_reservation_no_raise(self._committed_reservation)
        prepared_released = _release_reservation_no_raise(self._prepared_reservation)
        authority_closed = _close_authority_no_raise(self._authority)
        self._closed = committed_released and prepared_released and authority_closed

    def __repr__(self) -> str:
        return f"WindowsPreparedPdfExportRetry(committed={self._committed}, closed={self._closed})"


class WindowsPdfExportRetryRepository:
    """Prepare, confirm, inventory, and clear protected case-journal receipts."""

    __slots__ = ("_id_factory", "_manager")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not isinstance(manager, CaseWorkspaceManager) or not callable(id_factory):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.repository",
            )
        self._manager = manager
        self._id_factory = id_factory

    def prepare(
        self,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PreparedPdfExportRetry:
        """Publish an inert receipt and prewrite its committed form before export."""

        if type(receipt) is not PdfExportHistoryRetryReceipt:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.prepare",
            )
        prepared_payload = canonical_pdf_export_retry_entry_bytes(
            PdfExportRetryJournalEntry(PdfExportRetryPhase.PREPARED, receipt)
        )
        committed_payload = canonical_pdf_export_retry_entry_bytes(
            PdfExportRetryJournalEntry(PdfExportRetryPhase.COMMITTED, receipt)
        )
        prepared_name = _phase_name(receipt.export_id, PdfExportRetryPhase.PREPARED)
        committed_name = _phase_name(receipt.export_id, PdfExportRetryPhase.COMMITTED)
        prepared_stage_id = self._next_stage_id(receipt.case_id)
        committed_stage_id = self._next_stage_id(
            receipt.case_id,
            excluded=frozenset({prepared_stage_id}),
        )
        prepared_stage_name = f"{prepared_stage_id}{_STAGE_SUFFIX}"
        committed_stage_name = f"{committed_stage_id}{_COMMITTED_STAGE_SUFFIX}"
        layout = self._manager.layout(receipt.case_id)
        prepared_stage_path = layout.temp / prepared_stage_name
        committed_stage_path = layout.temp / committed_stage_name
        prepared_path = layout.journal / prepared_name
        committed_path = layout.journal / committed_name
        prepared_reservation: WorkspaceCapacityReservation | None = None
        committed_reservation: WorkspaceCapacityReservation | None = None
        authority: AuthenticatedCase | None = None
        journal_handle: WindowsHandle | None = None
        prepared_handle: WindowsHandle | None = None
        committed_stage_handle: WindowsHandle | None = None
        complete = False
        try:
            prepared_reservation = self._manager.reserve_capacity(
                receipt.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{prepared_stage_name}",
                is_directory=False,
                byte_count=len(prepared_payload),
            )
            committed_reservation = self._manager.reserve_capacity(
                receipt.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{committed_stage_name}",
                is_directory=False,
                byte_count=len(committed_payload),
            )
            authority = self._manager.authenticate_case(receipt.case_id, retain_lease=True)
            if os.path.lexists(prepared_path) or os.path.lexists(committed_path):
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.RECORD_MISMATCH,
                    "export_retry.collision",
                )
            journal_handle = open_handle(layout.journal, traverse=True)
            _authenticate_directory(journal_handle, layout.journal)
            with prepared_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=prepared_reservation.relative_parts,
                is_directory=False,
                byte_count=len(prepared_payload),
                entry_count=1,
            ):
                prepared_handle = write_new_file(prepared_stage_path, prepared_payload)
                _authenticate_payload_handle(
                    prepared_handle,
                    prepared_stage_path,
                    prepared_payload,
                    expected_case_id=receipt.case_id,
                    expected_export_id=receipt.export_id,
                    expected_phase=PdfExportRetryPhase.PREPARED,
                )
                if not _rename_to_exact_target(
                    prepared_handle,
                    journal_handle,
                    initial_path=prepared_stage_path,
                    destination=prepared_path,
                ):
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                        "export_retry.prepare_publish",
                    )
                _authenticate_payload_handle(
                    prepared_handle,
                    prepared_path,
                    prepared_payload,
                    expected_case_id=receipt.case_id,
                    expected_export_id=receipt.export_id,
                    expected_phase=PdfExportRetryPhase.PREPARED,
                )
            with committed_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=committed_reservation.relative_parts,
                is_directory=False,
                byte_count=len(committed_payload),
                entry_count=1,
            ):
                committed_stage_handle = write_new_file(
                    committed_stage_path,
                    committed_payload,
                )
                _authenticate_payload_handle(
                    committed_stage_handle,
                    committed_stage_path,
                    committed_payload,
                    expected_case_id=receipt.case_id,
                    expected_export_id=receipt.export_id,
                    expected_phase=PdfExportRetryPhase.COMMITTED,
                )
            lease = WindowsPreparedPdfExportRetry(
                receipt,
                authority,
                prepared_handle,
                committed_stage_handle,
                journal_handle,
                prepared_path,
                committed_stage_path,
                committed_path,
                prepared_payload,
                committed_payload,
                prepared_reservation,
                committed_reservation,
            )
            complete = True
            return lease
        except PdfExportHistoryError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error, context="export_retry.prepare_storage")
        except Exception:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_retry.prepare_storage",
            )
        finally:
            if not complete:
                _delete_exact_handle(prepared_handle)
                _delete_exact_handle(committed_stage_handle)
                _close_handle(journal_handle)
                if authority is not None:
                    _close_authority_no_raise(authority)
                if committed_reservation is not None:
                    _release_reservation_no_raise(committed_reservation)
                if prepared_reservation is not None:
                    _release_reservation_no_raise(prepared_reservation)

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        """Classify exact replayable and commit-ambiguous entries without mutation."""

        selected = _require_case_id(case_id, context="export_retry.inventory_case")
        try:
            with self._manager.authenticate_case(selected) as authority:
                with authority.transaction_guard():
                    names, invalid_name_ids, invalid = _retry_names(authority.layout.journal)
                    committed: dict[UUID, PdfExportHistoryRetryReceipt] = {}
                    prepared: dict[UUID, PdfExportHistoryRetryReceipt] = {}
                    invalid_ids: set[UUID] = set(invalid_name_ids)
                    for name, export_id, phase in names:
                        try:
                            entry = _read_entry(
                                authority.layout.journal / name,
                                selected,
                                export_id,
                                phase,
                            )
                        except PdfExportHistoryError, WorkspaceError:
                            invalid += 1
                            invalid_ids.add(export_id)
                            continue
                        if phase is PdfExportRetryPhase.COMMITTED:
                            if export_id in committed:
                                invalid += 1
                            else:
                                committed[export_id] = entry.receipt
                        else:
                            if export_id in prepared:
                                invalid += 1
                            else:
                                prepared[export_id] = entry.receipt
                    for export_id in set(committed).intersection(prepared):
                        if hmac.compare_digest(
                            committed[export_id].record_sha256,
                            prepared[export_id].record_sha256,
                        ):
                            prepared.pop(export_id)
                            continue
                        committed.pop(export_id)
                        prepared.pop(export_id)
                        invalid_ids.add(export_id)
                        invalid += 2
                    return PdfExportRetryInventory(
                        committed=tuple(committed.values()),
                        prepared=tuple(prepared.values()),
                        invalid_export_ids=tuple(invalid_ids),
                        invalid_entries=invalid,
                    )
        except PdfExportHistoryError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error, context="export_retry.inventory_storage")
        except Exception:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_retry.inventory_storage",
            )

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        """Delete an exact committed marker and any duplicate prepared marker."""

        selected = _require_case_id(case_id, context="export_retry.discard_case")
        selected_export = _require_case_id(export_id, context="export_retry.discard_export")
        if type(record_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", record_sha256):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.discard_digest",
            )
        layout = self._manager.layout(selected)
        committed_path = layout.journal / _phase_name(
            selected_export,
            PdfExportRetryPhase.COMMITTED,
        )
        prepared_path = layout.journal / _phase_name(
            selected_export,
            PdfExportRetryPhase.PREPARED,
        )
        try:
            with self._manager.authenticate_case(selected) as authority:
                with authority.transaction_guard():
                    if not os.path.lexists(committed_path):
                        return not os.path.lexists(prepared_path)
                    if os.path.lexists(prepared_path):
                        try:
                            prepared = _open_exact_entry_for_delete(
                                prepared_path,
                                selected,
                                selected_export,
                                PdfExportRetryPhase.PREPARED,
                                record_sha256,
                            )
                        except PdfExportHistoryError, WorkspaceError:
                            return False
                        if not _delete_exact_handle(prepared):
                            return False
                    committed = _open_exact_entry_for_delete(
                        committed_path,
                        selected,
                        selected_export,
                        PdfExportRetryPhase.COMMITTED,
                        record_sha256,
                    )
                    if not _delete_exact_handle(committed):
                        return False
                    return True
        except PdfExportHistoryError:
            raise
        except WorkspaceError:
            return False
        except Exception:
            return False

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        """Delete one exact inert marker after independent downstream proof."""

        return self._discard_phase(
            case_id,
            export_id,
            record_sha256,
            PdfExportRetryPhase.PREPARED,
        )

    def _discard_phase(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
        phase: PdfExportRetryPhase,
    ) -> bool:
        selected = _require_case_id(case_id, context="export_retry.discard_case")
        selected_export = _require_case_id(export_id, context="export_retry.discard_export")
        if type(record_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", record_sha256):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.discard_digest",
            )
        path = self._manager.layout(selected).journal / _phase_name(selected_export, phase)
        try:
            with self._manager.authenticate_case(selected) as authority:
                with authority.transaction_guard():
                    if not os.path.lexists(path):
                        return True
                    handle = _open_exact_entry_for_delete(
                        path,
                        selected,
                        selected_export,
                        phase,
                        record_sha256,
                    )
                    return _delete_exact_handle(handle)
        except PdfExportHistoryError:
            raise
        except WorkspaceError:
            return False
        except Exception:
            return False

    def _next_stage_id(
        self,
        case_id: UUID,
        *,
        excluded: frozenset[UUID] = frozenset(),
    ) -> UUID:
        layout = self._manager.layout(case_id)
        for _attempt in range(_MAXIMUM_STAGE_ID_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.STORAGE_FAILED,
                    "export_retry.stage_id",
                )
            if type(value) is not UUID or value.version != 4 or value in excluded:
                continue
            paths = (
                layout.temp / f"{value}{_STAGE_SUFFIX}",
                layout.temp / f"{value}{_COMMITTED_STAGE_SUFFIX}",
            )
            if not any(os.path.lexists(path) for path in paths):
                return value
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.stage_id",
        )


def _phase_name(export_id: UUID, phase: PdfExportRetryPhase) -> str:
    suffix = _PREPARED_SUFFIX if phase is PdfExportRetryPhase.PREPARED else _COMMITTED_SUFFIX
    return f"{_NAME_PREFIX}{export_id}{suffix}"


def _retry_names(
    path: Path,
) -> tuple[
    tuple[tuple[str, UUID, PdfExportRetryPhase], ...],
    tuple[UUID, ...],
    int,
]:
    selected: list[tuple[str, UUID, PdfExportRetryPhase]] = []
    invalid_ids: set[UUID] = set()
    invalid_entries = 0
    try:
        examined = 0
        owned = 0
        with os.scandir(path) as iterator:
            for item in iterator:
                examined += 1
                if examined > MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES + 16:
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
                        "export_retry.inventory_limit",
                    )
                match = _RETRY_NAME.fullmatch(item.name)
                if match is None:
                    if not item.name.casefold().startswith(_NAME_PREFIX):
                        continue
                    owned += 1
                    if owned > MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES:
                        reject_pdf_export_history(
                            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
                            "export_retry.inventory_limit",
                        )
                    invalid_entries += 1
                    owned_id = _OWNED_ID_PREFIX.match(item.name)
                    if owned_id is not None:
                        invalid_ids.add(UUID(owned_id.group("id")))
                    continue
                owned += 1
                if owned > MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES:
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
                        "export_retry.inventory_limit",
                    )
                export_id = UUID(match.group("id"))
                phase = PdfExportRetryPhase(match.group("phase"))
                selected.append((item.name, export_id, phase))
    except PdfExportHistoryError:
        raise
    except OSError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.inventory_scan",
        )
    selected.sort(key=lambda item: item[0])
    return (
        tuple(selected),
        tuple(sorted(invalid_ids, key=str)),
        invalid_entries,
    )


def _read_entry(
    path: Path,
    case_id: UUID,
    export_id: UUID,
    phase: PdfExportRetryPhase,
) -> PdfExportRetryJournalEntry:
    with open_handle(path, read=True) as handle:
        _authenticate_regular(handle, path)
        payload = handle.read_bytes(maximum_bytes=MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES)
        _authenticate_regular(handle, path)
    return decode_pdf_export_retry_entry(
        payload,
        expected_case_id=case_id,
        expected_export_id=export_id,
        expected_phase=phase,
    )


def _open_exact_entry_for_delete(
    path: Path,
    case_id: UUID,
    export_id: UUID,
    phase: PdfExportRetryPhase,
    record_sha256: str,
) -> WindowsHandle:
    handle: WindowsHandle | None = None
    try:
        handle = open_handle(path, read=True, delete=True)
        _authenticate_regular(handle, path)
        payload = handle.read_bytes(maximum_bytes=MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES)
        _authenticate_regular(handle, path)
        entry = decode_pdf_export_retry_entry(
            payload,
            expected_case_id=case_id,
            expected_export_id=export_id,
            expected_phase=phase,
        )
        if not hmac.compare_digest(entry.receipt.record_sha256, record_sha256):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_retry.discard_mismatch",
            )
        return handle
    except BaseException:
        _close_handle(handle)
        raise


def _authenticate_payload_handle(
    handle: WindowsHandle,
    path: Path,
    payload: bytes,
    *,
    expected_case_id: UUID,
    expected_export_id: UUID,
    expected_phase: PdfExportRetryPhase,
) -> None:
    _authenticate_regular(handle, path)
    current = handle.info
    if current.logical_size != len(payload):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.payload_size",
        )
    try:
        with reopen_read_only_file(handle) as reader:
            observed = reader.read_bytes(maximum_bytes=MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES)
    except WorkspaceError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.payload_read",
        )
    _authenticate_regular(handle, path)
    if not hmac.compare_digest(observed, payload):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.payload_mismatch",
        )
    decode_pdf_export_retry_entry(
        observed,
        expected_case_id=expected_case_id,
        expected_export_id=expected_export_id,
        expected_phase=expected_phase,
    )


def _authenticate_regular(handle: WindowsHandle, path: Path) -> None:
    current = handle.refresh_info()
    require_expected_handle(handle, path, directory=False)
    if current.is_reparse_point or current.is_sparse or current.link_count != 1:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.identity",
        )
    require_no_alternate_streams(current.final_path)


def _authenticate_directory(handle: WindowsHandle, path: Path) -> None:
    current = handle.refresh_info()
    require_expected_handle(handle, path, directory=True)
    if current.is_reparse_point:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_retry.journal_identity",
        )
    verify_private_acl(handle)
    require_no_alternate_streams(current.final_path)


def _rename_to_exact_target(
    handle: WindowsHandle,
    destination_parent: WindowsHandle,
    *,
    initial_path: Path,
    destination: Path,
) -> bool:
    identity = handle.info.identity
    delayed = False
    try:
        renamed, _error = rename_open_file_no_replace_at(
            handle,
            destination_parent,
            destination_name=destination.name,
        )
    except BaseException:
        renamed = False
        delayed = True
    try:
        current = handle.refresh_info()
        _authenticate_regular(handle, current.final_path)
    except BaseException:
        return False
    if current.identity != identity:
        return False
    if _same_path(current.final_path, destination):
        return True
    if _same_path(current.final_path, initial_path):
        return False
    return False if delayed or not renamed else False


def _delete_exact_handle(handle: WindowsHandle | None) -> bool:
    if handle is None:
        return True
    try:
        handle.mark_for_deletion()
        handle.close()
        return True
    except BaseException:
        _close_handle(handle)
        return False


def _close_handle(handle: WindowsHandle | None) -> None:
    if handle is None:
        return
    try:
        handle.close()
    except BaseException:
        return


def _release_reservation_no_raise(
    reservation: WorkspaceCapacityReservation,
) -> bool:
    for _attempt in range(2):
        try:
            reservation.release()
            return not reservation.is_active
        except BaseException:
            continue
    return False


def _close_authority_no_raise(authority: AuthenticatedCase) -> bool:
    for _attempt in range(2):
        try:
            authority.close()
            return not authority.is_open
        except BaseException:
            continue
    return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _require_case_id(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_pdf_export_history(PdfExportHistoryErrorCode.MODEL_INVALID, context)
    return value


def _map_workspace_error(error: WorkspaceError, *, context: str) -> Never:
    if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED:
        reject_pdf_export_history(PdfExportHistoryErrorCode.CAPACITY_EXCEEDED, context)
    if error.code in {
        WorkspaceErrorCode.ALIAS_DETECTED,
        WorkspaceErrorCode.REPARSE_POINT,
        WorkspaceErrorCode.IDENTITY_CHANGED,
        WorkspaceErrorCode.PERMISSIONS_INSECURE,
    }:
        reject_pdf_export_history(PdfExportHistoryErrorCode.DOCUMENT_INVALID, context)
    reject_pdf_export_history(PdfExportHistoryErrorCode.STORAGE_FAILED, context)
