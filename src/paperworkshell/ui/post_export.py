"""Accessible review of case removal after independently verified export."""

from __future__ import annotations

from itertools import pairwise

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.post_export_deletion import PostExportDeletionInventory
from paperworkshell.localization.catalog import tr
from paperworkshell.ui.localization_layout import adapt_localized_content


class PostExportDeletionDialog(QDialog):
    """A safe default, explicit acknowledgement, and a separate destructive action."""

    def __init__(self, review: PostExportDeletionInventory, parent: QWidget | None = None) -> None:
        if type(review) is not PostExportDeletionInventory:
            raise ValueError("A current removal inventory is required.")
        super().__init__(parent)
        self.review_id = review.review_id
        self.setObjectName("postExportDeletionDialog")
        self.setWindowTitle(tr("Review case deletion"))
        self.setAccessibleName(tr("Review case deletion"))
        self.setAccessibleDescription(
            tr(
                "Read what will be removed and what remains. Keep case is the default. Nothing is deleted until you acknowledge the consequences and choose Delete case."
            )
        )
        self.setModal(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        inventory = review.inventory
        text = tr(
            "Case reference: {case}\n\nRemove from PaperWorkShell storage:\nImported original copies: {sources}\nPages: {pages}\nVisual overlays: {overlays}\nSignature images: {signatures}\nAll case metadata, history, OCR derivatives, previews and temporary files.\nTotal stored bytes: {total}\nCache bytes: {cache}\nTemporary bytes: {temporary}\n\nKeep outside the case:\nThe verified exports listed below stay in the folder you selected. Any separately saved summary or text companion also remains. External source files remain unchanged.\n\nCase removal does not guarantee forensic erasure. Backups and other copies may remain. Review your exports before deleting this case.",
            case=str(review.case_id),
            sources=inventory.source_count,
            pages=inventory.page_count,
            overlays=inventory.overlay_count,
            signatures=inventory.signature_image_count,
            total=inventory.case_bytes,
            cache=inventory.cache_bytes,
            temporary=inventory.temp_bytes,
        )
        self._inventory = QPlainTextEdit()
        self._inventory.setObjectName("postExportDeletionInventory")
        self._inventory.setAccessibleName(tr("Case deletion inventory and retained exports"))
        self._inventory.setAccessibleDescription(
            tr(
                "Read-only selectable plain text. Use arrow keys to read, Control+Home to return to the start, and Tab for the next control."
            )
        )
        self._inventory.setReadOnly(True)
        self._inventory.setTabChangesFocus(True)
        self._inventory.setPlainText(text + "\n\n" + "\n".join(review.export_names))
        self._inventory.setMinimumHeight(240)
        rows.addWidget(self._inventory, 1)
        self._ack = QCheckBox()
        self._ack.setObjectName("postExportDeletionAcknowledge")
        self._ack.setAccessibleName(tr("Confirm the case deletion consequences"))
        acknowledgement = tr(
            "I reviewed the saved exports and understand what will be deleted and what remains."
        )
        self._ack.setAccessibleDescription(acknowledgement)
        label = QLabel(acknowledgement)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setBuddy(self._ack)
        ack_row = QHBoxLayout()
        ack_row.addWidget(self._ack, 0, Qt.AlignmentFlag.AlignTop)
        ack_row.addWidget(label, 1)
        rows.addLayout(ack_row)
        self._remove = QPushButton(tr("Delete case"))
        self._remove.setObjectName("postExportDeleteConfirmButton")
        self._remove.setAccessibleName(tr("Delete this case from PaperWorkShell storage"))
        self._remove.setAccessibleDescription(
            tr(
                "Remove this case only. The checked saved exports and external source files remain. This action cannot be undone."
            )
        )
        self._remove.setAutoDefault(False)
        self._remove.setEnabled(False)
        self._remove.clicked.connect(self._accept_confirmed)
        self._ack.toggled.connect(self._remove.setEnabled)
        rows.addWidget(self._remove)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setAccessibleName(tr("Case deletion review controls"))
        adapt_localized_content(scroll)
        self._keep = QPushButton(tr("Keep case"))
        self._keep.setObjectName("postExportKeepCaseButton")
        self._keep.setAccessibleName(tr("Keep case and close deletion review"))
        self._keep.setDefault(True)
        self._keep.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._keep)
        for first, second in pairwise(
            (self._keep, self._inventory, self._ack, self._remove, self._keep)
        ):
            QWidget.setTabOrder(first, second)
        self.resize(650, 620)
        self._keep.setFocus()

    def _accept_confirmed(self) -> None:
        if self._ack.isChecked() and self._remove.isEnabled():
            self.accept()

    @property
    def confirmed(self) -> bool:
        return self._ack.isChecked() and self._remove.isEnabled()
