"""Routine-output privacy evidence for provisional scan review."""

from __future__ import annotations

import hashlib
import logging
import os
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QWidget,
)

from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewError,
    ScanReviewErrorCode,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
    reject_scan_review,
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
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.scan_review import ScanReviewDialog

_CASE_ID = UUID("85200000-0000-4000-8000-000000000001")
_PAGE_ID = UUID("85200000-0000-4000-8000-000000000002")
_SOURCE_ID = UUID("85200000-0000-4000-8000-000000000003")
_PRIVATE_PATH = r"C:\private\Applicant Name\scan-passport.png"
_PRIVATE_NAME = "SCAN-REVIEW-PRIVATE-APPLICANT-CANARY"
_PRIVATE_METADATA_VALUE = "SCAN-REVIEW-PRIVATE-EXIF-CANARY"
_RAW_EXCEPTION_VALUE = "SCAN-REVIEW-PRIVATE-DECODER-CANARY"
_PRIVATE_PHASE = "scan-review-private-filename-phase-canary"
_PRIVATE_PIXELS = bytes((80, 82, 73, 255, 86, 65, 84, 255))
_PRIVATE_SOURCE_DIGEST = hashlib.sha256(b"SCAN-REVIEW-PRIVATE-ENCODED-SOURCE-CANARY").hexdigest()


def _metadata() -> ImagePageMetadata:
    return ImagePageMetadata(
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
        metadata_record_count=1,
        metadata_byte_count=len(_PRIVATE_METADATA_VALUE),
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _draft() -> ScanReviewDraft:
    return ScanReviewDraft(
        case_id=_CASE_ID,
        page_id=_PAGE_ID,
        manifest_revision=7,
        base_intent=ImageTransformIntent(),
        candidate_intent=ImageTransformIntent(
            deskew_angle_millidegrees_clockwise=500,
            contrast_factor_millionths=1_250_000,
            background_cleanup_strength_millionths=100_000,
        ),
        suggestion_present=True,
    )


def _original_result(draft: ScanReviewDraft) -> ImagePagePreviewResult:
    request = ImagePagePreviewRequest(draft.case_id, draft.page_id, 2, 1)
    snapshot = ImagePagePreviewSnapshot(
        case_id=draft.case_id,
        page_id=draft.page_id,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        pixels=_PRIVATE_PIXELS,
        metadata=_metadata(),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
    )
    return ImagePagePreviewResult(request, snapshot)


def _adjusted_result(draft: ScanReviewDraft) -> ImagePageTransformResult:
    request = ImagePageTransformRequest(
        draft.case_id,
        draft.page_id,
        draft.candidate_intent,
        2,
        1,
    )
    snapshot = ImagePageTransformSnapshot(
        case_id=draft.case_id,
        source_id=_SOURCE_ID,
        page_id=draft.page_id,
        intent=draft.candidate_intent,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        bytes_written=len(_PRIVATE_PIXELS),
        pixel_sha256=hashlib.sha256(_PRIVATE_PIXELS).hexdigest(),
        cache_key_sha256=_PRIVATE_SOURCE_DIGEST,
    )
    return ImagePageTransformResult(request, snapshot, _PRIVATE_PIXELS)


def _privacy_canaries(draft: ScanReviewDraft) -> tuple[str, ...]:
    return (
        _PRIVATE_PATH,
        _PRIVATE_NAME,
        _PRIVATE_METADATA_VALUE,
        _RAW_EXCEPTION_VALUE,
        _PRIVATE_PHASE,
        _PRIVATE_SOURCE_DIGEST,
        _PRIVATE_PIXELS.hex(),
        repr(_PRIVATE_PIXELS),
        hashlib.sha256(_PRIVATE_PIXELS).hexdigest(),
        draft.candidate_intent.intent_sha256,
    )


def _render_exception(error: BaseException) -> str:
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog(draft: ScanReviewDraft) -> Iterator[ScanReviewDialog]:
    app = _application()
    dialog = ScanReviewDialog(draft)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _surface_text(dialog: ScanReviewDialog) -> str:
    values: list[str] = [
        dialog.objectName(),
        dialog.windowTitle(),
        dialog.accessibleName(),
        dialog.accessibleDescription(),
        repr(dialog.draft),
    ]
    for widget in dialog.findChildren(QWidget):
        values.extend(
            (
                widget.objectName(),
                widget.accessibleName(),
                widget.accessibleDescription(),
                widget.toolTip(),
                widget.statusTip(),
                widget.whatsThis(),
            )
        )
    for label in dialog.findChildren(QLabel):
        values.append(label.text())
    for button in dialog.findChildren(QPushButton):
        values.append(button.text())
    for group in dialog.findChildren(QGroupBox):
        values.append(group.title())
    for combo in dialog.findChildren(QComboBox):
        values.extend(combo.itemText(index) for index in range(combo.count()))
    for spin in dialog.findChildren(QDoubleSpinBox):
        values.extend((spin.prefix(), spin.suffix(), spin.specialValueText()))
    return "\n".join(values)


def _image() -> QImage:
    return QImage(
        _PRIVATE_PIXELS,
        2,
        1,
        8,
        QImage.Format.Format_RGBA8888,
    ).copy()


def test_scan_review_typed_results_errors_and_logs_exclude_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    draft = _draft()
    original = ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ORIGINAL,
        _original_result(draft),
    )
    adjusted = ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        _adjusted_result(draft),
    )
    private = " ".join(
        (_PRIVATE_PATH, _PRIVATE_NAME, _PRIVATE_METADATA_VALUE, _RAW_EXCEPTION_VALUE)
    )
    try:
        raise RuntimeError(private)
    except RuntimeError:
        with pytest.raises(ScanReviewError) as raised:
            reject_scan_review(ScanReviewErrorCode.MODEL_INVALID, private)

    error = raised.value
    assert error.code is ScanReviewErrorCode.MODEL_INVALID
    assert error.context == "scan_review.operation"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert repr(draft) == (
        "ScanReviewDraft(manifest_revision=7, suggestion_present=True, review_version=1)"
    )
    assert repr(original) == "ScanReviewPreviewResult(variant='original', preview_version=1)"
    assert repr(adjusted) == "ScanReviewPreviewResult(variant='adjusted', preview_version=1)"

    rendered_objects = (draft, original, adjusted, error)
    logging.getLogger("paperworkshell.tests.scan_review_privacy").debug(
        "scan-review=%r",
        rendered_objects,
    )
    rendered = "\n".join(
        (
            *(repr(value) for value in rendered_objects),
            *(str(value) for value in rendered_objects),
            _render_exception(error),
            caplog.text,
        )
    )
    assert all(canary not in rendered for canary in _privacy_canaries(draft))
    assert str(draft.case_id) not in rendered
    assert str(draft.page_id) not in rendered


