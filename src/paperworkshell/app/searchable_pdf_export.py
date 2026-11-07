"""Application orchestration for verified searchable mixed-PDF output."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Never, Protocol, Self, runtime_checkable
from uuid import UUID, uuid4

from paperworkshell.app.ocr import (
    OcrLanguageAvailability,
    built_in_ocr_language_availability,
)
from paperworkshell.app.pdf_export import manifest_export_identifier_ids
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedSearchablePdfSourceBatchLease,
    CommittedSearchablePdfSourceBatchStore,
    DeadlineProbe,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
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
from paperworkshell.domain.redaction import RedactionError
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportOutcome,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfPageOutcomeCode,
    SearchablePdfVerificationReport,
    build_searchable_pdf_export_plan,
    reject_searchable_pdf_export,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourcePolicyError,
    SignedSourceSignatureState,
    require_signed_source_modification_confirmation,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32


class SearchablePdfExportProgressSink(Protocol):
    """Receive only bounded numeric export progress."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


class VerifiedSearchablePdfCandidate(Protocol):
    """Single-use volatile candidate retained until destination publication ends."""

    @property
    def report(self) -> SearchablePdfVerificationReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class SearchablePdfExportBroker(Protocol):
    """Path-free one-shot searchable-export worker broker."""

    @property
    def maximum_output_bytes(self) -> int: ...

    def export(
        self,
        plan: SearchablePdfExportPlan,
        sources: CommittedSearchablePdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink,
        reserved_temporary_bytes: int = 0,
    ) -> VerifiedSearchablePdfCandidate: ...


