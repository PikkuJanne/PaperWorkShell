from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerPageView,
    OrganizerViewState,
)
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
)
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
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)
from paperworkshell.ui.scan_review import ScanReviewDialog
from paperworkshell.ui.window import (
    PaperWorkShellWindow,
    _LongOperationKind,
    _ScanReviewPreviewOperation,
)

CASE_ID = UUID("8a000000-0000-4000-8000-000000000001")
PDF_PAGE_ID = UUID("8a000000-0000-4000-8000-000000000002")
JPEG_PAGE_ID = UUID("8a000000-0000-4000-8000-000000000003")
PNG_PAGE_ID = UUID("8a000000-0000-4000-8000-000000000004")
SOURCE_ID = UUID("8a000000-0000-4000-8000-000000000005")
REVISION = 7
PREVIEW_WIDTH = 600
PREVIEW_HEIGHT = 800

type _PreviewCall = tuple[int, int, object, object, int, int, object]


def _private_value(instance: object, name: str) -> object:
    return vars(instance)[name]


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("this focused presentation test does not initialize its backend")


@contextmanager
def _window(
    *,
    resource_policy: ResourcePolicy | None = None,
) -> Iterator[tuple[PaperWorkShellWindow, OrganizerBackendCommands]]:
    application = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend_factory)
    window = PaperWorkShellWindow(commands, worker, resource_policy=resource_policy)
    window.show()
    window._backend_initialized()
    application.processEvents()
    try:
        yield window, commands
    finally:
        if window._scan_review_dialog is not None:
            window._dismiss_scan_review_after_state_change()
        window._allow_close = True
        window.close()
        window.deleteLater()
        worker.deleteLater()
        application.processEvents()


def _state(*, image_only: bool = True) -> OrganizerViewState:
    pages = (
        (OrganizerPageView(PNG_PAGE_ID, 1, 0, True, ImageTransformIntent()),)
        if image_only
        else (
            OrganizerPageView(PDF_PAGE_ID, 1, 0, True),
            OrganizerPageView(JPEG_PAGE_ID, 2, 0, True, ImageTransformIntent()),
            OrganizerPageView(PNG_PAGE_ID, 3, 0, True, ImageTransformIntent()),
        )
    )
    return OrganizerViewState(CASE_ID, REVISION, pages, False, False, False)


def _show_state(window: PaperWorkShellWindow, *, image_only: bool = True) -> None:
    window._state_changed(_state(image_only=image_only))
    window._preview_refresh_timer.stop()
    _application().processEvents()


def _draft(
    *,
    candidate: ImageTransformIntent | None = None,
) -> ScanReviewDraft:
    selected = ImageTransformIntent() if candidate is None else candidate
    return ScanReviewDraft(
        CASE_ID,
        PNG_PAGE_ID,
        REVISION,
        ImageTransformIntent(),
        selected,
    )


def _metadata(width: int, height: int) -> ImagePageMetadata:
    return ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=width,
        encoded_height_pixels=height,
        oriented_width_pixels=width,
        oriented_height_pixels=height,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _opaque_pixels(width: int, height: int, red: int) -> bytes:
    return bytes((red, 20, 30, 255)) * (width * height)


def _original_result(
    draft: ScanReviewDraft,
    *,
    width: int = 2,
    height: int = 1,
) -> ScanReviewPreviewResult:
    pixels = _opaque_pixels(width, height, 10)
    request = ImagePagePreviewRequest(
        draft.case_id,
        draft.page_id,
        PREVIEW_WIDTH,
        PREVIEW_HEIGHT,
    )
    snapshot = ImagePagePreviewSnapshot(
        draft.case_id,
        draft.page_id,
        width,
        height,
        width * 4,
        pixels,
        _metadata(width, height),
        ImageQuarterTurn.DEGREES_0,
    )
    return ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ORIGINAL,
        ImagePagePreviewResult(request, snapshot),
    )


