"""Conservative, bounded optimization of an already verified private PDF."""

from __future__ import annotations

import hashlib
import os
import zlib
from collections.abc import Buffer
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, cast

import pikepdf

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
    PdfStructureChecker,
    _BoundedCandidateStream,
    _checkpoint,
    _measure_output,
    _render_one,
    _same_rendered_page,
    _strict_open,
    _verify_candidate_with_qpdf,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.portal_policy import require_portal_output
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.renderer import PdfPageRenderer

# These keys describe serialized cross-reference storage, not document objects.
_TRAILER_STORAGE_KEYS = frozenset({"/Size", "/Prev", "/XRefStm", "/ID"})
_XREF_STREAM_KEYS = frozenset({"/Type", "/W", "/Index", "/Length", "/Filter", "/DecodeParms"})
_CHUNK_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class PdfOptimizationAdapterResult:
    """Only an optimized result grants authority to the separate candidate."""

    report: PdfOptimizationReport
    candidate_sha256: str | None = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class PdfOptimizationAdapter:
    """Never modify the baseline or publish an unverified rewrite."""

    qpdf_checker: PdfStructureChecker = field(default_factory=QpdfCommandChecker, repr=False)
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer, repr=False)

    def optimize(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
        maximum_candidate_bytes: int,
        maximum_semantic_bytes: int,
    ) -> PdfOptimizationAdapterResult:
        """Return a verified smaller candidate, or retain an unchanged baseline."""

        failure: tuple[PdfExportErrorCode, str]
        try:
            return self._optimize(
                baseline_path,
                candidate_path,
                baseline_report=baseline_report,
                limits=limits,
                control=control,
                maximum_candidate_bytes=maximum_candidate_bytes,
                maximum_semantic_bytes=maximum_semantic_bytes,
            )
        except PdfExportError as error:
            failure = (error.code, error.context)
        except MemoryError, RecursionError:
            failure = (PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.resources")
        except Exception:
            failure = (PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.adapter")
        # Raise outside the handler: library values cannot survive as exception context.
        reject_pdf_export(*failure)

    def _optimize(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
        maximum_candidate_bytes: int,
        maximum_semantic_bytes: int,
    ) -> PdfOptimizationAdapterResult:
        _validate_call(
            baseline_path,
            candidate_path,
            baseline_report,
            limits,
            control,
            maximum_candidate_bytes,
            maximum_semantic_bytes,
        )
        _checkpoint(control)
        baseline = _measure_output(baseline_path, limits=limits, control=control)
        if (baseline.byte_size, baseline.sha256) != (
            baseline_report.output_byte_size,
            baseline_report.output_sha256,
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.baseline"
            )
        expected = _fingerprint(
            baseline_path,
            baseline_report.page_count,
            limits,
            control,
            maximum_semantic_bytes,
            portal=baseline_report.portal_profile_verified,
        )
        try:
            stream = candidate_path.open("x+b")
        except FileExistsError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "optimization.candidate")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "optimization.candidate")
        retain_candidate = False
        try:
            with stream:
                bounded = _BoundedCandidateStream(stream, maximum_candidate_bytes)
                try:
                    with baseline_path.open("rb") as source, _strict_open(source) as document:
                        _write_candidate(document, bounded, control)
                    bounded.flush()
                    os.fsync(bounded.fileno())
                except Exception:
                    if not bounded.limit_exceeded:
                        raise
                _checkpoint(control)
            if bounded.limit_exceeded:
                if _measure_output(baseline_path, limits=limits, control=control) != baseline:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.baseline"
                    )
                return PdfOptimizationAdapterResult(
                    PdfOptimizationReport(
                        profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
                        outcome=PdfOptimizationOutcome.NO_REDUCTION,
                        baseline_byte_size=baseline.byte_size,
                        candidate_byte_size=None,
                        selected_byte_size=baseline.byte_size,
                        verified_page_count=0,
                    ),
                    None,
                )
            candidate = _measure_output(candidate_path, limits=limits, control=control)
            actual = _fingerprint(
                candidate_path,
                baseline_report.page_count,
                limits,
                control,
                maximum_semantic_bytes,
                portal=baseline_report.portal_profile_verified,
            )
            if actual != expected:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.semantics"
                )
            _verify_candidate_with_qpdf(
                self.qpdf_checker, candidate_path, limits=limits, control=control
            )
            for page_index in range(baseline_report.page_count):
                _checkpoint(control)
                before = _render_one(
                    self.renderer,
                    baseline_path,
                    page_index=page_index,
                    rotation=PdfRenderRotation.DEGREES_0,
                    limits=limits,
                    control=control,
                )
                after = _render_one(
                    self.renderer,
                    candidate_path,
                    page_index=page_index,
                    rotation=PdfRenderRotation.DEGREES_0,
                    limits=limits,
                    control=control,
                )
                if not _same_rendered_page(before, after):
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "optimization.render"
                    )
            if (
                _measure_output(baseline_path, limits=limits, control=control) != baseline
                or _measure_output(candidate_path, limits=limits, control=control) != candidate
            ):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.changed"
                )
            retain_candidate = True
            return PdfOptimizationAdapterResult(
                PdfOptimizationReport(
                    profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
                    outcome=PdfOptimizationOutcome.OPTIMIZED,
                    baseline_byte_size=baseline.byte_size,
                    candidate_byte_size=candidate.byte_size,
                    selected_byte_size=candidate.byte_size,
                    verified_page_count=baseline_report.page_count,
                ),
                candidate.sha256,
            )
        finally:
            if not retain_candidate:
                try:
                    candidate_path.unlink()
                except OSError:
                    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "optimization.cleanup")


