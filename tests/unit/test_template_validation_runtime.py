from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.templates import validation as runtime_validation
from paperworkshell.templates.codec import decode_template_bytes
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import (
    MAX_TEMPLATE_BYTES,
    MAX_TEMPLATE_COLLECTION_ITEMS,
    MAX_TEMPLATE_DEPTH,
    MAX_TEMPLATE_NODES,
    MAX_TEMPLATE_STRING_CODEPOINTS,
    TemplateArtifactEvidence,
    TemplateByteSnapshot,
    TemplateFreshness,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    TemplateSignatureStatus,
    TemplateSnapshot,
)
from paperworkshell.templates.validation import (
    CANONICAL_TEMPLATE_SCHEMA_FINGERPRINT,
    runtime_template_schema_fingerprint,
    validate_template_data,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "template-v1.schema.json"
CANONICAL_TEMPLATE_PATH = ROOT / "schemas" / "examples" / "template-v1-minimal.json"
INVALID_FIXTURE_DIRECTORY = ROOT / "tests" / "fixtures" / "templates" / "v1" / "invalid"
CANONICAL_KEY = TemplateKey("generic-portal-application", "1.0.0")
AS_OF = date(2026, 8, 1)

EXPECTED_NEGATIVE_CODES = {
    "duplicate-key.json": TemplateLoadErrorCode.JSON_DUPLICATE_KEY,
    "eligibility-decision.json": TemplateLoadErrorCode.ELIGIBILITY_MEMBER,
    "expired.json": TemplateLoadErrorCode.EXPIRED,
    "future-version.json": TemplateLoadErrorCode.VERSION_UNSUPPORTED,
    "legacy-version.json": TemplateLoadErrorCode.VERSION_UNSUPPORTED,
    "script-injection.json": TemplateLoadErrorCode.EXECUTABLE_MEMBER,
    "trust-assertion.json": TemplateLoadErrorCode.TRUST_ASSERTION_MEMBER,
}


class _OneTemplateRepository:
    def __init__(self, snapshot: TemplateByteSnapshot) -> None:
        self._snapshot = snapshot

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        del key
        return self._snapshot


def _canonical_document() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(CANONICAL_TEMPLATE_PATH.read_text(encoding="utf-8")),
    )


def _json_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _key_for_payload(payload: bytes) -> TemplateKey:
    try:
        document = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return CANONICAL_KEY
    if type(document) is dict:
        template_id = document.get("template_id")
        template_version = document.get("template_version")
        if type(template_id) is str and type(template_version) is str:
            return TemplateKey(template_id, template_version)
    return CANONICAL_KEY


def _load_payload(payload: bytes, *, as_of: date = AS_OF) -> TemplateSnapshot:
    key = _key_for_payload(payload)
    evidence = TemplateArtifactEvidence(
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        catalog_verified_on=AS_OF,
        signature_status=TemplateSignatureStatus.NOT_PROVIDED,
    )
    repository = _OneTemplateRepository(TemplateByteSnapshot(key, payload, evidence))
    return SafeTemplateLoader(repository).load(key, as_of=as_of)


def test_packaged_runtime_schema_matches_canonical_contract_and_pinned_fingerprint() -> None:
    canonical_bytes = SCHEMA_PATH.read_bytes()
    packaged_bytes = (
        files("paperworkshell.templates").joinpath("template-v1.schema.json").read_bytes()
    )

    assert packaged_bytes == canonical_bytes
    assert json.loads(packaged_bytes) == json.loads(canonical_bytes)
    assert (
        runtime_template_schema_fingerprint()
        == CANONICAL_TEMPLATE_SCHEMA_FINGERPRINT
        == "dd156071a2ec546f6fafa663168b49b93cf8298e782b4d2d21564eab3b38a06f"
    )


def test_substituted_packaged_schema_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    tampered["title"] = "synthetic substituted template contract"
    payload = _json_bytes(tampered)

    class _TamperedResource:
        def joinpath(self, _name: str) -> _TamperedResource:
            return self

        def read_bytes(self) -> bytes:
            return payload

    runtime_validation._runtime_schema.cache_clear()
    monkeypatch.setattr(runtime_validation, "files", lambda _package: _TamperedResource())
    with pytest.raises(TemplateLoadError) as raised:
        runtime_template_schema_fingerprint()

    assert raised.value.code is TemplateLoadErrorCode.SCHEMA_IDENTITY_MISMATCH
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert "substituted" not in repr(raised.value)
    runtime_validation._runtime_schema.cache_clear()


