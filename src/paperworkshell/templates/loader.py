"""Exact-byte, non-executable declarative template loader."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import date
from typing import cast

from paperworkshell.templates.codec import decode_template_bytes
from paperworkshell.templates.models import (
    TemplateByteSnapshot,
    TemplateDisplayField,
    TemplateFreshness,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    TemplatePresentation,
    TemplateRepository,
    TemplateSignatureStatus,
    TemplateSnapshot,
    reject_template,
)
from paperworkshell.templates.validation import TemplateValidationResult, validate_template_data


class SafeTemplateLoader:
    """Load one catalog-selected byte snapshot without executing or dereferencing content."""

    __slots__ = ("_repository",)

    def __init__(self, repository: TemplateRepository) -> None:
        if not isinstance(repository, TemplateRepository):
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.repository")
        self._repository = repository

    def load(self, key: TemplateKey, *, as_of: date) -> TemplateSnapshot:
        """Verify, decode, validate, and present one exact template artifact."""

        if type(key) is not TemplateKey or type(as_of) is not date:
            reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.request")
        repository_failure: tuple[TemplateLoadErrorCode, str] | None = None
        snapshot: object = None
        try:
            snapshot = self._repository.read(key)
        except TemplateLoadError as error:
            repository_failure = (error.code, error.context)
        except Exception:
            repository_failure = (
                TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
                "template.repository_read",
            )
        if repository_failure is not None:
            # Recreate even an already typed repository error after leaving its
            # handler, stripping any raw exception chain supplied by an adapter.
            reject_template(*repository_failure)
        if type(snapshot) is not TemplateByteSnapshot or snapshot.key != key:
            reject_template(
                TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
                "template.repository_result",
            )
        evidence = snapshot.evidence
        if evidence.catalog_verified_on > as_of:
            reject_template(TemplateLoadErrorCode.LIFECYCLE_INVALID, "template.catalog_date")
        if len(snapshot.content) != evidence.byte_size:
            reject_template(TemplateLoadErrorCode.INTEGRITY_MISMATCH, "template.byte_size")
        observed_digest = hashlib.sha256(snapshot.content).hexdigest()
        if not hmac.compare_digest(observed_digest, evidence.sha256):
            reject_template(TemplateLoadErrorCode.INTEGRITY_MISMATCH, "template.sha256")
        if evidence.signature_status is TemplateSignatureStatus.INVALID:
            reject_template(TemplateLoadErrorCode.SIGNATURE_INVALID, "template.signature")

        document = decode_template_bytes(snapshot.content)
        lifecycle = validate_template_data(document, as_of=as_of)
        if (
            document.get("template_id") != key.template_id
            or document.get("template_version") != key.template_version
        ):
            reject_template(TemplateLoadErrorCode.KEY_MISMATCH, "template.identity")
        presentation = _build_presentation(
            document,
            lifecycle,
            sha256=observed_digest,
            catalog_verified_on=evidence.catalog_verified_on,
            signature_status=evidence.signature_status,
        )
        return TemplateSnapshot(
            key=key,
            byte_size=len(snapshot.content),
            sha256=observed_digest,
            freshness=lifecycle.freshness,
            presentation=presentation,
            document=document,
        )


def _build_presentation(
    document: Mapping[str, object],
    lifecycle: TemplateValidationResult,
    *,
    sha256: str,
    catalog_verified_on: date,
    signature_status: TemplateSignatureStatus,
) -> TemplatePresentation:
    provenance = cast(Mapping[str, object], document["provenance"])
    source_url = cast(str | None, provenance["source_url"])
    source_document_sha256 = cast(str | None, provenance["source_document_sha256"])
    fields = [
        TemplateDisplayField("Publisher (template claim)", cast(str, provenance["publisher"])),
        TemplateDisplayField(
            "Source reference (template claim)",
            cast(str, provenance["source_reference"]),
        ),
        TemplateDisplayField(
            "Source kind (template claim)",
            cast(str, provenance["source_kind"]),
        ),
        TemplateDisplayField(
            "Template checksum",
            f"Verified SHA-256 {sha256}",
        ),
        TemplateDisplayField(
            "External signature",
            _signature_status_text(signature_status),
        ),
        TemplateDisplayField(
            "Catalog checksum verified on",
            catalog_verified_on.isoformat(),
        ),
        TemplateDisplayField(
            "Guidance verified on (template claim)",
            lifecycle.verified_on.isoformat(),
        ),
        TemplateDisplayField(
            "Review due on (template claim)",
            lifecycle.review_due_on.isoformat(),
        ),
        TemplateDisplayField(
            "Expires on (template claim)",
            lifecycle.expires_on.isoformat(),
        ),
    ]
    if source_url is not None:
        fields.append(TemplateDisplayField("Source URL (inert template claim)", source_url))
    if source_document_sha256 is not None:
        fields.append(
            TemplateDisplayField(
                "Source document checksum (template claim)",
                source_document_sha256,
            )
        )
    status_message = (
        "Review is due. Confirm that the procedural guidance is still current before using it."
        if lifecycle.freshness is TemplateFreshness.REVIEW_DUE
        else "The template's stated lifecycle dates are current for the selected review date."
    )
    return TemplatePresentation(
        title=cast(str, document["title"]),
        locale=cast(str, document["locale"]),
        freshness=lifecycle.freshness,
        signature_status=signature_status,
        fields=tuple(fields),
        status_message=status_message,
    )


def _signature_status_text(status: TemplateSignatureStatus) -> str:
    if status is TemplateSignatureStatus.VERIFIED:
        return "Verified by an external trusted catalog verifier"
    if status is TemplateSignatureStatus.NOT_PROVIDED:
        return "Not provided; exact checksum verification passed"
    reject_template(TemplateLoadErrorCode.SIGNATURE_INVALID, "template.signature")
