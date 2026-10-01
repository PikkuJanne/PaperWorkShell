"""Pure, transient facts for reviewing actual privately prepared compression."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_image_stages import PDF_IMAGE_STAGE_FLOOR_DPI, PdfImageColorMode
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailSnapshot
from paperworkshell.domain.target_size_search import (
    TargetSizeColorChoice,
    TargetSizeDecisionReport,
    TargetSizeSearchTerminal,
)


def _invalid(context: str) -> None:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, f"compression_review.{context}")


def _positive(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_SAFE_MANIFEST_INTEGER


def _identity(value: object) -> bool:
    return type(value) is UUID and value.version == 4


@dataclass(frozen=True, slots=True)
class CompressionImageChange:
    """Measured original and proposed properties for one changed embedded image."""

    original_width_pixels: int
    original_height_pixels: int
    proposed_width_pixels: int
    proposed_height_pixels: int
    original_effective_dpi_x_milli: int
    original_effective_dpi_y_milli: int
    proposed_effective_dpi_x_milli: int
    proposed_effective_dpi_y_milli: int
    original_color_mode: PdfImageColorMode
    proposed_color_mode: PdfImageColorMode
    jpeg_quality: int

    def __post_init__(self) -> None:
        if (
            any(
                not _positive(value)
                for value in (
                    self.original_width_pixels,
                    self.original_height_pixels,
                    self.proposed_width_pixels,
                    self.proposed_height_pixels,
                    self.original_effective_dpi_x_milli,
                    self.original_effective_dpi_y_milli,
                    self.proposed_effective_dpi_x_milli,
                    self.proposed_effective_dpi_y_milli,
                )
            )
            or self.proposed_width_pixels > self.original_width_pixels
            or self.proposed_height_pixels > self.original_height_pixels
            or min(self.proposed_effective_dpi_x_milli, self.proposed_effective_dpi_y_milli)
            < PDF_IMAGE_STAGE_FLOOR_DPI * 1_000
            or type(self.jpeg_quality) is not int
            or not 80 <= self.jpeg_quality <= 100
            or any(
                type(value) is not PdfImageColorMode or value is PdfImageColorMode.UNSUPPORTED
                for value in (self.original_color_mode, self.proposed_color_mode)
            )
        ):
            _invalid("image")


@dataclass(frozen=True, slots=True, repr=False)
class CompressionPageChange:
    output_key: str = field(repr=False)
    output_position: int
    page_id: UUID = field(repr=False)
    page_index: int
    images: tuple[CompressionImageChange, ...]

    def __post_init__(self) -> None:
        if (
            type(self.output_key) is not str
            or not 1 <= len(self.output_key) <= 80
            or type(self.output_position) is not int
            or not 1 <= self.output_position <= 128
            or not _identity(self.page_id)
            or type(self.page_index) is not int
            or not 0 <= self.page_index < 1_000
            or type(self.images) is not tuple
            or not 1 <= len(self.images) <= 100_000
            or any(type(value) is not CompressionImageChange for value in self.images)
        ):
            _invalid("page")

    def __repr__(self) -> str:
        return f"CompressionPageChange(output_position={self.output_position}, page_index={self.page_index})"


@dataclass(frozen=True, slots=True, repr=False)
class CompressionReviewState:
    """Actual byte counts and selected changes; never an approval or readability claim."""

    review_id: UUID = field(repr=False)
    case_id: UUID = field(repr=False)
    manifest_revision: int
    color_choice: TargetSizeColorChoice
    current_pdf_byte_sizes: tuple[int, ...]
    proposed_pdf_byte_sizes: tuple[int, ...]
    current_archive_byte_size: int | None
    proposed_archive_byte_size: int | None
    required_file_bytes: tuple[int | None, ...]
    required_total_bytes: int | None
    affected_pages: tuple[CompressionPageChange, ...] = field(repr=False)
    decision: TargetSizeDecisionReport = field(repr=False)
    export_allowed: bool
    required_archive_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not _identity(self.review_id)
            or not _identity(self.case_id)
            or self.review_id == self.case_id
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.color_choice) is not TargetSizeColorChoice
            or type(self.decision) is not TargetSizeDecisionReport
            or not self.decision.attempts
            or type(self.export_allowed) is not bool
            or type(self.current_pdf_byte_sizes) is not tuple
            or not 1 <= len(self.current_pdf_byte_sizes) <= 128
            or type(self.proposed_pdf_byte_sizes) is not tuple
            or len(self.proposed_pdf_byte_sizes) != len(self.current_pdf_byte_sizes)
            or any(
                not _positive(value)
                for value in (*self.current_pdf_byte_sizes, *self.proposed_pdf_byte_sizes)
            )
            or type(self.required_file_bytes) is not tuple
            or len(self.required_file_bytes) != len(self.current_pdf_byte_sizes)
            or any(
                value is not None and not _positive(value)
                for value in (
                    *self.required_file_bytes,
                    self.required_total_bytes,
                    self.required_archive_bytes,
                    self.current_archive_byte_size,
                    self.proposed_archive_byte_size,
                )
            )
            or (self.current_archive_byte_size is None) != (self.proposed_archive_byte_size is None)
            or type(self.affected_pages) is not tuple
            or len(self.affected_pages) > 1_000
            or any(type(page) is not CompressionPageChange for page in self.affected_pages)
        ):
            _invalid("state")
        if (
            self.color_choice is not self.decision.policy.color_choice
            or self.current_pdf_byte_sizes != self.decision.attempts[0].pdf_byte_sizes
            or self.proposed_pdf_byte_sizes != self.decision.attempts[-1].pdf_byte_sizes
            or self.current_archive_byte_size != self.decision.attempts[0].archive_byte_size
            or self.proposed_archive_byte_size != self.decision.attempts[-1].archive_byte_size
            or self.export_allowed != (self.decision.terminal is TargetSizeSearchTerminal.SATISFIED)
            or len({(page.output_key, page.page_index) for page in self.affected_pages})
            != len(self.affected_pages)
            or sum(len(page.images) for page in self.affected_pages) > 100_000
            or any(
                page.output_position > len(self.proposed_pdf_byte_sizes)
                for page in self.affected_pages
            )
            or (
                self.color_choice is TargetSizeColorChoice.PRESERVE
                and any(
                    image.original_color_mode is not image.proposed_color_mode
                    for page in self.affected_pages
                    for image in page.images
                )
            )
        ):
            _invalid("binding")

    @property
    def current_total_bytes(self) -> int:
        return (
            self.current_archive_byte_size
            if self.current_archive_byte_size is not None
            else sum(self.current_pdf_byte_sizes)
        )

    @property
    def proposed_total_bytes(self) -> int:
        return (
            self.proposed_archive_byte_size
            if self.proposed_archive_byte_size is not None
            else sum(self.proposed_pdf_byte_sizes)
        )

    @property
    def floor_dpi(self) -> int:
        return self.decision.policy.legibility_floor.minimum_dpi

    @property
    def floor_jpeg_quality(self) -> int:
        return self.decision.policy.legibility_floor.minimum_jpeg_quality

    def __repr__(self) -> str:
        return f"CompressionReviewState(affected_page_count={len(self.affected_pages)}, export_allowed={self.export_allowed})"


@dataclass(frozen=True, slots=True, repr=False)
class CompressionPagePreview:
    review_id: UUID = field(repr=False)
    output_key: str = field(repr=False)
    page_index: int
    before: PdfThumbnailSnapshot = field(repr=False)
    proposed: PdfThumbnailSnapshot = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _identity(self.review_id)
            or type(self.output_key) is not str
            or not 1 <= len(self.output_key) <= 80
            or type(self.page_index) is not int
            or not 0 <= self.page_index < 1_000
            or type(self.before) is not PdfThumbnailSnapshot
            or type(self.proposed) is not PdfThumbnailSnapshot
            or (
                self.before.case_id,
                self.before.page_id,
                self.before.width_pixels,
                self.before.height_pixels,
            )
            != (
                self.proposed.case_id,
                self.proposed.page_id,
                self.proposed.width_pixels,
                self.proposed.height_pixels,
            )
        ):
            _invalid("preview")

    def __repr__(self) -> str:
        return f"CompressionPagePreview(page_index={self.page_index})"
