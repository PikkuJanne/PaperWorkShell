"""Actual native launcher tests using a synthetic child, never user cases.

These prove launcher behavior only. The synthetic child is not CPython and does
not establish a packaged PaperWorkShell or clean-machine qualification result.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest

from paperworkshell.storage._windows import current_profile_root
from paperworkshell.storage.uninstall_review import uninstall_application_guard
from tools import build_libtiff_no_jbig as native
from tools.build_windows_launcher import (
    ARTIFACTS,
    PROJECT_ROOT,
    LauncherBuildError,
    _windows_directory,
    build_launcher,
    verify_launcher,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native launcher contract")
COMPILER = PROJECT_ROOT / "output/pws0801/native-rebuild/repeat-final/toolchain/ucrt64"

PROBE_SOURCE = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
static wchar_t value[32768], system_path[32768], module_path[32768], directory[32768];
static const GUID folder_program_data = {0x62ab5d82,0xfdc1,0x4dc3,{0xa9,0xdd,0x07,0x0d,0x1d,0x49,0x5d,0x97}};
static int equal(const wchar_t *a, const wchar_t *b) { while (*a && *a == *b) { ++a; ++b; } return *a == *b; }
void WINAPI probe_entry(void) {
    int count = 0;
    wchar_t **args = CommandLineToArgvW(GetCommandLineW(), &count);
    DWORD flags = 0, size = 0;
    BOOL review = count == 5 && equal(args[4], L"paperworkshell.ui.uninstall_review");
    BOOL notices = count == 5 && equal(args[4], L"paperworkshell.ui.notices");
    HANDLE file, ready, release;
    DWORD pid = GetCurrentProcessId();
    DWORD folder_flags = 0;
    PWSTR program_data = NULL;
    if (count != 5 || !equal(args[1], L"-I") || !equal(args[2], L"-B") || !equal(args[3], L"-m") || (!review && !notices && !equal(args[4], L"paperworkshell"))) flags |= 1;
    LocalFree(args);
    if (GetEnvironmentVariableW(L"PWS_QPDF_EXECUTABLE", value, 32768) || GetEnvironmentVariableW(L"PWS_TESSERACT_EXECUTABLE", value, 32768) || GetEnvironmentVariableW(L"PYTHONPATH", value, 32768) || GetEnvironmentVariableW(L"QT_PLUGIN_PATH", value, 32768)) flags |= 2;
    GetSystemDirectoryW(system_path, 32768);
    GetEnvironmentVariableW(L"PATH", value, 32768);
    if (!equal(system_path, value)) flags |= 4;
    GetWindowsDirectoryW(system_path, 32768);
    GetEnvironmentVariableW(L"SystemRoot", value, 32768);
    if (!equal(system_path, value)) flags |= 8;
    size = GetModuleFileNameW(NULL, module_path, 32768);
    while (size && module_path[size - 1] != L'\\') --size;
    module_path[size - 1] = 0;
    GetCurrentDirectoryW(32768, directory);
    if (!equal(directory, module_path)) flags |= 16;
    if (!GetEnvironmentVariableW(L"USERPROFILE", value, 32768) || equal(value, L"PRIVATE_TOKEN")) flags |= 32;
    if ((GetErrorMode() & (SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)) != (SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX)) flags |= 64;
    size = GetSystemWindowsDirectoryW(system_path, 32768);
    if (size < 2 || size >= 32768 || !GetEnvironmentVariableW(L"SystemDrive", value, 32768) || value[0] != system_path[0] || value[1] != L':' || value[2]) folder_flags |= 1;
    if (FAILED(SHGetKnownFolderPath(&folder_program_data, 0, NULL, &program_data)) || !program_data) folder_flags |= 2;
    else {
        if (!equal(program_data, L"EXPECTED_PROGRAM_DATA")) folder_flags |= 4;
        if (!((program_data[0] >= L'A' && program_data[0] <= L'Z') || (program_data[0] >= L'a' && program_data[0] <= L'z')) || program_data[1] != L':' || program_data[2] != L'\\') folder_flags |= 8;
        for (PWSTR cursor = program_data; *cursor; ++cursor) if (*cursor == L'%') folder_flags |= 16;
        CoTaskMemFree(program_data);
    }
    file = CreateFileW(L"launcher-known-folder.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) ExitProcess(3);
    WriteFile(file, &folder_flags, sizeof(folder_flags), &size, NULL);
    CloseHandle(file);
    file = CreateFileW(L"launcher-probe.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) ExitProcess(3);
    WriteFile(file, &flags, sizeof(flags), &size, NULL);
    WriteFile(file, &pid, sizeof(pid), &size, NULL);
    CloseHandle(file);
    if (flags) ExitProcess(3);
    if (notices && GetFileAttributesW(L"notices-failure.flag") != INVALID_FILE_ATTRIBUTES) ExitProcess(2);
    if (review) ExitProcess(1);
    ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, L"READY_EVENT_NAME");
    release = OpenEventW(SYNCHRONIZE, FALSE, L"RELEASE_EVENT_NAME");
    if (!ready || !release) ExitProcess(3);
    SetEvent(ready);
    if (WaitForSingleObject(release, 20000) != WAIT_OBJECT_0) ExitProcess(3);
    CloseHandle(ready); CloseHandle(release);
    ExitProcess(0);
}
"""


