"""Standards and semantic checks of synthetic resolved-inventory SBOM rendering."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from tools.release_sbom import ReleaseSbomError, build_cyclonedx, build_notices, build_spdx

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "fixtures/release-sbom/schema"


def synthetic_inventory() -> dict[str, Any]:
    """Reviewed-shape data with synthetic contents, never a real release claim."""
    components = []
    for identifier, scope, relationship, dependencies, expression in (
        ("paperworkshell", "runtime", "root", ["direct-lib", "build-tool"], "NOASSERTION"),
        ("direct-lib", "runtime", "direct", ["embedded-codec"], "MIT AND LicenseRef-Synthetic"),
        ("embedded-codec", "runtime", "embedded", [], "BSD-2-Clause"),
        ("build-tool", "build", "external", [], "MIT"),
    ):
        text = (
            "Synthetic licence/notice text for "
            + identifier
            + ".\r\nAll test data is synthetic.\r\n"
        )
        components.append(
            {
                "component_id": identifier,
                "name": identifier,
                "version": "0.0.0" if identifier == "paperworkshell" else "1.2.3",
                "type": "application"
                if identifier in {"paperworkshell", "build-tool"}
                else "library",
                "scope": scope,
                "supplier": "Synthetic contributors",
                "purl": None
                if identifier == "paperworkshell"
                else "pkg:generic/" + identifier + "@1.2.3",
                "license_expression": expression,
                "license_texts": []
                if identifier == "paperworkshell"
                else [
                    {
                        "path": "notices/" + identifier + "/LICENSE.txt",
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "text": text,
                    }
                ],
                "source_url": "https://example.invalid/source/" + identifier + "/1.2.3",
                "artifact_url": "https://example.invalid/artifacts/" + identifier + ".zip",
                "artifact_sha256": hashlib.sha256(("archive:" + identifier).encode()).hexdigest(),
                "dependencies": dependencies,
                "relationship": relationship,
            }
        )
    files = []
    for path, owner, data in (
        ("runtime/main.py", "paperworkshell", b"synthetic app bytes"),
        ("runtime/direct.dll", "direct-lib", b"synthetic direct bytes"),
        ("runtime/direct-notice.txt", "direct-lib", b"synthetic notice bytes"),
        ("runtime/codec.dll", "embedded-codec", b"synthetic embedded bytes"),
    ):
        files.append(
            {
                "path": path,
                "component_id": owner,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "sha1": hashlib.sha1(data, usedforsecurity=False).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "candidate": {
            "id": "synthetic-stage-v1",
            "version": "0.0.0",
            "created_utc": "2026-11-03T12:00:00Z",
            "source_revision": "working-tree-sha256:" + "a" * 64,
            "source_sha256": "a" * 64,
        },
        "target": {"os": "Windows", "architecture": "x86_64", "python_version": "3.14.4"},
        "components": components,
        "files": files,
        "license_refs": [
            {
                "id": "LicenseRef-Synthetic",
                "name": "Synthetic test terms",
                "text": "Synthetic custom licence definition.\n",
            }
        ],
    }


def _official_validator(name: str) -> Draft7Validator:
    # A plain referencing Registry has no remote retriever. Every reference must
    # resolve from these explicitly registered local resources.
    registry: Registry[Any] = Registry()
    for filename in (
        "cyclonedx-1.6.schema.json",
        "cyclonedx-spdx.schema.json",
        "cyclonedx-jsf-0.82.schema.json",
        "spdx-2.3.schema.json",
    ):
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema, default_specification=DRAFT7)
        identifier = schema["$id"]
        registry = registry.with_resource(identifier, resource)
        if identifier.startswith("http:"):
            registry = registry.with_resource(identifier.replace("http:", "https:", 1), resource)
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    return Draft7Validator(schema, registry=registry, format_checker=FormatChecker())


def test_vendored_schema_bytes_match_public_tagged_provenance() -> None:
    provenance = json.loads((SCHEMA_ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["schema_version"] == 1
    assert len(provenance["files"]) == 6
    for record in provenance["files"]:
        raw = (SCHEMA_ROOT / record["file"]).read_bytes()
        assert len(raw) == record["size_bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        assert record["source_url"].startswith(
            (
                "https://raw.githubusercontent.com/CycloneDX/specification/1.6/",
                "https://raw.githubusercontent.com/spdx/spdx-spec/v2.3/",
            )
        )


@pytest.mark.parametrize(
    "generator,schema",
    [(build_cyclonedx, "cyclonedx-1.6.schema.json"), (build_spdx, "spdx-2.3.schema.json")],
)
def test_sbom_validates_against_unmodified_official_schema(generator: Any, schema: str) -> None:
    document = generator(synthetic_inventory())
    _official_validator(schema).validate(document)


def test_cyclonedx_contains_every_owned_file_once_with_distinct_subject_hashes() -> None:
    inventory = synthetic_inventory()
    document = build_cyclonedx(inventory)
    components = [document["metadata"]["component"], *document["components"]]
    assert {component["bom-ref"] for component in components} == {
        "component:" + component["component_id"] for component in inventory["components"]
    }
    observed = []
    for component in components:
        identifier = component["bom-ref"].removeprefix("component:")
        source = next(
            item for item in inventory["components"] if item["component_id"] == identifier
        )
        assert component["hashes"] == [{"alg": "SHA-256", "content": source["artifact_sha256"]}]
        for file in component.get("components", []):
            original = next(item for item in inventory["files"] if item["path"] == file["name"])
            assert original["component_id"] == identifier
            assert file["hashes"] == [
                {"alg": "SHA-256", "content": original["sha256"]},
                {"alg": "SHA-1", "content": original["sha1"]},
            ]
            assert file["hashes"][0] != component["hashes"][0]
            observed.append(file["name"])
    assert sorted(observed) == sorted(file["path"] for file in inventory["files"])
    assert len(observed) == len(set(observed))
    assert "licenses" not in document["metadata"]["component"]


def test_cyclonedx_dependency_edges_and_build_embedded_roles_are_explicit() -> None:
    document = build_cyclonedx(synthetic_inventory())
    by_ref = {entry["ref"]: entry["dependsOn"] for entry in document["dependencies"]}
    assert by_ref["component:paperworkshell"] == ["component:build-tool", "component:direct-lib"]
    assert by_ref["component:direct-lib"] == ["component:embedded-codec"]
    assert by_ref["component:embedded-codec"] == []
    for identifier, expected_name, expected_value in (
        ("build-tool", "paperworkshell:scope", "build"),
        ("embedded-codec", "paperworkshell:relationship", "embedded"),
    ):
        component = next(
            item for item in document["components"] if item["bom-ref"] == "component:" + identifier
        )
        assert {"name": expected_name, "value": expected_value} in component["properties"]


def test_spdx_checksums_verification_codes_and_contains_relationships_match_owned_files() -> None:
    inventory = synthetic_inventory()
    document = build_spdx(inventory)
    assert len(document["files"]) == len(inventory["files"])
    for package in document["packages"]:
        identifier = package["SPDXID"].removeprefix("SPDXRef-Package-")
        original = next(
            component
            for component in inventory["components"]
            if component["component_id"] == identifier
        )
        owned = [file for file in inventory["files"] if file["component_id"] == identifier]
        assert package["checksums"] == [
            {"algorithm": "SHA256", "checksumValue": original["artifact_sha256"]}
        ]
        assert package["filesAnalyzed"] is bool(owned)
        if owned:
            expected = hashlib.sha1(
                "".join(sorted(file["sha1"] for file in owned)).encode("ascii"),
                usedforsecurity=False,
            ).hexdigest()
            assert package["packageVerificationCode"] == {"packageVerificationCodeValue": expected}
            assert package["licenseInfoFromFiles"] == ["NOASSERTION"]
            relations = [
                relationship
                for relationship in document["relationships"]
                if relationship["spdxElementId"] == package["SPDXID"]
                and relationship["relatedSpdxElement"].startswith("SPDXRef-File-")
            ]
            assert len(relations) == len(owned)
            assert all(relation["relationshipType"] == "CONTAINS" for relation in relations)
        else:
            assert "packageVerificationCode" not in package
            assert "licenseInfoFromFiles" not in package
    assert {
        tuple(item.values())
        for item in document["relationships"]
        if item["relationshipType"] == "BUILD_DEPENDENCY_OF"
    } == {("SPDXRef-Package-build-tool", "BUILD_DEPENDENCY_OF", "SPDXRef-Package-paperworkshell")}
    assert {
        "spdxElementId": "SPDXRef-Package-direct-lib",
        "relationshipType": "CONTAINS",
        "relatedSpdxElement": "SPDXRef-Package-embedded-codec",
    } in document["relationships"]


def test_custom_licence_definitions_and_original_text_survive_all_outputs() -> None:
    inventory = synthetic_inventory()
    cdx, spdx, notices = build_cyclonedx(inventory), build_spdx(inventory), build_notices(inventory)
    reference = inventory["license_refs"][0]
    assert spdx["hasExtractedLicensingInfos"] == [
        {
            "licenseId": reference["id"],
            "name": reference["name"],
            "extractedText": reference["text"],
        }
    ]
    component = next(
        item for item in cdx["components"] if item["bom-ref"] == "component:direct-lib"
    )
    assert any(
        item["license"]["text"]["content"] == reference["text"]
        for item in component["evidence"]["licenses"]
    )
    assert reference["text"] in notices
    for original in inventory["components"]:
        for text in original["license_texts"]:
            assert text["text"] in notices
            assert text["sha256"] in notices
    assert "No final PaperWorkShell product licence is asserted" in notices


@pytest.mark.parametrize("encoding", ["latin-1", "utf-8-sig"])
def test_non_utf8_original_licence_asset_is_bound_without_losing_text(encoding: str) -> None:
    inventory = synthetic_inventory()
    text = inventory["components"][1]["license_texts"][0]
    text["text"] = "Synthetic copyright \u00a9 and licence terms.\r\n"
    text["encoding"] = encoding
    text["sha256"] = hashlib.sha256(text["text"].encode(encoding)).hexdigest()
    cdx, spdx, notices = build_cyclonedx(inventory), build_spdx(inventory), build_notices(inventory)
    _official_validator("cyclonedx-1.6.schema.json").validate(cdx)
    _official_validator("spdx-2.3.schema.json").validate(spdx)
    component = next(
        item for item in cdx["components"] if item["bom-ref"] == "component:direct-lib"
    )
    licence = component["evidence"]["licenses"][0]["license"]
    assert licence["text"]["content"] == text["text"]
    assert {"name": "paperworkshell:original_asset_sha256", "value": text["sha256"]} in licence[
        "properties"
    ]
    assert {"name": "paperworkshell:original_asset_encoding", "value": encoding} in licence[
        "properties"
    ]
    package = next(
        item for item in spdx["packages"] if item["SPDXID"] == "SPDXRef-Package-direct-lib"
    )
    assert encoding in package["licenseComments"]
    assert text["sha256"] in package["licenseComments"]
    assert text["text"] in notices
    assert "Original asset encoding: " + encoding in notices
    assert "Original asset SHA-256: " + text["sha256"] in notices


def test_wrong_original_licence_encoding_cannot_reuse_the_asset_digest() -> None:
    inventory = synthetic_inventory()
    text = inventory["components"][1]["license_texts"][0]
    text["text"] = "Synthetic copyright \u00a9."
    text["sha256"] = hashlib.sha256(text["text"].encode("latin-1")).hexdigest()
    for generator in (build_cyclonedx, build_spdx, build_notices):
        with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$"):
            generator(inventory)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "latin-1"])
def test_legal_form_feed_survives_all_renderers_with_original_byte_identity(encoding: str) -> None:
    inventory = synthetic_inventory()
    licence = inventory["components"][1]["license_texts"][0]
    licence["text"] = "Synthetic copyright \u00a9 and first page.\r\n\f\nSynthetic second page.\r\n"
    licence["encoding"] = encoding
    licence["sha256"] = hashlib.sha256(licence["text"].encode(encoding)).hexdigest()
    reference = inventory["license_refs"][0]
    reference["text"] = "Synthetic custom terms, first page.\n\f\nSynthetic second page.\n"
    before = copy.deepcopy(inventory)
    cdx, spdx, notices = build_cyclonedx(inventory), build_spdx(inventory), build_notices(inventory)
    _official_validator("cyclonedx-1.6.schema.json").validate(cdx)
    _official_validator("spdx-2.3.schema.json").validate(spdx)
    component = next(
        item for item in cdx["components"] if item["bom-ref"] == "component:direct-lib"
    )
    evidence = [item["license"] for item in component["evidence"]["licenses"]]
    original = next(item for item in evidence if item["text"]["content"] == licence["text"])
    assert {"name": "paperworkshell:original_asset_sha256", "value": licence["sha256"]} in original[
        "properties"
    ]
    assert {"name": "paperworkshell:original_asset_encoding", "value": encoding} in original[
        "properties"
    ]
    assert any(item["text"]["content"] == reference["text"] for item in evidence)
    assert spdx["hasExtractedLicensingInfos"][0]["extractedText"] == reference["text"]
    package = next(
        item for item in spdx["packages"] if item["SPDXID"] == "SPDXRef-Package-direct-lib"
    )
    assert licence["sha256"] in package["licenseComments"]
    assert encoding in package["licenseComments"]
    assert licence["text"] in notices and reference["text"] in notices
    assert "Original asset SHA-256: " + licence["sha256"] in notices
    assert "Original asset encoding: " + encoding in notices
    assert inventory == before


@pytest.mark.parametrize(
    "codepoint", [value for value in range(32) if value not in {9, 10, 12, 13}]
)
@pytest.mark.parametrize("field", ["license_text", "license_ref_text"])
def test_legal_text_still_rejects_other_c0_controls(codepoint: int, field: str) -> None:
    inventory = synthetic_inventory()
    text = "Synthetic legal text before " + chr(codepoint) + " after.\n"
    if field == "license_text":
        licence = inventory["components"][1]["license_texts"][0]
        licence["text"] = text
        licence["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        inventory["license_refs"][0]["text"] = text
    for generator in (build_cyclonedx, build_spdx, build_notices):
        with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$"):
            generator(inventory)


@pytest.mark.parametrize("field", ["name", "supplier", "source_url", "license_expression"])
def test_form_feed_remains_disallowed_in_single_line_metadata(field: str) -> None:
    inventory = synthetic_inventory()
    inventory["components"][1][field] += "\f"
    for generator in (build_cyclonedx, build_spdx, build_notices):
        with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$"):
            generator(inventory)


@pytest.mark.parametrize("generator", [build_cyclonedx, build_spdx, build_notices])
def test_generation_is_order_independent_deterministic_and_does_not_mutate_input(
    generator: Any,
) -> None:
    inventory = synthetic_inventory()
    before = copy.deepcopy(inventory)
    first = generator(inventory)
    assert inventory == before
    inventory["components"].reverse()
    inventory["files"].reverse()
    inventory["license_refs"].reverse()
    for component in inventory["components"]:
        component["dependencies"].reverse()
        component["license_texts"].reverse()
    assert generator(inventory) == first


def test_changed_staged_bytes_change_document_identity_even_with_the_same_source() -> None:
    inventory = synthetic_inventory()
    first = build_spdx(inventory)["documentNamespace"]
    cdx_serial = build_cyclonedx(inventory)["serialNumber"]
    inventory["files"][0]["sha256"] = "f" * 64
    assert build_spdx(inventory)["documentNamespace"] != first
    assert build_cyclonedx(inventory)["serialNumber"] != cdx_serial


def test_absent_artifact_url_does_not_guess_a_download_location() -> None:
    inventory = synthetic_inventory()
    del inventory["components"][1]["artifact_url"]
    component = next(
        item
        for item in build_cyclonedx(inventory)["components"]
        if item["bom-ref"] == "component:direct-lib"
    )
    assert all(reference["type"] != "distribution" for reference in component["externalReferences"])
    package = next(
        item
        for item in build_spdx(inventory)["packages"]
        if item["SPDXID"] == "SPDXRef-Package-direct-lib"
    )
    assert package["downloadLocation"] == "NOASSERTION"


def test_missing_optional_relationship_is_derived_from_dependency_graph() -> None:
    inventory = synthetic_inventory()
    for component in inventory["components"]:
        del component["relationship"]
    component = next(
        item
        for item in build_cyclonedx(inventory)["components"]
        if item["bom-ref"] == "component:direct-lib"
    )
    assert {"name": "paperworkshell:relationship", "value": "direct"} in component["properties"]


@pytest.mark.parametrize("version", [0, 2, True, 1.0])
@pytest.mark.parametrize("generator", [build_cyclonedx, build_spdx, build_notices])
def test_legacy_future_and_noninteger_inventory_versions_fail(
    version: object, generator: Any
) -> None:
    inventory = synthetic_inventory()
    inventory["schema_version"] = version
    with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$"):
        generator(inventory)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate-component",
        "missing-root",
        "unknown-dependency",
        "duplicate-file",
        "case-collision",
        "unknown-owner",
        "undefined-license",
        "changed-license-text",
        "root-version",
        "invalid-date",
        "unknown-platform",
        "nonroot-noassertion",
    ],
)
def test_inconsistent_resolved_evidence_cannot_be_rendered(change: str) -> None:
    inventory = synthetic_inventory()
    if change == "duplicate-component":
        inventory["components"].append(copy.deepcopy(inventory["components"][0]))
    elif change == "missing-root":
        inventory["components"].pop(0)
    elif change == "unknown-dependency":
        inventory["components"][1]["dependencies"].append("unknown")
    elif change == "duplicate-file":
        inventory["files"].append(copy.deepcopy(inventory["files"][0]))
    elif change == "case-collision":
        duplicate = copy.deepcopy(inventory["files"][0])
        duplicate["path"] = duplicate["path"].upper()
        inventory["files"].append(duplicate)
    elif change == "unknown-owner":
        inventory["files"][0]["component_id"] = "unknown"
    elif change == "undefined-license":
        inventory["components"][1]["license_expression"] = "LicenseRef-Undefined"
    elif change == "changed-license-text":
        inventory["components"][1]["license_texts"][0]["text"] += "changed"
    elif change == "root-version":
        inventory["components"][0]["version"] = "1.0.0"
    elif change == "invalid-date":
        inventory["candidate"]["created_utc"] = "2026-02-30T00:00:00Z"
    elif change == "unknown-platform":
        inventory["target"]["os"] = "Linux"
    else:
        inventory["components"][1]["license_expression"] = "NOASSERTION"
    for generator in (build_cyclonedx, build_spdx, build_notices):
        with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$"):
            generator(inventory)


@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/private/document",
        "../escape",
        "/home/private/document",
        "a/../b",
        "a//b",
        "runtime/CON.txt",
        "a\\b",
        "a/file.",
        "a/file ",
    ],
)
def test_paths_cannot_disclose_private_locations_or_unsafe_staging_names(path: str) -> None:
    inventory = synthetic_inventory()
    inventory["files"][0]["path"] = path
    with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$") as raised:
        build_spdx(inventory)
    assert path not in str(raised.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "file:///private/data"),
        ("source_url", "https://user:secret@example.invalid/path"),
        ("source_url", "https://example.invalid/path?token=private"),
        ("name", "Private C:\\Users\\person\\secret"),
        ("supplier", "private\nfield"),
        ("artifact_sha256", "PRIVATE_HASH_TOKEN"),
    ],
)
def test_invalid_public_metadata_fails_without_echoing_values(field: str, value: str) -> None:
    inventory = synthetic_inventory()
    inventory["components"][1][field] = value
    with pytest.raises(ReleaseSbomError, match="^RELEASE_SBOM_INPUT$") as raised:
        build_cyclonedx(inventory)
    assert value not in str(raised.value)
