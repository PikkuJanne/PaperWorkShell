"""Toolkit-free organizer session orchestration for the desktop vertical slice.

The backend is deliberately synchronous and thread-confined.  A desktop host
must construct and call it on one coordinator thread so the injected document
worker client retains its single-thread ownership.  The module owns no Qt,
filesystem, storage-adapter, worker-host, renderer, or PDF-library dependency.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Final, Protocol, cast
from uuid import UUID

from paperworkshell.app.assisted_use import AssistedUseService
from paperworkshell.app.checklist import ChecklistEditorState, ChecklistWorkflowService
from paperworkshell.app.core_overlay_tools import (
    CoreOverlayToolsState,
    is_core_overlay_announcement_safe,
)
from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.app.ocr_languages import (
    OcrLanguageManager,
    OcrLanguageSnapshot,
    unavailable_ocr_language_manager,
)
from paperworkshell.app.package_workflow import (
    PackageReviewState,
    PackageWorkflowErrorCode,
    PackageWorkflowService,
    reject_package_workflow,
)
from paperworkshell.app.page_previews import (
    DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS,
    MAX_PAGE_PREVIEW_TIMEOUT_SECONDS,
    PagePreviewErrorCode,
    PagePreviewProgressSink,
    PagePreviewRequest,
    PagePreviewResult,
    reject_page_preview,
)
from paperworkshell.app.page_previews import (
    CancellationProbe as PreviewCancellationProbe,
)
from paperworkshell.app.pdf_export import PdfExportProgressSink, _require_active
from paperworkshell.app.portal_constraints import (
    ConstraintEditorErrorCode,
    ConstraintEditorState,
    PortalConstraintWorkflowService,
    is_constraint_announcement_safe,
    reject_constraint_editor,
)
from paperworkshell.app.post_export_deletion import PostExportDeletionService
from paperworkshell.app.quality_analysis import (
    QualityAnalysisCancellationProbe,
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProgressSink,
    QualityAnalysisRequest,
    QualityAnalysisService,
    QualityPageReviewKind,
    QualityPageReviewRequest,
    QualityReviewSnapshot,
    reject_quality_analysis,
)
from paperworkshell.app.redaction_export import ReviewedRedactionExport
from paperworkshell.app.redaction_tools import RedactionToolsState, _mark_facts
from paperworkshell.app.removal_export_review import RemovalExportReview
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewErrorCode,
    ScanReviewImageResult,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
    reject_scan_review,
)
from paperworkshell.app.signature_images import (
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
    SignatureImageWorkflowPort,
)
from paperworkshell.app.signed_source_policy import (
    SignedSourceConfirmationChoice,
    SignedSourceConfirmationService,
)
from paperworkshell.app.source_ingest import (
    DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe as SourceCancellationProbe,
)
from paperworkshell.app.source_ingest import (
    ProgressSink as SourceProgressSink,
)
from paperworkshell.domain.assisted_use import AssistedUseErrorCode, reject_assisted_use
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionResult,
    CaseIndex,
    CaseLifecycleErrorCode,
    CaseSummary,
    reject_case_lifecycle,
    require_case_name,
)
from paperworkshell.domain.checklist import (
    ChecklistErrorCode,
    reject_checklist,
)
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.image_pages import (
    MAX_IMAGE_DIMENSION_PIXELS,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImageQuarterTurn,
)
from paperworkshell.domain.image_transforms import (
    IDENTITY_CONTRAST_FACTOR_MILLIONTHS,
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImageTransformIntent,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
    reject_manifest_repository,
)
from paperworkshell.domain.package_summary import PackageSummary
from paperworkshell.domain.page_operations import (
    MAX_PAGE_COUNT,
    BackgroundCleanupPageCommand,
    ContrastPageCommand,
    CropPageCommand,
    DeskewPageCommand,
    NormalizedPageBox,
    OrganizerProjection,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
    PageState,
    RotatePageCommand,
    SequenceChangeCommand,
    SequenceChangeReason,
    reject_page_operation,
)
from paperworkshell.domain.pdf_export import (
    PdfExportErrorCode,
    PdfExportOutcome,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailRequest
from paperworkshell.domain.post_export_deletion import (
    PostExportDeletionConfirmation,
    PostExportDeletionErrorCode,
    PostExportDeletionInventory,
    reject_post_export_deletion,
)
from paperworkshell.domain.recovery import (
    RecoveryErrorCode,
    StartupRecoveryResult,
    reject_recovery,
)
from paperworkshell.domain.redaction import (
    MAX_REDACTION_MARKS,
    RedactionErrorCode,
    reject_redaction,
    require_redaction_export_allowed,
)
from paperworkshell.domain.redaction_export import RedactionExportReport
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportOutcome,
    SearchablePdfVerificationReport,
)
from paperworkshell.domain.signature_images import (
    SignatureImageCleanupResult,
    SignatureImageCleanupStatus,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
    reject_signature_image,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourcePolicyErrorCode,
    reject_signed_source_policy,
)
from paperworkshell.domain.source_ingest import SourceImportResult
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.localization.catalog import tr
from paperworkshell.overlays.commands import OverlayCommandHistory
from paperworkshell.overlays.exporting import FlattenedPdfExportOutcome
from paperworkshell.overlays.models import (
    MAX_OVERLAYS,
    OverlayCollection,
    OverlayErrorCode,
    OverlayKind,
    OverlayRecord,
    OverlayVisibility,
    SignatureImageOverlayPayload,
    reject_overlay,
)
from paperworkshell.overlays.signature_commands import (
    SignatureAssetDeletePolicy,
    SignatureImageManifestChange,
)
from paperworkshell.preflight.portal_constraints import PortalOutputMode

_DEFAULT_ORGANIZER_PREVIEW_PAGE_TIMEOUT_SECONDS: Final = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
MAX_ORGANIZER_PREVIEW_BATCH_PAGES: Final = 8
_QUALITY_PROFILE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class _CaseLifecyclePort(Protocol):
    @property
    def active_case(self) -> CaseSummary | None: ...

    def create_case(self, case_name: str = "") -> CaseSummary: ...

    def list_cases(self) -> CaseIndex: ...

    def open_case(self, case_id: UUID) -> CaseSummary: ...

    def close_case(self, case_id: UUID | None = None) -> CaseSummary | None: ...


class _SourceIngestPort(Protocol):
    def import_source(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> SourceImportResult: ...


class _PagesPdfExportPort(Protocol):
    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: PdfExportProgressSink,
        make_searchable: bool = False,
    ) -> PdfExportOutcome | FlattenedPdfExportOutcome | SearchablePdfExportOutcome: ...


class _RemovalPdfExportPort(Protocol):
    def export(
        self,
        review: ReviewedRedactionExport,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: PdfExportProgressSink,
    ) -> RedactionExportReport: ...


class _PagePreviewServicePort(Protocol):
    def get_previews(
        self,
        requests: tuple[PagePreviewRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> tuple[PagePreviewResult, ...]: ...


class _QualityAnalysisServicePort(Protocol):
    @property
    def profile_id(self) -> str: ...

    @property
    def profile_sha256(self) -> str: ...

    def analyze(
        self,
        request: QualityAnalysisRequest,
        *,
        cancellation: QualityAnalysisCancellationProbe | None = None,
        progress: QualityAnalysisProgressSink | None = None,
    ) -> QualityReviewSnapshot: ...


def _close_nothing() -> None:
    return None


@dataclass(frozen=True, slots=True)
class OrganizerOverlaySummary:
    """Bounded visual-overlay counts without values, asset details, or identifiers."""

    kind: OverlayKind
    visibility: OverlayVisibility
    count: int

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not OverlayKind
            or type(self.visibility) is not OverlayVisibility
            or self.visibility is OverlayVisibility.PAGE_INACTIVE
            or type(self.count) is not int
            or not 1 <= self.count <= MAX_OVERLAYS
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.overlays")


@dataclass(frozen=True, slots=True)
class OrganizerPageView:
    """Path-free presentation facts for one active organizer page."""

    page_id: UUID
    position: int
    rotation_degrees: int
    preview_supported: bool
    scan_review_intent: ImageTransformIntent | None = None
    document_number: int | None = None
    source_page_number: int | None = None
    crop: NormalizedPageBox | None = None
    overlay_summaries: tuple[OrganizerOverlaySummary, ...] = ()
    removal_mark_count: int = 0
    source_display_name: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if type(self.source_display_name) is not str or len(self.source_display_name) > 512:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.source_name")
        if type(self.page_id) is not UUID or self.page_id.version != 4:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, "organizer.page_id")
        if type(self.position) is not int or not 1 <= self.position <= MAX_PAGE_COUNT:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.position")
        if type(self.rotation_degrees) is not int or self.rotation_degrees not in {
            0,
            90,
            180,
            270,
        }:
            reject_page_operation(PageOperationErrorCode.ROTATION_INVALID, "organizer.rotation")
        if type(self.preview_supported) is not bool:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.preview")
        if (
            self.scan_review_intent is not None
            and type(self.scan_review_intent) is not ImageTransformIntent
        ):
            reject_page_operation(
                PageOperationErrorCode.MODEL_INVALID,
                "organizer.scan_review_intent",
            )
        if (self.document_number is None) != (self.source_page_number is None) or any(
            value is not None and (type(value) is not int or not 1 <= value <= MAX_PAGE_COUNT)
            for value in (self.document_number, self.source_page_number)
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.page_origin")
        if self.crop is not None and type(self.crop) is not NormalizedPageBox:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.crop")
        if (
            type(self.overlay_summaries) is not tuple
            or len(self.overlay_summaries) > len(OverlayKind) * (len(OverlayVisibility) - 1)
            or any(type(value) is not OrganizerOverlaySummary for value in self.overlay_summaries)
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.overlays")
        keys = tuple((value.kind, value.visibility) for value in self.overlay_summaries)
        if (
            len(set(keys)) != len(keys)
            or sum(value.count for value in self.overlay_summaries) > MAX_OVERLAYS
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.overlays")
        if (
            type(self.removal_mark_count) is not int
            or not 0 <= self.removal_mark_count <= MAX_REDACTION_MARKS
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.removal_marks")


@dataclass(frozen=True, slots=True)
class OrganizerViewState:
    """Complete UI-facing organizer state with no manifest containers or content."""

    case_id: UUID | None
    revision: int | None
    pages: tuple[OrganizerPageView, ...]
    can_undo: bool
    can_redo: bool
    autosave_pending: bool
    recovery_review_required: bool = False
    removal_page_ids: tuple[UUID, ...] = ()
    case_name: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if require_case_name(self.case_name, allow_empty=True) != self.case_name:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.case_name")
        flags = (
            self.can_undo,
            self.can_redo,
            self.autosave_pending,
            self.recovery_review_required,
        )
        if (
            any(type(value) is not bool for value in flags)
            or type(self.pages) is not tuple
            or type(self.removal_page_ids) is not tuple
            or len(self.removal_page_ids) > len(self.pages)
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.state")
        if any(type(page) is not OrganizerPageView for page in self.pages):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.pages")
        if self.case_id is None:
            if self.revision is not None or self.pages or self.removal_page_ids or any(flags):
                reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.empty_state")
            return
        if type(self.case_id) is not UUID or self.case_id.version != 4:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, "organizer.case_id")
        if (
            type(self.revision) is not int
            or self.revision < 0
            or self.revision > MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.revision")
        page_ids = tuple(page.page_id for page in self.pages)
        if (
            len(self.pages) > MAX_PAGE_COUNT
            or len(set(page_ids)) != len(page_ids)
            or self.case_id in page_ids
            or tuple(page.position for page in self.pages) != tuple(range(1, len(self.pages) + 1))
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.pages")
        if any(type(value) is not UUID for value in self.removal_page_ids):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.removal_pages")
        removal_ids = set(self.removal_page_ids)
        if len(removal_ids) != len(self.removal_page_ids) or (
            tuple(page_id for page_id in page_ids if page_id in removal_ids)
            != self.removal_page_ids
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.removal_pages")

    @classmethod
    def empty(cls) -> OrganizerViewState:
        """Return the one valid no-active-case state."""

        return cls(None, None, (), False, False, False, False)


class OrganizerImportRefreshStatus(StrEnum):
    """Closed organizer refresh state after immutable ingest has committed."""

    REFRESHED = "refreshed"
    REOPEN_REQUIRED = "reopen_required"


@dataclass(frozen=True, slots=True)
class OrganizerImportOutcome:
    """Path-free result that never disguises a committed import as a failure."""

    committed_import: SourceImportResult
    state: OrganizerViewState
    refresh_status: OrganizerImportRefreshStatus

    def __post_init__(self) -> None:
        if (
            type(self.committed_import) is not SourceImportResult
            or type(self.state) is not OrganizerViewState
            or type(self.refresh_status) is not OrganizerImportRefreshStatus
            or self.state.case_id != self.committed_import.case_id
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "organizer.import_outcome",
            )
        if self.refresh_status is OrganizerImportRefreshStatus.REFRESHED and (
            self.state.revision is None
            or self.state.revision < self.committed_import.manifest_revision
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                "organizer.import_outcome_revision",
            )

    @property
    def refresh_required(self) -> bool:
        """Return whether the active view must be reconstructed before more work."""

        return self.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED

    @property
    def safe_message(self) -> str:
        """Describe the durable outcome without paths, content, or adapter details."""

        if self.refresh_required:
            return tr(
                "Import committed. Close and reopen the case before continuing "
                "so the organizer can refresh."
            )
        return tr("Import committed and the organizer was refreshed.")


class OrganizerSignatureImageRefreshStatus(StrEnum):
    """Closed organizer refresh state after an atomic placement commit."""

    REFRESHED = "refreshed"
    REOPEN_REQUIRED = "reopen_required"


@dataclass(frozen=True, slots=True)
class OrganizerPagesPdfExportOutcome:
    """Preserve durable export success even if the organizer cannot reload history."""

    committed_export: PdfExportOutcome
    state: OrganizerViewState
    refresh_required: bool
    searchable_report: SearchablePdfVerificationReport | None = None

    def __post_init__(self) -> None:
        if (
            type(self.committed_export) is not PdfExportOutcome
            or type(self.state) is not OrganizerViewState
            or type(self.refresh_required) is not bool
            or self.state.case_id != self.committed_export.history_retry.case_id
            or self.state.autosave_pending
            or (
                self.searchable_report is not None
                and (
                    type(self.searchable_report) is not SearchablePdfVerificationReport
                    or self.searchable_report.base_report != self.committed_export.report
                )
            )
        ):
            reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "export.organizer_outcome")

    @property
    def safe_message(self) -> str:
        message = self.committed_export.safe_message
        report = self.searchable_report
        if report is not None:
            message += tr(
                " Searchable text added to {value1} pages; existing text preserved on {value2} pages.",
                value1=report.ocr_text_page_count,
                value2=report.existing_text_page_count,
            )
            if report.needs_review_page_count:
                message += tr(
                    " No text was recognized on {value1} pages. Needs review.",
                    value1=report.needs_review_page_count,
                )
        if self.refresh_required:
            message += tr(
                " Close and reopen the case before continuing so the organizer can refresh."
            )
        return message


@dataclass(frozen=True, slots=True)
class OrganizerSignatureImagePlacementOutcome:
    """Durable placement result plus an honest organizer refresh outcome."""

    committed_placement: SignatureImagePlacementCommitResult
    state: OrganizerViewState
    refresh_status: OrganizerSignatureImageRefreshStatus

    def __post_init__(self) -> None:
        if (
            type(self.committed_placement) is not SignatureImagePlacementCommitResult
            or type(self.state) is not OrganizerViewState
            or type(self.refresh_status) is not OrganizerSignatureImageRefreshStatus
            or self.state.case_id != self.committed_placement.case_id
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.outcome")

    @property
    def refresh_required(self) -> bool:
        return self.refresh_status is OrganizerSignatureImageRefreshStatus.REOPEN_REQUIRED

    @property
    def safe_message(self) -> str:
        if self.refresh_required:
            return tr(
                "Signature image placed. Close and reopen the case before continuing so the "
                "organizer can refresh."
            )
        return tr("Signature image placed as a visual image only.")


@dataclass(frozen=True, slots=True)
class OrganizerSignatureImageDeleteOutcome:
    """Durable placement deletion with truthful best-effort asset cleanup."""

    state: OrganizerViewState
    cleanup_result: SignatureImageCleanupResult | None
    cleanup_warning: str | None

    def __post_init__(self) -> None:
        if (
            type(self.state) is not OrganizerViewState
            or (
                self.cleanup_result is not None
                and type(self.cleanup_result) is not SignatureImageCleanupResult
            )
            or (self.cleanup_warning is not None and type(self.cleanup_warning) is not str)
            or (self.cleanup_result is not None and self.cleanup_warning is not None)
        ):
            reject_signature_image(
                SignatureImageErrorCode.RESULT_INVALID, "signature.delete_outcome"
            )

    @property
    def safe_message(self) -> str:
        if self.cleanup_warning is not None:
            return self.cleanup_warning
        if self.cleanup_result is None:
            return tr("Signature-image placement removed. The case copy was retained.")
        if self.cleanup_result.status is SignatureImageCleanupStatus.REMOVED:
            return tr("Signature-image placement removed. The unused case copy was cleaned up.")
        if self.cleanup_result.status is SignatureImageCleanupStatus.ALREADY_ABSENT:
            return tr("Signature-image placement removed. The unused case copy was already absent.")
        return tr("Signature-image placement removed. The case copy was retained.")


@dataclass(slots=True)
class _OrganizerSession:
    snapshot: ManifestSnapshot
    history: PageCommandHistory
    autosave: ManifestAutosaveService
    load_status: ManifestLoadStatus
    pending_case_name: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class _ActiveScanReview:
    """One ephemeral review bound to an exact organizer history value."""

    opening_draft: ScanReviewDraft
    origin_history: PageCommandHistory
    apply_eligible_intent: ImageTransformIntent | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _CoreOverlayEditorAuthority:
    """Backend-private opening facts that are never shared with the GUI."""

    case_id: UUID
    manifest_revision: int
    page_id: UUID
    editor_session_id: UUID
    opening_history: OverlayCommandHistory


@dataclass(frozen=True, slots=True, repr=False)
class _RedactionEditorAuthority:
    """Private primitive facts cannot alias the editable presentation draft."""

    case_id: UUID
    manifest_revision: int
    page_id: UUID
    editor_session_id: UUID
    opening_facts: tuple[tuple[object, ...], ...]


class OrganizerBackend:
    """Coordinate one active organizer without importing a desktop toolkit.

    All methods are synchronous.  Long import and thumbnail calls expose the
    existing cooperative cancellation and progress callbacks; the desktop host
    is responsible for invoking them away from the GUI event thread.
    """

    __slots__ = (
        "_active_checklist",
        "_assisted_use",
        "_active_core_overlay_tools",
        "_active_redaction_tools",
        "_active_portal_constraints",
        "_active_scan_review",
        "_active_signature_draft_id",
        "_close_resources",
        "_release_case_resources",
        "_checklist",
        "_lifecycle",
        "_ocr_languages",
        "_pending_committed_import",
        "_portal_constraints",
        "_package_workflow",
        "_post_export_deletion",
        "_pages_pdf_export",
        "_removal_pdf_export",
        "_export_reopen_required",
        "_quality_analysis",
        "_quality_profile_id",
        "_quality_profile_sha256",
        "_repository",
        "_session",
        "_shutdown",
        "_signature_images",
        "_source_ingest",
        "_startup_recovery",
        "_thumbnails",
        "_utc_now",
    )

    def __init__(
        self,
        lifecycle: _CaseLifecyclePort,
        repository: ManifestRepository,
        source_ingest: _SourceIngestPort,
        thumbnails: _PagePreviewServicePort,
        *,
        close_resources: Callable[[], None] = _close_nothing,
        release_case_resources: Callable[[], None] = _close_nothing,
        startup_recovery: StartupRecoveryResult | None = None,
        ocr_languages: OcrLanguageManager | None = None,
        quality_analysis: _QualityAnalysisServicePort | None = None,
        signature_images: SignatureImageWorkflowPort | None = None,
        checklist: ChecklistWorkflowService | None = None,
        portal_constraints: PortalConstraintWorkflowService | None = None,
        package_workflow: PackageWorkflowService | None = None,
        pages_pdf_export: _PagesPdfExportPort | None = None,
        removal_pdf_export: _RemovalPdfExportPort | None = None,
        assisted_use: AssistedUseService | None = None,
        post_export_deletion: PostExportDeletionService | None = None,
    ) -> None:
        if not callable(close_resources) or not callable(release_case_resources):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.resources")
        if startup_recovery is None:
            startup_recovery = StartupRecoveryResult()
        elif type(startup_recovery) is not StartupRecoveryResult:
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.organizer_result")
        selected_ocr_languages = (
            unavailable_ocr_language_manager() if ocr_languages is None else ocr_languages
        )
        if type(selected_ocr_languages) is not OcrLanguageManager:
            reject_page_operation(
                PageOperationErrorCode.MODEL_INVALID,
                "organizer.ocr_languages",
            )
        selected_quality_analysis = (
            QualityAnalysisService(thumbnails) if quality_analysis is None else quality_analysis
        )
        try:
            quality_profile_id = selected_quality_analysis.profile_id
            quality_profile_sha256 = selected_quality_analysis.profile_sha256
        except BaseException:
            quality_profile_id = None
            quality_profile_sha256 = None
        if (
            not callable(getattr(selected_quality_analysis, "analyze", None))
            or type(quality_profile_id) is not str
            or not quality_profile_id
            or len(quality_profile_id) > 128
            or _QUALITY_PROFILE_TOKEN.fullmatch(quality_profile_id) is None
            or type(quality_profile_sha256) is not str
            or len(quality_profile_sha256) != 64
            or any(character not in "0123456789abcdef" for character in quality_profile_sha256)
        ):
            reject_quality_analysis(
                QualityAnalysisErrorCode.MODEL_INVALID,
                "quality.organizer_service",
            )
        if signature_images is not None and any(
            not callable(getattr(signature_images, method, None))
            for method in (
                "begin",
                "commit",
                "cancel",
                "placements",
                "delete_candidate",
                "cleanup_deleted_asset",
                "close",
            )
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.organizer")
        if checklist is not None and type(checklist) is not ChecklistWorkflowService:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.organizer")
        if (
            portal_constraints is not None
            and type(portal_constraints) is not PortalConstraintWorkflowService
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.organizer",
            )
        self._lifecycle = lifecycle
        self._release_case_resources = release_case_resources
        self._assisted_use = assisted_use
        self._repository = repository
        self._source_ingest = source_ingest
        self._thumbnails = thumbnails
        self._close_resources = close_resources
        self._startup_recovery = startup_recovery
        self._ocr_languages = selected_ocr_languages
        self._quality_analysis = selected_quality_analysis
        self._quality_profile_id = quality_profile_id
        self._quality_profile_sha256 = quality_profile_sha256
        self._signature_images = signature_images
        self._checklist = checklist
        self._portal_constraints = portal_constraints
        self._package_workflow = package_workflow
        self._post_export_deletion = post_export_deletion
        self._pages_pdf_export = pages_pdf_export
        self._removal_pdf_export = removal_pdf_export
        self._export_reopen_required = False
        self._utc_now = checklist.utc_now if checklist is not None else lambda: datetime.now(UTC)
        self._active_checklist: ChecklistEditorState | None = None
        self._active_portal_constraints: ConstraintEditorState | None = None
        self._active_core_overlay_tools: _CoreOverlayEditorAuthority | None = None
        self._active_redaction_tools: _RedactionEditorAuthority | None = None
        self._active_scan_review: _ActiveScanReview | None = None
        self._active_signature_draft_id: UUID | None = None
        self._pending_committed_import: SourceImportResult | None = None
        self._session: _OrganizerSession | None = None
        self._shutdown = False

    @property
    def startup_recovery_result(self) -> StartupRecoveryResult:
        """Return the immutable path-free result from desktop startup recovery."""

        return self._startup_recovery

    @property
    def ocr_language_manager(self) -> OcrLanguageManager:
        """Expose the exact app-scoped manager used by OCR service preflight."""

        return self._ocr_languages

    def refresh_ocr_languages(self) -> OcrLanguageSnapshot:
        """Refresh local-only OCR language facts outside the GUI event thread."""

        self._require_not_shutdown()
        return self._ocr_languages.refresh()

    def select_ocr_language_codes(
        self,
        language_codes: tuple[str, ...],
    ) -> OcrLanguageSnapshot:
        """Retain one canonical process-local selection without changing a case."""

        self._require_not_shutdown()
        return self._ocr_languages.select_language_codes(language_codes)

    @property
    def state(self) -> OrganizerViewState:
        """Return the current path-free projection without performing I/O."""

        session = self._session
        return OrganizerViewState.empty() if session is None else _view_state(session)

    @property
    def load_status(self) -> ManifestLoadStatus | None:
        """Expose whether the active snapshot was current or recovered."""

        session = self._session
        return None if session is None else session.load_status

    def list_cases(self) -> CaseIndex:
        """Return the lifecycle service's deterministic path-free case index."""

        if self._assisted_use is not None and self._assisted_use.snapshot is not None:
            return CaseIndex()
        return self._lifecycle.list_cases()

    @property
    def assisted_use(self) -> AssistedUseService | None:
        """Expose the thread-safe local privacy gate to the desktop bridge."""

        return self._assisted_use

    def create_assisted_case(
        self, retention_days: int, passphrase: str, *, consent: bool, case_name: str = ""
    ) -> OrganizerViewState:
        """Publish no organizer content until the consent record is durable."""

        if self._assisted_use is None:
            raise RuntimeError("assisted use is unavailable")
        state = self.create_case(case_name)
        assert state.case_id is not None
        try:
            self._assisted_use.enroll(state.case_id, retention_days, passphrase, consent=consent)
        except BaseException:
            # The newly created empty case has never accepted an import.
            self.close_case()
            raise
        return state

    def create_case(self, case_name: str = "") -> OrganizerViewState:
        """Create, load, and activate one empty case."""

        selected_name = require_case_name(case_name, allow_empty=True)
        self._require_not_shutdown()
        if self._assisted_use is not None and self._assisted_use.snapshot is not None:
            reject_assisted_use(AssistedUseErrorCode.CASE_CONFLICT, "assisted.create_active")
        self._active_scan_review = None
        if self._session is not None:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                "organizer.create_active",
            )
        summary = (
            self._lifecycle.create_case(selected_name)
            if selected_name
            else self._lifecycle.create_case()
        )
        try:
            self._session = self._load_session(summary.case_id)
        except BaseException:
            _close_rejected_activation(self._lifecycle, summary.case_id)
            raise
        return self.state

    def rename_case(self, case_name: str) -> OrganizerViewState:
        """Persist private display text without changing case or source identity."""

        selected_name = require_case_name(case_name)
        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        self.flush_autosave()
        candidate = session.snapshot.document
        if candidate.get("case_name", "") == selected_name:
            return self.state
        candidate["case_name"] = selected_name
        _queue_session_edit(session, candidate)
        session.pending_case_name = selected_name
        return self.flush_autosave()

    def open_case(self, case_id: UUID) -> OrganizerViewState:
        """Open and reconstruct one organizer from its authoritative manifest."""

        self._require_not_shutdown()
        if self._assisted_use is not None:
            self._assisted_use.require_access(case_id)
        current = self._session
        if current is not None:
            if current.snapshot.case_id == case_id:
                return self.state
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                "organizer.open_active",
            )
        summary = self._lifecycle.open_case(case_id)
        self._active_scan_review = None
        try:
            self._session = self._load_session(summary.case_id)
        except BaseException:
            _close_rejected_activation(self._lifecycle, summary.case_id)
            raise
        return self.state

    def reload_case(self) -> OrganizerViewState:
        """Flush pending edits and rebuild state from the latest durable snapshot."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        self._active_scan_review = None
        pending = self._pending_committed_import
        if pending is not None:
            return self._reconcile_committed_import(session, pending).state
        self.flush_autosave()
        loaded = _require_load_result(
            self._repository.load(session.snapshot.case_id),
            session.snapshot.case_id,
        )
        replacement = _session_from_load(self._repository, loaded, utc_now=self._utc_now)
        session.autosave.close()
        self._session = replacement
        return self.state

    def close_case(self) -> OrganizerViewState:
        """Drain autosave before releasing the active case lease."""

        session = self._session
        self._require_core_overlay_tools_inactive()
        self._active_scan_review = None
        if session is None:
            self._release_case_resources()
            self._lifecycle.close_case()
            self._pending_committed_import = None
            if self._assisted_use is not None:
                self._assisted_use.release()
            return OrganizerViewState.empty()
        result = session.autosave.close()
        if result.snapshot is not None:
            session.snapshot = result.snapshot
        try:
            self._release_case_resources()
            self._lifecycle.close_case(session.snapshot.case_id)
        except BaseException:
            # Autosave is sealed after a successful close.  Retain the active
            # session and replace only that coordinator so a lifecycle-close
            # failure does not strand an otherwise editable case.
            session.autosave = ManifestAutosaveService(
                self._repository,
                session.snapshot.case_id,
                utc_now=self._utc_now,
            )
            raise
        self._session = None
        if self._post_export_deletion is not None:
            self._post_export_deletion.clear()
        self._pending_committed_import = None
        self._export_reopen_required = False
        if self._assisted_use is not None:
            self._assisted_use.release()
        return OrganizerViewState.empty()

    def import_source(
        self,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> OrganizerImportOutcome:
        """Import once, then report a closed post-commit organizer refresh outcome."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        self._active_scan_review = None
        pending = self._pending_committed_import
        if pending is not None:
            # A repeated UI import after a post-commit refresh problem is a
            # reconciliation attempt for the already committed source.  It
            # must never invoke immutable ingest a second time.
            return self._reconcile_committed_import(session, pending)
        self.flush_autosave()
        result = self._source_ingest.import_source(
            session.snapshot.case_id,
            source_path,
            display_name,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            progress=progress,
        )
        if type(result) is not SourceImportResult:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "organizer.import_result",
            )
        if result.case_id != session.snapshot.case_id:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.CASE_MISMATCH,
                "organizer.import_result",
            )
        # Returning SourceImportResult is the immutable-ingest commit point.
        # Record it before any fallible reload/session construction so no
        # later exception can make the caller retry the ingest itself.
        self._pending_committed_import = result
        return self._reconcile_committed_import(session, result)

    def begin_removal_export(self) -> RemovalExportReview:
        """Review saved intent without granting permission to publish."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "removal.desktop_active_review")
        self.flush_autosave()
        return RemovalExportReview.from_snapshot(session.snapshot)

    def export_removal_pdf(
        self,
        review: RemovalExportReview,
        destination: Path,
        *,
        signed_source_confirmed: bool,
        deadline: Callable[[], bool] = lambda: False,
        cancellation: Callable[[], bool] = lambda: False,
        progress: PdfExportProgressSink = lambda _value: None,
        state_changed: Callable[[OrganizerViewState], None] | None = None,
    ) -> RedactionExportReport:
        """Invoke the separately gated service on the permanent backend thread."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        exporter = self._removal_pdf_export
        if exporter is None:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.desktop_unavailable")
        if (
            type(review) is not RemovalExportReview
            or type(signed_source_confirmed) is not bool
            or (state_changed is not None and not callable(state_changed))
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "removal.desktop_request")
        review.__post_init__()
        _require_active(deadline, cancellation)
        self.flush_autosave()
        current = _require_load_result(
            self._repository.load(review.case_id), review.case_id
        ).snapshot
        if current != review.snapshot or session.snapshot != review.snapshot:
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "removal.desktop_review_stale")
        snapshot = current
        _require_active(deadline, cancellation)
        if review.signed_source_confirmation_required:
            if not signed_source_confirmed:
                reject_signed_source_policy(
                    SignedSourcePolicyErrorCode.CONFIRMATION_REQUIRED,
                    "removal.desktop_signed_source",
                )
            _outcome, snapshot = SignedSourceConfirmationService(
                self._repository
            ).decide_with_snapshot(
                review.signature_review,
                SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
            )
            replacement = _session_from_load(
                self._repository,
                ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot),
                utc_now=self._utc_now,
            )
            session.autosave.close()
            self._session = replacement
            if state_changed is not None:
                state_changed(_view_state(replacement))
        return exporter.export(
            ReviewedRedactionExport.from_snapshot(snapshot),
            destination,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )

    def export_pages_pdf(
        self,
        destination: Path,
        *,
        cancellation: Callable[[], bool],
        progress: PdfExportProgressSink,
        make_searchable: bool = False,
    ) -> OrganizerPagesPdfExportOutcome:
        """Export saved edits with an explicit OCR choice and no checklist dependency."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        exporter = self._pages_pdf_export
        if exporter is None:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.organizer_unavailable")
        self._active_scan_review = None
        self.flush_autosave()
        require_redaction_export_allowed(session.snapshot.document)
        expires = monotonic() + DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
        result = exporter.export_pdf(
            session.snapshot.case_id,
            destination,
            deadline=lambda: monotonic() >= expires,
            cancellation=cancellation,
            progress=progress,
            **({"make_searchable": True} if make_searchable else {}),
        )
        report = result.report if type(result) is SearchablePdfExportOutcome else None
        outcome = result if isinstance(result, PdfExportOutcome) else result.base_outcome
        # The destination is committed. A later view refresh must never turn this into
        # an apparent retryable export failure or permit edits based on stale history.
        self._export_reopen_required = True
        try:
            loaded = _require_load_result(
                self._repository.load(session.snapshot.case_id),
                session.snapshot.case_id,
            )
            if loaded.snapshot.revision < session.snapshot.revision:
                reject_pdf_export(PdfExportErrorCode.OUTCOME_INVALID, "export.organizer_revision")
            replacement = _session_from_load(self._repository, loaded, utc_now=self._utc_now)
            state = _view_state(replacement)
        except BaseException:
            return OrganizerPagesPdfExportOutcome(outcome, _view_state(session), True, report)
        try:
            session.autosave.close()
        except BaseException:
            pass
        self._session = replacement
        self._export_reopen_required = False
        return OrganizerPagesPdfExportOutcome(outcome, state, False, report)

    def rotate_clockwise(self, page_id: UUID) -> OrganizerViewState:
        """Record one additive clockwise quarter turn for an active page."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        history = session.history.execute(RotatePageCommand(page_id, 1))
        return self._queue_history(session, history)

    def remove_page(self, page_id: UUID) -> OrganizerViewState:
        """Remove one active page from the packet sequence without deleting its source."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        sequence = tuple(
            candidate
            for candidate in session.history.projection.page_sequence
            if candidate != page_id
        )
        history = session.history.execute(
            SequenceChangeCommand(SequenceChangeReason.REMOVE, sequence)
        )
        return self._queue_history(session, history)

    def move_page(self, page_id: UUID, target_position: int) -> OrganizerViewState:
        """Move one active page to a one-based position using reorder intent."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        sequence = list(session.history.projection.page_sequence)
        if type(target_position) is not int or not 1 <= target_position <= len(sequence):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.move_position")
        current_position = sequence.index(page_id) + 1
        if current_position == target_position:
            return self.state
        sequence.pop(current_position - 1)
        sequence.insert(target_position - 1, page_id)
        return self.reorder_pages(tuple(sequence))

    def reorder_pages(self, page_ids: tuple[UUID, ...]) -> OrganizerViewState:
        """Record one complete keyboard- or dialog-provided active page order."""

        session = self._require_session()
        if type(page_ids) is not tuple:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.reorder")
        if page_ids == session.history.projection.page_sequence:
            return self.state
        history = session.history.execute(
            SequenceChangeCommand(SequenceChangeReason.REORDER, page_ids)
        )
        return self._queue_history(session, history)

    def begin_redaction_tools(self, page_id: UUID) -> RedactionToolsState:
        """Open an exclusive unapplied-intent draft after existing page edits save."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.review")
        _require_active_page(session.history.projection, page_id)
        self.flush_autosave()
        state = RedactionToolsState.from_snapshot(session.snapshot, page_id)
        self._active_redaction_tools = _RedactionEditorAuthority(
            state.case_id,
            state.manifest_revision,
            state.page_id,
            state.editor_session_id,
            _mark_facts(state.opening_marks),
        )
        return state

    def save_redaction_tools(self, editor_state: RedactionToolsState) -> OrganizerViewState:
        """Save intent atomically; never invoke or claim permanent removal."""

        session = self._require_session()
        self._require_redaction_editor(editor_state)
        _require_active_page(session.history.projection, editor_state.page_id)
        if not editor_state.changed and not session.autosave.pending:
            self._active_redaction_tools = None
            return self.state
        candidate = editor_state.to_candidate(session.snapshot)
        _queue_session_edit(session, candidate)
        state = self._flush_autosave_unchecked()
        self._active_redaction_tools = None
        return state

    def cancel_redaction_tools(self, editor_state: RedactionToolsState) -> None:
        """Discard a matching draft before any queued save attempt."""

        session = self._require_session()
        self._require_redaction_editor(editor_state)
        if session.autosave.pending:
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.cancel_pending")
        self._active_redaction_tools = None

    def abort_redaction_tools(self) -> None:
        """Release unusable presentation state only when no save is pending."""

        session = self._require_session()
        if self._active_redaction_tools is None or session.autosave.pending:
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.abort")
        self._active_redaction_tools = None

    def _require_redaction_editor(self, state: RedactionToolsState) -> None:
        opening = self._active_redaction_tools
        if (
            type(state) is not RedactionToolsState
            or opening is None
            or state.case_id != opening.case_id
            or state.manifest_revision != opening.manifest_revision
            or state.page_id != opening.page_id
            or state.editor_session_id != opening.editor_session_id
            or not state.matches_opening_facts(opening.opening_facts)
        ):
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.active")

    def begin_core_overlay_tools(self, page_id: UUID) -> CoreOverlayToolsState:
        """Open one exclusive, revision-bound core-overlay editing draft.

        Existing organizer intent is durably flushed first so the overlay draft
        can never replace a coalesced page-edit candidate.  The returned state
        is toolkit-free and performs no persistence until explicitly applied.
        """

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_review")
        _require_active_page(session.history.projection, page_id)
        self.flush_autosave()
        editor_state = CoreOverlayToolsState.from_snapshot(session.snapshot, page_id)
        self._active_core_overlay_tools = _CoreOverlayEditorAuthority(
            editor_state.case_id,
            editor_state.manifest_revision,
            editor_state.page_id,
            editor_state.editor_session_id,
            OverlayCommandHistory.from_snapshot(session.snapshot),
        )
        return editor_state

    def apply_core_overlay_tools(
        self,
        editor_state: CoreOverlayToolsState,
    ) -> OrganizerViewState:
        """Atomically queue and save one exact local core-overlay draft."""

        session = self._require_session()
        if type(editor_state) is not CoreOverlayToolsState:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.editor_apply")
        if self._active_scan_review is not None:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_review")
        opening_state = self._active_core_overlay_tools
        if opening_state is None or not _core_overlay_editor_binding_matches(
            opening_state,
            editor_state,
        ):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_active")
        _require_active_page(session.history.projection, editor_state.page_id)
        if not editor_state.changed and not session.autosave.pending:
            self._active_core_overlay_tools = None
            return self.state
        candidate = editor_state.to_candidate(session.snapshot)
        _queue_session_edit(session, candidate)
        self._active_scan_review = None
        state = self._flush_autosave_unchecked()
        self._active_core_overlay_tools = None
        return state

    def abort_core_overlay_tools(self) -> None:
        """Release the backend-held draft when its presentation result is unusable."""

        session = self._require_session()
        if self._active_core_overlay_tools is None or session.autosave.pending:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_abort")
        self._active_core_overlay_tools = None

    def cancel_core_overlay_tools(self, editor_state: CoreOverlayToolsState) -> None:
        """Discard one matching local draft without queueing or saving anything."""

        session = self._require_session()
        opening_state = self._active_core_overlay_tools
        if (
            type(editor_state) is not CoreOverlayToolsState
            or opening_state is None
            or not _core_overlay_editor_binding_matches(opening_state, editor_state)
        ):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_cancel")
        if session.autosave.pending:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_cancel_pending")
        self._active_core_overlay_tools = None

    def begin_package_review(
        self,
        mode: PortalOutputMode | None = None,
        *,
        cancellation: SourceCancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> PackageReviewState:
        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_package_workflow(PackageWorkflowErrorCode.ACTIVE)
        self.flush_autosave()
        return self._require_package_workflow().begin(
            session.snapshot, mode, cancellation=cancellation, progress=progress
        )

    def refresh_package_review(
        self,
        mode: PortalOutputMode,
        *,
        cancellation: SourceCancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> PackageReviewState:
        self._require_session()
        return self._require_package_workflow().refresh(
            mode, cancellation=cancellation, progress=progress
        )

    def cancel_package_review(self) -> None:
        self._require_package_workflow().cancel()

    def export_package(
        self,
        destination_parent: Path,
        *,
        review_id: UUID,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: SourceCancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> PackageSummary:
        session = self._require_session()
        self._require_redaction_editor_inactive()
        require_redaction_export_allowed(session.snapshot.document)

        def consent_saved(snapshot: ManifestSnapshot) -> None:
            # Rebuild autosave as well as history: the old coordinator may hold
            # the previous saved revision. This occurs before any output writes,
            # so a refresh failure cannot hide a committed package outcome.
            replacement = _session_from_load(
                self._repository,
                ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot),
                utc_now=self._utc_now,
            )
            session.autosave.close()
            self._session = replacement

        return self._require_package_workflow().export(
            destination_parent,
            review_id=review_id,
            signed_source_confirmed=signed_source_confirmed,
            human_review_confirmed=human_review_confirmed,
            cancellation=cancellation,
            progress=progress,
            snapshot_changed=consent_saved,
        )

    def save_package_summary(
        self,
        destination: Path,
        *,
        cancellation: SourceCancellationProbe | None = None,
    ) -> None:
        self._require_session()
        self._require_package_workflow().save_summary(destination, cancellation=cancellation)

    def save_package_text_companion(
        self,
        destination: Path,
        *,
        privacy_confirmed: bool,
        cancellation: SourceCancellationProbe | None = None,
    ) -> None:
        self._require_session()
        self._require_package_workflow().save_text_companion(
            destination, privacy_confirmed=privacy_confirmed, cancellation=cancellation
        )

    def begin_post_export_deletion(
        self,
        *,
        cancellation: SourceCancellationProbe | None = None,
    ) -> PostExportDeletionInventory:
        session = self._require_session()
        service = self._post_export_deletion
        if service is None:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED, "post_export.unavailable"
            )
        if session.autosave.pending:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.REVIEW_STALE, "post_export.unsaved_changes"
            )
        expires = monotonic() + DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
        return service.prepare(
            session.snapshot, cancellation=cancellation, deadline=lambda: monotonic() >= expires
        )

    def confirm_post_export_deletion(self, review_id: UUID) -> CaseDeletionResult:
        session = self._require_session()
        service = self._post_export_deletion
        if service is None:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED, "post_export.unavailable"
            )
        if session.autosave.pending:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.REVIEW_STALE, "post_export.unsaved_changes"
            )
        expires = monotonic() + DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
        return service.confirm(
            review_id,
            session.snapshot,
            PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT,
            deadline=lambda: monotonic() >= expires,
        )

    def cancel_post_export_deletion(self) -> None:
        if self._post_export_deletion is not None:
            self._post_export_deletion.cancel()

    def prepare_compression(
        self,
        destination_parent: Path,
        *,
        review_id: UUID,
        color_choice: TargetSizeColorChoice,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: SourceCancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> CompressionReviewState:
        session = self._require_session()
        self._require_redaction_editor_inactive()
        require_redaction_export_allowed(session.snapshot.document)

        def consent_saved(snapshot: ManifestSnapshot) -> None:
            replacement = _session_from_load(
                self._repository,
                ManifestLoadResult(ManifestLoadStatus.CURRENT, snapshot),
                utc_now=self._utc_now,
            )
            session.autosave.close()
            self._session = replacement

        return self._require_package_workflow().prepare_compression(
            destination_parent,
            review_id=review_id,
            color_choice=color_choice,
            signed_source_confirmed=signed_source_confirmed,
            human_review_confirmed=human_review_confirmed,
            cancellation=cancellation,
            progress=progress,
            snapshot_changed=consent_saved,
        )

    def preview_compression_page(
        self,
        review_id: UUID,
        output_key: str,
        page_index: int,
        *,
        cancellation: SourceCancellationProbe | None = None,
    ) -> CompressionPagePreview:
        self._require_session()
        return self._require_package_workflow().preview_compression_page(
            review_id,
            output_key,
            page_index,
            cancellation=cancellation,
        )

    def commit_compression(
        self,
        review_id: UUID,
        *,
        human_review_confirmed: bool,
        cancellation: SourceCancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> PackageSummary:
        session = self._require_session()
        self._require_redaction_editor_inactive()
        require_redaction_export_allowed(session.snapshot.document)
        return self._require_package_workflow().commit_compression(
            review_id,
            human_review_confirmed=human_review_confirmed,
            cancellation=cancellation,
            progress=progress,
        )

    def discard_compression(self) -> PackageReviewState:
        self._require_session()
        workflow = self._require_package_workflow()
        workflow.discard_compression()
        return workflow.current_review

    def _require_package_workflow(self) -> PackageWorkflowService:
        workflow = self._package_workflow
        if workflow is None:
            reject_package_workflow(PackageWorkflowErrorCode.UNAVAILABLE)
        return workflow

    def begin_checklist(self) -> ChecklistEditorState:
        """Open one exclusive checklist draft against an exact durable revision.

        Pending page intent is flushed before template loading and checklist
        projection so a later checklist candidate cannot replace a coalesced
        organizer edit.  The returned state is detached and performs no case
        mutation until :meth:`apply_checklist` succeeds.
        """

        session = self._require_session()
        workflow = self._require_checklist()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.scan_review")
        self.flush_autosave()
        editor_state = workflow.begin(session.snapshot)
        if (
            type(editor_state) is not ChecklistEditorState
            or editor_state.case_id != session.snapshot.case_id
            or editor_state.manifest_revision != session.snapshot.revision
        ):
            if type(editor_state) is ChecklistEditorState:
                try:
                    workflow.abort(editor_state.editor_session_id)
                except BaseException:
                    pass
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.organizer_begin")
        self._active_checklist = editor_state
        return editor_state

    def apply_checklist(self, editor_state: ChecklistEditorState) -> OrganizerViewState:
        """Queue and immediately save one authenticated checklist draft.

        A failed save retains both the exact autosave candidate and backend
        authority.  Repeating Apply with the same editor state retries safely;
        cancellation and unrelated organizer mutations remain unavailable
        until the durable save succeeds.
        """

        session = self._require_session()
        workflow = self._require_checklist()
        opening_state = self._active_checklist
        if type(editor_state) is not ChecklistEditorState or opening_state is None:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.organizer_apply")
        candidate = workflow.to_candidate(opening_state, editor_state, session.snapshot)
        if not editor_state.changed and not session.autosave.pending:
            workflow.complete(opening_state, editor_state)
            self._active_checklist = None
            return self.state
        _queue_session_edit(session, candidate)
        self._active_scan_review = None
        state = self._flush_autosave_unchecked()
        workflow.complete(opening_state, editor_state)
        self._active_checklist = None
        return state

    def cancel_checklist(self, editor_state: ChecklistEditorState) -> None:
        """Discard one matching local checklist draft without saving anything."""

        session = self._require_session()
        workflow = self._require_checklist()
        opening_state = self._active_checklist
        if type(editor_state) is not ChecklistEditorState or opening_state is None:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.organizer_cancel")
        if session.autosave.pending:
            reject_checklist(
                ChecklistErrorCode.CONCURRENT_CHANGE,
                "checklist.organizer_cancel_pending",
            )
        workflow.cancel(opening_state, editor_state)
        self._active_checklist = None

    def abort_checklist(self) -> None:
        """Release backend-held checklist authority after unusable presentation state."""

        session = self._require_session()
        workflow = self._require_checklist()
        opening_state = self._active_checklist
        if opening_state is None or session.autosave.pending:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.organizer_abort")
        workflow.abort(opening_state.editor_session_id)
        self._active_checklist = None

    def begin_portal_constraints(self) -> ConstraintEditorState:
        """Open one exclusive, revision-bound packaging-constraint draft."""

        session = self._require_session()
        workflow = self._require_portal_constraints()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.WORKFLOW_ACTIVE,
                "constraints.scan_review",
            )
        self.flush_autosave()
        editor_state = workflow.begin(session.snapshot)
        if (
            type(editor_state) is not ConstraintEditorState
            or editor_state.case_id != session.snapshot.case_id
            or editor_state.manifest_revision != session.snapshot.revision
            or not is_constraint_announcement_safe(editor_state.announcement)
        ):
            if type(editor_state) is ConstraintEditorState:
                try:
                    workflow.abort(editor_state.editor_session_id)
                except BaseException:
                    pass
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.organizer_begin",
            )
        self._active_portal_constraints = editor_state
        return editor_state

    def apply_portal_constraints(
        self,
        editor_state: ConstraintEditorState,
    ) -> OrganizerViewState:
        """Queue and durably flush one authenticated complete constraint draft."""

        session = self._require_session()
        workflow = self._require_portal_constraints()
        opening_state = self._active_portal_constraints
        if type(editor_state) is not ConstraintEditorState or opening_state is None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.organizer_apply",
            )
        candidate = workflow.to_candidate(opening_state, editor_state, session.snapshot)
        if not editor_state.changed and not session.autosave.pending:
            workflow.complete(opening_state, editor_state)
            self._active_portal_constraints = None
            return self.state
        _queue_session_edit(session, candidate)
        self._active_scan_review = None
        state = self._flush_autosave_unchecked()
        workflow.complete(opening_state, editor_state)
        self._active_portal_constraints = None
        return state

    def cancel_portal_constraints(self, editor_state: ConstraintEditorState) -> None:
        """Discard one matching constraint draft without saving anything."""

        session = self._require_session()
        workflow = self._require_portal_constraints()
        opening_state = self._active_portal_constraints
        if type(editor_state) is not ConstraintEditorState or opening_state is None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.organizer_cancel",
            )
        if session.autosave.pending:
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.organizer_cancel_pending",
            )
        workflow.cancel(opening_state, editor_state)
        self._active_portal_constraints = None

    def abort_portal_constraints(self) -> None:
        """Release backend authority after an unusable constraint presentation response."""

        session = self._require_session()
        workflow = self._require_portal_constraints()
        opening_state = self._active_portal_constraints
        if opening_state is None or session.autosave.pending:
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.organizer_abort",
            )
        workflow.abort(opening_state.editor_session_id)
        self._active_portal_constraints = None

    def begin_signature_image(
        self,
        page_id: UUID,
        source_path: Path,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImagePlacementDraft:
        """Prepare and preview one private image without committing it."""

        session = self._require_session()
        workflow = self._require_signature_images()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.scan_review")
        if self._active_signature_draft_id is not None:
            reject_signature_image(
                SignatureImageErrorCode.CONFIRMATION_REQUIRED,
                "signature.active_draft",
            )
        _require_active_page(session.history.projection, page_id)
        self.flush_autosave()
        draft = workflow.begin(
            session.snapshot,
            page_id,
            source_path,
            cancellation=cancellation,
            deadline=deadline,
            progress=progress,
        )
        if (
            type(draft) is not SignatureImagePlacementDraft
            or draft.case_id != session.snapshot.case_id
            or draft.manifest_revision != session.snapshot.revision
            or draft.page_id != page_id
        ):
            if type(draft) is SignatureImagePlacementDraft:
                try:
                    workflow.cancel(draft.draft_id)
                except BaseException:
                    pass
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.draft")
        self._active_signature_draft_id = draft.draft_id
        return draft

    def commit_signature_image(
        self,
        request: SignatureImagePlacementRequest,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> OrganizerSignatureImagePlacementOutcome:
        """Commit one explicitly confirmed prepared image and refresh the organizer."""

        session = self._require_session()
        workflow = self._require_signature_images()
        if (
            type(request) is not SignatureImagePlacementRequest
            or self._active_signature_draft_id != request.draft_id
        ):
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.commit_draft")
        result = workflow.commit(
            request,
            cancellation=cancellation,
            deadline=deadline,
            progress=progress,
        )
        self._active_signature_draft_id = None
        return self._reconcile_signature_image_commit(session, result)

    def cancel_signature_image(self, draft_id: UUID) -> None:
        """Discard one retained prepared image without changing the manifest."""

        self._require_session()
        workflow = self._require_signature_images()
        if type(draft_id) is not UUID or self._active_signature_draft_id != draft_id:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.cancel")
        try:
            workflow.cancel(draft_id)
        finally:
            # The coordinator releases its retained authority before asking the
            # lease to clean up.  Do not strand the organizer's exclusive phase
            # if that best-effort cleanup reports a failure.
            self._active_signature_draft_id = None

    def signature_image_placements(
        self,
        page_id: UUID,
    ) -> tuple[SignatureImagePlacementChoice, ...]:
        """List visual signature images on one active page for explicit deletion."""

        session = self._require_session()
        workflow = self._require_signature_images()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.scan_review")
        self.flush_autosave()
        choices = workflow.placements(session.snapshot, page_id)
        if type(choices) is not tuple or any(
            type(choice) is not SignatureImagePlacementChoice for choice in choices
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.placements")
        return choices

    def delete_signature_image(
        self,
        page_id: UUID,
        overlay_id: UUID,
        policy: SignatureAssetDeletePolicy,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> OrganizerSignatureImageDeleteOutcome:
        """Persist one exact placement deletion, then clean only an unused asset."""

        if not callable(cancellation) or not callable(deadline) or not callable(progress):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.cleanup")
        if cancellation():
            reject_signature_image(SignatureImageErrorCode.CANCELLED, "signature.cleanup")
        if deadline():
            reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.cleanup")
        session = self._require_session()
        workflow = self._require_signature_images()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.scan_review")
        self.flush_autosave()
        change = workflow.delete_candidate(
            session.snapshot,
            page_id,
            overlay_id,
            policy,
        )
        if type(change) is not SignatureImageManifestChange:
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.delete")
        _queue_session_edit(session, change.candidate_document)
        state = self._flush_autosave_unchecked()
        cleanup: SignatureImageCleanupResult | None = None
        warning: str | None = None
        try:
            cleanup = workflow.cleanup_deleted_asset(
                change.cleanup_intent,
                case_id=change.case_id,
                cancellation=cancellation,
                deadline=deadline,
                progress=progress,
            )
        except SignatureImageError as error:
            if error.code is SignatureImageErrorCode.CANCELLED:
                warning = (
                    "Signature-image placement removed. Cleanup was cancelled; "
                    "the case copy was retained."
                )
            elif error.code is SignatureImageErrorCode.TIMED_OUT:
                warning = (
                    "Signature-image placement removed. Cleanup reached its time limit; "
                    "the case copy was retained."
                )
            else:
                warning = error.safe_message
        except BaseException:
            warning = SignatureImageError(
                SignatureImageErrorCode.CLEANUP_FAILED,
                "signature.cleanup",
            ).safe_message
        return OrganizerSignatureImageDeleteOutcome(state, cleanup, warning)

    def undo(self) -> OrganizerViewState:
        """Undo exactly one session page operation and queue the resulting state."""

        session = self._require_session()
        return self._queue_history(session, session.history.undo())

    def redo(self) -> OrganizerViewState:
        """Redo exactly one session page operation and queue the resulting state."""

        session = self._require_session()
        return self._queue_history(session, session.history.redo())

    def flush_autosave(self) -> OrganizerViewState:
        """Persist every queued edit, retaining pending state after a failure."""

        self._require_session(allow_locked=True)
        self._require_core_overlay_tools_inactive()
        return self._flush_autosave_unchecked()

    def _flush_autosave_unchecked(self) -> OrganizerViewState:
        """Drain autosave after the caller has established its exclusive phase."""

        session = self._require_session(allow_locked=True)
        result = session.autosave.flush()
        if result.snapshot is not None:
            session.snapshot = result.snapshot
        if not session.autosave.pending:
            session.pending_case_name = None
        return self.state

    def retry_autosave(self) -> OrganizerViewState:
        """Retry the same retained latest edit after a reported save failure."""

        return self.flush_autosave()

    def get_thumbnails(
        self,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        *,
        page_ids: tuple[UUID, ...],
        timeout_seconds: float | None = None,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> tuple[PagePreviewResult, ...]:
        """Render one bounded, ordered active-page subset.

        The preview service uses one shared batch deadline while its secure brokers launch fresh,
        one-shot workers for cache misses and image normalization. The organizer derives its
        default batch budget from the selected page count without changing the independently
        bounded worker-operation limits. An explicit caller timeout remains exact.
        """

        session = self._require_session()
        _require_thumbnail_dimension(maximum_width_pixels, "preview.organizer_width")
        _require_thumbnail_dimension(maximum_height_pixels, "preview.organizer_height")
        media_by_source = _preview_media_by_source(session.snapshot)
        requests: list[PagePreviewRequest] = []
        projection = session.history.projection
        selected_page_ids = _require_preview_page_subset(projection, page_ids)
        for page_id in selected_page_ids:
            page = projection.state_for(page_id)
            try:
                media_type = media_by_source[page.page.source_id]
            except KeyError:
                reject_page_operation(
                    PageOperationErrorCode.MANIFEST_INVALID,
                    "organizer.page_source",
                )
            rotation_degrees = page.quarter_turns_clockwise * 90
            if media_type is DetectedMediaType.PDF:
                if _has_raster_only_transform(page):
                    reject_page_operation(
                        PageOperationErrorCode.MODEL_INVALID,
                        "organizer.pdf_scan_transform",
                    )
                request: PagePreviewRequest = PdfThumbnailRequest(
                    case_id=session.snapshot.case_id,
                    page_id=page_id,
                    maximum_width_pixels=maximum_width_pixels,
                    maximum_height_pixels=maximum_height_pixels,
                    additional_rotation=PdfRenderRotation(rotation_degrees),
                )
            else:
                request = ImagePageTransformRequest(
                    case_id=session.snapshot.case_id,
                    page_id=page_id,
                    intent=_image_transform_intent(page),
                    maximum_width_pixels=maximum_width_pixels,
                    maximum_height_pixels=maximum_height_pixels,
                )
            requests.append(request)
        if not requests:
            return ()
        selected_timeout = (
            _default_organizer_preview_timeout(len(requests))
            if timeout_seconds is None
            else timeout_seconds
        )
        return self._thumbnails.get_previews(
            tuple(requests),
            timeout_seconds=selected_timeout,
            cancellation=cancellation,
            progress=progress,
        )

    def analyze_quality(
        self,
        *,
        expected_case_id: UUID,
        expected_revision: int,
        cancellation: QualityAnalysisCancellationProbe | None = None,
        progress: QualityAnalysisProgressSink | None = None,
    ) -> QualityReviewSnapshot:
        """Return advisory findings for one exact saved organizer revision.

        Analysis is disposable presentation state.  It neither queues page
        history nor writes, autosaves, or changes requirement confirmations.
        """

        session = self._require_session()
        if (
            type(expected_case_id) is not UUID
            or expected_case_id.version != 4
            or type(expected_revision) is not int
            or not 0 <= expected_revision <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_quality_analysis(
                QualityAnalysisErrorCode.MODEL_INVALID,
                "quality.organizer_expectation",
            )
        if (
            session.snapshot.case_id != expected_case_id
            or session.snapshot.revision != expected_revision
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.STALE, "quality.organizer_revision")
        if session.autosave.pending:
            reject_quality_analysis(
                QualityAnalysisErrorCode.SAVE_PENDING,
                "quality.organizer_save",
            )
        origin_history = session.history
        request = _quality_analysis_request(session)
        if not request.pages:
            reject_quality_analysis(
                QualityAnalysisErrorCode.MODEL_INVALID,
                "quality.organizer_pages",
            )
        try:
            snapshot = self._quality_analysis.analyze(
                request,
                cancellation=cancellation,
                progress=progress,
            )
        except QualityAnalysisError as error:
            if type(error) is not QualityAnalysisError:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.INTERNAL_ERROR,
                    "quality.organizer_service",
                )
            rebuilt = QualityAnalysisError(error.code, error.context)
            raise rebuilt from None
        except BaseException:
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.organizer_service",
            )
        if type(snapshot) is not QualityReviewSnapshot:
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.organizer_result",
            )
        if (
            snapshot.case_id != request.case_id
            or snapshot.manifest_revision != request.manifest_revision
            or snapshot.page_ids != request.page_ids
            or snapshot.profile_id != self._quality_profile_id
            or snapshot.profile_sha256 != self._quality_profile_sha256
        ):
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.organizer_binding",
            )
        if (
            self._session is not session
            or session.history is not origin_history
            or session.snapshot.case_id != expected_case_id
            or session.snapshot.revision != expected_revision
            or session.autosave.pending
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.STALE, "quality.organizer_changed")
        return snapshot

    def begin_scan_review(
        self,
        page_id: UUID,
        *,
        suggested_intent: ImageTransformIntent | None = None,
    ) -> ScanReviewDraft:
        """Begin one provisional JPEG/PNG review without changing page history."""

        session = self._require_session()
        self._require_core_overlay_tools_inactive()
        if self._active_scan_review is not None:
            reject_scan_review(
                ScanReviewErrorCode.ALREADY_ACTIVE,
                "scan_review.begin_active",
            )
        if session.autosave.pending:
            reject_scan_review(
                ScanReviewErrorCode.SAVE_PENDING,
                "scan_review.begin_save",
            )
        _require_active_page(session.history.projection, page_id)
        page = session.history.projection.state_for(page_id)
        media_type = _media_type_for_page(session.snapshot, page)
        if media_type not in {DetectedMediaType.JPEG, DetectedMediaType.PNG}:
            reject_scan_review(
                ScanReviewErrorCode.MEDIA_UNSUPPORTED,
                "scan_review.begin_media",
            )
        if suggested_intent is not None and type(suggested_intent) is not ImageTransformIntent:
            reject_scan_review(
                ScanReviewErrorCode.MODEL_INVALID,
                "scan_review.begin_suggestion",
            )
        base_intent = _image_transform_intent(page)
        draft = ScanReviewDraft(
            case_id=session.snapshot.case_id,
            page_id=page_id,
            manifest_revision=session.snapshot.revision,
            base_intent=base_intent,
            candidate_intent=(base_intent if suggested_intent is None else suggested_intent),
            suggestion_present=suggested_intent is not None,
        )
        self._active_scan_review = _ActiveScanReview(
            opening_draft=draft,
            origin_history=session.history,
        )
        return draft

    def get_scan_review_preview(
        self,
        draft: ScanReviewDraft,
        variant: ScanReviewPreviewVariant,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        *,
        timeout_seconds: float | None = None,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> ScanReviewPreviewResult:
        """Render one literal original or provisional adjusted review preview."""

        session = self._require_session()
        active = self._require_scan_review(session, draft)
        if type(variant) is not ScanReviewPreviewVariant:
            reject_scan_review(
                ScanReviewErrorCode.MODEL_INVALID,
                "scan_review.preview_variant",
            )
        _require_thumbnail_dimension(maximum_width_pixels, "preview.scan_review_width")
        _require_thumbnail_dimension(maximum_height_pixels, "preview.scan_review_height")
        if variant is ScanReviewPreviewVariant.ORIGINAL:
            request: PagePreviewRequest = ImagePagePreviewRequest(
                case_id=draft.case_id,
                page_id=draft.page_id,
                maximum_width_pixels=maximum_width_pixels,
                maximum_height_pixels=maximum_height_pixels,
                additional_rotation=ImageQuarterTurn.DEGREES_0,
            )
        else:
            # A newer adjusted request immediately makes any prior adjusted
            # result ineligible.  Only this exact request may enable Apply.
            active.apply_eligible_intent = None
            request = ImagePageTransformRequest(
                case_id=draft.case_id,
                page_id=draft.page_id,
                intent=draft.candidate_intent,
                maximum_width_pixels=maximum_width_pixels,
                maximum_height_pixels=maximum_height_pixels,
            )
        # Scan review is an interactive, explicitly cancelable workflow.  Its
        # outer application deadline spans trusted one-shot AppContainer
        # setup and revocation as well as the independently bounded worker
        # operation.  Use the existing service ceiling by default so slow ACL
        # preparation cannot relabel a safely completed preview as timed out.
        selected_timeout = (
            MAX_PAGE_PREVIEW_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        results = self._thumbnails.get_previews(
            (request,),
            timeout_seconds=selected_timeout,
            cancellation=cancellation,
            progress=progress,
        )
        if type(results) is not tuple or len(results) != 1:
            reject_scan_review(
                ScanReviewErrorCode.PREVIEW_RESULT_INVALID,
                "scan_review.preview_count",
            )
        result = results[0]
        if type(result) not in {ImagePagePreviewResult, ImagePageTransformResult}:
            reject_scan_review(
                ScanReviewErrorCode.PREVIEW_RESULT_INVALID,
                "scan_review.preview_type",
            )
        preview = ScanReviewPreviewResult(
            draft,
            variant,
            cast(ScanReviewImageResult, result),
        )
        if variant is ScanReviewPreviewVariant.ADJUSTED:
            active.apply_eligible_intent = draft.candidate_intent
        return preview

    def cancel_scan_review(self, draft: ScanReviewDraft) -> None:
        """Discard one matching provisional review without queueing autosave."""

        session = self._require_session()
        active = self._active_scan_review
        if active is None or not _draft_matches_opening(active.opening_draft, draft):
            reject_scan_review(
                ScanReviewErrorCode.NOT_ACTIVE,
                "scan_review.cancel",
            )
        if draft.case_id != session.snapshot.case_id:
            reject_scan_review(
                ScanReviewErrorCode.NOT_ACTIVE,
                "scan_review.cancel_case",
            )
        self._active_scan_review = None

    def apply_scan_review(self, draft: ScanReviewDraft) -> OrganizerViewState:
        """Confirm one reviewed draft and queue one candidate only when intent changed."""

        session = self._require_session()
        active = self._require_scan_review(session, draft)
        if active.apply_eligible_intent != draft.candidate_intent:
            reject_scan_review(
                ScanReviewErrorCode.PREVIEW_REQUIRED,
                "scan_review.apply_preview",
            )
        history = _history_with_scan_review(session.history, draft)
        if history is session.history:
            self._active_scan_review = None
            return self.state
        state = self._queue_history(
            session,
            history,
            preserve_scan_review=True,
        )
        self._active_scan_review = None
        return state

    def shutdown(self) -> None:
        """Drain the case, then close injected worker/resources exactly once."""

        if self._shutdown:
            return
        if self._package_workflow is not None:
            self._package_workflow.cancel()
        active_checklist = self._active_checklist
        if active_checklist is not None:
            self._require_checklist().abort(active_checklist.editor_session_id)
            self._active_checklist = None
        active_constraints = self._active_portal_constraints
        if active_constraints is not None:
            self._require_portal_constraints().abort(active_constraints.editor_session_id)
            self._active_portal_constraints = None
        self._active_core_overlay_tools = None
        self._active_redaction_tools = None
        workflow = self._signature_images
        self._active_signature_draft_id = None
        if workflow is not None:
            workflow.close()
        self.close_case()
        self._close_resources()
        self._shutdown = True

    def _reconcile_committed_import(
        self,
        session: _OrganizerSession,
        result: SourceImportResult,
    ) -> OrganizerImportOutcome:
        """Best-effort refresh after the immutable-ingest commit point."""

        try:
            loaded = _require_load_result(
                self._repository.load(session.snapshot.case_id),
                session.snapshot.case_id,
            )
            _require_committed_import(loaded.snapshot, result)
            replacement = _session_from_load(
                self._repository,
                loaded,
                utc_now=self._utc_now,
            )
            refreshed_state = _view_state(replacement)
        except BaseException:
            return OrganizerImportOutcome(
                result,
                _view_state(session),
                OrganizerImportRefreshStatus.REOPEN_REQUIRED,
            )

        # Pre-import edits were flushed before ingest, so the superseded
        # coordinator has no pending mutation.  Its cleanup cannot invalidate
        # the authoritative replacement or turn a committed import into an
        # apparent retryable failure.
        try:
            session.autosave.close()
        except BaseException:
            pass
        self._session = replacement
        self._pending_committed_import = None
        return OrganizerImportOutcome(
            result,
            refreshed_state,
            OrganizerImportRefreshStatus.REFRESHED,
        )

    def _reconcile_signature_image_commit(
        self,
        session: _OrganizerSession,
        result: SignatureImagePlacementCommitResult,
    ) -> OrganizerSignatureImagePlacementOutcome:
        """Best-effort refresh after the asset-plus-overlay durable commit point."""

        try:
            loaded = _require_load_result(
                self._repository.load(session.snapshot.case_id),
                session.snapshot.case_id,
            )
            _require_committed_signature_image(loaded.snapshot, result)
            replacement = _session_from_load(
                self._repository,
                loaded,
                utc_now=self._utc_now,
            )
            refreshed_state = _view_state(replacement)
        except BaseException:
            return OrganizerSignatureImagePlacementOutcome(
                result,
                _view_state(session),
                OrganizerSignatureImageRefreshStatus.REOPEN_REQUIRED,
            )
        try:
            session.autosave.close()
        except BaseException:
            pass
        self._session = replacement
        return OrganizerSignatureImagePlacementOutcome(
            result,
            refreshed_state,
            OrganizerSignatureImageRefreshStatus.REFRESHED,
        )

    def _require_scan_review(
        self,
        session: _OrganizerSession,
        draft: ScanReviewDraft,
    ) -> _ActiveScanReview:
        """Return the exact active review or reject stale presentation state."""

        if type(draft) is not ScanReviewDraft:
            reject_scan_review(
                ScanReviewErrorCode.MODEL_INVALID,
                "scan_review.request_draft",
            )
        active = self._active_scan_review
        if active is None or not _draft_matches_opening(active.opening_draft, draft):
            reject_scan_review(
                ScanReviewErrorCode.NOT_ACTIVE,
                "scan_review.request_active",
            )
        try:
            current_page = session.history.projection.state_for(draft.page_id)
        except PageOperationError:
            reject_scan_review(
                ScanReviewErrorCode.STALE,
                "scan_review.request_page",
            )
        if (
            session.snapshot.case_id != draft.case_id
            or session.snapshot.revision != draft.manifest_revision
            or session.history is not active.origin_history
            or not current_page.active
            or _image_transform_intent(current_page) != draft.base_intent
        ):
            reject_scan_review(
                ScanReviewErrorCode.STALE,
                "scan_review.request_stale",
            )
        return active

    def _load_session(self, case_id: UUID) -> _OrganizerSession:
        loaded = _require_load_result(self._repository.load(case_id), case_id)
        return _session_from_load(self._repository, loaded, utc_now=self._utc_now)

    def _require_session(self, *, allow_locked: bool = False) -> _OrganizerSession:
        if self._export_reopen_required:
            reject_pdf_export(
                PdfExportErrorCode.HISTORY_RECOVERY_REQUIRED, "export.organizer_refresh"
            )
        self._require_not_shutdown()
        session = self._session
        if session is None:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.NO_ACTIVE_CASE,
                "organizer.active_case",
            )
        active = self._lifecycle.active_case
        if active is None or active.case_id != session.snapshot.case_id:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_MISMATCH,
                "organizer.active_case",
            )
        if self._assisted_use is not None and not allow_locked:
            self._assisted_use.require_access(session.snapshot.case_id)
        return session

    def _require_not_shutdown(self) -> None:
        if self._shutdown:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.NO_ACTIVE_CASE,
                "organizer.shutdown",
            )

    def _require_core_overlay_tools_inactive(self) -> None:
        self._require_redaction_editor_inactive()
        if self._package_workflow is not None and self._package_workflow.active:
            reject_package_workflow(PackageWorkflowErrorCode.ACTIVE)
        if self._active_core_overlay_tools is not None:
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.editor_exclusive")
        if self._active_signature_draft_id is not None:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.exclusive")
        if self._active_checklist is not None:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.exclusive")
        if self._active_portal_constraints is not None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.WORKFLOW_ACTIVE,
                "constraints.exclusive",
            )

    def _require_redaction_editor_inactive(self) -> None:
        """An existing package review is allowed; an unsaved intent editor is not."""

        if self._active_redaction_tools is not None:
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.exclusive")

    def _require_checklist(self) -> ChecklistWorkflowService:
        workflow = self._checklist
        if workflow is None:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.unavailable")
        return workflow

    def _require_portal_constraints(self) -> PortalConstraintWorkflowService:
        workflow = self._portal_constraints
        if workflow is None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.unavailable",
            )
        return workflow

    def _require_signature_images(self) -> SignatureImageWorkflowPort:
        workflow = self._signature_images
        if workflow is None:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.unavailable")
        return workflow

    def _queue_history(
        self,
        session: _OrganizerSession,
        history: PageCommandHistory,
        *,
        preserve_scan_review: bool = False,
    ) -> OrganizerViewState:
        self._require_core_overlay_tools_inactive()
        candidate = history.to_candidate(session.snapshot)
        _queue_session_edit(session, candidate)
        session.history = history
        if not preserve_scan_review:
            self._active_scan_review = None
        return self.state


def _session_from_load(
    repository: ManifestRepository,
    loaded: ManifestLoadResult,
    *,
    utc_now: Callable[[], datetime],
) -> _OrganizerSession:
    snapshot = loaded.snapshot
    return _OrganizerSession(
        snapshot=snapshot,
        history=PageCommandHistory.from_snapshot(snapshot),
        autosave=ManifestAutosaveService(repository, snapshot.case_id, utc_now=utc_now),
        load_status=loaded.status,
    )


def _core_overlay_editor_binding_matches(
    opening: _CoreOverlayEditorAuthority,
    candidate: CoreOverlayToolsState,
) -> bool:
    """Compare only the non-content authority tuple for one exclusive editor."""

    return (
        type(opening) is _CoreOverlayEditorAuthority
        and type(candidate) is CoreOverlayToolsState
        and opening.case_id == candidate.case_id
        and opening.manifest_revision == candidate.manifest_revision
        and opening.page_id == candidate.page_id
        and opening.editor_session_id == candidate.editor_session_id
        and is_core_overlay_announcement_safe(candidate.announcement)
        and candidate.history.is_page_scoped_extension_of(
            opening.opening_history,
            opening.page_id,
        )
    )


def _require_load_result(result: object, case_id: UUID) -> ManifestLoadResult:
    if type(result) is not ManifestLoadResult or result.snapshot.case_id != case_id:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "organizer.load_result",
        )
    return result


def _require_committed_import(
    snapshot: ManifestSnapshot,
    result: SourceImportResult,
) -> None:
    """Prove that an authoritative snapshot contains the committed import."""

    document = snapshot.document
    sources = document.get("sources")
    pages = document.get("pages")
    expected_source_id = str(result.source_id)
    expected_page_ids = {str(page_id) for page_id in result.page_ids}
    source_found = False
    found_page_ids: set[str] = set()
    if snapshot.revision >= result.manifest_revision and type(sources) is list:
        for value in sources:
            if not isinstance(value, Mapping) or value.get("source_id") != expected_source_id:
                continue
            source_found = (
                value.get("detected_media_type") == result.media_type.value
                and value.get("byte_size") == result.byte_size
                and value.get("sha256") == result.sha256
                and value.get("page_count") == len(result.page_ids)
            )
            break
    if source_found and type(pages) is list:
        for value in pages:
            if (
                isinstance(value, Mapping)
                and value.get("source_id") == expected_source_id
                and value.get("page_id") in expected_page_ids
            ):
                page_id = value.get("page_id")
                if type(page_id) is str:
                    found_page_ids.add(page_id)
    if not source_found or found_page_ids != expected_page_ids:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.REVISION_CONFLICT,
            "organizer.import_reconciliation",
        )


def _require_committed_signature_image(
    snapshot: ManifestSnapshot,
    result: SignatureImagePlacementCommitResult,
) -> None:
    """Prove that a refreshed manifest contains the exact durable placement."""

    collection = OverlayCollection.from_manifest_document(snapshot.document)
    asset_found = any(asset.asset_id == result.asset_id for asset in collection.assets)
    overlay_found = any(
        record.overlay_id == result.overlay_id
        and type(record.payload) is SignatureImageOverlayPayload
        and record.payload.asset_id == result.asset_id
        for record in collection.overlays
    )
    if (
        snapshot.case_id != result.case_id
        or snapshot.revision < result.manifest_revision
        or not asset_found
        or not overlay_found
    ):
        reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.reconciliation")


def _queue_session_edit(session: _OrganizerSession, candidate: Mapping[str, object]) -> None:
    """Coalesce edits without discarding a private name awaiting a durable save."""

    if session.pending_case_name is not None:
        candidate = dict(candidate)
        candidate["case_name"] = session.pending_case_name
    session.autosave.queue(candidate)


def _view_state(session: _OrganizerSession) -> OrganizerViewState:
    projection = session.history.projection
    media_by_source = _preview_media_by_source(session.snapshot)
    # Catalogue order remains stable when packet pages are moved or removed.
    document_numbers = {source_id: number for number, source_id in enumerate(media_by_source, 1)}
    document = session.snapshot.document
    source_names = {
        UUID(cast(str, source["source_id"])): cast(str, source["display_name"])
        for source in cast(list[dict[str, object]], document["sources"])
    }
    name_counts: dict[str, int] = {}
    for name in source_names.values():
        name_counts[name.casefold()] = name_counts.get(name.casefold(), 0) + 1
    removal_counts: dict[UUID, int] = {}
    for mark in cast(list[dict[str, object]], document.get("redaction_marks", [])):
        mark_page_id = UUID(cast(str, mark["page_id"]))
        removal_counts[mark_page_id] = removal_counts.get(mark_page_id, 0) + 1
    overlay_counts: dict[UUID, dict[tuple[OverlayKind, OverlayVisibility], int]] = {}
    # Decode only bounded overlay records from the already detached, validated
    # snapshot. Re-copying the complete manifest for each UI refresh is unnecessary.
    raw_overlays = document.get("overlays")
    if type(raw_overlays) is not list or len(raw_overlays) > MAX_OVERLAYS:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.overlays")
    page_states = {page_state.page_id: page_state for page_state in projection.pages}
    for raw_overlay in raw_overlays:
        overlay = OverlayRecord.from_manifest(raw_overlay)
        page_state = page_states.get(overlay.page_id)
        if page_state is None:
            reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "organizer.overlay_page")
        if not page_state.active:
            continue
        crop = page_state.crop or NormalizedPageBox.full_page()
        visible_box = crop.intersection(overlay.box)
        visibility = (
            OverlayVisibility.OUTSIDE_ACTIVE_CROP
            if visible_box is None
            else OverlayVisibility.VISIBLE
            if visible_box == overlay.box
            else OverlayVisibility.PARTIALLY_VISIBLE
        )
        counts = overlay_counts.setdefault(overlay.page_id, {})
        key = (overlay.kind, visibility)
        counts[key] = counts.get(key, 0) + 1
    pages: list[OrganizerPageView] = []
    for position, page_id in enumerate(projection.page_sequence, start=1):
        page = page_states[page_id]
        media_type = media_by_source.get(page.page.source_id)
        if media_type is None:
            reject_page_operation(
                PageOperationErrorCode.MANIFEST_INVALID,
                "organizer.page_source",
            )
        pages.append(
            OrganizerPageView(
                page_id=page_id,
                position=position,
                rotation_degrees=page.quarter_turns_clockwise * 90,
                preview_supported=True,
                scan_review_intent=(
                    _image_transform_intent(page)
                    if media_type in {DetectedMediaType.JPEG, DetectedMediaType.PNG}
                    else None
                ),
                document_number=document_numbers[page.page.source_id],
                source_page_number=page.page.source_page_index + 1,
                crop=page.crop,
                overlay_summaries=tuple(
                    OrganizerOverlaySummary(kind, visibility, count)
                    for (kind, visibility), count in sorted(
                        overlay_counts.get(page_id, {}).items(),
                        key=lambda item: (item[0][0].value, item[0][1].value),
                    )
                ),
                removal_mark_count=removal_counts.get(page_id, 0),
                source_display_name=(
                    source_names[page.page.source_id]
                    + (
                        f" (import {document_numbers[page.page.source_id]})"
                        if name_counts[source_names[page.page.source_id].casefold()] > 1
                        else ""
                    )
                ),
            )
        )
    return OrganizerViewState(
        case_id=session.snapshot.case_id,
        revision=session.snapshot.revision,
        pages=tuple(pages),
        can_undo=session.history.can_undo,
        can_redo=session.history.can_redo,
        autosave_pending=session.autosave.pending,
        case_name=(
            session.pending_case_name
            if session.pending_case_name is not None
            else cast(str, document.get("case_name", ""))
        ),
        recovery_review_required=session.load_status
        in {
            ManifestLoadStatus.RECOVERED,
            ManifestLoadStatus.RECOVERED_MIGRATED,
        },
        removal_page_ids=tuple(
            page_id for page_id in projection.page_sequence if page_id in removal_counts
        ),
    )


def _quality_analysis_request(session: _OrganizerSession) -> QualityAnalysisRequest:
    """Project complete active-page intent without exposing manifest containers."""

    projection = session.history.projection
    media_by_source = _preview_media_by_source(session.snapshot)
    pages: list[QualityPageReviewRequest] = []
    for page_id in projection.page_sequence:
        page = projection.state_for(page_id)
        media_type = media_by_source.get(page.page.source_id)
        if media_type is None:
            reject_page_operation(
                PageOperationErrorCode.MANIFEST_INVALID,
                "organizer.page_source",
            )
        if media_type is DetectedMediaType.PDF:
            if _has_raster_only_transform(page):
                reject_quality_analysis(
                    QualityAnalysisErrorCode.MODEL_INVALID,
                    "quality.organizer_pdf_transform",
                )
            pages.append(
                QualityPageReviewRequest(
                    page_id=page_id,
                    kind=QualityPageReviewKind.PDF,
                    pdf_rotation=PdfRenderRotation(page.quarter_turns_clockwise * 90),
                )
            )
        else:
            pages.append(
                QualityPageReviewRequest(
                    page_id=page_id,
                    kind=QualityPageReviewKind.IMAGE,
                    image_intent=_image_transform_intent(page),
                )
            )
    return QualityAnalysisRequest(
        case_id=session.snapshot.case_id,
        manifest_revision=session.snapshot.revision,
        pages=tuple(pages),
    )


def _preview_media_by_source(snapshot: ManifestSnapshot) -> dict[UUID, DetectedMediaType]:
    document = snapshot.document
    sources = document.get("sources")
    if type(sources) is not list:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.sources")
    result: dict[UUID, DetectedMediaType] = {}
    for value in sources:
        if not isinstance(value, Mapping):
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source")
        source_id_value = value.get("source_id")
        media_type = value.get("detected_media_type")
        if type(source_id_value) is not str or type(media_type) is not str:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source")
        try:
            source_id = UUID(source_id_value)
        except ValueError:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source_id")
        if source_id.version != 4 or source_id in result:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source_id")
        try:
            detected_media_type = DetectedMediaType(media_type)
        except ValueError:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.media_type")
        result[source_id] = detected_media_type
    return result


def _image_transform_intent(page: PageState) -> ImageTransformIntent:
    """Bind the authoritative replay projection to one canonical worker intent."""

    if type(page) is not PageState:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.transform_state")
    crop = page.crop
    selected_crop = (
        ImageNormalizedCrop()
        if crop is None
        else ImageNormalizedCrop(crop.x, crop.y, crop.width, crop.height)
    )
    return ImageTransformIntent(
        crop=selected_crop,
        additional_rotation=ImageQuarterTurn(page.quarter_turns_clockwise * 90),
        deskew_angle_millidegrees_clockwise=page.deskew_angle_millidegrees_clockwise,
        contrast_factor_millionths=page.contrast_factor_millionths,
        background_cleanup_strength_millionths=(page.background_cleanup_strength_millionths),
    )


def _media_type_for_page(
    snapshot: ManifestSnapshot,
    page: PageState,
) -> DetectedMediaType:
    """Resolve one content-derived source type without exposing manifest containers."""

    if type(page) is not PageState:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.page_media")
    media_by_source = _preview_media_by_source(snapshot)
    try:
        return media_by_source[page.page.source_id]
    except KeyError:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.page_source")


def _draft_matches_opening(opening: ScanReviewDraft, draft: object) -> bool:
    """Compare immutable review authority while allowing a provisional candidate change."""

    return (
        type(opening) is ScanReviewDraft
        and type(draft) is ScanReviewDraft
        and draft.case_id == opening.case_id
        and draft.page_id == opening.page_id
        and draft.manifest_revision == opening.manifest_revision
        and draft.base_intent == opening.base_intent
        and draft.review_version == opening.review_version
    )


def _history_with_scan_review(
    history: PageCommandHistory,
    draft: ScanReviewDraft,
) -> PageCommandHistory:
    """Build every changed operation locally, leaving the caller untouched on failure."""

    base = draft.base_intent
    candidate = draft.candidate_intent
    updated = history
    if candidate.crop != base.crop:
        updated = updated.execute(
            CropPageCommand(
                draft.page_id,
                NormalizedPageBox(
                    candidate.crop.x,
                    candidate.crop.y,
                    candidate.crop.width,
                    candidate.crop.height,
                ),
            )
        )
    base_turns = int(base.additional_rotation) // 90
    candidate_turns = int(candidate.additional_rotation) // 90
    rotation_delta = (candidate_turns - base_turns) % 4
    if rotation_delta:
        updated = updated.execute(RotatePageCommand(draft.page_id, rotation_delta))
    if candidate.deskew_angle_millidegrees_clockwise != base.deskew_angle_millidegrees_clockwise:
        updated = updated.execute(
            DeskewPageCommand(
                draft.page_id,
                candidate.deskew_angle_millidegrees_clockwise,
            )
        )
    if candidate.contrast_factor_millionths != base.contrast_factor_millionths:
        updated = updated.execute(
            ContrastPageCommand(
                draft.page_id,
                candidate.contrast_factor_millionths,
            )
        )
    if (
        candidate.background_cleanup_strength_millionths
        != base.background_cleanup_strength_millionths
    ):
        updated = updated.execute(
            BackgroundCleanupPageCommand(
                draft.page_id,
                candidate.background_cleanup_strength_millionths,
            )
        )
    return updated


def _has_raster_only_transform(page: PageState) -> bool:
    """Return whether a page requests intent not consumed by the PDF preview path."""

    return (
        page.crop is not None
        or page.deskew_angle_millidegrees_clockwise != 0
        or page.contrast_factor_millionths != IDENTITY_CONTRAST_FACTOR_MILLIONTHS
        or page.background_cleanup_strength_millionths != 0
    )


def _require_active_page(projection: OrganizerProjection, page_id: UUID) -> None:
    page = projection.state_for(page_id)
    if not page.active:
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "organizer.active_page")


def _require_thumbnail_dimension(value: object, context: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_IMAGE_DIMENSION_PIXELS:
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, context)


def _require_preview_page_subset(
    projection: OrganizerProjection,
    page_ids: object,
) -> tuple[UUID, ...]:
    """Validate one small organizer-ordered subset before any worker request."""

    if type(page_ids) is not tuple or len(page_ids) > MAX_ORGANIZER_PREVIEW_BATCH_PAGES:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.thumbnail_pages")
    selected: set[UUID] = set()
    for page_id in page_ids:
        if type(page_id) is not UUID or page_id.version != 4:
            reject_page_operation(
                PageOperationErrorCode.IDENTIFIER_INVALID,
                "organizer.thumbnail_page_id",
            )
        if page_id in selected:
            reject_page_operation(
                PageOperationErrorCode.PAGE_DUPLICATE,
                "organizer.thumbnail_pages",
            )
        selected.add(page_id)
        _require_active_page(projection, page_id)
    ordered = tuple(page_id for page_id in projection.page_sequence if page_id in selected)
    if ordered != page_ids:
        reject_page_operation(
            PageOperationErrorCode.SEQUENCE_MISMATCH,
            "organizer.thumbnail_pages",
        )
    return ordered


def _default_organizer_preview_timeout(request_count: int) -> float:
    """Bound the serial batch without weakening any per-render worker limit."""

    return min(
        MAX_PAGE_PREVIEW_TIMEOUT_SECONDS,
        _DEFAULT_ORGANIZER_PREVIEW_PAGE_TIMEOUT_SECONDS * request_count,
    )


def _close_rejected_activation(lifecycle: _CaseLifecyclePort, case_id: UUID) -> None:
    try:
        lifecycle.close_case(case_id)
    except Exception:
        return
