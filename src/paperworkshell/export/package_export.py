"""Verified multi-PDF and ZIP package orchestration for one immutable plan.

This adapter owns destination-local staging and publication.  It consumes a
pure package plan plus a PDF-producing application port; it never interprets
checklist state, template expressions, or caller paths as archive members.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Final, Never, Protocol, Self, runtime_checkable
from uuid import UUID

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportProgress,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_inspection import PdfEncryptionState
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.export.safe_zip import (
    SafeZipError,
    SafeZipErrorCode,
    SafeZipMember,
    SafeZipPolicy,
    SafeZipReport,
    write_safe_zip,
)
from paperworkshell.preflight.facts import OutputItemPreflightFact, OutputPlanPreflightFacts
from paperworkshell.preflight.models import PreflightError, PreflightFindingTarget
from paperworkshell.preflight.package_planner import PackageExportPlan, PlannedPackagePdf
from paperworkshell.preflight.portal_constraints import (
    PortalArchiveObservation,
    PortalConstraintEvaluation,
    PortalEncryptionPolicy,
    PortalOutputMode,
    PortalOutputObservation,
    evaluate_portal_constraints,
    normalize_output_filename,
)
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    WindowsExportDestination,
)
from paperworkshell.storage.export_package_destination import (
    ExportPackageDestinationDependencies,
    ExportPackageDestinationError,
    ExportPackageDestinationErrorCode,
    ExportPackageDestinationResult,
    WindowsExportPackageTransaction,
)

PACKAGE_EXPORT_REPORT_VERSION: Final = 1
PACKAGE_EXPORT_OUTCOME_VERSION: Final = 1

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")
_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class PackageExportErrorCode(StrEnum):
    """Closed, path-free failures before a trustworthy outcome exists."""

    MODEL_INVALID = "PACKAGE_EXPORT_MODEL_INVALID"
    PLAN_STALE = "PACKAGE_EXPORT_PLAN_STALE"
    PLAN_INVALID = "PACKAGE_EXPORT_PLAN_INVALID"
    PDF_FAILED = "PACKAGE_EXPORT_PDF_FAILED"
    ZIP_FAILED = "PACKAGE_EXPORT_ZIP_FAILED"
    DESTINATION_FAILED = "PACKAGE_EXPORT_DESTINATION_FAILED"
    LIMIT_EXCEEDED = "PACKAGE_EXPORT_LIMIT_EXCEEDED"
    CANCELLED = "PACKAGE_EXPORT_CANCELLED"
    TIMED_OUT = "PACKAGE_EXPORT_TIMED_OUT"
    INTERNAL_ERROR = "PACKAGE_EXPORT_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[PackageExportErrorCode, str]] = {
    PackageExportErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid package-export data."),
    PackageExportErrorCode.PLAN_STALE: (
        "The case changed after this package was planned. Refresh the package and try again."
    ),
    PackageExportErrorCode.PLAN_INVALID: (
        "The package plan is not valid for the selected destination."
    ),
    PackageExportErrorCode.PDF_FAILED: (
        "PaperWorkShell could not create and verify every planned PDF."
    ),
    PackageExportErrorCode.ZIP_FAILED: (
        "PaperWorkShell could not create and verify the ZIP package."
    ),
    PackageExportErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not safely publish the verified package."
    ),
    PackageExportErrorCode.LIMIT_EXCEEDED: (
        "The package exceeds a configured processing or temporary-storage limit."
    ),
    PackageExportErrorCode.CANCELLED: "Package export was cancelled before completion.",
    PackageExportErrorCode.TIMED_OUT: "Package export exceeded its configured time limit.",
    PackageExportErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete package export safely."
    ),
}


@dataclass(slots=True, repr=False)
class PackageExportError(ValueError):
    """Sanitized package failure retaining no names, paths, IDs, or digests."""

    code: PackageExportErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackageExportErrorCode:
            self.code = PackageExportErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "package_export.operation"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PackageExportError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_package_export(code: PackageExportErrorCode, context: str) -> Never:
    error = PackageExportError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PackagePdfPipeline(StrEnum):
    """Pipeline facts accepted from the application PDF producer."""

    FLATTENED = "flattened"
    SEARCHABLE = "searchable"
    IMAGE_STAGE = "image_stage"


class PackageExportStatus(StrEnum):
    """Truthful terminal state for one package attempt."""

    COMMITTED = "committed"
    CONSTRAINTS_BLOCKED = "constraints_blocked"
    NOT_COMMITTED = "not_committed"
    PARTIAL = "partial"
    UNCERTAIN = "uncertain"


class PackageExportProgressPhase(StrEnum):
    PREPARING = "preparing"
    GENERATING_PDFS = "generating_pdfs"
    PACKAGING_ZIP = "packaging_zip"
    CHECKING_CONSTRAINTS = "checking_constraints"
    COMMITTING = "committing"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PackageExportProgress:
    """Bounded, path-free aggregate progress suitable for UI announcements."""

    phase: PackageExportProgressPhase
    output_position: int
    output_count: int
    completed_pages: int
    total_pages: int

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not PackageExportProgressPhase
            or type(self.output_position) is not int
            or type(self.output_count) is not int
            or not 1 <= self.output_count <= 128
            or not 0 <= self.output_position <= self.output_count
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= 1_000
            or not 0 <= self.completed_pages <= self.total_pages
            or (
                self.phase in {PackageExportProgressPhase.PREPARING}
                and (self.output_position != 0 or self.completed_pages != 0)
            )
            or (
                self.phase is PackageExportProgressPhase.COMPLETE
                and (
                    self.output_position != self.output_count
                    or self.completed_pages != self.total_pages
                )
            )
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.progress",
            )


class PackageExportProgressSink(Protocol):
    def __call__(self, value: PackageExportProgress, /) -> None: ...


class PackagePdfCandidateReportPort(Protocol):
    @property
    def output_key(self) -> str: ...

    @property
    def position(self) -> int: ...

    @property
    def page_ids(self) -> tuple[UUID, ...]: ...

    @property
    def pipeline(self) -> object: ...

    @property
    def base_report(self) -> PdfExportVerificationReport: ...


class PackagePdfCandidatePort(Protocol):
    @property
    def report(self) -> PackagePdfCandidateReportPort: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PackagePdfProducerPort(Protocol):
    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[PdfExportProgress], None],
    ) -> PackagePdfCandidatePort: ...


@runtime_checkable
class PackagePdfReservedCapacityPort(Protocol):
    """Account destination-local stages in the next worker's storage allowance."""

    def reserve_temporary_bytes(self, byte_count: int) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedPackagePdf:
    """One exact PDF candidate after worker verification and local staging."""

    output_key: str
    position: int
    requirement_id: str | None = field(repr=False)
    page_ids: tuple[UUID, ...] = field(repr=False)
    filename: str = field(repr=False)
    pipeline: PackagePdfPipeline
    base_report: PdfExportVerificationReport = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.output_key) is not str
            or _TOKEN.fullmatch(self.output_key) is None
            or type(self.position) is not int
            or not 1 <= self.position <= 128
            or (
                self.requirement_id is not None
                and (
                    type(self.requirement_id) is not str
                    or _TOKEN.fullmatch(self.requirement_id) is None
                )
            )
            or type(self.page_ids) is not tuple
            or not self.page_ids
            or len(set(self.page_ids)) != len(self.page_ids)
            or any(type(value) is not UUID or value.version != 4 for value in self.page_ids)
            or type(self.pipeline) is not PackagePdfPipeline
            or type(self.base_report) is not PdfExportVerificationReport
            or self.base_report.page_count != len(self.page_ids)
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.pdf_report",
            )
        try:
            if normalize_output_filename(self.filename) != self.filename:
                raise ValueError
        except PreflightError, ValueError:
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.pdf_name",
            )

    def __repr__(self) -> str:
        return (
            "VerifiedPackagePdf("
            f"position={self.position!r}, page_count={len(self.page_ids)}, "
            f"pipeline={self.pipeline.value!r}, byte_size={self.base_report.output_byte_size!r})"
        )


