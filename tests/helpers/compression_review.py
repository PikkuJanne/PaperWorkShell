"""Small synthetic, validated compression review facts for UI tests."""

from __future__ import annotations

from uuid import UUID

from paperworkshell.domain.compression_review import (
    CompressionImageChange,
    CompressionPageChange,
    CompressionReviewState,
)
from paperworkshell.domain.pdf_image_stages import PdfImageColorMode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.target_size_search import (
    TargetSizeAttempt,
    TargetSizeColorChoice,
    TargetSizeDecisionReport,
    TargetSizeSearchPolicy,
    TargetSizeSearchTerminal,
)


def compression_review(
    *,
    grayscale: bool = False,
    blocked: bool = False,
    floor: bool = False,
    unchanged: bool = False,
    archive: bool = False,
) -> CompressionReviewState:
    choice = TargetSizeColorChoice.ALLOW_GRAYSCALE if grayscale else TargetSizeColorChoice.PRESERVE
    policy = TargetSizeSearchPolicy.for_resource_policy(
        CONSTRAINED_RESOURCE_POLICY_V1,
        color_choice=choice,
    )
    count = 1 if unchanged else 4 if blocked or floor else 2
    attempts = tuple(
        TargetSizeAttempt(
            ordinal=index + 1,
            profile=policy.profiles[index],
            pdf_byte_sizes=(20_000 - index * 3_000,),
            archive_byte_size=21_000 - index * 3_000 if archive else None,
            constraints_satisfied=not blocked and index == count - 1,
            blocking_codes=("OUTPUT_TOO_LARGE",) if blocked or index != count - 1 else (),
        )
        for index in range(count)
    )
    profile = attempts[-1].profile
    image = CompressionImageChange(
        original_width_pixels=1200,
        original_height_pixels=1600,
        proposed_width_pixels=600 if floor else 900,
        proposed_height_pixels=800 if floor else 1200,
        original_effective_dpi_x_milli=400_000,
        original_effective_dpi_y_milli=400_000,
        proposed_effective_dpi_x_milli=200_000 if floor else 300_000,
        proposed_effective_dpi_y_milli=200_000 if floor else 300_000,
        original_color_mode=PdfImageColorMode.RGB,
        proposed_color_mode=PdfImageColorMode.GRAYSCALE if grayscale else PdfImageColorMode.RGB,
        jpeg_quality=80 if floor else 90,
    )
    return CompressionReviewState(
        review_id=UUID("11111111-1111-4111-8111-111111111111"),
        case_id=UUID("22222222-2222-4222-8222-222222222222"),
        manifest_revision=3,
        color_choice=choice,
        current_pdf_byte_sizes=attempts[0].pdf_byte_sizes,
        proposed_pdf_byte_sizes=attempts[-1].pdf_byte_sizes,
        current_archive_byte_size=attempts[0].archive_byte_size,
        proposed_archive_byte_size=attempts[-1].archive_byte_size,
        required_file_bytes=(18_000,),
        required_total_bytes=18_000,
        affected_pages=()
        if unchanged
        else (
            CompressionPageChange(
                "output-one",
                1,
                UUID("33333333-3333-4333-8333-333333333333"),
                0,
                (image,),
            ),
            CompressionPageChange(
                "output-one",
                1,
                UUID("44444444-4444-4444-8444-444444444444"),
                1,
                (image,),
            ),
        ),
        decision=TargetSizeDecisionReport(
            "a" * 64,
            policy,
            attempts,
            TargetSizeSearchTerminal.UNATTAINABLE
            if blocked
            else TargetSizeSearchTerminal.SATISFIED,
            selected_profile=None if blocked else profile,
        ),
        export_allowed=not blocked,
        required_archive_bytes=18_000 if archive else None,
    )
