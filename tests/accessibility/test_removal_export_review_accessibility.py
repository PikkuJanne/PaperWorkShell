"""Keyboard, labels and enlarged-text review remain available before export."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QScrollArea

from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.ui.removal_export_review import RemovalExportReviewDialog
from tests.accessibility.test_redaction_export_accessibility import _dialog, _report
from tests.unit.test_package_export_window import _application
from tests.unit.test_removal_export_desktop import _backend


def test_removal_review_labels_keyboard_consent_and_enlarged_text() -> None:
    app = _application()
    backend, _, _ = _backend(signed=True)
    dialog = RemovalExportReviewDialog(backend.begin_removal_export())
    font = dialog.font()
    font.setPointSizeF(max(font.pointSizeF(), 10) * 2)
    dialog.setFont(font)
    dialog.resize(480, 360)
    dialog.show()
    app.processEvents()
    try:
        review = dialog.findChild(QCheckBox, "removalExportReviewedCheckBox")
        signature = dialog.findChild(QCheckBox, "removalExportUnsignedCheckBox")
        choose = dialog.findChild(QPushButton, "removalExportChooseFileButton")
        cancel = dialog.findChild(QPushButton, "removalExportCancelButton")
        warning = dialog.findChild(QLabel, "removalExportReviewWarning")
        scroll = dialog.findChild(QScrollArea, "removalExportReviewScrollArea")
        assert review is not None and signature is not None and choose is not None
        assert cancel is not None and warning is not None and scroll is not None
        assert all(control.accessibleName() for control in (review, signature, choose, cancel))
        assert review.accessibleDescription() and signature.accessibleDescription()
        assert warning.wordWrap() and scroll.widgetResizable()
        assert "accessibility structure may be lost" in warning.text()
        assert cancel.isDefault() and not choose.isEnabled()
        scroll.ensureWidgetVisible(review)
        review.setFocus()
        QTest.keyClick(review, Qt.Key.Key_Space)
        assert review.isChecked() and not choose.isEnabled()
        QTest.keyClick(review, Qt.Key.Key_Tab)
        assert signature.hasFocus()
        QTest.keyClick(signature, Qt.Key.Key_Space)
        assert signature.isChecked() and choose.isEnabled()
        QTest.keyClick(signature, Qt.Key.Key_Tab)
        assert choose.hasFocus()
        assert dialog.rect().contains(choose.geometry())
        assert dialog.rect().contains(cancel.geometry())
        assert review.width() >= review.sizeHint().width()
        assert signature.width() >= signature.sizeHint().width()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("code", "status"),
    (
        (RedactionExportCode.COMPLETE, RedactionExportStatus.COMMITTED),
        (RedactionExportCode.RELEASE_PENDING, RedactionExportStatus.BLOCKED),
        (RedactionExportCode.VERIFICATION_UNKNOWN, RedactionExportStatus.BLOCKED),
        (RedactionExportCode.CANCELLED, RedactionExportStatus.NOT_COMMITTED),
        (RedactionExportCode.DESTINATION_UNCERTAIN, RedactionExportStatus.UNCERTAIN),
    ),
)
def test_removal_result_dialog_description_exposes_actual_status(
    code: RedactionExportCode, status: RedactionExportStatus
) -> None:
    if status is RedactionExportStatus.UNCERTAIN:
        complete = _report(RedactionExportCode.COMPLETE, RedactionExportStatus.COMMITTED)
        report = RedactionExportReport(
            complete.case_id,
            complete.manifest_revision,
            complete.affected_page_ids,
            complete.verification,
            status,
            code,
        )
    else:
        report = _report(code, status)
    with _dialog(report) as dialog:
        prefix = report.status_label + ". " + report.safe_message + " "
        assert dialog.accessibleDescription().startswith(prefix)
        interface = QAccessible.queryAccessibleInterface(dialog)
        assert interface is not None
        assert interface.text(QAccessible.Text.Description).startswith(prefix)
        assert "Review affected internal page IDs" in dialog.accessibleDescription()
        assert "a" * 64 not in dialog.accessibleDescription()
        assert "b" * 64 not in dialog.accessibleDescription()
        assert dialog.report is report
