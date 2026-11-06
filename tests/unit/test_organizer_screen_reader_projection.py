"""Closed projection facts for screen readers; no source names or mark values."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.organizer import OrganizerOverlaySummary, OrganizerPageView
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import (
    CropPageCommand,
    PageCommandHistory,
    PageOperationError,
)
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MAX_OVERLAYS,
    OverlayKind,
    OverlayRecord,
    OverlayVisibility,
    RgbaColor,
    TextualOverlayPayload,
)
from tests.unit.test_organizer_backend import (
    CASE_ID,
    IMAGE_PAGE_ID,
    PAGE_IDS,
    _backend,
    _document,
    _snapshot,
)


def test_document_ordinals_and_source_page_numbers_survive_packet_reordering() -> None:
    backend, *_ = _backend(_document(page_count=2, include_image=True))
    initial = backend.open_case(CASE_ID)
    assert [(page.document_number, page.source_page_number) for page in initial.pages] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]
    moved = backend.move_page(IMAGE_PAGE_ID, 1)
    assert [(page.document_number, page.source_page_number) for page in moved.pages] == [
        (2, 1),
        (1, 1),
        (1, 2),
    ]
    assert moved.autosave_pending


def test_projection_groups_overlay_types_and_crop_visibility_without_text_values() -> None:
    document = _document(page_count=1)
    crop = NormalizedPageBox(250_000, 250_000, 500_000, 500_000)
    history = PageCommandHistory.from_snapshot(_snapshot(document))
    document = history.execute(CropPageCommand(PAGE_IDS[0], crop)).to_candidate(_snapshot(document))
    boxes = (
        NormalizedPageBox(300_000, 300_000, 100_000, 100_000),
        NormalizedPageBox(400_000, 400_000, 100_000, 100_000),
        NormalizedPageBox(200_000, 200_000, 100_000, 100_000),
        NormalizedPageBox(0, 0, 100_000, 100_000),
    )
    document["overlays"] = [
        OverlayRecord(
            UUID(f"60000000-0000-4000-8000-{index:012d}"),
            PAGE_IDS[0],
            box,
            index,
            TextualOverlayPayload(
                OverlayKind.TEXT,
                "PWS_PRIVATE_VALUE",
                DEFAULT_FONT_TOKEN,
                12,
                RgbaColor("#000000ff"),
            ),
        ).to_manifest()
        for index, box in enumerate(boxes, 1)
    ]
    document["redaction_marks"] = [
        RedactionMark(
            UUID("61000000-0000-4000-8000-000000000001"), PAGE_IDS[0], boxes[0]
        ).to_manifest_record()
    ]
    sources = cast(list[dict[str, object]], document["sources"])
    sources[0]["display_name"] = "PWS_PRIVATE_NAME.pdf"
    backend, *_ = _backend(document)
    state = backend.open_case(CASE_ID)
    page = state.pages[0]
    assert page.crop == crop
    assert page.removal_mark_count == 1
    assert page.overlay_summaries == (
        OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.OUTSIDE_ACTIVE_CROP, 1),
        OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.PARTIALLY_VISIBLE, 1),
        OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.VISIBLE, 2),
    )
    assert "PWS_PRIVATE_VALUE" not in repr(state)
    assert "PWS_PRIVATE_NAME" not in repr(state)


@pytest.mark.parametrize(
    "changes",
    [
        {"document_number": 1},
        {"document_number": True, "source_page_number": 1},
        {"document_number": 0, "source_page_number": 1},
        {"document_number": 1, "source_page_number": 4097},
        {"removal_mark_count": -1},
        {"removal_mark_count": True},
        {"removal_mark_count": 4097},
        {
            "overlay_summaries": (
                OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.VISIBLE, 1),
            )
            * 2
        },
        {
            "overlay_summaries": (
                OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.VISIBLE, MAX_OVERLAYS),
                OrganizerOverlaySummary(OverlayKind.DATE, OverlayVisibility.VISIBLE, 1),
            )
        },
    ],
)
def test_invalid_or_unbounded_page_semantic_facts_are_rejected(changes: dict[str, object]) -> None:
    page = OrganizerPageView(PAGE_IDS[0], 1, 0, True)
    with pytest.raises(PageOperationError):
        OrganizerPageView(
            page.page_id,
            page.position,
            page.rotation_degrees,
            page.preview_supported,
            document_number=cast(int | None, changes.get("document_number")),
            source_page_number=cast(int | None, changes.get("source_page_number")),
            removal_mark_count=cast(int, changes.get("removal_mark_count", 0)),
            overlay_summaries=cast(
                tuple[OrganizerOverlaySummary, ...], changes.get("overlay_summaries", ())
            ),
        )


@pytest.mark.parametrize(
    "kind,visibility,count",
    [
        (OverlayKind.TEXT, OverlayVisibility.PAGE_INACTIVE, 1),
        (OverlayKind.TEXT, OverlayVisibility.VISIBLE, 0),
        (OverlayKind.TEXT, OverlayVisibility.VISIBLE, True),
        (OverlayKind.TEXT, OverlayVisibility.VISIBLE, MAX_OVERLAYS + 1),
    ],
)
def test_invalid_overlay_semantic_summary_is_rejected(
    kind: OverlayKind, visibility: OverlayVisibility, count: int
) -> None:
    with pytest.raises(PageOperationError):
        OrganizerOverlaySummary(kind, visibility, count)
