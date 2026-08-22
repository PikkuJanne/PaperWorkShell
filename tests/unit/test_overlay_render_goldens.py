"""Approval-boundary tests for the PWS-0303 overlay golden harness."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import tools.overlay_render_goldens as golden_tool


def _approval() -> golden_tool.GoldenApproval:
    return golden_tool.GoldenApproval(
        golden_tool.APPROVED_AT,
        golden_tool.APPROVAL_REVIEWER_ROLE,
        golden_tool.APPROVAL_RATIONALE,
    )


def test_candidate_index_requires_review_before_promotion() -> None:
    artifacts = golden_tool.build_artifacts()

    candidate = golden_tool.candidate_index(artifacts)
    approved = golden_tool.approved_index(artifacts)

    assert candidate["approval"] == {"status": "candidate-requires-human-review"}
    assert approved["approval"] == {
        "status": "approved",
        "reviewed_at": "2026-08-22",
        "reviewer_role": "implementation-agent",
        "rationale": "corrected-pws-0303-rendering-baseline-after-audit",
    }


@pytest.mark.parametrize(
    ("reviewed_at", "reviewer_role", "rationale"),
    (
        ("2026-08-21", golden_tool.APPROVAL_REVIEWER_ROLE, golden_tool.APPROVAL_RATIONALE),
        (golden_tool.APPROVED_AT, "unreviewed", golden_tool.APPROVAL_RATIONALE),
        (golden_tool.APPROVED_AT, golden_tool.APPROVAL_REVIEWER_ROLE, "arbitrary"),
    ),
)
def test_invalid_approval_record_is_rejected(
    reviewed_at: str,
    reviewer_role: str,
    rationale: str,
) -> None:
    with pytest.raises(ValueError, match="approval record is invalid"):
        golden_tool.GoldenApproval(reviewed_at, reviewer_role, rationale)


def test_candidate_promotion_rebuilds_an_approved_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    destination = tmp_path / "approved"
    golden_tool._write_candidate(candidate)
    staged = json.loads((candidate / "index.json").read_text(encoding="utf-8"))
    assert staged["approval"] == {"status": "candidate-requires-human-review"}
    monkeypatch.setattr(golden_tool, "GOLDEN_ROOT", destination)

    golden_tool._approve_candidate(candidate, _approval())

    promoted = json.loads((destination / "index.json").read_text(encoding="utf-8"))
    assert promoted["approval"]["status"] == "approved"
    assert not any(path.suffix == ".pdf" for path in destination.iterdir())


def test_oversized_or_same_size_tampered_candidate_file_is_rejected(tmp_path: Path) -> None:
    candidate_file = tmp_path / "candidate.bin"
    candidate_file.write_bytes(b"expected-extra")

    with pytest.raises(ValueError, match="candidate file is unavailable, changed, or unsafe"):
        golden_tool._require_exact_candidate_file(candidate_file, b"expected")

    candidate_file.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="candidate file is unavailable, changed, or unsafe"):
        golden_tool._require_exact_candidate_file(candidate_file, b"expected")


def test_hardlinked_candidate_file_is_rejected(tmp_path: Path) -> None:
    candidate_file = tmp_path / "candidate.bin"
    second_name = tmp_path / "second.bin"
    candidate_file.write_bytes(b"expected")
    os.link(candidate_file, second_name)

    with pytest.raises(ValueError, match="candidate file is unavailable, changed, or unsafe"):
        golden_tool._require_exact_candidate_file(candidate_file, b"expected")


def test_candidate_inventory_stops_at_the_first_untrusted_excess_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_names = {"index.json", "reference.pdf", "reference.png"}
    consumed: list[str] = []

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    def untrusted_entries() -> Iterator[_Entry]:
        for name in (*sorted(expected_names), "unexpected.bin"):
            consumed.append(name)
            yield _Entry(name)
        raise AssertionError("candidate inventory enumeration was not bounded")

    @contextmanager
    def untrusted_scandir(_path: object) -> Iterator[Iterator[_Entry]]:
        yield untrusted_entries()

    monkeypatch.setattr(os, "scandir", untrusted_scandir)

    with pytest.raises(ValueError, match="candidate inventory differs"):
        golden_tool._require_exact_candidate_inventory(tmp_path, expected_names)

    assert consumed == [*sorted(expected_names), "unexpected.bin"]
