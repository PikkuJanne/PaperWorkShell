"""Fixed isolated probe for the production PDF operation adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import Event
from uuid import UUID

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
sys.path.insert(0, str(_SOURCE_ROOT))
_SITE_PACKAGES = Path(sys.prefix) / "Lib" / "site-packages"
if _SITE_PACKAGES.is_dir():
    sys.path.insert(1, str(_SITE_PACKAGES))

from paperworkshell.domain.pdf_inspection import PdfInspectionError  # noqa: E402
from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.operations import OperationContext  # noqa: E402
from paperworkshell.worker.pdf_operation import WindowsPdfInspectionAdapter  # noqa: E402
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import read_frame, write_frame  # noqa: E402
from paperworkshell.worker.protocol.models import (  # noqa: E402
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    verify_runtime_isolation()
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    payload = read_frame(reader)
    if payload is None:
        return 2
    try:
        request = json.loads(payload)
        source_handle = request["source_handle"]
        source_size = request["source_size"]
        source_sha256 = request["source_sha256"]
        if (
            type(source_handle) is not int
            or source_handle < 1
            or type(source_size) is not int
            or source_size < 1
            or type(source_sha256) is not str
        ):
            return 3
        source = CaseObjectReference(
            ReferenceArea.SOURCE_READ,
            UUID("12000000-0000-4000-8000-000000000105"),
            None,
            "pdf",
            source_size,
            source_sha256,
        )
        context = OperationContext(
            UUID("11000000-0000-4000-8000-000000000105"),
            ResourceLimits(
                wall_time_ms=10_000,
                cpu_time_ms=5_000,
                memory_bytes=384 * 1024 * 1024,
                temporary_storage_bytes=8 * 1024 * 1024,
                output_bytes=4_096,
                page_count=100,
                pixel_count=1,
            ),
            Event(),
            lambda _progress: None,
            (source,),
        )
        result = WindowsPdfInspectionAdapter().inspect(context, source_handle)
        response: dict[str, object] = {
            "status": "ok",
            "open_state": result.open_state.value,
            "page_count": result.page_count,
        }
    except PdfInspectionError as error:
        response = {
            "status": "pdf_error",
            "code": error.code.value,
            "context": error.context,
        }
    except BaseException:
        response = {"status": "unexpected"}
    write_frame(
        writer,
        json.dumps(response, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    del grant
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
