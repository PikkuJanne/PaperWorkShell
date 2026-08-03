"""PWS-0105 contract tests for the bounded pikepdf adapter."""

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
    return summary.values


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
        "page-geometry.pdf",
        "password-required.pdf",
        "render-calibration.pdf",
        "render-cmyk.pdf",
        "render-high-dimension.pdf",
        "render-malformed-page-tree.pdf",
        "render-transparency.pdf",
        "script-marker.pdf",
        "signed-marker.pdf",
    }
    assert len(unicode_names) == 1
    assert names == expected_names | unicode_names

    ordinary_names = {
        "blank.pdf",
        "born-digital.pdf",
        "duplicate-a.pdf",
        "duplicate-b.pdf",
        "image-only.pdf",
        "incremental-update.pdf",
        "mixed.pdf",
        "page-geometry.pdf",
        "render-calibration.pdf",
        "render-cmyk.pdf",
        "render-high-dimension.pdf",
        "render-transparency.pdf",
        *unicode_names,
    }
    not_detected = (PdfFeatureState.NOT_DETECTED,) * 6

    for path in paths:
        result = _inspect(path)
        if path.name in {"malformed-truncated.pdf", "render-malformed-page-tree.pdf"}:
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
        assert result.page_count == (
            2 if path.name in {"mixed.pdf", "page-geometry.pdf", "render-calibration.pdf"} else 1
        )
        assert result.object_count is not None and result.object_count > 0
        values = _feature_values(result.features)
        if path.name in ordinary_names:
            assert values == not_detected
        elif path.name == "annotation.pdf":
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
