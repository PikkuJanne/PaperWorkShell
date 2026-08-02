from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.source_ingest import (
    CancellationProbe,
    PreparedSourceLease,
    ProgressSink,
    SourceIngestService,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    DuplicateContentSignal,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityResult,
    SourceIntegrityStatus,
    SourceProgressPhase,
    SourceSignatureState,
)


def _uuid(number: int) -> UUID:
    return UUID(f"20000000-0000-4000-8000-{number:012d}")


def _summary() -> PreparedSourceSummary:
    return PreparedSourceSummary(
        pending_id=_uuid(1),
        case_id=_uuid(2),
        source_id=_uuid(3),
        media_type=DetectedMediaType.PDF,
        byte_size=25,
        sha256="c" * 64,
    )


def _inspection(*, pending_id: UUID | None = None) -> CompletedSourceInspection:
    return CompletedSourceInspection(
        pending_id=pending_id or _uuid(1),
        media_type=DetectedMediaType.PDF,
        page_count=2,
        signature_state=SourceSignatureState.NONE_DETECTED,
        encryption_state=SourceEncryptionState.UNENCRYPTED,
    )


def _result(
    *,
    case_id: UUID | None = None,
    source_id: UUID | None = None,
    page_ids: tuple[UUID, ...] = (_uuid(4), _uuid(5)),
) -> SourceImportResult:
    return SourceImportResult(
        case_id=case_id or _uuid(2),
        source_id=source_id or _uuid(3),
        page_ids=page_ids,
        media_type=DetectedMediaType.PDF,
        byte_size=25,
        sha256="c" * 64,
        manifest_revision=1,
        duplicate=DuplicateContentSignal(),
    )


