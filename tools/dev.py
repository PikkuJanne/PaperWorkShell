"""Stable, cross-platform developer command dispatcher."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

COMMANDS: Final[dict[str, tuple[str, ...]]] = {
    "format": ("ruff", "format", "."),
    "format-check": ("ruff", "format", "--check", "."),
    "lint": ("ruff", "check", "."),
    "typecheck": ("mypy", "src", "tools", "tests"),
    "test-unit": ("pytest", "tests/unit"),
    "validate-schemas": ("python", "tools/validate_tasks.py", "--schema-only"),
    "validate-task-graph": ("python", "tools/validate_tasks.py"),
}

CHECK_COMMANDS: Final = (
    "format-check",
    "lint",
    "typecheck",
    "test-unit",
    "validate-schemas",
    "validate-task-graph",
)


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise FileNotFoundError("uv")
    return executable


def _run(arguments: Sequence[str]) -> int:
    rendered = subprocess.list2cmdline(list(arguments))
    print(f"> {rendered}", flush=True)
    completed = subprocess.run(  # noqa: S603 - arguments are a fixed internal tuple
        list(arguments),
        cwd=PROJECT_ROOT,
        check=False,
        shell=False,
    )
    return completed.returncode


def run_named(command: str) -> int:
    """Run one named command and return its process exit code."""

    uv = _uv_executable()
    if command == "bootstrap":
        return _run((uv, "sync", "--locked"))
    if command == "check":
        for name in CHECK_COMMANDS:
            result = run_named(name)
            if result != 0:
                return result
        return 0

    tool_arguments = COMMANDS[command]
    return _run((uv, "run", "--locked", "--no-sync", *tool_arguments))


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and run the selected developer command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=sorted((*COMMANDS, "bootstrap", "check")),
        help="developer command to run",
    )
    arguments = parser.parse_args(argv)
    try:
        return run_named(arguments.command)
    except FileNotFoundError:
        print(
            "ERROR [TOOL_MISSING] uv is required; see README.md for installation instructions.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
