"""Pure contract tests for typed PDF structural inspection results."""

from __future__ import annotations

import json
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
        lambda data: data.update(report_version=2),
        lambda data: data.update(extra=True),
        lambda data: data.update(page_count=True),
        lambda data: data["features"].update(signature_markers="certain"),
        lambda data: data["features"].pop("scripts"),
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
