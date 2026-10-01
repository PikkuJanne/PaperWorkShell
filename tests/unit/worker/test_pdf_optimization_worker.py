"""Private optimization selection, quota accounting, and publication authority."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.pdf_rendering import PdfPageRenderLimits
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.pdf.optimization_adapter import PdfOptimizationAdapterResult
from paperworkshell.worker.pdf_optimization import optimize_verified_pdf
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import ResourceLimits

BASELINE = b"%PDF-synthetic-selection-test\n" * 64
CANDIDATE = b"%PDF-smaller-synthetic-selection-test\n"


def _report(payload: bytes = BASELINE) -> PdfExportVerificationReport:
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
        "a" * 64,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        1,
        tuple(PdfExportCheck(code, 1 if code in counted else None) for code in codes),
        False,
    )


def _resources() -> ResourceLimits:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=policy.worker_cpu_time_ms,
        memory_bytes=policy.worker_process_memory_bytes,
        temporary_storage_bytes=policy.maximum_temporary_storage_bytes,
        output_bytes=1_048_576,
        page_count=policy.maximum_page_count,
        pixel_count=policy.maximum_preview_pixels_per_page,
    )


def _limits() -> PdfExportAdapterLimits:
    return PdfExportAdapterLimits(
        PdfInspectionLimits(10, 4096, 1_048_576, 1_048_576, 1_048_576, 4096),
        PdfPageRenderLimits(
            10,
            1024,
            1024,
            1024 * 1024,
            4 * 1024 * 1024,
            4 * 1024 * 1024,
            16 * 1024 * 1024,
            10_000,
        ),
        1_048_576,
    )


def _control() -> PdfExportAdapterControl:
    return PdfExportAdapterControl(100, monotonic=lambda: 1.0)


@dataclass
class _Optimizer:
    outcome: PdfOptimizationOutcome = PdfOptimizationOutcome.OPTIMIZED
    corrupt_digest: bool = False
    mutate_baseline: bool = False
    calls: int = 0
    maximum_candidate_bytes: int = 0

    def optimize(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
        maximum_candidate_bytes: int,
        maximum_semantic_bytes: int,
    ) -> PdfOptimizationAdapterResult:
        self.calls += 1
        self.maximum_candidate_bytes = maximum_candidate_bytes
        assert maximum_semantic_bytes == _resources().memory_bytes // 4
        if self.mutate_baseline:
            baseline_path.write_bytes(b"changed")
        selected = self.outcome is PdfOptimizationOutcome.OPTIMIZED
        if selected:
            candidate_path.write_bytes(CANDIDATE)
        report = PdfOptimizationReport(
            PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            self.outcome,
            baseline_report.output_byte_size,
            len(CANDIDATE) if selected else None,
            len(CANDIDATE) if selected else baseline_report.output_byte_size,
            1 if selected else 0,
        )
        return PdfOptimizationAdapterResult(
            report,
            ("b" * 64 if self.corrupt_digest else hashlib.sha256(CANDIDATE).hexdigest())
            if selected
            else None,
        )


def test_constrained_selection_binds_smaller_path_report_and_original(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    optimizer = _Optimizer()
    result = optimize_verified_pdf(
        baseline,
        _report(),
        operation_temp=tmp_path,
        resource_limits=_resources(),
        adapter_limits=_limits(),
        control=_control(),
        private_temp_storage=OsWorkerPrivateTempStorage(available_bytes_probe=lambda _: 2**40),
        optimizer=optimizer,
    )
    assert result.path != baseline
    assert result.path.read_bytes() == CANDIDATE
    assert result.report.output_byte_size == len(CANDIDATE)
    assert result.report.report_version == 2
    assert result.report.optimization is not None
    assert result.report.optimization.verified_page_count == 1
    assert baseline.read_bytes() == BASELINE
    assert optimizer.maximum_candidate_bytes == len(BASELINE) - 1


@pytest.mark.parametrize("extra_bytes", [0, 700])
def test_budget_accounts_for_disk_extents_output_and_external_sections(
    tmp_path: Path, extra_bytes: int
) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    scratch = tmp_path / "page"
    scratch.mkdir()
    (scratch / "scratch.bin").write_bytes(b"x" * 1000)
    optimizer = _Optimizer()
    resources = replace(
        _resources(),
        temporary_storage_bytes=(2 * len(BASELINE)) + 1000 + 4096 + extra_bytes - 2,
        output_bytes=4096,
    )
    result = optimize_verified_pdf(
        baseline,
        _report(),
        operation_temp=tmp_path,
        resource_limits=resources,
        adapter_limits=_limits(),
        control=_control(),
        private_temp_storage=OsWorkerPrivateTempStorage(available_bytes_probe=lambda _: 0),
        reserved_temporary_bytes=extra_bytes,
        optimizer=optimizer,
    )
    assert optimizer.calls == 0
    assert result.path == baseline
    assert result.report.optimization is not None
    assert result.report.optimization.outcome is PdfOptimizationOutcome.SKIPPED_BUDGET
    assert baseline.read_bytes() == BASELINE


def test_ineffective_optimization_keeps_exact_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    result = optimize_verified_pdf(
        baseline,
        _report(),
        operation_temp=tmp_path,
        resource_limits=_resources(),
        adapter_limits=_limits(),
        control=_control(),
        private_temp_storage=OsWorkerPrivateTempStorage(available_bytes_probe=lambda _: 2**40),
        optimizer=_Optimizer(PdfOptimizationOutcome.NO_REDUCTION),
    )
    assert result.path == baseline
    assert result.report.output_sha256 == _report().output_sha256
    assert len(tuple(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("failure", ["disk", "cancel", "timeout", "digest", "mutation"])
def test_failed_attempt_never_publishes_a_selection(tmp_path: Path, failure: str) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    optimizer = _Optimizer(
        corrupt_digest=failure == "digest", mutate_baseline=failure == "mutation"
    )
    control = PdfExportAdapterControl(
        100,
        cancellation_probe=lambda: failure == "cancel",
        monotonic=lambda: 101.0 if failure == "timeout" else 1.0,
    )
    with pytest.raises(PdfExportError) as caught:
        optimize_verified_pdf(
            baseline,
            _report(),
            operation_temp=tmp_path,
            resource_limits=_resources(),
            adapter_limits=_limits(),
            control=control,
            private_temp_storage=OsWorkerPrivateTempStorage(
                available_bytes_probe=lambda _: 0 if failure == "disk" else 2**40
            ),
            optimizer=optimizer,
        )
    assert (
        caught.value.code
        is {
            "disk": PdfExportErrorCode.LIMIT_EXCEEDED,
            "cancel": PdfExportErrorCode.CANCELLED,
            "timeout": PdfExportErrorCode.TIMED_OUT,
            "digest": PdfExportErrorCode.RESULT_INVALID,
            "mutation": PdfExportErrorCode.RESULT_INVALID,
        }[failure]
    )
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__context__ is None
    if failure != "mutation":
        assert baseline.read_bytes() == BASELINE


def test_existing_hardlink_is_rejected_without_optimization(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    os.link(baseline, tmp_path / "alias.pdf")
    optimizer = _Optimizer()
    with pytest.raises(PdfExportError, match="PDF_EXPORT_RESULT_INVALID"):
        optimize_verified_pdf(
            baseline,
            _report(),
            operation_temp=tmp_path,
            resource_limits=_resources(),
            adapter_limits=_limits(),
            control=_control(),
            private_temp_storage=OsWorkerPrivateTempStorage(),
            optimizer=optimizer,
        )
    assert optimizer.calls == 0
    assert baseline.read_bytes() == BASELINE


def test_existing_extent_and_output_reservation_cannot_exceed_quota(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    baseline.write_bytes(BASELINE)
    optimizer = _Optimizer()
    with pytest.raises(PdfExportError, match="PDF_EXPORT_LIMIT_EXCEEDED"):
        optimize_verified_pdf(
            baseline,
            _report(),
            operation_temp=tmp_path,
            resource_limits=replace(
                _resources(),
                temporary_storage_bytes=len(BASELINE) + 4096 - 1,
                output_bytes=4096,
            ),
            adapter_limits=_limits(),
            control=_control(),
            private_temp_storage=OsWorkerPrivateTempStorage(),
            optimizer=optimizer,
        )
    assert optimizer.calls == 0
    assert baseline.read_bytes() == BASELINE
