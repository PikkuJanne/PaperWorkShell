"""Real Windows children prove bounded cleanup without escaping the kill job."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from tools.windows_signing import WindowsSigningError, run_build_process


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle")
def test_successful_uninstaller_child_can_finish_deferred_self_deletion(tmp_path: Path) -> None:
    owned = tmp_path / "synthetic-owned-uninstaller"
    owned.write_bytes(b"synthetic owned uninstall bytes")
    child = "import pathlib,sys,time;time.sleep(0.5);pathlib.Path(sys.argv[1]).unlink()"
    parent = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1],sys.argv[2]],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print('parent completed')"
    )
    output = run_build_process(
        [sys.executable, "-I", "-B", "-c", parent, child, str(owned)],
        tmp_path,
        timeout=15,
        successful_descendant_grace_seconds=3,
    )
    assert output.strip() == b"parent completed"
    assert not owned.exists(), "Deferred owned-file cleanup was killed after the first phase exited"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle")
@pytest.mark.parametrize("mode", ["default", "grace-timeout", "parent-failure"])
def test_default_failure_and_expired_grace_still_kill_descendants(
    tmp_path: Path, mode: str
) -> None:
    owned = tmp_path / "synthetic-owned-uninstaller"
    owned.write_bytes(b"synthetic owned uninstall bytes")
    pid_file = tmp_path / "child-pid.txt"
    child = "import pathlib,sys,time;time.sleep(15);pathlib.Path(sys.argv[1]).unlink()"
    parent = (
        "import pathlib,subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1],sys.argv[2]],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "pathlib.Path(sys.argv[3]).write_text(str(child.pid));"
        "raise SystemExit(int(sys.argv[4]))"
    )
    argv = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        parent,
        child,
        str(owned),
        str(pid_file),
        "1" if mode == "parent-failure" else "0",
    ]
    if mode == "default":
        run_build_process(argv, tmp_path, timeout=10)
    else:
        code = "FAILED" if mode == "parent-failure" else "TIMEOUT"
        with pytest.raises(WindowsSigningError, match=f"SIGNING_PROCESS_{code}"):
            run_build_process(
                argv,
                tmp_path,
                timeout=10,
                successful_descendant_grace_seconds=0.1,
            )
    assert owned.exists()
    pid = int(pid_file.read_text())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 1000) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87  # Process no longer exists.
