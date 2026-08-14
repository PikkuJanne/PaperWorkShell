"""Validate sanitized PWS-0208 constrained incremental-workload evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Self, cast

from paperworkshell.domain.ocr import TRUSTED_OCRMYPDF_VERSION, TRUSTED_TESSERACT_VERSION
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    RESOURCE_POLICY_VERSION,
)
from paperworkshell.worker.pdf_export_report import (
    PDFIUM_EXPORT_ENGINE_VERSION,
    PIKEPDF_EXPORT_ENGINE_VERSION,
    PYPDFIUM2_EXPORT_ENGINE_VERSION,
    QPDF_EXPORT_ENGINE_VERSION,
)

PWS0208_RESOURCE_REPORT_ID: Final = "pws0208-resource-evidence"
PWS0208_RESOURCE_REPORT_VERSION: Final = 1
PWS0208_WORKLOAD_ID: Final = "pws0208-searchable-mixed-incremental"
PWS0208_WORKLOAD_VERSION: Final = 1
PWS0208_WORKLOAD_PAGE_COUNT: Final = 12
PWS0208_WORKLOAD_SOURCE_COUNT: Final = 3
PWS0208_WORKLOAD_SHA256: Final = "ce04fcca1b67fa212d790ddda5074baf82f2d55e8212fc5467f879fbab8dd70a"
PWS0208_MAXIMUM_CANCELLATION_ACKNOWLEDGEMENT_MS: Final = 2_000
_MAXIMUM_DOCUMENT_BYTES: Final = 16 * 1024
_PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
_WORKLOAD_FIXTURE_PATHS: Final[dict[str, Path]] = {
    "pdf-ocr-readable-mixed": Path("pdf/ocr-readable-mixed.pdf"),
    "png-ocr-raster-text": Path("image/ocr-raster-text.png"),
    "pdf-signed-marker": Path("pdf/signed-marker.pdf"),
}

_WORKLOAD_DOCUMENT: Final[dict[str, object]] = {
    "workload_id": PWS0208_WORKLOAD_ID,
    "workload_version": PWS0208_WORKLOAD_VERSION,
    "policy_profile": CONSTRAINED_RESOURCE_POLICY_V1.profile.value,
    "policy_version": RESOURCE_POLICY_VERSION,
    "source_count": PWS0208_WORKLOAD_SOURCE_COUNT,
    "page_count": PWS0208_WORKLOAD_PAGE_COUNT,
    "sources": [
        {
            "fixture_id": "pdf-ocr-readable-mixed",
            "sha256": "27cd473b273d4b7fe6ea8183e39a5b4523f66021b5b3208fad5499084fc02f3a",
            "derivation": "append pages 0 then 1, repeated five times",
        },
        {
            "fixture_id": "png-ocr-raster-text",
            "sha256": "f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82",
            "derivation": "one image page",
        },
        {
            "fixture_id": "pdf-signed-marker",
            "sha256": "520a27e1952cdad2d936729041dd28c7ce7bbb150510cac9ab7481485976c3e4",
            "derivation": "one retained marker-present PDF page",
        },
    ],
    "signed_source_confirmation": {
        "confirmation_code": "signed-source-unsigned-derivative",
        "statement_version": 1,
    },
    "scheduler": {
        "maximum_active_full_resolution_page_buffers": 1,
        "maximum_active_page_results": 1,
    },
    "sampling_interval_ms": CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms,
    "active_page_measurement_scope": "focused-production-adapter-instrumentation",
    "scenarios": [
        "confirmed-success",
        "cancel-after-four-pages",
        "one-byte-output-limit",
        "fresh-retry",
    ],
}

_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_MEMBERS: Final = frozenset(
    {
        "report_id",
        "report_version",
        "workload_id",
        "workload_version",
        "workload_sha256",
        "revision",
        "policy_profile",
        "policy_version",
        "source_count",
        "page_count",
        "input_bytes",
        "output_bytes",
        "output_sha256",
        "ocrmypdf_version",
        "tesseract_version",
        "pikepdf_version",
        "qpdf_version",
        "pypdfium2_version",
        "pdfium_version",
        "preparing_duration_ms",
        "confirmed_success_duration_ms",
        "cancellation_duration_ms",
        "limit_failure_duration_ms",
        "retry_duration_ms",
        "total_duration_ms",
        "complete_process_tree_rss_limit_bytes",
        "peak_complete_process_tree_rss_bytes",
        "complete_process_tree_rss_sample_count",
        "worker_process_memory_limit_bytes",
        "worker_job_memory_limit_bytes",
        "peak_worker_process_commit_bytes",
        "peak_worker_job_commit_bytes",
        "temporary_storage_limit_bytes",
        "sampled_worker_private_temp_peak_bytes",
        "worker_private_temp_sample_count",
        "worker_private_temp_sampling_interval_ms",
        "worker_private_temp_measurement_complete",
        "maximum_active_full_resolution_page_buffers",
        "peak_active_full_resolution_page_buffers",
        "maximum_active_page_results",
        "peak_active_page_results",
        "active_page_measurement_scope",
        "maximum_heavy_operation_concurrency",
        "peak_heavy_operation_concurrency",
        "cancellation_acknowledgement_ms",
        "limit_failure_code",
        "limit_failure_operation_correlated",
        "worker_tree_terminated_after_cancel",
        "worker_tree_terminated_after_limit",
        "cancel_cleanup_complete",
        "limit_cleanup_complete",
        "retry_succeeded",
        "originals_unchanged",
        "manifest_expected_mutations_only",
        "manifest_unchanged_after_cancel_and_limit",
        "destination_absent_after_cancel",
        "destination_absent_after_limit",
        "overall_pass",
    }
)


@dataclass(frozen=True, slots=True)
class Pws0208ResourceEvidence:
    """Versioned numeric evidence with no document or machine identity."""

    revision: str
    input_bytes: int
    output_bytes: int
    output_sha256: str = field(repr=False)
    preparing_duration_ms: int
    confirmed_success_duration_ms: int
    cancellation_duration_ms: int
    limit_failure_duration_ms: int
    retry_duration_ms: int
    total_duration_ms: int
    peak_complete_process_tree_rss_bytes: int
    complete_process_tree_rss_sample_count: int
    peak_worker_process_commit_bytes: int
    peak_worker_job_commit_bytes: int
    sampled_worker_private_temp_peak_bytes: int
    worker_private_temp_sample_count: int
    worker_private_temp_measurement_complete: bool
    peak_active_full_resolution_page_buffers: int
    peak_active_page_results: int
    peak_heavy_operation_concurrency: int
    cancellation_acknowledgement_ms: int
    limit_failure_code: str
    limit_failure_operation_correlated: bool
    worker_tree_terminated_after_cancel: bool
    worker_tree_terminated_after_limit: bool
    cancel_cleanup_complete: bool
    limit_cleanup_complete: bool
    retry_succeeded: bool
    originals_unchanged: bool
    manifest_expected_mutations_only: bool
    manifest_unchanged_after_cancel_and_limit: bool
    destination_absent_after_cancel: bool
    destination_absent_after_limit: bool
    overall_pass: bool
    report_id: str = PWS0208_RESOURCE_REPORT_ID
    report_version: int = PWS0208_RESOURCE_REPORT_VERSION
    workload_id: str = PWS0208_WORKLOAD_ID
    workload_version: int = PWS0208_WORKLOAD_VERSION
    workload_sha256: str = PWS0208_WORKLOAD_SHA256
    policy_profile: str = CONSTRAINED_RESOURCE_POLICY_V1.profile.value
    policy_version: int = RESOURCE_POLICY_VERSION
    ocrmypdf_version: str = TRUSTED_OCRMYPDF_VERSION
    tesseract_version: str = TRUSTED_TESSERACT_VERSION
    pikepdf_version: str = PIKEPDF_EXPORT_ENGINE_VERSION
    qpdf_version: str = QPDF_EXPORT_ENGINE_VERSION
    pypdfium2_version: str = PYPDFIUM2_EXPORT_ENGINE_VERSION
    pdfium_version: str = PDFIUM_EXPORT_ENGINE_VERSION
    source_count: int = PWS0208_WORKLOAD_SOURCE_COUNT
    page_count: int = PWS0208_WORKLOAD_PAGE_COUNT
    complete_process_tree_rss_limit_bytes: int = (
        CONSTRAINED_RESOURCE_POLICY_V1.canonical_process_tree_rss_bytes
    )
    worker_process_memory_limit_bytes: int = (
        CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
    )
    worker_job_memory_limit_bytes: int = CONSTRAINED_RESOURCE_POLICY_V1.worker_job_memory_bytes
    temporary_storage_limit_bytes: int = (
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
    )
    worker_private_temp_sampling_interval_ms: int = (
        CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms
    )
    maximum_active_full_resolution_page_buffers: int = (
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
    )
    maximum_active_page_results: int = 1
    active_page_measurement_scope: str = "focused-production-adapter-instrumentation"
    maximum_heavy_operation_concurrency: int = 1

    def __post_init__(self) -> None:
        fixed_strings = (
            self.report_id,
            self.workload_id,
            self.workload_sha256,
            self.policy_profile,
            self.ocrmypdf_version,
            self.tesseract_version,
            self.pikepdf_version,
            self.qpdf_version,
            self.pypdfium2_version,
            self.pdfium_version,
            self.active_page_measurement_scope,
        )
        fixed_integers = (
            self.report_version,
            self.workload_version,
            self.policy_version,
            self.source_count,
            self.page_count,
            self.complete_process_tree_rss_limit_bytes,
            self.worker_process_memory_limit_bytes,
            self.worker_job_memory_limit_bytes,
            self.temporary_storage_limit_bytes,
            self.worker_private_temp_sampling_interval_ms,
            self.maximum_active_full_resolution_page_buffers,
            self.maximum_active_page_results,
            self.maximum_heavy_operation_concurrency,
        )
        if (
            type(self.revision) is not str
            or _SHA1.fullmatch(self.revision) is None
            or type(self.output_sha256) is not str
            or _SHA256.fullmatch(self.output_sha256) is None
            or type(self.limit_failure_code) is not str
            or _TOKEN.fullmatch(self.limit_failure_code.lower()) is None
            or any(type(value) is not str for value in fixed_strings)
            or any(type(value) is not int for value in fixed_integers)
            or self.report_id != PWS0208_RESOURCE_REPORT_ID
            or self.report_version != PWS0208_RESOURCE_REPORT_VERSION
            or self.workload_id != PWS0208_WORKLOAD_ID
            or self.workload_version != PWS0208_WORKLOAD_VERSION
            or self.workload_sha256 != PWS0208_WORKLOAD_SHA256
            or self.policy_profile != CONSTRAINED_RESOURCE_POLICY_V1.profile.value
            or self.policy_version != RESOURCE_POLICY_VERSION
            or self.ocrmypdf_version != TRUSTED_OCRMYPDF_VERSION
            or self.tesseract_version != TRUSTED_TESSERACT_VERSION
            or self.pikepdf_version != PIKEPDF_EXPORT_ENGINE_VERSION
            or self.qpdf_version != QPDF_EXPORT_ENGINE_VERSION
            or self.pypdfium2_version != PYPDFIUM2_EXPORT_ENGINE_VERSION
            or self.pdfium_version != PDFIUM_EXPORT_ENGINE_VERSION
            or self.source_count != PWS0208_WORKLOAD_SOURCE_COUNT
            or self.page_count != PWS0208_WORKLOAD_PAGE_COUNT
            or self.complete_process_tree_rss_limit_bytes
            != CONSTRAINED_RESOURCE_POLICY_V1.canonical_process_tree_rss_bytes
            or self.worker_process_memory_limit_bytes
            != CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes
            or self.worker_job_memory_limit_bytes
            != CONSTRAINED_RESOURCE_POLICY_V1.worker_job_memory_bytes
            or self.temporary_storage_limit_bytes
            != CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
            or self.worker_private_temp_sampling_interval_ms
            != CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms
            or self.maximum_active_full_resolution_page_buffers
            != CONSTRAINED_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
            or self.maximum_active_page_results != 1
            or self.active_page_measurement_scope != "focused-production-adapter-instrumentation"
            or self.maximum_heavy_operation_concurrency != 1
        ):
            raise ValueError("PWS-0208 evidence identity is invalid")
        positive = (
            self.input_bytes,
            self.output_bytes,
            self.preparing_duration_ms,
            self.confirmed_success_duration_ms,
            self.cancellation_duration_ms,
            self.limit_failure_duration_ms,
            self.retry_duration_ms,
            self.total_duration_ms,
            self.peak_complete_process_tree_rss_bytes,
            self.complete_process_tree_rss_sample_count,
            self.peak_worker_process_commit_bytes,
            self.peak_worker_job_commit_bytes,
            self.sampled_worker_private_temp_peak_bytes,
            self.worker_private_temp_sample_count,
            self.peak_active_full_resolution_page_buffers,
            self.peak_active_page_results,
            self.peak_heavy_operation_concurrency,
        )
        booleans = (
            self.worker_private_temp_measurement_complete,
            self.cancel_cleanup_complete,
            self.limit_cleanup_complete,
            self.limit_failure_operation_correlated,
            self.worker_tree_terminated_after_cancel,
            self.worker_tree_terminated_after_limit,
            self.retry_succeeded,
            self.originals_unchanged,
            self.manifest_expected_mutations_only,
            self.manifest_unchanged_after_cancel_and_limit,
            self.destination_absent_after_cancel,
            self.destination_absent_after_limit,
            self.overall_pass,
        )
        if (
            any(type(value) is not int or value < 1 for value in positive)
            or any(type(value) is not bool for value in booleans)
            or type(self.cancellation_acknowledgement_ms) is not int
            or self.cancellation_acknowledgement_ms < 0
        ):
            raise ValueError("PWS-0208 evidence values are invalid")
        if (
            self.total_duration_ms
            < self.preparing_duration_ms
            + self.confirmed_success_duration_ms
            + self.cancellation_duration_ms
            + self.limit_failure_duration_ms
            + self.retry_duration_ms
            or self.peak_heavy_operation_concurrency > self.maximum_heavy_operation_concurrency
        ):
            raise ValueError("PWS-0208 evidence duration or concurrency is invalid")
        passed = (
            self.peak_complete_process_tree_rss_bytes <= self.complete_process_tree_rss_limit_bytes
            and self.peak_worker_process_commit_bytes <= self.worker_process_memory_limit_bytes
            and self.peak_worker_job_commit_bytes <= self.worker_job_memory_limit_bytes
            and self.peak_worker_process_commit_bytes <= self.peak_worker_job_commit_bytes
            and self.sampled_worker_private_temp_peak_bytes <= self.temporary_storage_limit_bytes
            and self.worker_private_temp_measurement_complete
            and self.peak_active_full_resolution_page_buffers
            <= self.maximum_active_full_resolution_page_buffers
            and self.peak_active_page_results <= self.maximum_active_page_results
            and self.cancellation_acknowledgement_ms
            <= PWS0208_MAXIMUM_CANCELLATION_ACKNOWLEDGEMENT_MS
            and self.limit_failure_code == "PDF_EXPORT_LIMIT_EXCEEDED"
            and self.limit_failure_operation_correlated
            and self.worker_tree_terminated_after_cancel
            and self.worker_tree_terminated_after_limit
            and self.peak_heavy_operation_concurrency <= self.maximum_heavy_operation_concurrency
            and self.cancel_cleanup_complete
            and self.limit_cleanup_complete
            and self.retry_succeeded
            and self.originals_unchanged
            and self.manifest_expected_mutations_only
            and self.manifest_unchanged_after_cancel_and_limit
            and self.destination_absent_after_cancel
            and self.destination_absent_after_limit
        )
        if self.overall_pass is not passed:
            raise ValueError("PWS-0208 evidence overall result does not match its budgets")

    def to_document(self) -> dict[str, object]:
        """Return the exact closed JSON object in stable report order."""

        return {name: getattr(self, name) for name in sorted(_MEMBERS)}

    @classmethod
    def from_document(cls, value: object) -> Self:
        """Decode the exact report object, rejecting extensions and coercion."""

        if type(value) is not dict or frozenset(value) != _MEMBERS:
            raise ValueError("PWS-0208 evidence members are invalid")
        record = cast(dict[str, object], value)
        return cls(**record)  # type: ignore[arg-type]


def encode_pws0208_resource_evidence(value: Pws0208ResourceEvidence) -> bytes:
    """Encode canonical newline-terminated ASCII JSON."""

    if type(value) is not Pws0208ResourceEvidence:
        raise ValueError("PWS-0208 evidence model is invalid")
    return (
        json.dumps(
            value.to_document(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def decode_pws0208_resource_evidence(payload: bytes) -> Pws0208ResourceEvidence:
    """Decode canonical evidence and reject duplicate keys or alternate bytes."""

    if type(payload) is not bytes or not 1 <= len(payload) <= _MAXIMUM_DOCUMENT_BYTES:
        raise ValueError("PWS-0208 evidence bytes are invalid")

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("PWS-0208 evidence has a duplicate member")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        result = Pws0208ResourceEvidence.from_document(document)
    except UnicodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError("PWS-0208 evidence is invalid") from None
    if encode_pws0208_resource_evidence(result) != payload:
        raise ValueError("PWS-0208 evidence is not canonical")
    return result


def validate_report(path: Path) -> Pws0208ResourceEvidence:
    """Validate one local evidence file without emitting its contents."""

    if not isinstance(path, Path):
        raise ValueError("PWS-0208 evidence path is invalid")
    return decode_pws0208_resource_evidence(_read_bounded_file(path))


def validate_workload(path: Path) -> None:
    """Validate the exact public-synthetic workload descriptor and its identity."""

    if not isinstance(path, Path):
        raise ValueError("PWS-0208 workload path is invalid")
    payload = _read_bounded_file(path)
    if hashlib.sha256(payload).hexdigest() != PWS0208_WORKLOAD_SHA256:
        raise ValueError("PWS-0208 workload identity is invalid")

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("PWS-0208 workload has a duplicate member")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError("PWS-0208 workload is invalid") from None
    if document != _WORKLOAD_DOCUMENT:
        raise ValueError("PWS-0208 workload contract is invalid")
    fixture_root = _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1"
    sources = cast(list[dict[str, object]], _WORKLOAD_DOCUMENT["sources"])
    for source in sources:
        fixture_id = cast(str, source["fixture_id"])
        expected_sha256 = cast(str, source["sha256"])
        fixture_path = fixture_root / _WORKLOAD_FIXTURE_PATHS[fixture_id]
        try:
            observed_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        except OSError:
            raise ValueError("PWS-0208 workload fixture is unavailable") from None
        if observed_sha256 != expected_sha256:
            raise ValueError("PWS-0208 workload fixture identity is invalid")


def _read_bounded_file(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(_MAXIMUM_DOCUMENT_BYTES + 1)
    except OSError:
        raise ValueError("PWS-0208 evidence input is unavailable") from None
    if not 1 <= len(payload) <= _MAXIMUM_DOCUMENT_BYTES:
        raise ValueError("PWS-0208 evidence input size is invalid")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path)
    parser.add_argument("--workload", type=Path)
    args = parser.parse_args(argv)
    if args.workload is None and args.report is None:
        parser.error("a report or --workload is required")
    if args.workload is not None:
        validate_workload(args.workload)
        print("PWS-0208 constrained incremental workload: valid")
    if args.report is not None:
        validate_report(args.report)
        print("PWS-0208 constrained incremental resource evidence: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
