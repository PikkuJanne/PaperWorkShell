"""Owned HOST_NOT_CLEAN installer observations; never a clean-machine certificate.

No normal desktop launch or bulk deletion of the current user's cases occurs.
The optional installed-runtime harness uses an explicitly isolated synthetic
manager through an existing internal composition seam, not a product API.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from typing import Any, Final, NoReturn
from uuid import uuid4

from tools import build_windows_installer as builder
from tools.release_artifacts import file_hashes, plain_path, safe_relative
from tools.release_evidence import load_json
from tools.release_staging import verify_files

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
SESSION = "qualification-session.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}_is1"
MAX_RECORDS: Final = 10_000
QUIET_ARGUMENTS: Final = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")
_INTERRUPTION_NOTICE: Final = "notices/UNSIGNED_INTERNAL.txt"
_INTERRUPTION_BARRIER: Final = "notices/build-support/gcc-runtime/COPYING.RUNTIME"
FIXTURES: Final = {
    "source.pdf": (
        "tests/fixtures/synthetic/v1/pdf/born-digital.pdf",
        "3f62fe484b8b389ec18afe115939d491f795e517195a7acf7fe5989075839c47",
    ),
    "ocr.png": (
        "tests/fixtures/synthetic/v1/image/ocr-raster-text.png",
        "f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82",
    ),
}


class QualificationError(ValueError):
    """Path-free refusal; raw host exceptions never become a public receipt."""


def fail(code: str) -> NoReturn:
    raise QualificationError(code)


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", ctypes.c_ulong),
        ("offset_high", ctypes.c_ulong),
        ("event", ctypes.c_void_p),
    ]


class _OplockInput(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_ushort),
        ("length", ctypes.c_ushort),
        ("level", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
    ]


class _OplockOutput(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_ushort),
        ("length", ctypes.c_ushort),
        ("original_level", ctypes.c_ulong),
        ("new_level", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("access", ctypes.c_ulong),
        ("share", ctypes.c_ushort),
    ]


def _interruption_kernel() -> Any:
    """Only process-local, held-handle Windows operations; no PID discovery."""
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CloseHandle": ((ctypes.c_void_p,), ctypes.c_int),
        "ReOpenFile": (
            (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong),
            ctypes.c_void_p,
        ),
        "CreateEventW": (
            (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p),
            ctypes.c_void_p,
        ),
        "DeviceIoControl": (
            (
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ),
            ctypes.c_int,
        ),
        "GetOverlappedResult": (
            (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int),
            ctypes.c_int,
        ),
        "WaitForSingleObject": ((ctypes.c_void_p, ctypes.c_ulong), ctypes.c_ulong),
        "TerminateJobObject": ((ctypes.c_void_p, ctypes.c_uint), ctypes.c_int),
        "QueryFullProcessImageNameW": (
            (ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_void_p),
            ctypes.c_int,
        ),
        "GetProcessTimes": (
            (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p),
            ctypes.c_int,
        ),
        "SetErrorMode": ((ctypes.c_uint,), ctypes.c_uint),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    return library


class _ReplacementBarrier(AbstractContextManager["_ReplacementBarrier"]):
    """RH oplock: compatible reads proceed; incompatible replacement waits.

    The file is reopened from an authenticated storage handle, not by path.
    A break is accepted only when Windows requires acknowledgement. The caller
    keeps this handle open until its contained writer has completely stopped.
    See Microsoft FSCTL_REQUEST_OPLOCK and Types of Opportunistic Locks.
    """

    def __init__(self, authenticated_handle: int) -> None:
        self.kernel = _interruption_kernel()
        self.file = 0
        self.event = 0
        self.request = _OplockInput(1, ctypes.sizeof(_OplockInput), 3, 1)
        self.output = _OplockOutput()
        self.overlapped = _Overlapped()
        try:
            # READ | READ_ATTRIBUTES, SHARE_READ, OVERLAPPED. Write/delete are
            # deliberately not shared: their open must wait for an RH break ACK.
            self.file = int(
                self.kernel.ReOpenFile(authenticated_handle, 0x80000080, 1, 0x40000000) or 0
            )
            if not self.file or self.file == ctypes.c_void_p(-1).value:
                self.file = 0
                fail("QUALIFIER_BARRIER_OPEN")
            self.event = int(self.kernel.CreateEventW(None, True, False, None) or 0)
            if not self.event:
                fail("QUALIFIER_BARRIER_EVENT")
            self.overlapped.event = self.event
            result = self.kernel.DeviceIoControl(
                self.file,
                0x00090240,
                ctypes.byref(self.request),
                ctypes.sizeof(self.request),
                ctypes.byref(self.output),
                ctypes.sizeof(self.output),
                None,
                ctypes.byref(self.overlapped),
            )
            if result or ctypes.get_last_error() != 997:  # ERROR_IO_PENDING == granted
                fail("QUALIFIER_BARRIER_NOT_GRANTED")
        except BaseException:
            self.close()
            raise

    def replacement_waiting(self) -> bool:
        result = self.kernel.WaitForSingleObject(self.event, 0)
        if result == 258:
            return False
        received = ctypes.c_ulong()
        if result != 0 or not self.kernel.GetOverlappedResult(
            self.file, ctypes.byref(self.overlapped), ctypes.byref(received), False
        ):
            fail("QUALIFIER_BARRIER_OBSERVATION")
        if (
            self.overlapped.internal != 0
            or self.output.original_level != 3
            or not self.output.flags & 1
        ):  # ACK_REQUIRED, not an advisory break
            fail("QUALIFIER_BARRIER_NOT_BLOCKING")
        return True

    def close(self) -> None:
        # Closing the file cancels a still-pending request. OVERLAPPED and its
        # event remain alive until completion, including exceptional cleanup.
        if self.file:
            self.kernel.CloseHandle(self.file)
            self.file = 0
        if self.event:
            self.kernel.WaitForSingleObject(self.event, 1000)
            self.kernel.CloseHandle(self.event)
            self.event = 0

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _held_process_identity(handle: int, executable: Path) -> dict[str, int | bool]:
    kernel = _interruption_kernel()
    size = ctypes.c_ulong(32768)
    name = ctypes.create_unicode_buffer(size.value)
    times = [ctypes.c_ulonglong() for _ in range(4)]
    if (
        not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))
        or not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
        or os.path.normcase(name.value) != os.path.normcase(str(executable.absolute()))
    ):
        fail("QUALIFIER_INTERRUPTION_PROCESS_IDENTITY")
    return {"exact_image_verified": True, "creation_filetime": times[0].value}


class _InterruptionProcess(AbstractContextManager["_InterruptionProcess"]):
    """Direct suspended launch; retained process/job handles outlive observation."""

    def __init__(self, argv: Sequence[str], directory: Path) -> None:
        from paperworkshell.storage._windows import current_profile_root
        from tools.build_windows_launcher import _windows_directory
        from tools.dev import _assign_windows_kill_job, _resume_windows_process

        self.kernel = _interruption_kernel()
        self.job = 0
        self.terminated = False
        self.process: subprocess.Popen[bytes] | None = None
        windows = _windows_directory()
        profile = current_profile_root()
        environment = {
            "SystemRoot": str(windows),
            "WINDIR": str(windows),
            "USERPROFILE": str(profile),
            "APPDATA": str(profile / "AppData/Roaming"),
            "LOCALAPPDATA": str(profile / "AppData/Local"),
            "PATH": str(windows / "System32"),
            "TEMP": str(directory),
            "TMP": str(directory),
        }
        mode = self.kernel.SetErrorMode(0x8003)
        try:
            self.process = subprocess.Popen(
                list(argv),
                cwd=directory,
                env=environment,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000004,  # CREATE_NO_WINDOW | CREATE_SUSPENDED
            )
        finally:
            self.kernel.SetErrorMode(mode)
        try:
            self.handle = int(self.process._handle)  # type: ignore[attr-defined]
            self.pid = self.process.pid
            self.identity = _held_process_identity(self.handle, Path(argv[0]))
            self.job = _assign_windows_kill_job(self.process)
            _resume_windows_process(self.process)
        except BaseException:
            self.close()
            raise

    def alive(self) -> bool:
        result = int(self.kernel.WaitForSingleObject(self.handle, 0))
        if result not in {0, 258}:
            fail("QUALIFIER_INTERRUPTION_PROCESS_WAIT")
        return result == 258

    def stop(self) -> None:
        from tools.windows_signing import _job_active_process_count

        if self.job and not self.terminated:
            if not self.kernel.TerminateJobObject(self.job, 73):
                fail("QUALIFIER_INTERRUPTION_TERMINATE")
            self.terminated = True  # Termination is asynchronous; never issue it twice.
        deadline = time.monotonic() + 10
        while self.job and _job_active_process_count(self.job):
            if time.monotonic() >= deadline:
                fail("QUALIFIER_INTERRUPTION_CLEANUP_TIMEOUT")
            time.sleep(0.02)
        if self.process is not None:
            self.process.wait(timeout=max(0.1, deadline - time.monotonic()))

    def close(self) -> None:
        try:
            if self.job:
                self.stop()
            elif self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
        finally:
            if self.job:
                self.kernel.CloseHandle(self.job)  # Last-handle kill remains the fallback.
                self.job = 0

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _digest(path: Path, expected: str) -> None:
    plain_path(path)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or file_hashes(path)[1] != expected:
        fail("QUALIFIER_INPUT_HASH")


def _microsoft_authority(data: Mapping[str, Any]) -> dict[str, Any]:
    mode = data.get("microsoft_runtime_delivery")
    overlay = data.get("microsoft_overlay_receipt_sha256")
    terms = data.get("microsoft_recipient_terms_sha256")
    if mode is None and overlay is None and terms is None:
        return {}  # Historical prerequisite-only receipts remain readable.
    if mode == "external-prerequisite-pending-entitlement" and overlay is None and terms is None:
        return {
            "microsoft_runtime_delivery": mode,
            "microsoft_overlay_receipt_sha256": None,
            "microsoft_recipient_terms_sha256": None,
        }
    if mode != "app-local-community2022" or any(
        type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in (overlay, terms)
    ):
        fail("QUALIFIER_MICROSOFT_AUTHORITY")
    return {
        "microsoft_runtime_delivery": mode,
        "microsoft_overlay_receipt_sha256": overlay,
        "microsoft_recipient_terms_sha256": terms,
    }


def quiet_arguments(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """Accept only the exact component terms in an independently anchored candidate.

    The maintainer has authorized this qualification of the reviewed agreement.
    This never adds implicit consent to ordinary product installation or removal.
    """
    authority = _microsoft_authority(candidate)
    if authority.get("microsoft_runtime_delivery") != "app-local-community2022":
        return QUIET_ARGUMENTS
    receipt = candidate.get("build_receipt_sha256")
    records = candidate.get("files")
    if (
        type(receipt) is not str
        or re.fullmatch(r"[0-9a-f]{64}", receipt) is None
        or not isinstance(records, list)
    ):
        fail("QUALIFIER_MICROSOFT_AUTHORITY")
    for path, key in (
        ("notices/Microsoft-runtime-recipient-terms.txt", "microsoft_recipient_terms_sha256"),
        ("evidence/microsoft-runtime/overlay-receipt.json", "microsoft_overlay_receipt_sha256"),
    ):
        matches = [
            record for record in records if isinstance(record, dict) and record.get("path") == path
        ]
        if len(matches) != 1 or matches[0].get("sha256") != authority[key]:
            fail("QUALIFIER_MICROSOFT_AUTHORITY")
    return (
        *QUIET_ARGUMENTS,
        "/ACCEPTMICROSOFTRUNTIME=" + authority["microsoft_recipient_terms_sha256"],
    )


def owned_work(path: Path, *, root: Path = PROJECT_ROOT, new: bool = False) -> Path:
    plain_path(path)
    result = path.absolute()
    boundary = root.absolute() / "output/pws0802/qualification"
    if result == boundary or not result.is_relative_to(boundary) or ".." in result.parts:
        fail("QUALIFIER_WORK_SCOPE")
    if new and result.exists():
        fail("QUALIFIER_WORK_EXISTS")
    return result


def inspect_candidate(
    installer: Path,
    build_receipt: Path,
    *,
    expected_installer_sha256: str,
    expected_receipt_sha256: str,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Reconcile exact installer, receipt, staged payload and generated inputs."""
    root = root.absolute()
    installer, build_receipt = installer.absolute(), build_receipt.absolute()
    for path in (installer, build_receipt):
        if not path.is_relative_to(root / "output/pws0802"):
            fail("QUALIFIER_CANDIDATE_SCOPE")
    _digest(installer, expected_installer_sha256)
    _digest(build_receipt, expected_receipt_sha256)
    receipt = load_json(build_receipt, 128 * 1024)
    candidate = build_receipt.parent
    if (
        receipt.get("schema_version") != 1
        or receipt.get("status") != "unsigned-internal-built"
        or receipt.get("public_distribution_allowed") is not False
        or receipt.get("public_trusted_signature") is not False
        or receipt.get("installer_sha256") != expected_installer_sha256
        or receipt.get("installer_size_bytes") != installer.stat().st_size
        or installer != candidate / "installer" / safe_relative(receipt.get("installer", ""))
    ):
        fail("QUALIFIER_CANDIDATE_IDENTITY")
    authority = _microsoft_authority(receipt)
    _digest(candidate / "resolved-inventory.json", receipt["inventory_sha256"])
    inventory = load_json(candidate / "resolved-inventory.json")
    verify_files(candidate / "payload", inventory["files"])
    for item in receipt["generated_inputs"]:
        name = safe_relative(item["path"])
        if name not in {"config.iss.inc", "files.iss.inc", "checks.iss.inc"}:
            fail("QUALIFIER_GENERATED_INPUT")
        _digest(candidate / name, item["sha256"])
    if inventory["candidate"]["id"] != receipt["candidate_id"]:
        fail("QUALIFIER_CANDIDATE_IDENTITY")
    marker = next(
        (
            record
            for record in inventory["files"]
            if record["path"] == "runtime/paperworkshell-installed-v1.json"
        ),
        None,
    )
    if marker is None or len(inventory["files"]) > MAX_RECORDS:
        fail("QUALIFIER_PAYLOAD_LIMIT")
    result = {
        "installer": installer.relative_to(root).as_posix(),
        "build_receipt": build_receipt.relative_to(root).as_posix(),
        "installer_sha256": expected_installer_sha256,
        "build_receipt_sha256": expected_receipt_sha256,
        "inventory_sha256": receipt["inventory_sha256"],
        "candidate_id": receipt["candidate_id"],
        "files": inventory["files"],
        "marker_sha256": marker["sha256"],
        **authority,
    }
    quiet_arguments(result)  # Complete-runtime hashes must also bind actual payload records.
    return result


