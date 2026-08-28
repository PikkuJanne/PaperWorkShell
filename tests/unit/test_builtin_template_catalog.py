from __future__ import annotations

import hashlib
from datetime import date

import pytest

from paperworkshell.templates.catalog import (
    BUILT_IN_TEMPLATE_KEYS,
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import (
    TemplateFreshness,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    TemplateRepository,
    TemplateSignatureStatus,
)

EXPECTED_BYTE_SIZE = 7_145
EXPECTED_SHA256 = "f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd"


def test_builtin_catalog_exposes_one_exact_immutable_artifact() -> None:
    repository = built_in_template_repository()

    assert isinstance(repository, TemplateRepository)
    assert BUILT_IN_TEMPLATE_KEYS == (GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,)
    snapshot = repository.read(GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY)
    assert snapshot.key == GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY
    assert len(snapshot.content) == EXPECTED_BYTE_SIZE
    assert hashlib.sha256(snapshot.content).hexdigest() == EXPECTED_SHA256
    assert snapshot.evidence.byte_size == EXPECTED_BYTE_SIZE
    assert snapshot.evidence.sha256 == EXPECTED_SHA256
    assert snapshot.evidence.catalog_verified_on == date(2026, 8, 28)
    assert snapshot.evidence.signature_status is TemplateSignatureStatus.NOT_PROVIDED
    assert EXPECTED_SHA256 not in repr(snapshot)
    assert EXPECTED_SHA256 not in repr(snapshot.evidence)


def test_builtin_template_loads_through_safe_loader_with_expected_identity() -> None:
    loaded = SafeTemplateLoader(built_in_template_repository()).load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
    )

    assert loaded.key == GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY
    assert loaded.byte_size == EXPECTED_BYTE_SIZE
    assert loaded.sha256 == EXPECTED_SHA256
    assert loaded.freshness is TemplateFreshness.CURRENT
    assert loaded.document["template_id"] == "generic-portal-application"
    assert loaded.document["template_version"] == "1.1.0"
    assert loaded.document["requirement_order"] == [
        "application-form",
        "identity-evidence",
        "address-evidence",
        "supporting-evidence",
    ]


@pytest.mark.parametrize(
    ("as_of", "expected"),
    (
        (date(2027, 2, 27), TemplateFreshness.CURRENT),
        (date(2027, 2, 28), TemplateFreshness.REVIEW_DUE),
        (date(2027, 8, 28), TemplateFreshness.REVIEW_DUE),
    ),
)
def test_builtin_template_lifecycle_boundaries(
    as_of: date,
    expected: TemplateFreshness,
) -> None:
    loaded = SafeTemplateLoader(built_in_template_repository()).load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=as_of,
    )

    assert loaded.freshness is expected


@pytest.mark.parametrize(
    ("as_of", "expected_code", "expected_context"),
    (
        (
            date(2026, 8, 27),
            TemplateLoadErrorCode.LIFECYCLE_INVALID,
            "template.catalog_date",
        ),
        (date(2027, 8, 29), TemplateLoadErrorCode.EXPIRED, "template.lifecycle"),
    ),
)
def test_builtin_template_rejects_out_of_lifecycle_dates(
    as_of: date,
    expected_code: TemplateLoadErrorCode,
    expected_context: str,
) -> None:
    with pytest.raises(TemplateLoadError) as captured:
        SafeTemplateLoader(built_in_template_repository()).load(
            GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
            as_of=as_of,
        )

    assert captured.value.code is expected_code
    assert captured.value.context == expected_context
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_builtin_catalog_rejects_unknown_key_without_resource_fallback() -> None:
    unknown_key = TemplateKey("generic-portal-application", "9.9.9")

    with pytest.raises(TemplateLoadError) as captured:
        built_in_template_repository().read(unknown_key)

    assert captured.value.code is TemplateLoadErrorCode.NOT_FOUND
    assert captured.value.context == "template.builtin_catalog"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
