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


class PreviewState(StrEnum):
    """Visible and accessible state of a disposable page preview."""

    NOT_LOADED = "not loaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


_PAGE_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1
_PREVIEW_STATE_ROLE: Final = _PAGE_ID_ROLE + 1
_INVALID_INDEX: Final = QModelIndex()


class OrganizerPageListModel(QAbstractListModel):
    """Read-only page list driven exclusively by organizer projection state."""

    PageIdRole: Final = _PAGE_ID_ROLE
    PreviewStateRole: Final = _PREVIEW_STATE_ROLE

    def __init__(self) -> None:
        super().__init__()
        self._pages: tuple[OrganizerPageView, ...] = ()
        self._previews: OrderedDict[UUID, QPixmap] = OrderedDict()
        self._preview_states: dict[UUID, PreviewState] = {}

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
        page_text = f"Page {page.position} of {len(self._pages)}"
        rotation_text = f"rotated {page.rotation_degrees} degrees"
        if role == int(Qt.ItemDataRole.DisplayRole):
            return f"{page_text}\n{page.rotation_degrees}\N{DEGREE SIGN} rotation\nPreview {preview.value}"
        if role == int(Qt.ItemDataRole.AccessibleTextRole):
            return f"{page_text}; {rotation_text}; preview {preview.value}"
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return "Use the organizer action buttons or keyboard shortcuts to change this page."
        if role == int(Qt.ItemDataRole.DecorationRole):
            return self._previews.get(page.page_id)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return f"{page_text}; {rotation_text}; preview {preview.value}."
        if role == self.PageIdRole:
            return page.page_id
        if role == self.PreviewStateRole:
            return preview.value
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
        return roles

    def set_organizer_state(self, state: OrganizerViewState) -> None:
        """Replace rows and discard previews invalidated by page facts."""

        prior_pages = {page.page_id: page for page in self._pages}
        retained = {page.page_id for page in state.pages}
        self.beginResetModel()
        self._pages = state.pages
        self._previews = OrderedDict(
            (page_id, preview) for page_id, preview in self._previews.items() if page_id in retained
        )
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
            )
            if preview_changed or not page.preview_supported:
                self._previews.pop(page.page_id, None)
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

    def set_preview_loading(self, page_ids: tuple[UUID, ...]) -> None:
        """Mark supported rows as loading using visible text as well as model state."""

        for page_id in page_ids:
            self._set_preview_state(page_id, PreviewState.LOADING, None)

    def set_preview(self, page_id: UUID, preview: QPixmap) -> None:
        """Attach one already detached GUI-thread pixmap to its matching page."""

        self._set_preview_state(page_id, PreviewState.READY, preview)

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
        maximum_count: int,
        *,
        protected_page_ids: tuple[UUID, ...] = (),
    ) -> tuple[UUID, ...]:
        """Evict least-recently-used pixmaps outside the current viewport budget."""

        if type(maximum_count) is not int or maximum_count < 0:
            raise ValueError("maximum_count must be a non-negative integer")
        protected = frozenset(protected_page_ids)
        evicted: list[UUID] = []
        while len(self._previews) > maximum_count:
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

    @property
    def retained_preview_count(self) -> int:
        """Return the number of GUI-memory pixmaps currently retained."""

        return len(self._previews)

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
            self._previews.pop(page_id, None)
        else:
            self._previews[page_id] = preview
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
