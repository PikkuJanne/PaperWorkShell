from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from uuid import UUID

import jsonschema
import pytest

from paperworkshell.app.export_history import PdfExportHistoryService
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import (
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_export_history import (
    PDF_EXPORT_HISTORY_RECORD_VERSION,
    PDF_EXPORT_HISTORY_SCHEMA_VERSION,
    PdfExportHistoryAppendResult,
    PdfExportHistoryAppendStatus,
    PdfExportHistoryDocument,
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    PdfExportHistorySnapshot,
    build_pdf_export_history_record,
    canonical_pdf_export_history_bytes,
    canonical_pdf_export_history_record_bytes,
    decode_pdf_export_history,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000711")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000711")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000711")
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000711")
RECORDED_AT = datetime(2026, 8, 6, 10, 11, 12, 123456, tzinfo=UTC)


def _plan(
    *,
    signed: bool = False,
    manifest_revision: int = 7,
    manifest_sha256: str = "b" * 64,
) -> PdfExportPlan:
    source = PdfExportSource(
        source_id=SOURCE_ID,
        byte_size=99,
        page_count=1,
        signature_state=(
            PdfExportSourceSignatureState.MARKERS_DETECTED
            if signed
            else PdfExportSourceSignatureState.NONE_DETECTED
        ),
        sha256="a" * 64,
    )
    return PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=manifest_revision,
        manifest_sha256=manifest_sha256,
        manifest_cas_token="history_manifest_cas_token_0001",
        sources=(source,),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=PAGE_ID,
                source_id=SOURCE_ID,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_90,
            ),
        ),
    )


def _outcome(plan: PdfExportPlan | None = None) -> PdfExportOutcome:
    selected = _plan() if plan is None else plan
    report = PdfExportVerificationReport.for_verified_plan(
        selected,
        output_sha256="c" * 64,
        output_byte_size=1234,
    )
    return PdfExportOutcome(
        export_id=EXPORT_ID,
        status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
        report=report,
        destination_reopened=True,
    )


def _record(*, signed: bool = False) -> PdfExportHistoryRecord:
    plan = _plan(signed=signed)
    return build_pdf_export_history_record(plan, _outcome(plan), recorded_at=RECORDED_AT)


def _manifest_document(
    *, revision: int = 7, updated_at: str = "2026-08-06T09:00:00Z"
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-06T08:00:00Z",
        "updated_at": updated_at,
        "template_ref": None,
        "sources": [],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _manifest_snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _manifest_document() if document is None else document
    return ManifestSnapshot(
        CASE_ID,
        1,
        cast(int, selected["revision"]),
        selected,
        hashlib.sha256(repr(selected).encode("utf-8")).hexdigest(),
        "manifest_history_cas_0000001",
    )


class _MemoryHistory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.document = PdfExportHistoryDocument()
        self.fail_append = False
        self.append_calls = 0

    def load(self, case_id: UUID) -> PdfExportHistorySnapshot:
        assert case_id == CASE_ID
        self.events.append("history.load")
        payload = canonical_pdf_export_history_bytes(self.document)
        digest = hashlib.sha256(payload).hexdigest()
        return PdfExportHistorySnapshot(self.document, digest, "d" * 64)

    def append(
        self,
        case_id: UUID,
        record: PdfExportHistoryRecord,
    ) -> PdfExportHistoryAppendResult:
        assert case_id == CASE_ID
        self.events.append("history.append")
        self.append_calls += 1
        if self.fail_append:
            raise PdfExportHistoryError(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.test",
            )
        candidate, appended = self.document.append(record)
        self.document = candidate
        return PdfExportHistoryAppendResult(
            (
                PdfExportHistoryAppendStatus.PUBLISHED
                if appended
                else PdfExportHistoryAppendStatus.ALREADY_PRESENT
            ),
            record.export_id,
            record.sha256,
            len(candidate.records),
        )


class _MemoryManifests:
    def __init__(self, events: list[str], document: dict[str, object] | None = None) -> None:
        self.events = events
        self.snapshot = _manifest_snapshot(document)
        self.save_failures: list[ManifestRepositoryErrorCode] = []
        self.save_calls = 0

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.events.append("manifest.load")
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, self.snapshot)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == CASE_ID
        self.events.append("manifest.save")
        self.save_calls += 1
        assert expected_revision == self.snapshot.revision
        assert expected_sha256 == self.snapshot.sha256
        assert expected_cas_token == self.snapshot.cas_token
        if self.save_failures:
            raise ManifestRepositoryError(
                self.save_failures.pop(0),
                "manifest.test",
            )
        saved_document = dict(candidate)
        self.snapshot = ManifestSnapshot(
            CASE_ID,
            1,
            cast(int, saved_document["revision"]),
            saved_document,
            hashlib.sha256(repr(saved_document).encode("utf-8")).hexdigest(),
            f"manifest_history_cas_{self.save_calls:07d}",
        )
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.snapshot)


