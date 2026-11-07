from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.app.organizer as organizer_module
from paperworkshell.app.checklist import ChecklistEditorState, ChecklistWorkflowService
from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
)
from paperworkshell.app.organizer import (
    MAX_ORGANIZER_PREVIEW_BATCH_PAGES,
    OrganizerBackend,
    OrganizerImportRefreshStatus,
    OrganizerSignatureImageDeleteOutcome,
    OrganizerViewState,
    _default_organizer_preview_timeout,
)
from paperworkshell.app.page_previews import (
    DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS,
    MAX_PAGE_PREVIEW_TIMEOUT_SECONDS,
    PagePreviewProgressSink,
    PagePreviewRequest,
    PagePreviewResult,
)
from paperworkshell.app.page_previews import (
    CancellationProbe as PreviewCancellationProbe,
)
from paperworkshell.app.portal_constraints import (
    ConstraintDraftInput,
    ConstraintEditorError,
    ConstraintEditorErrorCode,
    ConstraintEditorState,
    PortalConstraintWorkflowService,
)
from paperworkshell.app.quality_analysis import (
    QualityAnalysisCancellationProbe,
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProgress,
    QualityAnalysisProgressPhase,
    QualityAnalysisProgressSink,
    QualityAnalysisRequest,
    QualityPageReviewKind,
    QualityReviewSnapshot,
)
from paperworkshell.app.scan_review import (
    ScanReviewError,
    ScanReviewErrorCode,
    ScanReviewPreviewVariant,
)
from paperworkshell.app.signature_images import (
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
    SignatureImageWorkflowPort,
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
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseSummary
from paperworkshell.domain.checklist import ChecklistError, ChecklistErrorCode
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageMetadata,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
    copy_manifest_document,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import (
    PageCommand,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailRequest
from paperworkshell.domain.quality_analysis import (
    DEFAULT_QUALITY_ANALYSIS_PROFILE,
)
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.domain.signature_images import (
    SignatureImageCleanupResult,
    SignatureImageCleanupStatus,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
)
from paperworkshell.domain.source_ingest import SourceImportResult
from paperworkshell.overlays.commands import CreateCoreOverlayCommand
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.signature_commands import (
    CreateSignatureImageCommand,
    DeleteSignatureImageCommand,
    SignatureAssetCleanupIntent,
    SignatureAssetDeletePolicy,
    SignatureImageManifestChange,
    create_signature_image_candidate,
    delete_signature_image_candidate,
)
from paperworkshell.overlays.signature_rasters import SignatureImagePreview
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

CASE_ID = UUID("10000000-0000-4000-8000-000000000111")
PDF_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000111")
IMAGE_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000112")
IMPORTED_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000113")
PAGE_IDS = (
    UUID("30000000-0000-4000-8000-000000000111"),
    UUID("30000000-0000-4000-8000-000000000112"),
    UUID("30000000-0000-4000-8000-000000000113"),
)
IMAGE_PAGE_ID = UUID("30000000-0000-4000-8000-000000000114")
IMPORTED_PAGE_ID = UUID("30000000-0000-4000-8000-000000000115")
IMPORT_OPERATION_ID = UUID("50000000-0000-4000-8000-000000000111")
SIGNATURE_DRAFT_ID = UUID("70000000-0000-4000-8000-000000000111")
SIGNATURE_ASSET_ID = UUID("40000000-0000-4000-8000-000000000111")
SIGNATURE_OVERLAY_ID = UUID("60000000-0000-4000-8000-000000000111")
STAMP = "2026-08-05T10:00:00Z"


def _document(*, page_count: int = 3, include_image: bool = False) -> dict[str, object]:
    additional_pages = tuple(
        UUID(f"30000000-0000-4000-8000-{number:012d}")
        for number in range(1_000, 1_000 + max(0, page_count - len(PAGE_IDS)))
    )
    selected_pages = (*PAGE_IDS[:page_count], *additional_pages)
    sources: list[dict[str, object]] = []
    if selected_pages:
        sources.append(
            {
                "source_id": str(PDF_SOURCE_ID),
                "display_name": "Synthetic packet.pdf",
                "storage_name": f"{PDF_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.PDF.value,
                "byte_size": 1024,
                "page_count": len(selected_pages),
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
    pages = [
        {
            "page_id": str(page_id),
            "source_id": str(PDF_SOURCE_ID),
            "source_page_index": index,
        }
        for index, page_id in enumerate(selected_pages)
    ]
    sequence = [str(page_id) for page_id in selected_pages]
    if include_image:
        sources.append(
            {
                "source_id": str(IMAGE_SOURCE_ID),
                "display_name": "Synthetic scan.jpg",
                "storage_name": f"{IMAGE_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.JPEG.value,
                "byte_size": 512,
                "page_count": 1,
                "sha256": "b" * 64,
                "signature_state": "not_applicable",
                "encryption_state": "not_applicable",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
        pages.append(
            {
                "page_id": str(IMAGE_PAGE_ID),
                "source_id": str(IMAGE_SOURCE_ID),
                "source_page_index": 0,
            }
        )
        sequence.append(str(IMAGE_PAGE_ID))
    return {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "case_id": str(CASE_ID),
        "revision": 0,
        "created_at": STAMP,
        "updated_at": STAMP,
        "template_ref": None,
        "sources": sources,
        "pages": pages,
        "page_sequence_base": list(sequence),
        "page_sequence": list(sequence),
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "redaction_marks": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: Mapping[str, object]) -> ManifestSnapshot:
    revision = cast(int, document["revision"])
    return ManifestSnapshot(
        CASE_ID,
        CURRENT_MANIFEST_SCHEMA_VERSION,
        revision,
        document,
        f"{revision + 1:064x}",
        f"organizer_cas_{revision:016d}",
    )


class _Repository:
    def __init__(self, document: Mapping[str, object]) -> None:
        self.current = _snapshot(document)
        self.load_status = ManifestLoadStatus.CURRENT
        self.load_calls = 0
        self.load_failures: list[BaseException] = []
        self.fail_saves = 0
        self.save_calls = 0

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.load_calls += 1
        if self.load_failures:
            raise self.load_failures.pop(0)
        return ManifestLoadResult(self.load_status, self.current)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == CASE_ID
        assert expected_revision == self.current.revision
        assert expected_sha256 == self.current.sha256
        assert expected_cas_token == self.current.cas_token
        self.save_calls += 1
        if self.fail_saves:
            self.fail_saves -= 1
            raise ManifestRepositoryError(
                ManifestRepositoryErrorCode.SAVE_FAILED,
                "test.organizer_save",
            )
        published = copy_manifest_document(candidate)
        self.current = _snapshot(published)
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.current)

    def import_pdf(self, display_name: str) -> SourceImportResult:
        document = self.current.document
        sources = cast(list[dict[str, object]], document["sources"])
        pages = cast(list[dict[str, object]], document["pages"])
        base = cast(list[str], document["page_sequence_base"])
        sequence = cast(list[str], document["page_sequence"])
        operations = cast(list[dict[str, object]], document["page_operations"])
        sources.append(
            {
                "source_id": str(IMPORTED_SOURCE_ID),
                "display_name": display_name,
                "storage_name": f"{IMPORTED_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.PDF.value,
                "byte_size": 2048,
                "page_count": 1,
                "sha256": "c" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
        pages.append(
            {
                "page_id": str(IMPORTED_PAGE_ID),
                "source_id": str(IMPORTED_SOURCE_ID),
                "source_page_index": 0,
            }
        )
        if operations:
            before = list(sequence)
            sequence.append(str(IMPORTED_PAGE_ID))
            operations.append(
                {
                    "operation_id": str(IMPORT_OPERATION_ID),
                    "operation_version": 1,
                    "sequence": len(operations) + 1,
                    "payload": {
                        "kind": "change_sequence",
                        "reason": "import",
                        "before_page_ids": before,
                        "after_page_ids": list(sequence),
                    },
                }
            )
        else:
            base.append(str(IMPORTED_PAGE_ID))
            sequence.append(str(IMPORTED_PAGE_ID))
        revision = self.current.revision + 1
        document["revision"] = revision
        document["updated_at"] = f"2026-08-05T10:{revision:02d}:00Z"
        self.current = _snapshot(document)
        return SourceImportResult(
            case_id=CASE_ID,
            source_id=IMPORTED_SOURCE_ID,
            page_ids=(IMPORTED_PAGE_ID,),
            media_type=DetectedMediaType.PDF,
            byte_size=2048,
            sha256="c" * 64,
            manifest_revision=revision,
        )


class _Lifecycle:
    def __init__(self, repository: _Repository) -> None:
        self._repository = repository
        self._active: CaseSummary | None = None
        self.close_calls = 0

    @property
    def active_case(self) -> CaseSummary | None:
        return self._active

    def create_case(self, case_name: str = "") -> CaseSummary:
        assert self._active is None
        if case_name:
            document = self._repository.current.document
            document["case_name"] = case_name
            self._repository.current = _snapshot(document)
        self._active = self._summary()
        return self._active

    def list_cases(self) -> CaseIndex:
        return CaseIndex((self._summary(),))

    def open_case(self, case_id: UUID) -> CaseSummary:
        assert case_id == CASE_ID
        assert self._active is None
        self._active = self._summary()
        return self._active

    def close_case(self, case_id: UUID | None = None) -> CaseSummary | None:
        if case_id is not None:
            assert case_id == CASE_ID
        prior = self._active
        self._active = None
        self.close_calls += 1
        return prior

    def _summary(self) -> CaseSummary:
        timestamp = datetime(2026, 8, 5, 10, tzinfo=UTC)
        return CaseSummary(
            CASE_ID,
            1,
            self._repository.current.revision,
            timestamp,
            timestamp,
        )


class _SourceIngest:
    def __init__(self, repository: _Repository) -> None:
        self._repository = repository
        self.calls = 0
        self.revision_seen: int | None = None
        self.timeout_seen: float | None = None
        self.cancellation_seen: SourceCancellationProbe | None = None
        self.progress_seen: SourceProgressSink | None = None

    def import_source(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> SourceImportResult:
        del source_path
        assert case_id == CASE_ID
        self.calls += 1
        self.revision_seen = self._repository.current.revision
        self.timeout_seen = timeout_seconds
        self.cancellation_seen = cancellation
        self.progress_seen = progress
        return self._repository.import_pdf(display_name)


class _Thumbnails:
    def __init__(self) -> None:
        self.requests: tuple[PagePreviewRequest, ...] = ()
        self.timeout_seen: float | None = None
        self.cancellation_seen: PreviewCancellationProbe | None = None
        self.progress_seen: PagePreviewProgressSink | None = None
        self.results: tuple[PagePreviewResult, ...] = ()

    def get_previews(
        self,
        requests: tuple[PagePreviewRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> tuple[PagePreviewResult, ...]:
        self.requests = requests
        self.timeout_seen = timeout_seconds
        self.cancellation_seen = cancellation
        self.progress_seen = progress
        return self.results


class _QualityAnalysis:
    def __init__(
        self,
        *,
        profile_id: str = DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id,
        profile_sha256: str = DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256,
    ) -> None:
        self.requests: list[QualityAnalysisRequest] = []
        self.cancellation_seen: QualityAnalysisCancellationProbe | None = None
        self.progress_seen: QualityAnalysisProgressSink | None = None
        self.result_factory: Callable[[QualityAnalysisRequest], object] | None = None
        self.profile_id = profile_id
        self.profile_sha256 = profile_sha256

    def analyze(
        self,
        request: QualityAnalysisRequest,
        *,
        cancellation: QualityAnalysisCancellationProbe | None = None,
        progress: QualityAnalysisProgressSink | None = None,
    ) -> QualityReviewSnapshot:
        self.requests.append(request)
        self.cancellation_seen = cancellation
        self.progress_seen = progress
        if progress is not None and request.pages:
            progress(
                QualityAnalysisProgress(
                    QualityAnalysisProgressPhase.PREVIEWING,
                    0,
                    len(request.pages),
                )
            )
        if self.result_factory is not None:
            return cast(QualityReviewSnapshot, self.result_factory(request))
        return QualityReviewSnapshot(
            case_id=request.case_id,
            manifest_revision=request.manifest_revision,
            page_ids=request.page_ids,
            findings=(),
            profile_id=self.profile_id,
            profile_sha256=self.profile_sha256,
        )


class _SignatureImages:
    """Small durable fake for organizer-exclusive phase and refresh evidence."""

    def __init__(
        self,
        repository: _Repository,
        *,
        fail_cancel_cleanup: bool = False,
        fail_asset_cleanup: bool = False,
    ) -> None:
        self._repository = repository
        self._active: tuple[ManifestSnapshot, SignatureImagePlacementDraft] | None = None
        self._fail_cancel_cleanup = fail_cancel_cleanup
        self._fail_asset_cleanup = fail_asset_cleanup
        self.cleanup_revisions: list[int] = []
        self.close_calls = 0

    def begin(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        source_path: Path,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImagePlacementDraft:
        del source_path, progress
        assert not cancellation() and not deadline()
        preview = SignatureImagePreview(
            SIGNATURE_ASSET_ID,
            1,
            1,
            4,
            bytes((0, 0, 0, 0)),
            True,
            False,
            False,
            20,
            10,
        )
        draft = SignatureImagePlacementDraft(
            SIGNATURE_DRAFT_ID,
            snapshot.case_id,
            snapshot.revision,
            page_id,
            preview,
        )
        self._active = (snapshot, draft)
        return draft

    def commit(
        self,
        request: SignatureImagePlacementRequest,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImagePlacementCommitResult:
        del progress
        assert not cancellation() and not deadline()
        assert request.disclaimer_confirmed and request.whole_image_and_background_confirmed
        active = self._active
        assert active is not None and request.draft_id == active[1].draft_id
        snapshot, draft = active
        asset = OverlayAssetRecord(
            SIGNATURE_ASSET_ID,
            OverlayAssetKind.SIGNATURE_IMAGE,
            f"{SIGNATURE_ASSET_ID}.bin",
            OverlayAssetMediaType.PNG,
            4,
            "e" * 64,
        )
        change = create_signature_image_candidate(
            snapshot,
            CreateSignatureImageCommand(
                snapshot.case_id,
                snapshot.revision,
                asset,
                SIGNATURE_OVERLAY_ID,
                draft.page_id,
                request.box,
            ),
        )
        candidate = change.candidate_document
        candidate["revision"] = snapshot.revision + 1
        candidate["updated_at"] = "2026-08-05T10:01:00Z"
        self._repository.current = _snapshot(candidate)
        self._repository.save_calls += 1
        self._active = None
        return SignatureImagePlacementCommitResult(
            snapshot.case_id,
            SIGNATURE_ASSET_ID,
            SIGNATURE_OVERLAY_ID,
            snapshot.revision + 1,
        )

    def cancel(self, draft_id: UUID) -> None:
        active = self._active
        assert active is not None and active[1].draft_id == draft_id
        self._active = None
        if self._fail_cancel_cleanup:
            raise SignatureImageError(
                SignatureImageErrorCode.CLEANUP_FAILED,
                "signature.test_cleanup",
            )

    def placements(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
    ) -> tuple[SignatureImagePlacementChoice, ...]:
        collection = OverlayCollection.from_manifest_document(snapshot.document)
        records = tuple(
            record
            for record in collection.overlays
            if record.page_id == page_id and type(record.payload) is SignatureImageOverlayPayload
        )
        return tuple(
            SignatureImagePlacementChoice(
                record.overlay_id, page_id, record.box, index, len(records)
            )
            for index, record in enumerate(records, start=1)
        )

    def delete_candidate(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        overlay_id: UUID,
        policy: SignatureAssetDeletePolicy,
    ) -> SignatureImageManifestChange:
        return delete_signature_image_candidate(
            snapshot,
            DeleteSignatureImageCommand(
                snapshot.case_id,
                snapshot.revision,
                overlay_id,
                page_id,
                policy,
            ),
        )

    def cleanup_deleted_asset(
        self,
        intent: SignatureAssetCleanupIntent,
        *,
        case_id: UUID,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImageCleanupResult | None:
        del cancellation, deadline, progress
        if not intent.requires_file_removal:
            return None
        asset = intent.asset
        assert asset is not None
        self.cleanup_revisions.append(self._repository.current.revision)
        if self._fail_asset_cleanup:
            raise RuntimeError("private native cleanup detail")
        return SignatureImageCleanupResult(
            case_id,
            asset.asset_id,
            SignatureImageCleanupStatus.REMOVED,
        )

    def close(self) -> None:
        self.close_calls += 1
        self._active = None


def _review_metadata() -> ImagePageMetadata:
    return ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _review_original_result(request: ImagePagePreviewRequest) -> ImagePagePreviewResult:
    pixels = bytes((10, 20, 30, 255)) * 2
    snapshot = ImagePagePreviewSnapshot(
        request.case_id,
        request.page_id,
        2,
        1,
        8,
        pixels,
        _review_metadata(),
        request.additional_rotation,
    )
    return ImagePagePreviewResult(request, snapshot)


def _review_adjusted_result(
    request: ImagePageTransformRequest,
) -> ImagePageTransformResult:
    pixels = bytes((40, 50, 60, 255))
    snapshot = ImagePageTransformSnapshot(
        case_id=request.case_id,
        source_id=IMAGE_SOURCE_ID,
        page_id=request.page_id,
        intent=request.intent,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        bytes_written=4,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        cache_key_sha256="c" * 64,
    )
    return ImagePageTransformResult(request, snapshot, pixels)


def _backend(
    document: Mapping[str, object],
    *,
    close_resources: Callable[[], None] = lambda: None,
    release_case_resources: Callable[[], None] = lambda: None,
    ocr_languages: OcrLanguageManager | None = None,
    quality_analysis: _QualityAnalysis | None = None,
    signature_images: SignatureImageWorkflowPort | None = None,
) -> tuple[OrganizerBackend, _Repository, _Lifecycle, _SourceIngest, _Thumbnails]:
    repository = _Repository(document)
    lifecycle = _Lifecycle(repository)
    source_ingest = _SourceIngest(repository)
    thumbnails = _Thumbnails()
    return (
        OrganizerBackend(
            lifecycle,
            repository,
            source_ingest,
            thumbnails,
            close_resources=close_resources,
            release_case_resources=release_case_resources,
            ocr_languages=ocr_languages,
            quality_analysis=quality_analysis,
            signature_images=signature_images,
        ),
        repository,
        lifecycle,
        source_ingest,
        thumbnails,
    )


def _signature_backend(
    *,
    fail_cancel_cleanup: bool = False,
    fail_asset_cleanup: bool = False,
) -> tuple[OrganizerBackend, _Repository, _SignatureImages]:
    repository = _Repository(_document(page_count=1))
    workflow = _SignatureImages(
        repository,
        fail_cancel_cleanup=fail_cancel_cleanup,
        fail_asset_cleanup=fail_asset_cleanup,
    )
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        signature_images=workflow,
    )
    return backend, repository, workflow


def _checklist_backend() -> tuple[OrganizerBackend, _Repository, ChecklistWorkflowService]:
    repository = _Repository(_document())
    workflow = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        clock=lambda: datetime(2026, 8, 28, 12, tzinfo=UTC),
    )
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        checklist=workflow,
    )
    return backend, repository, workflow


def _portal_constraints_backend() -> tuple[
    OrganizerBackend,
    _Repository,
    PortalConstraintWorkflowService,
]:
    repository = _Repository(_document())
    loader = SafeTemplateLoader(built_in_template_repository())
    checklist = ChecklistWorkflowService(
        loader,
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        clock=lambda: datetime(2026, 8, 28, 12, tzinfo=UTC),
    )
    workflow = PortalConstraintWorkflowService(
        loader,
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
    )
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        checklist=checklist,
        portal_constraints=workflow,
    )
    return backend, repository, workflow


def _edited_portal_constraints(opening: ConstraintEditorState) -> ConstraintEditorState:
    constraints = opening.constraints
    return opening.replace(
        ConstraintDraftInput(
            constraints.allowed_modes,
            constraints.encryption_policy,
            4,
            constraints.maximum_total_bytes,
            constraints.maximum_file_bytes,
            25,
            ("reviewed-packet",),
        )
    )


def test_signature_image_placement_refreshes_then_deletes_before_asset_cleanup() -> None:
    backend, repository, workflow = _signature_backend()
    opened = backend.open_case(CASE_ID)
    page_id = opened.pages[0].page_id
    draft = backend.begin_signature_image(
        page_id,
        Path("synthetic-private-signature.png"),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    placement = backend.commit_signature_image(
        SignatureImagePlacementRequest(
            draft.draft_id,
            NormalizedPageBox(100_000, 200_000, 300_000, 100_000),
            True,
            True,
        ),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert not placement.refresh_required
    assert placement.state.revision == 1
    choices = backend.signature_image_placements(page_id)
    assert len(choices) == 1 and choices[0].overlay_id == SIGNATURE_OVERLAY_ID

    deletion = backend.delete_signature_image(
        page_id,
        choices[0].overlay_id,
        SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert deletion.state.revision == 2
    assert deletion.cleanup_result is not None
    assert deletion.cleanup_result.status is SignatureImageCleanupStatus.REMOVED
    assert workflow.cleanup_revisions == [2]
    collection = OverlayCollection.from_manifest_document(repository.current.document)
    assert SIGNATURE_OVERLAY_ID not in {record.overlay_id for record in collection.overlays}
    assert SIGNATURE_ASSET_ID not in {asset.asset_id for asset in collection.assets}

    backend.rotate_clockwise(page_id)
    backend.flush_autosave()
    rebased = OverlayCollection.from_manifest_document(repository.current.document)
    assert SIGNATURE_OVERLAY_ID not in {record.overlay_id for record in rebased.overlays}
    assert SIGNATURE_ASSET_ID not in {asset.asset_id for asset in rebased.assets}


def test_signature_delete_reports_sanitized_cleanup_warning_after_durable_removal() -> None:
    backend, repository, workflow = _signature_backend(fail_asset_cleanup=True)
    page_id = backend.open_case(CASE_ID).pages[0].page_id
    draft = backend.begin_signature_image(
        page_id,
        Path("synthetic-private-signature.png"),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )
    backend.commit_signature_image(
        SignatureImagePlacementRequest(
            draft.draft_id,
            NormalizedPageBox(100_000, 200_000, 300_000, 100_000),
            True,
            True,
        ),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    outcome = backend.delete_signature_image(
        page_id,
        SIGNATURE_OVERLAY_ID,
        SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert outcome.state.revision == 2
    assert outcome.cleanup_result is None
    assert outcome.cleanup_warning is not None
    assert "unused case copy could not be removed" in outcome.cleanup_warning
    assert "private native cleanup detail" not in outcome.safe_message
    assert workflow.cleanup_revisions == [2]
    collection = OverlayCollection.from_manifest_document(repository.current.document)
    assert SIGNATURE_OVERLAY_ID not in {record.overlay_id for record in collection.overlays}
    assert SIGNATURE_ASSET_ID not in {asset.asset_id for asset in collection.assets}


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (SignatureImageCleanupStatus.RETAINED, "case copy was retained"),
        (SignatureImageCleanupStatus.NOT_REQUESTED, "case copy was retained"),
        (SignatureImageCleanupStatus.ALREADY_ABSENT, "case copy was already absent"),
        (SignatureImageCleanupStatus.REMOVED, "unused case copy was cleaned up"),
    ),
)
def test_signature_delete_message_matches_exact_cleanup_status(
    status: SignatureImageCleanupStatus,
    expected: str,
) -> None:
    backend, _repository, _workflow = _signature_backend()
    state = backend.open_case(CASE_ID)
    outcome = OrganizerSignatureImageDeleteOutcome(
        state,
        SignatureImageCleanupResult(CASE_ID, SIGNATURE_ASSET_ID, status),
        None,
    )

    assert expected in outcome.safe_message


def test_signature_cancel_cleanup_failure_does_not_strand_organizer_exclusivity() -> None:
    backend, _repository, _workflow = _signature_backend(fail_cancel_cleanup=True)
    state = backend.open_case(CASE_ID)
    page_id = state.pages[0].page_id
    draft = backend.begin_signature_image(
        page_id,
        Path("synthetic-private-signature.png"),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    with pytest.raises(SignatureImageError) as raised:
        backend.cancel_signature_image(draft.draft_id)

    assert raised.value.code is SignatureImageErrorCode.CLEANUP_FAILED
    edited = backend.rotate_clockwise(page_id)
    assert edited.autosave_pending


def test_list_create_open_and_close_expose_only_path_free_state() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=0))

    assert backend.state == OrganizerViewState.empty()
    assert tuple(case.case_id for case in backend.list_cases().cases) == (CASE_ID,)
    created = backend.create_case()

    assert created.case_id == CASE_ID
    assert created.revision == 0
    assert created.pages == ()
    assert backend.close_case() == OrganizerViewState.empty()

    opened = backend.open_case(CASE_ID)

    assert opened.case_id == CASE_ID
    assert opened.revision == 0
    assert backend.close_case() == OrganizerViewState.empty()


def test_case_resource_cleanup_failure_keeps_saved_case_open_and_retryable() -> None:
    attempts: list[int] = []

    def release() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic exact runtime cleanup failed")

    backend, repository, lifecycle, _, _ = _backend(_document(), release_case_resources=release)
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    with pytest.raises(RuntimeError, match="runtime cleanup failed"):
        backend.close_case()
    assert lifecycle.close_calls == 0
    assert backend.state.case_id == CASE_ID and not backend.state.autosave_pending
    assert repository.save_calls == 1
    backend.rotate_clockwise(PAGE_IDS[0])
    assert backend.close_case() == OrganizerViewState.empty()
    assert lifecycle.close_calls == 1 and repository.save_calls == 2
    assert attempts == [1, 1]


def test_startup_recovery_result_is_preserved_for_the_desktop_bridge() -> None:
    recovery = StartupRecoveryResult()
    repository = _Repository(_document(page_count=0))
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        startup_recovery=recovery,
    )

    assert backend.startup_recovery_result is recovery


class _InstalledOcrLanguages:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng", "deu"),
        )


def test_language_management_uses_injected_manager_without_mutating_active_case() -> None:
    manager = OcrLanguageManager(_InstalledOcrLanguages())
    backend, repository, _, _, _ = _backend(
        _document(page_count=1),
        ocr_languages=manager,
    )
    before_state = backend.open_case(CASE_ID)
    before_manifest = copy_manifest_document(repository.current.document)
    before_revision = repository.current.revision

    refreshed = backend.refresh_ocr_languages()
    selected = backend.select_ocr_language_codes(("deu", "eng"))

    assert backend.ocr_language_manager is manager
    assert refreshed.installed_language_codes == frozenset({"eng", "deu"})
    assert selected.selected_language_codes == ("eng", "deu")
    assert backend.state == before_state
    assert repository.current.revision == before_revision
    assert repository.current.document == before_manifest
    assert repository.save_calls == 0


def test_default_language_manager_fails_safely_when_runtime_is_unavailable() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    before_state = backend.open_case(CASE_ID)

    snapshot = backend.refresh_ocr_languages()

    assert snapshot.runtime_status is OcrLanguageRuntimeStatus.UNAVAILABLE
    assert not snapshot.selected_available
    assert backend.state == before_state
    assert repository.save_calls == 0


def test_recovered_manifest_sets_path_free_review_status() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    repository.load_status = ManifestLoadStatus.RECOVERED

    state = backend.open_case(CASE_ID)

    assert state.recovery_review_required
    assert backend.load_status is ManifestLoadStatus.RECOVERED
    assert not OrganizerViewState.empty().recovery_review_required


def test_recovered_migration_sets_path_free_review_status() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    repository.load_status = ManifestLoadStatus.RECOVERED_MIGRATED

    state = backend.open_case(CASE_ID)

    assert state.recovery_review_required
    assert backend.load_status is ManifestLoadStatus.RECOVERED_MIGRATED


def test_page_actions_coalesce_and_preserve_history_after_successful_save() -> None:
    backend, repository, _, _, _ = _backend(_document())
    backend.open_case(CASE_ID)

    backend.rotate_clockwise(PAGE_IDS[0])
    backend.move_page(PAGE_IDS[2], 1)
    changed = backend.remove_page(PAGE_IDS[1])

    assert changed.autosave_pending
    assert changed.can_undo
    assert tuple(page.page_id for page in changed.pages) == (PAGE_IDS[2], PAGE_IDS[0])
    assert tuple(page.rotation_degrees for page in changed.pages) == (0, 90)
    saved = backend.flush_autosave()
    assert repository.save_calls == 1
    assert saved.revision == 1
    assert not saved.autosave_pending
    assert saved.can_undo

    undone = backend.undo()
    assert tuple(page.page_id for page in undone.pages) == (
        PAGE_IDS[2],
        PAGE_IDS[0],
        PAGE_IDS[1],
    )
    assert undone.autosave_pending
    assert undone.can_redo
    retried = backend.retry_autosave()
    assert retried.revision == 2
    assert not retried.autosave_pending


def test_failed_save_keeps_the_latest_edit_pending_for_retry() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError) as raised:
        backend.flush_autosave()

    assert raised.value.code is ManifestRepositoryErrorCode.SAVE_FAILED
    assert backend.state.autosave_pending
    assert backend.state.pages[0].rotation_degrees == 90
    saved = backend.retry_autosave()
    assert saved.revision == 1
    assert not saved.autosave_pending


def test_import_flushes_pending_edits_then_reloads_a_non_undoable_baseline() -> None:
    backend, _, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])

    def cancellation() -> bool:
        return False

    def progress(_event: object, /) -> None:
        return None

    outcome = backend.import_source(
        Path("synthetic.pdf"),
        "Added packet.pdf",
        timeout_seconds=12.5,
        cancellation=cancellation,
        progress=progress,
    )

    assert source_ingest.revision_seen == 1
    assert source_ingest.timeout_seen == 12.5
    assert source_ingest.cancellation_seen is cancellation
    assert source_ingest.progress_seen is progress
    assert outcome.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert not outcome.refresh_required
    assert outcome.committed_import.manifest_revision == 2
    assert outcome.state == backend.state
    assert tuple(page.page_id for page in backend.state.pages) == (
        PAGE_IDS[0],
        IMPORTED_PAGE_ID,
    )
    assert tuple(page.rotation_degrees for page in backend.state.pages) == (90, 0)
    assert not backend.state.can_undo
    assert not backend.state.can_redo
    assert not backend.state.autosave_pending


def test_committed_import_reload_failure_is_reconciled_without_second_ingest() -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    repository.load_failures.append(
        ManifestRepositoryError(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "test.private_reload_failure",
        )
    )

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert outcome.refresh_required
    assert outcome.safe_message == (
        "Import committed. Close and reopen the case before continuing so the organizer can "
        "refresh."
    )
    assert source_ingest.calls == 1
    assert repository.current.revision == 1
    assert backend.state.revision == 0
    assert tuple(page.page_id for page in backend.state.pages) == (PAGE_IDS[0],)

    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert reconciled.committed_import is outcome.committed_import
    assert source_ingest.calls == 1
    assert backend.state.revision == 1
    assert tuple(page.page_id for page in backend.state.pages) == (
        PAGE_IDS[0],
        IMPORTED_PAGE_ID,
    )


def test_committed_import_session_replacement_failure_is_a_closed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    original_session_from_load = organizer_module._session_from_load
    attempts = 0

    def fail_once(
        selected_repository: object,
        loaded: ManifestLoadResult,
        *,
        utc_now: Callable[[], datetime],
    ) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private replacement detail")
        return original_session_from_load(
            cast(ManifestRepository, selected_repository),
            loaded,
            utc_now=utc_now,
        )

    monkeypatch.setattr(organizer_module, "_session_from_load", fail_once)

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert "private replacement detail" not in outcome.safe_message
    assert repository.current.revision == 1
    assert backend.state.revision == 0

    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert source_ingest.calls == 1
    assert attempts == 2
    assert backend.state.revision == 1


def test_reconciliation_requires_the_committed_source_and_page_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    original_load = repository.load

    def load_with_wrong_page_binding(case_id: UUID) -> ManifestLoadResult:
        loaded = original_load(case_id)
        document = loaded.snapshot.document
        pages = cast(list[dict[str, object]], document["pages"])
        pages[-1]["source_id"] = str(PDF_SOURCE_ID)
        return ManifestLoadResult(loaded.status, _snapshot(document))

    monkeypatch.setattr(repository, "load", load_with_wrong_page_binding)

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert backend.state.revision == 0
    assert source_ingest.calls == 1

    monkeypatch.setattr(repository, "load", original_load)
    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert backend.state.revision == 1
    assert source_ingest.calls == 1


def test_repeated_refresh_failure_restarts_from_the_single_committed_import() -> None:
    backend, repository, lifecycle, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    repository.load_failures.extend(
        ManifestRepositoryError(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "test.private_reload_failure",
        )
        for _ in range(2)
    )

    first = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")
    repeated = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert first.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert repeated.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert repeated.committed_import is first.committed_import
    assert source_ingest.calls == 1
    assert repository.current.revision == 1

    assert backend.close_case() == OrganizerViewState.empty()
    restarted = OrganizerBackend(lifecycle, repository, source_ingest, _Thumbnails())
    state = restarted.open_case(CASE_ID)

    assert state.revision == 1
    assert tuple(page.page_id for page in state.pages) == (PAGE_IDS[0], IMPORTED_PAGE_ID)
    assert source_ingest.calls == 1


def test_preview_requests_cover_pdf_and_image_pages_in_order_and_forward_controls() -> None:
    backend, _, _, _, thumbnails = _backend(_document(page_count=1, include_image=True))
    backend.open_case(CASE_ID)
    state = backend.rotate_clockwise(PAGE_IDS[0])
    assert tuple(page.preview_supported for page in state.pages) == (True, True)

    def cancellation() -> bool:
        return False

    def progress(_event: object, /) -> None:
        return None

    assert (
        backend.get_thumbnails(
            maximum_width_pixels=320,
            maximum_height_pixels=240,
            page_ids=(PAGE_IDS[0], IMAGE_PAGE_ID),
            timeout_seconds=4.5,
            cancellation=cancellation,
            progress=progress,
        )
        == ()
    )

    assert len(thumbnails.requests) == 2
    pdf_request, image_request = thumbnails.requests
    assert type(pdf_request) is PdfThumbnailRequest
    assert pdf_request.page_id == PAGE_IDS[0]
    assert pdf_request.maximum_width_pixels == 320
    assert pdf_request.maximum_height_pixels == 240
    assert pdf_request.additional_rotation is PdfRenderRotation.DEGREES_90
    assert type(image_request) is ImagePageTransformRequest
    assert image_request.page_id == IMAGE_PAGE_ID
    assert image_request.maximum_width_pixels == 320
    assert image_request.maximum_height_pixels == 240
    assert image_request.intent == ImageTransformIntent(
        additional_rotation=ImageQuarterTurn.DEGREES_0
    )
    assert thumbnails.timeout_seen == 4.5
    assert thumbnails.cancellation_seen is cancellation
    assert thumbnails.progress_seen is progress


def test_image_preview_request_binds_the_complete_persisted_transform_intent() -> None:
    document = _document(page_count=0, include_image=True)
    operations = cast(list[object], document["page_operations"])
    operations.extend(
        (
            {
                "operation_id": "50000000-0000-4000-8000-000000000201",
                "operation_version": 1,
                "sequence": 1,
                "payload": {
                    "kind": "crop",
                    "page_id": str(IMAGE_PAGE_ID),
                    "box": {
                        "coordinate_space": "normalized_page_millionths_v1",
                        "x": 100_000,
                        "y": 200_000,
                        "width": 700_000,
                        "height": 600_000,
                    },
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000202",
                "operation_version": 1,
                "sequence": 2,
                "payload": {
                    "kind": "rotate",
                    "page_id": str(IMAGE_PAGE_ID),
                    "quarter_turns_clockwise": 3,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000203",
                "operation_version": 2,
                "sequence": 3,
                "payload": {
                    "kind": "deskew",
                    "page_id": str(IMAGE_PAGE_ID),
                    "angle_millidegrees_clockwise": -1_250,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000204",
                "operation_version": 2,
                "sequence": 4,
                "payload": {
                    "kind": "contrast",
                    "page_id": str(IMAGE_PAGE_ID),
                    "factor_millionths": 1_150_000,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000205",
                "operation_version": 2,
                "sequence": 5,
                "payload": {
                    "kind": "background_cleanup",
                    "page_id": str(IMAGE_PAGE_ID),
                    "strength_millionths": 250_000,
                },
            },
        )
    )
    backend, _, _, _, previews = _backend(document)
    backend.open_case(CASE_ID)

    assert backend.get_thumbnails(320, 240, page_ids=(IMAGE_PAGE_ID,)) == ()

    request = previews.requests[0]
    assert type(request) is ImagePageTransformRequest
    assert request.intent == ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 200_000, 700_000, 600_000),
        additional_rotation=ImageQuarterTurn.DEGREES_270,
        deskew_angle_millidegrees_clockwise=-1_250,
        contrast_factor_millionths=1_150_000,
        background_cleanup_strength_millionths=250_000,
    )


def test_pdf_preview_fails_closed_for_raster_only_transform_intent() -> None:
    document = _document(page_count=1)
    cast(list[object], document["page_operations"]).extend(
        (
            {
                "operation_id": "50000000-0000-4000-8000-000000000211",
                "operation_version": 2,
                "sequence": 1,
                "payload": {
                    "kind": "deskew",
                    "page_id": str(PAGE_IDS[0]),
                    "angle_millidegrees_clockwise": 1_000,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000212",
                "operation_version": 2,
                "sequence": 2,
                "payload": {
                    "kind": "contrast",
                    "page_id": str(PAGE_IDS[0]),
                    "factor_millionths": 1_100_000,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000213",
                "operation_version": 2,
                "sequence": 3,
                "payload": {
                    "kind": "background_cleanup",
                    "page_id": str(PAGE_IDS[0]),
                    "strength_millionths": 100_000,
                },
            },
        )
    )
    backend, _, _, _, previews = _backend(document)
    backend.open_case(CASE_ID)

    with pytest.raises(PageOperationError) as caught:
        backend.get_thumbnails(320, 240, page_ids=(PAGE_IDS[0],))

    assert caught.value.code is PageOperationErrorCode.MODEL_INVALID
    assert caught.value.context == "organizer.pdf_scan_transform"
    assert previews.requests == ()


def test_scan_review_exposes_images_only_and_refuses_pending_autosave() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=1, include_image=True))
    state = backend.open_case(CASE_ID)

    assert state.pages[0].scan_review_intent is None
    assert state.pages[1].scan_review_intent == ImageTransformIntent()
    with pytest.raises(ScanReviewError) as pdf_review:
        backend.begin_scan_review(PAGE_IDS[0])
    assert pdf_review.value.code is ScanReviewErrorCode.MEDIA_UNSUPPORTED

    backend.rotate_clockwise(IMAGE_PAGE_ID)
    with pytest.raises(ScanReviewError) as pending:
        backend.begin_scan_review(IMAGE_PAGE_ID)
    assert pending.value.code is ScanReviewErrorCode.SAVE_PENDING


def test_scan_review_suggestion_reset_and_cancel_never_queue_history() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=0, include_image=True))
    backend.open_case(CASE_ID)
    suggestion = ImageTransformIntent(
        deskew_angle_millidegrees_clockwise=1_250,
        contrast_factor_millionths=1_200_000,
    )

    draft = backend.begin_scan_review(IMAGE_PAGE_ID, suggested_intent=suggestion)
    reset = draft.reset_to_original()

    assert draft.suggestion_present
    assert draft.base_intent == ImageTransformIntent()
    assert draft.candidate_intent is suggestion
    assert not backend.state.autosave_pending
    assert repository.save_calls == 0
    assert repository.current.document["page_operations"] == []
    backend.cancel_scan_review(reset)
    assert not backend.state.autosave_pending
    assert backend.begin_scan_review(IMAGE_PAGE_ID).base_intent == ImageTransformIntent()


def test_scan_review_uses_literal_original_and_requires_exact_adjusted_preview() -> None:
    backend, repository, _, _, thumbnails = _backend(_document(page_count=0, include_image=True))
    backend.open_case(CASE_ID)
    candidate = ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 200_000, 700_000, 600_000),
        additional_rotation=ImageQuarterTurn.DEGREES_270,
        deskew_angle_millidegrees_clockwise=-1_250,
        contrast_factor_millionths=1_150_000,
        background_cleanup_strength_millionths=250_000,
    )
    draft = backend.begin_scan_review(IMAGE_PAGE_ID).with_candidate(candidate)

    original_request = ImagePagePreviewRequest(CASE_ID, IMAGE_PAGE_ID, 320, 240)
    thumbnails.results = (_review_original_result(original_request),)
    original = backend.get_scan_review_preview(
        draft,
        ScanReviewPreviewVariant.ORIGINAL,
        320,
        240,
    )

    assert original.variant is ScanReviewPreviewVariant.ORIGINAL
    assert type(thumbnails.requests[0]) is ImagePagePreviewRequest
    assert thumbnails.requests[0].additional_rotation is ImageQuarterTurn.DEGREES_0
    assert thumbnails.timeout_seen == MAX_PAGE_PREVIEW_TIMEOUT_SECONDS
    assert not backend.state.autosave_pending
    with pytest.raises(ScanReviewError) as not_adjusted:
        backend.apply_scan_review(draft)
    assert not_adjusted.value.code is ScanReviewErrorCode.PREVIEW_REQUIRED

    adjusted_request = ImagePageTransformRequest(
        CASE_ID,
        IMAGE_PAGE_ID,
        candidate,
        320,
        240,
    )
    thumbnails.results = (_review_adjusted_result(adjusted_request),)
    adjusted = backend.get_scan_review_preview(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
        timeout_seconds=7.5,
    )
    state = backend.apply_scan_review(draft)

    assert adjusted.variant is ScanReviewPreviewVariant.ADJUSTED
    assert thumbnails.requests == (adjusted_request,)
    assert thumbnails.timeout_seen == 7.5
    assert state.autosave_pending
    assert state.pages[0].scan_review_intent == candidate
    assert repository.save_calls == 0

    saved = backend.flush_autosave()
    payloads = [
        cast(dict[str, object], operation)["payload"]
        for operation in cast(list[object], repository.current.document["page_operations"])
    ]
    assert [cast(dict[str, object], payload)["kind"] for payload in payloads] == [
        "crop",
        "rotate",
        "deskew",
        "contrast",
        "background_cleanup",
    ]
    assert saved.revision == 1
    assert repository.save_calls == 1


def test_scan_review_late_command_failure_keeps_authoritative_state_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, repository, _, _, thumbnails = _backend(_document(page_count=0, include_image=True))
    before = backend.open_case(CASE_ID)
    session = backend._require_session()
    origin_history = session.history
    origin_document = copy_manifest_document(repository.current.document)
    candidate = ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 200_000, 700_000, 600_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=1_250,
        contrast_factor_millionths=1_150_000,
        background_cleanup_strength_millionths=250_000,
    )
    draft = backend.begin_scan_review(IMAGE_PAGE_ID).with_candidate(candidate)
    request = ImagePageTransformRequest(CASE_ID, IMAGE_PAGE_ID, candidate, 320, 240)
    thumbnails.results = (_review_adjusted_result(request),)
    backend.get_scan_review_preview(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
    )
    original_execute = PageCommandHistory.execute
    execute_calls = 0

    def fail_second_execute(
        history: PageCommandHistory,
        command: PageCommand,
    ) -> PageCommandHistory:
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 2:
            raise PageOperationError(
                PageOperationErrorCode.CAPACITY_EXCEEDED,
                "scan_review.atomic_apply",
            )
        return original_execute(history, command)

    monkeypatch.setattr(PageCommandHistory, "execute", fail_second_execute)

    with pytest.raises(PageOperationError) as caught:
        backend.apply_scan_review(draft)

    assert caught.value.code is PageOperationErrorCode.CAPACITY_EXCEEDED
    assert execute_calls == 2
    assert backend._require_session().history is origin_history
    assert backend.state == before
    assert not backend.state.autosave_pending
    assert repository.save_calls == 0
    assert repository.load_calls == 1
    assert repository.current.document == origin_document
    backend.cancel_scan_review(draft)
    assert backend.begin_scan_review(IMAGE_PAGE_ID).base_intent == ImageTransformIntent()


def test_noop_scan_review_apply_queues_nothing_and_closes_review() -> None:
    backend, repository, _, _, thumbnails = _backend(_document(page_count=0, include_image=True))
    backend.open_case(CASE_ID)
    draft = backend.begin_scan_review(IMAGE_PAGE_ID)
    request = ImagePageTransformRequest(
        CASE_ID,
        IMAGE_PAGE_ID,
        draft.candidate_intent,
        320,
        240,
    )
    thumbnails.results = (_review_adjusted_result(request),)
    backend.get_scan_review_preview(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
    )

    state = backend.apply_scan_review(draft)

    assert not state.autosave_pending
    assert repository.save_calls == 0
    assert repository.current.document["page_operations"] == []
    with pytest.raises(ScanReviewError) as closed:
        backend.apply_scan_review(draft)
    assert closed.value.code is ScanReviewErrorCode.NOT_ACTIVE


def test_scan_review_reset_records_neutral_latest_wins_intent_and_rotation_delta() -> None:
    document = _document(page_count=0, include_image=True)
    cast(list[object], document["page_operations"]).extend(
        (
            {
                "operation_id": "50000000-0000-4000-8000-000000000301",
                "operation_version": 1,
                "sequence": 1,
                "payload": {
                    "kind": "crop",
                    "page_id": str(IMAGE_PAGE_ID),
                    "box": {
                        "coordinate_space": "normalized_page_millionths_v1",
                        "x": 100_000,
                        "y": 100_000,
                        "width": 800_000,
                        "height": 800_000,
                    },
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000302",
                "operation_version": 1,
                "sequence": 2,
                "payload": {
                    "kind": "rotate",
                    "page_id": str(IMAGE_PAGE_ID),
                    "quarter_turns_clockwise": 1,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000303",
                "operation_version": 2,
                "sequence": 3,
                "payload": {
                    "kind": "deskew",
                    "page_id": str(IMAGE_PAGE_ID),
                    "angle_millidegrees_clockwise": 1_250,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000304",
                "operation_version": 2,
                "sequence": 4,
                "payload": {
                    "kind": "contrast",
                    "page_id": str(IMAGE_PAGE_ID),
                    "factor_millionths": 1_200_000,
                },
            },
            {
                "operation_id": "50000000-0000-4000-8000-000000000305",
                "operation_version": 2,
                "sequence": 5,
                "payload": {
                    "kind": "background_cleanup",
                    "page_id": str(IMAGE_PAGE_ID),
                    "strength_millionths": 250_000,
                },
            },
        )
    )
    backend, repository, _, _, thumbnails = _backend(document)
    backend.open_case(CASE_ID)
    draft = backend.begin_scan_review(IMAGE_PAGE_ID).reset_to_original()
    request = ImagePageTransformRequest(
        CASE_ID,
        IMAGE_PAGE_ID,
        draft.candidate_intent,
        320,
        240,
    )
    thumbnails.results = (_review_adjusted_result(request),)
    backend.get_scan_review_preview(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
    )

    state = backend.apply_scan_review(draft)
    backend.flush_autosave()

    assert state.pages[0].scan_review_intent == ImageTransformIntent()
    operations = cast(list[dict[str, object]], repository.current.document["page_operations"])
    appended = operations[-5:]
    assert [cast(dict[str, object], operation["payload"])["kind"] for operation in appended] == [
        "crop",
        "rotate",
        "deskew",
        "contrast",
        "background_cleanup",
    ]
    assert cast(dict[str, object], appended[1]["payload"])["quarter_turns_clockwise"] == 3


def test_failed_new_adjusted_preview_revokes_prior_apply_eligibility() -> None:
    backend, _, _, _, thumbnails = _backend(_document(page_count=0, include_image=True))
    backend.open_case(CASE_ID)
    first = backend.begin_scan_review(IMAGE_PAGE_ID).with_candidate(
        ImageTransformIntent(contrast_factor_millionths=1_100_000)
    )
    first_request = ImagePageTransformRequest(
        CASE_ID,
        IMAGE_PAGE_ID,
        first.candidate_intent,
        320,
        240,
    )
    thumbnails.results = (_review_adjusted_result(first_request),)
    backend.get_scan_review_preview(
        first,
        ScanReviewPreviewVariant.ADJUSTED,
        320,
        240,
    )

    second = first.with_candidate(ImageTransformIntent(contrast_factor_millionths=1_200_000))
    thumbnails.results = ()
    with pytest.raises(ScanReviewError) as failed:
        backend.get_scan_review_preview(
            second,
            ScanReviewPreviewVariant.ADJUSTED,
            320,
            240,
        )
    assert failed.value.code is ScanReviewErrorCode.PREVIEW_RESULT_INVALID
    with pytest.raises(ScanReviewError) as revoked:
        backend.apply_scan_review(first)
    assert revoked.value.code is ScanReviewErrorCode.PREVIEW_REQUIRED


def test_successful_unrelated_page_mutation_invalidates_active_scan_review() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=0, include_image=True))
    backend.open_case(CASE_ID)
    draft = backend.begin_scan_review(IMAGE_PAGE_ID)

    backend.rotate_clockwise(IMAGE_PAGE_ID)

    with pytest.raises(ScanReviewError) as stale:
        backend.apply_scan_review(draft)
    assert stale.value.code is ScanReviewErrorCode.NOT_ACTIVE


def test_thumbnail_subset_preserves_organizer_order_and_scales_for_requested_pdfs() -> None:
    backend, _, _, _, thumbnails = _backend(_document(page_count=3))
    backend.open_case(CASE_ID)

    assert backend.get_thumbnails(320, 240, page_ids=(PAGE_IDS[0], PAGE_IDS[2])) == ()
    assert tuple(request.page_id for request in thumbnails.requests) == (
        PAGE_IDS[0],
        PAGE_IDS[2],
    )
    assert thumbnails.timeout_seen == DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS * 2


def test_thumbnail_subset_rejects_duplicates_unknown_order_and_over_cap() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=9))
    state = backend.open_case(CASE_ID)

    cases = (
        ((state.pages[0].page_id, state.pages[0].page_id), PageOperationErrorCode.PAGE_DUPLICATE),
        (
            (state.pages[1].page_id, state.pages[0].page_id),
            PageOperationErrorCode.SEQUENCE_MISMATCH,
        ),
        ((UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),), PageOperationErrorCode.PAGE_UNKNOWN),
        (
            tuple(page.page_id for page in state.pages[: MAX_ORGANIZER_PREVIEW_BATCH_PAGES + 1]),
            PageOperationErrorCode.MODEL_INVALID,
        ),
    )
    for page_ids, expected_code in cases:
        with pytest.raises(PageOperationError) as raised:
            backend.get_thumbnails(320, 240, page_ids=page_ids)
        assert raised.value.code is expected_code


def test_default_thumbnail_deadline_helper_remains_service_capped() -> None:
    assert _default_organizer_preview_timeout(31) == MAX_PAGE_PREVIEW_TIMEOUT_SECONDS


def test_quality_review_binds_saved_revision_and_current_page_intent_without_mutation() -> None:
    quality = _QualityAnalysis()
    backend, repository, _, _, thumbnails = _backend(
        _document(page_count=1, include_image=True),
        quality_analysis=quality,
    )
    state = backend.open_case(CASE_ID)
    backend.rotate_clockwise(IMAGE_PAGE_ID)
    state = backend.flush_autosave()
    before_document = copy_manifest_document(repository.current.document)
    before_save_calls = repository.save_calls

    def cancellation() -> bool:
        return False

    progress: list[QualityAnalysisProgress] = []
    progress_callback = progress.append

    snapshot = backend.analyze_quality(
        expected_case_id=CASE_ID,
        expected_revision=cast(int, state.revision),
        cancellation=cancellation,
        progress=progress_callback,
    )

    assert snapshot.page_ids == (PAGE_IDS[0], IMAGE_PAGE_ID)
    assert len(quality.requests) == 1
    request = quality.requests[0]
    assert request.case_id == CASE_ID
    assert request.manifest_revision == state.revision
    assert tuple(page.kind for page in request.pages) == (
        QualityPageReviewKind.PDF,
        QualityPageReviewKind.IMAGE,
    )
    assert request.pages[0].pdf_rotation is PdfRenderRotation.DEGREES_0
    assert request.pages[1].image_intent is not None
    assert request.pages[1].image_intent.additional_rotation is ImageQuarterTurn.DEGREES_90
    assert quality.cancellation_seen is cancellation
    assert quality.progress_seen is progress_callback
    assert repository.save_calls == before_save_calls
    assert repository.current.document == before_document
    assert thumbnails.requests == ()
    assert backend.state == state


def test_quality_review_rejects_stale_or_unsaved_state_before_analysis() -> None:
    quality = _QualityAnalysis()
    backend, _, _, _, _ = _backend(
        _document(page_count=1),
        quality_analysis=quality,
    )
    state = backend.open_case(CASE_ID)

    with pytest.raises(QualityAnalysisError) as wrong_case:
        backend.analyze_quality(
            expected_case_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            expected_revision=cast(int, state.revision),
        )
    assert wrong_case.value.code is QualityAnalysisErrorCode.STALE

    with pytest.raises(QualityAnalysisError) as wrong_revision:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, state.revision) + 1,
        )
    assert wrong_revision.value.code is QualityAnalysisErrorCode.STALE

    backend.rotate_clockwise(PAGE_IDS[0])
    with pytest.raises(QualityAnalysisError) as save_pending:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, state.revision),
        )
    assert save_pending.value.code is QualityAnalysisErrorCode.SAVE_PENDING
    assert quality.requests == []


