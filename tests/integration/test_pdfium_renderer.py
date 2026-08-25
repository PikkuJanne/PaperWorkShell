from __future__ import annotations

import hashlib
import json
import math
import traceback
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image

from paperworkshell.domain.pdf_geometry import (
    PdfPageGeometryLimits,
    PdfPageRectangle,
    PdfPageRenderGeometry,
    PdfResolvedPageGeometry,
    fit_pdf_page_geometry,
)
from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterLayout,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
)
from paperworkshell.pdf import pdfium_renderer as pdfium_renderer_module
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _ROOT / "tests" / "fixtures" / "synthetic" / "v1"
_GOLDEN_INDEX = _ROOT / "tests" / "golden" / "renderer-v1" / "index.json"


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled = cancelled
        self.expired = expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


class _GeometryPage:
    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float],
        size: tuple[float, float],
        rotation: PdfRenderRotation,
    ) -> None:
        self.bbox = bbox
        self.size = size
        self.rotation = rotation

    def set_mediabox(self, *_values: float) -> None:
        return

    def set_cropbox(self, *_values: float) -> None:
        return

    def get_rotation(self) -> int:
        return int(self.rotation)

    def get_bbox(self) -> tuple[float, float, float, float]:
        return self.bbox

    def get_size(self) -> tuple[float, float]:
        return self.size


class _ChunkedSource:
    def __init__(self, value: bytes, maximum_read: int) -> None:
        self._stream = BytesIO(value)
        self.maximum_read = maximum_read

    def read(self, size: int | None = -1, /) -> bytes:
        if size is None or size < 0:
            size = self.maximum_read
        return self._stream.read(min(size, self.maximum_read))

    def seek(self, offset: int, whence: int = 0, /) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()


class _FailingFlush(BytesIO):
    def flush(self) -> Never:
        raise OSError("PRIVATE-DESTINATION-CANARY")


