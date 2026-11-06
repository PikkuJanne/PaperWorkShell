"""Bounded Windows signing handoff with pinned public policy and exact PE binding.

This tool never enrolls certificates, reads private keys, uploads artifacts or
accepts credentials. Signing is an explicit maintainer-owned executable call or
an offline handoff. A marker, detached signature or unverified return code cannot
replace native Authenticode, CMS, timestamp and original-image verification.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Final, Never, cast

from tools.dev import (
    _assign_windows_kill_job,
    _resume_windows_process,
    _terminate_and_close_windows_job,
)
from tools.release_artifacts import CHUNK_BYTES, MAX_ARTIFACT_BYTES, safe_relative

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
APPLICATION_ID: Final = "io.github.pikkujanne.paperworkshell"
MAX_CERTIFICATE_BYTES: Final = 4 * 1024 * 1024
MAX_REQUEST_FILES: Final = 32
MAX_PROCESS_OUTPUT: Final = 64 * 1024
PROCESS_TIMEOUT_SECONDS: Final = 120
_HASH: Final = re.compile(r"[0-9a-f]{64}\Z")
_THUMBPRINT: Final = re.compile(r"[0-9a-f]{40}\Z")
_UNSAFE_ATTRIBUTES: Final = 0x400 | 0x1000 | 0x40000 | 0x400000
_SHA256_OID: Final = bytes.fromhex("608648016503040201")
_TIMESTAMP_SIGNATURE_DIGESTS: Final = frozenset(
    {_SHA256_OID, bytes.fromhex("608648016503040202"), bytes.fromhex("608648016503040203")}
)
_SIGNED_DATA_OID: Final = bytes.fromhex("2a864886f70d010702")
_SPC_OID: Final = bytes.fromhex("2b060104018237020104")
_TST_INFO_OID: Final = bytes.fromhex("2a864886f70d0109100104")
_RFC3161_OID: Final = bytes.fromhex("2b060104018237030301")


class WindowsSigningError(ValueError):
    """Only a closed code is exposed; paths and native diagnostics stay private."""


def _fail(code: str) -> Never:
    raise WindowsSigningError(code)


@dataclass(frozen=True)
class SigningPolicy:
    policy_sha256: str
    certificate_subject: str
    certificate_sha256: str
    certificate_thumbprint: str


def _plain(path: Path, *, directory: bool = False) -> os.stat_result:
    if not path.is_absolute() or len(path.parts) > 64 or ".." in path.parts:
        _fail("SIGNING_PATH")
    metadata: os.stat_result | None = None
    try:
        for item in (*reversed(path.parents), path):
            metadata = item.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & _UNSAFE_ATTRIBUTES
            ):
                _fail("SIGNING_PATH")
            if item != path or directory:
                if not stat.S_ISDIR(metadata.st_mode):
                    _fail("SIGNING_PATH")
            elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                _fail("SIGNING_PATH")
    except OSError, ValueError:
        metadata = None
    if metadata is None:
        _fail("SIGNING_PATH")
    return metadata


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_nlink,
        getattr(metadata, "st_file_attributes", 0),
    )


def _hash(path: Path, maximum: int = MAX_ARTIFACT_BYTES) -> tuple[int, str]:
    before = _plain(path)
    if not 0 < before.st_size <= maximum:
        _fail("SIGNING_LIMIT")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            _fail("SIGNING_CHANGED")
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size > maximum:
                _fail("SIGNING_LIMIT")
            digest.update(chunk)
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            _fail("SIGNING_CHANGED")
    if size != before.st_size or _identity(_plain(path)) != _identity(before):
        _fail("SIGNING_CHANGED")
    return size, digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("SIGNING_JSON")
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, Any]:
    value: object = None
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    except UnicodeError, ValueError, RecursionError:
        pass
    if not isinstance(value, dict):
        _fail("SIGNING_JSON")
    return cast(dict[str, Any], value)


def _read_json(path: Path, maximum: int) -> tuple[dict[str, Any], str]:
    size, digest = _hash(path, maximum)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        _fail("SIGNING_CHANGED")
    return _json(raw), digest


def load_policy(path: Path, expected_sha256: str) -> SigningPolicy:
    """Require an out-of-band retained hash of a maintainer-approved public policy."""

    if _HASH.fullmatch(expected_sha256) is None:
        _fail("SIGNING_POLICY")
    policy, actual = _read_json(path.absolute(), 4096)
    keys = {
        "schema_version",
        "application_id",
        "approval",
        "certificate_subject",
        "certificate_sha256",
        "certificate_thumbprint",
        "digest_algorithm",
        "timestamp_digest_algorithm",
        "timestamp_required",
    }
    if (
        actual != expected_sha256
        or set(policy) != keys
        or type(policy["schema_version"]) is not int
        or policy["schema_version"] != 1
        or policy["application_id"] != APPLICATION_ID
        or policy["approval"] != "maintainer-approved"
        or policy["digest_algorithm"] != "sha256"
        or policy["timestamp_digest_algorithm"] != "sha256"
        or policy["timestamp_required"] is not True
        or type(policy["certificate_subject"]) is not str
        or not 1 <= len(policy["certificate_subject"]) <= 512
        or any(ord(char) < 32 for char in policy["certificate_subject"])
        or type(policy["certificate_sha256"]) is not str
        or _HASH.fullmatch(policy["certificate_sha256"]) is None
        or policy["certificate_sha256"] == "0" * 64
        or type(policy["certificate_thumbprint"]) is not str
        or _THUMBPRINT.fullmatch(policy["certificate_thumbprint"]) is None
        or policy["certificate_thumbprint"] == "0" * 40
    ):
        _fail("SIGNING_POLICY")
    return SigningPolicy(
        actual,
        policy["certificate_subject"],
        policy["certificate_sha256"],
        policy["certificate_thumbprint"],
    )


def _candidate_root(path: Path, root: Path) -> Path:
    absolute = path.absolute()
    _plain(absolute, directory=True)
    allowed = root.absolute() / "output" / "pws0802"
    if absolute == allowed or not absolute.is_relative_to(allowed):
        _fail("SIGNING_SCOPE")
    return absolute


def _candidate_file(candidate: Path, relative: str) -> Path:
    try:
        safe_relative(relative)
    except ValueError:
        _fail("SIGNING_PATH")
    path = candidate / relative
    if path.suffix.lower() not in {".exe", ".e32", ".e64"}:
        _fail("SIGNING_PATH")
    _plain(path)
    return path


def _output(candidate: Path, path: Path) -> Path:
    absolute = path.absolute()
    if absolute == candidate or not absolute.is_relative_to(candidate):
        _fail("SIGNING_SCOPE")
    _plain(absolute.parent, directory=True)
    if absolute.exists() or absolute.is_symlink():
        _fail("SIGNING_EXISTS")
    return absolute


@dataclass(frozen=True)
class _Pe:
    size: int
    checksum_offset: int
    directory_offset: int
    certificate_offset: int
    certificate_size: int


def _at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    result = stream.read(size)
    if len(result) != size:
        _fail("SIGNING_PE")
    return result


def _pe(path: Path) -> _Pe:
    size = _plain(path).st_size
    if not 512 <= size <= MAX_ARTIFACT_BYTES:
        _fail("SIGNING_LIMIT")
    with path.open("rb") as stream:
        header = _at(stream, 0, 64)
        offset = struct.unpack_from("<I", header, 60)[0]
        if header[:2] != b"MZ" or not 64 <= offset <= min(size - 256, 1024 * 1024):
            _fail("SIGNING_PE")
        header = _at(stream, offset, 24)
        optional_size = struct.unpack_from("<H", header, 20)[0]
        if header[:4] != b"PE\0\0" or not 144 <= optional_size <= 4096:
            _fail("SIGNING_PE")
        optional = _at(stream, offset + 24, optional_size)
        magic = struct.unpack_from("<H", optional)[0]
        if magic not in {0x10B, 0x20B}:
            _fail("SIGNING_PE")
        directories = 96 if magic == 0x10B else 112
        if (
            len(optional) < directories + 40
            or struct.unpack_from("<I", optional, directories - 4)[0] < 5
        ):
            _fail("SIGNING_PE")
        certificate, length = struct.unpack_from("<II", optional, directories + 32)
        if bool(certificate) != bool(length) or (
            length
            and (
                certificate < offset + 24 + optional_size
                or certificate % 8
                or not 8 <= length <= MAX_CERTIFICATE_BYTES
                or certificate + length != size
            )
        ):
            _fail("SIGNING_PE")
        return _Pe(size, offset + 24 + 64, offset + 24 + directories + 32, certificate, length)


def _image_hash(path: Path, pe: _Pe, original_size: int) -> str:
    if not 512 <= original_size <= pe.size:
        _fail("SIGNING_IMAGE")
    if pe.certificate_size:
        padding = pe.certificate_offset - original_size
        if not 0 <= padding <= 7:
            _fail("SIGNING_IMAGE")
        with path.open("rb") as stream:
            if any(_at(stream, original_size, padding)):
                _fail("SIGNING_IMAGE")
    elif original_size != pe.size:
        _fail("SIGNING_IMAGE")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        position = 0
        for offset, excluded_size in ((pe.checksum_offset, 4), (pe.directory_offset, 8)):
            if offset < position or offset + excluded_size > original_size:
                _fail("SIGNING_IMAGE")
            remaining = offset - position
            while remaining:
                block = stream.read(min(CHUNK_BYTES, remaining))
                if not block:
                    _fail("SIGNING_IMAGE")
                digest.update(block)
                remaining -= len(block)
            stream.seek(excluded_size, os.SEEK_CUR)
            digest.update(bytes(excluded_size))
            position = offset + excluded_size
        remaining = original_size - position
        while remaining:
            block = stream.read(min(CHUNK_BYTES, remaining))
            if not block:
                _fail("SIGNING_IMAGE")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _unsigned_entry(candidate: Path, relative: str) -> dict[str, Any]:
    path = _candidate_file(candidate, relative)
    size, digest = _hash(path)
    pe = _pe(path)
    if pe.certificate_size:
        _fail("SIGNING_ALREADY_SIGNED")
    image = _image_hash(path, pe, size)
    if _hash(path) != (size, digest):
        _fail("SIGNING_CHANGED")
    return {
        "path": relative,
        "original_size_bytes": size,
        "original_sha256": digest,
        "image_sha256": image,
    }


def prepare(
    candidate_root: Path,
    paths: Sequence[str],
    policy: SigningPolicy,
    output: Path,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Write a new immutable request; retain its returned hash before handoff."""

    candidate = _candidate_root(candidate_root, root)
    target = _output(candidate, output)
    if not 1 <= len(paths) <= MAX_REQUEST_FILES or len({p.casefold() for p in paths}) != len(paths):
        _fail("SIGNING_SELECTION")
    entries = [_unsigned_entry(candidate, relative) for relative in paths]
    request = {
        "schema_version": 1,
        "application_id": APPLICATION_ID,
        "policy_sha256": policy.policy_sha256,
        "files": entries,
    }
    raw = _json_bytes(request)
    with target.open("xb") as stream:
        stream.write(raw)
    return {
        "status": "prepared",
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "files": entries,
    }


