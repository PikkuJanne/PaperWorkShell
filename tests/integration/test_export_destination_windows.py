"""Live Windows evidence for the PWS-0112 external destination transaction."""

from __future__ import annotations

import ctypes
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from paperworkshell.storage import export_destination as destination_module
from paperworkshell.storage._windows import WindowsHandle, rename_open_file_no_replace_at
from paperworkshell.storage.export_destination import (
    MAX_EXPORT_DESTINATION_CHUNK_BYTES,
    ExportCommitState,
    ExportDestinationDependencies,
    ExportDestinationError,
    ExportDestinationErrorCode,
    ExportDestinationPhase,
    ExportDestinationResult,
    WindowsExportDestination,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0112 targets the supported Windows storage boundary"
)


def _never_cancel() -> bool:
    return False


def _commit(
    destination: Path,
    payload: bytes,
    *,
    dependencies: ExportDestinationDependencies | None = None,
    cancellation: object = _never_cancel,
) -> ExportDestinationResult:
    assert callable(cancellation)
    return WindowsExportDestination(dependencies).commit(
        destination,
        BytesIO(payload),
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        cancellation=cancellation,
    )


def _stages(parent: Path) -> tuple[Path, ...]:
    return tuple(parent.glob(".*.pws-export.tmp"))


@pytest.mark.parametrize("available_delta, succeeds", [(0, True), (-1, False)])
def test_free_space_preflight_enforces_output_plus_reserve_boundary(
    tmp_path: Path,
    available_delta: int,
    succeeds: bool,
) -> None:
    output_bytes = 4096
    reserve_bytes = 8192
    observed = output_bytes + reserve_bytes + available_delta
    destination = tmp_path / "capacity-boundary.pdf"
    selected = WindowsExportDestination(
        ExportDestinationDependencies(available_space=lambda _parent: observed),
        minimum_free_space_reserve_bytes=reserve_bytes,
    )

    if succeeds:
        selected.require_capacity(destination, estimated_byte_size=output_bytes)
    else:
        with pytest.raises(ExportDestinationError) as raised:
            selected.require_capacity(destination, estimated_byte_size=output_bytes)
        assert raised.value.code is ExportDestinationErrorCode.DESTINATION_FULL
        assert raised.value.context == "export.capacity"
    assert not destination.exists()
    assert _stages(tmp_path) == ()


def test_real_commit_remains_durable_after_every_retained_handle_closes(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "verified-packet.pdf"
    payload = b"%PDF-synthetic-verified-export\n" * 8192

    result = _commit(destination, payload)

    assert result.state is ExportCommitState.COMMITTED
    assert result.byte_size == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert destination.is_file()
    assert destination.read_bytes() == payload
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == result.sha256
    assert _stages(tmp_path) == ()


def test_committed_requires_destination_name_reopen_read_and_close(tmp_path: Path) -> None:
    destination = tmp_path / "reopened-before-success.pdf"
    payload = b"independent-destination-reopen" * 4096
    events: list[str] = []
    reopened_handles: list[WindowsHandle] = []

    def open_committed(path: Path) -> WindowsHandle:
        assert path == destination
        events.append("open")
        handle = destination_module._open_committed_destination(path)
        reopened_handles.append(handle)
        return handle

    def read_committed(handle: WindowsHandle, count: int) -> bytes:
        events.append("read")
        return destination_module._read_committed_chunk(handle, count)

    def close_committed(handle: WindowsHandle) -> None:
        events.append("close")
        destination_module._close_committed_destination(handle)

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(
            open_committed=open_committed,
            read_committed=read_committed,
            close_committed=close_committed,
        ),
    )

    assert result.state is ExportCommitState.COMMITTED
    assert events[0] == "open"
    assert "read" in events
    assert events[-1] == "close"
    assert len(reopened_handles) == 1
    assert reopened_handles[0].value == 0
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_tampered_reopen_read_cannot_return_committed(tmp_path: Path) -> None:
    destination = tmp_path / "tampered-reopen-read.pdf"
    payload = b"verified-committed-payload" * 512
    first = True

    def tamper_first_read(handle: WindowsHandle, count: int) -> bytes:
        nonlocal first
        chunk = destination_module._read_committed_chunk(handle, count)
        if first:
            first = False
            return bytes((chunk[0] ^ 1,)) + chunk[1:]
        return chunk

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(read_committed=tamper_first_read),
    )

    assert result.state is ExportCommitState.UNKNOWN
    assert result.recovered_outcome
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_mismatched_reopen_identity_cannot_return_committed(tmp_path: Path) -> None:
    destination = tmp_path / "mismatched-reopen.pdf"
    payload = b"committed-identity-must-match"
    unrelated = tmp_path / "unrelated.pdf"
    unrelated.write_bytes(b"unrelated-sentinel")

    def open_unrelated(_path: Path) -> WindowsHandle:
        return destination_module._open_committed_destination(unrelated)

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(open_committed=open_unrelated),
    )

    assert result.state is ExportCommitState.UNKNOWN
    assert destination.read_bytes() == payload
    assert unrelated.read_bytes() == b"unrelated-sentinel"
    assert _stages(tmp_path) == ()


