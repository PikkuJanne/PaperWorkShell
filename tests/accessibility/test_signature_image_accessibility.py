from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DELETE_KEEP_WORDING,
    SIGNATURE_IMAGE_DELETE_UNUSED_WORDING,
    SIGNATURE_IMAGE_DISCLAIMER,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy
from paperworkshell.overlays.signature_rasters import SignatureImagePreview
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
)

DRAFT_ID = UUID("30510000-0000-4000-8000-000000000001")
CASE_ID = UUID("30510000-0000-4000-8000-000000000002")
PAGE_ID = UUID("30510000-0000-4000-8000-000000000003")
ASSET_ID = UUID("30510000-0000-4000-8000-000000000004")
OVERLAY_ID = UUID("30510000-0000-4000-8000-000000000005")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _draft() -> SignatureImagePlacementDraft:
    preview = SignatureImagePreview(
        ASSET_ID,
        6,
        4,
        24,
        bytes((20, 80, 140, 96)) * 24,
        True,
        False,
        False,
        1_200,
        800,
    )
    return SignatureImagePlacementDraft(DRAFT_ID, CASE_ID, 4, PAGE_ID, preview)


def _choice() -> SignatureImagePlacementChoice:
    return SignatureImagePlacementChoice(
        OVERLAY_ID,
        PAGE_ID,
        NormalizedPageBox(100_000, 200_000, 400_000, 150_000),
        1,
        1,
    )


@contextmanager
def _placement_dialog() -> Iterator[SignatureImagePlacementDialog]:
    app = _application()
    dialog = SignatureImagePlacementDialog(_draft())
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@contextmanager
def _delete_dialog() -> Iterator[SignatureImageDeleteDialog]:
    app = _application()
    dialog = SignatureImageDeleteDialog((_choice(),))
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _widget[WidgetT: QWidget](
    dialog: SignatureImagePlacementDialog | SignatureImageDeleteDialog,
    widget_type: type[WidgetT],
    name: str,
) -> WidgetT:
    widget = dialog.findChild(widget_type, name)
    assert widget is not None
    return widget


def _assert_role(widget: QWidget, role: QAccessible.Role) -> None:
    interface = QAccessible.queryAccessibleInterface(widget)
    assert interface is not None
    assert interface.role() is role


def test_placement_exposes_exact_visual_only_contract_and_accessible_roles() -> None:
    with _placement_dialog() as dialog:
        disclaimer = _widget(dialog, QLabel, "signatureImageDisclaimer")
        preview = _widget(dialog, QLabel, "signatureImagePreview")
        status = _widget(dialog, QLabel, "signatureImagePlacementStatus")
        visual_only = _widget(
            dialog,
            QCheckBox,
            "signatureImageDisclaimerConfirmation",
        )
        whole_image = _widget(
            dialog,
            QCheckBox,
            "signatureImageBackgroundConfirmation",
        )
        place = _widget(dialog, QPushButton, "placeSignatureImageButton")
        scroll = _widget(dialog, QScrollArea, "signatureImagePlacementScrollArea")

        assert dialog.accessibleName() == "Place signature image dialog"
        assert "Confirm both statements" in dialog.accessibleDescription()
        assert disclaimer.text() == SIGNATURE_IMAGE_DISCLAIMER
        assert disclaimer.accessibleDescription() == SIGNATURE_IMAGE_DISCLAIMER
        assert preview.accessibleName() == "Signature image preview"
        assert "not described or read aloud" in preview.accessibleDescription()
        assert preview.pixmap().width() <= 512
        assert preview.pixmap().height() <= 512
        assert status.accessibleName()
        assert status.accessibleDescription() == status.text()
        assert visual_only.accessibleName()
        assert visual_only.accessibleDescription() == SIGNATURE_IMAGE_DISCLAIMER
        assert whole_image.accessibleName()
        assert whole_image.accessibleDescription()
        assert place.accessibleName() == "Place signature image as a visual image"
        assert place.accessibleDescription()
        assert scroll.widgetResizable()
        assert scroll.accessibleName() == "Signature image review content"

        _assert_role(dialog, QAccessible.Role.Dialog)
        _assert_role(visual_only, QAccessible.Role.CheckBox)
        _assert_role(whole_image, QAccessible.Role.CheckBox)
        _assert_role(
            _widget(dialog, QSpinBox, "signatureImageLeft"),
            QAccessible.Role.SpinBox,
        )
        _assert_role(place, QAccessible.Role.Button)


