"""No private content path exists through support preview, errors or publication."""

from __future__ import annotations

import traceback
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from paperworkshell.app.diagnostic_bundle import DiagnosticBundleService
from paperworkshell.diagnostics.bundle import DiagnosticBundlePreview
from paperworkshell.diagnostics.errors import DiagnosticError
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.summary import DiagnosticSummary
from tests.helpers.diagnostic_bundle import sample_preview

CANARIES = (
    "OCR-CONTENT-CANARY-0805",
    "FORM-VALUE-CANARY-0805",
    "PERSON-NAME-CANARY-0805",
    "ADDRESS-CANARY-0805",
    "original-filename-canary-0805.pdf",
    "C:\\private\\canary0805",
    "/private/canary0805",
    "METADATA-CANARY-0805",
    "THUMBNAIL-CANARY-0805",
    "SIGNATURE-CANARY-0805",
    "DOCUMENT-BYTES-CANARY-0805",
)


def test_every_prohibited_token_is_absent_from_preview_and_all_archive_members(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    preview = sample_preview()
    with ZipFile(BytesIO(preview.archive)) as archive:
        output = (
            preview.archive
            + preview.render().encode()
            + b"".join(archive.read(name) for name in archive.namelist())
        )
    captured = capsys.readouterr()
    output += (caplog.text + captured.out + captured.err).encode()
    assert all(token.encode() not in output for token in CANARIES)
    assert not any("/" in member.name or "\\" in member.name for member in preview.files)


@pytest.mark.parametrize("token", CANARIES)
def test_forged_summary_never_emits_rejected_values(token: str) -> None:
    summary = sample_preview().summary
    object.__setattr__(summary, "codes", (token,))
    with pytest.raises(DiagnosticError) as error:
        DiagnosticBundlePreview(summary)
    assert token not in str(error.value)
    assert token not in repr(error.value)
    assert error.value.__context__ is None


def test_raw_writer_failure_is_sanitized_without_exception_context() -> None:
    token = CANARIES[5]

    class FailedWriter:
        def write(self, preview: object, destination: object, *, cancellation: object) -> None:
            raise PermissionError(token)

    service = DiagnosticBundleService(SanitizedDiagnosticLogger(), FailedWriter())
    with pytest.raises(DiagnosticError) as error:
        service.save(sample_preview(), Path("unused.zip"), consent=True, cancellation=lambda: False)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert token not in "".join(traceback.format_exception(error.value))


def test_collection_failure_has_no_file_reads_writes_or_content_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = CANARIES[5]
    logger = SanitizedDiagnosticLogger()

    def fail(_self: SanitizedDiagnosticLogger, **_kwargs: object) -> DiagnosticSummary:
        raise OSError(token)

    monkeypatch.setattr(SanitizedDiagnosticLogger, "snapshot_summary", fail)
    service = DiagnosticBundleService(logger, FailedCollectionWriter())
    with pytest.raises(DiagnosticError) as error:
        service.preview()
    assert token not in "".join(traceback.format_exception(error.value))
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert logger.snapshot_records() == ()


class FailedCollectionWriter:
    def write(self, preview: object, destination: object, *, cancellation: object) -> None:
        raise AssertionError("collection must not access the destination writer")
