"""PWS-0204/PWS-0801 hash-addressed Tesseract runtime-closure admission tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest

import paperworkshell.worker.client.tesseract_runtime as runtime_module
from paperworkshell.domain.ocr_languages import (
    OCR_LANGUAGE_SET_DEFINITIONS,
    OcrLanguageSetDefinition,
)
from paperworkshell.worker.client.tesseract_runtime import (
    TESSERACT_LANGUAGE_PACK_CATALOG_SHA256,
    TESSERACT_RUNTIME_FILE_COUNT,
    TESSERACT_RUNTIME_MANIFEST_SHA256,
    TesseractRuntimeAdmission,
    TesseractRuntimeAdmissionError,
    admit_packaged_tesseract_runtime,
    admit_tesseract_runtime,
    packaged_tesseract_language_pack_catalog,
    packaged_tesseract_runtime_manifest,
)


class _ExtraByteReader:
    def __init__(self, inner: Any, read_requests: list[int]) -> None:
        self._inner = inner
        self._read_requests = read_requests
        self._read_count = 0

    def __enter__(self) -> _ExtraByteReader:
        return self

    def __exit__(self, *_args: object) -> None:
        self._inner.close()

    def fileno(self) -> int:
        return int(self._inner.fileno())

    def read(self, size: int = -1) -> bytes:
        self._read_requests.append(size)
        self._read_count += 1
        if self._read_count == 1:
            return bytes(self._inner.read(size))
        if self._read_count == 2:
            return b"x"
        return b""


class _CountingScandir:
    def __init__(self, entry_count: int) -> None:
        self._entry_count = entry_count
        self._index = 0
        self.next_calls = 0

    def __enter__(self) -> _CountingScandir:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self) -> _CountingScandir:
        return self

    def __next__(self) -> SimpleNamespace:
        if self._index >= self._entry_count:
            raise StopIteration
        self.next_calls += 1
        self._index += 1
        return SimpleNamespace(name=f"unlisted-{self._index:05d}.bin")


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
        {
            f"bin/runtime-{index:02d}.dll": f"runtime-{index:02d}".encode()
            for index in range(TESSERACT_RUNTIME_FILE_COUNT - 2)
        }
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
        "schema_version": 2,
        "manifest_id": "paperworkshell-tesseract-runtime-windows-x86_64",
        "task_id": "PWS-0801",
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


def _language_catalog(payload: bytes) -> tuple[bytes, str]:
    archive = "mingw-w64-ucrt-x86_64-tesseract-data-deu-4.1.0-3-any.pkg.tar.zst"
    document = {
        "schema_version": 1,
        "catalog_id": "paperworkshell-tesseract-language-packs-windows-x86_64",
        "reviewed_on": "2026-08-14",
        "task_id": "PWS-0205",
        "target": {
            "os": "Windows",
            "architecture": "x86_64",
            "abi": "MSYS2 UCRT64",
        },
        "runtime_policy": {
            "model_family": "tessdata_fast",
            "model_version": "4.1.0",
            "installation_mode": "offline-prestaged-exact-file",
            "network_download_allowed": False,
            "allow_unlisted_files": False,
            "allow_reparse_points": False,
        },
        "trust": {
            "runtime_authority": "catalog-file-size-and-sha256",
            "upstream_signature_requirement": (
                "The detached MSYS2 package signature must validate against the pinned "
                "reviewed MSYS2 key before extraction."
            ),
            "signature_evidence_status": ("required-upstream-not-reverified-for-pws-0205"),
            "required_key_fingerprint": "5F944B027F7FE2091985AA2EFA11531AA0AA7F57",
        },
        "packs": [
            {
                "language_code": "deu",
                "display_name": "German",
                "relative_path": "share/tessdata/deu.traineddata",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "license_expression": "Apache-2.0",
                "distribution_package_id": ("mingw-w64-ucrt-x86_64-tesseract-data-deu"),
                "distribution_package_version": "4.1.0-3",
                "archive": archive,
                "archive_url": f"https://mirror.msys2.org/mingw/ucrt64/{archive}",
                "archive_size_bytes": 1,
                "archive_sha256": "1" * 64,
                "signature_artifact": f"{archive}.sig",
                "signature_url": (f"https://mirror.msys2.org/mingw/ucrt64/{archive}.sig"),
                "signature_size_bytes": 1,
                "signature_sha256": "2" * 64,
                "expected_signer_fingerprint": ("5F944B027F7FE2091985AA2EFA11531AA0AA7F57"),
            }
        ],
    }
    return _encode(document)


def _admit_with_catalog(
    executable: Path,
    tessdata: Path,
    manifest: bytes,
    digest: str,
    catalog: bytes,
    catalog_digest: str,
) -> TesseractRuntimeAdmission:
    return admit_tesseract_runtime(
        executable,
        tessdata,
        manifest_bytes=manifest,
        expected_manifest_sha256=digest,
        language_catalog_bytes=catalog,
        expected_language_catalog_sha256=catalog_digest,
    )


def test_packaged_manifest_is_the_reviewed_hash_addressed_copy() -> None:
    content = packaged_tesseract_runtime_manifest().read_bytes()

    assert hashlib.sha256(content).hexdigest() == TESSERACT_RUNTIME_MANIFEST_SHA256
    assert (
        content
        == (
            Path(__file__).parents[3] / "packaging" / "tesseract-runtime-closure-v2.json"
        ).read_bytes()
    )


def test_current_manifest_distinguishes_vendor_and_source_build_providers() -> None:
    content = packaged_tesseract_runtime_manifest().read_bytes()
    document = _document(content)
    files = runtime_module._parse_manifest(content)
    assert len(files) == 38
    assert document["schema_version"] == 2
    assert len(document["packages"]) == 28
    assert len(document["source_builds"]) == 1
    assert document["closure_evidence"]["provider_count"] == 29
    assert not any(file.path.as_posix() == "bin/libjbig-0.dll" for file in files)
    source = document["source_builds"][0]
    native_file = next(file for file in document["files"] if file["path"] == "bin/libtiff-6.dll")
    assert "package_id" not in native_file
    assert native_file["source_build_id"] == source["source_build_id"]
    assert native_file["sha256"] == source["sha256"]
    assert native_file["size_bytes"] == source["size_bytes"] == 557_070
    assert source["sha256"] == "1f41506b92ab95df1511410122ee2eade01dacb49d2206242f1e460426faeae2"
    assert source["signature_status"] == "source-built-not-MSYS2-signed"


def test_historical_v1_is_preserved_and_rejected_by_current_parser() -> None:
    path = Path(__file__).parents[3] / "packaging/tesseract-runtime-closure-v1.json"
    historical = path.read_bytes()
    assert hashlib.sha256(historical).hexdigest() == (
        "1ffb85724770b4c30aa4d982564d548dadf466ebd5861cd827d2836198119c0a"
    )
    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_schema"):
        runtime_module._parse_manifest(historical)


@pytest.mark.parametrize("version", [1, 3, "2", None])
def test_unknown_or_invalid_manifest_version_fails_without_runtime_mutation(
    tmp_path: Path, version: object
) -> None:
    executable, tessdata, content, _digest = _runtime(tmp_path)
    original = executable.read_bytes()
    document = _document(content)
    document["schema_version"] = version
    changed, digest = _encode(document)
    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_schema"):
        _admit(executable, tessdata, changed, digest)
    assert executable.read_bytes() == original


@pytest.mark.parametrize("mutation", ["legacy", "source_digest", "recipe_digest", "source_missing"])
def test_packaged_authority_rejects_replaced_or_tampered_source_build_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    executable, tessdata, _content, _digest = _runtime(tmp_path)
    canonical = packaged_tesseract_runtime_manifest().read_bytes()
    if mutation == "legacy":
        changed = (
            Path(__file__).parents[3] / "packaging/tesseract-runtime-closure-v1.json"
        ).read_bytes()
    else:
        document = _document(canonical)
        if mutation == "source_digest":
            document["source_builds"][0]["sha256"] = "0" * 64
        elif mutation == "recipe_digest":
            document["source_builds"][0]["recipe_sha256"] = "0" * 64
        else:
            document["source_builds"] = []
        changed, _changed_digest = _encode(document)
    original_reader = runtime_module._read_packaged_resource

    def substituted_reader(path: Path, context: str) -> bytes:
        return changed if context == "manifest_resource" else original_reader(path, context)

    monkeypatch.setattr(runtime_module, "_read_packaged_resource", substituted_reader)
    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_hash"):
        admit_packaged_tesseract_runtime(executable, tessdata)


@pytest.mark.parametrize("mutation", ["size", "hash", "missing"])
def test_declared_rebuilt_libtiff_file_fingerprint_is_enforced(
    tmp_path: Path, mutation: str
) -> None:
    executable, tessdata, content, _digest = _runtime(tmp_path)
    document = _document(content)
    entry = next(file for file in document["files"] if file["path"] == "bin/runtime-00.dll")
    old = executable.parent / "runtime-00.dll"
    target = executable.parent / "libtiff-6.dll"
    old.rename(target)
    entry["path"] = "bin/libtiff-6.dll"
    changed, digest = _encode(document)
    _admit(executable, tessdata, changed, digest)
    if mutation == "missing":
        target.unlink()
    elif mutation == "size":
        target.write_bytes(target.read_bytes() + b"x")
    else:
        target.write_bytes(b"x" * target.stat().st_size)
    with pytest.raises(TesseractRuntimeAdmissionError, match=mutation):
        _admit(executable, tessdata, changed, digest)


def test_packaged_language_catalog_is_hash_addressed_and_matches_canonical() -> None:
    content = packaged_tesseract_language_pack_catalog().read_bytes()
    canonical = (
        Path(__file__).parents[3] / "packaging" / "tesseract-language-pack-catalog-v1.json"
    ).read_bytes()

    assert content == canonical
    assert hashlib.sha256(content).hexdigest() == TESSERACT_LANGUAGE_PACK_CATALOG_SHA256
    document = json.loads(content)
    german = document["packs"][0]
    assert german["language_code"] == "deu"
    assert german["size_bytes"] == 1_525_436
    assert german["sha256"] == ("19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d")
    assert document["trust"]["signature_evidence_status"] == (
        "required-upstream-not-reverified-for-pws-0205"
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
    assert admission.installed_language_codes == ("eng",)
    assert admission.available_language_set_ids == ("latin-default-v1",)


def test_runtime_inventory_stops_after_the_first_entry_over_its_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "bounded-runtime"
    runtime_root.mkdir()
    scanner = _CountingScandir(runtime_module._MAX_RUNTIME_ENTRIES + 1_000)
    monkeypatch.setattr(os, "scandir", lambda _path: scanner)
    monkeypatch.setattr(
        runtime_module,
        "_require_only_unnamed_data_stream",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(TesseractRuntimeAdmissionError, match="inventory"):
        runtime_module._inventory_runtime(runtime_root)

    assert scanner.next_calls == runtime_module._MAX_RUNTIME_ENTRIES + 1


def test_runtime_file_hashing_reads_only_the_expected_size_plus_one_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"bounded reviewed payload"
    target = tmp_path / "reviewed-runtime-file.bin"
    target.write_bytes(payload)
    expected = runtime_module._ManifestFile(
        path=PurePosixPath(target.name),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    real_open = Path.open
    read_requests: list[int] = []

    def open_with_extra(path: Path, *args: Any, **kwargs: Any) -> Any:
        inner = real_open(path, *args, **kwargs)
        if path == target:
            return _ExtraByteReader(inner, read_requests)
        return inner

    monkeypatch.setattr(Path, "open", open_with_extra)
    monkeypatch.setattr(
        runtime_module,
        "_require_only_unnamed_data_stream",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(TesseractRuntimeAdmissionError, match="size"):
        runtime_module._verify_file(target, expected)

    assert read_requests == [len(payload), 1]


def test_packaged_resource_read_is_bounded_by_authenticated_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"bounded packaged resource"
    target = tmp_path / "packaged-resource.json"
    target.write_bytes(payload)
    real_open = Path.open
    read_requests: list[int] = []

    def open_with_extra(path: Path, *args: Any, **kwargs: Any) -> Any:
        inner = real_open(path, *args, **kwargs)
        if path == target:
            return _ExtraByteReader(inner, read_requests)
        return inner

    monkeypatch.setattr(Path, "open", open_with_extra)

    with pytest.raises(TesseractRuntimeAdmissionError, match="manifest_resource"):
        runtime_module._read_packaged_resource(target, "manifest_resource")

    assert read_requests == [len(payload), 1]


@pytest.mark.parametrize(
    "runtime_root",
    (
        r"\\server\share\approved-runtime",
        r"\\?\C:\approved-runtime",
        r"\\.\C:\approved-runtime",
        r"\??\C:\approved-runtime",
    ),
)
def test_remote_and_device_locations_are_rejected_before_runtime_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_root: str,
) -> None:
    _executable, _tessdata, manifest, digest = _runtime(tmp_path)

    def unexpected_io(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("remote runtime location must fail before file I/O")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", unexpected_io)
    monkeypatch.setattr(Path, "lstat", unexpected_io)
    monkeypatch.setattr(Path, "iterdir", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)
    root = Path(runtime_root)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(root / "bin" / "tesseract.exe", root / "share" / "tessdata", manifest, digest)


@pytest.mark.parametrize("drive_type", (2, 4, 5, 6))
def test_non_fixed_drive_is_rejected_before_runtime_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drive_type: int,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)

    def unexpected_io(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("non-fixed runtime location must fail before file I/O")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", lambda _root: drive_type)
    monkeypatch.setattr(Path, "lstat", unexpected_io)
    monkeypatch.setattr(Path, "iterdir", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(executable, tessdata, manifest, digest)


def test_reparse_ancestor_is_rejected_before_runtime_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)

    def unexpected_io(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("redirected runtime location must fail before file I/O")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", lambda _root: 3)
    monkeypatch.setattr(
        runtime_module,
        "_windows_file_attributes",
        lambda _path: runtime_module._FILE_ATTRIBUTE_REPARSE_POINT,
    )
    monkeypatch.setattr(Path, "lstat", unexpected_io)
    monkeypatch.setattr(Path, "iterdir", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)

    with pytest.raises(TesseractRuntimeAdmissionError, match="reparse"):
        _admit(executable, tessdata, manifest, digest)


@pytest.mark.parametrize(
    "device_target",
    (r"\??\C:\approved-runtime", r"\Device\GoogleDriveFS"),
)
def test_fixed_classification_cannot_admit_dos_alias_or_virtual_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    device_target: str,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)

    def unexpected_provider_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("untrusted provider must fail before volume or path queries")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", lambda _root: 3)
    monkeypatch.setattr(
        runtime_module,
        "_windows_dos_device_targets",
        lambda _name: (device_target,),
    )
    monkeypatch.setattr(runtime_module, "_windows_volume_name", unexpected_provider_query)
    monkeypatch.setattr(runtime_module, "_windows_volume_information", unexpected_provider_query)
    monkeypatch.setattr(runtime_module, "_windows_file_attributes", unexpected_provider_query)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(executable, tessdata, manifest, digest)


def test_volume_without_persistent_acls_is_rejected_before_path_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    device = r"\Device\HarddiskVolume7"

    def unexpected_path_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsupported volume must fail before path queries")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", lambda _root: 3)
    monkeypatch.setattr(
        runtime_module,
        "_windows_dos_device_targets",
        lambda _name: (device,),
    )
    monkeypatch.setattr(
        runtime_module,
        "_windows_volume_name",
        lambda _root: r"\\?\Volume{00000000-0000-0000-0000-000000000007}" + "\\",
    )
    monkeypatch.setattr(
        runtime_module,
        "_windows_volume_information",
        lambda _root: (0, "FAT32"),
    )
    monkeypatch.setattr(
        runtime_module,
        "_windows_volume_paths",
        lambda _volume_name: (executable.anchor,),
    )
    monkeypatch.setattr(runtime_module, "_windows_file_attributes", unexpected_path_query)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(executable, tessdata, manifest, digest)


def test_direct_physical_device_alias_must_be_registered_by_mount_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    device = r"\Device\HarddiskVolume7"

    def unexpected_provider_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unregistered DOS alias must fail before provider path queries")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", lambda _root: 3)
    monkeypatch.setattr(
        runtime_module,
        "_windows_dos_device_targets",
        lambda _name: (device,),
    )
    monkeypatch.setattr(
        runtime_module,
        "_windows_volume_name",
        lambda _root: r"\\?\Volume{00000000-0000-0000-0000-000000000007}" + "\\",
    )
    monkeypatch.setattr(runtime_module, "_windows_volume_paths", lambda _name: ())
    monkeypatch.setattr(runtime_module, "_windows_volume_information", unexpected_provider_query)
    monkeypatch.setattr(runtime_module, "_windows_file_attributes", unexpected_provider_query)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(executable, tessdata, manifest, digest)


@pytest.mark.parametrize("second_path_kind", ("remote", "layout"))
def test_both_runtime_paths_are_validated_before_any_native_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_path_kind: str,
) -> None:
    executable, _tessdata, manifest, digest = _runtime(tmp_path)
    tessdata = (
        Path(r"\\server\share\approved-runtime\share\tessdata")
        if second_path_kind == "remote"
        else (tmp_path / "different-runtime" / "share" / "tessdata").absolute()
    )

    def unexpected_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("paired lexical and layout checks must run before native queries")

    monkeypatch.setattr(runtime_module, "_windows_drive_type", unexpected_query)
    monkeypatch.setattr(runtime_module, "_windows_dos_device_targets", unexpected_query)
    monkeypatch.setattr(runtime_module, "_windows_file_attributes", unexpected_query)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location|layout"):
        _admit(executable, tessdata, manifest, digest)


def test_offline_or_recall_attribute_is_rejected_before_runtime_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)

    def unexpected_io(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline runtime must fail before file I/O")

    monkeypatch.setattr(
        runtime_module,
        "_windows_file_attributes",
        lambda _path: runtime_module._FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    )
    monkeypatch.setattr(Path, "lstat", unexpected_io)
    monkeypatch.setattr(Path, "iterdir", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)

    with pytest.raises(TesseractRuntimeAdmissionError, match="location"):
        _admit(executable, tessdata, manifest, digest)


@pytest.mark.parametrize(
    "relative_target",
    ("bin/runtime-00.dll", "share/tessdata/eng.traineddata"),
)
def test_offline_closure_entry_is_rejected_before_stream_or_content_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_target: str,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    root = executable.parent.parent
    target = root.joinpath(*relative_target.split("/"))
    target_identity = runtime_module._file_identity(target.lstat())
    real_attribute_check = runtime_module._has_unsupported_runtime_attributes
    real_stream_check = runtime_module._require_only_unnamed_data_stream

    def has_unsupported_attributes(metadata: os.stat_result) -> bool:
        return runtime_module._file_identity(metadata) == target_identity or real_attribute_check(
            metadata
        )

    def stream_check(
        path: Path,
        *,
        is_directory: bool,
        expected_size: int = 0,
    ) -> None:
        if os.path.normcase(path) == os.path.normcase(target):
            raise AssertionError("offline closure entry must fail before stream enumeration")
        real_stream_check(path, is_directory=is_directory, expected_size=expected_size)

    monkeypatch.setattr(
        runtime_module,
        "_has_unsupported_runtime_attributes",
        has_unsupported_attributes,
    )
    monkeypatch.setattr(runtime_module, "_require_only_unnamed_data_stream", stream_check)

    with pytest.raises(TesseractRuntimeAdmissionError, match="attributes"):
        _admit(executable, tessdata, manifest, digest)


def test_exact_present_optional_pack_is_merged_into_admission(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    german = tessdata / "deu.traineddata"
    german.write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)

    admission = _admit_with_catalog(
        executable,
        tessdata,
        manifest,
        digest,
        catalog,
        catalog_digest,
    )

    assert german in admission.resources
    assert admission.installed_language_codes == ("deu", "eng")
    assert admission.available_language_set_ids == (
        "english-german-fast-v1",
        "german-fast-v1",
        "latin-default-v1",
    )


def test_catalogued_optional_pack_may_be_absent(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    catalog, catalog_digest = _language_catalog(b"not installed")

    admission = _admit_with_catalog(
        executable,
        tessdata,
        manifest,
        digest,
        catalog,
        catalog_digest,
    )

    assert admission.installed_language_codes == ("eng",)
    assert admission.available_language_set_ids == ("latin-default-v1",)


@pytest.mark.parametrize("optional_pack", (False, True))
def test_named_data_stream_on_base_or_optional_file_is_rejected(
    tmp_path: Path,
    optional_pack: bool,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    target = executable.parent / "runtime-00.dll"
    catalog: bytes | None = None
    catalog_digest: str | None = None
    if optional_pack:
        target = tessdata / "deu.traineddata"
        target.write_bytes(payload)
        catalog, catalog_digest = _language_catalog(payload)
    stream = Path(f"{target}:private-stream-canary")
    try:
        stream.write_bytes(b"unlisted alternate data")
    except OSError as error:
        pytest.skip(f"alternate data streams unavailable: {error.winerror}")

    try:
        with pytest.raises(TesseractRuntimeAdmissionError, match="stream") as raised:
            if catalog is None or catalog_digest is None:
                _admit(executable, tessdata, manifest, digest)
            else:
                _admit_with_catalog(
                    executable,
                    tessdata,
                    manifest,
                    digest,
                    catalog,
                    catalog_digest,
                )
    finally:
        stream.unlink(missing_ok=True)

    assert "private-stream-canary" not in str(raised.value)
    assert "private-stream-canary" not in repr(raised.value)


def test_language_catalog_hash_mismatch_is_rejected_before_inventory(
    tmp_path: Path,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    catalog, _catalog_digest = _language_catalog(b"not installed")
    (executable.parent / "runtime-00.dll").unlink()

    with pytest.raises(TesseractRuntimeAdmissionError, match="language_catalog_hash"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            "0" * 64,
        )


@pytest.mark.parametrize(
    "mutation",
    ["extra_top_level_member", "network_enabled", "unknown_language"],
)
def test_language_catalog_schema_is_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    catalog, _catalog_digest = _language_catalog(b"not installed")
    document = _document(catalog)
    if mutation == "extra_top_level_member":
        document["unreviewed"] = True
    elif mutation == "network_enabled":
        policy = document["runtime_policy"]
        assert isinstance(policy, dict)
        policy["network_download_allowed"] = True
    else:
        packs = document["packs"]
        assert isinstance(packs, list)
        pack = packs[0]
        assert isinstance(pack, dict)
        pack["language_code"] = "fra"
    changed, changed_digest = _encode(document)

    with pytest.raises(TesseractRuntimeAdmissionError, match="language_catalog_schema"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            changed,
            changed_digest,
        )


def test_language_catalog_must_cover_every_supported_optional_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    catalog, catalog_digest = _language_catalog(b"not installed")
    monkeypatch.setattr(
        runtime_module,
        "OCR_LANGUAGE_SET_DEFINITIONS",
        (
            *OCR_LANGUAGE_SET_DEFINITIONS,
            OcrLanguageSetDefinition("french-fast-v1", ("fra",)),
        ),
    )

    with pytest.raises(TesseractRuntimeAdmissionError, match="language_catalog_schema"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


@pytest.mark.parametrize("mutation", ["size", "hash"])
def test_present_optional_pack_must_match_catalog_exactly(
    tmp_path: Path,
    mutation: str,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    german = tessdata / "deu.traineddata"
    german.write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)
    german.write_bytes(payload + b"x" if mutation == "size" else b"x" * len(payload))

    with pytest.raises(TesseractRuntimeAdmissionError, match=mutation):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


def test_unlisted_file_stays_rejected_when_catalog_is_present(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    catalog, catalog_digest = _language_catalog(b"not installed")
    (tessdata / "fra.traineddata").write_bytes(b"not catalogued")

    with pytest.raises(TesseractRuntimeAdmissionError, match="unlisted"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


def test_optional_pack_reparse_is_rejected_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    german = tessdata / "deu.traineddata"
    german.write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)
    german_metadata = german.lstat()
    real_is_reparse = runtime_module._is_reparse

    def is_reparse(metadata: os.stat_result) -> bool:
        return (
            metadata.st_dev == german_metadata.st_dev and metadata.st_ino == german_metadata.st_ino
        ) or real_is_reparse(metadata)

    monkeypatch.setattr(runtime_module, "_is_reparse", is_reparse)

    with pytest.raises(TesseractRuntimeAdmissionError, match="reparse"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


def test_optional_pack_hardlink_is_rejected_before_hashing(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    german = tessdata / "deu.traineddata"
    german.write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)
    alias = tmp_path / "outside-german-language-alias"
    try:
        os.link(german, alias)
    except OSError as error:
        pytest.skip(f"hard links are unavailable on this test volume: {error.errno}")

    with pytest.raises(TesseractRuntimeAdmissionError, match="type"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


def test_optional_pack_case_variant_is_rejected(tmp_path: Path) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    (tessdata / "DEU.traineddata").write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)

    with pytest.raises(TesseractRuntimeAdmissionError, match="case_collision"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


def test_optional_pack_identity_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, tessdata, manifest, digest = _runtime(tmp_path)
    payload = b"synthetic reviewed German language data"
    german = tessdata / "deu.traineddata"
    german.write_bytes(payload)
    catalog, catalog_digest = _language_catalog(payload)
    real_lstat = Path.lstat
    german_calls = 0

    def changed_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        nonlocal german_calls
        metadata = real_lstat(path)
        if path != german:
            return metadata
        german_calls += 1
        if german_calls == 1:
            return metadata
        return SimpleNamespace(
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns + 1,
            st_mode=metadata.st_mode,
            st_nlink=metadata.st_nlink,
            st_file_attributes=getattr(metadata, "st_file_attributes", 0),
        )

    monkeypatch.setattr(Path, "lstat", changed_lstat)

    with pytest.raises(TesseractRuntimeAdmissionError, match="changed"):
        _admit_with_catalog(
            executable,
            tessdata,
            manifest,
            digest,
            catalog,
            catalog_digest,
        )


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
