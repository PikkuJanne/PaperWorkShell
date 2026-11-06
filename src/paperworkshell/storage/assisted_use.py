"""Bounded, authenticated Windows persistence for assisted-case access policy."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from paperworkshell.domain.assisted_use import (
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseRecord,
    record_from_document,
    record_to_document,
    reject_assisted_use,
    require_assisted_case_id,
)
from paperworkshell.storage._windows import (
    ManifestReplaceState,
    WindowsHandle,
    open_handle,
    publish_assisted_use_record_atomically,
    reopen_read_only_file,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import CaseWorkspaceManager

ASSISTED_USE_RECORD_NAME: Final = "assisted-use-v1.json"
MAX_ASSISTED_USE_RECORD_BYTES: Final = 4096


def _record_payload(record: AssistedUseRecord) -> bytes:
    payload = (
        json.dumps(record_to_document(record), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_ASSISTED_USE_RECORD_BYTES:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.size")
    return payload


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.duplicate")
        result[key] = value
    return result


def _decode(payload: bytes, case_id: UUID) -> AssistedUseRecord:
    if len(payload) > MAX_ASSISTED_USE_RECORD_BYTES:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.size")
    try:
        document: object = json.loads(payload.decode("utf-8"), object_pairs_hook=_closed_pairs)
    except AssistedUseError:
        raise
    except ValueError, RecursionError:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.json")
    record = record_from_document(document)
    if record.case_id != case_id:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.case_identity")
    return record


def _authenticate_file(handle: WindowsHandle, path: Path) -> None:
    handle.refresh_info()
    require_expected_handle(handle, path, directory=False)
    if handle.info.link_count != 1 or handle.info.is_sparse:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.file_identity")
    require_no_alternate_streams(path)


def _read_record(handle: WindowsHandle, path: Path, case_id: UUID) -> AssistedUseRecord:
    _authenticate_file(handle, path)
    with reopen_read_only_file(handle) as reader:
        payload = reader.read_bytes(maximum_bytes=MAX_ASSISTED_USE_RECORD_BYTES)
    _authenticate_file(handle, path)
    return _decode(payload, case_id)


class WindowsAssistedUseStore:
    """Keep one fixed-size policy per case, without reading any document content.

    Existing settings and credential material cannot be replaced by a scheduler
    update. Persistent high-water time and expiry can only advance. The shared
    case transaction mutex serializes policy, manifest, cleanup and deletion work.
    """

    __slots__ = ("_id_factory", "_manager")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not isinstance(manager, CaseWorkspaceManager) or not callable(id_factory):
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.store")
        self._manager = manager
        self._id_factory = id_factory

    def __repr__(self) -> str:
        return "WindowsAssistedUseStore(<protected>)"

    def load(self, case_id: UUID) -> AssistedUseRecord | None:
        """Authenticate one exact policy, rejecting malformed or future records."""

        require_assisted_case_id(case_id)
        try:
            with self._manager.authenticate_case(case_id) as authority:
                with authority.transaction_guard():
                    path = authority.layout.journal / ASSISTED_USE_RECORD_NAME
                    with (
                        open_handle(authority.layout.case_root, traverse=True) as case_root,
                        open_handle(authority.layout.journal, traverse=True) as root,
                    ):
                        require_expected_handle(
                            case_root,
                            authority.layout.case_root,
                            directory=True,
                            expected_identity=authority.identity,
                        )
                        require_expected_handle(root, path.parent, directory=True)
                        verify_private_acl(root)
                        require_no_alternate_streams(path.parent)
                        if not os.path.lexists(path):
                            return None
                        with open_handle(path, read=True) as handle:
                            return _read_record(handle, path, case_id)
        except AssistedUseError:
            raise
        except Exception:
            reject_assisted_use(AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.load")

    def save(self, record: AssistedUseRecord) -> None:
        """Atomically create a consent record or advance its existing high-water."""

        if type(record) is not AssistedUseRecord:
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.save")
        payload = _record_payload(record)
        stage: WindowsHandle | None = None
        stage_path: Path | None = None
        try:
            stage_id = self._id_factory()
            if type(stage_id) is not UUID or stage_id.version != 4:
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted_use.stage")
            layout = self._manager.layout(record.case_id)
            stage_path = layout.temp / f"{stage_id}.tmp"
            with self._manager.reserve_capacity(
                record.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{stage_id}.tmp",
                is_directory=False,
                byte_count=len(payload),
            ) as reservation:
                with self._manager.authenticate_case(record.case_id) as authority:
                    with authority.transaction_guard(), ExitStack() as handles:
                        path = layout.journal / ASSISTED_USE_RECORD_NAME
                        case_root = handles.enter_context(
                            open_handle(layout.case_root, traverse=True)
                        )
                        require_expected_handle(
                            case_root,
                            layout.case_root,
                            directory=True,
                            expected_identity=authority.identity,
                        )
                        root = handles.enter_context(open_handle(layout.journal, traverse=True))
                        require_expected_handle(root, layout.journal, directory=True)
                        verify_private_acl(root)
                        require_no_alternate_streams(layout.journal)
                        temp_root = handles.enter_context(open_handle(layout.temp, traverse=True))
                        require_expected_handle(temp_root, layout.temp, directory=True)
                        verify_private_acl(temp_root)
                        require_no_alternate_streams(layout.temp)
                        expected: WindowsHandle | None = None
                        if os.path.lexists(path):
                            expected = handles.enter_context(
                                open_handle(path, read=True, share_delete=True)
                            )
                            previous = _read_record(expected, path, record.case_id)
                            if (
                                record.last_seen_at < previous.last_seen_at
                                or (previous.expired and not record.expired)
                                or replace(
                                    record,
                                    last_seen_at=previous.last_seen_at,
                                    expired=previous.expired,
                                )
                                != previous
                            ):
                                reject_assisted_use(
                                    AssistedUseErrorCode.INVALID, "assisted_use.stale_update"
                                )
                            if previous == record:
                                return
                        with reservation.publication_guard(
                            case_identity=authority.identity,
                            relative_parts=reservation.relative_parts,
                            is_directory=False,
                            byte_count=len(payload),
                            entry_count=1,
                        ):
                            stage = write_new_file(stage_path, payload)
                            if _read_record(stage, stage_path, record.case_id) != record:
                                reject_assisted_use(
                                    AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.stage_verify"
                                )
                            outcome = publish_assisted_use_record_atomically(
                                stage, authority.handle, root, expected
                            )
                            if outcome.state is not ManifestReplaceState.COMMITTED:
                                reject_assisted_use(
                                    AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.publish"
                                )
                            if _read_record(stage, path, record.case_id) != record:
                                reject_assisted_use(
                                    AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.verify"
                                )
        except AssistedUseError:
            raise
        except Exception:
            reject_assisted_use(AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.save")
        finally:
            if stage is not None:
                cleanup_failed = False
                try:
                    # Only an unpublished exact stage may be discarded. An
                    # ambiguous publication must retain its authoritative policy.
                    current = stage.refresh_info()
                    if current.final_path == stage_path:
                        stage.mark_for_deletion()
                except Exception:
                    cleanup_failed = True
                finally:
                    try:
                        stage.close()
                    except Exception:
                        cleanup_failed = True
                if cleanup_failed:
                    reject_assisted_use(
                        AssistedUseErrorCode.STORAGE_FAILED, "assisted_use.stage_cleanup"
                    )
