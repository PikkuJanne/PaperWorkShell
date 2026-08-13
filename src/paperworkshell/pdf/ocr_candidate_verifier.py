"""Narrow pikepdf seam for reopening a volatile OCR PDF candidate."""

from __future__ import annotations

import gc
from pathlib import Path

import pikepdf


class OcrPdfCandidateVerificationError(RuntimeError):
    """Content-free failure raised when an OCR candidate cannot be reopened."""

    def __init__(self) -> None:
        super().__init__("OCR_PDF_CANDIDATE_INVALID")


def close_private_ocr_pdf_documents(work_directories: tuple[Path, ...]) -> bool:
    """Close pikepdf documents pinned beneath authenticated OCR work roots."""

    if type(work_directories) is not tuple or any(
        not isinstance(root, Path) or not root.is_absolute() for root in work_directories
    ):
        return False
    if not work_directories:
        return True
    try:
        objects = gc.get_objects()
    except Exception:
        return False
    for value in objects:
        if type(value) is not pikepdf.Pdf:
            continue
        try:
            filename = value.filename
            path = Path(filename) if type(filename) is str else None
        except Exception:
            return False
        if path is None or not any(_is_beneath(path, root) for root in work_directories):
            continue
        try:
            value.close()
        except Exception:
            return False
    return True


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


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root
