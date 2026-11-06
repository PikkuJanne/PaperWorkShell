"""Native keyboard, status and scalable removal-export result recovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QWidget

from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationReport,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.ui.redaction_export import RedactionExportDialog


def _app() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _report(
    code: RedactionExportCode = RedactionExportCode.VERIFICATION_UNKNOWN,
    status: RedactionExportStatus = RedactionExportStatus.BLOCKED,
) -> RedactionExportReport:
    evidence = None
    if code in {RedactionExportCode.COMPLETE, RedactionExportCode.RELEASE_PENDING}:
        evidence = RedactionVerificationReport(
            "a" * 64,
            "b" * 64,
            tuple(
                VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)
                for method in VerificationMethod
            ),
        )
    return RedactionExportReport(
        UUID("a0000000-0000-4000-8000-000000000001"),
        4,
        (UUID("a0000000-0000-4000-8000-000000000002"),),
        evidence,
        status,
        code,
    )


@contextmanager
def _dialog(report: RedactionExportReport | None = None) -> Iterator[RedactionExportDialog]:
    app = _app()
    dialog = RedactionExportDialog(report or _report())
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: RedactionExportDialog, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def test_native_roles_names_descriptions_plain_text_and_unknown_status() -> None:
    with _dialog() as dialog:
        for kind, name, role in (
            (QLabel, "redactionExportStatus", QAccessible.Role.StaticText),
            (QPlainTextEdit, "redactionExportReportText", QAccessible.Role.EditableText),
            (QPushButton, "redactionExportRetryButton", QAccessible.Role.Button),
            (QPushButton, "redactionExportReviewButton", QAccessible.Role.Button),
            (QPushButton, "redactionExportCloseButton", QAccessible.Role.Button),
        ):
            widget = _widget(dialog, kind, name)
            assert widget.accessibleName() and widget.accessibleDescription()
            interface = QAccessible.queryAccessibleInterface(widget)
            assert interface is not None and interface.role() is role
        details = _widget(dialog, QPlainTextEdit, "redactionExportReportText")
        assert details.isReadOnly() and details.tabChangesFocus()
        assert details.toPlainText().count("unknown (RV_METHOD_MISSING)") == 7
        assert "Saved marks remain unapplied" in details.toPlainText()
        assert "redacted" not in details.toPlainText().lower()
        status = _widget(dialog, QLabel, "redactionExportStatus")
        assert status.textFormat() is Qt.TextFormat.PlainText
        assert "Export blocked" in status.text()
        assert "QLabel:focus" in status.styleSheet()
        assert not dialog.findChildren(QPushButton, "redactionExportOverrideButton")


def test_keyboard_retry_review_and_escape_do_not_change_report_or_publish() -> None:
    with _dialog() as dialog:
        original = dialog.report
        events: list[str] = []
        dialog.retry_requested.connect(lambda: events.append("retry"))
        dialog.review_marks_requested.connect(lambda: events.append("review"))
        retry = _widget(dialog, QPushButton, "redactionExportRetryButton")
        assert QApplication.focusWidget() is retry
        QTest.keyClick(retry, Qt.Key.Key_Space)
        assert events == ["retry"] and dialog.report is original
        QTest.keyClick(retry, Qt.Key.Key_Tab)
        review = _widget(dialog, QPushButton, "redactionExportReviewButton")
        assert QApplication.focusWidget() is review
        QTest.keyClick(review, Qt.Key.Key_Space)
        assert events == ["retry", "review"] and dialog.report is original
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == dialog.DialogCode.Rejected
        assert events == ["retry", "review"]


@pytest.mark.parametrize(
    "code,status,focus",
    [
        (RedactionExportCode.STALE_INTENT, RedactionExportStatus.BLOCKED, "Review"),
        (RedactionExportCode.RELEASE_PENDING, RedactionExportStatus.BLOCKED, "Review"),
        (RedactionExportCode.COMPLETE, RedactionExportStatus.COMMITTED, "Close"),
    ],
)
def test_recovery_focus_respects_result_without_retrying_stale_or_release_pending(
    code: RedactionExportCode, status: RedactionExportStatus, focus: str
) -> None:
    with _dialog(_report(code, status)) as dialog:
        assert not _widget(dialog, QPushButton, "redactionExportRetryButton").isEnabled()
        expected = _widget(dialog, QPushButton, f"redactionExport{focus}Button")
        assert QApplication.focusWidget() is expected
        events: list[str] = []
        dialog.retry_requested.connect(lambda: events.append("retry"))
        _widget(dialog, QPushButton, "redactionExportRetryButton").click()
        assert events == []


def test_doubled_text_and_small_dialog_keep_recovery_controls_visible() -> None:
    with _dialog() as dialog:
        font = dialog.font()
        font.setPointSizeF(font.pointSizeF() * 2)
        dialog.setFont(font)
        dialog.resize(460, 420)
        _app().processEvents()
        for name in ("Retry", "Review", "Close"):
            button = _widget(dialog, QPushButton, f"redactionExport{name}Button")
            assert button.isVisible()
            assert dialog.rect().contains(button.geometry())
        details = _widget(dialog, QPlainTextEdit, "redactionExportReportText")
        assert details.lineWrapMode() is QPlainTextEdit.LineWrapMode.WidgetWidth


def test_show_announces_actual_status_and_recovery_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append(event.message())

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    with _dialog(_report(RedactionExportCode.RELEASE_PENDING)) as dialog:
        assert messages == [_widget(dialog, QLabel, "redactionExportStatus").text()]
        assert "independent security approval" in messages[0]
        assert "a" * 64 not in messages[0] and "b" * 64 not in messages[0]
