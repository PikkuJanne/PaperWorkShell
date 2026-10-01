"""Generated PWS-0502 corpus and independent optimization failure probes."""

from __future__ import annotations

import hashlib
import io
import time
import traceback
import zlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pikepdf
import pytest
from PIL import Image

import paperworkshell.pdf.optimization_adapter as adapter_module
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits, PdfStructuralState
from paperworkshell.domain.pdf_optimization import PdfOptimizationOutcome
from paperworkshell.domain.pdf_rendering import PdfPageRenderLimits
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
    _BoundedCandidateStream,
)
from paperworkshell.pdf.optimization_adapter import PdfOptimizationAdapter


class CleanChecker:
    """Unit seam; the live integration owns executable qpdf evidence."""

    def __init__(self, state: PdfStructuralState = PdfStructuralState.CLEAN) -> None:
        self.state = state
        self.calls = 0

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: object,
        monotonic: object,
    ) -> PdfStructuralState:
        assert source_path.is_file() and deadline > 0
        assert limits.maximum_pages >= 1
        assert callable(cancellation_probe) and callable(monotonic)
        self.calls += 1
        return self.state


def limits() -> PdfExportAdapterLimits:
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(10, 10_000, 2_000_000, 2_000_000, 2_000_000, 100_000),
        rendering=PdfPageRenderLimits(
            maximum_pages=10,
            maximum_width_pixels=128,
            maximum_height_pixels=128,
            maximum_pixels=128 * 128,
            maximum_bitmap_bytes=128 * 128 * 4,
            maximum_destination_bytes=128 * 128 * 4,
            maximum_memory_bytes=128 * 128 * 4,
            wall_time_ms=30_000,
        ),
        maximum_total_source_bytes=2_000_000,
        verification_width_pixels=128,
        verification_height_pixels=128,
    )


def control() -> PdfExportAdapterControl:
    return PdfExportAdapterControl(time.monotonic() + 30)


def make_baseline(path: Path, *, compressed: bool = False, pages: int = 2) -> None:
    """No external assets: text, Form paint, pixels, metadata, and retained features."""
    with pikepdf.Pdf.new() as document:
        font = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
            )
        )
        font.ToUnicode = document.make_stream(
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"/CIDSystemInfo << /Registry (Synthetic) /Ordering (Unicode) /Supplement 0 >> def\n"
            b"/CMapName /Synthetic def /CMapType 2 def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n"
            b"1 beginbfchar <49> <0049> endbfchar\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end\n"
        )
        form = document.make_stream(b"q 0 0 1 rg 10 10 20 20 re f Q\n")
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        form.Resources = pikepdf.Dictionary()
        image = document.make_stream(bytes([80, 160, 220]) * 16)
        image.Type = pikepdf.Name.XObject
        image.Subtype = pikepdf.Name.Image
        image.Width = image.Height = 4
        image.BitsPerComponent = 8
        image.ColorSpace = pikepdf.Name.DeviceRGB
        unused = document.make_stream(b"preserved reachable unused resource\n" * 300)
        unused.Type = pikepdf.Name.XObject
        unused.Subtype = pikepdf.Name.Form
        unused.BBox = pikepdf.Array([0, 0, 100, 100])
        unused.Resources = pikepdf.Dictionary()
        # Valid unused content, with the token confined to comments.
        unused.write(b"% preserved reachable unused resource\n" * 300)
        for index in range(pages):
            page = document.add_blank_page(page_size=(200, 160))
            page.obj.Resources = pikepdf.Dictionary(
                Font=pikepdf.Dictionary(F1=font),
                XObject=pikepdf.Dictionary(Overlay=form, Pixels=image, Unused=unused),
            )
            page.obj.Contents = document.make_stream(
                b"% compression overhead\n" * 800
                + f"BT /F1 12 Tf 20 110 Td (VISIBLE {index}) Tj ET\n".encode()
                + b"BT /F1 12 Tf 3 Tr 20 90 Td (INVISIBLE TEXT) Tj ET\n"
                + b"q /Overlay Do Q\nq 30 0 0 30 70 20 cm /Pixels Do Q\n"
            )
            page.obj.CropBox = pikepdf.Array([2, 2, 198, 158])
            page.obj.Rotate = index * 90
            page.obj.UserUnit = 1
            page.obj.Annots = pikepdf.Array(
                [
                    document.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name.Annot,
                            Subtype=pikepdf.Name.Text,
                            Rect=pikepdf.Array([150, 30, 170, 50]),
                            Contents="SYNTHETIC COMMENT",
                        )
                    )
                ]
            )
        document.docinfo["/Title"] = "SYNTHETIC METADATA"
        document.Root.PWSRetained = pikepdf.Dictionary(
            File=document.make_stream(b"SYNTHETIC ATTACHMENT"),
            Nested=pikepdf.Array([True, None, pikepdf.String("SYNTHETIC VALUE")]),
        )
        document.save(
            path,
            compress_streams=compressed,
            object_stream_mode=(
                pikepdf.ObjectStreamMode.generate
                if compressed
                else pikepdf.ObjectStreamMode.disable
            ),
            deterministic_id=True,
        )


