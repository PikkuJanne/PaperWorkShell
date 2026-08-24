"""Qt presentation models for path-free organizer state."""

from __future__ import annotations

from collections import OrderedDict
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
from paperworkshell.domain.quality_analysis import QualityFinding
from paperworkshell.domain.resource_policy import NORMAL_RESOURCE_POLICY_V1


class PreviewState(StrEnum):
    """Visible and accessible state of a disposable page preview."""

    NOT_LOADED = "not loaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class QualityReviewState(StrEnum):
    """Visible advisory state for one page in an exact saved revision."""

    NOT_CHECKED = "not checked"
    NO_SIGNALS = "no review signals from limited checks"
    NEEDS_REVIEW = "needs review"


_PAGE_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1
_PREVIEW_STATE_ROLE: Final = _PAGE_ID_ROLE + 1
_QUALITY_REVIEW_STATE_ROLE: Final = _PREVIEW_STATE_ROLE + 1
_QUALITY_FINDING_COUNT_ROLE: Final = _QUALITY_REVIEW_STATE_ROLE + 1
_QUALITY_FINDING_PAGE_ID_ROLE: Final = _QUALITY_FINDING_COUNT_ROLE + 1
_QUALITY_FINDING_CODE_ROLE: Final = _QUALITY_FINDING_PAGE_ID_ROLE + 1
_QUALITY_FINDING_RELATED_PAGE_ID_ROLE: Final = _QUALITY_FINDING_CODE_ROLE + 1
_INVALID_INDEX: Final = QModelIndex()


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
        page_text = f"Page {page.position} of {len(self._pages)}"
        rotation_text = f"rotated {page.rotation_degrees} degrees"
        quality_text = _page_quality_text(quality_state, len(findings))
        if role == int(Qt.ItemDataRole.DisplayRole):
            return (
                f"{page_text}\n{page.rotation_degrees}\N{DEGREE SIGN} rotation\n"
                f"Preview {preview.value}\n{quality_text}"
            )
        if role == int(Qt.ItemDataRole.AccessibleTextRole):
            return (
                f"{page_text}; {rotation_text}; preview {preview.value}; {quality_text.casefold()}"
            )
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return (
                "Use the organizer action buttons or keyboard shortcuts to change this page. "
                "Automatic quality signals are suggestions and do not complete a requirement."
            )
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._previews.get(page.page_id)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return f"{page_text}; {rotation_text}; preview {preview.value}; {quality_text}."
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
                prior.rotation_degrees != page.rotation_degrees
                or prior.preview_supported != page.preview_supported
                or prior.scan_review_intent != page.scan_review_intent
            )
            if preview_changed or not page.preview_supported:
                self._discard_preview(page.page_id)
                self._preview_states.pop(page.page_id, None)
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
        """Evict LRU pixmaps until their measured decoded byte cost is bounded."""

        if maximum_bytes is None:
            byte_budget = self._maximum_preview_cache_bytes
        elif type(maximum_bytes) is int and maximum_bytes >= 0:
            byte_budget = min(maximum_bytes, self._maximum_preview_cache_bytes)
        else:
            raise ValueError("maximum_bytes must be a non-negative integer")
        protected = frozenset(protected_page_ids)
        evicted: list[UUID] = []
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
        """Return the shared bounded GUI preview for one current page, if ready."""

        if type(page_id) is not UUID or self._preview_states.get(page_id) is not PreviewState.READY:
            return None
        return self._previews.get(page_id)

    @property
    def retained_preview_count(self) -> int:
        """Return the number of GUI-memory pixmaps currently retained."""

        return len(self._previews)

    @property
    def retained_preview_bytes(self) -> int:
        """Return the deterministic decoded-pixel cost charged to the LRU."""

        return self._retained_preview_bytes

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
        self._preview_states[page_id] = preview_state
        if preview is None:
            self._discard_preview(page_id)
        else:
            self._discard_preview(page_id)
            self._previews[page_id] = preview
            byte_cost = _preview_byte_cost(preview)
            self._preview_costs[page_id] = byte_cost
            self._retained_preview_bytes += byte_cost
            self._previews.move_to_end(page_id)
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
        message = f"{page_text} — Needs review: {finding.title}. {finding.message}"
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
            int(Qt.ItemDataRole.ToolTipRole),
        }:
            return message
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return (
                f"Activate to go to page {finding.page_position} in the organizer. "
                "This automatic suggestion does not complete a requirement."
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
        return "Quality review not checked for this saved revision"
    if state is QualityReviewState.NO_SIGNALS:
        return "Limited quality checks found no review signals"
    noun = "signal" if finding_count == 1 else "signals"
    return f"Needs review: {finding_count} quality {noun}"


def _finding_page_text(finding: QualityFinding) -> str:
    if finding.related_page_position is None:
        return f"Page {finding.page_position}"
    return f"Pages {finding.page_position} and {finding.related_page_position}"


def _preview_byte_cost(preview: QPixmap) -> int:
    """Charge decoded pixels without creating a duplicate image buffer."""

    bytes_per_pixel = max(1, (preview.depth() + 7) // 8)
    return preview.width() * preview.height() * bytes_per_pixel
