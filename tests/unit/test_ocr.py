"""Pure contract tests for bounded, content-free OCR models."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    MAX_OCR_PAGES,
    MAX_OCR_WORKER_REPORT_BYTES,
    OcrCandidateArtifact,
    OcrEngineCapability,
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrProgress,
    OcrProgressPhase,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
    decode_ocr_worker_report,
    encode_ocr_worker_report,
    ocr_engine_profile_sha256,
)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _pdf_request() -> OcrProcessRequest:
    return OcrProcessRequest(
        case_id=_uuid(1),
        source_id=_uuid(2),
        source_kind=OcrSourceKind.PDF,
        source_byte_size=4_096,
        source_page_count=3,
        source_sha256="a" * 64,
        signature_state=OcrSourceSignatureState.NONE_DETECTED,
        encryption_state=OcrSourceEncryptionState.UNENCRYPTED,
        language_set_id="latin-default-v1",
    )


def test_request_is_path_free_and_redacts_configuration_and_digest() -> None:
    request = _pdf_request()

    assert request.profile_id == DEFAULT_OCR_PROFILE_ID
    assert request.source_page_count == 3
    assert all(
        prohibited not in {field.name for field in fields(OcrProcessRequest)}
        for prohibited in ("text", "ocr_text", "filename", "path", "arguments")
    )
    assert "a" * 64 not in repr(request)
    assert "latin-default-v1" not in repr(request)
    assert DEFAULT_OCR_PROFILE_ID not in repr(request)


@pytest.mark.parametrize("source_kind", (OcrSourceKind.JPEG, OcrSourceKind.PNG))
def test_image_request_requires_single_page_and_nonapplicable_pdf_states(
    source_kind: OcrSourceKind,
) -> None:
    request = OcrProcessRequest(
        case_id=_uuid(1),
        source_id=_uuid(2),
        source_kind=source_kind,
        source_byte_size=100,
        source_page_count=1,
        source_sha256="b" * 64,
        signature_state=OcrSourceSignatureState.NOT_APPLICABLE,
        encryption_state=OcrSourceEncryptionState.NOT_APPLICABLE,
        language_set_id="latin-default-v1",
    )

    assert request.source_kind is source_kind


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    (
        ({"source_page_count": 0}, OcrErrorCode.MODEL_INVALID),
        ({"source_page_count": MAX_OCR_PAGES + 1}, OcrErrorCode.MODEL_INVALID),
        ({"source_sha256": "secret-document-token"}, OcrErrorCode.MODEL_INVALID),
        ({"language_set_id": "eng;run-command"}, OcrErrorCode.MODEL_INVALID),
        ({"profile_id": "../../profile"}, OcrErrorCode.MODEL_INVALID),
        ({"encryption_state": OcrSourceEncryptionState.ENCRYPTED}, OcrErrorCode.SOURCE_ENCRYPTED),
        (
            {"encryption_state": OcrSourceEncryptionState.UNKNOWN},
            OcrErrorCode.SOURCE_ENCRYPTION_UNKNOWN,
        ),
        (
            {"signature_state": OcrSourceSignatureState.MARKERS_DETECTED},
            OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION,
        ),
        (
            {"signature_state": OcrSourceSignatureState.UNKNOWN},
            OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION,
        ),
    ),
)
def test_request_fails_closed_with_stable_sanitized_errors(
    changes: dict[str, object],
    expected_code: OcrErrorCode,
) -> None:
    with pytest.raises(OcrError) as raised:
        replace(_pdf_request(), **cast(dict[str, Any], changes))

    assert raised.value.code is expected_code
    assert "secret-document-token" not in repr(raised.value)
    assert "secret-document-token" not in str(raised.value)


def test_image_request_rejects_pdf_state_or_multiple_pages() -> None:
    base = OcrProcessRequest(
        case_id=_uuid(1),
        source_id=_uuid(2),
        source_kind=OcrSourceKind.JPEG,
        source_byte_size=100,
        source_page_count=1,
        source_sha256="b" * 64,
        signature_state=OcrSourceSignatureState.NOT_APPLICABLE,
        encryption_state=OcrSourceEncryptionState.NOT_APPLICABLE,
        language_set_id="latin-default-v1",
    )

    for changes in (
        {"source_page_count": 2},
        {"signature_state": OcrSourceSignatureState.NONE_DETECTED},
        {"encryption_state": OcrSourceEncryptionState.UNENCRYPTED},
    ):
        with pytest.raises(OcrError) as raised:
            replace(base, **changes)
        assert raised.value.code is OcrErrorCode.MODEL_INVALID


def test_progress_is_content_free_and_phase_bounded() -> None:
    progress = OcrProgress(_uuid(10), OcrProgressPhase.RECOGNIZING, 2, 3)

    assert progress.completed_pages == 2
    assert OcrProgress(_uuid(10), OcrProgressPhase.PREPARING, 0, 3).completed_pages == 0
    assert OcrProgress(_uuid(10), OcrProgressPhase.COMPLETE, 3, 3).completed_pages == 3

    for invalid in (
        (_uuid(10), OcrProgressPhase.PREPARING, 1, 3),
        (_uuid(10), OcrProgressPhase.FINALIZING, 2, 3),
        (_uuid(10), OcrProgressPhase.RECOGNIZING, 4, 3),
    ):
        with pytest.raises(OcrError) as raised:
            OcrProgress(*invalid)
        assert raised.value.code is OcrErrorCode.MODEL_INVALID


def test_candidate_artifact_binds_distinct_ids_and_redacts_digest_and_configuration() -> None:
    artifact = OcrCandidateArtifact(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_id=_uuid(4),
        byte_size=2_048,
        page_count=3,
        language_set_id="latin-default-v1",
        profile_id=DEFAULT_OCR_PROFILE_ID,
        sha256="c" * 64,
    )

    assert artifact.page_count == 3
    assert "c" * 64 not in repr(artifact)
    assert "latin-default-v1" not in repr(artifact)
    assert DEFAULT_OCR_PROFILE_ID not in repr(artifact)

    with pytest.raises(OcrError) as raised:
        replace(artifact, output_id=artifact.operation_id)
    assert raised.value.code is OcrErrorCode.RESULT_INVALID


def test_engine_capability_accepts_only_bounded_unique_content_free_tokens() -> None:
    capability = OcrEngineCapability("17.10.0", "5.5.1", ("latin-default-v1",))

    assert capability.language_set_ids == ("latin-default-v1",)
    for arguments in (
        ("17.10.0", "5.5.1", ("latin-default-v1", "latin-default-v1")),
        ("raw version\nsource.pdf", "5.5.1", ("latin-default-v1",)),
    ):
        with pytest.raises(OcrError) as raised:
            OcrEngineCapability(*arguments)
        assert raised.value.code is OcrErrorCode.MODEL_INVALID


def test_engine_profile_hash_freezes_exact_content_free_execution_settings() -> None:
    assert (
        ocr_engine_profile_sha256("17.10.0", "5.5.3")
        == "3fb6b920809bd4f9c410ff0ddb5da8b2af90896024e8dbddfac62e26cb989fb3"
    )
    assert ocr_engine_profile_sha256("17.10.0", "5.5.4") != ocr_engine_profile_sha256(
        "17.10.0", "5.5.3"
    )

    for invalid in ("17.10.0 source.pdf", "17.10.0\nprivate", ""):
        with pytest.raises(OcrError) as raised:
            ocr_engine_profile_sha256(invalid, "5.5.3")
        assert raised.value.code is OcrErrorCode.RESULT_INVALID
        if invalid:
            assert invalid not in repr(raised.value)
            assert invalid not in str(raised.value)


def _worker_report() -> OcrWorkerReport:
    return OcrWorkerReport(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_object_id=_uuid(4),
        source_kind=OcrSourceKind.PDF,
        page_count=3,
        processed_page_count=3,
        language_set_id="latin-default-v1",
        profile_id=DEFAULT_OCR_PROFILE_ID,
        ocrmypdf_version="17.10.0",
        tesseract_version="5.5.1",
        engine_profile_sha256="d" * 64,
        output_byte_size=2_048,
        output_sha256="e" * 64,
    )


def test_worker_report_round_trips_one_canonical_content_free_frame() -> None:
    report = _worker_report()

    payload = encode_ocr_worker_report(report)

    assert decode_ocr_worker_report(payload) == report
    assert payload == json.dumps(
        json.loads(payload.decode("ascii")),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert len(payload) <= MAX_OCR_WORKER_REPORT_BYTES
    assert b"text" not in payload
    assert b"filename" not in payload
    assert b"path" not in payload
    assert "d" * 64 not in repr(report)
    assert "e" * 64 not in repr(report)
    assert "latin-default-v1" not in repr(report)


@pytest.mark.parametrize(
    "changes",
    (
        {"source_id": _uuid(3)},
        {"output_object_id": _uuid(3)},
        {"processed_page_count": 2},
        {"engine_profile_sha256": "not-a-digest"},
        {"output_sha256": "secret-document-token"},
        {"output_byte_size": 0},
        {"language_set_id": "eng;run-command"},
        {"profile_id": "../../profile"},
        {"ocrmypdf_version": "17.10.0\nsource.pdf"},
        {"report_version": 2},
    ),
)
def test_worker_report_model_rejects_uncorrelated_or_unbounded_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(OcrError) as raised:
        replace(_worker_report(), **cast(dict[str, Any], changes))

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert "secret-document-token" not in repr(raised.value)
    assert "secret-document-token" not in str(raised.value)


@pytest.mark.parametrize("mutation", ("missing", "extra", "future", "float", "alias"))
def test_worker_report_decoder_rejects_every_closed_frame_deviation(mutation: str) -> None:
    document = json.loads(encode_ocr_worker_report(_worker_report()).decode("ascii"))
    if mutation == "missing":
        del document["profile_id"]
    elif mutation == "extra":
        document["ocr_text"] = "prohibited fixture token"
    elif mutation == "future":
        document["report_version"] = 2
    elif mutation == "float":
        document["page_count"] = 3.0
    else:
        document["output_object_id"] = document["operation_id"]
    payload = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")

    with pytest.raises(OcrError) as raised:
        decode_ocr_worker_report(payload)

    assert raised.value.code is OcrErrorCode.RESULT_INVALID
    assert "prohibited fixture token" not in repr(raised.value)
    assert "prohibited fixture token" not in str(raised.value)


def test_worker_report_decoder_rejects_duplicate_noncanonical_and_oversized_frames() -> None:
    payload = encode_ocr_worker_report(_worker_report())
    duplicate = b'{"report_version":1,' + payload[1:]

    for invalid in (duplicate, b" " + payload, payload + b"\n", b"x" * 16_385):
        with pytest.raises(OcrError) as raised:
            decode_ocr_worker_report(invalid)
        assert raised.value.code is OcrErrorCode.RESULT_INVALID
