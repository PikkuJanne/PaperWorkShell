"""Bounded path-free worker envelope for one deterministic PDF export plan."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    MAX_PDF_EXPORT_PAGES,
    MAX_PDF_EXPORT_SOURCES,
    PDF_EXPORT_PLAN_VERSION,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    reject_pdf_export,
)
from paperworkshell.worker.protocol.models import MAX_PDF_EXPORT_PLAN_BYTES

PDF_EXPORT_WORKER_PLAN_VERSION: Final = 1
MAX_PDF_EXPORT_WORKER_PLAN_DEPTH: Final = 16
MAX_PDF_EXPORT_WORKER_PLAN_NODES: Final = 65_536
MAX_PDF_EXPORT_WORKER_PLAN_STRING_CODEPOINTS: Final = 4_096

_ROOT_MEMBERS: Final = frozenset(
    {
        "worker_plan_version",
        "plan_version",
        "case_id",
        "manifest_revision",
        "manifest_sha256",
        "manifest_cas_token",
        "semantic_fingerprint",
        "profile",
        "sources",
        "pages",
    }
)
_PROFILE_MEMBERS: Final = frozenset(
    {"profile_version", "assembly", "render", "qpdf", "encryption_policy"}
)
_SOURCE_MEMBERS: Final = frozenset(
    {
        "source_id",
        "byte_size",
        "page_count",
        "sha256",
        "signature_state",
        "media_type",
        "encryption_state",
        "worker_handle",
    }
)
_PAGE_MEMBERS: Final = frozenset(
    {
        "output_page_index",
        "page_id",
        "source_id",
        "source_page_index",
        "additional_rotation_degrees",
    }
)


class _PlanDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfExportWorkerSource:
    """One plan source paired with its worker-local reduced-right handle value."""

    source: PdfExportSource
    worker_handle: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.source) is not PdfExportSource
            or type(self.worker_handle) is not int
            or not 1 <= self.worker_handle <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_source")


@dataclass(frozen=True, slots=True)
class PdfExportWorkerPlan:
    """Decoded plan with handle values hidden from representations."""

    plan: PdfExportPlan
    sources: tuple[PdfExportWorkerSource, ...] = field(repr=False)
    worker_plan_version: int = PDF_EXPORT_WORKER_PLAN_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not PdfExportPlan
            or type(self.sources) is not tuple
            or any(type(source) is not PdfExportWorkerSource for source in self.sources)
            or type(self.worker_plan_version) is not int
            or self.worker_plan_version != PDF_EXPORT_WORKER_PLAN_VERSION
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_plan")
        if tuple(source.source for source in self.sources) != self.plan.sources:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_sources")
        handles = tuple(source.worker_handle for source in self.sources)
        if len(set(handles)) != len(handles):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")

    def handle_for(self, source_id: UUID) -> int:
        """Return one worker handle without including its value in any failure."""

        if type(source_id) is not UUID or source_id.version != 4:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_lookup")
        for source in self.sources:
            if source.source.source_id == source_id:
                return source.worker_handle
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_lookup")


def encode_pdf_export_worker_plan(
    plan: PdfExportPlan,
    source_handles: Mapping[UUID, int],
) -> bytes:
    """Encode a canonical bounded envelope after exact source-handle binding."""

    if type(plan) is not PdfExportPlan or not isinstance(source_handles, Mapping):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_encode")
    expected_ids = tuple(source.source_id for source in plan.sources)
    try:
        supplied_ids = tuple(source_handles.keys())
    except Exception:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")
    if set(supplied_ids) != set(expected_ids) or len(supplied_ids) != len(expected_ids):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")
    handles: list[int] = []
    for source_id in expected_ids:
        try:
            value = source_handles[source_id]
        except Exception:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")
        if type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")
        handles.append(value)
    if len(set(handles)) != len(handles):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_handles")

    profile = plan.profile
    document = {
        "worker_plan_version": PDF_EXPORT_WORKER_PLAN_VERSION,
        "plan_version": plan.plan_version,
        "case_id": str(plan.case_id),
        "manifest_revision": plan.manifest_revision,
        "manifest_sha256": plan.manifest_sha256,
        "manifest_cas_token": plan.manifest_cas_token,
        "semantic_fingerprint": plan.semantic_fingerprint,
        "profile": {
            "profile_version": profile.profile_version,
            "assembly": profile.assembly.value,
            "render": profile.render.value,
            "qpdf": profile.qpdf.value,
            "encryption_policy": profile.encryption_policy.value,
        },
        "sources": [
            {
                "source_id": str(source.source_id),
                "byte_size": source.byte_size,
                "page_count": source.page_count,
                "sha256": source.sha256,
                "signature_state": source.signature_state.value,
                "media_type": source.media_type.value,
                "encryption_state": source.encryption_state.value,
                "worker_handle": handle,
            }
            for source, handle in zip(plan.sources, handles, strict=True)
        ],
        "pages": [
            {
                "output_page_index": page.output_page_index,
                "page_id": str(page.page_id),
                "source_id": str(page.source_id),
                "source_page_index": page.source_page_index,
                "additional_rotation_degrees": int(page.additional_rotation),
            }
            for page in plan.pages
        ],
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
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_encode")
    if not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_plan_size")
    return payload


def decode_pdf_export_worker_plan(payload: bytes) -> PdfExportWorkerPlan:
    """Decode an exact canonical envelope and reconstruct its typed domain plan."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_plan_size")
    document = _decode_json(payload)
    root = _require_object(document, _ROOT_MEMBERS, "export.worker_plan")
    if _require_integer(root["worker_plan_version"], "export.worker_plan_version") != 1:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_plan_version")
    plan_version = _require_integer(root["plan_version"], "export.plan_version")
    if plan_version != PDF_EXPORT_PLAN_VERSION:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_version")
    _decode_profile(root["profile"])

    raw_sources = _require_array(
        root["sources"],
        minimum=1,
        maximum=MAX_PDF_EXPORT_SOURCES,
        context="export.worker_sources",
    )
    sources: list[PdfExportSource] = []
    worker_sources: list[PdfExportWorkerSource] = []
    for member in raw_sources:
        record = _require_object(member, _SOURCE_MEMBERS, "export.worker_source")
        try:
            source = PdfExportSource(
                source_id=_require_uuid(record["source_id"], "export.source_id"),
                byte_size=_require_integer(record["byte_size"], "export.source_size"),
                page_count=_require_integer(record["page_count"], "export.source_pages"),
                sha256=_require_string(record["sha256"], "export.source_digest"),
                signature_state=PdfExportSourceSignatureState(
                    _require_string(record["signature_state"], "export.source_signature")
                ),
            )
        except PdfExportError:
            raise
        except ValueError:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_source")
        if (
            record["media_type"] != source.media_type.value
            or record["encryption_state"] != source.encryption_state.value
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_source")
        worker_source = PdfExportWorkerSource(
            source,
            _require_integer(record["worker_handle"], "export.source_handle"),
        )
        sources.append(source)
        worker_sources.append(worker_source)

    raw_pages = _require_array(
        root["pages"],
        minimum=1,
        maximum=MAX_PDF_EXPORT_PAGES,
        context="export.worker_pages",
    )
    pages: list[PdfExportPage] = []
    for member in raw_pages:
        record = _require_object(member, _PAGE_MEMBERS, "export.worker_page")
        try:
            pages.append(
                PdfExportPage(
                    output_page_index=_require_integer(
                        record["output_page_index"], "export.output_index"
                    ),
                    page_id=_require_uuid(record["page_id"], "export.page_id"),
                    source_id=_require_uuid(record["source_id"], "export.page_source"),
                    source_page_index=_require_integer(
                        record["source_page_index"], "export.source_page"
                    ),
                    additional_rotation=PdfExportRotation(
                        _require_integer(
                            record["additional_rotation_degrees"],
                            "export.page_rotation",
                        )
                    ),
                )
            )
        except PdfExportError:
            raise
        except ValueError:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_page")

    try:
        plan = PdfExportPlan(
            case_id=_require_uuid(root["case_id"], "export.case_id"),
            manifest_revision=_require_integer(
                root["manifest_revision"], "export.manifest_revision"
            ),
            manifest_sha256=_require_string(root["manifest_sha256"], "export.manifest_digest"),
            manifest_cas_token=_require_string(root["manifest_cas_token"], "export.manifest_cas"),
            sources=tuple(sources),
            pages=tuple(pages),
            profile=DEFAULT_PDF_EXPORT_PROFILE,
            plan_version=plan_version,
        )
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_plan")
    if plan.semantic_fingerprint != root["semantic_fingerprint"]:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_fingerprint")
    return PdfExportWorkerPlan(plan, tuple(worker_sources))


def _decode_json(payload: bytes) -> object:
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=_bounded_json_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        _validate_tree(value)
        return value
    except PdfExportError:
        raise
    except UnicodeDecodeError, json.JSONDecodeError, _PlanDecodeError, RecursionError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_plan_json")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_plan_json")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _PlanDecodeError
        result[key] = value
    return result


