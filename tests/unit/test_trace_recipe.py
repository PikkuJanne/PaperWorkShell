"""Focused contracts for the portable recipe and authenticated result files."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtWidgets import QListWidget

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import PageCommandHistory
from paperworkshell.domain.pdf_export import DEFAULT_PDF_EXPORT_ENGINE_PROFILE
from paperworkshell.domain.trace_recipe import (
    TraceOperationKind,
    TraceOutputStatus,
    TraceRecipe,
    TraceRecipeError,
    TraceRecipeErrorCode,
    TraceRecipePage,
    TraceSourceBinding,
    build_trace_plan,
    decode_trace_recipe,
    encode_trace_recipe,
)
from paperworkshell.storage.trace_files import (
    inspect_trace_output,
    load_trace_recipe,
    save_trace_recipe,
)
from paperworkshell.ui.trace_recipe import TraceRecipePane
from tests.unit.test_package_export_window import _application


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


def test_recipe_inspector_uses_saved_mark_geometry_and_package_limits() -> None:
    fixture = Path("schemas/examples/case-manifest-v4-representative.json")
    payload = fixture.read_bytes()
    document = json.loads(payload)
    snapshot = ManifestSnapshot(
        UUID(document["case_id"]),
        document["schema_version"],
        document["revision"],
        document,
        hashlib.sha256(payload).hexdigest(),
        "trace_recipe_inspection_token",
    )
    plan = build_trace_plan(
        snapshot.document, PageCommandHistory.from_snapshot(snapshot).projection
    )
    operation_rows = {operation.kind: row for row, operation in enumerate(plan.operations)}
    _application()
    pane = TraceRecipePane()
    pane.set_plan(plan)
    steps = pane.findChild(QListWidget, "traceRecipeSteps")
    configuration = pane.findChild(QListWidget, "traceOperationConfiguration")
    assert steps is not None and configuration is not None

    def details() -> str:
        return "\n".join(configuration.item(row).text() for row in range(configuration.count()))

    try:
        first_page = UUID(document["pages"][0]["page_id"])
        third_page = UUID(document["pages"][2]["page_id"])
        pane.select_page(first_page)
        steps.setCurrentRow(operation_rows[TraceOperationKind.OVERLAY])
        assert "Layer index 0" in details()
        assert "Box x " in details()
        assert document["overlays"][0]["payload"]["value"] not in details()
        steps.setCurrentRow(operation_rows[TraceOperationKind.REMOVAL])
        assert "Unapplied removal mark" in details()
        assert "width 30.0%" in details()
        pane.select_page(third_page)
        steps.setCurrentRow(operation_rows[TraceOperationKind.REMOVAL])
        assert "Select an affected page" in details()

        steps.setCurrentRow(operation_rows[TraceOperationKind.PACKAGE])
        assert "ZIP package" in details()
        assert "Maximum files: 4" in details()
        assert "Maximum pages per file: 20" in details()
        assert "Requested output names: 2" in details()
        assert "application.pdf" not in details()

        steps.setCurrentRow(operation_rows[TraceOperationKind.EXPORT])
        assert f"qpdf {DEFAULT_PDF_EXPORT_ENGINE_PROFILE.qpdf}" in details()
        assert "Required for OCR" in details()
        assert "Optional searchable text profile" in details()
        assert b'"marks"' not in encode_trace_recipe(plan.recipe)
        assert b'"package"' not in encode_trace_recipe(plan.recipe)
    finally:
        pane.close()


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
