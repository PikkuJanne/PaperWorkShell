"""Path-free contracts for declarative template loading and presentation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Final, Never, Protocol, cast, runtime_checkable

SUPPORTED_TEMPLATE_SCHEMA_VERSION: Final = 1
MAX_TEMPLATE_BYTES: Final = 1_048_576
MAX_TEMPLATE_DEPTH: Final = 48
MAX_TEMPLATE_NODES: Final = 32_768
MAX_TEMPLATE_STRING_CODEPOINTS: Final = 65_536
MAX_TEMPLATE_COLLECTION_ITEMS: Final = 4_096

_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SEMANTIC_VERSION: Final = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")


class TemplateLoadErrorCode(StrEnum):
    """Stable, content-free template loading failures."""

    MODEL_INVALID = "TEMPLATE_MODEL_INVALID"
    NOT_FOUND = "TEMPLATE_NOT_FOUND"
    REPOSITORY_UNAVAILABLE = "TEMPLATE_REPOSITORY_UNAVAILABLE"
    SOURCE_UNSAFE = "TEMPLATE_SOURCE_UNSAFE"
    SOURCE_CHANGED = "TEMPLATE_SOURCE_CHANGED"
    SIZE_LIMIT_EXCEEDED = "TEMPLATE_SIZE_LIMIT_EXCEEDED"
    INTEGRITY_MISMATCH = "TEMPLATE_INTEGRITY_MISMATCH"
    SIGNATURE_INVALID = "TEMPLATE_SIGNATURE_INVALID"
    JSON_INVALID = "TEMPLATE_JSON_INVALID"
    JSON_DUPLICATE_KEY = "TEMPLATE_JSON_DUPLICATE_KEY"
    JSON_NUMBER_INVALID = "TEMPLATE_JSON_NUMBER_INVALID"
    JSON_RESOURCE_LIMIT = "TEMPLATE_JSON_RESOURCE_LIMIT"
    VERSION_INVALID = "TEMPLATE_VERSION_INVALID"
    VERSION_UNSUPPORTED = "TEMPLATE_VERSION_UNSUPPORTED"
    EXECUTABLE_MEMBER = "TEMPLATE_EXECUTABLE_MEMBER"
    ELIGIBILITY_MEMBER = "TEMPLATE_ELIGIBILITY_MEMBER"
    TRUST_ASSERTION_MEMBER = "TEMPLATE_TRUST_ASSERTION_MEMBER"
    TEXT_UNSAFE = "TEMPLATE_TEXT_UNSAFE"
    SCHEMA_IDENTITY_MISMATCH = "TEMPLATE_SCHEMA_IDENTITY_MISMATCH"
    SCHEMA_INVALID = "TEMPLATE_SCHEMA_INVALID"
    SEMANTIC_INVALID = "TEMPLATE_SEMANTIC_INVALID"
    KEY_MISMATCH = "TEMPLATE_KEY_MISMATCH"
    LIFECYCLE_INVALID = "TEMPLATE_LIFECYCLE_INVALID"
    EXPIRED = "TEMPLATE_EXPIRED"


_SAFE_ERROR_MESSAGES: Final[dict[TemplateLoadErrorCode, str]] = {
    TemplateLoadErrorCode.MODEL_INVALID: "The template request is invalid.",
    TemplateLoadErrorCode.NOT_FOUND: "The selected template is not available.",
    TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE: (
        "PaperWorkShell could not read the local template safely."
    ),
    TemplateLoadErrorCode.SOURCE_UNSAFE: (
        "The local template is stored in an unsupported or unsafe location."
    ),
    TemplateLoadErrorCode.SOURCE_CHANGED: (
        "The local template changed while it was being checked. Try again."
    ),
    TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED: (
        "The selected template exceeds the supported size limit."
    ),
    TemplateLoadErrorCode.INTEGRITY_MISMATCH: (
        "The selected template does not match its trusted checksum and was not loaded."
    ),
    TemplateLoadErrorCode.SIGNATURE_INVALID: (
        "The selected template has invalid external signature evidence and was not loaded."
    ),
    TemplateLoadErrorCode.JSON_INVALID: "The selected template is not valid UTF-8 JSON.",
    TemplateLoadErrorCode.JSON_DUPLICATE_KEY: (
        "The selected template repeats a JSON member and was not loaded."
    ),
    TemplateLoadErrorCode.JSON_NUMBER_INVALID: (
        "The selected template contains an unsupported numeric value."
    ),
    TemplateLoadErrorCode.JSON_RESOURCE_LIMIT: (
        "The selected template exceeds a supported structural limit."
    ),
    TemplateLoadErrorCode.VERSION_INVALID: (
        "The selected template does not declare a valid schema version."
    ),
    TemplateLoadErrorCode.VERSION_UNSUPPORTED: (
        "This template version is not supported by this PaperWorkShell build."
    ),
    TemplateLoadErrorCode.EXECUTABLE_MEMBER: (
        "Templates may contain guidance data only, not commands or executable behavior."
    ),
    TemplateLoadErrorCode.ELIGIBILITY_MEMBER: (
        "Templates may contain packaging guidance only, not eligibility decisions."
    ),
    TemplateLoadErrorCode.TRUST_ASSERTION_MEMBER: (
        "A template cannot declare itself trusted or approved."
    ),
    TemplateLoadErrorCode.TEXT_UNSAFE: (
        "The selected template contains ambiguous or unsupported text controls."
    ),
    TemplateLoadErrorCode.SCHEMA_IDENTITY_MISMATCH: (
        "PaperWorkShell could not authenticate its packaged template rules."
    ),
    TemplateLoadErrorCode.SCHEMA_INVALID: (
        "The selected template does not follow the supported declarative template rules."
    ),
    TemplateLoadErrorCode.SEMANTIC_INVALID: (
        "The selected template contains inconsistent packaging guidance."
    ),
    TemplateLoadErrorCode.KEY_MISMATCH: (
        "The selected template does not match the requested template identity."
    ),
    TemplateLoadErrorCode.LIFECYCLE_INVALID: (
        "The selected template has inconsistent verification or review dates."
    ),
    TemplateLoadErrorCode.EXPIRED: (
        "The selected template is expired. Choose a current template or obtain reviewed guidance."
    ),
}


@dataclass(slots=True, repr=False)
class TemplateLoadError(ValueError):
    """Sanitized failure that never retains template text, paths, URLs, or digests."""

    code: TemplateLoadErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not TemplateLoadErrorCode:
            self.code = TemplateLoadErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "template.load"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"TemplateLoadError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_template(code: TemplateLoadErrorCode, context: str) -> Never:
    """Raise one stable, sanitized template failure."""

    # Suppress a caller-owned ambient exception if validation is invoked from
    # inside an unrelated handler. Internal adapters additionally leave their
    # own handlers before calling this helper so raw failures are not retained.
    raise TemplateLoadError(code=code, context=context) from None


class TemplateSignatureStatus(StrEnum):
    """Result from a trusted verifier outside embedded template content."""

    NOT_PROVIDED = "not_provided"
    VERIFIED = "verified"
    INVALID = "invalid"


class TemplateFreshness(StrEnum):
    """Lifecycle state at one trusted evaluation date."""

    CURRENT = "current"
    REVIEW_DUE = "review_due"


@dataclass(frozen=True, slots=True)
class TemplateKey:
    """Opaque catalog identity; never a path or URL."""

    template_id: str
    template_version: str

    def __post_init__(self) -> None:
        if (
            type(self.template_id) is not str
            or not 1 <= len(self.template_id) <= 128
            or _TOKEN.fullmatch(self.template_id) is None
            or type(self.template_version) is not str
            or not 1 <= len(self.template_version) <= 32
            or _SEMANTIC_VERSION.fullmatch(self.template_version) is None
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.key")


@dataclass(frozen=True, slots=True, repr=False)
class TemplateArtifactEvidence:
    """Trusted catalog evidence bound to the exact local artifact bytes."""

    byte_size: int
    sha256: str
    catalog_verified_on: date
    signature_status: TemplateSignatureStatus = TemplateSignatureStatus.NOT_PROVIDED

    def __post_init__(self) -> None:
        if (
            type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_TEMPLATE_BYTES
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or _SHA256.fullmatch(self.sha256) is None
            or type(self.catalog_verified_on) is not date
            or type(self.signature_status) is not TemplateSignatureStatus
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.evidence")

    def __repr__(self) -> str:
        return (
            "TemplateArtifactEvidence("
            f"byte_size={self.byte_size}, catalog_verified_on={self.catalog_verified_on!r}, "
            f"signature_status={self.signature_status.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class TemplateByteSnapshot:
    """One immutable repository result with no filesystem authority."""

    key: TemplateKey
    content: bytes = field(repr=False)
    evidence: TemplateArtifactEvidence

    def __post_init__(self) -> None:
        if type(self.key) is not TemplateKey or type(self.content) is not bytes:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.byte_snapshot")
        if not self.content:
            reject_template(TemplateLoadErrorCode.JSON_INVALID, "template.byte_snapshot")
        if len(self.content) > MAX_TEMPLATE_BYTES:
            reject_template(TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED, "template.byte_snapshot")
        if type(self.evidence) is not TemplateArtifactEvidence:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.byte_snapshot")

    def __repr__(self) -> str:
        return f"TemplateByteSnapshot(key={self.key!r}, byte_size={len(self.content)})"


@runtime_checkable
class TemplateRepository(Protocol):
    """Path-free source of exact template bytes and external trust evidence."""

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        """Read one catalog-selected artifact without accepting caller paths."""


@dataclass(frozen=True, slots=True, repr=False)
class TemplateDisplayField:
    """One explicit label/value pair ready for accessible plain-text presentation."""

    label: str
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.label) is not str
            or not self.label
            or type(self.value) is not str
            or not self.value
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.display_field")

    def __repr__(self) -> str:
        return f"TemplateDisplayField(label={self.label!r}, value='<redacted>')"


@dataclass(frozen=True, slots=True, repr=False)
class TemplatePresentation:
    """UI-ready provenance, integrity, and lifecycle facts with honest claim labels."""

    title: str = field(repr=False)
    locale: str
    freshness: TemplateFreshness
    signature_status: TemplateSignatureStatus
    fields: tuple[TemplateDisplayField, ...] = field(repr=False)
    status_message: str
    provenance_notice: str = (
        "Publisher and source details are claims embedded in the template. "
        "PaperWorkShell verified the exact local template bytes against its trusted catalog; "
        "it does not guarantee eligibility, legal correctness, or portal acceptance."
    )

    def __post_init__(self) -> None:
        if (
            type(self.title) is not str
            or not self.title
            or type(self.locale) is not str
            or not self.locale
            or type(self.freshness) is not TemplateFreshness
            or type(self.signature_status) is not TemplateSignatureStatus
            or type(self.fields) is not tuple
            or not self.fields
            or any(type(item) is not TemplateDisplayField for item in self.fields)
            or type(self.status_message) is not str
            or not self.status_message
            or type(self.provenance_notice) is not str
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.presentation")

    def __repr__(self) -> str:
        return (
            "TemplatePresentation("
            f"freshness={self.freshness.value!r}, "
            f"signature_status={self.signature_status.value!r}, field_count={len(self.fields)})"
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class TemplateSnapshot:
    """Validated template data with defensive document access and no path authority."""

    key: TemplateKey
    schema_version: int
    byte_size: int
    sha256: str = field(repr=False)
    freshness: TemplateFreshness
    presentation: TemplatePresentation = field(repr=False)
    _document: _FrozenTemplateObject = field(repr=False)

    def __init__(
        self,
        *,
        key: TemplateKey,
        byte_size: int,
        sha256: str,
        freshness: TemplateFreshness,
        presentation: TemplatePresentation,
        document: Mapping[str, object],
    ) -> None:
        if (
            type(key) is not TemplateKey
            or type(byte_size) is not int
            or not 1 <= byte_size <= MAX_TEMPLATE_BYTES
            or type(sha256) is not str
            or _SHA256.fullmatch(sha256) is None
            or type(freshness) is not TemplateFreshness
            or type(presentation) is not TemplatePresentation
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot")
        copied = copy_template_document(document, context="template.snapshot_document")
        if copied.get("schema_version") != SUPPORTED_TEMPLATE_SCHEMA_VERSION:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_version")
        if (
            copied.get("template_id") != key.template_id
            or copied.get("template_version") != key.template_version
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_key")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "schema_version", SUPPORTED_TEMPLATE_SCHEMA_VERSION)
        object.__setattr__(self, "byte_size", byte_size)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "freshness", freshness)
        object.__setattr__(self, "presentation", presentation)
        frozen_document = _freeze_template_value(copied)
        if type(frozen_document) is not _FrozenTemplateObject:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_document")
        object.__setattr__(self, "_document", frozen_document)

    @property
    def document(self) -> dict[str, object]:
        """Return a fresh bounded JSON-compatible copy."""

        thawed = _thaw_template_value(self._document)
        if type(thawed) is not dict:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_document")
        return cast(dict[str, object], thawed)

    def __repr__(self) -> str:
        return (
            "TemplateSnapshot("
            f"key={self.key!r}, schema_version={self.schema_version}, "
            f"byte_size={self.byte_size}, freshness={self.freshness.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _FrozenTemplateArray:
    """Private immutable storage for one validated JSON array."""

    items: tuple[object, ...] = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _FrozenTemplateObject:
    """Private immutable storage for one validated JSON object."""

    items: tuple[tuple[str, object], ...] = field(repr=False)


def _freeze_template_value(value: object) -> object:
    """Freeze an already bounded defensive JSON copy recursively."""

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is list:
        return _FrozenTemplateArray(tuple(_freeze_template_value(item) for item in value))
    if type(value) is dict:
        mapping = cast(dict[str, object], value)
        return _FrozenTemplateObject(
            tuple((key, _freeze_template_value(item)) for key, item in mapping.items())
        )
    reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_document")


def _thaw_template_value(value: object) -> object:
    """Return fresh mutable JSON data from private immutable storage."""

    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is _FrozenTemplateArray:
        return [_thaw_template_value(item) for item in value.items]
    if type(value) is _FrozenTemplateObject:
        return {key: _thaw_template_value(item) for key, item in value.items}
    reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.snapshot_document")


def copy_template_document(
    document: Mapping[str, object],
    *,
    context: str = "template.document",
) -> dict[str, object]:
    """Defensively copy one bounded acyclic JSON object."""

    if not isinstance(document, Mapping):
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
    budget = [MAX_TEMPLATE_NODES]
    active: set[int] = set()
    copied = _copy_json_value(document, depth=1, budget=budget, active=active, context=context)
    if type(copied) is not dict:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
    return copied


def _copy_json_value(
    value: object,
    *,
    depth: int,
    budget: list[int],
    active: set[int],
    context: str,
) -> object:
    if depth > MAX_TEMPLATE_DEPTH or budget[0] <= 0:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
    budget[0] -= 1
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str and len(value) > MAX_TEMPLATE_STRING_CODEPOINTS:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
        return value
    if type(value) not in {dict, list}:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
    identity = id(value)
    if identity in active:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
    active.add(identity)
    try:
        if type(value) is list:
            if len(value) > MAX_TEMPLATE_COLLECTION_ITEMS:
                reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
            return [
                _copy_json_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                    context=context,
                )
                for item in value
            ]
        mapping = cast(dict[object, object], value)
        if len(mapping) > MAX_TEMPLATE_COLLECTION_ITEMS:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
        copied: dict[str, object] = {}
        for key, item in mapping.items():
            if type(key) is not str or len(key) > MAX_TEMPLATE_STRING_CODEPOINTS:
                reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
            if budget[0] <= 0:
                reject_template(TemplateLoadErrorCode.MODEL_INVALID, context)
            budget[0] -= 1
            copied[key] = _copy_json_value(
                item,
                depth=depth + 1,
                budget=budget,
                active=active,
                context=context,
            )
        return copied
    finally:
        active.remove(identity)
