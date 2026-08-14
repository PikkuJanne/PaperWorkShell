"""Isolated PySide process probe for a desktop autosave crash boundary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Event, Thread
from time import sleep
from typing import Any, NoReturn, cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.fspath(_REPOSITORY_ROOT / "src"))

from PySide6.QtCore import Qt, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import paperworkshell.storage.manifest_repository as manifest_storage  # noqa: E402
from paperworkshell.__main__ import build_windows_startup_recovery_service  # noqa: E402
from paperworkshell.app.case_lifecycle import CaseLifecycleService  # noqa: E402
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState  # noqa: E402
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore  # noqa: E402
from paperworkshell.storage.manifest_repository import WindowsManifestRepository  # noqa: E402
from paperworkshell.storage.quotas import WorkspaceQuota  # noqa: E402
from paperworkshell.storage.workspace import CaseWorkspaceManager  # noqa: E402
from paperworkshell.ui.backend import (  # noqa: E402
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
from paperworkshell.ui.window import PaperWorkShellWindow  # noqa: E402

_PROCESS_TIMEOUT_SECONDS = 45.0


class _EmptyThumbnailService:
    def get_thumbnails(self, *_args: object, **_kwargs: object) -> tuple[()]:
        return ()

    def get_previews(self, *_args: object, **_kwargs: object) -> tuple[()]:
        return ()


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


def _abort_after_timeout() -> NoReturn:
    sleep(_PROCESS_TIMEOUT_SECONDS)
    os._exit(3)


def _write_exact(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _backend(
    profile: Path,
    *,
    recover: bool,
    recovery_holder: list[object],
) -> OrganizerBackend:
    manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    startup = build_windows_startup_recovery_service(manager).recover_startup() if recover else None
    if startup is not None:
        recovery_holder.append(startup)
    return OrganizerBackend(
        CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
        WindowsManifestRepository(manager),
        cast(Any, object()),
        cast(Any, _EmptyThumbnailService()),
        startup_recovery=startup,
    )


def _run(mode: str, profile: Path, case_id: UUID, output: Path) -> int:
    recovery_holder: list[object] = []
    if mode == "crash":

        def stop_after_checkpoint(*_args: object, **_kwargs: object) -> NoReturn:
            _write_exact(output, b"checkpoint-ready\n")
            Event().wait()
            raise AssertionError("unreachable")

        setattr(  # noqa: B010 - replace one imported transaction seam in this child only
            manifest_storage,
            "replace_manifest_atomically",
            cast(Any, stop_after_checkpoint),
        )

    application = QApplication([])
    application.setApplicationName("PaperWorkShellAutosaveRestartProbe")
    application.setQuitOnLastWindowClosed(False)
    commands = OrganizerBackendCommands()
    backend_thread = QThread()
    worker = OrganizerBackendWorker(
        lambda: _backend(
            profile,
            recover=mode == "verify",
            recovery_holder=recovery_holder,
        )
    )
    worker.moveToThread(backend_thread)
    commands.connect_worker(worker)
    backend_thread.started.connect(worker.initialize)
    worker.shutdown_complete.connect(worker.deleteLater)
    worker.shutdown_complete.connect(
        backend_thread.quit,
        Qt.ConnectionType.DirectConnection,
    )
    backend_thread.finished.connect(application.quit)
    window = PaperWorkShellWindow(commands, worker)
    action_started = False

    def initialized() -> None:
        commands.open_case_requested.emit(case_id)

    def state_changed(state: object) -> None:
        nonlocal action_started
        if not isinstance(state, OrganizerViewState) or state.case_id != case_id or not state.pages:
            return
        if mode == "crash":
            if not action_started and not state.autosave_pending:
                action_started = True
                commands.rotate_requested.emit(state.pages[0].page_id)
            return
        if not recovery_holder:
            os._exit(4)
        startup = cast(Any, recovery_holder[0])
        recovered = next(item for item in startup.cases if item.case_id == case_id)
        payload = {
            "case_id": str(case_id),
            "issues": [issue.value for issue in recovered.issues],
            "manifest_revision": recovered.manifest_revision,
            "page_rotations": [page.rotation_degrees for page in state.pages],
            "recovered_cases": startup.recovered_cases,
            "recovery_status": recovered.status.value,
            "startup_status": startup.status.value,
            "state_revision": state.revision,
        }
        _write_exact(
            output,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
        )
        commands.shutdown_requested.emit()

    worker.initialized.connect(initialized)
    worker.state_changed.connect(state_changed)
    window.show()
    Thread(target=_abort_after_timeout, daemon=True).start()
    backend_thread.start()
    exit_code = application.exec()
    backend_thread.wait()
    window.deleteLater()
    backend_thread.deleteLater()
    application.processEvents()
    return exit_code


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in {"crash", "verify"}:
        return 2
    try:
        profile = Path(argv[2]).resolve(strict=True)
        case_id = UUID(argv[3])
        output = Path(argv[4]).resolve(strict=False)
    except OSError, ValueError:
        return 2
    if case_id.version != 4 or output.exists():
        return 2
    return _run(argv[1], profile, case_id, output)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
