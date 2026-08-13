"""Fail-closed Python process-creation policy for the isolated worker."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

_EXECUTABLE_ENVIRONMENT_NAMES: Final = (
    "PWS_QPDF_EXECUTABLE",
    "PWS_TESSERACT_EXECUTABLE",
)
_CHILD_ENVIRONMENT_ALLOWLIST: Final = frozenset(
    {
        "LOCALAPPDATA",
        "OMP_THREAD_LIMIT",
        "SYSTEMROOT",
        "TEMP",
        "TESSDATA_PREFIX",
        "TMP",
        "WINDIR",
    }
)
_installed_authorities: tuple[str, ...] | None = None


class WorkerProcessCreationDenied(RuntimeError):
    """Sanitized denial that retains no executable, arguments, or paths."""

    def __init__(self) -> None:
        super().__init__("WORKER_PROCESS_CREATION_DENIED")


def _normalized_path(value: object) -> str | None:
    if type(value) is not str or not value or "\x00" in value or not os.path.isabs(value):
        return None
    try:
        return os.path.normcase(os.path.abspath(value))
    except OSError, ValueError:
        return None


def configured_process_authorities(
    environment: Mapping[str, str] = os.environ,
) -> tuple[str, ...]:
    """Read the closed executable authority list from the audited environment."""

    result: list[str] = []
    for name in _EXECUTABLE_ENVIRONMENT_NAMES:
        raw = environment.get(name)
        if raw is None:
            continue
        normalized = _normalized_path(raw)
        if normalized is None or normalized in result:
            raise WorkerProcessCreationDenied from None
        result.append(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True, repr=False)
class WorkerProcessCreationAuditHook:
    """Allow subprocesses only for exact launch-authorized executables."""

    authorities: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.authorities) is not tuple
            or any(_normalized_path(value) != value for value in self.authorities)
            or len(frozenset(self.authorities)) != len(self.authorities)
        ):
            raise WorkerProcessCreationDenied from None

    def __repr__(self) -> str:
        return "WorkerProcessCreationAuditHook(<fixed executable authorities>)"

    def __call__(self, event: str, arguments: tuple[object, ...]) -> None:
        if event == "subprocess.Popen":
            if not self._allows_popen(arguments):
                raise WorkerProcessCreationDenied from None
            return
        if event == "os.system" or event == "os.posix_spawn" or event.startswith("os.spawn"):
            raise WorkerProcessCreationDenied from None

    def _allows_popen(self, arguments: tuple[object, ...]) -> bool:
        if type(arguments) is not tuple or len(arguments) != 4:
            return False
        executable = _normalized_path(arguments[0])
        argv = arguments[1]
        cwd = _normalized_path(arguments[2])
        environment = arguments[3]
        if (
            executable is None
            or executable not in self.authorities
            or cwd != os.path.dirname(executable)
            or not _argv_targets_executable(argv, executable)
            or not isinstance(environment, Mapping)
        ):
            return False
        environment_names: set[str] = set()
        for name, value in environment.items():
            if type(name) is not str or type(value) is not str or "\x00" in name or "\x00" in value:
                return False
            environment_names.add(name.upper())
        return (
            environment_names <= _CHILD_ENVIRONMENT_ALLOWLIST
            and {"TEMP", "TMP"} <= environment_names
            and not {"PATH", "PATHEXT", "COMSPEC"} & environment_names
        )


def _argv_targets_executable(argv: object, executable: str) -> bool:
    """Accept the sequence before, or Windows-rendered text after, Popen validation."""

    if type(argv) in (tuple, list):
        argv_values = cast(tuple[object, ...] | list[object], argv)
        return bool(argv_values) and _normalized_path(argv_values[0]) == executable
    if type(argv) is not str or not argv or any(character in argv for character in "\x00\r\n"):
        return False
    normalized_argv = os.path.normcase(argv)
    executable_prefix = os.path.normcase(subprocess.list2cmdline((executable,)))
    return normalized_argv == executable_prefix or normalized_argv.startswith(
        executable_prefix + " "
    )


def install_worker_process_creation_policy() -> None:
    """Install the immutable allowlist before any optional engine is loaded."""

    global _installed_authorities
    authorities = configured_process_authorities()
    if _installed_authorities is not None:
        if _installed_authorities != authorities:
            raise WorkerProcessCreationDenied from None
        return
    sys.addaudithook(WorkerProcessCreationAuditHook(authorities))
    _installed_authorities = authorities
