"""Signing never authorizes arbitrary paths, replacement images or weak proof."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.unit.test_windows_signing import fake_signed_pe, make_candidate, unsigned_pe
from tools import windows_signing as signing


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.exe",
        "/outside.exe",
        "C:/outside.exe",
        "x/../../outside.exe",
        "a:stream.exe",
        "app.exe.",
        "app.dll",
        "app.exe\n",
        "app.exe;command",
    ],
)
def test_signing_path_selection_fails_before_native_execution(
    tmp_path: Path, relative: str
) -> None:
    candidate, policy = make_candidate(tmp_path)
    outside = tmp_path / "outside.exe"
    outside.write_bytes(unsigned_pe())
    with pytest.raises(signing.WindowsSigningError):
        signing.prepare(candidate, [relative], policy, candidate / "request.json", root=tmp_path)
    assert outside.read_bytes() == unsigned_pe()
    assert not (candidate / "request.json").exists()


def test_parent_traversal_candidate_root_cannot_escape_the_owned_output_scope(
    tmp_path: Path,
) -> None:
    candidate, policy = make_candidate(tmp_path)
    disguised = candidate / ".." / ".." / ".." / "outside"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.exe").write_bytes(unsigned_pe())
    with pytest.raises(signing.WindowsSigningError):
        signing.prepare(disguised, ["app.exe"], policy, outside / "request.json", root=tmp_path)
    assert not (outside / "request.json").exists()


@pytest.mark.parametrize("selected", ["target", "policy"])
def test_hardlink_aliases_are_refused(tmp_path: Path, selected: str) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    target.write_bytes(unsigned_pe())
    if selected == "policy":
        target = tmp_path / "public-policy.json"
    os.link(target, tmp_path / "alias.bin")
    with pytest.raises(signing.WindowsSigningError):
        if selected == "target":
            signing.prepare(
                candidate, [target.name], policy, candidate / "request.json", root=tmp_path
            )
        else:
            signing.load_policy(target, hashlib.sha256(target.read_bytes()).hexdigest())


def test_signed_pe_overlay_and_multiple_certificate_records_are_refused(tmp_path: Path) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    target.write_bytes(fake_signed_pe(unsigned_pe()) + b"unverified-overlay")
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_PE"):
        signing.verify(candidate, target.name, policy, root=tmp_path)


def test_request_does_not_accept_a_new_image_under_an_unretained_hash(tmp_path: Path) -> None:
    candidate, policy = make_candidate(tmp_path)
    target, request = candidate / "app.exe", candidate / "request.json"
    target.write_bytes(unsigned_pe())
    receipt = signing.prepare(candidate, [target.name], policy, request, root=tmp_path)
    payload = json.loads(request.read_bytes())
    payload["files"][0]["image_sha256"] = "d" * 64
    request.write_text(json.dumps(payload), encoding="utf-8")
    signed = candidate / "signed.exe"
    signed.write_bytes(fake_signed_pe(unsigned_pe()))
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_REQUEST"):
        signing.ingest(
            candidate,
            target.name,
            signed.name,
            request,
            receipt["request_sha256"],
            policy,
            root=tmp_path,
        )
    assert target.read_bytes() == unsigned_pe()


def test_diagnostic_errors_do_not_echo_supplied_policy_path_or_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        signing.main(
            ["verify", "--policy", "private-path-canary", "--private-key", "private-secret-canary"]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR [SIGNING_ARGUMENTS] Windows signing could not be verified.\n"
    assert "private" not in captured.err
