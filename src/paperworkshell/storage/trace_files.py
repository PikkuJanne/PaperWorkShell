"""Bounds and authenticates the local files TRACE opens and saves."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Final

from paperworkshell.domain.trace_recipe import (
    MAX_TRACE_RECIPE_BYTES,
    TraceOutputStatus,
    TraceRecipe,
    TraceRecipeError,
    TraceRecipeErrorCode,
    decode_trace_recipe,
    encode_trace_recipe,
)
from paperworkshell.storage._windows import (
    MAXIMUM_STREAM_CHUNK_BYTES,
    open_handle,
    require_local_acl_volume,
)
from paperworkshell.storage.errors import WorkspaceError
from paperworkshell.storage.export_destination import (
    MAX_EXPORT_DESTINATION_BYTES,
    ExportDestinationError,
    WindowsExportDestination,
    _normalized,
    _validate_destination,
)

_TRACE_INSPECTION_SECONDS: Final = 30


def load_trace_recipe(path: Path) -> TraceRecipe:
    """Read a bounded local recipe without executing any of its contents."""

    try:
        selected = _validate_destination(path)
        require_local_acl_volume(selected.parent)
        with open_handle(selected, read=True) as file:
            facts = file.info
            if (
                facts.is_directory
                or facts.is_reparse_point
                or _normalized(facts.final_path) != _normalized(selected)
                or not 1 <= facts.logical_size <= MAX_TRACE_RECIPE_BYTES
            ):
                raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
            payload = file.read_exact(facts.logical_size)
            file.require_eof()
        return decode_trace_recipe(payload)
    except OSError, WorkspaceError, ExportDestinationError:
        raise TraceRecipeError(TraceRecipeErrorCode.IO_FAILED) from None


def save_trace_recipe(path: Path, recipe: TraceRecipe) -> None:
    """Commit portable recipe bytes using the established no-replace publisher."""

    recipe.require_automation_supported()
    payload = encode_trace_recipe(recipe)
    try:
        result = WindowsExportDestination().commit(
            path,
            BytesIO(payload),
            expected_byte_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            cancellation=lambda: False,
        )
        if not result.committed:
            raise TraceRecipeError(TraceRecipeErrorCode.IO_FAILED)
    except ExportDestinationError:
        raise TraceRecipeError(TraceRecipeErrorCode.IO_FAILED) from None


def inspect_trace_output(
    destination: Path,
    expected_byte_size: int,
    expected_sha256: str,
    cancellation: Callable[[], bool],
) -> TraceOutputStatus:
    """Before opening, stream-verify the selected artifact against its prior readback."""

    if cancellation():
        return TraceOutputStatus.CANCELLED
    deadline = monotonic() + _TRACE_INSPECTION_SECONDS
    try:
        selected = _validate_destination(destination)
        require_local_acl_volume(selected.parent)
        selected.lstat()
        with open_handle(selected, read=True) as file:
            facts = file.info
            if (
                facts.is_directory
                or facts.is_reparse_point
                or facts.link_count != 1
                or _normalized(facts.final_path) != _normalized(selected)
                or facts.logical_size != expected_byte_size
                or not 1 <= facts.logical_size <= MAX_EXPORT_DESTINATION_BYTES
            ):
                return TraceOutputStatus.CHANGED
            remaining = facts.logical_size
            hasher = hashlib.sha256()
            while remaining:
                if cancellation():
                    return TraceOutputStatus.CANCELLED
                if monotonic() >= deadline:
                    return TraceOutputStatus.UNAVAILABLE
                count = min(remaining, MAXIMUM_STREAM_CHUNK_BYTES)
                hasher.update(file.read_exact(count))
                remaining -= count
            file.require_eof()
    except FileNotFoundError:
        return TraceOutputStatus.MISSING
    except OSError, WorkspaceError, ExportDestinationError:
        return TraceOutputStatus.UNAVAILABLE
    return (
        TraceOutputStatus.PRESENT
        if hmac.compare_digest(hasher.hexdigest(), expected_sha256)
        else TraceOutputStatus.CHANGED
    )
