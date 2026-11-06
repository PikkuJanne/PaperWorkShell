"""Inspection upgrades preserve exact human facts and cannot reuse stale proofs."""

from __future__ import annotations

import ast
import importlib.util
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_verification import (
    AUTOMATED_METHODS,
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from tests.security.test_redaction_verification_corpus import _verification_request
from tests.unit import test_pws0606_redaction_verification_harness as fixtures
from tools import dev
from tools import pws0606_redaction_verification as original
from tools import pws0606_viewer_resave as viewer

_COMPOSITION_ADDITIONS = (
    "src/paperworkshell/domain/redaction_export.py",
    "src/paperworkshell/domain/redaction_export_candidate.py",
    "src/paperworkshell/pdf/redaction_export.py",
    "src/paperworkshell/pdf/redaction_export_ocr.py",
)
_BUILD_GATE_EXAMPLES = {
    "src/paperworkshell/domain/redaction.py": (
        None,
        b"PERMANENT_REDACTION_ENABLED: Final = False\n",
    ),
    "tests/security/test_redaction_reconstruction.py": (
        "_run",
        b"    assert PERMANENT_REDACTION_ENABLED is False\n",
    ),
    "tests/security/test_redaction_verification_corpus.py": (
        "test_complete_reconstructed_sanitized_corpus_passes_automated_methods",
        b'    assert PERMANENT_REDACTION_ENABLED is False, "CORPUS_RELEASE_GATE_ENABLED"\n',
    ),
    "tests/integration/test_redaction_verification_pipeline.py": (
        "test_admitted_independent_ocr_recovers_source_and_rejects_unsafe_result",
        b"    assert PERMANENT_REDACTION_ENABLED is False\n",
    ),
}


def _gate_example(relative: str) -> tuple[bytes, bytes]:
    owner, false_line = _BUILD_GATE_EXAMPLES[relative]
    del owner
    true_line = false_line.replace(b"False", b"True")
    current = (original.ROOT / relative).read_bytes()
    if current.splitlines(keepends=True).count(true_line) == 1:
        current = current.replace(true_line, false_line, 1)
    assert current.splitlines(keepends=True).count(false_line) == 1
    return current, current.replace(false_line, true_line, 1)


def _gate_workspace(
    relative: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, bytes, dict[str, str]]:
    factory = cast(
        Callable[[Path, pytest.MonkeyPatch], Path], fixtures.workspace._get_wrapped_function()
    )
    path = factory(tmp_path, monkeypatch)
    previous, current = _gate_example(relative)
    provenance = original._provenance().copy()
    provenance[relative] = viewer._digest(previous)
    monkeypatch.setattr(original, "_provenance", lambda: provenance)
    evidence_path = path / "evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence["implementation"][relative] = provenance[relative]
    evidence_path.write_text(json.dumps(evidence))
    # Finish this synthetic fixture's preparation before recording any mock
    # observation. No actual accepted evidence is opened or changed here.
    (path / "guidance/guidance-01.json").unlink()
    original.prepare()
    provenance[original.HELPER] = viewer._digest((original.ROOT / original.HELPER).read_bytes())
    for slot in original.SLOTS:
        fixtures._record(path, monkeypatch, slot)
    monkeypatch.setattr(viewer, "_native", _native)
    return path, current, provenance


def _native(
    content: bytes, reference: bytes, session: viewer.RetainedSession, _path: Path
) -> dict[str, object]:
    request = replace(
        _verification_request(
            content,
            reference,
            tuple(session.evidence["affected_pages"]),
            session.evidence["page_count"],
            original._TOKENS,
            tuple(session.evidence["forbidden_stream_sha256"]),
        ),
        profile_id=VIEWER_VERIFICATION_PROFILE,
    )
    report = RedactionVerificationReport(
        request.fingerprint,
        request.candidate_sha256,
        tuple(
            VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)
            if method in AUTOMATED_METHODS
            else VerificationMethodResult(
                method, VerificationStatus.UNKNOWN, VerificationCode.MISSING
            )
            for method in VerificationMethod
        ),
    )
    return {
        "automated_passed": report.automated_passed,
        "release_ready": report.release_ready,
        "report": report.to_record(),
    }


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    factory = cast(
        Callable[[Path, pytest.MonkeyPatch], Path], fixtures.workspace._get_wrapped_function()
    )
    path = factory(tmp_path, monkeypatch)
    provenance = original._provenance()
    provenance[original.HELPER] = viewer._digest((original.ROOT / original.HELPER).read_bytes())
    monkeypatch.setattr(original, "_provenance", lambda: provenance)
    for slot in original.SLOTS:
        fixtures._record(path, monkeypatch, slot)
    monkeypatch.setattr(viewer, "_native", _native)
    return path


