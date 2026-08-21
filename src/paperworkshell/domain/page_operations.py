"""Pure page-operation replay, commands, and versioned manifest history integration.

The module deliberately owns no paths and performs no I/O.  It treats even a
``ManifestSnapshot`` as an untrusted boundary, reconstructs the relevant page
graph defensively, and raises only closed, non-disclosing failures.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID, uuid4

from paperworkshell.domain.image_transforms import (
    IDENTITY_CONTRAST_FACTOR_MILLIONTHS as _NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
)
from paperworkshell.domain.image_transforms import (
    MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS as _MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
)
from paperworkshell.domain.image_transforms import (
    MAX_CONTRAST_FACTOR_MILLIONTHS as _MAX_CONTRAST_FACTOR_MILLIONTHS,
)
from paperworkshell.domain.image_transforms import (
    MAX_DESKEW_ANGLE_MILLIDEGREES as _MAX_DESKEW_ANGLE_MILLIDEGREES,
)
from paperworkshell.domain.manifest_repository import (
    ManifestSnapshot,
    copy_manifest_document,
)
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS as NORMALIZED_PAGE_MILLIONTHS,
)
from paperworkshell.domain.page_coordinates import (
    NormalizedPageBox as NormalizedPageBox,
)
from paperworkshell.domain.page_coordinates import (
    PageCoordinateError,
)

MAX_PAGE_COUNT: Final = 4_096
MAX_PAGE_OPERATION_COUNT: Final = 4_096
MAX_REPLAY_MEMBERS: Final = 100_000
MAX_SAFE_MANIFEST_INTEGER: Final = 9_007_199_254_740_991
LEGACY_PAGE_OPERATION_VERSION: Final = 1
SCAN_TRANSFORM_OPERATION_VERSION: Final = 2
# Kept as the default for the released organizer operations. New scan-transform
# payloads explicitly select ``SCAN_TRANSFORM_OPERATION_VERSION``.
PAGE_OPERATION_VERSION: Final = LEGACY_PAGE_OPERATION_VERSION
NEUTRAL_CONTRAST_FACTOR_MILLIONTHS: Final = _NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
MAX_CONTRAST_FACTOR_MILLIONTHS: Final = _MAX_CONTRAST_FACTOR_MILLIONTHS
MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS: Final = _MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
MAX_DESKEW_ANGLE_MILLIDEGREES: Final = _MAX_DESKEW_ANGLE_MILLIDEGREES
MAX_ID_GENERATION_ATTEMPTS: Final = 32

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_UUID_V4_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_ROOT_MEMBERS: Final = frozenset(
    {
        "schema_version",
        "case_id",
        "revision",
        "created_at",
        "updated_at",
        "template_ref",
        "sources",
        "pages",
        "page_sequence_base",
        "page_sequence",
        "page_operations",
        "assets",
        "overlays",
        "assignments",
        "constraints",
        "confirmations",
        "export_history",
    }
)
_SOURCE_MEMBERS: Final = frozenset(
    {
        "source_id",
        "display_name",
        "storage_name",
        "detected_media_type",
        "byte_size",
        "page_count",
        "sha256",
        "signature_state",
        "encryption_state",
        "import_state",
        "imported_at",
    }
)
_PAGE_MEMBERS: Final = frozenset({"page_id", "source_id", "source_page_index"})
_OPERATION_MEMBERS: Final = frozenset({"operation_id", "operation_version", "sequence", "payload"})
_ROTATE_MEMBERS: Final = frozenset({"kind", "page_id", "quarter_turns_clockwise"})
_CROP_MEMBERS: Final = frozenset({"kind", "page_id", "box"})
_SEQUENCE_MEMBERS: Final = frozenset({"kind", "reason", "before_page_ids", "after_page_ids"})
_GROUP_MEMBERS: Final = frozenset({"kind", "group_id", "page_ids"})
_DESKEW_MEMBERS: Final = frozenset({"kind", "page_id", "angle_millidegrees_clockwise"})
_CONTRAST_MEMBERS: Final = frozenset({"kind", "page_id", "factor_millionths"})
_BACKGROUND_CLEANUP_MEMBERS: Final = frozenset({"kind", "page_id", "strength_millionths"})


class PageOperationErrorCode(StrEnum):
    """Closed registry of sanitized page-domain failures."""

    MODEL_INVALID = "PAGE_OPERATION_MODEL_INVALID"
    MANIFEST_INVALID = "PAGE_OPERATION_MANIFEST_INVALID"
    VERSION_UNSUPPORTED = "PAGE_OPERATION_VERSION_UNSUPPORTED"
    CAPACITY_EXCEEDED = "PAGE_OPERATION_CAPACITY_EXCEEDED"
    IDENTIFIER_INVALID = "PAGE_OPERATION_IDENTIFIER_INVALID"
    IDENTIFIER_COLLISION = "PAGE_OPERATION_IDENTIFIER_COLLISION"
    PAGE_UNKNOWN = "PAGE_OPERATION_PAGE_UNKNOWN"
    PAGE_DUPLICATE = "PAGE_OPERATION_PAGE_DUPLICATE"
    PAGE_NOT_INTRODUCED = "PAGE_OPERATION_PAGE_NOT_INTRODUCED"
    PAGE_INTRODUCTION_INCOMPLETE = "PAGE_OPERATION_INTRODUCTION_INCOMPLETE"
    OPERATION_SEQUENCE_INVALID = "PAGE_OPERATION_SEQUENCE_INVALID"
    SEQUENCE_MISMATCH = "PAGE_OPERATION_SEQUENCE_MISMATCH"
    SEQUENCE_REASON_INVALID = "PAGE_OPERATION_SEQUENCE_REASON_INVALID"
    ROTATION_INVALID = "PAGE_OPERATION_ROTATION_INVALID"
    CROP_INVALID = "PAGE_OPERATION_CROP_INVALID"
    DESKEW_INVALID = "PAGE_OPERATION_DESKEW_INVALID"
    CONTRAST_INVALID = "PAGE_OPERATION_CONTRAST_INVALID"
    BACKGROUND_CLEANUP_INVALID = "PAGE_OPERATION_BACKGROUND_CLEANUP_INVALID"
    GROUP_EMPTY = "PAGE_OPERATION_GROUP_EMPTY"
    UNDO_UNAVAILABLE = "PAGE_OPERATION_UNDO_UNAVAILABLE"
    REDO_UNAVAILABLE = "PAGE_OPERATION_REDO_UNAVAILABLE"
    CONCURRENT_CHANGE = "PAGE_OPERATION_CONCURRENT_CHANGE"
    ID_GENERATION_FAILED = "PAGE_OPERATION_ID_GENERATION_FAILED"


_SAFE_ERROR_MESSAGES: Final[dict[PageOperationErrorCode, str]] = {
    PageOperationErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid page-operation data."),
    PageOperationErrorCode.MANIFEST_INVALID: (
        "The case page record is invalid and was not changed."
    ),
    PageOperationErrorCode.VERSION_UNSUPPORTED: (
        "This case uses an unsupported page-operation version and was not changed."
    ),
    PageOperationErrorCode.CAPACITY_EXCEEDED: (
        "The case has reached the supported page-operation limit."
    ),
    PageOperationErrorCode.IDENTIFIER_INVALID: (
        "A page operation has an invalid internal identifier."
    ),
    PageOperationErrorCode.IDENTIFIER_COLLISION: (
        "A page operation conflicts with an existing internal identifier."
    ),
    PageOperationErrorCode.PAGE_UNKNOWN: (
        "A page operation refers to a page that is not in this case."
    ),
    PageOperationErrorCode.PAGE_DUPLICATE: (
        "A page sequence contains the same page more than once."
    ),
    PageOperationErrorCode.PAGE_NOT_INTRODUCED: (
        "A page operation occurs before that page enters the case sequence."
    ),
    PageOperationErrorCode.PAGE_INTRODUCTION_INCOMPLETE: (
        "The page-operation history does not introduce every case page."
    ),
    PageOperationErrorCode.OPERATION_SEQUENCE_INVALID: (
        "Page operations are not in one complete contiguous order."
    ),
    PageOperationErrorCode.SEQUENCE_MISMATCH: (
        "A page operation does not match the preceding page order."
    ),
    PageOperationErrorCode.SEQUENCE_REASON_INVALID: (
        "The requested page-order change does not match its operation type."
    ),
    PageOperationErrorCode.ROTATION_INVALID: (
        "Choose a clockwise rotation from zero through three quarter turns."
    ),
    PageOperationErrorCode.CROP_INVALID: (
        "The requested crop is outside the normalized page area."
    ),
    PageOperationErrorCode.DESKEW_INVALID: (
        "Choose a deskew angle from minus through plus fifteen degrees."
    ),
    PageOperationErrorCode.CONTRAST_INVALID: (
        "Choose a contrast adjustment within the supported range."
    ),
    PageOperationErrorCode.BACKGROUND_CLEANUP_INVALID: (
        "Choose a background cleanup strength within the supported range."
    ),
    PageOperationErrorCode.GROUP_EMPTY: ("Select at least one page before creating a page group."),
    PageOperationErrorCode.UNDO_UNAVAILABLE: "There is no session page edit to undo.",
    PageOperationErrorCode.REDO_UNAVAILABLE: "There is no session page edit to redo.",
    PageOperationErrorCode.CONCURRENT_CHANGE: (
        "The case pages changed before this edit could be saved. Try the edit again."
    ),
    PageOperationErrorCode.ID_GENERATION_FAILED: (
        "PaperWorkShell could not allocate a unique page-operation identifier."
    ),
}


@dataclass(slots=True, repr=False)
class PageOperationError(ValueError):
    """Stable failure that never includes manifest values or identifiers."""

    code: PageOperationErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PageOperationErrorCode:
            self.code = PageOperationErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "page.operation"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PageOperationError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_page_operation(code: PageOperationErrorCode, context: str) -> Never:
    """Raise one closed, non-disclosing page-domain failure."""

    raise PageOperationError(code=code, context=context)


def _require_uuid(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, context)
    return value


def _parse_uuid(value: object, *, context: str) -> UUID:
    if type(value) is not str or _UUID_V4_EXACT.fullmatch(value) is None:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    if parsed.version != 4 or str(parsed) != value:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    return parsed


def _copy_uuid_sequence(
    value: object,
    *,
    context: str,
    allow_empty: bool = True,
) -> tuple[UUID, ...]:
    if type(value) not in {tuple, list}:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, context)
    members = cast(list[object] | tuple[object, ...], value)
    if len(members) > MAX_PAGE_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, context)
    if not allow_empty and not members:
        reject_page_operation(PageOperationErrorCode.GROUP_EMPTY, context)
    copied = tuple(_require_uuid(item, context=context) for item in members)
    if len(set(copied)) != len(copied):
        reject_page_operation(PageOperationErrorCode.PAGE_DUPLICATE, context)
    return copied


def _parse_uuid_sequence(
    value: object,
    *,
    context: str,
    allow_empty: bool = True,
) -> tuple[UUID, ...]:
    if type(value) is not list:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    if len(value) > MAX_PAGE_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, context)
    if not allow_empty and not value:
        reject_page_operation(PageOperationErrorCode.GROUP_EMPTY, context)
    copied = tuple(_parse_uuid(item, context=context) for item in value)
    if len(set(copied)) != len(copied):
        reject_page_operation(PageOperationErrorCode.PAGE_DUPLICATE, context)
    return copied


def _require_members(value: object, expected: frozenset[str], *, context: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    return value


def _copy_public_mapping(value: Mapping[str, object], *, context: str) -> dict[str, object]:
    try:
        return copy_manifest_document(value, context=context)
    except Exception:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)


@dataclass(frozen=True, slots=True)
class PageReference:
    """Immutable identity of one page in an immutable source catalogue."""

    page_id: UUID
    source_id: UUID
    source_page_index: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="page.reference_id")
        _require_uuid(self.source_id, context="page.reference_source")
        if self.page_id == self.source_id:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "page.reference")
        if (
            type(self.source_page_index) is not int
            or not 0 <= self.source_page_index <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "page.source_index")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> PageReference:
        """Parse one exact shared case-manifest page record defensively."""

        copied = _copy_public_mapping(value, context="page.manifest")
        return _parse_page_reference(copied)

    def to_manifest(self) -> dict[str, object]:
        """Serialize to the exact shared case-manifest page-record shape."""

        return {
            "page_id": str(self.page_id),
            "source_id": str(self.source_id),
            "source_page_index": self.source_page_index,
        }


def _parse_page_reference(value: object) -> PageReference:
    record = _require_members(value, _PAGE_MEMBERS, context="page.manifest")
    source_page_index = record["source_page_index"]
    if (
        type(source_page_index) is not int
        or not 0 <= source_page_index <= MAX_SAFE_MANIFEST_INTEGER
    ):
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "page.source_index")
    return PageReference(
        page_id=_parse_uuid(record["page_id"], context="page.manifest_id"),
        source_id=_parse_uuid(record["source_id"], context="page.manifest_source"),
        source_page_index=source_page_index,
    )


def _parse_box(value: object) -> NormalizedPageBox:
    if type(value) is not dict:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "crop.manifest")
    try:
        return NormalizedPageBox.from_manifest(value)
    except PageCoordinateError:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "crop.manifest")


class SequenceChangeReason(StrEnum):
    """Closed legacy reasons retained by manifest v1/v2 sequence transitions."""

    IMPORT = "import"
    REORDER = "reorder"
    REMOVE = "remove"
    RESTORE = "restore"
    SPLIT = "split"
    COMBINE = "combine"


@dataclass(frozen=True, slots=True)
class RotatePayload:
    """Additive clockwise rotation intent for one introduced page."""

    page_id: UUID
    quarter_turns_clockwise: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="rotate.page_id")
        if (
            type(self.quarter_turns_clockwise) is not int
            or not 0 <= self.quarter_turns_clockwise <= 3
        ):
            reject_page_operation(PageOperationErrorCode.ROTATION_INVALID, "rotate.turns")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> RotatePayload:
        copied = _copy_public_mapping(value, context="rotate.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "rotate.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "rotate",
            "page_id": str(self.page_id),
            "quarter_turns_clockwise": self.quarter_turns_clockwise,
        }


@dataclass(frozen=True, slots=True)
class CropPayload:
    """Latest-wins normalized crop intent for one introduced page."""

    page_id: UUID
    box: NormalizedPageBox

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="crop.page_id")
        if type(self.box) is not NormalizedPageBox:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "crop.box")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> CropPayload:
        copied = _copy_public_mapping(value, context="crop.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "crop.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {"kind": "crop", "page_id": str(self.page_id), "box": self.box.to_manifest()}


@dataclass(frozen=True, slots=True)
class DeskewPayload:
    """Latest-wins bounded clockwise fine-rotation intent."""

    page_id: UUID
    angle_millidegrees_clockwise: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="deskew.page_id")
        if (
            type(self.angle_millidegrees_clockwise) is not int
            or not -MAX_DESKEW_ANGLE_MILLIDEGREES
            <= self.angle_millidegrees_clockwise
            <= MAX_DESKEW_ANGLE_MILLIDEGREES
        ):
            reject_page_operation(PageOperationErrorCode.DESKEW_INVALID, "deskew.angle")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> DeskewPayload:
        copied = _copy_public_mapping(value, context="deskew.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "deskew.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "deskew",
            "page_id": str(self.page_id),
            "angle_millidegrees_clockwise": self.angle_millidegrees_clockwise,
        }


@dataclass(frozen=True, slots=True)
class ContrastPayload:
    """Latest-wins deterministic contrast factor in integer millionths."""

    page_id: UUID
    factor_millionths: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="contrast.page_id")
        if (
            type(self.factor_millionths) is not int
            or not 0 <= self.factor_millionths <= MAX_CONTRAST_FACTOR_MILLIONTHS
        ):
            reject_page_operation(PageOperationErrorCode.CONTRAST_INVALID, "contrast.factor")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> ContrastPayload:
        copied = _copy_public_mapping(value, context="contrast.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "contrast.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "contrast",
            "page_id": str(self.page_id),
            "factor_millionths": self.factor_millionths,
        }


@dataclass(frozen=True, slots=True)
class BackgroundCleanupPayload:
    """Latest-wins deterministic background cleanup strength."""

    page_id: UUID
    strength_millionths: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="background_cleanup.page_id")
        if (
            type(self.strength_millionths) is not int
            or not 0 <= self.strength_millionths <= MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
        ):
            reject_page_operation(
                PageOperationErrorCode.BACKGROUND_CLEANUP_INVALID,
                "background_cleanup.strength",
            )

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> BackgroundCleanupPayload:
        copied = _copy_public_mapping(value, context="background_cleanup.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(
                PageOperationErrorCode.MANIFEST_INVALID,
                "background_cleanup.manifest",
            )
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "background_cleanup",
            "page_id": str(self.page_id),
            "strength_millionths": self.strength_millionths,
        }


@dataclass(frozen=True, slots=True)
class ChangeSequencePayload:
    """One replay-checked transition between complete active page sequences."""

    reason: SequenceChangeReason
    before_page_ids: tuple[UUID, ...]
    after_page_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if type(self.reason) is not SequenceChangeReason:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "sequence.reason")
        object.__setattr__(
            self,
            "before_page_ids",
            _copy_uuid_sequence(self.before_page_ids, context="sequence.before"),
        )
        object.__setattr__(
            self,
            "after_page_ids",
            _copy_uuid_sequence(self.after_page_ids, context="sequence.after"),
        )

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> ChangeSequencePayload:
        copied = _copy_public_mapping(value, context="sequence.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "sequence.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "change_sequence",
            "reason": self.reason.value,
            "before_page_ids": [str(page_id) for page_id in self.before_page_ids],
            "after_page_ids": [str(page_id) for page_id in self.after_page_ids],
        }


@dataclass(frozen=True, slots=True)
class SetGroupPayload:
    """Latest-wins membership intent for a stable page group identifier."""

    group_id: UUID
    page_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.group_id, context="group.group_id")
        object.__setattr__(
            self,
            "page_ids",
            _copy_uuid_sequence(self.page_ids, context="group.pages", allow_empty=False),
        )

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> SetGroupPayload:
        copied = _copy_public_mapping(value, context="group.manifest")
        payload = _parse_payload(copied)
        if type(payload) is not cls:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "group.manifest")
        return payload

    def to_manifest(self) -> dict[str, object]:
        return {
            "kind": "set_group",
            "group_id": str(self.group_id),
            "page_ids": [str(page_id) for page_id in self.page_ids],
        }


type PageOperationPayload = (
    RotatePayload
    | CropPayload
    | ChangeSequencePayload
    | SetGroupPayload
    | DeskewPayload
    | ContrastPayload
    | BackgroundCleanupPayload
)
RotatePagePayload = RotatePayload
CropPagePayload = CropPayload
SequenceChangePayload = ChangeSequencePayload
GroupPayload = SetGroupPayload

_LEGACY_PAYLOAD_TYPES: Final = (
    RotatePayload,
    CropPayload,
    ChangeSequencePayload,
    SetGroupPayload,
)
_SCAN_TRANSFORM_PAYLOAD_TYPES: Final = (
    DeskewPayload,
    ContrastPayload,
    BackgroundCleanupPayload,
)


def _parse_payload(value: object) -> PageOperationPayload:
    if type(value) is not dict or type(value.get("kind")) is not str:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "operation.payload")
    kind = value["kind"]
    if kind == "rotate":
        payload = _require_members(value, _ROTATE_MEMBERS, context="rotate.manifest")
        turns = payload["quarter_turns_clockwise"]
        if type(turns) is not int:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "rotate.turns")
        return RotatePayload(
            page_id=_parse_uuid(payload["page_id"], context="rotate.page_id"),
            quarter_turns_clockwise=turns,
        )
    if kind == "crop":
        payload = _require_members(value, _CROP_MEMBERS, context="crop.manifest")
        return CropPayload(
            page_id=_parse_uuid(payload["page_id"], context="crop.page_id"),
            box=_parse_box(payload["box"]),
        )
    if kind == "deskew":
        payload = _require_members(value, _DESKEW_MEMBERS, context="deskew.manifest")
        angle = payload["angle_millidegrees_clockwise"]
        if type(angle) is not int:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "deskew.angle")
        return DeskewPayload(
            page_id=_parse_uuid(payload["page_id"], context="deskew.page_id"),
            angle_millidegrees_clockwise=angle,
        )
    if kind == "contrast":
        payload = _require_members(value, _CONTRAST_MEMBERS, context="contrast.manifest")
        factor = payload["factor_millionths"]
        if type(factor) is not int:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "contrast.factor")
        return ContrastPayload(
            page_id=_parse_uuid(payload["page_id"], context="contrast.page_id"),
            factor_millionths=factor,
        )
    if kind == "background_cleanup":
        payload = _require_members(
            value,
            _BACKGROUND_CLEANUP_MEMBERS,
            context="background_cleanup.manifest",
        )
        strength = payload["strength_millionths"]
        if type(strength) is not int:
            reject_page_operation(
                PageOperationErrorCode.MANIFEST_INVALID,
                "background_cleanup.strength",
            )
        return BackgroundCleanupPayload(
            page_id=_parse_uuid(payload["page_id"], context="background_cleanup.page_id"),
            strength_millionths=strength,
        )
    if kind == "change_sequence":
        payload = _require_members(value, _SEQUENCE_MEMBERS, context="sequence.manifest")
        reason_value = payload["reason"]
        if type(reason_value) is not str:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "sequence.reason")
        try:
            reason = SequenceChangeReason(reason_value)
        except ValueError:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "sequence.reason")
        return ChangeSequencePayload(
            reason=reason,
            before_page_ids=_parse_uuid_sequence(
                payload["before_page_ids"], context="sequence.before"
            ),
            after_page_ids=_parse_uuid_sequence(
                payload["after_page_ids"], context="sequence.after"
            ),
        )
    if kind == "set_group":
        payload = _require_members(value, _GROUP_MEMBERS, context="group.manifest")
        return SetGroupPayload(
            group_id=_parse_uuid(payload["group_id"], context="group.group_id"),
            page_ids=_parse_uuid_sequence(
                payload["page_ids"], context="group.pages", allow_empty=False
            ),
        )
    reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "operation.kind")


@dataclass(frozen=True, slots=True)
class PageOperation:
    """One exact, versioned, contiguous case-manifest page operation."""

    operation_id: UUID
    sequence: int
    payload: PageOperationPayload
    operation_version: int = PAGE_OPERATION_VERSION

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, context="operation.id")
        if type(self.operation_version) is not int:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "operation.version")
        if self.operation_version not in {
            LEGACY_PAGE_OPERATION_VERSION,
            SCAN_TRANSFORM_OPERATION_VERSION,
        }:
            reject_page_operation(PageOperationErrorCode.VERSION_UNSUPPORTED, "operation.version")
        payload_type = type(self.payload)
        if (
            self.operation_version == LEGACY_PAGE_OPERATION_VERSION
            and payload_type not in _LEGACY_PAYLOAD_TYPES
        ) or (
            self.operation_version == SCAN_TRANSFORM_OPERATION_VERSION
            and payload_type not in _SCAN_TRANSFORM_PAYLOAD_TYPES
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "operation.version_payload")
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_SAFE_MANIFEST_INTEGER
            or payload_type not in {*_LEGACY_PAYLOAD_TYPES, *_SCAN_TRANSFORM_PAYLOAD_TYPES}
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "operation.model")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> PageOperation:
        """Parse one exact versioned manifest operation without retaining containers."""

        copied = _copy_public_mapping(value, context="operation.manifest")
        return _parse_operation(copied)

    def to_manifest(self) -> dict[str, object]:
        """Serialize to the exact versioned manifest page-operation shape."""

        return {
            "operation_id": str(self.operation_id),
            "operation_version": self.operation_version,
            "sequence": self.sequence,
            "payload": self.payload.to_manifest(),
        }


def _parse_operation(value: object) -> PageOperation:
    operation = _require_members(value, _OPERATION_MEMBERS, context="operation.manifest")
    version = operation["operation_version"]
    sequence = operation["sequence"]
    if type(version) is not int:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "operation.version")
    if version not in {LEGACY_PAGE_OPERATION_VERSION, SCAN_TRANSFORM_OPERATION_VERSION}:
        reject_page_operation(PageOperationErrorCode.VERSION_UNSUPPORTED, "operation.version")
    if type(sequence) is not int or not 1 <= sequence <= MAX_SAFE_MANIFEST_INTEGER:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "operation.sequence")
    payload = _parse_payload(operation["payload"])
    if (
        version == LEGACY_PAGE_OPERATION_VERSION and type(payload) not in _LEGACY_PAYLOAD_TYPES
    ) or (
        version == SCAN_TRANSFORM_OPERATION_VERSION
        and type(payload) not in _SCAN_TRANSFORM_PAYLOAD_TYPES
    ):
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "operation.version_payload")
    return PageOperation(
        operation_id=_parse_uuid(operation["operation_id"], context="operation.id"),
        operation_version=version,
        sequence=sequence,
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class PageGroup:
    """The final deterministic membership of one logical output group."""

    group_id: UUID
    page_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.group_id, context="projection.group_id")
        object.__setattr__(
            self,
            "page_ids",
            _copy_uuid_sequence(self.page_ids, context="projection.group_pages", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class PageState:
    """Fully replayed non-destructive state for one catalogued page."""

    page: PageReference
    active: bool
    quarter_turns_clockwise: int
    crop: NormalizedPageBox | None
    deskew_angle_millidegrees_clockwise: int = 0
    contrast_factor_millionths: int = NEUTRAL_CONTRAST_FACTOR_MILLIONTHS
    background_cleanup_strength_millionths: int = 0
    group_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if type(self.page) is not PageReference or type(self.active) is not bool:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.page")
        if (
            type(self.quarter_turns_clockwise) is not int
            or not 0 <= self.quarter_turns_clockwise <= 3
            or (self.crop is not None and type(self.crop) is not NormalizedPageBox)
            or type(self.deskew_angle_millidegrees_clockwise) is not int
            or not -MAX_DESKEW_ANGLE_MILLIDEGREES
            <= self.deskew_angle_millidegrees_clockwise
            <= MAX_DESKEW_ANGLE_MILLIDEGREES
            or type(self.contrast_factor_millionths) is not int
            or not 0 <= self.contrast_factor_millionths <= MAX_CONTRAST_FACTOR_MILLIONTHS
            or type(self.background_cleanup_strength_millionths) is not int
            or not 0
            <= self.background_cleanup_strength_millionths
            <= MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.transform")
        object.__setattr__(
            self,
            "group_ids",
            _copy_uuid_sequence(self.group_ids, context="projection.page_groups"),
        )

    @property
    def page_id(self) -> UUID:
        return self.page.page_id

    @property
    def reference(self) -> PageReference:
        return self.page


@dataclass(frozen=True, slots=True)
class OrganizerProjection:
    """Complete deterministic page organizer projection after replay."""

    page_sequence: tuple[UUID, ...]
    pages: tuple[PageState, ...]
    groups: tuple[PageGroup, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "page_sequence",
            _copy_uuid_sequence(self.page_sequence, context="projection.sequence"),
        )
        if type(self.pages) is not tuple or type(self.groups) is not tuple:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.model")
        if any(type(page) is not PageState for page in self.pages) or any(
            type(group) is not PageGroup for group in self.groups
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.model")
        page_ids = tuple(page.page_id for page in self.pages)
        source_ids = {page.page.source_id for page in self.pages}
        source_positions = {
            (page.page.source_id, page.page.source_page_index) for page in self.pages
        }
        if (
            len(page_ids) > MAX_PAGE_COUNT
            or len(set(page_ids)) != len(page_ids)
            or len(source_positions) != len(page_ids)
            or not set(self.page_sequence) <= set(page_ids)
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.pages")
        group_ids = tuple(group.group_id for group in self.groups)
        if (
            len(self.groups) > MAX_PAGE_OPERATION_COUNT
            or len(set(group_ids)) != len(group_ids)
            or bool(set(group_ids) & (set(page_ids) | source_ids))
        ):
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "projection.groups")
        known_pages = set(page_ids)
        active_pages = {page.page_id for page in self.pages if page.active}
        membership_count = 0
        expected_memberships: dict[UUID, list[UUID]] = {page_id: [] for page_id in page_ids}
        for group in self.groups:
            membership_count += len(group.page_ids)
            if membership_count > MAX_REPLAY_MEMBERS or not set(group.page_ids) <= known_pages:
                reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "projection.groups")
            for page_id in group.page_ids:
                expected_memberships[page_id].append(group.group_id)
        if active_pages != set(self.page_sequence) or any(
            page.group_ids != tuple(expected_memberships[page.page_id]) for page in self.pages
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "projection.consistency")

    @property
    def sequence(self) -> tuple[UUID, ...]:
        """Concise alias for the authoritative active page sequence."""

        return self.page_sequence

    @property
    def page_states(self) -> tuple[PageState, ...]:
        """Explicit alias for callers binding page state to an organizer."""

        return self.pages

    def state_for(self, page_id: UUID) -> PageState:
        """Return one state or raise a sanitized unknown-page failure."""

        selected = _require_uuid(page_id, context="projection.lookup_page")
        for state in self.pages:
            if state.page_id == selected:
                return state
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "projection.lookup_page")

    def group_for(self, group_id: UUID) -> PageGroup:
        """Return one group or raise a sanitized identifier failure."""

        selected = _require_uuid(group_id, context="projection.lookup_group")
        for group in self.groups:
            if group.group_id == selected:
                return group
        reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, "projection.lookup_group")


def replay_page_operations(
    pages: Sequence[PageReference],
    page_sequence_base: Sequence[UUID],
    operations: Sequence[PageOperation],
    *,
    final_sequence: Sequence[UUID] | None = None,
) -> OrganizerProjection:
    """Replay validated page intent with the exact versioned manifest semantics."""

    return _replay_page_operations(
        pages,
        page_sequence_base,
        operations,
        final_sequence=final_sequence,
        reserved_namespace=frozenset(),
    )


def _replay_page_operations(
    pages: Sequence[PageReference],
    page_sequence_base: Sequence[UUID],
    operations: Sequence[PageOperation],
    *,
    final_sequence: Sequence[UUID] | None,
    reserved_namespace: frozenset[UUID],
) -> OrganizerProjection:
    if type(pages) not in {tuple, list} or type(operations) not in {tuple, list}:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "replay.collections")
    if len(pages) > MAX_PAGE_COUNT or len(operations) > MAX_PAGE_OPERATION_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "replay.capacity")
    if any(type(page) is not PageReference for page in pages) or any(
        type(operation) is not PageOperation for operation in operations
    ):
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "replay.collections")
    page_records = tuple(pages)
    operation_records = tuple(operations)
    page_ids = tuple(page.page_id for page in page_records)
    if len(set(page_ids)) != len(page_ids):
        reject_page_operation(PageOperationErrorCode.PAGE_DUPLICATE, "replay.pages")
    page_origins = {(page.source_id, page.source_page_index) for page in page_records}
    if len(page_origins) != len(page_records):
        reject_page_operation(PageOperationErrorCode.PAGE_DUPLICATE, "replay.source_pages")
    source_ids = {page.source_id for page in page_records}
    if set(page_ids) & source_ids:
        reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "replay.page_namespace")
    base = _copy_uuid_sequence(page_sequence_base, context="replay.base")
    known_pages = set(page_ids)
    if not set(base) <= known_pages:
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "replay.base")
    expected_final = (
        None
        if final_sequence is None
        else _copy_uuid_sequence(final_sequence, context="replay.final")
    )
    if expected_final is not None and not set(expected_final) <= known_pages:
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "replay.final")
    replay_members = len(page_records) + len(base)
    if expected_final is not None:
        replay_members += len(expected_final)
    for operation in operation_records:
        replay_members += 1
        payload = operation.payload
        if type(payload) is ChangeSequencePayload:
            replay_members += len(payload.before_page_ids) + len(payload.after_page_ids)
        elif type(payload) is SetGroupPayload:
            replay_members += len(payload.page_ids)
        if replay_members > MAX_REPLAY_MEMBERS:
            reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "replay.work")

    namespace = set(reserved_namespace)
    if namespace & (known_pages | source_ids):
        reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "replay.namespace")
    namespace.update(known_pages)
    namespace.update(source_ids)
    operation_ids: set[UUID] = set()
    for index, operation in enumerate(operation_records, start=1):
        if operation.sequence != index:
            reject_page_operation(
                PageOperationErrorCode.OPERATION_SEQUENCE_INVALID,
                "replay.operation_sequence",
            )
        if operation.operation_id in namespace or operation.operation_id in operation_ids:
            reject_page_operation(
                PageOperationErrorCode.IDENTIFIER_COLLISION,
                "replay.operation_namespace",
            )
        operation_ids.add(operation.operation_id)
    namespace.update(operation_ids)

    sequence = list(base)
    introduced = set(base)
    rotations: dict[UUID, int] = {page_id: 0 for page_id in page_ids}
    crops: dict[UUID, NormalizedPageBox] = {}
    deskew_angles: dict[UUID, int] = {}
    contrast_factors: dict[UUID, int] = {}
    background_cleanup_strengths: dict[UUID, int] = {}
    groups: dict[UUID, tuple[UUID, ...]] = {}
    for operation in operation_records:
        payload = operation.payload
        if type(payload) is RotatePayload:
            _require_introduced_page(payload.page_id, known_pages, introduced, "replay.rotate")
            rotations[payload.page_id] = (
                rotations[payload.page_id] + payload.quarter_turns_clockwise
            ) % 4
            continue
        if type(payload) is CropPayload:
            _require_introduced_page(payload.page_id, known_pages, introduced, "replay.crop")
            crops[payload.page_id] = payload.box
            continue
        if type(payload) is DeskewPayload:
            _require_introduced_page(payload.page_id, known_pages, introduced, "replay.deskew")
            deskew_angles[payload.page_id] = payload.angle_millidegrees_clockwise
            continue
        if type(payload) is ContrastPayload:
            _require_introduced_page(payload.page_id, known_pages, introduced, "replay.contrast")
            contrast_factors[payload.page_id] = payload.factor_millionths
            continue
        if type(payload) is BackgroundCleanupPayload:
            _require_introduced_page(
                payload.page_id,
                known_pages,
                introduced,
                "replay.background_cleanup",
            )
            background_cleanup_strengths[payload.page_id] = payload.strength_millionths
            continue
        if type(payload) is ChangeSequencePayload:
            if tuple(sequence) != payload.before_page_ids:
                reject_page_operation(PageOperationErrorCode.SEQUENCE_MISMATCH, "replay.before")
            if not (set(payload.before_page_ids) | set(payload.after_page_ids)) <= known_pages:
                reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "replay.sequence")
            _apply_sequence_change(payload, introduced)
            if payload.reason is SequenceChangeReason.IMPORT:
                introduced.update(set(payload.after_page_ids) - set(payload.before_page_ids))
            sequence = list(payload.after_page_ids)
            continue
        if type(payload) is SetGroupPayload:
            for page_id in payload.page_ids:
                _require_introduced_page(page_id, known_pages, introduced, "replay.group")
            if payload.group_id not in groups:
                if payload.group_id in namespace:
                    reject_page_operation(
                        PageOperationErrorCode.IDENTIFIER_COLLISION,
                        "replay.group_namespace",
                    )
                namespace.add(payload.group_id)
            groups[payload.group_id] = payload.page_ids
            continue
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "replay.payload")

    if introduced != known_pages:
        reject_page_operation(
            PageOperationErrorCode.PAGE_INTRODUCTION_INCOMPLETE,
            "replay.introduction",
        )
    if expected_final is not None and tuple(sequence) != expected_final:
        reject_page_operation(PageOperationErrorCode.SEQUENCE_MISMATCH, "replay.final")
    active = set(sequence)
    group_models = tuple(PageGroup(group_id, page_ids) for group_id, page_ids in groups.items())
    page_groups: dict[UUID, list[UUID]] = {page_id: [] for page_id in page_ids}
    for group in group_models:
        for page_id in group.page_ids:
            page_groups[page_id].append(group.group_id)
    page_states = tuple(
        PageState(
            page=page,
            active=page.page_id in active,
            quarter_turns_clockwise=rotations[page.page_id],
            crop=crops.get(page.page_id),
            deskew_angle_millidegrees_clockwise=deskew_angles.get(page.page_id, 0),
            contrast_factor_millionths=contrast_factors.get(
                page.page_id,
                NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
            ),
            background_cleanup_strength_millionths=background_cleanup_strengths.get(
                page.page_id,
                0,
            ),
            group_ids=tuple(page_groups[page.page_id]),
        )
        for page in page_records
    )
    return OrganizerProjection(tuple(sequence), page_states, group_models)


def _require_introduced_page(
    page_id: UUID,
    known_pages: set[UUID],
    introduced: set[UUID],
    context: str,
) -> None:
    if page_id not in known_pages:
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, context)
    if page_id not in introduced:
        reject_page_operation(PageOperationErrorCode.PAGE_NOT_INTRODUCED, context)


def _apply_sequence_change(
    payload: ChangeSequencePayload,
    introduced: set[UUID],
) -> None:
    before = payload.before_page_ids
    after = payload.after_page_ids
    before_set = set(before)
    after_set = set(after)
    added = after_set - before_set
    if payload.reason is SequenceChangeReason.REMOVE:
        valid = after_set < before_set and after == tuple(
            page_id for page_id in before if page_id in after_set
        )
    elif payload.reason in {SequenceChangeReason.IMPORT, SequenceChangeReason.RESTORE}:
        preserves_prior_order = before_set < after_set and before == tuple(
            page_id for page_id in after if page_id in before_set
        )
        valid = preserves_prior_order and (
            added.isdisjoint(introduced)
            if payload.reason is SequenceChangeReason.IMPORT
            else added <= introduced
        )
    else:
        valid = before_set == after_set
    if not valid:
        reject_page_operation(
            PageOperationErrorCode.SEQUENCE_REASON_INVALID,
            "replay.sequence_reason",
        )


@dataclass(frozen=True, slots=True)
class SequenceChangeCommand:
    """Session command for reorder/remove/restore/split/combine intent."""

    reason: SequenceChangeReason
    after_page_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.reason) is not SequenceChangeReason
            or self.reason is SequenceChangeReason.IMPORT
        ):
            reject_page_operation(
                PageOperationErrorCode.SEQUENCE_REASON_INVALID, "command.sequence"
            )
        object.__setattr__(
            self,
            "after_page_ids",
            _copy_uuid_sequence(self.after_page_ids, context="command.sequence_pages"),
        )


@dataclass(frozen=True, slots=True)
class RotatePageCommand:
    """Session command for one additive clockwise page rotation."""

    page_id: UUID
    quarter_turns_clockwise: int

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="command.rotate_page")
        if (
            type(self.quarter_turns_clockwise) is not int
            or not 0 <= self.quarter_turns_clockwise <= 3
        ):
            reject_page_operation(PageOperationErrorCode.ROTATION_INVALID, "command.rotate")


@dataclass(frozen=True, slots=True)
class CropPageCommand:
    """Session command replacing the normalized crop intent for one page."""

    page_id: UUID
    box: NormalizedPageBox

    def __post_init__(self) -> None:
        _require_uuid(self.page_id, context="command.crop_page")
        if type(self.box) is not NormalizedPageBox:
            reject_page_operation(PageOperationErrorCode.CROP_INVALID, "command.crop")


@dataclass(frozen=True, slots=True)
class DeskewPageCommand:
    """Session command replacing one page's bounded fine-rotation intent."""

    page_id: UUID
    angle_millidegrees_clockwise: int

    def __post_init__(self) -> None:
        DeskewPayload(self.page_id, self.angle_millidegrees_clockwise)


