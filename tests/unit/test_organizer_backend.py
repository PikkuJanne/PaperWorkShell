from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.app.organizer as organizer_module
from paperworkshell.app.organizer import (
    MAX_ORGANIZER_PREVIEW_BATCH_PAGES,
    OrganizerBackend,
    OrganizerImportRefreshStatus,
    OrganizerViewState,
    _default_organizer_preview_timeout,
)
from paperworkshell.app.pdf_thumbnails import (
    DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    ThumbnailProgressSink,
)
from paperworkshell.app.pdf_thumbnails import (
    CancellationProbe as ThumbnailCancellationProbe,
)
from paperworkshell.app.source_ingest import (
    DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe as SourceCancellationProbe,
)
from paperworkshell.app.source_ingest import (
    ProgressSink as SourceProgressSink,
)
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseSummary
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
    copy_manifest_document,
)
from paperworkshell.domain.page_operations import PageOperationError, PageOperationErrorCode
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailRequest, PdfThumbnailResult
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.domain.source_ingest import SourceImportResult

CASE_ID = UUID("10000000-0000-4000-8000-000000000111")
PDF_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000111")
IMAGE_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000112")
IMPORTED_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000113")
PAGE_IDS = (
    UUID("30000000-0000-4000-8000-000000000111"),
    UUID("30000000-0000-4000-8000-000000000112"),
    UUID("30000000-0000-4000-8000-000000000113"),
)
IMAGE_PAGE_ID = UUID("30000000-0000-4000-8000-000000000114")
IMPORTED_PAGE_ID = UUID("30000000-0000-4000-8000-000000000115")
IMPORT_OPERATION_ID = UUID("50000000-0000-4000-8000-000000000111")
STAMP = "2026-08-05T10:00:00Z"


