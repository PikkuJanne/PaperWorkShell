"""Recovery authority never broadens an individual worker's resource grants."""

from __future__ import annotations

import ctypes
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import (
    MAX_ACL_RECOVERY_GRANT_ROOTS,
    AclRecoveryEntry,
)
from paperworkshell.worker.client.process import WorkerLaunchRequest, WorkerResourcePolicy
from paperworkshell.worker.protocol.models import SessionCapability


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> windows.WindowsLaunchConfig:
    # All native path/ACL/process entry points are replaced in these pure tests.
    monkeypatch.setattr(windows, "_require_no_reparse_local_path", lambda _path: None)
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    runtime.mkdir()
    code.mkdir()
    python = runtime / "python.exe"
    bootstrap = code / "host_bootstrap.py"
    python.write_bytes(b"synthetic interpreter")
    bootstrap.write_bytes(b"# synthetic bootstrap\n")
    return windows.WindowsLaunchConfig(
        python_executable=python,
        bootstrap_script=bootstrap,
        runtime_roots=(runtime,),
        code_roots=(code,),
        working_directory=code,
    )


def _request() -> WorkerLaunchRequest:
    return WorkerLaunchRequest(
        case_id=uuid4(),
        session_id=uuid4(),
        worker_instance_id=uuid4(),
        session_capability=SessionCapability("a" * 64),
        resource_policy=WorkerResourcePolicy(256 * 1024 * 1024, 512 * 1024 * 1024, 30_000, 4),
    )


def test_empty_recovery_authority_preserves_existing_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)

    assert config.recovery_authority_roots == ()
    assert config.approved_recovery_roots == config.access_roots


def test_explicit_engine_and_packaged_recovery_roots_form_a_bounded_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _config(tmp_path, monkeypatch)
    selected = tmp_path / "explicit-selected-qpdf"
    packaged = tmp_path / "packaged-tesseract"
    selected.mkdir()
    packaged.mkdir()
    qpdf = selected / "qpdf.exe"
    qpdf.write_bytes(b"synthetic qpdf")
    selected_config = replace(core, qpdf_executable=qpdf)
    config = replace(
        selected_config,
        recovery_authority_roots=(*core.access_roots, packaged, packaged / "."),
    )

    assert config.approved_recovery_roots == (*selected_config.access_roots, packaged)
    assert config.access_roots == selected_config.access_roots
    assert config.required_startup_resources == selected_config.required_startup_resources
    assert config.fixed_argv == selected_config.fixed_argv
    assert config.operation_scope == selected_config.operation_scope
    assert packaged not in config.required_startup_resources
    assert str(packaged) not in repr(config)


