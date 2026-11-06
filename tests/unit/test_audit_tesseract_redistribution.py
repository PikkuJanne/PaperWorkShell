"""Synthetic archive and PE evidence tests; no third-party binaries are fixtures."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from tools import audit_tesseract_redistribution as audit_module
from tools.audit_tesseract_redistribution import (
    BLOCKER_CODE,
    JBIG_PACKAGE,
    PE_EVIDENCE_PATHS,
    PREFIX,
    REQUIRED_IMPORT_EDGES,
    ArchiveSpec,
    RedistributionAuditError,
    SelectedFile,
    audit,
    inspect_archive,
    load_specs,
    main,
    pe_imports,
)


def synthetic_pe(imports: tuple[str, ...] = ()) -> bytes:
    """Construct data tables only; this image contains no code or executable entry."""
    content = bytearray(4096)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 60, 128)
    content[128:132] = b"PE\0\0"
    struct.pack_into("<HH", content, 132, 0x8664, 1)
    struct.pack_into("<H", content, 148, 240)
    optional = 152
    struct.pack_into("<H", content, optional, 0x20B)
    struct.pack_into("<Q", content, optional + 24, 0x140000000)
    struct.pack_into("<I", content, optional + 108, 16)
    section = optional + 240
    struct.pack_into("<IIII", content, section + 8, 3584, 4096, 3584, 512)
    if imports:
        struct.pack_into("<II", content, optional + 120, 4096, 2048)
        name_offset = 2048
        for index, name in enumerate(imports):
            encoded = name.encode("ascii") + b"\0"
            struct.pack_into("<I", content, 512 + index * 20 + 12, 4096 + name_offset - 512)
            content[name_offset : name_offset + len(encoded)] = encoded
            name_offset += len(encoded)
    return bytes(content)


def synthetic_archive(
    directory: Path,
    files: dict[str, bytes] | None = None,
    *,
    package: str = PREFIX + "tesseract-ocr",
    labels: tuple[str, ...] = ("spdx:Apache-2.0",),
    metadata_labels: tuple[str, ...] | None = None,
    extra: tuple[tarfile.TarInfo, ...] = (),
) -> tuple[Path, ArchiveSpec]:
    files = files or {"bin/synthetic.dll": b"synthetic-native-data"}
    selected = tuple(
        SelectedFile(name, package, len(value), hashlib.sha256(value).hexdigest())
        for name, value in files.items()
    )
    labels_text = "".join("license = " + label + "\n" for label in metadata_labels or labels)
    metadata = ("pkgname = " + package + "\npkgver = 1.0-1\n" + labels_text).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:zst") as archive:
        for name, value in {
            ".PKGINFO": metadata,
            **{"ucrt64/" + name: value for name, value in files.items()},
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
        for member in extra:
            archive.addfile(member, io.BytesIO(b"x" * member.size) if member.isfile() else None)
    raw = output.getvalue()
    path = directory / "synthetic.pkg.tar.zst"
    path.write_bytes(raw)
    spec = ArchiveSpec(
        package, "1.0-1", path.name, len(raw), hashlib.sha256(raw).hexdigest(), labels, selected
    )
    return path, spec


def test_frozen_specs_cover_exact_development_inputs() -> None:
    specs = load_specs()
    assert len(specs) == 30
    assert sum(len(spec.files) for spec in specs) == 39
    jbig = next(spec for spec in specs if spec.package_id == JBIG_PACKAGE)
    assert jbig.license_labels == ("spdx:GPL-2.0",)
    assert jbig.files[0].path == "bin/libjbig-0.dll"


def test_unreviewed_closure_cannot_change_the_audit_inputs(tmp_path: Path) -> None:
    destination = tmp_path / audit_module.CLOSURE_PATH
    destination.parent.mkdir()
    destination.write_bytes(b'{"schema_version":999}')
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_CLOSURE$"):
        load_specs(tmp_path)


def test_metadata_owner_and_selected_member_are_verified(tmp_path: Path) -> None:
    path, spec = synthetic_archive(tmp_path)
    assert inspect_archive(path, spec) == {}


def test_missing_archive_is_not_a_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--artifacts", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "invalid"
    assert report["release_approved"] is False


@pytest.mark.parametrize("change", ["same-size", "shorter", "longer"])
def test_archive_bytes_must_match_pinned_hash(tmp_path: Path, change: str) -> None:
    path, spec = synthetic_archive(tmp_path)
    raw = path.read_bytes()
    path.write_bytes(
        {"same-size": b"x" * len(raw), "shorter": raw[:-1], "longer": raw + b"x"}[change]
    )
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_(ARCHIVE_HASH|LIMIT)$"):
        inspect_archive(path, spec)


def test_selected_file_digest_must_match(tmp_path: Path) -> None:
    path, spec = synthetic_archive(tmp_path)
    bad = replace(spec, files=(replace(spec.files[0], sha256="0" * 64),))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_MEMBER_HASH$"):
        inspect_archive(path, bad)


def test_missing_selected_member_is_rejected(tmp_path: Path) -> None:
    path, spec = synthetic_archive(tmp_path)
    missing = replace(spec.files[0], path="bin/missing.dll")
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_MISSING_MEMBER$"):
        inspect_archive(path, replace(spec, files=(missing,)))


def test_unreviewed_metadata_license_cannot_be_reported_as_reviewed(tmp_path: Path) -> None:
    path, spec = synthetic_archive(tmp_path, metadata_labels=("unreviewed-license",))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_METADATA$"):
        inspect_archive(path, spec)


def test_changed_jbig_metadata_cannot_use_frozen_license_route(tmp_path: Path) -> None:
    path, spec = synthetic_archive(tmp_path, package=JBIG_PACKAGE, labels=("spdx:GPL-2.0",))
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_JBIG_METADATA$"):
        inspect_archive(path, spec)


def test_changed_jbig_license_is_rejected_even_with_matching_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = tarfile.TarInfo(audit_module.JBIG_LICENSE_PATH)
    member.size = 16
    path, spec = synthetic_archive(
        tmp_path, package=JBIG_PACKAGE, labels=("spdx:GPL-2.0",), extra=(member,)
    )
    with tarfile.open(path, "r:zst") as archive:
        source = archive.extractfile(".PKGINFO")
        assert source is not None
        metadata_digest = hashlib.sha256(source.read()).hexdigest()
    monkeypatch.setattr(audit_module, "JBIG_METADATA_SHA256", metadata_digest)
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_JBIG_LICENSE$"):
        inspect_archive(path, spec)


def test_pe_import_tables_are_read_without_loading_code() -> None:
    image = synthetic_pe(("libtiff-6.dll", "libstdc++-6.dll", "KERNEL32.dll"))
    assert pe_imports(image) == (("kernel32.dll", "libstdc++-6.dll", "libtiff-6.dll"), ())
    assert pe_imports(synthetic_pe()) == ((), ())


@pytest.mark.parametrize("image", [b"", b"MZ", b"synthetic not a PE image", synthetic_pe()[:300]])
def test_malformed_pe_has_no_evidence(image: bytes) -> None:
    with pytest.raises((RedistributionAuditError, struct.error)):
        pe_imports(image)


def test_repeated_normal_import_is_rejected() -> None:
    with pytest.raises(RedistributionAuditError, match="^OCR_AUDIT_PE$"):
        pe_imports(synthetic_pe(("libtiff-6.dll", "libtiff-6.dll")))


def test_confirmed_required_import_chain_is_always_a_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = {
        name: synthetic_pe(
            tuple(Path(end).name for start, end in sorted(REQUIRED_IMPORT_EDGES) if start == name)
        )
        for name in sorted(PE_EVIDENCE_PATHS)
    }
    _, spec = synthetic_archive(tmp_path, images)
    monkeypatch.setattr(audit_module, "load_specs", lambda root: (spec,))
    report = audit(tmp_path)
    assert report["status"] == "blocked"
    assert report["code"] == BLOCKER_CODE
    assert report["release_approved"] is False
    assert report == audit(tmp_path)
    assert len(report["normal_import_edges"]) == len(REQUIRED_IMPORT_EDGES)  # type: ignore[arg-type]


def test_main_sanitizes_malformed_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path, spec = synthetic_archive(tmp_path)
    raw = b"private filename and document text are not archive evidence"
    path.write_bytes(raw)
    bad = replace(spec, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(audit_module, "load_specs", lambda root: (bad,))
    assert main(["--artifacts", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "invalid"
    assert str(tmp_path) not in output
    assert "private filename" not in output
