"""Adversarial PDF, privacy, and resource evidence for PWS-0303."""

from __future__ import annotations

import hashlib
import logging
import traceback
import zlib
from collections.abc import Buffer, Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from importlib import metadata as importlib_metadata
from io import BytesIO
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

import fpdf
import pikepdf
import pytest
from fpdf import FPDF

import paperworkshell.pdf.overlay_renderer as overlay_adapter
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayKind,
    OverlayPayload,
    OverlayRecord,
    RgbaColor,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OverlayPageGeometry,
    OverlayPageRenderRequest,
    OverlayPageRenderResult,
    OverlayPdfDestination,
    OverlayPdfPlacementMatrix,
    OverlayRasterAsset,
    OverlayRasterSource,
    OverlayRenderControl,
    OverlayRenderError,
    OverlayRenderErrorCode,
    OverlayRenderLimits,
)
from paperworkshell.pdf.overlay_renderer import FpdfOverlayRenderer

PAGE_ID: Final = UUID("30000000-0000-4000-8000-000000000303")
ASSET_ID: Final = UUID("40000000-0000-4000-8000-000000000303")
SECOND_ASSET_ID: Final = UUID("40000000-0000-4000-8000-000000000304")
FULL_PAGE: Final = NormalizedPageBox.full_page()
PRIVATE_TEXT_CANARY: Final = "SYNTHETIC-PRIVATE-FORM-TEXT-CANARY"
PRIVATE_ASSET_CANARY: Final = "SYNTHETIC-PRIVATE-ASSET-BYTES-CANARY"
PRIVATE_PATH_CANARY: Final = r"C:\Private Applicant\signed-form.pdf"

_PROHIBITED_KEYS: Final = frozenset(
    {
        "/A",
        "/AA",
        "/AcroForm",
        "/AF",
        "/Annots",
        "/ByteRange",
        "/EmbeddedFiles",
        "/EF",
        "/JavaScript",
        "/JS",
        "/Metadata",
        "/Names",
        "/OpenAction",
        "/Perms",
        "/SubmitForm",
        "/URI",
        "/XFA",
    }
)
_PROHIBITED_TYPES: Final = frozenset({"/Action", "/EmbeddedFile", "/Filespec", "/Sig"})
_PROHIBITED_SUBTYPES: Final = frozenset(
    {
        "/FileAttachment",
        "/Link",
        "/Movie",
        "/Redact",
        "/RichMedia",
        "/Screen",
        "/Sound",
        "/Widget",
    }
)
_PROHIBITED_ACTIONS: Final = frozenset(
    {"/GoToR", "/ImportData", "/JavaScript", "/Launch", "/SubmitForm", "/URI"}
)


@dataclass(frozen=True, slots=True)
class _Control:
    cancelled: bool = False
    timed_out: bool = False

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.timed_out


class _MalformedControl:
    def cancellation_requested(self) -> object:
        return "not-a-boolean"

    def deadline_expired(self) -> bool:
        return False


class _CanaryControl:
    def cancellation_requested(self) -> bool:
        raise RuntimeError(PRIVATE_PATH_CANARY)

    def deadline_expired(self) -> bool:
        return False


def _private_overlay_error() -> OverlayRenderError:
    error = OverlayRenderError(OverlayRenderErrorCode.MODEL_INVALID, "overlay.poisoned")
    error.safe_message = PRIVATE_PATH_CANARY
    error.args = (PRIVATE_PATH_CANARY,)
    return error


class _InjectedErrorControl:
    def cancellation_requested(self) -> bool:
        raise _private_overlay_error()

    def deadline_expired(self) -> bool:
        return False


class _RasterSource(BytesIO):
    def __init__(self, metadata: OverlayRasterAsset, value: bytes) -> None:
        super().__init__(value)
        self._metadata = metadata

    @property
    def metadata(self) -> OverlayRasterAsset:
        return self._metadata


class _CanaryReadFailure(_RasterSource):
    def read(self, _size: int | None = -1, /) -> bytes:
        raise OSError(PRIVATE_ASSET_CANARY)


class _InjectedErrorRasterSource(_RasterSource):
    def read(self, _size: int | None = -1, /) -> bytes:
        raise _private_overlay_error()


class _InjectedLookupRasterSource(_RasterSource):
    def __getattribute__(self, name: str) -> object:
        if name == "read":
            raise _private_overlay_error()
        return super().__getattribute__(name)


class _MemoryReadFailure(_RasterSource):
    def read(self, _size: int | None = -1, /) -> bytes:
        raise MemoryError(PRIVATE_ASSET_CANARY)


class _OversizedRead(_RasterSource):
    def read(self, size: int | None = -1, /) -> bytes:
        assert isinstance(size, int) and size > 0
        return b"x" * (size + 1)


class _RestoreFailure(_RasterSource):
    def __init__(self, metadata: OverlayRasterAsset, value: bytes, original: int) -> None:
        super().__init__(metadata, value)
        self._original = original
        self._armed = False
        super().seek(original)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self._armed and whence == 0 and offset == self._original:
            raise OSError(PRIVATE_ASSET_CANARY)
        result = super().seek(offset, whence)
        if whence == 0 and offset == 0:
            self._armed = True
        return result


