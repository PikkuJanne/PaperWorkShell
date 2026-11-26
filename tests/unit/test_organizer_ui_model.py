from __future__ import annotations

import os
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformIntent,
)
from paperworkshell.domain.quality_analysis import (
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.ui.models import (
    OrganizerPageListModel,
    PreviewState,
    QualityFindingListModel,
    QualityReviewState,
)
from paperworkshell.ui.window import _bounded_preview_rows

CASE_ID = UUID("70000000-0000-4000-8000-000000000001")
OTHER_CASE_ID = UUID("70000000-0000-4000-8000-000000000002")
PAGE_ONE = UUID("71000000-0000-4000-8000-000000000001")
PAGE_TWO = UUID("71000000-0000-4000-8000-000000000002")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _state(*pages: OrganizerPageView) -> OrganizerViewState:
    return OrganizerViewState(CASE_ID, 2, pages, True, False, False)


def test_model_exposes_order_rotation_and_textual_preview_accessibility_roles() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            OrganizerPageView(PAGE_TWO, 2, 270, False),
        )
    )

    first = model.index(0, 0)
    second = model.index(1, 0)
    assert model.rowCount() == 2
    assert model.data(first, int(Qt.ItemDataRole.DisplayRole)) == (
        "Page 1 of 2\n0\N{DEGREE SIGN} rotation\nPreview not loaded\n"
        "Quality review not checked for this saved revision"
    )
    assert model.data(first, int(Qt.ItemDataRole.AccessibleTextRole)) == (
        "Page 1 of 2; rotated 0 degrees; preview not loaded; "
        "quality review not checked for this saved revision"
    )
    assert model.data(second, model.PreviewStateRole) == PreviewState.UNAVAILABLE.value
    assert model.data(second, model.PageIdRole) == PAGE_TWO
    assert model.flags(first) == Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    assert not model.flags(first) & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.flags(first) & Qt.ItemFlag.ItemIsDropEnabled


def test_quality_snapshot_attaches_by_page_id_without_mutating_or_evicting_previews() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            OrganizerPageView(PAGE_TWO, 2, 0, True),
        )
    )
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    model.set_preview(PAGE_ONE, QPixmap.fromImage(image))
    retained_preview = model.preview(PAGE_ONE)
    assert retained_preview is not None
    assert retained_preview.size() == image.size()
    assert model.preview(PAGE_TWO) is None
    finding = QualityFinding(
        page_id=PAGE_TWO,
        page_position=2,
        code=QualityFindingCode.LIKELY_BLUR,
        evidence_kind=QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
        observed_value=10,
        threshold_value=20,
    )
    snapshot = QualityReviewSnapshot(
        CASE_ID,
        2,
        (PAGE_ONE, PAGE_TWO),
        (finding,),
        "scan-quality-v1",
        "a" * 64,
    )

    assert model.set_quality_review(snapshot)
    assert model.quality_review == snapshot
    assert model.retained_preview_count == 1
    assert (
        model.data(model.index(0, 0), model.QualityReviewStateRole)
        == QualityReviewState.NO_SIGNALS.value
    )
    assert model.data(model.index(1, 0), model.QualityFindingCountRole) == 1
    assert "Needs review: 1 quality signal" in str(
        model.data(model.index(1, 0), int(Qt.ItemDataRole.DisplayRole))
    )

    findings = QualityFindingListModel()
    findings.set_snapshot(snapshot)
    finding_index = findings.index(0, 0)
    assert findings.rowCount() == 1
    assert findings.data(finding_index, findings.PageIdRole) == PAGE_TWO
    assert findings.data(finding_index, findings.FindingCodeRole) == "likely_blur"
    assert "Page 2 — Needs review: Likely blur" in str(
        findings.data(finding_index, int(Qt.ItemDataRole.AccessibleTextRole))
    )
    assert findings.flags(finding_index) == (
        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    )

    model.set_organizer_state(OrganizerViewState(CASE_ID, 2, model.pages, True, False, True))
    assert (
        model.data(model.index(1, 0), model.QualityReviewStateRole)
        == QualityReviewState.NOT_CHECKED.value
    )
    assert model.retained_preview_count == 1


def test_model_detaches_previews_and_discards_removed_page_state() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(
        _state(
            OrganizerPageView(PAGE_ONE, 1, 0, True),
            OrganizerPageView(PAGE_TWO, 2, 0, True),
        )
    )
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    preview = QPixmap.fromImage(image.copy())
    model.set_preview(PAGE_ONE, preview)

    assert model.data(model.index(0, 0), model.PreviewStateRole) == PreviewState.READY.value
    assert isinstance(model.data(model.index(0, 0), int(Qt.ItemDataRole.DecorationRole)), QPixmap)

    model.set_organizer_state(_state(OrganizerPageView(PAGE_TWO, 1, 90, True)))

    assert model.rowCount() == 1
    assert model.row_for_page(PAGE_ONE) is None
    assert model.page_id_at(0) == PAGE_TWO
    assert model.data(model.index(0, 0), model.PreviewStateRole) == PreviewState.NOT_LOADED.value


