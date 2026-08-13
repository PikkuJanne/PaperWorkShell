"""PWS-0204 production composition tests for the OCR-only worker."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import paperworkshell.__main__ as entrypoint
import paperworkshell.worker.client._windows as windows_launcher
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission
from paperworkshell.worker.ocr_composition import OCR_WORKER_MAXIMUM_PROCESSES


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


def test_ocr_composition_receives_exact_application_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata = _ocr_resources(tmp_path)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1
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
