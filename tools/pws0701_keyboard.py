"""Prepare or open an isolated synthetic production desktop for PWS-0701.

This developer entry point never records observations or declares a pass. The
fixed workspace is preserved between launches, including cases and exports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Never

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
_FIXTURE_ROOT: Final = Path("tests/fixtures/synthetic/v1")
_MAX_FIXTURE_BYTES: Final = 1024 * 1024
_INPUTS: Final = (
    ("packet.pdf", "pdf/ocr-readable-mixed.pdf"),
    ("support.pdf", "pdf/born-digital.pdf"),
    ("scan.png", "image/ocr-raster-text.png"),
    ("signature.png", "image/transparent.png"),
    ("removal.pdf", "pdf/redaction-born-digital.pdf"),
    ("invalid.pdf", "pdf/malformed-truncated.pdf"),
)
_ERROR_TEXT: Final = (
    "PWS0701_WORKSPACE_UNAVAILABLE: The isolated keyboard test workspace could not be "
    "prepared or validated. Preserve its files and check local permissions or changed inputs."
)


class KeyboardWorkspaceError(RuntimeError):
    """Sanitized developer-helper failure without document or filesystem values."""

    def __init__(self) -> None:
        super().__init__(_ERROR_TEXT)


@dataclass(frozen=True, slots=True)
class KeyboardWorkspace:
    root: Path = field(repr=False)

    @property
    def profile(self) -> Path:
        return self.root / "profile"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"


def _plain_path(root: Path, target: Path) -> None:
    """Reject links/reparse points before reading or creating a fixed child."""
    if not target.is_relative_to(root):
        raise KeyboardWorkspaceError
    parts = target.relative_to(root).parts
    for selected in (root, *(root.joinpath(*parts[:n]) for n in range(1, len(parts)))):
        try:
            info = selected.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise KeyboardWorkspaceError
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise KeyboardWorkspaceError


def _fixture_bytes(root: Path, target: Path) -> bytes:
    _plain_path(root, target)
    if not target.is_file() or not 0 < target.stat().st_size <= _MAX_FIXTURE_BYTES:
        raise KeyboardWorkspaceError
    return target.read_bytes()


def prepare_workspace(project_root: Path) -> KeyboardWorkspace:
    """Create once or validate exact inputs; never rewrite existing case/output files."""
    try:
        root = project_root.absolute()
        if root.resolve(strict=True) != root:
            raise KeyboardWorkspaceError
        workspace = KeyboardWorkspace(root / "output" / "pws0701-keyboard")
        _plain_path(root, workspace.root)
        inputs = tuple(
            (name, _fixture_bytes(root, root / _FIXTURE_ROOT / relative))
            for name, relative in _INPUTS
        )
        expected = {
            "schema_version": 1,
            "task_id": "PWS-0701",
            "inputs": {
                name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in inputs
            },
            "observation_status": "not_recorded",
        }
        identity = (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode("ascii")
        fresh = not workspace.root.exists()
        if fresh:
            workspace.root.mkdir(parents=True, exist_ok=False)
            for directory in (workspace.profile, workspace.inputs, workspace.exports):
                directory.mkdir()
            for name, content in inputs:
                with (workspace.inputs / name).open("xb") as stream:
                    stream.write(content)
            with (workspace.root / "inputs-v1.json").open("xb") as stream:
                stream.write(identity)
        for directory in (workspace.profile, workspace.inputs, workspace.exports):
            _plain_path(root, directory)
            if not directory.is_dir():
                raise KeyboardWorkspaceError
        for name, content in inputs:
            if _fixture_bytes(root, workspace.inputs / name) != content:
                raise KeyboardWorkspaceError
        if _fixture_bytes(root, workspace.root / "inputs-v1.json") != identity:
            raise KeyboardWorkspaceError
        return workspace
    except KeyboardWorkspaceError:
        raise
    except Exception:
        raise KeyboardWorkspaceError from None


def launch_workspace(workspace: KeyboardWorkspace) -> int:
    """Use the ordinary constrained production composition on an isolated profile."""
    import paperworkshell.__main__ as desktop
    from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
    from paperworkshell.storage.workspace import CaseWorkspaceManager
    from paperworkshell.ui.application import run_desktop_application

    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(policy)
    manager = CaseWorkspaceManager(
        profile_root=workspace.profile,
        quota=desktop._workspace_quota(policy),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    return run_desktop_application(
        lambda: desktop._build_backend(manager, resources=resources),
        ["PaperWorkShell PWS-0701 keyboard audit"],
        resource_policy=policy,
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise KeyboardWorkspaceError


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--prepare-only", action="store_true")
        args = parser.parse_args(argv)
        workspace = prepare_workspace(PROJECT_ROOT)
        print("PWS-0701 observations: NOT RECORDED. Launching is not a pass.", flush=True)
        # These explicit developer instructions identify only prepared synthetic
        # inputs/destinations; they are not routine application diagnostics.
        print(f"Synthetic inputs: {workspace.inputs}", flush=True)
        print(f"New export destinations: {workspace.exports}", flush=True)
        print("Cases and existing exports are preserved between launches.", flush=True)
        return 0 if args.prepare_only else launch_workspace(workspace)
    except KeyboardWorkspaceError:
        print(_ERROR_TEXT, file=sys.stderr)
        return 1
    except Exception:
        print(
            "PWS0701_LAUNCH_FAILED: The isolated keyboard desktop could not start.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
