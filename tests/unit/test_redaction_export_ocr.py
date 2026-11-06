"""Runtime OCR checks exact masked candidates and resaves without publishing text."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from paperworkshell.domain.ocr import OcrAdapterResult
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VerificationMethod,
    VerificationStatus,
)
from paperworkshell.pdf.redaction_export import RedactionExportAdapter
from paperworkshell.pdf.redaction_export_ocr import (
    RedactionCandidateOcrVerifier,
    _require_private_files,
)
from paperworkshell.pdf.redaction_ocr import _exclusion_boxes
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionAdapter
from tests.unit.test_redaction_ocr_adapter import (
    _adapter,
    _Boundary,
    _Control,
    _limits,
    _request,
    _source,
    _text,
)


def _prepare(tmp_path: Path, failure: str = "") -> tuple[Any, _Boundary, Path]:
    content = _source()
    boundary = _Boundary(failure)
    output = tmp_path / "candidate.pdf"
    result = RedactionExportAdapter(ocr=_adapter(boundary, tmp_path).ocr).prepare(
        BytesIO(content),
        output,
        tmp_path,
        _request(content).reconstruction,
        _limits(),
        _Control(),
        operation_id=uuid4(),
        output_object_id=uuid4(),
    )
    return result, boundary, output


def test_runtime_pipeline_runs_ocr_on_candidate_and_full_resave_and_keeps_image_only(
    tmp_path: Path,
) -> None:
    report, boundary, output = _prepare(tmp_path)
    assert report.verification_request.profile_id == RUNTIME_VERIFICATION_PROFILE
    assert report.verification.runtime_ready
    assert not report.verification.release_ready
    assert boundary.calls == 2
    assert report.verification.methods[-2].status is VerificationStatus.PASS
    assert report.verification.methods[-1].method is VerificationMethod.VIEWERS
    assert report.verification.methods[-1].status is VerificationStatus.UNKNOWN
    assert _text(output.read_bytes()) == ""
    assert report.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    "failure",
    [
        "cancel",
        "timeout",
        "language",
        "engine",
        "limit",
        "image",
        "geometry",
        "overlap",
        "visible",
        "masked_changed",
    ],
)
def test_unknown_or_invalid_runtime_ocr_never_approves_candidate_and_cleans_private_files(
    tmp_path: Path, failure: str
) -> None:
    report, boundary, output = _prepare(tmp_path, failure)
    assert boundary.calls >= 1
    assert not report.verification.runtime_ready
    assert not report.verification.release_ready
    assert report.verification.methods[-2].status is VerificationStatus.FAIL
    assert list(tmp_path.iterdir()) == [output]
    assert _text(output.read_bytes()) == ""


def test_runtime_ocr_owns_cursor_and_detects_input_mutation(tmp_path: Path) -> None:
    content = _source()
    intent = _request(content)
    path = tmp_path / "masked.pdf"
    reconstructor = RedactionReconstructionAdapter()
    control, limits = _Control(), _limits()
    reconstruction = reconstructor.reconstruct(
        BytesIO(content), path, tmp_path, intent.reconstruction, limits, control
    )
    candidate = BytesIO(path.read_bytes())
    path.unlink()
    candidate.seek(7)

    class MutatingProcessor:
        def process(self, source_path: Path, output_path: Path, **kwargs: Any) -> OcrAdapterResult:
            payload = source_path.read_bytes()
            output_path.write_bytes(payload)
            candidate.seek(0)
            candidate.write(b"MUTATED")
            return OcrAdapterResult(
                len(payload), 1, hashlib.sha256(payload).hexdigest(), "17.10.0", "5.5.3"
            )

    runner = RedactionCandidateOcrVerifier(
        MutatingProcessor(),
        reconstructor,
        reconstruction,
        _exclusion_boxes(BytesIO(content), intent, reconstruction, limits),
        tmp_path,
        limits,
        control,
        limits.maximum_temporary_storage_bytes,
    )
    with pytest.raises(PdfExportError) as failed:
        runner(candidate)
    assert failed.value.code is PdfExportErrorCode.SOURCE_CHANGED
    assert candidate.tell() == 7
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("reserve_rewrite", [False, True])
def test_runtime_ocr_reserves_outer_candidates_before_expensive_processing(
    tmp_path: Path, reserve_rewrite: bool
) -> None:
    content = _source()
    intent, limits, control = _request(content), _limits(), _Control()
    path = tmp_path / "masked.pdf"
    reconstructor = RedactionReconstructionAdapter()
    reconstruction = reconstructor.reconstruct(
        BytesIO(content), path, tmp_path, intent.reconstruction, limits, control
    )
    payload = path.read_bytes()
    path.unlink()
    boundary = _Boundary()
    runner = RedactionCandidateOcrVerifier(
        _adapter(boundary, tmp_path).ocr,
        reconstructor,
        reconstruction,
        (),
        tmp_path,
        limits,
        control,
        len(payload) + limits.maximum_output_bytes + 1 if reserve_rewrite else len(payload),
    )
    with pytest.raises(PdfExportError) as failed:
        runner(BytesIO(payload))
    assert failed.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert boundary.calls == 0 and not list(tmp_path.iterdir())


def test_successful_engine_cannot_hide_unaccounted_private_files(tmp_path: Path) -> None:
    class LeftoverBoundary(_Boundary):
        def process(self, source_path: Path, output_path: Path, **kwargs: Any) -> tuple[str, str]:
            result = super().process(source_path, output_path, **kwargs)
            (output_path.parent / "leftover.txt").write_bytes(b"PRIVATE-OCR-CONTROL")
            return result

    content, boundary = _source(), LeftoverBoundary()
    output = tmp_path / "candidate.pdf"
    result = RedactionExportAdapter(ocr=_adapter(boundary, tmp_path).ocr).prepare(
        BytesIO(content),
        output,
        tmp_path,
        _request(content).reconstruction,
        _limits(),
        _Control(),
        operation_id=uuid4(),
        output_object_id=uuid4(),
    )
    assert not result.verification.runtime_ready
    assert result.verification.methods[-2].status is VerificationStatus.FAIL
    assert list(tmp_path.iterdir()) == [output]


def test_private_ocr_work_rejects_actual_hardlink_aliases(tmp_path: Path) -> None:
    source, alias = tmp_path / "input.pdf", tmp_path / "output.pdf"
    source.write_bytes(b"synthetic private file")
    os.link(source, alias)
    with pytest.raises(PdfExportError) as failed:
        _require_private_files(tmp_path, (source, alias), 1024, _Control())
    assert failed.value.code is PdfExportErrorCode.RESULT_INVALID
    assert source.read_bytes() == b"synthetic private file"


def test_private_identity_uses_fresh_stat_instead_of_cached_directory_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    source = tmp_path / "input.pdf"
    source.write_bytes(b"synthetic private file")

    def cached_stat(**kwargs: object) -> object:
        # Windows directory enumeration does not supply reliable link/file IDs.
        raise AssertionError("cached directory identity is not authoritative")

    entry = SimpleNamespace(name=source.name, path=str(source), stat=cached_stat)

    class Entries:
        def __enter__(self) -> tuple[object, ...]:
            return (entry,)

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(
        "paperworkshell.pdf.redaction_export_ocr.os.scandir", lambda path: Entries()
    )
    _require_private_files(tmp_path, (source,), 1024, _Control())
