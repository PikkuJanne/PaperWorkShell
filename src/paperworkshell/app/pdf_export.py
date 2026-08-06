"""Application orchestration for one verified, no-replace PDF export."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Never, Protocol, Self, runtime_checkable
from uuid import UUID, uuid4

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
    PdfExportVerificationReport,
    build_pdf_export_plan,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryRecord,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32


class PdfExportProgressSink(Protocol):
    """Receive one validated, path-free export progress record."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


class VerifiedPdfCandidate(Protocol):
    """Single-use worker candidate retained until destination publication ends."""

    @property
    def report(self) -> PdfExportVerificationReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PdfExportBroker(Protocol):
    """Path-free port implemented by the one-shot worker broker."""

    def export(
        self,
        plan: PdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> VerifiedPdfCandidate: ...


@runtime_checkable
class VerifiedPdfDestination(Protocol):
    """Commit one verified candidate or raise a sanitized PDF-export failure."""

    def commit(
        self,
        destination: Path,
        source: VerifiedPdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> object | None: ...


@runtime_checkable
class PdfExportHistoryRecorder(Protocol):
    """Record and manifest-link one already destination-committed export."""

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]: ...

    def record_and_link(
        self,
        plan: PdfExportPlan,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome: ...


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PdfExportService:
    """Plan, run, publish, and record one deterministic PDF derivative."""

    __slots__ = (
        "_broker",
        "_clock",
        "_destination",
        "_history",
        "_id_factory",
        "_manifests",
        "_sources",
    )

    def __init__(
        self,
        manifests: ManifestRepository,
        sources: CommittedPdfSourceBatchStore,
        broker: PdfExportBroker,
        destination: VerifiedPdfDestination,
        history: PdfExportHistoryRecorder,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(manifests, ManifestRepository)
            or not isinstance(sources, CommittedPdfSourceBatchStore)
            or not isinstance(broker, PdfExportBroker)
            or not isinstance(destination, VerifiedPdfDestination)
            or not isinstance(history, PdfExportHistoryRecorder)
            or not callable(clock)
            or not callable(id_factory)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.service")
        self._manifests = manifests
        self._sources = sources
        self._broker = broker
        self._destination = destination
        self._history = history
        self._clock = clock
        self._id_factory = id_factory

    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink = _ignore_progress,
    ) -> PdfExportOutcome:
        """Return success only after destination reopen; history failure stays pending."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or not isinstance(destination, Path)
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.request")
        _require_active(deadline, cancellation)
        plan, manifest_ids = self._load_plan(case_id)
        history_ids = self._load_history_ids(case_id)
        export_id = self._new_id(set(manifest_ids | history_ids))
        _emit_progress(
            progress,
            PdfExportProgress(PdfExportProgressPhase.PREPARING, 0, plan.page_count),
        )

        report: PdfExportVerificationReport | None = None
        history_record: PdfExportHistoryRecord | None = None
        history_retry: PdfExportHistoryRetryReceipt | None = None
        destination_committed = False
        try:
            batch = self._sources.borrow_committed_pdf_sources(
                plan,
                deadline=deadline,
                cancellation=cancellation,
            )
            with batch as retained_sources:
                candidate_context = self._broker.export(
                    plan,
                    retained_sources,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
                with candidate_context as candidate:
                    report = _require_candidate(plan, candidate)
                    _require_active(deadline, cancellation)
                    history_record, history_retry = self._prepare_history(
                        plan,
                        export_id,
                        report,
                    )
                    _emit_progress(
                        progress,
                        PdfExportProgress(
                            PdfExportProgressPhase.SAVING,
                            plan.page_count,
                            plan.page_count,
                        ),
                    )
                    commit_result = self._destination.commit(
                        destination,
                        candidate,
                        expected_byte_size=report.output_byte_size,
                        expected_sha256=report.output_sha256,
                        cancellation=cancellation,
                        deadline=deadline,
                    )
                    if commit_result is not None:
                        reject_pdf_export(
                            PdfExportErrorCode.DESTINATION_FAILED,
                            "export.destination_result",
                        )
                    destination_committed = True
        except PdfExportError:
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
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.operation")

        # A hostile context manager may suppress an exception raised before
        # publication.  Only the exact successful destination return can cross
        # this boundary as a committed outcome.
        if (
            not destination_committed
            or type(report) is not PdfExportVerificationReport
            or type(history_record) is not PdfExportHistoryRecord
            or type(history_retry) is not PdfExportHistoryRetryReceipt
        ):
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.commit_state")

        pending = PdfExportOutcome(
            export_id=export_id,
            status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
            report=report,
            destination_reopened=True,
            history_retry=history_retry,
        )
        _emit_progress_after_commit(
            progress,
            PdfExportProgress(
                PdfExportProgressPhase.RECORDING,
                plan.page_count,
                plan.page_count,
            ),
        )
        result = pending
        try:
            selected = self._history.record_and_link(plan, history_record, pending)
            if (
                type(selected) is not PdfExportOutcome
                or selected.export_id != pending.export_id
                or selected.report != pending.report
                or selected.destination_reopened is not True
                or selected.history_retry != pending.history_retry
            ):
                reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "export.history_outcome")
            result = selected
        except PdfExportError, PdfExportHistoryError:
            result = pending
        except BaseException:
            result = pending
        _emit_progress_after_commit(
            progress,
            PdfExportProgress(
                PdfExportProgressPhase.COMPLETE,
                plan.page_count,
                plan.page_count,
            ),
        )
        return result

    def _prepare_history(
        self,
        plan: PdfExportPlan,
        export_id: UUID,
        report: PdfExportVerificationReport,
    ) -> tuple[PdfExportHistoryRecord, PdfExportHistoryRetryReceipt]:
        try:
            recorded_at = self._clock()
            record = build_pdf_export_history_record_for_commit(
                plan,
                export_id=export_id,
                report=report,
                recorded_at=recorded_at,
            )
            receipt = build_pdf_export_history_retry_receipt(plan, record, report)
            return record, receipt
        except PdfExportError:
            raise
        except PdfExportHistoryError:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.history_record")
        except BaseException:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")

    def _load_history_ids(self, case_id: UUID) -> frozenset[UUID]:
        try:
            identifiers = self._history.reserved_export_ids(case_id)
            if type(identifiers) is not frozenset or any(
                type(value) is not UUID or value.version != 4 for value in identifiers
            ):
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.history_ids")
            return identifiers
        except PdfExportError:
            raise
        except PdfExportHistoryError:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.history_ids")
        except BaseException:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.history_ids")

    def _load_plan(self, case_id: UUID) -> tuple[PdfExportPlan, frozenset[UUID]]:
        try:
            loaded = self._manifests.load(case_id)
            if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
                reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.manifest_load")
            return (
                build_pdf_export_plan(loaded.snapshot),
                _manifest_identifier_ids(loaded.snapshot.document),
            )
        except PdfExportError:
            raise
        except ManifestRepositoryError as error:
            _raise_manifest_error(error)
        except BaseException:
            reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.manifest_load")

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except PdfExportError:
                raise
            except BaseException:
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")


def _manifest_identifier_ids(document: dict[str, object]) -> frozenset[UUID]:
    if type(document) is not dict:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.manifest_identifiers")
    identifiers: set[UUID] = set()

    def collect(raw: object, *, repeatable: set[UUID] | None = None) -> None:
        if type(raw) is not str:
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        identifier = UUID(raw)
        repeated = repeatable is not None and identifier in repeatable
        if (
            identifier.version != 4
            or str(identifier) != raw
            or (identifier in identifiers and not repeated)
        ):
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        identifiers.add(identifier)
        if repeatable is not None:
            repeatable.add(identifier)

    try:
        collect(document.get("case_id"))
        for collection_name, field_name in (
            ("sources", "source_id"),
            ("pages", "page_id"),
            ("page_operations", "operation_id"),
            ("assets", "asset_id"),
            ("overlays", "overlay_id"),
            ("assignments", "assignment_id"),
            ("confirmations", "confirmation_id"),
        ):
            collection = document.get(collection_name)
            if type(collection) is not list:
                reject_pdf_export(
                    PdfExportErrorCode.MANIFEST_INVALID,
                    "export.manifest_identifiers",
                )
            for value in collection:
                if type(value) is not dict:
                    reject_pdf_export(
                        PdfExportErrorCode.MANIFEST_INVALID,
                        "export.manifest_identifiers",
                    )
                collect(value.get(field_name))
        operations = document.get("page_operations")
        if type(operations) is not list:
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        group_ids: set[UUID] = set()
        for value in operations:
            if type(value) is not dict:
                reject_pdf_export(
                    PdfExportErrorCode.MANIFEST_INVALID,
                    "export.manifest_identifiers",
                )
            payload = value.get("payload")
            if type(payload) is not dict:
                reject_pdf_export(
                    PdfExportErrorCode.MANIFEST_INVALID,
                    "export.manifest_identifiers",
                )
            if payload.get("kind") == "set_group":
                collect(payload.get("group_id"), repeatable=group_ids)
        history = document.get("export_history")
        if type(history) is not dict or set(history) != {"storage_name", "entries"}:
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        if history.get("storage_name") != "export-history.json":
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        entries = history.get("entries")
        if type(entries) is not list:
            reject_pdf_export(
                PdfExportErrorCode.MANIFEST_INVALID,
                "export.manifest_identifiers",
            )
        for value in entries:
            if type(value) is not dict:
                reject_pdf_export(
                    PdfExportErrorCode.MANIFEST_INVALID,
                    "export.manifest_identifiers",
                )
            collect(value.get("export_id"))
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.manifest_identifiers")
    return frozenset(identifiers)


def _require_candidate(
    plan: PdfExportPlan,
    candidate: VerifiedPdfCandidate,
) -> PdfExportVerificationReport:
    try:
        report = candidate.report
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate")
    try:
        reader = candidate.read
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate")
    if (
        type(report) is not PdfExportVerificationReport
        or report.semantic_fingerprint != plan.semantic_fingerprint
        or report.page_count != plan.page_count
        or report.unsigned_derivative != plan.contains_signature_markers
        or not callable(reader)
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate")
    return report


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _probe(deadline, context="export.deadline_probe"):
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.deadline")
    if _probe(cancellation, context="export.cancel_probe"):
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.cancelled")


def _probe(probe: Callable[[], bool], *, context: str) -> bool:
    try:
        value = probe()
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, context)
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, context)
    return value


def _emit_progress(sink: PdfExportProgressSink, value: PdfExportProgress) -> None:
    try:
        sink(value)
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.progress")


def _emit_progress_after_commit(
    sink: PdfExportProgressSink,
    value: PdfExportProgress,
) -> None:
    try:
        sink(value)
    except BaseException:
        # A committed, reopened output must never be relabelled as a failed export.
        return


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source")
    if error.code is SourceIngestErrorCode.MANIFEST_CONFLICT:
        reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "export.source")
    if error.code in {
        SourceIngestErrorCode.MANIFEST_INVALID,
        SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED,
    }:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.source")
    if error.code in {
        SourceIngestErrorCode.SOURCE_UNAVAILABLE,
        SourceIngestErrorCode.SOURCE_CHANGED,
    }:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source")
    reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.source")


def _raise_manifest_error(error: ManifestRepositoryError) -> Never:
    if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
        reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "export.manifest")
    if error.code is ManifestRepositoryErrorCode.CAPACITY_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.manifest")
    if error.code in {
        ManifestRepositoryErrorCode.MANIFEST_INVALID,
        ManifestRepositoryErrorCode.VERSION_UNSUPPORTED,
        ManifestRepositoryErrorCode.CASE_MISMATCH,
    }:
        reject_pdf_export(PdfExportErrorCode.MANIFEST_INVALID, "export.manifest")
    reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.manifest")
