"""Public attribution exceptions never authorize fixture or new personal data."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools import validate_synthetic_fixtures as validator
from tools._reviewed_upstream_notices import REVIEWED_UPSTREAM_NOTICE_EMAIL_DIGESTS


def _notice() -> bytes:
    # Deliberately assembled negative-test contact, never a repository literal.
    return b"Synthetic upstream attribution\nContact: " + b"maintainer" + b"@" + b"vendor.org\n"


def _review(monkeypatch: pytest.MonkeyPatch, content: bytes) -> None:
    monkeypatch.setattr(
        validator,
        "_REVIEWED_UPSTREAM_NOTICE_EMAIL_DIGESTS",
        frozenset({hashlib.sha256(content).digest()}),
        raising=False,
    )


def test_exact_reviewed_notice_is_allowed_only_in_repository_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _notice()
    _review(monkeypatch, content)
    assert validator._contains_prohibited_pattern(content)
    assert not validator._contains_prohibited_pattern(content, reviewed_source_notice=True)
    validator._repository_inspector(frozenset())(content)


@pytest.mark.parametrize("change", ["newline", "crlf", "new-contact", "other-text"])
def test_any_changed_notice_requires_fresh_exact_review(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    content = _notice()
    _review(monkeypatch, content)
    changed = {
        "newline": content + b"\n",
        "crlf": content.replace(b"\n", b"\r\n"),
        "new-contact": content + b"applicant" + b"@" + b"private.org\n",
        "other-text": b"Unreviewed record: " + content,
    }[change]
    assert validator._contains_prohibited_pattern(changed, reviewed_source_notice=True)
    with pytest.raises(validator.RepositoryPrivacyFinding):
        validator._repository_inspector(frozenset())(changed)


@pytest.mark.parametrize("kind", ["ssn", "pan", "iban", "mrz"])
def test_other_identity_patterns_are_never_exempted(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    patterns = {
        "ssn": b"123" + b"-" + b"45" + b"-" + b"6789",
        "pan": b"4111" + b"1111" + b"1111" + b"1111",
        "iban": b"DE89" + b"3704" + b"0044" + b"0532" + b"0130" + b"00",
        "mrz": b"P<" + b"UTO" + b"A" * 25,
    }
    content = _notice() + patterns[kind] + b"\n"
    _review(monkeypatch, content)
    assert validator._contains_prohibited_pattern(content, reviewed_source_notice=True)
    with pytest.raises(validator.RepositoryPrivacyFinding):
        validator._repository_inspector(frozenset())(content)


def test_document_identity_gate_is_not_an_attribution_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = bytes((37, 80, 68, 70, 45)) + b"1.7\n" + _notice()
    _review(monkeypatch, content)
    with pytest.raises(validator.RepositoryPrivacyFinding) as raised:
        validator._repository_inspector(frozenset())(content)
    assert raised.value.code == "FIXTURE_UNINDEXED_DOCUMENT"
    # The ordinary fixture-content predicate never opts into source exceptions.
    assert validator._contains_prohibited_pattern(content)


def test_real_reviewed_public_attribution_keeps_original_bytes() -> None:
    root = Path(__file__).resolve().parents[2]
    selected = None
    for path in sorted((root / "packaging/licenses").glob("release-*/**/*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).digest() in REVIEWED_UPSTREAM_NOTICE_EMAIL_DIGESTS:
            selected = path, content
            break
    assert selected is not None
    path, content = selected
    assert validator._contains_prohibited_pattern(content)
    validator._repository_inspector(frozenset())(content)
    assert path.read_bytes() == content
