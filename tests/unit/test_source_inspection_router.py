from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.app.source_ingest import CancellationProbe, DeadlineProbe, ProgressSink
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceProgressPhase,
    SourceSignatureState,
)

CASE_ID = UUID("72000000-0000-4000-8000-000000000001")
PENDING_ID = UUID("72000000-0000-4000-8000-000000000002")
SOURCE_ID = UUID("72000000-0000-4000-8000-000000000003")


class _PdfInspector:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(
        self, source: PreparedSourceSummary, **_kwargs: object
    ) -> CompletedSourceInspection:
        self.calls += 1
        return CompletedSourceInspection(
            source.pending_id,
            DetectedMediaType.PDF,
            2,
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
        )


class _ImageInspector:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        self.calls += 1
        if deadline():
            raise SourceIngestError(SourceIngestErrorCode.TIMED_OUT, "inspection.image_deadline")
        if cancellation():
            raise SourceIngestError(SourceIngestErrorCode.CANCELLED, "inspection.image_cancelled")
        progress(
            SourceImportProgress(
                source.pending_id,
                SourceProgressPhase.INSPECTING,
                1,
                1,
            )
        )
        return CompletedSourceInspection(
            source.pending_id,
            source.media_type,
            1,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.NOT_APPLICABLE,
        )


def _summary(media_type: DetectedMediaType) -> PreparedSourceSummary:
    return PreparedSourceSummary(
        pending_id=PENDING_ID,
        case_id=CASE_ID,
        source_id=SOURCE_ID,
        media_type=media_type,
        byte_size=128,
        sha256="1" * 64,
    )


def test_router_delegates_pdf_and_image_to_their_worker_ports() -> None:
    pdf = _PdfInspector()
    image = _ImageInspector()
    router = SourceInspectorRouter(pdf, image)
    progress: list[SourceImportProgress] = []

    pdf_result = router.inspect(
        _summary(DetectedMediaType.PDF),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )
    image_result = router.inspect(
        _summary(DetectedMediaType.PNG),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert pdf.calls == 1
    assert image.calls == 1
    assert pdf_result.page_count == 2
    assert image_result.page_count == 1
    assert image_result.signature_state is SourceSignatureState.NOT_APPLICABLE
    assert image_result.encryption_state is SourceEncryptionState.NOT_APPLICABLE
    assert len(progress) == 1
    assert progress[0].completed_units == progress[0].total_units == 1


@pytest.mark.parametrize(
    ("deadline", "cancellation", "code"),
    [
        (lambda: True, lambda: False, SourceIngestErrorCode.TIMED_OUT),
        (lambda: False, lambda: True, SourceIngestErrorCode.CANCELLED),
    ],
)
def test_router_rejects_image_deadline_or_cancellation(
    deadline: object,
    cancellation: object,
    code: SourceIngestErrorCode,
) -> None:
    router = SourceInspectorRouter(_PdfInspector(), _ImageInspector())

    with pytest.raises(SourceIngestError) as raised:
        router.inspect(
            _summary(DetectedMediaType.JPEG),
            deadline=deadline,  # type: ignore[arg-type]
            cancellation=cancellation,  # type: ignore[arg-type]
            progress=lambda _progress: None,
        )

    assert raised.value.code is code
