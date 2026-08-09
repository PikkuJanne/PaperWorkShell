"""Toolkit-free orchestration for two-phase immutable source ingest."""

from __future__ import annotations

import math
import time
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol, Self, runtime_checkable
from uuid import UUID

from paperworkshell.domain.pdf_export import (
    PdfExportPlan,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityResult,
    reject_source_ingest,
    require_internal_id,
    require_source_display_name,
    require_source_timeout_seconds,
)

DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS: Final = 300.0
DEFAULT_SOURCE_INTEGRITY_TIMEOUT_SECONDS: Final = 60.0


class DeadlineProbe(Protocol):
    """Callable check for one immutable absolute monotonic deadline."""

    def __call__(self) -> bool:
        """Return whether the operation is at or beyond its deadline."""


class MonotonicClock(Protocol):
    """Trusted monotonic clock seam used to arm and test operation deadlines."""

    def __call__(self) -> float:
        """Return finite monotonic seconds from an unspecified reference point."""


class CancellationProbe(Protocol):
    """Callable cooperative-cancellation check used by every long phase."""

    def __call__(self) -> bool:
        """Return an exact boolean without blocking."""


class ProgressSink(Protocol):
    """Callable receiver for path-free bounded progress records."""

    def __call__(self, progress: SourceImportProgress, /) -> None:
        """Receive one already validated progress record."""


@runtime_checkable
class CommittedPdfSourceLease(Protocol):
    """One exact committed PDF source exposed only as path-free facts and a handle."""

    @property
    def source_id(self) -> UUID:
        """Return the source's opaque manifest identifier."""

    @property
    def media_type(self) -> PdfExportSourceMediaType:
        """Return the fixed PDF media type authenticated from the manifest."""

    @property
    def byte_size(self) -> int:
        """Return the exact authenticated source byte size."""

    @property
    def page_count(self) -> int:
        """Return the exact inspected page count."""

    @property
    def sha256(self) -> str:
        """Return the exact authenticated source digest."""

    @property
    def signature_state(self) -> PdfExportSourceSignatureState:
        """Return the exact conservative signature-marker state."""

    @property
    def encryption_state(self) -> PdfExportSourceEncryptionState:
        """Return the required unencrypted state."""

    @property
    def handle_value(self) -> int:
        """Return the read-only handle value intended solely for worker duplication."""


@runtime_checkable
class CommittedPdfSourceBatchLease(Protocol):
    """Retain one manifest state and all exact PDF sources required by an export."""

    @property
    def case_id(self) -> UUID:
        """Return the one case bound to this batch."""

    @property
    def manifest_revision(self) -> int:
        """Return the authenticated manifest revision."""

    @property
    def manifest_sha256(self) -> str:
        """Return the authenticated manifest digest."""

    @property
    def manifest_cas_token(self) -> str:
        """Return the identity-bound manifest compare-and-swap token."""

    @property
    def sources(self) -> tuple[CommittedPdfSourceLease, ...]:
        """Return sources in deterministic first-use order."""

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        """Re-hash every retained source after worker termination."""

    def __enter__(self) -> Self:
        """Retain every authenticated handle for one export attempt."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Close the complete batch on every exit path."""