def _snapshot(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file() and viewer.FOLDER not in path.parts
    }


def test_upgrade_checks_unique_bytes_once_preserves_every_original_and_is_fast(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _snapshot(workspace)
    calls: list[bytes] = []

    def native(
        content: bytes, reference: bytes, session: viewer.RetainedSession, path: Path
    ) -> dict[str, object]:
        calls.append(content)
        return _native(content, reference, session, path)

    monkeypatch.setattr(viewer, "_native", native)
    viewer.verify()
    assert len(calls) == 2
    assert _snapshot(workspace) == before
    assert len(list((workspace / viewer.FOLDER).glob("*.json"))) == 11
    monkeypatch.setattr(
        viewer, "_native", lambda *_args: pytest.fail("repeated native verification")
    )
    viewer.status()
    viewer.verify()
    assert "BUILT-IN VIEWER CHECKS COMPLETE" in capsys.readouterr().out
    assert _snapshot(workspace) == before


@pytest.mark.parametrize(
    "kind",
    [
        "generation",
        "new_generation",
        "removed_generation",
        "packet",
        "guidance",
        "human_helper",
        "helper_code",
    ],
)
def test_material_change_cannot_be_excused_by_inspection_upgrade(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "helper_code":
        previous = original._provenance().copy()
        previous[original.HELPER] = "b" * 64
        monkeypatch.setattr(original, "_provenance", lambda: previous)
    elif kind in {"generation", "new_generation"}:
        previous = original._provenance()
        previous["synthetic-processing" if kind == "generation" else "new-generator"] = "b" * 64
        monkeypatch.setattr(original, "_provenance", lambda: previous)
    elif kind == "removed_generation":
        previous = original._provenance().copy()
        del previous["synthetic-processing"]
        monkeypatch.setattr(original, "_provenance", lambda: previous)
    elif kind == "packet":
        (workspace / "packet.pdf").write_bytes(b"DIFFERENT")
    elif kind == "guidance":
        path = workspace / "guidance/guidance-01.json"
        value = json.loads(path.read_bytes())
        value["pipeline_sha256"] = "b" * 64
        path.write_text(json.dumps(value))
    else:
        path = workspace / "attempts/W1-attempt-01.json"
        value = json.loads(path.read_bytes())
        value["helper_sha256"] = "b" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        viewer.retained_session()
    assert not (workspace / viewer.FOLDER).exists()


@pytest.mark.parametrize("added", _COMPOSITION_ADDITIONS)
def test_added_composition_requires_new_epoch_and_preserves_all_accepted_bytes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, added: str
) -> None:
    viewer.verify()
    original_session = viewer.retained_session()
    original_bytes = _snapshot(workspace)
    prior_receipts = {
        path: path.read_bytes() for path in (workspace / viewer.FOLDER).glob("*.json")
    }
    current = viewer._inputs()
    current[added] = "c" * 64
    monkeypatch.setattr(viewer, "_inputs", lambda: current)

    changed = viewer.retained_session()
    assert changed.generation_sha256 == original_session.generation_sha256
    assert changed.inputs_sha256 != original_session.inputs_sha256
    assert changed.observations == original_session.observations
    assert viewer._epoch(changed) is None
    viewer.verify()
    assert len(list((workspace / viewer.FOLDER).glob("epoch-*.json"))) == 2

    # Composition-only code is still an exact inspection input. A later edit
    # must invalidate every previous epoch rather than inherit its result.
    current[added] = "d" * 64
    assert viewer._epoch(viewer.retained_session()) is None
    viewer.verify()
    assert len(list((workspace / viewer.FOLDER).glob("epoch-*.json"))) == 3
    assert _snapshot(workspace) == original_bytes
    assert all(path.read_bytes() == content for path, content in prior_receipts.items())


@pytest.mark.parametrize("added", _COMPOSITION_ADDITIONS)
@pytest.mark.parametrize("mutation", ("change", "remove"))
def test_composition_name_in_original_generation_cannot_be_excused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, added: str, mutation: str
) -> None:
    evidence_path = workspace / "evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence["implementation"][added] = "c" * 64
    evidence_path.write_text(json.dumps(evidence))
    current = viewer._inputs()
    if mutation == "change":
        current[added] = "d" * 64
    monkeypatch.setattr(viewer, "_inputs", lambda: current)
    with pytest.raises(ValueError, match="PWS0606_GENERATION_CHANGED"):
        viewer.retained_session()
    assert not (workspace / viewer.FOLDER).exists()


