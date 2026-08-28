from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "templates/builtin/generic-portal-application-v1.1.0.json"


def _template() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(TEMPLATE_PATH.read_text(encoding="utf-8")))


def test_generic_template_declares_the_complete_reviewed_packaging_profile() -> None:
    template = _template()
    requirements = cast(list[dict[str, Any]], template["requirements"])

    assert template["schema_version"] == 1
    assert template["template_id"] == "generic-portal-application"
    assert template["template_version"] == "1.1.0"
    assert template["requirement_order"] == [
        "application-form",
        "identity-evidence",
        "address-evidence",
        "supporting-evidence",
    ]
    assert {
        requirement["requirement_id"]: requirement["count"] for requirement in requirements
    } == {
        "application-form": {"unit": "documents", "minimum": 1, "maximum": 1},
        "identity-evidence": {"unit": "documents", "minimum": 1, "maximum": 2},
        "address-evidence": {"unit": "documents", "minimum": 1, "maximum": 2},
        "supporting-evidence": {"unit": "documents", "minimum": 1, "maximum": 10},
    }

    output = cast(dict[str, Any], template["output"])
    assert output["allowed_modes"] == ["combined_pdf", "separate_pdfs", "zip"]
    assert output["default_mode"] == "combined_pdf"
    assert output["encryption_policy"] == "must_be_unencrypted"
    assert output["flatten_policy"] == "required"
    assert output["limits"] == {
        "maximum_file_count": 4,
        "maximum_total_bytes": 10_485_760,
        "maximum_file_bytes": 5_242_880,
        "maximum_pages_per_file": 100,
    }
    filename_policy = cast(dict[str, Any], output["filename_policy"])
    assert filename_policy["pattern"] == "safe_static_stem_with_counter_v1"
    assert filename_policy["package_stem"] == "generic-application-packet"
    assert filename_policy["collision_policy"] == "append_counter"
    assert filename_policy["extension_policy"] == "derive_from_output_mode"
    assert filename_policy["requirement_stems"] == [
        {"requirement_id": "application-form", "stem": "application-form"},
        {"requirement_id": "identity-evidence", "stem": "identity-evidence"},
        {"requirement_id": "address-evidence", "stem": "address-evidence"},
        {"requirement_id": "supporting-evidence", "stem": "supporting-evidence"},
    ]


def test_generic_template_has_review_only_confirmations_and_a_bounded_lifecycle() -> None:
    template = _template()
    confirmations = cast(list[dict[str, Any]], template["confirmations"])

    assert [confirmation["confirmation_id"] for confirmation in confirmations] == [
        "current-instructions-reviewed",
        "identity-sides-reviewed",
        "pages-reviewed",
        "package-arrangement-reviewed",
    ]
    assert all(confirmation["actor"] == "user" for confirmation in confirmations)
    assert all(confirmation["effect"] == "records_review_only" for confirmation in confirmations)
    assert template["lifecycle"] == {
        "verified_on": "2026-08-28",
        "review_due_on": "2027-02-28",
        "expires_on": "2027-08-28",
    }
