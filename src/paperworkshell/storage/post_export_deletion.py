"""Authenticated removal inventory and retained external-export guards."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from paperworkshell.domain.case_lifecycle import (
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.post_export_deletion import (
    MAX_PRESERVED_EXPORT_ARTIFACTS,
    PostExportCaseInventory,
    PostExportDeletionError,
    PostExportDeletionErrorCode,
    reject_post_export_deletion,
)
from paperworkshell.storage._windows import (
    open_handle,
    require_expected_handle,
    require_local_acl_volume,
    require_no_alternate_streams,
)
from paperworkshell.storage.case_lifecycle import _validate_case_manifest_payload
from paperworkshell.storage.export_destination import (
    MAX_EXPORT_DESTINATION_BYTES,
    MAX_EXPORT_DESTINATION_CHUNK_BYTES,
    _normalized,
    _require_committed_identity,
    _validate_destination,
)
from paperworkshell.storage.manifest_codec import MAX_MANIFEST_BYTES
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.workspace import CaseRemovalStatus, CaseWorkspaceManager


@dataclass(frozen=True, slots=True, repr=False)
class PreservedExportArtifact:
    """Trusted committed-output facts; filenames/paths never enter representations."""

    path: Path = field(repr=False)
    byte_size: int
    sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.path, Path)
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_EXPORT_DESTINATION_BYTES
            or type(self.sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            reject_post_export_deletion(PostExportDeletionErrorCode.INVALID, "post_export.artifact")

    def __repr__(self) -> str:
        return f"PreservedExportArtifact(byte_size={self.byte_size})"


class WindowsRetainedExportGuard:
    """Stream-verify and pin all external exports against writes or deletion.

    One bounded chunk is held in memory. Every ancestor remains open without
    delete sharing, and every file remains read-open without write/delete sharing.
    The caller supplies the complete application case-root boundary to exclude.
    """

    def __init__(
        self,
        artifacts: tuple[PreservedExportArtifact, ...],
        *,
        forbidden_root: Path,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> None:
        if (
            type(artifacts) is not tuple
            or not 1 <= len(artifacts) <= MAX_PRESERVED_EXPORT_ARTIFACTS
            or any(type(item) is not PreservedExportArtifact for item in artifacts)
            or not isinstance(forbidden_root, Path)
            or not forbidden_root.is_absolute()
            or len({_normalized(item.path) for item in artifacts}) != len(artifacts)
            or (cancellation is not None and not callable(cancellation))
            or (deadline is not None and not callable(deadline))
        ):
            reject_post_export_deletion(PostExportDeletionErrorCode.INVALID, "post_export.exports")
        self.artifacts = artifacts
        self._forbidden_root = forbidden_root
        self._cancellation = cancellation
        self._deadline = deadline

    @contextmanager
    def guard(
        self,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> Iterator[None]:
        with ExitStack() as stack:
            try:
                pinned_parents: set[str] = set()
                for artifact in self.artifacts:
                    self._check_probes(cancellation, deadline)
                    path = _validate_destination(artifact.path)
                    # Lexical exclusion is followed by authenticated final-name checks.
                    boundary = _normalized(self._forbidden_root).rstrip("\\/")
                    name = _normalized(path)
                    if name == boundary or name.startswith(boundary + "\\"):
                        reject_post_export_deletion(
                            PostExportDeletionErrorCode.EXPORT_UNAVAILABLE,
                            "post_export.case_export",
                        )
                    require_local_acl_volume(path.parent)
                    for parent in reversed(path.parents):
                        key = _normalized(parent)
                        if key in pinned_parents:
                            continue
                        handle = stack.enter_context(open_handle(parent))
                        require_expected_handle(handle, parent, directory=True)
                        require_no_alternate_streams(parent)
                        pinned_parents.add(key)
                    output = stack.enter_context(open_handle(path, read=True))
                    identity = output.info.identity
                    _require_committed_identity(
                        output,
                        path,
                        expected_identity=identity,
                        expected_byte_size=artifact.byte_size,
                    )
                    digest = hashlib.sha256()
                    remaining = artifact.byte_size
                    while remaining:
                        self._check_probes(cancellation, deadline)
                        count = min(remaining, MAX_EXPORT_DESTINATION_CHUNK_BYTES)
                        chunk = output.read_exact(count)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    output.require_eof()
                    if not hmac.compare_digest(digest.hexdigest(), artifact.sha256):
                        reject_post_export_deletion(
                            PostExportDeletionErrorCode.EXPORT_UNAVAILABLE,
                            "post_export.export_changed",
                        )
            except PostExportDeletionError:
                raise
            except Exception:
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.EXPORT_UNAVAILABLE, "post_export.export_guard"
                )
            yield

    def _check_probes(
        self,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> None:
        selected_cancellation = cancellation if cancellation is not None else self._cancellation
        selected_deadline = deadline if deadline is not None else self._deadline
        if selected_cancellation is not None and selected_cancellation():
            reject_post_export_deletion(
                PostExportDeletionErrorCode.CANCELLED, "post_export.cancelled"
            )
        if selected_deadline is not None and selected_deadline():
            reject_post_export_deletion(
                PostExportDeletionErrorCode.TIMED_OUT, "post_export.timed_out"
            )


class WindowsPostExportDeletionStore:
    """Use the existing bounded, no-follow whole-case removal implementation."""

    def __init__(self, manager: CaseWorkspaceManager) -> None:
        self._manager = manager
        self._manifests = WindowsManifestRepository(manager)

    def inspect(self, snapshot: ManifestSnapshot) -> PostExportCaseInventory:
        try:
            self._require_current(snapshot)
            usage = self._manager.measure_usage(snapshot.case_id)
            self._require_current(snapshot)
            document = snapshot.document
            sources = document.get("sources", [])
            pages = document.get("pages", [])
            overlays = document.get("overlays", [])
            assets = document.get("assets", [])
            if (
                type(sources) is not list
                or type(pages) is not list
                or type(overlays) is not list
                or type(assets) is not list
            ):
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.INVALID, "post_export.document"
                )
            return PostExportCaseInventory(
                len(sources),
                len(pages),
                len(overlays),
                len(assets),
                usage.case_bytes,
                usage.cache_bytes,
                usage.temp_bytes,
                usage.entry_count,
            )
        except PostExportDeletionError:
            raise
        except Exception:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.STORAGE_FAILED, "post_export.inspect"
            )

    def delete(self, snapshot: ManifestSnapshot) -> CaseDeletionResult:
        try:
            with self._manager.authenticate_case(snapshot.case_id, for_deletion=True) as authority:

                def validate(payload: bytes) -> None:
                    _validate_case_manifest_payload(payload, snapshot.case_id)
                    if not hmac.compare_digest(
                        hashlib.sha256(payload).hexdigest(), snapshot.sha256
                    ):
                        reject_post_export_deletion(
                            PostExportDeletionErrorCode.REVIEW_STALE, "post_export.delete_changed"
                        )

                report = self._manager.remove_case(
                    authority,
                    manifest_validator=validate,
                    maximum_manifest_bytes=MAX_MANIFEST_BYTES,
                )
            complete = report.status is CaseRemovalStatus.COMPLETE
            return CaseDeletionResult(
                snapshot.case_id,
                CaseDeletionStatus.COMPLETE if complete else CaseDeletionStatus.PARTIAL,
                report.removed_files,
                report.removed_directories,
                report.released_bytes,
                failure_code=None if complete else CaseLifecycleErrorCode.DELETE_FAILED,
            )
        except PostExportDeletionError:
            raise
        except Exception:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.STORAGE_FAILED, "post_export.storage_delete"
            )

    def _require_current(self, snapshot: ManifestSnapshot) -> None:
        current = self._manifests.load(snapshot.case_id).snapshot
        if current.revision != snapshot.revision or current.sha256 != snapshot.sha256:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.REVIEW_STALE, "post_export.storage_changed"
            )