def _bounded_json_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_SAFE_MANIFEST_INTEGER <= parsed <= MAX_SAFE_MANIFEST_INTEGER:
        raise _PlanDecodeError
    return parsed


def _reject_number(_value: str) -> Never:
    raise _PlanDecodeError


def _validate_tree(root: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_PDF_EXPORT_WORKER_PLAN_NODES or depth > MAX_PDF_EXPORT_WORKER_PLAN_DEPTH:
            raise _PlanDecodeError
        if type(value) is str:
            if len(value) > MAX_PDF_EXPORT_WORKER_PLAN_STRING_CODEPOINTS:
                raise _PlanDecodeError
        elif type(value) is list:
            stack.extend((member, depth + 1) for member in value)
        elif type(value) is dict:
            stack.extend((key, depth + 1) for key in value)
            stack.extend((member, depth + 1) for member in value.values())
        elif type(value) not in {int, bool, type(None)}:
            raise _PlanDecodeError


def _require_object(value: object, members: frozenset[str], context: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != members:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return cast(dict[str, object], value)


def _require_array(
    value: object,
    *,
    minimum: int,
    maximum: int,
    context: str,
) -> list[object]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return cast(list[object], value)


def _require_integer(value: object, context: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return value


def _require_string(value: object, context: str) -> str:
    if type(value) is not str:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return value


def _require_uuid(value: object, context: str) -> UUID:
    text = _require_string(value, context)
    try:
        parsed = UUID(text)
    except ValueError, AttributeError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    if parsed.version != 4 or str(parsed) != text:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return parsed


def _decode_profile(value: object) -> None:
    profile = _require_object(value, _PROFILE_MEMBERS, "export.worker_profile")
    expected = DEFAULT_PDF_EXPORT_PROFILE
    if profile != {
        "profile_version": expected.profile_version,
        "assembly": expected.assembly.value,
        "render": expected.render.value,
        "qpdf": expected.qpdf.value,
        "encryption_policy": expected.encryption_policy.value,
    }:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.worker_profile")
