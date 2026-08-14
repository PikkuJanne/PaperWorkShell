from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    MAX_PDF_EXPORT_SOURCES,
    PDF_EXPORT_PLAN_VERSION,
    PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION,
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPage,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
    build_pdf_export_plan,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000111")
SOURCE_A = UUID("20000000-0000-4000-8000-000000000111")
SOURCE_B = UUID("20000000-0000-4000-8000-000000000112")
PAGE_A0 = UUID("30000000-0000-4000-8000-000000000111")
PAGE_A1 = UUID("30000000-0000-4000-8000-000000000112")
PAGE_B0 = UUID("30000000-0000-4000-8000-000000000113")
OPERATION_IDS = (
    UUID("40000000-0000-4000-8000-000000000111"),
    UUID("40000000-0000-4000-8000-000000000112"),
    UUID("40000000-0000-4000-8000-000000000113"),
    UUID("40000000-0000-4000-8000-000000000114"),
    UUID("40000000-0000-4000-8000-000000000115"),
)
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000111")


def _history_retry(report: PdfExportVerificationReport) -> PdfExportHistoryRetryReceipt:
    payload = b'{"synthetic":"path-free retry record"}\n'
    return PdfExportHistoryRetryReceipt(
        case_id=CASE_ID,
        export_id=EXPORT_ID,
        report=report,
        record_sha256=hashlib.sha256(payload).hexdigest(),
        record_payload=payload,
    )


def _source(
    source_id: UUID,
    *,
    byte_size: int,
    page_count: int,
    sha256: str,
    signature_state: str = "none_detected",
    encryption_state: str = "unencrypted",
    media_type: str = "application/pdf",
) -> dict[str, object]:
    return {
        "source_id": str(source_id),
        "display_name": "Synthetic export source.pdf",
        "storage_name": f"{source_id}.bin",
        "detected_media_type": media_type,
        "byte_size": byte_size,
        "page_count": page_count,
        "sha256": sha256,
        "signature_state": signature_state,
        "encryption_state": encryption_state,
        "import_state": "complete",
        "imported_at": "2026-08-05T10:00:00Z",
    }


def _operation(
    sequence: int,
    payload: dict[str, object],
    *,
    operation_version: int = 1,
) -> dict[str, object]:
    return {
        "operation_id": str(OPERATION_IDS[sequence - 1]),
        "operation_version": operation_version,
        "sequence": sequence,
        "payload": payload,
    }


def _document(*, revision: int = 7) -> dict[str, object]:
    base = (PAGE_A0, PAGE_A1, PAGE_B0)
    final = (PAGE_B0, PAGE_A1, PAGE_A0)
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-05T09:00:00Z",
        "updated_at": "2026-08-05T10:00:00Z",
        "template_ref": None,
        "sources": [
            _source(SOURCE_A, byte_size=111, page_count=2, sha256="a" * 64),
            _source(
                SOURCE_B,
                byte_size=222,
                page_count=1,
                sha256="b" * 64,
                signature_state="markers_detected",
            ),
        ],
        "pages": [
            {
                "page_id": str(PAGE_A0),
                "source_id": str(SOURCE_A),
                "source_page_index": 0,
            },
            {
                "page_id": str(PAGE_A1),
                "source_id": str(SOURCE_A),
                "source_page_index": 1,
            },
            {
                "page_id": str(PAGE_B0),
                "source_id": str(SOURCE_B),
                "source_page_index": 0,
            },
        ],
        "page_sequence_base": [str(page_id) for page_id in base],
        "page_sequence": [str(page_id) for page_id in final],
        "page_operations": [
            _operation(
                1,
                {
                    "kind": "rotate",
                    "page_id": str(PAGE_A1),
                    "quarter_turns_clockwise": 1,
                },
            ),
            _operation(
                2,
                {
                    "kind": "rotate",
                    "page_id": str(PAGE_A1),
                    "quarter_turns_clockwise": 2,
                },
            ),
            _operation(
                3,
                {
                    "kind": "change_sequence",
                    "reason": "reorder",
                    "before_page_ids": [str(page_id) for page_id in base],
                    "after_page_ids": [str(page_id) for page_id in final],
                },
            ),
        ],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(
    document: dict[str, object] | None = None,
    *,
    case_id: UUID = CASE_ID,
    sha256: str = "c" * 64,
    cas_token: str = "pdf_export_cas_0000001",
) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        case_id,
        cast(int, selected["schema_version"]),
        cast(int, selected["revision"]),
        selected,
        sha256,
        cas_token,
    )


