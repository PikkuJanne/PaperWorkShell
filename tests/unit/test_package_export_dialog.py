from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.package_export import PackageExportDialog


def _application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


@contextmanager
def _dialog() -> Iterator[PackageExportDialog]:
    app = _application()
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: PackageExportDialog, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def _press(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    _application().processEvents()


def _summary(status: PackageSummaryStatus = PackageSummaryStatus.PLANNED) -> PackageSummary:
    committed = status is PackageSummaryStatus.COMMITTED
    return PackageSummary(
        status,
        outputs=(
            PackageSummaryOutput(
                filename="application.pdf",
                state=(
                    PackageSummaryOutputState.COMMITTED
                    if committed
                    else PackageSummaryOutputState.PLANNED
                ),
                page_count=2,
                byte_size=4200 if committed else None,
                categories=("<em>Application</em>",),
            ),
        ),
        active_page_count=2,
    )


def test_input_filenames_are_visible_plain_text_separate_from_authoritative_summary() -> None:
    app = _application()
    labels = (
        "Form Ää.PDF — source page 1, case page 1",
        "scan.png — source page 1, case page 2",
    )
    dialog = PackageExportDialog(tuple(PortalOutputMode), input_labels=labels)
    try:
        summary = _summary()
        dialog.show_review(summary, export_allowed=True)
        dialog.show()
        app.processEvents()
        names = _widget(dialog, QPlainTextEdit, "packageExportInputNames")
        text = _widget(dialog, QPlainTextEdit, "packageExportSummaryText")
        assert names.isVisible() and names.isReadOnly()
        assert names.toPlainText().splitlines() == list(labels)
        assert labels[0] not in text.toPlainText()
        assert "application.pdf" in text.toPlainText()
        assert "saved case page order" in names.accessibleDescription().casefold()
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def test_only_saved_allowed_modes_are_available_and_export_requires_fresh_review() -> None:
    app = _application()
    dialog = PackageExportDialog((PortalOutputMode.ZIP,), initial_mode=PortalOutputMode.ZIP)
    try:
        modes = _widget(dialog, QComboBox, "packageExportModeCombo")
        assert modes.count() == 1
        assert dialog.selected_mode is PortalOutputMode.ZIP
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isEnabled()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_plain_text_review_is_exact_selectable_and_not_html() -> None:
    with _dialog() as dialog:
        summary = _summary()
        dialog.show_review(summary, export_allowed=True)
        text = _widget(dialog, QPlainTextEdit, "packageExportSummaryText")
        assert text.toPlainText() == summary.to_plain_text()
        assert "<em>Application</em>" in text.toPlainText()
        assert text.isReadOnly()
        assert text.tabChangesFocus()
        assert dialog.summary is summary
        assert dialog.focusWidget() is text


def test_export_emits_explicit_acknowledgements_once_and_disables_repeated_requests() -> None:
    with _dialog() as dialog:
        emitted: list[tuple[bool, bool]] = []
        dialog.export_requested.connect(lambda signed, human: emitted.append((signed, human)))
        dialog.show_review(
            _summary(),
            export_allowed=True,
            signed_source_confirmation_required=True,
            human_review_required=True,
        )
        start = _widget(dialog, QPushButton, "packageExportStartButton")
        assert not start.isEnabled()
        _widget(dialog, QCheckBox, "packageExportHumanReviewCheck").setChecked(True)
        assert not start.isEnabled()
        _widget(dialog, QCheckBox, "packageExportUnsignedDerivativeCheck").setChecked(True)
        assert start.isEnabled()
        _press(start)
        _press(start)
        assert emitted == [(True, True)]
        assert dialog.busy
        assert not _widget(dialog, QComboBox, "packageExportModeCombo").isEnabled()


def test_blocking_review_cannot_be_overridden_by_checkboxes() -> None:
    with _dialog() as dialog:
        dialog.show_review(
            _summary(PackageSummaryStatus.NEEDS_REVIEW),
            export_allowed=False,
            signed_source_confirmation_required=True,
            human_review_required=True,
        )
        _widget(dialog, QCheckBox, "packageExportHumanReviewCheck").setChecked(True)
        _widget(dialog, QCheckBox, "packageExportUnsignedDerivativeCheck").setChecked(True)
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isEnabled()
        assert "blocking items" in _widget(dialog, QLabel, "packageExportStatus").text()


def test_new_mode_invalidates_summary_and_acknowledgements_before_emitting_request() -> None:
    with _dialog() as dialog:
        emitted: list[object] = []
        dialog.mode_requested.connect(emitted.append)
        dialog.show_review(_summary(), export_allowed=True, human_review_required=True)
        ack = _widget(dialog, QCheckBox, "packageExportHumanReviewCheck")
        ack.setChecked(True)
        modes = _widget(dialog, QComboBox, "packageExportModeCombo")
        modes.setCurrentIndex(modes.findData(PortalOutputMode.ZIP.value))
        assert emitted == [PortalOutputMode.ZIP]
        assert dialog.summary is None
        assert not ack.isChecked()
        assert dialog.busy
        assert (
            "application.pdf"
            not in _widget(dialog, QPlainTextEdit, "packageExportSummaryText").toPlainText()
        )
        dialog.show_review(_summary(), export_allowed=True, human_review_required=True)
        assert not ack.isChecked()
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isEnabled()


def test_escape_cancels_only_once_and_waits_for_terminal_result() -> None:
    with _dialog() as dialog:
        emitted: list[bool] = []
        dialog.cancel_requested.connect(lambda: emitted.append(True))
        dialog.set_busy("generating_pdfs")
        dialog.reject()
        dialog.reject()
        assert emitted == [True]
        assert dialog.isVisible()
        cancellation_pending = dialog.busy
        assert cancellation_pending
        assert not dialog.set_progress("generating_pdfs", 1, 2)
        dialog.show_result(_summary(PackageSummaryStatus.NOT_COMMITTED))
        terminal_busy = dialog.busy
        assert not terminal_busy
        dialog.reject()
        assert not dialog.isVisible()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PackageSummaryStatus.COMMITTED, "Package export complete"),
        (PackageSummaryStatus.CONSTRAINTS_BLOCKED, "blocked by constraints"),
        (PackageSummaryStatus.NOT_COMMITTED, "did not publish"),
        (PackageSummaryStatus.PARTIAL, "some outputs were saved"),
        (PackageSummaryStatus.UNCERTAIN, "could not confirm"),
    ],
)
def test_every_terminal_result_has_truthful_status_and_disables_export(
    status: PackageSummaryStatus,
    expected: str,
) -> None:
    with _dialog() as dialog:
        summary = _summary(status)
        dialog.show_result(summary)
        assert expected in _widget(dialog, QLabel, "packageExportStatus").text()
        assert (
            _widget(dialog, QPlainTextEdit, "packageExportSummaryText").toPlainText()
            == summary.to_plain_text()
        )
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isVisible()
        assert not _widget(dialog, QPushButton, "packageExportSaveSummaryButton").isVisible()
        assert _widget(dialog, QPushButton, "packageExportCloseButton").isEnabled()


