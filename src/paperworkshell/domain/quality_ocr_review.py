"""Pure, content-free models for one page-correlated OCR quality observation.

The worker may inspect a bounded raster and private Tesseract TSV, but the
application boundary receives only a terminal status, a word count, and an
aggregate integer confidence.  Recognized text and geometry are deliberately
absent from every public model and serialized report.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.ocr import TRUSTED_TESSERACT_VERSION
from paperworkshell.domain.ocr_languages import resolve_ocr_language_set

QUALITY_OCR_REVIEW_VERSION: Final = 1
QUALITY_OCR_REVIEW_REPORT_VERSION: Final = 1
QUALITY_OCR_REVIEW_PROFILE_ID: Final = "quality-ocr-review-v1"
MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS: Final = 256
MAX_QUALITY_OCR_REVIEW_PIXELS: Final = 256 * 256
MAX_QUALITY_OCR_REVIEW_ROW_PADDING_BYTES: Final = 4_096
MAX_QUALITY_OCR_REVIEW_WORDS: Final = 32_768
MAX_QUALITY_OCR_REVIEW_REPORT_BYTES: Final = 8_192
MAX_QUALITY_OCR_REVIEW_TSV_BYTES: Final = 1024 * 1024
MAX_QUALITY_OCR_REVIEW_TSV_ROWS: Final = 32_768
QUALITY_OCR_REVIEW_TSV_COLUMNS: Final = 12

_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_CONTEXT: Final = re.compile(r"quality_ocr(?:\.[a-z][a-z0-9_]{0,63}){1,4}\Z")
_MAX_TOKEN_CODEPOINTS: Final = 128
_REPORT_FIELDS: Final = frozenset(
    {
        "report_version",
        "operation_id",
        "case_id",
        "manifest_revision",
        "page_id",
        "sample_sha256",
        "presentation_sha256",
        "language_set_id",
        "profile_id",
        "status",
        "recognized_word_count",
        "mean_confidence_milli",
        "tesseract_version",
    }
)


class QualityOcrReviewErrorCode(StrEnum):
    """Closed failure registry for the OCR-review capability."""

    MODEL_INVALID = "QUALITY_OCR_REVIEW_MODEL_INVALID"
    CANCELLED = "QUALITY_OCR_REVIEW_CANCELLED"
    TIMED_OUT = "QUALITY_OCR_REVIEW_TIMED_OUT"
    ENGINE_UNAVAILABLE = "QUALITY_OCR_REVIEW_ENGINE_UNAVAILABLE"
    ENGINE_FAILED = "QUALITY_OCR_REVIEW_ENGINE_FAILED"
    LIMIT_EXCEEDED = "QUALITY_OCR_REVIEW_LIMIT_EXCEEDED"
    RESULT_INVALID = "QUALITY_OCR_REVIEW_RESULT_INVALID"
    INTERNAL_ERROR = "QUALITY_OCR_REVIEW_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[QualityOcrReviewErrorCode, str]] = {
    QualityOcrReviewErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid OCR review data."),
    QualityOcrReviewErrorCode.CANCELLED: "OCR review was cancelled.",
    QualityOcrReviewErrorCode.TIMED_OUT: "OCR review exceeded its configured time limit.",
    QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE: (
        "The selected local OCR engine or language resources are unavailable."
    ),
    QualityOcrReviewErrorCode.ENGINE_FAILED: (
        "The local OCR engine could not complete this page review."
    ),
    QualityOcrReviewErrorCode.LIMIT_EXCEEDED: ("OCR review exceeded a configured safety limit."),
    QualityOcrReviewErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid OCR review result."
    ),
    QualityOcrReviewErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete OCR review safely."
    ),
}


@dataclass(slots=True, repr=False)
class QualityOcrReviewError(ValueError):
    """Sanitized failure that never retains pixels, text, paths, or tool output."""

    code: QualityOcrReviewErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not QualityOcrReviewErrorCode:
            self.code = QualityOcrReviewErrorCode.INTERNAL_ERROR
        if (
            type(self.context) is not str
            or len(self.context) > _MAX_TOKEN_CODEPOINTS
            or _CONTEXT.fullmatch(self.context) is None
        ):
            self.code = QualityOcrReviewErrorCode.INTERNAL_ERROR
            self.context = "quality_ocr.internal"
        self.safe_message = _SAFE_MESSAGES[self.code]
        ValueError.__init__(self, self.safe_message)

    def __repr__(self) -> str:
        return f"QualityOcrReviewError(code={self.code.value!r}, context={self.context!r})"


def reject_quality_ocr_review(code: QualityOcrReviewErrorCode, context: str) -> Never:
    """Raise one content-free OCR-review failure without an exception chain."""

    raise QualityOcrReviewError(code, context) from None


class QualityOcrReviewStatus(StrEnum):
    """Terminal content-free outcomes for one exact quality sample."""

    SUCCEEDED = "succeeded"
    NO_TEXT = "no_text"
    FAILED = "failed"


class _DuplicateReportFieldError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class QualityOcrReviewRequest:
    """One exact saved-revision raster and selected local OCR configuration."""

    case_id: UUID
    manifest_revision: int
    page_id: UUID
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixels: bytes = field(repr=False)
    presentation_sha256: str = field(repr=False)
    language_set_id: str
    profile_id: str = QUALITY_OCR_REVIEW_PROFILE_ID
    request_version: int = QUALITY_OCR_REVIEW_VERSION
    _sample_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or self.page_id == self.case_id
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.width_pixels) is not int
            or not 1 <= self.width_pixels <= MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS
            or type(self.height_pixels) is not int
            or not 1 <= self.height_pixels <= MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS
            or self.width_pixels * self.height_pixels > MAX_QUALITY_OCR_REVIEW_PIXELS
            or type(self.row_stride_bytes) is not int
            or not self.width_pixels * 4
            <= self.row_stride_bytes
            <= self.width_pixels * 4 + MAX_QUALITY_OCR_REVIEW_ROW_PADDING_BYTES
            or type(self.pixels) is not bytes
            or len(self.pixels) != self.row_stride_bytes * self.height_pixels
            or type(self.presentation_sha256) is not str
            or _SHA256.fullmatch(self.presentation_sha256) is None
            or type(self.language_set_id) is not str
            or type(self.profile_id) is not str
            or len(self.profile_id) > _MAX_TOKEN_CODEPOINTS
            or _TOKEN.fullmatch(self.profile_id) is None
            or self.profile_id != QUALITY_OCR_REVIEW_PROFILE_ID
            or type(self.request_version) is not int
            or self.request_version != QUALITY_OCR_REVIEW_VERSION
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.request",
            )
        try:
            resolve_ocr_language_set(self.language_set_id)
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.language",
            )
        object.__setattr__(self, "_sample_sha256", hashlib.sha256(self.pixels).hexdigest())

    @property
    def sample_byte_size(self) -> int:
        return len(self.pixels)

    @property
    def sample_sha256(self) -> str:
        return self._sample_sha256

    def __repr__(self) -> str:
        return (
            "QualityOcrReviewRequest("
            f"manifest_revision={self.manifest_revision!r}, "
            f"width_pixels={self.width_pixels!r}, "
            f"height_pixels={self.height_pixels!r}, "
            f"language_set_id={self.language_set_id!r}, "
            f"profile_id={self.profile_id!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class QualityOcrReviewReport:
    """Canonical path- and content-free worker result bound to one request."""

    operation_id: UUID
    case_id: UUID
    manifest_revision: int
    page_id: UUID
    sample_sha256: str = field(repr=False)
    presentation_sha256: str = field(repr=False)
    language_set_id: str
    profile_id: str
    status: QualityOcrReviewStatus
    recognized_word_count: int
    mean_confidence_milli: int | None
    tesseract_version: str = TRUSTED_TESSERACT_VERSION
    report_version: int = QUALITY_OCR_REVIEW_REPORT_VERSION

    def __post_init__(self) -> None:
        identifiers = (self.operation_id, self.case_id, self.page_id)
        if (
            any(type(value) is not UUID or value.version != 4 for value in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.sample_sha256) is not str
            or _SHA256.fullmatch(self.sample_sha256) is None
            or type(self.presentation_sha256) is not str
            or _SHA256.fullmatch(self.presentation_sha256) is None
            or type(self.language_set_id) is not str
            or type(self.profile_id) is not str
            or len(self.profile_id) > _MAX_TOKEN_CODEPOINTS
            or _TOKEN.fullmatch(self.profile_id) is None
            or self.profile_id != QUALITY_OCR_REVIEW_PROFILE_ID
            or type(self.status) is not QualityOcrReviewStatus
            or type(self.recognized_word_count) is not int
            or not 0 <= self.recognized_word_count <= MAX_QUALITY_OCR_REVIEW_WORDS
            or (
                self.mean_confidence_milli is not None
                and (
                    type(self.mean_confidence_milli) is not int
                    or not 0 <= self.mean_confidence_milli <= 100_000
                )
            )
            or self.tesseract_version != TRUSTED_TESSERACT_VERSION
            or type(self.report_version) is not int
            or self.report_version != QUALITY_OCR_REVIEW_REPORT_VERSION
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.report",
            )
        try:
            resolve_ocr_language_set(self.language_set_id)
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.report_language",
            )
        if self.status is QualityOcrReviewStatus.SUCCEEDED:
            if self.recognized_word_count < 1 or self.mean_confidence_milli is None:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.report_terminal",
                )
        elif self.recognized_word_count != 0 or self.mean_confidence_milli is not None:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.report_terminal",
            )

    def __repr__(self) -> str:
        return (
            "QualityOcrReviewReport("
            f"manifest_revision={self.manifest_revision!r}, "
            f"status={self.status.value!r}, "
            f"recognized_word_count={self.recognized_word_count!r}, "
            f"mean_confidence_milli={self.mean_confidence_milli!r}, "
            f"language_set_id={self.language_set_id!r})"
        )


def encode_quality_ocr_review_report(report: QualityOcrReviewReport) -> bytes:
    """Encode one canonical ASCII report that cannot contain recognized text."""

    if type(report) is not QualityOcrReviewReport:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.MODEL_INVALID,
            "quality_ocr.report_encode",
        )
    document = {
        "report_version": report.report_version,
        "operation_id": str(report.operation_id),
        "case_id": str(report.case_id),
        "manifest_revision": report.manifest_revision,
        "page_id": str(report.page_id),
        "sample_sha256": report.sample_sha256,
        "presentation_sha256": report.presentation_sha256,
        "language_set_id": report.language_set_id,
        "profile_id": report.profile_id,
        "status": report.status.value,
        "recognized_word_count": report.recognized_word_count,
        "mean_confidence_milli": report.mean_confidence_milli,
        "tesseract_version": report.tesseract_version,
    }
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.report_encode",
        )
    if not 1 <= len(payload) <= MAX_QUALITY_OCR_REVIEW_REPORT_BYTES:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_size",
        )
    return payload


def decode_quality_ocr_review_report(payload: bytes) -> QualityOcrReviewReport:
    """Decode only the exact bounded, content-free report schema."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_QUALITY_OCR_REVIEW_REPORT_BYTES:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_size",
        )
    try:
        raw = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_report_fields,
        )
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_decode",
        )
    if type(raw) is not dict or set(raw) != _REPORT_FIELDS:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_fields",
        )
    document = cast(dict[str, object], raw)
    try:
        operation_id = UUID(_require_string(document["operation_id"]))
        case_id = UUID(_require_string(document["case_id"]))
        page_id = UUID(_require_string(document["page_id"]))
        status = QualityOcrReviewStatus(_require_string(document["status"]))
    except KeyError, TypeError, ValueError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_values",
        )
    report = QualityOcrReviewReport(
        operation_id=operation_id,
        case_id=case_id,
        manifest_revision=_require_integer(document["manifest_revision"]),
        page_id=page_id,
        sample_sha256=_require_string(document["sample_sha256"]),
        presentation_sha256=_require_string(document["presentation_sha256"]),
        language_set_id=_require_string(document["language_set_id"]),
        profile_id=_require_string(document["profile_id"]),
        status=status,
        recognized_word_count=_require_integer(document["recognized_word_count"]),
        mean_confidence_milli=_require_optional_integer(document["mean_confidence_milli"]),
        tesseract_version=_require_string(document["tesseract_version"]),
        report_version=_require_integer(document["report_version"]),
    )
    if encode_quality_ocr_review_report(report) != payload:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_canonical",
        )
    return report


def _reject_duplicate_report_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateReportFieldError
        result[key] = value
    return result


def _require_string(value: object) -> str:
    if type(value) is not str:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_values",
        )
    return value


def _require_integer(value: object) -> int:
    if type(value) is not int:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_values",
        )
    return value


def _require_optional_integer(value: object) -> int | None:
    if value is None:
        return None
    return _require_integer(value)
