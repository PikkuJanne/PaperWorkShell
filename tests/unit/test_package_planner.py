from __future__ import annotations

from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import (
    ASSIGNMENT_CONFIRMATION_CODE,
    ChecklistDefinition,
    ChecklistRequirement,
    RequirementCountUnit,
    TemplateBinding,
)
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestSnapshot,
)
from paperworkshell.preflight.facts import (
    OutputOverlayFlatteningAvailability,
    OutputPlanAvailability,
)
from paperworkshell.preflight.package_planner import (
    PackageExportPlan,
    PackagePlannerError,
    PackagePlannerErrorCode,
    build_package_export_plan,
)
from paperworkshell.preflight.portal_constraints import (
    PortalConstraintProfile,
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalFilenamePolicy,
    PortalOutputMode,
    RequirementFilenameStem,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"30000000-0000-4000-8000-{number:012d}") for number in range(1, 7))
ASSIGNMENT_IDS = tuple(UUID(f"70000000-0000-4000-8000-{number:012d}") for number in range(1, 7))
CONFIRMATION_IDS = tuple(UUID(f"a0000000-0000-4000-8000-{number:012d}") for number in range(1, 7))
BINDING = TemplateBinding(
    "generic-portal-application",
    "1.1.0",
    "f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd",
)


def _definition(*, beta_minimum: int = 1) -> ChecklistDefinition:
    return ChecklistDefinition(
        BINDING,
        (
            ChecklistRequirement("alpha", RequirementCountUnit.DOCUMENTS, 1, 3),
            ChecklistRequirement("beta", RequirementCountUnit.PAGES, beta_minimum, 3),
            ChecklistRequirement("optional", RequirementCountUnit.DOCUMENTS, 0, 2),
        ),
    )


def _profile(
    *,
    defaults: tuple[PortalOutputMode, ...] = (PortalOutputMode.COMBINED_PDF,),
) -> PortalConstraintProfile:
    return PortalConstraintProfile(
        BINDING,
        PortalConstraints(defaults, PortalEncryptionPolicy.MUST_BE_UNENCRYPTED),
        PortalFilenamePolicy(
            "whole-packet",
            (
                RequirementFilenameStem("alpha", "alpha-file"),
                RequirementFilenameStem("beta", "beta-file"),
                RequirementFilenameStem("optional", "optional-file"),
            ),
        ),
    )


def _assignment(
    index: int,
    requirement_id: str,
    page_ids: tuple[UUID, ...],
    *,
    status: str = "user_confirmed",
) -> dict[str, object]:
    confirmation_id = CONFIRMATION_IDS[index] if status == "user_confirmed" else None
    return {
        "assignment_id": str(ASSIGNMENT_IDS[index]),
        "requirement_id": requirement_id,
        "page_ids": [str(page_id) for page_id in page_ids],
        "origin": "user",
        "status": status,
        "confirmation_id": None if confirmation_id is None else str(confirmation_id),
        "assigned_at": "2026-08-29T10:01:00Z",
    }


def _confirmation(index: int) -> dict[str, object]:
    return {
        "confirmation_id": str(CONFIRMATION_IDS[index]),
        "confirmation_version": 1,
        "confirmation_code": ASSIGNMENT_CONFIRMATION_CODE,
        "statement_version": 1,
        "state": "confirmed",
        "actor": "user",
        "scope": {
            "kind": "assignment",
            "assignment_id": str(ASSIGNMENT_IDS[index]),
        },
        "recorded_at": "2026-08-29T10:02:00Z",
    }


def _document(
    *,
    requested_names: list[str] | None = None,
    modes: list[str] | None = None,
    active_pages: tuple[UUID, ...] = PAGE_IDS,
    assignments: list[dict[str, object]] | None = None,
    confirmations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    selected_assignments = (
        [
            _assignment(0, "beta", (PAGE_IDS[3],)),
            _assignment(1, "alpha", (PAGE_IDS[1], PAGE_IDS[0])),
            _assignment(2, "optional", (PAGE_IDS[5],)),
            _assignment(3, "alpha", (PAGE_IDS[2],)),
            _assignment(4, "beta", (PAGE_IDS[4],)),
        ]
        if assignments is None
        else assignments
    )
    selected_confirmations = (
        [_confirmation(index) for index in (0, 1, 2, 3, 4)]
        if confirmations is None
        else confirmations
    )
    constraints: dict[str, object] = {
        "constraints_version": 1,
        "allowed_output_modes": (
            ["combined_pdf", "separate_pdfs", "zip"] if modes is None else modes
        ),
        "encryption_policy": "must_be_unencrypted",
        "maximum_file_count": 4,
        "maximum_total_bytes": 10 * 1024 * 1024,
        "maximum_file_bytes": 5 * 1024 * 1024,
        "maximum_pages_per_file": 100,
    }
    if requested_names is not None:
        constraints["requested_output_names"] = requested_names
    return {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-29T10:00:00Z",
        "updated_at": "2026-08-29T10:03:00Z",
        "template_ref": BINDING.to_manifest(),
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "Synthetic input.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 4096,
                "page_count": len(PAGE_IDS),
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-29T10:00:30Z",
            }
        ],
        "pages": [
            {
                "page_id": str(page_id),
                "source_id": str(SOURCE_ID),
                "source_page_index": index,
            }
            for index, page_id in enumerate(PAGE_IDS)
        ],
        "page_sequence_base": [str(page_id) for page_id in PAGE_IDS],
        "page_sequence": [str(page_id) for page_id in active_pages],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "redaction_marks": [],
        "assignments": selected_assignments,
        "constraints": constraints,
        "confirmations": selected_confirmations,
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        CASE_ID, CURRENT_MANIFEST_SCHEMA_VERSION, 7, selected, "b" * 64, "c" * 64
    )


