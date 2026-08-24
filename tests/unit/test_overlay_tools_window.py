"""Qt bridge evidence for opening, applying, and cancelling PWS-0304 tools."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QSpinBox

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.domain.image_pages import ImageQuarterTurn
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformIntent,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
)
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog
from paperworkshell.ui.window import PaperWorkShellWindow, _overlay_editor_display_facts

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend() -> OrganizerBackend:
    raise AssertionError("this presentation test does not construct an organizer backend")


class _CoreOverlayBeginBackend:
    def __init__(self, editor: CoreOverlayToolsState, state: OrganizerViewState) -> None:
        self._editor = editor
        self.state = state

    def begin_core_overlay_tools(self, page_id: UUID) -> CoreOverlayToolsState:
        assert page_id == self._editor.page_id
        return self._editor


class _CoreOverlayApplyBackend:
    def __init__(self, state: OrganizerViewState) -> None:
        self.state = state

    def apply_core_overlay_tools(
        self,
        _editor: CoreOverlayToolsState,
    ) -> OrganizerViewState:
        return self.state


class _CoreOverlayAbortBackend:
    def __init__(self) -> None:
        self.aborted = False

    def abort_core_overlay_tools(self) -> None:
        self.aborted = True


def _editor(*, revision: int | None = None) -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE.read_text(encoding="utf-8")),
    )
    document["assets"] = []
    document["overlays"] = []
    if revision is not None:
        document["revision"] = revision
    case_id = UUID(cast(str, document["case_id"]))
    snapshot = ManifestSnapshot(
        case_id,
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "d" * 64,
        "overlay_window_test_cas",
    )
    page_id = UUID(cast(list[str], document["page_sequence"])[0])
    return CoreOverlayToolsState.from_snapshot(snapshot, page_id)


def _organizer_state(
    editor: CoreOverlayToolsState, *, revision: int | None = None
) -> OrganizerViewState:
    return OrganizerViewState(
        editor.case_id,
        editor.manifest_revision if revision is None else revision,
        (OrganizerPageView(editor.page_id, 1, 0, True),),
        False,
        False,
        False,
    )


def test_overlay_editor_display_facts_use_exact_crop_rotation_and_refuse_deskew_pointer_map() -> (
    None
):
    intent = ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 200_000, 600_000, 500_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=1_250,
    )
    page = OrganizerPageView(
        UUID("30000000-0000-4000-8000-000000000001"),
        1,
        90,
        True,
        intent,
    )
    transform, pointer_enabled = _overlay_editor_display_facts(page)
    assert transform.manifest_crop.x == 100_000
    assert transform.manifest_crop.y == 200_000
    assert transform.manifest_crop.width == 600_000
    assert transform.manifest_crop.height == 500_000
    assert int(transform.manifest_rotation) == 90
    assert not pointer_enabled


def test_window_uses_schematic_instead_of_misaligned_pixels_for_deskewed_page() -> None:
    editor = _editor()
    intent = ImageTransformIntent(
        crop=ImageNormalizedCrop(),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
        deskew_angle_millidegrees_clockwise=1_000,
    )
    organizer_state = OrganizerViewState(
        editor.case_id,
        editor.manifest_revision,
        (OrganizerPageView(editor.page_id, 1, 0, True, intent),),
        False,
        False,
        False,
    )
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        cancel_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_cancel_requested.disconnect(worker.cancel_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_cancel_requested.connect(
            lambda operation_id, state: cancel_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(organizer_state)
        image = QImage(32, 48, QImage.Format.Format_RGBA8888)
        image.fill(QColor("white"))
        window._model.set_preview(editor.page_id, QPixmap.fromImage(image))
        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()

        dialog = window._core_overlay_tools_dialog
        assert dialog is not None
        canvas = dialog.findChild(OverlayEditorCanvas, "overlayEditorCanvas")
        assert canvas is not None
        assert not canvas.pointer_mapping_enabled
        assert canvas._preview is None
        assert canvas._display_size is not None
        assert canvas._display_size.width_pixels == 32
        assert canvas._display_size.height_pixels == 48
        dialog.reject()
        _application().processEvents()
        assert len(cancel_requests) == 1
        worker.core_overlay_tools_cancelled.emit(cancel_requests[0][0])


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
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def test_window_shortcut_opens_dialog_and_cancel_releases_backend_before_focus_restore() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        cancel_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_cancel_requested.disconnect(worker.cancel_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_cancel_requested.connect(
            lambda operation_id, state: cancel_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()

        button = window.findChild(QPushButton, "editFormMarksButton")
        assert button is not None and button.isEnabled()
        assert button.accessibleName() == "Edit visual overlays"
        QTest.keyClick(
            window,
            Qt.Key.Key_F,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        _application().processEvents()

        assert len(begin_requests) == 1
        operation_id, page_id = begin_requests[0]
        assert page_id == editor.page_id
        worker.core_overlay_tools_started.emit(operation_id, editor)
        _application().processEvents()
        dialog = window.findChild(CoreOverlayToolsDialog, "coreOverlayToolsDialog")
        assert dialog is window._core_overlay_tools_dialog
        assert dialog is not None and dialog.isVisible()

        dialog.reject()
        _application().processEvents()
        assert len(cancel_requests) == 1
        cancel_operation_id, cancelled_state = cancel_requests[0]
        assert cancelled_state is editor
        assert not button.isEnabled()

        worker.core_overlay_tools_cancelled.emit(cancel_operation_id)
        _application().processEvents()
        assert button.isEnabled()
        assert button.hasFocus()


def test_preview_refresh_timer_stays_stopped_while_overlay_editor_opens_and_is_active() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        cancel_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_cancel_requested.disconnect(worker.cancel_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_cancel_requested.connect(
            lambda operation_id, state: cancel_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        state = _organizer_state(editor)
        worker.state_changed.emit(state)
        _application().processEvents()
        assert window._preview_refresh_timer.isActive()

        window._request_core_overlay_tools()
        assert not window._preview_refresh_timer.isActive()
        worker.state_changed.emit(state)
        window._schedule_preview_refresh()
        _application().processEvents()
        assert not window._preview_refresh_timer.isActive()

        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()
        assert window._core_overlay_tools_dialog is not None
        window._schedule_preview_refresh()
        window._refresh_visible_previews()
        assert not window._preview_refresh_timer.isActive()
        assert window._active_operation is None

        window._core_overlay_tools_dialog.reject()
        _application().processEvents()
        worker.core_overlay_tools_cancelled.emit(cancel_requests[0][0])


def test_window_routes_dialog_apply_and_closes_only_after_correlated_saved_state() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        apply_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_apply_requested.disconnect(worker.apply_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()
        window._request_core_overlay_tools()
        operation_id = begin_requests[0][0]
        worker.core_overlay_tools_started.emit(operation_id, editor)
        _application().processEvents()
        dialog = window._core_overlay_tools_dialog
        assert dialog is not None

        combo = dialog.findChild(QComboBox, "newOverlayTypeCombo")
        add = dialog.findChild(QPushButton, "addCoreOverlayButton")
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert combo is not None and add is not None and apply_button is not None
        combo.setCurrentIndex(combo.findData(OverlayKind.TEXT.value))
        add.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(add, Qt.Key.Key_Space)
        apply_button.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        _application().processEvents()

        assert len(apply_requests) == 1
        apply_operation_id, requested_state = apply_requests[0]
        assert requested_state is dialog.state
        assert dialog.apply_pending
        worker.state_changed.emit(_organizer_state(editor, revision=editor.manifest_revision + 1))
        worker.core_overlay_tools_applied.emit(
            apply_operation_id,
            _organizer_state(editor, revision=editor.manifest_revision + 1),
        )
        _application().processEvents()

        assert window._core_overlay_tools_dialog is None
        button = window.findChild(QPushButton, "editFormMarksButton")
        assert button is not None and button.isEnabled() and button.hasFocus()


def test_window_accepts_editor_after_begin_publishes_a_flushed_revision() -> None:
    opening = _editor()
    refreshed = _editor(revision=opening.manifest_revision + 1)
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(opening))
        _application().processEvents()
        window._preview_refresh_timer.stop()

        window._request_core_overlay_tools()
        operation_id = begin_requests[0][0]
        worker.state_changed.emit(_organizer_state(refreshed))
        worker.core_overlay_tools_started.emit(operation_id, refreshed)
        _application().processEvents()

        dialog = window._core_overlay_tools_dialog
        assert dialog is not None and dialog.state is refreshed
        dialog.reject()


def test_window_cancels_a_started_editor_that_no_longer_matches_published_state() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        abort_requests: list[int] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_abort_requested.disconnect(worker.abort_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_abort_requested.connect(abort_requests.append)
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()

        window._request_core_overlay_tools()
        operation_id = begin_requests[0][0]
        worker.state_changed.emit(_organizer_state(editor, revision=editor.manifest_revision + 1))
        worker.core_overlay_tools_started.emit(operation_id, editor)
        _application().processEvents()

        assert window._core_overlay_tools_dialog is None
        assert len(abort_requests) == 1
        cancel_operation_id = abort_requests[0]
        assert window._core_overlay_tools_cancel_operation_id == cancel_operation_id


def test_worker_publishes_begin_flush_state_before_revision_bound_editor() -> None:
    editor = _editor()
    organizer_state = _organizer_state(editor)
    backend = _CoreOverlayBeginBackend(editor, organizer_state)
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    events: list[tuple[str, object]] = []
    worker.state_changed.connect(lambda state: events.append(("state", state)))
    worker.core_overlay_tools_started.connect(
        lambda _operation_id, state: events.append(("editor", state))
    )

    worker.begin_core_overlay_tools(17, editor.page_id)

    assert events == [("state", organizer_state), ("editor", editor)]


def test_worker_rejects_exact_apply_result_for_an_uncorrelated_case() -> None:
    editor = _editor()
    wrong = replace(
        _organizer_state(editor, revision=editor.manifest_revision + 1),
        case_id=UUID("10000000-0000-4000-8000-000000000098"),
    )
    backend = _CoreOverlayApplyBackend(wrong)
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    states: list[OrganizerViewState] = []
    applied: list[tuple[int, object]] = []
    failures: list[tuple[int, object]] = []
    worker.state_changed.connect(states.append)
    worker.core_overlay_tools_applied.connect(
        lambda operation_id, state: applied.append((operation_id, state))
    )
    worker.core_overlay_tools_apply_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    worker.apply_core_overlay_tools(18, editor)

    assert states == []
    assert applied == []
    assert len(failures) == 1 and failures[0][0] == 18


def test_worker_correlates_state_free_abort_of_unusable_editor_presentation() -> None:
    backend = _CoreOverlayAbortBackend()
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    cancelled: list[int] = []
    failures: list[tuple[int, object]] = []
    worker.core_overlay_tools_cancelled.connect(cancelled.append)
    worker.core_overlay_tools_cancel_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )

    worker.abort_core_overlay_tools(19)

    assert backend.aborted
    assert cancelled == [19]
    assert failures == []


def test_shutdown_refusal_dismisses_apply_retry_dialog_and_exposes_save_retry() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        apply_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_apply_requested.disconnect(worker.apply_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()
        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()
        dialog = window._core_overlay_tools_dialog
        assert dialog is not None
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert apply_button is not None

        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        _application().processEvents()
        assert apply_requests and dialog.apply_pending
        window._closing_requested = True

        window._shutdown_refused(ShutdownRefusalKind.SAVE_PENDING)
        _application().processEvents()

        assert window._core_overlay_tools_dialog is None
        assert window._core_overlay_tools_apply_operation_id is None
        assert window._retry_save_button.isVisible()
        assert window._retry_save_button.hasFocus()


def test_malformed_apply_terminal_payloads_restore_a_safe_retry_path() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        apply_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_apply_requested.disconnect(worker.apply_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()
        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()
        dialog = window._core_overlay_tools_dialog
        assert dialog is not None
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert apply_button is not None

        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        first_operation_id = apply_requests[-1][0]
        worker.core_overlay_tools_applied.emit(first_operation_id, object())
        _application().processEvents()

        assert not dialog.apply_pending
        assert apply_button.isEnabled()
        assert "could not save the visual overlays safely" in dialog._status.text()

        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        second_operation_id = apply_requests[-1][0]
        assert second_operation_id != first_operation_id
        worker.core_overlay_tools_apply_failed.emit(second_operation_id, object())
        _application().processEvents()

        assert not dialog.apply_pending
        assert apply_button.isEnabled()
        assert "could not save the visual overlays safely" in dialog._status.text()


def test_shutdown_queues_latest_retained_draft_before_backend_shutdown() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        ordered_requests: list[tuple[str, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_apply_requested.disconnect(worker.apply_core_overlay_tools)
        commands.shutdown_requested.disconnect(worker.shutdown)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_apply_requested.connect(
            lambda _operation_id, state: ordered_requests.append(("apply", state))
        )
        commands.shutdown_requested.connect(lambda: ordered_requests.append(("shutdown", object())))
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()
        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()
        dialog = window._core_overlay_tools_dialog
        assert dialog is not None
        add_button = dialog.findChild(QPushButton, "addCoreOverlayButton")
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        assert add_button is not None and apply_button is not None

        QTest.keyClick(add_button, Qt.Key.Key_Space)
        QTest.keyClick(apply_button, Qt.Key.Key_Space)
        first_draft = dialog.state
        assert ordered_requests == [("apply", first_draft)]
        assert dialog.set_apply_failed("OVERLAY_TEST_SAVE_FAILED", "Try saving again.")
        QTest.keyClick(add_button, Qt.Key.Key_Space)
        left = dialog.findChild(QSpinBox, "coreOverlayLeftSpin")
        assert left is not None
        left.setValue(left.value() + 1_234)
        visible_left = left.value()
        assert dialog.state.selected_record is not None
        assert dialog.state.selected_record.box.x != visible_left

        window.request_shutdown()

        assert ordered_requests[1][0] == "apply"
        latest_draft = cast(CoreOverlayToolsState, ordered_requests[1][1])
        assert latest_draft is dialog.state
        assert latest_draft.selected_record is not None
        assert latest_draft.selected_record.box.x == visible_left
        assert ordered_requests[2][0] == "shutdown"


def test_window_cancels_started_editor_with_forged_announcement() -> None:
    editor = _editor()
    forged = replace(editor)
    object.__setattr__(forged, "announcement", "PRIVATE_ANNOUNCEMENT_<script>C:\\Applicant")
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        abort_requests: list[int] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_abort_requested.disconnect(worker.abort_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_abort_requested.connect(abort_requests.append)
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()

        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], forged)
        _application().processEvents()

        assert window._core_overlay_tools_dialog is None
        assert len(abort_requests) == 1
        assert "PRIVATE_ANNOUNCEMENT" not in window._status_label.text()
        assert "PRIVATE_ANNOUNCEMENT" not in window._error_label.text()


def test_exact_but_uncorrelated_apply_results_keep_dialog_on_safe_retry() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[tuple[int, object]] = []
        apply_requests: list[tuple[int, object]] = []
        commands.core_overlay_tools_begin_requested.disconnect(worker.begin_core_overlay_tools)
        commands.core_overlay_tools_apply_requested.disconnect(worker.apply_core_overlay_tools)
        commands.core_overlay_tools_begin_requested.connect(
            lambda operation_id, page_id: begin_requests.append((operation_id, page_id))
        )
        commands.core_overlay_tools_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        _application().processEvents()
        window._preview_refresh_timer.stop()
        window._request_core_overlay_tools()
        worker.core_overlay_tools_started.emit(begin_requests[0][0], editor)
        _application().processEvents()
        dialog = window._core_overlay_tools_dialog
        assert dialog is not None
        apply_button = dialog.findChild(QPushButton, "applyFormMarksButton")
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert apply_button is not None and status is not None

        wrong_results = (
            replace(
                _organizer_state(editor, revision=editor.manifest_revision + 1),
                case_id=UUID("10000000-0000-4000-8000-000000000099"),
            ),
            _organizer_state(editor, revision=editor.manifest_revision + 99),
        )
        for wrong in wrong_results:
            QTest.keyClick(apply_button, Qt.Key.Key_Space)
            operation_id = apply_requests[-1][0]
            worker.core_overlay_tools_applied.emit(operation_id, wrong)
            _application().processEvents()
            assert window._core_overlay_tools_dialog is dialog
            assert not dialog.apply_pending
            assert apply_button.isEnabled()
            assert "could not save the visual overlays safely" in status.text()
