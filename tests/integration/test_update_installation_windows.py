"""Exercise signed updates against an isolated installation on Windows."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from errno import ENOSPC
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from paperworkshell.domain.manifest_repository import CURRENT_MANIFEST_SCHEMA_VERSION
from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import (
    SIGNATURE_PREFIX,
    UpdateMetadata,
    verify_update_metadata,
)
from paperworkshell.storage import update_installation
from paperworkshell.storage.update_installation import UpdateInstallation, UpdateInstallationError
from paperworkshell.storage.update_payload import INSTALLED_MARKER_NAME
from paperworkshell.storage.update_trust import TRUST_SIGNATURE_PREFIX, TrustAnchor
from tests.security.test_update_payload_security import _archive, _file_bytes, _manifest
from tests.security.test_update_trust_security import _public_key, _record, _sign
from tools.release_staging import canonical_json

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows update staging")

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
_CHANNEL = "engineering"
_MARKER = "runtime/" + INSTALLED_MARKER_NAME
_TERMS = "notices/Microsoft-runtime-recipient-terms.txt"
_BASE_RUNTIME = b"same original base runtime"
_TERMS_BYTES = b"synthetic recipient terms"
_CASE_BYTES = b"synthetic case outside the installation"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(slots=True)
class _InstallationFixture:
    workspace: Path
    root: Path
    case: Path
    signer: ec.EllipticCurvePrivateKey
    root_key: ec.EllipticCurvePrivateKey
    clock: datetime = NOW

    def controller(
        self, check_runtime: Callable[[Path], None], *, sequence_floor: int = 0
    ) -> UpdateInstallation:
        return UpdateInstallation(
            self.root,
            base_version="0.0.0",
            base_candidate="pws0803-test-base",
            anchor=TrustAnchor(_CHANNEL, _public_key(self.root_key)),
            base_sequence_floor=sequence_floor,
            clock=lambda: self.clock,
            check_runtime=check_runtime,
        )

    def write_offer(
        self,
        sequence: int,
        *,
        channel: str = _CHANNEL,
        terms: bytes = _TERMS_BYTES,
        expires_at: str = "2026-11-26T13:00:00Z",
    ) -> Path:
        directory = self.workspace / f"offer-{sequence}"
        directory.mkdir()
        identity = {
            "application_id": INSTALLED_APPLICATION_ID,
            "channel": channel,
            "version": f"1.0.{sequence}",
            "candidate_id": f"pws0803-test-release-{sequence}",
        }
        files = _file_bytes()
        files[_MARKER] = canonical_json({"schema_version": 1, **identity})
        files["runtime/PaperWorkShellDesktop.exe"] = f"synthetic runtime {sequence}".encode()
        files[_TERMS] = terms
        manifest = (
            _manifest(files)
            | identity
            | {
                "case_schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
                "inventory_sha256": _sha(
                    canonical_json({name: _sha(raw) for name, raw in files.items()})
                ),
            }
        )
        package = _archive(files=files, manifest=canonical_json(manifest))
        release = canonical_json(
            {
                "schema_version": 1,
                "platform": "win-x64",
                "sequence": sequence,
                "issued_at": "2026-11-26T11:00:00Z",
                "expires_at": expires_at,
                "package_size": len(package),
                "package_sha256": _sha(package),
                **identity,
            }
        )
        trust = _record(self.signer)
        for name, raw in {
            "trust.json": trust,
            "trust.sig": _sign(self.root_key, TRUST_SIGNATURE_PREFIX + trust),
            "release.json": release,
            "release.sig": _sign(self.signer, SIGNATURE_PREFIX + release),
            "runtime.zip": package,
        }.items():
            (directory / name).write_bytes(raw)
        return directory

    @property
    def state_path(self) -> Path:
        return self.root / "update-state-v1"

    def state(self) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(self.state_path.read_bytes().split(b"\n", 1)[1])
        return state

    def assert_originals(self) -> None:
        assert (self.root / "runtime/PaperWorkShellDesktop.exe").read_bytes() == _BASE_RUNTIME
        assert (self.root / _TERMS).read_bytes() == _TERMS_BYTES
        assert self.case.read_bytes() == _CASE_BYTES


@pytest.fixture
def installed(tmp_path: Path) -> _InstallationFixture:
    local = tmp_path / "LocalAppData"
    root = local / "Programs" / "PaperWorkShell"
    root.mkdir(mode=0o700, parents=True)
    (root / "runtime").mkdir()
    (root / "notices").mkdir()
    (root / "runtime/PaperWorkShellDesktop.exe").write_bytes(_BASE_RUNTIME)
    (root / _TERMS).write_bytes(_TERMS_BYTES)
    cases = local / "PaperWorkShell" / "cases"
    cases.mkdir(mode=0o700, parents=True)
    case = cases / "synthetic-original.bin"
    case.write_bytes(_CASE_BYTES)
    return _InstallationFixture(
        tmp_path,
        root,
        case,
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )


def _never_cancel() -> bool:
    return False


def _check_staged_runtime(slot: Path) -> str:
    marker: dict[str, object] = json.loads((slot / _MARKER).read_bytes())
    version = marker["version"]
    assert isinstance(version, str)
    assert version.startswith("1.0.")
    executable = slot / "runtime/PaperWorkShellDesktop.exe"
    assert executable.read_bytes() == f"synthetic runtime {version.split('.')[-1]}".encode()
    assert not (slot / "cases").exists()
    return version


def test_signed_offline_updates_restore_exact_prior_release_and_keep_replay_floor(
    installed: _InstallationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks: list[str] = []

    def check_runtime(slot: Path) -> None:
        checks.append(_check_staged_runtime(slot))

    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline update initiated a socket operation")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
    controller = installed.controller(check_runtime)
    assert controller.status().active_version == "0.0.0"
    assert controller.status().trust_available
    assert not controller.status().can_restore

    earlier = installed.write_offer(6)
    later = installed.write_offer(7)
    for expected, directory in ((6, earlier), (7, later)):
        current = controller.inspect_offer(directory)
        assert current.metadata.sequence == expected
        controller.apply_offer(current, cancelled=_never_cancel)
        status = controller.status()
        assert status.can_restore
        assert status.active_version == f"1.0.{expected}"
        assert status.active_candidate == current.metadata.candidate_id
        assert not list((installed.root / "update-transport").iterdir())

    # A fresh controller resolves the previously admitted bytes and audit history.
    controller = installed.controller(check_runtime)
    controller.restore_previous()
    assert controller.status().active_version == "1.0.6"
    assert not controller.status().can_restore
    assert checks == ["1.0.6", "1.0.7", "1.0.6"]
    state = installed.state()
    assert state["sequence_floor"] == 7
    assert state["active"]["sequence"] == 6
    assert [entry["sequence"] for entry in state["audit"]] == [6, 7]
    assert all(len(entry["inventory_sha256"]) == 64 for entry in state["audit"])
    for directory in (earlier, later):
        with pytest.raises(UpdateInstallationError) as failure:
            controller.inspect_offer(directory)
        assert failure.value.code == "UPDATE_ROLLBACK_FORBIDDEN"
    installed.assert_originals()


def test_cancellation_after_native_staging_preserves_old_selection_and_allows_retry(
    installed: _InstallationFixture,
) -> None:
    cancel = False
    enabled = True

    def check_runtime(slot: Path) -> None:
        nonlocal cancel
        if _check_staged_runtime(slot) == "1.0.7" and enabled:
            cancel = True

    controller = installed.controller(check_runtime)
    base_update = controller.inspect_offer(installed.write_offer(6))
    controller.apply_offer(base_update, cancelled=_never_cancel)
    pending = controller.inspect_offer(installed.write_offer(7))
    state_before = installed.state_path.read_bytes()

    with pytest.raises(UpdateInstallationError) as failure:
        controller.apply_offer(pending, cancelled=lambda: cancel)
    assert failure.value.code == "UPDATE_CANCELLED"
    assert installed.state_path.read_bytes() == state_before
    assert controller.status().active_version == "1.0.6"
    assert not list((installed.root / "update-transport").iterdir())
    installed.assert_originals()

    enabled = False
    cancel = False
    controller.apply_offer(pending, cancelled=lambda: cancel)
    assert controller.status().active_version == "1.0.7"


@pytest.mark.parametrize(
    ("problem", "expected_code"),
    [
        ("unsigned", "UPDATE_SIGNATURE_INVALID"),
        ("wrong-channel", "UPDATE_TARGET_MISMATCH"),
        ("tampered-package", "UPDATE_PACKAGE_MISMATCH"),
        ("changed-terms", "UPDATE_TERMS_CHANGED"),
    ],
)
def test_invalid_offer_preserves_previous_build_and_private_data(
    installed: _InstallationFixture, problem: str, expected_code: str
) -> None:
    def unused_runtime(_slot: Path) -> None:
        pytest.fail("untrusted package reached runtime acceptance")

    controller = installed.controller(unused_runtime)
    directory = installed.write_offer(
        6,
        channel="preview" if problem == "wrong-channel" else _CHANNEL,
        terms=b"different terms" if problem == "changed-terms" else _TERMS_BYTES,
    )
    if problem == "unsigned":
        (directory / "release.sig").write_bytes(b"")
    if problem == "tampered-package":
        path = directory / "runtime.zip"
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])

    with pytest.raises(UpdateInstallationError) as failure:
        offer = controller.inspect_offer(directory)
        controller.apply_offer(offer, cancelled=_never_cancel)
    assert failure.value.code == expected_code
    assert controller.status().active_version == "0.0.0"
    assert installed.state()["sequence_floor"] == 0
    assert installed.state()["audit"] == []
    installed.assert_originals()


def test_expiry_during_staged_runtime_check_blocks_activation(
    installed: _InstallationFixture,
) -> None:
    def check_runtime(slot: Path) -> None:
        assert _check_staged_runtime(slot) == "1.0.6"
        installed.clock = datetime(2026, 11, 26, 12, 30, tzinfo=UTC)

    controller = installed.controller(check_runtime)
    directory = installed.write_offer(6, expires_at="2026-11-26T12:15:00Z")
    offer = controller.inspect_offer(directory)
    prior = installed.state_path.read_bytes()

    with pytest.raises(UpdateInstallationError) as failure:
        controller.apply_offer(offer, cancelled=_never_cancel)
    assert failure.value.code == "UPDATE_RELEASE_NOT_CURRENT"
    assert installed.state_path.read_bytes() == prior
    assert controller.status().active_version == "0.0.0"
    installed.assert_originals()


def test_selector_publication_failure_keeps_the_prior_build(
    installed: _InstallationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def check_runtime(slot: Path) -> None:
        _check_staged_runtime(slot)

    controller = installed.controller(check_runtime)
    controller.apply_offer(
        controller.inspect_offer(installed.write_offer(6)), cancelled=_never_cancel
    )
    pending = controller.inspect_offer(installed.write_offer(7))
    previous = installed.state_path.read_bytes()
    replace = os.replace

    def no_space(source: Path, destination: Path) -> None:
        if destination == installed.state_path:
            raise OSError(ENOSPC, "PRIVATE_VOLUME_CANARY")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", no_space)
        with pytest.raises(UpdateInstallationError) as failure:
            controller.apply_offer(pending, cancelled=_never_cancel)
    assert failure.value.code == "UPDATE_STORAGE_UNAVAILABLE"
    assert "PRIVATE_VOLUME_CANARY" not in str(failure.value)
    assert installed.state_path.read_bytes() == previous
    assert not list(installed.root.glob("update-state-*.tmp"))
    assert controller.status().active_version == "1.0.6"
    installed.assert_originals()


def test_restore_rejects_tampered_previous_runtime(installed: _InstallationFixture) -> None:
    checks: list[str] = []

    def check_runtime(slot: Path) -> None:
        checks.append(_check_staged_runtime(slot))

    controller = installed.controller(check_runtime)
    for sequence in (6, 7):
        offer = controller.inspect_offer(installed.write_offer(sequence))
        controller.apply_offer(offer, cancelled=_never_cancel)

    prior = installed.state()["previous"]["name"]
    (installed.root / f"update-slot-{prior}" / "runtime/PaperWorkShellDesktop.exe").write_bytes(
        b"tampered staged runtime"
    )
    previous = installed.state_path.read_bytes()
    with pytest.raises(UpdateInstallationError) as failure:
        controller.restore_previous()
    assert failure.value.code == "UPDATE_PAYLOAD_INVALID"
    assert checks == ["1.0.6", "1.0.7"]
    assert installed.state_path.read_bytes() == previous
    assert controller.status().active_version == "1.0.7"
    installed.assert_originals()


@pytest.mark.parametrize("action", ["restore", "remove"])
def test_unavailable_base_runtime_preserves_working_slot(
    installed: _InstallationFixture, action: str
) -> None:
    def check_runtime(slot: Path) -> None:
        if slot == installed.root:
            assert (slot / "runtime/PaperWorkShellDesktop.exe").read_bytes() == b"broken bootstrap"
            raise UpdateInstallationError(
                "UPDATE_RUNTIME_UNAVAILABLE", "Synthetic bootstrap failure."
            )
        assert _check_staged_runtime(slot) == "1.0.6"

    controller = installed.controller(check_runtime)
    offer = controller.inspect_offer(installed.write_offer(6))
    controller.apply_offer(offer, cancelled=_never_cancel)
    before = installed.state_path.read_bytes()
    slot = installed.root / f"update-slot-{installed.state()['active']['name']}"
    bootstrap = installed.root / "runtime/PaperWorkShellDesktop.exe"
    bootstrap.write_bytes(b"broken bootstrap")
    try:
        with pytest.raises(UpdateInstallationError) as failure:
            if action == "restore":
                controller.restore_previous()
            else:
                controller.remove_installed_updates()
        assert failure.value.code == "UPDATE_RUNTIME_UNAVAILABLE"
        assert installed.state_path.read_bytes() == before
        assert controller.status().active_version == "1.0.6"
        assert _check_staged_runtime(slot) == "1.0.6"
    finally:
        bootstrap.write_bytes(_BASE_RUNTIME)
    installed.assert_originals()


def test_bootstrap_release_sequence_floor_rejects_historical_offer(
    installed: _InstallationFixture,
) -> None:
    def check_runtime(slot: Path) -> None:
        assert _check_staged_runtime(slot) == "1.0.7"

    controller = installed.controller(check_runtime, sequence_floor=6)
    with pytest.raises(UpdateInstallationError) as failure:
        controller.inspect_offer(installed.write_offer(6))
    assert failure.value.code == "UPDATE_ROLLBACK_FORBIDDEN"
    assert controller.status().active_version == "0.0.0"
    assert installed.state()["sequence_floor"] == 6
    offer = controller.inspect_offer(installed.write_offer(7))
    controller.apply_offer(offer, cancelled=_never_cancel)
    assert controller.status().active_version == "1.0.7"
    installed.assert_originals()


def test_private_paperworkshell_folder_rejected_before_offer_files_open(
    installed: _InstallationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_file(*_args: object, **_kwargs: object) -> None:
        pytest.fail("private offer directory reached file-open boundary")

    controller = installed.controller(lambda _: None)
    monkeypatch.setattr(update_installation, "_pinned_file", unexpected_file)
    with pytest.raises(UpdateInstallationError) as failure:
        controller.inspect_offer(installed.case.parent)
    assert failure.value.code == "UPDATE_LOCATION_UNSUPPORTED"
    assert not installed.state_path.exists()
    installed.assert_originals()


def test_cancellation_during_final_recheck_does_not_publish_slot(
    installed: _InstallationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_checked = False
    cancelled = False

    def check_runtime(slot: Path) -> None:
        nonlocal runtime_checked
        assert _check_staged_runtime(slot) == "1.0.6"
        runtime_checked = True

    def verify_then_cancel(*args: Any, **kwargs: Any) -> UpdateMetadata:
        nonlocal cancelled
        metadata = verify_update_metadata(*args, **kwargs)
        if runtime_checked:
            cancelled = True
        return metadata

    controller = installed.controller(check_runtime)
    offer = controller.inspect_offer(installed.write_offer(6))
    before = installed.state_path.read_bytes()
    monkeypatch.setattr(update_installation, "verify_update_metadata", verify_then_cancel)
    with pytest.raises(UpdateInstallationError) as failure:
        controller.apply_offer(offer, cancelled=lambda: cancelled)
    assert failure.value.code == "UPDATE_CANCELLED"
    assert cancelled
    assert controller.status().active_version == "0.0.0"
    assert installed.state_path.read_bytes() == before
    installed.assert_originals()


def test_signed_update_recovers_abandoned_transport(installed: _InstallationFixture) -> None:
    def check_runtime(slot: Path) -> None:
        assert _check_staged_runtime(slot) == "1.0.6"

    transport = installed.root / "update-transport"
    transport.mkdir(mode=0o700)
    interrupted = transport / f"stage-{'1' * 32}"
    interrupted.mkdir(mode=0o700)
    (interrupted / "update-package.zip").write_bytes(b"partial bytes from interrupted copy")

    controller = installed.controller(check_runtime)
    offer = controller.inspect_offer(installed.write_offer(6))
    controller.apply_offer(offer, cancelled=_never_cancel)

    assert controller.status().active_version == "1.0.6"
    assert not list(transport.iterdir())
    installed.assert_originals()
