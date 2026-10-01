"""Bounded comparison rendering without launching native worker processes."""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID, uuid4

import pytest

import paperworkshell.worker.compression_preview as preview
from paperworkshell.domain.compression_review import CompressionPageChange
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportVerificationReport,
    build_pdf_export_plan_for_pages,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailResult,
    ResolvedPdfThumbnailRequest,
    encode_pdf_thumbnail_artifact,
    reject_pdf_thumbnail,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.worker.desktop_composition import create_compression_preview_renderer
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    CancellationProbe,
    DeadlineProbe,
    PdfThumbnailBrokerPolicy,
    ThumbnailPhaseSink,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)
from tests.helpers.compression_review import compression_review
from tests.unit.test_pdf_export import PAGE_A1, _snapshot
from tests.unit.worker.test_desktop_composition import _config
from tests.unit.worker.test_pdf_thumbnail_broker import _artifact


class _Sources:
    def __init__(self, snapshot: ManifestSnapshot) -> None:
        self.plan = build_pdf_export_plan_for_pages(snapshot, (PAGE_A1,))
        self.events: list[str] = []
        self.stale = False

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> preview._PageLease:
        assert case_id == self.plan.case_id and page_id == PAGE_A1
        assert not cancellation() and not deadline()
        self.events.append("borrow")
        source, page = self.plan.sources[0], self.plan.pages[0]
        lease = preview._PageLease(
            preview._Handle(101),
            case_id,
            source.source_id,
            page_id,
            page.source_page_index,
            source.page_count,
            source.byte_size,
            "d" * 64 if self.stale else source.sha256,
        )
        lease._owner.callback(self.events.append, "close")
        return lease


class _Worker:
    def __init__(self, events: list[str], **kwargs: object) -> None:
        self.events = events
        self.events.append("create")
        self.terminated = False
        assert kwargs["required_capabilities"] == (PDF_THUMBNAIL_CAPABILITY,)

    def terminate(self) -> None:
        self.events.append("terminate")
        self.terminated = True


@dataclass
class _Harness:
    renderer: preview.NativeCompressionPreviewRenderer
    sources: _Sources
    snapshot: ManifestSnapshot
    change: CompressionPageChange
    report: PdfExportVerificationReport
    heavy: HeavyOperationCoordinator
    workers: list[_Worker] = field(default_factory=list)
    stages: list[preview._VolatileStage] = field(default_factory=list)
    resolved: list[ResolvedPdfThumbnailRequest] = field(default_factory=list)
    limits: list[ResourceLimits] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    fail_at: int | None = None
    failure: PdfThumbnailErrorCode = PdfThumbnailErrorCode.WORKER_FAILED
    during_render: Callable[[], None] | None = None

    def render(
        self,
        *,
        reserved: int = 100,
        deadline: DeadlineProbe = lambda: False,
        cancellation: CancellationProbe = lambda: False,
    ) -> tuple[PdfThumbnailResult, PdfThumbnailResult]:
        with io.BytesIO(b"authenticated synthetic candidate") as stream:
            result = self.renderer.render_comparison(
                self.snapshot,
                self.change,
                cast(BinaryIO, stream),
                self.report,
                reserved_staged_bytes=reserved,
                maximum_width_pixels=1600,
                maximum_height_pixels=2000,
                deadline=deadline,
                cancellation=cancellation,
            )
            assert not stream.closed  # The transaction retains stream ownership.
            return result


