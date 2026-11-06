"""PWS-0701 regression coverage for dialog keyboard review and focus recovery."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
)
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.export.package_export import PackageExportError, PackageExportErrorCode
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.package_export import PackageExportDialog
from tests.accessibility.test_checklist_accessibility import (
    PAGE_IDS,
    _application,
    _button,
    _dialog,
    _empty_state,
)
from tests.accessibility.test_constraint_editor_accessibility import _dialog as _constraint_dialog
from tests.accessibility.test_redaction_export_accessibility import _dialog as _result_dialog
from tests.accessibility.test_redaction_tools_accessibility import (
    _dialog as _removal_mark_dialog,
)
from tests.accessibility.test_redaction_tools_accessibility import _state as _removal_mark_state
from tests.unit.test_compression_review_dialog import dialog_context as _compression_dialog


@contextmanager
def _package_dialog() -> Iterator[PackageExportDialog]:
    app = _application()
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    dialog.show_review(PackageSummary(PackageSummaryStatus.PLANNED), export_allowed=True)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: QDialog, kind: type[T], name: str) -> T:
    widget = dialog.findChild(kind, name)
    assert widget is not None
    return widget


def test_checklist_history_exhaustion_keeps_focus_on_an_enabled_history_action() -> None:
    state = _empty_state().add_pages("application-form", PAGE_IDS[:1])
    with _dialog(state) as dialog:
        undo = _button(dialog, "checklistUndoButton")
        redo = _button(dialog, "checklistRedoButton")
        undo.setFocus()
        QTest.keyClick(undo, Qt.Key.Key_Space)
        assert not undo.isEnabled()
        assert redo.isEnabled()
        assert dialog.focusWidget() is redo
        QTest.keyClick(redo, Qt.Key.Key_Space)
        assert not redo.isEnabled()
        assert undo.isEnabled()
        assert dialog.focusWidget() is undo


def test_filename_reorder_and_removal_keep_the_keyboard_at_the_selected_name() -> None:
    with _constraint_dialog() as dialog:
        stem = _widget(dialog, QLineEdit, "constraintNameStemInput")
        names = _widget(dialog, QListWidget, "constraintNameList")
        add = _widget(dialog, QPushButton, "constraintNameAddButton")
        for value in ("packet", "support"):
            stem.setText(value)
            add.setFocus()
            QTest.keyClick(add, Qt.Key.Key_Space)
        earlier = _widget(dialog, QPushButton, "constraintNameEarlierButton")
        earlier.setFocus()
        QTest.keyClick(earlier, Qt.Key.Key_Space)
        assert names.currentRow() == 0
        assert names.item(0).text() == "support.pdf"
        assert dialog.focusWidget() is names
        remove = _widget(dialog, QPushButton, "constraintNameRemoveButton")
        remove.setFocus()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        assert names.count() == 1 and names.currentRow() == 0
        assert dialog.focusWidget() is names
        remove.setFocus()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        assert names.count() == 0
        assert dialog.focusWidget() is stem


def test_removal_numeric_text_keys_never_change_draft_history_or_remove_a_mark() -> None:
    with _removal_mark_dialog(state=_removal_mark_state().create()) as dialog:
        field = _widget(dialog, QSpinBox, "redactionLeftSpin")
        editor = field.findChild(QLineEdit)
        assert editor is not None
        field.setFocus()
        before = dialog.state
        assert not editor.isUndoAvailable()
        # No local history must remain a text-editor no-op, never draft Undo.
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert dialog.state is before
        field.selectAll()
        QTest.keyClicks(field, "200000")
        QTest.keyClick(field, Qt.Key.Key_Home)
        QTest.keyClick(field, Qt.Key.Key_Delete)
        assert editor.text() == "00000"
        assert dialog.state is before
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert editor.text() == "200000"
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert editor.text() == "100000"
        QTest.keyClick(field, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert editor.text() == "200000"
        assert dialog.state is before
        assert len(dialog.state.records) == 1


@pytest.mark.parametrize("kind", ("package", "compression", "removal"))
def test_read_only_reports_support_keyboard_end_home_and_tab_escape(kind: str) -> None:
    context = {
        "package": _package_dialog,
        "compression": _compression_dialog,
        "removal": _result_dialog,
    }[kind]
    with context() as dialog:
        editor_name = {
            "package": "packageExportSummaryText",
            "compression": "compressionReviewSummaryText",
            "removal": "redactionExportReportText",
        }[kind]
        editor = _widget(dialog, QPlainTextEdit, editor_name)
        # A long synthetic report isolates scrolling from document processing.
        text = "\n".join(f"Synthetic review line {number}" for number in range(250))
        editor.setPlainText(text)
        editor.setFocus()
        _application().processEvents()
        scrollbar = editor.verticalScrollBar()
        assert scrollbar.maximum() > 0
        QTest.keyClick(editor, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        assert editor.textCursor().atEnd()
        assert scrollbar.value() > 0
        assert editor.viewport().rect().intersects(editor.cursorRect())
        QTest.keyClick(editor, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
        assert editor.textCursor().atStart()
        assert scrollbar.value() == 0
        QTest.keyClicks(editor, "must not change the report")
        assert editor.toPlainText() == text
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        following = dialog.focusWidget()
        assert following is not None and following is not editor and following.isEnabled()
        QTest.keyClick(following, Qt.Key.Key_Backtab)
        assert dialog.focusWidget() is editor
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        assert not dialog.isVisible()


def _package_error(
    code: PackageExportErrorCode | PackagePdfCandidateErrorCode,
) -> PackageExportError | PackagePdfCandidateError:
    if isinstance(code, PackageExportErrorCode):
        return PackageExportError(code, "package.test")
    return PackagePdfCandidateError(code, "package.test")


@pytest.mark.parametrize("code", (*PackageExportErrorCode, *PackagePdfCandidateErrorCode))
def test_package_known_failure_keeps_safe_cause_and_keyboard_recovery(
    code: PackageExportErrorCode | PackagePdfCandidateErrorCode,
) -> None:
    error = _package_error(code)
    safe_message = error.safe_message
    private = "C:/private/source.pdf PRIVATE PACKAGE FAILURE"
    error.safe_message = private
    error.context = private
    error.args = (private,)
    failure = sanitize_ui_failure(error, operation_id=91)
    assert failure.code == code.value
    assert failure.message == safe_message
    assert failure.operation_id == 91
    assert failure.retryable
    with _package_dialog() as dialog:
        summary = _widget(dialog, QPlainTextEdit, "packageExportSummaryText")
        original_summary = summary.toPlainText()
        dialog.set_busy("generating_pdfs")
        dialog.show_failure(private, phase="export", code=failure.code)
        status = _widget(dialog, QLabel, "packageExportStatus")
        assert safe_message in status.text()
        assert "destination state is not established" in status.text()
        assert "Inspect the destination" in status.text()
        assert private not in status.text()
        assert summary.toPlainText() == original_summary
        assert status.text() in status.accessibleName()
        assert not _widget(dialog, QPushButton, "packageExportStartButton").isEnabled()
        close = _widget(dialog, QPushButton, "packageExportCloseButton")
        assert close.isEnabled()
        assert dialog.focusWidget() is close
        QTest.keyClick(close, Qt.Key.Key_Escape)
        assert not dialog.isVisible()


@pytest.mark.parametrize(
    ("code", "recovery_terms"),
    (
        (
            PackageExportErrorCode.PDF_FAILED,
            ("output format", "requested filenames", "Constraints"),
        ),
        (
            PackageExportErrorCode.PLAN_INVALID,
            ("output format", "requested filenames", "Constraints"),
        ),
        (PackageExportErrorCode.LIMIT_EXCEEDED, ("constraints", "processing limits")),
        (
            PackagePdfCandidateErrorCode.UNSUPPORTED_OVERLAY_TRANSFORM,
            ("output format", "requested filenames", "Constraints"),
        ),
    ),
)
def test_package_known_failure_identifies_relevant_review_controls(
    code: PackageExportErrorCode | PackagePdfCandidateErrorCode,
    recovery_terms: tuple[str, ...],
) -> None:
    with _package_dialog() as dialog:
        dialog.show_failure(phase="export", code=code.value)
        text = _widget(dialog, QLabel, "packageExportStatus").text()
        assert all(term in text for term in recovery_terms)
        assert "destination state is not established" in text


def test_package_unknown_failure_or_subclass_cannot_supply_display_text() -> None:
    private = "C:/private/source.pdf PRIVATE PACKAGE FAILURE"

    class ForeignError(RuntimeError):
        code = PackageExportErrorCode.PDF_FAILED
        context = private
        safe_message = private

    class ForeignPackageError(PackageExportError):
        pass

    for error in (
        ForeignError(private),
        ForeignPackageError(PackageExportErrorCode.PDF_FAILED, "package.test"),
    ):
        failure = sanitize_ui_failure(error)
        assert failure.code == "PWS_UI_UNEXPECTED"
        assert private not in failure.message
    with _package_dialog() as dialog:
        dialog.show_failure(private, phase="export", code=private)
        text = _widget(dialog, QLabel, "packageExportStatus").text()
        assert text.startswith("Package export did not finish safely.")
        assert private not in text