@dataclass(frozen=True, slots=True)
class ContrastPageCommand:
    """Session command replacing one page's deterministic contrast factor."""

    page_id: UUID
    factor_millionths: int

    def __post_init__(self) -> None:
        ContrastPayload(self.page_id, self.factor_millionths)


@dataclass(frozen=True, slots=True)
class BackgroundCleanupPageCommand:
    """Session command replacing one page's background cleanup strength."""

    page_id: UUID
    strength_millionths: int

    def __post_init__(self) -> None:
        BackgroundCleanupPayload(self.page_id, self.strength_millionths)


@dataclass(frozen=True, slots=True)
class SetGroupCommand:
    """Session command that creates or replaces one logical page group."""

    page_ids: tuple[UUID, ...]
    group_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "page_ids",
            _copy_uuid_sequence(self.page_ids, context="command.group_pages", allow_empty=False),
        )
        if self.group_id is not None:
            _require_uuid(self.group_id, context="command.group_id")


type PageCommand = (
    SequenceChangeCommand
    | RotatePageCommand
    | CropPageCommand
    | DeskewPageCommand
    | ContrastPageCommand
    | BackgroundCleanupPageCommand
    | SetGroupCommand
)


@dataclass(frozen=True, slots=True)
class _ParsedSnapshot:
    document: dict[str, object]
    case_id: UUID
    pages: tuple[PageReference, ...]
    page_sequence_base: tuple[UUID, ...]
    operations: tuple[PageOperation, ...]
    projection: OrganizerProjection
    unrelated_namespace: frozenset[UUID]
    complete_namespace: frozenset[UUID]


