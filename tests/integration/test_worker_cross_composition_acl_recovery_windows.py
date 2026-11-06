"""Different operation closures share recovery authority, never worker grants."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal
from tests.integration.test_worker_windows_containment import _assert_profile_removed, _dacl_sddl

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows AppContainer ACLs")


def _configurations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[windows.WindowsLaunchConfig, windows.WindowsLaunchConfig, AclRecoveryJournal]:
    recovery = tmp_path / "acl-recovery"
    monkeypatch.setattr(windows, "_acl_recovery_directory_path", lambda: recovery)
    runtime = tmp_path / "runtime"
    code = tmp_path / "code"
    pdf_engine = tmp_path / "pdf-engine"
    ocr_engine = tmp_path / "ocr-engine"
    for root in (runtime, code, pdf_engine, ocr_engine):
        root.mkdir()
    python = runtime / "python.exe"
    bootstrap = code / "host_bootstrap.py"
    python.write_bytes(b"synthetic interpreter; never executed")
    bootstrap.write_bytes(b"# synthetic bootstrap; never executed\n")
    first_engine = pdf_engine / "qpdf.exe"
    second_engine = ocr_engine / "qpdf.exe"
    first_engine.write_bytes(b"synthetic first fixed engine; never executed")
    second_engine.write_bytes(b"synthetic other fixed engine; never executed")
    common = windows.WindowsLaunchConfig(
        python_executable=python,
        bootstrap_script=bootstrap,
        runtime_roots=(runtime,),
        code_roots=(code,),
        working_directory=code,
        qpdf_executable=first_engine,
        recovery_authority_roots=(runtime, code, pdf_engine, ocr_engine),
    )
    # Both synthetic engines use the existing qpdf configuration seam; no OCR
    # dependency or document parser is needed to exercise disjoint ACL closures.
    other = replace(common, qpdf_executable=second_engine)
    assert common.access_roots != other.access_roots
    assert set(common.approved_recovery_roots) == set(other.approved_recovery_roots)
    return common, other, AclRecoveryJournal(recovery)


@pytest.mark.parametrize("orphaned", (False, True))
def test_different_engine_lease_is_validated_without_granting_that_engine(
    orphaned: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_config, ocr_config, journal = _configurations(tmp_path, monkeypatch)
    assert pdf_config.qpdf_executable is not None
    assert ocr_config.qpdf_executable is not None
    paths = tuple(
        path
        for root in pdf_config.approved_recovery_roots
        for path in (root, *tuple(root.iterdir()))
    )
    before = tuple(_dacl_sddl(path) for path in paths)
    ocr = windows._AppContainerProfile.create(ocr_config.approved_recovery_roots)
    pdf: windows._AppContainerProfile | None = None
    try:
        pdf = windows._AppContainerProfile.create(pdf_config.approved_recovery_roots)
        ocr.grant_read_execute(ocr_config.access_roots)
        original = journal.inventory()[0]
        assert original.grant_roots == ocr_config.access_roots
        if orphaned:
            dead_pid = 2_000_000_000
            journal.write(replace(original, owner_pid=dead_pid))
            monkeypatch.setattr(
                windows,
                "_ledger_owner_is_active",
                lambda owner_pid, _created: owner_pid != dead_pid,
            )

        # The former per-operation authority failed here on the other engine.
        pdf.grant_read_execute(pdf_config.access_roots)

        entries = {entry.profile_id: entry for entry in journal.inventory()}
        assert entries[pdf.access.profile_id].grant_roots == pdf_config.access_roots
        assert tuple(pdf.access.paths) == pdf_config.access_roots
        assert windows._sid_access_mask(pdf_config.qpdf_executable, pdf.sid)
        assert windows._sid_access_mask(ocr_config.qpdf_executable, pdf.sid) == 0
        assert windows._sid_access_mask(ocr_config.qpdf_executable.parent, pdf.sid) == 0
        if orphaned:
            assert ocr.access.profile_id not in entries
            assert windows._sid_access_mask(ocr_config.qpdf_executable, ocr.sid) == 0
            assert (
                _dacl_sddl(ocr_config.qpdf_executable)
                == before[paths.index(ocr_config.qpdf_executable)]
            )
        else:
            assert entries[ocr.access.profile_id] == original
            assert windows._sid_access_mask(ocr_config.qpdf_executable, ocr.sid)
    finally:
        if pdf is not None:
            assert pdf.close_no_raise()
        assert ocr.close_no_raise()
    _assert_profile_removed(ocr.local_app_data)
    assert pdf is not None
    _assert_profile_removed(pdf.local_app_data)
    assert journal.inventory() == ()
    assert tuple(_dacl_sddl(path) for path in paths) == before


def test_shared_authority_still_refuses_an_unconfigured_journal_root_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _other, journal = _configurations(tmp_path, monkeypatch)
    unknown = tmp_path / "not-in-installed-or-selected-closure"
    unknown.mkdir()
    child = unknown / "synthetic-resource.dat"
    child.write_bytes(b"synthetic unknown resource")
    before = (_dacl_sddl(unknown), _dacl_sddl(child))
    profile = windows._AppContainerProfile.create((unknown,))
    try:
        profile.grant_read_execute((unknown,))
        entries = journal.inventory()
        granted = (_dacl_sddl(unknown), _dacl_sddl(child))

        with pytest.raises(windows.WindowsLaunchError) as captured:
            windows.recover_worker_acl_access(config.approved_recovery_roots)

        assert captured.value.context == "access.recovery_allowlist"
        assert journal.inventory() == entries
        assert (_dacl_sddl(unknown), _dacl_sddl(child)) == granted
        assert windows._sid_access_mask(child, profile.sid)
    finally:
        assert profile.close_no_raise()
    _assert_profile_removed(profile.local_app_data)
    assert (_dacl_sddl(unknown), _dacl_sddl(child)) == before
