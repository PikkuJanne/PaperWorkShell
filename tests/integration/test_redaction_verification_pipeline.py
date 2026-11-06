"""Independent native OCR and alternate rendering of exact verifier candidates.

PYTEST_DONT_REWRITE

QtPdf is the locked evaluation renderer, not a production renderer change or a
human viewer result. Missing OCR admission remains an explicit capability skip.
"""

from __future__ import annotations

import os
import subprocess
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pikepdf
import pytest
from PIL import Image, ImageChops, ImageStat

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED, RedactionMark
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.redaction_verification import (
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionAdapter
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
from paperworkshell.pdf.redaction_verification import verify_redaction
from paperworkshell.worker.client.tesseract_runtime import admit_packaged_tesseract_runtime
from tests.integration.test_ocr_worker_windows import _runtime_from_environment
from tests.security.test_redaction_reconstruction import _Control, _render
from tests.security.test_redaction_verification_corpus import (
    _CASES,
    _digest,
    _limits,
    _prepare,
    _verification_request,
)


def _large_source() -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(384, 288))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=document.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Font,
                        Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name.Helvetica,
                    )
                )
            )
        )
        page.Contents = document.make_stream(
            b"BT /F1 23 Tf 28 205 Td (SECRET CANARY ALPHA) Tj ET\nBT /F1 23 Tf 28 95 Td (PUBLIC ENGLISH CONTROL) Tj ET\n"
        )
        content = BytesIO()
        document.save(content, deterministic_id=True)
        return content.getvalue()


