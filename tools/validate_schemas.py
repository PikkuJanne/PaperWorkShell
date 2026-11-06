"""Validate every registered PaperWorkShell schema and checked-in instance."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tools.generate_synthetic_fixtures import SCHEMA_PATH as FIXTURE_INDEX_SCHEMA_PATH
from tools.m1_vertical_slice_benchmark import (
    REPORT_SCHEMA_PATH as M1_BENCHMARK_REPORT_SCHEMA_PATH,
)
from tools.m1_vertical_slice_benchmark import validate_report_schema
from tools.release_evidence import schema_messages as release_inventory_schema_messages
from tools.validate_dependencies import (
    INVENTORY_SCHEMA_PATH,
    validate_dependency_fixture_set,
)
from tools.validate_generic_portal_template import validate_generic_portal_template
from tools.validate_manifests import (
    CURRENT_MANIFEST_SCHEMA_PATH,
    MANIFEST_SCHEMA_PATH,
    validate_manifest_fixture_set,
)
from tools.validate_synthetic_fixtures import validate_fixture_index_set
from tools.validate_tasks import SCHEMA_FILE, TASK_FILE, validate_repository
from tools.validate_templates import TEMPLATE_SCHEMA_PATH, validate_template_fixture_set
from tools.validate_worker_rpc import RPC_SCHEMA_PATH, validate_worker_rpc_fixture_set

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
EXPORT_HISTORY_SCHEMA_PATH: Final = Path("schemas/export-history-v1.schema.json")
EXPORT_HISTORY_PACKAGED_SCHEMA_PATH: Final = Path(
    "src/paperworkshell/storage/export-history-v1.schema.json"
)
_MAX_EXPORT_HISTORY_SCHEMA_BYTES: Final = 256 * 1024


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic rendered errors from all registered schema pairs."""

    _, task_issues = validate_repository(root, schema_only=True)
    _, manifest_issues = validate_manifest_fixture_set(root)
    _, fixture_index_issues = validate_fixture_index_set(root)
    _, template_issues = validate_template_fixture_set(root)
    _, generic_portal_issues = validate_generic_portal_template(root)
    _, rpc_issues = validate_worker_rpc_fixture_set(root)
    _, dependency_issues = validate_dependency_fixture_set(root)
    m1_benchmark_issues = validate_report_schema(root)
    return sorted(
        [issue.render() for issue in task_issues]
        + [issue.render() for issue in manifest_issues]
        + [issue.render() for issue in fixture_index_issues]
        + [issue.render() for issue in template_issues]
        + [issue.render() for issue in generic_portal_issues]
        + [issue.render() for issue in rpc_issues]
        + [issue.render() for issue in dependency_issues]
        + [issue.render() for issue in m1_benchmark_issues]
        + _validate_export_history_schema(root)
        + release_inventory_schema_messages(root)
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
    print(
        "OK: validated frozen case-manifest v1/v2 and current v3 with "
        f"{MANIFEST_SCHEMA_PATH.as_posix()} and {CURRENT_MANIFEST_SCHEMA_PATH.as_posix()}."
    )
    print(f"OK: validated synthetic fixture index v1 with {FIXTURE_INDEX_SCHEMA_PATH.as_posix()}.")
    print(f"OK: validated declarative-template v1 with {TEMPLATE_SCHEMA_PATH.as_posix()}.")
    print("OK: validated generic-portal template and count-only synthetic packet scenarios.")
    print(f"OK: validated worker-RPC v1 with {RPC_SCHEMA_PATH.as_posix()}.")
    print(f"OK: validated dependency inventory v1 with {INVENTORY_SCHEMA_PATH.as_posix()}.")
    print(
        "OK: validated historical M1 reports v1/v2 and constrained-profile report v3 with "
        f"{M1_BENCHMARK_REPORT_SCHEMA_PATH.as_posix()}."
    )
    print(f"OK: validated export-history v1 with {EXPORT_HISTORY_SCHEMA_PATH.as_posix()}.")
    print("OK: validated resolved dependency-stage inventory v1 and current/rejected fixtures.")
    return 0


def _validate_export_history_schema(root: Path) -> list[str]:
    """Validate the closed path-free contract and its packaged runtime copy."""

    schema_path = root / EXPORT_HISTORY_SCHEMA_PATH
    packaged_path = root / EXPORT_HISTORY_PACKAGED_SCHEMA_PATH
    try:
        schema_payload = schema_path.read_bytes()
        packaged_payload = packaged_path.read_bytes()
    except OSError:
        return ["EXPORT_HISTORY_SCHEMA_MISSING: required schema copy is unavailable"]
    if (
        not 1 <= len(schema_payload) <= _MAX_EXPORT_HISTORY_SCHEMA_BYTES
        or schema_payload != packaged_payload
    ):
        return ["EXPORT_HISTORY_SCHEMA_IDENTITY_INVALID: canonical and packaged copies differ"]
    try:
        loaded = json.loads(schema_payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError:
        return ["EXPORT_HISTORY_SCHEMA_JSON_INVALID: schema is not strict UTF-8 JSON"]
    if type(loaded) is not dict:
        return ["EXPORT_HISTORY_SCHEMA_OBJECT_INVALID: schema root must be an object"]
    try:
        Draft202012Validator.check_schema(loaded)
        validator = Draft202012Validator(loaded)
    except SchemaError:
        return ["EXPORT_HISTORY_SCHEMA_INVALID: Draft 2020-12 schema validation failed"]
    positive = _export_history_positive_example()
    if list(validator.iter_errors(positive)):
        return ["EXPORT_HISTORY_POSITIVE_INVALID: canonical path-free record was rejected"]
    future = dict(positive)
    future["schema_version"] = 2
    if not list(validator.iter_errors(future)):
        return ["EXPORT_HISTORY_FUTURE_ACCEPTED: unknown future version was accepted"]
    path_bearing = json.loads(json.dumps(positive))
    record = path_bearing["records"][0]
    record["destination_path"] = "C:/private/final-name.pdf"
    if not list(validator.iter_errors(path_bearing)):
        return ["EXPORT_HISTORY_PATH_ACCEPTED: destination path field was accepted"]
    return []


def _export_history_positive_example() -> dict[str, object]:
    return {
        "schema_version": 1,
        "records": [
            {
                "export_id": "50000000-0000-4000-8000-000000000001",
                "record_version": 1,
                "recorded_at": "2026-08-06T12:00:00.000000Z",
                "manifest_revision": 1,
                "manifest_sha256": "a" * 64,
                "semantic_fingerprint": "b" * 64,
                "output_sha256": "c" * 64,
                "output_byte_size": 1,
                "page_count": 1,
                "check_codes": [
                    "PDF_REOPENED",
                    "STRUCTURE_CLEAN",
                    "PAGE_COUNT_MATCHED",
                    "UNENCRYPTED",
                    "ALL_PAGES_RENDERED",
                    "ORDER_ROTATION_MATCHED",
                    "OUTPUT_COMMIT_REOPENED",
                ],
                "unsigned_derivative": False,
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
