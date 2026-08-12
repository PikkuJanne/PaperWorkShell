"""Application-facing adapter for authenticated Windows PDF publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Never, Protocol

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationError,
    ExportDestinationErrorCode,
    ExportDestinationResult,
    WindowsExportDestination,
)


class BoundedVerifiedPdfSource(Protocol):
    """Read-only bounded candidate accepted by the storage adapter."""

    def read(self, size: int, /) -> bytes: ...


class CancellationProbe(Protocol):
    def __call__(self) -> bool: ...


class DeadlineProbe(Protocol):
    def __call__(self) -> bool: ...


@dataclass(frozen=True, slots=True, repr=False)
class WindowsVerifiedPdfDestination:
    """Map storage outcomes to the closed PDF-export application contract."""

    destination: WindowsExportDestination = field(
        default_factory=WindowsExportDestination,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.destination) is not WindowsExportDestination:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.destination_adapter")

    def __repr__(self) -> str:
        return "WindowsVerifiedPdfDestination()"

    def require_capacity(self, destination: Path, *, estimated_byte_size: int) -> None:
        """Preflight one conservative destination-space estimate before worker export."""

        try:
            self.destination.require_capacity(
                destination,
                estimated_byte_size=estimated_byte_size,
            )
        except PdfExportError:
            raise
        except ExportDestinationError as error:
            _raise_destination_error(error)
        except Exception:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.capacity")

    def commit(
        self,
        destination: Path,
        source: BoundedVerifiedPdfSource,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> None:
        """Return only after exact final-name reopen and digest verification."""

        try:
            result = self.destination.commit(
                destination,
                source,
                expected_byte_size=expected_byte_size,
                expected_sha256=expected_sha256,
                cancellation=cancellation,
                deadline=deadline,
            )
        except PdfExportError:
            raise
        except ExportDestinationError as error:
            _raise_destination_error(error)
        except BaseException:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.destination")
        if type(result) is not ExportDestinationResult:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.destination")
        if result.state is ExportCommitState.DESTINATION_EXISTS:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "export.destination")
        if result.state is ExportCommitState.UNKNOWN:
            reject_pdf_export(
                PdfExportErrorCode.DESTINATION_COMMIT_UNCERTAIN,
                "export.destination",
            )
        if (
            result.state is not ExportCommitState.COMMITTED
            or result.byte_size != expected_byte_size
            or result.sha256 != expected_sha256
        ):
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.destination")


def _raise_destination_error(error: ExportDestinationError) -> Never:
    if error.code is ExportDestinationErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.destination")
    if error.code is ExportDestinationErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.destination")
    if error.code is ExportDestinationErrorCode.DESTINATION_FULL:
        reject_pdf_export(
            PdfExportErrorCode.DESTINATION_CAPACITY_EXCEEDED,
            "export.destination_capacity",
        )
    if error.code in {
        ExportDestinationErrorCode.PATH_INVALID,
        ExportDestinationErrorCode.LOCATION_UNSUPPORTED,
        ExportDestinationErrorCode.NAME_INVALID,
        ExportDestinationErrorCode.PARENT_UNAVAILABLE,
        ExportDestinationErrorCode.DESTINATION_UNSAFE,
        ExportDestinationErrorCode.PLATFORM_UNSUPPORTED,
    }:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.destination")
    if error.code in {
        ExportDestinationErrorCode.SOURCE_INVALID,
        ExportDestinationErrorCode.SOURCE_MISMATCH,
    }:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.destination")
    reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.destination")