def _assert_error(
    code: PdfExportErrorCode,
    action: Callable[[], object],
) -> PdfExportError:
    with pytest.raises(PdfExportError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def _source_record(document: dict[str, object], index: int) -> dict[str, object]:
    return cast(dict[str, object], cast(list[object], document["sources"])[index])


def _overlay(page_id: UUID) -> dict[str, object]:
    return {
        "overlay_id": "60000000-0000-4000-8000-000000000111",
        "overlay_version": 1,
        "page_id": str(page_id),
        "box": {
            "coordinate_space": "normalized_page_millionths_v1",
            "x": 0,
            "y": 0,
            "width": 100_000,
            "height": 100_000,
        },
        "z_index": 0,
        "payload": {
            "kind": "highlight",
            "shape": "rectangle",
            "fill_color": "#ffff0080",
            "stroke_color": "#000000ff",
            "intent": "visual_overlay_only_not_permanent_removal",
        },
    }


def test_plan_resolves_first_use_sources_exact_order_indices_and_accumulated_rotation() -> None:
    snapshot = _snapshot()

    plan = build_pdf_export_plan(snapshot)

    assert plan.case_id == CASE_ID
    assert plan.manifest_revision == 7
    assert plan.manifest_sha256 == "c" * 64
    assert plan.manifest_cas_token == "pdf_export_cas_0000001"
    assert plan.profile == DEFAULT_PDF_EXPORT_PROFILE
    assert plan.plan_version == PDF_EXPORT_PLAN_VERSION
    assert tuple(source.source_id for source in plan.sources) == (SOURCE_B, SOURCE_A)
    assert tuple(page.output_page_index for page in plan.pages) == (0, 1, 2)
    assert tuple(page.page_id for page in plan.pages) == (PAGE_B0, PAGE_A1, PAGE_A0)
    assert tuple(page.source_page_index for page in plan.pages) == (0, 1, 0)
    assert tuple(page.additional_rotation for page in plan.pages) == (
        PdfExportRotation.DEGREES_0,
        PdfExportRotation.DEGREES_270,
        PdfExportRotation.DEGREES_0,
    )
    assert plan.page_count == 3
    assert plan.contains_signature_markers


def test_fingerprint_is_the_canonical_path_free_semantic_payload() -> None:
    plan = build_pdf_export_plan(_snapshot())
    canonical = {
        "fingerprint_version": PDF_EXPORT_SEMANTIC_FINGERPRINT_VERSION,
        "plan_version": 1,
        "profile": {
            "profile_version": 1,
            "assembly": "pikepdf_page_copy_v1",
            "render": "pdfium_rgba8_crop_white_stored_appearances_v1",
            "qpdf": "qpdf_check_no_recovery_no_warnings_v1",
            "encryption_policy": "must_be_unencrypted",
        },
        "pages": [
            {
                "source_sha256": "b" * 64,
                "source_byte_size": 222,
                "source_page_count": 1,
                "source_page_index": 0,
                "additional_rotation_degrees": 0,
            },
            {
                "source_sha256": "a" * 64,
                "source_byte_size": 111,
                "source_page_count": 2,
                "source_page_index": 1,
                "additional_rotation_degrees": 270,
            },
            {
                "source_sha256": "a" * 64,
                "source_byte_size": 111,
                "source_page_count": 2,
                "source_page_index": 0,
                "additional_rotation_degrees": 0,
            },
        ],
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")

    assert plan.semantic_fingerprint == hashlib.sha256(encoded).hexdigest()


def test_fingerprint_excludes_case_revision_ids_timestamps_signature_state_and_source_order() -> (
    None
):
    baseline = build_pdf_export_plan(_snapshot())
    changed = copy.deepcopy(_document())
    replacement_case = UUID("10000000-0000-4000-8000-000000000211")
    replacement_sources = {
        str(SOURCE_A): UUID("20000000-0000-4000-8000-000000000211"),
        str(SOURCE_B): UUID("20000000-0000-4000-8000-000000000212"),
    }
    replacement_pages = {
        str(PAGE_A0): UUID("30000000-0000-4000-8000-000000000211"),
        str(PAGE_A1): UUID("30000000-0000-4000-8000-000000000212"),
        str(PAGE_B0): UUID("30000000-0000-4000-8000-000000000213"),
    }
    changed["case_id"] = str(replacement_case)
    changed["revision"] = 91
    changed["created_at"] = "2031-01-01T00:00:00Z"
    changed["updated_at"] = "2031-01-02T00:00:00Z"
    sources = cast(list[dict[str, object]], changed["sources"])
    for source in sources:
        old_source_id = cast(str, source["source_id"])
        source["source_id"] = str(replacement_sources[old_source_id])
        source["storage_name"] = f"{replacement_sources[old_source_id]}.bin"
        source["display_name"] = "A completely different synthetic label.pdf"
        source["imported_at"] = "2031-01-01T01:00:00Z"
        source["signature_state"] = "none_detected"
    sources.reverse()
    for page in cast(list[dict[str, object]], changed["pages"]):
        page["page_id"] = str(replacement_pages[cast(str, page["page_id"])])
        page["source_id"] = str(replacement_sources[cast(str, page["source_id"])])
    for key in ("page_sequence_base", "page_sequence"):
        changed[key] = [
            str(replacement_pages[page_id]) for page_id in cast(list[str], changed[key])
        ]
    for sequence, operation in enumerate(
        cast(list[dict[str, object]], changed["page_operations"]), start=1
    ):
        operation["operation_id"] = str(UUID(f"4{sequence:07x}-0000-4000-8000-000000000211"))
        payload = cast(dict[str, object], operation["payload"])
        if payload["kind"] == "rotate":
            payload["page_id"] = str(replacement_pages[cast(str, payload["page_id"])])
        else:
            for key in ("before_page_ids", "after_page_ids"):
                payload[key] = [
                    str(replacement_pages[page_id]) for page_id in cast(list[str], payload[key])
                ]

    variant = build_pdf_export_plan(
        _snapshot(
            changed,
            case_id=replacement_case,
            sha256="d" * 64,
            cas_token="pdf_export_cas_0000002",
        )
    )

    assert variant.semantic_fingerprint == baseline.semantic_fingerprint
    assert not variant.contains_signature_markers


@pytest.mark.parametrize("mutation", ("hash", "size", "source_index", "order", "rotation"))
def test_each_output_semantic_change_invalidates_the_fingerprint(mutation: str) -> None:
    baseline = build_pdf_export_plan(_snapshot())
    changed = copy.deepcopy(_document())
    if mutation == "hash":
        _source_record(changed, 0)["sha256"] = "e" * 64
    elif mutation == "size":
        _source_record(changed, 0)["byte_size"] = 112
    elif mutation == "source_index":
        pages = cast(list[dict[str, object]], changed["pages"])
        pages[0]["source_page_index"] = 1
        pages[1]["source_page_index"] = 0
    elif mutation == "order":
        operation = cast(dict[str, object], cast(list[object], changed["page_operations"])[2])
        payload = cast(dict[str, object], operation["payload"])
        reordered = [str(PAGE_A1), str(PAGE_B0), str(PAGE_A0)]
        payload["after_page_ids"] = reordered
        changed["page_sequence"] = reordered
    else:
        operation = cast(dict[str, object], cast(list[object], changed["page_operations"])[1])
        cast(dict[str, object], operation["payload"])["quarter_turns_clockwise"] = 1

    assert build_pdf_export_plan(_snapshot(changed)).semantic_fingerprint != (
        baseline.semantic_fingerprint
    )


def test_empty_projected_sequence_is_rejected() -> None:
    document = _document()
    operations = cast(list[dict[str, object]], document["page_operations"])
    operations.append(
        _operation(
            4,
            {
                "kind": "change_sequence",
                "reason": "remove",
                "before_page_ids": [str(PAGE_B0), str(PAGE_A1), str(PAGE_A0)],
                "after_page_ids": [],
            },
        )
    )
    document["page_sequence"] = []

    _assert_error(PdfExportErrorCode.PLAN_EMPTY, lambda: build_pdf_export_plan(_snapshot(document)))


def test_non_pdf_active_source_is_rejected_before_any_path_can_enter_the_plan() -> None:
    document = _document()
    source = _source_record(document, 1)
    source["detected_media_type"] = "image/png"
    source["signature_state"] = "not_applicable"
    source["encryption_state"] = "not_applicable"

    _assert_error(
        PdfExportErrorCode.SOURCE_UNSUPPORTED,
        lambda: build_pdf_export_plan(_snapshot(document)),
    )


@pytest.mark.parametrize(
    ("state", "code"),
    (
        ("encrypted", PdfExportErrorCode.SOURCE_ENCRYPTED),
        ("unknown", PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN),
        ("not_checked", PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN),
        ("not_applicable", PdfExportErrorCode.SOURCE_INCONSISTENT),
    ),
)
def test_encrypted_unchecked_unknown_and_inapplicable_pdf_states_are_rejected(
    state: str,
    code: PdfExportErrorCode,
) -> None:
    document = _document()
    _source_record(document, 1)["encryption_state"] = state

    _assert_error(code, lambda: build_pdf_export_plan(_snapshot(document)))


@pytest.mark.parametrize("state", ("unknown", "not_checked"))
def test_legacy_export_rejects_unconfirmed_signature_state_compatibly(state: str) -> None:
    document = _document()
    _source_record(document, 1)["signature_state"] = state

    error = _assert_error(
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
        lambda: build_pdf_export_plan(_snapshot(document)),
    )

    assert error.context == "plan.source_signature"
    assert "could not confirm" in error.safe_message


def test_legacy_plan_model_cannot_bypass_unknown_signature_rejection() -> None:
    plan = build_pdf_export_plan(_snapshot())
    unknown_source = replace(
        plan.sources[0],
        signature_state=PdfExportSourceSignatureState.UNKNOWN,
    )

    _assert_error(
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN,
        lambda: PdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=(unknown_source, *plan.sources[1:]),
            pages=plan.pages,
            profile=plan.profile,
        ),
    )


def test_active_crop_is_rejected_but_crop_on_a_removed_page_is_not_export_intent() -> None:
    active = _document()
    cast(list[dict[str, object]], active["page_operations"]).append(
        _operation(
            4,
            {
                "kind": "crop",
                "page_id": str(PAGE_B0),
                "box": {
                    "coordinate_space": "normalized_page_millionths_v1",
                    "x": 0,
                    "y": 0,
                    "width": 500_000,
                    "height": 500_000,
                },
            },
        )
    )
    _assert_error(
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        lambda: build_pdf_export_plan(_snapshot(active)),
    )

    inactive = copy.deepcopy(active)
    cast(list[dict[str, object]], inactive["page_operations"]).append(
        _operation(
            5,
            {
                "kind": "change_sequence",
                "reason": "remove",
                "before_page_ids": [str(PAGE_B0), str(PAGE_A1), str(PAGE_A0)],
                "after_page_ids": [str(PAGE_A1), str(PAGE_A0)],
            },
        )
    )
    inactive["page_sequence"] = [str(PAGE_A1), str(PAGE_A0)]

    plan = build_pdf_export_plan(_snapshot(inactive))
    assert tuple(page.page_id for page in plan.pages) == (PAGE_A1, PAGE_A0)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "kind": "deskew",
            "page_id": str(PAGE_B0),
            "angle_millidegrees_clockwise": 1_000,
        },
        {
            "kind": "contrast",
            "page_id": str(PAGE_B0),
            "factor_millionths": 1_100_000,
        },
        {
            "kind": "background_cleanup",
            "page_id": str(PAGE_B0),
            "strength_millionths": 100_000,
        },
    ),
)
def test_active_scan_only_transform_is_rejected_until_export_consumes_it(
    payload: dict[str, object],
) -> None:
    document = _document()
    document["schema_version"] = 2
    cast(list[dict[str, object]], document["page_operations"]).append(
        _operation(4, payload, operation_version=2)
    )

    error = _assert_error(
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        lambda: build_pdf_export_plan(_snapshot(document)),
    )

    assert error.context == "plan.scan_transform"