class _RestoreFailingSource(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.entry_position = 7
        self.fail_restore = False
        super().seek(self.entry_position)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.fail_restore and whence == 0 and offset == self.entry_position:
            raise OSError("PRIVATE-SOURCE-CANARY")
        return super().seek(offset, whence)


class _ArmRestoreFailure(BytesIO):
    def __init__(self, source: _RestoreFailingSource) -> None:
        super().__init__()
        self.source = source

    def flush(self) -> None:
        self.source.fail_restore = True
        super().flush()


class _TrackedClose:
    def __init__(
        self,
        label: str,
        events: list[str],
        close_error: BaseException | None = None,
    ) -> None:
        self.label = label
        self.events = events
        self.close_error = close_error

    def close(self) -> None:
        self.events.append(self.label)
        if self.close_error is not None:
            raise self.close_error


def _limits() -> PdfPageRenderLimits:
    return PdfPageRenderLimits(
        maximum_pages=32,
        maximum_width_pixels=512,
        maximum_height_pixels=512,
        maximum_pixels=262_144,
        maximum_bitmap_bytes=1_048_576,
        maximum_destination_bytes=1_048_576,
        maximum_memory_bytes=1_048_576,
        wall_time_ms=10_000,
    )


def _renderer() -> PdfiumPageRenderer:
    return PdfiumPageRenderer(PdfPageGeometryLimits(1_000_000, 1_000_000))


def _float32_crop_geometry() -> tuple[
    PdfResolvedPageGeometry,
    PdfPageRenderGeometry,
    tuple[float, float, float, float],
    tuple[float, float],
]:
    media = PdfPageRectangle(0.0, 0.0, 36.0, 24.0)
    crop = PdfPageRectangle(0.372924, 0.523992, 34.80966, 19.120752)
    geometry = PdfResolvedPageGeometry(
        media_box=media,
        crop_box=crop,
        effective_box=crop,
        page_box=PdfPageBox.CROP,
        intrinsic_rotation=PdfRenderRotation.DEGREES_180,
        user_unit=2.0,
    )
    plan = fit_pdf_page_geometry(
        geometry,
        PdfPageRenderRequest(0, 512, 512, page_box=PdfPageBox.CROP),
        _limits(),
    )
    bbox = (
        pdfium_renderer_module._pdfium_float(crop.left),
        pdfium_renderer_module._pdfium_float(crop.bottom),
        pdfium_renderer_module._pdfium_float(crop.right),
        pdfium_renderer_module._pdfium_float(crop.top),
    )
    size = (
        pdfium_renderer_module._pdfium_float(bbox[2] - bbox[0]),
        pdfium_renderer_module._pdfium_float(bbox[3] - bbox[1]),
    )
    return geometry, plan, bbox, size


def _fixture_paths() -> dict[str, Path]:
    index = json.loads((_FIXTURE_ROOT / "index.json").read_text(encoding="utf-8"))
    return {item["id"]: _FIXTURE_ROOT / item["path"] for item in index["fixtures"]}


def _render_request(record: dict[str, object]) -> PdfPageRenderRequest:
    request = record["request"]
    assert isinstance(request, dict)
    return PdfPageRenderRequest(
        page_index=request["page_index"],
        maximum_width_pixels=request["maximum_width_pixels"],
        maximum_height_pixels=request["maximum_height_pixels"],
        page_box=PdfPageBox(request["page_box"]),
        additional_rotation=PdfRenderRotation(request["additional_rotation_degrees"]),
    )


def _render_fixture(
    fixture_name: str,
    width_pixels: int,
    height_pixels: int,
) -> tuple[PdfPageRenderResult, bytes]:
    destination = BytesIO()
    result = _renderer().render(
        BytesIO((_FIXTURE_ROOT / "pdf" / fixture_name).read_bytes()),
        destination,
        PdfPageRenderRequest(0, width_pixels, height_pixels),
        _limits(),
        _Control(),
    )
    return result, destination.getvalue()


def _pixel(
    raster: bytes,
    row_stride_bytes: int,
    x: int,
    y: int,
) -> tuple[int, int, int, int]:
    offset = (y * row_stride_bytes) + (x * 4)
    return cast(tuple[int, int, int, int], tuple(raster[offset : offset + 4]))


def _assert_sanitized_error(error: PdfRenderError, *canaries: str) -> None:
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert error.__cause__ is None
    assert error.__context__ is None
    for canary in canaries:
        assert canary not in str(error)
        assert canary not in formatted


def test_pdfium_float32_crop_rounding_reconciles_to_the_exact_bounded_layout() -> None:
    geometry, plan, bbox, size = _float32_crop_geometry()
    page = _GeometryPage(
        bbox=bbox,
        size=size,
        rotation=geometry.intrinsic_rotation,
    )

    render_scale = pdfium_renderer_module._set_and_verify_geometry(page, geometry, plan)

    assert render_scale < plan.scale_pixels_per_unit
    assert (
        math.ceil(size[0] * render_scale),
        math.ceil(size[1] * render_scale),
    ) == (plan.layout.width_pixels, plan.layout.height_pixels)


def test_pdfium_geometry_rejects_a_genuine_one_pixel_oversize() -> None:
    geometry, plan, bbox, size = _float32_crop_geometry()
    oversize = (
        (plan.layout.width_pixels + 0.25) / plan.scale_pixels_per_unit,
        size[1],
    )
    assert math.ceil(oversize[0] * plan.scale_pixels_per_unit) == (plan.layout.width_pixels + 1)
    page = _GeometryPage(
        bbox=bbox,
        size=oversize,
        rotation=geometry.intrinsic_rotation,
    )

    with pytest.raises(PdfRenderError) as raised:
        pdfium_renderer_module._set_and_verify_geometry(page, geometry, plan)

    assert raised.value.code is PdfRenderErrorCode.DEPENDENCY_FAILED
    assert raised.value.context == "render.pdfium.geometry"


def test_pdfium_scale_reconciliation_rejects_more_than_one_pixel_of_drift() -> None:
    geometry, plan, _bbox, size = _float32_crop_geometry()
    displaced_size = (
        (plan.layout.width_pixels + 1.25) / plan.scale_pixels_per_unit,
        size[1],
    )
    assert math.ceil(displaced_size[0] * plan.scale_pixels_per_unit) == (
        plan.layout.width_pixels + 2
    )

    with pytest.raises(PdfRenderError) as raised:
        pdfium_renderer_module._reconciled_render_scale(displaced_size, geometry, plan)

    assert raised.value.code is PdfRenderErrorCode.DEPENDENCY_FAILED
    assert raised.value.context == "render.pdfium.geometry"


def test_pdfium_renderer_matches_complete_analytical_golden_matrix() -> None:
    golden_index = json.loads(_GOLDEN_INDEX.read_text(encoding="utf-8"))
    paths = _fixture_paths()

    for record in golden_index["references"]:
        request = _render_request(record)
        source = BytesIO(paths[record["source_fixture_id"]].read_bytes())
        source.seek(5)
        destination = BytesIO()

        result = _renderer().render(source, destination, request, _limits(), _Control())

        expected = Image.open(paths[record["reference_fixture_id"]]).convert("RGB")
        rgba = destination.getvalue()
        rendered = Image.frombytes(
            "RGBA",
            (result.width_pixels, result.height_pixels),
            rgba,
            "raw",
            "RGBA",
            result.row_stride_bytes,
            1,
        )
        rendered_rgb = rendered.convert("RGB")
        maximum_delta = max(
            abs(actual - wanted)
            for actual, wanted in zip(
                rendered_rgb.tobytes(),
                expected.tobytes(),
                strict=True,
            )
        )

        assert rendered_rgb.size == expected.size, record["id"]
        assert maximum_delta <= record["comparison"]["maximum_channel_delta"], record["id"]
        assert rgba[3::4] == b"\xff" * (result.width_pixels * result.height_pixels)
        assert result.pixel_sha256 == hashlib.sha256(rgba).hexdigest()
        assert result.page_index == request.page_index
        assert result.page_box is request.page_box
        assert result.additional_rotation is request.additional_rotation
        assert source.tell() == 5


def test_pdfium_source_adapter_completes_bounded_short_reads() -> None:
    source = _ChunkedSource(
        (_FIXTURE_ROOT / "pdf" / "render-transparency.pdf").read_bytes(),
        maximum_read=3,
    )
    source.seek(2)
    destination = BytesIO()

    result = _renderer().render(
        source,
        destination,
        PdfPageRenderRequest(0, 64, 64),
        _limits(),
        _Control(),
    )

    assert (result.width_pixels, result.height_pixels) == (64, 64)
    assert len(destination.getvalue()) == 64 * 64 * 4
    assert source.tell() == 2


def test_inherited_asymmetric_media_box_is_rendered_outside_inherited_crop_box() -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "render-geometry-matrix.pdf").read_bytes())
    destination = BytesIO()

    result = _renderer().render(
        source,
        destination,
        PdfPageRenderRequest(0, 43, 37, page_box=PdfPageBox.MEDIA),
        _limits(),
        _Control(),
    )

    assert (result.width_pixels, result.height_pixels) == (43, 37)
    raster = destination.getvalue()

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        offset = (y * result.row_stride_bytes) + (x * 4)
        return tuple(raster[offset : offset + 4])  # type: ignore[return-value]

    assert pixel(0, 0) == (255, 255, 255, 255)
    assert pixel(10, 27) == (255, 0, 0, 255)
    assert pixel(25, 27) == (0, 255, 0, 255)
    assert pixel(10, 17) == (0, 0, 255, 255)
    assert pixel(25, 17) == (255, 255, 0, 255)


