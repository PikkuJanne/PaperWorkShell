"""Oversubscribed removal storage is refused before output allocation or launch."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Never, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import PdfExportWorkerCoordinator
from paperworkshell.worker.pdf_thumbnail_broker import CommittedPdfPageLease
from paperworkshell.worker.redaction_export_broker import BrokeredRedactionExporter
from paperworkshell.worker.redaction_export_wire import encode_redaction_export_plan
from tests.unit.test_redaction_reconstruction_models import request
from tests.unit.worker.test_pdf_export_broker import _policy


class _ForbiddenWorker:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def start(self, _case_id: UUID) -> Never:
        self.calls.append("worker.start")
        raise AssertionError("Storage preflight must precede worker admission")


@pytest.mark.parametrize("excess", ("output", "source", "plan"))
def test_removal_storage_preflight_rejects_before_allocation_or_worker(excess: str) -> None:
    intent = request()
    policy = _policy()
    if excess == "source":
        policy = _policy(temporary_storage_bytes=8 * 1024 * 1024)
        intent = replace(intent, source_byte_size=3 * 1024 * 1024)
    elif excess == "plan":
        encoded = encode_redaction_export_plan(intent)
        policy = _policy(
            temporary_storage_bytes=intent.source_byte_size
            + len(encoded)
            + 3 * policy.limits.output_bytes
            - 1
        )
    assert (
        intent.source_byte_size
        + len(encode_redaction_export_plan(intent))
        + 3 * policy.limits.output_bytes
        > policy.limits.temporary_storage_bytes
    )
    calls: list[str] = []
    worker = _ForbiddenWorker(calls)
    source = SimpleNamespace(
        **{
            name: getattr(intent, name)
            for name in (
                "case_id",
                "source_id",
                "page_id",
                "source_page_index",
                "source_sha256",
                "source_byte_size",
                "source_page_count",
            )
        },
        handle=SimpleNamespace(value=101),
    )
    gate = HeavyOperationCoordinator(1)

    def output_buffer(_capacity: int) -> Never:
        calls.append("output.buffer")
        raise AssertionError("Storage preflight must precede output allocation")

    def plan_buffer(_payload: bytes) -> Never:
        calls.append("plan.buffer")
        raise AssertionError("Storage preflight must precede plan allocation")

    def result_pipe() -> Never:
        calls.append("result.pipe")
        raise AssertionError("Storage preflight must precede result pipe allocation")

    broker = BrokeredRedactionExporter(
        cast(PdfExportWorkerCoordinator, worker),
        policy,
        heavy_operations=gate,
        output_buffer_factory=output_buffer,
        plan_buffer_factory=plan_buffer,
        pipe_factory=result_pipe,
    )
    with pytest.raises(PdfExportError) as rejected:
        broker.prepare(
            intent,
            cast(CommittedPdfPageLease, source),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert rejected.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert rejected.value.context == "redaction_export.storage_budget"
    assert calls == []
    assert gate.snapshot().active_operations == gate.snapshot().waiting_operations == 0


class _OutputFactoryReached(BaseException):
    """Stop equality-boundary review without allocating a real buffer."""


def test_removal_storage_preflight_allows_exact_equality_before_allocation() -> None:
    intent = request()
    policy = _policy()
    encoded = encode_redaction_export_plan(intent)
    policy = _policy(
        temporary_storage_bytes=intent.source_byte_size
        + len(encoded)
        + 3 * policy.limits.output_bytes
    )
    calls: list[str] = []
    worker = _ForbiddenWorker(calls)
    source = SimpleNamespace(
        **{
            name: getattr(intent, name)
            for name in (
                "case_id",
                "source_id",
                "page_id",
                "source_page_index",
                "source_sha256",
                "source_byte_size",
                "source_page_count",
            )
        },
        handle=SimpleNamespace(value=101),
    )
    gate = HeavyOperationCoordinator(1)

    def output_buffer(capacity: int) -> Never:
        assert capacity == policy.limits.output_bytes
        calls.append("output.buffer")
        raise _OutputFactoryReached

    def plan_buffer(_payload: bytes) -> Never:
        calls.append("plan.buffer")
        raise AssertionError("Equality test must stop before plan allocation")

    def result_pipe() -> Never:
        calls.append("result.pipe")
        raise AssertionError("Equality test must stop before result pipe allocation")

    broker = BrokeredRedactionExporter(
        cast(PdfExportWorkerCoordinator, worker),
        policy,
        heavy_operations=gate,
        output_buffer_factory=output_buffer,
        plan_buffer_factory=plan_buffer,
        pipe_factory=result_pipe,
    )
    with pytest.raises(_OutputFactoryReached):
        broker.prepare(
            intent,
            cast(CommittedPdfPageLease, source),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert calls == ["output.buffer"]
    assert gate.snapshot().active_operations == gate.snapshot().waiting_operations == 0
