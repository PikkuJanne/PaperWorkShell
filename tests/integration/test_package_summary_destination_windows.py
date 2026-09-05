"""PWS-0408 plain-text companion publication through real Windows handles."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryError,
    PackageSummaryErrorCode,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.package_export import PackagePdfProducerPort
from paperworkshell.export.package_service import WindowsPackageExportService
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.export_destination import (
    ExportDestinationDependencies,
    ExportDestinationError,
    ExportDestinationErrorCode,
    WindowsExportDestination,
)


def _unused_producer() -> AbstractContextManager[PackagePdfProducerPort]:
    raise AssertionError("saving visible summary text must not open document workers")


def _service(
    dependencies: ExportDestinationDependencies | None = None,
) -> WindowsPackageExportService:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return WindowsPackageExportService(
        _unused_producer,
        WindowsExportDestination(
            dependencies,
            minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
        ),
        policy,
    )


def _summary() -> PackageSummary:
    return PackageSummary(
        PackageSummaryStatus.COMMITTED,
        outputs=(
            PackageSummaryOutput(
                "synthetic-ä-application.pdf",
                PackageSummaryOutputState.COMMITTED,
                3,
                2048,
                ("Application form",),
                ("Expected page count",),
            ),
        ),
        active_page_count=3,
    )


def test_companion_contains_exact_visible_utf8_and_is_separate_from_pdf_count(
    tmp_path: Path,
) -> None:
    summary = _summary()
    target = tmp_path / "package-summary.txt"

    state = _service().save_summary(
        summary, target, deadline=lambda: False, cancellation=lambda: False
    )

    assert state == "committed"
    assert target.read_bytes() == summary.to_plain_text().encode("utf-8")
    assert summary.file_count == 1
    assert summary.saved_file_count == 1
    assert tuple(tmp_path.iterdir()) == (target,)


def test_preexisting_summary_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "package-summary.txt"
    target.write_text("synthetic previous summary", encoding="utf-8")

    state = _service().save_summary(
        _summary(), target, deadline=lambda: False, cancellation=lambda: False
    )

    assert state == "destination_exists"
    assert target.read_text(encoding="utf-8") == "synthetic previous summary"
    assert tuple(tmp_path.iterdir()) == (target,)


@pytest.mark.parametrize("expired", [False, True])
def test_cancelled_or_expired_companion_is_not_published(tmp_path: Path, expired: bool) -> None:
    with pytest.raises(ExportDestinationError) as caught:
        _service().save_summary(
            _summary(),
            tmp_path / "package-summary.txt",
            deadline=lambda: expired,
            cancellation=lambda: not expired,
        )
    expected = (
        ExportDestinationErrorCode.TIMED_OUT if expired else ExportDestinationErrorCode.CANCELLED
    )
    assert caught.value.code is expected
    assert tuple(tmp_path.iterdir()) == ()


def test_low_disk_companion_refusal_preserves_destination_and_reserve(tmp_path: Path) -> None:
    service = _service(ExportDestinationDependencies(available_space=lambda _: 0))

    with pytest.raises(ExportDestinationError) as caught:
        service.save_summary(
            _summary(),
            tmp_path / "package-summary.txt",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is ExportDestinationErrorCode.DESTINATION_FULL
    assert tuple(tmp_path.iterdir()) == ()


def test_write_failure_is_sanitized_and_private_stage_is_removed(tmp_path: Path) -> None:
    def fail_write(_stage: WindowsHandle, _payload: bytes) -> int:
        raise OSError(5, "PRIVATE synthetic document text must not escape")

    service = _service(ExportDestinationDependencies(write_stage=fail_write))
    with pytest.raises(ExportDestinationError) as caught:
        service.save_summary(
            _summary(),
            tmp_path / "package-summary.txt",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__context__ is None
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize("extension", [".pdf", ".zip", ".html", ""])
def test_summary_save_rejects_non_text_target_before_any_write(
    tmp_path: Path, extension: str
) -> None:
    with pytest.raises(PackageSummaryError) as caught:
        _service().save_summary(
            _summary(),
            tmp_path / f"package-summary{extension}",
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert caught.value.code is PackageSummaryErrorCode.MODEL_INVALID
    assert tuple(tmp_path.iterdir()) == ()
