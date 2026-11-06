"""Composed native preparation remains bounded, private and retryable."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VerificationStatus,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client.models import WorkerLifecyclePhase
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.redaction_export_composition import (
    create_redaction_export_worker_composition,
)
from tests.integration.test_ocr_worker_windows import _runtime_from_environment
from tests.integration.test_pdf_export_worker_windows import _CASE_ID, _stage_pdf_engines
from tests.integration.test_redaction_ocr_worker_windows import _case
from tests.integration.test_redaction_ocr_worker_windows import _config as _ocr_config
from tests.integration.test_redaction_sanitization_worker_windows import _config
from tests.security.test_redaction_reconstruction import _FIXTURES, _request
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows AppContainer")


@pytest.mark.parametrize("runtime_ocr", [False, True], ids=("legacy-private", "runtime-ocr"))
def test_native_composed_prepare_failure_cleanup_retry_and_resource_limits(
    tmp_path: Path, worker_test_resources: WorkerTestResources, runtime_ocr: bool
) -> None:
    _stage_pdf_engines(worker_test_resources)
    case = _case(tmp_path, _FIXTURES["pdf-redaction-born-digital"].content)
    config = _ocr_config(worker_test_resources) if runtime_ocr else _config(worker_test_resources)
    if runtime_ocr:
        config = replace(
            config, timeout_policy=replace(config.timeout_policy, handshake_seconds=40.0)
        )
    executable, tessdata = _runtime_from_environment() if runtime_ocr else (None, None)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    with case.sources.borrow_committed_pdf_page(
        _CASE_ID, case.page_id, cancellation=lambda: False, deadline=lambda: False
    ) as source:
        base = _request("pdf-redaction-born-digital")
        request = replace(
            base,
            case_id=_CASE_ID,
            source_id=case.source_id,
            page_id=case.page_id,
            marks=tuple(replace(mark, page_id=case.page_id) for mark in base.marks),
        )
        with create_redaction_export_worker_composition(
            config,
            heavy_operations=HeavyOperationCoordinator(1),
            tesseract_executable=executable,
            tessdata_root=tessdata,
        ) as runtime:
            for failure in ("cancel", "timeout", "terminate"):
                signal = Event()

                def progress(
                    _value: object, selected: str = failure, selected_signal: Event = signal
                ) -> None:
                    selected_signal.set()
                    if selected == "terminate":
                        runtime._worker.terminate()

                with pytest.raises(PdfExportError) as failed:
                    runtime.exporter.prepare(
                        request,
                        source,
                        progress=progress,
                        cancellation=signal.is_set if failure == "cancel" else lambda: False,
                        deadline=signal.is_set if failure == "timeout" else lambda: False,
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
            original_policy = runtime.exporter._policy
            runtime.exporter._policy = replace(
                original_policy, limits=replace(original_policy.limits, output_bytes=1)
            )
            with pytest.raises(PdfExportError):
                runtime.exporter.prepare(
                    request, source, cancellation=lambda: False, deadline=lambda: False
                )
            runtime.exporter._policy = original_policy
            with runtime.exporter.prepare(
                request, source, cancellation=lambda: False, deadline=lambda: False
            ) as candidate:
                runtime.exporter.validate_candidate(
                    candidate, request, cancellation=lambda: False, deadline=lambda: False
                )
                digest = hashlib.sha256()
                while chunk := candidate.read(65536):
                    digest.update(chunk)
                assert digest.hexdigest() == candidate.report.output_sha256
                assert candidate.report.verification.automated_passed, [
                    (item.method.value, item.status.value, item.code.value)
                    for item in candidate.report.verification.methods
                ]
                assert not candidate.report.verification.release_ready
                if runtime_ocr:
                    assert (
                        candidate.report.verification_request.profile_id
                        == RUNTIME_VERIFICATION_PROFILE
                    )
                    assert candidate.report.verification.runtime_ready
                    assert (
                        candidate.report.verification.methods[-2].status is VerificationStatus.PASS
                    )
                    assert (
                        candidate.report.verification.methods[-1].status
                        is VerificationStatus.UNKNOWN
                    )
                else:
                    assert all(
                        item.status is VerificationStatus.UNKNOWN
                        for item in candidate.report.verification.methods[-2:]
                    )
            assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
            resource = runtime.resource_snapshot()
            assert (
                resource is not None
                and resource.peak_process_memory_used_bytes <= policy.worker_process_memory_bytes
            )
            assert resource.peak_job_memory_used_bytes <= policy.worker_job_memory_bytes
            temporary = runtime.private_temp_snapshot()
            assert temporary is not None and temporary.measurement_complete
            assert temporary.sample_count > 0
            assert temporary.sampled_peak_bytes <= policy.maximum_temporary_storage_bytes
    case.assert_unchanged()