def test_admitted_independent_ocr_recovers_source_and_rejects_unsafe_result(tmp_path: Path) -> None:
    executable, tessdata = _runtime_from_environment()
    admission = admit_packaged_tesseract_runtime(executable, tessdata)
    source = _large_source()
    with pikepdf.Pdf.open(BytesIO(source), attempt_recovery=False) as original:
        streams = (_digest(original.pages[0].Contents.read_bytes()),)
    tokens = ("SECRET CANARY ALPHA",)
    page_id = uuid4()
    request = RedactionReconstructionRequest(
        uuid4(),
        0,
        uuid4(),
        page_id,
        _digest(source),
        len(source),
        0,
        1,
        (RedactionMark(uuid4(), page_id, NormalizedPageBox(40_000, 190_000, 920_000, 160_000)),),
    )
    reconstructed_path, candidate_path = tmp_path / "reconstructed.pdf", tmp_path / "candidate.pdf"
    RedactionReconstructionAdapter().reconstruct(
        BytesIO(source), reconstructed_path, tmp_path, request, _limits(), _Control()
    )
    reference = reconstructed_path.read_bytes()
    PikepdfRedactionSanitizer().sanitize(
        BytesIO(reference),
        candidate_path,
        tmp_path,
        RedactionSanitizationRequest(uuid4(), uuid4(), _digest(reference), len(reference), 1),
        _limits(),
        _Control(),
    )
    candidate = candidate_path.read_bytes()
    reconstructed_path.unlink()
    candidate_path.unlink()
    calls = 0

    def recognize(content: bytes) -> str:
        image_path = tmp_path / "recognition.png"
        image = _render(content, 0)
        try:
            image.save(image_path, format="PNG")
            completed = subprocess.run(
                [
                    str(admission.executable),
                    str(image_path),
                    "stdout",
                    "--tessdata-dir",
                    str(admission.tessdata_root),
                    "-l",
                    "eng",
                    "--psm",
                    "6",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=min(60, CONSTRAINED_RESOURCE_POLICY_V1.worker_cpu_time_ms // 1000),
                env={
                    "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                    "PATH": str(admission.executable.parent),
                    "OMP_THREAD_LIMIT": "1",
                },
            )
            assert completed.returncode == 0 and len(completed.stdout) < 4096, (
                "VERIFICATION_OCR_EXECUTION_FAILED"
            )
            return " ".join(completed.stdout.decode("utf-8").split()).upper()
        finally:
            image.close()
            image_path.unlink(missing_ok=True)

    assert "SECRET CANARY ALPHA" in recognize(source), "VERIFICATION_OCR_POSITIVE_CONTROL_FAILED"

    def independent_ocr(stream: BinaryIO) -> VerificationMethodResult:
        nonlocal calls
        calls += 1
        position = stream.tell()
        try:
            stream.seek(0)
            recognized = recognize(stream.read())
        finally:
            stream.seek(position)
        clear = (
            "SECRET" not in recognized and "CANARY" not in recognized and "ALPHA" not in recognized
        )
        assert "PUBLIC ENGLISH CONTROL" in recognized, "VERIFICATION_OCR_KEEP_CONTROL_MISSING"
        return VerificationMethodResult(
            VerificationMethod.OCR,
            VerificationStatus.PASS if clear else VerificationStatus.FAIL,
            VerificationCode.CLEAR if clear else VerificationCode.CANARY,
        )

    verified = verify_redaction(
        BytesIO(candidate),
        BytesIO(reference),
        _verification_request(candidate, reference, (0,), 1, tokens, streams),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=streams,
        ocr_runner=independent_ocr,
    )
    assert verified.automated_passed, "VERIFICATION_NATIVE_PIPELINE_FAILED"
    assert (
        next(item for item in verified.methods if item.method is VerificationMethod.OCR).status
        is VerificationStatus.PASS
    ), "VERIFICATION_NATIVE_OCR_FAILED"
    assert calls == 2, "VERIFICATION_RESAVED_OCR_NOT_CHECKED"
    assert not verified.release_ready, "VERIFICATION_UNRUN_VIEWER_ACCEPTED"
    unsafe = verify_redaction(
        BytesIO(source),
        BytesIO(source),
        _verification_request(source, source, (0,), 1, (), streams),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_stream_sha256=streams,
        ocr_runner=independent_ocr,
    )
    assert not unsafe.automated_passed and not unsafe.release_ready, (
        "VERIFICATION_UNSAFE_NATIVE_PIPELINE_ACCEPTED"
    )
    assert (
        next(item for item in unsafe.methods if item.method is VerificationMethod.OCR).code
        is VerificationCode.CANARY
    ), "VERIFICATION_OCR_CANARY_NOT_REPORTED"
    assert PERMANENT_REDACTION_ENABLED is True
    assert not list(tmp_path.iterdir()), "VERIFICATION_NATIVE_TEMP_RETAINED"


@pytest.mark.parametrize(
    "fixture_id", ["pdf-redaction-geometry", "pdf-redaction-visual-cover", "png-redaction-scan"]
)
def test_qtpdf_and_pdfium_agree_before_and_after_full_resave(
    tmp_path: Path, fixture_id: str
) -> None:
    qt = pytest.importorskip(
        "PySide6.QtPdf", reason="requires the locked renderer-evaluation group"
    )
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QGuiApplication, QImage

    application = QGuiApplication.instance() or QGuiApplication(["pws0606-synthetic-render"])
    assert application is not None
    case = next(case for case in _CASES if case.fixture_id == fixture_id)
    prepared = _prepare(case, tmp_path)
    baseline_path, resaved_path = tmp_path / "baseline.pdf", tmp_path / "resaved.pdf"
    baseline_path.write_bytes(prepared.candidate)
    with pikepdf.Pdf.open(BytesIO(prepared.candidate), attempt_recovery=False) as document:
        document.save(resaved_path, deterministic_id=True)
    for path in (baseline_path, resaved_path):
        reader = qt.QPdfDocument(None)
        try:
            assert reader.load(str(path)) == qt.QPdfDocument.Error.None_, (
                "VERIFICATION_QTPDF_OPEN_FAILED"
            )
            assert reader.pageCount() == (4 if fixture_id == "pdf-redaction-geometry" else 1), (
                "VERIFICATION_QTPDF_PAGE_COUNT"
            )
            for page_index in range(reader.pageCount()):
                pdfium_image = _render(path.read_bytes(), page_index, scale=1)
                qt_image = reader.render(page_index, QSize(*pdfium_image.size)).convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                assert not qt_image.isNull(), "VERIFICATION_QTPDF_RENDER_FAILED"
                rgba = bytes(qt_image.constBits())
                alternate = Image.frombytes(
                    "RGBA", pdfium_image.size, rgba, "raw", "RGBA", qt_image.bytesPerLine()
                ).convert("RGB")
                try:
                    assert (
                        max(ImageStat.Stat(ImageChops.difference(pdfium_image, alternate)).mean)
                        <= 1.0
                    ), "VERIFICATION_QTPDF_APPEARANCE_CHANGED"
                    assert ImageChops.invert(alternate).getbbox() is not None, (
                        "VERIFICATION_QTPDF_EMPTY_PAGE"
                    )
                finally:
                    pdfium_image.close()
                    alternate.close()
        finally:
            reader.close()
            del reader
    baseline_path.unlink()
    resaved_path.unlink()
    assert not list(tmp_path.iterdir()), "VERIFICATION_QTPDF_TEMP_RETAINED"