def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> _Harness:
    snapshot = _snapshot()
    sources = _Sources(snapshot)
    config = _config(tmp_path)
    config = replace(
        config,
        thumbnail_policy=PdfThumbnailBrokerPolicy(
            replace(
                config.thumbnail_policy.limits,
                output_bytes=PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
                + 4 * policy.maximum_preview_pixels_per_page,
                temporary_storage_bytes=policy.maximum_temporary_storage_bytes,
            )
        ),
    )
    heavy = HeavyOperationCoordinator(1)
    harness = _Harness(
        create_compression_preview_renderer(config, sources, policy, heavy_operations=heavy),
        sources,
        snapshot,
        replace(compression_review().affected_pages[0], page_id=PAGE_A1),
        PdfExportVerificationReport.for_verified_plan(
            sources.plan, output_sha256="e" * 64, output_byte_size=100
        ),
        heavy,
    )

    def worker(*args: object, **kwargs: object) -> _Worker:
        assert not harness.workers or harness.workers[-1].terminated
        value = _Worker(harness.events, **kwargs)
        harness.workers.append(value)
        return value

    def stage(cache: preview._VolatileCache, key: PdfThumbnailCacheKey) -> preview._VolatileStage:
        value = preview._VolatileStage(key)
        harness.stages.append(value)
        return value

    def run(
        generator: BrokeredPdfThumbnailGenerator,
        resolved: ResolvedPdfThumbnailRequest,
        limits: ResourceLimits,
        *,
        source_handle_value: int,
        cache_object_id: UUID,
        operation_id: UUID,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> tuple[CaseObjectReference, bytes]:
        assert harness.heavy.snapshot().active_operations == 1
        assert source_handle_value == (101 if not harness.resolved else 202)
        harness.resolved.append(resolved)
        harness.limits.append(limits)
        if harness.during_render is not None:
            harness.during_render()
        if harness.fail_at == len(harness.resolved):
            reject_pdf_thumbnail(harness.failure, "thumbnail.worker")
        artifact = _artifact(resolved.cache_key, operation_id, cache_object_id)
        encoded = encode_pdf_thumbnail_artifact(artifact.header, artifact.payload)
        return CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            cache_object_id,
            operation_id,
            PDF_THUMBNAIL_CONTENT_KIND,
            len(encoded),
            hashlib.sha256(encoded).hexdigest(),
        ), encoded

    monkeypatch.setattr(preview, "WorkerClient", worker)
    monkeypatch.setattr(preview, "WindowsProcessLauncher", lambda _config: object())
    monkeypatch.setattr(preview, "_stream_handle", lambda _source: 202)
    monkeypatch.setattr(
        preview,
        "drain_windows_bounded_output_cleanup",
        lambda: harness.events.append("output.cleanup"),
    )
    monkeypatch.setattr(
        preview, "drain_windows_result_pipe_cleanup", lambda: harness.events.append("pipe.cleanup")
    )
    monkeypatch.setattr(preview._VolatileCache, "create_stage", stage)
    monkeypatch.setattr(BrokeredPdfThumbnailGenerator, "_run_worker", run)
    return harness


@pytest.mark.parametrize("policy", [NORMAL_RESOURCE_POLICY_V1, CONSTRAINED_RESOURCE_POLICY_V1])
def test_comparison_binds_sources_rotation_and_releases_each_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: ResourcePolicy
) -> None:
    harness = _harness(tmp_path, monkeypatch, policy)
    original, proposed = harness.render(reserved=10_000)
    assert original.request.additional_rotation is PdfRenderRotation.DEGREES_270
    assert proposed.request.additional_rotation is PdfRenderRotation.DEGREES_0
    assert harness.resolved[0].source_sha256 == "a" * 64
    assert harness.resolved[0].source_page_index == 1
    assert harness.resolved[1].source_sha256 == harness.report.output_sha256
    assert harness.resolved[1].source_byte_size == harness.report.output_byte_size
    assert harness.resolved[1].source_page_index == harness.change.page_index
    assert harness.resolved[1].source_page_count == harness.report.page_count
    for limits in harness.limits:
        assert limits.temporary_storage_bytes + 10_000 <= policy.maximum_temporary_storage_bytes
        assert limits.pixel_count <= min(
            policy.maximum_preview_pixels_per_page, policy.thumbnail_preview_cache_bytes // 32
        )
    assert original.request.maximum_width_pixels <= 1600
    assert original.request.maximum_height_pixels <= 2000
    assert harness.events == ["create", "terminate", "output.cleanup", "pipe.cleanup"] * 2
    assert harness.sources.events == ["borrow", "close"]
    assert all(
        stage._closed and stage._payload is None and stage._artifact is None
        for stage in harness.stages
    )
    assert harness.heavy.snapshot().active_operations == 0
    assert not any(
        isinstance(value, (preview._PageLease, _Worker, PdfThumbnailResult))
        for value in vars(harness.renderer).values()
    )


