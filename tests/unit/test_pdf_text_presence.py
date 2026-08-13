"""Focused tests for bounded PDFium text-presence facts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.pdf.text_presence import (
    PdfiumTextPresenceInspector,
    PdfTextPresenceLimits,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURES = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf"


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled = cancelled
        self.expired = expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _limits(**changes: int) -> PdfTextPresenceLimits:
    values = {
        "maximum_pages": 100,
        "maximum_source_bytes": 20_000_000,
        "maximum_characters_per_page": 1_000_000,
    }
    values.update(changes)
    return PdfTextPresenceLimits(**values)


def test_inspector_returns_only_content_free_counts_for_born_digital_and_image_pages() -> None:
    mixed = (PDF_FIXTURES / "mixed.pdf").resolve()

    facts = PdfiumTextPresenceInspector().inspect(
        mixed,
        (0, 1),
        _limits(),
        _Control(),
    )

    assert tuple(fact.page_index for fact in facts) == (0, 1)
    assert facts[0].character_count > 0
    assert facts[0].non_whitespace_character_count > 0
    assert facts[0].has_extractable_text
    assert facts[1].character_count == 0
    assert facts[1].non_whitespace_character_count == 0
    assert not facts[1].has_extractable_text
    assert all(not hasattr(fact, "text") for fact in facts)
    rendered = " ".join((repr(facts), *(repr(fact) for fact in facts)))
    assert "Synthetic born-digital" not in rendered


def test_whitespace_only_text_is_not_reported_as_extractable_text(tmp_path: Path) -> None:
    import pikepdf

    source = tmp_path / "whitespace.pdf"
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 72))
        font = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.obj.Contents = pikepdf.Stream(document, b"BT /F1 10 Tf 0 0 Td (   ) Tj ET")
        document.save(source)

    fact = PdfiumTextPresenceInspector().inspect(
        source.resolve(),
        (0,),
        _limits(),
        _Control(),
    )[0]

    assert fact.character_count >= 1
    assert fact.non_whitespace_character_count == 0
    assert not fact.has_extractable_text


def test_exact_runtime_version_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pdfium,
        "PYPDFIUM_INFO",
        SimpleNamespace(version="future"),
    )

    with pytest.raises(PdfExportError) as raised:
        PdfiumTextPresenceInspector().inspect(
            (PDF_FIXTURES / "born-digital.pdf").resolve(),
            (0,),
            _limits(),
            _Control(),
        )

    assert raised.value.code is PdfExportErrorCode.VERIFICATION_REOPEN_FAILED
    assert raised.value.context == "export.text_runtime"


def test_page_character_budget_fails_before_any_text_can_escape() -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()

    with pytest.raises(PdfExportError) as raised:
        PdfiumTextPresenceInspector().inspect(
            source,
            (0,),
            _limits(maximum_characters_per_page=1),
            _Control(),
        )

    assert raised.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    rendered = " ".join((str(raised.value), repr(raised.value), *(map(str, raised.value.args))))
    assert "Synthetic born-digital" not in rendered
    assert str(source) not in rendered
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        (_Control(cancelled=True), PdfExportErrorCode.CANCELLED),
        (_Control(expired=True), PdfExportErrorCode.TIMED_OUT),
    ],
)
def test_cancellation_and_timeout_are_stable(
    control: _Control,
    expected: PdfExportErrorCode,
) -> None:
    with pytest.raises(PdfExportError) as raised:
        PdfiumTextPresenceInspector().inspect(
            (PDF_FIXTURES / "born-digital.pdf").resolve(),
            (0,),
            _limits(),
            control,
        )

    assert raised.value.code is expected


def test_duplicate_or_out_of_range_page_request_is_rejected() -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()

    with pytest.raises(PdfExportError) as duplicate:
        PdfiumTextPresenceInspector().inspect(source, (0, 0), _limits(), _Control())
    with pytest.raises(PdfExportError) as out_of_range:
        PdfiumTextPresenceInspector().inspect(source, (1,), _limits(), _Control())

    assert duplicate.value.code is PdfExportErrorCode.MODEL_INVALID
    assert out_of_range.value.code is PdfExportErrorCode.SOURCE_INCONSISTENT
