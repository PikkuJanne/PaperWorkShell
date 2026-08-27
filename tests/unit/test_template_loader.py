from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pytest

from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import (
    TemplateArtifactEvidence,
    TemplateByteSnapshot,
    TemplateDisplayField,
    TemplateFreshness,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    TemplateRepository,
    TemplateSignatureStatus,
    TemplateSnapshot,
    copy_template_document,
)

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_TEMPLATE_PATH = ROOT / "schemas" / "examples" / "template-v1-minimal.json"
CANONICAL_KEY = TemplateKey("generic-portal-application", "1.0.0")
CATALOG_VERIFIED_ON = date(2026, 8, 1)


class _Repository:
    def __init__(
        self,
        snapshot: TemplateByteSnapshot | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.failure = failure
        self.calls: list[TemplateKey] = []

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        self.calls.append(key)
        if self.failure is not None:
            raise self.failure
        if self.snapshot is None:  # pragma: no cover - test helper misuse
            raise AssertionError("missing test snapshot")
        return self.snapshot


def _canonical_bytes() -> bytes:
    return CANONICAL_TEMPLATE_PATH.read_bytes()


def _template_bytes(
    mutator: Callable[[dict[str, object]], None] | None = None,
) -> bytes:
    document = cast(dict[str, object], json.loads(_canonical_bytes().decode("utf-8")))
    if mutator is not None:
        mutator(document)
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _evidence(
    content: bytes,
    *,
    byte_size: int | None = None,
    sha256: str | None = None,
    catalog_verified_on: date = CATALOG_VERIFIED_ON,
    signature_status: TemplateSignatureStatus = TemplateSignatureStatus.NOT_PROVIDED,
) -> TemplateArtifactEvidence:
    return TemplateArtifactEvidence(
        byte_size=len(content) if byte_size is None else byte_size,
        sha256=hashlib.sha256(content).hexdigest() if sha256 is None else sha256,
        catalog_verified_on=catalog_verified_on,
        signature_status=signature_status,
    )


def _byte_snapshot(
    content: bytes | None = None,
    *,
    key: TemplateKey = CANONICAL_KEY,
    evidence: TemplateArtifactEvidence | None = None,
) -> TemplateByteSnapshot:
    selected = _canonical_bytes() if content is None else content
    return TemplateByteSnapshot(
        key=key,
        content=selected,
        evidence=_evidence(selected) if evidence is None else evidence,
    )


def _load(
    *,
    content: bytes | None = None,
    key: TemplateKey = CANONICAL_KEY,
    evidence: TemplateArtifactEvidence | None = None,
    as_of: date = date(2026, 8, 2),
) -> tuple[TemplateSnapshot, _Repository]:
    repository = _Repository(_byte_snapshot(content, key=key, evidence=evidence))
    loader = SafeTemplateLoader(repository)
    return loader.load(key, as_of=as_of), repository


def _field_values(snapshot: TemplateSnapshot) -> dict[str, str]:
    presentation = snapshot.presentation
    return {field.label: field.value for field in presentation.fields}


def test_canonical_template_loads_once_with_exact_binding_and_visible_trust_facts() -> None:
    content = _canonical_bytes()
    snapshot, repository = _load(content=content)
    digest = hashlib.sha256(content).hexdigest()

    assert repository.calls == [CANONICAL_KEY]
    assert snapshot.key == CANONICAL_KEY
    assert snapshot.schema_version == 1
    assert snapshot.byte_size == len(content)
    assert snapshot.sha256 == digest
    assert snapshot.freshness is TemplateFreshness.CURRENT
    assert snapshot.presentation.title == "Generic portal application packet"
    assert snapshot.presentation.locale == "en"
    assert snapshot.presentation.signature_status is TemplateSignatureStatus.NOT_PROVIDED
    assert not hasattr(snapshot, "path")

    visible = _field_values(snapshot)
    assert visible["Publisher (template claim)"] == "PaperWorkShell project"
    assert visible["Source reference (template claim)"] == "pws.generic.portal"
    assert visible["Source kind (template claim)"] == "built_in"
    assert visible["Template checksum"] == f"Verified SHA-256 {digest}"
    assert visible["Catalog checksum verified on"] == "2026-08-01"
    assert visible["Guidance verified on (template claim)"] == "2026-08-01"
    assert visible["Review due on (template claim)"] == "2026-12-01"
    assert visible["Expires on (template claim)"] == "2027-08-01"
    assert visible["Source URL (inert template claim)"].startswith("https://")
    assert "does not guarantee eligibility" in snapshot.presentation.provenance_notice


@pytest.mark.parametrize(
    ("as_of", "expected"),
    (
        (date(2026, 8, 1), TemplateFreshness.CURRENT),
        (date(2026, 11, 30), TemplateFreshness.CURRENT),
        (date(2026, 12, 1), TemplateFreshness.REVIEW_DUE),
        (date(2027, 8, 1), TemplateFreshness.REVIEW_DUE),
    ),
)
def test_lifecycle_is_inclusive_and_review_due_starts_on_the_due_date(
    as_of: date,
    expected: TemplateFreshness,
) -> None:
    snapshot, _repository = _load(as_of=as_of)

    assert snapshot.freshness is expected
    if expected is TemplateFreshness.REVIEW_DUE:
        assert "Review is due" in snapshot.presentation.status_message
    else:
        assert "lifecycle dates are current" in snapshot.presentation.status_message


@pytest.mark.parametrize(
    ("as_of", "expected_code"),
    (
        (date(2026, 7, 31), TemplateLoadErrorCode.LIFECYCLE_INVALID),
        (date(2027, 8, 2), TemplateLoadErrorCode.EXPIRED),
    ),
)
def test_template_is_rejected_before_claimed_verification_and_after_expiry(
    as_of: date,
    expected_code: TemplateLoadErrorCode,
) -> None:
    with pytest.raises(TemplateLoadError) as raised:
        _load(as_of=as_of)

    assert raised.value.code is expected_code


def test_catalog_verification_date_cannot_be_later_than_the_evaluation_date() -> None:
    content = _canonical_bytes()
    evidence = _evidence(content, catalog_verified_on=date(2026, 8, 3))

    with pytest.raises(TemplateLoadError) as raised:
        _load(content=content, evidence=evidence, as_of=date(2026, 8, 2))

    assert raised.value.code is TemplateLoadErrorCode.LIFECYCLE_INVALID
    assert raised.value.context == "template.catalog_date"


@pytest.mark.parametrize("failure", ("size", "digest"))
def test_exact_raw_bytes_must_match_catalog_size_and_sha256(failure: str) -> None:
    content = _canonical_bytes()
    evidence = (
        _evidence(content, byte_size=len(content) + 1)
        if failure == "size"
        else _evidence(content, sha256="0" * 64)
    )

    with pytest.raises(TemplateLoadError) as raised:
        _load(content=content, evidence=evidence)

    assert raised.value.code is TemplateLoadErrorCode.INTEGRITY_MISMATCH
    assert hashlib.sha256(content).hexdigest() not in repr(raised.value)


def test_semantically_equivalent_reformatting_is_still_a_different_trust_artifact() -> None:
    canonical = _canonical_bytes()
    compact = _template_bytes()
    assert json.loads(canonical) == json.loads(compact)
    assert canonical != compact
    stale_evidence = _evidence(
        compact,
        byte_size=len(canonical),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )

    with pytest.raises(TemplateLoadError) as raised:
        _load(content=compact, evidence=stale_evidence)

    assert raised.value.code is TemplateLoadErrorCode.INTEGRITY_MISMATCH


def test_invalid_signature_is_rejected_and_verified_signature_is_visible() -> None:
    content = _canonical_bytes()
    invalid = _evidence(content, signature_status=TemplateSignatureStatus.INVALID)
    with pytest.raises(TemplateLoadError) as raised:
        _load(content=content, evidence=invalid)
    assert raised.value.code is TemplateLoadErrorCode.SIGNATURE_INVALID

    verified = _evidence(content, signature_status=TemplateSignatureStatus.VERIFIED)
    snapshot, _repository = _load(content=content, evidence=verified)
    visible = _field_values(snapshot)
    assert snapshot.presentation.signature_status is TemplateSignatureStatus.VERIFIED
    assert visible["External signature"] == "Verified by an external trusted catalog verifier"


def test_decoded_template_identity_must_match_the_catalog_key() -> None:
    def mutate(document: dict[str, object]) -> None:
        document["template_id"] = "different-template"

    content = _template_bytes(mutate)

    with pytest.raises(TemplateLoadError) as raised:
        _load(content=content)

    assert raised.value.code is TemplateLoadErrorCode.KEY_MISMATCH


def test_repository_result_must_match_requested_key_and_have_the_exact_result_type() -> None:
    other_key = TemplateKey("different-template", "1.0.0")
    repository = _Repository(_byte_snapshot(key=other_key))
    loader = SafeTemplateLoader(repository)

    with pytest.raises(TemplateLoadError) as raised:
        loader.load(CANONICAL_KEY, as_of=date(2026, 8, 2))

    assert raised.value.code is TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE
    assert repository.calls == [CANONICAL_KEY]


@pytest.mark.parametrize(
    "template_id",
    (
        "../escape",
        "folder/template",
        "folder\\template",
        "c:template",
        "\\\\server\\share",
        ".",
        "..",
    ),
)
def test_template_keys_are_opaque_tokens_and_never_paths(template_id: str) -> None:
    with pytest.raises(TemplateLoadError) as raised:
        TemplateKey(template_id, "1.0.0")

    assert raised.value.code is TemplateLoadErrorCode.MODEL_INVALID
    if len(template_id) >= 3:
        assert template_id not in repr(raised.value)
        assert template_id not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("byte_size", True),
        ("byte_size", 0),
        ("sha256", "A" * 64),
        ("sha256", "private-invalid-digest"),
        ("catalog_verified_on", datetime(2026, 8, 1)),
        ("signature_status", "verified"),
    ),
)
def test_external_evidence_model_rejects_ambiguous_values_without_echoing_them(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "byte_size": 1,
        "sha256": "1" * 64,
        "catalog_verified_on": CATALOG_VERIFIED_ON,
        "signature_status": TemplateSignatureStatus.NOT_PROVIDED,
    }
    values[field] = value

    with pytest.raises(TemplateLoadError) as raised:
        TemplateArtifactEvidence(
            byte_size=cast(int, values["byte_size"]),
            sha256=cast(str, values["sha256"]),
            catalog_verified_on=cast(date, values["catalog_verified_on"]),
            signature_status=cast(
                TemplateSignatureStatus,
                values["signature_status"],
            ),
        )

    assert raised.value.code is TemplateLoadErrorCode.MODEL_INVALID
    assert str(value) not in repr(raised.value)
    assert str(value) not in str(raised.value)


