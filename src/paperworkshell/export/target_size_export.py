"""Opt-in bounded target search using the ordinary verified package transaction.

Each failed attempt destroys its destination-local stages before the next policy.
The first satisfying attempt is published by the same no-replace transaction that
measured it. No lossy search is enabled by the ordinary desktop export path.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Self, runtime_checkable

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_image_stages import (
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.target_size_search import (
    TargetSizeAttempt,
    TargetSizeDecisionReport,
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)
from paperworkshell.export.package_export import (
    PackageAggregateVerificationReport,
    PackageExportError,
    PackageExportErrorCode,
    PackageExportOutcome,
    PackageExportProgressSink,
    PackagePdfPipeline,
    PackagePdfProducerPort,
    PackagePdfReservedCapacityPort,
    PreparedPackageExport,
    prepare_package_plan,
    reject_package_export,
)
from paperworkshell.preflight.package_planner import PackageExportPlan
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.storage.export_package_destination import ExportPackageDestinationDependencies

_SIZE_CODES = frozenset({"PORTAL_TOTAL_SIZE_EXCEEDED", "PORTAL_FILE_SIZE_EXCEEDED"})


@runtime_checkable
class TargetSizePackageProducerPort(
    PackagePdfProducerPort, PackagePdfReservedCapacityPort, Protocol
):
    """One operation-scoped producer retaining bounded authenticated baseline facts."""

    def begin_attempt(self, profile: PdfImageStageProfile | None) -> None: ...

    @property
    def stage_reports(self) -> tuple[PdfImageStageReport, ...]: ...

    @property
    def unsupported_output_positions(self) -> tuple[int, ...]: ...


@dataclass(frozen=True, slots=True, repr=False)
class TargetSizePackageExportOutcome:
    """Search evidence and actual publication facts have separate meanings."""

    package: PackageExportOutcome = field(repr=False)
    decision: TargetSizeDecisionReport = field(repr=False)
    image_stage_reports: tuple[PdfImageStageReport, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.package) is not PackageExportOutcome
            or type(self.decision) is not TargetSizeDecisionReport
            or not self.decision.attempts
            or type(self.image_stage_reports) is not tuple
            or len(self.image_stage_reports) > 128
            or any(type(value) is not PdfImageStageReport for value in self.image_stage_reports)
            or self.decision.semantic_fingerprint != self.package.report.plan.semantic_fingerprint
            or self.decision.attempts[-1].pdf_byte_sizes
            != tuple(value.base_report.output_byte_size for value in self.package.report.pdfs)
            or self.decision.attempts[-1].constraints_satisfied
            != self.package.report.constraints_satisfied
            or self.decision.attempts[-1].archive_byte_size
            != (
                None
                if self.package.report.archive is None
                else self.package.report.archive.archive_byte_size
            )
        ):
            reject_package_export(PackageExportErrorCode.MODEL_INVALID, "target_size.outcome")

    @property
    def successful(self) -> bool:
        return self.package.successful

    def __repr__(self) -> str:
        return (
            f"TargetSizePackageExportOutcome(status={self.package.status.value!r}, "
            f"attempts={len(self.decision.attempts)})"
        )


def _ignore_progress(_value: object) -> None:
    pass


class PreparedTargetSizePackage:
    """Exact selected stages and measured search facts retained for human review."""

    def __init__(
        self,
        package: PreparedPackageExport,
        decision: TargetSizeDecisionReport,
        image_stage_reports: tuple[PdfImageStageReport, ...],
        preparation_deadline: Callable[[], bool],
    ) -> None:
        self.package = package
        self.decision = decision
        self.image_stage_reports = image_stage_reports
        self._preparation_deadline = preparation_deadline

    def __enter__(self) -> Self:
        self.package.__enter__()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.package.close()

    def commit(
        self,
        snapshot: ManifestSnapshot,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: PackageExportProgressSink = _ignore_progress,
    ) -> TargetSizePackageExportOutcome:
        result = self.package.commit(
            snapshot, deadline=deadline, cancellation=cancellation, progress=progress
        )
        return TargetSizePackageExportOutcome(result, self.decision, self.image_stage_reports)

    def __repr__(self) -> str:
        return f"PreparedTargetSizePackage(attempts={len(self.decision.attempts)})"


def export_target_size_package(
    snapshot: ManifestSnapshot,
    plan: PackageExportPlan,
    destination_parent: Path,
    producer: TargetSizePackageProducerPort,
    artifact_destination: WindowsExportDestination,
    resource_policy: ResourcePolicy,
    *,
    search_policy: TargetSizeSearchPolicy | None = None,
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
    progress: PackageExportProgressSink = _ignore_progress,
    destination_dependencies: ExportPackageDestinationDependencies | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> TargetSizePackageExportOutcome:
    """Measure the fixed policy prefix; publish only its first verified success.

    Minimal change means the first satisfying policy in this versioned global
    schedule. It does not claim an optimum over arbitrary per-asset combinations.
    The caller supplies the explicit target-capable producer; PWS-0505 owns review UI.
    """

    with prepare_target_size_package(
        snapshot,
        plan,
        destination_parent,
        producer,
        artifact_destination,
        resource_policy,
        search_policy=search_policy,
        deadline=deadline,
        cancellation=cancellation,
        progress=progress,
        destination_dependencies=destination_dependencies,
        monotonic=monotonic,
    ) as prepared:
        return prepared.commit(
            snapshot,
            deadline=prepared._preparation_deadline,
            cancellation=cancellation,
            progress=progress,
        )


def prepare_target_size_package(
    snapshot: ManifestSnapshot,
    plan: PackageExportPlan,
    destination_parent: Path,
    producer: TargetSizePackageProducerPort,
    artifact_destination: WindowsExportDestination,
    resource_policy: ResourcePolicy,
    *,
    search_policy: TargetSizeSearchPolicy | None = None,
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
    progress: PackageExportProgressSink = _ignore_progress,
    destination_dependencies: ExportPackageDestinationDependencies | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> PreparedTargetSizePackage:
    """Measure the fixed policy prefix and retain its terminal private candidate.

    Minimal change means the first satisfying policy in this versioned global
    schedule. It does not claim an optimum over arbitrary per-asset combinations.
    The caller supplies the explicit target-capable producer; PWS-0505 owns review UI.
    """

    if (
        type(resource_policy) is not ResourcePolicy
        or type(snapshot) is not ManifestSnapshot
        or type(plan) is not PackageExportPlan
        or not isinstance(producer, TargetSizePackageProducerPort)
        or not all(callable(value) for value in (deadline, cancellation, progress, monotonic))
    ):
        reject_package_export(PackageExportErrorCode.MODEL_INVALID, "target_size.request")
    selected_policy = search_policy or TargetSizeSearchPolicy.for_resource_policy(resource_policy)
    if (
        type(selected_policy) is not TargetSizeSearchPolicy
        or selected_policy.resource_policy != resource_policy
    ):
        reject_package_export(PackageExportErrorCode.MODEL_INVALID, "target_size.policy")

    def now() -> float:
        try:
            value = monotonic()
            if type(value) not in (float, int) or not math.isfinite(value):
                raise ValueError
            return float(value)
        except Exception:
            reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, "target_size.clock")

    end = now() + selected_policy.max_duration_ms / 1000

    def expired() -> bool:
        return deadline() or now() >= end

    attempts: tuple[TargetSizeAttempt, ...] = ()
    baseline_bindings: tuple[_StageBaselineBinding, ...] | None = None
    for ordinal, profile in enumerate(selected_policy.profiles, 1):
        prepared: _ReviewedAttempt | None = None

        def review(
            aggregate: PackageAggregateVerificationReport,
            *,
            previous: tuple[TargetSizeAttempt, ...] = attempts,
            position: int = ordinal,
            selected: PdfImageStageProfile | None = profile,
            expected_baselines: tuple[_StageBaselineBinding, ...] | None = baseline_bindings,
        ) -> None:
            nonlocal prepared
            prepared = _review_attempt(
                aggregate,
                producer,
                selected_policy,
                previous,
                position,
                selected,
                expected_baselines,
            )

        try:
            if cancellation():
                reject_package_export(PackageExportErrorCode.CANCELLED, "target_size.cancelled")
            if expired():
                reject_package_export(PackageExportErrorCode.TIMED_OUT, "target_size.deadline")
            producer.begin_attempt(profile)
            staged = prepare_package_plan(
                snapshot,
                plan,
                destination_parent,
                producer,
                artifact_destination,
                resource_policy,
                deadline=expired,
                cancellation=cancellation,
                progress=progress,
                destination_dependencies=destination_dependencies,
                review=review,
            )
        except PackageExportError:
            raise
        except Exception:
            reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, "target_size.operation")
        if prepared is None:
            staged.close()
            reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, "target_size.review")
        attempts = prepared.attempts
        baseline_bindings = prepared.baseline_bindings
        if prepared.decision is not None:
            return PreparedTargetSizePackage(staged, prepared.decision, prepared.reports, expired)
        staged.close()
    reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, "target_size.schedule")


@dataclass(frozen=True, slots=True)
class _StageBaselineBinding:
    """Small immutable identities retained across attempts; no asset inventories."""

    position: int
    semantic_fingerprint: str = field(repr=False)
    baseline_sha256: str = field(repr=False)
    baseline_byte_size: int
    page_count: int


@dataclass(frozen=True, slots=True)
class _ReviewedAttempt:
    attempts: tuple[TargetSizeAttempt, ...]
    reports: tuple[PdfImageStageReport, ...]
    decision: TargetSizeDecisionReport | None
    baseline_bindings: tuple[_StageBaselineBinding, ...]


def _review_attempt(
    aggregate: PackageAggregateVerificationReport,
    producer: TargetSizePackageProducerPort,
    policy: TargetSizeSearchPolicy,
    previous: tuple[TargetSizeAttempt, ...],
    ordinal: int,
    profile: PdfImageStageProfile | None,
    baseline_bindings: tuple[_StageBaselineBinding, ...] | None,
) -> _ReviewedAttempt:
    """Bind all policy evidence before the package can commit public files."""

    reports = producer.stage_reports
    unsupported = producer.unsupported_output_positions
    stage_pdfs = tuple(
        pdf for pdf in aggregate.pdfs if pdf.pipeline is PackagePdfPipeline.IMAGE_STAGE
    )
    if (
        type(reports) is not tuple
        or len(reports) != len(stage_pdfs)
        or any(type(report) is not PdfImageStageReport for report in reports)
        or type(unsupported) is not tuple
        or unsupported
        != tuple(
            pdf.position
            for pdf in aggregate.pdfs
            if pdf.pipeline is not PackagePdfPipeline.IMAGE_STAGE
        )
        or any(
            report.profile is not profile
            or report.output_sha256 != pdf.base_report.output_sha256
            or report.output_byte_size != pdf.base_report.output_byte_size
            or report.semantic_fingerprint != pdf.base_report.semantic_fingerprint
            or report.page_count != pdf.base_report.page_count
            for report, pdf in zip(reports, stage_pdfs, strict=True)
        )
    ):
        reject_package_export(PackageExportErrorCode.PDF_FAILED, "target_size.evidence")
    current_bindings = tuple(
        _StageBaselineBinding(
            pdf.position,
            report.semantic_fingerprint,
            report.baseline_sha256,
            report.baseline_byte_size,
            report.page_count,
        )
        for report, pdf in zip(reports, stage_pdfs, strict=True)
    )
    if baseline_bindings is not None and current_bindings != baseline_bindings:
        reject_package_export(PackageExportErrorCode.PDF_FAILED, "target_size.baseline_binding")
    codes = tuple(sorted({finding.code for finding in aggregate.constraint_findings}))
    attempt = TargetSizeAttempt(
        ordinal,
        profile,
        tuple(pdf.base_report.output_byte_size for pdf in aggregate.pdfs),
        None if aggregate.archive is None else aggregate.archive.archive_byte_size,
        aggregate.constraints_satisfied,
        codes,
        unsupported,
        any(
            report.outcome is PdfImageStageOutcome.SKIPPED_BUDGET
            or any(asset.outcome is PdfImageStageOutcome.SKIPPED_BUDGET for asset in report.assets)
            for report in reports
        ),
    )
    attempts = (*previous, attempt)
    terminal: TargetSizeSearchTerminal | None = None
    if aggregate.constraints_satisfied:
        terminal = TargetSizeSearchTerminal.SATISFIED
    elif any(code not in _SIZE_CODES for code in codes):
        terminal = TargetSizeSearchTerminal.OTHER_CONSTRAINTS
    elif len(unsupported) == len(aggregate.pdfs):
        terminal = TargetSizeSearchTerminal.UNSUPPORTED_POLICY
    elif ordinal == policy.max_attempts:
        terminal = (
            TargetSizeSearchTerminal.WORK_LIMIT
            if ordinal < 4 or any(item.budget_skipped for item in attempts)
            else TargetSizeSearchTerminal.UNSUPPORTED_POLICY
            if unsupported
            else TargetSizeSearchTerminal.UNATTAINABLE
        )
    decision = None
    if terminal is not None:
        decision = TargetSizeDecisionReport(
            aggregate.plan.semantic_fingerprint,
            policy,
            attempts,
            terminal,
            profile if terminal is TargetSizeSearchTerminal.SATISFIED else None,
        )
    return _ReviewedAttempt(attempts, reports, decision, current_bindings)
