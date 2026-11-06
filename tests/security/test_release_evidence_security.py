from __future__ import annotations

import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_release_evidence import add_stage_file, make_candidate, rename_dependency
from tools import release_evidence as evidence
from tools.release_sbom import ReleaseSbomError


def test_validation_and_freeze_have_no_network_or_process_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("offline evidence validation attempted network or execution")

    monkeypatch.setattr(socket, "socket", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    assert evidence.validate_stage(inventory, stage, artifacts)["status"] == "pass"
    evidence.freeze_evidence(inventory, stage, artifacts, tmp_path / "frozen")


@pytest.mark.parametrize(
    "path",
    [
        "runtime/Lib/site-packages/lxml/rng2schtrn.xsl",
        "runtime/Lib/site-packages/lxml/xsd2schtrn.xsl",
        "runtime/bin/libjbig-0.dll",
        "runtime/Lib/site-packages/pi_heif/__init__.py",
        "runtime/Lib/site-packages/_pi_heif.pyd",
        "runtime/Lib/site-packages/pi_heif.libs/libheif-1.dll",
        "runtime/Lib/site-packages/PySide6/QtPdf.pyd",
        "runtime/Lib/site-packages/PySide6/QtNetwork.pyd",
        "runtime/Lib/site-packages/PySide6/Qt6Network.dll",
        "runtime/Lib/site-packages/PySide6/QtQml.pyd",
        "runtime/Lib/site-packages/PySide6/Qt6Qml.dll",
        "runtime/Lib/site-packages/PySide6/QtQuick.pyd",
        "runtime/Lib/site-packages/PySide6/Qt6Quick.dll",
        "runtime/Lib/site-packages/PySide6/Qt6Designer.dll",
    ],
)
def test_excluded_runtime_files_cannot_reenter_with_permissive_label(
    tmp_path: Path, path: str
) -> None:
    inventory, stage, _ = make_candidate(tmp_path)
    add_stage_file(inventory, stage, path)
    with pytest.raises(evidence.ReleaseEvidenceError, match="RELEASE_EXCLUDED_FILE"):
        evidence.validate_data(inventory)


@pytest.mark.parametrize(
    "component_id", ["ghostscript", "mupdf", "poppler", "pyside6-addons", "qt-pdf", "pi-heif"]
)
@pytest.mark.parametrize("scope", ["runtime", "build"])
def test_unadmitted_component_family_cannot_be_relabelled_or_hidden_as_build(
    tmp_path: Path, component_id: str, scope: str
) -> None:
    inventory, _, _ = make_candidate(tmp_path)
    rename_dependency(inventory, component_id)
    inventory["components"][1]["scope"] = scope
    with pytest.raises(evidence.ReleaseEvidenceError):
        evidence.validate_data(inventory)


@pytest.mark.parametrize(
    "digest",
    [
        "6d1657e08318630738fb0841ee28bbe7272b41b3b2ef93adaa228c4cd10092c6",
        "832f84eb1476a77e522bdc1715386b68f24423d35da084cd1dde319aacbba316",
        "430791ac820cfb315c820f7c19703609a59f21b8151345ca11e6127efbfaec0d",
        "39b79bb01f16b7e777b8803f539354f394e99d0dcfc4f5f7f1374e02aa2c31a7",
        "ba96ae44888e0185281e937633a743ea90d5a196c6000f82565ebb0580012d40",
        "b2d444f2e66624d68e9c6b211e28a76e22fff5fcabcfff4deac18b529b7d4137",
        "ce0eb686a9b03a8586d7d1f833fadcbe67ad2f6ff8538c3f431d4edb072f935d",
        "334e156affd0052de8ae549ec7044d3b2cdb1f0c72569659619fb8e820e5d878",
    ],
)
def test_known_excluded_file_identity_cannot_be_hidden_by_renaming(
    tmp_path: Path, digest: str
) -> None:
    inventory, stage, _ = make_candidate(tmp_path)
    add_stage_file(inventory, stage, "runtime/renamed-synthetic.bin")
    # This is a negative metadata fixture only. No excluded third-party content
    # is copied into the test: the known public identity itself must fail policy.
    record = next(
        item for item in inventory["files"] if item["path"] == "runtime/renamed-synthetic.bin"
    )
    record["sha256"] = digest
    with pytest.raises(evidence.ReleaseEvidenceError):
        evidence.validate_data(inventory)


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/private/PRIVATE_APPLICANT_DOCUMENT.pdf",
        "https://private-user:PRIVATE_PASSWORD@example.invalid/input.zip",
        "http://example.invalid/input.zip",
        "https://example.invalid/input.zip#PRIVATE_FRAGMENT",
    ],
)
def test_verification_receipt_never_admits_unsafe_or_secret_bearing_input_urls(
    tmp_path: Path, url: str
) -> None:
    inventory, stage, artifacts = make_candidate(tmp_path)
    inventory["input_artifacts"][0]["url"] = url
    with pytest.raises((evidence.ReleaseEvidenceError, ValueError)):
        evidence.validate_stage(inventory, stage, artifacts)


def test_unknown_third_party_runtime_rights_do_not_inherit_product_deferral(tmp_path: Path) -> None:
    inventory, _, _ = make_candidate(tmp_path)
    # The first-party root is deliberately NOASSERTION until PWS-0802 chooses
    # product terms. That deferral grants no third-party distribution rights.
    inventory["components"][1]["license_expression"] = "NOASSERTION"
    with pytest.raises((evidence.ReleaseEvidenceError, ReleaseSbomError)):
        evidence.validate_data(inventory)


def test_error_output_does_not_expose_private_paths_or_library_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "PRIVATE_APPLICANT_NAME_AND_DOCUMENT_TOKEN"
    inventory = tmp_path / f"{secret}.json"
    inventory.write_bytes(b"{")
    result = evidence.main(
        [
            "validate",
            "--inventory",
            str(inventory),
            "--stage",
            str(tmp_path / secret),
            "--artifacts",
            str(tmp_path / secret),
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert secret not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err
    assert "JSONDecodeError" not in captured.err
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR [RELEASE_INPUT] Dependency staging evidence could not be read safely.\n"
    )


def test_raw_filesystem_exception_is_sanitized_by_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "PRIVATE_SOURCE_PATH_TOKEN"

    def failed(*args: Any, **kwargs: Any) -> None:
        raise OSError(f"C:/private/{token}/document.pdf")

    monkeypatch.setattr(evidence, "load_json", failed)
    assert (
        evidence.main(
            [
                "validate",
                "--inventory",
                "synthetic.json",
                "--stage",
                "synthetic",
                "--artifacts",
                "synthetic",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert "RELEASE_INPUT" in captured.err