@dataclass(frozen=True)
class _Der:
    tag: int
    content: bytes
    encoded: bytes

    def children(self) -> list[_Der]:
        return _der_nodes(self.content)


def _der_nodes(raw: bytes) -> list[_Der]:
    result: list[_Der] = []
    position = 0
    while position < len(raw):
        start = position
        if len(result) >= 4096 or position + 2 > len(raw):
            _fail("SIGNING_DER")
        tag, length = raw[position], raw[position + 1]
        position += 2
        if tag & 0x1F == 0x1F:
            _fail("SIGNING_DER")
        if length & 0x80:
            count = length & 0x7F
            if not 1 <= count <= 4 or position + count > len(raw) or raw[position] == 0:
                _fail("SIGNING_DER")
            length = int.from_bytes(raw[position : position + count])
            position += count
            if length < 128:
                _fail("SIGNING_DER")
        end = position + length
        if end > len(raw):
            _fail("SIGNING_DER")
        result.append(_Der(tag, raw[position:end], raw[start:end]))
        position = end
    return result


def _one(raw: bytes, tag: int) -> _Der:
    nodes = _der_nodes(raw)
    if len(nodes) != 1 or nodes[0].tag != tag:
        _fail("SIGNING_DER")
    return nodes[0]


def _algorithm(node: _Der, allowed: frozenset[bytes] = frozenset({_SHA256_OID})) -> None:
    values = node.children()
    if node.tag != 0x30 or not values or values[0].tag != 6 or values[0].content not in allowed:
        _fail("SIGNING_DIGEST")


