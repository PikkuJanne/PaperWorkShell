"""Closed staging and approval harness for PWS-0202 image-transform goldens.

This module deliberately does not import the production transform implementation.  A caller
supplies already-produced RGBA8 results, the harness binds them to deterministic public synthetic
sources and an exact ordered operation description, and writes a review set outside the
repository.  A separate explicit approval step creates the only inventory eligible for promotion
to ``tests/golden/image-transforms-v1``.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, NoReturn, cast

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
GOLDEN_DIRECTORY: Final = Path("tests/golden/image-transforms-v1")
GOLDEN_INDEX_PATH: Final = GOLDEN_DIRECTORY / "index.json"
GOLDEN_SCHEMA_VERSION: Final = 1
GOLDEN_SET_ID: Final = "paperworkshell-image-transforms-v1"
SOURCE_SET_ID: Final = "paperworkshell-image-transform-sources-v1"
TRANSFORM_PROFILE_VERSION: Final = 1
TRANSFORM_ENGINE_PROFILE_SHA256: Final = (
    "94c3b1995473060298cb1b8bb59bc4b778dce6dc098cb6b5c061044f96de32a7"
)
TRANSFORM_NORMALIZATION_PROFILE_SHA256: Final = (
    "b2444f98d966dc0ed683adb55830ad3349ff4726b693300748d94a6536aefafc"
)
TRANSFORM_INTENT_VERSION: Final = 1
TRANSFORM_CACHE_KEY_VERSION: Final = 1
TRANSFORM_ARTIFACT_VERSION: Final = 1
TRANSFORM_PIXEL_DEPENDENCY: Final = "Pillow"
TRANSFORM_PIXEL_DEPENDENCY_VERSION: Final = "12.3.0"
SYNTHETIC_MARKER: Final = "PWS-SYNTHETIC-FIXTURE"
PIXEL_LAYOUT: Final = "rgba8-top-down"
PIPELINE_ORDER: Final = (
    "exif",
    "crop",
    "quarter_turn",
    "deskew",
    "contrast",
    "background_cleanup",
    "fit",
    "serialize",
)
MAXIMUM_IMAGE_DIMENSION: Final = 4096
MAXIMUM_PIXEL_BYTES: Final = 64 * 1024 * 1024
MAXIMUM_INDEX_BYTES: Final = 256 * 1024
_INDEX_FILE_NAME: Final = "index.json"
_REVIEW_FILE_NAME: Final = "review.html"
_APPROVAL_RATIONALE: Final = "initial-pws-0202-baseline"
_REVIEWER_ROLES: Final = frozenset({"implementation-agent", "maintainer"})
_DATE_RE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SAFE_TOKEN_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_PARAMETER_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class ImageTransformGoldenError(Exception):
    """Sanitized, stable harness failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _reject(code: str, message: str) -> NoReturn:
    raise ImageTransformGoldenError(code, message) from None


@dataclass(frozen=True, slots=True)
class RgbaImage:
    """One bounded top-down RGBA8 raster."""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or not 1 <= self.width <= MAXIMUM_IMAGE_DIMENSION
            or not 1 <= self.height <= MAXIMUM_IMAGE_DIMENSION
            or type(self.pixels) is not bytes
            or self.width > MAXIMUM_PIXEL_BYTES // 4 // self.height
            or len(self.pixels) != self.width * self.height * 4
        ):
            _reject("GOLDEN_IMAGE_INVALID", "golden image geometry or payload is invalid")

    @property
    def rgba_sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    @property
    def is_opaque(self) -> bool:
        return all(alpha == 255 for alpha in self.pixels[3::4])


@dataclass(frozen=True, slots=True)
class GoldenCasePlan:
    """One fixed public source recipe and expected transform output geometry."""

    case_id: str
    output_name: PurePosixPath
    source_recipe: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    purpose: str

    def __post_init__(self) -> None:
        if (
            _SAFE_TOKEN_RE.fullmatch(self.case_id) is None
            or self.output_name != PurePosixPath(f"{self.case_id}.png")
            or _SAFE_TOKEN_RE.fullmatch(self.source_recipe) is None
            or type(self.source_width) is not int
            or type(self.source_height) is not int
            or type(self.output_width) is not int
            or type(self.output_height) is not int
            or min(
                self.source_width,
                self.source_height,
                self.output_width,
                self.output_height,
            )
            <= 0
            or max(
                self.source_width,
                self.source_height,
                self.output_width,
                self.output_height,
            )
            > MAXIMUM_IMAGE_DIMENSION
            or type(self.purpose) is not str
            or not self.purpose
            or len(self.purpose) > 160
        ):
            _reject("GOLDEN_PLAN_INVALID", "golden case plan is invalid")


