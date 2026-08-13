"""PWS-0206 security, privacy, and recoverable-resource evidence."""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import time
import traceback
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
    reject_searchable_pdf_export,
)
from paperworkshell.pdf.searchable_export_adapter import (
    SearchablePdfAdapterProgressPhase,
    SearchablePdfExportAdapter,
    SearchablePdfExportAdapterControl,
    _save_output,
)
from paperworkshell.worker.operations import SearchablePdfExportOperationRequest
from paperworkshell.worker.pdf_export_report import PdfExportWorkerReport
from paperworkshell.worker.searchable_pdf_export_plan import (
    SearchablePdfWorkerPlan,
    SearchablePdfWorkerSource,
    decode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    SearchablePdfWorkerReport,
    decode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    SearchablePdfPageStatusDocument,
    decode_searchable_pdf_page_status,
)
from tests.unit.test_searchable_pdf_export_service import (
    CASE_ID as SERVICE_CASE_ID,
)
from tests.unit.test_searchable_pdf_export_service import (
    _service,
)
from tests.unit.worker.test_searchable_pdf_export_broker import _System

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "paperworkshell"

_ADAPTER_TESTS: Any = import_module("tests.unit.test_searchable_export_adapter")
PDF_FIXTURES = cast(Path, _ADAPTER_TESTS.PDF_FIXTURES)
_CleanChecker: Any = _ADAPTER_TESTS._CleanChecker
_NeverOcr: Any = _ADAPTER_TESTS._NeverOcr
_control: Any = _ADAPTER_TESTS._control
_limits: Any = _ADAPTER_TESTS._limits
_plan: Any = _ADAPTER_TESTS._plan
_run: Any = _ADAPTER_TESTS._run

_CASE_ID = UUID("7a000000-0000-4000-8000-000000000001")
_SOURCE_ID = UUID("7a000000-0000-4000-8000-000000000002")
_PAGE_ID = UUID("7a000000-0000-4000-8000-000000000003")
_OPERATION_ID = UUID("7a000000-0000-4000-8000-000000000004")
_OUTPUT_ID = UUID("7a000000-0000-4000-8000-000000000005")
_STATUS_ID = UUID("7a000000-0000-4000-8000-000000000006")

_PRIVATE_PATH = "C:\\Applicants\\private-name\\source-private.pdf"
_PRIVATE_FILENAME = "source-private.pdf"
_PRIVATE_TEXT = "PRIVATE-OCR-DOCUMENT-TEXT-CANARY"
_PRIVATE_SOURCE_DIGEST = "a" * 64
_PRIVATE_MANIFEST_DIGEST = "b" * 64
_PRIVATE_OUTPUT_DIGEST = "c" * 64
_PRIVATE_STATUS_DIGEST = "d" * 64
_PRIVATE_CAS_TOKEN = "private_applicant_manifest_token_001"
_PRIVATE_LANGUAGE_ID = "applicant-private-language-v1"
_PRIVATE_PROFILE_ID = "applicant-private-profile-v1"


def _privacy_plan() -> SearchablePdfExportPlan:
    profile = SearchablePdfExportProfile(
        ocr_language_set_id=_PRIVATE_LANGUAGE_ID,
        ocr_profile_id=_PRIVATE_PROFILE_ID,
    )
    source = SearchablePdfSource(
        source_id=_SOURCE_ID,
        media_type=SearchablePdfSourceMediaType.PDF,
        byte_size=128,
        page_count=1,
        sha256=_PRIVATE_SOURCE_DIGEST,
        signature_state=SearchablePdfSourceSignatureState.NONE_DETECTED,
        encryption_state=SearchablePdfSourceEncryptionState.UNENCRYPTED,
    )
    page = SearchablePdfPage(
        output_page_index=0,
        page_id=_PAGE_ID,
        source_id=_SOURCE_ID,
        source_page_index=0,
        quarter_turns_clockwise=0,
        crop=None,
        deskew_angle_millidegrees_clockwise=0,
        contrast_factor_millionths=1_000_000,
        background_cleanup_strength_millionths=0,
    )
    return SearchablePdfExportPlan(
        case_id=_CASE_ID,
        manifest_revision=1,
        manifest_sha256=_PRIVATE_MANIFEST_DIGEST,
        manifest_cas_token=_PRIVATE_CAS_TOKEN,
        sources=(source,),
        pages=(page,),
        profile=profile,
    )


def _privacy_outcome() -> SearchablePdfPageOutcome:
    return SearchablePdfPageOutcome(
        eligibility=SearchablePdfPageEligibility(
            output_page_index=0,
            page_id=_PAGE_ID,
            code=SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
        ),
        outcome=SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        searchability=SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
    )


def _render_exception(error: BaseException) -> str:
    return (
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
        + "\n"
        + "\n".join((repr(error), str(error), repr(error.args)))
    )


