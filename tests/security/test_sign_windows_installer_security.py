"""Production callback scope and unapproved-promotion rejection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.unit.test_windows_signing import unsigned_pe
from tools import sign_windows_installer as production
from tools import windows_signing as signing
from tools.release_staging import canonical_json


def test_callback_cannot_sign_a_payload_or_external_file_even_with_an_authorized_model_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "output/pws0802/candidate"
    candidate.mkdir(parents=True)
    context = candidate / "context.json"
    context.write_bytes(canonical_json({"candidate": str(candidate)}))
    digest = hashlib.sha256(context.read_bytes()).hexdigest()
    monkeypatch.setattr(production, "_authorise", lambda *_a: (object(), {}, {}))
    monkeypatch.setattr(
        signing, "sign", lambda *_a, **_k: pytest.fail("unauthorized file reached signer")
    )
    for target in (candidate / "payload/app.exe", tmp_path / "outside.exe"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(unsigned_pe())
        with pytest.raises(signing.WindowsSigningError, match="SIGNING_CALLBACK_SCOPE"):
            production.callback(context, digest, target, root=tmp_path)
        assert target.read_bytes() == unsigned_pe()


def test_callback_context_change_is_refused_before_any_signing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "context.json"
    context.write_bytes(canonical_json({"schema_version": 1}))
    monkeypatch.setattr(
        production, "_authorise", lambda *_a: pytest.fail("changed context was trusted")
    )
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_CONTEXT"):
        production.callback(context, "a" * 64, tmp_path / "private-canary.exe", root=tmp_path)


def test_cli_rejects_private_credential_arguments_without_echoing_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert production.main(["build", "--pfx-password", "private-secret-canary"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR [SIGNING_ARGUMENTS] Installer signing could not be verified.\n"
