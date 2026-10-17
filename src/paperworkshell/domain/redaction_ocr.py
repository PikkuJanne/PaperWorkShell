"""Private OCR evidence bound to exact reconstructed pixels and removal intent.

The fixed profile recognizes only the fresh, opaque masked reconstruction. OCR
completion leaves the page unsigned and awaiting the later redaction checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, Self

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.ocr import (
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
)
from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    resolve_ocr_language_set,
)
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_reconstruction import (
    RECONSTRUCTION_STATUS,
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
    validate_reconstruction_report,
)

REDACTION_OCR_VERSION: Final = 1
REDACTION_OCR_PROFILE_ID: Final = "redacted-page-ocr-v1"
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_FIELDS: Final = frozenset(
    {"reconstruction", "language_set_id", "profile_id", "request_version"}
)
_REPORT_FIELDS: Final = frozenset(
    {
        "request_sha256",
        "reconstruction",
        "output_sha256",
        "output_byte_size",
        "language_set_id",
        "profile_id",
        "ocrmypdf_version",
        "tesseract_version",
        "report_version",
        "status",
        "unsigned_derivative",
    }
)


def _invalid() -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_ocr.model")


def _profile(language_set_id: object, profile_id: object) -> None:
    if type(language_set_id) is not str or profile_id != REDACTION_OCR_PROFILE_ID:
        _invalid()
    try:
        resolve_ocr_language_set(language_set_id)
    except OcrError:
        _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class RedactionOcrRequest:
    """One source page, exact marks and a closed installed-language selection."""

    reconstruction: RedactionReconstructionRequest = field(repr=False)
    language_set_id: str = DEFAULT_OCR_LANGUAGE_SET_ID
    profile_id: str = REDACTION_OCR_PROFILE_ID
    request_version: int = REDACTION_OCR_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.reconstruction) is not RedactionReconstructionRequest
            or type(self.request_version) is not int
            or self.request_version != REDACTION_OCR_VERSION
            or type(self.profile_id) is not str
        ):
            _invalid()
        self.reconstruction.__post_init__()
        _profile(self.language_set_id, self.profile_id)

    def __repr__(self) -> str:
        return "RedactionOcrRequest(profile='redacted-page-ocr-v1')"

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "reconstruction": self.reconstruction.to_record(),
            "language_set_id": self.language_set_id,
            "profile_id": self.profile_id,
            "request_version": self.request_version,
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REQUEST_FIELDS:
            _invalid()
        return cls(
            reconstruction=RedactionReconstructionRequest.from_record(value["reconstruction"]),
            language_set_id=value["language_set_id"],
            profile_id=value["profile_id"],
            request_version=value["request_version"],
        )


@dataclass(frozen=True, slots=True, repr=False)
class RedactionOcrReport:
    """OCR completed against masked pixels; this is never release verification."""

    request_sha256: str = field(repr=False)
    reconstruction: RedactionReconstructionReport = field(repr=False)
    output_sha256: str = field(repr=False)
    output_byte_size: int
    language_set_id: str
    ocrmypdf_version: str
    tesseract_version: str
    profile_id: str = REDACTION_OCR_PROFILE_ID
    report_version: int = REDACTION_OCR_VERSION
    status: str = RECONSTRUCTION_STATUS
    unsigned_derivative: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.reconstruction) is not RedactionReconstructionReport
            or any(
                type(value) is not str or _SHA256.fullmatch(value) is None
                for value in (self.request_sha256, self.output_sha256)
            )
            or type(self.output_byte_size) is not int
            or not 1 <= self.output_byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.profile_id) is not str
            or type(self.report_version) is not int
            or self.report_version != REDACTION_OCR_VERSION
            or type(self.status) is not str
            or self.status != RECONSTRUCTION_STATUS
            or self.unsigned_derivative is not True
            or type(self.ocrmypdf_version) is not str
            or self.ocrmypdf_version != TRUSTED_OCRMYPDF_VERSION
            or type(self.tesseract_version) is not str
            or self.tesseract_version != TRUSTED_TESSERACT_VERSION
        ):
            _invalid()
        self.reconstruction.__post_init__()
        _profile(self.language_set_id, self.profile_id)

    def __repr__(self) -> str:
        return "RedactionOcrReport(status='awaiting_verification', unsigned_derivative=True)"

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            name: self.reconstruction.to_record()
            if name == "reconstruction"
            else getattr(self, name)
            for name in _REPORT_FIELDS
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REPORT_FIELDS:
            _invalid()
        return cls(
            request_sha256=value["request_sha256"],
            reconstruction=RedactionReconstructionReport.from_record(value["reconstruction"]),
            output_sha256=value["output_sha256"],
            output_byte_size=value["output_byte_size"],
            language_set_id=value["language_set_id"],
            profile_id=value["profile_id"],
            ocrmypdf_version=value["ocrmypdf_version"],
            tesseract_version=value["tesseract_version"],
            report_version=value["report_version"],
            status=value["status"],
            unsigned_derivative=value["unsigned_derivative"],
        )


def validate_redaction_ocr_report(request: RedactionOcrRequest, report: RedactionOcrReport) -> None:
    if type(request) is not RedactionOcrRequest or type(report) is not RedactionOcrReport:
        _invalid()
    request.__post_init__()
    report.__post_init__()
    validate_reconstruction_report(request.reconstruction, report.reconstruction)
    if (
        report.request_sha256 != request.fingerprint
        or report.language_set_id != request.language_set_id
        or report.profile_id != request.profile_id
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.report_binding")