def test_indeterminate_reopen_close_preserves_final_and_returns_unknown(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "reopen-close-uncertain.pdf"
    payload = b"preserve-after-close-uncertainty"
    closed_handles: list[WindowsHandle] = []

    def close_then_fail(handle: WindowsHandle) -> None:
        destination_module._close_committed_destination(handle)
        closed_handles.append(handle)
        raise OSError(5, "raw-private-close-canary")

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(close_committed=close_then_fail),
    )

    assert result.state is ExportCommitState.UNKNOWN
    assert result.recovered_outcome
    assert len(closed_handles) == 1
    assert closed_handles[0].value == 0
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_authenticated_parent_handle_blocks_directory_name_swap(tmp_path: Path) -> None:
    parent = tmp_path / "exports"
    moved = tmp_path / "moved-exports"
    parent.mkdir()
    attempted = False

    def attempt_parent_swap(phase: ExportDestinationPhase) -> None:
        nonlocal attempted
        if phase is ExportDestinationPhase.BEFORE_STAGE_CREATE and not attempted:
            attempted = True
            with pytest.raises(OSError):
                parent.rename(moved)

    destination = parent / "pinned-parent.pdf"
    result = _commit(
        destination,
        b"parent-remained-pinned",
        dependencies=ExportDestinationDependencies(checkpoint=attempt_parent_swap),
    )

    assert attempted
    assert result.state is ExportCommitState.COMMITTED
    assert destination.read_bytes() == b"parent-remained-pinned"
    assert not moved.exists()


def test_source_is_requested_only_in_fixed_bounded_chunks(tmp_path: Path) -> None:
    payload = b"B" * (MAX_EXPORT_DESTINATION_CHUNK_BYTES * 2 + 17)

    class RecordingSource:
        def __init__(self) -> None:
            self._source = BytesIO(payload)
            self.requests: list[int] = []

        def read(self, size: int, /) -> bytes:
            self.requests.append(size)
            return self._source.read(size)

    source = RecordingSource()
    destination = tmp_path / "bounded-source.pdf"
    result = WindowsExportDestination().commit(
        destination,
        source,
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        cancellation=_never_cancel,
    )

    assert result.state is ExportCommitState.COMMITTED
    assert source.requests == [
        MAX_EXPORT_DESTINATION_CHUNK_BYTES,
        MAX_EXPORT_DESTINATION_CHUNK_BYTES,
        17,
        1,
    ]
    assert destination.stat().st_size == len(payload)


