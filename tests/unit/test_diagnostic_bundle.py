"""Frozen support inventory, consent, type validation and public sample binding."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID
from zipfile import ZIP_STORED, ZipFile

import pytest

from paperworkshell.app.diagnostic_bundle import DiagnosticBundleService
from paperworkshell.diagnostics.bundle import (
    ALLOWLIST_TEXT,
    BUNDLE_FILENAMES,
    MAX_BUNDLE_BYTES,
    DiagnosticBundlePreview,
)
from paperworkshell.diagnostics.errors import DiagnosticError, DiagnosticErrorCode
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    MAX_DIAGNOSTIC_RECORDS,
    DiagnosticEventCode,
    DiagnosticRecord,
)
from paperworkshell.diagnostics.summary import DiagnosticSummary
from tests.helpers.diagnostic_bundle import SAMPLE_TIME, sample_preview


class Writer:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(
        self, preview: DiagnosticBundlePreview, destination: Path, *, cancellation: object
    ) -> None:
        self.writes.append(preview.archive)


def test_complete_deterministic_preview_matches_exact_zip_members() -> None:
    preview = sample_preview()
    assert preview.archive == sample_preview().archive
    assert len(preview.archive) < MAX_BUNDLE_BYTES
    with ZipFile(BytesIO(preview.archive)) as archive:
        assert tuple(archive.namelist()) == BUNDLE_FILENAMES
        assert archive.testzip() is None
        assert all(item.compress_type == ZIP_STORED for item in archive.infolist())
        for member in preview.files:
            assert archive.read(member.name) == member.content
            assert member.content.decode() in preview.render()
            assert member.sha256 in preview.render()
            assert str(len(member.content)) in preview.render()
    assert preview.sha256 in preview.render()
    assert ALLOWLIST_TEXT in preview.render()


def test_maximum_established_summary_identifier_count_fits_closed_bundle_bound() -> None:
    preview = DiagnosticBundlePreview(
        DiagnosticSummary(
            generated_at=SAMPLE_TIME,
            operation_ids=tuple(
                UUID(f"10000000-0000-4000-8000-{item:012x}")
                for item in range(MAX_DIAGNOSTIC_RECORDS)
            ),
        )
    )
    preview.validate()
    assert len(preview.archive) < MAX_BUNDLE_BYTES


def test_consent_and_precommit_cancel_do_not_call_writer() -> None:
    writer = Writer()
    service = DiagnosticBundleService(SanitizedDiagnosticLogger(), writer)
    for consent in (False, cast(bool, 1), cast(bool, "yes")):
        with pytest.raises(DiagnosticError) as error:
            service.save(
                sample_preview(), Path("unused.zip"), consent=consent, cancellation=lambda: False
            )
        assert error.value.code is DiagnosticErrorCode.CONSENT_REQUIRED
    with pytest.raises(DiagnosticError) as error:
        service.save(sample_preview(), Path("unused.zip"), consent=True, cancellation=lambda: True)
    assert error.value.code is DiagnosticErrorCode.CANCELLED
    assert writer.writes == []


def test_saving_frozen_preview_does_not_recollect_logger() -> None:
    logger = SanitizedDiagnosticLogger()
    writer = Writer()
    service = DiagnosticBundleService(logger, writer)
    preview = service.preview(generated_at=SAMPLE_TIME)
    logger.emit(
        DiagnosticRecord(occurred_at=SAMPLE_TIME, event=DiagnosticEventCode.APPLICATION_STARTED)
    )
    service.save(preview, Path("unused.zip"), consent=True, cancellation=lambda: False)
    assert writer.writes == [preview.archive]
    assert b"Record count: 0" in preview.archive
    assert service.preview(generated_at=SAMPLE_TIME).archive != preview.archive


def test_mutated_bytes_or_members_fail_before_writer() -> None:
    writer = Writer()
    service = DiagnosticBundleService(SanitizedDiagnosticLogger(), writer)
    for field, value in (("archive", b"private document"), ("files", ())):
        preview = sample_preview()
        object.__setattr__(preview, field, value)
        with pytest.raises(DiagnosticError):
            service.save(preview, Path("unused.zip"), consent=True, cancellation=lambda: False)
    assert writer.writes == []


def test_published_sample_reopens_exact_allowlist_and_hash() -> None:
    root = Path(__file__).resolve().parents[1] / "fixtures/diagnostics/v1"
    index = json.loads((root / "index.json").read_text())
    assert index["synthetic_only"] is True
    assert index["task_id"] == "PWS-0805"
    sample = (root / "support-sample.zip").read_bytes()
    assert sample == sample_preview().archive
    assert hashlib.sha256(sample).hexdigest() == index["sha256"]
    assert len(sample) == index["size_bytes"]
    assert (root / "allowlist.txt").read_bytes() == ALLOWLIST_TEXT.encode("utf-8")
    with ZipFile(BytesIO(sample)) as archive:
        assert archive.namelist() == list(BUNDLE_FILENAMES)
        for item in index["members"]:
            payload = archive.read(item["name"])
            assert len(payload) == item["size_bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
