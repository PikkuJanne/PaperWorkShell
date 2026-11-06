"""Build a bounded, explicitly unsigned internal Windows installer from exact inputs.

This is release tooling, not a document-processing or application command. Network
acquisition is confined to the explicit prepare-compiler --fetch action. Public
signing and runtime redistribution entitlement are separate, fail-closed gates.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Final, NoReturn

from tools.release_artifacts import (
    MAX_STAGE_BYTES,
    Artifact,
    ReleaseArtifactError,
    acquire,
    file_hashes,
    plain_path,
    safe_relative,
    verify_artifact,
)
from tools.release_evidence import load_json, validate_data
from tools.release_sbom import build_cyclonedx, build_notices, build_spdx
from tools.release_staging import canonical_json, inventory_files, stage_wheel, verify_files

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PRODUCT: Final = "packaging/windows/product-v1.json"
COMPILER_REVIEW: Final = "packaging/windows/inno-setup-7.1.1-review.json"
SCRIPT: Final = "packaging/windows/paperworkshell.iss"
BASE_SHA256: Final = "8fb9366e08dee502a69858d8070b97a98ba9d55af232bd87fcf2a50ccad5eb2b"
APPLICATION_ID: Final = "io.github.pikkujanne.paperworkshell"
UPGRADE_ID: Final = "{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}"
MAX_FILES: Final = 10_000
MAX_LOG_BYTES: Final = 2 * 1024 * 1024
GUI_INTERPRETER: Final = "runtime/PaperWorkShellDesktop.exe"
MICROSOFT_TERMS: Final = "notices/Microsoft-runtime-recipient-terms.txt"
MICROSOFT_COMPONENT: Final = "microsoft-vc14-x64-community2022"
MICROSOFT_REVIEW: Final = "packaging/release-reviews/microsoft-community2022-v1.json"
MICROSOFT_ASSET_DIRECTORY: Final = "packaging/licenses/release-native/microsoft-msvc-runtime"
MICROSOFT_ASSETS: Final = {
    "COMMUNITY-2022-FULL-TERMS.txt": "cbfa92f37dc9cc11735952457c4c68df6f6aec0dfdb895e31c15c1adc0ed7963",
    "COMMUNITY-2022-RECIPIENT-TERMS.txt": "a5cdf1136b18611fc5235c657b7ad113a9ac338abe6c1af3c299205d9492c718",
    "COMMUNITY-2022-ROUTE.txt": "9dc5d40efc8acc857711535c16a88a894c38a48cf3d7176dda14093346ff86ce",
}


class WindowsInstallerError(ValueError):
    """A stable build error without paths, raw subprocess text or credentials."""


def fail(code: str) -> NoReturn:
    raise WindowsInstallerError(code)


def owned_output(path: Path, *, root: Path = PROJECT_ROOT) -> Path:
    plain_path(path)
    absolute = path.absolute().resolve()
    allowed = (root / "output/pws0802").resolve()
    if absolute == allowed or not absolute.is_relative_to(allowed):
        fail("INSTALLER_OUTPUT_SCOPE")
    return absolute


def product(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Validate current build policy, not immutable historical artifact inputs.

    PWS-D043 changes source support metadata without restaging R7. Its original
    product bytes remain authenticated by that build epoch's source archive and
    receipt; accepting the old route here would re-enable it for a new build.
    """
    value = load_json(root / PRODUCT, 4096)
    expected = {
        "schema_version": 1,
        "product_name": "PaperWorkShell",
        "application_id": APPLICATION_ID,
        "upgrade_identity": UPGRADE_ID,
        "publisher": "Janne Vuorela",
        "first_party_license": "MIT",
        "official_distribution_price": "free, including commercial use",
        "os": "Windows 11",
        "minimum_os_build": 22000,
        "architecture": "x64",
        "arm64_route": "not supported; native ARM64 and x64 emulation are outside project scope",
        "file_associations": False,
        "user_accounts": False,
        "activation": False,
        "telemetry": False,
        "document_uploads": False,
        "automatic_crash_uploads": False,
    }
    if set(value) != set(expected) | {"version", "channel", "candidate_id"}:
        fail("INSTALLER_PRODUCT_FIELDS")
    if any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items()):
        fail("INSTALLER_PRODUCT_IDENTITY")
    if (
        not isinstance(value["version"], str)
        or re.fullmatch(r"(?:0|[1-9][0-9]{0,3})(?:\.(?:0|[1-9][0-9]{0,3})){2}", value["version"])
        is None
        or value["channel"] != "engineering"
        or not isinstance(value["candidate_id"], str)
        or re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,95}", value["candidate_id"]) is None
    ):
        fail("INSTALLER_ENGINEERING_IDENTITY")
    return value


def marker(configuration: Mapping[str, Any]) -> bytes:
    return canonical_json(
        {k: configuration[k] for k in ("application_id", "version", "channel", "candidate_id")}
        | {"schema_version": 1}
    )


