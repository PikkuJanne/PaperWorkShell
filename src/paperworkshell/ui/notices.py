"""Bundled installed-notices viewer; no optional Windows editor or path options."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.installed_notices import (
    NOTICE_PAGE_CHARACTERS,
    InstalledNoticesError,
    NoticeCatalog,
    NoticeText,
    current_notice_catalog,
    read_notice,
)
from paperworkshell.domain.uninstall_review import UninstallReviewError, UninstallReviewErrorCode
from paperworkshell.localization.catalog import tr
from paperworkshell.storage.uninstall_review import uninstall_application_guard
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.application import configure_ui_locale
from paperworkshell.ui.localization_layout import adapt_localized_content


class NoticesDialog(QDialog):
    """At most one small plain-text page, with a bounded installed-root selector."""

    def __init__(
        self, catalog: NoticeCatalog | None, parent: QWidget | None = None, *, busy: bool = False
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._content = NoticeText("")
        self._page = 0
        self._last_query = ""
        self._search_end = 0
        self._busy = busy
        self._announcer = ScreenReaderAnnouncer(self)
        self.failed = catalog is None
        self.setObjectName("installedNoticesDialog")
        self.setWindowTitle(tr("PaperWorkShell notices and licences"))
        self.setAccessibleName(tr("Installed component notices and licence texts"))
        available = self.screen().availableGeometry()
        self.resize(min(760, available.width()), min(600, available.height()))
        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel(
            tr(
                "Read the included component versions, notices and licence texts. Nothing is uploaded."
            )
        )
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        if catalog is not None:
            version = QLabel(
                tr(
                    "PaperWorkShell {version} ({candidate})",
                    version=catalog.version,
                    candidate=catalog.candidate_id,
                )
            )
            version.setTextFormat(Qt.TextFormat.PlainText)
            version.setWordWrap(True)
            layout.addWidget(version)
        self._files = QComboBox()
        self._files.setObjectName("noticeFile")
        self._files.setAccessibleName(tr("Included notice or licence file"))
        self._files.setEditable(False)
        self._files.setMaxVisibleItems(20)
        if catalog is not None:
            for item in catalog.files:
                self._files.addItem(item.relative_name)
        layout.addWidget(self._files)
        find_row = QHBoxLayout()
        self._find = QLineEdit()
        self._find.setObjectName("noticeFind")
        self._find.setMaxLength(256)
        self._find.setPlaceholderText(tr("Find text"))
        self._find.setAccessibleName(tr("Find text in the selected notice"))
        find_row.addWidget(self._find, 1)
        self._find_next = QPushButton(tr("Find next"))
        self._find_next.setObjectName("noticeFindNext")
        self._find_next.setAutoDefault(False)
        find_row.addWidget(self._find_next)
        layout.addLayout(find_row)
        self._view = QPlainTextEdit()
        self._view.setObjectName("noticeText")
        self._view.setAccessibleName(tr("Read-only installed notice text"))
        self._view.setAccessibleDescription(
            tr(
                "Control+Home and Control+End move within this page. Use Previous page and Next page for the complete text. Tab leaves the text view."
            )
        )
        self._view.setReadOnly(True)
        self._view.setMinimumHeight(140)
        self._view.setTabChangesFocus(True)
        layout.addWidget(self._view, 1)
        self._status = QLabel()
        self._status.setObjectName("noticeStatus")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setAccessibleName(tr("Notice page and search status"))
        layout.addWidget(self._status)
        self._previous = QPushButton(tr("Previous page"))
        self._previous.setObjectName("noticePrevious")
        self._previous.setAutoDefault(False)
        self._next = QPushButton(tr("Next page"))
        self._next.setObjectName("noticeNext")
        self._next.setAutoDefault(False)
        self._close = QPushButton(tr("Close"))
        self._close.setObjectName("noticeClose")
        self._close.setDefault(True)
        layout.addWidget(self._previous)
        layout.addWidget(self._next)
        area = QScrollArea()
        area.setObjectName("noticeControls")
        area.setAccessibleName(tr("Notice selection, search and page controls"))
        area.setWidgetResizable(True)
        area.setWidget(content)
        outer = QVBoxLayout(self)
        outer.addWidget(area, 1)
        outer.addWidget(self._close)
        for first, second in zip(
            (self._files, self._find, self._find_next, self._view, self._previous, self._next),
            (self._find, self._find_next, self._view, self._previous, self._next, self._close),
            strict=True,
        ):
            QWidget.setTabOrder(first, second)
        self._files.currentIndexChanged.connect(self._selected)
        self._previous.clicked.connect(lambda: self._show_page(self._page - 1))
        self._next.clicked.connect(lambda: self._show_page(self._page + 1))
        self._find_next.clicked.connect(self._search)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self._find)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self._search)
        self._close.clicked.connect(self.accept)
        adapt_localized_content(area)
        self._selected(0)
        self._view.setFocus()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._announcer.announce(text, assertive=error)

    def _error(self) -> None:
        self.failed = True
        self._content = NoticeText("")
        self._view.setPlainText(
            tr(
                "Close PaperWorkShell or its removal review, then open notices again. No case or document was opened. (NOTICES_APPLICATION_BUSY)"
            )
            if self._busy
            else tr(
                "The installed notice could not be read safely. Repair the same PaperWorkShell version and try again. Your cases are separate. (INSTALLED_NOTICES_UNAVAILABLE)"
            )
        )
        self._set_status(tr("Notice unavailable. No case or document was opened."), error=True)
        self._previous.setEnabled(False)
        self._next.setEnabled(False)
        self._find_next.setEnabled(False)
        self._find.setEnabled(False)

    def _selected(self, index: int) -> None:
        if self._catalog is None or not 0 <= index < len(self._catalog.files):
            self._error()
            return
        try:
            self._content = read_notice(self._catalog, self._catalog.files[index])
        except InstalledNoticesError:
            self._error()
            return
        self._last_query = ""
        self._search_end = 0
        self._find_next.setEnabled(True)
        self._find.setEnabled(True)
        self._show_page(0)

    def _show_page(self, page: int) -> None:
        if not 0 <= page < self._content.page_count:
            return
        self._page = page
        self._view.setPlainText(self._content.page(page))
        self._previous.setEnabled(page > 0)
        self._next.setEnabled(page + 1 < self._content.page_count)
        self._set_status(
            tr("Page {page} of {pages}.", page=page + 1, pages=self._content.page_count)
        )

    def _search(self) -> None:
        query = self._find.text()
        if not query:
            self._find.setFocus()
            return
        begin = self._search_end if query == self._last_query else 0
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        found = pattern.search(self._content.text, begin)
        if found is None and begin:
            found = pattern.search(self._content.text, 0, begin)
        self._last_query = query
        if found is None:
            self._set_status(tr("No matching text was found in this notice."))
            return
        self._search_end = found.end()
        page = found.start() // NOTICE_PAGE_CHARACTERS
        self._show_page(page)
        page_begin = page * NOTICE_PAGE_CHARACTERS
        visible = self._content.page(page)
        start_text = visible[: found.start() - page_begin].replace("\r\n", "\n").replace("\r", "\n")
        end_text = (
            visible[: min(found.end() - page_begin, len(visible))]
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        start = len(start_text.encode("utf-16-le")) // 2
        end = len(end_text.encode("utf-16-le")) // 2
        cursor = self._view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()
        self._view.setFocus()
        if found.end() > page_begin + len(visible):
            self._set_status(
                tr("The match continues on the next page. Use Next page to read the rest.")
            )


def _show(application: QApplication, catalog: NoticeCatalog | None, *, busy: bool = False) -> int:
    dialog = NoticesDialog(catalog, busy=busy)
    dialog.finished.connect(application.quit)
    dialog.show()
    application.exec()
    return 2 if dialog.failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments or os.name != "nt":
        print(
            "ERROR [NOTICES_ARGUMENT_INVALID] Installed notices accept no options.", file=sys.stderr
        )
        return 2
    application: QApplication | None = None
    try:
        with uninstall_application_guard():
            application = QApplication(["PaperWorkShell"])
            configure_ui_locale(application)
            application.setApplicationName("PaperWorkShell")
            try:
                catalog = current_notice_catalog()
            except Exception:
                catalog = None
            return _show(application, catalog)
    except Exception as error:
        try:
            if application is None:
                application = QApplication(["PaperWorkShell"])
                configure_ui_locale(application)
                application.setApplicationName("PaperWorkShell")
            return _show(
                application,
                None,
                busy=isinstance(error, UninstallReviewError)
                and error.code is UninstallReviewErrorCode.APPLICATION_RUNNING,
            )
        except Exception:
            print(
                "ERROR [INSTALLED_NOTICES_UNAVAILABLE] Close PaperWorkShell and retry notices.",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