def test_canonical_runtime_validation_is_deterministic_and_does_not_mutate_input() -> None:
    document = _canonical_document()
    before = copy.deepcopy(document)

    first = validate_template_data(document, as_of=date(2026, 8, 1))
    second = validate_template_data(document, as_of=date(2026, 8, 1))

    assert first == second
    assert first.verified_on == date(2026, 8, 1)
    assert first.review_due_on == date(2026, 12, 1)
    assert first.expires_on == date(2027, 8, 1)
    assert first.freshness is TemplateFreshness.CURRENT
    assert document == before


@pytest.mark.parametrize(
    ("fixture_name", "expected_code"),
    tuple(sorted(EXPECTED_NEGATIVE_CODES.items())),
)
def test_complete_reviewed_negative_corpus_fails_for_the_expected_runtime_reason(
    fixture_name: str,
    expected_code: TemplateLoadErrorCode,
) -> None:
    payload = (INVALID_FIXTURE_DIRECTORY / fixture_name).read_bytes()

    with pytest.raises(TemplateLoadError) as raised:
        _load_payload(payload)

    assert raised.value.code is expected_code
    rendered = f"{raised.value!r}\n{raised.value}\n{raised.value.args!r}"
    assert str(INVALID_FIXTURE_DIRECTORY) not in rendered
    assert "synthetic condition" not in rendered
    assert "synthetic-command" not in rendered


def test_negative_fixture_inventory_is_exact_and_no_case_is_silently_uncovered() -> None:
    observed = {path.name for path in INVALID_FIXTURE_DIRECTORY.glob("*.json")}

    assert observed == set(EXPECTED_NEGATIVE_CODES)


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"", TemplateLoadErrorCode.JSON_INVALID),
        (b"\xff", TemplateLoadErrorCode.JSON_INVALID),
        (b"\xef\xbb\xbf{}", TemplateLoadErrorCode.JSON_INVALID),
        (b"[]", TemplateLoadErrorCode.JSON_INVALID),
        (b'{"schema_version":1,"schema_version":1}', TemplateLoadErrorCode.JSON_DUPLICATE_KEY),
        (b'{"schema_version":1,"number":1.5}', TemplateLoadErrorCode.JSON_NUMBER_INVALID),
        (b'{"schema_version":1,"number":NaN}', TemplateLoadErrorCode.JSON_NUMBER_INVALID),
        (
            b'{"schema_version":1,"number":9007199254740992}',
            TemplateLoadErrorCode.JSON_NUMBER_INVALID,
        ),
    ),
)
def test_strict_json_codec_rejects_invalid_encoding_roots_keys_and_numbers(
    payload: bytes,
    expected_code: TemplateLoadErrorCode,
) -> None:
    with pytest.raises(TemplateLoadError) as raised:
        decode_template_bytes(payload)

    assert raised.value.code is expected_code
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None


def test_codec_enforces_exact_byte_string_collection_depth_and_node_limits() -> None:
    with pytest.raises(TemplateLoadError) as oversized:
        decode_template_bytes(b" " * (MAX_TEMPLATE_BYTES + 1))
    assert oversized.value.code is TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED

    too_long_string = _json_bytes(
        {"schema_version": 1, "text": "s" * (MAX_TEMPLATE_STRING_CODEPOINTS + 1)}
    )
    with pytest.raises(TemplateLoadError) as string_error:
        decode_template_bytes(too_long_string)
    assert string_error.value.code is TemplateLoadErrorCode.JSON_RESOURCE_LIMIT

    too_many_items = _json_bytes(
        {"schema_version": 1, "items": [0] * (MAX_TEMPLATE_COLLECTION_ITEMS + 1)}
    )
    with pytest.raises(TemplateLoadError) as collection_error:
        decode_template_bytes(too_many_items)
    assert collection_error.value.code is TemplateLoadErrorCode.JSON_RESOURCE_LIMIT

    nested: dict[str, object] = {"leaf": 1}
    for _index in range(MAX_TEMPLATE_DEPTH):
        nested = {"nested": nested}
    with pytest.raises(TemplateLoadError) as depth_error:
        decode_template_bytes(_json_bytes(nested))
    assert depth_error.value.code is TemplateLoadErrorCode.JSON_RESOURCE_LIMIT

    node_heavy = {
        "schema_version": 1,
        "items": [
            {"a": index, "b": index, "c": index, "d": index}
            for index in range(MAX_TEMPLATE_COLLECTION_ITEMS)
        ],
    }
    assert len(_json_bytes(node_heavy)) < MAX_TEMPLATE_BYTES
    with pytest.raises(TemplateLoadError) as node_error:
        decode_template_bytes(_json_bytes(node_heavy))
    assert node_error.value.code is TemplateLoadErrorCode.JSON_RESOURCE_LIMIT
    assert MAX_TEMPLATE_NODES == 32_768


