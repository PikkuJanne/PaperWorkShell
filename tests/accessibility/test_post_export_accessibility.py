"""Keyboard consent, plain-text inventory, and scalable destructive review."""

from __future__ import annotations

import os
from typing import cast
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
)

from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.domain.post_export_deletion import (
    PostExportCaseInventory,
    PostExportDeletionInventory,
)
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.post_export import PostExportDeletionDialog


def _application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def _review() -> PostExportDeletionInventory:
    return PostExportDeletionInventory(
        uuid4(),
        uuid4(),
        3,
        PostExportCaseInventory(2, 4, 1, 1, 400, 100, 0, 10),
        1,
        ("synthetic-application.pdf",),
    )


@pytest.mark.parametrize("status", list(PackageSummaryStatus))
def test_post_export_actions_require_complete_publication_and_new_text_consent(
    status: PackageSummaryStatus,
) -> None:
    app = _application()
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    try:
        if status in {PackageSummaryStatus.PLANNED, PackageSummaryStatus.NEEDS_REVIEW}:
            dialog.show_review(PackageSummary(status), export_allowed=False)
        else:
            summary = (
                PackageSummary(
                    status,
                    outputs=(
                        PackageSummaryOutput(
                            "synthetic.pdf", PackageSummaryOutputState.COMMITTED, 1, 200
                        ),
                    ),
                    active_page_count=1,
                )
                if status is PackageSummaryStatus.COMMITTED
                else PackageSummary(status)
            )
            dialog.show_result(summary)
        dialog.show()
        app.processEvents()
        companion = dialog.findChild(QPushButton, "packageTextCompanionButton")
        deletion = dialog.findChild(QPushButton, "packageDeleteCaseButton")
        privacy = dialog.findChild(QCheckBox, "packageTextPrivacyCheck")
        assert companion is not None and deletion is not None and privacy is not None
        committed = status is PackageSummaryStatus.COMMITTED
        assert deletion.isVisible() is committed
        assert companion.isVisible() is committed
        assert not companion.isEnabled()
        if committed:
            emitted: list[bool] = []
            dialog.text_companion_requested.connect(lambda: emitted.append(True))
            privacy.setFocus()
            QTest.keyClick(privacy, Qt.Key.Key_Space)
            assert companion.isEnabled()
            QTest.keyClick(privacy, Qt.Key.Key_Tab)
            assert companion.hasFocus()
            QTest.keyClick(companion, Qt.Key.Key_Space)
            assert emitted == [True]
            assert not deletion.isEnabled()
            dialog.complete_post_export("Synthetic result")
            dialog.clear_post_export_consent()
            assert not companion.isEnabled()
            dialog.disable_post_export()
            assert not deletion.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_deletion_default_enter_keeps_case_and_inventory_is_accessible_plain_text() -> None:
    app = _application()
    dialog = PostExportDeletionDialog(_review())
    dialog.show()
    app.processEvents()
    try:
        keep = dialog.findChild(QPushButton, "postExportKeepCaseButton")
        remove = dialog.findChild(QPushButton, "postExportDeleteConfirmButton")
        inventory = dialog.findChild(QPlainTextEdit)
        assert keep is not None and remove is not None and inventory is not None
        assert dialog.focusWidget() is keep
        assert keep.isDefault() and not remove.isDefault()
        assert not dialog.confirmed
        assert not remove.isEnabled()
        assert inventory.isReadOnly() and inventory.tabChangesFocus()
        assert "synthetic-application.pdf" in inventory.toPlainText()
        assert "forensic erasure" in inventory.toPlainText()
        for widget in (keep, remove, inventory):
            interface = QAccessible.queryAccessibleInterface(widget)
            assert interface is not None
            assert interface.text(QAccessible.Text.Name)
        QTest.keyClick(keep, Qt.Key.Key_Return)
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_delete_requires_keyboard_acknowledgement_and_explicit_final_action() -> None:
    app = _application()
    dialog = PostExportDeletionDialog(_review())
    dialog.show()
    app.processEvents()
    try:
        ack = dialog.findChild(QCheckBox)
        remove = dialog.findChild(QPushButton, "postExportDeleteConfirmButton")
        assert ack is not None and remove is not None
        ack.setFocus()
        QTest.keyClick(ack, Qt.Key.Key_Space)
        assert dialog.confirmed and remove.isEnabled()
        assert dialog.isVisible()
        QTest.keyClick(ack, Qt.Key.Key_Tab)
        assert remove.hasFocus()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_enlarged_inventory_and_confirmation_are_reachable_in_small_window() -> None:
    app = _application()
    original = QFont(app.font())
    enlarged = QFont(original)
    enlarged.setPointSizeF(max(24, original.pointSizeF() * 2))
    app.setFont(enlarged)
    dialog = PostExportDeletionDialog(_review())
    try:
        dialog.resize(520, 360)
        dialog.show()
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        assert scroll is not None
        remove = dialog.findChild(QPushButton, "postExportDeleteConfirmButton")
        assert remove is not None
        scroll.ensureWidgetVisible(remove)
        app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert (
            scroll.viewport()
            .rect()
            .contains(remove.mapTo(scroll.viewport(), remove.rect().center()))
        )
    finally:
        dialog.deleteLater()
        app.setFont(original)
        app.processEvents()
