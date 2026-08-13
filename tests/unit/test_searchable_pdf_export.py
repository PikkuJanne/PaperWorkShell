"""Pure PWS-0206 contracts for searchable mixed-document output."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.ocr import TRUSTED_OCR_ENGINE_PROFILE_SHA256
from paperworkshell.domain.pdf_export import (
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportVerificationReport,
)
from paperworkshell.domain.searchable_pdf_export import (
    DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
    SEARCHABLE_PDF_PROFILE_VERSION,
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportOutcome,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
    build_searchable_pdf_export_plan,
    reject_searchable_pdf_export,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = (
    PROJECT_ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
)
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
IMAGE_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000002")
PDF_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
IMAGE_PAGE_ID = UUID("30000000-0000-4000-8000-000000000003")
PDF_PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000099")


def _document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(REPRESENTATIVE_MANIFEST.read_text("utf-8"))
    # Overlays are owned by Milestone 3. Searchable output must reject an active
    # overlay rather than silently drop it, so the normal PWS-0206 fixture omits
    # them explicitly.
    document["overlays"] = []
    return document


def _snapshot(
    document: dict[str, Any] | None = None,
    *,
    schema_version: int = 2,
) -> ManifestSnapshot:
    selected = _document() if document is None else document
    selected["schema_version"] = schema_version
    return ManifestSnapshot(
        case_id=CASE_ID,
        schema_version=schema_version,
        revision=selected["revision"],
        document=selected,
        sha256="f" * 64,
        cas_token="manifest-cas-token-v2",
    )


def _assert_error(
    code: SearchablePdfExportErrorCode,
    action: Callable[[], object],
) -> SearchablePdfExportError:
    with pytest.raises(SearchablePdfExportError) as raised:
        action()
    assert raised.value.code is code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    return raised.value


def _source_record(document: dict[str, Any], source_id: UUID) -> dict[str, Any]:
    return next(source for source in document["sources"] if source["source_id"] == str(source_id))


def _base_report(plan: SearchablePdfExportPlan) -> PdfExportVerificationReport:
    counted = {
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    }
    codes: tuple[PdfExportCheckCode, ...] = (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    )
    if plan.requires_unsigned_derivative:
        codes += (PdfExportCheckCode.UNSIGNED_DERIVATIVE,)
    return PdfExportVerificationReport(
        semantic_fingerprint=plan.semantic_fingerprint,
        output_sha256="e" * 64,
        output_byte_size=8_192,
        page_count=plan.page_count,
        checks=tuple(
            PdfExportCheck(code, plan.page_count if code in counted else None) for code in codes
        ),
        unsigned_derivative=plan.requires_unsigned_derivative,
    )


def _committed_outcome(
    report: PdfExportVerificationReport,
    *,
    status: PdfExportOutcomeStatus = PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
) -> PdfExportOutcome:
    payload = b'{"synthetic":"searchable export retry"}\n'
    retry = PdfExportHistoryRetryReceipt(
        case_id=CASE_ID,
        export_id=EXPORT_ID,
        report=report,
        record_sha256=hashlib.sha256(payload).hexdigest(),
        record_payload=payload,
    )
    return PdfExportOutcome(
        export_id=EXPORT_ID,
        status=status,
        report=report,
        destination_reopened=True,
        history_retry=retry,
    )


def _eligibility(
    plan: SearchablePdfExportPlan,
    index: int,
    code: SearchablePdfPageEligibilityCode,
) -> SearchablePdfPageEligibility:
    page = plan.pages[index]
    return SearchablePdfPageEligibility(page.output_page_index, page.page_id, code)


def _outcome(
    plan: SearchablePdfExportPlan,
    index: int,
    eligibility: SearchablePdfPageEligibilityCode,
    outcome: SearchablePdfPageOutcomeCode,
) -> SearchablePdfPageOutcome:
    facts = {
        SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED: (
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
            None,
        ),
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED: (
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
            None,
        ),
        SearchablePdfPageOutcomeCode.OCR_NO_TEXT_RETAINED: (
            SearchablePdfPageSearchability.NOT_VERIFIED,
            SearchablePdfPageFailureCode.NO_EXTRACTABLE_TEXT,
        ),
        SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED: (
            SearchablePdfPageSearchability.NOT_VERIFIED,
            SearchablePdfPageFailureCode.OCR_ENGINE_FAILED,
        ),
        SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED: (
            SearchablePdfPageSearchability.NOT_VERIFIED,
            SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED,
        ),
    }
    searchability, failure = facts[outcome]
    return SearchablePdfPageOutcome(
        _eligibility(plan, index, eligibility),
        outcome,
        searchability,
        failure,
    )


def test_manifest_v2_plan_preserves_mixed_order_and_every_projected_transform() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())

    assert plan.case_id == CASE_ID
    assert tuple(source.source_id for source in plan.sources) == (
        IMAGE_SOURCE_ID,
        PDF_SOURCE_ID,
    )
    assert tuple(page.page_id for page in plan.pages) == (IMAGE_PAGE_ID, PDF_PAGE_ID)
    image_page, pdf_page = plan.pages
    assert image_page.output_page_index == 0
    assert image_page.quarter_turns_clockwise == 1
    assert image_page.crop is not None
    assert image_page.crop.to_manifest() == {
        "coordinate_space": "normalized_page_millionths_v1",
        "x": 50_000,
        "y": 50_000,
        "width": 900_000,
        "height": 900_000,
    }
    assert image_page.deskew_angle_millidegrees_clockwise == -1_250
    assert image_page.contrast_factor_millionths == 1_150_000
    assert image_page.background_cleanup_strength_millionths == 250_000
    assert image_page.has_pixel_transform_intent
    assert not pdf_page.has_pixel_transform_intent
    assert plan.contains_signature_markers
    assert not plan.contains_unknown_signature_state
    assert not plan.contains_signature_uncertainty
    assert plan.requires_unsigned_derivative
    assert len(plan.semantic_fingerprint) == 64

    representation = repr(plan) + repr(plan.sources) + repr(plan.profile)
    for private in (
        "Synthetic application form.pdf",
        "Synthetic supporting image.png",
        "a" * 64,
        "b" * 64,
        "f" * 64,
        TRUSTED_OCR_ENGINE_PROFILE_SHA256,
        "manifest-cas-token-v2",
    ):
        assert private not in representation


@pytest.mark.parametrize(
    "mutation",
    (
        "signature",
        "media",
        "transform",
        "engine",
        "text_profile",
        "language",
    ),
)
def test_semantic_fingerprint_binds_source_state_transform_and_complete_profile(
    mutation: str,
) -> None:
    baseline = build_searchable_pdf_export_plan(_snapshot())
    document = _document()
    profile = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE
    if mutation == "signature":
        _source_record(document, PDF_SOURCE_ID)["signature_state"] = "none_detected"
    elif mutation == "media":
        _source_record(document, IMAGE_SOURCE_ID)["detected_media_type"] = "image/jpeg"
    elif mutation == "transform":
        document["page_operations"][-1]["payload"]["strength_millionths"] = 250_001
    elif mutation == "engine":
        profile = SearchablePdfExportProfile(ocr_engine_profile_sha256="1" * 64)
    elif mutation == "text_profile":
        profile = SearchablePdfExportProfile(text_presence_profile_id="pdfium-text-v2")
    else:
        profile = SearchablePdfExportProfile(ocr_language_set_id="eng-deu-v1")

    variant = build_searchable_pdf_export_plan(_snapshot(document), profile=profile)

    assert variant.semantic_fingerprint != baseline.semantic_fingerprint


def test_same_semantics_produce_the_same_plan_fingerprint() -> None:
    first = build_searchable_pdf_export_plan(_snapshot())
    second = build_searchable_pdf_export_plan(_snapshot(copy.deepcopy(_document())))

    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.pages == second.pages
    assert first.sources == second.sources


@pytest.mark.parametrize(
    ("encryption", "code"),
    (
        ("encrypted", SearchablePdfExportErrorCode.SOURCE_ENCRYPTED),
        ("unknown", SearchablePdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN),
        ("not_checked", SearchablePdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN),
        ("not_applicable", SearchablePdfExportErrorCode.SOURCE_INCONSISTENT),
    ),
)
def test_pdf_encryption_states_fail_closed(
    encryption: str, code: SearchablePdfExportErrorCode
) -> None:
    document = _document()
    _source_record(document, PDF_SOURCE_ID)["encryption_state"] = encryption

    _assert_error(code, lambda: build_searchable_pdf_export_plan(_snapshot(document)))


def test_legacy_manifest_and_active_overlay_are_not_silently_reinterpreted() -> None:
    legacy = _document()
    _assert_error(
        SearchablePdfExportErrorCode.VERSION_UNSUPPORTED,
        lambda: build_searchable_pdf_export_plan(_snapshot(legacy, schema_version=1)),
    )


@pytest.mark.parametrize("signature", ("markers_detected", "unknown"))
def test_signed_or_unknown_pdf_with_pixel_transform_fails_plan(signature: str) -> None:
    document = _document()
    _source_record(document, PDF_SOURCE_ID)["signature_state"] = signature
    document["page_operations"].append(
        {
            "operation_id": "50000000-0000-4000-8000-000000000099",
            "operation_version": 2,
            "sequence": 9,
            "payload": {
                "kind": "deskew",
                "page_id": str(PDF_PAGE_ID),
                "angle_millidegrees_clockwise": 500,
            },
        }
    )

    _assert_error(
        SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        lambda: build_searchable_pdf_export_plan(_snapshot(document)),
    )

    active_overlay = _document()
    active_overlay["overlays"] = json.loads(REPRESENTATIVE_MANIFEST.read_text("utf-8"))["overlays"]
    _assert_error(
        SearchablePdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        lambda: build_searchable_pdf_export_plan(_snapshot(active_overlay)),
    )


def test_image_source_requires_one_page_and_not_applicable_pdf_states() -> None:
    document = _document()
    image = _source_record(document, IMAGE_SOURCE_ID)
    image["page_count"] = 2

    _assert_error(
        SearchablePdfExportErrorCode.MANIFEST_INVALID,
        lambda: build_searchable_pdf_export_plan(_snapshot(document)),
    )

    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfSource(
            source_id=IMAGE_SOURCE_ID,
            media_type=SearchablePdfSourceMediaType.PNG,
            byte_size=100,
            page_count=1,
            sha256="a" * 64,
            signature_state=SearchablePdfSourceSignatureState.NONE_DETECTED,
            encryption_state=SearchablePdfSourceEncryptionState.NOT_APPLICABLE,
        ),
    )


def test_profile_is_versioned_token_bounded_and_redacts_engine_identity() -> None:
    assert DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE.profile_version == (SEARCHABLE_PDF_PROFILE_VERSION)
    assert TRUSTED_OCR_ENGINE_PROFILE_SHA256 not in repr(DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE)

    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfExportProfile(text_presence_profile_id="../../private"),
    )
    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfExportProfile(ocr_engine_profile_sha256="not-a-digest"),
    )
    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfExportProfile(profile_version=2),
    )


def test_plan_rejects_noncontiguous_pages_and_non_first_use_source_order() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    second = plan.pages[1]
    wrong_index = SearchablePdfPage(
        output_page_index=0,
        page_id=second.page_id,
        source_id=second.source_id,
        source_page_index=second.source_page_index,
        quarter_turns_clockwise=second.quarter_turns_clockwise,
        crop=second.crop,
        deskew_angle_millidegrees_clockwise=second.deskew_angle_millidegrees_clockwise,
        contrast_factor_millionths=second.contrast_factor_millionths,
        background_cleanup_strength_millionths=(second.background_cleanup_strength_millionths),
    )
    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=plan.sources,
            pages=(plan.pages[0], wrong_index),
            profile=plan.profile,
        ),
    )
    _assert_error(
        SearchablePdfExportErrorCode.MODEL_INVALID,
        lambda: SearchablePdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=tuple(reversed(plan.sources)),
            pages=plan.pages,
            profile=plan.profile,
        ),
    )


@pytest.mark.parametrize(
    ("eligibility", "outcome"),
    (
        (
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
        (
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_NO_TEXT_RETAINED,
        ),
        (
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
        ),
    ),
)
def test_ocr_eligible_page_has_exact_success_or_review_outcomes(
    eligibility: SearchablePdfPageEligibilityCode,
    outcome: SearchablePdfPageOutcomeCode,
) -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())

    value = _outcome(plan, 0, eligibility, outcome)

    assert value.has_extractable_text is (outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED)


def test_outcome_rejects_false_searchability_failure_and_eligibility_combinations() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    image_eligibility = _eligibility(
        plan,
        0,
        SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
    )
    _assert_error(
        SearchablePdfExportErrorCode.RESULT_INVALID,
        lambda: SearchablePdfPageOutcome(
            image_eligibility,
            SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
            SearchablePdfPageFailureCode.OCR_ENGINE_FAILED,
        ),
    )
    _assert_error(
        SearchablePdfExportErrorCode.RESULT_INVALID,
        lambda: SearchablePdfPageOutcome(
            image_eligibility,
            SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
        ),
    )


def test_verified_report_combines_base_checks_with_limited_per_page_text_facts() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    outcomes = (
        _outcome(
            plan,
            0,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
        _outcome(
            plan,
            1,
            SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
            SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        ),
    )

    report = SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256="e" * 64,
        output_byte_size=8_192,
        page_outcomes=outcomes,
    )

    assert report.all_pages_have_extractable_text
    assert report.existing_text_page_count == 1
    assert report.ocr_text_page_count == 1
    assert report.needs_review_page_count == 0
    assert report.base_report.unsigned_derivative
    assert report.claims[-1] == (
        "The supported text-presence profile found non-whitespace extractable text on 2 of 2 pages."
    )
    claims = " ".join(report.claims).casefold()
    for prohibited in (
        "ocr is correct",
        "fully searchable",
        "pdf/ua",
        "portal-ready",
        "still signed",
    ):
        assert prohibited not in claims


def test_page_local_ocr_failure_is_reported_without_a_searchability_claim() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    report = SearchablePdfVerificationReport(
        plan,
        _base_report(plan),
        (
            _outcome(
                plan,
                0,
                SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
                SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
            ),
            _outcome(
                plan,
                1,
                SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
                SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
            ),
        ),
    )

    assert not report.all_pages_have_extractable_text
    assert report.ocr_text_page_count == 0
    assert report.needs_review_page_count == 1
    assert report.claims[-1] == ("1 page has no verified extractable-text result and needs review.")


@pytest.mark.parametrize("signature", ("markers_detected", "unknown"))
def test_signed_or_unknown_pdf_with_quarter_turn_can_only_preserve_or_block(
    signature: str,
) -> None:
    document = _document()
    _source_record(document, PDF_SOURCE_ID)["signature_state"] = signature
    document["page_operations"].append(
        {
            "operation_id": "50000000-0000-4000-8000-000000000098",
            "operation_version": 1,
            "sequence": 9,
            "payload": {
                "kind": "rotate",
                "page_id": str(PDF_PAGE_ID),
                "quarter_turns_clockwise": 1,
            },
        }
    )
    plan = build_searchable_pdf_export_plan(_snapshot(document))
    assert plan.pages[1].quarter_turns_clockwise == 1
    assert not plan.pages[1].has_pixel_transform_intent
    assert plan.requires_unsigned_derivative
    assert plan.contains_signature_uncertainty is (signature == "unknown")
    blocked = _outcome(
        plan,
        1,
        SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE,
        SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
    )
    report = SearchablePdfVerificationReport(
        plan,
        _base_report(plan),
        (
            _outcome(
                plan,
                0,
                SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
                SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
            ),
            blocked,
        ),
    )
    assert report.needs_review_page_count == 1
    assert report.base_report.unsigned_derivative

    invalid_ocr = (
        _outcome(
            plan,
            0,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
        _outcome(
            plan,
            1,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_ONLY_PDF,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
    )
    _assert_error(
        SearchablePdfExportErrorCode.REPORT_INVALID,
        lambda: SearchablePdfVerificationReport(plan, _base_report(plan), invalid_ocr),
    )


@pytest.mark.parametrize(
    ("signature", "contains_markers"),
    (
        ("markers_detected", True),
        ("unknown", False),
        ("not_checked", False),
    ),
)
def test_unsigned_derivative_claim_names_only_the_authenticated_signature_reason(
    signature: str,
    contains_markers: bool,
) -> None:
    document = _document()
    _source_record(document, PDF_SOURCE_ID)["signature_state"] = signature
    plan = build_searchable_pdf_export_plan(_snapshot(document))
    report = SearchablePdfVerificationReport(
        plan,
        _base_report(plan),
        (
            _outcome(
                plan,
                0,
                SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
                SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
            ),
            _outcome(
                plan,
                1,
                SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE,
                SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
            ),
        ),
    )

    claim = next(value for value in report.claims if "unsigned derivative" in value)
    if contains_markers:
        assert "contained digital-signature markers" in claim
        assert "could not confirm" not in claim
    else:
        assert "could not confirm the digital-signature-marker state" in claim
        assert "contained digital-signature markers" not in claim
        assert "markers were detected" not in claim
    assert plan.contains_signature_markers is contains_markers
    assert plan.contains_signature_uncertainty is (not contains_markers)


def test_report_rejects_page_order_fingerprint_count_and_unsigned_mismatch() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    outcomes = (
        _outcome(
            plan,
            0,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
        _outcome(
            plan,
            1,
            SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
            SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        ),
    )
    base = _base_report(plan)
    _assert_error(
        SearchablePdfExportErrorCode.REPORT_INVALID,
        lambda: SearchablePdfVerificationReport(plan, base, tuple(reversed(outcomes))),
    )
    wrong_fingerprint = PdfExportVerificationReport(
        semantic_fingerprint="0" * 64,
        output_sha256=base.output_sha256,
        output_byte_size=base.output_byte_size,
        page_count=base.page_count,
        checks=base.checks,
        unsigned_derivative=base.unsigned_derivative,
    )
    _assert_error(
        SearchablePdfExportErrorCode.REPORT_INVALID,
        lambda: SearchablePdfVerificationReport(plan, wrong_fingerprint, outcomes),
    )
    _assert_error(
        SearchablePdfExportErrorCode.REPORT_INVALID,
        lambda: SearchablePdfVerificationReport(plan, base, outcomes[:1]),
    )
    unsigned_mismatch = PdfExportVerificationReport(
        semantic_fingerprint=base.semantic_fingerprint,
        output_sha256=base.output_sha256,
        output_byte_size=base.output_byte_size,
        page_count=base.page_count,
        checks=base.checks[:-1],
        unsigned_derivative=False,
    )
    _assert_error(
        SearchablePdfExportErrorCode.REPORT_INVALID,
        lambda: SearchablePdfVerificationReport(plan, unsigned_mismatch, outcomes),
    )


def test_committed_outcome_wraps_exact_base_report_and_delegates_state() -> None:
    plan = build_searchable_pdf_export_plan(_snapshot())
    page_outcomes = (
        _outcome(
            plan,
            0,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        ),
        _outcome(
            plan,
            1,
            SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
            SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        ),
    )
    report = SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256="d" * 64,
        output_byte_size=9_001,
        page_outcomes=page_outcomes,
    )
    base_outcome = _committed_outcome(report.base_report)
    outcome = SearchablePdfExportOutcome(base_outcome, report)

    assert outcome.export_id == EXPORT_ID
    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert outcome.destination_reopened
    assert outcome.history_retry is base_outcome.history_retry
    assert outcome.history_retry_required
    assert not outcome.export_retry_required
    assert outcome.checks == base_outcome.checks
    assert outcome.claims[-1] == base_outcome.claims[-1]
    assert outcome.safe_message == base_outcome.safe_message
    assert str(EXPORT_ID) not in repr(outcome)
    assert "d" * 64 not in repr(outcome)

    other_report = SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256="c" * 64,
        output_byte_size=9_001,
        page_outcomes=page_outcomes,
    )
    _assert_error(
        SearchablePdfExportErrorCode.RESULT_INVALID,
        lambda: SearchablePdfExportOutcome(base_outcome, other_report),
    )


def test_errors_and_representations_never_retain_private_values() -> None:
    private = "C:\\Applicants\\private-name.pdf OCR-CONTENT-CANARY"
    error = SearchablePdfExportError(
        SearchablePdfExportErrorCode.MANIFEST_INVALID,
        private,
    )

    assert error.context == "searchable_pdf.export"
    assert private not in str(error)
    assert private not in repr(error)
    assert "OCR-CONTENT-CANARY" not in error.args

    with pytest.raises(SearchablePdfExportError) as raised:
        try:
            raise RuntimeError(private)
        except RuntimeError:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.private",
            )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert private not in repr(raised.value)
