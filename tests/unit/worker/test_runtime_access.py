"""No native calls: exact ownership and failure disposal for static runtime pins."""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from paperworkshell.worker import _windows_broker
from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._windows import WindowsLaunchError
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope


@dataclass
class _Profile:
    _deleted: bool = False
    pycache_guard: object | None = None
    sid: ctypes.c_void_p = field(default_factory=lambda: ctypes.c_void_p(123))
    name: str = "PaperWorkShell.Worker." + "1" * 24
    local_app_data: Path = Path("C:/synthetic-unused-profile")


def _profile(value: _Profile) -> windows._AppContainerProfile:
    return cast(windows._AppContainerProfile, value)


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[_Profile]]:
    calls: list[str] = []
    profiles: list[_Profile] = []

    def create(_cls: object, _roots: tuple[Path, ...]) -> windows._AppContainerProfile:
        calls.append("create")
        value = _Profile()
        profiles.append(value)
        return _profile(value)

    def reset(profile: windows._AppContainerProfile) -> None:
        calls.append("reset")
        profile._deleted = True

    def recreate(profile: windows._AppContainerProfile) -> None:
        calls.append("recreate")
        assert profile._deleted
        profile._deleted = False

    def close(_profile: object) -> bool:
        calls.append("close")
        return True

    monkeypatch.setattr(windows._AppContainerProfile, "create", classmethod(create))
    monkeypatch.setattr(windows, "_reset_profile_storage", reset)
    monkeypatch.setattr(windows, "_recreate_profile_storage", recreate)
    monkeypatch.setattr(windows, "_close_profile_bounded", close)
    return calls, profiles


