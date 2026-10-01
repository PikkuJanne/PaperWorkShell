"""Previously staged package members reduce actual image-worker authority."""

from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import PdfImageStageRequest
from paperworkshell.worker.pdf_export_broker import PdfExportSourceBatchLease
from tests.unit.worker.pdf_image_stage_fixtures import StageSystem


@pytest.mark.parametrize("reservation", (-1, True, 16 * 1024 * 1024, 15 * 1024 * 1024))
def test_invalid_or_exhausted_reservation_allocates_no_worker(reservation: int) -> None:
    system = StageSystem()
    with pytest.raises(PdfExportError) as caught:
        system.processor.process(
            system.plan,
            cast(PdfExportSourceBatchLease, system.sources),
            PdfImageStageRequest(),
            deadline=lambda: False,
            cancellation=lambda: False,
            reserved_temporary_bytes=reservation,
        )
    assert caught.value.code in {
        PdfExportErrorCode.MODEL_INVALID,
        PdfExportErrorCode.LIMIT_EXCEEDED,
    }
    assert not system.worker.started and system._buffer_calls == 0


def test_valid_reservation_reduces_the_worker_requested_limits() -> None:
    system = StageSystem()
    reserved = 1_000_000
    with system.processor.process(
        system.plan,
        cast(PdfExportSourceBatchLease, system.sources),
        PdfImageStageRequest(),
        deadline=lambda: False,
        cancellation=lambda: False,
        reserved_temporary_bytes=reserved,
    ):
        assert system.worker.payload is not None
        assert system.worker.payload.requested_limits.temporary_storage_bytes == (
            system.policy.limits.temporary_storage_bytes - reserved
        )
    assert system.output.close_calls == 1
