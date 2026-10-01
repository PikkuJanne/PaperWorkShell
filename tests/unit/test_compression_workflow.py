"""Review authority, consent, and cancellation before publishing private bytes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from paperworkshell.app.package_workflow import (
    PackageWorkflowError,
    PackageWorkflowErrorCode,
    PackageWorkflowService,
)
from paperworkshell.domain.checklist import ChecklistDefinition
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailSnapshot
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
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
from tests.helpers.compression_review import compression_review
from tests.helpers.package_pilot import AS_OF, pilot_document, pilot_snapshot
from tests.unit.test_package_workflow import Exporter, Repository


class Compression:
    def __init__(self) -> None:
        self.state: CompressionReviewState | None = None
        self.discards = 0
        self.commits = 0
        self.snapshot: ManifestSnapshot | None = None
        self.after_prepare: Callable[[], None] = lambda: None
        self.after_preview: Callable[[], None] = lambda: None
        self.fail_commit = False
        self.fail_discard = False

    def prepare(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        plan: PackageExportPlan,
        destination_parent: Path,
        *,
        color_choice: TargetSizeColorChoice,
        human_review_confirmed: bool,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> CompressionReviewState:
        assert human_review_confirmed and not deadline() and not cancellation()
        self.snapshot = snapshot
        base = compression_review(grayscale=color_choice is TargetSizeColorChoice.ALLOW_GRAYSCALE)
        self.state = replace(
            base,
            review_id=uuid4(),
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            affected_pages=tuple(
                replace(page, page_id=plan.pdfs[0].page_ids[page.page_index])
                for page in base.affected_pages
            ),
        )
        progress(1, 1)
        self.after_prepare()
        return self.state

    def preview_page(
        self,
        review_id: UUID,
        output_key: str,
        page_index: int,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> CompressionPagePreview:
        assert self.state is not None and not deadline() and not cancellation()
        page = next(page for page in self.state.affected_pages if page.page_index == page_index)
        pixels = PdfThumbnailSnapshot(
            self.state.case_id,
            page.page_id,
            2,
            2,
            8,
            b"\xff" * 16,
            PdfRenderRotation.DEGREES_0,
            PdfRenderRotation.DEGREES_0,
        )
        self.after_preview()
        return CompressionPagePreview(review_id, output_key, page_index, pixels, pixels)

    def commit(
        self,
        review_id: UUID,
        snapshot: ManifestSnapshot,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
    ) -> PackageSummary:
        assert self.state is not None and review_id == self.state.review_id
        assert snapshot == self.snapshot and not deadline() and not cancellation()
        self.commits += 1
        self.state = None
        if self.fail_commit:
            raise RuntimeError("PRIVATE failed publication")
        return PackageSummary(PackageSummaryStatus.NOT_COMMITTED)

    def discard(self) -> None:
        self.discards += 1
        if self.fail_discard:
            raise RuntimeError("PRIVATE cleanup failure")
        self.state = None


def service(
    document: dict[str, object] | None = None,
) -> tuple[PackageWorkflowService, Repository, Compression]:
    repository = Repository(pilot_snapshot(document))
    compression = Compression()
    return (
        PackageWorkflowService(
            repository,
            SafeTemplateLoader(built_in_template_repository()),
            GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
            as_of=AS_OF,
            exporter=Exporter(),
            summary_builder=build_package_summary,
            compression=compression,
        ),
        repository,
        compression,
    )


def prepare(workflow: PackageWorkflowService, destination: Path) -> CompressionReviewState:
    return workflow.prepare_compression(
        destination,
        review_id=workflow.current_review.review_id,
        color_choice=TargetSizeColorChoice.PRESERVE,
        signed_source_confirmed=True,
        human_review_confirmed=True,
    )


def preview(workflow: PackageWorkflowService, state: CompressionReviewState) -> None:
    page = state.affected_pages[0]
    workflow.preview_compression_page(state.review_id, page.output_key, page.page_index)


def test_exact_candidate_requires_preview_and_explicit_ack(tmp_path: Path) -> None:
    workflow, repository, compression = service()
    assert workflow.begin(repository.snapshot).compression_allowed
    state = prepare(workflow, tmp_path)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_REQUIRED
    preview(workflow, state)
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(state.review_id, human_review_confirmed=False)
    result = workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert result.status is PackageSummaryStatus.NOT_COMMITTED and compression.commits == 1
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(state.review_id, human_review_confirmed=True)


def test_signed_consent_rebinds_revision_before_prepare_and_reprepare(tmp_path: Path) -> None:
    workflow, repository, compression = service(pilot_document(signature="unknown"))
    review = workflow.begin(repository.snapshot)
    before = repository.snapshot.revision
    first = prepare(workflow, tmp_path)
    assert repository.saves == 1 and first.manifest_revision == before + 1
    assert compression.snapshot == repository.snapshot
    assert workflow.current_review.review_id == review.review_id
    preview(workflow, first)
    second = prepare(workflow, tmp_path)
    assert second.review_id != first.review_id and repository.saves == 1
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(second.review_id, human_review_confirmed=True)


def test_consent_saved_before_failed_refresh_keeps_only_saved_recovery_authority(
    tmp_path: Path,
) -> None:
    workflow, repository, compression = service(pilot_document(signature="unknown"))
    review = workflow.begin(repository.snapshot)

    def failure(_snapshot: ManifestSnapshot) -> None:
        raise RuntimeError("PRIVATE presentation failure")

    with pytest.raises(RuntimeError):
        workflow.prepare_compression(
            tmp_path,
            review_id=review.review_id,
            color_choice=TargetSizeColorChoice.PRESERVE,
            signed_source_confirmed=True,
            human_review_confirmed=True,
            snapshot_changed=failure,
        )
    assert repository.saves == 1 and compression.snapshot is None
    assert workflow.current_snapshot == repository.snapshot
    with pytest.raises(PackageWorkflowError) as caught:
        _ = workflow.current_review
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_REQUIRED
    restored = workflow.refresh(review.selected_mode)
    assert restored.review_id != review.review_id
    assert not restored.signed_source_confirmation_required
    prepare(workflow, tmp_path)
    assert repository.saves == 1


@pytest.mark.parametrize("phase", ["prepare", "preview", "commit"])
def test_stale_case_never_authorizes_publication(tmp_path: Path, phase: str) -> None:
    workflow, repository, compression = service()
    review = workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    preview(workflow, state)
    document = repository.snapshot.document
    document["revision"] = repository.snapshot.revision + 1
    repository.snapshot = pilot_snapshot(document)
    with pytest.raises(PackageWorkflowError) as caught:
        if phase == "prepare":
            workflow.prepare_compression(
                tmp_path,
                review_id=review.review_id,
                color_choice=TargetSizeColorChoice.PRESERVE,
                signed_source_confirmed=True,
                human_review_confirmed=True,
            )
        elif phase == "preview":
            preview(workflow, state)
        else:
            workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert caught.value.code is PackageWorkflowErrorCode.STALE
    assert compression.state is None and compression.commits == 0


@pytest.mark.parametrize("phase", ["prepare", "preview"])
def test_late_cancellation_cannot_create_review_proof(tmp_path: Path, phase: str) -> None:
    workflow, repository, compression = service()
    review = workflow.begin(repository.snapshot)
    cancelled = False

    def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    if phase == "prepare":
        compression.after_prepare = cancel
        with pytest.raises(PackageWorkflowError) as caught:
            workflow.prepare_compression(
                tmp_path,
                review_id=review.review_id,
                color_choice=TargetSizeColorChoice.PRESERVE,
                signed_source_confirmed=True,
                human_review_confirmed=True,
                cancellation=lambda: cancelled,
            )
        assert caught.value.code is PackageWorkflowErrorCode.CANCELLED
        assert compression.state is None
    else:
        state = prepare(workflow, tmp_path)
        compression.after_preview = cancel
        page = state.affected_pages[0]
        with pytest.raises(PackageWorkflowError) as caught:
            workflow.preview_compression_page(
                state.review_id,
                page.output_key,
                page.page_index,
                cancellation=lambda: cancelled,
            )
        assert caught.value.code is PackageWorkflowErrorCode.CANCELLED
        with pytest.raises(PackageWorkflowError):
            workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert compression.commits == 0


def test_forged_page_and_wrong_review_are_rejected(tmp_path: Path) -> None:
    workflow, repository, _ = service()
    workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    for key, index in [("unknown", 0), ("output-one", -1), ("output-one", True)]:
        with pytest.raises(PackageWorkflowError):
            workflow.preview_compression_page(state.review_id, key, index)
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(uuid4(), human_review_confirmed=True)


@pytest.mark.parametrize("action", ["refresh", "cancel", "discard"])
def test_leaving_review_discards_private_candidate(tmp_path: Path, action: str) -> None:
    workflow, repository, compression = service()
    workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    if action == "refresh":
        workflow.refresh(PortalOutputMode.SEPARATE_PDFS)
    elif action == "cancel":
        workflow.cancel()
    else:
        workflow.discard_compression()
    assert compression.state is None
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(state.review_id, human_review_confirmed=True)


def test_failed_cleanup_retains_exclusive_review_for_retry(tmp_path: Path) -> None:
    workflow, repository, compression = service()
    workflow.begin(repository.snapshot)
    prepare(workflow, tmp_path)
    compression.fail_discard = True
    with pytest.raises(RuntimeError):
        workflow.cancel()
    assert workflow.active and compression.state is not None
    compression.fail_discard = False
    workflow.cancel()
    assert bool(workflow.active) is False
    assert compression.state is None


def test_failed_publication_consumes_authority(tmp_path: Path) -> None:
    workflow, repository, compression = service()
    workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    preview(workflow, state)
    compression.fail_commit = True
    with pytest.raises(RuntimeError):
        workflow.commit_compression(state.review_id, human_review_confirmed=True)
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(state.review_id, human_review_confirmed=True)
    assert compression.commits == 1


def test_unrelated_blocker_still_prevents_compression(tmp_path: Path) -> None:
    workflow, repository, compression = service(pilot_document(missing=True))
    assert not workflow.begin(repository.snapshot).compression_allowed
    with pytest.raises(PackageWorkflowError) as caught:
        prepare(workflow, tmp_path)
    assert caught.value.code is PackageWorkflowErrorCode.BLOCKED
    assert compression.snapshot is None


def test_small_configured_target_still_allows_preparation(tmp_path: Path) -> None:
    document = pilot_document()
    cast(dict[str, object], document["constraints"])["maximum_total_bytes"] = 1
    workflow, repository, compression = service(document)
    review = workflow.begin(repository.snapshot)
    assert review.compression_allowed
    # Preflight may not yet know actual bytes. The private writer measures them;
    # a size-only configured target must not suppress the preparation entry point.
    prepare(workflow, tmp_path)
    assert compression.snapshot is not None


def test_human_review_pause_does_not_reuse_processing_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperworkshell.app.package_workflow as module

    clock = 100.0
    monkeypatch.setattr(module, "monotonic", lambda: clock)
    workflow, repository, _ = service()
    workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    clock += 24 * 60 * 60
    preview(workflow, state)
    clock += 24 * 60 * 60
    assert (
        workflow.commit_compression(
            state.review_id,
            human_review_confirmed=True,
        ).status
        is PackageSummaryStatus.NOT_COMMITTED
    )


@pytest.mark.parametrize("invalid", [None, 0, ""])
def test_invalid_cancellation_cannot_persist_signed_consent(
    tmp_path: Path, invalid: object
) -> None:
    workflow, repository, compression = service(pilot_document(signature="unknown"))
    review = workflow.begin(repository.snapshot)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.prepare_compression(
            tmp_path,
            review_id=review.review_id,
            color_choice=TargetSizeColorChoice.PRESERVE,
            signed_source_confirmed=True,
            human_review_confirmed=True,
            cancellation=cast(Callable[[], bool], lambda: invalid),
        )
    assert caught.value.code is PackageWorkflowErrorCode.INVALID
    assert repository.saves == 0 and compression.snapshot is None


def test_invalid_cancellation_after_preview_cannot_create_proof(tmp_path: Path) -> None:
    workflow, repository, compression = service()
    workflow.begin(repository.snapshot)
    state = prepare(workflow, tmp_path)
    returned: object = False

    def invalidate() -> None:
        nonlocal returned
        returned = None

    compression.after_preview = invalidate
    page = state.affected_pages[0]
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.preview_compression_page(
            state.review_id,
            page.output_key,
            page.page_index,
            cancellation=cast(Callable[[], bool], lambda: returned),
        )
    assert caught.value.code is PackageWorkflowErrorCode.INVALID
    with pytest.raises(PackageWorkflowError):
        workflow.commit_compression(state.review_id, human_review_confirmed=True)
