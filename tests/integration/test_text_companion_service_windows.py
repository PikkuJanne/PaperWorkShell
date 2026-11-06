"""PWS-0706 explicit privacy consent, publication and export preservation."""

from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.text_companion import TextCompanionError, TextCompanionErrorCode
from paperworkshell.export.package_export import PackagePdfProducerPort
from paperworkshell.export.package_service import WindowsPackageExportService
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.export_destination import (
    ExportDestinationDependencies,
    ExportDestinationError,
    WindowsExportDestination,
)
from tests.unit.test_package_summary import SummaryInputs, summary_inputs, summary_outcome


def _unused() -> AbstractContextManager[PackagePdfProducerPort]:
    raise AssertionError("Post-export text never opens original sources")


class _Extractor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def extract_handle(self, case_id: UUID, source_handle: int, **kwargs: Any) -> bytes:
        assert source_handle > 0
        self.calls.append(kwargs)
        return "Page 1\nPWSKEEP UTF-8 ä\n".encode()


def _service(
    tmp_path: Path, dependencies: ExportDestinationDependencies | None = None
) -> tuple[WindowsPackageExportService, SummaryInputs, _Extractor]:
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF)
    outcome = summary_outcome(inputs)
    assert inputs.plan is not None
    (tmp_path / inputs.plan.pdfs[0].filename).write_bytes(b"%PDF-synthetic-summary-report-only\n")
    extractor = _Extractor()
    service = WindowsPackageExportService(
        _unused,
        WindowsExportDestination(dependencies),
        CONSTRAINED_RESOURCE_POLICY_V1,
        text_extractor=extractor,
        forbidden_export_root=tmp_path / "private-cases",
    )
    service.remember_verified_export(inputs.snapshot, outcome, tmp_path)
    return service, inputs, extractor


def test_text_companion_is_separate_utf8_warned_and_retained_after_explicit_save(
    tmp_path: Path,
) -> None:
    service, inputs, extractor = _service(tmp_path)
    target = tmp_path / "companion.txt"
    assert (
        service.save_text_companion(
            target, privacy_confirmed=True, deadline=lambda: False, cancellation=lambda: False
        )
        == "committed"
    )
    text = target.read_text(encoding="utf-8")
    assert "PWSKEEP UTF-8 ä" in text and "remains after case deletion" in text
    assert "PDF 1" in text and "OCR and reading order may be wrong" in text
    assert len(extractor.calls) == 1 and len(service.preserved_export_artifacts) == 2
    assert (
        service.preserved_export_artifacts[-1].sha256
        == hashlib.sha256(target.read_bytes()).hexdigest()
    )
    assert inputs.snapshot == service.last_verified_export_snapshot
    assert "companion" not in repr(service.preserved_export_artifacts)


