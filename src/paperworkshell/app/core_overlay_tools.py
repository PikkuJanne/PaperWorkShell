"""Toolkit-free authority for core tools and geometry editing of every overlay kind.

The facade owns only an ephemeral, revision-bound editing draft.  It never
opens a document, writes a manifest, infers a date, or exposes field values in
its summaries and representations. Signature removal remains outside this
authority so its case-asset cleanup policy stays explicit. Persistence remains an explicit
application-layer commit through :class:`OrganizerBackend`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Self
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    NormalizedPageBox,
    PageCoordinateError,
)
from paperworkshell.overlays.commands import (
    CORE_OVERLAY_KINDS,
    EDITOR_GEOMETRY_OVERLAY_KINDS,
    CreateCoreOverlayCommand,
    DeleteOverlayCommand,
    OverlayCommandHistory,
    ReplaceCoreOverlayPayloadCommand,
    ReplaceOverlayBoxCommand,
)
from paperworkshell.overlays.models import (
    MarkOverlayPayload,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    TextualOverlayPayload,
    reject_overlay,
)

KEYBOARD_MOVE_STEP_MILLIONTHS: Final = 10_000
KEYBOARD_FINE_MOVE_STEP_MILLIONTHS: Final = 1_000
GUIDE_STEPS_MILLIONTHS: Final = (10_000, 25_000, 50_000, 100_000)


class KeyboardDirection(StrEnum):
    """Closed direction names shared by the Qt and non-Qt keyboard paths."""

    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class CoreOverlayToolDescriptor:
    """Content-free UI metadata and deterministic initial geometry for one tool."""

    kind: OverlayKind
    label: str
    accessible_name: str
    value_label: str | None
    value_description: str | None
    default_width: int
    default_height: int

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not OverlayKind
            or self.kind not in CORE_OVERLAY_KINDS
            or type(self.label) is not str
            or not self.label
            or type(self.accessible_name) is not str
            or not self.accessible_name
            or type(self.default_width) is not int
            or type(self.default_height) is not int
            or self.default_width <= 0
            or self.default_height <= 0
            or self.default_width > NORMALIZED_PAGE_MILLIONTHS
            or self.default_height > NORMALIZED_PAGE_MILLIONTHS
        ):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.tool_descriptor")
        textual = self.kind in {
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.INITIALS,
        }
        if textual != (type(self.value_label) is str and type(self.value_description) is str):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.tool_descriptor")


CORE_OVERLAY_TOOL_DESCRIPTORS: Final = (
    CoreOverlayToolDescriptor(
        OverlayKind.TEXT,
        "Text",
        "Text visual mark",
        "Text value",
        "Enter the text exactly as it should appear.",
        400_000,
        80_000,
    ),
    CoreOverlayToolDescriptor(
        OverlayKind.DATE,
        "Date",
        "Date visual mark",
        "Date display value",
        "Enter exactly how the date should appear; PaperWorkShell does not reformat it.",
        250_000,
        80_000,
    ),
    CoreOverlayToolDescriptor(
        OverlayKind.CHECKMARK,
        "Checkmark",
        "Checkmark visual mark",
        None,
        None,
        60_000,
        60_000,
    ),
    CoreOverlayToolDescriptor(
        OverlayKind.CROSS,
        "Cross",
        "Cross visual mark",
        None,
        None,
        60_000,
        60_000,
    ),
    CoreOverlayToolDescriptor(
        OverlayKind.INITIALS,
        "Initials",
        "Initials visual mark",
        "Initials value",
        "Enter the initials exactly as they should appear.",
        160_000,
        80_000,
    ),
)
_DESCRIPTOR_BY_KIND: Final = {
    descriptor.kind: descriptor for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS
}
_EDITOR_KIND_ACCESSIBLE_NAMES: Final = {
    **{descriptor.kind: descriptor.accessible_name for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS},
    OverlayKind.SIGNATURE_IMAGE: "Signature image visual mark",
    OverlayKind.HIGHLIGHT: "Highlight visual mark",
    OverlayKind.SHAPE: "Shape visual mark",
}
_SAFE_EDITOR_ANNOUNCEMENTS: Final = frozenset(
    {
        "Visual form-mark editor ready.",
        "Last visual form-mark change undone.",
        "Visual form-mark change redone.",
    }
    | {
        message
        for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS
        for message in (
            f"{descriptor.accessible_name} selected.",
            f"{descriptor.accessible_name} added and selected.",
            f"{descriptor.accessible_name} position and size updated.",
            f"{descriptor.accessible_name} properties updated.",
            f"{descriptor.accessible_name} removed.",
        )
    }
    | {f"{accessible_name} selected." for accessible_name in _EDITOR_KIND_ACCESSIBLE_NAMES.values()}
    | {
        f"{accessible_name} position and size updated."
        for accessible_name in _EDITOR_KIND_ACCESSIBLE_NAMES.values()
    }
    | {
        f"{accessible_name} snapped to the nearest guides."
        for accessible_name in _EDITOR_KIND_ACCESSIBLE_NAMES.values()
    }
    | {
        f"{accessible_name} moved {direction.value}."
        for accessible_name in _EDITOR_KIND_ACCESSIBLE_NAMES.values()
        for direction in KeyboardDirection
    }
    | {
        f"{accessible_name} {dimension} {change}."
        for accessible_name in _EDITOR_KIND_ACCESSIBLE_NAMES.values()
        for dimension in ("width", "height")
        for change in ("increased", "decreased")
    }
)


def is_core_overlay_announcement_safe(value: object) -> bool:
    """Recognize the closed, content-free editor announcement vocabulary."""

    return type(value) is str and value in _SAFE_EDITOR_ANNOUNCEMENTS


def core_overlay_tool_descriptor(kind: OverlayKind) -> CoreOverlayToolDescriptor:
    """Return fixed content-free metadata for one supported core kind."""

    if type(kind) is not OverlayKind:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.tool_kind")
    descriptor = _DESCRIPTOR_BY_KIND.get(kind)
    if descriptor is None:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.tool_kind")
    return descriptor


def core_overlay_record_summary(record: OverlayRecord, position: int, total: int) -> str:
    """Describe geometry and type without disclosing the overlay's field value."""

    if (
        type(record) is not OverlayRecord
        or record.kind not in CORE_OVERLAY_KINDS
        or type(position) is not int
        or type(total) is not int
        or not 1 <= position <= total
    ):
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.summary")
    descriptor = core_overlay_tool_descriptor(record.kind)
    box = record.box
    return (
        f"{descriptor.accessible_name}, item {position} of {total}; "
        f"left {_percent_text(box.x)}; top {_percent_text(box.y)}; "
        f"width {_percent_text(box.width)}; height {_percent_text(box.height)}."
    )


