"""Native linked TRACE recipe and verified result inspection."""

from __future__ import annotations

from typing import Final
from uuid import UUID

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.trace_results import (
    TRACE_CHECK_LABELS,
    TraceExportEvidence,
    TraceSavedExportEvidence,
)
from paperworkshell.domain.ocr import TRUSTED_OCRMYPDF_VERSION, TRUSTED_TESSERACT_VERSION
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_ENGINE_PROFILE,
    IMAGE_PDF_EXPORT_PROFILE,
)
from paperworkshell.domain.searchable_pdf_export import DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE
from paperworkshell.domain.trace_recipe import (
    TRACE_OPERATION_LABELS,
    TraceMarkConfiguration,
    TraceOperation,
    TraceOperationKind,
    TraceOutputStatus,
    TracePackageConfiguration,
    TracePlan,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.exporting import FLATTENED_PDF_EXPORT_PLAN_VERSION

_MARK_LABELS: Final = {
    "text": mark("Text"),
    "date": mark("Date"),
    "checkmark": mark("Checkmark"),
    "cross": mark("Cross"),
    "initials": mark("Initials"),
    "signature_image": mark("Signature image"),
    "highlight": mark("Highlight"),
    "shape": mark("Shape"),
    "unapplied": mark("Unapplied removal mark"),
}
_PACKAGE_MODE_LABELS: Final = {
    "combined_pdf": mark("One combined PDF"),
    "separate_pdfs": mark("Several separate PDFs"),
    "zip": mark("ZIP package"),
}
_PACKAGE_ENCRYPTION_LABELS: Final = {
    "must_be_unencrypted": mark("Outputs must be unencrypted"),
    "allowed": mark("Encrypted outputs are allowed"),
}


class TraceRecipePane(QWidget):
    """Present derived operations and navigate back to their stable page targets."""

    page_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("traceRecipePane")
        self.setAccessibleName(tr("Recipe and results"))
        self._plan: TracePlan | None = None
        self._page_id: UUID | None = None
        self._evidence: TraceExportEvidence | TraceSavedExportEvidence | None = None
        self._output_status: TraceOutputStatus | None = None
        self._ocr_language_set_id: str | None = None
        self._shown_configuration: tuple[TraceOperation | None, UUID | None] = (None, None)

        layout = QVBoxLayout(self)
        title = QLabel(tr("Recipe"), self)
        title.setProperty("traceRole", "panelTitle")
        layout.addWidget(title)
        self._list = QListWidget(self)
        self._list.setObjectName("traceRecipeSteps")
        self._list.setAccessibleName(tr("Effective recipe operations"))
        self._list.setAccessibleDescription(
            tr("Select a step to inspect its parameters. Activate it to move to an affected page.")
        )
        self._list.currentRowChanged.connect(self._show_operation)
        self._list.itemActivated.connect(self._activate_step)
        self._list.itemClicked.connect(self._activate_step)
        layout.addWidget(self._list, 2)

        self._selection_text = QLabel(self)
        self._selection_text.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_text.setWordWrap(True)
        layout.addWidget(self._selection_text)
        self._details = QLabel(self)
        self._details.setTextFormat(Qt.TextFormat.PlainText)
        self._details.setWordWrap(True)
        self._details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._details.setAccessibleName(tr("Operation parameters and dependencies"))
        layout.addWidget(self._details)
        self._configuration = QPlainTextEdit(self)
        self._configuration.setObjectName("traceOperationConfiguration")
        self._configuration.setAccessibleName(tr("Selected operation configuration"))
        self._configuration.setReadOnly(True)
        self._configuration.setTabChangesFocus(True)
        self._configuration.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._configuration.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._configuration.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._configuration, 2)
        self._configuration.hide()

        self._result_status = QLabel(self)
        self._result_status.setWordWrap(True)
        self._result_status.setTextFormat(Qt.TextFormat.PlainText)
        self._result_status.setAccessibleName(tr("Export result status"))
        layout.addWidget(self._result_status)
        self._checks_button = QPushButton(tr("Show export checks"), self)
        self._checks_button.setCheckable(True)
        self._checks_button.toggled.connect(self._toggle_checks)
        layout.addWidget(self._checks_button)
        self._checks = QListWidget(self)
        self._checks.setObjectName("traceExportChecks")
        self._checks.setAccessibleName(tr("Export verification findings"))
        self._checks.setWordWrap(True)
        self._checks.hide()
        layout.addWidget(self._checks, 2)
        self._show_result()

    def set_plan(self, plan: TracePlan | None) -> None:
        if self._plan == plan:
            return
        previous_row = self._list.currentRow()
        self._plan = plan
        with QSignalBlocker(self._list):
            self._list.clear()
            if plan is not None:
                for operation in plan.operations:
                    item = QListWidgetItem(tr(TRACE_OPERATION_LABELS[operation.kind]), self._list)
                    item.setData(
                        Qt.ItemDataRole.AccessibleDescriptionRole, _operation_description(operation)
                    )
                if plan.operations:
                    self._list.setCurrentRow(max(0, min(previous_row, len(plan.operations) - 1)))
        self.select_page(self._page_id)
        self._show_operation(self._list.currentRow())
        self._show_result()

    def select_page(self, page_id: UUID | None) -> None:
        self._page_id = page_id
        self._show_configuration(self._selected_operation())
        if self._plan is None or page_id is None:
            self._selection_text.setText(tr("Select a page to inspect its recipe operations."))
            return
        baseline = {
            TraceOperationKind.INPUT,
            TraceOperationKind.SELECTION,
            TraceOperationKind.ORDER,
            TraceOperationKind.EXPORT,
        }
        matching = [
            row
            for row, operation in enumerate(self._plan.operations)
            if page_id in operation.page_ids and operation.kind not in baseline
        ]
        if matching:
            self._selection_text.setText(
                tr("Selected page has {value1} additional recipe steps.", value1=len(matching))
            )
            if self._list.currentRow() not in matching and not self._list.hasFocus():
                self._list.setCurrentRow(matching[0])
        else:
            self._selection_text.setText(tr("The selected page follows the assembled order."))

    def set_export_evidence(
        self, evidence: TraceExportEvidence | TraceSavedExportEvidence | None
    ) -> None:
        if self._evidence == evidence:
            return
        self._evidence = evidence
        self._output_status = None
        self._checks.clear()
        if evidence is not None:
            if isinstance(evidence, TraceExportEvidence):
                for detail in evidence.execution_details:
                    QListWidgetItem(detail, self._checks)
            for check in evidence.checks:
                finding = tr(
                    "{value1} · {value2}",
                    value1=tr(TRACE_CHECK_LABELS[check.status]),
                    value2=check.finding,
                )
                QListWidgetItem(finding, self._checks)
        self._show_result()

    def set_output_status(self, status: TraceOutputStatus | None) -> None:
        """Refresh live artifact availability while retaining the historical findings."""

        self._output_status = status
        self._show_result()

    def set_ocr_language_set(self, language_set_id: str | None) -> None:
        self._ocr_language_set_id = language_set_id
        self._show_result()

    def _activate_step(self, item: QListWidgetItem) -> None:
        row = self._list.row(item)
        if self._plan is None or not 0 <= row < len(self._plan.operations):
            return
        operation = self._plan.operations[row]
        if operation.page_ids and self._page_id not in operation.page_ids:
            self.page_requested.emit(operation.page_ids[0])

    def _show_operation(self, row: int) -> None:
        if self._plan is not None and 0 <= row < len(self._plan.operations):
            self._details.setText(_operation_description(self._plan.operations[row]))
        else:
            self._details.clear()
        self._show_configuration(self._selected_operation())

    def _selected_operation(self) -> TraceOperation | None:
        row = self._list.currentRow()
        if self._plan is not None and 0 <= row < len(self._plan.operations):
            return self._plan.operations[row]
        return None

    def _show_configuration(self, operation: TraceOperation | None) -> None:
        page = self._page_id if operation is not None and operation.marks else None
        if self._shown_configuration == (operation, page):
            return
        self._shown_configuration = (operation, page)
        rows: tuple[str, ...] = ()
        if operation is not None:
            if operation.marks:
                selected = sorted(
                    (mark for mark in operation.marks if mark.page_id == page),
                    key=lambda mark: mark.layer or 0,
                )
                rows = tuple(_mark_description(mark) for mark in selected) or (
                    tr("Select an affected page to inspect its saved mark settings."),
                )
            elif operation.package is not None:
                rows = _package_description(operation.package)
            elif operation.kind is TraceOperationKind.EXPORT:
                rows = _export_configuration()
        self._configuration.setPlainText("\n".join(rows))
        self._configuration.setVisible(bool(rows))

    def _toggle_checks(self, enabled: bool) -> None:
        self._checks_button.setText(
            tr("Hide export checks") if enabled else tr("Show export checks")
        )
        self._checks.setVisible(enabled and self._evidence is not None)

    def _show_result(self) -> None:
        evidence = self._evidence
        self._checks_button.setEnabled(evidence is not None)
        if evidence is None:
            self._checks_button.setChecked(False)
            self._result_status.setText(tr("Export checks · Not checked"))
        elif isinstance(evidence, TraceSavedExportEvidence):
            self._result_status.setText(
                tr(
                    "Saved export checks · {value1} pages · Case revision {value2}",
                    value1=evidence.page_count,
                    value2=evidence.plan_revision,
                )
            )
        elif self._output_status in {TraceOutputStatus.MISSING, TraceOutputStatus.CHANGED}:
            self._result_status.setText(
                tr("Out of date · Exported file is missing.")
                if self._output_status is TraceOutputStatus.MISSING
                else tr("Out of date · Exported file has changed.")
            )
        elif self._plan is None or not evidence.applies_to(
            evidence.case_id, self._plan.fingerprint, ocr_language_set_id=self._ocr_language_set_id
        ):
            self._result_status.setText(
                tr(
                    "Out of date · Prior result is from plan revision {value1}.",
                    value1=evidence.plan_revision,
                )
            )
        else:
            message = (
                mark("Verified page range · {value1} pages · Plan revision {value2}")
                if evidence.selected_range
                else mark("Verified PDF · {value1} pages · Plan revision {value2}")
            )
            self._result_status.setText(
                tr(message, value1=evidence.page_count, value2=evidence.plan_revision)
            )


