"""PWS-0204 exact Tesseract runtime-closure admission tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import paperworkshell.worker.client.tesseract_runtime as runtime_module
from paperworkshell.worker.client.tesseract_runtime import (
    TESSERACT_RUNTIME_FILE_COUNT,
    TESSERACT_RUNTIME_MANIFEST_SHA256,
    TesseractRuntimeAdmissionError,
    admit_tesseract_runtime,
    packaged_tesseract_runtime_manifest,
)


def _runtime(tmp_path: Path) -> tuple[Path, Path, bytes, str]:
    root = tmp_path / "approved-runtime"
    executable = root / "bin" / "tesseract.exe"
    tessdata = root / "share" / "tessdata"
    executable.parent.mkdir(parents=True)
    tessdata.mkdir(parents=True)

    payloads: dict[str, bytes] = {
        "bin/tesseract.exe": b"synthetic reviewed executable",
        "share/tessdata/eng.traineddata": b"synthetic reviewed language data",
    }
    payloads.update(
        {f"bin/runtime-{index:02d}.dll": f"runtime-{index:02d}".encode() for index in range(37)}
    )
    files: list[dict[str, object]] = []
    for relative, payload in sorted(payloads.items()):
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        files.append(
            {
                "path": relative,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    document: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "paperworkshell-tesseract-runtime-windows-x86_64",
        "task_id": "PWS-0204",
        "layout": {
            "entrypoint": "bin/tesseract.exe",
            "tessdata_directory": "share/tessdata",
            "allow_unlisted_files": False,
            "allow_reparse_points": False,
        },
        "closure_evidence": {
            "selected_file_count": TESSERACT_RUNTIME_FILE_COUNT,
            "selected_file_bytes": sum(len(payload) for payload in payloads.values()),
        },
        "files": files,
    }
    manifest = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return executable, tessdata, manifest, hashlib.sha256(manifest).hexdigest()


def _admit(
    executable: Path,
    tessdata: Path,
    manifest: bytes,
    digest: str,
) -> object:
    return admit_tesseract_runtime(
        executable,
        tessdata,
        manifest_bytes=manifest,
        expected_manifest_sha256=digest,
    )


def _document(manifest: bytes) -> dict[str, Any]:
    value = json.loads(manifest)
    assert isinstance(value, dict)
    return value


def _encode(document: dict[str, Any]) -> tuple[bytes, str]:
    manifest = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return manifest, hashlib.sha256(manifest).hexdigest()


def test_packaged_manifest_is_the_reviewed_hash_addressed_copy() -> None:
    content = packaged_tesseract_runtime_manifest().read_bytes()

    assert hashlib.sha256(content).hexdigest() == TESSERACT_RUNTIME_MANIFEST_SHA256
    assert (
        content
        == (
            Path(__file__).parents[3] / "packaging" / "tesseract-runtime-closure-v1.json"
        ).read_bytes()
    )


def test_exact_closure_is_admitted_with_fixed_layout(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)

    admission = admit_tesseract_runtime(
        executable,
        tessdata,
        manifest_bytes=manifest,
        expected_manifest_sha256=digest,
    )

    assert admission.root == executable.parent.parent
    assert admission.executable == executable
    assert admission.tessdata_root == tessdata
    assert executable in admission.resources
    assert tessdata in admission.resources
    assert len(admission.resources) == TESSERACT_RUNTIME_FILE_COUNT + 4


def test_manifest_hash_mismatch_is_rejected_before_inventory(tmp_path: Path) -> None:
    executable, tessdata, manifest, _digest = _runtime(tmp_path)

    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_hash"):
        _admit(executable, tessdata, manifest, "0" * 64)


@pytest.mark.parametrize("mutation", ["size", "hash"])
def test_file_size_and_hash_mismatch_are_rejected(tmp_path: Path, mutation: str) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    target = executable.parent / "runtime-00.dll"
    if mutation == "size":
        target.write_bytes(b"different-size-payload")
    else:
        original = target.read_bytes()
        target.write_bytes(b"x" * len(original))

    with pytest.raises(TesseractRuntimeAdmissionError, match=mutation):
        _admit(executable, tessdata, manifest, digest)


def test_missing_entry_is_rejected(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    (executable.parent / "runtime-00.dll").unlink()

    with pytest.raises(TesseractRuntimeAdmissionError, match="missing"):
        _admit(executable, tessdata, manifest, digest)


def test_unlisted_file_and_directory_are_rejected(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    extra = executable.parent.parent / "decoy" / "plugin.dll"
    extra.parent.mkdir()
    extra.write_bytes(b"not reviewed")

    with pytest.raises(TesseractRuntimeAdmissionError, match="unlisted"):
        _admit(executable, tessdata, manifest, digest)


def test_decoy_executable_outside_fixed_layout_is_rejected(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    decoy = executable.parent.parent / "tools" / "tesseract.exe"
    decoy.parent.mkdir()
    decoy.write_bytes(executable.read_bytes())

    with pytest.raises(TesseractRuntimeAdmissionError, match="layout"):
        _admit(decoy, tessdata, manifest, digest)


def test_reparse_entry_is_rejected_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    monkeypatch.setattr(runtime_module, "_is_reparse", lambda _metadata: True)

    with pytest.raises(TesseractRuntimeAdmissionError, match="reparse"):
        _admit(executable, tessdata, manifest, digest)


def test_hardlinked_runtime_file_is_rejected_before_hashing(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    target = executable.parent / "runtime-00.dll"
    alias = tmp_path / "outside-runtime-alias.dll"
    try:
        os.link(target, alias)
    except OSError as error:
        pytest.skip(f"hard links are unavailable on this test volume: {error.errno}")

    with pytest.raises(TesseractRuntimeAdmissionError, match="type"):
        _admit(executable, tessdata, manifest, digest)


def test_special_runtime_file_is_rejected_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    target = executable.parent / "runtime-00.dll"
    real_lstat = Path.lstat

    def lstat_with_special_entry(path: Path) -> os.stat_result | object:
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat_with_special_entry)

    with pytest.raises(TesseractRuntimeAdmissionError, match="type"):
        _admit(executable, tessdata, manifest, digest)


def test_manifest_traversal_path_is_rejected(tmp_path: Path) -> None:
    executable, tessdata, manifest, _digest = _runtime(tmp_path)
    document = _document(manifest)
    files = document["files"]
    assert isinstance(files, list)
    entry = files[0]
    assert isinstance(entry, dict)
    entry["path"] = "../decoy.dll"
    changed_manifest, changed_digest = _encode(document)

    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_schema"):
        _admit(executable, tessdata, changed_manifest, changed_digest)
