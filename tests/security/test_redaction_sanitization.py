"""Synthetic active-content, metadata and resource-boundary regression evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import zlib
from collections.abc import Buffer
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pikepdf
import pytest
from fontTools import subset  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont, newTable  # type: ignore[import-untyped]
from PIL import Image

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_rendering import PdfPageRenderRequest
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionLimits,
    _render_limits,
)
from paperworkshell.pdf.redaction_sanitization import _NOTO_NAMES, PikepdfRedactionSanitizer

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "v1" / "pdf"
_CANARY = b"PWSSANITIZERREMOVEDTOKEN"


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


class _Sink:
    def write(self, data: Buffer, /) -> int:
        return memoryview(data).nbytes

    def flush(self) -> None:
        return None


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _serialize(document: pikepdf.Pdf) -> bytes:
    content = BytesIO()
    document.save(content, compress_streams=False, deterministic_id=True)
    return content.getvalue()


def _sanitize(content: bytes, output: Path) -> RedactionSanitizationReport:
    request = RedactionSanitizationRequest(
        uuid4(), uuid4(), hashlib.sha256(content).hexdigest(), len(content), 1
    )
    return PikepdfRedactionSanitizer().sanitize(
        BytesIO(content), output, output.parent, request, _limits(), _Control()
    )


def _all_bytes(path: Path) -> bytes:
    with pikepdf.Pdf.open(path) as document:
        streams = [
            item.read_bytes() for item in document.objects if isinstance(item, pikepdf.Stream)
        ]
    return path.read_bytes() + b"".join(streams)


@pytest.mark.parametrize(
    ("fixture", "category", "canary"),
    [
        ("attachment", "attachments", b"PWSREDFILEHOTEL"),
        ("annotation", "annotations", b"PWSREDANNOTDELTA"),
        ("form", "form_fields", b"PWSREDFORMECHO"),
        ("metadata", "metadata", b"PWSREDXMPGOLF"),
        ("incremental", "incremental_revisions", b"PWSREDOLDJULIET"),
    ],
)
def test_feature_corpus_leaves_no_recoverable_nonpage_canary(
    tmp_path: Path, fixture: str, category: str, canary: bytes
) -> None:
    content = (_FIXTURES / f"redaction-{fixture}.pdf").read_bytes()
    output = tmp_path / "candidate.pdf"
    report = _sanitize(content, output)
    assert getattr(report.before, category) > 0
    assert report.after.empty
    assert report.status == "awaiting_verification"
    assert canary not in _all_bytes(output)
    assert output.read_bytes().count(b"startxref") == 1
    with pikepdf.Pdf.open(BytesIO(content)) as source, pikepdf.Pdf.open(output) as result:
        assert pikepdf.unparse_content_stream(
            pikepdf.parse_content_stream(source.pages[0])
        ) == pikepdf.unparse_content_stream(pikepdf.parse_content_stream(result.pages[0]))


def test_nested_resource_stream_and_global_carriers_are_removed_without_logging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        metadata = pikepdf.Stream(
            document, _CANARY, Type=pikepdf.Name.Metadata, Subtype=pikepdf.Name.XML
        )
        action = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String(_CANARY))
        image = pikepdf.Stream(
            document,
            b"\xff\x00\x00" * 4,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=2,
            Height=2,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Metadata=metadata,
            AA=action,
        )
        form = pikepdf.Stream(
            document,
            b"q 72 0 0 48 0 0 cm /Im0 Do Q",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=pikepdf.Array([0, 0, 72, 48]),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image)),
            Metadata=metadata,
            AF=pikepdf.Array([pikepdf.Dictionary(EF=pikepdf.Dictionary(F=metadata))]),
        )
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Form0=form), Metadata=metadata
        )
        page.Contents = pikepdf.Stream(
            document,
            b"/Form0 Do",
            Metadata=metadata,
            PieceInfo=pikepdf.Dictionary(Secret=pikepdf.String(_CANARY)),
        )
        document.Root.OpenAction = action
        document.Root.PrivateCarrier = pikepdf.Dictionary(Secret=pikepdf.String(_CANARY))
        document.docinfo["/Subject"] = pikepdf.String(_CANARY)
        content = _serialize(document)
    output = tmp_path / "candidate.pdf"
    report = _sanitize(content, output)
    assert report.before.scripts > 0 and report.before.actions > 0
    assert report.after.empty
    assert _CANARY not in _all_bytes(output)
    assert _CANARY.decode("ascii") not in caplog.text
    assert _CANARY.decode("ascii") not in repr(report)
    assert _CANARY.decode("ascii") not in str(report.to_record())
    with output.open("rb") as source:
        assert (
            PdfiumPageRenderer()
            .render(
                source,
                _Sink(),
                PdfPageRenderRequest(0, 72, 48),
                _render_limits(_limits()),
                _Control(),
            )
            .bytes_written
            == 72 * 48 * 4
        )


@pytest.mark.parametrize(
    "carrier", ["unknown", "type3", "icc", "jpeg", "trailing", "expansion", "depth", "external"]
)
def test_unsupported_or_excessive_carriers_fail_closed(
    tmp_path: Path, carrier: str, caplog: pytest.LogCaptureFixture
) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        page.Contents = pikepdf.Stream(document, b"q 72 0 0 48 0 0 cm /Im0 Do Q")
        image = pikepdf.Stream(
            document,
            b"\xff\x00\x00" * 4,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=2,
            Height=2,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
        if carrier == "unknown":
            image.PrivateCarrier = pikepdf.String(_CANARY)
        elif carrier == "type3":
            page.Resources.Font = pikepdf.Dictionary(
                F0=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type3)
            )
        elif carrier == "icc":
            image.ColorSpace = pikepdf.Array(
                [pikepdf.Name.ICCBased, pikepdf.Stream(document, _CANARY, N=3)]
            )
        elif carrier == "jpeg":
            image.Filter = pikepdf.Name.DCTDecode
        elif carrier in {"trailing", "expansion"}:
            image.write(
                zlib.compress(b"\xff\x00\x00" * (4 if carrier == "trailing" else 2000))
                + (_CANARY if carrier == "trailing" else b""),
                filter=pikepdf.Name.FlateDecode,
            )
        elif carrier == "depth":
            nested = pikepdf.Dictionary(Secret=pikepdf.String(_CANARY))
            for _ in range(70):
                nested = pikepdf.Dictionary(Nested=nested)
            document.Root.PrivateCarrier = nested
        else:
            image.F = pikepdf.String(_CANARY)
        content = _serialize(document)
    output = tmp_path / "candidate.pdf"
    with pytest.raises(PdfExportError) as caught:
        _sanitize(content, output)
    assert caught.value.code in {
        PdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.LIMIT_EXCEEDED,
    }
    assert not output.exists()
    assert _CANARY.decode("ascii") not in caplog.text + str(caught.value)


def _noto_program(*, custom_table: bool = False, unknown_family: bool = False) -> bytes:
    spec = importlib.util.find_spec("ocrmypdf")
    assert spec is not None and spec.origin is not None
    with TTFont(
        Path(spec.origin).parent / "data" / "NotoSans-Regular.ttf", recalcTimestamp=False
    ) as font:
        options = subset.Options()
        options.name_IDs = list(_NOTO_NAMES)
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text="SYNTHETIC")
        subsetter.subset(font)
        for tag in ("GDEF", "GPOS", "GSUB"):
            if tag in font:
                del font[tag]
        font["name"].setName(_CANARY.decode("ascii"), 256, 3, 1, 0x409)
        if unknown_family:
            font["name"].setName("Unknown-Synthetic-Family", 6, 3, 1, 0x409)
        if custom_table:
            custom = newTable("ZZZZ")
            custom.data = _CANARY
            font["ZZZZ"] = custom
        output = BytesIO()
        font.save(output)
        return output.getvalue()


def _ocr_source(program: bytes, *, registry: bytes = b"Adobe", cmap_extra: bytes = b"") -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name("/MPDFAA+NotoSans"),
            Flags=4,
            FontBBox=pikepdf.Array([0, 0, 1000, 1000]),
            ItalicAngle=0,
            Ascent=1069,
            Descent=-293,
            CapHeight=714,
            StemV=87,
            FontFile2=pikepdf.Stream(document, program, Length1=len(program)),
        )
        cid = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            BaseFont=pikepdf.Name("/MPDFAA+NotoSans"),
            CIDSystemInfo=pikepdf.Dictionary(
                Registry=pikepdf.String(registry), Ordering="UCS", Supplement=0
            ),
            FontDescriptor=descriptor,
            CIDToGIDMap=pikepdf.Name.Identity,
        )
        cmap = (
            b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n1 beginbfchar\n<0001> <0053>\nendbfchar\n"
            + cmap_extra
            + b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
        )
        font = pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name("/MPDFAA+NotoSans"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([cid]),
            ToUnicode=pikepdf.Stream(document, cmap),
        )
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.Contents = pikepdf.Stream(document, b"BT /F1 8 Tf 3 Tr 4 20 Td <0001> Tj ET")
        return _serialize(document)


def test_ocr_font_program_and_cmap_metadata_are_canonicalized(tmp_path: Path) -> None:
    content = _ocr_source(_noto_program(), cmap_extra=b"% " + _CANARY + b"\n")
    output = tmp_path / "candidate.pdf"
    assert _sanitize(content, output).after.empty
    assert _CANARY not in _all_bytes(output)
    with pikepdf.Pdf.open(output) as document:
        font = document.pages[0].Resources.Font.F1.DescendantFonts[0]
        with TTFont(BytesIO(font.FontDescriptor.FontFile2.read_bytes())) as result:
            assert set(item.nameID for item in result["name"].names) == set(_NOTO_NAMES)
            assert result["post"].formatType == 3.0
            assert result["name"].getDebugName(0) == _NOTO_NAMES[0]


@pytest.mark.parametrize("failure", ["custom", "family", "registry", "cmap"])
def test_unapproved_embedded_font_and_cmap_profiles_fail_closed(
    tmp_path: Path, failure: str
) -> None:
    content = _ocr_source(
        _noto_program(custom_table=failure == "custom", unknown_family=failure == "family"),
        registry=_CANARY if failure == "registry" else b"Adobe",
        cmap_extra=b"/Secret (" + _CANARY + b") def\n" if failure == "cmap" else b"",
    )
    with pytest.raises(PdfExportError) as caught:
        _sanitize(content, tmp_path / "candidate.pdf")
    assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
    assert not list(tmp_path.iterdir())


def test_font_parser_diagnostics_are_contained_and_logger_state_is_restored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _ocr_source(_noto_program())
    original = TTFont.save
    logger = logging.getLogger("fontTools.ttLib")
    previous = (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))

    def noisy_save(self: object, file: object, *args: object, **kwargs: object) -> object:
        logger.warning(_CANARY.decode("ascii"))
        return original(self, file, *args, **kwargs)

    monkeypatch.setattr(TTFont, "save", noisy_save)
    assert _sanitize(content, tmp_path / "candidate.pdf").after.empty
    assert _CANARY.decode("ascii") not in caplog.text
    assert (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers)) == previous


def test_signed_source_creates_only_unsigned_derivative(tmp_path: Path) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        signature = pikepdf.Dictionary(
            FT=pikepdf.Name.Sig,
            Type=pikepdf.Name.Sig,
            ByteRange=pikepdf.Array([0, 1, 2, 3]),
            Contents=pikepdf.String(_CANARY),
        )
        page.Annots = pikepdf.Array([signature])
        document.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([signature]))
        content = _serialize(document)
    output = tmp_path / "candidate.pdf"
    report = _sanitize(content, output)
    assert report.before.signatures > 0
    assert report.unsigned_derivative
    assert report.after.empty
    assert _CANARY not in _all_bytes(output)


@pytest.mark.parametrize("field", ["cmap_name", "cmap_inheritance", "encoding", "cidmap"])
def test_font_identity_extensions_are_normalized_or_rejected(tmp_path: Path, field: str) -> None:
    content = _ocr_source(_noto_program())
    with pikepdf.Pdf.open(BytesIO(content)) as document:
        font = document.pages[0].Resources.Font.F1
        if field == "cmap_name":
            font.ToUnicode.CMapName = pikepdf.Name("/" + _CANARY.decode("ascii"))
        elif field == "cmap_inheritance":
            font.ToUnicode.UseCMap = pikepdf.Name("/" + _CANARY.decode("ascii"))
        elif field == "encoding":
            font.Encoding = pikepdf.Name("/" + _CANARY.decode("ascii"))
        else:
            font.DescendantFonts[0].CIDToGIDMap = pikepdf.Name("/" + _CANARY.decode("ascii"))
        content = _serialize(document)
    output = tmp_path / "candidate.pdf"
    if field == "cmap_name":
        assert _sanitize(content, output).after.empty
        assert _CANARY not in _all_bytes(output)
    else:
        with pytest.raises(PdfExportError) as caught:
            _sanitize(content, output)
        assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
        assert not output.exists()


def _content_pdf(data: bytes, *, form: bool, compressed: bool) -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )
        stream = pikepdf.Stream(document, zlib.compress(data) if compressed else data)
        if compressed:
            stream.Filter = pikepdf.Name.FlateDecode
        if form:
            stream.Type = pikepdf.Name.XObject
            stream.Subtype = pikepdf.Name.Form
            stream.BBox = pikepdf.Array([0, 0, 72, 48])
            stream.Resources = resources
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=stream))
            page.Contents = pikepdf.Stream(document, b"/Fm0 Do")
        else:
            page.Resources = resources
            page.Contents = stream
        return _serialize(document)


def _inline_jpeg() -> bytes:
    output = BytesIO()
    with Image.new("RGB", (1, 1), (255, 0, 0)) as image:
        image.save(output, format="JPEG", comment=_CANARY)
    assert _CANARY in output.getvalue()
    return b"q BI /W 1 /H 1 /BPC 8 /CS /RGB /F /DCT ID " + output.getvalue() + b" EI Q"


@pytest.mark.parametrize("form", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize(
    "carrier",
    [
        "inline",
        "actualtext",
        "properties",
        "marked",
        "point",
        "unknown",
        "extra",
        "dangling",
        "wrong_type",
        "nested_array",
        "outside_text",
        "unfinished_text",
    ],
)
def test_content_and_form_payload_carriers_fail_closed(
    tmp_path: Path, form: bool, compressed: bool, carrier: str, caplog: pytest.LogCaptureFixture
) -> None:
    cases = {
        "inline": _inline_jpeg(),
        "actualtext": b"/Span << /ActualText (" + _CANARY + b") >> BDC EMC",
        "properties": b"/Span /PrivateProperties BDC EMC",
        "marked": b"/Artifact BMC EMC",
        "point": b"/Span << /ActualText (" + _CANARY + b") >> DP",
        "unknown": b"(" + _CANARY + b") PrivateUnknownOperator",
        "extra": b"(" + _CANARY + b") q Q",
        "dangling": b"(" + _CANARY + b")",
        "wrong_type": b"1 Do",
        "nested_array": b"[[(SYNTHETIC)]] TJ",
        "outside_text": b"(" + _CANARY + b") Tj",
        "unfinished_text": b"BT /F1 8 Tf (" + _CANARY + b") Tj",
    }
    content = _content_pdf(cases[carrier], form=form, compressed=compressed)
    source = BytesIO(content)
    source.seek(7)
    request = RedactionSanitizationRequest(
        uuid4(), uuid4(), hashlib.sha256(content).hexdigest(), len(content), 1
    )
    output = tmp_path / "candidate.pdf"
    with pytest.raises(PdfExportError) as caught:
        PikepdfRedactionSanitizer().sanitize(
            source, output, tmp_path, request, _limits(), _Control()
        )
    assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
    assert source.getvalue() == content and source.tell() == 7
    assert not output.exists()
    assert _CANARY.decode("ascii") not in caplog.text + str(caught.value)


@pytest.mark.parametrize("form", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_content_tokenization_removes_comments_preserves_literal_text_and_rendering(
    tmp_path: Path, form: bool, compressed: bool
) -> None:
    drawing = (
        b"% " + _CANARY + b"\nq\n0.5 w [] 0 d 0 0 1 RG 1 1 70 46 re S\n"
        b"BT /F1 5 Tf 1 0 0 1 2 20 Tm (KEEP % literal \\(parentheses\\)) Tj "
        b"[(KEEP ) -10 <54455854>] TJ ET Q % " + _CANARY
    )
    content = _content_pdf(drawing, form=form, compressed=compressed)
    output = tmp_path / "candidate.pdf"
    assert _sanitize(content, output).after.empty
    assert _CANARY not in _all_bytes(output)
    with pikepdf.Pdf.open(BytesIO(content)) as original, pikepdf.Pdf.open(output) as sanitized:
        left = original.pages[0].Resources.XObject.Fm0 if form else original.pages[0].Contents
        right = sanitized.pages[0].Resources.XObject.Fm0 if form else sanitized.pages[0].Contents
        assert pikepdf.unparse_content_stream(
            pikepdf.parse_content_stream(left)
        ) == pikepdf.unparse_content_stream(pikepdf.parse_content_stream(right))
        assert b"KEEP % literal" in right.read_bytes()
    request = PdfPageRenderRequest(0, 144, 96)
    renderer = PdfiumPageRenderer()
    expected = renderer.render(
        BytesIO(content), _Sink(), request, _render_limits(_limits()), _Control()
    )
    with output.open("rb") as source:
        actual = renderer.render(source, _Sink(), request, _render_limits(_limits()), _Control())
    assert actual.pixel_sha256 == expected.pixel_sha256
