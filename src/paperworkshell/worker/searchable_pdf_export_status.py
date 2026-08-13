"""Canonical bounded page-status section for searchable mixed-PDF export."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.searchable_pdf_export import (
    MAX_SEARCHABLE_PDF_PAGES,
    SearchablePdfExportErrorCode,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    reject_searchable_pdf_export,
)
from paperworkshell.worker.protocol.models import MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES

SEARCHABLE_PDF_STATUS_VERSION: Final = 1
_ROOT_MEMBERS: Final = frozenset(
    {"status_version", "operation_id", "status_object_id", "semantic_fingerprint", "pages"}
)
_PAGE_MEMBERS: Final = frozenset(
    {
        "output_page_index",
        "page_id",
        "eligibility",
        "outcome",
        "searchability",
        "failure_code",
        "eligibility_version",
        "outcome_version",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfPageStatusDocument:
    """Content-free page outcomes bound to an operation, object, and plan."""

    operation_id: UUID
    status_object_id: UUID
    semantic_fingerprint: str = field(repr=False)
    page_outcomes: tuple[SearchablePdfPageOutcome, ...]
    status_version: int = SEARCHABLE_PDF_STATUS_VERSION

    def __post_init__(self) -> None:
        if (
            not _uuid4(self.operation_id)
            or not _uuid4(self.status_object_id)
            or self.operation_id == self.status_object_id
            or not _sha256(self.semantic_fingerprint)
            or type(self.page_outcomes) is not tuple
            or not 1 <= len(self.page_outcomes) <= MAX_SEARCHABLE_PDF_PAGES
            or any(type(value) is not SearchablePdfPageOutcome for value in self.page_outcomes)
            or tuple(value.output_page_index for value in self.page_outcomes)
            != tuple(range(len(self.page_outcomes)))
            or len({value.page_id for value in self.page_outcomes}) != len(self.page_outcomes)
            or type(self.status_version) is not int
            or self.status_version != SEARCHABLE_PDF_STATUS_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.status",
            )

    def __repr__(self) -> str:
        return (
            "SearchablePdfPageStatusDocument("
            f"page_count={len(self.page_outcomes)}, status_version={self.status_version})"
        )


def encode_searchable_pdf_page_status(value: SearchablePdfPageStatusDocument) -> bytes:
    """Encode exact canonical ASCII bytes for the bounded status section."""

    if type(value) is not SearchablePdfPageStatusDocument:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.status_encode",
        )
    document = {
        "status_version": value.status_version,
        "operation_id": str(value.operation_id),
        "status_object_id": str(value.status_object_id),
        "semantic_fingerprint": value.semantic_fingerprint,
        "pages": [
            {
                "output_page_index": outcome.output_page_index,
                "page_id": str(outcome.page_id),
                "eligibility": outcome.eligibility.code.value,
                "outcome": outcome.outcome.value,
                "searchability": outcome.searchability.value,
                "failure_code": (
                    None if outcome.failure_code is None else outcome.failure_code.value
                ),
                "eligibility_version": outcome.eligibility.eligibility_version,
                "outcome_version": outcome.outcome_version,
            }
            for outcome in value.page_outcomes
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
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.status_encode",
        )
    if not 1 <= len(payload) <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.status_size",
        )
    return payload


def decode_searchable_pdf_page_status(payload: bytes) -> SearchablePdfPageStatusDocument:
    """Decode exact canonical bytes and reconstruct every closed page outcome."""

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.status_size",
        )
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        root = _object(document, _ROOT_MEMBERS)
        raw_pages = root["pages"]
        if type(raw_pages) is not list or not 1 <= len(raw_pages) <= MAX_SEARCHABLE_PDF_PAGES:
            raise ValueError
        outcomes: list[SearchablePdfPageOutcome] = []
        for raw in raw_pages:
            page = _object(raw, _PAGE_MEMBERS)
            failure_raw = page["failure_code"]
            failure = (
                None if failure_raw is None else SearchablePdfPageFailureCode(_string(failure_raw))
            )
            eligibility = SearchablePdfPageEligibility(
                output_page_index=_integer(page["output_page_index"]),
                page_id=_uuid(page["page_id"]),
                code=SearchablePdfPageEligibilityCode(_string(page["eligibility"])),
                eligibility_version=_integer(page["eligibility_version"]),
            )
            outcomes.append(
                SearchablePdfPageOutcome(
                    eligibility=eligibility,
                    outcome=SearchablePdfPageOutcomeCode(_string(page["outcome"])),
                    searchability=SearchablePdfPageSearchability(_string(page["searchability"])),
                    failure_code=failure,
                    outcome_version=_integer(page["outcome_version"]),
                )
            )
        result = SearchablePdfPageStatusDocument(
            operation_id=_uuid(root["operation_id"]),
            status_object_id=_uuid(root["status_object_id"]),
            semantic_fingerprint=_string(root["semantic_fingerprint"]),
            page_outcomes=tuple(outcomes),
            status_version=_integer(root["status_version"]),
        )
        if encode_searchable_pdf_page_status(result) != payload:
            raise ValueError
        return result
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.status_decode",
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


def _reject_number(_value: str) -> None:
    raise ValueError


def _object(value: object, members: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != members:
        raise ValueError
    return cast(dict[str, object], value)


def _integer(value: object) -> int:
    if type(value) is not int:
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


def _uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
