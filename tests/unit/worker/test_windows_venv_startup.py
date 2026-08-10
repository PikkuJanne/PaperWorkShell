"""Focused contracts for one-process Windows virtual-environment startup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    _minimal_environment_block,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires fixed Windows paths")


def _resources(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    base_runtime = tmp_path / "base-runtime"
    venv_runtime = tmp_path / "venv-runtime"
    scripts = venv_runtime / "Scripts"
    code_root = tmp_path / "code"
    base_runtime.mkdir()
    scripts.mkdir(parents=True)
    code_root.mkdir()
    base_python = base_runtime / "python.exe"
    venv_launcher = scripts / "python.exe"
    bootstrap = code_root / "host_bootstrap.py"
    base_python.write_bytes(b"synthetic base interpreter")
    venv_launcher.write_bytes(b"synthetic venv launcher")
    bootstrap.write_text("# synthetic bootstrap\n", encoding="utf-8")
    return base_runtime, venv_runtime, code_root, base_python, venv_launcher


def _config(tmp_path: Path) -> WindowsLaunchConfig:
    base_runtime, venv_runtime, code_root, base_python, venv_launcher = _resources(tmp_path)
    return WindowsLaunchConfig(
        python_executable=base_python,
        bootstrap_script=code_root / "host_bootstrap.py",
        runtime_roots=(base_runtime, venv_runtime),
        code_roots=(code_root,),
        working_directory=code_root,
        venv_launcher=venv_launcher,
    )


def _environment(buffer: object) -> dict[str, str]:
    rendered = cast(Any, buffer)[:]
    assert type(rendered) is str
    entries = tuple(value for value in rendered.split("\0") if value)
    return dict(entry.split("=", 1) for entry in entries)


def test_config_keeps_base_process_and_validated_venv_identity_distinct(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config.fixed_argv[0] == os.fspath(config.python_executable)
    assert config.venv_launcher is not None
    assert config.venv_launcher != config.python_executable
    assert any(config.venv_launcher.is_relative_to(root) for root in config.runtime_roots)
    rendered = repr(config)
    assert os.fspath(config.python_executable) not in rendered
    assert os.fspath(config.venv_launcher) not in rendered


def test_startup_access_verification_includes_the_fixed_venv_launcher(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert config.required_startup_resources == (
        *config.access_roots,
        config.python_executable,
        config.bootstrap_script,
        config.working_directory,
        config.venv_launcher,
    )


def test_non_venv_startup_needs_no_launcher_hint(tmp_path: Path) -> None:
    base_runtime, _venv_runtime, code_root, base_python, _venv_launcher = _resources(tmp_path)
    config = WindowsLaunchConfig(
        python_executable=base_python,
        bootstrap_script=code_root / "host_bootstrap.py",
        runtime_roots=(base_runtime,),
        code_roots=(code_root,),
        working_directory=code_root,
    )

    environment = _environment(_minimal_environment_block(tmp_path / "profile"))

    assert config.venv_launcher is None
    assert config.required_startup_resources == (
        *config.access_roots,
        config.python_executable,
        config.bootstrap_script,
        config.working_directory,
    )
    assert "__PYVENV_LAUNCHER__" not in environment


def test_minimal_child_environment_contains_only_the_fixed_startup_hint(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    local_app_data = tmp_path / "app-container-profile"
    launcher = config.venv_launcher
    assert launcher is not None

    environment = _environment(_minimal_environment_block(local_app_data, venv_launcher=launcher))

    assert environment["__PYVENV_LAUNCHER__"] == os.fspath(launcher)
    assert set(environment) == {
        "LOCALAPPDATA",
        "PWS_PROFILE_ROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "WINDIR",
        "__PYVENV_LAUNCHER__",
    }
    assert "PATH" not in environment
    assert "PYTHONPATH" not in environment


@pytest.mark.parametrize("failure", ("outside", "same", "missing", "relative"))
def test_config_rejects_an_unapproved_venv_launcher(
    tmp_path: Path,
    failure: str,
) -> None:
    base_runtime, venv_runtime, code_root, base_python, venv_launcher = _resources(tmp_path)
    selected = venv_launcher
    expected_context = "config.venv_launcher"
    if failure == "outside":
        selected = code_root / "outside.exe"
        selected.write_bytes(b"synthetic outside launcher")
    elif failure == "same":
        selected = base_python
    elif failure == "missing":
        selected = venv_runtime / "Scripts" / "missing.exe"
        expected_context = "config.resources"
    else:
        selected = Path("relative-python.exe")
        expected_context = "config.paths"

    with pytest.raises(WindowsLaunchError) as caught:
        WindowsLaunchConfig(
            python_executable=base_python,
            bootstrap_script=code_root / "host_bootstrap.py",
            runtime_roots=(base_runtime, venv_runtime),
            code_roots=(code_root,),
            working_directory=code_root,
            venv_launcher=selected,
        )

    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert caught.value.context == expected_context
    assert os.fspath(selected) not in str(caught.value)
