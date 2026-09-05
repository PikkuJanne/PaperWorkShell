"""Correlated, content-safe Qt boundary for the PWS-0408 package workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.package_workflow import (
    PackageReviewState,
    PackageWorkflowError,
    PackageWorkflowErrorCode,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    is_package_review_state_valid,
    sanitize_ui_failure,
)

CASE_ID = UUID("a4000000-0000-4000-8000-000000000001")
REVIEW_ID = UUID("a4000000-0000-4000-8000-000000000002")


def _review(mode: PortalOutputMode = PortalOutputMode.COMBINED_PDF) -> PackageReviewState:
    return PackageReviewState(
        REVIEW_ID,
        PackageSummary(PackageSummaryStatus.PLANNED),
        tuple(PortalOutputMode),
        mode,
        True,
        False,
        True,
    )


class _Backend:
    startup_recovery_result = StartupRecoveryResult()

    def __init__(self) -> None:
        self.state = OrganizerViewState(CASE_ID, 3, (), False, False, False)
        self.calls: list[tuple[object, ...]] = []
        self.review: object = _review()
        self.error: BaseException | None = None
        self.save_cancelled = False

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def begin_package_review(self, mode: PortalOutputMode | None = None) -> PackageReviewState:
        self.calls.append(("begin", mode))
        if self.error is not None:
            raise self.error
        return cast(PackageReviewState, self.review)

    def refresh_package_review(self, mode: PortalOutputMode) -> PackageReviewState:
        self.calls.append(("refresh", mode))
        return _review(mode)

    def export_package(
        self,
        destination: Path,
        *,
        review_id: UUID,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> PackageSummary:
        self.calls.append(
            ("export", review_id, destination, signed_source_confirmed, human_review_confirmed)
        )
        assert not cancellation()
        progress(1, 2)
        return PackageSummary(PackageSummaryStatus.NOT_COMMITTED)

    def cancel_package_review(self) -> None:
        self.calls.append(("cancel",))

    def save_package_summary(self, destination: Path, *, cancellation: Callable[[], bool]) -> None:
        self.calls.append(("save", destination))
        self.save_cancelled = cancellation()
        if self.save_cancelled:
            raise PackageWorkflowError(PackageWorkflowErrorCode.CANCELLED)


def _bridge() -> tuple[_Backend, OrganizerBackendCommands, OrganizerBackendWorker]:
    backend = _Backend()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    worker.initialize()
    return backend, commands, worker


def test_review_refresh_export_progress_save_and_release_keep_operation_correlation() -> None:
    backend, commands, worker = _bridge()
    reviews: list[tuple[int, PackageReviewState]] = []
    results: list[tuple[int, PackageSummary]] = []
    progress: list[tuple[int, int, int]] = []
    saved: list[int] = []
    cancelled: list[int] = []
    worker.package_review_ready.connect(
        lambda operation, review: reviews.append((operation, review))
    )
    worker.package_export_ready.connect(
        lambda operation, summary: results.append((operation, summary))
    )
    worker.package_progress.connect(
        lambda operation, completed, total: progress.append((operation, completed, total))
    )
    worker.package_summary_saved.connect(saved.append)
    worker.package_review_cancelled.connect(cancelled.append)
    commands.package_review_begin_requested.emit(10, None)
    commands.package_review_refresh_requested.emit(11, PortalOutputMode.ZIP)
    commands.package_export_requested.emit(
        12, REVIEW_ID, Path("destination"), False, True, OperationCancellation()
    )
    commands.package_summary_save_requested.emit(13, Path("summary.txt"), OperationCancellation())
    commands.package_review_cancel_requested.emit(14)
    assert [operation for operation, _ in reviews] == [10, 11]
    assert reviews[1][1].selected_mode is PortalOutputMode.ZIP
    assert [(operation, result.status) for operation, result in results] == [
        (12, PackageSummaryStatus.NOT_COMMITTED)
    ]
    assert progress == [(12, 1, 2)]
    assert saved == [13]
    assert cancelled == [14]
    assert backend.calls == [
        ("begin", None),
        ("refresh", PortalOutputMode.ZIP),
        ("export", REVIEW_ID, Path("destination"), False, True),
        ("save", Path("summary.txt")),
        ("cancel",),
    ]


def test_invalid_review_and_export_request_fail_without_emitting_private_values() -> None:
    backend, _commands, worker = _bridge()
    errors: list[tuple[int, str, UiFailure]] = []
    reviews: list[object] = []
    worker.package_failed.connect(
        lambda operation, phase, failure: errors.append((operation, phase, failure))
    )
    worker.package_review_ready.connect(lambda _operation, value: reviews.append(value))
    backend.review = "private-path-and-source.pdf"
    worker.begin_package_review(20, None)
    worker.export_package(
        21, "private-review-token", Path("out"), False, True, OperationCancellation()
    )
    assert reviews == []
    assert [item[:2] for item in errors] == [(20, "review"), (21, "export")]
    assert all("private" not in item[2].message for item in errors)
    assert all(call[0] != "export" for call in backend.calls)


def test_known_workflow_failure_is_rebuilt_from_code_before_display() -> None:
    error = PackageWorkflowError(PackageWorkflowErrorCode.DESTINATION_CHANGED)
    error.safe_message = "private filename and exception content"
    failure = sanitize_ui_failure(error, operation_id=50)
    assert failure.operation_id == 50
    assert failure.code == "PACKAGE_WORKFLOW_DESTINATION_CHANGED"
    assert "shorter destination path" in failure.message
    assert "private" not in failure.message


def test_review_validation_rejects_malformed_mode_or_permission_gates() -> None:
    valid = _review()
    assert is_package_review_state_valid(valid)
    object.__setattr__(valid, "export_allowed", "private unchecked gate")
    assert not is_package_review_state_valid(valid)


def test_summary_save_forwards_cancellation_and_never_announces_cancelled_write_as_saved() -> None:
    backend, commands, worker = _bridge()
    saved: list[int] = []
    failed: list[tuple[int, str, UiFailure]] = []
    worker.package_summary_saved.connect(saved.append)
    worker.package_failed.connect(
        lambda operation, phase, failure: failed.append((operation, phase, failure))
    )
    cancellation = OperationCancellation()
    cancellation.cancel()
    commands.package_summary_save_requested.emit(71, Path("summary.txt"), cancellation)
    assert backend.save_cancelled
    assert saved == []
    assert len(failed) == 1
    assert failed[0][:2] == (71, "save")
    assert failed[0][2].code == PackageWorkflowErrorCode.CANCELLED.value

    worker.save_package_summary(72, Path("summary.txt"), "invalid cancellation data")
    assert len(backend.calls) == 1
    assert saved == []
