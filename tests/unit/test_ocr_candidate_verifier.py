"""Focused tests for the strict OCR-candidate PDF reopen seam."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import paperworkshell.pdf.ocr_candidate_verifier as verifier
from paperworkshell.pdf.ocr_candidate_verifier import (
    OcrPdfCandidateVerificationError,
    close_private_ocr_pdf_documents,
    verified_ocr_pdf_page_count,
)


class _Document:
    def __init__(self, *, encrypted: bool, page_count: int) -> None:
        self.is_encrypted = encrypted
        self.pages = [object()] * page_count

    def __enter__(self) -> _Document:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _ClosableDocument:
    def __init__(self, filename: str, *, fails: bool = False) -> None:
        self.filename = filename
        self.fails = fails
        self.closed = False

    def close(self) -> None:
        if self.fails:
            raise RuntimeError
        self.closed = True


def test_empty_private_work_inventory_requires_no_gc_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_scan() -> list[object]:
        raise AssertionError("an empty work inventory needs no scan")

    monkeypatch.setattr(
        "paperworkshell.pdf.ocr_candidate_verifier.gc.get_objects",
        fail_scan,
    )

    assert close_private_ocr_pdf_documents(())


def test_private_work_document_closure_is_scoped_and_content_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = (tmp_path / "work").resolve()
    outside = (tmp_path / "outside.pdf").resolve()
    inside_document = _ClosableDocument(str(work / "origin.pdf"))
    outside_document = _ClosableDocument(str(outside))
    monkeypatch.setattr(verifier, "pikepdf", SimpleNamespace(Pdf=_ClosableDocument))
    monkeypatch.setattr(
        "paperworkshell.pdf.ocr_candidate_verifier.gc.get_objects",
        lambda: (inside_document, outside_document),
    )

    assert close_private_ocr_pdf_documents((work,))
    assert inside_document.closed
    assert not outside_document.closed


def test_private_work_document_close_failure_returns_only_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = (tmp_path / "work").resolve()
    document = _ClosableDocument(str(work / "origin.pdf"), fails=True)
    monkeypatch.setattr(verifier, "pikepdf", SimpleNamespace(Pdf=_ClosableDocument))
    monkeypatch.setattr(
        "paperworkshell.pdf.ocr_candidate_verifier.gc.get_objects",
        lambda: (document,),
    )

    assert not close_private_ocr_pdf_documents((work,))


def test_candidate_reopen_disables_recovery_and_uses_no_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.pdf"
    observed: dict[str, object] = {}

    def open_pdf(path: Path, **options: object) -> _Document:
        observed["path"] = path
        observed.update(options)
        return _Document(encrypted=False, page_count=2)

    monkeypatch.setattr(
        verifier,
        "pikepdf",
        SimpleNamespace(Pdf=SimpleNamespace(open=open_pdf)),
    )

    assert verified_ocr_pdf_page_count(candidate) == 2
    assert observed == {
        "path": candidate,
        "password": "",
        "attempt_recovery": False,
        "suppress_warnings": True,
    }


def test_encrypted_candidate_fails_closed_with_content_free_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "private-candidate-name"
    candidate = tmp_path / f"{canary}.pdf"
    monkeypatch.setattr(
        verifier,
        "pikepdf",
        SimpleNamespace(
            Pdf=SimpleNamespace(
                open=lambda *_args, **_kwargs: _Document(encrypted=True, page_count=1)
            )
        ),
    )

    with pytest.raises(OcrPdfCandidateVerificationError) as raised:
        verified_ocr_pdf_page_count(candidate)

    assert str(raised.value) == "OCR_PDF_CANDIDATE_INVALID"
    assert canary not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
