"""Pure, path-free contracts for bounded local OCR processing.

The models deliberately contain no document text, filenames, paths, command
arguments, or native-library objects.  ``OcrCandidateArtifact`` describes an
opaque, operation-scoped PDF candidate; it does not claim that searchability
has been verified or that the candidate has been committed to a case.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

OCR_REQUEST_VERSION: Final = 1
OCR_PROGRESS_VERSION: Final = 1
OCR_CAPABILITY_VERSION: Final = 1
OCR_CANDIDATE_ARTIFACT_VERSION: Final = 1
OCR_WORKER_REPORT_VERSION: Final = 1
OCR_ENGINE_PROFILE_VERSION: Final = 1
OCR_ENGINE_IMAGE_DPI: Final = 300
MAX_OCR_WORKER_REPORT_BYTES: Final = 16 * 1024
MAX_OCR_PAGES: Final = 4_096
MAX_OCR_LANGUAGE_SETS: Final = 64
MAX_OCR_SAFE_INTEGER: Final = 9_007_199_254_740_991
DEFAULT_OCR_PROFILE_ID: Final = "searchable-pdf-v1"
TRUSTED_OCRMYPDF_VERSION: Final = "17.10.0"
TRUSTED_TESSERACT_VERSION: Final = "5.5.3"

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_OPAQUE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_VERSION_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_OCR_WORKER_REPORT_MEMBERS: Final = frozenset(
    {
        "report_version",
        "operation_id",
        "source_id",
        "output_object_id",
        "source_kind",
        "page_count",
        "processed_page_count",
        "language_set_id",
        "profile_id",
        "ocrmypdf_version",
        "tesseract_version",
        "engine_profile_sha256",
        "output_byte_size",
        "output_sha256",
    }
)


class OcrSourceKind(StrEnum):
    """Closed source encodings accepted by ``ocr.process`` version 1."""

    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"


class OcrSourceSignatureState(StrEnum):
    """Conservative signature-marker facts available before OCR."""

    NOT_APPLICABLE = "not_applicable"
    NONE_DETECTED = "none_detected"
    MARKERS_DETECTED = "markers_detected"
    UNKNOWN = "unknown"


class OcrSourceEncryptionState(StrEnum):
    """Conservative encryption facts available before OCR."""

    NOT_APPLICABLE = "not_applicable"
    UNENCRYPTED = "unencrypted"
    ENCRYPTED = "encrypted"
    UNKNOWN = "unknown"


class OcrProgressPhase(StrEnum):
    """Content-free application phases for one OCR attempt."""

    PREPARING = "preparing"
    RECOGNIZING = "recognizing"
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    COMPLETE = "complete"


class OcrErrorCode(StrEnum):
    """Closed registry of sanitized OCR-domain failures."""

    MODEL_INVALID = "OCR_MODEL_INVALID"
    SOURCE_INVALID = "OCR_SOURCE_INVALID"
    SOURCE_ENCRYPTED = "OCR_SOURCE_ENCRYPTED"
    SOURCE_ENCRYPTION_UNKNOWN = "OCR_SOURCE_ENCRYPTION_UNKNOWN"
    SIGNED_SOURCE_REQUIRES_CONFIRMATION = "OCR_SIGNED_SOURCE_REQUIRES_CONFIRMATION"
    LANGUAGE_UNAVAILABLE = "OCR_LANGUAGE_UNAVAILABLE"
    LIMIT_EXCEEDED = "OCR_LIMIT_EXCEEDED"
    CANCELLED = "OCR_CANCELLED"
    TIMED_OUT = "OCR_TIMED_OUT"
    ENGINE_UNAVAILABLE = "OCR_ENGINE_UNAVAILABLE"
    ENGINE_FAILED = "OCR_ENGINE_FAILED"
    OUTPUT_INVALID = "OCR_OUTPUT_INVALID"
    RESULT_INVALID = "OCR_RESULT_INVALID"
    INTERNAL_ERROR = "OCR_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[OcrErrorCode, str]] = {
    OcrErrorCode.MODEL_INVALID: "PaperWorkShell received invalid OCR-operation data.",
    OcrErrorCode.SOURCE_INVALID: "The selected OCR source is not available or supported.",
    OcrErrorCode.SOURCE_ENCRYPTED: "The selected PDF is encrypted and cannot be processed.",
    OcrErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
        "PaperWorkShell could not confirm that the selected PDF is unencrypted."
    ),
    OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION: (
        "The selected PDF may contain a digital signature and needs review before modification."
    ),
    OcrErrorCode.LANGUAGE_UNAVAILABLE: "The configured OCR language resources are unavailable.",
    OcrErrorCode.LIMIT_EXCEEDED: "The OCR request exceeds a configured safety limit.",
    OcrErrorCode.CANCELLED: "OCR was cancelled before completion.",
    OcrErrorCode.TIMED_OUT: "OCR exceeded its configured time limit.",
    OcrErrorCode.ENGINE_UNAVAILABLE: "A required local OCR component is unavailable.",
    OcrErrorCode.ENGINE_FAILED: "The local OCR component could not complete safely.",
    OcrErrorCode.OUTPUT_INVALID: "The OCR output candidate could not be verified.",
    OcrErrorCode.RESULT_INVALID: "PaperWorkShell received an invalid OCR result.",
    OcrErrorCode.INTERNAL_ERROR: "PaperWorkShell could not complete OCR safely.",
}


@dataclass(slots=True, repr=False)
class OcrError(ValueError):
    """Stable OCR failure containing no source or recognized document data."""

    code: OcrErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not OcrErrorCode:
            self.code = OcrErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "ocr.process"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"OcrError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_ocr(code: OcrErrorCode, context: str) -> Never:
    """Raise one closed OCR failure without retaining a raw exception."""

    error = OcrError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def ocr_engine_profile_sha256(ocrmypdf_version: str, tesseract_version: str) -> str:
    """Hash the exact local OCR execution profile without document values."""

    if (
        type(ocrmypdf_version) is not str
        or _VERSION_TOKEN.fullmatch(ocrmypdf_version) is None
        or type(tesseract_version) is not str
        or _VERSION_TOKEN.fullmatch(tesseract_version) is None
    ):
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.engine_profile")
    document = {
        "engine_profile_version": OCR_ENGINE_PROFILE_VERSION,
        "image_dpi": OCR_ENGINE_IMAGE_DPI,
        "jobs": 1,
        "ocrmypdf": ocrmypdf_version,
        "optimize": 0,
        "output_type": "pdf",
        "pdf_renderer": "fpdf2",
        "rasterizer": "pypdfium",
        "tesseract": tesseract_version,
        "use_threads": True,
    }
    payload = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


TRUSTED_OCR_ENGINE_PROFILE_SHA256: Final = ocr_engine_profile_sha256(
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
)


def _require_uuid4(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_ocr(OcrErrorCode.MODEL_INVALID, context)
    return value


def _require_sha256(value: object, context: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        reject_ocr(OcrErrorCode.MODEL_INVALID, context)
    return value


def _require_opaque_token(value: object, context: str) -> str:
    if type(value) is not str or len(value) > 128 or _OPAQUE_TOKEN.fullmatch(value) is None:
        reject_ocr(OcrErrorCode.MODEL_INVALID, context)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class OcrProcessRequest:
    """Immutable source facts and opaque configuration IDs for one OCR run."""

    case_id: UUID
    source_id: UUID
    source_kind: OcrSourceKind
    source_byte_size: int
    source_page_count: int
    source_sha256: str = field(repr=False)
    signature_state: OcrSourceSignatureState
    encryption_state: OcrSourceEncryptionState
    language_set_id: str
    profile_id: str = DEFAULT_OCR_PROFILE_ID
    request_version: int = OCR_REQUEST_VERSION

    def __post_init__(self) -> None:
        case_id = _require_uuid4(self.case_id, "ocr.request_case")
        source_id = _require_uuid4(self.source_id, "ocr.request_source")
        _require_sha256(self.source_sha256, "ocr.request_digest")
        _require_opaque_token(self.language_set_id, "ocr.request_language_set")
        _require_opaque_token(self.profile_id, "ocr.request_profile")
        if (
            case_id == source_id
            or type(self.source_kind) is not OcrSourceKind
            or type(self.source_byte_size) is not int
            or not 1 <= self.source_byte_size <= MAX_OCR_SAFE_INTEGER
            or type(self.source_page_count) is not int
            or not 1 <= self.source_page_count <= MAX_OCR_PAGES
            or type(self.signature_state) is not OcrSourceSignatureState
            or type(self.encryption_state) is not OcrSourceEncryptionState
            or type(self.request_version) is not int
            or self.request_version != OCR_REQUEST_VERSION
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.request")
        if self.source_kind is OcrSourceKind.PDF:
            if self.encryption_state is OcrSourceEncryptionState.ENCRYPTED:
                reject_ocr(OcrErrorCode.SOURCE_ENCRYPTED, "ocr.request_encryption")
            if self.encryption_state is not OcrSourceEncryptionState.UNENCRYPTED:
                reject_ocr(OcrErrorCode.SOURCE_ENCRYPTION_UNKNOWN, "ocr.request_encryption")
            if self.signature_state is not OcrSourceSignatureState.NONE_DETECTED:
                reject_ocr(
                    OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION,
                    "ocr.request_signature",
                )
        elif (
            self.source_page_count != 1
            or self.signature_state is not OcrSourceSignatureState.NOT_APPLICABLE
            or self.encryption_state is not OcrSourceEncryptionState.NOT_APPLICABLE
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.request_image_source")

    def __repr__(self) -> str:
        return (
            "OcrProcessRequest("
            f"case_id={self.case_id!r}, source_id={self.source_id!r}, "
            f"source_kind={self.source_kind!r}, source_byte_size={self.source_byte_size!r}, "
            f"source_page_count={self.source_page_count!r}, "
            f"signature_state={self.signature_state!r}, "
            f"encryption_state={self.encryption_state!r}, request_version={self.request_version!r})"
        )


@dataclass(frozen=True, slots=True)
class OcrProgress:
    """Monotonic, content-free page progress suitable for UI announcements."""

    operation_id: UUID
    phase: OcrProgressPhase
    completed_pages: int
    total_pages: int
    progress_version: int = OCR_PROGRESS_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.operation_id, "ocr.progress_operation")
        if (
            type(self.phase) is not OcrProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= MAX_OCR_PAGES
            or not 0 <= self.completed_pages <= self.total_pages
            or (self.phase is OcrProgressPhase.PREPARING and self.completed_pages != 0)
            or (
                self.phase
                in {
                    OcrProgressPhase.FINALIZING,
                    OcrProgressPhase.VALIDATING,
                    OcrProgressPhase.COMPLETE,
                }
                and self.completed_pages != self.total_pages
            )
            or type(self.progress_version) is not int
            or self.progress_version != OCR_PROGRESS_VERSION
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.progress")


@dataclass(frozen=True, slots=True, repr=False)
class OcrCandidateArtifact:
    """Authenticated metadata for one volatile opaque PDF candidate."""

    operation_id: UUID
    source_id: UUID
    output_id: UUID
    byte_size: int
    page_count: int
    language_set_id: str
    profile_id: str
    sha256: str = field(repr=False)
    artifact_version: int = OCR_CANDIDATE_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        identifiers = tuple(
            _require_uuid4(value, "ocr.artifact_ids")
            for value in (self.operation_id, self.source_id, self.output_id)
        )
        _require_sha256(self.sha256, "ocr.artifact_digest")
        _require_opaque_token(self.language_set_id, "ocr.artifact_language_set")
        _require_opaque_token(self.profile_id, "ocr.artifact_profile")
        if (
            len(set(identifiers)) != len(identifiers)
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_OCR_SAFE_INTEGER
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_OCR_PAGES
            or type(self.artifact_version) is not int
            or self.artifact_version != OCR_CANDIDATE_ARTIFACT_VERSION
        ):
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.artifact")

    def __repr__(self) -> str:
        return (
            "OcrCandidateArtifact("
            f"operation_id={self.operation_id!r}, source_id={self.source_id!r}, "
            f"output_id={self.output_id!r}, byte_size={self.byte_size!r}, "
            f"page_count={self.page_count!r}, artifact_version={self.artifact_version!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class OcrWorkerReport:
    """Content-free worker report correlated to one opaque PDF candidate."""

    operation_id: UUID
    source_id: UUID
    output_object_id: UUID
    source_kind: OcrSourceKind
    page_count: int
    processed_page_count: int
    language_set_id: str
    profile_id: str
    ocrmypdf_version: str
    tesseract_version: str
    engine_profile_sha256: str = field(repr=False)
    output_byte_size: int
    output_sha256: str = field(repr=False)
    report_version: int = OCR_WORKER_REPORT_VERSION

    def __post_init__(self) -> None:
        identifiers = (self.operation_id, self.source_id, self.output_object_id)
        if (
            any(not _is_uuid4(value) for value in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or type(self.source_kind) is not OcrSourceKind
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_OCR_PAGES
            or type(self.processed_page_count) is not int
            or self.processed_page_count != self.page_count
            or not _is_opaque_token(self.language_set_id)
            or not _is_opaque_token(self.profile_id)
            or not _is_version_token(self.ocrmypdf_version)
            or not _is_version_token(self.tesseract_version)
            or not _is_sha256(self.engine_profile_sha256)
            or type(self.output_byte_size) is not int
            or not 1 <= self.output_byte_size <= MAX_OCR_SAFE_INTEGER
            or not _is_sha256(self.output_sha256)
            or type(self.report_version) is not int
            or self.report_version != OCR_WORKER_REPORT_VERSION
        ):
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.worker_report")

    def __repr__(self) -> str:
        return (
            "OcrWorkerReport("
            f"operation_id={self.operation_id!r}, source_id={self.source_id!r}, "
            f"output_object_id={self.output_object_id!r}, source_kind={self.source_kind!r}, "
            f"page_count={self.page_count!r}, "
            f"processed_page_count={self.processed_page_count!r}, "
            f"output_byte_size={self.output_byte_size!r}, "
            f"report_version={self.report_version!r})"
        )


def encode_ocr_worker_report(value: OcrWorkerReport) -> bytes:
    """Encode one canonical bounded OCR worker report as ASCII JSON."""

    if type(value) is not OcrWorkerReport:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_encode")
    document = {
        "report_version": value.report_version,
        "operation_id": str(value.operation_id),
        "source_id": str(value.source_id),
        "output_object_id": str(value.output_object_id),
        "source_kind": value.source_kind.value,
        "page_count": value.page_count,
        "processed_page_count": value.processed_page_count,
        "language_set_id": value.language_set_id,
        "profile_id": value.profile_id,
        "ocrmypdf_version": value.ocrmypdf_version,
        "tesseract_version": value.tesseract_version,
        "engine_profile_sha256": value.engine_profile_sha256,
        "output_byte_size": value.output_byte_size,
        "output_sha256": value.output_sha256,
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
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_encode")
    if not 1 <= len(payload) <= MAX_OCR_WORKER_REPORT_BYTES:
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.report_size")
    return payload


def decode_ocr_worker_report(payload: bytes) -> OcrWorkerReport:
    """Decode one exact canonical report and re-run every typed invariant."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_OCR_WORKER_REPORT_BYTES:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_size")
    try:
        decoded = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_report_object,
            parse_int=_bounded_report_integer,
            parse_float=_reject_report_number,
            parse_constant=_reject_report_number,
        )
    except OcrError:
        raise
    except Exception:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_json")
    document = _report_object(decoded, _OCR_WORKER_REPORT_MEMBERS, "ocr.report_fields")
    version = _report_integer(document["report_version"], "ocr.report_version")
    if version != OCR_WORKER_REPORT_VERSION:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_version")
    try:
        source_kind = OcrSourceKind(_report_string(document["source_kind"], "ocr.report_source"))
    except ValueError:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_source")
    report = OcrWorkerReport(
        operation_id=_report_uuid(document["operation_id"], "ocr.report_operation"),
        source_id=_report_uuid(document["source_id"], "ocr.report_source_id"),
        output_object_id=_report_uuid(document["output_object_id"], "ocr.report_output"),
        source_kind=source_kind,
        page_count=_report_integer(document["page_count"], "ocr.report_pages"),
        processed_page_count=_report_integer(
            document["processed_page_count"], "ocr.report_processed_pages"
        ),
        language_set_id=_report_string(document["language_set_id"], "ocr.report_language_set"),
        profile_id=_report_string(document["profile_id"], "ocr.report_profile"),
        ocrmypdf_version=_report_string(
            document["ocrmypdf_version"], "ocr.report_ocrmypdf_version"
        ),
        tesseract_version=_report_string(
            document["tesseract_version"], "ocr.report_tesseract_version"
        ),
        engine_profile_sha256=_report_string(
            document["engine_profile_sha256"], "ocr.report_engine_profile"
        ),
        output_byte_size=_report_integer(document["output_byte_size"], "ocr.report_output_size"),
        output_sha256=_report_string(document["output_sha256"], "ocr.report_output_digest"),
        report_version=version,
    )
    if encode_ocr_worker_report(report) != payload:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_canonical")
    return report


