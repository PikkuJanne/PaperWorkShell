"""Pure, deterministic SBOM and notice rendering for reviewed release inventories.

These functions do not discover software, read files, download licences, or approve
redistribution. The caller must first validate the resolved staging inventory and
its source/artifact/file identities. Only the supplied public relative staging
paths are rendered. Input-archive checksums and staged-file checksums are different
subjects and remain separate in both SBOM formats.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Final, NoReturn, cast
from urllib.parse import urlsplit

GENERATOR_NAME: Final = "PaperWorkShell release evidence"
GENERATOR_VERSION: Final = "1"
MAX_COMPONENTS: Final = 1024
MAX_FILES: Final = 25_000
MAX_LICENSE_TEXT_BYTES: Final = 1024 * 1024
MAX_TOTAL_LICENSE_BYTES: Final = 32 * 1024 * 1024
_IDENTIFIER: Final = re.compile(r"[a-z0-9][a-z0-9.+-]{0,127}\Z")
_LICENSE_REF: Final = re.compile(r"LicenseRef-[A-Za-z0-9.-]+")
_PRIVATE_PATH: Final = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[/\\]|file://|\\\\|/(?:Users|home)/", re.I
)
_HEX: Final = {length: re.compile(rf"[0-9a-f]{{{length}}}\Z") for length in (40, 64)}
_RESERVED_PATHS: Final = frozenset(
    {
        "aux",
        "con",
        "prn",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


class ReleaseSbomError(ValueError):
    """Stable, sanitized generator input failure."""

    def __init__(self) -> None:
        super().__init__("RELEASE_SBOM_INPUT")


@dataclass(frozen=True)
class _LicenseText:
    path: str
    sha256: str
    text: str
    encoding: str


@dataclass(frozen=True)
class _Component:
    component_id: str
    name: str
    version: str
    type: str
    scope: str
    supplier: str
    purl: str | None
    license_expression: str
    license_texts: tuple[_LicenseText, ...]
    source_url: str
    artifact_sha256: str
    dependencies: tuple[str, ...]
    relationship: str
    artifact_url: str | None


@dataclass(frozen=True)
class _File:
    path: str
    component_id: str
    size_bytes: int
    sha256: str
    sha1: str


@dataclass(frozen=True)
class _LicenseRef:
    id: str
    name: str
    text: str


@dataclass(frozen=True)
class _Inventory:
    candidate: Mapping[str, str]
    target: Mapping[str, str]
    components: tuple[_Component, ...]
    files: tuple[_File, ...]
    license_refs: tuple[_LicenseRef, ...]
    digest: str


def _fail() -> NoReturn:
    raise ReleaseSbomError


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail()
    return cast(Mapping[str, Any], value)


def _items(value: object, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail()
    return value


def _text(value: object, *, multiline: bool = False, maximum: int = 1024) -> str:
    # Only legal-text callers opt into multiline. GNU notices use form feeds as
    # page breaks; retain them verbatim so original encoded asset hashes hold.
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
        or _PRIVATE_PATH.search(value)
        or any(ord(char) < 32 and (not multiline or char not in "\r\n\t\f") for char in value)
    ):
        _fail()
    return value


def _identifier(value: object) -> str:
    text = _text(value, maximum=128)
    if not _IDENTIFIER.fullmatch(text):
        _fail()
    return text


def _hash(value: object, length: int = 64) -> str:
    if not isinstance(value, str) or not _HEX[length].fullmatch(value):
        _fail()
    return value


def _url(value: object) -> str:
    text = _text(value, maximum=2048)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail()
    return text


def _path(value: object) -> str:
    text = _text(value, maximum=512)
    path = PurePosixPath(text)
    if (
        text.startswith("/")
        or path.as_posix() != text
        or any(char in text for char in '<>:"\\|?*')
        or any(
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in _RESERVED_PATHS
            for part in text.split("/")
        )
    ):
        _fail()
    return text


def _normalise(inventory: Mapping[str, Any]) -> _Inventory:
    """Check rendering invariants; the caller owns release-policy admission checks."""
    try:
        if type(inventory["schema_version"]) is not int or inventory["schema_version"] != 1:
            _fail()
        raw_candidate = _mapping(inventory["candidate"])
        candidate = {key: _text(raw_candidate[key]) for key in ("id", "version", "created_utc")}
        candidate["id"] = _identifier(candidate["id"])
        candidate["source_revision"] = _text(raw_candidate["source_revision"], maximum=128)
        candidate["source_sha256"] = _hash(raw_candidate["source_sha256"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._+-]*", candidate["source_revision"]):
            _fail()
        parsed_date = datetime.strptime(candidate["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
        if parsed_date.strftime("%Y-%m-%dT%H:%M:%SZ") != candidate["created_utc"]:
            _fail()
        raw_target = _mapping(inventory["target"])
        target = {key: _text(raw_target[key]) for key in ("os", "architecture", "python_version")}
        if target["os"] != "Windows" or target["architecture"] != "x86_64":
            _fail()
        if not re.fullmatch(r"3\.14\.[0-9]+", target["python_version"]):
            _fail()
        references: list[_LicenseRef] = []
        for value in _items(inventory["license_refs"], MAX_COMPONENTS):
            item = _mapping(value)
            identifier = _text(item["id"], maximum=128)
            if not _LICENSE_REF.fullmatch(identifier):
                _fail()
            references.append(
                _LicenseRef(
                    identifier,
                    _text(item["name"]),
                    _text(item["text"], multiline=True, maximum=MAX_LICENSE_TEXT_BYTES),
                )
            )
        ref_ids = {reference.id for reference in references}
        if len(ref_ids) != len(references):
            _fail()
        components: list[_Component] = []
        derive_relationship: set[str] = set()
        license_bytes = sum(len(reference.text.encode("utf-8")) for reference in references)
        for value in _items(inventory["components"], MAX_COMPONENTS):
            item = _mapping(value)
            identifier = _identifier(item["component_id"])
            expression = _text(item["license_expression"], maximum=2048)
            if expression == "NOASSERTION":
                if identifier != "paperworkshell":
                    _fail()
            elif not re.fullmatch(r"[A-Za-z0-9.+() -]+", expression):
                _fail()
            if not set(_LICENSE_REF.findall(expression)) <= ref_ids:
                _fail()
            purl = item["purl"]
            if purl is not None:
                purl = _text(purl, maximum=2048)
                if not purl.startswith("pkg:") or "@" not in purl or "\\" in purl:
                    _fail()
            component_type, scope = _text(item["type"]), _text(item["scope"])
            if component_type not in {"application", "framework", "library", "data"}:
                _fail()
            if scope not in {"runtime", "build"}:
                _fail()
            texts: list[_LicenseText] = []
            for raw_text in _items(item["license_texts"], MAX_COMPONENTS):
                licence = _mapping(raw_text)
                text = _text(licence["text"], multiline=True, maximum=MAX_LICENSE_TEXT_BYTES)
                digest = _hash(licence["sha256"])
                encoding = licence.get("encoding", "utf-8")
                if encoding not in {"utf-8", "utf-8-sig", "latin-1"}:
                    _fail()
                if hashlib.sha256(text.encode(encoding)).hexdigest() != digest:
                    _fail()
                license_bytes += len(text.encode("utf-8"))
                texts.append(_LicenseText(_path(licence["path"]), digest, text, encoding))
            if len({text.path.casefold() for text in texts}) != len(texts):
                _fail()
            dependencies = tuple(
                _identifier(dependency)
                for dependency in _items(item["dependencies"], MAX_COMPONENTS)
            )
            if len(set(dependencies)) != len(dependencies) or identifier in dependencies:
                _fail()
            relationship = _text(
                item.get("relationship", "root" if identifier == "paperworkshell" else "transitive")
            )
            if "relationship" not in item and identifier != "paperworkshell":
                derive_relationship.add(identifier)
            if relationship not in {"root", "direct", "transitive", "embedded", "external"}:
                _fail()
            artifact_url = item.get("artifact_url")
            components.append(
                _Component(
                    identifier,
                    _text(item["name"]),
                    _text(item["version"], maximum=128),
                    component_type,
                    scope,
                    _text(item["supplier"]),
                    purl,
                    expression,
                    tuple(sorted(texts, key=lambda text: text.path)),
                    _url(item["source_url"]),
                    _hash(item["artifact_sha256"]),
                    tuple(sorted(dependencies)),
                    relationship,
                    _url(artifact_url) if artifact_url is not None else None,
                )
            )
        ids = {component.component_id for component in components}
        if len(ids) != len(components) or "paperworkshell" not in ids:
            _fail()
        if any(not set(component.dependencies) <= ids for component in components):
            _fail()
        root = next(
            component for component in components if component.component_id == "paperworkshell"
        )
        if root.version != candidate["version"]:
            _fail()
        components = [
            replace(
                component,
                relationship="direct"
                if component.component_id in root.dependencies
                else "transitive",
            )
            if component.component_id in derive_relationship
            else component
            for component in components
        ]
        if license_bytes > MAX_TOTAL_LICENSE_BYTES:
            _fail()
        files: list[_File] = []
        for value in _items(inventory["files"], MAX_FILES):
            item = _mapping(value)
            owner = _identifier(item["component_id"])
            size = item["size_bytes"]
            if (
                owner not in ids
                or isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 <= size <= 9_007_199_254_740_991
            ):
                _fail()
            files.append(
                _File(
                    _path(item["path"]), owner, size, _hash(item["sha256"]), _hash(item["sha1"], 40)
                )
            )
        if len({file.path.casefold() for file in files}) != len(files):
            _fail()
        components.sort(key=lambda component: component.component_id)
        files.sort(key=lambda file: file.path)
        references.sort(key=lambda reference: reference.id)
        # Canonical input identity includes every rendered field and is independent
        # of the order in which a staging walker discovered components/files.
        canonical = {
            "candidate": candidate,
            "target": target,
            "components": [component.__dict__ for component in components],
            "files": [file.__dict__ for file in files],
            "license_refs": [reference.__dict__ for reference in references],
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                default=lambda value: value.__dict__,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return _Inventory(
            candidate, target, tuple(components), tuple(files), tuple(references), digest
        )
    except ReleaseSbomError:
        raise
    except KeyError, TypeError, ValueError, AttributeError, OverflowError:
        raise ReleaseSbomError from None


def _component_ref(component: _Component) -> str:
    return "component:" + component.component_id


def _file_ref(file: _File) -> str:
    return "file:" + hashlib.sha256(file.path.encode("utf-8")).hexdigest()


def _package_id(component: _Component) -> str:
    return "SPDXRef-Package-" + component.component_id


def _spdx_file_id(file: _File) -> str:
    return "SPDXRef-File-" + hashlib.sha256(file.path.encode("utf-8")).hexdigest()


def _owned(component: _Component, resolved: _Inventory) -> tuple[_File, ...]:
    return tuple(file for file in resolved.files if file.component_id == component.component_id)


def _properties(**values: str) -> list[dict[str, str]]:
    return [
        {"name": "paperworkshell:" + name, "value": value} for name, value in sorted(values.items())
    ]


def _cdx_component(component: _Component, resolved: _Inventory) -> dict[str, Any]:
    result: dict[str, Any] = {
        "bom-ref": _component_ref(component),
        "type": component.type,
        "name": component.name,
        "version": component.version,
        "supplier": {"name": component.supplier},
        "hashes": [{"alg": "SHA-256", "content": component.artifact_sha256}],
        "properties": _properties(
            scope=component.scope,
            relationship=component.relationship,
            checksum_subject="consumed-input-artifact",
        ),
        "externalReferences": [
            {
                "type": "other",
                "url": component.source_url,
                "comment": "Reviewed source location; not a staged-file checksum subject.",
            }
        ],
    }
    if component.purl is not None:
        result["purl"] = component.purl
    if component.artifact_url is not None:
        result["externalReferences"].append(
            {
                "type": "distribution",
                "url": component.artifact_url,
                "hashes": [{"alg": "SHA-256", "content": component.artifact_sha256}],
            }
        )
    if component.license_expression != "NOASSERTION":
        result["licenses"] = [{"expression": component.license_expression}]
    evidence = [
        {
            "license": {
                "name": text.path,
                "text": {"contentType": "text/plain", "content": text.text},
                "properties": _properties(
                    original_asset_sha256=text.sha256, original_asset_encoding=text.encoding
                ),
            }
        }
        for text in component.license_texts
    ]
    used_refs = set(_LICENSE_REF.findall(component.license_expression))
    evidence.extend(
        {
            "license": {
                "bom-ref": "license:" + reference.id + ":" + component.component_id,
                "name": reference.id + " — " + reference.name,
                "text": {"contentType": "text/plain", "content": reference.text},
            }
        }
        for reference in resolved.license_refs
        if reference.id in used_refs
    )
    if evidence:
        result["evidence"] = {"licenses": evidence}
    owned = _owned(component, resolved)
    if owned:
        result["components"] = [
            {
                "bom-ref": _file_ref(file),
                "type": "file",
                "name": file.path,
                "hashes": [
                    {"alg": "SHA-256", "content": file.sha256},
                    {"alg": "SHA-1", "content": file.sha1},
                ],
                "properties": _properties(
                    owner=component.component_id,
                    size_bytes=str(file.size_bytes),
                    checksum_subject="staged-file",
                ),
            }
            for file in owned
        ]
    return result


def build_cyclonedx(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Render CycloneDX 1.6; package nesting assigns staged-file ownership."""
    resolved = _normalise(inventory)
    root = next(
        component for component in resolved.components if component.component_id == "paperworkshell"
    )
    namespace = (
        "https://paperworkshell.invalid/sbom/"
        + resolved.candidate["source_sha256"]
        + "/"
        + resolved.digest
    )
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, namespace)),
        "version": 1,
        "metadata": {
            "timestamp": resolved.candidate["created_utc"],
            "tools": {
                "components": [
                    {"type": "application", "name": GENERATOR_NAME, "version": GENERATOR_VERSION}
                ]
            },
            "component": _cdx_component(root, resolved),
            "properties": _properties(
                candidate_id=resolved.candidate["id"],
                candidate_version=resolved.candidate["version"],
                source_revision=resolved.candidate["source_revision"],
                source_sha256=resolved.candidate["source_sha256"],
                inventory_sha256=resolved.digest,
                target_os=resolved.target["os"],
                target_architecture=resolved.target["architecture"],
                python_version=resolved.target["python_version"],
                evidence_scope="dependency-stage; no installer or release approval assertion",
            ),
        },
        "components": [
            _cdx_component(component, resolved)
            for component in resolved.components
            if component != root
        ],
        "dependencies": [
            {
                "ref": _component_ref(component),
                "dependsOn": ["component:" + dependency for dependency in component.dependencies],
            }
            for component in resolved.components
        ]
        + [{"ref": _file_ref(file), "dependsOn": []} for file in resolved.files],
    }