@dataclass
class Build:
    root: Path
    executable: Path
    receipt: dict[str, object]
    ready: int
    release: int


def _kernel() -> ctypes.WinDLL:
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateEventW.argtypes = (
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    library.CreateEventW.restype = wintypes.HANDLE
    for name in ("SetEvent", "ResetEvent", "CloseHandle"):
        function = getattr(library, name)
        function.argtypes = (wintypes.HANDLE,)
        function.restype = wintypes.BOOL
    library.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    library.WaitForSingleObject.restype = wintypes.DWORD
    library.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    library.OpenProcess.restype = wintypes.HANDLE
    return library


def _program_data_directory() -> str:
    """Independent parent known-folder result; no ambient path expansion."""
    identifier = ctypes.create_string_buffer(UUID("62ab5d82-fdc1-4dc3-a9dd-070d1d495d97").bytes_le)
    shell = ctypes.WinDLL("shell32", winmode=0x800)
    shell.SHGetKnownFolderPath.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    )
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32", winmode=0x800)
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole.CoTaskMemFree.restype = None
    result = ctypes.c_void_p()
    try:
        assert shell.SHGetKnownFolderPath(identifier, 0, None, ctypes.byref(result)) == 0
        assert result.value is not None
        value = ctypes.wstring_at(result)
        assert Path(value).is_absolute() and "%" not in value
        return value
    finally:
        if result.value is not None:
            ole.CoTaskMemFree(result)


@pytest.fixture(scope="module")
def native_launcher() -> Iterator[Build]:
    if not COMPILER.is_dir() or not (PROJECT_ROOT / ARTIFACTS).is_dir():
        pytest.skip(
            "Reviewed offline MSYS2 build inputs unavailable; native qualification remains separate"
        )
    work = PROJECT_ROOT / "output/pws0802" / ("launcher-tests-" + uuid4().hex + " with spaces ä")
    kernel = _kernel()
    ready = release = 0
    try:
        receipt = build_launcher(work, COMPILER)
        identifier = uuid4().hex
        ready_name = "Local\\PaperWorkShell.LauncherTest.Ready." + identifier
        release_name = "Local\\PaperWorkShell.LauncherTest.Release." + identifier
        ready = int(kernel.CreateEventW(None, True, False, ready_name))
        release = int(kernel.CreateEventW(None, True, True, release_name))
        assert ready and release
        probe = PROBE_SOURCE.replace("READY_EVENT_NAME", ready_name.replace("\\", "\\\\")).replace(
            "RELEASE_EVENT_NAME", release_name.replace("\\", "\\\\")
        )
        probe = probe.replace(
            "EXPECTED_PROGRAM_DATA", _program_data_directory().replace("\\", "\\\\")
        )
        (work / "probe.c").write_text(probe, encoding="utf-8")
        windows = _windows_directory()
        environment = {
            "SystemRoot": str(windows),
            "PATH": os.pathsep.join((str(COMPILER / "bin"), str(windows / "System32"))),
            "TEMP": str(work / "temp"),
            "TMP": str(work / "temp"),
        }
        native._run(
            [
                str(COMPILER / "bin/gcc.exe"),
                "-Os",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-stack-protector",
                "-nostdlib",
                "-Wl,--entry,probe_entry,--subsystem,windows,--no-insert-timestamp",
                str(work / "probe.c"),
                "-o",
                str(work / "runtime/PaperWorkShellDesktop.exe"),
                "-lkernel32",
                "-lshell32",
                "-lole32",
            ],
            work=work,
            label="probe",
            environment=environment,
        )
        yield Build(work, PROJECT_ROOT / str(receipt["executable"]), receipt, ready, release)
    finally:
        if release:
            kernel.SetEvent(release)
            kernel.CloseHandle(release)
        if ready:
            kernel.CloseHandle(ready)
        if work.exists():
            resolved = work.resolve(strict=True)
            assert resolved.is_relative_to(
                PROJECT_ROOT / "output/pws0802"
            ) and resolved.name.startswith("launcher-tests-")
            shutil.rmtree(resolved)