def overlay_editor_kind_accessible_name(kind: OverlayKind) -> str:
    """Return fixed, non-disclosing editor terminology for every released overlay kind."""

    if type(kind) is not OverlayKind:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.editor_kind")
    name = _EDITOR_KIND_ACCESSIBLE_NAMES.get(kind)
    if name is None:
        reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.editor_kind")
    return name


def overlay_editor_record_accessible_name(record: OverlayRecord) -> str:
    """Return a fixed type name, including a non-private shape primitive."""

    if type(record) is not OverlayRecord:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.editor_record_name")
    name = overlay_editor_kind_accessible_name(record.kind)
    if type(record.payload) is ShapeOverlayPayload:
        return f"{record.payload.shape.value.capitalize()} {name.casefold()}"
    return name


def overlay_editor_record_summary(record: OverlayRecord, position: int, total: int) -> str:
    """Describe any page overlay by type and geometry without exposing its payload."""

    if (
        type(record) is not OverlayRecord
        or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS
        or type(position) is not int
        or type(total) is not int
        or not 1 <= position <= total
    ):
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.editor_summary")
    box = record.box
    return (
        f"{overlay_editor_record_accessible_name(record)}, item {position} of {total}; "
        f"left {_percent_text(box.x)}; top {_percent_text(box.y)}; "
        f"width {_percent_text(box.width)}; height {_percent_text(box.height)}."
    )


