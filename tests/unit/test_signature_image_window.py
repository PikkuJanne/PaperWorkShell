"""Qt routing evidence for the PWS-0305 visual signature-image workflow."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QRadioButton,
)
from pytest import MonkeyPatch

from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerPageView,
    OrganizerSignatureImageDeleteOutcome,
    OrganizerSignatureImagePlacementOutcome,
    OrganizerSignatureImageRefreshStatus,
    OrganizerViewState,
)
from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DISCLAIMER,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.signature_images import (
    SignatureImageCleanupResult,
    SignatureImageCleanupStatus,
    SignatureImageErrorCode,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
)
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy
from paperworkshell.overlays.signature_rasters import SignatureImagePreview
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
)
from paperworkshell.ui.window import PaperWorkShellWindow

CASE_ID = UUID("30520000-0000-4000-8000-000000000001")
PAGE_ID = UUID("30520000-0000-4000-8000-000000000002")
DRAFT_ID = UUID("30520000-0000-4000-8000-000000000003")
ASSET_ID = UUID("30520000-0000-4000-8000-000000000004")
OVERLAY_ID = UUID("30520000-0000-4000-8000-000000000005")
BOX = NormalizedPageBox(300_000, 425_000, 400_000, 150_000)


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend() -> OrganizerBackend:
    raise AssertionError("presentation tests do not construct an organizer backend")


def _state(revision: int = 7) -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        revision,
        (OrganizerPageView(PAGE_ID, 1, 0, True),),
        False,
        False,
        False,
    )


def _draft() -> SignatureImagePlacementDraft:
    preview = SignatureImagePreview(
        ASSET_ID,
        3,
        2,
        12,
        bytes((20, 80, 140, 128)) * 6,
        True,
        False,
        False,
        900,
        600,
    )
    return SignatureImagePlacementDraft(DRAFT_ID, CASE_ID, 7, PAGE_ID, preview)


def _choice() -> SignatureImagePlacementChoice:
    return SignatureImagePlacementChoice(OVERLAY_ID, PAGE_ID, BOX, 1, 1)


class _SignatureBridgeBackend:
    def __init__(self) -> None:
        self.state = _state()
        self.prepared_path: Path | None = None
        self.cancelled_drafts: list[UUID] = []

    def begin_signature_image(
        self,
        page_id: UUID,
        source_path: Path,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImagePlacementDraft:
        del progress
        assert page_id == PAGE_ID
        assert not cancellation() and not deadline()
        self.prepared_path = source_path
        return _draft()

    def commit_signature_image(
        self,
        request: SignatureImagePlacementRequest,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> OrganizerSignatureImagePlacementOutcome:
        del progress
        assert request.draft_id == DRAFT_ID
        assert not cancellation() and not deadline()
        self.state = _state(8)
        return OrganizerSignatureImagePlacementOutcome(
            SignatureImagePlacementCommitResult(CASE_ID, ASSET_ID, OVERLAY_ID, 8),
            self.state,
            OrganizerSignatureImageRefreshStatus.REFRESHED,
        )

    def cancel_signature_image(self, draft_id: UUID) -> None:
        self.cancelled_drafts.append(draft_id)

    def signature_image_placements(
        self,
        page_id: UUID,
    ) -> tuple[SignatureImagePlacementChoice, ...]:
        assert page_id == PAGE_ID
        return (_choice(),)

    def delete_signature_image(
        self,
        page_id: UUID,
        overlay_id: UUID,
        policy: SignatureAssetDeletePolicy,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> OrganizerSignatureImageDeleteOutcome:
        del progress
        assert (page_id, overlay_id, policy) == (
            PAGE_ID,
            OVERLAY_ID,
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        )
        assert not cancellation() and not deadline()
        self.state = _state(9)
        return OrganizerSignatureImageDeleteOutcome(self.state, None, None)


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend)
    commands.connect_worker(worker)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    worker.initialized.emit()
    worker.state_changed.emit(_state())
    app.processEvents()
    window._preview_refresh_timer.stop()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _button(window: PaperWorkShellWindow, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert button is not None
    return button


def _active_delete_dialog(
    window: PaperWorkShellWindow,
) -> SignatureImageDeleteDialog | None:
    return window._signature_image_delete_dialog


def test_window_routes_choice_review_and_exact_confirmed_placement(
    monkeypatch: MonkeyPatch,
) -> None:
    private_path = Path("C:/private/NEVER-DISPLAY-APPLICANT.png")
    with _window() as (window, commands, worker):
        prepare_requests: list[tuple[int, object, object, object]] = []
        place_requests: list[tuple[int, object, object]] = []
        commands.signature_image_prepare_requested.disconnect(worker.prepare_signature_image)
        commands.signature_image_place_requested.disconnect(worker.place_signature_image)
        commands.signature_image_prepare_requested.connect(
            lambda operation_id, page_id, path, cancellation: prepare_requests.append(
                (operation_id, page_id, path, cancellation)
            )
        )
        commands.signature_image_place_requested.connect(
            lambda operation_id, request, cancellation: place_requests.append(
                (operation_id, request, cancellation)
            )
        )
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(private_path), "Signature images (*.png)"),
        )

        place_button = _button(window, "placeSignatureImageButton")
        remove_button = _button(window, "removeSignatureImageButton")
        assert place_button.isEnabled() and remove_button.isEnabled()
        assert place_button.accessibleName() == "Place signature image"
        place_button.click()
        _application().processEvents()

        assert len(prepare_requests) == 1
        prepare_id, page_id, path, _cancellation = prepare_requests[0]
        assert page_id == PAGE_ID
        assert path == private_path
        worker.signature_image_prepared.emit(prepare_id, _draft())
        worker.operation_finished.emit(prepare_id)
        _application().processEvents()

        dialog = window.findChild(SignatureImagePlacementDialog, "signatureImagePlacementDialog")
        assert dialog is window._signature_image_dialog
        assert dialog is not None and dialog.isVisible()
        disclaimer = dialog.findChild(QLabel, "signatureImageDisclaimer")
        visual_only = dialog.findChild(QCheckBox, "signatureImageDisclaimerConfirmation")
        background = dialog.findChild(QCheckBox, "signatureImageBackgroundConfirmation")
        apply_button = dialog.findChild(QPushButton, "placeSignatureImageButton")
        assert disclaimer is not None and disclaimer.text() == SIGNATURE_IMAGE_DISCLAIMER
        assert visual_only is not None and background is not None and apply_button is not None
        assert private_path.name not in " ".join(
            label.text() for label in dialog.findChildren(QLabel)
        )

        visual_only.setChecked(True)
        background.setChecked(True)
        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        _application().processEvents()

        assert len(place_requests) == 1
        apply_id, request, _cancellation = place_requests[0]
        assert type(request) is SignatureImagePlacementRequest
        assert request.disclaimer_confirmed
        assert request.whole_image_and_background_confirmed
        result = SignatureImagePlacementCommitResult(CASE_ID, ASSET_ID, OVERLAY_ID, 8)
        outcome = OrganizerSignatureImagePlacementOutcome(
            result,
            _state(8),
            OrganizerSignatureImageRefreshStatus.REFRESHED,
        )
        worker.state_changed.emit(outcome.state)
        worker.signature_image_placed.emit(apply_id, outcome)
        worker.operation_finished.emit(apply_id)
        _application().processEvents()

        assert window._signature_image_dialog is None
        assert window._signature_image_source_path is None
        assert place_button.isEnabled()


def test_window_cancel_releases_draft_before_restoring_focus(monkeypatch: MonkeyPatch) -> None:
    with _window() as (window, commands, worker):
        prepares: list[tuple[int, object, object, object]] = []
        cancels: list[tuple[int, object]] = []
        commands.signature_image_prepare_requested.disconnect(worker.prepare_signature_image)
        commands.signature_image_cancel_requested.disconnect(worker.cancel_signature_image)
        commands.signature_image_prepare_requested.connect(
            lambda *values: prepares.append(cast(tuple[int, object, object, object], values))
        )
        commands.signature_image_cancel_requested.connect(
            lambda operation_id, draft_id: cancels.append((operation_id, draft_id))
        )
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: ("C:/private/hidden.png", ""),
        )

        window._choose_signature_image()
        prepare_id = prepares[0][0]
        worker.signature_image_prepared.emit(prepare_id, _draft())
        worker.operation_finished.emit(prepare_id)
        _application().processEvents()
        dialog = window._signature_image_dialog
        assert dialog is not None

        dialog.reject()
        _application().processEvents()
        assert len(cancels) == 1
        cancel_id, draft_id = cancels[0]
        assert draft_id == DRAFT_ID
        assert not _button(window, "placeSignatureImageButton").isEnabled()

        worker.signature_image_cancelled.emit(cancel_id)
        _application().processEvents()

        button = _button(window, "placeSignatureImageButton")
        assert window._signature_image_dialog is None
        assert window._signature_image_source_path is None
        assert button.isEnabled() and button.hasFocus()


def test_cancelled_in_flight_placement_closes_disposed_draft_without_retry(
    monkeypatch: MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        prepares: list[tuple[int, object, object, object]] = []
        places: list[tuple[int, object, object]] = []
        draft_cancels: list[tuple[int, object]] = []
        commands.signature_image_prepare_requested.disconnect(worker.prepare_signature_image)
        commands.signature_image_place_requested.disconnect(worker.place_signature_image)
        commands.signature_image_cancel_requested.disconnect(worker.cancel_signature_image)
        commands.signature_image_prepare_requested.connect(
            lambda *values: prepares.append(cast(tuple[int, object, object, object], values))
        )
        commands.signature_image_place_requested.connect(
            lambda *values: places.append(cast(tuple[int, object, object], values))
        )
        commands.signature_image_cancel_requested.connect(
            lambda operation_id, draft_id: draft_cancels.append((operation_id, draft_id))
        )
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: ("C:/private/hidden.png", ""),
        )

        window._choose_signature_image()
        prepare_id = prepares[0][0]
        worker.signature_image_prepared.emit(prepare_id, _draft())
        worker.operation_finished.emit(prepare_id)
        _application().processEvents()
        dialog = window._signature_image_dialog
        assert dialog is not None
        visual_only = dialog.findChild(QCheckBox, "signatureImageDisclaimerConfirmation")
        background = dialog.findChild(QCheckBox, "signatureImageBackgroundConfirmation")
        place = dialog.findChild(QPushButton, "placeSignatureImageButton")
        cancel = dialog.findChild(QPushButton, "cancelSignatureImageButton")
        assert visual_only is not None and background is not None
        assert place is not None and cancel is not None
        visual_only.setChecked(True)
        background.setChecked(True)
        QTest.keyClick(place, Qt.Key.Key_Space)
        _application().processEvents()

        assert len(places) == 1
        place_id, _request, cancellation = places[0]
        assert type(cancellation) is OperationCancellation
        assert not cancellation.cancelled()
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        _application().processEvents()
        assert cancellation.cancelled()

        worker.signature_image_place_failed.emit(
            place_id,
            UiFailure(
                SignatureImageErrorCode.CANCELLED.value,
                "Private backend detail must not be announced.",
                True,
                place_id,
            ),
        )
        _application().processEvents()

        assert window._signature_image_dialog is None
        assert window._signature_image_source_path is None
        assert not dialog.isVisible()
        assert draft_cancels == []
        assert window._status_label.text() == (
            "Signature-image placement cancelled. No placement was committed."
        )

        worker.operation_finished.emit(place_id)
        _application().processEvents()
        place_button = _button(window, "placeSignatureImageButton")
        assert place_button.isEnabled() and place_button.hasFocus()


def test_window_routes_explicit_remove_if_unused_policy() -> None:
    with _window() as (window, commands, worker):
        list_requests: list[tuple[int, object]] = []
        delete_requests: list[tuple[int, object, object, object, object]] = []
        commands.signature_image_placements_requested.disconnect(
            worker.list_signature_image_placements
        )
        commands.signature_image_delete_requested.disconnect(worker.delete_signature_image)
        commands.signature_image_placements_requested.connect(
            lambda request_id, page_id: list_requests.append((request_id, page_id))
        )
        commands.signature_image_delete_requested.connect(
            lambda operation_id, page_id, overlay_id, policy, cancellation: delete_requests.append(
                (operation_id, page_id, overlay_id, policy, cancellation)
            )
        )

        _button(window, "removeSignatureImageButton").click()
        _application().processEvents()
        assert len(list_requests) == 1 and list_requests[0][1] == PAGE_ID
        request_id = list_requests[0][0]
        worker.signature_image_placements_ready.emit(request_id, (_choice(),))
        _application().processEvents()

        dialog = window.findChild(SignatureImageDeleteDialog, "signatureImageDeleteDialog")
        assert dialog is window._signature_image_delete_dialog
        assert dialog is not None
        remove_unused = dialog.findChild(
            QRadioButton,
            "removeUnusedSignatureImageAssetRadio",
        )
        confirm = dialog.findChild(QPushButton, "confirmSignatureImageDeleteButton")
        assert remove_unused is not None and confirm is not None
        remove_unused.setChecked(True)
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        _application().processEvents()

        assert len(delete_requests) == 1
        delete_id, page_id, overlay_id, policy, cancellation = delete_requests[0]
        assert (page_id, overlay_id, policy) == (
            PAGE_ID,
            OVERLAY_ID,
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        )
        assert type(cancellation) is OperationCancellation
        assert not cancellation.cancelled()
        cleanup = SignatureImageCleanupResult(
            CASE_ID,
            ASSET_ID,
            SignatureImageCleanupStatus.REMOVED,
        )
        outcome = OrganizerSignatureImageDeleteOutcome(_state(8), cleanup, None)
        worker.state_changed.emit(outcome.state)
        worker.signature_image_deleted.emit(delete_id, outcome)
        worker.operation_finished.emit(delete_id)
        _application().processEvents()

        assert _active_delete_dialog(window) is None
        assert window._signature_image_page_id is None


def test_window_delete_cancel_waits_for_truthful_post_manifest_outcome() -> None:
    with _window() as (window, commands, worker):
        list_requests: list[tuple[int, object]] = []
        delete_requests: list[tuple[int, object, object, object, object]] = []
        commands.signature_image_placements_requested.disconnect(
            worker.list_signature_image_placements
        )
        commands.signature_image_delete_requested.disconnect(worker.delete_signature_image)
        commands.signature_image_placements_requested.connect(
            lambda request_id, page_id: list_requests.append((request_id, page_id))
        )
        commands.signature_image_delete_requested.connect(
            lambda operation_id, page_id, overlay_id, policy, cancellation: delete_requests.append(
                (operation_id, page_id, overlay_id, policy, cancellation)
            )
        )

        _button(window, "removeSignatureImageButton").click()
        _application().processEvents()
        request_id = list_requests[0][0]
        worker.signature_image_placements_ready.emit(request_id, (_choice(),))
        _application().processEvents()
        dialog = window._signature_image_delete_dialog
        assert dialog is not None
        confirm = dialog.findChild(QPushButton, "confirmSignatureImageDeleteButton")
        cancel = dialog.findChild(QPushButton, "cancelSignatureImageDeleteButton")
        assert confirm is not None and cancel is not None

        QTest.keyClick(confirm, Qt.Key.Key_Space)
        _application().processEvents()
        assert len(delete_requests) == 1
        delete_id, _page_id, _overlay_id, _policy, cancellation = delete_requests[0]
        assert type(cancellation) is OperationCancellation
        assert not cancellation.cancelled()

        QTest.keyClick(cancel, Qt.Key.Key_Space)
        _application().processEvents()
        assert cancellation.cancelled()
        assert window._signature_image_delete_dialog is dialog
        assert dialog.isVisible()

        warning = (
            "Signature-image placement removed, but the unused case copy could not be removed."
        )
        outcome = OrganizerSignatureImageDeleteOutcome(_state(8), None, warning)
        worker.state_changed.emit(outcome.state)
        worker.signature_image_deleted.emit(delete_id, outcome)
        _application().processEvents()

        assert _active_delete_dialog(window) is None
        assert window._signature_image_page_id is None
        assert window._status_label.text() == warning

        worker.operation_finished.emit(delete_id)
        _application().processEvents()
        assert window._active_operation is None


def test_window_precommit_delete_cancellation_closes_without_retry() -> None:
    with _window() as (window, commands, worker):
        list_requests: list[tuple[int, object]] = []
        delete_requests: list[tuple[int, object, object, object, object]] = []
        commands.signature_image_placements_requested.disconnect(
            worker.list_signature_image_placements
        )
        commands.signature_image_delete_requested.disconnect(worker.delete_signature_image)
        commands.signature_image_placements_requested.connect(
            lambda request_id, page_id: list_requests.append((request_id, page_id))
        )
        commands.signature_image_delete_requested.connect(
            lambda operation_id, page_id, overlay_id, policy, cancellation: delete_requests.append(
                (operation_id, page_id, overlay_id, policy, cancellation)
            )
        )

        _button(window, "removeSignatureImageButton").click()
        _application().processEvents()
        worker.signature_image_placements_ready.emit(list_requests[0][0], (_choice(),))
        _application().processEvents()
        dialog = window._signature_image_delete_dialog
        assert dialog is not None
        confirm = dialog.findChild(QPushButton, "confirmSignatureImageDeleteButton")
        cancel = dialog.findChild(QPushButton, "cancelSignatureImageDeleteButton")
        assert confirm is not None and cancel is not None
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        _application().processEvents()
        delete_id, _page_id, _overlay_id, _policy, cancellation = delete_requests[0]
        assert type(cancellation) is OperationCancellation

        QTest.keyClick(cancel, Qt.Key.Key_Space)
        _application().processEvents()
        assert cancellation.cancelled()
        worker.signature_image_delete_failed.emit(
            delete_id,
            UiFailure(
                SignatureImageErrorCode.CANCELLED.value,
                "Private backend detail must not be announced.",
                True,
                delete_id,
            ),
        )
        _application().processEvents()

        assert window._signature_image_delete_dialog is None
        assert window._signature_image_page_id is None
        assert not dialog.isVisible()
        assert window._status_label.text() == (
            "Signature-image removal cancelled. The placement was not removed."
        )

        worker.operation_finished.emit(delete_id)
        _application().processEvents()
        remove = _button(window, "removeSignatureImageButton")
        assert remove.isEnabled() and remove.hasFocus()


def test_worker_bridge_correlates_prepare_commit_list_delete_and_cancel() -> None:
    backend = _SignatureBridgeBackend()
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    prepared: list[tuple[int, object]] = []
    placed: list[tuple[int, object]] = []
    listed: list[tuple[int, object]] = []
    deleted: list[tuple[int, object]] = []
    cancelled: list[int] = []
    finished: list[int] = []
    failures: list[object] = []
    worker.signature_image_prepared.connect(
        lambda operation_id, draft: prepared.append((operation_id, draft))
    )
    worker.signature_image_placed.connect(
        lambda operation_id, outcome: placed.append((operation_id, outcome))
    )
    worker.signature_image_placements_ready.connect(
        lambda request_id, choices: listed.append((request_id, choices))
    )
    worker.signature_image_deleted.connect(
        lambda operation_id, outcome: deleted.append((operation_id, outcome))
    )
    worker.signature_image_cancelled.connect(cancelled.append)
    worker.operation_finished.connect(finished.append)
    worker.failed.connect(failures.append)

    worker.prepare_signature_image(
        41,
        PAGE_ID,
        Path("C:/private/never-log-this.png"),
        OperationCancellation(),
    )
    request = SignatureImagePlacementRequest(DRAFT_ID, BOX, True, True)
    worker.place_signature_image(42, request, OperationCancellation())
    worker.list_signature_image_placements(43, PAGE_ID)
    worker.delete_signature_image(
        44,
        PAGE_ID,
        OVERLAY_ID,
        SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        OperationCancellation(),
    )
    worker.cancel_signature_image(45, DRAFT_ID)

    assert prepared == [(41, _draft())]
    assert len(placed) == 1 and placed[0][0] == 42
    assert listed == [(43, (_choice(),))]
    assert len(deleted) == 1 and deleted[0][0] == 44
    assert cancelled == [45]
    assert finished == [41, 42, 44]
    assert failures == []
    assert backend.prepared_path == Path("C:/private/never-log-this.png")
    assert backend.cancelled_drafts == [DRAFT_ID]
