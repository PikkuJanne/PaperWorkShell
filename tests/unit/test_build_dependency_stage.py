"""Synthetic inputs for the isolated, offline release assembler."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tools import build_dependency_stage as builder
from tools.audit_tesseract_redistribution import ArchiveSpec, SelectedFile
from tools.release_artifacts import ReleaseArtifactError
from tools.release_evidence import validate_data, validate_stage


def identity(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "name": path.name,
        "url": "https://example.invalid/" + path.name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "local_path": path.relative_to(root).as_posix(),
    }


@pytest.fixture
def assembly(tmp_path: Path) -> builder.Assembly:
    stage, artifacts = tmp_path / "output/stage", tmp_path / "output/inputs"
    stage.mkdir(parents=True)
    artifacts.mkdir()
    return builder.Assembly(tmp_path, stage, artifacts, [], fetch=False)


def test_missing_input_never_fetches(
    assembly: builder.Assembly, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Offline assembly attempted network retrieval")

    monkeypatch.setattr(builder, "acquire", forbidden)
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_INPUT_MISSING$"):
        assembly.input(
            {
                "name": "missing.zip",
                "url": "https://example.invalid/missing.zip",
                "size_bytes": 1,
                "sha256": "a" * 64,
            },
            "runtime",
        )
    assert not list(assembly.artifacts.iterdir())


def test_authenticated_input_copy_and_collision(assembly: builder.Assembly) -> None:
    path = assembly.root / "output/source.zip"
    path.write_bytes(b"synthetic trusted build input")
    item = identity(path, assembly.root)
    target = assembly.input(item, "build")
    assert target.read_bytes() == path.read_bytes()
    changed = dict(item, sha256="a" * 64)
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_INPUT_COLLISION$"):
        assembly.input(changed, "runtime")
    assert path.read_bytes() == b"synthetic trusted build input"


def test_bad_input_hash_is_rejected_before_copy(assembly: builder.Assembly) -> None:
    source = assembly.root / "output/input.zip"
    source.write_bytes(b"synthetic")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_ARTIFACT_HASH$"):
        assembly.input(dict(identity(source, assembly.root), sha256="f" * 64), "runtime")
    assert not list(assembly.artifacts.iterdir())
    assert source.read_bytes() == b"synthetic"


def test_legal_asset_keeps_raw_encoding_and_digest(assembly: builder.Assembly) -> None:
    source = assembly.root / "packaging/legal.txt"
    source.parent.mkdir()
    raw = "Synthetic legal text: café\r\n".encode("latin-1")
    source.write_bytes(raw)
    text = assembly.text(
        {
            "asset_path": "packaging/legal.txt",
            "path": "notices/test/legal.txt",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "encoding": "latin-1",
        },
        "synthetic",
    )
    assert text["text"].encode(text["encoding"]) == raw
    assert (assembly.stage / text["path"]).read_bytes() == raw
    assert text["sha256"] == hashlib.sha256(raw).hexdigest()


def test_duplicate_component_and_notice_cannot_silently_change(assembly: builder.Assembly) -> None:
    item = {"component_id": "synthetic", "name": "Original"}
    assembly.add_components([item, item])
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_COMPONENT_CONFLICT$"):
        assembly.add_components([dict(item, name="Changed")])
    assembly.bytes("notices/test.txt", b"first", "synthetic")
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_COLLISION$"):
        assembly.bytes("notices/test.txt", b"changed", "synthetic")


def test_wheel_subset_omits_conventional_crt_and_stale_record(assembly: builder.Assembly) -> None:
    wheel = assembly.root / "output/synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("synthetic/__init__.py", "# Synthetic fixture\n")
        archive.writestr("synthetic/vcruntime140.dll", "Synthetic omitted placeholder")
        archive.writestr("synthetic-1.dist-info/RECORD", "synthetic stale record")
        archive.writestr("synthetic-1.dist-info/sboms/upstream.json", "{}")
    builder.stage_wheel_input(assembly, identity(wheel, assembly.root), "synthetic", {})
    assert set(assembly.owners) == {"runtime/Lib/site-packages/synthetic/__init__.py"}
    assert len(assembly.omissions["synthetic"]) == 3


def test_source_copy_bounds_cleanup_new_target(
    assembly: builder.Assembly, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = assembly.root / "output/source.bin"
    source.write_bytes(b"synthetic input longer than bound")
    monkeypatch.setattr(builder, "MAX_ARTIFACT_BYTES", 4)
    target = assembly.artifacts / "copy.bin"
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_LIMIT$"):
        assembly._copy(source, target)
    assert not target.exists()
    assert source.read_bytes() == b"synthetic input longer than bound"


def test_tar_staging_retains_all_selected_files_not_only_pe_proof_objects(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.pkg.tar.zst"
    package = "mingw-w64-ucrt-x86_64-synthetic"
    first, second = b"Synthetic library payload", b"Synthetic language data payload"
    with tarfile.open(path, "w:zst") as archive:
        for name, data in {
            ".PKGINFO": f"pkgname = {package}\npkgver = 1-1\nlicense = spdx:MIT\n".encode(),
            "ucrt64/bin/synthetic.dll": first,
            "ucrt64/share/tessdata/synthetic.traineddata": second,
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    spec = ArchiveSpec(
        package,
        "1-1",
        path.name,
        path.stat().st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        ("spdx:MIT",),
        tuple(
            SelectedFile(name, package, len(data), hashlib.sha256(data).hexdigest())
            for name, data in [
                ("bin/synthetic.dll", first),
                ("share/tessdata/synthetic.traineddata", second),
            ]
        ),
    )
    assert builder.selected_tar_bytes(path, spec) == {
        "bin/synthetic.dll": first,
        "share/tessdata/synthetic.traineddata": second,
    }


def synthetic_reviews(root: Path) -> tuple[str, str]:
    cache = root / "output/cache"
    cache.mkdir(parents=True)
    reviews = root / builder.REVIEW_DIRECTORY
    reviews.mkdir(parents=True)
    legal = root / "packaging/synthetic-legal.txt"
    legal.write_text("Synthetic legal fixture, no third-party text.\n", encoding="utf8")
    notice = {
        "path": "notices/synthetic/legal.txt",
        "asset_path": legal.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(legal.read_bytes()).hexdigest(),
    }
    wheel = cache / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("paperworkshell/__init__.py", "# Synthetic application\n")
    embed = cache / "python.zip"
    with zipfile.ZipFile(embed, "w") as archive:
        archive.writestr("LICENSE.txt", "Synthetic interpreter notice")
        archive.writestr("python314._pth", "# Must be replaced explicitly\n")
        archive.writestr("vcruntime140.dll", "Synthetic omitted CRT placeholder")
    source = cache / "synthetic-source.tar.gz"
    source.write_bytes(b"Synthetic source archive stand-in; never parsed or executed.")
    source_input = dict(identity(source, root), stage_path="sources/synthetic/source.tar.gz")
    derived = cache / "synthetic-approved-source.tar.xz"
    derived.write_bytes(b"Synthetic derived source fixture")
    derived_input = dict(
        identity(derived, root), url=None, stage_path="sources/synthetic/approved.tar.xz"
    )
    qpdf = cache / "synthetic-qpdf.zip"
    with zipfile.ZipFile(qpdf, "w") as archive:
        archive.writestr("qpdf.exe", "Synthetic executable-name placeholder; never executed")
    wheel_input = identity(wheel, root)

    def component(cid: str) -> dict[str, Any]:
        return {
            "component_id": cid,
            "name": cid,
            "version": "1",
            "type": "library",
            "scope": "runtime",
            "supplier": "Synthetic tests",
            "purl": None,
            "license_expression": "MIT",
            "license_texts": [notice],
            "source_url": "https://example.invalid/source",
            "artifact_sha256": wheel_input["sha256"],
            "dependencies": [],
        }

    payloads: dict[str, dict[str, Any]] = {
        name: {"schema_version": 1} for name in builder.REVIEW_NAMES
    }
    payloads["python-inputs-v1.json"].update(
        components=[], lock_sha256=hashlib.sha256(b"synthetic lock").hexdigest()
    )
    payloads["python-components-v1.json"].update(
        components=[component("lxml"), component("pikepdf")], license_refs=[], artifacts_add=[]
    )
    payloads["native-components-v1.json"].update(
        components=[],
        license_refs=[],
        notice_assets=[],
        msvc_license_review={"license_assets": [notice]},
    )
    payloads["python-native-components-v1.json"].update(
        components=[],
        license_refs=[],
        parent_overrides=[{"name": "cpython", "license_texts": [notice]}],
    )
    payloads["qt-pdfium-components-v1.json"].update(
        components=[],
        license_refs=[],
        artifacts=[],
        assets=[],
        component_overrides=[],
        source_stage_paths=[],
        external_prerequisites=[],
    )
    for name in ("python-rust-components-v1.json", "build-tool-components-v1.json"):
        payloads[name].update(components=[], license_refs=[], parent_evidence={}, artifacts=[])
    payloads["cpython-inputs-v1.json"]["artifacts"] = [identity(embed, root)]
    payloads["lxml-source-subset-v1.json"]["output_artifact"] = derived_input
    payloads["lxml-exclusions-v1.json"]["exclusions"] = []
    for name, payload in payloads.items():
        (reviews / name).write_text(json.dumps(payload), encoding="utf8")
    (root / "packaging/tesseract-runtime-closure-v2.json").write_text(
        '{"schema_version":2}', encoding="utf8"
    )
    (root / "packaging/libtiff-no-jbig-build-v1.json").write_text(
        json.dumps({"schema_version": 1, "build_inputs": [], "source": source_input}),
        encoding="utf8",
    )
    (reviews / "libtiff-build-support-v1.json").write_text('{"schema_version":1}', encoding="utf8")
    (reviews / "native-build-tool-components-v1.json").write_text(
        '{"schema_version":1}', encoding="utf8"
    )
    qcomp = component("qpdf-cli")
    qcomp["artifact_sha256"] = identity(qpdf, root)["sha256"]
    raw_qpdf = b"Synthetic executable-name placeholder; never executed"
    (reviews / "qpdf-test.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": [qcomp],
                "artifacts": [identity(qpdf, root)],
                "stage_files": [
                    {
                        "artifact": qpdf.name,
                        "member": "qpdf.exe",
                        "path": "runtime/engines/qpdf/bin/qpdf.exe",
                        "component_id": "qpdf-cli",
                        "size_bytes": len(raw_qpdf),
                        "sha256": hashlib.sha256(raw_qpdf).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf8",
    )
    root_component = component("paperworkshell")
    root_component.update(
        type="application",
        version="0.0.0",
        relationship="root",
        license_expression="NOASSERTION",
        license_texts=[],
    )
    local_wheel = dict(wheel_input, url=None, recipe_path="pyproject.toml")
    local_source = dict(source_input, url=None, recipe_path="pyproject.toml")
    candidate = {
        "id": "synthetic-stage",
        "version": "0.0.0",
        "created_utc": "2026-11-03T00:00:00Z",
        "source_sha256": local_source["sha256"],
        "source_revision": "source-snapshot:" + local_source["sha256"],
    }
    (reviews / "app-test.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": [local_wheel, local_source],
                "components": [root_component],
                "candidate": candidate,
            }
        ),
        encoding="utf8",
    )
    tools = root / "tools"
    tools.mkdir()
    for name in (
        "build_dependency_stage.py",
        "release_artifacts.py",
        "release_staging.py",
        "release_evidence.py",
        "release_sbom.py",
        "audit_tesseract_redistribution.py",
        "build_libtiff_no_jbig.py",
        "stage_lxml_source.py",
    ):
        (tools / name).write_text("# Synthetic hash-bound recipe\n", encoding="utf8")
    (root / "pyproject.toml").write_text("# Synthetic build recipe\n", encoding="utf8")
    (root / "uv.lock").write_bytes(b"synthetic lock")
    return builder.REVIEW_DIRECTORY + "app-test.json", builder.REVIEW_DIRECTORY + "qpdf-test.json"


def test_synthetic_candidate_assembles_and_preserves_prior_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, qpdf = synthetic_reviews(tmp_path)
    # A new build review intentionally describes a crate source archive, even
    # when its parent also occurs in the older runtime-native Rust review.
    directory = tmp_path / builder.REVIEW_DIRECTORY
    input_review = json.loads((directory / "python-inputs-v1.json").read_text(encoding="utf8"))
    for cid, scope in [("pi-heif", "runtime"), ("uv-build", "build")]:
        wheel = tmp_path / "output/cache" / ("synthetic-" + cid + ".whl")
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("unused.py", "# Synthetic unshipped input\n")
        input_review["components"].append(
            {"component_id": cid, "scope": scope, "wheel": identity(wheel, tmp_path)}
        )
    (directory / "python-inputs-v1.json").write_text(json.dumps(input_review), encoding="utf8")
    source = identity(tmp_path / "output/cache/synthetic-source.tar.gz", tmp_path)
    python_review = json.loads((directory / "python-components-v1.json").read_text(encoding="utf8"))
    crate = dict(
        python_review["components"][0],
        component_id="validation-source-crate",
        scope="build",
        relationship="external",
        parent_component_id="pikepdf",
        artifact_sha256=source["sha256"],
        license_texts=[],
    )
    (directory / "validation-build-components-v1.json").write_text(
        json.dumps({"schema_version": 1, "components": [crate], "artifacts": [source]}),
        encoding="utf8",
    )
    legacy = json.loads((directory / "python-rust-components-v1.json").read_text(encoding="utf8"))
    legacy["parent_evidence"]["pikepdf"] = {
        "wheel_sha256": python_review["components"][0]["artifact_sha256"],
        "root_dependencies": ["validation-source-crate"],
    }
    (directory / "python-rust-components-v1.json").write_text(json.dumps(legacy), encoding="utf8")
    monkeypatch.setattr(builder, "stage_tesseract", lambda *_args: None)
    monkeypatch.setattr(builder, "validate_data", lambda value, **_kwargs: validate_data(value))
    stage, inventory_path = tmp_path / "output/final-stage", tmp_path / "output/resolved.json"
    kwargs: dict[str, Any] = {
        "artifacts": tmp_path / "output/inputs",
        "destination": stage,
        "inventory_output": inventory_path,
        "cache_directories": [tmp_path / "output/cache"],
        "app_review": app,
        "qpdf_review": qpdf,
        "gpg_executable": tmp_path / "unused-gpg.exe",
        "candidate_id": "synthetic-stage",
        "created_utc": "2026-11-03T00:00:00Z",
        "root": tmp_path,
    }
    report = builder.assemble(**kwargs)
    assert report["status"] == "assembled"
    inventory = json.loads(inventory_path.read_text())
    scopes = {item["name"]: item["scope"] for item in inventory["input_artifacts"]}
    assert scopes["synthetic-pi-heif.whl"] == "review"
    assert scopes["synthetic-uv-build.whl"] == "build"
    assert not any(f["path"].endswith("unused.py") for f in inventory["files"])
    assert (
        next(c for c in inventory["components"] if c["component_id"] == "validation-source-crate")[
            "artifact_sha256"
        ]
        == source["sha256"]
    )
    assert (stage / "runtime/python314._pth").read_bytes() == builder.ISOLATED_PATH
    assert not (stage / "runtime/vcruntime140.dll").exists()
    assert inventory_path.with_name(inventory_path.name + ".omissions.json").is_file()
    assert not any("resolved" in f["path"] for f in inventory["files"])
    # Verify the actual produced inventory and copies with the repository validator.
    monkeypatch.setattr(
        "tools.release_evidence.validate_data", lambda value, **_kwargs: validate_data(value)
    )
    validate_stage(inventory, stage, kwargs["artifacts"], root=tmp_path)
    before = inventory_path.read_bytes()
    with pytest.raises(ReleaseArtifactError, match="^RELEASE_OUTPUT_EXISTS$"):
        builder.assemble(**kwargs)
    assert inventory_path.read_bytes() == before