class PackageConstraintCheckCode(StrEnum):
    MODE_ALLOWED = "PACKAGE_MODE_ALLOWED"
    ORGANIZATION_VALID = "PACKAGE_ORGANIZATION_VALID"
    FILENAMES_VALID = "PACKAGE_FILENAMES_VALID"
    FILE_COUNT_WITHIN_LIMIT = "PACKAGE_FILE_COUNT_WITHIN_LIMIT"
    TOTAL_SIZE_WITHIN_LIMIT = "PACKAGE_TOTAL_SIZE_WITHIN_LIMIT"
    FILE_SIZE_WITHIN_LIMIT = "PACKAGE_FILE_SIZE_WITHIN_LIMIT"
    PAGE_COUNT_WITHIN_LIMIT = "PACKAGE_PAGE_COUNT_WITHIN_LIMIT"
    ENCRYPTION_POLICY_SATISFIED = "PACKAGE_ENCRYPTION_POLICY_SATISFIED"
    ZIP_ARCHIVE_VERIFIED = "PACKAGE_ZIP_ARCHIVE_VERIFIED"


@dataclass(frozen=True, slots=True)
class PackageConstraintCheck:
    """One explicitly reported active constraint or fixed output invariant."""

    code: PackageConstraintCheckCode
    passed: bool
    finding_codes: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.code) is not PackageConstraintCheckCode
            or type(self.passed) is not bool
            or type(self.finding_codes) is not tuple
            or any(
                type(value) is not str or not value.startswith("PORTAL_")
                for value in self.finding_codes
            )
            or len(set(self.finding_codes)) != len(self.finding_codes)
            or self.passed == bool(self.finding_codes)
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.constraint_check",
            )


