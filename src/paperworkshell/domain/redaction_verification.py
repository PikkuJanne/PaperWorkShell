"""Closed, value-free evidence for the separately gated redaction verifier.

Evidence describes exact candidate bytes. It never grants publication authority,
changes removal-mark state, or constitutes independent M6 release approval.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import chain
from typing import Final, Never, Self

from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export

VERIFICATION_PROFILE: Final = "redaction-verification-v1"
VIEWER_VERIFICATION_PROFILE: Final = "redaction-viewer-resave-verification-v1"
RUNTIME_VERIFICATION_PROFILE: Final = "redaction-runtime-verification-v1"
# Set only after the exact supported implementation receives independent M6 approval.
QUALIFIED_RUNTIME_PROFILE: Final[str | None] = RUNTIME_VERIFICATION_PROFILE
VERIFICATION_VERSION: Final = 1
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EMPTY_PROBES: Final = hashlib.sha256(b"[[],[]]").hexdigest()


def verification_probe_digest(tokens: tuple[str, ...], stream_hashes: tuple[str, ...]) -> str:
    """Bind the ephemeral probes without serializing their values in evidence."""
    if (
        type(tokens) is not tuple
        or type(stream_hashes) is not tuple
        or any(type(value) is not str for value in chain(tokens, stream_hashes))
    ):
        _invalid()
    for value in stream_hashes:
        _digest(value)
    return hashlib.sha256(
        json.dumps(
            [list(tokens), list(stream_hashes)], ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class VerificationMethod(StrEnum):
    STRUCTURE = "objects_revisions_features"
    TEXT = "text_selection_search_extraction"
    IMAGES = "image_extraction"
    RENDER = "rendering"
    RESAVE = "save_resave"
    OCR = "independent_ocr"
    VIEWERS = "viewer_matrix"


class VerificationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class VerificationCode(StrEnum):
    SCRATCH_ACCESS_DENIED = "RV_SCRATCH_ACCESS_DENIED"
    CLEAR = "RV_CLEAR"
    CANARY = "RV_CANARY_RECOVERED"
    REVISION = "RV_REVISION_RETAINED"
    FEATURE = "RV_FEATURE_RETAINED"
    ORIGINAL_OBJECT = "RV_ORIGINAL_OBJECT_RETAINED"
    UNSUPPORTED = "RV_STRUCTURE_UNSUPPORTED"
    FAILED = "RV_METHOD_FAILED"
    MISSING = "RV_METHOD_MISSING"
    INVALID = "RV_RESULT_INVALID"
    LIMIT = "RV_LIMIT_EXCEEDED"
    CANCELLED = "RV_CANCELLED"
    TIMED_OUT = "RV_TIMED_OUT"
    IMAGE = "RV_IMAGE_MISMATCH"
    RENDER = "RV_RENDER_MISMATCH"
    TEXT = "RV_TEXT_MISMATCH"
    BINDING = "RV_INPUT_CHANGED"
    RESAVE = "RV_RESAVE_FAILED"


AUTOMATED_METHODS: Final = tuple(
    method
    for method in VerificationMethod
    if method not in {VerificationMethod.OCR, VerificationMethod.VIEWERS}
)
RUNTIME_METHODS: Final = (*AUTOMATED_METHODS, VerificationMethod.OCR)


def runtime_release_qualified() -> bool:
    """Read compiled build qualification; no evidence record can supply it."""
    from paperworkshell.domain import redaction

    return (
        redaction.PERMANENT_REDACTION_ENABLED
        and QUALIFIED_RUNTIME_PROFILE == RUNTIME_VERIFICATION_PROFILE
    )


def _invalid() -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "verification.model")


def _digest(value: object) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class RedactionVerificationRequest:
    candidate_sha256: str = field(repr=False)
    candidate_byte_size: int
    reference_sha256: str = field(repr=False)
    reference_byte_size: int
    page_count: int
    affected_pages: tuple[int, ...]
    intent_sha256: str = field(repr=False)
    probes_sha256: str = field(default=_EMPTY_PROBES, repr=False)
    profile_id: str = VERIFICATION_PROFILE
    request_version: int = VERIFICATION_VERSION

    def __post_init__(self) -> None:
        for value in (
            self.candidate_sha256,
            self.reference_sha256,
            self.intent_sha256,
            self.probes_sha256,
        ):
            _digest(value)
        if (
            any(
                type(value) is not int or not 1 <= value <= 2**53 - 1
                for value in (self.candidate_byte_size, self.reference_byte_size)
            )
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= 4096
            or type(self.affected_pages) is not tuple
            or len(self.affected_pages) > self.page_count
            or any(
                type(value) is not int or not 0 <= value < self.page_count
                for value in self.affected_pages
            )
            or tuple(sorted(set(self.affected_pages))) != self.affected_pages
            or type(self.profile_id) is not str
            or self.profile_id
            not in {VERIFICATION_PROFILE, VIEWER_VERIFICATION_PROFILE, RUNTIME_VERIFICATION_PROFILE}
            or type(self.request_version) is not int
            or self.request_version != VERIFICATION_VERSION
        ):
            _invalid()

    def __repr__(self) -> str:
        return f"RedactionVerificationRequest(profile_id={self.profile_id!r})"

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            name: list(value) if name == "affected_pages" else value
            for name in self.__dataclass_fields__
            for value in (getattr(self, name),)
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_record(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def from_record(cls, value: object) -> Self:
        if (
            not isinstance(value, Mapping)
            or set(value) != set(cls.__dataclass_fields__)
            or type(value["affected_pages"]) is not list
        ):
            _invalid()
        return cls(
            candidate_sha256=value["candidate_sha256"],
            candidate_byte_size=value["candidate_byte_size"],
            reference_sha256=value["reference_sha256"],
            reference_byte_size=value["reference_byte_size"],
            page_count=value["page_count"],
            affected_pages=tuple(value["affected_pages"]),
            intent_sha256=value["intent_sha256"],
            probes_sha256=value["probes_sha256"],
            profile_id=value["profile_id"],
            request_version=value["request_version"],
        )


@dataclass(frozen=True, slots=True)
class VerificationMethodResult:
    method: VerificationMethod
    status: VerificationStatus
    code: VerificationCode

    def __post_init__(self) -> None:
        if (
            type(self.method) is not VerificationMethod
            or type(self.status) is not VerificationStatus
            or type(self.code) is not VerificationCode
            or (self.status is VerificationStatus.PASS) != (self.code is VerificationCode.CLEAR)
            or (self.status is VerificationStatus.UNKNOWN)
            != (self.code is VerificationCode.MISSING)
        ):
            _invalid()

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {"method": self.method.value, "status": self.status.value, "code": self.code.value}

    @classmethod
    def from_record(cls, value: object) -> Self:
        if not isinstance(value, Mapping) or set(value) != {"method", "status", "code"}:
            _invalid()
        try:
            return cls(
                VerificationMethod(value["method"]),
                VerificationStatus(value["status"]),
                VerificationCode(value["code"]),
            )
        except ValueError, TypeError:
            _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class RedactionVerificationReport:
    request_sha256: str = field(repr=False)
    candidate_sha256: str = field(repr=False)
    methods: tuple[VerificationMethodResult, ...]
    report_version: int = VERIFICATION_VERSION
    unsigned_derivative: bool = True

    def __post_init__(self) -> None:
        _digest(self.request_sha256)
        _digest(self.candidate_sha256)
        if (
            type(self.methods) is not tuple
            or len(self.methods) != len(VerificationMethod)
            or any(type(item) is not VerificationMethodResult for item in self.methods)
            or tuple(item.method for item in self.methods) != tuple(VerificationMethod)
            or type(self.report_version) is not int
            or self.report_version != VERIFICATION_VERSION
            or self.unsigned_derivative is not True
        ):
            _invalid()
        for item in self.methods:
            item.__post_init__()

    def __repr__(self) -> str:
        return f"RedactionVerificationReport(automated_passed={self.automated_passed}, release_ready={self.release_ready})"

    @property
    def automated_passed(self) -> bool:
        self.__post_init__()
        return all(
            item.status is VerificationStatus.PASS
            for item in self.methods
            if item.method in AUTOMATED_METHODS
        )

    @property
    def release_ready(self) -> bool:
        """Method completeness only; PWS-0608 approval remains separately required."""
        self.__post_init__()
        return all(item.status is VerificationStatus.PASS for item in self.methods)

    @property
    def runtime_ready(self) -> bool:
        """Exact-file methods only; no viewer testimony or release authority.

        This is usable only with an independently authenticated runtime-profile
        request. The historical all-seven ``release_ready`` meaning is unchanged.
        Viewer qualification belongs to the frozen implementation/profile gate;
        its status must remain unknown for a file not opened in that matrix.
        """
        self.__post_init__()
        return all(
            item.status is VerificationStatus.PASS
            if item.method in RUNTIME_METHODS
            else item.status is VerificationStatus.UNKNOWN
            for item in self.methods
        )

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "request_sha256": self.request_sha256,
            "candidate_sha256": self.candidate_sha256,
            "methods": [item.to_record() for item in self.methods],
            "report_version": self.report_version,
            "unsigned_derivative": self.unsigned_derivative,
        }

    @classmethod
    def from_record(cls, value: object) -> Self:
        if (
            not isinstance(value, Mapping)
            or set(value) != set(cls.__dataclass_fields__)
            or type(value["methods"]) is not list
        ):
            _invalid()
        return cls(
            request_sha256=value["request_sha256"],
            candidate_sha256=value["candidate_sha256"],
            methods=tuple(VerificationMethodResult.from_record(item) for item in value["methods"]),
            report_version=value["report_version"],
            unsigned_derivative=value["unsigned_derivative"],
        )


def run_verification_methods(
    request: RedactionVerificationRequest,
    runners: Mapping[VerificationMethod, Callable[[], VerificationMethodResult]],
) -> RedactionVerificationReport:
    """Run every required method; exceptions, wrong identities and omissions fail closed."""
    if type(request) is not RedactionVerificationRequest or any(
        type(key) is not VerificationMethod for key in runners
    ):
        _invalid()
    request.__post_init__()
    results = []
    for method in VerificationMethod:
        runner = runners.get(method)
        if runner is None:
            result = VerificationMethodResult(
                method, VerificationStatus.UNKNOWN, VerificationCode.MISSING
            )
        else:
            try:
                result = runner()
                if type(result) is not VerificationMethodResult or result.method is not method:
                    raise ValueError
                result.__post_init__()
            except Exception:
                result = VerificationMethodResult(
                    method, VerificationStatus.FAIL, VerificationCode.INVALID
                )
        results.append(result)
    return RedactionVerificationReport(
        request.fingerprint, request.candidate_sha256, tuple(results)
    )


def validate_verification_report(
    request: RedactionVerificationRequest, report: RedactionVerificationReport
) -> None:
    if (
        type(request) is not RedactionVerificationRequest
        or type(report) is not RedactionVerificationReport
    ):
        _invalid()
    request.__post_init__()
    report.__post_init__()
    if (
        report.request_sha256 != request.fingerprint
        or report.candidate_sha256 != request.candidate_sha256
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "verification.binding")