def _cms(raw: bytes, content_type: bytes) -> tuple[bytes, list[_Der]]:
    wrapper = _one(raw, 0x30).children()
    if (
        len(wrapper) != 2
        or wrapper[0].tag != 6
        or wrapper[0].content != _SIGNED_DATA_OID
        or wrapper[1].tag != 0xA0
    ):
        _fail("SIGNING_CMS")
    signed = _one(wrapper[1].content, 0x30).children()
    if len(signed) < 4 or signed[1].tag != 0x31 or signed[-1].tag != 0x31:
        _fail("SIGNING_CMS")
    algorithms = signed[1].children()
    if len(algorithms) != 1:
        _fail("SIGNING_DIGEST")
    # /td sha256 controls the RFC3161 message imprint. The TSA's own CMS
    # signature may independently use a stronger SHA-384 or SHA-512 digest.
    allowed = (
        _TIMESTAMP_SIGNATURE_DIGESTS if content_type == _TST_INFO_OID else frozenset({_SHA256_OID})
    )
    _algorithm(algorithms[0], allowed)
    content = signed[2].children()
    signers = signed[-1].children()
    if (
        len(content) != 2
        or content[0].tag != 6
        or content[0].content != content_type
        or content[1].tag != 0xA0
        or len(signers) != 1
        or signers[0].tag != 0x30
    ):
        _fail("SIGNING_CMS")
    enclosed = _der_nodes(content[1].content)
    if len(enclosed) != 1:
        _fail("SIGNING_CMS")
    payload = enclosed[0].content if enclosed[0].tag == 4 else enclosed[0].encoded
    signer = signers[0].children()
    if not 5 <= len(signer) <= 7:
        _fail("SIGNING_CMS")
    _algorithm(signer[2], allowed)
    return payload, signer


def _signature(signer: list[_Der]) -> bytes:
    position = 5 if signer[3].tag == 0xA0 else 4
    if signer[position].tag != 4 or not signer[position].content:
        _fail("SIGNING_CMS")
    return signer[position].content


def _certificate_cms(path: Path, pe: _Pe) -> bytes:
    if not pe.certificate_size:
        _fail("SIGNING_NOT_SIGNED")
    with path.open("rb") as stream:
        certificate = _at(stream, pe.certificate_offset, pe.certificate_size)
    length, revision, kind = struct.unpack_from("<IHH", certificate)
    if (
        revision != 0x200
        or kind != 2
        or not 8 < length <= len(certificate)
        or len(certificate) - length > 7
        or any(certificate[length:])
    ):
        _fail("SIGNING_CERTIFICATE")
    code_cms = certificate[8:length]
    # Windows signers may include the alignment bytes in dwLength itself.
    # Accept only the one terminal DER object and at most seven zero pad bytes.
    if len(code_cms) < 2 or code_cms[0] != 0x30:
        _fail("SIGNING_DER")
    count = (code_cms[1] & 0x7F) if code_cms[1] & 0x80 else 0
    if count > 4 or 2 + count > len(code_cms):
        _fail("SIGNING_DER")
    content_size = int.from_bytes(code_cms[2 : 2 + count]) if count else code_cms[1]
    encoded_size = 2 + count + content_size
    if not 0 <= len(certificate) - 8 - encoded_size <= 7 or any(code_cms[encoded_size:]):
        _fail("SIGNING_DER")
    return code_cms[:encoded_size]


