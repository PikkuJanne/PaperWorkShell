"""Pure synthetic path/receipt/failure probes; no Microsoft binaries are used."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers.microsoft_runtime import change_receipt, fixture
from tools import prepare_windows_microsoft_runtime as tool


@pytest.mark.parametrize(
    "name",
    (
        "../outside.bin",
        "/outside.bin",
        "C:/Windows/System32/file.dll",
        "folder\\file.bin",
        "file:stream",
        "folder/../file",
        "NUL.bin",
    ),
)
def test_acquisition_paths_cannot_escape_or_name_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    assert inputs.community_copy_receipt is not None
    document = json.loads(inputs.community_copy_receipt.read_bytes())
    document["artifacts"][0]["path"] = name
    inputs = change_receipt(inputs, "community_copy_receipt", "artifacts", document["artifacts"])
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


def test_hardlinked_member_is_rejected_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    member = inputs.inspection_root / tool.MEMBERS[0].source
    outside = tmp_path / "synthetic-outside.bin"
    os.link(member, outside)
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_PATH_UNSAFE"):
        tool.prepare(inputs)
    assert not inputs.output.exists() and outside.exists()


def test_receipt_stream_path_is_rejected_without_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    assert inputs.owner_acceptance is not None
    stream = inputs.owner_acceptance.with_name(inputs.owner_acceptance.name + ":stream")
    with pytest.raises(tool.PreparationError, match="MICROSOFT_RUNTIME_PATH_UNSAFE"):
        tool.prepare(replace(inputs, owner_acceptance=stream))
    assert not inputs.output.exists()


def test_partial_write_failure_cleans_only_new_owned_identities_and_sanitizes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    original = tool._write
    calls = 0

    def interrupted(path: Path, payload: bytes, created: dict[Path, tuple[int, int]]) -> None:
        nonlocal calls
        original(path, payload, created)
        calls += 1
        if calls == 2:
            raise PermissionError("C:\\PRIVATE-CANARY\\outside")

    monkeypatch.setattr(tool, "_write", interrupted)
    with pytest.raises(tool.PreparationError) as error:
        tool.prepare(inputs)
    assert not inputs.output.exists()
    assert "PRIVATE-CANARY" not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_failure_preserves_unknown_new_directory_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    original = tool._write

    def interrupted(path: Path, payload: bytes, created: dict[Path, tuple[int, int]]) -> None:
        original(path, payload, created)
        (inputs.output / "unrelated.txt").write_bytes(b"unrelated synthetic sentinel")
        raise OSError("synthetic failure")

    monkeypatch.setattr(tool, "_write", interrupted)
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert (inputs.output / "unrelated.txt").read_bytes() == b"unrelated synthetic sentinel"
    assert not any((inputs.output / "runtime").glob("*.dll"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("path", "../outside.dll"),
        ("sha256", "0" * 64),
        ("canonical_member", "vcruntime140.dll"),
        ("filename_alias_only", False),
        ("filename_alias_only", 1),
        ("historical_notices_preserved", False),
    ],
)
def test_new_alias_cannot_escape_or_claim_different_source_or_remove_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: object
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    assert inputs.recipient_terms_review is not None
    review = json.loads(inputs.recipient_terms_review.read_bytes())
    review["pikepdf_alias_source"][key] = value
    inputs = change_receipt(
        inputs, "recipient_terms_review", "pikepdf_alias_source", review["pikepdf_alias_source"]
    )
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


@pytest.mark.parametrize("change", ["extra-file", "extra-directory", "alias-bytes", "receipt-path"])
def test_builder_gate_rejects_tampered_overlay_without_repair_or_other_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    tool.prepare(inputs)
    receipt = inputs.output / "overlay-receipt.json"
    expected = tool._digest(receipt.read_bytes())
    if change == "extra-file":
        (inputs.output / "runtime/unknown.dll").write_bytes(b"synthetic unexpected object")
    elif change == "extra-directory":
        (inputs.output / "unknown").mkdir()
    elif change == "alias-bytes":
        alias = inputs.output / tool.PIKEPDF_ALIAS
        alias.write_bytes(b"x" * alias.stat().st_size)
    else:
        report = json.loads(receipt.read_bytes())
        report["preparation_inputs"]["owner_acceptance"] = "../outside.json"
        receipt.write_text(json.dumps(report), encoding="utf-8")
        expected = tool._digest(receipt.read_bytes())
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(tool.PreparationError):
        tool.verify_overlay(inputs.output, expected)
    assert all(path.read_bytes() == payload for path, payload in originals.items())


def test_alias_write_failure_cleans_all_new_nested_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    original = tool._write

    def interrupted(path: Path, payload: bytes, created: dict[Path, tuple[int, int]]) -> None:
        original(path, payload, created)
        if path == inputs.output / tool.PIKEPDF_ALIAS:
            raise PermissionError("synthetic alias publication failure")

    monkeypatch.setattr(tool, "_write", interrupted)
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()


@pytest.mark.parametrize("change", ["missing", "outside", "hash"])
def test_qpdf_adjacency_review_requires_exact_three_acquired_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    inputs = fixture(tmp_path, monkeypatch, review_version=2)
    members = tool._qpdf_members()
    if change == "missing":
        members.pop()
    elif change == "outside":
        members[0]["path"] = "../outside.dll"
    else:
        members[0]["sha256"] = "0" * 64
    inputs = change_receipt(inputs, "recipient_terms_review", "qpdf_app_local_members", members)
    with pytest.raises(tool.PreparationError):
        tool.prepare(inputs)
    assert not inputs.output.exists()
