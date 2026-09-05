"""Launch the PWS-0408 human pilot in a fresh, isolated synthetic workspace.

This developer-only helper uses the ordinary production desktop and worker
composition. Preparing or opening the pilot never records a human test pass.
"""

from __future__ import annotations

import argparse
import hashlib
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Never
from uuid import UUID, uuid4

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
TASK_ID: Final = "PWS-0408"
SOURCE_FILENAME: Final = "synthetic-generic-packet.pdf"
CARD_FILENAME: Final = "pilot-instructions.txt"
HASH_FILENAME: Final = "synthetic-source.sha256"
MAX_SOURCE_BYTES: Final = 65_536
PAGE_HEADINGS: Final = (
    "Application form",
    "Identity evidence",
    "Proof of address",
    "Supporting evidence",
)


class PilotErrorCode(StrEnum):
    ARGUMENT_INVALID = "PWS0408_PILOT_ARGUMENT_INVALID"
    WORKSPACE_UNSAFE = "PWS0408_PILOT_WORKSPACE_UNSAFE"
    PREPARE_FAILED = "PWS0408_PILOT_PREPARE_FAILED"
    RESUME_INVALID = "PWS0408_PILOT_RESUME_INVALID"
    LAUNCH_FAILED = "PWS0408_PILOT_LAUNCH_FAILED"


_ERROR_TEXT: Final = {
    PilotErrorCode.ARGUMENT_INVALID: "Use --prepare-only or --resume followed by an existing pilot attempt UUID.",
    PilotErrorCode.WORKSPACE_UNSAFE: "The pilot workspace must be an ordinary directory inside this repository's output folder.",
    PilotErrorCode.PREPARE_FAILED: "The synthetic pilot could not be prepared. Check free disk space and workspace permissions.",
    PilotErrorCode.RESUME_INVALID: "That pilot attempt is missing, changed, or not a complete prepared attempt. Start a new pilot.",
    PilotErrorCode.LAUNCH_FAILED: "The pilot desktop could not start. Keep the attempt and use the resume command after resolving the development environment.",
}


@dataclass(slots=True, repr=False)
class PilotError(RuntimeError):
    code: PilotErrorCode

    def __post_init__(self) -> None:
        if type(self.code) is not PilotErrorCode:
            self.code = PilotErrorCode.ARGUMENT_INVALID
        self.args = (self.code.value, _ERROR_TEXT[self.code])

    def __str__(self) -> str:
        return f"{self.code.value}: {_ERROR_TEXT[self.code]}"

    def __repr__(self) -> str:
        return f"PilotError(code={self.code.value!r})"


def _reject(code: PilotErrorCode) -> Never:
    error = PilotError(code)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class PilotAttempt:
    attempt_id: UUID
    root: Path = field(repr=False)

    @property
    def profile(self) -> Path:
        return self.root / "profile"

    @property
    def source(self) -> Path:
        return self.root / SOURCE_FILENAME

    @property
    def card(self) -> Path:
        return self.root / CARD_FILENAME

    @property
    def export_directories(self) -> tuple[Path, ...]:
        return tuple(self.root / "exports" / mode for mode in ("combined", "separate", "zip"))

    def __repr__(self) -> str:
        return f"PilotAttempt(attempt_id={str(self.attempt_id)!r})"


def _require_plain_path(root: Path, target: Path) -> None:
    if not root.is_absolute() or not target.is_relative_to(root):
        _reject(PilotErrorCode.WORKSPACE_UNSAFE)
    parts = (
        root,
        *(
            root.joinpath(*target.relative_to(root).parts[:n])
            for n in range(1, len(target.relative_to(root).parts) + 1)
        ),
    )
    for candidate in parts:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _reject(PilotErrorCode.WORKSPACE_UNSAFE)
        if candidate != target and not stat.S_ISDIR(info.st_mode):
            _reject(PilotErrorCode.WORKSPACE_UNSAFE)


