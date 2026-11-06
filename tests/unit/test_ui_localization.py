"""Localized presentation keeps canonical security facts and user values intact."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QRadioButton

from paperworkshell.app.core_overlay_tools import (
    is_core_overlay_announcement_safe,
    overlay_editor_record_summary,
)
from paperworkshell.app.organizer import OrganizerPageView
from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DELETE_KEEP_WORDING,
    SIGNATURE_IMAGE_DELETE_UNUSED_WORDING,
    SIGNATURE_IMAGE_DISCLAIMER,
    SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION,
)
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.localization.catalog import localized, tr
from paperworkshell.overlays.models import OverlayKind, TextualOverlayPayload
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.checklist import ChecklistRequirementListModel, _count_text
from paperworkshell.ui.compression_review import CompressionReviewDialog, _page_changes
from paperworkshell.ui.localized_messages import (
    localized_overlay_announcement,
    localized_overlay_record_summary,
)
from paperworkshell.ui.models import _page_edit_description
from paperworkshell.ui.overlay_tools import _percent_text
from paperworkshell.ui.package_export import PackageExportDialog
from tests.accessibility.test_checklist_accessibility import _dialog as checklist_dialog
from tests.accessibility.test_checklist_accessibility import _empty_state as checklist_state
from tests.accessibility.test_constraint_editor_accessibility import _dialog as constraint_dialog
from tests.accessibility.test_constraint_editor_accessibility import _state as constraint_state
from tests.accessibility.test_redaction_tools_accessibility import _dialog as removal_dialog
from tests.helpers.compression_review import compression_review
from tests.unit.test_overlay_tools_dialog import _application, _state
from tests.unit.test_signature_image_dialogs import _delete_dialog, _placement_dialog


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_overlay_presentation_translates_after_canonical_validation(locale: str) -> None:
    canary = "SYNTHETIC-PRIVATE-OVERLAY-VALUE-0704"
    state = _state().create(OverlayKind.TEXT, value=canary)
    source = state.announcement
    record = state.records[0]
    assert is_core_overlay_announcement_safe(source)
    with localized(locale):
        translated = localized_overlay_announcement(source)
        summary = localized_overlay_record_summary(record, 1, 1)
        assert translated != source
        assert summary != overlay_editor_record_summary(record, 1, 1)
        assert canary not in summary
        assert canary not in translated
    assert state.announcement == source
    assert is_core_overlay_announcement_safe(state.announcement)
    assert isinstance(record.payload, TextualOverlayPayload)
    assert record.payload.value == canary


def test_unknown_overlay_announcement_fails_before_translation() -> None:
    with localized("ar-XB"), pytest.raises(ValueError, match="approved vocabulary"):
        localized_overlay_announcement("SYNTHETIC-UNTRUSTED-ANNOUNCEMENT-0704")


def test_compression_display_keeps_exact_bytes_alongside_iec_size() -> None:
    review = compression_review()
    with localized("en"):
        text = CompressionReviewDialog._review_text(review)
        assert "Current verified total: 20,000 bytes (19.5 KiB)" in text
    with localized("ar-XB"):
        translated = CompressionReviewDialog._review_text(review)
        assert "٢٠٬٠٠٠" in translated
        assert "١٩٫٥ KiB" in translated
    assert review.current_total_bytes == 20_000


# The following regressions exercise complete displayed messages and leave the
# validated application state and document values in their canonical form.


@pytest.mark.parametrize(
    "value,english,arabic", ((123456, "12.3456", "١٢٫٣٤٥٦"), (100, "0.0100", "٠٫٠١٠٠"))
)
def test_editor_percent_displays_keep_one_localized_numeric_value(
    value: int, english: str, arabic: str
) -> None:
    state = _state().create(OverlayKind.TEXT, value="SYNTHETIC-PAYLOAD-0704")
    record = replace(state.records[0], box=NormalizedPageBox(value, value, 100000, 100000))
    with localized("en"):
        assert _percent_text(value) == english + " percent"
    with localized("ar-XB"):
        assert arabic in _percent_text(value)
        assert arabic in localized_overlay_record_summary(record, 1, 1)
    assert record.box.x == value
    assert isinstance(record.payload, TextualOverlayPayload)
    assert record.payload.value == "SYNTHETIC-PAYLOAD-0704"


def test_page_geometry_display_localizes_fractional_adjustments_without_mutating_intent() -> None:
    intent = ImageTransformIntent(
        deskew_angle_millidegrees_clockwise=1234,
        contrast_factor_millionths=1234500,
        background_cleanup_strength_millionths=678900,
    )
    page = OrganizerPageView(
        UUID("30000000-0000-4000-8000-000000000001"),
        1,
        0,
        True,
        scan_review_intent=intent,
        crop=NormalizedPageBox(123450, 234560, 500000, 500000),
    )
    original = intent.canonical_bytes()
    with localized("en"):
        english = _page_edit_description(page)
        assert "left 12.345 percent" in english
        assert "deskew 1.234 degrees" in english
    with localized("ar-XB"):
        translated = _page_edit_description(page)
        for number in ("١٢٫٣٤٥", "٢٣٫٤٥٦", "١٫٢٣٤", "١٢٣٫٤٥", "٦٧٫٨٩"):
            assert number in translated
    assert intent.canonical_bytes() == original


def test_compression_image_ranges_and_dpi_are_locale_numbers() -> None:
    review = compression_review()
    page = review.affected_pages[0]
    image = page.images[0]
    changed = replace(image, original_width_pixels=1235, original_effective_dpi_x_milli=123456)
    page = replace(page, images=(image, changed))
    with localized("en"):
        english = _page_changes(page)
        assert "1200–1235" in english
        assert "123.456–400" in english
    with localized("ar-XB"):
        translated = _page_changes(page)
        assert "١٢٠٠–١٢٣٥" in translated
        assert "١٢٣٫٤٥٦–٤٠٠" in translated
    assert page.images[0].original_width_pixels == 1200
    assert page.images[1].original_effective_dpi_x_milli == 123456


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_signature_review_and_retention_wording_is_translated(locale: str) -> None:
    with localized(locale), _placement_dialog() as dialog:
        disclaimer = dialog.findChild(QLabel, "signatureImageDisclaimer")
        assert disclaimer is not None
        assert disclaimer.text() == tr(SIGNATURE_IMAGE_DISCLAIMER)
        assert disclaimer.text() != SIGNATURE_IMAGE_DISCLAIMER
        assert tr(SIGNATURE_IMAGE_DISCLAIMER) in disclaimer.accessibleName()
        checkbox = dialog.findChild(QCheckBox, "signatureImageBackgroundConfirmation")
        label = dialog.findChild(QLabel, "signatureImageBackgroundConfirmationLabel")
        assert checkbox is not None and label is not None
        assert checkbox.accessibleName() == tr(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION)
        assert label.text() == tr(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION)
    with localized(locale), _delete_dialog() as dialog:
        for name, source in (
            ("keepSignatureImageAssetRadio", SIGNATURE_IMAGE_DELETE_KEEP_WORDING),
            ("removeUnusedSignatureImageAssetRadio", SIGNATURE_IMAGE_DELETE_UNUSED_WORDING),
        ):
            radio = dialog.findChild(QRadioButton, name)
            label = dialog.findChild(QLabel, name + "Label")
            assert radio is not None and label is not None
            assert radio.accessibleName() == tr(source)
            assert label.text() == tr(source)


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_removal_transition_translates_canonical_announcement_before_composition(
    locale: str,
) -> None:
    with localized(locale), removal_dialog() as dialog:
        dialog._add_button.click()
        _application().processEvents()
        source = dialog.state.announcement
        status = dialog.findChild(QLabel, "redactionToolsStatus")
        assert status is not None
        assert tr(source) in status.text()
        assert source not in status.text()
        assert dialog.state.marks


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_package_and_compression_progress_use_translated_phase_prose(locale: str) -> None:
    _application()
    with localized(locale):
        package = PackageExportDialog(tuple(PortalOutputMode))
        compression = CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1)
        try:
            for dialog, source in (
                (package, "Preparing package review"),
                (compression, "Preparing and verifying a private candidate"),
            ):
                dialog.set_busy("preparing")
                assert tr(source) in dialog._status.text()
                assert source not in dialog._status.text()
                assert dialog.set_progress("preparing", 1, 3)
                assert tr(source) in dialog._status.text()
                assert source not in dialog._status.text()
        finally:
            package.hide()
            compression.finish_operation()
            compression.hide()
            package.deleteLater()
            compression.deleteLater()
            _application().processEvents()


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_builtin_template_requirement_and_source_guidance_is_translated(locale: str) -> None:
    with localized("en"):
        state = checklist_state()
        constraints = constraint_state()
    requirement = state.requirements[0]
    with localized(locale), checklist_dialog(state) as dialog:
        guidance = dialog.findChild(QLabel, "checklistRequirementGuidance")
        assert guidance is not None
        assert tr(requirement.display_name) in guidance.text()
        assert tr(requirement.description) in guidance.text()
        assert requirement.display_name not in guidance.text()
        model = ChecklistRequirementListModel()
        model.replace(state.requirements, frozenset())
        display = model.data(model.index(0, 0), int(Qt.ItemDataRole.DisplayRole))
        assert isinstance(display, str)
        assert tr(requirement.display_name) in display
        assert tr("documents") in _count_text(requirement)
    with localized(locale), constraint_dialog(constraints) as dialog:
        source = dialog.findChild(QLabel, "constraintSourceStatus")
        title = dialog.findChild(QLabel, "constraintTemplateTitle")
        assert source is not None and title is not None
        assert tr(constraints.source.heading) in source.text()
        assert tr(constraints.source.detail) in source.text()
        assert constraints.source.heading not in source.text()
        assert tr(constraints.source.template_title) in title.text()
        assert dialog.state.source == constraints.source


@pytest.mark.parametrize("locale", ("en", "en-XA", "ar-XB"))
def test_highlight_keeps_its_kind_name_when_its_payload_uses_rectangle_geometry(
    locale: str,
) -> None:
    from paperworkshell.ui.localized_messages import localized_overlay_record_name
    from tests.unit.test_overlay_editor import PAGE_THREE
    from tests.unit.test_overlay_editor import _state as released_overlay_state

    record = next(
        record
        for record in released_overlay_state(PAGE_THREE).editor_records
        if record.kind is OverlayKind.HIGHLIGHT
    )
    with localized(locale):
        name = localized_overlay_record_name(record)
        if locale == "en":
            assert name == "Rectangle highlight visual mark"
        else:
            assert "híghlíght" in name.casefold()
            assert "šhápé" not in name.casefold()
    assert record.kind is OverlayKind.HIGHLIGHT


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
@pytest.mark.parametrize("selected", (False, True))
def test_coordinate_accessible_description_does_not_accumulate_translated_history(
    locale: str,
    selected: bool,
) -> None:
    from tests.unit.test_overlay_tools_dialog import _dialog as overlay_dialog

    state = (
        _state().create(OverlayKind.TEXT, value="SYNTHETIC-FORM-VALUE-0704")
        if selected
        else _state()
    )
    with localized(locale), overlay_dialog(state) as dialog:
        before = dialog._left.accessibleDescription()
        for _ in range(20):
            dialog._load_selected_properties()
        assert dialog._left.accessibleDescription() == before
        assert len(dialog._left.accessibleDescription()) < 1000
        assert dialog.state.history is state.history


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_overlay_guide_descriptions_translate_the_spacing_phrase(locale: str) -> None:
    from tests.unit.test_overlay_tools_dialog import _dialog as overlay_dialog

    state = _state().create(OverlayKind.TEXT, value="SYNTHETIC-FORM-VALUE-0704")
    with localized(locale), overlay_dialog(state) as dialog:
        dialog._guide_options_changed()
        assert tr("5 percent") in dialog._show_guides.accessibleDescription()
        dialog._snap_selected()
        assert tr("5 percent") in dialog._snap_feedback.text()
        assert dialog._current_guide_step() == 50000


def test_removal_list_localizes_percentages_without_changing_mark_geometry() -> None:
    box = NormalizedPageBox(123450, 234560, 100000, 200000)
    with localized("ar-XB"), removal_dialog() as dialog:
        dialog._publish(dialog.state.create(box))
        text = dialog._list.item(0).text()
        assert "١٢٫٣٤٥" in text
        assert "٢٣٫٤٥٦" in text
        assert dialog.state.marks[0].box == box
        assert dialog._list.item(0).data(Qt.ItemDataRole.UserRole) == dialog.state.marks[0].mark_id


def test_painted_overlay_grid_localizes_its_numeric_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPixmap

    from paperworkshell.domain.page_coordinates import PageCoordinateTransform
    from paperworkshell.ui import overlay_canvas

    _application()
    drawn: list[str] = []
    original = tr

    def translated(source: str, **values: object) -> str:
        text = original(source, **values)
        if source.startswith("Dashed guides:"):
            drawn.append(text)
        return text

    monkeypatch.setattr(overlay_canvas, "tr", translated)
    with localized("ar-XB"):
        canvas = overlay_canvas.OverlayEditorCanvas(transform=PageCoordinateTransform())
        canvas.set_guide_options(visible=True, snap=False, step_millionths=25000)
        pixmap = QPixmap(680, 800)
        pixmap.fill(Qt.GlobalColor.white)
        painter = QPainter(pixmap)
        try:
            canvas._paint_guides(painter, QRectF(0, 0, 600, 780))
        finally:
            painter.end()
            canvas.deleteLater()
        assert drawn and "٢٫٥" in drawn[-1]
        assert canvas._guide_step == 25000


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_recovery_messages_translate_before_adding_the_stable_code(locale: str) -> None:
    from paperworkshell.app.checklist import ChecklistEditorState
    from paperworkshell.domain.checklist import ChecklistError, ChecklistErrorCode
    from paperworkshell.domain.package_export_errors import (
        PackageExportError,
        PackageExportErrorCode,
    )

    error = ChecklistError(ChecklistErrorCode.ASSIGNMENT_UNKNOWN, "checklist.test")

    def fail(_state: ChecklistEditorState) -> ChecklistEditorState:
        raise error

    with localized(locale), checklist_dialog(checklist_state()) as dialog:
        assert not dialog._change_state(fail, "")
        assert error.code.value in dialog._status.text()
        assert tr(error.safe_message) in dialog._status.text()
        assert error.safe_message not in dialog._status.text()
    with localized(locale):
        package = PackageExportDialog(tuple(PortalOutputMode))
        package_error = PackageExportError(PackageExportErrorCode.PLAN_INVALID, "package.test")
        try:
            package.show_failure(code=package_error.code.value)
            assert tr(package_error.safe_message) in package._status.text()
            assert package_error.safe_message not in package._status.text()
        finally:
            package.deleteLater()
            _application().processEvents()


def test_dense_removal_legend_reserves_the_height_of_localized_visible_text() -> None:
    from PySide6.QtCore import QRect

    from tests.accessibility.test_localization_layout import _display_locale

    with _display_locale("ar-XB", 18), removal_dialog() as dialog:
        state = dialog.state
        for _ in range(20):
            state = state.create(NormalizedPageBox(0, 0, 1000000, 1000000))
        dialog._publish(state)
        canvas = dialog._canvas
        for width in (320, 360, 440):
            canvas.resize(width, 760)
            canvas._ensure_projection()
            assert canvas._dense_preview
            required = (
                canvas.fontMetrics()
                .boundingRect(
                    QRect(0, 0, canvas.width() - 8, 100000),
                    int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                    canvas._legend_text(),
                )
                .height()
            )
            assert canvas._legend_height() >= required + 8


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
def test_checklist_failure_support_codes_remain_ascii_in_localized_messages(locale: str) -> None:
    from typing import cast

    from paperworkshell.app.checklist import ChecklistEditorState

    def invalid(_state: ChecklistEditorState) -> ChecklistEditorState:
        return cast(ChecklistEditorState, object())

    state = checklist_state()
    with localized(locale), checklist_dialog(state) as dialog:
        assert not dialog._change_state(invalid, "")
        assert "PWS_CHECKLIST_ACTION_FAILED:" in dialog._status.text()
        assert "PaperWorkShell could not apply" not in dialog._status.text()
    state = state.add_pages(state.requirements[0].requirement_id, (state.pages[0].page_id,))
    with localized(locale), checklist_dialog(state) as dialog:
        dialog._request_apply()
        assert dialog.apply_pending
        assert dialog.mark_apply_failed("PWS_TEST_FAILURE", "IGNORED-PRIVATE-FAILURE-0704")
        assert "PWS_CHECKLIST_APPLY_FAILED:" in dialog._status.text()
        assert "PaperWorkShell could not save" not in dialog._status.text()
        assert "IGNORED-PRIVATE-FAILURE-0704" not in dialog._status.text()


@pytest.mark.parametrize("blocked", (False, True))
def test_compression_decision_prose_is_localized_in_the_complete_summary(blocked: bool) -> None:
    review = compression_review(blocked=blocked)
    source = review.decision.message
    with localized("ar-XB"):
        assert tr(source) != source
        text = CompressionReviewDialog._review_text(review)
        assert tr(source) in text
        assert source not in text
    assert review.export_allowed is not blocked
