"""Verify local update bytes without selecting, staging, or running an installer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import BinaryIO, Final, cast

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID

SIGNATURE_PREFIX: Final = b"PaperWorkShell update metadata v1\x00"
MAX_METADATA_BYTES: Final = 4096
MAX_PACKAGE_BYTES: Final = 1024 * 1024 * 1024
_MAX_SEQUENCE: Final = (1 << 63) - 1
_SIGNATURE_BYTES: Final = 64
_CHUNK_BYTES: Final = 1024 * 1024
_PLATFORM: Final = "win-x64"
_METADATA_KEYS: Final = frozenset(
    {
        "schema_version",
        "application_id",
        "platform",
        "channel",
        "version",
        "candidate_id",
        "sequence",
        "issued_at",
        "expires_at",
        "package_size",
        "package_sha256",
    }
)
_CHANNEL: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")
_VERSION: Final = re.compile(r"(?:0|[1-9][0-9]{0,3})(?:\.(?:0|[1-9][0-9]{0,3})){2}")
_CANDIDATE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,95}")
_UTC_TIME: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


class UpdateErrorCode(StrEnum):
    METADATA_INVALID = "UPDATE_METADATA_INVALID"
    SIGNATURE_INVALID = "UPDATE_SIGNATURE_INVALID"
    TARGET_MISMATCH = "UPDATE_TARGET_MISMATCH"
    RELEASE_NOT_CURRENT = "UPDATE_RELEASE_NOT_CURRENT"
    ROLLBACK_FORBIDDEN = "UPDATE_ROLLBACK_FORBIDDEN"
    PACKAGE_UNREADABLE = "UPDATE_PACKAGE_UNREADABLE"
    PACKAGE_MISMATCH = "UPDATE_PACKAGE_MISMATCH"


_MESSAGES: Final = {
    UpdateErrorCode.METADATA_INVALID: "The PaperWorkShell update information is invalid.",
    UpdateErrorCode.SIGNATURE_INVALID: "The PaperWorkShell update signature could not be verified.",
    UpdateErrorCode.TARGET_MISMATCH: "This update is for a different product, platform, or channel.",
    UpdateErrorCode.RELEASE_NOT_CURRENT: "The PaperWorkShell update information is outside its validity period.",
    UpdateErrorCode.ROLLBACK_FORBIDDEN: "This PaperWorkShell update is already installed or superseded.",
    UpdateErrorCode.PACKAGE_UNREADABLE: "The PaperWorkShell update package could not be read.",
    UpdateErrorCode.PACKAGE_MISMATCH: "The package does not match the signed update information.",
}


class UpdateVerificationError(ValueError):
    """Stable failure code and message with no metadata or local paths."""

    def __init__(self, code: UpdateErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class UpdateMetadata:
    channel: str
    version: str
    candidate_id: str
    sequence: int
    issued_at: datetime
    expires_at: datetime
    package_size: int
    package_sha256: str


def verify_update_bundle(
    metadata_bytes: bytes,
    signature: bytes,
    package: BinaryIO,
    *,
    verify_signature: Callable[[bytes, bytes], bool],
    expected_channel: str,
    sequence_floor: int,
    now: datetime,
) -> UpdateMetadata:
    """Verify signed metadata and read the exact package bytes from the current position.

    The caller owns the stream and its lifetime. The result identifies the verified
    bytes; it grants no authority to open a path or execute a package later.
    """
    metadata = verify_update_metadata(
        metadata_bytes,
        signature,
        verify_signature=verify_signature,
        expected_channel=expected_channel,
        sequence_floor=sequence_floor,
        now=now,
    )
    _verify_package(package, metadata)
    return metadata


def verify_update_metadata(
    metadata_bytes: bytes,
    signature: bytes,
    *,
    verify_signature: Callable[[bytes, bytes], bool],
    expected_channel: str,
    sequence_floor: int,
    now: datetime,
) -> UpdateMetadata:
    """Authenticate the offer before inspecting any package content.

    The caller supplies trusted policy and a verifier bound to trusted key
    material. It returns True only after verifying the domain-prefixed message
    and detached signature, and translates signature rejection to False.
    """
    if (
        _CHANNEL.fullmatch(expected_channel) is None
        or type(sequence_floor) is not int
        or not 0 <= sequence_floor <= _MAX_SEQUENCE
        or now.utcoffset() != timedelta(0)
    ):
        raise ValueError("Invalid trusted update policy.")
    if not 0 < len(metadata_bytes) <= MAX_METADATA_BYTES:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)
    if (
        len(signature) != _SIGNATURE_BYTES
        or verify_signature(SIGNATURE_PREFIX + metadata_bytes, signature) is not True
    ):
        raise UpdateVerificationError(UpdateErrorCode.SIGNATURE_INVALID)

    metadata = _parse_metadata(metadata_bytes, expected_channel)
    if not metadata.issued_at <= now < metadata.expires_at:
        raise UpdateVerificationError(UpdateErrorCode.RELEASE_NOT_CURRENT)
    if metadata.sequence <= sequence_floor:
        raise UpdateVerificationError(UpdateErrorCode.ROLLBACK_FORBIDDEN)

    return metadata


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate update field.")
        result[key] = value
    return result


def _parse_metadata(content: bytes, expected_channel: str) -> UpdateMetadata:
    try:
        parsed: object = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except ValueError, RecursionError:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID) from None
    if not isinstance(parsed, dict) or parsed.keys() != _METADATA_KEYS:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)
    payload = cast("dict[str, object]", parsed)
    schema = payload["schema_version"]
    if type(schema) is not int or schema != 1:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)

    channel = _matching_text(payload["channel"], _CHANNEL)
    if (
        payload["application_id"] != INSTALLED_APPLICATION_ID
        or payload["platform"] != _PLATFORM
        or channel != expected_channel
    ):
        raise UpdateVerificationError(UpdateErrorCode.TARGET_MISMATCH)

    issued_at = _utc_time(payload["issued_at"])
    expires_at = _utc_time(payload["expires_at"])
    if expires_at <= issued_at:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)

    return UpdateMetadata(
        channel=channel,
        version=_matching_text(payload["version"], _VERSION),
        candidate_id=_matching_text(payload["candidate_id"], _CANDIDATE),
        sequence=_positive_int(payload["sequence"], _MAX_SEQUENCE),
        issued_at=issued_at,
        expires_at=expires_at,
        package_size=_positive_int(payload["package_size"], MAX_PACKAGE_BYTES),
        package_sha256=_matching_text(payload["package_sha256"], _SHA256),
    )


def _matching_text(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)
    return value


def _utc_time(value: object) -> datetime:
    timestamp = _matching_text(value, _UTC_TIME)
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID) from None


def _positive_int(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise UpdateVerificationError(UpdateErrorCode.METADATA_INVALID)
    return value


def _verify_package(package: BinaryIO, metadata: UpdateMetadata) -> None:
    remaining = metadata.package_size
    digest = hashlib.sha256()
    while True:
        try:
            chunk = package.read(min(_CHUNK_BYTES, remaining + 1))
        except OSError, ValueError:
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_UNREADABLE) from None
        if type(chunk) is not bytes:
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_UNREADABLE)
        if not chunk:
            break
        if len(chunk) > remaining:
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_MISMATCH)
        remaining -= len(chunk)
        digest.update(chunk)

    if remaining or digest.hexdigest() != metadata.package_sha256:
        raise UpdateVerificationError(UpdateErrorCode.PACKAGE_MISMATCH)
