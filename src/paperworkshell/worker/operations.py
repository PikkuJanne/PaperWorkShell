"""Fixed, explicit operation handlers used by the document-worker host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID

from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_inspection import PdfInspectionError, PdfInspectionErrorCode
from paperworkshell.domain.pdf_rendering import PdfPageBox, PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
    IMAGE_PDF_EXPORT_CAPABILITY,
    IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
    IMAGE_TRANSFORM_CAPABILITY,
    IMAGE_TRANSFORM_CONTRAST_PARAMETER,
    IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_CROP_X_PARAMETER,
    IMAGE_TRANSFORM_CROP_Y_PARAMETER,
    IMAGE_TRANSFORM_DESKEW_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_TRANSFORM_PAGE_ID_PARAMETER,
    IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER,
    IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER,
    MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES,
    NOOP_CAPABILITY,
    OCR_PROCESS_CAPABILITY,
    OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
    OCR_PROCESS_OUTPUT_CONTENT_KIND,
    OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
    OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
    OCR_PROCESS_PAGE_COUNT_PARAMETER,
    OCR_PROCESS_PROFILE_ID_PARAMETER,
    OCR_PROCESS_RESULT_HANDLE_PARAMETER,
    OCR_PROCESS_SOURCE_CONTENT_KINDS,
    OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
    PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_CAPABILITY,
    PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
    PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
    PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
    PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
    PDF_THUMBNAIL_PAGE_ID_PARAMETER,
    PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
    SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
    CapabilityDescriptor,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    ScalarValueType,
    TaggedScalar,
)

NOOP_COOPERATION_WINDOW_SECONDS: Final = 0.100


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Trusted host context passed to one fixed operation handler."""

    operation_id: UUID
    case_id: UUID
    limits: ResourceLimits
    cancel_requested: Event
    report_progress: Callable[[OperationProgressPayload], None]
    deadline_expired: Callable[[], bool]
    inputs: tuple[CaseObjectReference, ...] = ()
    parameters: tuple[TaggedScalar, ...] = ()


class OperationHandler(Protocol):
    """A statically registered worker operation implementation."""

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """Return the exact capability implemented by this handler."""

    def execute(self, context: OperationContext) -> OperationResultPayload:
        """Run one operation and return a sanitized terminal result."""


@dataclass(frozen=True, slots=True)
class NoopOperationHandler:
    """A cooperative protocol smoke-test operation with no document access."""

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return NOOP_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.STEPS)
        )
        if context.cancel_requested.wait(NOOP_COOPERATION_WINDOW_SECONDS):
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.STEPS)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (), None)


@dataclass(frozen=True, slots=True)
class ImageNormalizeOperationRequest:
    """Path-free authority and bounds for one normalized image-page artifact."""

    source_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    output_object_id: UUID
    maximum_width_pixels: int
    maximum_height_pixels: int
    additional_rotation: int

    def __post_init__(self) -> None:
        handles = (self.source_handle, self.output_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or type(self.maximum_width_pixels) is not int
            or self.maximum_width_pixels <= 0
            or type(self.maximum_height_pixels) is not int
            or self.maximum_height_pixels <= 0
            or type(self.additional_rotation) is not int
            or self.additional_rotation not in {0, 90, 180, 270}
        ):
            raise ValueError("validated image-normalize parameters are unavailable")


