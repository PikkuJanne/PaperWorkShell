"""Real packaged-runtime processing through one static owner and fresh Jobs.

This is source integration evidence, never installed desktop/visible-UI proof.
Select the exact packaged runtime via PWS_TEST_RUNTIME_ROOT; all source and runtime
file identities are retained by owned_production_worker_code in JUnit properties.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal
from paperworkshell.worker.client.process import WorkerLaunchRequest
from paperworkshell.worker.desktop_composition import DesktopWorkerCompositionConfig

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows packaged-runtime integration")

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/synthetic/v1/image/ocr-raster-text.png"


def test_ordinary_plain_and_searchable_exports_share_only_fixed_runtime_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_production_worker_code: Path,
    record_property: Callable[[str, object], None],
) -> None:
    selected = os.environ.get("PWS_TEST_RUNTIME_ROOT")
    if not selected:
        pytest.skip("PWS_TEST_RUNTIME_ROOT must select an exact packaged engine closure")
    assert owned_production_worker_code.is_dir()
    runtime = Path(selected).resolve(strict=True)
    qpdf = runtime / "engines/qpdf/bin/qpdf.exe"
    tesseract = runtime / "engines/tesseract/bin/tesseract.exe"
    tessdata = runtime / "engines/tesseract/share/tessdata"
    assert qpdf.is_file() and tesseract.is_file() and (tessdata / "eng.traineddata").is_file()
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", str(qpdf))
    monkeypatch.setattr(
        desktop,
        "_installed_worker_recovery_authority",
        lambda: (runtime, owned_production_worker_code, qpdf.parent, tesseract.parent.parent),
    )
    journal = windows._acl_recovery_directory_path()
    original_config = desktop._desktop_worker_config

    def config(policy: Any = None) -> DesktopWorkerCompositionConfig:
        return replace(original_config(policy), retain_runtime_access=True)

    monkeypatch.setattr(desktop, "_desktop_worker_config", config)
    phases: list[dict[str, object]] = []
    launches: list[dict[str, object]] = []

    def timed(target: object, name: str) -> None:
        original = getattr(target, name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                phases.append({"phase": name, "seconds": time.monotonic() - started})

        monkeypatch.setattr(target, name, invoke)

    for target, name in (
        (windows._AccessGrantLease, "grant_all"),
        (windows._AccessGrantLease, "require_read_execute"),
        (windows._AccessGrantLease, "close_no_raise"),
        (windows, "_reset_profile_storage"),
        (windows, "_recreate_profile_storage"),
        (windows.WindowsWorkerProcess, "close"),
    ):
        timed(target, name)
    original_launch = windows.WindowsProcessLauncher.launch

    def launch(
        self: windows.WindowsProcessLauncher, request: WorkerLaunchRequest
    ) -> windows.WindowsWorkerProcess:
        started = time.monotonic()
        process = original_launch(self, request)
        launches.append(
            {
                "pid": process.pid,
                "case_id": str(request.case_id),
                "session_id": str(request.session_id),
                "worker_id": str(request.worker_instance_id),
                "profile_sid": windows._sid_string(process._profile.sid),
                "qpdf_admitted": self._config.qpdf_executable is not None,
                "ocr_admitted": self._config.tesseract_executable is not None,
                "maximum_processes": request.resource_policy.maximum_processes,
                "scope": self._config.operation_scope.value,
                "launch_seconds": time.monotonic() - started,
            }
        )
        return process

    monkeypatch.setattr(windows.WindowsProcessLauncher, "launch", launch)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(
        policy, tesseract_executable=tesseract, tessdata_root=tessdata
    )
    resources.ocr_languages.select_language_codes(("eng",))
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(profile_root=profile, quota=desktop._workspace_quota(policy))
    backend = desktop._build_backend(manager, resources=resources)
    source_before = hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()
    record_property("fixture_sha256", source_before)
    record_property(
        "verification_scope", "owned current source + exact packaged runtime; no UI claim"
    )
    try:
        created = backend.create_case("Synthetic shared runtime")
        imported = backend.import_source(_FIXTURE, "Synthetic OCR raster.png")
        page_ids = imported.committed_import.page_ids
        assert created.case_id is not None and len(page_ids) == 1
        backend.get_thumbnails(160, 160, page_ids=page_ids)
        first = AclRecoveryJournal(journal).inventory()
        assert len(first) == 1
        fixed_objects = first[0].objects
        fixed_sid = first[0].sid

        plain = backend.export_pages_pdf(
            tmp_path / "plain.pdf", cancellation=lambda: False, progress=lambda _value: None
        )
        assert plain.committed_export.report.page_count == 1
        after_plain = AclRecoveryJournal(journal).inventory()
        assert len(after_plain) == 1
        assert after_plain[0].sid == fixed_sid and after_plain[0].objects == fixed_objects

        searchable = backend.export_pages_pdf(
            tmp_path / "searchable.pdf",
            cancellation=lambda: False,
            progress=lambda _value: None,
            make_searchable=True,
        )
        assert searchable.committed_export.report.page_count == 1
        assert searchable.searchable_report is not None
        assert searchable.searchable_report.ocr_text_page_count == 1
        after_ocr = AclRecoveryJournal(journal).inventory()
        assert len(after_ocr) == 1
        assert after_ocr[0].sid == fixed_sid and after_ocr[0].objects == fixed_objects

        backend.rotate_clockwise(page_ids[0])
        backend.flush_autosave()
        backend.get_thumbnails(160, 160, page_ids=page_ids)
        assert any(item["qpdf_admitted"] for item in launches)
        assert any(item["ocr_admitted"] for item in launches)
        assert len({item["profile_sid"] for item in launches}) == 1
        assert len({item["session_id"] for item in launches}) == len(launches)
        assert len({item["worker_id"] for item in launches}) == len(launches)
        assert all(item["case_id"] == str(created.case_id) for item in launches)
        assert sum(item["phase"] == "grant_all" for item in phases) == 1
        assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == source_before
        backend.close_case()
        assert AclRecoveryJournal(journal).inventory() == ()
    except BaseException as error:
        record_property("primary_exception_type", type(error).__name__)
        if type(error).__module__.startswith("paperworkshell."):
            record_property("primary_error_code", getattr(error, "code", None))
            record_property("primary_error_context", getattr(error, "context", None))
        raise
    finally:
        try:
            backend.shutdown()
        finally:
            record_property("runtime_access_launches", json.dumps(launches))
            record_property("runtime_access_phase_trace", json.dumps(phases))
