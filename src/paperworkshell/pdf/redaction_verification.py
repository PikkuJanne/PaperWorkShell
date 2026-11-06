"""Worker-only verifier composition; this module has no export/publication authority.

The reference must be the caller-authenticated reconstruction/OCR result, never
an arbitrary document supplied as its own oracle. Corpus probes are ephemeral;
they are excluded from requests, reports, exception messages and representations.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Buffer, Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import BinaryIO, cast
from uuid import uuid4

import pikepdf

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction_verification import (
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    run_verification_methods,
    verification_probe_digest,
)
from paperworkshell.pdf.raster_pdf_page import _BoundedOutput
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _measure_source,
)
from paperworkshell.pdf.redaction_verification_resources import verify_resource_identity
from paperworkshell.pdf.redaction_verification_structure import inspect_structure
from paperworkshell.pdf.redaction_verification_visual import verify_visual

MethodRunner = Callable[[BinaryIO], VerificationMethodResult]
_SCRATCH_AUTHORITY_TOKEN = object()


class _VerificationScratchAuthority:
    """Local capability backed by a worker-owned retained directory pin.

    It has no wire representation. Only the scoped worker binds its native
    identity validator; ordinary adapter calls keep full ancestry validation.
    """

    __slots__ = ("_path", "_validator")

    def __init__(self, path: Path, validator: Callable[[], None], token: object) -> None:
        if token is not _SCRATCH_AUTHORITY_TOKEN:
            raise ValueError("RV_RESULT_INVALID")
        self._path, self._validator = path, validator

    def require_directory(self, path: Path) -> None:
        if path != self._path or self._validator() is not None:
            raise ValueError("RV_INPUT_CHANGED")


def _bind_verification_scratch(
    path: Path, validator: Callable[[], None]
) -> _VerificationScratchAuthority:
    """Bind a local retained-pin validator; never accept serialized authority."""
    if not isinstance(path, Path) or not path.is_absolute() or not callable(validator):
        raise ValueError("RV_RESULT_INVALID")
    authority = _VerificationScratchAuthority(path, validator, _SCRATCH_AUTHORITY_TOKEN)
    authority.require_directory(path)
    return authority


class _VerificationOutput(_BoundedOutput):
    def __init__(
        self, stream: BinaryIO, maximum: int, control: RedactionReconstructionControl
    ) -> None:
        super().__init__(stream, maximum)
        self.control = control

    def write(self, data: Buffer, /) -> int:
        _checkpoint(self.control)
        return super().write(data)


def _external(
    runner: MethodRunner, stream: BinaryIO, control: RedactionReconstructionControl
) -> VerificationMethodResult:
    position = stream.tell()
    try:
        _checkpoint(control)
        stream.seek(0)
        result = runner(stream)
        _checkpoint(control)
        return result
    finally:
        if stream.seek(position) != position:
            raise ValueError("RV_INPUT_CHANGED")


def _result(method: VerificationMethod, code: str | None) -> VerificationMethodResult:
    if code is None:
        return VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)
    try:
        parsed = VerificationCode(code)
    except ValueError:
        parsed = VerificationCode.INVALID
    if parsed in {VerificationCode.CLEAR, VerificationCode.MISSING}:
        parsed = VerificationCode.INVALID
    return VerificationMethodResult(method, VerificationStatus.FAIL, parsed)


class _ScratchAccessDenied(Exception):
    """Private working-directory ancestry could not be authenticated."""


def _failure(error: Exception) -> VerificationCode:
    if isinstance(error, _ScratchAccessDenied):
        return VerificationCode.SCRATCH_ACCESS_DENIED
    if isinstance(error, PdfExportError):
        return {
            PdfExportErrorCode.CANCELLED: VerificationCode.CANCELLED,
            PdfExportErrorCode.TIMED_OUT: VerificationCode.TIMED_OUT,
            PdfExportErrorCode.LIMIT_EXCEEDED: VerificationCode.LIMIT,
            PdfExportErrorCode.SOURCE_CHANGED: VerificationCode.BINDING,
        }.get(error.code, VerificationCode.FAILED)
    if isinstance(error, (MemoryError, RecursionError)):
        return VerificationCode.LIMIT
    return VerificationCode.FAILED


def _scratch(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except PermissionError:
            raise _ScratchAccessDenied from None
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError


def verify_redaction(
    candidate: BinaryIO,
    reference: BinaryIO,
    request: RedactionVerificationRequest,
    scratch_directory: Path,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
    *,
    forbidden_tokens: tuple[str, ...] = (),
    forbidden_stream_sha256: tuple[str, ...] = (),
    ocr_runner: MethodRunner | None = None,
    viewer_runner: MethodRunner | None = None,
    _scratch_authority: _VerificationScratchAuthority | None = None,
) -> RedactionVerificationReport:
    """Inspect exact private bytes, resave, and repeat all applicable checks.

    Optional runners supply actual per-stream evidence; missing methods remain
    unknown. The explicit runtime profile requires local OCR and separately
    qualified release-level viewer coverage. Legacy all-seven completeness stays
    unchanged. A report is data, not a capability; publication binds its own
    authenticated worker result and independent release approval.
    """
    if (
        type(request) is not RedactionVerificationRequest
        or type(limits) is not RedactionReconstructionLimits
    ):
        raise ValueError("RV_RESULT_INVALID")
    request.__post_init__()
    limits.__post_init__()
    viewer_resave = request.profile_id == VIEWER_VERIFICATION_PROFILE
    original_candidate: int | None = None
    original_reference: int | None = None
    results: dict[VerificationMethod, VerificationMethodResult] = {}
    resaved: Path | None = None
    owned = False

    def require_scratch() -> None:
        if _scratch_authority is None:
            _scratch(scratch_directory)
        elif type(_scratch_authority) is _VerificationScratchAuthority:
            _scratch_authority.require_directory(scratch_directory)
        else:
            raise ValueError("RV_RESULT_INVALID")

    try:
        require_scratch()
        resaved = scratch_directory / f"{uuid4()}.pdf"
        original_candidate, original_reference = candidate.tell(), reference.tell()
        _checkpoint(control)
        if (
            type(forbidden_tokens) is not tuple
            or type(forbidden_stream_sha256) is not tuple
            or len(forbidden_tokens) + len(forbidden_stream_sha256) > limits.maximum_pdf_objects
            or any(
                type(value) is not str or not 1 <= len(value) <= 1024 for value in forbidden_tokens
            )
            or any(type(value) is not str or len(value) != 64 for value in forbidden_stream_sha256)
            or sum(map(len, forbidden_tokens)) * 16 > limits.maximum_memory_bytes // 8
        ):
            raise PdfExportError(PdfExportErrorCode.LIMIT_EXCEEDED, "verification.probe_limits")
        if (
            request.probes_sha256
            != verification_probe_digest(forbidden_tokens, forbidden_stream_sha256)
            or request.affected_pages
            and not forbidden_stream_sha256
        ):
            raise PdfExportError(PdfExportErrorCode.SOURCE_CHANGED, "verification.probes")
        if (
            request.page_count > limits.maximum_pages
            or request.candidate_byte_size
            + request.reference_byte_size
            + limits.maximum_output_bytes
            > limits.maximum_temporary_storage_bytes
        ):
            raise PdfExportError(PdfExportErrorCode.LIMIT_EXCEEDED, "verification.resources")

        def bound() -> bool:
            return _measure_source(candidate, limits.maximum_source_bytes, control) == (
                request.candidate_byte_size,
                request.candidate_sha256,
            ) and _measure_source(reference, limits.maximum_source_bytes, control) == (
                request.reference_byte_size,
                request.reference_sha256,
            )

        if not bound():
            raise PdfExportError(PdfExportErrorCode.SOURCE_CHANGED, "verification.inputs")

        evidence = inspect_structure(
            candidate,
            forbidden_tokens=forbidden_tokens,
            forbidden_stream_sha256=forbidden_stream_sha256,
            limits=limits,
            control=control,
            viewer_resave=viewer_resave,
        )
        if evidence.passed:
            evidence = verify_resource_identity(
                candidate, reference, limits, control, viewer_resave=viewer_resave
            )
        results[VerificationMethod.STRUCTURE] = _result(
            VerificationMethod.STRUCTURE, None if evidence.passed else evidence.code
        )
        visual = verify_visual(
            candidate,
            reference,
            forbidden_tokens=forbidden_tokens,
            affected_pages=request.affected_pages,
            limits=limits,
            control=control,
        )
        for method, code in (
            (VerificationMethod.TEXT, visual.text_code),
            (VerificationMethod.IMAGES, visual.image_code),
            (VerificationMethod.RENDER, visual.render_code),
        ):
            results[method] = _result(method, code)

        _checkpoint(control)
        require_scratch()
        candidate.seek(0)
        with pikepdf.Pdf.open(
            candidate, attempt_recovery=False, suppress_warnings=True
        ) as document:
            if len(document.pages) != request.page_count or document.get_warnings():
                raise ValueError
            with resaved.open("xb") as output:
                owned = True
                bounded = _VerificationOutput(output, limits.maximum_output_bytes, control)
                document.save(
                    cast(BinaryIO, bounded),
                    deterministic_id=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.disable,
                    encryption=False,
                    linearize=False,
                    fix_metadata_version=not viewer_resave,
                    progress=lambda _value: _checkpoint(control),
                )
                bounded.flush()
                os.fsync(output.fileno())
        _checkpoint(control)
        require_scratch()
        external: dict[VerificationMethod, VerificationMethodResult] = {}
        with resaved.open("rb") as reopened:
            resaved_identity = _measure_source(reopened, limits.maximum_output_bytes, control)
            after = inspect_structure(
                reopened,
                forbidden_tokens=forbidden_tokens,
                forbidden_stream_sha256=forbidden_stream_sha256,
                limits=limits,
                control=control,
                viewer_resave=viewer_resave,
            )
            if after.passed:
                after = verify_resource_identity(
                    reopened, reference, limits, control, viewer_resave=viewer_resave
                )
            appearance = verify_visual(
                reopened,
                reference,
                forbidden_tokens=forbidden_tokens,
                affected_pages=request.affected_pages,
                limits=limits,
                control=control,
            )
            for method, runner in (
                (VerificationMethod.OCR, ocr_runner),
                (VerificationMethod.VIEWERS, viewer_runner),
            ):
                if runner is not None:
                    method_report = run_verification_methods(
                        request, {method: partial(_external, runner, reopened, control)}
                    )
                    external[method] = next(
                        item for item in method_report.methods if item.method is method
                    )
            if _measure_source(reopened, limits.maximum_output_bytes, control) != resaved_identity:
                raise PdfExportError(
                    PdfExportErrorCode.SOURCE_CHANGED, "verification.resave_recheck"
                )
        codes = (
            None if after.passed else after.code,
            appearance.text_code,
            appearance.image_code,
            appearance.render_code,
        )
        results[VerificationMethod.RESAVE] = _result(
            VerificationMethod.RESAVE, next((code for code in codes if code is not None), None)
        )
        for method, runner in (
            (VerificationMethod.OCR, ocr_runner),
            (VerificationMethod.VIEWERS, viewer_runner),
        ):
            if runner is not None:
                # The pure orchestrator checks identity, shape and every status.
                method_report = run_verification_methods(
                    request, {method: partial(_external, runner, candidate, control)}
                )
                results[method] = next(
                    item for item in method_report.methods if item.method is method
                )
                if external[method].status is not VerificationStatus.PASS:
                    results[method] = external[method]
                    results[VerificationMethod.RESAVE] = _result(
                        VerificationMethod.RESAVE, "RV_RESAVE_FAILED"
                    )
        if not bound():
            raise PdfExportError(PdfExportErrorCode.SOURCE_CHANGED, "verification.input_recheck")
        require_scratch()
        _checkpoint(control)
    except Exception as error:
        code = _failure(error)
        results = {
            method: VerificationMethodResult(method, VerificationStatus.FAIL, code)
            for method in VerificationMethod
        }
    finally:
        cleanup_failed = False
        for stream, position in ((candidate, original_candidate), (reference, original_reference)):
            if position is None:
                continue
            try:
                if stream.seek(position) != position:
                    raise OSError
            except Exception:
                cleanup_failed = True
        if owned and resaved is not None:
            try:
                require_scratch()
                resaved.unlink()
                require_scratch()
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            results = {
                method: VerificationMethodResult(
                    method, VerificationStatus.FAIL, VerificationCode.FAILED
                )
                for method in VerificationMethod
            }
    report = run_verification_methods(
        request, {method: partial(_identity, item) for method, item in results.items()}
    )
    # The result remains unsigned even on complete method evidence.
    return replace(report, unsigned_derivative=True)


def _identity(item: VerificationMethodResult) -> VerificationMethodResult:
    return item
