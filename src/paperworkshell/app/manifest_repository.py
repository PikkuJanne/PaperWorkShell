"""Toolkit-free synchronous autosave orchestration for case manifests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestAutosaveResult,
    ManifestAutosaveStatus,
    ManifestLoadResult,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
    copy_manifest_document,
    reject_manifest_repository,
)


class UtcClock(Protocol):
    """Trusted UTC clock seam used to produce durable manifest timestamps."""

    def __call__(self) -> datetime:
        """Return one timezone-aware instant."""


class AutosaveResultSink(Protocol):
    """Receive one already durable, path-free autosave result."""

    def __call__(self, result: ManifestAutosaveResult, /) -> None:
        """Observe a durable save outside autosave locks."""


@dataclass(frozen=True, slots=True)
class _PendingEdit:
    generation: int
    base_revision: int
    document: dict[str, object]


class ManifestAutosaveService:
    """Coalesce queued edits and persist the latest state on explicit flush."""

    __slots__ = (
        "_case_id",
        "_closed",
        "_closing",
        "_current",
        "_flush_lock",
        "_generation",
        "_last_updated_at",
        "_on_saved",
        "_owned_base_counts",
        "_pending",
        "_repository",
        "_state_lock",
        "_utc_now",
    )

    def __init__(
        self,
        repository: ManifestRepository,
        case_id: UUID,
        *,
        utc_now: UtcClock = lambda: datetime.now(UTC),
        on_saved: AutosaveResultSink | None = None,
    ) -> None:
        if not isinstance(repository, ManifestRepository):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "autosave.repository",
            )
        if type(case_id) is not UUID or case_id.version != 4:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "autosave.case_id",
            )
        if not callable(utc_now) or (on_saved is not None and not callable(on_saved)):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "autosave.callbacks",
            )
        self._repository = repository
        self._case_id = case_id
        self._utc_now = utc_now
        self._on_saved = on_saved
        self._state_lock = Lock()
        self._flush_lock = Lock()
        self._pending: _PendingEdit | None = None
        self._current: ManifestSnapshot | None = None
        self._owned_base_counts: dict[int, int] = {}
        self._last_updated_at: datetime | None = None
        self._generation = 0
        self._closing = False
        self._closed = False

    @property
    def pending(self) -> bool:
        """Return whether one latest edit still needs durable publication."""

        with self._state_lock:
            return self._pending is not None

    @property
    def closed(self) -> bool:
        """Return whether close successfully drained and sealed the service."""

        with self._state_lock:
            return self._closed

    def queue(self, candidate: Mapping[str, object]) -> int:
        """Freeze and replace the pending candidate without performing storage I/O."""

        frozen = copy_manifest_document(candidate, context="autosave.candidate")
        base_revision = frozen.get("revision")
        if (
            type(base_revision) is not int
            or base_revision < 0
            or base_revision > MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "autosave.candidate_revision",
            )
        with self._state_lock:
            if self._closed or self._closing:
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.AUTOSAVE_CLOSED,
                    "autosave.queue",
                )
            if self._current is not None and base_revision != self._current.revision:
                if (
                    base_revision in self._owned_base_counts
                    and self._current.revision == base_revision + 1
                ):
                    # The host can still hold the pre-save document until flush returns
                    # or its completion callback finishes. Advance that edit only across
                    # the exact revision this service just committed.
                    base_revision = self._current.revision
                else:
                    reject_manifest_repository(
                        ManifestRepositoryErrorCode.REVISION_CONFLICT,
                        "autosave.candidate_revision",
                    )
            if self._generation >= MAX_SAFE_MANIFEST_INTEGER:
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                    "autosave.generation",
                )
            self._generation += 1
            self._pending = _PendingEdit(self._generation, base_revision, frozen)
            return self._generation

    def flush(self) -> ManifestAutosaveResult:
        """Serialize durable saves until no edit newer than the saved state remains."""

        notifications: list[ManifestAutosaveResult] = []
        owned_bases: set[int] = set()
        try:
            with self._flush_lock:
                with self._state_lock:
                    if self._closed:
                        return ManifestAutosaveResult(
                            ManifestAutosaveStatus.CLOSED,
                            self._current,
                        )
                result = self._flush_locked(notifications, owned_bases)
        except BaseException:
            try:
                self._notify_saved(notifications, preserve_active_error=True)
            finally:
                self._release_owned_bases(owned_bases)
            raise
        try:
            self._notify_saved(notifications, preserve_active_error=False)
        finally:
            self._release_owned_bases(owned_bases)
        return result

    def close(self) -> ManifestAutosaveResult:
        """Reject new edits, durably drain the latest pending edit, and seal autosave."""

        notifications: list[ManifestAutosaveResult] = []
        owned_bases: set[int] = set()
        result: ManifestAutosaveResult
        try:
            with self._flush_lock:
                with self._state_lock:
                    if self._closed:
                        return ManifestAutosaveResult(
                            ManifestAutosaveStatus.CLOSED,
                            self._current,
                        )
                    self._closing = True
                try:
                    self._flush_locked(notifications, owned_bases)
                except BaseException:
                    with self._state_lock:
                        self._closing = False
                    raise
                with self._state_lock:
                    self._closing = False
                    self._closed = True
                    result = ManifestAutosaveResult(
                        ManifestAutosaveStatus.CLOSED,
                        self._current,
                    )
        except BaseException:
            try:
                self._notify_saved(notifications, preserve_active_error=True)
            finally:
                self._release_owned_bases(owned_bases)
            raise
        try:
            self._notify_saved(notifications, preserve_active_error=False)
        finally:
            self._release_owned_bases(owned_bases)
        return result

    def _flush_locked(
        self,
        notifications: list[ManifestAutosaveResult],
        owned_bases: set[int],
    ) -> ManifestAutosaveResult:
        latest_result: ManifestAutosaveResult | None = None
        while True:
            with self._state_lock:
                pending = self._pending
                current = self._current
            if pending is None:
                if latest_result is not None:
                    return latest_result
                return ManifestAutosaveResult(
                    ManifestAutosaveStatus.NO_PENDING,
                    current,
                )
            if current is None:
                current = self._load_current()
                with self._state_lock:
                    self._current = current
                continue

            candidate = self._rebase_candidate(pending, current)
            try:
                raw_result = self._repository.save(
                    self._case_id,
                    candidate,
                    expected_revision=current.revision,
                    expected_sha256=current.sha256,
                    expected_cas_token=current.cas_token,
                )
            except ManifestRepositoryError as error:
                if error.code in {
                    ManifestRepositoryErrorCode.REVISION_CONFLICT,
                    ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
                }:
                    with self._state_lock:
                        self._current = None
                raise
            except Exception:
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                    "autosave.save",
                )
            saved = self._require_save_result(raw_result, current)
            latest_result = ManifestAutosaveResult(
                ManifestAutosaveStatus.SAVED,
                saved.snapshot,
                pending.generation,
            )
            with self._state_lock:
                if current.revision not in owned_bases:
                    owned_bases.add(current.revision)
                    self._owned_base_counts[current.revision] = (
                        self._owned_base_counts.get(current.revision, 0) + 1
                    )
                self._current = saved.snapshot
                if self._pending is not None and self._pending.generation == pending.generation:
                    self._pending = None
                elif self._pending is not None and self._pending.base_revision == current.revision:
                    # A newer edit queued while this service-owned save was in flight is
                    # now based on the exact revision just published by this service.
                    self._pending = _PendingEdit(
                        self._pending.generation,
                        saved.snapshot.revision,
                        self._pending.document,
                    )
            notifications.append(latest_result)

    def _release_owned_bases(self, owned_bases: set[int]) -> None:
        with self._state_lock:
            for revision in owned_bases:
                count = self._owned_base_counts.get(revision)
                if count is None or count <= 1:
                    self._owned_base_counts.pop(revision, None)
                else:
                    self._owned_base_counts[revision] = count - 1

    def _load_current(self) -> ManifestSnapshot:
        try:
            raw_result = self._repository.load(self._case_id)
        except ManifestRepositoryError:
            raise
        except Exception:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.load",
            )
        if (
            type(raw_result) is not ManifestLoadResult
            or raw_result.snapshot.case_id != self._case_id
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.load_result",
            )
        return raw_result.snapshot

    def _rebase_candidate(
        self,
        pending: _PendingEdit,
        current: ManifestSnapshot,
    ) -> dict[str, object]:
        if pending.base_revision != current.revision:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                "autosave.base_revision",
            )
        candidate = copy_manifest_document(pending.document, context="autosave.pending")
        schema_version = candidate.get("schema_version")
        if type(schema_version) is int and schema_version > CURRENT_MANIFEST_SCHEMA_VERSION:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.VERSION_UNSUPPORTED,
                "autosave.candidate_version",
            )
        if schema_version != CURRENT_MANIFEST_SCHEMA_VERSION:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MANIFEST_INVALID,
                "autosave.candidate_version",
            )
        if current.revision >= MAX_SAFE_MANIFEST_INTEGER:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.revision",
            )
        current_document = current.document
        created_at = current_document.get("created_at")
        updated_at = current_document.get("updated_at")
        if type(created_at) is not str or type(updated_at) is not str:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.current_timestamps",
            )
        prior_updated_at = _parse_utc_timestamp(updated_at)
        next_updated_at = self._next_updated_at(prior_updated_at)
        candidate["case_id"] = str(self._case_id)
        candidate["revision"] = current.revision + 1
        candidate["created_at"] = created_at
        candidate["updated_at"] = _format_utc_timestamp(next_updated_at)
        return candidate

    def _next_updated_at(self, prior: datetime) -> datetime:
        try:
            observed = self._utc_now()
        except BaseException:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.utc_clock",
            )
        if type(observed) is not datetime or observed.tzinfo is None:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.utc_clock",
            )
        observed = observed.astimezone(UTC)
        floor = prior
        if self._last_updated_at is not None and self._last_updated_at > floor:
            floor = self._last_updated_at
        if observed <= floor:
            try:
                observed = floor + timedelta(microseconds=1)
            except OverflowError:
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                    "autosave.utc_clock",
                )
        self._last_updated_at = observed
        return observed

    def _require_save_result(
        self,
        result: object,
        previous: ManifestSnapshot,
    ) -> ManifestSaveResult:
        if (
            type(result) is not ManifestSaveResult
            or result.status is not ManifestSaveStatus.SAVED
            or result.snapshot.case_id != self._case_id
            or result.snapshot.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION
            or result.snapshot.revision != previous.revision + 1
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
                "autosave.save_result",
            )
        return result

    def _notify_saved(
        self,
        notifications: list[ManifestAutosaveResult],
        *,
        preserve_active_error: bool,
    ) -> None:
        callback: Callable[[ManifestAutosaveResult], None] | None = self._on_saved
        if callback is None:
            return
        for result in notifications:
            try:
                callback(result)
            except BaseException:
                if preserve_active_error:
                    return
                reject_manifest_repository(
                    ManifestRepositoryErrorCode.CALLBACK_FAILED,
                    "autosave.callback",
                )


def _parse_utc_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        reject_manifest_repository(
            ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
            "autosave.current_timestamp",
        )
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
            "autosave.current_timestamp",
        )
    if parsed.tzinfo is None:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.AUTOSAVE_FAILED,
            "autosave.current_timestamp",
        )
    return parsed.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