def _dismiss_owned_dialogs(pid: int) -> list[str]:
    library = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    library.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    library.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    library.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    library.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    library.FindWindowExW.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
    )
    library.FindWindowExW.restype = wintypes.HWND
    library.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    texts: list[str] = []

    def inspect(window: int, _parameter: int) -> bool:
        process = wintypes.DWORD()
        library.GetWindowThreadProcessId(window, ctypes.byref(process))
        if process.value != pid:
            return True
        name = ctypes.create_unicode_buffer(64)
        library.GetClassNameW(window, name, len(name))
        if name.value != "#32770":
            return True
        child = library.FindWindowExW(window, None, "Static", None)
        while child:
            text = ctypes.create_unicode_buffer(4096)
            library.GetWindowTextW(child, text, len(text))
            texts.append(text.value)
            child = library.FindWindowExW(window, child, "Static", None)
        library.PostMessageW(window, 0x10, 0, 0)  # WM_CLOSE follows the native OK path.
        return True

    callback = callback_type(inspect)
    library.EnumWindows(callback, 0)
    return texts


def _wait(process: subprocess.Popen[bytes]) -> tuple[int, str]:
    end = monotonic() + 15
    messages: list[str] = []
    try:
        while process.poll() is None and monotonic() < end:
            messages.extend(_dismiss_owned_dialogs(process.pid))
            sleep(0.01)
        return process.wait(timeout=2), "\n".join(messages)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_native_launcher_receipt_reverifies_and_rejects_unanchored_receipt(
    native_launcher: Build,
) -> None:
    receipt_path = PROJECT_ROOT / str(native_launcher.receipt["receipt_path"])
    verified = verify_launcher(
        native_launcher.executable,
        receipt_path,
        expected_receipt_sha256=str(native_launcher.receipt["receipt_sha256"]),
    )
    assert verified["sha256"] == native_launcher.receipt["sha256"]
    with pytest.raises(LauncherBuildError, match="LAUNCHER_BUILD_RECEIPT_ANCHOR_REQUIRED"):
        verify_launcher(native_launcher.executable, receipt_path)


def test_native_launcher_rejects_unsupported_arguments_without_echo(native_launcher: Build) -> None:
    process = subprocess.Popen(
        [str(native_launcher.executable), "PRIVATE_TOKEN"], cwd=native_launcher.root
    )
    code, message = _wait(process)
    assert code == 2 and "LAUNCHER_ARGUMENT_INVALID" in message and "PRIVATE_TOKEN" not in message


def test_native_launcher_uses_fixed_child_arguments_known_folders_and_clean_environment(
    native_launcher: Build,
) -> None:
    environment = {
        "PATH": "PRIVATE_TOKEN",
        "USERPROFILE": str(current_profile_root()),
        "SystemRoot": str(_windows_directory()),
        "PWS_QPDF_EXECUTABLE": "PRIVATE_TOKEN",
        "PWS_TESSERACT_EXECUTABLE": "PRIVATE_TOKEN",
        "PYTHONPATH": "PRIVATE_TOKEN",
        "QT_PLUGIN_PATH": "PRIVATE_TOKEN",
    }
    process = subprocess.Popen(
        [str(native_launcher.executable)], cwd=native_launcher.root, env=environment
    )
    code, message = _wait(process)
    proof_path = native_launcher.executable.parent / "launcher-probe.bin"
    proof = proof_path.read_bytes() if proof_path.exists() else b""
    assert code == 0 and not message, (code, message, int.from_bytes(proof[:4], "little"))
    assert len(proof) == 8 and proof[:4] == bytes(4)


