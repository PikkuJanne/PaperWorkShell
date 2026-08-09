"""Bounded pikepdf adapter for conservative PDF structural inspection."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Final

import pikepdf

from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    reject_pdf_inspection,
)

CancellationProbe = Callable[[], bool]
MonotonicClock = Callable[[], float]

DEFAULT_MAXIMUM_GRAPH_DEPTH: Final = 64
DEFAULT_MAXIMUM_COLLECTION_ITEMS: Final = 65_536
DEFAULT_GRAPH_NODES_PER_OBJECT: Final = 64
MAXIMUM_GRAPH_NODES: Final = 4_000_000

_NAME_A: Final = pikepdf.Name("/A")
_NAME_AA: Final = pikepdf.Name("/AA")
_NAME_ACROFORM: Final = pikepdf.Name("/AcroForm")
_NAME_AF: Final = pikepdf.Name("/AF")
_NAME_ANNOTS: Final = pikepdf.Name("/Annots")
_NAME_ANNOT: Final = pikepdf.Name("/Annot")
_NAME_BYTERANGE: Final = pikepdf.Name("/ByteRange")
_NAME_CONTENTS: Final = pikepdf.Name("/Contents")
_NAME_DOCMDP: Final = pikepdf.Name("/DocMDP")
_NAME_EF: Final = pikepdf.Name("/EF")
_NAME_EMBEDDEDFILE: Final = pikepdf.Name("/EmbeddedFile")
_NAME_EMBEDDEDFILES: Final = pikepdf.Name("/EmbeddedFiles")
_NAME_FIELDMDP: Final = pikepdf.Name("/FieldMDP")
_NAME_FIELDS: Final = pikepdf.Name("/Fields")
_NAME_FILESPEC: Final = pikepdf.Name("/Filespec")
_NAME_FT: Final = pikepdf.Name("/FT")
_NAME_JAVASCRIPT: Final = pikepdf.Name("/JavaScript")
_NAME_JS: Final = pikepdf.Name("/JS")
_NAME_OPENACTION: Final = pikepdf.Name("/OpenAction")
_NAME_S: Final = pikepdf.Name("/S")
_NAME_SIG: Final = pikepdf.Name("/Sig")
_NAME_SIGFLAGS: Final = pikepdf.Name("/SigFlags")
_NAME_SUBTYPE: Final = pikepdf.Name("/Subtype")
_NAME_TYPE: Final = pikepdf.Name("/Type")
_NAME_WIDGET: Final = pikepdf.Name("/Widget")
_NAME_XFA: Final = pikepdf.Name("/XFA")

_ACTION_TYPES: Final = frozenset(
    pikepdf.Name(value)
    for value in (
        "/GoTo",
        "/GoToR",
        "/GoToE",
        "/Launch",
        "/Thread",
        "/URI",
        "/Sound",
        "/Movie",
        "/Hide",
        "/Named",
        "/SubmitForm",
        "/ResetForm",
        "/ImportData",
        "/JavaScript",
        "/SetOCGState",
        "/Rendition",
        "/Trans",
        "/GoTo3DView",
        "/RichMediaExecute",
    )
)


def _never_cancel() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class _InspectionOperation:
    cancellation_probe: CancellationProbe
    deadline: float
    monotonic: MonotonicClock

    def checkpoint(self) -> None:
        try:
            cancelled = self.cancellation_probe()
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "pdf.cancel_probe")
        if type(cancelled) is not bool:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "pdf.cancel_probe")
        if cancelled:
            reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "pdf.operation")
        try:
            current = self.monotonic()
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "pdf.clock")
        if type(current) not in (int, float) or not math.isfinite(current):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "pdf.clock")
        if current >= self.deadline:
            reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "pdf.operation")


@dataclass(slots=True)
class _FeatureAccumulator:
    signature_markers: bool = False
    attachments: bool = False
    interactive_content: bool = False
    actions: bool = False
    scripts: bool = False
    xfa: bool = False

    def freeze(self) -> PdfFeatureSummary:
        def state(detected: bool) -> PdfFeatureState:
            if detected:
                return PdfFeatureState.DETECTED
            return PdfFeatureState.NOT_DETECTED

        return PdfFeatureSummary(
            signature_markers=state(self.signature_markers),
            attachments=state(self.attachments),
            interactive_content=state(self.interactive_content),
            actions=state(self.actions),
            scripts=state(self.scripts),
            xfa=state(self.xfa),
        )


@dataclass(frozen=True, slots=True)
class PikePdfAdapter:
    """Inspect one authenticated binary stream without exposing document values."""

    maximum_graph_depth: int = DEFAULT_MAXIMUM_GRAPH_DEPTH
    maximum_collection_items: int = DEFAULT_MAXIMUM_COLLECTION_ITEMS
    graph_nodes_per_object: int = DEFAULT_GRAPH_NODES_PER_OBJECT

    def __post_init__(self) -> None:
        values = (
            self.maximum_graph_depth,
            self.maximum_collection_items,
            self.graph_nodes_per_object,
        )
        if (
            any(type(value) is not int or value < 1 for value in values)
            or self.maximum_graph_depth > DEFAULT_MAXIMUM_GRAPH_DEPTH
            or self.maximum_collection_items > DEFAULT_MAXIMUM_COLLECTION_ITEMS
            or self.graph_nodes_per_object > DEFAULT_GRAPH_NODES_PER_OBJECT
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.adapter_limits")

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_bytes: int,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: CancellationProbe = _never_cancel,
        monotonic: MonotonicClock = time.monotonic,
    ) -> PdfInspectionResult:
        """Return a closed structural result or raise a sanitized operation failure."""

        if type(limits) is not PdfInspectionLimits:
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.limits")
        if type(source_bytes) is not int or source_bytes < 1:
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_INVALID, "pdf.source")
        if source_bytes > limits.maximum_input_bytes:
            reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.input_bytes")
        if (
            type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline <= 0
            or not callable(cancellation_probe)
            or not callable(monotonic)
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "pdf.operation")

        operation = _InspectionOperation(
            cancellation_probe=cancellation_probe,
            deadline=float(deadline),
            monotonic=monotonic,
        )
        operation.checkpoint()
        try:
            stream.seek(0)
        except AttributeError, OSError, TypeError, ValueError:
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_INVALID, "pdf.stream")

        try:
            with pikepdf.Pdf.open(
                stream,
                password="",
                attempt_recovery=False,
                suppress_warnings=True,
            ) as document:
                operation.checkpoint()
                if document.is_encrypted:
                    return _encrypted_result()

                page_count = len(document.pages)
                if page_count < 1:
                    return _malformed_result()
                if page_count > limits.maximum_pages:
                    reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.pages")
                operation.checkpoint()

                object_count = len(document.objects)
                if object_count < 1:
                    return _malformed_result()
                if object_count > limits.maximum_objects:
                    reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.objects")

                node_budget = min(
                    MAXIMUM_GRAPH_NODES,
                    object_count * self.graph_nodes_per_object,
                )
                features = self._scan_all_objects(
                    document,
                    operation=operation,
                    node_budget=node_budget,
                )
                operation.checkpoint()

                def syntax_progress(_percent: int) -> None:
                    operation.checkpoint()

                syntax_warnings = document.check_pdf_syntax(progress=syntax_progress)
                if type(syntax_warnings) is not list:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.INTERNAL_ERROR,
                        "pdf.syntax_report",
                    )
                structural_state = (
                    PdfStructuralState.WARNINGS if syntax_warnings else PdfStructuralState.CLEAN
                )
                operation.checkpoint()
                return PdfInspectionResult(
                    open_state=PdfOpenState.OPENED,
                    page_count=page_count,
                    object_count=object_count,
                    encryption_state=PdfEncryptionState.UNENCRYPTED,
                    structural_state=structural_state,
                    features=features,
                )
        except PdfInspectionError:
            raise
        except pikepdf.PasswordError:
            return _encrypted_result()
        except (
            pikepdf.DataDecodingError,
            pikepdf.DeletedObjectError,
            pikepdf.ForeignObjectError,
            pikepdf.PdfError,
            RuntimeError,
            ValueError,
        ):
            return _malformed_result()
        except MemoryError, RecursionError:
            reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.resources")
        except OSError:
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_INVALID, "pdf.stream")
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "pdf.adapter")

    def _scan_all_objects(
        self,
        document: pikepdf.Pdf,
        *,
        operation: _InspectionOperation,
        node_budget: int,
    ) -> PdfFeatureSummary:
        accumulator = _FeatureAccumulator()
        stack: list[tuple[object, int]] = [(current, 0) for current in document.objects]
        seen_indirect: set[tuple[int, int]] = set()
        visited_nodes = 0

        while stack:
            operation.checkpoint()
            current, depth = stack.pop()
            if depth > self.maximum_graph_depth:
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.graph_depth")

            if isinstance(current, pikepdf.Object):
                object_generation = current.objgen
                if object_generation != (0, 0):
                    if object_generation in seen_indirect:
                        continue
                    seen_indirect.add(object_generation)

            visited_nodes += 1
            if visited_nodes > node_budget:
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.graph_nodes")

            if isinstance(current, pikepdf.Stream):
                dictionary = current.stream_dict
                if len(dictionary) > self.maximum_collection_items:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.LIMIT_EXCEEDED,
                        "pdf.collection_items",
                    )
                _observe_dictionary(dictionary, accumulator)
                stack.extend((value, depth + 1) for _, value in dictionary.items())
            elif isinstance(current, pikepdf.Dictionary):
                if len(current) > self.maximum_collection_items:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.LIMIT_EXCEEDED,
                        "pdf.collection_items",
                    )
                _observe_dictionary(current, accumulator)
                stack.extend((value, depth + 1) for _, value in current.items())
            elif isinstance(current, pikepdf.Array):
                if len(current) > self.maximum_collection_items:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.LIMIT_EXCEEDED,
                        "pdf.collection_items",
                    )
                stack.extend((value, depth + 1) for value in current)

        return accumulator.freeze()


def _observe_dictionary(
    current: pikepdf.Dictionary,
    accumulator: _FeatureAccumulator,
) -> None:
    object_type = current.get(_NAME_TYPE)
    field_type = current.get(_NAME_FT)
    subtype = current.get(_NAME_SUBTYPE)
    action_type = current.get(_NAME_S)

    signature_marker = (
        _is_name(object_type, _NAME_SIG)
        or _is_name(field_type, _NAME_SIG)
        or (_NAME_BYTERANGE in current and _NAME_CONTENTS in current)
        or _NAME_SIGFLAGS in current
        or _NAME_DOCMDP in current
        or _NAME_FIELDMDP in current
    )
    attachment_marker = (
        _NAME_EMBEDDEDFILES in current
        or _NAME_AF in current
        or _NAME_EF in current
        or _is_name(object_type, _NAME_EMBEDDEDFILE)
        or (_is_name(object_type, _NAME_FILESPEC) and _NAME_EF in current)
    )
    action_marker = (
        _NAME_A in current
        or _NAME_AA in current
        or _NAME_OPENACTION in current
        or (isinstance(action_type, pikepdf.Name) and action_type in _ACTION_TYPES)
    )
    script_marker = (
        _NAME_JS in current
        or _NAME_JAVASCRIPT in current
        or _is_name(action_type, _NAME_JAVASCRIPT)
    )
    xfa_marker = _NAME_XFA in current
    interactive_marker = (
        _NAME_ACROFORM in current
        or _NAME_FIELDS in current
        or _NAME_FT in current
        or _NAME_ANNOTS in current
        or _is_name(object_type, _NAME_ANNOT)
        or _is_name(subtype, _NAME_WIDGET)
        or signature_marker
        or action_marker
        or script_marker
        or xfa_marker
    )

    accumulator.signature_markers |= signature_marker
    accumulator.attachments |= attachment_marker
    accumulator.interactive_content |= interactive_marker
    accumulator.actions |= action_marker
    accumulator.scripts |= script_marker
    accumulator.xfa |= xfa_marker


def _is_name(value: object, expected: pikepdf.Object) -> bool:
    return isinstance(value, pikepdf.Name) and value == expected


def _encrypted_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.ENCRYPTED,
        page_count=None,
        object_count=None,
        encryption_state=PdfEncryptionState.ENCRYPTED,
        structural_state=PdfStructuralState.FAILED,
        features=PdfFeatureSummary.unknown(),
    )


def _malformed_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.MALFORMED,
        page_count=None,
        object_count=None,
        encryption_state=PdfEncryptionState.UNKNOWN,
        structural_state=PdfStructuralState.FAILED,
        features=PdfFeatureSummary.unknown(),
    )
