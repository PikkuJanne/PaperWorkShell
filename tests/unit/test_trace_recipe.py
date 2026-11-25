"""Focused contracts for the portable recipe and authenticated result files."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.trace_recipe import (
    TraceOutputStatus,
    TraceRecipe,
    TraceRecipeError,
    TraceRecipeErrorCode,
    TraceRecipePage,
    TraceSourceBinding,
    decode_trace_recipe,
    encode_trace_recipe,
)
from paperworkshell.storage.trace_files import (
    inspect_trace_output,
    load_trace_recipe,
    save_trace_recipe,
)


def _recipe() -> TraceRecipe:
    return TraceRecipe(
        (TraceSourceBinding(1, DetectedMediaType.PDF, 3),),
        (TraceRecipePage(1, 3, 90), TraceRecipePage(1, 1, 0)),
    )


def test_trace_recipe_round_trip_and_closed_admission() -> None:
    recipe = _recipe()
    encoded = encode_trace_recipe(recipe)
    assert decode_trace_recipe(encoded) == recipe
    with pytest.raises(TraceRecipeError) as invalid:
        decode_trace_recipe(encoded.replace(b'"rotation":90', b'"rotation":90,"rotation":180'))
    assert invalid.value.code is TraceRecipeErrorCode.INVALID
    with pytest.raises(TraceRecipeError) as future:
        decode_trace_recipe(encoded.replace(b'"recipe_version":1', b'"recipe_version":2'))
    assert future.value.code is TraceRecipeErrorCode.VERSION_UNSUPPORTED
    with pytest.raises(TraceRecipeError) as repeated:
        TraceRecipe(recipe.sources, (recipe.pages[0], recipe.pages[0]))
    assert repeated.value.code is TraceRecipeErrorCode.INVALID


@pytest.mark.skipif(sys.platform != "win32", reason="Windows authenticated storage")
def test_trace_recipe_no_replace_and_output_byte_identity(tmp_path: Path) -> None:
    original = _recipe()
    path = tmp_path / "TRACE Ångström recipe.json"
    save_trace_recipe(path, original)
    assert load_trace_recipe(path) == original
    payload = path.read_bytes()
    with pytest.raises(TraceRecipeError):
        save_trace_recipe(path, original)
    assert path.read_bytes() == payload
    sha256 = hashlib.sha256(payload).hexdigest()
    assert (
        inspect_trace_output(path, len(payload), sha256, lambda: False) is TraceOutputStatus.PRESENT
    )

    path.write_bytes(payload[:-1] + b" ")
    assert (
        inspect_trace_output(path, len(payload), sha256, lambda: False) is TraceOutputStatus.CHANGED
    )
    assert (
        inspect_trace_output(path, len(payload), sha256, lambda: True)
        is TraceOutputStatus.CANCELLED
    )
    path.unlink()
    assert (
        inspect_trace_output(path, len(payload), sha256, lambda: False) is TraceOutputStatus.MISSING
    )
