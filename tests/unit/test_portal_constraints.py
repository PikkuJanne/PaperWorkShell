"""PWS-0406 pure portal-constraint and safe-filename contracts."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_inspection import PdfEncryptionState
from paperworkshell.preflight.facts import (
    OutputItemPreflightFact,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
)
from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_STATEMENT_VERSION,
    PreflightError,
    PreflightErrorCode,
    PreflightScopeKind,
)
from paperworkshell.preflight.portal_constraints import (
    MAX_PORTAL_OUTPUTS,
    MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS,
    PORTAL_CONSTRAINT_FINDING_DEFINITIONS,
    PortalArchiveObservation,
    PortalConstraintEvaluation,
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalFilenamePolicy,
    PortalFilenameRequest,
    PortalOutputMode,
    PortalOutputObservation,
    RequirementFilenameStem,
    evaluate_portal_constraints,
    normalize_output_filename,
    portal_constraint_profile_from_template,
    resolve_output_filenames,
    resolve_zip_archive_filename,
)
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

CASE_ID = UUID("11111111-1111-4111-8111-111111111111")
PAGE_ID = UUID("22222222-2222-4222-8222-222222222222")
SECOND_PAGE_ID = UUID("33333333-3333-4333-8333-333333333333")
ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src" / "paperworkshell" / "preflight" / "portal_constraints.py"


def _constraints(
    *,
    allowed_modes: tuple[PortalOutputMode, ...] = (PortalOutputMode.COMBINED_PDF,),
    encryption_policy: PortalEncryptionPolicy = PortalEncryptionPolicy.MUST_BE_UNENCRYPTED,
    maximum_file_count: int | None = 1,
    maximum_total_bytes: int | None = 1_000,
    maximum_file_bytes: int | None = 1_000,
    maximum_pages_per_file: int | None = 10,
    default_mode: PortalOutputMode | None = PortalOutputMode.COMBINED_PDF,
) -> PortalConstraints:
    return PortalConstraints(
        allowed_modes=allowed_modes,
        encryption_policy=encryption_policy,
        maximum_file_count=maximum_file_count,
        maximum_total_bytes=maximum_total_bytes,
        maximum_file_bytes=maximum_file_bytes,
        maximum_pages_per_file=maximum_pages_per_file,
        default_mode=default_mode,
    )


def _plan(
    *,
    mode: str = "combined_pdf",
    sizes: tuple[int | None, ...] = (80,),
    pages: tuple[tuple[UUID, ...], ...] | None = None,
) -> OutputPlanPreflightFacts:
    selected_pages = (
        tuple((page_id,) for page_id in (PAGE_ID, SECOND_PAGE_ID)[: len(sizes)])
        if pages is None
        else pages
    )
    return OutputPlanPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=4,
        availability=OutputPlanAvailability.CURRENT,
        output_mode=mode,
        items=tuple(
            OutputItemPreflightFact(
                output_key=f"packet-{position}",
                position=position,
                page_ids=page_ids,
                predicted_bytes=size,
            )
            for position, (page_ids, size) in enumerate(
                zip(selected_pages, sizes, strict=True),
                start=1,
            )
        ),
    )


def _outputs(
    plan: OutputPlanPreflightFacts,
    *,
    names: tuple[str, ...] | None = None,
    encryption: PdfEncryptionState = PdfEncryptionState.UNENCRYPTED,
) -> tuple[PortalOutputObservation, ...]:
    selected_names = (
        tuple(f"packet-{item.position}.pdf" for item in plan.items) if names is None else names
    )
    return tuple(
        PortalOutputObservation(
            output_key=item.output_key,
            position=item.position,
            filename=filename,
            encryption_state=encryption,
        )
        for item, filename in zip(plan.items, selected_names, strict=True)
    )


def _codes(evaluation: PortalConstraintEvaluation) -> tuple[str, ...]:
    return tuple(target.code for target in evaluate_portal_constraints(evaluation))


def test_template_snapshot_maps_exact_v1_output_contract_without_schema_change() -> None:
    snapshot = SafeTemplateLoader(built_in_template_repository()).load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
    )

    profile = portal_constraint_profile_from_template(snapshot)

    assert profile.binding.template_id == "generic-portal-application"
    assert profile.binding.template_version == "1.1.0"
    assert profile.binding.sha256 == snapshot.sha256
    assert profile.constraints.allowed_modes == tuple(PortalOutputMode)
    assert profile.constraints.default_mode is PortalOutputMode.COMBINED_PDF
    assert profile.constraints.encryption_policy is PortalEncryptionPolicy.MUST_BE_UNENCRYPTED
    assert profile.constraints.maximum_file_count == 4
    assert profile.constraints.maximum_total_bytes == 10_485_760
    assert profile.constraints.maximum_file_bytes == 5_242_880
    assert profile.constraints.maximum_pages_per_file == 100
    assert profile.filename_policy.package_stem == "generic-application-packet"
    assert tuple(item.requirement_id for item in profile.filename_policy.requirement_stems) == (
        "application-form",
        "identity-evidence",
        "address-evidence",
        "supporting-evidence",
    )


def test_manual_constraints_are_frozen_canonical_and_optional() -> None:
    constraints = PortalConstraints(
        allowed_modes=(PortalOutputMode.ZIP, PortalOutputMode.COMBINED_PDF),
        encryption_policy=PortalEncryptionPolicy.ALLOWED,
        maximum_file_count=MAX_PORTAL_OUTPUTS + 1,
        maximum_pages_per_file=MAX_PORTAL_OUTPUTS + 1,
    )

    assert constraints.allowed_modes == (
        PortalOutputMode.COMBINED_PDF,
        PortalOutputMode.ZIP,
    )
    assert constraints.maximum_file_count == MAX_PORTAL_OUTPUTS + 1
    assert constraints.maximum_pages_per_file == MAX_PORTAL_OUTPUTS + 1
    with pytest.raises(FrozenInstanceError):
        constraints.maximum_file_count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: PortalConstraints((), PortalEncryptionPolicy.ALLOWED),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF, PortalOutputMode.COMBINED_PDF),
            PortalEncryptionPolicy.ALLOWED,
        ),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF,),
            PortalEncryptionPolicy.ALLOWED,
            default_mode=PortalOutputMode.ZIP,
        ),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF,),
            PortalEncryptionPolicy.ALLOWED,
            maximum_file_count=0,
        ),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF,),
            PortalEncryptionPolicy.ALLOWED,
            maximum_file_count=True,
        ),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF,),
            PortalEncryptionPolicy.ALLOWED,
            maximum_file_count=MAX_PREFLIGHT_STATEMENT_VERSION + 1,
        ),
        lambda: PortalConstraints(
            (PortalOutputMode.COMBINED_PDF,),
            PortalEncryptionPolicy.ALLOWED,
            maximum_total_bytes=MAX_PREFLIGHT_STATEMENT_VERSION + 1,
        ),
    ),
)
def test_manual_constraints_reject_invalid_values_before_activation(
    factory: Callable[[], PortalConstraints],
) -> None:
    with pytest.raises(PreflightError) as captured:
        factory()

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.__cause__ is None


def test_per_file_and_total_limits_remain_independent_as_released_in_manifest_v2() -> None:
    constraints = PortalConstraints(
        (PortalOutputMode.COMBINED_PDF,),
        PortalEncryptionPolicy.ALLOWED,
        maximum_total_bytes=10,
        maximum_file_bytes=11,
    )

    assert constraints.maximum_total_bytes == 10
    assert constraints.maximum_file_bytes == 11


def test_filename_policy_is_fixed_inert_and_content_minimal_in_repr() -> None:
    policy = PortalFilenamePolicy(
        package_stem="private-packet",
        requirement_stems=(RequirementFilenameStem("identity", "private-identity"),),
    )

    assert "private" not in repr(policy)
    assert "private" not in repr(policy.requirement_stems[0])
    with pytest.raises(PreflightError):
        replace(policy, package_stem="{execute-command}")
    with pytest.raises(PreflightError):
        replace(policy, package_stem="COM1")


def test_unicode_filename_is_normalized_to_nfc_without_case_or_content_rewrite() -> None:
    assert normalize_output_filename("Cafe\u0301.PDF") == "Café.PDF"
    assert normalize_output_filename("資料.pdf") == "資料.pdf"


@pytest.mark.parametrize(
    "filename",
    (
        ".",
        "..",
        "../packet.pdf",
        "..\\packet.pdf",
        "folder/packet.pdf",
        "folder\\packet.pdf",
        "C:packet.pdf",
        "packet.pdf:stream",
        "packet%2fpart.pdf",
        "AUX.pdf",
        "clock$.txt",
        "COM¹.pdf",
        "packet.pdf.",
        "packet.pdf ",
        "packet\u202epdf",
        "packet\x00.pdf",
        "packet\ud800.pdf",
    ),
)
def test_filename_rejects_traversal_separators_devices_controls_and_archives(
    filename: str,
) -> None:
    with pytest.raises(PreflightError) as captured:
        normalize_output_filename(filename)

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.__cause__ is None


def test_reserved_device_property_matrix_rejects_case_and_extension_variants() -> None:
    reserved = (
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        "conin$",
        "conout$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    )
    candidates = tuple(
        f"{variant}{suffix}"
        for value in reserved
        for variant in (value.lower(), value.upper(), value.title())
        for suffix in ("", ".pdf", ".txt.more")
    )

    for candidate in candidates:
        with pytest.raises(PreflightError):
            normalize_output_filename(candidate)


def test_unsafe_character_property_matrix_rejects_every_supported_path_mutation() -> None:
    mutations = tuple('<>:"/\\|?*%') + (
        "\x01",
        "\x7f",
        "\u202e",
        "\u2066",
        "\ud800",
    )

    for mutation in mutations:
        with pytest.raises(PreflightError):
            normalize_output_filename(f"packet{mutation}part.pdf")


def test_unicode_collision_resolution_is_nfc_stable_and_repeatable() -> None:
    stems = ("Cafe\u0301", "Café", "CAFÉ", "資料", "資料")
    requests = tuple(
        PortalFilenameRequest(f"packet-{position}", position, stem)
        for position, stem in enumerate(stems, start=1)
    )

    first = resolve_output_filenames(requests, PortalOutputMode.SEPARATE_PDFS)
    second = resolve_output_filenames(requests, PortalOutputMode.SEPARATE_PDFS)

    assert first == second
    assert tuple(value.filename for value in first) == (
        "Café.pdf",
        "Café-2.pdf",
        "CAFÉ-3.pdf",
        "資料.pdf",
        "資料-2.pdf",
    )
    assert all(value.filename == normalize_output_filename(value.filename) for value in first)


def test_filename_and_complete_path_use_windows_utf16_limits() -> None:
    maximum_component = f"{'a' * 174}\U0001f4c4.pdf"
    assert len(maximum_component.encode("utf-16-le")) // 2 == 180
    assert normalize_output_filename(maximum_component) == maximum_component
    with pytest.raises(PreflightError):
        normalize_output_filename(f"a{maximum_component}")

    filename = "packet.pdf"
    filename_units = len(filename.encode("utf-16-le")) // 2
    exact_parent = MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS - filename_units - 1
    assert (
        normalize_output_filename(
            filename,
            destination_parent_utf16_units=exact_parent,
        )
        == filename
    )
    with pytest.raises(PreflightError) as captured:
        normalize_output_filename(
            filename,
            destination_parent_utf16_units=exact_parent + 1,
        )
    assert captured.value.context == "portal.filename"


def test_collisions_resolve_by_stable_counter_and_windows_comparison() -> None:
    requests = (
        PortalFilenameRequest("packet-one", 1, "Packet"),
        PortalFilenameRequest("packet-two", 2, "packet"),
        PortalFilenameRequest("packet-three", 3, "packet-2"),
    )

    first = resolve_output_filenames(requests, PortalOutputMode.SEPARATE_PDFS)
    second = resolve_output_filenames(requests, PortalOutputMode.SEPARATE_PDFS)

    assert first == second
    assert tuple(item.filename for item in first) == (
        "Packet.pdf",
        "packet-2.pdf",
        "packet-2-2.pdf",
    )
    assert tuple(item.collision_index for item in first) == (1, 2, 2)
    assert all("Packet" not in repr(item) and "packet" not in repr(item) for item in first)


def test_collision_counter_shortens_only_as_needed_at_component_limit() -> None:
    stem = "a" * 176
    resolved = resolve_output_filenames(
        (
            PortalFilenameRequest("packet-one", 1, stem),
            PortalFilenameRequest("packet-two", 2, stem),
        ),
        PortalOutputMode.SEPARATE_PDFS,
    )

    assert resolved[0].filename == f"{stem}.pdf"
    assert resolved[1].filename == f"{'a' * 174}-2.pdf"
    assert all(len(item.filename.encode("utf-16-le")) // 2 == 180 for item in resolved)


def test_zip_member_and_outer_archive_names_use_distinct_fixed_namespaces() -> None:
    members = resolve_output_filenames(
        (
            PortalFilenameRequest("member-one", 1, "first"),
            PortalFilenameRequest("member-two", 2, "second"),
        ),
        PortalOutputMode.ZIP,
    )
    archive = resolve_zip_archive_filename(PortalFilenameRequest("package", 1, "safe-package"))

    assert tuple(value.filename for value in members) == ("first.pdf", "second.pdf")
    assert archive.filename == "safe-package.zip"
    with pytest.raises(PreflightError):
        resolve_zip_archive_filename(PortalFilenameRequest("package", 2, "unsafe-position"))


def test_zip_member_path_budget_is_independent_from_outer_destination_parent() -> None:
    plan = _plan(mode="zip")
    member_name = f"{'a' * 176}.pdf"
    archive_name = "p.zip"
    parent_units = (
        MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS
        - len(archive_name.encode("utf-16-le")) // 2
        - 1
    )

    evaluation = PortalConstraintEvaluation(
        _constraints(allowed_modes=(PortalOutputMode.ZIP,), default_mode=PortalOutputMode.ZIP),
        plan,
        _outputs(plan, names=(member_name,)),
        PortalArchiveObservation(archive_name, 100),
        destination_parent_utf16_units=parent_units,
    )

    assert evaluate_portal_constraints(evaluation) == ()


def test_zip_constraints_distinguish_outer_archive_from_member_pdfs() -> None:
    plan = _plan(
        mode="zip",
        sizes=(80, 90),
        pages=((PAGE_ID,), (SECOND_PAGE_ID,)),
    )
    constraints = _constraints(
        allowed_modes=(PortalOutputMode.ZIP,),
        maximum_file_count=1,
        maximum_total_bytes=200,
        maximum_file_bytes=200,
        maximum_pages_per_file=1,
        default_mode=PortalOutputMode.ZIP,
    )
    resolved_members = resolve_output_filenames(
        (
            PortalFilenameRequest("packet-1", 1, "first"),
            PortalFilenameRequest("packet-2", 2, "second"),
        ),
        PortalOutputMode.ZIP,
    )
    resolved_archive = resolve_zip_archive_filename(PortalFilenameRequest("package", 1, "package"))
    clean = PortalConstraintEvaluation(
        constraints,
        plan,
        _outputs(plan, names=tuple(value.filename for value in resolved_members)),
        PortalArchiveObservation(resolved_archive.filename, 170),
    )

    assert evaluate_portal_constraints(clean) == ()

    missing_archive = PortalConstraintEvaluation(constraints, plan, _outputs(plan))
    assert set(_codes(missing_archive)) == {
        "PORTAL_TOTAL_SIZE_UNAVAILABLE",
        "PORTAL_ZIP_ARCHIVE_UNAVAILABLE",
    }


def test_zip_checks_archive_size_extension_and_each_member_pdf_policy() -> None:
    plan = _plan(
        mode="zip",
        sizes=(201,),
        pages=((PAGE_ID, SECOND_PAGE_ID),),
    )
    constraints = _constraints(
        allowed_modes=(PortalOutputMode.ZIP,),
        maximum_file_count=1,
        maximum_total_bytes=200,
        maximum_file_bytes=200,
        maximum_pages_per_file=1,
        default_mode=PortalOutputMode.ZIP,
    )
    evaluation = PortalConstraintEvaluation(
        constraints,
        plan,
        _outputs(
            plan,
            names=("member.zip",),
            encryption=PdfEncryptionState.ENCRYPTED,
        ),
        PortalArchiveObservation("package.pdf", 210),
    )

    targets = evaluate_portal_constraints(evaluation)

    assert {target.code for target in targets} == {
        "PORTAL_ENCRYPTION_DETECTED",
        "PORTAL_EXTENSION_INVALID",
        "PORTAL_FILE_SIZE_EXCEEDED",
        "PORTAL_PAGE_COUNT_EXCEEDED",
        "PORTAL_TOTAL_SIZE_EXCEEDED",
    }
    assert sum(target.code == "PORTAL_EXTENSION_INVALID" for target in targets) == 2
    assert sum(target.code == "PORTAL_FILE_SIZE_EXCEEDED" for target in targets) == 2
    assert any(target.scope.kind is PreflightScopeKind.CASE for target in targets)
    assert any(target.scope.kind is PreflightScopeKind.OUTPUT for target in targets)


def test_clean_constraint_evaluation_returns_no_acceptance_or_success_finding() -> None:
    plan = _plan()
    evaluation = PortalConstraintEvaluation(_constraints(), plan, _outputs(plan))

    assert evaluate_portal_constraints(evaluation) == ()


def test_all_numeric_extension_and_encryption_limits_produce_typed_scoped_findings() -> None:
    plan = _plan(
        sizes=(110,),
        pages=((PAGE_ID, SECOND_PAGE_ID),),
    )
    evaluation = PortalConstraintEvaluation(
        _constraints(
            maximum_total_bytes=100,
            maximum_file_bytes=100,
            maximum_pages_per_file=1,
        ),
        plan,
        _outputs(plan, names=("packet.txt",), encryption=PdfEncryptionState.ENCRYPTED),
    )

    targets = evaluate_portal_constraints(evaluation)

    assert {target.code for target in targets} == {
        "PORTAL_ENCRYPTION_DETECTED",
        "PORTAL_EXTENSION_INVALID",
        "PORTAL_FILE_SIZE_EXCEEDED",
        "PORTAL_PAGE_COUNT_EXCEEDED",
        "PORTAL_TOTAL_SIZE_EXCEEDED",
    }
    aggregate = next(target for target in targets if target.code == "PORTAL_TOTAL_SIZE_EXCEEDED")
    per_file = next(target for target in targets if target.code == "PORTAL_FILE_SIZE_EXCEEDED")
    pages = next(target for target in targets if target.code == "PORTAL_PAGE_COUNT_EXCEEDED")
    assert aggregate.scope.kind is PreflightScopeKind.CASE
    assert per_file.scope.kind is PreflightScopeKind.OUTPUT
    assert per_file.evidence[0].observed_value == 110
    assert per_file.evidence[0].limit_value == 100
    assert pages.evidence[0].observed_value == 2
    assert pages.evidence[0].limit_value == 1


def test_numeric_constraint_equality_boundaries_are_inclusive() -> None:
    plan = _plan(
        mode="separate_pdfs",
        sizes=(50, 50),
        pages=((PAGE_ID,), (SECOND_PAGE_ID,)),
    )
    constraints = _constraints(
        allowed_modes=(PortalOutputMode.SEPARATE_PDFS,),
        maximum_file_count=2,
        maximum_total_bytes=100,
        maximum_file_bytes=50,
        maximum_pages_per_file=1,
        default_mode=PortalOutputMode.SEPARATE_PDFS,
    )

    assert (
        evaluate_portal_constraints(PortalConstraintEvaluation(constraints, plan, _outputs(plan)))
        == ()
    )


def test_unavailable_sizes_and_unknown_encryption_never_become_clean() -> None:
    plan = _plan(sizes=(None,))
    evaluation = PortalConstraintEvaluation(
        _constraints(),
        plan,
        _outputs(plan, encryption=PdfEncryptionState.UNKNOWN),
    )

    assert set(_codes(evaluation)) == {
        "PORTAL_ENCRYPTION_UNVERIFIED",
        "PORTAL_FILE_SIZE_UNAVAILABLE",
        "PORTAL_TOTAL_SIZE_UNAVAILABLE",
    }


def test_allowed_encryption_does_not_create_a_false_policy_failure() -> None:
    plan = _plan()
    evaluation = PortalConstraintEvaluation(
        _constraints(
            encryption_policy=PortalEncryptionPolicy.ALLOWED,
        ),
        plan,
        _outputs(plan, encryption=PdfEncryptionState.ENCRYPTED),
    )

    assert evaluate_portal_constraints(evaluation) == ()


def test_file_count_output_mode_and_organization_are_independent_checks() -> None:
    separate = _plan(mode="separate_pdfs", sizes=(10, 10))
    separate_evaluation = PortalConstraintEvaluation(
        _constraints(maximum_file_count=1),
        separate,
        _outputs(separate),
    )
    assert set(_codes(separate_evaluation)) == {
        "PORTAL_FILE_COUNT_EXCEEDED",
        "PORTAL_OUTPUT_MODE_NOT_ALLOWED",
    }

    combined = _plan(mode="combined_pdf", sizes=(10, 10))
    combined_evaluation = PortalConstraintEvaluation(
        _constraints(maximum_file_count=2),
        combined,
        _outputs(combined),
    )
    assert _codes(combined_evaluation) == ("PORTAL_OUTPUT_ORGANIZATION_INVALID",)


def test_wrong_extension_is_not_reported_when_mode_itself_is_unknown() -> None:
    plan = _plan(mode="future_mode")
    evaluation = PortalConstraintEvaluation(
        _constraints(),
        plan,
        _outputs(plan, names=("packet.future",)),
    )

    assert _codes(evaluation) == ("PORTAL_OUTPUT_MODE_NOT_ALLOWED",)


def test_filename_collisions_point_to_both_output_scopes() -> None:
    plan = _plan(
        mode="separate_pdfs",
        sizes=(10, 10),
    )
    evaluation = PortalConstraintEvaluation(
        _constraints(
            allowed_modes=(PortalOutputMode.SEPARATE_PDFS,),
            default_mode=PortalOutputMode.SEPARATE_PDFS,
            maximum_file_count=2,
        ),
        plan,
        _outputs(plan, names=("Résumé.pdf", "re\u0301sume\u0301.PDF")),
    )

    targets = evaluate_portal_constraints(evaluation)

    assert _codes(evaluation) == ("PORTAL_FILENAME_COLLISION",)
    assert targets[0].scope.item_id == "packet-2"
    assert targets[0].related_scope is not None
    assert targets[0].related_scope.item_id == "packet-1"


def test_unavailable_plan_produces_explicit_blocking_finding() -> None:
    plan = OutputPlanPreflightFacts(
        CASE_ID,
        4,
        OutputPlanAvailability.UNAVAILABLE,
        "unavailable",
        (),
    )
    evaluation = PortalConstraintEvaluation(_constraints(), plan, ())

    assert _codes(evaluation) == ("PORTAL_OUTPUT_PLAN_UNAVAILABLE",)


def test_evaluation_rejects_observations_not_exactly_bound_to_plan() -> None:
    plan = _plan()
    wrong = PortalOutputObservation(
        "other-packet",
        1,
        "packet.pdf",
        PdfEncryptionState.UNENCRYPTED,
    )

    with pytest.raises(PreflightError) as captured:
        PortalConstraintEvaluation(_constraints(), plan, (wrong,))

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "portal.output_binding"


def test_finding_registry_is_closed_unique_and_avoids_acceptance_claims() -> None:
    codes = tuple(definition.code for definition in PORTAL_CONSTRAINT_FINDING_DEFINITIONS)

    assert codes == tuple(sorted(codes))
    assert len(codes) == len(set(codes)) == 14
    assert all(
        definition.severity.value == "blocking_error"
        for definition in PORTAL_CONSTRAINT_FINDING_DEFINITIONS
    )
    rendered = " ".join(
        f"{definition.message} {definition.remediation}"
        for definition in PORTAL_CONSTRAINT_FINDING_DEFINITIONS
    ).casefold()
    assert "portal accepted" not in rendered
    assert "everything is valid" not in rendered


def test_safe_representations_exclude_output_names_and_template_stems() -> None:
    plan = _plan()
    output = PortalOutputObservation(
        "packet-1",
        1,
        "private-canary.pdf",
        PdfEncryptionState.UNENCRYPTED,
    )
    evaluation = PortalConstraintEvaluation(_constraints(), plan, (output,))
    request = PortalFilenameRequest("packet-1", 1, "private-canary")

    assert "private-canary" not in repr(output)
    assert "private-canary" not in repr(evaluation)
    assert "private-canary" not in repr(request)


def test_module_has_no_filesystem_process_network_or_dynamic_execution_surface() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    prohibited_import_roots = {
        "asyncio",
        "ctypes",
        "importlib",
        "os",
        "pathlib",
        "shutil",
        "socket",
        "subprocess",
        "tempfile",
        "urllib",
    }
    prohibited_calls = {"compile", "eval", "exec", "open", "__import__"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".", maxsplit=1)[0] not in prohibited_import_roots
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".", maxsplit=1)[0] not in prohibited_import_roots
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in prohibited_calls
