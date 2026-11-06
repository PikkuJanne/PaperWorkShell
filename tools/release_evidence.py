"""Validate and freeze one exact, offline Windows dependency staging candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

from tools.release_artifacts import (
    Artifact,
    ReleaseArtifactError,
    file_hashes,
    plain_path,
    safe_relative,
    verify_artifact,
)
from tools.release_sbom import ReleaseSbomError, build_cyclonedx, build_notices, build_spdx
from tools.release_staging import canonical_json, verify_files, write_package

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA_PATH: Final = Path("schemas/resolved-dependency-inventory-v1.schema.json")
FIXTURES: Final = Path("tests/fixtures/release-inventory/v1")
MAX_INVENTORY_BYTES: Final = 32 * 1024 * 1024
MAX_SCHEMA_BYTES: Final = 128 * 1024
_HASH: Final = re.compile(r"[0-9a-f]{64}\Z")
_EXCLUDED_COMPONENTS: Final = frozenset(
    {
        "ghostscript",
        "mupdf",
        "poppler",
        "pyside6-addons",
        "qt-pdf",
        "pi-heif",
        "jbigkit",
        "msys2-jbigkit",
    }
)
_EXCLUDED_HASHES: Final = frozenset(
    {
        "6d1657e08318630738fb0841ee28bbe7272b41b3b2ef93adaa228c4cd10092c6",
        "832f84eb1476a77e522bdc1715386b68f24423d35da084cd1dde319aacbba316",
        "430791ac820cfb315c820f7c19703609a59f21b8151345ca11e6127efbfaec0d",
        "39b79bb01f16b7e777b8803f539354f394e99d0dcfc4f5f7f1374e02aa2c31a7",
        # Authenticated historical inputs may be retained in the input store,
        # but their complete archives/DLL cannot re-enter the payload under an alias.
        "ba96ae44888e0185281e937633a743ea90d5a196c6000f82565ebb0580012d40",
        "b2d444f2e66624d68e9c6b211e28a76e22fff5fcabcfff4deac18b529b7d4137",
        "ce0eb686a9b03a8586d7d1f833fadcbe67ad2f6ff8538c3f431d4edb072f935d",
        "334e156affd0052de8ae549ec7044d3b2cdb1f0c72569659619fb8e820e5d878",
    }
)


class ReleaseEvidenceError(ValueError):
    """Closed release error code without supplied paths, values or library text."""


def _fail(code: str) -> NoReturn:
    raise ReleaseEvidenceError(code)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _fail("RELEASE_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value: str) -> NoReturn:
    _fail("RELEASE_JSON_NUMBER")


def load_json(path: Path, maximum: int = MAX_INVENTORY_BYTES) -> dict[str, Any]:
    plain_path(path)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        _fail("RELEASE_INPUT_LIMIT")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=_constant,
        parse_float=_constant,
    )
    if not isinstance(value, dict):
        _fail("RELEASE_INPUT_OBJECT")
    return cast(dict[str, Any], value)


def validate_data(inventory: Mapping[str, Any], *, root: Path = PROJECT_ROOT) -> None:
    """Validate the versioned shape, graph and distribution obligations offline."""
    schema = load_json(root / SCHEMA_PATH, MAX_SCHEMA_BYTES)
    Draft202012Validator.check_schema(schema)
    if next(Draft202012Validator(schema).iter_errors(inventory), None) is not None:
        _fail("RELEASE_INVENTORY_SCHEMA")
    # These renderers also validate identifiers, text bounds, LicenseRef closure,
    # per-file owners and artifact-vs-file hash subjects without filesystem access.
    build_cyclonedx(inventory)
    build_spdx(inventory)
    components = {item["component_id"]: item for item in inventory["components"]}
    inputs = inventory["input_artifacts"]
    input_hashes = {item["sha256"] for item in inputs}
    if len({item["name"].casefold() for item in inputs}) != len(inputs):
        _fail("RELEASE_INPUT_COLLISION")
    for item in inputs:
        if safe_relative(item["name"]) != Path(item["name"]).name:
            _fail("RELEASE_INPUT_NAME")
        url = item["url"]
        if url is None:
            if item.get("origin") not in {"local-build", "derived-source"} or item.get(
                "recipe_path"
            ) not in {review["path"] for review in inventory["review_inputs"]}:
                _fail("RELEASE_INPUT_SOURCE")
            continue
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or re.search(
                r"(?<![A-Za-z0-9])[A-Za-z]:[/\\]|file://|\\\\|/(?:Users|home)/", unquote(url), re.I
            )
        ):
            _fail("RELEASE_INPUT_SOURCE")
    if any(item["artifact_sha256"] not in input_hashes for item in components.values()):
        _fail("RELEASE_COMPONENT_INPUT")
    obligations = {item["component_id"]: item for item in inventory["obligations"]}
    if len(obligations) != len(inventory["obligations"]) or set(obligations) != set(components):
        _fail("RELEASE_OBLIGATION_CLOSURE")
    files = {item["path"]: item for item in inventory["files"]}
    for component_id, component in components.items():
        if component_id in _EXCLUDED_COMPONENTS:
            _fail("RELEASE_EXCLUDED_COMPONENT")
        expression = component["license_expression"]
        # Only the selected runtime-exception route permits GPL-labelled native
        # runtime code here. LGPL/MPL remain source/replacement-gated below.
        if component["scope"] == "runtime":
            without_exception = expression.replace("GPL-3.0-or-later WITH GCC-exception-3.1", "")
            if re.search(r"(?<!L)GPL-[0-9]", without_exception):
                _fail("RELEASE_DISTRIBUTION_DECISION_REQUIRED")
        obligation = obligations[component_id]
        owned = [item for item in files.values() if item["component_id"] == component_id]
        if component_id != "paperworkshell" and (
            (component["scope"] == "runtime" and component.get("relationship") != "external")
            or owned
        ):
            if not component["license_texts"]:
                _fail("RELEASE_NOTICE_MISSING")
        for text in component["license_texts"]:
            record = files.get(text["path"])
            if record is None or record["sha256"] != text["sha256"]:
                _fail("RELEASE_NOTICE_MISSING")
        source_required = component["scope"] == "runtime" and (
            "LGPL-" in expression or "MPL-" in expression or "GCC-exception" in expression
        )
        if source_required and not obligation["source_paths"]:
            _fail("RELEASE_SOURCE_MISSING")
        if any(path not in files for path in obligation["source_paths"]):
            _fail("RELEASE_SOURCE_MISSING")
        if component["scope"] == "runtime" and "LGPL-" in expression:
            path = obligation["replacement_instructions_path"]
            if path is None or path not in files:
                _fail("RELEASE_REPLACEMENT_MISSING")
    for path, record in files.items():
        if record["sha256"] in _EXCLUDED_HASHES:
            _fail("RELEASE_EXCLUDED_FILE")
        safe_relative(path)
        if path.split("/", 1)[0] not in {"runtime", "sources", "notices", "evidence"}:
            _fail("RELEASE_STAGE_SCOPE")
        lower = path.casefold()
        if lower.endswith(("/rng2schtrn.xsl", "/xsd2schtrn.xsl", "/libjbig-0.dll")):
            _fail("RELEASE_EXCLUDED_FILE")
        if lower.startswith("runtime/") and any(
            part in lower
            for part in (
                "/pi_heif/",
                "/_pi_heif.",
                "/libheif-",
                "/libde265-",
                "/pyside6/qt6pdf",
                "/pyside6/qtpdf",
                "/pyside6/qtnetwork",
                "/pyside6/qt6network",
                "/pyside6/qtqml",
                "/pyside6/qt6qml",
                "/pyside6/qtquick",
                "/pyside6/qt6quick",
                "/pyside6/qtdesigner",
                "/pyside6/qt6designer",
                "/pyside6_addons-",
                "/gswin64c.exe",
            )
        ):
            _fail("RELEASE_EXCLUDED_FILE")
    if inventory["target"]["python_version"] != "3.14.6":
        _fail("RELEASE_TARGET")
    required_review = "packaging/release-reviews/lxml-exclusions-v1.json"
    review_inputs = {item["path"]: item["sha256"] for item in inventory["review_inputs"]}
    if required_review not in review_inputs:
        _fail("RELEASE_APPROVAL_MISSING")


def validate_stage(
    inventory: Mapping[str, Any], stage: Path, artifacts: Path, *, root: Path = PROJECT_ROOT
) -> dict[str, object]:
    validate_data(inventory, root=root)
    verified: list[dict[str, object]] = []
    for item in inventory["input_artifacts"]:
        expected = Artifact(item["name"], item["url"] or "", item["size_bytes"], item["sha256"])
        verify_artifact(artifacts / expected.name, expected)
        verified.append(
            {key: item[key] for key in ("name", "url", "size_bytes", "sha256", "scope")}
        )
    for item in inventory["review_inputs"]:
        relative = safe_relative(item["path"])
        if file_hashes(root / relative)[1] != item["sha256"]:
            _fail("RELEASE_REVIEW_CHANGED")
    verify_files(stage, inventory["files"])
    # The staged source, local data and notices have explicit exact byte identities;
    # this verifies those copies, not merely existence of a URL or a source offer.
    return {
        "schema_version": 1,
        "candidate_id": inventory["candidate"]["id"],
        "status": "pass",
        "source_sha256": inventory["candidate"]["source_sha256"],
        "verified_input_artifacts": verified,
        "verified_staged_files": len(inventory["files"]),
        "verified_components": len(inventory["components"]),
        "verified_stage_bytes": sum(item["size_bytes"] for item in inventory["files"]),
        "scope": "Internal dependency staging candidate; installer/signing/product-release approval remain separately gated",
    }


def freeze_evidence(
    inventory: Mapping[str, Any],
    stage: Path,
    artifacts: Path,
    output: Path,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    """Create new sidecars and one verified ZIP; never replace earlier evidence."""
    report = validate_stage(inventory, stage, artifacts, root=root)
    plain_path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError
    output.parent.mkdir(parents=True, exist_ok=True)
    # Sidecars are outside the package's exhaustive file inventory. No BOM hashes
    # itself, and the notices' inventory digest has no circular package dependency.
    payloads = {
        "resolved-inventory.json": canonical_json(inventory),
        "bom.cdx.json": canonical_json(build_cyclonedx(inventory)),
        "bom.spdx.json": canonical_json(build_spdx(inventory)),
        "THIRD_PARTY_NOTICES.txt": build_notices(inventory).encode("utf-8"),
        "input-verification.json": canonical_json(report),
    }
    temporary = Path(tempfile.mkdtemp(prefix="pws-dependency-freeze-", dir=output.parent))
    try:
        for name, raw in payloads.items():
            with (temporary / name).open("xb") as stream:
                stream.write(raw)
        packaged = write_package(stage, inventory["files"], temporary / "dependency-stage.zip")
        hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()}
        hashes["dependency-stage.zip"] = cast(str, packaged["sha256"])
        with (temporary / "SHA256SUMS").open("x", encoding="ascii", newline="\n") as stream:
            stream.writelines(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
        verify_files(stage, inventory["files"])
        if output.exists() or output.is_symlink():
            raise FileExistsError
        temporary.rename(output)
    except BaseException:
        # Only the fixed files created by this call are eligible for cleanup.
        # Never recursively remove a caller-provided or pre-existing directory.
        for name in (*payloads, "dependency-stage.zip", "SHA256SUMS"):
            path = temporary / name
            plain_path(path)
            path.unlink(missing_ok=True)
        temporary.rmdir()
        raise
    return {"candidate_id": inventory["candidate"]["id"], "artifacts": hashes, "package": packaged}


def schema_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Validate current, old-policy, future-version and invalid synthetic fixtures."""
    try:
        valid = load_json(root / FIXTURES / "current.json")
        validate_data(valid, root=root)
        for name in ("old-policy-profile.json", "future-version.json", "invalid.json"):
            invalid = load_json(root / FIXTURES / name)
            try:
                validate_data(invalid, root=root)
            except ReleaseEvidenceError, ReleaseSbomError:
                continue
            return ["RELEASE_SCHEMA_NEGATIVE_ACCEPTED"]
    except Exception:
        return ["RELEASE_SCHEMA_INVALID"]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("schemas", "validate", "freeze"))
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "schemas":
            if schema_messages():
                _fail("RELEASE_SCHEMA_INVALID")
            print("OK: resolved dependency-stage schema, current and rejected fixtures are valid.")
            return 0
        if args.inventory is None or args.stage is None or args.artifacts is None:
            _fail("RELEASE_ARGUMENTS")
        inventory = load_json(args.inventory)
        if args.command == "freeze":
            if args.output is None:
                _fail("RELEASE_ARGUMENTS")
            result = freeze_evidence(inventory, args.stage, args.artifacts, args.output)
        else:
            result = validate_stage(inventory, args.stage, args.artifacts)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ReleaseEvidenceError, ReleaseArtifactError, ReleaseSbomError) as error:
        print(
            f"ERROR [{error}] Dependency staging evidence could not be verified.", file=sys.stderr
        )
    except Exception:
        print(
            "ERROR [RELEASE_INPUT] Dependency staging evidence could not be read safely.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