def _adjusted_result(
    draft: ScanReviewDraft,
    *,
    width: int = 2,
    height: int = 1,
) -> ScanReviewPreviewResult:
    pixels = _opaque_pixels(width, height, 40)
    request = ImagePageTransformRequest(
        draft.case_id,
        draft.page_id,
        draft.candidate_intent,
        PREVIEW_WIDTH,
        PREVIEW_HEIGHT,
    )
    snapshot = ImagePageTransformSnapshot(
        draft.case_id,
        SOURCE_ID,
        draft.page_id,
        draft.candidate_intent,
        width,
        height,
        width * 4,
        len(pixels),
        hashlib.sha256(pixels).hexdigest(),
        "b" * 64,
    )
    return ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        ImagePageTransformResult(request, snapshot, pixels),
    )


def _record_preview_calls(
    commands: OrganizerBackendCommands,
    calls: list[_PreviewCall],
) -> None:
    def record(
        operation_id: int,
        generation: int,
        draft: object,
        variant: object,
        maximum_width: int,
        maximum_height: int,
        cancellation: object,
    ) -> None:
        calls.append(
            (
                operation_id,
                generation,
                draft,
                variant,
                maximum_width,
                maximum_height,
                cancellation,
            )
        )

    commands.scan_review_preview_requested.connect(record)


def _open_review(
    window: PaperWorkShellWindow,
    draft: ScanReviewDraft,
) -> ScanReviewDialog:
    _show_state(window)
    window._request_scan_review()
    operation_id = window._scan_review_start_operation_id
    assert operation_id is not None
    window._scan_review_started(operation_id, draft)
    _application().processEvents()
    dialog = window._scan_review_dialog
    assert dialog is not None
    return dialog


def _finish_current_preview(
    window: PaperWorkShellWindow,
    result: ScanReviewPreviewResult,
) -> None:
    active = window._active_scan_review_preview
    assert active is not None
    assert active.draft == result.draft
    assert active.variant is result.variant
    window._scan_review_preview_ready(active.operation_id, active.generation, result)
    window._scan_review_preview_finished(active.operation_id, active.generation)


def _complete_review(
    window: PaperWorkShellWindow,
    draft: ScanReviewDraft,
) -> ScanReviewDialog:
    dialog = _open_review(window, draft)
    _finish_current_preview(window, _original_result(draft))
    _finish_current_preview(window, _adjusted_result(draft))
    assert dialog.preview_ready_for_apply
    assert window._active_scan_review_preview is None
    return dialog


def test_scan_review_action_is_available_for_projected_jpeg_and_png_but_not_pdf() -> None:
    with _window() as (window, _commands):
        _show_state(window, image_only=False)

        for row, expected in ((0, False), (1, True), (2, True)):
            window._page_view.setCurrentIndex(window._model.index(row, 0))
            window._update_page_actions()
            assert window._scan_review_button.isEnabled() is expected


def test_scan_review_waits_for_the_current_thumbnail_operation_to_drain() -> None:
    with _window() as (window, commands):
        starts: list[tuple[int, object, object]] = []

        def record_start(operation_id: int, page_id: object, suggestion: object) -> None:
            starts.append((operation_id, page_id, suggestion))

        commands.scan_review_begin_requested.connect(record_start)
        _show_state(window)
        window._start_preview((PNG_PAGE_ID,))
        thumbnail = window._active_operation
        assert thumbnail is not None
        assert thumbnail.kind is _LongOperationKind.PREVIEW

        window._request_scan_review()

        assert thumbnail.cancellation.cancelled()
        pending_before_drain = window._pending_scan_review_page_id
        assert pending_before_drain == PNG_PAGE_ID
        assert _private_value(window, "_scan_review_start_operation_id") is None
        assert not window._import_button.isEnabled()

        window._operation_finished(thumbnail.operation_id)

        assert len(starts) == 1
        assert starts[0][1:] == (PNG_PAGE_ID, None)
        assert window._pending_scan_review_page_id is None
        assert window._scan_review_start_page_id == PNG_PAGE_ID
        assert not window._import_button.isEnabled()


