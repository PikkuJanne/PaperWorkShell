"""Live PWS-0204 proof through the constrained AppContainer OCR worker."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from uuid import UUID

import pytest
from packaging.requirements import Requirement
from PIL import Image, ImageDraw, ImageFont

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    TRUSTED_OCR_ENGINE_PROFILE_SHA256,
    OcrError,
    OcrProcessRequest,
    OcrProgress,
    OcrProgressPhase,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import (
    CommittedSourcePageReadLease,
    WindowsSourceIngestStore,
)
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.ocr_broker import BrokeredOcrProcessor, OcrBrokerPolicy
from paperworkshell.worker.protocol.models import OCR_PROCESS_CAPABILITY, ResourceLimits
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0204 targets the supported Windows AppContainer boundary",
)

_CASE_ID = UUID("20400000-0000-4000-8000-000000000204")
_NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
_PRIVATE_DISPLAY_NAME = "PRIVATE-OCR-FILENAME-CANARY.png"


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


@dataclass(frozen=True, slots=True)
class _ImageInspector:
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> CompletedSourceInspection:
        assert callable(deadline) and not deadline()
        assert callable(cancellation) and not cancellation()
        assert callable(progress)
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NOT_APPLICABLE,
            encryption_state=SourceEncryptionState.NOT_APPLICABLE,
        )


@dataclass(frozen=True, slots=True)
class _OcrImageLease:
    source: CommittedSourcePageReadLease

    @property
    def case_id(self) -> UUID:
        return self.source.case_id

    @property
    def source_id(self) -> UUID:
        return self.source.source_id

    @property
    def source_kind(self) -> OcrSourceKind:
        return OcrSourceKind.PNG

    @property
    def byte_size(self) -> int:
        return self.source.source_byte_size

    @property
    def page_count(self) -> int:
        return self.source.source_page_count

    @property
    def sha256(self) -> str:
        return self.source.source_sha256

    @property
    def signature_state(self) -> OcrSourceSignatureState:
        return OcrSourceSignatureState.NOT_APPLICABLE

    @property
    def encryption_state(self) -> OcrSourceEncryptionState:
        return OcrSourceEncryptionState.NOT_APPLICABLE

    @property
    def handle_value(self) -> int:
        return self.source.handle_value

    def revalidate(self, *, deadline: object, cancellation: object) -> None:
        assert callable(deadline)
        assert callable(cancellation)
        self.source.revalidate(deadline=deadline, cancellation=cancellation)


def _stage_ocr_python_closure(resources: WorkerTestResources) -> None:
    """Copy only OCRmyPDF's locked transitive Python distributions."""

    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    pending = ["ocrmypdf"]
    completed: set[str] = set()
    while pending:
        selected = pending.pop()
        normalized = selected.casefold().replace("_", "-")
        if normalized in completed:
            continue
        try:
            package = distribution(selected)
        except PackageNotFoundError:
            pytest.fail("the locked OCRmyPDF test dependency closure is unavailable")
        completed.add(normalized)
        for raw in package.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate():
                pending.append(requirement.name)
        for entry in package.files or ():
            relative = Path(entry)
            source = Path(str(package.locate_file(entry))).resolve()
            try:
                contained = source.relative_to(Path(str(package.locate_file(""))).resolve())
            except ValueError:
                continue
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not source.is_file()
                or source.suffix.casefold() in {".pyc", ".pyo"}
                or "__pycache__" in contained.parts
            ):
                continue
            destination = destination_root / contained
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                assert destination.is_file()
                assert source.read_bytes() == destination.read_bytes()
            else:
                shutil.copyfile(source, destination)


def _runtime_from_environment() -> tuple[Path, Path]:
    executable = os.environ.get("PWS_TESSERACT_EXECUTABLE")
    tessdata = os.environ.get("PWS_TESSDATA_ROOT")
    if not executable or not tessdata:
        pytest.skip("the admitted PWS-0204 Tesseract runtime is not staged")
    return Path(executable), Path(tessdata)


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=4_000_000,
        maximum_pdf_pages=10,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=16_000_000,
        maximum_decompressed_bytes=64_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


