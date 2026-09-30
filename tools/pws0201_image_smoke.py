"""Prepare and resume a synthetic image journey in the ordinary desktop.

This developer helper creates no acceptance result. Its local test card includes
paths for the operator; those paths are never copied into committed evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = ("synthetic-ä-画像.jpg", "synthetic-alpha.png")
MAX_SOURCE_BYTES = 1_048_576


def _plain_path(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError("smoke.workspace")
    for candidate in (root, *reversed(path.relative_to(root).parents)):
        selected = candidate if candidate.is_absolute() else root / candidate
        if selected.exists():
            info = selected.lstat()
            if not stat.S_ISDIR(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("smoke.workspace")
    if path.exists():
        info = path.lstat()
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or (
            stat.S_ISREG(info.st_mode) and info.st_nlink != 1
        ):
            raise ValueError("smoke.workspace")


def _make_sources(root: Path) -> None:
    from PIL import Image, ImageDraw

    with Image.new("RGB", (600, 800), "white") as upright:
        draw = ImageDraw.Draw(upright)
        draw.rectangle((35, 35, 565, 765), outline="black", width=8)
        draw.polygon(
            ((300, 90), (130, 280), (240, 280), (240, 540), (360, 540), (360, 280), (470, 280)),
            fill="black",
        )
        draw.text((105, 610), "UPRIGHT JPEG", fill="black", font_size=48)
        draw.rectangle((45, 45, 105, 105), fill="red")
        exif = Image.Exif()
        exif[274] = 6
        with upright.transpose(Image.Transpose.ROTATE_90) as encoded:
            with (root / SOURCE_NAMES[0]).open("xb") as output:
                encoded.save(output, "JPEG", exif=exif, quality=90)
    with Image.new("RGBA", (600, 800), (0, 0, 255, 0)) as alpha:
        draw = ImageDraw.Draw(alpha)
        draw.rectangle((35, 35, 565, 765), outline="black", width=8)
        draw.polygon(
            ((300, 90), (130, 280), (240, 280), (240, 540), (360, 540), (360, 280), (470, 280)),
            fill="black",
        )
        draw.text((125, 610), "ALPHA PNG", fill="black", font_size=48)
        draw.rectangle((45, 45, 105, 105), fill=(255, 0, 0, 255))
        with (root / SOURCE_NAMES[1]).open("xb") as output:
            alpha.save(output, "PNG")


def prepare_attempt(workspace: Path, resume: str | None = None) -> Path:
    """Create fresh inputs; a resume only authenticates a prior attempt."""
    workspace = workspace.absolute()
    if workspace.resolve(strict=True) != workspace:
        raise ValueError("smoke.workspace")
    attempt_id = uuid4() if resume is None else UUID(resume)
    if attempt_id.version != 4 or (resume is not None and str(attempt_id) != resume):
        raise ValueError("smoke.attempt")
    root = workspace / "output" / "pws0201-image-smoke" / str(attempt_id)
    _plain_path(workspace, root)
    if resume is None:
        root.mkdir(parents=True, exist_ok=False)
        (root / "profile").mkdir()
        (root / "exports").mkdir()
        _make_sources(root)
        expected = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_NAMES
        }
        with (root / "source-hashes.json").open("x", encoding="utf-8") as output:
            json.dump(expected, output, ensure_ascii=True, sort_keys=True)
    for name in (*SOURCE_NAMES, "source-hashes.json"):
        candidate = root / name
        _plain_path(workspace, candidate)
        if not candidate.is_file() or not 0 < candidate.stat().st_size <= MAX_SOURCE_BYTES:
            raise ValueError("smoke.source")
    expected = json.loads((root / "source-hashes.json").read_text(encoding="utf-8"))
    actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_NAMES}
    if expected != actual:
        raise ValueError("smoke.source_changed")
    for name in ("profile", "exports"):
        _plain_path(workspace, root / name)
        if not (root / name).is_dir():
            raise ValueError("smoke.workspace")
    return root


@contextmanager
def _worker_runtime(staged: bool) -> Iterator[None]:
    """Use the existing copy-owned integration runtime for this desktop loop only.

    The stage helper validates and owns a random directory on Python's fixed drive.
    Its cleanup contract deliberately excludes the source runtime and attempt data.
    All ordinary AppContainer, ACL, Job, RPC, and document checks still run.
    """
    if not staged:
        yield
        return

    import paperworkshell.__main__ as desktop
    from tests.integration.test_pdf_export_worker_windows import _stage_pdf_engines
    from tests.worker_resources import (
        cleanup_worker_test_resources,
        create_worker_test_stage_root,
        stage_worker_test_resources,
    )

    resources = stage_worker_test_resources(create_worker_test_stage_root())
    try:
        _stage_pdf_engines(resources)
        with patch.object(
            desktop,
            "_worker_runtime_resources",
            lambda: (
                resources.python_executable,
                None,
                resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
                (resources.runtime_root,),
                (resources.source_root,),
                resources.source_root,
            ),
        ):
            yield
    finally:
        cleanup_worker_test_resources(resources)


def launch_attempt(root: Path, *, staged_worker_runtime: bool = False) -> int:
    import paperworkshell.__main__ as desktop
    from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
    from paperworkshell.storage.workspace import CaseWorkspaceManager
    from paperworkshell.ui.application import run_desktop_application

    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(policy)
    manager = CaseWorkspaceManager(
        profile_root=root / "profile",
        quota=desktop._workspace_quota(policy),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    with _worker_runtime(staged_worker_runtime):
        return run_desktop_application(
            lambda: desktop._build_backend(manager, resources=resources),
            ["PaperWorkShell synthetic image smoke"],
            resource_policy=policy,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument(
        "--staged-worker-runtime",
        action="store_true",
        help="Use the copy-owned integration worker runtime; no acceptance result is recorded.",
    )
    args = parser.parse_args(argv)
    try:
        root = prepare_attempt(PROJECT_ROOT, args.resume)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print("Synthetic test card; no pass recorded. Keep these local paths out of evidence.")
        if args.staged_worker_runtime:
            print(
                "Worker runtime: staged copies of CPython, Pillow, PDF engines, and current code."
            )
            print(
                "Ordinary worker security checks apply. This does not test development-runtime startup."
            )
        else:
            print("Worker runtime: current development environment.")
        for name in SOURCE_NAMES:
            print(f"Import: {root / name}")
        print(f"Export PDF: {root / 'exports' / 'image-pages.pdf'}")
        print("Create a case; import both sources with Ctrl+I. Both arrows must point up,")
        print("with a red square at top left and white background. Close and reopen the case.")
        print("Use Export pages as PDF (Ctrl+Shift+E), then inspect the derivative.")
        print("Repeat import with Escape to cancel, then retry. Test 200% scaling.")
        runtime_option = " --staged-worker-runtime" if args.staged_worker_runtime else ""
        print(
            "Resume: uv run --locked --no-sync python -m tools.pws0201_image_smoke "
            f"--resume {root.name}{runtime_option}"
        )
        return (
            0
            if args.prepare_only
            else launch_attempt(root, staged_worker_runtime=args.staged_worker_runtime)
        )
    except Exception:
        print(
            "PWS0201_SMOKE_FAILED: Check the local attempt and development environment.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
