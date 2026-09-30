"""JPEG/PNG import, reopen, and verified PDF export with no OCR resources granted."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from paperworkshell.app.export_history import PdfExportHistoryService
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.pdf_export import IMAGE_PDF_EXPORT_PROFILE, PdfExportOutcomeStatus
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import BrokeredPdfExporter
from paperworkshell.worker.protocol.models import IMAGE_PDF_EXPORT_CAPABILITY
from tests.integration.test_image_normalize_worker_windows import _CASE_ID, _live_system
from tests.integration.test_pdf_export_worker_windows import (
    _broker_policy,
    _launch_config,
    _qpdf_executable,
    _stage_pdf_engines,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer is required")


def test_live_images_reopen_and_export_without_ocr_resources(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    manager, sources, import_worker, inspector, _generator, _transformer = _live_system(
        tmp_path, worker_test_resources
    )
    authenticated: dict[Path, str] = {}
    for format in ("JPEG", "PNG"):
        path = tmp_path / f"synthetic-résumé.{format.lower()}"
        image = Image.new("RGB" if format == "JPEG" else "RGBA", (80, 40))
        image.paste((220, 20, 10) if format == "JPEG" else (220, 20, 10, 96), (0, 0, 40, 40))
        image.paste((20, 30, 230) if format == "JPEG" else (20, 30, 230, 255), (40, 0, 80, 40))
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, format=format, exif=exif)
        image.close()
        imported = SourceIngestService(sources, inspector).import_source(
            _CASE_ID, path, f"Synthetic résumé {format}"
        )
        authenticated[path] = imported.sha256
        committed = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
        authenticated[committed] = imported.sha256
    assert import_worker.query().phase is WorkerLifecyclePhase.STOPPED

    reopened = WindowsCaseLifecycleStore(manager).open_case(_CASE_ID)
    reopened.close()
    manifests = WindowsManifestRepository(manager)
    history = WindowsPdfExportHistoryRepository(manager)
    # This launcher grants qpdf and the Python runtime only: no Tesseract,
    # tessdata, OCRmyPDF, or OCR language selection participates in this path.
    worker = WorkerClient(
        WindowsProcessLauncher(_launch_config(worker_test_resources, _qpdf_executable())),
        WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=60_000,
            maximum_processes=2,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(IMAGE_PDF_EXPORT_CAPABILITY,),
    )
    service = PdfExportService(
        manifests,
        sources,
        BrokeredPdfExporter(
            worker, _broker_policy(), heavy_operations=HeavyOperationCoordinator(1)
        ),
        WindowsVerifiedPdfDestination(),
        PdfExportHistoryService(history, manifests),
        WindowsPdfExportRetryRepository(manager),
        profile=IMAGE_PDF_EXPORT_PROFILE,
    )
    destination = tmp_path / "images-without-ocr.pdf"
    result = service.export_pdf(
        _CASE_ID, destination, deadline=lambda: False, cancellation=lambda: False
    )

    assert result.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert len(result.checks) == 7
    assert result.report.page_count == 2
    with pikepdf.Pdf.open(destination, attempt_recovery=False) as output:
        assert len(output.pages) == 2
        for page in output.pages:
            raster = next(value for _key, value in page.Resources.XObject.items())
            assert (int(raster.Width), int(raster.Height)) == (40, 80)
            assert "/Font" not in page.Resources
    assert len(history.load(_CASE_ID).document.records) == 1
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert not tuple(manager.layout(_CASE_ID).temp.iterdir())
    for path, expected_sha256 in authenticated.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
