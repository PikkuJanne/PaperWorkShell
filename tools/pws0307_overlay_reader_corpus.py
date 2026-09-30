"""Generate the deterministic PWS-0307 flattened-overlay reader corpus.

The tool writes only to the two explicitly requested destinations.  It uses
the production flattened-export adapter, including its fixed qpdf and PDFium
verification seams, and records a content-free automated evidence summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, TextIO
from uuid import UUID

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_OBJECTS,
    MAX_PDF_INSPECTION_PAGES,
    PdfInspectionError,
    PdfInspectionLimits,
)
from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    PdfPageRenderLimits,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportAsset,
    FlattenedPdfExportError,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset, OverlayRenderLimits
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.pdf.flattened_export_adapter import FlattenedPdfExportAdapter
from paperworkshell.pdf.overlay_renderer import (
    EXPECTED_FONTTOOLS_VERSION,
    EXPECTED_FPDF2_VERSION,
    EXPECTED_OCRMYPDF_VERSION,
    EXPECTED_PIKEPDF_VERSION,
    EXPECTED_PILLOW_VERSION,
    EXPECTED_QPDF_VERSION,
    EXPECTED_UHARFBUZZ_VERSION,
    OVERLAY_FONT_FAMILY,
    OVERLAY_FONT_RESOURCE_SHA256,
    OVERLAY_FONT_VERSION,
)
from paperworkshell.pdf.pdfium_renderer import (
    EXPECTED_PDFIUM_VERSION,
    EXPECTED_PYPDFIUM2_VERSION,
)
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE, QpdfCommandChecker

GENERATOR_VERSION: Final = 1
EVIDENCE_VERSION: Final = 1
TASK_ID: Final = "PWS-0307"

_ROOT: Final = Path(__file__).resolve().parents[1]
_BORN_DIGITAL_SOURCE: Final = _ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf"
_GEOMETRY_SOURCE: Final = _ROOT / "tests/fixtures/synthetic/v1/pdf/render-geometry-matrix.pdf"
_BORN_DIGITAL_PAGE_COUNT: Final = 1
_GEOMETRY_PAGE_COUNT: Final = 6
_DEADLINE_SECONDS: Final = 120.0
_VERIFICATION_DIMENSION_PIXELS: Final = 1_024
_RGBA_BYTES_PER_PIXEL: Final = 4
_MINIMUM_OBJECT_LIMIT: Final = 4_096
_OBJECTS_PER_PAGE_LIMIT: Final = 4_096

_CASE_ID: Final = UUID("73070000-0000-4000-8000-000000000001")
_BORN_DIGITAL_SOURCE_ID: Final = UUID("73070100-0000-4000-8000-000000000001")
_GEOMETRY_SOURCE_ID: Final = UUID("73070100-0000-4000-8000-000000000002")
_PAGE_IDS: Final = tuple(UUID(f"73070200-0000-4000-8000-{index:012x}") for index in range(1, 6))
_OVERLAY_IDS: Final = tuple(UUID(f"73070300-0000-4000-8000-{index:012x}") for index in range(1, 9))
_SIGNATURE_ASSET_ID: Final = UUID("73070400-0000-4000-8000-000000000001")
_MANIFEST_SHA256: Final = hashlib.sha256(b"pws-0307-overlay-reader-corpus-plan-v1").hexdigest()
_MANIFEST_CAS_TOKEN: Final = "pws0307-reader-corpus-v1"
_ENCODED_SIGNATURE_FACT: Final = b"pws-0307-synthetic-signature-image-v1"

_BLACK: Final = RgbaColor("#102030ff")
_BLUE: Final = RgbaColor("#0060ffff")
_RED: Final = RgbaColor("#c02020ff")
_SHAPE_FILL: Final = RgbaColor("#d8e8ff80")
_SHAPE_STROKE: Final = RgbaColor("#204080ff")
_HIGHLIGHT_FILL: Final = RgbaColor("#ffe05070")
_HIGHLIGHT_STROKE: Final = RgbaColor("#806000ff")


@dataclass(slots=True, repr=False)
class ReaderCorpusError(RuntimeError):
    """Stable, path-free failure emitted by the standalone generator."""

    code: str
    safe_message: str

    def __post_init__(self) -> None:
        self.args = (self.code, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code}: {self.safe_message}"


@dataclass(frozen=True, slots=True)
class _MeasuredSource:
    byte_size: int
    sha256: str


def _uuid_set(plan: FlattenedPdfExportPlan) -> tuple[UUID, ...]:
    return (
        plan.base_plan.case_id,
        *(source.source_id for source in plan.base_plan.sources),
        *(page.page_id for page in plan.base_plan.pages),
        *(record.overlay_id for page in plan.pages for record in page.overlays),
        *(asset.asset_id for asset in plan.assets),
    )


def _measure_source(path: Path, policy: ResourcePolicy) -> _MeasuredSource:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb", buffering=0) as stream:
            while True:
                block = stream.read(policy.import_io_chunk_bytes)
                if not block:
                    break
                total += len(block)
                if total > policy.maximum_source_bytes:
                    raise ReaderCorpusError(
                        "PWS0307_CORPUS_SOURCE_LIMIT",
                        "A synthetic corpus source exceeds the approved resource limit.",
                    )
                digest.update(block)
    except ReaderCorpusError:
        raise
    except OSError:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_SOURCE_UNAVAILABLE",
            "A required synthetic corpus source is unavailable.",
        ) from None
    if total < 1:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_SOURCE_INVALID",
            "A required synthetic corpus source is empty.",
        )
    return _MeasuredSource(total, digest.hexdigest())


def _source(
    source_id: UUID,
    measured: _MeasuredSource,
    page_count: int,
) -> PdfExportSource:
    return PdfExportSource(
        source_id=source_id,
        byte_size=measured.byte_size,
        page_count=page_count,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256=measured.sha256,
    )


def _text_record(
    overlay_id: UUID,
    page_id: UUID,
    kind: OverlayKind,
    value: str,
    box: NormalizedPageBox,
    z_index: int,
    *,
    size: int,
) -> OverlayRecord:
    return OverlayRecord(
        overlay_id=overlay_id,
        page_id=page_id,
        box=box,
        z_index=z_index,
        payload=TextualOverlayPayload(
            kind,
            value,
            DEFAULT_FONT_TOKEN,
            size,
            _BLACK,
        ),
    )


def _mark_record(
    overlay_id: UUID,
    page_id: UUID,
    kind: OverlayKind,
    box: NormalizedPageBox,
    z_index: int,
    color: RgbaColor,
) -> OverlayRecord:
    return OverlayRecord(
        overlay_id=overlay_id,
        page_id=page_id,
        box=box,
        z_index=z_index,
        payload=MarkOverlayPayload(kind, color),
    )


def _shape_record(
    overlay_id: UUID,
    page_id: UUID,
    kind: OverlayKind,
    shape: ShapePrimitive,
    box: NormalizedPageBox,
    z_index: int,
    fill: RgbaColor,
    stroke: RgbaColor,
) -> OverlayRecord:
    return OverlayRecord(
        overlay_id=overlay_id,
        page_id=page_id,
        box=box,
        z_index=z_index,
        payload=ShapeOverlayPayload(kind, shape, fill, stroke),
    )


def _signature_pixels() -> bytes:
    width = 64
    height = 24
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            phase = x % 16
            center = 5 + phase if phase < 8 else 20 - phase
            ink = abs(y - center) <= 1 or (47 <= x <= 61 and abs(y - 18) <= 1)
            pixels.extend((16, 48, 96, 255) if ink else (255, 255, 255, 0))
    return bytes(pixels)


def _signature_asset() -> tuple[
    FlattenedPdfExportAsset,
    SignatureImageOverlayRasterSource,
]:
    pixels = _signature_pixels()
    raster = OverlayRasterAsset(
        asset_id=_SIGNATURE_ASSET_ID,
        width_pixels=64,
        height_pixels=24,
        row_stride_bytes=64 * _RGBA_BYTES_PER_PIXEL,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    encoded = OverlayAssetRecord(
        asset_id=_SIGNATURE_ASSET_ID,
        kind=OverlayAssetKind.SIGNATURE_IMAGE,
        storage_name=f"{_SIGNATURE_ASSET_ID}.bin",
        media_type=OverlayAssetMediaType.PNG,
        byte_size=len(_ENCODED_SIGNATURE_FACT),
        sha256=hashlib.sha256(_ENCODED_SIGNATURE_FACT).hexdigest(),
    )
    return (
        FlattenedPdfExportAsset(encoded, raster),
        SignatureImageOverlayRasterSource(raster, pixels),
    )


def build_reader_corpus_plan(
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> FlattenedPdfExportPlan:
    """Build the fixed path-free plan used by automated and manual evidence."""

    if type(policy) is not ResourcePolicy:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_POLICY_INVALID",
            "The synthetic corpus resource policy is invalid.",
        )
    born = _source(
        _BORN_DIGITAL_SOURCE_ID,
        _measure_source(_BORN_DIGITAL_SOURCE, policy),
        _BORN_DIGITAL_PAGE_COUNT,
    )
    geometry = _source(
        _GEOMETRY_SOURCE_ID,
        _measure_source(_GEOMETRY_SOURCE, policy),
        _GEOMETRY_PAGE_COUNT,
    )
    if born.byte_size + geometry.byte_size > policy.maximum_source_bytes:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_SOURCE_LIMIT",
            "The synthetic corpus sources exceed the approved resource limit.",
        )

    page_specs = (
        (_BORN_DIGITAL_SOURCE_ID, 0, PdfExportRotation.DEGREES_0),
        (_GEOMETRY_SOURCE_ID, 0, PdfExportRotation.DEGREES_90),
        (_GEOMETRY_SOURCE_ID, 1, PdfExportRotation.DEGREES_0),
        (_GEOMETRY_SOURCE_ID, 4, PdfExportRotation.DEGREES_270),
        (_GEOMETRY_SOURCE_ID, 5, PdfExportRotation.DEGREES_180),
    )
    base_pages = tuple(
        PdfExportPage(
            output_page_index=index,
            page_id=_PAGE_IDS[index],
            source_id=source_id,
            source_page_index=source_page_index,
            additional_rotation=rotation,
        )
        for index, (source_id, source_page_index, rotation) in enumerate(page_specs)
    )
    base_plan = PdfExportPlan(
        case_id=_CASE_ID,
        manifest_revision=1,
        manifest_sha256=_MANIFEST_SHA256,
        manifest_cas_token=_MANIFEST_CAS_TOKEN,
        sources=(born, geometry),
        pages=base_pages,
    )
    signature_asset, _ = _signature_asset()
    crops = (
        NormalizedPageBox.full_page(),
        NormalizedPageBox(100_000, 100_000, 800_000, 750_000),
        NormalizedPageBox(100_000, 100_000, 800_000, 750_000),
        NormalizedPageBox(100_000, 100_000, 800_000, 750_000),
        NormalizedPageBox(125_000, 125_000, 750_000, 750_000),
    )
    overlays = (
        (
            _text_record(
                _OVERLAY_IDS[0],
                _PAGE_IDS[0],
                OverlayKind.TEXT,
                "Reader matrix text",
                NormalizedPageBox(80_000, 80_000, 620_000, 80_000),
                0,
                size=18,
            ),
            _text_record(
                _OVERLAY_IDS[1],
                _PAGE_IDS[0],
                OverlayKind.DATE,
                "2026-08-25",
                NormalizedPageBox(80_000, 200_000, 300_000, 80_000),
                1,
                size=16,
            ),
            _text_record(
                _OVERLAY_IDS[2],
                _PAGE_IDS[0],
                OverlayKind.INITIALS,
                "PWS",
                NormalizedPageBox(80_000, 320_000, 180_000, 90_000),
                2,
                size=18,
            ),
        ),
        (
            _mark_record(
                _OVERLAY_IDS[3],
                _PAGE_IDS[1],
                OverlayKind.CHECKMARK,
                NormalizedPageBox(180_000, 180_000, 220_000, 220_000),
                0,
                _BLUE,
            ),
            _mark_record(
                _OVERLAY_IDS[4],
                _PAGE_IDS[1],
                OverlayKind.CROSS,
                NormalizedPageBox(500_000, 180_000, 220_000, 220_000),
                1,
                _RED,
            ),
        ),
        (
            _shape_record(
                _OVERLAY_IDS[5],
                _PAGE_IDS[2],
                OverlayKind.SHAPE,
                ShapePrimitive.ELLIPSE,
                NormalizedPageBox(200_000, 250_000, 500_000, 300_000),
                0,
                _SHAPE_FILL,
                _SHAPE_STROKE,
            ),
        ),
        (
            OverlayRecord(
                overlay_id=_OVERLAY_IDS[6],
                page_id=_PAGE_IDS[3],
                box=NormalizedPageBox(180_000, 250_000, 600_000, 300_000),
                z_index=0,
                payload=SignatureImageOverlayPayload(_SIGNATURE_ASSET_ID),
            ),
        ),
        (
            _shape_record(
                _OVERLAY_IDS[7],
                _PAGE_IDS[4],
                OverlayKind.HIGHLIGHT,
                ShapePrimitive.RECTANGLE,
                NormalizedPageBox(200_000, 250_000, 550_000, 250_000),
                0,
                _HIGHLIGHT_FILL,
                _HIGHLIGHT_STROKE,
            ),
        ),
    )
    plan = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=tuple(
            FlattenedPdfExportPage(
                base_page=base_page,
                active_crop=crops[index],
                overlays=overlays[index],
            )
            for index, base_page in enumerate(base_pages)
        ),
        assets=(signature_asset,),
    )
    identities = _uuid_set(plan)
    if len(set(identities)) != len(identities):
        raise ReaderCorpusError(
            "PWS0307_CORPUS_IDENTITY_COLLISION",
            "The synthetic corpus contains an internal identity collision.",
        )
    return plan


def _adapter_limits(policy: ResourcePolicy, page_count: int) -> PdfExportAdapterLimits:
    maximum_pages = min(policy.maximum_page_count, MAX_PDF_INSPECTION_PAGES, MAX_PDF_RENDER_PAGES)
    maximum_pixels = min(policy.maximum_preview_pixels_per_page, MAX_PDF_RENDER_PIXELS)
    maximum_dimension = min(
        MAX_PDF_RENDER_DIMENSION_PIXELS,
        math.isqrt(maximum_pixels),
    )
    verification_dimension = min(_VERIFICATION_DIMENSION_PIXELS, maximum_dimension)
    bitmap_bytes = min(
        maximum_pixels * _RGBA_BYTES_PER_PIXEL,
        policy.worker_process_memory_bytes // 8,
        MAX_PDF_RENDER_BYTES,
    )
    maximum_output = policy.maximum_temporary_storage_bytes // 2
    object_limit = min(
        MAX_PDF_INSPECTION_OBJECTS,
        max(_MINIMUM_OBJECT_LIMIT, page_count * _OBJECTS_PER_PAGE_LIMIT),
    )
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=maximum_pages,
            maximum_objects=object_limit,
            maximum_input_bytes=policy.maximum_source_bytes,
            maximum_temporary_bytes=maximum_output,
            maximum_output_bytes=maximum_output,
            maximum_tool_output_bytes=min(policy.import_io_chunk_bytes, maximum_output),
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=maximum_pages,
            maximum_width_pixels=maximum_dimension,
            maximum_height_pixels=maximum_dimension,
            maximum_pixels=maximum_pixels,
            maximum_bitmap_bytes=bitmap_bytes,
            maximum_destination_bytes=bitmap_bytes,
            maximum_memory_bytes=bitmap_bytes,
            wall_time_ms=policy.worker_cpu_time_ms,
        ),
        maximum_total_source_bytes=policy.maximum_source_bytes,
        verification_width_pixels=verification_dimension,
        verification_height_pixels=verification_dimension,
    )


def _resolve_destinations(output_pdf: Path, evidence_json: Path) -> tuple[Path, Path]:
    if not isinstance(output_pdf, Path) or not isinstance(evidence_json, Path):
        raise ReaderCorpusError(
            "PWS0307_CORPUS_DESTINATION_INVALID",
            "Both corpus destinations must be explicit file paths.",
        )
    pdf = output_pdf.resolve(strict=False)
    evidence = evidence_json.resolve(strict=False)
    if (
        pdf == evidence
        or pdf.suffix.lower() != ".pdf"
        or evidence.suffix.lower() != ".json"
        or not pdf.parent.is_dir()
        or not evidence.parent.is_dir()
    ):
        raise ReaderCorpusError(
            "PWS0307_CORPUS_DESTINATION_INVALID",
            "Choose distinct existing-parent PDF and JSON destinations.",
        )
    if pdf.exists() or evidence.exists():
        raise ReaderCorpusError(
            "PWS0307_CORPUS_DESTINATION_EXISTS",
            "The corpus generator does not replace an existing destination.",
        )
    return pdf, evidence


def _evidence_summary(
    plan: FlattenedPdfExportPlan,
    report: FlattenedPdfExportVerificationReport,
) -> dict[str, object]:
    page_roles = (
        "born_digital_text_date_initials",
        "cropped_additional_rotation_marks",
        "intrinsic_rotation_shape",
        "mixed_size_signature_image",
        "user_unit_crop_highlight",
    )
    source_roles = {
        _BORN_DIGITAL_SOURCE_ID: "born_digital",
        _GEOMETRY_SOURCE_ID: "geometry_matrix",
    }
    return {
        "evidence_version": EVIDENCE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "task_id": TASK_ID,
        "result": "automated_verification_passed",
        "artifact": {
            "sha256": report.base_report.output_sha256,
            "byte_size": report.base_report.output_byte_size,
            "page_count": report.base_report.page_count,
            "semantic_fingerprint": plan.semantic_fingerprint,
        },
        "sources": [
            {
                "role": source_roles[source.source_id],
                "sha256": source.sha256,
                "byte_size": source.byte_size,
                "page_count": source.page_count,
            }
            for source in plan.base_plan.sources
        ],
        "coverage": {
            "overlay_kinds": [record.kind.value for page in plan.pages for record in page.overlays],
            "pages": [
                {
                    "page_index": page.output_page_index,
                    "role": page_roles[page.output_page_index],
                    "source_role": source_roles[page.base_page.source_id],
                    "source_page_index": page.base_page.source_page_index,
                    "additional_rotation_degrees": int(page.base_page.additional_rotation),
                    "active_crop": page.active_crop.to_manifest(),
                    "overlay_kinds": [record.kind.value for record in page.overlays],
                }
                for page in plan.pages
            ],
            "intrinsic_rotations_degrees": [0, 0, 90, 0, 0],
            "user_unit_values": [1, 1, 1, 1, 2],
            "mixed_page_sizes": True,
            "transparent_signature_raster": True,
            "interactive_appearance_dependency": False,
            "signature_marker_terminology": "placed_signature_image_visual_mark",
        },
        "font_strategy": {
            "token": DEFAULT_FONT_TOKEN,
            "family": OVERLAY_FONT_FAMILY,
            "version": OVERLAY_FONT_VERSION,
            "resource_sha256": OVERLAY_FONT_RESOURCE_SHA256,
            "embedding": "subset_type0_cidfonttype2_fontfile2_tounicode",
        },
        "automated_checks": {
            "qpdf": {
                "profile": "check_no_recovery_no_warnings_v1",
                "version": EXPECTED_QPDF_VERSION,
                "result": "pass",
            },
            "base_check_codes": [check.code.value for check in report.base_report.checks],
            "flattened_check_codes": [check.code.value for check in report.checks],
            "strict_reopen": "pass",
            "all_pages_pdfium_rendered": "pass",
            "overlay_form_presence": "pass",
            "noninteractive_output_graph": "pass",
        },
        "engine_versions": {
            "fpdf2": EXPECTED_FPDF2_VERSION,
            "fonttools": EXPECTED_FONTTOOLS_VERSION,
            "uharfbuzz": EXPECTED_UHARFBUZZ_VERSION,
            "pillow": EXPECTED_PILLOW_VERSION,
            "pikepdf": EXPECTED_PIKEPDF_VERSION,
            "qpdf": EXPECTED_QPDF_VERSION,
            "ocrmypdf": EXPECTED_OCRMYPDF_VERSION,
            "pypdfium2": EXPECTED_PYPDFIUM2_VERSION,
            "pdfium": EXPECTED_PDFIUM_VERSION,
        },
        "manual_reader_matrix": {
            "result": "not_run",
            "required_windows_viewers": [
                "Adobe Acrobat Reader",
                "Microsoft Edge PDF viewer",
                "Google Chrome PDF viewer",
                "Mozilla Firefox PDF viewer",
            ],
        },
        "claim_boundaries": {
            "universal_reader_compatibility": False,
            "pdf_ua_conformance": False,
            "portal_acceptance": False,
            "digital_signature_creation": False,
        },
    }


def _open_evidence_reservation(path: Path) -> TextIO:
    try:
        return path.open("x", encoding="utf-8", newline="\n")
    except FileExistsError:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_DESTINATION_EXISTS",
            "The corpus generator does not replace an existing destination.",
        ) from None
    except OSError:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_DESTINATION_FAILED",
            "The corpus evidence destination could not be reserved safely.",
        ) from None


def _configured_qpdf_checker(deadline: float) -> QpdfCommandChecker:
    """Use the desktop/CI qpdf override and verify the pinned executable version."""

    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    try:
        executable = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
        checker = QpdfCommandChecker(executable=executable)
        checker.require_trusted_version(deadline=deadline)
        return checker
    except OSError, TypeError, ValueError, PdfInspectionError:
        raise ReaderCorpusError(
            "PWS0307_CORPUS_QPDF_UNAVAILABLE",
            "The pinned qpdf checker is unavailable or its configuration is invalid.",
        ) from None


def generate_reader_corpus(
    output_pdf: Path,
    evidence_json: Path,
    *,
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> dict[str, object]:
    """Generate one exclusively new PDF/evidence pair or leave neither new output."""

    pdf, evidence = _resolve_destinations(output_pdf, evidence_json)
    plan = build_reader_corpus_plan(policy)
    deadline = time.monotonic() + _DEADLINE_SECONDS
    qpdf_checker = _configured_qpdf_checker(deadline)
    _, raster_source = _signature_asset()
    evidence_stream = _open_evidence_reservation(evidence)
    generated_pdf = False
    try:
        with ExitStack() as stack:
            source_streams: dict[UUID, BinaryIO] = {
                _BORN_DIGITAL_SOURCE_ID: stack.enter_context(
                    _BORN_DIGITAL_SOURCE.open("rb", buffering=0)
                ),
                _GEOMETRY_SOURCE_ID: stack.enter_context(_GEOMETRY_SOURCE.open("rb", buffering=0)),
            }
            report = FlattenedPdfExportAdapter(qpdf_checker=qpdf_checker).assemble_and_verify(
                plan,
                source_streams,
                {_SIGNATURE_ASSET_ID: raster_source},
                pdf,
                _adapter_limits(policy, plan.page_count),
                OverlayRenderLimits.from_resource_policy(policy),
                PdfExportAdapterControl(deadline=deadline),
            )
        generated_pdf = True
        summary = _evidence_summary(plan, report)
        payload = (
            json.dumps(
                summary,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        evidence_stream.write(payload)
        evidence_stream.flush()
        os.fsync(evidence_stream.fileno())
        evidence_stream.close()
        return summary
    except BaseException:
        try:
            evidence_stream.close()
        except OSError:
            pass
        try:
            evidence.unlink(missing_ok=True)
        except OSError:
            pass
        if generated_pdf:
            try:
                pdf.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the PWS-0307 flattened-overlay reader corpus and evidence summary."
    )
    parser.add_argument("--output-pdf", required=True, type=Path)
    parser.add_argument("--evidence-json", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        generate_reader_corpus(arguments.output_pdf, arguments.evidence_json)
    except ReaderCorpusError as error:
        print(str(error), file=sys.stderr)
        return 2
    except FlattenedPdfExportError as error:
        print(f"{error.code.value}: {error.safe_message}", file=sys.stderr)
        return 2
    except OSError:
        print(
            "PWS0307_CORPUS_IO_FAILED: The synthetic reader corpus could not be written safely.",
            file=sys.stderr,
        )
        return 2
    print("PWS-0307 reader corpus and content-free evidence generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
