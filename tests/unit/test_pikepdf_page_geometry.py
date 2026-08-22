from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from io import BytesIO
from typing import BinaryIO

import pikepdf
import pytest

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    PdfPageBoxSelection,
    PdfUserSpaceBox,
)
from paperworkshell.domain.pdf_geometry import (
    PdfPageGeometryLimits,
    PdfPageRectangle,
    PdfResolvedPageGeometry,
    resolve_pdf_page_geometry,
)
from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
)
from paperworkshell.pdf.page_geometry import (
    MAXIMUM_PAGE_TREE_CHILDREN,
    MAXIMUM_PAGE_TREE_DEPTH,
    MAXIMUM_PAGE_TREE_NODES,
    PikePdfPageGeometryResolver,
    _InheritedPageValues,
    _read_inherited_page_values,
    _select_bounded_page_from_catalog,
    to_page_coordinate_geometry,
)

PageConfigurator = Callable[[pikepdf.Dictionary, pikepdf.Dictionary], None]


def _render_limits(**changes: int) -> PdfPageRenderLimits:
    values = {
        "maximum_pages": 10,
        "maximum_width_pixels": 1_000,
        "maximum_height_pixels": 1_000,
        "maximum_pixels": 1_000_000,
        "maximum_bitmap_bytes": 4_000_000,
        "maximum_destination_bytes": 4_000_000,
        "maximum_memory_bytes": 4_000_000,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return PdfPageRenderLimits(**values)


def _geometry_limits() -> PdfPageGeometryLimits:
    return PdfPageGeometryLimits(
        maximum_coordinate_magnitude_units=1_000_000,
        maximum_box_span_units=1_000_000,
    )


def _pdf_stream(configure: PageConfigurator | None = None, *, pages: int = 1) -> BytesIO:
    document = pikepdf.Pdf.new()
    try:
        for index in range(pages):
            page = document.add_blank_page(page_size=(200, 100))
            if index == 0 and configure is not None:
                parent = page.obj.get("/Parent")
                assert isinstance(parent, pikepdf.Dictionary)
                configure(page.obj, parent)
        stream = BytesIO()
        document.save(stream)
    finally:
        document.close()
    stream.seek(0)
    return stream


def _resolve(
    stream: BinaryIO,
    *,
    page_index: int = 0,
    page_box: PdfPageBox = PdfPageBox.CROP,
    render_limits: PdfPageRenderLimits | None = None,
) -> PdfResolvedPageGeometry:
    return PikePdfPageGeometryResolver().resolve(
        stream,
        PdfPageRenderRequest(page_index, 500, 500, page_box),
        _render_limits() if render_limits is None else render_limits,
        _geometry_limits(),
    )


def _root_pages(document: pikepdf.Pdf) -> pikepdf.Dictionary:
    root_pages = document.Root.get("/Pages")
    assert isinstance(root_pages, pikepdf.Dictionary)
    return root_pages


def _read_page_values(
    document: pikepdf.Pdf,
    page: pikepdf.Dictionary,
    *,
    maximum_depth: int = 8,
    maximum_children: int = 16,
) -> _InheritedPageValues:
    return _read_inherited_page_values(
        page,
        root_pages=_root_pages(document),
        maximum_depth=maximum_depth,
        maximum_children=maximum_children,
    )


def _select_page(
    document: pikepdf.Pdf,
    *,
    page_index: int = 0,
    maximum_pages: int = 10,
    maximum_depth: int = 8,
    maximum_children: int = 16,
    maximum_nodes: int = 32,
) -> tuple[pikepdf.Dictionary, int]:
    return _select_bounded_page_from_catalog(
        _root_pages(document),
        page_index=page_index,
        maximum_pages=maximum_pages,
        maximum_depth=maximum_depth,
        maximum_children=maximum_children,
        maximum_nodes=maximum_nodes,
    )


@pytest.mark.parametrize(
    "resolver_factory",
    (
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_depth=0),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_depth=MAXIMUM_PAGE_TREE_DEPTH + 1),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_depth=True),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_children=0),
        lambda: PikePdfPageGeometryResolver(
            maximum_page_tree_children=MAXIMUM_PAGE_TREE_CHILDREN + 1
        ),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_children=True),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_nodes=0),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_nodes=MAXIMUM_PAGE_TREE_NODES + 1),
        lambda: PikePdfPageGeometryResolver(maximum_page_tree_nodes=True),
    ),
)
def test_resolver_rejects_invalid_page_tree_budgets(
    resolver_factory: Callable[[], PikePdfPageGeometryResolver],
) -> None:
    with pytest.raises(PdfRenderError) as raised:
        resolver_factory()

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "render.geometry.adapter"


