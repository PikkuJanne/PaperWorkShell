"""Focused application evidence for the PWS-0305 signature-image workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DISCLAIMER,
    SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS,
    PreparedSignatureImageLease,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
    SignatureImageWorkflowCoordinator,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.signature_images import (
    PreparedSignatureImageSummary,
    SignatureImageCleanupResult,
    SignatureImageCleanupStatus,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
    SignatureImageRasterRequest,
)
from paperworkshell.overlays.models import (
    OverlayAssetRecord,
    OverlayCollection,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.signature_commands import (
    SignatureAssetCleanupIntent,
    SignatureAssetDeletePolicy,
    SignatureImageManifestChange,
)
from paperworkshell.overlays.signature_rasters import SignatureImagePreview

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
PENDING_ID = UUID("7f000000-0000-4000-8000-000000000305")
ASSET_ID = UUID("4f000000-0000-4000-8000-000000000305")
OVERLAY_ID = UUID("6f000000-0000-4000-8000-000000000305")
BOX = NormalizedPageBox(120_000, 220_000, 360_000, 120_000)
PAYLOAD = b"synthetic-private-signature-workflow"


def _snapshot(*, sha256: str = "a" * 64) -> ManifestSnapshot:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    document = cast(dict[str, object], loaded)
    return ManifestSnapshot(CASE_ID, 2, 7, document, sha256, "signature-app-token")


def _summary() -> PreparedSignatureImageSummary:
    return PreparedSignatureImageSummary(
        PENDING_ID,
        CASE_ID,
        ASSET_ID,
        SignatureImageMediaType.PNG,
        len(PAYLOAD),
        hashlib.sha256(PAYLOAD).hexdigest(),
    )


def _preview() -> SignatureImagePreview:
    return SignatureImagePreview(
        ASSET_ID,
        2,
        1,
        8,
        bytes((0, 0, 0, 0, 20, 40, 60, 255)),
        True,
        False,
        True,
        20,
        10,
    )


class _Lease:
    def __init__(self, snapshot: ManifestSnapshot) -> None:
        self.summary = _summary()
        self.manifest_snapshot = snapshot
        self.case_id = CASE_ID
        self.asset_id = ASSET_ID
        self.media_type = SignatureImageMediaType.PNG
        self.byte_size = len(PAYLOAD)
        self.sha256 = hashlib.sha256(PAYLOAD).hexdigest()
        self.handle_value = 41
        self._aborted = False
        self.change: SignatureImageManifestChange | None = None

    @property
    def aborted(self) -> bool:
        return self._aborted

    def commit(
        self,
        change: SignatureImageManifestChange,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImagePlacementCommitResult:
        del cancellation, deadline, progress
        self.change = change
        collection = OverlayCollection.from_manifest_document(change.candidate_document)
        created = collection.overlays[-1]
        assert type(created.payload) is SignatureImageOverlayPayload
        return SignatureImagePlacementCommitResult(
            CASE_ID,
            ASSET_ID,
            created.overlay_id,
            change.source_revision + 1,
        )

    def abort(self) -> None:
        self._aborted = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exception_type, exception, traceback
        self.abort()
        return None


class _Store:
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease
        self.prepared_paths: list[Path] = []
        self.cleaned: list[UUID] = []

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> _Lease:
        del cancellation, deadline, progress
        assert case_id == CASE_ID
        self.prepared_paths.append(source_path)
        return self.lease

    def cleanup_unused_asset(
        self,
        case_id: UUID,
        asset: OverlayAssetRecord,
        *,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None],
    ) -> SignatureImageCleanupResult:
        del cancellation, deadline, progress
        assert case_id == CASE_ID
        asset_id = asset.asset_id
        self.cleaned.append(asset_id)
        return SignatureImageCleanupResult(case_id, asset_id, SignatureImageCleanupStatus.REMOVED)


class _Rasterizer:
    def __init__(self) -> None:
        self.requests: list[SignatureImageRasterRequest] = []

    def preview_prepared(
        self,
        request: SignatureImageRasterRequest,
        lease: PreparedSignatureImageLease,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[SignatureImageProgress], None] | None = None,
    ) -> SignatureImagePreview:
        del deadline, cancellation, progress
        assert lease is not None
        self.requests.append(request)
        assert request.maximum_width_pixels == SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS
        assert request.maximum_height_pixels == SIGNATURE_IMAGE_PREVIEW_EDGE_PIXELS
        return _preview()


def _coordinator(
    snapshot: ManifestSnapshot,
) -> tuple[SignatureImageWorkflowCoordinator, _Store, _Rasterizer, _Lease]:
    lease = _Lease(snapshot)
    store = _Store(lease)
    rasterizer = _Rasterizer()
    coordinator = SignatureImageWorkflowCoordinator(
        store,
        rasterizer,
        id_factory=lambda: OVERLAY_ID,
    )
    return coordinator, store, rasterizer, lease


def _begin(
    coordinator: SignatureImageWorkflowCoordinator,
    snapshot: ManifestSnapshot,
) -> SignatureImagePlacementDraft:
    return coordinator.begin(
        snapshot,
        PAGE_ID,
        Path("private-applicant-name.png"),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )


def test_begin_and_explicit_commit_create_one_typed_visual_placement() -> None:
    snapshot = _snapshot()
    coordinator, store, rasterizer, lease = _coordinator(snapshot)

    draft = _begin(coordinator, snapshot)

    assert draft.case_id == CASE_ID
    assert draft.page_id == PAGE_ID
    assert draft.preview == _preview()
    assert draft.crop_review == "The entire oriented image is used. No additional crop is applied."
    assert "Transparent pixels are preserved" in draft.background_review
    assert len(store.prepared_paths) == 1
    assert len(rasterizer.requests) == 1
    assert not lease.aborted

    result = coordinator.commit(
        SignatureImagePlacementRequest(draft.draft_id, BOX, True, True),
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert result.overlay_id == OVERLAY_ID
    assert result.manifest_revision == snapshot.revision + 1
    assert lease.change is not None
    collection = OverlayCollection.from_manifest_document(lease.change.candidate_document)
    created = collection.overlays[-1]
    assert created.page_id == PAGE_ID
    assert created.box == BOX
    assert created.payload == SignatureImageOverlayPayload(ASSET_ID)
    assert lease.aborted


@pytest.mark.parametrize("disclaimer_confirmed", (False, True))
def test_commit_requires_both_independent_confirmations(disclaimer_confirmed: bool) -> None:
    snapshot = _snapshot()
    coordinator, _store, _rasterizer, lease = _coordinator(snapshot)
    draft = _begin(coordinator, snapshot)

    with pytest.raises(SignatureImageError) as raised:
        coordinator.commit(
            SignatureImagePlacementRequest(
                draft.draft_id,
                BOX,
                disclaimer_confirmed,
                not disclaimer_confirmed,
            ),
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _progress: None,
        )

    assert raised.value.code is SignatureImageErrorCode.CONFIRMATION_REQUIRED
    assert not lease.aborted
    coordinator.cancel(draft.draft_id)
    assert lease.aborted


def test_begin_rejects_nonexact_manifest_lease_and_aborts_private_copy() -> None:
    snapshot = _snapshot()
    mismatched = _snapshot(sha256="b" * 64)
    coordinator, _store, _rasterizer, lease = _coordinator(mismatched)

    with pytest.raises(SignatureImageError) as raised:
        _begin(coordinator, snapshot)

    assert raised.value.code is SignatureImageErrorCode.CASE_CHANGED
    assert lease.aborted


def test_existing_placements_and_delete_cleanup_have_explicit_policy() -> None:
    snapshot = _snapshot()
    coordinator, store, _rasterizer, _lease = _coordinator(snapshot)
    choices = coordinator.placements(snapshot, PAGE_ID)

    assert len(choices) == 1
    assert choices[0].accessible_summary.startswith("Signature image 1 of 1;")
    change = coordinator.delete_candidate(
        snapshot,
        PAGE_ID,
        choices[0].overlay_id,
        SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
    )
    assert change.cleanup_intent.requires_file_removal

    cleanup = coordinator.cleanup_deleted_asset(
        change.cleanup_intent,
        case_id=CASE_ID,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert cleanup is not None
    assert cleanup.status is SignatureImageCleanupStatus.REMOVED
    assert store.cleaned == [cleanup.asset_id]


def test_keep_policy_never_calls_file_cleanup_and_representations_hide_content() -> None:
    snapshot = _snapshot()
    coordinator, store, _rasterizer, _lease = _coordinator(snapshot)
    choice = coordinator.placements(snapshot, PAGE_ID)[0]
    change = coordinator.delete_candidate(
        snapshot,
        PAGE_ID,
        choice.overlay_id,
        SignatureAssetDeletePolicy.KEEP_ASSET,
    )

    assert (
        coordinator.cleanup_deleted_asset(
            change.cleanup_intent,
            case_id=CASE_ID,
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _progress: None,
        )
        is None
    )
    assert store.cleaned == []
    assert "private-applicant-name" not in repr(coordinator)
    assert "qualified electronic signature" in SIGNATURE_IMAGE_DISCLAIMER
    assert repr(_preview()) == (
        "SignatureImagePreview(width_pixels=2, height_pixels=1, details_hidden=True)"
    )


def test_invalid_cleanup_intent_is_rejected_without_store_mutation() -> None:
    snapshot = _snapshot()
    coordinator, store, _rasterizer, _lease = _coordinator(snapshot)

    with pytest.raises(SignatureImageError) as raised:
        coordinator.cleanup_deleted_asset(
            cast(SignatureAssetCleanupIntent, object()),
            case_id=CASE_ID,
            cancellation=lambda: False,
            deadline=lambda: False,
            progress=lambda _progress: None,
        )

    assert raised.value.code is SignatureImageErrorCode.MODEL_INVALID
    assert store.cleaned == []