def _plan(
    mode: PortalOutputMode,
    document: dict[str, object] | None = None,
    *,
    destination_parent_utf16_units: int = 0,
) -> PackageExportPlan:
    return build_package_export_plan(
        _snapshot(document),
        _definition(),
        _profile(),
        mode,
        destination_parent_utf16_units=destination_parent_utf16_units,
    )


def _assert_error(
    code: PackagePlannerErrorCode,
    mode: PortalOutputMode,
    document: dict[str, object],
) -> PackagePlannerError:
    with pytest.raises(PackagePlannerError) as caught:
        _plan(mode, document)
    assert caught.value.code is code
    return caught.value


def test_combined_plan_uses_requirement_assignment_and_assignment_page_order() -> None:
    document = _document(active_pages=tuple(reversed(PAGE_IDS)))

    plan = _plan(PortalOutputMode.COMBINED_PDF, document)

    assert len(plan.pdfs) == 1
    assert plan.pdfs[0].page_ids == (
        PAGE_IDS[1],
        PAGE_IDS[0],
        PAGE_IDS[2],
        PAGE_IDS[3],
        PAGE_IDS[4],
        PAGE_IDS[5],
    )
    assert plan.pdfs[0].assignment_ids == (
        ASSIGNMENT_IDS[1],
        ASSIGNMENT_IDS[3],
        ASSIGNMENT_IDS[0],
        ASSIGNMENT_IDS[4],
        ASSIGNMENT_IDS[2],
    )
    assert plan.pdfs[0].filename == "whole-packet.pdf"
    assert plan.archive is None
    assert plan.output_facts.availability is OutputPlanAvailability.CURRENT
    assert plan.output_facts.items[0].page_ids == plan.pdfs[0].page_ids
    assert plan.output_facts.items[0].predicted_bytes is None
    assert plan.active_constraints.values.maximum_file_count == 4


def test_separate_plan_emits_one_pdf_per_nonempty_requirement_in_template_order() -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)

    assert tuple(pdf.requirement_id for pdf in plan.pdfs) == ("alpha", "beta", "optional")
    assert tuple(pdf.page_ids for pdf in plan.pdfs) == (
        (PAGE_IDS[1], PAGE_IDS[0], PAGE_IDS[2]),
        (PAGE_IDS[3], PAGE_IDS[4]),
        (PAGE_IDS[5],),
    )
    assert tuple(pdf.filename for pdf in plan.pdfs) == (
        "alpha-file.pdf",
        "beta-file.pdf",
        "optional-file.pdf",
    )


def test_zip_uses_requested_outer_name_but_default_requirement_member_names() -> None:
    document = _document(requested_names=["chosen-archive.zip"])

    plan = _plan(PortalOutputMode.ZIP, document)

    assert plan.archive is not None
    assert plan.archive.filename == "chosen-archive.zip"
    assert plan.archive.member_output_keys == tuple(pdf.output_key for pdf in plan.pdfs)
    assert tuple(pdf.filename for pdf in plan.pdfs) == (
        "alpha-file.pdf",
        "beta-file.pdf",
        "optional-file.pdf",
    )
    assert tuple(pdf.page_ids for pdf in plan.pdfs) == tuple(
        pdf.page_ids for pdf in _plan(PortalOutputMode.SEPARATE_PDFS).pdfs
    )


def test_optional_empty_requirement_is_omitted_without_position_gap() -> None:
    assignments = [
        _assignment(0, "beta", (PAGE_IDS[3], PAGE_IDS[4])),
        _assignment(1, "alpha", (PAGE_IDS[1], PAGE_IDS[0], PAGE_IDS[2])),
    ]
    document = _document(
        active_pages=PAGE_IDS[:5],
        assignments=assignments,
        confirmations=[_confirmation(0), _confirmation(1)],
    )

    plan = _plan(PortalOutputMode.SEPARATE_PDFS, document)

    assert tuple(pdf.requirement_id for pdf in plan.pdfs) == ("alpha", "beta")
    assert tuple(pdf.position for pdf in plan.pdfs) == (1, 2)