def test_resolver_uses_nearest_direct_page_values_and_restores_stream_position() -> None:
    def configure(page: pikepdf.Dictionary, parent: pikepdf.Dictionary) -> None:
        parent["/MediaBox"] = pikepdf.Array([0, 0, 400, 200])
        parent["/CropBox"] = pikepdf.Array([20, 20, 380, 180])
        parent["/Rotate"] = 90
        page["/MediaBox"] = pikepdf.Array([10, 15, 210, 115])
        page["/CropBox"] = pikepdf.Array([30, 25, 190, 105])
        page["/Rotate"] = 180
        page["/UserUnit"] = pikepdf.Real(Decimal("2.5"))

    stream = _pdf_stream(configure)
    stream.seek(7)

    geometry = _resolve(stream)

    assert geometry.media_box == PdfPageRectangle(10.0, 15.0, 210.0, 115.0)
    assert geometry.crop_box == PdfPageRectangle(30.0, 25.0, 190.0, 105.0)
    assert geometry.intrinsic_rotation is PdfRenderRotation.DEGREES_180
    assert geometry.user_unit == 2.5
    assert stream.tell() == 7
    assert not stream.closed


def test_resolver_walks_leaf_to_parent_for_inherited_boxes_and_rotation() -> None:
    def configure(page: pikepdf.Dictionary, parent: pikepdf.Dictionary) -> None:
        del page["/MediaBox"]
        parent["/MediaBox"] = pikepdf.Array([400, 0, 0, 200])
        parent["/CropBox"] = pikepdf.Array([380, 180, 20, 20])
        parent["/Rotate"] = -90

    geometry = _resolve(_pdf_stream(configure))

    assert geometry.media_box == PdfPageRectangle(0.0, 0.0, 400.0, 200.0)
    assert geometry.crop_box == PdfPageRectangle(20.0, 20.0, 380.0, 180.0)
    assert geometry.intrinsic_rotation is PdfRenderRotation.DEGREES_270


def test_user_unit_is_page_local_and_parent_value_is_not_inherited() -> None:
    def configure(page: pikepdf.Dictionary, parent: pikepdf.Dictionary) -> None:
        del page["/MediaBox"]
        parent["/MediaBox"] = pikepdf.Array([0, 0, 200, 100])
        parent["/UserUnit"] = pikepdf.Real(Decimal("7.5"))

    geometry = _resolve(_pdf_stream(configure))

    assert geometry.user_unit == 1.0
    assert not geometry.uses_non_default_user_unit


def test_crop_mode_intersects_inherited_crop_with_direct_media() -> None:
    def configure(page: pikepdf.Dictionary, parent: pikepdf.Dictionary) -> None:
        page["/MediaBox"] = pikepdf.Array([0, 0, 200, 100])
        parent["/CropBox"] = pikepdf.Array([-50, 20, 150, 120])

    geometry = _resolve(_pdf_stream(configure))
    assert geometry.effective_box == PdfPageRectangle(0.0, 20.0, 150.0, 100.0)


