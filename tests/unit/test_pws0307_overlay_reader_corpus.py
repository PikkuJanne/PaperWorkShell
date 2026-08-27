"""Focused production-corpus evidence for PWS-0307 reader compatibility."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pikepdf
import pytest

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportRotation
from paperworkshell.overlays.models import OverlayKind, SignatureImageOverlayPayload
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE, QPDF_VERSION
from tools.pws0307_overlay_reader_corpus import (
    ReaderCorpusError,
    build_reader_corpus_plan,
    generate_reader_corpus,
)
from tools.pws0307_reader_test import APPROVED_READER_ARTIFACT


def _overlay_form(page: pikepdf.Page) -> pikepdf.Stream:
    resources = cast(Any, page.obj.Resources)
    xobjects = cast(Any, resources.XObject)
    names = [name for name in xobjects.keys() if str(name).startswith("/PWSOverlay")]
    assert len(names) == 1
    form = xobjects[names[0]]
    assert isinstance(form, pikepdf.Stream)
    return form


def test_corpus_plan_has_global_id_namespace_and_exact_semantic_coverage() -> None:
    plan = build_reader_corpus_plan()

    assert plan.page_count == 5
    assert plan.visible_overlay_count == 8
    assert plan.text_overlay_count == 3
    assert plan.placed_signature_image_count == 1
    assert tuple(record.kind for page in plan.pages for record in page.overlays) == (
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.INITIALS,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.SHAPE,
        OverlayKind.SIGNATURE_IMAGE,
        OverlayKind.HIGHLIGHT,
    )
    assert tuple(page.base_page.source_page_index for page in plan.pages) == (0, 0, 1, 4, 5)
    assert tuple(page.base_page.additional_rotation for page in plan.pages) == (
        PdfExportRotation.DEGREES_0,
        PdfExportRotation.DEGREES_90,
        PdfExportRotation.DEGREES_0,
        PdfExportRotation.DEGREES_270,
        PdfExportRotation.DEGREES_180,
    )
    assert plan.pages[0].active_crop == NormalizedPageBox.full_page()
    assert all(page.active_crop != NormalizedPageBox.full_page() for page in plan.pages[1:])
    signature_payload = plan.pages[3].overlays[0].payload
    assert type(signature_payload) is SignatureImageOverlayPayload
    assert signature_payload.asset_id == plan.assets[0].asset_id

    identities = (
        plan.base_plan.case_id,
        *(source.source_id for source in plan.base_plan.sources),
        *(page.page_id for page in plan.base_plan.pages),
        *(record.overlay_id for page in plan.pages for record in page.overlays),
        *(asset.asset_id for asset in plan.assets),
    )
    assert len(set(identities)) == len(identities)


def test_generator_is_byte_deterministic_and_records_actual_production_checks(
    tmp_path: Path,
) -> None:
    assert DEFAULT_QPDF_EXECUTABLE.is_file()
    first_pdf = tmp_path / "reader-corpus-first.pdf"
    first_json = tmp_path / "reader-corpus-first.json"
    second_pdf = tmp_path / "reader-corpus-second.pdf"
    second_json = tmp_path / "reader-corpus-second.json"

    first_summary = generate_reader_corpus(first_pdf, first_json)
    second_summary = generate_reader_corpus(second_pdf, second_json)

    assert first_pdf.read_bytes() == second_pdf.read_bytes()
    assert first_json.read_bytes() == second_json.read_bytes()
    assert len(first_pdf.read_bytes()) == APPROVED_READER_ARTIFACT.pdf_byte_size
    assert hashlib.sha256(first_pdf.read_bytes()).hexdigest() == APPROVED_READER_ARTIFACT.pdf_sha256
    assert len(first_json.read_bytes()) == APPROVED_READER_ARTIFACT.evidence_byte_size
    assert (
        hashlib.sha256(first_json.read_bytes()).hexdigest()
        == APPROVED_READER_ARTIFACT.evidence_sha256
    )
    assert first_summary == second_summary == json.loads(first_json.read_text(encoding="utf-8"))
    artifact = cast(dict[str, object], first_summary["artifact"])
    assert artifact == {
        "sha256": hashlib.sha256(first_pdf.read_bytes()).hexdigest(),
        "byte_size": first_pdf.stat().st_size,
        "page_count": 5,
        "semantic_fingerprint": build_reader_corpus_plan().semantic_fingerprint,
    }
    automated = cast(dict[str, object], first_summary["automated_checks"])
    qpdf = cast(dict[str, object], automated["qpdf"])
    assert qpdf == {
        "profile": "check_no_recovery_no_warnings_v1",
        "version": QPDF_VERSION,
        "result": "pass",
    }
    assert automated["strict_reopen"] == "pass"
    assert automated["all_pages_pdfium_rendered"] == "pass"
    assert automated["overlay_form_presence"] == "pass"
    assert automated["noninteractive_output_graph"] == "pass"

    with pikepdf.Pdf.open(first_pdf, attempt_recovery=False) as document:
        assert len(document.pages) == 5
        assert [int(page.obj.get("/Rotate", 0)) for page in document.pages] == [
            0,
            90,
            90,
            270,
            180,
        ]
        assert [float(page.obj.get("/UserUnit", 1)) for page in document.pages] == [
            1.0,
            1.0,
            1.0,
            1.0,
            2.0,
        ]
        assert pikepdf.Name("/AcroForm") not in document.Root
        forms = tuple(_overlay_form(page) for page in document.pages)
        assert all(str(form.Subtype) == "/Form" for form in forms)
        fonts = cast(Any, forms[0].Resources.Font)
        assert len(fonts) == 1
        font = fonts[next(iter(fonts.keys()))]
        assert str(font.Subtype) == "/Type0"
        assert pikepdf.Name("/FontFile2") in font.DescendantFonts[0].FontDescriptor
        signature_images = cast(Any, forms[3].Resources.XObject)
        assert len(signature_images) == 1
        image = signature_images[next(iter(signature_images.keys()))]
        assert str(image.Subtype) == "/Image"


def test_evidence_is_content_free_and_scopes_manual_and_conformance_claims(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reader-corpus.pdf"
    evidence_path = tmp_path / "reader-corpus.json"

    summary = generate_reader_corpus(output, evidence_path)
    encoded = evidence_path.read_text(encoding="utf-8")

    assert "Reader matrix text" not in encoded
    assert "2026-08-25" not in encoded
    assert "born-digital.pdf" not in encoded
    assert "render-geometry-matrix.pdf" not in encoded
    assert str(tmp_path) not in encoded
    assert re.search(r"[A-Za-z]:\\", encoded) is None
    assert (
        re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            encoded,
        )
        is None
    )
    manual = cast(dict[str, object], summary["manual_reader_matrix"])
    assert manual["result"] == "not_run"
    boundaries = cast(dict[str, object], summary["claim_boundaries"])
    assert boundaries == {
        "universal_reader_compatibility": False,
        "pdf_ua_conformance": False,
        "portal_acceptance": False,
        "digital_signature_creation": False,
    }


def test_generator_never_replaces_an_existing_destination(tmp_path: Path) -> None:
    output = tmp_path / "existing.pdf"
    evidence = tmp_path / "unused.json"
    output.write_bytes(b"existing synthetic sentinel")

    with pytest.raises(ReaderCorpusError) as raised:
        generate_reader_corpus(output, evidence)

    assert raised.value.code == "PWS0307_CORPUS_DESTINATION_EXISTS"
    assert output.read_bytes() == b"existing synthetic sentinel"
    assert not evidence.exists()
    assert str(output) not in str(raised.value)
