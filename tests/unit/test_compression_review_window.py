"""Correlated private review, pixel delivery, and publication lifecycle without native workers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QFileDialog, QLabel, QPushButton

from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailSnapshot
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker, UiFailure
from paperworkshell.ui.compression_review import CompressionReviewDialog
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.helpers.compression_review import compression_review
from tests.unit.test_compression_review_dialog import press, widget
from tests.unit.test_package_export_dialog import _summary
from tests.unit.test_package_export_window import (
    CASE_ID,
    PAGE_ID,
    _open,
    _review,
    _window,
)


@contextmanager
def compression_window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    with _window() as (window, commands, worker):
        commands.compression_prepare_requested.disconnect(worker.prepare_compression)
        commands.compression_preview_requested.disconnect(worker.preview_compression_page)
        commands.compression_export_requested.disconnect(worker.commit_compression)
        commands.compression_discard_requested.disconnect(worker.discard_compression)
        yield window, commands, worker
        window._dispose_compression_dialog()


def review_state() -> CompressionReviewState:
    review = compression_review()
    return replace(
        review,
        case_id=CASE_ID,
        affected_pages=(replace(review.affected_pages[0], page_id=PAGE_ID),),
    )


def open_compression(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
    monkeypatch: pytest.MonkeyPatch,
    destination: Path,
) -> tuple[CompressionReviewDialog, int]:
    parent = _open(window, commands, worker)
    reviewed = replace(_review(), compression_allowed=True)
    window._package_review_state = reviewed
    parent.show_review(
        reviewed.summary, export_allowed=True, compression_allowed=True, human_review_required=True
    )
    widget(parent, QCheckBox, "packageExportHumanReviewCheck").setChecked(True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(destination))
    press(widget(parent, QPushButton, "packageExportCompressionButton"))
    child = window._compression_dialog
    operation_id = window._package_export_operation_id
    assert child is not None and operation_id is not None
    assert child.busy and parent.busy
    return child, operation_id


def preview_result(review: CompressionReviewState, *, side: int = 80) -> CompressionPagePreview:
    page = review.affected_pages[0]
    snapshot = PdfThumbnailSnapshot(
        review.case_id,
        page.page_id,
        side,
        side,
        side * 4,
        b"\xff\xff\xff\xff" * side * side,
        PdfRenderRotation.DEGREES_0,
        PdfRenderRotation.DEGREES_0,
    )
    return CompressionPagePreview(
        review.review_id, page.output_key, page.page_index, snapshot, snapshot
    )


def load_window_pair(window: PaperWorkShellWindow, worker: OrganizerBackendWorker) -> None:
    child = window._compression_dialog
    review = window._compression_review_state
    assert child is not None and review is not None
    press(widget(child, QPushButton, "compressionReviewShowPageButton"))
    operation_id = window._package_export_operation_id
    assert operation_id is not None
    worker.compression_preview_ready.emit(operation_id, preview_result(review))
    assert child.preview_bytes > 0


def test_preparation_carries_consent_then_exact_pair_and_commit_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        prepares: list[tuple[object, ...]] = []
        commits: list[tuple[object, ...]] = []
        commands.compression_prepare_requested.connect(lambda *args: prepares.append(args))
        commands.compression_export_requested.connect(lambda *args: commits.append(args))
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        assert prepares[0][1:6] == (
            _review().review_id,
            tmp_path,
            TargetSizeColorChoice.PRESERVE,
            False,
            True,
        )
        review = review_state()
        worker.compression_ready.emit(operation_id, review)
        assert child.review is review
        assert window._package_export_dialog is not None and window._package_export_dialog.busy
        assert not window._package_export_button.isEnabled()
        window._export_compression(review.review_id)
        assert commits == []
        load_window_pair(window, worker)
        window._export_compression(review.review_id)
        assert commits == []
        widget(child, QCheckBox, "compressionReviewAcknowledgementCheck").setChecked(True)
        press(widget(child, QPushButton, "compressionReviewExportButton"))
        assert commits[0][1:3] == (review.review_id, True)
        summary = _summary(PackageSummaryStatus.COMMITTED)
        worker.package_export_ready.emit(cast_operation(commits[0][0]), summary)
        assert window._compression_dialog is None
        assert window._package_export_dialog is not None
        assert window._package_export_dialog.summary is summary
        assert not window._package_export_dialog.busy


def cast_operation(value: object) -> int:
    assert type(value) is int
    return value


def test_destination_cancel_restores_capability_and_clears_previous_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with compression_window() as (window, commands, worker):
        parent = _open(window, commands, worker)
        reviewed = replace(_review(), compression_allowed=True)
        window._package_review_state = reviewed
        parent.show_review(
            reviewed.summary,
            export_allowed=True,
            compression_allowed=True,
            human_review_required=True,
        )
        ack = widget(parent, QCheckBox, "packageExportHumanReviewCheck")
        ack.setChecked(True)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")
        press(widget(parent, QPushButton, "packageExportCompressionButton"))
        assert window._compression_dialog is None
        assert not parent.busy and not ack.isChecked()
        ack.setChecked(True)
        assert widget(parent, QPushButton, "packageExportCompressionButton").isEnabled()


def test_late_cancelled_preparation_is_discarded_and_refreshed_before_parent_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        discards: list[int] = []
        commands.compression_discard_requested.connect(discards.append)
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        child.reject()
        assert window._package_cancellation is not None and window._package_cancellation.cancelled()
        worker.compression_ready.emit(operation_id + 1, review_state())
        waiting_dialog = window._compression_dialog
        assert waiting_dialog is child
        worker.compression_ready.emit(operation_id, review_state())
        assert window._compression_dialog is None
        assert len(discards) == 1
        assert window._package_review_operation_id == discards[0]
        fresh = replace(_review(), compression_allowed=True)
        worker.package_review_ready.emit(discards[0], fresh)
        assert window._package_review_state is fresh
        assert window._package_export_dialog is not None and not window._package_export_dialog.busy
        assert not widget(
            window._package_export_dialog, QCheckBox, "packageExportHumanReviewCheck"
        ).isChecked()


@pytest.mark.parametrize("mutation", ("case", "revision", "color"))
def test_prepared_state_must_match_current_case_revision_and_requested_color(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    with compression_window() as (window, commands, worker):
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        review = review_state()
        if mutation == "case":
            review = replace(review, case_id=UUID("55555555-5555-4555-8555-555555555555"))
        elif mutation == "revision":
            review = replace(review, manifest_revision=4)
        else:
            gray = compression_review(grayscale=True)
            review = replace(
                gray,
                case_id=CASE_ID,
                affected_pages=(replace(gray.affected_pages[0], page_id=PAGE_ID),),
            )
        worker.compression_ready.emit(operation_id, review)
        assert child.review is None
        assert window._compression_review_state is None
        assert not widget(child, QPushButton, "compressionReviewExportButton").isEnabled()
        assert "could not be prepared" in widget(child, QLabel, "compressionReviewStatus").text()


def test_grayscale_reprepare_drops_pair_and_serializes_new_choice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        prepares: list[tuple[object, ...]] = []
        commands.compression_prepare_requested.connect(lambda *args: prepares.append(args))
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        review = review_state()
        worker.compression_ready.emit(operation_id, review)
        load_window_pair(window, worker)
        widget(child, QCheckBox, "compressionReviewGrayscaleCheck").setChecked(True)
        assert child.preview_bytes == 0
        window._export_compression(review.review_id)
        assert window._package_export_operation_id is None
        press(widget(child, QPushButton, "compressionReviewPrepareButton"))
        assert len(prepares) == 2 and prepares[-1][3] is TargetSizeColorChoice.ALLOW_GRAYSCALE
        assert window._compression_pair_review_id is None
        worker.compression_ready.emit(operation_id, review)
        assert child.review is None


def test_cancelled_or_wrong_page_preview_cannot_authorize_export_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        review = review_state()
        worker.compression_ready.emit(operation_id, review)
        for cancel in (False, True):
            press(widget(child, QPushButton, "compressionReviewShowPageButton"))
            preview_id = window._package_export_operation_id
            assert preview_id is not None
            result = preview_result(review)
            if cancel:
                child.reject()
            else:
                result = replace(result, page_index=1)
            worker.compression_preview_ready.emit(preview_id, result)
            assert child.preview_bytes == 0
            assert window._compression_pair_review_id is None
            assert not widget(child, QPushButton, "compressionReviewExportButton").isEnabled()
            assert widget(child, QPushButton, "compressionReviewShowPageButton").isEnabled()
        load_window_pair(window, worker)
        assert window._compression_pair_review_id == review.review_id


def test_case_drift_invalidates_idle_review_and_late_preview_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        worker.compression_ready.emit(operation_id, review_state())
        press(widget(child, QPushButton, "compressionReviewShowPageButton"))
        preview_id = window._package_export_operation_id
        assert preview_id is not None and window._state is not None
        worker.state_changed.emit(replace(window._state, revision=4))
        worker.compression_preview_ready.emit(preview_id, preview_result(review_state()))
        assert child.review is None
        assert window._compression_review_state is None
        assert child.preview_bytes == 0


def test_preview_cache_is_evicted_and_pair_is_released_on_idle_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.white)
        window._model.set_preview(PAGE_ID, pixmap)
        assert window._model.retained_preview_bytes > 0
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        assert window._model.retained_preview_bytes == 0
        window._model.set_preview(PAGE_ID, pixmap)
        window._rebalance_preview_cache()
        assert window._model.retained_preview_bytes == 0
        worker.compression_ready.emit(operation_id, review_state())
        load_window_pair(window, worker)
        discards: list[int] = []
        commands.compression_discard_requested.connect(discards.append)
        child.reject()
        assert child.preview_bytes == 0
        assert window._compression_dialog is None
        assert len(discards) == 1


def test_shutdown_during_commit_cancels_but_retains_partial_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        shutdowns: list[bool] = []
        commands.shutdown_requested.connect(lambda: shutdowns.append(True))
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        worker.compression_ready.emit(operation_id, review_state())
        load_window_pair(window, worker)
        widget(child, QCheckBox, "compressionReviewAcknowledgementCheck").setChecked(True)
        press(widget(child, QPushButton, "compressionReviewExportButton"))
        commit_id = window._package_export_operation_id
        assert commit_id is not None
        window.request_shutdown()
        assert shutdowns == []
        assert window._package_cancellation is not None and window._package_cancellation.cancelled()
        summary = PackageSummary(PackageSummaryStatus.PARTIAL)
        worker.package_export_ready.emit(commit_id, summary)
        assert window._compression_dialog is None
        assert window._package_export_dialog is not None
        assert window._package_export_dialog.summary is summary
        assert window._package_export_dialog.isVisible()
        assert shutdowns == []


def test_idle_shutdown_disposes_child_and_uses_full_package_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        shutdowns: list[bool] = []
        releases: list[int] = []
        discards: list[int] = []
        commands.shutdown_requested.connect(lambda: shutdowns.append(True))
        commands.package_review_cancel_requested.connect(releases.append)
        commands.compression_discard_requested.connect(discards.append)
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        worker.compression_ready.emit(operation_id, review_state())
        load_window_pair(window, worker)
        window.request_shutdown()
        assert child.preview_bytes == 0
        assert window._compression_dialog is None and window._package_export_dialog is None
        assert len(releases) == 1 and discards == []
        assert shutdowns == [True]


def test_failure_and_progress_are_correlated_and_do_not_expose_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with compression_window() as (window, commands, worker):
        child, operation_id = open_compression(window, commands, worker, monkeypatch, tmp_path)
        worker.package_progress.emit(operation_id, 1, 3)
        assert "1 of 3" in widget(child, QLabel, "compressionReviewStatus").text()
        private = "C:/private/person.pdf secret"
        failure = UiFailure("UNEXPECTED_PRIVATE", private, True, operation_id)
        worker.package_failed.emit(operation_id + 1, "compression_prepare", failure)
        pending = child.busy
        assert pending
        worker.package_failed.emit(operation_id, "compression_prepare", failure)
        finished = child.busy
        assert not finished
        assert private not in widget(child, QLabel, "compressionReviewStatus").text()
        assert widget(child, QPushButton, "compressionReviewPrepareButton").isEnabled()