class ImageNormalizeAdapter(Protocol):
    """Injected image adapter that consumes both transferred handles."""

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> None:
        """Consume handles when terminal state prevents normalization."""

    def normalize(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> CaseObjectReference:
        """Normalize one source into a bounded authenticated staging artifact."""


@dataclass(frozen=True, slots=True)
class ImageNormalizeOperationHandler:
    """Coordinate the closed ``image.normalize`` operation around one adapter."""

    adapter: ImageNormalizeAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return IMAGE_NORMALIZE_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _image_normalize_operation_request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()

        try:
            output = self.adapter.normalize(context, request)
        except ImagePageError as error:
            if error.code is ImagePageErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is ImagePageErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise

        # The adapter has consumed both handles. Late terminal state suppresses
        # the staging reference; the broker removes it after worker termination.
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_image_normalize_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


@dataclass(frozen=True, slots=True)
class SignatureImageRasterOperationRequest:
    """Path-free authority and bounds for one straight-alpha signature raster."""

    source_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    output_object_id: UUID
    maximum_width_pixels: int
    maximum_height_pixels: int

    def __post_init__(self) -> None:
        handles = (self.source_handle, self.output_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or type(self.maximum_width_pixels) is not int
            or self.maximum_width_pixels <= 0
            or type(self.maximum_height_pixels) is not int
            or self.maximum_height_pixels <= 0
        ):
            raise ValueError("validated signature-raster parameters are unavailable")


class SignatureImageRasterAdapter(Protocol):
    """Injected adapter consuming one asset handle and one bounded output section."""

    def discard_handles(
        self,
        context: OperationContext,
        request: SignatureImageRasterOperationRequest,
    ) -> None: ...

    def rasterize(
        self,
        context: OperationContext,
        request: SignatureImageRasterOperationRequest,
    ) -> CaseObjectReference: ...


@dataclass(frozen=True, slots=True)
class SignatureImageRasterOperationHandler:
    """Coordinate the closed ``image.signature_raster`` operation."""

    adapter: SignatureImageRasterAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return SIGNATURE_IMAGE_RASTER_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _signature_image_raster_operation_request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()
        try:
            output = self.adapter.rasterize(context, request)
        except ImagePageError as error:
            if error.code is ImagePageErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is ImagePageErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is ImagePageErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            if error.code is ImagePageErrorCode.UNSUPPORTED and error.context in {
                "image.signature_icc_profile",
                "image.signature_color_profile",
            }:
                return _unsupported_signature_image_profile_result()
            raise
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_signature_image_raster_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


def _signature_image_raster_operation_request(
    context: OperationContext,
) -> SignatureImageRasterOperationRequest:
    return SignatureImageRasterOperationRequest(
        source_handle=_positive_integer_parameter(
            context, SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER
        ),
        output_handle=_positive_integer_parameter(
            context, SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER
        ),
        output_object_id=_uuid_parameter(
            context, SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER
        ),
        maximum_width_pixels=_positive_integer_parameter(
            context, SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER
        ),
        maximum_height_pixels=_positive_integer_parameter(
            context, SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER
        ),
    )


def _require_signature_image_raster_output(
    context: OperationContext,
    request: SignatureImageRasterOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("signature-raster adapter returned an invalid staging reference")


def _unsupported_signature_image_profile_result() -> OperationResultPayload:
    """Return one value-free category for the signature profile's color-policy rejection."""

    return OperationResultPayload(
        OperationOutcome.FAILED,
        (),
        ErrorPayload(
            ErrorCode.WORKER_OPERATION_FAILED,
            ErrorCategory.UNSUPPORTED,
            RetryAction.USER_ACTION,
            False,
            (),
        ),
    )


@dataclass(frozen=True, slots=True)
class ImageTransformOperationRequest:
    """Path-free authority and normalized intent for one scan transform."""

    source_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    output_object_id: UUID
    page_id: UUID
    maximum_width_pixels: int
    maximum_height_pixels: int
    quarter_turns_clockwise: int
    crop_x_millionths: int
    crop_y_millionths: int
    crop_width_millionths: int
    crop_height_millionths: int
    deskew_millidegrees_clockwise: int
    contrast_factor_millionths: int
    background_cleanup_strength_millionths: int

    def __post_init__(self) -> None:
        handles = (self.source_handle, self.output_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or self.page_id == self.output_object_id
            or type(self.maximum_width_pixels) is not int
            or self.maximum_width_pixels <= 0
            or type(self.maximum_height_pixels) is not int
            or self.maximum_height_pixels <= 0
            or type(self.quarter_turns_clockwise) is not int
            or self.quarter_turns_clockwise not in {0, 1, 2, 3}
            or any(
                type(value) is not int
                for value in (
                    self.crop_x_millionths,
                    self.crop_y_millionths,
                    self.crop_width_millionths,
                    self.crop_height_millionths,
                    self.deskew_millidegrees_clockwise,
                    self.contrast_factor_millionths,
                    self.background_cleanup_strength_millionths,
                )
            )
            or not 0 <= self.crop_x_millionths <= 1_000_000
            or not 0 <= self.crop_y_millionths <= 1_000_000
            or not 1 <= self.crop_width_millionths <= 1_000_000
            or not 1 <= self.crop_height_millionths <= 1_000_000
            or self.crop_x_millionths + self.crop_width_millionths > 1_000_000
            or self.crop_y_millionths + self.crop_height_millionths > 1_000_000
            or not -15_000 <= self.deskew_millidegrees_clockwise <= 15_000
            or not 0 <= self.contrast_factor_millionths <= 4_000_000
            or not 0 <= self.background_cleanup_strength_millionths <= 1_000_000
        ):
            raise ValueError("validated image-transform parameters are unavailable")


class ImageTransformAdapter(Protocol):
    """Injected image adapter that consumes one immutable source and output section."""

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> None: ...

    def transform(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> CaseObjectReference: ...


@dataclass(frozen=True, slots=True)
class ImageTransformOperationHandler:
    """Coordinate the closed ``image.transform`` operation around one adapter."""

    adapter: ImageTransformAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return IMAGE_TRANSFORM_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _image_transform_operation_request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()
        try:
            output = self.adapter.transform(context, request)
        except ImagePageError as error:
            if error.code is ImagePageErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is ImagePageErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_image_transform_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


def _image_transform_operation_request(
    context: OperationContext,
) -> ImageTransformOperationRequest:
    return ImageTransformOperationRequest(
        source_handle=_positive_integer_parameter(context, IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER),
        output_handle=_positive_integer_parameter(context, IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER),
        output_object_id=_uuid_parameter(context, IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER),
        page_id=_uuid_parameter(context, IMAGE_TRANSFORM_PAGE_ID_PARAMETER),
        maximum_width_pixels=_positive_integer_parameter(
            context, IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER
        ),
        maximum_height_pixels=_positive_integer_parameter(
            context, IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER
        ),
        quarter_turns_clockwise=_integer_parameter(
            context, IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER, minimum=0
        ),
        crop_x_millionths=_integer_parameter(context, IMAGE_TRANSFORM_CROP_X_PARAMETER, minimum=0),
        crop_y_millionths=_integer_parameter(context, IMAGE_TRANSFORM_CROP_Y_PARAMETER, minimum=0),
        crop_width_millionths=_positive_integer_parameter(
            context, IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER
        ),
        crop_height_millionths=_positive_integer_parameter(
            context, IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER
        ),
        deskew_millidegrees_clockwise=_integer_parameter(
            context, IMAGE_TRANSFORM_DESKEW_PARAMETER, minimum=-15_000
        ),
        contrast_factor_millionths=_integer_parameter(
            context, IMAGE_TRANSFORM_CONTRAST_PARAMETER, minimum=0
        ),
        background_cleanup_strength_millionths=_integer_parameter(
            context, IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER, minimum=0
        ),
    )


def _require_image_transform_output(
    context: OperationContext,
    request: ImageTransformOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("image-transform adapter returned an invalid staging reference")


def _image_normalize_operation_request(
    context: OperationContext,
) -> ImageNormalizeOperationRequest:
    return ImageNormalizeOperationRequest(
        source_handle=_positive_integer_parameter(
            context,
            IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
        ),
        output_handle=_positive_integer_parameter(
            context,
            IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
        ),
        output_object_id=_uuid_parameter(
            context,
            IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
        ),
        maximum_width_pixels=_positive_integer_parameter(
            context,
            IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
        ),
        maximum_height_pixels=_positive_integer_parameter(
            context,
            IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
        ),
        additional_rotation=_integer_parameter(
            context,
            IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
            minimum=0,
        ),
    )


def _require_image_normalize_output(
    context: OperationContext,
    request: ImageNormalizeOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("image-normalize adapter returned an invalid staging reference")


@dataclass(frozen=True, slots=True)
class PdfThumbnailRenderRequest:
    """Strongly typed, path-free parameters for one thumbnail render."""

    source_handle: int
    cache_handle: int
    cache_object_id: UUID
    page_id: UUID
    source_page_index: int
    maximum_width_pixels: int
    maximum_height_pixels: int
    page_box: PdfPageBox
    additional_rotation: PdfRenderRotation

    def __post_init__(self) -> None:
        if (
            type(self.source_handle) is not int
            or self.source_handle <= 0
            or type(self.cache_handle) is not int
            or self.cache_handle <= 0
            or self.source_handle == self.cache_handle
            or type(self.cache_object_id) is not UUID
            or self.cache_object_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or self.cache_object_id == self.page_id
            or type(self.source_page_index) is not int
            or self.source_page_index < 0
            or type(self.maximum_width_pixels) is not int
            or self.maximum_width_pixels <= 0
            or type(self.maximum_height_pixels) is not int
            or self.maximum_height_pixels <= 0
            or type(self.page_box) is not PdfPageBox
            or type(self.additional_rotation) is not PdfRenderRotation
        ):
            raise ValueError("validated thumbnail render parameters are unavailable")


class PdfThumbnailAdapter(Protocol):
    """Injected renderer that owns both brokered handles on every dispatch."""

    def discard_handles(self, source_handle: int, cache_handle: int) -> None:
        """Consume both handles when cancellation or deadline prevents dispatch."""

    def render_to_cache(
        self,
        context: OperationContext,
        request: PdfThumbnailRenderRequest,
    ) -> CaseObjectReference:
        """Consume both handles and return the authenticated staging reference."""


@dataclass(frozen=True, slots=True)
class PdfThumbnailOperationHandler:
    """Coordinate the closed ``pdf.thumbnail`` operation around one adapter."""

    adapter: PdfThumbnailAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return PDF_THUMBNAIL_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _pdf_thumbnail_render_request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(request.source_handle, request.cache_handle)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(request.source_handle, request.cache_handle)
            return timed_out_operation_result()

        try:
            output = self.adapter.render_to_cache(context, request)
        except PdfThumbnailError as error:
            if error.code is PdfThumbnailErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfThumbnailErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise

        # The adapter has consumed both handles at this point. A late terminal
        # request suppresses the staging reference; the trusted broker owns its
        # eventual cleanup after confirmed worker-tree termination.
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_pdf_thumbnail_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


@dataclass(frozen=True, slots=True, repr=False)
class OcrProcessOperationRequest:
    """Path-free authority and opaque configuration for one local OCR run."""

    source_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    result_handle: int = field(repr=False)
    output_object_id: UUID
    source_content_kind: str
    page_count: int
    language_set_id: str
    profile_id: str

    def __post_init__(self) -> None:
        handles = (self.source_handle, self.output_handle, self.result_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or type(self.source_content_kind) is not str
            or self.source_content_kind not in OCR_PROCESS_SOURCE_CONTENT_KINDS
            or type(self.page_count) is not int
            or self.page_count <= 0
            or type(self.language_set_id) is not str
            or type(self.profile_id) is not str
        ):
            raise ValueError("validated OCR-process parameters are unavailable")

    def __repr__(self) -> str:
        return (
            "OcrProcessOperationRequest("
            f"output_object_id={self.output_object_id!r}, "
            f"source_content_kind={self.source_content_kind!r}, "
            f"page_count={self.page_count!r})"
        )


class OcrProcessAdapter(Protocol):
    """Injected OCR adapter that consumes every transferred handle."""

    def discard_handles(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> None:
        """Consume transferred handles when terminal state prevents OCR."""

    def process(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> CaseObjectReference:
        """Write one bounded report and opaque PDF candidate through brokered handles."""


@dataclass(frozen=True, slots=True)
class OcrProcessOperationHandler:
    """Coordinate the closed ``ocr.process`` operation around one adapter."""

    adapter: OcrProcessAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return OCR_PROCESS_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _ocr_process_operation_request(context)
        total_pages = request.page_count
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                0,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()

        try:
            output = self.adapter.process(context, request)
        except OcrError as error:
            if error.code is OcrErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is OcrErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise

        # Successful adapters have consumed and closed every transferred handle.
        # Late terminal state suppresses the untrusted candidate reference.
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_ocr_process_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                total_pages,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


def _ocr_process_operation_request(context: OperationContext) -> OcrProcessOperationRequest:
    if len(context.inputs) != 1:
        raise ValueError("validated OCR-process source is unavailable")
    return OcrProcessOperationRequest(
        source_handle=_positive_integer_parameter(context, OCR_PROCESS_SOURCE_HANDLE_PARAMETER),
        output_handle=_positive_integer_parameter(context, OCR_PROCESS_OUTPUT_HANDLE_PARAMETER),
        result_handle=_positive_integer_parameter(context, OCR_PROCESS_RESULT_HANDLE_PARAMETER),
        output_object_id=_uuid_parameter(context, OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER),
        source_content_kind=context.inputs[0].content_kind,
        page_count=_positive_integer_parameter(context, OCR_PROCESS_PAGE_COUNT_PARAMETER),
        language_set_id=_token_parameter(context, OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER),
        profile_id=_token_parameter(context, OCR_PROCESS_PROFILE_ID_PARAMETER),
    )


def _require_ocr_process_output(
    context: OperationContext,
    request: OcrProcessOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != OCR_PROCESS_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("OCR-process adapter returned an invalid staging reference")


@dataclass(frozen=True, slots=True)
class PdfExportOperationRequest:
    """Path-free authority transferred for one deterministic PDF export."""

    plan_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    result_handle: int = field(repr=False)
    output_object_id: UUID

    def __post_init__(self) -> None:
        handles = (self.plan_handle, self.output_handle, self.result_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
        ):
            raise ValueError("validated PDF-export parameters are unavailable")


class PdfExportAdapter(Protocol):
    """Injected worker adapter that consumes every transferred export authority."""

    def discard_handles(
        self, context: OperationContext, request: PdfExportOperationRequest
    ) -> None:
        """Consume transfer handles when terminal state prevents dispatch."""

    def export(
        self,
        context: OperationContext,
        request: PdfExportOperationRequest,
    ) -> CaseObjectReference:
        """Assemble, verify, report, and copy one bounded output candidate."""


@dataclass(frozen=True, slots=True)
class PdfExportOperationHandler:
    """Coordinate the closed ``pdf.export`` operation around one adapter."""

    adapter: PdfExportAdapter
    operation_version: int = 1

    def __post_init__(self) -> None:
        if type(self.operation_version) is not int or self.operation_version not in {1, 2}:
            raise ValueError("Unsupported PDF export operation version")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return PDF_EXPORT_CAPABILITY if self.operation_version == 1 else IMAGE_PDF_EXPORT_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _pdf_export_operation_request(context)
        total_pages = context.limits.page_count
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                0,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()

        try:
            output = self.adapter.export(context, request)
        except PdfExportError as error:
            if error.code is PdfExportErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfExportErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise

        # Successful adapters have already flushed and closed the result pipe and
        # bounded output section. A late terminal request suppresses the reference;
        # the trusted broker terminates the one-shot worker before reading either.
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_pdf_export_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                total_pages,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


def _pdf_export_operation_request(context: OperationContext) -> PdfExportOperationRequest:
    return PdfExportOperationRequest(
        plan_handle=_positive_integer_parameter(context, PDF_EXPORT_PLAN_HANDLE_PARAMETER),
        output_handle=_positive_integer_parameter(context, PDF_EXPORT_OUTPUT_HANDLE_PARAMETER),
        result_handle=_positive_integer_parameter(context, PDF_EXPORT_RESULT_HANDLE_PARAMETER),
        output_object_id=_uuid_parameter(context, PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER),
    )


def _require_pdf_export_output(
    context: OperationContext,
    request: PdfExportOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("PDF-export adapter returned an invalid staging reference")


@dataclass(frozen=True, slots=True)
class FlattenedPdfExportOperationRequest:
    """Transferred authority for one overlay-aware flattened PDF export."""

    plan_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    result_handle: int = field(repr=False)
    output_object_id: UUID

    def __post_init__(self) -> None:
        handles = (self.plan_handle, self.output_handle, self.result_handle)
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
        ):
            raise ValueError("validated flattened-PDF export parameters are unavailable")


class FlattenedPdfExportAdapter(Protocol):
    """Injected adapter consuming all transferred flattened-export authority."""

    def discard_handles(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> None:
        """Consume transfer handles when terminal state prevents dispatch."""

    def export(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> CaseObjectReference:
        """Create, verify, report, and return one flattened-PDF staging output."""


@dataclass(frozen=True, slots=True)
class FlattenedPdfExportOperationHandler:
    """Coordinate ``pdf.export.flattened`` around one injected adapter."""

    adapter: FlattenedPdfExportAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return FLATTENED_PDF_EXPORT_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _flattened_pdf_export_operation_request(context)
        total_pages = context.limits.page_count
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                0,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()

        try:
            output = self.adapter.export(context, request)
        except FlattenedPdfExportError as error:
            if error.code is FlattenedPdfExportErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is FlattenedPdfExportErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise

        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_flattened_pdf_export_output(context, request, output)
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                total_pages,
                total_pages,
                ProgressUnit.PAGES,
            )
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)


def _flattened_pdf_export_operation_request(
    context: OperationContext,
) -> FlattenedPdfExportOperationRequest:
    return FlattenedPdfExportOperationRequest(
        plan_handle=_positive_integer_parameter(
            context,
            FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
        ),
        output_handle=_positive_integer_parameter(
            context,
            FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
        ),
        result_handle=_positive_integer_parameter(
            context,
            FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        ),
        output_object_id=_uuid_parameter(
            context,
            FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
        ),
    )


def _require_flattened_pdf_export_output(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("flattened-PDF adapter returned an invalid staging reference")


@dataclass(frozen=True, slots=True)
class SearchablePdfExportOperationRequest:
    """Path-free authority for one searchable mixed-document derivative."""

    plan_handle: int = field(repr=False)
    output_handle: int = field(repr=False)
    status_handle: int = field(repr=False)
    result_handle: int = field(repr=False)
    output_object_id: UUID
    status_object_id: UUID
    status_capacity_bytes: int

    def __post_init__(self) -> None:
        handles = (
            self.plan_handle,
            self.output_handle,
            self.status_handle,
            self.result_handle,
        )
        if (
            any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or type(self.status_object_id) is not UUID
            or self.status_object_id.version != 4
            or self.output_object_id == self.status_object_id
            or type(self.status_capacity_bytes) is not int
            or not 1 <= self.status_capacity_bytes <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
        ):
            raise ValueError("validated searchable-PDF export parameters are unavailable")


class SearchablePdfExportAdapter(Protocol):
    """Injected adapter consuming all transferred searchable-export authority."""

    def discard_handles(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> None:
        """Consume transfer handles when terminal state prevents dispatch."""

    def export(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> tuple[CaseObjectReference, CaseObjectReference]:
        """Create exact PDF and page-status staging outputs in that order."""


@dataclass(frozen=True, slots=True)
class SearchablePdfExportOperationHandler:
    """Coordinate ``pdf.export.searchable`` around one injected adapter."""

    adapter: SearchablePdfExportAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return SEARCHABLE_PDF_EXPORT_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _searchable_pdf_export_operation_request(context)
        if context.cancel_requested.is_set():
            self.adapter.discard_handles(context, request)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            self.adapter.discard_handles(context, request)
            return timed_out_operation_result()

        try:
            outputs = self.adapter.export(context, request)
        except PdfExportError as error:
            if error.code is PdfExportErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfExportErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is PdfExportErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise
        except SearchablePdfExportError as error:
            if error.code is SearchablePdfExportErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise

        # The adapter closes all four transfer handles before returning. A late
        # terminal request suppresses both staging references atomically.
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        _require_searchable_pdf_export_outputs(context, request, outputs)
        return OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None)


def _searchable_pdf_export_operation_request(
    context: OperationContext,
) -> SearchablePdfExportOperationRequest:
    return SearchablePdfExportOperationRequest(
        plan_handle=_positive_integer_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
        ),
        output_handle=_positive_integer_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
        ),
        status_handle=_positive_integer_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
        ),
        result_handle=_positive_integer_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        ),
        output_object_id=_uuid_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
        ),
        status_object_id=_uuid_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
        ),
        status_capacity_bytes=_positive_integer_parameter(
            context,
            SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
        ),
    )


def _require_searchable_pdf_export_outputs(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
    outputs: tuple[CaseObjectReference, CaseObjectReference],
) -> None:
    if type(outputs) is not tuple or len(outputs) != 2:
        raise ValueError("searchable-PDF adapter returned invalid staging references")
    output, status = outputs
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.output_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
        or type(status) is not CaseObjectReference
        or status.area is not ReferenceArea.OPERATION_STAGING
        or status.object_id != request.status_object_id
        or status.operation_id != context.operation_id
        or status.content_kind != SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND
        or status.expected_byte_size is None
        or not 1 <= status.expected_byte_size <= request.status_capacity_bytes
        or status.expected_sha256 is None
    ):
        raise ValueError("searchable-PDF adapter returned invalid staging references")


def _pdf_thumbnail_render_request(context: OperationContext) -> PdfThumbnailRenderRequest:
    page_box_value = _token_parameter(context, PDF_THUMBNAIL_PAGE_BOX_PARAMETER)
    if page_box_value == PdfPageBox.CROP.value:
        page_box = PdfPageBox.CROP
    elif page_box_value == PdfPageBox.MEDIA.value:
        page_box = PdfPageBox.MEDIA
    else:
        raise ValueError("validated thumbnail page-box parameter is unavailable")

    rotation_value = _integer_parameter(
        context,
        PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
        minimum=0,
    )
    try:
        additional_rotation = PdfRenderRotation(rotation_value)
    except ValueError:
        raise ValueError("validated thumbnail rotation parameter is unavailable") from None

    return PdfThumbnailRenderRequest(
        source_handle=_integer_parameter(
            context,
            PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
            minimum=1,
        ),
        cache_handle=_integer_parameter(
            context,
            PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
            minimum=1,
        ),
        cache_object_id=_uuid_parameter(
            context,
            PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
        ),
        page_id=_uuid_parameter(context, PDF_THUMBNAIL_PAGE_ID_PARAMETER),
        source_page_index=_integer_parameter(
            context,
            PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
            minimum=0,
        ),
        maximum_width_pixels=_integer_parameter(
            context,
            PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
            minimum=1,
        ),
        maximum_height_pixels=_integer_parameter(
            context,
            PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
            minimum=1,
        ),
        page_box=page_box,
        additional_rotation=additional_rotation,
    )


def _require_pdf_thumbnail_output(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
    output: CaseObjectReference,
) -> None:
    if (
        type(output) is not CaseObjectReference
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != request.cache_object_id
        or output.operation_id != context.operation_id
        or output.content_kind != PDF_THUMBNAIL_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= context.limits.output_bytes
        or output.expected_sha256 is None
    ):
        raise ValueError("thumbnail adapter returned an invalid staging reference")


class PdfInspectionAdapter[InspectionT](Protocol):
    """Injected parser adapter that consumes source authority on every path."""

    def discard_source(self, source_handle: int) -> None:
        """Close a source handle when cancellation prevents inspection."""

    def inspect(self, context: OperationContext, source_handle: int) -> InspectionT:
        """Inspect and consume the already-brokered read-only source handle."""


class PdfInspectionResultWriter[InspectionT](Protocol):
    """Injected out-of-band writer that always consumes result authority."""

    def discard_result(self, result_handle: int) -> None:
        """Close a result handle when no report can be written."""

    def write(
        self,
        context: OperationContext,
        result_handle: int,
        inspection: InspectionT,
    ) -> None:
        """Write, flush, and close one bounded result before returning."""


@dataclass(frozen=True, slots=True)
class PdfInspectionOperationHandler[InspectionT]:
    """Coordinate the closed ``pdf.inspect`` operation around injected seams."""

    adapter: PdfInspectionAdapter[InspectionT]
    result_writer: PdfInspectionResultWriter[InspectionT]

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return PDF_INSPECT_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        source_handle = _positive_integer_parameter(
            context,
            PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
        )
        result_handle = _positive_integer_parameter(
            context,
            PDF_INSPECT_RESULT_HANDLE_PARAMETER,
        )
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.STEPS)
        )
        if context.cancel_requested.is_set():
            try:
                self.adapter.discard_source(source_handle)
            finally:
                self.result_writer.discard_result(result_handle)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        try:
            inspection = self.adapter.inspect(context, source_handle)
        except PdfInspectionError as error:
            self.result_writer.discard_result(result_handle)
            if error.code is PdfInspectionErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfInspectionErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            raise
        except BaseException:
            self.result_writer.discard_result(result_handle)
            raise
        if context.cancel_requested.is_set():
            self.result_writer.discard_result(result_handle)
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        # A successful return proves the writer flushed and closed the bounded
        # out-of-band frame before the RPC terminal success can be emitted.
        self.result_writer.write(context, result_handle, inspection)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.STEPS)
        )
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (), None)


