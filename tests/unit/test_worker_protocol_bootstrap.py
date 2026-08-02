"""Tests for the fixed worker launch bootstrap grant."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.bootstrap import (
    BOOTSTRAP_BYTES,
    BootstrapError,
    BootstrapErrorCode,
    BootstrapGrant,
    decode_bootstrap_grant,
    encode_bootstrap_grant,
)
from paperworkshell.worker.protocol.models import SessionCapability


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _grant() -> BootstrapGrant:
    return BootstrapGrant(_uuid(1), _uuid(2), _uuid(3), SessionCapability("a" * 64))


def test_bootstrap_round_trip_is_exact_and_redacted() -> None:
    grant = _grant()
    encoded = encode_bootstrap_grant(grant)

    assert len(encoded) == BOOTSTRAP_BYTES
    assert decode_bootstrap_grant(encoded) == grant
    assert "a" * 64 not in repr(grant)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda raw: raw[:-1], BootstrapErrorCode.INVALID_SIZE),
        (lambda raw: b"X" + raw[1:], BootstrapErrorCode.INVALID_MAGIC),
        (
            lambda raw: raw[:8] + (2).to_bytes(4, "big") + raw[12:],
            BootstrapErrorCode.UNSUPPORTED_VERSION,
        ),
        (
            lambda raw: raw[:12] + bytes(16) + raw[28:],
            BootstrapErrorCode.INVALID_IDENTITY,
        ),
    ],
)
def test_bootstrap_variants_fail_closed_without_values(
    mutate: Callable[[bytes], bytes],
    expected: BootstrapErrorCode,
) -> None:
    raw = encode_bootstrap_grant(_grant())
    with pytest.raises(BootstrapError) as raised:
        decode_bootstrap_grant(mutate(raw))

    assert raised.value.code is expected
    assert "a" * 64 not in str(raised.value)
