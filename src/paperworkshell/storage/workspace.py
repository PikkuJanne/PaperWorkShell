"""Secure, UUID-addressed case-workspace layout and cleanup primitives."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from threading import Lock, RLock
from typing import Final
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    current_profile_root,
    open_handle,
    require_expected_handle,
    require_local_acl_volume,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode, reject
from paperworkshell.storage.permissions import (
    PermissionAdapter,
    WindowsPrivateDirectoryPermissions,
)
from paperworkshell.storage.quotas import (
    QuotaArea,
    QuotaReservation,
    QuotaReservationBook,
    WorkspaceQuota,
    WorkspaceUsage,
    validate_quota_request,
)

DEFAULT_CASES_ROOT_PARTS: Final = ("AppData", "Local", "PaperWorkShell", "Cases")
MAX_CASE_ID_COLLISION_ATTEMPTS: Final = 16

_UUID4_RE: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_INTERNAL_COMPONENT_RE: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_WINDOWS_FORBIDDEN_CHARACTERS: Final = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
_TOP_LEVEL_DIRECTORY_NAMES: Final = frozenset({"journal", "originals", "cache", "temp"})
_TOP_LEVEL_FILE_NAMES: Final = frozenset({"manifest.json", "export-history.json"})
_FIXED_CACHE_DIRECTORY_PARTS: Final[frozenset[tuple[str, ...]]] = frozenset(
    {("thumbnails",), ("previews",)}
)
_RESERVATION_REGISTRY_LOCK = Lock()


class WorkspaceArea(StrEnum):
    """Closed set of fixed case-workspace areas."""

    MANIFEST = "manifest"
    JOURNAL = "journal"
    ORIGINALS = "originals"
    CACHE = "cache"
    THUMBNAILS = "thumbnails"
    PREVIEWS = "previews"
    TEMP = "temp"
    EXPORT_HISTORY = "export_history"


class CleanupTarget(StrEnum):
    """Only areas whose contents may be removed by PWS-0101 cleanup."""

    CACHE = "cache"
    TEMP = "temp"


class CleanupStatus(StrEnum):
    """Whether bounded cleanup removed the complete authenticated inventory."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class CaseWorkspacePaths:
    """Fixed paths for one opaque case ID; path fields are hidden from representations."""

    case_id: UUID
    profile_root: Path = field(repr=False)
    cases_root: Path = field(repr=False)
    case_root: Path = field(repr=False)
    manifest: Path = field(repr=False)
    journal: Path = field(repr=False)
    originals: Path = field(repr=False)
    cache: Path = field(repr=False)
    thumbnails: Path = field(repr=False)
    previews: Path = field(repr=False)
    temp: Path = field(repr=False)
    export_history: Path = field(repr=False)

    @property
    def directories(self) -> tuple[Path, ...]:
        """Return the exact directory skeleton in parent-before-child order."""

        return (
            self.case_root,
            self.journal,
            self.originals,
            self.cache,
            self.thumbnails,
            self.previews,
            self.temp,
        )

    def area_path(self, area: WorkspaceArea) -> Path:
        """Resolve one fixed area without accepting arbitrary path text."""

        mapping = {
            WorkspaceArea.MANIFEST: self.manifest,
            WorkspaceArea.JOURNAL: self.journal,
            WorkspaceArea.ORIGINALS: self.originals,
            WorkspaceArea.CACHE: self.cache,
            WorkspaceArea.THUMBNAILS: self.thumbnails,
            WorkspaceArea.PREVIEWS: self.previews,
            WorkspaceArea.TEMP: self.temp,
            WorkspaceArea.EXPORT_HISTORY: self.export_history,
        }
        try:
            return mapping[area]
        except KeyError, TypeError:
            reject(
                WorkspaceErrorCode.PATH_INVALID,
                "workspace.area",
                "The requested case area is not supported.",
            )


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Sanitized outcome for disposable-area cleanup."""

    target: CleanupTarget
    status: CleanupStatus
    removed_files: int
    removed_directories: int
    released_bytes: int
    failure_code: WorkspaceErrorCode | None = None


@dataclass(slots=True, weakref_slot=True)
class _CaseReservationState:
    """Per-case coordination shared by every manager in the broker process."""

    book: QuotaReservationBook
    coordination_lock: RLock = field(default_factory=RLock)
    case_identity: FileIdentity | None = None


_RESERVATION_REGISTRY: WeakValueDictionary[tuple[str, UUID], _CaseReservationState] = (
    WeakValueDictionary()
)


@dataclass(frozen=True, slots=True)
class WorkspaceCapacityReservation:
    """A quota claim bound to one validated disposable case-relative object."""

    case_id: UUID
    area: QuotaArea
    is_directory: bool
    byte_count: int
    entry_count: int
    _case_identity: FileIdentity = field(repr=False)
    _relative_parts: tuple[str, ...] = field(repr=False)
    _reservation: QuotaReservation = field(repr=False)
    _state: _CaseReservationState = field(repr=False)

    @property
    def case_identity(self) -> FileIdentity:
        return self._case_identity

    @property
    def relative_parts(self) -> tuple[str, ...]:
        return self._relative_parts

    @property
    def token(self) -> UUID:
        return self._reservation.token

    @property
    def is_active(self) -> bool:
        with self._state.coordination_lock:
            return self._reservation.is_active

    def __enter__(self) -> WorkspaceCapacityReservation:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def release(self) -> None:
        """Release the underlying process-local quota claim idempotently."""

        with self._state.coordination_lock:
            self._reservation.release()

    @contextmanager
    def publication_guard(
        self,
        *,
        case_identity: FileIdentity,
        relative_parts: tuple[str, ...],
        is_directory: bool,
        byte_count: int,
        entry_count: int,
    ) -> Iterator[None]:
        """Hold quota coordination while a matching authenticated write publishes."""

        validate_quota_request(byte_count=byte_count, entry_count=entry_count)
        with self._state.coordination_lock:
            if not self._reservation.is_active:
                reject(
                    WorkspaceErrorCode.QUOTA_INVALID,
                    "quota.reservation",
                    "The workspace capacity reservation is no longer active.",
                )
            if case_identity != self._case_identity:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "quota.case_identity",
                    "The reserved case changed before the bounded write could publish.",
                )
            if (
                type(relative_parts) is not tuple
                or relative_parts != self._relative_parts
                or type(is_directory) is not bool
                or is_directory is not self.is_directory
            ):
                reject(
                    WorkspaceErrorCode.QUOTA_INVALID,
                    "quota.scope",
                    "The bounded write does not match its workspace reservation scope.",
                )
            if byte_count > self.byte_count or entry_count > self.entry_count:
                reject(
                    WorkspaceErrorCode.QUOTA_EXCEEDED,
                    "quota.claim",
                    "The bounded write exceeds its reserved workspace capacity.",
                )
            yield


@dataclass(frozen=True, slots=True)
class _InventoryEntry:
    relative_parts: tuple[str, ...]
    identity: FileIdentity
    is_directory: bool
    logical_size: int


@dataclass(slots=True)
class _InventoryState:
    entries: list[_InventoryEntry]
    total_bytes: int = 0
    cache_bytes: int = 0
    temp_bytes: int = 0
    maximum_depth_seen: int = 0


def new_case_id() -> UUID:
    """Generate a cryptographically random canonical UUIDv4 case identifier."""

    return uuid4()


def validate_case_id(value: UUID | str) -> UUID:
    """Validate an internal case ID without performing filesystem access."""

    if type(value) is UUID:
        if value.version == 4 and _UUID4_RE.fullmatch(str(value)) is not None:
            return value
        reject(
            WorkspaceErrorCode.CASE_ID_INVALID,
            "case.id",
            "A canonical UUIDv4 case identifier is required.",
        )
    if type(value) is not str or _UUID4_RE.fullmatch(value) is None:
        reject(
            WorkspaceErrorCode.CASE_ID_INVALID,
            "case.id",
            "A canonical UUIDv4 case identifier is required.",
        )
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        reject(
            WorkspaceErrorCode.CASE_ID_INVALID,
            "case.id",
            "A canonical UUIDv4 case identifier is required.",
        )
    return parsed


def validate_internal_component(value: str) -> str:
    """Validate a single ASCII internal component before it reaches the filesystem."""

    if (
        type(value) is not str
        or _INTERNAL_COMPONENT_RE.fullmatch(value) is None
        or value in {".", ".."}
        or value.endswith((".", " "))
        or value.partition(".")[0].casefold() in _WINDOWS_RESERVED_STEMS
    ):
        reject(
            WorkspaceErrorCode.NAME_INVALID,
            "workspace.name",
            "An opaque lowercase internal storage name is required.",
        )
    return value


def validate_case_relative_path(value: str) -> tuple[str, ...]:
    """Lexically validate a case-relative path as a precheck, never as authorization."""

    if type(value) is not str or not value or len(value) > 1024:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "A bounded case-relative path is required.",
        )
    raw = value.replace("/", "\\")
    lowered = raw.casefold()
    if (
        "\x00" in raw
        or raw.startswith(("\\", "\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
        or lowered.startswith("globalroot\\")
        or ":" in raw
    ):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "Absolute, device, network, and alternate-stream paths are not supported.",
        )
    parsed = PureWindowsPath(raw)
    if parsed.is_absolute() or parsed.drive or parsed.root:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "A relative case path is required.",
        )
    raw_parts = raw.split("\\")
    if any(part in {"", ".", ".."} for part in raw_parts):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "Path traversal and empty path components are not supported.",
        )
    parts = tuple(validate_internal_component(part) for part in raw_parts)
    first = parts[0]
    if first not in _TOP_LEVEL_DIRECTORY_NAMES | _TOP_LEVEL_FILE_NAMES:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "The path is outside the fixed case-workspace layout.",
        )
    if first in _TOP_LEVEL_FILE_NAMES and len(parts) != 1:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.relative",
            "Reserved case records cannot contain child paths.",
        )
    return parts


def _validate_external_component(value: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in value)
        or any(
            ord(character) < 32 or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in value
        )
        or value.partition(".")[0].casefold() in _WINDOWS_RESERVED_STEMS
        or len(value.encode("utf-16-le")) // 2 > 240
    ):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "workspace.root",
            "The configured case-storage location is not a safe local path.",
        )


def _preflight_absolute_local_path(value: Path, *, context: str) -> Path:
    raw = os.fspath(value)
    if type(raw) is not str or not raw or len(raw) > 32_767:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            context,
            "A bounded absolute local storage path is required.",
        )
    windows_text = raw.replace("/", "\\")
    lowered = windows_text.casefold()
    if (
        "\x00" in windows_text
        or windows_text.startswith("\\\\")
        or lowered.startswith(("\\\\?\\", "\\\\.\\", "\\??\\"))
        or "globalroot" in lowered.split("\\")
    ):
        reject(
            WorkspaceErrorCode.LOCATION_UNSUPPORTED,
            context,
            "Network, device, and extended-namespace storage paths are not supported.",
        )
    parsed = PureWindowsPath(windows_text)
    if (
        not parsed.is_absolute()
        or not re.fullmatch(r"[A-Za-z]:", parsed.drive)
        or parsed.root != "\\"
        or ":" in windows_text[2:]
    ):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            context,
            "A drive-qualified absolute local storage path is required.",
        )
    raw_components = windows_text[3:].split("\\") if len(windows_text) > 3 else []
    if any(component in {"", ".", ".."} for component in raw_components):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            context,
            "Path traversal and empty path components are not supported.",
        )
    for component in raw_components:
        _validate_external_component(component)
    return Path(os.path.abspath(value))


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_normalized(candidate), _normalized(root))) == _normalized(root)
    except ValueError:
        return False


def _normalized(value: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


class CaseWorkspaceManager:
    """Provision, inspect, reserve, and clean one fixed secure workspace convention."""

    def __init__(
        self,
        *,
        profile_root: Path,
        quota: WorkspaceQuota,
        cases_root: Path | None = None,
        permissions: PermissionAdapter | None = None,
        id_factory: Callable[[], UUID] = new_case_id,
    ) -> None:
        profile = _preflight_absolute_local_path(profile_root, context="profile.root")
        configured_cases = (
            profile.joinpath(*DEFAULT_CASES_ROOT_PARTS)
            if cases_root is None
            else _preflight_absolute_local_path(cases_root, context="cases.root")
        )
        if not _is_within(configured_cases, profile) or configured_cases == profile:
            reject(
                WorkspaceErrorCode.LOCATION_UNSUPPORTED,
                "cases.containment",
                "Case storage must be a dedicated directory inside the current user profile.",
            )
        self._profile_root = profile
        self._cases_root = configured_cases
        self._quota = quota
        self._permissions = permissions or WindowsPrivateDirectoryPermissions()
        self._id_factory = id_factory
        self._reservation_states: dict[tuple[str, UUID], _CaseReservationState] = {}

    @classmethod
    def for_current_user(
        cls,
        *,
        quota: WorkspaceQuota,
        permissions: PermissionAdapter | None = None,
    ) -> CaseWorkspaceManager:
        """Build the production convention from the OS-authenticated current profile."""

        return cls(
            profile_root=current_profile_root(),
            quota=quota,
            permissions=permissions,
        )

    @property
    def profile_root(self) -> Path:
        return self._profile_root

    @property
    def cases_root(self) -> Path:
        return self._cases_root

    def layout(self, case_id: UUID | str) -> CaseWorkspacePaths:
        """Build the fixed layout for a validated internal case ID without filesystem I/O."""

        parsed_id = validate_case_id(case_id)
        case_root = self._cases_root / str(parsed_id)
        return CaseWorkspacePaths(
            case_id=parsed_id,
            profile_root=self._profile_root,
            cases_root=self._cases_root,
            case_root=case_root,
            manifest=case_root / "manifest.json",
            journal=case_root / "journal",
            originals=case_root / "originals",
            cache=case_root / "cache",
            thumbnails=case_root / "cache" / "thumbnails",
            previews=case_root / "cache" / "previews",
            temp=case_root / "temp",
            export_history=case_root / "export-history.json",
        )

    def resolve_case_relative(self, case_id: UUID | str, relative: str) -> Path:
        """Resolve a lexically valid relative path beneath one opaque case root."""

        layout = self.layout(case_id)
        parts = validate_case_relative_path(relative)
        candidate = layout.case_root.joinpath(*parts)
        if not _is_within(candidate, layout.case_root) or candidate == layout.case_root:
            reject(
                WorkspaceErrorCode.PATH_INVALID,
                "workspace.relative",
                "The relative path is outside the case workspace.",
            )
        return candidate

    def provision(self, case_id: UUID | str | None = None) -> CaseWorkspacePaths:
        """Create and independently verify a new private directory skeleton."""

        explicit = case_id is not None
        with self._pinned_cases_root(create=True):
            for _ in range(MAX_CASE_ID_COLLISION_ATTEMPTS):
                selected = (
                    validate_case_id(self._id_factory())
                    if case_id is None
                    else validate_case_id(case_id)
                )
                layout = self.layout(selected)
                if os.path.lexists(layout.case_root):
                    if explicit:
                        reject(
                            WorkspaceErrorCode.ALREADY_EXISTS,
                            "case.create",
                            "A case with this internal identifier already exists.",
                        )
                    continue
                try:
                    self._provision_layout(layout)
                except WorkspaceError as error:
                    if (
                        not explicit
                        and error.code is WorkspaceErrorCode.ALREADY_EXISTS
                        and error.context == "case.root"
                    ):
                        continue
                    raise
                return layout
        reject(
            WorkspaceErrorCode.ALREADY_EXISTS,
            "case.create",
            "PaperWorkShell could not allocate a unique case identifier.",
        )

    def measure_usage(self, case_id: UUID | str) -> WorkspaceUsage:
        """Measure logical bytes with a bounded, handle-pinned, no-follow inventory."""

        layout = self.layout(case_id)
        usage, _ = self._measure_usage_and_identity(layout)
        return usage

    def reserve_capacity(
        self,
        case_id: UUID | str,
        area: QuotaArea,
        *,
        relative_path: str,
        is_directory: bool,
        byte_count: int,
        entry_count: int = 1,
    ) -> WorkspaceCapacityReservation:
        """Reserve a bounded cache/temp write within this storage broker process."""

        parsed_id = validate_case_id(case_id)
        if type(area) is not QuotaArea:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.area",
                "Capacity may be reserved only for cache or temporary storage.",
            )
        if type(is_directory) is not bool:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.object_type",
                "A capacity reservation requires an explicit storage-object type.",
            )
        validate_quota_request(byte_count=byte_count, entry_count=entry_count)
        parts = validate_case_relative_path(relative_path)
        if len(parts) < 2 or parts[0] != area.value:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.path",
                "Capacity may be reserved only inside its selected disposable area.",
            )
        intended_depth = len(parts) if is_directory else len(parts) - 1
        if intended_depth > self._quota.maximum_depth:
            reject(
                WorkspaceErrorCode.QUOTA_EXCEEDED,
                "quota.depth",
                "The requested object would exceed the configured workspace depth.",
            )
        layout = self.layout(parsed_id)
        with self._coordinated_reservation_state(parsed_id) as (key, state):
            usage, identity = self._measure_usage_and_identity(layout)
            self._bind_case_identity(state, identity)
            reservation = state.book.reserve(
                usage,
                area,
                byte_count=byte_count,
                entry_count=entry_count,
            )
            self._reservation_states[key] = state
        return WorkspaceCapacityReservation(
            case_id=parsed_id,
            area=area,
            is_directory=is_directory,
            byte_count=byte_count,
            entry_count=entry_count,
            _case_identity=identity,
            _relative_parts=parts,
            _reservation=reservation,
            _state=state,
        )

    def cleanup_disposable(
        self,
        case_id: UUID | str,
        target: CleanupTarget,
    ) -> CleanupReport:
        """Remove one authenticated cache/temp inventory without deleting its root."""

        if type(target) is not CleanupTarget:
            reject(
                WorkspaceErrorCode.PATH_INVALID,
                "cleanup.target",
                "Cleanup is limited to a case cache or temporary area.",
            )
        layout = self.layout(case_id)
        target_path = layout.cache if target is CleanupTarget.CACHE else layout.temp
        quota_area = QuotaArea.CACHE if target is CleanupTarget.CACHE else QuotaArea.TEMP
        with self._coordinated_reservation_state(layout.case_id) as (_, reservation_state):
            identity = self._authenticate_case_identity(layout)
            self._bind_case_identity(reservation_state, identity)
            if reservation_state.book.has_reservations_for(quota_area):
                reject(
                    WorkspaceErrorCode.CLEANUP_FAILED,
                    "cleanup.active_reservation",
                    "Disposable cleanup cannot run while the selected area is in use.",
                )
            with self._pinned_layout(layout, target_path=target_path) as target_handle:
                with ExitStack() as stack:
                    fixed_cache_identities: dict[tuple[str, ...], FileIdentity] = {}
                    if target is CleanupTarget.CACHE:
                        for fixed_parts in sorted(_FIXED_CACHE_DIRECTORY_PARTS):
                            path = target_path.joinpath(*fixed_parts)
                            handle = stack.enter_context(open_handle(path))
                            require_expected_handle(handle, path, directory=True)
                            verify_private_acl(handle)
                            require_no_alternate_streams(path)
                            fixed_cache_identities[fixed_parts] = handle.info.identity
                    inventory_state = _InventoryState(entries=[])
                    self._inventory_directory(
                        target_path,
                        target_handle,
                        relative_parts=(),
                        depth=0,
                        state=inventory_state,
                        top_level=False,
                    )
                    inventory = {entry.relative_parts: entry for entry in inventory_state.entries}
                    for fixed_parts, fixed_identity in fixed_cache_identities.items():
                        entry = inventory.get(fixed_parts)
                        if (
                            entry is None
                            or not entry.is_directory
                            or entry.identity != fixed_identity
                        ):
                            reject(
                                WorkspaceErrorCode.IDENTITY_CHANGED,
                                "cleanup.cache_layout",
                                "A fixed cache directory changed during cleanup inventory.",
                            )
                    return self._delete_inventory(
                        target,
                        target_path,
                        target_handle,
                        inventory_state.entries,
                    )

    @contextmanager
    def _coordinated_reservation_state(
        self,
        case_id: UUID,
    ) -> Iterator[tuple[tuple[str, UUID], _CaseReservationState]]:
        key = (_normalized(self._cases_root), case_id)
        while True:
            with _RESERVATION_REGISTRY_LOCK:
                state = _RESERVATION_REGISTRY.get(key)
                if state is None:
                    state = _CaseReservationState(book=QuotaReservationBook(self._quota))
                    _RESERVATION_REGISTRY[key] = state
            with state.coordination_lock:
                with _RESERVATION_REGISTRY_LOCK:
                    if _RESERVATION_REGISTRY.get(key) is not state:
                        continue
                if state.book.quota != self._quota:
                    if state.case_identity is None and not state.book.has_reservations:
                        replacement = _CaseReservationState(book=QuotaReservationBook(self._quota))
                        with _RESERVATION_REGISTRY_LOCK:
                            if _RESERVATION_REGISTRY.get(key) is state:
                                _RESERVATION_REGISTRY[key] = replacement
                        continue
                    reject(
                        WorkspaceErrorCode.QUOTA_INVALID,
                        "quota.configuration",
                        "One case cannot use conflicting workspace quota policies.",
                    )
                yield key, state
                return

    @staticmethod
    def _bind_case_identity(
        state: _CaseReservationState,
        identity: FileIdentity,
    ) -> None:
        if state.case_identity is None:
            state.case_identity = identity
        elif state.case_identity != identity:
            if state.book.has_reservations:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "quota.case_identity",
                    "The reserved case changed while capacity claims remain active.",
                )
            state.case_identity = identity

    def _authenticate_case_identity(self, layout: CaseWorkspacePaths) -> FileIdentity:
        with self._pinned_layout(layout) as case_handle:
            require_no_alternate_streams(layout.case_root)
            return case_handle.info.identity

    def _measure_usage_and_identity(
        self,
        layout: CaseWorkspacePaths,
    ) -> tuple[WorkspaceUsage, FileIdentity]:
        with self._pinned_layout(layout) as case_handle:
            state = _InventoryState(entries=[])
            self._inventory_directory(
                layout.case_root,
                case_handle,
                relative_parts=(),
                depth=0,
                state=state,
                top_level=True,
            )
            return (
                WorkspaceUsage(
                    case_bytes=state.total_bytes,
                    cache_bytes=state.cache_bytes,
                    temp_bytes=state.temp_bytes,
                    entry_count=len(state.entries),
                    maximum_depth_seen=state.maximum_depth_seen,
                ),
                case_handle.info.identity,
            )

    def _provision_layout(self, layout: CaseWorkspacePaths) -> None:
        created: list[tuple[Path, FileIdentity]] = []
        try:
            with ExitStack() as stack:
                for index, directory in enumerate(layout.directories):
                    verification = self._permissions.create_private_directory(
                        directory,
                        context="case.root" if index == 0 else "case.layout",
                    )
                    created.append((directory, verification.identity))
                    handle = stack.enter_context(open_handle(directory))
                    require_expected_handle(
                        handle,
                        directory,
                        directory=True,
                        expected_identity=verification.identity,
                    )
                    verify_private_acl(handle)
                    require_no_alternate_streams(directory)
                self._verify_layout(layout, created)
        except BaseException:
            self._rollback_empty_layout(created)
            raise

    def _verify_layout(
        self,
        layout: CaseWorkspacePaths,
        expected: list[tuple[Path, FileIdentity]],
    ) -> None:
        for directory, identity in expected:
            verification = self._permissions.verify_private_directory(
                directory,
                context="case.layout",
            )
            if verification.identity != identity:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "case.layout",
                    "A case directory changed while its layout was being verified.",
                )
        if os.path.lexists(layout.manifest) or os.path.lexists(layout.export_history):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "case.reserved_records",
                "Workspace provisioning cannot publish authoritative case records.",
            )

    @staticmethod
    def _rollback_empty_layout(created: list[tuple[Path, FileIdentity]]) -> None:
        for path, identity in reversed(created):
            try:
                with open_handle(path, delete=True) as handle:
                    require_expected_handle(
                        handle,
                        path,
                        directory=True,
                        expected_identity=identity,
                    )
                    handle.mark_for_deletion()
            except OSError, WorkspaceError:
                return

    @contextmanager
    def _pinned_cases_root(self, *, create: bool) -> Iterator[WindowsHandle]:
        require_local_acl_volume(self._profile_root)
        with ExitStack() as stack:
            profile_handle = stack.enter_context(open_handle(self._profile_root))
            require_expected_handle(profile_handle, self._profile_root, directory=True)
            current = self._profile_root
            relative = self._cases_root.relative_to(self._profile_root)
            current_handle = profile_handle
            for component in relative.parts:
                current = current / component
                expected_identity: FileIdentity | None = None
                if not os.path.lexists(current):
                    if not create:
                        reject(
                            WorkspaceErrorCode.PATH_INVALID,
                            "cases.root",
                            "The configured case-storage root does not exist.",
                        )
                    verification = self._permissions.create_private_directory(
                        current,
                        context="cases.root",
                    )
                    expected_identity = verification.identity
                current_handle = stack.enter_context(open_handle(current))
                require_expected_handle(
                    current_handle,
                    current,
                    directory=True,
                    expected_identity=expected_identity,
                )
            verification = self._permissions.verify_private_directory(
                self._cases_root,
                context="cases.root",
            )
            if verification.identity != current_handle.info.identity:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "cases.root",
                    "The case-storage root changed during verification.",
                )
            yield current_handle

    @contextmanager
    def _pinned_layout(
        self,
        layout: CaseWorkspacePaths,
        *,
        target_path: Path | None = None,
    ) -> Iterator[WindowsHandle]:
        with self._pinned_cases_root(create=False):
            with ExitStack() as stack:
                case_handle = stack.enter_context(open_handle(layout.case_root))
                require_expected_handle(case_handle, layout.case_root, directory=True)
                verify_private_acl(case_handle)
                selected = case_handle
                if target_path is not None:
                    selected = stack.enter_context(open_handle(target_path))
                    require_expected_handle(selected, target_path, directory=True)
                    verify_private_acl(selected)
                yield selected

    def _inventory_directory(
        self,
        path: Path,
        handle: WindowsHandle,
        *,
        relative_parts: tuple[str, ...],
        depth: int,
        state: _InventoryState,
        top_level: bool,
    ) -> None:
        require_expected_handle(handle, path, directory=True)
        verify_private_acl(handle)
        require_no_alternate_streams(path)
        if depth > self._quota.maximum_depth:
            reject(
                WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                "inventory.depth",
                "The case workspace exceeds its configured inventory depth.",
            )
        state.maximum_depth_seen = max(state.maximum_depth_seen, depth)
        try:
            names: list[str] = []
            remaining_entries = self._quota.maximum_entries - len(state.entries)
            with os.scandir(path) as iterator:
                for scan_entry in iterator:
                    if len(names) >= remaining_entries:
                        reject(
                            WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                            "inventory.entries",
                            "The case workspace exceeds its configured inventory size.",
                        )
                    names.append(scan_entry.name)
            names.sort(key=str.casefold)
        except OSError:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "inventory.directory",
                "The case workspace changed during its bounded inventory.",
            )
        for name in names:
            validate_internal_component(name)
            if top_level and name not in _TOP_LEVEL_DIRECTORY_NAMES | _TOP_LEVEL_FILE_NAMES:
                reject(
                    WorkspaceErrorCode.PATH_INVALID,
                    "inventory.layout",
                    "The case contains an object outside the fixed workspace layout.",
                )
            child_parts = (*relative_parts, name)
            child_path = path / name
            if len(state.entries) >= self._quota.maximum_entries:
                reject(
                    WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                    "inventory.entries",
                    "The case workspace exceeds its configured inventory size.",
                )
            with open_handle(child_path) as child_handle:
                require_expected_handle(
                    child_handle, child_path, directory=child_handle.info.is_directory
                )
                if top_level:
                    expected_directory = name in _TOP_LEVEL_DIRECTORY_NAMES
                    if child_handle.info.is_directory is not expected_directory:
                        reject(
                            WorkspaceErrorCode.PATH_INVALID,
                            "inventory.layout",
                            "A fixed case-workspace object has an unexpected type.",
                        )
                if child_handle.info.is_reparse_point:
                    reject(
                        WorkspaceErrorCode.REPARSE_POINT,
                        "inventory.reparse",
                        "Linked or redirected objects are not allowed in case storage.",
                    )
                if not child_handle.info.is_directory and child_handle.info.link_count != 1:
                    reject(
                        WorkspaceErrorCode.ALIAS_DETECTED,
                        "inventory.links",
                        "A disposable file aliases another filesystem object.",
                    )
                require_no_alternate_streams(child_path)
                inventory_entry = _InventoryEntry(
                    relative_parts=child_parts,
                    identity=child_handle.info.identity,
                    is_directory=child_handle.info.is_directory,
                    logical_size=0
                    if child_handle.info.is_directory
                    else child_handle.info.logical_size,
                )
                state.entries.append(inventory_entry)
                if child_handle.info.is_directory:
                    verify_private_acl(child_handle)
                    self._inventory_directory(
                        child_path,
                        child_handle,
                        relative_parts=child_parts,
                        depth=depth + 1,
                        state=state,
                        top_level=False,
                    )
                else:
                    state.total_bytes += child_handle.info.logical_size
                    first = child_parts[0]
                    if first == "cache" or not relative_parts and path.name == "cache":
                        state.cache_bytes += child_handle.info.logical_size
                    if first == "temp" or not relative_parts and path.name == "temp":
                        state.temp_bytes += child_handle.info.logical_size

    def _delete_inventory(
        self,
        target: CleanupTarget,
        target_path: Path,
        target_handle: WindowsHandle,
        entries: list[_InventoryEntry],
    ) -> CleanupReport:
        removed_files = 0
        removed_directories = 0
        released_bytes = 0
        by_parts = {entry.relative_parts: entry for entry in entries}
        ordered = sorted(
            (
                entry
                for entry in entries
                if not (
                    target is CleanupTarget.CACHE
                    and entry.relative_parts in {("thumbnails",), ("previews",)}
                )
            ),
            key=lambda entry: (len(entry.relative_parts), entry.is_directory),
            reverse=True,
        )
        for entry in ordered:
            try:
                with ExitStack() as stack:
                    current_path = target_path
                    current_handle = target_handle
                    for count, component in enumerate(entry.relative_parts, start=1):
                        current_path = current_path / component
                        is_leaf = count == len(entry.relative_parts)
                        opened = stack.enter_context(open_handle(current_path, delete=is_leaf))
                        expected = by_parts[entry.relative_parts[:count]]
                        require_expected_handle(
                            opened,
                            current_path,
                            directory=expected.is_directory,
                            expected_identity=expected.identity,
                        )
                        if not opened.info.is_directory and opened.info.link_count != 1:
                            reject(
                                WorkspaceErrorCode.ALIAS_DETECTED,
                                "cleanup.links",
                                "A disposable file changed into an aliased filesystem object.",
                            )
                        if (
                            not opened.info.is_directory
                            and opened.info.logical_size != expected.logical_size
                        ):
                            reject(
                                WorkspaceErrorCode.IDENTITY_CHANGED,
                                "cleanup.size",
                                "A disposable file changed after cleanup inventory.",
                            )
                        require_no_alternate_streams(current_path)
                        current_handle = opened
                    current_handle.mark_for_deletion()
                if entry.is_directory:
                    removed_directories += 1
                else:
                    removed_files += 1
                    released_bytes += entry.logical_size
            except WorkspaceError as error:
                return CleanupReport(
                    target=target,
                    status=CleanupStatus.PARTIAL,
                    removed_files=removed_files,
                    removed_directories=removed_directories,
                    released_bytes=released_bytes,
                    failure_code=error.code,
                )
        return CleanupReport(
            target=target,
            status=CleanupStatus.COMPLETE,
            removed_files=removed_files,
            removed_directories=removed_directories,
            released_bytes=released_bytes,
        )
