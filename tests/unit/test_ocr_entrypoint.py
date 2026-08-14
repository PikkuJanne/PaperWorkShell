"""PWS-0204 production composition tests for the OCR-only worker."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

import paperworkshell.__main__ as entrypoint
import paperworkshell.worker.client._windows as windows_launcher
from paperworkshell.app.ocr_languages import OcrLanguageRuntimeStatus
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client.tesseract_runtime import (
    TesseractRuntimeAdmission,
    TesseractRuntimeAdmissionError,
)
from paperworkshell.worker.ocr_composition import (
    OCR_WORKER_MAXIMUM_PROCESSES,
    OcrWorkerComposition,
)


@pytest.fixture(autouse=True)
def _admit_synthetic_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def admit(executable: Path, tessdata_root: Path) -> TesseractRuntimeAdmission:
        root = executable.parent.parent
        return TesseractRuntimeAdmission(
            root=root,
            executable=executable,
            tessdata_root=tessdata_root,
            resources=(root, executable.parent, executable, root / "share", tessdata_root),
        )

    monkeypatch.setattr(windows_launcher, "admit_packaged_tesseract_runtime", admit)


def _runtime(
    tmp_path: Path,
) -> tuple[Path, Path, Path, tuple[Path, ...], tuple[Path, ...], Path]:
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    runtime.mkdir()
    code.mkdir()
    python = runtime / "python.exe"
    venv = runtime / "venv-python.exe"
    bootstrap = code / "host_bootstrap.py"
    python.write_bytes(b"python")
    venv.write_bytes(b"venv")
    bootstrap.write_text("# host\n", encoding="utf-8")
    return python, venv, bootstrap, (runtime,), (code,), code


def _ocr_resources(tmp_path: Path) -> tuple[Path, Path]:
    tool = tmp_path / "ocr" / "bin"
    tessdata = tmp_path / "ocr" / "share" / "tessdata"
    tool.mkdir(parents=True)
    tessdata.mkdir(parents=True)
    executable = tool / "tesseract.exe"
    executable.write_bytes(b"tesseract")
    (tessdata / "eng.traineddata").write_bytes(b"eng")
    return executable, tessdata


def test_ocr_config_uses_exact_resources_and_central_constrained_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entrypoint, "_worker_runtime_resources", lambda: _runtime(tmp_path))
    executable, tessdata = _ocr_resources(tmp_path)

    config = entrypoint.build_windows_ocr_worker_config(
        executable,
        tessdata,
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1,
    )

    assert config.tesseract_executable == executable
    assert config.tessdata_root == tessdata
    assert config.resource_policy.maximum_processes == OCR_WORKER_MAXIMUM_PROCESSES
    assert (
        config.resource_policy.process_memory_bytes
        == CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
    )
    assert (
        config.resource_policy.job_memory_bytes
        == CONSTRAINED_RESOURCE_POLICY_V1.worker_job_memory_bytes
    )
    assert config.broker_policy.limits == entrypoint.build_windows_ocr_limits(
        CONSTRAINED_RESOURCE_POLICY_V1
    )


def test_ocr_config_reads_only_explicit_environment_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entrypoint, "_worker_runtime_resources", lambda: _runtime(tmp_path))
    executable, tessdata = _ocr_resources(tmp_path)
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))

    config = entrypoint.build_windows_ocr_worker_config(
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1
    )

    assert config.tesseract_executable == executable
    assert config.tessdata_root == tessdata


def test_missing_ocr_resources_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_TESSERACT_EXECUTABLE", raising=False)
    monkeypatch.delenv("PWS_TESSDATA_ROOT", raising=False)

    with pytest.raises(OcrError) as raised:
        entrypoint.build_windows_ocr_worker_config(resource_policy=CONSTRAINED_RESOURCE_POLICY_V1)

    assert raised.value.code is OcrErrorCode.ENGINE_UNAVAILABLE
    assert raised.value.context == "ocr.engine_configuration"


def test_language_manager_stays_lazy_and_reports_unavailable_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_TESSERACT_EXECUTABLE", raising=False)
    monkeypatch.delenv("PWS_TESSDATA_ROOT", raising=False)
    manager = entrypoint.create_desktop_application_resource_context().ocr_languages

    assert manager.snapshot.runtime_status is OcrLanguageRuntimeStatus.UNCHECKED
    snapshot = manager.refresh()

    assert snapshot.runtime_status is OcrLanguageRuntimeStatus.UNAVAILABLE
    assert snapshot.installed_language_codes == frozenset()
    assert not snapshot.selected_available


def test_language_manager_discovers_authenticated_optional_pack_in_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path)
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))

    def admit(
        executable_arg: Path,
        tessdata_arg: Path,
    ) -> TesseractRuntimeAdmission:
        assert executable_arg == executable
        assert tessdata_arg == tessdata
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
    manager = entrypoint.create_desktop_application_resource_context().ocr_languages

    snapshot = manager.refresh()
    selected = manager.select_language_codes(("deu", "eng"))

    assert snapshot.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
    assert tuple(option.code for option in snapshot.language_options if option.installed) == (
        "eng",
        "deu",
    )
    assert selected.selected_language_set_id == "english-german-fast-v1"
    assert selected.selected_available
    manager.require_available(selected.selected_language_set_id)


def test_language_manager_reports_invalid_without_exposing_admission_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path)
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))

    def reject(_executable: Path, _tessdata: Path) -> TesseractRuntimeAdmission:
        raise TesseractRuntimeAdmissionError("private_path_canary")

    monkeypatch.setattr(entrypoint, "admit_packaged_tesseract_runtime", reject)

    snapshot = entrypoint.create_desktop_application_resource_context().ocr_languages.refresh()

    assert snapshot.runtime_status is OcrLanguageRuntimeStatus.INVALID
    assert snapshot.installed_language_codes == frozenset()
    assert "private_path_canary" not in repr(snapshot)


def test_ocr_composition_receives_exact_application_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=executable,
        tessdata_root=tessdata,
    )
    sentinel = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        entrypoint,
        "build_windows_ocr_worker_config",
        lambda executable_arg, tessdata_arg, *, resource_policy: (
            observed.update(
                executable=executable_arg,
                tessdata=tessdata_arg,
                resource_policy=resource_policy,
            )
            or sentinel
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "create_ocr_worker_composition",
        lambda config, *, heavy_operations: (
            observed.update(
                config=config,
                heavy_operations=heavy_operations,
            )
            or sentinel
        ),
    )

    result = entrypoint.build_windows_ocr_worker_composition(
        resources,
        executable,
        tessdata,
    )

    assert result is sentinel
    assert observed == {
        "executable": executable,
        "tessdata": tessdata,
        "resource_policy": resources.resource_policy,
        "config": sentinel,
        "heavy_operations": resources.heavy_operations,
    }


def test_ocr_composition_rejects_runtime_different_from_app_authority(
    tmp_path: Path,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path / "captured")
    other_executable, other_tessdata = _ocr_resources(tmp_path / "other")
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=executable,
        tessdata_root=tessdata,
    )

    with pytest.raises(OcrError) as captured:
        entrypoint.build_windows_ocr_worker_composition(
            resources,
            other_executable,
            other_tessdata,
        )

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.language_composition"


def test_ocr_service_reuses_manager_and_rejects_cross_wired_composition(
    tmp_path: Path,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=executable,
        tessdata_root=tessdata,
    )

    class _Sources:
        def borrow_committed_source_page(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("not exercised")

    class _Processor:
        def process(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("not exercised")

    matching = OcrWorkerComposition(
        cast(Any, object()),
        cast(Any, _Processor()),
        resources.heavy_operations,
        cast(str, resources.ocr_languages.runtime_authority_id),
    )
    service = entrypoint.build_windows_ocr_service(
        cast(Any, _Sources()),
        matching,
        resources=resources,
    )

    assert cast(Any, service)._language_sets is resources.ocr_languages

    mismatched = OcrWorkerComposition(
        cast(Any, object()),
        cast(Any, _Processor()),
        resources.heavy_operations,
        "0" * 64,
    )
    with pytest.raises(OcrError) as captured:
        entrypoint.build_windows_ocr_service(
            cast(Any, _Sources()),
            mismatched,
            resources=resources,
        )

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.composition"
