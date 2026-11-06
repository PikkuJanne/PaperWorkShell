"""Stable local synthetic-only preparation and human viewer/resave recording.

This development harness never accepts a user document and never enables the
product feature. Only the independently approved PWS-0608 gate can do that.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Never, cast
from uuid import uuid4

import pikepdf

from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationReport,
    validate_verification_report,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.security.test_redaction_reconstruction import _Control
from tests.security.test_redaction_verification_corpus import (
    _CASES,
    _FIXTURES,
    _digest,
    _limits,
    _prepare,
    _verification_request,
)

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "output" / "pws0606-verification-v1"
ENVIRONMENT = ROOT / "docs/development/human-test-environment-v1.json"
HELPER = "tools/pws0606_redaction_verification.py"
PROTOCOL = 2
MAX_RECORDS = 99
QUERY = "PWSRED"
KEEP_QUERY = "PWSKEEP"
_LEGACY_HELPER = "cdd9b96c04e7003170848d21ed536531fba64064e05196251b016146379da254"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,5}\Z")
SLOTS = {
    "W1": "Adobe desktop Reader/free/reduced mode",
    "W2": "Microsoft Edge built-in PDF viewer",
    "W3": "Google Chrome built-in PDF viewer",
    "W4": "Mozilla Firefox built-in PDF viewer",
    "A1": "Adobe Acrobat Reader for Android",
}
CHECKS = (
    "correct_reader",
    "all_pages_masks_and_visible_controls",
    "selection_copy_and_all_canary_searches",
    "no_layer_attachment_annotation_form_or_action_carriers",
    "saved_copy_reopened_and_all_checks_repeated",
    "second_saved_copy_reopened_and_all_checks_repeated",
)
_ACCEPTED = tuple(case for case in _CASES if case.expected_open == "accept")
_TOKENS = tuple(canary.token for case in _ACCEPTED for canary in case.canaries)
_PROVENANCE = (
    "pyproject.toml",
    "uv.lock",
    "tools/pws0606_redaction_verification.py",
    "tools/generate_synthetic_fixtures.py",
    "tools/redaction_corpus.py",
    "tests/fixtures/redaction/v1/canaries.json",
    "tests/security/test_redaction_verification_corpus.py",
    "tests/security/test_redaction_reconstruction.py",
    "tests/integration/test_redaction_verification_pipeline.py",
    "src/paperworkshell/domain/redaction_verification.py",
    "src/paperworkshell/pdf/redaction_reconstruction.py",
    "src/paperworkshell/pdf/redaction_sanitization.py",
    "src/paperworkshell/pdf/redaction_verification.py",
    "src/paperworkshell/pdf/redaction_verification_structure.py",
    "src/paperworkshell/pdf/redaction_verification_visual.py",
)


def _fail(code: str) -> Never:
    raise ValueError(code)


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        _fail("PWS0606_RECORD_INVALID")
    with path.open("rb") as source:
        content = source.read(262145)
    if len(content) > 262144:
        _fail("PWS0606_RECORD_INVALID")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("PWS0606_RECORD_INVALID")
            result[key] = value
        return result

    result = json.loads(content.decode("utf-8"), object_pairs_hook=unique)
    if type(result) is not dict:
        _fail("PWS0606_RECORD_INVALID")
    return cast(dict[str, Any], result)


def _write(path: Path, value: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(value, destination, indent=2, sort_keys=True)
        destination.write("\n")


def _provenance() -> dict[str, str]:
    # Include the complete local contracts/adapters used by source generation,
    # geometry, resource planning, normalization, reconstruction and verification.
    # This is development evidence, not permission to load another repository.
    paths = set(_PROVENANCE)
    for package in ("domain", "pdf", "imaging"):
        paths.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src" / "paperworkshell" / package).rglob("*.py")
        )
    return {name: _digest((ROOT / name).read_bytes()) for name in sorted(paths)}


def _verify(
    content: bytes, reference: bytes, evidence: dict[str, Any], scratch: Path
) -> dict[str, object]:
    streams = tuple(evidence["forbidden_stream_sha256"])
    request = _verification_request(
        content,
        reference,
        tuple(evidence["affected_pages"]),
        evidence["page_count"],
        _TOKENS,
        streams,
    )
    report = verify_redaction(
        BytesIO(content),
        BytesIO(reference),
        request,
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=_TOKENS,
        forbidden_stream_sha256=streams,
    )
    return {
        "automated_passed": report.automated_passed,
        "release_ready": report.release_ready,
        "report": report.to_record(),
    }


def _generate_packet() -> dict[str, object]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if any(WORKSPACE.iterdir()):
        _fail("PWS0606_PARTIAL_PREPARATION")
    initial_provenance = _provenance()
    with TemporaryDirectory(prefix="prepare-", dir=WORKSPACE) as temporary:
        scratch = Path(temporary)
        case_workspace = scratch / "case"
        case_workspace.mkdir()
        affected: list[int] = []
        stream_hashes: set[str] = set()
        cases: list[dict[str, object]] = []
        with pikepdf.Pdf.new() as combined:
            for case in _ACCEPTED:
                prepared = _prepare(case, case_workspace)
                with pikepdf.Pdf.open(
                    BytesIO(prepared.candidate), attempt_recovery=False
                ) as document:
                    offset = len(combined.pages)
                    combined.pages.extend(document.pages)
                    affected.extend(offset + index for index in prepared.affected_pages)
                    cases.append(
                        {
                            "fixture_id": case.fixture_id,
                            "source_sha256": _digest(_FIXTURES[case.fixture_id]),
                            "first_page": offset + 1,
                            "page_count": len(document.pages),
                        }
                    )
                stream_hashes.update(prepared.forbidden_streams)
            output = BytesIO()
            combined.save(output, deterministic_id=True)
            reference = output.getvalue()
            page_count = len(combined.pages)
        candidate_path = scratch / "candidate.pdf"
        PikepdfRedactionSanitizer().sanitize(
            BytesIO(reference),
            candidate_path,
            scratch,
            RedactionSanitizationRequest(
                uuid4(), uuid4(), _digest(reference), len(reference), page_count
            ),
            _limits(),
            _Control(),
        )
        candidate = candidate_path.read_bytes()
        candidate_path.unlink()
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "task_id": "PWS-0606",
            "synthetic_only": True,
            "feature_enabled": False,
            "page_count": page_count,
            "affected_pages": affected,
            "forbidden_stream_sha256": sorted(stream_hashes),
            "cases": cases,
            "implementation": initial_provenance,
            "manual_slots": {slot: "NOT RUN" for slot in SLOTS},
        }
        verification = _verify(candidate, reference, evidence, scratch)
        if not verification["automated_passed"]:
            _fail("PWS0606_AUTOMATED_VERIFICATION_FAILED")
        if _provenance() != initial_provenance:
            _fail("PWS0606_IMPLEMENTATION_CHANGED")
        artifacts = {
            "packet.pdf": candidate,
            "reference.pdf": reference,
            "unsafe-cover.pdf": _FIXTURES["pdf-redaction-visual-cover"],
        }
        evidence["artifacts"] = {
            name: {"sha256": _digest(content), "byte_size": len(content)}
            for name, content in artifacts.items()
        }
        evidence["verification"] = verification
        for name, content in artifacts.items():
            with (WORKSPACE / name).open("xb") as destination:
                destination.write(content)
        _write(WORKSPACE / "evidence.json", evidence)
    (WORKSPACE / "resaves").mkdir(exist_ok=True)
    (WORKSPACE / "attempts").mkdir(exist_ok=True)
    return verification


def _bound_evidence() -> tuple[dict[str, Any], str]:
    evidence_path = WORKSPACE / "evidence.json"
    evidence = _read(evidence_path)
    if (
        evidence.get("schema_version") != 1
        or evidence.get("task_id") != "PWS-0606"
        or evidence.get("synthetic_only") is not True
        or evidence.get("feature_enabled") is not False
        or type(evidence.get("implementation")) is not dict
        or {key: value for key, value in evidence["implementation"].items() if key != HELPER}
        != {key: value for key, value in _provenance().items() if key != HELPER}
    ):
        _fail("PWS0606_IMPLEMENTATION_CHANGED")
    for name in ("packet.pdf", "reference.pdf", "unsafe-cover.pdf"):
        artifact = WORKSPACE / name
        expected = evidence["artifacts"][name]
        if (
            artifact.is_symlink()
            or type(expected["byte_size"]) is not int
            or not 1 <= expected["byte_size"] <= _limits().maximum_source_bytes
            or _identity(artifact) != expected
        ):
            _fail("PWS0606_ARTIFACT_CHANGED")
    return evidence, _digest(evidence_path.read_bytes())


def _identity(path: Path) -> dict[str, object] | None:
    """Hash current bytes without materializing a complete saved PDF."""
    import hashlib
    import stat

    try:
        before = path.lstat()
        limit = _limits().maximum_source_bytes
        if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
            return None
        if not 1 <= before.st_size <= limit:
            return None
        digest, count = hashlib.sha256(), 0
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > limit:
                    return None
                digest.update(block)
            after = os.fstat(source.fileno())
        final = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(
            getattr(before, key) != getattr(info, key) for info in (after, final) for key in fields
        ):
            return None
        return (
            {"sha256": digest.hexdigest(), "byte_size": count} if count == before.st_size else None
        )
    except OSError:
        return None


def _saved_copy(path: Path) -> bytes | None:
    limit = _limits().maximum_source_bytes
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= limit:
        return None
    with path.open("rb") as source:
        content = source.read(limit + 1)
    return content if 1 <= len(content) <= limit else None


def _copy_matches(path: Path, expected: object) -> bool:
    identity = _identity(path)
    return identity is not None and identity["sha256"] == expected


def _environment() -> tuple[dict[str, Any], str]:
    value = _read(ENVIRONMENT)
    if (
        set(value)
        != {
            "schema_version",
            "source_task_id",
            "source_evidence_sha256",
            "confirmed_at",
            "basis",
            "slots",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["source_task_id"] != "PWS-0506"
        or value["basis"] != "maintainer_confirmed_unchanged_from_last_accepted_test"
        or type(value["confirmed_at"]) is not str
        or re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value["confirmed_at"]) is None
        or type(value["source_evidence_sha256"]) is not str
        or _SHA.fullmatch(value["source_evidence_sha256"]) is None
        or type(value["slots"]) is not dict
        or set(value["slots"]) != set(SLOTS)
    ):
        _fail("PWS0606_ENVIRONMENT_INVALID")
    for slot, record in value["slots"].items():
        if (
            type(record) is not dict
            or set(record)
            != {"reader_version", "os_version", "source_attempt_sha256", "source_attempt"}
            or any(
                type(record[key]) is not str
                or not 1 <= len(record[key]) <= 64
                or _VERSION.fullmatch(record[key]) is None
                for key in ("reader_version", "os_version")
            )
            or type(record["source_attempt_sha256"]) is not str
            or _SHA.fullmatch(record["source_attempt_sha256"]) is None
            or type(record["source_attempt"]) is not str
            or len(record["source_attempt"]) > 256
            or re.fullmatch(
                r"output/pws0506-portal-compatibility/[a-z0-9_-]+/"
                + slot
                + r"-attempt-[0-9]{2}\.json",
                record["source_attempt"],
            )
            is None
        ):
            _fail("PWS0606_ENVIRONMENT_INVALID")
    return value, _digest(ENVIRONMENT.read_bytes())


def _query() -> str:
    if (
        len(_TOKENS) != 17
        or len(set(_TOKENS)) != 17
        or any(type(token) is not str or not token.startswith(QUERY) for token in _TOKENS)
    ):
        _fail("PWS0606_QUERY_PLAN_CHANGED")
    return QUERY


def _human_plan(evidence: dict[str, Any]) -> dict[str, object]:
    identifiers = [canary.canary_id for case in _ACCEPTED for canary in case.canaries]
    if len(identifiers) != 17 or len(identifiers) != len(set(identifiers)):
        _fail("PWS0606_QUERY_PLAN_CHANGED")
    return {
        "canary_ids": identifiers,
        "query_plan_sha256": _digest(
            json.dumps(
                [PROTOCOL, identifiers, _query(), KEEP_QUERY, False], separators=(",", ":")
            ).encode()
        ),
        "source_request_sha256": evidence["verification"]["report"]["request_sha256"],
    }


def _processing(evidence: dict[str, Any]) -> str:
    return _digest(
        json.dumps(
            {key: value for key, value in evidence["implementation"].items() if key != HELPER},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _pipeline() -> str:
    tree = ast.parse((ROOT / HELPER).read_text(encoding="utf-8"))
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.Assign, ast.AnnAssign))
        and (
            isinstance(node, ast.FunctionDef)
            and node.name in {"_verify", "_generate_packet"}
            or isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in {"_ACCEPTED", "_TOKENS"}
                for target in node.targets
            )
        )
    ]
    return _digest(
        ast.dump(ast.Module(body=selected, type_ignores=[]), include_attributes=False).encode()
    )


def _records(directory: str, prefix: str) -> list[Path]:
    folder = WORKSPACE / directory
    if folder.is_symlink() or folder.is_junction():
        _fail("PWS0606_RECORD_INVALID")
    records = sorted(islice(folder.glob(prefix + "-*.json"), MAX_RECORDS + 1))
    if len(records) > MAX_RECORDS or any(
        re.fullmatch(re.escape(prefix) + r"-[0-9]{2}\.json", path.name) is None for path in records
    ):
        _fail("PWS0606_ATTEMPT_LIMIT")
    return records


def _append(directory: str, prefix: str, value: dict[str, object]) -> Path:
    folder = WORKSPACE / directory
    if folder.is_symlink() or folder.is_junction():
        _fail("PWS0606_RECORD_INVALID")
    folder.mkdir(exist_ok=True)
    for number in range(1, MAX_RECORDS + 1):
        path = folder / f"{prefix}-{number:02}.json"
        if not path.exists():
            _write(path, value)
            return path
    _fail("PWS0606_ATTEMPT_LIMIT")
    raise AssertionError


def _validated_proof(
    proof: object,
    identity: dict[str, object],
    evidence: dict[str, Any],
) -> bool:
    if type(proof) is not dict or set(proof) != {"automated_passed", "release_ready", "report"}:
        _fail("PWS0606_PROOF_INVALID")
    if (
        type(identity) is not dict
        or set(identity) != {"sha256", "byte_size"}
        or type(identity["sha256"]) is not str
        or _SHA.fullmatch(identity["sha256"]) is None
        or type(identity["byte_size"]) is not int
        or identity["byte_size"] < 1
    ):
        _fail("PWS0606_PROOF_INVALID")
    record = RedactionVerificationReport.from_record(proof["report"])
    reference = evidence["artifacts"]["reference.pdf"]
    request = replace(
        _verification_request(
            b"_",
            b"_",
            tuple(evidence["affected_pages"]),
            evidence["page_count"],
            _TOKENS,
            tuple(evidence["forbidden_stream_sha256"]),
        ),
        candidate_sha256=identity["sha256"],
        candidate_byte_size=identity["byte_size"],
        reference_sha256=reference["sha256"],
        reference_byte_size=reference["byte_size"],
    )
    validate_verification_report(request, record)
    if (
        type(proof["automated_passed"]) is not bool
        or proof["automated_passed"] != record.automated_passed
        or type(proof["release_ready"]) is not bool
        or proof["release_ready"] != record.release_ready
    ):
        _fail("PWS0606_PROOF_INVALID")
    return record.automated_passed


def _guidance(evidence: dict[str, Any], binding: str) -> dict[str, Any] | None:
    records = _records("guidance", "guidance")
    if not records:
        return None
    value = _read(records[-1])
    if (
        set(value)
        != {
            "schema_version",
            "evidence_sha256",
            "processing_sha256",
            "pipeline_sha256",
            "artifacts",
            "helper_sha256",
            "environment_sha256",
            "proof",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != PROTOCOL
        or value["evidence_sha256"] != binding
        or value["processing_sha256"] != _processing(evidence)
        or value["pipeline_sha256"] != _pipeline()
        or value["artifacts"] != evidence["artifacts"]
        or not _validated_proof(value["proof"], evidence["artifacts"]["packet.pdf"], evidence)
        or value["proof"]["report"]["request_sha256"]
        != evidence["verification"]["report"]["request_sha256"]
    ):
        _fail("PWS0606_GUIDANCE_CHANGED")
    return value


def _ensure_guidance(
    evidence: dict[str, Any],
    binding: str,
    initial_proof: dict[str, object] | None = None,
) -> None:
    _query()
    _environment_value, environment_hash = _environment()
    current_helper = _digest((ROOT / HELPER).read_bytes())
    previous = _guidance(evidence, binding)
    if previous is not None and (
        previous["helper_sha256"] == current_helper
        and previous["environment_sha256"] == environment_hash
    ):
        return
    if previous is None and evidence["implementation"].get(HELPER) not in {
        current_helper,
        _LEGACY_HELPER,
    }:
        _fail("PWS0606_GUIDANCE_CHANGED")
    proof = initial_proof
    if proof is None:
        print(
            "Checking the existing synthetic packet once; its files and observations stay in place."
        )
        with TemporaryDirectory(prefix="guidance-", dir=WORKSPACE) as temporary:
            proof = _verify(
                (WORKSPACE / "packet.pdf").read_bytes(),
                (WORKSPACE / "reference.pdf").read_bytes(),
                evidence,
                Path(temporary),
            )
    if (
        not _validated_proof(proof, evidence["artifacts"]["packet.pdf"], evidence)
        or cast(dict[str, Any], proof["report"])["request_sha256"]
        != evidence["verification"]["report"]["request_sha256"]
        or _bound_evidence()[1] != binding
    ):
        _fail("PWS0606_GUIDANCE_VERIFICATION_FAILED")
    _append(
        "guidance",
        "guidance",
        {
            "schema_version": PROTOCOL,
            "evidence_sha256": binding,
            "processing_sha256": _processing(evidence),
            "pipeline_sha256": _pipeline(),
            "artifacts": evidence["artifacts"],
            "helper_sha256": current_helper,
            "environment_sha256": environment_hash,
            "proof": proof,
        },
    )


def _session() -> tuple[dict[str, Any], str, dict[str, Any], str]:
    evidence, binding = _bound_evidence()
    _query()
    environment, environment_hash = _environment()
    guidance = _guidance(evidence, binding)
    if (
        guidance is None
        or guidance["helper_sha256"] != _digest((ROOT / HELPER).read_bytes())
        or guidance["environment_sha256"] != environment_hash
    ):
        _fail("PWS0606_PREPARE_REQUIRED")
    return evidence, binding, environment, environment_hash


def prepare() -> None:
    proof = _generate_packet() if not (WORKSPACE / "evidence.json").exists() else None
    evidence, binding = _bound_evidence()
    _ensure_guidance(evidence, binding, proof)
    status()


def _copies(slot: str) -> tuple[tuple[str, Path, str], ...]:
    return (
        ("saved", WORKSPACE / "resaves" / f"{slot}.pdf", "saved_sha256"),
        ("resaved", WORKSPACE / "resaves" / f"{slot}-resaved.pdf", "resaved_sha256"),
    )


def _observation(
    slot: str, evidence: dict[str, Any], binding: str, environment: dict[str, Any]
) -> dict[str, Any] | None:
    records = _records("attempts", slot + "-attempt")
    if not records:
        return None
    value = _read(records[-1])
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, PROTOCOL}:
        _fail("PWS0606_RECORD_INVALID")
    if value.get("slot") != slot or value.get("operator") != "human":
        _fail("PWS0606_RECORD_INVALID")
    if value.get("evidence_sha256") != binding:
        return None
    if value.get("result") not in {"PASS", "OBSERVED", "FAIL"}:
        _fail("PWS0606_RECORD_INVALID")
    if value.get("result") == "FAIL":
        return value
    if (
        type(value.get("checks")) is not dict
        or set(value["checks"]) != set(CHECKS)
        or any(value["checks"][check] is not True for check in CHECKS)
        or any(not _copy_matches(path, value.get(field)) for _name, path, field in _copies(slot))
    ):
        return None
    known = environment["slots"][slot]
    if (
        value.get("reader_version") != known["reader_version"]
        or value.get("os_version") != known["os_version"]
    ):
        return None
    if version == PROTOCOL and (
        value.get("processing_sha256") != _processing(evidence)
        or value.get("environment_source") != known
        or value.get("protocol_version") != PROTOCOL
        or any(value.get(key) != item for key, item in _human_plan(evidence).items())
    ):
        return None
    return value


def _receipt(
    slot: str,
    copy: str,
    path: Path,
    evidence: dict[str, Any],
    binding: str,
) -> bool | None:
    records = _records("verification", slot + "-" + copy)
    if not records:
        return None
    value = _read(records[-1])
    if (
        set(value)
        != {
            "schema_version",
            "slot",
            "copy",
            "evidence_sha256",
            "processing_sha256",
            "identity",
            "verified_at_utc",
            "proof",
        }
        or type(value["schema_version"]) is not int
        or value["schema_version"] != PROTOCOL
        or value["slot"] != slot
        or value["copy"] != copy
    ):
        _fail("PWS0606_RECEIPT_INVALID")
    if (
        value["evidence_sha256"] != binding
        or value["processing_sha256"] != _processing(evidence)
        or value["identity"] != _identity(path)
    ):
        return None
    return _validated_proof(value["proof"], value["identity"], evidence)


def status() -> None:
    evidence, binding, environment, _environment_hash = _session()
    print("READY [PWS0606_VERIFICATION_READY]")
    print(WORKSPACE)
    complete = True
    for number, (slot, reader) in enumerate(SLOTS.items(), 1):
        observation = _observation(slot, evidence, binding, environment)
        state = "PENDING — choose this reader to continue"
        if observation is not None:
            if observation["result"] == "FAIL":
                state = "PROBLEM RECORDED — choose this reader to retry"
            else:
                results = [
                    _receipt(slot, name, path, evidence, binding)
                    for name, path, _field in _copies(slot)
                ]
                state = (
                    "PASS"
                    if all(result is True for result in results)
                    else "AUTOMATED CHECK FAILED"
                    if False in results
                    else "RECORDED — automated checks pending"
                )
                if state == "PASS" and not all(
                    _copy_matches(path, observation.get(field))
                    for _name, path, field in _copies(slot)
                ):
                    state = "PENDING — a saved copy changed"
        complete &= state == "PASS"
        print(f"{number}. {reader} ({slot}): {state}")
    print(
        "VIEWER OBSERVATIONS AND BUILT-IN CHECKS COMPLETE; independent OCR and release review remain."
        if complete
        else "PENDING — existing observations are preserved. Run verify for automated saved-copy checks."
    )


def verify() -> None:
    evidence, binding, environment, _environment_hash = _session()
    reference = (WORKSPACE / "reference.pdf").read_bytes()
    for slot in SLOTS:
        observation = _observation(slot, evidence, binding, environment)
        if observation is None or observation["result"] == "FAIL":
            continue
        for name, path, _field in _copies(slot):
            if _receipt(slot, name, path, evidence, binding) is True:
                continue
            identity = _identity(path)
            content = _saved_copy(path)
            if identity is None or content is None or _digest(content) != identity["sha256"]:
                _fail("PWS0606_ARTIFACT_CHANGED")
            print(f"Checking {slot} {name} copy...")
            with TemporaryDirectory(prefix="verify-", dir=WORKSPACE) as temporary:
                proof = _verify(content, reference, evidence, Path(temporary))
            _validated_proof(proof, identity, evidence)
            if _identity(path) != identity or _bound_evidence()[1] != binding:
                _fail("PWS0606_ARTIFACT_CHANGED")
            _append(
                "verification",
                slot + "-" + name,
                {
                    "schema_version": PROTOCOL,
                    "slot": slot,
                    "copy": name,
                    "evidence_sha256": binding,
                    "processing_sha256": _processing(evidence),
                    "identity": identity,
                    "verified_at_utc": datetime.now(UTC).isoformat(),
                    "proof": proof,
                },
            )
    status()


def _draft(
    slot: str, evidence: dict[str, Any], binding: str, environment: dict[str, Any]
) -> dict[str, Any]:
    path = WORKSPACE / "progress" / f"{slot}.json"
    empty = {
        "schema_version": PROTOCOL,
        "slot": slot,
        "evidence_sha256": binding,
        "processing_sha256": _processing(evidence),
        "environment_source": environment["slots"][slot],
        "steps": ["pending"] * 9,
        "copies": {},
        "cursor": 0,
        **_human_plan(evidence),
    }
    if not path.exists():
        return empty
    value = _read(path)
    if (
        set(value) != set(empty)
        or type(value["schema_version"]) is not int
        or value["schema_version"] != PROTOCOL
        or value["slot"] != slot
        or type(value["steps"]) is not list
        or len(value["steps"]) != 9
        or any(
            type(step) is not str or step not in {"pending", "passed", "problem"}
            for step in value["steps"]
        )
        or type(value["cursor"]) is not int
        or not 0 <= value["cursor"] < 9
        or type(value["copies"]) is not dict
        or set(value["copies"]) - {"saved", "resaved"}
    ):
        _fail("PWS0606_PROGRESS_INVALID")
    if any(
        value[key] != empty[key]
        for key in (
            "evidence_sha256",
            "processing_sha256",
            "environment_source",
            "canary_ids",
            "query_plan_sha256",
            "source_request_sha256",
        )
    ):
        _preserve_draft(slot, value)
        return empty
    changed = False
    for name, path, _field in _copies(slot):
        if name in value["copies"] and value["copies"][name] != _identity(path):
            if not changed:
                _preserve_draft(slot, value)
                changed = True
            first = 3 if name == "saved" else 6
            value["steps"][first:] = ["pending"] * (9 - first)
            value["cursor"] = min(value["cursor"], first)
            value["copies"].pop(name)
    return value


def _preserve_draft(slot: str, value: dict[str, Any]) -> None:
    records = _records("progress-history", slot + "-progress")
    if not records or _read(records[-1]) != value:
        _append("progress-history", slot + "-progress", value)


def _preserve_copy(path: Path) -> dict[str, object] | None:
    """Keep exact earlier saves before asking a reader to replace its fixed path."""
    import hashlib
    import stat

    identity = _identity(path)
    if identity is None:
        if path.exists():
            _fail("PWS0606_ARTIFACT_CHANGED")
        return None
    folder = WORKSPACE / "preserved-saves"
    if folder.is_symlink() or folder.is_junction():
        _fail("PWS0606_RECORD_INVALID")
    folder.mkdir(exist_ok=True)
    files = list(islice(folder.iterdir(), MAX_RECORDS + 1))
    if len(files) > MAX_RECORDS:
        _fail("PWS0606_PRESERVATION_LIMIT")
    total = 0
    for existing in files:
        info = existing.lstat()
        if (
            re.fullmatch(r"[0-9a-f]{64}\.pdf", existing.name) is None
            or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            _fail("PWS0606_RECORD_INVALID")
        total += info.st_size
    if total > CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes:
        _fail("PWS0606_PRESERVATION_LIMIT")
    destination = folder / f"{identity['sha256']}.pdf"
    if destination.exists():
        if _identity(destination) != identity:
            _fail("PWS0606_ARTIFACT_CHANGED")
        return identity
    if (
        len(files) >= MAX_RECORDS
        or total + cast(int, identity["byte_size"])
        > CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
    ):
        _fail("PWS0606_PRESERVATION_LIMIT")
    digest, count, created = hashlib.sha256(), 0, False
    try:
        with path.open("rb") as source, destination.open("xb") as target:
            created = True
            while block := source.read(1024 * 1024):
                count += len(block)
                if count > cast(int, identity["byte_size"]):
                    _fail("PWS0606_ARTIFACT_CHANGED")
                digest.update(block)
                target.write(block)
        if (
            {"sha256": digest.hexdigest(), "byte_size": count} != identity
            or _identity(path) != identity
            or _identity(destination) != identity
        ):
            _fail("PWS0606_ARTIFACT_CHANGED")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return identity


def _save_draft(slot: str, draft: dict[str, Any]) -> None:
    folder = WORKSPACE / "progress"
    if folder.is_symlink() or folder.is_junction():
        _fail("PWS0606_PROGRESS_INVALID")
    folder.mkdir(exist_ok=True)
    temporary = folder / f"{uuid4()}.json"
    try:
        _write(temporary, draft)
        os.replace(temporary, folder / f"{slot}.json")
    finally:
        temporary.unlink(missing_ok=True)


def _clipboard_write(text: str) -> None:
    """Write only explicit synthetic query/path actions; never inspect clipboard."""
    if os.name != "nt":
        _fail("PWS0606_CLIPBOARD_UNAVAILABLE")
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    user.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user.SetClipboardData.restype = wintypes.HANDLE
    user.OpenClipboard.argtypes = (wintypes.HWND,)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    payload = (text + "\0").encode("utf-16-le")
    handle = kernel.GlobalAlloc(0x0002, len(payload))
    if not handle:
        _fail("PWS0606_CLIPBOARD_UNAVAILABLE")
    transferred = False
    try:
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            _fail("PWS0606_CLIPBOARD_UNAVAILABLE")
        ctypes.memmove(pointer, payload, len(payload))
        kernel.GlobalUnlock(handle)
        if not user.OpenClipboard(kernel.GetConsoleWindow()):
            _fail("PWS0606_CLIPBOARD_UNAVAILABLE")
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(13, handle):
                _fail("PWS0606_CLIPBOARD_UNAVAILABLE")
            transferred = True
        finally:
            user.CloseClipboard()
    finally:
        if not transferred:
            kernel.GlobalFree(handle)


def _open_folder() -> None:
    # Open only the fixed synthetic workspace, never an arbitrary user path or
    # default PDF association that might silently select a different reader.
    if os.name != "nt":
        _fail("PWS0606_FOLDER_UNAVAILABLE")
    if WORKSPACE.is_symlink() or WORKSPACE.is_junction() or not WORKSPACE.is_dir():
        _fail("PWS0606_FOLDER_UNAVAILABLE")
    os.startfile(WORKSPACE)


def _card(step: int, slot: str) -> tuple[str, str | None, bool]:
    if step == 0:
        return (
            "Open unsafe-cover.pdf in the named reader. Confirm the correct free/built-in mode.\n"
            "Turn whole-word matching OFF. Search PWSRED: it MUST be found. Copy actual covered\n"
            "text into an unsaved text window: the covered value MUST be recoverable. Close it.",
            _query(),
            False,
        )
    if step in {1, 4, 7}:
        label = "packet.pdf" if step == 1 else f"{slot}.pdf" if step == 4 else f"{slot}-resaved.pdf"
        return (
            f"Open {label} in this SAME reader. Whole-word matching must stay OFF.\n"
            "FIRST search PWSRED: zero matches. THEN search PWSKEEP: find pages 2 and 13.",
            _query(),
            True,
        )
    if step in {2, 5, 8}:
        label = "packet.pdf" if step == 2 else f"{slot}.pdf" if step == 5 else f"{slot}-resaved.pdf"
        return (
            f"Keep {label} open in the SAME named reader.\n"
            "Review all 16 pages and their visible keep controls at a comfortable readable zoom;\n"
            "enlarge only when needed. Confirm masks, crop and orientation. Inspect available\n"
            "layers/comments/forms/attachments/actions; no original carriers should remain.\n"
            "NOW copy actual visible PWSKEEPFILE or PWSKEEPMETA FROM THIS PDF and confirm\n"
            "it in an unsaved text view. Re-prime this benign control even after a pause.\n"
            "Attempt mask selection/copy and Select All/Copy into a fresh unsaved text view.\n"
            "No removed value may be recovered. A failed copy can leave the benign PWSKEEP\n"
            "clipboard unchanged: that is NOT recovered document content. Close the text view.\n"
            "Query-copy actions are disabled here so they cannot replace the benign clipboard.",
            None,
            False,
        )
    filename = f"{slot}.pdf" if step == 3 else f"{slot}-resaved.pdf"
    path = WORKSPACE / "resaves" / filename
    return (
        "Use this SAME reader's ordinary Save As/Save a copy. Do not print, convert, or copy\n"
        f"the file in Explorer. Save to:\n{path}\n"
        "Preserve the first copy too. Close the current PDF and reopen this saved copy\n"
        "in the same reader. Enter only when that actual file is saved and reopened.",
        str(path),
        False,
    )


def _record(slot: str) -> None:
    evidence, binding, environment, environment_hash = _session()
    observation = _observation(slot, evidence, binding, environment)
    if observation is not None and observation["result"] != "FAIL":
        print(
            "Your human observations are already recorded; no repeat is needed. Automated checks use verify."
        )
        return
    draft = _draft(slot, evidence, binding, environment)
    known = environment["slots"][slot]
    print(
        f"{SLOTS[slot]} — using confirmed previous version {known['reader_version']}.\n"
        "No version entry is needed. ENTER means ALL checks displayed on this step passed."
    )
    index = min(
        draft["cursor"], next((i for i, state in enumerate(draft["steps"]) if state != "passed"), 8)
    )
    while index < 9:
        refreshed = _draft(slot, evidence, binding, environment)
        if refreshed["copies"] != draft["copies"]:
            draft = refreshed
            index = min(
                index, next((i for i, state in enumerate(draft["steps"]) if state != "passed"), 8)
            )
        if index in {3, 6}:
            _preserve_copy(_copies(slot)[0 if index == 3 else 1][1])
        card, copy_text, positive = _card(index, slot)
        print(f"\nStep {index + 1} of 9\n{card}")
        choices = "ENTER all displayed checks passed; P problem; L later; B back; F open files"
        if copy_text is not None:
            choices += "; C copy query/path"
        if positive:
            choices += "; K copy PWSKEEP"
        answer = input(choices + ": ").strip().upper()
        if answer in {"C", "K", "F"}:
            try:
                if answer == "F":
                    _open_folder()
                elif answer == "C" and copy_text is not None:
                    _clipboard_write(copy_text)
                elif answer == "K" and positive:
                    _clipboard_write(KEEP_QUERY)
                else:
                    print("That copy action is unavailable on this step.")
            except Exception:
                print(
                    "That optional action is unavailable. The displayed query/path remains usable."
                )
            continue
        if answer == "B":
            index = max(0, index - 1)
            draft["cursor"] = index
            _save_draft(slot, draft)
            continue
        if answer == "L":
            draft["cursor"] = index
            _save_draft(slot, draft)
            print("Paused. Rerun the same command and choose this reader to continue.")
            return
        if answer not in {"", "P"}:
            print("Use ENTER, P, L, B, C, K or F as offered. No result was recorded.")
            continue
        if index in {3, 6} and answer == "":
            name = "saved" if index == 3 else "resaved"
            path = dict((name, path) for name, path, _field in _copies(slot))[name]
            identity = _identity(path)
            if identity is None:
                print(
                    "That saved file is missing, empty or changed. Save it from the named reader, then retry."
                )
                continue
            if _preserve_copy(path) != identity:
                _fail("PWS0606_ARTIFACT_CHANGED")
            draft["copies"][name] = identity
        draft["steps"][index] = "problem" if answer == "P" else "passed"
        draft["cursor"] = min(index + 1, 8) if answer == "" else index
        _save_draft(slot, draft)
        if answer == "P":
            break
        index += 1
    passed = all(step == "passed" for step in draft["steps"])
    hashes = {
        field: draft["copies"].get(name, {}).get("sha256") for name, _path, field in _copies(slot)
    }
    if passed and not all(
        _copy_matches(path, hashes[field]) for _name, path, field in _copies(slot)
    ):
        print(
            "A saved copy changed. Your earlier unaffected steps remain saved; choose this reader again."
        )
        return
    _append(
        "attempts",
        slot + "-attempt",
        {
            "schema_version": PROTOCOL,
            "protocol_version": PROTOCOL,
            "slot": slot,
            "operator": "human",
            "reader_version": known["reader_version"],
            "os_version": known["os_version"],
            "environment_source": known,
            "environment_sha256": environment_hash,
            "environment_basis": environment["basis"],
            "environment_confirmed_at": environment["confirmed_at"],
            "helper_sha256": _digest((ROOT / HELPER).read_bytes()),
            "processing_sha256": _processing(evidence),
            "at_utc": datetime.now(UTC).isoformat(),
            "evidence_sha256": binding,
            "checks": dict.fromkeys(CHECKS, passed),
            **hashes,
            **_human_plan(evidence),
            "result": "OBSERVED" if passed else "FAIL",
        },
    )
    print(
        "RECORDED — automated checks pending. Your observations and both viewer files are preserved."
        if passed
        else "Problem recorded. Choose this reader again to retry the unfinished step."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("prepare", "status", "guided", "verify"), nargs="?", default="guided"
    )
    selected = parser.parse_args(argv)
    try:
        if selected.mode == "status":
            status()
        elif selected.mode == "verify":
            verify()
        else:
            prepare()
            if selected.mode == "guided":
                names = list(SLOTS)
                while True:
                    print(
                        "\nChoose a reader: "
                        + "; ".join(
                            f"{number} {reader}" for number, reader in enumerate(SLOTS.values(), 1)
                        )
                    )
                    choice = input("1–5 or W1/W2/W3/W4/A1; S status; Q stop: ").strip().upper()
                    if choice == "Q":
                        break
                    if choice == "S":
                        status()
                        continue
                    slot = names[int(choice) - 1] if choice in {"1", "2", "3", "4", "5"} else choice
                    if slot in SLOTS:
                        _record(slot)
                    else:
                        print("Choose a named reader, S or Q. No result was recorded.")
        return 0
    except EOFError, KeyboardInterrupt:
        print(
            "STOPPED [PWS0606_STOPPED]; your checkpoints and observations remain. Rerun to continue."
        )
        return 0
    except Exception:
        print("ERROR [PWS0606_VERIFICATION_HARNESS_FAILED]; preserve the workspace for inspection.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