@dataclass(frozen=True, slots=True)
class StageBinding:
    """Canonical public parameters for one ordered transform stage."""

    stage: str
    parameters: Mapping[str, JsonScalar]

    def __post_init__(self) -> None:
        if self.stage not in PIPELINE_ORDER or type(self.parameters) is not dict:
            _reject("GOLDEN_BINDING_INVALID", "golden pipeline binding is invalid")
        copied: dict[str, JsonScalar] = {}
        for key, value in self.parameters.items():
            if (
                type(key) is not str
                or _SAFE_PARAMETER_RE.fullmatch(key) is None
                or type(value) not in {str, int, bool, type(None)}
                or (type(value) is str and len(value) > 128)
            ):
                _reject("GOLDEN_BINDING_INVALID", "golden stage parameter is invalid")
            copied[key] = value
        object.__setattr__(self, "parameters", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class PipelineBinding:
    """Exact eight-token machine binding for one golden case."""

    stages: tuple[StageBinding, ...]

    def __post_init__(self) -> None:
        if (
            type(self.stages) is not tuple
            or tuple(item.stage for item in self.stages) != PIPELINE_ORDER
        ):
            _reject(
                "GOLDEN_PIPELINE_ORDER_INVALID",
                "golden stages do not match the approved pipeline order",
            )

    def to_json(self) -> list[dict[str, JsonValue]]:
        return [
            {
                "stage": item.stage,
                "parameters": dict(sorted(item.parameters.items())),
            }
            for item in self.stages
        ]

    @property
    def sha256(self) -> str:
        serialized = cast(list[JsonValue], self.to_json())
        return hashlib.sha256(_canonical_json_bytes(serialized, trailing_newline=False)).hexdigest()


@dataclass(frozen=True, slots=True)
class GoldenApproval:
    """Closed visual-review record required before a candidate can be promoted."""

    reviewed_at: str
    reviewer_role: str = "implementation-agent"
    rationale: str = _APPROVAL_RATIONALE

    def __post_init__(self) -> None:
        if (
            type(self.reviewed_at) is not str
            or _DATE_RE.fullmatch(self.reviewed_at) is None
            or self.reviewer_role not in _REVIEWER_ROLES
            or self.rationale != _APPROVAL_RATIONALE
        ):
            _reject("GOLDEN_APPROVAL_INVALID", "golden approval record is invalid")
        try:
            parsed = date.fromisoformat(self.reviewed_at)
        except ValueError:
            _reject("GOLDEN_APPROVAL_INVALID", "golden approval date is invalid")
        if parsed.isoformat() != self.reviewed_at:
            _reject("GOLDEN_APPROVAL_INVALID", "golden approval date is invalid")


CASE_PLANS: Final = (
    GoldenCasePlan(
        "rotate-90",
        PurePosixPath("rotate-90.png"),
        "asymmetric-corners-13x9-v1",
        13,
        9,
        9,
        13,
        "Quarter-turn orientation and asymmetric corner preservation.",
    ),
    GoldenCasePlan(
        "crop",
        PurePosixPath("crop.png"),
        "crop-grid-16x12-v1",
        16,
        12,
        8,
        6,
        "Pixel-aligned normalized crop with no off-by-one border retention.",
    ),
    GoldenCasePlan(
        "deskew",
        PurePosixPath("deskew.png"),
        "deskew-grid-48x32-v1",
        48,
        32,
        48,
        32,
        "Fine-angle deskew, preserved canvas, white fill, and line continuity.",
    ),
    GoldenCasePlan(
        "contrast",
        PurePosixPath("contrast.png"),
        "contrast-ramp-32x8-v1",
        32,
        8,
        32,
        8,
        "Deterministic contrast response without geometry change.",
    ),
    GoldenCasePlan(
        "background-cleanup",
        PurePosixPath("background-cleanup.png"),
        "paper-background-32x16-v1",
        32,
        16,
        32,
        16,
        "Background cleanup preserves dark strokes while whitening the synthetic paper field.",
    ),
    GoldenCasePlan(
        "transparency",
        PurePosixPath("transparency.png"),
        "alpha-canary-12x10-v1",
        12,
        10,
        12,
        10,
        "Transparent source normalization remains opaque white through a no-op transform chain.",
    ),
)
_PLANS_BY_ID: Final = {plan.case_id: plan for plan in CASE_PLANS}


def _canonical_json_bytes(value: JsonValue, *, trailing_newline: bool = True) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return rendered + (b"\n" if trailing_newline else b"")


def _stored_zlib(content: bytes) -> bytes:
    output = bytearray(b"\x78\x01")
    if not content:
        output.extend(b"\x01\x00\x00\xff\xff")
    else:
        cursor = 0
        while cursor < len(content):
            block = content[cursor : cursor + 65_535]
            cursor += len(block)
            output.append(1 if cursor == len(content) else 0)
            output.extend(struct.pack("<H", len(block)))
            output.extend(struct.pack("<H", 0xFFFF ^ len(block)))
            output.extend(block)
    output.extend(struct.pack(">I", zlib.adler32(content) & 0xFFFFFFFF))
    return bytes(output)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def canonical_rgba_png(image: RgbaImage, *, description: str) -> bytes:
    """Encode one byte-deterministic no-filter RGBA8 PNG."""

    if (
        type(description) is not str
        or not description.startswith(f"{SYNTHETIC_MARKER}: ")
        or len(description) > 160
        or not description.isascii()
    ):
        _reject("GOLDEN_PNG_DESCRIPTION_INVALID", "golden PNG description is invalid")
    stride = image.width * 4
    raw = b"".join(
        b"\x00" + image.pixels[offset : offset + stride]
        for offset in range(0, len(image.pixels), stride)
    )
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0),
            ),
            _png_chunk(b"tEXt", b"Comment\x00" + description.encode("ascii")),
            _png_chunk(b"IDAT", _stored_zlib(raw)),
            _png_chunk(b"IEND", b""),
        )
    )


