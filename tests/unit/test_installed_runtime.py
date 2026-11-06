"""Fixed installed engine discovery, refusal paths and desktop wiring."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.installed_runtime import (
    INSTALLED_APPLICATION_ID,
    INSTALLED_GUI_EXECUTABLE_NAME,
    INSTALLED_MARKER_NAME,
    MAX_INSTALLED_MARKER_BYTES,
    InstalledRuntime,
    InstalledRuntimeError,
    discover_installed_runtime,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1


def _marker() -> dict[str, object]:
    return {
        "schema_version": 1,
        "application_id": INSTALLED_APPLICATION_ID,
        "version": "0.0.0",
        "channel": "engineering",
        "candidate_id": "pws0802-internal-20261103-r1",
    }


@pytest.fixture
def installed_tree(tmp_path: Path) -> Path:
    runtime = tmp_path / "PaperWorkShell" / "runtime"
    for relative in (
        "python.exe",
        "pythonw.exe",
        INSTALLED_GUI_EXECUTABLE_NAME,
        "python314._pth",
        "Lib/site-packages/paperworkshell/__main__.py",
        "Lib/site-packages/paperworkshell/worker/host_bootstrap.py",
        "engines/qpdf/bin/qpdf.exe",
        "engines/tesseract/bin/tesseract.exe",
    ):
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic installed resource")
    (runtime / "engines/tesseract/share/tessdata").mkdir(parents=True)
    (runtime / INSTALLED_MARKER_NAME).write_text(json.dumps(_marker()), encoding="utf-8")
    return runtime


def _discover(runtime: Path, **overrides: Path) -> InstalledRuntime | None:
    return discover_installed_runtime(
        package_root=overrides.get("package_root", runtime / "Lib/site-packages/paperworkshell"),
        executable=overrides.get("executable", runtime / "pythonw.exe"),
        prefix=overrides.get("prefix", runtime),
        base_prefix=overrides.get("base_prefix", runtime),
    )


def _require(runtime: Path) -> InstalledRuntime:
    installed = _discover(runtime)
    assert installed is not None
    return installed


def test_installed_resources_are_fixed_relative_to_the_actual_runtime(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", "private-path-canary/qpdf.exe")
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", "private-path-canary/tesseract.exe")
    monkeypatch.setenv("PWS_TESSDATA_ROOT", "private-path-canary/tessdata")
    monkeypatch.setenv("PWS_GUI_EXECUTABLE", "private-path-canary/gui.exe")
    installed = _require(installed_tree)
    assert installed.version == "0.0.0"
    assert installed.candidate_id == "pws0802-internal-20261103-r1"
    assert installed.python_executable == installed_tree / "python.exe"
    assert installed.gui_executable == installed_tree / "PaperWorkShellDesktop.exe"
    assert installed.qpdf_executable == installed_tree / "engines/qpdf/bin/qpdf.exe"
    assert installed.tesseract_executable == installed_tree / "engines/tesseract/bin/tesseract.exe"
    assert installed.tessdata_root == installed_tree / "engines/tesseract/share/tessdata"
    assert _discover(installed_tree, executable=installed_tree / "python.exe") == installed
    assert str(installed_tree) not in repr(installed)
    assert "private-path-canary" not in repr(installed)


def test_named_gui_interpreter_is_discovered_without_changing_worker_interpreter(
    installed_tree: Path,
) -> None:
    gui = installed_tree / INSTALLED_GUI_EXECUTABLE_NAME
    installed = _discover(installed_tree, executable=gui)
    assert installed is not None
    assert installed.gui_executable == gui
    assert installed.python_executable == installed_tree / "python.exe"


def test_missing_named_gui_is_rejected_from_original_helper_interpreter(
    installed_tree: Path,
) -> None:
    (installed_tree / INSTALLED_GUI_EXECUTABLE_NAME).unlink(missing_ok=True)
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree, executable=installed_tree / "python.exe")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 0),
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", "1"),
        ("application_id", "private-path-canary"),
        ("channel", "stable"),
        ("channel", None),
        ("version", "1.0"),
        ("version", "01.0.0"),
        ("version", "0.0.0\n"),
        ("version", "0.0.0;command"),
        ("version", 1),
        ("candidate_id", ""),
        ("candidate_id", "../private-path-canary"),
        ("candidate_id", "a" * 97),
        ("candidate_id", "candidate\n"),
        ("candidate_id", "candidate\N{SNOWMAN}"),
        ("engine_path", "private-path-canary"),
    ],
)
def test_closed_marker_rejects_unknown_versions_fields_and_values(
    installed_tree: Path, key: str, value: object
) -> None:
    marker = _marker()
    marker[key] = value
    (installed_tree / INSTALLED_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(InstalledRuntimeError) as caught:
        _discover(installed_tree)
    assert caught.value.code == "INSTALLED_RUNTIME_INVALID"
    assert "private-path-canary" not in str(caught.value)
    assert str(installed_tree) not in repr(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("missing", list(_marker()))
def test_marker_requires_every_identity_field(installed_tree: Path, missing: str) -> None:
    marker = _marker()
    del marker[missing]
    (installed_tree / INSTALLED_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xffprivate-path-canary",
        b"[]",
        b"null",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b"[" * 2000,
        b" " * (MAX_INSTALLED_MARKER_BYTES + 1),
    ],
)
def test_marker_parser_is_bounded_and_failure_is_sanitized(
    installed_tree: Path, content: bytes
) -> None:
    (installed_tree / INSTALLED_MARKER_NAME).write_bytes(content)
    with pytest.raises(InstalledRuntimeError) as caught:
        _discover(installed_tree)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private-path-canary" not in str(caught.value)


@pytest.mark.parametrize(
    "resource",
    [
        INSTALLED_MARKER_NAME,
        "python.exe",
        "pythonw.exe",
        INSTALLED_GUI_EXECUTABLE_NAME,
        "python314._pth",
        "Lib/site-packages/paperworkshell/__main__.py",
        "Lib/site-packages/paperworkshell/worker/host_bootstrap.py",
        "engines/qpdf/bin/qpdf.exe",
        "engines/tesseract/bin/tesseract.exe",
        "engines/tesseract/share/tessdata",
    ],
)
def test_missing_installed_resource_never_falls_back_to_an_external_engine(
    installed_tree: Path, resource: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = installed_tree / resource
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", "external-engine-canary")
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree)


@pytest.mark.parametrize(
    "resource", [INSTALLED_MARKER_NAME, "engines/qpdf/bin/qpdf.exe", INSTALLED_GUI_EXECUTABLE_NAME]
)
def test_filesystem_aliases_are_refused(
    installed_tree: Path, tmp_path: Path, resource: str
) -> None:
    os.link(installed_tree / resource, tmp_path / "alias.bin")
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree)


def test_named_gui_resource_must_be_an_ordinary_file(installed_tree: Path) -> None:
    gui = installed_tree / INSTALLED_GUI_EXECUTABLE_NAME
    gui.unlink()
    gui.mkdir()
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree)


@pytest.mark.parametrize("changed_field", ["st_ctime_ns", "st_mtime_ns", "st_ino", "st_size"])
def test_marker_read_checks_content_identity_without_relying_on_windows_change_time(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch, changed_field: str
) -> None:
    original = os.fstat

    class Metadata:
        def __init__(self, metadata: os.stat_result) -> None:
            self._metadata = metadata

        def __getattr__(self, name: str) -> int:
            value = cast(int, getattr(self._metadata, name))
            return value + 1 if name == changed_field else value

    def altered(descriptor: int) -> os.stat_result:
        return cast(os.stat_result, Metadata(original(descriptor)))

    monkeypatch.setattr(os, "fstat", altered)
    if changed_field == "st_ctime_ns":
        # Windows metadata change time can advance on opening an unchanged file.
        assert _require(installed_tree).version == "0.0.0"
    else:
        with pytest.raises(InstalledRuntimeError):
            _discover(installed_tree)


def test_symlinked_engine_directory_is_refused(installed_tree: Path, tmp_path: Path) -> None:
    qpdf = installed_tree / "engines/qpdf"
    moved = tmp_path / "redirected-qpdf"
    qpdf.rename(moved)
    try:
        qpdf.symlink_to(moved, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create the synthetic symlink")
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree)


@pytest.mark.parametrize("parameter", ["package_root", "executable", "prefix", "base_prefix"])
def test_marker_cannot_bind_a_different_package_interpreter_or_virtual_environment(
    installed_tree: Path, tmp_path: Path, parameter: str
) -> None:
    if parameter == "prefix":
        # A marker on the other prefix still cannot authorize this package/interpreter.
        target = tmp_path / "other-runtime"
        target.mkdir()
        (target / INSTALLED_MARKER_NAME).write_text(json.dumps(_marker()), encoding="utf-8")
    else:
        target = tmp_path / "other"
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree, **{parameter: target})


@pytest.mark.parametrize("virtual_environment", [False, True])
def test_source_development_without_a_marker_is_unchanged(
    tmp_path: Path, virtual_environment: bool
) -> None:
    prefix = tmp_path / "development-python"
    prefix.mkdir()
    assert (
        discover_installed_runtime(
            package_root=tmp_path / "src/paperworkshell",
            executable=prefix / "python.exe",
            prefix=prefix,
            base_prefix=tmp_path / "base-python" if virtual_environment else prefix,
        )
        is None
    )


def test_missing_embedded_marker_is_refused_even_when_source_code_was_injected(
    installed_tree: Path, tmp_path: Path
) -> None:
    (installed_tree / INSTALLED_MARKER_NAME).unlink()
    with pytest.raises(InstalledRuntimeError):
        _discover(installed_tree, package_root=tmp_path / "src/paperworkshell")


def _bind_desktop(installed_tree: Path, monkeypatch: pytest.MonkeyPatch) -> InstalledRuntime:
    installed = _require(installed_tree)
    monkeypatch.setattr(desktop, "_installed_runtime", lambda: installed)
    return installed


def test_desktop_ignores_engine_environment_in_installed_mode(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _bind_desktop(installed_tree, monkeypatch)
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", "private-path-canary")
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", "private-path-canary")
    monkeypatch.setenv("PWS_TESSDATA_ROOT", "private-path-canary")
    before = dict(os.environ)
    assert desktop._configured_qpdf_executable(None) == installed.qpdf_executable
    assert desktop._configured_optional_ocr_resources(None, None) == (
        installed.tesseract_executable,
        installed.tessdata_root,
    )
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    assert resources.resource_policy is CONSTRAINED_RESOURCE_POLICY_V1
    assert resources._ocr_executable == installed.tesseract_executable
    assert resources._ocr_tessdata_root == installed.tessdata_root
    assert resources.heavy_operations.snapshot().maximum_concurrency == 1
    assert dict(os.environ) == before


def test_explicit_test_resources_are_preserved_without_environment_fallback(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed = _bind_desktop(installed_tree, monkeypatch)
    explicit = tmp_path / "explicit-engine"
    data = tmp_path / "explicit-data"
    assert desktop._configured_qpdf_executable(explicit) == explicit
    assert desktop._configured_optional_ocr_resources(explicit, data) == (explicit, data)
    assert desktop._configured_optional_ocr_resources(explicit, None) == (
        explicit,
        installed.tessdata_root,
    )


def test_installed_workers_use_only_bundled_interpreter_and_existing_bootstrap(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _bind_desktop(installed_tree, monkeypatch)
    code = installed.package_root.parent
    assert desktop._worker_runtime_resources() == (
        installed.python_executable,
        None,
        installed.package_root / "worker/host_bootstrap.py",
        (installed.runtime_root,),
        (code,),
        code,
    )


@pytest.mark.parametrize(
    "interpreter_name", ["python.exe", "pythonw.exe", INSTALLED_GUI_EXECUTABLE_NAME]
)
def test_real_desktop_discovery_uses_executing_package_and_interpreter(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch, interpreter_name: str
) -> None:
    package = installed_tree / "Lib/site-packages/paperworkshell"
    with monkeypatch.context() as runtime:
        runtime.setattr(desktop, "__file__", str(package / "__main__.py"))
        runtime.setattr(sys, "prefix", str(installed_tree))
        runtime.setattr(sys, "base_prefix", str(installed_tree))
        runtime.setattr(sys, "executable", str(installed_tree / interpreter_name))
        installed = desktop._installed_runtime()
        assert installed is not None
        assert installed.runtime_root == installed_tree
        assert desktop._worker_runtime_resources()[0] == installed_tree / "python.exe"
        assert desktop._configured_qpdf_executable(None) == installed.qpdf_executable


def test_main_reports_invalid_installation_before_creating_backend_or_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[InstalledRuntimeError] = []

    def reject() -> InstalledRuntime | None:
        raise InstalledRuntimeError()

    monkeypatch.setattr(desktop, "_installed_runtime", reject)
    monkeypatch.setattr(desktop, "_show_installed_startup_error", errors.append)
    monkeypatch.setattr(
        desktop,
        "create_desktop_application_resource_context",
        lambda: pytest.fail("invalid installation must not create application resources"),
    )
    assert desktop.main(("PaperWorkShell",)) == 2
    assert len(errors) == 1
    assert errors[0].code == "INSTALLED_RUNTIME_INVALID"


def test_main_sets_installed_identity_before_opening_the_same_resource_bound_window(
    installed_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_desktop(installed_tree, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(desktop, "_set_installed_application_identity", lambda: calls.append("id"))

    def run(factory: Callable[[], object], argv: object, *, resource_policy: object) -> int:
        assert calls == ["id"]
        assert argv == ("PaperWorkShell",)
        assert factory() is resource_policy
        calls.append("window")
        return 17

    monkeypatch.setattr(desktop, "run_desktop_application", run)
    monkeypatch.setattr(desktop, "build_backend", lambda resources: resources.resource_policy)
    assert desktop.main(("PaperWorkShell",)) == 17
    assert calls == ["id", "window"]


def test_native_startup_dialog_has_accessible_plain_text_and_no_private_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class MessageBox:
        argtypes: object
        restype: object

        def __call__(self, *args: object) -> int:
            calls.append(args)
            return 1

    class Library:
        MessageBoxW = MessageBox()

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: Library(), raising=False)
    desktop._show_installed_startup_error(InstalledRuntimeError())
    assert calls == [(None, str(InstalledRuntimeError()), "PaperWorkShell", 0x10010)]
    assert "Reinstall" in cast(str, calls[0][1])


def test_windows_process_identity_uses_the_product_id_and_system_dll_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class Identity:
        argtypes: object
        restype: object

        def __call__(self, value: str) -> int:
            calls.append(value)
            return 0

    class Library:
        SetCurrentProcessExplicitAppUserModelID = Identity()

    def load(name: str, **kwargs: object) -> Any:
        calls.append((name, kwargs))
        return Library()

    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    desktop._set_installed_application_identity()
    assert calls == [
        ("shell32.dll", {"use_last_error": True, "winmode": 0x800}),
        INSTALLED_APPLICATION_ID,
    ]