class _HostileMapping(Mapping[UUID, OverlayRasterSource]):
    def __iter__(self) -> Iterator[UUID]:
        raise RuntimeError(PRIVATE_ASSET_CANARY)

    def __len__(self) -> int:
        return 1

    def __getitem__(self, _key: UUID) -> OverlayRasterSource:
        raise RuntimeError(PRIVATE_ASSET_CANARY)


class _ChangingMetadataRasterSource(BytesIO):
    def __init__(
        self,
        first: OverlayRasterAsset,
        later: OverlayRasterAsset,
        value: bytes,
    ) -> None:
        super().__init__(value)
        self._first = first
        self._later = later
        self.metadata_calls = 0

    @property
    def metadata(self) -> OverlayRasterAsset:
        self.metadata_calls += 1
        return self._first if self.metadata_calls == 1 else self._later


class _ChangingInventoryMapping(Mapping[UUID, OverlayRasterSource]):
    def __init__(
        self,
        sources: Mapping[UUID, OverlayRasterSource],
        first_inventory: tuple[UUID, ...],
        later_inventory: tuple[UUID, ...],
    ) -> None:
        self._sources = sources
        self._first_inventory = first_inventory
        self._later_inventory = later_inventory
        self.iteration_calls = 0
        self.getitem_calls: list[UUID] = []

    def __iter__(self) -> Iterator[UUID]:
        self.iteration_calls += 1
        inventory = self._first_inventory if self.iteration_calls == 1 else self._later_inventory
        return iter(inventory)

    def __len__(self) -> int:
        return len(self._first_inventory)

    def __getitem__(self, key: UUID) -> OverlayRasterSource:
        self.getitem_calls.append(key)
        return self._sources[key]


class _CanaryDestination(BytesIO):
    def write(self, _data: Buffer, /) -> int:
        raise OSError(PRIVATE_PATH_CANARY)

    def flush(self) -> None:
        raise OSError(PRIVATE_PATH_CANARY)


class _InjectedErrorDestination(BytesIO):
    def write(self, _data: Buffer, /) -> int:
        raise _private_overlay_error()


class _InjectedLookupDestination(BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self._write_lookups = 0

    def __getattribute__(self, name: str) -> object:
        if name == "write":
            lookups = super().__getattribute__("_write_lookups") + 1
            super().__setattr__("_write_lookups", lookups)
            if lookups > 1:
                raise _private_overlay_error()
        return super().__getattribute__(name)


class _PartialFailureDestination(BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self._write_calls = 0

    def write(self, data: Buffer, /) -> int:
        self._write_calls += 1
        if self._write_calls > 1:
            raise OSError(PRIVATE_PATH_CANARY)
        view = memoryview(data)
        return super().write(view[: min(17, len(view))])


class _RollbackFailureDestination(_PartialFailureDestination):
    def truncate(self, _size: int | None = None, /) -> int:
        raise OSError(PRIVATE_PATH_CANARY)


def _overlay_id(index: int) -> UUID:
    return UUID(f"60000000-0000-4000-8000-{index:012x}")


def _record(index: int, payload: OverlayPayload) -> OverlayRecord:
    return OverlayRecord(
        _overlay_id(index),
        PAGE_ID,
        NormalizedPageBox(50_000, 50_000 + index * 100_000, 900_000, 80_000),
        index,
        payload,
    )


def _asset_record(asset_id: UUID) -> OverlayAssetRecord:
    return OverlayAssetRecord(
        asset_id,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{asset_id}.bin",
        OverlayAssetMediaType.PNG,
        16,
        "a" * 64,
    )


def _request(
    records: tuple[OverlayRecord, ...],
    *,
    assets: tuple[OverlayAssetRecord, ...] = (),
) -> OverlayPageRenderRequest:
    collection = OverlayCollection(
        assets,
        records,
        known_page_ids=(PAGE_ID,),
        active_page_ids=(PAGE_ID,),
    )
    geometry = OverlayPageGeometry(
        612.0,
        792.0,
        OverlayPdfPlacementMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
    )
    return OverlayPageRenderRequest.from_collection(
        collection,
        PAGE_ID,
        geometry=geometry,
        active_crop=FULL_PAGE,
    )


def _mark_request() -> OverlayPageRenderRequest:
    return _request(
        (
            _record(
                1,
                MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#102030ff")),
            ),
        )
    )


def _text_request(value: str = "Résumé Ελληνικά Русский हिन्दी") -> OverlayPageRenderRequest:
    return _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    value,
                    DEFAULT_FONT_TOKEN,
                    14,
                    RgbaColor("#102030ff"),
                ),
            ),
        )
    )


def _raster(
    asset_id: UUID = ASSET_ID,
    *,
    pixels: bytes | None = None,
) -> tuple[OverlayRasterAsset, bytes]:
    value = (
        bytes((255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 255, 255, 255, 0, 64))
        if pixels is None
        else pixels
    )
    metadata = OverlayRasterAsset(
        asset_id,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=16,
        pixel_sha256=hashlib.sha256(value).hexdigest(),
    )
    return metadata, value


