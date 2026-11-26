"""Native Windows verification of offline update-key authorization and revocation."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import (
    SIGNATURE_PREFIX,
    UpdateErrorCode,
    UpdateVerificationError,
    verify_update_bundle,
)
from paperworkshell.storage.update_trust import (
    MAX_TRUST_RECORD_BYTES,
    TRUST_SIGNATURE_PREFIX,
    ChannelTrust,
    TrustAnchor,
    TrustCheckpoint,
    TrustErrorCode,
    UpdateTrustError,
    verify_channel_trust,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows CNG provider")

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
INITIAL_CHECKPOINT = TrustCheckpoint()


def _public_key(private: ec.EllipticCurvePrivateKey) -> bytes:
    numbers = private.public_key().public_numbers()
    return numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def _sign(private: ec.EllipticCurvePrivateKey, message: bytes) -> bytes:
    r, s = decode_dss_signature(private.sign(message, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


@pytest.fixture
def root_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def metadata_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _record(key: ec.EllipticCurvePrivateKey | None = None, /, **changes: object) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "application_id": INSTALLED_APPLICATION_ID,
            "platform": "win-x64",
            "channel": "engineering",
            "epoch": 1,
            "issued_at": "2026-11-26T11:00:00Z",
            "expires_at": "2026-11-26T13:00:00Z",
            "release_sequence_floor": 5,
            "metadata_public_key": None if key is None else _public_key(key).hex(),
            **changes,
        },
    ).encode("utf-8")


def _verify(
    record: bytes,
    root_key: ec.EllipticCurvePrivateKey,
    *,
    signature: bytes | None = None,
    checkpoint: TrustCheckpoint = INITIAL_CHECKPOINT,
    minimum_epoch: int = 1,
) -> ChannelTrust:
    if signature is None:
        signature = _sign(root_key, TRUST_SIGNATURE_PREFIX + record)
    return verify_channel_trust(
        record,
        signature,
        anchor=TrustAnchor("engineering", _public_key(root_key), minimum_epoch),
        checkpoint=checkpoint,
        now=NOW,
    )


def test_native_verified_delegation_checks_the_actual_update_bundle(
    root_key: ec.EllipticCurvePrivateKey, metadata_key: ec.EllipticCurvePrivateKey
) -> None:
    package = b"synthetic offline update bytes"
    metadata = json.dumps(
        {
            "schema_version": 1,
            "application_id": INSTALLED_APPLICATION_ID,
            "platform": "win-x64",
            "channel": "engineering",
            "version": "0.0.0",
            "candidate_id": "pws0803-test",
            "sequence": 6,
            "issued_at": "2026-11-26T11:00:00Z",
            "expires_at": "2026-11-26T13:00:00Z",
            "package_size": len(package),
            "package_sha256": hashlib.sha256(package).hexdigest(),
        },
    ).encode("utf-8")
    record = _record(metadata_key)
    trust = _verify(record, root_key)
    assert trust.checkpoint == TrustCheckpoint(1, hashlib.sha256(record).hexdigest())
    assert _verify(record, root_key, checkpoint=trust.checkpoint) == trust
    assert not trust.revoked
    assert trust.release_sequence_floor == 5
    assert (
        verify_update_bundle(
            metadata,
            _sign(metadata_key, SIGNATURE_PREFIX + metadata),
            io.BytesIO(package),
            verify_signature=trust.permit_update_signature,
            expected_channel=trust.channel,
            sequence_floor=trust.release_sequence_floor,
            now=NOW,
        ).sequence
        == 6
    )
    with pytest.raises(UpdateVerificationError) as rejected:
        verify_update_bundle(
            metadata,
            _sign(root_key, SIGNATURE_PREFIX + metadata),
            io.BytesIO(package),
            verify_signature=trust.permit_update_signature,
            expected_channel=trust.channel,
            sequence_floor=trust.release_sequence_floor,
            now=NOW,
        )
    assert rejected.value.code == UpdateErrorCode.SIGNATURE_INVALID


def test_compromised_metadata_key_cannot_replace_its_own_authority(
    root_key: ec.EllipticCurvePrivateKey, metadata_key: ec.EllipticCurvePrivateKey
) -> None:
    replacement = _record(metadata_key, epoch=2)
    for signature in (
        b"",
        _sign(metadata_key, TRUST_SIGNATURE_PREFIX + replacement),
        _sign(root_key, SIGNATURE_PREFIX + replacement),
        _sign(root_key, TRUST_SIGNATURE_PREFIX + replacement + b"\n"),
    ):
        with pytest.raises(UpdateTrustError) as rejected:
            _verify(replacement, root_key, signature=signature)
        assert rejected.value.code == TrustErrorCode.SIGNATURE_INVALID


def test_rotation_and_revocation_reject_the_prior_metadata_key(
    root_key: ec.EllipticCurvePrivateKey, metadata_key: ec.EllipticCurvePrivateKey
) -> None:
    prior = _verify(_record(metadata_key), root_key)
    next_metadata_key = ec.generate_private_key(ec.SECP256R1())
    rotated = _verify(_record(next_metadata_key, epoch=2), root_key, checkpoint=prior.checkpoint)
    revoked = _verify(_record(epoch=3), root_key, checkpoint=rotated.checkpoint)
    message = SIGNATURE_PREFIX + b"signed update metadata"
    prior_signature = _sign(metadata_key, message)
    new_signature = _sign(next_metadata_key, message)
    assert prior.permit_update_signature(message, prior_signature)
    assert rotated.permit_update_signature(message, new_signature)
    assert not rotated.permit_update_signature(message, prior_signature)
    assert revoked.revoked
    assert not revoked.permit_update_signature(message, new_signature)
    assert not revoked.permit_update_signature(message, prior_signature)
    reenabled = _verify(
        _record(next_metadata_key, epoch=4), root_key, checkpoint=revoked.checkpoint
    )
    assert reenabled.permit_update_signature(message, new_signature)

    for record in (_record(metadata_key), _record(metadata_key, epoch=3)):
        with pytest.raises(UpdateTrustError) as rejected:
            _verify(record, root_key, checkpoint=revoked.checkpoint)
        assert rejected.value.code == TrustErrorCode.ROLLBACK_FORBIDDEN


def test_embedded_minimum_epoch_rejects_an_old_record_without_local_state(
    root_key: ec.EllipticCurvePrivateKey,
) -> None:
    with pytest.raises(UpdateTrustError) as rejected:
        _verify(_record(), root_key, minimum_epoch=2)
    assert rejected.value.code == TrustErrorCode.ROLLBACK_FORBIDDEN


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"application_id": "example.other"}, TrustErrorCode.TARGET_MISMATCH),
        ({"platform": "win-arm64"}, TrustErrorCode.TARGET_MISMATCH),
        ({"channel": "stable"}, TrustErrorCode.TARGET_MISMATCH),
        ({"epoch": True}, TrustErrorCode.RECORD_INVALID),
        ({"schema_version": 2}, TrustErrorCode.RECORD_INVALID),
        ({"release_sequence_floor": -1}, TrustErrorCode.RECORD_INVALID),
        ({"metadata_public_key": "00" * 64}, TrustErrorCode.RECORD_INVALID),
        ({"expires_at": "2026-11-26T12:00:00Z"}, TrustErrorCode.RECORD_NOT_CURRENT),
        ({"issued_at": "2026-11-26T12:00:01Z"}, TrustErrorCode.RECORD_NOT_CURRENT),
        ({"unexpected": "test-canary"}, TrustErrorCode.RECORD_INVALID),
    ],
)
def test_authentic_record_is_still_subject_to_target_scope_and_validation(
    root_key: ec.EllipticCurvePrivateKey, changes: dict[str, object], code: TrustErrorCode
) -> None:
    with pytest.raises(UpdateTrustError) as rejected:
        _verify(_record(**changes), root_key)
    assert rejected.value.code == code
    assert "test-canary" not in str(rejected.value)


def test_conflicting_fields_and_root_as_metadata_key_are_rejected(
    root_key: ec.EllipticCurvePrivateKey,
) -> None:
    for record in (
        _record(root_key),
        _record().replace(b'"epoch": 1', b'"epoch": 1, "epoch": 2'),
        _record() + b" " * MAX_TRUST_RECORD_BYTES,
    ):
        with pytest.raises(UpdateTrustError) as rejected:
            _verify(record, root_key)
        assert rejected.value.code == TrustErrorCode.RECORD_INVALID
