"""Validated packaged JSON catalogs and deterministic presentation formatting."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from string import Formatter
from types import MappingProxyType
from typing import cast

CATALOG_VERSION = 1
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CATALOG_MESSAGES = 10_000
MAX_MESSAGE_CHARACTERS = 32_768
_LOCALE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8}){0,3}\Z")
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SPEC = re.compile(r"(?:[<>=^])?[+ -]?(?:0)?\d{0,3}(?:,|_)?(?:\.\d{1,2})?[bcdeEfFgGnosxX%]?\Z")
_FORMATTER = Formatter()
_FILE_GLOB = re.compile(r"\*\.[A-Za-z0-9]+")
_RICH_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^<>]*>")
_STABLE_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_QT_PROGRESS_TOKEN = re.compile(r"%[vmp]")


class LocalizationError(ValueError):
    """Stable failure without translation content, document values, or paths."""

    code = "LOCALIZATION_RESOURCE_INVALID"

    def __init__(self) -> None:
        super().__init__("The language resource could not be used. Choose another language.")


def placeholders(text: str) -> tuple[tuple[str, str, str | None], ...]:
    """Accept simple named fields only; no attribute/index lookup or nested specs."""

    try:
        fields = []
        for _literal, name, spec, conversion in _FORMATTER.parse(text):
            if name is None:
                continue
            spec = spec or ""
            if (
                not _FIELD.fullmatch(name)
                or not _SPEC.fullmatch(spec)
                or conversion
                not in (
                    None,
                    "s",
                    "r",
                )
            ):
                raise LocalizationError()
            fields.append((name, spec, conversion))
        return tuple(sorted(fields, key=lambda item: (item[0], item[1], item[2] or "")))
    except ValueError, TypeError:
        raise LocalizationError() from None


@dataclass(frozen=True, slots=True)
class Catalog:
    """One bounded immutable language resource, never persisted in case state."""

    locale: str
    direction: str
    decimal_separator: str
    group_separator: str
    digits: str
    date_order: str
    date_separator: str
    messages: Mapping[str, str]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LocalizationError()
        result[key] = value
    return result


def validate_catalog(payload: object) -> Catalog:
    """Reject unknown versions/fields, unsafe placeholders and malformed metadata."""

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "locale",
        "direction",
        "number",
        "date",
        "messages",
    }:
        raise LocalizationError()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != CATALOG_VERSION:
        raise LocalizationError()
    locale = payload["locale"]
    direction = payload["direction"]
    number = payload["number"]
    dates = payload["date"]
    messages = payload["messages"]
    if (
        not isinstance(locale, str)
        or not _LOCALE.fullmatch(locale)
        or direction not in ("ltr", "rtl")
        or not isinstance(number, dict)
        or set(number) != {"decimal_separator", "group_separator", "digits"}
        or not isinstance(dates, dict)
        or set(dates) != {"order", "separator"}
        or not isinstance(messages, dict)
        or len(messages) > MAX_CATALOG_MESSAGES
    ):
        raise LocalizationError()
    decimal = number["decimal_separator"]
    group = number["group_separator"]
    digits = number["digits"]
    order = dates["order"]
    separator = dates["separator"]
    if (
        not isinstance(decimal, str)
        or decimal not in (".", ",", "\u066b")
        or not isinstance(group, str)
        or group not in (",", ".", " ", "\u00a0", "\u202f", "\u066c")
        or decimal == group
        or not isinstance(digits, str)
        or len(digits) != 10
        or len(set(digits)) != 10
        or not all(character.isdecimal() for character in digits)
        or order not in ("ymd", "dmy", "mdy")
        or separator not in ("-", "/", ".")
    ):
        raise LocalizationError()
    validated: dict[str, str] = {}
    for source, translation in messages.items():
        if (
            not isinstance(source, str)
            or not isinstance(translation, str)
            or not source
            or not translation
            or len(source) > MAX_MESSAGE_CHARACTERS
            or len(translation) > MAX_MESSAGE_CHARACTERS
            or unicodedata.normalize("NFC", translation) != translation
            or any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"} and character not in "\n\t\r"
                for character in translation
            )
            or placeholders(source) != placeholders(translation)
            or sorted(_FILE_GLOB.findall(source)) != sorted(_FILE_GLOB.findall(translation))
            or sorted(_STABLE_CODE.findall(source)) != sorted(_STABLE_CODE.findall(translation))
            or sorted(_QT_PROGRESS_TOKEN.findall(source))
            != sorted(_QT_PROGRESS_TOKEN.findall(translation))
            or _RICH_TAG.findall(source) != _RICH_TAG.findall(translation)
            or source.count("<") != translation.count("<")
            or source.count(">") != translation.count(">")
        ):
            raise LocalizationError()
        validated[source] = translation
    return Catalog(
        locale,
        cast(str, direction),
        decimal,
        group,
        digits,
        cast(str, order),
        cast(str, separator),
        MappingProxyType(validated),
    )


def normalize_locale(locale: str) -> str:
    """Normalize a locale identifier without allowing paths or user filenames."""

    if not isinstance(locale, str):
        raise LocalizationError()
    normalized = locale.replace("_", "-")
    if not _LOCALE.fullmatch(normalized):
        raise LocalizationError()
    parts = normalized.split("-")
    return "-".join(
        part.lower() if index == 0 else part.title() if len(part) == 4 else part.upper()
        for index, part in enumerate(parts)
    )


def load_catalog(locale: str) -> Catalog:
    """Read a fixed packaged resource only; fallback is a configure concern."""

    normalized = normalize_locale(locale)
    try:
        resource = files("paperworkshell.localization").joinpath("catalogs", normalized + ".json")
        with resource.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise LocalizationError()
        payload: object = json.loads(raw, object_pairs_hook=_unique_object)
        catalog = validate_catalog(payload)
        if catalog.locale != normalized:
            raise LocalizationError()
        return catalog
    except OSError, UnicodeError, json.JSONDecodeError, RecursionError:
        raise LocalizationError() from None


_default_catalog: Catalog | None = None
_context_catalog: ContextVar[Catalog | None] = ContextVar("pws_presentation_locale", default=None)


def _catalog() -> Catalog:
    global _default_catalog
    selected = _context_catalog.get()
    if selected is not None:
        return selected
    if _default_catalog is None:
        _default_catalog = load_catalog("en")
    return _default_catalog


def configure_locale(locale: str = "en") -> str:
    """Select a process-wide presentation language, falling back to English."""

    global _default_catalog
    normalized = normalize_locale(locale)
    candidates = tuple(dict.fromkeys((normalized, normalized.partition("-")[0], "en")))
    available = files("paperworkshell.localization").joinpath("catalogs")
    for candidate in candidates:
        if available.joinpath(candidate + ".json").is_file():
            _default_catalog = load_catalog(candidate)
            return _default_catalog.locale
    raise LocalizationError()


@contextmanager
def localized(locale: str) -> Iterator[None]:
    """Temporarily select an exact installed locale without leaking test state."""

    selected = load_catalog(locale)
    token = _context_catalog.set(selected)
    try:
        yield
    finally:
        _context_catalog.reset(token)


def current_locale() -> str:
    """Return the selected display identifier, independent of template locale."""

    return _catalog().locale


def is_rtl() -> bool:
    """Return layout direction; never change document geometry or storage order."""

    return _catalog().direction == "rtl"


def mark(source: str) -> str:
    """Mark deferred English source text for extraction without translating it."""

    return source


class _PresentationFormatter(Formatter):
    """Localize numeric fields after formatting, preserving string/path digits."""

    def __init__(self, selected: Catalog) -> None:
        super().__init__()
        self._selected = selected

    def format_field(self, value: object, format_spec: str) -> str:
        rendered = cast(str, super().format_field(value, format_spec))
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            replacements = {
                ord("."): self._selected.decimal_separator,
                ord(","): self._selected.group_separator,
                **{
                    ord(digit): target
                    for digit, target in zip("0123456789", self._selected.digits, strict=True)
                },
            }
            return rendered.translate(replacements)
        return rendered


def tr(source: str, **values: object) -> str:
    """Translate known application text and interpolate after translation."""

    selected = _catalog()
    translated = selected.messages.get(source, source)
    if not values:
        return translated
    expected = {name for name, _spec, _conversion in placeholders(source)}
    if expected != set(values):
        raise LocalizationError()
    try:
        return _PresentationFormatter(selected).format(translated, **values)
    except ValueError, TypeError, KeyError, AttributeError:
        raise LocalizationError() from None


def _localized_digits(value: str, selected: Catalog) -> str:
    return value.translate(str.maketrans("0123456789", selected.digits))


def format_number(value: int | float | Decimal, *, decimals: int | None = None) -> str:
    """Format bounded numeric UI values; persisted numeric values remain numbers."""

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise LocalizationError()
    if decimals is not None and (type(decimals) is not int or not 0 <= decimals <= 6):
        raise LocalizationError()
    if isinstance(value, float) and not math.isfinite(value):
        raise LocalizationError()
    try:
        numeric = Decimal(str(value))
        if not numeric.is_finite() or abs(numeric) >= Decimal("1e30"):
            raise LocalizationError()
        places = (
            decimals if decimals is not None else (0 if numeric == numeric.to_integral() else 2)
        )
        ascii_value = f"{numeric:,.{places}f}"
    except InvalidOperation, ValueError:
        raise LocalizationError() from None
    selected = _catalog()
    integer, point, fraction = ascii_value.partition(".")
    rendered = integer.replace(",", selected.group_separator)
    if point:
        rendered += selected.decimal_separator + fraction
    return _localized_digits(rendered, selected)


def format_date(value: date | datetime) -> str:
    """Format a date without changing timezone, machine date, or template meaning."""

    if not isinstance(value, date):
        raise LocalizationError()
    selected = _catalog()
    parts = {"y": f"{value.year:04d}", "m": f"{value.month:02d}", "d": f"{value.day:02d}"}
    return _localized_digits(
        selected.date_separator.join(parts[key] for key in selected.date_order), selected
    )


def format_file_size(byte_count: int) -> str:
    """Display IEC sizes with an exact bytes value available to callers as needed."""

    if type(byte_count) is not int or not 0 <= byte_count < 10**30:
        raise LocalizationError()
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")
    value = Decimal(byte_count)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    rendered = format_number(value, decimals=0 if index == 0 else 1)
    return f"{rendered} {units[index]}"
