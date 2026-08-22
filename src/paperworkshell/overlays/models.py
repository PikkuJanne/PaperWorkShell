"""Pure typed visual-overlay intent over the released manifest-v1 wire.

The case-manifest v1 and v2 schemas already reserve one closed overlay record
shape.  This module implements that existing contract without adding renderer,
UI, filesystem, or document-engine behavior.  Saved boxes always remain full-
page normalized intent; visibility and crop intersection are derived views.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Self, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    SUPPORTED_MANIFEST_SCHEMA_VERSIONS,
    copy_manifest_document,
)
from paperworkshell.domain.page_coordinates import (
    NormalizedPageBox,
    PageCoordinateError,
)

OVERLAY_VERSION: Final = 1
MAX_OVERLAY_ASSETS: Final = 4_096
MAX_OVERLAYS: Final = 4_096
MAX_OVERLAY_TEXT_CODE_POINTS: Final = 4_096
MAX_FONT_TOKEN_CODE_POINTS: Final = 128
MIN_FONT_SIZE_POINTS: Final = 1
MAX_FONT_SIZE_POINTS: Final = 288
DEFAULT_FONT_TOKEN: Final = "paperworkshell-default"
SIGNATURE_IMAGE_TERMINOLOGY: Final = "visual_mark_not_digital_signature"
ORDINARY_SHAPE_INTENT: Final = "visual_overlay_only_not_permanent_removal"

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_UUID_V4_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_SHA256_EXACT: Final = re.compile(r"[0-9a-f]{64}\Z")
_STORAGE_NAME_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.bin\Z"
)
_FONT_TOKEN_EXACT: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_RGBA_EXACT: Final = re.compile(r"#[0-9a-fA-F]{8}\Z")

_ASSET_MEMBERS: Final = frozenset(
    {"asset_id", "kind", "storage_name", "media_type", "byte_size", "sha256"}
)
_OVERLAY_MEMBERS: Final = frozenset(
    {"overlay_id", "overlay_version", "page_id", "box", "z_index", "payload"}
)
_TEXT_PAYLOAD_MEMBERS: Final = frozenset(
    {"kind", "value", "font_token", "font_size_points", "color"}
)
_MARK_PAYLOAD_MEMBERS: Final = frozenset({"kind", "color"})
_SIGNATURE_PAYLOAD_MEMBERS: Final = frozenset({"kind", "asset_id", "terminology"})
_SHAPE_PAYLOAD_MEMBERS: Final = frozenset({"kind", "shape", "fill_color", "stroke_color", "intent"})


class OverlayErrorCode(StrEnum):
    """Closed registry of non-disclosing overlay failures."""

    MODEL_INVALID = "OVERLAY_MODEL_INVALID"
    MANIFEST_INVALID = "OVERLAY_MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "OVERLAY_VERSION_UNSUPPORTED"
    CAPACITY_EXCEEDED = "OVERLAY_CAPACITY_EXCEEDED"
    IDENTIFIER_INVALID = "OVERLAY_IDENTIFIER_INVALID"
    IDENTIFIER_COLLISION = "OVERLAY_IDENTIFIER_COLLISION"
    PAGE_UNKNOWN = "OVERLAY_PAGE_UNKNOWN"
    BOX_INVALID = "OVERLAY_BOX_INVALID"
    Z_INDEX_INVALID = "OVERLAY_Z_INDEX_INVALID"
    Z_INDEX_COLLISION = "OVERLAY_Z_INDEX_COLLISION"
    PAYLOAD_UNSUPPORTED = "OVERLAY_PAYLOAD_UNSUPPORTED"
    TEXT_INVALID = "OVERLAY_TEXT_INVALID"
    FONT_INVALID = "OVERLAY_FONT_INVALID"
    FONT_SIZE_INVALID = "OVERLAY_FONT_SIZE_INVALID"
    COLOR_INVALID = "OVERLAY_COLOR_INVALID"
    ASSET_INVALID = "OVERLAY_ASSET_INVALID"
    ASSET_UNKNOWN = "OVERLAY_ASSET_UNKNOWN"
    ASSET_INCOMPATIBLE = "OVERLAY_ASSET_INCOMPATIBLE"
    SIGNATURE_TERMINOLOGY_INVALID = "OVERLAY_SIGNATURE_TERMINOLOGY_INVALID"
    SHAPE_INVALID = "OVERLAY_SHAPE_INVALID"
    SHAPE_INTENT_INVALID = "OVERLAY_SHAPE_INTENT_INVALID"


_SAFE_ERROR_MESSAGES: Final[dict[OverlayErrorCode, str]] = {
    OverlayErrorCode.MODEL_INVALID: "PaperWorkShell received invalid overlay data.",
    OverlayErrorCode.MANIFEST_INVALID: ("The saved overlay record is invalid and was not changed."),
    OverlayErrorCode.VERSION_UNSUPPORTED: (
        "This case uses an unsupported overlay version and was not changed."
    ),
    OverlayErrorCode.CAPACITY_EXCEEDED: (
        "The case has reached the supported visual-overlay limit."
    ),
    OverlayErrorCode.IDENTIFIER_INVALID: ("A visual overlay has an invalid internal identifier."),
    OverlayErrorCode.IDENTIFIER_COLLISION: (
        "A visual overlay conflicts with another case identifier."
    ),
    OverlayErrorCode.PAGE_UNKNOWN: ("A visual overlay refers to a page that is not in this case."),
    OverlayErrorCode.BOX_INVALID: ("The visual overlay area is outside the normalized page."),
    OverlayErrorCode.Z_INDEX_INVALID: (
        "The visual-overlay layer number is outside the supported range."
    ),
    OverlayErrorCode.Z_INDEX_COLLISION: ("Two visual overlays use the same layer on one page."),
    OverlayErrorCode.PAYLOAD_UNSUPPORTED: ("This visual-overlay type is not supported."),
    OverlayErrorCode.TEXT_INVALID: "The visual-overlay text value is too large.",
    OverlayErrorCode.FONT_INVALID: "The visual-overlay font token is invalid.",
    OverlayErrorCode.FONT_SIZE_INVALID: (
        "Choose a visual-overlay font size within the supported range."
    ),
    OverlayErrorCode.COLOR_INVALID: "The visual-overlay color is invalid.",
    OverlayErrorCode.ASSET_INVALID: "The visual-overlay image asset is invalid.",
    OverlayErrorCode.ASSET_UNKNOWN: (
        "The visual overlay refers to an image asset that is not in this case."
    ),
    OverlayErrorCode.ASSET_INCOMPATIBLE: (
        "The referenced case asset cannot be used by this visual overlay."
    ),
    OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID: (
        "A placed signature image cannot claim cryptographic signature status."
    ),
    OverlayErrorCode.SHAPE_INVALID: "The ordinary visual shape is invalid.",
    OverlayErrorCode.SHAPE_INTENT_INVALID: (
        "An ordinary visual shape cannot claim permanent removal of information."
    ),
}


@dataclass(slots=True, repr=False)
class OverlayError(ValueError):
    """Stable overlay failure that never includes form values or asset details."""

    code: OverlayErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not OverlayErrorCode:
            self.code = OverlayErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "overlay.model"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"OverlayError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_overlay(code: OverlayErrorCode, context: str) -> Never:
    """Raise one closed, non-disclosing overlay failure."""

    raise OverlayError(code=code, context=context)


class OverlayKind(StrEnum):
    TEXT = "text"
    DATE = "date"
    CHECKMARK = "checkmark"
    CROSS = "cross"
    INITIALS = "initials"
    SIGNATURE_IMAGE = "signature_image"
    HIGHLIGHT = "highlight"
    SHAPE = "shape"


class ShapePrimitive(StrEnum):
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"


class OverlayAssetKind(StrEnum):
    SIGNATURE_IMAGE = "signature_image"


class OverlayAssetMediaType(StrEnum):
    JPEG = "image/jpeg"
    PNG = "image/png"


class OverlayVisibility(StrEnum):
    """Derived visibility reason; never persisted in a case manifest."""

    VISIBLE = "visible"
    PARTIALLY_VISIBLE = "partially_visible"
    PAGE_INACTIVE = "page_inactive"
    OUTSIDE_ACTIVE_CROP = "outside_active_crop"


def _copy_mapping(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    try:
        copied = copy_manifest_document(value, context=context)
    except BaseException:
        copied = None
    if copied is None:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, context)
    return copied


def _parse_uuid(value: object, *, context: str) -> UUID:
    if type(value) is not str or _UUID_V4_EXACT.fullmatch(value) is None:
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        parsed = None
    if parsed is None:
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    if parsed.version != 4 or str(parsed) != value:
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    return parsed


def _require_uuid(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4 or str(value) != str(value).lower():
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    return value


def _require_font_token(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_FONT_TOKEN_CODE_POINTS
        or _FONT_TOKEN_EXACT.fullmatch(value) is None
    ):
        reject_overlay(OverlayErrorCode.FONT_INVALID, "overlay.font")
    return value


def _require_text(value: object) -> str:
    if type(value) is not str or len(value) > MAX_OVERLAY_TEXT_CODE_POINTS:
        reject_overlay(OverlayErrorCode.TEXT_INVALID, "overlay.text")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class RgbaColor:
    """One exact released ``#rrggbbaa`` value, retaining legacy letter case."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _RGBA_EXACT.fullmatch(self.value) is None:
            reject_overlay(OverlayErrorCode.COLOR_INVALID, "overlay.color")

    def __repr__(self) -> str:
        return "RgbaColor(valid=True)"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        if type(value) is not str:
            reject_overlay(OverlayErrorCode.COLOR_INVALID, "overlay.color")
        return cls(value)

    def to_manifest(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, repr=False)
