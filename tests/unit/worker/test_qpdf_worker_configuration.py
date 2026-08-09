"""PWS-0112 unit coverage for the trusted optional qpdf worker resource."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import cast

import pytest

import paperworkshell.worker.client._windows as windows_launcher
from paperworkshell.pdf.export_adapter import PdfExportAdapter
from paperworkshell.pdf.qpdf_checker import (
    DEFAULT_QPDF_EXECUTABLE,
    QpdfCommandChecker,
)
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    _AccessGrantLease,
    _minimal_environment_block,
)
from paperworkshell.worker.operations import PdfExportOperationHandler
from paperworkshell.worker.pdf_export_operation import (
    WindowsPdfExportOperationAdapter,
    build_pdf_export_operation_handler,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0112 launch configuration targets the supported Windows platform",
)


def _resource_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    code_root = tmp_path / "code"
    qpdf_root = tmp_path / "approved tools" / "qpdf" / "bin"
    runtime_root.mkdir()
    code_root.mkdir()
    qpdf_root.mkdir(parents=True)
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    qpdf_executable = qpdf_root / "qpdf.exe"
    python_executable.write_bytes(b"synthetic python marker")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    qpdf_executable.write_bytes(b"synthetic qpdf marker")
    return (
        runtime_root,
        code_root,
        python_executable,
        bootstrap_script,
        qpdf_executable,
    )


def _launch_config(tmp_path: Path, *, include_qpdf: bool) -> WindowsLaunchConfig:
    runtime_root, code_root, python_executable, bootstrap_script, qpdf_executable = _resource_paths(
        tmp_path
    )
    return WindowsLaunchConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=(runtime_root,),
        code_roots=(code_root,),
        working_directory=code_root,
        qpdf_executable=qpdf_executable if include_qpdf else None,
    )


def _environment_entries(
    block: ctypes.Array[ctypes.c_wchar],
) -> dict[str, str]:
    serialized = ctypes.wstring_at(ctypes.addressof(block), len(block))
    return {
        name.upper(): value
        for item in serialized.split("\0")
        if item
        for name, value in (item.split("=", maxsplit=1),)
    }


def test_optional_qpdf_is_a_fixed_sanitized_launch_resource(tmp_path: Path) -> None:
    config = _launch_config(tmp_path, include_qpdf=True)
    qpdf_executable = config.qpdf_executable
    adjacent_dll = qpdf_executable.parent / "qpdf29.dll" if qpdf_executable else None

    assert qpdf_executable is not None
    assert adjacent_dll is not None
    adjacent_dll.write_bytes(b"synthetic adjacent dependency marker")
    assert config.access_roots == (
        *config.runtime_roots,
        *config.code_roots,
        qpdf_executable.parent,
    )
    assert config.qpdf_access_resources == (
        qpdf_executable.parent,
        qpdf_executable,
        adjacent_dll,
    )
    assert config.fixed_argv == (
        os.fspath(config.python_executable),
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        os.fspath(config.bootstrap_script),
    )
    assert os.fspath(qpdf_executable) not in repr(config)


def test_default_launch_configuration_is_unchanged(tmp_path: Path) -> None:
    config = _launch_config(tmp_path, include_qpdf=False)

    assert config.qpdf_executable is None
    assert config.access_roots == config.runtime_roots + config.code_roots


def test_minimal_environment_adds_only_explicit_qpdf(tmp_path: Path) -> None:
    local_app_data = tmp_path / "profile"
    qpdf_executable = tmp_path / "approved tools" / "qpdf.exe"
    default_entries = _environment_entries(_minimal_environment_block(local_app_data))
    configured_entries = _environment_entries(
        _minimal_environment_block(local_app_data, qpdf_executable)
    )

    assert set(default_entries) == {
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    assert configured_entries == {
        **default_entries,
        "PWS_QPDF_EXECUTABLE": os.fspath(qpdf_executable),
    }
    assert (
        not {
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "HTTP_PROXY",
            "HTTPS_PROXY",
        }
        & configured_entries.keys()
    )


def test_existing_appcontainer_access_does_not_mutate_qpdf_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qpdf_executable = tmp_path / "Program Files" / "qpdf" / "bin" / "qpdf.exe"
    lease = _AccessGrantLease(sid=ctypes.c_void_p(1))

    monkeypatch.setattr(
        windows_launcher,
        "_has_effective_app_container_read_execute",
        lambda _path, _sid: True,
    )

    def unexpected_grant(_self: _AccessGrantLease, _paths: tuple[Path, ...]) -> None:
        pytest.fail("an already-readable qpdf resource must not receive an ACL grant")

    monkeypatch.setattr(_AccessGrantLease, "grant_all", unexpected_grant)

    lease.ensure_read_execute(
        (qpdf_executable.parent,),
        (qpdf_executable.parent, qpdf_executable),
    )

    assert lease.paths == []
    assert lease.observed_objects == []


def test_missing_appcontainer_access_uses_existing_reversible_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qpdf_executable = tmp_path / "runner-temp" / "qpdf" / "qpdf.exe"
    lease = _AccessGrantLease(sid=ctypes.c_void_p(1))
    granted: list[tuple[Path, ...]] = []
    access_granted = False

    def has_access(_path: Path, _sid: ctypes.c_void_p) -> bool:
        return access_granted

    def grant(_self: _AccessGrantLease, paths: tuple[Path, ...]) -> None:
        nonlocal access_granted
        granted.append(paths)
        access_granted = True

    monkeypatch.setattr(
        windows_launcher,
        "_has_effective_app_container_read_execute",
        has_access,
    )
    monkeypatch.setattr(_AccessGrantLease, "grant_all", grant)

    lease.ensure_read_execute(
        (qpdf_executable.parent,),
        (qpdf_executable.parent, qpdf_executable),
    )

    assert granted == [(qpdf_executable.parent,)]


def test_effective_access_never_ignores_a_package_deny() -> None:
    required = windows_launcher._FILE_GENERIC_READ_EXECUTE

    assert windows_launcher._masks_grant_app_container_read_execute(
        (0, 0),
        (required, 0),
    )
    assert not windows_launcher._masks_grant_app_container_read_execute(
        (0, required),
        (required, 0),
    )
    assert not windows_launcher._masks_grant_app_container_read_execute(
        (required, 0),
        (0, required),
    )


def test_access_classifier_ignores_inherit_only_allow_and_blocks_foreign_deny() -> None:
    required = windows_launcher._FILE_GENERIC_READ_EXECUTE
    inherit_only_allow = windows_launcher._classify_access_ace(
        windows_launcher._ACCESS_ALLOWED_ACE_TYPE,
        windows_launcher._INHERIT_ONLY_ACE,
        required,
        trustee_matches=True,
    )
    foreign_deny = windows_launcher._classify_access_ace(
        windows_launcher._ACCESS_DENIED_ACE_TYPE,
        0,
        required,
        trustee_matches=False,
    )

    assert inherit_only_allow == (0, 0)
    assert foreign_deny == (0, required)
    assert not windows_launcher._masks_grant_app_container_read_execute(
        inherit_only_allow,
        (0, 0),
    )
    assert not windows_launcher._masks_grant_app_container_read_execute(
        (required, foreign_deny[1]),
        (0, 0),
    )
    assert not windows_launcher._masks_grant_app_container_read_execute(
        (required, 0),
        (0, windows_launcher._GENERIC_ALL),
    )


def test_export_host_uses_default_qpdf_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_QPDF_EXECUTABLE", raising=False)

    handler = build_pdf_export_operation_handler()
    operation_adapter = cast(WindowsPdfExportOperationAdapter, handler.adapter)
    candidate_adapter = cast(PdfExportAdapter, operation_adapter.candidate_adapter)
    checker = cast(QpdfCommandChecker, candidate_adapter.qpdf_checker)

    assert type(handler) is PdfExportOperationHandler
    assert type(operation_adapter) is WindowsPdfExportOperationAdapter
    assert type(candidate_adapter) is PdfExportAdapter
    assert type(checker) is QpdfCommandChecker
    assert checker.executable == DEFAULT_QPDF_EXECUTABLE


def test_export_host_uses_only_exact_configured_qpdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    qpdf_executable = tmp_path / "approved qpdf" / "qpdf.exe"
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf_executable))

    handler = build_pdf_export_operation_handler()
    operation_adapter = cast(WindowsPdfExportOperationAdapter, handler.adapter)
    candidate_adapter = cast(PdfExportAdapter, operation_adapter.candidate_adapter)
    checker = cast(QpdfCommandChecker, candidate_adapter.qpdf_checker)

    assert type(checker) is QpdfCommandChecker
    assert os.fspath(checker.executable) == os.fspath(qpdf_executable)
    assert os.fspath(qpdf_executable) not in " ".join(
        (
            repr(handler),
            repr(operation_adapter),
            repr(candidate_adapter),
            repr(checker),
        )
    )
