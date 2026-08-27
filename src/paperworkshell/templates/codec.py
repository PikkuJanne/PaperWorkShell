"""Strict, bounded JSON decoding for untrusted declarative template bytes."""

from __future__ import annotations

import json
from collections import deque
from typing import Never

from paperworkshell.templates.models import (
    MAX_TEMPLATE_BYTES,
    MAX_TEMPLATE_COLLECTION_ITEMS,
    MAX_TEMPLATE_DEPTH,
    MAX_TEMPLATE_NODES,
    MAX_TEMPLATE_STRING_CODEPOINTS,
    TemplateLoadErrorCode,
    reject_template,
)

_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class _DuplicateJsonKeyError(ValueError):
    pass


class _InvalidJsonNumberError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise _InvalidJsonNumberError


def _reject_float(_value: str) -> Never:
    raise _InvalidJsonNumberError


def _bounded_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise _InvalidJsonNumberError from error
    if not -_MAX_SAFE_INTEGER <= parsed <= _MAX_SAFE_INTEGER:
        raise _InvalidJsonNumberError
    return parsed


def decode_template_bytes(payload: bytes) -> dict[str, object]:
    """Decode one exact immutable byte snapshot without dereferencing its content."""

    if type(payload) is not bytes:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.payload")
    if not payload:
        reject_template(TemplateLoadErrorCode.JSON_INVALID, "template.json")
    if len(payload) > MAX_TEMPLATE_BYTES:
        reject_template(TemplateLoadErrorCode.SIZE_LIMIT_EXCEEDED, "template.bytes")
    loaded: object = None
    failure_code: TemplateLoadErrorCode | None = None
    try:
        text = payload.decode("utf-8", errors="strict")
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_bounded_integer,
        )
    except _DuplicateJsonKeyError:
        failure_code = TemplateLoadErrorCode.JSON_DUPLICATE_KEY
    except _InvalidJsonNumberError:
        failure_code = TemplateLoadErrorCode.JSON_NUMBER_INVALID
    except UnicodeDecodeError, json.JSONDecodeError:
        failure_code = TemplateLoadErrorCode.JSON_INVALID
    except MemoryError, RecursionError:
        failure_code = TemplateLoadErrorCode.JSON_RESOURCE_LIMIT
    except Exception:
        failure_code = TemplateLoadErrorCode.JSON_INVALID
    if failure_code is not None:
        # Raise only after the parser exception has left active exception state so
        # traceback consumers cannot recover raw input through ``__context__``.
        reject_template(failure_code, "template.json")
    if type(loaded) is not dict:
        reject_template(TemplateLoadErrorCode.JSON_INVALID, "template.root")
    _validate_resource_limits(loaded)
    return loaded


def _validate_resource_limits(root: dict[str, object]) -> None:
    nodes = 0
    queue: deque[tuple[object, int]] = deque([(root, 1)])
    memory_exhausted = False
    try:
        while queue:
            value, depth = queue.popleft()
            nodes += 1
            if nodes > MAX_TEMPLATE_NODES or depth > MAX_TEMPLATE_DEPTH:
                reject_template(TemplateLoadErrorCode.JSON_RESOURCE_LIMIT, "template.structure")
            if type(value) is str:
                if len(value) > MAX_TEMPLATE_STRING_CODEPOINTS:
                    reject_template(TemplateLoadErrorCode.JSON_RESOURCE_LIMIT, "template.string")
                continue
            if type(value) is list:
                if len(value) > MAX_TEMPLATE_COLLECTION_ITEMS:
                    reject_template(
                        TemplateLoadErrorCode.JSON_RESOURCE_LIMIT,
                        "template.collection",
                    )
                queue.extend((item, depth + 1) for item in value)
                continue
            if type(value) is dict:
                if len(value) > MAX_TEMPLATE_COLLECTION_ITEMS:
                    reject_template(
                        TemplateLoadErrorCode.JSON_RESOURCE_LIMIT,
                        "template.collection",
                    )
                for key, item in value.items():
                    if type(key) is not str or len(key) > MAX_TEMPLATE_STRING_CODEPOINTS:
                        reject_template(
                            TemplateLoadErrorCode.JSON_RESOURCE_LIMIT,
                            "template.member",
                        )
                    queue.append((key, depth + 1))
                    queue.append((item, depth + 1))
                continue
            if value is not None and type(value) not in {bool, int}:
                reject_template(TemplateLoadErrorCode.JSON_INVALID, "template.value")
    except MemoryError:
        memory_exhausted = True
    if memory_exhausted:
        reject_template(TemplateLoadErrorCode.JSON_RESOURCE_LIMIT, "template.structure")
