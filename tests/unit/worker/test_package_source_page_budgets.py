"""Export page subsets retain source parsing limits without raising policy caps."""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportProgress,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfSourceMediaType,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
    WorkerOperationSnapshot,
    WorkerProgressSnapshot,
)
from paperworkshell.worker.flattened_pdf_export_broker import (
    BrokeredFlattenedPdfExporter,
)
from paperworkshell.worker.flattened_pdf_export_broker import (
    _operation_limits as flattened_operation_limits,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import (
    BrokeredPdfExporter,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_export_broker import (
    _operation_limits as pdf_operation_limits,
)
from paperworkshell.worker.protocol.models import (
    PDF_EXPORT_CAPABILITY,
    OperationOutcome,
    ProgressStage,
    ProgressUnit,
)
from paperworkshell.worker.searchable_pdf_export_broker import (
    BrokeredSearchablePdfExporter,
)
from paperworkshell.worker.searchable_pdf_export_broker import (
    _operation_limits as searchable_operation_limits,
)
from tests.unit.worker.flattened_pdf_export_fixtures import synthetic_flattened_plan
from tests.unit.worker.test_flattened_pdf_export_broker import _policy as flattened_policy
from tests.unit.worker.test_flattened_pdf_export_broker import _snapshot
from tests.unit.worker.test_pdf_export_broker import _plan as pdf_plan
from tests.unit.worker.test_pdf_export_broker import _policy as pdf_policy
from tests.unit.worker.test_searchable_pdf_export_broker import _plan as searchable_plan
from tests.unit.worker.test_searchable_pdf_export_broker import _policy as searchable_policy


@pytest.mark.parametrize("admitted", [True, False])
def test_flattened_subset_inspection_capacity_covers_full_source_within_policy(
    admitted: bool,
) -> None:
    plan = synthetic_flattened_plan()
    plan = FlattenedPdfExportPlan(
        base_plan=PdfExportPlan(
            case_id=plan.base_plan.case_id,
            manifest_revision=plan.base_plan.manifest_revision,
            manifest_sha256=plan.base_plan.manifest_sha256,
            manifest_cas_token=plan.base_plan.manifest_cas_token,
            sources=tuple(replace(source, page_count=7) for source in plan.base_plan.sources),
            pages=plan.base_plan.pages,
        ),
        pages=plan.pages,
        assets=plan.assets,
    )
    limits = replace(flattened_policy().limits, page_count=7 if admitted else 6)
    assert plan.page_count == 1
    if admitted:
        operation = flattened_operation_limits(limits, plan, (), plan_byte_size=100)
        assert operation.page_count == 7
        assert operation.memory_bytes == limits.memory_bytes
        assert operation.temporary_storage_bytes == limits.temporary_storage_bytes
    else:
        with pytest.raises(FlattenedPdfExportError) as caught:
            flattened_operation_limits(limits, plan, (), plan_byte_size=100)
        assert caught.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED


@pytest.mark.parametrize("admitted", [True, False])
def test_searchable_subset_inspection_capacity_covers_full_source_within_policy(
    admitted: bool,
) -> None:
    plan = searchable_plan()
    plan = SearchablePdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=tuple(
            replace(source, page_count=7)
            if source.media_type is SearchablePdfSourceMediaType.PDF
            else source
            for source in plan.sources
        ),
        pages=plan.pages,
    )
    policy = searchable_policy(page_count=7 if admitted else 6)
    assert plan.page_count == 2
    if admitted:
        operation = searchable_operation_limits(policy, plan)
        assert operation.page_count == 7
        assert operation.memory_bytes == policy.limits.memory_bytes
        assert operation.temporary_storage_bytes == policy.limits.temporary_storage_bytes
    else:
        with pytest.raises(PdfExportError) as caught:
            searchable_operation_limits(policy, plan)
        assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED


@pytest.mark.parametrize("admitted", [True, False])
def test_structural_subset_inspection_capacity_covers_full_source_within_policy(
    admitted: bool,
) -> None:
    plan = pdf_plan()
    plan = PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=(replace(plan.sources[0], page_count=7), *plan.sources[1:]),
        pages=plan.pages,
    )
    limits = pdf_policy(page_count=7 if admitted else 6).limits
    assert plan.page_count == 2
    if admitted:
        operation = pdf_operation_limits(limits, plan)
        assert operation.page_count == 7
        assert operation.memory_bytes == limits.memory_bytes
        assert operation.temporary_storage_bytes == limits.temporary_storage_bytes
    else:
        with pytest.raises(PdfExportError) as caught:
            pdf_operation_limits(limits, plan)
        assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED


class _ProgressWorker:
    def __init__(self, snapshots: tuple[WorkerClientSnapshot, ...]) -> None:
        self.snapshots = iter(snapshots)

    def query(self) -> WorkerClientSnapshot:
        return next(self.snapshots)

    def pump(self, timeout_seconds: float = 0.0) -> None:
        assert timeout_seconds == 0.05


@pytest.mark.parametrize("pipeline", ["structural", "flattened", "searchable"])
@pytest.mark.parametrize("invalid", [None, "total"])
def test_source_capacity_progress_is_bound_and_mapped_to_output_pages(
    pipeline: str, invalid: str | None
) -> None:
    operation_id, case_id = uuid4(), uuid4()
    result = WorkerOperationResultSnapshot(1, operation_id, OperationOutcome.SUCCEEDED, (), None)
    snapshots = tuple(
        _snapshot(
            generation=1,
            phase=WorkerLifecyclePhase.BUSY,
            case_id=case_id,
            capabilities=(PDF_EXPORT_CAPABILITY,),
            active=WorkerOperationSnapshot(
                operation_id,
                uuid4(),
                PDF_EXPORT_CAPABILITY.capability_id,
                PDF_EXPORT_CAPABILITY.capability_version,
                True,
                False,
                WorkerProgressSnapshot(
                    ProgressStage.PROCESSING,
                    completed,
                    8 if invalid == "total" else 7,
                    ProgressUnit.PAGES,
                ),
            ),
        )
        for completed in (0, 3, 7)
    )
    final = _snapshot(
        generation=1,
        phase=WorkerLifecyclePhase.BUSY,
        case_id=case_id,
        capabilities=(PDF_EXPORT_CAPABILITY,),
        result=result,
    )
    worker = cast(PdfExportWorkerCoordinator, _ProgressWorker((*snapshots, final)))
    exporter = (
        BrokeredFlattenedPdfExporter(
            worker, flattened_policy(), heavy_operations=HeavyOperationCoordinator(1)
        )
        if pipeline == "flattened"
        else BrokeredSearchablePdfExporter(
            worker, searchable_policy(), heavy_operations=HeavyOperationCoordinator(1)
        )
        if pipeline == "searchable"
        else BrokeredPdfExporter(
            worker, pdf_policy(), heavy_operations=HeavyOperationCoordinator(1)
        )
    )
    progress: list[PdfExportProgress] = []

    def wait() -> WorkerOperationResultSnapshot:
        return exporter._wait_result(
            operation_id,
            1,
            page_count=3,
            worker_page_capacity=7,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=progress.append,
        )

    if invalid is None:
        assert wait() is result
        assert [(item.completed_pages, item.total_pages) for item in progress] == [
            (0, 3),
            (1, 3),
            (3, 3),
        ]
    else:
        with pytest.raises((PdfExportError, FlattenedPdfExportError)) as caught:
            wait()
        assert isinstance(caught.value, (PdfExportError, FlattenedPdfExportError))
        assert caught.value.code.value.endswith("REPORT_INVALID")