def _cms_evidence(path: Path, pe: _Pe) -> dict[str, str]:
    code_cms = _certificate_cms(path, pe)
    content, signer = _cms(code_cms, _SPC_OID)
    indirect = _one(content, 0x30).children()
    if len(indirect) != 2:
        _fail("SIGNING_CMS")
    digest = indirect[1].children()
    if len(digest) != 2 or digest[1].tag != 4 or len(digest[1].content) != 32:
        _fail("SIGNING_DIGEST")
    _algorithm(digest[0])
    unsigned = [node for node in signer if node.tag == 0xA1]
    if len(unsigned) != 1:
        _fail("SIGNING_TIMESTAMP")
    timestamps: list[bytes] = []
    for attribute in unsigned[0].children():
        values = attribute.children()
        if len(values) != 2 or values[0].tag != 6 or values[1].tag != 0x31:
            _fail("SIGNING_CMS")
        oid = values[0].content
        if oid != _RFC3161_OID:
            _fail("SIGNING_UNSUPPORTED_SIGNATURE")
        if oid == _RFC3161_OID:
            tokens = values[1].children()
            if len(tokens) != 1:
                _fail("SIGNING_TIMESTAMP")
            timestamps.append(tokens[0].encoded)
    if len(timestamps) != 1:
        _fail("SIGNING_TIMESTAMP")
    timestamp_cms = timestamps[0]
    token_content, token_signer = _cms(timestamp_cms, _TST_INFO_OID)
    _signature(token_signer)
    token = _one(token_content, 0x30).children()
    if len(token) < 5 or token[4].tag != 0x18:
        _fail("SIGNING_TIMESTAMP")
    imprint = token[2].children()
    if len(imprint) != 2 or imprint[1].tag != 4:
        _fail("SIGNING_TIMESTAMP")
    _algorithm(imprint[0])
    if imprint[1].content != hashlib.sha256(_signature(signer)).digest():
        _fail("SIGNING_TIMESTAMP_BINDING")
    try:
        text = token[4].content.decode("ascii")
        if re.fullmatch(r"[0-9]{14}(?:\.[0-9]{1,9})?Z", text) is None:
            _fail("SIGNING_TIMESTAMP")
        timestamp = datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except UnicodeError, ValueError:
        _fail("SIGNING_TIMESTAMP")
    if timestamp > datetime.now(UTC) + timedelta(minutes=5):
        _fail("SIGNING_TIMESTAMP")
    return {
        "cms": base64.b64encode(code_cms).decode("ascii"),
        "timestamp_cms": base64.b64encode(timestamp_cms).decode("ascii"),
        "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
    }