def _operation_description(operation: TraceOperation) -> str:
    facts = [
        tr("Affected pages: {value1}", value1=len(operation.page_ids)),
    ]
    kind = operation.kind
    values = operation.parameters
    if kind is TraceOperationKind.INPUT:
        facts.append(tr("Source files: {value1}", value1=values[0]))
    elif kind is TraceOperationKind.SELECTION:
        facts.append(tr("Retained pages: {value1} of {value2}", value1=values[0], value2=values[1]))
    elif kind is TraceOperationKind.ROTATION:
        facts.append(tr("Rotation: {value1}° clockwise", value1=values[0]))
    elif kind is TraceOperationKind.CROP:
        facts.append(
            tr(
                "Crop x {value1}%, y {value2}%, width {value3}%, height {value4}%.",
                value1=values[0] / 10_000,
                value2=values[1] / 10_000,
                value3=values[2] / 10_000,
                value4=values[3] / 10_000,
            )
        )
    elif kind is TraceOperationKind.DESKEW:
        facts.append(tr("Deskew: {value1}°", value1=values[0] / 1_000))
    elif kind in {TraceOperationKind.CONTRAST, TraceOperationKind.BACKGROUND}:
        facts.append(tr("Level: {value1}%", value1=values[0] / 10_000))
    elif kind in {TraceOperationKind.OVERLAY, TraceOperationKind.REMOVAL}:
        facts.append(tr("Saved marks: {value1}", value1=values[0]))
    if operation.depends_on:
        dependencies = ", ".join(
            tr(TRACE_OPERATION_LABELS[value]) for value in operation.depends_on
        )
        facts.append(tr("Depends on: {value1}", value1=dependencies))
    return "\n".join(facts)


