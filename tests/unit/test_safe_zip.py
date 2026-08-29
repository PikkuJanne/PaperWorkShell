"""Unit evidence for deterministic, bounded safe ZIP packaging."""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Buffer
from dataclasses import FrozenInstanceError
from io import BytesIO
from typing import cast

import pytest

from paperworkshell.domain.resource_policy import NORMAL_RESOURCE_POLICY_V1
from paperworkshell.export.safe_zip import (
    SafeZipCheckCode,
    SafeZipError,
    SafeZipErrorCode,
    SafeZipMember,
    SafeZipPolicy,
    write_safe_zip,
)


def _policy(
    *,
    maximum_members: int = 8,
    maximum_member_bytes: int = 64 * 1_024,
    maximum_total_member_bytes: int = 128 * 1_024,
    maximum_archive_bytes: int = 256 * 1_024,
    io_chunk_bytes: int = 4 * 1_024,
) -> SafeZipPolicy:
    return SafeZipPolicy(
        maximum_members=maximum_members,
        maximum_member_bytes=maximum_member_bytes,
        maximum_total_member_bytes=maximum_total_member_bytes,
        maximum_archive_bytes=maximum_archive_bytes,
        io_chunk_bytes=io_chunk_bytes,
    )


def _member(
    filename: str,
    payload: bytes,
    *,
    reader: BytesIO | None = None,
    digest: str | None = None,
) -> SafeZipMember:
    return SafeZipMember(
        filename=filename,
        reader=BytesIO(payload) if reader is None else reader,
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest() if digest is None else digest,
    )


def _never() -> bool:
    return False


