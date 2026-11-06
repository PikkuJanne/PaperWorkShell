"""Qt-interface and spoken-event checks for long-operation dialogs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QImage
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from paperworkshell.app.scan_review import ScanReviewDraft, ScanReviewPreviewVariant
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.compression_review import CompressionReviewDialog
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.scan_review import ScanReviewDialog


def _application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


@contextmanager
def _dialog[T: PackageExportDialog | CompressionReviewDialog | ScanReviewDialog](
    factory: Callable[[], T],
) -> Iterator[T]:
    application = _application()
    dialog = factory()
    dialog.show()
    application.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        application.processEvents()


def _spoken(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append(event.message())

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    return messages


def _current_label_name(dialog: object, label: QLabel) -> str:
    del dialog
    interface = QAccessible.queryAccessibleInterface(label)
    assert interface is not None
    assert interface.role() is QAccessible.Role.StaticText
    name = interface.text(QAccessible.Text.Name)
    assert label.text() in name
    return name


@pytest.mark.parametrize("kind", ["package", "compression"])
def test_export_dialogs_sample_progress_and_preserve_immediate_outcome(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    factory: Callable[[], PackageExportDialog | CompressionReviewDialog] = (
        (lambda: PackageExportDialog(tuple(PortalOutputMode)))
        if kind == "package"
        else (lambda: CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1))
    )
    with _dialog(factory) as dialog:
        messages = _spoken(monkeypatch)
        clock = [0.0]
        dialog._announcer = ScreenReaderAnnouncer(dialog, clock=lambda: clock[0])
        dialog.set_busy("preparing")
        assert len(messages) == 1
        assert dialog.set_progress("preparing", 0, 100)
        for completed in range(1, 101):
            assert dialog.set_progress("preparing", completed, 100)
        assert len(messages) == 2
        status = dialog.findChild(
            QLabel, f"{kind}ExportStatus" if kind == "package" else "compressionReviewStatus"
        )
        assert status is not None
        assert "100 of 100" in _current_label_name(dialog, status)
        clock[0] = 2.0
        assert dialog.set_progress("preparing", 100, 100)
        assert len(messages) == 3
        dialog.show_failure()
        assert len(messages) == 4
        recovery_name = (
            "packageExportRefreshButton" if kind == "package" else "compressionReviewPrepareButton"
        )
        recovery = dialog.findChild(QPushButton, recovery_name)
        assert recovery is not None and recovery.isEnabled()
        assert dialog.focusWidget() is recovery
        _current_label_name(dialog, status)
        assert "again" in messages[-1] or "Refresh review" in messages[-1]
        dialog.set_busy("preparing")
        assert dialog.set_progress("preparing", 0, 100)
        assert len(messages) == 6


@pytest.mark.parametrize("kind", ["package", "compression"])
def test_export_cancellation_is_announced_once_and_stale_progress_is_ignored(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    factory: Callable[[], PackageExportDialog | CompressionReviewDialog] = (
        (lambda: PackageExportDialog(tuple(PortalOutputMode)))
        if kind == "package"
        else (lambda: CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1))
    )
    with _dialog(factory) as dialog:
        messages = _spoken(monkeypatch)
        dialog.set_busy("preparing")
        dialog.reject()
        dialog.reject()
        assert sum("Cancellation requested" in message for message in messages) == 1
        assert not dialog.set_progress("preparing", 1, 1)
        if isinstance(dialog, PackageExportDialog):
            dialog.show_result(PackageSummary(PackageSummaryStatus.NOT_COMMITTED))
            assert "did not publish" in messages[-1]
        else:
            dialog.show_failure()
            assert "could not be prepared" in messages[-1]


def _draft() -> ScanReviewDraft:
    intent = ImageTransformIntent()
    return ScanReviewDraft(
        case_id=UUID("23000000-0000-4000-8000-000000000001"),
        page_id=UUID("23000000-0000-4000-8000-000000000002"),
        manifest_revision=1,
        base_intent=intent,
        candidate_intent=intent,
        suggestion_present=False,
    )


def test_scan_preview_start_progress_cancellation_and_retry_have_current_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog(lambda: ScanReviewDialog(_draft())) as dialog:
        messages = _spoken(monkeypatch)
        clock = [0.0]
        dialog._announcer = ScreenReaderAnnouncer(dialog, clock=lambda: clock[0])
        request = dialog.draft
        assert dialog.set_preview_loading(request)
        assert "Building original and adjusted previews" in messages[-1]
        assert dialog.set_progress(request, "generating", 0, 100)
        for completed in range(1, 101):
            assert dialog.set_progress(request, "generating", completed, 100)
        assert len(messages) == 2
        status = dialog.findChild(QLabel, "scanReviewStatus")
        assert status is not None
        assert "100 of 100" in _current_label_name(dialog, status)
        dialog.reject()
        dialog.reject()
        assert sum("Cancellation requested" in message for message in messages) == 1
        assert not dialog.set_progress(request, "generating", 100, 100)
        assert dialog.set_preview_cancelled(request, ScanReviewPreviewVariant.ORIGINAL)
        assert "Preview cancelled" in messages[-1]
        retry = dialog.findChild(QPushButton, "scanReviewUpdatePreviewButton")
        assert dialog.focusWidget() is retry
        assert dialog.set_preview_loading(request)
        assert dialog.set_progress(request, "generating", 0, 100)
        assert "0 of 100" in messages[-1]
        assert dialog.set_failure(
            request,
            "SCAN_REVIEW_PREVIEW_FAILED",
            "The preview could not be prepared. Try again.",
            variant=ScanReviewPreviewVariant.ORIGINAL,
        )
        error = dialog.findChild(QLabel, "scanReviewError")
        assert error is not None
        assert "SCAN_REVIEW_PREVIEW_FAILED" in _current_label_name(dialog, error)
        assert "Update preview" in error.accessibleDescription()
        assert dialog.focusWidget() is retry
        assert "The preview could not be prepared" in messages[-1]
        assert "Update preview" in messages[-1]


def test_unknown_progress_phase_never_reaches_accessible_names_or_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = "PRIVATE_PROGRESS_SOURCE_PATH"
    with _dialog(lambda: PackageExportDialog(tuple(PortalOutputMode))) as dialog:
        messages = _spoken(monkeypatch)
        dialog.set_busy(private)
        assert dialog.set_progress(private, 1, 2)
        status = dialog.findChild(QLabel, "packageExportStatus")
        assert status is not None
        assert private not in _current_label_name(dialog, status)
        assert all(private not in message for message in messages)


def test_instructions_and_unsigned_derivative_warnings_do_not_require_descriptions() -> None:
    with _dialog(lambda: PackageExportDialog(tuple(PortalOutputMode))) as dialog:
        for name in (
            "packageExportGuidance",
            "packageExportHumanReviewGuidance",
            "packageExportUnsignedDerivativeGuidance",
        ):
            label = dialog.findChild(QLabel, name)
            assert label is not None
            _current_label_name(dialog, label)
    with _dialog(lambda: CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1)) as compression:
        floor = compression.findChild(QLabel, "compressionReviewFloor")
        assert floor is not None
        assert "200 dpi" in _current_label_name(compression, floor)
    with _dialog(lambda: ScanReviewDialog(_draft())) as scan:
        for name in (
            "scanReviewGuidance",
            "scanReviewSuggestionNotice",
            "scanReviewOriginalPreviewStatus",
            "scanReviewAdjustedPreviewStatus",
        ):
            label = scan.findChild(QLabel, name)
            assert label is not None
            _current_label_name(scan, label)


def test_scan_apply_failure_focuses_enabled_retry_and_announces_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dialog(lambda: ScanReviewDialog(_draft())) as dialog:
        messages = _spoken(monkeypatch)
        request = dialog.draft
        assert dialog.set_preview_loading(request)
        image = QImage(20, 20, QImage.Format.Format_RGBA8888)
        image.fill(0xFFFFFFFF)
        assert dialog.set_original_image(request, image)
        assert dialog.set_adjusted_image(request, image)
        apply = dialog.findChild(QPushButton, "scanReviewApplyAdjustmentsButton")
        assert apply is not None and apply.isEnabled()
        apply.click()
        assert dialog.set_failure(
            request,
            "SCAN_REVIEW_APPLY_FAILED",
            "The scan adjustments could not be saved. Try again.",
        )
        assert apply.isEnabled() and dialog.focusWidget() is apply
        assert "could not be saved" in messages[-1]
        assert "Apply adjustments" in messages[-1]