# Native cache-only WinTrust is important: Get-AuthenticodeSignature exposes no
# switch that prevents network retrieval. Explicit online build verification may
# retrieve public certificate/revocation metadata, never the candidate bytes.
# https://learn.microsoft.com/windows/win32/api/wintrust/ns-wintrust-wintrust_data
_WINTRUST_SCRIPT: Final = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PaperWorkShellBuildTrust {
  [StructLayout(LayoutKind.Sequential)]
  public struct FileTime { public UInt32 Low, High; }
  [StructLayout(LayoutKind.Sequential)]
  public struct ProviderSigner {
    public UInt32 Size; public FileTime VerifyTime; public UInt32 CertCount;
    public IntPtr Certs; public UInt32 Type; public IntPtr Signer;
    public UInt32 Error, CounterCount; public IntPtr Counters, Chain;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct FileInfo {
    public UInt32 Size; [MarshalAs(UnmanagedType.LPWStr)] public string Path;
    public IntPtr File; public IntPtr Subject;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct SignatureSettings {
    public UInt32 Size, Index, Flags, SecondaryCount, VerifiedIndex; public IntPtr Policy;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public class TrustData {
    public UInt32 Size; public IntPtr Policy, Sip; public UInt32 Ui, Revocation, Choice;
    public IntPtr File; public UInt32 State; public IntPtr StateData, Url;
    public UInt32 Flags, Context; public IntPtr Signature;
  }
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern IntPtr LoadLibraryExW(string name, IntPtr file, UInt32 flags);
  [DllImport("wintrust.dll", ExactSpelling=true, PreserveSig=true)]
  static extern Int32 WinVerifyTrust(IntPtr window, ref Guid action, [In, Out] TrustData data);
  [DllImport("wintrust.dll", ExactSpelling=true)]
  static extern IntPtr WTHelperProvDataFromStateData(IntPtr state);
  [DllImport("wintrust.dll", ExactSpelling=true)]
  static extern IntPtr WTHelperGetProvSignerFromChain(IntPtr data, UInt32 signer, bool counter, UInt32 index);
  [DllImport("wintrust.dll", ExactSpelling=true)]
  static extern IntPtr WTHelperGetProvCertFromChain(IntPtr signer, UInt32 index);
  public static string Check(string path, bool online, bool timestampRequired) {
    if (LoadLibraryExW(System.IO.Path.Combine(Environment.SystemDirectory,"wintrust.dll"),IntPtr.Zero,0x800)==IntPtr.Zero)
      throw new InvalidOperationException("native verifier unavailable");
    var file = new FileInfo { Size=(UInt32)Marshal.SizeOf(typeof(FileInfo)), Path=path };
    var signature = new SignatureSettings { Size=(UInt32)Marshal.SizeOf(typeof(SignatureSettings)), Flags=1, Index=0 };
    IntPtr filePointer=Marshal.AllocHGlobal(Marshal.SizeOf(file));
    IntPtr signaturePointer=Marshal.AllocHGlobal(Marshal.SizeOf(signature));
    Marshal.StructureToPtr(file,filePointer,false);
    Marshal.StructureToPtr(signature,signaturePointer,false);
    var data=new TrustData { Size=(UInt32)Marshal.SizeOf(typeof(TrustData)), Ui=2, Revocation=1,
      Choice=1, File=filePointer, State=1, Flags=0x80u|0x2000u|(online?0u:0x1000u), Signature=signaturePointer };
    Guid action=new Guid("00AAC56B-CD44-11D0-8CC2-00C04FC295EE");
    try {
      Int32 status=WinVerifyTrust(new IntPtr(-1),ref action,data);
      if (status==0) {
        if (!timestampRequired) return "valid";
        IntPtr provider=WTHelperProvDataFromStateData(data.StateData);
        IntPtr primaryPointer=WTHelperGetProvSignerFromChain(provider,0,false,0);
        if (primaryPointer==IntPtr.Zero) return "invalid";
        if (Marshal.ReadInt32(primaryPointer)<Marshal.SizeOf(typeof(ProviderSigner))) return "invalid";
        var primary=(ProviderSigner)Marshal.PtrToStructure(primaryPointer,typeof(ProviderSigner));
        if (primary.Error!=0 || primary.CounterCount!=1) return "invalid";
        IntPtr counterPointer=WTHelperGetProvSignerFromChain(provider,0,true,0);
        if (counterPointer==IntPtr.Zero) return "invalid";
        if (Marshal.ReadInt32(counterPointer)<Marshal.SizeOf(typeof(ProviderSigner))) return "invalid";
        var counter=(ProviderSigner)Marshal.PtrToStructure(counterPointer,typeof(ProviderSigner));
        if (counter.Error!=0 || counter.CertCount==0 || counter.Chain==IntPtr.Zero ||
            Marshal.ReadInt32(counter.Chain)<12 || Marshal.ReadInt32(counter.Chain,4)!=0) return "invalid";
        IntPtr certificate=WTHelperGetProvCertFromChain(counterPointer,0);
        if (certificate==IntPtr.Zero) return "invalid";
        IntPtr context=Marshal.ReadIntPtr(certificate,IntPtr.Size==8?8:4);
        using (var cert=new System.Security.Cryptography.X509Certificates.X509Certificate2(context))
        using (var hash=System.Security.Cryptography.SHA256.Create())
          return "valid:"+BitConverter.ToString(hash.ComputeHash(cert.RawData)).Replace("-","").ToLowerInvariant();
      }
      return unchecked((UInt32)status)==0x800B0100 ? "not_signed" : "invalid";
    } finally {
      data.State=2; WinVerifyTrust(new IntPtr(-1),ref action,data);
      Marshal.DestroyStructure(filePointer,typeof(FileInfo)); Marshal.FreeHGlobal(filePointer);
      Marshal.DestroyStructure(signaturePointer,typeof(SignatureSettings)); Marshal.FreeHGlobal(signaturePointer);
    }
  }
}
'@
"""

# These scripts are constant. Only bounded JSON enters stdin; paths never become
# PowerShell source, command fragments, variables or expressions.
_VERIFY_SCRIPT: Final = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Security
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  $trust = [PaperWorkShellBuildTrust]::Check($r.path, $r.online_revocation, $true)
  if (-not $trust.StartsWith('valid:')) { throw 'signature' }
  $code = New-Object System.Security.Cryptography.Pkcs.SignedCms
  $code.Decode([Convert]::FromBase64String($r.cms))
  $code.CheckSignature($true)
  $token = New-Object System.Security.Cryptography.Pkcs.SignedCms
  $token.Decode([Convert]::FromBase64String($r.timestamp_cms))
  $token.CheckSignature($true)
  if ($code.SignerInfos.Count -ne 1 -or $token.SignerInfos.Count -ne 1) { throw 'count' }
  function Sha256($bytes) {
    $h = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($h.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $h.Dispose() }
  }
  $leaf = $code.SignerInfos[0].Certificate
  $tsa = $token.SignerInfos[0].Certificate
  if ($null -eq $leaf -or $null -eq $tsa -or (Sha256 $tsa.RawData) -ne $trust.Substring(6)) { throw 'identity' }
  $when = [DateTimeOffset]::Parse($r.timestamp_utc).UtcDateTime
  if ($when -lt $leaf.NotBefore.ToUniversalTime() -or $when -gt $leaf.NotAfter.ToUniversalTime()) { throw 'time' }
  [ordered]@{
    status='valid'; certificate_subject=$leaf.Subject;
    certificate_sha256=(Sha256 $leaf.RawData);
    certificate_thumbprint=$leaf.Thumbprint.ToLowerInvariant();
    timestamp_certificate_sha256=(Sha256 $tsa.RawData);
    timestamp_utc=$r.timestamp_utc; cms_valid=$true;
    timestamp_signature_valid=$true; timestamp_chain_valid=$true
  } | ConvertTo-Json -Compress
} catch {
  [Console]::Out.WriteLine('{"status":"error"}')
  exit 1
}
"""

_INSPECT_SCRIPT: Final = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Security
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  $status = [PaperWorkShellBuildTrust]::Check($r.path, $r.online_revocation, $false)
  $subject = $null; $digest = $null; $thumbprint = $null
  $hasTimestamp = $false
  if ($null -ne $r.cms) {
    $cms = New-Object System.Security.Cryptography.Pkcs.SignedCms
    $cms.Decode([Convert]::FromBase64String($r.cms))
    $leaf = $cms.SignerInfos[0].Certificate
    $subject = $leaf.Subject
    $h = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = ([BitConverter]::ToString($h.ComputeHash($leaf.RawData))).Replace('-','').ToLowerInvariant() }
    finally { $h.Dispose() }
    $thumbprint = $leaf.Thumbprint.ToLowerInvariant()
    foreach ($attribute in $cms.SignerInfos[0].UnsignedAttributes) {
      if ($attribute.Oid.Value -in @('1.3.6.1.4.1.311.3.3.1','1.2.840.113549.1.9.6')) { $hasTimestamp = $true }
    }
  }
  [ordered]@{status=$status;subject=$subject;certificate_sha256=$digest;
    thumbprint=$thumbprint;timestamp_present=$hasTimestamp} | ConvertTo-Json -Compress
} catch {
  [Console]::Out.WriteLine('{"status":"error"}')
  exit 1
}
"""


def _system_powershell() -> Path:
    if os.name != "nt":
        _fail("SIGNING_WINDOWS_REQUIRED")
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    get_directory = kernel.GetSystemDirectoryW
    get_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        _fail("SIGNING_VERIFIER_UNAVAILABLE")
    directory = Path(buffer.value)
    path = directory / "WindowsPowerShell/v1.0/powershell.exe"
    if not path.is_file():
        _fail("SIGNING_VERIFIER_UNAVAILABLE")
    return path


def _capture_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    timeout: float = PROCESS_TIMEOUT_SECONDS,
    maximum_seconds: int = PROCESS_TIMEOUT_SECONDS,
    output_limit: int = MAX_PROCESS_OUTPUT,
    merge_stderr: bool = False,
    successful_descendant_grace_seconds: float = 0,
) -> bytes:
    if (
        (maximum_seconds, output_limit, merge_stderr)
        not in {
            (PROCESS_TIMEOUT_SECONDS, MAX_PROCESS_OUTPUT, False),
            (600, 2 * 1024 * 1024, True),
        }
        or not 0 < timeout <= maximum_seconds
        or type(successful_descendant_grace_seconds) not in {int, float}
        or not 0 <= successful_descendant_grace_seconds <= 30
        or successful_descendant_grace_seconds > 0
        and (maximum_seconds, output_limit, merge_stderr) != (600, 2 * 1024 * 1024, True)
        or input_bytes is not None
        and len(input_bytes) > 3 * MAX_CERTIFICATE_BYTES + 8192
    ):
        _fail("SIGNING_PROCESS_LIMIT")
    system = _system_powershell().parents[2]
    environment = {
        "SystemRoot": str(system.parent),
        "WINDIR": str(system.parent),
        "PATH": str(system),
        "TEMP": str(cwd),
        "TMP": str(cwd),
        "PSModulePath": str(system / "WindowsPowerShell/v1.0/Modules"),
    }
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        shell=False,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
        env=environment,
        creationflags=0x00000004 | 0x08000000,
        bufsize=0,
    )
    job: int | None = None
    output = bytearray()
    overflow = threading.Event()
    failed = threading.Event()

    def read() -> None:
        try:
            assert process.stdout is not None
            while len(output) <= output_limit:
                chunk = process.stdout.read(min(4096, output_limit + 1 - len(output)))
                if not chunk:
                    return
                output.extend(chunk)
            overflow.set()
        except OSError:
            failed.set()

    def write() -> None:
        if input_bytes is not None:
            try:
                assert process.stdin is not None
                with process.stdin:
                    view = memoryview(input_bytes)
                    while view:
                        count = process.stdin.write(view)
                        if not count:
                            raise OSError
                        view = view[count:]
            except OSError:
                failed.set()

    reader = threading.Thread(target=read, name="pws-signing-output", daemon=True)
    writer = threading.Thread(target=write, name="pws-signing-input", daemon=True)
    code = -1
    timed_out = False
    cleanup_failed = False
    try:
        job = _assign_windows_kill_job(process)
        reader.start()
        writer.start()
        _resume_windows_process(process)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if overflow.is_set() or failed.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
        observed_code = process.poll()
        code = observed_code if observed_code is not None else -1
        if code == 0 and successful_descendant_grace_seconds and job is not None:
            # Inno's second phase deletes its first-phase executable only after
            # that process exits. Keep descendants contained until they finish;
            # closed stdout alone is not evidence that the job is empty.
            grace_deadline = min(deadline, time.monotonic() + successful_descendant_grace_seconds)
            while not overflow.is_set() and not failed.is_set():
                if _job_active_process_count(job) == 0:
                    break
                if time.monotonic() >= grace_deadline:
                    timed_out = True
                    break
                time.sleep(0.01)
    finally:
        if job is not None:
            try:
                _terminate_and_close_windows_job(job)
            except OSError:
                cleanup_failed = True
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup_failed = True
        if reader.ident is not None:
            reader.join(timeout=5)
        if writer.ident is not None:
            writer.join(timeout=5)
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()
    if cleanup_failed or reader.is_alive() or writer.is_alive():
        _fail("SIGNING_PROCESS_CLEANUP")
    if timed_out:
        _fail("SIGNING_PROCESS_TIMEOUT")
    if overflow.is_set() or len(output) > output_limit:
        _fail("SIGNING_PROCESS_OUTPUT")
    if failed.is_set() or code != 0:
        _fail("SIGNING_PROCESS_FAILED")
    return bytes(output)


def _run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    timeout: float = PROCESS_TIMEOUT_SECONDS,
) -> bytes:
    return _capture_process(argv, cwd=cwd, input_bytes=input_bytes, timeout=timeout)


def _job_active_process_count(job_handle: int) -> int:
    """Read the held job's count, without following process IDs or child paths."""

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("total_user_time", ctypes.c_longlong),
            ("total_kernel_time", ctypes.c_longlong),
            ("period_user_time", ctypes.c_longlong),
            ("period_kernel_time", ctypes.c_longlong),
            ("page_faults", ctypes.c_ulong),
            ("total_processes", ctypes.c_ulong),
            ("active_processes", ctypes.c_ulong),
            ("terminated_processes", ctypes.c_ulong),
        ]

    query = ctypes.WinDLL("kernel32", use_last_error=True).QueryInformationJobObject
    query.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    )
    query.restype = ctypes.c_int
    information = Accounting()
    if not query(job_handle, 1, ctypes.byref(information), ctypes.sizeof(information), None):
        _fail("SIGNING_PROCESS_CLEANUP")
    return int(information.active_processes)