def test_active_overlay_is_rejected_but_overlay_on_a_removed_page_is_not_export_intent() -> None:
    active = _document()
    cast(list[object], active["overlays"]).append(_overlay(PAGE_A0))
    _assert_error(
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        lambda: build_pdf_export_plan(_snapshot(active)),
    )

    inactive = copy.deepcopy(active)
    cast(list[dict[str, object]], inactive["page_operations"]).append(
        _operation(
            4,
            {
                "kind": "change_sequence",
                "reason": "remove",
                "before_page_ids": [str(PAGE_B0), str(PAGE_A1), str(PAGE_A0)],
                "after_page_ids": [str(PAGE_B0), str(PAGE_A1)],
            },
        )
    )
    inactive["page_sequence"] = [str(PAGE_B0), str(PAGE_A1)]

    assert build_pdf_export_plan(_snapshot(inactive)).page_count == 2


@pytest.mark.parametrize("source_count", (MAX_PDF_EXPORT_SOURCES, MAX_PDF_EXPORT_SOURCES + 1))
def test_unique_source_handle_batch_limit_is_exact(source_count: int) -> None:
    document = _document()
    sources: list[dict[str, object]] = []
    pages: list[dict[str, object]] = []
    sequence: list[str] = []
    for index in range(source_count):
        source_id = UUID(f"2{index:07x}-0000-4000-8000-{index:012x}")
        page_id = UUID(f"3{index:07x}-0000-4000-8000-{index:012x}")
        sources.append(
            _source(
                source_id,
                byte_size=index + 1,
                page_count=1,
                sha256=f"{index:064x}",
            )
        )
        pages.append(
            {
                "page_id": str(page_id),
                "source_id": str(source_id),
                "source_page_index": 0,
            }
        )
        sequence.append(str(page_id))
    document["sources"] = sources
    document["pages"] = pages
    document["page_sequence_base"] = sequence
    document["page_sequence"] = sequence
    document["page_operations"] = []

    if source_count == MAX_PDF_EXPORT_SOURCES:
        assert len(build_pdf_export_plan(_snapshot(document)).sources) == source_count
    else:
        _assert_error(
            PdfExportErrorCode.LIMIT_EXCEEDED,
            lambda: build_pdf_export_plan(_snapshot(document)),
        )