def test_scan_review_pending_behind_thumbnail_drain_rejects_selection_drift() -> None:
    with _window() as (window, commands):
        starts: list[tuple[int, object, object]] = []
        commands.scan_review_begin_requested.connect(
            lambda operation_id, page_id, suggestion: starts.append(
                (operation_id, page_id, suggestion)
            )
        )
        _show_state(window, image_only=False)
        window._page_view.setCurrentIndex(window._model.index(2, 0))
        window._start_preview((PNG_PAGE_ID,))
        thumbnail = window._active_operation
        assert thumbnail is not None
        window._request_scan_review()
        assert _private_value(window, "_pending_scan_review_page_id") == PNG_PAGE_ID

        window._page_view.setCurrentIndex(window._model.index(1, 0))
        window._operation_finished(thumbnail.operation_id)

        assert window._selected_page_id() == JPEG_PAGE_ID
        assert _private_value(window, "_pending_scan_review_page_id") is None
        assert _private_value(window, "_scan_review_start_operation_id") is None
        assert starts == []
        assert window._error_label.isVisible()
        assert "selected page changed" in window._error_label.text().casefold()


def test_scan_review_begin_result_rejects_selection_drift() -> None:
    with _window() as (window, commands):
        cancellations: list[object] = []
        preview_calls: list[_PreviewCall] = []
        commands.scan_review_cancel_requested.connect(cancellations.append)
        _record_preview_calls(commands, preview_calls)
        _show_state(window, image_only=False)
        window._page_view.setCurrentIndex(window._model.index(2, 0))
        window._request_scan_review()
        operation_id = window._scan_review_start_operation_id
        assert operation_id is not None
        draft = _draft()

        window._page_view.setCurrentIndex(window._model.index(1, 0))
        window._scan_review_started(operation_id, draft)

        assert window._selected_page_id() == JPEG_PAGE_ID
        assert window._scan_review_dialog is None
        assert preview_calls == []
        assert cancellations == [draft]
        assert window._error_label.isVisible()
        assert "could not open scan review safely" in window._error_label.text()


def test_scan_review_disables_import_from_opening_until_failure_or_cancel() -> None:
    with _window() as (window, commands):
        imports: list[object] = []
        commands.import_requested.connect(lambda *values: imports.append(values))
        _show_state(window)
        assert window._import_button.isEnabled()

        window._request_scan_review()
        opening_operation_id = window._scan_review_start_operation_id
        assert opening_operation_id is not None
        assert not window._import_button.isEnabled()

        window._queue_import(Path("never-opened.pdf"), "never-opened.pdf")
        assert imports == []
        assert window._active_operation is None

        window._scan_review_started(opening_operation_id, object())
        assert window._import_button.isEnabled()

        window._request_scan_review()
        failed_operation_id = window._scan_review_start_operation_id
        assert failed_operation_id is not None
        window._scan_review_start_failed(failed_operation_id, object())
        assert window._import_button.isEnabled()

        draft = _draft()
        window._request_scan_review()
        accepted_operation_id = window._scan_review_start_operation_id
        assert accepted_operation_id is not None
        window._scan_review_started(accepted_operation_id, draft)
        dialog = window._scan_review_dialog
        assert dialog is not None
        assert not window._import_button.isEnabled()

        _finish_current_preview(window, _original_result(draft))
        _finish_current_preview(window, _adjusted_result(draft))
        dialog.reject()
        _application().processEvents()
        assert window._scan_review_dialog is None
        assert window._import_button.isEnabled()