class _Lease:
    def __init__(
        self,
        events: list[str],
        *,
        summary: object | None = None,
        result: object | None = None,
        commit_error: Exception | None = None,
        exit_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._summary = _summary() if summary is None else summary
        self._result = _result() if result is None else result
        self._commit_error = commit_error
        self._exit_error = exit_error
        self.received_inspection: CompletedSourceInspection | None = None
        self.received_cancellation: CancellationProbe | None = None
        self.received_progress: ProgressSink | None = None

    @property
    def summary(self) -> PreparedSourceSummary:
        self._events.append("summary")
        return cast(PreparedSourceSummary, self._summary)

    def __enter__(self) -> Self:
        self._events.append("enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self._events.append("exit")
        if self._exit_error is not None:
            raise self._exit_error

    def commit(
        self,
        inspection: CompletedSourceInspection,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceImportResult:
        self._events.append("commit")
        self.received_inspection = inspection
        self.received_cancellation = cancellation
        self.received_progress = progress
        if self._commit_error is not None:
            raise self._commit_error
        return cast(SourceImportResult, self._result)


class _Store:
    def __init__(self, events: list[str], lease: _Lease) -> None:
        self.events = events
        self.lease = lease
        self.prepare_error: Exception | None = None
        self.integrity_error: Exception | None = None
        self.integrity_result: object = SourceIntegrityResult(
            _uuid(2), _uuid(3), SourceIntegrityStatus.VERIFIED
        )
        self.display_name: str | None = None
        self.source_path: Path | None = None
        self.prepare_cancellation: CancellationProbe | None = None
        self.prepare_progress: ProgressSink | None = None

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> PreparedSourceLease:
        assert case_id == _uuid(2)
        self.events.append("prepare")
        self.display_name = display_name
        self.source_path = source_path
        self.prepare_cancellation = cancellation
        self.prepare_progress = progress
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.lease

    def verify_integrity(
        self,
        case_id: UUID,
        source_id: UUID,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceIntegrityResult:
        assert case_id == _uuid(2)
        assert source_id == _uuid(3)
        self.events.append("verify")
        if self.integrity_error is not None:
            raise self.integrity_error
        cancellation()
        progress(
            SourceImportProgress(
                operation_id=_uuid(9),
                phase=SourceProgressPhase.VERIFYING_INTEGRITY,
                completed_units=1,
                total_units=1,
            )
        )
        return cast(SourceIntegrityResult, self.integrity_result)


class _Inspector:
    def __init__(self, events: list[str], value: object | None = None) -> None:
        self._events = events
        self.value = _inspection() if value is None else value
        self.error: Exception | None = None
        self.received_source: PreparedSourceSummary | None = None
        self.received_cancellation: CancellationProbe | None = None
        self.received_progress: ProgressSink | None = None

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        self._events.append("inspect")
        self.received_source = source
        self.received_cancellation = cancellation
        self.received_progress = progress
        if self.error is not None:
            raise self.error
        return cast(CompletedSourceInspection, self.value)


class _CancellationSequence:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        value = self.values.pop(0) if self.values else False
        if isinstance(value, Exception):
            raise value
        return cast(bool, value)


def _service(
    *,
    lease_summary: object | None = None,
    lease_result: object | None = None,
    inspection: object | None = None,
    commit_error: Exception | None = None,
    exit_error: Exception | None = None,
) -> tuple[SourceIngestService, _Store, _Inspector, _Lease, list[str]]:
    events: list[str] = []
    lease = _Lease(
        events,
        summary=lease_summary,
        result=lease_result,
        commit_error=commit_error,
        exit_error=exit_error,
    )
    store = _Store(events, lease)
    inspector = _Inspector(events, inspection)
    return SourceIngestService(store, inspector), store, inspector, lease, events


def test_service_orders_prepare_inspection_and_commit_with_hardened_callbacks() -> None:
    service, store, inspector, lease, events = _service()
    cancellation = _CancellationSequence(False, False, False, True)
    reported: list[SourceImportProgress] = []

    result = service.import_source(
        _uuid(2),
        Path("C:/synthetic/source.pdf"),
        "Synthetic source.pdf",
        cancellation=cancellation,
        progress=reported.append,
    )

    assert result == _result()
    assert events == ["prepare", "enter", "summary", "inspect", "commit", "exit"]
    assert cancellation.calls == 3
    assert store.display_name == "Synthetic source.pdf"
    assert store.source_path == Path("C:/synthetic/source.pdf")
    assert inspector.received_source == _summary()
    assert lease.received_inspection == _inspection()
    assert store.prepare_cancellation is inspector.received_cancellation
    assert store.prepare_cancellation is lease.received_cancellation
    assert store.prepare_progress is inspector.received_progress
    assert store.prepare_progress is lease.received_progress


def test_cancellation_before_prepare_performs_no_storage_work() -> None:
    service, _, _, _, events = _service()

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(
            _uuid(2),
            Path("C:/synthetic/source.pdf"),
            "Synthetic.pdf",
            cancellation=lambda: True,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLED
    assert events == []


def test_cancellation_after_prepare_closes_pending_source_without_inspection() -> None:
    service, _, _, _, events = _service()
    cancellation = _CancellationSequence(False, True)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(
            _uuid(2),
            Path("C:/synthetic/source.pdf"),
            "Synthetic.pdf",
            cancellation=cancellation,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLED
    assert events == ["prepare", "enter", "summary", "exit"]


def test_cancellation_after_inspection_closes_without_commit() -> None:
    service, _, _, _, events = _service()
    cancellation = _CancellationSequence(False, False, True)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(
            _uuid(2),
            Path("C:/synthetic/source.pdf"),
            "Synthetic.pdf",
            cancellation=cancellation,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLED
    assert events == ["prepare", "enter", "summary", "inspect", "exit"]


def test_inspection_must_bind_pending_id_and_media_type() -> None:
    service, _, _, _, events = _service(inspection=_inspection(pending_id=_uuid(8)))

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "Synthetic.pdf")

    assert raised.value.code is SourceIngestErrorCode.INSPECTION_MISMATCH
    assert events == ["prepare", "enter", "summary", "inspect", "exit"]


@pytest.mark.parametrize(
    "result",
    (
        _result(case_id=_uuid(8)),
        _result(source_id=_uuid(8)),
        _result(page_ids=(_uuid(4),)),
        object(),
    ),
)
def test_commit_result_must_match_every_prepared_and_inspected_fact(result: object) -> None:
    service, _, _, _, events = _service(lease_result=result)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "Synthetic.pdf")

    assert raised.value.code is SourceIngestErrorCode.RESULT_INVALID
    assert events == ["prepare", "enter", "summary", "inspect", "commit", "exit"]


def test_validated_commit_result_wins_over_late_lease_close_error() -> None:
    service, _, _, _, events = _service(exit_error=RuntimeError("private close detail"))

    result = service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "Synthetic.pdf")

    assert result == _result()
    assert events[-2:] == ["commit", "exit"]


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    (
        ("prepare", SourceIngestErrorCode.PREPARE_FAILED),
        ("inspect", SourceIngestErrorCode.INSPECT_FAILED),
        ("commit", SourceIngestErrorCode.COMMIT_FAILED),
    ),
)
def test_raw_component_exceptions_are_sanitized_by_current_phase(
    stage: str,
    expected_code: SourceIngestErrorCode,
) -> None:
    canary = "private path and parser value"
    service, store, inspector, _, _ = _service(
        commit_error=RuntimeError(canary) if stage == "commit" else None
    )
    if stage == "prepare":
        store.prepare_error = RuntimeError(canary)
    elif stage == "inspect":
        inspector.error = RuntimeError(canary)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "Synthetic.pdf")

    assert raised.value.code is expected_code
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)
    assert canary not in repr(raised.value.args)


def test_closed_source_errors_from_components_are_preserved() -> None:
    service, store, _, _, _ = _service()
    store.prepare_error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "source.terminal_identity",
    )

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "Synthetic.pdf")

    assert raised.value.code is SourceIngestErrorCode.SOURCE_CHANGED


