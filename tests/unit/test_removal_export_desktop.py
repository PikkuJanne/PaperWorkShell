"""The real desktop route reviews exact intent and invokes the gated export port."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QFileDialog, QPushButton

import paperworkshell.__main__ as desktop
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.redaction_export import ReviewedRedactionExport
from paperworkshell.app.removal_export_review import RemovalExportReview
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.signed_source_policy import SignedSourcePolicyError
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker
from paperworkshell.ui.removal_export_review import RemovalExportReviewDialog
from paperworkshell.ui.window import _LongOperationKind
from paperworkshell.worker.desktop_composition import OCR_WORKER_MAXIMUM_PROCESSES
from tests.unit.test_organizer_backend import (
    CASE_ID,
    PAGE_IDS,
    _document,
    _Lifecycle,
    _Repository,
    _SourceIngest,
    _Thumbnails,
)
from tests.unit.test_package_export_window import _application, _press, _window
from tests.unit.test_pdf_export_entrypoint import _manager, _synthetic_export_config


class _Exporter:
    def __init__(self) -> None:
        self.calls: list[ReviewedRedactionExport] = []

    def export(
        self,
        review: ReviewedRedactionExport,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[PdfExportProgress], None],
    ) -> RedactionExportReport:
        assert destination.is_absolute() and not deadline() and not cancellation()
        self.calls.append(review)
        progress(PdfExportProgress(PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING, 1, 1))
        return RedactionExportReport(
            review.snapshot.case_id,
            review.snapshot.revision,
            (review.request.page_id,),
            None,
            RedactionExportStatus.BLOCKED,
            RedactionExportCode.RELEASE_PENDING,
        )


def _backend(*, signed: bool = False) -> tuple[OrganizerBackend, _Repository, _Exporter]:
    document = _document(page_count=1)
    document["redaction_marks"] = [
        RedactionMark(
            UUID(int=81001, version=4), PAGE_IDS[0], NormalizedPageBox(0, 0, 100_000, 100_000)
        ).to_manifest_record()
    ]
    if signed:
        cast(list[dict[str, object]], document["sources"])[0]["signature_state"] = (
            "markers_detected"
        )
    repository = _Repository(document)
    exporter = _Exporter()
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        removal_pdf_export=exporter,
    )
    backend.open_case(CASE_ID)
    return backend, repository, exporter


def test_exact_saved_review_invokes_separate_service_without_promoting_marks(
    tmp_path: Path,
) -> None:
    backend, repository, exporter = _backend()
    backend.rotate_clockwise(PAGE_IDS[0])
    review = backend.begin_removal_export()
    original = repository.current
    report = backend.export_removal_pdf(review, tmp_path / "new.pdf", signed_source_confirmed=False)
    assert len(exporter.calls) == 1
    assert exporter.calls[0].snapshot == original
    assert report.code is RedactionExportCode.RELEASE_PENDING
    assert repository.current == original
    assert backend.state.removal_page_ids == (PAGE_IDS[0],)
    assert not backend.state.autosave_pending
    assert "Synthetic packet" not in repr(review)


def test_changed_review_never_reaches_service(tmp_path: Path) -> None:
    backend, _, exporter = _backend()
    review = backend.begin_removal_export()
    backend.rotate_clockwise(PAGE_IDS[0])
    with pytest.raises(PdfExportError) as failed:
        backend.export_removal_pdf(review, tmp_path / "new.pdf", signed_source_confirmed=False)
    assert failed.value.code is PdfExportErrorCode.PLAN_STALE
    assert not exporter.calls


def test_signed_consent_is_explicit_saved_and_rebound_before_worker(tmp_path: Path) -> None:
    backend, repository, exporter = _backend(signed=True)
    review = backend.begin_removal_export()
    before = repository.current
    with pytest.raises(SignedSourcePolicyError):
        backend.export_removal_pdf(review, tmp_path / "new.pdf", signed_source_confirmed=False)
    assert repository.current == before and not exporter.calls
    changed: list[object] = []
    report = backend.export_removal_pdf(
        review,
        tmp_path / "new.pdf",
        signed_source_confirmed=True,
        state_changed=changed.append,
    )
    assert len(exporter.calls) == 1 and len(changed) == 1
    assert exporter.calls[0].snapshot.revision == before.revision + 1
    assert report.manifest_revision == backend.state.revision == before.revision + 1
    assert backend.state.removal_page_ids == (PAGE_IDS[0],)


def test_cancel_before_dispatch_does_not_save_signed_consent(tmp_path: Path) -> None:
    backend, repository, exporter = _backend(signed=True)
    review = backend.begin_removal_export()
    before = repository.current
    with pytest.raises(PdfExportError) as failed:
        backend.export_removal_pdf(
            review,
            tmp_path / "new.pdf",
            signed_source_confirmed=True,
            cancellation=lambda: True,
        )
    assert failed.value.code is PdfExportErrorCode.CANCELLED
    assert repository.current == before and not exporter.calls


def test_bridge_correlates_review_progress_report_and_terminal_state(tmp_path: Path) -> None:
    backend, _, exporter = _backend()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: backend)
    commands.connect_worker(worker)
    worker.initialize()
    reviews: list[tuple[int, RemovalExportReview]] = []
    reports: list[tuple[int, RedactionExportReport]] = []
    finished: list[int] = []
    progress: list[object] = []
    worker.removal_export_review_ready.connect(lambda op, review: reviews.append((op, review)))
    worker.removal_export_ready.connect(lambda op, report: reports.append((op, report)))
    worker.operation_finished.connect(finished.append)
    worker.progress_changed.connect(progress.append)
    worker.begin_removal_export(101)
    assert reviews[0][0] == 101 and finished == [101]
    from paperworkshell.ui.backend import OperationCancellation

    worker.export_removal_pdf(
        102, reviews[0][1], tmp_path / "new.pdf", False, OperationCancellation()
    )
    assert reports[0][0] == 102 and finished == [101, 102]
    assert len(exporter.calls) == 1 and progress


def test_review_dialog_requires_keyboard_review_and_unsigned_consent() -> None:
    app = _application()
    backend, _, _ = _backend(signed=True)
    dialog = RemovalExportReviewDialog(backend.begin_removal_export())
    dialog.show()
    app.processEvents()
    reviewed = dialog.findChild(QCheckBox, "removalExportReviewedCheckBox")
    signed = dialog.findChild(QCheckBox, "removalExportUnsignedCheckBox")
    choose = dialog.findChild(QPushButton, "removalExportChooseFileButton")
    cancel = dialog.findChild(QPushButton, "removalExportCancelButton")
    assert reviewed is not None and signed is not None and choose is not None and cancel is not None
    assert not choose.isEnabled() and cancel.isDefault()
    reviewed.setFocus()
    QTest.keyClick(reviewed, Qt.Key.Key_Space)
    assert not choose.isEnabled()
    signed.setFocus()
    QTest.keyClick(signed, Qt.Key.Key_Space)
    assert choose.isEnabled()
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def test_desktop_destination_flow_waits_for_terminal_and_preserves_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend, _, _ = _backend()
    review = backend.begin_removal_export()
    with _window() as (window, commands, worker):
        state = backend.state
        worker.state_changed.emit(
            replace(
                state, pages=tuple(replace(page, preview_supported=False) for page in state.pages)
            )
        )
        commands.removal_export_review_requested.disconnect(worker.begin_removal_export)
        commands.removal_pdf_export_requested.disconnect(worker.export_removal_pdf)
        beginnings: list[int] = []
        exports: list[tuple[object, ...]] = []
        commands.removal_export_review_requested.connect(beginnings.append)
        commands.removal_pdf_export_requested.connect(lambda *args: exports.append(args))
        _press(window._removal_export_button)
        worker.removal_export_review_ready.emit(beginnings[0], review)
        assert cast(object, window._removal_review_dialog) is None
        worker.operation_finished.emit(beginnings[0])
        _application().processEvents()
        dialog = window._removal_review_dialog
        assert dialog is not None and dialog.isVisible()
        reviewed = dialog.findChild(QCheckBox, "removalExportReviewedCheckBox")
        choose = dialog.findChild(QPushButton, "removalExportChooseFileButton")
        assert reviewed is not None and choose is not None
        reviewed.setChecked(True)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: ("", ""))
        _press(choose)
        assert not exports and window._removal_review_dialog is dialog
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **kw: (str(tmp_path / "new.pdf"), "")
        )
        _press(choose)
        assert len(exports) == 1 and exports[0][1] is review
        operation = window._active_operation
        assert operation is not None and operation.kind is _LongOperationKind.REMOVAL_EXPORT
        assert window._cancel_button.isEnabled()
        _press(window._cancel_button)
        assert operation.cancellation.cancelled()
        worker.removal_export_ready.emit(
            operation.operation_id,
            RedactionExportReport(
                review.case_id,
                review.manifest_revision,
                review.affected_page_ids,
                None,
                RedactionExportStatus.NOT_COMMITTED,
                RedactionExportCode.CANCELLED,
            ),
        )
        assert cast(object, window._redaction_export_dialog) is None
        worker.operation_finished.emit(operation.operation_id)
        _application().processEvents()
        report_dialog = window._redaction_export_dialog
        assert report_dialog is not None and report_dialog.isVisible()
        assert report_dialog.report.code is RedactionExportCode.CANCELLED
        retry = report_dialog.findChild(QPushButton, "redactionExportRetryButton")
        assert retry is not None and retry.isEnabled()
        _press(retry)
        _application().processEvents()
        assert len(beginnings) == 2
        worker.operation_finished.emit(beginnings[-1])
        assert backend.state.removal_page_ids == (PAGE_IDS[0],)


def test_cancelled_review_discards_late_result_and_restores_actions() -> None:
    backend, _, _ = _backend()
    review = backend.begin_removal_export()
    with _window() as (window, commands, worker):
        state = backend.state
        worker.state_changed.emit(
            replace(
                state, pages=tuple(replace(page, preview_supported=False) for page in state.pages)
            )
        )
        commands.removal_export_review_requested.disconnect(worker.begin_removal_export)
        requests: list[int] = []
        commands.removal_export_review_requested.connect(requests.append)
        _press(window._removal_export_button)
        _press(window._cancel_button)
        worker.removal_export_review_ready.emit(requests[0], review)
        worker.operation_finished.emit(requests[0])
        _application().processEvents()
        assert window._removal_review_dialog is None
        assert window._removal_export_button.isEnabled()


def test_production_builder_uses_admitted_ocr_and_shared_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    base = _synthetic_export_config(tmp_path)
    executable, tessdata = tmp_path / "tesseract.exe", tmp_path / "tessdata"
    monkeypatch.setattr(
        desktop, "_resource_bound_ocr_resources", lambda *args: (executable, tessdata)
    )
    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", lambda **kwargs: base)
    observed: list[tuple[object, dict[str, object]]] = []

    def create(config: object, **kwargs: object) -> object:
        observed.append((config, kwargs))
        return object()

    monkeypatch.setattr(desktop, "create_redaction_export_worker_composition", create)
    desktop.build_windows_redaction_export_worker_composition(resources)
    config, values = observed[0]
    assert type(config) is type(base)
    assert config.resource_policy.maximum_processes == OCR_WORKER_MAXIMUM_PROCESSES
    assert config.resource_policy.process_memory_bytes == base.resource_policy.process_memory_bytes
    assert config.resource_policy.job_memory_bytes == base.resource_policy.job_memory_bytes
    assert config.export_policy == base.export_policy
    assert config.timeout_policy.handshake_seconds == 40.0
    assert values == {
        "heavy_operations": resources.heavy_operations,
        "tesseract_executable": executable,
        "tessdata_root": tessdata,
    }


def test_production_builder_cannot_raise_an_injected_process_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = replace(CONSTRAINED_RESOURCE_POLICY_V1, worker_maximum_processes=1)
    resources = desktop.create_desktop_application_resource_context(policy)
    base = _synthetic_export_config(tmp_path)
    monkeypatch.setattr(
        desktop, "_resource_bound_ocr_resources", lambda *args: (tmp_path / "engine.exe", tmp_path)
    )
    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", lambda **kwargs: base)
    called: list[object] = []

    def create(*args: object, **_kwargs: object) -> object:
        called.append(args)
        return object()

    monkeypatch.setattr(
        desktop,
        "create_redaction_export_worker_composition",
        create,
    )
    with pytest.raises(PdfExportError) as rejected:
        desktop.build_windows_redaction_export_worker_composition(resources)
    assert rejected.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not called


def test_lazy_production_service_constructs_once_and_closes_owned_composition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend, repository, exporter = _backend()
    review = ReviewedRedactionExport.from_snapshot(backend.begin_removal_export().snapshot)
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    calls: list[object] = []
    closings: list[str] = []
    owned = desktop._BackendWorkerResources(lambda: closings.append("desktop"))
    composition = SimpleNamespace(exporter=exporter, close=lambda: closings.append("removal"))

    def create_composition(selected: object) -> SimpleNamespace:
        calls.append(selected)
        return composition

    monkeypatch.setattr(
        desktop, "build_windows_redaction_export_worker_composition", create_composition
    )
    monkeypatch.setattr(desktop, "WindowsManifestRepository", lambda manager: repository)
    constructed: list[tuple[object, ...]] = []

    def service(*args: object) -> _Exporter:
        constructed.append(args)
        return exporter

    monkeypatch.setattr(desktop, "RedactionExportService", service)
    sources = cast(WindowsSourceIngestStore, object())
    lazy = desktop._LazyRemovalPdfExportService(_manager(tmp_path), sources, resources, owned)
    assert not calls and not constructed
    with pytest.raises(PdfExportError) as cancelled:
        lazy.export(
            review,
            tmp_path / "new.pdf",
            deadline=lambda: False,
            cancellation=lambda: True,
            progress=lambda _value: None,
        )
    assert cancelled.value.code is PdfExportErrorCode.CANCELLED and not calls
    for _ in range(2):
        lazy.export(
            review,
            tmp_path / "new.pdf",
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )
    assert calls == [resources] and len(constructed) == 1
    assert constructed[0][0] is repository and constructed[0][1] is sources
    assert constructed[0][2] is exporter
    owned.close()
    owned.close()
    assert closings == ["removal", "desktop"]
    with pytest.raises(PdfExportError):
        lazy.export(
            review,
            tmp_path / "new.pdf",
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )
