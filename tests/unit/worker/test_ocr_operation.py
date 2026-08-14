"""Focused production OCR worker-operation tests at pure seams."""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, cast
from uuid import UUID

import pytest

import paperworkshell.worker.ocr_operation as operation
from paperworkshell.domain.ocr import (
    OcrError,
    OcrErrorCode,
    ocr_engine_profile_sha256,
    reject_ocr,
)
from paperworkshell.ocr.ocrmypdf_adapter import OcrAdapterResult
from paperworkshell.worker.ocr_operation import (
    OCR_ENGINE_PROFILE_SHA256,
    OCRMY_PDF_ENGINE_VERSION,
    TESSERACT_ENGINE_VERSION,
    _languages_for_set,
    _private_operation_temp,
    build_ocr_process_operation_handler,
)
from paperworkshell.worker.operations import OcrProcessOperationRequest, OperationContext
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import (
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"8c000000-0000-4000-8000-{number:012d}")


@dataclass(slots=True)
class _CandidateProcessor:
    payload: bytes = b"%PDF-1.7\nsynthetic OCR candidate\n%%EOF\n"
    calls: int = 0

    def process(
        self,
        source_path: Path,
        output_path: Path,
        **options: object,
    ) -> OcrAdapterResult:
        self.calls += 1
        assert source_path.read_bytes().startswith(b"%PDF-")
        output_path.write_bytes(self.payload)
        return OcrAdapterResult(
            byte_size=len(self.payload),
            page_count=cast(int, options["expected_page_count"]),
            sha256=hashlib.sha256(self.payload).hexdigest(),
            ocrmypdf_version=OCRMY_PDF_ENGINE_VERSION,
            tesseract_version=TESSERACT_ENGINE_VERSION,
        )


class _TempStorage:
    def require_capacity(self, _root: Path, _required: int) -> None:
        return None

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        return io.BytesIO(), root / f"unused{suffix}"

    def open_anonymous(self, _root: Path) -> BinaryIO:
        return io.BytesIO()


class _CapacityRefusalTempStorage:
    def __init__(self, *, available_bytes: int) -> None:
        self._storage = OsWorkerPrivateTempStorage(
            available_bytes_probe=lambda _root: available_bytes,
            minimum_free_space_reserve_bytes=0,
        )
        self.named_opens = 0

    def require_capacity(self, root: Path, byte_count: int) -> None:
        self._storage.require_capacity(root, byte_count)

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        self.named_opens += 1
        return self._storage.open_named(root, suffix=suffix)

    def open_anonymous(self, root: Path) -> BinaryIO:
        return self._storage.open_anonymous(root)


