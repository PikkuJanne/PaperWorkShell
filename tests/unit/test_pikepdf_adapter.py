"""PWS-0105/PWS-0501 contract tests for the bounded pikepdf adapter."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

import pikepdf
import pytest

from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    encode_pdf_inspection_report,
)
from paperworkshell.pdf.pikepdf_adapter import PikePdfAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURES = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf"


def _limits(**overrides: int) -> PdfInspectionLimits:
    values = {
        "maximum_pages": 100,
        "maximum_objects": 10_000,
        "maximum_input_bytes": 10_000_000,
        "maximum_temporary_bytes": 10_000_000,
        "maximum_output_bytes": 4_096,
        "maximum_tool_output_bytes": 1_000_000,
    }
    values.update(overrides)
    return PdfInspectionLimits(**values)


def _inspect(
    path: Path,
    *,
    adapter: PikePdfAdapter | None = None,
    limits: PdfInspectionLimits | None = None,
    deadline: float | None = None,
    cancellation_probe: Callable[[], bool] | None = None,
) -> PdfInspectionResult:
    selected_adapter = adapter if adapter is not None else PikePdfAdapter()
    selected_limits = limits if limits is not None else _limits()
    selected_deadline = deadline if deadline is not None else time.monotonic() + 30.0
    selected_probe = cancellation_probe if cancellation_probe is not None else lambda: False
    with path.open("rb") as stream:
        return selected_adapter.inspect(
            stream,
            source_bytes=path.stat().st_size,
            limits=selected_limits,
            deadline=selected_deadline,
            cancellation_probe=selected_probe,
        )


def _feature_values(summary: PdfFeatureSummary) -> tuple[PdfFeatureState, ...]:
    return summary.values[:6]


def test_complete_synthetic_corpus_has_closed_distinct_outcomes() -> None:
    paths = sorted(PDF_FIXTURES.glob("*.pdf"))
    names = {path.name for path in paths}
    unicode_names = {name for name in names if name.startswith("unicode-named-")}
    expected_names = {
        "action-marker.pdf",
        "annotation.pdf",
        "attachment.pdf",
        "blank.pdf",
        "born-digital.pdf",
        "duplicate-a.pdf",
        "duplicate-b.pdf",
        "image-only.pdf",
        "incremental-update.pdf",
        "malformed-truncated.pdf",
        "mixed.pdf",
        "multilingual-tounicode.pdf",
        "ocr-raster-text.pdf",
        "ocr-readable-mixed.pdf",
        "page-geometry.pdf",
        "password-required.pdf",
        "render-calibration.pdf",
        "render-cmyk.pdf",
        "render-colorspaces.pdf",
        "render-annotation-appearance.pdf",
        "render-embedded-type3-font.pdf",
        "render-geometry-matrix.pdf",
        "render-high-dimension.pdf",
        "render-malformed-page-tree.pdf",
        "render-missing-font-fallback.pdf",
        "render-soft-mask-blend.pdf",
        "render-transparency.pdf",
        "script-marker.pdf",
        "signed-marker.pdf",
    }
    assert len(unicode_names) == 1
    absent = PdfFeatureState.NOT_DETECTED
    detected = PdfFeatureState.DETECTED
    ordinary = (absent,) * 6
    interactive = (absent, absent, detected, absent, absent, absent)
    attached = (absent, detected, absent, absent, absent, absent)
    redaction_expected: dict[
        str, tuple[int | None, tuple[PdfFeatureState, ...], PdfFeatureState]
    ] = {
        "redaction-born-digital.pdf": (1, ordinary, absent),
        "redaction-scan.pdf": (1, ordinary, absent),
        "redaction-embedded-image.pdf": (1, ordinary, absent),
        "redaction-annotation.pdf": (1, interactive, detected),
        "redaction-form.pdf": (1, interactive, detected),
        "redaction-metadata.pdf": (1, ordinary, absent),
        "redaction-attachment.pdf": (1, attached, absent),
        "redaction-hidden-layer.pdf": (1, ordinary, absent),
        "redaction-incremental.pdf": (1, ordinary, absent),
        "redaction-geometry.pdf": (4, ordinary, absent),
        "redaction-hidden-ocr.pdf": (1, ordinary, absent),
        "redaction-malformed.pdf": (None, (PdfFeatureState.UNKNOWN,) * 6, PdfFeatureState.UNKNOWN),
        "redaction-visual-cover.pdf": (1, ordinary, absent),
    }
    assert names == expected_names | unicode_names | redaction_expected.keys()

    ordinary_names = {
        "blank.pdf",
        "born-digital.pdf",
        "duplicate-a.pdf",
        "duplicate-b.pdf",
        "image-only.pdf",
        "incremental-update.pdf",
        "mixed.pdf",
        "multilingual-tounicode.pdf",
        "ocr-raster-text.pdf",
        "ocr-readable-mixed.pdf",
        "page-geometry.pdf",
        "render-calibration.pdf",
        "render-cmyk.pdf",
        "render-colorspaces.pdf",
        "render-embedded-type3-font.pdf",
        "render-geometry-matrix.pdf",
        "render-high-dimension.pdf",
        "render-missing-font-fallback.pdf",
        "render-soft-mask-blend.pdf",
        "render-transparency.pdf",
        *unicode_names,
    }
    not_detected = (PdfFeatureState.NOT_DETECTED,) * 6

    for path in paths:
        result = _inspect(path)
        assert result.report_version == 2
        if path.name in {
            "malformed-truncated.pdf",
            "render-malformed-page-tree.pdf",
            "redaction-malformed.pdf",
        }:
            assert result.open_state is PdfOpenState.MALFORMED
            assert result.encryption_state is PdfEncryptionState.UNKNOWN
            assert result.structural_state is PdfStructuralState.FAILED
            assert result.features == PdfFeatureSummary.unknown()
            continue
        if path.name == "password-required.pdf":
            assert result.open_state is PdfOpenState.ENCRYPTED
            assert result.encryption_state is PdfEncryptionState.ENCRYPTED
            assert result.structural_state is PdfStructuralState.FAILED
            assert result.features == PdfFeatureSummary.unknown()
            continue

        assert result.open_state is PdfOpenState.OPENED
        assert result.encryption_state is PdfEncryptionState.UNENCRYPTED
        assert result.structural_state is PdfStructuralState.CLEAN
        if path.name in redaction_expected:
            page_count, features, annotations = redaction_expected[path.name]
            assert result.page_count == page_count
            assert _feature_values(result.features) == features
            assert result.features.annotations is annotations
            assert result.object_count is not None and result.object_count > 0
            continue
        assert result.page_count == (
            6
            if path.name == "render-geometry-matrix.pdf"
            else 2
            if path.name
            in {
                "mixed.pdf",
                "ocr-readable-mixed.pdf",
                "page-geometry.pdf",
                "render-calibration.pdf",
            }
            else 1
        )
        assert result.object_count is not None and result.object_count > 0
        values = _feature_values(result.features)
        if path.name in ordinary_names:
            assert values == not_detected
        elif path.name in {"annotation.pdf", "render-annotation-appearance.pdf"}:
            assert values == (
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
            )
        elif path.name == "attachment.pdf":
            assert values == (
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
            )
        elif path.name == "action-marker.pdf":
            assert values == (
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
            )
        elif path.name == "script-marker.pdf":
            assert values == (
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
            )
        elif path.name == "signed-marker.pdf":
            assert values == (
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
                PdfFeatureState.NOT_DETECTED,
            )
        else:  # pragma: no cover - guarded by the exact corpus assertion above
            raise AssertionError("fixture expectation is missing")
        assert result.features.annotations is (
            PdfFeatureState.DETECTED
            if path.name
            in {"annotation.pdf", "render-annotation-appearance.pdf", "signed-marker.pdf"}
            else PdfFeatureState.NOT_DETECTED
        )


def test_open_uses_no_password_no_recovery_and_suppresses_raw_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = pikepdf.Pdf.open
    observed: list[dict[str, object]] = []

    def tracked_open(stream: BinaryIO, **kwargs: object) -> pikepdf.Pdf:
        observed.append(kwargs)
        return original_open(stream, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pikepdf.Pdf, "open", tracked_open)

    result = _inspect(PDF_FIXTURES / "blank.pdf")

    assert result.open_state is PdfOpenState.OPENED
    assert observed == [
        {
            "password": "",
            "attempt_recovery": False,
            "suppress_warnings": True,
        }
    ]


@pytest.mark.parametrize(
    ("path_name", "limits", "context"),
    [
        ("mixed.pdf", _limits(maximum_pages=1), "pdf.pages"),
        ("blank.pdf", _limits(maximum_objects=3), "pdf.objects"),
        ("blank.pdf", _limits(maximum_input_bytes=1), "pdf.input_bytes"),
    ],
)
def test_input_page_and_object_limits_fail_closed(
    path_name: str,
    limits: PdfInspectionLimits,
    context: str,
) -> None:
    with pytest.raises(PdfInspectionError) as raised:
        _inspect(PDF_FIXTURES / path_name, limits=limits)

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == context


@pytest.mark.parametrize(
    ("adapter", "expected_context"),
    [
        (PikePdfAdapter(maximum_graph_depth=1), "pdf.graph_depth"),
        (PikePdfAdapter(maximum_collection_items=1), "pdf.collection_items"),
        (PikePdfAdapter(graph_nodes_per_object=1), "pdf.graph_nodes"),
    ],
)
def test_depth_collection_and_node_limits_cover_nested_object_graph(
    adapter: PikePdfAdapter,
    expected_context: str,
) -> None:
    with pytest.raises(PdfInspectionError) as raised:
        _inspect(PDF_FIXTURES / "blank.pdf", adapter=adapter)

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == expected_context


def test_cancellation_is_checked_during_the_all_object_scan() -> None:
    checkpoints = 0

    def cancellation_probe() -> bool:
        nonlocal checkpoints
        checkpoints += 1
        return checkpoints >= 6

    with pytest.raises(PdfInspectionError) as raised:
        _inspect(
            PDF_FIXTURES / "born-digital.pdf",
            cancellation_probe=cancellation_probe,
        )

    assert checkpoints >= 6
    assert raised.value.code is PdfInspectionErrorCode.CANCELLED


def test_deadline_is_checked_before_untrusted_parsing() -> None:
    with pytest.raises(PdfInspectionError) as raised:
        _inspect(PDF_FIXTURES / "blank.pdf", deadline=1.0)

    assert raised.value.code is PdfInspectionErrorCode.TIMED_OUT


def test_syntax_warning_values_are_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_token = "PRIVATE-PDF-SYNTAX-VALUE"

    def warning_report(
        _document: pikepdf.Pdf,
        progress: Callable[[int], None] | None = None,
    ) -> list[str]:
        if callable(progress):
            progress(50)
        return [private_token]

    monkeypatch.setattr(pikepdf.Pdf, "check_pdf_syntax", warning_report)

    result = _inspect(PDF_FIXTURES / "blank.pdf")

    assert result.structural_state is PdfStructuralState.WARNINGS
    assert private_token not in repr(result)
    assert PdfFeatureState.NOT_DETECTED not in result.features.values


class _BrokenStream:
    def seek(self, _offset: int) -> int:
        raise OSError("PRIVATE-PDF-PATH:C:\\private\\applicant.pdf")


def test_raw_source_failures_never_escape_the_adapter() -> None:
    with pytest.raises(PdfInspectionError) as raised:
        PikePdfAdapter().inspect(
            cast(BinaryIO, _BrokenStream()),
            source_bytes=100,
            limits=_limits(),
            deadline=time.monotonic() + 30,
        )

    rendered = f"{raised.value!s} {raised.value!r}"
    assert raised.value.code is PdfInspectionErrorCode.SOURCE_INVALID
    assert "PRIVATE-PDF-PATH" not in rendered
    assert "applicant.pdf" not in rendered


def test_repeated_checkpoint_probe_is_not_replaced_by_one_shot_checks() -> None:
    calls: list[int] = []

    def probe() -> bool:
        calls.append(len(calls))
        return False

    _inspect(PDF_FIXTURES / "page-geometry.pdf", cancellation_probe=probe)

    assert len(calls) > 10


def _write_feature_pdf(path: Path, feature: str) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page()
        if feature == "info":
            document.docinfo["/Title"] = "PRIVATE-PDF-METADATA-VALUE"
        elif feature == "empty_info":
            document.trailer["/Info"] = document.make_indirect(pikepdf.Dictionary())
        elif feature in {"catalog_metadata", "page_metadata", "typed_metadata"}:
            metadata = document.make_stream(b"PRIVATE-PDF-XMP-VALUE")
            metadata["/Type"] = pikepdf.Name("/Metadata")
            metadata["/Subtype"] = pikepdf.Name("/XML")
            target = page.obj if feature == "page_metadata" else document.Root
            target["/SyntheticCarrier" if feature == "typed_metadata" else "/Metadata"] = metadata
        elif feature in {"text_comment", "popup_comment", "free_text_comment", "widget"}:
            subtype = {
                "text_comment": "/Text",
                "popup_comment": "/Popup",
                "free_text_comment": "/FreeText",
                "widget": "/Widget",
            }[feature]
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name(subtype),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                Contents="PRIVATE-PDF-COMMENT-VALUE",
            )
            annotation = document.make_indirect(annotation)
            page.obj["/Annots"] = pikepdf.Array([annotation])
            if feature == "widget":
                annotation["/FT"] = pikepdf.Name("/Tx")
                document.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([annotation]))
        elif feature == "form_fields":
            field = document.make_indirect(
                pikepdf.Dictionary(
                    FT=pikepdf.Name("/Tx"),
                    T="PRIVATE-PDF-FIELD-NAME",
                    V="PRIVATE-PDF-FIELD-VALUE",
                )
            )
            document.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([field]))
        elif feature == "script":
            action = document.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name("/JavaScript"),
                    JS="PRIVATE-PDF-SCRIPT-VALUE",
                )
            )
            document.Root["/OpenAction"] = action
            action["/Next"] = action
        elif feature == "attachment":
            stream = document.make_stream(b"PRIVATE-PDF-ATTACHMENT-VALUE")
            stream["/Type"] = pikepdf.Name("/EmbeddedFile")
            filespec = document.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name("/Filespec"),
                    F="PRIVATE-PDF-ATTACHMENT-NAME",
                    EF=pikepdf.Dictionary(F=stream),
                )
            )
            document.Root["/AF"] = pikepdf.Array([filespec])
        elif feature == "xfa":
            document.Root["/AcroForm"] = pikepdf.Dictionary(
                XFA=document.make_stream(b"PRIVATE-PDF-XFA-VALUE"),
            )
        elif feature == "xfa_packets":
            document.Root["/AcroForm"] = pikepdf.Dictionary(
                XFA=pikepdf.Array(
                    ["template", document.make_stream(b"PRIVATE-PDF-XFA-PACKET-VALUE")]
                ),
            )
        elif feature == "signature":
            document.Root["/AcroForm"] = pikepdf.Dictionary(
                Fields=pikepdf.Array(
                    [document.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name("/Sig")))]
                )
            )
        elif feature == "bad_info":
            document.trailer["/Info"] = "PRIVATE-PDF-INVALID-INFO"
        elif feature == "bad_metadata":
            document.Root["/Metadata"] = "PRIVATE-PDF-INVALID-METADATA"
        elif feature == "bad_annots":
            page.obj["/Annots"] = "PRIVATE-PDF-INVALID-ANNOTS"
        elif feature == "bad_annotation_member":
            page.obj["/Annots"] = pikepdf.Array([42])
        elif feature == "bad_field_member":
            document.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([42]))
        elif feature == "bad_associated_file_member":
            document.Root["/AF"] = pikepdf.Array([42])
        elif feature == "bad_embedded_file_member":
            document.Root["/AF"] = pikepdf.Array(
                [pikepdf.Dictionary(Type=pikepdf.Name.Filespec, EF=pikepdf.Dictionary(F=42))]
            )
        elif feature in {"bad_xfa_odd_packets", "bad_xfa_packet_name", "bad_xfa_packet_stream"}:
            packet_values = {
                "bad_xfa_odd_packets": pikepdf.Array([42]),
                "bad_xfa_packet_name": pikepdf.Array([42, document.make_stream(b"synthetic")]),
                "bad_xfa_packet_stream": pikepdf.Array(["template", 42]),
            }
            document.Root["/AcroForm"] = pikepdf.Dictionary(XFA=packet_values[feature])
        elif feature == "unknown_annotation":
            page.obj["/Annots"] = pikepdf.Array(
                [
                    document.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name("/Annot"),
                            Subtype=pikepdf.Name("/FutureAnnotation"),
                        )
                    )
                ]
            )
        elif feature == "unknown_form":
            document.Root["/AcroForm"] = pikepdf.Dictionary(
                Fields=pikepdf.Array(
                    [document.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name("/FutureField")))]
                )
            )
        elif feature == "unknown_action":
            document.Root["/OpenAction"] = pikepdf.Dictionary(
                Type=pikepdf.Name("/Action"),
                S=pikepdf.Name("/FutureAction"),
            )
        elif feature == "unknown_additional_action":
            document.Root["/AA"] = pikepdf.Dictionary(
                WC=pikepdf.Dictionary(S=pikepdf.Name("/FutureAction")),
            )
        elif feature == "unknown_next_action":
            document.Root["/OpenAction"] = pikepdf.Dictionary(
                S=pikepdf.Name("/Named"),
                N=pikepdf.Name("/NextPage"),
                Next=pikepdf.Array([pikepdf.Dictionary(S=pikepdf.Name("/FutureAction"))]),
            )
        elif feature != "none":
            raise AssertionError("unknown synthetic feature")
        document.save(path, fix_metadata_version=False)


@pytest.mark.parametrize(
    ("feature", "detected"),
    [
        ("none", set()),
        ("info", {"metadata"}),
        ("empty_info", {"metadata"}),
        ("catalog_metadata", {"metadata"}),
        ("page_metadata", {"metadata"}),
        ("typed_metadata", {"metadata"}),
        ("text_comment", {"annotations", "interactive_content"}),
        ("popup_comment", {"annotations", "interactive_content"}),
        ("free_text_comment", {"annotations", "interactive_content"}),
        ("widget", {"annotations", "form_fields", "interactive_content"}),
        ("form_fields", {"form_fields", "interactive_content"}),
        ("script", {"scripts", "actions", "interactive_content"}),
        ("attachment", {"attachments"}),
        ("xfa", {"xfa", "form_fields", "interactive_content"}),
        ("xfa_packets", {"xfa", "form_fields", "interactive_content"}),
        ("signature", {"signature_markers", "form_fields", "interactive_content"}),
    ],
)
def test_generated_feature_matrix_has_closed_value_free_states(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    feature: str,
    detected: set[str],
) -> None:
    source = tmp_path / "synthetic.pdf"
    _write_feature_pdf(source, feature)
    original = source.read_bytes()

    result = _inspect(source)

    assert result.report_version == 2
    assert result.open_state is PdfOpenState.OPENED
    assert result.structural_state is PdfStructuralState.CLEAN
    report = encode_pdf_inspection_report(result)
    for name in (
        "metadata",
        "annotations",
        "form_fields",
        "signature_markers",
        "attachments",
        "interactive_content",
        "actions",
        "scripts",
        "xfa",
    ):
        assert getattr(result.features, name) is (
            PdfFeatureState.DETECTED if name in detected else PdfFeatureState.NOT_DETECTED
        )
    captured = capsys.readouterr()
    assert "PRIVATE-PDF-" not in f"{result!r}{report!r}{caplog.text}{captured.out}{captured.err}"
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    ("feature", "detected"),
    [
        ("bad_info", "metadata"),
        ("bad_metadata", "metadata"),
        ("bad_annots", "annotations"),
        ("unknown_annotation", "annotations"),
        ("unknown_form", "form_fields"),
        ("unknown_action", "actions"),
        ("unknown_additional_action", "actions"),
        ("unknown_next_action", "actions"),
    ],
)
def test_unsupported_feature_structures_do_not_create_absence_claims(
    tmp_path: Path,
    feature: str,
    detected: str,
) -> None:
    source = tmp_path / "synthetic.pdf"
    _write_feature_pdf(source, feature)

    result = _inspect(source)

    assert result.open_state is PdfOpenState.OPENED
    assert getattr(result.features, detected) is (
        PdfFeatureState.UNKNOWN if feature == "bad_annots" else PdfFeatureState.DETECTED
    )
    assert PdfFeatureState.NOT_DETECTED not in result.features.values


@pytest.mark.parametrize(
    ("feature", "detected"),
    (
        ("bad_annotation_member", "annotations"),
        ("bad_field_member", "form_fields"),
        ("bad_associated_file_member", "attachments"),
        ("bad_embedded_file_member", "attachments"),
        ("bad_xfa_odd_packets", "xfa"),
        ("bad_xfa_packet_name", "xfa"),
        ("bad_xfa_packet_stream", "xfa"),
    ),
)
def test_malformed_feature_members_remain_unknown_even_without_syntax_warnings(
    tmp_path: Path,
    feature: str,
    detected: str,
) -> None:
    source = tmp_path / "synthetic.pdf"
    _write_feature_pdf(source, feature)
    original = source.read_bytes()

    result = _inspect(source)

    assert result.open_state is PdfOpenState.OPENED
    assert result.structural_state is PdfStructuralState.CLEAN
    assert getattr(result.features, detected) is PdfFeatureState.DETECTED
    assert PdfFeatureState.NOT_DETECTED not in result.features.values
    assert result.features.signature_markers is PdfFeatureState.UNKNOWN
    assert source.read_bytes() == original


def test_syntax_warning_preserves_observed_features_and_unknown_remainder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "synthetic.pdf"
    _write_feature_pdf(source, "form_fields")
    monkeypatch.setattr(
        pikepdf.Pdf, "check_pdf_syntax", lambda *_args, **_kwargs: ["PRIVATE-PDF-WARNING"]
    )

    result = _inspect(source)

    assert result.features.form_fields is PdfFeatureState.DETECTED
    assert result.features.interactive_content is PdfFeatureState.DETECTED
    assert result.features.signature_markers is PdfFeatureState.UNKNOWN
    assert PdfFeatureState.NOT_DETECTED not in result.features.values
    assert b"PRIVATE-PDF-" not in encode_pdf_inspection_report(result)


def test_empty_password_encryption_remains_uninspected(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.pdf"
    with pikepdf.Pdf.new() as document:
        document.add_blank_page()
        document.save(source, encryption=pikepdf.Encryption(owner="synthetic-owner", user="", R=6))

    result = _inspect(source)

    assert result.open_state is PdfOpenState.ENCRYPTED
    assert result.encryption_state is PdfEncryptionState.ENCRYPTED
    assert result.features == PdfFeatureSummary.unknown()