def _unique_report_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_duplicate")
        result[key] = value
    return result


def _bounded_report_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_OCR_SAFE_INTEGER <= parsed <= MAX_OCR_SAFE_INTEGER:
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_integer")
    return parsed


def _reject_report_number(_value: str) -> None:
    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_number")


def _report_object(value: object, members: frozenset[str], context: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != members:
        reject_ocr(OcrErrorCode.RESULT_INVALID, context)
    return cast(dict[str, object], value)


def _report_integer(value: object, context: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_OCR_SAFE_INTEGER:
        reject_ocr(OcrErrorCode.RESULT_INVALID, context)
    return value


def _report_string(value: object, context: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        reject_ocr(OcrErrorCode.RESULT_INVALID, context)
    return value


def _report_uuid(value: object, context: str) -> UUID:
    text = _report_string(value, context)
    try:
        parsed = UUID(text)
    except ValueError, AttributeError:
        reject_ocr(OcrErrorCode.RESULT_INVALID, context)
    if parsed.version != 4 or str(parsed) != text:
        reject_ocr(OcrErrorCode.RESULT_INVALID, context)
    return parsed


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_opaque_token(value: object) -> bool:
    return type(value) is str and len(value) <= 128 and _OPAQUE_TOKEN.fullmatch(value) is not None


def _is_version_token(value: object) -> bool:
    return type(value) is str and _VERSION_TOKEN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class OcrEngineCapability:
    """Content-free discovery result for one fixed local OCR installation."""

    ocrmypdf_version: str
    tesseract_version: str
    language_set_ids: tuple[str, ...]
    capability_version: int = OCR_CAPABILITY_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.ocrmypdf_version) is not str
            or _VERSION_TOKEN.fullmatch(self.ocrmypdf_version) is None
            or type(self.tesseract_version) is not str
            or _VERSION_TOKEN.fullmatch(self.tesseract_version) is None
            or type(self.language_set_ids) is not tuple
            or not 1 <= len(self.language_set_ids) <= MAX_OCR_LANGUAGE_SETS
            or len(set(self.language_set_ids)) != len(self.language_set_ids)
            or type(self.capability_version) is not int
            or self.capability_version != OCR_CAPABILITY_VERSION
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.capability")
        for language_set_id in self.language_set_ids:
            _require_opaque_token(language_set_id, "ocr.capability_language_set")