def test_missing_font_fallback_is_bounded_opaque_grayscale_content() -> None:
    result, raster = _render_fixture("render-missing-font-fallback.pdf", 128, 64)

    assert (result.width_pixels, result.height_pixels) == (128, 64)
    assert raster[3::4] == b"\xff" * (128 * 64)
    nonwhite: list[tuple[int, int, int, int, int]] = []
    for y in range(result.height_pixels):
        for x in range(result.width_pixels):
            red, green, blue, _alpha = _pixel(raster, result.row_stride_bytes, x, y)
            if (red, green, blue) != (255, 255, 255):
                nonwhite.append((x, y, red, green, blue))

    assert 200 <= len(nonwhite) <= 1_000
    assert all(red == green == blue for _x, _y, red, green, blue in nonwhite)
    xs = [x for x, _y, _red, _green, _blue in nonwhite]
    ys = [y for _x, y, _red, _green, _blue in nonwhite]
    assert 8 <= min(xs) <= 12
    assert 56 <= max(xs) + 1 <= 64
    assert 24 <= min(ys) <= 29
    assert 42 <= max(ys) + 1 <= 48


def test_locked_pdfium_device_cmyk_vector_and_image_swatches_match() -> None:
    result, raster = _render_fixture("render-cmyk.pdf", 64, 64)
    expected_rgb = (
        (0, 174, 239),
        (237, 2, 140),
        (255, 241, 1),
        (35, 31, 32),
    )

    for x, expected in zip((8, 24, 40, 56), expected_rgb, strict=True):
        vector = _pixel(raster, result.row_stride_bytes, x, 16)
        image = _pixel(raster, result.row_stride_bytes, x, 56)
        assert vector == (*expected, 255)
        assert image == vector