def test_snapshot_returns_defensive_documents_and_hides_content_and_digests_from_repr() -> None:
    snapshot, _repository = _load()
    first = snapshot.document
    instructions = cast(list[dict[str, object]], first["instructions"])
    instructions[0]["text"] = "caller mutation"
    first["title"] = "caller title"

    second = snapshot.document
    original_instructions = cast(list[dict[str, object]], second["instructions"])
    assert second["title"] == "Generic portal application packet"
    assert original_instructions[0]["text"] != "caller mutation"

    rendered = "\n".join(
        (
            repr(snapshot),
            repr(snapshot.presentation),
            repr(snapshot.presentation.fields[0]),
        )
    )
    assert "Generic portal application packet" not in rendered
    assert "PaperWorkShell project" not in rendered
    assert "pws.generic.portal" not in rendered
    assert snapshot.sha256 not in rendered
    assert "<redacted>" in rendered


def test_snapshot_internal_document_storage_cannot_be_mutated_through_private_reach_in() -> None:
    snapshot, _repository = _load()
    internal = snapshot._document

    with pytest.raises(TypeError):
        cast(dict[str, object], internal)["title"] = "private reach-in mutation"

    assert snapshot.document["title"] == "Generic portal application packet"


def test_defensive_document_copy_rejects_cycles_and_non_json_values() -> None:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    invalid: tuple[Mapping[str, object], ...] = (
        cycle,
        {"tuple": (1, 2)},
        {"bytes": b"private-template-bytes"},
        {"float": 1.5},
        cast(Mapping[str, object], {1: "not-a-string-key"}),
    )

    for document in invalid:
        with pytest.raises(TemplateLoadError) as raised:
            copy_template_document(document)
        assert raised.value.code is TemplateLoadErrorCode.MODEL_INVALID
        assert "private-template-bytes" not in repr(raised.value)


