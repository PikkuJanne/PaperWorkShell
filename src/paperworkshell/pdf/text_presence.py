"""Bounded PDFium text-presence inspection without returning document text."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_PAGES
from paperworkshell.pdf.pdfium_renderer import (
    EXPECTED_PDFIUM_BUILD,
    EXPECTED_PDFIUM_VERSION,
    EXPECTED_PYPDFIUM2_VERSION,
)

PDF_TEXT_PRESENCE_INSPECTOR_VERSION: Final = 1
_MAXIMUM_CHARACTERS_PER_PAGE: Final = 10_000_000
_TEXT_CHUNK_CHARACTERS: Final = 256
_LOCK_POLL_SECONDS: Final = 0.05
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_PDFIUM_LOCK: Final = threading.Lock()


class PdfTextPresenceControl(Protocol):
    """Cooperative probes supplied by the owning one-shot worker."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PdfTextPresenceLimits:
    """Trusted file, page, and text-page budgets checked before extraction."""

    maximum_pages: int
    maximum_source_bytes: int
    maximum_characters_per_page: int

    def __post_init__(self) -> None:
        if (
            type(self.maximum_pages) is not int
            or not 1 <= self.maximum_pages <= MAX_PDF_INSPECTION_PAGES
            or type(self.maximum_source_bytes) is not int
            or self.maximum_source_bytes < 1
            or type(self.maximum_characters_per_page) is not int
            or not 1 <= self.maximum_characters_per_page <= _MAXIMUM_CHARACTERS_PER_PAGE
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.text_limits")


@dataclass(frozen=True, slots=True)
class PdfTextPresenceFact:
    """Content-free counts for one PDF page under the fixed text profile."""

    page_index: int
    character_count: int
    non_whitespace_character_count: int

    def __post_init__(self) -> None:
        if (
            type(self.page_index) is not int
            or not 0 <= self.page_index < MAX_PDF_INSPECTION_PAGES
            or type(self.character_count) is not int
            or self.character_count < 0
            or type(self.non_whitespace_character_count) is not int
            or not 0 <= self.non_whitespace_character_count <= self.character_count
        ):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.text_fact")

    @property
    def has_extractable_text(self) -> bool:
        """Return whether PDFium exposed at least one non-whitespace character."""

        return self.non_whitespace_character_count > 0


@dataclass(frozen=True, slots=True)
class PdfiumTextPresenceInspector:
    """Inspect selected pages with the exact approved PDFium runtime."""

    def inspect(
        self,
        source_path: Path,
        page_indices: tuple[int, ...],
        limits: PdfTextPresenceLimits,
        control: PdfTextPresenceControl,
    ) -> tuple[PdfTextPresenceFact, ...]:
        """Return only counts and presence facts; never return extracted text."""

        _validate_request(source_path, page_indices, limits, control)
        _require_active(control)
        _require_approved_runtime()
        _validate_source_file(source_path, limits)

        try:
            with _serialized_pdfium(control), source_path.open("rb") as source:
                with pdfium.PdfDocument(source, password="", autoclose=False) as document:
                    page_count = len(document)
                    if page_count < 1 or page_count > limits.maximum_pages:
                        reject_pdf_export(
                            PdfExportErrorCode.LIMIT_EXCEEDED,
                            "export.text_pages",
                        )
                    if any(page_index >= page_count for page_index in page_indices):
                        reject_pdf_export(
                            PdfExportErrorCode.SOURCE_INCONSISTENT,
                            "export.text_page_index",
                        )
                    facts = tuple(
                        _inspect_page(document, page_index, limits, control)
                        for page_index in page_indices
                    )
        except PdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.text_resources")
        except Exception:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
                "export.text_pdfium",
            )
        _require_active(control)
        return facts


def _inspect_page(
    document: object,
    page_index: int,
    limits: PdfTextPresenceLimits,
    control: PdfTextPresenceControl,
) -> PdfTextPresenceFact:
    _require_active(control)
    page = None
    text_page = None
    try:
        page = document.get_page(page_index)  # type: ignore[attr-defined]
        text_page = page.get_textpage()
        character_count = text_page.count_chars()
        if type(character_count) is not int or character_count < 0:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                "export.text_count",
            )
        if character_count > limits.maximum_characters_per_page:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.text_characters")

        non_whitespace = 0
        for character_index in range(character_count):
            if character_index % _TEXT_CHUNK_CHARACTERS == 0:
                _require_active(control)
            codepoint = pdfium_c.FPDFText_GetUnicode(text_page, character_index)
            if type(codepoint) is not int or not 0 <= codepoint <= 0x10FFFF:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "export.text_character",
                )
            # Convert one scalar only long enough to derive a boolean. No document
            # text is assembled, returned, retained on an error, logged, or formatted.
            if codepoint != 0 and not chr(codepoint).isspace():
                non_whitespace += 1
        return PdfTextPresenceFact(page_index, character_count, non_whitespace)
    finally:
        # PDFium child handles must close before their owning document. Any close
        # failure is sanitized by the public boundary above.
        if text_page is not None:
            text_page.close()
        if page is not None:
            page.close()


def _validate_request(
    source_path: Path,
    page_indices: tuple[int, ...],
    limits: PdfTextPresenceLimits,
    control: PdfTextPresenceControl,
) -> None:
    if (
        not isinstance(source_path, Path)
        or not source_path.is_absolute()
        or type(page_indices) is not tuple
        or not page_indices
        or len(page_indices) > MAX_PDF_INSPECTION_PAGES
        or len(set(page_indices)) != len(page_indices)
        or any(
            type(page_index) is not int or not 0 <= page_index < MAX_PDF_INSPECTION_PAGES
            for page_index in page_indices
        )
        or type(limits) is not PdfTextPresenceLimits
        or not callable(getattr(control, "cancellation_requested", None))
        or not callable(getattr(control, "deadline_expired", None))
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.text_request")


def _validate_source_file(source_path: Path, limits: PdfTextPresenceLimits) -> None:
    try:
        info = os.lstat(source_path)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.text_source")
    attributes = getattr(info, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(info.st_mode)
        or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        or not 1 <= info.st_size <= limits.maximum_source_bytes
    ):
        code = (
            PdfExportErrorCode.LIMIT_EXCEEDED
            if info.st_size > limits.maximum_source_bytes
            else PdfExportErrorCode.SOURCE_UNSUPPORTED
        )
        reject_pdf_export(code, "export.text_source")


def _require_approved_runtime() -> None:
    try:
        wrapper_version = pdfium.PYPDFIUM_INFO.version
        engine_version = pdfium.PDFIUM_INFO.version
        engine_build = pdfium.PDFIUM_INFO.build
        feature_flags = pdfium.PDFIUM_INFO.flags
    except Exception:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "export.text_runtime",
        )
    if (
        wrapper_version != EXPECTED_PYPDFIUM2_VERSION
        or engine_version != EXPECTED_PDFIUM_VERSION
        or engine_build != EXPECTED_PDFIUM_BUILD
        or feature_flags != ()
    ):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "export.text_runtime",
        )


@contextmanager
def _serialized_pdfium(control: PdfTextPresenceControl) -> Iterator[None]:
    acquired = False
    try:
        while not acquired:
            _require_active(control)
            acquired = _PDFIUM_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
        _require_active(control)
        yield
    finally:
        if acquired:
            _PDFIUM_LOCK.release()


def _require_active(control: PdfTextPresenceControl) -> None:
    try:
        cancelled = control.cancellation_requested()
        expired = control.deadline_expired()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.text_control")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.text_control")
    if cancelled:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.text_operation")
    if expired:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.text_operation")
