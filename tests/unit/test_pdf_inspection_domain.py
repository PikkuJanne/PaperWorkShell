"""Pure contract tests for typed PDF structural inspection results."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_REPORT_BYTES,
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionEnvelope,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    decode_pdf_inspection_frame,
    decode_pdf_inspection_report,
    encode_pdf_inspection_frame,
    encode_pdf_inspection_report,
)


def _ordinary_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.OPENED,
        page_count=1,
        object_count=6,
        encryption_state=PdfEncryptionState.UNENCRYPTED,
        structural_state=PdfStructuralState.CLEAN,
        features=PdfFeatureSummary(
            signature_markers=PdfFeatureState.NOT_DETECTED,
            attachments=PdfFeatureState.NOT_DETECTED,
            interactive_content=PdfFeatureState.NOT_DETECTED,
            actions=PdfFeatureState.NOT_DETECTED,
            scripts=PdfFeatureState.NOT_DETECTED,
            xfa=PdfFeatureState.NOT_DETECTED,
        ),
    )


def test_current_report_round_trips_canonically() -> None:
    result = _ordinary_result()
    encoded = encode_pdf_inspection_report(result)

    assert decode_pdf_inspection_report(encoded) == result
    assert encoded == encode_pdf_inspection_report(decode_pdf_inspection_report(encoded))
    assert len(encoded) <= MAX_PDF_INSPECTION_REPORT_BYTES
    assert b"path" not in encoded
    assert result.report_version == 2
    assert set(json.loads(encoded)["features"]) == {
        "signature_markers",
        "attachments",
        "interactive_content",
        "actions",
        "scripts",
        "xfa",
        "metadata",
        "annotations",
        "form_fields",
    }


def test_v1_wire_report_stays_exact_and_does_not_invent_new_inspection_facts() -> None:
    result = replace(_ordinary_result(), report_version=1)
    encoded = encode_pdf_inspection_report(result)

    assert encoded == (
        b'{"encryption_state":"unencrypted","features":{"actions":"not_detected",'
        b'"attachments":"not_detected","interactive_content":"not_detected",'
        b'"scripts":"not_detected","signature_markers":"not_detected","xfa":"not_detected"},'
        b'"object_count":6,"open_state":"opened","page_count":1,"report_version":1,'
        b'"structural_state":"clean"}'
    )
    decoded = decode_pdf_inspection_report(encoded)
    assert decoded == result
    assert decoded.features.values[6:] == (PdfFeatureState.UNKNOWN,) * 3


@pytest.mark.parametrize("feature", ["metadata", "annotations", "form_fields"])
def test_v1_cannot_claim_new_feature_inspection(feature: str) -> None:
    with pytest.raises(PdfInspectionError):
        replace(
            _ordinary_result(),
            report_version=1,
            features=replace(_ordinary_result().features, **{feature: PdfFeatureState.DETECTED}),
        )


def test_v1_opened_still_requires_six_known_fields_while_v2_preserves_unknown() -> None:
    result = replace(_ordinary_result(), features=PdfFeatureSummary.unknown())
    assert decode_pdf_inspection_report(encode_pdf_inspection_report(result)) == result
    with pytest.raises(PdfInspectionError):
        replace(result, report_version=1)


@pytest.mark.parametrize("version", [1, 2])
def test_report_versions_have_exact_feature_field_sets(version: int) -> None:
    document = json.loads(
        encode_pdf_inspection_report(replace(_ordinary_result(), report_version=version))
    )
    if version == 1:
        document["features"]["metadata"] = "unknown"
    else:
        del document["features"]["metadata"]
    with pytest.raises(PdfInspectionError) as caught:
        decode_pdf_inspection_report(json.dumps(document).encode("ascii"))
    assert caught.value.code is PdfInspectionErrorCode.REPORT_INVALID


def test_worker_result_frame_binds_operation_source_and_report() -> None:
    envelope = PdfInspectionEnvelope(
        UUID("11000000-0000-4000-8000-000000000105"),
        UUID("12000000-0000-4000-8000-000000000105"),
        _ordinary_result(),
    )
    frame = encode_pdf_inspection_frame(envelope)

    assert decode_pdf_inspection_frame(frame) == envelope
    assert len(frame) - 4 == int.from_bytes(frame[:4], "big")


@pytest.mark.parametrize("mutation", ["truncated", "surplus", "length", "identifier"])
def test_worker_result_frame_fails_closed_on_corruption(mutation: str) -> None:
    frame = bytearray(
        encode_pdf_inspection_frame(
            PdfInspectionEnvelope(
                UUID("11000000-0000-4000-8000-000000000105"),
                UUID("12000000-0000-4000-8000-000000000105"),
                _ordinary_result(),
            )
        )
    )
    if mutation == "truncated":
        del frame[-1]
    elif mutation == "surplus":
        frame.extend(b"x")
    elif mutation == "length":
        frame[:4] = (1).to_bytes(4, "big")
    else:
        frame[4:20] = bytes(16)

    with pytest.raises(PdfInspectionError) as caught:
        decode_pdf_inspection_frame(bytes(frame))

    assert caught.value.code is PdfInspectionErrorCode.REPORT_INVALID


@pytest.mark.parametrize(
    "result",
    [
        PdfInspectionResult(
            PdfOpenState.ENCRYPTED,
            None,
            None,
            PdfEncryptionState.ENCRYPTED,
            PdfStructuralState.FAILED,
            PdfFeatureSummary.unknown(),
        ),
        PdfInspectionResult(
            PdfOpenState.MALFORMED,
            None,
            None,
            PdfEncryptionState.UNKNOWN,
            PdfStructuralState.FAILED,
            PdfFeatureSummary.unknown(),
        ),
    ],
)
def test_unopened_outcomes_round_trip_without_false_feature_claims(
    result: PdfInspectionResult,
) -> None:
    assert decode_pdf_inspection_report(encode_pdf_inspection_report(result)) == result


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(report_version=3),
        lambda data: data.update(report_version=True),
        lambda data: data.update(extra=True),
        lambda data: data.update(page_count=True),
        lambda data: data["features"].update(signature_markers="certain"),
        lambda data: data["features"].pop("scripts"),
        lambda data: data["features"].update(metadata="certain"),
        lambda data: data["features"].update(form_fields=True),
    ],
)
def test_report_decoder_rejects_future_unknown_and_invalid_values(mutation: object) -> None:
    document = json.loads(encode_pdf_inspection_report(_ordinary_result()))
    mutation(document)  # type: ignore[operator]
    payload = json.dumps(document, separators=(",", ":")).encode("ascii")

    with pytest.raises(PdfInspectionError) as caught:
        decode_pdf_inspection_report(payload)

    assert caught.value.code is PdfInspectionErrorCode.REPORT_INVALID


def test_report_decoder_rejects_duplicate_members_and_oversize() -> None:
    with pytest.raises(PdfInspectionError) as duplicate:
        decode_pdf_inspection_report(b'{"report_version":1,"report_version":1}')
    with pytest.raises(PdfInspectionError) as oversize:
        decode_pdf_inspection_report(b"x" * (MAX_PDF_INSPECTION_REPORT_BYTES + 1))

    assert duplicate.value.code is PdfInspectionErrorCode.REPORT_INVALID
    assert oversize.value.code is PdfInspectionErrorCode.REPORT_INVALID


@pytest.mark.parametrize("payload", [b'"PRIVATE-PDF-PARSE-VALUE', b'"PRIVATE-PDF-PARSE-VALUE\xff"'])
def test_report_parse_failure_does_not_retain_document_values_in_exception_context(
    payload: bytes,
) -> None:
    with pytest.raises(PdfInspectionError) as caught:
        decode_pdf_inspection_report(payload)

    assert caught.value.code is PdfInspectionErrorCode.REPORT_INVALID
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "PRIVATE-PDF-" not in f"{caught.value!r} {caught.value!s}"


def test_opened_result_requires_complete_known_features_and_counts() -> None:
    with pytest.raises(PdfInspectionError) as caught:
        PdfInspectionResult(
            PdfOpenState.OPENED,
            None,
            None,
            PdfEncryptionState.UNENCRYPTED,
            PdfStructuralState.CLEAN,
            PdfFeatureSummary.unknown(),
        )

    assert caught.value.code is PdfInspectionErrorCode.MODEL_INVALID
    assert "selected" not in repr(caught.value).lower()


def test_encrypted_result_cannot_claim_an_unperformed_clean_structural_check() -> None:
    with pytest.raises(PdfInspectionError) as caught:
        PdfInspectionResult(
            PdfOpenState.ENCRYPTED,
            None,
            None,
            PdfEncryptionState.ENCRYPTED,
            PdfStructuralState.CLEAN,
            PdfFeatureSummary.unknown(),
        )

    assert caught.value.code is PdfInspectionErrorCode.MODEL_INVALID


def test_limits_reject_report_budget_that_cannot_hold_the_closed_schema() -> None:
    with pytest.raises(PdfInspectionError) as caught:
        PdfInspectionLimits(
            maximum_pages=1,
            maximum_objects=1,
            maximum_input_bytes=1,
            maximum_temporary_bytes=1,
            maximum_output_bytes=MAX_PDF_INSPECTION_REPORT_BYTES - 1,
            maximum_tool_output_bytes=1,
        )

    assert caught.value.code is PdfInspectionErrorCode.MODEL_INVALID
