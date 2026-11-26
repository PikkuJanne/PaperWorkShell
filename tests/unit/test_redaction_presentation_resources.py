"""Deterministic presentation-work bounds for PWS-0602, without timing assertions."""

from __future__ import annotations

import os
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    NormalizedPagePoint,
    PageCoordinateTransform,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
    RenderedImageSize,
    ScreenPoint,
    ScreenViewport,
)
from paperworkshell.domain.redaction import MAX_REDACTION_MARKS, RedactionMark
from tests.accessibility.test_redaction_tools_accessibility import _dialog, _state


def _marks(count: int) -> tuple[RedactionMark, ...]:
    page_id = _state().page_id
    return tuple(
        RedactionMark(
            UUID(int=10_000 + index, version=4), page_id, NormalizedPageBox(0, 0, 10_000, 10_000)
        )
        for index in range(count)
    )


def test_one_projection_is_reused_for_selection_and_rebuilt_for_each_geometry_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog() as dialog:
        canvas = dialog._canvas
        marks = _marks(64)
        canvas.set_records(marks, marks[0].mark_id)
        projections = 0
        original = PageCoordinateTransform.normalized_to_screen

        def project(
            value: PageCoordinateTransform,
            point: NormalizedPagePoint,
            size: RenderedImageSize,
            viewport: ScreenViewport,
        ) -> ScreenPoint:
            nonlocal projections
            projections += 1
            return original(value, point, size, viewport)

        monkeypatch.setattr(PageCoordinateTransform, "normalized_to_screen", project)
        surface = QPixmap(canvas.size())
        canvas.render(surface)
        assert projections == 3 and len(canvas._projected_rects) == len(marks)
        canvas.render(surface)
        canvas.set_records(marks, marks[-1].mark_id)
        canvas.render(surface)
        assert projections == 3
        assert canvas._preview is not None
        canvas._preview.fill()
        canvas.render(surface)
        assert projections == 6
        canvas.resize(canvas.width() + 1, canvas.height())
        canvas.render(QPixmap(canvas.size()))
        assert projections == 9
        changed = (
            *marks[:-1],
            RedactionMark(
                marks[-1].mark_id,
                marks[-1].page_id,
                NormalizedPageBox(50_000, 50_000, 10_000, 10_000),
            ),
        )
        canvas.set_records(changed, marks[-1].mark_id)
        canvas.render(QPixmap(canvas.size()))
        assert projections == 12


def test_selection_keeps_all_native_list_entries_without_rebuilding_the_model() -> None:
    base = _state()
    state = type(base)(
        base.case_id, base.manifest_revision, base.page_id, base.editor_session_id, _marks(64)
    )
    with _dialog(state=state) as dialog:
        removals: list[tuple[int, int]] = []
        insertions: list[tuple[int, int]] = []
        model = dialog._list.model()
        model.rowsRemoved.connect(lambda _parent, first, last: removals.append((first, last)))
        model.rowsInserted.connect(lambda _parent, first, last: insertions.append((first, last)))
        last = state.records[-1]
        dialog._publish(state.select(last.mark_id))
        assert dialog._list.count() == 64 and dialog._list.currentRow() == 63
        assert not removals and not insertions
        dialog._publish(dialog.state.delete_selected())
        assert dialog._list.count() == 63 and removals and insertions
        QApplication.processEvents()


