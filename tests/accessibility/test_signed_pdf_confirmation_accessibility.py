from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationReview,
    SignedSourceModificationSource,
    SignedSourceReviewStatus,
    SignedSourceSignatureState,
)
from paperworkshell.ui.signed_pdf_confirmation import (
    UnsignedDerivativeConfirmationDialog,
    UnsignedDerivativeConfirmationPresentation,
)

CASE_ID = UUID("26000000-0000-4000-8000-000000000011")
MARKER_SOURCE_ID = UUID("26000000-0000-4000-8000-000000000012")
UNKNOWN_SOURCE_ID = UUID("26000000-0000-4000-8000-000000000013")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog(
    *states: SignedSourceSignatureState,
) -> Iterator[UnsignedDerivativeConfirmationDialog]:
    app = _application()
    if not states:
        states = (SignedSourceSignatureState.MARKERS_DETECTED,)
    source_ids = (MARKER_SOURCE_ID, UNKNOWN_SOURCE_ID)
    sources = tuple(
        SignedSourceModificationSource(source_ids[index], state)
        for index, state in enumerate(states)
    )
    review = SignedSourceModificationReview(
        case_id=CASE_ID,
        manifest_revision=7,
        manifest_sha256="b" * 64,
        manifest_cas_token="cas-token-0208-valid",
        sources=sources,
        pending_source_ids=tuple(source.source_id for source in sources),
        status=SignedSourceReviewStatus.CONFIRMATION_REQUIRED,
    )
    dialog = UnsignedDerivativeConfirmationDialog(
        UnsignedDerivativeConfirmationPresentation(review)
    )
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _button(dialog: UnsignedDerivativeConfirmationDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def test_confirmation_has_explicit_action_visible_label_and_accessible_context() -> None:
    with _dialog() as dialog:
        create = _button(dialog, "createUnsignedDerivativeButton")
        cancel = _button(dialog, "cancelUnsignedDerivativeButton")
        label = dialog.findChild(QLabel, "unsignedDerivativeLabel")
        reason = dialog.findChild(QLabel, "unsignedDerivativeReason")

        assert dialog.windowTitle() == "Create unsigned derivative"
        assert dialog.accessibleName()
        assert "safe default" in dialog.accessibleDescription()
        assert create.text() == "Create unsigned derivative"
        assert create.accessibleName() == "Create unsigned derivative"
        assert create.accessibleDescription()
        assert cancel.text() == "Cancel"
        assert cancel.accessibleName()
        assert cancel.accessibleDescription()
        assert label is not None
        assert label.text() == "Unsigned derivative"
        assert label.accessibleName()
        assert label.accessibleDescription()
        assert reason is not None
        assert "does not validate digital signatures" in reason.text()
        assert reason.accessibleName()
        assert reason.accessibleDescription()


@pytest.mark.parametrize(
    ("notice_reason", "visible_fragment", "qualification"),
    (
        (
            SignedSourceSignatureState.MARKERS_DETECTED,
            "contains digital-signature markers",
            "does not validate",
        ),
        (
            SignedSourceSignatureState.UNKNOWN,
            "could not confirm",
            "does not validate",
        ),
    ),
)
def test_confirmation_shows_the_reason_for_each_closed_notice_state(
    notice_reason: SignedSourceSignatureState,
    visible_fragment: str,
    qualification: str,
) -> None:
    with _dialog(notice_reason) as dialog:
        reason = dialog.findChild(QLabel, "unsignedDerivativeReason")

        assert reason is not None
        assert visible_fragment in reason.text()
        assert qualification in reason.text()


def test_confirmation_combines_detected_and_unknown_reason_without_losing_either() -> None:
    with _dialog(
        SignedSourceSignatureState.MARKERS_DETECTED,
        SignedSourceSignatureState.UNKNOWN,
    ) as dialog:
        reason = dialog.findChild(QLabel, "unsignedDerivativeReason")

        assert reason is not None
        assert "contains digital-signature markers" in reason.text()
        assert "could not confirm" in reason.text()


def test_cancel_is_initial_default_and_escape_rejects() -> None:
    with _dialog() as dialog:
        create = _button(dialog, "createUnsignedDerivativeButton")
        cancel = _button(dialog, "cancelUnsignedDerivativeButton")

        assert dialog.focusWidget() is cancel
        assert cancel.isDefault()
        assert not create.isDefault()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _application().processEvents()

        assert dialog.result() == UnsignedDerivativeConfirmationDialog.DialogCode.Rejected


def test_keyboard_focus_reaches_explicit_create_action_and_space_accepts() -> None:
    with _dialog() as dialog:
        create = _button(dialog, "createUnsignedDerivativeButton")
        cancel = _button(dialog, "cancelUnsignedDerivativeButton")

        assert dialog.focusWidget() is cancel
        QTest.keyClick(cancel, Qt.Key.Key_Tab)
        _application().processEvents()
        scroll = dialog.findChild(QScrollArea, "unsignedDerivativeConfirmationScrollArea")
        assert dialog.focusWidget() is scroll
        QTest.keyClick(scroll, Qt.Key.Key_Tab)
        _application().processEvents()
        assert dialog.focusWidget() is create
        QTest.keyClick(create, Qt.Key.Key_Space)
        _application().processEvents()

        assert dialog.result() == UnsignedDerivativeConfirmationDialog.DialogCode.Accepted


def test_warning_remains_scrollable_and_actions_visible_with_enlarged_text() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSize(max(24, original_font.pointSize() * 2))
    app.setFont(enlarged_font)
    try:
        with _dialog() as dialog:
            dialog.resize(420, 280)
            app.processEvents()
            scroll = dialog.findChild(QScrollArea, "unsignedDerivativeConfirmationScrollArea")
            create = _button(dialog, "createUnsignedDerivativeButton")
            cancel = _button(dialog, "cancelUnsignedDerivativeButton")

            assert scroll is not None
            assert scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            assert create.isVisible()
            assert cancel.isVisible()
            assert dialog.rect().contains(create.mapTo(dialog, create.rect().center()))
            assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().center()))
            create.setFocus()
            app.processEvents()
            assert dialog.focusWidget() is create

            scroll.setFocus()
            app.processEvents()
            assert dialog.focusWidget() is scroll
            before = scroll.verticalScrollBar().value()
            QTest.keyClick(scroll, Qt.Key.Key_PageDown)
            app.processEvents()
            assert scroll.verticalScrollBar().value() > before
    finally:
        app.setFont(original_font)
