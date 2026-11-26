"""Exact-source and generated-input rejection checks for the installer pipeline."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tools import build_windows_installer as build
from tools.release_artifacts import ReleaseArtifactError
from tools.release_staging import inventory_files

_P256_GENERATOR = (
    "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
)


def _anchor(**changes: object) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "channel": "engineering",
            "root_public_key": _P256_GENERATOR,
            "minimum_epoch": 1,
            "release_sequence_floor": 17,
            **changes,
        }
    ).encode()


def _write_anchor(root: Path, raw: bytes) -> tuple[Path, str]:
    path = root / "output" / "anchor.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_update_anchor_requires_a_build_scoped_input_and_exact_hash_pair(tmp_path: Path) -> None:
    path, digest = _write_anchor(tmp_path, _anchor())
    assert build.update_anchor_input(None, None, channel="engineering", root=tmp_path) is None
    for value, sha in ((path, None), (None, digest), (path, digest.upper())):
        with pytest.raises(build.WindowsInstallerError, match="INSTALLER_UPDATE_ANCHOR_PAIR"):
            build.update_anchor_input(value, sha, channel="engineering", root=tmp_path)
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_INPUT_SCOPE"):
        build.update_anchor_input(
            tmp_path / "user.json", digest, channel="engineering", root=tmp_path
        )
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_UPDATE_ANCHOR_HASH"):
        build.update_anchor_input(path, "b" * 64, channel="engineering", root=tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        _anchor(schema_version=True),
        _anchor(channel="stable"),
        _anchor(root_public_key="0" * 128),
        _anchor(root_public_key=_P256_GENERATOR.upper()),
        _anchor(minimum_epoch=0),
        _anchor(release_sequence_floor=False),
        _anchor(release_sequence_floor=2**63),
        _anchor(unknown="private-path-canary"),
        _anchor().replace(b'"minimum_epoch": 1', b'"minimum_epoch": 1, "minimum_epoch": 2'),
        b"\xff",
    ],
)
def test_update_anchor_refuses_invalid_json_channel_key_and_policy(
    tmp_path: Path, raw: bytes
) -> None:
    path, digest = _write_anchor(tmp_path, raw)
    with pytest.raises(build.WindowsInstallerError, match="^INSTALLER_UPDATE_ANCHOR_FORMAT$"):
        build.update_anchor_input(path, digest, channel="engineering", root=tmp_path)


def test_update_anchor_size_is_bounded(tmp_path: Path) -> None:
    path, digest = _write_anchor(tmp_path, b"a" * (build.MAX_UPDATE_ANCHOR_BYTES + 1))
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_UPDATE_ANCHOR_SIZE"):
        build.update_anchor_input(path, digest, channel="engineering", root=tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Uses Windows CNG public point validation.")
def test_build_plan_binds_public_anchor_bytes_and_filename_to_ownership(tmp_path: Path) -> None:
    original = _anchor(release_sequence_floor=0)
    path, digest = _write_anchor(tmp_path, original)
    assert build.update_anchor_input(path, digest, channel="engineering", root=tmp_path) == original
    candidate = tmp_path / "output/pws0802/candidate"
    payload = candidate / "payload" / build.UPDATE_ANCHOR_PAYLOAD_PATH
    source = candidate / "artifacts" / build.UPDATE_ANCHOR_FILENAME
    for target in (payload, source):
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
    inventory = {
        "files": [
            {
                "path": build.UPDATE_ANCHOR_PAYLOAD_PATH,
                "component_id": "paperworkshell",
                "sha256": digest,
                "size_bytes": len(original),
            }
        ],
        "input_artifacts": [
            {
                "name": build.UPDATE_ANCHOR_FILENAME,
                "sha256": digest,
                "size_bytes": len(original),
                "origin": "local-build",
                "recipe_path": "tools/build_windows_installer.py",
                "scope": "runtime",
                "url": None,
            }
        ],
    }
    build.verify_build_update_anchor(
        candidate, {"files": [], "input_artifacts": []}, None, channel="engineering", root=tmp_path
    )
    build.verify_build_update_anchor(
        candidate, inventory, digest, channel="engineering", root=tmp_path
    )
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_UPDATE_ANCHOR_BINDING"):
        build.verify_build_update_anchor(
            candidate, inventory, None, channel="engineering", root=tmp_path
        )
    source.write_bytes(_anchor())
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_UPDATE_ANCHOR_HASH"):
        build.verify_build_update_anchor(
            candidate, inventory, digest, channel="engineering", root=tmp_path
        )


def _product(root: Path) -> dict[str, Any]:
    source = build.PROJECT_ROOT / build.PRODUCT
    value: dict[str, Any] = json.loads(source.read_bytes())
    target = root / build.PRODUCT
    target.parent.mkdir(parents=True)
    target.write_bytes(json.dumps(value).encode())
    return value


def _application(root: Path, *, code: bytes = b"value = 1\n") -> tuple[Path, Path]:
    target = root / "src/paperworkshell/__init__.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(code)
    wheel = root / "paperworkshell-0.0.0-py3-none-any.whl"
    sdist = root / "paperworkshell-0.0.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("paperworkshell/__init__.py", code)
        archive.writestr(
            "paperworkshell-0.0.0.dist-info/METADATA",
            "Name: paperworkshell\nVersion: 0.0.0\nLicense-Expression: MIT\n",
        )
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("paperworkshell-0.0.0/src/paperworkshell/__init__.py")
        member.size = len(code)
        archive.addfile(member, io.BytesIO(code))
    return wheel, sdist


def test_identity_is_exact_and_marker_contains_no_paths_or_trust_assertion(tmp_path: Path) -> None:
    configured = _product(tmp_path)
    assert build.product(tmp_path) == configured
    assert set(json.loads(build.marker(configured))) == {
        "schema_version",
        "application_id",
        "version",
        "channel",
        "candidate_id",
    }


def test_current_product_scope_is_native_windows_11_x64_only() -> None:
    configured = build.product()
    assert configured["os"] == "Windows 11"
    assert configured["architecture"] == "x64"
    assert configured["arm64_route"] == (
        "not supported; native ARM64 and x64 emulation are outside project scope"
    )
    script = (build.PROJECT_ROOT / build.SCRIPT).read_text(encoding="utf-8")
    directives = dict(
        line.split("=", 1)
        for line in script.splitlines()
        if line.startswith(("ArchitecturesAllowed=", "ArchitecturesInstallIn64BitMode="))
    )
    assert directives == {
        "ArchitecturesAllowed": "x64os",
        "ArchitecturesInstallIn64BitMode": "x64os",
    }
    for name in ("dependency-inventory-v1", "resolved-dependency-inventory-v1"):
        schema = json.loads((build.PROJECT_ROOT / f"schemas/{name}.schema.json").read_bytes())
        target = schema["properties"]["target"]
        if "$ref" in target:
            target = schema["$defs"]["target"]
        assert target["properties"]["architecture"] == {"const": "x86_64"}


@pytest.mark.parametrize(
    "key,value",
    [
        ("architecture", "ARM64"),
        ("architecture", "x64 and ARM64"),
        ("arm64_route", "x64 emulation; not enabled until qualified"),
        ("arm64_route", "native ARM64 supported"),
        ("arm64_route", "ARM64 qualification required before PWS-0802 or PWS-0803"),
        ("arm64_route", "ARM64 qualification required before public distribution"),
    ],
)
def test_current_product_refuses_arm_support_or_qualification_prerequisites(
    tmp_path: Path, key: str, value: str
) -> None:
    configured = _product(tmp_path)
    configured[key] = value
    (tmp_path / build.PRODUCT).write_bytes(json.dumps(configured).encode())
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_PRODUCT_IDENTITY"):
        build.product(tmp_path)


def test_scope_policy_revision_does_not_change_existing_installed_marker() -> None:
    current = build.product()
    historical_r7 = current | {"arm64_route": "x64 emulation; not enabled until qualified"}
    # R7's authenticated source archive remains the build input.  This current
    # policy correction neither restages it nor changes its installed identity.
    assert build.marker(historical_r7) == build.marker(current)


@pytest.mark.parametrize(
    "key,value",
    [
        ("publisher", "Another publisher"),
        ("telemetry", True),
        ("first_party_license", "NOASSERTION"),
        ("schema_version", True),
        ("minimum_os_build", 19045),
        ("channel", "stable"),
    ],
)
def test_changed_identity_or_public_promotion_is_rejected(
    tmp_path: Path, key: str, value: Any
) -> None:
    configured = _product(tmp_path)
    configured[key] = value
    (tmp_path / build.PRODUCT).write_bytes(json.dumps(configured).encode())
    with pytest.raises(build.WindowsInstallerError):
        build.product(tmp_path)


def test_candidate_output_cannot_target_repository_or_external_data(tmp_path: Path) -> None:
    for target in (tmp_path, tmp_path / "output/pws0802", tmp_path / "user-export.pdf"):
        with pytest.raises(build.WindowsInstallerError, match="INSTALLER_OUTPUT_SCOPE"):
            build.owned_output(target, root=tmp_path)
    assert build.owned_output(tmp_path / "output/pws0802/new", root=tmp_path).name == "new"


def test_current_wheel_and_corresponding_source_are_equal(tmp_path: Path) -> None:
    _product(tmp_path)
    wheel, sdist = _application(tmp_path)
    assert build.application_sources(wheel, sdist, tmp_path) == {
        "src/paperworkshell/__init__.py": hashlib.sha256(b"value = 1\n").hexdigest(),
    }


def test_source_mutation_or_new_module_cannot_be_omitted_from_candidate(tmp_path: Path) -> None:
    _product(tmp_path)
    wheel, sdist = _application(tmp_path)
    source = tmp_path / "src/paperworkshell/__init__.py"
    source.write_bytes(b"value = 2\n")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_APPLICATION_SOURCE_CHANGED"):
        build.application_sources(wheel, sdist, tmp_path)
    source.write_bytes(b"value = 1\n")
    source.with_name("new_feature.py").write_bytes(b"new = True\n")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_APPLICATION_SOURCE_COVERAGE"):
        build.application_sources(wheel, sdist, tmp_path)


def test_different_corresponding_source_fails_even_when_wheel_matches(tmp_path: Path) -> None:
    _product(tmp_path)
    wheel, sdist = _application(tmp_path)
    raw = b"other = True\n"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("paperworkshell-0.0.0/src/paperworkshell/__init__.py")
        member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_APPLICATION_SOURCE_MISMATCH"):
        build.application_sources(wheel, sdist, tmp_path)


def test_wheel_cannot_install_another_package_namespace(tmp_path: Path) -> None:
    _product(tmp_path)
    wheel, sdist = _application(tmp_path)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("another_product/__init__.py", "")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_APPLICATION_NAMESPACE"):
        build.application_sources(wheel, sdist, tmp_path)


@pytest.mark.parametrize("version", ["01.0.0", "10000.0.0"])
def test_version_must_also_be_accepted_by_installed_runtime(tmp_path: Path, version: str) -> None:
    configured = _product(tmp_path)
    configured["version"] = version
    (tmp_path / build.PRODUCT).write_bytes(json.dumps(configured).encode())
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_ENGINEERING_IDENTITY"):
        build.product(tmp_path)


@pytest.mark.parametrize(
    "name,version,license_expression",
    [
        ("another-product", "0.0.0", "MIT"),
        ("paperworkshell", "9.9.9", "MIT"),
        ("paperworkshell", "0.0.0", "AGPL-3.0-only"),
    ],
)
def test_wheel_cannot_contradict_candidate_identity_or_mit_scope(
    tmp_path: Path,
    name: str,
    version: str,
    license_expression: str,
) -> None:
    _product(tmp_path)
    wheel, sdist = _application(tmp_path)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("paperworkshell/__init__.py", b"value = 1\n")
        archive.writestr(
            "paperworkshell-0.0.0.dist-info/METADATA",
            f"Name: {name}\nVersion: {version}\nLicense-Expression: {license_expression}\n",
        )
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_APPLICATION_METADATA"):
        build.application_sources(wheel, sdist, tmp_path)


def test_generated_entries_escape_pascal_quotes_and_never_use_wildcards(tmp_path: Path) -> None:
    configuration = _product(tmp_path)
    build.generate_includes(
        tmp_path, tmp_path / "payload", configuration, [{"path": "runtime/it's-a-file.txt"}]
    )
    checks = (tmp_path / "checks.iss.inc").read_text()
    assert "it''s-a-file.txt" in checks
    assert "runtime\\" in checks
    files = (tmp_path / "files.iss.inc").read_text()
    assert "recursesubdirs" not in files
    assert "*" not in files
    assert "PWSSignedBuild 0" in (tmp_path / "config.iss.inc").read_text()


@pytest.mark.parametrize(
    "path", ["../user.pdf", "runtime/{win}.txt", 'runtime/a"; DestDir: "elsewhere']
)
def test_unsafe_script_path_is_rejected(tmp_path: Path, path: str) -> None:
    configuration = _product(tmp_path)
    with pytest.raises((build.WindowsInstallerError, ReleaseArtifactError)):
        build.generate_includes(tmp_path, tmp_path / "payload", configuration, [{"path": path}])
    assert not (tmp_path / "files.iss.inc").exists()


def test_complete_runtime_requires_exact_inventoried_recipient_terms(tmp_path: Path) -> None:
    configuration = _product(tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_MICROSOFT_TERMS_REQUIRED"):
        build.generate_includes(tmp_path, payload, configuration, [], complete_runtime=True)
    terms = payload / build.MICROSOFT_TERMS
    terms.parent.mkdir()
    terms.write_bytes(b"Synthetic Microsoft-only terms; no licence or approval.\n")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_MICROSOFT_TERMS_REQUIRED"):
        build.generate_includes(tmp_path, payload, configuration, [], complete_runtime=True)
    records = inventory_files(payload, {build.MICROSOFT_TERMS: "synthetic-microsoft"})
    terms.write_bytes(b"changed unreviewed terms")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_MICROSOFT_TERMS_REQUIRED"):
        build.generate_includes(tmp_path, payload, configuration, records, complete_runtime=True)
    assert not (tmp_path / "files.iss.inc").exists()


def test_complete_runtime_uses_scoped_terms_and_binds_quiet_acceptance_hash(tmp_path: Path) -> None:
    configuration = _product(tmp_path)
    payload = tmp_path / "payload"
    terms = payload / build.MICROSOFT_TERMS
    terms.parent.mkdir(parents=True)
    terms.write_bytes(b"Synthetic Microsoft-only terms; no licence or approval.\n")
    records = inventory_files(payload, {build.MICROSOFT_TERMS: "synthetic-microsoft"})
    build.generate_includes(tmp_path, payload, configuration, records, complete_runtime=True)
    config = (tmp_path / "config.iss.inc").read_text(encoding="utf-8")
    assert f'#define PWSLicenseFile "{terms}"' in config
    assert (
        f'#define PWSRecipientTermsSHA256 "{hashlib.sha256(terms.read_bytes()).hexdigest()}"'
        in config
    )
    assert "#define PWSCompleteRuntime 1" in config


@pytest.mark.parametrize("directory,digest", [(None, "a" * 64), (Path("overlay"), None)])
def test_microsoft_overlay_requires_both_coordinator_inputs(
    tmp_path: Path, directory: Path | None, digest: str | None
) -> None:
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_MICROSOFT_INPUT_REQUIRED"):
        build.microsoft_runtime_input(directory, digest, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_legacy_build_never_infers_runtime_authority(tmp_path: Path) -> None:
    assert build.microsoft_runtime_input(None, None, root=tmp_path) is None


def test_product_named_gui_is_exact_upstream_cpython_bytes_with_preserved_ownership(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "base"
    source = stage / "runtime/pythonw.exe"
    source.parent.mkdir(parents=True)
    original = b"synthetic interpreter; no executable code"
    source.write_bytes(original)
    records = inventory_files(stage, {"runtime/pythonw.exe": "cpython"})
    owners: dict[str, str] = {}
    payload = tmp_path / "payload"
    build.stage_gui_interpreter(stage, payload, owners, records)
    assert (payload / build.GUI_INTERPRETER).read_bytes() == original
    assert source.read_bytes() == original
    assert owners[build.GUI_INTERPRETER] == "cpython"
    notice = "notices/PaperWorkShell-CPython-GUI-ALIAS.txt"
    assert owners[notice] == "paperworkshell"
    assert "PSF" in (payload / notice).read_text()
    assert str(records[0]["sha256"]) in (payload / notice).read_text()
    from paperworkshell.app.installed_runtime import INSTALLED_GUI_EXECUTABLE_NAME

    assert build.GUI_INTERPRETER == "runtime/" + INSTALLED_GUI_EXECUTABLE_NAME


def test_gui_interpreter_refuses_missing_wrong_owner_and_changed_upstream(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "base"
    source = stage / "runtime/pythonw.exe"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic original")
    records = inventory_files(stage, {"runtime/pythonw.exe": "cpython"})
    payload = tmp_path / "payload"
    for invalid in ([], [{**records[0], "component_id": "paperworkshell"}]):
        with pytest.raises(build.WindowsInstallerError, match="INSTALLER_GUI_INTERPRETER_SOURCE"):
            build.stage_gui_interpreter(stage, payload, {}, invalid)
    source.write_bytes(b"changed source")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_COPY_SOURCE_CHANGED"):
        build.stage_gui_interpreter(stage, payload, {}, records)
    assert not (payload / build.GUI_INTERPRETER).exists()


def test_unverified_microsoft_overlay_fails_without_build_output(tmp_path: Path) -> None:
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_MICROSOFT_INPUT_INVALID"):
        build.microsoft_runtime_input(tmp_path / "unapproved", "a" * 64, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_microsoft_runtime_replaces_only_new_candidate_routes_and_preserves_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import prepare_windows_microsoft_runtime as microsoft

    payload = tmp_path / "payload"
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    material = b"synthetic object; not a Microsoft binary or entitlement"
    digest = hashlib.sha256(material).hexdigest()
    paths = ["runtime/" + member.name for member in microsoft.MEMBERS]
    paths += [microsoft.PIKEPDF_ALIAS]
    paths += ["runtime/engines/qpdf/bin/" + name for name in microsoft.QPDF_APP_LOCAL_NAMES]
    members = []
    for name in paths:
        target = overlay / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(material)
        members.append({"path": name, "size_bytes": len(material), "sha256": digest})
    (overlay / "overlay-receipt.json").write_bytes(b"{}")
    notices = {"synthetic-terms.txt": b"Synthetic component-only terms; no licence grant.\n"}
    monkeypatch.setattr(
        build,
        "MICROSOFT_ASSETS",
        {name: hashlib.sha256(raw).hexdigest() for name, raw in notices.items()},
    )
    for name, raw in notices.items():
        target = tmp_path / build.MICROSOFT_ASSET_DIRECTORY / name
        target.parent.mkdir(parents=True)
        target.write_bytes(raw)
    report: dict[str, Any] = {
        "members": members,
        "approved_receipt_inputs": {},
        "preparation_inputs": {},
    }
    for name in ("owner_acceptance", "community_copy_receipt", "recipient_terms_review"):
        target = tmp_path / (name + ".json")
        target.write_bytes(b"{}")
        report["preparation_inputs"][name] = target.name
        report["approved_receipt_inputs"][name + "_sha256"] = hashlib.sha256(b"{}").hexdigest()
    old_notice = "notices/native/microsoft-msvc-runtime/historical.txt"
    retained = payload / old_notice
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"Original historical third-party notice, unchanged.\r\n")
    original = retained.read_bytes()
    other = {"component_id": "other-open-source", "dependencies": [], "license_expression": "MIT"}
    dependent = {
        "component_id": "pikepdf",
        "dependencies": ["pikepdf-msvc-runtime", "microsoft-vc14-x64-external"],
    }
    inventory: dict[str, Any] = {
        "components": [
            other,
            dependent,
            {"component_id": "microsoft-vc14-x64-external"},
            {"component_id": "pikepdf-msvc-runtime"},
        ],
        "obligations": [
            {"component_id": name}
            for name in (
                "other-open-source",
                "pikepdf",
                "microsoft-vc14-x64-external",
                "pikepdf-msvc-runtime",
            )
        ],
        "license_refs": [{"id": "historical-ref", "text": "Unchanged historical notice"}],
    }
    owners = {old_notice: "pikepdf-msvc-runtime"}
    build.integrate_microsoft_runtime(payload, owners, inventory, overlay, report, root=tmp_path)
    assert retained.read_bytes() == original
    assert inventory["components"][0] == other
    assert dependent["dependencies"] == [build.MICROSOFT_COMPONENT]
    assert inventory["license_refs"][0] == {
        "id": "historical-ref",
        "text": "Unchanged historical notice",
    }
    assert all((payload / name).read_bytes() == material for name in paths)
    assert all(owners[name] == build.MICROSOFT_COMPONENT for name in [old_notice, *paths])
    assert not {"microsoft-vc14-x64-external", "pikepdf-msvc-runtime"} & {
        c["component_id"] for c in inventory["components"]
    }


def test_compiler_missing_extra_changed_files_fail_closed(tmp_path: Path) -> None:
    compiler = tmp_path / "compiler"
    compiler.mkdir()
    exe = compiler / "ISCC.exe"
    exe.write_bytes(b"synthetic compiler bytes, never executed")
    records = inventory_files(compiler, {"ISCC.exe": "inno-setup"})
    review = {
        "version": "7.1.1",
        "source_commit": "2527da77ff7983514f535d19391a748c5d898336",
        "compiler_files": [{k: r[k] for k in ("path", "size_bytes", "sha256")} for r in records],
    }
    target = tmp_path / build.COMPILER_REVIEW
    target.parent.mkdir(parents=True)
    target.write_bytes(json.dumps(review).encode())
    assert build.verify_compiler(compiler, root=tmp_path) == review
    exe.write_bytes(b"changed")
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_COMPILER_CHANGED"):
        build.verify_compiler(compiler, root=tmp_path)
    exe.write_bytes(b"synthetic compiler bytes, never executed")
    exe.with_name("extra.dll").write_bytes(b"extra")
    with pytest.raises(ReleaseArtifactError):
        build.verify_compiler(compiler, root=tmp_path)


def test_generated_include_mutation_fails_before_compiler_execution(tmp_path: Path) -> None:
    candidate = tmp_path / "output/pws0802/candidate"
    candidate.mkdir(parents=True)
    entries = []
    for name in (build.COMPILER_REVIEW, build.SCRIPT, build.PRODUCT):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")
        entries.append(hashlib.sha256(b"{}").hexdigest())
    inventory = candidate / "resolved-inventory.json"
    inventory.write_bytes(b'{"files": []}')
    includes = []
    for name in ("config.iss.inc", "files.iss.inc", "checks.iss.inc"):
        (candidate / name).write_bytes(b"original")
        includes.append({"path": name, "sha256": hashlib.sha256(b"original").hexdigest()})
    plan = {
        "schema_version": 1,
        "status": "unsigned-internal-stage",
        "public_distribution_allowed": False,
        "compiler_review_sha256": entries[0],
        "script_sha256": entries[1],
        "product_sha256": entries[2],
        "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
        "generated_inputs": includes,
    }
    (candidate / "build-plan.json").write_bytes(json.dumps(plan).encode())
    for changed in includes:
        original = (candidate / changed["path"]).read_bytes()
        (candidate / changed["path"]).write_bytes(b'#expr Exec("unsafe")')
        with patch.object(build, "_run", side_effect=AssertionError("compiler must not run")):
            with pytest.raises(
                build.WindowsInstallerError, match="INSTALLER_GENERATED_INPUT_CHANGED"
            ):
                build.compile_installer(candidate, tmp_path / "compiler", root=tmp_path)
        (candidate / changed["path"]).write_bytes(original)


def test_relative_compiler_is_anchored_before_changing_to_candidate_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "output/pws0802/candidate"
    candidate.mkdir(parents=True)
    (candidate / "payload").mkdir()
    compiler = tmp_path / "output/pws0802/compiler"
    compiler.mkdir()
    executable = compiler / "ISCC.exe"
    executable.write_bytes(b"synthetic compiler, never executed")
    records = inventory_files(compiler, {"ISCC.exe": "inno-setup"})
    review = {
        "version": "7.1.1",
        "source_commit": "2527da77ff7983514f535d19391a748c5d898336",
        "compiler_files": [{k: r[k] for k in ("path", "size_bytes", "sha256")} for r in records],
    }
    for name, raw in (
        (build.COMPILER_REVIEW, json.dumps(review).encode()),
        (build.SCRIPT, b"synthetic installer recipe"),
        (build.PRODUCT, b"{}"),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    inventory = candidate / "resolved-inventory.json"
    inventory.write_bytes(b'{"files": []}')
    includes = []
    for name in ("config.iss.inc", "files.iss.inc", "checks.iss.inc"):
        (candidate / name).write_bytes(b"synthetic include")
        includes.append({"path": name, "sha256": hashlib.sha256(b"synthetic include").hexdigest()})
    plan = {
        "schema_version": 1,
        "status": "unsigned-internal-stage",
        "public_distribution_allowed": False,
        "compiler_review_sha256": hashlib.sha256(
            (tmp_path / build.COMPILER_REVIEW).read_bytes()
        ).hexdigest(),
        "script_sha256": hashlib.sha256((tmp_path / build.SCRIPT).read_bytes()).hexdigest(),
        "product_sha256": hashlib.sha256((tmp_path / build.PRODUCT).read_bytes()).hexdigest(),
        "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
        "generated_inputs": includes,
    }
    (candidate / "build-plan.json").write_bytes(json.dumps(plan).encode())
    calls = []

    def fake_run(args: list[str], work: Path, label: str, *, seconds: int, root: Path) -> None:
        selected = Path(args[0])
        if not selected.is_absolute():
            selected = work / selected
        assert selected.is_file(), (
            "Compiler argv must remain valid in its candidate working directory"
        )
        assert selected == executable
        assert work == candidate and root == tmp_path and seconds == 600
        calls.append(label)
        (work / "installer").mkdir()
        (work / "installer/synthetic-UNSIGNED.exe").write_bytes(b"synthetic installer output")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "_run", fake_run)
    receipt = build.compile_installer(candidate, compiler.relative_to(tmp_path), root=tmp_path)
    assert calls == ["compile-installer"]
    assert receipt["status"] == "unsigned-internal-built"
    assert receipt["installer_sha256"] == hashlib.sha256(b"synthetic installer output").hexdigest()


def test_build_runner_anchors_relative_program_before_changing_child_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import windows_signing

    executable = tmp_path / "compiler/ISCC.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"synthetic compiler, never executed")
    work = tmp_path / "candidate/inno-temp"
    work.mkdir(parents=True)

    def capture(
        args: list[str],
        cwd: Path,
        timeout: int,
        *,
        successful_descendant_grace_seconds: float,
    ) -> bytes:
        assert args[1:4] == ["-I", "-B", "-c"]
        selected = Path(args[5])
        if not selected.is_absolute():
            selected = cwd / selected
        assert selected.is_file(), (
            "Nested build executable must remain valid in its child directory"
        )
        assert selected == executable
        assert args[6:] == ["--synthetic-argument"]
        assert cwd == work and timeout == 300 and successful_descendant_grace_seconds == 0
        return b"synthetic output\nPWS_BUILD_EXIT_CODE:0\n"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(windows_signing, "run_build_process", capture)
    build._run(["compiler/ISCC.exe", "--synthetic-argument"], work, "compile", root=tmp_path)
    assert (work / "compile.txt").read_bytes() == b"synthetic output"


def test_build_runner_rejects_empty_command_before_process_execution(tmp_path: Path) -> None:
    with patch(
        "tools.windows_signing.run_build_process", side_effect=AssertionError("must not run")
    ):
        with pytest.raises(build.WindowsInstallerError, match="INSTALLER_COMMAND_PROTOCOL"):
            build._run([], tmp_path, "empty", root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_failed_copy_removes_only_its_new_partial_file(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    original.write_bytes(b"synthetic immutable sentinel")
    target = tmp_path / "partial"
    with pytest.raises(build.WindowsInstallerError, match="INSTALLER_COPY_LIMIT"):
        build._copy(original, target, maximum=5)
    assert not target.exists()
    assert original.read_bytes() == b"synthetic immutable sentinel"
    previous = copy.copy(original.read_bytes())
    with pytest.raises(FileExistsError):
        build._copy(original, original)
    assert original.read_bytes() == previous


def test_installed_notices_shortcut_uses_bundled_viewer_without_optional_windows_app() -> None:
    script = (build.PROJECT_ROOT / build.SCRIPT).read_text(encoding="utf-8")
    row = next(
        line
        for line in script.splitlines()
        if 'Name: "{autoprograms}\\PaperWorkShell notices"' in line
    )
    assert 'Filename: "{app}\\runtime\\PaperWorkShell.exe"' in row
    assert 'Parameters: "--notices"' in row
    assert 'WorkingDir: "{app}\\runtime"' in row
    assert "notepad" not in row.casefold() and "{sys}" not in row
    assert "PaperWorkShellDesktop.exe" not in row
