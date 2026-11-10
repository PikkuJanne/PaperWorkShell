"""Closed launch-time operation scopes for isolated document workers."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT: Final = "PWS_WORKER_OPERATION_SCOPE"
QUALITY_OCR_REVIEW_ENGINE_ADMISSION_TIMEOUT_SECONDS: Final = 15.0


class WorkerOperationScope(StrEnum):
    """Authenticated parent-selected handler allowlists for one worker process."""

    ALL = "all-v1"
    DESKTOP = "desktop-v1"
    IMAGE_PAGES = "image-pages-v1"
    PDF_EXPORT = "pdf-export-v1"
    REDACTION_EXPORT = "redaction-export-private-v1"
    REDACTION_SANITIZATION = "redaction-sanitization-private-v1"
    REDACTION_OCR = "redaction-ocr-private-v1"
    REDACTION_RECONSTRUCTION = "redaction-reconstruction-private-v1"
    QUALITY_OCR_REVIEW = "quality-ocr-review-v1"
