"""Bounded correlated result envelope for verified PDF-export worker output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_ENGINE_PROFILE as DEFAULT_PDF_EXPORT_ENGINE_PROFILE,
)
from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_CHECKS,
    OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
    OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION,
    PDF_EXPORT_REPORT_VERSION,
    PORTAL_PDF_EXPORT_REPORT_VERSION,
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_export import (
    PDFIUM_EXPORT_ENGINE_VERSION as PDFIUM_EXPORT_ENGINE_VERSION,
)
from paperworkshell.domain.pdf_export import (
    PIKEPDF_EXPORT_ENGINE_VERSION as PIKEPDF_EXPORT_ENGINE_VERSION,
)
from paperworkshell.domain.pdf_export import (
    PYPDFIUM2_EXPORT_ENGINE_VERSION as PYPDFIUM2_EXPORT_ENGINE_VERSION,
)
from paperworkshell.domain.pdf_export import (
    QPDF_EXPORT_ENGINE_VERSION as QPDF_EXPORT_ENGINE_VERSION,
)
from paperworkshell.domain.pdf_export import (
    PdfExportEngineProfile as PdfExportEngineProfile,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)

PDF_EXPORT_WORKER_REPORT_VERSION: Final = 1
# The broker intentionally does not read the result pipe until the one-shot
# worker tree is confirmed terminated. Keep the complete closed report below
# the pipe's 8 KiB kernel buffer so a worker can never block on its own result.
MAX_PDF_EXPORT_WORKER_REPORT_BYTES: Final = 4_096

_ROOT_MEMBERS: Final = frozenset(
    {
        "worker_report_version",
        "operation_id",
        "output_object_id",
        "engine_profile",
        "report",
    }
)
_ENGINE_MEMBERS: Final = frozenset({"pikepdf", "qpdf", "pypdfium2", "pdfium"})
_REPORT_MEMBERS: Final = frozenset(
    {
        "report_version",
        "semantic_fingerprint",
        "output_sha256",
        "output_byte_size",
        "page_count",
        "checks",
        "unsigned_derivative",
    }
)
_CHECK_MEMBERS: Final = frozenset({"code", "page_count"})
_OPTIMIZATION_MEMBERS: Final = frozenset(
    {
        "report_version",
        "profile",
        "outcome",
        "baseline_byte_size",
        "candidate_byte_size",
        "selected_byte_size",
        "verified_page_count",
    }
)


@dataclass(frozen=True, slots=True)
class PdfExportWorkerReport:
    """Worker report bound to one operation and one staging-output identity."""

    operation_id: UUID
    output_object_id: UUID
    report: PdfExportVerificationReport
    engine_profile: PdfExportEngineProfile = DEFAULT_PDF_EXPORT_ENGINE_PROFILE
    worker_report_version: int = PDF_EXPORT_WORKER_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.operation_id)
            or not _is_uuid4(self.output_object_id)
            or self.operation_id == self.output_object_id
            or type(self.report) is not PdfExportVerificationReport
            or type(self.engine_profile) is not PdfExportEngineProfile
            or type(self.worker_report_version) is not int
            or self.worker_report_version != PDF_EXPORT_WORKER_REPORT_VERSION
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.worker_report")


def encode_pdf_export_worker_report(value: PdfExportWorkerReport) -> bytes:
    """Encode one canonical bounded worker report without paths or filenames."""

    if type(value) is not PdfExportWorkerReport:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_encode")
    report = value.report
    engine = value.engine_profile
    report_document: dict[str, object] = {
        "report_version": report.report_version,
        "semantic_fingerprint": report.semantic_fingerprint,
        "output_sha256": report.output_sha256,
        "output_byte_size": report.output_byte_size,
        "page_count": report.page_count,
        "checks": [
            {"code": check.code.value, "page_count": check.page_count} for check in report.checks
        ],
        "unsigned_derivative": report.unsigned_derivative,
    }
    if report.optimization is not None:
        optimization = report.optimization
        report_document["optimization"] = {
            "report_version": optimization.report_version,
            "profile": optimization.profile.value,
            "outcome": optimization.outcome.value,
            "baseline_byte_size": optimization.baseline_byte_size,
            "candidate_byte_size": optimization.candidate_byte_size,
            "selected_byte_size": optimization.selected_byte_size,
            "verified_page_count": optimization.verified_page_count,
        }
    document = {
        "worker_report_version": value.worker_report_version,
        "operation_id": str(value.operation_id),
        "output_object_id": str(value.output_object_id),
        "engine_profile": {
            "pikepdf": engine.pikepdf,
            "qpdf": engine.qpdf,
            "pypdfium2": engine.pypdfium2,
            "pdfium": engine.pdfium,
        },
        "report": report_document,
    }
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_encode")
    if not 1 <= len(payload) <= MAX_PDF_EXPORT_WORKER_REPORT_BYTES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.report_size")
    return payload


def decode_pdf_export_worker_report(payload: bytes) -> PdfExportWorkerReport:
    """Decode one exact report and re-run every typed report invariant."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_WORKER_REPORT_BYTES:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_size")
    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_json")
    root = _object(document, _ROOT_MEMBERS, "export.worker_report")
    version = _integer(root["worker_report_version"], "export.report_version")
    if version != PDF_EXPORT_WORKER_REPORT_VERSION:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_version")
    engine_record = _object(root["engine_profile"], _ENGINE_MEMBERS, "export.report_engines")
    engine = PdfExportEngineProfile(
        pikepdf=_string(engine_record["pikepdf"], "export.engine_pikepdf"),
        qpdf=_string(engine_record["qpdf"], "export.engine_qpdf"),
        pypdfium2=_string(engine_record["pypdfium2"], "export.engine_pypdfium2"),
        pdfium=_string(engine_record["pdfium"], "export.engine_pdfium"),
    )
    raw_report = root["report"]
    if type(raw_report) is not dict:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report")
    report_version = _integer(raw_report.get("report_version"), "export.domain_report_version")
    if report_version in {PDF_EXPORT_REPORT_VERSION, PORTAL_PDF_EXPORT_REPORT_VERSION}:
        report_members = _REPORT_MEMBERS
    elif report_version in {
        OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
        OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION,
    }:
        report_members = _REPORT_MEMBERS | {"optimization"}
    else:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.domain_report_version")
    report_record = _object(raw_report, report_members, "export.report")
    optimization = (
        _optimization_report(report_record["optimization"])
        if report_version
        in {
            OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
            OPTIMIZED_PORTAL_PDF_EXPORT_REPORT_VERSION,
        }
        else None
    )
    raw_checks = report_record["checks"]
    if type(raw_checks) is not list or not 1 <= len(raw_checks) <= MAX_PDF_EXPORT_CHECKS:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_checks")
    checks: list[PdfExportCheck] = []
    for member in raw_checks:
        check = _object(member, _CHECK_MEMBERS, "export.report_check")
        try:
            code = PdfExportCheckCode(_string(check["code"], "export.report_check_code"))
        except ValueError:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_check_code")
        page_count = check["page_count"]
        if page_count is not None:
            page_count = _integer(page_count, "export.report_check_count")
        checks.append(PdfExportCheck(code, page_count))
    report = PdfExportVerificationReport(
        semantic_fingerprint=_string(
            report_record["semantic_fingerprint"], "export.report_fingerprint"
        ),
        output_sha256=_string(report_record["output_sha256"], "export.report_digest"),
        output_byte_size=_integer(report_record["output_byte_size"], "export.report_size"),
        page_count=_integer(report_record["page_count"], "export.report_pages"),
        checks=tuple(checks),
        unsigned_derivative=_boolean(
            report_record["unsigned_derivative"], "export.report_signature"
        ),
        report_version=report_version,
        optimization=optimization,
    )
    return PdfExportWorkerReport(
        operation_id=_uuid(root["operation_id"], "export.report_operation"),
        output_object_id=_uuid(root["output_object_id"], "export.report_output"),
        report=report,
        engine_profile=engine,
        worker_report_version=version,
    )