def test_success_retains_only_one_runtime_owner_and_resets_every_generation(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, _ = lifecycle
    case = uuid4()
    access = windows._CaseRuntimeAccess(())
    first = access.acquire(case)
    assert access.snapshot == windows.RuntimeAccessSnapshot(case, True, True, False)
    access.finish(first, reusable=True)
    assert access.snapshot == windows.RuntimeAccessSnapshot(case, False, True, False)
    second = access.acquire(case)
    assert second is first
    access.finish(second, reusable=True)
    access.release_case()
    assert calls == ["create", "reset", "recreate", "reset", "close"]
    assert access.snapshot == windows.RuntimeAccessSnapshot()


def test_failure_or_cancellation_retires_and_next_attempt_gets_new_identity(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, profiles = lifecycle
    case = uuid4()
    access = windows._CaseRuntimeAccess(())
    first = access.acquire(case)
    access.finish(first, reusable=False)
    second = access.acquire(case)
    assert second is not first and len(profiles) == 2
    access.finish(second, reusable=False)
    access.release_case()
    assert calls == ["create", "reset", "close", "create", "reset", "close"]


def test_active_or_foreign_case_cannot_reuse_authority(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    access = windows._CaseRuntimeAccess(())
    first = access.acquire(uuid4())
    with pytest.raises(WindowsLaunchError):
        access.acquire(uuid4())
    with pytest.raises(WindowsLaunchError):
        access.release_case()
    access.finish(first, reusable=True)
    with pytest.raises(WindowsLaunchError):
        access.acquire(uuid4())
    access.release_case()
    second = access.acquire(uuid4())
    access.finish(second, reusable=False)
    access.release_case()


def test_reset_failure_blocks_reuse_but_allows_exact_cleanup_and_stays_sticky(
    lifecycle: tuple[list[str], list[_Profile]], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = lifecycle

    def fail(_profile: object) -> None:
        raise RuntimeError("synthetic reset failure")

    monkeypatch.setattr(windows, "_reset_profile_storage", fail)
    access = windows._CaseRuntimeAccess(())
    first = access.acquire(uuid4())
    with pytest.raises(RuntimeError):
        access.finish(first, reusable=True)
    assert access.snapshot.poisoned
    with pytest.raises(WindowsLaunchError):
        access.acquire(uuid4())
    monkeypatch.setattr(
        windows, "_reset_profile_storage", lambda profile: setattr(profile, "_deleted", True)
    )
    access.release_case()
    assert calls == ["create", "close"]
    assert access.snapshot.poisoned and not access.snapshot.retained


def test_uncertain_tree_never_releases_or_reuses_profile(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, _ = lifecycle
    access = windows._CaseRuntimeAccess(())
    access.acquire(uuid4())
    access.abandon()
    with pytest.raises(WindowsLaunchError):
        access.release_case()
    with pytest.raises(WindowsLaunchError):
        access.acquire(uuid4())
    assert calls == ["create"]
    assert access.snapshot.active_generation and access.snapshot.poisoned


def test_other_thread_cannot_mutate_a_case_runtime_context(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    access = windows._CaseRuntimeAccess(())
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            access.acquire(uuid4())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    assert len(errors) == 1 and isinstance(errors[0], WindowsLaunchError)
    assert lifecycle[0] == []


def test_broker_post_close_validation_failure_retires_runtime_access() -> None:
    events: list[str] = []

    class Worker:
        def retire_runtime_access(self) -> None:
            events.append("retire")

    class Broker:
        _worker = Worker()

        @_windows_broker.retire_runtime_on_failure
        def run(self, valid: bool) -> int:
            events.append("tree_closed")
            if not valid:
                raise ValueError("synthetic invalid artifact")
            return 7

    assert Broker().run(True) == 7
    with pytest.raises(ValueError):
        Broker().run(False)
    assert events == ["tree_closed", "tree_closed", "retire"]


def test_broker_cleanup_failure_is_not_hidden_by_bad_output() -> None:
    class Worker:
        def retire_runtime_access(self) -> None:
            raise RuntimeError("synthetic cleanup failed")

    class Broker:
        _worker = Worker()

        @_windows_broker.retire_runtime_on_failure
        def run(self) -> None:
            raise ValueError("synthetic invalid output")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        Broker().run()


def test_shared_authority_rejects_any_changed_fixed_runtime_or_recovery_identity() -> None:
    root = Path("C:/synthetic-fixed-runtime")

    def config(
        *, bootstrap: str = "host.py", recovery: str = "approved"
    ) -> windows.WindowsLaunchConfig:
        return cast(
            windows.WindowsLaunchConfig,
            SimpleNamespace(
                python_executable=root / "python.exe",
                bootstrap_script=root / bootstrap,
                working_directory=root,
                runtime_roots=(root,),
                code_roots=(root / "code",),
                venv_launcher=None,
                approved_recovery_roots=(root / recovery,),
                exact_runtime_files=(),
            ),
        )

    access = windows._CaseRuntimeAccess(())
    access.bind(config())
    access.bind(config())
    for changed in (config(bootstrap="different.py"), config(recovery="wider")):
        with pytest.raises(WindowsLaunchError) as error:
            access.bind(changed)
        assert error.value.context == "runtime_access.identity"


def test_switching_exact_inventories_revokes_the_previous_profile(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, _ = lifecycle
    access = windows._CaseRuntimeAccess(())
    case_id = uuid4()
    image_files = (Path("C:/synthetic-runtime/image.py"),)
    pdf_files = (Path("C:/synthetic-runtime/pdf.py"),)
    image = access.acquire(case_id, exact_files=image_files)
    access.finish(image, reusable=True)

    with pytest.raises(WindowsLaunchError) as error:
        access.acquire(case_id, exact_files=pdf_files)
    assert error.value.context == "runtime_access.exact_inventory"

    assert access.active_recovery_witness(case_id, exact_files=pdf_files) is None
    assert not access.snapshot.retained
    export = access.acquire(case_id, exact_files=pdf_files)
    assert export is not image
    access.finish(export, reusable=True)
    assert access.acquire(case_id, exact_files=pdf_files) is export
    access.finish(export, reusable=True)
    access.release_case()
    assert calls == ["create", "reset", "close", "create", "reset", "recreate", "reset", "close"]


def test_normalize_then_transform_reuses_the_exact_case_inventory(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, profiles = lifecycle
    access = windows._CaseRuntimeAccess(())
    case_id = uuid4()
    exact_files = (Path("C:/synthetic-runtime/image.py"),)
    for scope in (WorkerOperationScope.IMAGE_NORMALIZE, WorkerOperationScope.IMAGE_PAGES):
        config = cast(
            windows.WindowsLaunchConfig,
            SimpleNamespace(operation_scope=scope, exact_runtime_files=exact_files),
        )
        profile = access.acquire(case_id, exact_files=access.launch_inventory(config))
        assert profile is _profile(profiles[0])
        access.finish(profile, reusable=True)

    assert len(profiles) == 1
    access.release_case()
    assert calls == ["create", "reset", "recreate", "reset", "close"]
    assert access.snapshot == windows.RuntimeAccessSnapshot()


def test_pdf_launch_uses_exact_files_unless_a_broad_case_profile_already_exists(
    lifecycle: tuple[list[str], list[_Profile]],
) -> None:
    calls, _ = lifecycle
    access = windows._CaseRuntimeAccess(())
    case_id = uuid4()
    exact_files = (Path("C:/synthetic-runtime/engine.dll"),)
    export = cast(
        windows.WindowsLaunchConfig,
        SimpleNamespace(
            operation_scope=WorkerOperationScope.PDF_EXPORT,
            exact_runtime_files=exact_files,
        ),
    )
    image = cast(
        windows.WindowsLaunchConfig,
        SimpleNamespace(
            operation_scope=WorkerOperationScope.IMAGE_PAGES,
            exact_runtime_files=exact_files,
        ),
    )

    assert access.launch_inventory(export) == exact_files
    broad = access.acquire(case_id)
    access.finish(broad, reusable=True)
    assert access.launch_inventory(export) == ()
    assert access.launch_inventory(image) == exact_files
    with pytest.raises(WindowsLaunchError):
        access.active_recovery_witness(uuid4(), exact_files=access.launch_inventory(export))
    assert access.acquire(case_id, exact_files=access.launch_inventory(export)) is broad
    access.finish(broad, reusable=True)
    access.release_case()
    assert calls == ["create", "reset", "recreate", "reset", "close"]
