"""Data-only translation admission, display formatting and inventory regressions."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Thread
from typing import cast

import pytest

from paperworkshell.localization.catalog import (
    MAX_CATALOG_BYTES,
    LocalizationError,
    configure_locale,
    current_locale,
    format_date,
    format_file_size,
    format_number,
    is_rtl,
    load_catalog,
    localized,
    mark,
    placeholders,
    tr,
    validate_catalog,
)
from tools.validate_localization import (
    _payload,
    pseudo_translate,
    source_messages,
    unmarked_ui_strings,
    validate_resources,
)


def test_english_and_two_explicit_test_locales_are_installed() -> None:
    assert load_catalog("en").direction == "ltr"
    assert load_catalog("en-XA").direction == "ltr"
    assert load_catalog("ar-XB").direction == "rtl"
    assert set(load_catalog("en").messages) == set(load_catalog("en-XA").messages)
    assert set(load_catalog("en").messages) == set(load_catalog("ar-XB").messages)


def test_source_marking_is_deferred_and_context_restores_after_failure() -> None:
    original = current_locale()
    source = mark("Cancel")
    with pytest.raises(RuntimeError), localized("en-XA"):
        assert current_locale() == "en-XA"
        assert tr(source) != source
        assert not is_rtl()
        with localized("ar-XB"):
            assert is_rtl()
        assert current_locale() == "en-XA"
        raise RuntimeError("synthetic failure")
    assert current_locale() == original
    assert source == "Cancel"


@pytest.mark.parametrize("locale", ["en", "en-XA", "ar-XB"])
def test_unknown_user_content_is_never_pseudo_localized(locale: str) -> None:
    private = "SYNTHETIC_CONTENT_TOKEN_1078 {unmatched document field}"
    with localized(locale):
        assert tr(private) == private


def test_resource_only_translation_and_locale_fallback_need_no_application_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "catalogs"
    resources.mkdir()
    english = _payload("en", ["Count: {count}", "Cancel"])
    french = _payload("fr", ["Count: {count}", "Cancel"])
    french["messages"] = {"Count: {count}": "Nombre : {count}", "Cancel": "Annuler"}
    french["number"] = {
        "decimal_separator": ",",
        "group_separator": "\u202f",
        "digits": "0123456789",
    }
    french["date"] = {"order": "dmy", "separator": "/"}
    for payload in (english, french):
        (resources / (str(payload["locale"]) + ".json")).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    try:
        with monkeypatch.context() as patched:
            patched.setattr("paperworkshell.localization.catalog.files", lambda _package: tmp_path)
            assert configure_locale("fr_CA") == "fr"
            assert tr("Count: {count}", count=format_number(1234)) == "Nombre : 1\u202f234"
            assert tr("Cancel") == "Annuler"
            assert format_date(date(2026, 11, 2)) == "02/11/2026"
            assert format_number(Decimal("1234.5"), decimals=1) == "1\u202f234,5"
            assert configure_locale("zz-ZZ") == "en"
    finally:
        configure_locale("en")


def test_interpolated_numeric_fields_localize_without_changing_user_string_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "Page {count}, size {size:,.1f}, file {filename}, literal {literal!s}"
    payload = _payload("ar-XB", [source])
    resources = tmp_path / "catalogs"
    resources.mkdir()
    (resources / "ar-XB.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("paperworkshell.localization.catalog.files", lambda _package: tmp_path)
    with localized("ar-XB"):
        rendered = tr(source, count=12, size=1234.5, filename="synthetic-123.pdf", literal=123)
    assert "١٢" in rendered
    assert "١٬٢٣٤٫٥" in rendered
    assert "synthetic-123.pdf" in rendered
    assert "123" in rendered  # Explicit string conversion remains literal.


def test_case_and_underscore_aliases_select_the_canonical_test_catalog() -> None:
    try:
        assert configure_locale("EN_xa") == "en-XA"
        assert configure_locale("ar_xb") == "ar-XB"
        assert configure_locale("en-US") == "en"
    finally:
        configure_locale("en")


def test_startup_language_selection_applies_to_a_new_backend_thread() -> None:
    captured: list[tuple[str, str]] = []
    try:
        configure_locale("en-XA")
        expected = tr("Cancel")
        worker = Thread(target=lambda: captured.append((current_locale(), tr("Cancel"))))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert captured == [("en-XA", expected)]
    finally:
        configure_locale("en")


@pytest.mark.parametrize("identifier", ["../en", "C:/private", "en.json", "", "en/../../en"])
def test_locale_identifiers_cannot_be_paths(identifier: str) -> None:
    with pytest.raises(LocalizationError, match="language resource") as raised:
        configure_locale(identifier)
    assert raised.value.code == "LOCALIZATION_RESOURCE_INVALID"
    assert identifier not in str(raised.value) or not identifier


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("Count {count}", "Count {other}"),
        ("Count {count}", "Count {count.real}"),
        ("Count {count}", "Count {count[private]}"),
        ("Count {count}", "Count {count:{width}}"),
        ("Count {count}", "Count {count!a}"),
        ("Count {count}", "Count {count:999999999f}"),
        ("Count {count}", "Count {"),
        ("Count {count}", "Count"),
        ("Count {count}", "Count {count} {count}"),
        ("Count {count}", "Count {count}\u202e"),
        ("Count {count}", "Count {count}\x00"),
        ("Count {count}", "Count {count}\ud800"),
        ("Count {count}", "Cafe\u0301 {count}"),
    ],
)
def test_unsafe_or_mismatched_translation_fails_sanitized(source: str, replacement: str) -> None:
    payload = _payload("en", [source])
    payload["messages"] = {source: replacement}
    with pytest.raises(LocalizationError) as raised:
        validate_catalog(payload)
    assert "Count" not in str(raised.value)


@pytest.mark.parametrize("mutation", ["old", "future", "bool", "unknown", "number", "date"])
def test_version_and_display_metadata_fail_before_use(mutation: str) -> None:
    payload = _payload("en", ["Cancel"])
    if mutation == "old":
        payload["schema_version"] = 0
    elif mutation == "future":
        payload["schema_version"] = 2
    elif mutation == "bool":
        payload["schema_version"] = True
    elif mutation == "unknown":
        payload["execute"] = "synthetic prohibited code"
    elif mutation == "number":
        payload["number"] = {
            "decimal_separator": ".",
            "group_separator": ".",
            "digits": "0123456789",
        }
    else:
        payload["date"] = {"order": "python", "separator": "-"}
    with pytest.raises(LocalizationError):
        validate_catalog(payload)


def test_duplicate_json_keys_and_oversize_resources_fail_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "catalogs"
    resources.mkdir()
    resource = resources / "en.json"
    monkeypatch.setattr("paperworkshell.localization.catalog.files", lambda _package: tmp_path)
    resource.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(LocalizationError):
        load_catalog("en")
    resource.write_bytes(b" " * (MAX_CATALOG_BYTES + 1))
    with pytest.raises(LocalizationError):
        load_catalog("en")


def test_validated_message_map_is_immutable() -> None:
    payload = _payload("en", ["Cancel"])
    selected = validate_catalog(payload)
    cast(dict[str, str], payload["messages"])["Cancel"] = "changed after admission"
    assert selected.messages["Cancel"] == "Cancel"
    with pytest.raises(TypeError):
        cast(dict[str, str], selected.messages)["Cancel"] = "mutated"


def test_number_date_and_file_size_policy_changes_display_only() -> None:
    instant = datetime(2026, 11, 2, 12, 30)
    with localized("en"):
        assert format_number(1234) == "1,234"
        assert format_number(12.345, decimals=2) == "12.34"
        assert format_file_size(0) == "0 B"
        assert format_file_size(1536) == "1.5 KiB"
        assert format_file_size(1024**2) == "1.0 MiB"
        assert format_date(instant) == "2026-11-02"
    with localized("ar-XB"):
        assert format_number(1234) == "١٬٢٣٤"
        assert format_number(1.5, decimals=1) == "١٫٥"
        assert format_file_size(1536) == "١٫٥ KiB"
        assert format_date(instant) == "٠٢/١١/٢٠٢٦"
    assert instant.isoformat() == "2026-11-02T12:30:00"


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), Decimal("NaN"), 10**30])
def test_invalid_numeric_display_is_sanitized(value: int | float | Decimal) -> None:
    with pytest.raises(LocalizationError):
        format_number(value)


def test_pseudo_locales_preserve_placeholders_and_never_reverse_coordinates() -> None:
    source = "&Move page {page} to {x:.1f}, {y:.1f}\nNext line"
    for rtl in (False, True):
        expanded = pseudo_translate(source, rtl=rtl)
        assert len(expanded) > len(source)
        assert placeholders(source) == placeholders(expanded)
        assert expanded.count("\n") == 1
        assert "&" in expanded
        assert "{x:.1f}" in expanded
        assert "{y:.1f}" in expanded


def test_pseudo_preserves_product_identity_shortcut_syntax_and_file_filters() -> None:
    source = "PaperWorkShell images (*.png *.jpg); use Ctrl+Shift+Left"
    for rtl in (False, True):
        expanded = pseudo_translate(source, rtl=rtl)
        assert "PaperWorkShell" in expanded
        assert "*.png *.jpg" in expanded
        assert "Ctrl+Shift+Left" in expanded
    payload = _payload("en", ["Images (*.png)"])
    payload["messages"] = {"Images (*.png)": "Images (*.pñg)"}
    with pytest.raises(LocalizationError):
        validate_catalog(payload)


def test_pseudo_preserves_stable_code_tokens_and_catalog_rejects_their_mutation() -> None:
    source = "PWS_CHECKLIST_ACTION_FAILED: Review {count} pages before retrying."
    for rtl in (False, True):
        expanded = pseudo_translate(source, rtl=rtl)
        assert "PWS_CHECKLIST_ACTION_FAILED" in expanded
        assert placeholders(source) == placeholders(expanded)
        payload = _payload("ar-XB" if rtl else "en-XA", [source])
        validate_catalog(payload)
    for translation in (
        "CHANGED_ACTION_FAILED: Review {count} pages before retrying.",
        "Review {count} pages before retrying.",
        "PWS_CHECKLIST_ACTION_FAILED PWS_CHECKLIST_ACTION_FAILED: Review {count} pages.",
    ):
        invalid = _payload("en", [source])
        invalid["messages"] = {source: translation}
        with pytest.raises(LocalizationError):
            validate_catalog(invalid)


def test_qt_progress_tokens_survive_translation_without_treating_literal_percent_as_a_field() -> (
    None
):
    source = "{phase}: %v of %m (%p%); review at 50%"
    translated = "Review at 50%: {phase}, %p%, %v / %m"
    payload = _payload("en", [source])
    payload["messages"] = {source: translated}
    validate_catalog(payload)
    for invalid_text in (
        "{phase}: of %m (%p%); review at 50%",  # Dropped value token.
        "{phase}: %p of %m (%p%); review at 50%",  # Changed value to percentage.
        "{phase}: %v of %m (%p% %p%); review at 50%",  # Duplicated percentage.
    ):
        invalid = _payload("en", [source])
        invalid["messages"] = {source: invalid_text}
        with pytest.raises(LocalizationError):
            validate_catalog(invalid)


def test_resource_validation_detects_missing_and_stale_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = source_messages()
    monkeypatch.setattr(
        "tools.validate_localization.source_messages",
        lambda: original | {"Missing translation entry"},
    )
    with pytest.raises(LocalizationError):
        validate_resources()


def test_source_inventory_and_unmarked_display_negative(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "sample.py").write_text(
        "button = QPushButton('Forgotten control')\n"
        "label.setAccessibleName(tr('Marked label'))\n"
        "DEFERRED = mark('Marked deferred text')\n"
        "PLURAL = tr('One page' if count == 1 else 'Many pages')\n"
        "_SAFE_MESSAGES = {'CODE':'Closed error message.'}\n",
        encoding="utf-8",
    )
    assert source_messages(tmp_path) == {
        "Marked label",
        "Marked deferred text",
        "Closed error message.",
        "One page",
        "Many pages",
    }
    assert unmarked_ui_strings(tmp_path) == (("ui/sample.py", 1),)


def test_refresh_payload_does_not_mutate_source_inputs() -> None:
    sources = ["Cancel", "Count {count}"]
    before = copy.deepcopy(sources)
    validate_catalog(_payload("en-XA", sources))
    assert sources == before


@pytest.mark.parametrize(
    "translation", ["<b>Cancel</b>", "<a href='file://private'>Cancel</a>", "<html"]
)
def test_translation_cannot_introduce_qt_rich_text(translation: str) -> None:
    payload = _payload("en", ["Cancel"])
    payload["messages"] = {"Cancel": translation}
    with pytest.raises(LocalizationError):
        validate_catalog(payload)


def test_resource_validation_requires_all_three_builtin_catalogs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "en-XA.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("tools.validate_localization.CATALOG_ROOT", tmp_path)
    with pytest.raises(LocalizationError):
        validate_resources()
