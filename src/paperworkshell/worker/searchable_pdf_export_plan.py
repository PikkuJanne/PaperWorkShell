"""Canonical path-free worker envelope for searchable mixed-PDF export."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import NormalizedPageBox
from paperworkshell.domain.searchable_pdf_export import (
    DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE,
    MAX_SEARCHABLE_PDF_PAGES,
    MAX_SEARCHABLE_PDF_SOURCES,
    SEARCHABLE_PDF_PLAN_VERSION,
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    reject_searchable_pdf_export,
)
from paperworkshell.worker.protocol.models import MAX_PDF_EXPORT_PLAN_BYTES

SEARCHABLE_PDF_WORKER_PLAN_VERSION: Final = 1
MAX_SEARCHABLE_PDF_WORKER_PLAN_DEPTH: Final = 16
MAX_SEARCHABLE_PDF_WORKER_PLAN_NODES: Final = 98_304
MAX_SEARCHABLE_PDF_WORKER_PLAN_STRING_CODEPOINTS: Final = 4_096

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
    {
        "profile_version",
        "eligibility_policy_version",
        "text_presence_policy_version",
        "ocr_language_set_id",
        "ocr_profile_id",
        "ocr_engine_profile_sha256",
        "eligibility_policy_id",
        "text_presence_profile_id",
        "pdf_profile_version",
        "pdf_assembly",
        "pdf_render",
        "pdf_qpdf",
        "pdf_encryption_policy",
    }
)
_SOURCE_MEMBERS: Final = frozenset(
    {
        "source_version",
        "source_id",
        "media_type",
        "byte_size",
        "page_count",
        "sha256",
        "signature_state",
        "encryption_state",
        "worker_handle",
    }
)
_PAGE_MEMBERS: Final = frozenset(
    {
        "page_version",
        "output_page_index",
        "page_id",
        "source_id",
        "source_page_index",
        "quarter_turns_clockwise",
        "crop",
        "deskew_angle_millidegrees_clockwise",
        "contrast_factor_millionths",
        "background_cleanup_strength_millionths",
    }
)
_CROP_MEMBERS: Final = frozenset({"coordinate_space", "x", "y", "width", "height"})


class _PlanDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SearchablePdfWorkerSource:
    """One authenticated source paired with a reduced-right worker handle."""

    source: SearchablePdfSource
    worker_handle: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.source) is not SearchablePdfSource
            or type(self.worker_handle) is not int
            or not 1 <= self.worker_handle <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.worker_source",
            )


@dataclass(frozen=True, slots=True)
class SearchablePdfWorkerPlan:
    """Decoded domain plan whose native handle values remain representation-safe."""

    plan: SearchablePdfExportPlan
    sources: tuple[SearchablePdfWorkerSource, ...] = field(repr=False)
    worker_plan_version: int = SEARCHABLE_PDF_WORKER_PLAN_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not SearchablePdfExportPlan
            or type(self.sources) is not tuple
            or any(type(source) is not SearchablePdfWorkerSource for source in self.sources)
            or tuple(source.source for source in self.sources) != self.plan.sources
            or type(self.worker_plan_version) is not int
            or self.worker_plan_version != SEARCHABLE_PDF_WORKER_PLAN_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.worker_plan",
            )
        handles = tuple(source.worker_handle for source in self.sources)
        if len(set(handles)) != len(handles):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.worker_handles",
            )


def encode_searchable_pdf_worker_plan(
    plan: SearchablePdfExportPlan,
    source_handles: Mapping[UUID, int],
) -> bytes:
    """Encode a canonical bounded plan after exact source-handle binding."""

    if type(plan) is not SearchablePdfExportPlan or not isinstance(source_handles, Mapping):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.worker_encode",
        )
    expected_ids = tuple(source.source_id for source in plan.sources)
    try:
        supplied_ids = tuple(source_handles.keys())
        handles = tuple(source_handles[source_id] for source_id in expected_ids)
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.worker_handles",
        )
    if (
        set(supplied_ids) != set(expected_ids)
        or len(supplied_ids) != len(expected_ids)
        or any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER
            for value in handles
        )
        or len(set(handles)) != len(handles)
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.worker_handles",
        )
    profile = plan.profile
    pdf_profile = profile.pdf_profile
    document = {
        "worker_plan_version": SEARCHABLE_PDF_WORKER_PLAN_VERSION,
        "plan_version": plan.plan_version,
        "case_id": str(plan.case_id),
        "manifest_revision": plan.manifest_revision,
        "manifest_sha256": plan.manifest_sha256,
        "manifest_cas_token": plan.manifest_cas_token,
        "semantic_fingerprint": plan.semantic_fingerprint,
        "profile": {
            "profile_version": profile.profile_version,
            "eligibility_policy_version": profile.eligibility_policy_version,
            "text_presence_policy_version": profile.text_presence_policy_version,
            "ocr_language_set_id": profile.ocr_language_set_id,
            "ocr_profile_id": profile.ocr_profile_id,
            "ocr_engine_profile_sha256": profile.ocr_engine_profile_sha256,
            "eligibility_policy_id": profile.eligibility_policy_id,
            "text_presence_profile_id": profile.text_presence_profile_id,
            "pdf_profile_version": pdf_profile.profile_version,
            "pdf_assembly": pdf_profile.assembly.value,
            "pdf_render": pdf_profile.render.value,
            "pdf_qpdf": pdf_profile.qpdf.value,
            "pdf_encryption_policy": pdf_profile.encryption_policy.value,
        },
        "sources": [
            {
                "source_version": source.source_version,
                "source_id": str(source.source_id),
                "media_type": source.media_type.value,
                "byte_size": source.byte_size,
                "page_count": source.page_count,
                "sha256": source.sha256,
                "signature_state": source.signature_state.value,
                "encryption_state": source.encryption_state.value,
                "worker_handle": handle,
            }
            for source, handle in zip(plan.sources, handles, strict=True)
        ],
        "pages": [_encode_page(page) for page in plan.pages],
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
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.worker_encode",
        )
    if not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.worker_plan_size",
        )
    return payload


def decode_searchable_pdf_worker_plan(payload: bytes) -> SearchablePdfWorkerPlan:
    """Decode exact canonical bytes and reconstruct the trusted domain model."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.worker_plan_size",
        )
    try:
        document = _decode_json(payload)
        root = _object(document, _ROOT_MEMBERS)
        if _integer(root["worker_plan_version"]) != SEARCHABLE_PDF_WORKER_PLAN_VERSION:
            raise _PlanDecodeError
        if _integer(root["plan_version"]) != SEARCHABLE_PDF_PLAN_VERSION:
            raise _PlanDecodeError
        profile_doc = _object(root["profile"], _PROFILE_MEMBERS)
        expected_profile = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE
        expected_pdf = expected_profile.pdf_profile
        expected_profile_values = {
            "profile_version": expected_profile.profile_version,
            "eligibility_policy_version": expected_profile.eligibility_policy_version,
            "text_presence_policy_version": expected_profile.text_presence_policy_version,
            "ocr_language_set_id": expected_profile.ocr_language_set_id,
            "ocr_profile_id": expected_profile.ocr_profile_id,
            "ocr_engine_profile_sha256": expected_profile.ocr_engine_profile_sha256,
            "eligibility_policy_id": expected_profile.eligibility_policy_id,
            "text_presence_profile_id": expected_profile.text_presence_profile_id,
            "pdf_profile_version": expected_pdf.profile_version,
            "pdf_assembly": expected_pdf.assembly.value,
            "pdf_render": expected_pdf.render.value,
            "pdf_qpdf": expected_pdf.qpdf.value,
            "pdf_encryption_policy": expected_pdf.encryption_policy.value,
        }
        if dict(profile_doc) != expected_profile_values:
            raise _PlanDecodeError
        sources_raw = _array(root["sources"], maximum=MAX_SEARCHABLE_PDF_SOURCES)
        pages_raw = _array(root["pages"], maximum=MAX_SEARCHABLE_PDF_PAGES)
        worker_sources: list[SearchablePdfWorkerSource] = []
        for item in sources_raw:
            value = _object(item, _SOURCE_MEMBERS)
            source = SearchablePdfSource(
                source_id=_uuid(value["source_id"]),
                media_type=SearchablePdfSourceMediaType(_string(value["media_type"])),
                byte_size=_integer(value["byte_size"]),
                page_count=_integer(value["page_count"]),
                sha256=_string(value["sha256"]),
                signature_state=SearchablePdfSourceSignatureState(
                    _string(value["signature_state"])
                ),
                encryption_state=SearchablePdfSourceEncryptionState(
                    _string(value["encryption_state"])
                ),
                source_version=_integer(value["source_version"]),
            )
            worker_sources.append(
                SearchablePdfWorkerSource(
                    source=source,
                    worker_handle=_integer(value["worker_handle"]),
                )
            )
        pages = tuple(_decode_page(item) for item in pages_raw)
        plan = SearchablePdfExportPlan(
            case_id=_uuid(root["case_id"]),
            manifest_revision=_integer(root["manifest_revision"]),
            manifest_sha256=_string(root["manifest_sha256"]),
            manifest_cas_token=_string(root["manifest_cas_token"]),
            sources=tuple(item.source for item in worker_sources),
            pages=pages,
            profile=expected_profile,
            plan_version=_integer(root["plan_version"]),
        )
        if plan.semantic_fingerprint != _string(root["semantic_fingerprint"]):
            raise _PlanDecodeError
        decoded = SearchablePdfWorkerPlan(plan=plan, sources=tuple(worker_sources))
        if (
            encode_searchable_pdf_worker_plan(
                decoded.plan,
                {item.source.source_id: item.worker_handle for item in decoded.sources},
            )
            != payload
        ):
            raise _PlanDecodeError
        return decoded
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.worker_plan",
        )


