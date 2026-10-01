"""Staged target-size decisions reject unbounded, reordered, and false evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import PdfImageStageProfile
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
)
from paperworkshell.domain.target_size_search import (
    TARGET_SIZE_PROFILE_LADDER,
    TargetSizeAttempt,
    TargetSizeDecisionReport,
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)

_SEMANTIC = "a" * 64
_SIZE_CODE = "PORTAL_TOTAL_SIZE_EXCEEDED"


def _policy() -> TargetSizeSearchPolicy:
    return TargetSizeSearchPolicy.for_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _attempt(ordinal: int = 1, *, success: bool = False) -> TargetSizeAttempt:
    return TargetSizeAttempt(
        ordinal=ordinal,
        profile=TARGET_SIZE_PROFILE_LADDER[ordinal - 1],
        pdf_byte_sizes=(1_000, 2_000),
        archive_byte_size=None,
        constraints_satisfied=success,
        blocking_codes=() if success else (_SIZE_CODE,),
    )


def _report() -> TargetSizeDecisionReport:
    return TargetSizeDecisionReport(
        semantic_fingerprint=_SEMANTIC,
        policy=_policy(),
        attempts=(_attempt(), _attempt(2, success=True)),
        terminal=TargetSizeSearchTerminal.SATISFIED,
        selected_profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90,
    )


def test_search_starts_safe_and_ends_at_existing_color_preserving_floor() -> None:
    policy = _policy()
    assert policy.profiles == (
        None,
        PdfImageStageProfile.PRESERVE_300_DPI_Q90,
        PdfImageStageProfile.PRESERVE_240_DPI_Q85,
        PdfImageStageProfile.PRESERVE_200_DPI_Q80,
    )
    assert policy.max_duration_ms == CONSTRAINED_RESOURCE_POLICY_V1.worker_cpu_time_ms * 4
    assert policy.profiles[-1] is not None
    assert policy.profiles[-1].floor_dpi == 200
    for maximum in range(1, 5):
        assert replace(policy, max_attempts=maximum).profiles == policy.profiles[:maximum]
    with pytest.raises(FrozenInstanceError):
        policy.max_attempts = 8  # type: ignore[misc]


def test_custom_resource_policy_changes_duration_and_complete_resource_fingerprint() -> None:
    constrained = _policy()
    custom = replace(CONSTRAINED_RESOURCE_POLICY_V1, worker_cpu_time_ms=15)
    reduced = TargetSizeSearchPolicy.for_resource_policy(custom)
    assert reduced.max_duration_ms == 60
    assert reduced.resource_policy_fingerprint != constrained.resource_policy_fingerprint
    assert (
        reduced.resource_policy_fingerprint
        == hashlib.sha256(
            json.dumps(custom.to_evidence(), sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
    )
    custom_storage = replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_temporary_storage_bytes=20_000)
    assert (
        TargetSizeSearchPolicy.for_resource_policy(custom_storage).resource_policy_fingerprint
        != constrained.resource_policy_fingerprint
    )
    normal = TargetSizeSearchPolicy.for_resource_policy(NORMAL_RESOURCE_POLICY_V1)
    assert normal.resource_policy_fingerprint != constrained.resource_policy_fingerprint
    assert _report().resource_policy_fingerprint == constrained.resource_policy_fingerprint


@pytest.mark.parametrize(
    "changes",
    (
        {"resource_policy": "constrained"},
        {"max_attempts": 0},
        {"max_attempts": 5},
        {"max_attempts": True},
        {"max_attempts": 2.0},
        {"max_duration_ms": 0},
        {"max_duration_ms": -1},
        {"max_duration_ms": True},
        {"max_duration_ms": 480_001},
        {"max_duration_ms": MAX_SAFE_MANIFEST_INTEGER + 1},
        {"policy_version": True},
        {"policy_version": 0},
        {"policy_version": 2},
    ),
)
def test_search_policy_rejects_invalid_or_unbounded_configuration(
    changes: dict[str, object],
) -> None:
    with pytest.raises(PdfExportError) as caught:
        replace(_policy(), **changes)  # type: ignore[arg-type]
    assert caught.value.code is PdfExportErrorCode.MODEL_INVALID


def test_policy_factory_rejects_unknown_resource_profile_and_invalid_override() -> None:
    with pytest.raises(PdfExportError):
        TargetSizeSearchPolicy.for_resource_policy("constrained")  # type: ignore[arg-type]
    with pytest.raises(PdfExportError):
        TargetSizeSearchPolicy.for_resource_policy(
            CONSTRAINED_RESOURCE_POLICY_V1, max_duration_ms=True
        )
    assert (
        TargetSizeSearchPolicy.for_resource_policy(
            CONSTRAINED_RESOURCE_POLICY_V1, max_attempts=1, max_duration_ms=1
        ).max_duration_ms
        == 1
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"ordinal": 0},
        {"ordinal": 5},
        {"ordinal": True},
        {"profile": "preserve_300_dpi_q90_v1"},
        {"profile": "PRIVATE_SOURCE_CANARY"},
        {"pdf_byte_sizes": ()},
        {"pdf_byte_sizes": [100]},
        {"pdf_byte_sizes": (True,)},
        {"pdf_byte_sizes": (0,)},
        {"pdf_byte_sizes": (-1,)},
        {"pdf_byte_sizes": (1.0,)},
        {"pdf_byte_sizes": (MAX_SAFE_MANIFEST_INTEGER + 1,)},
        {"pdf_byte_sizes": (1,) * (MAX_PAGE_COUNT + 1)},
        {"archive_byte_size": 0},
        {"archive_byte_size": True},
        {"archive_byte_size": MAX_SAFE_MANIFEST_INTEGER + 1},
        {"constraints_satisfied": 1},
        {"budget_skipped": 1},
        {"budget_skipped": "true"},
        {"constraints_satisfied": True},
        {"blocking_codes": ()},
        {"blocking_codes": [_SIZE_CODE]},
        {"blocking_codes": ("private/file.pdf",)},
        {"blocking_codes": (True,)},
        {"blocking_codes": (_SIZE_CODE, _SIZE_CODE)},
        {"blocking_codes": (_SIZE_CODE, "PORTAL_FILE_SIZE_EXCEEDED")},
        {"unsupported_output_positions": [1]},
        {"unsupported_output_positions": (0,)},
        {"unsupported_output_positions": (3,)},
        {"unsupported_output_positions": (True,)},
        {"unsupported_output_positions": (1, 1)},
        {"unsupported_output_positions": (2, 1)},
    ),
)
def test_attempt_rejects_ambiguous_or_unsafe_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError) as caught:
        replace(_attempt(), **changes)  # type: ignore[arg-type]
    assert "PRIVATE_SOURCE_CANARY" not in repr(caught.value)
    assert "private/file.pdf" not in repr(caught.value)
    assert caught.value.code is PdfExportErrorCode.MODEL_INVALID
    assert caught.value.__context__ is None


def test_baseline_can_satisfy_constraints_without_a_lossy_policy() -> None:
    report = replace(_report(), attempts=(_attempt(success=True),), selected_profile=None)
    assert report.terminal is TargetSizeSearchTerminal.SATISFIED
    assert report.suggestions(allow_splitting=True) == ()
    assert "meets the output constraints" in report.message
    assert "saved" not in report.message


def test_policy_selection_accepts_measured_non_monotonic_pdf_and_archive_sizes() -> None:
    attempts = (
        replace(_attempt(1), pdf_byte_sizes=(10_000, 8_000), archive_byte_size=12_000),
        replace(_attempt(2), pdf_byte_sizes=(11_000, 6_000), archive_byte_size=13_000),
        replace(_attempt(3, success=True), pdf_byte_sizes=(9_000, 4_000), archive_byte_size=9_000),
    )
    report = replace(
        _report(), attempts=attempts, selected_profile=PdfImageStageProfile.PRESERVE_240_DPI_Q85
    )
    assert report.attempts == attempts


@pytest.mark.parametrize(
    "changes",
    (
        {"semantic_fingerprint": "PRIVATE_SOURCE_CANARY"},
        {"semantic_fingerprint": "A" * 64},
        {"semantic_fingerprint": "a" * 63},
        {"semantic_fingerprint": True},
        {"policy": None},
        {"attempts": [_attempt()]},
        {"attempts": ("PRIVATE_SOURCE_CANARY",)},
        {"attempts": ()},
        {"attempts": (_attempt(2, success=True),)},
        {"attempts": (_attempt(), _attempt(3, success=True))},
        {"attempts": (_attempt(), _attempt())},
        {"attempts": (_attempt(success=True), _attempt(2, success=True))},
        {"attempts": (_attempt(success=True), _attempt(2))},
        {"attempts": (_attempt(), _attempt(2))},
        {"attempts": (_attempt(), replace(_attempt(2, success=True), pdf_byte_sizes=(1_000,)))},
        {"attempts": (_attempt(), replace(_attempt(2, success=True), archive_byte_size=2_000))},
        {"terminal": "satisfied"},
        {"terminal": TargetSizeSearchTerminal.UNATTAINABLE},
        {"selected_profile": None},
        {"selected_profile": PdfImageStageProfile.PRESERVE_200_DPI_Q80},
        {"selected_profile": "preserve_300_dpi_q90_v1"},
        {"report_version": True},
        {"report_version": 0},
        {"report_version": 2},
        {"policy": replace(_policy(), max_attempts=1)},
    ),
)
def test_report_rejects_stale_versions_reordering_and_non_first_success(
    changes: dict[str, object],
) -> None:
    with pytest.raises(PdfExportError) as caught:
        replace(_report(), **changes)  # type: ignore[arg-type]
    assert caught.value.code is PdfExportErrorCode.MODEL_INVALID
    assert "PRIVATE_SOURCE_CANARY" not in repr(caught.value)


def test_full_exhaustion_is_honestly_unattainable_and_suggestions_respect_allowed_modes() -> None:
    report = replace(
        _report(),
        attempts=tuple(_attempt(ordinal) for ordinal in range(1, 5)),
        terminal=TargetSizeSearchTerminal.UNATTAINABLE,
        selected_profile=None,
    )
    assert "supported image policies" in report.message
    assert len(report.suggestions(allow_splitting=False)) == 1
    assert len(report.suggestions(allow_splitting=True)) == 2
    assert all("Split" not in item for item in report.suggestions(allow_splitting=False))
    with pytest.raises(PdfExportError):
        replace(report, attempts=report.attempts[:3])
    with pytest.raises(PdfExportError):
        replace(report, selected_profile=PdfImageStageProfile.PRESERVE_200_DPI_Q80)


def test_work_limit_can_stop_before_first_attempt_or_at_a_shorter_configured_prefix() -> None:
    report = replace(
        _report(),
        attempts=(),
        terminal=TargetSizeSearchTerminal.WORK_LIMIT,
        selected_profile=None,
    )
    assert "work limit" in report.message
    assert len(report.suggestions(allow_splitting=False)) == 2
    short = replace(report, policy=replace(_policy(), max_attempts=1), attempts=(_attempt(),))
    assert short.attempts == (_attempt(),)
    with pytest.raises(PdfExportError):
        replace(short, attempts=(_attempt(), _attempt(2)))


def test_budget_skipped_ladder_cannot_claim_unattainable_but_later_success_is_valid() -> None:
    attempts = tuple(
        replace(_attempt(ordinal), budget_skipped=ordinal == 2) for ordinal in range(1, 5)
    )
    report = replace(
        _report(),
        attempts=attempts,
        terminal=TargetSizeSearchTerminal.WORK_LIMIT,
        selected_profile=None,
    )
    assert "resource limits" in report.message
    with pytest.raises(PdfExportError):
        replace(report, terminal=TargetSizeSearchTerminal.UNATTAINABLE)
    satisfied = replace(
        report,
        attempts=(*attempts[:3], replace(_attempt(4, success=True), budget_skipped=False)),
        terminal=TargetSizeSearchTerminal.SATISFIED,
        selected_profile=PdfImageStageProfile.PRESERVE_200_DPI_Q80,
    )
    assert "meets" in satisfied.message and satisfied.suggestions(allow_splitting=True) == ()


@pytest.mark.parametrize(
    "terminal",
    (TargetSizeSearchTerminal.OTHER_CONSTRAINTS, TargetSizeSearchTerminal.UNSUPPORTED_POLICY),
)
def test_other_constraints_and_unsupported_policy_can_stop_early(
    terminal: TargetSizeSearchTerminal,
) -> None:
    report = replace(_report(), attempts=(_attempt(),), terminal=terminal, selected_profile=None)
    assert report.suggestions(allow_splitting=False)
    assert report.message
    with pytest.raises(PdfExportError):
        replace(report, attempts=())


def test_unsupported_outputs_and_other_constraints_get_specific_actionable_suggestions() -> None:
    report = replace(
        _report(),
        attempts=(replace(_attempt(), unsupported_output_positions=(2,)),),
        terminal=TargetSizeSearchTerminal.WORK_LIMIT,
        selected_profile=None,
    )
    assert "unchanged" in report.suggestions(allow_splitting=False)[-1]
    other = replace(report, terminal=TargetSizeSearchTerminal.OTHER_CONSTRAINTS)
    assert other.suggestions(allow_splitting=True) == (
        "Review and resolve the reported output constraints, then try again.",
    )
    with pytest.raises(PdfExportError):
        other.suggestions(allow_splitting=1)  # type: ignore[arg-type]


def test_evidence_is_immutable_and_representations_exclude_input_digests_and_codes() -> None:
    report = _report()
    with pytest.raises(FrozenInstanceError):
        report.selected_profile = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.attempts[0].ordinal = 4  # type: ignore[misc]
    representation = repr(report)
    assert _SEMANTIC not in representation
    assert _SIZE_CODE not in representation
    assert "2000" in representation
    assert "max_attempts=4" in representation