@dataclass(frozen=True, slots=True, init=False)
class PageCommandHistory:
    """Immutable session history over a durable, non-undoable manifest baseline."""

    _case_id: UUID
    _origin_revision: int
    _pages: tuple[PageReference, ...]
    _page_sequence_base: tuple[UUID, ...]
    _durable_operations: tuple[PageOperation, ...]
    _session_operations: tuple[PageOperation, ...]
    _redo_operations: tuple[PageOperation, ...]
    _owned_operations: tuple[PageOperation, ...]
    _known_session_states: frozenset[bytes]
    _origin_namespace: frozenset[UUID]
    _projection: OrganizerProjection

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot) -> PageCommandHistory:
        """Create a clean session over one validated manifest snapshot."""

        parsed = _parse_snapshot(snapshot)
        return cls._create(
            case_id=parsed.case_id,
            origin_revision=snapshot.revision,
            pages=parsed.pages,
            page_sequence_base=parsed.page_sequence_base,
            durable_operations=parsed.operations,
            session_operations=(),
            redo_operations=(),
            owned_operations=(),
            known_session_states=frozenset({_session_state_key(())}),
            origin_namespace=parsed.complete_namespace,
            projection=parsed.projection,
        )

    @classmethod
    def _create(
        cls,
        *,
        case_id: UUID,
        origin_revision: int,
        pages: tuple[PageReference, ...],
        page_sequence_base: tuple[UUID, ...],
        durable_operations: tuple[PageOperation, ...],
        session_operations: tuple[PageOperation, ...],
        redo_operations: tuple[PageOperation, ...],
        owned_operations: tuple[PageOperation, ...],
        known_session_states: frozenset[bytes],
        origin_namespace: frozenset[UUID],
        projection: OrganizerProjection,
    ) -> PageCommandHistory:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_case_id", case_id)
        object.__setattr__(instance, "_origin_revision", origin_revision)
        object.__setattr__(instance, "_pages", pages)
        object.__setattr__(instance, "_page_sequence_base", page_sequence_base)
        object.__setattr__(instance, "_durable_operations", durable_operations)
        object.__setattr__(instance, "_session_operations", session_operations)
        object.__setattr__(instance, "_redo_operations", redo_operations)
        object.__setattr__(instance, "_owned_operations", owned_operations)
        object.__setattr__(instance, "_known_session_states", known_session_states)
        object.__setattr__(instance, "_origin_namespace", origin_namespace)
        object.__setattr__(instance, "_projection", projection)
        return instance

    @property
    def projection(self) -> OrganizerProjection:
        return self._projection

    @property
    def operations(self) -> tuple[PageOperation, ...]:
        return self._durable_operations + self._session_operations

    @property
    def can_undo(self) -> bool:
        return bool(self._session_operations)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_operations)

    def execute(
        self,
        command: PageCommand,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> PageCommandHistory:
        """Return a new history containing one validated session operation."""

        if type(command) not in {
            SequenceChangeCommand,
            RotatePageCommand,
            CropPageCommand,
            DeskewPageCommand,
            ContrastPageCommand,
            BackgroundCleanupPageCommand,
            SetGroupCommand,
        }:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "history.command")
        if len(self._durable_operations) + len(self._owned_operations) >= MAX_PAGE_OPERATION_COUNT:
            reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "history.operations")
        if not callable(id_factory):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "history.id_factory")
        reserved = set(self._origin_namespace)
        for owned in self._owned_operations:
            reserved.add(owned.operation_id)
            if type(owned.payload) is SetGroupPayload:
                reserved.add(owned.payload.group_id)
        operation_id = _generate_unique_id(id_factory, reserved)
        reserved.add(operation_id)
        payload: PageOperationPayload
        if isinstance(command, SequenceChangeCommand):
            payload = ChangeSequencePayload(
                reason=command.reason,
                before_page_ids=self._projection.page_sequence,
                after_page_ids=command.after_page_ids,
            )
        elif isinstance(command, RotatePageCommand):
            payload = RotatePayload(command.page_id, command.quarter_turns_clockwise)
        elif isinstance(command, CropPageCommand):
            payload = CropPayload(command.page_id, command.box)
        elif isinstance(command, DeskewPageCommand):
            payload = DeskewPayload(command.page_id, command.angle_millidegrees_clockwise)
        elif isinstance(command, ContrastPageCommand):
            payload = ContrastPayload(command.page_id, command.factor_millionths)
        elif isinstance(command, BackgroundCleanupPageCommand):
            payload = BackgroundCleanupPayload(command.page_id, command.strength_millionths)
        elif isinstance(command, SetGroupCommand):
            existing_groups = {group.group_id for group in self._projection.groups}
            group_id = command.group_id
            if group_id is None:
                group_id = _generate_unique_id(id_factory, reserved)
            elif group_id not in existing_groups and group_id in reserved:
                reject_page_operation(
                    PageOperationErrorCode.IDENTIFIER_COLLISION,
                    "history.group_id",
                )
            payload = SetGroupPayload(group_id=group_id, page_ids=command.page_ids)
        else:  # pragma: no cover - exact type guard above makes this unreachable
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "history.command")
        operation = PageOperation(
            operation_id=operation_id,
            sequence=len(self.operations) + 1,
            payload=payload,
            operation_version=(
                SCAN_TRANSFORM_OPERATION_VERSION
                if type(payload) in _SCAN_TRANSFORM_PAYLOAD_TYPES
                else LEGACY_PAGE_OPERATION_VERSION
            ),
        )
        session = self._session_operations + (operation,)
        projection = _replay_page_operations(
            self._pages,
            self._page_sequence_base,
            self._durable_operations + session,
            final_sequence=None,
            reserved_namespace=frozenset(),
        )
        return self._transition(
            session_operations=session,
            redo_operations=(),
            owned_operations=self._owned_operations + (operation,),
            known_session_states=self._known_session_states | {_session_state_key(session)},
            projection=projection,
        )

    def undo(self) -> PageCommandHistory:
        """Remove exactly one session operation; durable loaded intent is never undone."""

        if not self._session_operations:
            reject_page_operation(PageOperationErrorCode.UNDO_UNAVAILABLE, "history.undo")
        operation = self._session_operations[-1]
        session = self._session_operations[:-1]
        projection = _replay_page_operations(
            self._pages,
            self._page_sequence_base,
            self._durable_operations + session,
            final_sequence=None,
            reserved_namespace=frozenset(),
        )
        return self._transition(
            session_operations=session,
            redo_operations=(operation,) + self._redo_operations,
            owned_operations=self._owned_operations,
            known_session_states=self._known_session_states | {_session_state_key(session)},
            projection=projection,
        )

    def redo(self) -> PageCommandHistory:
        """Restore the exact next undone operation identifier and payload."""

        if not self._redo_operations:
            reject_page_operation(PageOperationErrorCode.REDO_UNAVAILABLE, "history.redo")
        operation = self._redo_operations[0]
        session = self._session_operations + (operation,)
        projection = _replay_page_operations(
            self._pages,
            self._page_sequence_base,
            self._durable_operations + session,
            final_sequence=None,
            reserved_namespace=frozenset(),
        )
        return self._transition(
            session_operations=session,
            redo_operations=self._redo_operations[1:],
            owned_operations=self._owned_operations,
            known_session_states=self._known_session_states | {_session_state_key(session)},
            projection=projection,
        )

    def to_candidate(self, current_snapshot: ManifestSnapshot) -> dict[str, object]:
        """Rebase only this session's known operation suffix onto current unrelated state."""

        current = _parse_snapshot(current_snapshot)
        if (
            current.case_id != self._case_id
            or current_snapshot.revision < self._origin_revision
            or current.pages != self._pages
            or current.page_sequence_base != self._page_sequence_base
            or current.operations[: len(self._durable_operations)] != self._durable_operations
            or len(current.operations) < len(self._durable_operations)
        ):
            reject_page_operation(PageOperationErrorCode.CONCURRENT_CHANGE, "history.rebase")
        current_suffix = current.operations[len(self._durable_operations) :]
        owned_by_id = {operation.operation_id: operation for operation in self._owned_operations}
        if any(
            owned_by_id.get(operation.operation_id) != operation for operation in current_suffix
        ):
            reject_page_operation(PageOperationErrorCode.CONCURRENT_CHANGE, "history.ownership")
        if _session_state_key(current_suffix) not in self._known_session_states:
            reject_page_operation(PageOperationErrorCode.CONCURRENT_CHANGE, "history.suffix")
        target_operations = self.operations
        target_domain_ids: set[UUID] = {operation.operation_id for operation in target_operations}
        target_group_ids = {
            operation.payload.group_id
            for operation in target_operations
            if type(operation.payload) is SetGroupPayload
        }
        if target_domain_ids & target_group_ids or (
            (target_domain_ids | target_group_ids) & set(current.unrelated_namespace)
        ):
            reject_page_operation(PageOperationErrorCode.CONCURRENT_CHANGE, "history.namespace")
        projection = _replay_page_operations(
            self._pages,
            self._page_sequence_base,
            target_operations,
            final_sequence=self._projection.page_sequence,
            reserved_namespace=frozenset(),
        )
        candidate = current.document
        candidate["page_operations"] = [operation.to_manifest() for operation in target_operations]
        candidate["page_sequence"] = [str(page_id) for page_id in projection.page_sequence]
        return candidate

    def _transition(
        self,
        *,
        session_operations: tuple[PageOperation, ...],
        redo_operations: tuple[PageOperation, ...],
        owned_operations: tuple[PageOperation, ...],
        known_session_states: frozenset[bytes],
        projection: OrganizerProjection,
    ) -> PageCommandHistory:
        return self._create(
            case_id=self._case_id,
            origin_revision=self._origin_revision,
            pages=self._pages,
            page_sequence_base=self._page_sequence_base,
            durable_operations=self._durable_operations,
            session_operations=session_operations,
            redo_operations=redo_operations,
            owned_operations=owned_operations,
            known_session_states=known_session_states,
            origin_namespace=self._origin_namespace,
            projection=projection,
        )