def _encode_page(page: SearchablePdfPage) -> dict[str, object]:
    crop: dict[str, object] | None = None
    if page.crop is not None:
        crop = page.crop.to_manifest()
    return {
        "page_version": page.page_version,
        "output_page_index": page.output_page_index,
        "page_id": str(page.page_id),
        "source_id": str(page.source_id),
        "source_page_index": page.source_page_index,
        "quarter_turns_clockwise": page.quarter_turns_clockwise,
        "crop": crop,
        "deskew_angle_millidegrees_clockwise": page.deskew_angle_millidegrees_clockwise,
        "contrast_factor_millionths": page.contrast_factor_millionths,
        "background_cleanup_strength_millionths": (page.background_cleanup_strength_millionths),
    }


def _decode_page(value: object) -> SearchablePdfPage:
    item = _object(value, _PAGE_MEMBERS)
    crop_raw = item["crop"]
    crop: NormalizedPageBox | None
    if crop_raw is None:
        crop = None
    else:
        crop_doc = _object(crop_raw, _CROP_MEMBERS)
        crop = NormalizedPageBox.from_manifest(crop_doc)
    return SearchablePdfPage(
        output_page_index=_integer(item["output_page_index"]),
        page_id=_uuid(item["page_id"]),
        source_id=_uuid(item["source_id"]),
        source_page_index=_integer(item["source_page_index"]),
        quarter_turns_clockwise=_integer(item["quarter_turns_clockwise"]),
        crop=crop,
        deskew_angle_millidegrees_clockwise=_integer(item["deskew_angle_millidegrees_clockwise"]),
        contrast_factor_millionths=_integer(item["contrast_factor_millionths"]),
        background_cleanup_strength_millionths=_integer(
            item["background_cleanup_strength_millionths"]
        ),
        page_version=_integer(item["page_version"]),
    )


