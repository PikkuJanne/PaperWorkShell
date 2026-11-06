"""Exact-byte binding, resave, resource failure and private scratch cleanup."""

from __future__ import annotations

import errno
import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from paperworkshell.domain.redaction_verification import (
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.unit.test_redaction_sanitization_adapter import _content, _Control, _limits, _request


def _candidate(tmp_path: Path) -> bytes:
    content = _content()
    path = tmp_path / "candidate.pdf"
    PikepdfRedactionSanitizer().sanitize(
        BytesIO(content), path, tmp_path, _request(content), _limits(), _Control()
    )
    return path.read_bytes()


def _verification_request(candidate: bytes, reference: bytes) -> RedactionVerificationRequest:
    return RedactionVerificationRequest(
        hashlib.sha256(candidate).hexdigest(),
        len(candidate),
        hashlib.sha256(reference).hexdigest(),
        len(reference),
        1,
        (),
        "f" * 64,
    )


def test_private_suite_resaves_restores_streams_and_keeps_release_unknown(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    source = BytesIO(candidate)
    reference = BytesIO(_content())
    source.seek(9)
    reference.seek(11)
    report = verify_redaction(
        source,
        reference,
        _verification_request(candidate, reference.getvalue()),
        tmp_path,
        _limits(),
        _Control(),
    )
    assert report.automated_passed, report.to_record()
    assert not report.release_ready
    assert source.tell() == 9 and reference.tell() == 11
    assert source.getvalue() == candidate
    assert [path.name for path in tmp_path.iterdir()] == ["candidate.pdf"]


@pytest.mark.parametrize(
    "failure",
    [
        "hash",
        "size",
        "reference",
        "pages",
        "cancel",
        "timeout",
        "disk",
        "memory",
        "output",
        "objects",
        "scratch",
    ],
)
def test_failure_is_closed_content_free_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    candidate = _candidate(tmp_path)
    request = _verification_request(candidate, candidate)
    source, reference = BytesIO(candidate), BytesIO(candidate)
    limits, control = _limits(), _Control()
    scratch = tmp_path
    if failure == "hash":
        request = replace(request, candidate_sha256="a" * 64)
    elif failure == "size":
        request = replace(request, candidate_byte_size=len(candidate) + 1)
    elif failure == "reference":
        request = replace(request, reference_sha256="a" * 64)
    elif failure == "pages":
        request = replace(request, page_count=2)
    elif failure == "cancel":
        control = _Control(cancel_at=1)
    elif failure == "timeout":
        control = _Control(expired=True)
    elif failure == "disk":
        limits = replace(limits, maximum_temporary_storage_bytes=16)
    elif failure == "memory":
        limits = replace(limits, maximum_memory_bytes=16)
    elif failure == "output":
        limits = replace(limits, maximum_output_bytes=16)
    elif failure == "objects":
        limits = replace(limits, maximum_pdf_objects=1)
    else:
        scratch = Path("relative")
    source.seek(7)
    reference.seek(8)
    report = verify_redaction(source, reference, request, scratch, limits, control)
    assert not report.automated_passed and not report.release_ready
    assert source.tell() == 7 and reference.tell() == 8
    assert source.getvalue() == candidate and reference.getvalue() == candidate
    assert [path.name for path in tmp_path.iterdir()] == ["candidate.pdf"]
    retry = verify_redaction(
        source,
        reference,
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
    )
    assert retry.automated_passed, retry.to_record()


def test_input_mutation_during_external_method_invalidates_all_results(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    source, reference = BytesIO(candidate), BytesIO(candidate)

    def mutate(_stream: object) -> VerificationMethodResult:
        source.seek(0)
        source.write(b"%changed")
        return VerificationMethodResult(
            VerificationMethod.OCR, VerificationStatus.PASS, VerificationCode.CLEAR
        )

    report = verify_redaction(
        source,
        reference,
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
        ocr_runner=mutate,
    )
    assert not report.release_ready and not report.automated_passed
    assert all(item.code is VerificationCode.BINDING for item in report.methods)


def test_ocr_is_repeated_after_resave_and_failure_blocks_result(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    observed = []

    def check(stream: object) -> VerificationMethodResult:
        observed.append(stream)
        # The resaved candidate is checked before the original.
        return VerificationMethodResult(
            VerificationMethod.OCR, VerificationStatus.FAIL, VerificationCode.CANARY
        )

    report = verify_redaction(
        BytesIO(candidate),
        BytesIO(candidate),
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
        ocr_runner=check,
    )
    assert len(observed) == 2 and observed[0] is not observed[1]
    assert not report.automated_passed
    assert (
        next(item for item in report.methods if item.method is VerificationMethod.RESAVE).status
        is VerificationStatus.FAIL
    )


def test_disk_full_during_resave_preserves_existing_output_and_cleans_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paperworkshell.pdf.raster_pdf_page import _BoundedOutput

    candidate = _candidate(tmp_path)
    previous = tmp_path / "previous.pdf"
    previous.write_bytes(b"SYNTHETIC PREVIOUS GOOD OUTPUT")

    def full(*_args: object) -> int:
        raise OSError(errno.ENOSPC, "PRIVATE_SYNTHETIC_VALUE")

    with monkeypatch.context() as patch:
        patch.setattr(_BoundedOutput, "write", full)
        report = verify_redaction(
            BytesIO(candidate),
            BytesIO(candidate),
            _verification_request(candidate, candidate),
            tmp_path,
            _limits(),
            _Control(),
        )
    assert not report.automated_passed
    assert "PRIVATE_SYNTHETIC_VALUE" not in repr(report.to_record())
    assert previous.read_bytes() == b"SYNTHETIC PREVIOUS GOOD OUTPUT"
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf", "previous.pdf"}


@pytest.mark.parametrize("restoration_fault", ["raises", "wrong_position"])
def test_restore_failure_still_removes_resaved_private_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restoration_fault: str
) -> None:
    from typing import BinaryIO

    import paperworkshell.pdf.redaction_verification as verification_module
    from paperworkshell.pdf.redaction_reconstruction import (
        RedactionReconstructionControl,
    )
    from paperworkshell.pdf.redaction_reconstruction import (
        _measure_source as measure,
    )

    class FailingRestore(BytesIO):
        restoration_armed = False

        def seek(self, offset: int, whence: int = 0) -> int:
            if self.restoration_armed:
                if restoration_fault == "raises":
                    raise OSError("SYNTHETIC PRIVATE RESTORE DETAIL")
                return super().seek(offset + 1, whence)
            return super().seek(offset, whence)

    candidate = _candidate(tmp_path)
    source, reference = FailingRestore(candidate), BytesIO(candidate)
    source.seek(13)
    reference.seek(19)
    measurements = 0

    def arm_final_restore(
        stream: BinaryIO, maximum: int, control: RedactionReconstructionControl
    ) -> tuple[int, str]:
        nonlocal measurements
        measured = measure(stream, maximum, control)
        if stream is source:
            measurements += 1
            # Both input binding checks complete before the final restoration.
            if measurements == 2:
                source.restoration_armed = True
        return measured

    monkeypatch.setattr(verification_module, "_measure_source", arm_final_restore)
    report = verify_redaction(
        source,
        reference,
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
    )
    assert measurements == 2
    assert not report.automated_passed and not report.release_ready
    assert all(item.code is VerificationCode.FAILED for item in report.methods)
    assert reference.tell() == 19
    assert source.getvalue() == reference.getvalue() == candidate
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf"}
    assert "PRIVATE RESTORE DETAIL" not in repr(report.to_record())


def test_external_runner_cannot_mutate_resaved_artifact_after_checks(tmp_path: Path) -> None:
    from typing import BinaryIO

    candidate = _candidate(tmp_path)
    source, reference = BytesIO(candidate), BytesIO(candidate)
    mutated = False

    def mutate(stream: BinaryIO) -> VerificationMethodResult:
        nonlocal mutated
        if stream is not source:
            # Simulate a viewer modifying its saved copy after its extraction
            # checks. The verifier must bind the exact inspected resaved bytes.
            Path(stream.name).write_bytes(b"%SYNTHETIC CHANGED SAVED CANDIDATE")
            mutated = True
        return VerificationMethodResult(
            VerificationMethod.OCR, VerificationStatus.PASS, VerificationCode.CLEAR
        )

    report = verify_redaction(
        source,
        reference,
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
        ocr_runner=mutate,
    )
    assert mutated
    assert not report.automated_passed and not report.release_ready
    assert all(item.code is VerificationCode.BINDING for item in report.methods)
    assert source.getvalue() == reference.getvalue() == candidate
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf"}


def test_every_external_runner_starts_at_zero_and_original_positions_return(tmp_path: Path) -> None:
    from collections.abc import Callable
    from typing import BinaryIO

    candidate = _candidate(tmp_path)
    source, reference = BytesIO(candidate), BytesIO(candidate)
    source.seek(13)
    reference.seek(19)
    observed: list[tuple[VerificationMethod, int]] = []

    def runner(method: VerificationMethod) -> Callable[[BinaryIO], VerificationMethodResult]:
        def consume(stream: BinaryIO) -> VerificationMethodResult:
            observed.append((method, stream.tell()))
            stream.read()
            return VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)

        return consume

    report = verify_redaction(
        source,
        reference,
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
        ocr_runner=runner(VerificationMethod.OCR),
        viewer_runner=runner(VerificationMethod.VIEWERS),
    )
    assert report.automated_passed and report.release_ready
    assert observed == [
        (VerificationMethod.OCR, 0),
        (VerificationMethod.VIEWERS, 0),
        (VerificationMethod.OCR, 0),
        (VerificationMethod.VIEWERS, 0),
    ]
    assert source.tell() == 13 and reference.tell() == 19
    assert source.getvalue() == reference.getvalue() == candidate
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf"}


def test_metadata_only_sanitization_needs_no_affected_page_raster(tmp_path: Path) -> None:
    import pikepdf

    from paperworkshell.domain.redaction_verification import verification_probe_digest

    original = _content()
    candidate = _candidate(tmp_path)
    token = "SYNTHETIC REMOVE"
    request = replace(
        _verification_request(candidate, original),
        probes_sha256=verification_probe_digest((token,), ()),
    )
    assert request.affected_pages == ()
    with pikepdf.Pdf.open(BytesIO(original)) as document:
        assert str(document.docinfo.Subject) == token
    with pikepdf.Pdf.open(BytesIO(candidate)) as document:
        assert "/Info" not in document.trailer
        assert "/XObject" not in document.pages[0].Resources
    source, reference = BytesIO(candidate), BytesIO(original)
    report = verify_redaction(
        source,
        reference,
        request,
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=(token,),
    )
    assert report.automated_passed, report.to_record()
    assert not report.release_ready
    assert source.getvalue() == candidate and reference.getvalue() == original
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf"}


@pytest.mark.parametrize(
    "unexpected_code", ["RV_OBJECTS_CLEAR", "SYNTHETIC_UNKNOWN_PRIVATE_METHOD_VALUE"]
)
def test_visual_method_cannot_pass_with_foreign_or_unknown_success_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unexpected_code: str
) -> None:
    from paperworkshell.pdf.redaction_verification_visual import VisualVerificationResult

    def invalid_visual(*_args: object, **_kwargs: object) -> VisualVerificationResult:
        return VisualVerificationResult(unexpected_code, None, None)

    candidate = _candidate(tmp_path)
    monkeypatch.setattr("paperworkshell.pdf.redaction_verification.verify_visual", invalid_visual)
    report = verify_redaction(
        BytesIO(candidate),
        BytesIO(candidate),
        _verification_request(candidate, candidate),
        tmp_path,
        _limits(),
        _Control(),
    )
    text = next(item for item in report.methods if item.method is VerificationMethod.TEXT)
    assert text.status is VerificationStatus.FAIL and text.code is VerificationCode.INVALID
    assert not report.automated_passed and not report.release_ready
    assert unexpected_code not in repr(report.to_record())
    assert {path.name for path in tmp_path.iterdir()} == {"candidate.pdf"}