def test_quality_review_rejects_reentrant_page_change_and_invalid_service_binding() -> None:
    quality = _QualityAnalysis()
    backend, _, _, _, _ = _backend(
        _document(page_count=1),
        quality_analysis=quality,
    )
    state = backend.open_case(CASE_ID)

    def mutate_on_progress(_progress: QualityAnalysisProgress) -> None:
        backend.rotate_clockwise(PAGE_IDS[0])

    with pytest.raises(QualityAnalysisError) as changed:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, state.revision),
            progress=mutate_on_progress,
        )
    assert changed.value.code is QualityAnalysisErrorCode.STALE

    backend.flush_autosave()
    current = backend.state
    quality.result_factory = lambda request: QualityReviewSnapshot(
        case_id=request.case_id,
        manifest_revision=request.manifest_revision,
        page_ids=(),
        findings=(),
        profile_id=DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id,
        profile_sha256=DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256,
    )
    with pytest.raises(QualityAnalysisError) as invalid:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, current.revision),
        )
    assert invalid.value.code is QualityAnalysisErrorCode.RESULT_INVALID


def test_quality_service_profile_authority_is_validated_and_cannot_be_forged() -> None:
    with pytest.raises(QualityAnalysisError) as bad_profile:
        _backend(
            _document(page_count=1),
            quality_analysis=_QualityAnalysis(profile_id="bad id"),
        )
    assert bad_profile.value.code is QualityAnalysisErrorCode.MODEL_INVALID

    quality = _QualityAnalysis()
    backend, _, _, _, _ = _backend(
        _document(page_count=1),
        quality_analysis=quality,
    )
    state = backend.open_case(CASE_ID)
    quality.result_factory = lambda request: QualityReviewSnapshot(
        case_id=request.case_id,
        manifest_revision=request.manifest_revision,
        page_ids=request.page_ids,
        findings=(),
        profile_id="forged-quality-v1",
        profile_sha256="f" * 64,
    )

    with pytest.raises(QualityAnalysisError) as forged:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, state.revision),
        )

    assert forged.value.code is QualityAnalysisErrorCode.RESULT_INVALID


