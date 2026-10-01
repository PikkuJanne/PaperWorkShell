"""Explicit one-stage PDF image rewriting from an authenticated baseline."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, cast

import pikepdf

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetImpact,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageReport,
    PdfImageStageRequest,
    stage_image_geometry,
    validate_pdf_image_stage_report,
    validate_pdf_image_stage_request,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
    PdfStructureChecker,
    _BoundedCandidateStream,
    _checkpoint,
    _measure_output,
    _remove_failed_candidate,
    _render_one,
    _same_rendered_page,
    _strict_open,
    _verify_candidate_with_qpdf,
)
from paperworkshell.pdf.image_stage_analysis import analyze_document, fail, raw_stream
from paperworkshell.pdf.image_stage_encoder import EncodedImage, encode_image, required_image_memory
from paperworkshell.pdf.optimization_adapter import _graph_fingerprint
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.renderer import PdfPageRenderer


def _ignore_progress(completed: int, total: int) -> None:
    pass


@dataclass(frozen=True, slots=True)
class PdfImageStageAdapterLimits:
    export: PdfExportAdapterLimits
    maximum_memory_bytes: int
    maximum_temporary_bytes: int
    maximum_candidate_bytes: int
    maximum_source_pixels: int

    def __post_init__(self) -> None:
        if (
            type(self.export) is not PdfExportAdapterLimits
            or any(
                type(v) is not int or v < 1
                for v in (self.maximum_memory_bytes, self.maximum_source_pixels)
            )
            or any(
                type(v) is not int or v < 0
                for v in (self.maximum_temporary_bytes, self.maximum_candidate_bytes)
            )
            or self.maximum_candidate_bytes > self.export.inspection.maximum_output_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.limits")


@dataclass(frozen=True, slots=True)
class PdfImageStageAdapterResult:
    analysis: PdfImageAnalysis
    report: PdfImageStageReport
    candidate_sha256: str | None = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class PdfImageStageAdapter:
    qpdf_checker: PdfStructureChecker = field(default_factory=QpdfCommandChecker, repr=False)
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer, repr=False)

    def process(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        request: PdfImageStageRequest,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        progress: Callable[[int, int], None] = _ignore_progress,
    ) -> PdfImageStageAdapterResult:
        """Analysis alone never writes; explicit stages retain baseline on no gain."""

        failure: tuple[PdfExportErrorCode, str]
        try:
            return self._process(
                baseline_path, candidate_path, baseline_report, request, limits, control, progress
            )
        except PdfExportError as error:
            failure = error.code, error.context
        except MemoryError, RecursionError:
            failure = PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.resources"
        except OSError:
            failure = PdfExportErrorCode.DESTINATION_FAILED, "image_stage.io"
        except Exception:
            failure = PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "image_stage.adapter"
        # No native exception values, filenames, or parser text survive this seam.
        reject_pdf_export(*failure)

    def _process(
        self,
        baseline_path: Path,
        candidate_path: Path,
        baseline_report: PdfExportVerificationReport,
        request: PdfImageStageRequest,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        progress: Callable[[int, int], None],
    ) -> PdfImageStageAdapterResult:
        if (
            not isinstance(baseline_path, Path)
            or not isinstance(candidate_path, Path)
            or type(baseline_report) is not PdfExportVerificationReport
            or type(request) is not PdfImageStageRequest
            or type(limits) is not PdfImageStageAdapterLimits
            or type(control) is not PdfExportAdapterControl
            or not callable(progress)
            or baseline_path.resolve() == candidate_path.resolve()
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.call")
        _checkpoint(control)
        total = 3 * baseline_report.page_count + len(request.selected_asset_ids)
        progress(0, total)
        baseline = _measure_output(baseline_path, limits=limits.export, control=control)
        if (baseline.sha256, baseline.byte_size) != (
            baseline_report.output_sha256,
            baseline_report.output_byte_size,
        ):
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "image_stage.baseline")
        candidate_owned = False
        keep = False
        try:
            with baseline_path.open("rb") as source, _strict_open(source) as document:
                analysis, bindings = analyze_document(
                    document,
                    baseline_sha256=baseline.sha256,
                    baseline_byte_size=baseline.byte_size,
                    limits=limits.export,
                    control=control,
                    maximum_memory_bytes=limits.maximum_memory_bytes,
                    page_progress=lambda completed: progress(completed, total),
                )
                if analysis.page_count != baseline_report.page_count:
                    fail("page_count")
                validate_pdf_image_stage_request(
                    request, analysis, baseline_report.semantic_fingerprint
                )
                if request.analysis_only or not request.selected_asset_ids:
                    result = _result(
                        analysis, baseline_report, request, (), PdfImageStageOutcome.UNCHANGED
                    )
                else:
                    capacity = min(
                        limits.maximum_candidate_bytes,
                        limits.maximum_temporary_bytes,
                        baseline.byte_size - 1,
                    )
                    replacements: dict[int, EncodedImage] = {}
                    impacts = self._encode_selected(
                        bindings,
                        analysis,
                        request,
                        limits,
                        control,
                        capacity,
                        replacements,
                        lambda completed: progress(analysis.page_count + completed, total),
                    )
                    if not replacements:
                        outcome = (
                            PdfImageStageOutcome.SKIPPED_BUDGET
                            if all(
                                impact.outcome is PdfImageStageOutcome.SKIPPED_BUDGET
                                for impact in impacts
                            )
                            else PdfImageStageOutcome.NO_REDUCTION
                        )
                        result = _result(analysis, baseline_report, request, impacts, outcome)
                    else:
                        projection = {bindings[index].objgen: index for index in replacements}
                        expected = _graph_fingerprint(
                            cast(pikepdf.Dictionary, document.trailer),
                            limits.export,
                            control,
                            limits.maximum_memory_bytes // 4,
                            projection,
                        )
                        for index, encoded in replacements.items():
                            stream = bindings[index]
                            stream.write(encoded.data, filter=pikepdf.Name.DCTDecode)
                            stream.Width = encoded.geometry.width_pixels
                            stream.Height = encoded.geometry.height_pixels
                        # x+b is mandatory: no pre-existing derivative is owned here.
                        try:
                            output = candidate_path.open("x+b")
                        except FileExistsError:
                            reject_pdf_export(
                                PdfExportErrorCode.DESTINATION_EXISTS, "image_stage.candidate"
                            )
                        candidate_owned = True
                        with output:
                            bounded = _BoundedCandidateStream(output, capacity)
                            try:
                                _write_candidate(document, bounded, control)
                                bounded.flush()
                                os.fsync(bounded.fileno())
                            except Exception:
                                if not bounded.limit_exceeded:
                                    raise
                            _checkpoint(control)
                        if bounded.limit_exceeded:
                            result = _result(
                                analysis,
                                baseline_report,
                                request,
                                _restore_impacts(impacts),
                                PdfImageStageOutcome.NO_REDUCTION,
                            )
                        else:
                            candidate = _measure_output(
                                candidate_path, limits=limits.export, control=control
                            )
                            self._verify(
                                baseline_path,
                                candidate_path,
                                analysis,
                                replacements,
                                expected,
                                limits,
                                control,
                                lambda completed: progress(
                                    analysis.page_count
                                    + len(request.selected_asset_ids)
                                    + 2 * completed,
                                    total,
                                ),
                            )
                            if candidate.byte_size >= baseline.byte_size:
                                result = _result(
                                    analysis,
                                    baseline_report,
                                    request,
                                    _restore_impacts(impacts),
                                    PdfImageStageOutcome.NO_REDUCTION,
                                    candidate_byte_size=candidate.byte_size,
                                )
                            else:
                                result = _result(
                                    analysis,
                                    baseline_report,
                                    request,
                                    impacts,
                                    PdfImageStageOutcome.REENCODED,
                                    candidate_byte_size=candidate.byte_size,
                                    output_sha256=candidate.sha256,
                                )
                                if (
                                    _measure_output(
                                        candidate_path, limits=limits.export, control=control
                                    )
                                    != candidate
                                ):
                                    fail("candidate_changed")
            if _measure_output(baseline_path, limits=limits.export, control=control) != baseline:
                fail("baseline_changed")
            progress(total, total)
            keep = result.candidate_sha256 is not None
            return result
        finally:
            if candidate_owned and not keep:
                _remove_failed_candidate(candidate_path)

    def _encode_selected(
        self,
        bindings: tuple[pikepdf.Stream, ...],
        analysis: PdfImageAnalysis,
        request: PdfImageStageRequest,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        capacity: int,
        replacements: dict[int, EncodedImage],
        progress: Callable[[int], None],
    ) -> tuple[PdfImageAssetImpact, ...]:
        assert request.profile is not None
        impacts = []
        retained = 0
        for index in request.selected_asset_ids:
            _checkpoint(control)
            asset = analysis.assets[index]
            geometry = stage_image_geometry(asset, request.profile)
            skipped = (
                capacity < 1
                or asset.width_pixels * asset.height_pixels > limits.maximum_source_pixels
                or required_image_memory(asset, geometry) + retained * 2
                > limits.maximum_memory_bytes
                or capacity + retained * 2 + asset.encoded_byte_size * 2
                > limits.maximum_temporary_bytes
            )
            encoded = (
                None
                if skipped
                else encode_image(
                    bindings[index],
                    asset,
                    geometry,
                    request.profile,
                    maximum_bytes=asset.encoded_byte_size - 1,
                    control=control,
                )
            )
            outcome = (
                PdfImageStageOutcome.SKIPPED_BUDGET
                if skipped
                else PdfImageStageOutcome.NO_REDUCTION
                if encoded is None
                else PdfImageStageOutcome.REENCODED
            )
            if encoded is not None:
                replacements[index] = encoded
                retained += len(encoded.data)
            assert (
                asset.effective_dpi_x_milli is not None and asset.effective_dpi_y_milli is not None
            )
            impacts.append(
                PdfImageAssetImpact(
                    index,
                    asset.page_indexes,
                    asset.width_pixels,
                    asset.height_pixels,
                    asset.width_pixels if encoded is None else geometry.width_pixels,
                    asset.height_pixels if encoded is None else geometry.height_pixels,
                    asset.effective_dpi_x_milli,
                    asset.effective_dpi_y_milli,
                    asset.effective_dpi_x_milli
                    if encoded is None
                    else geometry.effective_dpi_x_milli,
                    asset.effective_dpi_y_milli
                    if encoded is None
                    else geometry.effective_dpi_y_milli,
                    asset.color_mode,
                    asset.encoded_byte_size,
                    geometry.estimated_encoded_byte_size,
                    None if encoded is None else len(encoded.data),
                    asset.encoded_byte_size if encoded is None else len(encoded.data),
                    outcome,
                )
            )
            progress(len(impacts))
        return tuple(impacts)

    def _verify(
        self,
        baseline_path: Path,
        candidate_path: Path,
        analysis: PdfImageAnalysis,
        replacements: dict[int, EncodedImage],
        expected: str,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        progress: Callable[[int], None],
    ) -> None:
        with candidate_path.open("rb") as source, _strict_open(source) as document:
            current, bindings = analyze_document(
                document,
                baseline_sha256=analysis.baseline_sha256,
                baseline_byte_size=analysis.baseline_byte_size,
                limits=limits.export,
                control=control,
                maximum_memory_bytes=limits.maximum_memory_bytes,
            )
            if current.page_count != analysis.page_count or len(current.assets) != len(
                analysis.assets
            ):
                fail("candidate_inventory")
            projection = {}
            for index, encoded in replacements.items():
                stream = bindings[index]
                original = analysis.assets[index]
                actual = current.assets[index]
                if (
                    actual.page_indexes != original.page_indexes
                    or actual.color_mode is not original.color_mode
                    or actual.width_pixels != encoded.geometry.width_pixels
                    or actual.height_pixels != encoded.geometry.height_pixels
                    or stream.get("/BitsPerComponent") != 8
                    or stream.get("/Filter") != pikepdf.Name.DCTDecode
                    or "/DecodeParms" in stream
                    or hashlib.sha256(raw_stream(stream, len(encoded.data))).hexdigest()
                    != encoded.sha256
                ):
                    fail("candidate_image")
                projection[stream.objgen] = index
            actual_digest = _graph_fingerprint(
                cast(pikepdf.Dictionary, document.trailer),
                limits.export,
                control,
                limits.maximum_memory_bytes // 4,
                projection,
            )
            if actual_digest != expected:
                fail("candidate_semantics")
            # Native syntax checking and qpdf remain under the worker Job/deadline;
            # the Python comparator never expands opaque filters itself.
            if document.check_pdf_syntax(progress=lambda _percent: _checkpoint(control)):
                fail("candidate_syntax")
        _verify_candidate_with_qpdf(
            self.qpdf_checker, candidate_path, limits=limits.export, control=control
        )
        changed_pages = {
            page for index in replacements for page in analysis.assets[index].page_indexes
        }
        for page_index in range(analysis.page_count):
            before = _render_one(
                self.renderer,
                baseline_path,
                page_index=page_index,
                rotation=PdfRenderRotation.DEGREES_0,
                limits=limits.export,
                control=control,
            )
            after = _render_one(
                self.renderer,
                candidate_path,
                page_index=page_index,
                rotation=PdfRenderRotation.DEGREES_0,
                limits=limits.export,
                control=control,
            )
            # Changed pages are deliberately lossy. Bind all render geometry and
            # flags, but do not advertise pixel equality for these pages.
            expected_render = (
                before
                if page_index not in changed_pages
                else replace(before, pixel_sha256=after.pixel_sha256)
            )
            if not _same_rendered_page(expected_render, after):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "image_stage.render"
                )
            progress(page_index + 1)


def _restore_impacts(impacts: tuple[PdfImageAssetImpact, ...]) -> tuple[PdfImageAssetImpact, ...]:
    return tuple(
        replace(
            impact,
            output_width_pixels=impact.original_width_pixels,
            output_height_pixels=impact.original_height_pixels,
            output_effective_dpi_x_milli=impact.original_effective_dpi_x_milli,
            output_effective_dpi_y_milli=impact.original_effective_dpi_y_milli,
            selected_encoded_byte_size=impact.original_encoded_byte_size,
            outcome=PdfImageStageOutcome.NO_REDUCTION,
        )
        if impact.outcome is PdfImageStageOutcome.REENCODED
        else impact
        for impact in impacts
    )


def _write_candidate(
    document: pikepdf.Pdf, stream: _BoundedCandidateStream, control: PdfExportAdapterControl
) -> None:
    """Preserve all existing stream encodings; only explicit JPEGs are new."""

    _checkpoint(control)
    document.save(
        cast(BinaryIO, stream),
        compress_streams=False,
        stream_decode_level=pikepdf.StreamDecodeLevel.none,
        recompress_flate=False,
        object_stream_mode=pikepdf.ObjectStreamMode.preserve,
        normalize_content=False,
        linearize=False,
        fix_metadata_version=False,
        deterministic_id=True,
        encryption=False,
        progress=lambda _percent: _checkpoint(control),
    )
    _checkpoint(control)


def _result(
    analysis: PdfImageAnalysis,
    baseline_report: PdfExportVerificationReport,
    request: PdfImageStageRequest,
    impacts: tuple[PdfImageAssetImpact, ...],
    outcome: PdfImageStageOutcome,
    *,
    candidate_byte_size: int | None = None,
    output_sha256: str | None = None,
) -> PdfImageStageAdapterResult:
    pages = []
    for page in analysis.pages:
        selected = tuple(impact for impact in impacts if page.page_index in impact.page_indexes)
        original = sum(impact.original_encoded_byte_size for impact in selected)
        estimated = (
            None
            if any(impact.estimated_encoded_byte_size is None for impact in selected)
            else page.image_encoded_byte_size
            - original
            + sum(impact.estimated_encoded_byte_size or 0 for impact in selected)
        )
        pages.append(
            PdfImagePageImpact(
                page.page_index,
                tuple(impact.asset_id for impact in selected),
                page.image_encoded_byte_size,
                estimated,
                page.image_encoded_byte_size
                - original
                + sum(impact.selected_encoded_byte_size for impact in selected),
            )
        )
    report = PdfImageStageReport(
        request.profile,
        outcome,
        baseline_report.semantic_fingerprint,
        analysis.baseline_sha256,
        analysis.baseline_byte_size,
        candidate_byte_size,
        output_sha256 or analysis.baseline_sha256,
        candidate_byte_size
        if output_sha256 is not None and candidate_byte_size is not None
        else analysis.baseline_byte_size,
        analysis.page_count,
        impacts,
        tuple(pages),
        analysis.page_count,
    )
    validate_pdf_image_stage_report(request, analysis, report)
    return PdfImageStageAdapterResult(analysis, report, output_sha256)