def test_inconsistent_page_catalogue_is_converted_to_a_closed_export_failure() -> None:
    document = _document()
    _source_record(document, 0)["page_count"] = 1

    error = _assert_error(
        PdfExportErrorCode.MANIFEST_INVALID,
        lambda: build_pdf_export_plan(_snapshot(document)),
    )
    assert error.__context__ is None
    assert error.__cause__ is None


def test_plan_and_errors_do_not_disclose_names_paths_hashes_or_cas_values() -> None:
    canary = "Private Applicant C:/cases/private-source.pdf"
    document = _document()
    _source_record(document, 1)["display_name"] = canary
    plan = build_pdf_export_plan(_snapshot(document))
    rendered_plan = repr(plan)
    for secret in (canary, "a" * 64, "b" * 64, "c" * 64, "pdf_export_cas_0000001"):
        assert secret not in rendered_plan

    _source_record(document, 1)["encryption_state"] = canary
    error = _assert_error(
        PdfExportErrorCode.MANIFEST_INVALID,
        lambda: build_pdf_export_plan(_snapshot(document)),
    )
    for rendered_error in (str(error), repr(error), repr(error.args)):
        assert canary not in rendered_error
        assert "private-source.pdf" not in rendered_error

    closed = PdfExportError(code=canary, context=canary)  # type: ignore[arg-type]
    assert closed.code is PdfExportErrorCode.INTERNAL_ERROR
    assert closed.context == "pdf.export"
    assert canary not in str(closed)


