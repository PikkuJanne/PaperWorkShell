"""Fixed, explicit operation handlers used by the document-worker host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID

from paperworkshell.domain.pdf_inspection import PdfInspectionError, PdfInspectionErrorCode
from paperworkshell.domain.pdf_rendering import PdfPageBox, PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
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
