"""Fixed, explicit operation handlers used by the document-worker host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID

from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    CapabilityDescriptor,
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
)

NOOP_COOPERATION_WINDOW_SECONDS: Final = 0.100


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Trusted host context passed to one fixed operation handler."""

    operation_id: UUID
    limits: ResourceLimits
    cancel_requested: Event
    report_progress: Callable[[OperationProgressPayload], None]


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


class RuntimeOperationRegistry:
    """Immutable mapping from negotiated descriptors to fixed handlers."""

    def __init__(self, handlers: tuple[OperationHandler, ...] | None = None) -> None:
        selected = (NoopOperationHandler(),) if handlers is None else handlers
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
