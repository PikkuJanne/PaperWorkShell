"""Routine-output privacy evidence for PWS-0201 image page previews."""

from __future__ import annotations

import hashlib
import logging
import traceback
from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.page_previews import (
    CancellationProbe,
    DeadlineProbe,
    ImagePreviewPhaseSink,
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgress,
    PagePreviewProgressPhase,
    PagePreviewService,
    PdfPreviewPhaseSink,
    reject_page_preview,
)
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationResult,
    ImagePageArtifact,
    ImagePageArtifactHeader,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
    image_page_preview_result_from_artifact,
    reject_image_page,
)
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailRequest,
    PdfThumbnailResult,
)

_CASE_ID = UUID("85100000-0000-4000-8000-000000000001")
_PAGE_ID = UUID("85100000-0000-4000-8000-000000000002")
_OPERATION_ID = UUID("85100000-0000-4000-8000-000000000003")
_OUTPUT_ID = UUID("85100000-0000-4000-8000-000000000004")
_PRIVATE_PATH = r"C:\private\Applicant Name\passport-photo.png"
_PRIVATE_NAME = "PRIVATE-APPLICANT-NAME-CANARY"
_PRIVATE_METADATA_VALUE = "EXIF-USER-COMMENT-PRIVATE-CANARY"
_RAW_EXCEPTION_VALUE = "NATIVE-DECODER-PRIVATE-CANARY"
_PRIVATE_PIXELS = bytes((80, 82, 73, 255, 86, 65, 84, 255))


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


def _request() -> ImagePagePreviewRequest:
    return ImagePagePreviewRequest(_CASE_ID, _PAGE_ID, 2, 1)


def _artifact() -> ImagePageArtifact:
    pixel_digest = hashlib.sha256(_PRIVATE_PIXELS).hexdigest()
    normalized = ImageNormalizationResult(
        metadata=_metadata(),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        bytes_written=len(_PRIVATE_PIXELS),
        pixel_sha256=pixel_digest,
    )
    header = ImagePageArtifactHeader(_OPERATION_ID, _OUTPUT_ID, normalized)
    return ImagePageArtifact(header, _PRIVATE_PIXELS)


def _result(request: ImagePagePreviewRequest | None = None) -> ImagePagePreviewResult:
    selected_request = _request() if request is None else request
    return image_page_preview_result_from_artifact(selected_request, _artifact())


class _UnusedPdfGenerator:
    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfPreviewPhaseSink,
    ) -> PdfThumbnailResult:
        del request, deadline, cancellation, progress
        raise AssertionError("the PDF generator must not run for an image-only batch")


