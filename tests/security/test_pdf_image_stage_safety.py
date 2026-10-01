"""Image stages cannot hide unrelated mutations or publish failed private output."""

from __future__ import annotations

import errno
import hashlib
import traceback
import zlib
from dataclasses import replace
from pathlib import Path
from typing import cast

import pikepdf
import pytest

import paperworkshell.pdf.image_stage_adapter as adapter_module
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import (
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageRequest,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, _BoundedCandidateStream
from paperworkshell.pdf.image_stage_adapter import PdfImageStageAdapter
from paperworkshell.pdf.optimization_adapter import _graph_fingerprint
from tests.unit.test_pdf_image_stage_adapter import (
    image_limits,
    make_image_baseline,
    run_image_stage,
)
from tests.unit.test_pdf_optimization_adapter import CleanChecker, baseline_report, control, limits


@pytest.mark.parametrize(
    "mutation",
    [
        "text",
        "invisible_text",
        "font",
        "other_image",
        "other_encoding",
        "selected_bytes",
        "mask",
        "metadata",
        "alias",
        "geometry",
        "page_count",
    ],
)
def test_projected_image_verification_rejects_every_nonselected_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    def fixture(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        other = document.make_stream(b"abc" * 16)
        other.Type, other.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        other.Width = other.Height = 4
        other.BitsPerComponent = 8
        other.ColorSpace = pikepdf.Name.DeviceRGB
        page.obj.Resources.XObject.Zother = other

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=fixture)
    original = baseline.read_bytes()
    real_write = adapter_module._write_candidate

    def corrupt(
        document: pikepdf.Pdf,
        output: _BoundedCandidateStream,
        operation_control: PdfExportAdapterControl,
    ) -> None:
        page = document.pages[0]
        if mutation == "text":
            page.obj.Contents.write(
                page.obj.Contents.read_bytes().replace(b"SYNTHETIC TEXT", b"DIFFERENT TEXT")
            )
        elif mutation == "invisible_text":
            page.obj.Contents.write(
                page.obj.Contents.read_bytes() + b"BT /F1 8 Tf 3 Tr (HIDDEN CHANGE) Tj ET\n"
            )
        elif mutation == "font":
            page.obj.Resources.Font.F1.BaseFont = pikepdf.Name.Courier
        elif mutation == "other_image":
            page.obj.Resources.XObject.Zother.write(b"xyz" * 16)
        elif mutation == "other_encoding":
            image = page.obj.Resources.XObject.Zother
            image.write(zlib.compress(image.read_bytes()), filter=pikepdf.Name.FlateDecode)
        elif mutation == "selected_bytes":
            image = page.obj.Resources.XObject.Pixels
            image.write(b"invalid encoded image", filter=pikepdf.Name.DCTDecode)
        elif mutation == "mask":
            page.obj.Resources.XObject.Pixels.Mask = pikepdf.Array([0, 0, 0, 0, 0, 0])
        elif mutation == "metadata":
            document.docinfo.Title = "DIFFERENT SYNTHETIC METADATA"
        elif mutation == "alias":
            document.Root.ExtraImageAlias = page.obj.Resources.XObject.Pixels
        elif mutation == "geometry":
            page.obj.CropBox = pikepdf.Array([0, 0, 50, 50])
        elif mutation == "page_count":
            del document.pages[1]
        real_write(document, output, operation_control)

    monkeypatch.setattr(adapter_module, "_write_candidate", corrupt)
    with pytest.raises(PdfExportError):
        run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert baseline.read_bytes() == original and not candidate.exists()


@pytest.mark.parametrize("failure", ["cancel", "deadline", "disk_full", "invalid", "callback"])
def test_active_failure_cleanup_is_sanitized_and_next_attempt_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline)
    original = baseline.read_bytes()
    report = baseline_report(baseline)
    request = PdfImageStageRequest(
        PdfImageStageProfile.PRESERVE_300_DPI_Q90,
        (0,),
        report.output_sha256,
        report.semantic_fingerprint,
    )
    active = False
    operation_control = PdfExportAdapterControl(
        deadline=10_000,
        cancellation_probe=lambda: active and failure == "cancel",
        monotonic=lambda: 20_000 if active and failure == "deadline" else 1,
    )
    real_write = adapter_module._write_candidate

    def corrupt(
        document: pikepdf.Pdf,
        output: _BoundedCandidateStream,
        selected_control: PdfExportAdapterControl,
    ) -> None:
        nonlocal active
        active = True
        output.write(b"partial")
        if failure == "disk_full":
            raise OSError(errno.ENOSPC, "SYNTHETIC_PRIVATE_PATH_CANARY")
        if failure == "invalid":
            return
        real_write(document, output, selected_control)

    def progress(done: int, total: int) -> None:
        if active and failure == "callback":
            raise ValueError("SYNTHETIC_PRIVATE_PATH_CANARY")

    monkeypatch.setattr(adapter_module, "_write_candidate", corrupt)
    with pytest.raises(PdfExportError) as caught:
        PdfImageStageAdapter(qpdf_checker=CleanChecker()).process(
            baseline,
            candidate,
            baseline_report=report,
            request=request,
            limits=image_limits(),
            control=operation_control,
            progress=progress,
        )
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_PATH_CANARY" not in "".join(traceback.format_exception(caught.value))
    if failure == "cancel":
        assert caught.value.code is PdfExportErrorCode.CANCELLED
    elif failure == "deadline":
        assert caught.value.code is PdfExportErrorCode.TIMED_OUT
    assert baseline.read_bytes() == original and not candidate.exists()
    monkeypatch.setattr(adapter_module, "_write_candidate", real_write)
    assert (
        run_image_stage(
            baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90
        ).report.outcome
        is PdfImageStageOutcome.REENCODED
    )