def test_native_launcher_fails_closed_when_windows_folder_expansion_is_poisoned(
    native_launcher: Build,
) -> None:
    proof_path = native_launcher.executable.parent / "launcher-probe.bin"
    before = proof_path.read_bytes() if proof_path.exists() else b""
    environment = {"SystemRoot": str(_windows_directory()), "USERPROFILE": "PRIVATE_TOKEN"}
    process = subprocess.Popen(
        [str(native_launcher.executable)], cwd=native_launcher.root, env=environment
    )
    code, message = _wait(process)
    assert code == 2 and "LAUNCHER_PROFILE_UNAVAILABLE" in message
    assert "PRIVATE_TOKEN" not in message
    assert (proof_path.read_bytes() if proof_path.exists() else b"") == before


def test_native_review_mode_propagates_cancel_and_delegates_application_mutex(
    native_launcher: Build,
) -> None:
    with uninstall_application_guard():
        process = subprocess.Popen(
            [str(native_launcher.executable), "--review-uninstall"], cwd=native_launcher.root
        )
        code, message = _wait(process)
    assert code == 1 and not message


def test_native_launcher_holds_mutex_and_kills_child_if_launcher_is_terminated(
    native_launcher: Build,
) -> None:
    kernel = _kernel()
    kernel.ResetEvent(native_launcher.ready)
    kernel.ResetEvent(native_launcher.release)
    process = subprocess.Popen([str(native_launcher.executable)], cwd=native_launcher.root)
    child = None
    try:
        assert kernel.WaitForSingleObject(native_launcher.ready, 10_000) == 0
        proof = (native_launcher.executable.parent / "launcher-probe.bin").read_bytes()
        child_pid = int.from_bytes(proof[4:8], "little")
        child = kernel.OpenProcess(0x00100000, False, child_pid)
        assert child and kernel.WaitForSingleObject(child, 0) == 258
        blocked = subprocess.Popen([str(native_launcher.executable)], cwd=native_launcher.root)
        code, message = _wait(blocked)
        assert code == 2 and "LAUNCHER_APPLICATION_BUSY" in message
        process.terminate()
        process.wait(timeout=5)
        assert kernel.WaitForSingleObject(child, 10_000) == 0
        with uninstall_application_guard():
            pass
    finally:
        kernel.SetEvent(native_launcher.release)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child:
            kernel.CloseHandle(child)


def test_native_launcher_refuses_generic_pythonw_when_product_gui_alias_is_missing(
    native_launcher: Build,
) -> None:
    runtime = native_launcher.executable.parent
    alias = runtime / "PaperWorkShellDesktop.exe"
    held = runtime / "held-product-gui-probe.bin"
    generic = runtime / "pythonw.exe"
    proof_path = runtime / "launcher-probe.bin"
    before = proof_path.read_bytes() if proof_path.exists() else b""
    kernel = _kernel()
    kernel.ResetEvent(native_launcher.ready)
    kernel.SetEvent(native_launcher.release)
    assert alias.is_file() and not held.exists() and not generic.exists()
    # This fixture owns a new, bounded synthetic runtime. Leave a working
    # generic interpreter present while only the product-specific alias is absent.
    shutil.copyfile(alias, generic)
    alias.rename(held)
    try:
        process = subprocess.Popen([str(native_launcher.executable)], cwd=native_launcher.root)
        code, message = _wait(process)
        assert code == 2 and "LAUNCHER_RUNTIME_UNAVAILABLE" in message
        assert "pythonw.exe" not in message and "PaperWorkShellDesktop.exe" not in message
        assert (proof_path.read_bytes() if proof_path.exists() else b"") == before
        assert kernel.WaitForSingleObject(native_launcher.ready, 0) == 258
    finally:
        held.rename(alias)
        generic.unlink()


