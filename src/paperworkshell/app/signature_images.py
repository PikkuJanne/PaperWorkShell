"""Toolkit-free coordination for the visual signature-image workflow.

The coordinator retains an opaque prepared-asset lease on its owner thread.
Only a bounded, metadata-free preview and content-free identifiers cross to
presentation code.  Persistence remains split deliberately: the prepared
asset port atomically commits creation, while deletion returns a manifest
candidate for the organizer's existing autosave boundary and performs file
cleanup only after that boundary reports success.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER, ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.signature_images import (
    PreparedSignatureImageSummary,
    SignatureImageCleanupResult,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
    SignatureImageRasterRequest,
    reject_signature_image,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    SignatureImageOverlayPayload,
    reject_overlay,
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

SIGNATURE_IMAGE_DISCLAIMER: Final = (
    "This places a visual image only. It does not create or validate a digital "
    "signature or a qualified electronic signature."
)
SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION: Final = (
    "I reviewed the entire image and its background. Use the entire oriented image; "
    "preserve transparent pixels, and keep any opaque background as part of the image."
)
SIGNATURE_IMAGE_DELETE_KEEP_WORDING: Final = (
    "Remove this placement and keep the case copy for possible reuse."
)
SIGNATURE_IMAGE_DELETE_UNUSED_WORDING: Final = (
    "Remove this placement and remove the case copy only if no other placement uses it."
)
SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS: Final = 512
SIGNATURE_IMAGE_WORKFLOW_TIMEOUT_SECONDS: Final = 120.0
MAX_SIGNATURE_IMAGE_IDENTIFIER_ATTEMPTS: Final = 32

CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SignatureImageProgress], None]


class PreparedSignatureImageLease(Protocol):
    """Opaque owner-thread lease returned by the authenticated asset store."""

    @property
    def summary(self) -> PreparedSignatureImageSummary: ...

    @property
    def manifest_snapshot(self) -> ManifestSnapshot: ...

    @property
    def case_id(self) -> UUID: ...

    @property
    def asset_id(self) -> UUID: ...

    @property
    def media_type(self) -> SignatureImageMediaType: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def handle_value(self) -> int: ...

    def commit(
        self,
        change: SignatureImageManifestChange,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementCommitResult: ...

    def abort(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class SignatureImageAssetStorePort(Protocol):
    """Prepare/commit and post-manifest cleanup surface needed by the app."""

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> PreparedSignatureImageLease: ...

    def cleanup_unused_asset(
        self,
        case_id: UUID,
        asset: OverlayAssetRecord,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImageCleanupResult: ...


class SignatureImagePreviewRasterizerPort(Protocol):
    """Restricted-worker preview surface used before user confirmation."""

    def preview_prepared(
        self,
        request: SignatureImageRasterRequest,
        lease: PreparedSignatureImageLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink | None = None,
    ) -> SignatureImagePreview: ...


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImagePlacementDraft:
    """GUI-safe review value for one retained, uncommitted private asset."""

    draft_id: UUID
    case_id: UUID
    manifest_revision: int
    page_id: UUID
    preview: SignatureImagePreview = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.preview) is not SignatureImagePreview
            or any(
                type(value) is not UUID or value.version != 4
                for value in (self.draft_id, self.case_id, self.page_id)
            )
            or len({self.draft_id, self.case_id, self.page_id, self.preview.asset_id}) != 4
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.draft")

    @property
    def background_review(self) -> str:
        if self.preview.source_has_alpha:
            return tr(
                "Transparent pixels are preserved. Review the checkerboard preview; any opaque "
                "background shown in the image will remain."
            )
        return tr(
            "This image has no transparent pixels. Any visible background is part of the image "
            "and will remain."
        )

    @property
    def crop_review(self) -> str:
        return tr("The entire oriented image is used. No additional crop is applied.")

    def __repr__(self) -> str:
        return "SignatureImagePlacementDraft(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImagePlacementRequest:
    """Explicit reviewed placement submitted from the modal UI."""

    draft_id: UUID
    box: NormalizedPageBox = field(repr=False)
    disclaimer_confirmed: bool
    whole_image_and_background_confirmed: bool

    def __post_init__(self) -> None:
        if (
            type(self.draft_id) is not UUID
            or self.draft_id.version != 4
            or type(self.box) is not NormalizedPageBox
            or type(self.disclaimer_confirmed) is not bool
            or type(self.whole_image_and_background_confirmed) is not bool
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.placement")

    def __repr__(self) -> str:
        return "SignatureImagePlacementRequest(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImagePlacementChoice:
    """One existing placement exposed for keyboard deletion selection."""

    overlay_id: UUID
    page_id: UUID
    box: NormalizedPageBox = field(repr=False)
    position: int
    total: int

    def __post_init__(self) -> None:
        if (
            type(self.overlay_id) is not UUID
            or self.overlay_id.version != 4
            or type(self.page_id) is not UUID
            or self.page_id.version != 4
            or self.overlay_id == self.page_id
            or type(self.box) is not NormalizedPageBox
            or type(self.position) is not int
            or type(self.total) is not int
            or not 1 <= self.position <= self.total
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.choice")

    @property
    def accessible_summary(self) -> str:
        return tr(
            "Signature image {position} of {total}; left {left}; top {top}; "
            "width {width}; height {height}.",
            position=self.position,
            total=self.total,
            left=_percent(self.box.x),
            top=_percent(self.box.y),
            width=_percent(self.box.width),
            height=_percent(self.box.height),
        )

    def __repr__(self) -> str:
        return (
            "SignatureImagePlacementChoice("
            f"position={self.position!r}, total={self.total!r}, details_hidden=True)"
        )


class SignatureImageWorkflowPort(Protocol):
    """Narrow organizer-facing contract for production and fake coordinators."""

    def begin(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        source_path: Path,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementDraft: ...

    def commit(
        self,
        request: SignatureImagePlacementRequest,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementCommitResult: ...

    def cancel(self, draft_id: UUID) -> None: ...

    def placements(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
    ) -> tuple[SignatureImagePlacementChoice, ...]: ...

    def delete_candidate(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        overlay_id: UUID,
        policy: SignatureAssetDeletePolicy,
    ) -> SignatureImageManifestChange: ...

    def cleanup_deleted_asset(
        self,
        intent: SignatureAssetCleanupIntent,
        *,
        case_id: UUID,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImageCleanupResult | None: ...

    def close(self) -> None: ...


@dataclass(slots=True, repr=False)
class _ActiveDraft:
    public: SignatureImagePlacementDraft
    snapshot: ManifestSnapshot = field(repr=False)
    lease: PreparedSignatureImageLease = field(repr=False)

    def __repr__(self) -> str:
        return "_ActiveDraft(details_hidden=True)"


class SignatureImageWorkflowCoordinator:
    """Retain one prepared image and coordinate explicit review and cleanup."""

    __slots__ = ("_active", "_assets", "_id_factory", "_rasterizer")

    def __init__(
        self,
        assets: SignatureImageAssetStorePort,
        rasterizer: SignatureImagePreviewRasterizerPort,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not callable(getattr(assets, "prepare", None))
            or not callable(getattr(assets, "cleanup_unused_asset", None))
            or not callable(getattr(rasterizer, "preview_prepared", None))
            or not callable(id_factory)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.workflow")
        self._assets = assets
        self._rasterizer = rasterizer
        self._id_factory = id_factory
        self._active: _ActiveDraft | None = None

    def begin(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        source_path: Path,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementDraft:
        if (
            type(snapshot) is not ManifestSnapshot
            or type(page_id) is not UUID
            or page_id.version != 4
            or not isinstance(source_path, Path)
            or not callable(cancellation)
            or not callable(deadline)
            or not callable(progress)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.begin")
        if self._active is not None:
            reject_signature_image(
                SignatureImageErrorCode.CONFIRMATION_REQUIRED,
                "signature.active_draft",
            )
        _require_active_page(snapshot, page_id)
        lease: PreparedSignatureImageLease | None = None
        retained = False
        try:
            lease = self._assets.prepare(
                snapshot.case_id,
                source_path,
                cancellation=cancellation,
                deadline=deadline,
                progress=progress,
            )
            summary = _validated_lease_summary(lease, snapshot)
            request = SignatureImageRasterRequest(
                summary.case_id,
                summary.asset_id,
                summary.media_type,
                summary.byte_size,
                summary.sha256,
                SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS,
                SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS,
            )
            preview = self._rasterizer.preview_prepared(
                request,
                lease,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
            )
            if type(preview) is not SignatureImagePreview or preview.asset_id != summary.asset_id:
                reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.preview")
            draft = SignatureImagePlacementDraft(
                summary.pending_id,
                summary.case_id,
                snapshot.revision,
                page_id,
                preview,
            )
            self._active = _ActiveDraft(draft, snapshot, lease)
            retained = True
            return draft
        finally:
            if lease is not None and not retained:
                _abort_no_raise(lease)

    def commit(
        self,
        request: SignatureImagePlacementRequest,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementCommitResult:
        if (
            type(request) is not SignatureImagePlacementRequest
            or not callable(cancellation)
            or not callable(deadline)
            or not callable(progress)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.commit")
        if not request.disclaimer_confirmed or not request.whole_image_and_background_confirmed:
            reject_signature_image(
                SignatureImageErrorCode.CONFIRMATION_REQUIRED,
                "signature.confirmation",
            )
        active = self._require_active(request.draft_id)
        summary = _validated_lease_summary(active.lease, active.snapshot)
        change, overlay_id = self._create_change(active, summary, request.box)
        try:
            result = active.lease.commit(
                change,
                cancellation=cancellation,
                deadline=deadline,
                progress=progress,
            )
            if (
                type(result) is not SignatureImagePlacementCommitResult
                or result.case_id != active.public.case_id
                or result.asset_id != summary.asset_id
                or result.overlay_id != overlay_id
                or result.manifest_revision != active.snapshot.revision + 1
            ):
                reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.commit")
            return result
        finally:
            self._active = None
            _abort_no_raise(active.lease)

    def cancel(self, draft_id: UUID) -> None:
        if type(draft_id) is not UUID or draft_id.version != 4:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.cancel")
        active = self._active
        if active is None:
            return
        if active.public.draft_id != draft_id:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.cancel")
        self._active = None
        active.lease.abort()

    def placements(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
    ) -> tuple[SignatureImagePlacementChoice, ...]:
        if type(snapshot) is not ManifestSnapshot or type(page_id) is not UUID:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.placements")
        _require_active_page(snapshot, page_id)
        collection = OverlayCollection.from_manifest_document(snapshot.document)
        records = tuple(
            sorted(
                (
                    record
                    for record in collection.overlays
                    if record.page_id == page_id
                    and type(record.payload) is SignatureImageOverlayPayload
                ),
                key=lambda record: (record.z_index, record.overlay_id.int),
            )
        )
        return tuple(
            SignatureImagePlacementChoice(
                record.overlay_id,
                page_id,
                record.box,
                position,
                len(records),
            )
            for position, record in enumerate(records, start=1)
        )

    def delete_candidate(
        self,
        snapshot: ManifestSnapshot,
        page_id: UUID,
        overlay_id: UUID,
        policy: SignatureAssetDeletePolicy,
    ) -> SignatureImageManifestChange:
        if type(snapshot) is not ManifestSnapshot:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.delete")
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
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImageCleanupResult | None:
        if (
            type(intent) is not SignatureAssetCleanupIntent
            or type(case_id) is not UUID
            or case_id.version != 4
            or not callable(cancellation)
            or not callable(deadline)
            or not callable(progress)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.cleanup")
        if not intent.requires_file_removal:
            return None
        asset = intent.asset
        if type(asset) is not OverlayAssetRecord:
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.cleanup")
        result = self._assets.cleanup_unused_asset(
            case_id,
            asset,
            cancellation=cancellation,
            deadline=deadline,
            progress=progress,
        )
        if (
            type(result) is not SignatureImageCleanupResult
            or result.case_id != case_id
            or result.asset_id != asset.asset_id
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.cleanup")
        return result

    def close(self) -> None:
        active = self._active
        self._active = None
        if active is not None:
            active.lease.abort()

    def _require_active(self, draft_id: UUID) -> _ActiveDraft:
        active = self._active
        if active is None or active.public.draft_id != draft_id:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.draft")
        return active

    def _create_change(
        self,
        active: _ActiveDraft,
        summary: PreparedSignatureImageSummary,
        box: NormalizedPageBox,
    ) -> tuple[SignatureImageManifestChange, UUID]:
        asset = OverlayAssetRecord(
            summary.asset_id,
            OverlayAssetKind.SIGNATURE_IMAGE,
            f"{summary.asset_id}.bin",
            (
                OverlayAssetMediaType.JPEG
                if summary.media_type is SignatureImageMediaType.JPEG
                else OverlayAssetMediaType.PNG
            ),
            summary.byte_size,
            summary.sha256,
        )
        for _ in range(MAX_SIGNATURE_IMAGE_IDENTIFIER_ATTEMPTS):
            overlay_id = _next_identifier(self._id_factory)
            if overlay_id is None:
                continue
            try:
                change = create_signature_image_candidate(
                    active.snapshot,
                    CreateSignatureImageCommand(
                        active.public.case_id,
                        active.public.manifest_revision,
                        asset,
                        overlay_id,
                        active.public.page_id,
                        box,
                    ),
                )
            except OverlayError as error:
                if error.code is OverlayErrorCode.IDENTIFIER_COLLISION:
                    continue
                raise
            return change, overlay_id
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.identifier")

    def __repr__(self) -> str:
        return f"SignatureImageWorkflowCoordinator(active={self._active is not None!r})"


def _validated_lease_summary(
    lease: PreparedSignatureImageLease,
    snapshot: ManifestSnapshot,
) -> PreparedSignatureImageSummary:
    try:
        summary = lease.summary
        lease_snapshot = lease.manifest_snapshot
        exact = (
            type(summary) is PreparedSignatureImageSummary
            and type(lease_snapshot) is ManifestSnapshot
            and summary.case_id == snapshot.case_id
            and lease_snapshot.case_id == snapshot.case_id
            and lease_snapshot.revision == snapshot.revision
            and lease_snapshot.sha256 == snapshot.sha256
            and lease_snapshot.cas_token == snapshot.cas_token
            and lease.case_id == summary.case_id
            and lease.asset_id == summary.asset_id
            and lease.media_type is summary.media_type
            and lease.byte_size == summary.byte_size
            and lease.sha256 == summary.sha256
            and type(lease.handle_value) is int
            and lease.handle_value > 0
        )
    except BaseException:
        summary = None
        exact = False
    if not exact or type(summary) is not PreparedSignatureImageSummary:
        reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.lease")
    return summary


def _require_active_page(snapshot: ManifestSnapshot, page_id: UUID) -> None:
    document = snapshot.document
    collection = OverlayCollection.from_manifest_document(document)
    raw_sequence = document.get("page_sequence")
    if type(raw_sequence) is not list:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "signature.pages")
    try:
        active_page_ids = tuple(UUID(value) for value in raw_sequence if type(value) is str)
    except ValueError:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "signature.pages")
    if len(active_page_ids) != len(raw_sequence):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "signature.pages")
    del collection
    if page_id not in active_page_ids:
        reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "signature.page")


def _next_identifier(factory: Callable[[], UUID]) -> UUID | None:
    try:
        candidate = factory()
    except BaseException:
        return None
    return candidate if type(candidate) is UUID and candidate.version == 4 else None


def _abort_no_raise(lease: PreparedSignatureImageLease) -> None:
    try:
        lease.abort()
    except BaseException:
        pass


def _percent(value: int) -> str:
    return tr("{value:.4f} percent", value=value / 10_000)