def test_plan_is_immutable_and_does_not_retain_mutable_manifest_containers() -> None:
    document = _document()
    plan = build_pdf_export_plan(_snapshot(document))
    cast(list[object], document["sources"]).clear()
    cast(list[object], document["pages"]).clear()

    assert plan.page_count == 3
    assert len(plan.sources) == 2
    with pytest.raises(FrozenInstanceError):
        plan.manifest_revision = 8  # type: ignore[misc]


def test_direct_plan_contract_rejects_noncanonical_source_order_and_duplicate_origins() -> None:
    plan = build_pdf_export_plan(_snapshot())
    _assert_error(
        PdfExportErrorCode.MODEL_INVALID,
        lambda: PdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=tuple(reversed(plan.sources)),
            pages=plan.pages,
        ),
    )
    duplicate = PdfExportPage(
        output_page_index=1,
        page_id=PAGE_A1,
        source_id=SOURCE_B,
        source_page_index=0,
        additional_rotation=PdfExportRotation.DEGREES_0,
    )
    _assert_error(
        PdfExportErrorCode.MODEL_INVALID,
        lambda: PdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=plan.sources,
            pages=(plan.pages[0], duplicate, plan.pages[2]),
        ),
    )


def test_successful_report_has_exact_checks_concrete_claims_and_unsigned_derivative() -> None:
    plan = build_pdf_export_plan(_snapshot())

    report = PdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256="f" * 64,
        output_byte_size=1_024,
    )

    assert tuple(check.code for check in report.checks) == (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
        PdfExportCheckCode.UNSIGNED_DERIVATIVE,
    )
    assert report.claims == (
        "The exported PDF reopened successfully.",
        "The qpdf structural check completed without warnings.",
        "The exported PDF contains the expected 3 pages.",
        "The exported PDF is not encrypted.",
        "All 3 pages rendered with the verification profile.",
        "Rendered page order and rotations match the export plan for all 3 pages.",
        "The exported PDF is an unsigned derivative because at least one source "
        "contained digital-signature markers or its marker state could not be confirmed.",
    )
    claims = " ".join(report.claims).casefold()
    for prohibited in ("portal-ready", "accepted by", "legally valid", "still signed"):
        assert prohibited not in claims
    assert "f" * 64 not in repr(report)
    assert plan.semantic_fingerprint not in repr(report)