def baseline_report(path: Path) -> PdfExportVerificationReport:
    payload = path.read_bytes()
    with pikepdf.Pdf.open(path) as document:
        count = len(document.pages)
    source_id = uuid4()
    plan = PdfExportPlan(
        case_id=uuid4(),
        manifest_revision=1,
        manifest_sha256="0" * 64,
        manifest_cas_token="optimization-test",
        sources=(
            PdfExportSource(
                source_id,
                len(payload),
                count,
                PdfExportSourceSignatureState.NONE_DETECTED,
                hashlib.sha256(payload).hexdigest(),
            ),
        ),
        pages=tuple(
            PdfExportPage(index, uuid4(), source_id, index, PdfExportRotation.DEGREES_0)
            for index in range(count)
        ),
    )
    return PdfExportVerificationReport.for_verified_plan(
        plan, output_sha256=hashlib.sha256(payload).hexdigest(), output_byte_size=len(payload)
    )


def run(
    baseline: Path,
    candidate: Path,
    *,
    cap: int | None = None,
    semantic: int = 1_000_000,
    selected_control: PdfExportAdapterControl | None = None,
    selected_limits: PdfExportAdapterLimits | None = None,
    checker: CleanChecker | None = None,
) -> adapter_module.PdfOptimizationAdapterResult:
    report = baseline_report(baseline)
    return PdfOptimizationAdapter(qpdf_checker=checker or CleanChecker()).optimize(
        baseline,
        candidate,
        baseline_report=report,
        limits=selected_limits or limits(),
        control=selected_control or control(),
        maximum_candidate_bytes=report.output_byte_size - 1 if cap is None else cap,
        maximum_semantic_bytes=semantic,
    )


def test_generated_corpus_is_smaller_and_preserves_all_reachable_state(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    checker = CleanChecker()
    result = run(baseline, candidate, checker=checker)
    assert result.report.outcome is PdfOptimizationOutcome.OPTIMIZED
    assert result.report.verified_page_count == 2
    assert result.report.selected_byte_size == candidate.stat().st_size < len(original)
    assert result.candidate_sha256 == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert checker.calls == 1
    assert baseline.read_bytes() == original
    with pikepdf.Pdf.open(candidate) as document:
        assert "/Unused" in document.pages[0].obj.Resources.XObject
        assert document.Root.PWSRetained.File.read_bytes() == b"SYNTHETIC ATTACHMENT"
        assert document.pages[0].obj.Annots[0].Contents == "SYNTHETIC COMMENT"


@pytest.mark.parametrize("cap", [1, 100, None])
def test_ineffective_candidate_keeps_baseline_and_removes_scratch(
    tmp_path: Path, cap: int | None
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline, compressed=True)
    original = baseline.read_bytes()
    result = run(baseline, candidate, cap=cap)
    assert result.report.outcome is PdfOptimizationOutcome.NO_REDUCTION
    assert result.report.candidate_byte_size is None
    assert result.report.selected_byte_size == len(original)
    assert result.candidate_sha256 is None
    assert baseline.read_bytes() == original and not candidate.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "hidden_text",
        "font",
        "text_map",
        "overlay",
        "pixels",
        "metadata",
        "annotation",
        "reachable_unused",
        "pages",
        "encryption",
    ],
)
def test_candidate_semantic_mutation_never_replaces_good_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    write = adapter_module._write_candidate

    def corrupt(
        document: pikepdf.Pdf,
        stream: _BoundedCandidateStream,
        selected_control: PdfExportAdapterControl,
    ) -> None:
        page = document.pages[0].obj
        if mutation == "hidden_text":
            page.Contents.write(
                page.Contents.read_bytes().replace(b"INVISIBLE TEXT", b"CHANGED TEXT")
            )
        elif mutation == "font":
            page.Resources.Font.F1.Encoding = pikepdf.Name.MacRomanEncoding
        elif mutation == "text_map":
            mapping = page.Resources.Font.F1.ToUnicode
            mapping.write(mapping.read_bytes().replace(b"<0049>", b"<0058>"))
        elif mutation == "overlay":
            page.Resources.XObject.Overlay.write(b"q Q\n")
        elif mutation == "pixels":
            page.Resources.XObject.Pixels.write(bytes([255, 255, 255]) * 16)
        elif mutation == "metadata":
            document.docinfo["/Title"] = "CHANGED"
        elif mutation == "annotation":
            page.Annots[0].Contents = "CHANGED"
        elif mutation == "reachable_unused":
            del page.Resources.XObject["/Unused"]
        elif mutation == "pages":
            del document.pages[-1]
        else:
            document.save(stream, encryption=pikepdf.Encryption(owner="", user="", R=4))  # type: ignore[arg-type]
            return
        write(document, stream, selected_control)

    monkeypatch.setattr(adapter_module, "_write_candidate", corrupt)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate)
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert caught.value.context == (
        "optimization.policy" if mutation in {"pages", "encryption"} else "optimization.semantics"
    )
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert baseline.read_bytes() == original and not candidate.exists()