def run_build_process(
    argv: Sequence[str],
    cwd: Path,
    timeout: float = 600,
    *,
    successful_descendant_grace_seconds: float = 0,
) -> bytes:
    """Bound the two trusted installer build callers to 600 seconds / 2 MiB.

    Uses the same suspended/job-contained process boundary as signing, with
    merged compiler diagnostics. An explicit success-only grace of at most
    30 seconds remains inside the original deadline and kill-on-close job.
    Signing's smaller limits and all default callers remain unchanged.
    """

    return _capture_process(
        argv,
        cwd=cwd,
        timeout=timeout,
        maximum_seconds=600,
        output_limit=2 * 1024 * 1024,
        merge_stderr=True,
        successful_descendant_grace_seconds=successful_descendant_grace_seconds,
    )


def _native_verify(
    path: Path,
    evidence: Mapping[str, str],
    candidate: Path,
    *,
    online_revocation: bool = False,
) -> dict[str, Any]:
    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    executable = _system_powershell()
    encoded = base64.b64encode((_WINTRUST_SCRIPT + _VERIFY_SCRIPT).encode("utf-16-le")).decode(
        "ascii"
    )
    raw = _run_bounded(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=candidate,
        input_bytes=_json_bytes(
            {"path": str(path), "online_revocation": online_revocation, **evidence}
        ),
    )
    return _json(raw)


