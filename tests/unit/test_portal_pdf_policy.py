"""PWS-0506 native portal policy, all assembly paths and retained transforms."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest

from paperworkshell.domain.pdf_export import (
    PORTAL_PDF_EXPORT_PROFILE,
    PORTAL_PDF_EXPORT_VERSION,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportVerificationReport,
)
from paperworkshell.domain.searchable_pdf_export import SearchablePdfExportPlan
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapter
from paperworkshell.pdf.portal_policy import require_portal_output, require_portal_source_version
from tests.unit import test_flattened_export_adapter as flattened
from tests.unit import test_pdf_export_adapter as basic
from tests.unit import test_searchable_export_adapter as searchable


def _portal_plan(plan: PdfExportPlan) -> PdfExportPlan:
    return PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=plan.sources,
        pages=plan.pages,
        profile=PORTAL_PDF_EXPORT_PROFILE,
        plan_version=PORTAL_PDF_EXPORT_VERSION,
    )


def _payload(marker: str | None = None, *, version: str = "1.7") -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(flattened._TEXT_SOURCE) as document:
        # The legacy source contains an Info dictionary. Construct a clean
        # derivative fixture before adding exactly the feature under test.
        if "/Info" in document.trailer:
            del document.trailer["/Info"]
        if "/Metadata" in document.Root:
            del document.Root["/Metadata"]
        page = document.pages[0].obj
        nested = pikepdf.Dictionary()
        page.Resources.PWSPolicy = nested
        if marker == "metadata":
            stream = document.make_stream(b"SYNTHETIC PRIVATE METADATA")
            stream.Type = pikepdf.Name.Metadata
            nested.Metadata = stream
        elif marker == "annotation":
            page.Annots = pikepdf.Array(
                [pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text)]
            )
        elif marker == "script":
            nested.JS = pikepdf.String("SYNTHETIC SCRIPT")
        elif marker == "action":
            nested.A = pikepdf.Dictionary(S=pikepdf.Name.URI, URI="https://invalid.example")
        elif marker == "attachment":
            nested.AF = pikepdf.Array(
                [pikepdf.Dictionary(Type=pikepdf.Name.Filespec, EF=pikepdf.Dictionary())]
            )
        elif marker == "form":
            nested.FT = pikepdf.Name.Tx
        elif marker == "piece_info":
            nested.PieceInfo = pikepdf.Dictionary()
        elif marker == "last_modified":
            nested.LastModified = pikepdf.String("SYNTHETIC DATE")
        elif marker == "optional_content":
            nested.OC = pikepdf.Dictionary(Type=pikepdf.Name.OCG)
        elif marker == "extensions":
            document.Root.Extensions = pikepdf.Dictionary()
        elif marker == "ordinary_metadata":
            document.docinfo.Title = "SYNTHETIC TITLE"
            page.Metadata = document.make_stream(b"SYNTHETIC PAGE METADATA")
            page.PieceInfo = pikepdf.Dictionary()
            page.LastModified = pikepdf.String("SYNTHETIC DATE")
        elif marker is not None:
            raise AssertionError(marker)
        document.save(output, min_version=version, deterministic_id=True)
    return output.getvalue()


def _run_pipeline(tmp_path: Path, pipeline: str, payload: bytes) -> PdfExportVerificationReport:
    source = tmp_path / "source.pdf"
    source.write_bytes(payload)
    if pipeline == "basic":
        plan, paths = basic._single_plan(source)
        return PdfExportAdapter(qpdf_checker=basic._CleanChecker()).assemble_and_verify(
            _portal_plan(plan), paths, tmp_path / "output.pdf", basic._limits(), basic._control()
        )
    if pipeline == "flattened":
        flattened_plan, _ = flattened._text_plan()
        flattened_plan = flattened._plan_with_source_payload(flattened_plan, payload)
        portal_plan = FlattenedPdfExportPlan(
            base_plan=_portal_plan(flattened_plan.base_plan),
            pages=flattened_plan.pages,
            assets=flattened_plan.assets,
        )
        report, _, _ = flattened._run(portal_plan, payload, tmp_path / "output.pdf")
        assert isinstance(report.base_report, PdfExportVerificationReport)
        return report.base_report
    assert pipeline == "searchable"
    searchable_plan, paths = searchable._plan(source, (0,))
    portal_searchable = SearchablePdfExportPlan(
        case_id=searchable_plan.case_id,
        manifest_revision=searchable_plan.manifest_revision,
        manifest_sha256=searchable_plan.manifest_sha256,
        manifest_cas_token=searchable_plan.manifest_cas_token,
        sources=searchable_plan.sources,
        pages=searchable_plan.pages,
        profile=replace(searchable_plan.profile, pdf_profile=PORTAL_PDF_EXPORT_PROFILE),
    )
    _, report, _ = searchable._run(tmp_path, portal_searchable, paths, searchable._NeverOcr())
    assert isinstance(report.base_report, PdfExportVerificationReport)
    return report.base_report


@pytest.mark.parametrize("pipeline", ("basic", "flattened", "searchable"))
@pytest.mark.parametrize("metadata", (None, "ordinary_metadata"))
def test_portal_pipeline_reopens_renders_and_preserves_original(
    tmp_path: Path, pipeline: str, metadata: str | None
) -> None:
    payload = _payload(metadata)
    report = _run_pipeline(tmp_path, pipeline, payload)
    assert report.portal_profile_verified
    assert (
        report.output_sha256 == hashlib.sha256((tmp_path / "output.pdf").read_bytes()).hexdigest()
    )
    assert (tmp_path / "source.pdf").read_bytes() == payload
    with pikepdf.Pdf.open(tmp_path / "output.pdf", attempt_recovery=False) as output:
        assert output.pdf_version == "1.7"
        assert "/Info" not in output.trailer
        assert "/Metadata" not in output.pages[0].obj
        require_portal_output(output, maximum_objects=100_000, checkpoint=lambda: None)


@pytest.mark.parametrize("pipeline", ("basic", "flattened", "searchable"))
@pytest.mark.parametrize(
    "marker",
    (
        "metadata",
        "annotation",
        "script",
        "action",
        "attachment",
        "form",
        "piece_info",
        "last_modified",
    ),
)
def test_every_portal_pipeline_rejects_retained_features_and_removes_candidate(
    tmp_path: Path, pipeline: str, marker: str
) -> None:
    payload = _payload(marker)
    with pytest.raises((PdfExportError, FlattenedPdfExportError)) as caught:
        _run_pipeline(tmp_path, pipeline, payload)
    assert isinstance(caught.value, (PdfExportError, FlattenedPdfExportError))
    assert caught.value.code in {
        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
        FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
    }
    assert not (tmp_path / "output.pdf").exists()
    assert (tmp_path / "source.pdf").read_bytes() == payload


@pytest.mark.parametrize("pipeline", ("basic", "flattened", "searchable"))
@pytest.mark.parametrize("unsupported", ("2.0", "extensions"))
def test_portal_rejects_future_sources_before_creating_candidate(
    tmp_path: Path, pipeline: str, unsupported: str
) -> None:
    payload = _payload("extensions") if unsupported == "extensions" else _payload(version="2.0")
    with pytest.raises((PdfExportError, FlattenedPdfExportError)) as caught:
        _run_pipeline(tmp_path, pipeline, payload)
    assert isinstance(caught.value, (PdfExportError, FlattenedPdfExportError))
    assert caught.value.code in {
        PdfExportErrorCode.SOURCE_UNSUPPORTED,
        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
    }
    assert not (tmp_path / "output.pdf").exists()
    assert (tmp_path / "source.pdf").read_bytes() == payload


@pytest.mark.parametrize("version", ("1.0", "1.3", "1.5", "1.6", "1.7"))
def test_supported_source_version_is_not_downgraded(version: str) -> None:
    with pikepdf.Pdf.open(BytesIO(_payload(version=version))) as document:
        require_portal_source_version(document)


@pytest.mark.parametrize("version", ("1.5", "1.6", "1.7"))
def test_supported_output_versions(version: str) -> None:
    with pikepdf.Pdf.open(BytesIO(_payload(version=version))) as document:
        require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