def test_fresh_qpdf_failure_removes_only_candidate(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate, checker=CleanChecker(PdfStructuralState.FAILED))
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert baseline.read_bytes() == original and not candidate.exists()


@pytest.mark.parametrize("stage", ["cancel", "deadline", "semantic", "objects"])
def test_constrained_failure_cleans_and_allows_fresh_retry(tmp_path: Path, stage: str) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    kwargs: dict[str, object] = {}
    if stage == "cancel":
        kwargs["selected_control"] = PdfExportAdapterControl(time.monotonic() + 30, lambda: True)
    elif stage == "deadline":
        kwargs["selected_control"] = PdfExportAdapterControl(1)
    elif stage == "semantic":
        kwargs["semantic"] = 100
    else:
        kwargs["selected_limits"] = replace(
            limits(), inspection=replace(limits().inspection, maximum_objects=3)
        )
    with pytest.raises(PdfExportError):
        run(baseline, candidate, **kwargs)  # type: ignore[arg-type]
    assert baseline.read_bytes() == original and not candidate.exists()
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED


def test_private_native_exception_is_not_retained_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_baseline(baseline)
    original = baseline.read_bytes()
    write = adapter_module._write_candidate
    canary = "SYNTHETIC_PRIVATE_PATH_AND_PDF_VALUE"

    def fail(*_args: object) -> None:
        raise RuntimeError(canary) from ValueError(canary)

    monkeypatch.setattr(adapter_module, "_write_candidate", fail)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert canary not in "".join(traceback.format_exception(caught.value))
    assert baseline.read_bytes() == original and not candidate.exists()
    monkeypatch.setattr(adapter_module, "_write_candidate", write)
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED


def test_incremental_flate_expansion_fails_before_native_syntax_check(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    with pikepdf.Pdf.new() as document:
        document.add_blank_page()
        stream = document.make_stream(zlib.compress(b"x" * 2_000_000))
        stream.Filter = pikepdf.Name.FlateDecode
        document.Root.PWSRetained = stream
        document.save(baseline, stream_decode_level=pikepdf.StreamDecodeLevel.none)
    original = baseline.read_bytes()
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate, semantic=100_000)
    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "optimization.semantic_bytes"
    assert baseline.read_bytes() == original and not candidate.exists()


def test_cyclic_shared_graph_and_opaque_filter_bytes_survive_rewrite(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(100, 100))
        page.obj.Contents = document.make_stream(b"% removable serialization overhead\n" * 200)
        node = document.make_indirect(pikepdf.Dictionary(Value="SYNTHETIC SHARED VALUE"))
        node.Self = node
        opaque = document.make_stream(zlib.compress(b"SAMPLE"))
        opaque.Filter = pikepdf.Name.FlateDecode
        opaque.DecodeParms = pikepdf.Dictionary(Predictor=1)
        document.Root.PWSRetained = pikepdf.Dictionary(
            First=node,
            Second=node,
            Opaque=opaque,
            Precise=Decimal("0.123456789012345678901234567890123456789"),
        )
        document.save(
            baseline, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none
        )
    original = baseline.read_bytes()
    result = run(baseline, candidate)
    assert result.report.outcome is PdfOptimizationOutcome.OPTIMIZED
    assert baseline.read_bytes() == original
    with pikepdf.Pdf.open(candidate) as document:
        retained = document.Root.PWSRetained
        assert retained.First.objgen == retained.Second.objgen == retained.First.Self.objgen
        assert retained.Opaque.read_raw_bytes() == zlib.compress(b"SAMPLE")
        assert retained.Opaque.Filter == pikepdf.Name.FlateDecode
        assert retained.Opaque.DecodeParms.Predictor == 1


