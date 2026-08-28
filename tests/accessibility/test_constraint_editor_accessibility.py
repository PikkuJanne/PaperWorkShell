from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.app.portal_constraints import (
    ConstraintEditorState,
    PortalConstraintWorkflowService,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.manifest_validation import MAX_OUTPUT_NAME_UTF16_CODE_UNITS
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.ui.constraints import ConstraintEditorDialog

CASE_ID = UUID("81000000-0000-4000-8000-000000000001")
EDITOR_ID = UUID("82000000-0000-4000-8000-000000000001")
PRIVATE_CANARY = "private-source-name-and-path-canary.pdf"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _id(value: UUID) -> Callable[[], UUID]:
    return lambda: value


def _state() -> ConstraintEditorState:
    document: dict[str, object] = {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 4,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:01:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": "83000000-0000-4000-8000-000000000001",
                "display_name": PRIVATE_CANARY,
                "storage_name": f"{PRIVATE_CANARY}.bin",
            }
        ],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    snapshot = ManifestSnapshot(CASE_ID, 2, 4, document, "a" * 64, "b" * 64)
    service = PortalConstraintWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=_id(EDITOR_ID),
    )
    return service.begin(snapshot)


@contextmanager
def _dialog(state: ConstraintEditorState | None = None) -> Iterator[ConstraintEditorDialog]:
    app = _application()
    dialog = ConstraintEditorDialog(_state() if state is None else state)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _widget[T: QWidget](dialog: ConstraintEditorDialog, kind: type[T], name: str) -> T:
    widget = dialog.findChild(kind, name)
    assert widget is not None
    return widget


def _press(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    _application().processEvents()


def test_textual_source_non_acceptance_guidance_labels_and_controls_are_accessible() -> None:
    with _dialog() as dialog:
        assert dialog.isModal()
        assert dialog.windowTitle() == "Review portal packaging constraints"
        assert "Cancel is the safe default" in dialog.accessibleDescription()

        source = _widget(dialog, QLabel, "constraintSourceStatus")
        assert "Manual case constraints" in source.text()
        assert "current portal instructions" in source.text()
        guidance = _widget(dialog, QLabel, "constraintEditorGuidance")
        assert "does not confirm" in guidance.text()
        assert "acceptance" in guidance.text()

        labels_with_mnemonics = [
            label for label in dialog.findChildren(QLabel) if "&" in label.text()
        ]
        assert len(labels_with_mnemonics) >= 8
        assert all(label.buddy() is not None for label in labels_with_mnemonics)

        interactive_names = (
            "constraintSourceStatus",
            "constraintRestoreDefaultsButton",
            "constraintCombinedPdfCheck",
            "constraintSeparatePdfsCheck",
            "constraintZipCheck",
            "constraintEncryptionCombo",
            "constraintFileCountEnabled",
            "constraintFileCountInput",
            "constraintTotalBytesEnabled",
            "constraintTotalBytesInput",
            "constraintFileBytesEnabled",
            "constraintFileBytesInput",
            "constraintPagesPerFileEnabled",
            "constraintPagesPerFileInput",
            "constraintNameList",
            "constraintNameStemInput",
            "constraintNameExtension",
            "constraintNameAddButton",
            "constraintNameUpdateButton",
            "constraintNameRemoveButton",
            "constraintNameEarlierButton",
            "constraintNameLaterButton",
            "constraintEditorStatus",
            "constraintApplyButton",
            "constraintCancelButton",
        )
        for object_name in interactive_names:
            widget = dialog.findChild(QWidget, object_name)
            assert widget is not None
            assert widget.accessibleName()
            assert widget.accessibleDescription()

        extension = _widget(dialog, QLineEdit, "constraintNameExtension")
        assert extension.isReadOnly()
        visible_text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        visible_text += " ".join(
            _widget(dialog, QListWidget, "constraintNameList").item(index).text()
            for index in range(_widget(dialog, QListWidget, "constraintNameList").count())
        )
        assert PRIVATE_CANARY not in visible_text


def test_cancel_is_safe_default_and_scroll_keeps_controls_reachable_with_large_text() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSizeF(max(24.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged_font)
    try:
        with _dialog() as dialog:
            cancel = _widget(dialog, QPushButton, "constraintCancelButton")
            apply = _widget(dialog, QPushButton, "constraintApplyButton")
            scroll = _widget(dialog, QScrollArea, "constraintEditorScrollArea")
            assert cancel.isDefault()
            assert dialog.focusWidget() is cancel

            QTest.keyClick(cancel, Qt.Key.Key_Tab)
            app.processEvents()
            assert dialog.focusWidget() is scroll

            dialog.resize(520, 360)
            app.processEvents()
            assert scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            assert apply.isVisible()
            assert cancel.isVisible()
            assert dialog.rect().contains(apply.mapTo(dialog, apply.rect().center()))
            assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().center()))
    finally:
        app.setFont(original_font)


