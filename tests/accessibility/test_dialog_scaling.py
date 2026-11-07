"""PWS-0703 constrained viewport regressions for the densest editing dialogs."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QScrollArea

from paperworkshell.ui.checklist import ChecklistEditorDialog
from paperworkshell.ui.constraints import ConstraintEditorDialog
from paperworkshell.ui.overlay_tools import CoreOverlayToolsDialog
from paperworkshell.ui.redaction_tools import RedactionToolsDialog
from tests.accessibility.test_checklist_accessibility import (
    _application,
    _empty_state,
)
from tests.accessibility.test_checklist_accessibility import _dialog as _checklist_dialog
from tests.accessibility.test_constraint_editor_accessibility import _dialog as _constraint_dialog
from tests.accessibility.test_core_overlay_tools_accessibility import _dialog as _overlay_dialog
from tests.accessibility.test_redaction_tools_accessibility import _dialog as _removal_dialog

type _EditorDialog = (
    ChecklistEditorDialog | ConstraintEditorDialog | CoreOverlayToolsDialog | RedactionToolsDialog
)

_DIALOGS: dict[str, tuple[Callable[[], AbstractContextManager[_EditorDialog]], str]] = {
    "overlays": (_overlay_dialog, "coreOverlayToolsScrollArea"),
    "checklist": (lambda: _checklist_dialog(_empty_state()), "checklistEditorScrollArea"),
    "constraints": (_constraint_dialog, "constraintEditorScrollArea"),
    "removal": (_removal_dialog, "redactionToolsScroll"),
}


@pytest.mark.parametrize("kind", tuple(_DIALOGS))
@pytest.mark.parametrize("point_size", (9, 18))
def test_editing_dialogs_keep_complete_footer_inside_constrained_viewport(
    kind: str, point_size: int
) -> None:
    """A native footer cannot force critical actions beyond the available width.

    The 18-point run doubles the normal nine-point Windows text size. It is a
    text-enlargement regression, separate from the real DPI-process scenarios.
    """
    app = _application()
    original_font = QFont(app.font())
    enlarged = QFont(original_font)
    enlarged.setPointSize(point_size)
    app.setFont(enlarged)
    try:
        factory, scroll_name = _DIALOGS[kind]
        with factory() as dialog:
            dialog.resize(640, 420)
            app.processEvents()
            assert dialog.width() <= 640
            assert dialog.height() <= 420
            scroll = dialog.findChild(QScrollArea, scroll_name)
            assert scroll is not None and scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            if point_size == 9:
                # Page canvases retain their own deliberate pan/zoom scrolling;
                # ordinary form controls must fit without horizontal panning.
                assert scroll.horizontalScrollBar().maximum() == 0
            footer = dialog.findChild(QDialogButtonBox)
            assert footer is not None
            visible = [button for button in footer.buttons() if button.isVisible()]
            for index, button in enumerate(visible):
                assert footer.rect().contains(button.geometry())
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
                assert (
                    button.width()
                    >= max(
                        button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                        for line in button.text().splitlines()
                    )
                    + 12
                )
                for other in visible[index + 1 :]:
                    assert not button.geometry().intersects(other.geometry())
    finally:
        app.setFont(original_font)


@pytest.mark.parametrize("kind", tuple(_DIALOGS))
def test_resizing_editor_reflows_controls_without_changing_document_intent(kind: str) -> None:
    app = _application()
    factory, scroll_name = _DIALOGS[kind]
    with factory() as dialog:
        state = dialog.state
        for width in (1000, 640, 900):
            dialog.resize(width, 420)
            app.processEvents()
            assert dialog.state is state
            scroll = dialog.findChild(QScrollArea, scroll_name)
            assert scroll is not None
            assert scroll.horizontalScrollBar().maximum() == 0
