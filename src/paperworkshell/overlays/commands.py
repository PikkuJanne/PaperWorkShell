"""Pure bounded commands and session history for visual-overlay intent.

The released manifest wire stores the resulting ``overlays`` array, not a
second durable operation stream.  This module therefore keeps reversible
session deltas in memory and replaces only that array when it constructs a
candidate for the existing atomic manifest repository.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MAX_OVERLAYS,
    MarkOverlayPayload,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    TextualOverlayPayload,
    reject_overlay,
)

MAX_OVERLAY_COMMANDS: Final = 4_096
MAX_OVERLAY_ID_GENERATION_ATTEMPTS: Final = 32
DEFAULT_CORE_FONT_SIZE_POINTS: Final = 12
DEFAULT_CORE_COLOR_VALUE: Final = "#000000ff"
CORE_OVERLAY_KINDS: Final = frozenset(
    {
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.INITIALS,
    }
)
EDITOR_GEOMETRY_OVERLAY_KINDS: Final = frozenset(OverlayKind)

_TEXTUAL_CORE_KINDS: Final = frozenset({OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS})
_MARK_CORE_KINDS: Final = frozenset({OverlayKind.CHECKMARK, OverlayKind.CROSS})
_UUID_V4_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_IDENTITY_COLLECTIONS: Final = (
    ("sources", "source_id"),
    ("pages", "page_id"),
    ("page_operations", "operation_id"),
    ("assets", "asset_id"),
    ("assignments", "assignment_id"),
    ("confirmations", "confirmation_id"),
)


def default_core_overlay_payload(
    kind: OverlayKind,
    *,
    value: str | None = None,
) -> TextualOverlayPayload | MarkOverlayPayload:
    """Build one core payload without interpreting a user-entered value.

    In particular, a date remains the exact display string supplied by the
    caller.  No calendar, locale, trimming, or normalization rule runs here.
    """

    if type(kind) is not OverlayKind or kind not in CORE_OVERLAY_KINDS:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.command_kind")
    if kind in _TEXTUAL_CORE_KINDS:
        selected_value = "" if value is None else value
        if type(selected_value) is not str:
            reject_overlay(OverlayErrorCode.TEXT_INVALID, "overlay.command_value")
        return TextualOverlayPayload(
            kind,
            selected_value,
            DEFAULT_FONT_TOKEN,
            DEFAULT_CORE_FONT_SIZE_POINTS,
            RgbaColor(DEFAULT_CORE_COLOR_VALUE),
        )
    if value is not None:
        reject_overlay(OverlayErrorCode.EDIT_KIND_MISMATCH, "overlay.command_value")
    return MarkOverlayPayload(kind, RgbaColor(DEFAULT_CORE_COLOR_VALUE))


@dataclass(frozen=True, slots=True, repr=False)
class CreateCoreOverlayCommand:
    """Create one of the five core form-marking overlays."""

    kind: OverlayKind
    page_id: UUID
    box: NormalizedPageBox
    value: str | None = None

    def __post_init__(self) -> None:
        _require_uuid_model(self.page_id, context="overlay.command_page")
        if type(self.box) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.command_box")
        default_core_overlay_payload(self.kind, value=self.value)

    def __repr__(self) -> str:
        kind = self.kind.value if type(self.kind) is OverlayKind else "invalid"
        return f"CreateCoreOverlayCommand(kind={kind!r}, details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class ReplaceOverlayBoxCommand:
    """Replace one complete full-page normalized box for move or resize."""

    overlay_id: UUID
    box: NormalizedPageBox

    def __post_init__(self) -> None:
        _require_uuid_model(self.overlay_id, context="overlay.command_overlay")
        if type(self.box) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.command_box")

    def __repr__(self) -> str:
        return "ReplaceOverlayBoxCommand(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class ReplaceCoreOverlayPayloadCommand:
    """Replace formatting/value intent while retaining the exact core kind."""

    overlay_id: UUID
    payload: TextualOverlayPayload | MarkOverlayPayload

    def __post_init__(self) -> None:
        _require_uuid_model(self.overlay_id, context="overlay.command_overlay")
        if type(self.payload) not in {TextualOverlayPayload, MarkOverlayPayload}:
            reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.command_payload")
        if self.payload.kind not in CORE_OVERLAY_KINDS:
            reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.command_payload")

    def __repr__(self) -> str:
        kind = (
            self.payload.kind.value
            if type(self.payload) in {TextualOverlayPayload, MarkOverlayPayload}
            else "invalid"
        )
        return f"ReplaceCoreOverlayPayloadCommand(kind={kind!r}, details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class DeleteOverlayCommand:
    """Remove one core overlay record without touching its source page."""

    overlay_id: UUID

    def __post_init__(self) -> None:
        _require_uuid_model(self.overlay_id, context="overlay.command_overlay")

    def __repr__(self) -> str:
        return "DeleteOverlayCommand(details_hidden=True)"


type OverlayCommand = (
    CreateCoreOverlayCommand
    | ReplaceOverlayBoxCommand
    | ReplaceCoreOverlayPayloadCommand
    | DeleteOverlayCommand
)


@dataclass(frozen=True, slots=True, repr=False)
class _OverlayDelta:
    """One compact reversible array edit; representations hide both records."""

    position: int
    before: OverlayRecord | None
    after: OverlayRecord | None

    def __post_init__(self) -> None:
        if (
            type(self.position) is not int
            or self.position < 0
            or (self.before is None and self.after is None)
            or (self.before is not None and type(self.before) is not OverlayRecord)
            or (self.after is not None and type(self.after) is not OverlayRecord)
        ):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.delta")
        if (
            self.before is not None
            and self.after is not None
            and self.before.overlay_id != self.after.overlay_id
        ):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.delta_identity")

    def __repr__(self) -> str:
        return "OverlayDelta(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class _ParsedSnapshot:
    document: dict[str, object] = field(repr=False)
    case_id: UUID
    revision: int
    assets: tuple[OverlayAssetRecord, ...] = field(repr=False)
    overlays: tuple[OverlayRecord, ...] = field(repr=False)
    known_page_ids: tuple[UUID, ...] = field(repr=False)
    active_page_ids: tuple[UUID, ...] = field(repr=False)
    non_overlay_namespace: frozenset[UUID] = field(repr=False)
    complete_namespace: frozenset[UUID] = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False, init=False)
class OverlayCommandHistory:
    """Immutable bounded session history over durable loaded overlay intent."""

    _case_id: UUID
    _origin_revision: int
    _assets: tuple[OverlayAssetRecord, ...]
    _known_page_ids: tuple[UUID, ...]
    _active_page_ids: tuple[UUID, ...]
    _origin_overlays: tuple[OverlayRecord, ...]
    _overlays: tuple[OverlayRecord, ...]
    _applied_deltas: tuple[_OverlayDelta, ...]
    _redo_deltas: tuple[_OverlayDelta, ...]
    _owned_deltas: tuple[_OverlayDelta, ...]
    _known_state_keys: frozenset[bytes]
    _origin_namespace: frozenset[UUID]

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot) -> Self:
        """Start a clean session; loaded records are not undoable."""

        parsed = _parse_snapshot(snapshot)
        return cls._create(
            case_id=parsed.case_id,
            origin_revision=parsed.revision,
            assets=parsed.assets,
            known_page_ids=parsed.known_page_ids,
            active_page_ids=parsed.active_page_ids,
            origin_overlays=parsed.overlays,
            overlays=parsed.overlays,
            applied_deltas=(),
            redo_deltas=(),
            owned_deltas=(),
            known_state_keys=frozenset({_overlay_state_key(parsed.overlays)}),
            origin_namespace=parsed.complete_namespace,
        )

    @classmethod
    def _create(
        cls,
        *,
        case_id: UUID,
        origin_revision: int,
        assets: tuple[OverlayAssetRecord, ...],
        known_page_ids: tuple[UUID, ...],
        active_page_ids: tuple[UUID, ...],
        origin_overlays: tuple[OverlayRecord, ...],
        overlays: tuple[OverlayRecord, ...],
        applied_deltas: tuple[_OverlayDelta, ...],
        redo_deltas: tuple[_OverlayDelta, ...],
        owned_deltas: tuple[_OverlayDelta, ...],
        known_state_keys: frozenset[bytes],
        origin_namespace: frozenset[UUID],
    ) -> Self:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_case_id", case_id)
        object.__setattr__(instance, "_origin_revision", origin_revision)
        object.__setattr__(instance, "_assets", assets)
        object.__setattr__(instance, "_known_page_ids", known_page_ids)
        object.__setattr__(instance, "_active_page_ids", active_page_ids)
        object.__setattr__(instance, "_origin_overlays", origin_overlays)
        object.__setattr__(instance, "_overlays", overlays)
        object.__setattr__(instance, "_applied_deltas", applied_deltas)
        object.__setattr__(instance, "_redo_deltas", redo_deltas)
        object.__setattr__(instance, "_owned_deltas", owned_deltas)
        object.__setattr__(instance, "_known_state_keys", known_state_keys)
        object.__setattr__(instance, "_origin_namespace", origin_namespace)
        return instance

    def __repr__(self) -> str:
        return (
            "OverlayCommandHistory("
            f"overlay_count={len(self._overlays)!r}, "
            f"undo_count={len(self._applied_deltas)!r}, "
            f"redo_count={len(self._redo_deltas)!r})"
        )

    @property
    def overlays(self) -> tuple[OverlayRecord, ...]:
        """Return immutable current intent in persisted array order."""

        return self._overlays

    @property
    def can_undo(self) -> bool:
        return bool(self._applied_deltas)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_deltas)

    def record_for(self, overlay_id: UUID) -> OverlayRecord:
        """Resolve one record without exposing its identifier in a failure."""

        selected = _require_uuid_model(overlay_id, context="overlay.command_overlay")
        index = _record_index(self._overlays, selected)
        return self._overlays[index]

    def changes_are_scoped_to_page(self, page_id: UUID) -> bool:
        """Return whether every session-owned delta is confined to one core page."""

        if type(page_id) is not UUID or page_id.version != 4:
            return False
        return all(_delta_is_scoped_to_page(delta, page_id) for delta in self._owned_deltas)

    def is_page_scoped_extension_of(
        self,
        opening: OverlayCommandHistory,
        page_id: UUID,
    ) -> bool:
        """Authenticate this history as a replayable extension of one opening state."""

        owned = frozenset(self._owned_deltas)
        if (
            type(opening) is not OverlayCommandHistory
            or type(page_id) is not UUID
            or page_id.version != 4
            or opening._applied_deltas
            or opening._redo_deltas
            or opening._owned_deltas
            or opening._overlays != opening._origin_overlays
            or self._case_id != opening._case_id
            or self._origin_revision != opening._origin_revision
            or self._assets != opening._assets
            or self._known_page_ids != opening._known_page_ids
            or self._active_page_ids != opening._active_page_ids
            or self._origin_overlays != opening._origin_overlays
            or self._origin_namespace != opening._origin_namespace
            or not self.changes_are_scoped_to_page(page_id)
            or any(delta not in owned for delta in self._applied_deltas)
            or any(delta not in owned for delta in self._redo_deltas)
        ):
            return False
        replayed = self._origin_overlays
        try:
            for delta in self._applied_deltas:
                replayed = _apply_delta(replayed, delta, forward=True)
            replayed = self._validate_state(replayed)
        except OverlayError:
            return False
        return replayed == self._overlays

    def execute(
        self,
        command: OverlayCommand,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        """Apply one validated command and return a new immutable history."""

        if type(command) not in {
            CreateCoreOverlayCommand,
            ReplaceOverlayBoxCommand,
            ReplaceCoreOverlayPayloadCommand,
            DeleteOverlayCommand,
        }:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.history_command")
        if not callable(id_factory):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.history_id_factory")

        delta: _OverlayDelta
        if type(command) is CreateCoreOverlayCommand:
            if len(self._overlays) >= MAX_OVERLAYS:
                reject_overlay(OverlayErrorCode.CAPACITY_EXCEEDED, "overlay.history_records")
            reserved = set(self._origin_namespace)
            for owned in self._owned_deltas:
                if owned.after is not None and owned.before is None:
                    reserved.add(owned.after.overlay_id)
            overlay_id = _generate_unique_id(id_factory, reserved)
            z_index = _next_top_z_index(self._overlays, command.page_id)
            record = OverlayRecord(
                overlay_id,
                command.page_id,
                command.box,
                z_index,
                default_core_overlay_payload(command.kind, value=command.value),
            )
            delta = _OverlayDelta(len(self._overlays), None, record)
        elif type(command) is ReplaceOverlayBoxCommand:
            index = _record_index(self._overlays, command.overlay_id)
            before = self._overlays[index]
            _require_editor_geometry_record(before)
            if before.box == command.box:
                return self
            after = OverlayRecord(
                before.overlay_id,
                before.page_id,
                command.box,
                before.z_index,
                before.payload,
                before.overlay_version,
            )
            delta = _OverlayDelta(index, before, after)
        elif type(command) is ReplaceCoreOverlayPayloadCommand:
            index = _record_index(self._overlays, command.overlay_id)
            before = self._overlays[index]
            _require_core_record(before)
            if before.kind is not command.payload.kind:
                reject_overlay(OverlayErrorCode.EDIT_KIND_MISMATCH, "overlay.command_payload")
            if before.payload == command.payload:
                return self
            after = OverlayRecord(
                before.overlay_id,
                before.page_id,
                before.box,
                before.z_index,
                command.payload,
                before.overlay_version,
            )
            delta = _OverlayDelta(index, before, after)
        elif type(command) is DeleteOverlayCommand:
            index = _record_index(self._overlays, command.overlay_id)
            before = self._overlays[index]
            _require_core_record(before)
            delta = _OverlayDelta(index, before, None)
        else:  # pragma: no cover - the exact command guard makes this unreachable
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.history_command")

        if len(self._owned_deltas) >= MAX_OVERLAY_COMMANDS:
            reject_overlay(OverlayErrorCode.CAPACITY_EXCEEDED, "overlay.history_commands")
        overlays = _apply_delta(self._overlays, delta, forward=True)
        overlays = self._validate_state(overlays)
        applied = self._applied_deltas + (delta,)
        return self._transition(
            overlays=overlays,
            applied_deltas=applied,
            redo_deltas=(),
            owned_deltas=self._owned_deltas + (delta,),
            known_state_keys=self._known_state_keys | {_overlay_state_key(overlays)},
        )

    def undo(self) -> Self:
        """Reverse exactly one session delta; loaded intent remains durable."""

        if not self._applied_deltas:
            reject_overlay(OverlayErrorCode.UNDO_UNAVAILABLE, "overlay.history_undo")
        delta = self._applied_deltas[-1]
        overlays = self._validate_state(_apply_delta(self._overlays, delta, forward=False))
        return self._transition(
            overlays=overlays,
            applied_deltas=self._applied_deltas[:-1],
            redo_deltas=(delta,) + self._redo_deltas,
            owned_deltas=self._owned_deltas,
            known_state_keys=self._known_state_keys | {_overlay_state_key(overlays)},
        )

    def redo(self) -> Self:
        """Reapply the exact next delta, including its original generated ID."""

        if not self._redo_deltas:
            reject_overlay(OverlayErrorCode.REDO_UNAVAILABLE, "overlay.history_redo")
        delta = self._redo_deltas[0]
        overlays = self._validate_state(_apply_delta(self._overlays, delta, forward=True))
        return self._transition(
            overlays=overlays,
            applied_deltas=self._applied_deltas + (delta,),
            redo_deltas=self._redo_deltas[1:],
            owned_deltas=self._owned_deltas,
            known_state_keys=self._known_state_keys | {_overlay_state_key(overlays)},
        )

    def to_candidate(self, current_snapshot: ManifestSnapshot) -> dict[str, object]:
        """Replace only overlays on one exact history-produced current state."""

        current = _parse_snapshot(current_snapshot)
        if current.case_id != self._case_id or current.revision < self._origin_revision:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.history_rebase")
        current_key = _overlay_state_key(current.overlays)
        if current_key not in self._known_state_keys or not self._records_are_history_owned(
            current.overlays
        ):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.history_state")

        target_ids = {record.overlay_id for record in self._overlays}
        if target_ids & set(current.non_overlay_namespace):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.history_namespace")
        compatible = True
        try:
            OverlayCollection(
                current.assets,
                self._overlays,
                known_page_ids=current.known_page_ids,
                active_page_ids=current.active_page_ids,
            )
        except OverlayError:
            compatible = False
        if not compatible:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.history_rebase")

        candidate = current.document
        candidate["overlays"] = [record.to_manifest() for record in self._overlays]
        return candidate

    def _records_are_history_owned(self, records: Sequence[OverlayRecord]) -> bool:
        allowed: dict[UUID, set[OverlayRecord]] = {}
        for record in self._origin_overlays:
            allowed.setdefault(record.overlay_id, set()).add(record)
        for delta in self._owned_deltas:
            if delta.before is not None:
                allowed.setdefault(delta.before.overlay_id, set()).add(delta.before)
            if delta.after is not None:
                allowed.setdefault(delta.after.overlay_id, set()).add(delta.after)
        return all(record in allowed.get(record.overlay_id, set()) for record in records)

    def _validate_state(self, overlays: tuple[OverlayRecord, ...]) -> tuple[OverlayRecord, ...]:
        collection = OverlayCollection(
            self._assets,
            overlays,
            known_page_ids=self._known_page_ids,
            active_page_ids=self._active_page_ids,
        )
        return collection.overlays

    def _transition(
        self,
        *,
        overlays: tuple[OverlayRecord, ...],
        applied_deltas: tuple[_OverlayDelta, ...],
        redo_deltas: tuple[_OverlayDelta, ...],
        owned_deltas: tuple[_OverlayDelta, ...],
        known_state_keys: frozenset[bytes],
    ) -> Self:
        return self._create(
            case_id=self._case_id,
            origin_revision=self._origin_revision,
            assets=self._assets,
            known_page_ids=self._known_page_ids,
            active_page_ids=self._active_page_ids,
            origin_overlays=self._origin_overlays,
            overlays=overlays,
            applied_deltas=applied_deltas,
            redo_deltas=redo_deltas,
            owned_deltas=owned_deltas,
            known_state_keys=known_state_keys,
            origin_namespace=self._origin_namespace,
        )


def _require_uuid_model(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    return value


def _parse_uuid_text(value: object, *, context: str) -> UUID:
    if type(value) is not str or _UUID_V4_EXACT.fullmatch(value) is None:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    return parsed


def _record_index(records: Sequence[OverlayRecord], overlay_id: UUID) -> int:
    selected = _require_uuid_model(overlay_id, context="overlay.command_overlay")
    for index, record in enumerate(records):
        if record.overlay_id == selected:
            return index
    reject_overlay(OverlayErrorCode.RECORD_UNKNOWN, "overlay.command_overlay")


def _require_core_record(record: OverlayRecord) -> None:
    if type(record) is not OverlayRecord or record.kind not in CORE_OVERLAY_KINDS:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.command_record")


def _require_editor_geometry_record(record: OverlayRecord) -> None:
    if type(record) is not OverlayRecord or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.command_record")


def _next_top_z_index(records: Sequence[OverlayRecord], page_id: UUID) -> int:
    page_records = [record.z_index for record in records if record.page_id == page_id]
    if not page_records:
        return 0
    top = max(page_records)
    if top >= MAX_SAFE_MANIFEST_INTEGER:
        reject_overlay(OverlayErrorCode.Z_INDEX_INVALID, "overlay.command_z_index")
    return top + 1


def _generate_unique_id(factory: Callable[[], UUID], reserved: set[UUID]) -> UUID:
    for _ in range(MAX_OVERLAY_ID_GENERATION_ATTEMPTS):
        try:
            candidate = factory()
        except Exception:
            continue
        if type(candidate) is UUID and candidate.version == 4 and candidate not in reserved:
            return candidate
    reject_overlay(OverlayErrorCode.ID_GENERATION_FAILED, "overlay.history_identifier")


def _apply_delta(
    records: tuple[OverlayRecord, ...],
    delta: _OverlayDelta,
    *,
    forward: bool,
) -> tuple[OverlayRecord, ...]:
    before = delta.before if forward else delta.after
    after = delta.after if forward else delta.before
    position = delta.position
    mutable = list(records)
    if before is None:
        if position > len(mutable) or after is None:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.delta_apply")
        mutable.insert(position, after)
    else:
        if position >= len(mutable) or mutable[position] != before:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.delta_apply")
        if after is None:
            del mutable[position]
        else:
            mutable[position] = after
    return tuple(mutable)


def _delta_is_scoped_to_page(delta: _OverlayDelta, page_id: UUID) -> bool:
    before = delta.before
    after = delta.after
    records = tuple(record for record in (before, after) if record is not None)
    if not records or any(record.page_id != page_id for record in records):
        return False
    if before is None or after is None:
        return all(record.kind in CORE_OVERLAY_KINDS for record in records)
    common_identity = (
        before.overlay_id == after.overlay_id
        and before.kind is after.kind
        and before.kind in EDITOR_GEOMETRY_OVERLAY_KINDS
        and before.page_id == after.page_id
        and before.z_index == after.z_index
        and before.overlay_version == after.overlay_version
    )
    if not common_identity:
        return False
    box_only = before.payload == after.payload and before.box != after.box
    core_payload_only = (
        before.kind in CORE_OVERLAY_KINDS
        and before.box == after.box
        and before.payload != after.payload
    )
    return box_only or core_payload_only


def _overlay_state_key(records: Sequence[OverlayRecord]) -> bytes:
    payload = json.dumps(
        [record.to_manifest() for record in records],
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(len(records).to_bytes(4, byteorder="big", signed=False))
    digest.update(payload)
    return digest.digest()


def _parse_snapshot(snapshot: ManifestSnapshot) -> _ParsedSnapshot:
    if type(snapshot) is not ManifestSnapshot:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.history_snapshot")
    try:
        document = snapshot.document
    except Exception:
        document = None
    if document is None:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.history_snapshot")
    case_id = _parse_uuid_text(document.get("case_id"), context="overlay.manifest_case")
    revision = document.get("revision")
    if (
        case_id != snapshot.case_id
        or type(revision) is not int
        or revision != snapshot.revision
        or not 0 <= revision <= MAX_SAFE_MANIFEST_INTEGER
    ):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_identity")

    collection = OverlayCollection.from_manifest_document(document)
    known_page_ids = _identity_sequence(document, "pages", "page_id")
    active_page_ids = _uuid_sequence(document.get("page_sequence"), "overlay.manifest_pages")
    non_overlay, complete = _manifest_namespaces(document)
    return _ParsedSnapshot(
        document=document,
        case_id=case_id,
        revision=revision,
        assets=collection.assets,
        overlays=collection.overlays,
        known_page_ids=known_page_ids,
        active_page_ids=active_page_ids,
        non_overlay_namespace=frozenset(non_overlay),
        complete_namespace=frozenset(complete),
    )


def _identity_sequence(
    document: Mapping[str, object], collection_name: str, id_field: str
) -> tuple[UUID, ...]:
    records = _record_collection(document.get(collection_name), "overlay.manifest_namespace")
    return tuple(
        _parse_uuid_text(record.get(id_field), context="overlay.manifest_namespace")
        for record in records
    )


def _uuid_sequence(value: object, context: str) -> tuple[UUID, ...]:
    if type(value) is not list or len(value) > MAX_OVERLAYS:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    return tuple(_parse_uuid_text(item, context=context) for item in value)


def _record_collection(value: object, context: str) -> tuple[dict[str, object], ...]:
    if type(value) is not list or len(value) > MAX_OVERLAYS:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    if any(type(record) is not dict for record in value):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    return cast(tuple[dict[str, object], ...], tuple(value))


def _manifest_namespaces(document: Mapping[str, object]) -> tuple[set[UUID], set[UUID]]:
    non_overlay: set[UUID] = set()
    case_id = _parse_uuid_text(document.get("case_id"), context="overlay.manifest_namespace")
    _register_namespace(non_overlay, case_id)

    if document.get("schema_version") in {3, 4}:
        for record in _record_collection(
            document.get("redaction_marks"), "overlay.manifest_namespace"
        ):
            _register_namespace(
                non_overlay,
                _parse_uuid_text(record.get("mark_id"), context="overlay.manifest_namespace"),
            )

    operation_records: tuple[dict[str, object], ...] = ()
    for collection_name, id_field in _IDENTITY_COLLECTIONS:
        records = _record_collection(document.get(collection_name), "overlay.manifest_namespace")
        if collection_name == "page_operations":
            operation_records = records
        for record in records:
            _register_namespace(
                non_overlay,
                _parse_uuid_text(
                    record.get(id_field),
                    context="overlay.manifest_namespace",
                ),
            )

    group_ids: set[UUID] = set()
    for operation in operation_records:
        payload = operation.get("payload")
        if type(payload) is not dict:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_namespace")
        if payload.get("kind") == "set_group":
            group_id = _parse_uuid_text(
                payload.get("group_id"), context="overlay.manifest_namespace"
            )
            if group_id not in group_ids:
                group_ids.add(group_id)
                _register_namespace(non_overlay, group_id)

    export_history = document.get("export_history")
    if type(export_history) is not dict:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_namespace")
    exports = _record_collection(export_history.get("entries"), "overlay.manifest_namespace")
    for record in exports:
        _register_namespace(
            non_overlay,
            _parse_uuid_text(record.get("export_id"), context="overlay.manifest_namespace"),
        )

    complete = set(non_overlay)
    overlays = _record_collection(document.get("overlays"), "overlay.manifest_namespace")
    for record in overlays:
        _register_namespace(
            complete,
            _parse_uuid_text(record.get("overlay_id"), context="overlay.manifest_namespace"),
        )
    return non_overlay, complete


def _register_namespace(namespace: set[UUID], identifier: UUID) -> None:
    if identifier in namespace:
        reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.manifest_namespace")
    namespace.add(identifier)