def test_quality_review_rebuilds_adapter_error_without_forwarding_mutable_message() -> None:
    quality = _QualityAnalysis()
    backend, _, _, _, _ = _backend(
        _document(page_count=1),
        quality_analysis=quality,
    )
    state = backend.open_case(CASE_ID)
    canary = "private OCR text at C:\\private\\source.png"

    def fail(_request: QualityAnalysisRequest) -> object:
        error = QualityAnalysisError(
            QualityAnalysisErrorCode.PREVIEW_FAILED,
            "quality.preview_service",
        )
        error.safe_message = canary
        raise error

    quality.result_factory = fail

    with pytest.raises(QualityAnalysisError) as rebuilt:
        backend.analyze_quality(
            expected_case_id=CASE_ID,
            expected_revision=cast(int, state.revision),
        )

    assert rebuilt.value.code is QualityAnalysisErrorCode.PREVIEW_FAILED
    assert rebuilt.value.safe_message != canary
    assert canary not in str(rebuilt.value)
    assert canary not in repr(rebuilt.value)


def test_restart_reconstructs_saved_projection_without_session_undo() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    backend.flush_autosave()
    backend.close_case()
    lifecycle = _Lifecycle(repository)
    restarted = OrganizerBackend(lifecycle, repository, _SourceIngest(repository), _Thumbnails())

    state = restarted.open_case(CASE_ID)

    assert state.pages[0].rotation_degrees == 90
    assert not state.can_undo
    assert not state.can_redo
    assert not state.autosave_pending


