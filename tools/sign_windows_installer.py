"""Sign an exact engineering installer stage without granting public release.

This separate production handoff requires pinned public signing and component
rights approvals before creating or changing artifacts. It never enrolls a key,
logs credentials, uploads files, installs a product or claims guest qualification.
The final embedded-uninstaller receipt requires a qualifier-collected installed
copy matching the exact compiler-generated, image-bound signed material.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Never

from tools import build_windows_installer as build
from tools import windows_signing as signing
from tools.release_artifacts import safe_relative
from tools.release_evidence import load_json, validate_data
from tools.release_sbom import build_cyclonedx, build_notices, build_spdx
from tools.release_staging import canonical_json, inventory_files, verify_files

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
_HASH: Final = re.compile(r"[0-9a-f]{64}\Z")
_GENERATED: Final = {"config.iss.inc", "files.iss.inc", "checks.iss.inc"}
_SOURCE: Final = ("tools/windows_signing.py", "tools/sign_windows_installer.py")
_RUNNER: Final = (
    "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
    "runpy.run_module('tools.sign_windows_installer',run_name='__main__')"
)


def _fail(code: str) -> Never:
    raise signing.WindowsSigningError(code)


def _digest(path: Path) -> str:
    return signing._hash(path.absolute())[1]


def _text_argument(value: str) -> str:
    if not value or any(ord(char) < 32 or char in '$"' for char in value):
        _fail("SIGNING_CALLBACK_ARGUMENT")
    return "$q" + value + "$q"


def _engineering(path: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    candidate = signing._candidate_root(path, root)
    plan, digest = signing._read_json(candidate / "build-plan.json", 64 * 1024)
    if (
        type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
        or plan.get("status") != "unsigned-internal-stage"
        or plan.get("public_distribution_allowed") is not False
    ):
        _fail("SIGNING_ENGINEERING_PLAN")
    for name, expected in (
        (build.PRODUCT, plan["product_sha256"]),
        (build.SCRIPT, plan["script_sha256"]),
        (build.COMPILER_REVIEW, plan["compiler_review_sha256"]),
    ):
        if _digest(root / name) != expected:
            _fail("SIGNING_ENGINEERING_RECIPE")
    inventory, inventory_digest = signing._read_json(
        candidate / "resolved-inventory.json", 32 * 1024 * 1024
    )
    if inventory_digest != plan["inventory_sha256"]:
        _fail("SIGNING_ENGINEERING_INVENTORY")
    generated = plan["generated_inputs"]
    if (
        not isinstance(generated, list)
        or len(generated) != len(_GENERATED)
        or {item.get("path") for item in generated if isinstance(item, dict)} != _GENERATED
    ):
        _fail("SIGNING_ENGINEERING_GENERATED")
    for item in generated:
        if _digest(candidate / item["path"]) != item["sha256"]:
            _fail("SIGNING_ENGINEERING_GENERATED")
    validate_data(inventory, root=root)
    for item in inventory["review_inputs"]:
        if _digest(root / safe_relative(item["path"])) != item["sha256"]:
            _fail("SIGNING_ENGINEERING_RECIPE")
    verify_files(candidate / "payload", inventory["files"])
    return plan, inventory, digest


def component_selection_sha256(inventory: Mapping[str, Any]) -> str:
    identifiers = sorted(component["component_id"] for component in inventory["components"])
    return hashlib.sha256(canonical_json(identifiers)).hexdigest()


def load_rights_review(
    path: Path,
    expected_sha256: str,
    plan: Mapping[str, Any],
    plan_sha256: str,
    inventory: Mapping[str, Any],
    policy: signing.SigningPolicy,
    signer_sha256: str,
) -> dict[str, Any]:
    """Consume explicit approval; neither source licensing nor a signature supplies it."""

    value, digest = signing._read_json(path.absolute(), 64 * 1024)
    expected = {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "approval": "maintainer-approved",
        "approval_scope": "production-signing-of-exact-candidate",
        "approved_by": "Janne Vuorela",
        "candidate_id": plan["candidate_id"],
        "engineering_plan_sha256": plan_sha256,
        "engineering_inventory_sha256": plan["inventory_sha256"],
        "selected_component_ids_sha256": component_selection_sha256(inventory),
        "signing_policy_sha256": policy.policy_sha256,
        "signer_sha256": signer_sha256,
        "signing_provider_route": "maintainer-controlled-executable",
        "all_selected_component_rights_resolved": True,
        "microsoft_runtime_distribution_resolved": True,
    }
    if (
        _HASH.fullmatch(expected_sha256) is None
        or digest != expected_sha256
        or set(value) != set(expected)
        or any(
            type(value[key]) is not type(item) or value[key] != item
            for key, item in expected.items()
        )
    ):
        _fail("SIGNING_RIGHTS_REVIEW_REQUIRED")
    return value


def _authorise(
    context: Mapping[str, Any], root: Path
) -> tuple[signing.SigningPolicy, dict[str, Any], dict[str, Any]]:
    expected_keys = {
        "schema_version",
        "application_id",
        "candidate",
        "engineering",
        "engineering_plan_sha256",
        "policy",
        "policy_sha256",
        "rights_review",
        "rights_review_sha256",
        "signer",
        "signer_sha256",
        "online_revocation",
    }
    if (
        set(context) != expected_keys
        or type(context["schema_version"]) is not int
        or context["schema_version"] != 1
        or context["application_id"] != signing.APPLICATION_ID
        or type(context["online_revocation"]) is not bool
    ):
        _fail("SIGNING_CONTEXT")
    policy = signing.load_policy(Path(context["policy"]), context["policy_sha256"])
    plan, inventory, digest = _engineering(Path(context["engineering"]), root)
    if digest != context["engineering_plan_sha256"]:
        _fail("SIGNING_ENGINEERING_PLAN")
    signer = Path(context["signer"])
    if (
        not signer.is_absolute()
        or signer.suffix.lower() != ".exe"
        or _digest(signer) != context["signer_sha256"]
    ):
        _fail("SIGNING_SIGNER")
    load_rights_review(
        Path(context["rights_review"]),
        context["rights_review_sha256"],
        plan,
        digest,
        inventory,
        policy,
        context["signer_sha256"],
    )
    return policy, plan, inventory


def callback_command(context: Path, context_sha256: str, *, root: Path = PROJECT_ROOT) -> str:
    if _HASH.fullmatch(context_sha256) is None:
        _fail("SIGNING_CONTEXT")
    values = [
        str(Path(sys.executable).absolute()),
        "-I",
        "-B",
        "-c",
        _RUNNER,
        str(root.absolute()),
        "callback",
        "--context",
        str(context.absolute()),
        "--context-sha256",
        context_sha256,
        "--file",
    ]
    # ISCC invokes this fixed program command directly. $f is Inno's own quoted
    # filename substitution, not a shell. All other values are controlled here.
    return " ".join(_text_argument(value) for value in values) + " $f"


def callback(
    context_path: Path,
    context_sha256: str,
    path: Path,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    context, digest = signing._read_json(context_path.absolute(), 64 * 1024)
    if digest != context_sha256:
        _fail("SIGNING_CONTEXT")
    policy, _, _ = _authorise(context, root)
    candidate = signing._candidate_root(Path(context["candidate"]), root)
    absolute = path.absolute()
    signing._plain(absolute)
    allowed = (candidate / "signed-uninstallers", candidate / "inno-temp", candidate / "installer")
    if not any(absolute.is_relative_to(directory) for directory in allowed):
        _fail("SIGNING_CALLBACK_SCOPE")
    relative = absolute.relative_to(candidate).as_posix()
    receipt = signing.sign(
        candidate,
        relative,
        policy,
        Path(context["signer"]),
        context["signer_sha256"],
        root=root,
        online_revocation=context["online_revocation"],
    )
    receipts = candidate / "signing-receipts"
    signing._plain(receipts, directory=True)
    name = hashlib.sha256(relative.encode()).hexdigest() + ".json"
    build._write(receipts / name, canonical_json(receipt))
    return receipt


def _freeze_payload(
    output: Path, inventory: dict[str, Any], owners: Mapping[str, str], root: Path
) -> None:
    inventory["files"] = inventory_files(output / "payload", owners)
    validate_data(inventory, root=root)
    build._write(output / "resolved-inventory.json", canonical_json(inventory))
    build._write(output / "bom.cdx.json", canonical_json(build_cyclonedx(inventory)))
    build._write(output / "bom.spdx.json", canonical_json(build_spdx(inventory)))
    build._write(output / "THIRD_PARTY_NOTICES.txt", build_notices(inventory).encode())


def _bound_result(
    candidate: Path,
    relative: str,
    policy: signing.SigningPolicy,
    *,
    online_revocation: bool,
    root: Path,
) -> dict[str, Any]:
    verified = signing.verify(
        candidate, relative, policy, root=root, online_revocation=online_revocation
    )
    name = hashlib.sha256(relative.encode()).hexdigest() + ".json"
    bound, _ = signing._read_json(candidate / "signing-receipts" / name, 64 * 1024)
    if (
        bound.get("path") != relative
        or bound.get("sha256") != verified["sha256"]
        or bound.get("policy_sha256") != policy.policy_sha256
        or bound.get("image_binding") != "verified"
        or any(
            type(bound.get(key)) is not str or _HASH.fullmatch(bound[key]) is None
            for key in ("unsigned_original_sha256", "unsigned_image_sha256")
        )
    ):
        _fail("SIGNING_ORIGINAL_BINDING")
    return {**verified, **bound}


def build_signed(
    engineering: Path,
    output: Path,
    compiler: Path,
    policy_path: Path,
    policy_sha256: str,
    rights_review: Path,
    rights_review_sha256: str,
    signer: Path,
    signer_sha256: str,
    *,
    online_revocation: bool = False,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    # Every approval, input and compiler check precedes mkdir, copying or signing.
    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    policy = signing.load_policy(policy_path, policy_sha256)
    engineering = signing._candidate_root(engineering, root)
    plan, source_inventory, plan_digest = _engineering(engineering, root)
    if (
        not signer.is_absolute()
        or signer.suffix.lower() != ".exe"
        or _digest(signer) != signer_sha256
    ):
        _fail("SIGNING_SIGNER")
    load_rights_review(
        rights_review,
        rights_review_sha256,
        plan,
        plan_digest,
        source_inventory,
        policy,
        signer_sha256,
    )
    build.verify_compiler(compiler, root=root)
    signing._unsigned_entry(engineering, "payload/runtime/PaperWorkShell.exe")
    output = build.owned_output(output, root=root)
    if output.exists() or output == engineering:
        _fail("SIGNING_EXISTS")
    for value in (
        output,
        engineering,
        compiler,
        policy_path,
        rights_review,
        signer,
        root,
        Path(sys.executable),
    ):
        _text_argument(str(value.absolute()))
    configuration = build.product(root)
    original_config = (engineering / "config.iss.inc").read_text(encoding="utf-8")
    complete = "#define PWSCompleteRuntime 1" in original_config.splitlines()
    output.mkdir(parents=True)
    payload = output / "payload"
    inventory = copy.deepcopy(source_inventory)
    owners = {item["path"]: item["component_id"] for item in inventory["files"]}
    for item in inventory["files"]:
        build._copy(
            engineering / "payload" / item["path"],
            payload / item["path"],
            maximum=item["size_bytes"],
        )
    # Rights and source approval bind the engineering bytes, not a new baseline
    # accidentally created by a failed copy. Authenticate the complete clone
    # before the first signing operation can change its approved original.
    verify_files(payload, source_inventory["files"])
    signed_launcher = signing.sign(
        output,
        "payload/runtime/PaperWorkShell.exe",
        policy,
        signer,
        signer_sha256,
        root=root,
        online_revocation=online_revocation,
    )
    unsigned_notice = "notices/UNSIGNED_INTERNAL.txt"
    if unsigned_notice not in owners or owners[unsigned_notice] != "paperworkshell":
        _fail("SIGNING_ENGINEERING_NOTICE")
    (payload / unsigned_notice).unlink()
    del owners[unsigned_notice]
    notice = "notices/INTERNAL_QUALIFICATION.txt"
    build._write(
        payload / notice,
        b"Signed internal qualification candidate. Not a public release.\nFull packaged qualification and release approval remain pending.\n",
    )
    owners[notice] = "paperworkshell"
    reviews = {item["path"]: item["sha256"] for item in inventory["review_inputs"]}
    added_sources = []
    for name in _SOURCE:
        relative = "sources/application/windows-signing-tooling-v1/" + Path(name).name
        build._copy(root / name, payload / relative)
        owners[relative] = "paperworkshell"
        added_sources.append(relative)
        reviews[name] = _digest(root / name)
    inventory["review_inputs"] = [
        {"path": name, "sha256": reviews[name]} for name in sorted(reviews)
    ]
    obligation = next(
        item for item in inventory["obligations"] if item["component_id"] == "paperworkshell"
    )
    obligation["source_paths"] = sorted(set(obligation["source_paths"]) | set(added_sources))
    obligation["distribution_note"] = (
        "Signed internal candidate under separately pinned component-rights and signer approval. Public distribution and packaged qualification remain gated."
    )
    _freeze_payload(output, inventory, owners, root)
    build.generate_includes(
        output, payload, configuration, inventory["files"], complete_runtime=complete
    )
    config = output / "config.iss.inc"
    text = config.read_text(encoding="utf-8")
    replacements = {
        "#define PWSSignedBuild 0": "#define PWSSignedBuild 1",
        '#define PWSChannelLabel "UNSIGNED INTERNAL - not a public release"': '#define PWSChannelLabel "SIGNED INTERNAL - not a public release"',
        str(payload / unsigned_notice): str(payload / notice),
        "-win-x64-UNSIGNED": "-win-x64-SIGNED-INTERNAL",
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            _fail("SIGNING_CONFIG")
        text = text.replace(before, after)
    signed_cache = output / "signed-uninstallers"
    for directory in (signed_cache, output / "inno-temp", output / "signing-receipts"):
        directory.mkdir()
    text += '#define PWSSignedUninstallerDirectory "' + str(signed_cache) + '"\n'
    config.write_text(text, encoding="utf-8")
    context = {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "candidate": str(output),
        "engineering": str(engineering),
        "engineering_plan_sha256": plan_digest,
        "policy": str(policy_path.absolute()),
        "policy_sha256": policy_sha256,
        "rights_review": str(rights_review.absolute()),
        "rights_review_sha256": rights_review_sha256,
        "signer": str(signer),
        "signer_sha256": signer_sha256,
        "online_revocation": online_revocation,
    }
    context_path = output / "signing-context.json"
    build._write(context_path, canonical_json(context))
    command = callback_command(context_path, _digest(context_path), root=root)
    generated = {name: _digest(output / name) for name in sorted(_GENERATED)}
    build._run(
        [
            str(compiler / "ISCC.exe"),
            "--no-ide-signtools",
            "/Qp",
            "--signtool=pws_approved_signer=" + command,
            "/DPWSGeneratedConfig=" + str(config),
            str(root / build.SCRIPT),
        ],
        output / "inno-temp",
        "compile-signed-installer",
        seconds=600,
        root=root,
    )
    _authorise(context, root)
    build.verify_compiler(compiler, root=root)
    if any(_digest(output / name) != digest for name, digest in generated.items()):
        _fail("SIGNING_CONFIG_CHANGED")
    verify_files(payload, inventory["files"])
    outputs = sorted((output / "installer").glob("*.exe"))
    if len(outputs) != 1:
        _fail("SIGNING_INSTALLER_OUTPUT")
    outer = _bound_result(
        output,
        outputs[0].relative_to(output).as_posix(),
        policy,
        root=root,
        online_revocation=online_revocation,
    )
    cached = []
    for path in sorted(signed_cache.iterdir()):
        relative = path.relative_to(output).as_posix()
        receipt = _bound_result(
            output, relative, policy, root=root, online_revocation=online_revocation
        )
        if path.name.lower().startswith("uninst"):
            cached.append({"path": relative, "component_id": "inno-setup", **receipt})
    if not cached:
        _fail("SIGNING_UNINSTALLER_CACHE")
    receipt = {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "candidate_id": plan["candidate_id"],
        "status": "signed-awaiting-embedded-uninstaller-verification",
        "public_distribution_allowed": False,
        "qualification": "not-run",
        "engineering_plan_sha256": plan_digest,
        "policy_sha256": policy_sha256,
        "rights_review_sha256": rights_review_sha256,
        "signer_sha256": signer_sha256,
        "launcher": signed_launcher,
        "installer": outer,
        "signed_uninstaller_material": cached,
        "inventory_sha256": _digest(output / "resolved-inventory.json"),
        "sbom_cdx_sha256": _digest(output / "bom.cdx.json"),
        "sbom_spdx_sha256": _digest(output / "bom.spdx.json"),
        "notices_sha256": _digest(output / "THIRD_PARTY_NOTICES.txt"),
        "generated_inputs": generated,
        "online_revocation": online_revocation,
        "tooling_sha256": {name: _digest(root / name) for name in _SOURCE},
    }
    build._write(output / "signed-build-receipt.json", canonical_json(receipt))
    checksums = {
        name: _digest(output / name)
        for name in (
            "resolved-inventory.json",
            "bom.cdx.json",
            "bom.spdx.json",
            "THIRD_PARTY_NOTICES.txt",
            "signed-build-receipt.json",
        )
    }
    checksums[outer["path"]] = outer["sha256"]
    build._write(
        output / "SIGNED_SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())).encode(),
    )
    return receipt


def finalize(
    candidate_root: Path,
    installed_uninstaller: str,
    expected_build_receipt_sha256: str,
    policy_path: Path,
    policy_sha256: str,
    *,
    online_revocation: bool = False,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    candidate = signing._candidate_root(candidate_root, root)
    policy = signing.load_policy(policy_path, policy_sha256)
    receipt, digest = signing._read_json(candidate / "signed-build-receipt.json", 128 * 1024)
    if (
        digest != expected_build_receipt_sha256
        or receipt.get("schema_version") != 1
        or receipt.get("application_id") != signing.APPLICATION_ID
        or receipt.get("status") != "signed-awaiting-embedded-uninstaller-verification"
        or receipt.get("policy_sha256") != policy_sha256
        or receipt.get("public_distribution_allowed") is not False
    ):
        _fail("SIGNING_FINALIZE_RECEIPT")
    safe_relative(installed_uninstaller)
    if not installed_uninstaller.startswith("qualification/"):
        _fail("SIGNING_FINALIZE_SCOPE")
    verified = signing.verify(
        candidate, installed_uninstaller, policy, root=root, online_revocation=online_revocation
    )
    if verified["sha256"] not in {
        item["sha256"] for item in receipt["signed_uninstaller_material"]
    }:
        _fail("SIGNING_EMBEDDED_UNINSTALLER_MISMATCH")
    outer = signing.verify(
        candidate,
        receipt["installer"]["path"],
        policy,
        root=root,
        online_revocation=online_revocation,
    )
    if outer["sha256"] != receipt["installer"]["sha256"]:
        _fail("SIGNING_FINALIZE_INSTALLER")
    for name, key in (
        ("bom.cdx.json", "sbom_cdx_sha256"),
        ("bom.spdx.json", "sbom_spdx_sha256"),
        ("THIRD_PARTY_NOTICES.txt", "notices_sha256"),
    ):
        if _digest(candidate / name) != receipt[key]:
            _fail("SIGNING_FINALIZE_SIDECAR")
    inventory = load_json(candidate / "resolved-inventory.json")
    if _digest(candidate / "resolved-inventory.json") != receipt["inventory_sha256"]:
        _fail("SIGNING_FINALIZE_INVENTORY")
    verify_files(candidate / "payload", inventory["files"])
    result = {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "status": "signed-artifacts-verified",
        "public_distribution_allowed": False,
        "qualification": "separate-controller-receipts-required",
        "build_receipt_sha256": digest,
        "installed_uninstaller": verified,
        "scope": "Installed-copy bytes match signed uninstaller material; guest provenance and full packaged qualification remain separate evidence.",
    }
    build._write(candidate / "signed-artifacts-finalization.json", canonical_json(result))
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        _fail("SIGNING_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    compile_action = actions.add_parser("build")
    for name in ("engineering", "output", "compiler", "policy", "rights-review", "signer"):
        compile_action.add_argument("--" + name, type=Path, required=True)
    for name in ("policy-sha256", "rights-review-sha256", "signer-sha256"):
        compile_action.add_argument("--" + name, required=True)
    compile_action.add_argument("--online-revocation", action="store_true")
    cb = actions.add_parser("callback")
    cb.add_argument("--context", type=Path, required=True)
    cb.add_argument("--context-sha256", required=True)
    cb.add_argument("--file", type=Path, required=True)
    finish = actions.add_parser("finalize")
    finish.add_argument("--candidate-root", type=Path, required=True)
    finish.add_argument("--installed-uninstaller", required=True)
    finish.add_argument("--build-receipt-sha256", required=True)
    finish.add_argument("--policy", type=Path, required=True)
    finish.add_argument("--policy-sha256", required=True)
    finish.add_argument("--online-revocation", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.action == "build":
            result = build_signed(
                args.engineering,
                args.output,
                args.compiler,
                args.policy,
                args.policy_sha256,
                args.rights_review,
                args.rights_review_sha256,
                args.signer,
                args.signer_sha256,
                online_revocation=args.online_revocation,
            )
        elif args.action == "callback":
            result = callback(args.context, args.context_sha256, args.file)
        else:
            result = finalize(
                args.candidate_root,
                args.installed_uninstaller,
                args.build_receipt_sha256,
                args.policy,
                args.policy_sha256,
                online_revocation=args.online_revocation,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except signing.WindowsSigningError as error:
        code = str(error)
    except OSError, ValueError, TypeError, KeyError, StopIteration:
        code = "SIGNING_INPUT"
    print(f"ERROR [{code}] Installer signing could not be verified.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
