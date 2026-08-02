"""Pure and narrow-adapter tests for PWS-0101 workspace conventions."""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import UUID

import pytest

from paperworkshell.storage import _windows as windows_storage
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.permissions import WindowsPrivateDirectoryPermissions
from paperworkshell.storage.quotas import (
    MAX_SAFE_QUOTA_INTEGER,
    MAXIMUM_WORKSPACE_DEPTH,
    MINIMUM_WORKSPACE_DEPTH,
    MINIMUM_WORKSPACE_ENTRY_COUNT,
    QuotaArea,
    QuotaReservationBook,
    WorkspaceQuota,
    WorkspaceUsage,
    require_capacity,
)
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    WorkspaceArea,
    new_case_id,
    validate_case_id,
    validate_case_relative_path,
    validate_internal_component,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")


def _quota(**overrides: int) -> WorkspaceQuota:
    values = {
        "maximum_case_bytes": 10_000,
        "maximum_cache_bytes": 4_000,
        "maximum_temp_bytes": 3_000,
        "maximum_entries": 100,
        "maximum_depth": 10,
    }
    values.update(overrides)
    return WorkspaceQuota(**values)


def _usage(**overrides: int) -> WorkspaceUsage:
    values = {
        "case_bytes": 1_000,
        "cache_bytes": 500,
        "temp_bytes": 250,
        "entry_count": 10,
        "maximum_depth_seen": 2,
    }
    values.update(overrides)
    return WorkspaceUsage(**values)


def test_new_case_ids_are_random_canonical_uuid4_values() -> None:
    identifiers = {new_case_id() for _ in range(128)}

    assert len(identifiers) == 128
    assert all(identifier.version == 4 for identifier in identifiers)
    assert all(validate_case_id(str(identifier)) == identifier for identifier in identifiers)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "10000000-0000-1000-8000-000000000001",
        "10000000-0000-4000-7000-000000000001",
        "10000000-0000-4000-8000-000000000001.bin",
        "10000000-0000-4000-8000-000000000001/child",
        "10000000-0000-4000-8000-000000000001\\child",
        "10000000-0000-4000-8000-000000000001 ",
        "10000000-0000-4000-8000-000000000001\n",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper(),
        "{10000000-0000-4000-8000-000000000001}",
    ],
)
def test_invalid_case_ids_fail_before_path_construction(value: str) -> None:
    with pytest.raises(WorkspaceError) as raised:
        validate_case_id(value)

    assert raised.value.code is WorkspaceErrorCode.CASE_ID_INVALID
    if len(value) >= 4:
        assert value not in str(raised.value)


def test_layout_is_exact_separated_and_non_disclosing(tmp_path: Path) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.layout(CASE_ID)

    assert layout.case_root == manager.cases_root / str(CASE_ID)
    assert tuple(
        path.relative_to(layout.case_root).as_posix() for path in layout.directories[1:]
    ) == (
        "journal",
        "originals",
        "cache",
        "cache/thumbnails",
        "cache/previews",
        "temp",
    )
    assert layout.manifest == layout.case_root / "manifest.json"
    assert layout.export_history == layout.case_root / "export-history.json"
    assert len(set((*layout.directories, layout.manifest, layout.export_history))) == 9
    assert str(tmp_path) not in repr(layout)
    with pytest.raises(FrozenInstanceError):
        layout.case_id = new_case_id()  # type: ignore[misc]


def test_fixed_area_resolver_rejects_unknown_runtime_values(tmp_path: Path) -> None:
    layout = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota()).layout(CASE_ID)

    assert layout.area_path(WorkspaceArea.ORIGINALS) == layout.originals
    assert layout.area_path(WorkspaceArea.EXPORT_HISTORY) == layout.export_history
    with pytest.raises(WorkspaceError) as raised:
        layout.area_path("outside")  # type: ignore[arg-type]
    assert raised.value.code is WorkspaceErrorCode.PATH_INVALID


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "temp/../originals",
        "temp\\..\\originals",
        "/temp/item",
        "\\temp\\item",
        r"C:\temp\item",
        r"C:temp\item",
        r"\\server\share\item",
        r"\\?\C:\item",
        r"\\?\UNC\server\share\item",
        r"\\.\PhysicalDrive0",
        r"\??\C:\item",
        r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\item",
        "temp/item:stream",
        "temp/item::$DATA",
        "temp//item",
        "temp\\\\item",
        "temp/item.",
        "temp/item ",
        "temp/CON",
        "temp/nul.bin",
        "temp/COM1.txt",
        "temp/LPT³.log",
        "temp/control\x1fvalue",
        "temp/bidi\u202evalue",
        "outside/item",
        "manifest.json/item",
        "export-history.json/item",
    ],
)
def test_hostile_case_relative_paths_are_rejected_without_echo(value: str) -> None:
    with pytest.raises(WorkspaceError) as raised:
        validate_case_relative_path(value)

    assert raised.value.code in {WorkspaceErrorCode.PATH_INVALID, WorkspaceErrorCode.NAME_INVALID}
    if len(value) >= 4:
        assert value not in str(raised.value)


