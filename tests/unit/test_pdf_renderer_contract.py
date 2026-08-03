from __future__ import annotations

import hashlib
from collections.abc import Buffer
from io import BytesIO
from typing import Never, cast

import pytest

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
from paperworkshell.pdf.renderer import (
    PdfPageRenderer,
    PdfRasterDestination,
    PdfRenderControl,
    PdfRenderSource,
    require_pdf_render_active,
    write_exact_pdf_raster,
)


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


class _Renderer:
    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        assert source.read(4) == b"%PDF"
        assert limits.maximum_destination_bytes >= 4
        assert not control.cancellation_requested()
        destination.write(b"\xff\xff\xff\xff")
        destination.flush()
        return PdfPageRenderResult(
            page_index=request.page_index,
            width_pixels=1,
            height_pixels=1,
            row_stride_bytes=4,
            bytes_written=4,
            pixel_sha256="ad95131bc0b799c0b1af477fb14fcf26a6a9f76079e48bf090acb7e8367bfd0e",
            page_box=request.page_box,
            intrinsic_rotation=PdfRenderRotation.DEGREES_0,
            additional_rotation=request.additional_rotation,
        )


def test_renderer_protocol_accepts_only_neutral_streams_and_models() -> None:
    renderer: PdfPageRenderer = _Renderer()
    destination = BytesIO()

    result = renderer.render(
        BytesIO(b"%PDF-1.7\n"),
        destination,
        PdfPageRenderRequest(0, 1, 1, PdfPageBox.CROP),
        PdfPageRenderLimits(1, 1, 1, 1, 4, 4, 4, 1_000),
        _Control(),
    )

    assert result.width_pixels == 1
    assert destination.getvalue() == b"\xff\xff\xff\xff"


class _ScriptedControl:
    def __init__(
        self,
        *,
        cancellations: tuple[object, ...] = (False,),
        deadlines: tuple[object, ...] = (False,),
    ) -> None:
        self._cancellations = iter(cancellations)
        self._deadlines = iter(deadlines)

    def cancellation_requested(self) -> bool:
        value = next(self._cancellations, False)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def deadline_expired(self) -> bool:
        value = next(self._deadlines, False)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


class _ShortWriter(BytesIO):
    def __init__(self, maximum_write: int) -> None:
        super().__init__()
        self.maximum_write = maximum_write

    def write(self, data: object) -> int:
        view = memoryview(data)  # type: ignore[arg-type]
        return super().write(view[: self.maximum_write])


class _InvalidWriter:
    def __init__(self, result: object) -> None:
        self.result = result

    def write(self, _data: Buffer) -> int | None:
        return cast(int | None, self.result)

    def flush(self) -> None:
        return


class _FailingFlush(BytesIO):
    def flush(self) -> Never:
        raise OSError("private destination detail")


def _layout() -> PdfRasterLayout:
    return PdfRasterLayout(2, 2, 8, 4, 16)


def _write_limits(**changes: int) -> PdfPageRenderLimits:
    values = {
        "maximum_pages": 1,
        "maximum_width_pixels": 2,
        "maximum_height_pixels": 2,
        "maximum_pixels": 4,
        "maximum_bitmap_bytes": 16,
        "maximum_destination_bytes": 16,
        "maximum_memory_bytes": 16,
        "wall_time_ms": 1_000,
    }
    values.update(changes)
    return PdfPageRenderLimits(**values)


def test_exact_raster_writer_handles_short_writes_by_row_and_returns_digest() -> None:
    raster = bytes((0, 1, 2, 255, 4, 5, 6, 255, 8, 9, 10, 255, 12, 13, 14, 255))
    destination = _ShortWriter(3)

    digest = write_exact_pdf_raster(
        destination,
        raster,
        _layout(),
        _write_limits(),
        _ScriptedControl(),
    )

    assert destination.getvalue() == raster
    assert digest == hashlib.sha256(raster).hexdigest()


@pytest.mark.parametrize("result", (None, 0, -1, 9, True, "4"))
def test_exact_raster_writer_rejects_invalid_write_results(result: object) -> None:
    with pytest.raises(PdfRenderError) as raised:
        write_exact_pdf_raster(
            _InvalidWriter(result),
            b"\x00\x00\x00\xff" * 4,
            _layout(),
            _write_limits(),
            _ScriptedControl(),
        )

    assert raised.value.code is PdfRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "render.destination_write"