def test_case_change_discards_preview_when_page_identifier_is_reused() -> None:
    _application()
    page = OrganizerPageView(PAGE_ONE, 1, 0, True)
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(page))
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("darkred"))
    model.set_preview(PAGE_ONE, QPixmap.fromImage(image))
    assert model.preview(PAGE_ONE) is not None
    assert model.retained_preview_count == 1
    assert model.retained_preview_bytes > 0

    model.set_organizer_state(OrganizerViewState(OTHER_CASE_ID, 2, (page,), True, False, False))

    index = model.index(0, 0)
    assert model.page_id_at(0) == PAGE_ONE
    assert model.preview(PAGE_ONE) is None
    assert model.data(index, model.PreviewStateRole) == PreviewState.NOT_LOADED.value
    assert model.data(index, int(Qt.ItemDataRole.DecorationRole)) is None
    assert model.retained_preview_count == 0
    assert model.retained_preview_bytes == 0


def test_rotation_invalidates_stale_pixels_before_autosave_preview_suppression() -> None:
    _application()
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 0, True)))
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    model.set_preview(PAGE_ONE, QPixmap.fromImage(image))

    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 90, True)))

    index = model.index(0, 0)
    assert model.data(index, model.PreviewStateRole) == PreviewState.NOT_LOADED.value
    assert model.data(index, int(Qt.ItemDataRole.DecorationRole)) is None
    # The stale orientation is retained for undo, never exposed as the current image.
    assert model.retained_preview_count == 1


def test_any_persisted_scan_transform_invalidates_stale_image_pixels() -> None:
    _application()
    base = ImageTransformIntent()
    adjusted = ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 120_000, 700_000, 650_000),
        deskew_angle_millidegrees_clockwise=1_250,
        contrast_factor_millionths=1_150_000,
        background_cleanup_strength_millionths=200_000,
    )
    model = OrganizerPageListModel()
    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 0, True, base)))
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    model.set_preview(PAGE_ONE, QPixmap.fromImage(image))

    model.set_organizer_state(_state(OrganizerPageView(PAGE_ONE, 1, 0, True, adjusted)))

    index = model.index(0, 0)
    assert model.data(index, model.PreviewStateRole) == PreviewState.NOT_LOADED.value
    assert model.data(index, int(Qt.ItemDataRole.DecorationRole)) is None
    assert model.retained_preview_count == 0


def test_lru_eviction_and_terminal_loading_states_remain_truthful() -> None:
    _application()
    image = QImage(8, 12, QImage.Format.Format_RGBA8888)
    image.fill(QColor("white"))
    preview = QPixmap.fromImage(image)
    preview_bytes = preview.width() * preview.height() * ((preview.depth() + 7) // 8)
    model = OrganizerPageListModel(maximum_preview_cache_bytes=preview_bytes * 2)
    pages = tuple(
        OrganizerPageView(
            UUID(f"71000000-0000-4000-8000-{number:012d}"),
            number,
            0,
            True,
        )
        for number in range(1, 5)
    )
    model.set_organizer_state(_state(*pages))
    for page in pages[:3]:
        model.set_preview(page.page_id, preview.copy())
    model.touch_previews((pages[0].page_id,))

    assert model.preview_state(pages[0].page_id) is PreviewState.NOT_LOADED
    assert model.retained_preview_count == 2
    assert model.retained_preview_bytes == preview_bytes * 2
    assert model.maximum_preview_cache_bytes == preview_bytes * 2

    model.touch_previews((pages[1].page_id,))
    model.set_preview(pages[3].page_id, preview.copy())

    assert model.preview_state(pages[2].page_id) is PreviewState.NOT_LOADED
    assert model.preview_state(pages[1].page_id) is PreviewState.READY
    assert model.preview_state(pages[3].page_id) is PreviewState.READY

    evicted = model.evict_previews(
        preview_bytes,
        protected_page_ids=(pages[3].page_id,),
    )
    assert evicted == (pages[1].page_id,)
    assert model.retained_preview_count == 1

    model.set_preview_loading((pages[3].page_id,))
    model.finish_preview_loading((pages[3].page_id,))
    assert model.preview_state(pages[3].page_id) is PreviewState.NOT_LOADED
    model.set_preview_loading((pages[3].page_id,))
    model.set_preview_failed(pages[3].page_id)
    model.finish_preview_loading((pages[3].page_id,))
    assert model.preview_state(pages[3].page_id) is PreviewState.FAILED
    assert model.reset_failed_preview(pages[3].page_id)
    assert model.preview_state(pages[3].page_id) is PreviewState.NOT_LOADED
    assert not model.reset_failed_preview(pages[3].page_id)


def test_constrained_preview_window_prefetches_at_most_one_adjacent_row() -> None:
    policy = CONSTRAINED_RESOURCE_POLICY_V1

    rows = _bounded_preview_rows(
        20,
        (8, 9),
        9,
        overscan_rows=policy.preview_prefetch_radius_pages,
        maximum_count=policy.maximum_preview_request_pages,
    )

    assert {8, 9}.issubset(rows)
    assert set(rows).issubset({7, 8, 9, 10})
    assert len(rows) <= policy.maximum_preview_request_pages