@pytest.mark.parametrize(
    "value",
    [
        "Uppercase",
        "café",
        "space name",
        "name.",
        "name ",
        "con",
        "con.txt",
        "lpt1.bin",
        "com¹.log",
        "item:stream",
        "item\\child",
        "item/child",
        ".hidden",
        "a" * 129,
    ],
)
def test_internal_components_use_closed_opaque_ascii_grammar(value: str) -> None:
    with pytest.raises(WorkspaceError) as raised:
        validate_internal_component(value)
    assert raised.value.code is WorkspaceErrorCode.NAME_INVALID


def test_valid_case_relative_paths_remain_lexical_prechecks() -> None:
    assert validate_case_relative_path("manifest.json") == ("manifest.json",)
    assert validate_case_relative_path("originals/10000000-0000-4000-8000-000000000001.bin") == (
        "originals",
        "10000000-0000-4000-8000-000000000001.bin",
    )
    assert validate_case_relative_path("cache/thumbnails/item-01.bin") == (
        "cache",
        "thumbnails",
        "item-01.bin",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"maximum_case_bytes": True},
        {"maximum_case_bytes": 0},
        {"maximum_cache_bytes": -1},
        {"maximum_temp_bytes": MAX_SAFE_QUOTA_INTEGER + 1},
        {"maximum_entries": 0},
        {"maximum_entries": MINIMUM_WORKSPACE_ENTRY_COUNT - 1},
        {"maximum_depth": 0},
        {"maximum_depth": MINIMUM_WORKSPACE_DEPTH - 1},
        {"maximum_depth": MAXIMUM_WORKSPACE_DEPTH + 1},
        {"maximum_case_bytes": 100, "maximum_cache_bytes": 101},
        {"maximum_case_bytes": 100, "maximum_temp_bytes": 101},
    ],
)
def test_quota_configuration_rejects_unbounded_or_inconsistent_values(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(WorkspaceError) as raised:
        _quota(**overrides)
    assert raised.value.code is WorkspaceErrorCode.QUOTA_INVALID


def test_quota_configuration_accepts_exact_structural_depth_and_entry_bounds() -> None:
    quota = _quota(
        maximum_entries=MINIMUM_WORKSPACE_ENTRY_COUNT,
        maximum_depth=MINIMUM_WORKSPACE_DEPTH,
    )
    deepest = _quota(maximum_depth=MAXIMUM_WORKSPACE_DEPTH)

    assert quota.maximum_entries == MINIMUM_WORKSPACE_ENTRY_COUNT
    assert quota.maximum_depth == MINIMUM_WORKSPACE_DEPTH
    assert deepest.maximum_depth == MAXIMUM_WORKSPACE_DEPTH


def test_quota_boundaries_accept_exact_maximum_and_reject_one_over() -> None:
    quota = _quota(
        maximum_case_bytes=2_000,
        maximum_cache_bytes=1_000,
        maximum_temp_bytes=1_000,
        maximum_entries=20,
    )
    usage = _usage(case_bytes=1_000, cache_bytes=500, entry_count=10)

    require_capacity(quota, usage, QuotaArea.CACHE, byte_count=500, entry_count=10)
    with pytest.raises(WorkspaceError) as bytes_over:
        require_capacity(quota, usage, QuotaArea.CACHE, byte_count=501, entry_count=10)
    with pytest.raises(WorkspaceError) as entries_over:
        require_capacity(quota, usage, QuotaArea.CACHE, byte_count=500, entry_count=11)
    assert bytes_over.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED
    assert entries_over.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED


def test_quota_request_rejects_bool_negative_zero_and_overflow() -> None:
    quota = _quota()
    usage = _usage()
    cases = ((True, 1), (-1, 1), (0, 0), (MAX_SAFE_QUOTA_INTEGER + 1, 1), (1, True))
    for byte_count, entry_count in cases:
        with pytest.raises(WorkspaceError) as raised:
            require_capacity(
                quota,
                usage,
                QuotaArea.TEMP,
                byte_count=byte_count,
                entry_count=entry_count,
            )
        assert raised.value.code is WorkspaceErrorCode.QUOTA_INVALID


def test_outstanding_quota_reservations_cannot_overcommit() -> None:
    quota = _quota(maximum_case_bytes=10, maximum_cache_bytes=10, maximum_temp_bytes=10)
    usage = _usage(case_bytes=0, cache_bytes=0, temp_bytes=0, entry_count=0)
    book = QuotaReservationBook(quota)
    first = book.reserve(usage, QuotaArea.CACHE, byte_count=6)

    with pytest.raises(WorkspaceError) as raised:
        book.reserve(usage, QuotaArea.CACHE, byte_count=5)
    assert raised.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED

    first.release()
    second = book.reserve(usage, QuotaArea.CACHE, byte_count=5)
    second.release()


@pytest.mark.parametrize("capacity", ["bytes", "entries"])
def test_outstanding_reservation_overflow_reports_exceeded_not_invalid(capacity: str) -> None:
    quota = _quota(
        maximum_case_bytes=MAX_SAFE_QUOTA_INTEGER,
        maximum_cache_bytes=MAX_SAFE_QUOTA_INTEGER,
        maximum_temp_bytes=MAX_SAFE_QUOTA_INTEGER,
        maximum_entries=MAX_SAFE_QUOTA_INTEGER,
    )
    usage = _usage(case_bytes=0, cache_bytes=0, temp_bytes=0, entry_count=0)
    book = QuotaReservationBook(quota)
    if capacity == "bytes":
        first = book.reserve(
            usage,
            QuotaArea.CACHE,
            byte_count=MAX_SAFE_QUOTA_INTEGER,
            entry_count=0,
        )
        request = {"byte_count": 1, "entry_count": 0}
    else:
        first = book.reserve(
            usage,
            QuotaArea.CACHE,
            byte_count=0,
            entry_count=MAX_SAFE_QUOTA_INTEGER,
        )
        request = {"byte_count": 0, "entry_count": 1}

    with pytest.raises(WorkspaceError) as raised:
        book.reserve(usage, QuotaArea.CACHE, **request)
    first.release()

    assert raised.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED


def test_concurrent_quota_reservations_are_serialized() -> None:
    quota = _quota(maximum_case_bytes=10, maximum_cache_bytes=10, maximum_temp_bytes=10)
    usage = _usage(case_bytes=0, cache_bytes=0, temp_bytes=0, entry_count=0)
    book = QuotaReservationBook(quota)
    start = Barrier(2)
    release = Event()

    def reserve() -> WorkspaceErrorCode | None:
        start.wait()
        try:
            reservation = book.reserve(usage, QuotaArea.CACHE, byte_count=6)
        except WorkspaceError as error:
            return error.code
        release.wait(timeout=5)
        reservation.release()
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        completed, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
        assert completed
        release.set()
        outcomes = [future.result(timeout=5) for future in futures]

    assert outcomes.count(None) == 1
    assert outcomes.count(WorkspaceErrorCode.QUOTA_EXCEEDED) == 1


def test_cleanup_target_is_a_closed_runtime_enum(tmp_path: Path) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(CASE_ID, "originals")  # type: ignore[arg-type]
    assert raised.value.code is WorkspaceErrorCode.PATH_INVALID


@pytest.mark.skipif(os.name != "nt", reason="requires Windows delete-sharing semantics")
def test_failed_permission_verification_cannot_swap_then_delete_a_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private-leaf"
    displaced = tmp_path / "displaced-leaf"
    replacement = tmp_path / "replacement-leaf"
    replacement.mkdir(mode=0o700)
    marker = replacement / "marker.bin"
    marker.write_bytes(b"replacement")
    swap_was_blocked = False

    def fail_verification(_handle: object) -> None:
        nonlocal swap_was_blocked
        try:
            target.rename(displaced)
        except OSError:
            swap_was_blocked = True
        else:
            replacement.rename(target)
        raise WorkspaceError(
            WorkspaceErrorCode.PERMISSIONS_INSECURE,
            "test.permissions",
            "Synthetic verification failure.",
        )

    monkeypatch.setattr(
        "paperworkshell.storage.permissions.verify_private_acl",
        fail_verification,
    )
    permissions = WindowsPrivateDirectoryPermissions()

    with pytest.raises(WorkspaceError) as raised:
        permissions.create_private_directory(target, context="test.permissions")

    assert raised.value.code is WorkspaceErrorCode.PERMISSIONS_INSECURE
    assert swap_was_blocked
    assert not target.exists()
    assert not displaced.exists()
    assert marker.read_bytes() == b"replacement"


def test_known_folder_result_is_com_freed_even_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingKnownFolderCall:
        def __init__(self) -> None:
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *_args: object) -> int:
            return 1

    shell32 = SimpleNamespace(SHGetKnownFolderPath=FailingKnownFolderCall())
    released: list[object] = []
    monkeypatch.setattr(windows_storage, "_libraries", lambda: (object(), object(), shell32))
    monkeypatch.setattr(windows_storage, "_co_task_mem_free", released.append)

    with pytest.raises(WorkspaceError) as raised:
        windows_storage.current_profile_root()

    assert raised.value.code is WorkspaceErrorCode.LOCATION_UNSUPPORTED
    assert len(released) == 1
