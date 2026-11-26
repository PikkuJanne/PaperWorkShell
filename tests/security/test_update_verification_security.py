"""Isolated signing fixtures for the read-only update admission boundary."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import BinaryIO

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from paperworkshell.app.installed_runtime import INSTALLED_APPLICATION_ID
from paperworkshell.app.update_verification import (
    MAX_METADATA_BYTES,
    SIGNATURE_PREFIX,
    UpdateErrorCode,
    UpdateMetadata,
    UpdateVerificationError,
    verify_update_bundle,
)

PACKAGE = b"synthetic update bytes, never executable"
NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _verifier(key: Ed25519PrivateKey) -> Callable[[bytes, bytes], bool]:
    public = key.public_key()

    def verify(message: bytes, signature: bytes) -> bool:
        try:
            public.verify(signature, message)
        except InvalidSignature:
            return False
        return True

    return verify


def _metadata(**changes: object) -> bytes:
    payload = {
        "schema_version": 1,
        "application_id": INSTALLED_APPLICATION_ID,
        "platform": "win-x64",
        "channel": "engineering",
        "version": "1.2.3",
        "candidate_id": "pws0803-test-r2",
        "sequence": 2,
        "issued_at": "2026-11-26T11:00:00Z",
        "expires_at": "2026-11-26T13:00:00Z",
        "package_size": len(PACKAGE),
        "package_sha256": hashlib.sha256(PACKAGE).hexdigest(),
        **changes,
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _verify(
    raw: bytes,
    key: Ed25519PrivateKey,
    package: BinaryIO,
    *,
    signature: bytes | None = None,
    sequence_floor: int = 1,
) -> UpdateMetadata:
    if signature is None:
        signature = key.sign(SIGNATURE_PREFIX + raw)
    return verify_update_bundle(
        raw,
        signature,
        package,
        verify_signature=_verifier(key),
        expected_channel="engineering",
        sequence_floor=sequence_floor,
        now=NOW,
    )


class _MustNotRead(io.BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        pytest.fail("package was read before metadata trust and policy checks completed")


class _ShortReads(io.BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        assert size is not None and 0 < size <= len(PACKAGE) + 1
        return super().read(min(size, 3))


def test_exact_signed_bytes_and_bounded_short_package_reads(key: Ed25519PrivateKey) -> None:
    assert SIGNATURE_PREFIX == b"PaperWorkShell update metadata v1\x00"
    raw = _metadata()
    package = _ShortReads(PACKAGE)

    accepted = _verify(raw, key, package)

    assert accepted.channel == "engineering"
    assert accepted.version == "1.2.3"
    assert accepted.candidate_id == "pws0803-test-r2"
    assert accepted.sequence == 2
    assert accepted.issued_at == datetime(2026, 11, 26, 11, tzinfo=UTC)
    assert accepted.expires_at == datetime(2026, 11, 26, 13, tzinfo=UTC)
    assert accepted.package_size == len(PACKAGE)
    assert accepted.package_sha256 == hashlib.sha256(PACKAGE).hexdigest()
    assert not package.closed


def test_missing_wrong_key_and_wrong_message_signatures_fail_before_package_read(
    key: Ed25519PrivateKey,
) -> None:
    raw = _metadata()
    wrong_key = Ed25519PrivateKey.generate()
    for payload, signature in (
        (raw, b""),
        (raw, wrong_key.sign(SIGNATURE_PREFIX + raw)),
        (raw, key.sign(raw)),
        (raw + b"\n", key.sign(SIGNATURE_PREFIX + raw)),
        (b"invalid JSON", bytes(64)),
    ):
        with pytest.raises(UpdateVerificationError) as caught:
            _verify(payload, key, _MustNotRead(), signature=signature)
        assert caught.value.code == UpdateErrorCode.SIGNATURE_INVALID


@pytest.mark.parametrize("raw", [b"", b" " * (MAX_METADATA_BYTES + 1)])
def test_metadata_size_limit_applies_before_signature_verification(raw: bytes) -> None:
    def must_not_verify(message: bytes, signature: bytes) -> bool:
        pytest.fail("signature verification was called for empty or oversized metadata")

    with pytest.raises(UpdateVerificationError) as caught:
        verify_update_bundle(
            raw,
            bytes(64),
            _MustNotRead(),
            verify_signature=must_not_verify,
            expected_channel="engineering",
            sequence_floor=1,
            now=NOW,
        )
    assert caught.value.code == UpdateErrorCode.METADATA_INVALID


@pytest.mark.parametrize(
    "raw",
    [
        b"{\x80}",
        _metadata() + b"{}",
        _metadata().replace(b'"sequence": 2', b'"sequence": 2, "sequence": 2'),
        _metadata(schema_version=True),
        _metadata(schema_version=2),
        _metadata(sequence=True),
        _metadata(package_size=2**63),
        _metadata(version="01.2.3"),
        _metadata(candidate_id="../private-path-canary"),
        _metadata(package_sha256=hashlib.sha256(PACKAGE).hexdigest().upper()),
        _metadata(expires_at="2026-11-26T13:00:00+00:00"),
        _metadata(unexpected="private-path-canary"),
    ],
)
def test_signed_invalid_metadata_is_rejected_without_exposing_it(
    key: Ed25519PrivateKey, raw: bytes
) -> None:
    with pytest.raises(UpdateVerificationError) as caught:
        _verify(raw, key, _MustNotRead())
    assert caught.value.code == UpdateErrorCode.METADATA_INVALID
    assert "private-path-canary" not in str(caught.value)


@pytest.mark.parametrize(
    ("changes", "failure"),
    [
        ({"application_id": "io.example.other"}, UpdateErrorCode.TARGET_MISMATCH),
        ({"platform": "win-arm64"}, UpdateErrorCode.TARGET_MISMATCH),
        ({"channel": "stable"}, UpdateErrorCode.TARGET_MISMATCH),
        ({"issued_at": "2026-11-26T12:00:01Z"}, UpdateErrorCode.RELEASE_NOT_CURRENT),
        ({"expires_at": "2026-11-26T12:00:00Z"}, UpdateErrorCode.RELEASE_NOT_CURRENT),
        ({"sequence": 1}, UpdateErrorCode.ROLLBACK_FORBIDDEN),
    ],
)
def test_authenticated_metadata_still_must_meet_target_freshness_and_rollback_policy(
    key: Ed25519PrivateKey, changes: dict[str, object], failure: UpdateErrorCode
) -> None:
    with pytest.raises(UpdateVerificationError) as caught:
        _verify(_metadata(**changes), key, _MustNotRead())
    assert caught.value.code == failure


@pytest.mark.parametrize("package", [PACKAGE[:-1], PACKAGE + b"!", b"?" + PACKAGE[1:]])
def test_verified_metadata_does_not_admit_changed_package_bytes(
    key: Ed25519PrivateKey, package: bytes
) -> None:
    with pytest.raises(UpdateVerificationError) as caught:
        _verify(_metadata(), key, io.BytesIO(package))
    assert caught.value.code == UpdateErrorCode.PACKAGE_MISMATCH


def test_package_read_failure_has_a_stable_private_error(key: Ed25519PrivateKey) -> None:
    class BrokenPackage(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            if self.tell() == 0:
                return super().read(3)
            raise OSError("private-path-canary")

    package = BrokenPackage(PACKAGE)
    with pytest.raises(UpdateVerificationError) as caught:
        _verify(_metadata(), key, package)
    assert caught.value.code == UpdateErrorCode.PACKAGE_UNREADABLE
    assert "private-path-canary" not in str(caught.value)
    assert package.tell() == 3