@pytest.mark.parametrize(
    ("page_box", "expected_selection", "expected_effective"),
    (
        (
            PdfPageBox.MEDIA,
            PdfPageBoxSelection.MEDIA,
            PdfUserSpaceBox(0.0, 0.0, 400.0, 200.0),
        ),
        (
            PdfPageBox.CROP,
            PdfPageBoxSelection.CROP,
            PdfUserSpaceBox(0.0, 20.0, 350.0, 200.0),
        ),
    ),
)
def test_page_coordinate_bridge_preserves_box_selection_and_intersection(
    page_box: PdfPageBox,
    expected_selection: PdfPageBoxSelection,
    expected_effective: PdfUserSpaceBox,
) -> None:
    media_box = PdfPageRectangle(0.0, 0.0, 400.0, 200.0)
    crop_box = PdfPageRectangle(-50.0, 20.0, 350.0, 250.0)
    effective_box = (
        media_box
        if page_box is PdfPageBox.MEDIA
        else PdfPageRectangle(
            0.0,
            20.0,
            350.0,
            200.0,
        )
    )

    converted = to_page_coordinate_geometry(
        PdfResolvedPageGeometry(
            media_box=media_box,
            crop_box=crop_box,
            effective_box=effective_box,
            page_box=page_box,
            intrinsic_rotation=PdfRenderRotation.DEGREES_0,
            user_unit=1.0,
        )
    )

    assert converted.media_box == PdfUserSpaceBox(0.0, 0.0, 400.0, 200.0)
    assert converted.crop_box == PdfUserSpaceBox(-50.0, 20.0, 350.0, 250.0)
    assert converted.page_box is expected_selection
    assert converted.effective_box == expected_effective


@pytest.mark.parametrize(
    ("source_rotation", "expected_rotation"),
    (
        (PdfRenderRotation.DEGREES_0, ClockwiseRotation.DEGREES_0),
        (PdfRenderRotation.DEGREES_90, ClockwiseRotation.DEGREES_90),
        (PdfRenderRotation.DEGREES_180, ClockwiseRotation.DEGREES_180),
        (PdfRenderRotation.DEGREES_270, ClockwiseRotation.DEGREES_270),
    ),
)
def test_page_coordinate_bridge_preserves_rotation_and_user_unit(
    source_rotation: PdfRenderRotation,
    expected_rotation: ClockwiseRotation,
) -> None:
    box = PdfPageRectangle(10.0, 15.0, 210.0, 115.0)
    converted = to_page_coordinate_geometry(
        PdfResolvedPageGeometry(
            media_box=box,
            crop_box=box,
            effective_box=box,
            page_box=PdfPageBox.MEDIA,
            intrinsic_rotation=source_rotation,
            user_unit=2.5,
        )
    )

    assert converted.intrinsic_rotation is expected_rotation
    assert converted.user_unit == 2.5


def test_page_coordinate_bridge_maps_invalid_input_to_model_error() -> None:
    with pytest.raises(PdfRenderError) as raised:
        to_page_coordinate_geometry(object())  # type: ignore[arg-type]

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "render.geometry.page_coordinates"


def test_page_coordinate_bridge_maps_unresolvable_span_to_geometry_error() -> None:
    narrow_box = PdfPageRectangle(999_999_999.0, 0.0, 1_000_000_000.0, 100.0)

    with pytest.raises(PdfRenderError) as raised:
        to_page_coordinate_geometry(
            PdfResolvedPageGeometry(
                media_box=narrow_box,
                crop_box=narrow_box,
                effective_box=narrow_box,
                page_box=PdfPageBox.MEDIA,
                intrinsic_rotation=PdfRenderRotation.DEGREES_0,
                user_unit=1.0,
            )
        )

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_coordinates"
    assert "999999999" not in str(raised.value)


def test_page_coordinate_bridge_rejects_inconsistent_effective_crop() -> None:
    media_box = PdfPageRectangle(0.0, 0.0, 400.0, 200.0)
    crop_box = PdfPageRectangle(-50.0, 20.0, 350.0, 250.0)

    with pytest.raises(PdfRenderError) as raised:
        to_page_coordinate_geometry(
            PdfResolvedPageGeometry(
                media_box=media_box,
                crop_box=crop_box,
                effective_box=PdfPageRectangle(10.0, 30.0, 340.0, 190.0),
                page_box=PdfPageBox.CROP,
                intrinsic_rotation=PdfRenderRotation.DEGREES_0,
                user_unit=1.0,
            )
        )

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_coordinates"


