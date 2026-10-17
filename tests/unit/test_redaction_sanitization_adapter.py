"""Bounded private rewriting and source/output ownership contracts."""

from __future__ import annotations

import errno
import hashlib
import zlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import pikepdf
import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.pdf.redaction_sanitization import (
    PikepdfRedactionSanitizer,
    _canonical_content,
    _Graph,
    _Output,
)


class _Control:
    def __init__(self, *, cancel_at: int = 0, expired: bool = False) -> None:
        self.calls, self.cancel_at, self.expired = 0, cancel_at, expired

    def cancellation_requested(self) -> bool:
        self.calls += 1
        return self.cancel_at != 0 and self.calls >= self.cancel_at

    def deadline_expired(self) -> bool:
        return self.expired


def _content(*, resource_name: str = "/F1") -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                {
                    resource_name: pikepdf.Dictionary(
                        Type=pikepdf.Name.Font,
                        Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name.Helvetica,
                    ),
                }
            )
        )
        page.Contents = pikepdf.Stream(
            document, f"BT {resource_name} 8 Tf 4 20 Td (SYNTHETIC KEEP) Tj ET".encode("ascii")
        )
        document.docinfo["/Subject"] = "SYNTHETIC REMOVE"
        output = BytesIO()
        document.save(output, deterministic_id=True)
        return output.getvalue()


def _request(content: bytes) -> RedactionSanitizationRequest:
    return RedactionSanitizationRequest(
        uuid4(), uuid4(), hashlib.sha256(content).hexdigest(), len(content), 1
    )


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


@pytest.mark.parametrize("resource_name", ["/F1", "/A", "/AF", "/V", "/Type"])
def test_fresh_derivative_preserves_content_and_opaque_resource_names(
    tmp_path: Path, resource_name: str
) -> None:
    content = _content(resource_name=resource_name)
    source = BytesIO(content)
    source.seek(7)
    output = tmp_path / "candidate.pdf"
    report = PikepdfRedactionSanitizer().sanitize(
        source, output, tmp_path, _request(content), _limits(), _Control()
    )
    assert source.tell() == 7
    assert source.getvalue() == content
    assert report.unsigned_derivative
    assert report.status == "awaiting_verification"
    assert report.before.metadata > 0
    assert report.after.empty
    assert report.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    with pikepdf.Pdf.open(BytesIO(content)) as original, pikepdf.Pdf.open(output) as result:
        assert pikepdf.unparse_content_stream(
            pikepdf.parse_content_stream(result.pages[0])
        ) == pikepdf.unparse_content_stream(pikepdf.parse_content_stream(original.pages[0]))
        assert list(result.pages[0].MediaBox) == list(original.pages[0].MediaBox)
        assert resource_name in result.pages[0].Resources.Font
        assert set(map(str, result.Root.keys())) == {"/Type", "/Pages"}
        assert "/Info" not in result.trailer
        assert "/Prev" not in result.trailer
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    "failure", ["cancel", "deadline", "source", "objects", "memory", "output", "temp"]
)
def test_failure_preserves_source_and_leaves_no_candidate(tmp_path: Path, failure: str) -> None:
    content = _content()
    limits, request, control = _limits(), _request(content), _Control()
    expected = PdfExportErrorCode.LIMIT_EXCEEDED
    if failure == "cancel":
        control, expected = _Control(cancel_at=20), PdfExportErrorCode.CANCELLED
    elif failure == "deadline":
        control, expected = _Control(expired=True), PdfExportErrorCode.TIMED_OUT
    elif failure == "source":
        request, expected = (
            replace(request, source_sha256="0" * 64),
            PdfExportErrorCode.SOURCE_CHANGED,
        )
    elif failure == "objects":
        limits = replace(limits, maximum_pdf_objects=1)
    elif failure == "memory":
        limits = replace(limits, maximum_memory_bytes=16)
    elif failure == "output":
        limits = replace(limits, maximum_output_bytes=300)
    else:
        limits = replace(limits, maximum_temporary_storage_bytes=16)
    source = BytesIO(content)
    source.seek(9)
    with pytest.raises(PdfExportError) as caught:
        PikepdfRedactionSanitizer().sanitize(
            source, tmp_path / "candidate.pdf", tmp_path, request, limits, control
        )
    assert caught.value.code == expected
    assert source.getvalue() == content
    assert source.tell() == 9
    assert not list(tmp_path.iterdir())


def test_existing_destination_is_never_deleted(tmp_path: Path) -> None:
    content = _content()
    output = tmp_path / "candidate.pdf"
    output.write_bytes(b"PREVIOUS SYNTHETIC OUTPUT")
    with pytest.raises(PdfExportError) as caught:
        PikepdfRedactionSanitizer().sanitize(
            BytesIO(content), output, tmp_path, _request(content), _limits(), _Control()
        )
    assert caught.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert output.read_bytes() == b"PREVIOUS SYNTHETIC OUTPUT"


def test_disk_full_cleans_owned_output_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _content()
    output = tmp_path / "candidate.pdf"
    original = _Output.write

    def disk_full(self: _Output, data: object, /) -> int:
        raise OSError(errno.ENOSPC, "SYNTHETIC DISK FULL")

    with monkeypatch.context() as patch:
        patch.setattr(_Output, "write", disk_full)
        with pytest.raises(PdfExportError):
            PikepdfRedactionSanitizer().sanitize(
                BytesIO(content), output, tmp_path, _request(content), _limits(), _Control()
            )
    assert not output.exists()
    assert _Output.write is original
    assert (
        PikepdfRedactionSanitizer()
        .sanitize(BytesIO(content), output, tmp_path, _request(content), _limits(), _Control())
        .after.empty
    )


