"""PWS-0206 production composition-root tests for searchable PDF export."""

from __future__ import annotations

from pathlib import Path

import pytest

import paperworkshell.__main__ as entrypoint
import paperworkshell.worker.client._windows as windows_launcher
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client.tesseract_runtime import TesseractRuntimeAdmission
from paperworkshell.worker.searchable_pdf_export_composition import (
    SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
)


@pytest.fixture(autouse=True)
def _admit_synthetic_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
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
    bootstrap = code / "host.py"
    python.write_bytes(b"python")
    venv.write_bytes(b"venv")
    bootstrap.write_text("# host\n", encoding="utf-8")
    return python, venv, bootstrap, (runtime,), (code,), code


def _engines(tmp_path: Path) -> tuple[Path, Path, Path]:
    qpdf = tmp_path / "qpdf" / "qpdf.exe"
    tesseract = tmp_path / "tesseract" / "bin" / "tesseract.exe"
    tessdata = tmp_path / "tesseract" / "share" / "tessdata"
    qpdf.parent.mkdir()
    tesseract.parent.mkdir(parents=True)
    tessdata.mkdir(parents=True)
    qpdf.write_bytes(b"qpdf")
    tesseract.write_bytes(b"tesseract")
    (tessdata / "eng.traineddata").write_bytes(b"eng")
    return qpdf, tesseract, tessdata


def test_searchable_config_uses_exact_combined_authority_and_central_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entrypoint, "_worker_runtime_resources", lambda: _runtime(tmp_path))
    qpdf, tesseract, tessdata = _engines(tmp_path)

    config = entrypoint.build_windows_searchable_pdf_export_worker_config(
        qpdf,
        tesseract,
        tessdata,
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1,
    )
    limits = entrypoint.build_windows_searchable_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)

    assert config.qpdf_executable == qpdf
    assert config.tesseract_executable == tesseract
    assert config.tessdata_root == tessdata
    assert config.resource_policy.maximum_processes == (
        SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES
    )
    assert config.resource_policy.process_memory_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
    )
    assert config.resource_policy.job_memory_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_job_memory_bytes
    )
    assert config.broker_policy.limits == limits
    assert config.broker_policy.status_capacity_bytes == 2 * 1024 * 1024
    assert config.maximum_active_full_resolution_page_buffers == (
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
    )
    assert config.resource_sampling_interval_ms == (
        CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms
    )
    assert config.broker_policy.pump_interval_seconds == (
        CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms / 1_000
    )
    assert (
        2 * limits.output_bytes
    ) + config.broker_policy.status_capacity_bytes < limits.temporary_storage_bytes


def test_searchable_composition_receives_exact_application_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qpdf, tesseract, tessdata = _engines(tmp_path)
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=tesseract,
        tessdata_root=tessdata,
    )
    config = object()
    composition = object()
    observed: dict[str, object] = {}

    def build_config(
        qpdf_arg: Path | None,
        tesseract_arg: Path | None,
        tessdata_arg: Path | None,
        *,
        resource_policy: object,
    ) -> object:
        observed.update(
            qpdf=qpdf_arg,
            tesseract=tesseract_arg,
            tessdata=tessdata_arg,
            resource_policy=resource_policy,
        )
        return config

    def compose(config_arg: object, *, heavy_operations: object) -> object:
        observed.update(config=config_arg, heavy_operations=heavy_operations)
        return composition

    monkeypatch.setattr(
        entrypoint, "build_windows_searchable_pdf_export_worker_config", build_config
    )
    monkeypatch.setattr(
        entrypoint,
        "create_searchable_pdf_export_worker_composition",
        compose,
    )

    result = entrypoint.build_windows_searchable_pdf_export_worker_composition(
        resources,
        qpdf,
        tesseract,
        tessdata,
    )

    assert result is composition
    assert observed == {
        "qpdf": qpdf,
        "tesseract": tesseract,
        "tessdata": tessdata,
        "resource_policy": resources.resource_policy,
        "config": config,
        "heavy_operations": resources.heavy_operations,
    }


def test_searchable_composition_rejects_runtime_different_from_app_authority(
    tmp_path: Path,
) -> None:
    (tmp_path / "captured").mkdir()
    (tmp_path / "other").mkdir()
    qpdf, tesseract, tessdata = _engines(tmp_path / "captured")
    _other_qpdf, other_tesseract, other_tessdata = _engines(tmp_path / "other")
    resources = entrypoint.create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1,
        tesseract_executable=tesseract,
        tessdata_root=tessdata,
    )

    with pytest.raises(OcrError) as captured:
        entrypoint.build_windows_searchable_pdf_export_worker_composition(
            resources,
            qpdf,
            other_tesseract,
            other_tessdata,
        )

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.language_composition"
