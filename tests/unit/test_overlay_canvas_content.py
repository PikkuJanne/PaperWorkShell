"""Real canvas painting, beyond the records delivered by the editor."""

from __future__ import annotations

import os
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QPushButton, QSpinBox

import paperworkshell.ui.overlay_canvas as canvas_module
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
)
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MAX_OVERLAYS,
    MarkOverlayPayload,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    TextualOverlayPayload,
)
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from tests.unit.test_overlay_tools_dialog import _application, _dialog

PAGE = UUID("30000000-0000-4000-8000-000000000001")
MARK = UUID("30000000-0000-4000-8000-000000000091")
GREEN = RgbaColor("#00b400ff")


def _record(value: str = "Internal trial", points: int = 24) -> OverlayRecord:
    return OverlayRecord(
        MARK,
        PAGE,
        NormalizedPageBox(150_000, 200_000, 650_000, 300_000),
        0,
        TextualOverlayPayload(OverlayKind.TEXT, value, DEFAULT_FONT_TOKEN, points, GREEN),
    )


def _transform(
    rotation: int = 0,
    crop: NormalizedPageBox | None = None,
) -> PageCoordinateTransform:
    return PageCoordinateTransform(
        pdf_geometry=PdfPageCoordinateGeometry(
            PdfUserSpaceBox(0.0, 0.0, 300.0, 400.0),
            None,
            PdfPageBoxSelection.MEDIA,
            ClockwiseRotation.DEGREES_0,
        ),
        manifest_crop=crop or NormalizedPageBox.full_page(),
        manifest_rotation=ClockwiseRotation(rotation),
    )


def _canvas(transform: PageCoordinateTransform | None = None) -> OverlayEditorCanvas:
    _application()
    selected_transform = transform or _transform()
    crop = selected_transform.manifest_crop
    width, height = round(300 * crop.width / 1_000_000), round(400 * crop.height / 1_000_000)
    if int(selected_transform.manifest_rotation) in {90, 270}:
        width, height = height, width
    preview = QPixmap(max(1, width), max(1, height))
    preview.fill(Qt.GlobalColor.white)
    canvas = OverlayEditorCanvas(transform=selected_transform, preview=preview)
    canvas.set_guide_options(visible=False, snap=False, step_millionths=50_000)
    return canvas


def _render(canvas: OverlayEditorCanvas) -> QImage:
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    canvas.render(image)
    return image


def _green(image: QImage) -> set[tuple[int, int]]:
    # Opaque core pixels avoid platform-specific antialiasing/glyph goldens.
    pixels = bytes(image.constBits())
    stride = image.bytesPerLine()
    return {
        (offset % stride // 4, offset // stride)
        for offset in range(0, len(pixels), 4)
        if pixels[offset : offset + 4] == b"\x00\xb4\x00\xff"
    }


def _bounds(points: set[tuple[int, int]]) -> tuple[int, int, int, int]:
    assert points, "The actual colored mark must paint, not only a type label."
    return (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )


@pytest.mark.parametrize("selected", (False, True))
def test_canvas_passes_literal_value_and_font_to_real_painting_and_changes_pixels(
    selected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, QFont]] = []
    original_path_text, original_painter_text = QPainterPath.addText, QPainter.drawText

    def path_text(self: QPainterPath, *args: Any, **kwargs: Any) -> Any:
        if args and isinstance(args[-1], str):
            seen.append((args[-1], QFont(args[-2])))
        return original_path_text(self, *args, **kwargs)

    def painter_text(self: QPainter, *args: Any, **kwargs: Any) -> Any:
        if args and isinstance(args[-1], str):
            seen.append((args[-1], QFont(self.font())))
        return original_painter_text(self, *args, **kwargs)

    monkeypatch.setattr(QPainterPath, "addText", path_text)
    monkeypatch.setattr(QPainter, "drawText", painter_text)
    canvas = _canvas()
    record = _record()
    try:
        canvas.set_records((record,), MARK if selected else None)
        first = _render(canvas)
        assert any(value == "Internal trial" for value, _font in seen)
        assert any(value == "Internal trial" and font.pixelSize() == 24 for value, font in seen)
        first_pixels = _green(first)
        assert len(first_pixels) > 20
        assert type(record.payload) is TextualOverlayPayload
        canvas.set_records(
            (replace(record, payload=replace(record.payload, value="MMMMMMMM")),),
            MARK if selected else None,
        )
        assert _green(_render(canvas)) != first_pixels
    finally:
        canvas.deleteLater()
        _application().processEvents()


