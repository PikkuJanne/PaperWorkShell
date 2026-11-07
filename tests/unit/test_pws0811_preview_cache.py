"""Current and historical preview pixels share one measured resource budget."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap

from paperworkshell.app.organizer import OrganizerOverlaySummary, OrganizerPageView
from paperworkshell.domain.image_pages import ImageQuarterTurn
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.models import OverlayKind, OverlayVisibility
from paperworkshell.ui.models import OrganizerPageListModel, PreviewState
from tests.unit.test_organizer_ui_model import (
    OTHER_CASE_ID,
    PAGE_ONE,
    PAGE_TWO,
    _application,
    _state,
)


def _pixels(color: str, *, width: int = 12, height: int = 8) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    return QPixmap.fromImage(image)


def test_rotation_history_counts_all_pixmaps_and_restores_png_undo_without_render() -> None:
    _application()
    model = OrganizerPageListModel()
    initial = OrganizerPageView(PAGE_ONE, 1, 0, True, ImageTransformIntent())
    right = replace(
        initial,
        rotation_degrees=90,
        scan_review_intent=ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_90),
    )
    red = _pixels("red")
    blue = _pixels("blue", width=8, height=12)
    cost = red.width() * red.height() * 4
    model.set_organizer_state(_state(initial))
    model.set_preview(PAGE_ONE, red)
    model.set_organizer_state(_state(right))
    assert model.preview(PAGE_ONE) is None
    assert model.retained_preview_count == 1
    assert model.retained_preview_bytes == cost
    model.set_preview(PAGE_ONE, blue)
    assert model.retained_preview_count == 2
    assert model.retained_preview_bytes == cost * 2
    model.set_organizer_state(_state(initial))
    restored = model.preview(PAGE_ONE)
    assert restored is not None
    assert restored.toImage().pixelColor(0, 0) == QColor("red")
    assert model.preview_state(PAGE_ONE) is PreviewState.READY
    model.set_organizer_state(_state(right))
    restored_right = model.preview(PAGE_ONE)
    assert restored_right is not None
    assert restored_right.toImage().pixelColor(0, 0) == QColor("blue")
    assert model.retained_preview_count == 2


def test_transforms_crop_unsupported_removal_and_case_switch_discard_history() -> None:
    _application()
    initial = OrganizerPageView(PAGE_ONE, 1, 0, True)
    right = replace(initial, rotation_degrees=90)
    for changed in (
        replace(right, crop=NormalizedPageBox(100_000, 0, 800_000, 1_000_000)),
        replace(
            right, scan_review_intent=ImageTransformIntent(contrast_factor_millionths=1_100_000)
        ),
        replace(right, preview_supported=False),
    ):
        model = OrganizerPageListModel()
        model.set_organizer_state(_state(initial))
        model.set_preview(PAGE_ONE, _pixels("red"))
        model.set_organizer_state(_state(right))
        model.set_preview(PAGE_ONE, _pixels("blue"))
        model.set_organizer_state(_state(changed))
        assert model.retained_preview_count == 0
        assert model.retained_preview_bytes == 0
        model.set_organizer_state(_state(initial))
        assert model.preview(PAGE_ONE) is None

    model = OrganizerPageListModel()
    model.set_organizer_state(_state(initial))
    model.set_preview(PAGE_ONE, _pixels("red"))
    model.set_organizer_state(_state(right))
    model.set_organizer_state(_state(OrganizerPageView(PAGE_TWO, 1, 0, True)))
    assert model.retained_preview_count == model.retained_preview_bytes == 0
    model.set_organizer_state(_state(initial))
    model.set_preview(PAGE_ONE, _pixels("red"))
    model.set_organizer_state(_state(right))
    model.set_organizer_state(replace(_state(initial), case_id=OTHER_CASE_ID))
    assert model.preview(PAGE_ONE) is None
    assert model.retained_preview_count == model.retained_preview_bytes == 0


def test_low_budget_evicts_history_before_current_and_zero_budget_releases_everything() -> None:
    _application()
    red = _pixels("red")
    cost = red.width() * red.height() * 4
    model = OrganizerPageListModel(maximum_preview_cache_bytes=cost * 2)
    initial = OrganizerPageView(PAGE_ONE, 1, 0, True)
    right = replace(initial, rotation_degrees=90)
    model.set_organizer_state(_state(initial))
    model.set_preview(PAGE_ONE, red)
    model.set_organizer_state(_state(right))
    model.set_preview(PAGE_ONE, _pixels("blue"))
    assert model.retained_preview_count == 2
    assert model.retained_preview_bytes == cost * 2
    assert model.evict_previews(cost, protected_page_ids=(PAGE_ONE,)) == ()
    assert model.preview(PAGE_ONE) is not None
    assert model.retained_preview_count == 1
    assert model.retained_preview_bytes == cost
    model.set_organizer_state(_state(initial))
    assert model.preview(PAGE_ONE) is None
    model.evict_previews(0)
    assert model.retained_preview_count == model.retained_preview_bytes == 0


def test_pixmap_ownership_and_failed_null_or_oversized_preview_are_truthful() -> None:
    _application()
    model = OrganizerPageListModel(maximum_preview_cache_bytes=1024)
    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 0, True)))
    supplied = _pixels("red")
    model.set_preview(PAGE_ONE, supplied)
    supplied.fill(QColor("blue"))
    retained = model.preview(PAGE_ONE)
    assert retained is not None
    assert retained.toImage().pixelColor(0, 0) == QColor("red")
    retained.fill(QColor("green"))
    unchanged = model.preview(PAGE_ONE)
    assert unchanged is not None
    assert unchanged.toImage().pixelColor(0, 0) == QColor("red")
    model.set_preview(PAGE_ONE, QPixmap())
    assert model.preview_state(PAGE_ONE) is PreviewState.FAILED
    assert model.preview(PAGE_ONE) is None
    model.set_preview(PAGE_ONE, _pixels("white", width=32, height=32))
    assert model.preview_state(PAGE_ONE) is PreviewState.FAILED
    assert model.retained_preview_count == model.retained_preview_bytes == 0


def test_central_constrained_policy_covers_current_and_historical_pixels() -> None:
    _application()
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    model = OrganizerPageListModel(maximum_preview_cache_bytes=policy.thumbnail_preview_cache_bytes)
    pages = tuple(
        replace(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            page_id=PAGE_ONE if i == 0 else PAGE_TWO,
            position=i + 1,
        )
        for i in range(2)
    )
    # An injected smaller budget is stricter than the actual constrained profile.
    pixmap = _pixels("white", width=24, height=32)
    cost = pixmap.width() * pixmap.height() * 4
    budget = min(policy.thumbnail_preview_cache_bytes, cost * 3)
    model = OrganizerPageListModel(maximum_preview_cache_bytes=budget)
    model.set_organizer_state(_state(*pages))
    observed: list[int] = []
    model.dataChanged.connect(lambda *_args: observed.append(model.retained_preview_bytes))
    for turn in (0, 90, 180, 270, 0):
        model.set_organizer_state(_state(*(replace(page, rotation_degrees=turn) for page in pages)))
        for page in pages:
            if model.preview_state(page.page_id) is not PreviewState.READY:
                model.set_preview(page.page_id, pixmap)
            assert model.retained_preview_bytes <= budget
    assert observed and max(observed) <= budget
    assert model.retained_preview_count <= 3


def test_source_filename_case_and_source_only_preview_explanation_are_preserved() -> None:
    _application()
    model = OrganizerPageListModel()
    page = OrganizerPageView(
        PAGE_ONE,
        1,
        0,
        True,
        document_number=1,
        source_page_number=1,
        source_display_name="Äänekoski APPLICATION.PDF",
        overlay_summaries=(
            OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.VISIBLE, 1),
        ),
    )
    model.set_organizer_state(_state(page))
    index = model.index(0, 0)
    assert "Äänekoski APPLICATION.PDF" in str(
        model.data(index, int(Qt.ItemDataRole.AccessibleTextRole))
    )
    assert "1 saved mark — open editor to preview" in str(model.data(index))