def test_exact_raster_writer_rejects_wrong_or_noncontiguous_buffer_before_write() -> None:
    destination = BytesIO()

    for raster in (bytes(15), memoryview(bytes(32))[::2]):
        with pytest.raises(PdfRenderError) as raised:
            write_exact_pdf_raster(
                destination,
                raster,
                _layout(),
                _write_limits(),
                _ScriptedControl(),
            )

        assert raised.value.code is PdfRenderErrorCode.RESULT_INVALID
        assert raised.value.context == "render.raster_buffer"

    assert destination.getvalue() == b""


def test_exact_raster_writer_rejects_nonopaque_alpha_before_writing() -> None:
    destination = BytesIO()
    raster = b"\x00\x00\x00\xff" * 3 + b"\x00\x00\x00\xfe"

    with pytest.raises(PdfRenderError) as raised:
        write_exact_pdf_raster(
            destination,
            raster,
            _layout(),
            _write_limits(),
            _ScriptedControl(),
        )

    assert raised.value.code is PdfRenderErrorCode.RESULT_INVALID
    assert raised.value.context == "render.raster_alpha"
    assert destination.getvalue() == b""


def test_exact_raster_writer_revalidates_operation_limits_before_writing() -> None:
    destination = BytesIO()

    with pytest.raises(PdfRenderError) as raised:
        write_exact_pdf_raster(
            destination,
            b"\x00\x00\x00\xff" * 4,
            _layout(),
            _write_limits(maximum_destination_bytes=15),
            _ScriptedControl(),
        )

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.destination"
    assert destination.getvalue() == b""


def test_exact_raster_writer_sanitizes_flush_failure() -> None:
    with pytest.raises(PdfRenderError) as raised:
        write_exact_pdf_raster(
            _FailingFlush(),
            b"\x00\x00\x00\xff" * 4,
            _layout(),
            _write_limits(),
            _ScriptedControl(),
        )

    assert raised.value.code is PdfRenderErrorCode.DESTINATION_FAILED
    assert "private destination detail" not in str(raised.value)


@pytest.mark.parametrize(
    ("control", "code", "context"),
    (
        (_ScriptedControl(cancellations=(True,)), PdfRenderErrorCode.CANCELLED, "render.cancelled"),
        (_ScriptedControl(deadlines=(True,)), PdfRenderErrorCode.TIMED_OUT, "render.deadline"),
        (
            _ScriptedControl(cancellations=(1,)),
            PdfRenderErrorCode.INTERNAL_ERROR,
            "render.cancel_probe",
        ),
        (
            _ScriptedControl(deadlines=(ValueError("private"),)),
            PdfRenderErrorCode.INTERNAL_ERROR,
            "render.deadline_probe",
        ),
    ),
)
def test_active_probe_has_closed_typed_failure(
    control: _ScriptedControl,
    code: PdfRenderErrorCode,
    context: str,
) -> None:
    with pytest.raises(PdfRenderError) as raised:
        require_pdf_render_active(control)

    assert raised.value.code is code
    assert raised.value.context == context


class _MissingControl:
    pass


class _RaisingControlProperty:
    @property
    def cancellation_requested(self) -> object:
        raise RuntimeError("private control detail")

    def deadline_expired(self) -> bool:
        return False


@pytest.mark.parametrize("control", (_MissingControl(), _RaisingControlProperty()))
def test_control_attribute_failure_is_sanitized(control: object) -> None:
    with pytest.raises(PdfRenderError) as raised:
        require_pdf_render_active(cast(PdfRenderControl, control))

    assert raised.value.code is PdfRenderErrorCode.INTERNAL_ERROR
    assert raised.value.context == "render.control"
    assert "private control detail" not in str(raised.value)


def test_process_control_exception_from_probe_is_not_swallowed() -> None:
    with pytest.raises(SystemExit):
        require_pdf_render_active(_ScriptedControl(cancellations=(SystemExit(7),)))


def test_cancellation_after_partial_output_fails_and_leaves_caller_to_discard_sink() -> None:
    destination = BytesIO()
    control = _ScriptedControl(cancellations=(False, True))
    raster = b"\x00\x00\x00\xff" * 4

    with pytest.raises(PdfRenderError) as raised:
        write_exact_pdf_raster(destination, raster, _layout(), _write_limits(), control)

    assert raised.value.code is PdfRenderErrorCode.CANCELLED
    assert destination.getvalue() == raster[:8]