def test_shutdown_leaves_resources_open_until_pending_save_can_be_retried() -> None:
    close_events: list[str] = []

    def close_resources() -> None:
        close_events.append("closed")

    backend, repository, lifecycle, _, _ = _backend(
        _document(page_count=1),
        close_resources=close_resources,
    )
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.shutdown()

    assert close_events == []
    assert backend.state.autosave_pending
    backend.shutdown()
    assert close_events == ["closed"]
    assert lifecycle.active_case is None
    assert backend.state == OrganizerViewState.empty()
    backend.shutdown()
    assert close_events == ["closed"]


def test_shutdown_retries_resource_cleanup_after_the_case_session_is_closed() -> None:
    close_attempts = 0

    def close_resources() -> None:
        nonlocal close_attempts
        close_attempts += 1
        if close_attempts == 1:
            raise RuntimeError("PRIVATE_RESOURCE_CLEANUP_CANARY")

    backend, _, lifecycle, _, _ = _backend(
        _document(page_count=1),
        close_resources=close_resources,
    )
    backend.open_case(CASE_ID)

    with pytest.raises(RuntimeError, match="PRIVATE_RESOURCE_CLEANUP_CANARY"):
        backend.shutdown()

    assert close_attempts == 1
    assert lifecycle.active_case is None
    assert backend.state == OrganizerViewState.empty()

    backend.shutdown()

    assert close_attempts == 2
    assert backend.state == OrganizerViewState.empty()