def test_existing_destination_is_never_opened_for_replacement_or_changed(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "existing.pdf"
    previous = b"previous-verified-export"
    destination.write_bytes(previous)
    source = BytesIO(b"new-export")

    result = WindowsExportDestination().commit(
        destination,
        source,
        expected_byte_size=10,
        expected_sha256=hashlib.sha256(b"new-export").hexdigest(),
        cancellation=_never_cancel,
    )

    assert result.state is ExportCommitState.DESTINATION_EXISTS
    assert destination.read_bytes() == previous
    assert source.tell() == 0
    assert _stages(tmp_path) == ()


def test_stage_name_collision_never_deletes_the_preexisting_object(tmp_path: Path) -> None:
    identifier = UUID("a0000000-0000-4000-8000-000000000012")
    collision = tmp_path / f".{identifier}.pws-export.tmp"
    collision.write_bytes(b"preexisting-stage-name-sentinel")

    with pytest.raises(ExportDestinationError) as raised:
        _commit(
            tmp_path / "collision.pdf",
            b"new-export",
            dependencies=ExportDestinationDependencies(id_factory=lambda: identifier),
        )

    assert raised.value.code is ExportDestinationErrorCode.STAGE_CREATE_FAILED
    assert collision.read_bytes() == b"preexisting-stage-name-sentinel"
    assert not (tmp_path / "collision.pdf").exists()


def test_failed_stage_authentication_closes_and_deletes_the_created_identity(
    tmp_path: Path,
) -> None:
    alternate_id = UUID("a0000000-0000-4000-8000-000000000013")
    alternate = tmp_path / f".{alternate_id}.pws-export.tmp"

    def create_wrong_identity(_requested: Path) -> WindowsHandle:
        return destination_module._create_exclusive_stage(alternate)

    with pytest.raises(ExportDestinationError) as raised:
        _commit(
            tmp_path / "wrong-stage.pdf",
            b"never-written",
            dependencies=ExportDestinationDependencies(create_stage=create_wrong_identity),
        )

    assert raised.value.code is ExportDestinationErrorCode.DESTINATION_UNSAFE
    assert not alternate.exists()
    assert not (tmp_path / "wrong-stage.pdf").exists()
    assert _stages(tmp_path) == ()


def test_short_native_writes_are_completed_before_flush_and_commit(tmp_path: Path) -> None:
    destination = tmp_path / "short-writes.pdf"
    payload = b"bounded-short-write" * 4096

    def write_short(stage: WindowsHandle, remaining: bytes) -> int:
        return destination_module._write_stage_once(stage, remaining[:7])

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(write_stage=write_short),
    )

    assert result.state is ExportCommitState.COMMITTED
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_disk_full_after_prefix_removes_only_the_exact_stage(tmp_path: Path) -> None:
    destination = tmp_path / "disk-full.pdf"
    sibling = tmp_path / "unrelated.bin"
    sibling.write_bytes(b"preserve-me")
    writes = 0

    def fill_after_prefix(stage: WindowsHandle, remaining: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return destination_module._write_stage_once(stage, remaining[:11])
        raise ctypes.WinError(112)

    with pytest.raises(ExportDestinationError) as raised:
        _commit(
            destination,
            b"export-that-does-not-fit" * 64,
            dependencies=ExportDestinationDependencies(write_stage=fill_after_prefix),
        )

    assert raised.value.code is ExportDestinationErrorCode.DESTINATION_FULL
    assert not destination.exists()
    assert sibling.read_bytes() == b"preserve-me"
    assert _stages(tmp_path) == ()


def test_flush_failure_and_readback_corruption_prevent_commit(tmp_path: Path) -> None:
    flush_destination = tmp_path / "flush-failure.pdf"

    def fail_flush(_stage: WindowsHandle) -> None:
        raise OSError(12345, "raw-private-flush-message")

    with pytest.raises(ExportDestinationError) as flush_error:
        _commit(
            flush_destination,
            b"flush-me",
            dependencies=ExportDestinationDependencies(flush_stage=fail_flush),
        )
    assert flush_error.value.code is ExportDestinationErrorCode.FLUSH_FAILED
    assert not flush_destination.exists()
    assert _stages(tmp_path) == ()

    corrupt_destination = tmp_path / "corrupt-readback.pdf"

    def corrupt_after_flush(stage: WindowsHandle) -> None:
        destination_module._flush_stage(stage)
        assert destination_module._write_stage_once(stage, b"!") == 1
        destination_module._flush_stage(stage)

    with pytest.raises(ExportDestinationError) as corrupt_error:
        _commit(
            corrupt_destination,
            b"verified-before-readback",
            dependencies=ExportDestinationDependencies(flush_stage=corrupt_after_flush),
        )
    assert corrupt_error.value.code is ExportDestinationErrorCode.VERIFY_FAILED
    assert not corrupt_destination.exists()
    assert _stages(tmp_path) == ()


def test_cancellation_before_write_cleans_stage_without_creating_final(tmp_path: Path) -> None:
    destination = tmp_path / "cancelled.pdf"

    with pytest.raises(ExportDestinationError) as raised:
        _commit(destination, b"cancelled-export", cancellation=lambda: True)

    assert raised.value.code is ExportDestinationErrorCode.CANCELLED
    assert not destination.exists()
    assert _stages(tmp_path) == ()


def test_interrupt_after_native_rename_is_resolved_as_committed(tmp_path: Path) -> None:
    destination = tmp_path / "interrupted-after-rename.pdf"
    payload = b"commit-wins-over-late-interruption"

    def rename_then_interrupt(
        stage: WindowsHandle,
        parent: WindowsHandle,
        name: str,
    ) -> tuple[bool, int]:
        outcome = rename_open_file_no_replace_at(stage, parent, destination_name=name)
        assert outcome == (True, 0)
        raise KeyboardInterrupt()

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(rename_stage=rename_then_interrupt),
    )

    assert result.state is ExportCommitState.COMMITTED
    assert result.recovered_outcome
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_base_exception_during_post_rename_resolution_preserves_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "post-rename-resolution-interrupted.pdf"
    payload = b"indeterminate-resolution-must-preserve-commit"
    renamed = False
    real_refresh = WindowsHandle.refresh_info

    def rename_and_arm_interrupt(
        stage: WindowsHandle,
        parent: WindowsHandle,
        name: str,
    ) -> tuple[bool, int]:
        nonlocal renamed
        outcome = rename_open_file_no_replace_at(stage, parent, destination_name=name)
        renamed = outcome[0]
        return outcome

    def interrupt_after_rename(handle: WindowsHandle) -> object:
        if renamed:
            raise KeyboardInterrupt
        return real_refresh(handle)

    monkeypatch.setattr(WindowsHandle, "refresh_info", interrupt_after_rename)
    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(rename_stage=rename_and_arm_interrupt),
    )

    assert renamed
    assert result.state is ExportCommitState.UNKNOWN
    assert result.recovered_outcome
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_late_cancellation_after_native_commit_is_not_observed(tmp_path: Path) -> None:
    destination = tmp_path / "late-cancel.pdf"
    payload = b"late-cancellation-cannot-delete-commit"
    renamed = False
    cancellation_calls = 0

    def cancellation() -> bool:
        nonlocal cancellation_calls
        cancellation_calls += 1
        return renamed

    def rename_and_arm_cancel(
        stage: WindowsHandle,
        parent: WindowsHandle,
        name: str,
    ) -> tuple[bool, int]:
        nonlocal renamed
        outcome = rename_open_file_no_replace_at(stage, parent, destination_name=name)
        renamed = outcome[0]
        return outcome

    result = _commit(
        destination,
        payload,
        dependencies=ExportDestinationDependencies(rename_stage=rename_and_arm_cancel),
        cancellation=cancellation,
    )

    assert cancellation_calls > 0
    assert renamed
    assert result.state is ExportCommitState.COMMITTED
    assert destination.read_bytes() == payload
    assert _stages(tmp_path) == ()


