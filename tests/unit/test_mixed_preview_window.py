from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.page_previews import PagePreviewProgress, PagePreviewProgressPhase
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageMetadata,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
)
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker, UiFailure
from paperworkshell.ui.models import PreviewState
from paperworkshell.ui.window import (
    _AUTOSAVE_DEBOUNCE_MS,
    _PREVIEW_REFRESH_DEBOUNCE_MS,
    PaperWorkShellWindow,
    _LongOperationKind,
)

CASE_ID = UUID("83000000-0000-4000-8000-000000000001")
PDF_PAGE_ID = UUID("83000000-0000-4000-8000-000000000002")
IMAGE_PAGE_ID = UUID("83000000-0000-4000-8000-000000000003")
PDF_SOURCE_ID = UUID("83000000-0000-4000-8000-000000000004")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("this focused presentation test does not initialize its backend")


@contextmanager
def _window() -> Iterator[PaperWorkShellWindow]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend_factory)
    commands.connect_worker(worker)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    app.processEvents()
    try:
        yield window
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _state() -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        0,
        (
            OrganizerPageView(PDF_PAGE_ID, 1, 0, True),
            OrganizerPageView(IMAGE_PAGE_ID, 2, 0, True),
        ),
        False,
        False,
        False,
    )


def _pdf_result() -> PdfThumbnailResult:
    request = PdfThumbnailRequest(CASE_ID, PDF_PAGE_ID, 240, 320)
    key = PdfThumbnailCacheKey(
        case_id=CASE_ID,
        source_id=PDF_SOURCE_ID,
        page_id=PDF_PAGE_ID,
        source_page_index=0,
        source_byte_size=128,
        source_sha256="a" * 64,
        maximum_width_pixels=240,
        maximum_height_pixels=320,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256="b" * 64,
    )
    snapshot = PdfThumbnailSnapshot(
        CASE_ID,
        PDF_PAGE_ID,
        1,
        1,
        4,
        bytes((1, 2, 3, 255)),
        PdfRenderRotation.DEGREES_0,
        PdfRenderRotation.DEGREES_0,
    )
    return PdfThumbnailResult(request, key, snapshot, PdfThumbnailCacheStatus.GENERATED)


def _image_result() -> ImagePagePreviewResult:
    request = ImagePagePreviewRequest(CASE_ID, IMAGE_PAGE_ID, 240, 320)
    metadata = ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )
    snapshot = ImagePagePreviewSnapshot(
        CASE_ID,
        IMAGE_PAGE_ID,
        2,
        1,
        8,
        bytes((4, 5, 6, 255, 7, 8, 9, 255)),
        metadata,
        ImageQuarterTurn.DEGREES_0,
    )
    return ImagePagePreviewResult(request, snapshot)


def _start_mixed_preview(window: PaperWorkShellWindow) -> tuple[int, int]:
    window._state_changed(_state())
    window._preview_refresh_timer.stop()
    operation = window._begin_long_operation(_LongOperationKind.PREVIEW)
    window._thumbnail_generation += 1
    window._active_preview_page_ids = (PDF_PAGE_ID, IMAGE_PAGE_ID)
    window._model.set_preview_loading((PDF_PAGE_ID, IMAGE_PAGE_ID))
    return operation.operation_id, window._thumbnail_generation


def test_window_loads_ordered_pdf_and_image_rgba_into_the_same_bounded_pixmap_lru() -> None:
    with _window() as window:
        operation_id, generation = _start_mixed_preview(window)

        window._thumbnails_ready(operation_id, generation, (_pdf_result(), _image_result()))

        assert window._preview_batch_complete
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.READY
        assert window._model.preview_state(IMAGE_PAGE_ID) is PreviewState.READY
        assert window._model.retained_preview_count == 2
        assert window._model.retained_preview_bytes <= window._model.maximum_preview_cache_bytes
        pdf_pixmap = window._model.data(
            window._model.index(0, 0), int(Qt.ItemDataRole.DecorationRole)
        )
        image_pixmap = window._model.data(
            window._model.index(1, 0), int(Qt.ItemDataRole.DecorationRole)
        )
        assert isinstance(pdf_pixmap, QPixmap)
        assert isinstance(image_pixmap, QPixmap)
        assert pdf_pixmap.toImage().pixelColor(0, 0).getRgb() == (1, 2, 3, 255)
        assert image_pixmap.toImage().pixelColor(0, 0).getRgb() == (4, 5, 6, 255)
        assert image_pixmap.toImage().pixelColor(1, 0).getRgb() == (7, 8, 9, 255)