@pytest.mark.parametrize("ambient_drive", [None, r"Z:\PRIVATE_TOKEN", "%PRIVATE_TOKEN%"])
def test_native_launcher_derives_system_drive_and_absolute_program_data(
    native_launcher: Build, ambient_drive: str | None
) -> None:
    environment = {
        "SystemRoot": str(_windows_directory()),
        "USERPROFILE": str(current_profile_root()),
        "PATH": "PRIVATE_TOKEN",
        "PYTHONPATH": "PRIVATE_TOKEN",
        "QT_PLUGIN_PATH": "PRIVATE_TOKEN",
    }
    if ambient_drive is not None:
        environment["SystemDrive"] = ambient_drive
    proof_path = native_launcher.executable.parent / "launcher-known-folder.bin"
    proof_path.unlink(missing_ok=True)
    process = subprocess.Popen(
        [str(native_launcher.executable)], cwd=native_launcher.root, env=environment
    )
    code, message = _wait(process)
    proof = proof_path.read_bytes() if proof_path.exists() else b""
    assert code == 0 and not message
    assert len(proof) == 4 and proof == bytes(4), int.from_bytes(proof, "little")


def test_native_notices_mode_uses_privacy_environment_and_delegates_viewer_guard(
    native_launcher: Build,
) -> None:
    environment = {
        "SystemRoot": str(_windows_directory()),
        "USERPROFILE": str(current_profile_root()),
        "SystemDrive": r"Z:\PRIVATE_TOKEN",
        "PATH": "PRIVATE_TOKEN",
        "PYTHONPATH": "PRIVATE_TOKEN",
        "QT_PLUGIN_PATH": "PRIVATE_TOKEN",
    }
    with uninstall_application_guard():
        process = subprocess.Popen(
            [str(native_launcher.executable), "--notices"],
            cwd=native_launcher.root,
            env=environment,
        )
        code, message = _wait(process)
    assert code == 0 and not message, (code, message)
    # Exact fixed module/isolated argv, inherited 0x8003 error mode, sanitized
    # environment and native known folders are checked inside the real child.
    assert (native_launcher.executable.parent / "launcher-probe.bin").read_bytes()[:4] == bytes(4)
    assert (native_launcher.executable.parent / "launcher-known-folder.bin").read_bytes() == bytes(
        4
    )


@pytest.mark.parametrize("extra", ["PRIVATE_TOKEN", r"C:\PRIVATE_TOKEN.pdf", "--review-uninstall"])
def test_native_notices_mode_rejects_extra_options_and_paths_without_child(
    native_launcher: Build, extra: str
) -> None:
    proof = native_launcher.executable.parent / "launcher-probe.bin"
    before = proof.read_bytes() if proof.exists() else b""
    process = subprocess.Popen(
        [str(native_launcher.executable), "--notices", extra], cwd=native_launcher.root
    )
    code, message = _wait(process)
    assert code == 2 and "LAUNCHER_ARGUMENT_INVALID" in message
    assert "PRIVATE_TOKEN" not in message
    assert (proof.read_bytes() if proof.exists() else b"") == before


def test_native_notices_failure_is_sanitized_and_returns_failure(native_launcher: Build) -> None:
    marker = native_launcher.executable.parent / "notices-failure.flag"
    marker.write_bytes(b"owned synthetic child failure")
    try:
        process = subprocess.Popen(
            [str(native_launcher.executable), "--notices"], cwd=native_launcher.root
        )
        code, message = _wait(process)
        assert code == 2 and "LAUNCHER_NOTICES_FAILED" in message
        assert "PRIVATE_TOKEN" not in message and "notices-failure.flag" not in message
    finally:
        marker.unlink()


def test_native_notices_mode_keeps_child_job_until_natural_exit_or_launcher_termination(
    native_launcher: Build,
) -> None:
    kernel = _kernel()
    kernel.ResetEvent(native_launcher.ready)
    kernel.ResetEvent(native_launcher.release)
    process = subprocess.Popen(
        [str(native_launcher.executable), "--notices"], cwd=native_launcher.root
    )
    child = None
    try:
        assert kernel.WaitForSingleObject(native_launcher.ready, 10_000) == 0
        proof = (native_launcher.executable.parent / "launcher-probe.bin").read_bytes()
        assert len(proof) == 8 and proof[:4] == bytes(4)
        child = kernel.OpenProcess(0x00100000, False, int.from_bytes(proof[4:8], "little"))
        assert child and kernel.WaitForSingleObject(child, 0) == 258
        process.terminate()
        process.wait(timeout=5)
        assert kernel.WaitForSingleObject(child, 10_000) == 0
        with uninstall_application_guard():
            pass
    finally:
        kernel.SetEvent(native_launcher.release)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child:
            kernel.CloseHandle(child)
