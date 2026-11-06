"""Publication authority, fail-closed retries and sanitized removal export facts."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

import paperworkshell.app.redaction_export as service_module
from paperworkshell.app.redaction_export import (
    RedactionExportService,
    RemovalCandidate,
    RemovalSource,
    ReviewedRedactionExport,
)
from paperworkshell.domain.manifest_repository import ManifestRepository
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    reject_pdf_export,
)
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_export_candidate import RedactionExportWorkerReport
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
)
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationInventory,
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
)
from paperworkshell.domain.redaction_verification import (
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from tests.unit.test_organizer_backend import PAGE_IDS, _document, _Repository, _snapshot

_BODY = b"%PDF-1.7\nSYNTHETIC_PRIVATE_CANDIDATE_BYTES\n%%EOF\n"
_BODY_DIGEST = hashlib.sha256(_BODY).hexdigest()
_REFERENCE_DIGEST = hashlib.sha256(b"SYNTHETIC_PRIVATE_REFERENCE_BYTES").hexdigest()
_PRIVATE = "SYNTHETIC_PRIVATE_REPORT_VALUE"


def _review() -> ReviewedRedactionExport:
    document = _document(page_count=1)
    document["redaction_marks"] = [
        RedactionMark(
            UUID("70000000-0000-4000-8000-000000000222"),
            PAGE_IDS[0],
            NormalizedPageBox(100_000, 100_000, 200_000, 200_000),
        ).to_manifest_record()
    ]
    return ReviewedRedactionExport.from_snapshot(_snapshot(document))


def _worker_report(
    request: RedactionReconstructionRequest,
    *,
    method: VerificationMethod | None = None,
    status: VerificationStatus = VerificationStatus.PASS,
) -> RedactionExportWorkerReport:
    reconstruction = RedactionReconstructionReport(
        request.fingerprint,
        request.page_id,
        request.source_sha256,
        _REFERENCE_DIGEST,
        50,
        300,
        300,
        72.0,
        72.0,
        len(request.marks),
    )
    sanitization_request = RedactionSanitizationRequest(
        request.case_id,
        UUID("80000000-0000-4000-8000-000000000001"),
        reconstruction.output_sha256,
        reconstruction.output_byte_size,
        1,
    )
    sanitization = RedactionSanitizationReport(
        sanitization_request.fingerprint,
        reconstruction.output_sha256,
        _BODY_DIGEST,
        len(_BODY),
        1,
        RedactionSanitizationInventory(),
        RedactionSanitizationInventory(),
    )
    verification_request = RedactionVerificationRequest(
        _BODY_DIGEST,
        len(_BODY),
        reconstruction.output_sha256,
        reconstruction.output_byte_size,
        1,
        (0,),
        request.fingerprint,
    )
    methods = tuple(
        VerificationMethodResult(
            item,
            status if item is method else VerificationStatus.PASS,
            (
                VerificationCode.CANARY
                if status is VerificationStatus.FAIL
                else VerificationCode.MISSING
                if status is VerificationStatus.UNKNOWN
                else VerificationCode.CLEAR
            )
            if item is method
            else VerificationCode.CLEAR,
        )
        for item in VerificationMethod
    )
    verification = RedactionVerificationReport(
        verification_request.fingerprint, _BODY_DIGEST, methods
    )
    return RedactionExportWorkerReport(
        UUID("90000000-0000-4000-8000-000000000001"),
        UUID("90000000-0000-4000-8000-000000000002"),
        reconstruction,
        sanitization_request,
        sanitization,
        verification_request,
        verification,
    )


@dataclass(frozen=True, slots=True)
class _SourceHandle:
    value: int = 42


class _Source:
    def __init__(self) -> None:
        request = _review().request
        self.handle = _SourceHandle()
        self.case_id = request.case_id
        self.source_id = request.source_id
        self.page_id = request.page_id
        self.source_page_index = request.source_page_index
        self.source_page_count = request.source_page_count
        self.source_byte_size = request.source_byte_size
        self.source_sha256 = request.source_sha256
        self.closed = False
        self.revalidations = 0
        self.on_revalidate: Callable[[int], None] = lambda _count: None
        self.suppress = False
        self.exit_error: Exception | None = None

    def revalidate(self, *, deadline: Callable[[], bool], cancellation: Callable[[], bool]) -> None:
        self.revalidations += 1
        self.on_revalidate(self.revalidations)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.closed = True
        if self.exit_error is not None:
            raise self.exit_error
        return self.suppress


class _Sources:
    def __init__(self) -> None:
        self.leases: list[_Source] = []
        self.configure: Callable[[_Source], None] = lambda _source: None

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> _Source:
        source = _Source()
        self.configure(source)
        self.leases.append(source)
        return source


class _Candidate:
    def __init__(self, report: RedactionExportWorkerReport) -> None:
        self.stream = io.BytesIO(_BODY)
        self.report = report
        self.suppress = False
        self.exit_error: Exception | None = None

    @property
    def closed(self) -> bool:
        return self.stream.closed

    def tell(self) -> int:
        return self.stream.tell()

    def getvalue(self) -> bytes:
        return self.stream.getvalue()

    def seek(self, position: int) -> int:
        return self.stream.seek(position)

    def write(self, body: bytes) -> int:
        return self.stream.write(body)

    def read(self, size: int, /) -> bytes:
        return self.stream.read(size)

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.close()
        if self.exit_error is not None:
            raise self.exit_error
        return self.suppress


class _Broker:
    def __init__(self) -> None:
        self.candidates: list[_Candidate] = []
        self.requests: list[RedactionReconstructionRequest] = []
        self.validations = 0
        self.report_factory: Callable[
            [RedactionReconstructionRequest], RedactionExportWorkerReport
        ] = _worker_report
        self.configure: Callable[[_Candidate], None] = lambda _candidate: None
        self.prepare_error: Exception | None = None
        self.validate_error: Exception | None = None

    def prepare(
        self,
        request: RedactionReconstructionRequest,
        source: RemovalSource,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[PdfExportProgress], None],
    ) -> _Candidate:
        self.requests.append(request)
        if self.prepare_error is not None:
            raise self.prepare_error
        candidate = _Candidate(self.report_factory(request))
        self.configure(candidate)
        self.candidates.append(candidate)
        return candidate

    def validate_candidate(
        self,
        candidate: RemovalCandidate,
        request: RedactionReconstructionRequest,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> None:
        self.validations += 1
        if self.validate_error is not None:
            raise self.validate_error
        if not self.candidates or candidate is not self.candidates[-1]:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "test.candidate_authority")
        selected = self.candidates[-1]
        if (
            selected.closed
            or selected.tell() != 0
            or hashlib.sha256(selected.getvalue()).hexdigest() != _BODY_DIGEST
            or selected.report.output_sha256 != _BODY_DIGEST
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "test.candidate_bytes")


class _Destination:
    def __init__(self) -> None:
        self.capacity_calls = 0
        self.commit_calls = 0
        self.on_commit: Callable[[Path], None] = lambda _destination: None
        self.on_capacity: Callable[[], None] = lambda: None
        self.commit_error: Exception | None = None
        self.commit_result: object | None = None

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        assert estimated_byte_size == len(_BODY)
        self.capacity_calls += 1
        self.on_capacity()

    def commit(
        self,
        destination: Path,
        source: RemovalCandidate,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> object | None:
        self.commit_calls += 1
        self.on_commit(destination)
        if self.commit_error is not None:
            raise self.commit_error
        if self.commit_result is not None:
            return self.commit_result
        body = source.read(expected_byte_size)
        if len(body) != expected_byte_size or hashlib.sha256(body).hexdigest() != expected_sha256:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "test.destination_digest")
        try:
            with destination.open("xb") as output:
                output.write(body)
        except FileExistsError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "test.no_replace")
        assert destination.read_bytes() == body
        return None


class _Harness:
    def __init__(self) -> None:
        self.review = _review()
        self.repository = _Repository(self.review.snapshot.document)
        self.sources = _Sources()
        self.broker = _Broker()
        self.destination = _Destination()
        self.service = RedactionExportService(
            cast(ManifestRepository, self.repository),
            self.sources,
            self.broker,
            self.destination,
        )

    def export(
        self,
        destination: Path,
        *,
        deadline: Callable[[], bool] = lambda: False,
        cancellation: Callable[[], bool] = lambda: False,
    ) -> RedactionExportReport:
        return self.service.export(
            self.review, destination, deadline=deadline, cancellation=cancellation
        )

    def assert_closed(self) -> None:
        assert all(source.closed for source in self.sources.leases)
        assert all(candidate.closed for candidate in self.broker.candidates)


@pytest.mark.parametrize("method", list(VerificationMethod))
@pytest.mark.parametrize("status", [VerificationStatus.FAIL, VerificationStatus.UNKNOWN])
def test_each_failed_or_unknown_method_blocks_even_simulated_approved_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: VerificationMethod,
    status: VerificationStatus,
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    harness.broker.report_factory = lambda request: _worker_report(
        request, method=method, status=status
    )
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.status is RedactionExportStatus.BLOCKED
    assert result.code is (
        RedactionExportCode.VERIFICATION_FAILED
        if status is VerificationStatus.FAIL
        else RedactionExportCode.VERIFICATION_UNKNOWN
    )
    assert harness.destination.commit_calls == harness.destination.capacity_calls == 0
    assert not path.exists()
    harness.assert_closed()


def test_allpass_evidence_cannot_open_independent_release_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: False)
    harness = _Harness()
    result = harness.export(tmp_path / "result.pdf")
    assert result.code is RedactionExportCode.RELEASE_PENDING
    assert result.verification is not None and result.verification.release_ready
    assert harness.destination.commit_calls == harness.destination.capacity_calls == 0
    assert not (tmp_path / "result.pdf").exists()
    harness.assert_closed()


def test_simulated_approved_commit_then_failed_retry_preserves_exact_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    path = tmp_path / "result.pdf"
    original_snapshot = harness.repository.current
    first = harness.export(path)
    assert first.status is RedactionExportStatus.COMMITTED
    assert first.code is RedactionExportCode.COMPLETE and path.read_bytes() == _BODY
    previous = path.read_bytes()
    harness.broker.report_factory = lambda request: _worker_report(
        request, method=VerificationMethod.TEXT, status=VerificationStatus.FAIL
    )
    second = harness.export(path)
    assert second.code is RedactionExportCode.VERIFICATION_FAILED
    assert path.read_bytes() == previous
    assert harness.destination.commit_calls == 1
    assert len(harness.broker.candidates) == 2
    assert harness.broker.candidates[0] is not harness.broker.candidates[1]
    assert harness.repository.current == original_snapshot and harness.repository.save_calls == 0
    harness.assert_closed()


def test_simulated_approved_retry_cannot_replace_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    path = tmp_path / "result.pdf"
    path.write_bytes(b"SYNTHETIC_PREVIOUS_VERIFIED_OUTPUT")
    result = harness.export(path)
    assert result.status is RedactionExportStatus.NOT_COMMITTED
    assert result.code is RedactionExportCode.DESTINATION_EXISTS
    assert path.read_bytes() == b"SYNTHETIC_PREVIOUS_VERIFIED_OUTPUT"
    harness.assert_closed()


@pytest.mark.parametrize("moment", ["before", "capacity"])
def test_changed_saved_case_blocks_before_commit_and_retry_uses_current_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()

    def change() -> None:
        document = harness.repository.current.document
        document["revision"] = 1
        harness.repository.current = _snapshot(document)

    if moment == "before":
        change()
    else:
        harness.destination.on_capacity = change
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.code is RedactionExportCode.STALE_INTENT
    assert harness.destination.commit_calls == 0 and not path.exists()
    harness.assert_closed()


def test_forged_review_request_rejected_without_borrowing_or_worker(tmp_path: Path) -> None:
    harness = _Harness()
    object.__setattr__(
        harness.review, "request", replace(harness.review.request, source_sha256="d" * 64)
    )
    with pytest.raises(PdfExportError):
        harness.export(tmp_path / "result.pdf")
    assert harness.broker.requests == [] and harness.sources.leases == []


@pytest.mark.parametrize(
    "forgery",
    ["reconstruction", "sanitization_source", "reference", "intent", "profile", "verification"],
)
def test_forged_upstream_chain_cannot_reach_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forgery: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()

    def forge(candidate: _Candidate) -> None:
        report = candidate.report
        if forgery == "reconstruction":
            object.__setattr__(report.reconstruction, "request_sha256", "d" * 64)
        elif forgery == "sanitization_source":
            object.__setattr__(report.sanitization_request, "source_sha256", "d" * 64)
        elif forgery == "reference":
            object.__setattr__(report.verification_request, "reference_sha256", "d" * 64)
        elif forgery == "intent":
            object.__setattr__(report.verification_request, "intent_sha256", "d" * 64)
        elif forgery == "profile":
            object.__setattr__(
                report.verification_request, "profile_id", VIEWER_VERIFICATION_PROFILE
            )
        else:
            object.__setattr__(report.verification, "request_sha256", "d" * 64)

    harness.broker.configure = forge
    result = harness.export(tmp_path / "result.pdf")
    assert result.code is RedactionExportCode.RESULT_INVALID
    assert harness.destination.commit_calls == 0
    harness.assert_closed()


def test_candidate_changed_after_capacity_is_authenticated_again_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()

    def alter() -> None:
        candidate = harness.broker.candidates[-1]
        candidate.seek(0)
        candidate.write(b"ALTERED")
        candidate.seek(0)

    harness.destination.on_capacity = alter
    result = harness.export(tmp_path / "result.pdf")
    assert result.code is RedactionExportCode.RESULT_INVALID
    assert harness.broker.validations == 2
    assert harness.destination.commit_calls == 0
    harness.assert_closed()


@pytest.mark.parametrize("code", [PdfExportErrorCode.CANCELLED, PdfExportErrorCode.TIMED_OUT])
@pytest.mark.parametrize("moment", ["before", "worker", "before_commit"])
def test_cancellation_and_timeout_do_not_publish_and_cleanup_completed_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: PdfExportErrorCode,
    moment: str,
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    active = moment == "before"

    def probe() -> bool:
        return active

    if moment == "worker":
        harness.broker.prepare_error = PdfExportError(code, "test.cancelled")
    elif moment == "before_commit":

        def cancel() -> None:
            nonlocal active
            active = True

        harness.destination.on_capacity = cancel
    result = harness.export(
        tmp_path / "result.pdf",
        cancellation=probe if code is PdfExportErrorCode.CANCELLED else lambda: False,
        deadline=probe if code is PdfExportErrorCode.TIMED_OUT else lambda: False,
    )
    assert result.code is (
        RedactionExportCode.CANCELLED
        if code is PdfExportErrorCode.CANCELLED
        else RedactionExportCode.TIMED_OUT
    )
    assert result.status is RedactionExportStatus.NOT_COMMITTED
    assert harness.destination.commit_calls == 0
    harness.assert_closed()


@pytest.mark.parametrize(
    "source_code,expected_code,expected_status",
    [
        (
            SourceIngestErrorCode.CANCELLED,
            RedactionExportCode.CANCELLED,
            RedactionExportStatus.NOT_COMMITTED,
        ),
        (
            SourceIngestErrorCode.TIMED_OUT,
            RedactionExportCode.TIMED_OUT,
            RedactionExportStatus.NOT_COMMITTED,
        ),
        (
            SourceIngestErrorCode.MANIFEST_CONFLICT,
            RedactionExportCode.STALE_INTENT,
            RedactionExportStatus.BLOCKED,
        ),
    ],
)
@pytest.mark.parametrize("revalidation", [1, 2, 3])
def test_retained_source_errors_preserve_cancellation_timeout_and_stale_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_code: SourceIngestErrorCode,
    expected_code: RedactionExportCode,
    expected_status: RedactionExportStatus,
    revalidation: int,
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()

    def configure(source: _Source) -> None:
        def fail(count: int) -> None:
            if count == revalidation:
                raise SourceIngestError(source_code, "test.retained_source")

        source.on_revalidate = fail

    harness.sources.configure = configure
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.code is expected_code
    assert result.status is expected_status
    assert not path.exists() and harness.destination.commit_calls == 0
    harness.assert_closed()


@pytest.mark.parametrize("suppressor", ["source", "candidate"])
def test_context_exception_suppression_cannot_fabricate_complete_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suppressor: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    if suppressor == "source":
        harness.sources.configure = lambda source: setattr(source, "suppress", True)
        harness.broker.prepare_error = RuntimeError(_PRIVATE)
    else:
        harness.broker.configure = lambda candidate: setattr(candidate, "suppress", True)
        harness.broker.validate_error = RuntimeError(_PRIVATE)
    result = harness.export(tmp_path / "result.pdf")
    assert result.code is RedactionExportCode.RESULT_INVALID
    assert result.status is RedactionExportStatus.BLOCKED
    assert harness.destination.commit_calls == 0
    assert _PRIVATE not in result.user_text + result.to_audit_text() + repr(result)
    harness.assert_closed()


def test_failed_attempt_does_not_reuse_candidate_or_verification_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: False)
    harness = _Harness()
    harness.broker.report_factory = lambda request: _worker_report(
        request, method=VerificationMethod.IMAGES, status=VerificationStatus.FAIL
    )
    first = harness.export(tmp_path / "result.pdf")
    harness.broker.report_factory = _worker_report
    second = harness.export(tmp_path / "result.pdf")
    assert first.code is RedactionExportCode.VERIFICATION_FAILED
    assert second.code is RedactionExportCode.RELEASE_PENDING
    assert len(harness.broker.requests) == len(harness.broker.candidates) == 2
    assert len(harness.sources.leases) == 2
    assert harness.repository.load_calls >= 4
    assert harness.destination.commit_calls == 0
    harness.assert_closed()


@pytest.mark.parametrize("kind", ["result", "error"])
def test_uncertain_destination_never_claims_complete_or_automatic_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    if kind == "result":
        harness.destination.commit_result = object()
    else:
        harness.destination.commit_error = PdfExportError(
            PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN, "test.destination"
        )
    result = harness.export(tmp_path / "result.pdf")
    assert result.status is RedactionExportStatus.UNCERTAIN
    assert result.code is RedactionExportCode.DESTINATION_UNCERTAIN
    assert not result.retry_allowed
    harness.assert_closed()


def test_reports_never_contain_candidate_content_source_digest_or_raw_failure(
    tmp_path: Path,
) -> None:
    harness = _Harness()
    harness.broker.prepare_error = RuntimeError(
        _PRIVATE + " C:\\private\\Synthetic applicant.pdf name@example.invalid"
    )
    result = harness.export(tmp_path / "result.pdf")
    text = result.user_text + result.to_audit_text() + repr(result)
    for token in (
        _PRIVATE,
        "C:\\private",
        "Synthetic applicant.pdf",
        "name@example.invalid",
        "SYNTHETIC_PRIVATE_CANDIDATE_BYTES",
        _BODY_DIGEST,
        _REFERENCE_DIGEST,
        harness.review.request.source_sha256,
    ):
        assert token not in text
    assert str(PAGE_IDS[0]) in result.to_audit_text()


@pytest.mark.parametrize("closer", ["candidate", "source"])
def test_cleanup_exception_cannot_convert_unpublished_candidate_into_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closer: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: False)
    harness = _Harness()
    if closer == "candidate":
        harness.broker.configure = lambda candidate: setattr(
            candidate, "exit_error", RuntimeError(_PRIVATE)
        )
    else:
        harness.sources.configure = lambda source: setattr(
            source, "exit_error", RuntimeError(_PRIVATE)
        )
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.code is RedactionExportCode.RESULT_INVALID
    assert result.status is RedactionExportStatus.BLOCKED
    assert not path.exists() and harness.destination.commit_calls == 0
    assert _PRIVATE not in result.user_text + result.to_audit_text()
    harness.assert_closed()


@pytest.mark.parametrize("closer", ["candidate", "source"])
def test_cleanup_exception_after_known_commit_preserves_verified_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closer: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    if closer == "candidate":
        harness.broker.configure = lambda candidate: setattr(
            candidate, "exit_error", RuntimeError(_PRIVATE)
        )
    else:
        harness.sources.configure = lambda source: setattr(
            source, "exit_error", RuntimeError(_PRIVATE)
        )
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.code is RedactionExportCode.COMPLETE
    assert result.status is RedactionExportStatus.COMMITTED
    assert path.read_bytes() == _BODY
    assert harness.destination.capacity_calls == harness.destination.commit_calls == 1
    assert len(harness.sources.leases) == len(harness.broker.candidates) == 1
    assert not result.retry_allowed
    assert _PRIVATE not in result.user_text + result.to_audit_text() + repr(result)
    harness.assert_closed()


@pytest.mark.parametrize("kind", ["result", "error"])
@pytest.mark.parametrize(
    "cleanup",
    [
        "candidate_error",
        "source_error",
        "candidate_suppresses",
        "source_suppresses",
        "candidate_error_source_suppresses",
    ],
)
def test_uncertain_publication_survives_cleanup_exception_or_suppression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, cleanup: str
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    path = tmp_path / "result.pdf"

    def publish_before_uncertain(destination: Path) -> None:
        assert destination == path
        with destination.open("xb") as output:
            output.write(_BODY)

    harness.destination.on_commit = publish_before_uncertain
    if kind == "result":
        harness.destination.commit_result = object()
    else:
        harness.destination.commit_error = PdfExportError(
            PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN, "test.destination"
        )
    if cleanup in {"candidate_error", "candidate_error_source_suppresses"}:
        harness.broker.configure = lambda candidate: setattr(
            candidate, "exit_error", RuntimeError(_PRIVATE)
        )
    elif cleanup == "source_error":
        harness.sources.configure = lambda source: setattr(
            source, "exit_error", RuntimeError(_PRIVATE)
        )
    elif cleanup == "candidate_suppresses":
        harness.broker.configure = lambda candidate: setattr(candidate, "suppress", True)
    if cleanup in {"source_suppresses", "candidate_error_source_suppresses"}:
        harness.sources.configure = lambda source: setattr(source, "suppress", True)
    result = harness.export(path)
    assert result.code is RedactionExportCode.DESTINATION_UNCERTAIN
    assert result.status is RedactionExportStatus.UNCERTAIN
    assert not result.retry_allowed
    assert harness.destination.capacity_calls == harness.destination.commit_calls == 1
    assert len(harness.sources.leases) == len(harness.broker.candidates) == 1
    assert path.read_bytes() == _BODY
    assert _PRIVATE not in result.user_text + result.to_audit_text() + repr(result)
    assert "No export was committed" not in result.safe_message
    harness.assert_closed()


def test_one_page_profile_cannot_silently_drop_another_active_page() -> None:
    document = _document(page_count=2)
    document["redaction_marks"] = _review().snapshot.document["redaction_marks"]
    with pytest.raises(PdfExportError) as caught:
        ReviewedRedactionExport.from_snapshot(_snapshot(document))
    assert caught.value.code is PdfExportErrorCode.TRANSFORM_UNSUPPORTED


def test_missing_removal_intent_cannot_enter_private_export_pipeline() -> None:
    with pytest.raises(PdfExportError):
        ReviewedRedactionExport.from_snapshot(_snapshot(_document(page_count=1)))


def test_release_approval_rechecked_after_preparation_and_destination_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness()
    approved = True
    monkeypatch.setattr(service_module, "_release_approved", lambda: approved)

    def revoke() -> None:
        nonlocal approved
        approved = False

    harness.destination.on_capacity = revoke
    result = harness.export(tmp_path / "result.pdf")
    assert result.code is RedactionExportCode.RELEASE_PENDING
    assert harness.destination.commit_calls == 0
    harness.assert_closed()


def test_last_source_check_cannot_mutate_method_evidence_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()

    def configure(source: _Source) -> None:
        def mutate(count: int) -> None:
            if count == 3:
                verification = harness.broker.candidates[-1].report.verification
                object.__setattr__(
                    verification,
                    "methods",
                    tuple(
                        VerificationMethodResult(
                            item.method, VerificationStatus.FAIL, VerificationCode.CANARY
                        )
                        if item.method is VerificationMethod.TEXT
                        else item
                        for item in verification.methods
                    ),
                )

        source.on_revalidate = mutate

    harness.sources.configure = configure
    path = tmp_path / "result.pdf"
    result = harness.export(path)
    assert result.status is RedactionExportStatus.BLOCKED
    assert harness.destination.commit_calls == 0 and not path.exists()
    harness.assert_closed()