def _signature_request(
    asset_ids: tuple[UUID, ...] = (ASSET_ID,),
) -> OverlayPageRenderRequest:
    return _request(
        tuple(
            _record(index, SignatureImageOverlayPayload(asset_id))
            for index, asset_id in enumerate(asset_ids, 1)
        ),
        assets=tuple(_asset_record(asset_id) for asset_id in asset_ids),
    )


def _limits() -> OverlayRenderLimits:
    return OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _render(
    request: OverlayPageRenderRequest,
    *,
    raster_sources: Mapping[UUID, OverlayRasterSource] | None = None,
    destination: OverlayPdfDestination | None = None,
    limits: OverlayRenderLimits | None = None,
    control: OverlayRenderControl | None = None,
) -> tuple[bytes, OverlayPageRenderResult]:
    selected_destination = BytesIO() if destination is None else destination
    result = FpdfOverlayRenderer().render(
        request,
        {} if raster_sources is None else raster_sources,
        selected_destination,
        _limits() if limits is None else limits,
        _Control() if control is None else control,
    )
    data = selected_destination.getvalue() if isinstance(selected_destination, BytesIO) else b""
    return data, result


def _rendered_exception(error: BaseException) -> str:
    return f"{error!s}\n{error!r}\n{error.args!r}\n" + "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )


def _assert_sanitized_failure(
    error: OverlayRenderError,
    caplog: pytest.LogCaptureFixture,
    *canaries: str,
) -> None:
    rendered = _rendered_exception(error) + caplog.text
    assert error.__cause__ is None
    assert error.__context__ is None
    for canary in canaries:
        assert canary not in rendered


def _malicious_pdf(key: str) -> bytes:
    destination = BytesIO()
    with pikepdf.new() as document:
        page = document.add_blank_page(page_size=(612.0, 792.0))
        page.obj[pikepdf.Name(key)] = pikepdf.Array()
        document.save(destination, deterministic_id=True)
    return destination.getvalue()


def _malicious_nested_direct_pdf(location: str) -> bytes:
    destination = BytesIO()
    with pikepdf.new() as document:
        page = document.add_blank_page(page_size=(612.0, 792.0))
        action = pikepdf.Dictionary(
            {
                "/S": pikepdf.Name("/JavaScript"),
                "/JS": "synthetic-script",
            }
        )
        if location == "resource":
            page.obj[pikepdf.Name("/Resources")] = pikepdf.Dictionary(
                {
                    "/ExtGState": pikepdf.Dictionary(
                        {
                            "/GS1": pikepdf.Dictionary(
                                {"/AA": action},
                            )
                        }
                    )
                }
            )
        else:
            document.Root[pikepdf.Name("/Outlines")] = pikepdf.Dictionary(
                {
                    "/First": pikepdf.Dictionary(
                        {
                            "/Title": "Synthetic outline",
                            "/A": action,
                        }
                    )
                }
            )
        document.save(destination, deterministic_id=True)
    return destination.getvalue()


def _output_with_page_geometry(
    generated: bytearray,
    mutation: str,
) -> bytearray:
    destination = BytesIO()
    with pikepdf.Pdf.open(BytesIO(generated), attempt_recovery=False) as document:
        page = document.pages[0].obj
        parent = page["/Parent"]
        if mutation == "rotate":
            parent["/Rotate"] = 90
        elif mutation == "user-unit":
            parent["/UserUnit"] = 2
        elif mutation == "crop":
            parent["/CropBox"] = pikepdf.Array((0, 0, 306, 792))
        elif mutation == "crop-delta":
            parent["/CropBox"] = pikepdf.Array((0, 0, Decimal("612.01"), 792))
        elif mutation == "media-delta":
            page["/MediaBox"] = pikepdf.Array((0, 0, Decimal("612.01"), 792))
        else:
            page[f"/{mutation}"] = pikepdf.Array((0, 0, 612, 792))
        document.save(destination, deterministic_id=True)
    return bytearray(destination.getvalue())


def test_verified_output_has_no_scripts_actions_attachments_annotations_or_metadata() -> None:
    data, _result = _render(_text_request())

    with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
        assert document.is_encrypted is False
        assert "/Encrypt" not in document.trailer
        assert "/Info" not in document.trailer
        assert len(document.pages) == 1
        for value in document.objects:
            if not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            keys = {str(key) for key in value.keys()}
            assert keys.isdisjoint(_PROHIBITED_KEYS)
            assert str(value.get("/Type", "")) not in _PROHIBITED_TYPES
            assert str(value.get("/Subtype", "")) not in _PROHIBITED_SUBTYPES
            assert str(value.get("/S", "")) not in _PROHIBITED_ACTIONS
            assert str(value.get("/FT", "")) != "/Sig"


