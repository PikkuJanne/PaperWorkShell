"""Verify update signatures with the Windows CNG primitive provider."""

from __future__ import annotations

import ctypes
import hashlib
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

P256_PUBLIC_KEY_BYTES: Final = 64
P256_SIGNATURE_BYTES: Final = 64
_ECDSA_PUBLIC_BLOB_HEADER: Final = b"ECS1" + struct.pack("<I", 32)
_SYSTEM32: Final = 0x00000800
_MICROSOFT_PRIMITIVE_PROVIDER: Final = "Microsoft Primitive Provider"


def _bcrypt() -> ctypes.WinDLL:
    bcrypt = ctypes.WinDLL("bcrypt.dll", winmode=_SYSTEM32)
    handle = ctypes.c_void_p
    size = ctypes.c_ulong
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(handle),
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        size,
    ]
    bcrypt.BCryptImportKeyPair.argtypes = [
        handle,
        handle,
        ctypes.c_wchar_p,
        ctypes.POINTER(handle),
        ctypes.c_char_p,
        size,
        size,
    ]
    bcrypt.BCryptVerifySignature.argtypes = [
        handle,
        ctypes.c_void_p,
        ctypes.c_char_p,
        size,
        ctypes.c_char_p,
        size,
        size,
    ]
    bcrypt.BCryptDestroyKey.argtypes = [handle]
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [handle, size]
    for function in (
        bcrypt.BCryptOpenAlgorithmProvider,
        bcrypt.BCryptImportKeyPair,
        bcrypt.BCryptVerifySignature,
        bcrypt.BCryptDestroyKey,
        bcrypt.BCryptCloseAlgorithmProvider,
    ):
        function.restype = ctypes.c_long
    return bcrypt


@contextmanager
def _import_key(public_key: bytes) -> Iterator[tuple[ctypes.WinDLL, ctypes.c_void_p] | None]:
    if sys.platform != "win32" or len(public_key) != P256_PUBLIC_KEY_BYTES:
        yield None
        return
    try:
        bcrypt = _bcrypt()
    except AttributeError, OSError:
        yield None
        return

    provider = ctypes.c_void_p()
    status = bcrypt.BCryptOpenAlgorithmProvider(
        ctypes.byref(provider), "ECDSA_P256", _MICROSOFT_PRIMITIVE_PROVIDER, 0
    )
    if status != 0:
        yield None
        return

    try:
        key = ctypes.c_void_p()
        blob = _ECDSA_PUBLIC_BLOB_HEADER + public_key
        status = bcrypt.BCryptImportKeyPair(
            provider, None, "ECCPUBLICBLOB", ctypes.byref(key), blob, len(blob), 0
        )
        if status != 0:
            yield None
            return
        try:
            yield bcrypt, key
        finally:
            bcrypt.BCryptDestroyKey(key)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(provider, 0)


def is_p256_public_key(public_key: bytes) -> bool:
    """Check that CNG admits the two 32-byte big-endian public coordinates."""
    with _import_key(public_key) as imported:
        return imported is not None


def verify_p256_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify SHA-256 with a fixed-size big-endian r||s ECDSA signature."""
    if len(signature) != P256_SIGNATURE_BYTES:
        return False
    with _import_key(public_key) as imported:
        if imported is None:
            return False
        bcrypt, key = imported
        digest = hashlib.sha256(message).digest()
        status = bcrypt.BCryptVerifySignature(
            key, None, digest, len(digest), signature, len(signature), 0
        )
        return bool(status == 0)
