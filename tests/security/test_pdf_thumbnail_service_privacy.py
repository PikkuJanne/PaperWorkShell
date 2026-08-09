"""Routine-output privacy evidence for the PWS-0108 thumbnail service."""

from __future__ import annotations

import hashlib
import traceback
from dataclasses import dataclass
from uuid import UUID

import pytest

from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
)

_CASE_ID = UUID("71800000-0000-4000-8000-000000000001")
_SOURCE_ID = UUID("71800000-0000-4000-8000-000000000002")
_PAGE_ID = UUID("71800000-0000-4000-8000-000000000003")
_OPERATION_ID = UUID("71800000-0000-4000-8000-000000000004")
_PRIVATE_PATH = "C:/PRIVATE-APPLICANT-NAME/source-canary.pdf"
_PRIVATE_TEXT = "PRIVATE-APPLICANT-TEXT-CANARY"
_PRIVATE_PIXELS = b"PRIVATE-PIXEL-CANARY"[:3] + b"\xff"


def _request() -> PdfThumbnailRequest:
    return PdfThumbnailRequest(_CASE_ID, _PAGE_ID, 1, 1)


def _result(request: PdfThumbnailRequest) -> PdfThumbnailResult:
    source_digest = hashlib.sha256(_PRIVATE_TEXT.encode("ascii")).hexdigest()
    profile_digest = hashlib.sha256(b"private profile canary").hexdigest()
    key = PdfThumbnailCacheKey(
        case_id=request.case_id,
        source_id=_SOURCE_ID,
        page_id=request.page_id,
        source_page_index=0,
        source_byte_size=123,
        source_sha256=source_digest,
        maximum_width_pixels=1,
        maximum_height_pixels=1,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=profile_digest,
    )
    snapshot = PdfThumbnailSnapshot(
        case_id=request.case_id,
        page_id=request.page_id,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        pixels=_PRIVATE_PIXELS,
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=PdfRenderRotation.DEGREES_0,
    )
    return PdfThumbnailResult(request, key, snapshot, PdfThumbnailCacheStatus.GENERATED)


@dataclass(slots=True)
class _Generator:
    failure: BaseException | None = None

    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> PdfThumbnailResult:
        assert callable(deadline) and not deadline()
        assert callable(cancellation) and not cancellation()
        assert callable(progress)
        if self.failure is not None:
            raise self.failure
        progress(PdfThumbnailProgressPhase.RENDERING)
        progress(PdfThumbnailProgressPhase.VALIDATING)
        return _result(request)


def test_results_progress_and_logs_exclude_paths_text_digests_and_pixel_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    generator = _Generator()
    progress: list[PdfThumbnailProgress] = []
    service = PdfThumbnailService(generator, id_factory=lambda: _OPERATION_ID)

    result = service.get_thumbnails((_request(),), progress=progress.append)[0]

    rendered = "\n".join(
        (
            repr(result),
            repr(result.snapshot),
            repr(result.cache_key),
            repr(progress),
            caplog.text,
        )
    )
    assert _PRIVATE_PATH not in rendered
    assert _PRIVATE_TEXT not in rendered
    assert repr(_PRIVATE_PIXELS) not in rendered
    assert _PRIVATE_PIXELS.hex() not in rendered
    assert result.cache_key.source_sha256 not in rendered
    assert result.cache_key.renderer_profile_sha256 not in rendered


def test_generator_exception_content_is_removed_from_error_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = f"{_PRIVATE_PATH} {_PRIVATE_TEXT} {_PRIVATE_PIXELS.hex()}"
    service = PdfThumbnailService(_Generator(RuntimeError(private)))

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails((_request(),))

    rendered = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    rendered += repr(raised.value) + repr(raised.value.args) + caplog.text
    assert raised.value.code is PdfThumbnailErrorCode.WORKER_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _PRIVATE_PATH not in rendered
    assert _PRIVATE_TEXT not in rendered
    assert _PRIVATE_PIXELS.hex() not in rendered
