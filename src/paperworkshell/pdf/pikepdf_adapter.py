"""Bounded pikepdf adapter for conservative PDF structural inspection."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Final, Protocol

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
DictionaryObserver = Callable[[pikepdf.Dictionary, bool, bool], None]


class _InspectionCheckpoint(Protocol):
    def checkpoint(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _DocumentCheckpoint:
    checkpoint: Callable[[], None]


DEFAULT_MAXIMUM_GRAPH_DEPTH: Final = 64
DEFAULT_MAXIMUM_COLLECTION_ITEMS: Final = 65_536
DEFAULT_GRAPH_NODES_PER_OBJECT: Final = 64
MAXIMUM_GRAPH_NODES: Final = 4_000_000

_NAME_A: Final = pikepdf.Name("/A")
_NAME_AA: Final = pikepdf.Name("/AA")
_NAME_ACTION: Final = pikepdf.Name("/Action")
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
_NAME_INFO: Final = pikepdf.Name("/Info")
_NAME_JAVASCRIPT: Final = pikepdf.Name("/JavaScript")
_NAME_JS: Final = pikepdf.Name("/JS")
_NAME_METADATA: Final = pikepdf.Name("/Metadata")
_NAME_NEXT: Final = pikepdf.Name("/Next")
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
_ANNOTATION_SUBTYPES: Final = frozenset(
    pikepdf.Name(value)
    for value in (
        "/Text",
        "/Link",
        "/FreeText",
        "/Line",
        "/Square",
        "/Circle",
        "/Polygon",
        "/PolyLine",
        "/Highlight",
        "/Underline",
        "/Squiggly",
        "/StrikeOut",
        "/Stamp",
        "/Caret",
        "/Ink",
        "/Popup",
        "/FileAttachment",
        "/Sound",
        "/Movie",
        "/Widget",
        "/Screen",
        "/PrinterMark",
        "/TrapNet",
        "/Watermark",
        "/3D",
        "/Redact",
        "/Projection",
        "/RichMedia",
    )
)
_FIELD_TYPES: Final = frozenset(pikepdf.Name(value) for value in ("/Btn", "/Tx", "/Ch", "/Sig"))


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
    metadata: bool = False
    annotations: bool = False
    form_fields: bool = False
    incomplete: bool = False

    def freeze(self, *, syntax_warnings: bool = False) -> PdfFeatureSummary:
        def state(detected: bool) -> PdfFeatureState:
            if detected:
                return PdfFeatureState.DETECTED
            if self.incomplete or syntax_warnings:
                return PdfFeatureState.UNKNOWN
            return PdfFeatureState.NOT_DETECTED

        return PdfFeatureSummary(
            signature_markers=state(self.signature_markers),
            attachments=state(self.attachments),
            interactive_content=state(self.interactive_content),
            actions=state(self.actions),
            scripts=state(self.scripts),
            xfa=state(self.xfa),
            metadata=state(self.metadata),
            annotations=state(self.annotations),
            form_fields=state(self.form_fields),
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
                    features=features.freeze(syntax_warnings=bool(syntax_warnings)),
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

    def inspect_open_features(
        self,
        document: pikepdf.Pdf,
        *,
        maximum_objects: int,
        checkpoint: Callable[[], None],
        dictionary_observer: DictionaryObserver | None = None,
    ) -> PdfFeatureSummary:
        """Inspect an already strictly opened worker document with the same graph budgets.

        The owner remains responsible for syntax, encryption and page-count checks.
        An optional native observer adds profile restrictions during this single walk.
        """

        checkpoint()
        object_count = len(document.objects)
        if not 1 <= object_count <= maximum_objects:
            reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "pdf.objects")
        return self._scan_all_objects(
            document,
            operation=_DocumentCheckpoint(checkpoint),
            node_budget=min(MAXIMUM_GRAPH_NODES, object_count * self.graph_nodes_per_object),
            dictionary_observer=dictionary_observer,
        ).freeze()

    def _scan_all_objects(
        self,
        document: pikepdf.Pdf,
        *,
        operation: _InspectionCheckpoint,
        node_budget: int,
        dictionary_observer: DictionaryObserver | None = None,
    ) -> _FeatureAccumulator:
        accumulator = _FeatureAccumulator()
        stack: list[tuple[object, int, bool]] = [
            (current, 0, False) for current in document.objects
        ]
        stack.append((document.trailer, 0, True))
        seen_indirect: set[tuple[int, int]] = set()
        visited_nodes = 0

        while stack:
            operation.checkpoint()
            current, depth, is_trailer = stack.pop()
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
                _observe_dictionary(
                    dictionary,
                    accumulator,
                    maximum_collection_items=self.maximum_collection_items,
                    is_stream=True,
                )
                if dictionary_observer is not None:
                    dictionary_observer(dictionary, False, True)
                stack.extend((value, depth + 1, False) for _, value in dictionary.items())
            elif isinstance(current, pikepdf.Dictionary):
                if len(current) > self.maximum_collection_items:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.LIMIT_EXCEEDED,
                        "pdf.collection_items",
                    )
                _observe_dictionary(
                    current,
                    accumulator,
                    maximum_collection_items=self.maximum_collection_items,
                    is_trailer=is_trailer,
                )
                if dictionary_observer is not None:
                    dictionary_observer(current, is_trailer, False)
                stack.extend((value, depth + 1, False) for _, value in current.items())
            elif isinstance(current, pikepdf.Array):
                if len(current) > self.maximum_collection_items:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.LIMIT_EXCEEDED,
                        "pdf.collection_items",
                    )
                stack.extend((value, depth + 1, False) for value in current)

        return accumulator


def _observe_dictionary(
    current: pikepdf.Dictionary,
    accumulator: _FeatureAccumulator,
    *,
    maximum_collection_items: int,
    is_trailer: bool = False,
    is_stream: bool = False,
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
        or _is_name(subtype, pikepdf.Name("/FileAttachment"))
    )
    action_marker = (
        _NAME_A in current
        or _NAME_AA in current
        or _NAME_OPENACTION in current
        or _is_name(object_type, _NAME_ACTION)
        or (isinstance(action_type, pikepdf.Name) and action_type in _ACTION_TYPES)
    )
    script_marker = (
        _NAME_JS in current
        or _NAME_JAVASCRIPT in current
        or _is_name(action_type, _NAME_JAVASCRIPT)
    )
    xfa_marker = _NAME_XFA in current
    metadata_marker = (
        (is_trailer and _NAME_INFO in current)
        or _NAME_METADATA in current
        or _is_name(object_type, _NAME_METADATA)
    )
    annotation_marker = (
        _NAME_ANNOTS in current
        or _is_name(object_type, _NAME_ANNOT)
        or (isinstance(subtype, pikepdf.Name) and subtype in _ANNOTATION_SUBTYPES)
    )
    form_marker = (
        _NAME_ACROFORM in current
        or _NAME_FIELDS in current
        or _NAME_FT in current
        or _is_name(subtype, _NAME_WIDGET)
        or xfa_marker
    )
    interactive_marker = (
        form_marker
        or annotation_marker
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
    accumulator.metadata |= metadata_marker
    accumulator.annotations |= annotation_marker
    accumulator.form_fields |= form_marker

    # Retain observed markers, but never treat unsupported or malformed feature
    # structures as proof that the other feature categories are absent.
    expected_types = (
        (_NAME_ACROFORM, (pikepdf.Dictionary,)),
        (_NAME_FIELDS, (pikepdf.Array,)),
        (_NAME_ANNOTS, (pikepdf.Array,)),
        (_NAME_METADATA, (pikepdf.Stream,)),
        (_NAME_A, (pikepdf.Dictionary,)),
        (_NAME_AA, (pikepdf.Dictionary,)),
        (_NAME_OPENACTION, (pikepdf.Dictionary, pikepdf.Array)),
        (_NAME_JS, (pikepdf.String, pikepdf.Stream)),
        (_NAME_XFA, (pikepdf.Array, pikepdf.Stream)),
        (_NAME_EMBEDDEDFILES, (pikepdf.Dictionary,)),
        (_NAME_AF, (pikepdf.Array,)),
        (_NAME_EF, (pikepdf.Dictionary,)),
    )
    accumulator.incomplete |= any(
        key in current and not isinstance(current.get(key), allowed)
        for key, allowed in expected_types
    )
    accumulator.incomplete |= any(
        key in current
        and not _supported_dictionary_array(current.get(key), maximum_collection_items)
        for key in (_NAME_FIELDS, _NAME_ANNOTS, _NAME_AF)
    )
    accumulator.incomplete |= (
        (
            is_trailer
            and _NAME_INFO in current
            and not isinstance(current.get(_NAME_INFO), pikepdf.Dictionary)
        )
        or (_is_name(object_type, _NAME_METADATA) and not is_stream)
        or (_NAME_FT in current and not _known_name(field_type, _FIELD_TYPES))
        or (_is_name(object_type, _NAME_ANNOT) and not _known_name(subtype, _ANNOTATION_SUBTYPES))
        or (_is_name(object_type, _NAME_ACTION) and not _known_name(action_type, _ACTION_TYPES))
        or (
            _NAME_XFA in current
            and not _supported_xfa_packets(current.get(_NAME_XFA), maximum_collection_items)
        )
        or (
            _NAME_EF in current
            and not _supported_embedded_streams(current.get(_NAME_EF), maximum_collection_items)
        )
        or (_NAME_A in current and not _supported_action(current.get(_NAME_A)))
        or (
            _NAME_AA in current
            and not _supported_action_collection(current.get(_NAME_AA), maximum_collection_items)
        )
        or (
            _known_name(action_type, _ACTION_TYPES)
            and _NAME_NEXT in current
            and not _supported_next_action(current.get(_NAME_NEXT), maximum_collection_items)
        )
        or (
            isinstance(current.get(_NAME_OPENACTION), pikepdf.Dictionary)
            and not _supported_action(current.get(_NAME_OPENACTION))
        )
    )


def _known_name(value: object, known: frozenset[pikepdf.Object]) -> bool:
    return isinstance(value, pikepdf.Name) and value in known


def _supported_dictionary_array(value: object, maximum_items: int) -> bool:
    # Check only immediate shapes; the existing bounded object walk owns traversal.
    return (
        isinstance(value, pikepdf.Array)
        and len(value) <= maximum_items
        and all(isinstance(item, pikepdf.Dictionary) for item in value)
    )


def _supported_xfa_packets(value: object, maximum_items: int) -> bool:
    if isinstance(value, pikepdf.Stream):
        return True
    if not isinstance(value, pikepdf.Array) or len(value) > maximum_items or len(value) % 2 != 0:
        return False
    return all(
        isinstance(value[index], pikepdf.String) and isinstance(value[index + 1], pikepdf.Stream)
        for index in range(0, len(value), 2)
    )


def _supported_embedded_streams(value: object, maximum_items: int) -> bool:
    return (
        isinstance(value, pikepdf.Dictionary)
        and len(value) <= maximum_items
        and all(isinstance(stream, pikepdf.Stream) for _, stream in value.items())
    )


def _supported_action(value: object) -> bool:
    return isinstance(value, pikepdf.Dictionary) and _known_name(value.get(_NAME_S), _ACTION_TYPES)


def _supported_action_collection(value: object, maximum_items: int) -> bool:
    if not isinstance(value, pikepdf.Dictionary) or len(value) > maximum_items:
        return False
    return all(_supported_action(action) for _, action in value.items())


def _supported_next_action(value: object, maximum_items: int) -> bool:
    if isinstance(value, pikepdf.Array):
        return len(value) <= maximum_items and all(_supported_action(action) for action in value)
    return _supported_action(value)


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