def test_report_without_signature_markers_omits_unsigned_derivative_claim() -> None:
    document = _document()
    _source_record(document, 1)["signature_state"] = "none_detected"
    report = PdfExportVerificationReport.for_verified_plan(
        build_pdf_export_plan(_snapshot(document)),
        output_sha256="f" * 64,
        output_byte_size=1,
    )

    assert not report.unsigned_derivative
    assert PdfExportCheckCode.UNSIGNED_DERIVATIVE not in tuple(
        check.code for check in report.checks
    )


def test_report_rejects_missing_reordered_or_mismatched_checks() -> None:
    plan = build_pdf_export_plan(_snapshot())
    report = PdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256="f" * 64,
        output_byte_size=1,
    )
    _assert_error(
        PdfExportErrorCode.REPORT_INVALID,
        lambda: PdfExportVerificationReport(
            semantic_fingerprint=report.semantic_fingerprint,
            output_sha256=report.output_sha256,
            output_byte_size=report.output_byte_size,
            page_count=report.page_count,
            checks=report.checks[:-1],
            unsigned_derivative=True,
        ),
    )
    mismatched = tuple(
        PdfExportCheck(check.code, 2)
        if check.code is PdfExportCheckCode.PAGE_COUNT_MATCHED
        else check
        for check in report.checks
    )
    _assert_error(
        PdfExportErrorCode.REPORT_INVALID,
        lambda: PdfExportVerificationReport(
            semantic_fingerprint=report.semantic_fingerprint,
            output_sha256=report.output_sha256,
            output_byte_size=report.output_byte_size,
            page_count=report.page_count,
            checks=mismatched,
            unsigned_derivative=True,
        ),
    )


@pytest.mark.parametrize(
    ("status", "history_retry"),
    (
        (PdfExportOutcomeStatus.COMMITTED_RECORDED, False),
        (PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING, True),
    ),
)
def test_committed_outcomes_never_request_reexport(
    status: PdfExportOutcomeStatus,
    history_retry: bool,
) -> None:
    report = PdfExportVerificationReport.for_verified_plan(
        build_pdf_export_plan(_snapshot()),
        output_sha256="f" * 64,
        output_byte_size=1,
    )
    outcome = PdfExportOutcome(
        export_id=EXPORT_ID,
        status=status,
        report=report,
        destination_reopened=True,
        history_retry=_history_retry(report),
    )

    assert outcome.history_retry_required is history_retry
    assert not outcome.export_retry_required
    assert outcome.checks[-1].code is PdfExportCheckCode.OUTPUT_COMMIT_REOPENED
    assert outcome.claims[-1] == "PaperWorkShell reopened the committed output successfully."
    assert outcome.safe_message.startswith("Unsigned derivative: ")
    assert "saved" in outcome.safe_message.casefold()
    if history_retry:
        assert "does not need to be exported again" in outcome.safe_message
    else:
        assert "recorded in case history" in outcome.safe_message
    assert str(EXPORT_ID) not in repr(outcome)
    assert str(CASE_ID) not in repr(outcome.history_retry)
    assert str(EXPORT_ID) not in repr(outcome.history_retry)
    assert "path-free retry record" not in repr(outcome.history_retry)