def test_unlisted_composition_addition_is_a_generation_change(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = viewer._inputs()
    current["src/paperworkshell/pdf/redaction_export_future.py"] = "c" * 64
    monkeypatch.setattr(viewer, "_inputs", lambda: current)
    with pytest.raises(ValueError, match="PWS0606_GENERATION_CHANGED"):
        viewer.retained_session()
    assert not (workspace / viewer.FOLDER).exists()


@pytest.mark.parametrize("relative", _BUILD_GATE_EXAMPLES)
def test_exact_build_gate_flip_preserves_generation_but_requires_new_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    workspace, enabled, provenance = _gate_workspace(relative, tmp_path, monkeypatch)
    viewer.verify()
    old_session = viewer.retained_session()
    originals = _snapshot(workspace)
    receipts = {path: path.read_bytes() for path in (workspace / viewer.FOLDER).glob("*.json")}
    provenance[relative] = viewer._digest(enabled)
    monkeypatch.setattr(viewer, "_gate_input_bytes", lambda _name: enabled, raising=False)
    session = viewer.retained_session()
    assert session.inputs_sha256 != old_session.inputs_sha256
    assert session.generation_sha256 == old_session.generation_sha256
    assert session.observations == old_session.observations
    assert session.evidence["feature_enabled"] is False
    assert viewer._epoch(session) is None
    viewer.verify()
    assert len(list((workspace / viewer.FOLDER).glob("epoch-*.json"))) == 2
    assert _snapshot(workspace) == originals
    assert all(path.read_bytes() == content for path, content in receipts.items())


@pytest.mark.parametrize("relative", _BUILD_GATE_EXAMPLES)
@pytest.mark.parametrize("mutation", ("other_byte", "old_digest", "current_digest", "duplicate"))
def test_build_gate_flip_never_excuses_unauthenticated_bytes(
    monkeypatch: pytest.MonkeyPatch, relative: str, mutation: str
) -> None:
    old, enabled = _gate_example(relative)
    old_digest, current_digest = viewer._digest(old), viewer._digest(enabled)
    if mutation == "other_byte":
        enabled += b"\n# Changed code outside the reviewed build switch.\n"
        current_digest = viewer._digest(enabled)
    elif mutation == "old_digest":
        old_digest = "b" * 64
    elif mutation == "current_digest":
        current_digest = "b" * 64
    else:
        enabled += _BUILD_GATE_EXAMPLES[relative][1].replace(b"False", b"True")
        current_digest = viewer._digest(enabled)
    monkeypatch.setattr(viewer, "_gate_input_bytes", lambda _name: enabled)
    assert not viewer._build_gate_only_change(relative, old_digest, current_digest)


@pytest.mark.parametrize("relative", _BUILD_GATE_EXAMPLES)
def test_build_gate_flip_must_be_an_actual_assignment_or_owner_assert(
    monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    owner, false_line = _BUILD_GATE_EXAMPLES[relative]
    # The exact permitted line in a string is not an AST assignment/assert.
    old = (
        b'"""\n' + false_line + b'"""\n'
        if owner is None
        else f"def {owner}():\n".encode() + b'    """\n' + false_line + b'    """\n'
    )
    enabled = old.replace(false_line, false_line.replace(b"False", b"True"), 1)
    monkeypatch.setattr(viewer, "_gate_input_bytes", lambda _name: enabled)
    assert not viewer._build_gate_only_change(
        relative, viewer._digest(old), viewer._digest(enabled)
    )


@pytest.mark.parametrize("relative", _BUILD_GATE_EXAMPLES)
def test_build_gate_flip_must_have_the_reviewed_annotation_or_function(
    monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    old, _enabled = _gate_example(relative)
    owner, false_line = _BUILD_GATE_EXAMPLES[relative]
    if owner is None:
        old = old.replace(
            b"PERMANENT_REDACTION_ENABLED: Final", b"PERMANENT_REDACTION_ENABLED: bool"
        )
        enabled = old.replace(
            b"PERMANENT_REDACTION_ENABLED: bool = False",
            b"PERMANENT_REDACTION_ENABLED: bool = True",
        )
    else:
        old = old.replace(f"def {owner}(".encode(), b"def unreviewed_owner(", 1)
        enabled = old.replace(false_line, false_line.replace(b"False", b"True"), 1)
    monkeypatch.setattr(viewer, "_gate_input_bytes", lambda _name: enabled)
    assert not viewer._build_gate_only_change(
        relative, viewer._digest(old), viewer._digest(enabled)
    )


@pytest.mark.parametrize("relative", _BUILD_GATE_EXAMPLES)
def test_build_gate_name_without_original_provenance_is_not_admitted(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    _old, enabled = _gate_example(relative)
    current = viewer._inputs()
    current[relative] = viewer._digest(enabled)
    monkeypatch.setattr(viewer, "_inputs", lambda: current)
    monkeypatch.setattr(
        viewer, "_gate_input_bytes", lambda _name: pytest.fail("unbound source opened")
    )
    with pytest.raises(ValueError, match="PWS0606_GENERATION_CHANGED"):
        viewer.retained_session()


def test_build_gate_edits_cannot_target_another_input_or_reverse_a_released_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "src/paperworkshell/domain/redaction.py"
    old, enabled = _gate_example(relative)
    monkeypatch.setattr(viewer, "_gate_input_bytes", lambda _name: old)
    assert not viewer._build_gate_only_change(
        relative, viewer._digest(enabled), viewer._digest(old)
    )
    assert not viewer._build_gate_only_change(
        "new-input", viewer._digest(old), viewer._digest(enabled)
    )
    assert not viewer._build_gate_only_change(relative, None, viewer._digest(enabled))


def _packet_preparation_imports() -> set[str]:
    # Inspect imports without importing or executing the private compositions.
    # Package initializers and relative/from-import targets are included, so an
    # indirect local dependency cannot be hidden by moving it into __init__.py.
    local: dict[str, Path] = {}
    for relative in ("src/paperworkshell", "tools", "tests"):
        base = original.ROOT / ("src" if relative.startswith("src/") else "")
        for path in (original.ROOT / relative).rglob("*.py"):
            parts = list(path.relative_to(base).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            local[".".join(parts)] = path
    pending = ["tools.pws0606_redaction_verification"]
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reached or name not in local:
            continue
        reached.add(name)
        path = local[name]
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        targets = {".".join(name.split(".")[:index]) for index in range(1, len(name.split(".")))}
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base_name = (
                    importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                    if node.level
                    else node.module or ""
                )
                targets.add(base_name)
                targets.update(
                    f"{base_name}.{alias.name}" for alias in node.names if alias.name != "*"
                )
        pending.extend(target for target in targets if target in local and target not in reached)
    return reached


def test_composition_additions_are_outside_packet_preparation_import_closure() -> None:
    additions = {
        path.removeprefix("src/").removesuffix(".py").replace("/", ".")
        for path in _COMPOSITION_ADDITIONS
    }
    reached = _packet_preparation_imports()
    assert additions.isdisjoint(reached), sorted(additions & reached)


def test_reviewed_build_gate_statements_do_not_control_packet_pixel_generation() -> None:
    def functions(relative: str) -> dict[str, ast.FunctionDef]:
        tree = ast.parse((original.ROOT / relative).read_text(encoding="utf-8"))
        return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    domain = ast.parse(
        (original.ROOT / "src/paperworkshell/domain/redaction.py").read_text(encoding="utf-8")
    )
    # The pure mark model only defines this build switch; no geometry,
    # validation or transformation in that module reads it.
    assert not any(
        isinstance(node, ast.Name)
        and node.id == "PERMANENT_REDACTION_ENABLED"
        and isinstance(node.ctx, ast.Load)
        for node in ast.walk(domain)
    )
    helper = functions(original.HELPER)["_generate_packet"]
    corpus = functions("tests/security/test_redaction_verification_corpus.py")["_prepare"]
    for entry in (helper, corpus):
        names = {
            node.id
            for node in ast.walk(entry)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        assert "PERMANENT_REDACTION_ENABLED" not in names
        assert "_run" not in names
        assert not any(name.startswith("test_") for name in names)
    # The frozen helper calls _prepare directly, not the security test's _run
    # wrapper where the reconstruction assertion lives. The integration file
    # is provenance-only, not even imported by packet preparation.
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_prepare"
        for node in ast.walk(helper)
    )
    assert (
        "tests.integration.test_redaction_verification_pipeline"
        not in _packet_preparation_imports()
    )


def test_changed_saved_file_or_observation_cannot_pass_from_old_receipt(workspace: Path) -> None:
    viewer.verify()
    (workspace / "resaves/W1-resaved.pdf").write_bytes(b"CHANGED SAVED BYTES")
    session = viewer.retained_session()
    assert session.observations["W1"] is None
    assert all(session.observations[slot] is not None for slot in original.SLOTS if slot != "W1")
    assert _snapshot(workspace)["resaves/W1-resaved.pdf"] == b"CHANGED SAVED BYTES"


def test_backend_change_requires_new_automated_epoch_without_losing_human_facts(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer.verify()
    before = _snapshot(workspace)
    current = viewer._inputs()
    current["src/paperworkshell/pdf/redaction_verification.py"] = "b" * 64
    monkeypatch.setattr(viewer, "_inputs", lambda: current)
    assert viewer._epoch(viewer.retained_session()) is None
    viewer.verify()
    assert len(list((workspace / viewer.FOLDER).glob("epoch-*.json"))) == 2
    assert _snapshot(workspace) == before


@pytest.mark.parametrize(
    "mutation", ["profile", "future", "candidate", "forged_pass", "observation"]
)
def test_unknown_forged_or_foreign_receipt_is_never_a_pass(workspace: Path, mutation: str) -> None:
    viewer.verify()
    path = workspace / viewer.FOLDER / "W1-saved-01.json"
    value: dict[str, Any] = json.loads(path.read_bytes())
    if mutation == "profile":
        value["profile_id"] = "future-viewer"
    elif mutation == "future":
        value["schema_version"] = 2
    elif mutation == "candidate":
        value["proof"]["report"]["candidate_sha256"] = "b" * 64
    elif mutation == "forged_pass":
        value["proof"]["release_ready"] = True
    else:
        value["observation_sha256"] = "b" * 64
    path.write_text(json.dumps(value))
    session = viewer.retained_session()
    epoch = viewer._epoch(session)
    assert epoch is not None
    if mutation == "observation":
        assert (
            viewer._receipt("W1", "saved", workspace / "resaves/W1.pdf", session, epoch[1]) is None
        )
    else:
        with pytest.raises((ValueError, PdfExportError)):
            viewer._receipt("W1", "saved", workspace / "resaves/W1.pdf", session, epoch[1])


def test_legacy_entrypoint_uses_completed_session_without_any_human_prompt(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer.verify()
    monkeypatch.setattr("builtins.input", lambda *_args: pytest.fail("unnecessary human prompt"))
    assert dev.COMMANDS["pws0606-redaction-test"].arguments == (
        "python",
        "-m",
        "tools.pws0606_viewer_resave",
    )
    assert viewer.main(["status"]) == 0


def test_native_input_mutation_cannot_append_a_successful_receipt(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate(
        content: bytes, reference: bytes, session: viewer.RetainedSession, path: Path
    ) -> dict[str, object]:
        result = _native(content, reference, session, path)
        (workspace / "resaves/W1.pdf").write_bytes(b"CHANGED DURING VERIFICATION")
        return result

    monkeypatch.setattr(viewer, "_native", mutate)
    with pytest.raises(ValueError):
        viewer.verify()
    assert not list((workspace / viewer.FOLDER).glob("W1-saved-*.json"))


def test_receipt_write_failure_preserves_human_facts_and_recovers(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _snapshot(workspace)
    append = original._append

    def full(folder: str, stem: str, value: dict[str, object]) -> None:
        if stem == "W1-saved":
            raise OSError("synthetic disk full")
        append(folder, stem, value)

    monkeypatch.setattr(original, "_append", full)
    assert viewer.main(["verify"]) == 1
    assert _snapshot(workspace) == before
    assert not list((workspace / viewer.FOLDER).glob("W1-saved-*.json"))
    monkeypatch.setattr(original, "_append", append)
    assert viewer.main(["verify"]) == 0
    assert _snapshot(workspace) == before


def test_missing_observation_never_becomes_a_pass(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace / "attempts/A1-attempt-01.json").unlink()
    viewer.verify()
    assert (
        f"A1. {original.SLOTS['A1']}: HUMAN EVIDENCE MISSING OR CHANGED" in capsys.readouterr().out
    )
    assert not list((workspace / viewer.FOLDER).glob("A1-*.json"))