def _document(*, page_count: int = 3, include_image: bool = False) -> dict[str, object]:
    additional_pages = tuple(
        UUID(f"30000000-0000-4000-8000-{number:012d}")
        for number in range(1_000, 1_000 + max(0, page_count - len(PAGE_IDS)))
    )
    selected_pages = (*PAGE_IDS[:page_count], *additional_pages)
    sources: list[dict[str, object]] = []
    if selected_pages:
        sources.append(
            {
                "source_id": str(PDF_SOURCE_ID),
                "display_name": "Synthetic packet.pdf",
                "storage_name": f"{PDF_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.PDF.value,
                "byte_size": 1024,
                "page_count": len(selected_pages),
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
    pages = [
        {
            "page_id": str(page_id),
            "source_id": str(PDF_SOURCE_ID),
            "source_page_index": index,
        }
        for index, page_id in enumerate(selected_pages)
    ]
    sequence = [str(page_id) for page_id in selected_pages]
    if include_image:
        sources.append(
            {
                "source_id": str(IMAGE_SOURCE_ID),
                "display_name": "Synthetic scan.jpg",
                "storage_name": f"{IMAGE_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.JPEG.value,
                "byte_size": 512,
                "page_count": 1,
                "sha256": "b" * 64,
                "signature_state": "not_applicable",
                "encryption_state": "not_applicable",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
        pages.append(
            {
                "page_id": str(IMAGE_PAGE_ID),
                "source_id": str(IMAGE_SOURCE_ID),
                "source_page_index": 0,
            }
        )
        sequence.append(str(IMAGE_PAGE_ID))
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": 0,
        "created_at": STAMP,
        "updated_at": STAMP,
        "template_ref": None,
        "sources": sources,
        "pages": pages,
        "page_sequence_base": list(sequence),
        "page_sequence": list(sequence),
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: Mapping[str, object]) -> ManifestSnapshot:
    revision = cast(int, document["revision"])
    return ManifestSnapshot(
        CASE_ID,
        1,
        revision,
        document,
        f"{revision + 1:064x}",
        f"organizer_cas_{revision:016d}",
    )


class _Repository:
    def __init__(self, document: Mapping[str, object]) -> None:
        self.current = _snapshot(document)
        self.load_status = ManifestLoadStatus.CURRENT
        self.load_calls = 0
        self.load_failures: list[BaseException] = []
        self.fail_saves = 0
        self.save_calls = 0

    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        self.load_calls += 1
        if self.load_failures:
            raise self.load_failures.pop(0)
        return ManifestLoadResult(self.load_status, self.current)

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        assert case_id == CASE_ID
        assert expected_revision == self.current.revision
        assert expected_sha256 == self.current.sha256
        assert expected_cas_token == self.current.cas_token
        self.save_calls += 1
        if self.fail_saves:
            self.fail_saves -= 1
            raise ManifestRepositoryError(
                ManifestRepositoryErrorCode.SAVE_FAILED,
                "test.organizer_save",
            )
        published = copy_manifest_document(candidate)
        self.current = _snapshot(published)
        return ManifestSaveResult(ManifestSaveStatus.SAVED, self.current)

    def import_pdf(self, display_name: str) -> SourceImportResult:
        document = self.current.document
        sources = cast(list[dict[str, object]], document["sources"])
        pages = cast(list[dict[str, object]], document["pages"])
        base = cast(list[str], document["page_sequence_base"])
        sequence = cast(list[str], document["page_sequence"])
        operations = cast(list[dict[str, object]], document["page_operations"])
        sources.append(
            {
                "source_id": str(IMPORTED_SOURCE_ID),
                "display_name": display_name,
                "storage_name": f"{IMPORTED_SOURCE_ID}.bin",
                "detected_media_type": DetectedMediaType.PDF.value,
                "byte_size": 2048,
                "page_count": 1,
                "sha256": "c" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        )
        pages.append(
            {
                "page_id": str(IMPORTED_PAGE_ID),
                "source_id": str(IMPORTED_SOURCE_ID),
                "source_page_index": 0,
            }
        )
        if operations:
            before = list(sequence)
            sequence.append(str(IMPORTED_PAGE_ID))
            operations.append(
                {
                    "operation_id": str(IMPORT_OPERATION_ID),
                    "operation_version": 1,
                    "sequence": len(operations) + 1,
                    "payload": {
                        "kind": "change_sequence",
                        "reason": "import",
                        "before_page_ids": before,
                        "after_page_ids": list(sequence),
                    },
                }
            )
        else:
            base.append(str(IMPORTED_PAGE_ID))
            sequence.append(str(IMPORTED_PAGE_ID))
        revision = self.current.revision + 1
        document["revision"] = revision
        document["updated_at"] = f"2026-08-05T10:{revision:02d}:00Z"
        self.current = _snapshot(document)
        return SourceImportResult(
            case_id=CASE_ID,
            source_id=IMPORTED_SOURCE_ID,
            page_ids=(IMPORTED_PAGE_ID,),
            media_type=DetectedMediaType.PDF,
            byte_size=2048,
            sha256="c" * 64,
            manifest_revision=revision,
        )


class _Lifecycle:
    def __init__(self, repository: _Repository) -> None:
        self._repository = repository
        self._active: CaseSummary | None = None
        self.close_calls = 0

    @property
    def active_case(self) -> CaseSummary | None:
        return self._active

    def create_case(self) -> CaseSummary:
        assert self._active is None
        self._active = self._summary()
        return self._active

    def list_cases(self) -> CaseIndex:
        return CaseIndex((self._summary(),))

    def open_case(self, case_id: UUID) -> CaseSummary:
        assert case_id == CASE_ID
        assert self._active is None
        self._active = self._summary()
        return self._active

    def close_case(self, case_id: UUID | None = None) -> CaseSummary | None:
        if case_id is not None:
            assert case_id == CASE_ID
        prior = self._active
        self._active = None
        self.close_calls += 1
        return prior

    def _summary(self) -> CaseSummary:
        timestamp = datetime(2026, 8, 5, 10, tzinfo=UTC)
        return CaseSummary(
            CASE_ID,
            1,
            self._repository.current.revision,
            timestamp,
            timestamp,
        )


class _SourceIngest:
    def __init__(self, repository: _Repository) -> None:
        self._repository = repository
        self.calls = 0
        self.revision_seen: int | None = None
        self.timeout_seen: float | None = None
        self.cancellation_seen: SourceCancellationProbe | None = None
        self.progress_seen: SourceProgressSink | None = None

    def import_source(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> SourceImportResult:
        del source_path
        assert case_id == CASE_ID
        self.calls += 1
        self.revision_seen = self._repository.current.revision
        self.timeout_seen = timeout_seconds
        self.cancellation_seen = cancellation
        self.progress_seen = progress
        return self._repository.import_pdf(display_name)


class _Thumbnails:
    def __init__(self) -> None:
        self.requests: tuple[PdfThumbnailRequest, ...] = ()
        self.timeout_seen: float | None = None
        self.cancellation_seen: ThumbnailCancellationProbe | None = None
        self.progress_seen: ThumbnailProgressSink | None = None
        self.results: tuple[PdfThumbnailResult, ...] = ()

    def get_thumbnails(
        self,
        requests: tuple[PdfThumbnailRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
        cancellation: ThumbnailCancellationProbe | None = None,
        progress: ThumbnailProgressSink | None = None,
    ) -> tuple[PdfThumbnailResult, ...]:
        self.requests = requests
        self.timeout_seen = timeout_seconds
        self.cancellation_seen = cancellation
        self.progress_seen = progress
        return self.results


def _backend(
    document: Mapping[str, object],
    *,
    close_resources: Callable[[], None] = lambda: None,
) -> tuple[OrganizerBackend, _Repository, _Lifecycle, _SourceIngest, _Thumbnails]:
    repository = _Repository(document)
    lifecycle = _Lifecycle(repository)
    source_ingest = _SourceIngest(repository)
    thumbnails = _Thumbnails()
    return (
        OrganizerBackend(
            lifecycle,
            repository,
            source_ingest,
            thumbnails,
            close_resources=close_resources,
        ),
        repository,
        lifecycle,
        source_ingest,
        thumbnails,
    )


def test_list_create_open_and_close_expose_only_path_free_state() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=0))

    assert backend.state == OrganizerViewState.empty()
    assert tuple(case.case_id for case in backend.list_cases().cases) == (CASE_ID,)
    created = backend.create_case()

    assert created.case_id == CASE_ID
    assert created.revision == 0
    assert created.pages == ()
    assert backend.close_case() == OrganizerViewState.empty()

    opened = backend.open_case(CASE_ID)

    assert opened.case_id == CASE_ID
    assert opened.revision == 0
    assert backend.close_case() == OrganizerViewState.empty()


def test_startup_recovery_result_is_preserved_for_the_desktop_bridge() -> None:
    recovery = StartupRecoveryResult()
    repository = _Repository(_document(page_count=0))
    backend = OrganizerBackend(
        _Lifecycle(repository),
        repository,
        _SourceIngest(repository),
        _Thumbnails(),
        startup_recovery=recovery,
    )

    assert backend.startup_recovery_result is recovery


def test_recovered_manifest_sets_path_free_review_status() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    repository.load_status = ManifestLoadStatus.RECOVERED

    state = backend.open_case(CASE_ID)

    assert state.recovery_review_required
    assert backend.load_status is ManifestLoadStatus.RECOVERED
    assert not OrganizerViewState.empty().recovery_review_required


def test_page_actions_coalesce_and_preserve_history_after_successful_save() -> None:
    backend, repository, _, _, _ = _backend(_document())
    backend.open_case(CASE_ID)

    backend.rotate_clockwise(PAGE_IDS[0])
    backend.move_page(PAGE_IDS[2], 1)
    changed = backend.remove_page(PAGE_IDS[1])

    assert changed.autosave_pending
    assert changed.can_undo
    assert tuple(page.page_id for page in changed.pages) == (PAGE_IDS[2], PAGE_IDS[0])
    assert tuple(page.rotation_degrees for page in changed.pages) == (0, 90)
    saved = backend.flush_autosave()
    assert repository.save_calls == 1
    assert saved.revision == 1
    assert not saved.autosave_pending
    assert saved.can_undo

    undone = backend.undo()
    assert tuple(page.page_id for page in undone.pages) == (
        PAGE_IDS[2],
        PAGE_IDS[0],
        PAGE_IDS[1],
    )
    assert undone.autosave_pending
    assert undone.can_redo
    retried = backend.retry_autosave()
    assert retried.revision == 2
    assert not retried.autosave_pending


def test_failed_save_keeps_the_latest_edit_pending_for_retry() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError) as raised:
        backend.flush_autosave()

    assert raised.value.code is ManifestRepositoryErrorCode.SAVE_FAILED
    assert backend.state.autosave_pending
    assert backend.state.pages[0].rotation_degrees == 90
    saved = backend.retry_autosave()
    assert saved.revision == 1
    assert not saved.autosave_pending


def test_import_flushes_pending_edits_then_reloads_a_non_undoable_baseline() -> None:
    backend, _, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])

    def cancellation() -> bool:
        return False

    def progress(_event: object, /) -> None:
        return None

    outcome = backend.import_source(
        Path("synthetic.pdf"),
        "Added packet.pdf",
        timeout_seconds=12.5,
        cancellation=cancellation,
        progress=progress,
    )

    assert source_ingest.revision_seen == 1
    assert source_ingest.timeout_seen == 12.5
    assert source_ingest.cancellation_seen is cancellation
    assert source_ingest.progress_seen is progress
    assert outcome.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert not outcome.refresh_required
    assert outcome.committed_import.manifest_revision == 2
    assert outcome.state == backend.state
    assert tuple(page.page_id for page in backend.state.pages) == (
        PAGE_IDS[0],
        IMPORTED_PAGE_ID,
    )
    assert tuple(page.rotation_degrees for page in backend.state.pages) == (90, 0)
    assert not backend.state.can_undo
    assert not backend.state.can_redo
    assert not backend.state.autosave_pending


def test_committed_import_reload_failure_is_reconciled_without_second_ingest() -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    repository.load_failures.append(
        ManifestRepositoryError(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "test.private_reload_failure",
        )
    )

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert outcome.refresh_required
    assert outcome.safe_message == (
        "Import committed. Close and reopen the case before continuing so the organizer can "
        "refresh."
    )
    assert source_ingest.calls == 1
    assert repository.current.revision == 1
    assert backend.state.revision == 0
    assert tuple(page.page_id for page in backend.state.pages) == (PAGE_IDS[0],)

    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert reconciled.committed_import is outcome.committed_import
    assert source_ingest.calls == 1
    assert backend.state.revision == 1
    assert tuple(page.page_id for page in backend.state.pages) == (
        PAGE_IDS[0],
        IMPORTED_PAGE_ID,
    )


def test_committed_import_session_replacement_failure_is_a_closed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    original_session_from_load = organizer_module._session_from_load
    attempts = 0

    def fail_once(
        selected_repository: object,
        loaded: ManifestLoadResult,
    ) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private replacement detail")
        return original_session_from_load(cast(ManifestRepository, selected_repository), loaded)

    monkeypatch.setattr(organizer_module, "_session_from_load", fail_once)

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert "private replacement detail" not in outcome.safe_message
    assert repository.current.revision == 1
    assert backend.state.revision == 0

    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert source_ingest.calls == 1
    assert attempts == 2
    assert backend.state.revision == 1


def test_reconciliation_requires_the_committed_source_and_page_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, repository, _, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    original_load = repository.load

    def load_with_wrong_page_binding(case_id: UUID) -> ManifestLoadResult:
        loaded = original_load(case_id)
        document = loaded.snapshot.document
        pages = cast(list[dict[str, object]], document["pages"])
        pages[-1]["source_id"] = str(PDF_SOURCE_ID)
        return ManifestLoadResult(loaded.status, _snapshot(document))

    monkeypatch.setattr(repository, "load", load_with_wrong_page_binding)

    outcome = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert outcome.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert backend.state.revision == 0
    assert source_ingest.calls == 1

    monkeypatch.setattr(repository, "load", original_load)
    reconciled = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert reconciled.refresh_status is OrganizerImportRefreshStatus.REFRESHED
    assert backend.state.revision == 1
    assert source_ingest.calls == 1


def test_repeated_refresh_failure_restarts_from_the_single_committed_import() -> None:
    backend, repository, lifecycle, source_ingest, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    repository.load_failures.extend(
        ManifestRepositoryError(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "test.private_reload_failure",
        )
        for _ in range(2)
    )

    first = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")
    repeated = backend.import_source(Path("synthetic.pdf"), "Added packet.pdf")

    assert first.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert repeated.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED
    assert repeated.committed_import is first.committed_import
    assert source_ingest.calls == 1
    assert repository.current.revision == 1

    assert backend.close_case() == OrganizerViewState.empty()
    restarted = OrganizerBackend(lifecycle, repository, source_ingest, _Thumbnails())
    state = restarted.open_case(CASE_ID)

    assert state.revision == 1
    assert tuple(page.page_id for page in state.pages) == (PAGE_IDS[0], IMPORTED_PAGE_ID)
    assert source_ingest.calls == 1


def test_thumbnail_requests_cover_only_pdf_pages_and_forward_controls() -> None:
    backend, _, _, _, thumbnails = _backend(_document(page_count=1, include_image=True))
    backend.open_case(CASE_ID)
    state = backend.rotate_clockwise(PAGE_IDS[0])
    assert tuple(page.preview_supported for page in state.pages) == (True, False)

    def cancellation() -> bool:
        return False

    def progress(_event: object, /) -> None:
        return None

    assert (
        backend.get_thumbnails(
            maximum_width_pixels=320,
            maximum_height_pixels=240,
            page_ids=(PAGE_IDS[0], IMAGE_PAGE_ID),
            timeout_seconds=4.5,
            cancellation=cancellation,
            progress=progress,
        )
        == ()
    )

    assert len(thumbnails.requests) == 1
    request = thumbnails.requests[0]
    assert request.page_id == PAGE_IDS[0]
    assert request.maximum_width_pixels == 320
    assert request.maximum_height_pixels == 240
    assert request.additional_rotation is PdfRenderRotation.DEGREES_90
    assert thumbnails.timeout_seen == 4.5
    assert thumbnails.cancellation_seen is cancellation
    assert thumbnails.progress_seen is progress


def test_thumbnail_subset_preserves_organizer_order_and_scales_for_requested_pdfs() -> None:
    backend, _, _, _, thumbnails = _backend(_document(page_count=3))
    backend.open_case(CASE_ID)

    assert backend.get_thumbnails(320, 240, page_ids=(PAGE_IDS[0], PAGE_IDS[2])) == ()
    assert tuple(request.page_id for request in thumbnails.requests) == (
        PAGE_IDS[0],
        PAGE_IDS[2],
    )
    assert thumbnails.timeout_seen == DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS * 2


def test_thumbnail_subset_rejects_duplicates_unknown_order_and_over_cap() -> None:
    backend, _, _, _, _ = _backend(_document(page_count=9))
    state = backend.open_case(CASE_ID)

    cases = (
        ((state.pages[0].page_id, state.pages[0].page_id), PageOperationErrorCode.PAGE_DUPLICATE),
        (
            (state.pages[1].page_id, state.pages[0].page_id),
            PageOperationErrorCode.SEQUENCE_MISMATCH,
        ),
        ((UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),), PageOperationErrorCode.PAGE_UNKNOWN),
        (
            tuple(page.page_id for page in state.pages[: MAX_ORGANIZER_PREVIEW_BATCH_PAGES + 1]),
            PageOperationErrorCode.MODEL_INVALID,
        ),
    )
    for page_ids, expected_code in cases:
        with pytest.raises(PageOperationError) as raised:
            backend.get_thumbnails(320, 240, page_ids=page_ids)
        assert raised.value.code is expected_code


