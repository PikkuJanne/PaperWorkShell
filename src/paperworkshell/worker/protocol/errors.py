"""Sanitized errors raised while validating the worker protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never


@dataclass(frozen=True, slots=True)
class ProtocolError(ValueError):
    """A stable protocol failure that never includes rejected input values."""

    code: str
    location: str
    safe_message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.location}: {self.safe_message}"


def reject(code: str, location: str, safe_message: str) -> Never:
    """Raise a sanitized protocol failure."""

    raise ProtocolError(code=code, location=location, safe_message=safe_message)