def _parse_attempt_id(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError, TypeError, AttributeError:
        _reject(PilotErrorCode.ARGUMENT_INVALID)
    if parsed.version != 4 or str(parsed) != value:
        _reject(PilotErrorCode.ARGUMENT_INVALID)
    return parsed


def prepare_attempt(workspace_root: Path, *, resume: str | None = None) -> PilotAttempt:
    """Create fresh synthetic inputs, or validate an exact local attempt for reuse."""
    try:
        root = workspace_root.absolute()
        if root.resolve(strict=True) != root:
            _reject(PilotErrorCode.WORKSPACE_UNSAFE)
        attempts = root / "output" / "pws0408-pilot"
        _require_plain_path(root, attempts)
        attempt_id = uuid4() if resume is None else _parse_attempt_id(resume)
        selected = PilotAttempt(attempt_id, attempts / str(attempt_id))
        _require_plain_path(root, selected.root)
        if resume is not None:
            _verify_prepared_attempt(root, selected)
            return selected
        attempts.mkdir(parents=True, exist_ok=True)
        selected.root.mkdir(exist_ok=False)
        selected.profile.mkdir()
        for path in selected.export_directories:
            path.mkdir(parents=True, exist_ok=False)
        _write_synthetic_pdf(selected.source)
        digest = hashlib.sha256(selected.source.read_bytes()).hexdigest()
        with (selected.root / HASH_FILENAME).open("x", encoding="ascii", newline="\n") as stream:
            stream.write(digest + "\n")
        with selected.card.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(pilot_card(selected))
        _verify_prepared_attempt(root, selected)
        return selected
    except PilotError:
        raise
    except Exception:
        _reject(
            PilotErrorCode.RESUME_INVALID if resume is not None else PilotErrorCode.PREPARE_FAILED
        )


def _verify_prepared_attempt(workspace_root: Path, attempt: PilotAttempt) -> None:
    for directory in (attempt.root, attempt.profile, *attempt.export_directories):
        _require_plain_path(workspace_root, directory)
        if not directory.is_dir():
            _reject(PilotErrorCode.RESUME_INVALID)
    for path, limit in (
        (attempt.source, MAX_SOURCE_BYTES),
        (attempt.card, 32_768),
        (attempt.root / HASH_FILENAME, 65),
    ):
        _require_plain_path(workspace_root, path)
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            _reject(PilotErrorCode.RESUME_INVALID)
    expected = (attempt.root / HASH_FILENAME).read_text(encoding="ascii")
    if expected != hashlib.sha256(attempt.source.read_bytes()).hexdigest() + "\n":
        _reject(PilotErrorCode.RESUME_INVALID)
    if attempt.card.read_text(encoding="utf-8") != pilot_card(attempt):
        _reject(PilotErrorCode.RESUME_INVALID)


def _write_synthetic_pdf(destination: Path) -> None:
    """Generate four small, clearly synthetic vector pages with large targets."""
    import pikepdf

    with pikepdf.Pdf.new() as document:
        font = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
        bold = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name("/Helvetica-Bold"),
            )
        )
        for position, heading in enumerate(PAGE_HEADINGS, 1):
            page = document.add_blank_page(page_size=(612, 792))
            page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font, F2=bold))
            content = (
                "0.1 0.1 0.1 rg\n"
                "BT /F2 18 Tf 56 736 Td (SYNTHETIC TRAINING PACKET) Tj ET\n"
                f"BT /F2 32 Tf 56 660 Td ({heading}) Tj ET\n"
                f"BT /F2 128 Tf 56 490 Td ({position}) Tj ET\n"
                f"BT /F1 22 Tf 56 442 Td (Page {position} of 4) Tj ET\n"
                "BT /F1 16 Tf 56 388 Td (Assign this page to the matching checklist category.) Tj ET\n"
                "0.1 0.3 0.6 RG 3 w 56 236 500 96 re S\n"
                "BT /F1 17 Tf 72 296 Td (Optional practice mark area) Tj ET\n"
                "BT /F1 13 Tf 72 268 Td (Place a short text mark inside this large box if testing Fill.) Tj ET\n"
                "BT /F2 17 Tf 56 154 Td (No real person or application is represented.) Tj ET\n"
                "BT /F1 14 Tf 56 118 Td (Do not use this packet for a real submission.) Tj ET\n"
                "BT /F1 12 Tf 56 64 Td (PWS-0408 human pilot - synthetic content only) Tj ET\n"
            ).encode("ascii")
            page.obj.Contents = document.make_stream(content)
        with destination.open("xb") as output:
            document.save(output, deterministic_id=True)
    if destination.stat().st_size > MAX_SOURCE_BYTES:
        _reject(PilotErrorCode.PREPARE_FAILED)


