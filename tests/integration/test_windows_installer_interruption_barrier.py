"""Real synthetic writers prove an acknowledged cutpoint without installing anything."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.storage._windows import open_handle
from tools import qualify_windows_installer as qualifier

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows blocking RH oplock")


def _wait(predicate: Callable[[], bool], seconds: int = 10) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Synthetic event did not arrive within its bound"
        time.sleep(0.01)


@pytest.mark.parametrize("operation", ["write", "replace"])
def test_reads_continue_but_actual_replacement_waits_until_owned_job_is_stopped(
    tmp_path: Path, operation: str
) -> None:
    target = tmp_path / "authenticated-notice.txt"
    target.write_bytes(b"unchanged barrier bytes")
    first = tmp_path / "earlier-copy.txt"
    read_marker = tmp_path / "compatible-read-complete"
    done = tmp_path / "must-not-finish"
    program = r"""
import os,sys
from pathlib import Path
base=Path(sys.argv[1]); target=base/'authenticated-notice.txt'
assert target.read_bytes()==b'unchanged barrier bytes'
assert target.stat().st_size==23
(base/'compatible-read-complete').write_bytes(b'read only')
(base/'earlier-copy.txt').write_bytes(b'earlier copy completed')
if sys.argv[2]=='write':
    target.write_bytes(b'unsafe replacement')
else:
    stage=base/'new-stage.txt';stage.write_bytes(b'unsafe replacement');os.replace(stage,target)
(base/'must-not-finish').write_bytes(b'unexpected')
"""
    with open_handle(target, read=True) as original:
        identity = original.info.identity
        with qualifier._ReplacementBarrier(original.value) as barrier:
            with qualifier._InterruptionProcess(
                [sys.executable, "-I", "-B", "-c", program, str(tmp_path), operation], tmp_path
            ) as process:
                _wait(barrier.replacement_waiting)
                assert process.alive()
                assert process.identity["exact_image_verified"] is True
                assert process.identity["creation_filetime"] > 0
                assert read_marker.read_bytes() == b"read only"
                assert first.read_bytes() == b"earlier copy completed"
                assert not done.exists()
                assert original.refresh_info().identity == identity
                assert original.read_bytes(maximum_bytes=128) == b"unchanged barrier bytes"
                process.stop()
                assert not process.alive()
                assert process.terminated
                process.stop()  # An accepted asynchronous termination is not issued twice.
            assert not done.exists()
    assert target.read_bytes() == b"unchanged barrier bytes"


def test_compatible_reads_do_not_become_a_false_cutpoint(tmp_path: Path) -> None:
    target = tmp_path / "read-only.txt"
    target.write_bytes(b"original")
    with open_handle(target, read=True) as original:
        with qualifier._ReplacementBarrier(original.value) as barrier:
            with qualifier._InterruptionProcess(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "from pathlib import Path;import sys;assert Path(sys.argv[1]).read_bytes()==b'original'",
                    str(target),
                ],
                tmp_path,
            ) as process:
                assert process.process is not None
                assert process.process.wait(timeout=10) == 0
                assert not barrier.replacement_waiting()
                assert not process.alive()
    assert target.read_bytes() == b"original"


def test_descendant_writer_is_contained_while_original_process_handle_stays_live(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"retained")
    child = "from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(b'changed')"
    parent = (
        "import subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1],sys.argv[2]]);"
        "raise SystemExit(p.wait())"
    )
    with open_handle(target, read=True) as original:
        with qualifier._ReplacementBarrier(original.value) as barrier:
            with qualifier._InterruptionProcess(
                [sys.executable, "-I", "-B", "-c", parent, child, str(target)], tmp_path
            ) as process:
                _wait(barrier.replacement_waiting)
                assert process.alive()
                process.stop()  # Returns only after the held job reports zero active members.
                assert not process.alive()
            assert target.read_bytes() == b"retained"
    assert target.read_bytes() == b"retained"


@pytest.mark.parametrize("complete_runtime", [False, True])
def test_synthetic_copy_cutpoint_then_exact_temp_recovery_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete_runtime: bool
) -> None:
    import hashlib

    from tools.release_artifacts import file_hashes
    from tools.release_evidence import load_json

    installation = tmp_path / "synthetic-product"
    notice = installation / qualifier._INTERRUPTION_NOTICE
    barrier = installation / qualifier._INTERRUPTION_BARRIER
    barrier.parent.mkdir(parents=True)
    notice.write_bytes(b"synthetic first-party notice")
    barrier.write_bytes(b"synthetic later notice")
    (installation / "unins000.exe").write_bytes(b"synthetic owned uninstall executable")
    (installation / "unins000.dat").write_bytes(b"synthetic owned uninstall data")
    contents = {
        qualifier._INTERRUPTION_NOTICE: notice.read_bytes(),
        qualifier._INTERRUPTION_BARRIER: barrier.read_bytes(),
    }
    records = [
        {"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in contents.items()
    ]
    (tmp_path / "files.iss.inc").write_text(
        "\\"
        + qualifier._INTERRUPTION_NOTICE.replace("/", "\\")
        + '";\n'
        + "\\"
        + qualifier._INTERRUPTION_BARRIER.replace("/", "\\")
        + '";\n',
        encoding="utf-8",
    )
    candidate = {
        "files": records,
        "installer": str(Path(sys.executable).absolute()),
        "installer_sha256": file_hashes(Path(sys.executable))[1],
        "build_receipt": "synthetic-receipt.json",
    }
    if complete_runtime:
        authority: dict[str, Any] = {
            "microsoft_runtime_delivery": "app-local-community2022",
            "build_receipt_sha256": "1" * 64,
        }
        for name, data, field in (
            (
                "notices/Microsoft-runtime-recipient-terms.txt",
                b"synthetic exact terms",
                "microsoft_recipient_terms_sha256",
            ),
            (
                "evidence/microsoft-runtime/overlay-receipt.json",
                b"synthetic exact authority",
                "microsoft_overlay_receipt_sha256",
            ),
        ):
            path = installation / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            records.append({"path": name, "size_bytes": len(data), "sha256": digest})
            authority[field] = digest
        candidate.update(authority)
    state: dict[str, Any] = {
        "candidate": candidate,
        "installed_files": qualifier._installed_files(installation, records),
    }
    direct_process = qualifier._InterruptionProcess
    program = r"""