def _validate_call(
    baseline_path: Path,
    candidate_path: Path,
    report: PdfExportVerificationReport,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    candidate_bytes: int,
    semantic_bytes: int,
) -> None:
    if (
        not isinstance(baseline_path, Path)
        or not baseline_path.is_absolute()
        or not isinstance(candidate_path, Path)
        or not candidate_path.is_absolute()
        or os.path.normcase(os.path.abspath(baseline_path))
        == os.path.normcase(os.path.abspath(candidate_path))
        or type(report) is not PdfExportVerificationReport
        or type(limits) is not PdfExportAdapterLimits
        or type(control) is not PdfExportAdapterControl
        or type(candidate_bytes) is not int
        or not 1 <= candidate_bytes < report.output_byte_size
        or candidate_bytes > limits.inspection.maximum_output_bytes
        or candidate_bytes > limits.inspection.maximum_temporary_bytes
        or type(semantic_bytes) is not int
        or semantic_bytes < 1
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "optimization.call")


def _write_candidate(
    document: pikepdf.Pdf, stream: _BoundedCandidateStream, control: PdfExportAdapterControl
) -> None:
    _checkpoint(control)
    document.save(
        cast(BinaryIO, stream),
        compress_streams=True,
        stream_decode_level=pikepdf.StreamDecodeLevel.none,
        recompress_flate=False,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
        normalize_content=False,
        linearize=False,
        fix_metadata_version=False,
        deterministic_id=True,
        encryption=False,
        progress=lambda _percent: _checkpoint(control),
    )
    _checkpoint(control)


@dataclass(slots=True)
class _SemanticBudget:
    maximum_bytes: int
    control: PdfExportAdapterControl
    used: int = 0

    def consume(self, size: int) -> None:
        _checkpoint(self.control)
        self.used += size
        if self.used > self.maximum_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.semantic_bytes")