@dataclass(frozen=True, slots=True, repr=False)
class PackageAggregateVerificationReport:
    """Per-output PDF evidence plus ZIP and active-constraint evaluation."""

    plan: PackageExportPlan = field(repr=False)
    pdfs: tuple[VerifiedPackagePdf, ...] = field(repr=False)
    constraint_evaluation: PortalConstraintEvaluation = field(repr=False)
    archive: SafeZipReport | None = field(default=None, repr=False)
    constraint_findings: tuple[PreflightFindingTarget, ...] = field(default=(), repr=False)
    constraint_checks: tuple[PackageConstraintCheck, ...] = ()
    report_version: int = PACKAGE_EXPORT_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not PackageExportPlan
            or type(self.pdfs) is not tuple
            or any(type(value) is not VerifiedPackagePdf for value in self.pdfs)
            or type(self.constraint_evaluation) is not PortalConstraintEvaluation
            or type(self.constraint_findings) is not tuple
            or any(type(value) is not PreflightFindingTarget for value in self.constraint_findings)
            or type(self.constraint_checks) is not tuple
            or any(type(value) is not PackageConstraintCheck for value in self.constraint_checks)
            or type(self.report_version) is not int
            or self.report_version != PACKAGE_EXPORT_REPORT_VERSION
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.aggregate_report",
            )
        planned = self.plan.pdfs
        if len(self.pdfs) != len(planned) or any(
            report.output_key != pdf.output_key
            or report.position != pdf.position
            or report.requirement_id != pdf.requirement_id
            or report.page_ids != pdf.page_ids
            or report.filename != pdf.filename
            for report, pdf in zip(self.pdfs, planned, strict=True)
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.aggregate_pdfs",
            )
        if self.plan.mode is PortalOutputMode.ZIP:
            if type(self.archive) is not SafeZipReport or self.plan.archive is None:
                reject_package_export(
                    PackageExportErrorCode.MODEL_INVALID,
                    "package_export.aggregate_archive",
                )
            if len(self.archive.members) != len(self.pdfs) or any(
                member.position != pdf.position
                or member.filename != pdf.filename
                or member.byte_size != pdf.base_report.output_byte_size
                or member.sha256 != pdf.base_report.output_sha256
                for member, pdf in zip(self.archive.members, self.pdfs, strict=True)
            ):
                reject_package_export(
                    PackageExportErrorCode.MODEL_INVALID,
                    "package_export.aggregate_members",
                )
        elif self.archive is not None:
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.aggregate_archive",
            )
        expected_findings = evaluate_portal_constraints(self.constraint_evaluation)
        expected_checks = _constraint_checks(self.plan, expected_findings, self.archive)
        if (
            self.constraint_evaluation.constraints != self.plan.active_constraints.values
            or not _evaluation_matches_report(
                self.plan,
                self.pdfs,
                self.archive,
                self.constraint_evaluation,
            )
            or self.constraint_findings != expected_findings
            or self.constraint_checks != expected_checks
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.aggregate_constraints",
            )

    @property
    def constraints_satisfied(self) -> bool:
        return not self.constraint_findings and all(
            value.passed for value in self.constraint_checks
        )

    def __repr__(self) -> str:
        return (
            "PackageAggregateVerificationReport("
            f"mode={self.plan.mode.value!r}, pdf_count={len(self.pdfs)}, "
            f"has_archive={self.archive is not None}, "
            f"constraint_finding_count={len(self.constraint_findings)}, "
            f"report_version={self.report_version!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PackageExportOutcome:
    """A constraint block or exact post-publication package state."""

    status: PackageExportStatus
    report: PackageAggregateVerificationReport = field(repr=False)
    destination: ExportPackageDestinationResult | None = field(default=None, repr=False)
    outcome_version: int = PACKAGE_EXPORT_OUTCOME_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.status) is not PackageExportStatus
            or type(self.report) is not PackageAggregateVerificationReport
            or (
                self.destination is not None
                and type(self.destination) is not ExportPackageDestinationResult
            )
            or type(self.outcome_version) is not int
            or self.outcome_version != PACKAGE_EXPORT_OUTCOME_VERSION
        ):
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.outcome",
            )
        if self.status is PackageExportStatus.CONSTRAINTS_BLOCKED:
            valid = self.destination is None and not self.report.constraints_satisfied
        elif self.destination is None or not self.report.constraints_satisfied:
            valid = False
        elif self.status is PackageExportStatus.COMMITTED:
            valid = self.destination.committed
        elif self.status is PackageExportStatus.PARTIAL:
            valid = self.destination.partial and not self.destination.uncertain
        elif self.status is PackageExportStatus.UNCERTAIN:
            valid = self.destination.uncertain
        else:
            valid = (
                not self.destination.committed
                and not self.destination.partial
                and not self.destination.uncertain
            )
        if not valid:
            reject_package_export(
                PackageExportErrorCode.MODEL_INVALID,
                "package_export.outcome_state",
            )

    @property
    def successful(self) -> bool:
        return self.status is PackageExportStatus.COMMITTED

    def __repr__(self) -> str:
        return (
            "PackageExportOutcome("
            f"status={self.status.value!r}, pdf_count={len(self.report.pdfs)}, "
            f"outcome_version={self.outcome_version!r})"
        )


