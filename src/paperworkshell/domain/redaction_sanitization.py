"""Private hardened-profile sanitization intent and value-free audit counts.

Sanitization evidence never authorizes export or proves permanent redaction.
Independent adversarial verification remains a separate mandatory gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, Self
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export

SANITIZATION_VERSION: Final = 1
SANITIZATION_PROFILE_ID: Final = "redaction-sanitization-v1"
SANITIZATION_STATUS: Final = "awaiting_verification"
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_FIELDS: Final = frozenset(
    {
        "case_id",
        "source_id",
        "source_sha256",
        "source_byte_size",
        "source_page_count",
        "profile_id",
        "request_version",
    }
)
_INVENTORY_FIELDS: Final = frozenset(
    {
        "attachments",
        "annotations",
        "actions",
        "scripts",
        "form_fields",
        "metadata",
        "signatures",
        "incremental_revisions",
    }
)
_REPORT_FIELDS: Final = frozenset(
    {
        "request_sha256",
        "source_sha256",
        "output_sha256",
        "output_byte_size",
        "page_count",
        "before",
        "after",
        "unsigned_derivative",
        "report_version",
        "status",
    }
)


def _invalid() -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "sanitization.model")


def _uuid(value: object) -> UUID:
    if type(value) is not UUID or value.version != 4:
        _invalid()
    return value


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        _invalid()
    try:
        parsed = UUID(str(value))
    except ValueError, AttributeError:
        _invalid()
    if str(parsed) != value:
        _invalid()
    return _uuid(parsed)


def _digest(value: object) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _invalid()


def _integer(value: object, minimum: int, maximum: int = MAX_SAFE_MANIFEST_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class RedactionSanitizationRequest:
    """Authenticate one input derivative; no path, source values or executable policy."""

    case_id: UUID
    source_id: UUID
    source_sha256: str = field(repr=False)
    source_byte_size: int
    source_page_count: int
    profile_id: str = SANITIZATION_PROFILE_ID
    request_version: int = SANITIZATION_VERSION

    def __post_init__(self) -> None:
        _uuid(self.case_id)
        _uuid(self.source_id)
        _digest(self.source_sha256)
        _integer(self.source_byte_size, 1)
        _integer(self.source_page_count, 1, 4096)
        if (
            self.case_id == self.source_id
            or type(self.profile_id) is not str
            or self.profile_id != SANITIZATION_PROFILE_ID
            or type(self.request_version) is not int
            or self.request_version != SANITIZATION_VERSION
        ):
            _invalid()

    def __repr__(self) -> str:
        return "RedactionSanitizationRequest(profile_id='redaction-sanitization-v1')"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(payload).hexdigest()

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            name: str(value) if name in {"case_id", "source_id"} else value
            for name in _REQUEST_FIELDS
            for value in (getattr(self, name),)
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REQUEST_FIELDS:
            _invalid()
        return cls(
            case_id=_parse_uuid(value["case_id"]),
            source_id=_parse_uuid(value["source_id"]),
            source_sha256=value["source_sha256"],
            source_byte_size=value["source_byte_size"],
            source_page_count=value["source_page_count"],
            profile_id=value["profile_id"],
            request_version=value["request_version"],
        )


@dataclass(frozen=True, slots=True)
class RedactionSanitizationInventory:
    """Counts only: never include feature names, values, PDF keys or document text."""

    attachments: int = 0
    annotations: int = 0
    actions: int = 0
    scripts: int = 0
    form_fields: int = 0
    metadata: int = 0
    signatures: int = 0
    incremental_revisions: int = 0

    def __post_init__(self) -> None:
        for name in _INVENTORY_FIELDS:
            _integer(getattr(self, name), 0)

    @property
    def empty(self) -> bool:
        self.__post_init__()
        return not any(getattr(self, name) for name in _INVENTORY_FIELDS)

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {name: getattr(self, name) for name in _INVENTORY_FIELDS}

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _INVENTORY_FIELDS:
            _invalid()
        return cls(**{name: _integer(value[name], 0) for name in _INVENTORY_FIELDS})


@dataclass(frozen=True, slots=True, repr=False)
class RedactionSanitizationReport:
    """Bounded local audit evidence; only awaiting_verification is admissible."""

    request_sha256: str = field(repr=False)
    source_sha256: str = field(repr=False)
    output_sha256: str = field(repr=False)
    output_byte_size: int
    page_count: int
    before: RedactionSanitizationInventory
    after: RedactionSanitizationInventory
    unsigned_derivative: bool = True
    report_version: int = SANITIZATION_VERSION
    status: str = SANITIZATION_STATUS

    def __post_init__(self) -> None:
        for value in (self.request_sha256, self.source_sha256, self.output_sha256):
            _digest(value)
        _integer(self.output_byte_size, 1)
        _integer(self.page_count, 1, 4096)
        if (
            type(self.before) is not RedactionSanitizationInventory
            or type(self.after) is not RedactionSanitizationInventory
            or self.unsigned_derivative is not True
            or type(self.report_version) is not int
            or self.report_version != SANITIZATION_VERSION
            or type(self.status) is not str
            or self.status != SANITIZATION_STATUS
        ):
            _invalid()
        self.before.__post_init__()
        self.after.__post_init__()
        if not self.after.empty:
            _invalid()

    def __repr__(self) -> str:
        return "RedactionSanitizationReport(status='awaiting_verification')"

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            name: value.to_record() if name in {"before", "after"} else value
            for name in _REPORT_FIELDS
            for value in (getattr(self, name),)
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or frozenset(value) != _REPORT_FIELDS:
            _invalid()
        return cls(
            request_sha256=value["request_sha256"],
            source_sha256=value["source_sha256"],
            output_sha256=value["output_sha256"],
            output_byte_size=value["output_byte_size"],
            page_count=value["page_count"],
            before=RedactionSanitizationInventory.from_record(value["before"]),
            after=RedactionSanitizationInventory.from_record(value["after"]),
            unsigned_derivative=value["unsigned_derivative"],
            report_version=value["report_version"],
            status=value["status"],
        )


def validate_sanitization_report(
    request: RedactionSanitizationRequest, report: RedactionSanitizationReport
) -> None:
    if (
        type(request) is not RedactionSanitizationRequest
        or type(report) is not RedactionSanitizationReport
    ):
        _invalid()
    request.__post_init__()
    report.__post_init__()
    if (
        report.request_sha256 != request.fingerprint
        or report.source_sha256 != request.source_sha256
        or report.page_count != request.source_page_count
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.report_binding")