def pilot_card(attempt: PilotAttempt) -> str:
    """Explicit human instructions and recovery paths; never a pass certificate."""
    combined, separate, zipped = attempt.export_directories
    return (
        "PWS-0408 human pilot instructions\n"
        "Human result: NOT RECORDED. Preparation and launch are not test evidence.\n"
        "Use only the synthetic source below. The application uses the constrained resource policy.\n"
        f"Attempt: {attempt.attempt_id}\n"
        f"Isolated case profile: {attempt.profile}\n"
        f"Synthetic source: {attempt.source}\n"
        f"Combined PDF destination: {combined}\n"
        f"Separate PDFs destination: {separate}\n"
        f"ZIP destination: {zipped}\n\n"
        "Keyboard walkthrough\n"
        "1. Activate Create new case, then Import document to import the synthetic source.\n"
        "   Confirm that all four pages are present and review their large headings.\n"
        "2. Open Review package constraints. Activate Restore generic defaults. Review the example\n"
        "   file counts, naming and size limits, then Apply constraint changes. These are not real portal rules.\n"
        "3. Press Ctrl+Shift+C to open Review checklist assignments. In Checklist categories,\n"
        "   choose Application form. In Pages without a confirmed assignment, select page 1\n"
        "   and activate Add selected pages, then Confirm added document. Repeat for Identity evidence/page 2\n"
        "   and Proof of address/page 3. Activate Apply checklist changes.\n"
        "4. Press Ctrl+E: Supporting evidence should be missing and page 4 unassigned.\n"
        "   Export package must be disabled. Close the review; reopen the checklist, assign and confirm\n"
        "   page 4 as Supporting evidence, then Apply checklist changes.\n"
        "5. Press Ctrl+E. Use Combined PDF. Read the entire Package summary.\n"
        "   Unavailable compatibility/quality checks must be described as unverified or needing review.\n"
        "6. Review the warnings and human checks, select their acknowledgement, and activate Export package.\n"
        "   Choose the Combined PDF destination above. Verify the saved name, bytes, pages and concrete checks.\n"
        "7. Activate Save summary and choose a new .txt filename in the same destination. This must not\n"
        "   change the upload count. Close review; repeat the export into the occupied destination.\n"
        "   It must preserve existing files and must not report the retry as a successful new export.\n"
        "8. Close and restart with the resume command below. Open the existing case and confirm the assignments remain.\n"
        "   The unsaved final package result is intentionally not retained across restart.\n\n"
        "Optional other formats\n"
        "Separate PDFs and ZIP are covered by automated real-worker tests. For optional human testing,\n"
        "first review and change the mode-specific requested filenames in Review package constraints.\n"
        "Do not silently clear or replace intentional saved names. Then select the matching export mode\n"
        "and its separate destination above; expect 4 named PDFs or 1 ZIP containing 4 PDF members.\n\n"
        "Use Tab and Shift+Tab to move focus, arrows to select rows/modes, and Space or Enter to activate controls.\n"
        "Keep the summary review open as long as needed. Check readable wrapping, visible focus, and 200% scaling.\n"
        "Record actual first-time-user and keyboard observations for steps 1-8 in the task's human-test protocol.\n"
        "NVDA and the broader manual recovery matrix belong to the separate PWS-0409 gate.\n"
        "This launcher does not claim those observations or record a pass.\n\n"
        "Recovery\n"
        "If the application closes, rerun this exact attempt; case data and generated exports remain local.\n"
        f"uv run --locked --no-sync python -m tools.pws0408_pilot --resume {attempt.attempt_id}\n"
        "Use a new destination subfolder or a new filename when retrying a known saved output.\n"
        "The launcher never deletes attempts. Retain this card with any human observations.\n"
    )


def launch_attempt(attempt: PilotAttempt) -> int:
    """Open the ordinary desktop on its permanent owner thread and isolated profile."""
    from paperworkshell import __main__ as desktop
    from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
    from paperworkshell.storage.workspace import CaseWorkspaceManager
    from paperworkshell.ui.application import run_desktop_application

    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(policy)
    manager = CaseWorkspaceManager(
        profile_root=attempt.profile,
        quota=desktop._workspace_quota(policy),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    return run_desktop_application(
        lambda: desktop._build_backend(manager, resources=resources),
        ["PaperWorkShell PWS-0408 synthetic pilot"],
        resource_policy=policy,
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        _reject(PilotErrorCode.ARGUMENT_INVALID)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare inputs and instructions without opening the desktop.",
    )
    parser.add_argument("--resume", help="Resume exactly one existing local pilot attempt UUID.")
    try:
        args = parser.parse_args(argv)
        attempt = prepare_attempt(PROJECT_ROOT, resume=args.resume)
        print(pilot_card(attempt), flush=True)
        if args.prepare_only:
            return 0
        try:
            return launch_attempt(attempt)
        except Exception:
            _reject(PilotErrorCode.LAUNCH_FAILED)
    except PilotError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