def _write(path: Path, raw: bytes) -> None:
    plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def _copy(
    source: Path,
    target: Path,
    *,
    maximum: int = MAX_STAGE_BYTES,
    expected_sha256: str | None = None,
) -> None:
    plain_path(source)
    plain_path(target)
    if not stat.S_ISREG(source.stat().st_mode) or source.stat().st_size > maximum:
        fail("INSTALLER_COPY_LIMIT")
    identity = file_hashes(source, maximum)
    if expected_sha256 is not None and identity[1] != expected_sha256:
        fail("INSTALLER_COPY_SOURCE_CHANGED")
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            created = True
            count = 0
            while chunk := incoming.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    fail("INSTALLER_COPY_LIMIT")
                outgoing.write(chunk)
        if file_hashes(target, maximum) != identity:
            fail("INSTALLER_COPY_CHANGED")
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def _run(
    args: Sequence[str],
    work: Path,
    label: str,
    *,
    seconds: int = 300,
    root: Path = PROJECT_ROOT,
    successful_descendant_grace_seconds: float = 0,
) -> None:
    """Bound compiler output and its entire suspended/job-contained process tree."""
    from tools.windows_signing import run_build_process

    if not args:
        fail("INSTALLER_COMMAND_PROTOCOL")
    program = str(Path(args[0]).absolute())
    # This constant runner preserves bounded diagnostics for an unsuccessful
    # compiler too. Tool paths and all arguments remain a structured argv list.
    runner = (
        "import subprocess,sys; "
        "code=subprocess.call(sys.argv[1:],shell=False,stdin=subprocess.DEVNULL); "
        "print('\\nPWS_BUILD_EXIT_CODE:'+str(code))"
    )
    raw = run_build_process(
        [sys.executable, "-I", "-B", "-c", runner, program, *args[1:]],
        work,
        seconds,
        successful_descendant_grace_seconds=successful_descendant_grace_seconds,
    )
    if len(raw) > MAX_LOG_BYTES:
        fail("INSTALLER_COMMAND_LIMIT")
    text, separator, code = raw.decode("utf-8", errors="replace").rpartition(
        "\nPWS_BUILD_EXIT_CODE:"
    )
    if not separator or re.fullmatch(r"-?[0-9]+\s*", code) is None:
        fail("INSTALLER_COMMAND_PROTOCOL")
    for source, replacement in ((str(root), "[REPOSITORY]"), (str(Path.home()), "[PROFILE]")):
        text = text.replace(source, replacement).replace(source.replace("\\", "/"), replacement)
    _write(work / (label + ".txt"), text.encode())
    if int(code) != 0:
        fail("INSTALLER_COMMAND_FAILED")


def verify_compiler(directory: Path, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    review = load_json(root / COMPILER_REVIEW)
    if review.get("version") != "7.1.1" or review.get("source_commit") != (
        "2527da77ff7983514f535d19391a748c5d898336"
    ):
        fail("INSTALLER_COMPILER_REVIEW")
    records = review["compiler_files"]
    owners = {r["path"]: "inno-setup" for r in records}
    actual = inventory_files(directory, owners)
    if [{k: r[k] for k in ("path", "size_bytes", "sha256")} for r in actual] != records:
        fail("INSTALLER_COMPILER_CHANGED")
    return review


def prepare_compiler(
    output: Path, artifacts: Path, *, fetch: bool = False, root: Path = PROJECT_ROOT
) -> None:
    output = owned_output(output, root=root)
    artifacts = owned_output(artifacts, root=root)
    if output.exists():
        fail("INSTALLER_OUTPUT_EXISTS")
    review = load_json(root / COMPILER_REVIEW)
    item = review["installer"]
    expected = Artifact(item["name"], item["url"], item["size_bytes"], item["sha256"])
    source = acquire(expected, artifacts) if fetch else artifacts / expected.name
    verify_artifact(source, expected)
    # The reviewed installer is Authenticode-verified before portable extraction.
    from tools.windows_signing import inspect_authenticode

    signature = inspect_authenticode(source)
    if (
        signature["status"] != "valid"
        or signature["subject"] != review["installer_signer_subject"]
        or signature["certificate_sha256"] != review["installer_signer_certificate_sha256"]
        or signature["thumbprint"] != review["installer_signer_thumbprint"]
    ):
        fail("INSTALLER_COMPILER_SIGNATURE")
    work = output.parent / (output.name + "-prepare")
    work.mkdir(parents=True, exist_ok=False)
    _run(
        [
            str(source),
            "/PORTABLE=1",
            "/CURRENTUSER",
            "/SP-",
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            "/TASKS=",
            "/DIR=" + str(output),
        ],
        work,
        "prepare-compiler",
        root=root,
    )
    verify_compiler(output, root=root)


def _source_paths(root: Path) -> list[str]:
    selected = {
        PRODUCT,
        SCRIPT,
        COMPILER_REVIEW,
        "LICENSE",
        "LICENSING.md",
        "README.md",
        ".gitattributes",
        "pyproject.toml",
        "uv.lock",
    }
    for directory, pattern in (
        ("tools", "*.py"),
        ("schemas", "*.json"),
        ("packaging", "*"),
        ("tests/fixtures/release-inventory", "*.json"),
        ("tests/fixtures/release-sbom", "*"),
    ):
        for path in (root / directory).rglob(pattern):
            plain_path(path)
            if path.is_file():
                selected.add(path.relative_to(root).as_posix())
                if len(selected) > 4096:
                    fail("INSTALLER_SOURCE_LIMIT")
    selected.discard("packaging/release-reviews/app-inputs-v1.json")
    selected.discard("packaging/README.md")
    return sorted(selected)


def _source_archive(root: Path, output: Path, expected: Mapping[str, str]) -> None:
    total = 0
    with tarfile.open(output, "x:xz") as archive:
        for name in _source_paths(root):
            source = root / safe_relative(name)
            plain_path(source)
            info = archive.gettarinfo(str(source), arcname=name)
            if not info.isfile() or info.size > 4 * 1024 * 1024:
                fail("INSTALLER_SOURCE_LIMIT")
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            raw = source.read_bytes()
            total += len(raw)
            if total > 64 * 1024 * 1024:
                fail("INSTALLER_SOURCE_LIMIT")
            if hashlib.sha256(raw).hexdigest() != expected[name]:
                fail("INSTALLER_SOURCE_CHANGED")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))


