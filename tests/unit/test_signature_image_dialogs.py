from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QSpinBox,
)

from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DELETE_KEEP_WORDING,
    SIGNATURE_IMAGE_DELETE_UNUSED_WORDING,
    SIGNATURE_IMAGE_DISCLAIMER,
    SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.signature_images import SignatureImageErrorCode
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy
from paperworkshell.overlays.signature_rasters import SignatureImagePreview
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
    default_signature_image_box,
)

DRAFT_ID = UUID("30500000-0000-4000-8000-000000000001")
CASE_ID = UUID("30500000-0000-4000-8000-000000000002")
PAGE_ID = UUID("30500000-0000-4000-8000-000000000003")
ASSET_ID = UUID("30500000-0000-4000-8000-000000000004")
OVERLAY_ID = UUID("30500000-0000-4000-8000-000000000005")
PRIVATE_FAILURE = "PRIVATE-SIGNATURE-PATH-AND-NATIVE-ERROR"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _draft() -> SignatureImagePlacementDraft:
    preview = SignatureImagePreview(
        ASSET_ID,
        4,
        3,
        16,
        bytes((30, 60, 90, 128)) * 12,
        True,
        False,
        False,
        800,
        600,
    )
    return SignatureImagePlacementDraft(DRAFT_ID, CASE_ID, 7, PAGE_ID, preview)


