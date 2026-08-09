"""Create one durable ACL lease, then remain the named-mutex owner until killed."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._windows import _AclMutationMutex, _AppContainerProfile


def main() -> int:
    if len(sys.argv) < 6:
        return 2
    cutpoint = sys.argv[1]
    recovery_root = Path(sys.argv[2])
    ready_path = Path(sys.argv[3])
    mutex_name = sys.argv[4]
    approved_roots = tuple(Path(value) for value in sys.argv[5:])
    if cutpoint not in {"active", "prepared", "partial_grant", "revoking"}:
        return 2
    windows_module._acl_recovery_directory_path = lambda: recovery_root
    vars(windows_module)["_ACL_MUTATION_MUTEX_NAME"] = mutex_name

    profile = _AppContainerProfile.create(approved_roots)

    def hold_until_killed() -> None:
        descriptor = os.open(ready_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            payload = profile.name.encode("ascii")
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short readiness write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        while True:
            time.sleep(60.0)

    if cutpoint == "active":
        profile.grant_read_execute(approved_roots)
        mutex = _AclMutationMutex(recover_abandoned=True)
        mutex.__enter__()
        hold_until_killed()

    original_change = windows_module._change_sid_access
    grant_calls = 0

    def interrupt_change(path: Path, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal grant_calls
        if mode == windows_module._GRANT_ACCESS:
            grant_calls += 1
            if cutpoint == "prepared" and grant_calls == 1:
                hold_until_killed()
            original_change(path, sid, mode)
            if cutpoint == "partial_grant" and grant_calls == 1:
                hold_until_killed()
            return
        original_change(path, sid, mode)
        if cutpoint == "revoking" and mode == windows_module._REVOKE_ACCESS:
            hold_until_killed()

    windows_module._change_sid_access = interrupt_change
    if cutpoint in {"prepared", "partial_grant"}:
        profile.grant_read_execute(approved_roots)
    else:
        profile.grant_read_execute(approved_roots)
        profile.close_no_raise()
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
