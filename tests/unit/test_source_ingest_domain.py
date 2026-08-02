from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import (
    MAX_SOURCE_PAGES,
    CompletedSourceInspection,
    DuplicateContentSignal,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityResult,
    SourceIntegrityStatus,
    SourceProgressPhase,
    SourceSignatureState,
    require_source_display_name,
)


def _uuid(number: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{number:012d}")


def _prepared() -> PreparedSourceSummary:
    return PreparedSourceSummary(
        pending_id=_uuid(1),
        case_id=_uuid(2),
        source_id=_uuid(3),
        media_type=DetectedMediaType.PDF,
        byte_size=17,
        sha256="a" * 64,
    )


def _inspection() -> CompletedSourceInspection:
    return CompletedSourceInspection(
        pending_id=_uuid(1),
        media_type=DetectedMediaType.PDF,
        page_count=2,
        signature_state=SourceSignatureState.UNKNOWN,
        encryption_state=SourceEncryptionState.UNENCRYPTED,
    )


def test_prepared_summary_is_immutable_path_free_and_hides_digest_from_repr() -> None:
    summary = _prepared()

    assert summary.byte_size == 17
    assert "a" * 64 not in repr(summary)
    assert not hasattr(summary, "path")
    assert not hasattr(summary, "display_name")
    with pytest.raises(AttributeError):
        summary.byte_size = 18  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("pending_id", UUID("10000000-0000-3000-8000-000000000001")),
        ("case_id", "10000000-0000-4000-8000-000000000002"),
        ("source_id", None),
        ("media_type", "application/pdf"),
        ("byte_size", True),
        ("byte_size", 0),
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
    ),
)
def test_prepared_summary_rejects_invalid_fields(field: str, value: object) -> None:
    values: dict[str, object] = {
        "pending_id": _uuid(1),
        "case_id": _uuid(2),
        "source_id": _uuid(3),
        "media_type": DetectedMediaType.PDF,
        "byte_size": 17,
        "sha256": "a" * 64,
    }
    values[field] = value

    with pytest.raises(SourceIngestError):
        PreparedSourceSummary(**values)  # type: ignore[arg-type]


def test_prepared_summary_rejects_identifier_collisions() -> None:
    with pytest.raises(SourceIngestError) as raised:
        PreparedSourceSummary(
            pending_id=_uuid(1),
            case_id=_uuid(1),
            source_id=_uuid(3),
            media_type=DetectedMediaType.PNG,
            byte_size=1,
            sha256="b" * 64,
        )

    assert raised.value.code is SourceIngestErrorCode.MODEL_INVALID


def test_display_name_validation_is_separate_and_matches_manifest_safety() -> None:
    assert require_source_display_name("SYNTHETIC $(inert) résumé.pdf") == (
        "SYNTHETIC $(inert) résumé.pdf"
    )

    invalid = (
        "",
        ".",
        "..",
        "folder/source.pdf",
        "folder\\source.pdf",
        "source\u0000.pdf",
        "source\u202e.pdf",
        "re\u0301sume\u0301.pdf",
        "x" * 256,
    )
    for value in invalid:
        with pytest.raises(SourceIngestError) as raised:
            require_source_display_name(value)
        assert raised.value.code is SourceIngestErrorCode.DISPLAY_NAME_INVALID
        if len(value) > 3:
            assert value not in repr(raised.value)
            assert value not in str(raised.value)
            assert value not in repr(raised.value.args)


@pytest.mark.parametrize(
    ("signature", "encryption"),
    (
        (SourceSignatureState.NONE_DETECTED, SourceEncryptionState.UNENCRYPTED),
        (SourceSignatureState.MARKERS_DETECTED, SourceEncryptionState.ENCRYPTED),
        (SourceSignatureState.UNKNOWN, SourceEncryptionState.UNKNOWN),
    ),
)
def test_pdf_inspection_accepts_only_authoritative_states(
    signature: SourceSignatureState,
    encryption: SourceEncryptionState,
) -> None:
    inspection = CompletedSourceInspection(
        pending_id=_uuid(1),
        media_type=DetectedMediaType.PDF,
        page_count=MAX_SOURCE_PAGES,
        signature_state=signature,
        encryption_state=encryption,
    )

    assert inspection.inspection_version == 1


@pytest.mark.parametrize("media_type", (DetectedMediaType.JPEG, DetectedMediaType.PNG))
def test_image_inspection_requires_one_page_and_not_applicable_states(
    media_type: DetectedMediaType,
) -> None:
    inspection = CompletedSourceInspection(
        pending_id=_uuid(1),
        media_type=media_type,
        page_count=1,
        signature_state=SourceSignatureState.NOT_APPLICABLE,
        encryption_state=SourceEncryptionState.NOT_APPLICABLE,
    )

    assert inspection.page_count == 1


