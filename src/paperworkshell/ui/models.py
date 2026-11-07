"""Qt presentation models for path-free organizer state."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QPixmap

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.domain.image_pages import ImageQuarterTurn
from paperworkshell.domain.image_transforms import (
    IDENTITY_CONTRAST_FACTOR_MILLIONTHS,
    ImageTransformIntent,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.quality_analysis import QualityFinding
from paperworkshell.domain.resource_policy import NORMAL_RESOURCE_POLICY_V1
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.models import OverlayKind, OverlayVisibility


class PreviewState(StrEnum):
    """Visible and accessible state of a disposable page preview."""

    NOT_LOADED = mark("not loaded")
    LOADING = mark("loading")
    READY = mark("ready")
    FAILED = mark("failed")
    UNAVAILABLE = mark("unavailable")


class QualityReviewState(StrEnum):
    """Visible advisory state for one page in an exact saved revision."""

    NOT_CHECKED = mark("not checked")
    NO_SIGNALS = mark("no review signals from limited checks")
    NEEDS_REVIEW = mark("needs review")


_PAGE_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1
_PREVIEW_STATE_ROLE: Final = _PAGE_ID_ROLE + 1
_QUALITY_REVIEW_STATE_ROLE: Final = _PREVIEW_STATE_ROLE + 1
_QUALITY_FINDING_COUNT_ROLE: Final = _QUALITY_REVIEW_STATE_ROLE + 1
_QUALITY_FINDING_PAGE_ID_ROLE: Final = _QUALITY_FINDING_COUNT_ROLE + 1
_QUALITY_FINDING_CODE_ROLE: Final = _QUALITY_FINDING_PAGE_ID_ROLE + 1
_QUALITY_FINDING_RELATED_PAGE_ID_ROLE: Final = _QUALITY_FINDING_CODE_ROLE + 1
_INVALID_INDEX: Final = QModelIndex()
_OVERLAY_KIND_TEXT: Final = {
    OverlayKind.TEXT: mark("text"),
    OverlayKind.DATE: mark("date"),
    OverlayKind.CHECKMARK: mark("checkmark"),
    OverlayKind.CROSS: mark("cross"),
    OverlayKind.INITIALS: mark("initials"),
    OverlayKind.SIGNATURE_IMAGE: mark("signature image"),
    OverlayKind.HIGHLIGHT: mark("highlight"),
    OverlayKind.SHAPE: mark("shape"),
}
type _PreviewKey = tuple[UUID, int, ImageTransformIntent | None, NormalizedPageBox | None]


class OrganizerPageListModel(QAbstractListModel):
    """Read-only page list driven exclusively by organizer projection state."""

    PageIdRole: Final = _PAGE_ID_ROLE
    PreviewStateRole: Final = _PREVIEW_STATE_ROLE
    QualityReviewStateRole: Final = _QUALITY_REVIEW_STATE_ROLE
    QualityFindingCountRole: Final = _QUALITY_FINDING_COUNT_ROLE

    def __init__(
        self,
        *,
        maximum_preview_cache_bytes: int = (
            NORMAL_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes
        ),
    ) -> None:
        if type(maximum_preview_cache_bytes) is not int or maximum_preview_cache_bytes <= 0:
            raise ValueError("maximum_preview_cache_bytes must be a positive integer")
        super().__init__()
        self._maximum_preview_cache_bytes = maximum_preview_cache_bytes
        self._pages: tuple[OrganizerPageView, ...] = ()
        self._previews: OrderedDict[UUID, QPixmap] = OrderedDict()
        self._preview_costs: dict[UUID, int] = {}
        self._preview_history: OrderedDict[_PreviewKey, QPixmap] = OrderedDict()
        self._preview_history_bytes = 0
        self._retained_preview_bytes = 0
        self._preview_states: dict[UUID, PreviewState] = {}
        self._quality_review: QualityReviewSnapshot | None = None
        self._quality_findings_by_page: dict[UUID, tuple[QualityFinding, ...]] = {}
        self._case_id: UUID | None = None
        self._revision: int | None = None
        self._autosave_pending = False

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_INDEX,
    ) -> int:
        """Return projected active-page count; child rows are never exposed."""

        return 0 if parent.isValid() else len(self._pages)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object:
        """Return text, accessibility metadata, identity, or a detached preview."""

        if not index.isValid() or not 0 <= index.row() < len(self._pages):
            return None
        page = self._pages[index.row()]
        preview = self._preview_states.get(
            page.page_id,
            PreviewState.NOT_LOADED if page.preview_supported else PreviewState.UNAVAILABLE,
        )
        findings = self._quality_findings_by_page.get(page.page_id, ())
        quality_state = self._quality_state(page.page_id)
        page_text = tr("Page {value1} of {value2}", value1=page.position, value2=len(self._pages))
        rotation_text = tr("rotated {value1} degrees", value1=page.rotation_degrees)
        quality_text = _page_quality_text(quality_state, len(findings))
        source_text = (
            tr(
                "{value1} — source page {value2}",
                value1=page.source_display_name,
                value2=page.source_page_number or 1,
            )
            if page.source_display_name
            else ""
            if page.document_number is None
            else tr(
                "Document {value1}, source page {value2}",
                value1=page.document_number,
                value2=page.source_page_number,
            )
        )
        overlay_count = sum(summary.count for summary in page.overlay_summaries)
        overlay_text = (
            tr(
                "; {count} visual overlay" if overlay_count == 1 else "; {count} visual overlays",
                count=overlay_count,
            )
            if overlay_count or source_text
            else ""
        )
        removal_text = (
            tr(
                "; {count} unapplied removal mark"
                if page.removal_mark_count == 1
                else "; {count} unapplied removal marks",
                count=page.removal_mark_count,
            )
            if page.removal_mark_count
            else ""
        )
        saved_mark_preview = (
            "\n"
            + tr(
                "{count} saved mark — open editor to preview"
                if overlay_count == 1
                else "{count} saved marks — open editor to preview",
                count=overlay_count,
            )
            if overlay_count
            else ""
        )
        if role == int(Qt.ItemDataRole.DisplayRole) and page.source_display_name:
            return (
                tr(
                    "{value1}\n{value2} — {value3}° rotation\nPreview {value4}",
                    value1=page.source_display_name,
                    value2=tr(
                        "Page {value1} of {value2}; source page {value3}",
                        value1=page.position,
                        value2=len(self._pages),
                        value3=page.source_page_number or 1,
                    ),
                    value3=page.rotation_degrees,
                    value4=tr(preview.value),
                )
                + saved_mark_preview
            )
        if role == int(Qt.ItemDataRole.DisplayRole):
            return (
                f"{page_text}\n"
                + (f"{source_text}\n" if source_text else "")
                + tr(
                    "{value1}° rotation\nPreview {value2}\n{value3}",
                    value1=page.rotation_degrees,
                    value2=tr(preview.value),
                    value3=quality_text,
                )
                + saved_mark_preview
            )
        if role == int(Qt.ItemDataRole.AccessibleTextRole):
            return (
                f"{page_text}"
                + (
                    f"; {source_text if page.source_display_name else source_text.lower()}"
                    if source_text
                    else ""
                )
                + tr(
                    "; {value1}; preview {value2}; {value3}",
                    value1=rotation_text,
                    value2=tr(preview.value),
                    value3=quality_text.casefold(),
                )
                + overlay_text
                + removal_text
            )
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return (
                (
                    tr(
                        "Unsaved organizer changes; saved revision {value1}. ",
                        value1=self._revision,
                    )
                    if self._autosave_pending
                    else tr("Saved revision {value1}. ", value1=self._revision)
                )
                + _page_edit_description(page)
                + tr(
                    " Use the organizer action buttons or keyboard shortcuts to change this page. Automatic quality signals are suggestions and do not complete a requirement."
                )
            )
        if role == int(Qt.ItemDataRole.DecorationRole):
            preview_pixmap = self._previews.get(page.page_id)
            return None if preview_pixmap is None else QPixmap(preview_pixmap)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return tr(
                "{value1}; {value2}; preview {value3}; {value4}.",
                value1=page_text,
                value2=f"{source_text}; {rotation_text}" if source_text else rotation_text,
                value3=tr(preview.value),
                value4=quality_text,
            )
        if role == self.PageIdRole:
            return page.page_id
        if role == self.PreviewStateRole:
            return preview.value
        if role == self.QualityReviewStateRole:
            return quality_state.value
        if role == self.QualityFindingCountRole:
            return len(findings)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """Expose keyboard selection without drag or drop capabilities."""

        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        """Name custom roles for accessibility tests and future delegates."""

        roles = super().roleNames()
        roles[self.PageIdRole] = QByteArray(b"pageId")
        roles[self.PreviewStateRole] = QByteArray(b"previewState")
        roles[self.QualityReviewStateRole] = QByteArray(b"qualityReviewState")
        roles[self.QualityFindingCountRole] = QByteArray(b"qualityFindingCount")
        return roles

    def set_organizer_state(self, state: OrganizerViewState) -> None:
        """Replace rows and discard previews invalidated by page facts."""

        case_changed = self._case_id != state.case_id
        prior_pages = {} if case_changed else {page.page_id: page for page in self._pages}
        retain_quality_review = (
            self._quality_review is not None
            and not state.autosave_pending
            and self._quality_review.case_id == state.case_id
            and self._quality_review.manifest_revision == state.revision
            and self._quality_review.page_ids == tuple(page.page_id for page in state.pages)
            and self._pages == state.pages
        )
        retained = {page.page_id for page in state.pages}
        self.beginResetModel()
        if case_changed:
            self._previews.clear()
            self._preview_costs.clear()
            self._preview_states.clear()
            self._retained_preview_bytes = 0
            self._preview_history.clear()
            self._preview_history_bytes = 0
        for key in tuple(self._preview_history):
            if key[0] not in retained:
                self._preview_history_bytes -= _preview_byte_cost(self._preview_history.pop(key))
        self._pages = state.pages
        self._case_id = state.case_id
        self._revision = state.revision
        self._autosave_pending = state.autosave_pending
        if not retain_quality_review:
            self._quality_review = None
            self._quality_findings_by_page = {}
        self._previews = OrderedDict(
            (page_id, preview) for page_id, preview in self._previews.items() if page_id in retained
        )
        self._preview_costs = {
            page_id: byte_cost
            for page_id, byte_cost in self._preview_costs.items()
            if page_id in self._previews
        }
        self._retained_preview_bytes = sum(self._preview_costs.values())
        self._preview_states = {
            page_id: preview_state
            for page_id, preview_state in self._preview_states.items()
            if page_id in retained
        }
        for page in state.pages:
            prior = prior_pages.get(page.page_id)
            preview_changed = prior is not None and (
                _preview_key(prior) != _preview_key(page)
                or prior.preview_supported != page.preview_supported
            )
            if preview_changed or not page.preview_supported:
                if (
                    prior is not None
                    and _rotation_independent_preview_content(prior)
                    == _rotation_independent_preview_content(page)
                    and page.preview_supported
                ):
                    previous = self._previews.get(page.page_id)
                    if previous is not None:
                        key = _preview_key(prior)
                        old = self._preview_history.pop(key, None)
                        if old is not None:
                            self._preview_history_bytes -= _preview_byte_cost(old)
                        self._preview_history[key] = previous
                        self._preview_history_bytes += _preview_byte_cost(previous)
                else:
                    for key in tuple(self._preview_history):
                        if key[0] == page.page_id:
                            self._preview_history_bytes -= _preview_byte_cost(
                                self._preview_history.pop(key)
                            )
                self._discard_preview(page.page_id)
                self._preview_states.pop(page.page_id, None)
                cached = self._preview_history.pop(_preview_key(page), None)
                if cached is not None:
                    cost = _preview_byte_cost(cached)
                    self._preview_history_bytes -= cost
                    self._previews[page.page_id] = cached
                    self._preview_costs[page.page_id] = cost
                    self._retained_preview_bytes += cost
                    self._preview_states[page.page_id] = PreviewState.READY
            self._preview_states.setdefault(
                page.page_id,
                PreviewState.NOT_LOADED if page.preview_supported else PreviewState.UNAVAILABLE,
            )
            if (
                self._preview_states[page.page_id] is PreviewState.READY
                and page.page_id not in self._previews
            ):
                self._preview_states[page.page_id] = PreviewState.NOT_LOADED
        self.endResetModel()
        self.evict_previews()

    def set_quality_review(self, snapshot: QualityReviewSnapshot) -> bool:
        """Attach one exact ephemeral review without changing page or preview state."""

        if type(snapshot) is not QualityReviewSnapshot:
            return False
        if (
            self._case_id is None
            or self._autosave_pending
            or snapshot.case_id != self._case_id
            or snapshot.manifest_revision != self._revision
            or snapshot.page_ids != tuple(page.page_id for page in self._pages)
        ):
            return False
        findings_by_page: dict[UUID, list[QualityFinding]] = {
            page.page_id: [] for page in self._pages
        }
        for finding in snapshot.findings:
            try:
                findings_by_page[finding.page_id].append(finding)
            except KeyError:
                return False
        self._quality_review = snapshot
        self._quality_findings_by_page = {
            page_id: tuple(findings) for page_id, findings in findings_by_page.items()
        }
        self._emit_quality_changed()
        return True

    def clear_quality_review(self) -> bool:
        """Remove advisory results while retaining every disposable preview."""

        if self._quality_review is None and not self._quality_findings_by_page:
            return False
        self._quality_review = None
        self._quality_findings_by_page = {}
        self._emit_quality_changed()
        return True

    @property
    def quality_review(self) -> QualityReviewSnapshot | None:
        """Return the accepted revision-bound review, if one is currently visible."""

        return self._quality_review

    def set_preview_loading(self, page_ids: tuple[UUID, ...]) -> None:
        """Mark supported rows as loading using visible text as well as model state."""

        for page_id in page_ids:
            self._set_preview_state(page_id, PreviewState.LOADING, None)

    def set_preview(self, page_id: UUID, preview: QPixmap) -> None:
        """Attach one already detached GUI-thread pixmap to its matching page."""

        self._set_preview_state(page_id, PreviewState.READY, preview)
        self.evict_previews()

    def set_preview_unavailable(self, page_id: UUID) -> None:
        """Show a textual unavailable state and discard any prior pixels."""

        self._set_preview_state(page_id, PreviewState.UNAVAILABLE, None)

    def set_preview_failed(self, page_id: UUID) -> None:
        """Show a textual failed state without automatically retrying it."""

        self._set_preview_state(page_id, PreviewState.FAILED, None)

    def reset_failed_preview(self, page_id: UUID) -> bool:
        """Return one failed row to not-loaded state after an explicit retry action."""

        if self._preview_states.get(page_id) is not PreviewState.FAILED:
            return False
        self._set_preview_state(page_id, PreviewState.NOT_LOADED, None)
        return True

    def finish_preview_loading(self, page_ids: tuple[UUID, ...]) -> None:
        """Return unfinished requested rows to an honest on-demand state."""

        for page_id in page_ids:
            if self._preview_states.get(page_id) is PreviewState.LOADING:
                self._set_preview_state(page_id, PreviewState.NOT_LOADED, None)

    def touch_previews(self, page_ids: tuple[UUID, ...]) -> None:
        """Refresh retained-preview recency without changing visible state."""

        for page_id in page_ids:
            if page_id in self._previews:
                self._previews.move_to_end(page_id)

    def evict_previews(
        self,
        maximum_bytes: int | None = None,
        *,
        protected_page_ids: tuple[UUID, ...] = (),
    ) -> tuple[UUID, ...]:
        """Evict old orientations first, then LRU current previews under one byte cap.

        Current visible pixels are more useful than historical undo orientations.
        Protected current pages are evicted only when no other current page can
        satisfy the cap. A zero budget always releases every retained pixmap.
        """

        if maximum_bytes is None:
            byte_budget = self._maximum_preview_cache_bytes
        elif type(maximum_bytes) is int and maximum_bytes >= 0:
            byte_budget = min(maximum_bytes, self._maximum_preview_cache_bytes)
        else:
            raise ValueError("maximum_bytes must be a non-negative integer")
        protected = frozenset(protected_page_ids)
        evicted: list[UUID] = []
        while (
            self._preview_history
            and self._retained_preview_bytes + self._preview_history_bytes > byte_budget
        ):
            _key, preview = self._preview_history.popitem(last=False)
            self._preview_history_bytes -= _preview_byte_cost(preview)
        while self._retained_preview_bytes > byte_budget:
            candidate = next(
                (page_id for page_id in self._previews if page_id not in protected),
                None,
            )
            if candidate is None:
                candidate = next(iter(self._previews))
            self._set_preview_state(candidate, PreviewState.NOT_LOADED, None)
            evicted.append(candidate)
        return tuple(evicted)

    def preview_state(self, page_id: UUID) -> PreviewState | None:
        """Return one page's current presentation state."""

        return self._preview_states.get(page_id)

    def preview(self, page_id: UUID) -> QPixmap | None:
        """Return a copy-on-write wrapper for the bounded current page pixels."""

        if type(page_id) is not UUID or self._preview_states.get(page_id) is not PreviewState.READY:
            return None
        preview_pixmap = self._previews.get(page_id)
        return None if preview_pixmap is None else QPixmap(preview_pixmap)

    @property
    def retained_preview_count(self) -> int:
        """Return the number of GUI-memory pixmaps currently retained."""

        return len(self._previews) + len(self._preview_history)

    @property
    def retained_preview_bytes(self) -> int:
        """Return the deterministic decoded-pixel cost charged to the LRU."""

        return self._retained_preview_bytes + self._preview_history_bytes

    @property
    def maximum_preview_cache_bytes(self) -> int:
        """Return the immutable upper bound applied by this model."""

        return self._maximum_preview_cache_bytes

    def page_id_at(self, row: int) -> UUID | None:
        """Return a row's internal page identifier without exposing source details."""

        if not 0 <= row < len(self._pages):
            return None
        return self._pages[row].page_id

    def row_for_page(self, page_id: UUID) -> int | None:
        """Return the current row for one internal page identifier."""

        for row, page in enumerate(self._pages):
            if page.page_id == page_id:
                return row
        return None

    @property
    def pages(self) -> tuple[OrganizerPageView, ...]:
        """Return the immutable projected rows for keyboard action construction."""

        return self._pages

    def _set_preview_state(
        self,
        page_id: UUID,
        preview_state: PreviewState,
        preview: QPixmap | None,
    ) -> None:
        row = self.row_for_page(page_id)
        if row is None:
            return
        self._discard_preview(page_id)
        if preview is None:
            self._preview_states[page_id] = preview_state
        else:
            byte_cost = _preview_byte_cost(preview)
            self._preview_states[page_id] = PreviewState.NOT_LOADED
            if preview.isNull() or byte_cost > self._maximum_preview_cache_bytes:
                self._preview_states[page_id] = PreviewState.FAILED
            else:
                # Make room before observers receive READY/dataChanged. A copy
                # of the wrapper shares Qt's buffer until either owner mutates
                # it; retaining the caller's mutable wrapper would corrupt our
                # pixels and accounting if the caller resized or filled it.
                self.evict_previews(self._maximum_preview_cache_bytes - byte_cost)
                self._previews[page_id] = QPixmap(preview)
                self._preview_costs[page_id] = byte_cost
                self._retained_preview_bytes += byte_cost
                self._previews.move_to_end(page_id)
                self._preview_states[page_id] = preview_state
        index = self.index(row, 0)
        self.dataChanged.emit(
            index,
            index,
            [
                int(Qt.ItemDataRole.DisplayRole),
                int(Qt.ItemDataRole.AccessibleTextRole),
                int(Qt.ItemDataRole.DecorationRole),
                self.PreviewStateRole,
            ],
        )

    def _discard_preview(self, page_id: UUID) -> None:
        self._previews.pop(page_id, None)
        self._retained_preview_bytes -= self._preview_costs.pop(page_id, 0)

    def _quality_state(self, page_id: UUID) -> QualityReviewState:
        if self._quality_review is None:
            return QualityReviewState.NOT_CHECKED
        if self._quality_findings_by_page.get(page_id):
            return QualityReviewState.NEEDS_REVIEW
        return QualityReviewState.NO_SIGNALS

    def _emit_quality_changed(self) -> None:
        if not self._pages:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._pages) - 1, 0),
            [
                int(Qt.ItemDataRole.DisplayRole),
                int(Qt.ItemDataRole.AccessibleTextRole),
                int(Qt.ItemDataRole.AccessibleDescriptionRole),
                int(Qt.ItemDataRole.ToolTipRole),
                self.QualityReviewStateRole,
                self.QualityFindingCountRole,
            ],
        )