def test_interrupt_before_rename_resolves_not_committed_and_cleans_stage(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "interrupted-before-rename.pdf"

    def interrupt_before_rename(
        _stage: WindowsHandle,
        _parent: WindowsHandle,
        _name: str,
    ) -> tuple[bool, int]:
        raise KeyboardInterrupt()

    result = _commit(
        destination,
        b"never-committed",
        dependencies=ExportDestinationDependencies(rename_stage=interrupt_before_rename),
    )

    assert result.state is ExportCommitState.NOT_COMMITTED
    assert result.recovered_outcome
    assert not destination.exists()
    assert _stages(tmp_path) == ()


def test_cleanup_fault_is_reported_without_touching_any_sibling(tmp_path: Path) -> None:
    destination = tmp_path / "cleanup-fault.pdf"
    sibling = tmp_path / "existing-sibling.bin"
    sibling.write_bytes(b"preserve-sibling")

    def interrupt_before_rename(
        _stage: WindowsHandle,
        _parent: WindowsHandle,
        _name: str,
    ) -> tuple[bool, int]:
        raise KeyboardInterrupt()

    def fail_exact_deletion(_stage: WindowsHandle, _enabled: bool) -> None:
        raise OSError(5, "raw-cleanup-canary")

    with pytest.raises(ExportDestinationError) as raised:
        _commit(
            destination,
            b"cleanup-fault-payload",
            dependencies=ExportDestinationDependencies(
                rename_stage=interrupt_before_rename,
                set_deletion=fail_exact_deletion,
            ),
        )

    assert raised.value.code is ExportDestinationErrorCode.STAGE_CLEANUP_FAILED
    assert not destination.exists()
    assert sibling.read_bytes() == b"preserve-sibling"
    assert len(_stages(tmp_path)) == 1


def test_unknown_rename_identity_is_quarantined_instead_of_deleted(tmp_path: Path) -> None:
    destination = tmp_path / "requested.pdf"
    quarantine_name = ".quarantined-export-stage.tmp"

    def rename_somewhere_unexpected(
        stage: WindowsHandle,
        parent: WindowsHandle,
        _name: str,
    ) -> tuple[bool, int]:
        outcome = rename_open_file_no_replace_at(
            stage,
            parent,
            destination_name=quarantine_name,
        )
        assert outcome == (True, 0)
        return False, 5

    result = _commit(
        destination,
        b"indeterminate-export",
        dependencies=ExportDestinationDependencies(rename_stage=rename_somewhere_unexpected),
    )

    assert result.state is ExportCommitState.UNKNOWN
    assert not destination.exists()
    assert (tmp_path / quarantine_name).read_bytes() == b"indeterminate-export"
    assert _stages(tmp_path) == ()


def test_two_writers_racing_for_one_final_have_exactly_one_winner(tmp_path: Path) -> None:
    destination = tmp_path / "raced.pdf"
    payloads = (b"first-complete-export" * 128, b"second-complete-export" * 128)
    barrier = Barrier(2)

    def synchronized_rename(
        stage: WindowsHandle,
        parent: WindowsHandle,
        name: str,
    ) -> tuple[bool, int]:
        barrier.wait(timeout=10)
        return rename_open_file_no_replace_at(stage, parent, destination_name=name)

    dependencies = ExportDestinationDependencies(rename_stage=synchronized_rename)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_commit, destination, payload, dependencies=dependencies)
            for payload in payloads
        ]
        results = [future.result(timeout=30) for future in futures]

    assert [result.state for result in results].count(ExportCommitState.COMMITTED) == 1
    assert [result.state for result in results].count(ExportCommitState.DESTINATION_EXISTS) == 1
    assert destination.read_bytes() in payloads
    assert _stages(tmp_path) == ()
