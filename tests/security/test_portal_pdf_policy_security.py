"""Bounded, sanitized rejection of hostile portal-output feature graphs."""

from __future__ import annotations

from io import BytesIO

import pikepdf
import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.pdf.portal_policy import require_portal_output, require_portal_source_version
from tests.unit.test_portal_pdf_policy import _payload


@pytest.mark.parametrize(
    "marker",
    (
        "metadata",
        "annotation",
        "script",
        "action",
        "attachment",
        "form",
        "optional_content",
        "piece_info",
        "last_modified",
    ),
)
def test_prohibited_features_never_expose_values(marker: str) -> None:
    with pikepdf.Pdf.open(BytesIO(_payload(marker))) as document:
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert "SYNTHETIC" not in str(caught.value) + repr(caught.value)
    assert "invalid.example" not in str(caught.value) + repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("key", ("/F", "/FFilter", "/FDecodeParms", "/Ref"))
def test_external_stream_references_are_blocked(key: str) -> None:
    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        stream = document.make_stream(b"SYNTHETIC STREAM")
        stream[key] = pikepdf.String("SYNTHETIC EXTERNAL PATH")
        document.pages[0].Resources.External = stream
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
    assert caught.value.context == "portal.output_external_stream"


def test_malformed_feature_is_blocked_without_treating_unknown_as_absent() -> None:
    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        document.pages[0].Resources.PWSPolicy.Fields = pikepdf.String("SYNTHETIC BAD FIELDS")
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
    assert caught.value.context == "portal.output_features"


def test_graph_depth_and_object_count_limits_fail_closed() -> None:
    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=1, checkpoint=lambda: None)
        assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
        parent = document.pages[0].Resources
        for _ in range(70):
            child = pikepdf.Dictionary()
            parent.Nested = child
            parent = parent.Nested
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
        assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
        assert caught.value.__context__ is None


@pytest.mark.parametrize("code", (PdfExportErrorCode.CANCELLED, PdfExportErrorCode.TIMED_OUT))
def test_graph_walk_cooperates_with_cancellation_and_deadline(code: PdfExportErrorCode) -> None:
    count = 0

    def checkpoint() -> None:
        nonlocal count
        count += 1
        if count == 8:
            raise PdfExportError(code, "portal.test_checkpoint")

    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=checkpoint)
    assert caught.value.code is code
    assert count == 8


@pytest.mark.parametrize("declared", ("/2.0", "/9.9", "malformed"))
def test_catalog_version_cannot_bypass_source_or_output_ceiling(declared: str) -> None:
    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        document.Root.Version = (
            pikepdf.Name(declared) if declared.startswith("/") else pikepdf.String(declared)
        )
        with pytest.raises(PdfExportError) as caught:
            require_portal_source_version(document)
        assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
        with pytest.raises(PdfExportError) as caught:
            require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
        assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED


def test_indirect_cycles_are_bounded_and_not_a_false_feature() -> None:
    with pikepdf.Pdf.open(BytesIO(_payload())) as document:
        cycle = document.make_indirect(pikepdf.Dictionary())
        cycle.Self = cycle
        document.pages[0].Resources.PWSPolicy = cycle
        require_portal_output(document, maximum_objects=100_000, checkpoint=lambda: None)