def test_direct_canvas_input_enforces_case_mark_limit_before_projection() -> None:
    with _dialog() as dialog:
        marks = _marks(MAX_REDACTION_MARKS)
        dialog._canvas.set_records(marks, None)
        with pytest.raises(ValueError, match="invalid removal preview records"):
            dialog._canvas.set_records((*marks, marks[0]), None)
        assert dialog._canvas._records == marks


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
@pytest.mark.parametrize("source_rotation", tuple(ClockwiseRotation))
@pytest.mark.parametrize("dpr", (1.0, 2.0))
def test_cached_qt_rectangles_match_domain_crop_projection_for_every_quarter_turn(
    rotation: ClockwiseRotation,
    source_rotation: ClockwiseRotation,
    dpr: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crop = NormalizedPageBox(200_000, 200_000, 600_000, 600_000)
    transform = PageCoordinateTransform(
        pdf_geometry=PdfPageCoordinateGeometry(
            media_box=PdfUserSpaceBox(-50, -20, 550, 780),
            crop_box=PdfUserSpaceBox(10, 20, 510, 720),
            page_box=PdfPageBoxSelection.CROP,
            intrinsic_rotation=source_rotation,
            user_unit=2.0,
        ),
        manifest_crop=crop,
        manifest_rotation=rotation,
    )
    with _dialog(transform=transform) as dialog:
        canvas = dialog._canvas
        page = _state().page_id
        boxes = (
            NormalizedPageBox(0, 0, 100_000, 100_000),
            NormalizedPageBox(100_000, 300_000, 300_000, 200_000),
            NormalizedPageBox(350_000, 350_000, 100_000, 100_000),
            NormalizedPageBox(700_000, 700_000, 200_000, 200_000),
        )
        marks = tuple(
            RedactionMark(UUID(int=50_000 + index, version=4), page, box)
            for index, box in enumerate(boxes)
        )
        canvas.set_records(marks, marks[2].mark_id)
        monkeypatch.setattr(
            canvas,
            "_mapping",
            lambda: (
                RenderedImageSize(901, 713),
                ScreenViewport(12.25, 47.5, 378.75, 112.125, dpr),
            ),
        )
        canvas.render(QPixmap(canvas.size()))
        size, viewport = canvas._mapping()
        expected_ids = set()
        for mark in marks:
            visible = transform.visible_part(mark.box)
            if visible is None:
                continue
            expected_ids.add(mark.mark_id)
            expected = transform.normalized_box_to_screen(visible, size, viewport)
            actual = canvas._projected_rects[mark.mark_id]
            assert (actual.left(), actual.top(), actual.right(), actual.bottom()) == pytest.approx(
                (expected.left, expected.top, expected.right, expected.bottom), abs=1e-9
            )
        assert set(canvas._projected_rects) == expected_ids
        assert "Selected region 3: UNAPPLIED" in canvas._legend_text()
        assert canvas._page_rect().top() >= canvas._legend_height()


def test_font_change_during_pointer_drag_cancels_old_geometry_and_mouse_capture() -> None:
    with _dialog() as dialog:
        canvas = dialog._canvas
        page = canvas._page_rect()
        start = QPoint(
            round(page.left() + page.width() * 0.2), round(page.top() + page.height() * 0.2)
        )
        end = QPoint(
            round(page.left() + page.width() * 0.8), round(page.top() + page.height() * 0.8)
        )
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        font = QFont(canvas.font())
        font.setPointSizeF(max(8, font.pointSizeF()) + 1)
        canvas.setFont(font)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        assert not dialog.state.records
        assert canvas._start is None and QWidget.mouseGrabber() is not canvas


@pytest.mark.parametrize("selected", (False, True))
def test_dense_preview_preserves_source_pixels_and_all_native_regions(
    selected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _state()
    marks = tuple(
        RedactionMark(
            UUID(int=100_000 + index, version=4),
            base.page_id,
            NormalizedPageBox((index % 64) * 15_000, (index // 64) * 15_000, 10_000, 10_000),
        )
        for index in range(MAX_REDACTION_MARKS)
    )
    state = type(base)(
        base.case_id,
        base.manifest_revision,
        base.page_id,
        base.editor_session_id,
        marks,
        selected_mark_id=marks[0].mark_id if selected else None,
    )
    with _dialog(state=state) as dialog:
        canvas = dialog._canvas
        assert canvas._preview is not None
        retained_color = QColor("tomato")
        canvas._preview.fill(retained_color)
        paths: list[int] = []
        legend_density: list[bool] = []
        original_path = QPainter.drawPath
        original_legend = canvas._legend_text

        def draw_path(painter: QPainter, path: QPainterPath) -> None:
            paths.append(path.elementCount())
            original_path(painter, path)

        def legend() -> str:
            legend_density.append(canvas._dense_preview)
            return original_legend()

        monkeypatch.setattr(QPainter, "drawPath", draw_path)
        monkeypatch.setattr(canvas, "_legend_text", legend)
        surface = QPixmap(canvas.size())
        canvas.render(surface)
        assert canvas._dense_preview and legend_density == [True]
        assert paths == ([9, 9] if selected else [])
        assert dialog._list.count() == MAX_REDACTION_MARKS
        assert canvas._records == marks and len(canvas._projected_rects) == MAX_REDACTION_MARKS
        description = "Dense preview: only the visible part of the selected region is shown; all regions remain in the list"
        assert description in canvas._legend_text()
        interface = QAccessible.queryAccessibleInterface(canvas)
        assert interface is not None
        assert description in interface.text(QAccessible.Text.Description)
        if not selected:
            assert "Select a region in the list" in canvas._legend_text()
        image = surface.toImage()
        page = canvas._page_rect()
        # Every sampled point is away from the selected upper-left region.
        for horizontal in (0.25, 0.5, 0.75):
            for vertical in (0.25, 0.5, 0.75):
                assert (
                    image.pixelColor(
                        round(page.left() + page.width() * horizontal),
                        round(page.top() + page.height() * vertical),
                    )
                    == retained_color
                )


def test_density_and_selection_modes_share_stable_legend_and_page_geometry() -> None:
    with _dialog() as dialog:
        canvas = dialog._canvas
        before_height = canvas._legend_height()
        before_page = canvas._page_rect()
        marks = _marks(MAX_REDACTION_MARKS)
        canvas.set_records(marks, None)
        canvas.render(QPixmap(canvas.size()))
        assert canvas._dense_preview
        assert canvas._legend_height() == before_height and canvas._page_rect() == before_page
        canvas.set_records(marks, marks[-1].mark_id)
        canvas.render(QPixmap(canvas.size()))
        assert canvas._legend_height() == before_height and canvas._page_rect() == before_page


def test_sparse_preview_retains_all_crossed_outlines_without_dense_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog() as dialog:
        canvas = dialog._canvas
        marks = _marks(3)
        canvas.set_records(marks, marks[0].mark_id)
        paths: list[int] = []
        original = QPainter.drawPath

        def draw_path(painter: QPainter, path: QPainterPath) -> None:
            paths.append(path.elementCount())
            original(painter, path)

        monkeypatch.setattr(QPainter, "drawPath", draw_path)
        canvas.render(QPixmap(canvas.size()))
        assert not canvas._dense_preview
        assert paths == [27, 27]
        assert "Dense preview" not in canvas.accessibleDescription()