def decode_canonical_rgba_png(content: bytes) -> RgbaImage:
    """Decode only the harness's strict bounded RGBA8/no-filter PNG profile."""

    if type(content) is not bytes or not content.startswith(b"\x89PNG\r\n\x1a\n"):
        _reject("GOLDEN_PNG_INVALID", "golden PNG framing is invalid")
    cursor = 8
    chunks: list[tuple[bytes, bytes]] = []
    while cursor < len(content):
        if len(content) - cursor < 12:
            _reject("GOLDEN_PNG_INVALID", "golden PNG chunk is truncated")
        length = struct.unpack_from(">I", content, cursor)[0]
        cursor += 4
        if length > MAXIMUM_PIXEL_BYTES or length > len(content) - cursor - 8:
            _reject("GOLDEN_PNG_INVALID", "golden PNG chunk length is invalid")
        kind = content[cursor : cursor + 4]
        payload = content[cursor + 4 : cursor + 4 + length]
        checksum = struct.unpack_from(">I", content, cursor + 4 + length)[0]
        if checksum != zlib.crc32(kind + payload) & 0xFFFFFFFF:
            _reject("GOLDEN_PNG_INVALID", "golden PNG checksum is invalid")
        chunks.append((kind, payload))
        cursor += 8 + length
        if kind == b"IEND":
            break
    if cursor != len(content) or [kind for kind, _payload in chunks] != [
        b"IHDR",
        b"tEXt",
        b"IDAT",
        b"IEND",
    ]:
        _reject("GOLDEN_PNG_INVALID", "golden PNG inventory is invalid")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        _reject("GOLDEN_PNG_INVALID", "golden PNG header is invalid")
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (bit_depth, color_type, compression, filter_method, interlace) != (8, 6, 0, 0, 0):
        _reject("GOLDEN_PNG_INVALID", "golden PNG pixel profile is invalid")
    expected_raw = height * (1 + width * 4)
    if (
        not 1 <= width <= MAXIMUM_IMAGE_DIMENSION
        or not 1 <= height <= MAXIMUM_IMAGE_DIMENSION
        or expected_raw > MAXIMUM_PIXEL_BYTES + height
    ):
        _reject("GOLDEN_PNG_INVALID", "golden PNG dimensions are invalid")
    description = chunks[1][1]
    if not description.startswith(b"Comment\x00PWS-SYNTHETIC-FIXTURE: "):
        _reject("GOLDEN_PNG_INVALID", "golden PNG provenance marker is missing")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(chunks[2][1], expected_raw + 1)
        if (
            len(raw) > expected_raw
            or decoder.unconsumed_tail
            or decoder.unused_data
            or not decoder.eof
            or decoder.flush()
        ):
            raise zlib.error("noncanonical raster stream")
    except TypeError, ValueError, zlib.error:
        _reject("GOLDEN_PNG_INVALID", "golden PNG raster is invalid")
    if len(raw) != expected_raw:
        _reject("GOLDEN_PNG_INVALID", "golden PNG raster length is invalid")
    stride = width * 4
    pixels = bytearray()
    for offset in range(0, len(raw), stride + 1):
        if raw[offset] != 0:
            _reject("GOLDEN_PNG_INVALID", "golden PNG uses an unsupported row filter")
        pixels.extend(raw[offset + 1 : offset + 1 + stride])
    return RgbaImage(width, height, bytes(pixels))


