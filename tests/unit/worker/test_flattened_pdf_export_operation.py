"""Focused boundary tests for the concrete flattened-PDF worker operation."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, cast
from uuid import UUID

import pytest

import paperworkshell.worker.flattened_pdf_export_operation as operation
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.overlays.models import (
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OverlayRasterAsset,
    OverlayRasterSource,
    OverlayRenderLimits,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.worker.flattened_pdf_export_plan import encode_flattened_pdf_worker_plan
from paperworkshell.worker.flattened_pdf_export_report import (
    decode_flattened_pdf_worker_report,
)
from paperworkshell.worker.operations import (
    FlattenedPdfExportOperationHandler,
    FlattenedPdfExportOperationRequest,
    OperationContext,
)
from paperworkshell.worker.pdf_export_buffers import PdfExportSectionWriteResult
from paperworkshell.worker.pdf_export_report import (
    PIKEPDF_EXPORT_ENGINE_VERSION,
    QPDF_EXPORT_ENGINE_VERSION,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)
from tests.unit.worker.flattened_pdf_export_fixtures import (
    synthetic_flattened_plan,
    uuid4,
)
from tests.unit.worker.pdf_optimization_fixtures import OptimizationStub


@pytest.fixture(autouse=True)
def optimizer(monkeypatch: pytest.MonkeyPatch) -> OptimizationStub:
    stub = OptimizationStub()
    monkeypatch.setattr(operation, "optimize_verified_pdf", stub)
    return stub


def _limits(*, memory_bytes: int = 256 * 1024 * 1024) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=30_000,
        cpu_time_ms=20_000,
        memory_bytes=memory_bytes,
        temporary_storage_bytes=16 * 1024 * 1024,
        output_bytes=1024 * 1024,
        page_count=1,
        pixel_count=4,
    )


def _payload(plan: FlattenedPdfExportPlan, *, source_handle: int = 601) -> bytes:
    return encode_flattened_pdf_worker_plan(
        plan,
        {plan.base_plan.sources[0].source_id: source_handle},
        {plan.assets[0].asset_id: 603},
    )


def _context(
    plan: FlattenedPdfExportPlan,
    payload: bytes,
    *,
    cancel: Event | None = None,
    deadline_expired: bool = False,
    raster_digest: str | None = None,
    limits: ResourceLimits | None = None,
) -> OperationContext:
    source = plan.base_plan.sources[0]
    raster = plan.assets[0].raster_asset
    return OperationContext(
        operation_id=uuid4(31),
        case_id=plan.base_plan.case_id,
        limits=_limits() if limits is None else limits,
        cancel_requested=Event() if cancel is None else cancel,
        report_progress=lambda _fact: None,
        deadline_expired=lambda: deadline_expired,
        inputs=(
            CaseObjectReference(
                ReferenceArea.OPERATION_TEMP,
                uuid4(20),
                uuid4(31),
                FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                source.source_id,
                None,
                "pdf",
                source.byte_size,
                source.sha256,
            ),
            CaseObjectReference(
                ReferenceArea.OPERATION_TEMP,
                raster.asset_id,
                uuid4(31),
                FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
                raster.byte_count,
                raster.pixel_sha256 if raster_digest is None else raster_digest,
            ),
        ),
    )


def _request() -> FlattenedPdfExportOperationRequest:
    return FlattenedPdfExportOperationRequest(501, 503, 505, uuid4(23))


class _Storage:
    def require_capacity(self, _root: Path, _byte_count: int) -> None:
        return None

    def open_named(self, _root: Path, *, suffix: str) -> object:
        del suffix
        raise AssertionError("source staging is replaced by the boundary fixture")


class _MappedRaster:
    closed_handles: list[int] = []

    def __init__(self, handle: int, metadata: OverlayRasterAsset) -> None:
        self.handle = handle
        self.metadata = metadata
        self.stream = io.BytesIO(bytes(range(metadata.byte_count)))

    @classmethod
    def adopt(
        cls,
        handle: int,
        metadata: OverlayRasterAsset,
        **_kwargs: object,
    ) -> _MappedRaster:
        return cls(handle, metadata)

    def read(self, size: int = -1, /) -> bytes:
        return self.stream.read(size)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        return self.stream.tell()

    def __enter__(self) -> _MappedRaster:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stream.close()
        self.closed_handles.append(self.handle)


class _Candidate:
    def __init__(self, output: bytes, *, fail: bool = False) -> None:
        self.output = output
        self.fail = fail
        self.observed_limits: tuple[PdfExportAdapterLimits, OverlayRenderLimits] | None = None

    def assemble_and_verify(
        self,
        plan: FlattenedPdfExportPlan,
        source_streams: Mapping[UUID, BinaryIO],
        raster_sources: Mapping[UUID, OverlayRasterSource],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        overlay_limits: OverlayRenderLimits,
        control: PdfExportAdapterControl,
    ) -> FlattenedPdfExportVerificationReport:
        self.observed_limits = (limits, overlay_limits)
        assert not control.cancellation_requested()
        assert source_streams[plan.base_plan.sources[0].source_id].read(4) == b"%PDF"
        raster = raster_sources[plan.assets[0].asset_id]
        assert raster.metadata == plan.assets[0].raster_asset
        if self.fail:
            raise FlattenedPdfExportError(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.synthetic",
            )
        output_path.write_bytes(self.output)
        return FlattenedPdfExportVerificationReport.for_verified_plan(
            plan,
            output_sha256=hashlib.sha256(self.output).hexdigest(),
            output_byte_size=len(self.output),
        )


def _patch_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
    consumed: list[int],
    report_frames: list[bytes],
    *,
    mismatched_output: bool = False,
) -> dict[str, bool]:
    state = {"temp_exited": False}
    private_root = tmp_path / "private"
    private_root.mkdir(exist_ok=True)
    operation_root = private_root / "operation"

    def read_plan(handle: int, **_kwargs: object) -> bytes:
        consumed.append(handle)
        return payload

    def stage_source(
        _context: OperationContext,
        worker_source: object,
        _reference: object,
        handle: int,
        *,
        operation_temp: Path,
        **_kwargs: object,
    ) -> Path:
        consumed.append(handle)
        source_path = operation_temp / "source.pdf"
        source_path.write_bytes(b"%PDF synthetic")
        del worker_source
        return source_path

    def copy_output(
        handle: int,
        source: BinaryIO,
        *,
        byte_count: int,
        expected_sha256: str,
        **_kwargs: object,
    ) -> PdfExportSectionWriteResult:
        consumed.append(handle)
        data = source.read()
        assert len(data) == byte_count
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        if mismatched_output:
            return PdfExportSectionWriteResult(byte_count, "f" * 64)
        return PdfExportSectionWriteResult(byte_count, expected_sha256)

    def write_report(handle: int, frame: bytes) -> None:
        consumed.append(handle)
        report_frames.append(frame)

    @contextmanager
    def operation_temp(_root: Path) -> Any:
        operation_root.mkdir()
        try:
            yield operation_root
        finally:
            state["temp_exited"] = True
            for child in tuple(operation_root.iterdir()):
                child.unlink()
            operation_root.rmdir()

    monkeypatch.setattr(operation, "read_pdf_export_plan_section", read_plan)
    monkeypatch.setattr(operation, "_stage_source_handle", stage_source)
    monkeypatch.setattr(operation, "write_pdf_export_output_section", copy_output)
    monkeypatch.setattr(operation, "_write_result_pipe", write_report)
    monkeypatch.setattr(operation, "_materialize_worker_private_temp", lambda: private_root)
    monkeypatch.setattr(operation, "_worker_private_operation_temp", operation_temp)
    monkeypatch.setattr(operation, "FlattenedPdfRasterSection", _MappedRaster)
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", consumed.append)
    _MappedRaster.closed_handles = []
    return state


@pytest.mark.parametrize("select_smaller", (False, True))
def test_worker_adapter_publishes_bound_output_report_and_closes_every_handle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    optimizer: OptimizationStub,
    select_smaller: bool,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    output = b"%PDF flattened synthetic output"
    candidate = _Candidate(output)
    expected_output = b"%PDF selected" if select_smaller else output
    optimizer.output = expected_output if select_smaller else None
    consumed: list[int] = []
    frames: list[bytes] = []
    state = _patch_boundary(monkeypatch, tmp_path, payload, consumed, frames)

    reference = operation.WindowsFlattenedPdfExportOperationAdapter(
        candidate,
        cast(Any, _Storage()),
    ).export(_context(plan, payload), _request())

    assert reference.object_id == _request().output_object_id
    assert reference.expected_byte_size == len(expected_output)
    assert reference.expected_sha256 == hashlib.sha256(expected_output).hexdigest()
    assert sorted((*consumed, *_MappedRaster.closed_handles)) == [501, 503, 505, 601, 603]
    assert len(set((*consumed, *_MappedRaster.closed_handles))) == 5
    assert state["temp_exited"]
    assert candidate.observed_limits is not None
    decoded = decode_flattened_pdf_worker_report(frames[0], plan=plan)
    assert decoded.operation_id == uuid4(31)
    assert decoded.output_object_id == _request().output_object_id
    assert decoded.report.base_report.output_sha256 == reference.expected_sha256
    assert decoded.report.base_report.output_byte_size == len(expected_output)
    assert decoded.report.base_report.optimization is not None
    assert decoded.report.base_report.optimization.baseline_byte_size == len(output)
    assert decoded.report.base_report.optimization.selected_byte_size == len(expected_output)
    assert (
        decoded.report.checks
        == FlattenedPdfExportVerificationReport.for_verified_plan(
            plan, output_byte_size=len(output), output_sha256=hashlib.sha256(output).hexdigest()
        ).checks
    )
    assert len(optimizer.calls) == 1
    assert optimizer.calls[0].reserved_temporary_bytes == plan.assets[0].raster_asset.byte_count
    assert optimizer.calls[0].resource_limits == _limits()


@pytest.mark.parametrize(
    ("code", "mapped"),
    (
        (PdfExportErrorCode.CANCELLED, FlattenedPdfExportErrorCode.CANCELLED),
        (PdfExportErrorCode.TIMED_OUT, FlattenedPdfExportErrorCode.TIMED_OUT),
        (PdfExportErrorCode.LIMIT_EXCEEDED, FlattenedPdfExportErrorCode.LIMIT_EXCEEDED),
    ),
)
def test_optimizer_failure_keeps_output_unpublished_and_closes_raster_and_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    optimizer: OptimizationStub,
    code: PdfExportErrorCode,
    mapped: FlattenedPdfExportErrorCode,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    consumed: list[int] = []
    frames: list[bytes] = []
    state = _patch_boundary(monkeypatch, tmp_path, payload, consumed, frames)
    optimizer.failure = PdfExportError(code, "optimization.synthetic")
    with pytest.raises(FlattenedPdfExportError) as caught:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"%PDF baseline"), cast(Any, _Storage())
        ).export(_context(plan, payload), _request())
    assert caught.value.code is mapped
    assert frames == [] and state["temp_exited"]
    assert len(optimizer.calls) == 1
    assert sorted((*consumed, *_MappedRaster.closed_handles)) == [501, 503, 505, 601, 603]


def test_malformed_plan_closes_only_discoverable_request_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = synthetic_flattened_plan()
    payload = b"{}"
    consumed: list[int] = []
    _patch_boundary(monkeypatch, tmp_path, payload, consumed, [])

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"unused"),
            cast(Any, _Storage()),
        ).export(_context(plan, payload), _request())

    assert captured.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert sorted(consumed) == [501, 503, 505]


def test_raster_reference_mismatch_closes_all_discovered_handles_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    consumed: list[int] = []
    _patch_boundary(monkeypatch, tmp_path, payload, consumed, [])

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"unused"),
            cast(Any, _Storage()),
        ).export(_context(plan, payload, raster_digest="f" * 64), _request())

    assert captured.value.code is FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID
    assert sorted(consumed) == [501, 503, 505, 601, 603]
    assert len(set(consumed)) == 5


def test_alias_with_consumed_plan_handle_is_rejected_without_double_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan, source_handle=501)
    consumed: list[int] = []
    _patch_boundary(monkeypatch, tmp_path, payload, consumed, [])

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"unused"),
            cast(Any, _Storage()),
        ).export(_context(plan, payload), _request())

    assert captured.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert sorted(consumed) == [501, 503, 505, 603]
    assert consumed.count(501) == 1


@pytest.mark.parametrize(
    ("cancelled", "deadline", "code"),
    (
        (True, False, FlattenedPdfExportErrorCode.CANCELLED),
        (False, True, FlattenedPdfExportErrorCode.TIMED_OUT),
    ),
)
def test_terminal_probe_prevents_plan_read_and_closes_request_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
    deadline: bool,
    code: FlattenedPdfExportErrorCode,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    consumed: list[int] = []
    _patch_boundary(monkeypatch, tmp_path, payload, consumed, [])
    event = Event()
    if cancelled:
        event.set()

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"unused"),
            cast(Any, _Storage()),
        ).export(
            _context(plan, payload, cancel=event, deadline_expired=deadline),
            _request(),
        )

    assert captured.value.code is code
    assert sorted(consumed) == [501, 503, 505]


def test_candidate_failure_closes_raster_and_private_temp_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    consumed: list[int] = []
    state = _patch_boundary(monkeypatch, tmp_path, payload, consumed, [])

    with pytest.raises(FlattenedPdfExportError):
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"unused", fail=True),
            cast(Any, _Storage()),
        ).export(_context(plan, payload), _request())

    assert state["temp_exited"]
    assert _MappedRaster.closed_handles == [603]
    assert sorted((*consumed, *_MappedRaster.closed_handles)) == [501, 503, 505, 601, 603]


def test_output_report_binding_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)
    consumed: list[int] = []
    _patch_boundary(
        monkeypatch,
        tmp_path,
        payload,
        consumed,
        [],
        mismatched_output=True,
    )

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation.WindowsFlattenedPdfExportOperationAdapter(
            _Candidate(b"%PDF mismatch"),
            cast(Any, _Storage()),
        ).export(_context(plan, payload), _request())

    assert captured.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID


def test_overlay_limit_derivation_rejects_inadequate_shared_memory() -> None:
    plan = synthetic_flattened_plan()
    payload = _payload(plan)

    with pytest.raises(FlattenedPdfExportError) as captured:
        operation._overlay_limits(
            _context(plan, payload, limits=_limits(memory_bytes=4_096)),
            plan,
            source_total=plan.base_plan.sources[0].byte_size,
            raster_total=plan.assets[0].raster_asset.byte_count,
        )

    assert captured.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED


def test_text_only_overlay_limits_keep_bounded_io_independent_of_raster_allowance() -> None:
    source_plan = synthetic_flattened_plan()
    source_page = source_plan.pages[0]
    source_record = source_page.overlays[0]
    text_record = OverlayRecord(
        overlay_id=uuid4(40),
        page_id=source_page.base_page.page_id,
        box=source_record.box,
        z_index=source_record.z_index,
        payload=TextualOverlayPayload(
            OverlayKind.TEXT,
            "Synthetic text-only overlay",
            "paperworkshell-default",
            12,
            RgbaColor("#102030ff"),
        ),
    )
    text_only_plan = FlattenedPdfExportPlan(
        base_plan=source_plan.base_plan,
        pages=(
            FlattenedPdfExportPage(
                base_page=source_page.base_page,
                active_crop=source_page.active_crop,
                overlays=(text_record,),
            ),
        ),
        assets=(),
    )
    context = _context(source_plan, _payload(source_plan))

    limits = operation._overlay_limits(
        context,
        text_only_plan,
        source_total=text_only_plan.base_plan.sources[0].byte_size,
        raster_total=0,
    )

    assert limits.maximum_total_raster_bytes == 1
    assert limits.io_chunk_bytes == min(operation._COPY_CHUNK_BYTES, context.limits.memory_bytes)
    assert limits.io_chunk_bytes > limits.maximum_total_raster_bytes


def test_builder_omits_capability_without_an_explicit_qpdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(operation.PWS_QPDF_EXECUTABLE_ENVIRONMENT, raising=False)

    assert operation.build_flattened_pdf_export_operation_handler() is None


def test_builder_admits_only_the_exact_available_engine_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "qpdf.exe"
    executable.write_bytes(b"synthetic executable marker")
    monkeypatch.setenv(operation.PWS_QPDF_EXECUTABLE_ENVIRONMENT, str(executable))
    monkeypatch.setattr(
        operation,
        "loaded_pikepdf_engine_versions",
        lambda: (
            PIKEPDF_EXPORT_ENGINE_VERSION,
            QPDF_EXPORT_ENGINE_VERSION,
        ),
    )
    monkeypatch.setattr(operation, "require_approved_pdfium_runtime", lambda: None)
    monkeypatch.setattr(operation, "require_approved_overlay_runtime", lambda: None)
    monkeypatch.setattr(
        QpdfCommandChecker,
        "require_trusted_version",
        lambda _self, **_kwargs: None,
    )

    handler = operation.build_flattened_pdf_export_operation_handler()

    assert type(handler) is FlattenedPdfExportOperationHandler


def test_builder_rejects_mismatched_engine_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "qpdf.exe"
    executable.write_bytes(b"synthetic executable marker")
    monkeypatch.setenv(operation.PWS_QPDF_EXECUTABLE_ENVIRONMENT, str(executable))
    monkeypatch.setattr(operation, "loaded_pikepdf_engine_versions", lambda: ("future", "future"))

    assert operation.build_flattened_pdf_export_operation_handler() is None