@pytest.mark.parametrize(
    ("media_type", "page_count", "signature", "encryption", "version"),
    (
        (
            DetectedMediaType.PDF,
            1,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.UNENCRYPTED,
            1,
        ),
        (
            DetectedMediaType.PDF,
            1,
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.NOT_APPLICABLE,
            1,
        ),
        (
            DetectedMediaType.PNG,
            2,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.NOT_APPLICABLE,
            1,
        ),
        (
            DetectedMediaType.JPEG,
            1,
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.NOT_APPLICABLE,
            1,
        ),
        (
            DetectedMediaType.PDF,
            True,
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.UNKNOWN,
            1,
        ),
        (
            DetectedMediaType.PDF,
            MAX_SOURCE_PAGES + 1,
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.UNKNOWN,
            1,
        ),
        (
            DetectedMediaType.PDF,
            1,
            SourceSignatureState.UNKNOWN,
            SourceEncryptionState.UNKNOWN,
            2,
        ),
    ),
)
def test_inspection_rejects_incomplete_or_inconsistent_metadata(
    media_type: DetectedMediaType,
    page_count: int,
    signature: SourceSignatureState,
    encryption: SourceEncryptionState,
    version: int,
) -> None:
    with pytest.raises(SourceIngestError) as raised:
        CompletedSourceInspection(
            pending_id=_uuid(1),
            media_type=media_type,
            page_count=page_count,
            signature_state=signature,
            encryption_state=encryption,
            inspection_version=version,
        )

    assert raised.value.code is SourceIngestErrorCode.INSPECTION_INVALID


def test_duplicate_signal_sorts_unique_internal_ids_without_display_data() -> None:
    signal = DuplicateContentSignal((_uuid(7), _uuid(4)))

    assert signal.detected
    assert signal.matching_source_ids == (_uuid(4), _uuid(7))
    assert not DuplicateContentSignal().detected
    with pytest.raises(SourceIngestError):
        DuplicateContentSignal((_uuid(4), _uuid(4)))
    with pytest.raises(SourceIngestError):
        DuplicateContentSignal([_uuid(4)])  # type: ignore[arg-type]


def test_committed_result_binds_source_pages_revision_and_duplicate_signal() -> None:
    result = SourceImportResult(
        case_id=_uuid(2),
        source_id=_uuid(3),
        page_ids=(_uuid(4), _uuid(5)),
        media_type=DetectedMediaType.PDF,
        byte_size=17,
        sha256="a" * 64,
        manifest_revision=2,
        duplicate=DuplicateContentSignal((_uuid(6),)),
    )

    assert result.manifest_revision == 2
    assert result.duplicate.detected
    assert "a" * 64 not in repr(result)
    assert not hasattr(result, "path")


@pytest.mark.parametrize(
    "mutation",
    (
        {"page_ids": ()},
        {"page_ids": (_uuid(4), _uuid(4))},
        {"page_ids": (_uuid(2),)},
        {"manifest_revision": True},
        {"manifest_revision": 0},
        {"duplicate": DuplicateContentSignal((_uuid(3),))},
    ),
)
def test_committed_result_rejects_inconsistent_identity_or_revision(
    mutation: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "case_id": _uuid(2),
        "source_id": _uuid(3),
        "page_ids": (_uuid(4),),
        "media_type": DetectedMediaType.PNG,
        "byte_size": 17,
        "sha256": "a" * 64,
        "manifest_revision": 1,
        "duplicate": DuplicateContentSignal(),
    }
    values.update(mutation)

    with pytest.raises(SourceIngestError):
        SourceImportResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "fragment"),
    (
        (SourceIntegrityStatus.VERIFIED, "size and SHA-256 match"),
        (SourceIntegrityStatus.MISSING, "copy is missing"),
        (SourceIntegrityStatus.MISMATCH, "no longer matches"),
    ),
)
def test_integrity_results_make_only_concrete_path_free_claims(
    status: SourceIntegrityStatus,
    fragment: str,
) -> None:
    result = SourceIntegrityResult(_uuid(2), _uuid(3), status)

    assert fragment in result.safe_message
    assert not hasattr(result, "path")
    assert not hasattr(result, "sha256")


def test_progress_requires_uuid_phase_and_bounded_monotonic_units() -> None:
    progress = SourceImportProgress(
        operation_id=_uuid(1),
        phase=SourceProgressPhase.COPYING,
        completed_units=5,
        total_units=10,
    )

    assert progress.completed_units == 5
    for completed, total in ((-1, 10), (11, 10), (0, 0), (True, 10)):
        with pytest.raises(SourceIngestError):
            SourceImportProgress(_uuid(1), SourceProgressPhase.COPYING, completed, total)
    with pytest.raises(SourceIngestError):
        SourceImportProgress(_uuid(1), "copying", 0, 1)  # type: ignore[arg-type]


def test_error_constructor_closes_code_context_message_and_exception_args() -> None:
    canary = "private-path-value"
    error = SourceIngestError(
        code=canary,  # type: ignore[arg-type]
        context=canary,
    )

    assert error.code is SourceIngestErrorCode.INTERNAL_ERROR
    assert error.context == "source.operation"
    assert canary not in repr(error)
    assert canary not in str(error)
    assert canary not in repr(error.args)
