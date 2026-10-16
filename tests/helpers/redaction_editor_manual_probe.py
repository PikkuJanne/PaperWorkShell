"""One synthetic-only entry point for PWS-0602 keyboard/NVDA review and recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.ui.redaction_tools import RedactionToolsDialog


def main() -> int:
    app = QApplication(sys.argv)
    root = Path(__file__).resolve().parents[2]
    document = cast(
        dict[str, object],
        json.loads(
            (root / "schemas/examples/case-manifest-v3-representative.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    document["redaction_marks"] = []
    state = RedactionToolsState.from_snapshot(
        ManifestSnapshot(
            UUID(cast(str, document["case_id"])),
            3,
            cast(int, document["revision"]),
            document,
            "d" * 64,
            "redaction_manual_synthetic",
        ),
        UUID(cast(list[str], document["page_sequence"])[0]),
    )
    state = state.create(NormalizedPageBox(150_000, 250_000, 700_000, 150_000))
    preview = QPixmap(600, 780)
    preview.fill(QColor("white"))
    painter = QPainter(preview)
    painter.setFont(QFont("Arial", 22))
    painter.drawText(50, 90, "SYNTHETIC REVIEW PAGE")
    painter.drawText(110, 255, "LARGE REMOVAL TARGET")
    painter.drawText(110, 500, "RETAINED SENTINEL")
    painter.end()
    dialog = RedactionToolsDialog(state, page_preview=preview)
    screen = app.primaryScreen()
    if screen is not None:
        dialog.setWindowTitle(
            f"PWS-0602 synthetic NVDA probe — effective DPR {screen.devicePixelRatio():.2f}"
        )
    # First Save deliberately fails; Retry acknowledges intent only. Nothing is written.
    attempts = 0

    def save(_state: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            QTimer.singleShot(
                0, lambda: dialog.save_failed("PWS_UI_UNEXPECTED", "synthetic failure")
            )
        else:
            QTimer.singleShot(0, dialog.finish_saved)

    dialog.save_requested.connect(save)
    dialog.finished.connect(app.quit)
    if screen is not None:
        available = screen.availableGeometry()
        dialog.resize(min(880, available.width() - 40), min(610, available.height() - 80))
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