def test_summary_save_is_explicit_and_failure_preserves_exact_terminal_inventory() -> None:
    with _dialog() as dialog:
        emitted: list[bool] = []
        dialog.summary_save_requested.connect(lambda: emitted.append(True))
        summary = _summary(PackageSummaryStatus.COMMITTED)
        dialog.show_result(summary, save_available=True)
        assert emitted == []
        save = _widget(dialog, QPushButton, "packageExportSaveSummaryButton")
        _press(save)
        assert emitted == [True]
        assert not save.isEnabled()
        dialog.complete_summary_save(succeeded=False)
        assert save.isEnabled()
        assert dialog.summary is summary
        assert (
            "package result above is unchanged"
            in _widget(dialog, QLabel, "packageExportStatus").text()
        )
        _press(save)
        dialog.complete_summary_save(succeeded=True)
        assert "summary was saved" in _widget(dialog, QLabel, "packageExportStatus").text()


def test_raw_failure_and_progress_strings_are_never_displayed() -> None:
    with _dialog() as dialog:
        private = "C:/private/source.pdf secret exception"
        dialog.set_busy(private)
        assert dialog.set_progress(private, 1, 2)
        dialog.show_failure(private, phase="export")
        text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert private not in text
        assert "not established" in text
        assert dialog.focusWidget() is _widget(dialog, QPushButton, "packageExportCloseButton")


def test_cancel_summary_save_waits_and_retains_known_package_inventory() -> None:
    with _dialog() as dialog:
        summary = _summary(PackageSummaryStatus.COMMITTED)
        dialog.show_result(summary, save_available=True)
        cancelled: list[bool] = []
        dialog.cancel_requested.connect(lambda: cancelled.append(True))
        _press(_widget(dialog, QPushButton, "packageExportSaveSummaryButton"))
        cancel = _widget(dialog, QPushButton, "packageExportCancelButton")
        assert cancel.isVisible() and cancel.isEnabled()
        dialog.reject()
        dialog.reject()
        assert cancelled == [True]
        assert dialog.isVisible()
        pending = dialog.busy
        assert pending
        assert dialog.summary is summary
        dialog.complete_summary_save(succeeded=False)
        finished = dialog.busy
        assert not finished
        assert dialog.summary is summary
        assert (
            _widget(dialog, QPlainTextEdit, "packageExportSummaryText").toPlainText()
            == summary.to_plain_text()
        )
        status = _widget(dialog, QLabel, "packageExportStatus").text()
        assert "cancellation request" in status
        assert "package result above is unchanged" in status
        assert "summary was saved" not in status


def test_size_only_blocker_allows_preview_with_explicit_human_and_signed_consent() -> None:
    with _dialog() as dialog:
        requests: list[tuple[bool, bool]] = []
        dialog.compression_requested.connect(lambda signed, human: requests.append((signed, human)))
        dialog.show_review(
            _summary(PackageSummaryStatus.NEEDS_REVIEW),
            export_allowed=False,
            compression_allowed=True,
            human_review_required=True,
            signed_source_confirmation_required=True,
        )
        preview = _widget(dialog, QPushButton, "packageExportCompressionButton")
        assert not preview.isEnabled()
        _widget(dialog, QCheckBox, "packageExportHumanReviewCheck").setChecked(True)
        assert not preview.isEnabled()
        _widget(dialog, QCheckBox, "packageExportUnsignedDerivativeCheck").setChecked(True)
        assert preview.isEnabled()
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isEnabled()
        _press(preview)
        _press(preview)
        assert requests == [(True, True)]
        assert dialog.busy


def test_preview_requires_explicit_capability_and_new_reviews_clear_its_consent() -> None:
    with _dialog() as dialog:
        dialog.show_review(_summary(), export_allowed=True)
        preview = _widget(dialog, QPushButton, "packageExportCompressionButton")
        assert not preview.isEnabled()
        dialog.show_review(
            _summary(),
            export_allowed=True,
            compression_allowed=True,
            human_review_required=True,
        )
        _widget(dialog, QCheckBox, "packageExportHumanReviewCheck").setChecked(True)
        assert preview.isEnabled()
        dialog.show_review(
            _summary(),
            export_allowed=True,
            compression_allowed=True,
            human_review_required=True,
        )
        assert not preview.isEnabled()
        dialog.show_result(_summary(PackageSummaryStatus.COMMITTED))
        assert not preview.isVisible()
