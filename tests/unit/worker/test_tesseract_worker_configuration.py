"""PWS-0204 fixed-resource configuration for the OCR worker."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

import paperworkshell.worker.client._windows as windows_launcher
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    _minimal_environment_block,
)
from paperworkshell.worker.client.tesseract_runtime import (
    TesseractRuntimeAdmission,
    TesseractRuntimeAdmissionError,
    admit_packaged_tesseract_runtime,
)
from paperworkshell.worker.protocol.operation_scope import (
    PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT,
    WorkerOperationScope,
)

_REAL_RUNTIME_ADMISSION = admit_packaged_tesseract_runtime

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0204 launch configuration targets the supported Windows platform",
)


@pytest.fixture(autouse=True)
def _admit_synthetic_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def admit(executable: Path, tessdata_root: Path) -> TesseractRuntimeAdmission:
        root = executable.parent.parent
        return TesseractRuntimeAdmission(
            root=root,
            executable=executable,
            tessdata_root=tessdata_root,
            resources=(
                root,
                executable.parent,
                executable.parent / "libtesseract-5.dll",
                executable,
                root / "share",
                tessdata_root,
                tessdata_root / "eng.traineddata",
            ),
        )

    monkeypatch.setattr(windows_launcher, "admit_packaged_tesseract_runtime", admit)


def _resources(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    code_root = tmp_path / "code"
    tool_root = tmp_path / "approved-ocr" / "bin"
    tessdata_root = tmp_path / "approved-ocr" / "share" / "tessdata"
    for directory in (runtime_root, code_root, tool_root, tessdata_root):
        directory.mkdir(parents=True)
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    tesseract_executable = tool_root / "tesseract.exe"
    python_executable.write_bytes(b"synthetic python marker")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    tesseract_executable.write_bytes(b"synthetic tesseract marker")
    (tool_root / "libtesseract-5.dll").write_bytes(b"synthetic dll marker")
    (tessdata_root / "eng.traineddata").write_bytes(b"synthetic language marker")
    return (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        tesseract_executable,
        tessdata_root,
    )


def _config(tmp_path: Path) -> WindowsLaunchConfig:
    (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        tesseract_executable,
        tessdata_root,
    ) = _resources(tmp_path)
    return WindowsLaunchConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=(runtime_root,),
        code_roots=(code_root,),
        working_directory=code_root,
        tesseract_executable=tesseract_executable,
        tessdata_root=tessdata_root,
    )


def _environment_entries(block: ctypes.Array[ctypes.c_wchar]) -> dict[str, str]:
    serialized = ctypes.wstring_at(ctypes.addressof(block), len(block))
    return {
        name.upper(): value
        for item in serialized.split("\0")
        if item
        for name, value in (item.split("=", maxsplit=1),)
    }


def test_tesseract_and_tessdata_are_one_closed_launch_authority(tmp_path: Path) -> None:
    config = _config(tmp_path)
    executable = config.tesseract_executable
    tessdata = config.tessdata_root

    assert executable is not None
    assert tessdata is not None
    assert config.access_roots == (
        *config.runtime_roots,
        *config.code_roots,
        executable.parent.parent,
    )
    assert config.tesseract_access_resources == (
        executable.parent.parent,
        executable.parent,
        executable.parent / "libtesseract-5.dll",
        executable,
        executable.parent.parent / "share",
        tessdata,
        tessdata / "eng.traineddata",
    )
    assert os.fspath(executable) not in repr(config)
    assert os.fspath(tessdata) not in repr(config)


@pytest.mark.parametrize("missing", ["executable", "tessdata"])
def test_tesseract_configuration_requires_both_resources(
    tmp_path: Path,
    missing: str,
) -> None:
    (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        tesseract_executable,
        tessdata_root,
    ) = _resources(tmp_path)

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsLaunchConfig(
            python_executable=python_executable,
            bootstrap_script=bootstrap_script,
            runtime_roots=(runtime_root,),
            code_roots=(code_root,),
            working_directory=code_root,
            tesseract_executable=(None if missing == "executable" else tesseract_executable),
            tessdata_root=None if missing == "tessdata" else tessdata_root,
        )

    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "config.tesseract_pair"


def test_tesseract_inventory_is_reauthenticated_at_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    def reject(_executable: Path, _tessdata_root: Path) -> TesseractRuntimeAdmission:
        raise TesseractRuntimeAdmissionError("hash")

    monkeypatch.setattr(windows_launcher, "admit_packaged_tesseract_runtime", reject)

    with pytest.raises(WindowsLaunchError) as raised:
        _ = config.tesseract_access_resources

    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "config.tesseract_runtime"


def test_windows_launch_boundary_rejects_an_unreviewed_tool_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        windows_launcher,
        "admit_packaged_tesseract_runtime",
        _REAL_RUNTIME_ADMISSION,
    )
    (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        tesseract_executable,
        tessdata_root,
    ) = _resources(tmp_path)

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsLaunchConfig(
            python_executable=python_executable,
            bootstrap_script=bootstrap_script,
            runtime_roots=(runtime_root,),
            code_roots=(code_root,),
            working_directory=code_root,
            tesseract_executable=tesseract_executable,
            tessdata_root=tessdata_root,
        )

    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "config.tesseract_runtime"


def test_tesseract_location_preflight_runs_before_path_type_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        tesseract_executable,
        tessdata_root,
    ) = _resources(tmp_path)

    def reject_location(_executable: Path, _tessdata_root: Path) -> Path:
        raise TesseractRuntimeAdmissionError("location")

    def unexpected_path_probe(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("OCR location preflight must precede pathlib type probes")

    monkeypatch.setattr(
        windows_launcher,
        "preflight_tesseract_runtime_paths",
        reject_location,
    )
    monkeypatch.setattr(Path, "is_file", unexpected_path_probe)
    monkeypatch.setattr(Path, "is_dir", unexpected_path_probe)

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsLaunchConfig(
            python_executable=python_executable,
            bootstrap_script=bootstrap_script,
            runtime_roots=(runtime_root,),
            code_roots=(code_root,),
            working_directory=code_root,
            tesseract_executable=tesseract_executable,
            tessdata_root=tessdata_root,
        )

    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "config.tesseract_location"


def test_minimal_worker_environment_carries_no_ambient_tool_search(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    executable = config.tesseract_executable
    tessdata = config.tessdata_root
    assert executable is not None
    assert tessdata is not None

    entries = _environment_entries(
        _minimal_environment_block(
            tmp_path / "profile",
            tesseract_executable=executable,
            tessdata_root=tessdata,
        )
    )

    assert entries["PWS_TESSERACT_EXECUTABLE"] == os.fspath(executable)
    assert entries["PWS_TESSDATA_ROOT"] == os.fspath(tessdata)
    assert PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT not in entries
    assert (
        not {
            "PATH",
            "PATHEXT",
            "COMSPEC",
            "TESSDATA_PREFIX",
            "HTTP_PROXY",
            "HTTPS_PROXY",
        }
        & entries.keys()
    )


def test_quality_worker_environment_carries_only_the_closed_operation_scope(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    executable = config.tesseract_executable
    tessdata = config.tessdata_root
    assert executable is not None
    assert tessdata is not None

    entries = _environment_entries(
        _minimal_environment_block(
            tmp_path / "profile",
            tesseract_executable=executable,
            tessdata_root=tessdata,
            operation_scope=WorkerOperationScope.QUALITY_OCR_REVIEW,
        )
    )

    assert entries[PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT] == (
        WorkerOperationScope.QUALITY_OCR_REVIEW.value
    )
