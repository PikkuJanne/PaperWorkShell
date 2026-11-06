from __future__ import annotations

import copy
import errno
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tools import release_evidence as evidence
from tools.release_artifacts import ReleaseArtifactError
from tools.release_staging import inventory_files, write_package


def make_candidate(tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    """Bind the versioned synthetic fixture to local bytes and real approval evidence."""
    inventory = evidence.load_json(evidence.PROJECT_ROOT / evidence.FIXTURES / "current.json")
    stage = tmp_path / "stage"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    with zipfile.ZipFile(artifacts / "synthetic.zip", "w") as archive:
        archive.writestr("synthetic.bin", b"synthetic runtime bytes only\n")
    raw = (artifacts / "synthetic.zip").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    inventory["input_artifacts"][0].update(size_bytes=len(raw), sha256=digest)
    for component in inventory["components"]:
        component["artifact_sha256"] = digest
    notice = inventory["components"][1]["license_texts"][0]
    for relative, payload in (
        (notice["path"], notice["text"].encode("utf-8")),
        ("runtime/synthetic.bin", b"synthetic runtime bytes only\n"),
    ):
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    inventory["files"] = inventory_files(
        stage, {notice["path"]: "synthetic", "runtime/synthetic.bin": "synthetic"}
    )
    for item in inventory["review_inputs"]:
        item["sha256"] = hashlib.sha256(
            (evidence.PROJECT_ROOT / item["path"]).read_bytes()
        ).hexdigest()
    return inventory, stage, artifacts


def add_stage_file(
    inventory: dict[str, Any], stage: Path, relative: str, raw: bytes = b"synthetic bytes"
) -> None:
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    owners = {item["path"]: item["component_id"] for item in inventory["files"]}
    owners[relative] = "synthetic"
    inventory["files"] = inventory_files(stage, owners)


def rename_dependency(inventory: dict[str, Any], component_id: str) -> None:
    inventory["components"][0]["dependencies"] = [component_id]
    inventory["components"][1]["component_id"] = component_id
    for item in inventory["files"]:
        item["component_id"] = component_id
    inventory["obligations"][1]["component_id"] = component_id


def test_current_and_rejected_versioned_fixtures() -> None:
    assert evidence.schema_messages() == []


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "RELEASE_INPUT_LIMIT"),
        (b"[]", "RELEASE_INPUT_OBJECT"),
        (b'{"x":1,"x":2}', "RELEASE_JSON_DUPLICATE_KEY"),
        (b'{"x":NaN}', "RELEASE_JSON_NUMBER"),
        (b'{"x":1.5}', "RELEASE_JSON_NUMBER"),
    ],
)
def test_json_shape_numbers_and_duplicate_keys_are_rejected(
    tmp_path: Path, raw: bytes, code: str
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(evidence.ReleaseEvidenceError, match=f"^{code}$"):
        evidence.load_json(path)


def test_json_input_limit_is_enforced_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b'{"too_long":"synthetic"}')
    with pytest.raises(evidence.ReleaseEvidenceError, match="RELEASE_INPUT_LIMIT"):
        evidence.load_json(path, maximum=8)


def test_stage_checks_exact_inputs_review_and_exhaustive_file_hashes(tmp_path: Path) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    receipt = evidence.validate_stage(inventory, stage, artifacts)
    assert receipt["status"] == "pass"
    assert receipt["verified_staged_files"] == 2
    assert receipt["verified_stage_bytes"] == sum(item["size_bytes"] for item in inventory["files"])
    assert "installer/signing/product-release approval remain separately gated" in str(
        receipt["scope"]
    )


@pytest.mark.parametrize("origin", ["local-build", "derived-source"])
def test_local_artifact_binds_real_recipe_bytes_without_download_claim(
    tmp_path: Path, origin: str
) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    root = tmp_path / "synthetic-project"
    recipe_relative = "packaging/synthetic-recipe.json"
    recipe = root / recipe_relative
    recipe.parent.mkdir(parents=True)
    recipe.write_bytes(b'{"purpose":"synthetic test recipe; never executed"}\n')
    schema = root / evidence.SCHEMA_PATH
    schema.parent.mkdir(parents=True)
    schema.write_bytes((evidence.PROJECT_ROOT / evidence.SCHEMA_PATH).read_bytes())
    for item in inventory["review_inputs"]:
        destination = root / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((evidence.PROJECT_ROOT / item["path"]).read_bytes())
    inventory["input_artifacts"][0].update(url=None, origin=origin, recipe_path=recipe_relative)
    inventory["review_inputs"].append(
        {"path": recipe_relative, "sha256": hashlib.sha256(recipe.read_bytes()).hexdigest()}
    )
    receipt = evidence.validate_stage(inventory, stage, artifacts, root=root)
    verified = receipt["verified_input_artifacts"]
    assert isinstance(verified, list)
    assert verified[0]["url"] is None
    recipe.write_bytes(b"changed synthetic recipe")
    with pytest.raises(evidence.ReleaseEvidenceError, match="RELEASE_REVIEW_CHANGED"):
        evidence.validate_stage(inventory, stage, artifacts, root=root)


