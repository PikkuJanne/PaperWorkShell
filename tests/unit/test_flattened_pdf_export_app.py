"""Focused application-orchestration evidence for PWS-0307 flattened export."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.flattened_pdf_export import (
    FlattenedPdfDestination,
    FlattenedPdfExportBroker,
    FlattenedPdfExportProgressSink,
    FlattenedPdfExportService,
    FlattenedPdfHistoryRecorder,
    FlattenedPdfRasterizer,
    VerifiedFlattenedPdfCandidate,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryPlanBinding,
    PdfExportHistoryRecord,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PdfExportRetryRepository,
    PreparedPdfExportRetry,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterRequest,
)
from paperworkshell.domain.signed_source_policy import (
    SIGNED_SOURCE_CONFIRMATION_CODE,
    SignedSourcePolicyError,
    SignedSourcePolicyErrorCode,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportOutcome,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    build_flattened_pdf_export_plan,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset, OverlayRenderLimits
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource

CASE_ID = UUID("10000000-0000-4000-8000-000000000731")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000731")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000731")


def _asset_id(index: int) -> UUID:
    return UUID(f"40000000-0000-4000-8000-{730 + index:012d}")


ASSET_IDS = (_asset_id(1), _asset_id(2))
EXPORT_ID = UUID("70000000-0000-4000-8000-000000000731")
DESTINATION = Path("C:/synthetic/flattened-output.pdf")
PAYLOAD = b"%PDF-1.7\n% synthetic flattened output\n%%EOF\n"
RECORDED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _box(x: int, y: int, width: int, height: int) -> dict[str, object]:
    return {
        "coordinate_space": "normalized_page_millionths_v1",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _document(
    *,
    signature_assets: int = 0,
    signed_source: bool = False,
    confirmed: bool = True,
) -> dict[str, object]:
    assets: list[dict[str, object]] = []
    overlays: list[dict[str, object]] = [
        {
            "overlay_id": "50000000-0000-4000-8000-000000000731",
            "overlay_version": 1,
            "page_id": str(PAGE_ID),
            "box": _box(100_000, 100_000, 300_000, 100_000),
            "z_index": 1,
            "payload": {
                "kind": "text",
                "value": "PRIVATE SYNTHETIC FORM VALUE",
                "font_token": "paperworkshell-default",
                "font_size_points": 12,
                "color": "#000000ff",
            },
        }
    ]
    for index in range(1, signature_assets + 1):
        asset_id = _asset_id(index)
        assets.append(
            {
                "asset_id": str(asset_id),
                "kind": "signature_image",
                "storage_name": f"{asset_id}.bin",
                "media_type": "image/png",
                "byte_size": 67 + index,
                "sha256": f"{index % 16:x}" * 64,
            }
        )
        overlays.append(
            {
                "overlay_id": f"50000000-0000-4000-8000-{731 + index:012d}",
                "overlay_version": 1,
                "page_id": str(PAGE_ID),
                "box": _box(100_000 * (((index - 1) % 5) + 1), 300_000, 150_000, 100_000),
                "z_index": 1 + index,
                "payload": {
                    "kind": "signature_image",
                    "asset_id": str(asset_id),
                    "terminology": "visual_mark_not_digital_signature",
                },
            }
        )
    confirmations: list[dict[str, object]] = []
    if signed_source and confirmed:
        confirmations.append(
            {
                "confirmation_id": "60000000-0000-4000-8000-000000000731",
                "confirmation_version": 1,
                "confirmation_code": SIGNED_SOURCE_CONFIRMATION_CODE,
                "statement_version": 1,
                "state": "confirmed",
                "actor": "user",
                "scope": {"kind": "source", "source_id": str(SOURCE_ID)},
                "recorded_at": "2026-08-25T11:00:00Z",
            }
        )
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-25T10:00:00Z",
        "updated_at": "2026-08-25T11:00:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "PRIVATE SYNTHETIC SOURCE.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 731,
                "page_count": 1,
                "sha256": "a" * 64,
                "signature_state": ("markers_detected" if signed_source else "none_detected"),
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-25T10:00:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(PAGE_ID),
                "source_id": str(SOURCE_ID),
                "source_page_index": 0,
            }
        ],
        "page_sequence_base": [str(PAGE_ID)],
        "page_sequence": [str(PAGE_ID)],
        "page_operations": [],
        "assets": assets,
        "overlays": overlays,
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": confirmations,
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        CASE_ID,
        cast(int, selected["schema_version"]),
        cast(int, selected["revision"]),
        selected,
        "e" * 64,
        "flattened_app_cas_0001",
    )


class _Manifests:
    def __init__(self, snapshot: ManifestSnapshot) -> None:
        self.snapshot = snapshot
        self.error: BaseException | None = None
        self.calls = 0

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, self.snapshot)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        del case_id, candidate, expected_revision, expected_sha256, expected_cas_token
        raise AssertionError("flattened export must not save the manifest directly")


class _Batch:
    def __init__(self, plan: PdfExportPlan) -> None:
        self.case_id = plan.case_id
        self.manifest_revision = plan.manifest_revision
        self.manifest_sha256 = plan.manifest_sha256
        self.manifest_cas_token = plan.manifest_cas_token
        self.sources = ()
        self.closed = False
        self.revalidated = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        del deadline, cancellation
        self.revalidated = True


class _Sources:
    def __init__(self) -> None:
        self.calls = 0
        self.error: BaseException | None = None
        self.batch: _Batch | None = None

    def borrow_committed_pdf_sources(
        self,
        plan: PdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> _Batch:
        del deadline, cancellation
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.batch = _Batch(plan)
        return self.batch


class _Rasterizer:
    def __init__(self) -> None:
        self.requests: list[SignatureImageRasterRequest] = []
        self.error: BaseException | None = None
        self.dimensions: dict[UUID, tuple[int, int]] = {}
        self.replacement_asset_id: UUID | None = None

    def rasterize_committed(
        self,
        request: SignatureImageRasterRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: object | None = None,
    ) -> SignatureImageOverlayRasterSource:
        del deadline, cancellation, progress
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        width, height = self.dimensions.get(request.asset_id, (2, 2))
        pixels = b"\x10\x20\x30\xff" * (width * height)
        asset_id = self.replacement_asset_id or request.asset_id
        metadata = OverlayRasterAsset(
            asset_id,
            width,
            height,
            width * 4,
            len(pixels),
            hashlib.sha256(pixels).hexdigest(),
        )
        return SignatureImageOverlayRasterSource(metadata, pixels)


class _Candidate:
    def __init__(self, report: object) -> None:
        self._report = report
        self.position = 0
        self.closed = False

    @property
    def report(self) -> FlattenedPdfExportVerificationReport:
        if isinstance(self._report, BaseException):
            raise self._report
        return cast(FlattenedPdfExportVerificationReport, self._report)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True

    def read(self, size: int, /) -> bytes:
        selected = PAYLOAD[self.position : self.position + size]
        self.position += len(selected)
        return selected


class _Broker:
    def __init__(self) -> None:
        self.input_limit: object = 256
        self.input_limit_error: BaseException | None = None
        self.output_limit: object = 8 * 1024 * 1024
        self.output_limit_error: BaseException | None = None
        self.error: BaseException | None = None
        self.report_override: object | None = None
        self.calls = 0
        self.plan: FlattenedPdfExportPlan | None = None
        self.rasters: Mapping[UUID, SignatureImageOverlayRasterSource] | None = None
        self.candidate: _Candidate | None = None

    @property
    def maximum_output_bytes(self) -> int:
        if self.output_limit_error is not None:
            raise self.output_limit_error
        return cast(int, self.output_limit)

    @property
    def maximum_input_references(self) -> int:
        if self.input_limit_error is not None:
            raise self.input_limit_error
        return cast(int, self.input_limit)

    def export(
        self,
        plan: FlattenedPdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink,
        reserved_temporary_bytes: int = 0,
    ) -> _Candidate:
        del sources, deadline, cancellation, progress
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.plan = plan
        self.rasters = dict(rasters)
        report = self.report_override or FlattenedPdfExportVerificationReport.for_verified_plan(
            plan,
            output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
            output_byte_size=len(PAYLOAD),
        )
        self.candidate = _Candidate(report)
        return self.candidate


class _Destination:
    def __init__(self) -> None:
        self.capacity_error: BaseException | None = None
        self.commit_error: BaseException | None = None
        self.commit_result: object | None = None
        self.capacity_calls: list[tuple[Path, int]] = []
        self.commit_calls = 0

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        self.capacity_calls.append((destination, estimated_byte_size))
        if self.capacity_error is not None:
            raise self.capacity_error

    def commit(
        self,
        destination: Path,
        source: VerifiedFlattenedPdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> object | None:
        del cancellation, deadline
        assert destination == DESTINATION
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        assert source.read(expected_byte_size) == PAYLOAD
        assert source.read(1) == b""
        assert expected_sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        return self.commit_result


class _History:
    def __init__(self) -> None:
        self.identifiers: object = frozenset()
        self.error: BaseException | None = None
        self.calls = 0
        self.binding: PdfExportHistoryPlanBinding | None = None

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]:
        assert case_id == CASE_ID
        if isinstance(self.identifiers, BaseException):
            raise self.identifiers
        return cast(frozenset[UUID], self.identifiers)

    def record_and_link_binding(
        self,
        binding: PdfExportHistoryPlanBinding,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        self.calls += 1
        self.binding = binding
        assert record.semantic_fingerprint == binding.semantic_fingerprint
        if self.error is not None:
            raise self.error
        return PdfExportOutcome(
            export_id=outcome.export_id,
            status=PdfExportOutcomeStatus.COMMITTED_RECORDED,
            report=outcome.report,
            destination_reopened=True,
            history_retry=outcome.history_retry,
        )


class _RetryLease:
    def __init__(self, receipt: PdfExportHistoryRetryReceipt) -> None:
        self.receipt = receipt
        self.mark_calls = 0
        self.resolve_calls = 0
        self.discard_calls = 0
        self.close_calls = 0

    def mark_committed(self) -> bool:
        self.mark_calls += 1
        return True

    def resolve(self) -> bool:
        self.resolve_calls += 1
        return True

    def discard_prepared(self) -> bool:
        self.discard_calls += 1
        return True

    def close(self) -> None:
        self.close_calls += 1


class _Retries:
    def __init__(self) -> None:
        self.inventory_result: object = PdfExportRetryInventory()
        self.prepare_error: BaseException | None = None
        self.lease: _RetryLease | None = None
        self.discard_prepared_calls = 0

    def prepare(self, receipt: PdfExportHistoryRetryReceipt) -> PreparedPdfExportRetry:
        if self.prepare_error is not None:
            raise self.prepare_error
        self.lease = _RetryLease(receipt)
        return self.lease

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        assert case_id == CASE_ID
        if isinstance(self.inventory_result, BaseException):
            raise self.inventory_result
        return cast(PdfExportRetryInventory, self.inventory_result)

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del case_id, export_id, record_sha256
        return True

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        del case_id, export_id, record_sha256
        self.discard_prepared_calls += 1
        return True


@dataclass(slots=True)
class _Stack:
    service: FlattenedPdfExportService
    manifests: _Manifests
    sources: _Sources
    rasterizer: _Rasterizer
    broker: _Broker
    destination: _Destination
    history: _History
    retries: _Retries
    policy: ResourcePolicy


def _stack(
    *,
    document: dict[str, object] | None = None,
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> _Stack:
    manifests = _Manifests(_snapshot(document))
    sources = _Sources()
    rasterizer = _Rasterizer()
    broker = _Broker()
    destination = _Destination()
    history = _History()
    retries = _Retries()
    assert isinstance(manifests, ManifestRepository)
    assert isinstance(sources, CommittedPdfSourceBatchStore)
    assert isinstance(rasterizer, FlattenedPdfRasterizer)
    assert isinstance(broker, FlattenedPdfExportBroker)
    assert isinstance(destination, FlattenedPdfDestination)
    assert isinstance(history, FlattenedPdfHistoryRecorder)
    assert isinstance(retries, PdfExportRetryRepository)
    service = FlattenedPdfExportService(
        manifests,
        sources,
        rasterizer,
        broker,
        destination,
        history,
        retries,
        policy,
        clock=lambda: RECORDED_AT,
        id_factory=lambda: EXPORT_ID,
    )
    return _Stack(
        service,
        manifests,
        sources,
        rasterizer,
        broker,
        destination,
        history,
        retries,
        policy,
    )


def _export(
    stack: _Stack,
    *,
    deadline: DeadlineProbe = lambda: False,
    cancellation: CancellationProbe = lambda: False,
    progress: FlattenedPdfExportProgressSink | None = None,
) -> FlattenedPdfExportOutcome:
    return stack.service.export_pdf(
        CASE_ID,
        DESTINATION,
        deadline=deadline,
        cancellation=cancellation,
        progress=(lambda _value: None) if progress is None else progress,
    )


def test_no_signature_export_commits_verified_candidate_and_records_history() -> None:
    stack = _stack()
    progress: list[PdfExportProgress] = []

    outcome = _export(stack, progress=progress.append)

    assert type(outcome) is FlattenedPdfExportOutcome
    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert outcome.report.plan.visible_overlay_count == 1
    assert outcome.report.plan.placed_signature_image_count == 0
    assert stack.rasterizer.requests == []
    assert stack.sources.calls == 1
    assert stack.sources.batch is not None and stack.sources.batch.closed
    assert stack.broker.calls == 1
    assert stack.broker.rasters == {}
    assert stack.broker.candidate is not None and stack.broker.candidate.closed
    assert stack.destination.capacity_calls == [(DESTINATION, cast(int, stack.broker.output_limit))]
    assert stack.destination.commit_calls == 1
    assert stack.history.calls == 1
    assert stack.history.binding == PdfExportHistoryPlanBinding(
        case_id=CASE_ID,
        manifest_revision=7,
        manifest_sha256="e" * 64,
        semantic_fingerprint=outcome.report.plan.semantic_fingerprint,
        page_count=1,
        unsigned_derivative=False,
    )
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 1
    assert stack.retries.lease.close_calls == 1
    assert tuple(value.phase for value in progress) == (
        PdfExportProgressPhase.PREPARING,
        PdfExportProgressPhase.SAVING,
        PdfExportProgressPhase.RECORDING,
        PdfExportProgressPhase.COMPLETE,
    )
    assert all(value.total_pages == 1 for value in progress)


def test_signature_raster_flow_uses_exact_asset_facts_and_policy_edge() -> None:
    stack = _stack(document=_document(signature_assets=1))
    limits = OverlayRenderLimits.from_resource_policy(stack.policy)
    expected_edge = min(
        limits.maximum_raster_width_pixels,
        limits.maximum_raster_height_pixels,
        math.isqrt(limits.maximum_raster_pixels),
        math.isqrt(limits.maximum_raster_bytes // 4),
    )

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.report.plan.placed_signature_image_count == 1
    assert len(stack.rasterizer.requests) == 1
    request = stack.rasterizer.requests[0]
    assert request.case_id == CASE_ID
    assert request.asset_id == ASSET_IDS[0]
    assert request.media_type is SignatureImageMediaType.PNG
    assert request.source_byte_count == 68
    assert request.source_sha256 == "1" * 64
    assert request.maximum_width_pixels == expected_edge == 2_000
    assert request.maximum_height_pixels == expected_edge
    assert stack.broker.rasters is not None
    assert frozenset(stack.broker.rasters) == {ASSET_IDS[0]}
    assert stack.broker.plan is outcome.report.plan
    assert stack.broker.plan.assets[0].raster_asset == (stack.broker.rasters[ASSET_IDS[0]].metadata)


def test_input_reference_capacity_accepts_255_bindings_and_rejects_256_before_work() -> None:
    accepted = _stack(document=_document(signature_assets=254))

    outcome = _export(accepted)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert len(accepted.rasterizer.requests) == 254
    assert accepted.broker.calls == 1

    rejected = _stack(document=_document(signature_assets=255))

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(rejected)

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.input_references"
    assert rejected.destination.capacity_calls == []
    assert rejected.rasterizer.requests == []
    assert rejected.sources.calls == 0
    assert rejected.broker.calls == 0


@pytest.mark.parametrize("dimensions", ((2_001, 1), (1, 2_001)))
def test_rasterizer_cannot_exceed_the_exact_requested_dimensions(
    dimensions: tuple[int, int],
) -> None:
    stack = _stack(document=_document(signature_assets=1))
    stack.rasterizer.dimensions[ASSET_IDS[0]] = dimensions

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert raised.value.context == "flattened.raster_result"
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0


def test_total_raster_bytes_are_bounded_by_derived_resource_policy() -> None:
    small_policy = replace(
        CONSTRAINED_RESOURCE_POLICY_V1,
        maximum_temporary_storage_bytes=1_048_576,
        maximum_source_bytes=524_288,
        worker_process_memory_bytes=16_777_216,
        worker_job_memory_bytes=16_777_216,
        canonical_process_tree_rss_bytes=16_777_216,
    )
    limits = OverlayRenderLimits.from_resource_policy(small_policy)
    assert limits.maximum_total_raster_bytes == 524_288
    stack = _stack(document=_document(signature_assets=2), policy=small_policy)
    stack.rasterizer.dimensions = {
        ASSET_IDS[0]: (250, 300),
        ASSET_IDS[1]: (250, 300),
    }

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.raster_total"
    assert len(stack.rasterizer.requests) == 2
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0
    assert stack.destination.commit_calls == 0


def test_signed_source_requires_exact_confirmation_before_export_work() -> None:
    stack = _stack(document=_document(signed_source=True, confirmed=False))

    with pytest.raises(SignedSourcePolicyError) as raised:
        _export(stack)

    assert raised.value.code is SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED
    assert stack.destination.capacity_calls == []
    assert stack.rasterizer.requests == []
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0
    assert stack.destination.commit_calls == 0
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_confirmed_signed_source_returns_explicit_unsigned_derivative() -> None:
    stack = _stack(document=_document(signed_source=True))

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.report.base_report.unsigned_derivative
    assert outcome.report.plan.contains_signature_markers
    assert outcome.safe_message.startswith("Unsigned derivative:")
    assert stack.history.binding is not None
    assert stack.history.binding.unsigned_derivative


@pytest.mark.parametrize(
    "code",
    (
        SignatureImageErrorCode.SOURCE_CHANGED,
        SignatureImageErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.CASE_CHANGED,
    ),
)
def test_changed_signature_asset_fails_closed_before_source_borrow(
    code: SignatureImageErrorCode,
) -> None:
    stack = _stack(document=_document(signature_assets=1))
    stack.rasterizer.error = SignatureImageError(code, "signature.private")

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSET_CHANGED
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0
    assert stack.destination.commit_calls == 0
    assert "private" not in str(raised.value)
    assert raised.value.__context__ is None


def test_wrong_raster_asset_identity_is_rejected_before_worker() -> None:
    stack = _stack(document=_document(signature_assets=1))
    stack.rasterizer.replacement_asset_id = ASSET_IDS[1]

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0


def test_stale_manifest_and_changed_source_map_to_closed_failures() -> None:
    stale = _stack()
    stale.manifests.error = ManifestRepositoryError(
        ManifestRepositoryErrorCode.REVISION_CONFLICT,
        "manifest.private",
    )
    with pytest.raises(FlattenedPdfExportError) as stale_error:
        _export(stale)
    assert stale_error.value.code is FlattenedPdfExportErrorCode.SOURCE_CHANGED
    assert stale.destination.capacity_calls == []

    changed = _stack()
    changed.sources.error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "source.private",
    )
    with pytest.raises(FlattenedPdfExportError) as changed_error:
        _export(changed)
    assert changed_error.value.code is FlattenedPdfExportErrorCode.SOURCE_CHANGED
    assert changed.broker.calls == 0
    assert changed.destination.commit_calls == 0


@pytest.mark.parametrize(
    ("deadline", "cancellation", "code"),
    (
        (lambda: False, lambda: True, FlattenedPdfExportErrorCode.CANCELLED),
        (lambda: True, lambda: False, FlattenedPdfExportErrorCode.TIMED_OUT),
    ),
)
def test_initial_cancellation_and_timeout_stop_before_manifest(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    code: FlattenedPdfExportErrorCode,
) -> None:
    stack = _stack()

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack, deadline=deadline, cancellation=cancellation)

    assert raised.value.code is code
    assert stack.manifests.calls == 0
    assert stack.destination.capacity_calls == []
    assert stack.broker.calls == 0


@pytest.mark.parametrize(
    ("signature_code", "export_code"),
    (
        (SignatureImageErrorCode.CANCELLED, FlattenedPdfExportErrorCode.CANCELLED),
        (SignatureImageErrorCode.TIMED_OUT, FlattenedPdfExportErrorCode.TIMED_OUT),
    ),
)
def test_rasterizer_cancellation_and_timeout_are_preserved(
    signature_code: SignatureImageErrorCode,
    export_code: FlattenedPdfExportErrorCode,
) -> None:
    stack = _stack(document=_document(signature_assets=1))
    stack.rasterizer.error = SignatureImageError(signature_code, "signature.private")

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is export_code
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0


def test_candidate_report_must_bind_to_the_exact_broker_plan() -> None:
    stack = _stack()
    changed_document = _document()
    overlays = cast(list[dict[str, object]], changed_document["overlays"])
    cast(dict[str, object], overlays[0]["payload"])["value"] = "OTHER SYNTHETIC VALUE"
    other_plan = build_flattened_pdf_export_plan(_snapshot(changed_document), {})
    stack.broker.report_override = FlattenedPdfExportVerificationReport.for_verified_plan(
        other_plan,
        output_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        output_byte_size=len(PAYLOAD),
    )

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert stack.broker.candidate is not None and stack.broker.candidate.closed
    assert stack.sources.batch is not None and stack.sources.batch.closed
    assert stack.destination.commit_calls == 0
    assert stack.history.calls == 0


def test_candidate_report_access_failure_is_sanitized_inside_cleanup_scopes() -> None:
    stack = _stack()
    stack.broker.report_override = RuntimeError("PRIVATE REPORT VALUE")

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert "PRIVATE REPORT VALUE" not in str(raised.value)
    assert raised.value.__context__ is None
    assert stack.broker.candidate is not None and stack.broker.candidate.closed
    assert stack.sources.batch is not None and stack.sources.batch.closed


def test_destination_no_replace_failure_discards_prepared_retry_and_never_records() -> None:
    stack = _stack()
    stack.destination.commit_error = PdfExportError(
        PdfExportErrorCode.DESTINATION_EXISTS,
        "export.private",
    )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert stack.retries.lease is not None
    assert stack.retries.lease.discard_calls == 1
    assert stack.retries.lease.mark_calls == 0
    assert stack.retries.lease.close_calls == 1
    assert stack.history.calls == 0
    assert stack.broker.candidate is not None and stack.broker.candidate.closed
    assert stack.sources.batch is not None and stack.sources.batch.closed


def test_history_failure_returns_committed_history_pending_with_retry_receipt() -> None:
    stack = _stack()
    stack.history.error = RuntimeError("PRIVATE HISTORY VALUE")

    outcome = _export(stack)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert outcome.destination_reopened
    assert outcome.history_retry_required
    assert not outcome.export_retry_required
    assert stack.destination.commit_calls == 1
    assert stack.history.calls == 1
    assert stack.retries.lease is not None
    assert stack.retries.lease.mark_calls == 1
    assert stack.retries.lease.resolve_calls == 0
    assert stack.retries.lease.discard_calls == 0
    assert stack.retries.lease.close_calls == 1
    assert "PRIVATE" not in outcome.safe_message


def test_uncertain_destination_failure_preserves_prepared_retry_and_is_sanitized() -> None:
    stack = _stack()
    stack.destination.commit_error = RuntimeError("PRIVATE DESTINATION VALUE")

    with pytest.raises(FlattenedPdfExportError) as raised:
        _export(stack)

    assert raised.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE DESTINATION VALUE" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert stack.retries.lease is not None
    assert stack.retries.lease.discard_calls == 0
    assert stack.retries.lease.close_calls == 1
    assert stack.history.calls == 0


def test_capacity_preflight_preserves_no_replace_error_without_worker_authority() -> None:
    stack = _stack()
    stack.destination.capacity_error = PdfExportError(
        PdfExportErrorCode.DESTINATION_EXISTS,
        "export.private",
    )

    with pytest.raises(PdfExportError) as raised:
        _export(stack)

    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert stack.sources.calls == 0
    assert stack.broker.calls == 0
    assert stack.retries.lease is None
    assert stack.destination.commit_calls == 0


def test_invalid_output_limit_and_precommit_progress_failures_are_sanitized() -> None:
    invalid_input_limit = _stack()
    invalid_input_limit.broker.input_limit = "PRIVATE INPUT LIMIT"
    with pytest.raises(FlattenedPdfExportError) as input_limit_error:
        _export(invalid_input_limit)
    assert input_limit_error.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE INPUT LIMIT" not in str(input_limit_error.value)
    assert invalid_input_limit.destination.capacity_calls == []

    invalid_limit = _stack()
    invalid_limit.broker.output_limit = "PRIVATE LIMIT"
    with pytest.raises(FlattenedPdfExportError) as limit_error:
        _export(invalid_limit)
    assert limit_error.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE LIMIT" not in str(limit_error.value)
    assert invalid_limit.destination.capacity_calls == []

    bad_progress = _stack()

    def progress(_value: PdfExportProgress) -> None:
        raise RuntimeError("PRIVATE PROGRESS VALUE")

    with pytest.raises(FlattenedPdfExportError) as progress_error:
        _export(bad_progress, progress=progress)
    assert progress_error.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE PROGRESS VALUE" not in str(progress_error.value)
    assert progress_error.value.__context__ is None
    assert bad_progress.rasterizer.requests == []
    assert bad_progress.sources.calls == 0
    assert bad_progress.broker.calls == 0


@pytest.mark.parametrize(
    "failed_phase",
    (PdfExportProgressPhase.RECORDING, PdfExportProgressPhase.COMPLETE),
)
def test_post_commit_progress_failures_do_not_relabel_verified_output(
    failed_phase: PdfExportProgressPhase,
) -> None:
    stack = _stack()

    def progress(value: PdfExportProgress) -> None:
        if value.phase is failed_phase:
            raise KeyboardInterrupt

    outcome = _export(stack, progress=progress)

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert stack.destination.commit_calls == 1
    assert stack.history.calls == 1


def test_invalid_callbacks_and_raw_rasterizer_failure_do_not_disclose_values() -> None:
    callback = _stack()
    with pytest.raises(FlattenedPdfExportError) as callback_error:
        _export(callback, deadline=cast(DeadlineProbe, lambda: "PRIVATE"))
    assert callback_error.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE" not in str(callback_error.value)
    assert callback.manifests.calls == 0

    raster = _stack(document=_document(signature_assets=1))
    raster.rasterizer.error = RuntimeError("PRIVATE SIGNATURE PIXELS")
    with pytest.raises(FlattenedPdfExportError) as raster_error:
        _export(raster)
    assert raster_error.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert "PRIVATE SIGNATURE PIXELS" not in str(raster_error.value)
    assert raster_error.value.__context__ is None
    assert "PRIVATE SYNTHETIC" not in repr(raster_error.value)
