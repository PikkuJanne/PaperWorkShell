"""Worker-side checks for the conservative portal PDF profile.

The profile preserves source font and visible page resources. It only accepts
the static output subset below; it does not silently flatten source forms,
remove visible annotations, substitute fonts or claim portal acceptance.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Never

import pikepdf

from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_inspection import (
    PdfFeatureState,
    PdfInspectionError,
    PdfInspectionErrorCode,
)
from paperworkshell.pdf.pikepdf_adapter import PikePdfAdapter

PORTAL_MINIMUM_OUTPUT_VERSION: Final = "1.7"
_SUPPORTED_SOURCE_VERSIONS: Final = frozenset(f"1.{minor}" for minor in range(8))
_SUPPORTED_OUTPUT_VERSIONS: Final = frozenset({"1.5", "1.6", "1.7"})
_PROHIBITED_KEYS: Final = frozenset(
    {
        "/PieceInfo",
        "/LastModified",
        "/OC",
        "/OCProperties",
        "/Extensions",
        "/RichMediaContent",
        "/RichMediaSettings",
        "/3DD",
        "/PresSteps",
    }
)


def require_portal_source_version(document: pikepdf.Pdf) -> None:
    """Reject unsupported declared versions before source page copying starts."""

    if document.pdf_version not in _SUPPORTED_SOURCE_VERSIONS:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "portal.source_version")
    declared = document.Root.get("/Version")
    if declared is not None and (
        not isinstance(declared, pikepdf.Name)
        or str(declared)[1:] not in _SUPPORTED_SOURCE_VERSIONS
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "portal.source_version")
    if "/Extensions" in document.Root:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "portal.source_extensions")


def require_portal_output(
    document: pikepdf.Pdf,
    *,
    maximum_objects: int,
    checkpoint: Callable[[], None],
) -> None:
    """Require bounded proof of the static, unencrypted PDF 1.5–1.7 subset."""

    checkpoint()
    if document.is_encrypted:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED, "portal.output_encrypted"
        )
    if document.pdf_version not in _SUPPORTED_OUTPUT_VERSIONS:
        _reject("portal.output_version")
    declared = document.Root.get("/Version")
    if declared is not None and (
        not isinstance(declared, pikepdf.Name)
        or str(declared)[1:] not in _SUPPORTED_OUTPUT_VERSIONS
    ):
        _reject("portal.output_version")
    try:
        features = PikePdfAdapter().inspect_open_features(
            document,
            maximum_objects=maximum_objects,
            checkpoint=checkpoint,
            dictionary_observer=_require_static_dictionary,
        )
    except PdfInspectionError as error:
        if error.code is PdfInspectionErrorCode.LIMIT_EXCEEDED:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "portal.graph_limit")
        _reject("portal.output_inspection")
    if any(state is not PdfFeatureState.NOT_DETECTED for state in features.values):
        _reject("portal.output_features")


def _require_static_dictionary(
    value: pikepdf.Dictionary, _is_trailer: bool, is_stream: bool
) -> None:
    if any(str(key) in _PROHIBITED_KEYS for key in value.keys()):
        _reject("portal.output_features")
    if is_stream and any(key in value for key in ("/F", "/FFilter", "/FDecodeParms", "/Ref")):
        _reject("portal.output_external_stream")
    if str(value.get("/Type", "")) in {"/Filespec", "/OCG", "/OCMD"}:
        _reject("portal.output_features")


def _reject(context: str) -> Never:
    reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, context)
