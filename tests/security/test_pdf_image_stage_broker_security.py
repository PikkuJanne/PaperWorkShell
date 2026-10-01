"""Hostile worker responses never become an accepted image-stage candidate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
)
from paperworkshell.domain.pdf_image_stages import PdfImageStageProfile, PdfImageStageRequest
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.pdf_export_broker import PdfExportSourceBatchLease
from paperworkshell.worker.pdf_image_stage_wire import (
    decode_pdf_image_stage_result,
    encode_pdf_image_stage_evidence,
    encode_pdf_image_stage_result,
)
from paperworkshell.worker.protocol.models import CancelReason, OperationStartPayload
from tests.unit.worker.pdf_image_stage_fixtures import StageSystem
from tests.unit.worker.test_pdf_export_broker import _uuid


def _after_advertise(
    monkeypatch: pytest.MonkeyPatch,
    system: StageSystem,
    mutation: Callable[[], None],
) -> None:
    original = system.worker.start_operation

    def start(payload: OperationStartPayload, *, operation_id: UUID | None = None) -> UUID:
        identifier = original(payload, operation_id=operation_id)
        mutation()
        return identifier

    monkeypatch.setattr(system.worker, "start_operation", start)


def _assert_rejected_and_released(system: StageSystem) -> None:
    assert system.worker.terminated
    assert system.events.count("worker.terminate") == 1
    assert system.output.close_calls == 1
    assert system.evidence.close_calls == 1
    assert system.pipe.exited
    assert system.plan_buffer is not None and system.plan_buffer.exited
    assert system.heavy.snapshot().active_operations == 0


@pytest.mark.parametrize("section", ("output", "evidence"))
def test_section_substitution_after_advertised_digest_cannot_be_accepted(
    monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    system = StageSystem()
    buffer = system.output if section == "output" else system.evidence

    def substitute() -> None:
        buffer.payload = bytes((buffer.payload[0] ^ 1,)) + buffer.payload[1:]

    _after_advertise(monkeypatch, system, substitute)
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.RESULT_INVALID
    assert system.events.index("worker.terminate") < system.events.index("pipe.read")
    assert system.events.index("worker.terminate") < system.events.index("output.iter.1")
    _assert_rejected_and_released(system)


@pytest.mark.parametrize("section", ("output", "evidence"))
@pytest.mark.parametrize("failure", ("short", "read_error", "empty_chunk"))
def test_incomplete_or_faulted_section_cannot_release_a_candidate(
    section: str, failure: str
) -> None:
    system = StageSystem()
    buffer = system.output if section == "output" else system.evidence
    if failure == "short":
        buffer.short_iter_call = 1
    elif failure == "read_error":
        buffer.fail_iter_call = 1
    else:
        buffer.invalid_chunk_call = 1
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.RESULT_INVALID
    assert "private output read failure" not in str(caught.value)
    assert caught.value.__context__ is None
    _assert_rejected_and_released(system)


@pytest.mark.parametrize(
    "forgery",
    ("analysis_digest", "analysis_size", "report_plan", "unrequested_profile"),
)
def test_authenticated_valid_records_still_require_exact_request_and_baseline_binding(
    forgery: str,
) -> None:
    system = StageSystem()
    if forgery == "analysis_digest":
        system.analysis = replace(system.analysis, baseline_sha256="f" * 64)
    elif forgery == "analysis_size":
        system.analysis = replace(
            system.analysis, baseline_byte_size=system.analysis.baseline_byte_size + 1
        )
    elif forgery == "report_plan":
        system.report = replace(system.report, semantic_fingerprint="f" * 64)
    else:
        system.report = replace(system.report, profile=PdfImageStageProfile.PRESERVE_200_DPI_Q80)
    # Advertise the correct hash of the forged records: integrity alone cannot
    # establish their relationship to this plan, baseline and explicit request.
    system.evidence.payload = encode_pdf_image_stage_evidence(system.analysis, system.report)
    with pytest.raises(PdfExportError):
        system.process()
    assert len(system.output.iter_calls) == len(system.evidence.iter_calls) == 1
    _assert_rejected_and_released(system)


@pytest.mark.parametrize(
    "forgery", ("foreign_baseline", "missing_safe_stage", "lost_unsigned_label")
)
def test_authenticated_baseline_report_cannot_remove_required_prior_evidence(forgery: str) -> None:
    system = StageSystem()
    if forgery == "foreign_baseline":
        system.baseline = replace(system.baseline, output_sha256="f" * 64)
    elif forgery == "missing_safe_stage":
        system.baseline = replace(system.baseline, optimization=None, report_version=1)
    else:
        system.baseline = replace(
            system.baseline,
            unsigned_derivative=False,
            checks=tuple(
                check
                for check in system.baseline.checks
                if check.code is not PdfExportCheckCode.UNSIGNED_DERIVATIVE
            ),
        )
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.REPORT_INVALID
    _assert_rejected_and_released(system)


@pytest.mark.parametrize("forgery", ("stale_baseline", "ignored_selection", "different_profile"))
def test_apply_response_cannot_replace_explicit_caller_intent(forgery: str) -> None:
    system = StageSystem()
    request = PdfImageStageRequest(
        PdfImageStageProfile.PRESERVE_300_DPI_Q90,
        (0,) if forgery == "ignored_selection" else (),
        "f" * 64 if forgery == "stale_baseline" else system.analysis.baseline_sha256,
        system.plan.semantic_fingerprint,
    )
    system.report = replace(
        system.report,
        profile=(
            PdfImageStageProfile.PRESERVE_200_DPI_Q80
            if forgery == "different_profile"
            else request.profile
        ),
    )
    system.evidence.payload = encode_pdf_image_stage_evidence(system.analysis, system.report)
    with pytest.raises(PdfExportError) as caught:
        system.process(request)
    if forgery == "stale_baseline":
        assert caught.value.code is PdfExportErrorCode.PLAN_STALE
    _assert_rejected_and_released(system)


@pytest.mark.parametrize("field", ("operation_id", "output_object_id", "evidence_object_id"))
def test_internally_valid_header_from_another_operation_or_section_is_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    system = StageSystem()

    def substitute() -> None:
        assert system.pipe.raw_frame is not None
        header = decode_pdf_image_stage_result(system.pipe.raw_frame)
        if field == "operation_id":
            header = replace(header, operation_id=_uuid(501))
        elif field == "output_object_id":
            header = replace(header, output_object_id=_uuid(501))
        else:
            header = replace(header, evidence_object_id=_uuid(501))
        system.pipe.raw_frame = encode_pdf_image_stage_result(header)

    _after_advertise(monkeypatch, system, substitute)
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.REPORT_INVALID
    assert not system.output.iter_calls and not system.evidence.iter_calls
    _assert_rejected_and_released(system)


@pytest.mark.parametrize("field", ("generation", "operation"))
def test_stale_terminal_result_is_rejected_before_reading_private_sections(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    system = StageSystem()

    def substitute() -> None:
        assert system.worker.terminal_result is not None
        system.worker.terminal_result = (
            replace(system.worker.terminal_result, generation=system.worker.generation + 1)
            if field == "generation"
            else replace(system.worker.terminal_result, operation_id=_uuid(501))
        )

    _after_advertise(monkeypatch, system, substitute)
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.REPORT_INVALID
    assert system.pipe.read_calls == 0
    _assert_rejected_and_released(system)


def test_cancelled_operation_cannot_publish_late_worker_success() -> None:
    system = StageSystem()
    system.worker.withhold_result = True
    system.worker.cancel_returns_success = True
    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=lambda: system.worker.result_queries >= 1,
        )
    assert caught.value.code is PdfExportErrorCode.CANCELLED
    assert system.worker.cancel_calls == [CancelReason.USER_REQUESTED]
    assert system.sources.revalidate_calls == system.pipe.read_calls == 0
    _assert_rejected_and_released(system)


@pytest.mark.parametrize("phase", ("source_revalidation", "evidence_authentication"))
def test_late_cancellation_after_worker_success_still_discards_private_candidate(
    phase: str,
) -> None:
    system = StageSystem()

    def cancelled() -> bool:
        if phase == "source_revalidation":
            return system.sources.revalidate_calls > 0
        return bool(system.evidence.iter_calls)

    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=cancelled,
        )
    assert caught.value.code is PdfExportErrorCode.CANCELLED
    assert not system.worker.cancel_calls
    if phase == "source_revalidation":
        assert system.pipe.read_calls == 0
    else:
        assert len(system.output.iter_calls) == len(system.evidence.iter_calls) == 1
    _assert_rejected_and_released(system)


def test_deadline_terminates_worker_and_never_reads_advertised_late_result() -> None:
    system = StageSystem()
    system.worker.withhold_result = True
    system.worker.cancel_returns_success = True
    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: system.worker.result_queries >= 1,
            cancellation=lambda: False,
        )
    assert caught.value.code is PdfExportErrorCode.TIMED_OUT
    assert system.worker.cancel_calls == [CancelReason.SHUTDOWN]
    assert system.sources.revalidate_calls == system.pipe.read_calls == 0
    _assert_rejected_and_released(system)


def test_unconfirmed_worker_termination_blocks_all_result_verification() -> None:
    system = StageSystem()
    system.worker.termination_error = WorkerClientError(
        WorkerClientErrorCode.TERMINATION_FAILED, WorkerClientContext.PROCESS
    )
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
    assert system.sources.revalidate_calls == system.pipe.read_calls == 0
    assert not system.output.iter_calls and not system.evidence.iter_calls
    _assert_rejected_and_released(system)


def test_evidence_cleanup_failure_blocks_candidate_and_releases_output_without_private_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = StageSystem()
    close = system.evidence.close
    canary = "PWS0503_PRIVATE_CLEANUP_CANARY"

    def failing_close() -> None:
        close()
        raise OSError(canary)

    monkeypatch.setattr(system.evidence, "close", failing_close)
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
    assert canary not in str(caught.value) + repr(caught.value)
    assert caught.value.__context__ is None
    assert system.worker.terminated
    assert system.output.close_calls == 1
    assert system.evidence.close_calls >= 1
    assert system.heavy.snapshot().active_operations == 0
