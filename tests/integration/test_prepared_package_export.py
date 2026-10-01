"""Private staged package lifetime and approval-time authority regressions."""

from __future__ import annotations

import ctypes
import msvcrt
from pathlib import Path

import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.package_export import (
    PackageExportError,
    PackageExportErrorCode,
    prepare_package_plan,
)
from paperworkshell.export.target_size_export import prepare_target_size_package
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.export_destination import WindowsExportDestination
from tests.integration.test_package_export import _payloads, _plan, _Producer, _snapshot
from tests.integration.test_target_size_package_export import _limited_plan, _Stages


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
def test_prepared_package_is_private_until_fresh_deadline_commit(
    tmp_path: Path, mode: PortalOutputMode
) -> None:
    plan = _plan(mode)
    payloads = _payloads(plan)
    expired = False
    with prepare_package_plan(
        _snapshot(),
        plan,
        tmp_path,
        _Producer(payloads),
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: expired,
        cancellation=lambda: False,
    ) as prepared:
        assert all(not (tmp_path / pdf.filename).exists() for pdf in plan.pdfs)
        if plan.archive is not None:
            assert not (tmp_path / plan.archive.filename).exists()
            assert prepared.report.archive is not None
            assert (
                prepared.staged_byte_size
                == sum(map(len, payloads.values())) + prepared.report.archive.archive_byte_size
            )
        expired = True  # Human review outlives its preparation deadline.
        for pdf in plan.pdfs:
            with prepared.open_pdf(
                pdf.output_key, deadline=lambda: False, cancellation=lambda: False
            ) as source:
                assert source.read() == payloads[pdf.output_key]
        outcome = prepared.commit(_snapshot(), deadline=lambda: False, cancellation=lambda: False)
    assert outcome.successful
    expected = (
        {plan.archive.filename} if plan.archive is not None else {pdf.filename for pdf in plan.pdfs}
    )
    assert {item.name for item in tmp_path.iterdir()} == expected


def test_discard_removes_private_zip_and_all_reviewable_pdf_members(tmp_path: Path) -> None:
    plan = _plan(PortalOutputMode.ZIP)
    with prepare_package_plan(
        _snapshot(),
        plan,
        tmp_path,
        _Producer(_payloads(plan)),
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as prepared:
        assert prepared.staged_byte_size > 0
    prepared.close()
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize(
    "cancelled, code",
    [(False, PackageExportErrorCode.PLAN_STALE), (True, PackageExportErrorCode.CANCELLED)],
)
def test_stale_or_cancelled_approval_never_publishes(
    tmp_path: Path, cancelled: bool, code: PackageExportErrorCode
) -> None:
    plan = _plan(PortalOutputMode.SEPARATE_PDFS)
    with prepare_package_plan(
        _snapshot(),
        plan,
        tmp_path,
        _Producer(_payloads(plan)),
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as prepared:
        with pytest.raises(PackageExportError) as captured:
            prepared.commit(
                _snapshot(sha256="e" * 64), deadline=lambda: False, cancellation=lambda: cancelled
            )
        assert captured.value.code is code
    assert tuple(tmp_path.iterdir()) == ()


def test_preview_native_handle_has_no_write_authority_and_blocks_concurrent_commit(
    tmp_path: Path,
) -> None:
    plan = _plan(PortalOutputMode.COMBINED_PDF)
    with prepare_package_plan(
        _snapshot(),
        plan,
        tmp_path,
        _Producer(_payloads(plan)),
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as prepared:
        with prepared.open_pdf(
            "package", deadline=lambda: False, cancellation=lambda: False
        ) as source:
            write = ctypes.WinDLL("kernel32", use_last_error=True).WriteFile
            write.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_void_p,
            )
            write.restype = ctypes.c_int
            count = ctypes.c_uint32()
            assert not write(
                msvcrt.get_osfhandle(source.fileno()),
                ctypes.create_string_buffer(b"bad"),
                3,
                ctypes.byref(count),
                None,
            )
            assert ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
            with pytest.raises(PackageExportError) as captured:
                prepared.commit(_snapshot(), deadline=lambda: False, cancellation=lambda: False)
            assert captured.value.code is PackageExportErrorCode.MODEL_INVALID
        assert prepared.commit(
            _snapshot(), deadline=lambda: False, cancellation=lambda: False
        ).successful


def test_target_search_keeps_only_terminal_candidate_private(tmp_path: Path) -> None:
    plan = _limited_plan(PortalOutputMode.COMBINED_PDF, file_bytes=600)
    producer = _Stages(plan, (1000, 600, 500, 400))
    with prepare_target_size_package(
        _snapshot(),
        plan,
        tmp_path,
        producer,
        WindowsExportDestination(minimum_free_space_reserve_bytes=0),
        CONSTRAINED_RESOURCE_POLICY_V1,
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as prepared:
        assert len(prepared.decision.attempts) == 2
        assert prepared.package.staged_byte_size == 600
        assert not (tmp_path / "packet.pdf").exists()
        with prepared.package.open_pdf(
            "package", deadline=lambda: False, cancellation=lambda: False
        ) as reader:
            assert reader.read() == b"x" * 600
    assert tuple(tmp_path.iterdir()) == ()
