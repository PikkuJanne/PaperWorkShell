"""Capture PWS-0202 transform candidates from the production Pillow pipeline.

Candidate output is always staged outside the repository.  Promotion remains a
separate, explicit review action owned by :mod:`tools.image_transform_goldens`.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Final

from paperworkshell.domain.image_pages import (
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformIntent,
    ImageTransformLimits,
)
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder
from paperworkshell.imaging.pillow_transformer import PillowImageTransformer
from tools.image_transform_goldens import (
    CASE_PLANS,
    PROJECT_ROOT,
    SYNTHETIC_MARKER,
    PipelineBinding,
    RgbaImage,
    canonical_rgba_png,
    expected_pipeline_binding,
    source_image,
    stage_review_candidate_set,
)

_MEBIBYTE: Final = 1024 * 1024


class _NeverCancel:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


def _normalization_limits() -> ImageNormalizationLimits:
    return ImageNormalizationLimits(
        maximum_source_bytes=_MEBIBYTE,
        maximum_encoded_width_pixels=4096,
        maximum_encoded_height_pixels=4096,
        maximum_source_pixels=_MEBIBYTE,
        maximum_decompressed_bytes=16 * _MEBIBYTE,
        maximum_metadata_records=256,
        maximum_metadata_bytes=_MEBIBYTE,
        maximum_output_width_pixels=4096,
        maximum_output_height_pixels=4096,
        maximum_output_pixels=_MEBIBYTE,
        maximum_output_bytes=16 * _MEBIBYTE,
        maximum_memory_bytes=64 * _MEBIBYTE,
        wall_time_ms=10_000,
    )


def _transform_limits() -> ImageTransformLimits:
    return ImageTransformLimits(
        maximum_source_width_pixels=4096,
        maximum_source_height_pixels=4096,
        maximum_source_pixels=_MEBIBYTE,
        maximum_intermediate_pixels=_MEBIBYTE,
        maximum_output_width_pixels=4096,
        maximum_output_height_pixels=4096,
        maximum_output_pixels=_MEBIBYTE,
        maximum_output_bytes=16 * _MEBIBYTE,
        maximum_memory_bytes=64 * _MEBIBYTE,
        maximum_temporary_storage_bytes=32 * _MEBIBYTE,
        wall_time_ms=10_000,
    )


def _intent(case_id: str) -> ImageTransformIntent:
    return ImageTransformIntent(
        crop=(
            ImageNormalizedCrop(250_000, 250_000, 500_000, 500_000)
            if case_id == "crop"
            else ImageNormalizedCrop()
        ),
        additional_rotation=(
            ImageQuarterTurn.DEGREES_90 if case_id == "rotate-90" else ImageQuarterTurn.DEGREES_0
        ),
        deskew_angle_millidegrees_clockwise=3_000 if case_id == "deskew" else 0,
        contrast_factor_millionths=1_250_000 if case_id == "contrast" else 1_000_000,
        background_cleanup_strength_millionths=(
            1_000_000 if case_id == "background-cleanup" else 0
        ),
    )


def _normalized_source(case_id: str) -> tuple[ImageRgba8Layout, bytes]:
    plan = next(item for item in CASE_PLANS if item.case_id == case_id)
    source = source_image(case_id)
    encoded = canonical_rgba_png(
        source,
        description=f"{SYNTHETIC_MARKER}: {plan.source_recipe}",
    )
    destination = BytesIO()
    result = PillowImageDecoder().normalize(
        BytesIO(encoded),
        destination,
        expected_format=ImageSourceFormat.PNG,
        request=ImageNormalizationRequest(
            expected_source_format=ImageSourceFormat.PNG,
            expected_source_bytes=len(encoded),
            expected_source_sha256=hashlib.sha256(encoded).hexdigest(),
            maximum_width_pixels=plan.source_width,
            maximum_height_pixels=plan.source_height,
        ),
        limits=_normalization_limits(),
        control=_NeverCancel(),
    )
    layout = ImageRgba8Layout(
        result.width_pixels,
        result.height_pixels,
        result.row_stride_bytes,
        result.width_pixels * result.height_pixels,
        result.bytes_written,
    )
    pixels = destination.getvalue()
    if len(pixels) != layout.byte_count:
        raise RuntimeError("production normalization returned an invalid raster")
    return layout, pixels


def capture_production_outputs() -> dict[str, RgbaImage]:
    """Run every closed public recipe through the production transform profile."""

    outputs: dict[str, RgbaImage] = {}
    transformer = PillowImageTransformer()
    for plan in CASE_PLANS:
        source_layout, pixels = _normalized_source(plan.case_id)
        destination = BytesIO()
        result = transformer.transform(
            BytesIO(pixels),
            destination,
            source_layout=source_layout,
            intent=_intent(plan.case_id),
            maximum_width_pixels=plan.output_width,
            maximum_height_pixels=plan.output_height,
            limits=_transform_limits(),
            control=_NeverCancel(),
        )
        outputs[plan.case_id] = RgbaImage(
            result.width_pixels,
            result.height_pixels,
            destination.getvalue(),
        )
    return outputs


def production_bindings() -> dict[str, PipelineBinding]:
    """Return the separately specified machine bindings for captured cases."""

    return {plan.case_id: expected_pipeline_binding(plan.case_id) for plan in CASE_PLANS}


def stage_production_review(destination: Path, *, repository_root: Path = PROJECT_ROOT) -> None:
    """Capture once and stage a non-promotable review tree outside the repository."""

    stage_review_candidate_set(
        destination,
        capture_production_outputs(),
        production_bindings(),
        repository_root=repository_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repository-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args(argv)
    stage_production_review(arguments.destination, repository_root=arguments.repository_root)
    print("OK: staged six production image-transform candidates for explicit review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
