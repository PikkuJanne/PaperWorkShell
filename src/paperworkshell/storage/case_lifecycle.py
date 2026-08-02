"""Authenticated Windows storage adapter for local case lifecycle operations."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never, cast
from uuid import UUID, uuid4

from paperworkshell.domain.case_lifecycle import (
    CURRENT_CASE_MANIFEST_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    CaseDeletionConfirmation,
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseIndex,
    CaseIndexIssue,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
    CaseSummary,
    reject_case_lifecycle,
    require_case_id,
)
from paperworkshell.storage._windows import (
    WindowsHandle,
    open_handle,
    publish_new_file,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseRemovalStatus,
    CaseWorkspaceManager,
)

MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
MAX_JSON_DEPTH: Final = 64
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_CODEPOINTS: Final = 1_048_576
MAX_MANIFEST_COLLECTION_ITEMS: Final = 4096

_CASE_ID_RE: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_UTC_TIMESTAMP_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_MANIFEST_KEYS: Final = frozenset(
    {
        "schema_version",
        "case_id",
        "revision",
        "created_at",
        "updated_at",
        "template_ref",
        "sources",
        "pages",
        "page_sequence_base",
        "page_sequence",
        "page_operations",
        "assets",
        "overlays",
        "assignments",
        "constraints",
        "confirmations",
        "export_history",
    }
)
_COLLECTION_KEYS: Final = (
    "sources",
    "pages",
    "page_sequence_base",
    "page_sequence",
    "page_operations",
    "assets",
    "overlays",
    "assignments",
    "confirmations",
)


class _DuplicateJsonKey(ValueError):
    pass


class _UnsupportedJsonNumber(ValueError):
    pass


@dataclass(slots=True)
class WindowsOpenCaseLease:
    """Path-free application lease backed by retained authenticated directory handles."""

    _summary: CaseSummary
    _authority: AuthenticatedCase = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def summary(self) -> CaseSummary:
        return self._summary

    def close(self) -> None:
        """Release the retained case authority idempotently."""

        if self._closed:
            return
        try:
            self._authority.close()
        except WorkspaceError:
            reject_case_lifecycle(CaseLifecycleErrorCode.CLOSE_FAILED, "case.close_storage")
        self._closed = True


class WindowsCaseLifecycleStore:
    """Create, enumerate, open, and remove cases through one secure workspace manager."""

    __slots__ = ("_clock", "_manager")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._manager = manager
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_case(self) -> WindowsOpenCaseLease:
        """Publish a schema-valid empty manifest last, then retain the new case lease."""

        authority: AuthenticatedCase | None = None
        try:
            layout = self._manager.provision()
            authority = self._manager.authenticate_case(layout.case_id, retain_lease=True)
            payload = build_initial_manifest_bytes(layout.case_id, self._clock())
            stage_name = f"{uuid4()}.tmp"
            stage_path = layout.temp / stage_name
            relative_parts = ("temp", stage_name)
            reservation = self._manager.reserve_capacity(
                layout.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{stage_name}",
                is_directory=False,
                byte_count=len(payload),
            )
            with reservation:
                with open_handle(layout.temp) as temp_handle:
                    require_expected_handle(temp_handle, layout.temp, directory=True)
                    verify_private_acl(temp_handle)
                    require_no_alternate_streams(layout.temp)
                    with reservation.publication_guard(
                        case_identity=authority.identity,
                        relative_parts=relative_parts,
                        is_directory=False,
                        byte_count=len(payload),
                        entry_count=1,
                    ):
                        self._publish_initial_manifest(
                            stage_path,
                            layout.manifest,
                            payload,
                            authority,
                        )
            summary = _read_case_summary(authority)
            return WindowsOpenCaseLease(summary, authority)
        except CaseLifecycleError:
            if authority is not None:
                _close_authority_safely(authority)
            raise
        except WorkspaceError:
            if authority is not None:
                _close_authority_safely(authority)
            reject_case_lifecycle(CaseLifecycleErrorCode.CREATE_FAILED, "case.create_storage")
        except BaseException:
            if authority is not None:
                _close_authority_safely(authority)
            raise

    def list_cases(self) -> CaseIndex:
        """Return current lifecycle envelopes; incomplete entries remain unavailable."""

        try:
            case_ids = self._manager.enumerate_case_ids()
        except WorkspaceError:
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.list_storage")
        cases: list[CaseSummary] = []
        unavailable: list[CaseIndexIssue] = []
        for case_id in case_ids:
            try:
                with self._manager.authenticate_case(case_id) as authority:
                    cases.append(_read_case_summary(authority))
            except CaseLifecycleError as error:
                unavailable.append(CaseIndexIssue(case_id, error.code))
            except WorkspaceError:
                unavailable.append(CaseIndexIssue(case_id, CaseLifecycleErrorCode.MANIFEST_INVALID))
        return CaseIndex(tuple(cases), tuple(unavailable))

    def open_case(self, case_id: UUID) -> WindowsOpenCaseLease:
        """Validate the lifecycle envelope without mutating the manifest or case tree."""

        selected = require_case_id(case_id, context="case.open_storage_id")
        layout = self._manager.layout(selected)
        if not os.path.lexists(layout.case_root):
            reject_case_lifecycle(CaseLifecycleErrorCode.CASE_NOT_FOUND, "case.open_missing")
        authority: AuthenticatedCase | None = None
        try:
            authority = self._manager.authenticate_case(selected, retain_lease=True)
            summary = _read_case_summary(authority)
            return WindowsOpenCaseLease(summary, authority)
        except CaseLifecycleError:
            if authority is not None:
                _close_authority_safely(authority)
            raise
        except WorkspaceError:
            if authority is not None:
                _close_authority_safely(authority)
            reject_case_lifecycle(CaseLifecycleErrorCode.OPEN_FAILED, "case.open_storage")
        except BaseException:
            if authority is not None:
                _close_authority_safely(authority)
            raise

    def delete_case(
        self,
        case_id: UUID,
        confirmation: CaseDeletionConfirmation,
    ) -> CaseDeletionResult:
        """Validate version and identity before authenticated manifest-first removal."""

        selected = require_case_id(case_id, context="case.delete_storage_id")
        if confirmation is not CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.DELETE_CONFIRMATION_REQUIRED,
                "case.delete_storage_confirmation",
            )
        layout = self._manager.layout(selected)
        if not os.path.lexists(layout.case_root):
            reject_case_lifecycle(CaseLifecycleErrorCode.CASE_NOT_FOUND, "case.delete_missing")
        try:
            with self._manager.authenticate_case(selected, for_deletion=True) as authority:
                report = self._manager.remove_case(
                    authority,
                    manifest_validator=lambda payload: _validate_case_manifest_payload(
                        payload, selected
                    ),
                    maximum_manifest_bytes=MAX_MANIFEST_BYTES,
                )
        except CaseLifecycleError:
            raise
        except WorkspaceError as error:
            code = (
                CaseLifecycleErrorCode.DELETE_ACTIVE_CASE
                if error.context in {"case.active", "case.deleting"}
                else CaseLifecycleErrorCode.DELETE_FAILED
            )
            reject_case_lifecycle(code, "case.delete_storage")
        return CaseDeletionResult(
            case_id=selected,
            status=(
                CaseDeletionStatus.COMPLETE
                if report.status is CaseRemovalStatus.COMPLETE
                else CaseDeletionStatus.PARTIAL
            ),
            removed_files=report.removed_files,
            removed_directories=report.removed_directories,
            released_bytes=report.released_bytes,
            failure_code=(
                None
                if report.status is CaseRemovalStatus.COMPLETE
                else CaseLifecycleErrorCode.DELETE_FAILED
            ),
        )

    @staticmethod
    def _publish_initial_manifest(
        stage_path: Path,
        destination: Path,
        payload: bytes,
        authority: AuthenticatedCase,
    ) -> None:
        stage_handle: WindowsHandle | None = None
        published = False
        try:
            stage_handle = write_new_file(stage_path, payload)
            publish_new_file(stage_handle, authority.handle, destination=destination)
            published = True
        finally:
            if stage_handle is not None:
                if not published:
                    try:
                        stage_handle.mark_for_deletion()
                    except WorkspaceError:
                        pass
                stage_handle.close()


def build_initial_manifest_bytes(case_id: UUID, timestamp: datetime) -> bytes:
    """Build the exact empty v1 manifest using one canonical UTC lifecycle timestamp."""

    selected = require_case_id(case_id, context="case.initial_id")
    rendered_timestamp = _render_utc_timestamp(timestamp)
    document: dict[str, object] = {
        "schema_version": CURRENT_CASE_MANIFEST_VERSION,
        "case_id": str(selected),
        "revision": 0,
        "created_at": rendered_timestamp,
        "updated_at": rendered_timestamp,
        "template_ref": None,
        "sources": [],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _read_case_summary(authority: AuthenticatedCase) -> CaseSummary:
    path = authority.layout.manifest
    if not os.path.lexists(path):
        reject_case_lifecycle(CaseLifecycleErrorCode.CASE_INCOMPLETE, "case.manifest_missing")
    _verify_case_skeleton(authority)
    try:
        with open_handle(path, read=True) as handle:
            require_expected_handle(handle, path, directory=False)
            if handle.info.link_count != 1:
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.MANIFEST_INVALID,
                    "case.manifest_links",
                )
            require_no_alternate_streams(path)
            payload = handle.read_bytes(maximum_bytes=MAX_MANIFEST_BYTES)
    except CaseLifecycleError:
        raise
    except WorkspaceError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_read")
    document = _decode_manifest(payload)
    return _summary_from_manifest(document, authority.case_id)


def _verify_case_skeleton(authority: AuthenticatedCase) -> None:
    expected_top_level = {"journal", "originals", "cache", "temp", "manifest.json"}
    optional_top_level = {"export-history.json"}
    try:
        names: list[str] = []
        with os.scandir(authority.layout.case_root) as iterator:
            for entry in iterator:
                if len(names) >= len(expected_top_level | optional_top_level):
                    reject_case_lifecycle(
                        CaseLifecycleErrorCode.MANIFEST_INVALID,
                        "case.layout_entries",
                    )
                names.append(entry.name)
    except CaseLifecycleError:
        raise
    except OSError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.layout_scan")
    present = set(names)
    if not expected_top_level.issubset(present) or not present.issubset(
        expected_top_level | optional_top_level
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.layout_members")
    for directory in authority.layout.directories[1:]:
        try:
            with open_handle(directory) as handle:
                require_expected_handle(handle, directory, directory=True)
                verify_private_acl(handle)
                require_no_alternate_streams(directory)
        except WorkspaceError:
            reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.layout_directory")
    if "export-history.json" in present:
        export_history = authority.layout.export_history
        try:
            with open_handle(export_history) as handle:
                require_expected_handle(handle, export_history, directory=False)
                if handle.info.link_count != 1:
                    reject_case_lifecycle(
                        CaseLifecycleErrorCode.MANIFEST_INVALID,
                        "case.export_history_links",
                    )
                require_no_alternate_streams(export_history)
        except WorkspaceError:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.export_history_record",
            )


def _close_authority_safely(authority: AuthenticatedCase) -> None:
    try:
        authority.close()
    except Exception:
        return


def _decode_manifest(payload: bytes) -> dict[str, object]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_utf8")
    if text.startswith("\ufeff"):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_bom")
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (
        _DuplicateJsonKey,
        _UnsupportedJsonNumber,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_json")
    if type(loaded) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_object")
    document = cast(dict[str, object], loaded)
    _validate_json_resources(document)
    return document


def _validate_case_manifest_payload(payload: bytes, expected_case_id: UUID) -> None:
    _summary_from_manifest(_decode_manifest(payload), expected_case_id)


def _summary_from_manifest(document: dict[str, object], expected_case_id: UUID) -> CaseSummary:
    version = document.get("schema_version")
    if type(version) is not int:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_version")
    if version != CURRENT_CASE_MANIFEST_VERSION:
        reject_case_lifecycle(
            CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED,
            "case.manifest_version",
        )
    if frozenset(document) != _MANIFEST_KEYS:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_members")
    raw_case_id = document["case_id"]
    if type(raw_case_id) is not str or _CASE_ID_RE.fullmatch(raw_case_id) is None:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_id")
    parsed_case_id = UUID(raw_case_id)
    if parsed_case_id != expected_case_id:
        reject_case_lifecycle(
            CaseLifecycleErrorCode.MANIFEST_CASE_ID_MISMATCH,
            "case.manifest_id",
        )
    revision = document["revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_SAFE_MANIFEST_INTEGER:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_revision")
    for key in _COLLECTION_KEYS:
        value = document[key]
        if type(value) is not list or len(value) > MAX_MANIFEST_COLLECTION_ITEMS:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_collections",
            )
    if document["template_ref"] is not None and type(document["template_ref"]) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_template")
    if type(document["constraints"]) is not dict or type(document["export_history"]) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_records")
    return CaseSummary(
        case_id=parsed_case_id,
        manifest_version=version,
        revision=revision,
        created_at=_parse_utc_timestamp(document["created_at"]),
        updated_at=_parse_utc_timestamp(document["updated_at"]),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise _UnsupportedJsonNumber


def _reject_float(_value: str) -> Never:
    raise _UnsupportedJsonNumber


def _validate_json_resources(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_resources",
            )
        if type(value) is dict:
            mapping = cast(dict[str, object], value)
            if len(mapping) > MAX_JSON_NODES - nodes - len(stack):
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.MANIFEST_INVALID,
                    "case.manifest_resources",
                )
            for key, child in mapping.items():
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    reject_case_lifecycle(
                        CaseLifecycleErrorCode.MANIFEST_INVALID,
                        "case.manifest_resources",
                    )
                stack.append((child, depth + 1))
        elif type(value) is list:
            items = cast(list[object], value)
            if len(items) > MAX_JSON_NODES - nodes - len(stack):
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.MANIFEST_INVALID,
                    "case.manifest_resources",
                )
            stack.extend((child, depth + 1) for child in items)
        elif type(value) is str and len(value) > MAX_JSON_STRING_CODEPOINTS:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_resources",
            )


def _parse_utc_timestamp(value: object) -> datetime:
    if type(value) is not str or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    return parsed


def _render_utc_timestamp(value: datetime) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.CREATE_FAILED, "case.initial_timestamp")
    normalized = value.astimezone(UTC)
    timespec = "seconds" if normalized.microsecond == 0 else "microseconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")
