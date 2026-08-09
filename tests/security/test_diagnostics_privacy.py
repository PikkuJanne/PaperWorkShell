"""Prohibited-token and capability tests for routine diagnostics."""

from __future__ import annotations

import ast
import json
import traceback
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Never, cast
from uuid import UUID

import pytest

from paperworkshell.diagnostics.codec import encode_diagnostic_record
from paperworkshell.diagnostics.errors import (
    DiagnosticError,
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticFailureCode,
    DiagnosticOutcome,
    DiagnosticRecord,
    DiagnosticResource,
    DiagnosticResourceKind,
    DiagnosticVersion,
    DiagnosticVersionKind,
)
from paperworkshell.diagnostics.summary import (
    DiagnosticSummary,
    build_diagnostic_summary,
    render_diagnostic_summary,
)

_NOW = datetime(2026, 8, 9, 12, 34, 56, tzinfo=UTC)
_OPERATION_ID = UUID("00000000-0000-4000-8000-000000000001")
_TEXT_CANARY = "OCR-DOCUMENT-TEXT-CANARY-7F21"
_FORM_CANARY = "FORM-VALUE-CANARY-62C4"
_NAME_CANARY = "APPLICANT-NAME-CANARY-51B8"
_ADDRESS_CANARY = "ADDRESS-CANARY-42 Example Street"
_FILENAME_CANARY = "original-applicant-CANARY-39A1.pdf"
_WINDOWS_PATH_CANARY = "C:\\private\\CANARY-2E04\\applicant.pdf"
_UNC_PATH_CANARY = "\\\\private-server\\CANARY-1D73\\applicant.pdf"
_POSIX_PATH_CANARY = "/private/CANARY-0C62/applicant.pdf"
_METADATA_CANARY = "METADATA-AUTHOR-CANARY-FB50"
_THUMBNAIL_CANARY = b"PNG-THUMBNAIL-CANARY-EA49"
_SIGNATURE_CANARY = b"SIGNATURE-IMAGE-CANARY-D938"
_RAW_ERROR_CANARY = "RAW-ERROR-CANARY-C827:C:\\private\\applicant.pdf"
_CANARIES: tuple[str | bytes, ...] = (
    _TEXT_CANARY,
    _FORM_CANARY,
    _NAME_CANARY,
    _ADDRESS_CANARY,
    _FILENAME_CANARY,
    _WINDOWS_PATH_CANARY,
    _UNC_PATH_CANARY,
    _POSIX_PATH_CANARY,
    _METADATA_CANARY,
    _THUMBNAIL_CANARY,
    _SIGNATURE_CANARY,
)


def _render_error(error: DiagnosticError) -> str:
    return "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            "".join(traceback.format_exception(error)),
        )
    )


def _canary_text(value: str | bytes) -> str:
    return value if isinstance(value, str) else value.decode("ascii")


def _safe_record() -> DiagnosticRecord:
    return DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
        outcome=DiagnosticOutcome.PARTIAL,
        failure_code=DiagnosticFailureCode.CASE_INVALID,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (0, 0, 0)),),
        counts=(DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, 3),),
        resources=(DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 20),),
    )


