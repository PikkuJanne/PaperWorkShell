from __future__ import annotations

import traceback
from datetime import date
from typing import cast
from unittest.mock import patch

import pytest

import paperworkshell.templates.catalog as catalog
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import (
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
)

CANARY = "SYNTHETIC_BUILTIN_CATALOG_CANARY_8E1A"
PRIVATE_PATH = rf"C:\synthetic-private\{CANARY}\template.json"


class _Resource:
    def __init__(self, *, payload: bytes | None = None, fail: bool = False) -> None:
        self._payload = payload
        self._fail = fail
        self.join_calls: list[tuple[str, ...]] = []

    def joinpath(self, *descendants: str) -> _Resource:
        self.join_calls.append(descendants)
        return self

    def read_bytes(self) -> bytes:
        if self._fail:
            raise OSError(f"could not read {PRIVATE_PATH}: {CANARY}")
        if self._payload is None:
            raise AssertionError("test resource has no payload")
        return self._payload


def _assert_sanitized(error: TemplateLoadError) -> None:
    rendered = "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            "".join(traceback.format_exception(error)),
        )
    )
    assert CANARY not in rendered
    assert PRIVATE_PATH not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_builtin_catalog_resource_failure_is_stable_and_sanitized() -> None:
    resource = _Resource(fail=True)

    with patch.object(catalog, "files", return_value=resource):
        with pytest.raises(TemplateLoadError) as captured:
            built_in_template_repository().read(GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY)

    assert captured.value.code is TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE
    assert captured.value.context == "template.builtin_resource"
    _assert_sanitized(captured.value)
    assert resource.join_calls == [("builtin", "generic-portal-application-v1.1.0.json")]


def test_builtin_loader_rejects_same_size_resource_tampering_before_decode() -> None:
    original = built_in_template_repository().read(GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY).content
    tampered = bytearray(original)
    tampered[-2] ^= 1
    resource = _Resource(payload=bytes(tampered))

    with patch.object(catalog, "files", return_value=resource):
        with pytest.raises(TemplateLoadError) as captured:
            SafeTemplateLoader(built_in_template_repository()).load(
                GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
                as_of=date(2026, 8, 28),
            )

    assert captured.value.code is TemplateLoadErrorCode.INTEGRITY_MISMATCH
    assert captured.value.context == "template.sha256"
    _assert_sanitized(captured.value)


def test_unknown_catalog_key_cannot_select_a_resource() -> None:
    resource = _Resource(payload=CANARY.encode("ascii"))
    unknown_key = TemplateKey("generic-portal-application", "9.9.9")

    with patch.object(catalog, "files", return_value=resource) as files_mock:
        with pytest.raises(TemplateLoadError) as captured:
            built_in_template_repository().read(unknown_key)

    assert captured.value.code is TemplateLoadErrorCode.NOT_FOUND
    _assert_sanitized(captured.value)
    files_mock.assert_not_called()
    assert resource.join_calls == []


def test_non_key_object_is_rejected_without_resource_access() -> None:
    resource = _Resource(payload=CANARY.encode("ascii"))

    with patch.object(catalog, "files", return_value=resource) as files_mock:
        with pytest.raises(TemplateLoadError) as captured:
            built_in_template_repository().read(cast(TemplateKey, object()))

    assert captured.value.code is TemplateLoadErrorCode.MODEL_INVALID
    assert captured.value.context == "template.builtin_catalog"
    _assert_sanitized(captured.value)
    files_mock.assert_not_called()
