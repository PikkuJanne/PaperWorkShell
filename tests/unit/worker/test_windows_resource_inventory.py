"""Unit contracts for retained Windows resource-verification handles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

from paperworkshell.worker.client._windows import (
    _ApprovedResourceInventory,
    _RetainedResource,
    _RetainedResourceGuard,
    _RetainedResourceInfo,
)


def test_inventory_retains_every_entry_until_all_handles_close() -> None:
    secret_root = Path("C:/synthetic-secret/approved-runtime")
    resource_handle = Mock()
    guard_handle = Mock()
    resource_close_attempts = 0

    def close_resource() -> bool:
        nonlocal resource_close_attempts
        resource_close_attempts += 1
        return resource_close_attempts >= 4

    resource_handle.close_no_raise.side_effect = close_resource
    guard_handle.close_no_raise.return_value = True
    info = _RetainedResourceInfo(
        identity=(7, b"synthetic-file-id"),
        is_directory=True,
        is_reparse_point=False,
        delete_pending=False,
        link_count=1,
    )
    resource = _RetainedResource(
        secret_root,
        cast(Any, resource_handle),
        info,
    )
    guard = _RetainedResourceGuard(
        secret_root.parent,
        cast(Any, guard_handle),
        info,
    )
    inventory = _ApprovedResourceInventory(
        resources=[resource],
        guards=[guard],
        resources_by_path={str(secret_root): resource},
        guards_by_path={str(secret_root.parent): guard},
    )

    assert not inventory.close_no_raise()
    assert inventory.resources == [resource]
    assert inventory.guards == [guard]

    assert inventory.close_no_raise()
    assert inventory.resources == []
    assert inventory.guards == []
    assert inventory.resources_by_path == {}
    assert inventory.guards_by_path == {}
    assert resource_close_attempts == 4
    assert guard_handle.close_no_raise.call_count == 2
    assert str(secret_root) not in repr(inventory)
    assert str(secret_root) not in repr(resource)
    assert str(secret_root.parent) not in repr(guard)
