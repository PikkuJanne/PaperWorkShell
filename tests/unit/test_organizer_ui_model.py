from __future__ import annotations

import os
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.ui.models import OrganizerPageListModel, PreviewState

CASE_ID = UUID("70000000-0000-4000-8000-000000000001")
PAGE_ONE = UUID("71000000-0000-4000-8000-000000000001")
PAGE_TWO = UUID("71000000-0000-4000-8000-000000000002")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state(*pages: OrganizerPageView) -> OrganizerViewState:
    return OrganizerViewState(CASE_ID, 2, pages, True, False, False)


def test_model_exposes_order_rotation_and_textual_preview_accessibility_roles() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            OrganizerPageView(PAGE_TWO, 2, 270, False),
        )
    )

    first = model.index(0, 0)
    second = model.index(1, 0)
    assert model.rowCount() == 2
    assert model.data(first, int(Qt.ItemDataRole.DisplayRole)) == (
        "Page 1 of 2\n0\N{DEGREE SIGN} rotation\nPreview not loaded"
    )
    assert model.data(first, int(Qt.ItemDataRole.AccessibleTextRole)) == (
        "Page 1 of 2; rotated 0 degrees; preview not loaded"
    )
    assert model.data(second, model.PreviewStateRole) == PreviewState.UNAVAILABLE.value
    assert model.data(second, model.PageIdRole) == PAGE_TWO
    assert model.flags(first) == Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    assert not model.flags(first) & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.flags(first) & Qt.ItemFlag.ItemIsDropEnabled


def test_model_detaches_previews_and_discards_removed_page_state() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            OrganizerPageView(PAGE_TWO, 2, 0, True),
        )
    )
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    preview = QPixmap.fromImage(image.copy())
    model.set_preview(PAGE_ONE, preview)

    assert model.data(model.index(0, 0), model.PreviewStateRole) == PreviewState.READY.value
    assert isinstance(model.data(model.index(0, 0), int(Qt.ItemDataRole.DecorationRole)), QPixmap)

    model.set_organizer_state(_state(OrganizerPageView(PAGE_TWO, 1, 90, True)))

    assert model.rowCount() == 1
    assert model.row_for_page(PAGE_ONE) is None
    assert model.page_id_at(0) == PAGE_TWO
    assert model.data(model.index(0, 0), model.PreviewStateRole) == PreviewState.NOT_LOADED.value


def test_rotation_invalidates_stale_pixels_before_autosave_preview_suppression() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 0, True)))
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    model.set_preview(PAGE_ONE, QPixmap.fromImage(image))

    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 90, True)))

    index = model.index(0, 0)
    assert model.data(index, model.PreviewStateRole) == PreviewState.NOT_LOADED.value
    assert model.data(index, int(Qt.ItemDataRole.DecorationRole)) is None
    assert model.retained_preview_count == 0


def test_lru_eviction_and_terminal_loading_states_remain_truthful() -> None:
    _application()
    model = OrganizerPageListModel()
    pages = tuple(
        OrganizerPageView(
            UUID(f"71000000-0000-4000-8000-{number:012d}"),
            number,
            0,
            True,
        )
        for number in range(1, 5)
    )
    model.set_organizer_state(_state(*pages))
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    for page in pages[:3]:
        model.set_preview(page.page_id, QPixmap.fromImage(image))
    model.touch_previews((pages[0].page_id,))

    evicted = model.evict_previews(2, protected_page_ids=(pages[2].page_id,))

    assert evicted == (pages[1].page_id,)
    assert model.retained_preview_count == 2
    assert model.preview_state(pages[1].page_id) is PreviewState.NOT_LOADED
    assert model.data(model.index(1, 0), int(Qt.ItemDataRole.DecorationRole)) is None

    model.evict_previews(
        1,
        protected_page_ids=(pages[0].page_id, pages[2].page_id),
    )
    assert model.retained_preview_count == 1

    model.set_preview_loading((pages[3].page_id,))
    model.finish_preview_loading((pages[3].page_id,))
    assert model.preview_state(pages[3].page_id) is PreviewState.NOT_LOADED
    model.set_preview_loading((pages[3].page_id,))
    model.set_preview_failed(pages[3].page_id)
    model.finish_preview_loading((pages[3].page_id,))
    assert model.preview_state(pages[3].page_id) is PreviewState.FAILED
    assert model.reset_failed_preview(pages[3].page_id)
    assert model.preview_state(pages[3].page_id) is PreviewState.NOT_LOADED
    assert not model.reset_failed_preview(pages[3].page_id)
