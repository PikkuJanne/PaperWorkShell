"""Offline update review is explicit, keyboard operable and scalable."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from paperworkshell.domain.update_verification import UpdateMetadata
from paperworkshell.storage.update_installation import (
    InstallationStatus,
    UpdateInstallationError,
    UpdateOffer,
)
from paperworkshell.ui.update_dialog import UpdateDialog


class _Installation:
    def __init__(self, *, trust_available: bool = True) -> None:
        self.trust_available = trust_available
        self.active = "original-build"
        self.can_restore = False
        self.has_staged = False
        self.calls: list[str] = []
        self._ui_thread = get_ident()
        self.wait_to_cancel = False
        self.inspection_error: Exception | None = None
        self.install_started = Event()

    def status(self) -> InstallationStatus:
        assert get_ident() != self._ui_thread
        return InstallationStatus(
            active_version="1.0.0" if self.active == "original-build" else "1.1.0",
            active_candidate=self.active,
            can_restore=self.can_restore,
            has_staged_updates=self.has_staged,
            trust_available=self.trust_available,
        )

    def inspect_offer(self, directory: Path) -> UpdateOffer:
        assert get_ident() != self._ui_thread
        assert directory.name == "synthetic-update"
        self.calls.append("inspect")
        if self.inspection_error is not None:
            raise self.inspection_error
        return UpdateOffer(
            UpdateMetadata(
                channel="engineering",
                version="1.1.0",
                candidate_id="verified-build",
                sequence=2,
                issued_at=datetime(2026, 11, 26, 11, tzinfo=UTC),
                expires_at=datetime(2026, 11, 26, 13, tzinfo=UTC),
                package_size=1,
                package_sha256="a" * 64,
            ),
            directory,
            "b" * 64,
        )

    def apply_offer(self, offer: UpdateOffer, *, cancelled: Callable[[], bool]) -> None:
        assert get_ident() != self._ui_thread
        assert offer.metadata.candidate_id == "verified-build"
        self.calls.append("install")
        self.install_started.set()
        if self.wait_to_cancel:
            wait = Event()
            deadline = monotonic() + 5
            while not cancelled() and monotonic() < deadline:
                wait.wait(0.01)
            assert cancelled()
            raise UpdateInstallationError("UPDATE_CANCELLED", "PaperWorkShell update cancelled.")
        self.active = "verified-build"
        self.has_staged = self.can_restore = True

    def restore_previous(self) -> None:
        assert get_ident() != self._ui_thread
        self.calls.append("restore")
        self.active = "original-build"
        self.can_restore = False

    def remove_installed_updates(self) -> None:
        assert get_ident() != self._ui_thread
        self.calls.append("remove")
        self.active = "original-build"
        self.has_staged = self.can_restore = False


def _app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast("QApplication", current)


def _until(app: QApplication, ready: Callable[[], bool]) -> None:
    deadline = monotonic() + 10
    while not ready() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(1)
    assert ready()
    app.processEvents()


def _idle(app: QApplication, dialog: UpdateDialog) -> None:
    _until(app, lambda: dialog._thread is None)


def _review_offer(
    app: QApplication, dialog: UpdateDialog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args: str(tmp_path / "synthetic-update"),
    )
    choose = dialog.findChild(QPushButton, "updateChooseFolder")
    assert choose is not None and choose.isEnabled()
    choose.setFocus()
    QTest.keyClick(choose, Qt.Key.Key_Space)
    _idle(app, dialog)


def test_keyboard_review_requires_confirmation_and_a_separate_install_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _app()
    installation = _Installation()
    dialog = UpdateDialog(installation)
    dialog.show()
    try:
        _idle(app, dialog)
        close = dialog.findChild(QPushButton, "updateClose")
        install = dialog.findChild(QPushButton, "updateInstall")
        assert close is not None and close.hasFocus() and close.isDefault()
        assert install is not None and not install.isEnabled() and not install.isDefault()
        _review_offer(app, dialog, tmp_path, monkeypatch)
        review = dialog.findChild(QPlainTextEdit, "updateReview")
        confirm = dialog.findChild(QCheckBox, "updateReviewConfirmation")
        assert review is not None and review.isReadOnly() and review.hasFocus()
        text = review.toPlainText()
        assert all(word in text for word in ("1.0.0", "1.1.0", "verified-build", "engineering"))
        assert confirm is not None and confirm.isEnabled() and not confirm.isChecked()
        assert not install.isEnabled() and installation.calls == ["inspect"]
        QTest.keyClick(review, Qt.Key.Key_Tab)
        assert confirm.hasFocus()
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        QTest.keyClick(confirm, Qt.Key.Key_Tab)
        assert install.hasFocus() and installation.calls == ["inspect"]
        QTest.keyClick(install, Qt.Key.Key_Space)
        _idle(app, dialog)
        assert installation.calls == ["inspect", "install"]
        assert "Current version: 1.1.0" in review.toPlainText()
        assert not confirm.isChecked() and not install.isEnabled()
    finally:
        _idle(app, dialog)
        dialog.deleteLater()
        app.processEvents()


def test_escape_requests_cancellation_without_closing_during_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _app()
    installation = _Installation()
    installation.wait_to_cancel = True
    dialog = UpdateDialog(installation)
    dialog.show()
    try:
        _idle(app, dialog)
        _review_offer(app, dialog, tmp_path, monkeypatch)
        confirm = dialog.findChild(QCheckBox, "updateReviewConfirmation")
        install = dialog.findChild(QPushButton, "updateInstall")
        close = dialog.findChild(QPushButton, "updateClose")
        activity = dialog.findChild(QLabel, "updateActivity")
        assert confirm is not None and install is not None and close is not None
        confirm.setChecked(True)
        install.click()
        _until(app, installation.install_started.is_set)
        assert close.isEnabled() and "Cancel" in close.accessibleName()
        QTest.keyClick(close, Qt.Key.Key_Escape)
        _idle(app, dialog)
        assert activity is not None and "cancelled" in activity.text()
        assert dialog.isVisible() and installation.active == "original-build"
        assert not install.isEnabled() and close.hasFocus()
    finally:
        dialog.reject()
        _idle(app, dialog)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("scale", [1, 2])
def test_recovery_controls_remain_accessible_when_update_trust_is_unavailable(scale: int) -> None:
    app = _app()
    installation = _Installation(trust_available=False)
    installation.can_restore = installation.has_staged = True
    dialog = UpdateDialog(installation)
    font = QFont(dialog.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    dialog.setFont(font)
    dialog.show()
    try:
        _idle(app, dialog)
        for control in dialog.findChildren(QPushButton) + dialog.findChildren(QCheckBox):
            interface = QAccessible.queryAccessibleInterface(control)
            assert interface is not None and interface.text(QAccessible.Text.Name)
            assert control.focusPolicy() != Qt.FocusPolicy.NoFocus
        choose = dialog.findChild(QPushButton, "updateChooseFolder")
        restore = dialog.findChild(QPushButton, "updateRestore")
        remove = dialog.findChild(QPushButton, "updateRemove")
        close = dialog.findChild(QPushButton, "updateClose")
        assert choose is not None and not choose.isEnabled()
        assert restore is not None and restore.isEnabled() and not restore.isDefault()
        assert remove is not None and remove.isEnabled() and not remove.isDefault()
        assert close is not None and dialog.rect().contains(close.geometry())
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("button_name", "action"), [("updateRestore", "restore"), ("updateRemove", "remove")]
)
def test_recovery_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch, button_name: str, action: str
) -> None:
    app = _app()
    installation = _Installation()
    installation.can_restore = installation.has_staged = True
    dialog = UpdateDialog(installation)
    dialog.show()
    accepted = False

    def confirm(*args: object) -> QMessageBox.StandardButton:
        assert args[-1] == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", confirm)
    try:
        _idle(app, dialog)
        button = dialog.findChild(QPushButton, button_name)
        assert button is not None and button.isEnabled()
        button.click()
        assert installation.calls == []
        accepted = True
        button.click()
        _idle(app, dialog)
        assert installation.calls == [action]
    finally:
        _idle(app, dialog)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ("error", "feedback"),
    [
        (OSError("private-source-path-canary"), "could not finish safely"),
        (
            UpdateInstallationError("UPDATE_ROLLBACK_FORBIDDEN", "private-source-path-canary"),
            "already been installed or superseded. Choose a newer update.",
        ),
    ],
)
def test_failed_review_keeps_install_disabled_and_does_not_echo_private_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, feedback: str
) -> None:
    app = _app()
    installation = _Installation()
    installation.inspection_error = error
    dialog = UpdateDialog(installation)
    dialog.show()
    try:
        _idle(app, dialog)
        _review_offer(app, dialog, tmp_path, monkeypatch)
        install = dialog.findChild(QPushButton, "updateInstall")
        choose = dialog.findChild(QPushButton, "updateChooseFolder")
        activity = dialog.findChild(QLabel, "updateActivity")
        assert install is not None and not install.isEnabled()
        assert choose is not None and choose.hasFocus()
        assert activity is not None and feedback in activity.text()
        assert "private-source-path-canary" not in activity.text()
    finally:
        _idle(app, dialog)
        dialog.deleteLater()
        app.processEvents()
