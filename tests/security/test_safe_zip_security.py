"""Adversarial filename, stream, corruption, and disclosure ZIP evidence."""

from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from typing import cast

import pytest

from paperworkshell.export.safe_zip import (
    SafeZipError,
    SafeZipErrorCode,
    SafeZipMember,
    SafeZipPolicy,
    write_safe_zip,
)


def _policy() -> SafeZipPolicy:
    return SafeZipPolicy(
        maximum_members=8,
        maximum_member_bytes=4_096,
        maximum_total_member_bytes=8_192,
        maximum_archive_bytes=16_384,
        io_chunk_bytes=4_096,
    )


def _member(filename: str, payload: bytes, *, reader: BytesIO | None = None) -> SafeZipMember:
    return SafeZipMember(
        filename=filename,
        reader=BytesIO(payload) if reader is None else reader,
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _never() -> bool:
    return False


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape.pdf",
        "..\\escape.pdf",
        "/absolute.pdf",
        "C:\\absolute.pdf",
        "member.pdf:private",
        "folder/member.pdf",
        "folder\\member.pdf",
        "member\x00hidden.pdf",
        "line\nbreak.pdf",
        "zero\u200bwidth.pdf",
        "safe\u202ereversed.pdf",
        "e\u0301.pdf",
        "CON.pdf",
        "nul.PDF",
        "LPT9.pdf",
        "member?.pdf",
        "member%2fescape.pdf",
        "member.pdf ",
        "member.pdf.",
        ".pdf",
        "archive.zip",
        f"{'a' * 177}.pdf",
    ],
)
def test_traversal_device_nul_separator_and_non_nfc_names_are_rejected_safely(
    unsafe_name: str,
) -> None:
    with pytest.raises(SafeZipError) as raised:
        _member(unsafe_name, b"synthetic")

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is SafeZipErrorCode.NAME_INVALID
    assert unsafe_name not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "names",
    [
        ("Alpha.pdf", "alpha.PDF"),
        ("Straße.pdf", "STRASSE.pdf"),
    ],
)
def test_exact_casefold_collisions_fail_before_output_mutation(names: tuple[str, str]) -> None:
    output = BytesIO()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member(names[0], b"first"), _member(names[1], b"second")),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.NAME_COLLISION
    assert output.getvalue() == b""


def test_valid_nfc_unicode_name_is_exact_and_uses_only_utf8_filename_flag() -> None:
    name = "café.pdf"
    output = BytesIO()

    report = write_safe_zip(
        output,
        (_member(name, b"verified-pdf"),),
        policy=_policy(),
        cancellation=_never,
        deadline=_never,
    )

    assert report.members[0].filename == name
    with zipfile.ZipFile(output, mode="r") as archive:
        info = archive.infolist()[0]
        assert info.filename == name
        assert info.orig_filename == name
        assert info.flag_bits == 0x0800
        assert info.extra == b""
        assert info.comment == b""
        assert info.extract_version == 20


class _ExplodingReader(BytesIO):
    def __init__(self, payload: bytes, canary: str) -> None:
        super().__init__(payload)
        self.canary = canary

    def read(self, size: int | None = -1, /) -> bytes:
        raise OSError(self.canary)


def test_raw_reader_failures_and_private_facts_never_escape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "SYNTHETIC-PRIVATE-READER-CANARY"
    payload = b"private-pdf-content"
    member = _member(
        "private-original-name-canary.pdf",
        payload,
        reader=_ExplodingReader(payload, canary),
    )

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            BytesIO(),
            (member,),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}\n{caplog.text}"
    assert raised.value.code is SafeZipErrorCode.SOURCE_INVALID
    assert canary not in rendered
    assert "private-original-name-canary.pdf" not in rendered
    assert hashlib.sha256(payload).hexdigest() not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


class _TransientSubstitutionOutput(BytesIO):
    """Return altered bytes for the first read without changing stored bytes."""

    def __init__(self) -> None:
        super().__init__()
        self.substitute = True

    def read(self, size: int | None = -1, /) -> bytes:
        value = super().read(size)
        if self.substitute and value:
            self.substitute = False
            return bytes((value[0] ^ 1,)) + value[1:]
        return value


def test_transient_output_substitution_is_caught_by_archive_stability_hash() -> None:
    output = _TransientSubstitutionOutput()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member("member.pdf", b"verified-pdf-content"),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.ARCHIVE_INVALID
    assert raised.value.context == "safe_zip.archive_changed"


class _PersistentCorruptionOutput(BytesIO):
    """Corrupt the first member byte immediately before verification reads."""

    def __init__(self) -> None:
        super().__init__()
        self.corrupted = False

    def read(self, size: int | None = -1, /) -> bytes:
        if not self.corrupted and len(self.getvalue()) > 40:
            current = super().tell()
            super().seek(40)
            original = super().read(1)
            super().seek(40)
            super().write(bytes((original[0] ^ 1,)))
            super().seek(current)
            self.corrupted = True
        return super().read(size)


def test_persistent_member_corruption_fails_crc_or_content_verification() -> None:
    output = _PersistentCorruptionOutput()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member("member.pdf", b"verified-pdf-content"),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code in {
        SafeZipErrorCode.ARCHIVE_INVALID,
        SafeZipErrorCode.VERIFICATION_FAILED,
    }


class _NonBytesReadbackOutput(BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        return cast(bytes, bytearray(super().read(size)))


def test_malformed_output_readback_is_rejected_without_success_report() -> None:
    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            _NonBytesReadbackOutput(),
            (_member("member.pdf", b"verified-pdf-content"),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.OUTPUT_INVALID


class _ShortReadingOutput(BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        requested = 3 if size is None or size < 0 else min(size, 3)
        return super().read(requested)


def test_legal_short_reading_seekable_output_is_filled_during_reopen() -> None:
    output = _ShortReadingOutput()
    payload = b"verified-pdf-content" * 100

    report = write_safe_zip(
        output,
        (_member("member.pdf", payload),),
        policy=_policy(),
        cancellation=_never,
        deadline=_never,
    )

    assert report.archive_sha256 == hashlib.sha256(output.getvalue()).hexdigest()
    with zipfile.ZipFile(BytesIO(output.getvalue()), mode="r") as archive:
        assert archive.read("member.pdf") == payload


class _SecondEocdSubstitutionOutput(BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.eocd_reads = 0
        self.read_requests: list[int] = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.read_requests.append(-1 if size is None else size)
        value = super().read(size)
        if size == 22 and len(value) == 22 and value.startswith(b"PK\x05\x06"):
            self.eocd_reads += 1
            if self.eocd_reads == 2:
                return b"XX" + value[2:]
        return value


def test_alternate_eocd_search_cannot_issue_an_out_of_policy_read() -> None:
    output = _SecondEocdSubstitutionOutput()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member("member.pdf", b"verified-pdf-content"),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.ARCHIVE_INVALID
    assert max(output.read_requests) <= 4_096
    assert -1 not in output.read_requests
