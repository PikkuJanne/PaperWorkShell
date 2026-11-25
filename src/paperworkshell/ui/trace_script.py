"""Publish a generated PowerShell script without replacing an existing file."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from paperworkshell.app.trace_automation import TraceAutomationError, make_trace_powershell
from paperworkshell.domain.resource_policy import select_resource_policy
from paperworkshell.domain.trace_recipe import TraceRecipe
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationError,
    WindowsExportDestination,
)


def export_trace_powershell(recipe: TraceRecipe, destination: Path) -> None:
    """Publish a script atomically and fail on an occupied destination."""

    payload = make_trace_powershell(recipe)
    if destination.suffix.casefold() != ".ps1":
        raise TraceAutomationError("TRACE_SCRIPT_NAME", "Choose a PowerShell .ps1 filename.")
    policy = select_resource_policy()
    publisher = WindowsExportDestination(
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    try:
        result = publisher.commit(
            destination,
            BytesIO(payload),
            expected_byte_size=len(payload),
            expected_sha256=sha256(payload).hexdigest(),
            cancellation=lambda: False,
        )
    except ExportDestinationError as error:
        raise TraceAutomationError(error.code.value, error.safe_message) from None
    if result.state is ExportCommitState.DESTINATION_EXISTS:
        raise TraceAutomationError(
            "TRACE_SCRIPT_EXISTS", "That filename is in use. Choose a new script filename."
        )
    if result.state is not ExportCommitState.COMMITTED:
        raise TraceAutomationError(
            "TRACE_SCRIPT_COMMIT_UNCERTAIN", "Check the destination before saving the script again."
        )
