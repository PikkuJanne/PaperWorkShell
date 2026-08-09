"""Canonical JSON encoding for the closed sanitized diagnostic record."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Final

from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.models import DiagnosticRecord, copy_diagnostic_record

MAX_DIAGNOSTIC_RECORD_BYTES: Final = 16_384


def _timestamp_text(value: datetime) -> str:
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


def encode_diagnostic_record(value: DiagnosticRecord) -> bytes:
    """Validate and canonically encode one content-free diagnostic record."""

    record = copy_diagnostic_record(value)
    document: dict[str, object] = {
        "case_id": None if record.case_id is None else str(record.case_id),
        "counts": [{"kind": item.kind.value, "value": item.value} for item in record.counts],
        "event": record.event.value,
        "failure_code": (None if record.failure_code is None else record.failure_code.value),
        "occurred_at": _timestamp_text(record.occurred_at),
        "operation_id": (None if record.operation_id is None else str(record.operation_id)),
        "operation_kind": (None if record.operation_kind is None else record.operation_kind.value),
        "outcome": None if record.outcome is None else record.outcome.value,
        "record_version": record.record_version,
        "resources": [{"kind": item.kind.value, "value": item.value} for item in record.resources],
        "versions": [
            {"components": list(item.components), "kind": item.kind.value}
            for item in record.versions
        ],
    }
    encoded: bytes | None = None
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except TypeError, ValueError, UnicodeError:
        pass
    if encoded is None:
        reject_diagnostic(
            DiagnosticErrorCode.ENCODING_FAILED,
            DiagnosticErrorContext.ENCODING,
        )
    if len(encoded) > MAX_DIAGNOSTIC_RECORD_BYTES:
        reject_diagnostic(
            DiagnosticErrorCode.ENCODING_FAILED,
            DiagnosticErrorContext.ENCODING,
        )
    return encoded