def _generate_unique_id(factory: Callable[[], UUID], reserved: set[UUID]) -> UUID:
    for _ in range(MAX_ID_GENERATION_ATTEMPTS):
        try:
            candidate = factory()
        except Exception:
            continue
        if type(candidate) is UUID and candidate.version == 4 and candidate not in reserved:
            return candidate
    reject_page_operation(PageOperationErrorCode.ID_GENERATION_FAILED, "history.identifier")


def _session_state_key(operations: Sequence[PageOperation]) -> bytes:
    """Return a compact deterministic key for one exact owned-operation order."""

    digest = hashlib.sha256()
    digest.update(len(operations).to_bytes(4, byteorder="big", signed=False))
    for operation in operations:
        digest.update(operation.operation_id.bytes)
    return digest.digest()


def _parse_snapshot(snapshot: ManifestSnapshot) -> _ParsedSnapshot:
    if type(snapshot) is not ManifestSnapshot:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.model")
    try:
        document = copy_manifest_document(snapshot.document, context="page.snapshot")
    except Exception:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.document")
    _require_members(document, _ROOT_MEMBERS, context="snapshot.root")
    if type(document["schema_version"]) is int and document["schema_version"] not in {1, 2}:
        reject_page_operation(PageOperationErrorCode.VERSION_UNSUPPORTED, "snapshot.version")
    if document["schema_version"] not in {1, 2} or document["case_id"] != str(snapshot.case_id):
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.identity")
    case_id = _parse_uuid(document["case_id"], context="snapshot.case_id")
    unrelated_namespace: set[UUID] = {case_id}
    sources = document["sources"]
    if type(sources) is not list or len(sources) > MAX_PAGE_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "snapshot.sources")
    source_page_counts: dict[UUID, int] = {}
    for source in sources:
        record = _require_members(source, _SOURCE_MEMBERS, context="snapshot.source")
        source_id = _parse_uuid(record["source_id"], context="snapshot.source_id")
        page_count = record["page_count"]
        if type(page_count) is not int or not 1 <= page_count <= MAX_PAGE_COUNT:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.page_count")
        if source_id in unrelated_namespace:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "snapshot.sources")
        unrelated_namespace.add(source_id)
        source_page_counts[source_id] = page_count

    raw_pages = document["pages"]
    if type(raw_pages) is not list or len(raw_pages) > MAX_PAGE_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "snapshot.pages")
    pages = tuple(_parse_page_reference(page) for page in raw_pages)
    page_ids: set[UUID] = set()
    source_indices: dict[UUID, set[int]] = {source_id: set() for source_id in source_page_counts}
    for page in pages:
        if page.page_id in page_ids:
            reject_page_operation(PageOperationErrorCode.PAGE_DUPLICATE, "snapshot.pages")
        if page.page_id in unrelated_namespace:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, "snapshot.pages")
        if (
            page.source_id not in source_page_counts
            or page.source_page_index >= source_page_counts[page.source_id]
            or page.source_page_index in source_indices[page.source_id]
        ):
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.page_source")
        source_indices[page.source_id].add(page.source_page_index)
        page_ids.add(page.page_id)
    if any(
        len(source_indices[source_id]) != page_count
        for source_id, page_count in source_page_counts.items()
    ):
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.page_catalog")
    unrelated_namespace.update(page_ids)

    _collect_record_namespace(
        document["assets"], "asset_id", unrelated_namespace, "snapshot.assets"
    )
    _collect_record_namespace(
        document["overlays"], "overlay_id", unrelated_namespace, "snapshot.overlays"
    )
    _collect_record_namespace(
        document["assignments"], "assignment_id", unrelated_namespace, "snapshot.assignments"
    )
    _collect_record_namespace(
        document["confirmations"],
        "confirmation_id",
        unrelated_namespace,
        "snapshot.confirmations",
    )
    export_history = document["export_history"]
    if type(export_history) is not dict or set(export_history) != {"storage_name", "entries"}:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.exports")
    _collect_record_namespace(
        export_history["entries"], "export_id", unrelated_namespace, "snapshot.exports"
    )

    base = _parse_uuid_sequence(document["page_sequence_base"], context="snapshot.base")
    final = _parse_uuid_sequence(document["page_sequence"], context="snapshot.final")
    raw_operations = document["page_operations"]
    if type(raw_operations) is not list or len(raw_operations) > MAX_PAGE_OPERATION_COUNT:
        reject_page_operation(PageOperationErrorCode.CAPACITY_EXCEEDED, "snapshot.operations")
    operations = tuple(_parse_operation(operation) for operation in raw_operations)
    if document["schema_version"] == 1 and any(
        operation.operation_version != LEGACY_PAGE_OPERATION_VERSION for operation in operations
    ):
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "snapshot.operations")
    projection = _replay_page_operations(
        pages,
        base,
        operations,
        final_sequence=final,
        reserved_namespace=frozenset(unrelated_namespace - page_ids - set(source_page_counts)),
    )
    complete_namespace = set(unrelated_namespace)
    complete_namespace.update(operation.operation_id for operation in operations)
    complete_namespace.update(
        operation.payload.group_id
        for operation in operations
        if type(operation.payload) is SetGroupPayload
    )
    return _ParsedSnapshot(
        document=document,
        case_id=case_id,
        pages=pages,
        page_sequence_base=base,
        operations=operations,
        projection=projection,
        unrelated_namespace=frozenset(unrelated_namespace),
        complete_namespace=frozenset(complete_namespace),
    )


def _collect_record_namespace(
    value: object,
    id_field: str,
    namespace: set[UUID],
    context: str,
) -> None:
    if type(value) is not list or len(value) > MAX_PAGE_COUNT:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
    for value_record in value:
        if type(value_record) is not dict or id_field not in value_record:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, context)
        identifier = _parse_uuid(value_record[id_field], context=context)
        if identifier in namespace:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_COLLISION, context)
        namespace.add(identifier)
