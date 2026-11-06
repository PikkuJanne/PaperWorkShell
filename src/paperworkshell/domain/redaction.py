"""Inert, bounded permanent-removal intent, separate from visual overlays.

Only unapplied intent is serializable. The state vocabulary anticipates the
separately gated derivative pipeline; this module cannot apply or verify marks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Self, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestRepositoryError,
    copy_manifest_document,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox, PageCoordinateError

REDACTION_MARK_VERSION: Final = 1
MAX_REDACTION_MARKS: Final = 4096
PERMANENT_REDACTION_ENABLED: Final = True
_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_MARK_MEMBERS: Final = frozenset({"mark_id", "mark_version", "page_id", "box", "state"})


class RedactionErrorCode(StrEnum):
    MODEL_INVALID = "REDACTION_MODEL_INVALID"
    MANIFEST_INVALID = "REDACTION_MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "REDACTION_VERSION_UNSUPPORTED"
    CAPACITY_EXCEEDED = "REDACTION_CAPACITY_EXCEEDED"
    IDENTIFIER_INVALID = "REDACTION_IDENTIFIER_INVALID"
    IDENTIFIER_COLLISION = "REDACTION_IDENTIFIER_COLLISION"
    PAGE_UNKNOWN = "REDACTION_PAGE_UNKNOWN"
    BOX_INVALID = "REDACTION_BOX_INVALID"
    STATE_INVALID = "REDACTION_STATE_INVALID"
    PIPELINE_DISABLED = "REDACTION_PIPELINE_DISABLED"
    RECORD_UNKNOWN = "REDACTION_RECORD_UNKNOWN"
    CONCURRENT_CHANGE = "REDACTION_CONCURRENT_CHANGE"


_SAFE_MESSAGES: Final = {
    RedactionErrorCode.MODEL_INVALID: "PaperWorkShell received invalid removal-mark data.",
    RedactionErrorCode.MANIFEST_INVALID: "The saved removal marks are invalid and were not changed.",
    RedactionErrorCode.VERSION_UNSUPPORTED: "This removal-mark version is not supported.",
    RedactionErrorCode.CAPACITY_EXCEEDED: "The case has reached the supported removal-mark limit.",
    RedactionErrorCode.IDENTIFIER_INVALID: "A removal mark has an invalid internal identifier.",
    RedactionErrorCode.IDENTIFIER_COLLISION: "A removal mark conflicts with another case identifier.",
    RedactionErrorCode.PAGE_UNKNOWN: "A removal mark refers to a page that is not in this case.",
    RedactionErrorCode.BOX_INVALID: "Choose a nonempty removal area inside the normalized page.",
    RedactionErrorCode.STATE_INVALID: "A saved removal mark can contain only unapplied intent.",
    RedactionErrorCode.PIPELINE_DISABLED: (
        "Permanent removal is unavailable until its security gate is approved. "
        "No information has been removed."
    ),
    RedactionErrorCode.RECORD_UNKNOWN: "The selected removal mark is no longer available.",
    RedactionErrorCode.CONCURRENT_CHANGE: (
        "The removal marks changed before this edit could be saved. Try the edit again."
    ),
}


@dataclass(slots=True, repr=False)
class RedactionError(ValueError):
    """Closed, sanitized failure without content, paths, IDs, or coordinates."""

    code: RedactionErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not RedactionErrorCode:
            self.code = RedactionErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "redaction.model"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"

    def __repr__(self) -> str:
        return f"RedactionError(code={self.code.value!r}, context={self.context!r})"


def reject_redaction(code: RedactionErrorCode, context: str) -> Never:
    raise RedactionError(code, context)


class RedactionMarkState(StrEnum):
    """Review vocabulary; applied and verified states require future pipeline evidence."""

    UNAPPLIED = "unapplied"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"

    @property
    def display_label(self) -> str:
        return _STATE_LABELS[self]

    @property
    def description(self) -> str:
        return _STATE_DESCRIPTIONS[self]


_STATE_LABELS: Final = {
    RedactionMarkState.UNAPPLIED: "Unapplied — information is still present",
    RedactionMarkState.APPLIED: "Applied — derivative is awaiting verification",
    RedactionMarkState.VERIFIED: "Verified — derivative passed removal checks",
    RedactionMarkState.FAILED: "Failed — derivative must not be exported",
}
_STATE_DESCRIPTIONS: Final = {
    RedactionMarkState.UNAPPLIED: "This is a review mark. No information has been removed.",
    RedactionMarkState.APPLIED: (
        "The future pipeline has written a derivative, but verification is incomplete."
    ),
    RedactionMarkState.VERIFIED: (
        "The future pipeline has verified a specific derivative. This does not alter the original."
    ),
    RedactionMarkState.FAILED: (
        "The future pipeline could not verify removal. Export of that derivative is blocked."
    ),
}


def transition_redaction_state(
    current: RedactionMarkState,
    target: RedactionMarkState,
) -> RedactionMarkState:
    """Fail closed for every pipeline state while the security gate is closed."""

    if type(current) is not RedactionMarkState or type(target) is not RedactionMarkState:
        reject_redaction(RedactionErrorCode.STATE_INVALID, "redaction.transition")
    if current is not RedactionMarkState.UNAPPLIED or target is not RedactionMarkState.UNAPPLIED:
        reject_redaction(RedactionErrorCode.PIPELINE_DISABLED, "redaction.transition")
    return RedactionMarkState.UNAPPLIED


@dataclass(frozen=True, slots=True, repr=False)
class RedactionMark:
    """One full-page normalized region intended for future permanent removal."""

    mark_id: UUID
    page_id: UUID
    box: NormalizedPageBox
    mark_version: int = REDACTION_MARK_VERSION

    def __post_init__(self) -> None:
        _require_uuid(self.mark_id)
        _require_uuid(self.page_id)
        if type(self.box) is not NormalizedPageBox:
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.box")
        try:
            NormalizedPageBox.from_manifest(self.box.to_manifest())
        except PageCoordinateError:
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.box")
        if type(self.mark_version) is not int or self.mark_version != REDACTION_MARK_VERSION:
            reject_redaction(RedactionErrorCode.VERSION_UNSUPPORTED, "redaction.mark_version")

    @property
    def state(self) -> RedactionMarkState:
        return RedactionMarkState.UNAPPLIED

    def __repr__(self) -> str:
        return "RedactionMark(state='unapplied')"

    def to_manifest_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "mark_id": str(self.mark_id),
            "mark_version": self.mark_version,
            "page_id": str(self.page_id),
            "box": self.box.to_manifest(),
            "state": self.state.value,
        }

    @classmethod
    def from_manifest_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _MARK_MEMBERS:
            reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.record")
        if value.get("state") != RedactionMarkState.UNAPPLIED.value:
            reject_redaction(RedactionErrorCode.STATE_INVALID, "redaction.state")
        version = value.get("mark_version")
        if type(version) is not int or version != REDACTION_MARK_VERSION:
            reject_redaction(RedactionErrorCode.VERSION_UNSUPPORTED, "redaction.mark_version")
        raw_box = value.get("box")
        if not isinstance(raw_box, Mapping):
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.box")
        try:
            box = NormalizedPageBox.from_manifest(raw_box)
        except PageCoordinateError:
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.box")
        return cls(_parse_uuid(value.get("mark_id")), _parse_uuid(value.get("page_id")), box)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionMarkCollection:
    """Bounded distinct marks; manifest binding additionally checks the case graph."""

    marks: tuple[RedactionMark, ...] = ()

    def __post_init__(self) -> None:
        if type(self.marks) is not tuple:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.collection")
        if len(self.marks) > MAX_REDACTION_MARKS:
            reject_redaction(RedactionErrorCode.CAPACITY_EXCEEDED, "redaction.collection")
        if any(type(mark) is not RedactionMark for mark in self.marks):
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.collection")
        for mark in self.marks:
            mark.__post_init__()
        if len({mark.mark_id for mark in self.marks}) != len(self.marks):
            reject_redaction(RedactionErrorCode.IDENTIFIER_COLLISION, "redaction.mark_id")

    def __repr__(self) -> str:
        return f"RedactionMarkCollection(mark_count={len(self.marks)})"

    @classmethod
    def from_manifest_document(cls, document: Mapping[str, object]) -> Self:
        copied = _copy_manifest(document)
        records = copied.get("redaction_marks")
        if type(records) is not list:
            reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.collection")
        if len(records) > MAX_REDACTION_MARKS:
            reject_redaction(RedactionErrorCode.CAPACITY_EXCEEDED, "redaction.collection")
        collection = cls(tuple(RedactionMark.from_manifest_record(record) for record in records))
        collection._validate_graph(copied)
        return collection

    def to_manifest_document(self, document: Mapping[str, object]) -> dict[str, object]:
        self.__post_init__()
        copied = _copy_manifest(document)
        self._validate_graph(copied)
        copied["redaction_marks"] = [mark.to_manifest_record() for mark in self.marks]
        return copied

    def _validate_graph(self, document: Mapping[str, object]) -> None:
        namespace = {_parse_uuid(document.get("case_id"))}
        known_pages: set[UUID] = set()
        for key, id_field in (
            ("sources", "source_id"),
            ("pages", "page_id"),
            ("page_operations", "operation_id"),
            ("assets", "asset_id"),
            ("overlays", "overlay_id"),
            ("assignments", "assignment_id"),
            ("confirmations", "confirmation_id"),
        ):
            records = document.get(key)
            if type(records) is not list:
                reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.references")
            for record in records:
                if not isinstance(record, Mapping):
                    reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.references")
                identity = _parse_uuid(record.get(id_field))
                namespace.add(identity)
                if key == "pages":
                    known_pages.add(identity)
                elif key == "page_operations":
                    payload = record.get("payload")
                    if isinstance(payload, Mapping) and payload.get("kind") == "set_group":
                        namespace.add(_parse_uuid(payload.get("group_id")))
        history = document.get("export_history")
        if not isinstance(history, Mapping) or type(history.get("entries")) is not list:
            reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.references")
        for entry in cast(list[object], history["entries"]):
            if not isinstance(entry, Mapping):
                reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.references")
            namespace.add(_parse_uuid(entry.get("export_id")))
        for mark in self.marks:
            if mark.mark_id in namespace:
                reject_redaction(RedactionErrorCode.IDENTIFIER_COLLISION, "redaction.mark_id")
            if mark.page_id not in known_pages:
                reject_redaction(RedactionErrorCode.PAGE_UNKNOWN, "redaction.page_id")
            namespace.add(mark.mark_id)


def require_redaction_export_allowed(document: Mapping[str, object]) -> None:
    """Block export while any active page has unapplied permanent-removal intent.

    Old supported manifests have no removal collection. Current manifests must
    parse that collection before considering active-page membership. No flag or
    state spelling can unlock the unavailable permanent-removal pipeline.
    """

    version = document.get("schema_version")
    if type(version) is int and version in {1, 2}:
        if "redaction_marks" in document:
            reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.export")
        return
    collection = RedactionMarkCollection.from_manifest_document(document)
    sequence = document.get("page_sequence")
    if type(sequence) is not list:
        reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.export")
    active_ids = {_parse_uuid(value) for value in sequence}
    if any(mark.page_id in active_ids for mark in collection.marks):
        reject_redaction(RedactionErrorCode.PIPELINE_DISABLED, "redaction.export")


def _copy_manifest(document: Mapping[str, object]) -> dict[str, object]:
    try:
        copied = copy_manifest_document(document)
    except ManifestRepositoryError:
        reject_redaction(RedactionErrorCode.MANIFEST_INVALID, "redaction.manifest")
    version = copied.get("schema_version")
    if type(version) is not int or version != CURRENT_MANIFEST_SCHEMA_VERSION:
        reject_redaction(RedactionErrorCode.VERSION_UNSUPPORTED, "redaction.manifest_version")
    return copied


def _require_uuid(value: object) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_redaction(RedactionErrorCode.IDENTIFIER_INVALID, "redaction.identifier")
    return value


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        reject_redaction(RedactionErrorCode.IDENTIFIER_INVALID, "redaction.identifier")
    try:
        parsed = UUID(value)
    except ValueError:
        reject_redaction(RedactionErrorCode.IDENTIFIER_INVALID, "redaction.identifier")
    if str(parsed) != value:
        reject_redaction(RedactionErrorCode.IDENTIFIER_INVALID, "redaction.identifier")
    return _require_uuid(parsed)
