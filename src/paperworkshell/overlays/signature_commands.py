"""Pure manifest candidates for signature-image asset placement and removal.

This module deliberately does not open an image, touch case storage, render a
page, or own persistence.  It binds one exact validated manifest snapshot to a
small asset-plus-overlay edit and returns a defensive candidate together with
typed post-commit cleanup intent.  Cleanup intent is never permission to
delete a file before the corresponding manifest candidate is durable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_MANIFEST_CONTAINER_MEMBERS,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
    copy_manifest_document,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    MAX_OVERLAY_ASSETS,
    MAX_OVERLAYS,
    OverlayAssetKind,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayErrorCode,
    OverlayRecord,
    SignatureImageOverlayPayload,
    reject_overlay,
)

_IDENTITY_COLLECTIONS: Final = (
    ("sources", "source_id"),
    ("pages", "page_id"),
    ("page_operations", "operation_id"),
    ("assets", "asset_id"),
    ("overlays", "overlay_id"),
    ("assignments", "assignment_id"),
    ("confirmations", "confirmation_id"),
)


class SignatureAssetDeletePolicy(StrEnum):
    """Explicit treatment of an asset after its selected placement is removed."""

    KEEP_ASSET = "keep_asset"
    REMOVE_IF_UNUSED = "remove_if_unused"


class SignatureAssetCleanupDisposition(StrEnum):
    """Why a command result does or does not request post-commit file cleanup."""

    NONE = "none"
    KEPT_BY_POLICY = "kept_by_policy"
    RETAINED_SHARED = "retained_shared"
    REMOVE_UNUSED = "remove_unused"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureAssetCleanupIntent:
    """One content-free decision about a case asset after manifest commit."""

    disposition: SignatureAssetCleanupDisposition
    asset: OverlayAssetRecord | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.disposition) is not SignatureAssetCleanupDisposition:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_cleanup")
        removing = self.disposition is SignatureAssetCleanupDisposition.REMOVE_UNUSED
        if removing != (type(self.asset) is OverlayAssetRecord):
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_cleanup")

    @property
    def requires_file_removal(self) -> bool:
        """Return whether storage may remove the named asset after durable save."""

        return self.disposition is SignatureAssetCleanupDisposition.REMOVE_UNUSED

    def __repr__(self) -> str:
        return (
            "SignatureAssetCleanupIntent("
            f"disposition={self.disposition.value!r}, details_hidden=True)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CreateSignatureImageCommand:
    """Add one new immutable case asset and one visual signature-image placement."""

    case_id: UUID
    manifest_revision: int
    asset: OverlayAssetRecord = field(repr=False)
    overlay_id: UUID
    page_id: UUID
    box: NormalizedPageBox = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.case_id, "overlay.signature_case")
        _require_revision(self.manifest_revision)
        if (
            type(self.asset) is not OverlayAssetRecord
            or self.asset.kind is not OverlayAssetKind.SIGNATURE_IMAGE
        ):
            reject_overlay(OverlayErrorCode.ASSET_INCOMPATIBLE, "overlay.signature_asset")
        _require_uuid(self.overlay_id, "overlay.signature_overlay")
        _require_uuid(self.page_id, "overlay.signature_page")
        if type(self.box) is not NormalizedPageBox:
            reject_overlay(OverlayErrorCode.BOX_INVALID, "overlay.signature_box")
        if self.overlay_id in {self.case_id, self.page_id, self.asset.asset_id}:
            reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.signature_identifier")

    def __repr__(self) -> str:
        return "CreateSignatureImageCommand(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class DeleteSignatureImageCommand:
    """Delete one exact signature placement under an explicit asset policy."""

    case_id: UUID
    manifest_revision: int
    overlay_id: UUID
    page_id: UUID
    asset_policy: SignatureAssetDeletePolicy

    def __post_init__(self) -> None:
        _require_uuid(self.case_id, "overlay.signature_case")
        _require_revision(self.manifest_revision)
        _require_uuid(self.overlay_id, "overlay.signature_overlay")
        _require_uuid(self.page_id, "overlay.signature_page")
        if type(self.asset_policy) is not SignatureAssetDeletePolicy:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_delete_policy")

    def __repr__(self) -> str:
        policy = (
            self.asset_policy.value
            if type(self.asset_policy) is SignatureAssetDeletePolicy
            else "invalid"
        )
        return f"DeleteSignatureImageCommand(policy={policy!r}, details_hidden=True)"


type SignatureImageCommand = CreateSignatureImageCommand | DeleteSignatureImageCommand


@dataclass(frozen=True, slots=True, repr=False, init=False)
class SignatureImageManifestChange:
    """Defensive same-revision candidate plus post-commit asset cleanup intent."""

    case_id: UUID
    source_revision: int
    _candidate_document: dict[str, object] = field(repr=False)
    cleanup_intent: SignatureAssetCleanupIntent

    def __init__(
        self,
        case_id: UUID,
        source_revision: int,
        candidate_document: Mapping[str, object],
        cleanup_intent: SignatureAssetCleanupIntent,
    ) -> None:
        selected_case = _require_uuid(case_id, "overlay.signature_result_case")
        _require_revision(source_revision)
        if type(cleanup_intent) is not SignatureAssetCleanupIntent:
            reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_result_cleanup")
        candidate = copy_manifest_document(
            candidate_document,
            context="overlay.signature_candidate",
        )
        if (
            candidate.get("case_id") != str(selected_case)
            or candidate.get("revision") != source_revision
        ):
            reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.signature_result_binding")
        object.__setattr__(self, "case_id", selected_case)
        object.__setattr__(self, "source_revision", source_revision)
        object.__setattr__(self, "_candidate_document", candidate)
        object.__setattr__(self, "cleanup_intent", cleanup_intent)

    @property
    def candidate_document(self) -> dict[str, object]:
        """Return a fresh JSON-compatible candidate for compare-and-swap persistence."""

        return copy_manifest_document(
            self._candidate_document,
            context="overlay.signature_candidate",
        )

    def __repr__(self) -> str:
        return (
            "SignatureImageManifestChange("
            f"cleanup={self.cleanup_intent.disposition.value!r}, details_hidden=True)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _ParsedSnapshot:
    document: dict[str, object] = field(repr=False)
    collection: OverlayCollection = field(repr=False)
    known_page_ids: tuple[UUID, ...]
    active_page_ids: tuple[UUID, ...]
    namespace: frozenset[UUID] = field(repr=False)


def apply_signature_image_command(
    snapshot: ManifestSnapshot,
    command: SignatureImageCommand,
) -> SignatureImageManifestChange:
    """Apply one exact typed signature-image command without mutating the snapshot."""

    if type(command) is CreateSignatureImageCommand:
        return create_signature_image_candidate(snapshot, command)
    if type(command) is DeleteSignatureImageCommand:
        return delete_signature_image_candidate(snapshot, command)
    reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_command")


def create_signature_image_candidate(
    snapshot: ManifestSnapshot,
    command: CreateSignatureImageCommand,
) -> SignatureImageManifestChange:
    """Append one new asset and visual placement to an exact manifest snapshot."""

    if type(command) is not CreateSignatureImageCommand:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_create")
    parsed = _parse_bound_snapshot(snapshot, command.case_id, command.manifest_revision)
    if command.page_id not in parsed.active_page_ids:
        reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.signature_page")
    if command.asset.asset_id in parsed.namespace or command.overlay_id in parsed.namespace:
        reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.signature_identifier")
    if (
        len(parsed.collection.assets) >= MAX_OVERLAY_ASSETS
        or len(parsed.collection.overlays) >= MAX_OVERLAYS
    ):
        reject_overlay(OverlayErrorCode.CAPACITY_EXCEEDED, "overlay.signature_capacity")

    z_index = _next_z_index(parsed.collection.overlays, command.page_id)
    record = OverlayRecord(
        command.overlay_id,
        command.page_id,
        command.box,
        z_index,
        SignatureImageOverlayPayload(command.asset.asset_id),
    )
    assets = parsed.collection.assets + (command.asset,)
    overlays = parsed.collection.overlays + (record,)
    _validate_graph(parsed, assets, overlays)
    candidate = parsed.document
    candidate["assets"] = [asset.to_manifest() for asset in assets]
    candidate["overlays"] = [overlay.to_manifest() for overlay in overlays]
    _validate_candidate_namespace(candidate)
    return SignatureImageManifestChange(
        command.case_id,
        command.manifest_revision,
        candidate,
        SignatureAssetCleanupIntent(SignatureAssetCleanupDisposition.NONE),
    )


def delete_signature_image_candidate(
    snapshot: ManifestSnapshot,
    command: DeleteSignatureImageCommand,
) -> SignatureImageManifestChange:
    """Remove one signature placement and derive exact post-commit cleanup intent."""

    if type(command) is not DeleteSignatureImageCommand:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_delete")
    parsed = _parse_bound_snapshot(snapshot, command.case_id, command.manifest_revision)
    if command.page_id not in parsed.active_page_ids:
        reject_overlay(OverlayErrorCode.PAGE_UNKNOWN, "overlay.signature_page")

    position = _overlay_position(parsed.collection.overlays, command.overlay_id)
    selected = parsed.collection.overlays[position]
    if selected.page_id != command.page_id:
        reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.signature_page")
    if type(selected.payload) is not SignatureImageOverlayPayload:
        reject_overlay(OverlayErrorCode.EDIT_KIND_MISMATCH, "overlay.signature_delete")
    asset = _asset_for(parsed.collection.assets, selected.payload.asset_id)
    overlays = parsed.collection.overlays[:position] + parsed.collection.overlays[position + 1 :]

    if command.asset_policy is SignatureAssetDeletePolicy.KEEP_ASSET:
        assets = parsed.collection.assets
        cleanup = SignatureAssetCleanupIntent(SignatureAssetCleanupDisposition.KEPT_BY_POLICY)
    elif any(
        type(record.payload) is SignatureImageOverlayPayload
        and record.payload.asset_id == asset.asset_id
        for record in overlays
    ):
        assets = parsed.collection.assets
        cleanup = SignatureAssetCleanupIntent(SignatureAssetCleanupDisposition.RETAINED_SHARED)
    else:
        assets = tuple(candidate for candidate in parsed.collection.assets if candidate != asset)
        cleanup = SignatureAssetCleanupIntent(
            SignatureAssetCleanupDisposition.REMOVE_UNUSED,
            asset,
        )

    _validate_graph(parsed, assets, overlays)
    candidate = parsed.document
    candidate["assets"] = [item.to_manifest() for item in assets]
    candidate["overlays"] = [item.to_manifest() for item in overlays]
    _validate_candidate_namespace(candidate)
    return SignatureImageManifestChange(
        command.case_id,
        command.manifest_revision,
        candidate,
        cleanup,
    )


def _parse_bound_snapshot(
    snapshot: ManifestSnapshot,
    case_id: UUID,
    revision: int,
) -> _ParsedSnapshot:
    if type(snapshot) is not ManifestSnapshot:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_snapshot")
    if snapshot.case_id != case_id or snapshot.revision != revision:
        reject_overlay(OverlayErrorCode.CONCURRENT_CHANGE, "overlay.signature_binding")
    try:
        document = snapshot.document
    except Exception:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_snapshot")
    document_case = _parse_uuid_text(document.get("case_id"))
    document_revision = document.get("revision")
    if (
        document_case != snapshot.case_id
        or type(document_revision) is not int
        or document_revision != snapshot.revision
        or not 0 <= document_revision <= MAX_SAFE_MANIFEST_INTEGER
    ):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_identity")
    collection = OverlayCollection.from_manifest_document(document)
    known_page_ids = _identity_sequence(document, "pages", "page_id")
    active_page_ids = _uuid_sequence(document.get("page_sequence"))
    namespace = _manifest_namespace(document)
    if snapshot.case_id not in namespace:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_namespace")
    return _ParsedSnapshot(
        document,
        collection,
        known_page_ids,
        active_page_ids,
        frozenset(namespace),
    )


def _validate_graph(
    parsed: _ParsedSnapshot,
    assets: Sequence[OverlayAssetRecord],
    overlays: Sequence[OverlayRecord],
) -> None:
    OverlayCollection(
        assets,
        overlays,
        known_page_ids=parsed.known_page_ids,
        active_page_ids=parsed.active_page_ids,
    )


def _validate_candidate_namespace(candidate: Mapping[str, object]) -> None:
    _manifest_namespace(candidate)


def _manifest_namespace(document: Mapping[str, object]) -> set[UUID]:
    namespace: set[UUID] = set()
    _register(namespace, _parse_uuid_text(document.get("case_id")))
    operations: tuple[dict[str, object], ...] = ()
    for collection_name, identifier_name in _IDENTITY_COLLECTIONS:
        records = _record_collection(document.get(collection_name))
        if collection_name == "page_operations":
            operations = records
        for record in records:
            _register(namespace, _parse_uuid_text(record.get(identifier_name)))
    group_ids: set[UUID] = set()
    for operation in operations:
        payload = operation.get("payload")
        if type(payload) is not dict:
            reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_namespace")
        if payload.get("kind") == "set_group":
            group_id = _parse_uuid_text(payload.get("group_id"))
            if group_id not in group_ids:
                group_ids.add(group_id)
                _register(namespace, group_id)
    export_history = document.get("export_history")
    if type(export_history) is not dict:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_namespace")
    for record in _record_collection(export_history.get("entries")):
        _register(namespace, _parse_uuid_text(record.get("export_id")))
    return namespace


def _identity_sequence(
    document: Mapping[str, object],
    collection_name: str,
    identifier_name: str,
) -> tuple[UUID, ...]:
    return tuple(
        _parse_uuid_text(record.get(identifier_name))
        for record in _record_collection(document.get(collection_name))
    )


def _uuid_sequence(value: object) -> tuple[UUID, ...]:
    if type(value) is not list or len(value) > MAX_MANIFEST_CONTAINER_MEMBERS:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_pages")
    return tuple(_parse_uuid_text(item) for item in value)


def _record_collection(value: object) -> tuple[dict[str, object], ...]:
    if type(value) is not list or len(value) > MAX_MANIFEST_CONTAINER_MEMBERS:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_collection")
    if any(type(record) is not dict for record in value):
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_collection")
    return cast(tuple[dict[str, object], ...], tuple(value))


def _parse_uuid_text(value: object) -> UUID:
    if type(value) is not str:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_identifier")
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        reject_overlay(OverlayErrorCode.MANIFEST_INVALID, "overlay.signature_identifier")
    return parsed


def _require_uuid(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_overlay(OverlayErrorCode.IDENTIFIER_INVALID, context)
    return value


def _require_revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        reject_overlay(OverlayErrorCode.MODEL_INVALID, "overlay.signature_revision")
    return value


def _register(namespace: set[UUID], identifier: UUID) -> None:
    if identifier in namespace:
        reject_overlay(OverlayErrorCode.IDENTIFIER_COLLISION, "overlay.signature_namespace")
    namespace.add(identifier)


def _next_z_index(records: Sequence[OverlayRecord], page_id: UUID) -> int:
    page_indices = [record.z_index for record in records if record.page_id == page_id]
    if not page_indices:
        return 0
    top = max(page_indices)
    if top >= MAX_SAFE_MANIFEST_INTEGER:
        reject_overlay(OverlayErrorCode.Z_INDEX_INVALID, "overlay.signature_z_index")
    return top + 1


def _overlay_position(records: Sequence[OverlayRecord], overlay_id: UUID) -> int:
    for position, record in enumerate(records):
        if record.overlay_id == overlay_id:
            return position
    reject_overlay(OverlayErrorCode.RECORD_UNKNOWN, "overlay.signature_overlay")


def _asset_for(
    assets: Sequence[OverlayAssetRecord],
    asset_id: UUID,
) -> OverlayAssetRecord:
    for asset in assets:
        if asset.asset_id == asset_id:
            return asset
    reject_overlay(OverlayErrorCode.ASSET_UNKNOWN, "overlay.signature_asset")
