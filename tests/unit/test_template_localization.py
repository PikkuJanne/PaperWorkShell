"""PWS-0704 display translation never changes trusted template authority."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

import pytest

from paperworkshell.localization.catalog import localized, tr
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateLoadError, TemplateLoadErrorCode
from tests.unit.test_template_loader import _load, _template_bytes


@pytest.mark.parametrize("locale", ("en", "en-XA", "ar-XB"))
def test_ui_locale_translates_presentation_without_rewriting_template(locale: str) -> None:
    repository = built_in_template_repository()
    key = GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY
    original = repository.read(key)
    with localized(locale):
        snapshot = SafeTemplateLoader(repository).load(key, as_of=date(2026, 11, 2))
        assert snapshot.presentation.title == tr("Generic portal application packet")
        assert snapshot.presentation.fields[0].label == tr("Publisher (template claim)")
        assert snapshot.presentation.fields[0].value == "PaperWorkShell project"
        if locale != "en":
            assert snapshot.presentation.title != snapshot.document["title"]
        assert snapshot.presentation.locale == "en"
        assert snapshot.document["title"] == "Generic portal application packet"
        assert snapshot.document["locale"] == "en"
        assert snapshot.key == key
        assert snapshot.schema_version == 1
        assert snapshot.sha256 == hashlib.sha256(original.content).hexdigest()
        assert snapshot.byte_size == len(original.content)
        assert snapshot.document == json.loads(original.content)
    assert repository.read(key).content == original.content


@pytest.mark.parametrize("locale", ("en", "fi-FI", "ar", "ar-XB", "he-IL", "zh-Hant-TW"))
def test_template_locale_is_validated_data_independent_of_ui_locale(locale: str) -> None:
    content = _template_bytes(lambda document: document.update(locale=locale))
    with localized("en-XA"):
        snapshot, _repository = _load(content=content)
    assert snapshot.document["locale"] == locale
    assert snapshot.presentation.locale == locale
    assert snapshot.schema_version == 1


@pytest.mark.parametrize("locale", ("en_US", "ar-rtl", "../en", "en-XA-unapproved", ""))
def test_invalid_template_locale_fails_without_changing_source(locale: str) -> None:
    content = _template_bytes(lambda document: document.update(locale=locale))
    original = copy.copy(content)
    with localized("ar-XB"), pytest.raises(TemplateLoadError) as caught:
        _load(content=content)
    assert caught.value.code is TemplateLoadErrorCode.SCHEMA_INVALID
    assert content == original


@pytest.mark.parametrize("version", (0, 2, 999))
def test_unsupported_template_schema_does_not_become_supported_through_translation(
    version: int,
) -> None:
    content = _template_bytes(lambda document: document.update(schema_version=version))
    original = hashlib.sha256(content).digest()
    with localized("en-XA"), pytest.raises(TemplateLoadError) as caught:
        _load(content=content)
    assert caught.value.code in {
        TemplateLoadErrorCode.VERSION_UNSUPPORTED,
        TemplateLoadErrorCode.VERSION_INVALID,
    }
    assert hashlib.sha256(content).digest() == original