def _host_paths() -> tuple[Path, Path]:
    from paperworkshell.storage._windows import current_profile_root

    library = ctypes.WinDLL("shell32", use_last_error=True)

    class Guid(ctypes.Structure):
        _fields_ = [
            ("a", ctypes.c_ulong),
            ("b", ctypes.c_ushort),
            ("c", ctypes.c_ushort),
            ("d", ctypes.c_ubyte * 8),
        ]

    folder = Guid(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    value = ctypes.c_wchar_p()
    function = library.SHGetKnownFolderPath
    function.argtypes = (
        ctypes.POINTER(Guid),
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    function.restype = ctypes.c_long
    if function(ctypes.byref(folder), 0, None, ctypes.byref(value)) != 0 or not value.value:
        fail("QUALIFIER_KNOWN_FOLDER")
    try:
        local = Path(value.value)
    finally:
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = (ctypes.c_void_p,)
        free(value)
    return (
        local / "Programs/PaperWorkShell",
        current_profile_root() / "AppData/Local/PaperWorkShell/Cases",
    )


def case_entry_count(root: Path) -> int:
    """Count immediate entries only: no names, manifests or document bytes retained."""
    plain_path(root)
    if not root.exists():
        return 0
    count = 0
    with os.scandir(root) as entries:
        for _entry in entries:
            count += 1
            if count > 4096:
                fail("QUALIFIER_CASE_COUNT_LIMIT")
    return count


def _registrations() -> list[tuple[int, int]]:
    import winreg

    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view):
                    found.append((hive, view))
            except FileNotFoundError:
                pass
    return found


def _token_elevated() -> bool:
    library = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    library.OpenProcessToken.argtypes = (
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
    )
    library.GetTokenInformation.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    )
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    token = ctypes.c_void_p()
    if not library.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        fail("QUALIFIER_TOKEN_UNKNOWN")
    try:
        elevated, length = ctypes.c_ulong(), ctypes.c_ulong()
        if not library.GetTokenInformation(
            token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(length)
        ):
            fail("QUALIFIER_TOKEN_UNKNOWN")
        return bool(elevated.value)
    finally:
        kernel.CloseHandle(token)