def application_sources(wheel: Path, sdist: Path, root: Path) -> dict[str, str]:
    """Bind wheel code, corresponding sdist code and the current local source."""
    sources: dict[str, str] = {}
    configuration = product(root)
    metadata_name = "paperworkshell-" + configuration["version"] + ".dist-info/METADATA"
    metadata_found = False
    names: set[str] = set()
    with zipfile.ZipFile(wheel) as archive:
        if len(archive.infolist()) > MAX_FILES:
            fail("INSTALLER_APPLICATION_LIMIT")
        for member in archive.infolist():
            name = safe_relative(member.filename.rstrip("/"))
            if name.casefold() in names:
                fail("INSTALLER_APPLICATION_COLLISION")
            names.add(name.casefold())
            if member.is_dir():
                continue
            if member.file_size > 32 * 1024 * 1024:
                fail("INSTALLER_APPLICATION_LIMIT")
            if name == metadata_name:
                if member.file_size > 128 * 1024:
                    fail("INSTALLER_APPLICATION_METADATA")
                metadata = BytesParser(policy=email_policy.compat32).parsebytes(
                    archive.read(member)
                )
                for key, expected in (
                    ("Name", "paperworkshell"),
                    ("Version", configuration["version"]),
                    ("License-Expression", "MIT"),
                ):
                    values = metadata.get_all(key, [])
                    if len(values) != 1 or str(values[0]) != expected:
                        fail("INSTALLER_APPLICATION_METADATA")
                metadata_found = True
            if name.startswith("paperworkshell/"):
                raw = archive.read(member)
                digest = hashlib.sha256(raw).hexdigest()
                source_name = "src/" + name
                if file_hashes(root / source_name)[1] != digest:
                    fail("INSTALLER_APPLICATION_SOURCE_CHANGED")
                sources[source_name] = digest
            elif not name.startswith("paperworkshell-" + configuration["version"] + ".dist-info/"):
                fail("INSTALLER_APPLICATION_NAMESPACE")
    if not sources or not metadata_found:
        fail("INSTALLER_APPLICATION_SOURCE_MISSING")
    current_sources = {
        p.relative_to(root).as_posix()
        for p in (root / "src/paperworkshell").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    }
    if current_sources != set(sources):
        fail("INSTALLER_APPLICATION_SOURCE_COVERAGE")
    found: set[str] = set()
    with tarfile.open(sdist, "r:gz") as source_archive:
        for number, source_member in enumerate(source_archive, 1):
            if number > MAX_FILES or source_member.size > 32 * 1024 * 1024:
                fail("INSTALLER_APPLICATION_LIMIT")
            name = safe_relative(source_member.name.rstrip("/"))
            if not source_member.isfile():
                if not source_member.isdir():
                    fail("INSTALLER_APPLICATION_SOURCE_MEMBER")
                continue
            relative = name.split("/", 1)[1] if "/" in name else ""
            if relative.startswith("src/paperworkshell/"):
                stream = source_archive.extractfile(source_member)
                if stream is None:
                    fail("INSTALLER_APPLICATION_SOURCE_MEMBER")
                digest = hashlib.sha256(stream.read(32 * 1024 * 1024 + 1)).hexdigest()
                if relative not in sources or sources[relative] != digest:
                    fail("INSTALLER_APPLICATION_SOURCE_MISMATCH")
                found.add(relative)
    if found != set(sources):
        fail("INSTALLER_APPLICATION_SOURCE_MISSING")
    return sources


def _input(path: Path, scope: str, recipe: str, *, url: str | None = None) -> dict[str, Any]:
    size, digest, _ = file_hashes(path)
    result: dict[str, Any] = {
        "name": path.name,
        "url": url,
        "size_bytes": size,
        "sha256": digest,
        "scope": scope,
    }
    if url is None:
        result.update(origin="local-build", recipe_path=recipe)
    return result


def _license(source: Path, target: str, expected: str) -> dict[str, str]:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        fail("INSTALLER_LICENSE_CHANGED")
    return {"path": target, "sha256": expected, "encoding": "utf-8", "text": raw.decode("utf-8")}


def stage_gui_interpreter(
    base_stage: Path,
    payload: Path,
    owners: dict[str, str],
    inherited_files: Sequence[Mapping[str, Any]],
) -> None:
    """Give the GUI a product process name without changing upstream Python bytes."""
    originals = [record for record in inherited_files if record["path"] == "runtime/pythonw.exe"]
    if len(originals) != 1 or originals[0]["component_id"] != "cpython":
        fail("INSTALLER_GUI_INTERPRETER_SOURCE")
    original = originals[0]
    _copy(
        base_stage / original["path"],
        payload / GUI_INTERPRETER,
        maximum=original["size_bytes"],
        expected_sha256=original["sha256"],
    )
    owners[GUI_INTERPRETER] = "cpython"
    notice = "notices/PaperWorkShell-CPython-GUI-ALIAS.txt"
    _write(
        payload / notice,
        (
            "PaperWorkShell CPython packaging change summary\n\n"
            "PaperWorkShellDesktop.exe is an unmodified byte-for-byte copy of the\n"
            "upstream CPython 3.14.6 pythonw.exe, using a filename that identifies\n"
            "the PaperWorkShell GUI process in Windows and accessibility tools.\n"
            "No Python object code, embedded notices, signature or metadata was changed.\n"
            "The original pythonw.exe is retained. Restricted workers use python.exe.\n"
            "All PSF and other original CPython notices and source remain supplied.\n"
            "This filename does not make the interpreter first-party MIT code.\n\n"
            "Upstream and alias SHA-256: " + original["sha256"] + "\n"
        ).encode(),
    )
    owners[notice] = "paperworkshell"