def test_failed_thumbnail_retry_is_disabled_and_guarded_during_scan_review() -> None:
    with _window() as (window, commands):
        thumbnail_requests: list[object] = []
        commands.thumbnails_requested.connect(lambda *values: thumbnail_requests.append(values))
        _show_state(window)
        window._model.set_preview_failed(PNG_PAGE_ID)
        window._update_preview_retry_action()
        assert window._retry_preview_button.isVisible()
        assert window._retry_preview_button.isEnabled()

        window._request_scan_review()
        opening_operation_id = window._scan_review_start_operation_id
        assert opening_operation_id is not None
        assert not window._retry_preview_button.isEnabled()
        window._retry_failed_preview()
        assert thumbnail_requests == []
        assert window._active_operation is None

        window._scan_review_started(opening_operation_id, object())
        assert window._retry_preview_button.isEnabled()

        draft = _draft()
        window._request_scan_review()
        accepted_operation_id = window._scan_review_start_operation_id
        assert accepted_operation_id is not None
        window._scan_review_started(accepted_operation_id, draft)
        dialog = window._scan_review_dialog
        assert dialog is not None
        assert not window._retry_preview_button.isEnabled()
        window._retry_failed_preview()
        assert thumbnail_requests == []

        active = window._active_scan_review_preview
        assert active is not None
        dialog.reject()
        window._scan_review_preview_cancelled(
            active.operation_id,
            active.generation,
            "Preview cancelled.",
        )
        window._scan_review_preview_finished(active.operation_id, active.generation)
        dialog.reject()
        _application().processEvents()
        window._preview_refresh_timer.stop()
        assert window._retry_preview_button.isEnabled()


def test_scan_review_rejects_a_begin_draft_with_wrong_authoritative_base_intent() -> None:
    with _window() as (window, commands):
        cancellations: list[object] = []
        preview_calls: list[_PreviewCall] = []
        commands.scan_review_cancel_requested.connect(cancellations.append)
        _record_preview_calls(commands, preview_calls)
        _show_state(window)
        window._request_scan_review()
        operation_id = window._scan_review_start_operation_id
        assert operation_id is not None
        forged_intent = ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_90)
        forged = ScanReviewDraft(
            CASE_ID,
            PNG_PAGE_ID,
            REVISION,
            forged_intent,
            forged_intent,
        )

        window._scan_review_started(operation_id, forged)

        assert window._scan_review_dialog is None
        assert preview_calls == []
        assert cancellations == [forged]
        assert window._error_label.isVisible()
        assert "could not open scan review safely" in window._error_label.text()


def test_scan_review_rejects_inconsistent_rotation_and_base_intent() -> None:
    with _window() as (window, commands):
        cancellations: list[object] = []
        preview_calls: list[_PreviewCall] = []
        commands.scan_review_cancel_requested.connect(cancellations.append)
        _record_preview_calls(commands, preview_calls)
        inconsistent = replace(
            _state(),
            pages=(OrganizerPageView(PNG_PAGE_ID, 1, 90, True, ImageTransformIntent()),),
        )
        window._state_changed(inconsistent)
        window._preview_refresh_timer.stop()
        window._request_scan_review()
        operation_id = window._scan_review_start_operation_id
        assert operation_id is not None
        draft = _draft()

        window._scan_review_started(operation_id, draft)

        assert window._scan_review_dialog is None
        assert preview_calls == []
        assert cancellations == [draft]
        assert window._error_label.isVisible()
        assert "could not open scan review safely" in window._error_label.text()


def test_scan_review_rejects_a_begin_result_after_autosave_becomes_pending() -> None:
    with _window() as (window, commands):
        cancellations: list[object] = []
        preview_calls: list[_PreviewCall] = []
        commands.scan_review_cancel_requested.connect(cancellations.append)
        _record_preview_calls(commands, preview_calls)
        _show_state(window)
        window._request_scan_review()
        operation_id = window._scan_review_start_operation_id
        assert operation_id is not None
        draft = _draft()

        window._state_changed(replace(_state(), autosave_pending=True))
        window._autosave_timer.stop()
        assert not window._scan_review_button.isEnabled()
        window._scan_review_started(operation_id, draft)

        assert window._scan_review_dialog is None
        assert preview_calls == []
        assert cancellations == [draft]
        assert window._error_label.isVisible()
        assert "could not open scan review safely" in window._error_label.text()