class _ImageGenerator:
    def __init__(
        self,
        outcome: Callable[[ImagePagePreviewRequest], ImagePagePreviewResult],
    ) -> None:
        self._outcome = outcome

    def get_or_generate(
        self,
        request: ImagePagePreviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePagePreviewResult:
        assert deadline() is False
        assert cancellation() is False
        progress(ImagePagePreviewProgressPhase.GENERATING)
        return self._outcome(request)


def _service(
    outcome: Callable[[ImagePagePreviewRequest], ImagePagePreviewResult],
) -> PagePreviewService:
    return PagePreviewService(
        _UnusedPdfGenerator(),
        _ImageGenerator(outcome),
        id_factory=lambda: _OPERATION_ID,
    )


def _render_exception(error: BaseException) -> str:
    return (
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
        + f"\n{error!s}\n{error!r}\n{error.args!r}"
    )


def _privacy_canaries(result: ImagePagePreviewResult) -> tuple[str, ...]:
    return (
        _PRIVATE_PATH,
        _PRIVATE_NAME,
        _PRIVATE_METADATA_VALUE,
        _RAW_EXCEPTION_VALUE,
        result.snapshot.pixels.hex(),
        repr(result.snapshot.pixels),
        hashlib.sha256(result.snapshot.pixels).hexdigest(),
    )


def test_typed_image_results_progress_and_logs_exclude_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    artifact = _artifact()
    result = _result()
    progress = PagePreviewProgress(
        _OPERATION_ID,
        PagePreviewProgressPhase.COMPLETE,
        1,
        1,
    )
    rendered_objects = (
        artifact.header.result,
        artifact.header,
        artifact,
        result.request,
        result.snapshot.metadata,
        result.snapshot,
        result,
        progress,
    )
    logging.getLogger("paperworkshell.tests.image_privacy").debug(
        "image-preview=%r",
        rendered_objects,
    )

    rendered = "\n".join(
        (
            *(repr(value) for value in rendered_objects),
            *(str(value) for value in rendered_objects),
            caplog.text,
        )
    )
    assert all(canary not in rendered for canary in _privacy_canaries(result))
    assert not hasattr(result.snapshot.metadata, "exif_values")
    assert not hasattr(result.snapshot.metadata, "source_name")
    assert not hasattr(result.snapshot, "source_path")


def test_domain_and_app_rejections_drop_raw_exception_links_and_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = " ".join(
        (_PRIVATE_PATH, _PRIVATE_NAME, _PRIVATE_METADATA_VALUE, _RAW_EXCEPTION_VALUE)
    )
    try:
        raise RuntimeError(private)
    except RuntimeError:
        with pytest.raises(ImagePageError) as domain_raised:
            reject_image_page(ImagePageErrorCode.MALFORMED, private)
    try:
        raise RuntimeError(private)
    except RuntimeError:
        with pytest.raises(PagePreviewError) as app_raised:
            reject_page_preview(PagePreviewErrorCode.GENERATOR_FAILED, private)

    assert domain_raised.value.code is ImagePageErrorCode.MALFORMED
    assert domain_raised.value.context == "image.page"
    assert app_raised.value.code is PagePreviewErrorCode.GENERATOR_FAILED
    assert app_raised.value.context == "preview.operation"
    for error in (domain_raised.value, app_raised.value):
        rendered = _render_exception(error) + caplog.text
        assert error.__cause__ is None
        assert error.__context__ is None
        assert all(canary not in rendered for canary in _privacy_canaries(_result()))


def test_raw_image_generator_failure_has_one_exact_sanitized_app_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = " ".join(
        (_PRIVATE_PATH, _PRIVATE_NAME, _PRIVATE_METADATA_VALUE, _RAW_EXCEPTION_VALUE)
    )

    def fail(_: ImagePagePreviewRequest) -> ImagePagePreviewResult:
        raise RuntimeError(private)

    with pytest.raises(PagePreviewError) as raised:
        _service(fail).get_previews((_request(),))

    assert raised.value.code is PagePreviewErrorCode.GENERATOR_FAILED
    assert raised.value.context == "preview.image_generator"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = _render_exception(raised.value) + caplog.text
    assert all(canary not in rendered for canary in _privacy_canaries(_result()))


def test_malformed_typed_generator_error_is_rebuilt_without_cause_or_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = " ".join(
        (_PRIVATE_PATH, _PRIVATE_NAME, _PRIVATE_METADATA_VALUE, _RAW_EXCEPTION_VALUE)
    )

    def fail(_: ImagePagePreviewRequest) -> ImagePagePreviewResult:
        try:
            raise RuntimeError(private)
        except RuntimeError as raw_error:
            malformed = ImagePageError(ImagePageErrorCode.MALFORMED, "image.decode")
            malformed.context = private
            malformed.args = (private,)
            raise malformed from raw_error

    with pytest.raises(ImagePageError) as raised:
        _service(fail).get_previews((_request(),))

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.page"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = _render_exception(raised.value) + caplog.text
    assert all(canary not in rendered for canary in _privacy_canaries(_result()))


def test_malformed_generator_result_is_rejected_without_stringification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _HostileResult:
        def __str__(self) -> str:
            raise AssertionError("malformed results must not be stringified")

        def __repr__(self) -> str:
            raise AssertionError("malformed results must not be represented")

    def malformed(_: ImagePagePreviewRequest) -> ImagePagePreviewResult:
        return cast(ImagePagePreviewResult, _HostileResult())

    with pytest.raises(PagePreviewError) as raised:
        _service(malformed).get_previews((_request(),))

    assert raised.value.code is PagePreviewErrorCode.RESULT_INVALID
    assert raised.value.context == "preview.image_result"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = _render_exception(raised.value) + caplog.text
    assert all(canary not in rendered for canary in _privacy_canaries(_result()))
