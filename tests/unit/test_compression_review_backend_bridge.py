"""Strict, correlated Qt requests for private compression preparation and review."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.package_workflow import (
    PackageReviewState,
    PackageWorkflowError,
    PackageWorkflowErrorCode,
)
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from tests.unit.test_compression_review_window import preview_result, review_state
from tests.unit.test_package_export_backend_bridge import _Backend, _review


class CompressionBackend(_Backend):
    def __init__(self) -> None:
        super().__init__()
        self.prepared: object = review_state()
        self.preview: object = preview_result(review_state())
        self.summary: object = PackageSummary(PackageSummaryStatus.NOT_COMMITTED)
        self.state = replace(self.state, case_id=review_state().case_id)
        self.cancelled = False

    def _check(self, cancellation: Callable[[], bool]) -> None:
        self.cancelled = cancellation()
        if self.cancelled:
            raise PackageWorkflowError(PackageWorkflowErrorCode.CANCELLED)
        if self.error is not None:
            raise self.error

    def prepare_compression(
        self,
        destination: Path,
        *,
        review_id: UUID,
        color_choice: TargetSizeColorChoice,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> CompressionReviewState:
        self.calls.append(
            (
                "prepare",
                review_id,
                destination,
                color_choice,
                signed_source_confirmed,
                human_review_confirmed,
            )
        )
        self._check(cancellation)
        progress(1, 2)
        return cast(CompressionReviewState, self.prepared)

    def preview_compression_page(
        self,
        review_id: UUID,
        output_key: str,
        page_index: int,
        *,
        cancellation: Callable[[], bool],
    ) -> CompressionPagePreview:
        self.calls.append(("preview", review_id, output_key, page_index))
        self._check(cancellation)
        return cast(CompressionPagePreview, self.preview)

    def commit_compression(
        self,
        review_id: UUID,
        *,
        human_review_confirmed: bool,
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> PackageSummary:
        self.calls.append(("commit", review_id, human_review_confirmed))
        self._check(cancellation)
        progress(1, 1)
        return cast(PackageSummary, self.summary)

    def discard_compression(self) -> PackageReviewState:
        self.calls.append(("discard",))
        return _review()


def bridge() -> tuple[CompressionBackend, OrganizerBackendCommands, OrganizerBackendWorker]:
    backend = CompressionBackend()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    worker.initialize()
    return backend, commands, worker


def test_all_review_requests_preserve_operation_ids_and_actual_result_types(tmp_path: Path) -> None:
    backend, commands, worker = bridge()
    prepared: list[tuple[int, object]] = []
    previews: list[tuple[int, object]] = []
    committed: list[tuple[int, object]] = []
    refreshed: list[tuple[int, object]] = []
    progress: list[tuple[int, int, int]] = []
    worker.compression_ready.connect(lambda *args: prepared.append(args))
    worker.compression_preview_ready.connect(lambda *args: previews.append(args))
    worker.package_export_ready.connect(lambda *args: committed.append(args))
    worker.package_review_ready.connect(lambda *args: refreshed.append(args))
    worker.package_progress.connect(lambda *args: progress.append(args))
    review_id = review_state().review_id
    commands.compression_prepare_requested.emit(
        101,
        _review().review_id,
        tmp_path,
        TargetSizeColorChoice.PRESERVE,
        True,
        True,
        OperationCancellation(),
    )
    commands.compression_preview_requested.emit(
        102, review_id, "output-one", 0, OperationCancellation()
    )
    commands.compression_export_requested.emit(103, review_id, True, OperationCancellation())
    commands.compression_discard_requested.emit(104)
    assert prepared == [(101, backend.prepared)]
    assert previews == [(102, backend.preview)]
    assert committed == [(103, backend.summary)]
    assert refreshed == [(104, _review())]
    assert progress == [(101, 1, 2), (103, 1, 1)]
    assert backend.calls == [
        ("prepare", _review().review_id, tmp_path, TargetSizeColorChoice.PRESERVE, True, True),
        ("preview", review_id, "output-one", 0),
        ("commit", review_id, True),
        ("discard",),
    ]


@pytest.mark.parametrize(
    ("key", "index"),
    (("", 0), ("x" * 81, 0), ("output-one", -1), ("output-one", 1000), ("output-one", True)),
)
def test_invalid_page_request_is_rejected_before_backend_delegation(key: str, index: int) -> None:
    backend, _commands, worker = bridge()
    failed: list[tuple[int, str, UiFailure]] = []
    worker.package_failed.connect(lambda *args: failed.append(args))
    worker.preview_compression_page(
        201, review_state().review_id, key, index, OperationCancellation()
    )
    assert backend.calls == []
    assert len(failed) == 1 and failed[0][:2] == (201, "compression_preview")


def test_all_candidate_actions_require_version_four_uuid_before_backend_delegation(
    tmp_path: Path,
) -> None:
    backend, _commands, worker = bridge()
    failed: list[tuple[int, str, UiFailure]] = []
    worker.package_failed.connect(lambda *args: failed.append(args))
    invalid = UUID(int=1)
    worker.prepare_compression(
        201, invalid, tmp_path, TargetSizeColorChoice.PRESERVE, True, True, OperationCancellation()
    )
    worker.preview_compression_page(202, invalid, "output-one", 0, OperationCancellation())
    worker.commit_compression(203, invalid, True, OperationCancellation())
    assert backend.calls == []
    assert [item[:2] for item in failed] == [
        (201, "compression_prepare"),
        (202, "compression_preview"),
        (203, "compression_export"),
    ]


@pytest.mark.parametrize("phase", ("prepare", "preview", "commit"))
def test_cancellation_is_forwarded_and_has_one_safe_correlated_terminal_failure(
    phase: str, tmp_path: Path
) -> None:
    backend, _commands, worker = bridge()
    failed: list[tuple[int, str, UiFailure]] = []
    ready: list[object] = []
    worker.package_failed.connect(lambda *args: failed.append(args))
    worker.compression_ready.connect(lambda *args: ready.append(args))
    worker.compression_preview_ready.connect(lambda *args: ready.append(args))
    worker.package_export_ready.connect(lambda *args: ready.append(args))
    cancellation = OperationCancellation()
    cancellation.cancel()
    review_id = review_state().review_id
    if phase == "prepare":
        worker.prepare_compression(
            301, review_id, tmp_path, TargetSizeColorChoice.PRESERVE, True, True, cancellation
        )
    elif phase == "preview":
        worker.preview_compression_page(301, review_id, "output-one", 0, cancellation)
    else:
        worker.commit_compression(301, review_id, True, cancellation)
    assert backend.cancelled
    assert ready == []
    assert len(failed) == 1 and failed[0][0] == 301
    assert failed[0][2].code == PackageWorkflowErrorCode.CANCELLED.value


@pytest.mark.parametrize("phase", ("prepare", "preview", "commit"))
def test_malformed_results_are_not_emitted_and_private_error_text_is_not_exposed(
    phase: str, tmp_path: Path
) -> None:
    backend, _commands, worker = bridge()
    failed: list[tuple[int, str, UiFailure]] = []
    ready: list[object] = []
    worker.package_failed.connect(lambda *args: failed.append(args))
    worker.compression_ready.connect(lambda *args: ready.append(args))
    worker.compression_preview_ready.connect(lambda *args: ready.append(args))
    worker.package_export_ready.connect(lambda *args: ready.append(args))
    private = "C:/private/person.pdf secret text"
    review_id = review_state().review_id
    if phase == "prepare":
        backend.prepared = private
        worker.prepare_compression(
            401,
            review_id,
            tmp_path,
            TargetSizeColorChoice.PRESERVE,
            True,
            True,
            OperationCancellation(),
        )
    elif phase == "preview":
        backend.preview = private
        worker.preview_compression_page(401, review_id, "output-one", 0, OperationCancellation())
    else:
        backend.summary = private
        worker.commit_compression(401, review_id, True, OperationCancellation())
    assert ready == []
    assert len(failed) == 1
    assert private not in failed[0][2].message
    backend.error = RuntimeError(private)
    worker.prepare_compression(
        402,
        review_id,
        tmp_path,
        TargetSizeColorChoice.PRESERVE,
        True,
        True,
        OperationCancellation(),
    )
    assert len(failed) == 2 and private not in failed[1][2].message