@pytest.mark.parametrize("selected", (False, True))
def test_geometric_checkmark_paints_both_segments_instead_of_its_type_label(selected: bool) -> None:
    canvas = _canvas()
    record = replace(
        _record(),
        box=NormalizedPageBox(200_000, 200_000, 400_000, 300_000),
        payload=MarkOverlayPayload(OverlayKind.CHECKMARK, GREEN),
    )
    try:
        canvas.set_records((record,), MARK if selected else None)
        pixels = _green(_render(canvas))
        left, top, right, bottom = _bounds(pixels)
        screen = canvas._screen_box_for(record)
        assert screen is not None
        assert screen.left <= left < right <= screen.right
        assert screen.top <= top < bottom <= screen.bottom
        # Both arms of the declared check geometry, not a green caption.
        pivot_x = screen.left + screen.right
        assert any(x < pivot_x / 2 and y > (screen.top + screen.bottom) / 2 for x, y in pixels)
        assert any(x > pivot_x / 2 and y < (screen.top + screen.bottom) / 2 for x, y in pixels)
    finally:
        canvas.deleteLater()


@pytest.mark.parametrize("physical_size_known", (True, False))
def test_point_size_and_zoom_change_actual_ink_proportionally(physical_size_known: bool) -> None:
    canvas = _canvas(_transform() if physical_size_known else PageCoordinateTransform())
    try:
        record = _record("MMMM", 12)
        canvas.set_records((record,), None)
        small = _bounds(_green(_render(canvas)))
        assert type(record.payload) is TextualOverlayPayload
        canvas.set_records(
            (replace(record, payload=replace(record.payload, font_size_points=24)),), None
        )
        large = _bounds(_green(_render(canvas)))
        assert large[3] - large[1] > 1.5 * (small[3] - small[1])
        canvas.set_zoom_percent(200)
        zoomed = _bounds(_green(_render(canvas)))
        assert zoomed[3] - zoomed[1] > 1.5 * (large[3] - large[1])
    finally:
        canvas.deleteLater()


@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_text_rotates_with_page_and_stays_inside_the_declared_box(rotation: int) -> None:
    canvas = _canvas(_transform(rotation))
    record = _record("MMMM", 24)
    try:
        canvas.set_records((record,), None)
        left, top, right, bottom = _bounds(_green(_render(canvas)))
        screen = canvas._screen_box_for(record)
        assert screen is not None
        assert screen.left <= left <= right <= screen.right
        assert screen.top <= top <= bottom <= screen.bottom
        assert (right - left > bottom - top) is (rotation in (0, 180))
    finally:
        canvas.deleteLater()


def test_partial_crop_clips_full_intent_and_selection_does_not_cover_ink() -> None:
    crop = NormalizedPageBox(300_000, 100_000, 500_000, 700_000)
    canvas = _canvas(_transform(0, crop))
    record = _record("MMMMMMMMMMMMMMMM", 100)
    try:
        canvas.set_records((record,), None)
        normal = _green(_render(canvas))
        left, top, right, bottom = _bounds(normal)
        screen = canvas._screen_box_for(record)
        assert screen is not None
        assert screen.left <= left <= right <= screen.right
        assert screen.top <= top <= bottom <= screen.bottom
        canvas.set_records((record,), MARK)
        assert _green(_render(canvas)) == normal
    finally:
        canvas.deleteLater()


def test_unknown_page_size_is_explicit_and_does_not_invent_paper_dimensions() -> None:
    canvas = _canvas(PageCoordinateTransform())
    try:
        canvas.set_records((_record(),), MARK)
        assert "Text size is approximate in this preview" in canvas.accessibleDescription()
        assert _green(_render(canvas))
        assert canvas.focusPolicy() is Qt.FocusPolicy.NoFocus
        assert "complete keyboard path" in canvas.accessibleDescription()
    finally:
        canvas.deleteLater()