def _pixel_source(plan: GoldenCasePlan) -> RgbaImage:
    pixels = bytearray()
    for y in range(plan.source_height):
        for x in range(plan.source_width):
            if plan.case_id == "rotate-90":
                red = 20 + (x * 17) % 220
                green = 18 + (y * 23) % 220
                blue = 40 + ((x + y) * 11) % 190
                alpha = 255
                corner_colors = {
                    (0, 0): (255, 0, 0, 255),
                    (plan.source_width - 1, 0): (0, 255, 0, 255),
                    (0, plan.source_height - 1): (0, 0, 255, 255),
                    (plan.source_width - 1, plan.source_height - 1): (255, 255, 0, 255),
                }
                red, green, blue, alpha = corner_colors.get((x, y), (red, green, blue, alpha))
            elif plan.case_id == "crop":
                red = 24 + x * 11
                green = 32 + y * 15
                blue = 220 if x in {3, 4, 11, 12} or y in {2, 3, 8, 9} else 72
                alpha = 255
            elif plan.case_id == "deskew":
                line = x % 8 == 0 or y % 8 == 0 or y == (x // 3) + 4
                red = green = blue = 24 if line else 244
                alpha = 255
            elif plan.case_id == "contrast":
                value = round(255 * x / (plan.source_width - 1))
                red = green = blue = value
                alpha = 255
            elif plan.case_id == "background-cleanup":
                paper = 224 + ((x * 3 + y * 5) % 24)
                stroke = x in {7, 8, 20} or y == 11
                red = green = blue = 32 if stroke else paper
                alpha = 255
            elif plan.case_id == "transparency":
                red = 24 + x * 15
                green = 36 + y * 17
                blue = 180
                alpha = ((x + 2 * y) * 19) % 256
            else:  # pragma: no cover - CASE_PLANS is closed above.
                raise AssertionError("unregistered golden source recipe")
            pixels.extend((red, green, blue, alpha))
    return RgbaImage(plan.source_width, plan.source_height, bytes(pixels))


def build_source_images() -> dict[str, RgbaImage]:
    """Return fresh deterministic public source rasters in case order."""

    return {plan.case_id: _pixel_source(plan) for plan in CASE_PLANS}


def _expected_pipeline_binding(plan: GoldenCasePlan) -> PipelineBinding:
    is_crop = plan.case_id == "crop"
    parameters: dict[str, dict[str, JsonScalar]] = {
        "exif": {"orientation": 1},
        "crop": {
            "x_millionths": 250_000 if is_crop else 0,
            "y_millionths": 250_000 if is_crop else 0,
            "width_millionths": 500_000 if is_crop else 1_000_000,
            "height_millionths": 500_000 if is_crop else 1_000_000,
        },
        "quarter_turn": {"degrees_clockwise": 90 if plan.case_id == "rotate-90" else 0},
        "deskew": {
            "angle_millidegrees_clockwise": 3_000 if plan.case_id == "deskew" else 0,
            "canvas": "fixed_opaque_white",
            "resampling": "bicubic",
        },
        "contrast": {
            "factor_millionths": 1_250_000 if plan.case_id == "contrast" else 1_000_000,
            "midpoint": 128,
            "rounding": "integer_half_away",
        },
        "background_cleanup": {
            "rounding": "integer_half_up",
            "strength_millionths": 1_000_000 if plan.case_id == "background-cleanup" else 0,
            "threshold": 192,
        },
        "fit": {
            "maximum_height_pixels": plan.output_height,
            "maximum_width_pixels": plan.output_width,
            "no_upscale": True,
            "reducing_gap_millionths": 3_000_000,
            "resampling": "lanczos",
        },
        "serialize": {
            "background": "opaque_white",
            "pixel_layout": "rgba8_top_down",
        },
    }
    return PipelineBinding(
        tuple(StageBinding(stage, parameters[stage]) for stage in PIPELINE_ORDER)
    )


def _require_exact_keys(value: Mapping[str, object], expected: set[str], *, code: str) -> None:
    if type(value) is not dict or set(value) != expected:
        _reject(code, "golden index object shape is invalid")


def _validated_output_map(outputs: Mapping[str, RgbaImage]) -> dict[str, RgbaImage]:
    if type(outputs) is not dict or set(outputs) != set(_PLANS_BY_ID):
        _reject("GOLDEN_OUTPUT_INVENTORY_INVALID", "golden output inventory is invalid")
    copied: dict[str, RgbaImage] = {}
    for case_id, image in outputs.items():
        plan = _PLANS_BY_ID[case_id]
        if (
            type(image) is not RgbaImage
            or image.width != plan.output_width
            or image.height != plan.output_height
            or not image.is_opaque
        ):
            _reject("GOLDEN_OUTPUT_INVALID", "golden output does not match its closed profile")
        copied[case_id] = image
    return copied


def _validated_binding_map(
    bindings: Mapping[str, PipelineBinding],
) -> dict[str, PipelineBinding]:
    if type(bindings) is not dict or set(bindings) != set(_PLANS_BY_ID):
        _reject("GOLDEN_BINDING_INVENTORY_INVALID", "golden binding inventory is invalid")
    copied: dict[str, PipelineBinding] = {}
    for case_id, binding in bindings.items():
        if (
            type(binding) is not PipelineBinding
            or binding.to_json() != _expected_pipeline_binding(_PLANS_BY_ID[case_id]).to_json()
        ):
            _reject("GOLDEN_BINDING_INVALID", "golden pipeline binding is invalid")
        copied[case_id] = binding
    return copied


def _record(
    plan: GoldenCasePlan,
    source: RgbaImage,
    output: RgbaImage,
    binding: PipelineBinding,
) -> dict[str, JsonValue]:
    source_png = canonical_rgba_png(
        source,
        description=f"{SYNTHETIC_MARKER}: {plan.source_recipe}",
    )
    output_png = canonical_rgba_png(
        output,
        description=f"{SYNTHETIC_MARKER}: transform {plan.case_id} output",
    )
    return {
        "id": plan.case_id,
        "path": plan.output_name.as_posix(),
        "purpose": plan.purpose,
        "source": {
            "set_id": SOURCE_SET_ID,
            "recipe": plan.source_recipe,
            "width": source.width,
            "height": source.height,
            "pixel_layout": PIXEL_LAYOUT,
            "rgba_sha256": source.rgba_sha256,
            "png_sha256": hashlib.sha256(source_png).hexdigest(),
        },
        "pipeline": cast(list[JsonValue], binding.to_json()),
        "pipeline_sha256": binding.sha256,
        "output": {
            "width": output.width,
            "height": output.height,
            "pixel_layout": PIXEL_LAYOUT,
            "background": "opaque-white",
            "rgba_sha256": output.rgba_sha256,
            "png_bytes": len(output_png),
            "png_sha256": hashlib.sha256(output_png).hexdigest(),
        },
        "comparison": {"mode": "exact", "maximum_channel_delta": 0},
    }


def _index(
    outputs: Mapping[str, RgbaImage],
    bindings: Mapping[str, PipelineBinding],
    *,
    approval: GoldenApproval | None,
) -> dict[str, JsonValue]:
    sources = build_source_images()
    records: list[JsonValue] = [
        _record(plan, sources[plan.case_id], outputs[plan.case_id], bindings[plan.case_id])
        for plan in CASE_PLANS
    ]
    approval_record: dict[str, JsonValue]
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
        "source_set_id": SOURCE_SET_ID,
        "pipeline_order": list(PIPELINE_ORDER),
        "production_profile": {
            "profile_version": TRANSFORM_PROFILE_VERSION,
            "engine_profile_sha256": TRANSFORM_ENGINE_PROFILE_SHA256,
            "normalization_profile_sha256": TRANSFORM_NORMALIZATION_PROFILE_SHA256,
            "intent_version": TRANSFORM_INTENT_VERSION,
            "cache_key_version": TRANSFORM_CACHE_KEY_VERSION,
            "artifact_version": TRANSFORM_ARTIFACT_VERSION,
            "pixel_dependency": TRANSFORM_PIXEL_DEPENDENCY,
            "pixel_dependency_version": TRANSFORM_PIXEL_DEPENDENCY_VERSION,
        },
        "policy": {
            "classification": "public-synthetic-only",
            "comparison": "exact-rgba8",
            "captured_from_production_profile": True,
            "automatic_refresh": False,
            "decision_ref": "PWS-D035",
            "task_ref": "PWS-0202",
        },
        "approval": approval_record,
        "references": records,
    }


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _external_parent(destination: Path, repository_root: Path) -> Path:
    try:
        repository = repository_root.resolve(strict=True)
        parent = destination.parent.resolve(strict=True)
        resolved_destination = destination.resolve(strict=False)
    except OSError:
        _reject("GOLDEN_OUTPUT_UNSAFE", "golden output location is unavailable")
    if (
        _is_link_or_reparse(parent)
        or resolved_destination == repository
        or resolved_destination.is_relative_to(repository)
    ):
        _reject("GOLDEN_OUTPUT_UNSAFE", "golden review output must remain outside the repository")
    if os.path.lexists(destination):
        _reject("GOLDEN_OUTPUT_EXISTS", "golden review output already exists")
    return parent


