"""Public signing policy, exact unsigned-image binding and refusal paths.

Synthetic CMS fixtures exercise parsing and pipeline decisions. Their native
trust result is explicitly mocked; they are never evidence of trusted signing.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

import pytest

from tools import windows_signing as signing


def policy_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "application_id": signing.APPLICATION_ID,
        "approval": "maintainer-approved",
        "certificate_subject": "CN=Synthetic Approved Signer",
        "certificate_sha256": "a" * 64,
        "certificate_thumbprint": "b" * 40,
        "digest_algorithm": "sha256",
        "timestamp_digest_algorithm": "sha256",
        "timestamp_required": True,
    }


def write_policy(path: Path, **changes: object) -> tuple[Path, str]:
    value = policy_data()
    value.update(changes)
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def make_candidate(tmp_path: Path) -> tuple[Path, signing.SigningPolicy]:
    candidate = tmp_path / "output/pws0802/synthetic-candidate"
    candidate.mkdir(parents=True)
    public, digest = write_policy(tmp_path / "public-policy.json")
    return candidate, signing.load_policy(public, digest)


def unsigned_pe() -> bytes:
    result = bytearray(1025)
    result[:2] = b"MZ"
    struct.pack_into("<I", result, 60, 128)
    result[128:132] = b"PE\0\0"
    struct.pack_into("<H", result, 132, 0x8664)
    struct.pack_into("<H", result, 148, 240)
    struct.pack_into("<H", result, 152, 0x20B)
    struct.pack_into("<I", result, 152 + 108, 16)
    result[512:540] = b"synthetic application bytes!".ljust(28, b" ")
    return bytes(result)


def der(tag: int, raw: bytes) -> bytes:
    length = len(raw)
    size = (length.bit_length() + 7) // 8
    encoded = bytes([length]) if length < 128 else bytes([0x80 | size]) + length.to_bytes(size)
    return bytes([tag]) + encoded + raw


def sequence(*values: bytes) -> bytes:
    return der(0x30, b"".join(values))


def oid(value: str) -> bytes:
    return der(6, bytes.fromhex(value))


def algorithm(*, sha256: bool = True) -> bytes:
    return sequence(oid("608648016503040201" if sha256 else "2b0e03021a"), der(5, b""))


def cms(content_type: str, content: bytes, signature: bytes, unsigned: bytes = b"") -> bytes:
    signer = sequence(
        der(2, b"\x01"),
        sequence(),
        algorithm(),
        der(0xA0, b""),
        sequence(oid("2a864886f70d010101"), der(5, b"")),
        der(4, signature),
        unsigned,
    )
    signed = sequence(
        der(2, b"\x01"),
        der(0x31, algorithm()),
        sequence(oid(content_type), der(0xA0, content)),
        der(0x31, signer),
    )
    return sequence(oid("2a864886f70d010702"), der(0xA0, signed))


def fake_signed_pe(
    original: bytes,
    *,
    timestamp: bool = True,
    timestamp_binding: bool = True,
    sha256: bool = True,
    checksum: int = 123,
) -> bytes:
    signature = b"synthetic-code-signature"
    imprint = hashlib.sha256(signature if timestamp_binding else b"different signature").digest()
    token = sequence(
        der(2, b"\x01"),
        oid("2a0304"),
        sequence(algorithm(), der(4, imprint)),
        der(2, b"\x01"),
        der(0x18, b"20200101000000Z"),
    )
    token_cms = cms("2a864886f70d0109100104", der(4, token), b"synthetic-timestamp-signature")
    unsigned = (
        der(0xA1, sequence(oid("2b060104018237030301"), der(0x31, token_cms))) if timestamp else b""
    )
    content = sequence(
        sequence(oid("2a0304")), sequence(algorithm(sha256=sha256), der(4, b"x" * 32))
    )
    encoded = cms("2b060104018237020104", content, signature, unsigned)
    certificate = struct.pack("<IHH", 8 + len(encoded), 0x200, 2) + encoded
    certificate += bytes(-len(certificate) % 8)
    result = bytearray(original)
    result += bytes(-len(result) % 8)
    offset = len(result)
    struct.pack_into("<I", result, 152 + 64, checksum)
    struct.pack_into("<II", result, 152 + 112 + 32, offset, len(certificate))
    result += certificate
    return bytes(result)


def native_result(policy: signing.SigningPolicy, **changes: object) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "valid",
        "certificate_subject": policy.certificate_subject,
        "certificate_sha256": policy.certificate_sha256,
        "certificate_thumbprint": policy.certificate_thumbprint,
        "timestamp_certificate_sha256": "c" * 64,
        "timestamp_utc": "2020-01-01T00:00:00Z",
        "cms_valid": True,
        "timestamp_signature_valid": True,
        "timestamp_chain_valid": True,
    }
    result.update(changes)
    return result


def mocked_native(monkeypatch: pytest.MonkeyPatch, policy: signing.SigningPolicy) -> None:
    monkeypatch.setattr(signing, "_native_verify", lambda *_args: native_result(policy))


def test_public_policy_is_hash_anchored_and_independent_of_product_publisher(
    tmp_path: Path,
) -> None:
    path, digest = write_policy(
        tmp_path / "policy.json", certificate_subject="CN=Synthetic Foundation"
    )
    policy = signing.load_policy(path, digest)
    assert policy.policy_sha256 == digest
    assert policy.certificate_subject == "CN=Synthetic Foundation"
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_POLICY"):
        signing.load_policy(path, "d" * 64)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", True),
        ("schema_version", 0),
        ("schema_version", 2),
        ("application_id", "unrelated.product"),
        ("approval", "proposed"),
        ("certificate_subject", ""),
        ("certificate_subject", "CN=private-canary\ninvalid"),
        ("certificate_sha256", "0" * 64),
        ("certificate_sha256", "a" * 63),
        ("certificate_thumbprint", "0" * 40),
        ("certificate_thumbprint", "b" * 39),
        ("digest_algorithm", "sha1"),
        ("timestamp_digest_algorithm", "sha1"),
        ("timestamp_required", False),
        ("private_key", "private-canary"),
    ],
)
def test_unapproved_or_incomplete_policy_is_refused(
    tmp_path: Path, key: str, value: object
) -> None:
    path, digest = write_policy(tmp_path / "policy.json", **{key: value})
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_POLICY"):
        signing.load_policy(path, digest)


def test_prepare_binds_original_bytes_and_never_overwrites_a_request(tmp_path: Path) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "PaperWorkShell.exe"
    raw = unsigned_pe()
    target.write_bytes(raw)
    output = candidate / "request.json"
    receipt = signing.prepare(candidate, [target.name], policy, output, root=tmp_path)
    assert receipt["request_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert receipt["files"][0]["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert target.read_bytes() == raw
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_EXISTS"):
        signing.prepare(candidate, [target.name], policy, output, root=tmp_path)


@pytest.mark.parametrize("extension", [".e32", ".e64"])
def test_inno_generated_uninstaller_extensions_still_require_actual_pe_content(
    tmp_path: Path, extension: str
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / ("uninstaller" + extension)
    target.write_bytes(unsigned_pe())
    assert (
        signing.prepare(
            candidate, [target.name], policy, candidate / "request.json", root=tmp_path
        )["status"]
        == "prepared"
    )


@pytest.mark.parametrize("online", [False, True])
def test_revocation_network_retrieval_requires_an_explicit_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: bool
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    target.write_bytes(fake_signed_pe(unsigned_pe()))
    observed: list[dict[str, object]] = []

    def native(*_args: object, **kwargs: object) -> dict[str, Any]:
        observed.append(kwargs)
        return native_result(policy)

    monkeypatch.setattr(signing, "_native_verify", native)
    result = signing.verify(candidate, target.name, policy, root=tmp_path, online_revocation=online)
    assert observed == [{"online_revocation": True} if online else {}]
    assert result["revocation_mode"] == ("online" if online else "cache_only")


def test_win_certificate_padding_may_be_included_in_its_declared_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    signed = bytearray(fake_signed_pe(unsigned_pe()))
    table_offset, table_size = struct.unpack_from("<II", signed, 152 + 112 + 32)
    struct.pack_into("<I", signed, table_offset, table_size)
    target.write_bytes(signed)
    mocked_native(monkeypatch, policy)
    assert signing.verify(candidate, target.name, policy, root=tmp_path)["status"] == "verified"


def test_ingest_requires_the_same_original_image_and_verifies_final_signed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target, incoming, request = (
        candidate / "app.exe",
        candidate / "signed.exe",
        candidate / "request.json",
    )
    original = unsigned_pe()
    target.write_bytes(original)
    prepared = signing.prepare(candidate, [target.name], policy, request, root=tmp_path)
    signed = fake_signed_pe(original)
    incoming.write_bytes(signed)
    mocked_native(monkeypatch, policy)
    receipt = signing.ingest(
        candidate,
        target.name,
        incoming.name,
        request,
        prepared["request_sha256"],
        policy,
        root=tmp_path,
    )
    assert receipt["image_binding"] == "verified"
    assert receipt["sha256"] == hashlib.sha256(signed).hexdigest()
    assert receipt["sha256"] != prepared["files"][0]["original_sha256"]
    assert target.read_bytes() == incoming.read_bytes() == signed
    assert sorted(path.name for path in candidate.iterdir()) == [
        "app.exe",
        "request.json",
        "signed.exe",
    ]


@pytest.mark.parametrize("change", ["image", "padding", "request", "original"])
def test_handoff_drift_preserves_original_and_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target, incoming, request = (
        candidate / "app.exe",
        candidate / "signed.exe",
        candidate / "request.json",
    )
    original = unsigned_pe()
    target.write_bytes(original)
    prepared = signing.prepare(candidate, [target.name], policy, request, root=tmp_path)
    signed = bytearray(fake_signed_pe(original))
    if change == "image":
        signed[600] ^= 1
    elif change == "padding":
        signed[len(original)] = 1
    elif change == "request":
        request.write_bytes(request.read_bytes() + b" ")
    else:
        target.write_bytes(original + b"changed")
    incoming.write_bytes(signed)
    before = target.read_bytes()
    mocked_native(monkeypatch, policy)
    with pytest.raises(signing.WindowsSigningError):
        signing.ingest(
            candidate,
            target.name,
            incoming.name,
            request,
            prepared["request_sha256"],
            policy,
            root=tmp_path,
        )
    assert target.read_bytes() == before
    assert not list(candidate.glob(".signing-*"))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"timestamp": False}, "SIGNING_TIMESTAMP"),
        ({"timestamp_binding": False}, "SIGNING_TIMESTAMP_BINDING"),
        ({"sha256": False}, "SIGNING_DIGEST"),
    ],
)
def test_missing_timestamp_wrong_imprint_and_weak_file_digest_fail_closed(
    tmp_path: Path, changes: dict[str, bool], expected: str
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    target.write_bytes(fake_signed_pe(unsigned_pe(), **changes))
    with pytest.raises(signing.WindowsSigningError, match=expected):
        signing.verify(candidate, target.name, policy, root=tmp_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "HashMismatch"),
        ("certificate_subject", "CN=Wrong Signer"),
        ("certificate_sha256", "d" * 64),
        ("certificate_thumbprint", "d" * 40),
        ("timestamp_certificate_sha256", None),
        ("timestamp_utc", "2020-01-02T00:00:00Z"),
        ("cms_valid", False),
        ("timestamp_signature_valid", False),
        ("timestamp_chain_valid", False),
    ],
)
def test_tampered_wrong_signer_or_untrusted_timestamp_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: object
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target = candidate / "app.exe"
    target.write_bytes(fake_signed_pe(unsigned_pe()))
    monkeypatch.setattr(
        signing, "_native_verify", lambda *_a: native_result(policy, **{key: value})
    )
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_TRUST"):
        signing.verify(candidate, target.name, policy, root=tmp_path)


def test_external_signer_receives_only_a_verified_owned_copy_with_no_shell_or_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target, signer = candidate / "app.exe", tmp_path / "approved-signer.exe"
    raw = unsigned_pe()
    target.write_bytes(raw)
    signer.write_bytes(b"authenticated synthetic signer")
    digest = hashlib.sha256(signer.read_bytes()).hexdigest()
    calls: list[tuple[list[str], Path]] = []

    def execute(argv: list[str], *, cwd: Path) -> bytes:
        assert len(argv) == 2
        assert argv[0] == str(signer)
        assert Path(argv[1]).is_relative_to(candidate)
        assert Path(argv[1]) != target
        assert target.read_bytes() == raw
        calls.append((argv, cwd))
        Path(argv[1]).write_bytes(fake_signed_pe(Path(argv[1]).read_bytes()))
        return b""

    monkeypatch.setattr(signing, "_run_bounded", execute)
    mocked_native(monkeypatch, policy)
    receipt = signing.sign(candidate, target.name, policy, signer, digest, root=tmp_path)
    assert len(calls) == 1
    assert receipt["image_binding"] == "verified"
    assert target.read_bytes() == fake_signed_pe(raw)


@pytest.mark.parametrize("failure", ["signer", "process", "untrusted", "diskfull"])
def test_failed_signing_preserves_original_and_cleans_only_owned_partial_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    candidate, policy = make_candidate(tmp_path)
    target, signer = candidate / "app.exe", tmp_path / "approved-signer.exe"
    target.write_bytes(unsigned_pe())
    signer.write_bytes(b"authenticated synthetic signer")
    digest = hashlib.sha256(signer.read_bytes()).hexdigest()
    before = target.read_bytes()

    def execute(argv: list[str], *, cwd: Path) -> bytes:
        if failure == "process":
            raise signing.WindowsSigningError("SIGNING_PROCESS_FAILED")
        Path(argv[1]).write_bytes(fake_signed_pe(Path(argv[1]).read_bytes()))
        return b""

    monkeypatch.setattr(signing, "_run_bounded", execute)
    monkeypatch.setattr(
        signing, "_native_verify", lambda *_a: native_result(policy, cms_valid=False)
    )
    if failure == "diskfull":

        def copy(source: Path, target: Path) -> None:
            target.write_bytes(b"partial")
            raise OSError("synthetic disk full private-canary")

        monkeypatch.setattr(signing, "_copy", copy)
    with pytest.raises((signing.WindowsSigningError, OSError)):
        signing.sign(
            candidate,
            target.name,
            policy,
            signer,
            "d" * 64 if failure == "signer" else digest,
            root=tmp_path,
        )
    assert target.read_bytes() == before
    assert not list(candidate.glob(".signing-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows process-boundary test")
@pytest.mark.parametrize("kind", ["success", "timeout", "overflow"])
def test_native_verifier_process_is_hidden_bounded_and_has_no_inherited_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("PWS_SIGNING_SECRET_CANARY", "must-not-inherit")
    script = {
        "success": "[Console]::Out.Write('ok:' + $env:PWS_SIGNING_SECRET_CANARY)",
        "timeout": "Start-Sleep -Seconds 15",
        "overflow": "[Console]::Out.Write(('x' * 65537))",
    }[kind]
    argv = [str(signing._system_powershell()), "-NoProfile", "-NonInteractive", "-Command", script]
    if kind == "success":
        assert signing._run_bounded(argv, cwd=tmp_path) == b"ok:"
    else:
        with pytest.raises(
            signing.WindowsSigningError,
            match=f"SIGNING_PROCESS_{kind.upper() if kind == 'timeout' else 'OUTPUT'}",
        ):
            signing._run_bounded(argv, cwd=tmp_path, timeout=0.1 if kind == "timeout" else 30)


@pytest.mark.skipif(os.name != "nt", reason="Windows build process boundary")
def test_build_process_merges_diagnostics_and_refuses_an_unbounded_timeout(tmp_path: Path) -> None:
    argv = [
        str(signing._system_powershell()),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[Console]::Out.Write('out'); [Console]::Error.Write('err')",
    ]
    output = signing.run_build_process(argv, tmp_path, timeout=30)
    assert output == b"outerr"
    with pytest.raises(signing.WindowsSigningError, match="SIGNING_PROCESS_LIMIT"):
        signing.run_build_process(argv, tmp_path, timeout=601)
