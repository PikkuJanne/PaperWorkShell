from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.worker.pdf_export_report import PdfExportWorkerReport
from paperworkshell.worker.searchable_pdf_export_plan import (
    decode_searchable_pdf_worker_plan,
    encode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    SearchablePdfWorkerReport,
    decode_searchable_pdf_worker_report,
    encode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    SearchablePdfPageStatusDocument,
    decode_searchable_pdf_page_status,
    encode_searchable_pdf_page_status,
)

CASE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SOURCE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PAGE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
OPERATION_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OUTPUT_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
STATUS_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


def _plan(
    *,
    language_set_id: str = "latin-default-v1",
) -> SearchablePdfExportPlan:
    source = SearchablePdfSource(
        source_id=SOURCE_ID,
        media_type=SearchablePdfSourceMediaType.PDF,
        byte_size=120,
        page_count=1,
        sha256="1" * 64,
        signature_state=SearchablePdfSourceSignatureState.NONE_DETECTED,
        encryption_state=SearchablePdfSourceEncryptionState.UNENCRYPTED,
    )
    page = SearchablePdfPage(
        output_page_index=0,
        page_id=PAGE_ID,
        source_id=SOURCE_ID,
        source_page_index=0,
        quarter_turns_clockwise=0,
        crop=None,
        deskew_angle_millidegrees_clockwise=0,
        contrast_factor_millionths=1_000_000,
        background_cleanup_strength_millionths=0,
    )
    return SearchablePdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=4,
        manifest_sha256="2" * 64,
        manifest_cas_token="abcdefghijklmnop",
        sources=(source,),
        pages=(page,),
        profile=SearchablePdfExportProfile(
            ocr_language_set_id=language_set_id,
        ),
    )


def _outcome() -> SearchablePdfPageOutcome:
    return SearchablePdfPageOutcome(
        eligibility=SearchablePdfPageEligibility(
            output_page_index=0,
            page_id=PAGE_ID,
            code=SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
        ),
        outcome=SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        searchability=SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
    )


def test_searchable_worker_plan_round_trips_exact_canonical_bytes() -> None:
    plan = _plan()
    payload = encode_searchable_pdf_worker_plan(plan, {SOURCE_ID: 41})

    decoded = decode_searchable_pdf_worker_plan(payload)

    assert decoded.plan == plan
    assert decoded.sources[0].worker_handle == 41
    assert (
        encode_searchable_pdf_worker_plan(
            decoded.plan,
            {SOURCE_ID: decoded.sources[0].worker_handle},
        )
        == payload
    )


def test_searchable_worker_plan_round_trips_supported_multiple_language_set() -> None:
    plan = _plan(language_set_id="english-german-fast-v1")
    payload = encode_searchable_pdf_worker_plan(plan, {SOURCE_ID: 41})

    decoded = decode_searchable_pdf_worker_plan(payload)

    assert decoded.plan == plan
    assert decoded.plan.profile.ocr_language_set_id == "english-german-fast-v1"


def test_searchable_worker_plan_rejects_unsupported_language_set() -> None:
    plan = _plan(language_set_id="unsupported-private-v1")
    payload = encode_searchable_pdf_worker_plan(plan, {SOURCE_ID: 41})

    with pytest.raises(SearchablePdfExportError):
        decode_searchable_pdf_worker_plan(payload)


@pytest.mark.parametrize("optimized", (False, True))
def test_searchable_status_and_worker_report_round_trip(optimized: bool) -> None:
    plan = _plan()
    output_bytes = b"verified-output"
    report = SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256=hashlib.sha256(output_bytes).hexdigest(),
        output_byte_size=len(output_bytes),
        page_outcomes=(_outcome(),),
    )
    if optimized:
        report = replace(
            report,
            base_report=replace(
                report.base_report,
                report_version=2,
                optimization=PdfOptimizationReport(
                    PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
                    PdfOptimizationOutcome.OPTIMIZED,
                    2 * len(output_bytes),
                    len(output_bytes),
                    len(output_bytes),
                    1,
                ),
            ),
        )
    status = SearchablePdfPageStatusDocument(
        operation_id=OPERATION_ID,
        status_object_id=STATUS_ID,
        semantic_fingerprint=plan.semantic_fingerprint,
        page_outcomes=report.page_outcomes,
    )
    status_payload = encode_searchable_pdf_page_status(status)
    worker_report = SearchablePdfWorkerReport(
        operation_id=OPERATION_ID,
        output_object_id=OUTPUT_ID,
        status_object_id=STATUS_ID,
        status_byte_size=len(status_payload),
        status_sha256=hashlib.sha256(status_payload).hexdigest(),
        base_report=PdfExportWorkerReport(
            operation_id=OPERATION_ID,
            output_object_id=OUTPUT_ID,
            report=report.base_report,
        ),
    )

    assert decode_searchable_pdf_page_status(status_payload) == status
    encoded_report = encode_searchable_pdf_worker_report(worker_report)
    assert decode_searchable_pdf_worker_report(encoded_report) == worker_report


@pytest.mark.parametrize(
    "decoder,payload",
    [
        (decode_searchable_pdf_worker_plan, b"{}"),
        (decode_searchable_pdf_page_status, b"{}"),
        (decode_searchable_pdf_worker_report, b"{}"),
    ],
)
def test_searchable_wire_decoders_reject_malformed_or_future_documents(
    decoder: object,
    payload: bytes,
) -> None:
    with pytest.raises((SearchablePdfExportError, PdfExportError)):
        decoder(payload)  # type: ignore[operator]


def test_searchable_status_rejects_noncanonical_json() -> None:
    status = SearchablePdfPageStatusDocument(
        operation_id=OPERATION_ID,
        status_object_id=STATUS_ID,
        semantic_fingerprint=_plan().semantic_fingerprint,
        page_outcomes=(_outcome(),),
    )

    with pytest.raises(SearchablePdfExportError):
        decode_searchable_pdf_page_status(
            encode_searchable_pdf_page_status(status).replace(b'"pages":', b'"pages" :')
        )
