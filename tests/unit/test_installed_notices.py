"""Fixed-root notice resources, exact paging and non-mutating failure paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from paperworkshell.app import installed_notices as notices
from paperworkshell.app.installed_runtime import InstalledRuntime


def notice_runtime(tmp_path: Path) -> InstalledRuntime:
    runtime = tmp_path / "installation" / "runtime"
    runtime.mkdir(parents=True)
    return InstalledRuntime(
        "0.0.0",
        "synthetic-notices",
        runtime,
        runtime / "Lib/site-packages/paperworkshell",
        runtime / "python.exe",
        runtime / "engines/qpdf/bin/qpdf.exe",
        runtime / "engines/tesseract/bin/tesseract.exe",
        runtime / "engines/tesseract/share/tessdata",
    )


def notice_catalog(
    tmp_path: Path, text: str = "Synthetic component 1.0\n"
) -> notices.NoticeCatalog:
    runtime = notice_runtime(tmp_path)
    root = runtime.runtime_root.parent / "notices"
    root.mkdir()
    (root / "COMPONENTS.txt").write_text(text, encoding="utf-8", newline="")
    (root / "PaperWorkShell-MIT-LICENSE.txt").write_text(
        "Synthetic first-party licence\n", encoding="utf-8"
    )
    (root / "Microsoft-runtime-recipient-terms.txt").write_text(
        "Synthetic recipient terms\n", encoding="utf-8"
    )
    return notices.catalog_for_runtime(runtime)


def test_fixed_catalog_exposes_summary_and_required_licence_texts_without_writes(
    tmp_path: Path,
) -> None:
    catalog = notice_catalog(tmp_path)
    before = {path.name: path.read_bytes() for path in catalog.root.iterdir()}
    assert catalog.files[0].relative_name == "COMPONENTS.txt"
    assert {item.relative_name for item in catalog.files} == set(before)
    for item in catalog.files:
        assert notices.read_notice(catalog, item).text == before[item.relative_name].decode("utf-8")
    assert {path.name: path.read_bytes() for path in catalog.root.iterdir()} == before
    assert str(catalog.root) not in repr(catalog)


def test_utf8_characters_and_newlines_are_preserved_across_small_pages() -> None:
    text = (
        "a" * (notices.NOTICE_PAGE_CHARACTERS - 1)
        + "😀\r\n<b>synthetic</b>\n"
        + "界" * notices.NOTICE_PAGE_CHARACTERS
    )
    value = notices.NoticeText(text)
    pages = [value.page(index) for index in range(value.page_count)]
    assert "".join(pages) == text
    assert all(len(page.encode("utf-8")) <= 65536 for page in pages)
    assert "synthetic" not in repr(value)
    for index in (True, -1, value.page_count):
        with pytest.raises(notices.InstalledNoticesError):
            value.page(index)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "latin-1"])
def test_bom_text_uses_declared_encoding_and_remains_plain(tmp_path: Path, encoding: str) -> None:
    catalog = notice_catalog(tmp_path)
    path = catalog.root / "COMPONENTS.txt"
    text = (
        "Synthetic licence ©" if encoding == "latin-1" else "Synthetic licence — 界"
    ) + "\n<a href='https://invalid.example'>plain text</a>"
    path.write_bytes(text.encode(encoding))
    runtime_root = catalog.root.parent / "runtime"
    runtime = InstalledRuntime(
        "0.0.0",
        "synthetic",
        runtime_root,
        runtime_root,
        runtime_root,
        runtime_root,
        runtime_root,
        runtime_root,
    )
    catalog = notices.catalog_for_runtime(runtime)
    assert notices.read_notice(catalog, catalog.files[0]).text == text


def test_changed_or_unlisted_notice_refuses_with_path_free_error(tmp_path: Path) -> None:
    catalog = notice_catalog(tmp_path)
    path = catalog.root / "COMPONENTS.txt"
    path.write_text("PRIVATE_TOKEN changed notice bytes\n", encoding="utf-8")
    with pytest.raises(notices.InstalledNoticesError) as caught:
        notices.read_notice(catalog, catalog.files[0])
    assert str(caught.value) == "INSTALLED_NOTICES_UNAVAILABLE"
    assert "PRIVATE_TOKEN" not in repr(caught.value) and str(tmp_path) not in repr(caught.value)
    outside = notices.NoticeFile("../outside.txt", 1, ())
    with pytest.raises(notices.InstalledNoticesError):
        notices.read_notice(catalog, outside)


def test_singlelink_limit_and_file_count_refusals_do_not_modify_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = notice_runtime(tmp_path)
    root = runtime.runtime_root.parent / "notices"
    root.mkdir()
    path = root / "COMPONENTS.txt"
    path.write_bytes(b"synthetic notice")
    monkeypatch.setattr(notices, "MAX_NOTICE_BYTES", 4)
    with pytest.raises(notices.InstalledNoticesError):
        notices.catalog_for_runtime(runtime)
    monkeypatch.setattr(notices, "MAX_NOTICE_BYTES", 100)
    (root / "second.txt").write_bytes(b"synthetic second")
    monkeypatch.setattr(notices, "MAX_NOTICE_FILES", 1)
    with pytest.raises(notices.InstalledNoticesError):
        notices.catalog_for_runtime(runtime)
    assert path.read_bytes() == b"synthetic notice"


def test_hardlinked_notice_is_not_opened(tmp_path: Path) -> None:
    catalog = notice_catalog(tmp_path)
    path = catalog.root / "COMPONENTS.txt"
    link = tmp_path / "synthetic-linked.txt"
    try:
        os.link(path, link)
    except OSError:
        pytest.skip("hardlink capability unavailable for this temporary fixture")
    with pytest.raises(notices.InstalledNoticesError):
        notices.read_notice(catalog, catalog.files[0])
    assert link.read_bytes() == path.read_bytes()


def test_empty_summary_is_not_a_successful_notice_inventory(tmp_path: Path) -> None:
    runtime = notice_runtime(tmp_path)
    root = runtime.runtime_root.parent / "notices"
    root.mkdir()
    (root / "COMPONENTS.txt").write_bytes(b"")
    with pytest.raises(notices.InstalledNoticesError):
        notices.catalog_for_runtime(runtime)


def test_included_licence_form_feed_is_preserved_as_plain_text(tmp_path: Path) -> None:
    text = "Synthetic licence first page\fsecond page\n"
    catalog = notice_catalog(tmp_path, text)
    assert notices.read_notice(catalog, catalog.files[0]).text == text
