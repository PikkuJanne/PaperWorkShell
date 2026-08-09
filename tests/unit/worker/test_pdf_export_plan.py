"""Tests for the bounded path-free PDF-export worker-plan envelope."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.worker.pdf_export_plan import (
    decode_pdf_export_worker_plan,
    encode_pdf_export_worker_plan,
)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _plan() -> PdfExportPlan:
    sources = (
        PdfExportSource(
            source_id=_uuid(10),
            byte_size=100,
            page_count=2,
            sha256="a" * 64,
            signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        ),
        PdfExportSource(
            source_id=_uuid(11),
            byte_size=200,
            page_count=1,
            sha256="b" * 64,
            signature_state=PdfExportSourceSignatureState.MARKERS_DETECTED,
        ),
    )
    return PdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=7,
        manifest_sha256="c" * 64,
        manifest_cas_token="synthetic_export_cas_001",
        sources=sources,
        pages=(
            PdfExportPage(0, _uuid(20), _uuid(10), 1, PdfExportRotation.DEGREES_270),
            PdfExportPage(1, _uuid(21), _uuid(11), 0, PdfExportRotation.DEGREES_90),
        ),
    )


def _encoded() -> bytes:
    return encode_pdf_export_worker_plan(_plan(), {_uuid(10): 501, _uuid(11): 503})


def _mutate(**updates: object) -> bytes:
    document = json.loads(_encoded())
    document.update(updates)
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_worker_plan_round_trip_is_canonical_exact_and_redacted() -> None:
    plan = _plan()
    handles = {_uuid(11): 503, _uuid(10): 501}

    first = encode_pdf_export_worker_plan(plan, handles)
    second = encode_pdf_export_worker_plan(plan, dict(reversed(tuple(handles.items()))))
    decoded = decode_pdf_export_worker_plan(first)

    assert first == second
    assert decoded.plan == plan
    assert decoded.handle_for(_uuid(10)) == 501
    assert decoded.handle_for(_uuid(11)) == 503
    rendered = repr(decoded)
    assert "501" not in rendered
    assert "503" not in rendered
    assert "synthetic_export_cas_001" not in rendered
    assert "a" * 64 not in rendered


@pytest.mark.parametrize(
    "handles",
    (
        {},
        {_uuid(10): 501},
        {_uuid(10): 501, _uuid(11): 503, _uuid(12): 505},
        {_uuid(10): 501, _uuid(11): 501},
        {_uuid(10): 0, _uuid(11): 503},
    ),
)
def test_worker_plan_encoder_rejects_missing_extra_duplicate_or_invalid_handles(
    handles: dict[UUID, int],
) -> None:
    with pytest.raises(PdfExportError) as raised:
        encode_pdf_export_worker_plan(_plan(), handles)

    assert raised.value.code is PdfExportErrorCode.MODEL_INVALID


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"not-json",
        b"{} trailing",
        b"\xff",
        b'{"worker_plan_version":1,"worker_plan_version":1}',
        b"[1.5]",
        b"[NaN]",
        b"[9007199254740992]",
        b"[" * 20 + b"0" + b"]" * 20,
    ),
    ids=(
        "empty",
        "not_json",
        "surplus",
        "utf8",
        "duplicate",
        "float",
        "nan",
        "large_integer",
        "deep",
    ),
)
def test_worker_plan_decoder_rejects_malformed_or_unbounded_json(payload: bytes) -> None:
    with pytest.raises(PdfExportError) as raised:
        decode_pdf_export_worker_plan(payload)

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID


@pytest.mark.parametrize(
    "payload",
    (
        _mutate(worker_plan_version=2),
        _mutate(plan_version=2),
        _mutate(semantic_fingerprint="0" * 64),
        _mutate(profile={}),
        _mutate(sources=[]),
        _mutate(pages=[]),
    ),
    ids=("future_worker", "future_plan", "fingerprint", "profile", "sources", "pages"),
)
def test_worker_plan_decoder_rejects_contract_tampering(payload: bytes) -> None:
    with pytest.raises(PdfExportError) as raised:
        decode_pdf_export_worker_plan(payload)

    assert raised.value.code in {
        PdfExportErrorCode.RESULT_INVALID,
        PdfExportErrorCode.PLAN_EMPTY,
        PdfExportErrorCode.MODEL_INVALID,
    }


def test_worker_plan_decoder_rejects_duplicate_handles_and_unknown_lookup() -> None:
    document = json.loads(_encoded())
    document["sources"][1]["worker_handle"] = document["sources"][0]["worker_handle"]
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")

    with pytest.raises(PdfExportError) as duplicate:
        decode_pdf_export_worker_plan(payload)
    assert duplicate.value.code is PdfExportErrorCode.MODEL_INVALID

    decoded = decode_pdf_export_worker_plan(_encoded())
    with pytest.raises(PdfExportError) as lookup:
        decoded.handle_for(_uuid(99))
    assert lookup.value.code is PdfExportErrorCode.MODEL_INVALID
