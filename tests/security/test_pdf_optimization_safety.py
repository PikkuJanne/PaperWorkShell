"""Faults cannot promote optimization output or mutate the verified baseline."""

from __future__ import annotations

import errno
import time
from collections.abc import Buffer
from dataclasses import replace
from pathlib import Path

import pikepdf
import pytest

import paperworkshell.pdf.optimization_adapter as adapter_module
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_optimization import PdfOptimizationOutcome
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, _BoundedCandidateStream
from paperworkshell.pdf.optimization_adapter import PdfOptimizationAdapter
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.renderer import PdfRasterDestination, PdfRenderControl, PdfRenderSource
from tests.unit.test_pdf_optimization_adapter import (
    CleanChecker,
    baseline_report,
    control,
    limits,
    make_baseline,
    run,
)


@pytest.mark.parametrize("failure", ["cancel", "deadline", "disk_full", "invalid"])
def test_failure_after_private_candidate_creation_removes_scratch_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    write = adapter_module._write_candidate
    active = False
    selected_control = PdfExportAdapterControl(
        deadline=10_000,
        cancellation_probe=lambda: active and failure == "cancel",
        monotonic=lambda: 20_000 if active and failure == "deadline" else 1,
    )

    def fail(
        document: pikepdf.Pdf,
        stream: _BoundedCandidateStream,
        operation_control: PdfExportAdapterControl,
    ) -> None:
        nonlocal active
        assert candidate.exists()
        stream.write(b"partial candidate")
        active = True
        if failure == "disk_full":
            raise OSError(errno.ENOSPC, "SYNTHETIC_PRIVATE_DISK_PATH")
        if failure == "invalid":
            return
        write(document, stream, operation_control)

    monkeypatch.setattr(adapter_module, "_write_candidate", fail)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate, selected_control=selected_control)
    expected = {
        "cancel": PdfExportErrorCode.CANCELLED,
        "deadline": PdfExportErrorCode.TIMED_OUT,
        "disk_full": PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
        "invalid": PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
    }
    assert caught.value.code is expected[failure]
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert baseline.read_bytes() == original and not candidate.exists()
    monkeypatch.setattr(adapter_module, "_write_candidate", write)
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED


def test_existing_candidate_and_baseline_paths_never_become_cleanup_targets(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    existing = b"prior independently verified output"
    candidate.write_bytes(existing)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate)
    assert caught.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert candidate.read_bytes() == existing and baseline.read_bytes() == original
    with pytest.raises(PdfExportError) as caught:
        run(baseline, baseline)
    assert caught.value.code is PdfExportErrorCode.MODEL_INVALID
    assert baseline.read_bytes() == original


def test_claimed_baseline_identity_is_checked_before_candidate_creation(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    report = replace(baseline_report(baseline), output_sha256="f" * 64)
    with pytest.raises(PdfExportError) as caught:
        PdfOptimizationAdapter(qpdf_checker=CleanChecker()).optimize(
            baseline,
            candidate,
            baseline_report=report,
            limits=limits(),
            control=control(),
            maximum_candidate_bytes=report.output_byte_size - 1,
            maximum_semantic_bytes=1_000_000,
        )
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert caught.value.context == "optimization.baseline" and not candidate.exists()


class MismatchingRenderer:
    def __init__(self) -> None:
        self.delegate = PdfiumPageRenderer()
        self.calls = 0

    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        operation_limits: PdfPageRenderLimits,
        operation_control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        result = self.delegate.render(
            source, destination, request, operation_limits, operation_control
        )
        self.calls += 1
        return replace(result, pixel_sha256="f" * 64) if self.calls == 4 else result


def test_every_page_render_digest_is_checked_after_structural_equality(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    report = baseline_report(baseline)
    renderer = MismatchingRenderer()
    with pytest.raises(PdfExportError) as caught:
        PdfOptimizationAdapter(qpdf_checker=CleanChecker(), renderer=renderer).optimize(
            baseline,
            candidate,
            baseline_report=report,
            limits=limits(),
            control=control(),
            maximum_candidate_bytes=report.output_byte_size - 1,
            maximum_semantic_bytes=1_000_000,
        )
    assert renderer.calls == 4
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_RENDER_FAILED
    assert baseline.read_bytes() == original and not candidate.exists()


def test_partial_short_write_cannot_be_described_as_ineffective_optimization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()

    def fail(_self: _BoundedCandidateStream, _data: Buffer) -> int:
        raise OSError(errno.EIO, "SYNTHETIC_PRIVATE_SHORT_WRITE")

    monkeypatch.setattr(_BoundedCandidateStream, "write", fail)
    with pytest.raises(PdfExportError):
        run(baseline, candidate)
    assert baseline.read_bytes() == original and not candidate.exists()


def test_post_write_cancel_does_not_turn_into_cap_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    write = adapter_module._write_candidate
    active = False

    def limited(
        document: pikepdf.Pdf,
        stream: _BoundedCandidateStream,
        operation_control: PdfExportAdapterControl,
    ) -> None:
        nonlocal active
        try:
            write(document, stream, operation_control)
        finally:
            active = True

    monkeypatch.setattr(adapter_module, "_write_candidate", limited)
    with pytest.raises(PdfExportError) as caught:
        run(
            baseline,
            candidate,
            cap=1,
            selected_control=PdfExportAdapterControl(time.monotonic() + 30, lambda: active),
        )
    assert caught.value.code is PdfExportErrorCode.CANCELLED and not candidate.exists()


def test_candidate_cleanup_failure_is_closed_without_touching_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    unlink = Path.unlink

    def denied(path: Path, missing_ok: bool = False) -> None:
        if path == candidate:
            raise PermissionError("SYNTHETIC PRIVATE CLEANUP PATH")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate, cap=1)
    assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
    assert caught.value.context == "optimization.cleanup"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert baseline.read_bytes() == original
    # The worker's owned operation-directory cleanup retains responsibility after
    # a failed per-candidate removal. Restore that capability for this unit seam.
    monkeypatch.setattr(Path, "unlink", unlink)
    candidate.unlink(missing_ok=True)
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED
