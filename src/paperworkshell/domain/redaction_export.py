"""Closed, transient facts about one removal export attempt.

This report describes a result; even an all-pass report grants no publication
authority or independent release approval. Authored removal marks stay unapplied.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Self
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_verification import (
    RUNTIME_METHODS,
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    RedactionVerificationReport,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.localization.catalog import tr

REDACTION_EXPORT_REPORT_VERSION: Final = 1
RUNTIME_EXPORT_REPORT_VERSION: Final = 2
_MAX_AFFECTED_PAGES: Final = 4096


class RedactionExportStatus(StrEnum):
    BLOCKED = "blocked"
    NOT_COMMITTED = "not_committed"
    COMMITTED = "committed"
    UNCERTAIN = "uncertain"


class RedactionExportCode(StrEnum):
    WORKFLOW_REQUIRED = "REDACTION_EXPORT_WORKFLOW_REQUIRED"
    VERIFICATION_FAILED = "REDACTION_EXPORT_VERIFICATION_FAILED"
    VERIFICATION_UNKNOWN = "REDACTION_EXPORT_VERIFICATION_UNKNOWN"
    RELEASE_PENDING = "REDACTION_EXPORT_RELEASE_PENDING"
    STALE_INTENT = "REDACTION_EXPORT_STALE_INTENT"
    CANCELLED = "REDACTION_EXPORT_CANCELLED"
    TIMED_OUT = "REDACTION_EXPORT_TIMED_OUT"
    RESULT_INVALID = "REDACTION_EXPORT_RESULT_INVALID"
    DESTINATION_FAILED = "REDACTION_EXPORT_DESTINATION_FAILED"
    DESTINATION_EXISTS = "REDACTION_EXPORT_DESTINATION_EXISTS"
    DESTINATION_UNCERTAIN = "REDACTION_EXPORT_DESTINATION_UNCERTAIN"
    COMPLETE = "REDACTION_EXPORT_COMPLETE"


_MESSAGES: Final = {
    RedactionExportCode.WORKFLOW_REQUIRED: (
        "Saved removal marks require the separate Export removal derivative action. "
        "Review the marks and choose a new file there. No removal checks ran and no derivative "
        "was committed by this ordinary export attempt."
    ),
    RedactionExportCode.VERIFICATION_FAILED: (
        "Export blocked because a required removal check failed. Review the marks, then retry. "
        "No verified removal export was committed."
    ),
    RedactionExportCode.VERIFICATION_UNKNOWN: (
        "Export blocked because a required removal check is incomplete. Review the method results "
        "before retrying. No verified removal export was committed."
    ),
    RedactionExportCode.RELEASE_PENDING: (
        "Permanent-removal export remains disabled until independent security approval. "
        "The method list shows only checks actually performed. No export was committed."
    ),
    RedactionExportCode.STALE_INTENT: (
        "Export blocked because the case or removal marks changed. Review the current marks, "
        "then start a new attempt. No export was committed."
    ),
    RedactionExportCode.CANCELLED: "Export cancelled before commit. Review the marks or retry.",
    RedactionExportCode.TIMED_OUT: (
        "The export attempt took too long and stopped before commit. Review the marks or retry."
    ),
    RedactionExportCode.RESULT_INVALID: (
        "Export blocked because the result could not be authenticated. Review the marks or retry. "
        "No export was committed."
    ),
    RedactionExportCode.DESTINATION_FAILED: (
        "The verified candidate could not be saved. Choose a writable destination and retry. "
        "No export was committed."
    ),
    RedactionExportCode.DESTINATION_EXISTS: (
        "A file already exists at the chosen destination. Choose a new filename and retry. "
        "The existing file was preserved."
    ),
    RedactionExportCode.DESTINATION_UNCERTAIN: (
        "The destination result is uncertain. Inspect the destination before starting another "
        "export. This attempt is not reported as complete."
    ),
    RedactionExportCode.COMPLETE: (
        "The exact unsigned derivative passed every required removal check and was saved and "
        "reopened. Original files and saved removal marks are unchanged."
    ),
}
_METHOD_LABELS: Final = {
    VerificationMethod.STRUCTURE: "Objects, revisions, metadata, attachments, forms and actions",
    VerificationMethod.TEXT: "Text selection, search and extraction",
    VerificationMethod.IMAGES: "Image extraction",
    VerificationMethod.RENDER: "Page rendering",
    VerificationMethod.RESAVE: "Save and resave",
    VerificationMethod.OCR: "Independent OCR",
    VerificationMethod.VIEWERS: "Viewer matrix",
}
_STATUS_LABELS: Final = {
    RedactionExportStatus.BLOCKED: "Export blocked",
    RedactionExportStatus.NOT_COMMITTED: "Export not committed",
    RedactionExportStatus.COMMITTED: "Export complete — unsigned derivative",
    RedactionExportStatus.UNCERTAIN: "Export outcome uncertain",
}


def _invalid() -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_export.report")


def _uuid(value: object) -> UUID:
    if type(value) is not UUID or value.version != 4:
        _invalid()
    return value


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        _invalid()
    try:
        result = UUID(value)
    except ValueError:
        _invalid()
    if str(result) != value:
        _invalid()
    return _uuid(result)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionExportReport:
    """Sanitized result facts, never a capability to publish or promote marks."""

    case_id: UUID
    manifest_revision: int
    affected_page_ids: tuple[UUID, ...]
    verification: RedactionVerificationReport | None = field(repr=False)
    status: RedactionExportStatus
    code: RedactionExportCode
    report_version: int = REDACTION_EXPORT_REPORT_VERSION
    unsigned_derivative: bool = True

    def __post_init__(self) -> None:
        _uuid(self.case_id)
        if (
            type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.affected_page_ids) is not tuple
            or not 1 <= len(self.affected_page_ids) <= _MAX_AFFECTED_PAGES
            or any(type(page_id) is not UUID for page_id in self.affected_page_ids)
            or len(set(self.affected_page_ids)) != len(self.affected_page_ids)
            or type(self.status) is not RedactionExportStatus
            or type(self.code) is not RedactionExportCode
            or type(self.report_version) is not int
            or self.report_version
            not in {REDACTION_EXPORT_REPORT_VERSION, RUNTIME_EXPORT_REPORT_VERSION}
            or self.unsigned_derivative is not True
        ):
            _invalid()
        for page_id in self.affected_page_ids:
            _uuid(page_id)
        if self.case_id in self.affected_page_ids:
            _invalid()
        if self.verification is not None:
            if type(self.verification) is not RedactionVerificationReport:
                _invalid()
            self.verification.__post_init__()
        runtime = self.report_version == RUNTIME_EXPORT_REPORT_VERSION
        if self.code is RedactionExportCode.WORKFLOW_REQUIRED and (
            not runtime or self.verification is not None
        ):
            _invalid()
        if (
            runtime
            and self.verification is not None
            and any(
                item.method is VerificationMethod.VIEWERS and item.status is VerificationStatus.PASS
                for item in self.verification.methods
            )
        ):
            # A qualified release never fabricates a named-viewer observation
            # for newly generated candidate bytes.
            _invalid()
        complete = self.verification is not None and (
            self.verification.runtime_ready if runtime else self.verification.release_ready
        )
        failed = self.verification is not None and any(
            method.status is VerificationStatus.FAIL for method in self.verification.methods
        )
        unknown = self.verification is None or any(
            method.status is VerificationStatus.UNKNOWN
            for method in self.verification.methods
            if not runtime or method.method in RUNTIME_METHODS
        )
        allowed = {
            RedactionExportStatus.BLOCKED: {
                RedactionExportCode.WORKFLOW_REQUIRED,
                RedactionExportCode.VERIFICATION_FAILED,
                RedactionExportCode.VERIFICATION_UNKNOWN,
                RedactionExportCode.RELEASE_PENDING,
                RedactionExportCode.STALE_INTENT,
                RedactionExportCode.RESULT_INVALID,
            },
            RedactionExportStatus.NOT_COMMITTED: {
                RedactionExportCode.CANCELLED,
                RedactionExportCode.TIMED_OUT,
                RedactionExportCode.DESTINATION_FAILED,
                RedactionExportCode.DESTINATION_EXISTS,
            },
            RedactionExportStatus.COMMITTED: {RedactionExportCode.COMPLETE},
            RedactionExportStatus.UNCERTAIN: {RedactionExportCode.DESTINATION_UNCERTAIN},
        }
        if (
            self.code not in allowed[self.status]
            or (self.code is RedactionExportCode.VERIFICATION_FAILED and not failed)
            or (self.code is RedactionExportCode.VERIFICATION_UNKNOWN and (not unknown or failed))
            or (
                self.code
                in {
                    RedactionExportCode.DESTINATION_FAILED,
                    RedactionExportCode.DESTINATION_EXISTS,
                    RedactionExportCode.DESTINATION_UNCERTAIN,
                    RedactionExportCode.COMPLETE,
                }
                and not complete
            )
        ):
            _invalid()

    def __repr__(self) -> str:
        return f"RedactionExportReport(status={self.status.value!r}, code={self.code.value!r})"

    @property
    def methods(self) -> tuple[VerificationMethodResult, ...]:
        self.__post_init__()
        if self.verification is not None:
            return self.verification.methods
        return tuple(
            VerificationMethodResult(method, VerificationStatus.UNKNOWN, VerificationCode.MISSING)
            for method in VerificationMethod
        )

    @property
    def safe_message(self) -> str:
        self.__post_init__()
        return _MESSAGES[self.code]

    @property
    def status_label(self) -> str:
        self.__post_init__()
        return _STATUS_LABELS[self.status]

    @property
    def retry_allowed(self) -> bool:
        self.__post_init__()
        return self.status in {
            RedactionExportStatus.BLOCKED,
            RedactionExportStatus.NOT_COMMITTED,
        } and self.code not in {
            RedactionExportCode.RELEASE_PENDING,
            RedactionExportCode.STALE_INTENT,
        }

    @property
    def review_allowed(self) -> bool:
        self.__post_init__()
        return self.status is not RedactionExportStatus.COMMITTED

    @property
    def user_text(self) -> str:
        self.__post_init__()
        lines = [
            tr(self.status_label),
            tr(self.safe_message),
            tr("Derivative status: unsigned derivative."),
            tr("Saved marks remain unapplied intent; originals are unchanged."),
            tr("Affected pages: {count}", count=len(self.affected_page_ids)),
            *(tr("Page ID: {page_id}", page_id=str(page_id)) for page_id in self.affected_page_ids),
            "",
            tr("Required removal checks:"),
        ]
        lines.extend(
            tr(
                "{method}: {status} ({code})",
                method=tr(_METHOD_LABELS[item.method]),
                status=tr(item.status.value),
                code=item.code.value,
            )
            for item in self.methods
            if self.report_version == REDACTION_EXPORT_REPORT_VERSION
            or item.method in RUNTIME_METHODS
        )
        if self.report_version == RUNTIME_EXPORT_REPORT_VERSION:
            lines.extend(
                (
                    "",
                    tr(
                        "Viewer compatibility requires separate release-profile qualification. "
                        "This file was not opened in the named viewer matrix."
                    ),
                )
            )
        lines.extend(("", tr("Result code: {code}", code=self.code.value)))
        return "\n".join(lines)

    def to_audit_record(self) -> dict[str, object]:
        """Return only closed codes, internal IDs, statuses, counts and versions."""
        self.__post_init__()
        record: dict[str, object] = {
            "report_version": self.report_version,
            "case_id": str(self.case_id),
            "manifest_revision": self.manifest_revision,
            "affected_page_ids": [str(page_id) for page_id in self.affected_page_ids],
            "affected_page_count": len(self.affected_page_ids),
            "status": self.status.value,
            "code": self.code.value,
            "unsigned_derivative": True,
            "methods": [item.to_record() for item in self.methods],
        }
        if self.report_version == RUNTIME_EXPORT_REPORT_VERSION:
            record.update(
                verification_profile_id=RUNTIME_VERIFICATION_PROFILE,
                required_methods=[method.value for method in RUNTIME_METHODS],
                viewer_matrix_scope="release_profile",
            )
        return record

    @property
    def verification_profile_id(self) -> str:
        self.__post_init__()
        return (
            RUNTIME_VERIFICATION_PROFILE
            if self.report_version == RUNTIME_EXPORT_REPORT_VERSION
            else VERIFICATION_PROFILE
        )

    def to_audit_text(self) -> str:
        return json.dumps(self.to_audit_record(), ensure_ascii=True, sort_keys=True, indent=2)

    def to_record(self) -> dict[str, object]:
        """Private correlation record; use to_audit_record for user/support output."""
        self.__post_init__()
        return {
            "report_version": self.report_version,
            "case_id": str(self.case_id),
            "manifest_revision": self.manifest_revision,
            "affected_page_ids": [str(page_id) for page_id in self.affected_page_ids],
            "verification": None if self.verification is None else self.verification.to_record(),
            "status": self.status.value,
            "code": self.code.value,
            "unsigned_derivative": True,
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if (
            not isinstance(value, Mapping)
            or set(value) != set(cls.__dataclass_fields__)
            or type(value["affected_page_ids"]) is not list
            or not 1 <= len(value["affected_page_ids"]) <= _MAX_AFFECTED_PAGES
        ):
            _invalid()
        try:
            return cls(
                case_id=_parse_uuid(value["case_id"]),
                manifest_revision=value["manifest_revision"],
                affected_page_ids=tuple(_parse_uuid(item) for item in value["affected_page_ids"]),
                verification=(
                    None
                    if value["verification"] is None
                    else RedactionVerificationReport.from_record(value["verification"])
                ),
                status=RedactionExportStatus(value["status"]),
                code=RedactionExportCode(value["code"]),
                report_version=value["report_version"],
                unsigned_derivative=value["unsigned_derivative"],
            )
        except ValueError, TypeError:
            _invalid()
