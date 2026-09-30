"""Exact saved-source authority and lifecycle checks for PWS-0501."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_inspection import (
    PdfFeatureState,
    PdfInspectionError,
    PdfInspectionErrorCode,
)
from paperworkshell.domain.source_ingest import (
    SourceIngestError,
    SourceIngestErrorCode,
    SourceSignatureState,
)
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from tests.unit.worker.test_pdf_source_inspector import _opened_result, _source, _stack, _uuid


def _snapshot() -> ManifestSnapshot:
    return ManifestSnapshot(
        _uuid(2),
        2,
        1,
        {
            "schema_version": 2,
            "case_id": str(_uuid(2)),
            "revision": 1,
            "sources": [
                {
                    "source_id": str(_uuid(3)),
                    "detected_media_type": "application/pdf",
                    "byte_size": 100,
                    "sha256": "a" * 64,
                }
            ],
        },
        "b" * 64,
        "synthetic-token-0001",
    )


@dataclass
class _CommittedLease:
    snapshot: ManifestSnapshot
    events: list[str]
    source_id: UUID = _uuid(3)
    byte_size: int = 100
    sha256: str = "a" * 64
    handle_value: int = 501
    fail_revalidation: bool = False

    def __enter__(self) -> Self:
        self.events.append("committed.enter")
        return self

    def __exit__(self, *_exc: object) -> None:
        if "worker.start" in self.events:
            assert "worker.terminate" in self.events
        self.events.append("committed.exit")

    def revalidate(self, *, deadline: Callable[[], bool], cancellation: Callable[[], bool]) -> None:
        assert "worker.terminate" in self.events
        assert not deadline() and not cancellation()
        self.events.append("committed.revalidate")
        if self.fail_revalidation:
            raise PdfInspectionError(PdfInspectionErrorCode.SOURCE_CHANGED, "test.revalidate")


@dataclass
class _CommittedStore:
    lease: _CommittedLease

    def borrow_pdf_inspection_source(
        self,
        snapshot: ManifestSnapshot,
        source_id: UUID,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> _CommittedLease:
        assert snapshot == _snapshot() and source_id == _uuid(3)
        assert not deadline() and not cancellation()
        return self.lease


def test_committed_result_is_source_bound_and_revalidated_after_worker_termination() -> None:
    inspector, worker, _pipe, _lease, events = _stack()
    inspector._committed_sources = _CommittedStore(_CommittedLease(_snapshot(), events))
    result = inspector.inspect_committed(
        _snapshot(), _uuid(3), deadline=lambda: False, cancellation=lambda: False
    )
    assert result == _opened_result()
    assert worker.payload is not None
    assert worker.payload.inputs[0].object_id == _uuid(3)
    assert worker.payload.inputs[0].expected_sha256 == "a" * 64
    assert events.index("worker.terminate") < events.index("committed.revalidate")
    assert events.index("committed.revalidate") < events.index("committed.exit")
    assert "broker.borrow" not in events


@pytest.mark.parametrize("mismatch", ["source", "digest", "size", "snapshot"])
def test_committed_borrow_binding_mismatch_fails_before_worker_start(mismatch: str) -> None:
    inspector, worker, _pipe, _lease, events = _stack()
    lease = _CommittedLease(_snapshot(), events)
    if mismatch == "source":
        lease.source_id = _uuid(99)
    elif mismatch == "digest":
        lease.sha256 = "c" * 64
    elif mismatch == "size":
        lease.byte_size += 1
    else:
        snapshot = _snapshot()
        lease.snapshot = ManifestSnapshot(
            snapshot.case_id, 2, 1, snapshot.document, "c" * 64, snapshot.cas_token
        )
    inspector._committed_sources = _CommittedStore(lease)
    with pytest.raises(PdfInspectionError) as error:
        inspector.inspect_committed(
            _snapshot(), _uuid(3), deadline=lambda: False, cancellation=lambda: False
        )
    assert error.value.code is PdfInspectionErrorCode.SOURCE_CHANGED
    assert "worker.start" not in events
    assert events[-1] == "committed.exit"
    assert not worker.terminated


def test_committed_revalidation_failure_never_returns_a_report() -> None:
    inspector, worker, _pipe, _lease, events = _stack()
    inspector._committed_sources = _CommittedStore(
        _CommittedLease(_snapshot(), events, fail_revalidation=True)
    )
    with pytest.raises(PdfInspectionError) as error:
        inspector.inspect_committed(
            _snapshot(), _uuid(3), deadline=lambda: False, cancellation=lambda: False
        )
    assert error.value.code is PdfInspectionErrorCode.SOURCE_CHANGED
    assert worker.terminated and events[-1] == "committed.exit"


@pytest.mark.parametrize("failure", ["cancel", "timeout", "bad_report"])
def test_committed_failure_terminates_worker_before_releasing_source(failure: str) -> None:
    inspector, worker, pipe, _lease, events = _stack(corrupt_frame=failure == "bad_report")
    inspector._committed_sources = _CommittedStore(_CommittedLease(_snapshot(), events))
    with pytest.raises(PdfInspectionError) as error:
        inspector.inspect_committed(
            _snapshot(),
            _uuid(3),
            deadline=lambda: failure == "timeout" and "worker.operation" in events,
            cancellation=lambda: failure == "cancel" and "worker.operation" in events,
        )
    assert (
        error.value.code
        is {
            "cancel": PdfInspectionErrorCode.CANCELLED,
            "timeout": PdfInspectionErrorCode.TIMED_OUT,
            "bad_report": PdfInspectionErrorCode.REPORT_INVALID,
        }[failure]
    )
    assert worker.terminated
    assert events.index("worker.terminate") < events.index("committed.exit")
    assert "committed.revalidate" not in events
    assert pipe.read_called is (failure == "bad_report")


def test_legacy_report_cannot_satisfy_current_worker_operation() -> None:
    inspector, worker, _pipe, _lease, _events = _stack(
        result=replace(_opened_result(), report_version=1)
    )
    with pytest.raises(PdfInspectionError) as error:
        inspector.inspect_structural(_source(), deadline=lambda: False, cancellation=lambda: False)
    assert error.value.code is PdfInspectionErrorCode.REPORT_INVALID
    assert worker.terminated


def test_import_preserves_unknown_signature_state_instead_of_claiming_absence() -> None:
    base = _opened_result()
    result = replace(
        base, features=replace(base.features, signature_markers=PdfFeatureState.UNKNOWN)
    )
    inspector, _worker, _pipe, _lease, _events = _stack(result=result)
    imported = inspector.inspect(
        _source(), deadline=lambda: False, cancellation=lambda: False, progress=lambda _item: None
    )
    assert imported.signature_state is SourceSignatureState.UNKNOWN


@pytest.mark.parametrize("failure_kind", ["raw", "source", "pdf", "worker", "timeout"])
def test_committed_errors_discard_private_exception_chains(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    inspector, _worker, _pipe, _lease, events = _stack()
    inspector._committed_sources = _CommittedStore(_CommittedLease(_snapshot(), events))
    private = "PRIVATE-PWS0501-EXCEPTION-CANARY"

    def fail(*_args: object, **_kwargs: object) -> None:
        try:
            raise OSError(private)
        except OSError as original:
            if failure_kind == "source":
                raise SourceIngestError(
                    SourceIngestErrorCode.SOURCE_CHANGED, "test.source"
                ) from original
            if failure_kind == "pdf":
                raise PdfInspectionError(
                    PdfInspectionErrorCode.REPORT_INVALID, "test.pdf"
                ) from original
            if failure_kind in {"worker", "timeout"}:
                raise WorkerClientError(
                    WorkerClientErrorCode.OPERATION_TIMED_OUT
                    if failure_kind == "timeout"
                    else WorkerClientErrorCode.LAUNCH_FAILED,
                    WorkerClientContext.OPERATION,
                ) from original
            raise

    monkeypatch.setattr(_CommittedStore, "borrow_pdf_inspection_source", fail)
    with pytest.raises((PdfInspectionError, SourceIngestError)) as caught:
        inspector.inspect_committed(
            _snapshot(), _uuid(3), deadline=lambda: False, cancellation=lambda: False
        )
    expected = {
        "raw": PdfInspectionErrorCode.INTERNAL_ERROR,
        "source": SourceIngestErrorCode.SOURCE_CHANGED,
        "pdf": PdfInspectionErrorCode.REPORT_INVALID,
        "worker": PdfInspectionErrorCode.WORKER_FAILED,
        "timeout": PdfInspectionErrorCode.TIMED_OUT,
    }[failure_kind]
    assert isinstance(caught.value, PdfInspectionError | SourceIngestError)
    assert caught.value.code is expected
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert private not in str(caught.value) + repr(caught.value)
    assert "worker.start" not in events
