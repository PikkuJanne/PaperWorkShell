"""The keyboard audit launcher preserves isolated state and never invents results."""

from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui import application
from tools import pws0701_keyboard as keyboard


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    for _name, relative in keyboard._INPUTS:
        destination = tmp_path / keyboard._FIXTURE_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(keyboard.PROJECT_ROOT / keyboard._FIXTURE_ROOT / relative, destination)
    return tmp_path


def test_prepare_preserves_exact_synthetic_inputs_and_never_claims_a_pass(
    project_root: Path,
) -> None:
    workspace = keyboard.prepare_workspace(project_root)
    assert workspace.root == project_root / "output" / "pws0701-keyboard"
    assert workspace.profile.is_dir() and workspace.exports.is_dir()
    for name, relative in keyboard._INPUTS:
        assert (workspace.inputs / name).read_bytes() == (
            project_root / keyboard._FIXTURE_ROOT / relative
        ).read_bytes()
    identity = json.loads((workspace.root / "inputs-v1.json").read_bytes())
    assert identity["observation_status"] == "not_recorded"
    assert identity["task_id"] == "PWS-0701"
    assert str(project_root) not in json.dumps(identity)
    assert str(project_root) not in repr(workspace)


def test_reopen_keeps_cases_exports_and_the_same_inputs(project_root: Path) -> None:
    first = keyboard.prepare_workspace(project_root)
    case = first.profile / "synthetic-case-marker.txt"
    output = first.exports / "already-exported.pdf"
    case.write_bytes(b"SYNTHETIC_CASE_STATE")
    output.write_bytes(b"SYNTHETIC_KEEP_EXPORT")
    assert keyboard.prepare_workspace(project_root) == first
    assert case.read_bytes() == b"SYNTHETIC_CASE_STATE"
    assert output.read_bytes() == b"SYNTHETIC_KEEP_EXPORT"


@pytest.mark.parametrize("name", ["inputs/packet.pdf", "inputs-v1.json"])
def test_mutation_is_refused_without_replacing_the_changed_file(
    project_root: Path, name: str
) -> None:
    workspace = keyboard.prepare_workspace(project_root)
    changed = workspace.root / name
    changed.write_bytes(b"PRIVATE_CHANGED_VALUE")
    with pytest.raises(keyboard.KeyboardWorkspaceError) as error:
        keyboard.prepare_workspace(project_root)
    assert "PRIVATE_CHANGED_VALUE" not in str(error.value)
    assert changed.read_bytes() == b"PRIVATE_CHANGED_VALUE"


def test_reparse_parent_is_refused_before_output_is_created(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.lstat
    selected = project_root / "output"

    def fake_lstat(path: Path) -> Any:
        if path == selected:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(keyboard.KeyboardWorkspaceError):
        keyboard.prepare_workspace(project_root)
    assert not selected.exists()


def test_prepare_only_is_safe_and_unknown_arguments_are_sanitized(
    project_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keyboard, "PROJECT_ROOT", project_root)

    def forbidden_launch(_workspace: keyboard.KeyboardWorkspace) -> Never:
        raise AssertionError("Preparation must not launch the desktop.")

    monkeypatch.setattr(keyboard, "launch_workspace", forbidden_launch)
    assert keyboard.main(("--prepare-only",)) == 0
    assert "NOT RECORDED" in capsys.readouterr().out
    assert keyboard.main(("--PRIVATE_ARGUMENT",)) == 1
    assert "PRIVATE_ARGUMENT" not in capsys.readouterr().err


def test_launch_uses_only_isolated_profile_and_production_constrained_composition(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = keyboard.prepare_workspace(project_root)
    observed: dict[str, object] = {}

    def fake_backend(
        manager: CaseWorkspaceManager,
        *,
        resources: desktop.DesktopApplicationResourceContext | None = None,
    ) -> object:
        observed["profile"] = manager.profile_root
        observed["resources"] = resources
        return object()

    def fake_desktop(factory: Any, argv: Any = None, *, resource_policy: Any) -> int:
        observed["policy"] = resource_policy
        observed["argv"] = argv
        factory()
        return 0

    def forbidden_current_user(**_kwargs: object) -> Never:
        raise AssertionError("The real user profile must never be selected.")

    monkeypatch.setattr(desktop, "_build_backend", fake_backend)
    monkeypatch.setattr(application, "run_desktop_application", fake_desktop)
    monkeypatch.setattr(CaseWorkspaceManager, "for_current_user", forbidden_current_user)
    assert keyboard.launch_workspace(workspace) == 0
    assert observed["profile"] == workspace.profile
    assert observed["policy"] is CONSTRAINED_RESOURCE_POLICY_V1
    assert observed["argv"] == ["PaperWorkShell PWS-0701 keyboard audit"]


def test_launch_failure_never_echoes_raw_exception_or_claims_a_pass(
    project_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keyboard, "PROJECT_ROOT", project_root)

    def failing_launch(_workspace: keyboard.KeyboardWorkspace) -> int:
        raise RuntimeError("PRIVATE_RUNTIME_VALUE")

    monkeypatch.setattr(keyboard, "launch_workspace", failing_launch)
    assert keyboard.main(()) == 1
    captured = capsys.readouterr()
    assert "PWS0701_LAUNCH_FAILED" in captured.err
    assert "PRIVATE_RUNTIME_VALUE" not in captured.err
    assert "NOT RECORDED" in captured.out
