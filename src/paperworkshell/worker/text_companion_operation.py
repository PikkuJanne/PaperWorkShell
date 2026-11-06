"""Handle-only companion extraction in the network-denied document worker."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from typing import BinaryIO, cast
from uuid import UUID

from paperworkshell.domain.text_companion import (
    TextCompanionError,
    TextCompanionErrorCode,
    TextCompanionLimits,
    reject_text_companion,
    require_companion_active,
)
from paperworkshell.pdf.text_companion import extract_verified_pdf_text
from paperworkshell.worker.image_normalize_operation import (
    _close_native_handle_no_raise,
    _MappedOutputSection,
    _require_non_inheritable,
)
from paperworkshell.worker.operations import (
    OperationContext,
    resource_limited_operation_result,
    timed_out_operation_result,
)
from paperworkshell.worker.protocol.models import (
    CapabilityDescriptor,
    CaseObjectReference,
    OperationOutcome,
    OperationResultPayload,
    ReferenceArea,
)
from paperworkshell.worker.protocol.text_companion import (
    TEXT_COMPANION_ARCHIVE_KIND,
    TEXT_COMPANION_CAPABILITY,
    TEXT_COMPANION_CONTENT_KIND,
)

if os.name == "nt":
    import msvcrt


@dataclass(frozen=True, slots=True)
class TextCompanionOperationHandler:
    @property
    def descriptor(self) -> CapabilityDescriptor:
        return TEXT_COMPANION_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        parameters = {item.name: item.value for item in context.parameters}
        source_handle = cast(int, parameters["source_handle"])
        output_handle = cast(int, parameters["output_handle"])
        try:
            if context.cancel_requested.is_set():
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if context.deadline_expired():
                return timed_out_operation_result()
            if os.name != "nt":
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            _require_non_inheritable(source_handle, context="companion.source")
            _require_non_inheritable(output_handle, context="companion.output")
            descriptor = msvcrt.open_osfhandle(source_handle, os.O_RDONLY | os.O_BINARY)
            source_handle = 0
            with ExitStack() as stack:
                source = stack.enter_context(os.fdopen(descriptor, "rb", buffering=0))
                taken_output_handle, output_handle = output_handle, 0
                output = stack.enter_context(
                    _MappedOutputSection.adopt(taken_output_handle, context.limits.output_bytes)
                )
                reference = context.inputs[0]
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
                pdf_source = cast(BinaryIO, source)
                pdf_size, pdf_digest = (
                    cast(int, reference.expected_byte_size),
                    cast(str, reference.expected_sha256),
                )
                if reference.content_kind == TEXT_COMPANION_ARCHIVE_KIND:
                    archive_digest = hashlib.sha256()
                    measured = 0
                    while chunk := source.read(min(64 * 1024, pdf_size - measured + 1)):
                        require_companion_active(
                            context.deadline_expired, context.cancel_requested.is_set
                        )
                        measured += len(chunk)
                        if measured > pdf_size:
                            reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
                        archive_digest.update(chunk)
                    if measured != pdf_size or not hmac.compare_digest(
                        archive_digest.hexdigest(), pdf_digest
                    ):
                        reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
                    source.seek(0)
                    archive = stack.enter_context(zipfile.ZipFile(source))
                    members = archive.infolist()
                    position = cast(int, parameters["member_position"])
                    if not 1 <= len(members) <= 128 or position >= len(members):
                        reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
                    member = members[position]
                    pdf_size, pdf_digest = (
                        cast(int, parameters["member_byte_size"]),
                        cast(str, parameters["member_sha256"])[7:],
                    )
                    if (
                        member.compress_type != zipfile.ZIP_STORED
                        or member.file_size != pdf_size
                        or member.compress_size != pdf_size
                        or member.flag_bits & 1
                    ):
                        reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
                    pdf_source = cast(BinaryIO, stack.enter_context(archive.open(member)))
                size, digest = extract_verified_pdf_text(
                    pdf_source,
                    cast(BinaryIO, output),
                    expected_byte_size=pdf_size,
                    expected_sha256=pdf_digest,
                    expected_page_count=cast(int, parameters["page_count"]),
                    limits=TextCompanionLimits(
                        context.limits.output_bytes,
                        context.limits.temporary_storage_bytes,
                        context.limits.page_count,
                    ),
                    deadline=context.deadline_expired,
                    cancellation=context.cancel_requested.is_set,
                )
                output.flush()
                return OperationResultPayload(
                    OperationOutcome.SUCCEEDED,
                    (
                        CaseObjectReference(
                            ReferenceArea.OPERATION_STAGING,
                            cast(UUID, parameters["output_object_id"]),
                            context.operation_id,
                            TEXT_COMPANION_CONTENT_KIND,
                            size,
                            digest,
                        ),
                    ),
                    None,
                )
        except TextCompanionError as error:
            if error.code is TextCompanionErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is TextCompanionErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is TextCompanionErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise
        finally:
            for handle in (source_handle, output_handle):
                if handle > 0:
                    _close_native_handle_no_raise(handle)


def build_text_companion_operation_handler() -> TextCompanionOperationHandler:
    return TextCompanionOperationHandler()