def test_existing_files_are_never_cleanup_targets(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline)
    original = baseline.read_bytes()
    candidate.write_bytes(b"GOOD EXISTING DERIVATIVE")
    with pytest.raises(PdfExportError) as caught:
        run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert caught.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert candidate.read_bytes() == b"GOOD EXISTING DERIVATIVE"
    with pytest.raises(PdfExportError):
        run_image_stage(baseline, baseline, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert baseline.read_bytes() == original


def test_old_analysis_binding_fails_before_encoding_or_candidate_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline)
    report = baseline_report(baseline)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("stale intent reached native encoder")

    monkeypatch.setattr(adapter_module, "encode_image", forbidden)
    for request in (
        PdfImageStageRequest(
            PdfImageStageProfile.PRESERVE_300_DPI_Q90, (0,), "a" * 64, report.semantic_fingerprint
        ),
        PdfImageStageRequest(
            PdfImageStageProfile.PRESERVE_300_DPI_Q90, (0,), report.output_sha256, "b" * 64
        ),
    ):
        with pytest.raises(PdfExportError) as caught:
            PdfImageStageAdapter(qpdf_checker=CleanChecker()).process(
                baseline,
                tmp_path / "candidate.pdf",
                baseline_report=report,
                request=request,
                limits=image_limits(),
                control=control(),
            )
        assert caught.value.code is PdfExportErrorCode.PLAN_STALE
    assert not (tmp_path / "candidate.pdf").exists()


def test_projection_default_still_authenticates_all_image_bytes(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline)
    with pikepdf.Pdf.open(baseline) as document:
        trailer = cast(pikepdf.Dictionary, document.trailer)
        image = document.pages[0].obj.Resources.XObject.Pixels
        projection = {image.objgen: 0}
        ordinary = _graph_fingerprint(trailer, limits(), control(), 4_000_000)
        projected = _graph_fingerprint(trailer, limits(), control(), 4_000_000, projection)
        image.write(b"DIFFERENT IMAGE SAMPLES")
        assert _graph_fingerprint(trailer, limits(), control(), 4_000_000) != ordinary
        assert _graph_fingerprint(trailer, limits(), control(), 4_000_000, projection) == projected
        document.docinfo.Title = "DIFFERENT METADATA"
        assert _graph_fingerprint(trailer, limits(), control(), 4_000_000, projection) != projected


def test_semantic_and_content_expansion_limits_fail_closed(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline)
    # The analysis byte cap is independent of input compression ratio.
    with pikepdf.Pdf.open(baseline, allow_overwriting_input=True) as document:
        document.pages[0].obj.Contents.write(b"% bounded content expansion\n" * 50_000)
        document.save(baseline)
    original = hashlib.sha256(baseline.read_bytes()).hexdigest()
    with pytest.raises(PdfExportError) as caught:
        run_image_stage(
            baseline,
            tmp_path / "candidate.pdf",
            selected_limits=replace(image_limits(), maximum_memory_bytes=1_000_000),
        )
    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == original
