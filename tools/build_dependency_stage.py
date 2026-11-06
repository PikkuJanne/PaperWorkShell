"""Assemble the reviewed PWS-0801 internal dependency candidate, offline by default.

The destination and every cache must be explicit repository output directories.
Only named, hash-authenticated inputs are copied. The explicit external GPG verifier
and its vendor libraries are authenticated before its subprocess runs. Sidecars
belong outside the payload. Installer and public-release facts remain TBD.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from tools.audit_tesseract_redistribution import (
    ArchiveSpec,
    SelectedFile,
    inspect_archive,
)
from tools.build_libtiff_no_jbig import REQUIRED_SIGNER, extract_tar, verify_signature
from tools.release_artifacts import (
    MAX_ARTIFACT_BYTES,
    Artifact,
    ReleaseArtifactError,
    acquire,
    extract_selected_zip,
    file_hashes,
    plain_path,
    safe_relative,
    verify_artifact,
)
from tools.release_evidence import load_json, validate_data
from tools.release_staging import canonical_json, inventory_files, stage_wheel, wheel_selection

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
REVIEW_DIRECTORY: Final = "packaging/release-reviews/"
REVIEW_NAMES: Final = (
    "python-inputs-v1.json",
    "python-components-v1.json",
    "native-components-v1.json",
    "python-native-components-v1.json",
    "qt-pdfium-components-v1.json",
    "python-rust-components-v1.json",
    "build-tool-components-v1.json",
    "cpython-inputs-v1.json",
    "lxml-exclusions-v1.json",
    "lxml-source-subset-v1.json",
    "runtime-omissions-v1.json",
    "validation-build-components-v1.json",
    "gpg-build-components-v1.json",
    "cmake-embedded-build-components-v1.json",
)
COMPONENT_FIELDS: Final = (
    "component_id",
    "name",
    "version",
    "type",
    "scope",
    "supplier",
    "purl",
    "license_expression",
    "license_texts",
    "source_url",
    "artifact_sha256",
    "dependencies",
)
CRT_NAMES: Final = frozenset(
    {
        "concrt140.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    }
)
PIKEPDF_CRT: Final = "msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll"
PIKEPDF_CRT_SHA256: Final = "0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9"
GERMAN_PACKAGE: Final = "mingw-w64-ucrt-x86_64-tesseract-data-deu"
GERMAN_ARCHIVE_SHA256: Final = "0d1bfa3838617fa0a6c439da638926e50f1186b87a247b3dd5ab3eac25887e41"
ISOLATED_PATH: Final = b"python314.zip\n.\nLib/site-packages\n"


def output_path(path: Path, root: Path) -> Path:
    """Check the original spelling before resolving; a junction cannot hide here."""
    plain_path(path)
    resolved = path.absolute().resolve()
    output = (root / "output").resolve()
    if resolved == output or not resolved.is_relative_to(output):
        raise ReleaseArtifactError("RELEASE_OUTPUT_SCOPE")
    return resolved


def repository_path(value: str, root: Path) -> Path:
    relative = safe_relative(value)
    path = root / relative
    plain_path(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ReleaseArtifactError("RELEASE_PATH")
    return path


def artifact_identity(item: Mapping[str, Any]) -> Artifact:
    return Artifact(
        item.get("name", item.get("artifact", item.get("filename", ""))),
        item.get("url") or "",
        item["size_bytes"],
        item["sha256"],
    )


class Assembly:
    """One owned candidate; this is deliberately specific to the reviewed inputs."""

    def __init__(
        self,
        root: Path,
        stage: Path,
        artifacts: Path,
        caches: Sequence[Path],
        *,
        fetch: bool,
    ) -> None:
        self.root, self.stage, self.artifacts = root, stage, artifacts
        self.caches = tuple(caches)
        self.fetch = fetch
        self.inputs: dict[str, dict[str, Any]] = {}
        self.components: dict[str, dict[str, Any]] = {}
        self.owners: dict[str, str] = {}
        self.asset_owners: dict[str, str] = {}
        self.references: dict[str, dict[str, str]] = {}
        self.review_inputs: dict[str, str] = {}
        self.omissions: dict[str, list[str]] = {}

    def review(self, relative: str) -> dict[str, Any]:
        path = repository_path(relative, self.root)
        payload = load_json(path)
        if payload.get("schema_version") not in {1, 2}:
            raise ReleaseArtifactError("RELEASE_REVIEW_VERSION")
        self.review_inputs[relative] = file_hashes(path)[1]
        return payload

    def input(
        self,
        item: Mapping[str, Any],
        scope: str,
        *,
        recipe: str | None = None,
        local: Path | None = None,
    ) -> Path:
        expected = artifact_identity(item)
        safe_relative(expected.name)
        if Path(expected.name).name != expected.name:
            raise ReleaseArtifactError("RELEASE_INPUT_NAME")
        record: dict[str, Any] = {
            "name": expected.name,
            "url": item.get("url"),
            "size_bytes": expected.size_bytes,
            "sha256": expected.sha256,
            "scope": scope,
        }
        if record["url"] is None:
            if recipe is None or recipe not in self.review_inputs:
                raise ReleaseArtifactError("RELEASE_INPUT_SOURCE")
            record.update(origin=item.get("origin", "local-build"), recipe_path=recipe)
        existing = self.inputs.get(expected.name.casefold())
        if existing is not None:
            if any(existing[key] != record[key] for key in ("name", "size_bytes", "sha256")):
                raise ReleaseArtifactError("RELEASE_INPUT_COLLISION")
            if scope == "runtime":
                existing["scope"] = scope
            verify_artifact(self.artifacts / expected.name, expected)
            return self.artifacts / expected.name
        target = self.artifacts / expected.name
        plain_path(target)
        if target.exists():
            verify_artifact(target, expected)
        else:
            candidates = [directory / expected.name for directory in self.caches]
            if item.get("local_path"):
                candidates.insert(0, repository_path(item["local_path"], self.root))
            if local is not None:
                candidates.insert(0, local)
            source = next((candidate for candidate in candidates if candidate.is_file()), None)
            if source is None:
                if not self.fetch or not expected.url:
                    raise ReleaseArtifactError("RELEASE_INPUT_MISSING")
                acquire(expected, self.artifacts)
            else:
                verify_artifact(source, expected)
                self._copy(source, target)
                verify_artifact(target, expected)
        self.inputs[expected.name.casefold()] = record
        return target

    @staticmethod
    def _copy(source: Path, target: Path) -> None:
        plain_path(source)
        plain_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with target.open("xb") as output, source.open("rb") as stream:
                created = True
                copied = 0
                while chunk := stream.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_ARTIFACT_BYTES:
                        raise ReleaseArtifactError("RELEASE_LIMIT")
                    output.write(chunk)
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise

    def bytes(self, relative: str, data: bytes, owner: str) -> None:
        relative = safe_relative(relative)
        if relative in self.owners:
            if (self.stage / relative).read_bytes() != data:
                raise ReleaseArtifactError("RELEASE_COLLISION")
            return
        if any(existing.casefold() == relative.casefold() for existing in self.owners):
            raise ReleaseArtifactError("RELEASE_COLLISION")
        path = self.stage / relative
        plain_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(data)
        self.owners[relative] = owner

    def stage_input(self, item: Mapping[str, Any], owner: str, *, recipe: str | None = None) -> str:
        source = self.input(item, "source", recipe=recipe)
        relative = safe_relative(item["stage_path"])
        if relative not in self.owners:
            if any(path.casefold() == relative.casefold() for path in self.owners):
                raise ReleaseArtifactError("RELEASE_COLLISION")
            self._copy(source, self.stage / relative)
            self.owners[relative] = owner
        elif file_hashes(self.stage / relative)[1] != item["sha256"]:
            raise ReleaseArtifactError("RELEASE_COLLISION")
        return relative

    def text(self, item: Mapping[str, Any], owner: str) -> dict[str, str]:
        path = repository_path(item["asset_path"], self.root)
        size, digest, _ = file_hashes(path, 1024 * 1024)
        if digest != item["sha256"] or ("size_bytes" in item and size != item["size_bytes"]):
            raise ReleaseArtifactError("RELEASE_NOTICE_HASH")
        encoding = item.get("encoding", item.get("source_encoding", "utf-8"))
        if encoding not in {"utf-8", "utf-8-sig", "latin-1"}:
            raise ReleaseArtifactError("RELEASE_NOTICE_ENCODING")
        data = path.read_bytes()
        text = data.decode(encoding)
        if text.encode(encoding) != data:
            raise ReleaseArtifactError("RELEASE_NOTICE_ENCODING")
        self.bytes(item["path"], data, owner)
        self.asset_owners[item["path"]] = self.owners[item["path"]]
        return {"path": item["path"], "sha256": digest, "text": text, "encoding": encoding}

    def add_components(self, items: Sequence[Mapping[str, Any]]) -> None:
        for item in items:
            cid = item["component_id"]
            raw = copy.deepcopy(dict(item))
            if cid in self.components and self.components[cid] != raw:
                raise ReleaseArtifactError("RELEASE_COMPONENT_CONFLICT")
            self.components[cid] = raw

    def override(self, item: Mapping[str, Any]) -> None:
        cid = item.get("component_id", item.get("name"))
        if cid not in self.components:
            raise ReleaseArtifactError("RELEASE_OVERRIDE_COMPONENT")
        component = self.components[cid]
        for key, value in item.items():
            if key in {"component_id", "name"}:
                continue
            if key == "dependencies_add":
                component["dependencies"] = sorted(set(component["dependencies"]) | set(value))
            elif key == "license_texts_add":
                by_path = {text["path"]: text for text in component["license_texts"]}
                for text in value:
                    if text["path"] in by_path and by_path[text["path"]] != text:
                        raise ReleaseArtifactError("RELEASE_NOTICE_CONFLICT")
                    by_path[text["path"]] = text
                component["license_texts"] = list(by_path.values())
            else:
                component[key] = copy.deepcopy(value)

    def refs(self, items: Sequence[Mapping[str, Any]]) -> None:
        for item in items:
            identifier = item.get("id", item.get("license_id", item.get("identifier")))
            if "text" in item:
                text = item["text"]
            else:
                data = repository_path(item["asset_path"], self.root).read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ReleaseArtifactError("RELEASE_NOTICE_HASH")
                text = data.decode(item.get("encoding", "utf-8"))
            value = {"id": identifier, "name": item["name"], "text": text}
            if identifier in self.references and self.references[identifier] != value:
                raise ReleaseArtifactError("RELEASE_LICENSE_REF_CONFLICT")
            self.references[identifier] = value


def stage_wheel_input(
    assembly: Assembly,
    item: Mapping[str, Any],
    cid: str,
    exclusions: Mapping[str, str],
) -> None:
    path = assembly.input(item, "runtime")
    expected = artifact_identity(item)
    selected = wheel_selection(path, expected, cid, exclusions)
    selected = {
        name: output
        for name, output in selected.items()
        if Path(name).name.casefold() not in CRT_NAMES
    }
    with zipfile.ZipFile(path) as archive:
        assembly.omissions[cid] = sorted(
            name for name in archive.namelist() if not name.endswith("/") and name not in selected
        )
    written = extract_selected_zip(path, expected, assembly.stage, selected)
    for relative in written:
        if relative in assembly.owners:
            raise ReleaseArtifactError("RELEASE_COLLISION")
        owner = cid
        if relative.endswith("/ocrmypdf/data/NotoSans-Regular.ttf"):
            owner = "noto-sans"
        if Path(relative).name.casefold() == PIKEPDF_CRT:
            if file_hashes(assembly.stage / relative)[1] != PIKEPDF_CRT_SHA256:
                raise ReleaseArtifactError("RELEASE_CRT_IDENTITY")
            owner = "pikepdf-msvc-runtime"
        assembly.owners[relative] = owner


def verify_signer_metadata(package: Mapping[str, Any], keyring: Mapping[str, Any]) -> None:
    """Only the exact reviewed German archive may inherit the recipe's pinned signer."""
    signer = package.get("expected_signer_fingerprint")
    if (
        signer is None
        and package.get("package_id") == GERMAN_PACKAGE
        and package.get("sha256") == GERMAN_ARCHIVE_SHA256
    ):
        signer = keyring["required_signer_fingerprint"]
    if signer != REQUIRED_SIGNER or signer != keyring["required_signer_fingerprint"]:
        raise ReleaseArtifactError("RELEASE_SIGNER_IDENTITY")


def verify_gpg_tool(executable: Path, review: Mapping[str, Any]) -> None:
    """Authenticate the actual verifier and every reviewed vendor DLL before execution.

    The independent review binds these exact14objects to a pinned official vendor
    archive. The caller may supply either that installed tool or an isolated copy;
    its unshipped files never become product runtime payload.
    """
    files = review["tool_files"]
    if len(files) != 14 or executable.name.casefold() != "gpg.exe":
        raise ReleaseArtifactError("RELEASE_GPG_IDENTITY")
    seen: set[str] = set()
    for item in files:
        name = safe_relative(item["name"])
        if Path(name).name != name or name.casefold() in seen:
            raise ReleaseArtifactError("RELEASE_GPG_IDENTITY")
        seen.add(name.casefold())
        verify_artifact(
            executable.parent / name,
            Artifact(name, "", item["size_bytes"], item["sha256"]),
        )
    if "gpg.exe" not in seen or any(
        dependency not in seen for item in files for dependency in item["local_dependencies"]
    ):
        raise ReleaseArtifactError("RELEASE_GPG_IDENTITY")


def verify_msys_signatures(
    assembly: Assembly,
    packages: Sequence[Mapping[str, Any]],
    recipe: Mapping[str, Any],
    work: Path,
    executable: Path,
) -> None:
    keyring = recipe["keyring"]
    source = assembly.input(keyring, "review")
    extract_tar(source, work / "keyring")
    public = work / "keyring" / keyring["public_key_path"]
    verify_artifact(
        public,
        Artifact(public.name, "", keyring["public_key_size_bytes"], keyring["public_key_sha256"]),
    )
    home = work / "gpg-home"
    home.mkdir()
    plain_path(executable)
    process = subprocess.run(
        [
            str(executable),
            "--batch",
            "--homedir",
            home.relative_to(assembly.root).as_posix(),
            "--dearmor",
            "--output",
            (home / "pubring.gpg").relative_to(assembly.root).as_posix(),
            public.relative_to(assembly.root).as_posix(),
        ],
        cwd=assembly.root,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if process.returncode:
        raise ReleaseArtifactError("RELEASE_KEYRING_INVALID")
    for package in packages:
        verify_signer_metadata(package, keyring)
        source = assembly.input(package, "runtime")
        signature = assembly.input(
            {
                "name": package["signature_artifact"],
                "url": package["signature_url"],
                "size_bytes": package["signature_size_bytes"],
                "sha256": package["signature_sha256"],
            },
            "review",
        )
        verify_signature(
            executable, home, signature, source, root=assembly.root, keyring_name="pubring.gpg"
        )


def selected_tar_bytes(path: Path, spec: ArchiveSpec) -> dict[str, bytes]:
    """Reconcile all selected bytes after the original bounded metadata audit.

    The historical audit intentionally retains just its five PE proof objects.
    Reopen a size/hash-verified compressed snapshot for this explicit staging subset;
    never expand or follow archive paths through a filesystem extraction API.
    """
    inspect_archive(path, spec)
    plain_path(path)
    with path.open("rb") as source:
        data = source.read(spec.size_bytes + 1)
    if len(data) != spec.size_bytes or hashlib.sha256(data).hexdigest() != spec.sha256:
        raise ReleaseArtifactError("RELEASE_ARTIFACT_HASH")
    wanted = {"ucrt64/" + item.path: item for item in spec.files}
    selected: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r|zst") as archive:
        for member in archive:
            record = wanted.get(member.name)
            if record is None:
                continue
            if not member.isfile() or member.size != record.size_bytes:
                raise ReleaseArtifactError("RELEASE_SELECTION")
            stream = archive.extractfile(member)
            if stream is None:
                raise ReleaseArtifactError("RELEASE_SELECTION")
            with stream:
                content = stream.read(record.size_bytes + 1)
            if (
                len(content) != record.size_bytes
                or hashlib.sha256(content).hexdigest() != record.sha256
            ):
                raise ReleaseArtifactError("RELEASE_STAGED_HASH")
            selected[record.path] = content
    if set(selected) != {item.path for item in spec.files}:
        raise ReleaseArtifactError("RELEASE_SELECTION")
    return selected


def stage_tesseract(
    assembly: Assembly,
    closure: Mapping[str, Any],
    native: Mapping[str, Any],
    recipe: Mapping[str, Any],
    work: Path,
    executable: Path,
    gpg_review: Mapping[str, Any],
) -> None:
    verify_gpg_tool(executable, gpg_review)
    by_package = {
        c["input_artifact"]["package_id"]: c for c in native["components"] if "input_artifact" in c
    }
    packages = list(closure["packages"])
    german = next(
        c for c in native["components"] if c["component_id"] == "msys2-tesseract-data-deu"
    )
    packages.append(german["input_artifact"])
    verify_msys_signatures(assembly, packages, recipe, work, executable)
    for package in packages:
        component = by_package[package["package_id"]]
        files = [
            item for item in closure["files"] if item.get("package_id") == package["package_id"]
        ]
        if component is german:
            files = german["selected_files"]
        spec = ArchiveSpec(
            package["package_id"],
            package["version"],
            artifact_identity(package).name,
            package["size_bytes"],
            package["sha256"],
            tuple(component["package_license_labels"]),
            tuple(
                SelectedFile(
                    item["path"], package["package_id"], item["size_bytes"], item["sha256"]
                )
                for item in files
            ),
        )
        selected = selected_tar_bytes(assembly.artifacts / spec.artifact, spec)
        for item in files:
            assembly.bytes(
                "runtime/engines/tesseract/" + item["path"],
                selected[item["path"]],
                component["component_id"],
            )
    built = closure["source_builds"][0]
    if assembly.review_inputs[built["recipe"]] != built["recipe_sha256"]:
        raise ReleaseArtifactError("RELEASE_RECIPE_CHANGED")
    path = assembly.input(
        {
            "name": built["artifact"],
            "url": None,
            "size_bytes": built["size_bytes"],
            "sha256": built["sha256"],
            "local_path": "output/pws0801/native-rebuild/repeat-frozen/rebuilt/libtiff-6.dll",
        },
        "runtime",
        recipe=built["recipe"],
    )
    assembly.bytes(
        "runtime/engines/tesseract/bin/libtiff-6.dll", path.read_bytes(), "msys2-libtiff"
    )
    component = assembly.components["msys2-libtiff"]
    component.update(
        version=built["version"],
        supplier="PaperWorkShell project; upstream LibTIFF contributors",
        artifact_sha256=built["sha256"],
        purl=None,
    )
    component["dependencies"] = [cid for cid in component["dependencies"] if cid != "msys2-jbigkit"]


def stage_explicit_files(assembly: Assembly, review: Mapping[str, Any]) -> None:
    """The CLI qpdf/additional app review declares every extra archive member."""
    by_name = {artifact_identity(item).name: item for item in review.get("artifacts", [])}
    grouped: dict[str, dict[str, str]] = {}
    for item in review.get("stage_files", []):
        if Path(item["path"]).name.casefold() in CRT_NAMES:
            continue
        grouped.setdefault(item["artifact"], {})[item["member"]] = item["path"]
    for name, selected in grouped.items():
        record = by_name[name]
        path = assembly.input(record, "runtime")
        extract_selected_zip(path, artifact_identity(record), assembly.stage, selected)
    for item in review.get("stage_files", []):
        relative = item["path"]
        if Path(relative).name.casefold() in CRT_NAMES:
            continue
        size, digest, _ = file_hashes(assembly.stage / relative)
        if size != item["size_bytes"] or digest != item["sha256"] or relative in assembly.owners:
            raise ReleaseArtifactError("RELEASE_STAGED_HASH")
        assembly.owners[relative] = item["component_id"]


def assemble(
    *,
    artifacts: Path,
    destination: Path,
    inventory_output: Path,
    cache_directories: Sequence[Path],
    app_review: str,
    qpdf_review: str,
    gpg_executable: Path,
    candidate_id: str,
    created_utc: str,
    fetch: bool = False,
    root: Path = PROJECT_ROOT,
    support_review: str = REVIEW_DIRECTORY + "libtiff-build-support-v1.json",
    native_build_tools_review: str = REVIEW_DIRECTORY + "native-build-tool-components-v1.json",
) -> dict[str, object]:
    """Create a fresh stage and inventory; existing stages and receipts are immutable."""
    root = root.resolve()
    destination, artifacts, inventory_output = (
        output_path(path, root) for path in (destination, artifacts, inventory_output)
    )
    caches = tuple(output_path(path, root) for path in cache_directories)
    omissions_output = inventory_output.with_name(inventory_output.name + ".omissions.json")
    if (
        destination.exists()
        or inventory_output.exists()
        or omissions_output.exists()
        or artifacts == destination
        or inventory_output.is_relative_to(destination)
    ):
        raise ReleaseArtifactError("RELEASE_OUTPUT_EXISTS")
    if artifacts.is_relative_to(destination) or destination.is_relative_to(artifacts):
        raise ReleaseArtifactError("RELEASE_OUTPUT_SCOPE")
    artifacts.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pws-stage-", dir=destination.parent) as temporary:
        work = Path(temporary)
        stage = work / "payload"
        stage.mkdir()
        assembly = Assembly(root, stage, artifacts, caches, fetch=fetch)
        reviews = {name: assembly.review(REVIEW_DIRECTORY + name) for name in REVIEW_NAMES}
        support = assembly.review(support_review)
        native_build_tools = assembly.review(native_build_tools_review)
        app, qpdf = assembly.review(app_review), assembly.review(qpdf_review)
        closure = assembly.review("packaging/tesseract-runtime-closure-v2.json")
        recipe = assembly.review("packaging/libtiff-no-jbig-build-v1.json")
        for relative in (
            "tools/build_dependency_stage.py",
            "tools/release_artifacts.py",
            "tools/release_staging.py",
            "tools/release_evidence.py",
            "tools/release_sbom.py",
            "tools/audit_tesseract_redistribution.py",
            "tools/build_libtiff_no_jbig.py",
            "tools/stage_lxml_source.py",
            "pyproject.toml",
            "uv.lock",
        ):
            assembly.review_inputs[relative] = file_hashes(repository_path(relative, root))[1]
        for item in app["artifacts"]:
            if item.get("recipe_path"):
                relative = safe_relative(item["recipe_path"])
                assembly.review_inputs[relative] = file_hashes(repository_path(relative, root))[1]
        subset = reviews["lxml-source-subset-v1.json"]
        if "recipe" in subset:
            subset_recipe = subset["recipe"]
            if assembly.review_inputs.get(subset_recipe["path"]) != subset_recipe["sha256"]:
                raise ReleaseArtifactError("RELEASE_RECIPE_CHANGED")
        if (
            "approval_sha256" in subset
            and subset["approval_sha256"]
            != assembly.review_inputs[REVIEW_DIRECTORY + "lxml-exclusions-v1.json"]
        ):
            raise ReleaseArtifactError("RELEASE_APPROVAL_CHANGED")
        pyinputs, py = reviews["python-inputs-v1.json"], reviews["python-components-v1.json"]
        native, pynative = (
            reviews["native-components-v1.json"],
            reviews["python-native-components-v1.json"],
        )
        qt, rust, build = (
            reviews[name]
            for name in (
                "qt-pdfium-components-v1.json",
                "python-rust-components-v1.json",
                "build-tool-components-v1.json",
            )
        )
        validation, gpg, cmake_embedded = (
            reviews[name]
            for name in (
                "validation-build-components-v1.json",
                "gpg-build-components-v1.json",
                "cmake-embedded-build-components-v1.json",
            )
        )
        for review in (
            py,
            native,
            pynative,
            qt,
            rust,
            build,
            support,
            native_build_tools,
            qpdf,
            validation,
            gpg,
            cmake_embedded,
        ):
            assembly.add_components(review.get("components", []))
            assembly.refs(review.get("license_refs", []))
        cpython = reviews["cpython-inputs-v1.json"]["artifacts"][0]
        assembly.add_components(
            [
                {
                    "component_id": "cpython",
                    "name": "CPython embedded",
                    "version": "3.14.6",
                    "type": "framework",
                    "scope": "runtime",
                    "supplier": "Python Software Foundation",
                    "purl": "pkg:generic/cpython@3.14.6?arch=x86_64&os=windows",
                    "license_expression": "PSF-2.0",
                    "license_texts": [],
                    "source_url": "https://www.python.org/",
                    "artifact_sha256": cpython["sha256"],
                    "dependencies": [],
                }
            ]
        )
        for item in pynative["parent_overrides"]:
            assembly.override(item)
        for review in (qt, support, native_build_tools, validation, gpg, cmake_embedded):
            for item in review.get("component_overrides", []):
                assembly.override(item)
        if support.get("components"):
            assembly.override(
                {
                    "component_id": "msys2-libtiff",
                    "dependencies_add": [
                        component["component_id"] for component in support["components"]
                    ],
                }
            )
        for review in (rust, build, validation, gpg, cmake_embedded):
            for parent, evidence in review.get("parent_evidence", {}).items():
                if parent not in assembly.components:
                    raise ReleaseArtifactError("RELEASE_PARENT_MISSING")
                assembly.override(
                    {"component_id": parent, "dependencies_add": evidence["root_dependencies"]}
                )
            for component in assembly.components.values():
                parent = component.get("parent_component_id")
                # The older runtime Rust review records source crate digests as
                # auxiliary provenance and needs its enclosing wheel subject.
                # New validation-build records already distinguish source-only
                # crate artifacts from native sysroot copies; retain their subjects.
                if (
                    review is rust
                    and component["component_id"]
                    in {item["component_id"] for item in rust["components"]}
                    and parent in review.get("parent_evidence", {})
                ):
                    component["artifact_sha256"] = review["parent_evidence"][parent].get(
                        "wheel_sha256",
                        review["parent_evidence"][parent].get("enclosing_artifact_sha256"),
                    )
        # lxml's statically linked LGPL iconv requires the extension and all native relink sources.
        assembly.override(
            {
                "component_id": "lxml",
                "dependencies_add": [
                    c["component_id"]
                    for c in native["components"]
                    if c.get("enclosing_component") == "lxml"
                ],
            }
        )
        for review in (
            build,
            qt,
            qpdf,
            support,
            native_build_tools,
            validation,
            gpg,
            cmake_embedded,
        ):
            for item in review.get("artifacts", []):
                assembly.input(item, item.get("scope", "build"))
        for item in py.get("artifacts_add", []):
            assembly.input(item, "runtime")
        for item in recipe["build_inputs"]:
            assembly.input(item, "build")
        assembly.input(recipe["source"], "source")
        # The derivation consumed the original sdist. Authenticate it as an input,
        # while publishing only the approved filtered source archive below.
        if "input_source" in subset:
            assembly.input(subset["input_source"], "source")
        if file_hashes(root / "uv.lock")[1] != pyinputs["lock_sha256"]:
            raise ReleaseArtifactError("RELEASE_LOCK_CHANGED")
        for item in pyinputs["components"]:
            cid = item["component_id"]
            if cid == "pi-heif" or item["scope"] != "runtime":
                assembly.input(item["wheel"], "review" if cid == "pi-heif" else "build")
                continue
            exclusions = (
                {
                    entry["path"]: entry["sha256"]
                    for entry in reviews["lxml-exclusions-v1.json"]["exclusions"]
                }
                if cid == "lxml"
                else {}
            )
            stage_wheel_input(assembly, item["wheel"], cid, exclusions)
        path = assembly.input(cpython, "runtime")
        with zipfile.ZipFile(path) as archive:
            selected = {
                name: "runtime/" + name
                for name in archive.namelist()
                if not name.endswith("/")
                and Path(name).name.casefold() not in CRT_NAMES
                and name != "python314._pth"
            }
        for name in extract_selected_zip(path, artifact_identity(cpython), stage, selected):
            assembly.owners[name] = "cpython"
        assembly.bytes("runtime/python314._pth", ISOLATED_PATH, "cpython")
        stage_tesseract(assembly, closure, native, recipe, work, gpg_executable, gpg)
        stage_explicit_files(assembly, qpdf)
        appwheel = next(item for item in app["artifacts"] if item["name"].endswith(".whl"))
        appsource = next(
            item for item in app["artifacts"] if item["sha256"] == app["candidate"]["source_sha256"]
        )
        apppath = assembly.input(appwheel, "runtime", recipe=appwheel["recipe_path"])
        appsha = appsource["sha256"]
        if app["candidate"]["source_revision"] != "source-snapshot:" + appsha:
            raise ReleaseArtifactError("RELEASE_SOURCE_SNAPSHOT")
        if candidate_id != app["candidate"]["id"] or created_utc != app["candidate"]["created_utc"]:
            raise ReleaseArtifactError("RELEASE_CANDIDATE_IDENTITY")
        assembly.add_components(app["components"])
        if assembly.components["paperworkshell"]["artifact_sha256"] != appwheel["sha256"]:
            raise ReleaseArtifactError("RELEASE_APPLICATION_IDENTITY")
        written, _ = stage_wheel(apppath, artifact_identity(appwheel), stage, "paperworkshell")
        assembly.owners.update(dict.fromkeys(written, "paperworkshell"))
        appsource = dict(
            appsource,
            stage_path=appsource.get(
                "stage_path", "sources/app/" + artifact_identity(appsource).name
            ),
        )
        app_source_path = assembly.stage_input(
            appsource, "paperworkshell", recipe=appsource["recipe_path"]
        )
        source_for: dict[str, list[str]] = {cid: [] for cid in assembly.components}
        source_for["paperworkshell"] = [app_source_path]
        for item in app["artifacts"]:
            if item["name"] in {appwheel["name"], appsource["name"]}:
                continue
            if "stage_path" in item:
                source_for["paperworkshell"].append(
                    assembly.stage_input(item, "paperworkshell", recipe=item["recipe_path"])
                )
            else:
                assembly.input(item, item["scope"], recipe=item.get("recipe_path"))
        lxml_sources = [reviews["lxml-source-subset-v1.json"]["output_artifact"]] + [
            s
            for c in native["components"]
            if c.get("enclosing_component") == "lxml"
            for s in c["source_artifacts"]
        ]
        for item in lxml_sources:
            source_for["lxml"].append(
                assembly.stage_input(
                    dict(item, origin="derived-source") if not item.get("url") else item,
                    "lxml",
                    recipe="tools/stage_lxml_source.py",
                )
            )
        for c in assembly.components.values():
            cid, expression = c["component_id"], c["license_expression"]
            if cid.startswith("lxml-embedded-"):
                source_for[cid] = list(source_for["lxml"])
            elif c["scope"] == "runtime" and (
                any(s in expression for s in ("LGPL-", "MPL-", "GCC-exception"))
                or any(
                    item.get("required_for_corresponding_source")
                    for item in c.get("source_artifacts", [])
                )
            ):
                for item in c.get("source_artifacts", []):
                    source_for[cid].append(assembly.stage_input(item, cid))
            for item in c["license_texts"]:
                assembly.text(item, cid)
            if "input_artifact" in c and c["component_id"] != "msys2-libtiff":
                input_scope = (
                    c["scope"] if c["input_artifact"]["sha256"] == c["artifact_sha256"] else "build"
                )
                assembly.input(c["input_artifact"], input_scope)
        for item in qt["source_stage_paths"]:
            record = next(a for a in qt["artifacts"] if a["name"] == item["artifact"])
            source_path = assembly.stage_input(
                dict(record, stage_path=item["path"]), item["component_id"]
            )
            for cid in assembly.components:
                if cid in {"pyside6-essentials", "shiboken6"} or cid.startswith("qt6-"):
                    source_for[cid].append(source_path)
        for item in native["notice_assets"]:
            assembly.text(item, item["component_id"])
        for item in qt["assets"]:
            assembly.text(item, "qt6-core")
        msvc = native["msvc_license_review"]
        terms = msvc["license_assets"]
        mtexts = [assembly.text(item, "pikepdf-msvc-runtime") for item in terms]
        # These full terms document only the exact upstream downstream route and condition.
        assembly.components["pikepdf-msvc-runtime"] = {
            "component_id": "pikepdf-msvc-runtime",
            "name": "Microsoft Visual C++ Runtime (pikepdf exact mangled object)",
            "version": "14.44.35211.0",
            "type": "library",
            "scope": "runtime",
            "supplier": "Microsoft Corporation; redistributed by pikepdf",
            "purl": None,
            "license_expression": "LicenseRef-Pikepdf-MSVC-Downstream",
            "license_texts": terms,
            "source_url": "https://visualstudio.microsoft.com/license-terms/mlt031619/",
            "artifact_sha256": assembly.components["pikepdf"]["artifact_sha256"],
            "dependencies": [],
            "relationship": "embedded",
        }
        source_for["pikepdf-msvc-runtime"] = []
        assembly.references["LicenseRef-Pikepdf-MSVC-Downstream"] = {
            "id": "LicenseRef-Pikepdf-MSVC-Downstream",
            "name": "Exact pikepdf Microsoft downstream runtime route",
            "text": "\n\n".join(t["text"] for t in mtexts),
        }
        assembly.override({"component_id": "pikepdf", "dependencies_add": ["pikepdf-msvc-runtime"]})
        for prerequisite in qt["external_prerequisites"]:
            record = prerequisite["reference_installer"]
            assembly.input(
                {
                    "name": record["artifact"],
                    "url": record["resolved_url"],
                    "size_bytes": record["size_bytes"],
                    "sha256": record["sha256"],
                },
                "review",
            )
            external_id = prerequisite["component_id"]
            enduser = [item for item in terms if "END-USER" in item["asset_path"]]
            if not enduser:
                raise ReleaseArtifactError("RELEASE_EXTERNAL_TERMS_MISSING")
            assembly.add_components(
                [
                    {
                        "component_id": external_id,
                        "name": prerequisite["name"],
                        "version": prerequisite["verified_host_version"],
                        "type": "library",
                        "scope": "runtime",
                        "supplier": "Microsoft Corporation",
                        "purl": None,
                        "license_expression": "LicenseRef-Microsoft-External-Runtime",
                        "license_texts": enduser,
                        "source_url": "https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/",
                        "artifact_sha256": record["sha256"],
                        "dependencies": [],
                        "relationship": "external",
                    }
                ]
            )
            source_for[external_id] = []
            assembly.references["LicenseRef-Microsoft-External-Runtime"] = {
                "id": "LicenseRef-Microsoft-External-Runtime",
                "name": "Microsoft separately installed end-user runtime terms",
                "text": assembly.text(enduser[0], external_id)["text"],
            }
            for cid in (
                "cpython",
                "pyside6-essentials",
                "shiboken6",
                "pikepdf-msvc-runtime",
                "qpdf-cli",
            ):
                assembly.override({"component_id": cid, "dependencies_add": [external_id]})
        normalized, obligations = [], []
        for cid, component in sorted(assembly.components.items()):
            result = {key: component[key] for key in COMPONENT_FIELDS}
            result["license_texts"] = [
                assembly.text(item, cid) for item in component["license_texts"]
            ]
            result["dependencies"] = sorted(set(result["dependencies"]))
            for key in ("relationship", "artifact_url"):
                if key in component:
                    result[key] = component[key]
            normalized.append(result)
            guide = next(
                (
                    path
                    for path in assembly.owners
                    if path.startswith("notices/")
                    and cid in path
                    and any(
                        word in path for word in ("SOURCE-AND-REPLACEMENT", "SOURCE-AND-RELINKING")
                    )
                ),
                None,
            )
            if cid in {"pyside6-essentials", "shiboken6"} or cid.startswith("qt6-"):
                guide = "notices/qt-pdfium/DYNAMIC-REPLACEMENT-AND-SOURCE.txt"
            if cid in {"fpdf2", "img2pdf"}:
                guide = "notices/DEPENDENCY-SOURCE-AND-REPLACEMENT.txt"
            obligations.append(
                {
                    "component_id": cid,
                    "source_paths": sorted(set(source_for[cid])),
                    "replacement_instructions_path": guide,
                    "distribution_note": "Internal dependency stage. Preserve full upstream notices and supplied source. Final installer, signing and protective product terms remain PWS-0802.",
                }
            )
        assembly.bytes(
            "notices/DEPENDENCY-SOURCE-AND-REPLACEMENT.txt",
            b"The supplied LGPL Python source can be modified and installed in place of the corresponding runtime/Lib/site-packages library. Recipients may rebuild this application and its owned manifest from supplied source to accept modified libraries. Reverse engineering for debugging modifications is permitted. No signature or manifest policy is a prohibition on recipient replacement. Final installer paths and product terms remain PWS-0802.\n",
            "paperworkshell",
        )
        incoming = {cid for component in normalized for cid in component["dependencies"]}
        root_component = next(c for c in normalized if c["component_id"] == "paperworkshell")
        root_component["dependencies"] = sorted(
            set(root_component["dependencies"])
            | (set(assembly.components) - incoming - {"paperworkshell"})
        )
        inventory: dict[str, Any] = {
            "schema_version": 1,
            "inventory_profile": "resolved-dependency-stage",
            "candidate": dict(app["candidate"]),
            "target": {"os": "Windows", "architecture": "x86_64", "python_version": "3.14.6"},
            "components": normalized,
            "files": inventory_files(stage, assembly.owners),
            "license_refs": sorted(assembly.references.values(), key=lambda i: i["id"]),
            "input_artifacts": sorted(assembly.inputs.values(), key=lambda i: i["name"]),
            "review_inputs": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(assembly.review_inputs.items())
            ],
            "obligations": obligations,
        }
        validate_data(inventory, root=root)
        # The authenticated review omissions remain external evidence, never a forged RECORD.
        inventory_output.parent.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        try:
            with inventory_output.open("xb") as stream:
                created.append(inventory_output)
                stream.write(canonical_json(inventory))
            with omissions_output.open("xb") as stream:
                created.append(omissions_output)
                stream.write(
                    canonical_json(
                        {
                            "schema_version": 1,
                            "candidate_id": candidate_id,
                            "wheel_omissions": assembly.omissions,
                            "cpython_path_configuration": ISOLATED_PATH.decode("ascii"),
                            "scope": "Reviewed dependency subset; original wheel RECORD and upstream SBOMs are input-only. Installer and public release remain separately gated.",
                        }
                    )
                )
            stage.rename(destination)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return {
            "status": "assembled",
            "candidate_id": candidate_id,
            "components": len(normalized),
            "files": len(inventory["files"]),
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--inventory-output", type=Path, required=True)
    parser.add_argument("--cache-directory", type=Path, action="append", default=[])
    parser.add_argument("--app-review", required=True)
    parser.add_argument("--qpdf-review", required=True)
    parser.add_argument(
        "--support-review", default=REVIEW_DIRECTORY + "libtiff-build-support-v1.json"
    )
    parser.add_argument(
        "--native-build-tools-review",
        default=REVIEW_DIRECTORY + "native-build-tool-components-v1.json",
    )
    parser.add_argument("--gpg-executable", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--created-utc", required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assemble(
            artifacts=args.artifacts,
            destination=args.destination,
            inventory_output=args.inventory_output,
            cache_directories=args.cache_directory,
            app_review=args.app_review,
            qpdf_review=args.qpdf_review,
            support_review=args.support_review,
            native_build_tools_review=args.native_build_tools_review,
            gpg_executable=args.gpg_executable,
            candidate_id=args.candidate_id,
            created_utc=args.created_utc,
            fetch=args.fetch,
        )
    except Exception:
        print(json.dumps({"status": "invalid", "code": "RELEASE_STAGE_INVALID"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