def test_locked_pdfium_device_cmyk_and_iccbased_samples() -> None:
    result, raster = _render_fixture("render-colorspaces.pdf", 64, 64)

    assert _pixel(raster, result.row_stride_bytes, 16, 16) == (0, 174, 239, 255)
    assert _pixel(raster, result.row_stride_bytes, 48, 16) == (237, 2, 140, 255)
    assert _pixel(raster, result.row_stride_bytes, 16, 48) == (255, 0, 0, 255)
    assert _pixel(raster, result.row_stride_bytes, 48, 48) == (0, 0, 255, 255)


def test_excessive_pixel_plan_fails_before_pdfium_bitmap_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_allocation(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("PDFium bitmap allocation must not run")

    monkeypatch.setattr(pdfium.PdfBitmap, "new_foreign", forbidden_allocation)
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "render-high-dimension.pdf").read_bytes())
    source.seek(3)
    limits = PdfPageRenderLimits(
        maximum_pages=32,
        maximum_width_pixels=32_768,
        maximum_height_pixels=32_768,
        maximum_pixels=1_000_000,
        maximum_bitmap_bytes=4_000_000,
        maximum_destination_bytes=4_000_000,
        maximum_memory_bytes=4_000_000,
        wall_time_ms=10_000,
    )

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            BytesIO(),
            PdfPageRenderRequest(0, 32_768, 32_768),
            limits,
            _Control(),
        )

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.pixels"
    assert source.tell() == 3


@pytest.mark.parametrize(
    "fixture_name",
    ("malformed-truncated.pdf", "password-required.pdf", "render-malformed-page-tree.pdf"),
)
def test_pdfium_renderer_rejects_hostile_sources_with_sanitized_failure(
    fixture_name: str,
) -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / fixture_name).read_bytes())
    source.seek(1)
    destination = BytesIO()

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            destination,
            PdfPageRenderRequest(0, 64, 64),
            _limits(),
            _Control(),
        )

    assert raised.value.code in {
        PdfRenderErrorCode.SOURCE_INVALID,
        PdfRenderErrorCode.GEOMETRY_INVALID,
    }
    assert destination.getvalue() == b""
    assert source.tell() == 1


def test_pdfium_renderer_checks_cancellation_before_source_access() -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())
    source.seek(3)
    destination = BytesIO()

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            destination,
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(cancelled=True),
        )

    assert raised.value.code is PdfRenderErrorCode.CANCELLED
    assert destination.getvalue() == b""
    assert source.tell() == 3


