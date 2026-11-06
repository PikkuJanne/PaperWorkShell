"""Actual reviewed source, restricted runtime checks and no-replace publication."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pikepdf
import pytest

from paperworkshell.app.redaction_export import RedactionExportService, ReviewedRedactionExport
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import (
    CropPageCommand,
    PageCommandHistory,
    RotatePageCommand,
)
from paperworkshell.domain.redaction import RedactionMarkCollection
from paperworkshell.domain.redaction_export import (
    RUNTIME_EXPORT_REPORT_VERSION,
    RedactionExportCode,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_verification import (
    VerificationStatus,
    runtime_release_qualified,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.redaction_export_composition import (
    create_redaction_export_worker_composition,
)
from tests.integration.test_ocr_worker_windows import _runtime_from_environment
from tests.integration.test_pdf_export_worker_windows import _CASE_ID
from tests.integration.test_redaction_ocr_worker_windows import _case, _config
from tests.security.test_redaction_reconstruction import _FIXTURES, _render, _request
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows AppContainer")


@pytest.mark.parametrize("transformed", [False, True], ids=("plain", "crop-quarter-turn"))
def test_real_runtime_checks_and_destination_publish_then_preserve_previous_output(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
    transformed: bool,
) -> None:
    # Exercise the actual independently qualified build without a gate override.
    assert runtime_release_qualified()
    original = _FIXTURES["pdf-redaction-born-digital"].content
    case = _case(tmp_path, original)
    repository = WindowsManifestRepository(case.manager)
    initial = repository.load(_CASE_ID).snapshot
    marks = tuple(
        replace(mark, page_id=case.page_id) for mark in _request("pdf-redaction-born-digital").marks
    )
    history = PageCommandHistory.from_snapshot(initial)
    if transformed:
        history = history.execute(
            CropPageCommand(case.page_id, NormalizedPageBox(20_000, 20_000, 960_000, 960_000))
        ).execute(RotatePageCommand(case.page_id, 1))
    document = RedactionMarkCollection(marks).to_manifest_document(history.to_candidate(initial))
    document["revision"] = initial.revision + 1
    document["updated_at"] = "2026-11-01T12:00:00Z"
    snapshot = repository.save(
        _CASE_ID,
        document,
        expected_revision=initial.revision,
        expected_sha256=initial.sha256,
        expected_cas_token=initial.cas_token,
    ).snapshot
    layout = case.manager.layout(_CASE_ID)
    case = replace(
        case,
        manifest=layout.manifest.read_bytes(),
        entries=frozenset(
            path.relative_to(layout.case_root) for path in layout.case_root.rglob("*")
        ),
    )
    review = ReviewedRedactionExport.from_snapshot(snapshot)
    executable, tessdata = _runtime_from_environment()
    base_config = _config(worker_test_resources)
    config = replace(
        base_config,
        timeout_policy=replace(base_config.timeout_policy, handshake_seconds=40.0),
    )
    with create_redaction_export_worker_composition(
        config,
        heavy_operations=HeavyOperationCoordinator(1),
        tesseract_executable=executable,
        tessdata_root=tessdata,
    ) as runtime:
        service = RedactionExportService(
            repository, case.sources, runtime.exporter, WindowsVerifiedPdfDestination()
        )
        target = tmp_path / "unsigned-removal-derivative.pdf"
        result = service.export(review, target, deadline=lambda: False, cancellation=lambda: False)
        assert result.code is RedactionExportCode.COMPLETE, result.to_audit_record()
        assert result.status is RedactionExportStatus.COMMITTED
        assert result.report_version == RUNTIME_EXPORT_REPORT_VERSION
        assert result.verification is not None and result.verification.runtime_ready
        assert result.methods[-1].status is VerificationStatus.UNKNOWN
        before = target.read_bytes()
        digest = hashlib.sha256(before).hexdigest()
        with pikepdf.Pdf.open(target, attempt_recovery=False) as output:
            assert len(output.pages) == 1 and len(output.docinfo) == 0
            assert "/AcroForm" not in output.Root
            assert "/Annots" not in output.pages[0].obj
            assert len(output.pages[0].Resources.XObject) == 1
        rendered = _render(before, 0)
        try:
            # Independent normalized crop/quarter-turn mapping, restricted to
            # the comfortable interior of the reviewed mask (away from rounding).
            mark = marks[0].box
            points = []
            for u, v in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                x = (mark.x + u * mark.width) / 1_000_000
                y = (mark.y + v * mark.height) / 1_000_000
                if transformed:
                    x, y = (x - 0.02) / 0.96, (y - 0.02) / 0.96
                    x, y = 1 - y, x
                points.append((x * rendered.width, y * rendered.height))
            interior = rendered.crop(
                (
                    int(min(x for x, _ in points)),
                    int(min(y for _, y in points)),
                    int(max(x for x, _ in points)),
                    int(max(y for _, y in points)),
                )
            )
            try:
                assert interior.width > 0 and interior.height > 0
                assert interior.getextrema() == ((0, 0), (0, 0), (0, 0))
            finally:
                interior.close()
        finally:
            rendered.close()
        retry = service.export(review, target, deadline=lambda: False, cancellation=lambda: False)
        assert retry.code is RedactionExportCode.DESTINATION_EXISTS
        assert (
            target.read_bytes() == before
            and hashlib.sha256(target.read_bytes()).hexdigest() == digest
        )
        temporary = runtime.private_temp_snapshot()
        assert temporary is not None and temporary.measurement_complete
        assert (
            temporary.sampled_peak_bytes
            <= CONSTRAINED_RESOURCE_POLICY_V1.maximum_temporary_storage_bytes
        )
    case.assert_unchanged()
