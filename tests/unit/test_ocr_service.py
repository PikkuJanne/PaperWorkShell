from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.ocr import (
    CancellationProbe,
    CommittedSourcePageLease,
    DeadlineProbe,
    OcrCandidate,
    OcrProgressSink,
    OcrService,
    RetainedOcrSource,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.ocr import (
    TRUSTED_OCR_ENGINE_PROFILE_SHA256,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
)
from paperworkshell.domain.source_ingest import (
    SourceEncryptionState,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceSignatureState,
)

CASE_ID = UUID("20440000-0000-4000-8000-000000000001")
PAGE_ID = UUID("20440000-0000-4000-8000-000000000002")
SOURCE_ID = UUID("20440000-0000-4000-8000-000000000003")
OPERATION_ID = UUID("20440000-0000-4000-8000-000000000004")
OUTPUT_ID = UUID("20440000-0000-4000-8000-000000000005")
SOURCE_SHA256 = hashlib.sha256(b"synthetic committed source").hexdigest()
OUTPUT = b"%PDF-1.7\n%%EOF\n"


@dataclass(slots=True)
class _Lease:
    source_media_type: DetectedMediaType
    signature_state: SourceSignatureState
    encryption_state: SourceEncryptionState
    source_page_count: int
    case_id: UUID = CASE_ID
    source_id: UUID = SOURCE_ID
    source_byte_size: int = 26
    source_sha256: str = SOURCE_SHA256
    handle_value: int = 901
    entered: bool = False
    exited: bool = False
    revalidated: bool = False

    def __enter__(self) -> Self:
        self.entered = True
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.exited = True

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        assert self.entered and not self.exited
        assert deadline() is False
        assert cancellation() is False
        self.revalidated = True


class _Store:
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease
        self.error: BaseException | None = None
        self.calls: list[tuple[UUID, UUID]] = []

    def borrow_committed_source_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> CommittedSourcePageLease:
        assert deadline() is False
        assert cancellation() is False
        self.calls.append((case_id, page_id))
        if self.error is not None:
            raise self.error
        return self.lease


class _Candidate:
    def __init__(self, request: OcrProcessRequest) -> None:
        self.report = OcrWorkerReport(
            operation_id=OPERATION_ID,
            source_id=request.source_id,
            output_object_id=OUTPUT_ID,
            source_kind=request.source_kind,
            page_count=request.source_page_count,
            processed_page_count=request.source_page_count,
            language_set_id=request.language_set_id,
            profile_id=request.profile_id,
            ocrmypdf_version=TRUSTED_OCRMYPDF_VERSION,
            tesseract_version=TRUSTED_TESSERACT_VERSION,
            engine_profile_sha256=TRUSTED_OCR_ENGINE_PROFILE_SHA256,
            output_byte_size=len(OUTPUT),
            output_sha256=hashlib.sha256(OUTPUT).hexdigest(),
        )
        self.closed = False

    def read(self, size: int, /) -> bytes:
        return OUTPUT[:size]

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.close()


class _Processor:
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease
        self.request: OcrProcessRequest | None = None
        self.source: RetainedOcrSource | None = None
        self.calls = 0
        self.error: BaseException | None = None

    def process(
        self,
        request: OcrProcessRequest,
        source: RetainedOcrSource,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink,
    ) -> OcrCandidate:
        del progress
        assert self.lease.entered and not self.lease.exited
        if self.error is not None:
            raise self.error
        assert source.handle_value == self.lease.handle_value
        source.revalidate(deadline=deadline, cancellation=cancellation)
        self.request = request
        self.source = source
        self.calls += 1
        return _Candidate(request)


@pytest.mark.parametrize(
    ("media_type", "signature", "encryption", "page_count", "expected_kind"),
    [
        (
            DetectedMediaType.PDF,
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
            3,
            OcrSourceKind.PDF,
        ),
        (
            DetectedMediaType.JPEG,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.NOT_APPLICABLE,
            1,
            OcrSourceKind.JPEG,
        ),
        (
            DetectedMediaType.PNG,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.NOT_APPLICABLE,
            1,
            OcrSourceKind.PNG,
        ),
    ],
)
def test_service_builds_request_only_from_same_authenticated_lease(
    media_type: DetectedMediaType,
    signature: SourceSignatureState,
    encryption: SourceEncryptionState,
    page_count: int,
    expected_kind: OcrSourceKind,
) -> None:
    lease = _Lease(media_type, signature, encryption, page_count)
    store = _Store(lease)
    processor = _Processor(lease)

    candidate = OcrService(store, processor).process_page(
        CASE_ID,
        PAGE_ID,
        "latin-default-v1",
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    request = cast(OcrProcessRequest, processor.request)
    assert store.calls == [(CASE_ID, PAGE_ID)]
    assert request.case_id == lease.case_id
    assert request.source_id == lease.source_id
    assert request.source_kind is expected_kind
    assert request.source_byte_size == lease.source_byte_size
    assert request.source_page_count == lease.source_page_count
    assert request.source_sha256 == lease.source_sha256
    assert request.signature_state is OcrSourceSignatureState(signature.value)
    assert request.encryption_state is OcrSourceEncryptionState(encryption.value)
    assert request.language_set_id == "latin-default-v1"
    assert processor.source is not None
    assert lease.revalidated and lease.exited
    assert candidate.report.source_id == lease.source_id


@pytest.mark.parametrize(
    ("signature", "encryption", "expected_code"),
    [
        (
            SourceSignatureState.MARKERS_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
            OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION,
        ),
        (
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.UNENCRYPTED,
            OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION,
        ),
        (
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.ENCRYPTED,
            OcrErrorCode.SOURCE_ENCRYPTED,
        ),
        (
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.UNKNOWN,
            OcrErrorCode.SOURCE_ENCRYPTION_UNKNOWN,
        ),
    ],
)
def test_service_maps_conservative_pdf_states_before_worker_launch(
    signature: SourceSignatureState,
    encryption: SourceEncryptionState,
    expected_code: OcrErrorCode,
) -> None:
    lease = _Lease(DetectedMediaType.PDF, signature, encryption, 1)
    processor = _Processor(lease)

    with pytest.raises(OcrError) as captured:
        OcrService(_Store(lease), processor).process_page(
            CASE_ID,
            PAGE_ID,
            "latin-default-v1",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert captured.value.code is expected_code
    assert processor.calls == 0
    assert lease.exited


def test_service_rejects_unadmitted_manifest_media_without_worker_launch() -> None:
    lease = _Lease(
        cast(DetectedMediaType, "image/tiff"),
        SourceSignatureState.NOT_APPLICABLE,
        SourceEncryptionState.NOT_APPLICABLE,
        1,
    )
    processor = _Processor(lease)

    with pytest.raises(OcrError) as captured:
        OcrService(_Store(lease), processor).process_page(
            CASE_ID,
            PAGE_ID,
            "latin-default-v1",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert captured.value.code is OcrErrorCode.SOURCE_INVALID
    assert processor.calls == 0
    assert lease.exited


@pytest.mark.parametrize(
    ("source_code", "ocr_code"),
    [
        (SourceIngestErrorCode.CANCELLED, OcrErrorCode.CANCELLED),
        (SourceIngestErrorCode.TIMED_OUT, OcrErrorCode.TIMED_OUT),
        (SourceIngestErrorCode.CAPACITY_EXCEEDED, OcrErrorCode.LIMIT_EXCEEDED),
        (SourceIngestErrorCode.SOURCE_CHANGED, OcrErrorCode.SOURCE_INVALID),
    ],
)
def test_service_maps_storage_failures_to_sanitized_ocr_errors(
    source_code: SourceIngestErrorCode,
    ocr_code: OcrErrorCode,
) -> None:
    lease = _Lease(
        DetectedMediaType.PNG,
        SourceSignatureState.NOT_APPLICABLE,
        SourceEncryptionState.NOT_APPLICABLE,
        1,
    )
    store = _Store(lease)
    store.error = SourceIngestError(source_code, "source.synthetic")

    with pytest.raises(OcrError) as captured:
        OcrService(store, _Processor(lease)).process_page(
            CASE_ID,
            PAGE_ID,
            "latin-default-v1",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert captured.value.code is ocr_code
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_service_rebuilds_processor_error_without_private_exception_chain() -> None:
    lease = _Lease(
        DetectedMediaType.PNG,
        SourceSignatureState.NOT_APPLICABLE,
        SourceEncryptionState.NOT_APPLICABLE,
        1,
    )
    processor = _Processor(lease)
    try:
        raise RuntimeError("C:\\Applicants\\private-name\\source.png OCR-CONTENT-CANARY")
    except RuntimeError as private_error:
        processor.error = OcrError(
            OcrErrorCode.ENGINE_FAILED,
            "ocr.engine",
        )
        processor.error.__cause__ = private_error

    with pytest.raises(OcrError) as captured:
        OcrService(_Store(lease), processor).process_page(
            CASE_ID,
            PAGE_ID,
            "latin-default-v1",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert captured.value.code is OcrErrorCode.ENGINE_FAILED
    assert captured.value.context == "ocr.engine"
    assert captured.value is not processor.error
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "OCR-CONTENT-CANARY" not in repr(captured.value)


def test_service_rejects_processor_error_subclass_as_closed_internal_failure() -> None:
    class _PrivateOcrError(OcrError):
        def __repr__(self) -> str:
            return "OCR-CONTENT-CANARY"

    lease = _Lease(
        DetectedMediaType.PNG,
        SourceSignatureState.NOT_APPLICABLE,
        SourceEncryptionState.NOT_APPLICABLE,
        1,
    )
    processor = _Processor(lease)
    processor.error = _PrivateOcrError(OcrErrorCode.ENGINE_FAILED, "ocr.engine")

    with pytest.raises(OcrError) as captured:
        OcrService(_Store(lease), processor).process_page(
            CASE_ID,
            PAGE_ID,
            "latin-default-v1",
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert type(captured.value) is OcrError
    assert captured.value.code is OcrErrorCode.INTERNAL_ERROR
    assert captured.value.context == "ocr.processor"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "OCR-CONTENT-CANARY" not in repr(captured.value)