def _write_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        if stream.write(content) != len(content):
            raise OSError("short golden review write")


def _atomic_external_tree(
    destination: Path,
    files: Mapping[PurePosixPath, bytes],
    *,
    repository_root: Path,
) -> None:
    parent = _external_parent(destination, repository_root)
    temporary = Path(tempfile.mkdtemp(prefix=".pws-image-golden-", dir=parent))
    succeeded = False
    try:
        if _is_link_or_reparse(temporary) or temporary.resolve(strict=True).parent != parent:
            raise OSError("unsafe golden review staging directory")
        for relative, content in files.items():
            if relative.is_absolute() or ".." in relative.parts or type(content) is not bytes:
                raise OSError("unsafe golden review inventory")
            target = temporary.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if _is_link_or_reparse(target.parent):
                raise OSError("unsafe golden review directory")
            _write_file(target, content)
        os.replace(temporary, destination)
        succeeded = True
    except OSError:
        _reject("GOLDEN_OUTPUT_UNWRITABLE", "golden review output could not be written safely")
    finally:
        if not succeeded:
            shutil.rmtree(temporary, ignore_errors=True)


def _review_html() -> bytes:
    rows = []
    for plan in CASE_PLANS:
        case_id = html.escape(plan.case_id)
        purpose = html.escape(plan.purpose)
        source_width = plan.source_width * 8
        source_height = plan.source_height * 8
        output_width = plan.output_width * 8
        output_height = plan.output_height * 8
        rows.append(
            "<figure><figcaption>"
            f"{case_id} — {purpose}"
            "</figcaption><div class=pair>"
            f"<section><h2>Source</h2><h3>Native</h3><img src=source/{case_id}.png "
            f"alt='Synthetic source for {case_id} at native scale'><h3>8× nearest-neighbour</h3>"
            f"<img src=source/{case_id}.png width={source_width} height={source_height} "
            f"alt='Synthetic source for {case_id} enlarged eight times'></section>"
            f"<section><h2>Candidate</h2><h3>Native</h3><img src=candidate/{case_id}.png "
            f"alt='Candidate output for {case_id} at native scale'><h3>8× nearest-neighbour</h3>"
            f"<img src=candidate/{case_id}.png width={output_width} height={output_height} "
            f"alt='Candidate output for {case_id} enlarged eight times'></section>"
            "</div></figure>"
        )
    document = (
        """<!doctype html>
<html lang=en><meta charset=utf-8><title>PWS-0202 transform golden review</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem;background:#eee;color:#111}
figure{background:white;border:2px solid #222;padding:1rem;margin:0 0 2rem}
figcaption{font-weight:700}.pair{display:flex;gap:2rem;flex-wrap:wrap}
section{min-width:16rem}img{image-rendering:pixelated;max-width:36rem;border:1px solid #555;background:white}
</style><body><h1>PWS-0202 before/after candidate review</h1>
<p>Inspect every source and candidate at native scale and enlarged nearest-neighbour scale. Approval is a separate explicit step.</p>
"""
        + "\n".join(rows)
        + "\n</body></html>\n"
    )
    return document.encode("utf-8")