@pytest.mark.parametrize("kind", ("list", "nonpath", "relative", "file", "missing", "stream"))
def test_invalid_recovery_roots_fail_during_configuration(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    values: dict[str, object] = {
        "list": [tmp_path],
        "nonpath": (str(tmp_path),),
        "relative": (Path("relative-private-token"),),
        "file": (config.python_executable,),
        "missing": (tmp_path / "missing-private-token",),
        "stream": (tmp_path / "private-token:stream",),
    }
    with pytest.raises(windows.WindowsLaunchError) as captured:
        replace(config, recovery_authority_roots=cast(tuple[Path, ...], values[kind]))

    assert captured.value.code is windows.WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert "private-token" not in str(captured.value)


def test_recovery_reparse_refuses_before_any_acl_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    alias = tmp_path / "synthetic-alias"
    alias.mkdir()

    def reject_alias(path: Path) -> None:
        if path == alias:
            raise windows.WindowsLaunchError(
                windows.WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.reparse",
                "Synthetic reparse refusal.",
            )

    monkeypatch.setattr(windows, "_require_no_reparse_local_path", reject_alias)
    with pytest.raises(windows.WindowsLaunchError, match="Synthetic reparse refusal"):
        replace(config, recovery_authority_roots=(alias,))


@pytest.mark.parametrize("combined", (False, True))
def test_recovery_root_count_is_bounded_before_launch(
    combined: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    count = MAX_ACL_RECOVERY_GRANT_ROOTS if combined else MAX_ACL_RECOVERY_GRANT_ROOTS + 1
    roots = tuple(tmp_path / f"root-{index}" for index in range(count))
    for root in roots:
        root.mkdir()
    with pytest.raises(windows.WindowsLaunchError) as captured:
        replace(config, recovery_authority_roots=roots)

    assert captured.value.code is windows.WindowsLaunchErrorCode.CONFIGURATION_INVALID


def test_launcher_forwards_recovery_authority_to_preflight_and_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    shared = tmp_path / "other-approved-engine"
    shared.mkdir()
    approved = (*config.access_roots, shared)
    # This also runs unchanged against the retained old module: only launch
    # forwarding changes, rather than failing just because the field is new.
    monkeypatch.setattr(
        windows.WindowsLaunchConfig,
        "approved_recovery_roots",
        property(lambda _self: approved),
        raising=False,
    )
    observed: list[tuple[Path, ...]] = []
    monkeypatch.setattr(windows, "_PROCESS_CREATION_QUARANTINED", False)
    monkeypatch.setattr(windows, "_PROCESS_CREATION_CLEANUP_PENDING", False)
    monkeypatch.setattr(windows, "_drain_launch_quarantines", lambda: True)
    monkeypatch.setattr(windows, "recover_worker_acl_access", lambda roots: observed.append(roots))

    class StopBeforeAnyGrant(Exception):
        pass

    def create(roots: tuple[Path, ...]) -> None:
        observed.append(roots)
        raise StopBeforeAnyGrant

    monkeypatch.setattr(windows._AppContainerProfile, "create", staticmethod(create))
    with pytest.raises(StopBeforeAnyGrant):
        windows.WindowsProcessLauncher(config).launch(_request())

    assert observed == [approved, approved]


def test_recovery_only_roots_are_not_added_to_launch_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    other_engine = tmp_path / "other-approved-engine"
    other_engine.mkdir()
    config = replace(config, recovery_authority_roots=(other_engine,))
    granted: list[tuple[Path, ...]] = []

    class StopAfterGrant(Exception):
        pass

    class Profile:
        sid = ctypes.c_void_p(1)

        def grant_read_execute(self, roots: tuple[Path, ...]) -> None:
            granted.append(roots)

        def require_read_execute(self, _resources: tuple[Path, ...]) -> None:
            raise StopAfterGrant

    monkeypatch.setattr(windows, "_PROCESS_CREATION_QUARANTINED", False)
    monkeypatch.setattr(windows, "_PROCESS_CREATION_CLEANUP_PENDING", False)
    monkeypatch.setattr(windows, "_drain_launch_quarantines", lambda: True)
    monkeypatch.setattr(windows, "recover_worker_acl_access", lambda _roots: None)
    monkeypatch.setattr(
        windows._AppContainerProfile, "create", staticmethod(lambda _roots: Profile())
    )
    monkeypatch.setattr(windows, "_close_profile_bounded", lambda _profile: True)
    with pytest.raises(StopAfterGrant):
        windows.WindowsProcessLauncher(config).launch(_request())

    assert granted == [config.access_roots]
    assert other_engine not in granted[0]


def test_unknown_journal_root_is_not_adopted_or_covered_by_an_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    unknown = tmp_path / "not-in-approved-config"
    entry = cast(AclRecoveryEntry, SimpleNamespace(grant_roots=(unknown,)))

    def forbidden_native(*_args: object) -> None:
        pytest.fail("Unknown journal authority reached SID/ACL work")

    monkeypatch.setattr(windows, "_derive_app_container_sid", forbidden_native)
    with pytest.raises(windows.WindowsLaunchError) as captured:
        windows._require_acl_recovery_entry_authority(entry, (tmp_path, *config.access_roots))

    assert captured.value.context == "access.recovery_allowlist"