@runtime_checkable
class CommittedPdfSourceBatchStore(Protocol):
    """Narrow storage port for a manifest-bound deterministic PDF-source batch."""

    def borrow_committed_pdf_sources(
        self,
        plan: PdfExportPlan,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> CommittedPdfSourceBatchLease:
        """Authenticate once and retain every exact source required by ``plan``."""


@runtime_checkable
class PreparedSourceLease(Protocol):
    """Storage-owned pending copy retained until commit or exact cleanup."""

    @property
    def summary(self) -> PreparedSourceSummary:
        """Return immutable verified facts about the pending case copy."""

    def __enter__(self) -> Self:
        """Retain the pending source and its case authority."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Clean an uncommitted pending copy and release retained authority."""

    def commit(
        self,
        inspection: CompletedSourceInspection,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceImportResult:
        """Publish source bytes first and atomically publish the manifest last."""


@runtime_checkable
class SourceInspector(Protocol):
    """Complete format-specific metadata for one exact prepared case copy."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        """Return metadata bound to the supplied pending identifier."""


@runtime_checkable
class SourceIngestStore(Protocol):
    """Narrow storage port for pending copies, commits, and integrity checks."""

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> PreparedSourceLease:
        """Return only after copy verification and terminal external-source recheck."""

    def verify_integrity(
        self,
        case_id: UUID,
        source_id: UUID,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceIntegrityResult:
        """Hash one exact committed case copy without mutating it or its manifest."""


class _MonotonicDeadline:
    __slots__ = ("_expires_at", "_monotonic")

    def __init__(self, timeout_seconds: float, monotonic: MonotonicClock) -> None:
        self._monotonic = monotonic
        expires_at = _read_monotonic(monotonic) + timeout_seconds
        if not math.isfinite(expires_at):
            reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, "source.deadline")
        self._expires_at = expires_at

    def __call__(self) -> bool:
        return _read_monotonic(self._monotonic) >= self._expires_at


class _SafeCancellationProbe:
    __slots__ = ("_callback", "_deadline")

    def __init__(self, callback: CancellationProbe | None, deadline: DeadlineProbe) -> None:
        self._callback = callback
        self._deadline = deadline

    def __call__(self) -> bool:
        _require_not_timed_out(self._deadline, context="source.cancellation")
        callback = self._callback
        if callback is None:
            return False
        try:
            value = callback()
        except BaseException:
            reject_source_ingest(
                SourceIngestErrorCode.CANCELLATION_FAILED,
                "source.cancellation",
            )
        if type(value) is not bool:
            reject_source_ingest(
                SourceIngestErrorCode.CANCELLATION_FAILED,
                "source.cancellation",
            )
        _require_not_timed_out(self._deadline, context="source.cancellation")
        return value


class _SafeProgressSink:
    __slots__ = ("_callback", "_deadline")

    def __init__(self, callback: ProgressSink | None, deadline: DeadlineProbe) -> None:
        self._callback = callback
        self._deadline = deadline

    def __call__(self, progress: SourceImportProgress) -> None:
        if type(progress) is not SourceImportProgress:
            reject_source_ingest(SourceIngestErrorCode.PROGRESS_FAILED, "source.progress")
        _require_not_timed_out(self._deadline, context="source.progress")
        callback = self._callback
        if callback is None:
            return
        try:
            callback(progress)
        except BaseException:
            reject_source_ingest(SourceIngestErrorCode.PROGRESS_FAILED, "source.progress")
        _require_not_timed_out(self._deadline, context="source.progress")


class SourceIngestService:
    """Order prepare, inspection, commit, and read-only integrity verification."""

    __slots__ = ("_inspector", "_monotonic", "_store")

    def __init__(
        self,
        store: SourceIngestStore,
        inspector: SourceInspector,
        *,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        self._store = store
        self._inspector = inspector
        self._monotonic = monotonic

    def import_source(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: CancellationProbe | None = None,
        progress: ProgressSink | None = None,
    ) -> SourceImportResult:
        """Commit only a fully copied, terminally rechecked, and inspected source."""

        selected_case = require_internal_id(case_id, context="import.case_id")
        if not isinstance(source_path, Path):
            reject_source_ingest(SourceIngestErrorCode.SOURCE_PATH_INVALID, "import.source_path")
        safe_display_name = require_source_display_name(display_name)
        timeout = require_source_timeout_seconds(
            timeout_seconds,
            context="import.timeout_seconds",
        )
        deadline = _MonotonicDeadline(timeout, self._monotonic)
        cancellation_probe = _SafeCancellationProbe(cancellation, deadline)
        progress_sink = _SafeProgressSink(progress, deadline)
        _require_not_cancelled(cancellation_probe, context="import.before_prepare")

        try:
            pending = self._store.prepare(
                selected_case,
                source_path,
                safe_display_name,
                deadline=deadline,
                cancellation=cancellation_probe,
                progress=progress_sink,
            )
        except SourceIngestError:
            raise
        except Exception:
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "import.prepare")

        phase = SourceIngestErrorCode.PREPARE_FAILED
        validated_result: SourceImportResult | None = None
        try:
            with pending as active:
                summary = _require_prepared_summary(active.summary, selected_case)
                _require_not_cancelled(cancellation_probe, context="import.before_inspection")

                phase = SourceIngestErrorCode.INSPECT_FAILED
                inspection = self._inspector.inspect(
                    summary,
                    deadline=deadline,
                    cancellation=cancellation_probe,
                    progress=progress_sink,
                )
                _require_matching_inspection(inspection, summary)
                _require_not_cancelled(cancellation_probe, context="import.before_commit")

                phase = SourceIngestErrorCode.COMMIT_FAILED
                result = active.commit(
                    inspection,
                    deadline=deadline,
                    cancellation=cancellation_probe,
                    progress=progress_sink,
                )
                validated_result = _require_matching_result(result, summary, inspection)
        except SourceIngestError:
            if validated_result is not None:
                return validated_result
            raise
        except Exception:
            if validated_result is not None:
                return validated_result
            reject_source_ingest(phase, f"import.{_phase_context(phase)}")
        if validated_result is None:
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "import.result")
        return validated_result

    def verify_integrity(
        self,
        case_id: UUID,
        source_id: UUID,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_INTEGRITY_TIMEOUT_SECONDS,
        cancellation: CancellationProbe | None = None,
        progress: ProgressSink | None = None,
    ) -> SourceIntegrityResult:
        """Return one hardened path-free integrity result without modifying case state."""

        selected_case = require_internal_id(case_id, context="verify.case_id")
        selected_source = require_internal_id(source_id, context="verify.source_id")
        if selected_case == selected_source:
            reject_source_ingest(SourceIngestErrorCode.IDENTIFIER_INVALID, "verify.identifiers")
        timeout = require_source_timeout_seconds(
            timeout_seconds,
            context="verify.timeout_seconds",
        )
        deadline = _MonotonicDeadline(timeout, self._monotonic)
        cancellation_probe = _SafeCancellationProbe(cancellation, deadline)
        progress_sink = _SafeProgressSink(progress, deadline)
        _require_not_cancelled(cancellation_probe, context="verify.before_start")
        try:
            result = self._store.verify_integrity(
                selected_case,
                selected_source,
                deadline=deadline,
                cancellation=cancellation_probe,
                progress=progress_sink,
            )
        except SourceIngestError:
            raise
        except Exception:
            reject_source_ingest(SourceIngestErrorCode.VERIFY_FAILED, "verify.storage")
        if (
            type(result) is not SourceIntegrityResult
            or result.case_id != selected_case
            or result.source_id != selected_source
        ):
            reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "verify.result")
        _require_not_timed_out(deadline, context="verify.after_storage")
        return result


def _require_not_cancelled(cancellation: CancellationProbe, *, context: str) -> None:
    if cancellation():
        reject_source_ingest(SourceIngestErrorCode.CANCELLED, context)


def _require_not_timed_out(deadline: DeadlineProbe, *, context: str) -> None:
    if deadline():
        reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, context)


def _read_monotonic(monotonic: MonotonicClock) -> float:
    try:
        value = monotonic()
    except BaseException:
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, "source.deadline_clock")
    if type(value) not in {int, float}:
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, "source.deadline_clock")
    try:
        observed = float(value)
    except OverflowError, ValueError:
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, "source.deadline_clock")
    if not math.isfinite(observed):
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, "source.deadline_clock")
    return observed


def _require_prepared_summary(
    value: object,
    expected_case_id: UUID,
) -> PreparedSourceSummary:
    if type(value) is not PreparedSourceSummary or value.case_id != expected_case_id:
        reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "import.prepared_result")
    return value


def _require_matching_inspection(
    value: object,
    source: PreparedSourceSummary,
) -> CompletedSourceInspection:
    if (
        type(value) is not CompletedSourceInspection
        or value.pending_id != source.pending_id
        or value.media_type is not source.media_type
    ):
        reject_source_ingest(
            SourceIngestErrorCode.INSPECTION_MISMATCH,
            "import.inspection_result",
        )
    return value


def _require_matching_result(
    value: object,
    source: PreparedSourceSummary,
    inspection: CompletedSourceInspection,
) -> SourceImportResult:
    if (
        type(value) is not SourceImportResult
        or value.case_id != source.case_id
        or value.source_id != source.source_id
        or value.media_type is not source.media_type
        or value.byte_size != source.byte_size
        or value.sha256 != source.sha256
        or len(value.page_ids) != inspection.page_count
    ):
        reject_source_ingest(SourceIngestErrorCode.RESULT_INVALID, "import.commit_result")
    return value


def _phase_context(code: SourceIngestErrorCode) -> str:
    if code is SourceIngestErrorCode.PREPARE_FAILED:
        return "prepare"
    if code is SourceIngestErrorCode.INSPECT_FAILED:
        return "inspect"
    return "commit"
