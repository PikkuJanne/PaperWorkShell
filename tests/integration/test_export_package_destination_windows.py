"""Windows integration evidence for PWS-0407 multi-artifact publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID

import pytest

import paperworkshell.storage.export_package_destination as package_destination_module
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationDependencies,
    WindowsExportDestination,
    _rename_stage_no_replace,
)
from paperworkshell.storage.export_package_destination import (
    ExportPackageDestinationDependencies,
    ExportPackageDestinationError,
    ExportPackageDestinationErrorCode,
    ExportPackageDestinationPhase,
    WindowsExportPackageTransaction,
)


class _BytesSource:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self._position = 0

    def read(self, size: int, /) -> bytes:
        selected = self._value[self._position : self._position + size]
        self._position += len(selected)
        return selected


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ids() -> object:
    values = iter(UUID(f"10000000-0000-4000-8000-{index:012d}") for index in range(1, 200))
    return lambda: next(values)


def _transaction(
    parent: Path,
    *,
    artifact_destination: WindowsExportDestination | None = None,
    dependencies: ExportPackageDestinationDependencies | None = None,
    cancellation: object = lambda: False,
    deadline: object = lambda: False,
) -> WindowsExportPackageTransaction:
    return WindowsExportPackageTransaction(
        parent,
        artifact_destination or WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        maximum_staged_bytes=8 * 1024 * 1024,
        cancellation=cancellation,  # type: ignore[arg-type]
        deadline=deadline,  # type: ignore[arg-type]
        dependencies=dependencies or ExportPackageDestinationDependencies(id_factory=_ids()),  # type: ignore[arg-type]
    )


def _stage(
    transaction: WindowsExportPackageTransaction,
    key: str,
    position: int,
    name: str | None,
    payload: bytes,
) -> None:
    transaction.stage(
        key,
        position,
        name,
        _BytesSource(payload),
        expected_byte_size=len(payload),
        expected_sha256=_digest(payload),
    )


def test_every_pdf_is_staged_before_deterministic_per_artifact_commit(tmp_path: Path) -> None:
    first = b"%PDF-1.7\n% synthetic first\n%%EOF\n"
    second = b"%PDF-1.7\n% synthetic second\n%%EOF\n"

    with _transaction(tmp_path) as transaction:
        _stage(transaction, "application-form", 1, "application-form.pdf", first)
        _stage(transaction, "identity-evidence", 2, "identity-evidence.pdf", second)

        assert not (tmp_path / "application-form.pdf").exists()
        assert not (tmp_path / "identity-evidence.pdf").exists()
        assert transaction.staged_byte_size == len(first) + len(second)
        assert len(tuple(tmp_path.glob(".*.pws-package-stage.tmp"))) == 2

        result = transaction.commit()

    assert result.committed
    assert not result.partial
    assert not result.uncertain
    assert tuple(item.state for item in result.artifacts) == (
        ExportCommitState.COMMITTED,
        ExportCommitState.COMMITTED,
    )
    assert (tmp_path / "application-form.pdf").read_bytes() == first
    assert (tmp_path / "identity-evidence.pdf").read_bytes() == second
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_preexisting_verified_package_member_blocks_every_new_commit(tmp_path: Path) -> None:
    prior = b"%PDF-1.7\n% prior verified output\n%%EOF\n"
    (tmp_path / "application-form.pdf").write_bytes(prior)

    with _transaction(tmp_path) as transaction:
        _stage(transaction, "application-form", 1, "application-form.pdf", b"new-one")
        _stage(transaction, "identity-evidence", 2, "identity-evidence.pdf", b"new-two")
        result = transaction.commit()

    assert tuple(item.state for item in result.artifacts) == (
        ExportCommitState.DESTINATION_EXISTS,
        ExportCommitState.NOT_COMMITTED,
    )
    assert not result.committed
    assert not result.partial
    assert (tmp_path / "application-form.pdf").read_bytes() == prior
    assert not (tmp_path / "identity-evidence.pdf").exists()
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_second_rename_failure_returns_exact_partial_set_without_success(
    tmp_path: Path,
) -> None:
    calls = 0

    def rename(stage: object, parent: object, name: str) -> tuple[bool, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            return False, 5
        return _rename_stage_no_replace(stage, parent, name)  # type: ignore[arg-type]

    dependencies = ExportPackageDestinationDependencies(
        id_factory=_ids(),  # type: ignore[arg-type]
        rename_stage=rename,
    )
    with _transaction(tmp_path, dependencies=dependencies) as transaction:
        _stage(transaction, "application-form", 1, "application-form.pdf", b"one")
        _stage(transaction, "identity-evidence", 2, "identity-evidence.pdf", b"two")
        result = transaction.commit()

    assert tuple(item.state for item in result.artifacts) == (
        ExportCommitState.COMMITTED,
        ExportCommitState.NOT_COMMITTED,
    )
    assert result.partial
    assert not result.committed
    assert (tmp_path / "application-form.pdf").read_bytes() == b"one"
    assert not (tmp_path / "identity-evidence.pdf").exists()
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_partial_commit_retries_transient_cleanup_checkpoint_failure(
    tmp_path: Path,
) -> None:
    rename_calls = 0
    cleanup_calls = 0

    def rename(stage: object, parent: object, name: str) -> tuple[bool, int]:
        nonlocal rename_calls
        rename_calls += 1
        if rename_calls == 2:
            return False, 5
        return _rename_stage_no_replace(stage, parent, name)  # type: ignore[arg-type]

    def checkpoint(phase: ExportPackageDestinationPhase, position: int) -> None:
        nonlocal cleanup_calls
        if phase is ExportPackageDestinationPhase.BEFORE_CLEANUP and position == 2:
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise OSError(5, "synthetic transient cleanup failure")

    dependencies = ExportPackageDestinationDependencies(
        id_factory=_ids(),  # type: ignore[arg-type]
        checkpoint=checkpoint,
        rename_stage=rename,
    )
    with _transaction(tmp_path, dependencies=dependencies) as transaction:
        _stage(transaction, "application-form", 1, "application-form.pdf", b"one")
        _stage(transaction, "identity-evidence", 2, "identity-evidence.pdf", b"two")
        result = transaction.commit()

    assert cleanup_calls == 2
    assert tuple(item.state for item in result.artifacts) == (
        ExportCommitState.COMMITTED,
        ExportCommitState.NOT_COMMITTED,
    )
    assert result.partial
    assert (tmp_path / "application-form.pdf").read_bytes() == b"one"
    assert not (tmp_path / "identity-evidence.pdf").exists()
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_partial_commit_surfaces_persistent_cleanup_checkpoint_failure_without_leak(
    tmp_path: Path,
) -> None:
    rename_calls = 0

    def rename(stage: object, parent: object, name: str) -> tuple[bool, int]:
        nonlocal rename_calls
        rename_calls += 1
        if rename_calls == 2:
            return False, 5
        return _rename_stage_no_replace(stage, parent, name)  # type: ignore[arg-type]

    def checkpoint(phase: ExportPackageDestinationPhase, position: int) -> None:
        if phase is ExportPackageDestinationPhase.BEFORE_CLEANUP and position == 2:
            raise OSError(5, "C:/PRIVATE/synthetic persistent cleanup failure")

    dependencies = ExportPackageDestinationDependencies(
        id_factory=_ids(),  # type: ignore[arg-type]
        checkpoint=checkpoint,
        rename_stage=rename,
    )
    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path, dependencies=dependencies) as transaction:
            _stage(transaction, "application-form", 1, "application-form.pdf", b"one")
            _stage(transaction, "identity-evidence", 2, "identity-evidence.pdf", b"two")
            transaction.commit()

    assert raised.value.code is ExportPackageDestinationErrorCode.CLEANUP_FAILED
    assert "PRIVATE" not in str(raised.value)
    assert (tmp_path / "application-form.pdf").read_bytes() == b"one"
    assert not (tmp_path / "identity-evidence.pdf").exists()
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_final_reopen_failure_is_unknown_and_never_package_success(tmp_path: Path) -> None:
    dependencies = ExportPackageDestinationDependencies(
        id_factory=_ids(),  # type: ignore[arg-type]
        verify_final=lambda _path, _identity, _size, _sha256: False,
    )
    with _transaction(tmp_path, dependencies=dependencies) as transaction:
        _stage(transaction, "combined", 1, "packet.pdf", b"verified-before-commit")
        result = transaction.commit()

    assert result.uncertain
    assert not result.committed
    assert result.artifacts[0].state is ExportCommitState.UNKNOWN
    assert (tmp_path / "packet.pdf").read_bytes() == b"verified-before-commit"


def test_disk_full_during_stage_cleans_private_prefix_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    completed = False

    def disk_full(_handle: object, _payload: bytes) -> int:
        nonlocal completed
        if completed:
            raise OSError(112, "synthetic disk full")
        completed = True
        return 1

    destination = WindowsExportDestination(
        ExportDestinationDependencies(write_stage=disk_full),
        minimum_free_space_reserve_bytes=0,
    )
    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path, artifact_destination=destination) as transaction:
            _stage(transaction, "combined", 1, "packet.pdf", b"bounded-stage")

    assert raised.value.code is ExportPackageDestinationErrorCode.DESTINATION_FULL
    assert not (tmp_path / "packet.pdf").exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_permission_denial_before_stage_is_sanitized_and_writes_nothing(tmp_path: Path) -> None:
    def checkpoint(phase: ExportPackageDestinationPhase, _position: int) -> None:
        if phase is ExportPackageDestinationPhase.BEFORE_STAGE:
            raise PermissionError("C:/PRIVATE/destination")

    dependencies = ExportPackageDestinationDependencies(
        id_factory=_ids(),  # type: ignore[arg-type]
        checkpoint=checkpoint,
    )
    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path, dependencies=dependencies) as transaction:
            _stage(transaction, "combined", 1, "packet.pdf", b"payload")

    assert raised.value.code is ExportPackageDestinationErrorCode.DESTINATION_DENIED
    assert "PRIVATE" not in str(raised.value)
    assert tuple(tmp_path.iterdir()) == ()


def test_cancellation_before_commit_removes_all_stages_and_keeps_finals_absent(
    tmp_path: Path,
) -> None:
    cancelled = False

    def cancellation() -> bool:
        return cancelled

    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path, cancellation=cancellation) as transaction:
            _stage(transaction, "first", 1, "first.pdf", b"first")
            _stage(transaction, "second", 2, "second.pdf", b"second")
            cancelled = True
            transaction.commit()

    assert raised.value.code is ExportPackageDestinationErrorCode.CANCELLED
    assert not (tmp_path / "first.pdf").exists()
    assert not (tmp_path / "second.pdf").exists()
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_internal_members_can_be_streamed_then_removed_before_archive_commit(
    tmp_path: Path,
) -> None:
    with _transaction(tmp_path) as transaction:
        _stage(transaction, "member-one", 1, None, b"member-one-bytes")
        _stage(transaction, "member-two", 2, None, b"member-two-bytes")
        with transaction.open_staged_readers(("member-one", "member-two")) as readers:
            assert tuple(reader.read() for reader in readers) == (
                b"member-one-bytes",
                b"member-two-bytes",
            )
        _stage(transaction, "archive", 3, "packet.zip", b"verified-archive")
        transaction.discard_internal()
        assert transaction.staged_keys == ("archive",)
        result = transaction.commit()

    assert result.committed
    assert result.artifacts[0].position == 1
    assert (tmp_path / "packet.zip").read_bytes() == b"verified-archive"
    assert tuple(tmp_path.glob(".*.pws-package-stage.tmp")) == ()


def test_generated_archive_is_written_directly_to_retained_destination_stage(
    tmp_path: Path,
) -> None:
    payload = b"PK\x03\x04synthetic-verified-archive"

    def writer(stream: BinaryIO) -> tuple[int, str]:
        written = stream.write(payload)
        assert written == len(payload)
        stream.seek(0)
        assert stream.read() == payload
        return len(payload), _digest(payload)

    with _transaction(tmp_path) as transaction:
        transaction.stage_generated("archive", 1, "packet.zip", writer)
        assert transaction.staged_byte_size == len(payload)
        assert len(tuple(tmp_path.glob(".*.pws-export.tmp"))) == 1
        result = transaction.commit()

    assert result.committed
    assert (tmp_path / "packet.zip").read_bytes() == payload
    assert tuple(tmp_path.glob(".*.pws-export.tmp")) == ()


def test_generated_archive_report_mismatch_cleans_stage_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    payload = b"untrusted-stage"

    def writer(stream: BinaryIO) -> tuple[int, str]:
        stream.write(payload)
        return len(payload), _digest(b"different")

    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path) as transaction:
            transaction.stage_generated("archive", 1, "packet.zip", writer)

    assert raised.value.code is ExportPackageDestinationErrorCode.STAGE_CHANGED
    assert tuple(tmp_path.iterdir()) == ()


def test_transient_retained_stage_reopen_failure_is_cleaned_before_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open_handle = cast(
        Callable[..., object],
        vars(package_destination_module)["open_handle"],
    )
    failed = False

    def open_with_one_stage_failure(path: Path, **kwargs: object) -> object:
        nonlocal failed
        if (
            path.name.endswith(".pws-package-stage.tmp")
            and kwargs.get("delete") is True
            and not failed
        ):
            failed = True
            raise OSError(5, "C:/PRIVATE/synthetic reopen failure")
        return real_open_handle(path, **kwargs)

    monkeypatch.setattr(package_destination_module, "open_handle", open_with_one_stage_failure)
    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path) as transaction:
            _stage(transaction, "combined", 1, "packet.pdf", b"verified-stage")

    assert raised.value.code is ExportPackageDestinationErrorCode.STAGE_CHANGED
    assert "PRIVATE" not in str(raised.value)
    assert tuple(tmp_path.iterdir()) == ()


def test_retained_stage_verification_failure_cleans_open_handle_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_verification(*_args: object, **_kwargs: object) -> None:
        raise OSError(5, "C:/PRIVATE/synthetic verification failure")

    monkeypatch.setattr(
        package_destination_module,
        "_verify_retained_stage",
        fail_verification,
    )
    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(tmp_path) as transaction:
            _stage(transaction, "combined", 1, "packet.pdf", b"verified-stage")

    assert raised.value.code is ExportPackageDestinationErrorCode.STAGE_CHANGED
    assert "PRIVATE" not in str(raised.value)
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize(
    ("probe_kind", "expected_code"),
    (
        ("cancellation", ExportPackageDestinationErrorCode.CANCELLED),
        ("deadline", ExportPackageDestinationErrorCode.TIMED_OUT),
    ),
)
def test_retained_digest_poll_maps_stop_and_cleans_private_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_kind: str,
    expected_code: ExportPackageDestinationErrorCode,
) -> None:
    class _ArmedProbe:
        armed = False
        calls = 0

        def __call__(self) -> bool:
            if not self.armed:
                return False
            self.calls += 1
            return self.calls >= 2

    probe = _ArmedProbe()
    real_open_handle = cast(
        Callable[..., object],
        vars(package_destination_module)["open_handle"],
    )

    def open_and_arm(path: Path, **kwargs: object) -> object:
        opened = real_open_handle(path, **kwargs)
        if path.name.endswith(".pws-package-stage.tmp") and kwargs.get("delete") is True:
            probe.armed = True
        return opened

    monkeypatch.setattr(package_destination_module, "open_handle", open_and_arm)
    monkeypatch.setattr(package_destination_module, "MAX_EXPORT_DESTINATION_CHUNK_BYTES", 4)
    cancellation = probe if probe_kind == "cancellation" else lambda: False
    deadline = probe if probe_kind == "deadline" else lambda: False

    with pytest.raises(ExportPackageDestinationError) as raised:
        with _transaction(
            tmp_path,
            cancellation=cancellation,
            deadline=deadline,
        ) as transaction:
            _stage(transaction, "combined", 1, "packet.pdf", b"0123456789")

    assert probe.calls == 2
    assert raised.value.code is expected_code
    assert not (tmp_path / "packet.pdf").exists()
    assert tuple(tmp_path.iterdir()) == ()
