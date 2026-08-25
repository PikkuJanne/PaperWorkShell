"""Bounded correlated worker report for verified flattened-PDF export."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.overlays.exporting import (
    FLATTENED_PDF_EXPORT_REPORT_VERSION,
    FlattenedPdfExportCheck,
    FlattenedPdfExportCheckCode,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    reject_flattened_pdf_export,
)
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
    encode_pdf_export_worker_report,
)

FLATTENED_PDF_WORKER_REPORT_VERSION: Final = 1
# The broker reads this pipe only after its one-shot worker is terminated.
MAX_FLATTENED_PDF_WORKER_REPORT_BYTES: Final = 4_096
_ROOT_MEMBERS: Final = frozenset(
    {
        "worker_report_version",
        "report_version",
        "semantic_fingerprint",
        "operation_id",
        "output_object_id",
        "base_report",
        "checks",
    }
)
_CHECK_MEMBERS: Final = frozenset({"code", "checked_count"})


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportWorkerReport:
    """Bind one flattened verification report to its operation and output."""

    operation_id: UUID
    output_object_id: UUID
    report: FlattenedPdfExportVerificationReport = field(repr=False)
    worker_report_version: int = FLATTENED_PDF_WORKER_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.operation_id)
            or not _is_uuid4(self.output_object_id)
            or self.operation_id == self.output_object_id
            or type(self.report) is not FlattenedPdfExportVerificationReport
            or type(self.worker_report_version) is not int
            or self.worker_report_version != FLATTENED_PDF_WORKER_REPORT_VERSION
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.REPORT_INVALID,
                "flattened.worker_report",
            )


def encode_flattened_pdf_worker_report(value: FlattenedPdfExportWorkerReport) -> bytes:
    """Encode one canonical, pipe-bounded report without paths or content."""

    if type(value) is not FlattenedPdfExportWorkerReport:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_encode",
        )
    try:
        base_report = json.loads(
            encode_pdf_export_worker_report(
                PdfExportWorkerReport(
                    operation_id=value.operation_id,
                    output_object_id=value.output_object_id,
                    report=value.report.base_report,
                )
            ).decode("ascii")
        )
        document = {
            "worker_report_version": value.worker_report_version,
            "report_version": value.report.report_version,
            "semantic_fingerprint": value.report.plan.semantic_fingerprint,
            "operation_id": str(value.operation_id),
            "output_object_id": str(value.output_object_id),
            "base_report": base_report,
            "checks": [
                {"code": check.code.value, "checked_count": check.checked_count}
                for check in value.report.checks
            ],
        }
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_encode",
        )
    if not 1 <= len(payload) <= MAX_FLATTENED_PDF_WORKER_REPORT_BYTES:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.report_size",
        )
    return payload


def decode_flattened_pdf_worker_report(
    payload: bytes,
    *,
    plan: FlattenedPdfExportPlan,
) -> FlattenedPdfExportWorkerReport:
    """Decode one exact report and bind it to the already-decoded plan."""

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_FLATTENED_PDF_WORKER_REPORT_BYTES
        or type(plan) is not FlattenedPdfExportPlan
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_size",
        )
    try:
        root = _object(
            json.loads(
                payload.decode("ascii"),
                object_pairs_hook=_unique_object,
                parse_int=_bounded_integer,
                parse_float=_reject_number,
                parse_constant=_reject_number,
            ),
            _ROOT_MEMBERS,
        )
        version = _integer(root["worker_report_version"])
        if version != FLATTENED_PDF_WORKER_REPORT_VERSION:
            raise ValueError
        report_version = _integer(root["report_version"])
        if (
            report_version != FLATTENED_PDF_EXPORT_REPORT_VERSION
            or _string(root["semantic_fingerprint"]) != plan.semantic_fingerprint
        ):
            raise ValueError
        operation_id = _uuid(root["operation_id"])
        output_object_id = _uuid(root["output_object_id"])
        base_payload = json.dumps(
            _object_any(root["base_report"]),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        base_report = decode_pdf_export_worker_report(base_payload)
        if (
            base_report.operation_id != operation_id
            or base_report.output_object_id != output_object_id
        ):
            raise ValueError
        raw_checks = root["checks"]
        if type(raw_checks) is not list or len(raw_checks) > len(FlattenedPdfExportCheckCode):
            raise ValueError
        checks = tuple(
            FlattenedPdfExportCheck(
                code=FlattenedPdfExportCheckCode(_string(check["code"])),
                checked_count=_integer(check["checked_count"]),
            )
            for member in cast(list[object], raw_checks)
            for check in (_object(member, _CHECK_MEMBERS),)
        )
        report = FlattenedPdfExportVerificationReport(
            plan=plan,
            base_report=base_report.report,
            checks=checks,
            report_version=report_version,
        )
        decoded = FlattenedPdfExportWorkerReport(
            operation_id=operation_id,
            output_object_id=output_object_id,
            report=report,
            worker_report_version=version,
        )
        if encode_flattened_pdf_worker_report(decoded) != payload:
            raise ValueError
        return decoded
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.worker_report",
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_SAFE_MANIFEST_INTEGER <= parsed <= MAX_SAFE_MANIFEST_INTEGER:
        raise ValueError
    return parsed


def _reject_number(_: str) -> Never:
    raise ValueError


def _object(value: object, members: frozenset[str]) -> dict[str, object]:
    result = _object_any(value)
    if set(result) != members:
        raise ValueError
    return result


def _object_any(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError
    return cast(dict[str, object], value)


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        raise ValueError
    return value


def _string(value: object) -> str:
    if type(value) is not str or len(value) > 4_096:
        raise ValueError
    return value


def _uuid(value: object) -> UUID:
    text = _string(value)
    parsed = UUID(text)
    if parsed.version != 4 or str(parsed) != text:
        raise ValueError
    return parsed


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4