def _save(work: Path, state: Mapping[str, Any]) -> str:
    stage = work / ("state-" + uuid4().hex + ".tmp")
    with stage.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, work / SESSION)
    return file_hashes(work / SESSION)[1]


def prepare_session(
    candidate: dict[str, Any], work: Path, *, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    work = owned_work(work, root=root, new=True)
    installation, cases = _host_paths()
    if installation.exists() or _registrations():
        fail("QUALIFIER_PREEXISTING_PRODUCT")
    if _token_elevated():
        fail("QUALIFIER_ELEVATED_TOKEN")
    initial = case_entry_count(cases)
    work.mkdir(parents=True, exist_ok=False)
    version = sys.getwindowsversion()
    state: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "PWS-0802",
        "scope": "HOST_NOT_CLEAN",
        "actual_standard_account_qualification": "NOT_PROVED",
        "clean_windows_qualification": "NOT_RUN",
        "normal_public_first_launch": "NOT_RUN",
        "production_case_content_integrity": "NOT_INSPECTED",
        "host_token_elevated": False,
        "os_version": list(version.platform_version),
        "candidate": candidate,
        "phase": "inspected",
        "initial_case_entry_count": initial,
        "observations": [],
        "owned_installation": False,
    }
    digest = _save(work, state)
    return {**state, "session_sha256": digest}