def microsoft_runtime_input(
    directory: Path | None, receipt_sha256: str | None, *, root: Path = PROJECT_ROOT
) -> dict[str, Any] | None:
    """A complete-runtime build consumes an authenticated full-route overlay only."""
    if directory is None and receipt_sha256 is None:
        return None
    if directory is None or receipt_sha256 is None:
        fail("INSTALLER_MICROSOFT_INPUT_REQUIRED")
    from tools import prepare_windows_microsoft_runtime as microsoft

    try:
        result = microsoft.verify_overlay(directory, receipt_sha256)
    except microsoft.PreparationError:
        fail("INSTALLER_MICROSOFT_INPUT_INVALID")
    for name, expected in MICROSOFT_ASSETS.items():
        if file_hashes(root / MICROSOFT_ASSET_DIRECTORY / name, 128 * 1024)[1] != expected:
            fail("INSTALLER_MICROSOFT_TERMS_CHANGED")
    if (
        result["approved_receipt_inputs"]["recipient_terms_sha256"]
        != MICROSOFT_ASSETS["COMMUNITY-2022-RECIPIENT-TERMS.txt"]
        or result["runtime_version"] != microsoft.VERSION
        or result["architecture"] != "AMD64"
        or result["pikepdf_alias_disposition"] != microsoft.COMMUNITY_ALIAS_DISPOSITION
        or len(result["members"]) != 9
    ):
        fail("INSTALLER_MICROSOFT_INPUT_INVALID")
    return result


def integrate_microsoft_runtime(
    payload: Path,
    owners: dict[str, str],
    inventory: dict[str, Any],
    directory: Path,
    report: Mapping[str, Any],
    *,
    root: Path = PROJECT_ROOT,
) -> None:
    """Replace only this candidate's old external/downstream runtime routes."""
    from tools import prepare_windows_microsoft_runtime as microsoft

    legacy = {"microsoft-vc14-x64-external", "pikepdf-msvc-runtime"}
    if {c["component_id"] for c in inventory["components"]} & legacy != legacy:
        fail("INSTALLER_MICROSOFT_BASE_CHANGED")
    for record in report["members"]:
        name = safe_relative(record["path"])
        # The alias is deliberately omitted from the inherited copy and supplied
        # from the newly authenticated canonical member, with unchanged bytes.
        _copy(
            directory / name,
            payload / name,
            maximum=record["size_bytes"],
            expected_sha256=record["sha256"],
        )
        owners[name] = MICROSOFT_COMPONENT
    licenses = []
    for name, expected in MICROSOFT_ASSETS.items():
        target = (
            MICROSOFT_TERMS
            if name == "COMMUNITY-2022-RECIPIENT-TERMS.txt"
            else "notices/microsoft-community2022/" + name
        )
        source = root / MICROSOFT_ASSET_DIRECTORY / name
        _copy(source, payload / target, expected_sha256=expected)
        owners[target] = MICROSOFT_COMPONENT
        licenses.append(_license(source, target, expected))
    evidence_names = ["evidence/microsoft-runtime/overlay-receipt.json"]
    _copy(directory / "overlay-receipt.json", payload / evidence_names[0])
    owners[evidence_names[0]] = MICROSOFT_COMPONENT
    for name in ("owner_acceptance", "community_copy_receipt", "recipient_terms_review"):
        target = "evidence/microsoft-runtime/" + name + ".json"
        expected = report["approved_receipt_inputs"][name + "_sha256"]
        _copy(
            root / safe_relative(report["preparation_inputs"][name]),
            payload / target,
            expected_sha256=expected,
        )
        owners[target] = MICROSOFT_COMPONENT
        evidence_names.append(target)
    for name, owner in tuple(owners.items()):
        if owner in legacy:
            owners[name] = MICROSOFT_COMPONENT
    inventory["components"] = [
        c for c in inventory["components"] if c["component_id"] not in legacy
    ]
    for component in inventory["components"]:
        component["dependencies"] = sorted(
            {MICROSOFT_COMPONENT if name in legacy else name for name in component["dependencies"]}
        )
    expression = "LicenseRef-PWS-Microsoft-Community2022-Runtime"
    inventory["components"].append(
        {
            "component_id": MICROSOFT_COMPONENT,
            "name": "Microsoft Visual C++ runtime (Community 2022 individual REDIST route)",
            "version": microsoft.VERSION,
            "type": "library",
            "scope": "runtime",
            "supplier": "Microsoft Corporation",
            "purl": None,
            "license_expression": expression,
            "license_texts": licenses,
            "source_url": microsoft.REFERENCE_URL,
            "artifact_sha256": microsoft.REFERENCE_SHA256,
            "dependencies": [],
            "relationship": "embedded",
        }
    )
    inventory["obligations"] = [
        o for o in inventory["obligations"] if o["component_id"] not in legacy
    ]
    inventory["obligations"].append(
        {
            "component_id": MICROSOFT_COMPONENT,
            "source_paths": evidence_names,
            "replacement_instructions_path": None,
            "distribution_note": "Unmodified Microsoft release object code in nine explicit app-local destinations, five unique hashes. Accepted individual Community2022 route and authenticated acquired product copy; local Microsoft-only recipient agreement. The pikepdf loader filename is a byte-identical alias under the documented interpretation. Original wheel/VS2019 notices are historical provenance, preserved unchanged; MIT and all other open-source rights remain separate. No public trusted signing or clean-machine qualification is supplied by this route.",
        }
    )
    inventory["license_refs"].append(
        {
            "id": expression,
            "name": "Microsoft Community2022 runtime component-only terms and provenance",
            "text": "\n\n".join(item["text"] for item in licenses),
        }
    )