def test_core_overlay_editor_flushes_page_intent_then_applies_exact_date_atomically() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    rotated = backend.state
    assert rotated.autosave_pending

    editor = backend.begin_core_overlay_tools(PAGE_IDS[0])

    assert editor.manifest_revision == 1
    assert repository.save_calls == 1
    opened = backend.state
    assert not opened.autosave_pending
    raw_date = " 24. VIII. 2033 / exact display \n"
    overlay_id = UUID("6f400000-0000-4000-8000-000000000001")
    edited = editor.create(
        OverlayKind.DATE,
        value=raw_date,
        id_factory=lambda: overlay_id,
    )

    state = backend.apply_core_overlay_tools(edited)

    assert repository.save_calls == 2
    assert state.revision == 2
    assert not state.autosave_pending
    overlays = cast(list[dict[str, object]], repository.current.document["overlays"])
    saved = next(record for record in overlays if record["overlay_id"] == str(overlay_id))
    payload = cast(dict[str, object], saved["payload"])
    assert payload["kind"] == OverlayKind.DATE.value
    assert payload["value"] == raw_date
    assert repository.current.document["page_operations"]


def test_core_overlay_editor_cancel_mutates_nothing_and_releases_exclusive_phase() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    editor = backend.begin_core_overlay_tools(PAGE_IDS[0])
    edited = editor.create(
        OverlayKind.CHECKMARK,
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000002"),
    )

    with pytest.raises(OverlayError) as exclusive:
        backend.rotate_clockwise(PAGE_IDS[0])
    assert exclusive.value.code is OverlayErrorCode.CONCURRENT_CHANGE

    backend.cancel_core_overlay_tools(edited)

    assert repository.save_calls == 0
    assert repository.current.document["overlays"] == []
    state = backend.rotate_clockwise(PAGE_IDS[0])
    assert state.autosave_pending