@pytest.mark.parametrize(
    ("key", "value", "context"),
    (
        ("/MediaBox", pikepdf.Array([0, 0, 0, 100]), "render.geometry.media_box"),
        ("/CropBox", pikepdf.Array([0, 0, 100]), "render.geometry.box"),
        ("/Rotate", 45, "render.geometry.rotate"),
        ("/Rotate", pikepdf.Real(Decimal("90.0")), "render.geometry.rotate"),
        ("/UserUnit", 0, "render.geometry.user_unit"),
        ("/UserUnit", -1, "render.geometry.user_unit"),
        (
            "/UserUnit",
            pikepdf.Real(Decimal("75000.1")),
            "render.geometry.user_unit",
        ),
        ("/UserUnit", pikepdf.String("2"), "render.geometry.number"),
    ),
)
def test_invalid_pdf_geometry_values_are_mapped_to_stable_sanitized_errors(
    key: str,
    value: object,
    context: str,
) -> None:
    def configure(page: pikepdf.Dictionary, _parent: pikepdf.Dictionary) -> None:
        page[key] = value

    with pytest.raises(PdfRenderError) as raised:
        _resolve(_pdf_stream(configure))

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == context
    assert repr(value) not in str(raised.value)


@pytest.mark.parametrize(
    ("media_box", "user_unit", "limits", "code", "context"),
    (
        (
            (0, 0, Decimal("1000.00000000000001"), 100),
            None,
            PdfPageGeometryLimits(1_000, 1_000),
            PdfRenderErrorCode.LIMIT_EXCEEDED,
            "render.geometry.media_box",
        ),
        (
            (0, 0, Decimal("500.00000000000001"), 100),
            None,
            PdfPageGeometryLimits(1_000, 500),
            PdfRenderErrorCode.LIMIT_EXCEEDED,
            "render.geometry.media_box",
        ),
        (
            (0, 0, 100, 100),
            Decimal("75000.000000000001"),
            PdfPageGeometryLimits(1_000, 1_000),
            PdfRenderErrorCode.GEOMETRY_INVALID,
            "render.geometry.user_unit",
        ),
    ),
)
def test_decimal_pdf_numbers_remain_exact_through_domain_policy_checks(
    media_box: tuple[object, object, object, object],
    user_unit: Decimal | None,
    limits: PdfPageGeometryLimits,
    code: PdfRenderErrorCode,
    context: str,
) -> None:
    assert any(
        isinstance(value, Decimal) and float(value) <= limits.maximum_coordinate_magnitude_units
        for value in media_box
    ) or (user_unit is not None and float(user_unit) == 75_000.0)

    document = pikepdf.Pdf.new()
    try:
        media_tokens = " ".join(str(value) for value in media_box)
        user_unit_entry = "" if user_unit is None else f" /UserUnit {user_unit}"
        parsed_page = pikepdf.Object.parse(
            f"<< /Type /Page /MediaBox [{media_tokens}]{user_unit_entry} >>".encode("ascii")
        )
        assert isinstance(parsed_page, pikepdf.Dictionary)
        page = document.make_indirect(parsed_page)
        root_pages = _root_pages(document)
        page["/Parent"] = root_pages
        root_pages["/Kids"] = pikepdf.Array([page])
        root_pages["/Count"] = 1
        inherited = _read_page_values(document, page)

        assert (
            any(type(value) is Decimal for value in inherited.media_box)
            or type(inherited.user_unit) is Decimal
        )
        with pytest.raises(PdfRenderError) as raised:
            resolve_pdf_page_geometry(
                inherited.media_box,
                inherited.crop_box,
                inherited.rotation,
                inherited.user_unit,
                PdfPageBox.CROP,
                limits,
            )
    finally:
        document.close()

    assert raised.value.code is code
    assert raised.value.context == context


def test_page_count_and_requested_page_are_bounded_before_geometry_use() -> None:
    with pytest.raises(PdfRenderError) as page_limit:
        _resolve(_pdf_stream(pages=2), render_limits=_render_limits(maximum_pages=1))
    assert page_limit.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert page_limit.value.context == "render.pages"

    with pytest.raises(PdfRenderError) as page_index:
        _resolve(_pdf_stream(), page_index=1)
    assert page_index.value.code is PdfRenderErrorCode.PAGE_OUT_OF_RANGE
    assert page_index.value.context == "render.geometry.page_index"


