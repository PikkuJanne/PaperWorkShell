"""Pure, content-free package-export failure contract for adapters and UI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")


class PackageExportErrorCode(StrEnum):
    """Closed, path-free failures before a trustworthy outcome exists."""

    MODEL_INVALID = "PACKAGE_EXPORT_MODEL_INVALID"
    PLAN_STALE = "PACKAGE_EXPORT_PLAN_STALE"
    PLAN_INVALID = "PACKAGE_EXPORT_PLAN_INVALID"
    PDF_FAILED = "PACKAGE_EXPORT_PDF_FAILED"
    ZIP_FAILED = "PACKAGE_EXPORT_ZIP_FAILED"
    DESTINATION_FAILED = "PACKAGE_EXPORT_DESTINATION_FAILED"
    LIMIT_EXCEEDED = "PACKAGE_EXPORT_LIMIT_EXCEEDED"
    CANCELLED = "PACKAGE_EXPORT_CANCELLED"
    TIMED_OUT = "PACKAGE_EXPORT_TIMED_OUT"
    INTERNAL_ERROR = "PACKAGE_EXPORT_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[PackageExportErrorCode, str]] = {
    PackageExportErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid package-export data."),
    PackageExportErrorCode.PLAN_STALE: (
        "The case changed after this package was planned. Refresh the package and try again."
    ),
    PackageExportErrorCode.PLAN_INVALID: (
        "The package plan is not valid for the selected destination."
    ),
    PackageExportErrorCode.PDF_FAILED: (
        "PaperWorkShell could not create and verify every planned PDF."
    ),
    PackageExportErrorCode.ZIP_FAILED: (
        "PaperWorkShell could not create and verify the ZIP package."
    ),
    PackageExportErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not safely publish the verified package."
    ),
    PackageExportErrorCode.LIMIT_EXCEEDED: (
        "The package exceeds a configured processing or temporary-storage limit."
    ),
    PackageExportErrorCode.CANCELLED: "Package export was cancelled before completion.",
    PackageExportErrorCode.TIMED_OUT: "Package export exceeded its configured time limit.",
    PackageExportErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete package export safely."
    ),
}


@dataclass(slots=True, repr=False)
class PackageExportError(ValueError):
    """Sanitized package failure retaining no names, paths, IDs, or digests."""

    code: PackageExportErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackageExportErrorCode:
            self.code = PackageExportErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "package_export.operation"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PackageExportError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_package_export(code: PackageExportErrorCode, context: str) -> Never:
    error = PackageExportError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None
