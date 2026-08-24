"""Launch the production PWS-0306 dialog with only synthetic in-memory presentation data."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import PageCoordinateTransform
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"


def _synthetic_state() -> CoreOverlayToolsState:
    document = cast(
        dict[str, object],
        json.loads(REPRESENTATIVE.read_text(encoding="utf-8")),
    )
    page_id = UUID("30000000-0000-4000-8000-000000000001")
    overlays = cast(list[dict[str, object]], document["overlays"])
    for z_index, overlay in enumerate(overlays):
        overlay["page_id"] = str(page_id)
        overlay["z_index"] = z_index
    snapshot = ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "8" * 64,
        "pws0306_manual_synthetic",
    )
    state = CoreOverlayToolsState.from_snapshot(snapshot, page_id)
    return state.select(state.editor_records[0].overlay_id)


def _synthetic_page_preview() -> QPixmap:
    preview = QPixmap(600, 780)
    preview.fill(QColor("white"))
    painter = QPainter(preview)
    painter.setPen(QPen(QColor("black"), 4))
    painter.drawRect(18, 18, 564, 744)
    painter.setPen(QPen(QColor("gray"), 2))
    for y in range(90, 720, 70):
        painter.drawLine(60, y, 540, y)
    painter.setPen(QColor("black"))
    painter.drawText(70, 65, "SYNTHETIC PAGE — NO APPLICANT DATA")
    painter.end()
    return preview


def main() -> int:
    app = QApplication(sys.argv)
    dialog = CoreOverlayToolsDialog(
        _synthetic_state(),
        page_transform=PageCoordinateTransform(),
        page_preview=_synthetic_page_preview(),
    )
    screen = app.primaryScreen()
    if screen is not None:
        dialog.setWindowTitle(
            "PWS-0306 synthetic NVDA probe — "
            f"effective DPR {screen.devicePixelRatio():.2f}, "
            f"logical DPI {screen.logicalDotsPerInch():.1f}"
        )
    dialog.apply_requested.connect(lambda _state: QTimer.singleShot(0, dialog.set_apply_succeeded))
    dialog.finished.connect(app.quit)
    dialog.resize(960, 540)
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