def test_catalog_walk_selects_nested_page_in_order_and_validates_exact_counts() -> None:
    document = pikepdf.Pdf.new()
    try:
        first = document.add_blank_page(page_size=(200, 100)).obj
        second = document.add_blank_page(page_size=(200, 100)).obj
        third = document.add_blank_page(page_size=(200, 100)).obj
        root_pages = _root_pages(document)
        branch = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=2))
        branch["/Parent"] = root_pages
        branch["/Kids"] = pikepdf.Array([second, third])
        second["/Parent"] = branch
        third["/Parent"] = branch
        root_pages["/Kids"] = pikepdf.Array([first, branch])
        root_pages["/Count"] = 3

        selected, page_count = _select_page(document, page_index=2)

        assert selected.objgen == third.objgen
        assert page_count == 3
    finally:
        document.close()


def test_catalog_walk_does_not_trust_small_count_to_bypass_actual_page_limit() -> None:
    document = pikepdf.Pdf.new()
    try:
        document.add_blank_page(page_size=(200, 100))
        document.add_blank_page(page_size=(200, 100))
        _root_pages(document)["/Count"] = 1

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document, maximum_pages=1)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.pages"


def test_catalog_walk_rejects_count_mismatch_after_selected_page() -> None:
    document = pikepdf.Pdf.new()
    try:
        document.add_blank_page(page_size=(200, 100))
        document.add_blank_page(page_size=(200, 100))
        _root_pages(document)["/Count"] = 1

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document, page_index=0)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_catalog_walk_rejects_duplicate_leaf_after_selected_page() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        root_pages = _root_pages(document)
        root_pages["/Kids"] = pikepdf.Array([page, page])
        root_pages["/Count"] = 2

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document, page_index=0)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_catalog_walk_rejects_later_parent_mismatch_after_selected_page() -> None:
    document = pikepdf.Pdf.new()
    try:
        document.add_blank_page(page_size=(200, 100))
        later = document.add_blank_page(page_size=(200, 100)).obj
        unrelated = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Pages"),
                Count=0,
                Kids=pikepdf.Array([]),
            )
        )
        later["/Parent"] = unrelated

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document, page_index=0)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_catalog_walk_rejects_direct_page_tree_objects() -> None:
    document = pikepdf.Pdf.new()
    try:
        root_pages = _root_pages(document)
        direct_page = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 20, 10]),
        )
        direct_page["/Parent"] = root_pages
        root_pages["/Kids"] = pikepdf.Array([direct_page])
        root_pages["/Count"] = 1
        assert direct_page.objgen == (0, 0)

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_catalog_walk_enforces_child_node_and_depth_budgets() -> None:
    document = pikepdf.Pdf.new()
    try:
        first = document.add_blank_page(page_size=(200, 100)).obj
        second = document.add_blank_page(page_size=(200, 100)).obj

        with pytest.raises(PdfRenderError) as child_limit:
            _select_page(document, maximum_children=1)
        assert child_limit.value.context == "render.geometry.page_tree_children"

        with pytest.raises(PdfRenderError) as node_limit:
            _select_page(document, maximum_nodes=2)
        assert node_limit.value.context == "render.geometry.page_tree_nodes"

        root_pages = _root_pages(document)
        branch = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Pages"),
                Count=2,
                Kids=pikepdf.Array([first, second]),
            )
        )
        branch["/Parent"] = root_pages
        first["/Parent"] = branch
        second["/Parent"] = branch
        root_pages["/Kids"] = pikepdf.Array([branch])
        root_pages["/Count"] = 2
        with pytest.raises(PdfRenderError) as depth_limit:
            _select_page(document, maximum_depth=2)
        assert depth_limit.value.context == "render.geometry.inheritance_depth"
    finally:
        document.close()

    assert child_limit.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert node_limit.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert depth_limit.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED


def test_catalog_walk_rejects_cycles_by_global_indirect_identity() -> None:
    document = pikepdf.Pdf.new()
    try:
        root_pages = _root_pages(document)
        branch = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=0))
        branch["/Parent"] = root_pages
        branch["/Kids"] = pikepdf.Array([root_pages])
        root_pages["/Kids"] = pikepdf.Array([branch])
        root_pages["/Count"] = 0

        with pytest.raises(PdfRenderError) as raised:
            _select_page(document)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_owner_encrypted_pdf_with_empty_user_password_is_never_upgraded_to_geometry() -> None:
    document = pikepdf.Pdf.new()
    try:
        document.add_blank_page(page_size=(200, 100))
        stream = BytesIO()
        document.save(
            stream,
            encryption=pikepdf.Encryption(
                owner="synthetic-owner-only",
                user="",
                R=6,
            ),
        )
    finally:
        document.close()
    stream.seek(0)

    with pytest.raises(PdfRenderError) as raised:
        _resolve(stream)

    assert raised.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert raised.value.context == "render.geometry.encrypted"
    assert not stream.closed


def test_missing_media_box_and_parent_before_declared_root_are_rejected() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        parent = page.get("/Parent")
        assert isinstance(parent, pikepdf.Dictionary)
        del page["/MediaBox"]

        with pytest.raises(PdfRenderError) as missing_box:
            _read_page_values(document, page)
        assert missing_box.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert missing_box.value.context == "render.geometry.media_box"

        page["/MediaBox"] = pikepdf.Array([0, 0, 20, 10])
        del page["/Parent"]
        with pytest.raises(PdfRenderError) as early_terminal:
            _read_page_values(document, page)
        assert early_terminal.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert early_terminal.value.context == "render.geometry.page_tree"
    finally:
        document.close()


def test_page_tree_parent_cycle_is_rejected_by_indirect_identity() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        first = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=1))
        second = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=1))
        first["/Kids"] = pikepdf.Array([page, second])
        second["/Kids"] = pikepdf.Array([first])
        page["/Parent"] = first
        first["/Parent"] = second
        second["/Parent"] = first

        with pytest.raises(PdfRenderError) as raised:
            _read_page_values(document, page)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.inheritance_cycle"


def test_page_tree_depth_is_hard_bounded_even_when_values_are_already_found() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        root_pages = _root_pages(document)
        first = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=1))
        second = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Count=1))
        first["/Kids"] = pikepdf.Array([page])
        second["/Kids"] = pikepdf.Array([first])
        root_pages["/Kids"] = pikepdf.Array([second])
        page["/Parent"] = first
        first["/Parent"] = second
        second["/Parent"] = root_pages

        with pytest.raises(PdfRenderError) as raised:
            _read_page_values(document, page, maximum_depth=2)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.geometry.inheritance_depth"


def test_non_dictionary_parent_is_rejected_without_following_it() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        page["/Parent"] = pikepdf.Array([1, 2])
        with pytest.raises(PdfRenderError) as raised:
            _read_page_values(document, page)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.parent_type"


@pytest.mark.parametrize("node", ("leaf", "root"))
def test_page_tree_node_types_are_required(node: str) -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        target = page if node == "leaf" else _root_pages(document)
        target["/Type"] = pikepdf.Name("/Pages" if node == "leaf" else "/Page")

        with pytest.raises(PdfRenderError) as raised:
            _read_page_values(document, page)
    finally:
        document.close()

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.page_tree"


def test_page_tree_parent_must_list_child_exactly_once_within_bound() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        root_pages = _root_pages(document)
        sibling = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 20, 10]),
            )
        )

        root_pages["/Kids"] = pikepdf.Array([sibling])
        with pytest.raises(PdfRenderError) as absent:
            _read_page_values(document, page)
        assert absent.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert absent.value.context == "render.geometry.page_tree"

        root_pages["/Kids"] = pikepdf.Array([page, page])
        with pytest.raises(PdfRenderError) as duplicate:
            _read_page_values(document, page)
        assert duplicate.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert duplicate.value.context == "render.geometry.page_tree"

        root_pages["/Kids"] = pikepdf.Array([page, sibling])
        with pytest.raises(PdfRenderError) as excessive:
            _read_page_values(document, page, maximum_children=1)
        assert excessive.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
        assert excessive.value.context == "render.geometry.page_tree_children"
    finally:
        document.close()


