"""Application orchestration for verified overlay-flattened PDF output."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Never, Protocol, Self, runtime_checkable
from uuid import UUID, uuid4

from paperworkshell.app.pdf_export import manifest_export_identifier_ids
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryPlanBinding,
    PdfExportHistoryRecord,
    build_pdf_export_history_record_for_binding,
    build_pdf_export_history_retry_receipt_for_binding,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PdfExportRetryRepository,
    PreparedPdfExportRetry,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterRequest,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourceSignatureState,
    require_signed_source_modification_confirmation,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportOutcome,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    build_flattened_pdf_export_plan,
    reject_flattened_pdf_export,
    required_visible_signature_assets,
)
from paperworkshell.overlays.models import OverlayAssetRecord
from paperworkshell.overlays.rendering import OverlayRasterAsset, OverlayRenderLimits
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32
_PLANNING_PIXEL_SHA256 = hashlib.sha256(b"\0\0\0\0").hexdigest()


class FlattenedPdfExportProgressSink(Protocol):
    """Receive only bounded numeric flattened-export progress."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


class VerifiedFlattenedPdfCandidate(Protocol):
    """Single-use volatile candidate retained through destination publication."""

    @property
    def report(self) -> FlattenedPdfExportVerificationReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class FlattenedPdfExportBroker(Protocol):
    """Path-free one-shot worker broker for exact overlays and raster pixels."""

    @property
    def maximum_output_bytes(self) -> int: ...

    @property
    def maximum_input_references(self) -> int: ...

    def export(
        self,
        plan: FlattenedPdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink,
    ) -> VerifiedFlattenedPdfCandidate: ...