class QualityFindingListModel(QAbstractListModel):
    """Virtual keyboard list for bounded closed quality findings."""

    PageIdRole: Final = _QUALITY_FINDING_PAGE_ID_ROLE
    FindingCodeRole: Final = _QUALITY_FINDING_CODE_ROLE
    RelatedPageIdRole: Final = _QUALITY_FINDING_RELATED_PAGE_ID_ROLE

    def __init__(self) -> None:
        super().__init__()
        self._findings: tuple[QualityFinding, ...] = ()

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._findings)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._findings):
            return None
        finding = self._findings[index.row()]
        page_text = _finding_page_text(finding)
        message = tr(
            "{value1} — Needs review: {value2}. {value3}",
            value1=page_text,
            value2=tr(finding.title),
            value3=tr(finding.message),
        )
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
            int(Qt.ItemDataRole.ToolTipRole),
        }:
            return message
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return tr(
                "Activate to go to page {value1} in the organizer. This automatic suggestion does not complete a requirement.",
                value1=finding.page_position,
            )
        if role == self.PageIdRole:
            return finding.page_id
        if role == self.FindingCodeRole:
            return finding.code.value
        if role == self.RelatedPageIdRole:
            return finding.related_page_id
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        roles = super().roleNames()
        roles[self.PageIdRole] = QByteArray(b"pageId")
        roles[self.FindingCodeRole] = QByteArray(b"findingCode")
        roles[self.RelatedPageIdRole] = QByteArray(b"relatedPageId")
        return roles

    def set_snapshot(self, snapshot: QualityReviewSnapshot | None) -> None:
        """Replace the virtual rows from one already correlated snapshot."""

        if snapshot is not None and type(snapshot) is not QualityReviewSnapshot:
            raise ValueError("snapshot must be a QualityReviewSnapshot or None")
        self.beginResetModel()
        self._findings = () if snapshot is None else snapshot.findings
        self.endResetModel()

    def finding_at(self, row: int) -> QualityFinding | None:
        if not 0 <= row < len(self._findings):
            return None
        return self._findings[row]


