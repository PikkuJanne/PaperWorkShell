"""PWS-0205 correlated OCR language dialog behavior tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel, QPushButton

from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.ui.ocr_languages import OcrLanguageDialog


class _Discovery:
    def __init__(self, results: tuple[OcrLanguageDiscoveryResult, ...]) -> None:
        self._results = iter(results)

    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return next(self._results)


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog(ids: tuple[int, ...] = (11, 12, 13)) -> Iterator[OcrLanguageDialog]:
    app = _application()
    identifier_values = iter(ids)
    dialog = OcrLanguageDialog(lambda: next(identifier_values))
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.discard_for_shutdown()
        dialog.deleteLater()
        app.processEvents()


def _manager(
    *results: OcrLanguageDiscoveryResult,
) -> OcrLanguageManager:
    return OcrLanguageManager(_Discovery(results))


def _button(dialog: OcrLanguageDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def _choice(dialog: OcrLanguageDialog, code: str) -> QCheckBox:
    choice = dialog.findChild(QCheckBox, f"ocrLanguageChoice{code.upper()}")
    assert choice is not None
    return choice


def test_refresh_and_multiple_selection_keep_exact_request_correlation() -> None:
    available = OcrLanguageDiscoveryResult(
        OcrLanguageRuntimeStatus.AVAILABLE,
        ("eng", "deu"),
    )
    manager = _manager(available, available)
    first = manager.refresh()
    refreshes: list[int] = []
    selections: list[tuple[int, tuple[str, ...]]] = []
    saved: list[OcrLanguageSnapshot] = []

    with _dialog() as dialog:
        dialog.refresh_requested.connect(refreshes.append)
        dialog.selection_requested.connect(
            lambda request_id, codes: selections.append((request_id, codes))
        )
        dialog.selection_saved.connect(saved.append)
        assert _button(dialog, "ocrLanguageCancelButton").hasFocus()

        dialog.request_refresh()
        assert refreshes == [11]
        assert dialog.pending_request_id == 11
        assert not dialog.set_snapshot(999, first)
        assert dialog.pending_request_id == 11
        assert dialog.set_snapshot(11, first)
        assert _choice(dialog, "eng").isChecked()
        assert not _choice(dialog, "deu").isChecked()

        german = _choice(dialog, "deu")
        german.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(german, Qt.Key.Key_Space)
        QTest.mouseClick(
            _button(dialog, "ocrLanguageSaveButton"),
            Qt.MouseButton.LeftButton,
        )
        assert selections == [(12, ("eng", "deu"))]
        selected = manager.select_language_codes(("deu", "eng"))
        assert not dialog.set_snapshot(11, selected)
        assert dialog.set_snapshot(12, selected)

        assert saved == [selected]
        assert dialog.result() == QDialog.DialogCode.Accepted


def test_cancel_and_window_close_cannot_race_a_pending_selection_commit() -> None:
    manager = _manager(
        OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng", "deu"),
        )
    )
    snapshot = manager.refresh()
    selections: list[tuple[int, tuple[str, ...]]] = []
    with _dialog() as dialog:
        dialog.selection_requested.connect(
            lambda request_id, codes: selections.append((request_id, codes))
        )
        dialog.request_refresh()
        assert dialog.set_snapshot(11, snapshot)
        QTest.mouseClick(
            _button(dialog, "ocrLanguageSaveButton"),
            Qt.MouseButton.LeftButton,
        )
        assert selections == [(12, ("eng",))]
        assert dialog.pending_request_id == 12
        assert not _button(dialog, "ocrLanguageCancelButton").isEnabled()

        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.isVisible()
        assert dialog.pending_request_id == 12
        assert not dialog.close()
        assert dialog.isVisible()

        assert dialog.set_snapshot(12, snapshot)
        assert dialog.result() == QDialog.DialogCode.Accepted


def test_refresh_retains_missing_selection_and_focuses_remediation() -> None:
    both = OcrLanguageDiscoveryResult(
        OcrLanguageRuntimeStatus.AVAILABLE,
        ("eng", "deu"),
    )
    english_only = OcrLanguageDiscoveryResult(
        OcrLanguageRuntimeStatus.AVAILABLE,
        ("eng",),
    )
    manager = _manager(both, both, english_only)
    manager.refresh()
    manager.select_language_codes(("eng", "deu"))
    missing = manager.refresh()

    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(11, missing)
        _application().processEvents()
        warning = dialog.findChild(QLabel, "ocrLanguageSelectionWarning")
        assert warning is not None
        assert warning.isVisible()
        assert warning.hasFocus()
        assert "German" in warning.text()
        assert _choice(dialog, "eng").isChecked()
        assert _choice(dialog, "deu").isChecked()
        assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()

        german = _choice(dialog, "deu")
        german.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(german, Qt.Key.Key_Space)

        assert _choice(dialog, "eng").isChecked()
        assert not _choice(dialog, "deu").isChecked()
        assert _button(dialog, "ocrLanguageSaveButton").isEnabled()


def test_failure_rejects_stale_result_and_focuses_safe_message() -> None:
    failures: list[int] = []
    with _dialog() as dialog:
        dialog.refresh_requested.connect(failures.append)
        dialog.request_refresh()
        assert not dialog.set_failure(12, "OCR_INTERNAL_ERROR", "Safe recovery message.")
        assert dialog.pending_request_id == 11

        assert dialog.set_failure(11, "OCR_INTERNAL_ERROR", "Safe recovery message.")
        _application().processEvents()
        error = dialog.findChild(QLabel, "ocrLanguageError")
        assert error is not None
        assert error.isVisible()
        assert error.hasFocus()
        assert error.text() == "OCR_INTERNAL_ERROR: Safe recovery message."
        assert _button(dialog, "ocrLanguageRefreshButton").isEnabled()
        assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()


def test_failed_recheck_clears_stale_availability_before_selection() -> None:
    snapshot = _manager(
        OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng", "deu"),
        )
    ).refresh()
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(11, snapshot)
        assert _button(dialog, "ocrLanguageSaveButton").isEnabled()

        dialog.request_refresh()
        assert dialog.set_failure(12, "OCR_INTERNAL_ERROR", "Try Refresh again.")

        assert dialog.snapshot is None
        assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()
        runtime_status = dialog.findChild(QLabel, "ocrLanguageRuntimeStatus")
        assert runtime_status is not None
        assert "availability is unknown" in runtime_status.text().lower()
        assert "check complete" not in runtime_status.text().lower()
        for code in ("eng", "deu"):
            choice = _choice(dialog, code)
            status = dialog.findChild(QLabel, f"ocrLanguageStatus{code.upper()}")
            assert status is not None
            assert not choice.isEnabled()
            assert not choice.isChecked()
            assert "installed and verified" not in status.text().lower()
            assert "installed and verified" not in choice.accessibleDescription().lower()
            assert "availability unknown" in status.text().lower()


def test_failed_selection_clears_stale_verified_availability() -> None:
    available = OcrLanguageDiscoveryResult(
        OcrLanguageRuntimeStatus.AVAILABLE,
        ("eng", "deu"),
    )
    snapshot = _manager(available).refresh()
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(11, snapshot)
        german = _choice(dialog, "deu")
        german.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(german, Qt.Key.Key_Space)
        QTest.mouseClick(
            _button(dialog, "ocrLanguageSaveButton"),
            Qt.MouseButton.LeftButton,
        )
        assert dialog.pending_request_id == 12

        assert dialog.set_failure(
            12,
            "OCR_LANGUAGE_UNAVAILABLE",
            "Review OCR language settings before retrying.",
        )

        assert dialog.snapshot is None
        assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()
        for code in ("eng", "deu"):
            choice = _choice(dialog, code)
            status = dialog.findChild(QLabel, f"ocrLanguageStatus{code.upper()}")
            assert status is not None
            assert not choice.isEnabled()
            assert not choice.isChecked()
            assert "installed and verified" not in status.text().lower()
            assert "availability unknown" in status.text().lower()


def test_unavailable_and_invalid_runtime_states_cannot_be_saved() -> None:
    for index, status in enumerate(
        (OcrLanguageRuntimeStatus.UNAVAILABLE, OcrLanguageRuntimeStatus.INVALID),
        start=1,
    ):
        snapshot = _manager(OcrLanguageDiscoveryResult(status, ())).refresh()
        with _dialog((index,)) as dialog:
            dialog.request_refresh()
            assert dialog.set_snapshot(index, snapshot)
            status_label = dialog.findChild(QLabel, "ocrLanguageRuntimeStatus")
            assert status_label is not None
            expected_text = (
                "unavailable"
                if status is OcrLanguageRuntimeStatus.UNAVAILABLE
                else "could not be verified"
            )
            assert expected_text in status_label.text().lower()
            if status is OcrLanguageRuntimeStatus.INVALID:
                english_status = dialog.findChild(QLabel, "ocrLanguageStatusENG")
                assert english_status is not None
                assert "not installed or could not be verified" in english_status.text().lower()
            assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()


def test_unchecked_result_never_claims_missing_or_completed_verification() -> None:
    snapshot = _manager(
        OcrLanguageDiscoveryResult(OcrLanguageRuntimeStatus.UNCHECKED, ())
    ).refresh()
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(11, snapshot)

        runtime_status = dialog.findChild(QLabel, "ocrLanguageRuntimeStatus")
        english_status = dialog.findChild(QLabel, "ocrLanguageStatusENG")
        assert runtime_status is not None and english_status is not None
        assert "not been checked" in runtime_status.text().lower()
        assert "check complete" not in runtime_status.text().lower()
        assert "not checked" in english_status.text().lower()
        assert "not installed" not in english_status.text().lower()
        assert not _button(dialog, "ocrLanguageSaveButton").isEnabled()


def test_request_factory_failure_is_closed_and_does_not_start_busy_state() -> None:
    with _dialog(()) as dialog:
        try:
            dialog.request_refresh()
        except RuntimeError as error:
            assert str(error) == "OCR language request identifier is unavailable"
        else:
            raise AssertionError("invalid request factory must fail closed")
        assert dialog.pending_request_id is None