def test_scan_review_requests_exact_original_then_adjusted_and_ignores_stale_generation() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        draft = _draft()
        dialog = _open_review(window, draft)

        assert len(calls) == 1
        original_call = calls[0]
        assert original_call[2:6] == (
            draft,
            ScanReviewPreviewVariant.ORIGINAL,
            PREVIEW_WIDTH,
            PREVIEW_HEIGHT,
        )
        assert isinstance(original_call[6], OperationCancellation)

        window._scan_review_preview_ready(
            original_call[0],
            original_call[1] + 1,
            _original_result(draft),
        )
        window._scan_review_preview_finished(original_call[0], original_call[1] + 1)

        assert dialog._original_pixmap is None
        assert window._active_scan_review_preview is not None
        assert len(calls) == 1

        _finish_current_preview(window, _original_result(draft))

        assert len(calls) == 2
        adjusted_call = calls[1]
        assert adjusted_call[2:6] == (
            draft,
            ScanReviewPreviewVariant.ADJUSTED,
            PREVIEW_WIDTH,
            PREVIEW_HEIGHT,
        )
        assert adjusted_call[0] != original_call[0]
        assert adjusted_call[1] > original_call[1]

        _finish_current_preview(window, _adjusted_result(draft))

        assert dialog.preview_ready_for_apply


def test_cancelled_preview_suppresses_late_pixels_and_keeps_apply_disabled() -> None:
    with _window() as (window, _commands):
        draft = _draft()
        dialog = _open_review(window, draft)
        active = window._active_scan_review_preview
        assert active is not None

        dialog._request_preview_cancel()
        assert active.cancellation.cancelled()
        cancellation_status = dialog._status.text()
        assert "Cancellation requested" in cancellation_status

        window._scan_review_progress_changed(
            UiProgress("Scan review", "generating", 1, 2, True, active.operation_id)
        )
        assert dialog._status.text() == cancellation_status

        window._scan_review_preview_ready(
            active.operation_id,
            active.generation,
            _original_result(draft),
        )
        window._scan_review_preview_cancelled(
            active.operation_id,
            active.generation,
            "Preview cancelled.",
        )
        window._scan_review_preview_finished(active.operation_id, active.generation)

        assert dialog._original_pixmap is None
        assert dialog._adjusted_pixmap is None
        assert not dialog.preview_ready_for_apply
        assert not dialog._apply_button.isEnabled()
        assert window._active_scan_review_preview is None
        assert window._pending_scan_review_preview is None

        window._scan_review_preview_ready(
            active.operation_id,
            active.generation,
            _original_result(draft),
        )
        assert dialog._original_pixmap is None


def test_cancel_after_original_ready_retains_original_and_cancels_adjusted_phase() -> None:
    with _window() as (window, _commands):
        draft = _draft()
        dialog = _open_review(window, draft)
        active = window._active_scan_review_preview
        assert active is not None
        assert active.variant is ScanReviewPreviewVariant.ORIGINAL
        window._scan_review_preview_ready(
            active.operation_id,
            active.generation,
            _original_result(draft),
        )
        original_charge = window._scan_review_original_bytes
        assert original_charge > 0
        assert dialog._original_pixmap is not None

        dialog._request_preview_cancel()
        window._scan_review_preview_cancelled(
            active.operation_id,
            active.generation,
            "Preview cancelled.",
        )
        window._scan_review_preview_finished(active.operation_id, active.generation)

        assert window._scan_review_original_bytes == original_charge
        assert dialog._original_pixmap is not None
        assert dialog._original_preview.accessibleDescription() == "Original preview ready."
        assert dialog._adjusted_preview.text() == "Adjusted preview cancelled."
        assert not dialog.preview_ready_for_apply
        assert dialog._adjusted_pixmap is None
        assert not dialog._apply_button.isEnabled()
        assert window._active_scan_review_preview is None
        assert window._pending_scan_review_preview is None