def build_spdx(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Render SPDX 2.3 with owned-file verification codes and distinct input hashes."""
    resolved = _normalise(inventory)
    packages: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-paperworkshell",
        }
    ]
    by_id = {component.component_id: component for component in resolved.components}
    for component in resolved.components:
        owned = _owned(component, resolved)
        package: dict[str, Any] = {
            "SPDXID": _package_id(component),
            "name": component.name,
            "versionInfo": component.version,
            "supplier": "Organization: " + component.supplier,
            "downloadLocation": component.artifact_url or "NOASSERTION",
            "filesAnalyzed": bool(owned),
            "checksums": [{"algorithm": "SHA256", "checksumValue": component.artifact_sha256}],
            "licenseConcluded": component.license_expression,
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "sourceInfo": "Reviewed source location: "
            + component.source_url
            + "; consumed-input-artifact SHA-256: "
            + component.artifact_sha256
            + "; scope: "
            + component.scope
            + "; relationship: "
            + component.relationship
            + ". The package checksum identifies the consumed input artifact, not a staged-file aggregate.",
        }
        if component.purl is not None:
            package["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": component.purl,
                }
            ]
        if component.license_texts:
            package["licenseComments"] = (
                "Licence/notice text is rendered as Unicode. Original asset identities: "
                + "; ".join(
                    text.path + " (" + text.encoding + "), SHA-256 " + text.sha256
                    for text in component.license_texts
                )
            )
        if owned:
            joined = "".join(sorted(file.sha1 for file in owned)).encode("ascii")
            package["packageVerificationCode"] = {
                "packageVerificationCodeValue": hashlib.sha1(
                    joined, usedforsecurity=False
                ).hexdigest()
            }
            package["licenseInfoFromFiles"] = ["NOASSERTION"]
            relationships.extend(
                {
                    "spdxElementId": _package_id(component),
                    "relationshipType": "CONTAINS",
                    "relatedSpdxElement": _spdx_file_id(file),
                }
                for file in owned
            )
        packages.append(package)
        for dependency in component.dependencies:
            target = by_id[dependency]
            if target.scope == "build":
                relationships.append(
                    {
                        "spdxElementId": _package_id(target),
                        "relationshipType": "BUILD_DEPENDENCY_OF",
                        "relatedSpdxElement": _package_id(component),
                    }
                )
            else:
                relationships.append(
                    {
                        "spdxElementId": _package_id(component),
                        "relationshipType": "DEPENDS_ON",
                        "relatedSpdxElement": _package_id(target),
                    }
                )
            if target.relationship == "embedded":
                relationships.append(
                    {
                        "spdxElementId": _package_id(component),
                        "relationshipType": "CONTAINS",
                        "relatedSpdxElement": _package_id(target),
                    }
                )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": resolved.candidate["id"] + " dependency-stage SBOM",
        "documentNamespace": "https://paperworkshell.invalid/spdx/"
        + resolved.candidate["source_sha256"]
        + "/"
        + resolved.digest,
        "creationInfo": {
            "created": resolved.candidate["created_utc"],
            "creators": ["Tool: " + GENERATOR_NAME + "-" + GENERATOR_VERSION],
        },
        "comment": "Dependency-stage evidence only; no installer, signature or release approval assertion. Candidate: "
        + resolved.candidate["id"]
        + "; version: "
        + resolved.candidate["version"]
        + "; source revision: "
        + resolved.candidate["source_revision"]
        + "; source SHA-256: "
        + resolved.candidate["source_sha256"]
        + "; inventory SHA-256: "
        + resolved.digest
        + "; target: "
        + resolved.target["os"]
        + " "
        + resolved.target["architecture"]
        + "; Python: "
        + resolved.target["python_version"]
        + ". File licence text was not individually classified; NOASSERTION denotes that limit, while package conclusions record the reviewed selected route.",
        "packages": packages,
        "files": [
            {
                "SPDXID": _spdx_file_id(file),
                "fileName": "./" + file.path,
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": file.sha256},
                    {"algorithm": "SHA1", "checksumValue": file.sha1},
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
                "comment": "Owned by "
                + file.component_id
                + "; size in bytes: "
                + str(file.size_bytes)
                + ". Checksums identify this staged file.",
            }
            for file in resolved.files
        ],
        "relationships": sorted(
            relationships,
            key=lambda relationship: (
                relationship["spdxElementId"],
                relationship["relationshipType"],
                relationship["relatedSpdxElement"],
            ),
        ),
        "hasExtractedLicensingInfos": [
            {"licenseId": reference.id, "name": reference.name, "extractedText": reference.text}
            for reference in resolved.license_refs
        ],
    }


def build_notices(inventory: Mapping[str, Any]) -> str:
    """Render the reviewed licence/notice texts without rewriting their contents."""
    resolved = _normalise(inventory)
    lines = [
        "PaperWorkShell dependency-stage third-party notices",
        "Candidate: " + resolved.candidate["id"],
        "Candidate version: " + resolved.candidate["version"],
        "Created UTC: " + resolved.candidate["created_utc"],
        "Source revision: " + resolved.candidate["source_revision"],
        "Source SHA-256: " + resolved.candidate["source_sha256"],
        "Inventory SHA-256: " + resolved.digest,
        "",
        "These notices describe the reviewed dependency stage. Installer identity, observed",
        "signing status, product completion and publication have their own candidate records.",
        "Build inputs are identified separately from runtime files. Original text below is preserved.",
    ]
    for component in resolved.components:
        lines.extend(
            [
                "",
                "=" * 72,
                component.name + " " + component.version + " [" + component.component_id + "]",
                "Scope: " + component.scope + "; relationship: " + component.relationship,
                "Supplier: " + component.supplier,
                "Selected licence expression: " + component.license_expression,
                "Reviewed source location: " + component.source_url,
                "Consumed input artifact SHA-256: " + component.artifact_sha256,
                "Owned staged files: " + str(len(_owned(component, resolved))),
                "Dependencies: "
                + (", ".join(component.dependencies) or "None in the resolved inventory"),
            ]
        )
        if component.artifact_url is not None:
            lines.append("Consumed input artifact URL: " + component.artifact_url)
        if component.purl is not None:
            lines.append("Package URL: " + component.purl)
        if component.license_expression == "NOASSERTION":
            lines.append(
                "No final PaperWorkShell product licence is asserted by this dependency stage."
            )
        for text in component.license_texts:
            lines.extend(
                [
                    "",
                    "--- " + text.path + " ---",
                    "Original asset encoding: " + text.encoding,
                    "Original asset SHA-256: " + text.sha256,
                    text.text,
                ]
            )
    if resolved.license_refs:
        lines.extend(["", "=" * 72, "Custom licence definitions"])
        for reference in resolved.license_refs:
            lines.extend(["", reference.id + " — " + reference.name, reference.text])
    return "\n".join(lines) + "\n"
