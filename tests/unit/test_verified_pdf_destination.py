from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationError,
    ExportDestinationErrorCode,
    ExportDestinationResult,
    WindowsExportDestination,
)

DESTINATION = Path("C:/synthetic/verified-output.pdf")
PAYLOAD = b"%PDF" + b"-1.7\n% verified destination unit\n%%EOF\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class _Source:
    def __init__(self) -> None:
        self.offset = 0

    def read(self, size: int, /) -> bytes:
        end = min(len(PAYLOAD), self.offset + size)
        selected = PAYLOAD[self.offset : end]
        self.offset = end
        return selected


def _adapter() -> tuple[WindowsVerifiedPdfDestination, WindowsExportDestination]:
    backend = WindowsExportDestination()
    return WindowsVerifiedPdfDestination(backend), backend


def test_committed_exact_result_forwards_every_trusted_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, backend = _adapter()
    source = _Source()
    calls = 0

    def cancellation_probe() -> bool:
        return False

    def deadline_probe() -> bool:
        return False

    def commit(
        selected: WindowsExportDestination,
        destination: Path,
        candidate: _Source,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: object,
        deadline: object,
    ) -> ExportDestinationResult:
        nonlocal calls
        calls += 1
        assert selected is backend
        assert destination == DESTINATION
        assert candidate is source
        assert expected_byte_size == len(PAYLOAD)
        assert expected_sha256 == DIGEST
        assert cancellation is cancellation_probe
        assert deadline is deadline_probe
        return ExportDestinationResult(
            ExportCommitState.COMMITTED,
            len(PAYLOAD),
            DIGEST,
            recovered_outcome=True,
        )

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)

    adapter.commit(
        DESTINATION,
        source,
        expected_byte_size=len(PAYLOAD),
        expected_sha256=DIGEST,
        cancellation=cancellation_probe,
        deadline=deadline_probe,
    )
    assert calls == 1
    assert repr(adapter) == "WindowsVerifiedPdfDestination()"
    assert str(DESTINATION) not in repr(adapter)
    assert DIGEST not in repr(adapter)


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (
            ExportDestinationResult(ExportCommitState.DESTINATION_EXISTS),
            PdfExportErrorCode.DESTINATION_EXISTS,
        ),
        (
            ExportDestinationResult(ExportCommitState.NOT_COMMITTED),
            PdfExportErrorCode.DESTINATION_FAILED,
        ),
        (
            ExportDestinationResult(ExportCommitState.UNKNOWN, recovered_outcome=True),
            PdfExportErrorCode.DESTINATION_FAILED,
        ),
        (
            ExportDestinationResult(ExportCommitState.COMMITTED, len(PAYLOAD) + 1, DIGEST),
            PdfExportErrorCode.DESTINATION_FAILED,
        ),
        (
            ExportDestinationResult(ExportCommitState.COMMITTED, len(PAYLOAD), "f" * 64),
            PdfExportErrorCode.DESTINATION_FAILED,
        ),
    ),
)
def test_storage_states_and_committed_facts_map_exactly(
    monkeypatch: pytest.MonkeyPatch,
    result: ExportDestinationResult,
    expected: PdfExportErrorCode,
) -> None:
    adapter, _backend = _adapter()

    def commit(*_args: object, **_kwargs: object) -> ExportDestinationResult:
        return result

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)

    with pytest.raises(PdfExportError) as raised:
        adapter.commit(
            DESTINATION,
            _Source(),
            expected_byte_size=len(PAYLOAD),
            expected_sha256=DIGEST,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is expected
    assert str(DESTINATION) not in str(raised.value)
    assert DIGEST not in str(raised.value)


_INVALID_DESTINATION_ERRORS = (
    ExportDestinationErrorCode.PATH_INVALID,
    ExportDestinationErrorCode.LOCATION_UNSUPPORTED,
    ExportDestinationErrorCode.NAME_INVALID,
    ExportDestinationErrorCode.PARENT_UNAVAILABLE,
    ExportDestinationErrorCode.DESTINATION_UNSAFE,
    ExportDestinationErrorCode.PLATFORM_UNSUPPORTED,
)
_INVALID_SOURCE_ERRORS = (
    ExportDestinationErrorCode.SOURCE_INVALID,
    ExportDestinationErrorCode.SOURCE_MISMATCH,
)


def _mapped_error(code: ExportDestinationErrorCode) -> PdfExportErrorCode:
    if code is ExportDestinationErrorCode.CANCELLED:
        return PdfExportErrorCode.CANCELLED
    if code is ExportDestinationErrorCode.TIMED_OUT:
        return PdfExportErrorCode.TIMED_OUT
    if code in _INVALID_DESTINATION_ERRORS:
        return PdfExportErrorCode.DESTINATION_INVALID
    if code in _INVALID_SOURCE_ERRORS:
        return PdfExportErrorCode.RESULT_INVALID
    return PdfExportErrorCode.DESTINATION_FAILED


@pytest.mark.parametrize("storage_code", tuple(ExportDestinationErrorCode))
def test_every_storage_error_code_has_one_exact_sanitized_mapping(
    monkeypatch: pytest.MonkeyPatch,
    storage_code: ExportDestinationErrorCode,
) -> None:
    adapter, _backend = _adapter()
    canary = "private destination dependency detail"

    def commit(*_args: object, **_kwargs: object) -> ExportDestinationResult:
        raise ExportDestinationError(storage_code, "export.test")

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)

    with pytest.raises(PdfExportError) as raised:
        adapter.commit(
            DESTINATION,
            _Source(),
            expected_byte_size=len(PAYLOAD),
            expected_sha256=DIGEST,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is _mapped_error(storage_code)
    assert canary not in str(raised.value)
    assert str(DESTINATION) not in str(raised.value)
    assert DIGEST not in repr(raised.value.args)


@dataclass(slots=True)
class _HostileResult:
    state: object
    byte_size: object = None
    sha256: object = None


@pytest.mark.parametrize(
    "result",
    (
        object(),
        _HostileResult("committed", len(PAYLOAD), DIGEST),
        _HostileResult(ExportCommitState.COMMITTED, "large", DIGEST),
        _HostileResult(ExportCommitState.COMMITTED, len(PAYLOAD), object()),
    ),
)
def test_hostile_dependency_results_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
) -> None:
    adapter, _backend = _adapter()

    def commit(*_args: object, **_kwargs: object) -> object:
        return result

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)

    with pytest.raises(PdfExportError) as raised:
        adapter.commit(
            DESTINATION,
            _Source(),
            expected_byte_size=len(PAYLOAD),
            expected_sha256=DIGEST,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is PdfExportErrorCode.DESTINATION_FAILED


@pytest.mark.parametrize("failure", (RuntimeError("private backend failure"), KeyboardInterrupt()))
def test_raw_backend_base_exception_and_invalid_adapter_dependency_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    adapter, _backend = _adapter()

    def commit(*_args: object, **_kwargs: object) -> ExportDestinationResult:
        raise failure

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)
    with pytest.raises(PdfExportError) as raised:
        adapter.commit(
            DESTINATION,
            _Source(),
            expected_byte_size=len(PAYLOAD),
            expected_sha256=DIGEST,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert raised.value.code is PdfExportErrorCode.DESTINATION_FAILED
    assert "private backend failure" not in str(raised.value)

    with pytest.raises(PdfExportError) as invalid:
        WindowsVerifiedPdfDestination(cast(WindowsExportDestination, object()))
    assert invalid.value.code is PdfExportErrorCode.MODEL_INVALID


def test_closed_pdf_export_error_from_backend_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _backend = _adapter()
    expected = PdfExportError(PdfExportErrorCode.CANCELLED, "export.test")

    def commit(*_args: object, **_kwargs: object) -> ExportDestinationResult:
        raise expected

    monkeypatch.setattr(WindowsExportDestination, "commit", commit)
    with pytest.raises(PdfExportError) as raised:
        adapter.commit(
            DESTINATION,
            _Source(),
            expected_byte_size=len(PAYLOAD),
            expected_sha256=DIGEST,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value is expected
