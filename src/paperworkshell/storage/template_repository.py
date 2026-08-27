"""Windows authenticated local repository for declarative template byte snapshots."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Final

from paperworkshell.storage._source_windows import (
    AuthenticatedSourceHandle,
    SourceFileInfo,
    is_fixed_local_drive,
    open_authenticated_source,
)
from paperworkshell.templates.models import (
    MAX_TEMPLATE_BYTES,
    TemplateArtifactEvidence,
    TemplateByteSnapshot,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    reject_template,
)

_CATALOG_FILE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}\.json\Z")
# Sparse, offline, recall-on-open, and recall-on-data-access artifacts can
# materialize data outside the bounded ordinary-file assumptions.
_UNSUPPORTED_FILE_ATTRIBUTES: Final = 0x0000_0200 | 0x0000_1000 | 0x0004_0000 | 0x0040_0000
_RESERVED_BASENAMES: Final = frozenset(
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
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class LocalTemplateCatalogEntry:
    """Trusted mapping from an opaque key to one repository-owned filename."""

    key: TemplateKey
    file_name: str = field(repr=False)
    evidence: TemplateArtifactEvidence

    def __post_init__(self) -> None:
        if (
            type(self.key) is not TemplateKey
            or type(self.file_name) is not str
            or _CATALOG_FILE_NAME.fullmatch(self.file_name) is None
            or self.file_name.split(".", maxsplit=1)[0].casefold() in _RESERVED_BASENAMES
            or type(self.evidence) is not TemplateArtifactEvidence
        ):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.catalog_entry")

    def __repr__(self) -> str:
        return f"LocalTemplateCatalogEntry(key={self.key!r})"


class WindowsTemplateRepository:
    """Read only catalog-selected, fixed-local files through one authenticated handle."""

    __slots__ = ("_entries", "_root")

    def __init__(self, root: Path, entries: Sequence[LocalTemplateCatalogEntry]) -> None:
        self._root = _validated_root(root)
        if not isinstance(entries, Sequence):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.catalog")
        selected: dict[TemplateKey, LocalTemplateCatalogEntry] = {}
        file_names: set[str] = set()
        for entry in entries:
            if type(entry) is not LocalTemplateCatalogEntry:
                reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.catalog")
            folded_name = entry.file_name.casefold()
            if entry.key in selected or folded_name in file_names:
                reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.catalog")
            selected[entry.key] = entry
            file_names.add(folded_name)
        if not selected:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.catalog")
        self._entries = selected

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        """Return one immutable same-handle snapshot selected only by an opaque catalog key."""

        if type(key) is not TemplateKey:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.key")
        entry = self._entries.get(key)
        if entry is None:
            reject_template(TemplateLoadErrorCode.NOT_FOUND, "template.catalog")
        expected_path = self._root / entry.file_name
        location_failure: tuple[TemplateLoadErrorCode, str] | None = None
        try:
            if not is_fixed_local_drive(expected_path):
                reject_template(TemplateLoadErrorCode.SOURCE_UNSAFE, "template.location")
        except TemplateLoadError as error:
            location_failure = (error.code, error.context)
        except Exception:
            location_failure = (TemplateLoadErrorCode.SOURCE_UNSAFE, "template.location")
        if location_failure is not None:
            reject_template(*location_failure)

        handle: AuthenticatedSourceHandle | None = None
        open_failed = False
        try:
            handle = open_authenticated_source(expected_path)
        except Exception:
            open_failed = True
        if open_failed or handle is None:
            reject_template(
                TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
                "template.source_open",
            )
        primary_error: BaseException | None = None
        try:
            initial = handle.initial_info
            _require_supported_file(initial, expected_path)
            if initial.logical_size > MAX_TEMPLATE_BYTES:
                reject_template(TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED, "template.bytes")
            if initial.logical_size < 1:
                reject_template(TemplateLoadErrorCode.JSON_INVALID, "template.bytes")
            read_failed = False
            content = b""
            current: SourceFileInfo | None = None
            try:
                content = handle.read_at(0, initial.logical_size)
                current = handle.current_info()
            except Exception:
                read_failed = True
            if read_failed or current is None:
                reject_template(TemplateLoadErrorCode.SOURCE_CHANGED, "template.source_read")
            if current != initial:
                reject_template(TemplateLoadErrorCode.SOURCE_CHANGED, "template.source_identity")
            _require_supported_file(current, expected_path)
            return TemplateByteSnapshot(key=key, content=content, evidence=entry.evidence)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            close_failed = False
            try:
                handle.close()
            except Exception:
                close_failed = True
            if close_failed and primary_error is None:
                reject_template(
                    TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
                    "template.source_close",
                )


def _validated_root(root: Path) -> Path:
    # ``Path`` constructs the platform-specific concrete subclass (``WindowsPath``
    # here), so an exact-type check would reject every legitimate caller.
    if not isinstance(root, Path):
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.root")
    raw = str(root)
    windows = PureWindowsPath(raw)
    if (
        not windows.is_absolute()
        or not windows.drive
        or raw.startswith(("\\\\", "\\?\\", "\\.\\"))
        or any(part in {"", ".", ".."} for part in windows.parts[1:])
        or any(":" in part for part in windows.parts[1:])
    ):
        reject_template(TemplateLoadErrorCode.SOURCE_UNSAFE, "template.root")
    return Path(os.path.normpath(raw))


def _require_supported_file(info: SourceFileInfo, expected_path: Path) -> None:
    if (
        info.link_count != 1
        or info.delete_pending
        or info.is_directory
        or info.is_reparse_point
        or info.file_attributes & _UNSUPPORTED_FILE_ATTRIBUTES
        or _normalized_path(info.final_path) != _normalized_path(expected_path)
    ):
        reject_template(TemplateLoadErrorCode.SOURCE_UNSAFE, "template.source_identity")


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