def _decode_json(payload: bytes) -> object:
    try:
        text = payload.decode("ascii")
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except Exception as error:
        raise _PlanDecodeError from error
    _walk(document)
    return document


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _PlanDecodeError
        result[key] = value
    return result


def _reject_constant(_: str) -> Never:
    raise _PlanDecodeError


def _reject_float(_: str) -> Never:
    raise _PlanDecodeError


def _walk(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if (
            nodes > MAX_SEARCHABLE_PDF_WORKER_PLAN_NODES
            or depth > MAX_SEARCHABLE_PDF_WORKER_PLAN_DEPTH
        ):
            raise _PlanDecodeError
        if type(current) is str:
            if len(current) > MAX_SEARCHABLE_PDF_WORKER_PLAN_STRING_CODEPOINTS:
                raise _PlanDecodeError
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif type(current) is dict:
            stack.extend((item, depth + 1) for item in cast(dict[str, object], current).values())
        elif current is not None and type(current) not in (int, bool):
            raise _PlanDecodeError


def _object(value: object, members: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise _PlanDecodeError
    result = cast(dict[str, object], value)
    if set(result) != members:
        raise _PlanDecodeError
    return result


def _array(value: object, *, maximum: int) -> list[object]:
    if type(value) is not list or not 1 <= len(value) <= maximum:
        raise _PlanDecodeError
    return cast(list[object], value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise _PlanDecodeError
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise _PlanDecodeError
    return value


def _uuid(value: object) -> UUID:
    text = _string(value)
    try:
        parsed = UUID(text)
    except ValueError as error:
        raise _PlanDecodeError from error
    if parsed.version != 4 or str(parsed) != text:
        raise _PlanDecodeError
    return parsed