def test_rejected_original_pixmap_releases_charge_and_retries_original() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        draft = _draft()
        dialog = _open_review(window, draft)
        active = window._active_scan_review_preview
        assert active is not None
        assert active.variant is ScanReviewPreviewVariant.ORIGINAL

        with patch.object(dialog, "set_original_image", return_value=False):
            window._scan_review_preview_ready(
                active.operation_id,
                active.generation,
                _original_result(draft),
            )

        assert window._scan_review_original_bytes == 0
        assert window._scan_review_adjusted_bytes == 0
        window._scan_review_preview_finished(active.operation_id, active.generation)
        assert window._active_scan_review_preview is None
        assert dialog._update_preview_button.isEnabled()

        dialog._request_preview()

        retried_value = vars(window)["_active_scan_review_preview"]
        assert retried_value is not None
        retried = cast(_ScanReviewPreviewOperation, retried_value)
        assert retried.variant is ScanReviewPreviewVariant.ORIGINAL
        assert calls[-1][3] is ScanReviewPreviewVariant.ORIGINAL


def test_failed_original_retry_survives_the_older_finished_signal() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        draft = _draft()
        dialog = _open_review(window, draft)
        active = window._active_scan_review_preview
        assert active is not None

        window._scan_review_preview_failed(
            active.operation_id,
            active.generation,
            UiFailure("PWS_UI_PREVIEW_FAILED", "Preview failed.", True),
        )
        dialog._request_preview()
        assert window._pending_scan_review_preview == (
            draft,
            ScanReviewPreviewVariant.ORIGINAL,
        )

        window._scan_review_preview_finished(active.operation_id, active.generation)

        retried_value = vars(window)["_active_scan_review_preview"]
        assert retried_value is not None
        retried = cast(_ScanReviewPreviewOperation, retried_value)
        assert retried.operation_id != active.operation_id
        assert retried.variant is ScanReviewPreviewVariant.ORIGINAL
        assert dialog._preview_active
        assert dialog._original_preview.text() == "Original preview loading."
        assert len(calls) == 2


def test_cancelled_original_retry_survives_the_older_finished_signal() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        draft = _draft()
        dialog = _open_review(window, draft)
        active = window._active_scan_review_preview
        assert active is not None

        window._scan_review_preview_cancelled(
            active.operation_id,
            active.generation,
            "Preview cancelled.",
        )
        dialog._request_preview()
        assert window._pending_scan_review_preview == (
            draft,
            ScanReviewPreviewVariant.ORIGINAL,
        )

        window._scan_review_preview_finished(active.operation_id, active.generation)

        retried_value = vars(window)["_active_scan_review_preview"]
        assert retried_value is not None
        retried = cast(_ScanReviewPreviewOperation, retried_value)
        assert retried.operation_id != active.operation_id
        assert retried.variant is ScanReviewPreviewVariant.ORIGINAL
        assert dialog._preview_active
        assert dialog._original_preview.text() == "Original preview loading."
        assert len(calls) == 2


def test_cancel_during_older_operation_drain_discards_the_visible_queued_retry() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        first_draft = _draft()
        dialog = _open_review(window, first_draft)
        active = window._active_scan_review_preview
        assert active is not None
        window._scan_review_preview_failed(
            active.operation_id,
            active.generation,
            UiFailure("PWS_UI_PREVIEW_FAILED", "Preview failed.", True),
        )
        contrast = dialog.findChild(QDoubleSpinBox, "scanReviewContrastSpin")
        assert contrast is not None
        contrast.setValue(125.0)
        _application().processEvents()
        queued_draft = dialog.draft
        assert queued_draft != first_draft

        dialog._request_preview()
        assert _private_value(window, "_pending_scan_review_preview") == (
            queued_draft,
            ScanReviewPreviewVariant.ORIGINAL,
        )
        dialog._request_preview_cancel()

        assert _private_value(window, "_pending_scan_review_preview") is None
        assert _private_value(dialog, "_preview_active") is False
        assert not dialog._cancel_preview_button.isVisible()
        assert "Original preview cancelled" in dialog._original_preview.text()

        window._scan_review_preview_finished(active.operation_id, active.generation)

        assert window._active_scan_review_preview is None
        assert len(calls) == 1
        dialog.reject()
        assert dialog.result() == ScanReviewDialog.DialogCode.Rejected