def _ignore_progress(_value: PackageExportProgress) -> None:
    return


def export_package_plan(
    snapshot: ManifestSnapshot,
    plan: PackageExportPlan,
    destination_parent: Path,
    producer: PackagePdfProducerPort,
    artifact_destination: WindowsExportDestination,
    policy: ResourcePolicy,
    *,
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
    progress: PackageExportProgressSink = _ignore_progress,
    destination_dependencies: ExportPackageDestinationDependencies | None = None,
    review: Callable[[PackageAggregateVerificationReport], None] | None = None,
) -> PackageExportOutcome:
    """Generate, verify, constraint-check, then publish one exact package plan."""

    try:
        return _export_package_plan(
            snapshot,
            plan,
            destination_parent,
            producer,
            artifact_destination,
            policy,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
            destination_dependencies=destination_dependencies,
            review=review,
        )
    except PackageExportError:
        raise
    except SafeZipError as error:
        _raise_zip_error(error)
    except ExportPackageDestinationError as error:
        _raise_destination_error(error)
    except PdfExportError as error:
        _raise_foreign_operation_error(error, "package_export.pdf")
    except PreflightError:
        reject_package_export(PackageExportErrorCode.PLAN_INVALID, "package_export.preflight")
    except BaseException as error:
        _raise_foreign_operation_error(error, "package_export.operation")