@pytest.mark.parametrize("confirmed", (False, None, 1, "yes"))
def test_privacy_confirmation_is_required_before_any_text_is_read(
    tmp_path: Path, confirmed: Any
) -> None:
    service, _, extractor = _service(tmp_path)
    with pytest.raises(TextCompanionError) as caught:
        service.save_text_companion(
            tmp_path / "companion.txt",
            privacy_confirmed=confirmed,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert caught.value.code is TextCompanionErrorCode.CONSENT_REQUIRED
    assert not extractor.calls


def test_changed_export_prevents_extraction_and_keeps_case_receipt(tmp_path: Path) -> None:
    service, _, extractor = _service(tmp_path)
    service.preserved_export_artifacts[0].path.write_bytes(b"changed")
    with pytest.raises(TextCompanionError) as caught:
        service.save_text_companion(
            tmp_path / "companion.txt",
            privacy_confirmed=True,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert caught.value.code is TextCompanionErrorCode.EXPORT_CHANGED
    assert not extractor.calls and not (tmp_path / "companion.txt").exists()


@pytest.mark.parametrize("kind", ("case", "cancel", "disk_full", "exists"))
def test_failure_or_refusal_preserves_verified_pdf_and_does_not_overwrite(
    tmp_path: Path, kind: str
) -> None:
    dependencies = (
        ExportDestinationDependencies(available_space=lambda _: 0) if kind == "disk_full" else None
    )
    service, _, extractor = _service(tmp_path, dependencies)
    target = tmp_path / "companion.txt"
    if kind == "case":
        target = tmp_path / "private-cases" / "companion.txt"
    if kind == "exists":
        target.write_bytes(b"PREVIOUS")
        assert (
            service.save_text_companion(
                target, privacy_confirmed=True, deadline=lambda: False, cancellation=lambda: False
            )
            == "destination_exists"
        )
        assert target.read_bytes() == b"PREVIOUS"
    else:
        with pytest.raises((TextCompanionError, ExportDestinationError)):
            service.save_text_companion(
                target,
                privacy_confirmed=True,
                deadline=lambda: False,
                cancellation=lambda: kind == "cancel",
            )
        assert not target.exists()
    assert len(service.preserved_export_artifacts) == 1
    assert (
        service.preserved_export_artifacts[0].path.read_bytes()
        == b"%PDF-synthetic-summary-report-only\n"
    )
    if kind in {"case", "cancel"}:
        assert not extractor.calls


@pytest.mark.parametrize("invalid", ("digest", "cas", "partial"))
def test_invalid_new_receipt_clears_prior_companion_authority(tmp_path: Path, invalid: str) -> None:
    from paperworkshell.storage.export_destination import ExportCommitState

    service, inputs, extractor = _service(tmp_path)
    snapshot = inputs.snapshot
    outcome = summary_outcome(inputs)
    if invalid == "partial":
        outcome = summary_outcome(inputs, (ExportCommitState.NOT_COMMITTED,))
    else:
        snapshot = ManifestSnapshot(
            snapshot.case_id,
            snapshot.schema_version,
            snapshot.revision,
            snapshot.document,
            "e" * 64 if invalid == "digest" else snapshot.sha256,
            "f" * 64 if invalid == "cas" else snapshot.cas_token,
        )
    service.remember_verified_export(snapshot, outcome, tmp_path)
    with pytest.raises(TextCompanionError) as caught:
        service.save_text_companion(
            tmp_path / "new.txt",
            privacy_confirmed=True,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert caught.value.code is TextCompanionErrorCode.EXPORT_REQUIRED
    assert service.last_verified_export_snapshot is None and not extractor.calls


def test_companion_expected_snapshot_cannot_read_previous_case_or_manifest_receipt(
    tmp_path: Path,
) -> None:
    service, inputs, extractor = _service(tmp_path)
    snapshot = inputs.snapshot
    different = ManifestSnapshot(
        snapshot.case_id,
        snapshot.schema_version,
        snapshot.revision,
        snapshot.document,
        "e" * 64,
        snapshot.cas_token,
    )
    with pytest.raises(TextCompanionError) as caught:
        service.save_text_companion(
            tmp_path / "new.txt",
            privacy_confirmed=True,
            expected_snapshot=different,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert caught.value.code is TextCompanionErrorCode.EXPORT_REQUIRED
    assert not extractor.calls


def test_same_snapshot_compression_cleanup_failure_cannot_reuse_prior_ordinary_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperworkshell.app.package_workflow import PackageWorkflowService
    from paperworkshell.domain.package_summary import PackageSummaryStatus
    from paperworkshell.domain.target_size_search import TargetSizeColorChoice
    from paperworkshell.export.package_summary import build_package_summary
    from paperworkshell.export.target_size_export import PreparedTargetSizePackage
    from paperworkshell.templates.catalog import (
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        built_in_template_repository,
    )
    from paperworkshell.templates.loader import SafeTemplateLoader
    from tests.helpers.package_pilot import AS_OF
    from tests.integration.test_compression_review_service import Renderer
    from tests.integration.test_compression_review_service import _service as compression_service
    from tests.unit.test_package_workflow import Repository

    old = tmp_path / "old"
    old.mkdir()
    service, _old_inputs, extractor = _service(old)
    inputs = summary_inputs(PortalOutputMode.COMBINED_PDF, maximum_file_bytes=600)
    compression = compression_service(inputs, Renderer())
    workflow = PackageWorkflowService(
        Repository(inputs.snapshot),
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=AS_OF,
        exporter=service,
        summary_builder=build_package_summary,
        compression=compression,
    )
    review = workflow.begin(inputs.snapshot, PortalOutputMode.COMBINED_PDF)
    # Simulate an older successful ordinary attempt on the exact same snapshot.
    service.remember_verified_export(inputs.snapshot, summary_outcome(inputs), old)
    saved_snapshot = service.last_verified_export_snapshot
    assert saved_snapshot is inputs.snapshot
    new = tmp_path / "new"
    new.mkdir()
    state = workflow.prepare_compression(
        new,
        review_id=review.review_id,
        color_choice=TargetSizeColorChoice.PRESERVE,
        signed_source_confirmed=True,
        human_review_confirmed=True,
    )
    cleared_snapshot = service.last_verified_export_snapshot
    assert cleared_snapshot is None
    for page in state.affected_pages:
        workflow.preview_compression_page(state.review_id, page.output_key, page.page_index)
    real_close = PreparedTargetSizePackage.close
    failures = 0

    def fail_first_close(prepared: PreparedTargetSizePackage) -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("PRIVATE cleanup failure")
        real_close(prepared)

    monkeypatch.setattr(PreparedTargetSizePackage, "close", fail_first_close)
    result = workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert result.status is PackageSummaryStatus.COMMITTED
    with pytest.raises(TextCompanionError) as caught:
        workflow.save_text_companion(new / "text.txt", privacy_confirmed=True)
    assert caught.value.code is TextCompanionErrorCode.EXPORT_REQUIRED and not extractor.calls
    workflow.cancel()


def test_reset_listener_invalidates_external_deletion_authority_even_for_rejected_receipt(
    tmp_path: Path,
) -> None:
    from paperworkshell.storage.export_destination import ExportCommitState

    service, inputs, _extractor = _service(tmp_path)
    resets: list[str] = []
    service._verified_export_reset = lambda: resets.append("cleared")
    service.clear_verified_export()
    assert resets == ["cleared"] and service.last_verified_export_snapshot is None
    service.remember_verified_export(inputs.snapshot, summary_outcome(inputs), tmp_path)
    assert resets == ["cleared", "cleared"]
    service.remember_verified_export(
        inputs.snapshot, summary_outcome(inputs, (ExportCommitState.NOT_COMMITTED,)), tmp_path
    )
    assert resets == ["cleared", "cleared", "cleared"]
    assert service.last_verified_export_snapshot is None and not service.preserved_export_artifacts