def test_scan_review_pixmaps_and_organizer_thumbnails_share_one_cache_budget() -> None:
    budget = (2 * PREVIEW_WIDTH * PREVIEW_HEIGHT * 4) + 1_024
    policy = replace(
        NORMAL_RESOURCE_POLICY_V1,
        thumbnail_preview_cache_bytes=budget,
    )
    with _window(resource_policy=policy) as (window, _commands):
        draft = _draft()
        dialog = _open_review(window, draft)
        thumbnail = QPixmap(100, 100)
        thumbnail.fill(Qt.GlobalColor.white)
        window._model.set_preview(PNG_PAGE_ID, thumbnail)
        assert window._model.retained_preview_bytes > 1_024

        _finish_current_preview(
            window,
            _original_result(draft, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT),
        )
        _finish_current_preview(
            window,
            _adjusted_result(draft, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT),
        )

        review_bytes = window._scan_review_original_bytes + window._scan_review_adjusted_bytes
        assert review_bytes == 2 * PREVIEW_WIDTH * PREVIEW_HEIGHT * 4
        assert dialog._original_pixmap is not None
        assert dialog._adjusted_pixmap is not None
        assert window._model.retained_preview_count == 0
        assert window._model.retained_preview_bytes + review_bytes <= budget


def test_scan_review_lifecycle_uses_the_unmodified_constrained_resource_profile() -> None:
    with _window(resource_policy=CONSTRAINED_RESOURCE_POLICY_V1) as (window, _commands):
        draft = _draft()
        dialog = _complete_review(window, draft)

        assert window.resource_policy is CONSTRAINED_RESOURCE_POLICY_V1
        assert dialog.preview_ready_for_apply
        assert (
            window._model.retained_preview_bytes
            + window._scan_review_original_bytes
            + window._scan_review_adjusted_bytes
            <= CONSTRAINED_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes
        )

        dialog.reject()
        _application().processEvents()
        assert window._scan_review_dialog is None
        assert window._scan_review_original_bytes == 0
        assert window._scan_review_adjusted_bytes == 0


def test_apply_requires_the_exact_previewed_draft_and_exact_applied_state() -> None:
    with _window() as (window, commands):
        calls: list[tuple[int, object]] = []
        cancellations: list[object] = []
        commands.scan_review_apply_requested.connect(
            lambda operation_id, draft: calls.append((operation_id, draft))
        )
        commands.scan_review_cancel_requested.connect(cancellations.append)
        opening_draft = _draft()
        dialog = _complete_review(window, opening_draft)
        contrast = dialog.findChild(QDoubleSpinBox, "scanReviewContrastSpin")
        assert contrast is not None
        contrast.setValue(125.0)
        _application().processEvents()
        draft = dialog.draft
        assert draft != opening_draft
        dialog._request_preview()
        _finish_current_preview(window, _adjusted_result(draft))
        stale = draft.with_candidate(
            ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_180)
        )

        window._request_scan_review_apply(stale)
        assert len(tuple(calls)) == 0

        dialog._request_apply()
        assert len(calls) == 1
        operation_id = calls[0][0]
        assert calls[0][1] == draft

        wrong_state = OrganizerViewState(
            CASE_ID,
            REVISION,
            (OrganizerPageView(PDF_PAGE_ID, 1, 0, True),),
            False,
            False,
            False,
        )
        window._scan_review_applied(operation_id, wrong_state)

        open_dialog = window._scan_review_dialog
        assert open_dialog is dialog
        assert not window._scan_review_apply_completed
        assert dialog._error.isVisible()

        dialog._request_apply()
        assert len(calls) == 2
        applied_state = OrganizerViewState(
            CASE_ID,
            REVISION,
            (
                OrganizerPageView(
                    PNG_PAGE_ID,
                    1,
                    0,
                    True,
                    draft.candidate_intent,
                ),
            ),
            True,
            False,
            True,
        )
        window._state_changed(applied_state)
        window._autosave_timer.stop()
        window._scan_review_applied(calls[1][0], applied_state)

        assert window._scan_review_dialog is None
        assert cancellations == []


