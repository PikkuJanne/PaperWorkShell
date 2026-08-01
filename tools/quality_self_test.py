"""Prove that deliberate local quality violations are rejected."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import yaml

from tools.dev import GITLEAKS_VERSION, scan_repository
from tools.validate_tasks import validate_repository

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SelfTestResult:
    """One negative quality check and whether it rejected the violation correctly."""

    code: str
    description: str
    passed: bool


class SelfTestSetupError(Exception):
    """Raised when the negative self-test cannot exercise a required tool."""


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SelfTestSetupError(f"required executable is missing: {name}")
    return executable


def _run_capture(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - callers provide fixed quality-tool argument arrays
        list(arguments),
        cwd=cwd,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        env=None if environment is None else dict(environment),
    )


def _sanitized_git_environment(empty_global_config: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = str(empty_global_config)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _formatting_violation(root: Path) -> SelfTestResult:
    source = root / "format_violation.py"
    source.write_text("VALUES=[1,2,3]\n", encoding="utf-8")
    completed = _run_capture(
        (_required_executable("ruff"), "format", "--check", str(source)),
        cwd=PROJECT_ROOT,
    )
    output = f"{completed.stdout}\n{completed.stderr}".casefold()
    return SelfTestResult(
        "SELF_TEST_FORMAT",
        "Ruff formatting violation",
        completed.returncode == 1 and "would reformat" in output,
    )


def _typing_violation(root: Path) -> SelfTestResult:
    source = root / "typing_violation.py"
    source.write_text(
        'def invalid_return() -> int:\n    return "synthetic-text"\n',
        encoding="utf-8",
    )
    completed = _run_capture(
        (
            _required_executable("mypy"),
            "--config-file",
            str(PROJECT_ROOT / "pyproject.toml"),
            "--no-pretty",
            str(source),
        ),
        cwd=PROJECT_ROOT,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    return SelfTestResult(
        "SELF_TEST_TYPE",
        "mypy return-type violation",
        completed.returncode == 1 and "[return-value]" in output,
    )


def _copy_task_inputs(destination: Path) -> dict[str, Any]:
    for name in ("TASKS.schema.json", "DECISIONS.md"):
        shutil.copy2(PROJECT_ROOT / name, destination / name)
    loaded = yaml.safe_load((PROJECT_ROOT / "TASKS.yaml").read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SelfTestSetupError("TASKS.yaml root is not a mapping")
    return cast(dict[str, Any], loaded)


def _write_tasks(destination: Path, data: dict[str, Any]) -> None:
    (destination / "TASKS.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _schema_violation(root: Path) -> SelfTestResult:
    case_root = root / "schema"
    case_root.mkdir()
    data = _copy_task_inputs(case_root)
    data["frozen_at"] = "not-a-date"
    _write_tasks(case_root, data)
    _, issues = validate_repository(case_root, schema_only=True)
    return SelfTestResult(
        "SELF_TEST_SCHEMA",
        "task-schema date violation",
        any(issue.code == "SCHEMA_VALIDATION" for issue in issues),
    )


def _graph_cycle_violation(root: Path) -> SelfTestResult:
    case_root = root / "cycle"
    case_root.mkdir()
    data = _copy_task_inputs(case_root)
    tasks = cast(list[dict[str, Any]], data["tasks"])
    bootstrap = next(task for task in tasks if task["id"] == "PWS-0001")
    bootstrap["depends_on"] = ["PWS-0002"]
    _write_tasks(case_root, data)
    _, issues = validate_repository(case_root)
    return SelfTestResult(
        "SELF_TEST_GRAPH",
        "task dependency-cycle violation",
        any(issue.code == "TASK_GRAPH_CYCLE" for issue in issues),
    )


def _secret_violation(root: Path) -> SelfTestResult:
    executable = _required_executable("gitleaks")
    version = _run_capture((executable, "version"), cwd=PROJECT_ROOT)
    if version.returncode != 0 or version.stdout.strip() != GITLEAKS_VERSION:
        raise SelfTestSetupError(f"gitleaks {GITLEAKS_VERSION} is required")

    secret_root = root / "secret"
    secret_root.mkdir()
    shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", secret_root / ".gitleaks.toml")
    (secret_root / "baseline.txt").write_text("synthetic baseline\n", encoding="utf-8")
    empty_global_config = secret_root / "empty-global.gitconfig"
    empty_global_config.write_text("", encoding="utf-8")
    empty_template = secret_root / "empty-template"
    empty_hooks = secret_root / "empty-hooks"
    empty_template.mkdir()
    empty_hooks.mkdir()
    git_environment = _sanitized_git_environment(empty_global_config)
    git = _required_executable("git")
    setup_commands = (
        (git, "init", f"--template={empty_template}"),
        (git, "add", ".gitleaks.toml", "baseline.txt"),
        (
            git,
            "-c",
            "user.name=PaperWorkShell Quality",
            "-c",
            "user.email=quality@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={empty_hooks}",
            "commit",
            "-m",
            "synthetic baseline",
        ),
    )
    if any(
        _run_capture(command, cwd=secret_root, environment=git_environment).returncode != 0
        for command in setup_commands
    ):
        raise SelfTestSetupError("temporary Git repository setup failed")

    prefix = "_".join(("PWS", "QUALITY", "CANARY")) + "_"
    suffix = hashlib.sha256(b"paperworkshell-quality-self-test").hexdigest()[:32].upper()
    canary = prefix + suffix
    staged_binary = secret_root / "synthetic.bin"
    staged_binary.write_bytes(
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n" + canary.encode("ascii") + b"\nendobj\n%%EOF\n"
    )
    if (
        _run_capture(
            (git, "add", "synthetic.bin"),
            cwd=secret_root,
            environment=git_environment,
        ).returncode
        != 0
    ):
        raise SelfTestSetupError("temporary Git index setup failed")
    staged_binary.write_bytes(b"\x00clean-working-tree-bytes\x00")

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
        result = scan_repository(secret_root)
    raw_output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
    return SelfTestResult(
        "SELF_TEST_SECRET",
        "Gitleaks staged-binary synthetic-canary violation",
        result == 42 and "SECRET_SCAN_FINDING" in raw_output and canary not in raw_output,
    )


def run_self_tests() -> list[SelfTestResult]:
    """Run every negative case in an isolated temporary directory."""

    with tempfile.TemporaryDirectory(prefix="pws-quality-self-test-") as temporary:
        root = Path(temporary)
        return [
            _formatting_violation(root),
            _typing_violation(root),
            _schema_violation(root),
            _graph_cycle_violation(root),
            _secret_violation(root),
        ]


def main() -> int:
    """Run negative quality cases without echoing their synthetic inputs."""

    try:
        results = run_self_tests()
    except SelfTestSetupError as error:
        print(f"ERROR [SELF_TEST_SETUP] {error}", file=sys.stderr)
        return 1
    failed = [result for result in results if not result.passed]
    for result in results:
        status = "OK" if result.passed else "ERROR"
        outcome = "detected" if result.passed else "not detected"
        print(f"{status} [{result.code}] {result.description} {outcome}.")
    if failed:
        print(
            f"ERROR [SELF_TEST_FAILED] {len(failed)} deliberate violation(s) were not detected.",
            file=sys.stderr,
        )
        return 1
    print("OK: all 5 deliberate quality violations were detected without retaining test data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