@pytest.mark.parametrize(
    ("version", "expected_code"),
    (
        (None, TemplateLoadErrorCode.VERSION_INVALID),
        (True, TemplateLoadErrorCode.VERSION_INVALID),
        ("1", TemplateLoadErrorCode.VERSION_INVALID),
        (0, TemplateLoadErrorCode.VERSION_UNSUPPORTED),
        (2, TemplateLoadErrorCode.VERSION_UNSUPPORTED),
    ),
)
def test_runtime_version_dispatch_fails_closed_before_schema_or_semantic_use(
    version: object,
    expected_code: TemplateLoadErrorCode,
) -> None:
    document: dict[str, object] = {"unexpected_future_member": "private future value"}
    if version is not None:
        document["schema_version"] = version

    with pytest.raises(TemplateLoadError) as raised:
        validate_template_data(document, as_of=AS_OF)

    assert raised.value.code is expected_code
    assert "private future value" not in repr(raised.value)


@pytest.mark.parametrize(
    ("member", "expected_code"),
    (
        ("script", TemplateLoadErrorCode.EXECUTABLE_MEMBER),
        ("command-line", TemplateLoadErrorCode.EXECUTABLE_MEMBER),
        ("path", TemplateLoadErrorCode.EXECUTABLE_MEMBER),
        ("eligibility_rule", TemplateLoadErrorCode.ELIGIBILITY_MEMBER),
        ("signature_verified", TemplateLoadErrorCode.TRUST_ASSERTION_MEMBER),
    ),
)
def test_prohibited_capabilities_fail_before_generic_schema_validation(
    member: str,
    expected_code: TemplateLoadErrorCode,
) -> None:
    document = _canonical_document()
    document[member] = "private hostile value"

    with pytest.raises(TemplateLoadError) as raised:
        validate_template_data(document, as_of=AS_OF)

    assert raised.value.code is expected_code
    assert "private hostile value" not in repr(raised.value)


def test_unsafe_unicode_schema_failure_and_semantic_failure_have_closed_codes() -> None:
    unsafe = _canonical_document()
    unsafe["title"] = "synthetic\u202econtrol"
    with pytest.raises(TemplateLoadError) as unsafe_error:
        validate_template_data(unsafe, as_of=AS_OF)
    assert unsafe_error.value.code is TemplateLoadErrorCode.TEXT_UNSAFE

    schema_invalid = _canonical_document()
    del schema_invalid["title"]
    with pytest.raises(TemplateLoadError) as schema_error:
        validate_template_data(schema_invalid, as_of=AS_OF)
    assert schema_error.value.code is TemplateLoadErrorCode.SCHEMA_INVALID

    semantic_invalid = _canonical_document()
    requirements = semantic_invalid["requirements"]
    assert isinstance(requirements, list)
    count = requirements[0]["count"]
    assert isinstance(count, dict)
    count["minimum"] = 2
    count["maximum"] = 1
    with pytest.raises(TemplateLoadError) as semantic_error:
        validate_template_data(semantic_invalid, as_of=AS_OF)
    assert semantic_error.value.code is TemplateLoadErrorCode.SEMANTIC_INVALID


def test_https_provenance_remains_inert_but_credentials_fragments_and_file_urls_reject() -> None:
    valid = _canonical_document()
    provenance = valid["provenance"]
    assert isinstance(provenance, dict)
    provenance["source_url"] = "https://synthetic.invalid/templates/review"
    assert validate_template_data(valid, as_of=AS_OF).freshness is TemplateFreshness.CURRENT

    for url in (
        "file:///private/template.json",
        "https://user:password@synthetic.invalid/template",
        "https://synthetic.invalid/template#fragment",
        "https://synthetic.invalid\\redirect",
    ):
        invalid = copy.deepcopy(valid)
        invalid_provenance = invalid["provenance"]
        assert isinstance(invalid_provenance, dict)
        invalid_provenance["source_url"] = url
        with pytest.raises(TemplateLoadError) as raised:
            validate_template_data(invalid, as_of=AS_OF)
        assert raised.value.code is (
            TemplateLoadErrorCode.SCHEMA_INVALID
            if url.startswith("file:")
            else TemplateLoadErrorCode.SEMANTIC_INVALID
        )
        assert url not in repr(raised.value)
