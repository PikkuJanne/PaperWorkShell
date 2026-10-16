"""Private, versioned intent and evidence for one reconstructed PDF page.

Reconstruction evidence never authorizes export or promotes saved removal marks.
OCR, whole-file sanitization and adversarial verification have separate gates.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, Self
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateError,
)
from paperworkshell.domain.pdf_export import (
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.redaction import MAX_REDACTION_MARKS, RedactionError, RedactionMark

RECONSTRUCTION_VERSION: Final = 1
RECONSTRUCTION_DPI: Final = 300
RECONSTRUCTION_STATUS: Final = "awaiting_verification"
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_FIELDS: Final = frozenset(
    {
        "case_id",
        "manifest_revision",
        "source_id",
        "page_id",
        "source_sha256",
        "source_byte_size",
        "source_page_index",
        "source_page_count",
        "marks",
        "crop",
        "rotation",
        "request_version",
    }
)
_REPORT_FIELDS: Final = frozenset(
    {
        "request_sha256",
        "page_id",
        "source_sha256",
        "output_sha256",
        "output_byte_size",
        "width_pixels",
        "height_pixels",
        "width_points",
        "height_points",
        "mark_count",
        "unsigned_derivative",
        "source_signature_detected",
        "report_version",
        "status",
    }
)


def _invalid() -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.model")


def _uuid(value: object) -> UUID:
    if type(value) is not UUID or value.version != 4:
        _invalid()
    return value


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        _invalid()
    try:
        parsed = UUID(str(value))
    except ValueError, AttributeError:
        _invalid()
    if str(parsed) != value:
        _invalid()
    return _uuid(parsed)


def _digest(value: object) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _invalid()


def _integer(value: object, minimum: int, maximum: int = MAX_SAFE_MANIFEST_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class RedactionReconstructionRequest:
    """One exact source page and nonempty, separately authored removal intent.

    Marks refer to the intrinsic-rotated full CropBox, before manifest crop and
    quarter-turn. No arbitrary transform, executable content or visual overlay
    can be substituted for a removal mark.
    """

    case_id: UUID
    manifest_revision: int
    source_id: UUID
    page_id: UUID
    source_sha256: str = field(repr=False)
    source_byte_size: int
    source_page_index: int
    source_page_count: int
    marks: tuple[RedactionMark, ...] = field(repr=False)
    crop: NormalizedPageBox = field(default_factory=NormalizedPageBox.full_page, repr=False)
    rotation: ClockwiseRotation = ClockwiseRotation.DEGREES_0
    request_version: int = RECONSTRUCTION_VERSION

    def __post_init__(self) -> None:
        for value in (self.case_id, self.source_id, self.page_id):
            _uuid(value)
        if len({self.case_id, self.source_id, self.page_id}) != 3:
            _invalid()
        _integer(self.manifest_revision, 0)
        _digest(self.source_sha256)
        _integer(self.source_byte_size, 1)
        _integer(self.source_page_count, 1, 4096)
        _integer(self.source_page_index, 0, self.source_page_count - 1)
        if (
            type(self.request_version) is not int
            or self.request_version != RECONSTRUCTION_VERSION
            or type(self.rotation) is not ClockwiseRotation
            or type(self.crop) is not NormalizedPageBox
            or type(self.marks) is not tuple
            or not 1 <= len(self.marks) <= MAX_REDACTION_MARKS
            or any(type(mark) is not RedactionMark for mark in self.marks)
        ):
            _invalid()
        try:
            self.crop.__post_init__()
            for mark in self.marks:
                mark.__post_init__()
        except RedactionError, PageCoordinateError:
            _invalid()
        if (
            any(mark.page_id != self.page_id for mark in self.marks)
            or len({mark.mark_id for mark in self.marks}) != len(self.marks)
            or any(
                mark.mark_id in {self.case_id, self.source_id, self.page_id} for mark in self.marks
            )
        ):
            _invalid()

    def __repr__(self) -> str:
        return f"RedactionReconstructionRequest(mark_count={len(self.marks)})"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(payload).hexdigest()

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "case_id": str(self.case_id),
            "manifest_revision": self.manifest_revision,
            "source_id": str(self.source_id),
            "page_id": str(self.page_id),
            "source_sha256": self.source_sha256,
            "source_byte_size": self.source_byte_size,
            "source_page_index": self.source_page_index,
            "source_page_count": self.source_page_count,
            "marks": [mark.to_manifest_record() for mark in self.marks],
            "crop": self.crop.to_manifest(),
            "rotation": int(self.rotation),
            "request_version": self.request_version,
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REQUEST_FIELDS:
            _invalid()
        marks, crop = value["marks"], value["crop"]
        if (
            type(marks) is not list
            or not 1 <= len(marks) <= MAX_REDACTION_MARKS
            or not isinstance(crop, Mapping)
        ):
            _invalid()
        try:
            return cls(
                case_id=_parse_uuid(value["case_id"]),
                manifest_revision=_integer(value["manifest_revision"], 0),
                source_id=_parse_uuid(value["source_id"]),
                page_id=_parse_uuid(value["page_id"]),
                source_sha256=value["source_sha256"],
                source_byte_size=value["source_byte_size"],
                source_page_index=value["source_page_index"],
                source_page_count=value["source_page_count"],
                marks=tuple(RedactionMark.from_manifest_record(mark) for mark in marks),
                crop=NormalizedPageBox.from_manifest(crop),
                rotation=ClockwiseRotation.from_degrees(value["rotation"]),
                request_version=value["request_version"],
            )
        except RedactionError, PageCoordinateError:
            _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class RedactionReconstructionReport:
    """Bounded page evidence; awaiting verification is the only valid status."""

    request_sha256: str = field(repr=False)
    page_id: UUID
    source_sha256: str = field(repr=False)
    output_sha256: str = field(repr=False)
    output_byte_size: int
    width_pixels: int
    height_pixels: int
    width_points: float = field(repr=False)
    height_points: float = field(repr=False)
    mark_count: int
    unsigned_derivative: bool = True
    source_signature_detected: bool = False
    report_version: int = RECONSTRUCTION_VERSION
    status: str = RECONSTRUCTION_STATUS

    def __post_init__(self) -> None:
        for value in (self.request_sha256, self.source_sha256, self.output_sha256):
            _digest(value)
        _uuid(self.page_id)
        _integer(self.output_byte_size, 1)
        _integer(self.width_pixels, 1, 32768)
        _integer(self.height_pixels, 1, 32768)
        _integer(self.mark_count, 1, MAX_REDACTION_MARKS)
        if (
            any(
                type(value) is not float or not math.isfinite(value) or not 0 < value <= 1e9
                for value in (self.width_points, self.height_points)
            )
            or self.unsigned_derivative is not True
            or type(self.source_signature_detected) is not bool
            or type(self.report_version) is not int
            or self.report_version != RECONSTRUCTION_VERSION
            or type(self.status) is not str
            or self.status != RECONSTRUCTION_STATUS
        ):
            _invalid()

    def __repr__(self) -> str:
        return f"RedactionReconstructionReport(status='awaiting_verification', mark_count={self.mark_count})"

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            name: str(value) if name == "page_id" else value
            for name in _REPORT_FIELDS
            for value in (getattr(self, name),)
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REPORT_FIELDS:
            _invalid()
        return cls(
            request_sha256=value["request_sha256"],
            page_id=_parse_uuid(value["page_id"]),
            source_sha256=value["source_sha256"],
            output_sha256=value["output_sha256"],
            output_byte_size=value["output_byte_size"],
            width_pixels=value["width_pixels"],
            height_pixels=value["height_pixels"],
            width_points=value["width_points"],
            height_points=value["height_points"],
            mark_count=value["mark_count"],
            unsigned_derivative=value["unsigned_derivative"],
            source_signature_detected=value["source_signature_detected"],
            report_version=value["report_version"],
            status=value["status"],
        )


def validate_reconstruction_report(
    request: RedactionReconstructionRequest, report: RedactionReconstructionReport
) -> None:
    if (
        type(request) is not RedactionReconstructionRequest
        or type(report) is not RedactionReconstructionReport
    ):
        _invalid()
    request.__post_init__()
    report.__post_init__()
    if (
        report.request_sha256 != request.fingerprint
        or report.page_id != request.page_id
        or report.source_sha256 != request.source_sha256
        or report.mark_count != len(request.marks)
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.report_binding")
