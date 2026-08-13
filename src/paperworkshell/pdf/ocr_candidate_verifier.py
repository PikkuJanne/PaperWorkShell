"""Narrow pikepdf seam for reopening a volatile OCR PDF candidate."""

from __future__ import annotations

from pathlib import Path

import pikepdf


class OcrPdfCandidateVerificationError(RuntimeError):
    """Content-free failure raised when an OCR candidate cannot be reopened."""

    def __init__(self) -> None:
        super().__init__("OCR_PDF_CANDIDATE_INVALID")


def verified_ocr_pdf_page_count(path: Path) -> int:
    """Reopen one private candidate and return only its positive page count."""

    try:
        with pikepdf.Pdf.open(
            path,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
        ) as document:
            if document.is_encrypted:
                raise OcrPdfCandidateVerificationError from None
            count = len(document.pages)
    except OcrPdfCandidateVerificationError:
        raise
    except Exception:
        raise OcrPdfCandidateVerificationError from None
    if type(count) is not int or count < 1:
        raise OcrPdfCandidateVerificationError from None
    return count
