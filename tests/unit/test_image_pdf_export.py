"""Image-page PDF export without OCR, preserving the original PDF-only contract."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast
from uuid import uuid4

import pikepdf
import pytest
from PIL import Image, ImageOps

from paperworkshell.domain.image_pages import ImageNormalizationLimits
from paperworkshell.domain.pdf_export import (
    IMAGE_PDF_EXPORT_PROFILE,
    PdfExportAssemblyProfile,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportProfile,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
    build_pdf_export_plan,
)
from paperworkshell.pdf import export_adapter as adapter_module
from paperworkshell.pdf.export_adapter import PdfExportAdapter
from paperworkshell.worker.pdf_export_plan import (
    decode_pdf_export_worker_plan,
    encode_pdf_export_worker_plan,
)
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND
from tests.unit.test_pdf_export import _document, _snapshot, _source_record
from tests.unit.test_pdf_export_adapter import (
    _CleanChecker,
    _control,
    _limits,
    _MismatchingRenderer,
)
from tests.unit.worker.test_pdf_export_protocol import _payload


def _image_source(path: Path, media: PdfExportSourceMediaType) -> PdfExportSource:
    payload = path.read_bytes()
    return PdfExportSource(
        source_id=uuid4(),
        byte_size=len(payload),
        page_count=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type=media,
        signature_state=PdfExportSourceSignatureState.NOT_APPLICABLE,
        encryption_state=PdfExportSourceEncryptionState.NOT_APPLICABLE,
    )


def _image_plan(
    source: PdfExportSource, rotation: PdfExportRotation = PdfExportRotation.DEGREES_0
) -> PdfExportPlan:
    return PdfExportPlan(
        case_id=uuid4(),
        manifest_revision=1,
        manifest_sha256="a" * 64,
        manifest_cas_token="synthetic_cas_000001",
        sources=(source,),
        pages=(PdfExportPage(0, uuid4(), source.source_id, 0, rotation),),
        profile=IMAGE_PDF_EXPORT_PROFILE,
        plan_version=2,
    )


def _image_limits() -> ImageNormalizationLimits:
    return ImageNormalizationLimits(
        maximum_source_bytes=1_000_000,
        maximum_encoded_width_pixels=1000,
        maximum_encoded_height_pixels=1000,
        maximum_source_pixels=1_000_000,
        maximum_decompressed_bytes=4_000_000,
        maximum_metadata_records=100,
        maximum_metadata_bytes=10_000,
        maximum_output_width_pixels=1000,
        maximum_output_height_pixels=1000,
        maximum_output_pixels=1_000_000,
        maximum_output_bytes=4_000_000,
        maximum_memory_bytes=64_000_000,
        wall_time_ms=60_000,
    )


def test_manifest_image_plan_requires_v2_and_round_trips_without_ocr_fields() -> None:
    document = _document()
    image_source = _source_record(document, 1)
    image_source.update(
        detected_media_type="image/png",
        signature_state="not_applicable",
        encryption_state="not_applicable",
    )
    snapshot = _snapshot(document)
    with pytest.raises(PdfExportError) as caught:
        build_pdf_export_plan(snapshot)
    assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
    plan = build_pdf_export_plan(snapshot, profile=IMAGE_PDF_EXPORT_PROFILE)
    assert plan.plan_version == 2
    assert plan.sources[0].media_type is PdfExportSourceMediaType.PNG
    payload = encode_pdf_export_worker_plan(
        plan,
        {source.source_id: 101 + index for index, source in enumerate(plan.sources)},
    )
    assert decode_pdf_export_worker_plan(payload).plan == plan
    assert b"ocr" not in payload
    raw = json.loads(payload)
    raw["plan_version"] = 1
    with pytest.raises(PdfExportError):
        decode_pdf_export_worker_plan(json.dumps(raw).encode())


@pytest.mark.parametrize("version", [0, 3, True])
def test_image_profile_rejects_unknown_and_invalid_versions(version: int) -> None:
    with pytest.raises(PdfExportError):
        replace(IMAGE_PDF_EXPORT_PROFILE, profile_version=version)


def test_profile_version_and_assembly_are_an_exact_pair() -> None:
    assert PdfExportProfile().profile_version == 1
    assert IMAGE_PDF_EXPORT_PROFILE.profile_version == 2
    with pytest.raises(PdfExportError):
        replace(IMAGE_PDF_EXPORT_PROFILE, profile_version=1)
    with pytest.raises(PdfExportError):
        replace(IMAGE_PDF_EXPORT_PROFILE, assembly=PdfExportAssemblyProfile.PIKEPDF_PAGE_COPY_V1)


@pytest.mark.parametrize("assembly", ["pikepdf_image_pages_v2", None, []])
def test_image_profile_rejects_untyped_assembly_with_closed_error(assembly: object) -> None:
    with pytest.raises(PdfExportError):
        replace(IMAGE_PDF_EXPORT_PROFILE, assembly=cast(PdfExportAssemblyProfile, assembly))


@pytest.mark.parametrize("kind", ["jpeg", "png"])
def test_protocol_image_sources_require_v2_plan_and_capability(kind: str) -> None:
    original = _payload()
    image = replace(original.inputs[1], content_kind=kind)
    with pytest.raises(ProtocolError):
        DEFAULT_OPERATION_REGISTRY.validate_start(
            replace(original, inputs=(original.inputs[0], image))
        )
    plan = replace(original.inputs[0], content_kind=IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND)
    current = replace(original, operation_version=2, inputs=(plan, image))
    DEFAULT_OPERATION_REGISTRY.validate_start(current)
    with pytest.raises(ProtocolError):
        DEFAULT_OPERATION_REGISTRY.validate_start(replace(current, operation_version=1))
    with pytest.raises(ProtocolError):
        DEFAULT_OPERATION_REGISTRY.validate_start(replace(current, operation_version=3))


@pytest.mark.parametrize("format", ["JPEG", "PNG"])
@pytest.mark.parametrize("orientation", list(range(1, 9)))
def test_image_pdf_preserves_orientation_alpha_hashes_and_cleans_scratch(
    tmp_path: Path,
    format: str,
    orientation: int,
) -> None:
    source_path = tmp_path / "synthetic-image.bin"
    original = Image.new("RGB" if format == "JPEG" else "RGBA", (80, 40))
    original.paste((220, 20, 10) if format == "JPEG" else (220, 20, 10, 96), (0, 0, 40, 40))
    original.paste((20, 30, 230) if format == "JPEG" else (20, 30, 230, 255), (40, 0, 80, 40))
    exif = Image.Exif()
    exif[274] = orientation
    original.save(source_path, format=format, exif=exif)
    original.close()
    before = source_path.read_bytes()
    source = _image_source(
        source_path,
        PdfExportSourceMediaType.JPEG if format == "JPEG" else PdfExportSourceMediaType.PNG,
    )
    plan = _image_plan(source, PdfExportRotation.DEGREES_90)
    output = tmp_path / "verified.pdf"
    limits = replace(
        _limits(), image_normalization=_image_limits(), maximum_image_scratch_bytes=12_000_000
    )
    report = PdfExportAdapter(qpdf_checker=_CleanChecker()).assemble_and_verify(
        plan,
        {source.source_id: source_path},
        output,
        limits,
        _control(),
    )
    assert report.page_count == 1
    assert source_path.read_bytes() == before
    assert {path.name for path in tmp_path.iterdir()} == {source_path.name, output.name}
    with pikepdf.Pdf.open(output) as document:
        page = document.pages[0]
        assert page.Rotate == 90
        image = next(value for _key, value in page.Resources.XObject.items())
        expected = (40, 80) if orientation >= 5 else (80, 40)
        assert (int(image.Width), int(image.Height)) == expected
        assert image.ColorSpace == pikepdf.Name.DeviceRGB
        assert "/SMask" not in image
        with Image.open(source_path) as encoded:
            with ImageOps.exif_transpose(encoded) as oriented:
                with oriented.convert("RGBA") as rgba:
                    with Image.new("RGBA", rgba.size, "white") as white:
                        white.alpha_composite(rgba)
                        with white.convert("RGB") as expected_rgb:
                            assert image.read_bytes() == expected_rgb.tobytes()


def test_image_pdf_capacity_failure_leaves_source_and_no_candidate(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.png"
    image = Image.new("RGB", (20, 20), "blue")
    image.save(path)
    image.close()
    source = _image_source(path, PdfExportSourceMediaType.PNG)
    plan = _image_plan(source)
    output = tmp_path / "rejected.pdf"
    image_limits = replace(_image_limits(), maximum_source_pixels=399)
    limits = replace(
        _limits(), image_normalization=image_limits, maximum_image_scratch_bytes=12_000_000
    )
    with pytest.raises(PdfExportError) as caught:
        PdfExportAdapter(qpdf_checker=_CleanChecker()).assemble_and_verify(
            plan,
            {source.source_id: path},
            output,
            limits,
            _control(),
        )
    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert set(tmp_path.iterdir()) == {path}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256


@pytest.mark.parametrize("failure", ["cancel", "render_mismatch"])
def test_image_pdf_failure_cleans_raster_candidate_and_can_retry(
    tmp_path: Path, failure: str
) -> None:
    path = tmp_path / "synthetic.png"
    with Image.new("RGBA", (20, 20), (10, 80, 160, 96)) as image:
        image.save(path)
    source = _image_source(path, PdfExportSourceMediaType.PNG)
    plan = _image_plan(source)
    output = tmp_path / "verified.pdf"
    limits = replace(
        _limits(), image_normalization=_image_limits(), maximum_image_scratch_bytes=12_000_000
    )
    control = (
        _control(cancellation_probe=lambda: bool(tuple(tmp_path.glob("*.rgba"))))
        if failure == "cancel"
        else _control()
    )
    adapter = (
        PdfExportAdapter(qpdf_checker=_CleanChecker())
        if failure == "cancel"
        else PdfExportAdapter(qpdf_checker=_CleanChecker(), renderer=_MismatchingRenderer())
    )
    with pytest.raises(PdfExportError) as caught:
        adapter.assemble_and_verify(plan, {source.source_id: path}, output, limits, control)
    assert caught.value.code is (
        PdfExportErrorCode.CANCELLED
        if failure == "cancel"
        else PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    )
    assert set(tmp_path.iterdir()) == {path}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256
    PdfExportAdapter(qpdf_checker=_CleanChecker()).assemble_and_verify(
        plan, {source.source_id: path}, output, limits, _control()
    )
    assert set(tmp_path.iterdir()) == {path, output}


def test_running_image_pdf_bytes_reject_before_next_append_and_clean_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = []
    paths = {}
    for index in range(2):
        path = tmp_path / f"synthetic-{index}.png"
        with Image.frombytes("RGB", (32, 20), random.Random(index).randbytes(32 * 20 * 3)) as image:
            image.save(path)
        source = _image_source(path, PdfExportSourceMediaType.PNG)
        sources.append(source)
        paths[source.source_id] = path
    base = _image_plan(sources[0])
    plan = PdfExportPlan(
        case_id=base.case_id,
        manifest_revision=base.manifest_revision,
        manifest_sha256=base.manifest_sha256,
        manifest_cas_token=base.manifest_cas_token,
        sources=tuple(sources),
        pages=tuple(
            PdfExportPage(index, uuid4(), source.source_id, 0, PdfExportRotation.DEGREES_0)
            for index, source in enumerate(sources)
        ),
        profile=IMAGE_PDF_EXPORT_PROFILE,
        plan_version=2,
    )
    original_open = adapter_module._strict_open
    opened_pages = 0

    def count_open(stream: BinaryIO) -> pikepdf.Pdf:
        nonlocal opened_pages
        opened_pages += 1
        return original_open(stream)

    monkeypatch.setattr(adapter_module, "_strict_open", count_open)
    limits = replace(
        _limits(maximum_temporary_bytes=4096),
        image_normalization=_image_limits(),
        maximum_image_scratch_bytes=12_000_000,
    )
    with pytest.raises(PdfExportError) as caught:
        PdfExportAdapter(qpdf_checker=_CleanChecker()).assemble_and_verify(
            plan, paths, tmp_path / "rejected.pdf", limits, _control()
        )
    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "export.image_output_bytes"
    assert opened_pages == 1
    assert set(tmp_path.iterdir()) == set(paths.values())
    for source in sources:
        assert hashlib.sha256(paths[source.source_id].read_bytes()).hexdigest() == source.sha256