def generate_includes(
    directory: Path,
    payload: Path,
    configuration: Mapping[str, Any],
    files: Sequence[Mapping[str, Any]],
    *,
    complete_runtime: bool = False,
) -> None:
    """Only reviewed paths become file entries; no recursive installer wildcard."""
    if len(files) > MAX_FILES:
        fail("INSTALLER_FILE_LIMIT")
    for path in (directory, payload):
        plain_path(path)
    if type(complete_runtime) is not bool:
        fail("INSTALLER_RUNTIME_MODE")
    terms_digest = ""
    if complete_runtime:
        terms = payload / MICROSOFT_TERMS
        plain_path(terms)
        if not terms.is_file():
            fail("INSTALLER_MICROSOFT_TERMS_REQUIRED")
        terms_digest = file_hashes(terms, 128 * 1024)[1]
        if not any(
            record.get("path") == MICROSOFT_TERMS and record.get("sha256") == terms_digest
            for record in files
        ):
            fail("INSTALLER_MICROSOFT_TERMS_REQUIRED")

    def quoted(value: str) -> str:
        if any(c in value for c in ('"', "\n", "\r", "\x00")):
            fail("INSTALLER_SCRIPT_VALUE")
        return '"' + value + '"'

    lines = ["[Files]"]
    checks = []
    for record in sorted(files, key=lambda r: r["path"]):
        relative = safe_relative(record["path"])
        if "{" in relative or "}" in relative:
            fail("INSTALLER_SCRIPT_VALUE")
        local = Path(relative)
        lines.append(
            "Source: "
            + quoted(str(payload / local))
            + "; DestDir: "
            + quoted("{app}\\" + str(local.parent))
            + "; DestName: "
            + quoted(local.name)
            + "; Flags: ignoreversion"
        )
        checks.append(
            "  if not SafeTarget(ExpandConstant('"
            + ("{app}\\" + str(local)).replace("'", "''")
            + "')) then Exit;"
        )
    _write(directory / "files.iss.inc", ("\n".join(lines) + "\n").encode())
    _write(directory / "checks.iss.inc", ("\n".join(checks) + "\n").encode())
    definitions = {
        "PWSVersion": configuration["version"],
        "PWSChannelLabel": "UNSIGNED INTERNAL - not a public release",
        "PWSLicenseFile": str(
            payload
            / (MICROSOFT_TERMS if complete_runtime else "notices/PaperWorkShell-MIT-LICENSE.txt")
        ),
        "PWSRecipientTermsSHA256": terms_digest,
        "PWSInformationFile": str(payload / "notices/UNSIGNED_INTERNAL.txt"),
        "PWSOutputDirectory": str(directory / "installer"),
        "PWSOutputName": "PaperWorkShell-" + configuration["candidate_id"] + "-win-x64-UNSIGNED",
        "PWSMarkerSHA256": hashlib.sha256(marker(configuration)).hexdigest(),
        "PWSGeneratedFiles": str(directory / "files.iss.inc"),
        "PWSGeneratedChecks": str(directory / "checks.iss.inc"),
    }
    text = ["#define " + key + " " + quoted(value) for key, value in definitions.items()]
    text += ["#define PWSSignedBuild 0", "#define PWSCompleteRuntime " + str(int(complete_runtime))]
    _write(directory / "config.iss.inc", ("\n".join(text) + "\n").encode())


