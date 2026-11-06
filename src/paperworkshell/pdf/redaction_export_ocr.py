"""Actual OCR of exact image-only removal candidates and their full resaves.

Recognized text is private verification material, never published or reported.
Only the already sanitized raster is supplied to the admitted local processor.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrAdapterResult,
    OcrError,
)
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionReport
from paperworkshell.domain.redaction_verification import (
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.pdf.redaction_ocr import (
    RedactionOcrProcessor,
    _image_identity,
    _map_ocr_error,
    _remove_tree,
    _render_digest,
    _rewrite_generated_ocr,
    _verify_text_exclusion,
)
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _measure_path,
    _measure_source,
)

_CHUNK = 64 * 1024


def _require_private_files(
    root: Path,
    expected: tuple[Path, ...],
    maximum_bytes: int,
    control: RedactionReconstructionControl,
    *,
    prospective_bytes: int = 0,
) -> None:
    """Account every live private file; reject leftover or aliased engine work."""
    names = {path.name for path in expected}
    seen: set[str] = set()
    identities: set[tuple[int, int]] = set()
    count = prospective_bytes
    with os.scandir(root) as entries:
        for entry in entries:
            _checkpoint(control)
            if entry.name not in names or entry.name in seen:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "removal_ocr.private_files")
            # Windows DirEntry.stat may expose zero link/file-ID fields from
            # cached directory enumeration. A fresh no-follow stat is required.
            info = Path(entry.path).stat(follow_symlinks=False)
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or info.st_nlink != 1
                or identity in identities
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "removal_ocr.private_files")
            count += info.st_size
            if count > maximum_bytes:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "removal_ocr.private_bytes")
            seen.add(entry.name)
            identities.add(identity)
    if seen != names:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "removal_ocr.private_files")


@dataclass(frozen=True, slots=True, repr=False)
class RedactionCandidateOcrVerifier:
    """A worker-owned runner with the exact reconstructed geometry and masks."""

    processor: RedactionOcrProcessor = field(repr=False)
    reconstructor: RedactionReconstructionAdapter = field(repr=False)
    reconstruction: RedactionReconstructionReport = field(repr=False)
    exclusions: tuple[tuple[float, float, float, float], ...] = field(repr=False)
    scratch_directory: Path = field(repr=False)
    limits: RedactionReconstructionLimits = field(repr=False)
    control: RedactionReconstructionControl = field(repr=False)
    maximum_private_bytes: int

    def __call__(self, source: BinaryIO) -> VerificationMethodResult:
        """Recognize each exact stream; reject text touching a removed region."""
        position = source.tell()
        private = self.scratch_directory / uuid4().hex
        owned = False
        try:
            _checkpoint(self.control)
            expected = _measure_source(source, self.limits.maximum_output_bytes, self.control)
            if type(self.maximum_private_bytes) is not int or self.maximum_private_bytes < max(
                expected[0] + self.limits.maximum_output_bytes,
                2 * self.limits.maximum_output_bytes,
            ):
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "removal_ocr.temp_budget")
            private.mkdir()
            owned = True
            staged = private / f"{uuid4().hex}.pdf"
            recognized = private / f"{uuid4().hex}.pdf"
            rewritten = private / f"{uuid4().hex}.pdf"
            source.seek(0)
            count, digest = 0, hashlib.sha256()
            with staged.open("xb") as output:
                while block := source.read(_CHUNK):
                    _checkpoint(self.control)
                    count += len(block)
                    if count > expected[0] or output.write(block) != len(block):
                        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "removal_ocr.copy")
                    digest.update(block)
            if (count, digest.hexdigest()) != expected:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "removal_ocr.input")
            _require_private_files(private, (staged,), self.maximum_private_bytes, self.control)
            image = _image_identity(staged, self.reconstruction, self.limits, self.control)
            rendered = _render_digest(
                self.reconstructor, staged, self.reconstruction, self.limits, self.control
            )
            result = self.processor.process(
                staged,
                recognized,
                temporary_directory=private,
                languages=("eng",),
                profile_id=DEFAULT_OCR_PROFILE_ID,
                expected_page_count=1,
                maximum_output_bytes=self.limits.maximum_output_bytes,
                maximum_image_pixels=self.limits.maximum_pixels,
                maximum_temporary_storage_bytes=self.maximum_private_bytes,
                control=self.control,
                progress=lambda *_values: _checkpoint(self.control),
            )
            _checkpoint(self.control)
            _require_private_files(
                private, (staged, recognized), self.maximum_private_bytes, self.control
            )
            if (
                type(result) is not OcrAdapterResult
                or result.page_count != 1
                or result.ocrmypdf_version != TRUSTED_OCRMYPDF_VERSION
                or result.tesseract_version != TRUSTED_TESSERACT_VERSION
                or _measure_path(recognized, self.limits.maximum_output_bytes, self.control)
                != (result.byte_size, result.sha256)
                or _measure_path(staged, self.limits.maximum_output_bytes, self.control) != expected
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "removal_ocr.output_binding")
            # OCRmyPDF may graft onto its already sanitized input incrementally.
            # Removing that staged copy before a fresh rewrite bounds live PDFs.
            staged.unlink()
            _require_private_files(
                private,
                (recognized,),
                self.maximum_private_bytes,
                self.control,
                prospective_bytes=self.limits.maximum_output_bytes,
            )
            _rewrite_generated_ocr(recognized, rewritten, self.limits, self.control)
            recognized.unlink()
            _require_private_files(private, (rewritten,), self.maximum_private_bytes, self.control)
            if _image_identity(rewritten, self.reconstruction, self.limits, self.control) != image:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "removal_ocr.image"
                )
            if (
                _render_digest(
                    self.reconstructor, rewritten, self.reconstruction, self.limits, self.control
                )
                != rendered
            ):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "removal_ocr.appearance"
                )
            _verify_text_exclusion(rewritten, self.exclusions, self.limits, self.control)
            if _measure_source(source, self.limits.maximum_output_bytes, self.control) != expected:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "removal_ocr.input_recheck")
            _checkpoint(self.control)
            return VerificationMethodResult(
                VerificationMethod.OCR, VerificationStatus.PASS, VerificationCode.CLEAR
            )
        except OcrError as error:
            _map_ocr_error(error)
        finally:
            try:
                if owned:
                    _remove_tree(private, self.limits.maximum_pdf_objects)
            finally:
                if source.seek(position) != position:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "removal_ocr.input_cursor")

    def __repr__(self) -> str:
        return "RedactionCandidateOcrVerifier(<private verification>)"