@pytest.mark.parametrize("key", ("/AA", "/AF", "/Annots"))
def test_generated_pdf_with_prohibited_structures_is_rejected_before_destination_write(
    key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malicious = _malicious_pdf(key)
    destination = BytesIO()

    def malicious_output(_document: object) -> bytearray:
        return bytearray(malicious)

    monkeypatch.setattr(FPDF, "output", malicious_output)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED
    assert raised.value.context == "overlay.pdf_prohibited"
    assert destination.getvalue() == b""


@pytest.mark.parametrize("location", ("resource", "outline"))
def test_nested_direct_actions_are_rejected_before_destination_write(
    location: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malicious = _malicious_nested_direct_pdf(location)
    with pikepdf.Pdf.open(BytesIO(malicious), attempt_recovery=False) as document:
        if location == "resource":
            nested = document.pages[0].obj["/Resources"]["/ExtGState"]["/GS1"]
        else:
            nested = document.Root["/Outlines"]["/First"]
        assert nested.is_indirect is False
        assert any(key in nested for key in ("/A", "/AA"))
        assert all(
            not (
                isinstance(value, (pikepdf.Dictionary, pikepdf.Stream))
                and {str(key) for key in value.keys()} & {"/A", "/AA", "/JS"}
            )
            for value in document.objects
        )

    destination = BytesIO()

    def malicious_output(_document: object) -> bytearray:
        return bytearray(malicious)

    monkeypatch.setattr(FPDF, "output", malicious_output)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED
    assert raised.value.context == "overlay.pdf_prohibited"
    assert destination.getvalue() == b""


@pytest.mark.parametrize(
    "mutation",
    (
        "rotate",
        "user-unit",
        "crop",
        "crop-delta",
        "media-delta",
        "BleedBox",
        "TrimBox",
        "ArtBox",
    ),
)
def test_generated_pdf_with_unexpected_page_geometry_is_rejected_before_write(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_output = cast(Callable[..., bytearray], FPDF.output)

    def output_with_geometry(self: FPDF, *args: object, **kwargs: object) -> bytearray:
        return _output_with_page_geometry(real_output(self, *args, **kwargs), mutation)

    monkeypatch.setattr(FPDF, "output", output_with_geometry)
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED
    assert raised.value.context == "overlay.pdf_geometry"
    assert destination.getvalue() == b""


def test_recursive_inspection_charges_direct_graph_nodes_to_pdf_object_budget() -> None:
    with pikepdf.new() as document:
        page = document.add_blank_page(page_size=(612.0, 792.0))
        page.obj["/Resources"] = pikepdf.Dictionary(
            {
                "/ExtGState": pikepdf.Dictionary(
                    {
                        "/GS1": pikepdf.Dictionary(
                            {"/CA": 1, "/ca": 1},
                        )
                    }
                )
            }
        )
        limits = replace(
            _limits(),
            maximum_pdf_objects=len(document.objects) + 1,
        )

        with pytest.raises(OverlayRenderError) as raised:
            overlay_adapter._inspect_objects(document, limits, _Control())

    assert raised.value.code is OverlayRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "overlay.pdf_objects"


def test_recursive_inspection_terminates_on_indirect_cycles() -> None:
    with pikepdf.new() as document:
        document.add_blank_page(page_size=(612.0, 792.0))
        first = document.make_indirect(pikepdf.Dictionary())
        second = document.make_indirect(pikepdf.Dictionary())
        first["/Next"] = second
        second["/Next"] = first
        document.Root["/SyntheticCycle"] = first

        limits = replace(_limits(), maximum_pdf_objects=32)
        overlay_adapter._inspect_objects(document, limits, _Control())


def test_unexpected_image_xobject_is_rejected_before_destination_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_output = cast(Callable[..., bytearray], FPDF.output)

    def output_with_extra_image(self: FPDF, *args: object, **kwargs: object) -> bytearray:
        generated = real_output(self, *args, **kwargs)
        destination = BytesIO()
        with pikepdf.Pdf.open(BytesIO(generated), attempt_recovery=False) as document:
            unexpected = document.make_stream(b"\x00\x00\x00")
            unexpected["/Type"] = pikepdf.Name("/XObject")
            unexpected["/Subtype"] = pikepdf.Name("/Image")
            unexpected["/Width"] = 1
            unexpected["/Height"] = 1
            unexpected["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
            unexpected["/BitsPerComponent"] = 8
            resources = document.pages[0].obj["/Resources"]
            xobjects = resources["/XObject"]
            xobjects["/Unexpected"] = unexpected
            document.save(destination, deterministic_id=True)
        return bytearray(destination.getvalue())

    monkeypatch.setattr(FPDF, "output", output_with_extra_image)
    metadata, pixels = _raster()
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources={ASSET_ID: _RasterSource(metadata, pixels)},
            destination=destination,
        )

    assert raised.value.code is OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED
    assert raised.value.context == "overlay.pdf_images"
    assert destination.getvalue() == b""


def test_duplicate_signature_pixels_share_one_exact_image_resource() -> None:
    first_metadata, pixels = _raster(ASSET_ID)
    second_metadata, _ = _raster(SECOND_ASSET_ID, pixels=pixels)

    data, result = _render(
        _signature_request((ASSET_ID, SECOND_ASSET_ID)),
        raster_sources={
            ASSET_ID: _RasterSource(first_metadata, pixels),
            SECOND_ASSET_ID: _RasterSource(second_metadata, pixels),
        },
    )

    assert result.image_count == 2
    with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
        xobjects = document.pages[0].obj["/Resources"]["/XObject"]
        assert len(xobjects) == 1
        assert all(
            isinstance(xobjects[key], pikepdf.Stream)
            and str(xobjects[key].get("/Subtype", "")) == "/Image"
            for key in xobjects.keys()
        )


def test_failed_security_reopen_is_closed_sanitized_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_open = cast(Callable[..., object], pikepdf.Pdf.open)

    class _FailingSecondOpen:
        calls = 0

        @classmethod
        def open(cls, *args: object, **kwargs: object) -> object:
            cls.calls += 1
            if cls.calls == 2:
                raise pikepdf.PdfError(PRIVATE_PATH_CANARY)
            return real_open(*args, **kwargs)

    monkeypatch.setattr(pikepdf, "Pdf", _FailingSecondOpen)
    destination = BytesIO()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED
    assert raised.value.context == "overlay.pdf_reopen"
    assert _FailingSecondOpen.calls == 2
    assert destination.getvalue() == b""
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


def test_private_text_is_absent_from_unsupported_glyph_failure_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_text_request(PRIVATE_TEXT_CANARY + "-😀"))

    assert raised.value.code is OverlayRenderErrorCode.GLYPH_UNSUPPORTED
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_TEXT_CANARY)


def test_fpdf_output_child_warning_is_scoped_and_logger_state_is_restored(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = PRIVATE_TEXT_CANARY + "-FPDF-OUTPUT"
    real_output = cast(Callable[..., bytearray], FPDF.output)

    def output_with_canary(self: FPDF, *args: object, **kwargs: object) -> bytearray:
        logging.getLogger("fpdf.output").warning(canary)
        return real_output(self, *args, **kwargs)

    monkeypatch.setattr(FPDF, "output", output_with_canary)
    caplog.set_level(logging.DEBUG)
    loggers = tuple(logging.getLogger(name) for name in ("fpdf", "fpdf.output", "fontTools.subset"))
    states = tuple(
        (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
        for logger in loggers
    )

    data, _result = _render(_mark_request())

    assert data.startswith(b"%PDF-")
    assert canary not in caplog.text
    assert states == tuple(
        (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
        for logger in loggers
    )


def test_successful_unicode_subsetting_does_not_log_text_or_glyph_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "PWS-SUPPORTED-PRIVATE-É-Ж"
    caplog.set_level(logging.DEBUG)

    data, _result = _render(_text_request(canary))

    assert data.startswith(b"%PDF-")
    captured = caplog.text.casefold()
    assert canary.casefold() not in captured
    assert "eacute" not in captured
    assert "uni0416" not in captured
    assert not any(
        record.name == "fontTools.subset" or record.name.startswith("fontTools.subset.")
        for record in caplog.records
    )


def test_source_failure_canary_is_sanitized_and_position_is_restored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metadata, pixels = _raster()
    source = _CanaryReadFailure(metadata, pixels)
    source.seek(3)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_signature_request(), raster_sources={ASSET_ID: source})

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_read"
    assert source.tell() == 3
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_ASSET_CANARY)


@pytest.mark.parametrize(
    "source_type",
    (_InjectedErrorRasterSource, _InjectedLookupRasterSource),
)
def test_injected_overlay_error_from_raster_source_is_remapped_and_sanitized(
    source_type: type[_RasterSource],
    caplog: pytest.LogCaptureFixture,
) -> None:
    metadata, pixels = _raster()
    source = source_type(metadata, pixels)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_signature_request(), raster_sources={ASSET_ID: source})

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_read"
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


def test_source_memory_failure_restores_position_and_maps_to_resource_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metadata, pixels = _raster()
    source = _MemoryReadFailure(metadata, pixels)
    source.seek(3)
    destination = BytesIO()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources={ASSET_ID: source},
            destination=destination,
        )

    assert raised.value.code is OverlayRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "overlay.memory"
    assert source.tell() == 3
    assert destination.getvalue() == b""
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_ASSET_CANARY)


