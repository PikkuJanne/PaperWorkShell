"""Closed, non-disclosing failures for sanitized diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never


class DiagnosticErrorCode(StrEnum):
    """Closed failure-code registry for the diagnostics boundary."""

    RECORD_INVALID = "DIAGNOSTIC_RECORD_INVALID"
    CONFIGURATION_INVALID = "DIAGNOSTIC_CONFIGURATION_INVALID"
    ENCODING_FAILED = "DIAGNOSTIC_ENCODING_FAILED"
    SINK_FAILED = "DIAGNOSTIC_SINK_FAILED"
    SUMMARY_INVALID = "DIAGNOSTIC_SUMMARY_INVALID"
    BUNDLE_INVALID = "DIAGNOSTIC_BUNDLE_INVALID"
    CONSENT_REQUIRED = "DIAGNOSTIC_CONSENT_REQUIRED"
    CANCELLED = "DIAGNOSTIC_CANCELLED"
    EXPORT_FAILED = "DIAGNOSTIC_EXPORT_FAILED"
    DESTINATION_EXISTS = "DIAGNOSTIC_DESTINATION_EXISTS"
    INTERNAL_ERROR = "DIAGNOSTIC_INTERNAL_ERROR"


class DiagnosticErrorContext(StrEnum):
    """Closed locations that never retain a rejected value or source exception."""

    RECORD = "diagnostic.record"
    CONFIGURATION = "diagnostic.configuration"
    ENCODING = "diagnostic.encoding"
    SINK = "diagnostic.sink"
    SUMMARY = "diagnostic.summary"
    BUNDLE = "diagnostic.bundle"
    INTERNAL = "diagnostic.internal"


_SAFE_MESSAGES: Final = MappingProxyType(
    {
        DiagnosticErrorCode.RECORD_INVALID: (
            "The diagnostic record is outside the documented safe field contract."
        ),
        DiagnosticErrorCode.CONFIGURATION_INVALID: (
            "The diagnostic logger configuration is outside the supported safe range."
        ),
        DiagnosticErrorCode.ENCODING_FAILED: (
            "PaperWorkShell could not encode the sanitized diagnostic record."
        ),
        DiagnosticErrorCode.SINK_FAILED: (
            "PaperWorkShell could not write the sanitized diagnostic record."
        ),
        DiagnosticErrorCode.SUMMARY_INVALID: (
            "The diagnostic summary is outside the documented safe field contract."
        ),
        DiagnosticErrorCode.BUNDLE_INVALID: "The support bundle no longer matches its safe preview. Review a new preview.",
        DiagnosticErrorCode.CONSENT_REQUIRED: "Review the support bundle and choose Save before creating it.",
        DiagnosticErrorCode.CANCELLED: "Support bundle creation was cancelled before publication.",
        DiagnosticErrorCode.EXPORT_FAILED: "The support bundle could not be saved safely. Choose another local folder and try again.",
        DiagnosticErrorCode.DESTINATION_EXISTS: "A file already exists there. Choose a new support bundle filename.",
        DiagnosticErrorCode.INTERNAL_ERROR: (
            "PaperWorkShell could not complete the diagnostic operation safely."
        ),
    }
)


@dataclass(slots=True, repr=False)
class DiagnosticError(RuntimeError):
    """A sanitized failure that never contains caller-controlled text."""

    code: DiagnosticErrorCode
    context: DiagnosticErrorContext

    def __post_init__(self) -> None:
        if type(self.code) is not DiagnosticErrorCode:
            self.code = DiagnosticErrorCode.INTERNAL_ERROR
        if type(self.context) is not DiagnosticErrorContext:
            self.context = DiagnosticErrorContext.INTERNAL
        self.args = (self.code.value, self.context.value, self.safe_message)

    @property
    def safe_message(self) -> str:
        """Return trusted UI/support text selected only by the closed code."""

        code = (
            self.code
            if type(self.code) is DiagnosticErrorCode
            else DiagnosticErrorCode.INTERNAL_ERROR
        )
        return _SAFE_MESSAGES[code]

    def __repr__(self) -> str:
        code = (
            self.code
            if type(self.code) is DiagnosticErrorCode
            else DiagnosticErrorCode.INTERNAL_ERROR
        )
        context = (
            self.context
            if type(self.context) is DiagnosticErrorContext
            else DiagnosticErrorContext.INTERNAL
        )
        return f"DiagnosticError(code={code.value!r}, context={context.value!r})"

    def __str__(self) -> str:
        code = (
            self.code
            if type(self.code) is DiagnosticErrorCode
            else DiagnosticErrorCode.INTERNAL_ERROR
        )
        context = (
            self.context
            if type(self.context) is DiagnosticErrorContext
            else DiagnosticErrorContext.INTERNAL
        )
        return f"{code.value} at {context.value}: {self.safe_message}"


def reject_diagnostic(
    code: DiagnosticErrorCode,
    context: DiagnosticErrorContext,
) -> Never:
    """Raise one sanitized failure without retaining an active source exception."""

    error = DiagnosticError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None
