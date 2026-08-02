"""Immutable, path-free snapshots for the GUI-side worker lifecycle."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    WorkerFailure,
    reject_client,
)
from paperworkshell.worker.protocol.models import (
    MAX_SAFE_INTEGER,
    CapabilityDescriptor,
    CaseObjectReference,
    ErrorCode,
    OperationOutcome,
    ProgressStage,
    ProgressUnit,
)

MAXIMUM_CLIENT_TIMEOUT_SECONDS: Final = 86_400.0


def _require_uuid4_or_none(value: object) -> None:
    if value is not None and (type(value) is not UUID or value.version != 4):
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )


def _require_uuid4(value: object) -> None:
    if type(value) is not UUID or value.version != 4:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )


class WorkerLifecyclePhase(StrEnum):
    """UI-facing lifecycle phases for one case-bound worker generation."""

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    CANCELLING = "cancelling"
    STOPPING = "stopping"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class WorkerTimeoutPolicy:
    """Trusted broker deadlines; operation wall time remains request-specific."""

    handshake_seconds: float
    cancellation_grace_seconds: float
    shutdown_grace_seconds: float

    def __post_init__(self) -> None:
        for value in (
            self.handshake_seconds,
            self.cancellation_grace_seconds,
            self.shutdown_grace_seconds,
        ):
            if (
                type(value) not in {int, float}
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 < value <= MAXIMUM_CLIENT_TIMEOUT_SECONDS
            ):
                reject_client(
                    WorkerClientErrorCode.CONFIGURATION_INVALID,
                    WorkerClientContext.CONFIGURATION,
                )


@dataclass(frozen=True, slots=True)
class WorkerProgressSnapshot:
    """The latest validated numeric progress for an active operation."""

    stage: ProgressStage
    completed_units: int
    total_units: int | None
    unit: ProgressUnit

    def __post_init__(self) -> None:
        if type(self.stage) is not ProgressStage or type(self.unit) is not ProgressUnit:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if (
            type(self.completed_units) is not int
            or not 0 <= self.completed_units <= MAX_SAFE_INTEGER
        ):
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if self.total_units is not None and (
            type(self.total_units) is not int
            or not 0 <= self.total_units <= MAX_SAFE_INTEGER
            or self.completed_units > self.total_units
        ):
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )


@dataclass(frozen=True, slots=True)
class WorkerOperationSnapshot:
    """Safe local state for the one active operation."""

    operation_id: UUID
    request_id: UUID
    operation_kind: str
    operation_version: int
    accepted: bool
    cancellation_requested: bool
    progress: WorkerProgressSnapshot | None

    def __post_init__(self) -> None:
        _require_uuid4(self.operation_id)
        _require_uuid4(self.request_id)
        if type(self.operation_kind) is not str or not self.operation_kind:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if type(self.operation_version) is not int or self.operation_version < 1:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if type(self.accepted) is not bool or type(self.cancellation_requested) is not bool:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if self.progress is not None and type(self.progress) is not WorkerProgressSnapshot:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )


@dataclass(frozen=True, slots=True)
class WorkerOperationResultSnapshot:
    """The last protocol-authoritative terminal result, with logical references only."""

    generation: int
    operation_id: UUID
    outcome: OperationOutcome
    outputs: tuple[CaseObjectReference, ...]
    error_code: ErrorCode | None

    def __post_init__(self) -> None:
        if type(self.generation) is not int or not 1 <= self.generation <= MAX_SAFE_INTEGER:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        _require_uuid4(self.operation_id)
        if type(self.outcome) is not OperationOutcome:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if type(self.outputs) is not tuple or any(
            type(output) is not CaseObjectReference for output in self.outputs
        ):
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )
        if self.error_code is not None and type(self.error_code) is not ErrorCode:
            reject_client(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
            )


@dataclass(frozen=True, slots=True)
class WorkerClientSnapshot:
    """A local-only immutable view that never queries the process or transport."""

    generation: int
    phase: WorkerLifecyclePhase
    case_id: UUID | None
    session_id: UUID | None
    worker_instance_id: UUID | None
    pid: int | None
    capabilities: tuple[CapabilityDescriptor, ...]
    active_operation: WorkerOperationSnapshot | None
    last_result: WorkerOperationResultSnapshot | None
    last_failure: WorkerFailure | None

    def __post_init__(self) -> None:
        if type(self.generation) is not int or not 0 <= self.generation <= MAX_SAFE_INTEGER:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        if type(self.phase) is not WorkerLifecyclePhase:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        for value in (self.case_id, self.session_id, self.worker_instance_id):
            _require_uuid4_or_none(value)
        if self.pid is not None and (type(self.pid) is not int or self.pid < 1):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        if type(self.capabilities) is not tuple or any(
            type(capability) is not CapabilityDescriptor for capability in self.capabilities
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        if (
            self.active_operation is not None
            and type(self.active_operation) is not WorkerOperationSnapshot
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        if (
            self.last_result is not None
            and type(self.last_result) is not WorkerOperationResultSnapshot
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
        if self.last_failure is not None and type(self.last_failure) is not WorkerFailure:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.STATE,
            )