def test_scan_review_dialog_exposes_only_sanitized_content_free_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    draft = _draft()
    retained_surface: list[str] = []
    with _dialog(draft) as dialog:
        assert dialog.set_preview_loading(draft)
        assert dialog.set_progress(draft, _PRIVATE_PHASE, 1, 3)
        retained_surface.append(_surface_text(dialog))
        assert _PRIVATE_PHASE not in retained_surface[-1]

        assert dialog.set_original_image(draft, _image())
        assert dialog.set_adjusted_image(draft, _image())
        assert dialog.preview_ready_for_apply

        raw_failure = RuntimeError(" ".join((_PRIVATE_PATH, _PRIVATE_NAME, _RAW_EXCEPTION_VALUE)))
        failure = sanitize_ui_failure(raw_failure)
        assert dialog.set_preview_loading(draft)
        assert dialog.set_failure(
            draft,
            failure.code,
            failure.message,
            variant=ScanReviewPreviewVariant.ADJUSTED,
        )
        retained_surface.append(_surface_text(dialog))
        original = dialog.findChild(QLabel, "scanReviewOriginalPreview")
        assert original is not None
        assert not original.pixmap().isNull()

        assert dialog.set_preview_loading(draft)
        assert dialog.set_failure(
            draft,
            f"INVALID-{_PRIVATE_NAME}",
            f"{_PRIVATE_METADATA_VALUE}\u0007",
            variant=ScanReviewPreviewVariant.ADJUSTED,
        )
        retained_surface.append(_surface_text(dialog))
        assert "PWS_UI_UNEXPECTED" in retained_surface[-1]

        assert dialog.set_preview_loading(draft)
        assert dialog.set_preview_cancelled(draft, ScanReviewPreviewVariant.ADJUSTED)
        retained_surface.append(_surface_text(dialog))
        assert "Preview cancelled. Adjustments were not applied." in retained_surface[-1]
        assert not original.pixmap().isNull()

        logging.getLogger("paperworkshell.tests.scan_review_ui_privacy").debug(
            "scan-review-ui=%r surface=%s",
            dialog.draft,
            retained_surface,
        )

    rendered = "\n".join((*retained_surface, caplog.text))
    assert all(canary not in rendered for canary in _privacy_canaries(draft))