@pytest.mark.parametrize("at", [1, 2])
@pytest.mark.parametrize(
    "code",
    [
        PdfThumbnailErrorCode.CANCELLED,
        PdfThumbnailErrorCode.TIMED_OUT,
        PdfThumbnailErrorCode.LIMIT_EXCEEDED,
    ],
)
def test_failed_render_releases_worker_source_cache_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int, code: PdfThumbnailErrorCode
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.fail_at, harness.failure = at, code
    with pytest.raises(PdfThumbnailError) as raised:
        harness.render()
    assert raised.value.code is code
    assert len(harness.workers) == at
    assert all(worker.terminated for worker in harness.workers)
    assert harness.sources.events == ["borrow", "close"]
    assert all(stage._closed and stage._payload is None for stage in harness.stages)
    assert harness.heavy.snapshot().active_operations == 0
    assert not harness.renderer._operation.locked()


def test_changed_original_is_rejected_and_lease_closed_before_worker_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.sources.stale = True
    with pytest.raises(PdfThumbnailError) as raised:
        harness.render()
    assert raised.value.code is PdfThumbnailErrorCode.SOURCE_INVALID
    assert not harness.resolved
    assert harness.sources.events == ["borrow", "close"]
    assert harness.workers[0].terminated


@pytest.mark.parametrize(
    "reserved",
    [
        True,
        0,
        99,
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes,
        CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes - 100,
    ],
)
def test_invalid_or_exhausted_stage_reservation_fails_before_any_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reserved: int
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    with pytest.raises(PdfThumbnailError):
        harness.render(reserved=reserved)
    assert not harness.workers and not harness.sources.events


@pytest.mark.parametrize(
    "deadline,cancellation,code",
    [
        (lambda: True, lambda: False, PdfThumbnailErrorCode.TIMED_OUT),
        (lambda: False, lambda: True, PdfThumbnailErrorCode.CANCELLED),
        (lambda: 1, lambda: False, PdfThumbnailErrorCode.MODEL_INVALID),
    ],
)
def test_cancel_deadline_and_invalid_probe_fail_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    code: PdfThumbnailErrorCode,
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    with pytest.raises(PdfThumbnailError) as raised:
        harness.render(deadline=deadline, cancellation=cancellation)
    assert raised.value.code is code
    assert not harness.workers


def test_renderer_refuses_overlapping_comparisons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)

    def overlapping() -> None:
        with pytest.raises(PdfThumbnailError) as raised:
            harness.render()
        assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED

    harness.during_render = overlapping
    harness.render()
    assert len(harness.workers) == 2


def test_volatile_stage_authenticates_bytes_and_clears_failure_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.render()
    key = harness.resolved[0].cache_key
    stage = preview._VolatileStage(key)
    with stage, pytest.raises(PdfThumbnailError):
        artifact = _artifact(key, uuid4(), stage.cache_object_id)
        stage.write_artifact(encode_pdf_thumbnail_artifact(artifact.header, artifact.payload))
        stage.validate(expected_operation_id=uuid4())
    assert stage._payload is None and stage._artifact is None
    assert not preview._VolatileCache().lookup(key).is_hit
    with preview._VolatileStage(key) as oversized, pytest.raises(PdfThumbnailError):
        oversized.write_artifact(b"x" * (oversized.maximum_bytes + 1))


def test_proposed_stream_requires_read_only_open_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.bin"
    candidate.write_bytes(b"synthetic")
    with candidate.open("r+b") as writable, pytest.raises(PdfThumbnailError):
        preview._stream_handle(writable)
    with candidate.open("rb") as readable:
        if os.name == "nt":
            assert preview._stream_handle(readable) > 0
        else:
            with pytest.raises(PdfThumbnailError):
                preview._stream_handle(readable)
    with pytest.raises(PdfThumbnailError):
        preview._stream_handle(readable)