def _export_package_plan(
    snapshot: ManifestSnapshot,
    plan: PackageExportPlan,
    destination_parent: Path,
    producer: PackagePdfProducerPort,
    artifact_destination: WindowsExportDestination,
    policy: ResourcePolicy,
    *,
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
    progress: PackageExportProgressSink,
    destination_dependencies: ExportPackageDestinationDependencies | None,
    review: Callable[[PackageAggregateVerificationReport], None] | None,
) -> PackageExportOutcome:
    if (
        type(snapshot) is not ManifestSnapshot
        or type(plan) is not PackageExportPlan
        or not isinstance(destination_parent, Path)
        or not isinstance(producer, PackagePdfProducerPort)
        or type(artifact_destination) is not WindowsExportDestination
        or type(policy) is not ResourcePolicy
        or not callable(deadline)
        or not callable(cancellation)
        or not callable(progress)
        or (review is not None and not callable(review))
        or (
            destination_dependencies is not None
            and type(destination_dependencies) is not ExportPackageDestinationDependencies
        )
    ):
        reject_package_export(PackageExportErrorCode.MODEL_INVALID, "package_export.request")
    _require_plan_authority(snapshot, plan)
    parent_units = _destination_parent_units(destination_parent)
    _require_destination_names(plan, parent_units)
    _require_active(deadline, cancellation)

    output_count = len(plan.pdfs)
    total_pages = sum(len(value.page_ids) for value in plan.pdfs)
    _emit_progress(
        progress,
        PackageExportProgress(
            PackageExportProgressPhase.PREPARING,
            0,
            output_count,
            0,
            total_pages,
        ),
    )
    completed_pages = 0
    verified_pdfs: list[VerifiedPackagePdf] = []
    archive_report: SafeZipReport | None = None

    with WindowsExportPackageTransaction(
        destination_parent,
        artifact_destination,
        maximum_staged_bytes=policy.maximum_temporary_storage_bytes,
        cancellation=cancellation,
        deadline=deadline,
        dependencies=destination_dependencies,
    ) as transaction:
        for planned_pdf in plan.pdfs:
            _require_active(deadline, cancellation)
            _emit_progress(
                progress,
                PackageExportProgress(
                    PackageExportProgressPhase.GENERATING_PDFS,
                    planned_pdf.position,
                    output_count,
                    completed_pages,
                    total_pages,
                ),
            )

            def pdf_progress(
                value: PdfExportProgress,
                *,
                before: int = completed_pages,
                position: int = planned_pdf.position,
            ) -> None:
                if type(value) is not PdfExportProgress:
                    reject_package_export(
                        PackageExportErrorCode.PDF_FAILED,
                        "package_export.pdf_progress",
                    )
                _emit_progress(
                    progress,
                    PackageExportProgress(
                        PackageExportProgressPhase.GENERATING_PDFS,
                        position,
                        output_count,
                        before + value.completed_pages,
                        total_pages,
                    ),
                )

            try:
                if isinstance(producer, PackagePdfReservedCapacityPort):
                    producer.reserve_temporary_bytes(
                        policy.maximum_temporary_storage_bytes - transaction.remaining_staged_bytes
                    )
                candidate_context = producer.produce(
                    snapshot,
                    planned_pdf,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=pdf_progress,
                )
            except PackageExportError, PdfExportError, ExportPackageDestinationError:
                raise
            except BaseException as error:
                _raise_foreign_operation_error(error, "package_export.pdf")
            staged = False
            try:
                with candidate_context as candidate:
                    verified = _require_candidate(planned_pdf, candidate)
                    transaction.stage(
                        planned_pdf.output_key,
                        planned_pdf.position,
                        None if plan.mode is PortalOutputMode.ZIP else planned_pdf.filename,
                        candidate,
                        expected_byte_size=verified.base_report.output_byte_size,
                        expected_sha256=verified.base_report.output_sha256,
                    )
                    staged = True
            except PackageExportError, PdfExportError, ExportPackageDestinationError:
                raise
            except BaseException as error:
                _raise_foreign_operation_error(error, "package_export.pdf")
            if not staged:
                reject_package_export(
                    PackageExportErrorCode.PDF_FAILED,
                    "package_export.pdf_stage",
                )
            verified_pdfs.append(verified)
            completed_pages += len(planned_pdf.page_ids)

        if len(verified_pdfs) != len(plan.pdfs):
            reject_package_export(
                PackageExportErrorCode.PDF_FAILED,
                "package_export.pdf_inventory",
            )

        if plan.mode is PortalOutputMode.ZIP:
            archive = plan.archive
            if archive is None:
                reject_package_export(
                    PackageExportErrorCode.PLAN_INVALID,
                    "package_export.archive_plan",
                )
            _emit_progress(
                progress,
                PackageExportProgress(
                    PackageExportProgressPhase.PACKAGING_ZIP,
                    output_count,
                    output_count,
                    total_pages,
                    total_pages,
                ),
            )
            member_keys = tuple(value.output_key for value in verified_pdfs)
            with transaction.open_staged_readers(member_keys) as readers:
                members = tuple(
                    SafeZipMember(
                        pdf.filename,
                        reader,
                        pdf.base_report.output_byte_size,
                        pdf.base_report.output_sha256,
                    )
                    for pdf, reader in zip(verified_pdfs, readers, strict=True)
                )
                zip_policy = _available_zip_policy(
                    policy,
                    transaction.remaining_staged_bytes,
                )

                def write_archive(output: BinaryIO) -> tuple[int, str]:
                    nonlocal archive_report
                    archive_report = write_safe_zip(
                        output,
                        members,
                        policy=zip_policy,
                        cancellation=cancellation,
                        deadline=deadline,
                    )
                    return (
                        archive_report.archive_byte_size,
                        archive_report.archive_sha256,
                    )

                transaction.stage_generated(
                    archive.output_key,
                    len(verified_pdfs) + 1,
                    archive.filename,
                    write_archive,
                )
            if type(archive_report) is not SafeZipReport:
                reject_package_export(
                    PackageExportErrorCode.ZIP_FAILED,
                    "package_export.archive_report",
                )
            transaction.discard_internal()

        _emit_progress(
            progress,
            PackageExportProgress(
                PackageExportProgressPhase.CHECKING_CONSTRAINTS,
                output_count,
                output_count,
                total_pages,
                total_pages,
            ),
        )
        evaluation = _constraint_evaluation(
            plan,
            tuple(verified_pdfs),
            archive_report,
            parent_units,
        )
        findings = evaluate_portal_constraints(evaluation)
        aggregate = PackageAggregateVerificationReport(
            plan=plan,
            pdfs=tuple(verified_pdfs),
            archive=archive_report,
            constraint_evaluation=evaluation,
            constraint_findings=findings,
            constraint_checks=_constraint_checks(plan, findings, archive_report),
        )
        if review is not None:
            # Validate all additional policy evidence while every output remains
            # private. A reviewer failure must never erase committed inventory.
            review(aggregate)
        if findings:
            return PackageExportOutcome(PackageExportStatus.CONSTRAINTS_BLOCKED, aggregate)

        _require_active(deadline, cancellation)
        _emit_progress(
            progress,
            PackageExportProgress(
                PackageExportProgressPhase.COMMITTING,
                output_count,
                output_count,
                total_pages,
                total_pages,
            ),
        )
        destination_result = transaction.commit()

    _require_destination_result(plan, destination_result)
    if destination_result.committed:
        status = PackageExportStatus.COMMITTED
    elif destination_result.uncertain:
        status = PackageExportStatus.UNCERTAIN
    elif destination_result.partial:
        status = PackageExportStatus.PARTIAL
    else:
        status = PackageExportStatus.NOT_COMMITTED
    outcome = PackageExportOutcome(status, aggregate, destination_result)
    _emit_progress_after_commit(
        progress,
        PackageExportProgress(
            PackageExportProgressPhase.COMPLETE,
            output_count,
            output_count,
            total_pages,
            total_pages,
        ),
    )
    return outcome