def _page_quality_text(state: QualityReviewState, finding_count: int) -> str:
    if state is QualityReviewState.NOT_CHECKED:
        return tr("Quality review not checked for this saved revision")
    if state is QualityReviewState.NO_SIGNALS:
        return tr("Limited quality checks found no review signals")
    noun = tr("signal") if finding_count == 1 else tr("signals")
    return tr("Needs review: {value1} quality {value2}", value1=finding_count, value2=noun)


def _page_edit_description(page: OrganizerPageView) -> str:
    """Describe non-destructive intent using closed facts, without reading page content."""

    intent = page.scan_review_intent
    crop = page.crop
    if crop is None and intent is not None:
        image_crop = intent.crop
        crop = NormalizedPageBox(image_crop.x, image_crop.y, image_crop.width, image_crop.height)
    descriptions = [
        tr("Full page; no crop.")
        if crop is None or crop == NormalizedPageBox.full_page()
        else (
            tr(
                "Cropped page: left {value1:g} percent, top {value2:g} percent, width {value3:g} percent, height {value4:g} percent.",
                value1=crop.x / 10000,
                value2=crop.y / 10000,
                value3=crop.width / 10000,
                value4=crop.height / 10000,
            )
        )
    ]
    if intent is not None:
        adjustments: list[str] = []
        if intent.deskew_angle_millidegrees_clockwise:
            adjustments.append(
                tr(
                    "deskew {value1:g} degrees clockwise",
                    value1=intent.deskew_angle_millidegrees_clockwise / 1000,
                )
            )
        if intent.contrast_factor_millionths != IDENTITY_CONTRAST_FACTOR_MILLIONTHS:
            adjustments.append(
                tr(
                    "contrast {value1:g} percent",
                    value1=intent.contrast_factor_millionths / 10000,
                )
            )
        if intent.background_cleanup_strength_millionths:
            adjustments.append(
                tr(
                    "background cleanup {value1:g} percent",
                    value1=intent.background_cleanup_strength_millionths / 10000,
                )
            )
        descriptions.append(
            tr("Scan adjustments: ") + ", ".join(adjustments) + "."
            if adjustments
            else tr("No scan cleanup adjustments.")
        )
    visibility_labels = {
        OverlayVisibility.VISIBLE: tr("visible"),
        OverlayVisibility.PARTIALLY_VISIBLE: tr("partially outside the crop; needs review"),
        OverlayVisibility.OUTSIDE_ACTIVE_CROP: tr("outside the crop; not visible"),
    }
    if page.overlay_summaries:
        descriptions.append(
            tr("Visual overlays: ")
            + "; ".join(
                tr(
                    "{count} {kind} overlay, {visibility}"
                    if summary.count == 1
                    else "{count} {kind} overlays, {visibility}",
                    count=summary.count,
                    kind=tr(_OVERLAY_KIND_TEXT[summary.kind]),
                    visibility=visibility_labels[summary.visibility],
                )
                for summary in page.overlay_summaries
            )
            + "."
        )
    kinds = {summary.kind for summary in page.overlay_summaries}
    if OverlayKind.SIGNATURE_IMAGE in kinds:
        descriptions.append(
            tr("Signature images are visual marks and do not create digital signatures.")
        )
    if kinds & {OverlayKind.SHAPE, OverlayKind.HIGHLIGHT}:
        descriptions.append(tr("Shapes and highlights do not permanently remove information."))
    if page.removal_mark_count:
        descriptions.append(
            tr(
                "Removal marks are unapplied intent. Information remains in the case; a separately verified removal derivative is required."
            )
        )
    return " ".join(descriptions)


