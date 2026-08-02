"""Windows integration evidence for the PWS-0103 authenticated source lease."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

import paperworkshell.storage._source_windows as source_windows_module
import paperworkshell.storage.source_precheck as source_precheck_module
from paperworkshell.domain.import_precheck import (
    DetectedMediaType,
    ImportLimits,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
    ImportPrecheckWarningCode,
    ImportPreliminaryState,
)
from paperworkshell.storage._source_windows import (
    AuthenticatedSourceHandle,
    open_authenticated_source,
)
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from tools.generate_synthetic_fixtures import generate_oversized_pdf

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0103 targets the supported Windows source boundary"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1"


def _limits(**overrides: int) -> ImportLimits:
    values = {
        "maximum_source_bytes": 2_000_000,
        "maximum_pdf_pages": 100,
        "maximum_image_width": 10_000,
        "maximum_image_height": 10_000,
        "maximum_image_pixels": 50_000_000,
        "maximum_decompressed_bytes": 200_000_000,
        "decompression_warning_ratio": 100,
        "maximum_decompression_ratio": 2_000,
        "maximum_header_bytes": 4_096,
        "maximum_structure_records": 64,
    }
    values.update(overrides)
    return ImportLimits(**values)


def _jpeg(*, width: int = 24, height: int = 12) -> bytes:
    components = b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    frame = b"\x08" + struct.pack(">HHB", height, width, 3) + components
    scan = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    return (
        b"\xff\xd8\xff\xc0"
        + struct.pack(">H", len(frame) + 2)
        + frame
        + b"\xff\xda"
        + struct.pack(">H", len(scan) + 2)
        + scan
        + b"\xff\xd9"
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("pdf/blank.pdf", DetectedMediaType.PDF),
        ("pdf/incremental-update.pdf", DetectedMediaType.PDF),
        ("image/handwriting-demonstration.png", DetectedMediaType.PNG),
        ("image/low-resolution.png", DetectedMediaType.PNG),
        ("image/transparent.png", DetectedMediaType.PNG),
    ],
)
def test_committed_synthetic_pdf_and_png_sources_use_one_live_authenticated_handle(
    relative: str,
    expected: DetectedMediaType,
) -> None:
    path = (SYNTHETIC_ROOT / relative).resolve()
    before_digest = _digest(path)
    before_stat = path.stat()

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.is_open
        assert source.result.media_type is expected
        assert source.result.byte_size == before_stat.st_size
        assert source.identity.file_id
        assert str(path) not in repr(source)

    after_stat = path.stat()
    assert _digest(path) == before_digest
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size


def test_task_local_deterministic_jpeg_header_carrier_is_detected(tmp_path: Path) -> None:
    path = (tmp_path / "synthetic.jpg").resolve()
    value = _jpeg()
    path.write_bytes(value)

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.result.media_type is DetectedMediaType.JPEG
        assert source.result.image_info is not None
        assert source.result.image_info.width == 24
        assert source.result.image_info.height == 12
        assert source.read_at(0, len(value)) == value

    path.write_bytes(value)
    assert path.read_bytes() == value


def test_exactly_one_authenticated_source_open_is_retained_then_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    calls = 0

    def counting_open(selected: Path) -> AuthenticatedSourceHandle:
        nonlocal calls
        calls += 1
        return open_authenticated_source(selected)

    monkeypatch.setattr(source_precheck_module, "open_authenticated_source", counting_open)
    with WindowsSourcePrechecker(_limits()).open(path) as source:
        retained = source
        assert retained.result.media_type is DetectedMediaType.PDF

    assert calls == 1
    assert not retained.is_open
    with pytest.raises(ImportPrecheckError) as raised:
        retained.read_at(0, 1)
    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_CHANGED


def test_every_native_open_requests_no_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    delegate = source_windows_module._open_raw_handle
    observed_flags: list[int] = []

    def record_flags(selected: Path, access: int, flags: int, operation: str) -> int:
        observed_flags.append(flags)
        return delegate(selected, access, flags, operation)

    monkeypatch.setattr(source_windows_module, "_open_raw_handle", record_flags)
    with WindowsSourcePrechecker(_limits()).open(path):
        pass

    assert observed_flags
    assert all(flags & 0x0010_0000 for flags in observed_flags)


def test_mixed_case_extension_uses_detected_pdf_content(tmp_path: Path) -> None:
    path = (tmp_path / "selected.PdF").resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    path.write_bytes(value)

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.result.media_type is DetectedMediaType.PDF

    assert path.read_bytes() == value


def test_real_extension_mismatch_fails_without_source_mutation(tmp_path: Path) -> None:
    path = (tmp_path / "selected.jpg").resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    path.write_bytes(value)

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path):
            pytest.fail("mismatched source content cannot yield a lease")

    assert raised.value.code is ImportPrecheckErrorCode.EXTENSION_MISMATCH
    assert path.read_bytes() == value


def test_real_jpeg_dimension_limit_and_warning_outcomes_are_typed(tmp_path: Path) -> None:
    path = (tmp_path / "selected.jpg").resolve()
    value = _jpeg(width=100, height=100)
    path.write_bytes(value)

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits(maximum_image_width=99)).open(path):
            pytest.fail("an excessive image cannot yield a source lease")
    assert raised.value.code is ImportPrecheckErrorCode.IMAGE_DIMENSION_LIMIT_EXCEEDED

    with WindowsSourcePrechecker(
        _limits(decompression_warning_ratio=1, maximum_decompression_ratio=2_000)
    ).open(path) as source:
        assert source.result.warnings == (ImportPrecheckWarningCode.DECOMPRESSION_NEEDS_REVIEW,)

    assert path.read_bytes() == value


def test_committed_truncated_pdf_is_rejected_without_source_mutation() -> None:
    path = (SYNTHETIC_ROOT / "pdf" / "malformed-truncated.pdf").resolve()
    before_digest = _digest(path)
    before_mtime = path.stat().st_mtime_ns

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path):
            pytest.fail("a truncated PDF cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED
    assert raised.value.state is ImportPreliminaryState.MALFORMED
    assert _digest(path) == before_digest
    assert path.stat().st_mtime_ns == before_mtime


def test_on_demand_oversized_pdf_is_rejected_before_header_processing(tmp_path: Path) -> None:
    path = generate_oversized_pdf(
        tmp_path.resolve(), repository_root=PROJECT_ROOT, byte_count=1_048_576
    )
    before_digest = _digest(path)

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits(maximum_source_bytes=1_048_575)).open(path):
            pytest.fail("an oversized source cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_TOO_LARGE
    assert _digest(path) == before_digest


def test_live_lease_denies_write_replace_and_delete_then_closes_for_retry(tmp_path: Path) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    replacement = (tmp_path / "replacement.pdf").resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    path.write_bytes(value)
    replacement.write_bytes(value)

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        with pytest.raises(OSError):
            path.write_bytes(b"changed")
        with pytest.raises(OSError):
            os.replace(replacement, path)
        with pytest.raises(OSError):
            path.unlink()
        assert source.read_at(0, len(value)) == value

    path.write_bytes(value)
    with WindowsSourcePrechecker(_limits()).open(path) as retry:
        assert retry.result.media_type is DetectedMediaType.PDF


def test_live_lease_pins_ancestor_names_until_terminal_verification(tmp_path: Path) -> None:
    parent = (tmp_path / "selected-parent").resolve()
    renamed = (tmp_path / "renamed-parent").resolve()
    parent.mkdir()
    path = parent / "selected.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        with pytest.raises(OSError):
            parent.rename(renamed)
        assert source.result.media_type is DetectedMediaType.PDF

    parent.rename(renamed)
    assert (renamed / "selected.pdf").is_file()


def test_existing_writer_prevents_a_false_snapshot_success(tmp_path: Path) -> None:
    path = (tmp_path / "busy.pdf").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")

    with path.open("r+b") as writer:
        with pytest.raises(ImportPrecheckError) as raised:
            with WindowsSourcePrechecker(_limits()).open(path):
                pytest.fail("a source with a live writer cannot be authenticated")
        writer.seek(0, os.SEEK_END)

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_UNAVAILABLE
    assert str(path) not in str(raised.value)


def test_hardlink_created_while_live_is_detected_by_terminal_same_handle_recheck(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    alias = (tmp_path / "alias.pdf").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path) as source:
            os.link(path, alias)
            assert source.result.media_type is DetectedMediaType.PDF

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_CHANGED
    assert path.read_bytes() == alias.read_bytes()


def test_preexisting_hardlink_alias_is_rejected_before_content_is_authoritative(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "selected.pdf").resolve()
    alias = (tmp_path / "alias.pdf").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    os.link(path, alias)

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path):
            pytest.fail("a hard-linked source cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_ALIAS_DETECTED


def test_missing_source_returns_one_sanitized_unavailable_error(tmp_path: Path) -> None:
    path = (tmp_path / "private-missing-source.pdf").resolve()

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(path):
            pytest.fail("a missing source cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_UNAVAILABLE
    assert str(path) not in str(raised.value)


def test_sparse_logical_size_is_rejected_before_content_inspection(tmp_path: Path) -> None:
    path = (tmp_path / "sparse.pdf").resolve()
    with path.open("wb") as stream:
        stream.seek(2_000_000 - 1)
        stream.write(b"\x00")

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits(maximum_source_bytes=1_999_999)).open(path):
            pytest.fail("an excessive logical source size cannot yield a lease")

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_TOO_LARGE
    assert path.stat().st_size == 2_000_000


def test_jpeg_fill_bomb_has_a_small_fixed_native_read_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (tmp_path / "fill-bomb.jpg").resolve()
    path.write_bytes(b"\xff\xd8" + b"\xff" * (1_048_576 - 2))
    delegate = AuthenticatedSourceHandle.read_at
    native_reads = 0

    def counting_read(handle: AuthenticatedSourceHandle, offset: int, count: int) -> bytes:
        nonlocal native_reads
        native_reads += 1
        return delegate(handle, offset, count)

    monkeypatch.setattr(AuthenticatedSourceHandle, "read_at", counting_read)
    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(
            _limits(
                maximum_header_bytes=1_048_576,
                maximum_structure_records=64,
            )
        ).open(path):
            pytest.fail("a JPEG fill bomb cannot yield a usable lease")

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED
    assert raised.value.context == "limits.structure_records"
    assert native_reads <= 70


def test_final_file_symlink_is_rejected_without_reading_target(tmp_path: Path) -> None:
    target = (tmp_path / "target.pdf").resolve()
    link = (tmp_path / "selected.pdf").resolve()
    value = b"%PDF-1.7\n%%EOF\n"
    target.write_bytes(value)
    try:
        os.symlink(target, link, target_is_directory=False)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("current Windows account lacks symbolic-link privilege; junction test ran")
        raise
    try:
        with pytest.raises(ImportPrecheckError) as raised:
            with WindowsSourcePrechecker(_limits()).open(link):
                pytest.fail("a final source reparse point cannot yield a usable lease")

        assert raised.value.code is ImportPrecheckErrorCode.SOURCE_REPARSE_POINT
        assert target.read_bytes() == value
    finally:
        link.unlink(missing_ok=True)


def test_replacement_before_authenticated_open_uses_only_the_opened_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperworkshell.storage.source_precheck as source_module

    path = (tmp_path / "selected").resolve()
    replacement = (tmp_path / "replacement").resolve()
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    replacement.write_bytes(_jpeg())
    delegate = open_authenticated_source

    def replace_then_open(selected: Path) -> AuthenticatedSourceHandle:
        os.replace(replacement, selected)
        return delegate(selected)

    monkeypatch.setattr(source_module, "open_authenticated_source", replace_then_open)

    with WindowsSourcePrechecker(_limits()).open(path) as source:
        assert source.result.media_type is DetectedMediaType.JPEG
        assert source.read_at(0, 2) == b"\xff\xd8"


@pytest.mark.parametrize(
    "configured",
    [
        Path(r"relative.pdf"),
        Path(r"C:drive-relative.pdf"),
        Path(r"\\server\share\source.pdf"),
        Path(r"\\?\C:\source.pdf"),
        Path(r"\\.\PhysicalDrive0"),
        Path(r"\??\C:\source.pdf"),
        Path(r"C:\source.pdf:alternate"),
    ],
)
def test_nonlocal_device_relative_and_ads_paths_fail_before_open(configured: Path) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(configured):
            pytest.fail("an unsafe source path cannot yield a usable lease")

    assert raised.value.code in {
        ImportPrecheckErrorCode.SOURCE_PATH_INVALID,
        ImportPrecheckErrorCode.SOURCE_LOCATION_UNSUPPORTED,
    }
    assert str(configured) not in str(raised.value)


def test_directory_is_not_accepted_as_a_document_source(tmp_path: Path) -> None:
    directory = (tmp_path / "source.pdf").resolve()
    directory.mkdir()

    with pytest.raises(ImportPrecheckError) as raised:
        with WindowsSourcePrechecker(_limits()).open(directory):
            pytest.fail("a directory cannot yield a usable source lease")

    assert raised.value.code in {
        ImportPrecheckErrorCode.SOURCE_NOT_REGULAR,
        ImportPrecheckErrorCode.SOURCE_UNAVAILABLE,
    }
