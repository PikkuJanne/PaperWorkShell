"""Unit evidence for immutable-ingest manifest and byte-copy helpers."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.case_lifecycle import (
    MAX_SAFE_MANIFEST_INTEGER,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    MAX_SOURCE_PAGES,
    CompletedSourceInspection,
    DuplicateContentSignal,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceProgressPhase,
    SourceSignatureState,
)
from paperworkshell.storage import source_ingest as ingest_storage
from paperworkshell.storage._windows import (
    MAXIMUM_STREAM_CHUNK_BYTES,
    FileIdentity,
    ManifestReplaceResult,
    ManifestReplaceState,
    WindowsPendingSourceFile,
)
from paperworkshell.storage.case_lifecycle import (
    MAX_MANIFEST_BYTES,
    build_initial_manifest_bytes,
)
from tools.validate_manifests import load_manifest_schema, validate_manifest_data

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
PENDING_ID = UUID("20000000-0000-4000-8000-000000000002")
IMAGE_SOURCE_ID = UUID("30000000-0000-4000-8000-000000000003")
PDF_SOURCE_ID = UUID("40000000-0000-4000-8000-000000000004")
DUPLICATE_SOURCE_ID = UUID("50000000-0000-4000-8000-000000000005")
PAGE_IDS = (
    UUID("60000000-0000-4000-8000-000000000006"),
    UUID("70000000-0000-4000-8000-000000000007"),
    UUID("80000000-0000-4000-8000-000000000008"),
)
IDENTITY = FileIdentity(23, b"synthetic-file-id")
CREATED_AT = datetime(2026, 8, 2, 10, 11, 12, 123456, tzinfo=UTC)
DISPLAY_NAME = "Synthetic source.pdf"
SOURCE_BYTES = b"PaperWorkShell synthetic source bytes"
SOURCE_DIGEST = hashlib.sha256(SOURCE_BYTES).hexdigest()


def _document(payload: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(payload))


def _initial_snapshot() -> ingest_storage._ManifestSnapshot:
    payload = build_initial_manifest_bytes(CASE_ID, CREATED_AT)
    return ingest_storage._validate_ingest_manifest(payload, IDENTITY, CASE_ID)


def _summary(
    media_type: DetectedMediaType,
    source_id: UUID,
    *,
    digest: str = SOURCE_DIGEST,
    byte_size: int = len(SOURCE_BYTES),
) -> PreparedSourceSummary:
    return PreparedSourceSummary(
        pending_id=PENDING_ID,
        case_id=CASE_ID,
        source_id=source_id,
        media_type=media_type,
        byte_size=byte_size,
        sha256=digest,
    )


def _inspection(
    media_type: DetectedMediaType,
    page_count: int,
) -> CompletedSourceInspection:
    if media_type is DetectedMediaType.PDF:
        return CompletedSourceInspection(
            pending_id=PENDING_ID,
            media_type=media_type,
            page_count=page_count,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )
    return CompletedSourceInspection(
        pending_id=PENDING_ID,
        media_type=media_type,
        page_count=page_count,
        signature_state=SourceSignatureState.NOT_APPLICABLE,
        encryption_state=SourceEncryptionState.NOT_APPLICABLE,
    )


def _candidate(
    snapshot: ingest_storage._ManifestSnapshot,
    *,
    media_type: DetectedMediaType,
    source_id: UUID,
    page_ids: tuple[UUID, ...],
    timestamp: datetime,
    digest: str = SOURCE_DIGEST,
    byte_size: int = len(SOURCE_BYTES),
) -> tuple[bytes, tuple[UUID, ...]]:
    return ingest_storage._build_candidate_manifest(
        snapshot,
        summary=_summary(
            media_type,
            source_id,
            digest=digest,
            byte_size=byte_size,
        ),
        display_name=DISPLAY_NAME,
        inspection=_inspection(media_type, len(page_ids)),
        page_ids=page_ids,
        timestamp=timestamp,
    )


def _valid_candidate_document(
    media_type: DetectedMediaType = DetectedMediaType.PNG,
) -> dict[str, Any]:
    page_ids = PAGE_IDS[:2] if media_type is DetectedMediaType.PDF else PAGE_IDS[:1]
    source_id = PDF_SOURCE_ID if media_type is DetectedMediaType.PDF else IMAGE_SOURCE_ID
    payload, _duplicates = _candidate(
        _initial_snapshot(),
        media_type=media_type,
        source_id=source_id,
        page_ids=page_ids,
        timestamp=CREATED_AT + timedelta(seconds=1),
    )
    return _document(payload)


def _serialized(document: dict[str, Any]) -> bytes:
    return ingest_storage._serialize_manifest(cast(dict[str, object], document))


class _SyntheticSource:
    def __init__(self, data: bytes, *, short_read: bool = False) -> None:
        self._data = data
        self._short_read = short_read
        self.reads: list[tuple[int, int]] = []

    def read_at(self, offset: int, count: int) -> bytes:
        self.reads.append((offset, count))
        actual_count = count - 1 if self._short_read else count
        return self._data[offset : offset + actual_count]


class _SyntheticPending:
    def __init__(self, *, corrupt_readback: bool = False) -> None:
        self._stored = bytearray()
        self._read_offset = 0
        self._corrupt_readback = corrupt_readback
        self.write_sizes: list[int] = []
        self.read_sizes: list[int] = []
        self.sealed = False
        self.rewound = False
        self.eof_checked = False

    def write_chunk(self, chunk: bytes) -> None:
        self.write_sizes.append(len(chunk))
        self._stored.extend(chunk)

    def seal(self, *, expected_size: int) -> None:
        assert len(self._stored) == expected_size
        self.sealed = True

    def rewind(self) -> None:
        self._read_offset = 0
        self.rewound = True

    def read_exact(self, count: int) -> bytes:
        self.read_sizes.append(count)
        end = self._read_offset + count
        chunk = bytes(self._stored[self._read_offset : end])
        self._read_offset = end
        if self._corrupt_readback and self._read_offset == count:
            return bytes([chunk[0] ^ 1]) + chunk[1:]
        return chunk

    def require_eof(self) -> None:
        assert self._read_offset == len(self._stored)
        self.eof_checked = True


class _CancelAt:
    def __init__(self, call_number: int) -> None:
        self._call_number = call_number
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls == self._call_number


class _ExpireAt:
    def __init__(self, call_number: int) -> None:
        self._call_number = call_number
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls >= self._call_number


class _CleanupFault(BaseException):
    pass


class _FaultingPending:
    def __init__(self, *, fail_mark: bool, fail_close: bool) -> None:
        self.handle = object()
        self.fail_mark = fail_mark
        self.fail_close = fail_close
        self.marks = 0
        self.closes = 0

    def mark_for_deletion(self) -> None:
        self.marks += 1
        if self.fail_mark:
            raise _CleanupFault("private pending deletion fault")

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            raise _CleanupFault("private pending close fault")


class _FaultingReservation:
    def __init__(self, relative_parts: tuple[str, ...], *, fail_release: bool) -> None:
        self.relative_parts = relative_parts
        self.byte_count = MAX_MANIFEST_BYTES
        self.fail_release = fail_release
        self.releases = 0

    def publication_guard(self, **_kwargs: object) -> AbstractContextManager[None]:
        return nullcontext()

    def release(self) -> None:
        self.releases += 1
        if self.fail_release:
            raise _CleanupFault("private reservation release fault")


class _SyntheticLayout:
    def __init__(self) -> None:
        self.temp = Path("C:/synthetic-case/temp")
        self.originals = Path("C:/synthetic-case/originals")
        self.manifest = Path("C:/synthetic-case/manifest.json")


class _FaultingAuthority:
    def __init__(self, *, fail_close: bool) -> None:
        self.layout = _SyntheticLayout()
        self.identity = IDENTITY
        self.handle = object()
        self.fail_close = fail_close
        self.closes = 0

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            raise _CleanupFault("private authority close fault")


class _SyntheticHandleInfo:
    def __init__(self, identity: FileIdentity) -> None:
        self.identity = identity


class _SyntheticCleanupHandle:
    def __init__(
        self,
        *,
        identity: FileIdentity = IDENTITY,
        fail_close: bool = False,
    ) -> None:
        self.info = _SyntheticHandleInfo(identity)
        self.fail_close = fail_close
        self.closes = 0

    def __enter__(self) -> _SyntheticCleanupHandle:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            raise _CleanupFault("private handle close fault")


def _cleanup_fault_lease(
    *,
    store: ingest_storage.WindowsSourceIngestStore,
    pending: _FaultingPending,
    source_reservation: _FaultingReservation,
    manifest_reservation: _FaultingReservation,
    authority: _FaultingAuthority,
) -> ingest_storage.WindowsPreparedSourceLease:
    return ingest_storage.WindowsPreparedSourceLease(
        store=store,
        authority=cast(Any, authority),
        summary=_summary(DetectedMediaType.PNG, IMAGE_SOURCE_ID),
        display_name=DISPLAY_NAME,
        snapshot=_initial_snapshot(),
        pending=cast(Any, pending),
        source_reservation=cast(Any, source_reservation),
        manifest_reservation=cast(Any, manifest_reservation),
        manifest_stage_path=authority.layout.temp / f"{PDF_SOURCE_ID}.tmp",
    )


def _cleanup_fault_store() -> ingest_storage.WindowsSourceIngestStore:
    return ingest_storage.WindowsSourceIngestStore(
        cast(Any, object()),
        cast(Any, object()),
        maximum_page_count=MAX_SOURCE_PAGES,
        clock=lambda: CREATED_AT + timedelta(seconds=1),
        id_factory=lambda: PAGE_IDS[0],
        temp_id_factory=lambda: PDF_SOURCE_ID,
    )


def test_initial_manifest_is_accepted_as_the_exact_empty_ingest_state() -> None:
    snapshot = _initial_snapshot()

    assert snapshot.revision == 0
    assert snapshot.created_at == CREATED_AT
    assert snapshot.updated_at == CREATED_AT
    assert snapshot.sources == ()
    assert snapshot.page_ids == ()
    assert snapshot.identifiers == frozenset({CASE_ID})
    assert snapshot.document["sources"] == []
    assert snapshot.document["pages"] == []
    assert snapshot.document["page_sequence_base"] == []
    assert snapshot.document["page_sequence"] == []


@pytest.mark.parametrize(
    ("defect", "expected_context"),
    [
        ("template", "source.manifest_template"),
        ("operation", "source.manifest_scope"),
        ("constraints", "source.manifest_constraints"),
        ("export_history", "source.manifest_export_history"),
    ],
)
def test_ingest_validator_rejects_state_outside_the_narrow_pre_journal_scope(
    defect: str,
    expected_context: str,
) -> None:
    document = _document(build_initial_manifest_bytes(CASE_ID, CREATED_AT))
    if defect == "template":
        document["template_ref"] = {
            "template_id": "synthetic.template",
            "template_version": 1,
            "sha256": "1" * 64,
        }
    elif defect == "operation":
        document["page_operations"] = [{"synthetic": "operation"}]
    elif defect == "constraints":
        document["constraints"]["encryption_policy"] = "allow_encrypted"
    else:
        document["export_history"]["entries"] = ["synthetic-export"]

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._validate_ingest_manifest(_serialized(document), IDENTITY, CASE_ID)

    assert raised.value.code is SourceIngestErrorCode.MANIFEST_INVALID
    assert raised.value.context == expected_context


@pytest.mark.parametrize(
    ("media_type", "source_id", "page_ids", "expected_signature", "expected_encryption"),
    [
        (
            DetectedMediaType.PNG,
            IMAGE_SOURCE_ID,
            PAGE_IDS[:1],
            "not_applicable",
            "not_applicable",
        ),
        (
            DetectedMediaType.PDF,
            PDF_SOURCE_ID,
            PAGE_IDS[:2],
            "none_detected",
            "unencrypted",
        ),
    ],
)
def test_candidate_appends_complete_source_pages_and_both_sequences(
    media_type: DetectedMediaType,
    source_id: UUID,
    page_ids: tuple[UUID, ...],
    expected_signature: str,
    expected_encryption: str,
) -> None:
    payload, duplicates = _candidate(
        _initial_snapshot(),
        media_type=media_type,
        source_id=source_id,
        page_ids=page_ids,
        timestamp=CREATED_AT + timedelta(seconds=1),
    )
    document = _document(payload)
    source = cast(dict[str, Any], document["sources"][0])
    rendered_pages = [str(page_id) for page_id in page_ids]

    assert duplicates == ()
    assert source == {
        "source_id": str(source_id),
        "display_name": DISPLAY_NAME,
        "storage_name": f"{source_id}.bin",
        "detected_media_type": media_type.value,
        "byte_size": len(SOURCE_BYTES),
        "page_count": len(page_ids),
        "sha256": SOURCE_DIGEST,
        "signature_state": expected_signature,
        "encryption_state": expected_encryption,
        "import_state": "complete",
        "imported_at": "2026-08-02T10:11:13.123456Z",
    }
    assert document["pages"] == [
        {
            "page_id": str(page_id),
            "source_id": str(source_id),
            "source_page_index": index,
        }
        for index, page_id in enumerate(page_ids)
    ]
    assert document["page_sequence_base"] == rendered_pages
    assert document["page_sequence"] == rendered_pages
    assert document["revision"] == 1

    snapshot = ingest_storage._validate_ingest_manifest(payload, IDENTITY, CASE_ID)
    schema, schema_issues = load_manifest_schema()
    assert snapshot.page_ids == page_ids
    assert snapshot.revision == 1
    assert schema is not None, [issue.render() for issue in schema_issues]
    assert validate_manifest_data(document, schema, document="candidate.json") == []


def test_duplicate_signal_matches_only_prior_equal_size_and_digest() -> None:
    first_payload, _duplicates = _candidate(
        _initial_snapshot(),
        media_type=DetectedMediaType.PNG,
        source_id=IMAGE_SOURCE_ID,
        page_ids=PAGE_IDS[:1],
        timestamp=CREATED_AT + timedelta(seconds=1),
    )
    first_snapshot = ingest_storage._validate_ingest_manifest(
        first_payload,
        IDENTITY,
        CASE_ID,
    )
    second_payload, duplicates = _candidate(
        first_snapshot,
        media_type=DetectedMediaType.PNG,
        source_id=DUPLICATE_SOURCE_ID,
        page_ids=PAGE_IDS[1:2],
        timestamp=CREATED_AT + timedelta(seconds=2),
    )

    signal = DuplicateContentSignal(duplicates)
    document = _document(second_payload)
    assert signal.detected
    assert signal.matching_source_ids == (IMAGE_SOURCE_ID,)
    assert len(document["sources"]) == 2
    assert document["sources"][0]["storage_name"] != document["sources"][1]["storage_name"]
    assert document["pages"][0]["page_id"] != document["pages"][1]["page_id"]


@pytest.mark.parametrize(
    ("defect", "expected_context"),
    [
        ("source", "source.manifest_storage_name"),
        ("page", "source.manifest_page_catalogue"),
        ("state", "source.manifest_inspection"),
        ("sequence", "source.manifest_sequence"),
    ],
)
def test_ingest_validator_rejects_invalid_prior_manifest_state(
    defect: str,
    expected_context: str,
) -> None:
    document = _valid_candidate_document()
    source = cast(dict[str, Any], document["sources"][0])
    page = cast(dict[str, Any], document["pages"][0])
    if defect == "source":
        source["storage_name"] = "Sensitive Applicant Name.pdf"
    elif defect == "page":
        page["source_id"] = str(PDF_SOURCE_ID)
    elif defect == "state":
        source["signature_state"] = "none_detected"
    else:
        document["page_sequence"] = []

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._validate_ingest_manifest(_serialized(document), IDENTITY, CASE_ID)

    assert raised.value.code is SourceIngestErrorCode.MANIFEST_INVALID
    assert raised.value.context == expected_context
    assert "Sensitive Applicant Name.pdf" not in str(raised.value)
    assert "Sensitive Applicant Name.pdf" not in repr(raised.value)


def test_ingest_validator_sanitizes_unhashable_pdf_inspection_state() -> None:
    document = _valid_candidate_document(DetectedMediaType.PDF)
    source = cast(dict[str, Any], document["sources"][0])
    source["signature_state"] = ["Sensitive Applicant State"]

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._validate_ingest_manifest(_serialized(document), IDENTITY, CASE_ID)

    assert raised.value.code is SourceIngestErrorCode.MANIFEST_INVALID
    assert raised.value.context == "source.manifest_inspection"
    assert "Sensitive Applicant State" not in str(raised.value)
    assert "Sensitive Applicant State" not in repr(raised.value)


def test_ingest_validator_rejects_cumulative_page_count_amplification() -> None:
    document = _document(build_initial_manifest_bytes(CASE_ID, CREATED_AT))
    first_page_count = MAX_SOURCE_PAGES // 2
    second_page_count = MAX_SOURCE_PAGES - first_page_count + 1
    second_timestamp = "2026-08-02T10:11:13.123456Z"
    document["revision"] = 2
    document["updated_at"] = second_timestamp
    document["sources"] = [
        {
            "source_id": str(IMAGE_SOURCE_ID),
            "display_name": "Synthetic first.pdf",
            "storage_name": f"{IMAGE_SOURCE_ID}.bin",
            "detected_media_type": DetectedMediaType.PDF.value,
            "byte_size": 1,
            "page_count": first_page_count,
            "sha256": "1" * 64,
            "signature_state": SourceSignatureState.NONE_DETECTED.value,
            "encryption_state": SourceEncryptionState.UNENCRYPTED.value,
            "import_state": "complete",
            "imported_at": "2026-08-02T10:11:12.123456Z",
        },
        {
            "source_id": str(PDF_SOURCE_ID),
            "display_name": "Synthetic second.pdf",
            "storage_name": f"{PDF_SOURCE_ID}.bin",
            "detected_media_type": DetectedMediaType.PDF.value,
            "byte_size": 1,
            "page_count": second_page_count,
            "sha256": "2" * 64,
            "signature_state": SourceSignatureState.NONE_DETECTED.value,
            "encryption_state": SourceEncryptionState.UNENCRYPTED.value,
            "import_state": "complete",
            "imported_at": second_timestamp,
        },
    ]

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._validate_ingest_manifest(_serialized(document), IDENTITY, CASE_ID)

    assert first_page_count <= MAX_SOURCE_PAGES
    assert second_page_count <= MAX_SOURCE_PAGES
    assert first_page_count + second_page_count == MAX_SOURCE_PAGES + 1
    assert raised.value.code is SourceIngestErrorCode.MANIFEST_INVALID
    assert raised.value.context == "source.manifest_page_count"


def test_ingest_validator_rejects_future_version_before_other_state() -> None:
    document = _valid_candidate_document()
    document["schema_version"] = 2
    document["sources"] = "Sensitive Applicant Source"

    with pytest.raises(CaseLifecycleError) as raised:
        ingest_storage._validate_ingest_manifest(_serialized(document), IDENTITY, CASE_ID)

    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED
    assert "Sensitive Applicant Source" not in str(raised.value)
    assert "Sensitive Applicant Source" not in repr(raised.value)


def test_candidate_manifest_upper_bound_is_conservative_and_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _initial_snapshot()
    summary = _summary(DetectedMediaType.PDF, PDF_SOURCE_ID)
    inspection = _inspection(DetectedMediaType.PDF, len(PAGE_IDS))
    upper_bound = ingest_storage._candidate_manifest_upper_bound(
        snapshot,
        source_id=PDF_SOURCE_ID,
        display_name=DISPLAY_NAME,
        media_type=DetectedMediaType.PDF,
        byte_size=len(SOURCE_BYTES),
        maximum_page_count=len(PAGE_IDS),
    )
    candidate, _duplicates = ingest_storage._build_candidate_manifest(
        snapshot,
        summary=summary,
        display_name=DISPLAY_NAME,
        inspection=inspection,
        page_ids=PAGE_IDS,
        timestamp=CREATED_AT + timedelta(seconds=1),
    )

    assert len(candidate) <= upper_bound <= MAX_MANIFEST_BYTES

    monkeypatch.setattr(ingest_storage, "MAX_MANIFEST_BYTES", upper_bound - 1)
    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._candidate_manifest_upper_bound(
            snapshot,
            source_id=PDF_SOURCE_ID,
            display_name=DISPLAY_NAME,
            media_type=DetectedMediaType.PDF,
            byte_size=len(SOURCE_BYTES),
            maximum_page_count=len(PAGE_IDS),
        )
    assert raised.value.code is SourceIngestErrorCode.CAPACITY_EXCEEDED
    assert raised.value.context == "source.manifest_bytes"


def test_candidate_clamps_a_rollback_clock_to_the_previous_manifest_time() -> None:
    payload, _duplicates = _candidate(
        _initial_snapshot(),
        media_type=DetectedMediaType.PNG,
        source_id=IMAGE_SOURCE_ID,
        page_ids=PAGE_IDS[:1],
        timestamp=CREATED_AT - timedelta(days=1),
    )
    document = _document(payload)

    assert document["updated_at"] == "2026-08-02T10:11:12.123456Z"
    assert document["sources"][0]["imported_at"] == document["updated_at"]


def test_candidate_rejects_invalid_clock_revision_and_page_capacity() -> None:
    snapshot = _initial_snapshot()
    summary = _summary(DetectedMediaType.PNG, IMAGE_SOURCE_ID)
    inspection = _inspection(DetectedMediaType.PNG, 1)

    with pytest.raises(SourceIngestError) as clock_error:
        ingest_storage._build_candidate_manifest(
            snapshot,
            summary=summary,
            display_name=DISPLAY_NAME,
            inspection=inspection,
            page_ids=PAGE_IDS[:1],
            timestamp=CREATED_AT.replace(tzinfo=None),
        )
    assert clock_error.value.code is SourceIngestErrorCode.COMMIT_FAILED
    assert clock_error.value.context == "source.clock"

    with pytest.raises(SourceIngestError) as revision_error:
        ingest_storage._build_candidate_manifest(
            replace(snapshot, revision=MAX_SAFE_MANIFEST_INTEGER),
            summary=summary,
            display_name=DISPLAY_NAME,
            inspection=inspection,
            page_ids=PAGE_IDS[:1],
            timestamp=CREATED_AT,
        )
    assert revision_error.value.code is SourceIngestErrorCode.CAPACITY_EXCEEDED
    assert revision_error.value.context == "source.revision"

    with pytest.raises(SourceIngestError) as page_error:
        ingest_storage._build_candidate_manifest(
            replace(snapshot, page_ids=(PAGE_IDS[0],) * MAX_SOURCE_PAGES),
            summary=summary,
            display_name=DISPLAY_NAME,
            inspection=inspection,
            page_ids=PAGE_IDS[:1],
            timestamp=CREATED_AT,
        )
    assert page_error.value.code is SourceIngestErrorCode.CAPACITY_EXCEEDED
    assert page_error.value.context == "source.pages"


def test_copy_and_readback_hashes_in_bounded_chunks_with_monotonic_progress() -> None:
    data = (b"0123456789abcdef" * ((MAXIMUM_STREAM_CHUNK_BYTES // 16) + 2))[
        : MAXIMUM_STREAM_CHUNK_BYTES + 17
    ]
    source = _SyntheticSource(data)
    pending = _SyntheticPending()
    progress: list[SourceImportProgress] = []

    digest = ingest_storage._copy_and_verify_source(
        source,
        cast(WindowsPendingSourceFile, pending),
        operation_id=PENDING_ID,
        byte_size=len(data),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )

    expected_sizes = [MAXIMUM_STREAM_CHUNK_BYTES, 17]
    assert digest == hashlib.sha256(data).hexdigest()
    assert source.reads == [(0, expected_sizes[0]), (expected_sizes[0], expected_sizes[1])]
    assert pending.write_sizes == expected_sizes
    assert pending.read_sizes == expected_sizes
    assert pending.sealed and pending.rewound and pending.eof_checked
    assert [item.phase for item in progress] == [
        SourceProgressPhase.COPYING,
        SourceProgressPhase.COPYING,
        SourceProgressPhase.VERIFYING_COPY,
        SourceProgressPhase.VERIFYING_COPY,
    ]
    assert [item.completed_units for item in progress] == [
        MAXIMUM_STREAM_CHUNK_BYTES,
        len(data),
        MAXIMUM_STREAM_CHUNK_BYTES,
        len(data),
    ]
    assert all(item.total_units == len(data) for item in progress)
    assert all(item.operation_id == PENDING_ID for item in progress)


@pytest.mark.parametrize(
    ("source", "pending", "expected_code", "expected_context"),
    [
        (
            _SyntheticSource(SOURCE_BYTES, short_read=True),
            _SyntheticPending(),
            SourceIngestErrorCode.SOURCE_CHANGED,
            "source.copy_read",
        ),
        (
            _SyntheticSource(SOURCE_BYTES),
            _SyntheticPending(corrupt_readback=True),
            SourceIngestErrorCode.COPY_FAILED,
            "source.copy_digest",
        ),
    ],
)
def test_copy_rejects_source_short_read_and_stored_digest_mismatch(
    source: _SyntheticSource,
    pending: _SyntheticPending,
    expected_code: SourceIngestErrorCode,
    expected_context: str,
) -> None:
    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._copy_and_verify_source(
            source,
            cast(WindowsPendingSourceFile, pending),
            operation_id=PENDING_ID,
            byte_size=len(SOURCE_BYTES),
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value.code is expected_code
    assert raised.value.context == expected_context
    assert SOURCE_BYTES.decode() not in str(raised.value)
    assert SOURCE_DIGEST not in repr(raised.value)


@pytest.mark.parametrize(
    ("cancel_at", "expected_context", "sealed", "rewound"),
    [
        (2, "source.copy_cancel", False, False),
        (5, "source.copy_cancel", False, False),
        (6, "source.verify_cancel", True, False),
        (7, "source.verify_cancel", True, True),
    ],
)
def test_copy_cancellation_stops_before_the_next_mutating_or_readback_step(
    cancel_at: int,
    expected_context: str,
    sealed: bool,
    rewound: bool,
) -> None:
    data = b"x" * (MAXIMUM_STREAM_CHUNK_BYTES + 1)
    source = _SyntheticSource(data)
    pending = _SyntheticPending()
    cancellation = _CancelAt(cancel_at)

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._copy_and_verify_source(
            source,
            cast(WindowsPendingSourceFile, pending),
            operation_id=PENDING_ID,
            byte_size=len(data),
            cancellation=cancellation,
            deadline=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLED
    assert raised.value.context == expected_context
    assert pending.sealed is sealed
    assert pending.rewound is rewound
    assert all(size <= MAXIMUM_STREAM_CHUNK_BYTES for _offset, size in source.reads)
    assert all(size <= MAXIMUM_STREAM_CHUNK_BYTES for size in pending.read_sizes)


@pytest.mark.parametrize(
    ("expire_at", "expected_context", "sealed", "rewound"),
    [
        (2, "source.copy_timeout", False, False),
        (5, "source.copy_timeout", False, False),
        (6, "source.verify_timeout", True, False),
        (7, "source.verify_timeout", True, True),
        (9, "source.verify_timeout", True, True),
    ],
)
def test_copy_timeout_stops_before_the_next_mutating_or_readback_step(
    expire_at: int,
    expected_context: str,
    sealed: bool,
    rewound: bool,
) -> None:
    data = b"x" * (MAXIMUM_STREAM_CHUNK_BYTES + 1)
    source = _SyntheticSource(data)
    pending = _SyntheticPending()

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._copy_and_verify_source(
            source,
            cast(WindowsPendingSourceFile, pending),
            operation_id=PENDING_ID,
            byte_size=len(data),
            cancellation=lambda: False,
            deadline=_ExpireAt(expire_at),
            progress=lambda _value: None,
        )

    assert raised.value.code is SourceIngestErrorCode.TIMED_OUT
    assert raised.value.context == expected_context
    assert pending.sealed is sealed
    assert pending.rewound is rewound
    assert all(size <= MAXIMUM_STREAM_CHUNK_BYTES for _offset, size in source.reads)
    assert all(size <= MAXIMUM_STREAM_CHUNK_BYTES for size in pending.read_sizes)


def test_identifier_collision_exhaustion_is_bounded_and_sanitized() -> None:
    calls = 0

    def occupied_factory() -> UUID:
        nonlocal calls
        calls += 1
        return IMAGE_SOURCE_ID

    with pytest.raises(SourceIngestError) as raised:
        ingest_storage._allocate_identifier(
            occupied_factory,
            {IMAGE_SOURCE_ID},
            context="source.page_id",
        )

    assert calls == ingest_storage._MAXIMUM_IDENTIFIER_ATTEMPTS
    assert raised.value.code is SourceIngestErrorCode.PUBLISH_FAILED
    assert raised.value.context == "source.page_id"
    assert str(IMAGE_SOURCE_ID) not in str(raised.value)
    assert str(IMAGE_SOURCE_ID) not in repr(raised.value)


def test_manifest_snapshot_repr_excludes_payload_display_name_and_digest() -> None:
    payload, _duplicates = _candidate(
        _initial_snapshot(),
        media_type=DetectedMediaType.PNG,
        source_id=IMAGE_SOURCE_ID,
        page_ids=PAGE_IDS[:1],
        timestamp=CREATED_AT + timedelta(seconds=1),
    )
    snapshot = ingest_storage._validate_ingest_manifest(payload, IDENTITY, CASE_ID)
    rendered = repr(snapshot)

    assert DISPLAY_NAME not in rendered
    assert SOURCE_DIGEST not in rendered
    assert payload.decode() not in rendered
    assert "document=" not in rendered
    assert "sources=" not in rendered


def test_confirmed_commit_survives_cleanup_baseexceptions_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _cleanup_fault_store()
    pending = _FaultingPending(fail_mark=True, fail_close=True)
    source_reservation = _FaultingReservation(
        ("originals", f"{IMAGE_SOURCE_ID}.bin"),
        fail_release=True,
    )
    manifest_reservation = _FaultingReservation(("manifest.json",), fail_release=True)
    authority = _FaultingAuthority(fail_close=True)
    prepared = _cleanup_fault_lease(
        store=store,
        pending=pending,
        source_reservation=source_reservation,
        manifest_reservation=manifest_reservation,
        authority=authority,
    )
    expected_manifest = _SyntheticCleanupHandle(fail_close=True)
    manifest_candidate = _SyntheticCleanupHandle(
        identity=FileIdentity(29, b"committed-candidate"),
        fail_close=True,
    )
    monkeypatch.setattr(
        ingest_storage,
        "open_handle",
        lambda _path, **_kwargs: _SyntheticCleanupHandle(),
    )
    monkeypatch.setattr(ingest_storage, "require_expected_handle", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest_storage, "verify_private_acl", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest_storage, "require_no_alternate_streams", lambda *_a: None)
    monkeypatch.setattr(
        ingest_storage,
        "_read_retained_manifest_snapshot",
        lambda *_a, **_k: (_initial_snapshot(), expected_manifest),
    )
    monkeypatch.setattr(
        ingest_storage,
        "write_new_file",
        lambda *_a, **_k: manifest_candidate,
    )
    monkeypatch.setattr(ingest_storage, "publish_new_original", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ingest_storage,
        "replace_manifest_atomically",
        lambda *_a, **_k: ManifestReplaceResult(ManifestReplaceState.COMMITTED),
    )

    result = prepared.commit(
        _inspection(DetectedMediaType.PNG, 1),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _value: None,
    )

    assert result.case_id == CASE_ID
    assert result.source_id == IMAGE_SOURCE_ID
    assert result.page_ids == PAGE_IDS[:1]
    assert result.manifest_revision == 1
    assert pending.marks == 0
    assert pending.closes == 1
    assert expected_manifest.closes == 1
    assert manifest_candidate.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1

    prepared.abort()

    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1


def test_precommit_interrupt_survives_all_handle_close_baseexceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _cleanup_fault_store()
    pending = _FaultingPending(fail_mark=False, fail_close=False)
    source_reservation = _FaultingReservation(
        ("originals", f"{IMAGE_SOURCE_ID}.bin"),
        fail_release=False,
    )
    manifest_reservation = _FaultingReservation(("manifest.json",), fail_release=False)
    authority = _FaultingAuthority(fail_close=False)
    prepared = _cleanup_fault_lease(
        store=store,
        pending=pending,
        source_reservation=source_reservation,
        manifest_reservation=manifest_reservation,
        authority=authority,
    )
    temp_root = _SyntheticCleanupHandle(fail_close=True)
    originals_root = _SyntheticCleanupHandle(fail_close=True)
    expected_manifest = _SyntheticCleanupHandle(fail_close=True)
    manifest_candidate = _SyntheticCleanupHandle(
        identity=FileIdentity(30, b"uncommitted-candidate"),
        fail_close=True,
    )
    interruption = KeyboardInterrupt()

    def open_synthetic_root(path: Path, **_kwargs: object) -> _SyntheticCleanupHandle:
        if path == authority.layout.temp:
            return temp_root
        assert path == authority.layout.originals
        return originals_root

    def interrupt_replace(*_args: object, **_kwargs: object) -> ManifestReplaceResult:
        raise interruption

    monkeypatch.setattr(ingest_storage, "open_handle", open_synthetic_root)
    monkeypatch.setattr(ingest_storage, "require_expected_handle", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest_storage, "verify_private_acl", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest_storage, "require_no_alternate_streams", lambda *_a: None)
    monkeypatch.setattr(
        ingest_storage,
        "_read_retained_manifest_snapshot",
        lambda *_a, **_k: (_initial_snapshot(), expected_manifest),
    )
    monkeypatch.setattr(
        ingest_storage,
        "write_new_file",
        lambda *_a, **_k: manifest_candidate,
    )
    monkeypatch.setattr(ingest_storage, "publish_new_original", lambda *_a, **_k: None)
    monkeypatch.setattr(ingest_storage, "replace_manifest_atomically", interrupt_replace)
    monkeypatch.setattr(
        ingest_storage,
        "_resolve_manifest_destination",
        lambda *_a, **_k: ManifestReplaceState.NOT_COMMITTED,
    )

    with pytest.raises(KeyboardInterrupt) as raised:
        prepared.commit(
            _inspection(DetectedMediaType.PNG, 1),
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value is interruption
    assert temp_root.closes == 1
    assert originals_root.closes == 1
    assert expected_manifest.closes == 1
    assert manifest_candidate.closes == 1
    assert pending.marks == 1
    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1


def test_uncommitted_primary_failure_is_preserved_while_cleanup_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _cleanup_fault_store()
    pending = _FaultingPending(fail_mark=True, fail_close=True)
    source_reservation = _FaultingReservation(
        ("originals", f"{IMAGE_SOURCE_ID}.bin"),
        fail_release=True,
    )
    manifest_reservation = _FaultingReservation(("manifest.json",), fail_release=True)
    authority = _FaultingAuthority(fail_close=True)
    prepared = _cleanup_fault_lease(
        store=store,
        pending=pending,
        source_reservation=source_reservation,
        manifest_reservation=manifest_reservation,
        authority=authority,
    )
    primary = SourceIngestError(
        code=SourceIngestErrorCode.MANIFEST_CONFLICT,
        context="source.manifest_cas",
    )

    def fail_candidate(*_args: object, **_kwargs: object) -> tuple[bytes, tuple[UUID, ...]]:
        raise primary

    monkeypatch.setattr(ingest_storage, "_build_candidate_manifest", fail_candidate)

    with pytest.raises(SourceIngestError) as raised:
        prepared.commit(
            _inspection(DetectedMediaType.PNG, 1),
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value is primary
    assert pending.marks == 1
    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1

    prepared.abort()

    assert pending.marks == 1
    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1


def test_precommit_validation_failure_also_aborts_with_sanitized_primary_error() -> None:
    store = _cleanup_fault_store()
    pending = _FaultingPending(fail_mark=True, fail_close=True)
    source_reservation = _FaultingReservation(
        ("originals", f"{IMAGE_SOURCE_ID}.bin"),
        fail_release=True,
    )
    manifest_reservation = _FaultingReservation(("manifest.json",), fail_release=True)
    authority = _FaultingAuthority(fail_close=True)
    prepared = _cleanup_fault_lease(
        store=store,
        pending=pending,
        source_reservation=source_reservation,
        manifest_reservation=manifest_reservation,
        authority=authority,
    )
    wrong_inspection = replace(
        _inspection(DetectedMediaType.PNG, 1),
        pending_id=PDF_SOURCE_ID,
    )

    with pytest.raises(SourceIngestError) as raised:
        prepared.commit(
            wrong_inspection,
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _value: None,
        )

    assert raised.value.code is SourceIngestErrorCode.INSPECTION_MISMATCH
    assert raised.value.context == "source.commit_inspection"
    assert "private" not in str(raised.value)
    assert pending.marks == 1
    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1

    prepared.abort()

    assert pending.marks == 1
    assert pending.closes == 1
    assert source_reservation.releases == 1
    assert manifest_reservation.releases == 1
    assert authority.closes == 1
