"""Pure, immutable OCR language-set definitions.

Language-set identifiers are versioned product configuration, not filenames or
Tesseract command fragments.  The worker resolves only these closed definitions
before the separately admitted language files can influence an OCR invocation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from paperworkshell.domain.ocr import OcrErrorCode, reject_ocr

DEFAULT_OCR_LANGUAGE_SET_ID: Final = "latin-default-v1"
GERMAN_OCR_LANGUAGE_SET_ID: Final = "german-fast-v1"
ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID: Final = "english-german-fast-v1"
MAX_OCR_LANGUAGE_CODES_PER_SET: Final = 16
MAX_OCR_LANGUAGE_SET_ID_CODEPOINTS: Final = 64

_OPAQUE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_TESSERACT_LANGUAGE_CODE: Final = re.compile(r"[a-z][a-z0-9_]{1,15}\Z")


def _language_codes_are_well_formed(value: object) -> bool:
    if (
        type(value) is not tuple
        or not 1 <= len(value) <= MAX_OCR_LANGUAGE_CODES_PER_SET
        or any(
            type(language_code) is not str
            or _TESSERACT_LANGUAGE_CODE.fullmatch(language_code) is None
            for language_code in value
        )
    ):
        return False
    return len(set(value)) == len(value)


@dataclass(frozen=True, slots=True)
class OcrLanguageSetDefinition:
    """One closed public set identifier and its canonical Tesseract order."""

    language_set_id: str
    language_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.language_set_id) is not str
            or len(self.language_set_id) > MAX_OCR_LANGUAGE_SET_ID_CODEPOINTS
            or _OPAQUE_TOKEN.fullmatch(self.language_set_id) is None
            or not _language_codes_are_well_formed(self.language_codes)
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_definition")


OCR_LANGUAGE_SET_DEFINITIONS: Final = (
    OcrLanguageSetDefinition(DEFAULT_OCR_LANGUAGE_SET_ID, ("eng",)),
    OcrLanguageSetDefinition(GERMAN_OCR_LANGUAGE_SET_ID, ("deu",)),
    OcrLanguageSetDefinition(
        ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID,
        ("eng", "deu"),
    ),
)

_DEFINITION_BY_ID: Final[Mapping[str, OcrLanguageSetDefinition]] = MappingProxyType(
    {definition.language_set_id: definition for definition in OCR_LANGUAGE_SET_DEFINITIONS}
)
_DEFINITION_BY_CODES: Final[Mapping[frozenset[str], OcrLanguageSetDefinition]] = MappingProxyType(
    {
        frozenset(definition.language_codes): definition
        for definition in OCR_LANGUAGE_SET_DEFINITIONS
    }
)


def resolve_ocr_language_set(language_set_id: str) -> tuple[str, ...]:
    """Resolve one closed set ID to canonical, argument-safe language codes."""

    if (
        type(language_set_id) is not str
        or len(language_set_id) > MAX_OCR_LANGUAGE_SET_ID_CODEPOINTS
        or _OPAQUE_TOKEN.fullmatch(language_set_id) is None
    ):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_set")
    try:
        return _DEFINITION_BY_ID[language_set_id].language_codes
    except KeyError:
        reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_set")


def select_ocr_language_set(language_codes: tuple[str, ...]) -> str:
    """Return the canonical set ID for one unique installed-language selection.

    Caller ordering is intentionally ignored.  The returned identifier resolves
    back to the product-defined canonical engine order.
    """

    if not _language_codes_are_well_formed(language_codes):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_selection")
    try:
        return _DEFINITION_BY_CODES[frozenset(language_codes)].language_set_id
    except KeyError:
        reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_selection")