_RUNNER = r"""
import ctypes, os, subprocess, sys
from ctypes import wintypes as w
class G(ctypes.Structure):
    _fields_=[('a',w.DWORD),('b',w.WORD),('c',w.WORD),('d',w.BYTE*8)]
g=G(0x5e6c858f,0x0e22,0x4760,(w.BYTE*8)(0x9a,0xfe,0xea,0x33,0x17,0xb6,0x71,0x73))
value=ctypes.c_wchar_p(); shell=ctypes.WinDLL('shell32'); shell.SHGetKnownFolderPath.argtypes=[ctypes.POINTER(G),w.DWORD,w.HANDLE,ctypes.POINTER(ctypes.c_wchar_p)]
if shell.SHGetKnownFolderPath(ctypes.byref(g),0,None,ctypes.byref(value)) != 0: raise SystemExit(2)
profile=value.value; free=ctypes.WinDLL('ole32').CoTaskMemFree;free.argtypes=[ctypes.c_void_p];free(value)
kernel=ctypes.WinDLL('kernel32');kernel.SetErrorMode(0x8003)
buffer=ctypes.create_unicode_buffer(32768);kernel.GetWindowsDirectoryW(buffer,len(buffer));windows=buffer.value
env={'SystemRoot':windows,'WINDIR':windows,'USERPROFILE':profile,'PATH':windows+'\\System32','TEMP':os.getcwd(),'TMP':os.getcwd()}
code=subprocess.call(sys.argv[1:],shell=False,stdin=subprocess.DEVNULL,env=env)
print('PWS_QUALIFIER_EXIT_CODE:'+str(code))
raise SystemExit(0 if code==0 else 1)
"""


def _execute(argv: Sequence[str], step: Path, *, root: Path = PROJECT_ROOT) -> None:
    step.mkdir(parents=True, exist_ok=False)
    builder._run(
        [sys.executable, "-I", "-B", "-c", _RUNNER, *argv],
        step,
        "process",
        seconds=600,
        root=root,
        successful_descendant_grace_seconds=30
        if Path(argv[0]).name.casefold() == "unins000.exe"
        else 0,
    )


