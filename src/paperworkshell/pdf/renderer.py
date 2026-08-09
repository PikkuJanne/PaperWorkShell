"""Renderer-neutral worker adapter interface for PDF page rasterization."""

from __future__ import annotations

import hashlib
from collections.abc import Buffer
from typing import Protocol

from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterLayout,
    PdfRenderError,
    PdfRenderErrorCode,
    checked_pdf_raster_layout,
    reject_pdf_rendering,
)


class PdfRenderSource(Protocol):
    """Already-authenticated, seekable binary PDF authority."""

    def read(self, size: int = -1, /) -> bytes: ...

    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def tell(self) -> int: ...


class PdfRasterDestination(Protocol):
    """Caller-owned bounded raw-raster destination; paths are never accepted."""

    def write(self, data: Buffer, /) -> int | None: ...

    def flush(self) -> None: ...


class PdfRenderControl(Protocol):
    """Cooperative probes backed by the owning worker deadline and cancellation."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


class PdfPageRenderer(Protocol):
    """Replaceable worker-only page renderer with no third-party boundary types."""

    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        """Render one page and return metadata only after exact output is flushed."""


def require_pdf_render_active(control: PdfRenderControl) -> None:
    """Fail closed when a typed cancellation or deadline probe is active or invalid."""

    try:
        cancellation_probe = control.cancellation_requested
        deadline_probe = control.deadline_expired
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.control")
    if not _probe(cancellation_probe, "render.cancel_probe"):
        if _probe(deadline_probe, "render.deadline_probe"):
            reject_pdf_rendering(PdfRenderErrorCode.TIMED_OUT, "render.deadline")
        return
    reject_pdf_rendering(PdfRenderErrorCode.CANCELLED, "render.cancelled")


def write_exact_pdf_raster(
    destination: PdfRasterDestination,
    raster: Buffer,
    layout: PdfRasterLayout,
    limits: PdfPageRenderLimits,
    control: PdfRenderControl,
) -> str:
    """Write, hash, and flush one exact packed raster without taking sink ownership."""

    if type(layout) is not PdfRasterLayout or type(limits) is not PdfPageRenderLimits:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.write_layout")
    checked_layout = checked_pdf_raster_layout(
        layout.width_pixels,
        layout.height_pixels,
        limits,
    )
    if checked_layout != layout:
        reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.raster_layout")
    try:
        view = memoryview(raster)
        if not view.contiguous or view.nbytes != layout.byte_count:
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.raster_buffer")
        bytes_view = view.cast("B")
        for row_start in range(0, layout.byte_count, layout.row_stride_bytes):
            require_pdf_render_active(control)
            row_end = row_start + layout.row_stride_bytes
            alpha = bytes_view[row_start + 3 : row_end : 4].tobytes()
            if alpha.count(255) != layout.width_pixels:
                reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.raster_alpha")
    except PdfRenderError:
        raise
    except BufferError, TypeError, ValueError:
        reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.raster_buffer")

    digest = hashlib.sha256()
    offset = 0
    try:
        while offset < layout.byte_count:
            require_pdf_render_active(control)
            end = min(offset + layout.row_stride_bytes, layout.byte_count)
            count = destination.write(bytes_view[offset:end])
            if type(count) is not int or not 1 <= count <= end - offset:
                reject_pdf_rendering(
                    PdfRenderErrorCode.DESTINATION_FAILED,
                    "render.destination_write",
                )
            digest.update(bytes_view[offset : offset + count])
            offset += count
        require_pdf_render_active(control)
        destination.flush()
        require_pdf_render_active(control)
    except PdfRenderError:
        raise
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.DESTINATION_FAILED, "render.destination_write")
    return digest.hexdigest()


def _probe(probe: object, context: str) -> bool:
    if not callable(probe):
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, context)
    try:
        value = probe()
    except PdfRenderError:
        raise
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, context)
    if type(value) is not bool:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, context)
    return value
