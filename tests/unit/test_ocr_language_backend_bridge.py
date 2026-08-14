"""PWS-0205 correlated OCR language backend bridge tests."""

from __future__ import annotations

from typing import cast

from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)


class _Discovery:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng",),
        )


class _LanguageBackend:
    def __init__(self) -> None:
        self.manager = OcrLanguageManager(_Discovery())
        self.refresh_calls = 0
        self.selection_calls: list[tuple[str, ...]] = []

    def refresh_ocr_languages(self) -> OcrLanguageSnapshot:
        self.refresh_calls += 1
        return self.manager.refresh()

    def select_ocr_language_codes(
        self,
        language_codes: tuple[str, ...],
    ) -> OcrLanguageSnapshot:
        self.selection_calls.append(language_codes)
        return self.manager.select_language_codes(language_codes)


class _WrongSelectionBackend(_LanguageBackend):
    def select_ocr_language_codes(
        self,
        language_codes: tuple[str, ...],
    ) -> OcrLanguageSnapshot:
        self.selection_calls.append(language_codes)
        return self.manager.snapshot


def _bridge(
    backend: _LanguageBackend,
) -> tuple[OrganizerBackendCommands, OrganizerBackendWorker]:
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    worker._backend = cast(OrganizerBackend, backend)
    commands.connect_worker(worker)
    return commands, worker


def test_refresh_and_selection_results_preserve_request_ids_and_types() -> None:
    backend = _LanguageBackend()
    commands, worker = _bridge(backend)
    changed: list[tuple[int, OcrLanguageSnapshot]] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_changed.connect(
        lambda request_id, snapshot: changed.append((request_id, snapshot))
    )
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    commands.ocr_languages_refresh_requested.emit(41)
    commands.ocr_languages_select_requested.emit(42, ("eng",))

    assert [request_id for request_id, _snapshot in changed] == [41, 42]
    assert all(type(snapshot) is OcrLanguageSnapshot for _request_id, snapshot in changed)
    assert backend.refresh_calls == 1
    assert backend.selection_calls == [("eng",)]
    assert failures == []


def test_missing_selection_is_sanitized_and_never_emitted_as_success() -> None:
    backend = _LanguageBackend()
    commands, worker = _bridge(backend)
    backend.manager.refresh()
    changed: list[object] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_changed.connect(lambda *_values: changed.append(_values))
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    commands.ocr_languages_select_requested.emit(52, ("deu",))

    assert changed == []
    assert len(failures) == 1
    request_id, failure = failures[0]
    assert request_id == 52
    assert failure == UiFailure(
        code="OCR_LANGUAGE_UNAVAILABLE",
        message=(
            "The selected OCR languages are not installed and verified. "
            "Review OCR language settings before retrying."
        ),
        retryable=False,
        operation_id=52,
    )
    assert "deu" not in repr(failure)


def test_invalid_payload_and_request_are_correlated_failures() -> None:
    backend = _LanguageBackend()
    commands, worker = _bridge(backend)
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    commands.ocr_languages_select_requested.emit(61, ["eng"])
    commands.ocr_languages_refresh_requested.emit(0)

    assert [(request_id, failure.code) for request_id, failure in failures] == [
        (61, "PWS_UI_UNEXPECTED"),
        (0, "PWS_UI_REQUEST_INVALID"),
    ]
    assert failures[0][1].operation_id == 61
    assert failures[1][1].operation_id is None


def test_hostile_same_id_wrong_selection_snapshot_is_rejected() -> None:
    backend = _WrongSelectionBackend()
    backend.manager.refresh()
    commands, worker = _bridge(backend)
    changed: list[object] = []
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_changed.connect(lambda *_values: changed.append(_values))
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    commands.ocr_languages_select_requested.emit(66, ("deu",))

    assert changed == []
    assert len(failures) == 1
    assert failures[0][0] == 66
    assert failures[0][1].code == "PWS_UI_UNEXPECTED"
    assert failures[0][1].operation_id == 66


def test_uninitialized_backend_reports_retryable_failure_for_same_request() -> None:
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, object()))
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    worker.refresh_ocr_languages(71)

    assert failures == [
        (
            71,
            UiFailure(
                code="PWS_UI_NOT_READY",
                message="PaperWorkShell is still starting. Try the action again.",
                retryable=True,
                operation_id=71,
            ),
        )
    ]