def test_history_retry_receipt_rejects_wrong_digest_and_future_version() -> None:
    report = PdfExportVerificationReport.for_verified_plan(
        build_pdf_export_plan(_snapshot()),
        output_sha256="f" * 64,
        output_byte_size=1,
    )
    payload = b'{"synthetic":"retry"}\n'
    _assert_error(
        PdfExportErrorCode.OUTCOME_INVALID,
        lambda: PdfExportHistoryRetryReceipt(
            case_id=CASE_ID,
            export_id=EXPORT_ID,
            report=report,
            record_sha256="0" * 64,
            record_payload=payload,
        ),
    )
    _assert_error(
        PdfExportErrorCode.OUTCOME_INVALID,
        lambda: PdfExportHistoryRetryReceipt(
            case_id=CASE_ID,
            export_id=EXPORT_ID,
            report=report,
            record_sha256=hashlib.sha256(payload).hexdigest(),
            record_payload=payload,
            receipt_version=2,
        ),
    )


def test_committed_outcome_requires_explicit_destination_reopen_fact() -> None:
    report = PdfExportVerificationReport.for_verified_plan(
        build_pdf_export_plan(_snapshot()),
        output_sha256="f" * 64,
        output_byte_size=1,
    )
    _assert_error(
        PdfExportErrorCode.OUTCOME_INVALID,
        lambda: PdfExportOutcome(
            export_id=EXPORT_ID,
            status=PdfExportOutcomeStatus.COMMITTED_RECORDED,
            report=report,
            destination_reopened=False,
            history_retry=_history_retry(report),
        ),
    )


def test_model_constructors_reject_wrong_enums_collections_and_report_counts() -> None:
    _assert_error(
        PdfExportErrorCode.MODEL_INVALID,
        lambda: PdfExportSource(
            source_id=SOURCE_A,
            byte_size=1,
            page_count=1,
            signature_state="unknown",  # type: ignore[arg-type]
            sha256="a" * 64,
        ),
    )
    plan = build_pdf_export_plan(_snapshot())
    _assert_error(
        PdfExportErrorCode.MODEL_INVALID,
        lambda: PdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=list(plan.sources),  # type: ignore[arg-type]
            pages=plan.pages,
        ),
    )
    _assert_error(
        PdfExportErrorCode.REPORT_INVALID,
        lambda: PdfExportCheck(PdfExportCheckCode.ALL_PAGES_RENDERED, None),
    )


@pytest.mark.parametrize(
    ("phase", "completed"),
    (
        (PdfExportProgressPhase.PREPARING, 0),
        (PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 0),
        (PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 2),
        (PdfExportProgressPhase.SAVING, 2),
        (PdfExportProgressPhase.RECORDING, 2),
        (PdfExportProgressPhase.COMPLETE, 2),
    ),
)
def test_export_progress_accepts_only_phase_consistent_page_counts(
    phase: PdfExportProgressPhase,
    completed: int,
) -> None:
    progress = PdfExportProgress(phase, completed, 2)

    assert progress.phase is phase
    assert progress.completed_pages == completed
    assert progress.total_pages == 2


@pytest.mark.parametrize(
    ("phase", "completed", "total"),
    (
        ("preparing", 0, 1),
        (PdfExportProgressPhase.PREPARING, 1, 1),
        (PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, -1, 1),
        (PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 2, 1),
        (PdfExportProgressPhase.SAVING, 0, 1),
        (PdfExportProgressPhase.RECORDING, 0, 1),
        (PdfExportProgressPhase.COMPLETE, 0, 1),
        (PdfExportProgressPhase.COMPLETE, 1, 0),
        (PdfExportProgressPhase.COMPLETE, True, 1),
    ),
)
def test_export_progress_rejects_hostile_or_phase_inconsistent_values(
    phase: object,
    completed: object,
    total: object,
) -> None:
    _assert_error(
        PdfExportErrorCode.MODEL_INVALID,
        lambda: PdfExportProgress(
            cast(PdfExportProgressPhase, phase),
            cast(int, completed),
            cast(int, total),
        ),
    )