def test_source_cannot_return_more_bytes_than_the_bounded_read_request() -> None:
    metadata, pixels = _raster()
    source = _OversizedRead(metadata, pixels)
    source.seek(3)
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources={ASSET_ID: source},
            destination=destination,
        )

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_read"
    assert source.tell() == 3
    assert destination.getvalue() == b""


def test_changed_source_identity_fails_closed_and_restores_the_caller_position() -> None:
    metadata, pixels = _raster()
    changed = pixels[:-1] + bytes((pixels[-1] ^ 0xFF,))
    source = _RasterSource(metadata, changed)
    source.seek(5)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_signature_request(), raster_sources={ASSET_ID: source})

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_identity"
    assert source.tell() == 5


def test_source_restore_failure_has_priority_and_does_not_disclose_asset_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    metadata, pixels = _raster()
    source = _RestoreFailure(metadata, pixels, original=4)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_signature_request(), raster_sources={ASSET_ID: source})

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_restore"
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_ASSET_CANARY)


def test_destination_write_failure_is_sanitized_and_never_returns_a_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=_CanaryDestination())

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_write"
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


@pytest.mark.parametrize(
    "destination_type",
    (_InjectedErrorDestination, _InjectedLookupDestination),
)
def test_injected_overlay_error_from_destination_is_remapped_and_sanitized(
    destination_type: type[BytesIO],
    caplog: pytest.LogCaptureFixture,
) -> None:
    destination = destination_type()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_write"
    assert destination.getvalue() == b""
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


