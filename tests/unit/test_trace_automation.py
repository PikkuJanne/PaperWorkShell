"""Automatic request admission fails closed before case and engine setup."""

from __future__ import annotations

import json
import sys
from base64 import b64encode
from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.app.trace_automation import (
    TraceAutomationError,
    decode_trace_automation_request,
    make_trace_powershell,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.trace_recipe import (
    TraceOperationKind,
    TraceRecipe,
    TraceRecipeError,
    TraceRecipeErrorCode,
    TraceRecipePage,
    TraceSourceBinding,
    encode_trace_recipe,
)


def _recipe() -> TraceRecipe:
    return TraceRecipe(
        (TraceSourceBinding(1, DetectedMediaType.PDF, 3),),
        (TraceRecipePage(1, 3, 90), TraceRecipePage(1, 1, 0)),
    )


def _request(recipe: TraceRecipe) -> dict[str, object]:
    return {
        "recipe_base64": b64encode(encode_trace_recipe(recipe)).decode("ascii"),
        "input_paths": [r"C:\Test data\März; $doc [2].pdf"],
        "output_path": r"C:\Test data\Übergabe.pdf",
    }


def test_automation_embeds_the_exact_versioned_recipe() -> None:
    recipe = _recipe()
    script = make_trace_powershell(recipe).decode("ascii")
    base64_recipe = b64encode(encode_trace_recipe(recipe)).decode("ascii")
    assert f"$recipe = '{base64_recipe}'\n" in script
    assert "& $python -I -B -m paperworkshell --trace-batch" in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path admission")
def test_automation_admits_literal_unicode_paths_and_exact_page_intent() -> None:
    recipe = _recipe()
    request = _request(recipe)
    decoded = decode_trace_automation_request(json.dumps(request).encode())
    assert decoded.recipe == recipe
    assert decoded.inputs == (Path(r"C:\Test data\März; $doc [2].pdf"),)
    assert decoded.destination == Path(r"C:\Test data\Übergabe.pdf")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path admission")
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("output_path", r"\\server\share\out.pdf", "TRACE_REQUEST_INVALID"),
        ("output_path", r"C:\Test data\out.pdf:alternate", "TRACE_REQUEST_INVALID"),
        ("output_path", r"C:\Test data\..\out.pdf", "TRACE_REQUEST_INVALID"),
        ("input_paths", [], "TRACE_BINDINGS_INVALID"),
        ("input_paths", [r"\\.\C:\test.pdf"], "TRACE_INPUT_PATH_INVALID"),
    ],
)
def test_automation_rejects_invalid_bindings_before_opening_a_case(
    field: str, value: object, code: str
) -> None:
    request = _request(_recipe())
    request[field] = value
    with pytest.raises(TraceAutomationError) as raised:
        decode_trace_automation_request(json.dumps(request).encode())
    assert raised.value.code == code


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path admission")
def test_automation_rejects_unsupported_steps_when_saving_and_receiving() -> None:
    recipe = replace(_recipe(), unsupported_operations=(TraceOperationKind.OCR,))
    with pytest.raises(TraceRecipeError) as script_error:
        make_trace_powershell(recipe)
    with pytest.raises(TraceRecipeError) as run_error:
        decode_trace_automation_request(json.dumps(_request(recipe)).encode())
    assert script_error.value.code is run_error.value.code is TraceRecipeErrorCode.UNSUPPORTED
