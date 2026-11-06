"""Notice-root aliases, changed resources, binary refusal and argument privacy."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from paperworkshell.app import installed_notices as notices
from tests.unit.test_installed_notices import notice_catalog


def test_reparse_attribute_is_refused_by_actual_plain_metadata_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = notice_catalog(tmp_path)
    selected = catalog.root / "COMPONENTS.txt"
    original = Path.lstat

    def metadata(path: Path) -> os.stat_result:
        value = original(path)
        if path == selected:
            return cast(
                os.stat_result, SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            )
        return value

    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(notices.InstalledNoticesError, match="^INSTALLED_NOTICES_UNAVAILABLE$"):
        notices.read_notice(catalog, catalog.files[0])


@pytest.mark.parametrize(
    "raw", [b"PDF\x00PRIVATE_TOKEN", b"\x80\x81binary", b"ordinary\x1bcontrol"]
)
def test_nontext_notice_is_not_rendered_or_exposed_in_errors(tmp_path: Path, raw: bytes) -> None:
    catalog = notice_catalog(tmp_path)
    path = catalog.root / "COMPONENTS.txt"
    path.write_bytes(raw)
    before = path.read_bytes()
    # Re-enumerate the same installed root without constructing a second tree.
    from paperworkshell.app.installed_runtime import InstalledRuntime

    root = catalog.root.parent / "runtime"
    runtime = InstalledRuntime("0.0.0", "synthetic", root, root, root, root, root, root)
    current = notices.catalog_for_runtime(runtime)
    with pytest.raises(notices.InstalledNoticesError) as caught:
        notices.read_notice(current, current.files[0])
    assert "PRIVATE_TOKEN" not in repr(caught.value) and str(tmp_path) not in repr(caught.value)
    assert path.read_bytes() == before


def test_selected_text_read_never_calls_an_external_application() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(notices))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        name and name.startswith(("subprocess", "socket", "urllib", "pikepdf", "PIL", "PySide6"))
        for name in imports
    )
