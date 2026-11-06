"""Accessible presentation for verified offline OCR language selection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.ocr_languages import (
    OCR_LANGUAGE_DISPLAY_NAMES,
    SUPPORTED_OCR_LANGUAGE_CODES,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import adapt_dialog_button_box, set_static_busy

_FOCUS_VISIBLE_TEXT_STYLE = """
QLabel {
    border: 2px solid transparent;
    padding: 2px;
}
QLabel:focus {
    border-color: palette(highlight);
}
"""
_FOCUS_VISIBLE_SCROLL_STYLE = """
QScrollArea {
    border: 2px solid transparent;
}
QScrollArea:focus {
    border-color: palette(highlight);
}
"""
_FOCUS_VISIBLE_PROGRESS_STYLE = """
QProgressBar:focus {
    border: 2px solid palette(highlight);
}
"""


class _RequestKind(StrEnum):
    REFRESH = "refresh"
    SELECT = "select"


@dataclass(frozen=True, slots=True)
class _PendingRequest:
    request_id: int
    kind: _RequestKind
    language_codes: tuple[str, ...] = ()


class OcrLanguageDialog(QDialog):
    """Select only locally verified OCR languages without initiating downloads."""

    refresh_requested = Signal(int)
    selection_requested = Signal(int, object)
    selection_saved = Signal(object)

    def __init__(
        self,
        request_id_factory: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not callable(request_id_factory):
            raise ValueError("request_id_factory must be callable")
        self._request_id_factory = request_id_factory
        self._pending: _PendingRequest | None = None
        self._snapshot: OcrLanguageSnapshot | None = None
        self._initial_focus_pending = True
        self._shutdown_discard = False
        self.setObjectName("ocrLanguageDialog")
        self.setWindowTitle(tr("Manage OCR languages"))
        self.setAccessibleName(tr("Manage OCR languages"))
        self.setAccessibleDescription(
            tr(
                "Review verified local OCR language packs and choose one or more languages. PaperWorkShell does not download language data from this dialog."
            )
        )
        self.setModal(True)
        self.resize(640, 560)

        heading = QLabel(tr("OCR languages"))
        heading.setObjectName("ocrLanguageHeading")
        heading.setAccessibleName(tr("OCR languages"))
        heading_font = heading.font()
        heading_font.setPointSizeF(heading_font.pointSizeF() * 1.3)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setWordWrap(True)

        guidance = QLabel(
            tr(
                "PaperWorkShell uses only installed and verified local OCR language packs. It never downloads language data while checking languages or processing documents."
            )
        )
        guidance.setObjectName("ocrLanguageGuidance")
        guidance.setAccessibleName(tr("OCR language guidance"))
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)

        self._runtime_status = QLabel(tr("Local OCR languages have not been checked yet."))
        self._runtime_status.setObjectName("ocrLanguageRuntimeStatus")
        self._runtime_status.setAccessibleName(tr("Local OCR language status"))
        self._runtime_status.setTextFormat(Qt.TextFormat.PlainText)
        self._runtime_status.setWordWrap(True)

        language_group = QGroupBox(tr("Languages"))
        language_group.setObjectName("ocrLanguageChoicesGroup")
        language_group.setAccessibleDescription(
            tr("Choose one or more languages. A language marked not installed cannot be saved.")
        )
        language_layout = QVBoxLayout(language_group)
        self._choices: dict[str, QCheckBox] = {}
        self._choice_statuses: dict[str, QLabel] = {}
        for code in SUPPORTED_OCR_LANGUAGE_CODES:
            display_name = tr(OCR_LANGUAGE_DISPLAY_NAMES[code])
            choice = QCheckBox(display_name)
            choice.setObjectName(f"ocrLanguageChoice{code.upper()}")
            choice.setAccessibleName(tr("{value1} OCR language", value1=display_name))
            choice.setAccessibleDescription(
                tr("{value1} local OCR language has not been checked yet.", value1=display_name)
            )
            choice.toggled.connect(self._selection_changed)
            status = QLabel(tr("{value1}: Not checked.", value1=display_name))
            status.setObjectName(f"ocrLanguageStatus{code.upper()}")
            status.setAccessibleName(tr("{value1} installation status", value1=display_name))
            status.setTextFormat(Qt.TextFormat.PlainText)
            status.setWordWrap(True)
            language_layout.addWidget(choice)
            language_layout.addWidget(status)
            self._choices[code] = choice
            self._choice_statuses[code] = status

        instructions_group = QGroupBox(tr("Offline OCR packs"))
        instructions_group.setObjectName("ocrLanguageInstructionsGroup")
        instructions_layout = QVBoxLayout(instructions_group)
        instructions = QLabel(
            tr(
                "Optional OCR packs are not distributed by this source build. A packaged release or administrator must provide the exact approved offline procedure. After verified local prestaging, reopen PaperWorkShell and choose Refresh. Do not copy unverified trained-data files into the application."
            )
        )
        instructions.setObjectName("ocrLanguageInstructions")
        instructions.setAccessibleName(tr("Offline OCR language availability instructions"))
        instructions.setTextFormat(Qt.TextFormat.PlainText)
        instructions.setWordWrap(True)
        instructions_layout.addWidget(instructions)

        self._selection_warning = QLabel()
        self._selection_warning.setObjectName("ocrLanguageSelectionWarning")
        self._selection_warning.setAccessibleName(tr("OCR language selection needs attention"))
        self._selection_warning.setAccessibleDescription(
            tr("The current selection includes a language that is not installed and verified.")
        )
        self._selection_warning.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._selection_warning.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._selection_warning.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_warning.setWordWrap(True)
        self._selection_warning.setVisible(False)

        self._error = QLabel()
        self._error.setObjectName("ocrLanguageError")
        self._error.setAccessibleName(tr("OCR language action error"))
        self._error.setAccessibleDescription(
            tr("A safe error code and recovery message for the latest OCR language action.")
        )
        self._error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setWordWrap(True)
        self._error.setVisible(False)

        content = QWidget()
        content.setObjectName("ocrLanguageScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(heading)
        content_layout.addWidget(guidance)
        content_layout.addWidget(self._runtime_status)
        content_layout.addWidget(language_group)
        content_layout.addWidget(self._selection_warning)
        content_layout.addWidget(self._error)
        content_layout.addWidget(instructions_group)
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ocrLanguageScrollArea")
        self._scroll.setAccessibleName(tr("OCR language settings and instructions"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable OCR language choices, status, and offline installation instructions.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setStyleSheet(_FOCUS_VISIBLE_SCROLL_STYLE)
        self._scroll.setWidget(content)

        adapt_localized_content(self._scroll)
        self._progress = QProgressBar()
        self._progress.setObjectName("ocrLanguageProgress")
        self._progress.setAccessibleName(tr("OCR language check progress"))
        self._progress.setAccessibleDescription(
            tr("PaperWorkShell is checking local OCR language resources.")
        )
        self._progress.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._progress.setStyleSheet(_FOCUS_VISIBLE_PROGRESS_STYLE)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)

        buttons = QDialogButtonBox()
        self._buttons = buttons
        adapt_dialog_button_box(buttons)
        buttons.setObjectName("ocrLanguageButtons")
        buttons.setAccessibleName(tr("OCR language actions"))
        self._refresh_button = buttons.addButton(
            tr("Refresh"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._refresh_button.setObjectName("ocrLanguageRefreshButton")
        self._refresh_button.setAccessibleName(tr("Refresh OCR languages"))
        self._refresh_button.setAccessibleDescription(
            tr("Check the local OCR runtime and approved language packs again.")
        )
        self._save_button = buttons.addButton(QDialogButtonBox.StandardButton.Save)
        self._save_button.setObjectName("ocrLanguageSaveButton")
        self._save_button.setText(tr("Save selection"))
        self._save_button.setAccessibleName(tr("Save OCR language selection"))
        self._save_button.setAccessibleDescription(
            tr("Use the checked installed languages for later OCR work.")
        )
        self._cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("ocrLanguageCancelButton")
        self._cancel_button.setAccessibleName(tr("Cancel OCR language changes"))
        self._cancel_button.setAccessibleDescription(
            tr("Close this dialog without changing the current OCR language selection.")
        )
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        self._refresh_button.clicked.connect(self.request_refresh)
        self._save_button.clicked.connect(self._request_selection)
        self._cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._progress)
        layout.addWidget(buttons)

        self._set_tab_order()
        self._update_save_availability()

    def _set_tab_order(self) -> None:
        """Keep recovery before the actions after Qt lays out the vertical footer."""
        # With the application's focus stylesheet, Qt assigns the vertical
        # button box a Cancel proxy. Even a NoFocus box then redirects Tab
        # past Refresh. Individual controls own this explicit tab sequence.
        # QWidget* is nullable in Qt; the PySide6 stub omits that annotation.
        self._buttons.setFocusProxy(cast(QWidget, None))
        tab_order = (
            *(self._choices[code] for code in SUPPORTED_OCR_LANGUAGE_CODES),
            self._selection_warning,
            self._error,
            self._scroll,
            self._progress,
            self._refresh_button,
            self._save_button,
            self._cancel_button,
        )
        for current, following in pairwise(tab_order):
            QWidget.setTabOrder(current, following)

    @property
    def snapshot(self) -> OcrLanguageSnapshot | None:
        """Return the latest complete path-free manager snapshot."""

        return self._snapshot

    @property
    def pending_request_id(self) -> int | None:
        """Return the current correlation identifier for focused tests."""

        return None if self._pending is None else self._pending.request_id

    def request_refresh(self) -> None:
        """Queue one correlated local-only discovery request."""

        if self._pending is not None:
            return
        request_id = self._next_request_id()
        self._pending = _PendingRequest(request_id, _RequestKind.REFRESH)
        self._clear_error()
        self._set_busy(tr("Checking installed OCR languages"))
        self.refresh_requested.emit(request_id)

    def set_snapshot(self, request_id: int, snapshot: object) -> bool:
        """Accept only the exact typed result for the current request."""

        pending = self._pending
        if (
            type(request_id) is not int
            or type(snapshot) is not OcrLanguageSnapshot
            or pending is None
            or pending.request_id != request_id
        ):
            return False
        if pending.kind is _RequestKind.SELECT and (
            snapshot.selected_language_codes != pending.language_codes
        ):
            return False
        self._pending = None
        self._snapshot = snapshot
        self._finish_busy()
        self._render_snapshot(snapshot)
        if pending.kind is _RequestKind.SELECT:
            self.selection_saved.emit(snapshot)
            self.accept()
            return True
        self._focus_after_refresh(snapshot)
        return True

    def set_failure(self, request_id: int, code: object, message: object) -> bool:
        """Present one correlated sanitized failure and a keyboard recovery target."""

        pending = self._pending
        if (
            type(request_id) is not int
            or type(code) is not str
            or not code
            or type(message) is not str
            or not message
            or pending is None
            or pending.request_id != request_id
        ):
            return False
        self._snapshot = None
        self._pending = None
        self._finish_busy()
        self._render_unknown_after_failure()
        self._error.setText(f"{code}: {message}")
        self._error.setAccessibleName(
            tr("OCR language error. {value1}: {value2}", value1=code, value2=message)
        )
        self._error.setVisible(True)
        self._refresh_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._refresh_button, 8, 8)
        self._announce(tr("OCR language action failed. {value1}", value1=message), assertive=True)
        self._update_save_availability()
        return True

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        """Use the non-mutating action as the safe initial focus target."""

        super().showEvent(event)
        self._set_tab_order()
        if self._initial_focus_pending:
            self._initial_focus_pending = False
            self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def reject(self) -> None:
        """Do not silently discard a selection mutation already queued to the backend."""

        pending = self._pending
        if (
            not self._shutdown_discard
            and pending is not None
            and pending.kind is _RequestKind.SELECT
        ):
            self._announce(
                tr(
                    "Saving the OCR language selection. Wait for this action to finish before closing."
                ),
                assertive=True,
            )
            self._progress.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        """Keep the dialog open while an already-queued selection is committing."""

        pending = self._pending
        if (
            not self._shutdown_discard
            and pending is not None
            and pending.kind is _RequestKind.SELECT
        ):
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)

    def discard_for_shutdown(self) -> None:
        """Dismiss presentation during process shutdown without promising cancellation."""

        self._shutdown_discard = True
        super().reject()

    def _request_selection(self) -> None:
        if self._pending is not None or not self._save_button.isEnabled():
            return
        language_codes = tuple(
            code for code in SUPPORTED_OCR_LANGUAGE_CODES if self._choices[code].isChecked()
        )
        request_id = self._next_request_id()
        self._pending = _PendingRequest(
            request_id,
            _RequestKind.SELECT,
            language_codes,
        )
        self._clear_error()
        self._set_busy(tr("Saving OCR language selection"))
        self.selection_requested.emit(request_id, language_codes)

    def _next_request_id(self) -> int:
        try:
            value = self._request_id_factory()
        except BaseException:
            raise RuntimeError("OCR language request identifier is unavailable") from None
        if type(value) is not int or value <= 0:
            raise RuntimeError("OCR language request identifier is invalid")
        return value

    def _set_busy(self, message: str) -> None:
        for choice in self._choices.values():
            choice.setEnabled(False)
        self._refresh_button.setEnabled(False)
        self._save_button.setEnabled(False)
        pending = self._pending
        self._cancel_button.setEnabled(pending is None or pending.kind is _RequestKind.REFRESH)
        self._progress.setVisible(True)
        set_static_busy(self._progress)
        self._progress.setFormat(message)
        self._progress.setAccessibleDescription(
            tr("{value1}. This check uses local files only.", value1=message)
        )
        if pending is not None and pending.kind is _RequestKind.SELECT:
            self._progress.setFocus(Qt.FocusReason.OtherFocusReason)
        self._announce(tr("{value1}. This check uses local files only.", value1=message))

    def _finish_busy(self) -> None:
        self._progress.setVisible(False)
        self._progress.setRange(0, 1)
        for choice in self._choices.values():
            choice.setEnabled(True)
        self._refresh_button.setEnabled(True)
        self._cancel_button.setEnabled(True)

    def _render_snapshot(self, snapshot: OcrLanguageSnapshot) -> None:
        installed = snapshot.installed_language_codes
        selected = snapshot.selected_language_codes
        runtime_message = {
            OcrLanguageRuntimeStatus.UNCHECKED: tr(
                "Local OCR languages have not been checked yet."
            ),
            OcrLanguageRuntimeStatus.AVAILABLE: tr("Local OCR runtime check complete."),
            OcrLanguageRuntimeStatus.UNAVAILABLE: (
                tr(
                    "Local OCR is unavailable. This source build has no install action. Use the verified offline procedure supplied by a packaged release or administrator, then Refresh."
                )
            ),
            OcrLanguageRuntimeStatus.INVALID: (
                tr(
                    "Local OCR resources could not be verified. Close PaperWorkShell and ask an administrator or packaged-release provider to restore the approved offline files, then reopen and Refresh."
                )
            ),
        }[snapshot.runtime_status]
        self._runtime_status.setText(runtime_message)
        self._runtime_status.setAccessibleName(
            tr("OCR availability. {value1}", value1=runtime_message)
        )
        self._runtime_status.setAccessibleDescription(runtime_message)
        for option in snapshot.language_options:
            choice = self._choices[option.code]
            status = self._choice_statuses[option.code]
            choice.blockSignals(True)
            choice.setChecked(option.code in selected)
            choice.blockSignals(False)
            installation = self._installation_text(snapshot, option.code)
            status.setText(f"{tr(option.display_name)}: {installation}")
            status.setAccessibleName(status.text())
            status.setAccessibleDescription(installation)
            selected_text = tr("Selected.") if option.code in selected else tr("Not selected.")
            choice.setAccessibleDescription(
                tr(
                    "{value1} OCR language. {value2} {value3}",
                    value1=tr(option.display_name),
                    value2=installation,
                    value3=selected_text,
                )
            )
        missing = tuple(code for code in selected if code not in installed)
        if missing:
            self._selection_warning.setText(self._missing_selection_text(snapshot, missing))
            self._selection_warning.setAccessibleName(self._selection_warning.text())
            self._selection_warning.setVisible(True)
        else:
            self._selection_warning.setVisible(False)
        self._update_save_availability()
        available_count = len(installed)
        noun = tr("language") if available_count == 1 else tr("languages")
        if snapshot.runtime_status is OcrLanguageRuntimeStatus.UNCHECKED:
            self._announce(tr("OCR language availability has not been checked."))
        else:
            self._announce(
                tr(
                    "OCR language check complete. {value1} installed and verified {value2} available.",
                    value1=available_count,
                    value2=noun,
                )
            )

    def _render_unknown_after_failure(self) -> None:
        message = tr("OCR language availability is unknown because the latest local check failed.")
        self._runtime_status.setText(message)
        self._runtime_status.setAccessibleName(tr("OCR availability. {value1}", value1=message))
        self._runtime_status.setAccessibleDescription(message)
        for code in SUPPORTED_OCR_LANGUAGE_CODES:
            display_name = tr(OCR_LANGUAGE_DISPLAY_NAMES[code])
            choice = self._choices[code]
            choice.blockSignals(True)
            choice.setChecked(False)
            choice.setEnabled(False)
            choice.blockSignals(False)
            status = self._choice_statuses[code]
            status.setText(tr("{value1}: Availability unknown.", value1=display_name))
            status.setAccessibleName(status.text())
            status.setAccessibleDescription(
                tr("Availability unknown because the latest check failed.")
            )
            choice.setAccessibleDescription(
                tr(
                    "{value1} OCR language. Availability unknown. Not selected.",
                    value1=display_name,
                )
            )
        self._selection_warning.setVisible(False)

    def _selection_changed(self, _checked: bool) -> None:
        snapshot = self._snapshot
        if snapshot is not None:
            installed = snapshot.installed_language_codes
            for code in SUPPORTED_OCR_LANGUAGE_CODES:
                choice = self._choices[code]
                installation = self._installation_text(snapshot, code)
                selected = tr("Selected.") if choice.isChecked() else tr("Not selected.")
                choice.setAccessibleDescription(
                    tr(
                        "{value1} OCR language. {value2} {value3}",
                        value1=tr(OCR_LANGUAGE_DISPLAY_NAMES[code]),
                        value2=installation,
                        value3=selected,
                    )
                )
            missing_checked = tuple(
                code
                for code in SUPPORTED_OCR_LANGUAGE_CODES
                if self._choices[code].isChecked() and code not in installed
            )
            if missing_checked:
                self._selection_warning.setText(
                    self._missing_selection_text(snapshot, missing_checked)
                )
                self._selection_warning.setAccessibleName(self._selection_warning.text())
                self._selection_warning.setVisible(True)
            else:
                self._selection_warning.setVisible(False)
        self._update_save_availability()

    @staticmethod
    def _installation_text(snapshot: OcrLanguageSnapshot, code: str) -> str:
        if code in snapshot.installed_language_codes:
            return tr("Installed and verified.")
        if snapshot.runtime_status is OcrLanguageRuntimeStatus.UNCHECKED:
            return tr("Not checked.")
        if snapshot.runtime_status is OcrLanguageRuntimeStatus.INVALID:
            return tr("Not installed or could not be verified.")
        return tr("Not installed.")

    @staticmethod
    def _missing_selection_text(
        snapshot: OcrLanguageSnapshot,
        missing: tuple[str, ...],
    ) -> str:
        names = ", ".join(tr(OCR_LANGUAGE_DISPLAY_NAMES[code]) for code in missing)
        verb = tr("is") if len(missing) == 1 else tr("are")
        condition = (
            tr("not installed or could not be verified")
            if snapshot.runtime_status is OcrLanguageRuntimeStatus.INVALID
            else tr("not installed and verified")
        )
        return tr(
            "Selection needs attention: {value1} {value2} {value3}. Choose only installed languages before saving. No case content was changed.",
            value1=names,
            value2=verb,
            value3=condition,
        )

    def _update_save_availability(self) -> None:
        snapshot = self._snapshot
        checked = frozenset(
            code for code in SUPPORTED_OCR_LANGUAGE_CODES if self._choices[code].isChecked()
        )
        self._save_button.setEnabled(
            self._pending is None
            and snapshot is not None
            and snapshot.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
            and bool(checked)
            and checked.issubset(snapshot.installed_language_codes)
        )
        self._set_tab_order()

    def _focus_after_refresh(self, snapshot: OcrLanguageSnapshot) -> None:
        if self._selection_warning.isVisible():
            self._selection_warning.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._selection_warning, 8, 8)
            return
        for code in snapshot.selected_language_codes:
            choice = self._choices.get(code)
            if choice is not None and choice.isEnabled():
                choice.setFocus(Qt.FocusReason.OtherFocusReason)
                self._scroll.ensureWidgetVisible(choice, 8, 8)
                return
        self._refresh_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_error(self) -> None:
        self._error.setVisible(False)

    def _announce(self, message: str, *, assertive: bool = False) -> None:
        message = tr(message)
        announcement = QAccessibleAnnouncementEvent(self._runtime_status, message)
        announcement.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(announcement)
