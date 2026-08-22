"""Pikepdf-backed inherited page-geometry resolution for the PDF worker layer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO, Final

import pikepdf

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    PageCoordinateError,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
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
    reject_pdf_rendering,
    validate_pdf_render_request,
)

DEFAULT_MAXIMUM_PAGE_TREE_DEPTH: Final = 64
MAXIMUM_PAGE_TREE_DEPTH: Final = 256
DEFAULT_MAXIMUM_PAGE_TREE_CHILDREN: Final = 4_096
MAXIMUM_PAGE_TREE_CHILDREN: Final = 65_536
DEFAULT_MAXIMUM_PAGE_TREE_NODES: Final = 16_384
MAXIMUM_PAGE_TREE_NODES: Final = 262_144

_NAME_CROP_BOX: Final = pikepdf.Name("/CropBox")
_NAME_COUNT: Final = pikepdf.Name("/Count")
_NAME_KIDS: Final = pikepdf.Name("/Kids")
_NAME_MEDIA_BOX: Final = pikepdf.Name("/MediaBox")
_NAME_PAGE: Final = pikepdf.Name("/Page")
_NAME_PAGES: Final = pikepdf.Name("/Pages")
_NAME_PARENT: Final = pikepdf.Name("/Parent")
_NAME_ROTATE: Final = pikepdf.Name("/Rotate")
_NAME_TYPE: Final = pikepdf.Name("/Type")
_NAME_USER_UNIT: Final = pikepdf.Name("/UserUnit")

_PAGE_BOX_SELECTION: Final = {
    PdfPageBox.MEDIA: PdfPageBoxSelection.MEDIA,
    PdfPageBox.CROP: PdfPageBoxSelection.CROP,
}
_CLOCKWISE_ROTATION: Final = {
    0: ClockwiseRotation.DEGREES_0,
    90: ClockwiseRotation.DEGREES_90,
    180: ClockwiseRotation.DEGREES_180,
    270: ClockwiseRotation.DEGREES_270,
}


@dataclass(frozen=True, slots=True, repr=False)
class _InheritedPageValues:
    media_box: tuple[object, ...]
    crop_box: tuple[object, ...] | None
    rotation: object | None
    user_unit: object | None

    def __repr__(self) -> str:
        return "_InheritedPageValues(<page-values-hidden>)"


@dataclass(slots=True, repr=False)
class _PageTreeFrame:
    """One bounded iterative page-tree traversal frame."""

    identity: tuple[int, int]
    kids: pikepdf.Array
    depth: int
    declared_page_count: int
    next_child_index: int = 0
    actual_page_count: int = 0


@dataclass(frozen=True, slots=True)
class PikePdfPageGeometryResolver:
    """Resolve page-tree geometry without exposing pikepdf objects or document values."""

    maximum_page_tree_depth: int = DEFAULT_MAXIMUM_PAGE_TREE_DEPTH
    maximum_page_tree_children: int = DEFAULT_MAXIMUM_PAGE_TREE_CHILDREN
    maximum_page_tree_nodes: int = DEFAULT_MAXIMUM_PAGE_TREE_NODES

    def __post_init__(self) -> None:
        if (
            type(self.maximum_page_tree_depth) is not int
            or not 1 <= self.maximum_page_tree_depth <= MAXIMUM_PAGE_TREE_DEPTH
            or type(self.maximum_page_tree_children) is not int
            or not 1 <= self.maximum_page_tree_children <= MAXIMUM_PAGE_TREE_CHILDREN
            or type(self.maximum_page_tree_nodes) is not int
            or not 1 <= self.maximum_page_tree_nodes <= MAXIMUM_PAGE_TREE_NODES
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.adapter")

    def resolve(
        self,
        source: BinaryIO,
        request: PdfPageRenderRequest,
        render_limits: PdfPageRenderLimits,
        geometry_limits: PdfPageGeometryLimits,
    ) -> PdfResolvedPageGeometry:
        """Resolve one page while retaining and restoring the caller-owned stream."""

        if (
            type(request) is not PdfPageRenderRequest
            or type(render_limits) is not PdfPageRenderLimits
            or type(geometry_limits) is not PdfPageGeometryLimits
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.input")
        validate_pdf_render_request(request, render_limits)

        try:
            original_position = source.tell()
        except Exception:
            reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.geometry.stream")
        if type(original_position) is not int or original_position < 0:
            reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.geometry.stream")

        result: PdfResolvedPageGeometry | None = None
        operation_error: PdfRenderError | None = None
        restore_failed = False
        try:
            source.seek(0)
            with pikepdf.Pdf.open(
                source,
                password="",
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=False,
            ) as document:
                if document.is_encrypted:
                    reject_pdf_rendering(
                        PdfRenderErrorCode.SOURCE_INVALID,
                        "render.geometry.encrypted",
                    )
                root_pages = document.Root.get(_NAME_PAGES)
                if not isinstance(root_pages, pikepdf.Dictionary):
                    reject_pdf_rendering(
                        PdfRenderErrorCode.GEOMETRY_INVALID,
                        "render.geometry.page_tree",
                    )
                page_object, _page_count = _select_bounded_page_from_catalog(
                    root_pages,
                    page_index=request.page_index,
                    maximum_pages=render_limits.maximum_pages,
                    maximum_depth=self.maximum_page_tree_depth,
                    maximum_children=self.maximum_page_tree_children,
                    maximum_nodes=self.maximum_page_tree_nodes,
                )
                inherited = _read_inherited_page_values(
                    page_object,
                    root_pages=root_pages,
                    maximum_depth=self.maximum_page_tree_depth,
                    maximum_children=self.maximum_page_tree_children,
                )
                result = resolve_pdf_page_geometry(
                    inherited.media_box,
                    inherited.crop_box,
                    inherited.rotation,
                    inherited.user_unit,
                    request.page_box,
                    geometry_limits,
                )
        except PdfRenderError as error:
            operation_error = error
        except pikepdf.PasswordError:
            operation_error = PdfRenderError(
                code=PdfRenderErrorCode.SOURCE_INVALID,
                context="render.geometry.encrypted",
            )
        except (
            pikepdf.DataDecodingError,
            pikepdf.DeletedObjectError,
            pikepdf.ForeignObjectError,
            pikepdf.PdfError,
            RuntimeError,
            ValueError,
        ):
            operation_error = PdfRenderError(
                code=PdfRenderErrorCode.SOURCE_INVALID,
                context="render.geometry.source",
            )
        except MemoryError, RecursionError:
            operation_error = PdfRenderError(
                code=PdfRenderErrorCode.LIMIT_EXCEEDED,
                context="render.geometry.resources",
            )
        except OSError:
            operation_error = PdfRenderError(
                code=PdfRenderErrorCode.SOURCE_INVALID,
                context="render.geometry.stream",
            )
        except Exception:
            operation_error = PdfRenderError(
                code=PdfRenderErrorCode.INTERNAL_ERROR,
                context="render.geometry.adapter",
            )
        finally:
            try:
                source.seek(original_position)
            except Exception:
                restore_failed = True

        if restore_failed:
            raise PdfRenderError(
                code=PdfRenderErrorCode.SOURCE_INVALID,
                context="render.geometry.stream",
            ) from None
        if operation_error is not None:
            raise operation_error from None
        if result is None:
            reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.geometry.adapter")
        return result


def to_page_coordinate_geometry(
    geometry: PdfResolvedPageGeometry,
) -> PdfPageCoordinateGeometry:
    """Bridge resolved adapter geometry into the normalized-coordinate model."""

    if type(geometry) is not PdfResolvedPageGeometry:
        reject_pdf_rendering(
            PdfRenderErrorCode.MODEL_INVALID,
            "render.geometry.page_coordinates",
        )
    try:
        converted = PdfPageCoordinateGeometry(
            media_box=_to_user_space_box(geometry.media_box),
            crop_box=_to_user_space_box(geometry.crop_box),
            page_box=_PAGE_BOX_SELECTION[geometry.page_box],
            intrinsic_rotation=_CLOCKWISE_ROTATION[int(geometry.intrinsic_rotation)],
            user_unit=geometry.user_unit,
        )
    except PageCoordinateError:
        reject_pdf_rendering(
            PdfRenderErrorCode.GEOMETRY_INVALID,
            "render.geometry.page_coordinates",
        )
    converted_effective = converted.effective_box
    source_effective = geometry.effective_box
    if (
        converted_effective.left,
        converted_effective.bottom,
        converted_effective.right,
        converted_effective.top,
    ) != (
        source_effective.left,
        source_effective.bottom,
        source_effective.right,
        source_effective.top,
    ):
        reject_pdf_rendering(
            PdfRenderErrorCode.GEOMETRY_INVALID,
            "render.geometry.page_coordinates",
        )
    return converted


def _to_user_space_box(rectangle: PdfPageRectangle) -> PdfUserSpaceBox:
    return PdfUserSpaceBox(
        left=rectangle.left,
        bottom=rectangle.bottom,
        right=rectangle.right,
        top=rectangle.top,
    )


def _select_bounded_page_from_catalog(
    root_pages: pikepdf.Dictionary,
    *,
    page_index: int,
    maximum_pages: int,
    maximum_depth: int,
    maximum_children: int,
    maximum_nodes: int,
) -> tuple[pikepdf.Dictionary, int]:
    """Validate the complete page tree and select one leaf without ``document.pages``."""

    if (
        not isinstance(root_pages, pikepdf.Dictionary)
        or type(page_index) is not int
        or page_index < 0
        or type(maximum_pages) is not int
        or maximum_pages < 1
        or type(maximum_depth) is not int
        or not 1 <= maximum_depth <= MAXIMUM_PAGE_TREE_DEPTH
        or type(maximum_children) is not int
        or not 1 <= maximum_children <= MAXIMUM_PAGE_TREE_CHILDREN
        or type(maximum_nodes) is not int
        or not 1 <= maximum_nodes <= MAXIMUM_PAGE_TREE_NODES
    ):
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.adapter")

    _require_page_tree_type(root_pages, _NAME_PAGES)
    if root_pages.get(_NAME_PARENT) is not None:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    root_identity = _page_tree_identity(root_pages)
    root_frame = _open_page_tree_frame(
        root_pages,
        identity=root_identity,
        depth=1,
        maximum_pages=maximum_pages,
        maximum_children=maximum_children,
    )
    scheduled_nodes = 1
    root_children = len(root_frame.kids)
    if root_children > maximum_nodes - scheduled_nodes:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.geometry.page_tree_nodes")
    scheduled_nodes += root_children

    frames = [root_frame]
    seen = {root_identity}
    selected_page: pikepdf.Dictionary | None = None
    actual_page_count = 0

    while frames:
        frame = frames[-1]
        if frame.next_child_index >= len(frame.kids):
            if frame.actual_page_count != frame.declared_page_count:
                reject_pdf_rendering(
                    PdfRenderErrorCode.GEOMETRY_INVALID,
                    "render.geometry.page_tree",
                )
            completed_page_count = frame.actual_page_count
            frames.pop()
            if frames:
                frames[-1].actual_page_count += completed_page_count
            continue

        child = frame.kids[frame.next_child_index]
        frame.next_child_index += 1
        if not isinstance(child, pikepdf.Dictionary):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
        child_identity = _page_tree_identity(child)
        if child_identity in seen:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
        seen.add(child_identity)

        child_depth = frame.depth + 1
        if child_depth > maximum_depth:
            reject_pdf_rendering(
                PdfRenderErrorCode.LIMIT_EXCEEDED,
                "render.geometry.inheritance_depth",
            )
        parent = child.get(_NAME_PARENT)
        if (
            not isinstance(parent, pikepdf.Dictionary)
            or _page_tree_identity(parent) != frame.identity
        ):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")

        child_type = child.get(_NAME_TYPE)
        if isinstance(child_type, pikepdf.Name) and child_type == _NAME_PAGE:
            actual_page_count += 1
            frame.actual_page_count += 1
            if actual_page_count > maximum_pages:
                reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pages")
            if actual_page_count - 1 == page_index:
                selected_page = child
            continue
        if not isinstance(child_type, pikepdf.Name) or child_type != _NAME_PAGES:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")

        child_frame = _open_page_tree_frame(
            child,
            identity=child_identity,
            depth=child_depth,
            maximum_pages=maximum_pages,
            maximum_children=maximum_children,
        )
        child_count = len(child_frame.kids)
        if child_count > maximum_nodes - scheduled_nodes:
            reject_pdf_rendering(
                PdfRenderErrorCode.LIMIT_EXCEEDED,
                "render.geometry.page_tree_nodes",
            )
        scheduled_nodes += child_count
        frames.append(child_frame)

    if actual_page_count < 1:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    if selected_page is None:
        reject_pdf_rendering(
            PdfRenderErrorCode.PAGE_OUT_OF_RANGE,
            "render.geometry.page_index",
        )
    return selected_page, actual_page_count


def _open_page_tree_frame(
    node: pikepdf.Dictionary,
    *,
    identity: tuple[int, int],
    depth: int,
    maximum_pages: int,
    maximum_children: int,
) -> _PageTreeFrame:
    count_value: object = node.get(_NAME_COUNT)
    if type(count_value) is not int or count_value < 0:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    count = count_value
    if count > maximum_pages:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pages")
    kids = node.get(_NAME_KIDS)
    if not isinstance(kids, pikepdf.Array):
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    if len(kids) > maximum_children:
        reject_pdf_rendering(
            PdfRenderErrorCode.LIMIT_EXCEEDED,
            "render.geometry.page_tree_children",
        )
    return _PageTreeFrame(
        identity=identity,
        kids=kids,
        depth=depth,
        declared_page_count=count,
    )


def _read_inherited_page_values(
    page_object: pikepdf.Dictionary,
    *,
    root_pages: pikepdf.Dictionary,
    maximum_depth: int,
    maximum_children: int,
) -> _InheritedPageValues:
    if not isinstance(page_object, pikepdf.Dictionary) or not isinstance(
        root_pages, pikepdf.Dictionary
    ):
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    if type(maximum_depth) is not int or not 1 <= maximum_depth <= MAXIMUM_PAGE_TREE_DEPTH:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.adapter")
    if type(maximum_children) is not int or not 1 <= maximum_children <= MAXIMUM_PAGE_TREE_CHILDREN:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.adapter")

    media_box: tuple[object, ...] | None = None
    crop_box: tuple[object, ...] | None = None
    rotation: object | None = None
    user_unit = _copy_pdf_number(page_object.get(_NAME_USER_UNIT), allow_none=True)
    seen: set[tuple[int, int]] = set()
    current = page_object
    visited = 0
    root_identity = _page_tree_identity(root_pages)

    while True:
        visited += 1
        if visited > maximum_depth:
            reject_pdf_rendering(
                PdfRenderErrorCode.LIMIT_EXCEEDED,
                "render.geometry.inheritance_depth",
            )
        identity = _page_tree_identity(current)
        if identity in seen:
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.inheritance_cycle",
            )
        seen.add(identity)

        is_root = identity == root_identity
        if visited == 1:
            _require_page_tree_type(current, _NAME_PAGE)
            if is_root:
                reject_pdf_rendering(
                    PdfRenderErrorCode.GEOMETRY_INVALID,
                    "render.geometry.page_tree",
                )
        else:
            _require_page_tree_type(current, _NAME_PAGES)

        if media_box is None:
            value = current.get(_NAME_MEDIA_BOX)
            if value is not None:
                media_box = _copy_pdf_box(value)
        if crop_box is None:
            value = current.get(_NAME_CROP_BOX)
            if value is not None:
                crop_box = _copy_pdf_box(value)
        if rotation is None:
            value = current.get(_NAME_ROTATE)
            if value is not None:
                rotation = _copy_pdf_number(value, allow_none=False)

        parent = current.get(_NAME_PARENT)
        if is_root:
            if parent is not None:
                reject_pdf_rendering(
                    PdfRenderErrorCode.GEOMETRY_INVALID,
                    "render.geometry.page_tree",
                )
            break
        if parent is None:
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.page_tree",
            )
        if not isinstance(parent, pikepdf.Dictionary):
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.parent_type",
            )
        _require_page_tree_type(parent, _NAME_PAGES)
        _require_bounded_kids_membership(
            parent,
            current,
            maximum_children=maximum_children,
        )
        current = parent

    if media_box is None:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.media_box")
    return _InheritedPageValues(
        media_box=media_box,
        crop_box=crop_box,
        rotation=rotation,
        user_unit=user_unit,
    )


def _copy_pdf_box(value: object) -> tuple[object, ...]:
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.box")
    return tuple(_copy_pdf_number(item, allow_none=False) for item in value)


def _copy_pdf_number(value: object | None, *, allow_none: bool) -> object | None:
    if value is None:
        if allow_none:
            return None
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.number")
    if type(value) is int:
        return value
    if type(value) is float:
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.number")
        return value
    reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.number")


def _require_page_tree_type(value: pikepdf.Dictionary, expected: pikepdf.Name) -> None:
    actual = value.get(_NAME_TYPE)
    if not isinstance(actual, pikepdf.Name) or actual != expected:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")


def _require_bounded_kids_membership(
    parent: pikepdf.Dictionary,
    child: pikepdf.Dictionary,
    *,
    maximum_children: int,
) -> None:
    kids = parent.get(_NAME_KIDS)
    if not isinstance(kids, pikepdf.Array):
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    child_count = len(kids)
    if child_count > maximum_children:
        reject_pdf_rendering(
            PdfRenderErrorCode.LIMIT_EXCEEDED,
            "render.geometry.page_tree_children",
        )

    expected_identity = _page_tree_identity(child)
    membership_count = 0
    for candidate in kids:
        if not isinstance(candidate, pikepdf.Dictionary):
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.page_tree",
            )
        candidate_type = candidate.get(_NAME_TYPE)
        if not isinstance(candidate_type, pikepdf.Name) or candidate_type not in (
            _NAME_PAGE,
            _NAME_PAGES,
        ):
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.page_tree",
            )
        if _page_tree_identity(candidate) == expected_identity:
            membership_count += 1
    if membership_count != 1:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")


def _page_tree_identity(value: pikepdf.Dictionary) -> tuple[int, int]:
    object_number, generation = value.objgen
    if (object_number, generation) == (0, 0):
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.page_tree")
    return object_number, generation