def assemble(
    output: Path,
    base_stage: Path,
    base_inventory: Path,
    base_artifacts: Path,
    wheel: Path,
    sdist: Path,
    launcher: Path,
    launcher_receipt: Path,
    launcher_receipt_sha256: str,
    compiler: Path,
    *,
    root: Path = PROJECT_ROOT,
    microsoft_overlay: Path | None = None,
    microsoft_overlay_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    configuration = product(root)
    microsoft = microsoft_runtime_input(
        microsoft_overlay, microsoft_overlay_receipt_sha256, root=root
    )
    for source in (
        base_stage,
        base_inventory,
        base_artifacts,
        wheel,
        sdist,
        launcher,
        launcher_receipt,
        compiler,
    ):
        plain_path(source)
        if not source.absolute().resolve().is_relative_to((root / "output").resolve()):
            fail("INSTALLER_INPUT_SCOPE")
    output = owned_output(output, root=root)
    if output.exists():
        fail("INSTALLER_OUTPUT_EXISTS")
    if file_hashes(base_inventory)[1] != BASE_SHA256:
        fail("INSTALLER_BASE_IDENTITY")
    base = load_json(base_inventory)
    validate_data(base, root=root)
    verify_files(base_stage, base["files"])
    review = verify_compiler(compiler, root=root)
    from tools.build_windows_launcher import verify_launcher

    native_launcher = verify_launcher(
        launcher, launcher_receipt, root=root, expected_receipt_sha256=launcher_receipt_sha256
    )
    source_bindings = application_sources(wheel, sdist, root)
    review_paths = (
        {r["path"] for r in base["review_inputs"]}
        | {PRODUCT, SCRIPT, COMPILER_REVIEW, "LICENSE", "LICENSING.md", "pyproject.toml"}
        | {p.relative_to(root).as_posix() for p in (root / "tools").glob("*windows*.py")}
        | {p.relative_to(root).as_posix() for p in (root / "packaging/windows").glob("launcher.*")}
    )
    if microsoft is not None:
        review_paths.add(MICROSOFT_REVIEW)
    recipe_bindings = {
        name: file_hashes(root / name)[1] for name in set(_source_paths(root)) | review_paths
    }
    application_inputs = {
        p.name: _input(p, scope, "pyproject.toml")
        for p, scope in ((wheel, "runtime"), (sdist, "source"))
    }
    # Verify all inherited acquisition inputs before consuming their provenance.
    for item in base["input_artifacts"]:
        verify_artifact(
            base_artifacts / item["name"],
            Artifact(
                item["name"],
                item["url"] or "",
                item["size_bytes"],
                item["sha256"],
            ),
        )
    output.mkdir(parents=True, exist_ok=False)
    payload, artifacts = output / "payload", output / "artifacts"
    artifacts.mkdir()
    owners: dict[str, str] = {}
    for record in base["files"]:
        if (
            microsoft is not None
            and record["path"]
            == "runtime/Lib/site-packages/pikepdf.libs/msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll"
        ):
            continue
        if record["component_id"] == "paperworkshell" and (
            record["path"].startswith("runtime/Lib/site-packages/paperworkshell/")
            or record["path"].startswith("runtime/Lib/site-packages/paperworkshell-")
            or record["path"].startswith("sources/application/")
        ):
            continue
        name = record["path"]
        _copy(base_stage / name, payload / name, maximum=record["size_bytes"])
        if file_hashes(payload / name) != (record["size_bytes"], record["sha256"], record["sha1"]):
            fail("INSTALLER_BASE_COPY_CHANGED")
        owners[name] = record["component_id"]
    stage_gui_interpreter(base_stage, payload, owners, base["files"])
    for item in base["input_artifacts"]:
        if item["name"] in {wheel.name, sdist.name}:
            continue
        _copy(base_artifacts / item["name"], artifacts / item["name"], maximum=item["size_bytes"])
        verify_artifact(
            artifacts / item["name"],
            Artifact(item["name"], item["url"] or "", item["size_bytes"], item["sha256"]),
        )
    current_inputs = []
    for source in (wheel, sdist):
        _copy(source, artifacts / source.name)
        item = application_inputs[source.name]
        verify_artifact(
            artifacts / source.name, Artifact(source.name, "", item["size_bytes"], item["sha256"])
        )
        current_inputs.append(item)
    staged, _ = stage_wheel(
        artifacts / wheel.name,
        Artifact(wheel.name, "", wheel.stat().st_size, file_hashes(wheel)[1]),
        payload,
        "paperworkshell",
        exclusions={},
    )
    owners.update({name: "paperworkshell" for name in staged})
    app_source = "sources/application/" + sdist.name
    _copy(sdist, payload / app_source, expected_sha256=application_inputs[sdist.name]["sha256"])
    owners[app_source] = "paperworkshell"
    installer_source = artifacts / "paperworkshell-installer-source.tar.xz"
    _source_archive(root, installer_source, recipe_bindings)
    current_inputs.append(_input(installer_source, "source", "tools/build_windows_installer.py"))
    source_name = "sources/application/" + installer_source.name
    _copy(installer_source, payload / source_name, expected_sha256=current_inputs[-1]["sha256"])
    owners[source_name] = "paperworkshell"
    first_party = {
        "runtime/paperworkshell-installed-v1.json": marker(configuration),
        "notices/PaperWorkShell-MIT-LICENSE.txt": (root / "LICENSE").read_bytes(),
        "notices/PaperWorkShell-LICENSING.md": (root / "LICENSING.md").read_bytes(),
        "notices/UNSIGNED_INTERNAL.txt": (
            b"PaperWorkShell - UNSIGNED INTERNAL ENGINEERING CANDIDATE\n\n"
            b"Not a public release. No publicly trusted product signature is present.\n"
            + (
                b"Complete app-local Microsoft runtime is included under the recorded Community route; clean-machine qualification remains pending.\n"
                if microsoft is not None
                else b"Microsoft runtime redistribution and clean-machine qualification remain pending.\n"
            )
            + b"Do not publish this artifact or disable Windows security controls.\n"
            b"No account, activation, licence key, telemetry or document upload.\n"
        ),
        "evidence/pws0801-base-inventory.json": base_inventory.read_bytes(),
    }
    for name, raw in first_party.items():
        _write(payload / name, raw)
        owners[name] = "paperworkshell"
    _copy(
        launcher, payload / "runtime/PaperWorkShell.exe", expected_sha256=native_launcher["sha256"]
    )
    owners["runtime/PaperWorkShell.exe"] = "paperworkshell"
    _copy(
        launcher_receipt,
        payload / "evidence/launcher-receipt.json",
        expected_sha256=launcher_receipt_sha256,
    )
    owners["evidence/launcher-receipt.json"] = "paperworkshell"
    for record in review["license_files"]:
        name = "notices/installer/" + Path(record["path"]).name
        _copy(root / record["path"], payload / name, expected_sha256=record["sha256"])
        owners[name] = "inno-setup"
    source = root / review["source_local_path"]
    if file_hashes(source)[1] != review["source_sha256"]:
        fail("INSTALLER_COMPILER_SOURCE")
    inno_source_name = "sources/installer/" + source.name
    _copy(source, payload / inno_source_name, expected_sha256=review["source_sha256"])
    owners[inno_source_name] = "inno-setup"
    for record in (review["installer"], review["source_artifact"]):
        source = root / record["local_path"]
        _copy(source, artifacts / record["name"], expected_sha256=record["sha256"])
        current_inputs.append(
            {k: record[k] for k in ("name", "url", "size_bytes", "sha256", "scope")}
        )
    result = copy.deepcopy(base)
    result["candidate"] = {
        "id": configuration["candidate_id"],
        "version": configuration["version"],
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_revision": "source-snapshot:" + file_hashes(sdist)[1],
        "source_sha256": file_hashes(sdist)[1],
    }
    result["input_artifacts"].extend(current_inputs)
    # New application inputs have different bytes but the same distribution filename.
    by_name = {item["name"]: item for item in result["input_artifacts"]}
    result["input_artifacts"] = list(by_name.values())
    app = next(c for c in result["components"] if c["component_id"] == "paperworkshell")
    app.update(
        supplier="Janne Vuorela",
        license_expression="MIT",
        artifact_sha256=file_hashes(wheel)[1],
        license_texts=[
            _license(
                root / "LICENSE",
                "notices/PaperWorkShell-MIT-LICENSE.txt",
                file_hashes(root / "LICENSE")[1],
            )
        ],
    )
    app["dependencies"].append("inno-setup")
    result["components"].append(
        {
            "component_id": "inno-setup",
            "name": "Inno Setup installer runtime and compiler",
            "version": "7.1.1",
            "type": "application",
            "scope": "runtime",
            "supplier": "Jordan Russell and Martijn Laan",
            "purl": "pkg:generic/inno-setup@7.1.1",
            "license_expression": "LicenseRef-PWS-Inno-Setup-7.1.1",
            "license_texts": [
                _license(root / r["path"], "notices/installer/" + Path(r["path"]).name, r["sha256"])
                for r in review["license_files"]
            ],
            "source_url": review["source_url"],
            "artifact_sha256": review["installer"]["sha256"],
            "dependencies": [],
        }
    )
    if microsoft is not None:
        assert microsoft_overlay is not None
        integrate_microsoft_runtime(
            payload, owners, result, microsoft_overlay, microsoft, root=root
        )
    result["license_refs"].append(
        {
            "id": "LicenseRef-PWS-Inno-Setup-7.1.1",
            "name": "Inno Setup 7.1.1 and bundled-source notices",
            "text": "\n\n".join(
                (root / r["path"]).read_bytes().decode("utf-8") for r in review["license_files"]
            ),
        }
    )
    obligation = next(c for c in result["obligations"] if c["component_id"] == "paperworkshell")
    obligation.update(
        source_paths=[app_source, source_name],
        distribution_note="MIT applies only to authorized first-party material. Unsigned internal installer; public signing and clean-machine qualification remain separate gates. Microsoft runtime delivery and applicable route are recorded explicitly in the build plan.",
    )
    python_obligation = next(
        item for item in result["obligations"] if item["component_id"] == "cpython"
    )
    python_obligation["distribution_note"] += (
        " Packaging change summary: runtime/PaperWorkShellDesktop.exe is a filename-only, "
        "byte-identical copy of upstream pythonw.exe; original interpreter, metadata, "
        "notices and all CPython terms remain unchanged. See "
        "notices/PaperWorkShell-CPython-GUI-ALIAS.txt."
    )
    result["obligations"].append(
        {
            "component_id": "inno-setup",
            "source_paths": [inno_source_name],
            "replacement_instructions_path": None,
            "distribution_note": "Exact upstream compiler and installer runtime, unchanged notices and complete pinned source. The final installer/uninstaller file identities are in the separate installer receipt, outside the payload to avoid self-hashes.",
        }
    )
    result["review_inputs"] = [
        {"path": name, "sha256": recipe_bindings[name]} for name in sorted(review_paths)
    ]
    result["files"] = inventory_files(payload, owners)
    notice = (
        "Installed component/notice report. Exact final inventory and artifact hashes are separate sidecars.\n\n"
        + "\n".join(
            line
            for line in build_notices(result).splitlines()
            if not line.startswith(("Inventory SHA-256:", "Owned staged files:"))
        )
        + "\n"
    ).encode()
    _write(payload / "notices/COMPONENTS.txt", notice)
    owners["notices/COMPONENTS.txt"] = "paperworkshell"
    result["files"] = inventory_files(payload, owners)
    validate_data(result, root=root)
    for name, digest in (recipe_bindings | source_bindings).items():
        if file_hashes(root / name)[1] != digest:
            fail("INSTALLER_SOURCE_CHANGED")
    verify_launcher(
        launcher, launcher_receipt, root=root, expected_receipt_sha256=launcher_receipt_sha256
    )
    _write(output / "resolved-inventory.json", canonical_json(result))
    _write(output / "bom.cdx.json", canonical_json(build_cyclonedx(result)))
    _write(output / "bom.spdx.json", canonical_json(build_spdx(result)))
    _write(output / "THIRD_PARTY_NOTICES.txt", build_notices(result).encode())
    if microsoft is not None:
        # Reauthenticate all legal/acquisition/material bindings after copying.
        microsoft_runtime_input(microsoft_overlay, microsoft_overlay_receipt_sha256, root=root)
    generate_includes(
        output, payload, configuration, result["files"], complete_runtime=microsoft is not None
    )
    plan = {
        "schema_version": 1,
        "candidate_id": configuration["candidate_id"],
        "status": "unsigned-internal-stage",
        "public_distribution_allowed": False,
        "microsoft_runtime_delivery": "app-local-community2022"
        if microsoft is not None
        else "external-prerequisite-pending-entitlement",
        "microsoft_overlay_receipt_sha256": microsoft_overlay_receipt_sha256,
        "microsoft_recipient_terms_sha256": microsoft["approved_receipt_inputs"][
            "recipient_terms_sha256"
        ]
        if microsoft is not None
        else None,
        "base_inventory_sha256": BASE_SHA256,
        "inventory_sha256": file_hashes(output / "resolved-inventory.json")[1],
        "compiler_review_sha256": file_hashes(root / COMPILER_REVIEW)[1],
        "script_sha256": file_hashes(root / SCRIPT)[1],
        "product_sha256": file_hashes(root / PRODUCT)[1],
        "generated_inputs": [
            {"path": name, "sha256": file_hashes(output / name)[1]}
            for name in ("config.iss.inc", "files.iss.inc", "checks.iss.inc")
        ],
    }
    _write(output / "build-plan.json", canonical_json(plan))
    return plan


def compile_installer(
    candidate: Path, compiler: Path, *, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    candidate = owned_output(candidate, root=root)
    compiler = compiler.absolute()
    plan = load_json(candidate / "build-plan.json")
    if (
        type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
        or plan.get("status") != "unsigned-internal-stage"
        or plan.get("public_distribution_allowed") is not False
    ):
        fail("INSTALLER_BUILD_MODE")
    for name, expected in (
        (COMPILER_REVIEW, plan["compiler_review_sha256"]),
        (SCRIPT, plan["script_sha256"]),
        (PRODUCT, plan["product_sha256"]),
    ):
        if file_hashes(root / name)[1] != expected:
            fail("INSTALLER_RECIPE_CHANGED")
    if file_hashes(candidate / "resolved-inventory.json")[1] != plan["inventory_sha256"]:
        fail("INSTALLER_INVENTORY_CHANGED")
    inventory = load_json(candidate / "resolved-inventory.json")
    generated = plan.get("generated_inputs")
    if (
        not isinstance(generated, list)
        or len(generated) != 3
        or {item.get("path") for item in generated if isinstance(item, dict)}
        != {"config.iss.inc", "files.iss.inc", "checks.iss.inc"}
    ):
        fail("INSTALLER_GENERATED_INPUT")
    for item in generated:
        if safe_relative(item["path"]) not in {"config.iss.inc", "files.iss.inc", "checks.iss.inc"}:
            fail("INSTALLER_GENERATED_INPUT")
        if file_hashes(candidate / item["path"])[1] != item["sha256"]:
            fail("INSTALLER_GENERATED_INPUT_CHANGED")
    verify_files(candidate / "payload", inventory["files"])
    verify_compiler(compiler, root=root)
    config = candidate / "config.iss.inc"
    if (candidate / "installer").exists():
        fail("INSTALLER_OUTPUT_EXISTS")
    _run(
        [
            str(compiler / "ISCC.exe"),
            "--no-ide-signtools",
            "/Qp",
            "/DPWSGeneratedConfig=" + str(config),
            str(root / SCRIPT),
        ],
        candidate,
        "compile-installer",
        seconds=600,
        root=root,
    )
    for item in plan["generated_inputs"]:
        if file_hashes(candidate / item["path"])[1] != item["sha256"]:
            fail("INSTALLER_GENERATED_INPUT_CHANGED")
    verify_compiler(compiler, root=root)
    verify_files(candidate / "payload", inventory["files"])
    outputs = list((candidate / "installer").glob("*.exe"))
    if len(outputs) != 1:
        fail("INSTALLER_OUTPUT_IDENTITY")
    output = outputs[0]
    size, digest, _ = file_hashes(output, MAX_STAGE_BYTES)
    receipt = {
        **plan,
        "status": "unsigned-internal-built",
        "installer": output.name,
        "installer_size_bytes": size,
        "installer_sha256": digest,
        "payload_file_count": len(inventory["files"]),
        "payload_expanded_bytes": sum(r["size_bytes"] for r in inventory["files"]),
        "qualification": "not-run",
        "public_trusted_signature": False,
    }
    _write(candidate / "installer-receipt.json", canonical_json(receipt))
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prep = actions.add_parser("prepare-compiler")
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--artifacts", type=Path, required=True)
    prep.add_argument("--fetch", action="store_true")
    stage = actions.add_parser("stage")
    for name in (
        "output",
        "base-stage",
        "base-inventory",
        "base-artifacts",
        "wheel",
        "sdist",
        "launcher",
        "launcher-receipt",
        "compiler",
    ):
        stage.add_argument("--" + name, type=Path, required=True)
    stage.add_argument("--launcher-receipt-sha256", required=True)
    stage.add_argument("--microsoft-overlay", type=Path)
    stage.add_argument("--microsoft-overlay-receipt-sha256")
    compile_action = actions.add_parser("compile")
    compile_action.add_argument("--candidate", type=Path, required=True)
    compile_action.add_argument("--compiler", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare-compiler":
            prepare_compiler(args.output, args.artifacts, fetch=args.fetch)
            result: Mapping[str, Any] = {"status": "verified-compiler"}
        elif args.action == "stage":
            result = assemble(
                args.output,
                args.base_stage,
                args.base_inventory,
                args.base_artifacts,
                args.wheel,
                args.sdist,
                args.launcher,
                args.launcher_receipt,
                args.launcher_receipt_sha256,
                args.compiler,
                microsoft_overlay=args.microsoft_overlay,
                microsoft_overlay_receipt_sha256=args.microsoft_overlay_receipt_sha256,
            )
        else:
            result = compile_installer(args.candidate, args.compiler)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (WindowsInstallerError, ReleaseArtifactError) as exc:
        print(
            "ERROR [" + str(exc) + "] Windows installer preparation failed safely.", file=sys.stderr
        )
        return 1
    except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
        print(
            "ERROR [INSTALLER_BUILD_FAILED] Windows installer preparation failed safely.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