def test_source_changed_during_processing_discards_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _content()
    source = BytesIO(content)
    original = _Output.write
    mutated = False

    def mutate(self: _Output, data: object, /) -> int:
        nonlocal mutated
        if not mutated:
            mutated = True
            source.seek(0)
            source.write(b"X")
        return original(self, data)  # type: ignore[arg-type]

    monkeypatch.setattr(_Output, "write", mutate)
    with pytest.raises(PdfExportError) as caught:
        PikepdfRedactionSanitizer().sanitize(
            source, tmp_path / "candidate.pdf", tmp_path, _request(content), _limits(), _Control()
        )
    assert caught.value.code is PdfExportErrorCode.SOURCE_CHANGED
    assert not list(tmp_path.iterdir())


def test_failed_final_source_restore_discards_output(tmp_path: Path) -> None:
    content = _content()

    class RestoreFailure(BytesIO):
        fail_restore = False

        def seek(self, offset: int, whence: int = 0) -> int:
            if self.fail_restore and offset == 7 and whence == 0:
                raise OSError("SYNTHETIC RESTORE ERROR")
            return super().seek(offset, whence)

    source = RestoreFailure(content)
    source.seek(7)

    class Control(_Control):
        def cancellation_requested(self) -> bool:
            if (tmp_path / "candidate.pdf").exists():
                source.fail_restore = True
            return False

    with pytest.raises(PdfExportError):
        PikepdfRedactionSanitizer().sanitize(
            cast_source(source),
            tmp_path / "candidate.pdf",
            tmp_path,
            _request(content),
            _limits(),
            Control(),
        )
    assert not list(tmp_path.iterdir())


def cast_source(source: BytesIO) -> BinaryIO:
    return source


def _image_packet() -> bytes:
    with pikepdf.Pdf.new() as document:
        for color in (b"\xff\x00\x00", b"\x00\xff\x00"):
            page = document.add_blank_page(page_size=(72, 48))
            image = pikepdf.Stream(
                document,
                zlib.compress(color * 64 * 64),
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=64,
                Height=64,
                ColorSpace=pikepdf.Name.DeviceRGB,
                BitsPerComponent=8,
                Filter=pikepdf.Name.FlateDecode,
            )
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
            page.Contents = pikepdf.Stream(document, b"q 72 0 0 48 0 0 cm /Im0 Do Q")
        output = BytesIO()
        document.save(output, deterministic_id=True)
        return output.getvalue()


def test_two_page_target_keeps_only_compressed_images_before_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _image_packet()
    request = replace(_request(content), source_page_count=2)
    original = pikepdf.Pdf.save
    retained_sizes: list[int] = []

    def inspect_target_before_save(
        document: pikepdf.Pdf, destination: Any, *args: Any, **kwargs: Any
    ) -> None:
        assert len(document.pages) == 2
        for page, color in zip(document.pages, (b"\xff\x00\x00", b"\x00\xff\x00"), strict=True):
            image = page.Resources.XObject.Im0
            raw = image.read_raw_bytes()
            assert image.Filter == pikepdf.Name.FlateDecode
            assert len(raw) < 64 * 64 * 3
            assert zlib.decompress(raw) == color * 64 * 64
            retained_sizes.append(len(raw))
        original(document, destination, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", inspect_target_before_save)
    source = BytesIO(content)
    report = PikepdfRedactionSanitizer().sanitize(
        source, tmp_path / "candidate.pdf", tmp_path, request, _limits(), _Control()
    )
    assert len(retained_sizes) == 2
    assert report.after.empty
    assert source.getvalue() == content


@pytest.mark.parametrize("failure", ["encode", "cancel"])
def test_image_encoding_failure_or_cancellation_leaves_no_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    content = _image_packet()
    request = replace(_request(content), source_page_count=2)
    control = _Control()
    original = zlib.compressobj

    class Encoder:
        def __init__(self) -> None:
            self.encoder = original()

        def compress(self, data: Any) -> bytes:
            if failure == "encode":
                raise OSError("SYNTHETIC IMAGE ENCODING FAILURE")
            result = self.encoder.compress(data)
            control.cancel_at = 1
            return result

        def flush(self) -> bytes:
            return self.encoder.flush()

    monkeypatch.setattr(zlib, "compressobj", Encoder)
    source = BytesIO(content)
    source.seek(7)
    with pytest.raises(PdfExportError) as caught:
        PikepdfRedactionSanitizer().sanitize(
            source, tmp_path / "candidate.pdf", tmp_path, request, _limits(), control
        )
    expected = (
        PdfExportErrorCode.CANCELLED if failure == "cancel" else PdfExportErrorCode.ASSEMBLY_FAILED
    )
    assert caught.value.code is expected
    assert source.getvalue() == content
    assert source.tell() == 7
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("budget", ["tokens", "array", "operands", "output", "cancel"])
def test_content_lexer_enforces_budgets_before_collecting_instructions(budget: str) -> None:
    limits, control = _limits(), _Control()
    maximum = limits.maximum_memory_bytes // 4
    if budget == "tokens":
        limits = replace(limits, maximum_pdf_objects=1)
        content = b"q Q " * 40
    elif budget == "array":
        content = b"BT [" + b"0 " * 65_537 + b"] TJ ET"
    elif budget == "operands":
        content, maximum = b"BT (" + b"S" * 32 + b") Tj ET", 16
    elif budget == "output":
        content, maximum = b"q Q q Q q Q q Q q Q", 16
    else:
        content, control = b"q Q " * 40, _Control(cancel_at=5)
    with pytest.raises(PdfExportError) as caught:
        _canonical_content(content, _Graph(limits, control), maximum)
    assert caught.value.code is (
        PdfExportErrorCode.CANCELLED if budget == "cancel" else PdfExportErrorCode.LIMIT_EXCEEDED
    )