def test_partial_crop_keeps_original_text_center_instead_of_reanchoring_visible_part() -> None:
    canvas = _canvas(_transform(0, NormalizedPageBox(0, 350_000, 1_000_000, 400_000)))
    try:
        # The full mark's ink center is exactly the crop's top edge. Only its
        # lower half is visible. Re-centering into the visible remainder would
        # incorrectly move the ink well below that edge.
        canvas.set_records((_record("MMMM", 24),), None)
        _left, top, _right, _bottom = _bounds(_green(_render(canvas)))
        assert top <= canvas._page_rect().top() + 2
    finally:
        canvas.deleteLater()


def test_actual_editor_typing_and_font_edit_repaint_the_entered_value_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values: list[str] = []
    original = QPainterPath.addText

    def add_text(self: QPainterPath, *args: Any, **kwargs: Any) -> Any:
        if args and isinstance(args[-1], str):
            values.append(args[-1])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(QPainterPath, "addText", add_text)
    with _dialog() as dialog:
        add = dialog.findChild(QPushButton, "addTextButton")
        value = dialog.findChild(QLineEdit, "coreOverlayValueEdit")
        font = dialog.findChild(QSpinBox, "coreOverlayFontSizeSpin")
        canvas = dialog.findChild(OverlayEditorCanvas, "overlayEditorCanvas")
        assert add is not None and value is not None and font is not None and canvas is not None
        QTest.mouseClick(add, Qt.MouseButton.LeftButton)
        QTest.keyClicks(value, "Internal trial")
        font.setValue(24)
        _application().processEvents()
        _render(canvas)
        assert "Internal trial" in values
        assert not dialog.apply_pending


def test_font_registration_requires_live_gui_and_reuses_one_bounded_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _application()
    with monkeypatch.context() as no_gui:
        no_gui.setattr(QGuiApplication, "instance", lambda: None)
        assert canvas_module._preview_font_family() is None
    previous_attempted, previous_family = canvas_module._font_attempted, canvas_module._font_family
    monkeypatch.setattr(canvas_module, "_font_attempted", False)
    monkeypatch.setattr(canvas_module, "_font_family", None)
    calls: list[int] = []
    original = QFontDatabase.addApplicationFontFromData

    def register(data: Any) -> int:
        assert len(data) == 556_216
        ident = original(data)
        calls.append(ident)
        return ident

    monkeypatch.setattr(QFontDatabase, "addApplicationFontFromData", register)
    try:
        first = canvas_module._preview_font_family()
        assert first
        assert canvas_module._preview_font_family() == first
        assert canvas_module._preview_font_family() == first
        assert len(calls) == 1
    finally:
        for ident in calls:
            if ident >= 0:
                QFontDatabase.removeApplicationFont(ident)
        canvas_module._font_attempted, canvas_module._font_family = (
            previous_attempted,
            previous_family,
        )


def test_changed_font_bytes_are_never_registered_and_preview_discloses_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _application()
    changed = tmp_path / "changed-font.ttf"
    changed.write_bytes(b"x" * 556_216)

    class Distribution:
        def locate_file(self, name: str) -> Path:
            assert name == "ocrmypdf/data/NotoSans-Regular.ttf"
            return changed

    monkeypatch.setattr(metadata, "distribution", lambda _name: Distribution())
    monkeypatch.setattr(canvas_module, "_font_attempted", False)
    monkeypatch.setattr(canvas_module, "_font_family", None)

    def reject_parse(_data: Any) -> int:
        raise AssertionError("Changed font bytes must not reach Qt's font loader.")

    monkeypatch.setattr(QFontDatabase, "addApplicationFontFromData", reject_parse)
    canvas = _canvas()
    try:
        canvas.set_records((_record(),), None)
        assert "preview font is unavailable" in canvas.accessibleDescription()
        _render(canvas)
    finally:
        canvas.deleteLater()


def test_transparent_text_does_not_paint_and_model_count_is_bounded() -> None:
    canvas = _canvas()
    record = _record()
    assert type(record.payload) is TextualOverlayPayload
    transparent = replace(record, payload=replace(record.payload, color=RgbaColor("#00b40000")))
    try:
        canvas.set_records((transparent,), MARK)
        assert _green(_render(canvas)) == set()
        with pytest.raises(ValueError, match="records"):
            canvas.set_records((record,) * (MAX_OVERLAYS + 1), None)
    finally:
        canvas.deleteLater()