def _positive_integer_parameter(context: OperationContext, name: str) -> int:
    return _integer_parameter(context, name, minimum=1)


def _integer_parameter(context: OperationContext, name: str, *, minimum: int) -> int:
    for parameter in context.parameters:
        if parameter.name == name:
            value = parameter.value
            if (
                parameter.value_type is ScalarValueType.INTEGER
                and type(value) is int
                and value >= minimum
            ):
                return value
            break
    raise ValueError("validated integer parameter is unavailable")


def _uuid_parameter(context: OperationContext, name: str) -> UUID:
    for parameter in context.parameters:
        if parameter.name == name:
            value = parameter.value
            if (
                parameter.value_type is ScalarValueType.UUID
                and type(value) is UUID
                and value.version == 4
            ):
                return value
            break
    raise ValueError("validated UUID parameter is unavailable")


def _token_parameter(context: OperationContext, name: str) -> str:
    for parameter in context.parameters:
        if parameter.name == name:
            value = parameter.value
            if parameter.value_type is ScalarValueType.TOKEN and type(value) is str:
                return value
            break
    raise ValueError("validated token parameter is unavailable")


@dataclass(frozen=True, slots=True)
class _UnavailablePdfInspectionSeam:
    """Fail closed until production parser and result-writer adapters are injected."""

    def discard_source(self, source_handle: int) -> None:
        del source_handle

    def inspect(self, context: OperationContext, source_handle: int) -> object:
        del context, source_handle
        raise RuntimeError("pdf inspection adapter is unavailable")

    def discard_result(self, result_handle: int) -> None:
        del result_handle

    def write(
        self,
        context: OperationContext,
        result_handle: int,
        inspection: object,
    ) -> None:
        del context, result_handle, inspection
        raise RuntimeError("pdf inspection result writer is unavailable")