@pytest.mark.parametrize(
    "case", ["missing_origin", "downloaded", "missing_recipe", "unreviewed_recipe"]
)
def test_local_artifact_cannot_omit_hashed_recipe_provenance(tmp_path: Path, case: str) -> None:
    inventory, _, _ = make_candidate(tmp_path)
    artifact = inventory["input_artifacts"][0]
    artifact.update(
        url=None, origin="local-build", recipe_path=inventory["review_inputs"][0]["path"]
    )
    if case == "missing_origin":
        del artifact["origin"]
    elif case == "downloaded":
        artifact["origin"] = "downloaded"
    elif case == "missing_recipe":
        del artifact["recipe_path"]
    else:
        artifact["recipe_path"] = "packaging/unreviewed-recipe.json"
    with pytest.raises(evidence.ReleaseEvidenceError, match="RELEASE_INPUT_SOURCE"):
        evidence.validate_data(inventory)


@pytest.mark.parametrize("change", ["input", "review", "extra", "missing", "changed"])
def test_stage_rejects_stale_or_incomplete_evidence(tmp_path: Path, change: str) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    if change == "input":
        (artifacts / "synthetic.zip").write_bytes(b"changed")
    elif change == "review":
        inventory["review_inputs"][0]["sha256"] = "0" * 64
    elif change == "extra":
        (stage / "runtime/extra.bin").write_bytes(b"extra")
    elif change == "missing":
        (stage / "runtime/synthetic.bin").unlink()
    else:
        (stage / "runtime/synthetic.bin").write_bytes(b"changed")
    with pytest.raises((evidence.ReleaseEvidenceError, ReleaseArtifactError)):
        evidence.validate_stage(inventory, stage, artifacts)


@pytest.mark.parametrize(
    "missing",
    [
        "notice",
        "notice_file",
        "source",
        "source_file",
        "replacement",
        "replacement_file",
        "obligation",
        "duplicate_obligation",
        "input",
        "approval",
    ],
)
def test_distribution_obligation_closure_is_required(tmp_path: Path, missing: str) -> None:
    inventory, stage, _ = make_candidate(tmp_path)
    component = inventory["components"][1]
    obligation = inventory["obligations"][1]
    if missing in {"source", "source_file", "replacement", "replacement_file"}:
        component["license_expression"] = "LGPL-3.0-only"
        add_stage_file(
            inventory, stage, "sources/synthetic/source.tar", b"synthetic corresponding source"
        )
        add_stage_file(
            inventory, stage, "notices/synthetic/REPLACE.txt", b"synthetic replacement instructions"
        )
        obligation.update(
            source_paths=["sources/synthetic/source.tar"],
            replacement_instructions_path="notices/synthetic/REPLACE.txt",
        )
    if missing == "notice":
        component["license_texts"] = []
    elif missing == "notice_file":
        inventory["files"] = [
            item for item in inventory["files"] if not item["path"].startswith("notices/")
        ]
    elif missing == "source":
        obligation["source_paths"] = []
    elif missing == "source_file":
        obligation["source_paths"] = ["sources/missing.tar"]
    elif missing == "replacement":
        obligation["replacement_instructions_path"] = None
    elif missing == "replacement_file":
        obligation["replacement_instructions_path"] = "notices/missing.txt"
    elif missing == "obligation":
        inventory["obligations"].pop()
    elif missing == "duplicate_obligation":
        inventory["obligations"].append(copy.deepcopy(obligation))
    elif missing == "input":
        component["artifact_sha256"] = "e" * 64
    else:
        inventory["review_inputs"][0]["path"] = "packaging/missing-review.json"
    with pytest.raises(evidence.ReleaseEvidenceError):
        evidence.validate_data(inventory)