def inspect_authenticode(path: Path, *, online_revocation: bool = False) -> dict[str, Any]:
    """Inspect an explicit upstream PE using Windows trust, without product approval.

    Callers must separately bind its official artifact hash, publisher and exact
    certificate identity. This result never qualifies a product signing handoff.
    """

    path = path.absolute()
    original = _hash(path)
    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    pe = _pe(path)
    cms = (
        base64.b64encode(_certificate_cms(path, pe)).decode("ascii")
        if pe.certificate_size
        else None
    )
    executable = _system_powershell()
    encoded = base64.b64encode((_WINTRUST_SCRIPT + _INSPECT_SCRIPT).encode("utf-16-le")).decode(
        "ascii"
    )
    result = _json(
        _run_bounded(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            cwd=path.parent,
            input_bytes=_json_bytes(
                {"path": str(path), "cms": cms, "online_revocation": online_revocation}
            ),
        )
    )
    if (
        set(result)
        != {"status", "subject", "certificate_sha256", "thumbprint", "timestamp_present"}
        or result["status"] not in {"valid", "not_signed", "invalid"}
        or type(result["timestamp_present"]) is not bool
    ):
        _fail("SIGNING_VERIFIER_RESULT")
    if result["status"] == "valid" and (
        type(result["subject"]) is not str
        or not 1 <= len(result["subject"]) <= 512
        or type(result["certificate_sha256"]) is not str
        or _HASH.fullmatch(result["certificate_sha256"]) is None
        or type(result["thumbprint"]) is not str
        or _THUMBPRINT.fullmatch(result["thumbprint"]) is None
    ):
        _fail("SIGNING_VERIFIER_RESULT")
    if _hash(path) != original:
        _fail("SIGNING_CHANGED")
    return result


def _verified_file(
    candidate: Path,
    path: Path,
    policy: SigningPolicy,
    entry: Mapping[str, Any] | None,
    online_revocation: bool = False,
) -> dict[str, Any]:
    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    size, digest = _hash(path)
    pe = _pe(path)
    evidence = _cms_evidence(path, pe)
    if (
        entry is not None
        and _image_hash(path, pe, entry["original_size_bytes"]) != entry["image_sha256"]
    ):
        _fail("SIGNING_IMAGE_BINDING")
    native = (
        _native_verify(path, evidence, candidate, online_revocation=True)
        if online_revocation
        else _native_verify(path, evidence, candidate)
    )
    if (
        native.get("status") != "valid"
        or native.get("certificate_subject") != policy.certificate_subject
        or native.get("certificate_sha256") != policy.certificate_sha256
        or native.get("certificate_thumbprint") != policy.certificate_thumbprint
        or type(native.get("timestamp_certificate_sha256")) is not str
        or _HASH.fullmatch(native["timestamp_certificate_sha256"]) is None
        or native.get("timestamp_utc") != evidence["timestamp_utc"]
        or native.get("cms_valid") is not True
        or native.get("timestamp_signature_valid") is not True
        or native.get("timestamp_chain_valid") is not True
    ):
        _fail("SIGNING_TRUST")
    if _hash(path) != (size, digest):
        _fail("SIGNING_CHANGED")
    return {
        "status": "verified",
        "application_id": APPLICATION_ID,
        "policy_sha256": policy.policy_sha256,
        "size_bytes": size,
        "sha256": digest,
        "certificate_sha256": policy.certificate_sha256,
        "certificate_thumbprint": policy.certificate_thumbprint,
        "timestamp_certificate_sha256": native["timestamp_certificate_sha256"],
        "timestamp_utc": evidence["timestamp_utc"],
        "image_binding": "verified" if entry is not None else "not_requested",
        "revocation_mode": "online" if online_revocation else "cache_only",
    }


def verify(
    candidate_root: Path,
    path: str,
    policy: SigningPolicy,
    *,
    root: Path = PROJECT_ROOT,
    online_revocation: bool = False,
) -> dict[str, Any]:
    candidate = _candidate_root(candidate_root, root)
    target = _candidate_file(candidate, path)
    return {"path": path, **_verified_file(candidate, target, policy, None, online_revocation)}


def _request_entry(
    request: Path,
    expected_sha256: str,
    relative: str,
    policy: SigningPolicy,
) -> dict[str, Any]:
    payload, actual = _read_json(request, 64 * 1024)
    if (
        actual != expected_sha256
        or set(payload) != {"schema_version", "application_id", "policy_sha256", "files"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["application_id"] != APPLICATION_ID
        or payload["policy_sha256"] != policy.policy_sha256
        or not isinstance(payload["files"], list)
        or not 1 <= len(payload["files"]) <= MAX_REQUEST_FILES
    ):
        _fail("SIGNING_REQUEST")
    selected: dict[str, Any] | None = None
    paths: set[str] = set()
    for item in payload["files"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "original_size_bytes", "original_sha256", "image_sha256"}
            or type(item["path"]) is not str
            or type(item["original_size_bytes"]) is not int
            or not 512 <= item["original_size_bytes"] <= MAX_ARTIFACT_BYTES
            or type(item["original_sha256"]) is not str
            or _HASH.fullmatch(item["original_sha256"]) is None
            or type(item["image_sha256"]) is not str
            or _HASH.fullmatch(item["image_sha256"]) is None
        ):
            _fail("SIGNING_REQUEST")
        safe_relative(item["path"])
        if item["path"].casefold() in paths:
            _fail("SIGNING_REQUEST")
        paths.add(item["path"].casefold())
        if item["path"] == relative:
            selected = item
    if selected is None:
        _fail("SIGNING_REQUEST")
    return selected


