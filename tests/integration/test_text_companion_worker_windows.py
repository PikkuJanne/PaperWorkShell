"""PWS-0706 final-derivative extraction in a real network-denied worker."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.text_companion import TextCompanionError
from paperworkshell.export.safe_zip import SafeZipMember, SafeZipPolicy, write_safe_zip
from paperworkshell.storage._windows import open_handle
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import _REQUIRED_DESKTOP_CAPABILITIES
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.text_companion_broker import BrokeredTextCompanionExtractor
from tests.integration.test_pdf_thumbnail_worker_windows import _stage_pdf_engines
from tests.unit.test_text_companion import _cropped_pdf, _pdf
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Final PDF text uses the supported Windows AppContainer worker"
)


def _worker(resources: WorkerTestResources) -> WorkerClient:
    _stage_pdf_engines(resources)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return WorkerClient(
        WindowsProcessLauncher(
            WindowsLaunchConfig(
                python_executable=resources.python_executable,
                bootstrap_script=resources.source_root / "paperworkshell/worker/host_bootstrap.py",
                runtime_roots=(resources.runtime_root,),
                code_roots=(resources.source_root,),
                working_directory=resources.source_root,
            )
        ),
        WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=policy.worker_cpu_time_ms,
            maximum_processes=1,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=30.0, cancellation_grace_seconds=5.0, shutdown_grace_seconds=10.0
        ),
        required_capabilities=_REQUIRED_DESKTOP_CAPABILITIES,
    )


@pytest.mark.parametrize("archive", (False, True))
def test_real_worker_extracts_only_verified_pdf_text_and_terminates(
    tmp_path: Path, worker_test_resources: WorkerTestResources, archive: bool
) -> None:
    pdf = _cropped_pdf(90)
    pdf_digest = hashlib.sha256(pdf).hexdigest()
    payload = pdf
    if archive:
        stream = BytesIO()
        write_safe_zip(
            stream,
            (SafeZipMember("packet.pdf", BytesIO(pdf), len(pdf), pdf_digest),),
            policy=SafeZipPolicy.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        payload = stream.getvalue()
    path = tmp_path / ("packet.zip" if archive else "packet.pdf")
    path.write_bytes(payload)
    worker = _worker(worker_test_resources)
    heavy = HeavyOperationCoordinator(1)
    broker = BrokeredTextCompanionExtractor(
        worker, CONSTRAINED_RESOURCE_POLICY_V1, heavy_operations=heavy
    )
    try:
        with open_handle(path, read=True) as source:
            result = broker.extract_handle(
                uuid4(),
                source.value,
                expected_byte_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                page_count=1,
                deadline=lambda: False,
                cancellation=lambda: False,
                member_position=0 if archive else None,
                member_byte_size=len(pdf) if archive else None,
                member_sha256=pdf_digest if archive else None,
            )
        assert b"PWSKEEPVISIBLE" in result
        assert b"PWSCROPPEDHIDDEN" not in result
        assert b"PWSPARTIAL" not in result
        assert b"PRIVATE" not in result
        assert worker.query().phase is WorkerLifecyclePhase.STOPPED
        assert worker.query().last_result is not None
        assert heavy.snapshot().active_operations == 0
        assert path.read_bytes() == payload
    finally:
        worker.terminate()


def test_changed_final_derivative_cannot_fall_back_to_an_original(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    payload = _pdf()
    path = tmp_path / "packet.pdf"
    path.write_bytes(payload)
    worker = _worker(worker_test_resources)
    broker = BrokeredTextCompanionExtractor(
        worker, CONSTRAINED_RESOURCE_POLICY_V1, heavy_operations=HeavyOperationCoordinator(1)
    )
    try:
        with open_handle(path, read=True) as source, pytest.raises(TextCompanionError) as caught:
            broker.extract_handle(
                uuid4(),
                source.value,
                expected_byte_size=len(payload),
                expected_sha256="a" * 64,
                page_count=1,
                deadline=lambda: False,
                cancellation=lambda: False,
            )
        assert "PRIVATE" not in repr(caught.value) + str(caught.value) + repr(caught.value.args)
        assert worker.query().phase is WorkerLifecyclePhase.STOPPED
        assert worker.query().last_result is not None
    finally:
        worker.terminate()