@runtime_checkable
class SearchablePdfDestination(Protocol):
    """No-replace destination port shared with structural PDF export."""

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None: ...

    def commit(
        self,
        destination: Path,
        source: VerifiedSearchablePdfCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> object | None: ...


@runtime_checkable
class SearchablePdfHistoryRecorder(Protocol):
    """Persist the unchanged export-history-v1 structural facts."""

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


class SearchablePdfExportService:
    """Plan, process, publish, and record one mixed searchable derivative."""

    __slots__ = (
        "_broker",
        "_clock",
        "_destination",
        "_history",
        "_id_factory",
        "_ocr_languages",
        "_manifests",
        "_retries",
        "_sources",
    )

    def __init__(
        self,
        manifests: ManifestRepository,
        sources: CommittedSearchablePdfSourceBatchStore,
        broker: SearchablePdfExportBroker,
        destination: SearchablePdfDestination,
        history: SearchablePdfHistoryRecorder,
        retries: PdfExportRetryRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], UUID] = uuid4,
        ocr_languages: OcrLanguageAvailability | None = None,
    ) -> None:
        if (
            not isinstance(manifests, ManifestRepository)
            or not isinstance(sources, CommittedSearchablePdfSourceBatchStore)
            or not isinstance(broker, SearchablePdfExportBroker)
            or not isinstance(destination, SearchablePdfDestination)
            or not isinstance(history, SearchablePdfHistoryRecorder)
            or not isinstance(retries, PdfExportRetryRepository)
            or not callable(clock)
            or not callable(id_factory)
            or (
                ocr_languages is not None and not isinstance(ocr_languages, OcrLanguageAvailability)
            )
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.service",
            )
        self._manifests = manifests
        self._sources = sources
        self._broker = broker
        self._destination = destination
        self._history = history
        self._retries = retries
        self._clock = clock
        self._id_factory = id_factory
        self._ocr_languages = ocr_languages or built_in_ocr_language_availability()

    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink = _ignore_progress,
        profile: SearchablePdfExportProfile | None = None,
        require_ocr_success: bool = False,
    ) -> SearchablePdfExportOutcome:
        """Return only after no-replace commit and destination reopen succeed."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or not isinstance(destination, Path)
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
            or (profile is not None and type(profile) is not SearchablePdfExportProfile)
            or type(require_ocr_success) is not bool
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.request",
            )
        _require_active(deadline, cancellation)
        selected_profile = self._select_profile(profile)
        plan, manifest_ids = self._load_plan(case_id, selected_profile)
        try:
            maximum_output_bytes = self._broker.maximum_output_bytes
        except BaseException:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.output_limit",
            )
        if type(maximum_output_bytes) is not int or maximum_output_bytes < 1:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.output_limit",
            )
        try:
            self._destination.require_capacity(
                destination,
                estimated_byte_size=maximum_output_bytes,
            )
        except PdfExportError:
            raise
        except BaseException:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.capacity_preflight")
        history_ids = self._load_history_ids(case_id)
        export_id = self._new_id(set(manifest_ids | history_ids))
        _emit_progress(
            progress,
            PdfExportProgress(PdfExportProgressPhase.PREPARING, 0, plan.page_count),
        )

        report: SearchablePdfVerificationReport | None = None
        record: PdfExportHistoryRecord | None = None
        receipt: PdfExportHistoryRetryReceipt | None = None
        retry: PreparedPdfExportRetry | None = None
        retry_phase_committed = False
        destination_committed = False
        binding = _history_binding(plan)
        try:
            batch = self._sources.borrow_committed_searchable_pdf_sources(
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
                    failed_page = next(
                        (
                            page
                            for page in report.page_outcomes
                            if page.outcome
                            in {
                                SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
                                SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
                            }
                        ),
                        None,
                    )
                    if require_ocr_success and failed_page is not None:
                        reject_searchable_pdf_export(
                            SearchablePdfExportErrorCode.OCR_FAILED,
                            "searchable.ocr_required",
                            output_page_number=failed_page.output_page_index + 1,
                        )
                    _require_active(deadline, cancellation)
                    record, receipt = self._prepare_history(
                        binding,
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
                        # Publication may have reached the destination before
                        # an untyped failure. Preserve the PREPARED receipt for
                        # authenticated recovery, but always release its lease.
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
        except SearchablePdfExportError:
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
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.INTERNAL_ERROR,
                    "searchable.operation",
                )

        if (
            not destination_committed
            or type(report) is not SearchablePdfVerificationReport
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
        return SearchablePdfExportOutcome(base_outcome=base_result, report=report)

    def _require_language_set(self, language_set_id: str) -> None:
        try:
            self._ocr_languages.require_available(language_set_id)
        except OcrError as error:
            if type(error) is not OcrError:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.INTERNAL_ERROR,
                    "searchable.language_preflight",
                )
            if error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE,
                    "searchable.language_preflight",
                )
            if error.code is OcrErrorCode.MODEL_INVALID:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.MODEL_INVALID,
                    "searchable.language_preflight",
                )
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )
        except Exception:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )

    def _select_profile(
        self,
        profile: SearchablePdfExportProfile | None,
    ) -> SearchablePdfExportProfile:
        if profile is not None:
            self._require_language_set(profile.ocr_language_set_id)
            return profile
        try:
            selected = self._ocr_languages.require_selected_available()
        except OcrError as error:
            if type(error) is not OcrError:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.INTERNAL_ERROR,
                    "searchable.language_preflight",
                )
            if error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE,
                    "searchable.language_preflight",
                )
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )
        except Exception:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )
        if type(selected) is not str:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )
        try:
            return SearchablePdfExportProfile(ocr_language_set_id=selected)
        except SearchablePdfExportError:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.language_preflight",
            )

    def _load_plan(
        self,
        case_id: UUID,
        profile: SearchablePdfExportProfile,
    ) -> tuple[SearchablePdfExportPlan, frozenset[UUID]]:
        try:
            loaded = self._manifests.load(case_id)
            if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.MANIFEST_INVALID,
                    "searchable.manifest_load",
                )
            plan = build_searchable_pdf_export_plan(loaded.snapshot, profile=profile)
            require_signed_source_modification_confirmation(
                loaded.snapshot,
                tuple(
                    SignedSourceModificationSource(
                        source.source_id,
                        SignedSourceSignatureState(source.signature_state.value),
                    )
                    for source in plan.sources
                ),
            )
            return plan, manifest_export_identifier_ids(loaded.snapshot.document)
        except SearchablePdfExportError, RedactionError:
            raise
        except SignedSourcePolicyError:
            raise
        except ManifestRepositoryError as error:
            _raise_manifest_error(error)
        except BaseException:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MANIFEST_INVALID,
                "searchable.manifest_load",
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
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.INTERNAL_ERROR,
                    "searchable.identifier",
                )
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.INTERNAL_ERROR,
            "searchable.identifier",
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
        report: SearchablePdfVerificationReport,
    ) -> tuple[PdfExportHistoryRecord, PdfExportHistoryRetryReceipt]:
        try:
            recorded_at = self._clock()
            record = build_pdf_export_history_record_for_binding(
                binding,
                export_id=export_id,
                report=report.base_report,
                recorded_at=recorded_at,
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
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.history_record",
            )


def _history_binding(plan: SearchablePdfExportPlan) -> PdfExportHistoryPlanBinding:
    return PdfExportHistoryPlanBinding(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        semantic_fingerprint=plan.semantic_fingerprint,
        page_count=plan.page_count,
        unsigned_derivative=plan.requires_unsigned_derivative,
    )


def _require_candidate(
    plan: SearchablePdfExportPlan,
    candidate: VerifiedSearchablePdfCandidate,
) -> SearchablePdfVerificationReport:
    try:
        report = candidate.report
        reader = candidate.read
    except BaseException:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.candidate",
        )
    if (
        type(report) is not SearchablePdfVerificationReport
        or report.plan != plan
        or not callable(reader)
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.candidate",
        )
    return report


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.callback")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.callback")
    if cancelled:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.cancelled")
    if expired:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.deadline")


def _emit_progress(
    sink: SearchablePdfExportProgressSink,
    value: PdfExportProgress,
) -> None:
    try:
        sink(value)
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.progress")


def _emit_progress_after_commit(
    sink: SearchablePdfExportProgressSink,
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


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.source_cancelled")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.source_timed_out")
    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source")


def _raise_manifest_error(error: ManifestRepositoryError) -> Never:
    if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
        reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "export.manifest")
    reject_searchable_pdf_export(
        SearchablePdfExportErrorCode.MANIFEST_INVALID,
        "searchable.manifest",
    )
