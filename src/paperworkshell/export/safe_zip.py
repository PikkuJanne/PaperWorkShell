"""Deterministic, bounded ZIP packaging for already-verified PDF streams.

The writer deliberately supports one narrow archive profile: a non-ZIP64,
uncompressed archive containing safe PDF leaf names.  It never owns or closes
the caller's streams and it never publishes a destination path.  A failed
call leaves the output stream untrusted; the caller must discard its stage.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import io
import re
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from typing import BinaryIO, Final, Never, Protocol, cast

from paperworkshell.domain.resource_policy import ResourcePolicy

SAFE_ZIP_POLICY_VERSION: Final = 1
SAFE_ZIP_REPORT_VERSION: Final = 1

_FIXED_DATE_TIME: Final = (1980, 1, 1, 0, 0, 0)
_FIXED_DOS_TIME: Final = 0
_FIXED_DOS_DATE: Final = 33
_DOS_ARCHIVE_ATTRIBUTE: Final = 0x20
_UTF8_FILENAME_FLAG: Final = 0x0800
_MAX_MEMBER_COUNT: Final = zipfile.ZIP_FILECOUNT_LIMIT - 1
_MAX_NON_ZIP64_BYTES: Final = zipfile.ZIP64_LIMIT
_MAX_FILENAME_UTF16_UNITS: Final = 180
_MIN_IO_CHUNK_BYTES: Final = 4 * 1024
_MAX_IO_CHUNK_BYTES: Final = 4 * 1024 * 1024

_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT_RE: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_WINDOWS_FORBIDDEN_CHARACTERS: Final = frozenset('<>:"/\\|?*%')
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)

_LOCAL_HEADER: Final = struct.Struct("<4s5H3L2H")
_CENTRAL_HEADER: Final = struct.Struct("<4s6H3L5H2L")
_END_OF_CENTRAL_DIRECTORY: Final = struct.Struct("<4s4H2LH")
_LOCAL_SIGNATURE: Final = b"PK\x03\x04"
_CENTRAL_SIGNATURE: Final = b"PK\x01\x02"
_END_SIGNATURE: Final = b"PK\x05\x06"


class CancellationProbe(Protocol):
    """Return ``True`` when the packaging operation should stop."""

    def __call__(self) -> bool: ...


class DeadlineProbe(Protocol):
    """Return ``True`` when the packaging deadline has expired."""

    def __call__(self) -> bool: ...


class SafeZipErrorCode(StrEnum):
    """Closed, non-disclosing failures from the safe ZIP boundary."""

    MODEL_INVALID = "SAFE_ZIP_MODEL_INVALID"
    NAME_INVALID = "SAFE_ZIP_NAME_INVALID"
    NAME_COLLISION = "SAFE_ZIP_NAME_COLLISION"
    LIMIT_EXCEEDED = "SAFE_ZIP_LIMIT_EXCEEDED"
    CANCELLED = "SAFE_ZIP_CANCELLED"
    TIMED_OUT = "SAFE_ZIP_TIMED_OUT"
    SOURCE_INVALID = "SAFE_ZIP_SOURCE_INVALID"
    SOURCE_MISMATCH = "SAFE_ZIP_SOURCE_MISMATCH"
    OUTPUT_INVALID = "SAFE_ZIP_OUTPUT_INVALID"
    WRITE_FAILED = "SAFE_ZIP_WRITE_FAILED"
    ARCHIVE_INVALID = "SAFE_ZIP_ARCHIVE_INVALID"
    VERIFICATION_FAILED = "SAFE_ZIP_VERIFICATION_FAILED"
    INTERNAL_ERROR = "SAFE_ZIP_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[SafeZipErrorCode, str]] = {
    SafeZipErrorCode.MODEL_INVALID: "PaperWorkShell received invalid ZIP packaging data.",
    SafeZipErrorCode.NAME_INVALID: "A ZIP member does not have a safe PDF filename.",
    SafeZipErrorCode.NAME_COLLISION: (
        "Two ZIP members would have the same filename on a case-insensitive system."
    ),
    SafeZipErrorCode.LIMIT_EXCEEDED: (
        "The ZIP package exceeds a configured resource or archive-format limit."
    ),
    SafeZipErrorCode.CANCELLED: "ZIP packaging was cancelled before completion.",
    SafeZipErrorCode.TIMED_OUT: "ZIP packaging exceeded its configured time limit.",
    SafeZipErrorCode.SOURCE_INVALID: "A verified PDF stream could not be read safely.",
    SafeZipErrorCode.SOURCE_MISMATCH: (
        "A verified PDF stream did not match its expected size and digest."
    ),
    SafeZipErrorCode.OUTPUT_INVALID: "The ZIP staging stream is not a safe empty binary stream.",
    SafeZipErrorCode.WRITE_FAILED: "PaperWorkShell could not write the ZIP stage safely.",
    SafeZipErrorCode.ARCHIVE_INVALID: (
        "The staged ZIP does not match the required safe archive structure."
    ),
    SafeZipErrorCode.VERIFICATION_FAILED: (
        "The staged ZIP member bytes did not pass verification."
    ),
    SafeZipErrorCode.INTERNAL_ERROR: "PaperWorkShell could not complete ZIP packaging safely.",
}


@dataclass(slots=True, repr=False)
class SafeZipError(ValueError):
    """Stable failure whose string and representation contain no member values."""

    code: SafeZipErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not SafeZipErrorCode:
            self.code = SafeZipErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_RE.fullmatch(self.context) is None:
            self.context = "safe_zip.internal"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"SafeZipError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_safe_zip(code: SafeZipErrorCode, context: str) -> Never:
    """Raise one sanitized ZIP error without retaining an exception chain."""

    error = SafeZipError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _raise_sanitized_copy(error: SafeZipError) -> Never:
    """Never trust a mutable exception instance received through a callback."""

    code = (
        error.code
        if type(error) is SafeZipError and type(error.code) is SafeZipErrorCode
        else SafeZipErrorCode.INTERNAL_ERROR
    )
    context = (
        error.context
        if type(error) is SafeZipError
        and type(error.context) is str
        and _CONTEXT_RE.fullmatch(error.context) is not None
        else "safe_zip.internal"
    )
    reject_safe_zip(code, context)


class SafeZipCheckCode(StrEnum):
    """Concrete checks completed before a ZIP report may be returned."""

    LAYOUT_VERIFIED = "SAFE_ZIP_LAYOUT_VERIFIED"
    ARCHIVE_REOPENED = "SAFE_ZIP_ARCHIVE_REOPENED"
    METADATA_VERIFIED = "SAFE_ZIP_METADATA_VERIFIED"
    MEMBER_ORDER_VERIFIED = "SAFE_ZIP_MEMBER_ORDER_VERIFIED"
    MEMBER_CONTENT_VERIFIED = "SAFE_ZIP_MEMBER_CONTENT_VERIFIED"
    ARCHIVE_STABILITY_VERIFIED = "SAFE_ZIP_ARCHIVE_STABILITY_VERIFIED"


_REQUIRED_CHECKS: Final = (
    SafeZipCheckCode.LAYOUT_VERIFIED,
    SafeZipCheckCode.ARCHIVE_REOPENED,
    SafeZipCheckCode.METADATA_VERIFIED,
    SafeZipCheckCode.MEMBER_ORDER_VERIFIED,
    SafeZipCheckCode.MEMBER_CONTENT_VERIFIED,
    SafeZipCheckCode.ARCHIVE_STABILITY_VERIFIED,
)


@dataclass(frozen=True, slots=True)
class SafeZipPolicy:
    """Typed ZIP budgets derived from the central resource policy."""

    maximum_members: int
    maximum_member_bytes: int
    maximum_total_member_bytes: int
    maximum_archive_bytes: int
    io_chunk_bytes: int
    policy_version: int = SAFE_ZIP_POLICY_VERSION

    def __post_init__(self) -> None:
        values = (
            self.maximum_members,
            self.maximum_member_bytes,
            self.maximum_total_member_bytes,
            self.maximum_archive_bytes,
            self.io_chunk_bytes,
            self.policy_version,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.policy")
        if (
            self.policy_version != SAFE_ZIP_POLICY_VERSION
            or self.maximum_members > _MAX_MEMBER_COUNT
            or self.maximum_member_bytes > _MAX_NON_ZIP64_BYTES
            or self.maximum_total_member_bytes > self.maximum_archive_bytes
            or self.maximum_archive_bytes > _MAX_NON_ZIP64_BYTES
            or self.maximum_member_bytes > self.maximum_total_member_bytes
            or not _MIN_IO_CHUNK_BYTES <= self.io_chunk_bytes <= _MAX_IO_CHUNK_BYTES
        ):
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.policy")

    @classmethod
    def from_resource_policy(cls, policy: ResourcePolicy) -> SafeZipPolicy:
        """Project central temporary-storage and I/O budgets into ZIP limits."""

        if type(policy) is not ResourcePolicy:
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.resource_policy")
        archive_limit = min(policy.maximum_temporary_storage_bytes, _MAX_NON_ZIP64_BYTES)
        return cls(
            maximum_members=min(policy.maximum_page_count, _MAX_MEMBER_COUNT),
            maximum_member_bytes=min(policy.maximum_source_bytes, archive_limit),
            maximum_total_member_bytes=archive_limit,
            maximum_archive_bytes=archive_limit,
            io_chunk_bytes=policy.import_io_chunk_bytes,
        )


@dataclass(frozen=True, slots=True, repr=False)
class SafeZipMember:
    """One already-verified PDF reader and its immutable expected facts."""

    filename: str = field(repr=False)
    reader: BinaryIO = field(repr=False, compare=False)
    expected_byte_size: int
    expected_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_safe_pdf_leaf_name(
            self.filename,
            code=SafeZipErrorCode.NAME_INVALID,
            context="safe_zip.member_name",
        )
        if (
            type(self.expected_byte_size) is not int
            or not 1 <= self.expected_byte_size <= _MAX_NON_ZIP64_BYTES
            or type(self.expected_sha256) is not str
            or _SHA256_RE.fullmatch(self.expected_sha256) is None
        ):
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.member")

    def __repr__(self) -> str:
        return f"SafeZipMember(expected_byte_size={self.expected_byte_size!r})"


@dataclass(frozen=True, slots=True, repr=False)
class SafeZipMemberFact:
    """One exact verified archive-member fact, with names and digests repr-hidden."""

    position: int
    filename: str = field(repr=False)
    byte_size: int
    sha256: str = field(repr=False)
    crc32: int = field(repr=False)

    def __post_init__(self) -> None:
        _require_safe_pdf_leaf_name(
            self.filename,
            code=SafeZipErrorCode.MODEL_INVALID,
            context="safe_zip.member_fact",
        )
        if (
            type(self.position) is not int
            or not 1 <= self.position <= _MAX_MEMBER_COUNT
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= _MAX_NON_ZIP64_BYTES
            or type(self.sha256) is not str
            or _SHA256_RE.fullmatch(self.sha256) is None
            or type(self.crc32) is not int
            or not 0 <= self.crc32 <= 0xFFFF_FFFF
        ):
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.member_fact")

    def __repr__(self) -> str:
        return f"SafeZipMemberFact(position={self.position!r}, byte_size={self.byte_size!r})"


@dataclass(frozen=True, slots=True, repr=False)
class SafeZipReport:
    """Immutable proof that one exact archive passed every required check."""

    archive_byte_size: int
    archive_sha256: str = field(repr=False)
    members: tuple[SafeZipMemberFact, ...] = field(repr=False)
    checks: tuple[SafeZipCheckCode, ...]
    report_version: int = SAFE_ZIP_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.archive_byte_size) is not int
            or not 1 <= self.archive_byte_size <= _MAX_NON_ZIP64_BYTES
            or type(self.archive_sha256) is not str
            or _SHA256_RE.fullmatch(self.archive_sha256) is None
            or type(self.members) is not tuple
            or not 1 <= len(self.members) <= _MAX_MEMBER_COUNT
            or any(type(member) is not SafeZipMemberFact for member in self.members)
            or tuple(member.position for member in self.members)
            != tuple(range(1, len(self.members) + 1))
            or len({member.filename.casefold() for member in self.members}) != len(self.members)
            or type(self.checks) is not tuple
            or self.checks != _REQUIRED_CHECKS
            or type(self.report_version) is not int
            or self.report_version != SAFE_ZIP_REPORT_VERSION
        ):
            reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.report")

    @property
    def member_count(self) -> int:
        return len(self.members)

    def __repr__(self) -> str:
        return (
            "SafeZipReport("
            f"archive_byte_size={self.archive_byte_size!r}, "
            f"member_count={len(self.members)!r}, check_count={len(self.checks)!r}, "
            f"report_version={self.report_version!r})"
        )


@dataclass(frozen=True, slots=True)
class _WrittenMember:
    fact: SafeZipMemberFact
    name_bytes: bytes
    flag_bits: int
    local_header_offset: int


class _OutputFault(Exception):
    pass


class _OutputLimitExceeded(Exception):
    pass


class _CheckedZipOutput:
    """Enforce exact writes and a maximum file extent beneath ``zipfile``."""

    __slots__ = ("_cancellation", "_deadline", "_maximum_bytes", "_stream")

    def __init__(
        self,
        stream: BinaryIO,
        maximum_bytes: int,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._cancellation = cancellation
        self._deadline = deadline

    def tell(self) -> int:
        try:
            position = self._stream.tell()
        except BaseException as error:
            raise _OutputFault from error
        if type(position) is not int or not 0 <= position <= self._maximum_bytes:
            raise _OutputFault
        return position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        _require_active(self._deadline, self._cancellation)
        try:
            position = self._stream.seek(offset, whence)
        except BaseException as error:
            raise _OutputFault from error
        if (
            type(position) is not int
            or not 0 <= position <= self._maximum_bytes
            or self.tell() != position
        ):
            raise _OutputFault
        return position

    def write(self, value: bytes | bytearray | memoryview) -> int:
        _require_active(self._deadline, self._cancellation)
        if not isinstance(value, bytes | bytearray | memoryview):
            raise _OutputFault
        position = self.tell()
        length = len(value)
        if position + length > self._maximum_bytes:
            raise _OutputLimitExceeded
        try:
            written = self._stream.write(value)
        except BaseException as error:
            raise _OutputFault from error
        if type(written) is not int or written != length or self.tell() != position + length:
            raise _OutputFault
        return written

    def flush(self) -> None:
        _require_active(self._deadline, self._cancellation)
        try:
            result = self._stream.flush()
        except BaseException as error:
            raise _OutputFault from error
        if result is not None:
            raise _OutputFault

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


class _CheckedZipInput:
    """Give ``zipfile`` fill reads while bounding every underlying request."""

    __slots__ = (
        "_cancellation",
        "_deadline",
        "_maximum_bytes",
        "_maximum_request_bytes",
        "_stream",
    )

    def __init__(
        self,
        stream: BinaryIO,
        *,
        maximum_bytes: int,
        maximum_request_bytes: int,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._maximum_request_bytes = maximum_request_bytes
        self._cancellation = cancellation
        self._deadline = deadline

    def tell(self) -> int:
        try:
            position = self._stream.tell()
        except BaseException as error:
            raise _OutputFault from error
        if type(position) is not int or not 0 <= position <= self._maximum_bytes:
            raise _OutputFault
        return position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        _require_active(self._deadline, self._cancellation)
        try:
            position = self._stream.seek(offset, whence)
        except BaseException as error:
            raise _OutputFault from error
        if (
            type(position) is not int
            or not 0 <= position <= self._maximum_bytes
            or self.tell() != position
        ):
            raise _OutputFault
        return position

    def read(self, size: int | None = -1) -> bytes:
        _require_active(self._deadline, self._cancellation)
        if type(size) is not int or not 0 <= size <= self._maximum_request_bytes:
            raise _OutputLimitExceeded
        collected = bytearray()
        while len(collected) < size:
            _require_active(self._deadline, self._cancellation)
            requested = min(self._maximum_request_bytes, size - len(collected))
            try:
                value = self._stream.read(requested)
            except BaseException as error:
                raise _OutputFault from error
            if type(value) is not bytes or len(value) > requested:
                raise _OutputFault
            if not value:
                break
            collected.extend(value)
        return bytes(collected)

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


def write_safe_zip(
    output: BinaryIO,
    members: tuple[SafeZipMember, ...],
    *,
    policy: SafeZipPolicy,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> SafeZipReport:
    """Write, reopen, and verify one deterministic archive without publishing it.

    The caller-provided tuple is the canonical package-plan order and is
    preserved exactly.  All input and output I/O uses
    ``policy.io_chunk_bytes`` or a smaller request.  The output must be an
    empty, readable, writable, seekable binary stream.
    """

    try:
        return _write_safe_zip(
            output,
            members,
            policy=policy,
            cancellation=cancellation,
            deadline=deadline,
        )
    except SafeZipError as error:
        _raise_sanitized_copy(error)
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.INTERNAL_ERROR, "safe_zip.internal")


def _write_safe_zip(
    output: BinaryIO,
    members: tuple[SafeZipMember, ...],
    *,
    policy: SafeZipPolicy,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> SafeZipReport:
    if (
        type(policy) is not SafeZipPolicy
        or type(members) is not tuple
        or not 1 <= len(members) <= policy.maximum_members
        or any(type(member) is not SafeZipMember for member in members)
        or not callable(cancellation)
        or not callable(deadline)
    ):
        reject_safe_zip(SafeZipErrorCode.MODEL_INVALID, "safe_zip.request")
    _require_active(deadline, cancellation)

    comparison_keys = tuple(member.filename.casefold() for member in members)
    if len(set(comparison_keys)) != len(comparison_keys):
        reject_safe_zip(SafeZipErrorCode.NAME_COLLISION, "safe_zip.member_names")
    canonical_members = members
    total_member_bytes = sum(member.expected_byte_size for member in canonical_members)
    if (
        any(member.expected_byte_size > policy.maximum_member_bytes for member in canonical_members)
        or total_member_bytes > policy.maximum_total_member_bytes
    ):
        reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.member_bytes")
    expected_archive_bytes = _expected_archive_size(canonical_members)
    if expected_archive_bytes > policy.maximum_archive_bytes:
        reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.archive_bytes")
    if _expected_central_directory_size(canonical_members) > policy.io_chunk_bytes:
        # The stdlib reader consumes the bounded central directory in one
        # request.  Keeping it within the policy chunk also bounds reopen I/O.
        reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.central_directory_bytes")

    _require_empty_output(output)
    for member in canonical_members:
        _require_active(deadline, cancellation)
        _require_member_reader(
            member,
            cancellation=cancellation,
            deadline=deadline,
        )
    _require_active(deadline, cancellation)

    written_members = _write_archive(
        output,
        canonical_members,
        policy=policy,
        cancellation=cancellation,
        deadline=deadline,
    )
    _flush_output(output)
    archive_byte_size = _output_size(output)
    if archive_byte_size != expected_archive_bytes:
        reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_size")
    initial_archive_sha256 = _hash_output(
        output,
        archive_byte_size,
        chunk_bytes=policy.io_chunk_bytes,
        cancellation=cancellation,
        deadline=deadline,
    )

    _verify_raw_layout(
        output,
        archive_byte_size,
        written_members,
        cancellation=cancellation,
        deadline=deadline,
    )
    _verify_members_with_zipfile(
        output,
        written_members,
        archive_byte_size=archive_byte_size,
        chunk_bytes=policy.io_chunk_bytes,
        cancellation=cancellation,
        deadline=deadline,
    )
    final_archive_sha256 = _hash_output(
        output,
        archive_byte_size,
        chunk_bytes=policy.io_chunk_bytes,
        cancellation=cancellation,
        deadline=deadline,
    )
    if not hmac.compare_digest(initial_archive_sha256, final_archive_sha256):
        reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_changed")
    _seek_output(output, 0, io.SEEK_SET, "safe_zip.output_rewind")
    return SafeZipReport(
        archive_byte_size=archive_byte_size,
        archive_sha256=final_archive_sha256,
        members=tuple(member.fact for member in written_members),
        checks=_REQUIRED_CHECKS,
    )


def _expected_archive_size(members: tuple[SafeZipMember, ...]) -> int:
    size = _END_OF_CENTRAL_DIRECTORY.size
    for member in members:
        name_bytes, _flag_bits = _encoded_name(member.filename)
        size += _LOCAL_HEADER.size + _CENTRAL_HEADER.size
        size += 2 * len(name_bytes) + member.expected_byte_size
        if size > _MAX_NON_ZIP64_BYTES:
            reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.archive_format")
    return size


def _expected_central_directory_size(members: tuple[SafeZipMember, ...]) -> int:
    return sum(_CENTRAL_HEADER.size + len(_encoded_name(member.filename)[0]) for member in members)


def _require_empty_output(output: BinaryIO) -> None:
    if output is None:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
    for capability in ("readable", "writable", "seekable"):
        method = getattr(output, capability, None)
        if not callable(method):
            reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
        try:
            available = method()
        except BaseException:
            reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
        if type(available) is not bool or not available:
            reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
    for operation in ("read", "write", "seek", "tell", "flush"):
        if not callable(getattr(output, operation, None)):
            reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
    end = _seek_output(output, 0, io.SEEK_END, "safe_zip.output")
    if end != 0:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output")
    _seek_output(output, 0, io.SEEK_SET, "safe_zip.output")


def _require_member_reader(
    member: SafeZipMember,
    *,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> None:
    reader = member.reader
    for capability in ("readable", "seekable"):
        method = getattr(reader, capability, None)
        if not callable(method):
            reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source")
        try:
            available = method()
        except BaseException:
            reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source")
        if type(available) is not bool or not available:
            reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source")
    for operation in ("read", "seek", "tell"):
        if not callable(getattr(reader, operation, None)):
            reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source")
    _require_active(deadline, cancellation)
    end = _seek_reader(reader, 0, io.SEEK_END)
    _require_active(deadline, cancellation)
    if end != member.expected_byte_size:
        reject_safe_zip(SafeZipErrorCode.SOURCE_MISMATCH, "safe_zip.source_size")
    _seek_reader(reader, 0, io.SEEK_SET)
    _require_active(deadline, cancellation)


def _write_archive(
    output: BinaryIO,
    members: tuple[SafeZipMember, ...],
    *,
    policy: SafeZipPolicy,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> tuple[_WrittenMember, ...]:
    checked_output = _CheckedZipOutput(
        output,
        policy.maximum_archive_bytes,
        cancellation=cancellation,
        deadline=deadline,
    )
    archive: zipfile.ZipFile | None = None
    written: list[_WrittenMember] = []
    try:
        archive = zipfile.ZipFile(
            cast(BinaryIO, checked_output),
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=False,
            compresslevel=None,
            strict_timestamps=True,
        )
        archive.comment = b""
        for position, member in enumerate(members, start=1):
            _require_active(deadline, cancellation)
            info = _zip_info(member.filename)
            digest = hashlib.sha256()
            crc32 = 0
            remaining = member.expected_byte_size
            with archive.open(info, mode="w", force_zip64=False) as destination:
                while remaining:
                    _require_active(deadline, cancellation)
                    requested = min(policy.io_chunk_bytes, remaining)
                    chunk = _read_member_chunk(member.reader, requested)
                    if not chunk:
                        reject_safe_zip(
                            SafeZipErrorCode.SOURCE_MISMATCH,
                            "safe_zip.source_truncated",
                        )
                    remaining -= len(chunk)
                    digest.update(chunk)
                    crc32 = binascii.crc32(chunk, crc32)
                    try:
                        count = destination.write(chunk)
                    except SafeZipError:
                        raise
                    except BaseException:
                        reject_safe_zip(SafeZipErrorCode.WRITE_FAILED, "safe_zip.member_write")
                    if type(count) is not int or count != len(chunk):
                        reject_safe_zip(SafeZipErrorCode.WRITE_FAILED, "safe_zip.member_write")
            if _read_member_chunk(member.reader, 1):
                reject_safe_zip(SafeZipErrorCode.SOURCE_MISMATCH, "safe_zip.source_grew")
            actual_sha256 = digest.hexdigest()
            if not hmac.compare_digest(actual_sha256, member.expected_sha256):
                reject_safe_zip(SafeZipErrorCode.SOURCE_MISMATCH, "safe_zip.source_digest")
            _seek_reader(member.reader, 0, io.SEEK_SET)
            name_bytes, flag_bits = _encoded_name(member.filename)
            if (
                info.file_size != member.expected_byte_size
                or info.compress_size != member.expected_byte_size
                or info.CRC != crc32
                or info.header_offset < 0
            ):
                reject_safe_zip(SafeZipErrorCode.WRITE_FAILED, "safe_zip.member_result")
            written.append(
                _WrittenMember(
                    fact=SafeZipMemberFact(
                        position=position,
                        filename=member.filename,
                        byte_size=member.expected_byte_size,
                        sha256=actual_sha256,
                        crc32=crc32,
                    ),
                    name_bytes=name_bytes,
                    flag_bits=flag_bits,
                    local_header_offset=info.header_offset,
                )
            )
        _require_active(deadline, cancellation)
        archive.close()
        archive = None
    except SafeZipError:
        raise
    except _OutputLimitExceeded:
        reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.archive_bytes")
    except zipfile.LargeZipFile:
        reject_safe_zip(SafeZipErrorCode.LIMIT_EXCEEDED, "safe_zip.archive_format")
    except _OutputFault, OSError, ValueError, RuntimeError:
        reject_safe_zip(SafeZipErrorCode.WRITE_FAILED, "safe_zip.archive_write")
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.INTERNAL_ERROR, "safe_zip.archive_write")
    finally:
        if archive is not None:
            try:
                archive.close()
            except BaseException:
                pass
    return tuple(written)


def _zip_info(filename: str) -> zipfile.ZipInfo:
    try:
        info = zipfile.ZipInfo(filename=filename, date_time=_FIXED_DATE_TIME)
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.NAME_INVALID, "safe_zip.member_name")
    if info.filename != filename or info.orig_filename != filename:
        reject_safe_zip(SafeZipErrorCode.NAME_INVALID, "safe_zip.member_name")
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 0
    info.create_version = 20
    info.extract_version = 20
    info.reserved = 0
    info.flag_bits = 0
    info.volume = 0
    info.internal_attr = 0
    info.external_attr = _DOS_ARCHIVE_ATTRIBUTE
    info.extra = b""
    info.comment = b""
    return info


def _verify_raw_layout(
    output: BinaryIO,
    archive_byte_size: int,
    members: tuple[_WrittenMember, ...],
    *,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> None:
    try:
        eocd_offset = archive_byte_size - _END_OF_CENTRAL_DIRECTORY.size
        eocd = _END_OF_CENTRAL_DIRECTORY.unpack(
            _read_output_at(output, eocd_offset, _END_OF_CENTRAL_DIRECTORY.size)
        )
        (
            signature,
            disk_number,
            central_disk,
            disk_entries,
            total_entries,
            central_size,
            central_offset,
            comment_size,
        ) = eocd
        if (
            signature != _END_SIGNATURE
            or disk_number != 0
            or central_disk != 0
            or disk_entries != len(members)
            or total_entries != len(members)
            or disk_entries == zipfile.ZIP_FILECOUNT_LIMIT
            or central_size == 0xFFFF_FFFF
            or central_offset == 0xFFFF_FFFF
            or comment_size != 0
            or central_offset + central_size != eocd_offset
        ):
            raise _OutputFault

        expected_local_offset = 0
        for member in members:
            _require_active(deadline, cancellation)
            if member.local_header_offset != expected_local_offset:
                raise _OutputFault
            local = _LOCAL_HEADER.unpack(
                _read_output_at(output, expected_local_offset, _LOCAL_HEADER.size)
            )
            (
                local_signature,
                extract_version,
                flags,
                method,
                dos_time,
                dos_date,
                crc32,
                compressed_size,
                file_size,
                name_size,
                extra_size,
            ) = local
            if (
                local_signature != _LOCAL_SIGNATURE
                or extract_version != 20
                or flags != member.flag_bits
                or method != zipfile.ZIP_STORED
                or dos_time != _FIXED_DOS_TIME
                or dos_date != _FIXED_DOS_DATE
                or crc32 != member.fact.crc32
                or compressed_size != member.fact.byte_size
                or file_size != member.fact.byte_size
                or name_size != len(member.name_bytes)
                or extra_size != 0
                or _read_output_at(
                    output,
                    expected_local_offset + _LOCAL_HEADER.size,
                    name_size,
                )
                != member.name_bytes
            ):
                raise _OutputFault
            expected_local_offset += _LOCAL_HEADER.size + name_size + member.fact.byte_size
        if expected_local_offset != central_offset:
            raise _OutputFault

        cursor = central_offset
        for member in members:
            _require_active(deadline, cancellation)
            central = _CENTRAL_HEADER.unpack(_read_output_at(output, cursor, _CENTRAL_HEADER.size))
            (
                central_signature,
                made_by_version,
                extract_version,
                flags,
                method,
                dos_time,
                dos_date,
                crc32,
                compressed_size,
                file_size,
                name_size,
                extra_size,
                member_comment_size,
                start_disk,
                internal_attr,
                external_attr,
                local_offset,
            ) = central
            if (
                central_signature != _CENTRAL_SIGNATURE
                or made_by_version != 20
                or extract_version != 20
                or flags != member.flag_bits
                or method != zipfile.ZIP_STORED
                or dos_time != _FIXED_DOS_TIME
                or dos_date != _FIXED_DOS_DATE
                or crc32 != member.fact.crc32
                or compressed_size != member.fact.byte_size
                or file_size != member.fact.byte_size
                or name_size != len(member.name_bytes)
                or extra_size != 0
                or member_comment_size != 0
                or start_disk != 0
                or internal_attr != 0
                or external_attr != _DOS_ARCHIVE_ATTRIBUTE
                or local_offset != member.local_header_offset
                or _read_output_at(
                    output,
                    cursor + _CENTRAL_HEADER.size,
                    name_size,
                )
                != member.name_bytes
            ):
                raise _OutputFault
            cursor += _CENTRAL_HEADER.size + name_size
        if cursor != eocd_offset or cursor - central_offset != central_size:
            raise _OutputFault
    except SafeZipError:
        raise
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_layout")


def _verify_members_with_zipfile(
    output: BinaryIO,
    members: tuple[_WrittenMember, ...],
    *,
    archive_byte_size: int,
    chunk_bytes: int,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> None:
    archive: zipfile.ZipFile | None = None
    try:
        _seek_output(output, 0, io.SEEK_SET, "safe_zip.archive_reopen")
        checked_input = _CheckedZipInput(
            output,
            maximum_bytes=archive_byte_size,
            maximum_request_bytes=chunk_bytes,
            cancellation=cancellation,
            deadline=deadline,
        )
        archive = zipfile.ZipFile(
            cast(BinaryIO, checked_input),
            mode="r",
            allowZip64=False,
        )
        if archive.comment != b"":
            raise zipfile.BadZipFile
        infos = archive.infolist()
        if len(infos) != len(members):
            raise zipfile.BadZipFile
        for info, member in zip(infos, members, strict=True):
            _require_active(deadline, cancellation)
            expected = member.fact
            if (
                info.filename != expected.filename
                or info.orig_filename != expected.filename
                or info.is_dir()
                or info.compress_type != zipfile.ZIP_STORED
                or info.date_time != _FIXED_DATE_TIME
                or info.flag_bits != member.flag_bits
                or info.create_system != 0
                or info.create_version != 20
                or info.extract_version != 20
                or info.reserved != 0
                or info.volume != 0
                or info.internal_attr != 0
                or info.external_attr != _DOS_ARCHIVE_ATTRIBUTE
                or info.extra != b""
                or info.comment != b""
                or info.file_size != expected.byte_size
                or info.compress_size != expected.byte_size
                or info.CRC != expected.crc32
                or info.header_offset != member.local_header_offset
            ):
                raise zipfile.BadZipFile
            digest = hashlib.sha256()
            count = 0
            with archive.open(info, mode="r") as source:
                while count < expected.byte_size:
                    _require_active(deadline, cancellation)
                    requested = min(chunk_bytes, expected.byte_size - count)
                    chunk = source.read(requested)
                    if type(chunk) is not bytes or not chunk or len(chunk) > requested:
                        raise zipfile.BadZipFile
                    count += len(chunk)
                    digest.update(chunk)
                if source.read(1) != b"":
                    raise zipfile.BadZipFile
            if count != expected.byte_size or not hmac.compare_digest(
                digest.hexdigest(), expected.sha256
            ):
                reject_safe_zip(
                    SafeZipErrorCode.VERIFICATION_FAILED,
                    "safe_zip.member_content",
                )
    except SafeZipError:
        raise
    except (
        _OutputFault,
        _OutputLimitExceeded,
        zipfile.BadZipFile,
        EOFError,
        OSError,
        ValueError,
        RuntimeError,
    ):
        reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_reopen")
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.INTERNAL_ERROR, "safe_zip.archive_reopen")
    finally:
        if archive is not None:
            try:
                archive.close()
            except BaseException:
                pass


def _hash_output(
    output: BinaryIO,
    expected_size: int,
    *,
    chunk_bytes: int,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> str:
    digest = hashlib.sha256()
    count = 0
    _seek_output(output, 0, io.SEEK_SET, "safe_zip.archive_hash")
    while count < expected_size:
        _require_active(deadline, cancellation)
        requested = min(chunk_bytes, expected_size - count)
        chunk = _read_output_chunk(output, requested, "safe_zip.archive_hash")
        if not chunk:
            reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_truncated")
        count += len(chunk)
        digest.update(chunk)
    if _read_output_chunk(output, 1, "safe_zip.archive_hash"):
        reject_safe_zip(SafeZipErrorCode.ARCHIVE_INVALID, "safe_zip.archive_grew")
    return digest.hexdigest()


def _output_size(output: BinaryIO) -> int:
    return _seek_output(output, 0, io.SEEK_END, "safe_zip.output_size")


def _flush_output(output: BinaryIO) -> None:
    try:
        result = output.flush()
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.WRITE_FAILED, "safe_zip.output_flush")
    if result is not None:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, "safe_zip.output_flush")


def _seek_output(output: BinaryIO, offset: int, whence: int, context: str) -> int:
    try:
        position = output.seek(offset, whence)
        confirmed = output.tell()
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, context)
    if (
        type(position) is not int
        or type(confirmed) is not int
        or position != confirmed
        or position < 0
    ):
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, context)
    return position


def _seek_reader(reader: BinaryIO, offset: int, whence: int) -> int:
    try:
        position = reader.seek(offset, whence)
        confirmed = reader.tell()
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source_seek")
    if (
        type(position) is not int
        or type(confirmed) is not int
        or position != confirmed
        or position < 0
    ):
        reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source_seek")
    return position


def _read_member_chunk(reader: BinaryIO, requested: int) -> bytes:
    try:
        value = reader.read(requested)
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source_read")
    if type(value) is not bytes or len(value) > requested:
        reject_safe_zip(SafeZipErrorCode.SOURCE_INVALID, "safe_zip.source_read")
    return value


def _read_output_chunk(output: BinaryIO, requested: int, context: str) -> bytes:
    try:
        value = output.read(requested)
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, context)
    if type(value) is not bytes or len(value) > requested:
        reject_safe_zip(SafeZipErrorCode.OUTPUT_INVALID, context)
    return value


def _read_output_at(output: BinaryIO, offset: int, count: int) -> bytes:
    _seek_output(output, offset, io.SEEK_SET, "safe_zip.archive_layout")
    collected = bytearray()
    while len(collected) < count:
        chunk = _read_output_chunk(
            output,
            count - len(collected),
            "safe_zip.archive_layout",
        )
        if not chunk:
            raise _OutputFault
        collected.extend(chunk)
    return bytes(collected)


def _encoded_name(filename: str) -> tuple[bytes, int]:
    try:
        return filename.encode("ascii"), 0
    except UnicodeEncodeError:
        try:
            return filename.encode("utf-8"), _UTF8_FILENAME_FLAG
        except UnicodeEncodeError:
            reject_safe_zip(SafeZipErrorCode.NAME_INVALID, "safe_zip.member_name")


def _require_safe_pdf_leaf_name(
    value: object,
    *,
    code: SafeZipErrorCode,
    context: str,
) -> None:
    if type(value) is not str:
        reject_safe_zip(code, context)
    normalized = unicodedata.normalize("NFC", value)
    invalid = (
        not value
        or value != normalized
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or not value.casefold().endswith(".pdf")
        or not value[:-4]
        or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in value)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    )
    try:
        utf16_units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        reject_safe_zip(code, context)
    stem = value.casefold().split(".", maxsplit=1)[0].rstrip(" .")
    if invalid or stem in _WINDOWS_RESERVED_STEMS or utf16_units > _MAX_FILENAME_UTF16_UNITS:
        reject_safe_zip(code, context)


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _probe(deadline, "safe_zip.deadline_probe"):
        reject_safe_zip(SafeZipErrorCode.TIMED_OUT, "safe_zip.deadline")
    if _probe(cancellation, "safe_zip.cancellation_probe"):
        reject_safe_zip(SafeZipErrorCode.CANCELLED, "safe_zip.cancelled")


def _probe(probe: DeadlineProbe | CancellationProbe, context: str) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_safe_zip(SafeZipErrorCode.INTERNAL_ERROR, context)
    if type(value) is not bool:
        reject_safe_zip(SafeZipErrorCode.INTERNAL_ERROR, context)
    return value
