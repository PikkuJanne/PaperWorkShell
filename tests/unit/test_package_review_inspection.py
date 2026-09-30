"""Fresh, bounded, source-bound inspection in the normal package review use case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.package_workflow import (
    PackageWorkflowError,
    PackageWorkflowErrorCode,
    PackageWorkflowService,
    evaluate_package_review,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.export.package_summary import build_package_summary
from paperworkshell.preflight.facts import SourceInspectionsPreflightFacts
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from tests.helpers.package_pilot import AS_OF, SOURCE_ID, pilot_document, pilot_snapshot
from tests.unit.test_package_workflow import Exporter, Repository


def _result(**features: PdfFeatureState) -> PdfInspectionResult:
    return PdfInspectionResult(
        PdfOpenState.OPENED,
        4,
        20,
        PdfEncryptionState.UNENCRYPTED,
        PdfStructuralState.CLEAN,
        replace(PdfFeatureSummary(*(PdfFeatureState.NOT_DETECTED for _ in range(9))), **features),
    )


class Inspector:
    def __init__(self, result: PdfInspectionResult | Exception | None = None) -> None:
        self.result = _result() if result is None else result
        self.calls: list[tuple[ManifestSnapshot, UUID]] = []
        self.deadlines: list[Callable[[], bool]] = []
        self.hook: Callable[[], None] = lambda: None

    def inspect_committed(
        self,
        snapshot: ManifestSnapshot,
        source_id: UUID,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> PdfInspectionResult:
        assert not deadline() and not cancellation()
        self.calls.append((snapshot, source_id))
        self.deadlines.append(deadline)
        self.hook()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _service(
    inspector: Inspector, document: dict[str, object] | None = None
) -> tuple[PackageWorkflowService, Repository, Exporter]:
    repository = Repository(pilot_snapshot(document))
    exporter = Exporter()
    return (
        PackageWorkflowService(
            repository,
            SafeTemplateLoader(built_in_template_repository()),
            GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
            as_of=AS_OF,
            exporter=exporter,
            summary_builder=build_package_summary,
            source_inspector=inspector,
        ),
        repository,
        exporter,
    )


def test_all_pdfs_are_inspected_sequentially_under_one_deadline_and_images_are_not() -> None:
    document = pilot_document()
    sources = cast(list[dict[str, object]], document["sources"])
    second_id = UUID("65010000-0000-4000-8000-000000000002")
    image_id = UUID("65010000-0000-4000-8000-000000000003")
    sources.append({**sources[0], "source_id": str(second_id), "storage_name": f"{second_id}.bin"})
    sources.append(
        {
            **sources[0],
            "source_id": str(image_id),
            "storage_name": f"{image_id}.bin",
            "detected_media_type": "image/png",
            "signature_state": "not_applicable",
            "encryption_state": "not_applicable",
            "page_count": 1,
        }
    )
    inspector = Inspector(_result(metadata=PdfFeatureState.DETECTED))
    workflow, repository, exporter = _service(inspector, document)
    progress: list[tuple[int, int]] = []
    review = workflow.begin(repository.snapshot, progress=lambda a, b: progress.append((a, b)))

    assert [source for _, source in inspector.calls] == [SOURCE_ID, second_id]
    assert all(snapshot is repository.snapshot for snapshot, _ in inspector.calls)
    assert inspector.deadlines[0] is inspector.deadlines[1]
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert "contains metadata" in review.summary.to_plain_text()
    assert "inspection is unavailable" not in review.summary.to_plain_text()
    assert repository.saves == exporter.calls == 0
    assert "PRIVATE" not in repr(review)


def test_refresh_performs_new_inspection_and_failed_refresh_cannot_reuse_old_export_permission(
    tmp_path: Path,
) -> None:
    inspector = Inspector()
    workflow, repository, exporter = _service(inspector)
    first = workflow.begin(repository.snapshot)
    assert first.export_allowed
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.refresh(PortalOutputMode.ZIP, cancellation=lambda: True)
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_CANCELLED
    with pytest.raises(PackageWorkflowError) as blocked:
        workflow.export(
            tmp_path,
            review_id=first.review_id,
            signed_source_confirmed=True,
            human_review_confirmed=True,
        )
    assert blocked.value.code is PackageWorkflowErrorCode.REVIEW_REQUIRED
    assert workflow._active is None
    inspector.result = _result(interactive_content=PdfFeatureState.DETECTED)
    second = workflow.refresh(PortalOutputMode.ZIP)
    assert len(inspector.calls) == 2
    assert not second.export_allowed and second.review_id != first.review_id
    assert "interactive content" in second.summary.to_plain_text()
    assert exporter.calls == 0


@pytest.mark.parametrize(
    "failure",
    [
        PdfInspectionError(PdfInspectionErrorCode.WORKER_FAILED, "inspection.worker"),
        SourceIngestError(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "inspection.source"),
    ],
)
def test_failed_inspection_is_explicitly_unavailable_and_private_errors_are_not_logged(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure.args = ("PRIVATE-FIXTURE-PATH-METADATA-VALUE",)
    workflow, repository, _ = _service(Inspector(failure))
    review = workflow.begin(repository.snapshot)
    assert "inspection is unavailable" in review.summary.to_plain_text()
    assert "PRIVATE-FIXTURE" not in review.summary.to_plain_text() + repr(review) + caplog.text


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            PdfInspectionError(PdfInspectionErrorCode.CANCELLED, "inspection.worker"),
            PackageWorkflowErrorCode.REVIEW_CANCELLED,
        ),
        (
            PdfInspectionError(PdfInspectionErrorCode.TIMED_OUT, "inspection.worker"),
            PackageWorkflowErrorCode.REVIEW_TIMED_OUT,
        ),
        (
            PdfInspectionError(PdfInspectionErrorCode.SOURCE_CHANGED, "inspection.worker"),
            PackageWorkflowErrorCode.STALE,
        ),
        (
            SourceIngestError(SourceIngestErrorCode.CANCELLED, "inspection.source"),
            PackageWorkflowErrorCode.REVIEW_CANCELLED,
        ),
        (
            SourceIngestError(SourceIngestErrorCode.TIMED_OUT, "inspection.source"),
            PackageWorkflowErrorCode.REVIEW_TIMED_OUT,
        ),
        (
            SourceIngestError(SourceIngestErrorCode.MANIFEST_CONFLICT, "inspection.source"),
            PackageWorkflowErrorCode.STALE,
        ),
    ],
)
def test_cancel_timeout_and_changed_source_abort_without_publishing_a_review(
    failure: Exception,
    expected: PackageWorkflowErrorCode,
) -> None:
    workflow, repository, exporter = _service(Inspector(failure))
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.begin(repository.snapshot)
    assert caught.value.code is expected
    assert not workflow.active and repository.saves == exporter.calls == 0


def test_deadline_is_checked_after_worker_and_prevents_publishing_late_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr("paperworkshell.app.package_workflow.monotonic", lambda: clock[0])
    inspector = Inspector()
    inspector.hook = lambda: clock.__setitem__(0, 401.0)
    workflow, repository, _ = _service(inspector)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.begin(repository.snapshot)
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_TIMED_OUT
    assert not workflow.active


def test_manifest_change_during_inspection_prevents_publishing_a_stale_review() -> None:
    inspector = Inspector()
    workflow, repository, _ = _service(inspector)

    def change_manifest() -> None:
        document = repository.snapshot.document
        document["revision"] = repository.snapshot.revision + 1
        repository.snapshot = pilot_snapshot(document)

    inspector.hook = change_manifest
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.begin(repository.snapshot)
    assert caught.value.code is PackageWorkflowErrorCode.STALE
    assert not workflow.active


def test_consent_reuses_source_bound_facts_and_cannot_bypass_interactive_blocker(
    tmp_path: Path,
) -> None:
    inspector = Inspector(_result(signature_markers=PdfFeatureState.DETECTED))
    workflow, repository, exporter = _service(
        inspector, pilot_document(signature="markers_detected")
    )
    review = workflow.begin(repository.snapshot)
    workflow.export(
        tmp_path,
        review_id=review.review_id,
        signed_source_confirmed=True,
        human_review_confirmed=True,
    )
    assert len(inspector.calls) == 1
    assert repository.saves == exporter.calls == 1
    workflow.cancel()
    inspector.result = _result(
        signature_markers=PdfFeatureState.DETECTED, interactive_content=PdfFeatureState.DETECTED
    )
    blocked = workflow.begin(repository.snapshot)
    assert not blocked.export_allowed
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=blocked.review_id,
            signed_source_confirmed=True,
            human_review_confirmed=True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.BLOCKED
    assert exporter.calls == 1


def test_inspection_facts_cannot_be_rebound_to_another_source_hash() -> None:
    workflow, repository, _ = _service(Inspector())
    workflow.begin(repository.snapshot)
    review = workflow._active
    assert review is not None
    changed = replace(review.inspections.inspections[0], source_sha256="b" * 64)
    with pytest.raises(PackageWorkflowError) as caught:
        evaluate_package_review(
            repository.snapshot,
            review.template,
            review.definition,
            review.state.selected_mode,
            review.plan,
            inspections=SourceInspectionsPreflightFacts(
                repository.snapshot.case_id, repository.snapshot.revision, (changed,)
            ),
        )
    assert caught.value.code is PackageWorkflowErrorCode.STALE


@pytest.mark.parametrize("fresh_signature", [PdfFeatureState.DETECTED, PdfFeatureState.UNKNOWN])
def test_fresh_signature_risk_missing_from_saved_record_blocks_acknowledgement_bypass(
    fresh_signature: PdfFeatureState,
    tmp_path: Path,
) -> None:
    workflow, repository, exporter = _service(Inspector(_result(signature_markers=fresh_signature)))
    review = workflow.begin(repository.snapshot)
    assert not review.export_allowed
    assert (
        "signature risk that the saved source record does not cover"
        in review.summary.to_plain_text()
    )
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.export(
            tmp_path,
            review_id=review.review_id,
            signed_source_confirmed=True,
            human_review_confirmed=True,
        )
    assert caught.value.code is PackageWorkflowErrorCode.BLOCKED
    assert repository.saves == exporter.calls == 0


def test_cancellation_after_the_last_worker_result_never_publishes_a_review() -> None:
    cancelled = [False]
    inspector = Inspector()
    inspector.hook = lambda: cancelled.__setitem__(0, True)
    workflow, repository, _ = _service(inspector)
    with pytest.raises(PackageWorkflowError) as caught:
        workflow.begin(repository.snapshot, cancellation=lambda: cancelled[0])
    assert caught.value.code is PackageWorkflowErrorCode.REVIEW_CANCELLED
    assert not workflow.active
