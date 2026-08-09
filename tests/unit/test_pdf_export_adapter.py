"""PWS-0112 tests for private PDF assembly and pre-commit verification."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Buffer
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pikepdf
import pytest

import paperworkshell.pdf.export_adapter as export_module
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits, PdfStructuralState
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRenderError,
    PdfRenderErrorCode,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapter,
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.renderer import (
    PdfPageRenderer,
    PdfRasterDestination,
    PdfRenderControl,
    PdfRenderSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURES = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf"


class _CleanChecker:
    def __init__(self, state: PdfStructuralState = PdfStructuralState.CLEAN) -> None:
        self.state = state
        self.calls = 0

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: object,
        monotonic: object,
    ) -> PdfStructuralState:
        assert source_path.is_absolute()
        assert type(limits) is PdfInspectionLimits
        assert deadline > 0
        assert callable(cancellation_probe)
        assert callable(monotonic)
        self.calls += 1
        return self.state


class _FailingRenderer:
    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        del source, destination, request, limits, control
        raise PdfRenderError(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.test")


class _MismatchingRenderer:
    def __init__(self) -> None:
        self.delegate = PdfiumPageRenderer()
        self.calls = 0

    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        result = self.delegate.render(source, destination, request, limits, control)
        self.calls += 1
        if self.calls == 2:
            return replace(result, pixel_sha256="f" * 64)
        return result


def _limits(
    *,
    maximum_temporary_bytes: int = 20_000_000,
    maximum_total_source_bytes: int = 20_000_000,
) -> PdfExportAdapterLimits:
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=100,
            maximum_objects=100_000,
            maximum_input_bytes=20_000_000,
            maximum_temporary_bytes=maximum_temporary_bytes,
            maximum_output_bytes=20_000_000,
            maximum_tool_output_bytes=1_000_000,
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=100,
            maximum_width_pixels=256,
            maximum_height_pixels=256,
            maximum_pixels=256 * 256,
            maximum_bitmap_bytes=256 * 256 * 4,
            maximum_destination_bytes=256 * 256 * 4,
            maximum_memory_bytes=256 * 256 * 4,
            wall_time_ms=60_000,
        ),
        maximum_total_source_bytes=maximum_total_source_bytes,
        verification_width_pixels=256,
        verification_height_pixels=256,
    )


def _control(
    *,
    cancellation_probe: object | None = None,
    deadline: float | None = None,
) -> PdfExportAdapterControl:
    selected_probe = cancellation_probe if cancellation_probe is not None else lambda: False
    assert callable(selected_probe)
    return PdfExportAdapterControl(
        deadline=time.monotonic() + 30.0 if deadline is None else deadline,
        cancellation_probe=selected_probe,
    )


def _page_count(path: Path) -> int:
    with path.open("rb") as stream:
        with pikepdf.Pdf.open(
            stream,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            return len(document.pages)


def _source(path: Path, source_id: UUID, *, page_count: int | None = None) -> PdfExportSource:
    payload = path.read_bytes()
    return PdfExportSource(
        source_id=source_id,
        byte_size=len(payload),
        page_count=_page_count(path) if page_count is None else page_count,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _plan(
    entries: tuple[tuple[UUID, Path, int, PdfExportRotation], ...],
    *,
    page_counts: dict[UUID, int] | None = None,
) -> tuple[PdfExportPlan, dict[UUID, Path]]:
    first_use_ids = tuple(dict.fromkeys(entry[0] for entry in entries))
    source_path_by_id = {source_id: path.resolve() for source_id, path, _, _ in entries}
    counts = {} if page_counts is None else page_counts
    sources = tuple(
        _source(
            source_path_by_id[source_id],
            source_id,
            page_count=counts.get(source_id),
        )
        for source_id in first_use_ids
    )
    pages = tuple(
        PdfExportPage(
            output_page_index=output_index,
            page_id=uuid4(),
            source_id=source_id,
            source_page_index=source_page_index,
            additional_rotation=rotation,
        )
        for output_index, (source_id, _path, source_page_index, rotation) in enumerate(entries)
    )
    return (
        PdfExportPlan(
            case_id=uuid4(),
            manifest_revision=3,
            manifest_sha256="0" * 64,
            manifest_cas_token="export-cas-token-1",
            sources=sources,
            pages=pages,
        ),
        source_path_by_id,
    )


def _single_plan(
    path: Path, *, page_count: int | None = None
) -> tuple[PdfExportPlan, dict[UUID, Path]]:
    source_id = uuid4()
    return _plan(
        ((source_id, path, 0, PdfExportRotation.DEGREES_0),),
        page_counts=None if page_count is None else {source_id: page_count},
    )


def _run(
    plan: PdfExportPlan,
    source_paths: dict[UUID, Path],
    output_path: Path,
    *,
    checker: _CleanChecker | None = None,
    renderer: PdfPageRenderer | None = None,
    limits: PdfExportAdapterLimits | None = None,
    control: PdfExportAdapterControl | None = None,
) -> PdfExportVerificationReport:
    adapter = PdfExportAdapter(
        qpdf_checker=_CleanChecker() if checker is None else checker,
        renderer=PdfiumPageRenderer() if renderer is None else renderer,
    )
    return adapter.assemble_and_verify(
        plan,
        source_paths,
        output_path.resolve(),
        _limits() if limits is None else limits,
        _control() if control is None else control,
    )


def _make_inherited_source(destination: Path) -> None:
    source = PDF_FIXTURES / "born-digital.pdf"
    with source.open("rb") as stream:
        with pikepdf.Pdf.open(
            stream,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            page = document.pages[0].obj
            root_pages = document.Root.Pages
            for key in (pikepdf.Name.MediaBox, pikepdf.Name.CropBox, pikepdf.Name.Resources):
                value = page.get(key)
                if value is not None:
                    root_pages[key] = value
                    del page[key]
            root_pages.Rotate = 90
            if pikepdf.Name.Rotate in page:
                del page[pikepdf.Name.Rotate]
            document.save(destination, deterministic_id=True)
    with destination.open("rb") as stream:
        with pikepdf.Pdf.open(
            stream,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            assert document.pages[0].obj.get(pikepdf.Name.Rotate) is None
            assert document.Root.Pages.Rotate == 90


def _candidate_rotations(path: Path) -> tuple[int, ...]:
    with path.open("rb") as stream:
        with pikepdf.Pdf.open(
            stream,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            return tuple(int(page.obj.Rotate) % 360 for page in document.pages)


def test_multi_source_reorder_and_absolute_rotation_materialize_inheritance(
    tmp_path: Path,
) -> None:
    inherited = tmp_path / "inherited.pdf"
    _make_inherited_source(inherited)
    matrix = PDF_FIXTURES / "render-geometry-matrix.pdf"
    inherited_id = uuid4()
    matrix_id = uuid4()
    plan, paths = _plan(
        (
            (matrix_id, matrix, 3, PdfExportRotation.DEGREES_90),
            (inherited_id, inherited, 0, PdfExportRotation.DEGREES_270),
            (matrix_id, matrix, 0, PdfExportRotation.DEGREES_180),
        )
    )
    before = {
        source_id: hashlib.sha256(path.read_bytes()).hexdigest()
        for source_id, path in paths.items()
    }
    output = tmp_path / "candidate.pdf"

    report = _run(plan, paths, output)

    assert report.semantic_fingerprint == plan.semantic_fingerprint
    assert report.page_count == 3
    assert _candidate_rotations(output) == (0, 0, 180)
    assert {
        source_id: hashlib.sha256(path.read_bytes()).hexdigest()
        for source_id, path in paths.items()
    } == before


def test_repeated_assembly_is_byte_and_semantically_equivalent(tmp_path: Path) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    first_path = tmp_path / "first.pdf"
    second_path = tmp_path / "second.pdf"

    first = _run(plan, paths, first_path)
    second = _run(plan, paths, second_path)

    assert first.semantic_fingerprint == second.semantic_fingerprint == plan.semantic_fingerprint
    assert first.output_sha256 == second.output_sha256
    assert first_path.read_bytes() == second_path.read_bytes()


def test_corrupt_candidate_fails_strict_reopen_and_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    original = export_module._verify_candidate_structure

    def corrupt(path: Path, **kwargs: object) -> None:
        path.write_bytes(b"%PDF-1.7\ncorrupt")
        original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_verify_candidate_structure", corrupt)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_REOPEN_FAILED
    assert not output.exists()


@pytest.mark.parametrize("state", [PdfStructuralState.WARNINGS, PdfStructuralState.FAILED])
def test_qpdf_nonclean_outcome_blocks_candidate(
    state: PdfStructuralState,
    tmp_path: Path,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    checker = _CleanChecker(state)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output, checker=checker)

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert checker.calls == 1
    assert not output.exists()


def _replace_candidate(path: Path, edit: object) -> None:
    replacement = path.with_name("tampered.pdf")
    with path.open("rb") as stream:
        with pikepdf.Pdf.open(
            stream,
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            assert callable(edit)
            edit(document)
            document.save(replacement, deterministic_id=True)
    os.replace(replacement, path)


def test_page_count_mismatch_blocks_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    original = export_module._verify_candidate_structure

    def alter(path: Path, **kwargs: object) -> None:
        _replace_candidate(path, lambda document: document.pages.append(document.pages[0]))
        original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_verify_candidate_structure", alter)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH
    assert not output.exists()


def test_rotation_mismatch_blocks_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    original = export_module._verify_candidate_structure

    def rotate(document: pikepdf.Pdf) -> None:
        document.pages[0].obj.Rotate = 90

    def alter(path: Path, **kwargs: object) -> None:
        _replace_candidate(path, rotate)
        original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_verify_candidate_structure", alter)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert not output.exists()


def test_encrypted_candidate_blocks_export_and_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    original = export_module._verify_candidate_structure

    def encrypt(path: Path, **kwargs: object) -> None:
        replacement = path.with_name("encrypted-candidate.pdf")
        with path.open("rb") as stream:
            with pikepdf.Pdf.open(
                stream,
                password="",
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=False,
            ) as document:
                document.save(
                    replacement,
                    encryption=pikepdf.Encryption(owner="owner", user="user", R=6),
                )
        os.replace(replacement, path)
        original(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_verify_candidate_structure", encrypt)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED
    assert not output.exists()


def test_render_failure_blocks_candidate(tmp_path: Path) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output, renderer=_FailingRenderer())

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_RENDER_FAILED
    assert not output.exists()


def test_render_digest_mismatch_blocks_candidate(tmp_path: Path) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output, renderer=_MismatchingRenderer())

    assert caught.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert not output.exists()


def test_encrypted_source_is_rejected_without_candidate(tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.pdf"
    with pikepdf.Pdf.new() as document:
        document.add_blank_page(page_size=(72, 72))
        document.save(
            encrypted,
            encryption=pikepdf.Encryption(owner="owner", user="user", R=6),
        )
    plan, paths = _single_plan(encrypted, page_count=1)
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.SOURCE_ENCRYPTED
    assert not output.exists()


def test_malformed_source_is_rejected_without_candidate(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"%PDF-1.7\nnot-a-document")
    plan, paths = _single_plan(malformed, page_count=1)
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
    assert not output.exists()


def test_existing_output_and_nonexact_source_key_set_are_rejected(tmp_path: Path) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    output.write_bytes(b"preserve-me")

    with pytest.raises(PdfExportError) as existing:
        _run(plan, paths, output)
    assert existing.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert output.read_bytes() == b"preserve-me"

    output.unlink()
    paths[uuid4()] = PDF_FIXTURES / "blank.pdf"
    with pytest.raises(PdfExportError) as keys:
        _run(plan, paths, output)
    assert keys.value.code is PdfExportErrorCode.SOURCE_INCONSISTENT
    assert not output.exists()


def test_bounded_candidate_stream_enforces_exact_write_seek_and_truncate_extent(
    tmp_path: Path,
) -> None:
    exact_path = tmp_path / "exact.bin"
    with exact_path.open("x+b") as stream:
        bounded = export_module._BoundedCandidateStream(stream, 4)
        assert bounded.write(b"1234") == 4
        assert bounded.tell() == 4
        assert bounded.seek(4) == 4
        assert bounded.truncate(4) == 4
        assert not bounded.limit_exceeded
    assert exact_path.read_bytes() == b"1234"

    for operation in ("write", "seek", "truncate"):
        path = tmp_path / f"{operation}.bin"
        with path.open("x+b") as stream:
            bounded = export_module._BoundedCandidateStream(stream, 4)
            with pytest.raises(export_module._CandidateLimitExceeded) as caught:
                if operation == "write":
                    assert bounded.write(b"1234") == 4
                    bounded.write(b"5")
                elif operation == "seek":
                    bounded.seek(5)
                else:
                    bounded.truncate(5)
            assert caught.value.args == ()
            assert str(caught.value) == ""
            assert bounded.limit_exceeded
        assert path.stat().st_size <= 4


def test_tiny_candidate_cap_aborts_during_native_save_even_when_error_is_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    native_secret = f"native-writer-secret:{output}"
    original_write = export_module._BoundedCandidateStream.write

    def wrapped_write(
        stream: export_module._BoundedCandidateStream,
        data: Buffer,
    ) -> int:
        try:
            return original_write(stream, data)
        except export_module._CandidateLimitExceeded:
            raise pikepdf.PdfError(native_secret) from None

    def post_save_measurement_was_not_reached(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("post-save measurement must not enforce the primary bound")

    monkeypatch.setattr(export_module._BoundedCandidateStream, "write", wrapped_write)
    monkeypatch.setattr(export_module, "_measure_output", post_save_measurement_was_not_reached)

    with pytest.raises(PdfExportError) as caught:
        _run(
            plan,
            paths,
            output,
            limits=_limits(maximum_temporary_bytes=100),
        )

    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert native_secret not in str(caught.value)
    assert str(output) not in str(caught.value)
    assert native_secret not in repr(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert not output.exists()


def test_native_assembly_error_is_sanitized_and_candidate_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    native_secret = f"parser-secret:{output}"

    def fail_materialization(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise pikepdf.PdfError(native_secret)

    monkeypatch.setattr(export_module, "_materialize_page", fail_materialization)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.ASSEMBLY_FAILED
    rendered = " ".join(
        (str(caught.value), repr(caught.value), *(str(v) for v in caught.value.args))
    )
    assert native_secret not in rendered
    assert str(output) not in rendered
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert not output.exists()


def test_candidate_writer_io_failure_maps_to_sanitized_assembly_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"
    native_secret = f"disk-full-secret:{output}"

    def fail_write(
        stream: export_module._BoundedCandidateStream,
        data: Buffer,
    ) -> int:
        del stream, data
        raise OSError(28, native_secret, str(output))

    monkeypatch.setattr(export_module._BoundedCandidateStream, "write", fail_write)

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output)

    assert caught.value.code is PdfExportErrorCode.ASSEMBLY_FAILED
    rendered = " ".join(
        (str(caught.value), repr(caught.value), *(str(v) for v in caught.value.args))
    )
    assert native_secret not in rendered
    assert str(output) not in rendered
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert not output.exists()


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        (_control(cancellation_probe=lambda: True), PdfExportErrorCode.CANCELLED),
        (_control(deadline=1.0), PdfExportErrorCode.TIMED_OUT),
    ],
)
def test_cancellation_and_deadline_are_stable(
    control: PdfExportAdapterControl,
    expected: PdfExportErrorCode,
    tmp_path: Path,
) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as caught:
        _run(plan, paths, output, control=control)

    assert caught.value.code is expected
    assert not output.exists()


def test_candidate_and_total_source_budgets_are_enforced(tmp_path: Path) -> None:
    plan, paths = _single_plan(PDF_FIXTURES / "born-digital.pdf")
    candidate = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError) as output_limit:
        _run(
            plan,
            paths,
            candidate,
            limits=_limits(maximum_temporary_bytes=100),
        )
    assert output_limit.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not candidate.exists()

    with pytest.raises(PdfExportError) as source_limit:
        _run(
            plan,
            paths,
            candidate,
            limits=_limits(maximum_total_source_bytes=1),
        )
    assert source_limit.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not candidate.exists()
