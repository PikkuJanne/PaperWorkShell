"""Explicit, bounded plain-text package presentation without private diagnostics.

These values are user-facing content, not routine logging records. Representations
contain counts and closed states only; rendering requires an explicit call.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never

from paperworkshell.domain.checklist import ChecklistStatus, RequirementCountUnit
from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER

MAX_SUMMARY_ROWS: Final = 16_384
MAX_SUMMARY_TEXT_CODEPOINTS: Final = 8_388_608
MAX_SUMMARY_LINE_CODEPOINTS: Final = 8_192


class PackageSummaryErrorCode(StrEnum):
    MODEL_INVALID = "PACKAGE_SUMMARY_MODEL_INVALID"
    AUTHORITY_MISMATCH = "PACKAGE_SUMMARY_AUTHORITY_MISMATCH"
    LIMIT_EXCEEDED = "PACKAGE_SUMMARY_LIMIT_EXCEEDED"


_ERROR_MESSAGES: Final = {
    PackageSummaryErrorCode.MODEL_INVALID: "The package summary could not be prepared safely.",
    PackageSummaryErrorCode.AUTHORITY_MISMATCH: (
        "The case, checklist, checks, or export changed. Refresh the package summary."
    ),
    PackageSummaryErrorCode.LIMIT_EXCEEDED: (
        "This package has too many review details for one summary. Review a smaller packet."
    ),
}


@dataclass(slots=True, repr=False)
class PackageSummaryError(ValueError):
    """A closed error with no caller-provided context or retained source content."""

    code: PackageSummaryErrorCode
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackageSummaryErrorCode:
            self.code = PackageSummaryErrorCode.MODEL_INVALID
        self.safe_message = _ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.safe_message)

    def __repr__(self) -> str:
        return f"PackageSummaryError(code={self.code.value!r})"

    def __str__(self) -> str:
        return self.safe_message


def reject_package_summary(code: PackageSummaryErrorCode) -> Never:
    error = PackageSummaryError(code)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PackageSummaryStatus(StrEnum):
    PLANNED = "planned"
    NEEDS_REVIEW = "needs_review"
    COMMITTED = "committed"
    CONSTRAINTS_BLOCKED = "constraints_blocked"
    NOT_COMMITTED = "not_committed"
    PARTIAL = "partial"
    UNCERTAIN = "uncertain"


_STATUS_MESSAGES: Final = {
    PackageSummaryStatus.PLANNED: "Package planned. No files have been saved by this attempt.",
    PackageSummaryStatus.NEEDS_REVIEW: (
        "Package needs review. No files have been saved by this attempt."
    ),
    PackageSummaryStatus.COMMITTED: "Package saved. Review the files and remaining review items.",
    PackageSummaryStatus.CONSTRAINTS_BLOCKED: (
        "Package was not saved because output constraints were not met."
    ),
    PackageSummaryStatus.NOT_COMMITTED: "Package was not saved. Review the destination and retry.",
    PackageSummaryStatus.PARTIAL: (
        "Only part of the package was saved. Check each file status before retrying."
    ),
    PackageSummaryStatus.UNCERTAIN: (
        "Some file save results could not be confirmed. Check the destination before retrying."
    ),
}


class PackageSummaryOutputState(StrEnum):
    PLANNED = "Planned; not saved"
    COMMITTED = "Saved and reopened"
    NOT_COMMITTED = "Not saved"
    UNKNOWN = "Save result unknown"


class PackageSummaryReviewKind(StrEnum):
    BLOCKING_ERROR = "Blocking issue"
    WARNING = "Needs review"
    CONFIRMATION_REQUIRED = "Human confirmation required"


@dataclass(frozen=True, slots=True, repr=False)
class PackageSummaryReviewItem:
    kind: PackageSummaryReviewKind
    scope: str = field(repr=False)
    message: str = field(repr=False)
    remediation: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not PackageSummaryReviewKind:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        for value in (self.scope, self.message, self.remediation):
            require_summary_line(value)

    def __repr__(self) -> str:
        return f"PackageSummaryReviewItem(kind={self.kind.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PackageSummaryCategory:
    label: str = field(repr=False)
    status: ChecklistStatus
    assignment_count: int
    page_count: int
    confirmed_count: int
    count_unit: RequirementCountUnit
    minimum_count: int
    maximum_count: int

    def __post_init__(self) -> None:
        require_summary_line(self.label)
        if (
            type(self.status) is not ChecklistStatus
            or type(self.count_unit) is not RequirementCountUnit
        ):
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        for value in (
            self.assignment_count,
            self.page_count,
            self.confirmed_count,
            self.minimum_count,
            self.maximum_count,
        ):
            _require_count(value)
        if self.minimum_count > self.maximum_count:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)

    def __repr__(self) -> str:
        return (
            f"PackageSummaryCategory(status={self.status.value!r}, "
            f"assignment_count={self.assignment_count}, page_count={self.page_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PackageSummaryOutput:
    filename: str = field(repr=False)
    state: PackageSummaryOutputState
    page_count: int
    byte_size: int | None = None
    categories: tuple[str, ...] = field(default=(), repr=False)
    checks: tuple[str, ...] = field(default=(), repr=False)
    archive_member: bool = False

    def __post_init__(self) -> None:
        require_summary_line(self.filename)
        if (
            type(self.state) is not PackageSummaryOutputState
            or type(self.archive_member) is not bool
        ):
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        _require_count(self.page_count)
        if self.byte_size is not None:
            _require_count(self.byte_size)
        _require_lines(self.categories)
        _require_lines(self.checks)
        if self.state is PackageSummaryOutputState.COMMITTED and self.byte_size is None:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)

    def __repr__(self) -> str:
        return (
            f"PackageSummaryOutput(state={self.state.value!r}, page_count={self.page_count}, "
            f"byte_size={self.byte_size!r}, archive_member={self.archive_member!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PackageSummary:
    """Display-only snapshot; no approval token, private authority, or hidden metadata."""

    status: PackageSummaryStatus
    outputs: tuple[PackageSummaryOutput, ...] = field(default=(), repr=False)
    categories: tuple[PackageSummaryCategory, ...] = field(default=(), repr=False)
    checks: tuple[str, ...] = field(default=(), repr=False)
    review_items: tuple[PackageSummaryReviewItem, ...] = field(default=(), repr=False)
    active_page_count: int = 0
    unassigned_page_count: int = 0
    unconfirmed_page_count: int = 0
    checklist_available: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.status) is not PackageSummaryStatus
            or type(self.checklist_available) is not bool
        ):
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        for values, kind in (
            (self.outputs, PackageSummaryOutput),
            (self.categories, PackageSummaryCategory),
            (self.review_items, PackageSummaryReviewItem),
        ):
            if type(values) is not tuple or any(type(value) is not kind for value in values):
                reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
            if len(values) > MAX_SUMMARY_ROWS:
                reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
        _require_lines(self.checks)
        for count in (
            self.active_page_count,
            self.unassigned_page_count,
            self.unconfirmed_page_count,
        ):
            _require_count(count)
        if max(self.unassigned_page_count, self.unconfirmed_page_count) > self.active_page_count:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        if self.status is PackageSummaryStatus.COMMITTED and (
            not self.outputs
            or any(
                output.state is not PackageSummaryOutputState.COMMITTED for output in self.outputs
            )
        ):
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)

    @property
    def status_message(self) -> str:
        return _STATUS_MESSAGES[self.status]

    @property
    def file_count(self) -> int:
        """Top-level planned artifact count; ZIP members are separately itemized."""
        return sum(not item.archive_member for item in self.outputs)

    @property
    def saved_file_count(self) -> int:
        return sum(
            not item.archive_member and item.state is PackageSummaryOutputState.COMMITTED
            for item in self.outputs
        )

    def to_plain_text(self) -> str:
        return render_package_summary_text(self)

    def __repr__(self) -> str:
        return (
            f"PackageSummary(status={self.status.value!r}, file_count={self.file_count}, "
            f"category_count={len(self.categories)}, review_count={len(self.review_items)})"
        )


def require_summary_line(value: object) -> str:
    """Reject invisible controls and multiline injection; markup remains literal text."""
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)
    ):
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
    if len(value) > MAX_SUMMARY_LINE_CODEPOINTS:
        reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
    return value


def _require_count(value: object) -> None:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)


def _require_lines(values: tuple[str, ...]) -> None:
    if type(values) is not tuple:
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
    if len(values) > MAX_SUMMARY_ROWS:
        reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
    for value in values:
        require_summary_line(value)


def render_package_summary_text(summary: PackageSummary) -> str:
    """Return precisely the visible summary as plain text, with no HTML or diagnostics."""
    if type(summary) is not PackageSummary:
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
    lines: list[str] = []
    codepoints = 0

    def append(line: str = "") -> None:
        nonlocal codepoints
        codepoints += len(line) + 1
        if codepoints > MAX_SUMMARY_TEXT_CODEPOINTS:
            reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
        lines.append(line)

    append("PaperWorkShell package summary")
    append(summary.status_message)
    append(
        f"Files planned: {summary.file_count}. Files saved and reopened: {summary.saved_file_count}."
    )
    append(f"Active pages: {summary.active_page_count}.")
    top_level = tuple(item for item in summary.outputs if not item.archive_member)
    if top_level and all(item.byte_size is not None for item in top_level):
        append(f"Total verified file size: {sum(item.byte_size or 0 for item in top_level)} bytes.")
    else:
        append("Total file size: not yet verified.")
    append()
    append("Files and PDF members")
    if not summary.outputs:
        append("No output plan is available yet.")
    for position, output in enumerate(summary.outputs, 1):
        kind = "ZIP member" if output.archive_member else "File"
        append(f"{kind} {position}: {output.filename}")
        append(f"  Status: {output.state.value}.")
        append(f"  Pages: {output.page_count}.")
        append(
            "  Size: not yet verified."
            if output.byte_size is None
            else f"  Verified size: {output.byte_size} bytes."
        )
        if output.categories:
            append("  Assigned categories: " + "; ".join(output.categories) + ".")
        for check in output.checks:
            append("  Check complete: " + check)
    append()
    append("Checklist assignments")
    if not summary.checklist_available:
        append("Checklist details are unavailable. Review the case before sharing files.")
    for category in summary.categories:
        state = {
            ChecklistStatus.READY: "Assignments confirmed by the user",
            ChecklistStatus.MISSING: "Missing required assignments",
            ChecklistStatus.ADDED: "Added; needs review",
            ChecklistStatus.NEEDS_REVIEW: "Needs review",
        }[category.status]
        append(f"{category.label}: {state}.")
        append(
            f"  Assigned documents: {category.assignment_count}; pages: {category.page_count}; "
            f"confirmed {category.count_unit.value}: {category.confirmed_count}; "
            f"required range: {category.minimum_count} to {category.maximum_count}."
        )
    if summary.checklist_available:
        append(
            f"Unassigned pages: {summary.unassigned_page_count}. Unconfirmed pages: {summary.unconfirmed_page_count}."
        )
    append()
    append("Checks performed")
    if not summary.checks:
        append("No package checks are reported as complete yet.")
    for check in summary.checks:
        append(check)
    append()
    append("Warnings and required human review")
    for item in summary.review_items:
        append(f"{item.kind.value} - {item.scope}: {item.message}")
        append("  Next step: " + item.remediation)
    append("Review every intended page for readability, correct content, and completeness.")
    append(
        "Compare the package with the recipient's current instructions before submitting it yourself."
    )
    append(
        "These packaging checks do not establish portal acceptance, legal sufficiency, signature validity, "
        "OCR correctness, or permanent removal of information."
    )
    return "\n".join(lines) + "\n"


__all__ = (
    "PackageSummary",
    "PackageSummaryCategory",
    "PackageSummaryError",
    "PackageSummaryErrorCode",
    "PackageSummaryOutput",
    "PackageSummaryOutputState",
    "PackageSummaryReviewItem",
    "PackageSummaryReviewKind",
    "PackageSummaryStatus",
    "render_package_summary_text",
)
