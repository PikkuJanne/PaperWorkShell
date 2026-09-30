"""Saved-source inspection borrows exact immutable authorities and cleans up on failure."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Never, cast
from uuid import UUID

import pytest

from paperworkshell.domain.case_lifecycle import CaseLifecycleError, CaseLifecycleErrorCode
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.storage import source_ingest as source_storage
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    canonical_manifest_bytes,
)
from tests.integration.test_source_ingest_windows import CASE_ID, _import_pdf, _stack

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated source handles")


def test_saved_pdf_inspection_read_revalidates_without_mutation(tmp_path: Path) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    snapshot = WindowsManifestRepository(setup.manager).load(CASE_ID).snapshot
    manifest_before = setup.layout.manifest.read_bytes()
    original = next(setup.layout.originals.iterdir())
    source_before = original.read_bytes()
    with setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    ) as lease:
        assert lease.snapshot == snapshot and lease.source_id == imported.source_id
        assert lease.byte_size == len(source_before)
        assert lease.handle_value > 0
        lease.revalidate(deadline=lambda: False, cancellation=lambda: False)
        assert "synthetic.pdf" not in repr(lease)
    with pytest.raises(SourceIngestError):
        _ = lease.handle_value
    assert original.read_bytes() == source_before
    assert setup.layout.manifest.read_bytes() == manifest_before
    assert list(setup.layout.temp.iterdir()) == []


@pytest.mark.parametrize("changed", ["revision", "digest", "cas", "missing", "source"])
def test_saved_pdf_inspection_rejects_stale_or_changed_authority_and_recovers(
    tmp_path: Path, changed: str
) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    repository = WindowsManifestRepository(setup.manager)
    snapshot = repository.load(CASE_ID).snapshot
    selected_id = imported.source_id
    current = snapshot
    original = next(setup.layout.originals.iterdir())
    original_bytes = original.read_bytes()
    if changed in {"revision", "digest", "cas"}:
        document = snapshot.document
        revision = snapshot.revision + (changed == "revision")
        document["revision"] = revision
        current = ManifestSnapshot(
            snapshot.case_id,
            snapshot.schema_version,
            revision,
            document,
            "c" * 64 if changed == "digest" else snapshot.sha256,
            "synthetic-stale-token" if changed == "cas" else snapshot.cas_token,
        )
    elif changed == "missing":
        selected_id = UUID("99000000-0000-4000-8000-000000000501")
    else:
        original.write_bytes(original_bytes.replace(b"inspection", b"corruption"))
    try:
        with pytest.raises(SourceIngestError) as error:
            setup.store.borrow_pdf_inspection_source(
                current, selected_id, deadline=lambda: False, cancellation=lambda: False
            )
        assert error.value.code is (
            SourceIngestErrorCode.MANIFEST_CONFLICT
            if changed in {"revision", "digest", "cas"}
            else SourceIngestErrorCode.SOURCE_CHANGED
            if changed == "source"
            else SourceIngestErrorCode.SOURCE_UNAVAILABLE
        )
    finally:
        if changed == "source":
            original.write_bytes(original_bytes)
    with setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    ) as lease:
        lease.revalidate(deadline=lambda: False, cancellation=lambda: False)
    assert list(setup.layout.temp.iterdir()) == []


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_saved_pdf_inspection_interrupts_hashing_and_releases_handles(
    tmp_path: Path, stop: str
) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    snapshot = WindowsManifestRepository(setup.manager).load(CASE_ID).snapshot
    calls = 0

    def interrupt() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 5

    with pytest.raises(SourceIngestError) as error:
        setup.store.borrow_pdf_inspection_source(
            snapshot,
            imported.source_id,
            deadline=interrupt if stop == "timeout" else lambda: False,
            cancellation=interrupt if stop == "cancel" else lambda: False,
        )
    assert error.value.code is (
        SourceIngestErrorCode.CANCELLED if stop == "cancel" else SourceIngestErrorCode.TIMED_OUT
    )
    with setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    ) as lease:
        lease.revalidate(deadline=lambda: False, cancellation=lambda: False)


def test_saved_pdf_inspection_refuses_non_ready_source(tmp_path: Path) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    repository = WindowsManifestRepository(setup.manager)
    snapshot = repository.load(CASE_ID).snapshot
    document = snapshot.document
    cast(list[dict[str, object]], document["sources"])[0]["import_state"] = "pending"
    setup.layout.manifest.write_bytes(canonical_manifest_bytes(document))
    with pytest.raises(SourceIngestError) as error:
        setup.store.borrow_pdf_inspection_source(
            snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
        )
    assert error.value.code is SourceIngestErrorCode.MANIFEST_INVALID


def _private_failure(kind: str) -> Never:
    try:
        raise OSError("PRIVATE-INSPECTION-STORAGE-PATH-AND-VALUE")
    except OSError as cause:
        if kind == "nested":
            raise SourceIngestError(
                SourceIngestErrorCode.SOURCE_CHANGED, "source.integrity"
            ) from cause
        if kind in {"manifest", "future_manifest"}:
            raise CaseLifecycleError(
                CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED
                if kind == "future_manifest"
                else CaseLifecycleErrorCode.MANIFEST_INVALID,
                "source.manifest",
            ) from cause
        raise


def _assert_private_failure_is_detached(error: SourceIngestError) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception(error))
    assert "PRIVATE-INSPECTION-STORAGE" not in rendered + repr(error) + str(error)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("raw", SourceIngestErrorCode.SOURCE_UNAVAILABLE),
        ("nested", SourceIngestErrorCode.SOURCE_CHANGED),
        ("manifest", SourceIngestErrorCode.MANIFEST_INVALID),
        ("future_manifest", SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED),
        ("probe", SourceIngestErrorCode.CANCELLATION_FAILED),
    ],
)
def test_inspection_acquisition_detaches_storage_error_chains_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, expected: SourceIngestErrorCode
) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    snapshot = WindowsManifestRepository(setup.manager).load(CASE_ID).snapshot
    manifest_before = setup.layout.manifest.read_bytes()

    def fail(*_args: object, **_kwargs: object) -> Never:
        _private_failure(kind)

    with monkeypatch.context() as patched:
        if kind in {"manifest", "future_manifest"}:
            patched.setattr(source_storage, "_read_retained_manifest_snapshot", fail)
        elif kind == "nested":
            patched.setattr(source_storage, "_hash_retained_pdf_source", fail)
        elif kind == "raw":
            patched.setattr(source_storage, "open_handle", fail)
        with pytest.raises(SourceIngestError) as caught:
            setup.store.borrow_pdf_inspection_source(
                snapshot,
                imported.source_id,
                deadline=lambda: False,
                cancellation=fail if kind == "probe" else lambda: False,
            )
    assert caught.value.code is expected
    _assert_private_failure_is_detached(caught.value)
    with setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    ) as lease:
        lease.revalidate(deadline=lambda: False, cancellation=lambda: False)
    assert setup.layout.manifest.read_bytes() == manifest_before
    assert list(setup.layout.temp.iterdir()) == []


@pytest.mark.parametrize("operation", ["handle", "revalidate", "close"])
def test_inspection_lease_detaches_nested_handle_and_cleanup_errors_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    setup = _stack(tmp_path)
    imported = _import_pdf(setup, tmp_path, name="synthetic.pdf", marker=b"inspection")
    snapshot = WindowsManifestRepository(setup.manager).load(CASE_ID).snapshot
    lease = setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    )

    def fail(*_args: object, **_kwargs: object) -> Never:
        _private_failure("raw")

    try:
        with monkeypatch.context() as patched:
            if operation == "handle":
                patched.setattr(WindowsHandle, "refresh_info", fail)
            elif operation == "revalidate":
                patched.setattr(WindowsHandle, "read_exact", fail)
            else:
                lease._stack.callback(fail)
            with pytest.raises(SourceIngestError) as caught:
                if operation == "handle":
                    _ = lease.handle_value
                elif operation == "revalidate":
                    lease.revalidate(deadline=lambda: False, cancellation=lambda: False)
                else:
                    lease.close()
        assert caught.value.code is (
            SourceIngestErrorCode.SOURCE_UNAVAILABLE
            if operation == "close"
            else SourceIngestErrorCode.SOURCE_CHANGED
        )
        _assert_private_failure_is_detached(caught.value)
    finally:
        lease.close()
    with setup.store.borrow_pdf_inspection_source(
        snapshot, imported.source_id, deadline=lambda: False, cancellation=lambda: False
    ) as recovered:
        recovered.revalidate(deadline=lambda: False, cancellation=lambda: False)
    assert list(setup.layout.temp.iterdir()) == []