def _mark_description(configuration: TraceMarkConfiguration) -> str:
    box: NormalizedPageBox = configuration.box
    facts = [
        tr(_MARK_LABELS[configuration.kind]),
        tr("Record version {value1}", value1=configuration.version),
    ]
    if configuration.layer is not None:
        facts.append(tr("Layer index {value1}", value1=configuration.layer))
    return (
        " · ".join(facts)
        + "\n"
        + tr(
            "Box x {value1}%, y {value2}%, width {value3}%, height {value4}%.",
            value1=box.x / 10_000,
            value2=box.y / 10_000,
            value3=box.width / 10_000,
            value4=box.height / 10_000,
        )
    )


def _package_description(configuration: TracePackageConfiguration) -> tuple[str, ...]:
    modes = ", ".join(tr(_PACKAGE_MODE_LABELS[mode]) for mode in configuration.allowed_modes)
    facts = [
        tr("Saved package policy version {value1}", value1=configuration.version),
        tr("Allowed output modes: {value1}", value1=modes),
        tr(_PACKAGE_ENCRYPTION_LABELS[configuration.encryption_policy]),
        tr("Saved assignments: {value1}", value1=configuration.assignment_count),
        tr("Requested output names: {value1}", value1=configuration.requested_name_count),
    ]
    if configuration.max_files is not None:
        facts.append(tr("Maximum files: {value1}", value1=configuration.max_files))
    if configuration.max_total_bytes is not None:
        facts.append(tr("Maximum package bytes: {value1}", value1=configuration.max_total_bytes))
    if configuration.max_file_bytes is not None:
        facts.append(tr("Maximum bytes per file: {value1}", value1=configuration.max_file_bytes))
    if configuration.max_pages_per_file is not None:
        facts.append(
            tr("Maximum pages per file: {value1}", value1=configuration.max_pages_per_file)
        )
    return tuple(facts)


def _export_configuration() -> tuple[str, ...]:
    engines = DEFAULT_PDF_EXPORT_ENGINE_PROFILE
    structural = IMAGE_PDF_EXPORT_PROFILE
    searchable = DEFAULT_SEARCHABLE_PDF_EXPORT_PROFILE
    return (
        tr(
            "Required PDF engines: pikepdf {value1}, qpdf {value2}, pypdfium2 {value3}, PDFium {value4}.",
            value1=engines.pikepdf,
            value2=engines.qpdf,
            value3=engines.pypdfium2,
            value4=engines.pdfium,
        ),
        tr("Structural PDF assembly profile: {value1}", value1=structural.assembly.value),
        tr("PDF render verification profile: {value1}", value1=structural.render.value),
        tr("PDF structure verification profile: {value1}", value1=structural.qpdf.value),
        tr("Flattened PDF plan version {value1}", value1=FLATTENED_PDF_EXPORT_PLAN_VERSION),
        tr(
            "Optional searchable text profile: {value1} (version {value2})",
            value1=searchable.ocr_profile_id,
            value2=searchable.profile_version,
        ),
        tr(
            "Required for OCR: OCRmyPDF {value1}, Tesseract {value2}.",
            value1=TRUSTED_OCRMYPDF_VERSION,
            value2=TRUSTED_TESSERACT_VERSION,
        ),
    )