def _choice() -> SignatureImagePlacementChoice:
    return SignatureImagePlacementChoice(
        OVERLAY_ID,
        PAGE_ID,
        NormalizedPageBox(125_000, 250_000, 300_000, 125_000),
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


def _button(
    dialog: SignatureImagePlacementDialog | SignatureImageDeleteDialog, name: str
) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def test_place_requires_both_exact_confirmations_and_emits_typed_request() -> None:
    app = _application()
    with _placement_dialog() as dialog:
        disclaimer = dialog.findChild(QLabel, "signatureImageDisclaimer")
        preview = dialog.findChild(QLabel, "signatureImagePreview")
        visual_only = dialog.findChild(QCheckBox, "signatureImageDisclaimerConfirmation")
        whole_image = dialog.findChild(QCheckBox, "signatureImageBackgroundConfirmation")
        place = _button(dialog, "placeSignatureImageButton")
        assert disclaimer is not None
        assert preview is not None
        assert visual_only is not None
        assert whole_image is not None

        assert disclaimer.text() == SIGNATURE_IMAGE_DISCLAIMER
        assert disclaimer.accessibleDescription() == SIGNATURE_IMAGE_DISCLAIMER
        assert whole_image.text() == SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION
        assert preview.pixmap() is not None
        assert not preview.pixmap().isNull()
        assert preview.pixmap().width() == 4
        assert preview.pixmap().height() == 3
        assert not place.isEnabled()

        visual_only.setChecked(True)
        assert not place.isEnabled()
        whole_image.setChecked(True)
        assert place.isEnabled()

        requests: list[SignatureImagePlacementRequest] = []
        dialog.place_requested.connect(requests.append)
        QTest.mouseClick(place, Qt.MouseButton.LeftButton)
        app.processEvents()

        assert len(requests) == 1
        request = requests[0]
        assert type(request) is SignatureImagePlacementRequest
        assert request.draft_id == DRAFT_ID
        assert request.box == default_signature_image_box()
        assert request.disclaimer_confirmed is True
        assert request.whole_image_and_background_confirmed is True
        assert not place.isEnabled()


def test_out_of_page_geometry_blocks_placement_until_corrected() -> None:
    with _placement_dialog() as dialog:
        visual_only = dialog.findChild(QCheckBox, "signatureImageDisclaimerConfirmation")
        whole_image = dialog.findChild(QCheckBox, "signatureImageBackgroundConfirmation")
        left = dialog.findChild(QSpinBox, "signatureImageLeft")
        width = dialog.findChild(QSpinBox, "signatureImageWidth")
        status = dialog.findChild(QLabel, "signatureImagePlacementStatus")
        place = _button(dialog, "placeSignatureImageButton")
        assert visual_only is not None
        assert whole_image is not None
        assert left is not None
        assert width is not None
        assert status is not None

        visual_only.setChecked(True)
        whole_image.setChecked(True)
        assert place.isEnabled()

        left.setValue(900_000)
        width.setValue(200_000)
        assert not place.isEnabled()
        assert "within the full normalized page" in status.text()

        width.setValue(100_000)
        assert place.isEnabled()


def test_failure_uses_closed_wording_and_exposes_keyboard_retry() -> None:
    app = _application()
    with _placement_dialog() as dialog:
        retry = _button(dialog, "retrySignatureImageButton")
        status = dialog.findChild(QLabel, "signatureImagePlacementStatus")
        assert status is not None
        retries: list[bool] = []
        dialog.retry_requested.connect(lambda: retries.append(True))

        dialog.show_failure(SignatureImageErrorCode.COMMIT_FAILED.value, PRIVATE_FAILURE)
        app.processEvents()

        assert retry.isVisible()
        assert retry.isEnabled()
        assert dialog.focusWidget() is retry
        assert status.text() == (
            "PaperWorkShell could not place that visual signature image safely. "
            "Choose Retry to prepare it again, or Cancel."
        )
        assert PRIVATE_FAILURE not in status.text()

        QTest.keyClick(retry, Qt.Key.Key_Space)
        app.processEvents()
        assert retries == [True]


@pytest.mark.parametrize(
    ("remove_unused", "expected_policy"),
    (
        (False, SignatureAssetDeletePolicy.KEEP_ASSET),
        (True, SignatureAssetDeletePolicy.REMOVE_IF_UNUSED),
    ),
)
def test_delete_policy_wording_and_signal_use_typed_enum(
    remove_unused: bool,
    expected_policy: SignatureAssetDeletePolicy,
) -> None:
    app = _application()
    with _delete_dialog() as dialog:
        placements = dialog.findChild(QListWidget, "signatureImagePlacementList")
        keep = dialog.findChild(QRadioButton, "keepSignatureImageAssetRadio")
        remove = dialog.findChild(QRadioButton, "removeUnusedSignatureImageAssetRadio")
        confirm = _button(dialog, "confirmSignatureImageDeleteButton")
        assert placements is not None
        assert keep is not None
        assert remove is not None

        assert keep.text() == SIGNATURE_IMAGE_DELETE_KEEP_WORDING
        assert keep.accessibleDescription() == SIGNATURE_IMAGE_DELETE_KEEP_WORDING
        assert remove.text() == SIGNATURE_IMAGE_DELETE_UNUSED_WORDING
        assert remove.accessibleDescription() == SIGNATURE_IMAGE_DELETE_UNUSED_WORDING
        assert placements.count() == 1
        assert placements.item(0).text() == _choice().accessible_summary
        assert not placements.dragEnabled()
        assert not placements.acceptDrops()

        remove.setChecked(remove_unused)
        requests: list[tuple[object, object]] = []
        dialog.delete_requested.connect(
            lambda overlay_id, policy: requests.append((overlay_id, policy))
        )
        QTest.mouseClick(confirm, Qt.MouseButton.LeftButton)
        app.processEvents()

        assert requests == [(OVERLAY_ID, expected_policy)]
        assert type(requests[0][1]) is SignatureAssetDeletePolicy


def test_pending_delete_exposes_one_cooperative_cancellation_request() -> None:
    app = _application()
    with _delete_dialog() as dialog:
        confirm = _button(dialog, "confirmSignatureImageDeleteButton")
        cancel = _button(dialog, "cancelSignatureImageDeleteButton")
        status = dialog.findChild(QLabel, "signatureImageDeleteStatus")
        assert status is not None
        cancellations: list[bool] = []
        dialog.cancel_operation_requested.connect(lambda: cancellations.append(True))

        QTest.keyClick(confirm, Qt.Key.Key_Space)
        app.processEvents()

        assert cancel.isEnabled()
        assert dialog.isVisible()
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        app.processEvents()

        assert cancellations == [True]
        assert dialog.isVisible()
        assert not cancel.isEnabled()
        assert status.text() == "Cancelling signature-image removal."

        dialog.reject()
        app.processEvents()
        assert cancellations == [True]
        assert dialog.isVisible()