def stage_review_candidate_set(
    destination: Path,
    outputs: Mapping[str, RgbaImage],
    bindings: Mapping[str, PipelineBinding],
    *,
    repository_root: Path = PROJECT_ROOT,
) -> None:
    """Write a non-promotable, labeled before/after review set outside the repository."""

    selected_outputs = _validated_output_map(outputs)
    selected_bindings = _validated_binding_map(bindings)
    sources = build_source_images()
    files: dict[PurePosixPath, bytes] = {
        PurePosixPath(_INDEX_FILE_NAME): _canonical_json_bytes(
            _index(selected_outputs, selected_bindings, approval=None)
        ),
        PurePosixPath(_REVIEW_FILE_NAME): _review_html(),
    }
    for plan in CASE_PLANS:
        files[PurePosixPath("source") / plan.output_name] = canonical_rgba_png(
            sources[plan.case_id],
            description=f"{SYNTHETIC_MARKER}: {plan.source_recipe}",
        )
        files[PurePosixPath("candidate") / plan.output_name] = canonical_rgba_png(
            selected_outputs[plan.case_id],
            description=f"{SYNTHETIC_MARKER}: transform {plan.case_id} output",
        )
    _atomic_external_tree(destination, files, repository_root=repository_root)


def _load_index(path: Path) -> dict[str, object]:
    content = _regular_file(path, maximum_bytes=MAXIMUM_INDEX_BYTES)
    if not content or len(content) > MAXIMUM_INDEX_BYTES:
        _reject("GOLDEN_INDEX_INVALID", "golden index size is invalid")

    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _reject("GOLDEN_INDEX_INVALID", "golden index contains a duplicate key")
            result[key] = value
        return result

    try:
        decoded: object = json.loads(content, object_pairs_hook=closed_object)
    except UnicodeDecodeError, json.JSONDecodeError:
        _reject("GOLDEN_INDEX_INVALID", "golden index JSON is invalid")
    if type(decoded) is not dict:
        _reject("GOLDEN_INDEX_INVALID", "golden index root is invalid")
    try:
        canonical = _canonical_json_bytes(decoded)
    except TypeError, ValueError:
        _reject("GOLDEN_INDEX_INVALID", "golden index values are invalid")
    if content != canonical:
        _reject("GOLDEN_INDEX_INVALID", "golden index is not canonical")
    return cast(dict[str, object], decoded)


def _regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= maximum_bytes
        ):
            raise OSError("unsafe golden file")
        content = path.read_bytes()
        final = path.lstat()
    except OSError:
        _reject("GOLDEN_FILE_UNSAFE", "golden file is unavailable or unsafe")
    if len(content) != metadata.st_size or (metadata.st_dev, metadata.st_ino) != (
        final.st_dev,
        final.st_ino,
    ):
        _reject("GOLDEN_FILE_UNSAFE", "golden file identity changed during validation")
    return content


def _parse_references(index: Mapping[str, object]) -> list[dict[str, object]]:
    _require_exact_keys(
        index,
        {
            "schema_version",
            "golden_set_id",
            "source_set_id",
            "pipeline_order",
            "production_profile",
            "policy",
            "approval",
            "references",
        },
        code="GOLDEN_INDEX_INVALID",
    )
    if (
        index["schema_version"] != GOLDEN_SCHEMA_VERSION
        or index["golden_set_id"] != GOLDEN_SET_ID
        or index["source_set_id"] != SOURCE_SET_ID
        or index["pipeline_order"] != list(PIPELINE_ORDER)
        or index["production_profile"]
        != {
            "profile_version": TRANSFORM_PROFILE_VERSION,
            "engine_profile_sha256": TRANSFORM_ENGINE_PROFILE_SHA256,
            "normalization_profile_sha256": TRANSFORM_NORMALIZATION_PROFILE_SHA256,
            "intent_version": TRANSFORM_INTENT_VERSION,
            "cache_key_version": TRANSFORM_CACHE_KEY_VERSION,
            "artifact_version": TRANSFORM_ARTIFACT_VERSION,
            "pixel_dependency": TRANSFORM_PIXEL_DEPENDENCY,
            "pixel_dependency_version": TRANSFORM_PIXEL_DEPENDENCY_VERSION,
        }
        or type(index["references"]) is not list
    ):
        _reject("GOLDEN_INDEX_INVALID", "golden index identity is invalid")
    references = index["references"]
    assert type(references) is list
    if len(references) != len(CASE_PLANS) or any(type(item) is not dict for item in references):
        _reject("GOLDEN_INDEX_INVALID", "golden references are invalid")
    return cast(list[dict[str, object]], list(references))


