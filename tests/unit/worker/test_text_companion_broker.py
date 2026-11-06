"""PWS-0706 one-shot cancellation, limits and untrusted-worker output checks."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.text_companion import TextCompanionError, TextCompanionErrorCode
from paperworkshell.worker.client.models import WorkerClientSnapshot, WorkerOperationResultSnapshot
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
)
from paperworkshell.worker.protocol.text_companion import (
    TEXT_COMPANION_CAPABILITY,
    TEXT_COMPANION_CONTENT_KIND,
)
from paperworkshell.worker.text_companion_broker import BrokeredTextCompanionExtractor
from tests.unit.worker.test_signature_image_raster_broker import _OutputBuffer, _uuid, _Worker


class _TextWorker(_Worker):
    def query(self) -> WorkerClientSnapshot:
        return replace(super().query(), capabilities=(TEXT_COMPANION_CAPABILITY,))

    def start_operation(
        self, payload: OperationStartPayload, *, operation_id: UUID | None = None
    ) -> UUID:
        result = super().start_operation(payload, operation_id=operation_id)
        self.encoded = b"Page 1\nPWSKEEP TEXT\n"
        output_id = next(
            item.value for item in payload.parameters if item.name == "output_object_id"
        )
        assert type(output_id) is UUID
        output = CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            uuid4() if self.wrong_output_id else output_id,
            result,
            TEXT_COMPANION_CONTENT_KIND,
            len(self.encoded),
            hashlib.sha256(self.encoded).hexdigest(),
        )
        if not self.withhold_result:
            self.result = WorkerOperationResultSnapshot(
                1, result, OperationOutcome.SUCCEEDED, (output,), None
            )
        return result


def _system() -> tuple[
    BrokeredTextCompanionExtractor, _TextWorker, list[str], HeavyOperationCoordinator
]:
    events: list[str] = []
    worker = _TextWorker(events)
    heavy = HeavyOperationCoordinator(1)
    broker = BrokeredTextCompanionExtractor(
        worker,
        CONSTRAINED_RESOURCE_POLICY_V1,
        heavy_operations=heavy,
        output_buffer_factory=lambda capacity: _OutputBuffer(capacity, worker, events),
    )
    return broker, worker, events, heavy


def test_text_broker_stops_worker_before_reading_output_and_shares_concurrency_budget() -> None:
    broker, worker, events, heavy = _system()
    result = broker.extract_handle(
        _uuid(1),
        101,
        expected_byte_size=100,
        expected_sha256="a" * 64,
        page_count=1,
        deadline=lambda: False,
        cancellation=lambda: False,
    )
    assert result == b"Page 1\nPWSKEEP TEXT\n"
    assert events.index("worker.terminate") < events.index("buffer.read")
    assert worker.payload is not None
    assert (
        worker.payload.requested_limits.output_bytes
        == CONSTRAINED_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes // 8
    )
    assert (
        heavy.snapshot().active_operations == 0
        and heavy.snapshot().maximum_observed_concurrency == 1
    )


@pytest.mark.parametrize("timeout", (False, True))
def test_inflight_cancel_or_timeout_terminates_worker_releases_gate_and_never_reads_text(
    timeout: bool,
) -> None:
    broker, worker, events, heavy = _system()
    worker.withhold_result = True
    probes = 0

    def stopped() -> bool:
        nonlocal probes
        probes += 1
        return probes > 3

    with pytest.raises(TextCompanionError) as caught:
        broker.extract_handle(
            _uuid(1),
            101,
            expected_byte_size=100,
            expected_sha256="a" * 64,
            page_count=1,
            deadline=stopped if timeout else lambda: False,
            cancellation=(lambda: False) if timeout else stopped,
        )
    assert caught.value.code is (
        TextCompanionErrorCode.TIMED_OUT if timeout else TextCompanionErrorCode.CANCELLED
    )
    assert (
        "worker.cancel.shutdown" in events if timeout else "worker.cancel.user_requested" in events
    )
    assert "buffer.read" not in events and worker.terminated
    assert heavy.snapshot().active_operations == 0


def test_malformed_result_identity_never_exposes_worker_bytes() -> None:
    broker, worker, events, heavy = _system()
    worker.wrong_output_id = True
    with pytest.raises(TextCompanionError):
        broker.extract_handle(
            _uuid(1),
            101,
            expected_byte_size=100,
            expected_sha256="a" * 64,
            page_count=1,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert "buffer.read" not in events and worker.terminated
    assert heavy.snapshot().active_operations == 0