class _DiskFullStream:
    """Persist a partial staged source, then raise one content-bearing ENOSPC."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def __enter__(self) -> _DiskFullStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def write(self, payload: bytes, /) -> int:
        self._stream.write(payload[: max(1, len(payload) // 2)])
        raise OSError(28, "private OCR source disk-full detail")

    def close(self) -> None:
        self._stream.close()

    def __getattr__(self, member: str) -> Any:
        return getattr(self._stream, member)


def _context(source_payload: bytes, *, temporary_storage_bytes: int = 4_096) -> OperationContext:
    return OperationContext(
        operation_id=_uuid(10),
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=1_000,
            cpu_time_ms=500,
            memory_bytes=1024 * 1024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=4_096,
            page_count=1,
            pixel_count=1_000_000,
        ),
        cancel_requested=Event(),
        report_progress=lambda _value: None,
        deadline_expired=lambda: False,
        inputs=(
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _uuid(2),
                None,
                "pdf",
                len(source_payload),
                hashlib.sha256(source_payload).hexdigest(),
            ),
        ),
    )


def _request() -> OcrProcessOperationRequest:
    return OcrProcessOperationRequest(
        source_handle=101,
        output_handle=202,
        result_handle=303,
        output_object_id=_uuid(20),
        source_content_kind="pdf",
        page_count=1,
        language_set_id="latin-default-v1",
        profile_id="searchable-pdf-v1",
    )


def _source_stream(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> io.BytesIO:
    source = io.BytesIO(payload)
    source.fileno = lambda: 99  # type: ignore[method-assign]
    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.os.fstat",
        lambda _descriptor: type(
            "Info",
            (),
            {"st_mode": 0o100000, "st_size": len(payload)},
        )(),
    )
    return source


def test_fixed_engine_profile_hash_is_public_and_deterministic() -> None:
    assert OCR_ENGINE_PROFILE_SHA256 == ocr_engine_profile_sha256(
        OCRMY_PDF_ENGINE_VERSION,
        TESSERACT_ENGINE_VERSION,
    )
    assert OCR_ENGINE_PROFILE_SHA256 == ocr_engine_profile_sha256("17.10.0", "5.5.3")
    assert len(OCR_ENGINE_PROFILE_SHA256) == 64
    assert OCR_ENGINE_PROFILE_SHA256 != hashlib.sha256(b"17.10.0|5.5.3").hexdigest()


def test_language_set_is_opaque_configuration_not_tesseract_argv() -> None:
    assert _languages_for_set("latin-default-v1") == ("eng",)
    assert _languages_for_set("german-fast-v1") == ("deu",)
    assert _languages_for_set("english-german-fast-v1") == ("eng", "deu")

    with pytest.raises(OcrError) as raised:
        _languages_for_set("eng;--config=private-file")

    assert raised.value.code is OcrErrorCode.MODEL_INVALID
    assert "private-file" not in str(raised.value)


@pytest.mark.skipif(os.name != "nt", reason="production OCR operation is Windows-only")
def test_unsupported_language_set_is_rejected_before_source_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = _CandidateProcessor()
    adapter = operation.WindowsOcrProcessAdapter(
        candidate_processor=processor,
        private_temp_storage=_TempStorage(),
    )
    temp_materialized = False

    def materialize() -> Path:
        nonlocal temp_materialized
        temp_materialized = True
        return Path("unused")

    closed: list[int] = []
    monkeypatch.setattr(operation, "_materialize_private_temp", materialize)
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", closed.append)

    with pytest.raises(OcrError) as raised:
        adapter.process(
            _context(b"%PDF-1.7\n%%EOF\n"),
            replace(_request(), language_set_id="unsupported-private-v1"),
        )

    assert raised.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert not temp_materialized
    assert processor.calls == 0
    assert sorted(closed) == [101, 202, 303]


def test_missing_fixed_resource_environment_builds_fail_closed_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_TESSERACT_EXECUTABLE", raising=False)
    monkeypatch.delenv("PWS_TESSDATA_ROOT", raising=False)

    assert build_ocr_process_operation_handler() is None


@pytest.mark.skipif(os.name != "nt", reason="production OCR admission is Windows-only")
def test_exact_available_engine_resources_are_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"synthetic executable")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    private_profile = tmp_path / "profile"
    private_profile.mkdir()
    private_temp = private_profile / "Temp"
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(private_profile))
    monkeypatch.setenv("TEMP", os.fspath(private_temp))
    monkeypatch.setenv("TMP", os.fspath(private_temp))
    monkeypatch.setattr(operation, "distribution_version", lambda _name: "17.10.0")
    monkeypatch.setattr(operation, "loaded_ocrmypdf_version", lambda: "17.10.0")

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def version(self) -> str:
            return "5.5.3"

        def languages(self) -> frozenset[str]:
            return frozenset({"eng"})

    monkeypatch.setattr(operation, "TesseractCommandRunner", _Runner)

    handler = build_ocr_process_operation_handler()

    assert handler is not None
    assert private_temp.is_dir()
    assert type(handler.adapter).__name__ == "WindowsOcrProcessAdapter"


@pytest.mark.skipif(os.name != "nt", reason="production OCR admission is Windows-only")
@pytest.mark.parametrize(
    ("ocrmypdf_version", "tesseract_version", "languages"),
    [
        ("17.10.1", "5.5.3", frozenset({"eng"})),
        ("17.10.0", "5.5.4", frozenset({"eng"})),
        ("17.10.0", "5.5.3", frozenset({"deu"})),
    ],
)
def test_untrusted_or_incomplete_engine_admission_omits_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ocrmypdf_version: str,
    tesseract_version: str,
    languages: frozenset[str],
) -> None:
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"synthetic executable")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    private_profile = tmp_path / "profile"
    private_profile.mkdir()
    private_temp = private_profile / "Temp"
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(private_profile))
    monkeypatch.setenv("TEMP", os.fspath(private_temp))
    monkeypatch.setenv("TMP", os.fspath(private_temp))
    monkeypatch.setattr(operation, "distribution_version", lambda _name: ocrmypdf_version)
    monkeypatch.setattr(operation, "loaded_ocrmypdf_version", lambda: ocrmypdf_version)

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def version(self) -> str:
            return tesseract_version

        def languages(self) -> frozenset[str]:
            return languages

    monkeypatch.setattr(operation, "TesseractCommandRunner", _Runner)

    assert build_ocr_process_operation_handler() is None


def test_private_temp_capacity_refusal_precedes_staging_and_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"%PDF-1.7\nsynthetic source\n%%EOF\n"
    context = _context(payload, temporary_storage_bytes=128)
    processor = _CandidateProcessor()
    storage = _CapacityRefusalTempStorage(
        available_bytes=context.limits.temporary_storage_bytes - 1,
    )
    adapter = operation.WindowsOcrProcessAdapter(
        candidate_processor=processor,
        private_temp_storage=storage,
    )
    temp_root = tmp_path.resolve()
    monkeypatch.setattr(operation, "_materialize_private_temp", lambda: temp_root)

    with pytest.raises(OcrError) as raised:
        adapter._process_open_source(
            context,
            _request(),
            context.inputs[0],
            _source_stream(monkeypatch, payload),
            operation._OwnedHandles({202, 303}),
            ("eng",),
        )

    assert raised.value.code is OcrErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "ocr.temp_capacity"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert processor.calls == 0
    assert storage.named_opens == 0
    assert tuple(temp_root.iterdir()) == ()


def test_mid_source_disk_full_cleans_partial_and_fresh_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"%PDF-1.7\nsynthetic source spanning the staged write\n%%EOF\n"
    context = _context(payload)
    processor = _CandidateProcessor()
    adapter = operation.WindowsOcrProcessAdapter(
        candidate_processor=processor,
        private_temp_storage=_TempStorage(),
    )
    temp_root = (tmp_path / "worker-profile" / "Temp").resolve()
    temp_root.mkdir(parents=True)
    monkeypatch.setattr(operation, "_materialize_private_temp", lambda: temp_root)
    monkeypatch.setattr(operation, "_close_handle_checked", lambda _handle: True)
    operation_counter = 0

    def create_operation_root(root: Path) -> tuple[Path, int]:
        nonlocal operation_counter
        operation_counter += 1
        operation_root = root / f"pws-ocr-test-{operation_counter}"
        operation_root.mkdir()
        return operation_root, 100 + operation_counter

    monkeypatch.setattr(operation, "_create_private_operation_temp", create_operation_root)
    real_open = Path.open
    fail_next_stage = True
    failed_stream: _DiskFullStream | None = None

    def open_with_one_disk_full(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        nonlocal fail_next_stage, failed_stream
        stream = real_open(path, mode, buffering, encoding, errors, newline)
        if fail_next_stage and mode == "xb" and path.name.startswith("source-"):
            fail_next_stage = False
            failed_stream = _DiskFullStream(cast(BinaryIO, stream))
            return failed_stream
        return stream

    monkeypatch.setattr(Path, "open", open_with_one_disk_full)
    copied_candidates: list[bytes] = []

    def copy_candidate(
        path: Path,
        handle: int,
        *,
        capacity: int,
        expected: OcrAdapterResult,
        control: object,
    ) -> tuple[int, str]:
        del control
        assert handle == 202
        candidate = path.read_bytes()
        assert len(candidate) <= capacity
        assert len(candidate) == expected.byte_size
        assert hashlib.sha256(candidate).hexdigest() == expected.sha256
        copied_candidates.append(candidate)
        return len(candidate), expected.sha256

    reports: list[tuple[int, bytes]] = []
    monkeypatch.setattr(operation, "_copy_candidate_to_section", copy_candidate)
    monkeypatch.setattr(
        operation,
        "_write_result_pipe",
        lambda handle, report: reports.append((handle, report)),
    )

    with pytest.raises(OcrError) as raised:
        adapter._process_open_source(
            context,
            _request(),
            context.inputs[0],
            _source_stream(monkeypatch, payload),
            operation._OwnedHandles({202, 303}),
            ("eng",),
        )

    assert raised.value.code is OcrErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "ocr.source_write"
    assert "private OCR source" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert failed_stream is not None and failed_stream.closed
    assert processor.calls == 0
    assert copied_candidates == []
    assert reports == []
    assert tuple(temp_root.iterdir()) == ()

    reference = adapter._process_open_source(
        context,
        _request(),
        context.inputs[0],
        _source_stream(monkeypatch, payload),
        operation._OwnedHandles({202, 303}),
        ("eng",),
    )

    assert reference.expected_byte_size == len(processor.payload)
    assert reference.expected_sha256 == hashlib.sha256(processor.payload).hexdigest()
    assert copied_candidates == [processor.payload]
    assert len(reports) == 1 and reports[0][0] == 303
    assert processor.calls == 1
    assert tuple(temp_root.iterdir()) == ()


def test_cleanup_failure_overrides_cancellation_body_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_root = tmp_path / "operation"
    operation_root.mkdir()
    monkeypatch.setattr(
        operation,
        "_create_private_operation_temp",
        lambda _root: (operation_root, 101),
    )
    monkeypatch.setattr(operation, "_remove_pinned_operation_temp", lambda _root, _pin: False)

    with pytest.raises(OcrError) as raised:
        with _private_operation_temp(tmp_path):
            reject_ocr(OcrErrorCode.CANCELLED, "ocr.operation")

    assert raised.value.code is OcrErrorCode.INTERNAL_ERROR
    assert raised.value.context == "ocr.temp_cleanup"


def test_recursive_cleanup_rejects_reparse_without_following_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_root = tmp_path / "operation"
    operation_root.mkdir()
    child = operation_root / "nested"
    child.mkdir()
    marker = child / "marker.bin"
    marker.write_bytes(b"synthetic")
    real_lstat = os.lstat

    class _ReparseInfo:
        st_mode = real_lstat(child).st_mode
        st_file_attributes = operation._FILE_ATTRIBUTE_REPARSE_POINT

    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.os.lstat",
        lambda path: _ReparseInfo() if Path(path) == child else real_lstat(path),
    )

    assert not operation._remove_tree_contents(operation_root, [0])
    assert marker.is_file()
