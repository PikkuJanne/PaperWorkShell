"""Persistent image lookup identity can be computed without decoding the source."""

from __future__ import annotations

from dataclasses import replace
from typing import TypedDict
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    ImageExifOrientation,
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformCacheKey,
    ImageTransformIntent,
    ImageTransformLookupKey,
    decode_image_transform_artifact,
)
from tests.unit.worker.test_image_transform_broker import _artifact, _uuid


class _Changes(TypedDict, total=False):
    case_id: UUID
    source_id: UUID
    page_id: UUID
    source_byte_size: int
    source_sha256: str
    source_format: ImageSourceFormat
    intent: ImageTransformIntent
    maximum_width_pixels: int
    maximum_height_pixels: int
    engine_profile_sha256: str
    artifact_version: int
    key_version: int


def _key() -> ImageTransformCacheKey:
    return ImageTransformCacheKey(
        case_id=_uuid(1),
        source_id=_uuid(2),
        page_id=_uuid(3),
        source_byte_size=100,
        source_sha256="a" * 64,
        source_format=ImageSourceFormat.JPEG,
        source_layout=ImageRgba8Layout(2, 2, 8, 4, 16),
        source_pixel_sha256="b" * 64,
        exif_orientation=ImageExifOrientation.NORMAL,
        intent=ImageTransformIntent(),
        maximum_width_pixels=240,
        maximum_height_pixels=320,
    )


def test_predecode_lookup_does_not_need_decoded_geometry_or_pixel_digest() -> None:
    full = _key()
    lookup = ImageTransformLookupKey.from_cache_key(full)
    assert lookup.matches(full)
    assert lookup.internal_name == f"{full.page_id}.{lookup.key_sha256}.pwstransform"
    assert (
        ImageTransformLookupKey(
            case_id=full.case_id,
            source_id=full.source_id,
            page_id=full.page_id,
            source_byte_size=full.source_byte_size,
            source_sha256=full.source_sha256,
            source_format=full.source_format,
            intent=full.intent,
            maximum_width_pixels=240,
            maximum_height_pixels=320,
        )
        == lookup
    )
    assert "a" * 64 not in repr(lookup)
    assert "b" * 64 not in repr(lookup)


@pytest.mark.parametrize(
    "change",
    (
        {"case_id": _uuid(10)},
        {"source_id": _uuid(10)},
        {"page_id": _uuid(10)},
        {"source_byte_size": 101},
        {"source_sha256": "c" * 64},
        {"source_format": ImageSourceFormat.PNG},
        {"maximum_width_pixels": 241},
        {"maximum_height_pixels": 321},
        {"intent": ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_90)},
        {"intent": ImageTransformIntent(crop=ImageNormalizedCrop(1, 2, 999_999, 999_998))},
        {"intent": ImageTransformIntent(deskew_angle_millidegrees_clockwise=1)},
        {"intent": ImageTransformIntent(contrast_factor_millionths=1_000_001)},
        {"intent": ImageTransformIntent(background_cleanup_strength_millionths=1)},
    ),
)
def test_every_known_pixel_identity_field_invalidates_lookup(change: _Changes) -> None:
    first = ImageTransformLookupKey.from_cache_key(_key())
    # Dataclass replacement deliberately exercises public constructor validation.
    changed = replace(first, **change)
    assert changed.key_sha256 != first.key_sha256
    assert changed.internal_name != first.internal_name
    assert not changed.matches(_key())


@pytest.mark.parametrize(
    "change",
    (
        {"key_version": 2},
        {"artifact_version": 2},
        {"engine_profile_sha256": "c" * 64},
        {"source_sha256": "bad"},
        {"case_id": UUID(int=0)},
        {"source_byte_size": 0},
        {"maximum_width_pixels": 0},
        {"maximum_height_pixels": True},
    ),
)
def test_lookup_rejects_future_versions_and_invalid_identity(change: _Changes) -> None:
    with pytest.raises(ImagePageError):
        replace(ImageTransformLookupKey.from_cache_key(_key()), **change)


def test_native_artifact_decode_checks_expected_predecode_identity() -> None:
    encoded = _artifact(_uuid(20), _uuid(21))
    artifact = decode_image_transform_artifact(encoded)
    lookup = ImageTransformLookupKey.from_cache_key(artifact.header.cache_key)
    assert decode_image_transform_artifact(encoded, expected_lookup_key=lookup) == artifact
    with pytest.raises(ImagePageError) as caught:
        decode_image_transform_artifact(
            encoded,
            expected_lookup_key=replace(lookup, source_sha256="c" * 64),
        )
    assert caught.value.code is ImagePageErrorCode.CACHE_INVALID
