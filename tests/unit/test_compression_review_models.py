"""Compression presentation rejects ambiguous measurements and unsafe claims."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from paperworkshell.domain.compression_review import CompressionImageChange, CompressionReviewState
from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.pdf_image_stages import PdfImageColorMode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.target_size_search import (
    TargetSizeAttempt,
    TargetSizeColorChoice,
    TargetSizeDecisionReport,
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)


def _state() -> CompressionReviewState:
    policy = TargetSizeSearchPolicy.for_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
    attempt = TargetSizeAttempt(1, None, (100,), None, True, (), (), False)
    decision = TargetSizeDecisionReport(
        "a" * 64, policy, (attempt,), TargetSizeSearchTerminal.SATISFIED, None
    )
    return CompressionReviewState(
        uuid4(),
        uuid4(),
        1,
        TargetSizeColorChoice.PRESERVE,
        (100,),
        (100,),
        None,
        None,
        (100,),
        None,
        (),
        decision,
        True,
    )


def test_empty_search_cannot_be_presented_as_measured_candidate() -> None:
    state = _state()
    empty = TargetSizeDecisionReport(
        "a" * 64, state.decision.policy, (), TargetSizeSearchTerminal.WORK_LIMIT, None
    )
    with pytest.raises(PdfExportError) as captured:
        replace(state, decision=empty, export_allowed=False)
    assert captured.value.context == "compression_review.state"
    assert captured.value.__context__ is None


def test_unsafe_size_does_not_become_exportable_from_presentation_flag() -> None:
    state = _state()
    with pytest.raises(PdfExportError):
        replace(state, proposed_pdf_byte_sizes=(99,))
    with pytest.raises(PdfExportError):
        replace(state, export_allowed=False)


@pytest.mark.parametrize("dpi,quality", [(199_999, 80), (200_000, 79)])
def test_image_changes_cannot_claim_a_profile_below_fixed_floor(dpi: int, quality: int) -> None:
    with pytest.raises(PdfExportError):
        CompressionImageChange(
            600,
            480,
            200,
            160,
            600_000,
            600_000,
            dpi,
            dpi,
            PdfImageColorMode.RGB,
            PdfImageColorMode.RGB,
            quality,
        )
