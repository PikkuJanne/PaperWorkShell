from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.worker.client._acl_recovery import (
    ACL_RECOVERY_JOURNAL_VERSION,
    ACL_RECOVERY_LEGACY_VERSION,
    MAX_ACL_RECOVERY_GRANT_ROOTS,
    MAX_ACL_RECOVERY_OBJECTS,
    AclRecoveryEntry,
    AclRecoveryJournal,
    AclRecoveryJournalError,
    AclRecoveryObject,
    AclRecoveryPhase,
    decode_acl_recovery_entry,
    encode_acl_recovery_entry,
)

PROFILE_ID = "0123456789abcdef01234567"
PROFILE_NAME = f"PaperWorkShell.Worker.{PROFILE_ID}"
APP_CONTAINER_SID = "S-1-15-2-1-2-3-4-5-6-7"
PEER_SID = "S-1-5-21-1-2-3-1001"


def _entry(
    *,
    phase: AclRecoveryPhase = AclRecoveryPhase.PREPARED,
    journal_version: int = ACL_RECOVERY_JOURNAL_VERSION,
) -> AclRecoveryEntry:
    root = Path("C:/approved/runtime")
    child = root / "python.exe"
    exact = journal_version == ACL_RECOVERY_JOURNAL_VERSION
    return AclRecoveryEntry(
        profile_id=PROFILE_ID,
        profile_name=PROFILE_NAME,
        sid=APP_CONTAINER_SID,
        owner_pid=1234,
        owner_created_100ns=987654321,
        phase=phase,
        grant_roots=(root,),
        objects=(
            AclRecoveryObject(
                path=root,
                device=10,
                inode=20,
                is_directory=True,
                auto_inherited=False,
                volume_serial=10 if exact else None,
                file_id="00000000000000000000000000000014" if exact else None,
            ),
            AclRecoveryObject(
                path=child,
                device=10,
                inode=21,
                is_directory=False,
                auto_inherited=True,
                volume_serial=10 if exact else None,
                file_id="00000000000000000000000000000015" if exact else None,
                guard_baseline=2 if exact else None,
            ),
        ),
        peer_sid=PEER_SID if exact else None,
        journal_version=journal_version,
    )


def test_acl_recovery_codec_is_canonical_bounded_and_path_private_in_repr() -> None:
    entry = _entry()

    payload = encode_acl_recovery_entry(entry)

    assert payload.endswith(b"\n")
    assert decode_acl_recovery_entry(payload, expected_profile_id=PROFILE_ID) == entry
    assert encode_acl_recovery_entry(decode_acl_recovery_entry(payload)) == payload
    assert "approved" not in repr(entry)
    assert "approved" not in repr(entry.objects[0])


def test_acl_recovery_codec_rejects_future_noncanonical_and_misbound_records() -> None:
    payload = encode_acl_recovery_entry(_entry())
    document = json.loads(payload)
    document["journal_version"] = ACL_RECOVERY_JOURNAL_VERSION + 1
    future = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(AclRecoveryJournalError):
        decode_acl_recovery_entry(future)
    with pytest.raises(AclRecoveryJournalError):
        decode_acl_recovery_entry(payload.replace(b'"phase":"prepared"', b'"phase": "prepared"'))
    with pytest.raises(AclRecoveryJournalError):
        decode_acl_recovery_entry(payload, expected_profile_id="fedcba9876543210fedcba98")


def test_acl_recovery_codec_preserves_legacy_root_grant_records_without_reinterpretation() -> None:
    entry = _entry(journal_version=ACL_RECOVERY_LEGACY_VERSION)

    payload = encode_acl_recovery_entry(entry)
    document = json.loads(payload)

    assert decode_acl_recovery_entry(payload) == entry
    assert document["journal_version"] == ACL_RECOVERY_LEGACY_VERSION
    assert "peer_sid" not in document
    assert all("guard_baseline" not in item for item in document["objects"])


