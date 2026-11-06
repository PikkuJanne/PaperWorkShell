"""Explicit, bounded plain-text companions for exact verified PDF derivatives.

Document text is user content. It is never part of a routine diagnostic, exception
or object representation. Extraction does not run OCR or consult source caches.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Never

from paperworkshell.domain.resource_policy import ResourcePolicy


class TextCompanionErrorCode(StrEnum):
    INVALID = "TEXT_COMPANION_INVALID"
    CONSENT_REQUIRED = "TEXT_COMPANION_CONSENT_REQUIRED"
    EXPORT_REQUIRED = "TEXT_COMPANION_EXPORT_REQUIRED"
    EXPORT_CHANGED = "TEXT_COMPANION_EXPORT_CHANGED"
    LIMIT_EXCEEDED = "TEXT_COMPANION_LIMIT_EXCEEDED"
    CANCELLED = "TEXT_COMPANION_CANCELLED"
    TIMED_OUT = "TEXT_COMPANION_TIMED_OUT"
    EXTRACTION_FAILED = "TEXT_COMPANION_EXTRACTION_FAILED"
    SAVE_FAILED = "TEXT_COMPANION_SAVE_FAILED"


_MESSAGES = {
    TextCompanionErrorCode.INVALID: "The text companion request could not be processed safely.",
    TextCompanionErrorCode.CONSENT_REQUIRED: "Review and confirm the text companion privacy warning first.",
    TextCompanionErrorCode.EXPORT_REQUIRED: "Save a verified package before creating its text companion.",
    TextCompanionErrorCode.EXPORT_CHANGED: "An exported file changed or is unavailable. Export the package again before creating its text companion.",
    TextCompanionErrorCode.LIMIT_EXCEEDED: "The text companion exceeds the available processing budget. Export a smaller packet.",
    TextCompanionErrorCode.CANCELLED: "Text companion creation was cancelled.",
    TextCompanionErrorCode.TIMED_OUT: "Text companion creation exceeded its processing time limit.",
    TextCompanionErrorCode.EXTRACTION_FAILED: "Text could not be read safely from the exported PDF. The verified package remains saved.",
    TextCompanionErrorCode.SAVE_FAILED: "The text companion was not confirmed saved. Check the destination before retrying.",
}


@dataclass(slots=True, repr=False)
class TextCompanionError(ValueError):
    code: TextCompanionErrorCode
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not TextCompanionErrorCode:
            self.code = TextCompanionErrorCode.INVALID
        self.safe_message = _MESSAGES[self.code]
        self.args = (self.code.value, self.safe_message)

    def __repr__(self) -> str:
        return f"TextCompanionError(code={self.code.value!r})"

    def __str__(self) -> str:
        return self.safe_message


def reject_text_companion(code: TextCompanionErrorCode) -> Never:
    error = TextCompanionError(code)
    try:
        raise error from None
    finally:
        error.__context__ = error.__cause__ = None


@dataclass(frozen=True, slots=True)
class TextCompanionLimits:
    maximum_output_bytes: int
    maximum_source_bytes: int
    maximum_pages: int

    @classmethod
    def from_resource_policy(cls, policy: ResourcePolicy) -> TextCompanionLimits:
        if type(policy) is not ResourcePolicy:
            reject_text_companion(TextCompanionErrorCode.INVALID)
        return cls(
            min(
                policy.thumbnail_preview_cache_bytes // 8,
                policy.maximum_temporary_storage_bytes // 16,
            ),
            policy.maximum_temporary_storage_bytes,
            policy.maximum_page_count,
        )

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int or value < 1
                for value in (
                    self.maximum_output_bytes,
                    self.maximum_source_bytes,
                    self.maximum_pages,
                )
            )
            or self.maximum_pages > 1_000
        ):
            reject_text_companion(TextCompanionErrorCode.INVALID)


def sanitize_companion_text(text: str) -> str:
    """Preserve ordinary Unicode and paragraphs, removing invisible controls.

    Bidirectional override/control characters are removed rather than interpreted
    as instructions by the reader. The text remains plain UTF-8, never markup.
    """
    if type(text) is not str:
        reject_text_companion(TextCompanionErrorCode.INVALID)
    return "".join(
        char
        for char in text.replace("\r\n", "\n").replace("\r", "\n")
        if char in "\n\t\u200c\u200d"
        or unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
    )


def require_companion_active(deadline: object, cancellation: object) -> None:
    if not callable(deadline) or not callable(cancellation):
        reject_text_companion(TextCompanionErrorCode.INVALID)
    try:
        cancelled, expired = cancellation(), deadline()
    except Exception:
        reject_text_companion(TextCompanionErrorCode.INVALID)
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_text_companion(TextCompanionErrorCode.INVALID)
    if cancelled:
        reject_text_companion(TextCompanionErrorCode.CANCELLED)
    if expired:
        reject_text_companion(TextCompanionErrorCode.TIMED_OUT)