@dataclass(frozen=True, slots=True, repr=False)
class CoreOverlayToolsState:
    """One exact, local editing draft bound to a case revision and active page."""

    case_id: UUID
    manifest_revision: int
    page_id: UUID
    editor_session_id: UUID = field(repr=False)
    history: OverlayCommandHistory = field(repr=False)
    selected_overlay_id: UUID | None = None
    announcement: str = "Visual form-mark editor ready."

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or type(self.editor_session_id) is not UUID
            or self.editor_session_id.version != 4
            or type(self.manifest_revision) is not int
            or self.manifest_revision < 0
            or type(self.history) is not OverlayCommandHistory
            or not self.history.changes_are_scoped_to_page(self.page_id)
            or not is_core_overlay_announcement_safe(self.announcement)
        ):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.editor_state")
        if self.selected_overlay_id is not None:
            if type(self.selected_overlay_id) is not UUID:
                reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, "overlay.selection")
            record = self.history.record_for(self.selected_overlay_id)
            if record.page_id != self.page_id or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS:
                reject_overlay(OverlayErrorCode.RECORD_UNKNOWN, "overlay.selection")

    def __repr__(self) -> str:
        return (
            "CoreOverlayToolsState("
            f"overlay_count={len(self.editor_records)!r}, selected={self.selected_overlay_id is not None!r}, "
            f"can_undo={self.can_undo!r}, can_redo={self.can_redo!r})"
        )

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot, page_id: UUID) -> Self:
        """Start a clean draft for one active page of an exact manifest revision."""

        if type(snapshot) is not ManifestSnapshot or type(page_id) is not UUID:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.editor_open")
        page_sequence = snapshot.document.get("page_sequence")
        if type(page_sequence) is not list or str(page_id) not in page_sequence:
            reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.editor_page")
        return cls(
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            page_id=page_id,
            editor_session_id=uuid4(),
            history=OverlayCommandHistory.from_snapshot(snapshot),
        )

    @property
    def records(self) -> tuple[OverlayRecord, ...]:
        """Return only core records for this page in deterministic stack order."""

        return tuple(
            sorted(
                (
                    record
                    for record in self.history.overlays
                    if record.page_id == self.page_id and record.kind in CORE_OVERLAY_KINDS
                ),
                key=lambda record: (record.z_index, record.overlay_id.int),
            )
        )

    @property
    def editor_records(self) -> tuple[OverlayRecord, ...]:
        """Return every released overlay kind on this page in stable stack order."""

        return tuple(
            sorted(
                (
                    record
                    for record in self.history.overlays
                    if record.page_id == self.page_id
                    and record.kind in EDITOR_GEOMETRY_OVERLAY_KINDS
                ),
                key=lambda record: (record.z_index, record.overlay_id.int),
            )
        )

    @property
    def selected_record(self) -> OverlayRecord | None:
        """Return the selected page overlay without inferring a replacement selection."""

        if self.selected_overlay_id is None:
            return None
        return self.history.record_for(self.selected_overlay_id)

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    @property
    def changed(self) -> bool:
        return self.history.can_undo

    def select(self, overlay_id: UUID) -> Self:
        """Select one released overlay on the bound page; selection is not history."""

        if type(overlay_id) is not UUID:
            reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, "overlay.selection")
        record = self.history.record_for(overlay_id)
        if record.page_id != self.page_id or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS:
            reject_overlay(OverlayErrorCode.RECORD_UNKNOWN, "overlay.selection")
        return self._transition(
            selected_overlay_id=overlay_id,
            announcement=f"{overlay_editor_kind_accessible_name(record.kind)} selected.",
        )

    def create(
        self,
        kind: OverlayKind,
        *,
        value: str | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        """Create one default core record and select it without interpreting its value."""

        descriptor = core_overlay_tool_descriptor(kind)
        box = self._default_box(descriptor)
        history = self.history.execute(
            CreateCoreOverlayCommand(kind, self.page_id, box, value),
            id_factory=id_factory,
        )
        created_ids = {record.overlay_id for record in history.overlays} - {
            record.overlay_id for record in self.history.overlays
        }
        if len(created_ids) != 1:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.create_result")
        created_id = next(iter(created_ids))
        return self._transition(
            history=history,
            selected_overlay_id=created_id,
            announcement=f"{descriptor.accessible_name} added and selected.",
        )

    def replace_selected_box(self, box: NormalizedPageBox) -> Self:
        """Apply one exact full-page normalized position and size."""

        record = self._require_selected_for_geometry()
        if type(box) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.editor_box")
        if box == record.box:
            return self
        history = self.history.execute(ReplaceOverlayBoxCommand(record.overlay_id, box))
        return self._transition(
            history=history,
            announcement=(
                f"{overlay_editor_kind_accessible_name(record.kind)} position and size updated."
            ),
        )

    def move_selected(
        self,
        direction: KeyboardDirection,
        *,
        fine: bool = False,
    ) -> Self:
        """Move the selection by a fixed keyboard step, rejecting page-edge overflow."""

        record = self._require_selected_for_geometry()
        selected_direction = _require_direction(direction)
        if type(fine) is not bool:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.keyboard_step")
        step = KEYBOARD_FINE_MOVE_STEP_MILLIONTHS if fine else KEYBOARD_MOVE_STEP_MILLIONTHS
        dx = (
            -step
            if selected_direction is KeyboardDirection.LEFT
            else (step if selected_direction is KeyboardDirection.RIGHT else 0)
        )
        dy = (
            -step
            if selected_direction is KeyboardDirection.UP
            else (step if selected_direction is KeyboardDirection.DOWN else 0)
        )
        try:
            box = NormalizedPageBox(
                record.box.x + dx,
                record.box.y + dy,
                record.box.width,
                record.box.height,
            )
        except PageCoordinateError:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.keyboard_move")
        history = self.history.execute(ReplaceOverlayBoxCommand(record.overlay_id, box))
        return self._transition(
            history=history,
            announcement=(
                f"{overlay_editor_kind_accessible_name(record.kind)} moved "
                f"{selected_direction.value}."
            ),
        )

    def resize_selected(
        self,
        direction: KeyboardDirection,
        *,
        fine: bool = False,
    ) -> Self:
        """Resize width or height by a fixed keyboard step without clipping."""

        record = self._require_selected_for_geometry()
        selected_direction = _require_direction(direction)
        if type(fine) is not bool:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.keyboard_step")
        step = KEYBOARD_FINE_MOVE_STEP_MILLIONTHS if fine else KEYBOARD_MOVE_STEP_MILLIONTHS
        width_delta = (
            -step
            if selected_direction is KeyboardDirection.LEFT
            else (step if selected_direction is KeyboardDirection.RIGHT else 0)
        )
        height_delta = (
            -step
            if selected_direction is KeyboardDirection.UP
            else (step if selected_direction is KeyboardDirection.DOWN else 0)
        )
        try:
            box = NormalizedPageBox(
                record.box.x,
                record.box.y,
                record.box.width + width_delta,
                record.box.height + height_delta,
            )
        except PageCoordinateError:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.keyboard_resize")
        history = self.history.execute(ReplaceOverlayBoxCommand(record.overlay_id, box))
        dimension = "width" if width_delta else "height"
        change = "increased" if width_delta > 0 or height_delta > 0 else "decreased"
        return self._transition(
            history=history,
            announcement=(
                f"{overlay_editor_kind_accessible_name(record.kind)} {dimension} {change}."
            ),
        )

    def snap_selected(self, guide_step_millionths: int) -> Self:
        """Move the selected box origin to the nearest deterministic full-page guides."""

        record = self._require_selected_for_geometry()
        if (
            type(guide_step_millionths) is not int
            or guide_step_millionths not in GUIDE_STEPS_MILLIONTHS
        ):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.guide_step")
        x = _nearest_guide_origin(
            record.box.x,
            guide_step_millionths,
            NORMALIZED_PAGE_MILLIONTHS - record.box.width,
        )
        y = _nearest_guide_origin(
            record.box.y,
            guide_step_millionths,
            NORMALIZED_PAGE_MILLIONTHS - record.box.height,
        )
        box = NormalizedPageBox(x, y, record.box.width, record.box.height)
        if box == record.box:
            return self
        history = self.history.execute(ReplaceOverlayBoxCommand(record.overlay_id, box))
        return self._transition(
            history=history,
            announcement=(
                f"{overlay_editor_kind_accessible_name(record.kind)} snapped to the nearest guides."
            ),
        )

    def edit_selected(
        self,
        *,
        value: str | None = None,
        font_size_points: int | None = None,
        color_value: str | None = None,
    ) -> Self:
        """Replace same-kind payload properties while keeping user content opaque."""

        record = self._require_selected_core()
        payload = record.payload
        if type(payload) is TextualOverlayPayload:
            color = payload.color if color_value is None else RgbaColor(color_value)
            selected_value = payload.value if value is None else value
            selected_size = (
                payload.font_size_points if font_size_points is None else font_size_points
            )
            replacement: TextualOverlayPayload | MarkOverlayPayload = TextualOverlayPayload(
                payload.kind,
                selected_value,
                payload.font_token,
                selected_size,
                color,
            )
        elif type(payload) is MarkOverlayPayload:
            color = payload.color if color_value is None else RgbaColor(color_value)
            if value is not None or font_size_points is not None:
                reject_overlay(OverlayErrorCode.EDIT_KIND_MISMATCH, "overlay.editor_payload")
            replacement = MarkOverlayPayload(payload.kind, color)
        else:
            reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.editor_payload")
        if replacement == payload:
            return self
        history = self.history.execute(
            ReplaceCoreOverlayPayloadCommand(record.overlay_id, replacement)
        )
        return self._transition(
            history=history,
            announcement=f"{core_overlay_tool_descriptor(record.kind).accessible_name} properties updated.",
        )

    def delete_selected(self) -> Self:
        """Delete the selected record from the local draft."""

        record = self._require_selected_core()
        history = self.history.execute(DeleteOverlayCommand(record.overlay_id))
        return self._transition(
            history=history,
            selected_overlay_id=None,
            announcement=f"{core_overlay_tool_descriptor(record.kind).accessible_name} removed.",
        )

    def undo(self) -> Self:
        """Undo one local overlay command and preserve selection only if it still exists."""

        history = self.history.undo()
        selection = _retained_selection(history, self.selected_overlay_id, self.page_id)
        return self._transition(
            history=history,
            selected_overlay_id=selection,
            announcement="Last visual form-mark change undone.",
        )

    def redo(self) -> Self:
        """Redo one local overlay command and preserve selection only if it still exists."""

        history = self.history.redo()
        selection = _retained_selection(history, self.selected_overlay_id, self.page_id)
        return self._transition(
            history=history,
            selected_overlay_id=selection,
            announcement="Visual form-mark change redone.",
        )

    def to_candidate(self, snapshot: ManifestSnapshot) -> dict[str, object]:
        """Build a candidate only for the exact opening case and revision."""

        if (
            type(snapshot) is not ManifestSnapshot
            or snapshot.case_id != self.case_id
            or snapshot.revision != self.manifest_revision
        ):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_binding")
        return self.history.to_candidate(snapshot)

    def _default_box(self, descriptor: CoreOverlayToolDescriptor) -> NormalizedPageBox:
        ordinal = len(self.records)
        offset = (ordinal % 20) * 20_000
        x = min(100_000 + offset, NORMALIZED_PAGE_MILLIONTHS - descriptor.default_width)
        y = min(100_000 + offset, NORMALIZED_PAGE_MILLIONTHS - descriptor.default_height)
        return NormalizedPageBox(x, y, descriptor.default_width, descriptor.default_height)

    def _require_selected_for_geometry(self) -> OverlayRecord:
        record = self.selected_record
        if record is None:
            reject_overlay(OverlayErrorCode.SELECTION_REQUIRED, "overlay.selection")
        if record.page_id != self.page_id or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS:
            reject_overlay(OverlayErrorCode.RECORD_UNKNOWN, "overlay.selection")
        return record

    def _require_selected_core(self) -> OverlayRecord:
        record = self._require_selected_for_geometry()
        if record.kind not in CORE_OVERLAY_KINDS:
            reject_overlay(OverlayErrorCode.CORE_TOOL_UNSUPPORTED, "overlay.selection")
        return record

    def _transition(
        self,
        *,
        history: OverlayCommandHistory | None = None,
        selected_overlay_id: UUID | None | object = ...,
        announcement: str,
    ) -> Self:
        selection = self.selected_overlay_id if selected_overlay_id is ... else selected_overlay_id
        if selection is not None and type(selection) is not UUID:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.selection")
        return type(self)(
            case_id=self.case_id,
            manifest_revision=self.manifest_revision,
            page_id=self.page_id,
            editor_session_id=self.editor_session_id,
            history=self.history if history is None else history,
            selected_overlay_id=selection,
            announcement=announcement,
        )


def _require_direction(direction: KeyboardDirection) -> KeyboardDirection:
    if type(direction) is not KeyboardDirection:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.keyboard_direction")
    return direction


def _retained_selection(
    history: OverlayCommandHistory,
    selected_overlay_id: UUID | None,
    page_id: UUID,
) -> UUID | None:
    if selected_overlay_id is None:
        return None
    try:
        record = history.record_for(selected_overlay_id)
    except OverlayError:
        return None
    if record.page_id != page_id or record.kind not in EDITOR_GEOMETRY_OVERLAY_KINDS:
        return None
    return selected_overlay_id


def _percent_text(value: int) -> str:
    whole, fraction = divmod(value, 10_000)
    return f"{whole}.{fraction:04d} percent"


def _round_to_guide(value: int, step: int) -> int:
    return ((value + step // 2) // step) * step


def _nearest_guide_origin(value: int, step: int, maximum_origin: int) -> int:
    return min(_round_to_guide(value, step), (maximum_origin // step) * step)
