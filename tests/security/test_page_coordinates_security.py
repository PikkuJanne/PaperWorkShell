from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateError,
    PageCoordinateErrorCode,
    PageCoordinateTransform,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
    PdfUserSpacePoint,
)


class _HostileMapping(Mapping[str, object]):
    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("PRIVATE_DOCUMENT_TOKEN")

    def __len__(self) -> int:
        return 5

    def __getitem__(self, key: str) -> object:
        raise RuntimeError("PRIVATE_DOCUMENT_TOKEN")


class _HostileCoordinateSpace:
    def __eq__(self, other: object) -> bool:
        raise RuntimeError("PRIVATE_DOCUMENT_TOKEN")


def test_untrusted_mapping_failures_do_not_disclose_fixture_values() -> None:
    with pytest.raises(PageCoordinateError) as raised:
        NormalizedPageBox.from_manifest(_HostileMapping())

    error = raised.value
    assert error.code is PageCoordinateErrorCode.MANIFEST_INVALID
    assert "PRIVATE_DOCUMENT_TOKEN" not in str(error)
    assert "PRIVATE_DOCUMENT_TOKEN" not in repr(error)

    with pytest.raises(PageCoordinateError) as direct:
        NormalizedPageBox(0, 0, 1, 1, _HostileCoordinateSpace())  # type: ignore[arg-type]
    assert direct.value.code is PageCoordinateErrorCode.BOX_INVALID
    assert "PRIVATE_DOCUMENT_TOKEN" not in str(direct.value)


def test_pdf_geometry_and_transform_reprs_hide_source_coordinate_values() -> None:
    coordinate_marker = 123_456_789.0
    point = PdfUserSpacePoint(coordinate_marker, -coordinate_marker)
    box = PdfUserSpaceBox(
        -coordinate_marker,
        -coordinate_marker,
        coordinate_marker,
        coordinate_marker,
    )
    geometry = PdfPageCoordinateGeometry(
        box,
        None,
        PdfPageBoxSelection.MEDIA,
        ClockwiseRotation.DEGREES_0,
    )
    transform = PageCoordinateTransform(geometry)

    for value in (point, box, geometry, transform):
        assert str(int(coordinate_marker)) not in repr(value)
