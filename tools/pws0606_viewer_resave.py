"""Verify retained synthetic viewer saves with explicit inspection-only lineage.

This never regenerates the packet, changes a human observation, or grants export
authority. Old canonical-profile failures remain intact beside the new receipts.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from paperworkshell.domain.redaction_verification import (
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    validate_verification_report,
)
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.security.test_redaction_reconstruction import _Control
from tests.security.test_redaction_verification_corpus import _limits, _verification_request
from tools import pws0606_redaction_verification as original

SELF = "tools/pws0606_viewer_resave.py"
ENTRY_POINT = "tools/dev.py"
FOLDER = "viewer-verification"
# Inspection changes may renew automated evidence. Any other preparation input
# change fails before accepting the original human/artifact bindings.
_INSPECTION_INPUTS = frozenset(
    {
        SELF,
        ENTRY_POINT,
        "src/paperworkshell/domain/redaction_verification.py",
        "src/paperworkshell/pdf/redaction_verification.py",
        "src/paperworkshell/pdf/redaction_verification_structure.py",
        "src/paperworkshell/pdf/redaction_verification_resources.py",
        "src/paperworkshell/pdf/redaction_verification_linearized.py",
        "src/paperworkshell/pdf/redaction_verification_viewer_metadata.py",
    }
)
# The frozen preparation helper does not import these later private export
# compositions (checked transitively in the lineage tests). Their addition to
# the original helper's broad package glob cannot alter the already accepted
# packet. This exception is additions-only: a file present in the preparation
# inventory keeps its original generation binding, even with one of these names.
# Current hashes still enter inputs_sha256 and require a new inspection epoch.
_COMPOSITION_ONLY_ADDITIONS = frozenset(
    {
        "src/paperworkshell/domain/redaction_export.py",
        "src/paperworkshell/domain/redaction_export_candidate.py",
        "src/paperworkshell/pdf/redaction_export.py",
        "src/paperworkshell/pdf/redaction_export_ocr.py",
    }
)
# Enabling an independently approved build changes these four policy tokens,
# not the accepted pixel-generation algorithm. Only the named statement's exact
# False -> True edit may be normalized, and all other original bytes must still
# authenticate against preparation. The full current hashes remain inspection
# inputs. This is evidence lineage, never release or publication authority.
_BUILD_GATE_INPUTS: dict[str, tuple[str | None, bytes]] = {
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


def _hash_record(value: object) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _inputs() -> dict[str, str]:
    return {
        **original._provenance(),
        SELF: _digest((original.ROOT / SELF).read_bytes()),
        ENTRY_POINT: _digest((original.ROOT / ENTRY_POINT).read_bytes()),
    }


def _gate_input_bytes(relative: str) -> bytes | None:
    path = original.ROOT / relative
    try:
        if path.is_symlink() or path.is_junction() or not path.is_file():
            return None
        with path.open("rb") as source:
            content = source.read(262145)
        return content if len(content) <= 262144 else None
    except OSError:
        return None


def _build_gate_only_change(relative: str, previous: object, current: object) -> bool:
    """Authenticate one closed build-policy flip without excusing any other byte."""
    rule = _BUILD_GATE_INPUTS.get(relative)
    if (
        rule is None
        or type(previous) is not str
        or type(current) is not str
        or original._SHA.fullmatch(previous) is None
        or original._SHA.fullmatch(current) is None
    ):
        return False
    owner, false_line = rule
    true_line = false_line.replace(b"False", b"True")
    content = _gate_input_bytes(relative)
    if content is None or _digest(content) != current:
        return False
    lines = content.splitlines(keepends=True)
    if lines.count(true_line) != 1:
        return False
    if _digest(content.replace(true_line, false_line, 1)) != previous:
        return False
    try:
        tree = ast.parse(content.decode("utf-8"))
    except UnicodeError, SyntaxError:
        return False
    node: ast.stmt
    if owner is None:
        assignments = [
            item
            for item in tree.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == "PERMANENT_REDACTION_ENABLED"
        ]
        if len(assignments) != 1:
            return False
        assignment = assignments[0]
        if (
            not isinstance(assignment.annotation, ast.Name)
            or assignment.annotation.id != "Final"
            or not isinstance(assignment.value, ast.Constant)
            or assignment.value.value is not True
            or sum(
                isinstance(item, ast.Name)
                and item.id == "PERMANENT_REDACTION_ENABLED"
                and isinstance(item.ctx, ast.Store)
                for item in ast.walk(tree)
            )
            != 1
        ):
            return False
        node = assignment
    else:
        functions = [
            item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == owner
        ]
        assertions = [
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.Assert)
            and any(
                isinstance(name, ast.Name) and name.id == "PERMANENT_REDACTION_ENABLED"
                for name in ast.walk(item.test)
            )
        ]
        if len(functions) != 1 or len(assertions) != 1 or assertions[0] not in functions[0].body:
            return False
        assertion = assertions[0]
        condition = assertion.test
        if (
            not isinstance(condition, ast.Compare)
            or not isinstance(condition.left, ast.Name)
            or condition.left.id != "PERMANENT_REDACTION_ENABLED"
            or len(condition.ops) != 1
            or not isinstance(condition.ops[0], ast.Is)
            or len(condition.comparators) != 1
            or not isinstance(condition.comparators[0], ast.Constant)
            or condition.comparators[0].value is not True
        ):
            return False
        node = assertion
    return node.lineno == node.end_lineno == lines.index(true_line) + 1


@dataclass(frozen=True)
class RetainedSession:
    evidence: dict[str, Any]
    binding: str
    environment: dict[str, Any]
    inputs_sha256: str
    generation_sha256: str
    observations: dict[str, tuple[dict[str, Any], str] | None]


def retained_session() -> RetainedSession:
    """Validate unchanged generation, original bytes and each historical observation."""
    evidence_path = original.WORKSPACE / "evidence.json"
    evidence = original._read(evidence_path)
    binding = _digest(evidence_path.read_bytes())
    if (
        evidence.get("schema_version") != 1
        or evidence.get("task_id") != "PWS-0606"
        or evidence.get("synthetic_only") is not True
        or evidence.get("feature_enabled") is not False
        or type(evidence.get("implementation")) is not dict
    ):
        original._fail("PWS0606_PREPARATION_INVALID")
    previous, current = evidence["implementation"], _inputs()
    if (
        set(previous) - set(current)
        or set(current) - set(previous) - _INSPECTION_INPUTS - _COMPOSITION_ONLY_ADDITIONS
        or any(
            previous[key] != current[key]
            and not _build_gate_only_change(key, previous[key], current[key])
            for key in previous
            if key not in _INSPECTION_INPUTS and key != original.HELPER
        )
    ):
        original._fail("PWS0606_GENERATION_CHANGED")
    for name in ("packet.pdf", "reference.pdf", "unsafe-cover.pdf"):
        if original._identity(original.WORKSPACE / name) != evidence["artifacts"][name]:
            original._fail("PWS0606_ARTIFACT_CHANGED")
    original._query()
    guidance = original._guidance(evidence, binding)
    if guidance is None:
        original._fail("PWS0606_GUIDANCE_CHANGED")
    if current[original.HELPER] != guidance["helper_sha256"]:
        original._fail("PWS0606_GUIDANCE_CHANGED")
    environment, _ = original._environment()
    observations: dict[str, tuple[dict[str, Any], str] | None] = {}
    for slot in original.SLOTS:
        observed = original._observation(slot, evidence, binding, environment)
        if observed is None or observed["result"] != "OBSERVED":
            observations[slot] = None
            continue
        if observed.get("helper_sha256") != guidance["helper_sha256"]:
            original._fail("PWS0606_OBSERVATION_CHANGED")
        path = original._records("attempts", slot + "-attempt")[-1]
        observations[slot] = observed, _digest(path.read_bytes())
    generation = {
        key: guidance["helper_sha256"] if key == original.HELPER else value
        for key, value in previous.items()
        if key not in _INSPECTION_INPUTS
    }
    return RetainedSession(
        evidence,
        binding,
        environment,
        _hash_record(current),
        _hash_record(generation),
        observations,
    )


def _proof_valid(proof: object, identity: dict[str, object], session: RetainedSession) -> bool:
    if type(proof) is not dict or set(proof) != {"automated_passed", "release_ready", "report"}:
        original._fail("PWS0606_PROOF_INVALID")
    reference = session.evidence["artifacts"]["reference.pdf"]
    digest, byte_size = identity["sha256"], identity["byte_size"]
    if type(digest) is not str or type(byte_size) is not int:
        original._fail("PWS0606_IDENTITY_INVALID")
    request = replace(
        _verification_request(
            b"_",
            b"_",
            tuple(session.evidence["affected_pages"]),
            session.evidence["page_count"],
            original._TOKENS,
            tuple(session.evidence["forbidden_stream_sha256"]),
        ),
        candidate_sha256=digest,
        candidate_byte_size=byte_size,
        reference_sha256=reference["sha256"],
        reference_byte_size=reference["byte_size"],
        profile_id=VIEWER_VERIFICATION_PROFILE,
    )
    report = RedactionVerificationReport.from_record(proof["report"])
    validate_verification_report(request, report)
    if (
        type(proof["automated_passed"]) is not bool
        or type(proof["release_ready"]) is not bool
        or proof["automated_passed"] != report.automated_passed
        or proof["release_ready"] != report.release_ready
    ):
        original._fail("PWS0606_PROOF_INVALID")
    return report.automated_passed


def _epoch(session: RetainedSession) -> tuple[dict[str, Any], str] | None:
    paths = original._records(FOLDER, "epoch")
    if not paths:
        return None
    value = original._read(paths[-1])
    if (
        set(value)
        != {
            "schema_version",
            "profile_id",
            "preparation_sha256",
            "inputs_sha256",
            "generation_sha256",
            "artifacts",
            "canonical_proof",
            "verified_at_utc",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["profile_id"] != VIEWER_VERIFICATION_PROFILE
    ):
        original._fail("PWS0606_EPOCH_INVALID")
    if (
        value["preparation_sha256"] != session.binding
        or value["inputs_sha256"] != session.inputs_sha256
        or value["generation_sha256"] != session.generation_sha256
        or value["artifacts"] != session.evidence["artifacts"]
    ):
        return None
    if not original._validated_proof(
        value["canonical_proof"], session.evidence["artifacts"]["packet.pdf"], session.evidence
    ):
        original._fail("PWS0606_CANONICAL_RECHECK_FAILED")
    return value, _digest(paths[-1].read_bytes())


def _receipt(
    slot: str, copy: str, path: Path, session: RetainedSession, epoch_sha256: str
) -> bool | None:
    observed = session.observations[slot]
    paths = original._records(FOLDER, f"{slot}-{copy}")
    if observed is None or not paths:
        return None
    value = original._read(paths[-1])
    if (
        set(value)
        != {
            "schema_version",
            "profile_id",
            "slot",
            "copy",
            "epoch_sha256",
            "preparation_sha256",
            "observation_sha256",
            "environment_source_sha256",
            "identity",
            "proof",
            "verified_at_utc",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["profile_id"] != VIEWER_VERIFICATION_PROFILE
        or value["slot"] != slot
        or value["copy"] != copy
    ):
        original._fail("PWS0606_RECEIPT_INVALID")
    identity = original._identity(path)
    if (
        identity is None
        or value["identity"] != identity
        or value["preparation_sha256"] != session.binding
        or value["epoch_sha256"] != epoch_sha256
        or value["observation_sha256"] != observed[1]
        or value["environment_source_sha256"] != _hash_record(session.environment["slots"][slot])
    ):
        return None
    passed = _proof_valid(value["proof"], identity, session)
    return passed if original._identity(path) == identity else None


def status() -> None:
    session = retained_session()
    epoch = _epoch(session)
    complete = True
    for slot, reader in original.SLOTS.items():
        results = [
            _receipt(slot, name, path, session, epoch[1]) if epoch is not None else None
            for name, path, _field in original._copies(slot)
        ]
        state = (
            "PASS"
            if all(value is True for value in results)
            else ("AUTOMATED CHECK FAILED" if False in results else "AUTOMATED CHECKS PENDING")
        )
        if session.observations[slot] is None:
            state = "HUMAN EVIDENCE MISSING OR CHANGED"
        complete &= state == "PASS"
        print(f"{slot}. {reader}: {state}")
    print(
        "RETAINED HUMAN OBSERVATIONS AND BUILT-IN VIEWER CHECKS COMPLETE; OCR/release evidence is separate."
        if complete
        else "PENDING; retained observations and earlier receipts are preserved."
    )


def _native(
    content: bytes, reference: bytes, session: RetainedSession, scratch: Path
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
    report = verify_redaction(
        BytesIO(content),
        BytesIO(reference),
        request,
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=original._TOKENS,
        forbidden_stream_sha256=tuple(session.evidence["forbidden_stream_sha256"]),
    )
    return {
        "automated_passed": report.automated_passed,
        "release_ready": report.release_ready,
        "report": report.to_record(),
    }


def verify() -> None:
    session = retained_session()
    reference = (original.WORKSPACE / "reference.pdf").read_bytes()
    epoch = _epoch(session)
    if epoch is None:
        print("Rechecking the unchanged packet with the strict canonical profile.")
        with TemporaryDirectory(prefix="canonical-recheck-", dir=original.WORKSPACE) as temporary:
            proof = original._verify(
                (original.WORKSPACE / "packet.pdf").read_bytes(),
                reference,
                session.evidence,
                Path(temporary),
            )
        if (
            not original._validated_proof(
                proof, session.evidence["artifacts"]["packet.pdf"], session.evidence
            )
            or retained_session().inputs_sha256 != session.inputs_sha256
        ):
            original._fail("PWS0606_CANONICAL_RECHECK_FAILED")
        original._append(
            FOLDER,
            "epoch",
            {
                "schema_version": 1,
                "profile_id": VIEWER_VERIFICATION_PROFILE,
                "preparation_sha256": session.binding,
                "inputs_sha256": session.inputs_sha256,
                "generation_sha256": session.generation_sha256,
                "artifacts": session.evidence["artifacts"],
                "canonical_proof": proof,
                "verified_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        epoch = _epoch(session)
        assert epoch is not None
    cache: dict[str, dict[str, object]] = {}  # At most the ten fixed slot/copy identities.
    for slot in original.SLOTS:
        observation = session.observations[slot]
        if observation is None:
            continue
        for copy, path, _field in original._copies(slot):
            if _receipt(slot, copy, path, session, epoch[1]) is True:
                continue
            identity, content = original._identity(path), original._saved_copy(path)
            if identity is None or content is None or _digest(content) != identity["sha256"]:
                original._fail("PWS0606_ARTIFACT_CHANGED")
            key = str(identity["sha256"])
            if key not in cache:
                print(f"Checking original saved bytes: {slot} {copy}.")
                with TemporaryDirectory(
                    prefix="viewer-recheck-", dir=original.WORKSPACE
                ) as temporary:
                    cache[key] = _native(content, reference, session, Path(temporary))
            proof = cache[key]
            _proof_valid(proof, identity, session)
            current = retained_session()
            if (
                original._identity(path) != identity
                or current.inputs_sha256 != session.inputs_sha256
                or current.observations[slot] != observation
                or current.binding != session.binding
            ):
                original._fail("PWS0606_INPUT_CHANGED")
            original._append(
                FOLDER,
                f"{slot}-{copy}",
                {
                    "schema_version": 1,
                    "profile_id": VIEWER_VERIFICATION_PROFILE,
                    "slot": slot,
                    "copy": copy,
                    "epoch_sha256": epoch[1],
                    "preparation_sha256": session.binding,
                    "observation_sha256": observation[1],
                    "environment_source_sha256": _hash_record(session.environment["slots"][slot]),
                    "identity": identity,
                    "proof": proof,
                    "verified_at_utc": datetime.now(UTC).isoformat(),
                },
            )
    status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("status", "verify"), nargs="?", default="status")
    selected = parser.parse_args(argv)
    try:
        verify() if selected.mode == "verify" else status()
        return 0
    except Exception:
        print(
            "ERROR [PWS0606_VIEWER_RECHECK_FAILED]; preserve the packet, observations and receipts."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
