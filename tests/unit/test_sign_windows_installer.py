"""Production installer orchestration with synthetic approval/native/compiler ports.

The model build below never produces trusted signatures or runs an installer.
It exercises real byte binding, inventory generation and policy/refusal ordering.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_release_evidence import make_candidate as release_candidate
from tests.unit.test_windows_signing import fake_signed_pe, native_result, unsigned_pe, write_policy
from tools import build_windows_installer as build
from tools import sign_windows_installer as production
from tools import windows_signing as signing
from tools.release_artifacts import ReleaseArtifactError
from tools.release_staging import canonical_json, inventory_files


def rights_data(
    plan: dict[str, Any],
    plan_digest: str,
    inventory: dict[str, Any],
    policy: signing.SigningPolicy,
    signer_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "approval": "maintainer-approved",
        "approval_scope": "production-signing-of-exact-candidate",
        "approved_by": "Janne Vuorela",
        "candidate_id": plan["candidate_id"],
        "engineering_plan_sha256": plan_digest,
        "engineering_inventory_sha256": plan["inventory_sha256"],
        "selected_component_ids_sha256": production.component_selection_sha256(inventory),
        "signing_policy_sha256": policy.policy_sha256,
        "signer_sha256": signer_digest,
        "signing_provider_route": "maintainer-controlled-executable",
        "all_selected_component_rights_resolved": True,
        "microsoft_runtime_distribution_resolved": True,
    }


@pytest.mark.parametrize(
    "changed",
    [
        "approval",
        "candidate_id",
        "engineering_plan_sha256",
        "engineering_inventory_sha256",
        "selected_component_ids_sha256",
        "signing_policy_sha256",
        "signer_sha256",
        "all_selected_component_rights_resolved",
        "microsoft_runtime_distribution_resolved",
    ],
)
def test_exact_component_and_provider_approval_cannot_be_inferred_or_rebound(
    tmp_path: Path,
    changed: str,
) -> None:
    path, digest = write_policy(tmp_path / "policy.json")
    policy = signing.load_policy(path, digest)
    plan = {"candidate_id": "synthetic-candidate", "inventory_sha256": "d" * 64}
    inventory = {
        "components": [{"component_id": "paperworkshell"}, {"component_id": "synthetic-msvc"}]
    }
    good = rights_data(plan, "e" * 64, inventory, policy, "f" * 64)
    bad = copy.deepcopy(good)
    bad[changed] = False if isinstance(bad[changed], bool) else "not-approved"
    review = tmp_path / "rights.json"
    review.write_bytes(canonical_json(bad))
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_RIGHTS_REVIEW_REQUIRED"):
        production.load_rights_review(
            review,
            hashlib.sha256(review.read_bytes()).hexdigest(),
            plan,
            "e" * 64,
            inventory,
            policy,
            "f" * 64,
        )


def test_missing_policy_refuses_before_creating_or_mutating_any_candidate(tmp_path: Path) -> None:
    output = tmp_path / "output/pws0802/signed"
    sentinel = tmp_path / "external-original.pdf"
    sentinel.write_bytes(b"synthetic external original")
    with pytest.raises(signing.WindowsSigningError):
        production.build_signed(
            tmp_path / "engineering",
            output,
            tmp_path / "compiler",
            tmp_path / "unapproved-policy.json",
            "a" * 64,
            tmp_path / "rights.json",
            "b" * 64,
            tmp_path / "signer.exe",
            "c" * 64,
            root=tmp_path,
        )
    assert not output.exists()
    assert sentinel.read_bytes() == b"synthetic external original"


@pytest.mark.parametrize("bad", ['quoted"path', "dollar$path", "newline\npath", "return\rpath"])
def test_inno_callback_command_rejects_source_substitution_characters(
    tmp_path: Path, bad: str
) -> None:
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_CALLBACK_ARGUMENT"):
        production.callback_command(tmp_path / bad / "context.json", "a" * 64, root=tmp_path)


def test_callback_command_is_fixed_program_syntax_with_only_inno_filename_substitution(
    tmp_path: Path,
) -> None:
    command = production.callback_command(tmp_path / "context.json", "a" * 64, root=tmp_path)
    assert command.endswith(" $f")
    assert command.count("$f") == 1
    assert "cmd.exe" not in command
    assert "powershell" not in command.lower()
    assert "--context-sha256" in command
    assert "tools.sign_windows_installer" in command


@pytest.mark.parametrize("clone_fault", [False, True])
def test_synthetic_orchestration_signs_before_inventory_and_requires_installed_copy_for_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clone_fault: bool,
) -> None:
    inventory, seed, _ = release_candidate(tmp_path)
    engineering = tmp_path / "output/pws0802/engineering"
    engineering.mkdir(parents=True)
    shutil.copytree(seed, engineering / "payload")
    payload = engineering / "payload"
    owners = {record["path"]: record["component_id"] for record in inventory["files"]}
    for relative, raw in (
        ("runtime/PaperWorkShell.exe", unsigned_pe()),
        ("notices/UNSIGNED_INTERNAL.txt", b"unsigned internal"),
    ):
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        owners[relative] = "paperworkshell"
    inventory["files"] = inventory_files(payload, owners)
    product_path = tmp_path / build.PRODUCT
    product_path.parent.mkdir(parents=True)
    product_path.write_bytes((build.PROJECT_ROOT / build.PRODUCT).read_bytes())
    schema = tmp_path / "schemas/resolved-dependency-inventory-v1.schema.json"
    schema.parent.mkdir()
    schema.write_bytes((build.PROJECT_ROOT / schema.relative_to(tmp_path)).read_bytes())
    for source in production._SOURCE:
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((build.PROJECT_ROOT / source).read_bytes())
    configuration = build.product(tmp_path)
    build.generate_includes(engineering, payload, configuration, inventory["files"])
    plan = {"candidate_id": configuration["candidate_id"], "inventory_sha256": "d" * 64}
    plan_digest = "e" * 64
    monkeypatch.setattr(production, "_engineering", lambda *_a: (plan, inventory, plan_digest))
    monkeypatch.setattr(build, "verify_compiler", lambda *_a, **_k: {})
    policy_path, policy_digest = write_policy(tmp_path / "public-policy.json")
    policy = signing.load_policy(policy_path, policy_digest)
    signer = tmp_path / "approved-synthetic-signer.exe"
    signer.write_bytes(b"not executed; explicit synthetic signing port")
    signer_digest = hashlib.sha256(signer.read_bytes()).hexdigest()
    rights = tmp_path / "synthetic-rights.json"
    rights.write_bytes(
        canonical_json(rights_data(plan, plan_digest, inventory, policy, signer_digest))
    )
    rights_digest = hashlib.sha256(rights.read_bytes()).hexdigest()
    order: list[str] = []

    def external_sign(argv: list[str], *, cwd: Path) -> bytes:
        assert argv[0] == str(signer)
        path = Path(argv[1])
        order.append(path.name)
        path.write_bytes(fake_signed_pe(path.read_bytes()))
        return b""

    monkeypatch.setattr(signing, "_run_bounded", external_sign)
    monkeypatch.setattr(signing, "_native_verify", lambda *_a, **_k: native_result(policy))

    def compile_model(argv: list[str], work: Path, label: str, **kwargs: object) -> None:
        candidate = work.parent
        # These checks prove the launcher signature changes precede inventory/config.
        actual = json.loads((candidate / "resolved-inventory.json").read_bytes())
        expected = next(
            record for record in actual["files"] if record["path"] == "runtime/PaperWorkShell.exe"
        )
        assert (
            expected["sha256"]
            == hashlib.sha256(
                (candidate / "payload/runtime/PaperWorkShell.exe").read_bytes()
            ).hexdigest()
        )
        assert "#define PWSSignedBuild 1" in (candidate / "config.iss.inc").read_text()
        assert any(arg.startswith("--signtool=pws_approved_signer=") for arg in argv)
        context = candidate / "signing-context.json"
        context_digest = hashlib.sha256(context.read_bytes()).hexdigest()
        for relative in (
            "signed-uninstallers/uninst-synthetic.e64",
            "installer/PaperWorkShell-signed.exe",
        ):
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(unsigned_pe())
            production.callback(context, context_digest, path, root=tmp_path)

    monkeypatch.setattr(build, "_run", compile_model)
    output = tmp_path / "output/pws0802/signed"
    if clone_fault:
        original_copy = build._copy

        def corrupt_copy(source: Path, target: Path, **kwargs: Any) -> None:
            original_copy(source, target, **kwargs)
            if target == output / "payload/runtime/PaperWorkShell.exe":
                corrupted = bytearray(target.read_bytes())
                corrupted[-1] ^= 1
                target.write_bytes(corrupted)

        monkeypatch.setattr(build, "_copy", corrupt_copy)
        with pytest.raises(ReleaseArtifactError):
            production.build_signed(
                engineering,
                output,
                tmp_path / "compiler",
                policy_path,
                policy_digest,
                rights,
                rights_digest,
                signer,
                signer_digest,
                root=tmp_path,
            )
        assert order == []
        assert (payload / "runtime/PaperWorkShell.exe").read_bytes() == unsigned_pe()
        return
    receipt = production.build_signed(
        engineering,
        output,
        tmp_path / "compiler",
        policy_path,
        policy_digest,
        rights,
        rights_digest,
        signer,
        signer_digest,
        root=tmp_path,
    )
    assert order[0] == "PaperWorkShell.exe"
    assert receipt["public_distribution_allowed"] is False
    assert receipt["status"] == "signed-awaiting-embedded-uninstaller-verification"
    assert receipt["installer"]["image_binding"] == "verified"
    assert (payload / "runtime/PaperWorkShell.exe").read_bytes() == unsigned_pe()
    qualified = output / "qualification/installed-unins000.exe"
    qualified.parent.mkdir()
    shutil.copyfile(output / "signed-uninstallers/uninst-synthetic.e64", qualified)
    build_digest = hashlib.sha256((output / "signed-build-receipt.json").read_bytes()).hexdigest()
    finalized = production.finalize(
        output,
        qualified.relative_to(output).as_posix(),
        build_digest,
        policy_path,
        policy_digest,
        root=tmp_path,
    )
    assert finalized["status"] == "signed-artifacts-verified"
    assert finalized["public_distribution_allowed"] is False
    assert finalized["qualification"] == "separate-controller-receipts-required"
