"""Security and privacy evidence for PWS-0112 export-history records."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportCheckCode
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    decode_pdf_export_history,
)
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import (
    PdfExportHistoryStorageDependencies,
    WindowsPdfExportHistoryRepository,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0112 history security targets the supported Windows boundary"
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000812")
EXPORT_ID = UUID("50000000-0000-4000-8000-000000000812")
CHECKS = (
    PdfExportCheckCode.PDF_REOPENED,
    PdfExportCheckCode.STRUCTURE_CLEAN,
    PdfExportCheckCode.PAGE_COUNT_MATCHED,
    PdfExportCheckCode.UNENCRYPTED,
    PdfExportCheckCode.ALL_PAGES_RENDERED,
    PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,
)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=8_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=256,
        maximum_depth=16,
    )


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID)


def _record() -> PdfExportHistoryRecord:
    return PdfExportHistoryRecord(
        export_id=EXPORT_ID,
        recorded_at="2026-08-06T12:00:00.000000Z",
        manifest_revision=4,
        manifest_sha256="a" * 64,
        semantic_fingerprint="b" * 64,
        output_sha256="c" * 64,
        output_byte_size=321,
        page_count=1,
        check_codes=CHECKS,
        unsigned_derivative=False,
    )


def test_path_filename_and_destination_fields_are_rejected_without_disclosure() -> None:
    canary = "Private Applicant C:/cases/secret/final-name.pdf"
    payload = (
        b'{"records":[{"check_codes":["PDF_REOPENED","STRUCTURE_CLEAN",'
        b'"PAGE_COUNT_MATCHED","UNENCRYPTED","ALL_PAGES_RENDERED",'
        b'"ORDER_ROTATION_MATCHED","OUTPUT_COMMIT_REOPENED"],'
        b'"destination_path":"Private Applicant C:/cases/secret/final-name.pdf",'
        b'"export_id":"50000000-0000-4000-8000-000000000812",'
        b'"manifest_revision":4,"manifest_sha256":"'
        + b"a" * 64
        + b'","output_byte_size":321,"output_sha256":"'
        + b"c" * 64
        + b'","page_count":1,"record_version":1,'
        b'"recorded_at":"2026-08-06T12:00:00.000000Z",'
        b'"semantic_fingerprint":"'
        + b"b" * 64
        + b'","unsigned_derivative":false}],"schema_version":1}\n'
    )

    with pytest.raises(PdfExportHistoryError) as raised:
        decode_pdf_export_history(payload)

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert canary not in rendered
    assert "final-name.pdf" not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_history_hard_link_is_rejected_without_changing_either_name(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsPdfExportHistoryRepository(manager)
    repository.append(CASE_ID, _record())
    before = layout.export_history.read_bytes()
    alias = layout.case_root / "history-alias.bin"
    os.link(layout.export_history, alias)

    with pytest.raises(PdfExportHistoryError) as raised:
        repository.load(CASE_ID)

    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert layout.export_history.read_bytes() == before
    assert alias.read_bytes() == before


def test_history_alternate_stream_is_rejected_and_stream_value_is_not_disclosed(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsPdfExportHistoryRepository(manager)
    repository.append(CASE_ID, _record())
    canary = b"Private alternate-stream export filename.pdf"
    stream = Path(f"{layout.export_history}:private")
    stream.write_bytes(canary)

    with pytest.raises(PdfExportHistoryError) as raised:
        repository.load(CASE_ID)

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert canary.decode() not in rendered
    assert stream.read_bytes() == canary


def test_directory_at_reserved_history_name_is_rejected_without_traversal(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    layout.export_history.mkdir()
    sentinel = layout.export_history / "private-filename.pdf"
    sentinel.write_bytes(b"outside-history-sentinel")

    with pytest.raises(PdfExportHistoryError) as raised:
        WindowsPdfExportHistoryRepository(manager).load(CASE_ID)

    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert sentinel.read_bytes() == b"outside-history-sentinel"


def test_history_symlink_is_rejected_without_reading_or_mutating_target(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    outside = tmp_path / "outside-private-filename.pdf"
    outside.write_bytes(b"outside-history-target")
    try:
        layout.export_history.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file-symlink capability unavailable: {error.winerror}")

    with pytest.raises(PdfExportHistoryError) as raised:
        WindowsPdfExportHistoryRepository(manager).load(CASE_ID)

    assert raised.value.code is PdfExportHistoryErrorCode.DOCUMENT_INVALID
    assert outside.read_bytes() == b"outside-history-target"


def test_raw_stage_failure_text_and_paths_do_not_escape_public_error(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    canary = f"Private Applicant {layout.case_root}/final-name.pdf"

    def fail_with_private_text(_path: Path, _payload: bytes) -> WindowsHandle:
        raise RuntimeError(canary)

    repository = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(write_stage=fail_with_private_text),
    )
    with pytest.raises(PdfExportHistoryError) as raised:
        repository.append(CASE_ID, _record())

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is PdfExportHistoryErrorCode.STORAGE_FAILED
    assert canary not in rendered
    assert "final-name.pdf" not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert not layout.export_history.exists()
