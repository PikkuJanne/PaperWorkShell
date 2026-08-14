"""Toolkit-free contracts for provisional before-and-after scan review.

The values in this module are deliberately ephemeral.  They describe one
review session and its disposable previews; they never mutate a case manifest
or imply that a suggested adjustment has been accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.image_pages import (
    ImagePagePreviewResult,
    ImageQuarterTurn,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformResult,
    ImageTransformIntent,
)
from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER

SCAN_REVIEW_VERSION: Final = 1
SCAN_REVIEW_PREVIEW_VERSION: Final = 1
_CONTEXT_TOKEN: Final = re.compile(r"scan_review(?:\.[a-z][a-z0-9_]*)+")


class ScanReviewErrorCode(StrEnum):
    """Closed registry of sanitized scan-review failures."""

    MODEL_INVALID = "SCAN_REVIEW_MODEL_INVALID"
    ALREADY_ACTIVE = "SCAN_REVIEW_ALREADY_ACTIVE"
    NOT_ACTIVE = "SCAN_REVIEW_NOT_ACTIVE"
    SAVE_PENDING = "SCAN_REVIEW_SAVE_PENDING"
    MEDIA_UNSUPPORTED = "SCAN_REVIEW_MEDIA_UNSUPPORTED"
    STALE = "SCAN_REVIEW_STALE"
    PREVIEW_REQUIRED = "SCAN_REVIEW_PREVIEW_REQUIRED"
    PREVIEW_RESULT_INVALID = "SCAN_REVIEW_PREVIEW_RESULT_INVALID"


_SAFE_ERROR_MESSAGES: Final[dict[ScanReviewErrorCode, str]] = {
    ScanReviewErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid scan-review data."),
    ScanReviewErrorCode.ALREADY_ACTIVE: (
        "Finish or cancel the current scan review before starting another."
    ),
    ScanReviewErrorCode.NOT_ACTIVE: (
        "This scan review is no longer active. Start the review again."
    ),
    ScanReviewErrorCode.SAVE_PENDING: (
        "Wait for the current case changes to finish saving, then start scan review."
    ),
    ScanReviewErrorCode.MEDIA_UNSUPPORTED: (
        "Scan review is available for imported JPEG and PNG pages."
    ),
    ScanReviewErrorCode.STALE: (
        "The page changed while scan review was open. Start the review again."
    ),
    ScanReviewErrorCode.PREVIEW_REQUIRED: (
        "Update the adjusted preview before applying these scan adjustments."
    ),
    ScanReviewErrorCode.PREVIEW_RESULT_INVALID: (
        "PaperWorkShell received an invalid scan-review preview."
    ),
}


@dataclass(slots=True, repr=False)
class ScanReviewError(ValueError):
    """Stable failure that never retains page IDs, intent, pixels, or paths."""

    code: ScanReviewErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ScanReviewErrorCode:
            self.code = ScanReviewErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "scan_review.operation"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ScanReviewError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_scan_review(code: ScanReviewErrorCode, context: str) -> Never:
    """Raise one closed error without retaining an underlying exception."""

    error = ScanReviewError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class ScanReviewPreviewVariant(StrEnum):
    """The two concrete, non-color-only scan-review presentations."""

    ORIGINAL = "original"
    ADJUSTED = "adjusted"


@dataclass(frozen=True, slots=True, repr=False)
class ScanReviewDraft:
    """One immutable provisional adjustment bound to an exact case revision."""

    case_id: UUID
    page_id: UUID
    manifest_revision: int
    base_intent: ImageTransformIntent
    candidate_intent: ImageTransformIntent
    suggestion_present: bool = False
    review_version: int = SCAN_REVIEW_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or self.case_id == self.page_id
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.base_intent) is not ImageTransformIntent
            or type(self.candidate_intent) is not ImageTransformIntent
            or type(self.suggestion_present) is not bool
            or type(self.review_version) is not int
            or self.review_version != SCAN_REVIEW_VERSION
        ):
            reject_scan_review(ScanReviewErrorCode.MODEL_INVALID, "scan_review.draft")

    def with_candidate(
        self,
        intent: ImageTransformIntent,
        suggestion_present: bool = False,
    ) -> ScanReviewDraft:
        """Return a detached draft without accepting or persisting its candidate."""

        if type(intent) is not ImageTransformIntent or type(suggestion_present) is not bool:
            reject_scan_review(
                ScanReviewErrorCode.MODEL_INVALID,
                "scan_review.candidate",
            )
        return ScanReviewDraft(
            case_id=self.case_id,
            page_id=self.page_id,
            manifest_revision=self.manifest_revision,
            base_intent=self.base_intent,
            candidate_intent=intent,
            suggestion_present=suggestion_present,
            review_version=self.review_version,
        )

    def reset_to_original(self) -> ScanReviewDraft:
        """Reset only the provisional candidate to the immutable imported page."""

        return self.with_candidate(ImageTransformIntent())

    def __repr__(self) -> str:
        return (
            "ScanReviewDraft("
            f"manifest_revision={self.manifest_revision!r}, "
            f"suggestion_present={self.suggestion_present!r}, "
            f"review_version={self.review_version!r})"
        )


type ScanReviewImageResult = ImagePagePreviewResult | ImagePageTransformResult


@dataclass(frozen=True, slots=True, repr=False)
class ScanReviewPreviewResult:
    """One exact original or adjusted preview bound to its provisional draft."""

    draft: ScanReviewDraft
    variant: ScanReviewPreviewVariant
    result: ScanReviewImageResult = field(repr=False)
    preview_version: int = SCAN_REVIEW_PREVIEW_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.draft) is not ScanReviewDraft
            or type(self.variant) is not ScanReviewPreviewVariant
            or type(self.preview_version) is not int
            or self.preview_version != SCAN_REVIEW_PREVIEW_VERSION
        ):
            reject_scan_review(
                ScanReviewErrorCode.PREVIEW_RESULT_INVALID,
                "scan_review.preview_result",
            )
        result = self.result
        if self.variant is ScanReviewPreviewVariant.ORIGINAL:
            if (
                type(result) is not ImagePagePreviewResult
                or result.request.case_id != self.draft.case_id
                or result.request.page_id != self.draft.page_id
                or result.request.additional_rotation is not ImageQuarterTurn.DEGREES_0
            ):
                reject_scan_review(
                    ScanReviewErrorCode.PREVIEW_RESULT_INVALID,
                    "scan_review.original_result",
                )
            return
        if (
            type(result) is not ImagePageTransformResult
            or result.request.case_id != self.draft.case_id
            or result.request.page_id != self.draft.page_id
            or result.request.intent != self.draft.candidate_intent
        ):
            reject_scan_review(
                ScanReviewErrorCode.PREVIEW_RESULT_INVALID,
                "scan_review.adjusted_result",
            )

    @property
    def preview(self) -> ScanReviewImageResult:
        """Readable alias for presentation callers."""

        return self.result

    def __repr__(self) -> str:
        return (
            "ScanReviewPreviewResult("
            f"variant={self.variant.value!r}, preview_version={self.preview_version!r})"
        )