def _optimization_report(value: object) -> PdfOptimizationReport:
    record = _object(value, _OPTIMIZATION_MEMBERS, "export.report_optimization")
    candidate_size = record["candidate_byte_size"]
    if candidate_size is not None:
        candidate_size = _integer(candidate_size, "export.optimization_candidate_size")
    try:
        return PdfOptimizationReport(
            profile=PdfOptimizationProfile(
                _string(record["profile"], "export.optimization_profile")
            ),
            outcome=PdfOptimizationOutcome(
                _string(record["outcome"], "export.optimization_outcome")
            ),
            baseline_byte_size=_integer(
                record["baseline_byte_size"], "export.optimization_baseline_size"
            ),
            candidate_byte_size=candidate_size,
            selected_byte_size=_integer(
                record["selected_byte_size"], "export.optimization_selected_size"
            ),
            verified_page_count=_integer(
                record["verified_page_count"], "export.optimization_pages"
            ),
            report_version=_integer(record["report_version"], "export.optimization_version"),
        )
    except PdfExportError:
        raise
    except ValueError:
        pass
    # Enum exceptions may retain hostile strings. Reject outside their scope.
    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_optimization")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_duplicate")
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_SAFE_MANIFEST_INTEGER <= parsed <= MAX_SAFE_MANIFEST_INTEGER:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_integer")
    return parsed


def _reject_number(_value: str) -> None:
    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_number")


def _object(value: object, members: frozenset[str], context: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != members:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    return cast(dict[str, object], value)


def _integer(value: object, context: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    return value


def _string(value: object, context: str) -> str:
    if type(value) is not str or len(value) > 4_096:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    return value


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    return value


def _uuid(value: object, context: str) -> UUID:
    text = _string(value, context)
    try:
        parsed = UUID(text)
    except ValueError, AttributeError:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    if parsed.version != 4 or str(parsed) != text:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, context)
    return parsed


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4