_UNAVAILABLE_PDF_INSPECTION_SEAM: Final = _UnavailablePdfInspectionSeam()


class RuntimeOperationRegistry:
    """Immutable mapping from negotiated descriptors to fixed handlers."""

    def __init__(self, handlers: tuple[OperationHandler, ...] | None = None) -> None:
        selected = (
            (
                NoopOperationHandler(),
                PdfInspectionOperationHandler(
                    _UNAVAILABLE_PDF_INSPECTION_SEAM,
                    _UNAVAILABLE_PDF_INSPECTION_SEAM,
                ),
            )
            if handlers is None
            else handlers
        )
        mapping: dict[CapabilityDescriptor, OperationHandler] = {}
        for handler in selected:
            descriptor = handler.descriptor
            if descriptor in mapping:
                raise ValueError("duplicate runtime operation descriptor")
            mapping[descriptor] = handler
        self._handlers = MappingProxyType(mapping)

    @property
    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        """Return registered descriptors in deterministic order."""

        return tuple(self._handlers)

    def handler_for(self, descriptor: CapabilityDescriptor) -> OperationHandler:
        """Return an exact handler or raise a sanitized local lookup error."""

        try:
            return self._handlers[descriptor]
        except KeyError:
            raise LookupError("worker operation is not registered") from None