def test_partial_destination_failure_rolls_the_operation_sink_back_to_empty() -> None:
    destination = _PartialFailureDestination()

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_write"
    assert destination.getvalue() == b""


def test_rollback_failure_never_returns_a_publishable_operation_sink(
    caplog: pytest.LogCaptureFixture,
) -> None:
    destination = _RollbackFailureDestination()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_write"
    assert destination.getvalue()
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


def test_digest_finalization_failure_discards_the_written_operation_sink(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingDigest:
        def update(self, _data: Buffer, /) -> None:
            return None

        def hexdigest(self) -> str:
            raise RuntimeError(PRIVATE_PATH_CANARY)

    monkeypatch.setattr(hashlib, "sha256", _FailingDigest)
    destination = BytesIO()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        overlay_adapter._write_exact_pdf(
            destination,
            b"verified-overlay-pdf",
            _limits(),
            _Control(),
        )

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_write"
    assert destination.getvalue() == b""
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


@pytest.mark.parametrize("position", (0, 6))
def test_nonempty_destination_is_rejected_without_mutating_existing_bytes(position: int) -> None:
    destination = BytesIO(b"PREFIX")
    destination.seek(position)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.DESTINATION_FAILED
    assert raised.value.context == "overlay.destination_state"
    assert destination.getvalue() == b"PREFIX"


def test_result_construction_failure_rolls_the_operation_sink_back_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_result(**_values: object) -> object:
        raise RuntimeError(PRIVATE_PATH_CANARY)

    monkeypatch.setattr(overlay_adapter, "OverlayPageRenderResult", fail_result)
    destination = BytesIO()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.INTERNAL_ERROR
    assert raised.value.context == "overlay.adapter"
    assert destination.getvalue() == b""
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


@pytest.mark.parametrize(
    ("render_request", "limit_changes", "expected_code", "expected_context"),
    (
        (
            _mark_request(),
            {"maximum_output_bytes": 1},
            OverlayRenderErrorCode.LIMIT_EXCEEDED,
            "overlay.output_bytes",
        ),
        (
            _mark_request(),
            {"maximum_pdf_objects": 1},
            OverlayRenderErrorCode.LIMIT_EXCEEDED,
            "overlay.pdf_objects",
        ),
        (
            _text_request("AB"),
            {"maximum_total_text_code_points": 1},
            OverlayRenderErrorCode.LIMIT_EXCEEDED,
            "overlay.text_count",
        ),
    ),
)
def test_output_object_and_text_limits_fail_before_destination_write(
    render_request: OverlayPageRenderRequest,
    limit_changes: dict[str, int],
    expected_code: OverlayRenderErrorCode,
    expected_context: str,
) -> None:
    destination = BytesIO()
    limits = replace(_limits(), **limit_changes)

    with pytest.raises(OverlayRenderError) as raised:
        _render(render_request, destination=destination, limits=limits)

    assert raised.value.code is expected_code
    assert raised.value.context == expected_context
    assert destination.getvalue() == b""


def test_raster_dimension_limit_fails_before_source_read_or_destination_write() -> None:
    metadata, pixels = _raster()
    source = _CanaryReadFailure(metadata, pixels)
    destination = BytesIO()
    limits = replace(_limits(), maximum_raster_width_pixels=1)

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources={ASSET_ID: source},
            destination=destination,
            limits=limits,
        )

    assert raised.value.code is OverlayRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "overlay.raster"
    assert destination.getvalue() == b""


def test_mutable_metadata_cannot_bypass_per_raster_width_limit() -> None:
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))
    digest = hashlib.sha256(pixels).hexdigest()
    validated = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=1,
        height_pixels=2,
        row_stride_bytes=4,
        byte_count=8,
        pixel_sha256=digest,
    )
    later_oversized = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        byte_count=8,
        pixel_sha256=digest,
    )
    source = _ChangingMetadataRasterSource(validated, later_oversized, pixels)
    limits = replace(_limits(), maximum_raster_width_pixels=1)

    data, result = _render(
        _signature_request(),
        raster_sources={ASSET_ID: source},
        limits=limits,
    )

    assert data.startswith(b"%PDF-")
    assert source.metadata_calls == 1
    painted = result.placements[0].painted_box
    assert painted is not None
    assert painted.width < painted.height