def test_default_thumbnail_deadline_helper_remains_service_capped() -> None:
    assert _default_organizer_preview_timeout(31) == MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS


def test_restart_reconstructs_saved_projection_without_session_undo() -> None:
    backend, repository, _, _, _ = _backend(_document(page_count=1))
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    backend.flush_autosave()
    backend.close_case()
    lifecycle = _Lifecycle(repository)
    restarted = OrganizerBackend(lifecycle, repository, _SourceIngest(repository), _Thumbnails())

    state = restarted.open_case(CASE_ID)

    assert state.pages[0].rotation_degrees == 90
    assert not state.can_undo
    assert not state.can_redo
    assert not state.autosave_pending


def test_shutdown_leaves_resources_open_until_pending_save_can_be_retried() -> None:
    close_events: list[str] = []

    def close_resources() -> None:
        close_events.append("closed")

    backend, repository, lifecycle, _, _ = _backend(
        _document(page_count=1),
        close_resources=close_resources,
    )
    backend.open_case(CASE_ID)
    backend.rotate_clockwise(PAGE_IDS[0])
    repository.fail_saves = 1

    with pytest.raises(ManifestRepositoryError):
        backend.shutdown()

    assert close_events == []
    assert backend.state.autosave_pending
    backend.shutdown()
    assert close_events == ["closed"]
    assert lifecycle.active_case is None
    assert backend.state == OrganizerViewState.empty()
    backend.shutdown()
    assert close_events == ["closed"]


def test_shutdown_retries_resource_cleanup_after_the_case_session_is_closed() -> None:
    close_attempts = 0

    def close_resources() -> None:
        nonlocal close_attempts
        close_attempts += 1
        if close_attempts == 1:
            raise RuntimeError("PRIVATE_RESOURCE_CLEANUP_CANARY")

    backend, _, lifecycle, _, _ = _backend(
        _document(page_count=1),
        close_resources=close_resources,
    )
    backend.open_case(CASE_ID)

    with pytest.raises(RuntimeError, match="PRIVATE_RESOURCE_CLEANUP_CANARY"):
        backend.shutdown()

    assert close_attempts == 1
    assert lifecycle.active_case is None
    assert backend.state == OrganizerViewState.empty()

    backend.shutdown()

    assert close_attempts == 2
    assert backend.state == OrganizerViewState.empty()
