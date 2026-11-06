"""Localized presentation of canonical, content-free overlay facts.

Canonical application announcements remain unchanged for their closed-vocabulary
security checks. Translation happens only after that check succeeds.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from paperworkshell.app.core_overlay_tools import (
    is_core_overlay_announcement_safe,
    overlay_editor_record_accessible_name,
    overlay_editor_record_summary,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.models import (
    OverlayKind,
    OverlayRecord,
    ShapeOverlayPayload,
    ShapePrimitive,
)

_SHAPE_LABELS: Final = {
    ShapePrimitive.RECTANGLE: mark("Rectangle"),
    ShapePrimitive.ELLIPSE: mark("Ellipse"),
    ShapePrimitive.LINE: mark("Line"),
}

_OVERLAY_ANNOUNCEMENT_PATTERNS: Final = (
    mark("{name} selected."),
    mark("{name} added and selected."),
    mark("{name} position and size updated."),
    mark("{name} properties updated."),
    mark("{name} removed."),
    mark("{name} snapped to the nearest guides."),
    mark("{name} moved left."),
    mark("{name} moved right."),
    mark("{name} moved up."),
    mark("{name} moved down."),
    mark("{name} width increased."),
    mark("{name} width decreased."),
    mark("{name} height increased."),
    mark("{name} height decreased."),
)


def localized_overlay_announcement(source: str) -> str:
    """Translate a validated announcement without changing its authority value."""
    if not is_core_overlay_announcement_safe(source):
        raise ValueError("overlay announcement is outside the approved vocabulary")
    for pattern in _OVERLAY_ANNOUNCEMENT_PATTERNS:
        suffix = pattern.removeprefix("{name}")
        if source.endswith(suffix):
            return tr(pattern, name=tr(source[: -len(suffix)]))
    return tr(source)


def localized_overlay_record_name(record: OverlayRecord) -> str:
    """Describe a type without translating or revealing user-entered content."""
    canonical = overlay_editor_record_accessible_name(record)
    if record.kind is OverlayKind.SHAPE and type(record.payload) is ShapeOverlayPayload:
        return tr(
            "{shape} shape visual mark",
            shape=tr(_SHAPE_LABELS[record.payload.shape]),
        )
    if record.kind is OverlayKind.HIGHLIGHT and type(record.payload) is ShapeOverlayPayload:
        return tr(
            "{shape} highlight visual mark",
            shape=tr(_SHAPE_LABELS[record.payload.shape]),
        )
    return tr(canonical)


def localized_overlay_record_summary(record: OverlayRecord, position: int, total: int) -> str:
    """Translate the geometry summary after the canonical validator accepts it."""
    overlay_editor_record_summary(record, position, total)
    box = record.box

    def percent(value: int) -> str:
        return tr("{value:.4f} percent", value=Decimal(value) / 10_000)

    return tr(
        "{name}, item {position} of {total}; left {left}; top {top}; width {width}; height {height}.",
        name=localized_overlay_record_name(record),
        position=position,
        total=total,
        left=percent(box.x),
        top=percent(box.y),
        width=percent(box.width),
        height=percent(box.height),
    )
