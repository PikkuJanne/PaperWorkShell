"""Privacy and authority regressions for the PWS-0306 full-page overlay editor."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QListWidget

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState, KeyboardDirection
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PRIVATE_VALUE = "PRIVATE_OVERLAY_EDITOR_VALUE_0306_<script>_C:\\Applicant"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state() -> tuple[CoreOverlayToolsState, str]:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE.read_text(encoding="utf-8")),
    )
    overlays = cast(list[dict[str, object]], document["overlays"])
    text = next(
        overlay
        for overlay in overlays
        if cast(dict[str, object], overlay["payload"])["kind"] == OverlayKind.TEXT.value
    )
    cast(dict[str, object], text["payload"])["value"] = PRIVATE_VALUE
    signature = next(
        overlay
        for overlay in overlays
        if cast(dict[str, object], overlay["payload"])["kind"] == OverlayKind.SIGNATURE_IMAGE.value
    )
    asset_id = cast(str, cast(dict[str, object], signature["payload"])["asset_id"])
    snapshot = ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "9" * 64,
        "overlay_editor_privacy_synthetic",
    )
    page_id = UUID(cast(str, text["page_id"]))
    return CoreOverlayToolsState.from_snapshot(snapshot, page_id), asset_id


def test_rows_canvas_status_repr_and_logs_hide_values_assets_and_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _application()
    state, asset_id = _state()
    text = next(record for record in state.editor_records if record.kind is OverlayKind.TEXT)
    selected = state.select(text.overlay_id)
    dialog = CoreOverlayToolsDialog(selected)
    dialog.show()
    app.processEvents()
    try:
        overlay_list = dialog.findChild(QListWidget, "coreOverlayList")
        status = dialog.findChild(QLabel, "coreOverlayToolsStatus")
        assert overlay_list is not None and status is not None
        surfaces: list[str] = [
            dialog.windowTitle(),
            dialog.accessibleName(),
            dialog.accessibleDescription(),
            status.text(),
            status.accessibleDescription(),
            repr(dialog.state),
        ]
        for row in range(overlay_list.count()):
            item = overlay_list.item(row)
            surfaces.extend(
                (
                    item.text(),
                    cast(str, item.data(Qt.ItemDataRole.AccessibleTextRole)),
                    cast(str, item.data(Qt.ItemDataRole.AccessibleDescriptionRole)),
                )
            )
        for label in dialog.findChildren(QLabel):
            surfaces.extend((label.text(), label.accessibleDescription()))
        assert all(PRIVATE_VALUE not in surface for surface in surfaces)
        assert all(asset_id not in surface for surface in surfaces)
        assert all(str(text.overlay_id) not in surface for surface in surfaces)

        logging.getLogger("paperworkshell").info("overlay editor geometry action")
        dialog._transition(lambda current: current.move_selected(KeyboardDirection.RIGHT))
        dialog._canvas_interaction_message(PRIVATE_VALUE, True)
        app.processEvents()
        assert PRIVATE_VALUE not in status.text()
        assert asset_id not in status.text()
        assert PRIVATE_VALUE not in caplog.text
        assert asset_id not in caplog.text
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()
