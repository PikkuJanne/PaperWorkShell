"""Installed recovery authority is fixed independently of active worker grants."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.installed_runtime import InstalledRuntime
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_export import IMAGE_PDF_EXPORT_PROFILE
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1 as POLICY
from paperworkshell.worker import (
    desktop_composition,
    flattened_pdf_export_composition,
    ocr_composition,
    quality_ocr_review_composition,
    searchable_pdf_export_composition,
)
from paperworkshell.worker.client.tesseract_runtime import (
    TesseractRuntimeAdmission,
    TesseractRuntimeAdmissionError,
)


def _installed(root: Path) -> InstalledRuntime:
    return InstalledRuntime(
        "0.0.0",
        "synthetic-installed",
        root,
        root / "Lib/site-packages/paperworkshell",
        root / "python.exe",
        root / "engines/qpdf/bin/qpdf.exe",
        root / "engines/tesseract/bin/tesseract.exe",
        root / "engines/tesseract/share/tessdata",
    )


def test_installed_recovery_roots_preserve_exact_engine_roots_and_ignore_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = _installed(tmp_path / "runtime")
    monkeypatch.setattr(desktop, "_installed_runtime", lambda: installed)
    observed: list[tuple[Path, Path]] = []

    def admit(executable: Path, tessdata: Path) -> TesseractRuntimeAdmission:
        observed.append((executable, tessdata))
        return TesseractRuntimeAdmission(
            installed.runtime_root / "engines/tesseract", executable, tessdata, ()
        )

    monkeypatch.setattr(desktop, "admit_packaged_tesseract_runtime", admit)
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", str(tmp_path / "unapproved/qpdf.exe"))
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", str(tmp_path / "unapproved/tesseract.exe"))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", str(tmp_path / "unapproved/tessdata"))
    assert desktop._installed_worker_recovery_authority() == (
        installed.runtime_root,
        installed.package_root.parent,
        installed.qpdf_executable.parent,
        installed.runtime_root / "engines/tesseract",
    )
    assert observed == [(installed.tesseract_executable, installed.tessdata_root)]


def test_source_development_keeps_existing_narrow_recovery_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop, "_installed_runtime", lambda: None)

    def unexpected(*_args: object) -> None:
        raise AssertionError("development must not acquire optional engine authority")

    monkeypatch.setattr(desktop, "admit_packaged_tesseract_runtime", unexpected)
    assert desktop._installed_worker_recovery_authority() == ()


def test_unadmitted_installed_engine_cannot_become_recovery_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "_installed_runtime", lambda: _installed(tmp_path / "runtime"))

    def reject(*_args: object) -> TesseractRuntimeAdmission:
        raise TesseractRuntimeAdmissionError("runtime.hash")

    monkeypatch.setattr(desktop, "admit_packaged_tesseract_runtime", reject)
    with pytest.raises(OcrError) as caught:
        desktop._installed_worker_recovery_authority()
    assert caught.value.code is OcrErrorCode.MODEL_INVALID
    assert caught.value.context == "ocr.recovery_authority"


@pytest.mark.parametrize(
    "kind", ["desktop", "pdf", "pdf-explicit", "flattened", "ocr", "searchable", "quality"]
)
def test_each_composition_forwards_authority_without_changing_active_engine_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    installed = _installed(tmp_path / "runtime")
    runtime = installed.runtime_root
    code = installed.package_root.parent
    authority = (runtime, code, installed.qpdf_executable.parent, runtime / "engines/tesseract")
    monkeypatch.setattr(
        desktop, "_installed_worker_recovery_authority", lambda: authority, raising=False
    )
    monkeypatch.setattr(
        desktop,
        "_worker_runtime_resources",
        lambda: (
            installed.python_executable,
            None,
            installed.package_root / "worker/host_bootstrap.py",
            (runtime,),
            (code,),
            code,
        ),
    )
    launches: list[dict[str, Any]] = []

    def launch(**kwargs: Any) -> Any:
        launches.append(kwargs)
        return cast(Any, SimpleNamespace())

    for module in (
        desktop_composition,
        flattened_pdf_export_composition,
        ocr_composition,
        quality_ocr_review_composition,
        searchable_pdf_export_composition,
    ):
        monkeypatch.setattr(module, "WindowsLaunchConfig", launch)
    qpdf, tesseract, tessdata = (
        installed.qpdf_executable,
        installed.tesseract_executable,
        installed.tessdata_root,
    )
    if kind == "pdf-explicit":
        qpdf = tmp_path / "explicit-test-engine/qpdf.exe"
    if kind == "desktop":
        desktop._desktop_worker_config(POLICY)
    elif kind in {"pdf", "pdf-explicit"}:
        desktop.build_windows_pdf_export_worker_config(qpdf, resource_policy=POLICY)
    elif kind == "flattened":
        desktop.build_windows_flattened_pdf_export_worker_config(qpdf, resource_policy=POLICY)
    elif kind == "ocr":
        desktop.build_windows_ocr_worker_config(tesseract, tessdata, resource_policy=POLICY)
    elif kind == "quality":
        desktop._quality_ocr_review_worker_config(POLICY, tesseract, tessdata)
    else:
        desktop.build_windows_searchable_pdf_export_worker_config(
            qpdf, tesseract, tessdata, resource_policy=POLICY
        )
    assert len(launches) == 1
    selected = launches[0]
    assert selected["recovery_authority_roots"] is authority
    assert selected["runtime_roots"] == (runtime,) and selected["code_roots"] == (code,)
    assert (selected.get("qpdf_executable") is not None) is (
        kind in {"pdf", "pdf-explicit", "flattened", "searchable"}
    )
    if kind == "pdf-explicit":
        assert selected["qpdf_executable"] == qpdf
        assert qpdf.parent not in authority
    assert (selected.get("tesseract_executable") is not None) is (
        kind in {"ocr", "quality", "searchable"}
    )


def test_export_config_preserves_existing_positional_operation_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = _installed(tmp_path / "runtime")
    code = installed.package_root.parent
    monkeypatch.setattr(desktop, "_installed_worker_recovery_authority", lambda: ())
    monkeypatch.setattr(
        desktop,
        "_worker_runtime_resources",
        lambda: (
            installed.python_executable,
            None,
            installed.package_root / "worker/host_bootstrap.py",
            (installed.runtime_root,),
            (code,),
            code,
        ),
    )
    monkeypatch.setattr(
        desktop_composition, "WindowsLaunchConfig", lambda **_kwargs: cast(Any, SimpleNamespace())
    )
    current = desktop.build_windows_pdf_export_worker_config(
        installed.qpdf_executable, resource_policy=POLICY, profile=IMAGE_PDF_EXPORT_PROFILE
    )
    old = desktop_composition.PdfExportWorkerCompositionConfig(
        current.python_executable,
        current.bootstrap_script,
        current.runtime_roots,
        current.code_roots,
        current.working_directory,
        current.qpdf_executable,
        current.resource_policy,
        current.timeout_policy,
        current.maximum_heavy_operation_concurrency,
        current.export_policy,
        current.venv_launcher,
        2,
    )
    assert old.operation_version == 2 and old.recovery_authority_roots == ()
