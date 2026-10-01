"""Measured package constraints drive the bounded schedule before publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAssetImpact,
    PdfImageColorMode,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.target_size_search import (
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)
from paperworkshell.export.package_export import (
    PackageExportError,
    PackageExportErrorCode,
    PackageExportProgress,
    PackageExportProgressPhase,
    PackageExportStatus,
    PackagePdfPipeline,
)
from paperworkshell.export.target_size_export import (
    TargetSizePackageExportOutcome,
    export_target_size_package,
)
from paperworkshell.preflight.package_planner import PackageExportPlan, PlannedPackagePdf
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.export_destination import (
    ExportDestinationDependencies,
    WindowsExportDestination,
    _rename_stage_no_replace,
)
from paperworkshell.storage.export_package_destination import ExportPackageDestinationDependencies
from tests.integration.test_package_export import (
    _Candidate,
    _constraints,
    _plan,
    _Producer,
    _snapshot,
)


class _Stages(_Producer):
    def __init__(self, plan: PackageExportPlan, sizes: tuple[int, ...]) -> None:
        super().__init__({})
        self.plan = plan
        self.sizes = sizes
        self.profiles: list[PdfImageStageProfile | None] = []
        self.reservations: list[int] = []
        self._stage_reports: tuple[PdfImageStageReport, ...] = ()
        self.unsupported_output_positions: tuple[int, ...] = ()
        self.before_produce: Callable[[], None] = lambda: None
        self.evidence_error = False
        self.transform_reports: Callable[
            [tuple[PdfImageStageReport, ...]], tuple[PdfImageStageReport, ...]
        ] = lambda reports: reports

    @property
    def stage_reports(self) -> tuple[PdfImageStageReport, ...]:
        if self.evidence_error:
            raise RuntimeError("PRIVATE POLICY EVIDENCE CANARY")
        return self.transform_reports(self._stage_reports)

    def begin_attempt(self, profile: PdfImageStageProfile | None) -> None:
        self.profiles.append(profile)
        self._stage_reports = ()
        size = self.sizes[len(self.profiles) - 1]
        self._payloads = {value.output_key: b"x" * size for value in self.plan.pdfs}

    def reserve_temporary_bytes(self, byte_count: int) -> None:
        self.reservations.append(byte_count)

    def produce(
        self,
        snapshot: ManifestSnapshot,
        planned_pdf: PlannedPackagePdf,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> _Candidate:
        self.before_produce()
        candidate = super().produce(
            snapshot, planned_pdf, deadline=deadline, cancellation=cancellation, progress=progress
        )
        if planned_pdf.position not in self.unsupported_output_positions:
            candidate._report = replace(candidate._report, pipeline=PackagePdfPipeline.IMAGE_STAGE)
            self._stage_reports = (
                *self._stage_reports,
                _synthetic_stage_report(candidate, self.sizes[0], self.profiles[-1]),
            )
        return candidate


def _synthetic_stage_report(
    candidate: _Candidate, baseline_size: int, profile: PdfImageStageProfile | None
) -> PdfImageStageReport:
    """Closed synthetic port evidence; parser verification has its own live tests."""

    base = candidate.report.base_report
    original_stream_size = baseline_size - 64
    selected_stream_size = base.output_byte_size - 64
    changed = profile is not None and base.output_byte_size < baseline_size
    assets: tuple[PdfImageAssetImpact, ...] = ()
    estimate = original_stream_size
    if profile is not None:
        width = profile.target_dpi
        height = profile.target_dpi * 480 // 600
        estimate = (original_stream_size * width * height + 600 * 480 - 1) // (600 * 480)
        assets = (
            PdfImageAssetImpact(
                0,
                (0,),
                600,
                480,
                width if changed else 600,
                height if changed else 480,
                600_000,
                600_000,
                profile.target_dpi * 1_000 if changed else 600_000,
                profile.target_dpi * 1_000 if changed else 600_000,
                PdfImageColorMode.RGB,
                original_stream_size,
                estimate,
                selected_stream_size,
                selected_stream_size,
                PdfImageStageOutcome.REENCODED if changed else PdfImageStageOutcome.NO_REDUCTION,
            ),
        )
    return PdfImageStageReport(
        profile,
        PdfImageStageOutcome.UNCHANGED
        if profile is None
        else PdfImageStageOutcome.REENCODED
        if changed
        else PdfImageStageOutcome.NO_REDUCTION,
        base.semantic_fingerprint,
        hashlib.sha256(b"x" * baseline_size).hexdigest(),
        baseline_size,
        None if profile is None else base.output_byte_size,
        base.output_sha256,
        base.output_byte_size,
        base.page_count,
        assets,
        tuple(
            PdfImagePageImpact(
                index,
                (0,) if index == 0 and profile is not None else (),
                original_stream_size if index == 0 else 0,
                estimate if index == 0 else 0,
                selected_stream_size if index == 0 else 0,
            )
            for index in range(base.page_count)
        ),
        base.page_count,
    )


def _limited_plan(
    mode: PortalOutputMode,
    *,
    file_bytes: int = 1_000_000,
    total_bytes: int = 1_000_000,
    page_count: int = 10,
) -> PackageExportPlan:
    plan = _plan(mode)
    return PackageExportPlan(
        authority=plan.authority,
        mode=plan.mode,
        active_constraints=_constraints(
            mode,
            maximum_file_bytes=file_bytes,
            maximum_total_bytes=total_bytes,
            maximum_pages_per_file=page_count,
        ),
        pdfs=plan.pdfs,
        archive=plan.archive,
        output_facts=plan.output_facts,
    )


def _run(
    path: Path,
    plan: PackageExportPlan,
    producer: _Stages,
    *,
    attempts: int = 4,
    deadline: Callable[[], bool] = lambda: False,
    cancellation: Callable[[], bool] = lambda: False,
    monotonic: Callable[[], float] = lambda: 0.0,
    destination: WindowsExportDestination | None = None,
    destination_dependencies: ExportPackageDestinationDependencies | None = None,
    progress: Callable[[PackageExportProgress], None] = lambda _: None,
) -> TargetSizePackageExportOutcome:
    return export_target_size_package(
        _snapshot(),
        plan,
        path,
        producer,
        destination or WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        search_policy=TargetSizeSearchPolicy.for_resource_policy(
            CONSTRAINED_RESOURCE_POLICY_V1, max_attempts=attempts
        ),
        deadline=deadline,
        cancellation=cancellation,
        monotonic=monotonic,
        destination_dependencies=destination_dependencies,
        progress=progress,
    )


@pytest.mark.parametrize("limit, expected", [(1_000, 1), (999, 2), (600, 2), (599, 3), (400, 4)])
def test_target_boundary_selects_first_measured_policy(
    tmp_path: Path, limit: int, expected: int
) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=limit)
    producer = _Stages(plan, (1_000, 600, 500, 400))
    result = _run(tmp_path, plan, producer)
    assert result.successful
    assert len(result.decision.attempts) == expected
    assert producer.profiles == list(result.decision.policy.profiles[:expected])
    assert len((tmp_path / "packet.pdf").read_bytes()) == producer.sizes[expected - 1]
    assert {item.name for item in tmp_path.iterdir()} == {"packet.pdf"}


def test_nonmonotonic_measured_stages_are_not_skipped(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=500)
    producer = _Stages(plan, (1_000, 700, 800, 500))
    result = _run(tmp_path, plan, producer)
    assert result.successful and len(producer.profiles) == 4
    assert [value.pdf_byte_sizes for value in result.decision.attempts] == [
        (1_000,),
        (700,),
        (800,),
        (500,),
    ]


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
def test_impossible_target_discards_every_attempt_and_preserves_existing_file(
    tmp_path: Path, mode: PortalOutputMode
) -> None:
    (tmp_path / "existing.pdf").write_bytes(b"existing verified bytes")
    plan = _limited_plan(mode, file_bytes=1)
    result = _run(tmp_path, plan, _Stages(plan, (1_000, 700, 600, 500)))
    assert not result.successful
    assert result.decision.terminal is TargetSizeSearchTerminal.UNATTAINABLE
    assert len(result.decision.attempts) == 4
    assert result.package.destination is None
    assert [item.name for item in tmp_path.iterdir()] == ["existing.pdf"]
    assert (tmp_path / "existing.pdf").read_bytes() == b"existing verified bytes"
    assert result.decision.suggestions(allow_splitting=True)


def test_multi_file_total_is_actual_sum_and_retained_stages_are_reserved(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.SEPARATE_PDFS, file_bytes=700, total_bytes=1_000)
    producer = _Stages(plan, (700, 500, 400, 300))
    result = _run(tmp_path, plan, producer)
    assert result.successful and len(result.decision.attempts) == 2
    assert producer.reservations == [0, 700, 0, 500]
    assert sum(value.stat().st_size for value in tmp_path.iterdir()) == 1_000


def test_zip_outer_overhead_is_measured_in_both_limits(tmp_path: Path) -> None:
    # Two 400-byte members fit a 1,000-byte sum, but the 208-byte ZIP overhead does not.
    plan = _limited_plan(PortalOutputMode.ZIP, file_bytes=1_000, total_bytes=1_000)
    producer = _Stages(plan, (400, 300, 200, 100))
    result = _run(tmp_path, plan, producer)
    assert result.successful and len(result.decision.attempts) == 2
    assert result.decision.attempts[0].archive_byte_size == 1_008
    assert result.decision.attempts[1].archive_byte_size == 808
    assert (tmp_path / "packet.zip").stat().st_size == 808
    assert {item.name for item in tmp_path.iterdir()} == {"packet.zip"}


def test_non_size_constraint_does_not_trigger_lossy_work(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, page_count=1)
    producer = _Stages(plan, (100,))
    result = _run(tmp_path, plan, producer)
    assert result.decision.terminal is TargetSizeSearchTerminal.OTHER_CONSTRAINTS
    assert producer.profiles == [None] and not tuple(tmp_path.iterdir())


def test_unsupported_pipeline_is_distinct_from_exhausted_policy(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=1)
    producer = _Stages(plan, (100,))
    producer.unsupported_output_positions = (1,)
    result = _run(tmp_path, plan, producer)
    assert result.decision.terminal is TargetSizeSearchTerminal.UNSUPPORTED_POLICY
    assert producer.profiles == [None] and not tuple(tmp_path.iterdir())


def test_configured_work_bound_does_not_claim_impossible_target(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=1)
    producer = _Stages(plan, (1_000, 600))
    result = _run(tmp_path, plan, producer, attempts=2)
    assert result.decision.terminal is TargetSizeSearchTerminal.WORK_LIMIT
    assert len(producer.profiles) == 2 and not tuple(tmp_path.iterdir())


def _mark_second_attempt_budget_skipped(
    reports: tuple[PdfImageStageReport, ...], *, entire_stage: bool
) -> tuple[PdfImageStageReport, ...]:
    if not reports or reports[0].profile is not PdfImageStageProfile.PRESERVE_300_DPI_Q90:
        return reports
    return tuple(
        replace(
            report,
            outcome=PdfImageStageOutcome.SKIPPED_BUDGET
            if entire_stage
            else PdfImageStageOutcome.NO_REDUCTION,
            candidate_byte_size=None if entire_stage else report.baseline_byte_size,
            assets=tuple(
                replace(
                    asset,
                    candidate_encoded_byte_size=None,
                    outcome=PdfImageStageOutcome.SKIPPED_BUDGET,
                )
                for asset in report.assets
            ),
        )
        for report in reports
    )


@pytest.mark.parametrize("entire_stage", [True, False])
@pytest.mark.parametrize("unsupported", [True, False])
def test_budget_skip_in_earlier_attempt_is_a_work_limit_after_ladder_exhaustion(
    tmp_path: Path, entire_stage: bool, unsupported: bool
) -> None:
    plan = _limited_plan(PortalOutputMode.SEPARATE_PDFS, file_bytes=1)
    producer = _Stages(plan, (1_000, 1_000, 600, 500))
    if unsupported:
        producer.unsupported_output_positions = (2,)
    producer.transform_reports = lambda reports: _mark_second_attempt_budget_skipped(
        reports, entire_stage=entire_stage
    )
    result = _run(tmp_path, plan, producer)
    assert result.decision.terminal is TargetSizeSearchTerminal.WORK_LIMIT
    assert [attempt.budget_skipped for attempt in result.decision.attempts] == [
        False,
        True,
        False,
        False,
    ]
    assert "resource limits" in result.decision.message
    assert tuple(tmp_path.iterdir()) == ()


def test_later_verified_success_is_accepted_after_a_budget_skipped_stage(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=600)
    producer = _Stages(plan, (1_000, 1_000, 600))
    producer.transform_reports = lambda reports: _mark_second_attempt_budget_skipped(
        reports, entire_stage=True
    )
    result = _run(tmp_path, plan, producer)
    assert result.successful and result.decision.terminal is TargetSizeSearchTerminal.SATISFIED
    assert result.decision.attempts[1].budget_skipped
    assert result.decision.selected_profile is PdfImageStageProfile.PRESERVE_240_DPI_Q85
    assert (tmp_path / "packet.pdf").stat().st_size == 600


def test_target_outcome_rejects_archive_size_different_from_verified_package(
    tmp_path: Path,
) -> None:
    plan = _limited_plan(PortalOutputMode.ZIP)
    result = _run(tmp_path, plan, _Stages(plan, (400,)))
    final = result.decision.attempts[-1]
    assert final.archive_byte_size is not None
    changed = replace(
        result.decision,
        attempts=(replace(final, archive_byte_size=final.archive_byte_size + 1),),
    )
    with pytest.raises(PackageExportError) as captured:
        replace(result, decision=changed)
    assert captured.value.code is PackageExportErrorCode.MODEL_INVALID


@pytest.mark.parametrize("kind", ("cancel", "timeout", "clock"))
def test_shared_operation_deadline_and_cancel_stop_between_attempts(
    tmp_path: Path, kind: str
) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=1)
    producer = _Stages(plan, (1_000, 600, 500, 400))
    active = False

    def stop() -> None:
        nonlocal active
        active = True

    producer.before_produce = stop
    with pytest.raises(PackageExportError) as captured:
        _run(
            tmp_path,
            plan,
            producer,
            deadline=lambda: active and kind == "timeout",
            cancellation=lambda: active and kind == "cancel",
            monotonic=lambda: 1000.0 if active and kind == "clock" else 0.0,
        )
    expected = (
        PackageExportErrorCode.CANCELLED if kind == "cancel" else PackageExportErrorCode.TIMED_OUT
    )
    assert captured.value.code is expected
    assert producer.profiles == [None] and not tuple(tmp_path.iterdir())


def test_low_disk_fails_and_fresh_retry_uses_same_policy(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=600)
    destination = WindowsExportDestination(
        ExportDestinationDependencies(available_space=lambda _: 1),
        minimum_free_space_reserve_bytes=0,
    )
    with pytest.raises(PackageExportError):
        _run(tmp_path, plan, _Stages(plan, (1_000, 600)), destination=destination)
    assert not tuple(tmp_path.iterdir())
    result = _run(tmp_path, plan, _Stages(plan, (1_000, 600)))
    assert (
        result.successful
        and result.decision.selected_profile is PdfImageStageProfile.PRESERVE_300_DPI_Q90
    )


def test_destination_collision_never_causes_stronger_compression(tmp_path: Path) -> None:
    (tmp_path / "packet.pdf").write_bytes(b"previous export")
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF)
    producer = _Stages(plan, (1_000,))
    result = _run(tmp_path, plan, producer)
    assert result.decision.terminal is TargetSizeSearchTerminal.SATISFIED
    assert not result.successful and producer.profiles == [None]
    assert (tmp_path / "packet.pdf").read_bytes() == b"previous export"


@pytest.mark.parametrize("invalid", ["profile", "digest", "missing", "unsupported"])
def test_invalid_policy_evidence_fails_before_any_publication(tmp_path: Path, invalid: str) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF)
    producer = _Stages(plan, (1_000,))

    def transform(reports: tuple[PdfImageStageReport, ...]) -> tuple[PdfImageStageReport, ...]:
        if invalid == "missing":
            return ()
        if invalid == "profile":
            return (replace(reports[0], profile=PdfImageStageProfile.PRESERVE_200_DPI_Q80),)
        if invalid == "digest":
            return (replace(reports[0], baseline_sha256="f" * 64, output_sha256="f" * 64),)
        producer.unsupported_output_positions = (1,)
        return reports

    producer.transform_reports = transform
    with pytest.raises(PackageExportError) as captured:
        _run(tmp_path, plan, producer)
    assert captured.value.code is PackageExportErrorCode.PDF_FAILED
    assert producer.profiles == [None]
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize("changed", ["digest", "size", "pipeline"])
def test_every_stage_binds_the_same_baseline_and_supported_output_inventory(
    tmp_path: Path, changed: str
) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=600)
    producer = _Stages(plan, (1_000, 600))

    def transform(reports: tuple[PdfImageStageReport, ...]) -> tuple[PdfImageStageReport, ...]:
        if len(producer.profiles) == 1 or changed == "pipeline":
            return reports
        if changed == "digest":
            return (replace(reports[0], baseline_sha256="f" * 64),)
        return (replace(reports[0], baseline_byte_size=1_001),)

    def alter_pipeline() -> None:
        if changed == "pipeline" and len(producer.profiles) > 1:
            producer.unsupported_output_positions = (1,)

    producer.transform_reports = transform
    producer.before_produce = alter_pipeline
    with pytest.raises(PackageExportError) as captured:
        _run(tmp_path, plan, producer)
    assert captured.value.code is PackageExportErrorCode.PDF_FAILED
    assert len(producer.profiles) == 2
    assert tuple(tmp_path.iterdir()) == ()


def test_policy_evidence_property_failure_discards_staged_outputs_before_commit(
    tmp_path: Path,
) -> None:
    plan = _limited_plan(PortalOutputMode.SEPARATE_PDFS)
    producer = _Stages(plan, (1_000,))
    producer.evidence_error = True
    with pytest.raises(PackageExportError) as captured:
        _run(tmp_path, plan, producer)
    assert captured.value.code is PackageExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE" not in str(captured.value)
    assert tuple(tmp_path.iterdir()) == ()


def test_partial_publication_preserves_inventory_without_reading_evidence_again(
    tmp_path: Path,
) -> None:
    plan = _limited_plan(PortalOutputMode.SEPARATE_PDFS)
    producer = _Stages(plan, (1_000,))
    renamed = 0

    def rename_stage(stage: WindowsHandle, parent: WindowsHandle, name: str) -> tuple[bool, int]:
        nonlocal renamed
        renamed += 1
        producer.evidence_error = True
        if renamed == 2:
            return False, 5
        return _rename_stage_no_replace(stage, parent, name)

    def progress(value: PackageExportProgress) -> None:
        if value.phase is PackageExportProgressPhase.COMPLETE:
            raise RuntimeError("PRIVATE POSTCOMMIT CANARY")

    result = _run(
        tmp_path,
        plan,
        producer,
        destination_dependencies=ExportPackageDestinationDependencies(rename_stage=rename_stage),
        progress=progress,
    )
    assert result.package.status is PackageExportStatus.PARTIAL
    assert result.package.destination is not None and result.package.destination.partial
    assert result.decision.terminal is TargetSizeSearchTerminal.SATISFIED
    assert not result.successful and producer.profiles == [None]
    assert (tmp_path / "zeta.pdf").stat().st_size == 1_000
    assert not (tmp_path / "alpha.pdf").exists()