def test_pdfium_renderer_checks_deadline_before_source_access() -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())
    source.seek(3)
    destination = BytesIO()

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            destination,
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(expired=True),
        )

    assert raised.value.code is PdfRenderErrorCode.TIMED_OUT
    assert raised.value.context == "render.deadline"
    assert destination.getvalue() == b""
    assert source.tell() == 3


def test_pdfium_renderer_rejects_page_out_of_range_without_output() -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())
    source.seek(2)
    destination = BytesIO()

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            destination,
            PdfPageRenderRequest(1, 16, 16),
            _limits(),
            _Control(),
        )

    assert raised.value.code is PdfRenderErrorCode.PAGE_OUT_OF_RANGE
    assert destination.getvalue() == b""
    assert source.tell() == 2


def test_pdfium_renderer_rejects_unapproved_runtime_before_source_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdfium,
        "PDFIUM_INFO",
        SimpleNamespace(version="152.0.7947.0", build=7_947, flags=("V8",)),
    )
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())
    source.seek(4)

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            BytesIO(),
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(),
        )

    assert raised.value.code is PdfRenderErrorCode.DEPENDENCY_UNSAFE
    assert raised.value.context == "render.pdfium.runtime"
    assert source.tell() == 4


def test_pdfium_renderer_sanitizes_destination_failure_and_restores_source() -> None:
    source = BytesIO((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())
    source.seek(6)

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            _FailingFlush(),
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(),
        )

    assert raised.value.code is PdfRenderErrorCode.DESTINATION_FAILED
    _assert_sanitized_error(raised.value, "PRIVATE-DESTINATION-CANARY")
    assert source.tell() == 6


def test_source_restore_failure_overrides_completed_native_render() -> None:
    source = _RestoreFailingSource((_FIXTURE_ROOT / "pdf" / "blank.pdf").read_bytes())

    with pytest.raises(PdfRenderError) as raised:
        _renderer().render(
            source,
            _ArmRestoreFailure(source),
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(),
        )

    assert raised.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert raised.value.context == "render.pdfium.source_restore"
    _assert_sanitized_error(raised.value, "PRIVATE-SOURCE-CANARY")


def test_native_render_failure_has_no_exception_chain_canary() -> None:
    class NativeFailurePage:
        def render(self, **_kwargs: object) -> Never:
            raise OSError("PRIVATE-NATIVE-CANARY")

    source = pdfium_renderer_module._ExactReadSource(BytesIO(b"synthetic neutral source"))
    plan = cast(
        Any,
        SimpleNamespace(
            layout=PdfRasterLayout(1, 1, 4, 1, 4),
            scale_pixels_per_unit=1.0,
        ),
    )

    with pytest.raises(PdfRenderError) as raised:
        pdfium_renderer_module._render_bitmap(
            NativeFailurePage(),
            source,
            BytesIO(),
            PdfPageRenderRequest(0, 1, 1),
            _limits(),
            _Control(),
            plan,
        )

    assert raised.value.code is PdfRenderErrorCode.DEPENDENCY_FAILED
    assert raised.value.context == "render.pdfium.render"
    _assert_sanitized_error(raised.value, "PRIVATE-NATIVE-CANARY")


def test_bitmap_close_cannot_mask_process_control_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class InterruptingPage:
        def render(self, **_kwargs: object) -> Never:
            raise KeyboardInterrupt

    class FailingBitmapMaker:
        bitmap = None

        def __init__(self, _layout: PdfRasterLayout) -> None:
            pass

        def close(self) -> None:
            events.append("bitmap")
            raise OSError("PRIVATE-BITMAP-CLOSE-CANARY")

    monkeypatch.setattr(pdfium_renderer_module, "_CheckedBitmapMaker", FailingBitmapMaker)
    plan = cast(
        Any,
        SimpleNamespace(
            layout=PdfRasterLayout(1, 1, 4, 1, 4),
            scale_pixels_per_unit=1.0,
        ),
    )

    with pytest.raises(KeyboardInterrupt):
        pdfium_renderer_module._render_bitmap(
            InterruptingPage(),
            pdfium_renderer_module._ExactReadSource(BytesIO(b"neutral source")),
            BytesIO(),
            PdfPageRenderRequest(0, 1, 1),
            _limits(),
            _Control(),
            plan,
        )

    assert events == ["bitmap"]


