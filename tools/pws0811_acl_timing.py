"""Measure ACL primitives on disposable synthetic trees, never application files."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import tempfile
from contextlib import nullcontext
from ctypes import wintypes
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from paperworkshell.worker.client import _windows as windows


def _legacy_setter(
    handle: int, expected: windows._RetainedResourceInfo, dacl: ctypes.c_void_p
) -> None:
    del expected
    setter = windows._advapi32().SetSecurityInfo
    setter.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    setter.restype = wintypes.DWORD
    if (
        setter(
            wintypes.HANDLE(handle),
            windows._SE_FILE_OBJECT,
            windows._DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        != windows._ERROR_SUCCESS
    ):
        raise RuntimeError("synthetic legacy ACL operation failed")


def measure(work_root: Path, *, legacy: bool) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="pws0811-acl-", dir=work_root) as raw:
        root = Path(raw).resolve()
        if not root.is_relative_to(work_root.resolve()):
            raise ValueError("synthetic benchmark escaped workspace")
        frontier = [root]
        for _ in range(3):
            following: list[Path] = []
            for parent in frontier:
                for number in range(5):
                    child = parent / f"d{number}"
                    child.mkdir()
                    following.append(child)
                    for file_number in range(4):
                        (child / f"synthetic{file_number}.py").write_text(
                            "pass\n", encoding="utf-8"
                        )
            frontier = following
        paths = (root, *sorted(root.rglob("*"), key=lambda path: len(path.parts)))
        sid = windows._convert_string_sid(
            "S-1-15-2-" + "-".join(str(value) for value in uuid4().bytes[:8])
        )
        handles: list[windows._OwnedHandle] = []
        inherited: list[bool] = []
        try:
            for path in paths:
                handle, _info = windows._open_retained_resource(path, acl_authority=True)
                handles.append(handle)
                inherited.append(windows._dacl_auto_inherited_handle(handle.value))
            mode = (
                patch.object(windows, "_set_pinned_directory_dacl", _legacy_setter)
                if legacy
                else nullcontext()
            )
            with mode:
                started = monotonic()
                for handle in handles:
                    windows._change_sid_access_handle(handle.value, sid, windows._GRANT_ACCESS)
                granted = monotonic()
                for handle in reversed(handles):
                    windows._change_sid_access_handle(handle.value, sid, windows._REVOKE_ACCESS)
                revoked = monotonic()
            for handle, expected in zip(handles, inherited, strict=True):
                assert windows._sid_access_mask_handle(handle.value, sid) == 0
                windows._restore_dacl_auto_inherited_handle(handle.value, expected)
            return {
                "mode": "legacy-SetSecurityInfo" if legacy else "pinned-directory-SetFileSecurityW",
                "objects": len(paths),
                "directories": sum(path.is_dir() for path in paths),
                "grant_seconds": granted - started,
                "revoke_seconds": revoked - granted,
                "grant_and_revoke_seconds": revoked - started,
                "revocation_verified": True,
            }
        finally:
            for handle in reversed(handles):
                try:
                    windows._change_sid_access_handle(handle.value, sid, windows._REVOKE_ACCESS)
                finally:
                    if not windows._close_owned_handle_bounded(handle):
                        raise RuntimeError("synthetic resource cleanup failed")
            windows._local_free(sid)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows required")
    work = Path(__file__).resolve().parents[1] / ".paperworkshell-local"
    work.mkdir(exist_ok=True)
    runs = [measure(work, legacy=legacy) for legacy in (True, False, True, False)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "scope": "Synthetic ACL primitive only. No worker, application/runtime ACL or global recovery state.",
                "runs": runs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for result in runs:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
