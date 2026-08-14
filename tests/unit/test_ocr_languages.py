"""PWS-0205 closed OCR language-set domain tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID,
    GERMAN_OCR_LANGUAGE_SET_ID,
    OCR_LANGUAGE_SET_DEFINITIONS,
    OcrLanguageSetDefinition,
    resolve_ocr_language_set,
    select_ocr_language_set,
)


def test_closed_language_sets_resolve_to_canonical_engine_order() -> None:
    assert resolve_ocr_language_set(DEFAULT_OCR_LANGUAGE_SET_ID) == ("eng",)
    assert resolve_ocr_language_set(GERMAN_OCR_LANGUAGE_SET_ID) == ("deu",)
    assert resolve_ocr_language_set(ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID) == (
        "eng",
        "deu",
    )


def test_inverse_selection_is_order_independent_and_canonical() -> None:
    assert select_ocr_language_set(("eng",)) == DEFAULT_OCR_LANGUAGE_SET_ID
    assert select_ocr_language_set(("deu",)) == GERMAN_OCR_LANGUAGE_SET_ID
    assert select_ocr_language_set(("deu", "eng")) == ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID
    assert resolve_ocr_language_set(select_ocr_language_set(("deu", "eng"))) == (
        "eng",
        "deu",
    )


@pytest.mark.parametrize(
    ("language_set_id", "expected_code"),
    [
        ("eng;run-command", OcrErrorCode.MODEL_INVALID),
        ("a" * 65, OcrErrorCode.MODEL_INVALID),
        ("unsupported-v1", OcrErrorCode.LANGUAGE_UNAVAILABLE),
    ],
)
def test_invalid_or_unknown_set_id_has_sanitized_failure(
    language_set_id: str,
    expected_code: OcrErrorCode,
) -> None:
    with pytest.raises(OcrError) as captured:
        resolve_ocr_language_set(language_set_id)

    assert captured.value.code is expected_code
    assert captured.value.context == "ocr.language_set"


@pytest.mark.parametrize(
    ("language_codes", "expected_code"),
    [
        (("eng", "eng"), OcrErrorCode.MODEL_INVALID),
        (("eng;run-command",), OcrErrorCode.MODEL_INVALID),
        (("fra",), OcrErrorCode.LANGUAGE_UNAVAILABLE),
    ],
)
def test_invalid_or_unsupported_selection_has_sanitized_failure(
    language_codes: tuple[str, ...],
    expected_code: OcrErrorCode,
) -> None:
    with pytest.raises(OcrError) as captured:
        select_ocr_language_set(language_codes)

    assert captured.value.code is expected_code
    assert captured.value.context == "ocr.language_selection"


def test_definitions_are_immutable() -> None:
    assert type(OCR_LANGUAGE_SET_DEFINITIONS) is tuple
    with pytest.raises(FrozenInstanceError):
        OCR_LANGUAGE_SET_DEFINITIONS[0].language_set_id = "changed"  # type: ignore[misc]


def test_definition_rejects_malformed_codes() -> None:
    with pytest.raises(OcrError) as captured:
        OcrLanguageSetDefinition("unsafe-v1", ("eng+deu",))

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.language_definition"


def test_unhashable_selection_member_is_sanitized_as_invalid() -> None:
    hostile = cast(tuple[str, ...], (["eng"],))

    with pytest.raises(OcrError) as captured:
        select_ocr_language_set(hostile)

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.language_selection"