def _require_candidate(
    planned: PlannedPackagePdf,
    candidate: PackagePdfCandidatePort,
) -> VerifiedPackagePdf:
    try:
        report = candidate.report
        reader = candidate.read
        pipeline_value = report.pipeline
        pipeline_name = getattr(pipeline_value, "value", pipeline_value)
        if type(pipeline_name) is not str:
            raise TypeError
        pipeline = PackagePdfPipeline(pipeline_name)
    except PackageExportError:
        raise
    except BaseException:
        reject_package_export(
            PackageExportErrorCode.PDF_FAILED,
            "package_export.pdf_candidate",
        )
    if (
        report.output_key != planned.output_key
        or report.position != planned.position
        or report.page_ids != planned.page_ids
        or type(report.base_report) is not PdfExportVerificationReport
        or report.base_report.page_count != len(planned.page_ids)
        or not callable(reader)
    ):
        reject_package_export(
            PackageExportErrorCode.PDF_FAILED,
            "package_export.pdf_binding",
        )
    return VerifiedPackagePdf(
        output_key=planned.output_key,
        position=planned.position,
        requirement_id=planned.requirement_id,
        page_ids=planned.page_ids,
        filename=planned.filename,
        pipeline=pipeline,
        base_report=report.base_report,
    )


def _constraint_evaluation(
    plan: PackageExportPlan,
    pdfs: tuple[VerifiedPackagePdf, ...],
    archive: SafeZipReport | None,
    parent_units: int,
) -> PortalConstraintEvaluation:
    source = plan.output_facts
    actual_plan = OutputPlanPreflightFacts(
        case_id=source.case_id,
        manifest_revision=source.manifest_revision,
        availability=source.availability,
        output_mode=source.output_mode,
        items=tuple(
            OutputItemPreflightFact(
                output_key=item.output_key,
                position=item.position,
                page_ids=item.page_ids,
                predicted_bytes=pdf.base_report.output_byte_size,
            )
            for item, pdf in zip(source.items, pdfs, strict=True)
        ),
        overlay_flattening_availability=source.overlay_flattening_availability,
        flattened_overlay_page_ids=source.flattened_overlay_page_ids,
    )
    observations = tuple(
        PortalOutputObservation(
            pdf.output_key,
            pdf.position,
            pdf.filename,
            PdfEncryptionState.UNENCRYPTED,
        )
        for pdf in pdfs
    )
    archive_observation = None
    if archive is not None:
        planned_archive = plan.archive
        if planned_archive is None:
            reject_package_export(
                PackageExportErrorCode.PLAN_INVALID,
                "package_export.archive_binding",
            )
        archive_observation = PortalArchiveObservation(
            planned_archive.filename,
            archive.archive_byte_size,
        )
    return PortalConstraintEvaluation(
        plan.active_constraints.values,
        actual_plan,
        observations,
        archive_observation,
        parent_units,
    )


def _evaluation_matches_report(
    plan: PackageExportPlan,
    pdfs: tuple[VerifiedPackagePdf, ...],
    archive: SafeZipReport | None,
    evaluation: PortalConstraintEvaluation,
) -> bool:
    facts = evaluation.plan
    if (
        facts.case_id != plan.authority.case_id
        or facts.manifest_revision != plan.authority.manifest_revision
        or facts.availability != plan.output_facts.availability
        or facts.output_mode != plan.mode.value
        or facts.overlay_flattening_availability
        != plan.output_facts.overlay_flattening_availability
        or facts.flattened_overlay_page_ids != plan.output_facts.flattened_overlay_page_ids
        or len(facts.items) != len(pdfs)
        or len(evaluation.outputs) != len(pdfs)
    ):
        return False
    if any(
        item.output_key != pdf.output_key
        or item.position != pdf.position
        or item.page_ids != pdf.page_ids
        or item.predicted_bytes != pdf.base_report.output_byte_size
        or output.output_key != pdf.output_key
        or output.position != pdf.position
        or output.filename != pdf.filename
        or output.encryption_state is not PdfEncryptionState.UNENCRYPTED
        for item, output, pdf in zip(
            facts.items,
            evaluation.outputs,
            pdfs,
            strict=True,
        )
    ):
        return False
    if archive is None:
        return evaluation.archive is None
    planned_archive = plan.archive
    return (
        planned_archive is not None
        and evaluation.archive is not None
        and evaluation.archive.filename == planned_archive.filename
        and evaluation.archive.predicted_bytes == archive.archive_byte_size
    )


