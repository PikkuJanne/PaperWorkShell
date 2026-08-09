from __future__ import annotations

import ast
import traceback
from io import BytesIO
from pathlib import Path

import pytest

from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfRenderError,
    PdfRenderErrorCode,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer

_ROOT = Path(__file__).resolve().parents[2]
_PDF_FIXTURES = _ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf"
_ADAPTER = _ROOT / "src" / "paperworkshell" / "pdf" / "pdfium_renderer.py"
_SINK_SENTINEL = b"PREEXISTING-SINK-BYTES"
_SOURCE_CANARY = "PRIVATE-APPLICANT-SOURCE-CANARY"


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


class _CanaryReadFailure(BytesIO):
    def read(self, _size: int | None = -1, /) -> bytes:
        raise OSError(_SOURCE_CANARY)


def _limits() -> PdfPageRenderLimits:
    return PdfPageRenderLimits(
        maximum_pages=32,
        maximum_width_pixels=512,
        maximum_height_pixels=512,
        maximum_pixels=262_144,
        maximum_bitmap_bytes=1_048_576,
        maximum_destination_bytes=1_048_576,
        maximum_memory_bytes=1_048_576,
        wall_time_ms=10_000,
    )


def _formatted_exception(error: BaseException) -> str:
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


@pytest.mark.parametrize(
    "fixture_name",
    (
        "malformed-truncated.pdf",
        "password-required.pdf",
        "render-malformed-page-tree.pdf",
    ),
)
def test_hostile_pdf_failure_is_sanitized_atomic_and_position_neutral(
    fixture_name: str,
) -> None:
    fixture = _PDF_FIXTURES / fixture_name
    source = BytesIO(fixture.read_bytes())
    source.seek(min(5, len(source.getbuffer())))
    original_source_position = source.tell()
    destination = BytesIO(_SINK_SENTINEL)
    destination.seek(4)
    original_destination_position = destination.tell()

    with pytest.raises(PdfRenderError) as raised:
        PdfiumPageRenderer().render(
            source,
            destination,
            PdfPageRenderRequest(0, 64, 64),
            _limits(),
            _Control(),
        )

    error = raised.value
    assert error.code in {
        PdfRenderErrorCode.SOURCE_INVALID,
        PdfRenderErrorCode.GEOMETRY_INVALID,
    }
    assert error.__cause__ is None
    assert error.__context__ is None
    assert str(fixture) not in str(error)
    assert str(fixture) not in _formatted_exception(error)
    assert source.tell() == original_source_position
    assert destination.getvalue() == _SINK_SENTINEL
    assert destination.tell() == original_destination_position


def test_source_callback_canary_is_absent_from_error_and_traceback() -> None:
    source = _CanaryReadFailure((_PDF_FIXTURES / "blank.pdf").read_bytes())
    source.seek(7)
    destination = BytesIO(_SINK_SENTINEL)
    destination.seek(3)

    with pytest.raises(PdfRenderError) as raised:
        PdfiumPageRenderer().render(
            source,
            destination,
            PdfPageRenderRequest(0, 16, 16),
            _limits(),
            _Control(),
        )

    error = raised.value
    assert error.code is PdfRenderErrorCode.SOURCE_INVALID
    assert error.__cause__ is None
    assert error.__context__ is None
    assert _SOURCE_CANARY not in str(error)
    assert _SOURCE_CANARY not in _formatted_exception(error)
    assert source.tell() == 7
    assert destination.getvalue() == _SINK_SENTINEL
    assert destination.tell() == 3


def test_pdfium_adapter_has_no_process_network_logging_or_shell_surface() -> None:
    tree = ast.parse(_ADAPTER.read_text(encoding="utf-8"), filename=str(_ADAPTER))
    imported_roots: set[str] = set()
    forbidden_calls: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and (
                    node.func.attr in {"system", "popen", "startfile"}
                    or node.func.attr.startswith(("exec", "spawn"))
                )
            ):
                forbidden_calls.append(f"os.{node.func.attr}")
            if any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                forbidden_calls.append("shell=True")

    assert imported_roots.isdisjoint({"subprocess", "socket", "logging"})
    assert forbidden_calls == []
