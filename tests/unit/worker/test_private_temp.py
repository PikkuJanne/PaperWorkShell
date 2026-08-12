"""Focused contract tests for worker-private temporary storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperworkshell.worker.private_temp import (
    WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES,
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)


def test_capacity_preflight_accepts_exact_boundary_and_rejects_one_byte_less(
    tmp_path: Path,
) -> None:
    required = 4096
    reserve = 512
    exact = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: required + reserve,
        minimum_free_space_reserve_bytes=reserve,
    )
    exact.require_capacity(tmp_path, required)

    insufficient = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: required + reserve - 1,
        minimum_free_space_reserve_bytes=reserve,
    )
    with pytest.raises(WorkerPrivateTempStorageError) as caught:
        insufficient.require_capacity(tmp_path, required)

    assert caught.value.args == ()


@pytest.mark.parametrize("available", (-1, True, "4096"))
def test_capacity_preflight_rejects_invalid_probe_results_without_details(
    tmp_path: Path,
    available: object,
) -> None:
    storage = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: available,  # type: ignore[arg-type,return-value]
        minimum_free_space_reserve_bytes=0,
    )

    with pytest.raises(WorkerPrivateTempStorageError) as caught:
        storage.require_capacity(tmp_path, 1)

    assert caught.value.args == ()


def test_named_file_factory_cleans_up_when_stream_adoption_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: 1,
        minimum_free_space_reserve_bytes=0,
    )

    def fail_adoption(*_args: object, **_kwargs: object) -> None:
        raise OSError("sensitive native detail")

    monkeypatch.setattr("paperworkshell.worker.private_temp.os.fdopen", fail_adoption)

    with pytest.raises(WorkerPrivateTempStorageError) as caught:
        storage.open_named(tmp_path, suffix=".pdf")

    assert caught.value.args == ()
    assert tuple(tmp_path.iterdir()) == ()


def test_default_reserve_is_the_approved_nonzero_volume_floor() -> None:
    storage = OsWorkerPrivateTempStorage(available_bytes_probe=lambda _root: 0)

    assert (
        storage.minimum_free_space_reserve_bytes
        == WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES
        == 256 * 1024 * 1024
    )
