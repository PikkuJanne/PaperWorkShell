"""Focused no-follow tests for worker-private temporary-storage sampling."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import paperworkshell.worker.client._windows as windows


def test_private_temp_scan_counts_only_bounded_regular_file_bytes(tmp_path: Path) -> None:
    nested = tmp_path / "operation" / "page"
    nested.mkdir(parents=True)
    (tmp_path / "source.bin").write_bytes(b"a" * 11)
    (nested / "scratch.bin").write_bytes(b"b" * 17)

    assert windows._private_temp_tree_bytes(tmp_path.resolve()) == 28


def test_private_temp_stream_size_uses_the_conservative_concurrent_bound(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "growing.bin"
    candidate.write_bytes(b"x" * 17)

    assert (
        windows._require_no_private_temp_alternate_streams(
            candidate,
            expected_size=5,
            is_directory=False,
        )
        == 17
    )
    assert (
        windows._require_no_private_temp_alternate_streams(
            candidate,
            expected_size=23,
            is_directory=False,
        )
        == 23
    )


def test_private_temp_scan_retries_a_concurrent_namespace_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def scan(_root: Path) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise windows._PrivateTempTreeChanged
        return 23

    monkeypatch.setattr(windows, "_scan_private_temp_tree_bytes", scan)

    assert windows._private_temp_tree_bytes(tmp_path.resolve()) == 23
    assert calls == 2


def test_private_temp_scan_fails_closed_after_bounded_continuous_churn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps = 0

    def scan(_root: Path) -> int:
        nonlocal calls
        calls += 1
        raise windows._PrivateTempTreeChanged

    def sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1

    monkeypatch.setattr(windows, "_scan_private_temp_tree_bytes", scan)
    monkeypatch.setattr("paperworkshell.worker.client._windows.time.sleep", sleep)

    with pytest.raises(windows.WindowsLaunchError) as raised:
        windows._private_temp_tree_bytes(tmp_path.resolve())

    assert raised.value.context == "profile.temp_measure"
    assert calls == windows._PRIVATE_TEMP_SCAN_ATTEMPTS
    assert sleeps == windows._PRIVATE_TEMP_SCAN_ATTEMPTS - 1


def test_private_temp_scan_rejects_reparse_entries_without_exposing_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = tmp_path / "PRIVATE-APPLICANT-CANARY.bin"
    canary.write_bytes(b"secret")
    original_lstat = os.lstat

    def lstat(path: str | os.PathLike[str]) -> Any:
        observed = original_lstat(path)
        if Path(path) != canary:
            return observed
        values = {
            name: getattr(observed, name)
            for name in (
                "st_mode",
                "st_ino",
                "st_dev",
                "st_nlink",
                "st_size",
            )
        }
        values["st_file_attributes"] = windows._FILE_ATTRIBUTE_REPARSE_POINT
        return SimpleNamespace(**values)

    monkeypatch.setattr(os, "lstat", lstat)

    with pytest.raises(windows.WindowsLaunchError) as raised:
        windows._private_temp_tree_bytes(tmp_path.resolve())

    assert "applicant" not in str(raised.value).casefold()
    assert "canary" not in repr(raised.value).casefold()


def test_private_temp_scan_fails_closed_at_entry_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "one.bin").write_bytes(b"1")
    (tmp_path / "two.bin").write_bytes(b"2")
    monkeypatch.setattr(windows, "_MAX_PRIVATE_TEMP_ENTRIES", 1)

    with pytest.raises(windows.WindowsLaunchError):
        windows._private_temp_tree_bytes(tmp_path.resolve())


def test_private_temp_scan_rejects_filesystem_aliases(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    alias = tmp_path / "alias.bin"
    first.write_bytes(b"bounded")
    os.link(first, alias)

    with pytest.raises(windows.WindowsLaunchError) as raised:
        windows._private_temp_tree_bytes(tmp_path.resolve())

    assert "first" not in str(raised.value).casefold()
    assert "alias" not in repr(raised.value).casefold()


@pytest.mark.parametrize("attach_to_directory", [False, True])
def test_private_temp_scan_fails_closed_for_alternate_data_streams(
    tmp_path: Path,
    attach_to_directory: bool,
) -> None:
    target = tmp_path if attach_to_directory else tmp_path / "scratch.bin"
    if not attach_to_directory:
        target.write_bytes(b"bounded")
    alternate_stream = Path(f"{target}:private-telemetry-canary")
    try:
        alternate_stream.write_bytes(b"hidden-storage")
    except OSError:
        pytest.skip("The test filesystem does not support alternate data streams.")

    with pytest.raises(windows.WindowsLaunchError) as raised:
        windows._private_temp_tree_bytes(tmp_path.resolve())

    assert "canary" not in str(raised.value).casefold()
    assert "telemetry" not in repr(raised.value).casefold()


def test_private_temp_scan_binds_path_to_retained_directory_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "bounded.bin").write_bytes(b"bounded")
    retain = windows._retain_private_temp_directory
    scandir_calls = 0
    original_scandir = os.scandir

    def mismatched_retain(
        path: Path,
    ) -> tuple[windows._OwnedHandle, windows._RetainedResourceInfo]:
        handle, info = retain(path)
        identifier = bytes([info.identity[1][0] ^ 1]) + info.identity[1][1:]
        return handle, replace(info, identity=(info.identity[0], identifier))

    def observed_scandir(path: str | os.PathLike[str]) -> Any:
        nonlocal scandir_calls
        scandir_calls += 1
        return original_scandir(path)

    monkeypatch.setattr(windows, "_retain_private_temp_directory", mismatched_retain)
    monkeypatch.setattr(os, "scandir", observed_scandir)

    with pytest.raises(windows.WindowsLaunchError):
        windows._private_temp_tree_bytes(tmp_path.resolve())

    assert scandir_calls == 0
