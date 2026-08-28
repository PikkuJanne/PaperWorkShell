from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "templates/builtin/generic-portal-application-v1.1.0.json"


def _template() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(TEMPLATE_PATH.read_text(encoding="utf-8")))


def test_generic_template_keeps_authority_external_and_scope_procedural() -> None:
    template = _template()
    provenance = cast(dict[str, Any], template["provenance"])
    warnings = {
        warning["warning_id"]: warning
        for warning in cast(list[dict[str, Any]], template["warnings"])
    }

    assert template["scope"] == "document_packaging_only"
    assert provenance["assertion_status"] == "untrusted_embedded_claims"
    assert provenance["trust_policy"] == "external_verification_required"
    assert provenance["source_url"] is None
    assert provenance["source_document_sha256"] is None
    assert "example packaging profile" in warnings["generic-profile-review"]["message"]
    assert (
        "Compare them with the current application instructions"
        in warnings["generic-profile-review"]["message"]
    )
    assert warnings["packaging-only-boundary"]["message"] == (
        "PaperWorkShell checks only the packaging rules configured in this template. "
        "It does not decide eligibility, provide legal advice, verify identity, submit documents, "
        "or guarantee acceptance."
    )


def test_identity_side_guidance_is_reviewable_and_jurisdiction_neutral() -> None:
    template = _template()
    requirements = {
        requirement["requirement_id"]: requirement
        for requirement in cast(list[dict[str, Any]], template["requirements"])
    }
    guidance = cast(str, requirements["identity-evidence"]["guidance"])

    assert "If an identity document has a front and back" in guidance
    assert "check whether the current instructions ask for both sides" in guidance
    assert "Include each side they ask for" in guidance
    assert "always" not in guidance.casefold()
    assert all(
        term not in guidance.casefold()
        for term in ("passport", "national law", "regulation", "statute", "country")
    )


def test_packet_mapping_contains_only_closed_structural_references() -> None:
    packet_path = PROJECT_ROOT / "tests/fixtures/templates/v1/generic-portal-packets.json"
    packet = cast(dict[str, Any], json.loads(packet_path.read_text(encoding="utf-8")))
    scenarios = cast(list[dict[str, Any]], packet["scenarios"])

    assert set(packet) == {"schema_version", "packet_set_id", "template", "scenarios"}
    for scenario in scenarios:
        assert set(scenario) == {"scenario_id", "documents", "expected_findings"}
        for document in cast(list[dict[str, Any]], scenario["documents"]):
            assert set(document) == {"document_id", "fixture_id", "requirement_id"}
            assert not any("path" in member or "filename" in member for member in document)
