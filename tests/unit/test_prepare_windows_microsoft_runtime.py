"""Pure synthetic gate tests; no Microsoft copy, acquisition or native API call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.microsoft_runtime import change_receipt, fixture
from tools import prepare_windows_microsoft_runtime as tool


def test_missing_rights_refuses_before_any_input_read_or_output_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("must reject before reading source software")

    monkeypatch.setattr(tool, "_read", forbidden)
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_RIGHTS_REQUIRED"):
        tool.prepare(tool.Inputs(tmp_path, tmp_path / "missing", tmp_path / "output"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("attribute", "key", "value"),
    (
        ("owner_acceptance", "accepted", False),
        ("owner_acceptance", "accepted", 1),
        ("owner_acceptance", "agent_acquisition_authorized", False),
        ("owner_acceptance", "agreement_sha256", "0" * 64),
        ("owner_acceptance", "accepted_as", "organization"),
        ("community_copy_receipt", "status", "pending"),
        ("community_copy_receipt", "product_id", "Microsoft.VisualStudio.Product.BuildTools"),
        ("community_copy_receipt", "licensed_copy_review", "unreviewed"),
        ("community_copy_receipt", "product_version", "18.0.0"),
        ("recipient_terms_review", "status", "pending"),
        ("recipient_terms_review", "qt_lgpl_rights_preserved", False),
        ("recipient_terms_review", "microsoft_scope", "MIT"),
        ("recipient_terms_review", "runtime_architecture", "ARM64"),
    ),
)
def test_ineligible_unaccepted_or_unreviewed_receipt_never_creates_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribute: str, key: str, value: object
) -> None:
    inputs = change_receipt(fixture(tmp_path, monkeypatch), attribute, key, value)
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


def test_synthetic_approved_inputs_copy_only_five_exact_objects_and_no_completion_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    assert inputs.community_copy_root is not None
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = tool.prepare(inputs)
    assert report["PWSCompleteRuntime"] is False
    assert sorted(
        path.relative_to(inputs.output).as_posix()
        for path in inputs.output.rglob("*")
        if path.is_file()
    ) == sorted(["overlay-receipt.json", *("runtime/" + member.name for member in tool.MEMBERS)])
    assert json.loads((inputs.output / "overlay-receipt.json").read_bytes()) == report
    for member in tool.MEMBERS:
        assert (inputs.output / "runtime" / member.name).read_bytes() == (
            inputs.inspection_root / member.source
        ).read_bytes()
    assert all(path.read_bytes() == payload for path, payload in originals.items())


def test_bootstrapper_alone_is_not_product_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    assert inputs.community_copy_receipt is not None
    copy = json.loads(inputs.community_copy_receipt.read_bytes())
    inputs = change_receipt(inputs, "community_copy_receipt", "artifacts", copy["artifacts"][:1])
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


def test_new_route_supplies_exact_canonical_msvcp_bytes_at_required_loader_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    report = tool.prepare(inputs)
    assert report["schema_version"] == 2
    assert report["pikepdf_alias_disposition"] == tool.COMMUNITY_ALIAS_DISPOSITION
    assert report["PWSCompleteRuntime"] is False
    canonical = inputs.output / "runtime/msvcp140.dll"
    alias = inputs.output / tool.PIKEPDF_ALIAS
    assert alias.read_bytes() == canonical.read_bytes()
    assert alias.read_bytes() == (inputs.inspection_root / tool.MEMBERS[0].source).read_bytes()
    assert len(report["members"]) == 9
    assert len({row["sha256"] for row in report["members"]}) == 5
    assert {row["path"] for row in report["members"]} == {
        *("runtime/" + member.name for member in tool.MEMBERS),
        tool.PIKEPDF_ALIAS,
        *("runtime/engines/qpdf/bin/" + name for name in tool.QPDF_APP_LOCAL_NAMES),
    }
    for name in tool.QPDF_APP_LOCAL_NAMES:
        assert (inputs.output / "runtime/engines/qpdf/bin" / name).read_bytes() == (
            inputs.output / "runtime" / name
        ).read_bytes()


@pytest.mark.parametrize("version", [0, 3, True])
def test_invalid_or_future_recipient_review_fails_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    inputs = change_receipt(
        fixture(tmp_path, monkeypatch), "recipient_terms_review", "schema_version", version
    )
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


def test_builder_verifier_rechecks_exact_overlay_and_all_bound_artifacts_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    report = tool.prepare(inputs)
    receipt = inputs.output / "overlay-receipt.json"
    expected = tool._digest(receipt.read_bytes())
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert tool.verify_overlay(inputs.output, expected) == report
    assert all(path.read_bytes() == payload for path, payload in originals.items())
    assert inputs.community_copy_root is not None
    artifact = next(inputs.community_copy_root.iterdir())
    artifact.write_bytes(b"changed acquisition artifact")
    with pytest.raises(tool.PreparationError):
        tool.verify_overlay(inputs.output, expected)


def test_legacy_five_file_overlay_cannot_satisfy_full_route_builder_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    tool.prepare(inputs)
    digest = tool._digest((inputs.output / "overlay-receipt.json").read_bytes())
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_OVERLAY_INVALID"):
        tool.verify_overlay(inputs.output, digest)


def test_input_hash_change_or_existing_output_fails_without_mutating_existing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    path = inputs.inspection_root / tool.MEMBERS[0].source
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_MEMBER_CHANGED"):
        tool.prepare(inputs)
    assert not inputs.output.exists()
    path.write_bytes(original)
    inputs.output.mkdir()
    sentinel = inputs.output / "unknown.txt"
    sentinel.write_bytes(b"keep unrelated synthetic data")
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_OUTPUT_EXISTS"):
        tool.prepare(inputs)
    assert sentinel.read_bytes() == b"keep unrelated synthetic data"


def test_missing_receipt_cli_is_stable_and_does_not_echo_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = tool.main(
        [
            "--inspection-root",
            str(tmp_path / "PRIVATE-CANARY"),
            "--reference",
            str(tmp_path / "PRIVATE-CANARY"),
            "--output",
            str(tmp_path / "output"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2 and "MICROSOFT_RUNTIME_RIGHTS_REQUIRED" in captured.err
    assert "PRIVATE-CANARY" not in captured.err and not captured.out
    assert not (tmp_path / "output").exists()


def test_legitimate_visual_studio_package_directory_punctuation_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    assert inputs.community_copy_root is not None and inputs.community_copy_receipt is not None
    document = json.loads(inputs.community_copy_receipt.read_bytes())
    artifact = document["artifacts"][0]
    old = inputs.community_copy_root / artifact["path"]
    relative = "Microsoft.VC.Redist.14.Latest,version=14.44.35211,chip=x64/package.bin"
    target = inputs.community_copy_root / relative
    target.parent.mkdir()
    old.rename(target)
    artifact["path"] = relative
    inputs = change_receipt(inputs, "community_copy_receipt", "artifacts", document["artifacts"])
    inputs = change_receipt(
        inputs,
        "recipient_terms_review",
        "community_copy_receipt_sha256",
        inputs.community_copy_receipt_sha256,
    )
    assert tool.prepare(inputs)["PWSCompleteRuntime"] is False