class TextualOverlayPayload:
    """Inert text, date, or initials display value with no formatting inference."""

    kind: OverlayKind
    value: str
    font_token: str
    font_size_points: int
    color: RgbaColor

    def __post_init__(self) -> None:
        if type(self.kind) is not OverlayKind or self.kind not in {
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.INITIALS,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")
        _require_text(self.value)
        _require_font_token(self.font_token)
        if (
            type(self.font_size_points) is not int
            or not MIN_FONT_SIZE_POINTS <= self.font_size_points <= MAX_FONT_SIZE_POINTS
        ):
            reject_overlay(OverlayErrorCode.FONT_SIZE_INVALID, "overlay.font_size")
        if type(self.color) is not RgbaColor:
            reject_overlay(OverlayErrorCode.COLOR_INVALID, "overlay.color")

    def __repr__(self) -> str:
        return (
            "TextualOverlayPayload("
            f"kind={self.kind.value!r}, font_size_points={self.font_size_points!r})"
        )

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.text_payload")
        if set(copied) != _TEXT_PAYLOAD_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.text_payload")
        raw_kind = copied["kind"]
        if type(raw_kind) is not str or raw_kind not in {
            OverlayKind.TEXT.value,
            OverlayKind.DATE.value,
            OverlayKind.INITIALS.value,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.text_payload")
        return cls(
            OverlayKind(raw_kind),
            _require_text(copied["value"]),
            _require_font_token(copied["font_token"]),
            cast(int, copied["font_size_points"]),
            RgbaColor.from_manifest(copied["color"]),
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "font_token": self.font_token,
            "font_size_points": self.font_size_points,
            "color": self.color.to_manifest(),
        }


@dataclass(frozen=True, slots=True, repr=False)
class MarkOverlayPayload:
    """One checkmark or cross visual mark."""

    kind: OverlayKind
    color: RgbaColor

    def __post_init__(self) -> None:
        if type(self.kind) is not OverlayKind or self.kind not in {
            OverlayKind.CHECKMARK,
            OverlayKind.CROSS,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")
        if type(self.color) is not RgbaColor:
            reject_overlay(OverlayErrorCode.COLOR_INVALID, "overlay.color")

    def __repr__(self) -> str:
        return f"MarkOverlayPayload(kind={self.kind.value!r})"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.mark_payload")
        if set(copied) != _MARK_PAYLOAD_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.mark_payload")
        raw_kind = copied["kind"]
        if type(raw_kind) is not str or raw_kind not in {
            OverlayKind.CHECKMARK.value,
            OverlayKind.CROSS.value,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.mark_payload")
        return cls(OverlayKind(raw_kind), RgbaColor.from_manifest(copied["color"]))

    def to_manifest(self) -> dict[str, object]:
        return {"kind": self.kind.value, "color": self.color.to_manifest()}


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImageOverlayPayload:
    """A visual image mark that explicitly is not a digital signature."""

    asset_id: UUID
    terminology: str = SIGNATURE_IMAGE_TERMINOLOGY

    def __post_init__(self) -> None:
        _require_uuid(self.asset_id, context="overlay.signature_asset")
        if type(self.terminology) is not str or self.terminology != SIGNATURE_IMAGE_TERMINOLOGY:
            reject_overlay(
                OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID,
                "overlay.signature_terminology",
            )

    @property
    def kind(self) -> OverlayKind:
        return OverlayKind.SIGNATURE_IMAGE

    def __repr__(self) -> str:
        return "SignatureImageOverlayPayload(terminology='visual-only')"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.signature_payload")
        if set(copied) != _SIGNATURE_PAYLOAD_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_payload")
        raw_kind = copied["kind"]
        if type(raw_kind) is not str or raw_kind != OverlayKind.SIGNATURE_IMAGE.value:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.signature_payload")
        terminology = copied["terminology"]
        if type(terminology) is not str or terminology != SIGNATURE_IMAGE_TERMINOLOGY:
            reject_overlay(
                OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID,
                "overlay.signature_terminology",
            )
        return cls(_parse_uuid(copied["asset_id"], context="overlay.signature_asset"))

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "asset_id": str(self.asset_id),
            "terminology": SIGNATURE_IMAGE_TERMINOLOGY,
        }


@dataclass(frozen=True, slots=True, repr=False)
class ShapeOverlayPayload:
    """An ordinary highlight or shape with no permanent-removal semantics."""

    kind: OverlayKind
    shape: ShapePrimitive
    fill_color: RgbaColor
    stroke_color: RgbaColor
    intent: str = ORDINARY_SHAPE_INTENT

    def __post_init__(self) -> None:
        if type(self.kind) is not OverlayKind or self.kind not in {
            OverlayKind.HIGHLIGHT,
            OverlayKind.SHAPE,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")
        if type(self.shape) is not ShapePrimitive:
            reject_overlay(OverlayErrorCode.SHAPE_INVALID, "overlay.shape")
        if type(self.fill_color) is not RgbaColor or type(self.stroke_color) is not RgbaColor:
            reject_overlay(OverlayErrorCode.COLOR_INVALID, "overlay.color")
        if type(self.intent) is not str or self.intent != ORDINARY_SHAPE_INTENT:
            reject_overlay(OverlayErrorCode.SHAPE_INTENT_INVALID, "overlay.shape_intent")

    def __repr__(self) -> str:
        return f"ShapeOverlayPayload(kind={self.kind.value!r}, shape={self.shape.value!r})"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.shape_payload")
        if set(copied) != _SHAPE_PAYLOAD_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.shape_payload")
        raw_kind = copied["kind"]
        if type(raw_kind) is not str or raw_kind not in {
            OverlayKind.HIGHLIGHT.value,
            OverlayKind.SHAPE.value,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.shape_payload")
        raw_shape = copied["shape"]
        if type(raw_shape) is not str:
            reject_overlay(OverlayErrorCode.SHAPE_INVALID, "overlay.shape")
        try:
            shape = ShapePrimitive(raw_shape)
        except ValueError:
            shape = None
        if shape is None:
            reject_overlay(OverlayErrorCode.SHAPE_INVALID, "overlay.shape")
        intent = copied["intent"]
        if type(intent) is not str or intent != ORDINARY_SHAPE_INTENT:
            reject_overlay(OverlayErrorCode.SHAPE_INTENT_INVALID, "overlay.shape_intent")
        return cls(
            OverlayKind(raw_kind),
            shape,
            RgbaColor.from_manifest(copied["fill_color"]),
            RgbaColor.from_manifest(copied["stroke_color"]),
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "shape": self.shape.value,
            "fill_color": self.fill_color.to_manifest(),
            "stroke_color": self.stroke_color.to_manifest(),
            "intent": ORDINARY_SHAPE_INTENT,
        }


type OverlayPayload = (
    TextualOverlayPayload | MarkOverlayPayload | SignatureImageOverlayPayload | ShapeOverlayPayload
)


def overlay_payload_from_manifest(value: object) -> OverlayPayload:
    """Decode exactly one released overlay-v1 payload variant."""

    copied = _copy_mapping(value, context="overlay.payload")
    kind = copied.get("kind")
    if type(kind) is not str:
        reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")
    if kind in {
        OverlayKind.TEXT.value,
        OverlayKind.DATE.value,
        OverlayKind.INITIALS.value,
    }:
        return TextualOverlayPayload.from_manifest(copied)
    if kind in {OverlayKind.CHECKMARK.value, OverlayKind.CROSS.value}:
        return MarkOverlayPayload.from_manifest(copied)
    if kind == OverlayKind.SIGNATURE_IMAGE.value:
        return SignatureImageOverlayPayload.from_manifest(copied)
    if kind in {OverlayKind.HIGHLIGHT.value, OverlayKind.SHAPE.value}:
        return ShapeOverlayPayload.from_manifest(copied)
    reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")


@dataclass(frozen=True, slots=True, repr=False)
class OverlayAssetRecord:
    """Case-internal signature-image asset metadata without paths or image bytes."""

    asset_id: UUID
    kind: OverlayAssetKind
    storage_name: str
    media_type: OverlayAssetMediaType
    byte_size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_uuid(self.asset_id, context="overlay.asset_id")
        if type(self.kind) is not OverlayAssetKind:
            reject_overlay(OverlayErrorCode.ASSET_INCOMPATIBLE, "overlay.asset_kind")
        if (
            type(self.storage_name) is not str
            or _STORAGE_NAME_EXACT.fullmatch(self.storage_name) is None
            or self.storage_name != f"{self.asset_id}.bin"
        ):
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_storage")
        if type(self.media_type) is not OverlayAssetMediaType:
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_media")
        if type(self.byte_size) is not int or not 1 <= self.byte_size <= MAX_SAFE_MANIFEST_INTEGER:
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_size")
        if type(self.sha256) is not str or _SHA256_EXACT.fullmatch(self.sha256) is None:
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_digest")

    def __repr__(self) -> str:
        return "OverlayAssetRecord(kind='signature_image', details_hidden=True)"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.asset")
        if set(copied) != _ASSET_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.asset")
        raw_kind = copied["kind"]
        if type(raw_kind) is not str or raw_kind != OverlayAssetKind.SIGNATURE_IMAGE.value:
            reject_overlay(OverlayErrorCode.ASSET_INCOMPATIBLE, "overlay.asset_kind")
        media_type = copied["media_type"]
        if type(media_type) is not str:
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_media")
        try:
            selected_media_type = OverlayAssetMediaType(media_type)
        except ValueError:
            selected_media_type = None
        if selected_media_type is None:
            reject_overlay(OverlayErrorCode.ASSET_INVALID, "overlay.asset_media")
        asset_id = _parse_uuid(copied["asset_id"], context="overlay.asset_id")
        return cls(
            asset_id,
            OverlayAssetKind.SIGNATURE_IMAGE,
            cast(str, copied["storage_name"]),
            selected_media_type,
            cast(int, copied["byte_size"]),
            cast(str, copied["sha256"]),
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "asset_id": str(self.asset_id),
            "kind": self.kind.value,
            "storage_name": self.storage_name,
            "media_type": self.media_type.value,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True, repr=False)
class OverlayRecord:
    """One immutable visual intent record in full-page normalized coordinates."""

    overlay_id: UUID
    page_id: UUID
    box: NormalizedPageBox
    z_index: int
    payload: OverlayPayload
    overlay_version: int = OVERLAY_VERSION

    def __post_init__(self) -> None:
        _require_uuid(self.overlay_id, context="overlay.overlay_id")
        _require_uuid(self.page_id, context="overlay.page_id")
        if type(self.overlay_version) is not int or self.overlay_version != OVERLAY_VERSION:
            reject_overlay(OverlayErrorCode.VERSION_UNSUPPORTED, "overlay.version")
        if type(self.box) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.box")
        if type(self.z_index) is not int or not 0 <= self.z_index <= MAX_SAFE_MANIFEST_INTEGER:
            reject_overlay(OverlayErrorCode.Z_INDEX_INVALID, "overlay.z_index")
        if type(self.payload) not in {
            TextualOverlayPayload,
            MarkOverlayPayload,
            SignatureImageOverlayPayload,
            ShapeOverlayPayload,
        }:
            reject_overlay(OverlayErrorCode.PAYLOAD_UNSUPPORTED, "overlay.payload")

    @property
    def kind(self) -> OverlayKind:
        return self.payload.kind

    def __repr__(self) -> str:
        return f"OverlayRecord(kind={self.kind.value!r}, z_index={self.z_index!r})"

    @classmethod
    def from_manifest(cls, value: object) -> Self:
        copied = _copy_mapping(value, context="overlay.record")
        if set(copied) != _OVERLAY_MEMBERS:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.record")
        version = copied["overlay_version"]
        if type(version) is not int or version != OVERLAY_VERSION:
            reject_overlay(OverlayErrorCode.VERSION_UNSUPPORTED, "overlay.version")
        raw_box = copied["box"]
        if not isinstance(raw_box, Mapping):
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.box")
        try:
            box = NormalizedPageBox.from_manifest(raw_box)
        except PageCoordinateError:
            box = None
        if box is None:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.box")
        return cls(
            _parse_uuid(copied["overlay_id"], context="overlay.overlay_id"),
            _parse_uuid(copied["page_id"], context="overlay.page_id"),
            box,
            cast(int, copied["z_index"]),
            overlay_payload_from_manifest(copied["payload"]),
            version,
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "overlay_id": str(self.overlay_id),
            "overlay_version": self.overlay_version,
            "page_id": str(self.page_id),
            "box": self.box.to_manifest(),
            "z_index": self.z_index,
            "payload": self.payload.to_manifest(),
        }


@dataclass(frozen=True, slots=True, repr=False)
class OverlayProjection:
    """One derived crop/page visibility result that never changes saved intent."""

    record: OverlayRecord
    visibility: OverlayVisibility
    visible_box: NormalizedPageBox | None

    def __post_init__(self) -> None:
        if type(self.record) is not OverlayRecord or type(self.visibility) is not OverlayVisibility:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.projection")
        if self.visibility is OverlayVisibility.VISIBLE:
            if (
                type(self.visible_box) is not NormalizedPageBox
                or self.visible_box != self.record.box
            ):
                reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.projection")
        elif self.visibility is OverlayVisibility.PARTIALLY_VISIBLE:
            if (
                type(self.visible_box) is not NormalizedPageBox
                or self.visible_box == self.record.box
                or not self.record.box.contains_box(self.visible_box)
            ):
                reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.projection")
        elif self.visible_box is not None:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.projection")

    def __repr__(self) -> str:
        return (
            "OverlayProjection("
            f"kind={self.record.kind.value!r}, visibility={self.visibility.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False, init=False)
class OverlayCollection:
    """Validated overlay graph plus deterministic order and derived visibility."""

    assets: tuple[OverlayAssetRecord, ...]
    overlays: tuple[OverlayRecord, ...]
    _known_page_ids: frozenset[UUID] = field(repr=False)
    _active_page_ids: frozenset[UUID] = field(repr=False)

    def __init__(
        self,
        assets: Sequence[OverlayAssetRecord],
        overlays: Sequence[OverlayRecord],
        *,
        known_page_ids: Sequence[UUID],
        active_page_ids: Sequence[UUID],
    ) -> None:
        if type(assets) not in {tuple, list} or type(overlays) not in {tuple, list}:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.collection")
        if type(known_page_ids) not in {tuple, list} or type(active_page_ids) not in {tuple, list}:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.pages")
        selected_assets = tuple(assets)
        selected_overlays = tuple(overlays)
        selected_known = tuple(known_page_ids)
        selected_active = tuple(active_page_ids)
        if (
            len(selected_assets) > MAX_OVERLAY_ASSETS
            or len(selected_overlays) > MAX_OVERLAYS
            or len(selected_known) > MAX_OVERLAYS
            or len(selected_active) > MAX_OVERLAYS
        ):
            reject_overlay(OverlayErrorCode.CAPACITY_EXCEEDED, "overlay.collection")
        if any(type(item) is not OverlayAssetRecord for item in selected_assets):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.assets")
        if any(type(item) is not OverlayRecord for item in selected_overlays):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.records")
        known = _unique_uuid_set(selected_known, context="overlay.known_pages")
        active = _unique_uuid_set(selected_active, context="overlay.active_pages")
        if not active <= known:
            reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.active_pages")

        namespace = set(known)
        asset_index: dict[UUID, OverlayAssetRecord] = {}
        for asset in selected_assets:
            if asset.asset_id in namespace:
                reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.asset_id")
            namespace.add(asset.asset_id)
            asset_index[asset.asset_id] = asset

        z_indices: set[tuple[UUID, int]] = set()
        for record in selected_overlays:
            if record.overlay_id in namespace:
                reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.overlay_id")
            namespace.add(record.overlay_id)
            if record.page_id not in known:
                reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.page_id")
            z_key = (record.page_id, record.z_index)
            if z_key in z_indices:
                reject_overlay(OverlayErrorCode.Z_INDEX_COLLISION, "overlay.z_index")
            z_indices.add(z_key)
            if type(record.payload) is SignatureImageOverlayPayload:
                resolved_asset = asset_index.get(record.payload.asset_id)
                if resolved_asset is None:
                    reject_overlay(OverlayErrorCode.ASSET_UNKNOWN, "overlay.signature_asset")
                if resolved_asset.kind is not OverlayAssetKind.SIGNATURE_IMAGE:
                    reject_overlay(
                        OverlayErrorCode.ASSET_INCOMPATIBLE,
                        "overlay.signature_asset",
                    )

        object.__setattr__(self, "assets", selected_assets)
        object.__setattr__(self, "overlays", selected_overlays)
        object.__setattr__(self, "_known_page_ids", frozenset(known))
        object.__setattr__(self, "_active_page_ids", frozenset(active))

    def __repr__(self) -> str:
        return (
            f"OverlayCollection(asset_count={len(self.assets)}, overlay_count={len(self.overlays)})"
        )

    @classmethod
    def from_manifest_document(cls, document: Mapping[str, object]) -> Self:
        """Decode the overlay surfaces of one fully schema-valid v1/v2 manifest."""

        copied = _copy_mapping(document, context="overlay.manifest")
        version = copied.get("schema_version")
        if type(version) is not int:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_version")
        if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            reject_overlay(OverlayErrorCode.VERSION_UNSUPPORTED, "overlay.manifest_version")
        pages = copied.get("pages")
        active_pages = copied.get("page_sequence")
        assets = copied.get("assets")
        overlays = copied.get("overlays")
        if type(pages) is not list or type(active_pages) is not list:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_pages")
        if type(assets) is not list or type(overlays) is not list:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.manifest_records")
        if (
            len(pages) > MAX_OVERLAYS
            or len(active_pages) > MAX_OVERLAYS
            or len(assets) > MAX_OVERLAY_ASSETS
            or len(overlays) > MAX_OVERLAYS
        ):
            reject_overlay(OverlayErrorCode.CAPACITY_EXCEEDED, "overlay.manifest_records")
        known_page_ids: list[UUID] = []
        for page in pages:
            page_record = _copy_mapping(page, context="overlay.manifest_page")
            known_page_ids.append(
                _parse_uuid(page_record.get("page_id"), context="overlay.page_id")
            )
        selected_active = [
            _parse_uuid(page_id, context="overlay.page_id") for page_id in active_pages
        ]
        selected_assets = [OverlayAssetRecord.from_manifest(asset) for asset in assets]
        selected_overlays = [OverlayRecord.from_manifest(record) for record in overlays]
        return cls(
            selected_assets,
            selected_overlays,
            known_page_ids=known_page_ids,
            active_page_ids=selected_active,
        )

    def to_manifest_assets(self) -> list[dict[str, object]]:
        """Return fresh asset records in their persisted array order."""

        return [asset.to_manifest() for asset in self.assets]

    def to_manifest_overlays(self) -> list[dict[str, object]]:
        """Return fresh overlay records in their persisted array order."""

        return [record.to_manifest() for record in self.overlays]

    def stack_for_page(self, page_id: UUID) -> tuple[OverlayRecord, ...]:
        """Return low-to-high sparse z-order; source array order has no draw meaning."""

        selected = _require_uuid(page_id, context="overlay.page_id")
        if selected not in self._known_page_ids:
            reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.page_id")
        return tuple(
            sorted(
                (record for record in self.overlays if record.page_id == selected),
                key=lambda record: record.z_index,
            )
        )

    def projections_for_page(
        self,
        page_id: UUID,
        *,
        active_crop: NormalizedPageBox,
    ) -> tuple[OverlayProjection, ...]:
        """Derive page/crop visibility without clipping or moving saved records."""

        selected = _require_uuid(page_id, context="overlay.page_id")
        if type(active_crop) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.active_crop")
        records = self.stack_for_page(selected)
        if selected not in self._active_page_ids:
            return tuple(
                OverlayProjection(record, OverlayVisibility.PAGE_INACTIVE, None)
                for record in records
            )
        projections: list[OverlayProjection] = []
        for record in records:
            visible_box = active_crop.intersection(record.box)
            if visible_box is None:
                projections.append(
                    OverlayProjection(
                        record,
                        OverlayVisibility.OUTSIDE_ACTIVE_CROP,
                        None,
                    )
                )
            else:
                visibility = (
                    OverlayVisibility.VISIBLE
                    if visible_box == record.box
                    else OverlayVisibility.PARTIALLY_VISIBLE
                )
                projections.append(OverlayProjection(record, visibility, visible_box))
        return tuple(projections)

    def visible_stack(
        self,
        page_id: UUID,
        *,
        active_crop: NormalizedPageBox,
    ) -> tuple[OverlayProjection, ...]:
        """Return only visible low-to-high projections for a page."""

        return tuple(
            projection
            for projection in self.projections_for_page(page_id, active_crop=active_crop)
            if projection.visibility
            in {
                OverlayVisibility.VISIBLE,
                OverlayVisibility.PARTIALLY_VISIBLE,
            }
        )


def _unique_uuid_set(values: Sequence[UUID], *, context: str) -> set[UUID]:
    selected: set[UUID] = set()
    for value in values:
        parsed = _require_uuid(value, context=context)
        if parsed in selected:
            reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, context)
        selected.add(parsed)
    return selected
