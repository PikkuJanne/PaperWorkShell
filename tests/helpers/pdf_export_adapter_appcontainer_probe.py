"""Run the real PDF-export adapter inside the restricted AppContainer test host."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from threading import Event
from uuid import UUID

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
_SITE_PACKAGES = Path(sys.executable).parent / "Lib" / "site-packages"
sys.path[:0] = [str(_SOURCE_ROOT), str(_SITE_PACKAGES)]

from paperworkshell.domain.pdf_export import (  # noqa: E402
    PdfExportError,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits  # noqa: E402
from paperworkshell.domain.pdf_rendering import PdfPageRenderLimits  # noqa: E402
from paperworkshell.pdf.export_adapter import (  # noqa: E402
    PdfExportAdapter,
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker  # noqa: E402
from paperworkshell.worker._windows_broker import WindowsResultPipe  # noqa: E402
from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.operations import OperationContext  # noqa: E402
from paperworkshell.worker.pdf_export_buffers import (  # noqa: E402
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_export_operation import (  # noqa: E402
    _materialize_worker_private_temp,
    build_pdf_export_operation_handler,
)
from paperworkshell.worker.pdf_export_plan import encode_pdf_export_worker_plan  # noqa: E402
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import write_frame  # noqa: E402
from paperworkshell.worker.protocol.models import (  # noqa: E402
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

_CASE_ID = UUID("11210000-0000-4000-8000-000000000112")
_SOURCE_ID = UUID("21210000-0000-4000-8000-000000000112")
_PAGE_IDS = (
    UUID("31210000-0000-4000-8000-000000000112"),
    UUID("31210000-0000-4000-8000-000000000113"),
)
_SOURCE_NAME = "pdf-export-adapter-probe.pdf"
_QPDF_ENVIRONMENT = "PWS_QPDF_EXECUTABLE"
_OPERATION_ID = UUID("41210000-0000-4000-8000-000000000112")
_PLAN_OBJECT_ID = UUID("41210000-0000-4000-8000-000000000113")
_OUTPUT_OBJECT_ID = UUID("41210000-0000-4000-8000-000000000114")
_FILE_MAP_READ = 0x0000_0004
_FILE_MAP_WRITE = 0x0000_0002
_FILE_READ_DATA = 0x0000_0001
_FILE_READ_ATTRIBUTES = 0x0000_0080
_FILE_WRITE_DATA = 0x0000_0002
_SYNCHRONIZE = 0x0010_0000
_FILE_SHARE_READ = 0x0000_0001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x0000_0080


def _plan(source: Path) -> PdfExportPlan:
    payload = source.read_bytes()
    selected_source = PdfExportSource(
        source_id=_SOURCE_ID,
        byte_size=len(payload),
        page_count=2,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return PdfExportPlan(
        case_id=_CASE_ID,
        manifest_revision=1,
        manifest_sha256="a" * 64,
        manifest_cas_token="b" * 64,
        sources=(selected_source,),
        pages=tuple(
            PdfExportPage(
                output_page_index=index,
                page_id=_PAGE_IDS[index],
                source_id=_SOURCE_ID,
                source_page_index=index,
                additional_rotation=PdfExportRotation.DEGREES_0,
            )
            for index in range(2)
        ),
    )


def _limits() -> PdfExportAdapterLimits:
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=2,
            maximum_objects=2_048,
            maximum_input_bytes=32 * 1024 * 1024,
            maximum_temporary_bytes=4 * 1024 * 1024,
            maximum_output_bytes=4 * 1024 * 1024,
            maximum_tool_output_bytes=64 * 1024,
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=2,
            maximum_width_pixels=1_024,
            maximum_height_pixels=1_024,
            maximum_pixels=1_048_576,
            maximum_bitmap_bytes=4 * 1024 * 1024,
            maximum_destination_bytes=4 * 1024 * 1024,
            maximum_memory_bytes=4 * 1024 * 1024,
            wall_time_ms=25_000,
        ),
        maximum_total_source_bytes=32 * 1024 * 1024,
        verification_width_pixels=1_024,
        verification_height_pixels=1_024,
    )


def _kernel32() -> object:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _duplicate_local_handle(handle: int, access: int) -> int:
    kernel32 = _kernel32()
    current_process = kernel32.GetCurrentProcess  # type: ignore[attr-defined]
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    duplicate = kernel32.DuplicateHandle  # type: ignore[attr-defined]
    duplicate.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate.restype = wintypes.BOOL
    target = wintypes.HANDLE()
    if not duplicate(
        current_process(),
        wintypes.HANDLE(handle),
        current_process(),
        ctypes.byref(target),
        access,
        False,
        0,
    ):
        raise OSError("reduced handle duplication failed")
    value = int(target.value or 0)
    if value <= 0:
        raise OSError("reduced handle duplication returned no authority")
    return value


def _open_reduced_source(path: Path) -> int:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW  # type: ignore[attr-defined]
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path),
        _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    value = int(handle or 0)
    if value <= 0:
        raise OSError("reduced source open failed")
    return value


def _close_handle_no_raise(handle: int) -> None:
    try:
        close = _kernel32().CloseHandle  # type: ignore[attr-defined]
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(wintypes.HANDLE(handle))
    except BaseException:
        return


def _operation_context(
    plan: PdfExportPlan,
    plan_payload: bytes,
    *,
    plan_handle: int,
    output_handle: int,
    result_handle: int,
) -> OperationContext:
    source = plan.sources[0]
    return OperationContext(
        operation_id=_OPERATION_ID,
        case_id=plan.case_id,
        limits=ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=25_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=32 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            page_count=2,
            pixel_count=1_048_576,
        ),
        cancel_requested=Event(),
        report_progress=lambda _progress: None,
        deadline_expired=lambda: False,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.OPERATION_TEMP,
                object_id=_PLAN_OBJECT_ID,
                operation_id=_OPERATION_ID,
                content_kind=PDF_EXPORT_PLAN_CONTENT_KIND,
                expected_byte_size=len(plan_payload),
                expected_sha256=hashlib.sha256(plan_payload).hexdigest(),
            ),
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=source.source_id,
                operation_id=None,
                content_kind="pdf",
                expected_byte_size=source.byte_size,
                expected_sha256=source.sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                PDF_EXPORT_PLAN_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                plan_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                _OUTPUT_OBJECT_ID,
            ),
        ),
    )


def _run_operation_probe(source: Path, plan: PdfExportPlan) -> dict[str, bool | int | str]:
    probe_step = "source_open"
    source_handle = _open_reduced_source(source)
    transferred = [source_handle]
    try:
        probe_step = "buffers"
        with (
            WindowsPdfExportOutputBuffer.create(4 * 1024 * 1024) as output,
            WindowsResultPipe.create() as result_pipe,
        ):
            probe_step = "handle_duplicate"
            output_handle = _duplicate_local_handle(output.write_handle_value, _FILE_MAP_WRITE)
            result_handle = _duplicate_local_handle(
                result_pipe.write_handle_value,
                _FILE_WRITE_DATA | _SYNCHRONIZE,
            )
            transferred.extend((output_handle, result_handle))
            result_pipe.close_local_writer()
            probe_step = "plan_encode"
            plan_payload = encode_pdf_export_worker_plan(
                plan,
                {_SOURCE_ID: source_handle},
            )
            probe_step = "plan_buffer"
            with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
                plan_handle = _duplicate_local_handle(
                    plan_buffer.read_handle_value,
                    _FILE_MAP_READ,
                )
                transferred.append(plan_handle)
                probe_step = "context"
                context = _operation_context(
                    plan,
                    plan_payload,
                    plan_handle=plan_handle,
                    output_handle=output_handle,
                    result_handle=result_handle,
                )
                probe_step = "operation_execute"
                result = build_pdf_export_operation_handler().execute(context)
            probe_step = "operation_result"
            if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
                return {"ok": False, "stage": "operation_result"}
            output_reference = result.outputs[0]
            if output_reference.expected_byte_size is None:
                return {"ok": False, "stage": "operation_reference"}
            probe_step = "output_read"
            observed = b"".join(output.iter_chunks(output_reference.expected_byte_size))
            probe_step = "report_read"
            result_pipe.read_complete_frame()
            return {
                "ok": True,
                "operation_output_byte_size": len(observed),
            }
    except PdfExportError as error:
        return {
            "ok": False,
            "stage": "operation",
            "error_code": error.code.value,
            "error_context": error.context,
        }
    except BaseException:
        return {
            "ok": False,
            "stage": "operation_unexpected",
            "probe_step": probe_step,
        }
    finally:
        for handle in transferred:
            _close_handle_no_raise(handle)


def _run_probe() -> dict[str, bool | int | str]:
    qpdf = os.environ.get(_QPDF_ENVIRONMENT)
    if qpdf is None:
        return {"ok": False, "stage": "configuration"}
    source = Path(__file__).with_name(_SOURCE_NAME)
    output = _materialize_worker_private_temp() / "pdf-export-adapter-result.pdf"
    deadline = time.monotonic() + 25.0
    adapter = PdfExportAdapter(qpdf_checker=QpdfCommandChecker(Path(qpdf)))
    try:
        plan = _plan(source)
        report = adapter.assemble_and_verify(
            plan,
            {_SOURCE_ID: source},
            output,
            _limits(),
            PdfExportAdapterControl(
                deadline=deadline,
                cancellation_probe=lambda: False,
                monotonic=time.monotonic,
            ),
        )
    except PdfExportError as error:
        return {
            "ok": False,
            "stage": "adapter",
            "error_code": error.code.value,
            "error_context": error.context,
        }
    except BaseException:
        return {"ok": False, "stage": "unexpected"}
    operation = _run_operation_probe(source, plan)
    if operation.get("ok") is not True:
        return operation
    return {
        "ok": True,
        "page_count": report.page_count,
        "output_byte_size": report.output_byte_size,
        "check_count": len(report.checks),
        "operation_output_byte_size": operation["operation_output_byte_size"],
    }


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    del grant
    evidence = verify_runtime_isolation()
    result = {
        "app_container": evidence.app_container,
        "in_job": evidence.in_job,
        "minimal_environment": evidence.minimal_environment,
        **_run_probe(),
    }
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    write_frame(
        writer,
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