def test_core_overlay_apply_failure_retains_exact_retry_and_forbids_false_cancel() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    editor = backend.begin_core_overlay_tools(PAGE_IDS[0]).create(
        OverlayKind.INITIALS,
        value="PRIVATE_RETRY_CANARY",
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000003"),
    )
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.apply_core_overlay_tools(editor)

    assert backend.state.autosave_pending
    with pytest.raises(OverlayError) as cancel_pending:
        backend.cancel_core_overlay_tools(editor)
    assert cancel_pending.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    with pytest.raises(OverlayError) as generic_flush:
        backend.flush_autosave()
    assert generic_flush.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    with pytest.raises(OverlayError) as generic_retry:
        backend.retry_autosave()
    assert generic_retry.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 1

    saved = backend.apply_core_overlay_tools(editor)

    assert saved.revision == 1
    assert not saved.autosave_pending
    overlays = cast(list[dict[str, object]], repository.current.document["overlays"])
    assert len(overlays) == 1


def test_core_overlay_retry_replaces_pending_candidate_after_undo_to_opening_state() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    opening = backend.begin_core_overlay_tools(PAGE_IDS[0])
    edited = opening.create(
        OverlayKind.CHECKMARK,
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000004"),
    )
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.apply_core_overlay_tools(edited)

    reverted = edited.undo()
    assert not reverted.changed

    saved = backend.apply_core_overlay_tools(reverted)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.current.document["overlays"] == []


