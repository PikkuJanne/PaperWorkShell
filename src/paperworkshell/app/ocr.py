"""Toolkit-free orchestration for OCR of one authenticated committed source."""

from __future__ import annotations

from types import TracebackType
from typing import Never, Protocol, Self, runtime_checkable
from uuid import UUID

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrProgress,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
    reject_ocr,
)
from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    resolve_ocr_language_set,
)
from paperworkshell.domain.source_ingest import (
    SourceEncryptionState,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceSignatureState,
)


class DeadlineProbe(Protocol):
    """Return whether the immutable OCR deadline has expired."""

    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    """Return whether OCR cancellation has been requested."""

    def __call__(self) -> bool: ...


class OcrProgressSink(Protocol):
    """Receive one validated, content-free OCR progress record."""

    def __call__(self, progress: OcrProgress, /) -> object: ...


@runtime_checkable
class OcrLanguageAvailability(Protocol):
    """Preflight one versioned OCR language set before source authority is borrowed."""

    def require_available(self, language_set_id: str) -> None: ...

    def require_selected_available(self) -> str: ...


class _BuiltInOcrLanguageAvailability:
    """Compatibility policy admitting only the immutable built-in English set."""

    __slots__ = ()

    def require_available(self, language_set_id: str) -> None:
        language_codes = resolve_ocr_language_set(language_set_id)
        if language_set_id != DEFAULT_OCR_LANGUAGE_SET_ID or language_codes != ("eng",):
            reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_preflight")

    def require_selected_available(self) -> str:
        return DEFAULT_OCR_LANGUAGE_SET_ID


_BUILT_IN_OCR_LANGUAGES = _BuiltInOcrLanguageAvailability()


def built_in_ocr_language_availability() -> OcrLanguageAvailability:
    """Return the compatibility policy for the authenticated built-in pack."""

    return _BUILT_IN_OCR_LANGUAGES


@runtime_checkable
class CommittedSourcePageLease(Protocol):
    """Manifest-authenticated, path-free authority for one immutable source page."""

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def source_media_type(self) -> DetectedMediaType: ...

    @property
    def source_byte_size(self) -> int: ...

    @property
    def source_page_count(self) -> int: ...

    @property
    def source_sha256(self) -> str: ...

    @property
    def signature_state(self) -> SourceSignatureState: ...

    @property
    def encryption_state(self) -> SourceEncryptionState: ...

    @property
    def handle_value(self) -> int: ...

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class CommittedSourcePageStore(Protocol):
    """Storage port that authenticates and retains one OCR source mapping."""

    def borrow_committed_source_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> CommittedSourcePageLease: ...


