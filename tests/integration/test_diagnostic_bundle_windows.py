"""Synthetic Windows publication: exact preview bytes and safe failure cleanup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from paperworkshell.diagnostics.errors import DiagnosticError, DiagnosticErrorCode
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.storage import export_destination as destination_module
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.diagnostic_bundle import WindowsDiagnosticBundleWriter
from paperworkshell.storage.export_destination import (
    ExportDestinationDependencies,
    ExportDestinationPhase,
    WindowsExportDestination,
)
from tests.helpers.diagnostic_bundle import sample_preview

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires authenticated Windows publication"
)


@pytest.mark.parametrize("policy", (NORMAL_RESOURCE_POLICY_V1, CONSTRAINED_RESOURCE_POLICY_V1))
def test_exact_preview_bytes_publish_without_case_or_log_access(
    tmp_path: Path, policy: ResourcePolicy
) -> None:
    preview = sample_preview()
    destination = tmp_path / "support.zip"
    writer = WindowsDiagnosticBundleWriter(policy)
    writer.write(preview, destination, cancellation=lambda: False)
    assert destination.read_bytes() == preview.archive
    with pytest.raises(DiagnosticError) as error:
        writer.write(preview, destination, cancellation=lambda: False)
    assert error.value.code is DiagnosticErrorCode.DESTINATION_EXISTS
    assert destination.read_bytes() == preview.archive
    assert list(tmp_path.glob(".*.pws-export.tmp")) == []


@pytest.mark.parametrize("failure", ("cancel", "denied", "partial", "disk_full", "low_space"))
def test_failure_never_publishes_partial_bundle_or_leaves_stage(
    tmp_path: Path, failure: str
) -> None:
    cancelled = False
    wrote_partial = False

    def checkpoint(phase: ExportDestinationPhase) -> None:
        nonlocal cancelled
        if phase is ExportDestinationPhase.BEFORE_READBACK and failure == "cancel":
            cancelled = True
        if phase is ExportDestinationPhase.BEFORE_STAGE_CREATE and failure == "denied":
            raise PermissionError("C:\\private\\prohibited-token")

    def write_stage(_handle: WindowsHandle, _payload: bytes) -> int:
        nonlocal wrote_partial
        if failure == "disk_full":
            raise OSError(28, "synthetic disk full")
        if wrote_partial:
            raise PermissionError("synthetic failure after partial stage write")
        wrote_partial = True
        return destination_module._write_stage_once(_handle, _payload[:7])

    dependencies = ExportDestinationDependencies(
        checkpoint=checkpoint,
        write_stage=write_stage if failure in {"partial", "disk_full"} else None,
        available_space=(lambda _path: 0) if failure == "low_space" else None,
    )
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    publisher = WindowsExportDestination(
        dependencies,
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    writer = WindowsDiagnosticBundleWriter(policy, publisher=publisher)
    destination = tmp_path / "support.zip"
    with pytest.raises(DiagnosticError) as error:
        writer.write(sample_preview(), destination, cancellation=lambda: cancelled)
    assert error.value.code is (
        DiagnosticErrorCode.CANCELLED if failure == "cancel" else DiagnosticErrorCode.EXPORT_FAILED
    )
    assert "private" not in str(error.value)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_successive_short_writes_publish_only_complete_verified_archive(tmp_path: Path) -> None:
    preview = sample_preview()
    destination = tmp_path / "support.zip"
    calls = 0

    def short_write(handle: WindowsHandle, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        return destination_module._write_stage_once(handle, payload[:7])

    writer = WindowsDiagnosticBundleWriter(
        CONSTRAINED_RESOURCE_POLICY_V1,
        publisher=WindowsExportDestination(ExportDestinationDependencies(write_stage=short_write)),
    )
    writer.write(preview, destination, cancellation=lambda: False)
    assert calls > 1
    assert destination.read_bytes() == preview.archive
    assert not list(tmp_path.glob(".*.pws-export.tmp"))
