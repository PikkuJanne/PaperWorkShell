"""Qt item semantics for path-free document, page and edit state."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QListView

from paperworkshell.app.organizer import (
    OrganizerOverlaySummary,
    OrganizerPageView,
    OrganizerViewState,
)
from paperworkshell.domain.image_transforms import ImageNormalizedCrop, ImageTransformIntent
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import OverlayKind, OverlayVisibility
from paperworkshell.ui.models import OrganizerPageListModel

CASE_ID = UUID("71000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("72000000-0000-4000-8000-000000000001")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _page(
    *,
    crop: NormalizedPageBox | None = None,
    scan_review_intent: ImageTransformIntent | None = None,
    overlay_summaries: tuple[OrganizerOverlaySummary, ...] = (),
    removal_mark_count: int = 0,
) -> OrganizerPageView:
    return OrganizerPageView(
        PAGE_ID,
        1,
        90,
        True,
        document_number=2,
        source_page_number=3,
        crop=crop,
        scan_review_intent=scan_review_intent,
        overlay_summaries=overlay_summaries,
        removal_mark_count=removal_mark_count,
    )


def _state(page: OrganizerPageView, *, pending: bool = False) -> OrganizerViewState:
    return OrganizerViewState(CASE_ID, 4, (page,), True, False, pending)


def test_qt_item_exposes_document_page_role_selection_and_edit_description() -> None:
    app = _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(_page(crop=NormalizedPageBox(100_000, 200_000, 700_000, 600_000)))
    )
    view = QListView()
    view.setModel(model)
    view.show()
    view.setCurrentIndex(model.index(0, 0))
    view.selectionModel().select(model.index(0, 0), QItemSelectionModel.SelectionFlag.Select)
    app.processEvents()
    try:
        interface = QAccessible.queryAccessibleInterface(view)
        assert interface is not None
        item = interface.child(0)
        assert item is not None
        assert item.role() is QAccessible.Role.ListItem
        assert item.state().selected
        name = item.text(QAccessible.Text.Name)
        assert "Page 1 of 1" in name
        assert "document 2, source page 3" in name
        assert "rotated 90 degrees" in name
        assert "preview not loaded" in name
        description = item.text(QAccessible.Text.Description)
        assert "Saved revision 4" in description
        assert "left 10 percent, top 20 percent, width 70 percent, height 60 percent" in description
        assert str(PAGE_ID) not in name + description
        assert str(CASE_ID) not in name + description
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_scan_crop_deskew_contrast_and_cleanup_have_numeric_descriptions() -> None:
    _application()
    model = OrganizerPageListModel()
    intent = ImageTransformIntent(
        crop=ImageNormalizedCrop(125_000, 200_000, 750_000, 600_000),
        deskew_angle_millidegrees_clockwise=-1_250,
        contrast_factor_millionths=1_150_000,
        background_cleanup_strength_millionths=250_000,
    )
    model.set_organizer_state(_state(_page(scan_review_intent=intent)))
    description = str(model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleDescriptionRole)))

    assert "left 12.5 percent" in description
    assert "deskew -1.25 degrees clockwise" in description
    assert "contrast 115 percent" in description
    assert "background cleanup 25 percent" in description


def test_overlay_types_visibility_and_removal_intent_are_distinct() -> None:
    _application()
    model = OrganizerPageListModel()
    summaries = (
        OrganizerOverlaySummary(OverlayKind.TEXT, OverlayVisibility.VISIBLE, 2),
        OrganizerOverlaySummary(OverlayKind.SIGNATURE_IMAGE, OverlayVisibility.VISIBLE, 1),
        OrganizerOverlaySummary(OverlayKind.SHAPE, OverlayVisibility.PARTIALLY_VISIBLE, 1),
        OrganizerOverlaySummary(OverlayKind.HIGHLIGHT, OverlayVisibility.OUTSIDE_ACTIVE_CROP, 1),
    )
    model.set_organizer_state(_state(_page(overlay_summaries=summaries, removal_mark_count=2)))
    index = model.index(0, 0)
    name = str(model.data(index, int(Qt.ItemDataRole.AccessibleTextRole)))
    description = str(model.data(index, int(Qt.ItemDataRole.AccessibleDescriptionRole)))

    assert "5 visual overlays; 2 unapplied removal marks" in name
    assert "2 text overlays, visible" in description
    assert "1 signature image overlay, visible" in description
    assert "1 shape overlay, partially outside the crop; needs review" in description
    assert "1 highlight overlay, outside the crop; not visible" in description
    assert "do not create digital signatures" in description
    assert "Shapes and highlights do not permanently remove information" in description
    assert "Removal marks are unapplied intent. Information remains in the case" in description


def test_pending_save_then_saved_state_and_removed_marks_never_stay_stale() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(_page(removal_mark_count=1), pending=True))
    description = str(model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleDescriptionRole)))
    assert "Unsaved organizer changes; saved revision 4" in description
    assert "unapplied" in description

    model.set_organizer_state(replace(_state(_page()), revision=5))
    description = str(model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleDescriptionRole)))
    assert "Saved revision 5" in description
    assert "Unsaved" not in description
    assert "Removal marks" not in description


def test_preview_failure_and_retry_change_accessible_item_name() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(_page()))
    model.set_preview_failed(PAGE_ID)
    assert "preview failed" in str(
        model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleTextRole))
    )
    assert model.reset_failed_preview(PAGE_ID)
    assert "preview not loaded" in str(
        model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleTextRole))
    )