def test_complete_keyboard_validation_focuses_first_invalid_field_without_echoing_names() -> None:
    with _dialog() as dialog:
        emitted: list[object] = []
        dialog.apply_requested.connect(emitted.append)
        combined = _widget(dialog, QCheckBox, "constraintCombinedPdfCheck")
        apply = _widget(dialog, QPushButton, "constraintApplyButton")
        combined.setChecked(False)
        _press(apply)

        assert emitted == []
        assert not dialog.apply_pending
        assert dialog.focusWidget() is combined
        status = _widget(dialog, QLabel, "constraintEditorStatus")
        assert "PWS_CONSTRAINT_DRAFT_INVALID" in status.text()

        combined.setChecked(True)
        set_count = _widget(dialog, QCheckBox, "constraintFileCountEnabled")
        count = _widget(dialog, QLineEdit, "constraintFileCountInput")
        set_count.setChecked(True)
        count.clear()
        _press(apply)
        assert emitted == []
        assert dialog.focusWidget() is count

        stem = _widget(dialog, QLineEdit, "constraintNameStemInput")
        add = _widget(dialog, QPushButton, "constraintNameAddButton")
        stem.setText(f"..\\{PRIVATE_CANARY}")
        _press(add)
        assert _widget(dialog, QListWidget, "constraintNameList").count() == 0
        assert PRIVATE_CANARY not in status.text()
        assert dialog.focusWidget() is stem


def test_keyboard_names_optional_limits_and_apply_failure_have_safe_retry_path() -> None:
    app = _application()
    with _dialog() as dialog:
        emitted: list[ConstraintEditorState] = []
        dialog.apply_requested.connect(emitted.append)
        stem = _widget(dialog, QLineEdit, "constraintNameStemInput")
        add = _widget(dialog, QPushButton, "constraintNameAddButton")
        names = _widget(dialog, QListWidget, "constraintNameList")
        extension = _widget(dialog, QLineEdit, "constraintNameExtension")

        assert stem.maxLength() == MAX_OUTPUT_NAME_UTF16_CODE_UNITS
        stem.setText("packet")
        _press(add)
        assert names.count() == 1
        assert names.item(0).text() == "packet.pdf"
        assert extension.text() == ".pdf"

        stem.setText("support")
        _press(add)
        assert [names.item(index).text() for index in range(names.count())] == [
            "packet.pdf",
            "support.pdf",
        ]
        earlier = _widget(dialog, QPushButton, "constraintNameEarlierButton")
        _press(earlier)
        assert names.item(0).text() == "support.pdf"
        stem.setText("evidence")
        _press(_widget(dialog, QPushButton, "constraintNameUpdateButton"))
        assert names.item(0).text() == "evidence.pdf"
        _press(_widget(dialog, QPushButton, "constraintNameRemoveButton"))
        assert names.count() == 1
        assert names.item(0).text() == "packet.pdf"

        zip_check = _widget(dialog, QCheckBox, "constraintZipCheck")
        combined = _widget(dialog, QCheckBox, "constraintCombinedPdfCheck")
        zip_check.setChecked(True)
        combined.setChecked(False)
        app.processEvents()
        assert names.item(0).text() == "packet.zip"
        assert extension.text() == ".zip"

        set_count = _widget(dialog, QCheckBox, "constraintFileCountEnabled")
        count = _widget(dialog, QLineEdit, "constraintFileCountInput")
        set_count.setChecked(True)
        count.setText("2")
        # Other unchecked limit fields remain exact None values.
        assert not _widget(dialog, QCheckBox, "constraintTotalBytesEnabled").isChecked()

        apply = _widget(dialog, QPushButton, "constraintApplyButton")
        cancel = _widget(dialog, QPushButton, "constraintCancelButton")
        _press(apply)
        assert len(emitted) == 1
        first_apply_pending = dialog.apply_pending
        assert first_apply_pending
        assert dialog.commit_started
        pending_shutdown_state = dialog.latest_state_for_shutdown()
        assert pending_shutdown_state is None
        assert not cancel.isEnabled()
        assert emitted[0].constraints.allowed_modes == (PortalOutputMode.ZIP,)
        assert emitted[0].constraints.maximum_file_count == 2
        assert emitted[0].constraints.maximum_total_bytes is None
        assert emitted[0].constraints.maximum_file_bytes is None
        assert emitted[0].constraints.maximum_pages_per_file is None
        assert emitted[0].output_names[0].filename == "packet.zip"
        status = _widget(dialog, QLabel, "constraintEditorStatus")
        assert "packet" not in status.text()

        assert dialog.mark_apply_failed("PRIVATE", PRIVATE_CANARY)
        app.processEvents()
        retry_apply_pending = dialog.apply_pending
        assert not retry_apply_pending
        retry_shutdown_state = dialog.latest_state_for_shutdown()
        assert retry_shutdown_state is dialog.state
        assert apply.isEnabled()
        assert not cancel.isEnabled()
        assert PRIVATE_CANARY not in status.text()
        assert "retained" in status.text().casefold()

        _press(apply)
        assert len(emitted) == 2
        second_apply_pending = dialog.apply_pending
        assert second_apply_pending
        assert dialog.mark_applied()


def test_generic_default_reset_is_explicit_textual_and_keyboard_reachable() -> None:
    with _dialog() as dialog:
        changed: list[ConstraintEditorState] = []
        dialog.state_changed.connect(changed.append)
        restore = _widget(dialog, QPushButton, "constraintRestoreDefaultsButton")
        _press(restore)

        assert len(changed) == 1
        assert changed[0].changed
        source = _widget(dialog, QLabel, "constraintSourceStatus")
        assert "copied into this draft" in source.text()
        assert "not been saved" in source.text()
        assert dialog.focusWidget() is source
        assert _widget(dialog, QListWidget, "constraintNameList").item(0).text().endswith(".pdf")
        assert PortalOutputMode.ZIP in changed[0].constraints.allowed_modes
        assert "accept" not in _widget(dialog, QLabel, "constraintEditorStatus").text().casefold()
