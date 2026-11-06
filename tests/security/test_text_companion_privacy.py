"""PWS-0706 synthetic canary exclusion for companion output and diagnostics."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from paperworkshell.domain.text_companion import TextCompanionError
from tests.unit.test_text_companion import _extract, _pdf


def test_companion_reads_final_derivative_without_source_ocr_cache_metadata_or_attachment_tokens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from io import BytesIO

    import pikepdf

    source = _pdf(text=b"PWSREMOVED SOURCE VALUE")
    (tmp_path / "original.pdf").write_bytes(source)
    (tmp_path / "ocr-cache.txt").write_text("PWSREMOVED CACHED OCR VALUE", encoding="utf-8")
    with pikepdf.Pdf.open(BytesIO(_pdf())) as final:
        final.attachments["synthetic.txt"] = pikepdf.AttachedFileSpec(
            final,
            b"PWSREMOVED ATTACHMENT VALUE",
            description="Synthetic",
            filename="synthetic.txt",
            mime_type="text/plain",
            creation_date="",
            mod_date="",
        )
        saved = BytesIO()
        final.save(saved)
        derivative = saved.getvalue()
    with caplog.at_level(logging.DEBUG):
        companion = _extract(derivative)
    assert b"PWSKEEP EXPORTED TEXT" in companion
    assert b"PWSREMOVED" not in companion and b"PRIVATE" not in companion
    assert (
        "PWSKEEP" not in caplog.text
        and "PWSREMOVED" not in caplog.text
        and "PRIVATE" not in caplog.text
    )
    assert (tmp_path / "original.pdf").read_bytes() == source


def test_failed_extraction_diagnostics_contain_only_stable_codes_not_fixture_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(TextCompanionError) as caught:
        _extract(b"PWSREMOVED PRIVATE MALFORMED PDF")
    report = str(caught.value) + repr(caught.value) + repr(caught.value.args) + caplog.text
    assert "PWSREMOVED" not in report and "PRIVATE" not in report
    assert caught.value.__context__ is None and caught.value.__cause__ is None