@pytest.mark.parametrize("bad_value", (1, "false", None))
def test_cancellation_probe_requires_exact_boolean_and_hides_callback_errors(
    bad_value: object,
) -> None:
    service, _, _, _, events = _service()
    callback = cast(CancellationProbe, lambda: bad_value)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(
            _uuid(2),
            Path("C:/synthetic/source.pdf"),
            "Synthetic.pdf",
            cancellation=callback,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLATION_FAILED
    assert events == []


def test_progress_callback_errors_are_closed_and_abort_component_work() -> None:
    service, store, _, _, _ = _service()
    canary = "private progress detail"

    def prepare_with_progress(
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> PreparedSourceLease:
        del case_id, source_path, display_name, cancellation
        progress(SourceImportProgress(_uuid(1), SourceProgressPhase.COPYING, 0, 1))
        return store.lease

    store.prepare = prepare_with_progress  # type: ignore[method-assign]

    def failing_progress(_progress: SourceImportProgress) -> None:
        raise RuntimeError(canary)

    with pytest.raises(SourceIngestError) as raised:
        service.import_source(
            _uuid(2),
            Path("C:/synthetic/source.pdf"),
            "Synthetic.pdf",
            progress=failing_progress,
        )

    assert raised.value.code is SourceIngestErrorCode.PROGRESS_FAILED
    assert canary not in str(raised.value)


def test_integrity_verification_hardens_callbacks_and_exact_result_identity() -> None:
    service, _, _, _, events = _service()
    reported: list[SourceImportProgress] = []

    result = service.verify_integrity(
        _uuid(2),
        _uuid(3),
        cancellation=lambda: False,
        progress=reported.append,
    )

    assert result.status is SourceIntegrityStatus.VERIFIED
    assert events == ["verify"]
    assert len(reported) == 1
    assert reported[0].phase is SourceProgressPhase.VERIFYING_INTEGRITY


def test_integrity_verification_rejects_mismatched_or_raw_results() -> None:
    service, store, _, _, _ = _service()
    store.integrity_result = SourceIntegrityResult(
        _uuid(2), _uuid(8), SourceIntegrityStatus.VERIFIED
    )

    with pytest.raises(SourceIngestError) as raised:
        service.verify_integrity(_uuid(2), _uuid(3))

    assert raised.value.code is SourceIngestErrorCode.RESULT_INVALID

    store.integrity_error = RuntimeError("private verifier value")
    with pytest.raises(SourceIngestError) as raw:
        service.verify_integrity(_uuid(2), _uuid(3))
    assert raw.value.code is SourceIngestErrorCode.VERIFY_FAILED
    assert "private verifier value" not in str(raw.value)


def test_invalid_path_and_display_name_fail_before_storage() -> None:
    service, _, _, _, events = _service()

    with pytest.raises(SourceIngestError) as bad_path:
        service.import_source(_uuid(2), cast(Path, "C:/synthetic/source.pdf"), "Synthetic.pdf")
    assert bad_path.value.code is SourceIngestErrorCode.SOURCE_PATH_INVALID

    with pytest.raises(SourceIngestError) as bad_display:
        service.import_source(_uuid(2), Path("C:/synthetic/source.pdf"), "../private.pdf")
    assert bad_display.value.code is SourceIngestErrorCode.DISPLAY_NAME_INVALID
    assert events == []