def _candidate_inventory(root: Path) -> set[PurePosixPath]:
    try:
        if _is_link_or_reparse(root) or not root.is_dir():
            raise OSError("unsafe candidate root")
        entries = list(root.rglob("*"))
        if len(entries) > 32:
            raise OSError("candidate inventory exceeds its bound")
        files: set[PurePosixPath] = set()
        for path in entries:
            metadata = path.lstat()
            if _is_link_or_reparse(path):
                raise OSError("candidate inventory contains a link")
            if stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise OSError("candidate inventory contains an aliased file")
                files.add(PurePosixPath(path.relative_to(root).as_posix()))
            elif not stat.S_ISDIR(metadata.st_mode):
                raise OSError("candidate inventory contains a special entry")
    except OSError:
        _reject("GOLDEN_CANDIDATE_UNSAFE", "golden candidate set is unavailable or unsafe")
    return files


def approve_review_candidate_set(
    candidate_root: Path,
    destination: Path,
    approval: GoldenApproval,
    *,
    repository_root: Path = PROJECT_ROOT,
) -> None:
    """Create a promotion-ready set outside the repository after explicit human approval."""

    if type(approval) is not GoldenApproval:
        _reject("GOLDEN_APPROVAL_INVALID", "golden approval record is invalid")
    try:
        repository = repository_root.resolve(strict=True)
        candidate = candidate_root.resolve(strict=True)
    except OSError:
        _reject("GOLDEN_CANDIDATE_UNSAFE", "golden candidate set is unavailable or unsafe")
    if candidate == repository or candidate.is_relative_to(repository):
        _reject(
            "GOLDEN_CANDIDATE_UNSAFE", "golden candidate set must remain outside the repository"
        )
    expected_inventory = {
        PurePosixPath(_INDEX_FILE_NAME),
        PurePosixPath(_REVIEW_FILE_NAME),
        *(PurePosixPath("source") / plan.output_name for plan in CASE_PLANS),
        *(PurePosixPath("candidate") / plan.output_name for plan in CASE_PLANS),
    }
    if _candidate_inventory(candidate_root) != expected_inventory:
        _reject("GOLDEN_CANDIDATE_INVENTORY_INVALID", "golden candidate inventory is invalid")
    index = _load_index(candidate_root / _INDEX_FILE_NAME)
    references = _parse_references(index)
    approval_record = index.get("approval")
    if approval_record != {"status": "candidate-requires-human-review"}:
        _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate approval state is invalid")
    outputs: dict[str, RgbaImage] = {}
    bindings: dict[str, PipelineBinding] = {}
    sources = build_source_images()
    for plan, reference in zip(CASE_PLANS, references, strict=True):
        if (
            reference.get("id") != plan.case_id
            or reference.get("path") != plan.output_name.as_posix()
        ):
            _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate reference is invalid")
        source_content = _regular_file(
            candidate_root / "source" / plan.output_name,
            maximum_bytes=MAXIMUM_PIXEL_BYTES,
        )
        candidate_content = _regular_file(
            candidate_root / "candidate" / plan.output_name,
            maximum_bytes=MAXIMUM_PIXEL_BYTES,
        )
        expected_source_content = canonical_rgba_png(
            sources[plan.case_id],
            description=f"{SYNTHETIC_MARKER}: {plan.source_recipe}",
        )
        if (
            source_content != expected_source_content
            or decode_canonical_rgba_png(source_content) != sources[plan.case_id]
        ):
            _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate source is invalid")
        output = decode_canonical_rgba_png(candidate_content)
        if candidate_content != canonical_rgba_png(
            output,
            description=f"{SYNTHETIC_MARKER}: transform {plan.case_id} output",
        ):
            _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate output is invalid")
        outputs[plan.case_id] = output
        raw_pipeline = reference.get("pipeline")
        if type(raw_pipeline) is not list:
            _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate pipeline is invalid")
        stages: list[StageBinding] = []
        for item in cast(list[object], raw_pipeline):
            if type(item) is not dict or set(item) != {"stage", "parameters"}:
                _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate stage is invalid")
            stage = item.get("stage")
            parameters = item.get("parameters")
            if type(stage) is not str or type(parameters) is not dict:
                _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate stage is invalid")
            stages.append(StageBinding(stage, parameters))
        bindings[plan.case_id] = PipelineBinding(tuple(stages))
    selected_outputs = _validated_output_map(outputs)
    selected_bindings = _validated_binding_map(bindings)
    if index != _index(selected_outputs, selected_bindings, approval=None):
        _reject("GOLDEN_CANDIDATE_INVALID", "golden candidate bindings or digests are invalid")
    approved_index = _index(selected_outputs, selected_bindings, approval=approval)
    files = {
        PurePosixPath(_INDEX_FILE_NAME): _canonical_json_bytes(approved_index),
        **{
            plan.output_name: canonical_rgba_png(
                selected_outputs[plan.case_id],
                description=f"{SYNTHETIC_MARKER}: transform {plan.case_id} output",
            )
            for plan in CASE_PLANS
        },
    }
    _atomic_external_tree(destination, files, repository_root=repository_root)


