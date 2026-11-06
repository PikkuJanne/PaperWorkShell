"""Bind and checkpoint agent-operated NVDA observations without recording speech.

This recorder never launches documents, changes existing cases, or infers a task
pass. Prepare a new attempt after a material source/environment change. A person
must not be handed this developer CLI as a finished human-testing helper.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never, cast
from uuid import uuid4

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
_MAX_FILE_BYTES: Final = 32 * 1024 * 1024
_MAX_SOURCE_BYTES: Final = 32 * 1024 * 1024
_MAX_FILES: Final = 1024
_MAX_RECORDS: Final = 128
_MAX_RECORD_BYTES: Final = 1024 * 1024
_INPUTS: Final = (
    "tests/fixtures/synthetic/v1/pdf/ocr-readable-mixed.pdf",
    "tests/fixtures/synthetic/v1/pdf/born-digital.pdf",
    "tests/fixtures/synthetic/v1/image/ocr-raster-text.png",
    "tests/fixtures/synthetic/v1/pdf/redaction-born-digital.pdf",
    "tests/fixtures/synthetic/v1/pdf/malformed-truncated.pdf",
)
PHASES: Final = {
    "case-pages": (
        "Hear the selected case and its revision; select two page rows and hear page/document "
        "position, selection, rotation, and preview availability."
    ),
    "improve-cancel": (
        "Open scan review and OCR settings; hear their controls and state. Start a long "
        "operation, request cancellation, and hear progress, one request, and its terminal result."
    ),
    "fill-protect": (
        "Open visual overlays, select different mark kinds and hear geometry/edit state. "
        "Open removal marks and hear that saved intent alone does not remove information."
    ),
    "check-findings": (
        "Hear checklist category, assigned-document and confirmation state; inspect a "
        "quality/preflight finding and follow its actionable page or correction control."
    ),
    "error-recovery": (
        "Import the prepared invalid synthetic PDF; hear its safe failure and reachable "
        "Import recovery. No failed action may be announced as completed."
    ),
    "export-result": (
        "Read package review and confirmation state, perform a supported export, and hear "
        "the actual terminal checks and remaining review. Save/read the summary and leave the dialog."
    ),
}
_OUTCOMES: Final = ("observed", "problem", "later")
_ERROR: Final = (
    "PWS0702_EVIDENCE_UNAVAILABLE: The observation record is missing, changed, unsupported, "
    "or no longer matches this implementation and environment. Preserve earlier files; "
    "prepare a new attempt for changed behavior."
)


class ScreenReaderEvidenceError(RuntimeError):
    """A bounded, path-free developer-recorder failure."""

    def __init__(self) -> None:
        super().__init__(_ERROR)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _plain_path(root: Path, target: Path) -> None:
    if not target.is_relative_to(root):
        raise ScreenReaderEvidenceError
    parts = target.relative_to(root).parts
    for index in range(len(parts) + 1):
        selected = root.joinpath(*parts[:index])
        try:
            info = selected.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ScreenReaderEvidenceError
        if index < len(parts) and not stat.S_ISDIR(info.st_mode):
            raise ScreenReaderEvidenceError


def _read(root: Path, path: Path, maximum: int = _MAX_FILE_BYTES) -> bytes:
    _plain_path(root, path)
    if not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ScreenReaderEvidenceError
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if not 0 < len(content) <= maximum:
        raise ScreenReaderEvidenceError
    return content


def _write_once(root: Path, target: Path, content: bytes) -> None:
    """Publish a fully flushed new record exclusively; an interrupted stage is never evidence."""
    _plain_path(root, target)
    pending = target.with_name(f".{target.name}.{uuid4().hex}.pending")
    with pending.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    # Creating the link is atomic and exclusive on supported local Windows case
    # volumes. Unlike replace(), this cannot overwrite an existing observation.
    os.link(pending, target)
    pending.unlink()


def _nvda_version(path: Path) -> str:
    """Read installed binary version metadata; never ask an operator to transcribe it."""
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p)
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version.GetFileVersionInfoW.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    version.GetFileVersionInfoW.restype = ctypes.c_int
    version.VerQueryValueW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    )
    version.VerQueryValueW.restype = ctypes.c_int
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not 0 < size <= 1024 * 1024:
        raise ScreenReaderEvidenceError
    buffer = ctypes.create_string_buffer(size)
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint32()
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer) or not version.VerQueryValueW(
        buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
    ):
        raise ScreenReaderEvidenceError
    if length.value < 13 * ctypes.sizeof(ctypes.c_uint32):
        raise ScreenReaderEvidenceError
    fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    if fields[0] != 0xFEEF04BD:
        raise ScreenReaderEvidenceError
    high, low = fields[2], fields[3]
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def capture_environment() -> dict[str, object]:
    """Collect fresh runtime facts; installed NVDA presence alone is not an observation."""
    if os.name != "nt":
        raise ScreenReaderEvidenceError
    candidates = (
        Path("C:/Program Files/NVDA/nvda.exe"),
        Path("C:/Program Files (x86)/NVDA/nvda.exe"),
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise ScreenReaderEvidenceError
    content = _read(path.parent, path)
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "python": platform.python_version(),
        "pyside6_essentials": importlib.metadata.version("pyside6-essentials"),
        "nvda_file_version": _nvda_version(path),
        "nvda_executable_sha256": _digest(content),
        "provenance": "automatically_observed_installed_binary_and_current_runtime",
        "actual_nvda_speech_observed": False,
    }


def bindings(root: Path, environment: Mapping[str, object]) -> dict[str, object]:
    """Bind production source, locked dependencies, helper and exact synthetic fixtures."""
    if root != root.resolve(strict=True):
        raise ScreenReaderEvidenceError
    source = root / "src" / "paperworkshell"
    _plain_path(root, source)
    files = sorted(source.rglob("*.py"))
    if not files or len(files) > _MAX_FILES:
        raise ScreenReaderEvidenceError
    selected = (
        *files,
        root / "pyproject.toml",
        root / "uv.lock",
        root / "tools" / "pws0702_screen_reader.py",
        *(root / relative for relative in _INPUTS),
    )
    records: dict[str, object] = {}
    total = 0
    for path in selected:
        content = _read(root, path)
        total += len(content)
        if total > _MAX_SOURCE_BYTES:
            raise ScreenReaderEvidenceError
        records[path.relative_to(root).as_posix()] = {
            "bytes": len(content),
            "sha256": _digest(content),
        }
    return {"files": records, "environment": dict(environment), "phases": PHASES}


def _attempt_root(root: Path, attempt: str) -> Path:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", attempt) is None:
        raise ScreenReaderEvidenceError
    result = root / "output" / "pws0702-screen-reader" / attempt
    _plain_path(root, result)
    return result


def prepare(root: Path, attempt: str, environment: Mapping[str, object]) -> dict[str, object]:
    """Create once or require the same frozen attempt, without touching earlier cases."""
    destination = _attempt_root(root, attempt)
    current = bindings(root, environment)
    receipt = {
        "schema_version": 1,
        "task_id": "PWS-0702",
        "operator": "agent_nvda_speech_viewer",
        "human_assessment_claimed": False,
        "bindings": current,
        "binding_sha256": _digest(_json_bytes(current)),
        "processing_success_inferred": False,
        "task_completion_inferred": False,
    }
    identity = destination / "attempt.json"
    if destination.exists():
        if _read(root, identity, _MAX_RECORD_BYTES) != _json_bytes(receipt):
            raise ScreenReaderEvidenceError
    else:
        destination.mkdir(parents=True, exist_ok=False)
        _write_once(root, identity, _json_bytes(receipt))
    return receipt


def _observations(
    root: Path, destination: Path, receipt: dict[str, object]
) -> list[dict[str, object]]:
    files = sorted(destination.iterdir())
    if len(files) > _MAX_RECORDS + 1:
        raise ScreenReaderEvidenceError
    observations: list[dict[str, object]] = []
    previous = _digest(_json_bytes(receipt))
    completed = []
    for path in files:
        _plain_path(root, path)
        if re.fullmatch(
            r"\.(?:attempt|observation-[0-9]{4})\.json\.[0-9a-f]{32}\.pending", path.name
        ):
            continue
        if path.name != "attempt.json":
            completed.append(path)
    for index, path in enumerate(completed, 1):
        if path.name != f"observation-{index:04d}.json":
            raise ScreenReaderEvidenceError
        content = _read(root, path, _MAX_RECORD_BYTES)
        value: object = json.loads(content)
        if not isinstance(value, dict):
            raise ScreenReaderEvidenceError
        record = cast(dict[str, object], value)
        if (
            set(record)
            != {
                "phase",
                "outcome",
                "recorded_at_utc",
                "input_actions",
                "previous_sha256",
                "artifacts",
            }
            or record["phase"] not in PHASES
            or record["outcome"] not in _OUTCOMES
            or record["previous_sha256"] != previous
            or type(record["input_actions"]) is not int
            or not 0 <= record["input_actions"] <= 10_000
            or not isinstance(record["recorded_at_utc"], str)
            or _json_bytes(record) != content
        ):
            raise ScreenReaderEvidenceError
        timestamp = datetime.fromisoformat(record["recorded_at_utc"])
        if timestamp.tzinfo is None:
            raise ScreenReaderEvidenceError
        artifacts = record["artifacts"]
        if not isinstance(artifacts, dict) or len(artifacts) > 16:
            raise ScreenReaderEvidenceError
        for relative, expected in artifacts.items():
            if not isinstance(relative, str) or _artifact(root, relative) != expected:
                raise ScreenReaderEvidenceError
        observations.append(record)
        previous = _digest(content)
    return observations


def _artifact(root: Path, relative: str) -> dict[str, object]:
    path = Path(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 3
        or path.parts[0] != "output"
        or not path.parts[1].startswith(("pws0701", "pws0702"))
        or path.as_posix() != relative
    ):
        raise ScreenReaderEvidenceError
    content = _read(root, root / path)
    return {"bytes": len(content), "sha256": _digest(content)}


def status(root: Path, attempt: str, environment: Mapping[str, object]) -> dict[str, object]:
    destination = _attempt_root(root, attempt)
    if not destination.is_dir():
        raise ScreenReaderEvidenceError
    receipt = prepare(root, attempt, environment)
    observations = _observations(root, destination, receipt)
    latest = {cast(str, row["phase"]): row["outcome"] for row in observations}
    pending = [phase for phase in PHASES if latest.get(phase) != "observed"]
    return {
        "task_id": "PWS-0702",
        "binding_sha256": receipt["binding_sha256"],
        "record_count": len(observations),
        "latest_observations": latest,
        "pending": pending,
        "next_action": PHASES[pending[0]] if pending else "All phase observations are recorded.",
        "problems_retained": sum(row["outcome"] == "problem" for row in observations),
        "status": "observations_recorded" if not pending else "incomplete",
        "task_completion_inferred": False,
        "human_assessment_claimed": False,
        "automated_verification": "separate_current_receipts_required",
    }


def record(
    root: Path,
    attempt: str,
    environment: Mapping[str, object],
    *,
    phase: str,
    outcome: str,
    input_actions: int,
    artifacts: Sequence[str] = (),
) -> dict[str, object]:
    """Immediately append one closed observation; retries preserve prior failures."""
    if (
        phase not in PHASES
        or outcome not in _OUTCOMES
        or type(input_actions) is not int
        or not 0 <= input_actions <= 10_000
        or len(artifacts) > 16
        or len(set(artifacts)) != len(artifacts)
    ):
        raise ScreenReaderEvidenceError
    current = status(root, attempt, environment)
    count = cast(int, current["record_count"])
    if count >= _MAX_RECORDS:
        raise ScreenReaderEvidenceError
    destination = _attempt_root(root, attempt)
    previous = destination / (f"observation-{count:04d}.json" if count else "attempt.json")
    observation = {
        "phase": phase,
        "outcome": outcome,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "input_actions": input_actions,
        "previous_sha256": _digest(_read(root, previous, _MAX_RECORD_BYTES)),
        "artifacts": {relative: _artifact(root, relative) for relative in artifacts},
    }
    target = destination / f"observation-{count + 1:04d}.json"
    _write_once(root, target, _json_bytes(observation))
    return status(root, attempt, environment)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise ScreenReaderEvidenceError


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("action", choices=("prepare", "status", "record"))
        parser.add_argument("--attempt", default="v1")
        parser.add_argument("--phase", choices=tuple(PHASES))
        parser.add_argument("--outcome", choices=_OUTCOMES)
        parser.add_argument("--input-actions", type=int)
        parser.add_argument("--artifact", action="append", default=[])
        args = parser.parse_args(argv)
        environment = capture_environment()
        if args.action == "prepare":
            prepare(PROJECT_ROOT, args.attempt, environment)
            result = status(PROJECT_ROOT, args.attempt, environment)
        elif args.action == "record":
            result = record(
                PROJECT_ROOT,
                args.attempt,
                environment,
                phase=args.phase,
                outcome=args.outcome,
                input_actions=args.input_actions,
                artifacts=args.artifact,
            )
        else:
            result = status(PROJECT_ROOT, args.attempt, environment)
        print(_json_bytes(result).decode("ascii"), end="")
        return 0
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
