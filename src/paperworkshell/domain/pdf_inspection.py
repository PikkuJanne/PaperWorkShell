"""Pure, path-free contracts for conservative PDF structural inspection."""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

MAX_PDF_INSPECTION_REPORT_BYTES: Final = 4_096
MAX_PDF_INSPECTION_FRAME_BYTES: Final = MAX_PDF_INSPECTION_REPORT_BYTES + 36
MAX_PDF_INSPECTION_OBJECTS: Final = 1_000_000
MAX_PDF_INSPECTION_PAGES: Final = 4_096

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_REPORT_KEYS: Final = frozenset(
    {
        "report_version",
        "open_state",
        "page_count",
        "object_count",
        "encryption_state",
        "structural_state",
        "features",
    }
)
_V1_FEATURE_KEYS: Final = frozenset(
    {
        "signature_markers",
        "attachments",
        "interactive_content",
        "actions",
        "scripts",
        "xfa",
    }
)
_V2_FEATURE_KEYS: Final = _V1_FEATURE_KEYS | {"metadata", "annotations", "form_fields"}


class PdfOpenState(StrEnum):
    """Conservative parser outcome without claiming document validity."""

    OPENED = "opened"
    ENCRYPTED = "encrypted"
    MALFORMED = "malformed"


class PdfEncryptionState(StrEnum):
    """Encryption state established without retaining or guessing passwords."""

    UNENCRYPTED = "unencrypted"
    ENCRYPTED = "encrypted"
    UNKNOWN = "unknown"


class PdfFeatureState(StrEnum):
    """Whether a structural marker was conservatively observed."""

    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class PdfStructuralState(StrEnum):
    """Sanitized qpdf structural-check outcome."""

    CLEAN = "clean"
    WARNINGS = "warnings"
    FAILED = "failed"


class PdfInspectionErrorCode(StrEnum):
    """Closed registry of safe PDF-inspection failures."""

    MODEL_INVALID = "PDF_INSPECTION_MODEL_INVALID"
    SOURCE_INVALID = "PDF_INSPECTION_SOURCE_INVALID"
    SOURCE_CHANGED = "PDF_INSPECTION_SOURCE_CHANGED"
    LIMIT_EXCEEDED = "PDF_INSPECTION_LIMIT_EXCEEDED"
    CANCELLED = "PDF_INSPECTION_CANCELLED"
    TIMED_OUT = "PDF_INSPECTION_TIMED_OUT"
    DEPENDENCY_UNAVAILABLE = "PDF_INSPECTION_DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_FAILED = "PDF_INSPECTION_DEPENDENCY_FAILED"
    HANDLE_INVALID = "PDF_INSPECTION_HANDLE_INVALID"
    REPORT_INVALID = "PDF_INSPECTION_REPORT_INVALID"
    WORKER_FAILED = "PDF_INSPECTION_WORKER_FAILED"
    INTERNAL_ERROR = "PDF_INSPECTION_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[PdfInspectionErrorCode, str]] = {
    PdfInspectionErrorCode.MODEL_INVALID: "PaperWorkShell received invalid PDF-inspection data.",
    PdfInspectionErrorCode.SOURCE_INVALID: "The selected source is not a supported PDF.",
    PdfInspectionErrorCode.SOURCE_CHANGED: "The selected PDF changed during inspection.",
    PdfInspectionErrorCode.LIMIT_EXCEEDED: (
        "The selected PDF exceeds a configured inspection limit."
    ),
    PdfInspectionErrorCode.CANCELLED: "PDF inspection was cancelled before completion.",
    PdfInspectionErrorCode.TIMED_OUT: "PDF inspection exceeded its configured time limit.",
    PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE: (
        "A required PDF inspection component is not available."
    ),
    PdfInspectionErrorCode.DEPENDENCY_FAILED: (
        "A required PDF inspection component could not complete safely."
    ),
    PdfInspectionErrorCode.HANDLE_INVALID: (
        "The worker did not receive valid authority for the requested case object."
    ),
    PdfInspectionErrorCode.REPORT_INVALID: (
        "PaperWorkShell received an invalid PDF-inspection report."
    ),
    PdfInspectionErrorCode.WORKER_FAILED: "The document worker could not inspect the PDF safely.",
    PdfInspectionErrorCode.INTERNAL_ERROR: "PaperWorkShell could not inspect the PDF safely.",
}


@dataclass(slots=True, repr=False)
class PdfInspectionError(ValueError):
    """Stable failure containing no path, filename, document value, or raw exception."""

    code: PdfInspectionErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PdfInspectionErrorCode:
            self.code = PdfInspectionErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "pdf.inspection"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PdfInspectionError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_pdf_inspection(code: PdfInspectionErrorCode, context: str) -> Never:
    """Raise one closed, sanitized PDF-inspection failure."""

    raise PdfInspectionError(code=code, context=context)