def test_core_overlay_editor_rejects_stale_or_forged_binding_without_queueing() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    editor = backend.begin_core_overlay_tools(PAGE_IDS[0])
    other_document = _document(page_count=1)
    other_document["revision"] = 1
    forged = CoreOverlayToolsState.from_snapshot(_snapshot(other_document), PAGE_IDS[0])

    with pytest.raises(OverlayError) as stale:
        backend.apply_core_overlay_tools(forged)

    assert stale.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    backend.cancel_core_overlay_tools(editor)


def test_core_overlay_editor_rejects_a_prior_same_revision_page_session() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    prior = backend.begin_core_overlay_tools(PAGE_IDS[0])
    backend.cancel_core_overlay_tools(prior)
    current = backend.begin_core_overlay_tools(PAGE_IDS[0])
    stale = prior.create(
        OverlayKind.CROSS,
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000005"),
    )

    with pytest.raises(OverlayError) as rejected:
        backend.apply_core_overlay_tools(stale)

    assert rejected.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    backend.cancel_core_overlay_tools(current)


def test_core_overlay_editor_rejects_copied_authority_with_foreign_page_history() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=2))
    backend.open_case(CASE_ID)
    opening = backend.begin_core_overlay_tools(PAGE_IDS[0])
    foreign_history = opening.history.execute(
        CreateCoreOverlayCommand(
            OverlayKind.TEXT,
            PAGE_IDS[1],
            NormalizedPageBox(10_000, 10_000, 100_000, 50_000),
            "PRIVATE_FOREIGN_PAGE_CANARY",
        ),
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000006"),
    )
    forged = replace(opening)
    object.__setattr__(forged, "history", foreign_history)

    with pytest.raises(OverlayError) as rejected:
        backend.apply_core_overlay_tools(forged)

    assert rejected.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    assert repository.current.document["overlays"] == []
    backend.cancel_core_overlay_tools(opening)


def test_core_overlay_editor_private_authority_rejects_mutated_returned_opening() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=2))
    backend.open_case(CASE_ID)
    opening = backend.begin_core_overlay_tools(PAGE_IDS[0])
    object.__setattr__(opening, "page_id", PAGE_IDS[1])
    forged = opening.create(
        OverlayKind.CROSS,
        id_factory=lambda: UUID("6f400000-0000-4000-8000-000000000007"),
    )

    with pytest.raises(OverlayError) as rejected:
        backend.apply_core_overlay_tools(forged)

    assert rejected.value.code is OverlayErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    assert repository.current.document["overlays"] == []
    backend.abort_core_overlay_tools()


def test_core_overlay_editor_abort_releases_private_authority_for_reopen() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    rejected_presentation = backend.begin_core_overlay_tools(PAGE_IDS[0])
    object.__setattr__(rejected_presentation, "announcement", "PRIVATE_INVALID_ANNOUNCEMENT")

    backend.abort_core_overlay_tools()
    reopened = backend.begin_core_overlay_tools(PAGE_IDS[0])

    assert reopened.editor_session_id != rejected_presentation.editor_session_id
    assert repository.save_calls == 0
    assert repository.current.document["overlays"] == []
    backend.cancel_core_overlay_tools(reopened)


def test_checklist_add_then_confirm_is_saved_as_one_explicit_review() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()
    added = opening.add_pages(
        "application-form",
        (PAGE_IDS[0],),
        clock=lambda: datetime(2026, 8, 6, 10, tzinfo=UTC),
    )
    assignment_id = added.assignment_rows[0].assignment_id
    confirmed = added.confirm_assignment(
        assignment_id,
        clock=lambda: datetime(2026, 8, 6, 11, tzinfo=UTC),
    )

    saved = backend.apply_checklist(confirmed)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.save_calls == 1
    template_ref = cast(dict[str, object], repository.current.document["template_ref"])
    assert template_ref["template_id"] == "generic-portal-application"
    assignments = cast(list[dict[str, object]], repository.current.document["assignments"])
    confirmations = cast(list[dict[str, object]], repository.current.document["confirmations"])
    assert assignments == [
        {
            "assignment_id": str(assignment_id),
            "requirement_id": "application-form",
            "page_ids": [str(PAGE_IDS[0])],
            "origin": "user",
            "status": "user_confirmed",
            "confirmation_id": assignments[0]["confirmation_id"],
            "assigned_at": "2026-08-06T10:00:00Z",
        }
    ]
    assert confirmations[0]["confirmation_code"] == "assignment-reviewed"
    assert confirmations[0]["scope"] == {
        "kind": "assignment",
        "assignment_id": str(assignment_id),
    }
    assert confirmations[0]["confirmation_id"] == assignments[0]["confirmation_id"]
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_checklist_shared_clock_survives_rollback_and_reopens_exact_state() -> None:
    observed_times = iter(
        (
            datetime(2026, 8, 28, 12, tzinfo=UTC),
            datetime(2026, 8, 28, 11, tzinfo=UTC),
            datetime(2026, 8, 28, 10, tzinfo=UTC),
        )
    )
    repository = _Repository(_document())
    workflow = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        clock=lambda: next(observed_times),
    )
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        checklist=workflow,
    )
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()
    added = opening.add_pages("application-form", (PAGE_IDS[0],))
    confirmed = added.confirm_assignment(added.assignment_rows[0].assignment_id)

    saved = backend.apply_checklist(confirmed)

    assignments = cast(list[dict[str, object]], repository.current.document["assignments"])
    confirmations = cast(
        list[dict[str, object]],
        repository.current.document["confirmations"],
    )
    assert assignments[0]["assigned_at"] == "2026-08-28T12:00:00Z"
    assert confirmations[0]["recorded_at"] == "2026-08-28T12:00:00.000001Z"
    assert str(repository.current.document["updated_at"]) == "2026-08-28T12:00:00.000002Z"
    assert saved.revision == 1

    backend.close_case()
    reopened_workflow = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        clock=lambda: datetime(2026, 8, 28, 9, tzinfo=UTC),
    )
    reopened_backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        checklist=reopened_workflow,
    )
    reopened_backend.open_case(CASE_ID)
    reopened = reopened_backend.begin_checklist()

    assert len(reopened.assignment_rows) == 1
    assert reopened.assignment_rows[0].review_state.value == "user_confirmed"
    assert reopened.requirement_rows[0].status.value == "ready"
    assert not reopened.changed
    assert not reopened.can_undo
    assert not reopened.can_redo
    reopened_added = reopened.add_pages("identity-evidence", (PAGE_IDS[1],))
    reopened_confirmed = reopened_added.confirm_assignment(
        reopened_added.assignment_rows[1].assignment_id
    )

    reopened_saved = reopened_backend.apply_checklist(reopened_confirmed)

    reopened_assignments = cast(
        list[dict[str, object]],
        repository.current.document["assignments"],
    )
    reopened_confirmations = cast(
        list[dict[str, object]],
        repository.current.document["confirmations"],
    )
    assert reopened_assignments[1]["assigned_at"] == "2026-08-28T12:00:00.000003Z"
    assert reopened_confirmations[1]["recorded_at"] == "2026-08-28T12:00:00.000004Z"
    assert str(repository.current.document["updated_at"]) == "2026-08-28T12:00:00.000005Z"
    assert reopened_saved.revision == 2


def test_checklist_begin_flushes_page_intent_and_cancel_mutates_nothing() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])

    opening = backend.begin_checklist()

    assert opening.manifest_revision == 1
    assert repository.save_calls == 1
    assert not backend.state.autosave_pending
    backend.cancel_checklist(opening)
    assert repository.save_calls == 1
    assert repository.current.document["template_ref"] is None
    assert repository.current.document["assignments"] == []
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_checklist_save_failure_retains_exact_retry_and_exclusive_authority() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()
    edited = opening.add_pages(
        "identity-evidence",
        (PAGE_IDS[0],),
        clock=lambda: datetime(2026, 8, 6, 10, tzinfo=UTC),
    )
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.apply_checklist(edited)

    assert backend.state.autosave_pending
    with pytest.raises(ChecklistError) as cancel_pending:
        backend.cancel_checklist(edited)
    assert cancel_pending.value.code is ChecklistErrorCode.CONCURRENT_CHANGE
    with pytest.raises(ChecklistError) as unrelated_mutation:
        backend.rotate_clockwise(PAGE_IDS[0])
    assert unrelated_mutation.value.code is ChecklistErrorCode.CONCURRENT_CHANGE
    with pytest.raises(ChecklistError) as generic_retry:
        backend.retry_autosave()
    assert generic_retry.value.code is ChecklistErrorCode.CONCURRENT_CHANGE

    saved = backend.apply_checklist(edited)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.save_calls == 2
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_checklist_rejects_forged_and_prior_same_revision_editor_states() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    prior = backend.begin_checklist()
    forged = replace(
        prior,
        editor_session_id=UUID("71000000-0000-4000-8000-000000000111"),
    )

    with pytest.raises(ChecklistError) as forged_failure:
        backend.apply_checklist(forged)

    assert forged_failure.value.code is ChecklistErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    backend.cancel_checklist(prior)
    current = backend.begin_checklist()

    with pytest.raises(ChecklistError) as stale_failure:
        backend.apply_checklist(prior)

    assert stale_failure.value.code is ChecklistErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    backend.cancel_checklist(current)


