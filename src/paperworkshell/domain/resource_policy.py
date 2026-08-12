"""Versioned, content-free resource budgets for bounded document processing."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Final

RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: Final = "PWS_RESOURCE_PROFILE"
RESOURCE_POLICY_VERSION: Final = 1
_MIB: Final = 1024 * 1024
_GIB: Final = 1024 * _MIB
_MINIMUM_IMPORT_CHUNK_BYTES: Final = 256 * 1024
_MAXIMUM_IMPORT_CHUNK_BYTES: Final = 4 * _MIB


class ProcessingProfile(StrEnum):
    """Closed names for the bounded policies supported by this release."""

    NORMAL = "normal"
    CONSTRAINED = "constrained"


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Immutable application, worker, UI, and measurement resource budgets."""

    profile: ProcessingProfile
    policy_version: int
    maximum_case_bytes: int
    maximum_cache_bytes: int
    maximum_temporary_storage_bytes: int
    maximum_source_bytes: int
    maximum_page_count: int
    worker_process_memory_bytes: int
    worker_job_memory_bytes: int
    worker_cpu_time_ms: int
    worker_maximum_processes: int
    maximum_heavy_operation_concurrency: int
    maximum_preview_request_pages: int
    preview_prefetch_radius_pages: int
    thumbnail_preview_cache_bytes: int
    maximum_preview_pixels_per_page: int
    import_io_chunk_bytes: int
    canonical_process_tree_rss_bytes: int
    minimum_export_free_space_reserve_bytes: int
    maximum_ui_heartbeat_gap_ms: int
    cancellation_acknowledgement_ms: int
    maximum_active_full_resolution_page_buffers: int
    resource_sampling_interval_ms: int

    def __post_init__(self) -> None:
        if type(self.profile) is not ProcessingProfile:
            raise ValueError("profile must be a supported ProcessingProfile")
        integer_values = tuple(
            getattr(self, field.name) for field in fields(self) if field.name != "profile"
        )
        if any(type(value) is not int or value <= 0 for value in integer_values):
            raise ValueError("resource budgets must be positive integers")
        if self.policy_version != RESOURCE_POLICY_VERSION:
            raise ValueError("unsupported resource policy version")
        if self.maximum_cache_bytes > self.maximum_case_bytes:
            raise ValueError("cache budget cannot exceed the case budget")
        if self.maximum_temporary_storage_bytes > self.maximum_case_bytes:
            raise ValueError("temporary-storage budget cannot exceed the case budget")
        if self.maximum_source_bytes > self.maximum_case_bytes:
            raise ValueError("source budget cannot exceed the case budget")
        if self.worker_process_memory_bytes > self.worker_job_memory_bytes:
            raise ValueError("per-process memory cannot exceed process-tree memory")
        if self.maximum_heavy_operation_concurrency > self.worker_maximum_processes:
            raise ValueError("heavy-operation concurrency cannot exceed the process limit")
        if self.maximum_preview_request_pages > 32:
            raise ValueError("preview request capacity exceeds the supported bounded batch")
        if self.preview_prefetch_radius_pages > 4:
            raise ValueError("preview prefetch radius exceeds the supported bounded window")
        if self.thumbnail_preview_cache_bytes > self.maximum_case_bytes:
            raise ValueError("preview memory-cache budget cannot exceed the case budget")
        if (
            not _MINIMUM_IMPORT_CHUNK_BYTES
            <= self.import_io_chunk_bytes
            <= (_MAXIMUM_IMPORT_CHUNK_BYTES)
        ):
            raise ValueError("import I/O chunk must be between 256 KiB and 4 MiB")
        if self.canonical_process_tree_rss_bytes < self.worker_job_memory_bytes:
            raise ValueError("canonical process-tree budget cannot be below the worker job limit")
        if self.minimum_export_free_space_reserve_bytes > self.maximum_case_bytes:
            raise ValueError("export safety reserve cannot exceed the case budget")
        if self.maximum_active_full_resolution_page_buffers > 2:
            raise ValueError("at most two full-resolution buffers are supported")

    def to_evidence(self) -> dict[str, str | int]:
        """Serialize only bounded policy values suitable for sanitized evidence."""

        evidence: dict[str, str | int] = {"profile": self.profile.value}
        evidence.update(
            {
                field.name: getattr(self, field.name)
                for field in fields(self)
                if field.name != "profile"
            }
        )
        return evidence


