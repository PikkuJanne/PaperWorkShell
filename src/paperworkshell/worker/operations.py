"""Fixed, explicit operation handlers used by the document-worker host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID

from paperworkshell.domain.pdf_inspection import PdfInspectionError, PdfInspectionErrorCode
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
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
    limits: ResourceLimits
    cancel_requested: Event
    report_progress: Callable[[OperationProgressPayload], None]
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
    for parameter in context.parameters:
        if parameter.name == name:
            value = parameter.value
            if parameter.value_type is ScalarValueType.INTEGER and type(value) is int and value > 0:
                return value
            break
    raise ValueError("validated brokered handle parameter is unavailable")


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
