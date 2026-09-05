"""The human pilot launcher stays synthetic, isolated, recoverable, and honest."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast
from uuid import UUID

import pikepdf
import pytest

from paperworkshell import __main__ as desktop
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui import application
from tools import pws0408_pilot as pilot
from tools.dev import CHECK_COMMANDS, COMMANDS


def test_stable_launcher_command_is_opt_in_not_a_quality_gate() -> None:
    assert COMMANDS["pws0408-pilot"].arguments == ("python", "-m", "tools.pws0408_pilot")
    assert "pws0408-pilot" not in CHECK_COMMANDS


def test_preparation_creates_four_large_synthetic_pages_and_scoped_paths(tmp_path: Path) -> None:
    attempt = pilot.prepare_attempt(tmp_path)
    assert attempt.root.parent == tmp_path / "output" / "pws0408-pilot"
    assert UUID(attempt.root.name).version == 4
    assert attempt.profile.is_dir()
    assert all(
        path.is_relative_to(attempt.root) and path.is_dir() for path in attempt.export_directories
    )
    with pikepdf.Pdf.open(attempt.source) as document:
        assert len(document.pages) == 4
        assert "/JavaScript" not in document.Root
        for n, (page, heading) in enumerate(
            zip(document.pages, pilot.PAGE_HEADINGS, strict=True), 1
        ):
            content = page.obj.Contents.read_bytes().decode("ascii")
            assert heading in content
            assert "SYNTHETIC TRAINING PACKET" in content
            assert "32 Tf" in content and "128 Tf" in content
            assert f"Page {n} of 4" in content
            assert "No real person or application is represented." in content
            assert page.obj.Resources.Font.F2.BaseFont == pikepdf.Name("/Helvetica-Bold")
    card = attempt.card.read_text(encoding="utf-8")
    assert "Human result: NOT RECORDED" in card
    assert "Ctrl+Shift+C" in card and "Ctrl+E" in card
    assert "Supporting evidence should be missing" in card
    assert "Create new case" in card and "Import document" in card
    assert "Restore generic template defaults" in card
    assert "press Space to select the checkbox" in card
    assert "Wait for: The displayed summary was saved. Review it before sharing." in card
    assert "Optional other formats" in card
    assert "first review and change the mode-specific requested filenames" in card
    assert "separate PWS-0409 gate" in card
    assert f"--resume {attempt.attempt_id}" in card
    assert str(attempt.source) in card
    assert str(attempt.profile) not in repr(attempt)


def test_repeated_preparation_never_replaces_an_existing_attempt(tmp_path: Path) -> None:
    first = pilot.prepare_attempt(tmp_path)
    marker = first.export_directories[0] / "existing-export.txt"
    marker.write_text("SYNTHETIC_KEEP", encoding="utf-8")
    second = pilot.prepare_attempt(tmp_path)
    assert first.root != second.root
    assert first.source.read_bytes() == second.source.read_bytes()
    assert marker.read_text(encoding="utf-8") == "SYNTHETIC_KEEP"


def test_resume_preserves_profile_cases_exports_and_original_bytes(tmp_path: Path) -> None:
    attempt = pilot.prepare_attempt(tmp_path)
    profile_marker = attempt.profile / "synthetic-case-marker.txt"
    profile_marker.write_text("SYNTHETIC_KEEP", encoding="utf-8")
    source_bytes = attempt.source.read_bytes()
    resumed = pilot.prepare_attempt(tmp_path, resume=str(attempt.attempt_id))
    assert resumed == attempt
    assert resumed.source.read_bytes() == source_bytes
    assert profile_marker.read_text(encoding="utf-8") == "SYNTHETIC_KEEP"


@pytest.mark.parametrize(
    "invalid", ("../elsewhere", "C:/outside", "not-a-uuid", "10000000-0000-1000-8000-000000000001")
)
def test_resume_cannot_select_arbitrary_paths(tmp_path: Path, invalid: str) -> None:
    with pytest.raises(pilot.PilotError) as error:
        pilot.prepare_attempt(tmp_path, resume=invalid)
    assert error.value.code is pilot.PilotErrorCode.ARGUMENT_INVALID
    assert invalid not in str(error.value)
    assert not (tmp_path / "output").exists()


def test_resume_rejects_changed_input_before_launch(tmp_path: Path) -> None:
    attempt = pilot.prepare_attempt(tmp_path)
    attempt.source.write_bytes(b"synthetic changed input")
    with pytest.raises(pilot.PilotError) as error:
        pilot.prepare_attempt(tmp_path, resume=str(attempt.attempt_id))
    assert error.value.code is pilot.PilotErrorCode.RESUME_INVALID


def test_reparse_workspace_parent_is_refused_without_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.lstat
    selected = tmp_path / "output"

    def fake_lstat(path: Path) -> Any:
        if path == selected:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", fake_lstat)
        with pytest.raises(pilot.PilotError) as error:
            pilot.prepare_attempt(tmp_path)
    assert error.value.code is pilot.PilotErrorCode.WORKSPACE_UNSAFE
    assert not selected.exists()


def test_prepare_only_does_not_start_gui_or_claim_human_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pilot, "PROJECT_ROOT", tmp_path)

    def unexpected_launch(_attempt: pilot.PilotAttempt) -> int:
        raise AssertionError("GUI must not launch")

    monkeypatch.setattr(pilot, "launch_attempt", unexpected_launch)
    assert pilot.main(("--prepare-only",)) == 0
    captured = capsys.readouterr()
    assert "Human result: NOT RECORDED" in captured.out
    assert not captured.err


def test_launch_uses_only_isolated_profile_and_the_real_constrained_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = pilot.prepare_attempt(tmp_path)
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
        raise AssertionError("real user profile must never be selected")

    monkeypatch.setattr(desktop, "_build_backend", fake_backend)
    monkeypatch.setattr(application, "run_desktop_application", fake_desktop)
    monkeypatch.setattr(CaseWorkspaceManager, "for_current_user", forbidden_current_user)
    assert pilot.launch_attempt(attempt) == 0
    assert observed["profile"] == attempt.profile
    assert observed["policy"] is CONSTRAINED_RESOURCE_POLICY_V1
    resources = cast(desktop.DesktopApplicationResourceContext, observed["resources"])
    assert resources.resource_policy is CONSTRAINED_RESOURCE_POLICY_V1


def test_launcher_errors_do_not_echo_untrusted_arguments_or_raw_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pilot, "PROJECT_ROOT", tmp_path)
    assert pilot.main(("--PRIVATE_SECRET_PATH",)) == 1
    assert "PRIVATE_SECRET_PATH" not in capsys.readouterr().err

    def failing_launch(_attempt: pilot.PilotAttempt) -> int:
        raise RuntimeError("PRIVATE_SECRET_SOURCE")

    monkeypatch.setattr(pilot, "launch_attempt", failing_launch)
    assert pilot.main(()) == 1
    captured = capsys.readouterr()
    assert "PWS0408_PILOT_LAUNCH_FAILED" in captured.err
    assert "PRIVATE_SECRET_SOURCE" not in captured.err
    assert len(tuple((tmp_path / "output" / "pws0408-pilot").iterdir())) == 1
