"""GUI-process-side process and transport contracts for the restricted worker."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Buffer
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    reject_client,
)
from paperworkshell.worker.protocol.framing import (
    MAX_FRAME_PAYLOAD_BYTES,
    read_frame,
    write_frame,
)
from paperworkshell.worker.protocol.models import MAX_SAFE_INTEGER, SessionCapability

MAXIMUM_WORKER_PROCESSES: Final = 256
MAXIMUM_INHERITED_HANDLES: Final = 64
MAXIMUM_BUFFERED_TRANSPORT_PAYLOADS: Final = 16
MAXIMUM_BUFFERED_TRANSPORT_WRITES: Final = 16
REQUIRED_LAUNCH_HANDLE_COUNT: Final = 3
TRANSPORT_CLOSE_SECONDS: Final = 1.0


def _require_uuid4(value: object) -> None:
    if type(value) is not UUID or value.version != 4:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )


@dataclass(frozen=True, slots=True)
class WorkerResourcePolicy:
    """OS process-tree limits selected by trusted application policy."""

    process_memory_bytes: int
    job_memory_bytes: int
    cpu_time_ms: int
    maximum_processes: int

    def __post_init__(self) -> None:
        values = (
            self.process_memory_bytes,
            self.job_memory_bytes,
            self.cpu_time_ms,
            self.maximum_processes,
        )
        if any(type(value) is not int or not 1 <= value <= MAX_SAFE_INTEGER for value in values):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        if (
            self.process_memory_bytes > self.job_memory_bytes
            or self.maximum_processes > MAXIMUM_WORKER_PROCESSES
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )


@dataclass(frozen=True, slots=True)
class WorkerLaunchRequest:
    """Path-free per-launch authority passed to a platform process launcher."""

    case_id: UUID
    session_id: UUID
    worker_instance_id: UUID
    session_capability: SessionCapability = field(repr=False)
    resource_policy: WorkerResourcePolicy

    def __post_init__(self) -> None:
        _require_uuid4(self.case_id)
        _require_uuid4(self.session_id)
        _require_uuid4(self.worker_instance_id)
        if type(self.session_capability) is not SessionCapability:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        if type(self.resource_policy) is not WorkerResourcePolicy:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )


@dataclass(frozen=True, slots=True)
class IsolationEvidence:
    """Safe facts attested by the trusted Windows launcher for one process."""

    app_container: bool
    network_denied: bool
    job_object_assigned: bool
    kill_on_job_close: bool
    case_paths_absent: bool
    minimal_environment: bool
    explicit_handle_list: bool
    shell_disabled: bool
    inherited_handle_count: int

    def __post_init__(self) -> None:
        flags = (
            self.app_container,
            self.network_denied,
            self.job_object_assigned,
            self.kill_on_job_close,
            self.case_paths_absent,
            self.minimal_environment,
            self.explicit_handle_list,
            self.shell_disabled,
        )
        if any(type(value) is not bool for value in flags):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.ISOLATION,
            )
        if (
            type(self.inherited_handle_count) is not int
            or not 0 <= self.inherited_handle_count <= MAXIMUM_INHERITED_HANDLES
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.ISOLATION,
            )

    @property
    def satisfies_required_boundary(self) -> bool:
        """Return whether every mandatory PWS-0107 launch property was attested."""

        return all(
            (
                self.app_container,
                self.network_denied,
                self.job_object_assigned,
                self.kill_on_job_close,
                self.case_paths_absent,
                self.minimal_environment,
                self.explicit_handle_list,
                self.shell_disabled,
                self.inherited_handle_count == REQUIRED_LAUNCH_HANDLE_COUNT,
            )
        )


class TransportReceiveKind(StrEnum):
    """Closed outcomes from one bounded transport receive attempt."""

    PAYLOAD = "payload"
    TIMED_OUT = "timed_out"
    CLOSED = "closed"


class BrokeredHandlePurpose(StrEnum):
    """Closed least-privilege access profiles for out-of-band worker handles."""

    READ_SOURCE = "read_source"
    WRITE_RESULT_PIPE = "write_result_pipe"
    WRITE_BOUNDED_OUTPUT_SECTION = "write_bounded_output_section"


@dataclass(frozen=True, slots=True)
class TransportReceive:
    """One path-free receive outcome; payload bytes are hidden from representations."""

    kind: TransportReceiveKind
    payload: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not TransportReceiveKind:
            reject_client(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
            )
        if self.kind is TransportReceiveKind.PAYLOAD:
            if type(self.payload) is not bytes or not self.payload:
                reject_client(
                    WorkerClientErrorCode.TRANSPORT_FAILED,
                    WorkerClientContext.TRANSPORT,
                )
        elif self.payload is not None:
            reject_client(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
            )


class BinaryReader(Protocol):
    """Blocking binary endpoint used by a concrete framed transport."""

    def read(self, size: int, /) -> bytes:
        """Return at most ``size`` bytes, or empty bytes at clean end of stream."""

    def close(self) -> None:
        """Close the endpoint idempotently."""


class BinaryWriter(Protocol):
    """Blocking binary endpoint used by a concrete framed transport."""

    def write(self, data: Buffer, /) -> int | None:
        """Write some bytes and return the count."""

    def flush(self) -> None:
        """Flush buffered data before a handle is transferred or closed."""

    def close(self) -> None:
        """Close the endpoint idempotently."""


class WorkerTransport(Protocol):
    """Length-framed byte transport; protocol decoding remains client-owned."""

    def send_payload(self, payload: bytes) -> None:
        """Send one already encoded, bounded protocol payload."""

    def receive_payload(self, timeout_seconds: float) -> TransportReceive:
        """Receive one payload, timeout, or clean-close event."""

    def close(self) -> None:
        """Close both transport directions idempotently."""


class _FramedWorkerTransport:
    """Bounded framed transport with private blocking I/O threads.

    The lifecycle coordinator only enqueues immutable outbound payloads.  A
    worker that stops draining its input therefore cannot pin that owner inside
    a synchronous pipe write before the coordinator arms a deadline.
    """

    def __init__(self, reader: BinaryReader, writer: BinaryWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._payloads: deque[bytes] = deque()
        self._outbound_payloads: deque[bytes] = deque()
        self._closed: bool = False
        self._cleanup_failed: bool = False
        self._peer_closed: bool = False
        self._failed: bool = False
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="PaperWorkShellWorkerTransport",
            daemon=True,
        )
        self._writer_thread = threading.Thread(
            target=self._write_loop,
            name="PaperWorkShellWorkerTransportWriter",
            daemon=True,
        )
        self._reader_thread.start()
        self._writer_thread.start()

    def __repr__(self) -> str:
        with self._condition:
            state = "closed" if self._closed else "open"
        return f"FramedWorkerTransport(state={state!r})"

    def send_payload(self, payload: bytes) -> None:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_FRAME_PAYLOAD_BYTES:
            reject_client(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
            )
        with self._condition:
            if self._closed or self._peer_closed or self._failed:
                reject_client(
                    WorkerClientErrorCode.TRANSPORT_FAILED,
                    WorkerClientContext.TRANSPORT,
                )
            if len(self._outbound_payloads) >= MAXIMUM_BUFFERED_TRANSPORT_WRITES:
                self._failed = True
                self._payloads.clear()
                self._outbound_payloads.clear()
                self._condition.notify_all()
                reject_client(
                    WorkerClientErrorCode.TRANSPORT_FAILED,
                    WorkerClientContext.TRANSPORT,
                )
            self._outbound_payloads.append(payload)
            self._condition.notify_all()

    def receive_payload(self, timeout_seconds: float) -> TransportReceive:
        if (
            type(timeout_seconds) not in {int, float}
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        with self._condition:
            ready = self._condition.wait_for(
                lambda: bool(self._payloads) or self._failed or self._peer_closed or self._closed,
                timeout=float(timeout_seconds),
            )
            if not ready:
                return TransportReceive(TransportReceiveKind.TIMED_OUT)
            if self._failed:
                reject_client(
                    WorkerClientErrorCode.TRANSPORT_FAILED,
                    WorkerClientContext.TRANSPORT,
                )
            if self._payloads:
                return TransportReceive(TransportReceiveKind.PAYLOAD, self._payloads.popleft())
            return TransportReceive(TransportReceiveKind.CLOSED)

    def close(self) -> None:
        failed = self._cleanup_failed
        with self._condition:
            first_close = not self._closed
            if first_close:
                self._closed = True
                self._condition.notify_all()
        if first_close:
            with self._condition:
                self._payloads.clear()
                self._outbound_payloads.clear()
        close_results = [False, False]

        def close_endpoint(index: int, endpoint: BinaryReader | BinaryWriter) -> None:
            try:
                endpoint.close()
            except Exception:
                return
            close_results[index] = True

        # Retry endpoint closure even after the transport has entered its terminal
        # state.  Windows endpoints retain failed CloseHandle authority, so a later
        # process cleanup attempt must be able to release the operating-system
        # resource.  `_cleanup_failed` remains sticky and prevents a later retry
        # from being reported as a clean lifecycle.
        close_threads = tuple(
            threading.Thread(
                target=close_endpoint,
                args=(index, endpoint),
                name="PaperWorkShellWorkerTransportCloser",
                daemon=True,
            )
            for index, endpoint in enumerate((self._reader, self._writer))
        )
        for thread in close_threads:
            thread.start()
        deadline = time.monotonic() + TRANSPORT_CLOSE_SECONDS
        for thread in close_threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        failed = (
            any(thread.is_alive() for thread in close_threads) or not all(close_results) or failed
        )
        threads = (
            self._reader_thread,
            self._writer_thread,
        )
        for thread in threads:
            if threading.current_thread() is thread:
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
            failed = thread.is_alive() or failed
        self._cleanup_failed = failed
        if failed:
            reject_client(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
            )

    def _read_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed or self._failed:
                    return
            try:
                payload = read_frame(self._reader)
            except Exception:
                self._mark_failed()
                return
            with self._condition:
                if self._closed_value():
                    return
                if payload is None:
                    self._peer_closed = True
                    self._condition.notify_all()
                    return
                if len(self._payloads) >= MAXIMUM_BUFFERED_TRANSPORT_PAYLOADS:
                    self._failed = True
                    self._payloads.clear()
                    self._condition.notify_all()
                    return
                self._payloads.append(payload)
                self._condition.notify()

    def _write_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: bool(self._outbound_payloads) or self._closed or self._failed
                )
                if self._closed or self._failed:
                    return
                payload = self._outbound_payloads.popleft()
            write_failed = False
            try:
                with self._write_lock:
                    write_frame(self._writer, payload)
                    self._writer.flush()
            except Exception:
                write_failed = True
            if write_failed:
                self._mark_failed()
                return

    def _closed_value(self) -> bool:
        return self._closed

    def _mark_failed(self) -> None:
        with self._condition:
            self._failed = True
            self._payloads.clear()
            self._outbound_payloads.clear()
            self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class WorkerTransportFactory:
    """Create the default bounded transport over two binary pipe endpoints."""

    def create(self, reader: BinaryReader, writer: BinaryWriter) -> WorkerTransport:
        """Bind framing to the endpoints without retaining a path or command."""

        return cast(WorkerTransport, _FramedWorkerTransport(reader, writer))


DEFAULT_WORKER_TRANSPORT_FACTORY: Final = WorkerTransportFactory()


def create_worker_transport(reader: BinaryReader, writer: BinaryWriter) -> WorkerTransport:
    """Create the repository-default framed worker transport."""

    return DEFAULT_WORKER_TRANSPORT_FACTORY.create(reader, writer)


class WorkerProcess(Protocol):
    """Retained process, job, isolation, and transport authority for one launch."""

    @property
    def pid(self) -> int:
        """Return the safe numeric process identifier."""

    @property
    def transport(self) -> WorkerTransport:
        """Return the process's bounded RPC transport."""

    @property
    def isolation(self) -> IsolationEvidence:
        """Return the launcher's safe isolation evidence."""

    def poll(self) -> int | None:
        """Return a numeric exit status, or ``None`` while the process is alive."""

    def wait(self, timeout_seconds: float | None) -> int | None:
        """Wait at most the supplied duration and return an exit status if observed."""

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        """Duplicate one reduced-right, non-inheritable handle into this worker."""

    def terminate_tree(self) -> None:
        """Terminate the retained job and its complete descendant tree."""

    def close(self) -> None:
        """Close transport, process, thread, job, and profile authority idempotently."""


class ProcessLauncher(Protocol):
    """Platform launcher implemented outside the lifecycle coordinator."""

    def launch(self, request: WorkerLaunchRequest) -> WorkerProcess:
        """Launch suspended, contain, bootstrap, resume, and return one worker."""
