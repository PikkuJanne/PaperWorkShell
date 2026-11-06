"""PWS-0705 keyboard, accessible role, focus, and enlarged-text behavior."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.ui.assisted_use import AssistedConsentDialog, AssistedLockScreen


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


def test_controls_have_names_roles_descriptions_and_deliberate_keyboard_consent() -> None:
    with _dialog() as dialog:
        cancel = dialog.findChild(QPushButton, "assistedConsentCancel")
        assert cancel is not None and cancel.hasFocus()
        for kind, name in (
            (QComboBox, "assistedRetentionDays"),
            (QLineEdit, "assistedPassphrase"),
            (QLineEdit, "assistedPassphraseConfirmation"),
            (QCheckBox, "assistedConsentConfirmation"),
            (QPushButton, "assistedConsentStart"),
            (QPushButton, "assistedConsentCancel"),
        ):
            widget = dialog.findChild(kind, name)
            assert widget is not None
            assert widget.accessibleName()
            assert widget.accessibleDescription()
        consent = dialog.findChild(QCheckBox, "assistedConsentConfirmation")
        assert consent is not None
        interface = QAccessible.queryAccessibleInterface(consent)
        assert interface is not None
        assert interface.role() == QAccessible.Role.CheckBox
        assert not interface.state().checked
        consent.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(consent, Qt.Key.Key_Space)
        assert consent.hasFocus() and consent.isChecked()
        assert interface.state().checked


def test_password_fields_expose_password_state_and_preserve_logical_tab_order() -> None:
    with _dialog() as dialog:
        retention = dialog.findChild(QComboBox, "assistedRetentionDays")
        first = dialog.findChild(QLineEdit, "assistedPassphrase")
        repeated = dialog.findChild(QLineEdit, "assistedPassphraseConfirmation")
        consent = dialog.findChild(QCheckBox, "assistedConsentConfirmation")
        assert retention is not None and first is not None
        assert repeated is not None and consent is not None
        for entry in (first, repeated):
            interface = QAccessible.queryAccessibleInterface(entry)
            assert interface is not None
            assert interface.state().passwordEdit
        retention.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(retention, Qt.Key.Key_Tab)
        assert first.hasFocus()
        QTest.keyClick(first, Qt.Key.Key_Tab)
        assert repeated.hasFocus()
        QTest.keyClick(repeated, Qt.Key.Key_Tab)
        assert consent.hasFocus()


def test_enlarged_text_keeps_policy_scrollable_and_actions_reachable() -> None:
    with _dialog() as dialog:
        font = QFont(dialog.font())
        font.setPointSize(max(24, font.pointSize() * 2))
        dialog.setFont(font)
        dialog.resize(420, 320)
        _application().processEvents()
        scroll = dialog.findChild(QScrollArea, "assistedConsentScroll")
        assert scroll is not None
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(scroll, Qt.Key.Key_PageDown)
        _application().processEvents()
        assert scroll.verticalScrollBar().value() > 0
        for name in ("assistedConsentStart", "assistedConsentCancel"):
            button = dialog.findChild(QPushButton, name)
            assert button is not None and button.isVisible()
            assert dialog.rect().contains(button.mapTo(dialog, QPoint(0, 0)))
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))


@pytest.mark.parametrize("enlarged", (False, True))
def test_consent_field_labels_remain_readable_and_scroll_into_view(enlarged: bool) -> None:
    application = _application()
    original_font = QFont(application.font())
    tested_font = QFont(original_font)
    if enlarged:
        tested_font.setPointSize(max(24, original_font.pointSize() * 2))
        application.setFont(tested_font)
    try:
        with _dialog() as dialog:
            if enlarged:
                dialog.resize(420, 420)
                application.processEvents()
            scroll = dialog.findChild(QScrollArea, "assistedConsentScroll")
            assert scroll is not None
            for text, control_name in (
                ("Case expires after", "assistedRetentionDays"),
                ("Local case passphrase", "assistedPassphrase"),
                ("Repeat passphrase", "assistedPassphraseConfirmation"),
            ):
                label = next(label for label in dialog.findChildren(QLabel) if label.text() == text)
                control = dialog.findChild(QWidget, control_name)
                assert control is not None and label.buddy() is control
                assert label.font().pointSizeF() >= tested_font.pointSizeF()
                assert label.width() >= min(
                    label.fontMetrics().horizontalAdvance(text), control.width()
                )
                assert label.height() >= label.heightForWidth(label.width()) > 0
                scroll.ensureWidgetVisible(label)
                application.processEvents()
                assert (
                    scroll.viewport().rect().contains(label.mapTo(scroll.viewport(), QPoint(0, 0)))
                )
                assert (
                    scroll.viewport()
                    .rect()
                    .contains(label.mapTo(scroll.viewport(), label.rect().bottomRight()))
                )
    finally:
        application.setFont(original_font)


def test_locked_surface_has_no_content_and_expiry_preserves_keyboard_recovery() -> None:
    application = _application()
    screen = AssistedLockScreen()
    screen.show()
    application.processEvents()
    unlocks: list[bool] = []
    endings: list[bool] = []
    screen.unlock_requested.connect(lambda: unlocks.append(True))
    screen.end_requested.connect(lambda: endings.append(True))
    try:
        assert screen.accessibleName() == "Assisted case locked"
        assert screen.findChildren(QLineEdit) == []
        unlock = screen.findChild(QPushButton, "assistedLockUnlock")
        end = screen.findChild(QPushButton, "assistedLockEnd")
        assert unlock is not None and end is not None
        screen.focus_recovery()
        assert unlock.hasFocus()
        QTest.keyClick(unlock, Qt.Key.Key_Space)
        assert unlocks == [True]
        screen.set_expired(True)
        screen.focus_recovery()
        assert not unlock.isEnabled()
        assert end.hasFocus()
        assert screen.accessibleName() == "Assisted case expired"
        QTest.keyClick(end, Qt.Key.Key_Space)
        assert endings == [True]
        assert unlocks == [True]
    finally:
        screen.close()
        screen.deleteLater()
        application.processEvents()


def test_clock_review_is_visible_accessible_and_cannot_override_expiry() -> None:
    application = _application()
    screen = AssistedLockScreen()
    screen.show()
    application.processEvents()
    try:
        screen.set_clock_review(True)
        guidance = screen.findChild(QLabel, "assistedLockGuidance")
        unlock = screen.findChild(QPushButton, "assistedLockUnlock")
        assert guidance is not None and unlock is not None
        assert "clock review" in screen.accessibleName()
        assert "Correct the clock" in guidance.text()
        assert "Correct the computer clock" in screen.accessibleDescription()
        assert unlock.isEnabled()
        screen.set_expired(True)
        screen.set_clock_review(True)
        assert screen.accessibleName() == "Assisted case expired"
        assert not unlock.isEnabled()
    finally:
        screen.close()
        screen.deleteLater()
        application.processEvents()


def test_finishing_unlock_keeps_end_assistance_available_and_respects_expiry() -> None:
    application = _application()
    screen = AssistedLockScreen()
    screen.show()
    application.processEvents()
    try:
        unlock = screen.findChild(QPushButton, "assistedLockUnlock")
        end = screen.findChild(QPushButton, "assistedLockEnd")
        guidance = screen.findChild(QLabel, "assistedLockGuidance")
        assert unlock is not None and end is not None and guidance is not None
        screen.set_unlock_pending(True)
        assert not unlock.isEnabled() and end.isEnabled()
        assert "An unlock is finishing" in guidance.text()
        assert "Case content stays hidden" in screen.accessibleDescription()
        screen.focus_recovery()
        assert end.hasFocus()
        screen.set_unlock_pending(False)
        assert unlock.isEnabled()
        screen.set_expired(True)
        screen.set_unlock_pending(True)
        screen.set_unlock_pending(False)
        assert not unlock.isEnabled()
        assert "expired" in screen.accessibleName()
    finally:
        screen.close()
        screen.deleteLater()
        application.processEvents()