@runtime_checkable
class FlattenedPdfRasterizer(Protocol):
    """Decode one exact committed visual-signature asset outside export work."""

    def rasterize_committed(
        self,
        request: SignatureImageRasterRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> SignatureImageOverlayRasterSource: ...


@runtime_checkable
class FlattenedPdfDestination(Protocol):
    """No-replace destination shared with structural PDF export."""

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None: ...

    def commit(
        self,
        destination: Path,
        source: VerifiedFlattenedPdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> object | None: ...


@runtime_checkable
class FlattenedPdfHistoryRecorder(Protocol):
    """Persist unchanged export-history-v1 structural facts."""

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]: ...

    def record_and_link_binding(
        self,
        binding: PdfExportHistoryPlanBinding,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome: ...


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FlattenedPdfExportService:
    """Rasterize, flatten, verify, publish, and record one PDF derivative."""

    __slots__ = (
        "_broker",
        "_clock",
        "_destination",
        "_history",
        "_id_factory",
        "_manifests",
        "_overlay_limits",
        "_rasterizer",
        "_retries",
        "_sources",
    )

    def __init__(
        self,
        manifests: ManifestRepository,
        sources: CommittedPdfSourceBatchStore,
        rasterizer: FlattenedPdfRasterizer,
        broker: FlattenedPdfExportBroker,
        destination: FlattenedPdfDestination,
        history: FlattenedPdfHistoryRecorder,
        retries: PdfExportRetryRepository,
        resource_policy: ResourcePolicy,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(manifests, ManifestRepository)
            or not isinstance(sources, CommittedPdfSourceBatchStore)
            or not isinstance(rasterizer, FlattenedPdfRasterizer)
            or not isinstance(broker, FlattenedPdfExportBroker)
            or not isinstance(destination, FlattenedPdfDestination)
            or not isinstance(history, FlattenedPdfHistoryRecorder)
            or not isinstance(retries, PdfExportRetryRepository)
            or type(resource_policy) is not ResourcePolicy
            or not callable(clock)
            or not callable(id_factory)
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.service",
            )
        self._manifests = manifests
        self._sources = sources
        self._rasterizer = rasterizer
        self._broker = broker
        self._destination = destination
        self._history = history
        self._retries = retries
        self._overlay_limits = OverlayRenderLimits.from_resource_policy(resource_policy)
        self._clock = clock
        self._id_factory = id_factory

    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink = _ignore_progress,
    ) -> FlattenedPdfExportOutcome:
        """Return only after no-replace commit and destination reopen succeed."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or not isinstance(destination, Path)
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.request",
            )
        _require_active(deadline, cancellation)
        snapshot, manifest_ids = self._load_snapshot(case_id)
        assets = required_visible_signature_assets(snapshot)
        planning_plan = _build_planning_plan(snapshot, assets)
        self._require_input_reference_capacity(planning_plan, len(assets))
        self._confirm_signed_sources(snapshot, planning_plan)
        maximum_output_bytes = self._maximum_output_bytes()
        try:
            self._destination.require_capacity(
                destination,
                estimated_byte_size=maximum_output_bytes,
            )
        except PdfExportError:
            raise
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.DESTINATION_FAILED,
                "flattened.capacity_preflight",
            )
        _emit_progress(
            progress,
            PdfExportProgress(
                PdfExportProgressPhase.PREPARING,
                0,
                planning_plan.page_count,
            ),
        )
        rasters = self._rasterize_assets(
            case_id,
            assets,
            deadline=deadline,
            cancellation=cancellation,
        )
        plan = build_flattened_pdf_export_plan(
            snapshot,
            {asset_id: source.metadata for asset_id, source in rasters.items()},
        )
        if plan.base_plan != planning_plan.base_plan or plan.pages != planning_plan.pages:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.planning_binding",
            )
        history_ids = self._load_history_ids(case_id)
        export_id = self._new_id(set(manifest_ids | history_ids))

        report: FlattenedPdfExportVerificationReport | None = None
        record: PdfExportHistoryRecord | None = None
        receipt: PdfExportHistoryRetryReceipt | None = None
        retry: PreparedPdfExportRetry | None = None
        retry_phase_committed = False
        destination_committed = False
        binding = _history_binding(plan)
        try:
            batch = self._sources.borrow_committed_pdf_sources(
                plan.base_plan,
                deadline=deadline,
                cancellation=cancellation,
            )
            with batch as retained_sources:
                candidate_context = self._broker.export(
                    plan,
                    retained_sources,
                    rasters,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
                with candidate_context as candidate:
                    report = _require_candidate(plan, candidate)
                    _require_active(deadline, cancellation)
                    record, receipt = self._prepare_history(binding, export_id, report)
                    _emit_progress(
                        progress,
                        PdfExportProgress(
                            PdfExportProgressPhase.SAVING,
                            plan.page_count,
                            plan.page_count,
                        ),
                    )
                    retry = self._prepare_retry(receipt)
                    try:
                        result = self._destination.commit(
                            destination,
                            candidate,
                            expected_byte_size=report.base_report.output_byte_size,
                            expected_sha256=report.base_report.output_sha256,
                            cancellation=cancellation,
                            deadline=deadline,
                        )
                        if result is not None:
                            reject_pdf_export(
                                PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN,
                                "export.destination_result",
                            )
                        destination_committed = True
                    except PdfExportError as error:
                        if error.code is not PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN:
                            try:
                                retry.discard_prepared()
                            except BaseException:
                                pass
                        _close_retry(retry)
                        retry = None
                        raise
                    except BaseException:
                        _close_retry(retry)
                        retry = None
                        raise
                    try:
                        retry_phase_committed = retry.mark_committed() is True
                    except BaseException:
                        retry_phase_committed = False
        except PdfExportError:
            if not destination_committed:
                raise
        except FlattenedPdfExportError:
            if not destination_committed:
                raise
        except SourceIngestError as error:
            if not destination_committed:
                _raise_source_error(error)
        except ManifestRepositoryError as error:
            if not destination_committed:
                _raise_manifest_error(error)
        except BaseException:
            if not destination_committed:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.operation",
                )

        if (
            not destination_committed
            or type(report) is not FlattenedPdfExportVerificationReport
            or type(record) is not PdfExportHistoryRecord
            or type(receipt) is not PdfExportHistoryRetryReceipt
            or not isinstance(retry, PreparedPdfExportRetry)
        ):
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.commit_state")

        pending = PdfExportOutcome(
            export_id=export_id,
            status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
            report=report.base_report,
            destination_reopened=True,
            history_retry=receipt,
        )
        _emit_progress_after_commit(
            progress,
            PdfExportProgress(
                PdfExportProgressPhase.RECORDING,
                plan.page_count,
                plan.page_count,
            ),
        )
        base_result = pending
        try:
            selected = self._history.record_and_link_binding(binding, record, pending)
            if (
                type(selected) is not PdfExportOutcome
                or selected.export_id != pending.export_id
                or selected.report != pending.report
                or selected.destination_reopened is not True
                or selected.history_retry != pending.history_retry
            ):
                reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "export.history_outcome")
            base_result = selected
        except PdfExportError, PdfExportHistoryError:
            base_result = pending
        except BaseException:
            base_result = pending
        if base_result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED:
            try:
                if retry_phase_committed:
                    retry.resolve()
                else:
                    self._retries.discard_prepared(
                        receipt.case_id,
                        receipt.export_id,
                        receipt.record_sha256,
                    )
            except BaseException:
                pass
        _close_retry(retry)
        _emit_progress_after_commit(
            progress,
            PdfExportProgress(
                PdfExportProgressPhase.COMPLETE,
                plan.page_count,
                plan.page_count,
            ),
        )
        return FlattenedPdfExportOutcome(base_outcome=base_result, report=report)

    def _load_snapshot(self, case_id: UUID) -> tuple[ManifestSnapshot, frozenset[UUID]]:
        try:
            loaded = self._manifests.load(case_id)
            if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                    "flattened.manifest_load",
                )
            return loaded.snapshot, manifest_export_identifier_ids(loaded.snapshot.document)
        except FlattenedPdfExportError:
            raise
        except ManifestRepositoryError as error:
            _raise_manifest_error(error)
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                "flattened.manifest_load",
            )

    def _maximum_output_bytes(self) -> int:
        try:
            value = self._broker.maximum_output_bytes
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.output_limit",
            )
        if type(value) is not int or value < 1:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.output_limit",
            )
        return value

    def _require_input_reference_capacity(
        self,
        plan: FlattenedPdfExportPlan,
        asset_count: int,
    ) -> None:
        try:
            maximum = self._broker.maximum_input_references
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.input_reference_limit",
            )
        if type(maximum) is not int or maximum < 2:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.input_reference_limit",
            )
        if 1 + len(plan.base_plan.sources) + asset_count > maximum:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.input_references",
            )

    def _rasterize_assets(
        self,
        case_id: UUID,
        assets: tuple[OverlayAssetRecord, ...],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> dict[UUID, SignatureImageOverlayRasterSource]:
        edge = min(
            self._overlay_limits.maximum_raster_width_pixels,
            self._overlay_limits.maximum_raster_height_pixels,
            math.isqrt(self._overlay_limits.maximum_raster_pixels),
            math.isqrt(self._overlay_limits.maximum_raster_bytes // 4),
        )
        if edge < 1:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.raster_limits",
            )
        selected: dict[UUID, SignatureImageOverlayRasterSource] = {}
        total_bytes = 0
        for asset in assets:
            _require_active(deadline, cancellation)
            source = self._rasterize_asset(
                case_id,
                asset,
                edge=edge,
                deadline=deadline,
                cancellation=cancellation,
            )
            total_bytes += source.metadata.byte_count
            if total_bytes > self._overlay_limits.maximum_total_raster_bytes:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.raster_total",
                )
            selected[asset.asset_id] = source
        return selected

    def _rasterize_asset(
        self,
        case_id: UUID,
        asset: OverlayAssetRecord,
        *,
        edge: int,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> SignatureImageOverlayRasterSource:
        try:
            media_type = SignatureImageMediaType(asset.media_type.value)
            request = SignatureImageRasterRequest(
                case_id=case_id,
                asset_id=asset.asset_id,
                media_type=media_type,
                source_byte_count=asset.byte_size,
                source_sha256=asset.sha256,
                maximum_width_pixels=edge,
                maximum_height_pixels=edge,
            )
            source = self._rasterizer.rasterize_committed(
                request,
                deadline=deadline,
                cancellation=cancellation,
            )
            if type(source) is not SignatureImageOverlayRasterSource:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.raster_result",
                )
            metadata = source.metadata
            if (
                type(metadata) is not OverlayRasterAsset
                or metadata.asset_id != asset.asset_id
                or metadata.width_pixels > request.maximum_width_pixels
                or metadata.height_pixels > request.maximum_height_pixels
                or metadata.width_pixels * metadata.height_pixels
                > self._overlay_limits.maximum_raster_pixels
                or metadata.row_stride_bytes != metadata.width_pixels * 4
                or metadata.byte_count != metadata.row_stride_bytes * metadata.height_pixels
                or metadata.byte_count > self._overlay_limits.maximum_raster_bytes
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.raster_result",
                )
            return source
        except FlattenedPdfExportError:
            raise
        except SignatureImageError as error:
            _raise_signature_error(error)
        except ValueError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MANIFEST_INVALID,
                "flattened.raster_request",
            )
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.rasterize",
            )

    def _confirm_signed_sources(
        self,
        snapshot: ManifestSnapshot,
        plan: FlattenedPdfExportPlan,
    ) -> None:
        require_signed_source_modification_confirmation(
            snapshot,
            tuple(
                SignedSourceModificationSource(
                    source.source_id,
                    SignedSourceSignatureState(source.signature_state.value),
                )
                for source in plan.base_plan.sources
            ),
        )

    def _load_history_ids(self, case_id: UUID) -> frozenset[UUID]:
        try:
            identifiers = self._history.reserved_export_ids(case_id)
            if type(identifiers) is not frozenset or any(
                type(value) is not UUID or value.version != 4 for value in identifiers
            ):
                raise ValueError
            inventory = self._retries.inventory(case_id)
            if type(inventory) is not PdfExportRetryInventory or inventory.invalid_entries:
                reject_pdf_export(
                    PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED,
                    "export.retry_inventory",
                )
            return identifiers | inventory.reserved_export_ids
        except PdfExportError:
            raise
        except BaseException:
            reject_pdf_export(
                PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED,
                "export.retry_inventory",
            )

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.identifier",
                )
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.identifier",
        )

    def _prepare_retry(self, receipt: PdfExportHistoryRetryReceipt) -> PreparedPdfExportRetry:
        try:
            prepared = self._retries.prepare(receipt)
            if not isinstance(prepared, PreparedPdfExportRetry) or prepared.receipt != receipt:
                raise ValueError
            return prepared
        except BaseException:
            reject_pdf_export(
                PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED,
                "export.retry_prepare",
            )

    def _prepare_history(
        self,
        binding: PdfExportHistoryPlanBinding,
        export_id: UUID,
        report: FlattenedPdfExportVerificationReport,
    ) -> tuple[PdfExportHistoryRecord, PdfExportHistoryRetryReceipt]:
        try:
            record = build_pdf_export_history_record_for_binding(
                binding,
                export_id=export_id,
                report=report.base_report,
                recorded_at=self._clock(),
            )
            return (
                record,
                build_pdf_export_history_retry_receipt_for_binding(
                    binding,
                    record,
                    report.base_report,
                ),
            )
        except BaseException:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.history_record",
            )


def _history_binding(plan: FlattenedPdfExportPlan) -> PdfExportHistoryPlanBinding:
    return PdfExportHistoryPlanBinding(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        semantic_fingerprint=plan.semantic_fingerprint,
        page_count=plan.page_count,
        unsigned_derivative=plan.contains_signature_markers,
    )


def _build_planning_plan(
    snapshot: ManifestSnapshot,
    assets: tuple[OverlayAssetRecord, ...],
) -> FlattenedPdfExportPlan:
    """Resolve pages/sources before decode using inert metadata-only placeholders."""

    return build_flattened_pdf_export_plan(
        snapshot,
        {
            asset.asset_id: OverlayRasterAsset(
                asset_id=asset.asset_id,
                width_pixels=1,
                height_pixels=1,
                row_stride_bytes=4,
                byte_count=4,
                pixel_sha256=_PLANNING_PIXEL_SHA256,
            )
            for asset in assets
        },
    )


def _require_candidate(
    plan: FlattenedPdfExportPlan,
    candidate: VerifiedFlattenedPdfCandidate,
) -> FlattenedPdfExportVerificationReport:
    try:
        report = candidate.report
        reader = candidate.read
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.candidate",
        )
    if (
        type(report) is not FlattenedPdfExportVerificationReport
        or report.plan != plan
        or not callable(reader)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.candidate",
        )
    return report


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.callback",
        )
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.callback",
        )
    if cancelled:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.cancelled",
        )
    if expired:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.deadline",
        )


def _emit_progress(
    sink: FlattenedPdfExportProgressSink,
    value: PdfExportProgress,
) -> None:
    try:
        sink(value)
    except PdfExportError, FlattenedPdfExportError:
        raise
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.progress",
        )


def _emit_progress_after_commit(
    sink: FlattenedPdfExportProgressSink,
    value: PdfExportProgress,
) -> None:
    try:
        sink(value)
    except BaseException:
        return


def _close_retry(retry: PreparedPdfExportRetry | None) -> None:
    if retry is None:
        return
    try:
        retry.close()
    except BaseException:
        return


def _raise_signature_error(error: SignatureImageError) -> Never:
    mapping = {
        SignatureImageErrorCode.MODEL_INVALID: FlattenedPdfExportErrorCode.RESULT_INVALID,
        SignatureImageErrorCode.SOURCE_CHANGED: FlattenedPdfExportErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.ASSET_CHANGED: FlattenedPdfExportErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.ASSET_UNKNOWN: FlattenedPdfExportErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.CASE_CHANGED: FlattenedPdfExportErrorCode.ASSET_CHANGED,
        SignatureImageErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        SignatureImageErrorCode.CAPACITY_EXCEEDED: (FlattenedPdfExportErrorCode.LIMIT_EXCEEDED),
        SignatureImageErrorCode.CANCELLED: FlattenedPdfExportErrorCode.CANCELLED,
        SignatureImageErrorCode.TIMED_OUT: FlattenedPdfExportErrorCode.TIMED_OUT,
        SignatureImageErrorCode.RESULT_INVALID: FlattenedPdfExportErrorCode.RESULT_INVALID,
        SignatureImageErrorCode.INTERNAL_ERROR: FlattenedPdfExportErrorCode.INTERNAL_ERROR,
    }
    reject_flattened_pdf_export(
        mapping.get(error.code, FlattenedPdfExportErrorCode.ASSEMBLY_FAILED),
        "flattened.signature_raster",
    )


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.source",
        )
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.source",
        )
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source",
        )
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.SOURCE_CHANGED,
        "flattened.source",
    )


def _raise_manifest_error(error: ManifestRepositoryError) -> Never:
    if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.manifest",
        )
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.MANIFEST_INVALID,
        "flattened.manifest",
    )
