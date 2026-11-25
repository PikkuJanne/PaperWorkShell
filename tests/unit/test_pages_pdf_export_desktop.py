"""Ordinary PDF export coordination preserves committed outcomes and recoverable UI."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest
from PySide6.QtWidgets import QFileDialog, QPushButton

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPagesPdfExportOutcome
from paperworkshell.app.pdf_export import PdfExportProgressSink
from paperworkshell.app.trace_results import TraceSavedExportSelection
from paperworkshell.domain.pdf_export import (
    IMAGE_PDF_EXPORT_PROFILE,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportVerificationReport,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryDocument,
    PdfExportHistorySnapshot,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
    canonical_pdf_export_history_bytes,
)
from paperworkshell.domain.trace_recipe import TraceOutputStatus
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)
from tests.unit.test_organizer_backend import (
    CASE_ID,
    PAGE_IDS,
    _document,
    _Lifecycle,
    _Repository,
    _snapshot,
    _SourceIngest,
    _Thumbnails,
)
from tests.unit.test_package_export_window import _application, _press, _window


class _Export:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.calls = 0
        self.error: PdfExportErrorCode | None = None
        self.fail_refresh = False

    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: PdfExportProgressSink,
        make_searchable: bool = False,
    ) -> PdfExportOutcome:
        assert not make_searchable
        self.calls += 1
        assert case_id == CASE_ID
        assert isinstance(destination, Path)
        assert not deadline()
        if cancellation():
            raise PdfExportError(PdfExportErrorCode.CANCELLED, "export.test")
        if self.error is not None:
            raise PdfExportError(self.error, "export.test")
        plan = build_pdf_export_plan(self.repository.current, profile=IMAGE_PDF_EXPORT_PROFILE)
        progress(PdfExportProgress(PdfExportProgressPhase.PREPARING, 0, plan.page_count))
        report = PdfExportVerificationReport.for_verified_plan(
            plan, output_sha256="f" * 64, output_byte_size=100
        )
        export_id = UUID("f0000000-0000-4000-8000-000000000001")
        record = build_pdf_export_history_record_for_commit(
            plan, export_id=export_id, report=report, recorded_at=datetime(2026, 9, 30, tzinfo=UTC)
        )
        result = PdfExportOutcome(
            export_id,
            PdfExportOutcomeStatus.COMMITTED_RECORDED,
            report,
            True,
            build_pdf_export_history_retry_receipt(plan, record, report),
        )
        document = self.repository.current.document
        document["revision"] = self.repository.current.revision + 1
        self.repository.current = _snapshot(document)
        if self.fail_refresh:
            self.repository.load_failures.append(RuntimeError("PRIVATE-REFRESH-CANARY"))
        return result


def _backend() -> tuple[OrganizerBackend, _Export]:
    repository = _Repository(_document(include_image=True))
    exporter = _Export(repository)
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        pages_pdf_export=exporter,
    )
    backend.open_case(CASE_ID)
    return backend, exporter


def test_pages_pdf_export_flushes_rotation_and_reloads_history_before_more_edits() -> None:
    backend, exporter = _backend()
    backend.rotate_clockwise(PAGE_IDS[0])
    outcome = backend.export_pages_pdf(
        Path("synthetic-result.pdf"), cancellation=lambda: False, progress=lambda value: None
    )
    assert exporter.calls == 1
    assert not outcome.refresh_required
    assert not outcome.state.autosave_pending
    assert outcome.state.revision == exporter.repository.current.revision
    backend.rotate_clockwise(PAGE_IDS[0])
    backend.flush_autosave()
    assert exporter.repository.save_calls == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file identity verification")
def test_reopened_trace_receipt_checks_the_selected_pdf_bytes(tmp_path: Path) -> None:
    source = Path("tests/fixtures/synthetic/v1/pdf/born-digital.pdf")
    original = source.read_bytes()
    sha256 = hashlib.sha256(original).hexdigest()
    plan = build_pdf_export_plan(_snapshot(_document(page_count=1)))
    report = PdfExportVerificationReport.for_verified_plan(
        plan, output_sha256=sha256, output_byte_size=len(original)
    )
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=UUID("f0000000-0000-4000-8000-000000000002"),
        report=report,
        recorded_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    history_document = PdfExportHistoryDocument((record,))
    history_payload = canonical_pdf_export_history_bytes(history_document)

    class RetainedHistory:
        def load(self, case_id: UUID) -> PdfExportHistorySnapshot:
            assert case_id == CASE_ID
            digest = hashlib.sha256(history_payload).hexdigest()
            return PdfExportHistorySnapshot(history_document, digest, "d" * 64)

        def append(self, case_id: UUID, record: object) -> Never:
            pytest.fail("Reopening and inspecting must not modify history")

    document = _document(page_count=2)
    document["revision"] = 2
    document["export_history"] = {
        "storage_name": "export-history.json",
        "entries": [record.manifest_reference],
    }
    repository = _Repository(document)
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        trace_history=RetainedHistory(),
    )
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: backend)
    commands.connect_worker(worker)
    worker.initialize()
    backend.open_case(CASE_ID)
    backend.close_case()
    reopened = backend.open_case(CASE_ID)
    receipt = reopened.trace_saved_export
    assert receipt is not None
    assert len(reopened.pages) == 2
    assert receipt.page_count == 1
    assert backend.rotate_clockwise(PAGE_IDS[0]).trace_saved_export == receipt

    observed: list[tuple[int, object, TraceOutputStatus]] = []
    worker.trace_export_inspected.connect(lambda *result: observed.append(result))
    selected_path = tmp_path / "Selected.PDF"
    selection = TraceSavedExportSelection(receipt, selected_path)
    commands.trace_export_inspect_requested.emit(30, selection, OperationCancellation())
    assert observed[-1] == (30, selection, TraceOutputStatus.MISSING)

    selected_path.write_bytes(b"!" + original[1:])
    commands.trace_export_inspect_requested.emit(31, selection, OperationCancellation())
    assert observed[-1] == (31, selection, TraceOutputStatus.CHANGED)

    selected_path.write_bytes(original)
    commands.trace_export_inspect_requested.emit(32, selection, OperationCancellation())
    assert observed[-1] == (32, selection, TraceOutputStatus.PRESENT)

    unsafe_name = selected_path.with_suffix(".exe")
    unsafe_name.write_bytes(original)
    unsupported = TraceSavedExportSelection(receipt, unsafe_name)
    commands.trace_export_inspect_requested.emit(33, unsupported, OperationCancellation())
    assert observed[-1] == (33, unsupported, TraceOutputStatus.CHANGED)

    backend.close_case()
    commands.trace_export_inspect_requested.emit(34, selection, OperationCancellation())
    assert observed[-1] == (34, selection, TraceOutputStatus.UNAVAILABLE)


def test_saved_pdf_refresh_failure_requires_reopen_without_retrying_the_export() -> None:
    backend, exporter = _backend()
    exporter.fail_refresh = True
    outcome = backend.export_pages_pdf(
        Path("synthetic-result.pdf"), cancellation=lambda: False, progress=lambda value: None
    )
    assert outcome.refresh_required
    assert "was saved" in outcome.safe_message
    assert "reopen" in outcome.safe_message
    assert "PRIVATE-REFRESH-CANARY" not in outcome.safe_message
    with pytest.raises(PdfExportError) as caught:
        backend.export_pages_pdf(
            Path("second-result.pdf"), cancellation=lambda: False, progress=lambda value: None
        )
    assert caught.value.code is PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED
    with pytest.raises(PdfExportError):
        backend.rotate_clockwise(PAGE_IDS[0])
    assert exporter.calls == 1
    backend.close_case()
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])


@pytest.mark.parametrize(
    "error", (PdfExportErrorCode.CANCELLED, PdfExportErrorCode.DESTINATION_EXISTS)
)
def test_export_bridge_correlates_failure_or_cancellation_and_finishes(
    error: PdfExportErrorCode,
) -> None:
    backend, exporter = _backend()
    exporter.error = error
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: backend)
    commands.connect_worker(worker)
    worker.initialize()
    cancelled: list[tuple[int, str]] = []
    failures: list[UiFailure] = []
    finished: list[int] = []
    worker.operation_cancelled.connect(
        lambda operation, message: cancelled.append((operation, message))
    )
    worker.failed.connect(failures.append)
    worker.operation_finished.connect(finished.append)
    commands.pages_pdf_export_requested.emit(
        42, Path("synthetic-result.pdf"), OperationCancellation()
    )
    assert finished == [42]
    if error is PdfExportErrorCode.CANCELLED:
        assert cancelled and cancelled[0][0] == 42
        assert not failures
    else:
        assert failures[0].code == error.value
        assert failures[0].operation_id == 42
        assert failures[0].retryable


def test_export_bridge_announces_committed_outcome_and_progress() -> None:
    backend, _exporter = _backend()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: backend)
    commands.connect_worker(worker)
    worker.initialize()
    progress: list[UiProgress] = []
    messages: list[str] = []
    worker.progress_changed.connect(progress.append)
    worker.operation_status_changed.connect(lambda operation, message: messages.append(message))
    commands.pages_pdf_export_requested.emit(
        43, Path("synthetic-result.pdf"), OperationCancellation()
    )
    assert progress[0].operation_id == 43
    assert progress[0].cancelable
    assert progress[0].total == 4
    assert "verified PDF was saved" in messages[0]


def test_pages_pdf_export_button_is_keyboard_reachable_cancelable_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: False)
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        requests: list[tuple[int, Path, OperationCancellation]] = []
        commands.pages_pdf_export_requested.connect(
            lambda operation, destination, cancellation: requests.append(
                (operation, destination, cancellation)
            )
        )
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("synthetic-result.pdf", "")
        )
        button = window.findChild(QPushButton, "pagesPdfExportButton")
        assert button is not None and button.isEnabled()
        assert button.accessibleName() == "Export 1-page PDF…"
        _press(button)
        assert len(requests) == 1
        assert not window._import_button.isEnabled()
        assert not window._rotate_button.isEnabled()
        assert window._cancel_button.isEnabled()
        _press(window._cancel_button)
        assert requests[0][2].cancelled()
        worker.operation_cancelled.emit(requests[0][0], "PDF export cancelled before completion.")
        worker.operation_finished.emit(requests[0][0])
        _application().processEvents()
        assert button.isEnabled()
        assert button.hasFocus()
        _press(button)
        assert len(requests) == 2
        worker.failed.emit(
            UiFailure("PDF_EXPORT_DESTINATION_EXISTS", "Choose another name.", True, requests[1][0])
        )
        worker.operation_finished.emit(requests[1][0])
        _application().processEvents()
        assert button.isEnabled()
        assert button.hasFocus()


def test_pages_pdf_export_destination_cancel_starts_no_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: False)
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        requests: list[int] = []
        commands.pages_pdf_export_requested.connect(
            lambda operation, *_rest: requests.append(operation)
        )
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("", ""))
        _press(window._pages_pdf_export_button)
        assert requests == []
        assert window._active_operation is None


def test_export_refresh_does_not_replace_completion_with_an_unneeded_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: False)
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("synthetic.pdf", "")
        )
        _press(window._pages_pdf_export_button)
        active = window._active_operation
        assert active is not None and window._state is not None
        worker.state_changed.emit(replace(window._state, revision=4))
        worker.operation_status_changed.emit(active.operation_id, "The verified PDF was saved.")
        worker.operation_finished.emit(active.operation_id)
        _application().processEvents()
        assert not window._preview_refresh_timer.isActive()
        assert window._deferred_preview_state is None
        assert window._status_label.text() == "The verified PDF was saved."


def test_export_refresh_failure_guidance_offers_close_case_without_claiming_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: False)
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("synthetic.pdf", "")
        )
        _press(window._pages_pdf_export_button)
        active = window._active_operation
        assert active is not None
        worker.case_reopen_required.emit(active.operation_id, "The PDF was saved. Reopen the case.")
        worker.operation_finished.emit(active.operation_id)
        _application().processEvents()
        assert window._case_reopen_required
        assert window._close_case_button.isEnabled()
        assert window._close_case_button.hasFocus()
        assert not window._pages_pdf_export_button.isEnabled()
        assert not window._rotate_button.isEnabled()
        assert "import was saved" not in window._organizer_guidance.text().lower()
        assert "reopened" in window._organizer_guidance.text()


def test_organizer_export_outcome_rejects_another_case_or_invalid_types() -> None:
    backend, _exporter = _backend()
    outcome = backend.export_pages_pdf(
        Path("synthetic.pdf"), cancellation=lambda: False, progress=lambda value: None
    )
    with pytest.raises(PdfExportError):
        OrganizerPagesPdfExportOutcome(
            outcome.committed_export,
            replace(outcome.state, case_id=UUID("f0000000-0000-4000-8000-000000000099")),
            False,
        )
