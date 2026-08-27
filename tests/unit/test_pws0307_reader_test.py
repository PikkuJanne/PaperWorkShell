"""Focused evidence for the guided PWS-0307 human reader workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import cast

import pytest

from tools import pws0307_reader_test as reader_test
from tools.pws0307_overlay_reader_corpus import EVIDENCE_VERSION, GENERATOR_VERSION, TASK_ID
from tools.pws0307_reader_test import (
    CORPUS_EVIDENCE_NAME,
    CORPUS_PDF_NAME,
    PROJECT_ROOT,
    READER_TEST_PROTOCOL_VERSION,
    ReaderAnswers,
    ReaderArtifactBinding,
    ReaderDefect,
    ReaderSlot,
    ReaderTestError,
    prepare_reader_test,
    reader_status,
    record_reader_result,
)


def _fake_material() -> tuple[bytes, bytes, ReaderArtifactBinding]:
    pdf = b"%PDF-1.7\n% PWS-0307 bounded synthetic unit artifact\n%%EOF\n"
    pdf_sha256 = hashlib.sha256(pdf).hexdigest()
    semantic_fingerprint = "b" * 64
    evidence = {
        "artifact": {
            "byte_size": len(pdf),
            "page_count": 5,
            "semantic_fingerprint": semantic_fingerprint,
            "sha256": pdf_sha256,
        },
        "evidence_version": EVIDENCE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "manual_reader_matrix": {"result": "not_run"},
        "result": "automated_verification_passed",
        "task_id": TASK_ID,
    }
    encoded = (
        json.dumps(evidence, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    binding = ReaderArtifactBinding(
        implementation_revision="a" * 40,
        pdf_sha256=pdf_sha256,
        pdf_byte_size=len(pdf),
        evidence_sha256=hashlib.sha256(encoded).hexdigest(),
        evidence_byte_size=len(encoded),
        page_count=5,
        semantic_fingerprint=semantic_fingerprint,
    )
    return pdf, encoded, binding


def _generator(pdf: bytes, evidence: bytes) -> Callable[[Path, Path], dict[str, object]]:
    def generate(output_pdf: Path, evidence_json: Path) -> dict[str, object]:
        output_pdf.write_bytes(pdf)
        evidence_json.write_bytes(evidence)
        return {}

    return generate


def _prepared(
    tmp_path: Path,
) -> tuple[reader_test.PreparedReaderTest, ReaderArtifactBinding, bytes, bytes]:
    pdf, evidence, binding = _fake_material()
    workspace = tmp_path / "output" / "reader" / binding.pdf_sha256[:12]
    prepared = prepare_reader_test(
        workspace,
        allowed_root=tmp_path,
        binding=binding,
        generator=_generator(pdf, evidence),
    )
    return prepared, binding, pdf, evidence


def _system_record() -> dict[str, str]:
    return {
        "platform": "Windows",
        "edition": "Professional",
        "release": "11",
        "version_build": "10.0.26200",
        "architecture": "AMD64",
    }


def _passing_answers() -> ReaderAnswers:
    return ReaderAnswers(
        configuration_confirmed=True,
        page_sequence_passed=True,
        visual_zoom_sweep_passed=True,
        search_matches=3,
        noninteractive_passed=True,
        close_reopen_passed=True,
    )


def test_default_workspace_is_fixed_below_ignored_project_output() -> None:
    assert reader_test.DEFAULT_WORKSPACE.is_relative_to(PROJECT_ROOT / "output")
    assert (
        reader_test.DEFAULT_WORKSPACE.name == reader_test.APPROVED_READER_ARTIFACT.pdf_sha256[:12]
    )
    assert "/output/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_prepare_creates_parent_and_idempotently_reuses_exact_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf, evidence, binding = _fake_material()
    workspace = tmp_path / "approved" / "nested" / binding.pdf_sha256[:12]
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    calls = 0

    def generate(output_pdf: Path, evidence_json: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        output_pdf.write_bytes(pdf)
        evidence_json.write_bytes(evidence)
        return {}

    first = prepare_reader_test(
        workspace,
        allowed_root=tmp_path,
        binding=binding,
        generator=generate,
    )
    first_mtimes = (first.pdf_path.stat().st_mtime_ns, first.evidence_path.stat().st_mtime_ns)
    second = prepare_reader_test(
        workspace,
        allowed_root=tmp_path,
        binding=binding,
        generator=generate,
    )

    assert calls == 1
    assert first.reused is False
    assert second.reused is True
    assert first.pdf_path.read_bytes() == pdf
    assert first.evidence_path.read_bytes() == evidence
    assert first.test_card_path.is_file()
    assert (
        second.pdf_path.stat().st_mtime_ns,
        second.evidence_path.stat().st_mtime_ns,
    ) == first_mtimes


def test_prepare_preserves_partial_or_mismatched_existing_pair(tmp_path: Path) -> None:
    pdf, evidence, binding = _fake_material()
    partial = tmp_path / "partial"
    partial.mkdir()
    partial_pdf = partial / CORPUS_PDF_NAME
    partial_pdf.write_bytes(pdf)

    with pytest.raises(ReaderTestError) as incomplete:
        prepare_reader_test(
            partial,
            allowed_root=tmp_path,
            binding=binding,
            generator=_generator(pdf, evidence),
        )

    assert incomplete.value.code == "PWS0307_READER_PAIR_INCOMPLETE"
    assert partial_pdf.read_bytes() == pdf
    assert not (partial / CORPUS_EVIDENCE_NAME).exists()

    mismatched = tmp_path / "mismatched"
    mismatched.mkdir()
    bad_pdf = mismatched / CORPUS_PDF_NAME
    bad_evidence = mismatched / CORPUS_EVIDENCE_NAME
    bad_pdf.write_bytes(b"preserved stale PDF")
    bad_evidence.write_bytes(b"preserved stale evidence")

    with pytest.raises(ReaderTestError) as mismatch:
        prepare_reader_test(
            mismatched,
            allowed_root=tmp_path,
            binding=binding,
            generator=_generator(pdf, evidence),
        )

    assert mismatch.value.code == "PWS0307_READER_PAIR_MISMATCH"
    assert bad_pdf.read_bytes() == b"preserved stale PDF"
    assert bad_evidence.read_bytes() == b"preserved stale evidence"


def test_prepare_rolls_back_new_pair_that_misses_approved_binding(tmp_path: Path) -> None:
    pdf, evidence, binding = _fake_material()
    workspace = tmp_path / "new-invalid"

    def generate(output_pdf: Path, evidence_json: Path) -> dict[str, object]:
        output_pdf.write_bytes(pdf + b"changed")
        evidence_json.write_bytes(evidence)
        return {}

    with pytest.raises(ReaderTestError) as raised:
        prepare_reader_test(
            workspace,
            allowed_root=tmp_path,
            binding=binding,
            generator=generate,
        )

    assert raised.value.code == "PWS0307_READER_PAIR_MISMATCH"
    assert not workspace.exists()


def test_prepare_rejects_a_modified_test_card_without_touching_bound_pair(tmp_path: Path) -> None:
    prepared, binding, pdf, evidence = _prepared(tmp_path)
    prepared.test_card_path.write_text("modified local instructions\n", encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        prepare_reader_test(
            prepared.workspace,
            allowed_root=tmp_path,
            binding=binding,
            generator=_generator(pdf, evidence),
        )

    assert raised.value.code == "PWS0307_READER_WORKSPACE_CONFLICT"
    assert prepared.pdf_path.read_bytes() == pdf
    assert prepared.evidence_path.read_bytes() == evidence
    assert prepared.test_card_path.read_text(encoding="utf-8") == "modified local instructions\n"


def test_prepare_accepts_an_exact_concurrently_created_card_without_deleting_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf, evidence, binding = _fake_material()
    workspace = tmp_path / "concurrent" / binding.pdf_sha256[:12]
    original_write = reader_test._write_exclusive_text
    injected = False

    def concurrent_write(path: Path, payload: str) -> None:
        nonlocal injected
        if path.name == reader_test.TEST_CARD_NAME and not injected:
            injected = True
            original_write(path, payload)
        original_write(path, payload)

    monkeypatch.setattr(reader_test, "_write_exclusive_text", concurrent_write)
    prepared = prepare_reader_test(
        workspace,
        allowed_root=tmp_path,
        binding=binding,
        generator=_generator(pdf, evidence),
    )

    assert injected is True
    assert prepared.pdf_path.read_bytes() == pdf
    assert prepared.evidence_path.read_bytes() == evidence
    assert prepared.test_card_path.read_text(encoding="utf-8") == reader_test._test_card(binding)


def test_prepare_rejects_link_like_workspace_without_calling_generator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "output" / "reader"
    called = False

    def generate(_output_pdf: Path, _evidence_json: Path) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    original = reader_test._path_is_link_like

    def link_probe(path: Path) -> bool:
        return path.name == "output" or original(path)

    monkeypatch.setattr(reader_test, "_path_is_link_like", link_probe)
    with pytest.raises(ReaderTestError) as raised:
        prepare_reader_test(workspace, allowed_root=tmp_path, generator=generate)

    assert raised.value.code == "PWS0307_READER_WORKSPACE_INVALID"
    assert called is False


def test_record_derives_content_free_pass_and_never_replaces_an_attempt(tmp_path: Path) -> None:
    prepared, binding, _pdf, _evidence = _prepared(tmp_path)
    first = record_reader_result(
        prepared,
        ReaderSlot.W1,
        "26.1.21789.0",
        "reader_free_or_reduced",
        _passing_answers(),
        allowed_root=tmp_path,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    first_bytes = first.read_bytes()
    failed_answers = ReaderAnswers(
        configuration_confirmed=True,
        page_sequence_passed=True,
        visual_zoom_sweep_passed=False,
        search_matches=2,
        noninteractive_passed=True,
        close_reopen_passed=False,
        affected_pages=(2, 4),
    )
    second = record_reader_result(
        prepared,
        ReaderSlot.W1,
        "26.1.21789.0",
        "reader_free_or_reduced",
        failed_answers,
        allowed_root=tmp_path,
        tested_at_utc="2026-08-27T12:05:00Z",
        system_record=_system_record(),
    )

    assert first.name.endswith("attempt-01.json")
    assert second.name.endswith("attempt-02.json")
    assert first.read_bytes() == first_bytes
    first_result = json.loads(first.read_text(encoding="utf-8"))
    second_result = json.loads(second.read_text(encoding="utf-8"))
    assert first_result["result"] == "pass"
    assert first_result["defect_categories"] == []
    assert second_result["result"] == "fail"
    assert second_result["defect_categories"] == [
        ReaderDefect.VISUAL_RENDERING.value,
        ReaderDefect.TEXT_SEARCH.value,
        ReaderDefect.CLOSE_REOPEN.value,
    ]
    assert second_result["affected_pages"] == [2, 4]
    encoded = first.read_text(encoding="utf-8") + second.read_text(encoding="utf-8")
    assert str(tmp_path) not in encoded
    assert "Reader matrix text" not in encoded
    assert "born-digital.pdf" not in encoded
    assert "synthetic-person" not in encoded
    assert binding.pdf_sha256 in encoded
    assert reader_status(prepared, allowed_root=tmp_path)[ReaderSlot.W1]["result"] == "fail"


def test_status_rejects_a_tampered_derived_pass(tmp_path: Path) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)
    result_path = record_reader_result(
        prepared,
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        allowed_root=tmp_path,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    checks = cast(dict[str, object], payload["checks"])
    checks["close_reopen_fit_and_400"] = False
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=tmp_path)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


def test_status_rejects_an_impossible_shaped_utc_timestamp(tmp_path: Path) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)
    result_path = record_reader_result(
        prepared,
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        allowed_root=tmp_path,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["tested_at_utc"] = "2026-99-99T99:99:99Z"
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=tmp_path)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    (
        ("result", []),
        ("viewer.slot", []),
        ("viewer.mode", {}),
    ),
)
def test_status_maps_unhashable_tampering_to_stable_invalid_result(
    tmp_path: Path,
    field: str,
    unsafe_value: object,
) -> None:
    allowed_root = tmp_path / field.replace(".", "-")
    allowed_root.mkdir()
    prepared, _binding, _pdf, _evidence = _prepared(allowed_root)
    result_path = record_reader_result(
        prepared,
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        allowed_root=allowed_root,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if field == "result":
        payload["result"] = unsafe_value
    else:
        viewer = cast(dict[str, object], payload["viewer"])
        viewer[field.removeprefix("viewer.")] = unsafe_value
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=allowed_root)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


@pytest.mark.parametrize(
    ("container_name", "extra_key"),
    (
        ("root", "notes"),
        ("viewer", "profile_path"),
        ("artifact", "source_path"),
        ("windows", "account"),
        ("checks", "free_text"),
    ),
)
def test_status_rejects_extra_fields_in_every_closed_result_object(
    tmp_path: Path,
    container_name: str,
    extra_key: str,
) -> None:
    allowed_root = tmp_path / container_name
    allowed_root.mkdir()
    prepared, _binding, _pdf, _evidence = _prepared(allowed_root)
    result_path = record_reader_result(
        prepared,
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        allowed_root=allowed_root,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    target = (
        payload if container_name == "root" else cast(dict[str, object], payload[container_name])
    )
    target[extra_key] = r"C:\Users\synthetic-person\private"
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=allowed_root)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


def test_status_rejects_duplicate_keys_that_hide_prohibited_raw_values(tmp_path: Path) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)
    result_path = record_reader_result(
        prepared,
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        allowed_root=tmp_path,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    encoded = result_path.read_text(encoding="utf-8")
    expected = '"version_build": "151.0.7922.174"'
    duplicate = '"version_build": "C:\\\\Users\\\\synthetic-person",\n    ' + expected
    assert encoded.count(expected) == 1
    result_path.write_text(encoded.replace(expected, duplicate), encoding="utf-8")

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=tmp_path)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


def test_status_rejects_an_oversized_result_before_json_decode(tmp_path: Path) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)
    oversized = prepared.workspace / "pws-0307-W4-attempt-01.json"
    oversized.write_bytes(b"{" + b" " * reader_test.MAXIMUM_READER_RESULT_BYTES)

    with pytest.raises(ReaderTestError) as raised:
        reader_status(prepared, allowed_root=tmp_path)

    assert raised.value.code == "PWS0307_READER_RESULT_INVALID"


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (
            ReaderAnswers(True, True, True, 4, True, True),
            "search result must be a count",
        ),
        (
            ReaderAnswers(True, True, True, 3, True, True, (1,)),
            "passing result cannot contain affected pages",
        ),
        (
            ReaderAnswers(True, False, True, 3, True, True, (6,)),
            "(?i)affected page numbers",
        ),
        (
            ReaderAnswers(1, True, True, 3, True, True),  # type: ignore[arg-type]
            "explicit yes or no",
        ),
    ],
)
def test_invalid_closed_answers_fail_before_result_write(
    tmp_path: Path,
    answers: ReaderAnswers,
    message: str,
) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)

    with pytest.raises(ReaderTestError, match=message):
        record_reader_result(
            prepared,
            ReaderSlot.W4,
            "154.0",
            "built_in_local_pdf_viewer",
            answers,
            allowed_root=tmp_path,
            tested_at_utc="2026-08-27T12:00:00Z",
            system_record=_system_record(),
        )

    assert not tuple(prepared.workspace.glob("pws-0307-W4-attempt-*.json"))


def test_interactive_session_retries_version_and_reaches_matrix_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _binding, _pdf, _evidence = _prepared(tmp_path)
    monkeypatch.setattr(reader_test, "prepare_reader_test", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(reader_test, "_safe_system_record", _system_record)
    scripted = iter(
        [
            "1",
            "",
            "Alice Applicant",
            "26.1.21789.0",
            "1",
            "y",
            "y",
            "y",
            "3",
            "y",
            "y",
            "3",
            "",
            "151.0.7922.174",
            "1",
            "y",
            "y",
            "y",
            "3",
            "y",
            "y",
            "4",
            "",
            "154.0",
            "1",
            "y",
            "y",
            "y",
            "3",
            "y",
            "y",
            "s",
            "q",
        ]
    )
    prompts: list[str] = []

    def scripted_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(scripted)

    output = StringIO()
    result = reader_test.interactive_session(
        input_fn=scripted_input,
        output=output,
        workspace=prepared.workspace,
        allowed_root=tmp_path,
    )
    rendered = output.getvalue()

    assert result == 0
    assert "Enter only the numeric dotted version/build" in rendered
    assert "Alice Applicant" not in rendered
    assert "MATRIX READY FOR CODEX REVIEW" in rendered
    assert "Numeric dotted About version/build (or cancel): " in prompts
    latest = reader_status(prepared, allowed_root=tmp_path)
    assert {slot: result["result"] for slot, result in latest.items()} == {
        ReaderSlot.W1: "pass",
        ReaderSlot.W3: "pass",
        ReaderSlot.W4: "pass",
    }


def test_protocol_card_is_complete_and_does_not_request_manual_hash_entry() -> None:
    card = reader_test._test_card(reader_test.APPROVED_READER_ARTIFACT)

    assert f"PROTOCOL {READER_TEST_PROTOCOL_VERSION}" in card
    assert "W2 Edge is already" not in card
    assert "W1 - Adobe Acrobat Reader/free-reduced" in card
    assert "W3 - Google Chrome" in card
    assert "W4 - Mozilla Firefox" in card
    assert "1 > 2 > 3 > 4 > 5 > 4 > 3 > 2 > 1" in card
    assert "Fit Page, 100%, 200%, and 400%" in card
    assert "require 3/3 found" in card
    assert "never asks you to type a hash" in card
    assert "digital signature" not in card.casefold()
