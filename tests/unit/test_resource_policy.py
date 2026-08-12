"""Central low-resource policy selection, validation, and product binding tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from typing import Any, cast

import pytest

from paperworkshell.__main__ import (
    _desktop_worker_config,
    _import_io_chunk_bytes,
    _import_limits,
    _workspace_quota,
    build_windows_pdf_export_limits,
)
from paperworkshell.domain.pdf_rendering import PdfRenderError, PdfRenderErrorCode
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    RESOURCE_POLICY_VERSION,
    RESOURCE_PROFILE_ENVIRONMENT_VARIABLE,
    ProcessingProfile,
    ResourcePolicy,
    select_resource_policy,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer

MIB = 1024 * 1024


def test_profiles_are_versioned_immutable_and_conservatively_bounded() -> None:
    normal = NORMAL_RESOURCE_POLICY_V1
    constrained = CONSTRAINED_RESOURCE_POLICY_V1

    assert normal.policy_version == constrained.policy_version == RESOURCE_POLICY_VERSION
    assert normal.profile is ProcessingProfile.NORMAL
    assert constrained.profile is ProcessingProfile.CONSTRAINED
    assert constrained.maximum_heavy_operation_concurrency == 1
    assert constrained.preview_prefetch_radius_pages == 1
    assert constrained.thumbnail_preview_cache_bytes == 64 * MIB
    assert constrained.maximum_cache_bytes + constrained.thumbnail_preview_cache_bytes == 128 * MIB
    assert constrained.maximum_preview_pixels_per_page == 4_000_000
    assert 256 * 1024 <= constrained.import_io_chunk_bytes <= 4 * MIB
    assert constrained.canonical_process_tree_rss_bytes == 1_280 * MIB
    assert constrained.minimum_export_free_space_reserve_bytes == 256 * MIB
    assert constrained.maximum_ui_heartbeat_gap_ms == 250
    assert constrained.cancellation_acknowledgement_ms == 2_000
    assert constrained.maximum_active_full_resolution_page_buffers == 1
    assert constrained.maximum_cache_bytes <= normal.maximum_cache_bytes
    assert constrained.maximum_temporary_storage_bytes <= normal.maximum_temporary_storage_bytes
    with pytest.raises(FrozenInstanceError):
        constrained.maximum_cache_bytes = 1  # type: ignore[misc]


def test_profile_selection_is_deterministic_and_fails_closed() -> None:
    assert select_resource_policy(environ={}) is NORMAL_RESOURCE_POLICY_V1
    assert (
        select_resource_policy(environ={RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: " constrained "})
        is CONSTRAINED_RESOURCE_POLICY_V1
    )
    assert select_resource_policy(ProcessingProfile.CONSTRAINED) is (CONSTRAINED_RESOURCE_POLICY_V1)
    assert select_resource_policy("normal") is NORMAL_RESOURCE_POLICY_V1
    private_profile = "PRIVATE-PROFILE-CANARY"
    with pytest.raises(ValueError, match="unsupported resource profile") as raised:
        select_resource_policy(environ={RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: private_profile})
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert private_profile not in repr(raised.value)
    with pytest.raises(ValueError, match="unsupported resource profile"):
        select_resource_policy(environ={RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: "unbounded"})
    with pytest.raises(ValueError, match="unsupported resource profile"):
        select_resource_policy("")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("policy_version", RESOURCE_POLICY_VERSION + 1),
        ("maximum_cache_bytes", CONSTRAINED_RESOURCE_POLICY_V1.maximum_case_bytes + 1),
        ("worker_process_memory_bytes", 0),
        ("preview_prefetch_radius_pages", 5),
        ("import_io_chunk_bytes", 256 * 1024 - 1),
        ("maximum_active_full_resolution_page_buffers", 3),
    ],
)
def test_invalid_policy_amendments_are_rejected(field_name: str, value: int) -> None:
    with pytest.raises(ValueError):
        replace(
            CONSTRAINED_RESOURCE_POLICY_V1,
            **cast(Any, {field_name: value}),
        )


def test_constrained_selection_changes_enforced_composition_limits() -> None:
    normal_quota = _workspace_quota(NORMAL_RESOURCE_POLICY_V1)
    constrained_quota = _workspace_quota(CONSTRAINED_RESOURCE_POLICY_V1)
    constrained_worker = _desktop_worker_config(CONSTRAINED_RESOURCE_POLICY_V1)
    constrained_import = _import_limits(CONSTRAINED_RESOURCE_POLICY_V1)
    constrained_export = build_windows_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)

    assert constrained_quota.maximum_cache_bytes == 64 * MIB
    assert constrained_quota.maximum_cache_bytes < normal_quota.maximum_cache_bytes
    assert constrained_quota.maximum_temp_bytes == 512 * MIB
    assert constrained_worker.resource_policy.maximum_processes == 1
    assert constrained_worker.timeout_policy.cancellation_grace_seconds == (
        CONSTRAINED_RESOURCE_POLICY_V1.cancellation_acknowledgement_ms / 1_000
    )
    assert constrained_worker.resource_policy.process_memory_bytes == 512 * MIB
    assert constrained_worker.thumbnail_policy.limits.pixel_count <= 4_000_000
    assert constrained_import.maximum_source_bytes == (
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_source_bytes
    )
    assert constrained_export.temporary_storage_bytes == 512 * MIB
    assert constrained_export.output_bytes == 256 * MIB
    assert constrained_export.output_bytes < constrained_export.temporary_storage_bytes
    assert _import_io_chunk_bytes(CONSTRAINED_RESOURCE_POLICY_V1) == 1 * MIB


def test_policy_evidence_is_content_free_and_has_no_environment_fields() -> None:
    evidence = CONSTRAINED_RESOURCE_POLICY_V1.to_evidence()

    assert evidence["profile"] == "constrained"
    assert evidence["policy_version"] == RESOURCE_POLICY_VERSION
    assert set(evidence) == {field.name for field in fields(ResourcePolicy)}
    prohibited = ("path", "filename", "metadata", "document", "ocr", "image")
    assert not any(token in key for key in evidence for token in prohibited)


def test_full_resolution_buffer_policy_is_bound_at_the_pdfium_allocation_boundary() -> None:
    renderer = PdfiumPageRenderer()

    assert (
        renderer.maximum_active_full_resolution_page_buffers
        == NORMAL_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
        == CONSTRAINED_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
        == 1
    )
    with pytest.raises(PdfRenderError) as raised:
        PdfiumPageRenderer(maximum_active_full_resolution_page_buffers=2)

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