import os,sys
from pathlib import Path
product=Path(sys.argv[1]); notice=product/'notices/UNSIGNED_INTERNAL.txt'
barrier=product/'notices/build-support/gcc-runtime/COPYING.RUNTIME'
(product/'is-ABCDEFGH01.tmp').write_bytes((product/'unins000.exe').read_bytes())
notice.write_bytes(b'synthetic first-party notice')
stage=barrier.parent/'is-IJKLMNOP23.tmp';stage.write_bytes(b'synthetic later notice')
os.replace(stage,barrier)
raise RuntimeError('MUST_NOT_REACH_AFTER_BARRIER')
"""

    def synthetic_process(argv: list[str], directory: Path) -> qualifier._InterruptionProcess:
        assert argv == [str(Path(sys.executable).absolute()), *qualifier.quiet_arguments(candidate)]
        return direct_process([argv[0], "-I", "-B", "-c", program, str(installation)], directory)

    monkeypatch.setattr(qualifier, "_InterruptionProcess", synthetic_process)
    report = qualifier._interrupt_owned_repair(
        installation, state, tmp_path / "interruption", root=tmp_path
    )
    assert report["status"] == "pass"
    assert all(
        report[name] is True
        for name in (
            "cutpoint_observed",
            "earlier_copy_verified",
            "acknowledgement_required",
            "exact_installer_live",
            "job_terminated",
            "job_empty",
        )
    )
    assert load_json(tmp_path / "interruption/interruption-result.json") == report
    assert (barrier.parent / "is-IJKLMNOP23.tmp").read_bytes() == contents[
        qualifier._INTERRUPTION_BARRIER
    ]
    monkeypatch.setattr(qualifier, "_check_registration", lambda _p: None)

    def synthetic_repair(argv: list[str], _step: Path, *, root: Path) -> None:
        assert argv == [candidate["installer"], *qualifier.quiet_arguments(candidate)]
        assert root == tmp_path
        assert not (installation / "is-ABCDEFGH01.tmp").exists()
        assert not (barrier.parent / "is-IJKLMNOP23.tmp").exists()
        (installation / "unins000.dat").write_bytes(b"new synthetic owned uninstall data")

    monkeypatch.setattr(qualifier, "_execute", synthetic_repair)
    verified = qualifier._recover_owned_repair(
        installation, state, tmp_path / "recovery", root=tmp_path
    )
    assert verified["unins000.exe"] == state["installed_files"]["unins000.exe"]
    assert verified["unins000.dat"] != state["installed_files"]["unins000.dat"]
    retained = load_json(tmp_path / "recovery/interruption-result.json")["operator_temp_recovery"]
    assert len(retained) == 2
    for name, data in contents.items():
        assert (installation / name).read_bytes() == data
