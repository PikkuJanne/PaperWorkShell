"""Regression coverage for previews arriving after the view has laid out rows."""

from __future__ import annotations

import os
from typing import cast
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QListView

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker
from paperworkshell.ui.models import OrganizerPageListModel
from paperworkshell.ui.page_delegate import OrganizerPageDelegate
from paperworkshell.ui.window import PaperWorkShellWindow


def _app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_partial_previews_and_rotations_keep_identical_nonintersecting_rows() -> None:
    app = _app()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: (_ for _ in ()).throw(AssertionError("no backend")))
    window = PaperWorkShellWindow(commands, worker)
    pages = tuple(OrganizerPageView(uuid4(), i + 1, 0, True) for i in range(4))
    window._state_changed(OrganizerViewState(uuid4(), 1, pages, False, False, False))
    window.show()
    app.processEvents()
    window._set_trace_lens("arrange")
    view = window._trace_arrangement
    try:
        assert view.viewMode() == QListView.ViewMode.ListMode
        before = [view.visualRect(window._model.index(i, 0)) for i in range(4)]
        for size in ((720, 240), (240, 720), (900, 70)):
            image = QImage(*size, QImage.Format.Format_RGBA8888)
            image.fill(QColor("blue"))
            for page in reversed(pages):
                window._model.set_preview(page.page_id, QPixmap.fromImage(image))
                app.processEvents()
                rects = [view.visualRect(window._model.index(i, 0)) for i in range(4)]
                assert rects == before
                for i, rect in enumerate(rects):
                    assert rect.height() >= 184
                    assert all(not rect.intersects(other) for other in rects[i + 1 :])
                    image_rect, caption_rect = OrganizerPageDelegate.regions(rect)
                    assert rect.contains(image_rect) and rect.contains(caption_rect)
                    assert not image_rect.intersects(caption_rect)
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def test_reserved_regions_fit_narrow_widths_and_enlarged_rows() -> None:
    for width in (240, 360, 720):
        for height in (184, 280, 400):
            rect = QRect(0, 0, width, height)
            image, caption = OrganizerPageDelegate.regions(rect)
            assert rect.contains(image) and rect.contains(caption)
            assert not image.intersects(caption)
            assert caption.width() > 80


def test_real_filename_and_source_page_remain_accessible_after_reorder() -> None:
    from dataclasses import replace

    from PySide6.QtCore import Qt

    _app()
    model = OrganizerPageListModel()
    first = OrganizerPageView(
        uuid4(),
        1,
        90,
        True,
        document_number=2,
        source_page_number=3,
        source_display_name="Hakemus äö — scan.png (import 2)",
    )
    state = OrganizerViewState(uuid4(), 1, (first,), False, False, False)
    model.set_organizer_state(state)
    before = str(model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleTextRole)))
    model.set_organizer_state(replace(state, revision=2))
    after = str(model.data(model.index(0, 0), int(Qt.ItemDataRole.AccessibleTextRole)))
    assert "scan.png (import 2)" in before and "source page 3" in before
    assert before == after