def test_window_rejects_a_mixed_result_sequence_not_matching_requested_page_order() -> None:
    with _window() as window:
        operation_id, generation = _start_mixed_preview(window)

        window._thumbnails_ready(operation_id, generation, (_image_result(), _pdf_result()))

        assert not window._preview_batch_complete
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.FAILED
        assert window._model.preview_state(IMAGE_PAGE_ID) is PreviewState.FAILED
        assert window._error_label.text() == (
            "PWS_UI_PREVIEW_RESULT_INVALID: PaperWorkShell could not display the preview safely. "
            "Retry the selected preview."
        )


def test_preview_completion_waits_for_accepted_pixels() -> None:
    with _window() as window:
        operation_id, generation = _start_mixed_preview(window)
        window._worker._thumbnail_progress(
            operation_id, PagePreviewProgress(CASE_ID, PagePreviewProgressPhase.VALIDATING, 1, 2)
        )
        window._worker._thumbnail_progress(
            operation_id, PagePreviewProgress(CASE_ID, PagePreviewProgressPhase.COMPLETE, 2, 2)
        )
        assert window._progress.text() == "Preview: validating — 1 of 2"
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.LOADING

        completion = "Preview update complete for 2 pages."
        window._worker.operation_status_changed.emit(operation_id, completion)
        assert window._status_label.text() != completion

        window._thumbnails_ready(operation_id, generation, (_pdf_result(), _image_result()))
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.READY
        window._worker.operation_status_changed.emit(operation_id, completion)
        assert window._status_label.text() == completion


def test_selected_rotation_queues_save_then_preview_without_debounce_delays() -> None:
    with _window() as window:
        commands = window._commands
        commands.flush_requested.disconnect()
        commands.retry_save_requested.disconnect()
        commands.thumbnails_requested.disconnect()
        saves: list[str] = []
        previews: list[tuple[UUID, ...]] = []
        commands.flush_requested.connect(lambda: saves.append("flush"))
        commands.retry_save_requested.connect(lambda: saves.append("retry"))
        commands.thumbnails_requested.connect(
            lambda _operation, _generation, pages, *_args: previews.append(pages)
        )
        state = _state()
        page, other_page = state.pages
        window._state_changed(state)

        rotated = replace(state, pages=(replace(page, rotation_degrees=90), other_page))
        window._state_changed(replace(rotated, autosave_pending=True))
        rotated_again = replace(rotated, pages=(replace(page, rotation_degrees=180), other_page))
        window._state_changed(replace(rotated_again, autosave_pending=True))

        assert window._autosave_timer.isActive()
        assert window._autosave_timer.interval() == 0
        assert not saves
        window._preview_refresh_timer.stop()
        window._preview_refresh_timer.timeout.emit()
        assert not previews

        window._autosave_timer.stop()
        window._autosave_timer.timeout.emit()
        assert saves == ["flush"]
        assert not previews
        window._failed(UiFailure("PWS_TEST_SAVE", "Synthetic save failed.", retryable=True))
        assert window._retry_save_button.isVisible()
        window._preview_refresh_timer.timeout.emit()
        assert not previews
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.NOT_LOADED
        assert window._model.preview(PDF_PAGE_ID) is None

        window._retry_save_button.click()
        assert saves == ["flush", "retry"]
        assert not previews
        window._state_changed(replace(rotated_again, revision=1))
        assert not window._retry_save_button.isVisible()

        assert window._preview_refresh_timer.isActive()
        assert window._preview_refresh_timer.interval() == 0
        window._preview_refresh_timer.stop()
        window._preview_refresh_timer.timeout.emit()
        assert len(previews) == 1 and PDF_PAGE_ID in previews[0]
        assert window._model.pages[0].rotation_degrees == 180

        window._schedule_preview_refresh()
        assert window._preview_refresh_timer.interval() == _PREVIEW_REFRESH_DEBOUNCE_MS


def test_reused_rotation_preview_keeps_autosave_coalescing() -> None:
    with _window() as window:
        operation_id, generation = _start_mixed_preview(window)
        window._thumbnails_ready(operation_id, generation, (_pdf_result(), _image_result()))
        window._finish_active_operation()
        state = _state()
        page, other_page = state.pages
        window._state_changed(
            replace(
                state,
                pages=(replace(page, rotation_degrees=90), other_page),
                autosave_pending=True,
            )
        )

        window._state_changed(replace(state, autosave_pending=True))

        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.READY
        assert window._autosave_timer.isActive()
        assert window._autosave_timer.interval() == _AUTOSAVE_DEBOUNCE_MS
