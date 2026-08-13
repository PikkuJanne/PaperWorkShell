"""Focused closed-harness tests for PWS-0202 transform golden review."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import cast

import pytest

from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_VERSION,
    IMAGE_TRANSFORM_CACHE_KEY_VERSION,
    IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
    IMAGE_TRANSFORM_INTENT_VERSION,
    IMAGE_TRANSFORM_NORMALIZATION_PROFILE_SHA256,
    IMAGE_TRANSFORM_ORDER,
    IMAGE_TRANSFORM_PROFILE_VERSION,
)
from paperworkshell.imaging.pillow_decoder import EXPECTED_PILLOW_VERSION
from tools.capture_image_transform_goldens import capture_production_outputs
from tools.image_transform_goldens import (
    CASE_PLANS,
    GOLDEN_DIRECTORY,
    PIPELINE_ORDER,
    PROJECT_ROOT,
    TRANSFORM_ARTIFACT_VERSION,
    TRANSFORM_CACHE_KEY_VERSION,
    TRANSFORM_ENGINE_PROFILE_SHA256,
    TRANSFORM_INTENT_VERSION,
    TRANSFORM_NORMALIZATION_PROFILE_SHA256,
    TRANSFORM_PIXEL_DEPENDENCY,
    TRANSFORM_PIXEL_DEPENDENCY_VERSION,
    TRANSFORM_PROFILE_VERSION,
    GoldenApproval,
    ImageTransformGoldenError,
    PipelineBinding,
    RgbaImage,
    StageBinding,
    approve_review_candidate_set,
    build_source_images,
    canonical_rgba_png,
    decode_canonical_rgba_png,
    expected_promotion_inventory,
    source_image,
    stage_review_candidate_set,
    validate_committed_golden_set,
)


def _binding(case_id: str) -> PipelineBinding:
    is_crop = case_id == "crop"
    values: dict[str, dict[str, str | int | bool | None]] = {
        "exif": {"orientation": 1},
        "crop": {
            "x_millionths": 250_000 if is_crop else 0,
            "y_millionths": 250_000 if is_crop else 0,
            "width_millionths": 500_000 if is_crop else 1_000_000,
            "height_millionths": 500_000 if is_crop else 1_000_000,
        },
        "quarter_turn": {"degrees_clockwise": 90 if case_id == "rotate-90" else 0},
        "deskew": {
            "angle_millidegrees_clockwise": 3_000 if case_id == "deskew" else 0,
            "canvas": "fixed_opaque_white",
            "resampling": "bicubic",
        },
        "contrast": {
            "factor_millionths": 1_250_000 if case_id == "contrast" else 1_000_000,
            "midpoint": 128,
            "rounding": "integer_half_away",
        },
        "background_cleanup": {
            "rounding": "integer_half_up",
            "strength_millionths": 1_000_000 if case_id == "background-cleanup" else 0,
            "threshold": 192,
        },
        "fit": {
            "maximum_height_pixels": next(
                plan.output_height for plan in CASE_PLANS if plan.case_id == case_id
            ),
            "maximum_width_pixels": next(
                plan.output_width for plan in CASE_PLANS if plan.case_id == case_id
            ),
            "no_upscale": True,
            "reducing_gap_millionths": 3_000_000,
            "resampling": "lanczos",
        },
        "serialize": {
            "background": "opaque_white",
            "pixel_layout": "rgba8_top_down",
        },
    }
    return PipelineBinding(tuple(StageBinding(stage, values[stage]) for stage in PIPELINE_ORDER))


def _bindings() -> dict[str, PipelineBinding]:
    return {plan.case_id: _binding(plan.case_id) for plan in CASE_PLANS}


def _outputs() -> dict[str, RgbaImage]:
    outputs: dict[str, RgbaImage] = {}
    for case_index, plan in enumerate(CASE_PLANS, start=1):
        pixels = bytearray()
        for y in range(plan.output_height):
            for x in range(plan.output_width):
                pixels.extend(
                    (
                        (x * 17 + case_index * 13) % 256,
                        (y * 19 + case_index * 7) % 256,
                        (x + y + case_index * 29) % 256,
                        255,
                    )
                )
        outputs[plan.case_id] = RgbaImage(
            plan.output_width,
            plan.output_height,
            bytes(pixels),
        )
    return outputs


def _external_layout(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    destination_parent = tmp_path / "review-output"
    destination_parent.mkdir()
    return repository, destination_parent


def test_source_recipes_are_closed_deterministic_and_cover_the_minimal_matrix() -> None:
    first = build_source_images()
    second = build_source_images()

    assert first == second
    assert tuple(first) == tuple(plan.case_id for plan in CASE_PLANS)
    assert tuple(plan.output_name for plan in CASE_PLANS) == (
        PurePosixPath("rotate-90.png"),
        PurePosixPath("crop.png"),
        PurePosixPath("deskew.png"),
        PurePosixPath("contrast.png"),
        PurePosixPath("background-cleanup.png"),
        PurePosixPath("transparency.png"),
    )
    assert [(plan.output_width, plan.output_height) for plan in CASE_PLANS] == [
        (9, 13),
        (8, 6),
        (48, 32),
        (32, 8),
        (32, 16),
        (12, 10),
    ]
    assert not first["transparency"].is_opaque
    assert all(image.is_opaque for case_id, image in first.items() if case_id != "transparency")
    assert source_image("rotate-90").pixels[:4] == bytes((255, 0, 0, 255))


def test_canonical_rgba_png_is_exact_round_trip_and_rejects_mutation() -> None:
    source = source_image("transparency")
    first = canonical_rgba_png(
        source,
        description="PWS-SYNTHETIC-FIXTURE: alpha-canary-12x10-v1",
    )
    second = canonical_rgba_png(
        source,
        description="PWS-SYNTHETIC-FIXTURE: alpha-canary-12x10-v1",
    )

    assert first == second
    assert decode_canonical_rgba_png(first) == source
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    changed = bytearray(first)
    changed[-5] ^= 1
    with pytest.raises(ImageTransformGoldenError) as raised:
        decode_canonical_rgba_png(bytes(changed))
    assert raised.value.code == "GOLDEN_PNG_INVALID"


def test_production_transform_profile_matches_every_approved_golden_pixel() -> None:
    captured = capture_production_outputs()

    assert tuple(captured) == tuple(plan.case_id for plan in CASE_PLANS)
    for plan in CASE_PLANS:
        committed = decode_canonical_rgba_png(
            (PROJECT_ROOT / GOLDEN_DIRECTORY / plan.output_name).read_bytes()
        )
        assert captured[plan.case_id] == committed


def test_pipeline_binding_requires_exact_documented_order_and_copies_parameters() -> None:
    assert PIPELINE_ORDER == IMAGE_TRANSFORM_ORDER
    assert TRANSFORM_PROFILE_VERSION == IMAGE_TRANSFORM_PROFILE_VERSION
    assert TRANSFORM_ENGINE_PROFILE_SHA256 == IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256
    assert TRANSFORM_NORMALIZATION_PROFILE_SHA256 == IMAGE_TRANSFORM_NORMALIZATION_PROFILE_SHA256
    assert (
        TRANSFORM_INTENT_VERSION,
        TRANSFORM_CACHE_KEY_VERSION,
        TRANSFORM_ARTIFACT_VERSION,
    ) == (
        IMAGE_TRANSFORM_INTENT_VERSION,
        IMAGE_TRANSFORM_CACHE_KEY_VERSION,
        IMAGE_TRANSFORM_ARTIFACT_VERSION,
    )
    assert (TRANSFORM_PIXEL_DEPENDENCY, TRANSFORM_PIXEL_DEPENDENCY_VERSION) == (
        "Pillow",
        EXPECTED_PILLOW_VERSION,
    )
    selected = _binding("crop")
    assert tuple(stage["stage"] for stage in selected.to_json()) == PIPELINE_ORDER
    assert len(selected.sha256) == 64

    with pytest.raises(ImageTransformGoldenError) as reordered:
        PipelineBinding(tuple(reversed(selected.stages)))
    assert reordered.value.code == "GOLDEN_PIPELINE_ORDER_INVALID"

    mutable = {"orientation": 1}
    stage = StageBinding("exif", mutable)
    mutable["orientation"] = 8
    assert stage.parameters == {"orientation": 1}
    with pytest.raises(TypeError):
        cast(dict[str, str | int | bool | None], stage.parameters)["orientation"] = 8


def test_candidate_staging_is_external_nonoverwriting_and_review_only(tmp_path: Path) -> None:
    repository, destination_parent = _external_layout(tmp_path)
    destination = destination_parent / "candidate"

    stage_review_candidate_set(
        destination,
        _outputs(),
        _bindings(),
        repository_root=repository,
    )

    index = json.loads((destination / "index.json").read_text(encoding="ascii"))
    assert index["approval"] == {"status": "candidate-requires-human-review"}
    assert index["pipeline_order"] == list(PIPELINE_ORDER)
    assert index["production_profile"] == {
        "artifact_version": IMAGE_TRANSFORM_ARTIFACT_VERSION,
        "cache_key_version": IMAGE_TRANSFORM_CACHE_KEY_VERSION,
        "engine_profile_sha256": IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
        "intent_version": IMAGE_TRANSFORM_INTENT_VERSION,
        "normalization_profile_sha256": IMAGE_TRANSFORM_NORMALIZATION_PROFILE_SHA256,
        "pixel_dependency": "Pillow",
        "pixel_dependency_version": EXPECTED_PILLOW_VERSION,
        "profile_version": IMAGE_TRANSFORM_PROFILE_VERSION,
    }
    assert [item["id"] for item in index["references"]] == [plan.case_id for plan in CASE_PLANS]
    review = (destination / "review.html").read_text(encoding="utf-8")
    assert all(plan.case_id in review for plan in CASE_PLANS)
    assert review.count("8× nearest-neighbour") == len(CASE_PLANS) * 2
    assert all((destination / "source" / plan.output_name).is_file() for plan in CASE_PLANS)
    assert all((destination / "candidate" / plan.output_name).is_file() for plan in CASE_PLANS)

    with pytest.raises(ImageTransformGoldenError) as overwrite:
        stage_review_candidate_set(
            destination,
            _outputs(),
            _bindings(),
            repository_root=repository,
        )
    assert overwrite.value.code == "GOLDEN_OUTPUT_EXISTS"

    with pytest.raises(ImageTransformGoldenError) as repository_write:
        stage_review_candidate_set(
            repository / "forbidden",
            _outputs(),
            _bindings(),
            repository_root=repository,
        )
    assert repository_write.value.code == "GOLDEN_OUTPUT_UNSAFE"


def test_candidate_requires_complete_opaque_outputs_and_exact_bindings(tmp_path: Path) -> None:
    repository, destination_parent = _external_layout(tmp_path)
    outputs = _outputs()
    del outputs["deskew"]
    with pytest.raises(ImageTransformGoldenError) as missing:
        stage_review_candidate_set(
            destination_parent / "missing-output",
            outputs,
            _bindings(),
            repository_root=repository,
        )
    assert missing.value.code == "GOLDEN_OUTPUT_INVENTORY_INVALID"

    outputs = _outputs()
    selected = outputs["transparency"]
    pixels = selected.pixels[:-1] + b"\x00"
    outputs["transparency"] = RgbaImage(selected.width, selected.height, pixels)
    with pytest.raises(ImageTransformGoldenError) as nonopaque:
        stage_review_candidate_set(
            destination_parent / "nonopaque-output",
            outputs,
            _bindings(),
            repository_root=repository,
        )
    assert nonopaque.value.code == "GOLDEN_OUTPUT_INVALID"

    bindings = _bindings()
    stages = list(bindings["crop"].stages)
    stages[0] = StageBinding("exif", {"orientation": 8})
    bindings["crop"] = PipelineBinding(tuple(stages))
    with pytest.raises(ImageTransformGoldenError) as wrong_profile:
        stage_review_candidate_set(
            destination_parent / "wrong-profile",
            _outputs(),
            bindings,
            repository_root=repository,
        )
    assert wrong_profile.value.code == "GOLDEN_BINDING_INVALID"


def test_explicit_approval_creates_only_promotion_inventory_and_validator_passes(
    tmp_path: Path,
) -> None:
    repository, destination_parent = _external_layout(tmp_path)
    candidate = destination_parent / "candidate"
    promotion = destination_parent / "promotion"
    stage_review_candidate_set(
        candidate,
        _outputs(),
        _bindings(),
        repository_root=repository,
    )

    approve_review_candidate_set(
        candidate,
        promotion,
        GoldenApproval("2026-08-13"),
        repository_root=repository,
    )

    actual = tuple(
        sorted(
            PurePosixPath(path.relative_to(promotion).as_posix())
            for path in promotion.rglob("*")
            if path.is_file()
        )
    )
    assert actual == tuple(sorted(expected_promotion_inventory()))
    approved = json.loads((promotion / "index.json").read_text(encoding="ascii"))
    assert approved["approval"] == {
        "status": "approved",
        "reviewed_at": "2026-08-13",
        "reviewer_role": "implementation-agent",
        "rationale": "initial-pws-0202-baseline",
    }

    validation_root = tmp_path / "validation-root"
    target = validation_root / GOLDEN_DIRECTORY
    target.parent.mkdir(parents=True)
    shutil.copytree(promotion, target)
    assert validate_committed_golden_set(validation_root) == (len(CASE_PLANS), ())


def test_validator_fails_closed_for_mutated_missing_or_unapproved_set(tmp_path: Path) -> None:
    repository, destination_parent = _external_layout(tmp_path)
    candidate = destination_parent / "candidate"
    promotion = destination_parent / "promotion"
    stage_review_candidate_set(
        candidate,
        _outputs(),
        _bindings(),
        repository_root=repository,
    )
    approve_review_candidate_set(
        candidate,
        promotion,
        GoldenApproval("2026-08-13"),
        repository_root=repository,
    )
    validation_root = tmp_path / "validation-root"
    target = validation_root / GOLDEN_DIRECTORY
    target.parent.mkdir(parents=True)
    shutil.copytree(promotion, target)

    (target / "crop.png").write_bytes((target / "crop.png").read_bytes()[:-1])
    count, issues = validate_committed_golden_set(validation_root)
    assert count == 0
    assert issues

    shutil.rmtree(target)
    shutil.copytree(promotion, target)
    (target / "extra.png").write_bytes(b"not a golden")
    assert validate_committed_golden_set(validation_root) == (
        0,
        ("GOLDEN_INVENTORY_INVALID",),
    )


def test_harness_has_no_production_transform_import() -> None:
    source = (Path(__file__).parents[2] / "tools" / "image_transform_goldens.py").read_text(
        encoding="utf-8"
    )
    assert "import paperworkshell" not in source
    assert "from paperworkshell" not in source
