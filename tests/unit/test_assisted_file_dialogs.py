"""Assisted file pickers stay inside the Qt-owned privacy boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QWidget

from paperworkshell.domain.assisted_use import AssistedUseSnapshot, AssistedUseStatus
from paperworkshell.ui.removal_export_review import RemovalExportReviewDialog
from paperworkshell.ui.window import _PdfExportOptions
from tests.unit.test_package_export_window import CASE_ID, _application, _open, _window
from tests.unit.test_removal_export_desktop import _backend


class _PickerOpened(Exception):
    """Stop at the actual chooser boundary without selecting or reading a file."""


_Picker = Literal["import", "pages", "removal", "package", "compression", "summary", "signature"]


@pytest.mark.parametrize("assisted", (False, True))
@pytest.mark.parametrize(
    "picker", ("import", "pages", "removal", "package", "compression", "summary", "signature")
)
def test_every_chooser_preserves_policy_and_uses_qt_ownership_during_assistance(
    picker: _Picker, assisted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: _PdfExportOptions(False))
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        if assisted:
            window._assisted_session.changed(
                AssistedUseSnapshot(
                    CASE_ID, AssistedUseStatus.UNLOCKED, datetime.now(UTC) + timedelta(days=7), 7
                )
            )
            window._assisted_session._timer.stop()
        # This unit check exercises chooser policy, with foreground explicitly
        # active. Offscreen Qt can otherwise emit inactive while constructing an
        # owned modal. The separate foreground-loss test still proves covering.
        monkeypatch.setattr(
            type(_application()),
            "applicationState",
            staticmethod(lambda: Qt.ApplicationState.ApplicationActive),
        )
        window.activateWindow()
        _application().processEvents()
        if picker in ("package", "compression", "summary"):
            _open(window, commands, worker)
            assert window._package_review_state is not None
            if picker == "compression":
                window._package_review_state = replace(
                    window._package_review_state, compression_allowed=True
                )
        if picker == "removal":
            backend, _repository, _exporter = _backend()
            review = backend.begin_removal_export()
            dialog = RemovalExportReviewDialog(review, window)
            checkbox = dialog.findChild(QCheckBox, "removalExportReviewedCheckBox")
            assert checkbox is not None
            checkbox.setChecked(True)
            window._removal_review_dialog = dialog

        def reached_picker(*args: object, **kwargs: object) -> None:
            assert args and isinstance(args[0], QWidget)
            directory = picker in ("package", "compression")
            default_options = (
                QFileDialog.Option.ShowDirsOnly if directory else QFileDialog.Option(0)
            )
            options = kwargs.get(
                "options", args[3] if len(args) > 3 and directory else default_options
            )
            assert isinstance(options, QFileDialog.Option)
            assert bool(options & QFileDialog.Option.DontUseNativeDialog) is assisted
            if picker in ("pages", "removal"):
                assert options & QFileDialog.Option.DontConfirmOverwrite
            if picker in ("package", "compression"):
                assert options & QFileDialog.Option.ShowDirsOnly
            raise _PickerOpened

        method = (
            "getExistingDirectory"
            if picker in ("package", "compression")
            else "getOpenFileName"
            if picker in ("import", "signature")
            else "getSaveFileName"
        )
        monkeypatch.setattr(QFileDialog, method, reached_picker)
        with pytest.raises(_PickerOpened):
            if picker == "import":
                window._choose_import()
            elif picker == "pages":
                monkeypatch.setattr(
                    window, "_ask_pdf_export_options", lambda: _PdfExportOptions(False)
                )
                window._choose_pages_pdf_export()
            elif picker == "removal":
                window._choose_removal_destination(False)
            elif picker == "package":
                window._choose_package_destination(False, True)
            elif picker == "compression":
                window._choose_compression_destination(False, True)
            elif picker == "summary":
                window._choose_package_summary_destination()
            else:
                window._choose_signature_image()


def test_genuine_qt_owned_picker_is_hidden_on_external_foreground_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: _PdfExportOptions(False))
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        controller = window._assisted_session
        controller.changed(
            AssistedUseSnapshot(
                CASE_ID, AssistedUseStatus.UNLOCKED, datetime.now(UTC) + timedelta(days=7), 7
            )
        )
        controller._timer.stop()
        picker = QFileDialog(window)
        picker.setOption(QFileDialog.Option.DontUseNativeDialog)
        picker.setModal(True)
        picker.show()
        application = _application()
        application.processEvents()
        assert picker.isVisible() and window._owns_ui_widget(picker)
        monkeypatch.setattr(
            type(application),
            "applicationState",
            staticmethod(lambda: Qt.ApplicationState.ApplicationInactive),
        )
        controller.eventFilter(application, QEvent(QEvent.Type.ApplicationStateChange))
        assert controller.locked
        assert not picker.isVisible()
        assert not window._application_scroll.isVisible()
        assert controller._curtain is not None and controller._curtain.isVisible()
        controller.changed(None)
