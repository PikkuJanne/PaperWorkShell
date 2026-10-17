"""Constrained native sanitization remains a private, recoverable derivative stage."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
from dataclasses import replace
from pathlib import Path
from threading import Event

import fontTools  # type: ignore[import-untyped]
import pikepdf
import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.source_ingest import CompletedSourceInspection, PreparedSourceSummary
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    create_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.redaction_ocr_composition import create_redaction_ocr_worker_composition
from paperworkshell.worker.redaction_sanitization_composition import (
    create_sanitization_worker_composition,
)
from tests.integration.test_ocr_worker_windows import _runtime_from_environment
from tests.integration.test_pdf_export_worker_windows import (
    _CASE_ID,
    _NOW,
    _broker_policy,
    _import_limits,
    _PdfInspector,
    _qpdf_executable,
    _SequentialUuidFactory,
    _stage_pdf_engines,
    _workspace_quota,
)
from tests.integration.test_redaction_ocr_worker_windows import (
    _SCENARIOS,
    _assert_budgets,
    _assert_search,
    _case,
    _payload,
    _request,
    _source,
)
from tests.integration.test_redaction_ocr_worker_windows import (
    _config as _ocr_config,
)
from tests.security.test_redaction_reconstruction import _render, _text
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires the supported Windows AppContainer worker"
)


class _SinglePageInspector(_PdfInspector):
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> CompletedSourceInspection:
        return replace(
            super().inspect(
                source, deadline=deadline, cancellation=cancellation, progress=progress
            ),
            page_count=1,
        )


def _config(worker_test_resources: WorkerTestResources) -> PdfExportWorkerCompositionConfig:
    font_source = Path(fontTools.__file__).resolve().parent
    font_target = worker_test_resources.runtime_root / "Lib" / "site-packages" / "fontTools"
    if not font_target.exists():
        shutil.copytree(
            font_source,
            font_target,
            copy_function=shutil.copyfile,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return create_pdf_export_worker_composition_config(
        python_executable=worker_test_resources.python_executable,
        bootstrap_script=worker_test_resources.source_root
        / "paperworkshell"
        / "worker"
        / "host_bootstrap.py",
        runtime_roots=(worker_test_resources.runtime_root,),
        code_roots=(worker_test_resources.source_root,),
        working_directory=worker_test_resources.source_root,
        qpdf_executable=_qpdf_executable(),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=policy.worker_cpu_time_ms,
            maximum_processes=1,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0, cancellation_grace_seconds=5.0, shutdown_grace_seconds=10.0
        ),
        maximum_heavy_operation_concurrency=1,
        export_limits=replace(
            _broker_policy().limits,
            pixel_count=policy.worker_process_memory_bytes // 16,
            memory_bytes=policy.worker_process_memory_bytes,
            temporary_storage_bytes=policy.maximum_temporary_storage_bytes,
            output_bytes=min(
                policy.maximum_source_bytes, policy.maximum_temporary_storage_bytes // 4
            ),
        ),
        pump_interval_seconds=0.01,
    )


def test_native_private_sanitization_cancel_timeout_limits_termination_and_recovery(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    _stage_pdf_engines(worker_test_resources)
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile, quota=_workspace_quota(), id_factory=lambda: _CASE_ID
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x60500000),
        temp_id_factory=_SequentialUuidFactory(0x60510000),
    )
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "synthetic"
        / "v1"
        / "pdf"
        / "redaction-metadata.pdf"
    )
    # A bounded graph keeps genuine document work active while cancellation
    # traverses the native protocol; a tiny rewrite can finish in one IPC pump.
    synthetic = tmp_path / "synthetic-graph.pdf"
    with pikepdf.Pdf.open(fixture) as document:
        fonts = document.pages[0].Resources.Font
        font = next(item for _, item in fonts.items())
        for index in range(20_000):
            fonts[f"/Pws{index}"] = font
        document.save(synthetic)
    imported = SourceIngestService(sources, _SinglePageInspector()).import_source(
        _CASE_ID,
        synthetic,
        "PRIVATE-SANITIZATION-CANARY.pdf",
    )
    layout = manager.layout(_CASE_ID)
    immutable = layout.originals / f"{imported.source_id}.bin"
    original = immutable.read_bytes()
    manifest = layout.manifest.read_bytes()
    before = {item.relative_to(layout.case_root) for item in layout.case_root.rglob("*")}
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    config = _config(worker_test_resources)
    with sources.borrow_committed_pdf_page(
        _CASE_ID, imported.page_ids[0], cancellation=lambda: False, deadline=lambda: False
    ) as source:
        request = RedactionSanitizationRequest(
            case_id=_CASE_ID,
            source_id=source.source_id,
            source_sha256=source.source_sha256,
            source_byte_size=source.source_byte_size,
            source_page_count=source.source_page_count,
        )
        with create_sanitization_worker_composition(
            config, heavy_operations=HeavyOperationCoordinator(1)
        ) as runtime:
            assert runtime._worker._launcher._config.qpdf_executable is None  # type: ignore[attr-defined]
            for failure in ("cancel", "timeout", "terminate"):
                signal = Event()

                def progress(
                    _value: object, selected_signal: Event = signal, selected_failure: str = failure
                ) -> None:
                    selected_signal.set()
                    if selected_failure == "terminate":
                        runtime._worker.terminate()

                with pytest.raises(PdfExportError) as failed:
                    runtime.sanitizer.sanitize(
                        request,
                        source,
                        cancellation=(signal.is_set if failure == "cancel" else lambda: False),
                        deadline=(signal.is_set if failure == "timeout" else lambda: False),
                        progress=progress,
                    )
                assert signal.is_set()
                if failure in {"cancel", "timeout"}:
                    assert failed.value.code is (
                        PdfExportErrorCode.CANCELLED
                        if failure == "cancel"
                        else PdfExportErrorCode.TIMED_OUT
                    )
                if failure == "cancel":
                    acknowledgement = runtime.last_cancellation_acknowledgement_ms()
                    assert (
                        acknowledgement is not None
                        and acknowledgement <= policy.cancellation_acknowledgement_ms
                    )
                assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
            original_policy = runtime.sanitizer._policy
            runtime.sanitizer._policy = replace(
                original_policy, limits=replace(original_policy.limits, output_bytes=1)
            )
            with pytest.raises(PdfExportError) as exhausted:
                runtime.sanitizer.sanitize(
                    request, source, cancellation=lambda: False, deadline=lambda: False
                )
            assert exhausted.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
            runtime.sanitizer._policy = original_policy
            with runtime.sanitizer.sanitize(
                request, source, cancellation=lambda: False, deadline=lambda: False
            ) as candidate:
                chunks: list[bytes] = []
                while chunk := candidate.read(65536):
                    chunks.append(chunk)
                payload = b"".join(chunks)
                assert hashlib.sha256(payload).hexdigest() == candidate.report.output_sha256
                assert candidate.report.status == "awaiting_verification"
                assert candidate.report.unsigned_derivative
                with pikepdf.Pdf.open(io.BytesIO(payload), attempt_recovery=False) as result:
                    assert len(result.pages) == 1
                    assert candidate.report.before.metadata > 0
                    assert candidate.report.after.empty
                    assert not result.docinfo
                    assert "/Prev" not in result.trailer
                    assert "/Annots" not in result.pages[0].obj
                    assert "/AcroForm" not in result.Root
            assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
            resource = runtime.resource_snapshot()
            assert (
                resource is not None
                and resource.peak_process_memory_used_bytes <= policy.worker_process_memory_bytes
                and resource.peak_job_memory_used_bytes <= policy.worker_job_memory_bytes
            )
            temporary = runtime.private_temp_snapshot()
            assert (
                temporary is not None
                and temporary.measurement_complete
                and temporary.sample_count > 0
                and temporary.sampling_interval_ms == policy.resource_sampling_interval_ms
                and temporary.sampled_peak_bytes <= policy.maximum_temporary_storage_bytes
            )
    assert immutable.read_bytes() == original
    assert layout.manifest.read_bytes() == manifest
    assert {item.relative_to(layout.case_root) for item in layout.case_root.rglob("*")} == before


def test_native_sanitization_preserves_real_regenerated_ocr_and_masked_image(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    tesseract, tessdata = _runtime_from_environment()
    ocr_root, sanitized_root = tmp_path / "ocr", tmp_path / "sanitized"
    ocr_root.mkdir()
    sanitized_root.mkdir()
    scenario = _SCENARIOS[0]
    original_case = _case(ocr_root, _source(scenario))
    ocr_config = _ocr_config(worker_test_resources)
    with original_case.sources.borrow_committed_pdf_page(
        _CASE_ID, original_case.page_id, cancellation=lambda: False, deadline=lambda: False
    ) as source:
        with create_redaction_ocr_worker_composition(
            ocr_config,
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
            heavy_operations=HeavyOperationCoordinator(1),
        ) as ocr_runtime:
            with ocr_runtime.processor.regenerate(
                _request(original_case, scenario),
                source,
                cancellation=lambda: False,
                deadline=lambda: False,
            ) as candidate:
                recognized = _payload(candidate.read)
            _assert_budgets(ocr_runtime)
    original_case.assert_unchanged()
    _assert_search(recognized, "PUBLIC", present=True)
    _assert_search(recognized, "SECRET", present=False)
    enriched = io.BytesIO()
    with pikepdf.Pdf.open(io.BytesIO(recognized)) as document:
        document.docinfo["/Title"] = "PWS-SYNTHETIC-SANITIZER-METADATA-CANARY"
        document.save(enriched)
    case = _case(sanitized_root, enriched.getvalue())
    with case.sources.borrow_committed_pdf_page(
        _CASE_ID, case.page_id, cancellation=lambda: False, deadline=lambda: False
    ) as source:
        request = RedactionSanitizationRequest(
            case_id=_CASE_ID,
            source_id=source.source_id,
            source_sha256=source.source_sha256,
            source_byte_size=source.source_byte_size,
            source_page_count=source.source_page_count,
        )
        with create_sanitization_worker_composition(
            _config(worker_test_resources), heavy_operations=HeavyOperationCoordinator(1)
        ) as sanitizer_runtime:
            with sanitizer_runtime.sanitizer.sanitize(
                request, source, cancellation=lambda: False, deadline=lambda: False
            ) as candidate:
                final = _payload(candidate.read)
                assert candidate.report.before.metadata > 0
                assert candidate.report.after.empty
                assert candidate.report.unsigned_derivative
                assert candidate.report.status == "awaiting_verification"
            policy = CONSTRAINED_RESOURCE_POLICY_V1
            resource = sanitizer_runtime.resource_snapshot()
            assert resource is not None
            assert resource.peak_process_memory_used_bytes <= policy.worker_process_memory_bytes
            temporary = sanitizer_runtime.private_temp_snapshot()
            assert temporary is not None and temporary.measurement_complete
            assert temporary.sampled_peak_bytes <= policy.maximum_temporary_storage_bytes
    case.assert_unchanged()
    assert _text(final) == _text(recognized)
    _assert_search(final, "PUBLIC", present=True)
    _assert_search(final, "SECRET", present=False)
    before_image, after_image = _render(recognized, 0), _render(final, 0)
    try:
        assert before_image.size == after_image.size
        assert before_image.tobytes() == after_image.tobytes()
    finally:
        before_image.close()
        after_image.close()
    assert b"PWS-SYNTHETIC-SANITIZER-METADATA-CANARY" not in final
