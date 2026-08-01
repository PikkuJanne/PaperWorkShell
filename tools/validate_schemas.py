"""Validate every registered PaperWorkShell schema and checked-in instance."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from tools.reference_profile import PROFILE_PATH, SCHEMA_PATH, validate_profile
from tools.validate_manifests import MANIFEST_SCHEMA_PATH, validate_manifest_fixture_set
from tools.validate_tasks import SCHEMA_FILE, TASK_FILE, validate_repository
from tools.validate_worker_rpc import RPC_SCHEMA_PATH, validate_worker_rpc_fixture_set

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic rendered errors from all registered schema pairs."""

    _, task_issues = validate_repository(root, schema_only=True)
    _, profile_issues = validate_profile(root)
    _, manifest_issues = validate_manifest_fixture_set(root)
    _, rpc_issues = validate_worker_rpc_fixture_set(root)
    return sorted(
        [issue.render() for issue in task_issues]
        + [issue.render() for issue in profile_issues]
        + [issue.render() for issue in manifest_issues]
        + [issue.render() for issue in rpc_issues]
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run all local-only repository schema validations."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    arguments = parser.parse_args(argv)
    messages = validation_messages(arguments.root)
    if messages:
        for message in messages:
            print(message, file=sys.stderr)
        return 1
    print(f"OK: validated {TASK_FILE} with {SCHEMA_FILE}.")
    print(f"OK: validated {PROFILE_PATH.as_posix()} with {SCHEMA_PATH.as_posix()}.")
    print(f"OK: validated case-manifest v1 with {MANIFEST_SCHEMA_PATH.as_posix()}.")
    print(f"OK: validated worker-RPC v1 with {RPC_SCHEMA_PATH.as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
