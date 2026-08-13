"""Run the real image-normalize adapter inside the restricted test host."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
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

from paperworkshell.domain.image_pages import (  # noqa: E402
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    ImagePageError,
    ImagePageErrorCode,
    ImageSourceColorModel,
    ImageSourceFormat,
    decode_image_page_artifact,
    reject_image_page,
)
from paperworkshell.worker._windows_broker import (  # noqa: E402
    WindowsBoundedOutputBuffer,
)
from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.image_normalize_operation import (  # noqa: E402
    WindowsImageNormalizeAdapter,
)
from paperworkshell.worker.operations import (  # noqa: E402
    ImageNormalizeOperationRequest,
    OperationContext,
)
from paperworkshell.worker.protocol.bootstrap import (  # noqa: E402
    decode_bootstrap_grant,
)
from paperworkshell.worker.protocol.framing import write_frame  # noqa: E402
from paperworkshell.worker.protocol.models import (  # noqa: E402
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)

_CASE_ID = UUID("11220000-0000-4000-8000-000000000201")
_SOURCE_ID = UUID("21220000-0000-4000-8000-000000000201")
_OPERATION_ID = UUID("31220000-0000-4000-8000-000000000201")
_OUTPUT_ID = UUID("41220000-0000-4000-8000-000000000201")
_SOURCE_NAME = "image-normalize-adapter-probe.png"
_MAXIMUM_WIDTH_PIXELS = 1
_MAXIMUM_HEIGHT_PIXELS = 1
_PIXEL_BYTES = 4
_OUTPUT_CAPACITY = IMAGE_PAGE_ARTIFACT_HEADER_BYTES + _PIXEL_BYTES

_FILE_MAP_WRITE = 0x0000_0002
_FILE_READ_DATA = 0x0000_0001
_FILE_READ_ATTRIBUTES = 0x0000_0080
_SYNCHRONIZE = 0x0010_0000
_FILE_SHARE_READ = 0x0000_0001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x0000_0080
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_ProbeValue = bool | int | str


def _kernel32() -> object:
    return ctypes.WinDLL("kernel32", use_last_error=True)


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
    opened = create_file(
        os.fspath(path),
        _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    handle = int(opened or 0)
    if handle in {0, _INVALID_HANDLE_VALUE}:
        raise OSError("reduced source open failed")
    return handle


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


def _close_handle_no_raise(handle: int) -> None:
    if handle <= 0:
        return
    try:
        close = _kernel32().CloseHandle  # type: ignore[attr-defined]
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(wintypes.HANDLE(handle))
    except BaseException:
        return


def _source_facts(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb", buffering=0) as source:
        while block := source.read(64 * 1024):
            byte_count += len(block)
            digest.update(block)
    return byte_count, digest.hexdigest()


def _operation_context(source_bytes: int, source_sha256: str) -> OperationContext:
    deadline = time.monotonic() + 20.0
    return OperationContext(
        operation_id=_OPERATION_ID,
        case_id=_CASE_ID,
        limits=ResourceLimits(
            wall_time_ms=20_000,
            cpu_time_ms=15_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=_PIXEL_BYTES,
            output_bytes=_OUTPUT_CAPACITY,
            page_count=1,
            pixel_count=10_000_000,
        ),
        cancel_requested=Event(),
        report_progress=lambda _progress: None,
        deadline_expired=lambda: time.monotonic() >= deadline,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=_SOURCE_ID,
                operation_id=None,
                content_kind=ImageSourceFormat.PNG.value,
                expected_byte_size=source_bytes,
                expected_sha256=source_sha256,
            ),
        ),
    )


def _operation_request(source_handle: int, output_handle: int) -> ImageNormalizeOperationRequest:
    return ImageNormalizeOperationRequest(
        source_handle=source_handle,
        output_handle=output_handle,
        output_object_id=_OUTPUT_ID,
        maximum_width_pixels=_MAXIMUM_WIDTH_PIXELS,
        maximum_height_pixels=_MAXIMUM_HEIGHT_PIXELS,
        additional_rotation=0,
    )


def _empty_result() -> dict[str, _ProbeValue]:
    return {
        "ok": False,
        "stage": "configuration",
        "error_code": "PROBE_CONFIGURATION",
        "error_context": "probe.source",
        "source_immutable": False,
        "output_byte_size": 0,
        "width_pixels": 0,
        "height_pixels": 0,
        "source_color_model": "",
        "source_has_alpha": False,
        "opaque_output": False,
    }


def _run_probe() -> dict[str, _ProbeValue]:
    result = _empty_result()
    source = Path(__file__).with_name(_SOURCE_NAME)
    probe_step = "source_authenticate"
    source_handle = 0
    output_handle = 0
    source_before: tuple[int, str] | None = None
    try:
        source_before = _source_facts(source)
        source_bytes, source_sha256 = source_before
        probe_step = "source_open"
        source_handle = _open_reduced_source(source)
        probe_step = "output_create"
        with WindowsBoundedOutputBuffer.create(_OUTPUT_CAPACITY) as output:
            probe_step = "output_duplicate"
            output_handle = _duplicate_local_handle(
                output.write_handle_value,
                _FILE_MAP_WRITE,
            )
            context = _operation_context(source_bytes, source_sha256)
            request = _operation_request(source_handle, output_handle)
            adapter = WindowsImageNormalizeAdapter()

            # Ownership passes to the adapter for every success and failure path.
            source_handle = 0
            output_handle = 0
            probe_step = "adapter_normalize"
            reference = adapter.normalize(context, request)
            if (
                reference.expected_byte_size is None
                or reference.expected_sha256 is None
                or not 1 <= reference.expected_byte_size <= _OUTPUT_CAPACITY
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.probe_reference")

            probe_step = "output_read"
            encoded = output.read_exact(reference.expected_byte_size)
            if not hmac.compare_digest(
                hashlib.sha256(encoded).hexdigest(),
                reference.expected_sha256,
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.probe_digest")
            probe_step = "artifact_validate"
            artifact = decode_image_page_artifact(
                encoded,
                expected_operation_id=_OPERATION_ID,
                expected_output_id=_OUTPUT_ID,
            )
            normalized = artifact.header.result
            if (
                normalized.metadata.source_format is not ImageSourceFormat.PNG
                or normalized.metadata.source_color_model is not ImageSourceColorModel.RGBA
                or not normalized.metadata.has_alpha
                or (normalized.width_pixels, normalized.height_pixels) != (1, 1)
                or artifact.payload[3::4] != b"\xff"
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.probe_artifact")
            result.update(
                {
                    "ok": True,
                    "stage": "complete",
                    "error_code": "",
                    "error_context": "",
                    "output_byte_size": len(encoded),
                    "width_pixels": normalized.width_pixels,
                    "height_pixels": normalized.height_pixels,
                    "source_color_model": normalized.metadata.source_color_model.value,
                    "source_has_alpha": normalized.metadata.has_alpha,
                    "opaque_output": True,
                }
            )
    except ImagePageError as error:
        result.update(
            {
                "stage": probe_step,
                "error_code": error.code.value,
                "error_context": error.context,
            }
        )
    except BaseException:
        result.update(
            {
                "stage": probe_step,
                "error_code": "PROBE_UNEXPECTED",
                "error_context": f"probe.{probe_step}",
            }
        )
    finally:
        _close_handle_no_raise(source_handle)
        _close_handle_no_raise(output_handle)
        if source_before is not None:
            try:
                result["source_immutable"] = _source_facts(source) == source_before
            except BaseException:
                result["source_immutable"] = False
        if not result["source_immutable"]:
            result.update(
                {
                    "ok": False,
                    "stage": "source_verify",
                    "error_code": "PROBE_SOURCE_CHANGED",
                    "error_context": "probe.source_immutable",
                }
            )
    return result


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