def _fingerprint(
    path: Path,
    page_count: int,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    maximum_bytes: int,
    *,
    portal: bool = False,
) -> str:
    with path.open("rb") as source, _strict_open(source) as document:
        _checkpoint(control)
        if document.is_encrypted or len(document.pages) != page_count:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.policy"
            )
        if page_count > limits.inspection.maximum_pages or len(document.objects) > (
            limits.inspection.maximum_objects
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.objects")
        if portal:
            require_portal_output(
                document,
                maximum_objects=limits.inspection.maximum_objects,
                checkpoint=lambda: _checkpoint(control),
            )
        fingerprint = _graph_fingerprint(
            cast(pikepdf.Dictionary, document.trailer), limits, control, maximum_bytes
        )
        if document.check_pdf_syntax(progress=lambda _percent: _checkpoint(control)):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.syntax"
            )
        return fingerprint


def _graph_fingerprint(
    trailer: pikepdf.Dictionary,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    maximum_bytes: int,
    selected_images: dict[tuple[int, int], int] | None = None,
) -> str:
    """Hash reachable state, optionally projecting authenticated image replacements.

    The optional map is worker-local analysis state. Its caller must authenticate
    every replacement's bytes and image dictionary separately. Reference ordinals
    and aliases remain in the graph, as do all other image dictionary entries.
    The default lossless comparator never projects image content.
    """

    digest = hashlib.sha256()
    budget = _SemanticBudget(maximum_bytes, control)
    maximum_objects = limits.inspection.maximum_objects
    pending: list[tuple[str, object]] = [("trailer", trailer)]
    seen: dict[tuple[int, int], int] = {}
    visited = 0

    def emit(tag: bytes, value: Buffer = b"") -> None:
        view = memoryview(value).cast("B")
        budget.consume(view.nbytes)
        digest.update(tag)
        digest.update(view.nbytes.to_bytes(8, "big"))
        for offset in range(0, view.nbytes, _CHUNK_BYTES):
            _checkpoint(control)
            digest.update(view[offset : offset + _CHUNK_BYTES])

    while pending:
        _checkpoint(control)
        role, value = pending.pop()
        if role == "key":
            emit(b"K", cast(bytes, value))
            continue
        visited += 1
        if visited > maximum_objects:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.objects")
        if isinstance(value, pikepdf.Object) and value.is_indirect:
            identity = value.objgen
            if identity in seen:
                emit(b"r", seen[identity].to_bytes(8, "big"))
                continue
            ordinal = len(seen)
            seen[identity] = ordinal
            emit(b"o", ordinal.to_bytes(8, "big"))
        if isinstance(value, pikepdf.Stream | pikepdf.Dictionary):
            if len(value.keys()) + visited + len(pending) > maximum_objects:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.objects")
            omitted: frozenset[str] = frozenset()
            if role == "trailer":
                omitted = _TRAILER_STORAGE_KEYS
                if value.get("/Type") == pikepdf.Name.XRef:
                    omitted |= _XREF_STREAM_KEYS
            if isinstance(value, pikepdf.Stream):
                selected = None if selected_images is None else selected_images.get(value.objgen)
                if selected is not None:
                    if value.get("/Subtype") != pikepdf.Name.Image:
                        reject_pdf_export(
                            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                            "optimization.image_projection",
                        )
                    omitted = frozenset({"/Length", "/Width", "/Height", "/Filter", "/DecodeParms"})
                    emit(b"I", selected.to_bytes(8, "big"))
                else:
                    # Image-stage callers must retain exact unselected encoded
                    # streams, both for preservation and honest byte impacts.
                    # The default lossless optimization comparator is unchanged.
                    normalized = _is_plain_or_flate(value) and not (
                        selected_images is not None and value.get("/Subtype") == pikepdf.Name.Image
                    )
                    omitted = frozenset({"/Length"})
                    if normalized:
                        omitted |= frozenset({"/Filter", "/DecodeParms"})
                    stream_size, stream_digest = _stream_fingerprint(value, normalized, budget)
                    emit(b"S", stream_size.to_bytes(8, "big") + stream_digest)
            else:
                emit(b"D")
            keys = sorted(key for key in value.keys() if key not in omitted)
            emit(b"n", len(keys).to_bytes(8, "big"))
            for key in reversed(keys):
                pending.append(("value", value[key]))
                pending.append(("key", bytes(pikepdf.Name(key))))
        elif isinstance(value, pikepdf.Array):
            if len(value) + visited + len(pending) > maximum_objects:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.objects")
            emit(b"A", len(value).to_bytes(8, "big"))
            for index in range(len(value) - 1, -1, -1):
                pending.append(("value", value[index]))
        elif isinstance(value, pikepdf.Name):
            emit(b"N", bytes(value))
        elif isinstance(value, pikepdf.String):
            emit(b"T", bytes(value))
        elif value is None:
            emit(b"Z")
        elif type(value) is bool:
            emit(b"B", b"1" if value else b"0")
        elif type(value) in (int, float, Decimal):
            number = Decimal(str(value))
            if not number.is_finite():
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.number"
                )
            # Neither Decimal.normalize (context rounding) nor fixed-point formatting
            # (exponent expansion) is safe for exact hostile numeric comparison.
            sign, digits, exponent = number.as_tuple()
            if type(exponent) is not int:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.number"
                )
            trimmed = list(digits)
            while trimmed and trimmed[-1] == 0:
                trimmed.pop()
                exponent += 1
            canonical = (
                f"{sign}:{''.join(str(digit) for digit in trimmed)}:{exponent}".encode("ascii")
                if trimmed
                else b"0"
            )
            emit(b"#", canonical)
        else:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.object"
            )
    return digest.hexdigest()