class RetainedOcrSource(Protocol):
    """Exact path-free source facts and handle consumed by the OCR broker port."""

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def source_kind(self) -> OcrSourceKind: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def page_count(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def signature_state(self) -> OcrSourceSignatureState: ...

    @property
    def encryption_state(self) -> OcrSourceEncryptionState: ...

    @property
    def handle_value(self) -> int: ...

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None: ...


@runtime_checkable
class OcrCandidate(Protocol):
    """One authenticated volatile PDF candidate returned by the OCR broker."""

    @property
    def report(self) -> OcrWorkerReport: ...

    def read(self, size: int, /) -> bytes: ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class OcrProcessor(Protocol):
    """Path-free broker port for one bounded worker OCR attempt."""

    def process(
        self,
        request: OcrProcessRequest,
        source: RetainedOcrSource,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink,
    ) -> OcrCandidate: ...


_SOURCE_KIND_BY_MEDIA_TYPE = {
    DetectedMediaType.PDF: OcrSourceKind.PDF,
    DetectedMediaType.JPEG: OcrSourceKind.JPEG,
    DetectedMediaType.PNG: OcrSourceKind.PNG,
}
_SIGNATURE_STATE_BY_SOURCE_STATE = {
    SourceSignatureState.NOT_APPLICABLE: OcrSourceSignatureState.NOT_APPLICABLE,
    SourceSignatureState.NONE_DETECTED: OcrSourceSignatureState.NONE_DETECTED,
    SourceSignatureState.MARKERS_DETECTED: OcrSourceSignatureState.MARKERS_DETECTED,
    SourceSignatureState.UNKNOWN: OcrSourceSignatureState.UNKNOWN,
}
_ENCRYPTION_STATE_BY_SOURCE_STATE = {
    SourceEncryptionState.NOT_APPLICABLE: OcrSourceEncryptionState.NOT_APPLICABLE,
    SourceEncryptionState.UNENCRYPTED: OcrSourceEncryptionState.UNENCRYPTED,
    SourceEncryptionState.ENCRYPTED: OcrSourceEncryptionState.ENCRYPTED,
    SourceEncryptionState.UNKNOWN: OcrSourceEncryptionState.UNKNOWN,
}


class _RetainedOcrSourceView:
    """Narrow OCR-domain view over the same retained storage-owned lease."""

    __slots__ = ("_lease", "_source_kind", "_signature_state", "_encryption_state")

    def __init__(self, lease: CommittedSourcePageLease) -> None:
        self._lease = lease
        self._source_kind = _map_source_kind(lease.source_media_type)
        self._signature_state = _map_signature_state(lease.signature_state)
        self._encryption_state = _map_encryption_state(lease.encryption_state)

    @property
    def case_id(self) -> UUID:
        return self._lease.case_id

    @property
    def source_id(self) -> UUID:
        return self._lease.source_id

    @property
    def source_kind(self) -> OcrSourceKind:
        return self._source_kind

    @property
    def byte_size(self) -> int:
        return self._lease.source_byte_size

    @property
    def page_count(self) -> int:
        return self._lease.source_page_count

    @property
    def sha256(self) -> str:
        return self._lease.source_sha256

    @property
    def signature_state(self) -> OcrSourceSignatureState:
        return self._signature_state

    @property
    def encryption_state(self) -> OcrSourceEncryptionState:
        return self._encryption_state

    @property
    def handle_value(self) -> int:
        return self._lease.handle_value

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        self._lease.revalidate(deadline=deadline, cancellation=cancellation)


def _ignore_progress(_progress: OcrProgress) -> None:
    return


class OcrService:
    """Authenticate, bind, and run OCR without accepting caller-supplied source facts."""

    __slots__ = ("_language_sets", "_processor", "_sources")

    def __init__(
        self,
        sources: CommittedSourcePageStore,
        processor: OcrProcessor,
        *,
        language_sets: OcrLanguageAvailability | None = None,
    ) -> None:
        if (
            not isinstance(sources, CommittedSourcePageStore)
            or not isinstance(processor, OcrProcessor)
            or (
                language_sets is not None and not isinstance(language_sets, OcrLanguageAvailability)
            )
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.service")
        self._sources = sources
        self._processor = processor
        self._language_sets = language_sets or _BUILT_IN_OCR_LANGUAGES

    def process_page(
        self,
        case_id: UUID,
        page_id: UUID,
        language_set_id: str | None = None,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink = _ignore_progress,
        profile_id: str = DEFAULT_OCR_PROFILE_ID,
    ) -> OcrCandidate:
        """Return a volatile candidate built only from an authenticated source lease."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(page_id) is not UUID
            or page_id.version != 4
            or case_id == page_id
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.service_request")

        _require_active(deadline, cancellation)
        selected_language_set_id = self._select_language_set(language_set_id)

        candidate: OcrCandidate | None = None
        try:
            lease_context = self._sources.borrow_committed_source_page(
                case_id,
                page_id,
                deadline=deadline,
                cancellation=cancellation,
            )
            with lease_context as lease:
                source = _RetainedOcrSourceView(lease)
                request = OcrProcessRequest(
                    case_id=source.case_id,
                    source_id=source.source_id,
                    source_kind=source.source_kind,
                    source_byte_size=source.byte_size,
                    source_page_count=source.page_count,
                    source_sha256=source.sha256,
                    signature_state=source.signature_state,
                    encryption_state=source.encryption_state,
                    language_set_id=selected_language_set_id,
                    profile_id=profile_id,
                )
                candidate = self._processor.process(
                    request,
                    source,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
                if (
                    not isinstance(candidate, OcrCandidate)
                    or type(candidate.report) is not OcrWorkerReport
                ):
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.service_candidate")
            return candidate
        except OcrError as error:
            _close_candidate_no_raise(candidate)
            if type(error) is not OcrError:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.processor")
            reject_ocr(error.code, error.context)
        except SourceIngestError as error:
            _close_candidate_no_raise(candidate)
            _raise_source_error(error)
        except Exception:
            _close_candidate_no_raise(candidate)
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.service")

    def _require_language_set(self, language_set_id: str) -> None:
        try:
            self._language_sets.require_available(language_set_id)
        except OcrError as error:
            if type(error) is not OcrError:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_preflight")
            reject_ocr(error.code, error.context)
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_preflight")

    def _select_language_set(self, language_set_id: str | None) -> str:
        if language_set_id is not None:
            self._require_language_set(language_set_id)
            return language_set_id
        try:
            selected = self._language_sets.require_selected_available()
        except OcrError as error:
            if type(error) is not OcrError:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_preflight")
            reject_ocr(error.code, error.context)
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_preflight")
        if type(selected) is not str:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_preflight")
        resolve_ocr_language_set(selected)
        return selected


def _map_source_kind(value: object) -> OcrSourceKind:
    if type(value) is not DetectedMediaType:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_media")
    try:
        return _SOURCE_KIND_BY_MEDIA_TYPE[value]
    except KeyError:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_media")


def _require_active(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.callback")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.callback")
    if cancelled:
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.cancelled")
    if expired:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.deadline")


def _map_signature_state(value: object) -> OcrSourceSignatureState:
    if type(value) is not SourceSignatureState:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_signature")
    try:
        return _SIGNATURE_STATE_BY_SOURCE_STATE[value]
    except KeyError:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_signature")


def _map_encryption_state(value: object) -> OcrSourceEncryptionState:
    if type(value) is not SourceEncryptionState:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_encryption")
    try:
        return _ENCRYPTION_STATE_BY_SOURCE_STATE[value]
    except KeyError:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_encryption")


def _close_candidate_no_raise(candidate: OcrCandidate | None) -> None:
    if candidate is None:
        return
    try:
        candidate.close()
    except BaseException:
        return


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.source")
    reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source")