class _TrackingReader(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.requests: list[int] = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.requests.append(-1 if size is None else size)
        return super().read(size)


class _TrackingOutput(BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.read_requests: list[int] = []
        self.write_requests: list[int] = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.read_requests.append(-1 if size is None else size)
        return super().read(size)

    def write(self, value: Buffer, /) -> int:
        self.write_requests.append(memoryview(value).nbytes)
        return super().write(value)


def test_archive_is_byte_deterministic_plan_ordered_and_strictly_profiled() -> None:
    alpha_payload = b"alpha-pdf-bytes" * 400
    beta_payload = b"beta-pdf-bytes" * 400
    alpha_reader = _TrackingReader(alpha_payload)
    beta_reader = _TrackingReader(beta_payload)
    first = _TrackingOutput()

    report = write_safe_zip(
        first,
        (
            _member("alpha.pdf", alpha_payload, reader=alpha_reader),
            _member("beta.pdf", beta_payload, reader=beta_reader),
        ),
        policy=_policy(io_chunk_bytes=4_096),
        cancellation=_never,
        deadline=_never,
    )
    second = BytesIO()
    second_report = write_safe_zip(
        second,
        (_member("alpha.pdf", alpha_payload), _member("beta.pdf", beta_payload)),
        policy=_policy(io_chunk_bytes=4_096),
        cancellation=_never,
        deadline=_never,
    )

    assert first.getvalue() == second.getvalue()
    assert report == second_report
    assert report.archive_byte_size == len(first.getvalue())
    assert report.archive_sha256 == hashlib.sha256(first.getvalue()).hexdigest()
    assert [member.filename for member in report.members] == ["alpha.pdf", "beta.pdf"]
    assert [member.byte_size for member in report.members] == [
        len(alpha_payload),
        len(beta_payload),
    ]
    assert [member.sha256 for member in report.members] == [
        hashlib.sha256(alpha_payload).hexdigest(),
        hashlib.sha256(beta_payload).hexdigest(),
    ]
    assert report.checks == (
        SafeZipCheckCode.LAYOUT_VERIFIED,
        SafeZipCheckCode.ARCHIVE_REOPENED,
        SafeZipCheckCode.METADATA_VERIFIED,
        SafeZipCheckCode.MEMBER_ORDER_VERIFIED,
        SafeZipCheckCode.MEMBER_CONTENT_VERIFIED,
        SafeZipCheckCode.ARCHIVE_STABILITY_VERIFIED,
    )
    assert first.tell() == 0
    assert alpha_reader.tell() == 0
    assert beta_reader.tell() == 0
    assert max(alpha_reader.requests) <= 4_096
    assert max(beta_reader.requests) <= 4_096
    assert -1 not in alpha_reader.requests
    assert -1 not in beta_reader.requests
    assert max(first.read_requests) <= 4_096
    assert max(first.write_requests) <= 4_096
    assert -1 not in first.read_requests

    with zipfile.ZipFile(first, mode="r") as archive:
        assert archive.comment == b""
        assert archive.namelist() == ["alpha.pdf", "beta.pdf"]
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 0
            assert info.create_version == 20
            assert info.extract_version == 20
            assert info.external_attr == 0x20
            assert info.internal_attr == 0
            assert info.comment == b""
            assert info.extra == b""
            assert not info.is_dir()


def test_caller_package_plan_order_is_preserved_in_archive_and_report() -> None:
    output = BytesIO()

    report = write_safe_zip(
        output,
        (_member("zeta.pdf", b"first"), _member("alpha.pdf", b"second")),
        policy=_policy(),
        cancellation=_never,
        deadline=_never,
    )

    assert [member.filename for member in report.members] == ["zeta.pdf", "alpha.pdf"]
    with zipfile.ZipFile(output, mode="r") as archive:
        assert archive.namelist() == ["zeta.pdf", "alpha.pdf"]


def test_policy_projects_only_central_resource_budgets() -> None:
    policy = SafeZipPolicy.from_resource_policy(NORMAL_RESOURCE_POLICY_V1)

    assert policy.maximum_members == NORMAL_RESOURCE_POLICY_V1.maximum_page_count
    assert policy.maximum_member_bytes == NORMAL_RESOURCE_POLICY_V1.maximum_source_bytes
    assert (
        policy.maximum_total_member_bytes
        == NORMAL_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
    )
    assert policy.maximum_archive_bytes == NORMAL_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
    assert policy.io_chunk_bytes == NORMAL_RESOURCE_POLICY_V1.import_io_chunk_bytes


def test_policy_rejects_chunks_below_stdlib_streaming_minimum() -> None:
    with pytest.raises(SafeZipError) as raised:
        _policy(io_chunk_bytes=4_095)

    assert raised.value.code is SafeZipErrorCode.MODEL_INVALID


def test_central_directory_must_fit_one_bounded_reopen_request() -> None:
    members = tuple(_member(f"{index:03d}-{'a' * 48}.pdf", b"x") for index in range(80))
    output = BytesIO()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            members,
            policy=_policy(maximum_members=80, io_chunk_bytes=4_096),
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "safe_zip.central_directory_bytes"
    assert output.getvalue() == b""


def test_report_and_member_representations_hide_names_digests_and_readers() -> None:
    name = "private-name-canary.pdf"
    payload = b"private-content-canary"
    member = _member(name, payload)
    output = BytesIO()
    report = write_safe_zip(
        output,
        (member,),
        policy=_policy(),
        cancellation=_never,
        deadline=_never,
    )
    digest = hashlib.sha256(payload).hexdigest()

    rendered = f"{member!r}\n{report!r}\n{report.members!r}"
    assert name not in rendered
    assert digest not in rendered
    assert "BytesIO" not in rendered
    immutable_attribute = "archive_byte_size"
    with pytest.raises(FrozenInstanceError):
        setattr(report, immutable_attribute, 1)


@pytest.mark.parametrize(
    ("policy", "members", "expected_context"),
    [
        (_policy(maximum_members=1), (_member("a.pdf", b"a"), _member("b.pdf", b"b")), "request"),
        (_policy(maximum_member_bytes=2), (_member("a.pdf", b"abc"),), "member_bytes"),
        (
            _policy(maximum_member_bytes=4, maximum_total_member_bytes=4),
            (_member("a.pdf", b"aaa"), _member("b.pdf", b"bb")),
            "member_bytes",
        ),
        (
            _policy(
                maximum_member_bytes=10,
                maximum_total_member_bytes=10,
                maximum_archive_bytes=100,
            ),
            (_member("a.pdf", b"a"),),
            "archive_bytes",
        ),
    ],
)
def test_count_member_total_and_archive_limits_fail_before_writes(
    policy: SafeZipPolicy,
    members: tuple[SafeZipMember, ...],
    expected_context: str,
) -> None:
    output = BytesIO()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            members,
            policy=policy,
            cancellation=_never,
            deadline=_never,
        )

    assert raised.value.code in {
        SafeZipErrorCode.MODEL_INVALID,
        SafeZipErrorCode.LIMIT_EXCEEDED,
    }
    assert expected_context in raised.value.context
    assert output.getvalue() == b""


def test_empty_package_and_nonempty_output_fail_closed() -> None:
    with pytest.raises(SafeZipError) as empty:
        write_safe_zip(
            BytesIO(),
            (),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert empty.value.code is SafeZipErrorCode.MODEL_INVALID

    output = BytesIO(b"prior-stage-bytes")
    with pytest.raises(SafeZipError) as nonempty:
        write_safe_zip(
            output,
            (_member("a.pdf", b"a"),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert nonempty.value.code is SafeZipErrorCode.OUTPUT_INVALID
    assert output.getvalue() == b"prior-stage-bytes"


class _CancelAfter:
    def __init__(self, allowed_calls: int) -> None:
        self.allowed_calls = allowed_calls
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls > self.allowed_calls


def test_cancellation_is_polled_within_bounded_member_streaming() -> None:
    payload = b"x" * 32_768
    reader = _TrackingReader(payload)
    cancellation = _CancelAfter(12)

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            BytesIO(),
            (_member("member.pdf", payload, reader=reader),),
            policy=_policy(io_chunk_bytes=4_096),
            cancellation=cancellation,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.CANCELLED
    assert reader.requests
    assert max(reader.requests) <= 4_096
    assert len(reader.requests) < 9


def test_deadline_is_distinct_and_checked_before_writing() -> None:
    output = BytesIO()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member("member.pdf", b"x"),),
            policy=_policy(),
            cancellation=_never,
            deadline=lambda: True,
        )

    assert raised.value.code is SafeZipErrorCode.TIMED_OUT
    assert output.getvalue() == b""


def test_cancellation_is_polled_during_central_directory_finalization() -> None:
    output = BytesIO()

    def cancel_after_central_header() -> bool:
        return b"PK\x01\x02" in output.getvalue()

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            output,
            (_member("first.pdf", b"first"), _member("second.pdf", b"second")),
            policy=_policy(),
            cancellation=cancel_after_central_header,
            deadline=_never,
        )

    assert raised.value.code is SafeZipErrorCode.CANCELLED
    assert b"PK\x01\x02" in output.getvalue()
    assert not output.getvalue().endswith(b"PK\x05\x06")


def test_source_size_digest_and_equal_length_substitution_are_rejected() -> None:
    with pytest.raises(SafeZipError) as size:
        write_safe_zip(
            BytesIO(),
            (
                SafeZipMember(
                    filename="member.pdf",
                    reader=BytesIO(b"short"),
                    expected_byte_size=6,
                    expected_sha256=hashlib.sha256(b"longer").hexdigest(),
                ),
            ),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert size.value.code is SafeZipErrorCode.SOURCE_MISMATCH

    expected = b"verified-content"
    substituted = b"changed--content"
    assert len(expected) == len(substituted)
    with pytest.raises(SafeZipError) as digest:
        write_safe_zip(
            BytesIO(),
            (_member("member.pdf", substituted, digest=hashlib.sha256(expected).hexdigest()),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert digest.value.code is SafeZipErrorCode.SOURCE_MISMATCH


class _NonBytesReader(BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        return cast(bytes, bytearray(super().read(size)))


class _ShortWriteOutput(BytesIO):
    def write(self, value: Buffer, /) -> int:
        payload = bytes(value)
        if not payload:
            return 0
        return super().write(payload[:-1])


def test_malformed_reader_and_short_writer_are_closed_failures() -> None:
    payload = b"synthetic"
    with pytest.raises(SafeZipError) as reader_failure:
        write_safe_zip(
            BytesIO(),
            (_member("member.pdf", payload, reader=_NonBytesReader(payload)),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert reader_failure.value.code is SafeZipErrorCode.SOURCE_INVALID

    with pytest.raises(SafeZipError) as writer_failure:
        write_safe_zip(
            _ShortWriteOutput(),
            (_member("member.pdf", payload),),
            policy=_policy(),
            cancellation=_never,
            deadline=_never,
        )
    assert writer_failure.value.code is SafeZipErrorCode.WRITE_FAILED


@pytest.mark.parametrize("probe_value", [None, 1, "false"])
def test_malformed_control_probe_is_sanitized(probe_value: object) -> None:
    canary = "private-probe-value-canary"

    def malformed_probe() -> bool:
        if probe_value == "false":
            raise RuntimeError(canary)
        return cast(bool, probe_value)

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            BytesIO(),
            (_member("member.pdf", b"x"),),
            policy=_policy(),
            cancellation=_never,
            deadline=malformed_probe,
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is SafeZipErrorCode.INTERNAL_ERROR
    assert canary not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_mutated_safe_zip_error_from_external_probe_is_reconstructed() -> None:
    canary = "SYNTHETIC-PRIVATE-ERROR-CANARY"
    injected = SafeZipError(SafeZipErrorCode.INTERNAL_ERROR, "safe_zip.internal")
    injected.safe_message = canary
    injected.args = (canary,)

    def hostile_probe() -> bool:
        raise injected

    with pytest.raises(SafeZipError) as raised:
        write_safe_zip(
            BytesIO(),
            (_member("member.pdf", b"x"),),
            policy=_policy(),
            cancellation=_never,
            deadline=hostile_probe,
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value is not injected
    assert raised.value.code is SafeZipErrorCode.INTERNAL_ERROR
    assert canary not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