def _is_plain_or_flate(stream: pikepdf.Stream) -> bool:
    filters = stream.get("/Filter")
    parameters = stream.get("/DecodeParms")
    if filters is None:
        if parameters is not None:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.filter"
            )
        return True
    if parameters is not None:
        return False
    return filters == pikepdf.Name.FlateDecode or (
        isinstance(filters, pikepdf.Array)
        and len(filters) == 1
        and filters[0] == pikepdf.Name.FlateDecode
    )


def _stream_fingerprint(
    stream: pikepdf.Stream, normalized: bool, budget: _SemanticBudget
) -> tuple[int, bytes]:
    # Encoded allocation is bounded by the authenticated PDF's byte capacity. Never
    # ask qpdf to expand a potentially hostile compressed stream into native memory.
    encoded_length: object = stream.get("/Length")
    if type(encoded_length) is not int or encoded_length < 0:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.length")
    if encoded_length > budget.maximum_bytes - budget.used:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.semantic_bytes")
    if any(key in stream for key in ("/F", "/FFilter", "/FDecodeParms")):
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.external")
    raw = memoryview(stream.get_raw_stream_buffer()).cast("B")
    if raw.nbytes != encoded_length:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.length")
    digest = hashlib.sha256()
    size = 0

    def update(block: Buffer) -> None:
        nonlocal size
        view = memoryview(block).cast("B")
        budget.consume(view.nbytes)
        size += view.nbytes
        digest.update(view)

    if not normalized or stream.get("/Filter") is None:
        for offset in range(0, raw.nbytes, _CHUNK_BYTES):
            update(raw[offset : offset + _CHUNK_BYTES])
        return size, digest.digest()
    decoder = zlib.decompressobj()
    for offset in range(0, raw.nbytes, _CHUNK_BYTES):
        block: Buffer = raw[offset : offset + _CHUNK_BYTES]
        while block:
            _checkpoint(budget.control)
            maximum = min(_CHUNK_BYTES, budget.maximum_bytes - budget.used + 1)
            decoded = decoder.decompress(block, maximum)
            update(decoded)
            block = decoder.unconsumed_tail
        if decoder.unused_data:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.flate"
            )
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "optimization.flate")
    return size, digest.digest()