def _constraint_checks(
    plan: PackageExportPlan,
    findings: tuple[PreflightFindingTarget, ...],
    archive: SafeZipReport | None,
) -> tuple[PackageConstraintCheck, ...]:
    codes = tuple(value.code for value in findings)

    def selected(*failure_codes: str) -> tuple[str, ...]:
        accepted = frozenset(failure_codes)
        return tuple(dict.fromkeys(value for value in codes if value in accepted))

    checks: list[PackageConstraintCheck] = []

    def add(code: PackageConstraintCheckCode, *failure_codes: str) -> None:
        failures = selected(*failure_codes)
        checks.append(PackageConstraintCheck(code, not failures, failures))

    add(PackageConstraintCheckCode.MODE_ALLOWED, "PORTAL_OUTPUT_MODE_NOT_ALLOWED")
    add(
        PackageConstraintCheckCode.ORGANIZATION_VALID,
        "PORTAL_OUTPUT_ORGANIZATION_INVALID",
    )
    add(
        PackageConstraintCheckCode.FILENAMES_VALID,
        "PORTAL_EXTENSION_INVALID",
        "PORTAL_FILENAME_COLLISION",
    )
    constraints = plan.active_constraints.values
    if constraints.maximum_file_count is not None:
        add(
            PackageConstraintCheckCode.FILE_COUNT_WITHIN_LIMIT,
            "PORTAL_FILE_COUNT_EXCEEDED",
        )
    if constraints.maximum_total_bytes is not None:
        add(
            PackageConstraintCheckCode.TOTAL_SIZE_WITHIN_LIMIT,
            "PORTAL_TOTAL_SIZE_EXCEEDED",
            "PORTAL_TOTAL_SIZE_UNAVAILABLE",
        )
    if constraints.maximum_file_bytes is not None:
        add(
            PackageConstraintCheckCode.FILE_SIZE_WITHIN_LIMIT,
            "PORTAL_FILE_SIZE_EXCEEDED",
            "PORTAL_FILE_SIZE_UNAVAILABLE",
        )
    if constraints.maximum_pages_per_file is not None:
        add(
            PackageConstraintCheckCode.PAGE_COUNT_WITHIN_LIMIT,
            "PORTAL_PAGE_COUNT_EXCEEDED",
        )
    if constraints.encryption_policy is PortalEncryptionPolicy.MUST_BE_UNENCRYPTED:
        add(
            PackageConstraintCheckCode.ENCRYPTION_POLICY_SATISFIED,
            "PORTAL_ENCRYPTION_DETECTED",
            "PORTAL_ENCRYPTION_UNVERIFIED",
        )
    if plan.mode is PortalOutputMode.ZIP:
        failures = selected("PORTAL_ZIP_ARCHIVE_UNAVAILABLE")
        if archive is None and not failures:
            failures = ("PORTAL_ZIP_ARCHIVE_UNAVAILABLE",)
        checks.append(
            PackageConstraintCheck(
                PackageConstraintCheckCode.ZIP_ARCHIVE_VERIFIED,
                archive is not None and not failures,
                failures,
            )
        )
    known = {value for check in checks for value in check.finding_codes}
    if set(codes) != known:
        reject_package_export(
            PackageExportErrorCode.INTERNAL_ERROR,
            "package_export.constraint_registry",
        )
    return tuple(checks)


def _available_zip_policy(policy: ResourcePolicy, available_bytes: int) -> SafeZipPolicy:
    base = SafeZipPolicy.from_resource_policy(policy)
    if type(available_bytes) is not int or available_bytes < 1:
        reject_package_export(
            PackageExportErrorCode.LIMIT_EXCEEDED,
            "package_export.zip_budget",
        )
    archive_limit = min(base.maximum_archive_bytes, available_bytes)
    total_limit = min(base.maximum_total_member_bytes, archive_limit)
    member_limit = min(base.maximum_member_bytes, total_limit)
    return SafeZipPolicy(
        maximum_members=base.maximum_members,
        maximum_member_bytes=member_limit,
        maximum_total_member_bytes=total_limit,
        maximum_archive_bytes=archive_limit,
        io_chunk_bytes=base.io_chunk_bytes,
    )


def _require_plan_authority(snapshot: ManifestSnapshot, plan: PackageExportPlan) -> None:
    authority = plan.authority
    if (
        snapshot.case_id != authority.case_id
        or snapshot.revision != authority.manifest_revision
        or snapshot.sha256 != authority.manifest_sha256
        or snapshot.cas_token != authority.manifest_cas_token
    ):
        reject_package_export(
            PackageExportErrorCode.PLAN_STALE,
            "package_export.authority",
        )


def _destination_parent_units(parent: Path) -> int:
    try:
        value = str(parent.absolute())
        return len(value.encode("utf-16-le")) // 2
    except BaseException:
        reject_package_export(
            PackageExportErrorCode.PLAN_INVALID,
            "package_export.destination_parent",
        )


def _require_destination_names(plan: PackageExportPlan, parent_units: int) -> None:
    member_parent_units = 0 if plan.mode is PortalOutputMode.ZIP else parent_units
    try:
        for pdf in plan.pdfs:
            normalize_output_filename(
                pdf.filename,
                destination_parent_utf16_units=member_parent_units,
            )
        if plan.archive is not None:
            normalize_output_filename(
                plan.archive.filename,
                destination_parent_utf16_units=parent_units,
            )
    except PreflightError:
        reject_package_export(
            PackageExportErrorCode.PLAN_INVALID,
            "package_export.destination_name",
        )


