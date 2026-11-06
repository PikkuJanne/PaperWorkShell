"""Owned host qualification refuses adoption and never opens real case contents."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tools import qualify_windows_installer as qualify
from tools.release_artifacts import file_hashes
from tools.release_staging import inventory_files
from tools.windows_signing import WindowsSigningError, run_build_process


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _candidate(root: Path) -> tuple[dict[str, Any], Path, Path]:
    base = root / "output/pws0802/candidate"
    marker = "runtime/paperworkshell-installed-v1.json"
    _write(base / "payload" / marker, b'{"synthetic":true}\n')
    files = inventory_files(base / "payload", {marker: "synthetic"})
    inventory = {"candidate": {"id": "test-internal"}, "files": files}
    inventory_sha = _write(base / "resolved-inventory.json", json.dumps(inventory).encode())
    installer = base / "installer/synthetic.exe"
    installer_sha = _write(installer, b"Synthetic installer, never executed")
    generated = []
    for name in ("config.iss.inc", "files.iss.inc", "checks.iss.inc"):
        generated.append({"path": name, "sha256": _write(base / name, b"; synthetic\n")})
    receipt = base / "installer-receipt.json"
    _write(
        receipt,
        json.dumps(
            {
                "schema_version": 1,
                "status": "unsigned-internal-built",
                "public_distribution_allowed": False,
                "public_trusted_signature": False,
                "installer": installer.name,
                "installer_sha256": installer_sha,
                "installer_size_bytes": installer.stat().st_size,
                "inventory_sha256": inventory_sha,
                "candidate_id": "test-internal",
                "generated_inputs": generated,
            }
        ).encode(),
    )
    candidate = qualify.inspect_candidate(
        installer,
        receipt,
        expected_installer_sha256=installer_sha,
        expected_receipt_sha256=file_hashes(receipt)[1],
        root=root,
    )
    return candidate, installer, receipt


def _host(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[Path, Path]:
    installation = root / "synthetic-host/Programs/PaperWorkShell"
    cases = root / "synthetic-host/Cases"
    cases.mkdir(parents=True)
    _write(cases / "case-one/DO-NOT-OPEN.txt", b"synthetic private sentinel")
    monkeypatch.setattr(qualify, "_host_paths", lambda: (installation, cases))
    monkeypatch.setattr(qualify, "_registrations", lambda: [])
    monkeypatch.setattr(qualify, "_token_elevated", lambda: False)
    return installation, cases


def test_candidate_inputs_have_independent_anchors_and_exhaustive_payload(tmp_path: Path) -> None:
    candidate, installer, receipt = _candidate(tmp_path)
    with pytest.raises(qualify.QualificationError, match="INPUT_HASH"):
        qualify.inspect_candidate(
            installer,
            receipt,
            expected_installer_sha256=candidate["installer_sha256"],
            expected_receipt_sha256="0" * 64,
            root=tmp_path,
        )
    _write(receipt.parent / "payload/runtime/extra.exe", b"Unexpected")
    with pytest.raises(ValueError, match="UNOWNED_FILE"):
        qualify.inspect_candidate(
            installer,
            receipt,
            expected_installer_sha256=candidate["installer_sha256"],
            expected_receipt_sha256=candidate["build_receipt_sha256"],
            root=tmp_path,
        )


@pytest.mark.parametrize("preexisting", ["directory", "registration", "elevated"])
def test_preexisting_product_and_elevated_token_refuse_before_case_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preexisting: str
) -> None:
    candidate, _installer, _receipt = _candidate(tmp_path)
    installation, _cases = _host(monkeypatch, tmp_path)
    if preexisting == "directory":
        installation.mkdir(parents=True)
    elif preexisting == "registration":
        monkeypatch.setattr(qualify, "_registrations", lambda: [(1, 1)])
    else:
        monkeypatch.setattr(qualify, "_token_elevated", lambda: True)

    def no_count(_path: Path) -> int:
        pytest.fail("A refused preflight must not inspect cases")

    monkeypatch.setattr(qualify, "case_entry_count", no_count)
    work = tmp_path / "output/pws0802/qualification/test"
    with pytest.raises(qualify.QualificationError, match="PREEXISTING_PRODUCT|ELEVATED_TOKEN"):
        qualify.prepare_session(candidate, work, root=tmp_path)
    assert not work.exists()


def test_mutations_require_previous_session_anchor_and_reject_edited_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, _installer, _receipt = _candidate(tmp_path)
    _host(monkeypatch, tmp_path)
    work = tmp_path / "output/pws0802/qualification/test"
    initial = qualify.prepare_session(candidate, work, root=tmp_path)
    assert initial["scope"] == "HOST_NOT_CLEAN"
    assert initial["actual_standard_account_qualification"] == "NOT_PROVED"
    assert initial["normal_public_first_launch"] == "NOT_RUN"
    assert initial["initial_case_entry_count"] == 1
    with pytest.raises(qualify.QualificationError, match="ANCHOR_REQUIRED"):
        qualify.run_action("preserve-uninstall", work, root=tmp_path)
    data = json.loads((work / qualify.SESSION).read_bytes())
    data["owned_installation"] = True
    (work / qualify.SESSION).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(qualify.QualificationError, match="INPUT_HASH"):
        qualify.run_action(
            "preserve-uninstall",
            work,
            expected_session_sha256=initial["session_sha256"],
            root=tmp_path,
        )


def test_owned_preserve_uninstall_reinstall_and_uninstaller_tamper_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, _installer, receipt = _candidate(tmp_path)
    installation, cases = _host(monkeypatch, tmp_path)
    work = tmp_path / "output/pws0802/qualification/test"
    state = qualify.prepare_session(candidate, work, root=tmp_path)
    external = tmp_path / "synthetic-external"
    _write(external / "original.pdf", b"original sentinel")
    _write(external / "export.pdf", b"export sentinel")
    preserved = {
        path: file_hashes(path)[1]
        for path in (cases / "case-one/DO-NOT-OPEN.txt", *external.iterdir())
    }
    registered = False
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(qualify, "_registrations", lambda: [(1, 1)] if registered else [])
    monkeypatch.setattr(qualify, "_check_registration", lambda _installation: None)

    def execute(argv: list[str], _step: Path, *, root: Path) -> None:
        nonlocal registered
        calls.append(tuple(argv))
        assert tuple(argv[1:]) == qualify.QUIET_ARGUMENTS
        if Path(argv[0]).name == "unins000.exe":
            assert Path(argv[0]) == installation / "unins000.exe"
            shutil.rmtree(installation)  # This fixture's synthetic product only.
            registered = False
        else:
            shutil.copytree(receipt.parent / "payload", installation)
            _write(installation / "unins000.exe", b"owned synthetic uninstaller")
            _write(installation / "unins000.dat", b"owned synthetic uninstall inventory")
            registered = True

    monkeypatch.setattr(qualify, "_execute", execute)
    for action in ("install", "preserve-uninstall", "reinstall"):
        state = qualify.run_action(
            action, work, expected_session_sha256=state["session_sha256"], root=tmp_path
        )
    assert len(calls) == 3
    assert all(file_hashes(path)[1] == digest for path, digest in preserved.items())
    _write(installation / "unins000.exe", b"replacement command must never run")
    with pytest.raises(qualify.QualificationError, match="INSTALL_NOT_OWNED"):
        qualify.run_action(
            "preserve-uninstall",
            work,
            expected_session_sha256=state["session_sha256"],
            root=tmp_path,
        )
    assert len(calls) == 3
    failed = qualify.run_action("status", work, root=tmp_path)
    assert failed["pending_action"] == "preserve-uninstall"
    assert failed["observations"][-1]["status"] == "failed"
    assert all(file_hashes(path)[1] == digest for path, digest in preserved.items())


def test_case_count_change_cannot_be_recorded_as_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, _installer, _receipt = _candidate(tmp_path)
    installation, cases = _host(monkeypatch, tmp_path)
    work = tmp_path / "output/pws0802/qualification/test"
    state = qualify.prepare_session(candidate, work, root=tmp_path)
    monkeypatch.setattr(qualify, "_installed_files", lambda *_args: {})
    monkeypatch.setattr(qualify, "_check_registration", lambda _installation: None)
    registered = False
    monkeypatch.setattr(qualify, "_registrations", lambda: [(1, 1)] if registered else [])

    def execute(_argv: list[str], _step: Path, *, root: Path) -> None:
        nonlocal registered
        installation.mkdir(parents=True)
        (cases / "concurrent-entry").mkdir()
        registered = True

    monkeypatch.setattr(qualify, "_execute", execute)
    with pytest.raises(qualify.QualificationError, match="CASE_COUNT_CHANGED"):
        qualify.run_action(
            "install", work, expected_session_sha256=state["session_sha256"], root=tmp_path
        )
    failed = qualify.run_action("status", work, root=tmp_path)
    assert [row["status"] for row in failed["observations"]] == ["failed"]
    assert failed["case_count_after_failed_action"] == 2
    with pytest.raises(qualify.QualificationError, match="PREVIOUS_ACTION_INCOMPLETE"):
        qualify.run_action(
            "install", work, expected_session_sha256=failed["session_sha256"], root=tmp_path
        )


def test_fixed_installed_harnesses_use_only_explicit_isolated_composition(tmp_path: Path) -> None:
    for source in (qualify._SMOKE, qualify._GUI):
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert not any(
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"main", "build_backend", "for_current_user"}
            for node in calls
        )
        assert any(
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "_build_backend"
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "manager"
            for node in calls
        )
        assert "SetErrorMode(0x8003)" in qualify._GUI
    step = tmp_path / "harness"
    qualify.prepare_smoke(step)
    assert (step / "smoke.py").read_text(encoding="utf-8") == qualify._SMOKE
    for name, (_relative, expected) in qualify.FIXTURES.items():
        assert file_hashes(step / name)[1] == expected
    with pytest.raises(FileExistsError):
        qualify.prepare_smoke(step)


@pytest.mark.parametrize("relative", ["output/pws0802/qualification", "output/other/attempt"])
def test_work_directory_cannot_adopt_output_boundary(tmp_path: Path, relative: str) -> None:
    with pytest.raises(qualify.QualificationError, match="WORK_SCOPE"):
        qualify.owned_work(tmp_path / relative, root=tmp_path)


@pytest.mark.parametrize("grace", [-1, 31, float("nan"), True])
def test_success_cleanup_grace_cannot_expand_the_build_process_limit(
    tmp_path: Path, grace: float
) -> None:
    with pytest.raises(WindowsSigningError, match="SIGNING_PROCESS_LIMIT"):
        run_build_process(["never-executed"], tmp_path, successful_descendant_grace_seconds=grace)


@pytest.mark.parametrize(
    "program,grace", [("unins000.exe", 30), ("installer.exe", 0), ("python.exe", 0)]
)
def test_only_fixed_uninstaller_execution_requests_descendant_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, program: str, grace: int
) -> None:
    observed: list[object] = []

    def run(*_args: object, **kwargs: object) -> None:
        observed.append(kwargs["successful_descendant_grace_seconds"])

    from tools import build_windows_installer

    monkeypatch.setattr(build_windows_installer, "_run", run)
    qualify._execute([str(tmp_path / program)], tmp_path / "step", root=tmp_path)
    assert observed == [grace]
