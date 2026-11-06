"""Complete reference resource integrity, including nonpainted payloads.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from paperworkshell.pdf.redaction_verification import verify_redaction
from paperworkshell.pdf.redaction_verification_resources import verify_resource_identity
from tests.security.test_redaction_sanitization import _noto_program, _ocr_source, _sanitize
from tests.security.test_redaction_source_recovery import _decode_pixel_line
from tests.security.test_redaction_verification_corpus import (
    _CASES,
    _limits,
    _prepare,
    _verification_request,
)
from tests.unit.test_redaction_sanitization_adapter import _Control


def _save(document: pikepdf.Pdf) -> bytes:
    output = BytesIO()
    document.save(
        output, deterministic_id=True, object_stream_mode=pikepdf.ObjectStreamMode.disable
    )
    return output.getvalue()


@pytest.mark.parametrize("resource_name", ["/UnusedOriginal", "/A", "/AF", "/V"])
def test_unused_image_with_modified_noncanary_pixel_cannot_hide_in_resources(
    tmp_path: Path, resource_name: str
) -> None:
    case = next(case for case in _CASES if case.fixture_id == "pdf-redaction-embedded-image")
    prepared = _prepare(case, tmp_path)
    with (
        pikepdf.Pdf.open(BytesIO(prepared.source)) as original,
        pikepdf.Pdf.open(BytesIO(prepared.candidate)) as document,
    ):
        source_image = original.pages[0].Resources.XObject.Im0
        pixels = bytearray(source_image.read_bytes())
        pixels[-1] ^= 1
        changed = bytes(pixels)
        assert hashlib.sha256(changed).hexdigest() not in prepared.forbidden_streams, (
            "RESOURCE_CONTROL_FINGERPRINT_UNCHANGED"
        )
        with Image.frombytes("L", (1024, 320), changed) as decoded:
            assert _decode_pixel_line(decoded, 48) == case.canaries[0].token, (
                "RESOURCE_CONTROL_NOT_RECOVERABLE"
            )
        carrier = pikepdf.Stream(
            document,
            changed,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=1024,
            Height=320,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
        )
        document.pages[0].Resources.XObject[resource_name] = carrier
        altered = _save(document)
    tokens = tuple(canary.token for canary in case.canaries)
    result = verify_redaction(
        BytesIO(altered),
        BytesIO(prepared.reference),
        _verification_request(
            altered, prepared.reference, (0,), 1, tokens, prepared.forbidden_streams
        ),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=prepared.forbidden_streams,
    )
    assert not result.automated_passed, "RESOURCE_UNUSED_IMAGE_ACCEPTED"
    assert not result.release_ready
    assert "RV_ORIGINAL_OBJECT_RETAINED" in str(result.to_record()), (
        "RESOURCE_REFERENCE_CHECK_NOT_RUN"
    )
    assert not list(tmp_path.iterdir()), "RESOURCE_PRIVATE_TEMP_RETAINED"


def _font_candidate(tmp_path: Path) -> bytes:
    output = tmp_path / "font.pdf"
    _sanitize(_ocr_source(_noto_program()), output)
    data = output.read_bytes()
    output.unlink()
    return data


@pytest.mark.parametrize("kind", ["binary_font", "extra_mask", "unused_font"])
def test_every_binary_resource_is_bound_even_when_rendering_cannot_observe_it(
    tmp_path: Path, kind: str
) -> None:
    reference = _font_candidate(tmp_path)
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        font = document.pages[0].Resources.Font.F1
        if kind == "unused_font":
            document.pages[0].Resources.Font.Extra = pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
            )
        elif kind == "binary_font":
            program = font.DescendantFonts[0].FontDescriptor.FontFile2
            data = bytearray(program.read_bytes())
            data[-1] ^= 1
            program.write(bytes(data))
        else:
            font.DescendantFonts[0].FontDescriptor.CIDSet = pikepdf.Stream(document, b"\xff\x01")
        altered = _save(document)
    result = verify_resource_identity(BytesIO(altered), BytesIO(reference), _limits(), _Control())
    assert not result.passed, "RESOURCE_UNPAINTED_BINARY_ACCEPTED"
    assert result.code in {"RV_ORIGINAL_OBJECT_RETAINED", "RV_STRUCTURE_UNSUPPORTED"}


def test_supported_native_font_resource_identity_is_stable(tmp_path: Path) -> None:
    content = _font_candidate(tmp_path)
    candidate, reference = BytesIO(content), BytesIO(content)
    candidate.seek(7)
    reference.seek(9)
    result = verify_resource_identity(candidate, reference, _limits(), _Control())
    assert result.passed, result.code
    assert candidate.tell() == 7 and reference.tell() == 9


@pytest.mark.parametrize(
    "kind",
    [
        "font_header",
        "font_checksum",
        "font_name",
        "length1",
        "resource_metadata",
        "catalog_resources",
        "page_tree_resources",
        "content_resources",
    ],
)
def test_normalization_never_ignores_new_candidate_payload_fields(
    tmp_path: Path, kind: str
) -> None:
    reference = _font_candidate(tmp_path)
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        page = document.pages[0]
        font = page.Resources.Font.F1
        program = font.DescendantFonts[0].FontDescriptor.FontFile2
        if kind in {"font_header", "font_checksum"}:
            data = bytearray(program.read_bytes())
            data[6 if kind == "font_header" else 16] ^= 1
            program.write(bytes(data))
        elif kind == "font_name":
            font.BaseFont = pikepdf.Name("/AAAAAA+NotoSans")
        elif kind == "length1":
            program.Length1 = int(program.Length1) + 1
        elif kind == "resource_metadata":
            font.ID = pikepdf.String("SYNTHETIC UNBOUND RESOURCE VALUE")
        elif kind == "catalog_resources":
            document.Root.Resources = page.Resources
        elif kind == "page_tree_resources":
            document.Root.Pages.Resources = page.Resources
        else:
            page.Contents.Font = page.Resources.Font
        altered = _save(document)
    result = verify_resource_identity(BytesIO(altered), BytesIO(reference), _limits(), _Control())
    assert not result.passed, "RESOURCE_UNBOUND_NORMALIZATION_ACCEPTED"
    assert result.code in {
        "RV_ORIGINAL_OBJECT_RETAINED",
        "RV_STRUCTURE_UNSUPPORTED",
        "RV_FEATURE_RETAINED",
    }


@pytest.mark.parametrize("failure", ["memory", "objects", "cancel", "timeout"])
def test_resource_comparison_limits_fail_closed(tmp_path: Path, failure: str) -> None:
    content = _font_candidate(tmp_path)
    limits, control = _limits(), _Control()
    if failure == "memory":
        limits = replace(limits, maximum_memory_bytes=16)
    elif failure == "objects":
        limits = replace(limits, maximum_pdf_objects=1)
    elif failure == "cancel":
        control = _Control(cancel_at=1)
    else:
        control = _Control(expired=True)
    result = verify_resource_identity(BytesIO(content), BytesIO(content), limits, control)
    assert not result.passed
    assert result.code in {"RV_CANCELLED", "RV_TIMED_OUT", "RV_LIMIT_EXCEEDED"}


@pytest.mark.parametrize("change", ["mapping", "extra_program", "name", "duplicate"])
def test_unicode_mapping_grammar_cannot_carry_unbound_data(tmp_path: Path, change: str) -> None:
    reference = _font_candidate(tmp_path)
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        cmap = document.pages[0].Resources.Font.F1.ToUnicode
        data = cmap.read_bytes()
        if change == "mapping":
            data = data.replace(b"<0053>", b"<0041>")
        elif change == "extra_program":
            data = data.replace(b"endcmap", b"/Extra (SYNTHETIC UNBOUND VALUE) def endcmap")
        elif change == "name":
            data = data.replace(b"/Adobe-Identity-UCS", b"/Arbitrary-Source-Value")
        else:
            data = data.replace(b"endcmap", b"1 beginbfchar <0001> <0053> endbfchar endcmap")
        cmap.write(data)
        altered = _save(document)
    result = verify_resource_identity(BytesIO(altered), BytesIO(reference), _limits(), _Control())
    assert not result.passed, "RESOURCE_UNBOUND_CMAP_ACCEPTED"