def test_verified_record_is_path_free_and_manifest_digest_is_exact() -> None:
    record = _record()
    document = record.to_document()

    assert record.record_version == PDF_EXPORT_HISTORY_RECORD_VERSION
    assert record.recorded_at == "2026-08-06T10:11:12.123456Z"
    assert record.manifest_revision == 7
    assert record.manifest_sha256 == "b" * 64
    assert record.semantic_fingerprint == _plan().semantic_fingerprint
    assert record.output_sha256 == "c" * 64
    assert record.output_byte_size == 1234
    assert record.page_count == 1
    assert record.manifest_reference == {
        "export_id": str(EXPORT_ID),
        "record_version": 1,
        "sha256": hashlib.sha256(canonical_pdf_export_history_record_bytes(record)).hexdigest(),
    }
    assert set(document) == {
        "export_id",
        "record_version",
        "recorded_at",
        "manifest_revision",
        "manifest_sha256",
        "semantic_fingerprint",
        "output_sha256",
        "output_byte_size",
        "page_count",
        "check_codes",
        "unsigned_derivative",
    }
    encoded = json.dumps(document, sort_keys=True)
    for prohibited in ("path", "filename", "destination", "C:/", ".pdf"):
        assert prohibited not in encoded


def test_current_document_round_trips_canonically_and_schema_copies_match() -> None:
    unsigned = _record()
    signed = _record(signed=True)
    # Give the second record a distinct internal identity while retaining its signed facts.
    signed = PdfExportHistoryRecord(
        export_id=UUID("50000000-0000-4000-8000-000000000712"),
        recorded_at=signed.recorded_at,
        manifest_revision=signed.manifest_revision,
        manifest_sha256=signed.manifest_sha256,
        semantic_fingerprint=signed.semantic_fingerprint,
        output_sha256=signed.output_sha256,
        output_byte_size=signed.output_byte_size,
        page_count=signed.page_count,
        check_codes=signed.check_codes,
        unsigned_derivative=signed.unsigned_derivative,
    )
    history = PdfExportHistoryDocument(records=(unsigned, signed))
    payload = canonical_pdf_export_history_bytes(history)

    assert decode_pdf_export_history(payload) == history
    assert canonical_pdf_export_history_bytes(decode_pdf_export_history(payload)) == payload
    root = Path(__file__).resolve().parents[2]
    canonical_schema = (root / "schemas" / "export-history-v1.schema.json").read_bytes()
    packaged_schema = (
        root / "src" / "paperworkshell" / "storage" / "export-history-v1.schema.json"
    ).read_bytes()
    assert canonical_schema == packaged_schema
    jsonschema.Draft202012Validator(json.loads(canonical_schema)).validate(history.to_document())


