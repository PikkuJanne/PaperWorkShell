from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.package_workflow import (
    PackageWorkflowError,
    PackageWorkflowErrorCode,
    PackageWorkflowService,
)
from paperworkshell.domain.checklist import ChecklistDefinition
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.export.package_summary import build_package_summary
from paperworkshell.preflight.engine import PreflightEvaluation
from paperworkshell.preflight.package_planner import PackageExportPlan
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateSnapshot
from tests.helpers.package_pilot import AS_OF, CASE_ID, pilot_document, pilot_snapshot
from tests.unit.test_organizer_backend import CASE_ID as ORGANIZER_CASE_ID
from tests.unit.test_organizer_backend import _Lifecycle, _Repository, _SourceIngest, _Thumbnails


class Repository:
    def __init__(self, snapshot: ManifestSnapshot) -> None:
        self.snapshot = snapshot
        self.saves = 0

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, self.snapshot)

    def save(
        self,
        case_id: UUID,
        document: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == CASE_ID
        assert (expected_revision, expected_sha256, expected_cas_token) == (
            self.snapshot.revision,
            self.snapshot.sha256,
            self.snapshot.cas_token,
        )
        self.snapshot = pilot_snapshot(dict(document))
        self.saves += 1
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.snapshot)


class Exporter:
    def __init__(self) -> None:
        self.calls = 0
        self.saved = 0
        self.save_result = "committed"
        self.save_cancelled = False
        self.snapshot: ManifestSnapshot | None = None

    def export(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        plan: PackageExportPlan,
        destination_parent: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
        human_review_confirmed: bool = False,
    ) -> PackageSummary:
        assert human_review_confirmed is True
        assert not deadline() and not cancellation()
        self.calls += 1
        self.snapshot = snapshot
        progress(4, 4)
        return replace(
            build_package_summary(snapshot, template, definition, preflight, plan=plan),
            status=PackageSummaryStatus.NOT_COMMITTED,
        )

    def save_summary(
        self,
        summary: PackageSummary,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> str:
        self.saved += 1
        self.save_cancelled = cancellation()
        return self.save_result


def service(
    document: dict[str, object] | None = None,
) -> tuple[
    PackageWorkflowService,
    Repository,
    Exporter,
]:
    repository = Repository(pilot_snapshot(document))
    exporter = Exporter()
    workflow = PackageWorkflowService(
        repository,
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=AS_OF,
        exporter=exporter,
        summary_builder=build_package_summary,
    )
    return workflow, repository, exporter


@pytest.mark.parametrize("mode", tuple(PortalOutputMode))
def test_complete_review_has_exact_names_counts_and_unverified_checks(
    mode: PortalOutputMode,
) -> None:
    workflow, repository, _ = service()
    review = workflow.begin(repository.snapshot, mode)
    assert review.export_allowed and review.human_review_required
    assert not review.signed_source_confirmation_required
    assert review.summary.active_page_count == 4
    assert review.summary.file_count == (4 if mode is PortalOutputMode.SEPARATE_PDFS else 1)
    rendered = review.summary.to_plain_text()
    assert "PRIVATE-PILOT-SOURCE" not in rendered
    assert "unverified" in rendered.lower()
    assert "current application instructions" in rendered
    assert "PRIVATE" not in repr(review)


def test_missing_case_remains_reviewable_but_cannot_export(tmp_path: Path) -> None:
    workflow, repository, exporter = service(pilot_document(missing=True))
    review = workflow.begin(repository.snapshot)
    assert not review.export_allowed
    assert review.summary.unassigned_page_count == 1
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.BLOCKED
    assert exporter.calls == 0


def test_explicit_ack_and_saved_authority_are_required(tmp_path: Path) -> None:
    workflow, repository, exporter = service()
    review = workflow.begin(repository.snapshot)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=False,
        )
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_REQUIRED
    document = repository.snapshot.document
    document["revision"] = repository.snapshot.revision + 1
    repository.snapshot = pilot_snapshot(document)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.STALE
    assert exporter.calls == 0


def test_cancel_is_nonmutating_and_mode_refresh_has_new_review(tmp_path: Path) -> None:
    workflow, repository, exporter = service()
    first = workflow.begin(repository.snapshot)
    second = workflow.refresh(PortalOutputMode.SEPARATE_PDFS)
    assert first.review_id != second.review_id
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=second.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
            cancellation=lambda: True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.CANCELLED
    workflow.cancel()
    assert not workflow.active and repository.saves == exporter.calls == 0


def test_actual_result_retained_until_explicit_summary_save_or_close(tmp_path: Path) -> None:
    workflow, repository, exporter = service()
    review = workflow.begin(repository.snapshot)
    result = workflow.export(
        tmp_path,
        review_id=review.review_id,
        signed_source_confirmed=False,
        human_review_confirmed=True,
    )
    assert result.status is PackageSummaryStatus.NOT_COMMITTED
    workflow.save_summary(tmp_path / "review.txt")
    assert exporter.saved == 1
    with pytest.raises(PackageWorkflowError):
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
    workflow.cancel()
    with pytest.raises(PackageWorkflowError):
        workflow.save_summary(tmp_path / "review.txt")