@pytest.mark.parametrize(
    "license_expression",
    ["GPL-2.0-only", "GPL-3.0-or-later", "MIT OR GPL-3.0-only", "AGPL-3.0-or-later"],
)
def test_unapproved_runtime_gpl_routes_fail(tmp_path: Path, license_expression: str) -> None:
    inventory, _, _ = make_candidate(tmp_path)
    inventory["components"][1]["license_expression"] = license_expression
    with pytest.raises(
        evidence.ReleaseEvidenceError, match="RELEASE_DISTRIBUTION_DECISION_REQUIRED"
    ):
        evidence.validate_data(inventory)


def test_gcc_runtime_exception_requires_and_accepts_exact_source_copy(tmp_path: Path) -> None:
    inventory, stage, _ = make_candidate(tmp_path)
    inventory["components"][1]["license_expression"] = "GPL-3.0-or-later WITH GCC-exception-3.1"
    with pytest.raises(evidence.ReleaseEvidenceError, match="RELEASE_SOURCE_MISSING"):
        evidence.validate_data(inventory)
    add_stage_file(inventory, stage, "sources/synthetic/source.tar")
    inventory["obligations"][1]["source_paths"] = ["sources/synthetic/source.tar"]
    evidence.validate_data(inventory)


def test_freeze_sidecars_zip_and_checksum_subjects_are_exact(tmp_path: Path) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    before = {item["path"]: (stage / item["path"]).read_bytes() for item in inventory["files"]}
    output = tmp_path / "frozen"
    receipt = evidence.freeze_evidence(inventory, stage, artifacts, output)
    expected = {
        "resolved-inventory.json",
        "bom.cdx.json",
        "bom.spdx.json",
        "THIRD_PARTY_NOTICES.txt",
        "input-verification.json",
        "dependency-stage.zip",
    }
    assert isinstance(receipt["artifacts"], dict)
    assert set(receipt["artifacts"]) == expected
    checksums = {}
    for line in (output / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ")
        checksums[name] = digest
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert checksums == receipt["artifacts"]
    with zipfile.ZipFile(output / "dependency-stage.zip") as archive:
        assert set(archive.namelist()) == set(before)
        for relative, raw in before.items():
            assert archive.read(relative) == raw == (stage / relative).read_bytes()
    assert json.loads((output / "resolved-inventory.json").read_bytes()) == inventory
    assert not list(tmp_path.glob("pws-dependency-freeze-*"))


def test_existing_frozen_evidence_is_never_replaced(tmp_path: Path) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    output = tmp_path / "prior"
    output.mkdir()
    (output / "receipt.txt").write_bytes(b"prior accepted evidence")
    with pytest.raises(FileExistsError):
        evidence.freeze_evidence(inventory, stage, artifacts, output)
    assert (output / "receipt.txt").read_bytes() == b"prior accepted evidence"
    assert list(output.iterdir()) == [output / "receipt.txt"]


def test_disk_full_during_sidecar_write_cleans_partial_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    original = Path.open

    def failed_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if (
            path.name == "bom.spdx.json"
            and path.parent.name.startswith("pws-dependency-freeze-")
            and mode == "xb"
        ):
            with original(path, mode, *args, **kwargs) as stream:
                stream.write(b"owned partial sidecar")
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failed_open)
    with pytest.raises(OSError) as caught:
        evidence.freeze_evidence(inventory, stage, artifacts, tmp_path / "new")
    assert caught.value.errno == errno.ENOSPC
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob("pws-dependency-freeze-*"))


@pytest.mark.parametrize("failure", ["disk_full", "cancel", "stage_changed"])
def test_failed_freeze_cleans_owned_temp_and_preserves_prior_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_bytes(b"prior accepted evidence")
    original = write_package

    def interrupted(*args: Any, **kwargs: Any) -> dict[str, object]:
        output = args[2]
        output.write_bytes(b"owned partial package")
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure == "disk_full":
            raise OSError(errno.ENOSPC, "synthetic disk full")
        output.unlink()
        result = original(*args, **kwargs)
        (stage / "runtime/synthetic.bin").write_bytes(b"changed after initial verification")
        return result

    monkeypatch.setattr(evidence, "write_package", interrupted)
    expected = (
        KeyboardInterrupt
        if failure == "cancel"
        else (OSError if failure == "disk_full" else ReleaseArtifactError)
    )
    with pytest.raises(expected):
        evidence.freeze_evidence(inventory, stage, artifacts, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob("pws-dependency-freeze-*"))
    assert prior.read_bytes() == b"prior accepted evidence"