def _installed_files(
    installation: Path, expected: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    plain_path(installation)
    expected_by_name = {record["path"]: record for record in expected}
    found: dict[str, dict[str, Any]] = {}
    for current, directories, files in os.walk(installation, followlinks=False):
        for name in directories:
            plain_path(Path(current) / name)
        for name in files:
            path = Path(current) / name
            plain_path(path)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or len(found) >= MAX_RECORDS:
                fail("QUALIFIER_INSTALLED_OBJECT")
            relative = path.relative_to(installation).as_posix()
            if relative not in expected_by_name and relative not in {
                "unins000.exe",
                "unins000.dat",
            }:
                fail("QUALIFIER_INSTALLED_EXTRA")
            size, digest, _ = file_hashes(path)
            if relative in expected_by_name and (size, digest) != (
                expected_by_name[relative]["size_bytes"],
                expected_by_name[relative]["sha256"],
            ):
                fail("QUALIFIER_INSTALLED_CHANGED")
            found[relative] = {"size_bytes": size, "sha256": digest}
    if set(found) != set(expected_by_name) | {"unins000.exe", "unins000.dat"}:
        fail("QUALIFIER_INSTALLED_MISSING")
    return found


def _check_owned(state: Mapping[str, Any], installation: Path) -> None:
    if state.get("owned_installation") is not True:
        fail("QUALIFIER_INSTALL_NOT_OWNED")
    actual = _installed_files(installation, state["candidate"]["files"])
    if actual != state["installed_files"] or not _registrations():
        fail("QUALIFIER_INSTALL_NOT_OWNED")
    _digest(
        installation / "runtime/paperworkshell-installed-v1.json",
        state["candidate"]["marker_sha256"],
    )
    _check_registration(installation)


def _check_registration(installation: Path) -> None:
    import winreg

    registrations = _registrations()
    if not registrations or any(hive != winreg.HKEY_CURRENT_USER for hive, _view in registrations):
        fail("QUALIFIER_REGISTRATION_CHANGED")
    for hive, view in registrations:
        with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
            location = winreg.QueryValueEx(key, "InstallLocation")[0]
            command = winreg.QueryValueEx(key, "UninstallString")[0]
        if (
            type(location) is not str
            or type(command) is not str
            or os.path.normcase(location.rstrip("\\/")) != os.path.normcase(str(installation))
            or command
            not in {
                str(installation / "unins000.exe"),
                '"' + str(installation / "unins000.exe") + '"',
            }
        ):
            fail("QUALIFIER_REGISTRATION_CHANGED")


def _pin_parents(stack: ExitStack, path: Path) -> None:
    from paperworkshell.storage._windows import open_handle, require_expected_handle

    for parent in reversed(path.absolute().parents):
        handle = stack.enter_context(open_handle(parent, traverse=True))
        require_expected_handle(handle, parent, directory=True)


def _pin_file(stack: ExitStack, path: Path, *, delete: bool = False) -> Any:
    from paperworkshell.storage._windows import (
        open_handle,
        require_expected_handle,
        require_no_alternate_streams,
    )

    _pin_parents(stack, path)
    handle = stack.enter_context(open_handle(path, read=True, delete=delete))
    require_expected_handle(handle, path.absolute(), directory=False)
    info = handle.info
    if info.link_count != 1 or info.is_sparse:
        fail("QUALIFIER_INTERRUPTION_FILE_ALIAS")
    require_no_alternate_streams(path)
    return handle


def _bounded_file_copy(handle: Any, record: Mapping[str, Any], target: Path) -> None:
    """Retain exact bytes before any deliberate, handle-bound notice/temp removal."""
    if not 0 < record["size_bytes"] <= 16 * 1024 * 1024:
        fail("QUALIFIER_INTERRUPTION_COPY_LIMIT")
    data = handle.read_bytes(maximum_bytes=record["size_bytes"])
    if (len(data), hashlib.sha256(data).hexdigest()) != (record["size_bytes"], record["sha256"]):
        fail("QUALIFIER_INTERRUPTION_FILE_CHANGED")
    with target.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _write_interruption_report(step: Path, report: Mapping[str, Any]) -> None:
    stage = step / (uuid4().hex + ".tmp")
    with stage.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(stage, step / "interruption-result.json")


def _interrupt_owned_repair(
    installation: Path, state: Mapping[str, Any], step: Path, *, root: Path
) -> dict[str, Any]:
    """One exact same-candidate repair, stopped at a blocking replacement event.

    No log switch, shipping instrumentation, normal desktop launch or case-file
    read occurs. A missed event is NOT_OBSERVED, even if earlier copying occurred.
    Explicit recovery is a separate action; interrupted-copy temps are not an
    automatic rollback claim.
    """
    step.mkdir(parents=True, exist_ok=False)
    candidate = state["candidate"]
    expected = {item["path"]: item for item in candidate["files"]}
    if not {_INTERRUPTION_NOTICE, _INTERRUPTION_BARRIER} <= expected.keys():
        fail("QUALIFIER_INTERRUPTION_NOTICE_MISSING")
    # The independently bound generated file order must put the restored notice
    # before the barrier. No arbitrary caller-specified file is accepted.
    files = (root / candidate["build_receipt"]).parent / "files.iss.inc"
    listing = files.read_text(encoding="utf-8-sig")
    notice_entry = "\\" + _INTERRUPTION_NOTICE.replace("/", "\\") + '";'
    barrier_entry = "\\" + _INTERRUPTION_BARRIER.replace("/", "\\") + '";'
    if listing.count(notice_entry) != 1 or listing.count(barrier_entry) != 1:
        fail("QUALIFIER_INTERRUPTION_FILE_ORDER")
    if listing.index(notice_entry) >= listing.index(barrier_entry):
        fail("QUALIFIER_INTERRUPTION_FILE_ORDER")
    report: dict[str, Any] = {
        "scope": "HOST_NOT_CLEAN",
        "status": "NOT_OBSERVED",
        "cutpoint": "blocking-owned-notice-replacement",
        "cutpoint_observed": False,
        "earlier_copy_verified": False,
        "acknowledgement_required": False,
        "exact_installer_live": False,
        "job_terminated": False,
        "job_empty": False,
        "notice_removed": False,
        "case_contents": "NOT_INSPECTED",
        "normal_public_first_launch": "NOT_RUN",
        "installer_sha256": candidate["installer_sha256"],
        "notice": _INTERRUPTION_NOTICE,
        "barrier": _INTERRUPTION_BARRIER,
        "automatic_rollback": "NOT_CLAIMED",
    }
    _write_interruption_report(step, report)
    try:
        with ExitStack() as pins:
            installer = root / candidate["installer"]
            _pin_file(pins, installer)
            _digest(installer, candidate["installer_sha256"])
            barrier_path = installation / _INTERRUPTION_BARRIER
            original = _pin_file(pins, barrier_path)
            _digest(barrier_path, expected[_INTERRUPTION_BARRIER]["sha256"])
            identity = original.info.identity
            with _ReplacementBarrier(original.value) as barrier:
                # Establish the working barrier before deliberate notice damage.
                with ExitStack() as notice_pins:
                    notice = _pin_file(
                        notice_pins, installation / _INTERRUPTION_NOTICE, delete=True
                    )
                    _bounded_file_copy(
                        notice, expected[_INTERRUPTION_NOTICE], step / "notice-before.bin"
                    )
                    notice.mark_for_deletion()
                report["notice_removed"] = True
                _write_interruption_report(step, report)
                with _InterruptionProcess(
                    [str(installer), *quiet_arguments(candidate)], step
                ) as process:
                    report["process"] = {**process.identity, "pid": process.pid}
                    deadline = time.monotonic() + 180
                    try:
                        while time.monotonic() < deadline and process.alive():
                            if barrier.replacement_waiting():
                                report["acknowledgement_required"] = True
                                # The blocking event, restored earlier bytes and
                                # live held image are checked in the same turn.
                                _digest(
                                    installation / _INTERRUPTION_NOTICE,
                                    expected[_INTERRUPTION_NOTICE]["sha256"],
                                )
                                if original.refresh_info().identity != identity:
                                    fail("QUALIFIER_INTERRUPTION_BARRIER_CHANGED")
                                _digest(barrier_path, expected[_INTERRUPTION_BARRIER]["sha256"])
                                _held_process_identity(process.handle, installer)
                                report["earlier_copy_verified"] = True
                                report["exact_installer_live"] = process.alive()
                                if not report["exact_installer_live"]:
                                    break
                                process.stop()
                                report.update(
                                    cutpoint_observed=True,
                                    status="pass",
                                    job_terminated=process.terminated,
                                    job_empty=True,
                                )
                                break
                            time.sleep(0.01)
                    finally:
                        process.stop()
                        report.update(job_terminated=process.terminated, job_empty=True)
                        _write_interruption_report(step, report)
    except Exception as error:
        report["error_code"] = (
            str(error) if type(error) is QualificationError else "QUALIFIER_INTERRUPTION_FAILED"
        )
        _write_interruption_report(step, report)
        raise
    _write_interruption_report(step, report)
    return report


def _recover_owned_repair(
    installation: Path, state: Mapping[str, Any], step: Path, *, root: Path
) -> dict[str, dict[str, Any]]:
    """Explicit exact-temp recovery, then ordinary same-candidate repair.

    Only two known Inno staging identities are permitted: a complete original
    uninstaller copy in the product root, and a complete barrier copy beside that
    barrier. Any other extra, alias, partial bytes or missing payload stops here.
    """
    step.mkdir(parents=True, exist_ok=False)
    candidate = state["candidate"]
    expected = {item["path"]: item for item in candidate["files"]}
    known = set(expected) | {"unins000.exe", "unins000.dat"}
    extras: list[Path] = []
    directory_count = 0
    file_count = 0
    allowed_directories = {
        parent.as_posix() for name in known for parent in Path(name).parents if parent != Path(".")
    }
    for directory, directories, filenames in os.walk(installation, followlinks=False):
        directory_count += 1
        if directory_count > MAX_RECORDS:
            fail("QUALIFIER_INTERRUPTION_INVENTORY_LIMIT")
        for name in directories:
            child = Path(directory) / name
            plain_path(child)
            if child.relative_to(installation).as_posix() not in allowed_directories:
                fail("QUALIFIER_INTERRUPTION_UNKNOWN_RESIDUE")
        for name in filenames:
            file_count += 1
            if file_count > MAX_RECORDS + 4:
                fail("QUALIFIER_INTERRUPTION_INVENTORY_LIMIT")
            path = Path(directory) / name
            plain_path(path)
            if path.relative_to(installation).as_posix() not in known:
                extras.append(path)
                if len(extras) > 2:
                    fail("QUALIFIER_INTERRUPTION_UNKNOWN_RESIDUE")
    allowed = {
        installation: state["installed_files"]["unins000.exe"],
        (installation / _INTERRUPTION_BARRIER).parent: expected[_INTERRUPTION_BARRIER],
    }
    retained: list[dict[str, Any]] = []
    seen_parents: set[Path] = set()
    for path in extras:
        if (
            path.parent not in allowed
            or path.parent in seen_parents
            # Inno 7.1.1 GenerateUniqueName uses two five-digit base36 groups.
            or not re.fullmatch(r"is-[A-Z0-9]{10}\.tmp", path.name)
        ):
            fail("QUALIFIER_INTERRUPTION_UNKNOWN_RESIDUE")
        seen_parents.add(path.parent)
        record = allowed[path.parent]
        retained.append(
            {
                "path": path.relative_to(installation).as_posix(),
                "size_bytes": record["size_bytes"],
                "sha256": record["sha256"],
            }
        )
    records = [
        item
        for item in candidate["files"]
        if item["path"] != _INTERRUPTION_NOTICE or (installation / _INTERRUPTION_NOTICE).exists()
    ]
    # Path readers do not share deletion. Complete this read-only inventory
    # before acquiring delete handles; every exact stage is authenticated again
    # through its pinned handle below, before any stage is marked for deletion.
    _installed_files(installation, [*records, *retained])
    _digest(installation / "unins000.exe", state["installed_files"]["unins000.exe"]["sha256"])
    _check_registration(installation)
    # Authenticate the whole allowed set before deleting any member.
    with ExitStack() as pins:
        handles: list[Any] = []
        for path in extras:
            handle = _pin_file(pins, path, delete=True)
            record = allowed[path.parent]
            copy = step / f"retained-stage-{len(handles) + 1}.bin"
            _bounded_file_copy(handle, record, copy)
            handles.append(handle)
        _digest(installation / "unins000.exe", state["installed_files"]["unins000.exe"]["sha256"])
        _check_registration(installation)
        for handle in handles:
            handle.mark_for_deletion()
    _write_interruption_report(
        step,
        {
            "scope": "HOST_NOT_CLEAN",
            "operator_temp_recovery": retained,
            "automatic_rollback": "NOT_CLAIMED",
        },
    )
    # Preserve the prior uninstaller authority; a successful repair may append
    # only its owned .dat. All exact payload bytes and registration are rechecked.
    _digest(installation / "unins000.exe", state["installed_files"]["unins000.exe"]["sha256"])
    _check_registration(installation)
    _execute(
        [str(root / candidate["installer"]), *quiet_arguments(candidate)],
        step / "ordinary-repair",
        root=root,
    )
    files = _installed_files(installation, candidate["files"])
    if files["unins000.exe"] != state["installed_files"]["unins000.exe"]:
        fail("QUALIFIER_INTERRUPTION_UNINSTALLER_CHANGED")
    _check_registration(installation)
    return files


def _run_action_checked(action: str, work: Path, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    work = owned_work(work, root=root)
    state = load_json(work / SESSION)
    candidate = state["candidate"]
    current = inspect_candidate(
        root / candidate["installer"],
        root / candidate["build_receipt"],
        expected_installer_sha256=candidate["installer_sha256"],
        expected_receipt_sha256=candidate["build_receipt_sha256"],
        root=root,
    )
    if (
        candidate != current
        or state.get("scope") != "HOST_NOT_CLEAN"
        or state.get("schema_version") != 1
    ):
        fail("QUALIFIER_SESSION_CHANGED")
    if action == "status":
        return state
    if _token_elevated():
        fail("QUALIFIER_ELEVATED_TOKEN")
    installation, cases = _host_paths()
    before = case_entry_count(cases)
    if before != state["initial_case_entry_count"]:
        fail("QUALIFIER_CASE_COUNT_CHANGED")
    index = len(state["observations"]) + 1
    step = work / f"step-{index:03d}-{action}"
    if action in {"install", "reinstall"}:
        required = "inspected" if action == "install" else "uninstalled"
        if state["phase"] != required or installation.exists() or _registrations():
            fail("QUALIFIER_PREEXISTING_PRODUCT")
        _execute([str(root / candidate["installer"]), *quiet_arguments(candidate)], step, root=root)
        files = _installed_files(installation, candidate["files"])
        if not _registrations():
            fail("QUALIFIER_REGISTRATION_MISSING")
        _check_registration(installation)
        state.update(owned_installation=True, installed_files=files, phase="installed")
    elif action == "interrupt-repair":
        if state["phase"] not in {"installed", "smoked"}:
            fail("QUALIFIER_INTERRUPTION_PHASE")
        _check_owned(state, installation)
        state.update(phase="interrupted", interruption_step=step.relative_to(work).as_posix())
        _save(work, state)  # Durable authority survives a failed observer or interrupted host.
        state["interruption"] = _interrupt_owned_repair(installation, state, step, root=root)
    elif action == "recover-repair":
        if state["phase"] != "interrupted" or state.get("owned_installation") is not True:
            fail("QUALIFIER_INTERRUPTION_PHASE")
        state["installed_files"] = _recover_owned_repair(installation, state, step, root=root)
        state["phase"] = "installed"
    elif action == "preserve-uninstall":
        _check_owned(state, installation)
        _execute([str(installation / "unins000.exe"), *QUIET_ARGUMENTS], step, root=root)
        if installation.exists() or _registrations():
            fail("QUALIFIER_UNINSTALL_RESIDUE")
        state.update(owned_installation=False, phase="uninstalled")
    elif action in {"smoke", "gui"}:
        _check_owned(state, installation)
        if action == "gui":
            return prepare_gui(work, installation, state, root=root)
        prepare_smoke(step, root=root)
        # _execute creates its own fresh log directory; synthetic fixtures stay separate.
        _execute(
            [str(installation / "runtime/python.exe"), "-I", "-B", str(step / "smoke.py")],
            step / "run",
            root=root,
        )
        report = load_json(step / "smoke-result.json", 64 * 1024)
        if (
            report.get("status") != "pass"
            or report.get("scope") != "INSTALLED_HOST_INTERNAL_COMPOSITION"
        ):
            fail("QUALIFIER_SMOKE_FAILED")
        state["last_smoke"] = report
        state["phase"] = "smoked"
    else:
        fail("QUALIFIER_ACTION_INVALID")
    after = case_entry_count(cases)
    if after != before:
        state["case_count_after_failed_action"] = after
        _save(work, state)
        fail("QUALIFIER_CASE_COUNT_CHANGED")
    state.pop("pending_action", None)
    observation = {
        "action": action,
        "status": "NOT_OBSERVED"
        if action == "interrupt-repair" and not state["interruption"]["cutpoint_observed"]
        else "pass",
        "case_entry_count_before": before,
        "case_entry_count_after": after,
        "case_contents": "NOT_INSPECTED",
    }
    state["observations"].append(observation)
    digest = _save(work, state)
    return {**state, "session_sha256": digest}


def run_action(
    action: str,
    work: Path,
    *,
    expected_session_sha256: str | None = None,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Mutations require the independently retained previous controlled receipt."""
    work = owned_work(work, root=root)
    if action != "status" or expected_session_sha256 is not None:
        if expected_session_sha256 is None:
            fail("QUALIFIER_SESSION_ANCHOR_REQUIRED")
        _digest(work / SESSION, expected_session_sha256)
    initial = load_json(work / SESSION)
    if initial.get("phase") not in {
        "inspected",
        "installed",
        "smoked",
        "uninstalled",
        "interrupted",
    }:
        fail("QUALIFIER_SESSION_INVALID")
    if action == "status":
        return {
            **initial,
            "session_sha256": file_hashes(work / SESSION)[1],
            "session_anchor_verified": expected_session_sha256 is not None,
        }
    lock = work / "operation.lock"
    try:
        with lock.open("xb") as stream:
            stream.write(str(os.getpid()).encode("ascii"))
    except FileExistsError:
        fail("QUALIFIER_OPERATION_BUSY")
    try:
        if initial.get("pending_action") is not None and not (
            action == "recover-repair"
            and initial.get("pending_action") == "interrupt-repair"
            and initial["phase"] == "interrupted"
        ):
            fail("QUALIFIER_PREVIOUS_ACTION_INCOMPLETE")
        initial["pending_action"] = action
        _save(work, initial)
        try:
            return _run_action_checked(action, work, root=root)
        except Exception as error:
            retained = load_json(work / SESSION)
            retained.setdefault("observations", []).append(
                {
                    "action": action,
                    "status": "failed",
                    "error_code": str(error)
                    if type(error) is QualificationError
                    else "QUALIFIER_ACTION_FAILED",
                    "case_contents": "NOT_INSPECTED",
                }
            )
            _save(work, retained)
            raise
    finally:
        lock.unlink()


_SMOKE = r"""
import hashlib,json,sys,time
from pathlib import Path
from paperworkshell import __main__ as desktop
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1 as policy
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.uninstall_review import uninstall_application_guard
base=Path(__file__).absolute().parent
result={'scope':'INSTALLED_HOST_INTERNAL_COMPOSITION','status':'failed','normal_public_first_launch':'NOT_RUN','clean_environment':'NOT_RUN','standard_account':'NOT_PROVED'}
backend=None
try:
    original_hashes={name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in ('source.pdf','ocr.png')}
    installed=desktop._installed_runtime()
    if installed is None: raise RuntimeError('QUALIFIER_INSTALLED_ORIGIN')
    module=Path(desktop.__file__).absolute()
    if module.parent != installed.package_root or Path(sys.executable) != installed.python_executable: raise RuntimeError('QUALIFIER_INSTALLED_ORIGIN')
    result['installed_module_origin_verified']=True
    profile=base/'profile';profile.mkdir()
    manager=CaseWorkspaceManager(profile_root=profile,quota=desktop._workspace_quota(policy),minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes)
    resources=desktop.create_desktop_application_resource_context(policy)
    with uninstall_application_guard():
        backend=desktop._build_backend(manager,resources=resources)
        backend.refresh_ocr_languages()
        state=backend.create_case();case_id=state.case_id
        first=backend.import_source(base/'source.pdf','Synthetic source.pdf')
        state=backend.state;first_id=state.pages[0].page_id
        backend.rotate_clockwise(first_id);backend.flush_autosave()
        backend.import_source(base/'ocr.png','Synthetic OCR.png')
        state=backend.state;ocr_page=state.pages[-1].page_id
        previews=backend.get_thumbnails(256,256,page_ids=tuple(p.page_id for p in state.pages))
        exported=base/'exports';exported.mkdir()
        outcome=backend.export_pages_pdf(exported/'packet.pdf',cancellation=lambda:False,progress=lambda _p:None)
        if not outcome.committed_export.destination_reopened: raise RuntimeError('QUALIFIER_EXPORT_REOPEN')
        result['export_checks']=[check.code.value for check in outcome.committed_export.checks]
        sources=WindowsSourceIngestStore(manager,WindowsSourcePrechecker(desktop._import_limits(policy)),maximum_page_count=policy.maximum_page_count)
        with desktop.build_windows_ocr_worker_composition(resources) as composition:
            service=desktop.build_windows_ocr_service(sources,composition,resources=resources)
            deadline=time.monotonic()+180
            with service.process_page(case_id,ocr_page,deadline=lambda:time.monotonic()>=deadline,cancellation=lambda:False) as candidate:
                digest=hashlib.sha256();count=0
                while block:=candidate.read(1048576):
                    count+=len(block);digest.update(block)
                    if count>policy.maximum_temporary_storage_bytes:raise RuntimeError('QUALIFIER_OCR_LIMIT')
                if count!=candidate.report.output_byte_size or digest.hexdigest()!=candidate.report.output_sha256:raise RuntimeError('QUALIFIER_OCR_DIGEST')
                result['ocr']={'processed_pages':candidate.report.processed_page_count,'output_bytes':count,'ocrmypdf_version':candidate.report.ocrmypdf_version,'tesseract_version':candidate.report.tesseract_version}
        (base/'invalid.bin').write_bytes(b'PWS synthetic unsupported source')
        failed=False
        try:backend.import_source(base/'invalid.bin','Synthetic invalid.bin')
        except Exception as error:
            failed=True;result['failure_code']=getattr(getattr(error,'code',None),'value','GENERIC_FAILURE')
        if not failed:raise RuntimeError('QUALIFIER_FAILURE_NOT_REJECTED')
        packet_digest=hashlib.sha256((exported/'packet.pdf').read_bytes()).hexdigest()
        refused=False
        try:backend.export_pages_pdf(exported/'packet.pdf',cancellation=lambda:False,progress=lambda _p:None)
        except Exception as error:
            refused=True;result['existing_export_refusal_code']=getattr(getattr(error,'code',None),'value','GENERIC_FAILURE')
        if not refused or hashlib.sha256((exported/'packet.pdf').read_bytes()).hexdigest()!=packet_digest:raise RuntimeError('QUALIFIER_EXPORT_OVERWRITE')
        backend.close_case();backend.open_case(case_id)
        result['reopened_page_count']=len(backend.state.pages)
        backend.shutdown();backend=None
        layout=manager.layout(case_id)
        result['temporary_files_remaining']=len(list(layout.temp.iterdir()))
        if result['temporary_files_remaining']:raise RuntimeError('QUALIFIER_TEMP_RESIDUE')
        if any(hashlib.sha256((base/name).read_bytes()).hexdigest()!=digest for name,digest in original_hashes.items()):raise RuntimeError('QUALIFIER_SOURCE_CHANGED')
        result['synthetic_external_originals_unchanged']=True
        result['synthetic_external_export_sha256']=packet_digest
        usage=manager.measure_usage(case_id)
        result['final_case_bytes']=usage.case_bytes;result['final_cache_bytes']=usage.cache_bytes
        result['peak_resource_measurement']='NOT_PERFORMED'
        result['preview_count']=len(previews)
        result['resource_profile']='constrained-v1'
        result['status']='pass'
except Exception as error:
    result['error_code']=getattr(getattr(error,'code',None),'value','QUALIFIER_SMOKE_FAILED')
finally:
    if backend is not None:
        try:backend.shutdown()
        except Exception:result['cleanup']='failed'
    (base/'smoke-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
raise SystemExit(0 if result['status']=='pass' else 1)
"""


def prepare_smoke(step: Path, *, root: Path = PROJECT_ROOT) -> None:
    plain_path(step)
    step.mkdir(parents=True, exist_ok=False)
    for name, (relative, digest) in FIXTURES.items():
        _digest(root / relative, digest)
        with (step / name).open("xb") as target:
            target.write((root / relative).read_bytes())
    with (step / "smoke.py").open("x", encoding="utf-8") as target:
        target.write(_SMOKE)


_GUI = r"""
import ctypes
ctypes.WinDLL('kernel32').SetErrorMode(0x8003)
from pathlib import Path
from paperworkshell import __main__ as desktop
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1 as policy
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.storage.uninstall_review import uninstall_application_guard
from paperworkshell.ui import application as ui
base=Path(__file__).absolute().parent
profile=base/'profile';profile.mkdir(exist_ok=True)
resources=desktop.create_desktop_application_resource_context(policy)
manager=CaseWorkspaceManager(profile_root=profile,quota=desktop._workspace_quota(policy),minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes)
class Window(ui.PaperWorkShellWindow):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.setWindowTitle('PaperWorkShell — isolated installed-host qualification')
ui.PaperWorkShellWindow=Window
with uninstall_application_guard():
    raise SystemExit(ui.run_desktop_application(lambda:desktop._build_backend(manager,resources=resources),['PaperWorkShell isolated qualification'],resource_policy=policy))
"""


def prepare_gui(
    work: Path, installation: Path, state: dict[str, Any], *, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    directory = work / "gui"
    prepare_smoke(directory, root=root)
    script = directory / "window.py"
    script.write_text(_GUI, encoding="utf-8")
    # This is an explicitly isolated engineering harness, not the normal launcher.
    from paperworkshell.storage._windows import current_profile_root
    from tools.build_windows_launcher import _windows_directory

    windows = _windows_directory()
    environment = {
        "SystemRoot": str(windows),
        "WINDIR": str(windows),
        "USERPROFILE": str(current_profile_root()),
        "PATH": str(windows / "System32"),
        "TEMP": str(directory),
        "TMP": str(directory),
    }
    process = subprocess.Popen(
        [str(installation / "runtime/pythonw.exe"), "-I", "-B", str(script)],
        cwd=directory,
        shell=False,
        env=environment,
    )
    state["gui"] = {
        "status": "started-not-qualified",
        "pid": process.pid,
        "title": "PaperWorkShell — isolated installed-host qualification",
        "harness_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
    }
    state.pop("pending_action", None)
    digest = _save(work, state)
    return {**state, "session_sha256": digest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "inspect",
            "install",
            "smoke",
            "gui",
            "preserve-uninstall",
            "reinstall",
            "status",
            "interrupt-repair",
            "recover-repair",
        ),
    )
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--build-receipt", type=Path)
    parser.add_argument("--installer-sha256")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--session-sha256")
    args = parser.parse_args(argv)
    try:
        if os.name != "nt":
            fail("QUALIFIER_WINDOWS_REQUIRED")
        if args.action == "inspect":
            if None in (
                args.installer,
                args.build_receipt,
                args.installer_sha256,
                args.receipt_sha256,
            ):
                fail("QUALIFIER_INPUT_REQUIRED")
            candidate = inspect_candidate(
                args.installer,
                args.build_receipt,
                expected_installer_sha256=args.installer_sha256,
                expected_receipt_sha256=args.receipt_sha256,
            )
            state = prepare_session(candidate, args.work_directory)
        else:
            state = run_action(
                args.action, args.work_directory, expected_session_sha256=args.session_sha256
            )
    except QualificationError as error:
        print(f"ERROR [{error}] Host qualification did not complete.", file=sys.stderr)
        return 1
    except Exception:
        print("ERROR [QUALIFIER_FAILED] Host qualification did not complete.", file=sys.stderr)
        return 1
    print(
        "HOST_NOT_CLEAN: "
        + str(state["phase"])
        + "; normal first launch/clean Windows/standard-account acceptance remain unproved."
    )
    print("Controlled session SHA-256: " + str(state["session_sha256"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
