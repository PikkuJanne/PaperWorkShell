"""Fixed-size, launch-only bootstrap grant for the document worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from struct import Struct
from typing import Final
from uuid import UUID

from paperworkshell.worker.protocol.models import SessionCapability

BOOTSTRAP_MAGIC: Final = b"PWSWRK1\x00"
BOOTSTRAP_VERSION: Final = 1
_BOOTSTRAP_STRUCT: Final = Struct(">8sI16s16s16s32s")
BOOTSTRAP_BYTES: Final = _BOOTSTRAP_STRUCT.size


class BootstrapErrorCode(StrEnum):
    """Stable reasons that a launch grant is rejected."""

    INVALID_SIZE = "WORKER_BOOTSTRAP_INVALID_SIZE"
    INVALID_MAGIC = "WORKER_BOOTSTRAP_INVALID_MAGIC"
    UNSUPPORTED_VERSION = "WORKER_BOOTSTRAP_UNSUPPORTED_VERSION"
    INVALID_IDENTITY = "WORKER_BOOTSTRAP_INVALID_IDENTITY"
    INVALID_CAPABILITY = "WORKER_BOOTSTRAP_INVALID_CAPABILITY"


@dataclass(slots=True)
class BootstrapError(ValueError):
    """Sanitized bootstrap failure that never includes launch data.

    Exception traceback, cause, and context attributes are interpreter-managed,
    so exception instances cannot use frozen-dataclass assignment guards.
    """

    code: BootstrapErrorCode
    safe_message: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.safe_message}"


@dataclass(frozen=True, slots=True)
class BootstrapGrant:
    """The identities and secret bound to exactly one worker launch."""

    session_id: UUID
    worker_instance_id: UUID
    case_id: UUID
    session_capability: SessionCapability = field(repr=False)

    def __post_init__(self) -> None:
        if any(type(value) is not UUID or value.version != 4 for value in self.identities):
            raise BootstrapError(
                BootstrapErrorCode.INVALID_IDENTITY,
                "bootstrap identities must be canonical UUIDv4 values",
            )
        if type(self.session_capability) is not SessionCapability:
            raise BootstrapError(
                BootstrapErrorCode.INVALID_CAPABILITY,
                "bootstrap capability is invalid",
            )

    @property
    def identities(self) -> tuple[UUID, UUID, UUID]:
        """Return non-secret launch identities in wire order."""

        return self.session_id, self.worker_instance_id, self.case_id


def encode_bootstrap_grant(grant: BootstrapGrant) -> bytes:
    """Encode a bootstrap grant into its exact, non-extensible version-1 form."""

    if type(grant) is not BootstrapGrant:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_IDENTITY,
            "a validated bootstrap grant is required",
        )
    try:
        capability = bytes.fromhex(grant.session_capability.value)
    except ValueError:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_CAPABILITY,
            "bootstrap capability is invalid",
        ) from None
    if len(capability) != 32:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_CAPABILITY,
            "bootstrap capability is invalid",
        )
    return _BOOTSTRAP_STRUCT.pack(
        BOOTSTRAP_MAGIC,
        BOOTSTRAP_VERSION,
        grant.session_id.bytes,
        grant.worker_instance_id.bytes,
        grant.case_id.bytes,
        capability,
    )


def decode_bootstrap_grant(raw: bytes) -> BootstrapGrant:
    """Decode one exact bootstrap grant and reject every variant fail-closed."""

    if type(raw) is not bytes or len(raw) != BOOTSTRAP_BYTES:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_SIZE,
            "bootstrap grant has an invalid size",
        )
    magic, version, session, worker, case, capability = _BOOTSTRAP_STRUCT.unpack(raw)
    if magic != BOOTSTRAP_MAGIC:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_MAGIC,
            "bootstrap grant identifier is invalid",
        )
    if version != BOOTSTRAP_VERSION:
        raise BootstrapError(
            BootstrapErrorCode.UNSUPPORTED_VERSION,
            "bootstrap grant version is unsupported",
        )
    try:
        grant = BootstrapGrant(
            session_id=UUID(bytes=session),
            worker_instance_id=UUID(bytes=worker),
            case_id=UUID(bytes=case),
            session_capability=SessionCapability(capability.hex()),
        )
    except BootstrapError, ValueError:
        raise BootstrapError(
            BootstrapErrorCode.INVALID_IDENTITY,
            "bootstrap grant identity is invalid",
        ) from None
    return grant