@pytest.mark.parametrize(
    "mutation",
    (
        {"schema_version": 2, "records": []},
        {"schema_version": 0, "records": []},
        {"schema_version": 1, "records": [], "destination": "private.pdf"},
    ),
)
def test_future_legacy_and_unknown_document_shapes_fail_before_use(
    mutation: dict[str, object],
) -> None:
    payload = json.dumps(mutation, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(PdfExportHistoryError) as raised:
        decode_pdf_export_history(payload)

    expected = (
        PdfExportHistoryErrorCode.VERSION_UNSUPPORTED
        if mutation.get("schema_version") != 1
        else PdfExportHistoryErrorCode.DOCUMENT_INVALID
    )
    assert raised.value.code is expected


def test_future_record_version_and_noncanonical_or_duplicate_json_fail_closed() -> None:
    record = _record().to_document()
    record["record_version"] = 2
    future = {"schema_version": 1, "records": [record]}
    payload = json.dumps(future, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(PdfExportHistoryError) as version:
        decode_pdf_export_history(payload)
    assert version.value.code is PdfExportHistoryErrorCode.VERSION_UNSUPPORTED

    with pytest.raises(PdfExportHistoryError) as duplicate:
        decode_pdf_export_history(b'{"schema_version":1,"schema_version":1,"records":[]}\n')
    assert duplicate.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID

    with pytest.raises(PdfExportHistoryError) as noncanonical:
        decode_pdf_export_history(b'{"records": [], "schema_version": 1}\n')
    assert noncanonical.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID


@pytest.mark.parametrize(
    "payload",
    (
        b'{"extra":' + (b"[" * 17) + b"0" + (b"]" * 17) + b',"records":[],"schema_version":1}\n',
        b'{"extra":[' + (b"0," * 150_000) + b'0],"records":[],"schema_version":1}\n',
        b'{"extra":"' + (b"x" * 129) + b'","records":[],"schema_version":1}\n',
    ),
    ids=("depth", "nodes", "single-string"),
)
def test_json_resource_limits_reject_before_materialization(
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_decoder(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("json.loads must not receive an over-limit history payload")

    monkeypatch.setattr(
        "paperworkshell.domain.pdf_export_history.json.loads",
        forbidden_decoder,
    )

    with pytest.raises(PdfExportHistoryError) as raised:
        decode_pdf_export_history(payload)

    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert raised.value.context == "export_history.json_limits"


def test_exact_append_is_idempotent_but_same_export_id_mismatch_fails() -> None:
    record = _record()
    document, appended = PdfExportHistoryDocument().append(record)
    retried, appended_again = document.append(record)

    assert appended
    assert not appended_again
    assert retried is document

    changed = copy.deepcopy(record.to_document())
    changed["output_sha256"] = "e" * 64
    conflicting = PdfExportHistoryRecord(
        export_id=record.export_id,
        recorded_at=cast(str, changed["recorded_at"]),
        manifest_revision=cast(int, changed["manifest_revision"]),
        manifest_sha256=cast(str, changed["manifest_sha256"]),
        semantic_fingerprint=cast(str, changed["semantic_fingerprint"]),
        output_sha256=cast(str, changed["output_sha256"]),
        output_byte_size=cast(int, changed["output_byte_size"]),
        page_count=cast(int, changed["page_count"]),
        check_codes=record.check_codes,
        unsigned_derivative=record.unsigned_derivative,
    )
    with pytest.raises(PdfExportHistoryError) as raised:
        document.append(conflicting)
    assert raised.value.code is PdfExportHistoryErrorCode.RECORD_MISMATCH


def test_record_timestamp_must_be_explicit_exact_utc() -> None:
    plan = _plan()
    outcome = _outcome(plan)
    non_utc = RECORDED_AT.astimezone(timezone(timedelta(hours=2)))

    with pytest.raises(PdfExportHistoryError) as raised:
        build_pdf_export_history_record(plan, outcome, recorded_at=non_utc)

    assert raised.value.code is PdfExportHistoryErrorCode.MODEL_INVALID


def test_repr_and_errors_do_not_disclose_record_values_or_paths() -> None:
    record = _record()
    canary = "Private Applicant C:/private/final-name.pdf"
    error = PdfExportHistoryError(code=canary, context=canary)  # type: ignore[arg-type]

    rendered = f"{record!r}\n{PdfExportHistoryDocument((record,))!r}\n{error!s}\n{error!r}"
    for secret in (
        canary,
        str(record.export_id),
        record.manifest_sha256,
        record.semantic_fingerprint,
        record.output_sha256,
    ):
        assert secret not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_service_appends_record_before_manifest_reference_and_returns_recorded() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    manifests = _MemoryManifests(events)
    outcome = _outcome()
    record = _record()

    result = PdfExportHistoryService(history, manifests).record_and_link(
        _plan(),
        record,
        outcome,
    )

    assert result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert events == ["history.append", "manifest.load", "manifest.save"]
    entries = cast(
        list[dict[str, object]],
        cast(dict[str, object], manifests.snapshot.document["export_history"])["entries"],
    )
    assert entries == [record.manifest_reference]
    assert entries[0]["sha256"] == record.sha256


def test_service_rejects_record_from_a_different_manifest_before_history_write() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    manifests = _MemoryManifests(events)

    with pytest.raises(PdfExportHistoryError) as raised:
        PdfExportHistoryService(history, manifests).record_and_link(
            _plan(manifest_sha256="e" * 64),
            _record(),
            _outcome(),
        )

    assert raised.value.code is PdfExportHistoryErrorCode.RECORD_MISMATCH
    assert events == []


def test_service_rejects_hostile_record_type_before_attribute_access() -> None:
    canary = "Private Applicant C:/private/final-name.pdf"

    class _HostileRecord:
        @property
        def manifest_revision(self) -> int:
            raise RuntimeError(canary)

    events: list[str] = []
    history = _MemoryHistory(events)
    manifests = _MemoryManifests(events)

    with pytest.raises(PdfExportHistoryError) as raised:
        PdfExportHistoryService(history, manifests).record_and_link(
            _plan(),
            cast(PdfExportHistoryRecord, _HostileRecord()),
            _outcome(),
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is PdfExportHistoryErrorCode.MODEL_INVALID
    assert canary not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert events == []


def test_manifest_link_failure_leaves_durable_orphan_and_retry_never_reexports() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    manifests = _MemoryManifests(events)
    manifests.save_failures = [ManifestRepositoryErrorCode.SAVE_FAILED]
    outcome = _outcome()
    record = _record()
    service = PdfExportHistoryService(history, manifests)

    first = service.record_and_link(_plan(), record, outcome)
    assert first.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert history.document.records == (record,)
    assert (
        cast(
            list[object],
            cast(dict[str, object], manifests.snapshot.document["export_history"])["entries"],
        )
        == []
    )

    second = service.record_and_link(_plan(), record, first)
    assert second.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert history.append_calls == 2
    assert history.document.records == (record,)
    assert events.count("manifest.save") == 2


def test_retry_manifest_link_reads_exact_orphan_without_append() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    record = _record()
    history.document = PdfExportHistoryDocument((record,))
    manifests = _MemoryManifests(events)

    result = PdfExportHistoryService(history, manifests).retry_manifest_link(
        CASE_ID,
        record.export_id,
        record.sha256,
        _outcome(),
    )

    assert result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert history.append_calls == 0
    assert events == ["history.load", "manifest.load", "manifest.save"]


def test_record_write_failure_does_not_attempt_manifest_link() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    history.fail_append = True
    manifests = _MemoryManifests(events)

    result = PdfExportHistoryService(history, manifests).record_and_link(
        _plan(),
        _record(),
        _outcome(),
    )

    assert result.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert events == ["history.append"]
    assert manifests.save_calls == 0


def test_manifest_conflict_reloads_and_retries_with_monotonic_timestamp() -> None:
    events: list[str] = []
    history = _MemoryHistory(events)
    later = "2026-08-06T11:00:00.000000Z"
    manifests = _MemoryManifests(events, _manifest_document(updated_at=later))
    manifests.save_failures = [ManifestRepositoryErrorCode.REVISION_CONFLICT]

    result = PdfExportHistoryService(history, manifests).record_and_link(
        _plan(),
        _record(),
        _outcome(),
    )

    assert result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert manifests.save_calls == 2
    assert manifests.snapshot.document["updated_at"] == "2026-08-06T11:00:00.000001Z"


def test_existing_manifest_reference_is_idempotent_and_mismatch_fails_closed() -> None:
    record = _record()
    matching = _manifest_document()
    cast(dict[str, object], matching["export_history"])["entries"] = [record.manifest_reference]
    events: list[str] = []
    history = _MemoryHistory(events)
    manifests = _MemoryManifests(events, matching)

    result = PdfExportHistoryService(history, manifests).record_and_link(
        _plan(),
        record,
        _outcome(),
    )
    assert result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert manifests.save_calls == 0

    conflicting = _manifest_document()
    cast(dict[str, object], conflicting["export_history"])["entries"] = [
        {
            "export_id": str(record.export_id),
            "record_version": 1,
            "sha256": "f" * 64,
        }
    ]
    with pytest.raises(PdfExportHistoryError) as raised:
        PdfExportHistoryService(history, _MemoryManifests([], conflicting)).record_and_link(
            _plan(),
            record,
            _outcome(),
        )
    assert raised.value.code is PdfExportHistoryErrorCode.RECORD_MISMATCH


def test_schema_rejects_path_filename_and_future_version_fields() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "export-history-v1.schema.json"
    validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text("utf-8")))
    document = PdfExportHistoryDocument((_record(),)).to_document()

    for member in ("destination_path", "filename"):
        candidate = copy.deepcopy(document)
        cast(dict[str, object], cast(list[object], candidate["records"])[0])[member] = "x"
        assert list(validator.iter_errors(candidate))
    future = copy.deepcopy(document)
    future["schema_version"] = PDF_EXPORT_HISTORY_SCHEMA_VERSION + 1
    assert list(validator.iter_errors(future))

    inconsistent_checks = copy.deepcopy(document)
    inconsistent_record = cast(
        dict[str, object], cast(list[object], inconsistent_checks["records"])[0]
    )
    inconsistent_record["unsigned_derivative"] = True
    assert list(validator.iter_errors(inconsistent_checks))