@dataclass(frozen=True, slots=True)
class PdfInspectionLimits:
    """Operation limits applied in addition to the worker Job Object."""

    maximum_pages: int
    maximum_objects: int
    maximum_input_bytes: int
    maximum_temporary_bytes: int
    maximum_output_bytes: int
    maximum_tool_output_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_pages,
            self.maximum_objects,
            self.maximum_input_bytes,
            self.maximum_temporary_bytes,
            self.maximum_output_bytes,
            self.maximum_tool_output_bytes,
        )
        if (
            any(type(value) is not int or value < 1 for value in values)
            or self.maximum_pages > MAX_PDF_INSPECTION_PAGES
            or self.maximum_objects > MAX_PDF_INSPECTION_OBJECTS
            or self.maximum_output_bytes < MAX_PDF_INSPECTION_REPORT_BYTES
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.limits")


@dataclass(frozen=True, slots=True)
class PdfFeatureSummary:
    """Conservative structural markers; these are not authenticity or safety claims."""

    signature_markers: PdfFeatureState
    attachments: PdfFeatureState
    interactive_content: PdfFeatureState
    actions: PdfFeatureState
    scripts: PdfFeatureState
    xfa: PdfFeatureState
    metadata: PdfFeatureState = PdfFeatureState.UNKNOWN
    annotations: PdfFeatureState = PdfFeatureState.UNKNOWN
    form_fields: PdfFeatureState = PdfFeatureState.UNKNOWN

    def __post_init__(self) -> None:
        if any(type(value) is not PdfFeatureState for value in self.values):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.features")

    @property
    def values(self) -> tuple[PdfFeatureState, ...]:
        return (
            self.signature_markers,
            self.attachments,
            self.interactive_content,
            self.actions,
            self.scripts,
            self.xfa,
            self.metadata,
            self.annotations,
            self.form_fields,
        )

    @classmethod
    def unknown(cls) -> PdfFeatureSummary:
        """Return a complete unknown summary for an unopened document."""

        return cls(*(PdfFeatureState.UNKNOWN for _ in range(9)))


@dataclass(frozen=True, slots=True)
class PdfInspectionResult:
    """Typed structural inspection result safe to cross into application code."""

    open_state: PdfOpenState
    page_count: int | None
    object_count: int | None
    encryption_state: PdfEncryptionState
    structural_state: PdfStructuralState
    features: PdfFeatureSummary
    report_version: int = 2

    def __post_init__(self) -> None:
        if (
            type(self.open_state) is not PdfOpenState
            or type(self.encryption_state) is not PdfEncryptionState
            or type(self.structural_state) is not PdfStructuralState
            or type(self.features) is not PdfFeatureSummary
            or type(self.report_version) is not int
            or self.report_version not in (1, 2)
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.result")
        if self.report_version == 1 and any(
            value is not PdfFeatureState.UNKNOWN for value in self.features.values[6:]
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.v1_features")
        if self.open_state is PdfOpenState.OPENED:
            if (
                type(self.page_count) is not int
                or not 1 <= self.page_count <= MAX_PDF_INSPECTION_PAGES
                or type(self.object_count) is not int
                or not 1 <= self.object_count <= MAX_PDF_INSPECTION_OBJECTS
                or self.encryption_state is not PdfEncryptionState.UNENCRYPTED
                or (
                    self.report_version == 1 and PdfFeatureState.UNKNOWN in self.features.values[:6]
                )
            ):
                reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.opened")
            return
        if self.page_count is not None or self.object_count is not None:
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.unopened_counts")
        if self.features != PdfFeatureSummary.unknown():
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.unopened_features")
        if self.open_state is PdfOpenState.ENCRYPTED:
            if (
                self.encryption_state is not PdfEncryptionState.ENCRYPTED
                or self.structural_state is not PdfStructuralState.FAILED
            ):
                reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.encrypted")
        elif (
            self.encryption_state is not PdfEncryptionState.UNKNOWN
            or self.structural_state is not PdfStructuralState.FAILED
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.malformed")


@dataclass(frozen=True, slots=True)
class PdfInspectionEnvelope:
    """Correlation binding for one out-of-band worker result frame."""

    operation_id: UUID
    source_object_id: UUID
    result: PdfInspectionResult

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not UUID
            or self.operation_id.version != 4
            or type(self.source_object_id) is not UUID
            or self.source_object_id.version != 4
            or self.operation_id == self.source_object_id
            or type(self.result) is not PdfInspectionResult
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.correlation")


def encode_pdf_inspection_report(result: PdfInspectionResult) -> bytes:
    """Encode a bounded canonical report containing only closed structural fields."""

    if type(result) is not PdfInspectionResult:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.result")
    features = {
        "signature_markers": result.features.signature_markers.value,
        "attachments": result.features.attachments.value,
        "interactive_content": result.features.interactive_content.value,
        "actions": result.features.actions.value,
        "scripts": result.features.scripts.value,
        "xfa": result.features.xfa.value,
    }
    if result.report_version == 2:
        features.update(
            metadata=result.features.metadata.value,
            annotations=result.features.annotations.value,
            form_fields=result.features.form_fields.value,
        )
    document = {
        "report_version": result.report_version,
        "open_state": result.open_state.value,
        "page_count": result.page_count,
        "object_count": result.object_count,
        "encryption_state": result.encryption_state.value,
        "structural_state": result.structural_state.value,
        "features": features,
    }
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except TypeError, ValueError, UnicodeError:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.encode")
    if not 1 <= len(encoded) <= MAX_PDF_INSPECTION_REPORT_BYTES:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.size")
    return encoded


def decode_pdf_inspection_report(payload: bytes) -> PdfInspectionResult:
    """Decode a closed v1 or v2 report without inventing unperformed inspection facts."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_INSPECTION_REPORT_BYTES:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.size")

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.duplicate")
            result[key] = value
        return result

    decoded: object = None
    try:
        decoded = json.loads(payload.decode("ascii"), object_pairs_hook=pairs_hook)
    except PdfInspectionError:
        raise
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError:
        pass
    if decoded is None:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.decode")
    if type(decoded) is not dict or frozenset(decoded) != _REPORT_KEYS:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.fields")
    document = cast(dict[str, object], decoded)
    version = document["report_version"]
    if type(version) is not int or version not in (1, 2):
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.version")
    features_value = document["features"]
    feature_keys = _V1_FEATURE_KEYS if version == 1 else _V2_FEATURE_KEYS
    if type(features_value) is not dict or frozenset(features_value) != feature_keys:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.features")
    features = cast(dict[str, object], features_value)
    try:
        return PdfInspectionResult(
            open_state=PdfOpenState(_require_string(document["open_state"])),
            page_count=_require_optional_integer(document["page_count"]),
            object_count=_require_optional_integer(document["object_count"]),
            encryption_state=PdfEncryptionState(_require_string(document["encryption_state"])),
            structural_state=PdfStructuralState(_require_string(document["structural_state"])),
            features=PdfFeatureSummary(
                signature_markers=PdfFeatureState(_require_string(features["signature_markers"])),
                attachments=PdfFeatureState(_require_string(features["attachments"])),
                interactive_content=PdfFeatureState(
                    _require_string(features["interactive_content"])
                ),
                actions=PdfFeatureState(_require_string(features["actions"])),
                scripts=PdfFeatureState(_require_string(features["scripts"])),
                xfa=PdfFeatureState(_require_string(features["xfa"])),
                metadata=PdfFeatureState(_require_string(features["metadata"]))
                if version == 2
                else PdfFeatureState.UNKNOWN,
                annotations=PdfFeatureState(_require_string(features["annotations"]))
                if version == 2
                else PdfFeatureState.UNKNOWN,
                form_fields=PdfFeatureState(_require_string(features["form_fields"]))
                if version == 2
                else PdfFeatureState.UNKNOWN,
            ),
            report_version=version,
        )
    except PdfInspectionError:
        raise
    except TypeError, ValueError:
        pass
    # Raise after the parser exception scope so its document value is not retained
    # as an exception context that diagnostics could accidentally serialize.
    reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.values")


def encode_pdf_inspection_frame(envelope: PdfInspectionEnvelope) -> bytes:
    """Encode one length-prefixed, correlated result frame for the broker pipe."""

    if type(envelope) is not PdfInspectionEnvelope:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.envelope")
    report = encode_pdf_inspection_report(envelope.result)
    payload = envelope.operation_id.bytes + envelope.source_object_id.bytes + report
    frame = struct.pack(">I", len(payload)) + payload
    if len(frame) > MAX_PDF_INSPECTION_FRAME_BYTES:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.frame_size")
    return frame


def decode_pdf_inspection_frame(frame: bytes) -> PdfInspectionEnvelope:
    """Decode one complete frame and reject truncation, surplus bytes, or misbinding."""

    if type(frame) is not bytes or not 37 <= len(frame) <= MAX_PDF_INSPECTION_FRAME_BYTES:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.frame_size")
    declared = struct.unpack(">I", frame[:4])[0]
    if declared != len(frame) - 4 or declared < 33:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.frame_length")
    try:
        operation_id = UUID(bytes=frame[4:20])
        source_object_id = UUID(bytes=frame[20:36])
    except ValueError:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.correlation")
    return PdfInspectionEnvelope(
        operation_id=operation_id,
        source_object_id=source_object_id,
        result=decode_pdf_inspection_report(frame[36:]),
    )


def _require_string(value: object) -> str:
    if type(value) is not str:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.value")
    return value


def _require_optional_integer(value: object) -> int | None:
    if value is not None and type(value) is not int:
        reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "report.value")
    return value
