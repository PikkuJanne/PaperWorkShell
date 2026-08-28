"""Closed exact-byte catalog for built-in declarative templates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Final

from paperworkshell.templates.models import (
    TemplateArtifactEvidence,
    TemplateByteSnapshot,
    TemplateKey,
    TemplateLoadErrorCode,
    TemplateRepository,
    TemplateSignatureStatus,
    reject_template,
)

GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY: Final = TemplateKey(
    "generic-portal-application",
    "1.1.0",
)


@dataclass(frozen=True, slots=True, repr=False)
class _BuiltInTemplateEntry:
    """One fixed resource name and its independently recorded byte evidence."""

    key: TemplateKey
    resource_name: str
    evidence: TemplateArtifactEvidence


_BUILT_IN_TEMPLATE_ENTRIES: Final = (
    _BuiltInTemplateEntry(
        key=GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        resource_name="generic-portal-application-v1.1.0.json",
        evidence=TemplateArtifactEvidence(
            byte_size=7_145,
            sha256="f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd",
            catalog_verified_on=date(2026, 8, 28),
            signature_status=TemplateSignatureStatus.NOT_PROVIDED,
        ),
    ),
)

BUILT_IN_TEMPLATE_KEYS: Final = tuple(entry.key for entry in _BUILT_IN_TEMPLATE_ENTRIES)


class _BuiltInTemplateRepository:
    """Read only catalog-selected resources from this installed package."""

    __slots__ = ()

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        """Return exact bytes and trusted evidence for one known catalog key."""

        if type(key) is not TemplateKey:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.builtin_catalog")
        entry = next(
            (candidate for candidate in _BUILT_IN_TEMPLATE_ENTRIES if candidate.key == key), None
        )
        if entry is None:
            reject_template(TemplateLoadErrorCode.NOT_FOUND, "template.builtin_catalog")

        payload: object = None
        try:
            payload = (
                files("paperworkshell.templates")
                .joinpath("builtin", entry.resource_name)
                .read_bytes()
            )
        except Exception:
            payload = None
        if type(payload) is not bytes:
            reject_template(
                TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
                "template.builtin_resource",
            )
        return TemplateByteSnapshot(
            key=entry.key,
            content=payload,
            evidence=entry.evidence,
        )


_BUILT_IN_TEMPLATE_REPOSITORY: Final[TemplateRepository] = _BuiltInTemplateRepository()


def built_in_template_repository() -> TemplateRepository:
    """Return the stateless repository for this build's closed built-in catalog."""

    return _BUILT_IN_TEMPLATE_REPOSITORY


__all__ = (
    "BUILT_IN_TEMPLATE_KEYS",
    "GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY",
    "built_in_template_repository",
)