def test_checklist_exclusive_phase_blocks_other_case_mutations_until_cancel() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()

    blocked_actions: tuple[Callable[[], object], ...] = (
        lambda: backend.rotate_clockwise(PAGE_IDS[0]),
        lambda: backend.begin_core_overlay_tools(PAGE_IDS[0]),
        backend.flush_autosave,
        backend.reload_case,
        backend.close_case,
        lambda: backend.import_source(Path("synthetic.pdf"), "Synthetic packet"),
    )
    for action in blocked_actions:
        with pytest.raises(ChecklistError) as blocked:
            action()
        assert blocked.value.code is ChecklistErrorCode.CONCURRENT_CHANGE

    assert repository.save_calls == 0
    backend.cancel_checklist(opening)
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_checklist_undo_to_opening_applies_as_noop_and_releases_authority() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()
    reverted = opening.add_pages(
        "address-evidence",
        (PAGE_IDS[0],),
        clock=lambda: datetime(2026, 8, 6, 10, tzinfo=UTC),
    ).undo()
    assert not reverted.changed

    unchanged = backend.apply_checklist(reverted)

    assert unchanged.revision == 0
    assert not unchanged.autosave_pending
    assert repository.save_calls == 0
    assert repository.current.document["template_ref"] is None
    assert repository.current.document["assignments"] == []
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_checklist_retry_replaces_failed_candidate_after_undo_to_opening() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_checklist()
    edited = opening.add_pages(
        "supporting-evidence",
        (PAGE_IDS[0], PAGE_IDS[1]),
        clock=lambda: datetime(2026, 8, 6, 10, tzinfo=UTC),
    )
    repository.fail_saves = 1
    with pytest.raises(ManifestRepositoryError):
        backend.apply_checklist(edited)

    reverted = edited.undo()
    assert not reverted.changed
    saved = backend.apply_checklist(reverted)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.save_calls == 2
    assert repository.current.document["template_ref"] is None
    assert repository.current.document["assignments"] == []


def test_checklist_abort_releases_private_authority_for_reopen() -> None:
    backend, repository, _workflow = _checklist_backend()
    backend.open_case(CASE_ID)
    rejected_presentation = backend.begin_checklist()

    backend.abort_checklist()
    reopened = backend.begin_checklist()

    assert type(reopened) is ChecklistEditorState
    assert reopened.editor_session_id != rejected_presentation.editor_session_id
    assert repository.save_calls == 0
    backend.cancel_checklist(reopened)


def test_portal_constraints_begin_flushes_page_intent_and_cancel_discards_draft() -> None:
    backend, repository, _workflow = _portal_constraints_backend()
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])

    opening = backend.begin_portal_constraints()
    edited = _edited_portal_constraints(opening)

    assert opening.manifest_revision == 1
    assert repository.save_calls == 1
    assert not backend.state.autosave_pending
    backend.cancel_portal_constraints(edited)
    assert repository.save_calls == 1
    assert repository.current.document["constraints"] == {
        "constraints_version": 1,
        "allowed_output_modes": ["combined_pdf"],
        "encryption_policy": "must_be_unencrypted",
    }
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_portal_constraints_apply_uses_organizer_autosave_and_changes_only_subset() -> None:
    backend, repository, _workflow = _portal_constraints_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_portal_constraints()
    edited = _edited_portal_constraints(opening)
    sources_before = copy_manifest_document({"sources": repository.current.document["sources"]})[
        "sources"
    ]
    pages_before = copy_manifest_document({"pages": repository.current.document["pages"]})["pages"]

    saved = backend.apply_portal_constraints(edited)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.save_calls == 1
    persisted = cast(dict[str, object], repository.current.document["constraints"])
    assert persisted["constraints_version"] == 1
    assert persisted["maximum_file_count"] == 4
    assert persisted["maximum_pages_per_file"] == 25
    assert persisted["requested_output_names"] == ["reviewed-packet.pdf"]
    assert repository.current.document["sources"] == sources_before
    assert repository.current.document["pages"] == pages_before
    assert repository.current.document["assignments"] == []


def test_portal_constraints_failed_save_retains_exact_retry_and_exclusivity() -> None:
    backend, repository, _workflow = _portal_constraints_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_portal_constraints()
    edited = _edited_portal_constraints(opening)
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.apply_portal_constraints(edited)

    assert backend.state.autosave_pending
    blocked_actions: tuple[Callable[[], object], ...] = (
        lambda: backend.cancel_portal_constraints(edited),
        lambda: backend.rotate_clockwise(PAGE_IDS[0]),
        backend.begin_checklist,
        backend.retry_autosave,
    )
    for action in blocked_actions:
        with pytest.raises(ConstraintEditorError) as blocked:
            action()
        assert blocked.value.code in {
            ConstraintEditorErrorCode.SESSION_MISMATCH,
            ConstraintEditorErrorCode.WORKFLOW_ACTIVE,
        }

    saved = backend.apply_portal_constraints(edited)

    assert saved.revision == 1
    assert not saved.autosave_pending
    assert repository.save_calls == 2
    assert backend.rotate_clockwise(PAGE_IDS[0]).autosave_pending


def test_portal_constraints_rejects_forged_state_and_abort_allows_reopen() -> None:
    backend, repository, _workflow = _portal_constraints_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_portal_constraints()
    forged = replace(
        opening,
        editor_session_id=UUID("72000000-0000-4000-8000-000000000111"),
    )

    with pytest.raises(ConstraintEditorError) as forged_failure:
        backend.apply_portal_constraints(forged)

    assert forged_failure.value.code is ConstraintEditorErrorCode.SESSION_MISMATCH
    assert repository.save_calls == 0
    backend.abort_portal_constraints()
    reopened = backend.begin_portal_constraints()
    assert reopened.editor_session_id != opening.editor_session_id
    backend.cancel_portal_constraints(reopened)


def test_portal_constraints_unchanged_apply_is_noop_and_releases_authority() -> None:
    backend, repository, _workflow = _portal_constraints_backend()
    backend.open_case(CASE_ID)
    opening = backend.begin_portal_constraints()

    unchanged = backend.apply_portal_constraints(opening)

    assert unchanged.revision == 0
    assert not unchanged.autosave_pending
    assert repository.save_calls == 0
    reopened = backend.begin_portal_constraints()
    assert reopened.editor_session_id != opening.editor_session_id
    backend.cancel_portal_constraints(reopened)


def test_named_case_and_rename_persist_without_changing_page_identity() -> None:
    backend, repository, _lifecycle, _ingest, _thumbnails = _backend(_document())
    created = backend.create_case("Helsinki – hakemus äö")
    assert created.case_name == "Helsinki – hakemus äö"
    source_before = repository.current.document["sources"]
    page_ids = tuple(page.page_id for page in created.pages)
    backend.rotate_clockwise(page_ids[0])
    renamed = backend.rename_case("Updated synthetic case")
    assert renamed.case_name == "Updated synthetic case"
    assert renamed.pages[0].rotation_degrees == 90
    assert repository.current.document["sources"] == source_before
    assert tuple(page.page_id for page in renamed.pages) == page_ids
    assert renamed.case_id == created.case_id
    assert "Updated synthetic case" not in repr(renamed)
    backend.close_case()
    reopened = backend.open_case(CASE_ID)
    assert reopened.case_name == renamed.case_name
    assert reopened.pages[0].rotation_degrees == 90
    assert not reopened.autosave_pending


def test_failed_rename_remains_pending_and_survives_subsequent_page_edit() -> None:
    backend, repository, _lifecycle, _ingest, _thumbnails = _backend(_document())
    created = backend.create_case("Old synthetic name")
    repository.fail_saves = 1
    with pytest.raises(ManifestRepositoryError):
        backend.rename_case("New synthetic name")
    assert repository.current.document["case_name"] == "Old synthetic name"
    assert backend.state.autosave_pending
    assert backend.state.case_name == "New synthetic name"
    backend.rotate_clockwise(created.pages[0].page_id)
    saved = backend.flush_autosave()
    assert saved.case_name == "New synthetic name"
    assert not saved.autosave_pending
    assert saved.pages[0].rotation_degrees == 90
    saved_document = repository.current.document
    assert saved_document["case_name"] == "New synthetic name"
    backend.close_case()
    reopened = backend.open_case(CASE_ID)
    assert reopened.case_name == "New synthetic name"
    assert reopened.pages[0].rotation_degrees == 90
    backend.close_case()


def test_source_display_names_disambiguate_duplicates_and_follow_stable_identity() -> None:
    document = _document(page_count=1, include_image=True)
    sources = cast(list[dict[str, object]], document["sources"])
    for source in sources:
        source["display_name"] = "Ääkköset scan.pdf"
    backend, _repository, _lifecycle, _ingest, _thumbnails = _backend(document)
    original = backend.open_case(CASE_ID)
    assert [page.source_display_name for page in original.pages] == [
        "Ääkköset scan.pdf (import 1)",
        "Ääkköset scan.pdf (import 2)",
    ]
    source_identity = {page.page_id: page.source_display_name for page in original.pages}
    moved = backend.move_page(original.pages[1].page_id, 1)
    assert moved.pages[0].page_id == original.pages[1].page_id
    assert {page.page_id: page.source_display_name for page in moved.pages} == source_identity
    assert "Ääkköset scan.pdf" not in repr(moved)
    backend.close_case()
