"""Worker-only authenticated reconstruction, sanitization and verification.

The supported output is one affected image-only page. The admitted runtime
composition performs actual OCR checks; the legacy private profile leaves OCR
unknown. Human viewer qualification and publication authority are separate.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pikepdf

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_export_candidate import (
    RedactionExportWorkerReport,
    validate_redaction_export_worker_report,
)
from paperworkshell.domain.redaction_ocr import RedactionOcrRequest
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    RedactionVerificationRequest,
    verification_probe_digest,
)
from paperworkshell.pdf.redaction_export_ocr import RedactionCandidateOcrVerifier
from paperworkshell.pdf.redaction_ocr import RedactionOcrProcessor, _exclusion_boxes
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _measure_source,
)
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
from paperworkshell.pdf.redaction_verification import (
    _VerificationScratchAuthority,
    verify_redaction,
)
from paperworkshell.pdf.redaction_verification_structure import _Inspection, _Rejected

_NUMBER = rb"[-+]?(?:[0-9]{1,32}(?:\.[0-9]{0,32})?|\.[0-9]{1,32})"
_GENERIC = re.compile(
    rb"\s*(?:q|Q|(?:" + _NUMBER + rb"\s+){6}cm|/[A-Za-z][A-Za-z0-9_]{0,63}\s+Do)\s*"
)


def _generic_placement(data: bytes, control: RedactionReconstructionControl) -> bool:
    """Recognize a deliberately narrow placement grammar without a token list."""
    position = 0
    while position < len(data):
        _checkpoint(control)
        match = _GENERIC.match(data, position)
        if match is None or match.end() == position:
            return False
        position = match.end()
    return True


def _source_fingerprints(
    source: BinaryIO,
    request: RedactionReconstructionRequest,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> tuple[str, ...]:
    """Bound all decoded affected content and reachable rendering payloads.

    Unproved filters fail closed. Generic raster placement may legitimately be
    identical after reconstruction; its image payload is fingerprinted instead.
    """
    _checkpoint(control)
    source.seek(0)
    inspection = _Inspection((), (), limits, control)
    hashes: set[str] = set()
    with pikepdf.Pdf.open(source, attempt_recovery=False, suppress_warnings=True) as document:
        if (
            len(document.pages) != request.source_page_count
            or len(document.objects) > limits.maximum_pdf_objects
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.source_objects")
        page = document.pages[request.source_page_index]
        contents = page.obj.get("/Contents")
        streams = contents if isinstance(contents, pikepdf.Array) else (contents,)
        if len(streams) > limits.maximum_pdf_objects:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.source_content")
        for stream in streams:
            if not isinstance(stream, pikepdf.Stream):
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID, "redaction_export.source_content"
                )
            decoded = inspection.decoded(stream)
            if not _generic_placement(decoded, control):
                hashes.add(hashlib.sha256(decoded).hexdigest())
            del decoded
        stack: list[tuple[object, int]] = [(page.obj.get("/Resources"), 0)]
        seen: set[tuple[int, int]] = set()
        while stack:
            value, depth = stack.pop()
            inspection.node(depth)
            if isinstance(value, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
                identity = value.objgen
                if identity != (0, 0):
                    if identity in seen:
                        continue
                    seen.add(identity)
            if (
                isinstance(value, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array))
                and len(value.stream_dict if isinstance(value, pikepdf.Stream) else value)
                + len(stack)
                > limits.maximum_pdf_objects
            ):
                reject_pdf_export(
                    PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.source_graph"
                )
            if isinstance(value, pikepdf.Stream):
                if value.get("/Subtype") in {pikepdf.Name.Image, pikepdf.Name.Form}:
                    hashes.add(hashlib.sha256(inspection.decoded(value)).hexdigest())
                stack.extend((item, depth + 1) for _, item in value.stream_dict.items())
            elif isinstance(value, pikepdf.Dictionary):
                stack.extend((item, depth + 1) for _, item in value.items())
            elif isinstance(value, pikepdf.Array):
                stack.extend((item, depth + 1) for item in value)
            if len(stack) > limits.maximum_pdf_objects:
                reject_pdf_export(
                    PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.source_graph"
                )
        if document.get_warnings() or not hashes:
            reject_pdf_export(
                PdfExportErrorCode.RESULT_INVALID, "redaction_export.source_inventory"
            )
    return tuple(sorted(hashes))


@dataclass(frozen=True, slots=True)
class RedactionExportAdapter:
    reconstructor: RedactionReconstructionAdapter = field(
        default_factory=RedactionReconstructionAdapter
    )
    sanitizer: PikepdfRedactionSanitizer = field(default_factory=PikepdfRedactionSanitizer)
    ocr: RedactionOcrProcessor | None = field(default=None, repr=False)

    def prepare(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionReconstructionRequest,
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
        *,
        operation_id: UUID,
        output_object_id: UUID,
        _scratch_authority: _VerificationScratchAuthority | None = None,
    ) -> RedactionExportWorkerReport:
        reference_path = scratch_directory / f"{uuid4().hex}.pdf"
        reference_owned = candidate_owned = completed = False
        try:
            if _measure_source(source, limits.maximum_source_bytes, control) != (
                request.source_byte_size,
                request.source_sha256,
            ):
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.source")
            probes = _source_fingerprints(source, request, limits, control)
            reconstruction = self.reconstructor.reconstruct(
                source, reference_path, scratch_directory, request, limits, control
            )
            reference_owned = True
            sanitization_request = RedactionSanitizationRequest(
                request.case_id,
                uuid4(),
                reconstruction.output_sha256,
                reconstruction.output_byte_size,
                1,
            )
            with reference_path.open("rb") as reference:
                sanitization = self.sanitizer.sanitize(
                    reference, output_path, scratch_directory, sanitization_request, limits, control
                )
            candidate_owned = True
            verification_request = RedactionVerificationRequest(
                sanitization.output_sha256,
                sanitization.output_byte_size,
                reconstruction.output_sha256,
                reconstruction.output_byte_size,
                1,
                (0,),
                request.fingerprint,
                verification_probe_digest((), probes),
                profile_id=(
                    VERIFICATION_PROFILE if self.ocr is None else RUNTIME_VERIFICATION_PROFILE
                ),
            )
            ocr_runner = None
            if self.ocr is not None:
                exclusions = _exclusion_boxes(
                    source, RedactionOcrRequest(request), reconstruction, limits
                )
                # The exact reference, candidate and maximum internal resave stay
                # live outside the OCR subtree. Reserve all three before OCR.
                ocr_private = (
                    limits.maximum_temporary_storage_bytes
                    - reconstruction.output_byte_size
                    - sanitization.output_byte_size
                    - limits.maximum_output_bytes
                )
                ocr_runner = RedactionCandidateOcrVerifier(
                    self.ocr,
                    self.reconstructor,
                    reconstruction,
                    exclusions,
                    scratch_directory,
                    limits,
                    control,
                    ocr_private,
                )
            with output_path.open("rb") as candidate, reference_path.open("rb") as reference:
                verification = verify_redaction(
                    candidate,
                    reference,
                    verification_request,
                    scratch_directory,
                    limits,
                    control,
                    forbidden_stream_sha256=probes,
                    ocr_runner=ocr_runner,
                    _scratch_authority=_scratch_authority,
                )
            _checkpoint(control)
            if _measure_source(source, limits.maximum_source_bytes, control) != (
                request.source_byte_size,
                request.source_sha256,
            ):
                reject_pdf_export(
                    PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.source_recheck"
                )
            report = RedactionExportWorkerReport(
                operation_id,
                output_object_id,
                reconstruction,
                sanitization_request,
                sanitization,
                verification_request,
                verification,
            )
            validate_redaction_export_worker_report(request, report)
            completed = True
            return report
        except PdfExportError:
            raise
        except _Rejected as error:
            code = {
                "RV_CANCELLED": PdfExportErrorCode.CANCELLED,
                "RV_TIMED_OUT": PdfExportErrorCode.TIMED_OUT,
                "RV_LIMIT_EXCEEDED": PdfExportErrorCode.LIMIT_EXCEEDED,
            }.get(error.code, PdfExportErrorCode.WORKER_FAILED)
            reject_pdf_export(code, "redaction_export.source_inventory")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_export.pipeline")
        finally:
            if reference_owned:
                reference_path.unlink()
            if candidate_owned and not completed:
                output_path.unlink()