def test_every_public_typed_seam_rejects_prohibited_values_without_echoing_them(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rendered_failures: list[str] = []
    for canary in _CANARIES:
        with pytest.raises(DiagnosticError) as record_failure:
            DiagnosticRecord(
                occurred_at=_NOW,
                event=cast(DiagnosticEventCode, canary),
            )
        rendered_failures.append(_render_error(record_failure.value))

        with pytest.raises(DiagnosticError) as version_failure:
            DiagnosticVersion(
                DiagnosticVersionKind.APPLICATION,
                cast(tuple[int, ...], (canary,)),
            )
        rendered_failures.append(_render_error(version_failure.value))

        with pytest.raises(DiagnosticError) as count_failure:
            DiagnosticCount(
                DiagnosticCountKind.CASES_SCANNED,
                cast(int, canary),
            )
        rendered_failures.append(_render_error(count_failure.value))

        with pytest.raises(DiagnosticError) as resource_failure:
            DiagnosticResource(
                DiagnosticResourceKind.DURATION_MILLISECONDS,
                cast(int, canary),
            )
        rendered_failures.append(_render_error(resource_failure.value))

        with pytest.raises(DiagnosticError) as summary_failure:
            DiagnosticSummary(
                generated_at=_NOW,
                codes=cast(tuple[DiagnosticFailureCode, ...], (canary,)),
            )
        rendered_failures.append(_render_error(summary_failure.value))

        with pytest.raises(DiagnosticError) as builder_failure:
            build_diagnostic_summary(
                (_safe_record(),),
                generated_at=cast(datetime, canary),
            )
        rendered_failures.append(_render_error(builder_failure.value))

        with pytest.raises(DiagnosticError) as configuration_failure:
            SanitizedDiagnosticLogger(sink=cast(BinaryIO, canary))
        rendered_failures.append(_render_error(configuration_failure.value))

        logger = SanitizedDiagnosticLogger()
        logger.emit(_safe_record())
        with pytest.raises(DiagnosticError) as snapshot_failure:
            logger.snapshot_summary(generated_at=cast(datetime, canary))
        rendered_failures.append(_render_error(snapshot_failure.value))

    routine_output = "\n".join(rendered_failures)
    captured = capsys.readouterr()
    routine_output += f"\n{caplog.text}\n{captured.out}\n{captured.err}"
    for canary in _CANARIES:
        assert _canary_text(canary) not in routine_output


class _RawFailureSink(BytesIO):
    def flush(self) -> Never:
        raise OSError(_RAW_ERROR_CANARY)


def _reject_while_handling_raw_error() -> Never:
    try:
        raise OSError(_RAW_ERROR_CANARY)
    except OSError:
        reject_diagnostic(
            DiagnosticErrorCode.INTERNAL_ERROR,
            DiagnosticErrorContext.INTERNAL,
        )


def test_raw_sink_exception_is_not_retained_logged_or_printed(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = SanitizedDiagnosticLogger(sink=_RawFailureSink())
    with pytest.raises(DiagnosticError) as raised:
        logger.emit(_safe_record())

    captured = capsys.readouterr()
    rendered = (
        f"{_render_error(raised.value)}\n{repr(logger)}\n{caplog.text}\n"
        f"{captured.out}\n{captured.err}"
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _RAW_ERROR_CANARY not in rendered
    assert "applicant.pdf" not in rendered


def test_public_rejection_helper_clears_an_active_raw_exception() -> None:
    with pytest.raises(DiagnosticError) as raised:
        _reject_while_handling_raw_error()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _RAW_ERROR_CANARY not in _render_error(raised.value)


def test_encoded_record_and_human_summary_use_only_the_safe_inventory() -> None:
    record = _safe_record()
    encoded = encode_diagnostic_record(record)
    summary = DiagnosticSummary(
        generated_at=_NOW,
        record_count=1,
        versions=record.versions,
        operation_ids=(_OPERATION_ID,),
        counts=record.counts,
        codes=(DiagnosticFailureCode.CASE_INVALID,),
        resources=record.resources,
    )
    rendered = render_diagnostic_summary(summary)
    combined = encoded.decode("ascii") + "\n" + rendered + "\n" + repr(record) + repr(summary)
    for canary in _CANARIES:
        assert _canary_text(canary) not in combined

    document = json.loads(encoded)
    assert {
        "message",
        "path",
        "filename",
        "text",
        "metadata",
        "thumbnail",
        "signature",
        "hash",
        "digest",
        "content",
        "exception",
    }.isdisjoint(document)
    nested_keys = {
        key
        for category in ("versions", "counts", "resources")
        for entry in document[category]
        for key in entry
    }
    assert nested_keys <= {"components", "kind", "value"}


def test_diagnostics_modules_have_no_path_network_process_or_generic_logging_surface() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "paperworkshell" / "diagnostics"
    prohibited_imports = {"logging", "os", "pathlib", "socket", "subprocess", "traceback"}
    prohibited_calls = {"breakpoint", "open", "print"}
    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path.name)
        imported_roots: set[str] = set()
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
        assert imported_roots.isdisjoint(prohibited_imports)
        assert called_names.isdisjoint(prohibited_calls)
