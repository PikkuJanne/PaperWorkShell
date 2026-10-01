from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportHistoryRetryReceipt,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
)
from paperworkshell.domain.pdf_export_retry import (
    PDF_EXPORT_RETRY_JOURNAL_VERSION,
    PdfExportRetryInventory,
    PdfExportRetryJournalEntry,
    PdfExportRetryPhase,
    canonical_pdf_export_retry_entry_bytes,
    decode_pdf_export_retry_entry,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000713")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000713")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000713")
EXPORT_ID = UUID("40000000-0000-4000-8000-000000000713")
SECOND_EXPORT_ID = UUID("50000000-0000-4000-8000-000000000713")


def _receipt(*, export_id: UUID = EXPORT_ID) -> PdfExportHistoryRetryReceipt:
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=3,
        manifest_sha256="b" * 64,
        manifest_cas_token="retry_manifest_cas_token_0001",
        sources=(
            PdfExportSource(
                source_id=SOURCE_ID,
                byte_size=99,
                page_count=1,
                signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
                sha256="a" * 64,
            ),
        ),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=PAGE_ID,
                source_id=SOURCE_ID,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_90,
            ),
        ),
    )
    report = PdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256="c" * 64,
        output_byte_size=1_234,
    )
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=export_id,
        report=report,
        recorded_at=datetime(2026, 8, 9, 12, 34, 56, 123456, tzinfo=UTC),
    )
    return build_pdf_export_history_retry_receipt(plan, record, report)


@pytest.mark.parametrize("phase", tuple(PdfExportRetryPhase))
def test_retry_journal_round_trip_is_canonical_and_reconstructs_report(
    phase: PdfExportRetryPhase,
) -> None:
    receipt = _receipt()
    entry = PdfExportRetryJournalEntry(phase, receipt)

    payload = canonical_pdf_export_retry_entry_bytes(entry)
    decoded = decode_pdf_export_retry_entry(payload)
    document = json.loads(payload)

    assert decoded == entry
    assert decoded.receipt == receipt
    assert canonical_pdf_export_retry_entry_bytes(decoded) == payload
    assert tuple(document) == tuple(sorted(document))
    assert set(document) == {
        "case_id",
        "journal_version",
        "phase",
        "receipt_version",
        "record",
        "record_sha256",
    }
    assert "path" not in payload.decode("ascii")
    assert "filename" not in payload.decode("ascii")


def test_retry_journal_rejects_future_noncanonical_and_misbound_payloads() -> None:
    payload = canonical_pdf_export_retry_entry_bytes(
        PdfExportRetryJournalEntry(PdfExportRetryPhase.COMMITTED, _receipt())
    )
    canonical = json.loads(payload)
    variants: list[tuple[dict[str, object], PdfExportHistoryErrorCode, UUID | None]] = []

    future = dict(canonical)
    future["journal_version"] = PDF_EXPORT_RETRY_JOURNAL_VERSION + 1
    variants.append((future, PdfExportHistoryErrorCode.VERSION_UNSUPPORTED, None))

    wrong_case = dict(canonical)
    wrong_case["case_id"] = str(SECOND_EXPORT_ID)
    variants.append((wrong_case, PdfExportHistoryErrorCode.RECORD_MISMATCH, CASE_ID))

    wrong_digest = dict(canonical)
    wrong_digest["record_sha256"] = "0" * 64
    variants.append((wrong_digest, PdfExportHistoryErrorCode.RECORD_MISMATCH, None))

    for document, code, expected_case_id in variants:
        hostile = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii")
        with pytest.raises(PdfExportHistoryError) as raised:
            decode_pdf_export_retry_entry(hostile, expected_case_id=expected_case_id)
        assert raised.value.code is code

    with pytest.raises(PdfExportHistoryError) as raised:
        decode_pdf_export_retry_entry(payload + b"\n")
    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID


def test_retry_journal_rejects_a_semantically_forged_receipt() -> None:
    receipt = _receipt()
    forged = PdfExportHistoryRetryReceipt(
        case_id=receipt.case_id,
        export_id=receipt.export_id,
        report=PdfExportVerificationReport(
            semantic_fingerprint=receipt.report.semantic_fingerprint,
            output_sha256="d" * 64,
            output_byte_size=receipt.report.output_byte_size,
            page_count=receipt.report.page_count,
            checks=receipt.report.checks,
            unsigned_derivative=receipt.report.unsigned_derivative,
        ),
        record_sha256=receipt.record_sha256,
        record_payload=receipt.record_payload,
    )

    with pytest.raises(PdfExportHistoryError) as raised:
        PdfExportRetryJournalEntry(PdfExportRetryPhase.PREPARED, forged)

    assert raised.value.code is PdfExportHistoryErrorCode.RECORD_MISMATCH


@pytest.mark.parametrize("phase", tuple(PdfExportRetryPhase))
def test_v2_report_journal_preserves_exact_v1_facts_without_persisting_optimization(
    phase: PdfExportRetryPhase,
) -> None:
    legacy = _receipt()
    optimization = PdfOptimizationReport(
        PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
        PdfOptimizationOutcome.OPTIMIZED,
        2_468,
        1_234,
        1_234,
        1,
    )
    current = replace(
        legacy,
        report=replace(legacy.report, report_version=2, optimization=optimization),
    )
    payload = canonical_pdf_export_retry_entry_bytes(PdfExportRetryJournalEntry(phase, current))

    assert payload == canonical_pdf_export_retry_entry_bytes(
        PdfExportRetryJournalEntry(phase, legacy)
    )
    recovered = decode_pdf_export_retry_entry(payload)
    assert recovered.receipt == legacy
    assert recovered.receipt.report.optimization is None
    assert recovered.receipt.report.claims == legacy.report.claims

    forged = replace(current, report=replace(current.report, output_sha256="d" * 64))
    with pytest.raises(PdfExportHistoryError) as captured:
        PdfExportRetryJournalEntry(phase, forged)
    assert captured.value.code is PdfExportHistoryErrorCode.RECORD_MISMATCH


def test_retry_inventory_is_sorted_bounded_and_reserves_both_phases() -> None:
    receipt = _receipt()
    inventory = PdfExportRetryInventory(
        committed=(receipt,),
        prepared=(_receipt(export_id=SECOND_EXPORT_ID),),
        invalid_export_ids=(PAGE_ID,),
        invalid_entries=2,
    )

    assert inventory.committed == (receipt,)
    assert inventory.prepared_export_ids == (SECOND_EXPORT_ID,)
    assert inventory.reserved_export_ids == frozenset({EXPORT_ID, SECOND_EXPORT_ID, PAGE_ID})
    assert "record_payload" not in repr(inventory)

    with pytest.raises(PdfExportHistoryError):
        PdfExportRetryInventory(
            committed=(receipt,),
            prepared=(receipt,),
        )
