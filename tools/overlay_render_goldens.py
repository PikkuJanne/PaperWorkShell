"""Closed capture and validation harness for PWS-0303 overlay-renderer goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Final
from uuid import UUID

from PIL import Image

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
)
from paperworkshell.domain.pdf_geometry import PdfPageGeometryLimits
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OverlayPageGeometry,
    OverlayPageRenderRequest,
    OverlayRasterAsset,
    OverlayRasterSource,
    OverlayRenderLimits,
)
from paperworkshell.pdf.overlay_renderer import (
    EXPECTED_FONTTOOLS_VERSION,
    EXPECTED_FPDF2_VERSION,
    EXPECTED_OCRMYPDF_VERSION,
    EXPECTED_PIKEPDF_VERSION,
    EXPECTED_PILLOW_VERSION,
    EXPECTED_QPDF_VERSION,
    EXPECTED_UHARFBUZZ_VERSION,
    EXPECTED_ZLIB_RUNTIME_VERSION,
    EXPECTED_ZLIB_VERSION,
    EXPECTED_ZLIBNG_VERSION,
    OVERLAY_FONT_RESOURCE_SHA256,
    OVERLAY_FONT_VERSION,
    FpdfOverlayRenderer,
)
from paperworkshell.pdf.pdfium_renderer import (
    EXPECTED_PDFIUM_VERSION,
    EXPECTED_PYPDFIUM2_VERSION,
    PdfiumPageRenderer,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
GOLDEN_ROOT: Final = PROJECT_ROOT / "tests" / "golden" / "overlay-renderer-v1"
GOLDEN_SET_ID: Final = "paperworkshell-overlay-renderer-v1"
GOLDEN_SCHEMA_VERSION: Final = 1
PAGE_ID: Final = UUID("30000000-0000-4000-8000-000000000303")
ASSET_ID: Final = UUID("40000000-0000-4000-8000-000000000303")
CANVAS_WIDTH: Final = 480
CANVAS_HEIGHT: Final = 600
CASE_IDS: Final = ("primitives", "unicode-shaping", "signature-image")
APPROVED_AT: Final = "2026-08-22"
APPROVAL_REVIEWER_ROLE: Final = "implementation-agent"
APPROVAL_RATIONALE: Final = "corrected-pws-0303-rendering-baseline-after-audit"
_CANDIDATE_READ_CHUNK_BYTES: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class GoldenApproval:
    """Explicit closed review record required to promote a candidate."""

    reviewed_at: str
    reviewer_role: str
    rationale: str

    def __post_init__(self) -> None:
        try:
            parsed_date = date.fromisoformat(self.reviewed_at)
        except TypeError, ValueError:
            raise ValueError("approval record is invalid") from None
        if (
            parsed_date.isoformat() != self.reviewed_at
            or self.reviewed_at != APPROVED_AT
            or self.reviewer_role != APPROVAL_REVIEWER_ROLE
            or self.rationale != APPROVAL_RATIONALE
        ):
            raise ValueError("approval record is invalid")


class _RasterSource(BytesIO):
    def __init__(self, metadata: OverlayRasterAsset, pixels: bytes) -> None:
        super().__init__(pixels)
        self._metadata = metadata

    @property
    def metadata(self) -> OverlayRasterAsset:
        return self._metadata


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    purpose: str
    request: OverlayPageRenderRequest
    sources: dict[UUID, OverlayRasterSource]
    expected_text: str


@dataclass(frozen=True, slots=True)
class GoldenArtifact:
    case_id: str
    purpose: str
    pdf_bytes: bytes
    png_bytes: bytes
    rgba_bytes: bytes
    width_pixels: int
    height_pixels: int
    expected_text: str


def _overlay_id(index: int) -> UUID:
    return UUID(f"60000000-0000-4000-8000-{index:012x}")


def _record(
    index: int,
    payload: (
        TextualOverlayPayload
        | MarkOverlayPayload
        | ShapeOverlayPayload
        | SignatureImageOverlayPayload
    ),
    box: NormalizedPageBox,
) -> OverlayRecord:
    return OverlayRecord(_overlay_id(index), PAGE_ID, box, index, payload)


def _text(
    index: int,
    value: str,
    box: NormalizedPageBox,
    *,
    kind: OverlayKind = OverlayKind.TEXT,
    size: int = 18,
    color: str = "#102030ff",
) -> OverlayRecord:
    return _record(
        index,
        TextualOverlayPayload(
            kind,
            value,
            DEFAULT_FONT_TOKEN,
            size,
            RgbaColor(color),
        ),
        box,
    )


def _request(
    records: tuple[OverlayRecord, ...],
    *,
    assets: tuple[OverlayAssetRecord, ...] = (),
) -> OverlayPageRenderRequest:
    box = PdfUserSpaceBox(0.0, 0.0, float(CANVAS_WIDTH), float(CANVAS_HEIGHT))
    geometry = PdfPageCoordinateGeometry(
        box,
        box,
        PdfPageBoxSelection.MEDIA,
        ClockwiseRotation.DEGREES_0,
    )
    collection = OverlayCollection(
        assets,
        records,
        known_page_ids=(PAGE_ID,),
        active_page_ids=(PAGE_ID,),
    )
    return OverlayPageRenderRequest.from_collection(
        collection,
        PAGE_ID,
        geometry=OverlayPageGeometry.from_pdf_geometry(geometry),
        active_crop=NormalizedPageBox.full_page(),
    )


def _primitives_case() -> GoldenCase:
    records = (
        _text(
            1,
            "Synthetic overlay text",
            NormalizedPageBox(60_000, 45_000, 880_000, 70_000),
            size=20,
        ),
        _text(
            2,
            "31/12/2030",
            NormalizedPageBox(60_000, 145_000, 360_000, 65_000),
            kind=OverlayKind.DATE,
            size=16,
            color="#303030ff",
        ),
        _text(
            3,
            "XY",
            NormalizedPageBox(520_000, 145_000, 150_000, 65_000),
            kind=OverlayKind.INITIALS,
            size=18,
            color="#203080ff",
        ),
        _record(
            4,
            MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#008040ff")),
            NormalizedPageBox(80_000, 285_000, 125_000, 100_000),
        ),
        _record(
            5,
            MarkOverlayPayload(OverlayKind.CROSS, RgbaColor("#a02020ff")),
            NormalizedPageBox(275_000, 285_000, 125_000, 100_000),
        ),
        _record(
            6,
            ShapeOverlayPayload(
                OverlayKind.HIGHLIGHT,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#ffff0070"),
                RgbaColor("#b09000ff"),
            ),
            NormalizedPageBox(500_000, 280_000, 390_000, 110_000),
        ),
        _record(
            7,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.ELLIPSE,
                RgbaColor("#3090e040"),
                RgbaColor("#205090ff"),
            ),
            NormalizedPageBox(80_000, 500_000, 300_000, 220_000),
        ),
        _record(
            8,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.LINE,
                RgbaColor("#00000000"),
                RgbaColor("#603090ff"),
            ),
            NormalizedPageBox(500_000, 540_000, 390_000, 160_000),
        ),
    )
    return GoldenCase(
        "primitives",
        "Text, date, initials, check, cross, highlight, ellipse, and line placement.",
        _request(records),
        {},
        "Synthetic overlay text\r\n31/12/2030 XY",
    )


def _unicode_case() -> GoldenCase:
    lines = (
        "Résumé Ångström",
        "Ελληνικά Ωμέγα",
        "Русский ЖЯ",
        "हिन्दी नमस्ते",
    )
    records = tuple(
        _text(
            index,
            value,
            NormalizedPageBox(80_000, 90_000 + (index - 1) * 180_000, 840_000, 110_000),
            size=26,
            color=("#102030ff", "#205080ff", "#603080ff", "#805020ff")[index - 1],
        )
        for index, value in enumerate(lines, start=1)
    )
    return GoldenCase(
        "unicode-shaping",
        "Noto Sans Latin, Greek, Cyrillic, and Devanagari shaping without substitution.",
        _request(records),
        {},
        "\r\n".join(lines),
    )


def _signature_pixels() -> bytes:
    pixels = bytearray()
    for y in range(12):
        for x in range(24):
            on_stroke = abs(y - (x // 3) % 8) <= 1 or (x > 8 and abs(y - 8 + (x - 8) // 4) <= 1)
            if on_stroke:
                pixels.extend((20, 60, 150, 230))
            else:
                pixels.extend((255, 255, 255, 0))
    return bytes(pixels)


def _signature_case() -> GoldenCase:
    pixels = _signature_pixels()
    raster = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=24,
        height_pixels=12,
        row_stride_bytes=96,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    asset = OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        256,
        "b" * 64,
    )
    records = (
        _text(
            1,
            "Visual signature image (not a digital signature)",
            NormalizedPageBox(70_000, 100_000, 860_000, 80_000),
            size=14,
            color="#303030ff",
        ),
        _record(
            2,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#00000000"),
                RgbaColor("#6080b0ff"),
            ),
            NormalizedPageBox(120_000, 300_000, 760_000, 280_000),
        ),
        _record(
            3,
            SignatureImageOverlayPayload(ASSET_ID),
            NormalizedPageBox(150_000, 330_000, 700_000, 220_000),
        ),
    )
    return GoldenCase(
        "signature-image",
        "Authenticated metadata-free RGBA image aspect-fit and vector frame placement.",
        _request(records, assets=(asset,)),
        {ASSET_ID: _RasterSource(raster, pixels)},
        "Visual signature image (not a digital signature)",
    )


def build_cases() -> tuple[GoldenCase, ...]:
    """Return the closed public-synthetic overlay golden recipes."""

    return (_primitives_case(), _unicode_case(), _signature_case())


def _overlay_limits() -> OverlayRenderLimits:
    return OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _pdfium_limits() -> PdfPageRenderLimits:
    return PdfPageRenderLimits(
        maximum_pages=1,
        maximum_width_pixels=CANVAS_WIDTH,
        maximum_height_pixels=CANVAS_HEIGHT,
        maximum_pixels=CANVAS_WIDTH * CANVAS_HEIGHT,
        maximum_bitmap_bytes=2 * 1024 * 1024,
        maximum_destination_bytes=2 * 1024 * 1024,
        maximum_memory_bytes=8 * 1024 * 1024,
        wall_time_ms=10_000,
    )


def render_case(case: GoldenCase) -> GoldenArtifact:
    """Render one logical overlay PDF and its opaque PDFium visual oracle."""

    pdf_destination = BytesIO()
    FpdfOverlayRenderer().render(
        case.request,
        case.sources,
        pdf_destination,
        _overlay_limits(),
        _Control(),
    )
    pdf_bytes = pdf_destination.getvalue()
    rgba_destination = BytesIO()
    result = PdfiumPageRenderer(PdfPageGeometryLimits(1_000_000, 1_000_000)).render(
        BytesIO(pdf_bytes),
        rgba_destination,
        PdfPageRenderRequest(0, CANVAS_WIDTH, CANVAS_HEIGHT),
        _pdfium_limits(),
        _Control(),
    )
    rgba = rgba_destination.getvalue()
    image = Image.frombytes(
        "RGBA",
        (result.width_pixels, result.height_pixels),
        rgba,
        "raw",
        "RGBA",
        result.row_stride_bytes,
        1,
    ).convert("RGB")
    png_destination = BytesIO()
    image.save(png_destination, format="PNG", compress_level=9, optimize=False)
    return GoldenArtifact(
        case.case_id,
        case.purpose,
        pdf_bytes,
        png_destination.getvalue(),
        rgba,
        result.width_pixels,
        result.height_pixels,
        case.expected_text,
    )


def build_artifacts() -> tuple[GoldenArtifact, ...]:
    artifacts = tuple(render_case(case) for case in build_cases())
    if tuple(artifact.case_id for artifact in artifacts) != CASE_IDS:
        raise AssertionError("overlay golden case inventory changed")
    return artifacts


def _index(
    artifacts: tuple[GoldenArtifact, ...],
    *,
    approval: GoldenApproval | None,
) -> dict[str, object]:
    approval_record: dict[str, object]
    if approval is None:
        approval_record = {"status": "candidate-requires-human-review"}
    else:
        approval_record = {
            "status": "approved",
            "reviewed_at": approval.reviewed_at,
            "reviewer_role": approval.reviewer_role,
            "rationale": approval.rationale,
        }
    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "golden_set_id": GOLDEN_SET_ID,
        "approval": approval_record,
        "policy": {
            "automatic_refresh": False,
            "classification": "public-synthetic-only",
            "comparison": "exact-rgba8",
            "task_ref": "PWS-0303",
            "decision_refs": ["PWS-D015", "PWS-D018", "PWS-D034"],
        },
        "engine_profile": {
            "fpdf2": EXPECTED_FPDF2_VERSION,
            "fonttools": EXPECTED_FONTTOOLS_VERSION,
            "uharfbuzz": EXPECTED_UHARFBUZZ_VERSION,
            "pillow": EXPECTED_PILLOW_VERSION,
            "pikepdf": EXPECTED_PIKEPDF_VERSION,
            "qpdf": EXPECTED_QPDF_VERSION,
            "ocrmypdf": EXPECTED_OCRMYPDF_VERSION,
            "zlib_compile": EXPECTED_ZLIB_VERSION,
            "zlib_runtime": EXPECTED_ZLIB_RUNTIME_VERSION,
            "zlib_ng": EXPECTED_ZLIBNG_VERSION,
            "font_version": OVERLAY_FONT_VERSION,
            "font_sha256": OVERLAY_FONT_RESOURCE_SHA256,
            "pypdfium2": EXPECTED_PYPDFIUM2_VERSION,
            "pdfium": EXPECTED_PDFIUM_VERSION,
        },
        "references": [
            {
                "id": artifact.case_id,
                "purpose": artifact.purpose,
                "path": f"{artifact.case_id}.png",
                "width_pixels": artifact.width_pixels,
                "height_pixels": artifact.height_pixels,
                "pdf_sha256": hashlib.sha256(artifact.pdf_bytes).hexdigest(),
                "png_sha256": hashlib.sha256(artifact.png_bytes).hexdigest(),
                "rgba_sha256": hashlib.sha256(artifact.rgba_bytes).hexdigest(),
                "expected_text": artifact.expected_text,
            }
            for artifact in artifacts
        ],
    }


def approved_index(artifacts: tuple[GoldenArtifact, ...]) -> dict[str, object]:
    return _index(
        artifacts,
        approval=GoldenApproval(APPROVED_AT, APPROVAL_REVIEWER_ROLE, APPROVAL_RATIONALE),
    )


def candidate_index(artifacts: tuple[GoldenArtifact, ...]) -> dict[str, object]:
    return _index(artifacts, approval=None)


def canonical_index_bytes(artifacts: tuple[GoldenArtifact, ...]) -> bytes:
    return (
        json.dumps(approved_index(artifacts), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def candidate_index_bytes(artifacts: tuple[GoldenArtifact, ...]) -> bytes:
    return (
        json.dumps(candidate_index(artifacts), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def validate_committed_goldens() -> None:
    artifacts = build_artifacts()
    expected = {"index.json": canonical_index_bytes(artifacts)}
    expected.update({f"{item.case_id}.png": item.png_bytes for item in artifacts})
    actual_names = {path.name for path in GOLDEN_ROOT.iterdir()} if GOLDEN_ROOT.is_dir() else set()
    if actual_names != set(expected):
        raise AssertionError("committed overlay golden inventory differs")
    for name, content in expected.items():
        if (GOLDEN_ROOT / name).read_bytes() != content:
            raise AssertionError(f"committed overlay golden differs: {name}")


def _write_candidate(output: Path) -> None:
    resolved = output.resolve(strict=False)
    if output.exists() or resolved.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("candidate must be a new directory outside the repository")
    artifacts = build_artifacts()
    output.mkdir(parents=False)
    for artifact in artifacts:
        (output / f"{artifact.case_id}.pdf").write_bytes(artifact.pdf_bytes)
        (output / f"{artifact.case_id}.png").write_bytes(artifact.png_bytes)
    (output / "index.json").write_bytes(candidate_index_bytes(artifacts))


def _has_reparse_attribute(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _require_safe_candidate_directory(candidate: Path) -> Path:
    try:
        metadata = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise ValueError("candidate must be an outside-repository directory") from None
    if (
        candidate.is_symlink()
        or _has_reparse_attribute(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or resolved.is_relative_to(PROJECT_ROOT.resolve())
    ):
        raise ValueError("candidate must be an outside-repository directory")
    return resolved


def _require_exact_candidate_file(path: Path, expected: bytes) -> None:
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or _has_reparse_attribute(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != len(expected)
        ):
            raise OSError
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size != len(expected)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise OSError
            offset = 0
            while offset < len(expected):
                end = min(offset + _CANDIDATE_READ_CHUNK_BYTES, len(expected))
                block = stream.read(end - offset)
                if block != expected[offset:end]:
                    raise OSError
                offset = end
            if stream.read(1):
                raise OSError
        after = path.lstat()
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or after.st_nlink != 1
            or _has_reparse_attribute(after)
        ):
            raise OSError
    except OSError:
        raise ValueError("candidate file is unavailable, changed, or unsafe") from None


def _require_exact_candidate_inventory(directory: Path, expected_names: set[str]) -> None:
    """Reject an unexpected entry after consuming at most the closed inventory plus one."""

    actual_names: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if name not in expected_names or name in actual_names:
                    raise ValueError("candidate inventory differs")
                actual_names.add(name)
    except OSError:
        raise ValueError("candidate inventory differs") from None
    if actual_names != expected_names:
        raise ValueError("candidate inventory differs")


def _approve_candidate(candidate: Path, approval: GoldenApproval) -> None:
    if type(approval) is not GoldenApproval:
        raise ValueError("approval record is invalid")
    resolved_candidate = _require_safe_candidate_directory(candidate)
    artifacts = build_artifacts()
    expected_names = {
        "index.json",
        *(f"{artifact.case_id}.pdf" for artifact in artifacts),
        *(f"{artifact.case_id}.png" for artifact in artifacts),
    }
    _require_exact_candidate_inventory(resolved_candidate, expected_names)
    _require_exact_candidate_file(
        resolved_candidate / "index.json",
        candidate_index_bytes(artifacts),
    )
    for artifact in artifacts:
        _require_exact_candidate_file(
            resolved_candidate / f"{artifact.case_id}.pdf",
            artifact.pdf_bytes,
        )
        _require_exact_candidate_file(
            resolved_candidate / f"{artifact.case_id}.png",
            artifact.png_bytes,
        )
    if GOLDEN_ROOT.exists():
        raise ValueError("committed golden directory already exists")
    GOLDEN_ROOT.mkdir()
    for artifact in artifacts:
        (GOLDEN_ROOT / f"{artifact.case_id}.png").write_bytes(artifact.png_bytes)
    (GOLDEN_ROOT / "index.json").write_bytes(canonical_index_bytes(artifacts))


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--candidate", type=Path)
    actions.add_argument("--approve-candidate", type=Path)
    actions.add_argument("--validate", action="store_true")
    parser.add_argument("--reviewed-at")
    parser.add_argument("--reviewer-role")
    parser.add_argument("--rationale")
    arguments = parser.parse_args(argv)
    try:
        if arguments.candidate is not None:
            _write_candidate(arguments.candidate)
        elif arguments.approve_candidate is not None:
            _approve_candidate(
                arguments.approve_candidate,
                GoldenApproval(
                    arguments.reviewed_at,
                    arguments.reviewer_role,
                    arguments.rationale,
                ),
            )
        else:
            validate_committed_goldens()
    except (AssertionError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: overlay renderer goldens are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
