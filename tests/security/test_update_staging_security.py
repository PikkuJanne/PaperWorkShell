"""Synthetic tests for Windows update staging and the exact-byte lease."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from errno import ENOSPC
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import (
    SIGNATURE_PREFIX,
    UpdateErrorCode,
    UpdateVerificationError,
)
from paperworkshell.storage.update_staging import (
    StagedUpdate,
    UpdateStagingError,
    cleanup_interrupted_update_stages,
    stage_update_bundle,
)
from tests.security.test_case_workspace_security import _create_junction, _remove_junction

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows sharing required")

PACKAGE = b"synthetic staged update bytes"
NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "private-update-root"
    directory.mkdir(mode=0o700)
    return directory


@contextmanager
def _stage(
    root: Path,
    package: BinaryIO,
    *,
    trusted: bool = True,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[StagedUpdate]:
    metadata = json.dumps(
        {
            "schema_version": 1,
            "application_id": INSTALLED_APPLICATION_ID,
            "platform": "win-x64",
            "channel": "engineering",
            "version": "1.2.3",
            "candidate_id": "synthetic-stage-test",
            "sequence": 2,
            "issued_at": "2026-11-26T11:00:00Z",
            "expires_at": "2026-11-26T13:00:00Z",
            "package_size": len(PACKAGE),
            "package_sha256": hashlib.sha256(PACKAGE).hexdigest(),
        }
    ).encode("utf-8")
    key = Ed25519PrivateKey.generate()
    public = key.public_key()

    def verify(message: bytes, signature: bytes) -> bool:
        try:
            public.verify(signature, message)
        except InvalidSignature:
            return False
        return True

    signer = key if trusted else Ed25519PrivateKey.generate()
    with stage_update_bundle(
        root,
        metadata,
        signer.sign(SIGNATURE_PREFIX + metadata),
        package,
        verify_signature=verify,
        expected_channel="engineering",
        sequence_floor=1,
        now=NOW,
        checkpoint=checkpoint,
    ) as staged:
        yield staged


class _UnreadablePackage(io.BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        raise OSError("REDACTED_SOURCE_CANARY")


def test_exact_read_stream_and_native_write_delete_denial(root: Path) -> None:
    with _stage(root, io.BytesIO(PACKAGE)) as staged:
        path = staged.package_path
        assert path.name == "update-package.zip"
        assert path.parent.parent == root
        for _ in range(2):
            with staged.open_package() as stream:
                assert stream.read() == PACKAGE
        with pytest.raises(PermissionError):
            path.write_bytes(b"altered")
        with pytest.raises(PermissionError):
            path.unlink()
        with pytest.raises(PermissionError):
            path.parent.rename(root / "renamed")
        with pytest.raises(PermissionError):
            root.rename(root.with_name("renamed-root"))
        staged.assert_package_identity()

    assert not path.exists()
    assert list(root.iterdir()) == []
    with pytest.raises(UpdateStagingError, match="could not safely stage"):
        staged.assert_package_identity()


def test_bad_signature_precedes_staging_and_untrusted_reads(root: Path) -> None:
    with pytest.raises(UpdateVerificationError) as failure:
        with _stage(root, _UnreadablePackage(), trusted=False):
            pytest.fail("untrusted update received a staging lease")
    assert failure.value.code is UpdateErrorCode.SIGNATURE_INVALID
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("content", [PACKAGE[:-1], PACKAGE + b"!", b"!" + PACKAGE[1:]])
def test_failed_length_or_stored_digest_cleans_owned_stage(root: Path, content: bytes) -> None:
    with pytest.raises(UpdateVerificationError) as failure:
        with _stage(root, io.BytesIO(content)):
            pytest.fail("changed package received a staging lease")
    assert failure.value.code is UpdateErrorCode.PACKAGE_MISMATCH
    assert list(root.iterdir()) == []


def test_failed_package_read_has_sanitized_error_and_cleanup(root: Path) -> None:
    with pytest.raises(UpdateVerificationError) as failure:
        with _stage(root, _UnreadablePackage()):
            pytest.fail("unreadable package received a staging lease")
    assert failure.value.code is UpdateErrorCode.PACKAGE_UNREADABLE
    assert "REDACTED_SOURCE_CANARY" not in str(failure.value)
    assert list(root.iterdir()) == []


def test_cancellation_during_copy_cleans_owned_stage(root: Path) -> None:
    class Cancelled(Exception):
        pass

    class ShortReads(io.BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            assert size is not None and size > 0
            return super().read(min(size, 3))

    package = ShortReads(PACKAGE)

    def cancel() -> None:
        if package.tell() > 0:
            raise Cancelled

    with pytest.raises(Cancelled):
        with _stage(root, package, checkpoint=cancel):
            pytest.fail("canceled package received a staging lease")
    assert package.tell() == 3
    assert list(root.iterdir()) == []


def test_failed_flush_cleans_only_owned_stage(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unrelated = root / "unrelated-update-receipt.bin"
    unrelated.write_bytes(b"preserved")

    def no_space(_descriptor: int) -> None:
        raise OSError(ENOSPC, "PRIVATE_VOLUME_LABEL_CANARY")

    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(UpdateStagingError) as failure:
        with _stage(root, io.BytesIO(PACKAGE)):
            pytest.fail("uncommitted package received a staging lease")
    assert "PRIVATE_VOLUME_LABEL_CANARY" not in str(failure.value)
    assert list(root.iterdir()) == [unrelated]
    assert unrelated.read_bytes() == b"preserved"


def test_interrupted_consumer_cleans_owned_stage(root: Path) -> None:
    class Interrupted(Exception):
        pass

    with pytest.raises(Interrupted):
        with _stage(root, io.BytesIO(PACKAGE)):
            raise Interrupted
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("unsafe", ["relative-dir", r"\\server\share\stage", r"\\?\C:\stage"])
def test_rejects_unsupported_staging_paths(unsafe: str) -> None:
    with pytest.raises(UpdateStagingError):
        with _stage(Path(unsafe), io.BytesIO(PACKAGE)):
            pytest.fail("unsupported root received a staging lease")


def test_rejects_root_without_private_acl(tmp_path: Path) -> None:
    directory = tmp_path / "unprotected-root"
    directory.mkdir()
    with pytest.raises(UpdateStagingError):
        with _stage(directory, io.BytesIO(PACKAGE)):
            pytest.fail("unprotected root received a staging lease")
    assert list(directory.iterdir()) == []


def test_rejects_a_redirected_staging_ancestor(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    root = target / "private-update-root"
    root.mkdir(mode=0o700)
    redirect = tmp_path / "redirect"
    _create_junction(redirect, target)
    try:
        with pytest.raises(UpdateStagingError):
            with _stage(redirect / root.name, io.BytesIO(PACKAGE)):
                pytest.fail("redirected root received a staging lease")
        assert list(root.iterdir()) == []
    finally:
        _remove_junction(redirect)


def _interrupted_stage(root: Path) -> Path:
    directory = root / f"stage-{uuid4().hex}"
    directory.mkdir(mode=0o700)
    return directory


def test_recover_stage_left_by_process_exit_without_unwinding(root: Path) -> None:
    script = """\
