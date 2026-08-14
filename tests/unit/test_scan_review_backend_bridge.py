"""Focused Qt-bridge contract tests for provisional scan review."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerPageView,
    OrganizerViewState,
)
from paperworkshell.app.page_previews import (
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgress,
    PagePreviewProgressPhase,
)
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewError,
    ScanReviewErrorCode,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)

CASE_ID = UUID("85300000-0000-4000-8000-000000000001")
PAGE_ID = UUID("85300000-0000-4000-8000-000000000002")
SOURCE_ID = UUID("85300000-0000-4000-8000-000000000003")
PROGRESS_ID = UUID("85300000-0000-4000-8000-000000000004")
WRONG_PAGE_ID = UUID("85300000-0000-4000-8000-000000000005")
PIXELS = bytes((16, 32, 48, 255))
PRIVATE_CANARY = r"SCAN_REVIEW_PRIVATE_C:\records\applicant-scan.png"


def _intent() -> ImageTransformIntent:
    return ImageTransformIntent(
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=750,
        contrast_factor_millionths=1_100_000,
        background_cleanup_strength_millionths=125_000,
    )


def _draft(*, suggested: bool = True) -> ScanReviewDraft:
    return ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=7,
        base_intent=ImageTransformIntent(),
        candidate_intent=_intent(),
        suggestion_present=suggested,
    )


def _state() -> OrganizerViewState:
    return OrganizerViewState(
        case_id=CASE_ID,
        revision=7,
        pages=(OrganizerPageView(PAGE_ID, 1, 90, True, _intent()),),
        can_undo=True,
        can_redo=False,
        autosave_pending=True,
    )


def _before_state(draft: ScanReviewDraft) -> OrganizerViewState:
    return OrganizerViewState(
        case_id=draft.case_id,
        revision=draft.manifest_revision,
        pages=(
            OrganizerPageView(
                draft.page_id,
                1,
                int(draft.base_intent.additional_rotation),
                True,
                draft.base_intent,
            ),
        ),
        can_undo=False,
        can_redo=False,
        autosave_pending=False,
    )


def _metadata() -> ImagePageMetadata:
    return ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=1,
        encoded_height_pixels=1,
        oriented_width_pixels=1,
        oriented_height_pixels=1,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _original_result(draft: ScanReviewDraft) -> ScanReviewPreviewResult:
    request = ImagePagePreviewRequest(draft.case_id, draft.page_id, 320, 240)
    snapshot = ImagePagePreviewSnapshot(
        case_id=draft.case_id,
        page_id=draft.page_id,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        pixels=PIXELS,
        metadata=_metadata(),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
    )
    return ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ORIGINAL,
        ImagePagePreviewResult(request, snapshot),
    )


def _adjusted_result(draft: ScanReviewDraft) -> ScanReviewPreviewResult:
    request = ImagePageTransformRequest(
        draft.case_id,
        draft.page_id,
        draft.candidate_intent,
        320,
        240,
    )
    snapshot = ImagePageTransformSnapshot(
        case_id=draft.case_id,
        source_id=SOURCE_ID,
        page_id=draft.page_id,
        intent=draft.candidate_intent,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        bytes_written=len(PIXELS),
        pixel_sha256=hashlib.sha256(PIXELS).hexdigest(),
        cache_key_sha256=hashlib.sha256(b"synthetic-cache-key").hexdigest(),
    )
    return ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        ImagePageTransformResult(request, snapshot, PIXELS),
    )


@dataclass(frozen=True, slots=True)
class _PreviewCall:
    draft: ScanReviewDraft
    variant: ScanReviewPreviewVariant
    maximum_width: int
    maximum_height: int
    timeout_seconds: float | None
    cancellation: Callable[[], bool]
    progress: Callable[[PagePreviewProgress], None]


class _ScanReviewBackend:
    startup_recovery_result = StartupRecoveryResult()

    def __init__(self, draft: ScanReviewDraft) -> None:
        self.draft = draft
        self.state = _before_state(draft)
        self.apply_result = _state()
        self.begin_calls: list[tuple[UUID, ImageTransformIntent | None]] = []
        self.preview_calls: list[_PreviewCall] = []
        self.apply_calls: list[ScanReviewDraft] = []
        self.cancel_calls: list[ScanReviewDraft] = []
        self.begin_error: BaseException | None = None
        self.preview_error: BaseException | None = None
        self.apply_error: BaseException | None = None
        self.cancel_error: BaseException | None = None
        self.preview_results = {
            ScanReviewPreviewVariant.ORIGINAL: _original_result(draft),
            ScanReviewPreviewVariant.ADJUSTED: _adjusted_result(draft),
        }

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def begin_scan_review(
        self,
        page_id: UUID,
        *,
        suggested_intent: ImageTransformIntent | None = None,
    ) -> ScanReviewDraft:
        self.begin_calls.append((page_id, suggested_intent))
        if self.begin_error is not None:
            raise self.begin_error
        return self.draft

    def get_scan_review_preview(
        self,
        draft: ScanReviewDraft,
        variant: ScanReviewPreviewVariant,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        *,
        timeout_seconds: float | None = None,
        cancellation: Callable[[], bool] | None = None,
        progress: Callable[[PagePreviewProgress], None] | None = None,
    ) -> ScanReviewPreviewResult:
        assert cancellation is not None
        assert progress is not None
        self.preview_calls.append(
            _PreviewCall(
                draft,
                variant,
                maximum_width_pixels,
                maximum_height_pixels,
                timeout_seconds,
                cancellation,
                progress,
            )
        )
        progress(
            PagePreviewProgress(
                PROGRESS_ID,
                PagePreviewProgressPhase.LOOKUP,
                completed_pages=0,
                total_pages=1,
            )
        )
        if self.preview_error is not None:
            raise self.preview_error
        return self.preview_results[variant]

    def apply_scan_review(self, draft: ScanReviewDraft) -> OrganizerViewState:
        self.apply_calls.append(draft)
        if self.apply_error is not None:
            raise self.apply_error
        self.state = self.apply_result
        return self.apply_result

    def cancel_scan_review(self, draft: ScanReviewDraft) -> None:
        self.cancel_calls.append(draft)
        if self.cancel_error is not None:
            raise self.cancel_error


class _InvalidApplyBackend(_ScanReviewBackend):
    def __init__(self, draft: ScanReviewDraft, result: object) -> None:
        super().__init__(draft)
        self._invalid_result = result

    def apply_scan_review(self, draft: ScanReviewDraft) -> OrganizerViewState:
        self.apply_calls.append(draft)
        return cast(OrganizerViewState, self._invalid_result)


def _bridge(
    backend: _ScanReviewBackend,
) -> tuple[OrganizerBackendCommands, OrganizerBackendWorker]:
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    worker.initialize()
    return commands, worker


@pytest.mark.parametrize("suggestion", (None, _intent()))
def test_commands_bind_begin_and_optional_suggestion_exactly(
    suggestion: ImageTransformIntent | None,
) -> None:
    base = ImageTransformIntent()
    draft = ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=7,
        base_intent=base,
        candidate_intent=base if suggestion is None else suggestion,
        suggestion_present=suggestion is not None,
    )
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    started: list[tuple[int, object]] = []
    failures: list[tuple[int, object]] = []
    worker.scan_review_started.connect(
        lambda operation_id, value: started.append((operation_id, value))
    )
    worker.scan_review_start_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.scan_review_begin_requested.emit(17, PAGE_ID, suggestion)

    assert len(backend.begin_calls) == 1
    assert backend.begin_calls[0][0] == PAGE_ID
    assert backend.begin_calls[0][1] is suggestion
    assert started == [(17, draft)]
    assert started[0][1] is draft
    assert failures == []


@pytest.mark.parametrize(
    ("suggestion", "draft"),
    (
        (
            None,
            ScanReviewDraft(
                CASE_ID,
                WRONG_PAGE_ID,
                7,
                ImageTransformIntent(),
                ImageTransformIntent(),
            ),
        ),
        (None, _draft(suggested=False)),
        (
            _intent(),
            ScanReviewDraft(
                CASE_ID,
                PAGE_ID,
                7,
                ImageTransformIntent(),
                ImageTransformIntent(),
                suggestion_present=False,
            ),
        ),
    ),
)
def test_mismatched_begin_result_is_discarded_before_ui_success(
    suggestion: ImageTransformIntent | None,
    draft: ScanReviewDraft,
) -> None:
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    started: list[object] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.scan_review_started.connect(lambda *_values: started.append(_values))
    worker.scan_review_start_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.scan_review_begin_requested.emit(18, PAGE_ID, suggestion)

    assert started == []
    assert backend.cancel_calls == [draft]
    assert len(failures) == 1
    operation_id, failure = failures[0]
    assert operation_id == 18
    assert failure.code == "PWS_UI_SCAN_REVIEW_RESULT_INVALID"
    assert failure.operation_id == 18


@pytest.mark.parametrize(
    "variant",
    (ScanReviewPreviewVariant.ORIGINAL, ScanReviewPreviewVariant.ADJUSTED),
)
def test_preview_result_progress_and_completion_keep_ui_correlation(
    variant: ScanReviewPreviewVariant,
) -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    cancellation = OperationCancellation()
    ready: list[tuple[int, int, object]] = []
    progress_events: list[UiProgress] = []
    cancelled: list[tuple[int, int, str]] = []
    failures: list[tuple[int, int, UiFailure]] = []
    finished: list[tuple[int, int]] = []
    worker.scan_review_preview_ready.connect(
        lambda operation_id, generation, result: ready.append((operation_id, generation, result))
    )
    worker.scan_review_progress_changed.connect(progress_events.append)
    worker.scan_review_preview_cancelled.connect(
        lambda operation_id, generation, message: cancelled.append(
            (operation_id, generation, message)
        )
    )
    worker.scan_review_preview_failed.connect(
        lambda operation_id, generation, failure: failures.append(
            (operation_id, generation, failure)
        )
    )
    worker.scan_review_preview_finished.connect(
        lambda operation_id, generation: finished.append((operation_id, generation))
    )

    commands.scan_review_preview_requested.emit(
        23,
        5,
        draft,
        variant,
        320,
        240,
        cancellation,
    )

    assert len(backend.preview_calls) == 1
    call = backend.preview_calls[0]
    assert call.draft is draft
    assert call.variant is variant
    assert (call.maximum_width, call.maximum_height) == (320, 240)
    assert call.timeout_seconds is None
    assert not call.cancellation()
    expected = backend.preview_results[variant]
    assert ready == [(23, 5, expected)]
    assert ready[0][2] is expected
    assert progress_events == [UiProgress("Scan review", "lookup", 0, 1, True, 23)]
    assert str(PROGRESS_ID) not in repr(progress_events)
    assert cancelled == []
    assert failures == []
    assert finished == [(23, 5)]


@pytest.mark.parametrize(
    "error",
    (
        ImagePageError(ImagePageErrorCode.CANCELLED, "image.review_cancel"),
        PagePreviewError(PagePreviewErrorCode.CANCELLED, "preview.review_cancel"),
    ),
)
def test_preview_cancellation_has_one_cancelled_terminal_and_one_finished_signal(
    error: BaseException,
) -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    backend.preview_error = error
    commands, worker = _bridge(backend)
    cancellation = OperationCancellation()
    ready: list[object] = []
    cancelled: list[tuple[int, int, str]] = []
    failures: list[object] = []
    finished: list[tuple[int, int]] = []
    timeline: list[str] = []
    worker.scan_review_preview_ready.connect(ready.append)

    def record_cancelled(operation_id: int, generation: int, message: str) -> None:
        timeline.append("cancelled")
        cancelled.append((operation_id, generation, message))

    def record_finished(operation_id: int, generation: int) -> None:
        timeline.append("finished")
        finished.append((operation_id, generation))

    worker.scan_review_preview_cancelled.connect(record_cancelled)
    worker.scan_review_preview_failed.connect(failures.append)
    worker.scan_review_preview_finished.connect(record_finished)

    commands.scan_review_preview_requested.emit(
        31,
        8,
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
        cancellation,
    )

    assert ready == []
    assert cancelled == [(31, 8, "Preview cancelled. Adjustments were not applied.")]
    assert failures == []
    assert finished == [(31, 8)]
    assert timeline == ["cancelled", "finished"]


def test_typed_preview_failure_is_sanitized_and_finishes_exactly_once() -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    backend.preview_error = ScanReviewError(
        ScanReviewErrorCode.STALE,
        "scan_review.private_context",
    )
    commands, worker = _bridge(backend)
    failures: list[tuple[int, int, UiFailure]] = []
    cancelled: list[object] = []
    finished: list[tuple[int, int]] = []
    worker.scan_review_preview_failed.connect(
        lambda operation_id, generation, failure: failures.append(
            (operation_id, generation, failure)
        )
    )
    worker.scan_review_preview_cancelled.connect(cancelled.append)
    worker.scan_review_preview_finished.connect(
        lambda operation_id, generation: finished.append((operation_id, generation))
    )

    commands.scan_review_preview_requested.emit(
        37,
        9,
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
        OperationCancellation(),
    )

    assert failures == [
        (
            37,
            9,
            UiFailure(
                "SCAN_REVIEW_STALE",
                "The page changed while scan review was open. Start the review again.",
                False,
                37,
            ),
        )
    ]
    assert "private_context" not in repr(failures)
    assert cancelled == []
    assert finished == [(37, 9)]


def test_apply_publishes_the_exact_state_before_correlated_success() -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    states: list[OrganizerViewState] = []
    statuses: list[str] = []
    applied: list[tuple[int, object]] = []
    failures: list[object] = []
    timeline: list[str] = []

    def record_state(state: OrganizerViewState) -> None:
        timeline.append("state")
        states.append(state)

    def record_status(message: str) -> None:
        timeline.append("status")
        statuses.append(message)

    def record_applied(operation_id: int, state: object) -> None:
        timeline.append("applied")
        applied.append((operation_id, state))

    worker.state_changed.connect(record_state)
    worker.status_changed.connect(record_status)
    worker.scan_review_applied.connect(record_applied)
    worker.scan_review_apply_failed.connect(failures.append)

    commands.scan_review_apply_requested.emit(41, draft)

    assert backend.apply_calls == [draft]
    assert backend.apply_calls[0] is draft
    assert states == [backend.state]
    assert states[0] is backend.state
    assert statuses == ["Scan adjustments applied. Saving changes."]
    assert applied == [(41, backend.state)]
    assert applied[0][1] is backend.state
    assert failures == []
    assert timeline == ["state", "status", "applied"]


def test_no_change_apply_reports_completion_without_claiming_a_save() -> None:
    neutral = ImageTransformIntent()
    draft = ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=7,
        base_intent=neutral,
        candidate_intent=neutral,
        suggestion_present=False,
    )
    backend = _ScanReviewBackend(draft)
    backend.state = OrganizerViewState(
        case_id=CASE_ID,
        revision=7,
        pages=(OrganizerPageView(PAGE_ID, 1, 0, True, neutral),),
        can_undo=False,
        can_redo=False,
        autosave_pending=False,
    )
    backend.apply_result = backend.state
    commands, worker = _bridge(backend)
    statuses: list[str] = []
    worker.status_changed.connect(statuses.append)

    commands.scan_review_apply_requested.emit(42, draft)

    assert statuses == ["Scan review complete. No changes were applied."]
    assert "saving" not in statuses[0].casefold()


def test_apply_rejects_an_internally_inconsistent_authoritative_before_state() -> None:
    neutral = ImageTransformIntent()
    draft = ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=7,
        base_intent=neutral,
        candidate_intent=neutral,
        suggestion_present=False,
    )
    inconsistent = OrganizerViewState(
        case_id=CASE_ID,
        revision=7,
        pages=(OrganizerPageView(PAGE_ID, 1, 90, True, neutral),),
        can_undo=False,
        can_redo=False,
        autosave_pending=False,
    )
    backend = _ScanReviewBackend(draft)
    backend.state = inconsistent
    backend.apply_result = inconsistent
    commands, worker = _bridge(backend)
    states: list[object] = []
    applied: list[object] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.state_changed.connect(states.append)
    worker.scan_review_applied.connect(lambda _operation_id, state: applied.append(state))
    worker.scan_review_apply_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.scan_review_apply_requested.emit(44, draft)

    assert states == []
    assert applied == []
    assert len(failures) == 1
    assert failures[0][0] == 44
    assert failures[0][1].code == "PWS_UI_SCAN_REVIEW_RESULT_INVALID"


@pytest.mark.parametrize(
    "result",
    (
        None,
        OrganizerViewState(
            case_id=CASE_ID,
            revision=7,
            pages=(OrganizerPageView(PAGE_ID, 1, 90, True, _intent()),),
            can_undo=False,
            can_redo=False,
            autosave_pending=False,
        ),
        OrganizerViewState(
            case_id=SOURCE_ID,
            revision=7,
            pages=(OrganizerPageView(PAGE_ID, 1, 90, True, _intent()),),
            can_undo=True,
            can_redo=False,
            autosave_pending=True,
        ),
        OrganizerViewState(
            case_id=CASE_ID,
            revision=8,
            pages=(OrganizerPageView(PAGE_ID, 1, 90, True, _intent()),),
            can_undo=True,
            can_redo=False,
            autosave_pending=True,
        ),
        OrganizerViewState(
            case_id=CASE_ID,
            revision=7,
            pages=(),
            can_undo=True,
            can_redo=False,
            autosave_pending=True,
        ),
        OrganizerViewState(
            case_id=CASE_ID,
            revision=7,
            pages=(OrganizerPageView(PAGE_ID, 1, 0, True, ImageTransformIntent()),),
            can_undo=True,
            can_redo=False,
            autosave_pending=True,
        ),
        OrganizerViewState(
            case_id=CASE_ID,
            revision=7,
            pages=(
                OrganizerPageView(PAGE_ID, 1, 90, True, _intent()),
                OrganizerPageView(
                    WRONG_PAGE_ID,
                    2,
                    0,
                    True,
                    ImageTransformIntent(),
                ),
            ),
            can_undo=True,
            can_redo=False,
            autosave_pending=True,
        ),
    ),
)
def test_malformed_or_inconsistent_apply_result_fails_before_any_success_signal(
    result: object,
) -> None:
    draft = _draft()
    backend = _InvalidApplyBackend(draft, result)
    commands, worker = _bridge(backend)
    states: list[object] = []
    statuses: list[str] = []
    applied: list[tuple[int, object]] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.state_changed.connect(states.append)
    worker.status_changed.connect(statuses.append)
    worker.scan_review_applied.connect(
        lambda operation_id, state: applied.append((operation_id, state))
    )
    worker.scan_review_apply_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.scan_review_apply_requested.emit(43, draft)

    assert backend.apply_calls == [draft]
    assert states == []
    assert statuses == []
    assert applied == []
    assert len(failures) == 1
    operation_id, failure = failures[0]
    assert operation_id == 43
    assert failure.code == "PWS_UI_SCAN_REVIEW_RESULT_INVALID"
    assert failure.operation_id == 43
    assert failure.retryable


def test_invalid_requests_are_rejected_without_calling_or_disclosing_values() -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    start_failures: list[tuple[int, UiFailure]] = []
    preview_failures: list[tuple[int, int, UiFailure]] = []
    preview_finished: list[tuple[int, int]] = []
    apply_failures: list[tuple[int, UiFailure]] = []
    discard_failures: list[UiFailure] = []
    worker.scan_review_start_failed.connect(
        lambda operation_id, failure: start_failures.append((operation_id, failure))
    )
    worker.scan_review_preview_failed.connect(
        lambda operation_id, generation, failure: preview_failures.append(
            (operation_id, generation, failure)
        )
    )
    worker.scan_review_preview_finished.connect(
        lambda operation_id, generation: preview_finished.append((operation_id, generation))
    )
    worker.scan_review_apply_failed.connect(
        lambda operation_id, failure: apply_failures.append((operation_id, failure))
    )
    worker.scan_review_discard_failed.connect(discard_failures.append)

    commands.scan_review_begin_requested.emit(43, PRIVATE_CANARY, _intent())
    commands.scan_review_preview_requested.emit(
        44,
        10,
        PRIVATE_CANARY,
        ScanReviewPreviewVariant.ORIGINAL,
        320,
        240,
        OperationCancellation(),
    )
    commands.scan_review_apply_requested.emit(45, PRIVATE_CANARY)
    commands.scan_review_cancel_requested.emit(PRIVATE_CANARY)

    assert backend.begin_calls == []
    assert backend.preview_calls == []
    assert backend.apply_calls == []
    assert backend.cancel_calls == []
    assert start_failures == [
        (
            43,
            UiFailure(
                "PWS_UI_REQUEST_INVALID",
                "PaperWorkShell rejected an invalid scan-review request.",
                True,
                43,
            ),
        )
    ]
    assert preview_failures == [
        (
            44,
            10,
            UiFailure(
                "PWS_UI_REQUEST_INVALID",
                "PaperWorkShell rejected an invalid scan-review preview request.",
                True,
                44,
            ),
        )
    ]
    assert preview_finished == [(44, 10)]
    assert apply_failures == [
        (
            45,
            UiFailure(
                "PWS_UI_REQUEST_INVALID",
                "PaperWorkShell rejected an invalid scan-review apply request.",
                True,
                45,
            ),
        )
    ]
    assert discard_failures == [
        UiFailure(
            "PWS_UI_REQUEST_INVALID",
            "PaperWorkShell could not discard the scan review safely.",
            True,
        )
    ]
    assert PRIVATE_CANARY not in repr(
        (start_failures, preview_failures, apply_failures, discard_failures)
    )


def test_cancel_discards_exact_draft_and_sanitizes_backend_failure_context() -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    commands, worker = _bridge(backend)
    failures: list[UiFailure] = []
    worker.scan_review_discard_failed.connect(failures.append)

    commands.scan_review_cancel_requested.emit(draft)

    assert backend.cancel_calls == [draft]
    assert backend.cancel_calls[0] is draft
    assert failures == []

    backend.cancel_error = ScanReviewError(
        ScanReviewErrorCode.STALE,
        "scan_review.private_discard_context",
    )
    commands.scan_review_cancel_requested.emit(draft)

    assert backend.cancel_calls == [draft, draft]
    assert failures == [
        UiFailure(
            "SCAN_REVIEW_STALE",
            "The page changed while scan review was open. Start the review again.",
            False,
        )
    ]
    assert "private_discard_context" not in repr(failures)


def test_unknown_backend_exception_payload_never_crosses_scan_review_signals() -> None:
    draft = _draft()
    backend = _ScanReviewBackend(draft)
    backend.begin_error = RuntimeError(PRIVATE_CANARY)
    commands, worker = _bridge(backend)
    failures: list[tuple[int, UiFailure]] = []
    worker.scan_review_start_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    commands.scan_review_begin_requested.emit(47, PAGE_ID, None)

    assert failures == [
        (
            47,
            UiFailure(
                "PWS_UI_UNEXPECTED",
                "PaperWorkShell could not complete that action. Try again or restart the application.",
                True,
                47,
            ),
        )
    ]
    assert PRIVATE_CANARY not in repr(failures)