def _copy(source: Path, target: Path) -> None:
    size, digest = _hash(source)
    with source.open("rb") as stream, target.open("xb") as output:
        copied = 0
        while block := stream.read(CHUNK_BYTES):
            copied += len(block)
            if copied > size:
                _fail("SIGNING_CHANGED")
            output.write(block)
    if _hash(target) != (size, digest) or _hash(source) != (size, digest):
        _fail("SIGNING_CHANGED")


def _publish_signed(
    candidate: Path,
    source: Path,
    target: Path,
    policy: SigningPolicy,
    entry: Mapping[str, Any],
    online_revocation: bool = False,
) -> dict[str, Any]:
    receipt = _verified_file(candidate, source, policy, entry, online_revocation)
    if _hash(target) != (entry["original_size_bytes"], entry["original_sha256"]):
        _fail("SIGNING_ORIGINAL_CHANGED")
    os.replace(source, target)
    if _hash(target) != (receipt["size_bytes"], receipt["sha256"]):
        _fail("SIGNING_CHANGED")
    return {
        **receipt,
        "unsigned_original_sha256": entry["original_sha256"],
        "unsigned_image_sha256": entry["image_sha256"],
        "unsigned_size_bytes": entry["original_size_bytes"],
    }


def ingest(
    candidate_root: Path,
    path: str,
    signed_source: str,
    request: Path,
    expected_request_sha256: str,
    policy: SigningPolicy,
    *,
    root: Path = PROJECT_ROOT,
    online_revocation: bool = False,
) -> dict[str, Any]:
    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    candidate = _candidate_root(candidate_root, root)
    target = _candidate_file(candidate, path)
    source = _candidate_file(candidate, signed_source)
    if source == target or not request.absolute().is_relative_to(candidate):
        _fail("SIGNING_SCOPE")
    entry = _request_entry(request.absolute(), expected_request_sha256, path, policy)
    _verified_file(candidate, source, policy, entry, online_revocation)
    work = Path(tempfile.mkdtemp(prefix=".signing-", dir=candidate))
    temporary = work / target.name
    try:
        _copy(source, temporary)
        return {
            "path": path,
            **_publish_signed(candidate, temporary, target, policy, entry, online_revocation),
        }
    finally:
        if temporary.exists():
            _plain(temporary)
            temporary.unlink()
        work.rmdir()


def sign(
    candidate_root: Path,
    path: str,
    policy: SigningPolicy,
    signer_executable: Path,
    signer_sha256: str,
    *,
    root: Path = PROJECT_ROOT,
    online_revocation: bool = False,
) -> dict[str, Any]:
    """Invoke an authenticated signer with one owned candidate-copy argument only."""

    if type(online_revocation) is not bool:
        _fail("SIGNING_POLICY")
    candidate = _candidate_root(candidate_root, root)
    target = _candidate_file(candidate, path)
    if not signer_executable.is_absolute():
        _fail("SIGNING_SIGNER")
    signer = signer_executable.absolute()
    if signer.suffix.lower() != ".exe" or _HASH.fullmatch(signer_sha256) is None:
        _fail("SIGNING_SIGNER")
    signer_identity = _hash(signer)
    if signer_identity[1] != signer_sha256:
        _fail("SIGNING_SIGNER")
    entry = _unsigned_entry(candidate, path)
    work = Path(tempfile.mkdtemp(prefix=".signing-", dir=candidate))
    temporary = work / target.name
    try:
        _copy(target, temporary)
        _run_bounded([str(signer), str(temporary)], cwd=work)
        if _hash(signer) != signer_identity:
            _fail("SIGNING_SIGNER_CHANGED")
        return {
            "path": path,
            **_publish_signed(candidate, temporary, target, policy, entry, online_revocation),
        }
    finally:
        if temporary.exists():
            _plain(temporary)
            temporary.unlink()
        work.rmdir()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        _fail("SIGNING_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("prepare", "verify", "sign", "ingest"):
        command = sub.add_parser(name)
        command.add_argument("--candidate-root", type=Path, required=True)
        command.add_argument("--policy", type=Path, required=True)
        command.add_argument("--policy-sha256", required=True)
        command.add_argument(
            "--file", action="append" if name == "prepare" else "store", required=True
        )
        if name == "prepare":
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument(
                "--online-revocation",
                action="store_true",
                help="Allow Windows to retrieve public certificate/revocation metadata during this explicit build verification; never uploads candidate bytes.",
            )
        if name == "sign":
            command.add_argument("--signer", type=Path, required=True)
            command.add_argument("--signer-sha256", required=True)
        elif name == "ingest":
            command.add_argument("--signed-source", required=True)
            command.add_argument("--request", type=Path, required=True)
            command.add_argument("--request-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        policy = load_policy(args.policy, args.policy_sha256)
        if args.command == "prepare":
            result = prepare(args.candidate_root, args.file, policy, args.output)
        elif args.command == "verify":
            result = verify(
                args.candidate_root, args.file, policy, online_revocation=args.online_revocation
            )
        elif args.command == "sign":
            result = sign(
                args.candidate_root,
                args.file,
                policy,
                args.signer,
                args.signer_sha256,
                online_revocation=args.online_revocation,
            )
        else:
            result = ingest(
                args.candidate_root,
                args.file,
                args.signed_source,
                args.request,
                args.request_sha256,
                policy,
                online_revocation=args.online_revocation,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except WindowsSigningError as error:
        code = str(error)
    except OSError, ValueError, TypeError, KeyError, struct.error:
        code = "SIGNING_INPUT"
    print(f"ERROR [{code}] Windows signing could not be verified.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
