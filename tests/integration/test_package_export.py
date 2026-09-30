"""PWS-0407 integration coverage for verified package orchestration."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import TemplateBinding
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportVerificationReport,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.package_export import (
    PackageConstraintCheckCode,
    PackageExportError,
    PackageExportErrorCode,
    PackageExportOutcome,
    PackageExportProgress,
    PackageExportStatus,
    PackagePdfPipeline,
    export_package_plan,
)
from paperworkshell.preflight.facts import (
    OutputItemPreflightFact,
    OutputOverlayFlatteningAvailability,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
)
from paperworkshell.preflight.package_planner import (
    ActivePackageConstraints,
    PackageExportPlan,
    PackagePlanAuthority,
    PlannedPackageArchive,
    PlannedPackagePdf,
)
from paperworkshell.preflight.portal_constraints import (
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalOutputMode,
)
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.export_destination import (
    ExportDestinationDependencies,
    WindowsExportDestination,
    _rename_stage_no_replace,
)
from paperworkshell.storage.export_package_destination import (
    ExportPackageDestinationDependencies,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"20000000-0000-4000-8000-{index:012d}") for index in range(1, 4))
ASSIGNMENT_IDS = tuple(UUID(f"30000000-0000-4000-8000-{index:012d}") for index in range(1, 4))
BINDING = TemplateBinding("generic-portal-application", "1.0.0", "a" * 64)


@dataclass(frozen=True)
class _CandidateReport:
    output_key: str
    position: int
    page_ids: tuple[UUID, ...]
    pipeline: PackagePdfPipeline
    base_report: PdfExportVerificationReport


class _Candidate:
    def __init__(self, payload: bytes, report: _CandidateReport) -> None:
        self._payload = payload
        self._position = 0
        self._report = report

    @property
    def report(self) -> _CandidateReport:
        return self._report

    def read(self, size: int, /) -> bytes:
        selected = self._payload[self._position : self._position + size]
        self._position += len(selected)
        return selected

    def __enter__(self) -> _Candidate:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return


class _Producer:
    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        fail_position: int | None = None,
        wrong_pages: bool = False,
    ) -> None:
        self._payloads = payloads
        self._fail_position = fail_position
        self._wrong_pages = wrong_pages
        self.calls: list[str] = []

    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> _Candidate:
        del snapshot, deadline, cancellation, progress
        self.calls.append(planned_pdf.output_key)
        if planned_pdf.position == self._fail_position:
            raise RuntimeError("PRIVATE producer detail")
        payload = self._payloads[planned_pdf.output_key]
        page_ids = (
            tuple(reversed(planned_pdf.page_ids)) if self._wrong_pages else planned_pdf.page_ids
        )
        return _Candidate(
            payload,
            _CandidateReport(
                planned_pdf.output_key,
                planned_pdf.position,
                page_ids,
                PackagePdfPipeline.FLATTENED,
                _pdf_report(payload, len(planned_pdf.page_ids)),
            ),
        )


def _snapshot(*, sha256: str = "b" * 64) -> ManifestSnapshot:
    return ManifestSnapshot(
        CASE_ID,
        2,
        7,
        {"schema_version": 2, "case_id": str(CASE_ID), "revision": 7},
        sha256,
        "c" * 64,
    )


def _constraints(
    mode: PortalOutputMode,
    *,
    maximum_file_count: int | None = 4,
    maximum_total_bytes: int | None = 1024 * 1024,
    maximum_file_bytes: int | None = 1024 * 1024,
    maximum_pages_per_file: int | None = 10,
) -> ActivePackageConstraints:
    return ActivePackageConstraints(
        PortalConstraints(
            (mode,),
            PortalEncryptionPolicy.MUST_BE_UNENCRYPTED,
            maximum_file_count=maximum_file_count,
            maximum_total_bytes=maximum_total_bytes,
            maximum_file_bytes=maximum_file_bytes,
            maximum_pages_per_file=maximum_pages_per_file,
        ),
        (),
    )


def _plan(
    mode: PortalOutputMode,
    *,
    maximum_file_bytes: int | None = 1024 * 1024,
) -> PackageExportPlan:
    pdfs: tuple[PlannedPackagePdf, ...]
    if mode is PortalOutputMode.COMBINED_PDF:
        pdfs = (
            PlannedPackagePdf(
                "package",
                1,
                None,
                ASSIGNMENT_IDS,
                PAGE_IDS,
                "packet.pdf",
                1,
            ),
        )
        archive = None
    else:
        pdfs = (
            PlannedPackagePdf(
                "alpha",
                1,
                "alpha",
                (ASSIGNMENT_IDS[0],),
                (PAGE_IDS[1], PAGE_IDS[0]),
                "zeta.pdf",
                1,
            ),
            PlannedPackagePdf(
                "beta",
                2,
                "beta",
                (ASSIGNMENT_IDS[1],),
                (PAGE_IDS[2],),
                "alpha.pdf",
                1,
            ),
        )
        archive = (
            PlannedPackageArchive("archive", "packet.zip", 1, ("alpha", "beta"))
            if mode is PortalOutputMode.ZIP
            else None
        )
    facts = OutputPlanPreflightFacts(
        CASE_ID,
        7,
        OutputPlanAvailability.CURRENT,
        mode.value,
        tuple(OutputItemPreflightFact(pdf.output_key, pdf.position, pdf.page_ids) for pdf in pdfs),
        OutputOverlayFlatteningAvailability.CURRENT,
        (),
    )
    return PackageExportPlan(
        authority=PackagePlanAuthority(CASE_ID, 7, "b" * 64, "c" * 64, BINDING),
        mode=mode,
        active_constraints=_constraints(mode, maximum_file_bytes=maximum_file_bytes),
        pdfs=pdfs,
        archive=archive,
        output_facts=facts,
    )


def _pdf_report(payload: bytes, page_count: int) -> PdfExportVerificationReport:
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
    )
    return PdfExportVerificationReport(
        "d" * 64,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        page_count,
        tuple(PdfExportCheck(code, page_count if code in counted else None) for code in codes),
        False,
    )


def _payloads(plan: PackageExportPlan) -> dict[str, bytes]:
    return {
        pdf.output_key: f"%PDF-1.7\n% output {pdf.position}\n%%EOF\n".encode() for pdf in plan.pdfs
    }


def _export(
    tmp_path: Path,
    plan: PackageExportPlan,
    producer: _Producer,
    *,
    progress: list[PackageExportProgress] | None = None,
    dependencies: ExportPackageDestinationDependencies | None = None,
) -> PackageExportOutcome:
    return export_package_plan(
        _snapshot(),
        plan,
        tmp_path,
        producer,
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=(lambda value: progress.append(value)) if progress is not None else lambda _: None,
        destination_dependencies=dependencies,
    )


def test_combined_package_commits_only_after_all_pdf_and_constraint_checks(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.COMBINED_PDF)
    payloads = _payloads(plan)
    progress: list[PackageExportProgress] = []

    outcome = _export(tmp_path, plan, _Producer(payloads), progress=progress)

    assert outcome.status is PackageExportStatus.COMMITTED
    assert outcome.successful
    assert outcome.report.constraints_satisfied
    assert (tmp_path / "packet.pdf").read_bytes() == payloads["package"]
    assert outcome.destination is not None
    assert outcome.destination.artifacts[0].destination_reopened
    assert progress[-1].phase.value == "complete"
    assert progress[-1].completed_pages == len(PAGE_IDS)


def test_separate_pdfs_preserve_template_group_order_and_publish_exact_set(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)
    payloads = _payloads(plan)

    outcome = _export(tmp_path, plan, _Producer(payloads))

    assert outcome.status is PackageExportStatus.COMMITTED
    assert tuple(value.output_key for value in outcome.report.pdfs) == ("alpha", "beta")
    assert tuple(value.page_ids for value in outcome.report.pdfs) == (
        (PAGE_IDS[1], PAGE_IDS[0]),
        (PAGE_IDS[2],),
    )
    assert (tmp_path / "zeta.pdf").read_bytes() == payloads["alpha"]
    assert (tmp_path / "alpha.pdf").read_bytes() == payloads["beta"]


def test_zip_round_trip_preserves_planner_member_order_and_exact_pdf_bytes(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.ZIP)
    payloads = _payloads(plan)

    outcome = _export(tmp_path, plan, _Producer(payloads))

    assert outcome.status is PackageExportStatus.COMMITTED
    assert outcome.report.archive is not None
    assert tuple(member.filename for member in outcome.report.archive.members) == (
        "zeta.pdf",
        "alpha.pdf",
    )
    with zipfile.ZipFile(tmp_path / "packet.zip") as archive:
        assert archive.namelist() == ["zeta.pdf", "alpha.pdf"]
        assert archive.read("zeta.pdf") == payloads["alpha"]
        assert archive.read("alpha.pdf") == payloads["beta"]
    assert not (tmp_path / "zeta.pdf").exists()
    assert not (tmp_path / "alpha.pdf").exists()


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
def test_constrained_free_space_reserve_refuses_package_and_clean_retry_succeeds(
    tmp_path: Path,
    mode: PortalOutputMode,
) -> None:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    plan = _plan(mode)
    payloads = _payloads(plan)
    first_payload = payloads[plan.pdfs[0].output_key]
    available = policy.minimum_export_free_space_reserve_bytes + len(first_payload) - 1
    destination = WindowsExportDestination(
        ExportDestinationDependencies(available_space=lambda _: available),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )

    def export() -> PackageExportOutcome:
        return export_package_plan(
            _snapshot(),
            plan,
            tmp_path,
            _Producer(payloads),
            destination,
            policy,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    with pytest.raises(PackageExportError) as captured:
        export()

    assert captured.value.code is PackageExportErrorCode.DESTINATION_FAILED
    assert tuple(tmp_path.iterdir()) == ()

    available = (
        policy.minimum_export_free_space_reserve_bytes + policy.maximum_temporary_storage_bytes
    )
    outcome = export()

    assert outcome.status is PackageExportStatus.COMMITTED
    assert outcome.destination is not None
    assert all(artifact.destination_reopened for artifact in outcome.destination.artifacts)
    expected_names = (
        (plan.archive.filename,)
        if plan.archive is not None
        else tuple(pdf.filename for pdf in plan.pdfs)
    )
    assert {path.name for path in tmp_path.iterdir()} == set(expected_names)


@pytest.mark.parametrize("delta", (0, -1))
def test_exact_file_size_limit_passes_and_one_byte_lower_blocks(
    tmp_path: Path,
    delta: int,
) -> None:
    baseline = _plan(PortalOutputMode.COMBINED_PDF)
    payloads = _payloads(baseline)
    limit = len(payloads["package"]) + delta
    plan = _plan(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=limit)

    outcome = _export(tmp_path, plan, _Producer(payloads))

    if delta == 0:
        assert outcome.status is PackageExportStatus.COMMITTED
        assert (tmp_path / "packet.pdf").exists()
    else:
        assert outcome.status is PackageExportStatus.CONSTRAINTS_BLOCKED
        failed = {check.code for check in outcome.report.constraint_checks if not check.passed}
        assert failed == {PackageConstraintCheckCode.FILE_SIZE_WITHIN_LIMIT}
        assert tuple(tmp_path.iterdir()) == ()


def test_repeated_scoped_file_size_findings_block_with_one_check_code(
    tmp_path: Path,
) -> None:
    baseline = _plan(PortalOutputMode.SEPARATE_PDFS)
    payloads = _payloads(baseline)
    limit = min(map(len, payloads.values())) - 1
    plan = _plan(PortalOutputMode.SEPARATE_PDFS, maximum_file_bytes=limit)

    outcome = _export(tmp_path, plan, _Producer(payloads))

    assert outcome.status is PackageExportStatus.CONSTRAINTS_BLOCKED
    findings = tuple(
        finding
        for finding in outcome.report.constraint_findings
        if finding.code == "PORTAL_FILE_SIZE_EXCEEDED"
    )
    assert tuple(finding.scope.item_id for finding in findings) == ("alpha", "beta")
    check = next(
        value
        for value in outcome.report.constraint_checks
        if value.code is PackageConstraintCheckCode.FILE_SIZE_WITHIN_LIMIT
    )
    assert not check.passed
    assert check.finding_codes == ("PORTAL_FILE_SIZE_EXCEEDED",)
    assert outcome.destination is None
    assert tuple(tmp_path.iterdir()) == ()


def test_second_publication_failure_returns_partial_inventory_never_success(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)
    payloads = _payloads(plan)
    calls = 0

    def rename_stage(
        stage: WindowsHandle,
        parent: WindowsHandle,
        name: str,
    ) -> tuple[bool, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            return False, 5
        return _rename_stage_no_replace(stage, parent, name)

    outcome = _export(
        tmp_path,
        plan,
        _Producer(payloads),
        dependencies=ExportPackageDestinationDependencies(rename_stage=rename_stage),
    )

    assert outcome.status is PackageExportStatus.PARTIAL
    assert not outcome.successful
    assert outcome.destination is not None and outcome.destination.partial
    assert (tmp_path / "zeta.pdf").exists()
    assert not (tmp_path / "alpha.pdf").exists()


def test_existing_member_blocks_entire_new_set_without_replacement(tmp_path: Path) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)
    prior = b"prior verified artifact"
    (tmp_path / "zeta.pdf").write_bytes(prior)

    outcome = _export(tmp_path, plan, _Producer(_payloads(plan)))

    assert outcome.status is PackageExportStatus.NOT_COMMITTED
    assert (tmp_path / "zeta.pdf").read_bytes() == prior
    assert not (tmp_path / "alpha.pdf").exists()


def test_second_pdf_failure_cleans_all_private_stages_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)

    with pytest.raises(PackageExportError) as captured:
        _export(tmp_path, plan, _Producer(_payloads(plan), fail_position=2))

    assert captured.value.code is PackageExportErrorCode.PDF_FAILED
    assert "PRIVATE" not in str(captured.value)
    assert tuple(tmp_path.iterdir()) == ()


def test_zip_cancellation_from_writer_propagates_and_cleans_every_stage(tmp_path: Path) -> None:
    plan = _plan(PortalOutputMode.ZIP)
    armed = False
    false_calls_remaining = 0

    def cancellation() -> bool:
        nonlocal false_calls_remaining
        if not armed:
            return False
        if false_calls_remaining:
            false_calls_remaining -= 1
            return False
        return True

    def progress(value: PackageExportProgress) -> None:
        nonlocal armed, false_calls_remaining
        if value.phase.value == "packaging_zip":
            armed = True
            false_calls_remaining = 1

    with pytest.raises(PackageExportError) as captured:
        export_package_plan(
            _snapshot(),
            plan,
            tmp_path,
            _Producer(_payloads(plan)),
            WindowsExportDestination(minimum_free_space_reserve_bytes=0),
            CONSTRAINED_RESOURCE_POLICY_V1,
            deadline=lambda: False,
            cancellation=cancellation,
            progress=progress,
        )

    assert captured.value.code is PackageExportErrorCode.CANCELLED
    assert tuple(tmp_path.iterdir()) == ()


def test_candidate_for_wrong_page_subset_is_rejected_before_publication(tmp_path: Path) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)

    with pytest.raises(PackageExportError) as captured:
        _export(tmp_path, plan, _Producer(_payloads(plan), wrong_pages=True))

    assert captured.value.code is PackageExportErrorCode.PDF_FAILED
    assert tuple(tmp_path.iterdir()) == ()


def test_stale_snapshot_authority_fails_before_producer_or_destination_access(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.COMBINED_PDF)
    producer = _Producer(_payloads(plan))

    with pytest.raises(PackageExportError) as captured:
        export_package_plan(
            _snapshot(sha256="e" * 64),
            plan,
            tmp_path,
            producer,
            WindowsExportDestination(minimum_free_space_reserve_bytes=0),
            CONSTRAINED_RESOURCE_POLICY_V1,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert captured.value.code is PackageExportErrorCode.PLAN_STALE
    assert producer.calls == []
    assert tuple(tmp_path.iterdir()) == ()


def test_reports_and_errors_hide_names_identifiers_paths_and_digests(tmp_path: Path) -> None:
    plan = _plan(PortalOutputMode.COMBINED_PDF)
    outcome = _export(tmp_path, plan, _Producer(_payloads(plan)))
    rendered = f"{outcome!r} {outcome.report!r} {outcome.report.pdfs[0]!r}"

    assert "packet.pdf" not in rendered
    assert str(CASE_ID) not in rendered
    assert "b" * 64 not in rendered
    assert str(tmp_path) not in rendered

    stale = replace(plan.authority, manifest_sha256="f" * 64)
    stale_plan = PackageExportPlan(
        authority=stale,
        mode=plan.mode,
        active_constraints=plan.active_constraints,
        pdfs=plan.pdfs,
        archive=plan.archive,
        output_facts=plan.output_facts,
    )
    with pytest.raises(PackageExportError) as captured:
        _export(tmp_path / "unused", stale_plan, _Producer(_payloads(plan)))
    assert "unused" not in f"{captured.value!r} {captured.value} {captured.value.args!r}"