import io
import os
import sys
from pathlib import Path
from tests.security.test_update_staging_security import PACKAGE, _stage

with _stage(Path(sys.argv[1]), io.BytesIO(PACKAGE)):
    os._exit(23)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 23
    (remaining,) = list(root.iterdir())
    assert (remaining / "update-package.zip").read_bytes() == PACKAGE
    _interrupted_stage(root)  # Also admit a process interrupted before file creation.

    cleanup_interrupted_update_stages(root)

    assert list(root.iterdir()) == []


def test_recovery_refuses_unexpected_member_without_discarding_any_stage(root: Path) -> None:
    valid = _interrupted_stage(root) / "update-package.zip"
    valid.write_bytes(PACKAGE)
    unexpected = _interrupted_stage(root) / "unowned.txt"
    unexpected.write_bytes(b"KEEP")

    with pytest.raises(UpdateStagingError):
        cleanup_interrupted_update_stages(root)

    assert valid.read_bytes() == PACKAGE
    assert unexpected.read_bytes() == b"KEEP"


def test_recovery_refuses_hardlinked_package(root: Path, tmp_path: Path) -> None:
    original = tmp_path / "outside-original.bin"
    original.write_bytes(b"KEEP")
    linked = _interrupted_stage(root) / "update-package.zip"
    os.link(original, linked)

    with pytest.raises(UpdateStagingError):
        cleanup_interrupted_update_stages(root)

    assert original.read_bytes() == linked.read_bytes() == b"KEEP"


def test_recovery_refuses_a_live_stage_lease(root: Path) -> None:
    with _stage(root, io.BytesIO(PACKAGE)) as staged:
        with pytest.raises(UpdateStagingError):
            cleanup_interrupted_update_stages(root)
        staged.assert_package_identity()
        with staged.open_package() as verified:
            assert verified.read() == PACKAGE
    assert list(root.iterdir()) == []


def test_recovery_caps_stage_directory_scan(root: Path) -> None:
    for _ in range(17):
        _interrupted_stage(root)

    with pytest.raises(UpdateStagingError):
        cleanup_interrupted_update_stages(root)

    assert len(list(root.iterdir())) == 17
