from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from tools.release_artifacts import Artifact, ReleaseArtifactError
from tools.release_staging import inventory_files, stage_wheel, verify_files, write_package


def _wheel(tmp_path: Path, members: dict[str, bytes]) -> tuple[Path, Artifact]:
    path = tmp_path / "synthetic-1-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    data = path.read_bytes()
    return path, Artifact(
        path.name,
        "https://example.invalid/synthetic.whl",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def test_wheel_platlib_native_dependency_is_relocated_instead_of_omitted(tmp_path: Path) -> None:
    path, artifact = _wheel(
        tmp_path,
        {
            "synthetic/__init__.py": b"",
            "synthetic-1.data/platlib/dependency.dll": b"synthetic binary data only",
            "synthetic-1.data/purelib/shared.py": b"value = 1\n",
            "synthetic-1.data/scripts/tool.exe": b"unneeded entry point",
            "synthetic-1.dist-info/RECORD": b"original wheel record",
            "synthetic-1.dist-info/sboms/vendor.json": b"upstream build machine metadata",
        },
    )
    stage = tmp_path / "stage"
    paths, omitted = stage_wheel(path, artifact, stage, "synthetic")
    assert "runtime/Lib/site-packages/dependency.dll" in paths
    assert "runtime/Lib/site-packages/shared.py" in paths
    assert (
        stage / "runtime/Lib/site-packages/dependency.dll"
    ).read_bytes() == b"synthetic binary data only"
    assert omitted == [
        "synthetic-1.data/scripts/tool.exe",
        "synthetic-1.dist-info/RECORD",
        "synthetic-1.dist-info/sboms/vendor.json",
    ]


def test_exclusion_is_bound_to_exact_member_digest(tmp_path: Path) -> None:
    path, artifact = _wheel(tmp_path, {"synthetic/a.xsl": b"synthetic unlicensed stand-in"})
    with pytest.raises(ReleaseArtifactError, match="RELEASE_EXCLUSION"):
        stage_wheel(
            path,
            artifact,
            tmp_path / "stage",
            "synthetic",
            exclusions={"synthetic/a.xsl": "0" * 64},
        )
    assert not (tmp_path / "stage").exists()


def test_stage_inventory_and_reopen_verification_are_exhaustive(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "synthetic.txt").write_bytes(b"only synthetic content")
    records = inventory_files(stage, {"synthetic.txt": "synthetic"})
    first = write_package(stage, records, tmp_path / "first.zip")
    second = write_package(stage, records, tmp_path / "second.zip")
    assert first["sha256"] == second["sha256"]
    (stage / "unexpected.txt").write_bytes(b"extra synthetic content")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_UNOWNED_FILE"):
        verify_files(stage, records)


def test_missing_changed_and_case_colliding_files_do_not_pass(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    item = stage / "synthetic.txt"
    item.write_bytes(b"original synthetic")
    records = inventory_files(stage, {"synthetic.txt": "synthetic"})
    item.write_bytes(b"changed synthetic")
    with pytest.raises(ReleaseArtifactError, match="RELEASE_STAGED_HASH"):
        verify_files(stage, records)
    item.unlink()
    with pytest.raises(ReleaseArtifactError, match="RELEASE_MISSING_FILE"):
        verify_files(stage, records)
    with pytest.raises(ReleaseArtifactError, match="RELEASE_COLLISION"):
        verify_files(stage, [*records, *records])


def test_existing_package_is_preserved_after_stage_failure(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    item = stage / "synthetic.txt"
    item.write_bytes(b"original synthetic")
    records = inventory_files(stage, {"synthetic.txt": "synthetic"})
    output = tmp_path / "previous.zip"
    output.write_bytes(b"previous good export stand-in")
    with pytest.raises(FileExistsError):
        write_package(stage, records, output)
    assert output.read_bytes() == b"previous good export stand-in"


def test_mixed_case_stage_paths_use_one_cross_platform_order(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    owners = {
        "runtime/Lib/synthetic.py": "synthetic",
        "runtime/engines/synthetic.exe": "synthetic",
    }
    for name in owners:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic data only")
    records = inventory_files(stage, owners)
    assert [record["path"] for record in records] == sorted(owners)
    verify_files(stage, records)