def _require_destination_result(
    plan: PackageExportPlan,
    result: ExportPackageDestinationResult,
) -> None:
    if type(result) is not ExportPackageDestinationResult:
        reject_package_export(
            PackageExportErrorCode.DESTINATION_FAILED,
            "package_export.destination_result",
        )
    if plan.mode is PortalOutputMode.ZIP:
        if plan.archive is None:
            reject_package_export(
                PackageExportErrorCode.PLAN_INVALID,
                "package_export.destination_plan",
            )
        expected: tuple[tuple[str, int], ...] = ((plan.archive.output_key, 1),)
    else:
        expected = tuple((value.output_key, value.position) for value in plan.pdfs)
    observed = tuple((value.output_key, value.position) for value in result.artifacts)
    if observed != expected:
        reject_package_export(
            PackageExportErrorCode.DESTINATION_FAILED,
            "package_export.destination_binding",
        )
    if any(
        item.state is ExportCommitState.COMMITTED
        and (not item.destination_reopened or item.byte_size is None or item.sha256 is None)
        for item in result.artifacts
    ):
        reject_package_export(
            PackageExportErrorCode.DESTINATION_FAILED,
            "package_export.destination_verification",
        )


def _require_active(
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
) -> None:
    try:
        expired = deadline()
        cancelled = cancellation()
    except BaseException:
        reject_package_export(
            PackageExportErrorCode.INTERNAL_ERROR,
            "package_export.probe",
        )
    if type(expired) is not bool or type(cancelled) is not bool:
        reject_package_export(
            PackageExportErrorCode.INTERNAL_ERROR,
            "package_export.probe",
        )
    if expired:
        reject_package_export(PackageExportErrorCode.TIMED_OUT, "package_export.deadline")
    if cancelled:
        reject_package_export(PackageExportErrorCode.CANCELLED, "package_export.cancelled")


def _emit_progress(sink: PackageExportProgressSink, value: PackageExportProgress) -> None:
    try:
        sink(value)
    except PackageExportError:
        raise
    except BaseException:
        reject_package_export(
            PackageExportErrorCode.INTERNAL_ERROR,
            "package_export.progress_sink",
        )


def _emit_progress_after_commit(
    sink: PackageExportProgressSink,
    value: PackageExportProgress,
) -> None:
    try:
        sink(value)
    except BaseException:
        return


def _raise_zip_error(error: SafeZipError) -> Never:
    if error.code is SafeZipErrorCode.CANCELLED:
        reject_package_export(PackageExportErrorCode.CANCELLED, "package_export.zip")
    if error.code is SafeZipErrorCode.TIMED_OUT:
        reject_package_export(PackageExportErrorCode.TIMED_OUT, "package_export.zip")
    if error.code is SafeZipErrorCode.LIMIT_EXCEEDED:
        reject_package_export(PackageExportErrorCode.LIMIT_EXCEEDED, "package_export.zip")
    reject_package_export(PackageExportErrorCode.ZIP_FAILED, "package_export.zip")


def _raise_destination_error(error: ExportPackageDestinationError) -> Never:
    if error.code is ExportPackageDestinationErrorCode.CANCELLED:
        reject_package_export(PackageExportErrorCode.CANCELLED, "package_export.destination")
    if error.code is ExportPackageDestinationErrorCode.TIMED_OUT:
        reject_package_export(PackageExportErrorCode.TIMED_OUT, "package_export.destination")
    if error.code is ExportPackageDestinationErrorCode.CAPACITY_EXCEEDED:
        reject_package_export(
            PackageExportErrorCode.LIMIT_EXCEEDED,
            "package_export.destination",
        )
    reject_package_export(
        PackageExportErrorCode.DESTINATION_FAILED,
        "package_export.destination",
    )


def _raise_foreign_operation_error(error: BaseException, context: str) -> Never:
    code = getattr(getattr(error, "code", None), "value", "")
    if type(code) is str and code.endswith("_CANCELLED"):
        reject_package_export(PackageExportErrorCode.CANCELLED, context)
    if type(code) is str and code.endswith("_TIMED_OUT"):
        reject_package_export(PackageExportErrorCode.TIMED_OUT, context)
    if type(code) is str and (code.endswith("_LIMIT_EXCEEDED") or "CAPACITY" in code):
        reject_package_export(PackageExportErrorCode.LIMIT_EXCEEDED, context)
    if context == "package_export.pdf":
        reject_package_export(PackageExportErrorCode.PDF_FAILED, context)
    reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, context)


__all__ = (
    "PACKAGE_EXPORT_OUTCOME_VERSION",
    "PACKAGE_EXPORT_REPORT_VERSION",
    "PackageAggregateVerificationReport",
    "PackageConstraintCheck",
    "PackageConstraintCheckCode",
    "PackageExportError",
    "PackageExportErrorCode",
    "PackageExportOutcome",
    "PackageExportProgress",
    "PackageExportProgressPhase",
    "PackageExportProgressSink",
    "PackageExportStatus",
    "PackagePdfCandidatePort",
    "PackagePdfCandidateReportPort",
    "PackagePdfPipeline",
    "PackagePdfProducerPort",
    "VerifiedPackagePdf",
    "export_package_plan",
    "reject_package_export",
)