NORMAL_RESOURCE_POLICY_V1: Final = ResourcePolicy(
    profile=ProcessingProfile.NORMAL,
    policy_version=RESOURCE_POLICY_VERSION,
    maximum_case_bytes=4 * _GIB,
    maximum_cache_bytes=512 * _MIB,
    maximum_temporary_storage_bytes=1 * _GIB,
    maximum_source_bytes=512 * _MIB,
    maximum_page_count=1_000,
    worker_process_memory_bytes=768 * _MIB,
    worker_job_memory_bytes=1 * _GIB,
    worker_cpu_time_ms=120_000,
    worker_maximum_processes=2,
    maximum_heavy_operation_concurrency=1,
    maximum_preview_request_pages=8,
    preview_prefetch_radius_pages=1,
    thumbnail_preview_cache_bytes=256 * _MIB,
    maximum_preview_pixels_per_page=4_000_000,
    import_io_chunk_bytes=1 * _MIB,
    canonical_process_tree_rss_bytes=1_280 * _MIB,
    minimum_export_free_space_reserve_bytes=256 * _MIB,
    maximum_ui_heartbeat_gap_ms=250,
    cancellation_acknowledgement_ms=2_000,
    maximum_active_full_resolution_page_buffers=1,
    resource_sampling_interval_ms=50,
)

CONSTRAINED_RESOURCE_POLICY_V1: Final = ResourcePolicy(
    profile=ProcessingProfile.CONSTRAINED,
    policy_version=RESOURCE_POLICY_VERSION,
    maximum_case_bytes=2 * _GIB,
    maximum_cache_bytes=64 * _MIB,
    maximum_temporary_storage_bytes=512 * _MIB,
    maximum_source_bytes=512 * _MIB,
    maximum_page_count=1_000,
    worker_process_memory_bytes=512 * _MIB,
    worker_job_memory_bytes=768 * _MIB,
    worker_cpu_time_ms=120_000,
    worker_maximum_processes=2,
    maximum_heavy_operation_concurrency=1,
    maximum_preview_request_pages=3,
    preview_prefetch_radius_pages=1,
    thumbnail_preview_cache_bytes=64 * _MIB,
    maximum_preview_pixels_per_page=4_000_000,
    import_io_chunk_bytes=1 * _MIB,
    canonical_process_tree_rss_bytes=1_280 * _MIB,
    minimum_export_free_space_reserve_bytes=256 * _MIB,
    maximum_ui_heartbeat_gap_ms=250,
    cancellation_acknowledgement_ms=2_000,
    maximum_active_full_resolution_page_buffers=1,
    resource_sampling_interval_ms=50,
)


def select_resource_policy(
    profile: ProcessingProfile | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResourcePolicy:
    """Select one real bounded policy deterministically, failing closed on bad input."""

    selected: ProcessingProfile
    if profile is None:
        environment = os.environ if environ is None else environ
        raw_profile = environment.get(RESOURCE_PROFILE_ENVIRONMENT_VARIABLE)
        selected = ProcessingProfile.NORMAL if raw_profile is None else _parse_profile(raw_profile)
    elif type(profile) is ProcessingProfile:
        selected = profile
    elif type(profile) is str:
        selected = _parse_profile(profile)
    else:
        raise ValueError("resource profile must be a string or ProcessingProfile")
    if selected is ProcessingProfile.NORMAL:
        return NORMAL_RESOURCE_POLICY_V1
    if selected is ProcessingProfile.CONSTRAINED:
        return CONSTRAINED_RESOURCE_POLICY_V1
    raise ValueError("unsupported resource profile")


def _parse_profile(value: str) -> ProcessingProfile:
    try:
        return ProcessingProfile(value.strip().lower())
    except ValueError:
        pass
    raise ValueError("unsupported resource profile") from None
