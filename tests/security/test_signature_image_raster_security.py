"""Privacy and hostile-metadata tests for signature-image worker rasters."""

from __future__ import annotations

import ast
import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, PngImagePlugin

from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImagePageError,
    ImagePageErrorCode,
    ImageSourceFormat,
)
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterRequest,
)
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder
from paperworkshell.worker.signature_image_raster_broker import _map_image_error

_PRIVATE_METADATA = "PRIVATE-SIGNATURE-METADATA-CANARY"
_PRIVATE_NATIVE = "PRIVATE-NATIVE-DECODER-CANARY"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


def _limits() -> ImageNormalizationLimits:
    return ImageNormalizationLimits(
        maximum_source_bytes=1_000_000,
        maximum_encoded_width_pixels=256,
        maximum_encoded_height_pixels=256,
        maximum_source_pixels=65_536,
        maximum_decompressed_bytes=262_144,
        maximum_metadata_records=256,
        maximum_metadata_bytes=65_536,
        maximum_output_width_pixels=256,
        maximum_output_height_pixels=256,
        maximum_output_pixels=65_536,
        maximum_output_bytes=262_144,
        maximum_memory_bytes=524_288,
        wall_time_ms=10_000,
    )


def _png_with_private_text() -> bytes:
    image = Image.new("RGBA", (2, 1))
    image.putdata(((71, 82, 93, 0), (12, 34, 56, 160)))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", _PRIVATE_METADATA)
    sink = BytesIO()
    image.save(sink, format="PNG", pnginfo=metadata)
    image.close()
    return sink.getvalue()


def test_private_metadata_is_stripped_and_transparent_rgb_is_canonical() -> None:
    source = _png_with_private_text()
    request = ImageNormalizationRequest(
        ImageSourceFormat.PNG,
        len(source),
        hashlib.sha256(source).hexdigest(),
        2,
        1,
        alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
    )
    destination = BytesIO()

    result = PillowImageDecoder().normalize(
        BytesIO(source),
        destination,
        expected_format=ImageSourceFormat.PNG,
        request=request,
        limits=_limits(),
        control=_Control(),
    )

    pixels = destination.getvalue()
    assert pixels == bytes((0, 0, 0, 0, 12, 34, 56, 160))
    surfaces = b"\n".join(
        (
            pixels,
            repr(result).encode(),
            str(result).encode(),
        )
    )
    assert _PRIVATE_METADATA.encode() not in surfaces
    assert result.metadata.metadata_byte_count >= len(_PRIVATE_METADATA)


def test_icc_and_native_failure_values_are_rebuilt_as_closed_signature_errors() -> None:
    for image_error in (
        ImagePageError(ImagePageErrorCode.UNSUPPORTED, "image.signature_icc_profile"),
        ImagePageError(ImagePageErrorCode.DEPENDENCY_FAILED, "image.decoder_dependency"),
    ):
        image_error.args = (*image_error.args, _PRIVATE_NATIVE)
        with pytest.raises(SignatureImageError) as caught:
            _map_image_error(image_error)

        expected = (
            SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED
            if image_error.code is ImagePageErrorCode.UNSUPPORTED
            else SignatureImageErrorCode.DECODE_FAILED
        )
        assert caught.value.code is expected
        assert _PRIVATE_NATIVE not in str(caught.value)
        assert _PRIVATE_NATIVE not in repr(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_signature_request_and_protocol_reference_representations_hide_digests() -> None:
    request = SignatureImageRasterRequest(
        UUID("82000000-0000-4000-8000-000000000001"),
        UUID("82000000-0000-4000-8000-000000000002"),
        SignatureImageMediaType.PNG,
        100,
        "de" * 32,
        2,
        1,
    )

    assert "de" * 32 not in repr(request)
    assert not hasattr(request, "source_path")
    assert not hasattr(request, "source_name")


def test_signature_worker_path_has_no_network_shell_or_logging_imports() -> None:
    forbidden = {"logging", "requests", "socket", "subprocess", "urllib"}
    paths = (
        _PROJECT_ROOT / "src/paperworkshell/domain/signature_images.py",
        _PROJECT_ROOT / "src/paperworkshell/imaging/pillow_decoder.py",
        _PROJECT_ROOT / "src/paperworkshell/worker/signature_image_raster_operation.py",
        _PROJECT_ROOT / "src/paperworkshell/worker/signature_image_raster_broker.py",
    )

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module.split(".", 1)[0])
        assert imports.isdisjoint(forbidden), path
