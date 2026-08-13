"""Focused tests for owner-thread desktop worker composition."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.app.page_previews import ImagePagePreviewGenerator
from paperworkshell.app.pdf_thumbnails import ThumbnailPageGenerator
from paperworkshell.app.source_ingest import SourceInspector
from paperworkshell.worker import desktop_composition as composition_module
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.errors import (
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    DesktopWorkerCompositionConfig,
    create_desktop_worker_composition,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_page_broker import ImageNormalizeBrokerPolicy
from paperworkshell.worker.image_transform_broker import ImageTransformBrokerPolicy
from paperworkshell.worker.pdf_source_inspector import (
    PdfInspectionBrokerPolicy,
    PreparedSourceHandleBroker,
)
from paperworkshell.worker.pdf_thumbnail_broker import (
    CommittedPdfPageStore,
    PdfThumbnailBrokerPolicy,
    ThumbnailCacheStore,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    CapabilityDescriptor,
    ResourceLimits,
)


class _PreparedSources:
    def borrow_prepared_source(self, *_args: object, **_kwargs: object) -> None:
        return None


class _CommittedPages:
    def borrow_committed_pdf_page(self, *_args: object, **_kwargs: object) -> None:
        return None

    def borrow_committed_image_page(self, *_args: object, **_kwargs: object) -> None:
        return None


class _ThumbnailCache:
    def lookup(self, *_args: object, **_kwargs: object) -> None:
        return None

    def create_stage(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeWorkerClient:
    def __init__(
        self,
        launcher: object,
        resource_policy: WorkerResourcePolicy,
        timeout_policy: WorkerTimeoutPolicy,
        *,
        required_capabilities: tuple[CapabilityDescriptor, ...],
    ) -> None:
        self.launcher = launcher
        self.resource_policy = resource_policy
        self.timeout_policy = timeout_policy
        self.required_capabilities = required_capabilities
        self.constructed_thread = threading.get_ident()
        self.terminate_calls = 0
        self.resource_snapshot_calls = 0
        self.snapshot = WorkerResourceSnapshot(
            process_memory_limit_bytes=resource_policy.process_memory_bytes,
            job_memory_limit_bytes=resource_policy.job_memory_bytes,
            peak_process_memory_used_bytes=1,
            peak_job_memory_used_bytes=2,
        )

    def terminate(self) -> None:
        self.terminate_calls += 1

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        self.resource_snapshot_calls += 1
        return self.snapshot


def _fixed_resources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runtime_root = tmp_path / "synthetic-runtime"
    code_root = tmp_path / "synthetic-code"
    runtime_root.mkdir()
    code_root.mkdir()
    python_executable = runtime_root / "python.exe"
    bootstrap_script = code_root / "host_bootstrap.py"
    python_executable.write_bytes(b"synthetic executable")
    bootstrap_script.write_text("# synthetic bootstrap\n", encoding="utf-8")
    return runtime_root, code_root, python_executable, bootstrap_script


def _operation_limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=15_000,
        cpu_time_ms=10_000,
        memory_bytes=256 * 1024 * 1024,
        temporary_storage_bytes=64 * 1024 * 1024,
        output_bytes=1024 * 1024,
        page_count=100,
        pixel_count=10_000_000,
    )


def _config(tmp_path: Path) -> DesktopWorkerCompositionConfig:
    runtime_root, code_root, python_executable, bootstrap_script = _fixed_resources(tmp_path)
    return DesktopWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=(runtime_root,),
        code_roots=(code_root,),
        working_directory=code_root,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=1,
        inspection_policy=PdfInspectionBrokerPolicy(_operation_limits()),
        image_policy=ImageNormalizeBrokerPolicy(
            ResourceLimits(
                wall_time_ms=15_000,
                cpu_time_ms=10_000,
                memory_bytes=256 * 1024 * 1024,
                temporary_storage_bytes=40_000_000,
                output_bytes=40_000_256,
                page_count=1,
                pixel_count=10_000_000,
            )
        ),
        image_transform_policy=ImageTransformBrokerPolicy(
            ResourceLimits(
                wall_time_ms=15_000,
                cpu_time_ms=10_000,
                memory_bytes=256 * 1024 * 1024,
                temporary_storage_bytes=128 * 1024 * 1024,
                output_bytes=40_001_024,
                page_count=1,
                pixel_count=10_000_000,
            )
        ),
        thumbnail_policy=PdfThumbnailBrokerPolicy(_operation_limits()),
    )


def _ports() -> tuple[
    PreparedSourceHandleBroker,
    CommittedPdfPageStore,
    ThumbnailCacheStore,
]:
    return (
        cast(PreparedSourceHandleBroker, _PreparedSources()),
        cast(CommittedPdfPageStore, _CommittedPages()),
        cast(ThumbnailCacheStore, _ThumbnailCache()),
    )


def _compose_with_fake_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, _FakeWorkerClient]:
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    prepared, committed, cache = _ports()
    composition = create_desktop_worker_composition(
        _config(tmp_path),
        prepared_sources=prepared,
        committed_pages=committed,
        thumbnail_cache=cache,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    worker = cast(_FakeWorkerClient, cast(Any, composition)._worker)
    return composition, worker


def test_factory_builds_one_shared_capability_scoped_client_and_structural_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)

    source_port: SourceInspector = composition.source_inspector
    thumbnail_port: ThumbnailPageGenerator = composition.thumbnail_generator
    image_port: ImagePagePreviewGenerator = composition.image_page_generator
    assert isinstance(source_port, SourceInspector)
    assert callable(thumbnail_port.get_or_generate)
    assert callable(image_port.get_or_generate)
    assert callable(composition.image_page_transformer.transform)
    assert callable(composition.image_source_inspector.inspect)
    assert isinstance(worker.launcher, WindowsProcessLauncher)
    assert worker.required_capabilities == (
        IMAGE_NORMALIZE_CAPABILITY,
        IMAGE_TRANSFORM_CAPABILITY,
        PDF_INSPECT_CAPABILITY,
        PDF_THUMBNAIL_CAPABILITY,
    )
    assert cast(Any, source_port)._worker is worker
    assert cast(Any, thumbnail_port)._worker is worker
    assert cast(Any, image_port)._worker is worker
    assert cast(Any, composition.image_page_transformer)._worker is worker
    assert cast(Any, source_port)._heavy_operations is cast(Any, thumbnail_port)._heavy_operations
    assert composition.heavy_operation_snapshot().maximum_concurrency == 1
    assert worker.constructed_thread == threading.get_ident()
    assert composition.resource_snapshot() == worker.snapshot
    assert worker.resource_snapshot_calls == 1
    composition.close()


def test_factory_rejects_a_coordinator_whose_limit_differs_from_config(
    tmp_path: Path,
) -> None:
    prepared, committed, cache = _ports()

    with pytest.raises(WorkerClientError) as caught:
        create_desktop_worker_composition(
            _config(tmp_path),
            prepared_sources=prepared,
            committed_pages=committed,
            thumbnail_cache=cache,
            heavy_operations=HeavyOperationCoordinator(2),
        )

    assert caught.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID


def test_close_is_idempotent_and_rejects_a_non_owner_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)
    failures: list[BaseException] = []

    def close_from_other_thread() -> None:
        try:
            composition.close()
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=close_from_other_thread)
    thread.start()
    thread.join()

    assert len(failures) == 1
    assert isinstance(failures[0], WorkerClientError)
    assert failures[0].code is WorkerClientErrorCode.OWNER_THREAD_REQUIRED
    assert not composition.closed
    assert worker.terminate_calls == 0

    composition.close()
    composition.close()
    assert composition.closed
    assert worker.terminate_calls == 1


def test_context_manager_terminates_the_shared_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_composition, worker = _compose_with_fake_client(tmp_path, monkeypatch)
    composition = cast(Any, raw_composition)

    with composition as entered:
        assert entered is composition

    assert composition.closed
    assert worker.terminate_calls == 1


def test_configuration_and_composition_representations_never_disclose_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    prepared, committed, cache = _ports()
    monkeypatch.setattr(composition_module, "WorkerClient", _FakeWorkerClient)
    composition = create_desktop_worker_composition(
        config,
        prepared_sources=prepared,
        committed_pages=committed,
        thumbnail_cache=cache,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    try:
        rendered = f"{config!r} {composition!r}"
        assert str(config.python_executable) not in rendered
        assert str(config.bootstrap_script) not in rendered
        assert str(config.working_directory) not in rendered
        assert "validated fixed resources" in rendered
    finally:
        composition.close()


@pytest.mark.parametrize(
    ("prepared", "committed", "cache"),
    (
        (object(), _CommittedPages(), _ThumbnailCache()),
        (_PreparedSources(), object(), _ThumbnailCache()),
        (_PreparedSources(), _CommittedPages(), object()),
    ),
)
def test_factory_rejects_invalid_structural_ports_with_sanitized_error(
    tmp_path: Path,
    prepared: object,
    committed: object,
    cache: object,
) -> None:
    with pytest.raises(WorkerClientError) as caught:
        create_desktop_worker_composition(
            _config(tmp_path),
            prepared_sources=cast(PreparedSourceHandleBroker, prepared),
            committed_pages=cast(CommittedPdfPageStore, committed),
            thumbnail_cache=cast(ThumbnailCacheStore, cache),
            heavy_operations=HeavyOperationCoordinator(1),
        )

    assert caught.value.code is WorkerClientErrorCode.CONFIGURATION_INVALID
    assert "object" not in str(caught.value).lower()