def test_page_cleanup_failure_wins_recoverable_error_and_document_close_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    document = _TrackedClose(
        "document",
        events,
        OSError("PRIVATE-DOCUMENT-CLOSE-CANARY"),
    )
    page = _TrackedClose("page", events, OSError("PRIVATE-PAGE-CLOSE-CANARY"))

    monkeypatch.setattr(pdfium_renderer_module, "_open_document", lambda _source: document)
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_page_count",
        lambda _document, _source: 1,
    )
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_load_page",
        lambda _document, _page_index, _source: page,
    )
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_set_and_verify_geometry",
        lambda _page, _geometry, _plan: None,
    )

    def recoverable_render_failure(*_args: object) -> Never:
        raise PdfRenderError(PdfRenderErrorCode.CANCELLED, "render.cancelled")

    monkeypatch.setattr(
        pdfium_renderer_module,
        "_render_bitmap",
        recoverable_render_failure,
    )

    with pytest.raises(PdfRenderError) as raised:
        pdfium_renderer_module._render_document(
            pdfium_renderer_module._ExactReadSource(BytesIO(b"neutral source")),
            BytesIO(),
            PdfPageRenderRequest(0, 1, 1),
            _limits(),
            _Control(),
            cast(Any, object()),
            cast(Any, object()),
        )

    assert events == ["page", "document"]
    assert raised.value.code is PdfRenderErrorCode.DEPENDENCY_FAILED
    assert raised.value.context == "render.pdfium.page_close"
    _assert_sanitized_error(
        raised.value,
        "PRIVATE-PAGE-CLOSE-CANARY",
        "PRIVATE-DOCUMENT-CLOSE-CANARY",
    )


def test_page_and_document_close_cannot_mask_process_control_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    document = _TrackedClose("document", events, OSError("document close fault"))
    page = _TrackedClose("page", events, OSError("page close fault"))

    monkeypatch.setattr(pdfium_renderer_module, "_open_document", lambda _source: document)
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_page_count",
        lambda _document, _source: 1,
    )
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_load_page",
        lambda _document, _page_index, _source: page,
    )
    monkeypatch.setattr(
        pdfium_renderer_module,
        "_set_and_verify_geometry",
        lambda _page, _geometry, _plan: None,
    )

    def interrupt_render(*_args: object) -> Never:
        raise SystemExit(23)

    monkeypatch.setattr(pdfium_renderer_module, "_render_bitmap", interrupt_render)

    with pytest.raises(SystemExit) as raised:
        pdfium_renderer_module._render_document(
            pdfium_renderer_module._ExactReadSource(BytesIO(b"neutral source")),
            BytesIO(),
            PdfPageRenderRequest(0, 1, 1),
            _limits(),
            _Control(),
            cast(Any, object()),
            cast(Any, object()),
        )

    assert raised.value.code == 23
    assert events == ["page", "document"]


def test_post_construction_callback_fault_closes_document_and_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    document = _TrackedClose("document", events)
    page = _TrackedClose("page", events)

    class FaultedSource:
        def raise_if_faulted(self) -> Never:
            raise PdfRenderError(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.source")

    class PageOwner:
        def __getitem__(self, _page_index: int) -> _TrackedClose:
            return page

    def create_document(
        _source: object,
        *,
        password: str,
        autoclose: bool,
    ) -> _TrackedClose:
        assert password == ""
        assert not autoclose
        return document

    monkeypatch.setattr(pdfium, "PdfDocument", create_document)
    faulted_source = cast(Any, FaultedSource())

    with pytest.raises(PdfRenderError) as document_error:
        pdfium_renderer_module._open_document(faulted_source)
    with pytest.raises(PdfRenderError) as page_error:
        pdfium_renderer_module._load_page(PageOwner(), 0, faulted_source)

    assert document_error.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert page_error.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert events == ["document", "page"]
