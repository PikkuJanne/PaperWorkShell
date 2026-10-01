"""Transient stage results require exact sources, policy, bytes, and cleanup."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, PdfExportProgress
from paperworkshell.domain.pdf_image_stages import PdfImageStageProfile, PdfImageStageRequest
from paperworkshell.worker.pdf_export_broker import PdfExportOutputBuffer, PdfExportSourceBatchLease
from tests.unit.worker.pdf_image_stage_fixtures import StageSystem
from tests.unit.worker.test_pdf_export_broker import _uuid


def test_success_terminates_revalidates_then_authenticates_both_sections() -> None:
    system = StageSystem()
    with system.process() as candidate:
        assert candidate.analysis == system.analysis
        assert candidate.report == system.report
        assert candidate.baseline_report == system.baseline
        assert candidate.read(4) + candidate.read(1024) == system.output.payload
        assert candidate.read(1024) == b""
        assert (
            system.events.index("worker.terminate")
            < system.events.index("source.revalidate")
            < system.events.index("pipe.read")
        )
        assert system.output.close_calls == 0
        assert system.evidence.close_calls == 1
    assert system.output.close_calls == 1
    assert system.heavy.snapshot().active_operations == 0


def test_stale_request_is_rejected_before_worker_or_output_allocation() -> None:
    system = StageSystem()
    request = PdfImageStageRequest(
        PdfImageStageProfile.PRESERVE_300_DPI_Q90, (), system.analysis.baseline_sha256, "a" * 64
    )
    with pytest.raises(PdfExportError) as caught:
        system.process(request)
    assert caught.value.code is PdfExportErrorCode.PLAN_STALE
    assert not system.worker.started and system._buffer_calls == 0


def test_selected_source_hash_is_revalidated_after_worker_exit() -> None:
    system = StageSystem()
    system.sources.after_revalidate = lambda: setattr(system.sources.sources[0], "sha256", "f" * 64)
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.SOURCE_CHANGED
    assert system.worker.terminated
    assert system.output.close_calls == system.evidence.close_calls == 1
    assert system.pipe.read_calls == 0


@pytest.mark.parametrize("kind", ("cancel", "timeout", "revoked"))
def test_terminal_before_processing_publishes_nothing(kind: str) -> None:
    system = StageSystem()
    if kind == "revoked":
        system.processor._revoke_permanently()
    with pytest.raises(PdfExportError):
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: kind == "timeout",
            cancellation=lambda: kind == "cancel",
        )
    assert not system.worker.started
    assert system.heavy.snapshot().active_operations == 0


@pytest.mark.parametrize("reserve_previous_output", (False, True))
def test_constrained_storage_lowers_output_capacity_before_allocation(
    reserve_previous_output: bool,
) -> None:
    system = StageSystem()
    configured = replace(
        system.policy,
        limits=replace(
            system.policy.limits,
            temporary_storage_bytes=2 * system.policy.limits.output_bytes
            + system.policy.evidence_capacity_bytes
            + 1,
        ),
    )
    system.processor._policy = configured
    capacities: list[int] = []

    def allocate(capacity: int) -> PdfExportOutputBuffer:
        capacities.append(capacity)
        if system._buffer_calls == 0:
            system.output.capacity = capacity
        return system.make_output_buffer(capacity)

    system.processor.output_buffer_factory = allocate
    reserved = configured.limits.output_bytes if reserve_previous_output else 0
    with system.processor.process(
        system.plan,
        cast(PdfExportSourceBatchLease, system.sources),
        PdfImageStageRequest(),
        deadline=lambda: False,
        cancellation=lambda: False,
        reserved_temporary_bytes=reserved,
    ) as candidate:
        assert candidate.read(1024) == system.output.payload
        assert candidate.read(1024) == b""
        assert system.worker.payload is not None
        assert system.plan_buffer is not None
        limits = system.worker.payload.requested_limits
        assert 0 < limits.output_bytes < configured.limits.output_bytes
        assert capacities == [limits.output_bytes, configured.evidence_capacity_bytes]
        assert (
            limits.temporary_storage_bytes == configured.limits.temporary_storage_bytes - reserved
        )
        assert (
            reserved
            + sum(source.byte_size for source in system.plan.sources)
            + 2 * limits.output_bytes
            + configured.evidence_capacity_bytes
            + len(system.plan_buffer.payload)
            <= configured.limits.temporary_storage_bytes
        )
    assert system.processor._policy == configured
    assert system.output.close_calls == system.evidence.close_calls == 1
    assert system.heavy.snapshot().active_operations == 0


@pytest.mark.parametrize("spare_bytes", (0, 2 * 4095))
def test_exhausted_storage_rejects_before_worker_or_allocation_without_raising_budget(
    spare_bytes: int,
) -> None:
    system = StageSystem()
    reserved = (
        system.policy.limits.temporary_storage_bytes
        - sum(source.byte_size for source in system.plan.sources)
        - system.policy.evidence_capacity_bytes
        - spare_bytes
    )
    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=lambda: False,
            reserved_temporary_bytes=reserved,
        )
    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not system.worker.started
    assert system._buffer_calls == 0
    assert system.processor._policy == system.policy
    assert system.heavy.snapshot().active_operations == 0


def test_broker_policy_accepts_output_ceiling_needing_a_smaller_operation_section() -> None:
    system = StageSystem()
    configured = replace(
        system.policy,
        limits=replace(
            system.policy.limits,
            output_bytes=system.policy.limits.temporary_storage_bytes // 2,
        ),
    )
    assert configured.limits == replace(
        system.policy.limits,
        output_bytes=system.policy.limits.temporary_storage_bytes // 2,
    )


def test_distinct_transferred_authorities_are_required() -> None:
    system = StageSystem()
    system.worker.duplicate_collision_call = 3
    with pytest.raises(PdfExportError):
        system.process()
    assert system.worker.terminated
    assert system.output.close_calls == system.evidence.close_calls == 1


@pytest.mark.parametrize("cancel_on_completion", (False, True))
def test_authenticated_completion_is_delivered_even_when_terminal_pump_drops_progress(
    cancel_on_completion: bool,
) -> None:
    system = StageSystem()
    observed: list[PdfExportProgress] = []
    cancelled = False

    def on_progress(value: PdfExportProgress) -> None:
        nonlocal cancelled
        observed.append(value)
        if value.completed_pages == value.total_pages:
            assert system.worker.terminated
            assert "source.revalidate" in system.events and "pipe.read" in system.events
            cancelled = cancel_on_completion

    def run() -> None:
        with system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=lambda: cancelled,
            progress=on_progress,
        ):
            pass

    if cancel_on_completion:
        with pytest.raises(PdfExportError) as caught:
            run()
        assert caught.value.code is PdfExportErrorCode.CANCELLED
    else:
        run()
    assert observed[-1].completed_pages == system.plan.page_count
    assert system.output.close_calls == system.evidence.close_calls == 1


def test_cancellation_receipt_is_a_request_id_and_terminal_result_binds_operation() -> None:
    system = StageSystem()
    system.worker.withhold_result = True
    system.worker.cancel_return_override = _uuid(98)
    cancelled = False

    def cancel_after_progress(_value: PdfExportProgress) -> None:
        nonlocal cancelled
        cancelled = True

    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=lambda: cancelled,
            progress=cancel_after_progress,
        )
    assert caught.value.code is PdfExportErrorCode.CANCELLED
    assert system.worker.cancel_calls and system.worker.terminated
    assert system.output.close_calls == system.evidence.close_calls == 1
