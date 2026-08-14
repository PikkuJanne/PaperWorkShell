from __future__ import annotations

import ctypes

import pytest

import paperworkshell.worker.quality_ocr_review_buffers as buffers_module
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
)
from paperworkshell.worker.quality_ocr_review_buffers import WindowsQualityOcrInputBuffer


def test_buffer_repr_is_redacted_and_close_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    def close_handle(handle: int) -> bool:
        closed.append(handle)
        return True

    monkeypatch.setattr(
        buffers_module,
        "_close_handle",
        close_handle,
    )
    buffer = WindowsQualityOcrInputBuffer(123, 64)

    assert repr(buffer) == "WindowsQualityOcrInputBuffer(<redacted>)"
    assert buffer.payload_size == 64
    assert buffer.read_handle_value == 123
    buffer.close()
    buffer.close()

    assert closed == [123]
    with pytest.raises(QualityOcrReviewError) as rejected:
        _ = buffer.read_handle_value
    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR


def test_failed_close_retains_handle_authority_for_a_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter((False, True))
    closed: list[int] = []

    def close_handle(handle: int) -> bool:
        closed.append(handle)
        return next(outcomes)

    monkeypatch.setattr(buffers_module, "_close_handle", close_handle)
    buffer = WindowsQualityOcrInputBuffer(123, 64)

    with pytest.raises(QualityOcrReviewError) as rejected:
        buffer.close()

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert buffer.read_handle_value == 123
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == [buffer]

    buffer.close()
    buffer.close()

    assert closed == [123, 123]
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == []
    with pytest.raises(QualityOcrReviewError):
        _ = buffer.read_handle_value


@pytest.mark.parametrize("failure_stage", ["copy_flush", "unmap"])
def test_create_rollback_retains_failed_view_and_handle_cleanup_until_drained(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    class _NativeCall:
        def __init__(self, result: object) -> None:
            self.result = result
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *_args: object) -> object:
            return self.result

    class _Kernel32:
        CreateFileMappingW = _NativeCall(123)
        SetHandleInformation = _NativeCall(True)
        MapViewOfFile = _NativeCall(456)

    cleanup_succeeds = False
    calls: list[tuple[str, int]] = []

    def unmap(view: int) -> bool:
        calls.append(("unmap", view))
        return cleanup_succeeds

    def close(handle: int) -> bool:
        calls.append(("close", handle))
        return cleanup_succeeds

    monkeypatch.setattr(buffers_module, "_kernel32", lambda: _Kernel32())
    monkeypatch.setattr(ctypes, "memmove", lambda *_args: None)
    monkeypatch.setattr(
        buffers_module,
        "_flush_view",
        lambda *_args: failure_stage != "copy_flush",
    )
    monkeypatch.setattr(buffers_module, "_unmap_view", unmap)
    monkeypatch.setattr(buffers_module, "_close_handle", close)

    with pytest.raises(QualityOcrReviewError) as rejected:
        WindowsQualityOcrInputBuffer.create(b"private raster")

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert rejected.value.context == "quality_ocr.input_cleanup"
    expected_failed_cleanup = (
        [("unmap", 456), ("close", 123)]
        if failure_stage == "copy_flush"
        else [("unmap", 456), ("unmap", 456), ("close", 123)]
    )
    assert calls == expected_failed_cleanup
    assert len(buffers_module._INPUT_CLEANUP_QUARANTINE) == 1
    retained = buffers_module._INPUT_CLEANUP_QUARANTINE[0]
    assert repr(retained) == "WindowsQualityOcrInputBuffer(<redacted>)"

    cleanup_succeeds = True
    buffers_module._drain_input_cleanup()

    assert calls == [*expected_failed_cleanup, ("unmap", 456), ("close", 123)]
    assert buffers_module._INPUT_CLEANUP_QUARANTINE == []


@pytest.mark.parametrize("payload", [b"", bytearray(b"private pixels")])
def test_buffer_rejects_invalid_payload_before_native_allocation(payload: object) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        WindowsQualityOcrInputBuffer.create(payload)  # type: ignore[arg-type]
    assert rejected.value.code is QualityOcrReviewErrorCode.MODEL_INVALID