def test_keyboard_only_confirmation_and_focus_order_reach_place_action() -> None:
    app = _application()
    with _placement_dialog() as dialog:
        visual_only = _widget(
            dialog,
            QCheckBox,
            "signatureImageDisclaimerConfirmation",
        )
        whole_image = _widget(
            dialog,
            QCheckBox,
            "signatureImageBackgroundConfirmation",
        )
        status = _widget(dialog, QLabel, "signatureImagePlacementStatus")
        place = _widget(dialog, QPushButton, "placeSignatureImageButton")
        requests: list[SignatureImagePlacementRequest] = []
        dialog.place_requested.connect(requests.append)

        assert dialog.focusWidget() is visual_only
        assert not place.isEnabled()
        QTest.keyClick(visual_only, Qt.Key.Key_Space)
        app.processEvents()
        assert visual_only.isChecked()
        assert not place.isEnabled()

        QTest.keyClick(visual_only, Qt.Key.Key_Tab)
        app.processEvents()
        assert dialog.focusWidget() is whole_image
        QTest.keyClick(whole_image, Qt.Key.Key_Space)
        app.processEvents()
        assert whole_image.isChecked()
        assert place.isEnabled()

        QTest.keyClick(whole_image, Qt.Key.Key_Tab)
        app.processEvents()
        assert dialog.focusWidget() is status
        QTest.keyClick(status, Qt.Key.Key_Tab)
        app.processEvents()
        assert dialog.focusWidget() is place
        QTest.keyClick(place, Qt.Key.Key_Space)
        app.processEvents()

        assert len(requests) == 1
        assert type(requests[0]) is SignatureImagePlacementRequest
        assert requests[0].disclaimer_confirmed
        assert requests[0].whole_image_and_background_confirmed


def test_delete_dialog_has_keyboard_selection_and_explicit_accessible_policy() -> None:
    app = _application()
    with _delete_dialog() as dialog:
        placements = _widget(dialog, QListWidget, "signatureImagePlacementList")
        keep = _widget(dialog, QRadioButton, "keepSignatureImageAssetRadio")
        remove_unused = _widget(
            dialog,
            QRadioButton,
            "removeUnusedSignatureImageAssetRadio",
        )
        remove = _widget(dialog, QPushButton, "confirmSignatureImageDeleteButton")
        cancel = _widget(dialog, QPushButton, "cancelSignatureImageDeleteButton")
        scroll = _widget(dialog, QScrollArea, "signatureImageDeleteScrollArea")
        requests: list[tuple[object, object]] = []
        cancellations: list[bool] = []
        dialog.delete_requested.connect(
            lambda overlay_id, policy: requests.append((overlay_id, policy))
        )
        dialog.cancel_operation_requested.connect(lambda: cancellations.append(True))

        assert dialog.accessibleName() == "Remove signature image dialog"
        assert dialog.focusWidget() is placements
        assert placements.accessibleName()
        assert placements.accessibleDescription()
        assert not placements.dragEnabled()
        assert not placements.acceptDrops()
        keep_label = dialog.findChild(QLabel, "keepSignatureImageAssetRadioLabel")
        assert keep_label is not None and keep_label.buddy() is keep
        assert keep_label.wordWrap() and keep_label.text() == SIGNATURE_IMAGE_DELETE_KEEP_WORDING
        assert keep.accessibleName() == SIGNATURE_IMAGE_DELETE_KEEP_WORDING
        assert keep.accessibleDescription() == SIGNATURE_IMAGE_DELETE_KEEP_WORDING
        remove_label = dialog.findChild(QLabel, "removeUnusedSignatureImageAssetRadioLabel")
        assert remove_label is not None and remove_label.buddy() is remove_unused
        assert (
            remove_label.wordWrap() and remove_label.text() == SIGNATURE_IMAGE_DELETE_UNUSED_WORDING
        )
        assert remove_unused.accessibleName() == SIGNATURE_IMAGE_DELETE_UNUSED_WORDING
        assert remove_unused.accessibleDescription() == SIGNATURE_IMAGE_DELETE_UNUSED_WORDING
        assert remove.accessibleDescription()
        assert cancel.accessibleDescription()
        assert scroll.widgetResizable()
        assert scroll.accessibleName() == "Signature image removal content"

        _assert_role(placements, QAccessible.Role.List)
        _assert_role(keep, QAccessible.Role.RadioButton)
        _assert_role(remove_unused, QAccessible.Role.RadioButton)
        _assert_role(remove, QAccessible.Role.Button)
        _assert_role(cancel, QAccessible.Role.Button)

        remove_unused.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(remove_unused, Qt.Key.Key_Space)
        app.processEvents()
        assert remove_unused.isChecked()
        remove.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(remove, Qt.Key.Key_Space)
        app.processEvents()

        assert requests == [(OVERLAY_ID, SignatureAssetDeletePolicy.REMOVE_IF_UNUSED)]
        assert type(requests[0][1]) is SignatureAssetDeletePolicy
        assert cancel.isEnabled()
        cancel.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        app.processEvents()
        assert cancellations == [True]
