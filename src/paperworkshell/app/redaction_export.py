"""Exact-intent removal export coordination through a separate worker port.

The current private profile produces one affected PDF page. It never silently
omits other active pages, overlays, cleanup, or requested OCR. Ordinary export
guards remain in force. Independent release approval is checked at publication,
and cannot be supplied by a report, UI flag, environment variable, or caller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, cast
from uuid import UUID

from paperworkshell.app.pdf_export import _require_active
from paperworkshell.domain import redaction_verification as verification_policy
from paperworkshell.domain.manifest_repository import ManifestRepository, ManifestSnapshot
from paperworkshell.domain.page_coordinates import ClockwiseRotation, NormalizedPageBox
from paperworkshell.domain.page_operations import PageCommandHistory
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    reject_pdf_export,
)
from paperworkshell.domain.redaction import RedactionMarkCollection
from paperworkshell.domain.redaction_export import (
    REDACTION_EXPORT_REPORT_VERSION,
    RUNTIME_EXPORT_REPORT_VERSION,
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_export_candidate import (
    RedactionExportWorkerReport,
    validate_redaction_export_worker_report,
)
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    RedactionVerificationReport,
    VerificationStatus,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourceSignatureState,
    require_signed_source_modification_confirmation,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode


class RemovalSourceHandle(Protocol):
    @property
    def value(self) -> int: ...


class RemovalSource(Protocol):
    """Retained source lease; the concrete broker checks reduced handle authority."""

    @property
    def handle(self) -> RemovalSourceHandle: ...

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def page_id(self) -> UUID: ...

    @property
    def source_page_index(self) -> int: ...

    @property
    def source_page_count(self) -> int: ...

    @property
    def source_byte_size(self) -> int: ...

    @property
    def source_sha256(self) -> str: ...

    def revalidate(
        self, *, deadline: Callable[[], bool], cancellation: Callable[[], bool]
    ) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class RemovalSourceStore(Protocol):
    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> RemovalSource: ...


class RemovalCandidate(Protocol):
    @property
    def report(self) -> RedactionExportWorkerReport: ...

    def read(self, size: int, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class RemovalExportBroker(Protocol):
    """Only this operation-scoped broker may issue and authenticate candidates."""

    def prepare(
        self,
        request: RedactionReconstructionRequest,
        source: RemovalSource,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[PdfExportProgress], None],
    ) -> RemovalCandidate: ...

    def validate_candidate(
        self,
        candidate: RemovalCandidate,
        request: RedactionReconstructionRequest,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> None: ...


class RemovalDestination(Protocol):
    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None: ...

    def commit(
        self,
        destination: Path,
        source: RemovalCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> object | None: ...


def _release_approved() -> bool:
    # This frozen build-time switch has no caller-supplied approval input.
    return verification_policy.runtime_release_qualified()


def _verification_ready(profile_id: str, report: RedactionVerificationReport) -> bool:
    if profile_id == RUNTIME_VERIFICATION_PROFILE:
        return report.runtime_ready
    return profile_id == VERIFICATION_PROFILE and report.release_ready


def _request(snapshot: ManifestSnapshot) -> RedactionReconstructionRequest:
    if type(snapshot) is not ManifestSnapshot:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.review")
    document = snapshot.document
    projection = PageCommandHistory.from_snapshot(snapshot).projection
    marks = RedactionMarkCollection.from_manifest_document(document).marks
    # A private one-page profile must never masquerade as a complete packet.
    if len(projection.page_sequence) != 1:
        reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "removal.page_scope")
    page_id = projection.page_sequence[0]
    selected = tuple(mark for mark in marks if mark.page_id == page_id)
    if not selected:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.marks")
    state = projection.state_for(page_id)
    overlays = cast(list[dict[str, object]], document["overlays"])
    if (
        any(record["page_id"] == str(page_id) for record in overlays)
        or state.deskew_angle_millidegrees_clockwise != 0
        or state.contrast_factor_millionths != 1_000_000
        or state.background_cleanup_strength_millionths != 0
    ):
        reject_pdf_export(PdfExportErrorCode.TRANSFORM_UNSUPPORTED, "removal.processing_intent")
    source = next(
        record
        for record in cast(list[dict[str, object]], document["sources"])
        if record["source_id"] == str(state.page.source_id)
    )
    if (
        source["detected_media_type"] != "application/pdf"
        or source["import_state"] != "complete"
        or source["encryption_state"] != "unencrypted"
        or source["signature_state"] not in {"none_detected", "markers_detected"}
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "removal.source")
    require_signed_source_modification_confirmation(
        snapshot,
        (
            SignedSourceModificationSource(
                state.page.source_id, SignedSourceSignatureState(source["signature_state"])
            ),
        ),
    )
    return RedactionReconstructionRequest(
        case_id=snapshot.case_id,
        manifest_revision=snapshot.revision,
        source_id=state.page.source_id,
        page_id=page_id,
        source_sha256=cast(str, source["sha256"]),
        source_byte_size=cast(int, source["byte_size"]),
        source_page_index=state.page.source_page_index,
        source_page_count=cast(int, source["page_count"]),
        marks=selected,
        crop=state.crop or NormalizedPageBox.full_page(),
        rotation=ClockwiseRotation.from_degrees(state.quarter_turns_clockwise * 90),
    )


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedRedactionExport:
    """A review of exact protected state; cannot authorize publication by itself."""

    snapshot: ManifestSnapshot = field(repr=False)
    request: RedactionReconstructionRequest = field(repr=False)

    def __post_init__(self) -> None:
        expected = _request(self.snapshot)
        if type(self.request) is not RedactionReconstructionRequest or self.request != expected:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "removal.review_binding")

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot) -> Self:
        return cls(snapshot, _request(snapshot))

    def __repr__(self) -> str:
        return "ReviewedRedactionExport(<protected intent>)"


class RedactionExportService:
    """Prepare, authenticate and gate each attempt, including every fresh retry.

    The release switch stays false until PWS-0608. Missing independent methods
    stay unknown for these exact bytes; historical viewer/OCR observations never
    become runtime testimony for a newly generated candidate.
    """

    def __init__(
        self,
        manifests: ManifestRepository,
        sources: RemovalSourceStore,
        broker: RemovalExportBroker,
        destination: RemovalDestination,
    ) -> None:
        self._manifests = manifests
        self._sources = sources
        self._broker = broker
        self._destination = destination

    def export(
        self,
        review: ReviewedRedactionExport,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[PdfExportProgress], None] = lambda _value: None,
    ) -> RedactionExportReport:
        if type(review) is not ReviewedRedactionExport:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.service_review")
        review.__post_init__()
        if not isinstance(destination, Path) or not destination.is_absolute():
            reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "removal.destination")
        verification: RedactionVerificationReport | None = None
        verification_profile = VERIFICATION_PROFILE

        def report(
            code: RedactionExportCode, status: RedactionExportStatus = RedactionExportStatus.BLOCKED
        ) -> RedactionExportReport:
            return RedactionExportReport(
                review.snapshot.case_id,
                review.snapshot.revision,
                (review.request.page_id,),
                verification,
                status,
                code,
                report_version=(
                    RUNTIME_EXPORT_REPORT_VERSION
                    if verification_profile == RUNTIME_VERIFICATION_PROFILE
                    else REDACTION_EXPORT_REPORT_VERSION
                ),
            )

        committed = False
        destination_uncertain = False
        try:
            _require_active(deadline, cancellation)
            self._require_current(review)
            with self._sources.borrow_committed_pdf_page(
                review.request.case_id,
                review.request.page_id,
                deadline=deadline,
                cancellation=cancellation,
            ) as source:
                source.revalidate(deadline=deadline, cancellation=cancellation)
                with self._broker.prepare(
                    review.request,
                    source,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                ) as candidate:
                    self._broker.validate_candidate(
                        candidate,
                        review.request,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    bound = candidate.report
                    validate_redaction_export_worker_report(review.request, bound)
                    verification_profile = bound.verification_request.profile_id
                    reviewed_source = next(
                        record
                        for record in cast(
                            list[dict[str, object]], review.snapshot.document["sources"]
                        )
                        if record["source_id"] == str(review.request.source_id)
                    )
                    if bound.reconstruction.source_signature_detected != (
                        reviewed_source["signature_state"] == "markers_detected"
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.RESULT_INVALID, "removal.signature_binding"
                        )
                    bound_record = bound.to_record()
                    verification = RedactionVerificationReport.from_record(
                        bound.verification.to_record()
                    )
                    output_byte_size, output_sha256 = bound.output_byte_size, bound.output_sha256
                    _require_active(deadline, cancellation)
                    self._require_current(review)
                    source.revalidate(deadline=deadline, cancellation=cancellation)
                    if any(item.status is VerificationStatus.FAIL for item in verification.methods):
                        return report(RedactionExportCode.VERIFICATION_FAILED)
                    if not _verification_ready(verification_profile, verification):
                        return report(RedactionExportCode.VERIFICATION_UNKNOWN)
                    if not _release_approved():
                        return report(RedactionExportCode.RELEASE_PENDING)
                    self._destination.require_capacity(
                        destination, estimated_byte_size=output_byte_size
                    )
                    # No report can substitute for release approval, exact current
                    # intent, retained upstream provenance and fresh buffer checks.
                    self._broker.validate_candidate(
                        candidate,
                        review.request,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    validate_redaction_export_worker_report(review.request, candidate.report)
                    if candidate.report.to_record() != bound_record:
                        reject_pdf_export(
                            PdfExportErrorCode.RESULT_INVALID, "removal.candidate_changed"
                        )
                    _require_active(deadline, cancellation)
                    self._require_current(review)
                    source.revalidate(deadline=deadline, cancellation=cancellation)
                    if not _release_approved():
                        return report(RedactionExportCode.RELEASE_PENDING)
                    self._broker.validate_candidate(
                        candidate, review.request, deadline=deadline, cancellation=cancellation
                    )
                    validate_redaction_export_worker_report(review.request, candidate.report)
                    if candidate.report.to_record() != bound_record or not _verification_ready(
                        candidate.report.verification_request.profile_id,
                        candidate.report.verification,
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.RESULT_INVALID, "removal.final_evidence"
                        )
                    try:
                        result = self._destination.commit(
                            destination,
                            candidate,
                            expected_byte_size=output_byte_size,
                            expected_sha256=output_sha256,
                            deadline=deadline,
                            cancellation=cancellation,
                        )
                    except PdfExportError as error:
                        if error.code is PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN:
                            destination_uncertain = True
                        raise
                    if result is not None:
                        destination_uncertain = True
                        return report(
                            RedactionExportCode.DESTINATION_UNCERTAIN,
                            RedactionExportStatus.UNCERTAIN,
                        )
                    committed = True
        except BaseException as error:
            if not committed and not destination_uncertain:
                if isinstance(error, SourceIngestError):
                    if error.code in {
                        SourceIngestErrorCode.CANCELLED,
                        SourceIngestErrorCode.TIMED_OUT,
                    }:
                        code = (
                            RedactionExportCode.CANCELLED
                            if error.code is SourceIngestErrorCode.CANCELLED
                            else RedactionExportCode.TIMED_OUT
                        )
                        return report(code, RedactionExportStatus.NOT_COMMITTED)
                    if error.code is SourceIngestErrorCode.MANIFEST_CONFLICT:
                        return report(RedactionExportCode.STALE_INTENT)
                if isinstance(error, PdfExportError):
                    if (
                        error.code is PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN
                        and verification is not None
                        and _verification_ready(verification_profile, verification)
                    ):
                        return report(
                            RedactionExportCode.DESTINATION_UNCERTAIN,
                            RedactionExportStatus.UNCERTAIN,
                        )
                    if error.code in {PdfExportErrorCode.CANCELLED, PdfExportErrorCode.TIMED_OUT}:
                        code = (
                            RedactionExportCode.CANCELLED
                            if error.code is PdfExportErrorCode.CANCELLED
                            else RedactionExportCode.TIMED_OUT
                        )
                        return report(code, RedactionExportStatus.NOT_COMMITTED)
                    if error.code is PdfExportErrorCode.PLAN_STALE:
                        return report(RedactionExportCode.STALE_INTENT)
                    if (
                        verification is not None
                        and _verification_ready(verification_profile, verification)
                        and error.code
                        in {
                            PdfExportErrorCode.DESTINATION_EXISTS,
                            PdfExportErrorCode.DESTINATION_FAILED,
                            PdfExportErrorCode.DESTINATION_CAPACITY_EXCEEDED,
                            PdfExportErrorCode.DESTINATION_INVALID,
                        }
                    ):
                        code = (
                            RedactionExportCode.DESTINATION_EXISTS
                            if error.code is PdfExportErrorCode.DESTINATION_EXISTS
                            else RedactionExportCode.DESTINATION_FAILED
                        )
                        return report(code, RedactionExportStatus.NOT_COMMITTED)
                return report(RedactionExportCode.RESULT_INVALID)
        # Cleanup cannot replace a known indeterminate publication outcome.
        if destination_uncertain:
            return report(
                RedactionExportCode.DESTINATION_UNCERTAIN,
                RedactionExportStatus.UNCERTAIN,
            )
        # A context manager cannot suppress an exception and fabricate success.
        if not committed:
            return report(RedactionExportCode.RESULT_INVALID)
        return report(RedactionExportCode.COMPLETE, RedactionExportStatus.COMMITTED)

    def _require_current(self, review: ReviewedRedactionExport) -> None:
        loaded = self._manifests.load(review.snapshot.case_id)
        if loaded.snapshot != review.snapshot or _request(loaded.snapshot) != review.request:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "removal.current_intent")
