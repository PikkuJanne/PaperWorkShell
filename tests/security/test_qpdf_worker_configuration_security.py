"""PWS-0112 security checks for optional qpdf worker configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from paperworkshell.worker._windows_runtime import _ALLOWED_ENVIRONMENT
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0112 launch security checks target the supported Windows platform",
)


def _base_configuration(tmp_path: Path, qpdf_executable: Path) -> WindowsLaunchConfig:
    runtime_root = tmp_path / "runtime"
    code_root = tmp_path / "code"
    runtime_root.mkdir(exist_ok=True)
    code_root.mkdir(exist_ok=True)
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    python_executable.write_bytes(b"synthetic python marker")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    return WindowsLaunchConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=(runtime_root,),
        code_roots=(code_root,),
        working_directory=code_root,
        qpdf_executable=qpdf_executable,
    )


@pytest.mark.parametrize(
    "invalid_qpdf",
    (
        Path("relative-secret-qpdf.exe"),
        cast(Path, "not-a-path-secret-qpdf.exe"),
    ),
)
def test_qpdf_configuration_rejects_non_absolute_or_untyped_values_without_disclosure(
    tmp_path: Path,
    invalid_qpdf: Path,
) -> None:
    with pytest.raises(WindowsLaunchError) as caught:
        _base_configuration(tmp_path, invalid_qpdf)

    rendered = f"{caught.value!s} {caught.value!r}"
    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert "secret-qpdf" not in rendered


def test_qpdf_configuration_rejects_missing_file_without_disclosure(
    tmp_path: Path,
) -> None:
    missing_qpdf = tmp_path / "private" / "missing-qpdf.exe"

    with pytest.raises(WindowsLaunchError) as caught:
        _base_configuration(tmp_path, missing_qpdf)

    rendered = f"{caught.value!s} {caught.value!r}"
    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert os.fspath(missing_qpdf) not in rendered
    assert missing_qpdf.name not in rendered


def test_qpdf_configuration_rejects_reparse_file_without_disclosure(
    tmp_path: Path,
) -> None:
    qpdf_root = tmp_path / "approved-tools"
    qpdf_root.mkdir()
    target = qpdf_root / "private-target-qpdf.exe"
    link = qpdf_root / "linked-qpdf.exe"
    target.write_bytes(b"synthetic qpdf marker")
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"creating a test reparse point is unavailable ({error.winerror})")

    with pytest.raises(WindowsLaunchError) as caught:
        _base_configuration(tmp_path, link)

    rendered = f"{caught.value!s} {caught.value!r}"
    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert os.fspath(target) not in rendered
    assert os.fspath(link) not in rendered
    assert target.name not in rendered
    assert link.name not in rendered


def test_qpdf_configuration_rejects_named_data_stream_without_disclosure(
    tmp_path: Path,
) -> None:
    qpdf_root = tmp_path / "approved-tools"
    qpdf_root.mkdir()
    base = qpdf_root / "qpdf.exe"
    base.write_bytes(b"synthetic qpdf marker")
    stream_token = "private-stream-canary"
    stream = Path(f"{base}:{stream_token}")
    try:
        stream.write_bytes(b"hidden synthetic stream")
    except OSError as error:
        pytest.skip(
            f"alternate data streams are unavailable on this test volume ({error.winerror})"
        )
    assert stream.is_file()

    with pytest.raises(WindowsLaunchError) as caught:
        _base_configuration(tmp_path, stream)

    rendered = f"{caught.value!s} {caught.value!r}"
    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert caught.value.context == "config.paths"
    assert stream_token not in rendered
    assert stream.read_bytes() == b"hidden synthetic stream"


def test_worker_environment_allowlist_remains_closed() -> None:
    assert _ALLOWED_ENVIRONMENT == frozenset(
        {
            "SYSTEMROOT",
            "WINDIR",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "PWS_QPDF_EXECUTABLE",
            "PWS_PROFILE_ROOT",
            "PWS_TESSDATA_ROOT",
            "PWS_TESSERACT_EXECUTABLE",
            "PYTHONIOENCODING",
            "PYTHONUTF8",
        }
    )
    assert {
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "PWS_QPDF_ARGUMENTS",
        "PWS_QPDF_DIRECTORY",
    }.isdisjoint(_ALLOWED_ENVIRONMENT)