def test_apply_waits_for_adjusted_preview_finished_without_stalling_dialog() -> None:
    with _window() as (window, commands):
        apply_requested = Mock()
        commands.scan_review_apply_requested.connect(apply_requested)
        draft = _draft()
        dialog = _open_review(window, draft)
        _finish_current_preview(window, _original_result(draft))
        active = window._active_scan_review_preview
        assert active is not None
        assert active.variant is ScanReviewPreviewVariant.ADJUSTED

        window._scan_review_preview_ready(
            active.operation_id,
            active.generation,
            _adjusted_result(draft),
        )
        assert dialog.preview_ready_for_apply

        dialog._request_apply()

        apply_requested.assert_not_called()
        assert window._scan_review_apply_operation_id is None
        pending_before_finish = window._pending_scan_review_apply_draft
        assert pending_before_finish == draft
        assert dialog._apply_active

        window._scan_review_preview_finished(active.operation_id, active.generation)

        apply_requested.assert_called_once()
        operation_id, emitted_draft = apply_requested.call_args.args
        assert emitted_draft == draft
        assert window._pending_scan_review_apply_draft is None
        assert window._scan_review_apply_operation_id == operation_id
        assert dialog._apply_active


def test_no_change_apply_completion_uses_truthful_dialog_wording() -> None:
    with _window() as (window, commands):
        apply_requested = Mock()
        commands.scan_review_apply_requested.connect(apply_requested)
        draft = _draft(candidate=ImageTransformIntent())
        dialog = _complete_review(window, draft)

        dialog._request_apply()
        apply_requested.assert_called_once()
        operation_id, emitted_draft = apply_requested.call_args.args
        assert emitted_draft == draft
        no_change_state = _state()
        window._state_changed(no_change_state)
        window._autosave_timer.stop()

        window._scan_review_applied(operation_id, no_change_state)

        assert window._scan_review_dialog is None
        assert dialog.result() == ScanReviewDialog.DialogCode.Accepted
        assert "No changes were applied" in dialog._status.text()
        assert "adjustments applied" not in dialog._status.text().casefold()


def test_cancel_discards_only_the_ephemeral_review_and_emits_no_manifest_mutation() -> None:
    with _window() as (window, commands):
        mutations: list[str] = []
        cancellations: list[object] = []
        commands.rotate_requested.connect(lambda _page_id: mutations.append("rotate"))
        commands.remove_requested.connect(lambda _page_id: mutations.append("remove"))
        commands.move_requested.connect(lambda _page_id, _offset: mutations.append("move"))
        commands.reorder_requested.connect(lambda _page_ids: mutations.append("reorder"))
        commands.undo_requested.connect(lambda: mutations.append("undo"))
        commands.redo_requested.connect(lambda: mutations.append("redo"))
        commands.flush_requested.connect(lambda: mutations.append("flush"))
        commands.scan_review_apply_requested.connect(
            lambda _operation_id, _draft_value: mutations.append("apply")
        )
        commands.scan_review_cancel_requested.connect(cancellations.append)
        draft = _draft()
        dialog = _complete_review(window, draft)

        dialog.reject()
        _application().processEvents()

        assert cancellations == [draft]
        assert mutations == []
        assert window._scan_review_dialog is None


def test_scan_review_controller_retains_one_active_and_only_the_latest_pending_request() -> None:
    with _window() as (window, commands):
        calls: list[_PreviewCall] = []
        _record_preview_calls(commands, calls)
        first = _draft()
        dialog = _open_review(window, first)
        active = window._active_scan_review_preview
        assert active is not None

        second = first.with_candidate(
            ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_180)
        )
        third = first.with_candidate(
            ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_270)
        )
        dialog._draft = second
        window._queue_scan_review_preview(second)
        dialog._draft = third
        window._queue_scan_review_preview(third)

        assert len(calls) == 1
        assert window._active_scan_review_preview is active
        assert active.cancellation.cancelled()
        assert window._pending_scan_review_preview == (
            third,
            ScanReviewPreviewVariant.ORIGINAL,
        )
