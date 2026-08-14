"""PWS-0205 offline, case-isolation, and path-sanitization checks."""

from __future__ import annotations

import copy
import os
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

import paperworkshell.__main__ as entrypoint
from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission

PRIVATE_CANARY = r"C:\Users\Private Applicant\Tax packet.pdf"


class _Discovery:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng", "deu"),
        )


class _PrivateFailureDiscovery:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        raise RuntimeError(PRIVATE_CANARY)


class _HostileOcrError(OcrError):
    pass


class _OcrErrorDiscovery:
    def __init__(self, *, subclass: bool) -> None:
        error_type = _HostileOcrError if subclass else OcrError
        self._error = error_type(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_discovery")
        self._error.safe_message = PRIVATE_CANARY
        self._error.args = (PRIVATE_CANARY,)

    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        raise self._error


class _Backend:
    def __init__(self, manager: OcrLanguageManager, manifest: dict[str, object]) -> None:
        self.manager = manager
        self.manifest = manifest

    def refresh_ocr_languages(self) -> OcrLanguageSnapshot:
        return self.manager.refresh()

    def select_ocr_language_codes(
        self,
        codes: tuple[str, ...],
    ) -> OcrLanguageSnapshot:
        return self.manager.select_language_codes(codes)


def _bridge(backend: _Backend) -> tuple[OrganizerBackendCommands, OrganizerBackendWorker]:
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    worker._backend = cast(OrganizerBackend, backend)
    commands.connect_worker(worker)
    return commands, worker


def test_capability_refresh_and_selection_never_open_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OCR language capability checks must remain offline")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)
    manager = OcrLanguageManager(_Discovery())

    manager.refresh()
    manager.select_language_codes(("eng", "deu"))
    manager.require_available(manager.snapshot.selected_language_set_id)


def test_production_packaged_discovery_uses_only_local_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def external_action_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("production OCR discovery must remain local")

    monkeypatch.setattr(socket, "socket", external_action_forbidden)
    monkeypatch.setattr(socket, "create_connection", external_action_forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", external_action_forbidden)
    monkeypatch.setattr(subprocess, "Popen", external_action_forbidden)
    monkeypatch.setattr(os, "system", external_action_forbidden)
    executable = (tmp_path / "runtime" / "bin" / "tesseract.exe").absolute()
    tessdata = (tmp_path / "runtime" / "share" / "tessdata").absolute()
    observed: list[tuple[Path, Path]] = []

    def admit(executable_arg: Path, tessdata_arg: Path) -> TesseractRuntimeAdmission:
        observed.append((executable_arg, tessdata_arg))
        return TesseractRuntimeAdmission(
            root=executable.parent.parent,
            executable=executable,
            tessdata_root=tessdata,
            resources=(),
            installed_language_codes=("deu", "eng"),
            available_language_set_ids=(
                "english-german-fast-v1",
                "german-fast-v1",
                "latin-default-v1",
            ),
        )

    monkeypatch.setattr(entrypoint, "admit_packaged_tesseract_runtime", admit)
    manager = entrypoint.create_desktop_application_resource_context(
        tesseract_executable=executable,
        tessdata_root=tessdata,
    ).ocr_languages

    snapshot = manager.refresh()
    manager.select_language_codes(("eng", "deu"))
    assert manager.require_selected_available() == "english-german-fast-v1"

    assert snapshot.installed_language_codes == frozenset({"eng", "deu"})
    assert observed == [
        (executable, tessdata),
        (executable, tessdata),
        (executable, tessdata),
    ]


def test_refresh_and_selection_leave_case_manifest_and_revision_untouched() -> None:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "case_id": "internal-case-id",
        "revision": 17,
        "page_sequence": ["internal-page-id"],
    }
    before = copy.deepcopy(manifest)
    backend = _Backend(OcrLanguageManager(_Discovery()), manifest)
    commands, worker = _bridge(backend)
    results: list[tuple[int, OcrLanguageSnapshot]] = []
    worker.ocr_languages_changed.connect(
        lambda request_id, snapshot: results.append((request_id, snapshot))
    )

    commands.ocr_languages_refresh_requested.emit(201)
    commands.ocr_languages_select_requested.emit(202, ("eng", "deu"))

    assert [request_id for request_id, _snapshot in results] == [201, 202]
    assert manifest == before
    assert manifest["revision"] == 17


@pytest.mark.parametrize("subclass", (False, True))
def test_discovery_supplied_ocr_error_text_and_subclasses_are_reconstructed(
    subclass: bool,
) -> None:
    manager = OcrLanguageManager(_OcrErrorDiscovery(subclass=subclass))

    with pytest.raises(OcrError) as captured:
        manager.refresh()

    expected_code = OcrErrorCode.INTERNAL_ERROR if subclass else OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert type(captured.value) is OcrError
    assert captured.value.code is expected_code
    assert PRIVATE_CANARY not in str(captured.value)
    assert PRIVATE_CANARY not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_private_discovery_failure_is_absent_from_repr_bridge_and_dialog() -> None:
    manager = OcrLanguageManager(_PrivateFailureDiscovery())
    backend = _Backend(manager, {"revision": 3})
    commands, worker = _bridge(backend)
    failures: list[tuple[int, UiFailure]] = []
    worker.ocr_languages_failed.connect(
        lambda request_id, failure: failures.append((request_id, failure))
    )

    commands.ocr_languages_refresh_requested.emit(301)

    assert len(failures) == 1
    request_id, failure = failures[0]
    assert request_id == 301
    assert failure.code == "OCR_INTERNAL_ERROR"
    assert PRIVATE_CANARY not in repr(manager)
    assert PRIVATE_CANARY not in repr(failure)

    existing = QApplication.instance()
    app = QApplication([]) if existing is None else cast(QApplication, existing)
    dialog = OcrLanguageDialog(lambda: 301)
    dialog.show()
    dialog.request_refresh()
    assert dialog.set_failure(301, failure.code, failure.message)
    app.processEvents()
    visible_text = " ".join(
        (
            widget.text()
            if isinstance(widget, QLabel)
            else widget.accessibleName() + " " + widget.accessibleDescription()
        )
        for widget in dialog.findChildren(QWidget)
    )
    assert PRIVATE_CANARY not in visible_text
    assert "Tax packet" not in visible_text
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