def test_generalized_filter_rewrite_is_rejected_without_decoding_opaque_semantics(
    tmp_path: Path,
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page()
        page.obj.Contents = document.make_stream(b"% compressible comment\n" * 300)
        opaque = document.make_stream(b"53414d504c45>")
        opaque.Filter = pikepdf.Name.ASCIIHexDecode
        document.Root.PWSRetained = opaque
        document.save(
            baseline, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none
        )
    # Pinned qpdf rewrites ASCIIHexDecode to FlateDecode even with decode_level=none.
    # This profile never guesses at generalized-filter equivalence.
    original = baseline.read_bytes()
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate)
    assert caught.value.context == "optimization.semantics"
    assert baseline.read_bytes() == original and not candidate.exists()


def test_dct_image_remains_byte_exact_without_reencoding(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    with Image.new("RGB", (8, 8), (60, 120, 220)) as image, io.BytesIO() as stream:
        image.save(stream, "JPEG", quality=90)
        encoded_image = stream.getvalue()
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(100, 100))
        raster = document.make_stream(encoded_image)
        raster.Type = pikepdf.Name.XObject
        raster.Subtype = pikepdf.Name.Image
        raster.Filter = pikepdf.Name.DCTDecode
        raster.Width = raster.Height = 8
        raster.ColorSpace = pikepdf.Name.DeviceRGB
        raster.BitsPerComponent = 8
        page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Image=raster))
        page.obj.Contents = document.make_stream(
            b"% compressible comment\n" * 300 + b"q 80 0 0 80 10 10 cm /Image Do Q\n"
        )
        document.save(
            baseline, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none
        )
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED
    with pikepdf.Pdf.open(candidate) as document:
        assert document.pages[0].obj.Resources.XObject.Image.read_raw_bytes() == encoded_image
        assert document.pages[0].obj.Resources.XObject.Image.Filter == pikepdf.Name.DCTDecode


def test_unreachable_stream_is_discarded_but_reachable_graph_is_preserved(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    content = b"q 0 0 1 rg 10 10 50 50 re f Q\n"
    unreachable = b"SYNTHETIC UNREACHABLE OBJECT\n" * 500
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources <<>> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Length "
        + str(len(unreachable)).encode()
        + b" >>\nstream\n"
        + unreachable
        + b"endstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    startxref = len(payload)
    payload.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010} 00000 n \n".encode())
    payload.extend(f"trailer << /Root 1 0 R /Size 6 >>\nstartxref\n{startxref}\n%%EOF\n".encode())
    baseline.write_bytes(payload)
    assert run(baseline, candidate).report.outcome is PdfOptimizationOutcome.OPTIMIZED
    assert baseline.read_bytes() == payload
    with pikepdf.Pdf.open(candidate) as document:
        assert all(
            value.read_bytes() != unreachable
            for value in document.objects
            if isinstance(value, pikepdf.Stream)
        )


def test_reachable_shared_identity_mutation_is_not_hidden_by_equal_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page()
        page.obj.Contents = document.make_stream(b"% compressible comment\n" * 300)
        node = document.make_indirect(pikepdf.Dictionary(Value="SYNTHETIC VALUE"))
        document.Root.PWSRetained = pikepdf.Dictionary(First=node, Second=node)
        document.save(baseline, compress_streams=False)
    original = baseline.read_bytes()
    write = adapter_module._write_candidate

    def corrupt(
        document: pikepdf.Pdf,
        stream: _BoundedCandidateStream,
        selected_control: PdfExportAdapterControl,
    ) -> None:
        document.Root.PWSRetained.Second = document.make_indirect(
            pikepdf.Dictionary(Value="SYNTHETIC VALUE")
        )
        write(document, stream, selected_control)

    monkeypatch.setattr(adapter_module, "_write_candidate", corrupt)
    with pytest.raises(PdfExportError) as caught:
        run(baseline, candidate)
    assert caught.value.context == "optimization.semantics"
    assert baseline.read_bytes() == original and not candidate.exists()