def test_mutable_metadata_cannot_bypass_raster_byte_budgets() -> None:
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255))
    validated = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        byte_count=4,
        pixel_sha256=hashlib.sha256(pixels[:4]).hexdigest(),
    )
    later_oversized = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=1,
        height_pixels=2,
        row_stride_bytes=4,
        byte_count=8,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    source = _ChangingMetadataRasterSource(validated, later_oversized, pixels)
    destination = BytesIO()
    limits = replace(
        _limits(),
        maximum_raster_bytes=4,
        maximum_total_raster_bytes=4,
    )

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources={ASSET_ID: source},
            destination=destination,
            limits=limits,
        )

    assert raised.value.code is OverlayRenderErrorCode.ASSET_CHANGED
    assert raised.value.context == "overlay.raster_read"
    assert source.metadata_calls == 1
    assert destination.getvalue() == b""


def test_mutable_mapping_is_snapshotted_once_before_later_unreferenced_extra() -> None:
    metadata, pixels = _raster()
    extra_metadata, extra_pixels = _raster(SECOND_ASSET_ID)
    mapping = _ChangingInventoryMapping(
        {
            ASSET_ID: _RasterSource(metadata, pixels),
            SECOND_ASSET_ID: _CanaryReadFailure(extra_metadata, extra_pixels),
        },
        (ASSET_ID,),
        (ASSET_ID, SECOND_ASSET_ID),
    )

    data, _result = _render(_signature_request(), raster_sources=mapping)

    assert data.startswith(b"%PDF-")
    assert mapping.iteration_calls == 1
    assert mapping.getitem_calls == [ASSET_ID]


def test_unreferenced_extra_in_initial_mapping_snapshot_fails_before_fetch() -> None:
    metadata, pixels = _raster()
    extra_metadata, extra_pixels = _raster(SECOND_ASSET_ID)
    mapping = _ChangingInventoryMapping(
        {
            ASSET_ID: _RasterSource(metadata, pixels),
            SECOND_ASSET_ID: _CanaryReadFailure(extra_metadata, extra_pixels),
        },
        (ASSET_ID, SECOND_ASSET_ID),
        (ASSET_ID,),
    )
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(
            _signature_request(),
            raster_sources=mapping,
            destination=destination,
        )

    assert raised.value.code is OverlayRenderErrorCode.ASSET_MISSING
    assert raised.value.context == "overlay.raster_sources"
    assert mapping.iteration_calls == 1
    assert mapping.getitem_calls == [ASSET_ID]
    assert destination.getvalue() == b""


@pytest.mark.parametrize(
    ("control", "code", "context"),
    (
        (_Control(cancelled=True), OverlayRenderErrorCode.CANCELLED, "overlay.cancelled"),
        (_Control(timed_out=True), OverlayRenderErrorCode.TIMED_OUT, "overlay.deadline"),
    ),
)
def test_cancellation_and_timeout_stop_before_destination_write(
    control: _Control,
    code: OverlayRenderErrorCode,
    context: str,
) -> None:
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), destination=destination, control=control)

    assert raised.value.code is code
    assert raised.value.context == context
    assert destination.getvalue() == b""


@pytest.mark.parametrize(
    "control",
    (_MalformedControl(), _CanaryControl(), _InjectedErrorControl()),
)
def test_malformed_control_probes_fail_closed_without_leaking_private_details(
    control: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), control=cast(OverlayRenderControl, control))

    assert raised.value.code is OverlayRenderErrorCode.INTERNAL_ERROR
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_PATH_CANARY)


def test_hostile_source_mapping_is_sanitized_unchained_and_unlogged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request(), raster_sources=_HostileMapping())

    assert raised.value.code is OverlayRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "overlay.raster_sources"
    _assert_sanitized_failure(raised.value, caplog, PRIVATE_ASSET_CANARY)


def test_renderer_output_is_byte_deterministic_for_identical_inputs() -> None:
    request = _text_request()

    first, first_result = _render(request)
    second, second_result = _render(request)

    assert first == second
    assert first_result == second_result
    assert hashlib.sha256(first).hexdigest() == first_result.pdf_sha256


def test_dependency_version_mismatch_is_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dependency_canary = "0.0.0-private-runtime-path-canary"
    monkeypatch.setattr(fpdf, "__version__", dependency_canary)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request())

    assert raised.value.code is OverlayRenderErrorCode.DEPENDENCY_UNSAFE
    assert raised.value.context == "overlay.runtime"
    _assert_sanitized_failure(raised.value, caplog, dependency_canary)


