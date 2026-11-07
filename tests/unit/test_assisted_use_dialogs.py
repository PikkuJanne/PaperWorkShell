"""PWS-0705 deliberate consent and secret-lifetime presentation checks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
)

from paperworkshell.domain.assisted_use import AssistedUseErrorCode
from paperworkshell.ui.assisted_use import (
    AssistedConsentDialog,
    AssistedHandoverDialog,
    AssistedUnlockDialog,
)


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog() -> Iterator[AssistedConsentDialog]:
    application = _application()
    dialog = AssistedConsentDialog()
    dialog.show()
    application.processEvents()
    try:
        yield dialog
    finally:
        dialog.reject()
        dialog.deleteLater()
        application.processEvents()


def _entry(dialog: QDialog, name: str) -> QLineEdit:
    edit = dialog.findChild(QLineEdit, name)
    assert edit is not None
    return edit


def _agree(dialog: AssistedConsentDialog) -> QCheckBox:
    consent = dialog.findChild(QCheckBox, "assistedConsentConfirmation")
    assert consent is not None
    consent.setChecked(True)
    return consent


def test_consent_is_not_implied_by_enter_or_api_acceptance() -> None:
    with _dialog() as dialog:
        assert dialog.retention_days == 7
        assert not dialog.consent_confirmed
        first = _entry(dialog, "assistedPassphrase")
        repeated = _entry(dialog, "assistedPassphraseConfirmation")
        first.setText("synthetic case phrase")
        repeated.setText("synthetic case phrase")
        dialog.accept()
        assert dialog.isVisible()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.take_passphrase() == ""
        consent = dialog.findChild(QCheckBox, "assistedConsentConfirmation")
        assert consent is not None and consent.hasFocus()


def test_consent_requires_sufficient_matching_passphrase_and_shows_safe_errors() -> None:
    with _dialog() as dialog:
        _agree(dialog)
        _entry(dialog, "assistedCaseName").setText("Application support")
        first = _entry(dialog, "assistedPassphrase")
        repeated = _entry(dialog, "assistedPassphraseConfirmation")
        first.setText("short")
        repeated.setText("short")
        dialog.accept()
        assert dialog.isVisible()
        assert first.hasFocus()
        first.setText("synthetic case phrase")
        repeated.setText("different phrase")
        dialog.accept()
        assert repeated.hasFocus()
        error = dialog.findChild(QLabel, "assistedConsentError")
        assert error is not None
        assert "must match" in error.text()
        assert "synthetic" not in error.text()
        assert "different" not in error.text()
        repeated.setText("synthetic case phrase")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.take_passphrase() == "synthetic case phrase"
        assert first.text() == repeated.text() == ""
        assert dialog.take_passphrase() == ""


def test_only_closed_retention_choices_are_available() -> None:
    with _dialog() as dialog:
        retention = dialog.findChild(QComboBox, "assistedRetentionDays")
        assert retention is not None
        assert not retention.isEditable()
        assert retention.count() == 3
        assert [retention.itemData(index) for index in range(3)] == [1, 7, 30]
        for index, days in enumerate((1, 7, 30)):
            retention.setCurrentIndex(index)
            assert dialog.retention_days == days


def test_assisted_creation_requires_a_nonblank_case_name_in_the_consent_dialog() -> None:
    with _dialog() as dialog:
        name = _entry(dialog, "assistedCaseName")
        assert name.accessibleName() == "Case name"
        assert "case chooser" in name.accessibleDescription()
        assert name.text() == ""
        _agree(dialog)
        _entry(dialog, "assistedPassphrase").setText("synthetic case phrase")
        _entry(dialog, "assistedPassphraseConfirmation").setText("synthetic case phrase")
        for empty_name in ("", "   "):
            name.setText(empty_name)
            dialog.accept()
            assert dialog.result() == QDialog.DialogCode.Rejected
            assert name.hasFocus()
        name.setText("  Äänekoski <support>  ")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.case_name == "Äänekoski <support>"


def test_escape_and_close_clear_password_widgets_without_consent() -> None:
    with _dialog() as dialog:
        first = _entry(dialog, "assistedPassphrase")
        repeated = _entry(dialog, "assistedPassphraseConfirmation")
        first.setText("synthetic case phrase")
        repeated.setText("synthetic case phrase")
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
        assert first.text() == repeated.text() == ""
        assert dialog.take_passphrase() == ""
    with _dialog() as dialog:
        first = _entry(dialog, "assistedPassphrase")
        first.setText("synthetic case phrase")
        assert dialog.close()
        assert first.text() == ""


def test_unlock_failure_is_fixed_and_cancel_clears_the_secret() -> None:
    application = _application()
    dialog = AssistedUnlockDialog()
    dialog.show()
    application.processEvents()
    try:
        first = _entry(dialog, "assistedUnlockPassphrase")
        assert first.hasFocus()
        assert first.echoMode() == QLineEdit.EchoMode.Password
        first.setText("synthetic case phrase")
        dialog.show_failure()
        assert first.text() == ""
        assert first.hasFocus()
        error = dialog.findChild(QLabel, "assistedUnlockError")
        assert error is not None and "could not be unlocked" in error.text()
        first.setText("synthetic case phrase")
        dialog.accept()
        assert dialog.take_passphrase() == "synthetic case phrase"
        assert first.text() == ""
        dialog.show()
        first.setText("synthetic case phrase")
        dialog.reject()
        assert first.text() == ""
        assert dialog.take_passphrase() == ""
    finally:
        dialog.reject()
        dialog.deleteLater()
        application.processEvents()


@pytest.mark.parametrize(
    "code,expected",
    (
        (AssistedUseErrorCode.CLOCK_REVIEW, "Correct the clock"),
        (AssistedUseErrorCode.UNLOCK_WAIT, "Wait 30 seconds"),
        (AssistedUseErrorCode.STORAGE_FAILED, "saved or read safely"),
        (AssistedUseErrorCode.EXPIRED, "case has expired"),
        (AssistedUseErrorCode.UNLOCK_FAILED, "Check the passphrase"),
    ),
)
def test_unlock_failure_explains_closed_clock_wait_and_storage_recovery(
    code: AssistedUseErrorCode, expected: str
) -> None:
    application = _application()
    dialog = AssistedUnlockDialog()
    dialog.show()
    application.processEvents()
    try:
        secret = _entry(dialog, "assistedUnlockPassphrase")
        secret.setText("synthetic case phrase")
        dialog.show_failure(code)
        error = dialog.findChild(QLabel, "assistedUnlockError")
        assert error is not None
        assert expected in error.text()
        assert "synthetic" not in error.text()
        assert secret.text() == ""
        assert secret.hasFocus()
    finally:
        dialog.reject()
        dialog.deleteLater()
        application.processEvents()


def test_handover_is_only_case_reference_expiry_and_plain_policy_guidance() -> None:
    application = _application()
    case_id = UUID("00000000-0000-4000-8000-000000000705")
    dialog = AssistedHandoverDialog(case_id, datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    try:
        text = dialog.summary_text
        assert str(case_id) in text
        assert "2026-11-09 12:00 UTC" in text
        assert "passphrase" in text and "cannot be recovered" in text
        assert "does not encrypt" in text
        assert "Exports are separate files" in text
        assert ":\\" not in text and "file://" not in text
        assert "synthetic case phrase" not in text
    finally:
        dialog.deleteLater()
        application.processEvents()


def test_pending_unlock_disables_duplicate_submission_and_retains_keyboard_cancel() -> None:
    application = _application()
    dialog = AssistedUnlockDialog()
    dialog.show()
    application.processEvents()
    try:
        password = _entry(dialog, "assistedUnlockPassphrase")
        submit = dialog.findChild(QPushButton, "assistedUnlockSubmit")
        cancel = dialog.findChild(QPushButton, "assistedUnlockCancel")
        assert submit is not None and cancel is not None
        password.setText("synthetic local case passphrase")
        dialog.set_pending(True)
        assert dialog.isEnabled()
        assert password.text() == ""
        assert not password.isEnabled() and not submit.isEnabled()
        assert cancel.isEnabled() and cancel.hasFocus()
        dialog.accept()
        assert dialog.isVisible()
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        assert not dialog.isVisible()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.take_passphrase() == ""
    finally:
        dialog.reject()
        dialog.deleteLater()
        application.processEvents()