def test_acl_recovery_codec_rejects_v2_with_missing_or_invalid_guard_fields() -> None:
    document = json.loads(encode_acl_recovery_entry(_entry()))
    del document["peer_sid"]
    missing_peer = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(AclRecoveryJournalError):
        decode_acl_recovery_entry(missing_peer)

    document = json.loads(encode_acl_recovery_entry(_entry()))
    document["objects"][1]["guard_baseline"] = None
    invalid_guard = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(AclRecoveryJournalError):
        decode_acl_recovery_entry(invalid_guard)


def test_acl_recovery_codec_roundtrips_the_exact_object_and_root_boundary() -> None:
    roots = tuple(
        Path(f"C:/approved/runtime-{index:02d}") for index in range(MAX_ACL_RECOVERY_GRANT_ROOTS)
    )
    objects = tuple(
        AclRecoveryObject(
            path=root,
            device=10,
            inode=index + 1,
            is_directory=True,
            auto_inherited=False,
            volume_serial=10,
            file_id=f"{index + 1:032x}",
        )
        for index, root in enumerate(roots)
    ) + tuple(
        AclRecoveryObject(
            path=roots[0] / f"module-{index:05d}.py",
            device=10,
            inode=MAX_ACL_RECOVERY_GRANT_ROOTS + index + 1,
            is_directory=False,
            auto_inherited=True,
            volume_serial=10,
            file_id=f"{MAX_ACL_RECOVERY_GRANT_ROOTS + index + 1:032x}",
            guard_baseline=0,
        )
        for index in range(MAX_ACL_RECOVERY_OBJECTS - MAX_ACL_RECOVERY_GRANT_ROOTS)
    )
    entry = AclRecoveryEntry(
        profile_id=PROFILE_ID,
        profile_name=PROFILE_NAME,
        sid=APP_CONTAINER_SID,
        owner_pid=1234,
        owner_created_100ns=987654321,
        phase=AclRecoveryPhase.ACTIVE,
        grant_roots=roots,
        objects=objects,
        peer_sid=PEER_SID,
    )

    payload = encode_acl_recovery_entry(entry)

    assert decode_acl_recovery_entry(payload) == entry


def test_acl_recovery_journal_publishes_updates_and_deletes_exact_record(
    tmp_path: Path,
) -> None:
    journal = AclRecoveryJournal(tmp_path)
    prepared = _entry()
    active = replace(prepared, phase=AclRecoveryPhase.ACTIVE)

    journal.write(prepared)
    assert journal.inventory() == (prepared,)

    journal.write(active)
    assert journal.inventory() == (active,)
    assert journal.exists(PROFILE_ID)

    journal.delete(PROFILE_ID)
    assert journal.inventory() == ()
    assert not journal.exists(PROFILE_ID)


def test_acl_recovery_journal_removes_only_exact_stages_and_blocks_unknown_entries(
    tmp_path: Path,
) -> None:
    journal = AclRecoveryJournal(tmp_path)
    stage = tmp_path / f"worker-acl-{PROFILE_ID}.fedcba9876543210fedcba98.tmp"
    stage.write_bytes(b"unpublished")

    assert journal.inventory() == ()
    assert not stage.exists()

    unknown = tmp_path / "unexpected.txt"
    unknown.write_bytes(b"untrusted")
    with pytest.raises(AclRecoveryJournalError) as raised:
        journal.inventory()
    assert "untrusted" not in str(raised.value)
    assert unknown.read_bytes() == b"untrusted"


def test_acl_recovery_journal_rejects_corruption_without_deleting_it(tmp_path: Path) -> None:
    journal = AclRecoveryJournal(tmp_path)
    path = tmp_path / f"worker-acl-{PROFILE_ID}.json"
    path.write_bytes(b'{"journal_version":1')

    with pytest.raises(AclRecoveryJournalError):
        journal.inventory()

    assert path.read_bytes() == b'{"journal_version":1'