def test_models_errors_and_logs_do_not_disclose_private_document_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    plan = _privacy_plan()
    outcome = _privacy_outcome()
    report = SearchablePdfVerificationReport.for_verified_plan(
        plan,
        output_sha256=_PRIVATE_OUTPUT_DIGEST,
        output_byte_size=256,
        page_outcomes=(outcome,),
    )
    status = SearchablePdfPageStatusDocument(
        operation_id=_OPERATION_ID,
        status_object_id=_STATUS_ID,
        semantic_fingerprint=plan.semantic_fingerprint,
        page_outcomes=(outcome,),
    )
    worker_report = SearchablePdfWorkerReport(
        operation_id=_OPERATION_ID,
        output_object_id=_OUTPUT_ID,
        status_object_id=_STATUS_ID,
        status_byte_size=128,
        status_sha256=_PRIVATE_STATUS_DIGEST,
        base_report=PdfExportWorkerReport(
            operation_id=_OPERATION_ID,
            output_object_id=_OUTPUT_ID,
            report=report.base_report,
        ),
    )
    worker_plan = SearchablePdfWorkerPlan(
        plan=plan,
        sources=(SearchablePdfWorkerSource(plan.sources[0], 91),),
    )
    operation_request = SearchablePdfExportOperationRequest(
        plan_handle=91,
        output_handle=92,
        status_handle=93,
        result_handle=94,
        output_object_id=_OUTPUT_ID,
        status_object_id=_STATUS_ID,
        status_capacity_bytes=1024,
    )
    values = (
        plan.profile,
        plan.sources[0],
        plan,
        report,
        status,
        worker_report,
        worker_plan,
        worker_plan.sources[0],
        operation_request,
    )
    logging.getLogger("paperworkshell.tests.searchable_privacy").debug(
        "typed searchable export state: %r",
        values,
    )

    raw = RuntimeError(f"{_PRIVATE_PATH} {_PRIVATE_TEXT}")
    try:
        raise raw
    except RuntimeError:
        with pytest.raises(SearchablePdfExportError) as raised:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.security_test",
            )

    rendered = "\n".join((repr(values), caplog.text, _render_exception(raised.value)))
    for private in (
        _PRIVATE_PATH,
        _PRIVATE_FILENAME,
        _PRIVATE_TEXT,
        _PRIVATE_SOURCE_DIGEST,
        _PRIVATE_MANIFEST_DIGEST,
        _PRIVATE_OUTPUT_DIGEST,
        _PRIVATE_STATUS_DIGEST,
        _PRIVATE_CAS_TOKEN,
        _PRIVATE_LANGUAGE_ID,
        _PRIVATE_PROFILE_ID,
        plan.semantic_fingerprint,
    ):
        assert private not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "decoder",
    (
        decode_searchable_pdf_worker_plan,
        decode_searchable_pdf_page_status,
        decode_searchable_pdf_worker_report,
    ),
)
@pytest.mark.parametrize(
    "payload",
    (
        b"{}",
        b'{"private":"C:\\\\Applicants\\\\private-name\\\\source.pdf"}',
        b'{"private":"PRIVATE-OCR-DOCUMENT-TEXT-CANARY"}',
        b'{"duplicate":1,"duplicate":2}',
        b'{"number":NaN}',
        (b"[" * 24) + b"0" + (b"]" * 24),
        b"\xff\xfe\x00private",
    ),
)
def test_hostile_wire_documents_fail_closed_without_echo_or_exception_chain(
    decoder: Any,
    payload: bytes,
) -> None:
    with pytest.raises(SearchablePdfExportError) as raised:
        decoder(payload)

    rendered = _render_exception(raised.value)
    for private in (_PRIVATE_PATH, _PRIVATE_FILENAME, _PRIVATE_TEXT, "private-name"):
        assert private not in rendered
    assert raised.value.code in {
        SearchablePdfExportErrorCode.RESULT_INVALID,
        SearchablePdfExportErrorCode.REPORT_INVALID,
    }
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_app_service_sanitizes_raw_broker_failure_and_closes_source_lease(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, sources, broker, destination, _history, _retries = _service()
    private_destination = Path(_PRIVATE_PATH)

    def capacity(_destination: Path, *, estimated_byte_size: int) -> None:
        assert estimated_byte_size > 0

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(f"{_PRIVATE_PATH} {_PRIVATE_TEXT}")

    monkeypatch.setattr(destination, "require_capacity", capacity)
    monkeypatch.setattr(broker, "export", explode)

    with pytest.raises(SearchablePdfExportError) as raised:
        service.export_pdf(
            SERVICE_CASE_ID,
            private_destination,
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    rendered = _render_exception(raised.value) + caplog.text
    assert raised.value.code is SearchablePdfExportErrorCode.INTERNAL_ERROR
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sources.batch.closed
    assert _PRIVATE_PATH not in rendered
    assert _PRIVATE_FILENAME not in rendered
    assert _PRIVATE_TEXT not in rendered


def test_broker_sanitizes_raw_candidate_read_failure_and_closes_all_sections(
    caplog: pytest.LogCaptureFixture,
) -> None:
    system = _System()

    def explode() -> None:
        raise RuntimeError(f"{_PRIVATE_PATH} {_PRIVATE_TEXT}")

    system.output.on_iter = explode
    with pytest.raises(PdfExportError) as raised:
        system.export()

    rendered = _render_exception(raised.value) + caplog.text
    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert system.worker.terminated
    assert system.sources.revalidate_calls == 1
    assert system.output.close_calls == 1
    assert system.status.close_calls == 1
    assert _PRIVATE_PATH not in rendered
    assert _PRIVATE_FILENAME not in rendered
    assert _PRIVATE_TEXT not in rendered


@pytest.mark.parametrize(
    ("terminal", "expected"),
    (
        ("cancel", PdfExportErrorCode.CANCELLED),
        ("timeout", PdfExportErrorCode.TIMED_OUT),
    ),
)
def test_terminal_request_after_candidate_save_cleans_up_and_allows_fresh_retry(
    tmp_path: Path,
    terminal: str,
    expected: PdfExportErrorCode,
) -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    signal = Event()
    if terminal == "cancel":
        control = SearchablePdfExportAdapterControl(
            time.monotonic() + 30,
            cancellation_probe=signal.is_set,
        )
    else:
        control = SearchablePdfExportAdapterControl(
            1.0,
            monotonic=lambda: 2.0 if signal.is_set() else 0.0,
        )
    output = (tmp_path / "output.pdf").resolve()

    def progress(
        phase: SearchablePdfAdapterProgressPhase,
        completed_pages: int,
        total_pages: int,
    ) -> None:
        del total_pages
        if phase is SearchablePdfAdapterProgressPhase.VERIFYING and completed_pages == 0:
            signal.set()

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=_NeverOcr(),
            qpdf_checker=_CleanChecker(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=control,
            progress=progress,
        )

    assert raised.value.code is expected
    assert not output.exists()
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before

    retried_output, report, _checker = _run(tmp_path, plan, paths, _NeverOcr())
    assert retried_output == output
    assert report.base_report.output_byte_size == output.stat().st_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before


def test_partial_disk_write_is_removed_sanitized_and_retryable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = (tmp_path / "output.pdf").resolve()

    class _PartialDiskFailure:
        def save(self, stream: Any, **_kwargs: object) -> None:
            assert stream.write(b"%PDF-1.7\npartial-private-candidate") > 0
            raise OSError(f"disk full at {_PRIVATE_PATH} containing {_PRIVATE_TEXT}")

    with pytest.raises(PdfExportError) as raised:
        _save_output(
            cast(Any, _PartialDiskFailure()),
            output,
            _limits(),
            _control(),
        )

    rendered = _render_exception(raised.value) + caplog.text
    assert raised.value.code is PdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert not output.exists()
    assert _PRIVATE_PATH not in rendered
    assert _PRIVATE_TEXT not in rendered

    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    retried_output, report, _checker = _run(tmp_path, plan, paths, _NeverOcr())
    assert retried_output == output
    assert report.base_report.output_byte_size == output.stat().st_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before


def test_low_candidate_capacity_fails_cleanly_and_allows_fresh_retry(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "output.pdf").resolve()
    ordinary_limits = _limits()
    constrained_limits = replace(
        ordinary_limits,
        inspection=replace(
            ordinary_limits.inspection,
            maximum_output_bytes=4096,
        ),
        maximum_ocr_output_bytes=4096,
    )

    class _CapacityExhaustion:
        def save(self, stream: Any, **_kwargs: object) -> None:
            assert stream.write(b"x" * 4096) == 4096
            stream.write(b"x")

    with pytest.raises(PdfExportError) as raised:
        _save_output(
            cast(Any, _CapacityExhaustion()),
            output,
            constrained_limits,
            _control(),
        )

    assert raised.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not output.exists()
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))

    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    retried_output, report, _checker = _run(tmp_path, plan, paths, _NeverOcr())
    assert retried_output == output
    assert report.base_report.output_byte_size == output.stat().st_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before


def test_source_path_cannot_be_reused_as_candidate_and_source_remains_immutable(
    tmp_path: Path,
) -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=_NeverOcr(),
            qpdf_checker=_CleanChecker(),
        ).assemble_and_verify(
            plan,
            paths,
            source,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before


def test_searchable_trust_boundary_modules_have_no_routine_logging_or_print_surface() -> None:
    relative_paths = (
        Path("domain/searchable_pdf_export.py"),
        Path("app/searchable_pdf_export.py"),
        Path("worker/searchable_pdf_export_broker.py"),
        Path("worker/searchable_pdf_export_plan.py"),
        Path("worker/searchable_pdf_export_status.py"),
        Path("worker/searchable_pdf_export_report.py"),
    )
    prohibited_import_roots = {"logging", "traceback"}
    prohibited_calls = {"breakpoint", "print"}

    for relative in relative_paths:
        path = SOURCE_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module.partition(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)
        assert imports.isdisjoint(prohibited_import_roots), relative
        assert calls.isdisjoint(prohibited_calls), relative
