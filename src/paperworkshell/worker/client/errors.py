"""Stable, non-disclosing failures for the GUI-safe worker client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never


class WorkerClientErrorCode(StrEnum):
    """Closed failure-code registry for client-side worker lifecycle control."""

    STATE_INVALID = "WORKER_CLIENT_STATE_INVALID"
    OWNER_THREAD_REQUIRED = "WORKER_CLIENT_OWNER_THREAD_REQUIRED"
    GENERATION_STALE = "WORKER_CLIENT_GENERATION_STALE"
    CONFIGURATION_INVALID = "WORKER_CLIENT_CONFIGURATION_INVALID"
    LAUNCH_FAILED = "WORKER_LAUNCH_FAILED"
    ISOLATION_INSUFFICIENT = "WORKER_ISOLATION_INSUFFICIENT"
    TRANSPORT_FAILED = "WORKER_TRANSPORT_FAILED"
    PROTOCOL_FAILED = "WORKER_PROTOCOL_FAILED"
    HANDSHAKE_TIMED_OUT = "WORKER_HANDSHAKE_TIMED_OUT"
    OPERATION_TIMED_OUT = "WORKER_OPERATION_TIMED_OUT"
    CANCELLATION_TIMED_OUT = "WORKER_CANCELLATION_TIMED_OUT"
    SHUTDOWN_TIMED_OUT = "WORKER_SHUTDOWN_TIMED_OUT"
    PROCESS_EXITED = "WORKER_PROCESS_EXITED"
    RESTART_REQUIRED = "WORKER_RESTART_REQUIRED"
    TERMINATION_FAILED = "WORKER_TERMINATION_FAILED"


class WorkerClientContext(StrEnum):
    """Safe locations that may be reported without retaining rejected values."""

    STATE = "client.state"
    OWNER = "client.owner"
    GENERATION = "client.generation"
    CONFIGURATION = "client.configuration"
    LAUNCH = "worker.launch"
    ISOLATION = "worker.isolation"
    TRANSPORT = "worker.transport"
    PROTOCOL = "worker.protocol"
    HANDSHAKE = "worker.handshake"
    OPERATION = "worker.operation"
    CANCELLATION = "worker.cancellation"
    SHUTDOWN = "worker.shutdown"
    PROCESS = "worker.process"


class WorkerRecoveryAction(StrEnum):
    """Trusted local recovery choices for UI/application orchestration."""

    NONE = "none"
    RETRY = "retry"
    RESTART_WORKER = "restart_worker"


_SAFE_MESSAGES: Final = MappingProxyType(
    {
        WorkerClientErrorCode.STATE_INVALID: "The worker action is unavailable in its current state.",
        WorkerClientErrorCode.OWNER_THREAD_REQUIRED: (
            "The worker client must be used by its coordinator thread."
        ),
        WorkerClientErrorCode.GENERATION_STALE: (
            "The worker event belongs to an earlier worker instance."
        ),
        WorkerClientErrorCode.CONFIGURATION_INVALID: (
            "The worker client configuration is outside the supported safe range."
        ),
        WorkerClientErrorCode.LAUNCH_FAILED: "PaperWorkShell could not start the document worker.",
        WorkerClientErrorCode.ISOLATION_INSUFFICIENT: (
            "The document worker did not start with the required isolation."
        ),
        WorkerClientErrorCode.TRANSPORT_FAILED: (
            "Communication with the document worker failed safely."
        ),
        WorkerClientErrorCode.PROTOCOL_FAILED: (
            "The document worker sent an invalid or incompatible response."
        ),
        WorkerClientErrorCode.HANDSHAKE_TIMED_OUT: (
            "The document worker did not become ready in time."
        ),
        WorkerClientErrorCode.OPERATION_TIMED_OUT: (
            "The document operation exceeded its time limit."
        ),
        WorkerClientErrorCode.CANCELLATION_TIMED_OUT: (
            "The document worker did not finish cancellation in time."
        ),
        WorkerClientErrorCode.SHUTDOWN_TIMED_OUT: (
            "The document worker did not shut down in time."
        ),
        WorkerClientErrorCode.PROCESS_EXITED: (
            "The document worker stopped unexpectedly. Restart it to continue."
        ),
        WorkerClientErrorCode.RESTART_REQUIRED: (
            "The document worker reported a fault and must be restarted."
        ),
        WorkerClientErrorCode.TERMINATION_FAILED: (
            "PaperWorkShell could not confirm that the document worker stopped."
        ),
    }
)

_RECOVERY_ACTIONS: Final = MappingProxyType(
    {
        WorkerClientErrorCode.STATE_INVALID: WorkerRecoveryAction.NONE,
        WorkerClientErrorCode.OWNER_THREAD_REQUIRED: WorkerRecoveryAction.NONE,
        WorkerClientErrorCode.GENERATION_STALE: WorkerRecoveryAction.NONE,
        WorkerClientErrorCode.CONFIGURATION_INVALID: WorkerRecoveryAction.NONE,
        WorkerClientErrorCode.LAUNCH_FAILED: WorkerRecoveryAction.RETRY,
        WorkerClientErrorCode.ISOLATION_INSUFFICIENT: WorkerRecoveryAction.NONE,
        WorkerClientErrorCode.TRANSPORT_FAILED: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.PROTOCOL_FAILED: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.HANDSHAKE_TIMED_OUT: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.OPERATION_TIMED_OUT: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.CANCELLATION_TIMED_OUT: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.SHUTDOWN_TIMED_OUT: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.PROCESS_EXITED: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.RESTART_REQUIRED: WorkerRecoveryAction.RESTART_WORKER,
        WorkerClientErrorCode.TERMINATION_FAILED: WorkerRecoveryAction.RETRY,
    }
)


@dataclass(frozen=True, slots=True)
class WorkerFailure:
    """An immutable failure snapshot containing no worker-controlled text."""

    code: WorkerClientErrorCode
    context: WorkerClientContext

    def __post_init__(self) -> None:
        if type(self.code) is not WorkerClientErrorCode:
            raise TypeError("a registered worker-client error code is required")
        if type(self.context) is not WorkerClientContext:
            raise TypeError("a registered worker-client context is required")

    @property
    def safe_message(self) -> str:
        """Return trusted, plain-language UI text selected only by the code."""

        return _SAFE_MESSAGES[self.code]

    @property
    def recovery_action(self) -> WorkerRecoveryAction:
        """Return the trusted recovery action selected only by the code."""

        return _RECOVERY_ACTIONS[self.code]


@dataclass(frozen=True, slots=True)
class WorkerClientError(RuntimeError):
    """Raised for one sanitized client misuse or lifecycle failure."""

    code: WorkerClientErrorCode
    context: WorkerClientContext

    @property
    def safe_message(self) -> str:
        return _SAFE_MESSAGES[self.code]

    @property
    def recovery_action(self) -> WorkerRecoveryAction:
        return _RECOVERY_ACTIONS[self.code]

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context.value}: {self.safe_message}"


def reject_client(
    code: WorkerClientErrorCode,
    context: WorkerClientContext,
) -> Never:
    """Raise a sanitized worker-client failure without retaining source exceptions."""

    raise WorkerClientError(code=code, context=context) from None