@pytest.mark.parametrize("dependency", ("pillow", "qpdf", "zlib"))
def test_output_dependency_version_mismatch_is_closed_and_sanitized(
    dependency: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dependency_canary = "0.0.0-private-output-dependency-canary"
    if dependency == "pillow":
        actual_version = importlib_metadata.version

        def mismatched_version(distribution_name: str) -> str:
            if distribution_name == "pillow":
                return dependency_canary
            return actual_version(distribution_name)

        monkeypatch.setattr(importlib_metadata, "version", mismatched_version)
    elif dependency == "qpdf":
        monkeypatch.setattr(pikepdf, "__libqpdf_version__", dependency_canary)
    else:
        monkeypatch.setattr(zlib, "ZLIB_RUNTIME_VERSION", dependency_canary)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(_mark_request())

    assert raised.value.code is OverlayRenderErrorCode.DEPENDENCY_UNSAFE
    assert raised.value.context == "overlay.runtime"
    _assert_sanitized_failure(raised.value, caplog, dependency_canary)


def test_font_digest_mismatch_fails_before_pdf_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overlay_adapter, "OVERLAY_FONT_RESOURCE_SHA256", "0" * 64)
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(_text_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.FONT_UNSAFE
    assert raised.value.context == "overlay.font_identity"
    assert destination.getvalue() == b""


@pytest.mark.parametrize("tampered_input", ("fonttools", "harfbuzz"))
def test_in_memory_fpdf_font_inputs_are_authenticated_before_registration(
    tampered_input: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_ttffont = cast(Any, overlay_adapter).TTFFont

    class _Blob:
        data = b"x" * overlay_adapter.OVERLAY_FONT_RESOURCE_SIZE

    class _Face:
        blob = _Blob()

    class _HarfBuzzFont:
        face = _Face()

    if tampered_input == "fonttools":

        def build_tampered_font(*args: object, **kwargs: object) -> object:
            loaded = cast(Any, real_ttffont)(*args, **kwargs)
            loaded.ttfont.reader.file = BytesIO(b"x" * overlay_adapter.OVERLAY_FONT_RESOURCE_SIZE)
            return loaded

        monkeypatch.setattr(overlay_adapter, "TTFFont", build_tampered_font)
    else:
        monkeypatch.setattr(overlay_adapter, "HarfBuzzFont", lambda _face: _HarfBuzzFont())

    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        _render(_text_request(), destination=destination)

    assert raised.value.code is OverlayRenderErrorCode.FONT_UNSAFE
    assert raised.value.context == "overlay.font_loaded"
    assert destination.getvalue() == b""


def test_font_path_mutation_after_snapshot_cannot_change_rendered_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_distribution = importlib_metadata.distribution(
        overlay_adapter.OVERLAY_FONT_RESOURCE_DISTRIBUTION
    )
    installed_path = Path(
        cast(Any, installed_distribution.locate_file(overlay_adapter.OVERLAY_FONT_RESOURCE_PATH))
    )
    original = installed_path.read_bytes()
    located_root = tmp_path / "site-packages"
    located_path = located_root / overlay_adapter.OVERLAY_FONT_RESOURCE_PATH
    located_path.parent.mkdir(parents=True)
    located_path.write_bytes(original)

    class _LocatedDistribution:
        version = overlay_adapter.EXPECTED_OCRMYPDF_VERSION

        def locate_file(self, resource: str) -> Path:
            return located_root if resource == "" else located_path

    real_distribution = importlib_metadata.distribution

    def located_distribution(name: str) -> object:
        if name == overlay_adapter.OVERLAY_FONT_RESOURCE_DISTRIBUTION:
            return _LocatedDistribution()
        return real_distribution(name)

    monkeypatch.setattr(
        importlib_metadata,
        "distribution",
        located_distribution,
    )
    baseline, _result = _render(_text_request())
    located_path.write_bytes(original)
    real_snapshot = overlay_adapter._load_authenticated_font_bytes

    def snapshot_then_mutate(
        control: OverlayRenderControl,
        limits: OverlayRenderLimits,
    ) -> bytes:
        snapshot = real_snapshot(control, limits)
        changed = bytearray(original)
        table_count = int.from_bytes(changed[4:6], "big")
        for index in range(table_count):
            entry = 12 + index * 16
            if changed[entry : entry + 4] == b"gasp":
                offset = int.from_bytes(changed[entry + 8 : entry + 12], "big")
                length = int.from_bytes(changed[entry + 12 : entry + 16], "big")
                changed[offset + length - 1] ^= 1
                break
        else:
            pytest.fail("approved font lacks the expected gasp table")
        located_path.write_bytes(changed)
        return snapshot

    monkeypatch.setattr(
        overlay_adapter,
        "_load_authenticated_font_bytes",
        snapshot_then_mutate,
    )

    after_mutation, _result = _render(_text_request())

    assert located_path.read_bytes() != original
    assert after_mutation == baseline


def test_final_font_resource_symlink_is_rejected_before_dereference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    located_root = tmp_path / "site-packages"
    located_root.mkdir()
    located_path = located_root / overlay_adapter.OVERLAY_FONT_RESOURCE_PATH
    resolved_paths: list[Path] = []
    real_resolve = Path.resolve

    class _LocatedDistribution:
        def locate_file(self, resource: str) -> Path:
            return located_root if resource == "" else located_path

    def simulated_is_symlink(path: Path) -> bool:
        return path == located_path

    def guarded_resolve(path: Path, strict: bool = False) -> Path:
        resolved_paths.append(path)
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(
        importlib_metadata,
        "distribution",
        lambda _name: _LocatedDistribution(),
    )
    monkeypatch.setattr(Path, "is_symlink", simulated_is_symlink)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)

    with pytest.raises(OverlayRenderError) as raised:
        overlay_adapter._load_authenticated_font_bytes(_Control(), _limits())

    assert raised.value.code is OverlayRenderErrorCode.FONT_UNAVAILABLE
    assert raised.value.context == "overlay.font_resource"
    assert located_path not in resolved_paths
