from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationReview,
    SignedSourceModificationSource,
    SignedSourceReviewStatus,
    SignedSourceSignatureState,
)
from paperworkshell.ui.signed_pdf_confirmation import (
    UnsignedDerivativeConfirmationPresentation,
)

CASE_ID = UUID("26000000-0000-4000-8000-000000000001")
MARKER_SOURCE_ID = UUID("26000000-0000-4000-8000-000000000002")
UNKNOWN_SOURCE_ID = UUID("26000000-0000-4000-8000-000000000003")


def _review(*states: SignedSourceSignatureState) -> SignedSourceModificationReview:
    source_ids = (MARKER_SOURCE_ID, UNKNOWN_SOURCE_ID)
    sources = tuple(
        SignedSourceModificationSource(source_ids[index], state)
        for index, state in enumerate(states)
    )
    return SignedSourceModificationReview(
        case_id=CASE_ID,
        manifest_revision=7,
        manifest_sha256="a" * 64,
        manifest_cas_token="cas-token-0208-valid",
        sources=sources,
        pending_source_ids=tuple(source.source_id for source in sources if source.requires_review),
        status=SignedSourceReviewStatus.CONFIRMATION_REQUIRED,
    )


@pytest.mark.parametrize(
    ("state", "explanation_fragment"),
    (
        (
            SignedSourceSignatureState.MARKERS_DETECTED,
            "contains digital-signature markers",
        ),
        (
            SignedSourceSignatureState.UNKNOWN,
            "could not confirm",
        ),
    ),
)
def test_confirmation_presentation_is_reason_aware_without_validity_claims(
    state: SignedSourceSignatureState,
    explanation_fragment: str,
) -> None:
    presentation = UnsignedDerivativeConfirmationPresentation(_review(state))

    assert explanation_fragment in presentation.reason_explanation
    assert "does not validate digital signatures" in presentation.reason_explanation
    assert "original remains unchanged" in presentation.reason_explanation
    assert presentation.question == "Create an unsigned derivative?"
    assert presentation.confirm_action == "Create unsigned derivative"
    assert presentation.cancel_action == "Cancel"
    assert presentation.result_label == "Unsigned derivative"


def test_detected_marker_wording_distinguishes_markers_from_signature_validation() -> None:
    presentation = UnsignedDerivativeConfirmationPresentation(
        _review(SignedSourceSignatureState.MARKERS_DETECTED)
    )

    assert "digital-signature markers" in presentation.reason_explanation
    assert "does not validate digital signatures" in presentation.reason_explanation


def test_presentation_rejects_a_review_that_does_not_require_confirmation() -> None:
    review = SignedSourceModificationReview(
        case_id=CASE_ID,
        manifest_revision=7,
        manifest_sha256="a" * 64,
        manifest_cas_token="cas-token-0208-valid",
        sources=(),
        pending_source_ids=(),
        status=SignedSourceReviewStatus.NOT_REQUIRED,
    )

    with pytest.raises(ValueError, match="does not require confirmation"):
        UnsignedDerivativeConfirmationPresentation(review)