def _finding_page_text(finding: QualityFinding) -> str:
    if finding.related_page_position is None:
        return tr("Page {value1}", value1=finding.page_position)
    return tr(
        "Pages {value1} and {value2}",
        value1=finding.page_position,
        value2=finding.related_page_position,
    )


def _preview_byte_cost(preview: QPixmap) -> int:
    """Charge decoded pixels without creating a duplicate image buffer."""

    # Worker previews use RGBA; charge at least that much for a caller's
    # lower-depth pixmap as well, including native scan-line padding.
    bytes_per_pixel = max(4, (preview.depth() + 7) // 8)
    return preview.width() * preview.height() * bytes_per_pixel


def _preview_key(page: OrganizerPageView) -> _PreviewKey:
    """Identify pixels in this model's single case and fixed renderer/size session.

    A page ID is an immutable source-page reference. Reordering, renaming and
    quality/checklist state do not alter its source pixels. Organizer thumbnails
    render source/scan transformations; editor canvases paint saved marks above
    those source pixels. The model is cleared when the case changes.
    """

    return page.page_id, page.rotation_degrees, page.scan_review_intent, page.crop


def _rotation_independent_preview_content(
    page: OrganizerPageView,
) -> tuple[ImageTransformIntent | None, NormalizedPageBox | None]:
    intent = page.scan_review_intent
    if intent is not None:
        intent = replace(intent, additional_rotation=ImageQuarterTurn.DEGREES_0)
    return intent, page.crop
