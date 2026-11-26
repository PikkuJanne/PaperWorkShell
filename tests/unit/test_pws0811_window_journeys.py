"""Visible naming/export dialogs and retained feedback, without a document worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
)

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.ui.backend import OperationCancellation, UiProgress
from paperworkshell.ui.models import PreviewState
from tests.helpers.qt_layout import wait_for_geometry
from tests.unit.test_mixed_preview_window import _pdf_result
from tests.unit.test_package_export_window import CASE_ID, PAGE_ID, _application, _window
from tests.unit.test_post_export_window import _assisted


def _drive_modal(action: Callable[[], None], interact: Callable[[QDialog], None]) -> None:
    """Observe the actual modal loop and close safely even if an assertion fails."""

    errors: list[Exception] = []
    observed: list[QDialog] = []

    def drive() -> None:
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog), "The action must show its actual dialog."
            observed.append(dialog)
            interact(dialog)
        except Exception as error:
            errors.append(error)
        finally:
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, drive)
    action()
    _application().processEvents()
    if errors:
        raise errors[0]
    assert len(observed) == 1


def _name_dialog(dialog: QDialog) -> tuple[QLineEdit, QPushButton, QPushButton]:
    assert isinstance(dialog, QInputDialog)
    assert dialog.objectName() == "caseNameDialog"
    field = dialog.findChild(QLineEdit, "caseNameField")
    buttons = dialog.findChild(QDialogButtonBox)
    assert field is not None and buttons is not None
    assert field.accessibleName() == "Case name"
    save = buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    assert save is not None and cancel is not None
    return field, save, cancel


def test_new_case_requires_a_name_and_cancel_never_emits_a_create_request() -> None:
    with _window() as (window, commands, worker):
        commands.create_named_case_requested.disconnect(worker.create_case)
        commands.create_case_requested.disconnect(worker.create_case)
        names: list[str] = []
        unnamed: list[bool] = []
        commands.create_named_case_requested.connect(names.append)
        commands.create_case_requested.connect(lambda: unnamed.append(True))
        worker.state_changed.emit(OrganizerViewState(None, None, (), False, False, False))
        button = window.findChild(QPushButton, "createCaseButton")
        assert button is not None and button.isEnabled()

        def cancel(dialog: QDialog) -> None:
            field, save, dismiss = _name_dialog(dialog)
            assert field.isVisible() and field.text() == ""
            assert not save.isEnabled()
            field.setText("   ")
            assert not save.isEnabled()
            QTest.mouseClick(dismiss, Qt.MouseButton.LeftButton)

        _drive_modal(button.click, cancel)
        assert names == [] and unnamed == []

        def create(dialog: QDialog) -> None:
            field, save, _dismiss = _name_dialog(dialog)
            field.setFocus()
            QTest.keyClicks(field, "  Housing application  ")
            assert save.isEnabled()
            QTest.mouseClick(save, Qt.MouseButton.LeftButton)

        _drive_modal(button.click, create)
        assert names == ["Housing application"] and unnamed == []


def test_rename_starts_with_the_current_name_and_preserves_case_identity() -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        state = replace(window._state, case_name="Housing application")
        assert state.revision is not None
        worker.state_changed.emit(state)
        commands.rename_case_requested.disconnect(worker.rename_case)
        names: list[str] = []
        commands.rename_case_requested.connect(names.append)
        button = window.findChild(QPushButton, "renameCaseButton")
        assert button is not None and button.isEnabled()

        def rename(dialog: QDialog) -> None:
            field, save, _dismiss = _name_dialog(dialog)
            assert field.text() == "Housing application"
            field.setText("Äänekoski application")
            save.click()

        _drive_modal(button.click, rename)
        assert names == ["Äänekoski application"]
        assert window._state is not None and window._state.case_id == state.case_id
        worker.state_changed.emit(replace(state, case_name=names[0], revision=state.revision + 1))
        heading = window.findChild(QLabel, "organizerHeading")
        assert heading is not None and "Äänekoski application" in heading.text()


def test_cancel_export_options_avoids_destination_picker_and_both_export_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        commands.searchable_pdf_export_requested.disconnect(worker.export_searchable_pdf)
        requests: list[str] = []
        pickers: list[bool] = []
        commands.pages_pdf_export_requested.connect(lambda *_args: requests.append("ordinary"))
        commands.searchable_pdf_export_requested.connect(lambda *_args: requests.append("ocr"))

        def pick(*_args: object, **_kwargs: object) -> tuple[str, str]:
            pickers.append(True)
            return "unwanted.pdf", ""

        monkeypatch.setattr(QFileDialog, "getSaveFileName", pick)
        button = window.findChild(QPushButton, "pagesPdfExportButton")
        assert button is not None and button.isEnabled()

        def cancel(dialog: QDialog) -> None:
            assert dialog.objectName() == "ordinaryPdfExportOptions"
            searchable = dialog.findChild(QCheckBox, "makeScannedTextSearchable")
            buttons = dialog.findChild(QDialogButtonBox)
            assert searchable is not None and buttons is not None
            assert searchable.text() == "Make scanned text searchable"
            assert not searchable.isChecked()
            searchable.setChecked(True)
            buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

        _drive_modal(button.click, cancel)
        assert requests == [] and pickers == []
        assert window._active_operation is None


def test_case_heading_displays_user_text_literally() -> None:
    with _window() as (window, _commands, worker):
        assert window._state is not None
        case_name = "<b>Housing & support</b>"
        worker.state_changed.emit(replace(window._state, case_name=case_name))
        heading = window.findChild(QLabel, "organizerHeading")
        assert heading is not None
        assert heading.textFormat() is Qt.TextFormat.PlainText
        assert heading.text() == case_name
        assert case_name in heading.accessibleName()


def test_case_close_announces_saving_and_cleanup_until_close_finishes() -> None:
    with _window() as (window, commands, worker):
        commands.close_case_requested.disconnect(worker.close_case)
        requests: list[bool] = []
        commands.close_case_requested.connect(lambda: requests.append(True))
        assert window._state is not None and window._state.case_id is not None
        window._request_close_case()
        _application().processEvents()
        assert requests == [True]
        assert window._case_close_pending
        assert window._status_label.text() == (
            "Closing the case. Saving changes and finishing cleanup."
        )
        assert window._status_label.isVisible()
        assert not window._close_case_button.isEnabled()
        assert not window._pages_pdf_export_button.isEnabled()
        worker.case_close_finished.emit()
        assert not window._case_close_pending


def test_assisted_dialog_cancels_without_creation_and_sends_the_entered_case_name() -> None:
    with _window() as (window, commands, worker):
        worker.state_changed.emit(OrganizerViewState.empty())
        commands.create_named_assisted_case_requested.disconnect(worker.create_assisted_case)
        commands.create_assisted_case_requested.disconnect(worker.create_assisted_case)
        named: list[tuple[int, str, bool, str]] = []
        legacy: list[bool] = []
        commands.create_named_assisted_case_requested.connect(
            lambda days, phrase, consent, name: named.append((days, phrase, consent, name))
        )
        commands.create_assisted_case_requested.connect(lambda *_args: legacy.append(True))

        def cancel(dialog: QDialog) -> None:
            assert dialog.objectName() == "assistedConsentDialog"
            field = dialog.findChild(QLineEdit, "assistedCaseName")
            assert field is not None and field.text() == ""
            dialog.reject()

        _drive_modal(window._assisted_session.start, cancel)
        assert named == [] and legacy == []

        def create(dialog: QDialog) -> None:
            name = dialog.findChild(QLineEdit, "assistedCaseName")
            first = dialog.findChild(QLineEdit, "assistedPassphrase")
            repeat = dialog.findChild(QLineEdit, "assistedPassphraseConfirmation")
            consent = dialog.findChild(QCheckBox, "assistedConsentConfirmation")
            assert name is not None and first is not None and repeat is not None
            assert consent is not None
            name.setFocus()
            QTest.keyClicks(name, "Shared application")
            first.setText("synthetic case phrase")
            repeat.setText("synthetic case phrase")
            consent.setChecked(True)
            dialog.accept()

        _drive_modal(window._assisted_session.start, create)
        assert named == [(7, "synthetic case phrase", True, "Shared application")]
        assert legacy == []


@pytest.mark.parametrize("filename", ("forms & support.pdf", "Ä" * 230 + ".pdf"))
def test_import_keeps_a_bounded_filename_target_during_progress_and_clears_it_on_case_change(
    filename: str,
) -> None:
    with _window() as (window, commands, worker):
        commands.import_requested.disconnect(worker.import_source)
        window.resize(520, 420)
        window._start_import(Path("C:/synthetic-private") / filename, filename)
        active = window._active_operation
        assert active is not None
        target = window.findChild(QLabel, "importFileTarget")
        assert target is not None and target.isVisible()
        assert filename in target.text() and filename in target.accessibleName()
        assert "synthetic-private" not in target.text()
        assert target.textFormat() is Qt.TextFormat.PlainText
        worker.progress_changed.emit(
            UiProgress("Import", "Inspecting", 0, 1, True, active.operation_id)
        )
        _application().processEvents()
        assert target.isVisible() and filename in target.accessibleName()
        assert "Inspecting" in window._status_label.text()
        assert target.heightForWidth(160) == target.fontMetrics().height() + 8
        assert window.rect().contains(target.mapTo(window, target.rect().bottomRight()))
        worker.operation_cancelled.emit(active.operation_id, "Import cancelled.")
        worker.operation_finished.emit(active.operation_id)
        worker.state_changed.emit(OrganizerViewState.empty())
        worker.status_changed.emit("Case closed. All queued changes were saved.")
        assert target.text() == "" and not target.isVisible()
        assert target.accessibleName() == target.accessibleDescription() == ""
        assert filename not in window._status_label.accessibleName()


@pytest.mark.parametrize("action", ("name", "export"))
def test_enlarged_name_and_export_dialogs_keep_primary_actions_inside_a_narrow_window(
    action: str,
) -> None:
    app = _application()
    previous_font = QFont(app.font())
    enlarged = QFont(previous_font)
    enlarged.setPointSizeF(max(24, previous_font.pointSizeF() * 2))
    app.setFont(enlarged)
    try:
        with _window() as (window, _commands, _worker):

            def inspect(dialog: QDialog) -> None:
                dialog.resize(520, 360)
                app.processEvents()
                buttons = dialog.findChild(QDialogButtonBox)
                assert buttons is not None
                assert dialog.width() <= 520 and dialog.height() <= 360
                for button in buttons.buttons():
                    assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
                    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
                if action == "export":
                    scroll = dialog.findChild(QScrollArea, "ordinaryPdfExportScrollArea")
                    assert scroll is not None and scroll.verticalScrollBar().maximum() > 0
                    save = buttons.button(QDialogButtonBox.StandardButton.Save)
                    assert save is not None and save.text().replace("\n", " ") == "Choose PDF file…"
                else:
                    field = dialog.findChild(QLineEdit, "caseNameField")
                    assert field is not None and field.isVisible()

            def trigger() -> None:
                if action == "name":
                    window._ask_case_name("Rename case", "Synthetic case")
                else:
                    window._ask_pdf_export_options()

            _drive_modal(trigger, inspect)
    finally:
        app.setFont(previous_font)


@pytest.mark.parametrize("point_size", (9, 12, 24))
def test_name_dialog_footer_stays_content_sized_after_native_layout_and_resizing(
    point_size: int,
) -> None:
    """A roomy name dialog must not turn ordinary actions into tall empty tiles."""

    app = _application()
    previous_font = QFont(app.font())
    font = QFont(previous_font)
    font.setPointSize(point_size)
    app.setFont(font)
    try:
        with _window() as (window, _commands, _worker):

            def inspect(dialog: QDialog) -> None:
                field, save, cancel = _name_dialog(dialog)
                footer = dialog.findChild(QDialogButtonBox)
                assert footer is not None

                def content_sized() -> bool:
                    layout = footer.layout()
                    assert layout is not None
                    return (
                        all(
                            button.height()
                            <= max(
                                button.sizeHint().height(),
                                len(button.text().splitlines()) * button.fontMetrics().lineSpacing()
                                + 24,
                            )
                            for button in (save, cancel)
                        )
                        and footer.height() <= layout.minimumSize().height() + 2
                    )

                wait_for_geometry(content_sized)
                if point_size <= 12:
                    assert dialog.height() <= 180, "A short case-name form should open compactly."
                for width, height in ((520, 340), (360, 520), (520, 360)):
                    dialog.resize(width, height)
                    wait_for_geometry(content_sized)
                    assert dialog.width() <= width
                    assert footer.rect().contains(save.geometry())
                    assert footer.rect().contains(cancel.geometry())
                    assert not save.geometry().intersects(cancel.geometry())
                    for button in (save, cancel):
                        assert (
                            button.width()
                            >= max(
                                button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                                for line in button.text().splitlines()
                            )
                            + 12
                        )
                    assert field.isVisible()
                    assert dialog.rect().contains(field.mapTo(dialog, field.rect().bottomRight()))
                field.setText("Housing application")
                assert save.isEnabled()
                for button in (save, cancel):
                    button.setFocus(Qt.FocusReason.OtherFocusReason)
                    wait_for_geometry(
                        lambda: (
                            content_sized()
                            and footer.rect().contains(save.geometry())
                            and footer.rect().contains(cancel.geometry())
                        )
                    )
                    assert button.hasFocus()
                QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)

            def trigger() -> None:
                window._ask_case_name("New case", "")

            _drive_modal(trigger, inspect)
    finally:
        app.setFont(previous_font)


def test_searchable_option_routes_the_actual_choice_to_a_separate_export_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _window() as (window, commands, worker):
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        commands.searchable_pdf_export_requested.disconnect(worker.export_searchable_pdf)
        requests: list[tuple[int, Path, OperationCancellation]] = []
        ordinary: list[bool] = []
        commands.searchable_pdf_export_requested.connect(
            lambda operation, destination, cancellation: requests.append(
                (operation, destination, cancellation)
            )
        )
        commands.pages_pdf_export_requested.connect(lambda *_args: ordinary.append(True))
        destination = tmp_path / "searchable.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(destination), "")
        )
        button = window.findChild(QPushButton, "pagesPdfExportButton")
        assert button is not None

        def choose(dialog: QDialog) -> None:
            searchable = dialog.findChild(QCheckBox, "makeScannedTextSearchable")
            buttons = dialog.findChild(QDialogButtonBox)
            scroll = dialog.findChild(QScrollArea, "ordinaryPdfExportScrollArea")
            assert searchable is not None and buttons is not None and scroll is not None
            searchable.setFocus(Qt.FocusReason.TabFocusReason)
            scroll.ensureWidgetVisible(searchable)
            wait_for_geometry(
                lambda: (
                    searchable.isVisible()
                    and searchable.isEnabled()
                    and searchable.hasFocus()
                    and scroll.viewport()
                    .rect()
                    .contains(searchable.mapTo(scroll.viewport(), searchable.rect().topLeft()))
                    and scroll.viewport()
                    .rect()
                    .contains(searchable.mapTo(scroll.viewport(), searchable.rect().bottomRight()))
                )
            )
            QTest.keyClick(searchable, Qt.Key.Key_Space)
            assert searchable.isChecked()
            buttons.button(QDialogButtonBox.StandardButton.Save).click()

        _drive_modal(button.click, choose)
        assert len(requests) == 1 and ordinary == []
        assert requests[0][1] == destination
        assert (
            not destination.exists()
        )  # This test observes the command; it never fabricates an output.
        worker.operation_cancelled.emit(requests[0][0], "PDF export cancelled before completion.")
        worker.operation_finished.emit(requests[0][0])


def test_selection_summary_keeps_filename_when_focus_and_output_position_change() -> None:
    with _window() as (window, _commands, worker):
        pages = (
            OrganizerPageView(
                PAGE_ID,
                1,
                0,
                False,
                document_number=1,
                source_page_number=3,
                source_display_name="FORM Ä.PDF",
            ),
            OrganizerPageView(
                UUID("a4000000-0000-4000-8000-000000000014"),
                2,
                0,
                False,
                document_number=2,
                source_page_number=1,
                source_display_name="scan.png",
            ),
        )
        state = OrganizerViewState(CASE_ID, 3, pages, True, True, False, case_name="Named case")
        worker.state_changed.emit(state)
        window._page_view.setCurrentIndex(window._model.index(1, 0))
        summary = window.findChild(QLabel, "selectedPageSummary")
        assert summary is not None
        assert "scan.png" in summary.text() and "output page 2" in summary.text()
        window._rotate_button.setFocus()
        _application().processEvents()
        assert "scan.png" in summary.text() and window._selected_page_id() == pages[1].page_id
        reordered = (replace(pages[1], position=1), replace(pages[0], position=2))
        worker.state_changed.emit(replace(state, pages=reordered, revision=4))
        assert window._selected_page_id() == pages[1].page_id
        assert "scan.png" in summary.text() and "output page 1" in summary.text()


def test_status_is_visible_while_workflow_scrolls_and_hidden_behind_privacy_curtain() -> None:
    with _window() as (window, _commands, worker):
        window.resize(900, 520)
        app = _application()
        app.processEvents()
        status = window.findChild(QGroupBox, "operationStatusGroup")
        assert status is not None and status.isVisible()
        scroll = window._application_scroll
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        assert window.rect().contains(status.mapTo(window, status.rect().center()))
        assert window.rect().contains(status.mapTo(window, status.rect().bottomRight()))
        _assisted(window)
        try:
            window._assisted_session.cover()
            worker.status_changed.emit("A delayed status must stay private.")
            assert not status.isVisible()
            assert not window._status_label.isVisible()
            assert window._assisted_session._curtain is not None
            assert window._assisted_session._curtain.isVisible()
        finally:
            window._assisted_session.changed(None)


def test_verified_export_name_count_and_open_actions_survive_a_later_preview(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _window() as (window, commands, worker):
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        commands.thumbnails_requested.disconnect(worker.get_thumbnails)
        requests: list[tuple[int, Path, OperationCancellation]] = []
        commands.pages_pdf_export_requested.connect(
            lambda operation, destination, cancellation: requests.append(
                (operation, destination, cancellation)
            )
        )
        pages = tuple(
            OrganizerPageView(UUID(f"a4000000-0000-4000-8000-{13 + i:012d}"), i + 1, 0, False)
            for i in range(3)
        )
        state = OrganizerViewState(CASE_ID, 4, pages, False, False, False)
        worker.state_changed.emit(state)
        destination = tmp_path / "basic confirmed.pdf"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(destination), "")
        )
        button = window.findChild(QPushButton, "pagesPdfExportButton")
        assert button is not None

        def choose(dialog: QDialog) -> None:
            buttons = dialog.findChild(QDialogButtonBox)
            assert buttons is not None
            buttons.button(QDialogButtonBox.StandardButton.Save).click()

        _drive_modal(button.click, choose)
        operation = requests[0][0]
        worker.pages_pdf_export_saved.emit(operation + 100, 3)
        result = window.findChild(QLabel, "lastResultLabel")
        assert result is not None and not result.isVisible()
        worker.pages_pdf_export_saved.emit(operation, 3)
        worker.operation_status_changed.emit(
            operation, "The verified PDF was saved and recorded in case history."
        )
        worker.operation_finished.emit(operation)
        assert "basic confirmed.pdf" in result.text() and "3 pages" in result.text()
        retained_result = result.text()
        worker.state_changed.emit(
            replace(state, pages=(replace(pages[0], preview_supported=True), *pages[1:]))
        )
        window._preview_refresh_timer.stop()
        assert window._state is not None
        window._request_previews(window._state)
        active = window._active_operation
        assert active is not None
        preview_result = _pdf_result()
        preview_result = replace(
            preview_result,
            request=replace(preview_result.request, case_id=CASE_ID, page_id=pages[0].page_id),
            cache_key=replace(preview_result.cache_key, case_id=CASE_ID, page_id=pages[0].page_id),
            snapshot=replace(preview_result.snapshot, case_id=CASE_ID, page_id=pages[0].page_id),
        )
        worker.thumbnails_ready.emit(
            active.operation_id, window._thumbnail_generation, (preview_result,)
        )
        worker.operation_status_changed.emit(active.operation_id, "Page previews ready.")
        worker.operation_finished.emit(active.operation_id)
        window._preview_refresh_timer.stop()
        assert window._model.preview_state(pages[0].page_id) is PreviewState.READY
        assert result.text() == retained_result
        opened: list[QUrl] = []

        def open_url(url: QUrl) -> bool:
            opened.append(QUrl(url))
            return True

        monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
        pdf = window.findChild(QPushButton, "openExportedPdfButton")
        folder = window.findChild(QPushButton, "openExportFolderButton")
        assert pdf is not None and folder is not None and pdf.isVisible() and folder.isVisible()
        pdf.click()
        folder.click()
        assert [Path(url.toLocalFile()) for url in opened] == [destination, destination.parent]