def test_saved_constraints_are_active_instead_of_profile_defaults() -> None:
    assert PortalOutputMode.SEPARATE_PDFS not in _profile().constraints.allowed_modes

    plan = _plan(PortalOutputMode.SEPARATE_PDFS)

    assert PortalOutputMode.SEPARATE_PDFS in plan.active_constraints.values.allowed_modes


def test_selected_mode_must_be_allowed_by_saved_constraints() -> None:
    document = _document(modes=["combined_pdf"])

    _assert_error(PackagePlannerErrorCode.MODE_NOT_ALLOWED, PortalOutputMode.ZIP, document)


def test_exact_template_binding_is_required_across_manifest_definition_and_profile() -> None:
    document = _document()
    cast(dict[str, object], document["template_ref"])["sha256"] = "d" * 64

    _assert_error(
        PackagePlannerErrorCode.TEMPLATE_MISMATCH,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


def test_suggested_assignment_blocks_and_error_does_not_retain_private_values() -> None:
    document = _document()
    assignments = cast(list[dict[str, object]], document["assignments"])
    assignments[0]["status"] = "suggested"
    assignments[0]["confirmation_id"] = None
    document["confirmations"] = cast(list[dict[str, object]], document["confirmations"])[1:]

    error = _assert_error(
        PackagePlannerErrorCode.ASSIGNMENT_UNCONFIRMED,
        PortalOutputMode.COMBINED_PDF,
        document,
    )

    rendered = f"{error!r} {error} {error.args!r}"
    assert str(ASSIGNMENT_IDS[0]) not in rendered
    assert "Synthetic input.pdf" not in rendered


def test_unassigned_active_page_blocks_exact_partition() -> None:
    document = _document()
    assignments = cast(list[dict[str, object]], document["assignments"])
    cast(list[str], assignments[2]["page_ids"]).clear()

    _assert_error(
        PackagePlannerErrorCode.MANIFEST_INVALID,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


def test_unassigned_page_with_no_assignment_blocks_exact_partition() -> None:
    document = _document(
        assignments=[
            _assignment(0, "beta", (PAGE_IDS[3], PAGE_IDS[4])),
            _assignment(1, "alpha", (PAGE_IDS[1], PAGE_IDS[0], PAGE_IDS[2])),
        ],
        confirmations=[_confirmation(0), _confirmation(1)],
    )

    _assert_error(
        PackagePlannerErrorCode.PAGE_UNASSIGNED,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


def test_inactive_assigned_page_blocks() -> None:
    document = _document(active_pages=PAGE_IDS[:5])

    _assert_error(
        PackagePlannerErrorCode.PAGE_INACTIVE,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


def test_missing_required_requirement_blocks() -> None:
    document = _document(
        active_pages=(PAGE_IDS[0], PAGE_IDS[1], PAGE_IDS[2], PAGE_IDS[5]),
        assignments=[
            _assignment(1, "alpha", (PAGE_IDS[1], PAGE_IDS[0], PAGE_IDS[2])),
            _assignment(2, "optional", (PAGE_IDS[5],)),
        ],
        confirmations=[_confirmation(1), _confirmation(2)],
    )

    _assert_error(
        PackagePlannerErrorCode.REQUIREMENT_MISSING,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


def test_duplicate_page_across_confirmed_assignments_blocks() -> None:
    document = _document()
    assignments = cast(list[dict[str, object]], document["assignments"])
    assignments[3]["page_ids"] = [str(PAGE_IDS[2]), str(PAGE_IDS[3])]

    _assert_error(
        PackagePlannerErrorCode.PAGE_DUPLICATE,
        PortalOutputMode.COMBINED_PDF,
        document,
    )


@pytest.mark.parametrize(
    ("mode", "names", "code"),
    (
        (
            PortalOutputMode.COMBINED_PDF,
            ["one.pdf", "two.pdf"],
            PackagePlannerErrorCode.FILENAME_COUNT_INVALID,
        ),
        (
            PortalOutputMode.SEPARATE_PDFS,
            ["one.pdf"],
            PackagePlannerErrorCode.FILENAME_COUNT_INVALID,
        ),
        (
            PortalOutputMode.ZIP,
            ["one.zip", "two.zip"],
            PackagePlannerErrorCode.FILENAME_COUNT_INVALID,
        ),
        (
            PortalOutputMode.COMBINED_PDF,
            ["one.zip"],
            PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID,
        ),
        (
            PortalOutputMode.SEPARATE_PDFS,
            ["one.PDF", "two.pdf", "three.pdf"],
            PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID,
        ),
        (PortalOutputMode.ZIP, ["one.pdf"], PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID),
    ),
)
def test_requested_names_require_exact_mode_specific_count_and_extension(
    mode: PortalOutputMode,
    names: list[str],
    code: PackagePlannerErrorCode,
) -> None:
    _assert_error(code, mode, _document(requested_names=names))


def test_requested_windows_name_collision_blocks_instead_of_silently_renaming() -> None:
    document = _document(requested_names=["same.pdf", "SAME.pdf", "third.pdf"])

    _assert_error(
        PackagePlannerErrorCode.FILENAME_COLLISION,
        PortalOutputMode.SEPARATE_PDFS,
        document,
    )


@pytest.mark.parametrize(
    "mode",
    (PortalOutputMode.COMBINED_PDF, PortalOutputMode.SEPARATE_PDFS, PortalOutputMode.ZIP),
)
def test_actual_destination_parent_budget_is_enforced(mode: PortalOutputMode) -> None:
    with pytest.raises(PackagePlannerError) as caught:
        _plan(mode, destination_parent_utf16_units=32_760)

    assert caught.value.code is PackagePlannerErrorCode.PATH_LENGTH_EXCEEDED


def test_overlay_flattening_facts_follow_planned_output_order() -> None:
    document = _document()
    document["overlays"] = [
        {
            "overlay_id": "90000000-0000-4000-8000-000000000001",
            "overlay_version": 1,
            "page_id": str(PAGE_IDS[3]),
            "box": {
                "coordinate_space": "normalized_page_millionths_v1",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            "z_index": 0,
            "payload": {
                "kind": "shape",
                "shape": "rectangle",
                "stroke_color": "#000000ff",
                "fill_color": "#ffffffff",
                "stroke_width_points": 1,
            },
        },
        {
            "overlay_id": "90000000-0000-4000-8000-000000000002",
            "overlay_version": 1,
            "page_id": str(PAGE_IDS[1]),
            "box": {
                "coordinate_space": "normalized_page_millionths_v1",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
            },
            "z_index": 0,
            "payload": {
                "kind": "shape",
                "shape": "rectangle",
                "stroke_color": "#000000ff",
                "fill_color": "#ffffffff",
                "stroke_width_points": 1,
            },
        },
    ]

    plan = _plan(PortalOutputMode.COMBINED_PDF, document)

    assert (
        plan.output_facts.overlay_flattening_availability
        is OutputOverlayFlatteningAvailability.CURRENT
    )
    assert plan.output_facts.flattened_overlay_page_ids == (PAGE_IDS[1], PAGE_IDS[3])


def test_plan_fingerprint_is_deterministic_and_changes_with_output_semantics() -> None:
    first = _plan(PortalOutputMode.COMBINED_PDF)
    second = _plan(PortalOutputMode.COMBINED_PDF)
    renamed = _plan(
        PortalOutputMode.COMBINED_PDF,
        _document(requested_names=["renamed.pdf"]),
    )

    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.semantic_fingerprint != renamed.semantic_fingerprint
    assert len(first.semantic_fingerprint) == 64


def test_all_plan_representations_hide_names_identifiers_and_digests() -> None:
    plan = _plan(PortalOutputMode.ZIP, _document(requested_names=["private-name.zip"]))

    rendered = " ".join(
        (
            repr(plan),
            repr(plan.authority),
            repr(plan.active_constraints),
            repr(plan.pdfs),
            repr(plan.archive),
            repr(plan.output_facts),
        )
    )
    assert "private-name" not in rendered
    assert "alpha-file" not in rendered
    assert str(CASE_ID) not in rendered
    assert str(PAGE_IDS[0]) not in rendered
    assert str(ASSIGNMENT_IDS[0]) not in rendered
    assert "b" * 64 not in rendered
    assert plan.semantic_fingerprint not in rendered


def test_requested_names_are_not_reused_as_zip_member_names() -> None:
    plan = _plan(PortalOutputMode.ZIP, _document(requested_names=["outer.zip"]))

    assert all(pdf.filename != "outer.pdf" for pdf in plan.pdfs)


def test_malformed_or_extra_persisted_constraint_member_fails_closed() -> None:
    document = deepcopy(_document())
    cast(dict[str, object], document["constraints"])["unknown"] = "private-value"

    error = _assert_error(
        PackagePlannerErrorCode.MANIFEST_INVALID,
        PortalOutputMode.COMBINED_PDF,
        document,
    )

    assert "private-value" not in repr(error)


def test_null_persisted_numeric_constraint_fails_closed_instead_of_meaning_absent() -> None:
    document = _document()
    cast(dict[str, object], document["constraints"])["maximum_file_count"] = None

    _assert_error(
        PackagePlannerErrorCode.MANIFEST_INVALID,
        PortalOutputMode.COMBINED_PDF,
        document,
    )