def validate_committed_golden_set(
    root: Path = PROJECT_ROOT,
) -> tuple[int, tuple[str, ...]]:
    """Validate exact approved inventory, source bindings, and output bytes non-mutatingly."""

    golden_root = root / GOLDEN_DIRECTORY
    try:
        actual = _candidate_inventory(golden_root)
    except ImageTransformGoldenError:
        return 0, ("GOLDEN_SET_UNAVAILABLE",)
    expected = {PurePosixPath(_INDEX_FILE_NAME), *(plan.output_name for plan in CASE_PLANS)}
    if actual != expected:
        return 0, ("GOLDEN_INVENTORY_INVALID",)
    try:
        index = _load_index(golden_root / _INDEX_FILE_NAME)
        references = _parse_references(index)
        approval = index.get("approval")
        if (
            type(approval) is not dict
            or set(approval) != {"status", "reviewed_at", "reviewer_role", "rationale"}
            or approval.get("status") != "approved"
        ):
            _reject("GOLDEN_APPROVAL_INVALID", "golden approval is missing")
        approval_record = cast(dict[str, object], approval)
        reviewed_at = approval_record.get("reviewed_at")
        reviewer_role = approval_record.get("reviewer_role")
        rationale = approval_record.get("rationale")
        if (
            type(reviewed_at) is not str
            or type(reviewer_role) is not str
            or type(rationale) is not str
        ):
            _reject("GOLDEN_APPROVAL_INVALID", "golden approval is invalid")
        approved = GoldenApproval(
            reviewed_at=reviewed_at,
            reviewer_role=reviewer_role,
            rationale=rationale,
        )
        outputs: dict[str, RgbaImage] = {}
        bindings: dict[str, PipelineBinding] = {}
        for plan, reference in zip(CASE_PLANS, references, strict=True):
            if (
                reference.get("id") != plan.case_id
                or reference.get("path") != plan.output_name.as_posix()
            ):
                _reject("GOLDEN_REFERENCE_INVALID", "golden reference identity is invalid")
            content = _regular_file(
                golden_root / plan.output_name,
                maximum_bytes=MAXIMUM_PIXEL_BYTES,
            )
            output = decode_canonical_rgba_png(content)
            if (
                output.width != plan.output_width
                or output.height != plan.output_height
                or not output.is_opaque
            ):
                _reject("GOLDEN_REFERENCE_INVALID", "golden output profile is invalid")
            expected_content = canonical_rgba_png(
                output,
                description=f"{SYNTHETIC_MARKER}: transform {plan.case_id} output",
            )
            if content != expected_content:
                _reject("GOLDEN_REFERENCE_INVALID", "golden output encoding is invalid")
            raw_pipeline = reference.get("pipeline")
            if type(raw_pipeline) is not list:
                _reject("GOLDEN_REFERENCE_INVALID", "golden reference binding is invalid")
            stages: list[StageBinding] = []
            for item in cast(list[object], raw_pipeline):
                if type(item) is not dict or set(item) != {"stage", "parameters"}:
                    _reject("GOLDEN_REFERENCE_INVALID", "golden pipeline stage is invalid")
                stage = item.get("stage")
                parameters = item.get("parameters")
                if type(stage) is not str or type(parameters) is not dict:
                    _reject("GOLDEN_REFERENCE_INVALID", "golden pipeline stage is invalid")
                stages.append(StageBinding(stage, parameters))
            outputs[plan.case_id] = output
            bindings[plan.case_id] = PipelineBinding(tuple(stages))
        selected_outputs = _validated_output_map(outputs)
        selected_bindings = _validated_binding_map(bindings)
        if index != _index(selected_outputs, selected_bindings, approval=approved):
            _reject("GOLDEN_REFERENCE_INVALID", "golden reference bindings are invalid")
    except ImageTransformGoldenError as error:
        return 0, (error.code,)
    return len(CASE_PLANS), ()


def case_plan(case_id: str) -> GoldenCasePlan:
    """Return one closed case plan without exposing the mutable index mapping."""

    try:
        return _PLANS_BY_ID[case_id]
    except KeyError, TypeError:
        _reject("GOLDEN_CASE_UNKNOWN", "golden case is not registered")


def source_image(case_id: str) -> RgbaImage:
    """Return one fresh public source image by closed case identifier."""

    return _pixel_source(case_plan(case_id))


def expected_pipeline_binding(case_id: str) -> PipelineBinding:
    """Return the exact profile-v1 machine binding for one closed golden case."""

    return _expected_pipeline_binding(case_plan(case_id))


def expected_promotion_inventory() -> tuple[PurePosixPath, ...]:
    """Return the exact reviewed directory inventory in stable order."""

    return (PurePosixPath(_INDEX_FILE_NAME), *(plan.output_name for plan in CASE_PLANS))
