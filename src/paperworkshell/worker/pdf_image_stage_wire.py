"""Bounded private plan, evidence, and result envelopes for one image stage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from uuid import UUID

from paperworkshell.domain.pdf_export import (
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetAnalysis,
    PdfImageAssetImpact,
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImagePageAnalysis,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
    PdfImageStageRequest,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_OBJECTS
from paperworkshell.worker.pdf_export_plan import (
    PdfExportWorkerPlan,
    _decode_json,
    _require_array,
    _require_integer,
    _require_object,
    _require_string,
    _require_uuid,
    decode_pdf_export_worker_plan,
    encode_pdf_export_worker_plan,
)
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
    encode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.models import (
    MAX_PDF_EXPORT_PLAN_BYTES,
    MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
)

MAX_PDF_IMAGE_STAGE_RESULT_BYTES = 4096
_REQUEST_FIELDS = frozenset(
    {"request_version", "profile", "selected_asset_ids", "baseline_sha256", "semantic_fingerprint"}
)


@dataclass(frozen=True, slots=True, repr=False)
class PdfImageStageWorkerPlan:
    base: PdfExportWorkerPlan = field(repr=False)
    request: PdfImageStageRequest = field(repr=False)


def _json(value: object, maximum: int) -> bytes:
    payload = json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if not 1 <= len(payload) <= maximum:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.wire_size")
    return payload


def encode_pdf_image_stage_plan(
    plan: PdfExportPlan,
    source_handles: Mapping[UUID, int],
    request: PdfImageStageRequest,
) -> bytes:
    if type(request) is not PdfImageStageRequest:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.request")
    return _json(
        {
            "worker_plan_version": 1,
            "base": json.loads(encode_pdf_export_worker_plan(plan, source_handles)),
            "request": asdict(request),
        },
        MAX_PDF_EXPORT_PLAN_BYTES,
    )


def decode_pdf_image_stage_plan(payload: bytes) -> PdfImageStageWorkerPlan:
    failure = False
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
            raise ValueError
        root = _require_object(
            _decode_json(payload),
            frozenset({"worker_plan_version", "base", "request"}),
            "image_stage.plan",
        )
        if type(root["worker_plan_version"]) is not int or root["worker_plan_version"] != 1:
            raise ValueError
        base = decode_pdf_export_worker_plan(_json(root["base"], MAX_PDF_EXPORT_PLAN_BYTES))
        raw = _require_object(root["request"], _REQUEST_FIELDS, "image_stage.request")
        profile = raw["profile"]
        request = PdfImageStageRequest(
            profile=None
            if profile is None
            else PdfImageStageProfile(_require_string(profile, "image_stage.profile")),
            selected_asset_ids=tuple(
                _require_integer(value, "image_stage.selection")
                for value in _require_array(
                    raw["selected_asset_ids"],
                    minimum=0,
                    maximum=MAX_PDF_INSPECTION_OBJECTS,
                    context="image_stage.selection",
                )
            ),
            baseline_sha256=None
            if raw["baseline_sha256"] is None
            else _require_string(raw["baseline_sha256"], "image_stage.baseline"),
            semantic_fingerprint=None
            if raw["semantic_fingerprint"] is None
            else _require_string(raw["semantic_fingerprint"], "image_stage.fingerprint"),
            request_version=_require_integer(raw["request_version"], "image_stage.request_version"),
        )
        if (
            encode_pdf_image_stage_plan(
                base.plan,
                {item.source.source_id: item.worker_handle for item in base.sources},
                request,
            )
            != payload
        ):
            raise ValueError
        result = PdfImageStageWorkerPlan(base, request)
    except Exception:
        failure = True
    if failure:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.plan_decode")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class PdfImageStageWorkerResult:
    operation_id: UUID
    output_object_id: UUID
    evidence_object_id: UUID
    baseline_report: PdfExportVerificationReport = field(repr=False)
    output_byte_size: int
    output_sha256: str = field(repr=False)
    evidence_byte_size: int
    evidence_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        identifiers = (self.operation_id, self.output_object_id, self.evidence_object_id)
        if (
            any(type(value) is not UUID or value.version != 4 for value in identifiers)
            or len(set(identifiers)) != 3
            or type(self.baseline_report) is not PdfExportVerificationReport
            or type(self.output_byte_size) is not int
            or not 1 <= self.output_byte_size <= self.baseline_report.output_byte_size
            or type(self.evidence_byte_size) is not int
            or not 1 <= self.evidence_byte_size <= MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES
            or any(
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in (self.output_sha256, self.evidence_sha256)
            )
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.result")


def encode_pdf_image_stage_result(value: PdfImageStageWorkerResult) -> bytes:
    if type(value) is not PdfImageStageWorkerResult:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.result_encode")
    return _json(
        {
            "result_version": 1,
            "operation_id": str(value.operation_id),
            "output_object_id": str(value.output_object_id),
            "evidence_object_id": str(value.evidence_object_id),
            "baseline": json.loads(
                encode_pdf_export_worker_report(
                    PdfExportWorkerReport(
                        value.operation_id, value.output_object_id, value.baseline_report
                    )
                )
            ),
            "output_byte_size": value.output_byte_size,
            "output_sha256": value.output_sha256,
            "evidence_byte_size": value.evidence_byte_size,
            "evidence_sha256": value.evidence_sha256,
        },
        MAX_PDF_IMAGE_STAGE_RESULT_BYTES,
    )


def decode_pdf_image_stage_result(payload: bytes) -> PdfImageStageWorkerResult:
    failure = False
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_IMAGE_STAGE_RESULT_BYTES:
            raise ValueError
        root = _require_object(
            _decode_json(payload),
            frozenset(
                {
                    "result_version",
                    "operation_id",
                    "output_object_id",
                    "evidence_object_id",
                    "baseline",
                    "output_byte_size",
                    "output_sha256",
                    "evidence_byte_size",
                    "evidence_sha256",
                }
            ),
            "image_stage.result",
        )
        if type(root["result_version"]) is not int or root["result_version"] != 1:
            raise ValueError
        baseline = decode_pdf_export_worker_report(
            _json(root["baseline"], MAX_PDF_IMAGE_STAGE_RESULT_BYTES)
        )
        result = PdfImageStageWorkerResult(
            operation_id=_require_uuid(root["operation_id"], "image_stage.operation"),
            output_object_id=_require_uuid(root["output_object_id"], "image_stage.output"),
            evidence_object_id=_require_uuid(root["evidence_object_id"], "image_stage.evidence"),
            baseline_report=baseline.report,
            output_byte_size=_require_integer(root["output_byte_size"], "image_stage.output_size"),
            output_sha256=_require_string(root["output_sha256"], "image_stage.output_digest"),
            evidence_byte_size=_require_integer(
                root["evidence_byte_size"], "image_stage.evidence_size"
            ),
            evidence_sha256=_require_string(root["evidence_sha256"], "image_stage.evidence_digest"),
        )
        if (
            baseline.operation_id != result.operation_id
            or baseline.output_object_id != result.output_object_id
            or encode_pdf_image_stage_result(result) != payload
        ):
            raise ValueError
    except Exception:
        failure = True
    if failure:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.result_decode")
    return result


def encode_pdf_image_stage_evidence(
    analysis: PdfImageAnalysis, report: PdfImageStageReport
) -> bytes:
    if type(analysis) is not PdfImageAnalysis or type(report) is not PdfImageStageReport:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.evidence")
    payload = _json(
        {"evidence_version": 1, "analysis": asdict(analysis), "report": asdict(report)},
        MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
    )
    # Apply the same structural cap on both sides of the trust boundary.
    _decode_json(payload)
    return payload


def decode_pdf_image_stage_evidence(payload: bytes) -> tuple[PdfImageAnalysis, PdfImageStageReport]:
    failed = False
    try:
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES
        ):
            raise ValueError
        root = _require_object(
            _decode_json(payload),
            frozenset({"evidence_version", "analysis", "report"}),
            "image_stage.evidence",
        )
        if type(root["evidence_version"]) is not int or root["evidence_version"] != 1:
            raise ValueError
        analysis = _decode_PdfImageAnalysis(root["analysis"])
        report = _decode_PdfImageStageReport(root["report"])
        if encode_pdf_image_stage_evidence(analysis, report) != payload:
            raise ValueError
    except Exception:
        failed = True
    if failed:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.evidence_decode")
    return analysis, report


def _optional_integer(value: object) -> int | None:
    return None if value is None else _require_integer(value, "image_stage.number")


def _indexes(value: object) -> tuple[int, ...]:
    return tuple(
        _require_integer(item, "image_stage.index")
        for item in _require_array(
            value, minimum=0, maximum=MAX_PDF_INSPECTION_OBJECTS, context="image_stage.indexes"
        )
    )


def _decode_PdfImageAssetAnalysis(value: object) -> PdfImageAssetAnalysis:
    raw = _require_object(
        value,
        frozenset(
            {
                "asset_id",
                "page_indexes",
                "width_pixels",
                "height_pixels",
                "effective_dpi_x_milli",
                "effective_dpi_y_milli",
                "color_mode",
                "encoded_byte_size",
                "eligibility",
            }
        ),
        "image_stage.record",
    )
    return PdfImageAssetAnalysis(
        asset_id=_require_integer(raw["asset_id"], "image_stage.number"),
        page_indexes=_indexes(raw["page_indexes"]),
        width_pixels=_require_integer(raw["width_pixels"], "image_stage.number"),
        height_pixels=_require_integer(raw["height_pixels"], "image_stage.number"),
        effective_dpi_x_milli=_optional_integer(raw["effective_dpi_x_milli"]),
        effective_dpi_y_milli=_optional_integer(raw["effective_dpi_y_milli"]),
        color_mode=PdfImageColorMode(_require_string(raw["color_mode"], "image_stage.enum")),
        encoded_byte_size=_require_integer(raw["encoded_byte_size"], "image_stage.number"),
        eligibility=PdfImageEligibility(_require_string(raw["eligibility"], "image_stage.enum")),
    )


def _decode_PdfImagePageAnalysis(value: object) -> PdfImagePageAnalysis:
    raw = _require_object(
        value,
        frozenset({"page_index", "asset_ids", "image_encoded_byte_size", "image_coverage_ppm"}),
        "image_stage.record",
    )
    return PdfImagePageAnalysis(
        page_index=_require_integer(raw["page_index"], "image_stage.number"),
        asset_ids=_indexes(raw["asset_ids"]),
        image_encoded_byte_size=_require_integer(
            raw["image_encoded_byte_size"], "image_stage.number"
        ),
        image_coverage_ppm=_optional_integer(raw["image_coverage_ppm"]),
    )


def _decode_PdfImageAnalysis(value: object) -> PdfImageAnalysis:
    raw = _require_object(
        value,
        frozenset(
            {
                "baseline_sha256",
                "baseline_byte_size",
                "page_count",
                "assets",
                "pages",
                "report_version",
            }
        ),
        "image_stage.record",
    )
    return PdfImageAnalysis(
        baseline_sha256=_require_string(raw["baseline_sha256"], "image_stage.token"),
        baseline_byte_size=_require_integer(raw["baseline_byte_size"], "image_stage.number"),
        page_count=_require_integer(raw["page_count"], "image_stage.number"),
        assets=tuple(
            _decode_PdfImageAssetAnalysis(item)
            for item in _require_array(
                raw["assets"],
                minimum=0,
                maximum=MAX_PDF_INSPECTION_OBJECTS,
                context="image_stage.rows",
            )
        ),
        pages=tuple(
            _decode_PdfImagePageAnalysis(item)
            for item in _require_array(
                raw["pages"],
                minimum=0,
                maximum=MAX_PDF_INSPECTION_OBJECTS,
                context="image_stage.rows",
            )
        ),
        report_version=_require_integer(raw["report_version"], "image_stage.number"),
    )


def _decode_PdfImageAssetImpact(value: object) -> PdfImageAssetImpact:
    raw = _require_object(
        value,
        frozenset(
            {
                "asset_id",
                "page_indexes",
                "original_width_pixels",
                "original_height_pixels",
                "output_width_pixels",
                "output_height_pixels",
                "original_effective_dpi_x_milli",
                "original_effective_dpi_y_milli",
                "output_effective_dpi_x_milli",
                "output_effective_dpi_y_milli",
                "color_mode",
                "original_encoded_byte_size",
                "estimated_encoded_byte_size",
                "candidate_encoded_byte_size",
                "selected_encoded_byte_size",
                "outcome",
            }
        ),
        "image_stage.record",
    )
    return PdfImageAssetImpact(
        asset_id=_require_integer(raw["asset_id"], "image_stage.number"),
        page_indexes=_indexes(raw["page_indexes"]),
        original_width_pixels=_require_integer(raw["original_width_pixels"], "image_stage.number"),
        original_height_pixels=_require_integer(
            raw["original_height_pixels"], "image_stage.number"
        ),
        output_width_pixels=_require_integer(raw["output_width_pixels"], "image_stage.number"),
        output_height_pixels=_require_integer(raw["output_height_pixels"], "image_stage.number"),
        original_effective_dpi_x_milli=_require_integer(
            raw["original_effective_dpi_x_milli"], "image_stage.number"
        ),
        original_effective_dpi_y_milli=_require_integer(
            raw["original_effective_dpi_y_milli"], "image_stage.number"
        ),
        output_effective_dpi_x_milli=_require_integer(
            raw["output_effective_dpi_x_milli"], "image_stage.number"
        ),
        output_effective_dpi_y_milli=_require_integer(
            raw["output_effective_dpi_y_milli"], "image_stage.number"
        ),
        color_mode=PdfImageColorMode(_require_string(raw["color_mode"], "image_stage.enum")),
        original_encoded_byte_size=_require_integer(
            raw["original_encoded_byte_size"], "image_stage.number"
        ),
        estimated_encoded_byte_size=_optional_integer(raw["estimated_encoded_byte_size"]),
        candidate_encoded_byte_size=_optional_integer(raw["candidate_encoded_byte_size"]),
        selected_encoded_byte_size=_require_integer(
            raw["selected_encoded_byte_size"], "image_stage.number"
        ),
        outcome=PdfImageStageOutcome(_require_string(raw["outcome"], "image_stage.enum")),
    )


def _decode_PdfImagePageImpact(value: object) -> PdfImagePageImpact:
    raw = _require_object(
        value,
        frozenset(
            {
                "page_index",
                "selected_asset_ids",
                "original_image_byte_size",
                "estimated_image_byte_size",
                "actual_image_byte_size",
            }
        ),
        "image_stage.record",
    )
    return PdfImagePageImpact(
        page_index=_require_integer(raw["page_index"], "image_stage.number"),
        selected_asset_ids=_indexes(raw["selected_asset_ids"]),
        original_image_byte_size=_require_integer(
            raw["original_image_byte_size"], "image_stage.number"
        ),
        estimated_image_byte_size=_optional_integer(raw["estimated_image_byte_size"]),
        actual_image_byte_size=_require_integer(
            raw["actual_image_byte_size"], "image_stage.number"
        ),
    )


def _decode_PdfImageStageReport(value: object) -> PdfImageStageReport:
    raw = _require_object(
        value,
        frozenset(
            {
                "profile",
                "outcome",
                "semantic_fingerprint",
                "baseline_sha256",
                "baseline_byte_size",
                "candidate_byte_size",
                "output_sha256",
                "output_byte_size",
                "page_count",
                "assets",
                "pages",
                "verified_page_count",
                "report_version",
            }
        ),
        "image_stage.record",
    )
    return PdfImageStageReport(
        profile=None
        if raw["profile"] is None
        else PdfImageStageProfile(_require_string(raw["profile"], "image_stage.profile")),
        outcome=PdfImageStageOutcome(_require_string(raw["outcome"], "image_stage.enum")),
        semantic_fingerprint=_require_string(raw["semantic_fingerprint"], "image_stage.token"),
        baseline_sha256=_require_string(raw["baseline_sha256"], "image_stage.token"),
        baseline_byte_size=_require_integer(raw["baseline_byte_size"], "image_stage.number"),
        candidate_byte_size=_optional_integer(raw["candidate_byte_size"]),
        output_sha256=_require_string(raw["output_sha256"], "image_stage.token"),
        output_byte_size=_require_integer(raw["output_byte_size"], "image_stage.number"),
        page_count=_require_integer(raw["page_count"], "image_stage.number"),
        assets=tuple(
            _decode_PdfImageAssetImpact(item)
            for item in _require_array(
                raw["assets"],
                minimum=0,
                maximum=MAX_PDF_INSPECTION_OBJECTS,
                context="image_stage.rows",
            )
        ),
        pages=tuple(
            _decode_PdfImagePageImpact(item)
            for item in _require_array(
                raw["pages"],
                minimum=0,
                maximum=MAX_PDF_INSPECTION_OBJECTS,
                context="image_stage.rows",
            )
        ),
        verified_page_count=_require_integer(raw["verified_page_count"], "image_stage.number"),
        report_version=_require_integer(raw["report_version"], "image_stage.number"),
    )
