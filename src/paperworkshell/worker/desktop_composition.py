"""Owner-thread composition for the desktop document-worker services."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsProcessLauncher,
)
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    reject_client,
)
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.pdf_source_inspector import (
    BrokeredPdfSourceInspector,
    PdfInspectionBrokerPolicy,
    PreparedSourceHandleBroker,
)
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    CommittedPdfPageStore,
    PdfThumbnailBrokerPolicy,
    ThumbnailCacheStore,
)
from paperworkshell.worker.protocol.models import (
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    ResourceLimits,
)

_REQUIRED_DESKTOP_CAPABILITIES = (
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
)


@dataclass(frozen=True, slots=True, repr=False)
class DesktopWorkerCompositionConfig:
    """Validated fixed resources and policies for the shared desktop worker client."""

    python_executable: Path = field(repr=False)
    bootstrap_script: Path = field(repr=False)
    runtime_roots: tuple[Path, ...] = field(repr=False)
    code_roots: tuple[Path, ...] = field(repr=False)
    working_directory: Path = field(repr=False)
    resource_policy: WorkerResourcePolicy
    timeout_policy: WorkerTimeoutPolicy
    inspection_policy: PdfInspectionBrokerPolicy
    thumbnail_policy: PdfThumbnailBrokerPolicy
    venv_launcher: Path | None = field(default=None, repr=False)
    _launch_config: WindowsLaunchConfig = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.runtime_roots) is not tuple
            or type(self.code_roots) is not tuple
            or type(self.resource_policy) is not WorkerResourcePolicy
            or type(self.timeout_policy) is not WorkerTimeoutPolicy
            or type(self.inspection_policy) is not PdfInspectionBrokerPolicy
            or type(self.thumbnail_policy) is not PdfThumbnailBrokerPolicy
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        launch_config = WindowsLaunchConfig(
            python_executable=self.python_executable,
            bootstrap_script=self.bootstrap_script,
            runtime_roots=self.runtime_roots,
            code_roots=self.code_roots,
            working_directory=self.working_directory,
            venv_launcher=self.venv_launcher,
        )
        object.__setattr__(self, "_launch_config", launch_config)

    def __repr__(self) -> str:
        return "DesktopWorkerCompositionConfig(<validated fixed resources>)"


class DesktopWorkerComposition(AbstractContextManager["DesktopWorkerComposition"]):
    """Own one shared worker client and its structural inspection/rendering ports."""

    __slots__ = (
        "_closed",
        "_owner_thread_id",
        "_source_inspector",
        "_thumbnail_generator",
        "_worker",
    )

    def __init__(
        self,
        worker: WorkerClient,
        source_inspector: BrokeredPdfSourceInspector,
        thumbnail_generator: BrokeredPdfThumbnailGenerator,
    ) -> None:
        self._worker = worker
        self._source_inspector = source_inspector
        self._thumbnail_generator = thumbnail_generator
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    @property
    def source_inspector(self) -> BrokeredPdfSourceInspector:
        """Expose the source-inspection application port without launcher details."""

        return self._source_inspector

    @property
    def thumbnail_generator(self) -> BrokeredPdfThumbnailGenerator:
        """Expose the page-thumbnail application port without launcher details."""

        return self._thumbnail_generator

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Idempotently terminate the shared client on its construction thread."""

        self._require_owner()
        if self._closed:
            return
        self._worker.terminate()
        self._closed = True

    def __enter__(self) -> Self:
        self._require_owner()
        if self._closed:
            reject_client(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DesktopWorkerComposition(closed={self._closed})"

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )


def create_desktop_worker_composition(
    config: DesktopWorkerCompositionConfig,
    *,
    prepared_sources: PreparedSourceHandleBroker,
    committed_pages: CommittedPdfPageStore,
    thumbnail_cache: ThumbnailCacheStore,
) -> DesktopWorkerComposition:
    """Build the fixed launcher, shared client, and both desktop broker ports."""

    if type(config) is not DesktopWorkerCompositionConfig:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )
    _require_port(prepared_sources, ("borrow_prepared_source",))
    _require_port(committed_pages, ("borrow_committed_pdf_page",))
    _require_port(thumbnail_cache, ("lookup", "create_stage"))

    launcher = WindowsProcessLauncher(config._launch_config)
    worker = WorkerClient(
        launcher,
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=_REQUIRED_DESKTOP_CAPABILITIES,
    )
    inspector = BrokeredPdfSourceInspector(
        worker,
        prepared_sources,
        config.inspection_policy,
    )
    generator = BrokeredPdfThumbnailGenerator(
        worker,
        committed_pages,
        thumbnail_cache,
        config.thumbnail_policy,
    )
    return DesktopWorkerComposition(worker, inspector, generator)


def create_desktop_worker_composition_config(
    *,
    python_executable: Path,
    bootstrap_script: Path,
    runtime_roots: tuple[Path, ...],
    code_roots: tuple[Path, ...],
    working_directory: Path,
    resource_policy: WorkerResourcePolicy,
    timeout_policy: WorkerTimeoutPolicy,
    inspection_limits: ResourceLimits,
    thumbnail_limits: ResourceLimits,
    venv_launcher: Path | None = None,
) -> DesktopWorkerCompositionConfig:
    """Build broker policies behind the sole desktop-worker composition boundary."""

    return DesktopWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        resource_policy=resource_policy,
        timeout_policy=timeout_policy,
        inspection_policy=PdfInspectionBrokerPolicy(inspection_limits),
        thumbnail_policy=PdfThumbnailBrokerPolicy(thumbnail_limits),
        venv_launcher=venv_launcher,
    )


def _require_port(value: object, methods: tuple[str, ...]) -> None:
    try:
        valid = all(callable(getattr(value, method, None)) for method in methods)
    except BaseException:
        valid = False
    if not valid:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )
