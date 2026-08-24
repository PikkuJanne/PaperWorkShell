"""Privacy, hostile-input, and bounded-history evidence for PWS-0304."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.overlays.commands as command_module
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.commands import (
    CreateCoreOverlayCommand,
    OverlayCommandHistory,
    ReplaceCoreOverlayPayloadCommand,
    ReplaceOverlayBoxCommand,
)
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    RgbaColor,
    TextualOverlayPayload,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
PRIVATE_CANARY = "SYNTHETIC-PRIVATE-FORM-VALUE-C:/private/applicant.pdf"
BOX = NormalizedPageBox(100_000, 100_000, 200_000, 60_000)


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    case_id = selected["case_id"]
    revision = selected["revision"]
    assert type(case_id) is str and type(revision) is int
    return ManifestSnapshot(
        UUID(case_id),
        2,
        revision,
        selected,
        "b" * 64,
        "synthetic-overlay-security-token",
    )


def _assert_error(code: OverlayErrorCode, action: object) -> OverlayError:
    assert callable(action)
    with pytest.raises(OverlayError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_private_values_are_absent_from_commands_history_errors_reprs_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    overlay_id = UUID("6f000000-0000-4000-8000-000000000101")
    create = CreateCoreOverlayCommand(OverlayKind.DATE, PAGE_ID, BOX, PRIVATE_CANARY)
    history = OverlayCommandHistory.from_snapshot(_snapshot()).execute(
        create,
        id_factory=lambda: overlay_id,
    )
    replacement = ReplaceCoreOverlayPayloadCommand(
        overlay_id,
        TextualOverlayPayload(
            OverlayKind.DATE,
            PRIVATE_CANARY,
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
    )
    missing = ReplaceOverlayBoxCommand(
        UUID("6f000000-0000-4000-8000-000000000199"),
        BOX,
    )
    error = _assert_error(OverlayErrorCode.RECORD_UNKNOWN, lambda: history.execute(missing))

    rendered = " ".join(
        (
            repr(create),
            repr(replacement),
            repr(history),
            repr(history.overlays),
            repr(error),
            str(error),
            repr(error.args),
        )
    )
    assert PRIVATE_CANARY not in rendered
    assert "applicant.pdf" not in rendered
    assert PRIVATE_CANARY not in caplog.text
    assert not caplog.records


def test_identifier_factory_is_bounded_across_collisions_exceptions_and_bad_types() -> None:
    history = OverlayCommandHistory.from_snapshot(_snapshot())
    attempts = 0
    reserved = PAGE_ID

    def exhausted() -> UUID:
        nonlocal attempts
        attempts += 1
        if attempts % 3 == 0:
            raise RuntimeError(PRIVATE_CANARY)
        if attempts % 3 == 1:
            return reserved
        return cast(UUID, PRIVATE_CANARY)

    error = _assert_error(
        OverlayErrorCode.ID_GENERATION_FAILED,
        lambda: history.execute(
            CreateCoreOverlayCommand(OverlayKind.TEXT, PAGE_ID, BOX, PRIVATE_CANARY),
            id_factory=exhausted,
        ),
    )

    assert attempts == command_module.MAX_OVERLAY_ID_GENERATION_ATTEMPTS == 32
    assert PRIVATE_CANARY not in f"{error!r} {error} {error.args!r}"
    assert str(reserved) not in f"{error!r} {error}"


def test_history_command_capacity_is_bounded_without_mutating_prior_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command_module, "MAX_OVERLAY_COMMANDS", 2)
    history = OverlayCommandHistory.from_snapshot(_snapshot())
    overlay_id = UUID("60000000-0000-4000-8000-000000000001")
    first = history.execute(
        ReplaceOverlayBoxCommand(
            overlay_id,
            NormalizedPageBox(100_001, 100_000, 350_000, 50_000),
        )
    )
    second = first.execute(
        ReplaceOverlayBoxCommand(
            overlay_id,
            NormalizedPageBox(100_002, 100_000, 350_000, 50_000),
        )
    )
    before = second.overlays

    _assert_error(
        OverlayErrorCode.CAPACITY_EXCEEDED,
        lambda: second.execute(
            ReplaceOverlayBoxCommand(
                overlay_id,
                NormalizedPageBox(100_003, 100_000, 350_000, 50_000),
            )
        ),
    )
    assert second.overlays == before
    assert first.overlays != second.overlays


def test_unknown_concurrent_private_overlay_state_is_sanitized_and_unchanged() -> None:
    origin = _snapshot()
    history = OverlayCommandHistory.from_snapshot(origin).execute(
        CreateCoreOverlayCommand(OverlayKind.TEXT, PAGE_ID, BOX, PRIVATE_CANARY),
        id_factory=lambda: UUID("6f000000-0000-4000-8000-000000000301"),
    )
    document = origin.document
    overlays = cast(list[dict[str, object]], document["overlays"])
    payload = cast(dict[str, object], overlays[0]["payload"])
    payload["value"] = PRIVATE_CANARY
    document["revision"] = origin.revision + 1
    current = _snapshot(document)
    before = current.document

    error = _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(current),
    )

    assert current.document == before
    assert PRIVATE_CANARY not in f"{error!r} {error} {error.args!r}"


def test_malformed_namespace_collision_fails_before_any_candidate_mutation() -> None:
    document = _document()
    overlays = cast(list[dict[str, object]], document["overlays"])
    overlays[0]["overlay_id"] = str(PAGE_ID)
    snapshot = _snapshot(document)
    before = snapshot.document

    error = _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: OverlayCommandHistory.from_snapshot(snapshot),
    )

    assert snapshot.document == before
    assert str(PAGE_ID) not in f"{error!r} {error} {error.args!r}"
