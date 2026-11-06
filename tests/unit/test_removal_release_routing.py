"""Approved builds keep ordinary export blocked and offer the real removal route."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from paperworkshell.domain import redaction as build
from paperworkshell.domain import redaction_verification as qualification
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.domain.redaction_export import RedactionExportCode
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.redaction_tools import RedactionToolsDialog
from tests.unit.test_package_export_window import _application, _press, _window
from tests.unit.test_redaction_export_window import _marked_state
from tests.unit.test_redaction_tools_window import _editor


def test_independently_qualified_build_enables_only_the_approved_runtime_profile() -> None:
    assert build.PERMANENT_REDACTION_ENABLED is True
    assert qualification.QUALIFIED_RUNTIME_PROFILE == qualification.RUNTIME_VERIFICATION_PROFILE
    assert qualification.runtime_release_qualified()


def _qualified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "PERMANENT_REDACTION_ENABLED", True)
    monkeypatch.setattr(
        qualification, "QUALIFIED_RUNTIME_PROFILE", qualification.RUNTIME_VERIFICATION_PROFILE
    )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "profile", [None, "unknown-profile", qualification.RUNTIME_VERIFICATION_PROFILE]
)
def test_build_switch_and_exact_profile_are_both_required(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, profile: str | None
) -> None:
    monkeypatch.setattr(build, "PERMANENT_REDACTION_ENABLED", enabled)
    monkeypatch.setattr(qualification, "QUALIFIED_RUNTIME_PROFILE", profile)
    assert qualification.runtime_release_qualified() is (
        enabled and profile == qualification.RUNTIME_VERIFICATION_PROFILE
    )


def test_enabled_ordinary_export_recovery_invokes_a_fresh_removal_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified(monkeypatch)
    with _window() as (window, commands, worker):
        assert window._state is not None
        commands.removal_export_review_requested.disconnect(worker.begin_removal_export)
        requests: list[int] = []
        commands.removal_export_review_requested.connect(requests.append)
        worker.state_changed.emit(_marked_state(window._state))
        assert window._queue_redaction_export_report()
        assert window._show_pending_redaction_export_report()
        dialog = window._redaction_export_dialog
        assert dialog is not None
        assert dialog.report.code is RedactionExportCode.WORKFLOW_REQUIRED
        assert dialog.report.report_version == 2 and dialog.report.verification is None
        assert "approval" not in dialog.report.safe_message
        button = dialog.findChild(QPushButton, "redactionExportRetryButton")
        assert button is not None and button.isEnabled()
        assert button.accessibleName() == "Review and export removal derivative"
        _press(button)
        _application().processEvents()
        assert len(requests) == 1


def test_enabled_bridge_error_does_not_claim_release_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified(monkeypatch)
    result = sanitize_ui_failure(
        RedactionError(RedactionErrorCode.PIPELINE_DISABLED, "synthetic.ordinary_export")
    )
    assert result.code == RedactionErrorCode.PIPELINE_DISABLED.value
    assert "Export removal derivative" in result.message
    assert "until" not in result.message and "approval" not in result.message
    assert not result.retryable


def test_enabled_editor_still_saves_only_intent_without_disabled_feature_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualified(monkeypatch)
    _application()
    dialog = RedactionToolsDialog(_editor())
    try:
        assert "Export removal derivative" in dialog.accessibleDescription()
        warning = dialog.findChild(QLabel, "redactionIntentWarning")
        assert warning is not None and "Save marks only" in warning.text()
        assert "not enabled" not in warning.text()
        assert "No removal has been verified" in warning.text()
    finally:
        dialog.deleteLater()
        _application().processEvents()