def _write_source(path: Path) -> None:
    image = Image.new("RGB", (1_600, 500), "white")
    try:
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 120)
        draw.text((70, 150), "PAPER WORK SHELL 204", fill="black", font=font)
        image.save(path, format="PNG")
    finally:
        image.close()


def test_live_ocr_worker_emits_verified_volatile_candidate_and_preserves_case(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_ocr_python_closure(worker_test_resources)
    tesseract, tessdata = _runtime_from_environment()
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=64_000_000,
            maximum_cache_bytes=8_000_000,
            maximum_temp_bytes=32_000_000,
            maximum_entries=1_024,
            maximum_depth=16,
        ),
        id_factory=lambda: _CASE_ID,
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=10,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x20410000),
        temp_id_factory=_SequentialUuidFactory(0x20420000),
    )
    external = tmp_path / "synthetic-ocr-source.png"
    _write_source(external)
    external_bytes = external.read_bytes()
    imported = SourceIngestService(sources, _ImageInspector()).import_source(
        _CASE_ID,
        external,
        _PRIVATE_DISPLAY_NAME,
    )
    case_copy = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    manifest = manager.layout(_CASE_ID).manifest
    case_bytes = case_copy.read_bytes()
    manifest_bytes = manifest.read_bytes()

    worker = WorkerClient(
        WindowsProcessLauncher(
            WindowsLaunchConfig(
                python_executable=worker_test_resources.python_executable,
                bootstrap_script=(
                    worker_test_resources.source_root
                    / "paperworkshell"
                    / "worker"
                    / "host_bootstrap.py"
                ),
                runtime_roots=(worker_test_resources.runtime_root,),
                code_roots=(worker_test_resources.source_root,),
                working_directory=worker_test_resources.source_root,
                tesseract_executable=tesseract,
                tessdata_root=tessdata,
            )
        ),
        WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=120_000,
            maximum_processes=2,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(OCR_PROCESS_CAPABILITY,),
    )
    processor = BrokeredOcrProcessor(
        worker,
        OcrBrokerPolicy(
            ResourceLimits(
                wall_time_ms=120_000,
                cpu_time_ms=120_000,
                memory_bytes=512 * 1024 * 1024,
                temporary_storage_bytes=256 * 1024 * 1024,
                output_bytes=16 * 1024 * 1024,
                page_count=1,
                pixel_count=16_000_000,
            ),
            pump_interval_seconds=0.01,
        ),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=_SequentialUuidFactory(0x20430000),
    )
    progress: list[OcrProgress] = []
    with sources.borrow_committed_image_page(
        _CASE_ID,
        imported.page_ids[0],
        cancellation=lambda: False,
        deadline=lambda: False,
    ) as source:
        request = OcrProcessRequest(
            case_id=_CASE_ID,
            source_id=imported.source_id,
            source_kind=OcrSourceKind.PNG,
            source_byte_size=len(case_bytes),
            source_page_count=1,
            source_sha256=hashlib.sha256(case_bytes).hexdigest(),
            signature_state=OcrSourceSignatureState.NOT_APPLICABLE,
            encryption_state=OcrSourceEncryptionState.NOT_APPLICABLE,
            language_set_id="latin-default-v1",
            profile_id=DEFAULT_OCR_PROFILE_ID,
        )
        try:
            candidate_context = processor.process(
                request,
                _OcrImageLease(source),
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=progress.append,
            )
        except OcrError:
            snapshot = worker.query()
            pytest.fail(f"OCR worker failed at {snapshot.phase.value}: {snapshot.last_failure!r}")
        with candidate_context as candidate:
            payload = bytearray()
            while True:
                block = candidate.read(1_048_576)
                if not block:
                    break
                payload.extend(block)
            assert bytes(payload).startswith(b"%PDF-")
            assert hashlib.sha256(payload).hexdigest() == candidate.report.output_sha256
            assert candidate.report.engine_profile_sha256 == TRUSTED_OCR_ENGINE_PROFILE_SHA256
            assert candidate.report.page_count == 1

    assert progress[0].phase is OcrProgressPhase.PREPARING
    assert progress[-1].phase is OcrProgressPhase.COMPLETE
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert external.read_bytes() == external_bytes
    assert case_copy.read_bytes() == case_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert manager.measure_usage(_CASE_ID).temp_bytes == 0
