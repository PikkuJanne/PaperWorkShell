"""PWS-0811 keeps grants exact while avoiding repeated subtree propagation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from paperworkshell.worker.client import _windows as windows

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")


def test_live_nested_grants_never_inherit_to_new_files_and_cleanup_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery = tmp_path / "private-test-recovery"
    monkeypatch.setattr(windows, "_acl_recovery_directory_path", lambda: recovery)
    root = tmp_path / "approved-runtime"
    nested = root / "one" / "two" / "three"
    nested.mkdir(parents=True)
    source = nested / "synthetic.py"
    source.write_text("pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    before = source.read_bytes(), outside.read_bytes()
    profile = windows._AppContainerProfile.create((root,))
    try:
        profile.grant_read_execute((root,))
        profile.require_read_execute((root, nested, source))
        assert not windows._has_effective_app_container_read_execute(outside, profile.sid)
        added = nested / "created-after-inventory.py"
        added.write_text("pass\n", encoding="utf-8")
        handle, _info = windows._open_retained_resource(added, acl_authority=False)
        try:
            assert windows._sid_access_mask_handle(handle.value, profile.sid) == 0
        finally:
            assert windows._close_owned_handle_bounded(handle)
        assert source.read_bytes() == before[0]
        assert outside.read_bytes() == before[1]
    finally:
        assert windows._close_profile_bounded(profile)


def test_directory_grant_does_not_rewrite_noninventoried_descendant_inheritance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "single-approved-directory"
    child = root / "uninventoried-child"
    child.mkdir(parents=True)
    root_handle, _root_info = windows._open_retained_resource(root, acl_authority=True)
    child_handle, _child_info = windows._open_retained_resource(child, acl_authority=True)
    sid = windows._convert_string_sid("S-1-15-2-101-102-103-104-105-106-107-108")
    root_control = windows._dacl_auto_inherited_handle(root_handle.value)
    child_control = windows._dacl_auto_inherited_handle(child_handle.value)
    try:
        windows._restore_dacl_auto_inherited_handle(child_handle.value, False)
        assert not windows._dacl_auto_inherited_handle(child_handle.value)
        windows._change_sid_access_handle(root_handle.value, sid, windows._GRANT_ACCESS)
        assert not windows._dacl_auto_inherited_handle(child_handle.value)
        assert windows._sid_access_mask_handle(child_handle.value, sid) == 0
    finally:
        windows._change_sid_access_handle(root_handle.value, sid, windows._REVOKE_ACCESS)
        windows._restore_dacl_auto_inherited_handle(root_handle.value, root_control)
        windows._restore_dacl_auto_inherited_handle(child_handle.value, child_control)
        windows._local_free(sid)
        assert windows._close_owned_handle_bounded(child_handle)
        assert windows._close_owned_handle_bounded(root_handle)
