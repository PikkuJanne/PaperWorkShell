"""Read only bounded plain-text resources under the fixed installed notices root."""

from __future__ import annotations

import os
import re
import stat
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from paperworkshell.app.installed_runtime import InstalledRuntime, discover_installed_runtime

MAX_NOTICE_BYTES: Final = 16 * 1024 * 1024
MAX_NOTICE_TOTAL_BYTES: Final = 64 * 1024 * 1024
MAX_NOTICE_FILES: Final = 2048
MAX_NOTICE_DIRECTORIES: Final = 2048
NOTICE_PAGE_CHARACTERS: Final = 16_384
_UNSUPPORTED_ATTRIBUTES: Final = 0x00000400 | 0x00001000 | 0x00040000 | 0x00400000
_CONTROL_TEXT: Final = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")


class InstalledNoticesError(RuntimeError):
    """No resource text, selected name or machine path in the failure."""

    code: Final = "INSTALLED_NOTICES_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__(self.code)


def _require(value: bool) -> None:
    if not value:
        raise InstalledNoticesError()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        getattr(value, "st_file_attributes", 0),
    )


def _plain(path: Path, *, directory: bool) -> os.stat_result:
    _require(path.is_absolute() and len(path.parts) <= 64)
    try:
        for parent in (*reversed(path.parents), path):
            value = parent.lstat()
            _require(
                not stat.S_ISLNK(value.st_mode)
                and not getattr(value, "st_file_attributes", 0) & _UNSUPPORTED_ATTRIBUTES
            )
            if parent != path or directory:
                _require(stat.S_ISDIR(value.st_mode))
            else:
                _require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1)
        return value
    except OSError, ValueError:
        raise InstalledNoticesError() from None


@dataclass(frozen=True, slots=True)
class NoticeFile:
    relative_name: str
    size_bytes: int
    identity: tuple[int, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class NoticeCatalog:
    version: str
    candidate_id: str
    files: tuple[NoticeFile, ...]
    root: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class NoticeText:
    text: str = field(repr=False)

    @property
    def page_count(self) -> int:
        return max(1, (len(self.text) + NOTICE_PAGE_CHARACTERS - 1) // NOTICE_PAGE_CHARACTERS)

    def page(self, index: int) -> str:
        _require(type(index) is int and 0 <= index < self.page_count)
        begin = index * NOTICE_PAGE_CHARACTERS
        return self.text[begin : begin + NOTICE_PAGE_CHARACTERS]


def catalog_for_runtime(runtime: InstalledRuntime) -> NoticeCatalog:
    """Enumerate only this code-relative root; names never become external paths."""
    root = runtime.runtime_root.parent / "notices"
    _plain(root, directory=True)
    pending = deque([root])
    files: list[NoticeFile] = []
    directories = 0
    total = 0
    seen: set[str] = set()
    try:
        while pending:
            directory = pending.popleft()
            directories += 1
            _require(directories <= MAX_NOTICE_DIRECTORIES)
            _plain(directory, directory=True)
            with os.scandir(directory) as children:
                for child in children:
                    path = directory / child.name
                    relative = path.relative_to(root).as_posix()
                    _require(
                        len(path.relative_to(root).parts) <= 16
                        and len(relative) <= 512
                        and not any(ord(char) < 32 or ord(char) == 127 for char in relative)
                        and not any(char in child.name for char in '\\:<>"|?*')
                    )
                    if child.is_dir(follow_symlinks=False):
                        _plain(path, directory=True)
                        pending.append(path)
                        _require(directories + len(pending) <= MAX_NOTICE_DIRECTORIES)
                        continue
                    value = _plain(path, directory=False)
                    _require(
                        0 <= value.st_size <= MAX_NOTICE_BYTES and relative.casefold() not in seen
                    )
                    seen.add(relative.casefold())
                    total += value.st_size
                    _require(len(files) < MAX_NOTICE_FILES and total <= MAX_NOTICE_TOTAL_BYTES)
                    files.append(NoticeFile(relative, value.st_size, _identity(value)))
    except OSError, ValueError:
        raise InstalledNoticesError() from None
    _require(any(item.relative_name == "COMPONENTS.txt" and item.size_bytes > 0 for item in files))
    files.sort(
        key=lambda item: (item.relative_name != "COMPONENTS.txt", item.relative_name.casefold())
    )
    return NoticeCatalog(runtime.version, runtime.candidate_id, tuple(files), root)


def current_notice_catalog() -> NoticeCatalog:
    """No user-supplied path, environment-selected resource or case-store lookup."""
    runtime = discover_installed_runtime(
        package_root=Path(__file__).absolute().parent.parent,
        executable=Path(sys.executable),
        prefix=Path(sys.prefix),
        base_prefix=Path(sys.base_prefix),
    )
    _require(runtime is not None)
    assert runtime is not None
    return catalog_for_runtime(runtime)


def read_notice(catalog: NoticeCatalog, item: NoticeFile) -> NoticeText:
    """One selected included text; close its file before returning to the viewer."""
    _require(item in catalog.files)
    parts = item.relative_name.split("/")
    _require(
        all(
            part not in {"", ".", ".."} and not any(char in part for char in '\\:<>"|?*')
            for part in parts
        )
    )
    path = catalog.root.joinpath(*parts)
    before = _plain(path, directory=False)
    _require(_identity(before) == item.identity and before.st_size <= MAX_NOTICE_BYTES)
    try:
        with path.open("rb") as stream:
            _require(_identity(os.fstat(stream.fileno())) == item.identity)
            raw = stream.read(MAX_NOTICE_BYTES + 1)
            _require(_identity(os.fstat(stream.fileno())) == item.identity)
        _require(
            len(raw) == item.size_bytes
            and _identity(_plain(path, directory=False)) == item.identity
        )
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                # Included original licence assets can be Latin-1 (notably
                # FreeType). Display text only; never interpret tags or code.
                text = raw.decode("latin-1")
        _require(_CONTROL_TEXT.search(text) is None)
        return NoticeText(text)
    except OSError, UnicodeError, ValueError:
        raise InstalledNoticesError() from None