def test_unrelated_root_and_parent_on_declared_root_are_rejected() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        actual_root = _root_pages(document)
        unrelated_root = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Pages"),
                Count=1,
                Kids=pikepdf.Array([page]),
            )
        )
        with pytest.raises(PdfRenderError) as unrelated:
            _read_inherited_page_values(
                page,
                root_pages=unrelated_root,
                maximum_depth=8,
                maximum_children=16,
            )
        assert unrelated.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert unrelated.value.context == "render.geometry.page_tree"

        parent_above_root = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Pages"),
                Count=1,
                Kids=pikepdf.Array([actual_root]),
            )
        )
        actual_root["/Parent"] = parent_above_root
        with pytest.raises(PdfRenderError) as root_parent:
            _read_page_values(document, page)
        assert root_parent.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
        assert root_parent.value.context == "render.geometry.page_tree"
    finally:
        document.close()


class _FailingSeek(BytesIO):
    def seek(self, _offset: int, _whence: int = 0, /) -> int:
        raise OSError("PRIVATE-GEOMETRY-PATH")


class _RestoreFailingStream(BytesIO):
    def __init__(self) -> None:
        super().__init__(b"synthetic-placeholder")
        super().seek(7)
        self._rewound = False

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self._rewound and whence == 0 and offset == 7:
            raise OSError("PRIVATE-RESTORE-PATH")
        position = super().seek(offset, whence)
        if whence == 0 and offset == 0:
            self._rewound = True
        return position


def test_stream_and_parser_failures_are_sanitized_and_do_not_close_source() -> None:
    source = _FailingSeek(b"%PDF-1.7\n")
    with pytest.raises(PdfRenderError) as stream_error:
        _resolve(source)
    assert stream_error.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert "PRIVATE-GEOMETRY-PATH" not in str(stream_error.value)
    assert not source.closed

    malformed = BytesIO(b"PRIVATE-MALFORMED-PDF")
    with pytest.raises(PdfRenderError) as parser_error:
        _resolve(malformed)
    assert parser_error.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert "PRIVATE-MALFORMED-PDF" not in str(parser_error.value)
    assert parser_error.value.__cause__ is None
    assert parser_error.value.__context__ is None
    assert not malformed.closed


def test_stream_restore_failure_takes_precedence_over_operation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise PdfRenderError(
            code=PdfRenderErrorCode.GEOMETRY_INVALID,
            context="render.geometry.primary_failure",
        )

    monkeypatch.setattr(pikepdf.Pdf, "open", staticmethod(fail_open))
    source = _RestoreFailingStream()

    with pytest.raises(PdfRenderError) as raised:
        _resolve(source)

    assert raised.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert raised.value.context == "render.geometry.stream"
    assert raised.value.__cause__ is None
    assert "primary_failure" not in str(raised.value)
    assert "PRIVATE-RESTORE-PATH" not in str(raised.value)
    assert source.tell() == 0
    assert not source.closed


def test_stream_restore_failure_discards_successful_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = pikepdf.Pdf.new()
    document.add_blank_page(page_size=(200, 100))
    monkeypatch.setattr(pikepdf.Pdf, "open", staticmethod(lambda *_args, **_kwargs: document))
    source = _RestoreFailingStream()

    with pytest.raises(PdfRenderError) as raised:
        _resolve(source)

    assert raised.value.code is PdfRenderErrorCode.SOURCE_INVALID
    assert raised.value.context == "render.geometry.stream"
    assert source.tell() == 0
    assert not source.closed


def test_inherited_value_representation_hides_pdf_coordinates_and_user_unit() -> None:
    document = pikepdf.Pdf.new()
    try:
        page = document.add_blank_page(page_size=(200, 100)).obj
        page["/MediaBox"] = pikepdf.Array(
            [pikepdf.Real(Decimal("1234.567")), 0, pikepdf.Real(Decimal("1434.567")), 100]
        )
        page["/UserUnit"] = pikepdf.Real(Decimal("2.5"))
        inherited = _read_page_values(document, page)
    finally:
        document.close()

    assert "1234.567" not in repr(inherited)
    assert "2.5" not in repr(inherited)
