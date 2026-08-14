"""Focused Qt-bridge contracts for revision-bound page-quality review."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.quality_analysis import (
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProgress,
    QualityAnalysisProgressPhase,
    QualityReviewSnapshot,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)

CASE_ID = UUID("8d300000-0000-4000-8000-000000000001")
PAGE_ID = UUID("8d300000-0000-4000-8000-000000000002")
WRONG_PAGE_ID = UUID("8d300000-0000-4000-8000-000000000003")
PRIVATE_CANARY = r"QUALITY_PRIVATE_C:\records\applicant-ocr-secret.txt"


def _state(*, autosave_pending: bool = False) -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        7,
        (OrganizerPageView(PAGE_ID, 1, 0, True),),
        False,
        False,
        autosave_pending,
    )


def _snapshot(*, page_id: UUID = PAGE_ID) -> QualityReviewSnapshot:
    return QualityReviewSnapshot(
        CASE_ID,
        7,
        (page_id,),
        (),
        "scan-quality-v1",
        "a" * 64,
    )


class _QualityBackend:
    def __init__(self) -> None:
        self.state = _state()
        self.result: object = _snapshot()
        self.error: BaseException | None = None
        self.mutate_after = False
        self.calls: list[tuple[UUID, int, Callable[[], bool]]] = []

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def analyze_quality(
        self,
        *,
        expected_case_id: UUID,
        expected_revision: int,
        cancellation: Callable[[], bool],
        progress: Callable[[QualityAnalysisProgress], None],
    ) -> QualityReviewSnapshot:
        self.calls.append((expected_case_id, expected_revision, cancellation))
        progress(
            QualityAnalysisProgress(
                QualityAnalysisProgressPhase.PREVIEWING,
                0,
                1,
            )
        )
        progress(
            QualityAnalysisProgress(
                QualityAnalysisProgressPhase.OCR_REVIEWING,
                0,
                1,
            )
        )
        if self.error is not None:
            raise self.error
        result = self.result
        if self.mutate_after:
            self.state = replace(self.state, revision=8)
        return cast(QualityReviewSnapshot, result)


def _bridge(
    backend: _QualityBackend,
) -> tuple[OrganizerBackendCommands, OrganizerBackendWorker]:
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    worker.initialize()
    return commands, worker


def test_bridge_emits_only_correlated_progress_result_status_and_finish() -> None:
    backend = _QualityBackend()
    commands, worker = _bridge(backend)
    cancellation = OperationCancellation()
    ready: list[tuple[int, int, object]] = []
    progress: list[UiProgress] = []
    statuses: list[tuple[int, str]] = []
    failures: list[UiFailure] = []
    finished: list[int] = []
    worker.quality_review_ready.connect(
        lambda operation_id, generation, value: ready.append((operation_id, generation, value))
    )
    worker.progress_changed.connect(progress.append)
    worker.operation_status_changed.connect(
        lambda operation_id, message: statuses.append((operation_id, message))
    )
    worker.failed.connect(failures.append)
    worker.operation_finished.connect(finished.append)

    commands.quality_review_requested.emit(21, 4, CASE_ID, 7, cancellation)

    assert backend.calls == [(CASE_ID, 7, backend.calls[0][2])]
    assert not backend.calls[0][2]()
    assert ready == [(21, 4, backend.result)]
    assert progress == [
        UiProgress("Page quality check", "previewing", 0, 1, True, 21),
        UiProgress("Page quality check", "ocr reviewing", 0, 1, True, 21),
    ]
    assert statuses == [
        (
            21,
            "Page quality check finished. No review signals were found by these limited checks. "
            "Review the pages yourself before export.",
        )
    ]
    assert failures == []
    assert finished == [21]


@pytest.mark.parametrize("hostile", ("wrong-pages", "state-changed", "wrong-type"))
def test_hostile_backend_result_emits_only_correlated_failure_and_finish(hostile: str) -> None:
    backend = _QualityBackend()
    if hostile == "wrong-pages":
        backend.result = _snapshot(page_id=WRONG_PAGE_ID)
    elif hostile == "state-changed":
        backend.mutate_after = True
    else:
        backend.result = PRIVATE_CANARY
    commands, worker = _bridge(backend)
    ready: list[object] = []
    statuses: list[object] = []
    failures: list[UiFailure] = []
    finished: list[int] = []
    worker.quality_review_ready.connect(lambda *values: ready.append(values))
    worker.operation_status_changed.connect(lambda *values: statuses.append(values))
    worker.failed.connect(failures.append)
    worker.operation_finished.connect(finished.append)

    commands.quality_review_requested.emit(
        22,
        5,
        CASE_ID,
        7,
        OperationCancellation(),
    )

    assert ready == []
    assert statuses == []
    assert len(failures) == 1
    assert failures[0].code == QualityAnalysisErrorCode.RESULT_INVALID.value
    assert failures[0].operation_id == 22
    assert PRIVATE_CANARY not in repr(failures)
    assert finished == [22]


def test_cancellation_and_unknown_payloads_are_sanitized_without_success() -> None:
    backend = _QualityBackend()
    commands, worker = _bridge(backend)
    cancelled: list[tuple[int, str]] = []
    failures: list[UiFailure] = []
    ready: list[object] = []
    worker.operation_cancelled.connect(
        lambda operation_id, message: cancelled.append((operation_id, message))
    )
    worker.failed.connect(failures.append)
    worker.quality_review_ready.connect(lambda *values: ready.append(values))

    backend.error = QualityAnalysisError(
        QualityAnalysisErrorCode.CANCELLED,
        "quality.test_cancel",
    )
    commands.quality_review_requested.emit(23, 6, CASE_ID, 7, OperationCancellation())
    assert cancelled == [(23, "Page quality check cancelled. No review results were applied.")]
    assert failures == []
    assert ready == []

    backend.error = RuntimeError(PRIVATE_CANARY)
    commands.quality_review_requested.emit(24, 7, CASE_ID, 7, OperationCancellation())
    assert len(failures) == 1
    assert failures[0].code == "PWS_UI_UNEXPECTED"
    assert failures[0].operation_id == 24
    assert PRIVATE_CANARY not in repr(failures)