DEFAULT_RUNTIME_OPERATION_REGISTRY: Final = RuntimeOperationRegistry()


def failed_operation_result() -> OperationResultPayload:
    """Return the closed protocol-v1 result for a sanitized handler failure."""

    return OperationResultPayload(
        OperationOutcome.FAILED,
        (),
        ErrorPayload(
            ErrorCode.WORKER_OPERATION_FAILED,
            ErrorCategory.OPERATION,
            RetryAction.RETRY,
            False,
            (),
        ),
    )


def resource_limited_operation_result() -> OperationResultPayload:
    """Return a retryable protocol-v1 resource failure without adding wire fields."""

    return OperationResultPayload(
        OperationOutcome.FAILED,
        (),
        ErrorPayload(
            ErrorCode.WORKER_OPERATION_FAILED,
            ErrorCategory.RESOURCE_LIMIT,
            RetryAction.RETRY,
            False,
            (),
        ),
    )


def timed_out_operation_result() -> OperationResultPayload:
    """Return the restart-required result for a host-enforced deadline."""

    return OperationResultPayload(
        OperationOutcome.TIMED_OUT,
        (),
        ErrorPayload(
            ErrorCode.WORKER_OPERATION_TIMED_OUT,
            ErrorCategory.RESOURCE_LIMIT,
            RetryAction.RESTART_WORKER,
            True,
            (),
        ),
    )
