"""PWS-0408 summary wording, authority, and truthful output-state tests."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, replace
from datetime import date
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.checklist import checklist_definition_from_template
from paperworkshell.domain.checklist import (
    ASSIGNMENT_CONFIRMATION_CODE,
    ChecklistCommandHistory,
    ChecklistDefinition,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryError,
    PackageSummaryErrorCode,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.domain.pdf_export import (
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.package_export import (
    PackageAggregateVerificationReport,
    PackageExportOutcome,
    PackageExportStatus,
    PackagePdfPipeline,
    VerifiedPackagePdf,
    _constraint_checks,
    _constraint_evaluation,
)
from paperworkshell.export.package_summary import (
    build_package_summary,
    build_package_summary_recovery,
)
from paperworkshell.export.safe_zip import SafeZipMember, SafeZipPolicy, write_safe_zip
from paperworkshell.preflight.core_rules import CoreCompletenessRule, CoreTemplateNoticeRule
from paperworkshell.preflight.engine import PreflightEngine, PreflightEvaluation
from paperworkshell.preflight.facts import (
    CasePreflightFacts,
    CaseSourcePreflightFact,
    ChecklistFactAvailability,
    ChecklistPreflightFacts,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
    TemplateFactAvailability,
    TemplateNoticeFact,
    TemplateNoticeKind,
    TemplatePreflightFacts,
)
from paperworkshell.preflight.models import (
    PreflightAuthority,
    PreflightDependency,
    PreflightInput,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
)
from paperworkshell.preflight.package_planner import PackageExportPlan, build_package_export_plan
from paperworkshell.preflight.portal_constraints import (
    PortalOutputMode,
    evaluate_portal_constraints,
    portal_constraint_profile_from_template,
)
from paperworkshell.storage.export_destination import ExportCommitState
from paperworkshell.storage.export_package_destination import (
    ExportPackageDestinationResult,
    PackageArtifactCommitResult,
)
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateSnapshot

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"30000000-0000-4000-8000-{n:012d}") for n in range(1, 5))
ASSIGNMENT_IDS = tuple(UUID(f"40000000-0000-4000-8000-{n:012d}") for n in range(1, 5))
CONFIRMATION_IDS = tuple(UUID(f"50000000-0000-4000-8000-{n:012d}") for n in range(1, 5))


@dataclass(frozen=True)
class SummaryInputs:
    snapshot: ManifestSnapshot
    template: TemplateSnapshot
    definition: ChecklistDefinition
    preflight: PreflightEvaluation
    plan: PackageExportPlan | None

    def build(self, outcome: PackageExportOutcome | None = None) -> PackageSummary:
        return build_package_summary(
            self.snapshot,
            self.template,
            self.definition,
            self.preflight,
            plan=self.plan,
            outcome=outcome,
        )


def summary_inputs(
    mode: PortalOutputMode = PortalOutputMode.SEPARATE_PDFS,
    *,
    missing: bool = False,
    maximum_file_bytes: int = 5_242_880,
    template: TemplateSnapshot | None = None,
    warning_heavy: bool = False,
    source_name: str = "Synthetic original.pdf",
) -> SummaryInputs:
    selected_template = template or SafeTemplateLoader(built_in_template_repository()).load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY, as_of=date(2026, 9, 5)
    )
    definition = checklist_definition_from_template(selected_template)
    count = 3 if missing else 4
    assignments: list[dict[str, object]] = []
    confirmations: list[dict[str, object]] = []
    for n, requirement in enumerate(definition.requirements[:count]):
        assignments.append(
            {
                "assignment_id": str(ASSIGNMENT_IDS[n]),
                "requirement_id": requirement.requirement_id,
                "page_ids": [str(PAGE_IDS[n])],
                "origin": "user",
                "status": "user_confirmed",
                "confirmation_id": str(CONFIRMATION_IDS[n]),
                "assigned_at": "2026-09-05T10:01:00Z",
            }
        )
        confirmations.append(
            {
                "confirmation_id": str(CONFIRMATION_IDS[n]),
                "confirmation_version": 1,
                "confirmation_code": ASSIGNMENT_CONFIRMATION_CODE,
                "statement_version": 1,
                "state": "confirmed",
                "actor": "user",
                "scope": {"kind": "assignment", "assignment_id": str(ASSIGNMENT_IDS[n])},
                "recorded_at": "2026-09-05T10:02:00Z",
            }
        )
    document: dict[str, object] = {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-09-05T10:00:00Z",
        "updated_at": "2026-09-05T10:03:00Z",
        "template_ref": definition.binding.to_manifest(),
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": source_name,
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 4096,
                "page_count": 4,
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-09-05T10:00:30Z",
            }
        ],
        "pages": [
            {"page_id": str(page), "source_id": str(SOURCE_ID), "source_page_index": n}
            for n, page in enumerate(PAGE_IDS)
        ],
        "page_sequence_base": [str(page) for page in PAGE_IDS],
        "page_sequence": [str(page) for page in PAGE_IDS],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": assignments,
        "confirmations": confirmations,
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf", "separate_pdfs", "zip"],
            "encryption_policy": "must_be_unencrypted",
            "maximum_file_count": 4,
            "maximum_total_bytes": 10_485_760,
            "maximum_file_bytes": maximum_file_bytes,
            "maximum_pages_per_file": 100,
        },
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    snapshot = ManifestSnapshot(CASE_ID, 2, 7, document, "b" * 64, "c" * 64)
    plan = (
        None
        if missing
        else build_package_export_plan(
            snapshot, definition, portal_constraint_profile_from_template(selected_template), mode
        )
    )
    evaluation = summary_preflight(snapshot, definition, plan, warning_heavy=warning_heavy)
    return SummaryInputs(snapshot, selected_template, definition, evaluation, plan)


def summary_preflight(
    snapshot: ManifestSnapshot,
    definition: ChecklistDefinition,
    plan: PackageExportPlan | None,
    *,
    warning_heavy: bool = False,
) -> PreflightEvaluation:
    projection = ChecklistCommandHistory.from_snapshot(snapshot, definition).projection
    assignments = tuple(
        item for requirement in projection.requirements for item in requirement.assignments
    )
    output = (
        plan.output_facts
        if plan is not None
        else OutputPlanPreflightFacts(
            CASE_ID, snapshot.revision, OutputPlanAvailability.UNAVAILABLE, "separate_pdfs", ()
        )
    )
    scopes = (
        (PreflightScope(PreflightScopeKind.CASE, CASE_ID),)
        + tuple(
            PreflightScope(PreflightScopeKind.REQUIREMENT, item.requirement_id, n)
            for n, item in enumerate(definition.requirements, 1)
        )
        + (PreflightScope(PreflightScopeKind.SOURCE, SOURCE_ID, 1),)
        + tuple(
            PreflightScope(PreflightScopeKind.ASSIGNMENT, item.assignment_id, n)
            for n, item in enumerate(assignments, 1)
        )
        + tuple(
            PreflightScope(PreflightScopeKind.PAGE, item, n) for n, item in enumerate(PAGE_IDS, 1)
        )
        + tuple(
            PreflightScope(PreflightScopeKind.OUTPUT, item.output_key, item.position)
            for item in output.items
        )
    )
    authority = PreflightAuthority(
        CASE_ID,
        snapshot.revision,
        snapshot.sha256,
        definition.binding,
        PreflightScopeCatalog(CASE_ID, scopes),
    )
    notices = (
        tuple(
            TemplateNoticeFact(
                f"synthetic-review-{n}",
                TemplateNoticeKind.NEEDS_REVIEW if n % 2 else TemplateNoticeKind.CONFIRMATION,
                n,
                f"Synthetic statement {n}.",
            )
            for n in range(1, 17)
        )
        if warning_heavy
        else ()
    )
    values = (
        (
            PreflightDependency.CASE,
            CasePreflightFacts(
                CASE_ID,
                snapshot.revision,
                definition.binding,
                (CaseSourcePreflightFact(SOURCE_ID, 1, DetectedMediaType.PDF, "a" * 64),),
                PAGE_IDS,
            ),
        ),
        (
            PreflightDependency.CHECKLIST,
            ChecklistPreflightFacts(
                CASE_ID,
                snapshot.revision,
                ChecklistFactAvailability.CURRENT,
                definition,
                projection,
            ),
        ),
        (
            PreflightDependency.TEMPLATE,
            TemplatePreflightFacts(
                CASE_ID,
                snapshot.revision,
                TemplateFactAvailability.CURRENT,
                definition.binding,
                notices,
            ),
        ),
        (PreflightDependency.OUTPUT_PLAN, output),
    )
    return PreflightEngine((CoreCompletenessRule(), CoreTemplateNoticeRule())).evaluate(
        tuple(PreflightInput(authority, dependency, value) for dependency, value in values)
    )


def summary_outcome(
    inputs: SummaryInputs,
    states: tuple[ExportCommitState, ...] | None = None,
    *,
    unsigned: bool = False,
) -> PackageExportOutcome:
    plan = inputs.plan
    assert plan is not None
    counted = {
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    }
    codes = (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    ) + ((PdfExportCheckCode.UNSIGNED_DERIVATIVE,) if unsigned else ())
    payload = b"%PDF-synthetic-summary-report-only\n"
    pdfs = tuple(
        VerifiedPackagePdf(
            item.output_key,
            item.position,
            item.requirement_id,
            item.page_ids,
            item.filename,
            PackagePdfPipeline.FLATTENED,
            PdfExportVerificationReport(
                "d" * 64,
                hashlib.sha256(payload).hexdigest(),
                len(payload),
                len(item.page_ids),
                tuple(
                    PdfExportCheck(code, len(item.page_ids) if code in counted else None)
                    for code in codes
                ),
                unsigned,
            ),
        )
        for item in plan.pdfs
    )
    archive = None
    if plan.archive is not None:
        archive = write_safe_zip(
            io.BytesIO(),
            tuple(
                SafeZipMember(
                    item.filename,
                    io.BytesIO(payload),
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                )
                for item in plan.pdfs
            ),
            policy=SafeZipPolicy.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    constraints = _constraint_evaluation(plan, pdfs, archive, 0)
    findings = evaluate_portal_constraints(constraints)
    report = PackageAggregateVerificationReport(
        plan, pdfs, constraints, archive, findings, _constraint_checks(plan, findings, archive)
    )
    if findings:
        return PackageExportOutcome(PackageExportStatus.CONSTRAINTS_BLOCKED, report)
    expected = (
        ((plan.archive.output_key, archive.archive_byte_size, archive.archive_sha256),)
        if plan.archive is not None and archive is not None
        else tuple(
            (item.output_key, item.base_report.output_byte_size, item.base_report.output_sha256)
            for item in pdfs
        )
    )
    selected_states = states or tuple(ExportCommitState.COMMITTED for _ in expected)
    destination = ExportPackageDestinationResult(
        tuple(
            PackageArtifactCommitResult(
                key,
                n,
                state,
                byte_size if state is ExportCommitState.COMMITTED else None,
                digest if state is ExportCommitState.COMMITTED else None,
                state is ExportCommitState.COMMITTED,
            )
            for n, ((key, byte_size, digest), state) in enumerate(
                zip(expected, selected_states, strict=True), 1
            )
        )
    )
    status = (
        PackageExportStatus.COMMITTED
        if destination.committed
        else (
            PackageExportStatus.UNCERTAIN
            if destination.uncertain
            else PackageExportStatus.PARTIAL
            if destination.partial
            else PackageExportStatus.NOT_COMMITTED
        )
    )
    return PackageExportOutcome(status, report, destination)


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
def test_complete_summary_reports_exact_names_counts_bytes_and_performed_checks(
    mode: PortalOutputMode,
) -> None:
    inputs = summary_inputs(mode)
    outcome = summary_outcome(inputs)
    summary = inputs.build(outcome)
    text = summary.to_plain_text()
    assert summary.status is PackageSummaryStatus.COMMITTED
    assert summary.file_count == (4 if mode is PortalOutputMode.SEPARATE_PDFS else 1)
    assert summary.saved_file_count == summary.file_count
    assert "Active pages: 4." in text
    assert "Application form" in text and "Proof of address" in text
    assert "The qpdf structural check completed without warnings." in text
    assert "Maximum individual file size." in text
    for output in summary.outputs:
        assert output.filename in text
        assert f"{output.byte_size} bytes" in text
        assert output.state is PackageSummaryOutputState.COMMITTED
    if mode is PortalOutputMode.ZIP:
        assert text.count("ZIP member") == 4
        assert "safe leaf names" in text
    assert not any(token in text for token in (str(CASE_ID), "b" * 64, "Synthetic original.pdf"))


def test_preview_does_not_claim_pdf_or_byte_verification_before_export() -> None:
    summary = summary_inputs().build()
    text = summary.to_plain_text()
    assert summary.saved_file_count == 0
    assert all(output.byte_size is None and not output.checks for output in summary.outputs)
    assert "Total file size: not yet verified." in text
    assert "Check complete: Maximum" not in text
    assert (
        "Final file sizes, PDF verification, and destination save checks have not been completed."
        in text
    )


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
@pytest.mark.parametrize("optimization_outcome", tuple(PdfOptimizationOutcome))
def test_summary_includes_only_the_recorded_optimization_outcome(
    mode: PortalOutputMode, optimization_outcome: PdfOptimizationOutcome
) -> None:
    inputs = summary_inputs(mode)
    outcome = summary_outcome(inputs)
    optimized = optimization_outcome is PdfOptimizationOutcome.OPTIMIZED
    pdfs = []
    for item in outcome.report.pdfs:
        base = item.base_report
        optimization = PdfOptimizationReport(
            profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            outcome=optimization_outcome,
            baseline_byte_size=base.output_byte_size + (100 if optimized else 0),
            candidate_byte_size=base.output_byte_size if optimized else None,
            selected_byte_size=base.output_byte_size,
            verified_page_count=base.page_count if optimized else 0,
        )
        pdfs.append(
            replace(item, base_report=replace(base, report_version=2, optimization=optimization))
        )
    updated = replace(outcome, report=replace(outcome.report, pdfs=tuple(pdfs)))
    text = inputs.build(updated).to_plain_text()
    for item in pdfs:
        assert item.base_report.optimization is not None
        assert item.base_report.optimization.claim in text
    assert "Safe optimization" not in inputs.build().to_plain_text()


def test_missing_scenario_shows_missing_category_unassigned_page_and_recovery() -> None:
    summary = summary_inputs(missing=True).build()
    text = summary.to_plain_text()
    assert summary.status is PackageSummaryStatus.NEEDS_REVIEW
    assert "Supporting evidence: Missing required assignments." in text
    assert "Unassigned pages: 1." in text
    assert "Page 4" in text
    assert "Add and confirm the required documents or pages before export." in text
    assert summary.file_count == 0


def test_warning_heavy_scenario_preserves_every_preflight_finding_and_specific_template_review() -> (
    None
):
    inputs = summary_inputs(warning_heavy=True)
    summary = inputs.build(summary_outcome(inputs))
    assert len(summary.review_items) == len(inputs.preflight.report.findings) + 6
    text = summary.to_plain_text()
    assert "identity evidence" in text
    assert (
        "I reviewed the document assignments, page order, and filenames shown for this package."
        in text
    )
    assert text.count("A template statement") == 16
    assert summary.status is PackageSummaryStatus.COMMITTED


@pytest.mark.parametrize(
    ("states", "expected", "saved"),
    [
        (
            (
                ExportCommitState.COMMITTED,
                ExportCommitState.NOT_COMMITTED,
                ExportCommitState.NOT_COMMITTED,
                ExportCommitState.NOT_COMMITTED,
            ),
            PackageSummaryStatus.PARTIAL,
            1,
        ),
        (
            (
                ExportCommitState.COMMITTED,
                ExportCommitState.UNKNOWN,
                ExportCommitState.NOT_COMMITTED,
                ExportCommitState.NOT_COMMITTED,
            ),
            PackageSummaryStatus.UNCERTAIN,
            1,
        ),
        ((ExportCommitState.DESTINATION_EXISTS,) * 4, PackageSummaryStatus.NOT_COMMITTED, 0),
    ],
)
def test_failure_summary_preserves_each_publication_state(
    states: tuple[ExportCommitState, ...], expected: PackageSummaryStatus, saved: int
) -> None:
    inputs = summary_inputs()
    summary = inputs.build(summary_outcome(inputs, states))
    assert summary.status is expected
    assert summary.saved_file_count == saved
    assert "Package saved." not in summary.to_plain_text()
    assert (
        sum(
            "saved file was reopened" in check
            for output in summary.outputs
            for check in output.checks
        )
        == saved
    )


def test_constraint_block_reports_all_scoped_violations_and_no_saved_files() -> None:
    inputs = summary_inputs(maximum_file_bytes=1)
    summary = inputs.build(summary_outcome(inputs))
    assert summary.status is PackageSummaryStatus.CONSTRAINTS_BLOCKED
    assert summary.saved_file_count == 0
    assert summary.to_plain_text().count("Constraint not met: Maximum individual file size.") == 1
    assert sum(item.scope.startswith("PDF ") for item in summary.review_items) == 4


def test_unsigned_derivative_warning_is_not_suppressed_after_success() -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF)
    text = inputs.build(summary_outcome(inputs, unsigned=True)).to_plain_text()
    assert "unsigned derivative" in text
    assert "signature validity" in text


def test_same_manifest_stale_output_mode_fails_even_when_scopes_match() -> None:
    separate = summary_inputs(PortalOutputMode.SEPARATE_PDFS)
    zipped = summary_inputs(PortalOutputMode.ZIP)
    assert separate.preflight.authority == zipped.preflight.authority
    with pytest.raises(PackageSummaryError) as error:
        replace(zipped, preflight=separate.preflight).build()
    assert error.value.code is PackageSummaryErrorCode.AUTHORITY_MISMATCH


@pytest.mark.parametrize("mutation", ("sha", "cas", "template", "definition", "outcome"))
def test_summary_rejects_mismatched_authority(mutation: str) -> None:
    inputs = summary_inputs()
    outcome = summary_outcome(inputs)
    if mutation in {"sha", "cas"}:
        snapshot = ManifestSnapshot(
            CASE_ID,
            2,
            7,
            inputs.snapshot.document,
            "e" * 64 if mutation == "sha" else inputs.snapshot.sha256,
            "f" * 64 if mutation == "cas" else inputs.snapshot.cas_token,
        )
        inputs = replace(inputs, snapshot=snapshot)
    elif mutation == "template":
        inputs = replace(
            inputs,
            template=TemplateSnapshot(
                key=inputs.template.key,
                byte_size=inputs.template.byte_size,
                sha256="e" * 64,
                freshness=inputs.template.freshness,
                presentation=inputs.template.presentation,
                document=inputs.template.document,
            ),
        )
    elif mutation == "definition":
        definition = replace(
            inputs.definition, requirements=tuple(reversed(inputs.definition.requirements))
        )
        inputs = replace(inputs, definition=definition)
    else:
        outcome = summary_outcome(summary_inputs(PortalOutputMode.COMBINED_PDF))
    with pytest.raises(PackageSummaryError) as error:
        inputs.build(outcome)
    assert error.value.code is PackageSummaryErrorCode.AUTHORITY_MISMATCH


def test_summary_rejects_destination_result_with_unrelated_committed_bytes() -> None:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF)
    outcome = summary_outcome(inputs)
    assert outcome.destination is not None
    wrong = replace(outcome.destination.artifacts[0], sha256="e" * 64)
    forged = replace(outcome, destination=ExportPackageDestinationResult((wrong,)))
    with pytest.raises(PackageSummaryError) as error:
        inputs.build(forged)
    assert error.value.code is PackageSummaryErrorCode.AUTHORITY_MISMATCH


def test_summary_never_marks_empty_or_unverified_outputs_successful() -> None:
    with pytest.raises(PackageSummaryError):
        PackageSummary(PackageSummaryStatus.COMMITTED)
    with pytest.raises(PackageSummaryError):
        PackageSummary(cast(PackageSummaryStatus, "committed"))


@pytest.mark.parametrize("state", tuple(ExportCommitState))
def test_recovery_inventory_preserves_saved_states_when_detailed_summary_fails(
    state: ExportCommitState,
) -> None:
    inputs = summary_inputs()
    outcome = summary_outcome(inputs, (state,) * 4)
    summary = build_package_summary_recovery(outcome)
    text = summary.to_plain_text()
    assert summary.status.value == outcome.status.value
    assert summary.file_count == 4
    assert summary.saved_file_count == (4 if state is ExportCommitState.COMMITTED else 0)
    assert "Detailed package summary unavailable" in text
    assert "Checklist details are unavailable" in text
    assert "Unassigned pages: 0" not in text
    assert all(output.filename in text for output in summary.outputs)
    assert inputs.snapshot.sha256 not in text


def test_zip_recovery_preserves_outer_artifact_and_each_verified_pdf_member() -> None:
    inputs = summary_inputs(PortalOutputMode.ZIP)
    summary = build_package_summary_recovery(summary_outcome(inputs))
    assert summary.file_count == summary.saved_file_count == 1
    assert len(summary.outputs) == 5
    assert summary.to_plain_text().count("ZIP member") == 4