def test_repository_protocol_is_structural_and_loader_maps_raw_failures_without_leakage() -> None:
    repository = _Repository(_byte_snapshot())
    assert isinstance(repository, TemplateRepository)

    canary = "private-path-template-value"
    failing = _Repository(failure=RuntimeError(canary))
    loader = SafeTemplateLoader(failing)
    with pytest.raises(TemplateLoadError) as raised:
        loader.load(CANONICAL_KEY, as_of=date(2026, 8, 2))

    assert raised.value.code is TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE
    assert canary not in repr(raised.value)
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value.args)


def test_display_field_repr_redacts_the_user_visible_value() -> None:
    canary = "visible-only-template-value"
    field = TemplateDisplayField("Publisher (template claim)", canary)

    assert field.value == canary
    assert canary not in repr(field)


def test_request_rejects_datetime_as_an_ambiguous_evaluation_date() -> None:
    loader = SafeTemplateLoader(_Repository(_byte_snapshot()))

    with pytest.raises(TemplateLoadError) as raised:
        loader.load(CANONICAL_KEY, as_of=datetime(2026, 8, 2))

    assert raised.value.code is TemplateLoadErrorCode.MODEL_INVALID


def test_source_mapping_cannot_be_mutated_by_loader() -> None:
    content = _canonical_bytes()
    before = bytes(content)
    snapshot, _repository = _load(content=content)
    returned = snapshot.document
    returned_copy = copy.deepcopy(returned)

    assert content == before
    assert snapshot.document == returned_copy
