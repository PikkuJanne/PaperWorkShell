"""Live PWS-0209 evidence for the content-free OCR-review AppContainer."""

from __future__ import annotations

import hashlib
import os
import shutil
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, ImageDraw, ImageFont

from paperworkshell.app.ocr import built_in_ocr_language_availability
from paperworkshell.app.quality_ocr_review import QualityOcrReviewService
from paperworkshell.domain.quality_analysis import (
    OcrReviewFact,
    OcrReviewStatus,
    QualityPageKind,
    QualityPageSample,
)
from paperworkshell.domain.quality_ocr_review import (
    MAX_QUALITY_OCR_REVIEW_PIXELS,
    MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
    MAX_QUALITY_OCR_REVIEW_WORDS,
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import ResourceLimits
from paperworkshell.worker.quality_ocr_review_composition import (
    QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
    QualityOcrReviewWorkerComposition,
    QualityOcrReviewWorkerCompositionConfig,
    create_quality_ocr_review_worker_composition,
    create_quality_ocr_review_worker_composition_config,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0209 OCR review targets the supported Windows AppContainer",
)

_CASE_ID = UUID("20900000-0000-4000-8000-000000000209")
_PAGE_ID = UUID("20910000-0000-4000-8000-000000000209")
_MANIFEST_REVISION = 9
_SYNTHETIC_TEXT = "PWS 209"
_PRESENTATION_SHA256 = hashlib.sha256(
    b"paperworkshell-pws0209-quality-review-presentation-v1"
).hexdigest()
_QUALITY_OCR_TEMPORARY_BYTES = 4 * 1024 * 1024


class _CancelAfterWorkerStart:
    """Cancel on the first broker probe made after worker.start()."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls >= 3


def _runtime_from_environment() -> tuple[Path, Path]:
    executable = os.environ.get("PWS_TESSERACT_EXECUTABLE")
    tessdata = os.environ.get("PWS_TESSDATA_ROOT")
    if type(executable) is not str or not executable or type(tessdata) is not str or not tessdata:
        pytest.skip("the admitted PWS-0209 Tesseract runtime is not staged")
    return Path(executable), Path(tessdata)


def _stage_quality_ocr_python_closure(resources: WorkerTestResources) -> None:
    """Stage the one locked Python package imported by the Tesseract runner."""

    try:
        package = distribution("packaging")
    except PackageNotFoundError:
        pytest.fail("the locked packaging dependency is unavailable")
    if package.version != "26.2":
        pytest.fail("the locked packaging dependency has the wrong version")
    source_root = Path(str(package.locate_file("packaging"))).resolve()
    destination_root = resources.runtime_root / "Lib" / "site-packages" / "packaging"
    if not source_root.is_dir():
        pytest.fail("the locked packaging dependency has no package tree")
    for source in sorted(source_root.rglob("*"), key=lambda path: path.as_posix().casefold()):
        relative = source.relative_to(source_root)
        if source.name == "__pycache__" or source.suffix.casefold() in {".pyc", ".pyo"}:
            continue
        destination = destination_root / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if not source.is_file():
            pytest.fail("the locked packaging dependency contains a special entry")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            assert destination.is_file()
            assert destination.read_bytes() == source.read_bytes()
        else:
            shutil.copyfile(source, destination)


def _quality_sample() -> QualityPageSample:
    width = 256
    height = 96
    image = Image.new("RGBA", (width, height), "white")
    try:
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 46)
        draw.text((10, 20), _SYNTHETIC_TEXT, fill="black", font=font)
        pixels = image.tobytes()
    finally:
        image.close()
    return QualityPageSample(
        page_id=_PAGE_ID,
        page_position=1,
        page_kind=QualityPageKind.IMAGE,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=width * 4,
        pixels=pixels,
        source_width_pixels=1_600,
        source_height_pixels=600,
    )


def _composition_config(
    resources: WorkerTestResources,
    tesseract: Path,
    tessdata: Path,
) -> QualityOcrReviewWorkerCompositionConfig:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return create_quality_ocr_review_worker_composition_config(
        python_executable=resources.python_executable,
        bootstrap_script=(
            resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py"
        ),
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
        tesseract_executable=tesseract,
        tessdata_root=tessdata,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=policy.worker_cpu_time_ms,
            maximum_processes=QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=policy.cancellation_acknowledgement_ms / 1_000,
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=policy.maximum_heavy_operation_concurrency,
        limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=min(45_000, policy.worker_cpu_time_ms),
            memory_bytes=policy.worker_process_memory_bytes,
            temporary_storage_bytes=min(
                _QUALITY_OCR_TEMPORARY_BYTES,
                policy.maximum_temporary_storage_bytes,
            ),
            output_bytes=MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
            page_count=1,
            pixel_count=MAX_QUALITY_OCR_REVIEW_PIXELS,
        ),
        pump_interval_seconds=0.010,
    )


def _composition(
    resources: WorkerTestResources,
    tesseract: Path,
    tessdata: Path,
) -> QualityOcrReviewWorkerComposition:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return create_quality_ocr_review_worker_composition(
        _composition_config(resources, tesseract, tessdata),
        heavy_operations=HeavyOperationCoordinator(policy.maximum_heavy_operation_concurrency),
    )


def _assert_content_free_success(fact: OcrReviewFact, tesseract: Path) -> None:
    assert fact.page_id == _PAGE_ID
    assert fact.status is OcrReviewStatus.SUCCEEDED
    assert 1 <= fact.recognized_word_count <= MAX_QUALITY_OCR_REVIEW_WORDS
    assert fact.mean_confidence_milli is not None
    assert 0 <= fact.mean_confidence_milli <= 100_000
    assert set(type(fact).__dataclass_fields__) == {
        "page_id",
        "status",
        "recognized_word_count",
        "mean_confidence_milli",
    }
    assert _SYNTHETIC_TEXT.casefold() not in repr(fact).casefold()
    assert os.fspath(tesseract).casefold() not in repr(fact).casefold()


def test_live_quality_ocr_review_returns_counts_and_recovers_after_cancellation(
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_quality_ocr_python_closure(worker_test_resources)
    tesseract, tessdata = _runtime_from_environment()
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    sample = _quality_sample()
    composition = _composition(worker_test_resources, tesseract, tessdata)
    service = QualityOcrReviewService(
        composition.reviewer,
        language_sets=built_in_ocr_language_availability(),
    )

    with composition:
        first_fact = service.review_sample(
            _CASE_ID,
            _MANIFEST_REVISION,
            _PRESENTATION_SHA256,
            sample,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        _assert_content_free_success(first_fact, tesseract)

        cancellation = _CancelAfterWorkerStart()
        with pytest.raises(QualityOcrReviewError) as cancelled:
            service.review_sample(
                _CASE_ID,
                _MANIFEST_REVISION,
                _PRESENTATION_SHA256,
                sample,
                deadline=lambda: False,
                cancellation=cancellation,
            )

        assert cancellation.calls == 3
        assert cancelled.value.code is QualityOcrReviewErrorCode.CANCELLED
        assert cancelled.value.__cause__ is None
        rendered_error = f"{cancelled.value!r} {cancelled.value}".casefold()
        assert _SYNTHETIC_TEXT.casefold() not in rendered_error
        assert os.fspath(tesseract).casefold() not in rendered_error
        assert composition.resource_snapshot() is not None
        after_cancel = composition.heavy_operation_snapshot()
        assert after_cancel.active_operations == 0
        assert after_cancel.waiting_operations == 0

        retried_fact = service.review_sample(
            _CASE_ID,
            _MANIFEST_REVISION,
            _PRESENTATION_SHA256,
            sample,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        resource = composition.resource_snapshot()
        heavy = composition.heavy_operation_snapshot()

    assert composition.closed
    _assert_content_free_success(retried_fact, tesseract)
    assert retried_fact == first_fact
    assert resource is not None
    assert resource.process_memory_limit_bytes == policy.worker_process_memory_bytes
    assert resource.job_memory_limit_bytes == policy.worker_job_memory_bytes
    assert resource.peak_process_memory_used_bytes <= resource.process_memory_limit_bytes
    assert resource.peak_job_memory_used_bytes <= resource.job_memory_limit_bytes
    assert heavy.maximum_concurrency == policy.maximum_heavy_operation_concurrency
    assert heavy.maximum_observed_concurrency == 1
    assert heavy.active_operations == 0
    assert heavy.waiting_operations == 0
