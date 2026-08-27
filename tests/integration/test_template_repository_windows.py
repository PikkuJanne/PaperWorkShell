"""Windows filesystem boundary evidence for the PWS-0401 template repository."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from paperworkshell.storage import template_repository as repository_storage
from paperworkshell.storage._source_windows import SourceFileInfo
from paperworkshell.storage._windows import FileIdentity
from paperworkshell.storage.template_repository import (
    LocalTemplateCatalogEntry,
    WindowsTemplateRepository,
)
from paperworkshell.templates.models import (
    MAX_TEMPLATE_BYTES,
    TemplateArtifactEvidence,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0401 targets the supported Windows template repository"
)

TEMPLATE_KEY = TemplateKey("generic.portal", "1.0.0")
OTHER_TEMPLATE_KEY = TemplateKey("other.portal", "1.0.0")
VERIFIED_ON = date(2026, 8, 27)


def _evidence(content: bytes) -> TemplateArtifactEvidence:
    return TemplateArtifactEvidence(
        byte_size=len(content),
        sha256=sha256(content).hexdigest(),
        catalog_verified_on=VERIFIED_ON,
    )


def _entry(file_name: str, content: bytes = b"{}") -> LocalTemplateCatalogEntry:
    return LocalTemplateCatalogEntry(
        key=TEMPLATE_KEY,
        file_name=file_name,
        evidence=_evidence(content),
    )


def _repository(
    tmp_path: Path,
    content: bytes,
    *,
    file_name: str = "generic-portal.json",
) -> tuple[WindowsTemplateRepository, Path, LocalTemplateCatalogEntry]:
    root = tmp_path / "template-catalog"
    root.mkdir()
    (root / file_name).write_bytes(content)
    entry = _entry(file_name, content)
    return WindowsTemplateRepository(root, (entry,)), root, entry


def _failure_text(error: TemplateLoadError) -> str:
    return " ".join((str(error), repr(error), *(str(argument) for argument in error.args)))


def test_reads_exact_catalog_selected_snapshot_through_authenticated_handle(
    tmp_path: Path,
) -> None:
    content = b'{"schema_version":1,"title":"Synthetic template"}'
    repository, root, entry = _repository(tmp_path, content)

    snapshot = repository.read(TEMPLATE_KEY)

    assert snapshot.key == TEMPLATE_KEY
    assert snapshot.content == content
    assert snapshot.evidence == entry.evidence
    assert snapshot.evidence.byte_size == len(content)
    assert snapshot.evidence.sha256 == sha256(content).hexdigest()
    assert tuple(path.name for path in root.iterdir()) == ("generic-portal.json",)


def test_accepts_exact_maximum_size_snapshot(tmp_path: Path) -> None:
    content = b"{" + (b" " * (MAX_TEMPLATE_BYTES - 2)) + b"}"
    repository, _root, _entry_value = _repository(tmp_path, content)

    snapshot = repository.read(TEMPLATE_KEY)

    assert len(snapshot.content) == MAX_TEMPLATE_BYTES
    assert snapshot.content == content


def test_unknown_opaque_key_does_not_probe_a_path_or_disclose_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root, _entry_value = _repository(tmp_path, b"{}")
    before = tuple(root.iterdir())

    def fail_open(_path: Path) -> None:
        pytest.fail("an unknown opaque key must fail before any filesystem open")

    monkeypatch.setattr(repository_storage, "open_authenticated_source", fail_open)

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(OTHER_TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.NOT_FOUND
    assert tuple(root.iterdir()) == before
    assert OTHER_TEMPLATE_KEY.template_id not in _failure_text(raised.value)


@pytest.mark.parametrize(
    "unsafe_name",
    (
        "../escape.json",
        "C:\\outside.json",
        "\\\\server\\share\\outside.json",
        "\\\\?\\C:\\outside.json",
        "\\\\.\\NUL",
        "template.json:alternate-stream",
        "con.json",
        "con.payload.json",
        "aux.notes.json",
        "com1.data.json",
        "lpt9.json",
    ),
)
def test_catalog_rejects_traversal_absolute_unc_device_ads_and_reserved_names(
    unsafe_name: str,
) -> None:
    with pytest.raises(TemplateLoadError) as raised:
        _entry(unsafe_name)

    assert raised.value.code is TemplateLoadErrorCode.MODEL_INVALID
    assert unsafe_name not in _failure_text(raised.value)


def test_rejects_hardlinked_template_without_reading_or_modifying_it(tmp_path: Path) -> None:
    content = b'{"schema_version":1}'
    root = tmp_path / "template-catalog"
    root.mkdir()
    original = root / "original.json"
    selected = root / "selected.json"
    original.write_bytes(content)
    try:
        os.link(original, selected)
    except OSError as error:
        pytest.skip(f"hard links are unavailable on this test volume: {error.winerror}")
    repository = WindowsTemplateRepository(root, (_entry(selected.name, content),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SOURCE_UNSAFE
    assert original.read_bytes() == selected.read_bytes() == content
    assert str(root) not in _failure_text(raised.value)


def test_rejects_reparse_point_template_where_supported(tmp_path: Path) -> None:
    content = b'{"schema_version":1}'
    root = tmp_path / "template-catalog"
    root.mkdir()
    target = root / "target.json"
    selected = root / "selected.json"
    target.write_bytes(content)
    try:
        selected.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"file symlinks are unavailable: {error}")
    repository = WindowsTemplateRepository(root, (_entry(selected.name, content),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SOURCE_UNSAFE
    assert target.read_bytes() == content
    assert selected.is_symlink()
    assert str(target) not in _failure_text(raised.value)


def test_rejects_oversize_template_before_materializing_content(tmp_path: Path) -> None:
    root = tmp_path / "template-catalog"
    root.mkdir()
    selected = root / "oversize.json"
    selected.write_bytes(b"x" * (MAX_TEMPLATE_BYTES + 1))
    repository = WindowsTemplateRepository(root, (_entry(selected.name),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED
    assert selected.stat().st_size == MAX_TEMPLATE_BYTES + 1
    assert selected.name not in _failure_text(raised.value)


def test_missing_catalog_artifact_failure_is_sanitized(tmp_path: Path) -> None:
    root = tmp_path / "private-catalog-4711"
    root.mkdir()
    private_name = "private-template-9831.json"
    evidence = _evidence(b"{}")
    repository = WindowsTemplateRepository(
        root,
        (
            LocalTemplateCatalogEntry(
                key=TEMPLATE_KEY,
                file_name=private_name,
                evidence=evidence,
            ),
        ),
    )

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    exposed = _failure_text(raised.value)
    assert raised.value.code is TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE
    assert str(root) not in exposed
    assert private_name not in exposed
    assert evidence.sha256 not in exposed


def test_raw_open_failure_context_is_discarded_before_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"{}"
    repository, root, _entry_value = _repository(tmp_path, content)
    canary = rf"SYNTHETIC_OPEN_FAILURE C:\private\{root.name}\template.json"

    def fail_open(_path: Path) -> None:
        raise RuntimeError(canary)

    monkeypatch.setattr(repository_storage, "open_authenticated_source", fail_open)
    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert canary not in _failure_text(raised.value)


class _ChangingSourceHandle:
    def __init__(
        self,
        *,
        content: bytes,
        initial: SourceFileInfo,
        current: SourceFileInfo,
    ) -> None:
        self.initial_info = initial
        self._content = content
        self._current = current
        self.read_count = 0
        self.closed = False

    def read_at(self, offset: int, count: int) -> bytes:
        assert offset == 0
        assert count == len(self._content)
        self.read_count += 1
        return self._content

    def current_info(self) -> SourceFileInfo:
        return self._current

    def close(self) -> None:
        self.closed = True


def _source_info(path: Path, content: bytes) -> SourceFileInfo:
    return SourceFileInfo(
        identity=FileIdentity(volume_serial_number=7, file_id=b"a" * 16),
        final_path=path,
        logical_size=len(content),
        link_count=1,
        delete_pending=False,
        is_directory=False,
        is_reparse_point=False,
        creation_time=11,
        last_write_time=12,
        change_time=13,
        file_attributes=0,
    )


def test_terminal_identity_change_fails_closed_and_closes_the_retained_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b'{"schema_version":1,"canary":"must-not-leak-6631"}'
    root = tmp_path / "template-catalog"
    root.mkdir()
    selected = root / "selected.json"
    initial = _source_info(selected, content)
    changed = replace(
        initial,
        identity=FileIdentity(volume_serial_number=7, file_id=b"b" * 16),
        last_write_time=99,
    )
    handle = _ChangingSourceHandle(content=content, initial=initial, current=changed)
    monkeypatch.setattr(repository_storage, "is_fixed_local_drive", lambda _path: True)
    monkeypatch.setattr(repository_storage, "open_authenticated_source", lambda _path: handle)
    repository = WindowsTemplateRepository(root, (_entry(selected.name, content),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SOURCE_CHANGED
    assert handle.read_count == 1
    assert handle.closed
    assert "must-not-leak-6631" not in _failure_text(raised.value)
    assert str(selected) not in _failure_text(raised.value)


def test_initial_final_path_mismatch_fails_before_reading_and_closes_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b'{"schema_version":1}'
    root = tmp_path / "template-catalog"
    root.mkdir()
    selected = root / "selected.json"
    aliased = root / "other-private-location.json"
    initial = _source_info(aliased, content)
    handle = _ChangingSourceHandle(content=content, initial=initial, current=initial)
    monkeypatch.setattr(repository_storage, "is_fixed_local_drive", lambda _path: True)
    monkeypatch.setattr(repository_storage, "open_authenticated_source", lambda _path: handle)
    repository = WindowsTemplateRepository(root, (_entry(selected.name, content),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SOURCE_UNSAFE
    assert handle.read_count == 0
    assert handle.closed
    assert str(aliased) not in _failure_text(raised.value)


def test_sparse_template_fails_before_reading_and_closes_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b'{"schema_version":1}'
    root = tmp_path / "template-catalog"
    root.mkdir()
    selected = root / "selected.json"
    sparse = replace(_source_info(selected, content), file_attributes=0x0000_0200)
    handle = _ChangingSourceHandle(content=content, initial=sparse, current=sparse)
    monkeypatch.setattr(repository_storage, "is_fixed_local_drive", lambda _path: True)
    monkeypatch.setattr(repository_storage, "open_authenticated_source", lambda _path: handle)
    repository = WindowsTemplateRepository(root, (_entry(selected.name, content),))

    with pytest.raises(TemplateLoadError) as raised:
        repository.read(TEMPLATE_KEY)

    assert raised.value.code is TemplateLoadErrorCode.SOURCE_UNSAFE
    assert handle.read_count == 0
    assert handle.closed
    assert str(selected) not in _failure_text(raised.value)