@pytest.mark.parametrize("signature", ["unknown", "markers_detected", "not_checked"])
def test_signed_source_ack_is_durable_before_export_and_rebinds_plan(
    tmp_path: Path,
    signature: str,
) -> None:
    workflow, repository, exporter = service(pilot_document(signature=signature))
    state = workflow.begin(repository.snapshot)
    assert state.signed_source_confirmation_required
    with pytest.raises(PackageWorkflowError):
        workflow.export(
            tmp_path,
            review_id=state.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
    workflow.export(
        tmp_path,
        review_id=state.review_id,
        signed_source_confirmed=True,
        human_review_confirmed=True,
    )
    assert repository.saves == 1 and exporter.calls == 1
    assert exporter.snapshot is not None and exporter.snapshot.revision == 8
    confirmations = cast(list[dict[str, object]], repository.snapshot.document["confirmations"])
    assert any(
        item["confirmation_code"] == "signed-source-unsigned-derivative" for item in confirmations
    )


@pytest.mark.parametrize("state", ["destination_exists", "unknown", "not_committed"])
def test_failed_summary_save_is_not_reported_as_saved(tmp_path: Path, state: str) -> None:
    workflow, repository, exporter = service()
    review = workflow.begin(repository.snapshot)
    workflow.export(
        tmp_path,
        review_id=review.review_id,
        signed_source_confirmed=False,
        human_review_confirmed=True,
    )
    exporter.save_result = state
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.save_summary(tmp_path / "review.txt")
    assert caught.value.code is PackageWorkflowErrorCode.SAVE_FAILED


def test_mode_refresh_rejects_old_displayed_review_acknowledgement(tmp_path: Path) -> None:
    workflow, repository, exporter = service()
    first = workflow.begin(repository.snapshot)
    workflow.refresh(PortalOutputMode.ZIP)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=first.review_id,
            signed_source_confirmed=False,
            human_review_confirmed=True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.STALE
    assert exporter.calls == 0


def test_organizer_resumes_autosave_after_signed_consent_and_export(tmp_path: Path) -> None:
    document = pilot_document(signature="unknown")
    document["case_id"] = str(ORGANIZER_CASE_ID)
    repository = _Repository(document)
    exporter = Exporter()
    workflow = PackageWorkflowService(
        repository,
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=AS_OF,
        exporter=exporter,
        summary_builder=build_package_summary,
    )
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        package_workflow=workflow,
    )
    backend.open_case(ORGANIZER_CASE_ID)
    page = backend.state.pages[0].page_id
    backend.rotate_clockwise(page)
    backend.flush_autosave()
    review = backend.begin_package_review()
    opening_revision = repository.current.revision
    with pytest.raises(PackageWorkflowError):
        backend.rotate_clockwise(page)
    backend.export_package(
        tmp_path,
        review_id=review.review_id,
        signed_source_confirmed=True,
        human_review_confirmed=True,
    )
    backend.cancel_package_review()
    assert backend.state.revision == opening_revision + 1
    backend.rotate_clockwise(page)
    backend.flush_autosave()
    assert backend.state.revision == opening_revision + 2
    assert len(cast(list[object], repository.current.document["confirmations"])) == 5
    backend.shutdown()


def test_concurrent_change_after_consent_is_not_silently_exported(tmp_path: Path) -> None:
    workflow, repository, exporter = service(pilot_document(signature="unknown"))
    review = workflow.begin(repository.snapshot)

    def change_after_save(saved: ManifestSnapshot) -> None:
        document = saved.document
        document["revision"] = saved.revision + 1
        repository.snapshot = pilot_snapshot(document)

    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=True,
            human_review_confirmed=True,
            snapshot_changed=change_after_save,
        )
    assert caught.value.code is PackageWorkflowErrorCode.STALE
    assert exporter.calls == 0 and repository.saves == 1
    assert workflow.current_snapshot is not None and workflow.current_snapshot.revision == 8


def test_saved_combined_names_have_actionable_mode_change_recovery() -> None:
    document = pilot_document()
    constraints = cast(dict[str, object], document["constraints"])
    constraints["requested_output_names"] = ["my-packet.pdf"]
    workflow, repository, _ = service(document)
    workflow.begin(repository.snapshot)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.refresh(PortalOutputMode.SEPARATE_PDFS)
    assert caught.value.code is PackageWorkflowErrorCode.FILENAME_MODE_MISMATCH
    assert "Constraints" in caught.value.safe_message
    assert workflow.refresh(PortalOutputMode.COMBINED_PDF).export_allowed


def test_summary_save_forwards_cancellation_without_losing_package_inventory(
    tmp_path: Path,
) -> None:
    workflow, repository, exporter = service()
    review = workflow.begin(repository.snapshot)
    workflow.export(
        tmp_path,
        review_id=review.review_id,
        signed_source_confirmed=False,
        human_review_confirmed=True,
    )
    exporter.save_result = "not_committed"
    with pytest.raises(PackageWorkflowError):
        workflow.save_summary(tmp_path / "review.txt", cancellation=lambda: True)
    assert exporter.save_cancelled and workflow.active
    exporter.save_result = "committed"
    workflow.save_summary(tmp_path / "second-review.txt")
    assert exporter.saved == 2 and not exporter.save_cancelled
