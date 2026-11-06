"""Pinned native OCR canaries remain absent after private page reconstruction.

PYTEST_DONT_REWRITE

This is private stage evidence. Searchability does not approve permanent
redaction, commit an export, or claim accessibility conformance.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from uuid import UUID

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image, ImageChops, ImageFilter, ImageStat

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.page_coordinates import ClockwiseRotation, NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED, RedactionMark
from paperworkshell.domain.redaction_ocr import RedactionOcrRequest
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionAdapter
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsWorkerProcess
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import admit_packaged_tesseract_runtime
from paperworkshell.worker.desktop_composition import (
    PdfExportWorkerCompositionConfig,
    create_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.redaction_ocr_composition import (
    RedactionOcrWorkerComposition,
    create_redaction_ocr_worker_composition,
)
from tests.integration.test_ocr_worker_windows import (
    _runtime_from_environment,
    _stage_ocr_python_closure,
)
from tests.integration.test_pdf_export_worker_windows import (
    _CASE_ID,
    _NOW,
    _broker_policy,
    _import_limits,
    _qpdf_executable,
    _SequentialUuidFactory,
    _stage_pdf_engines,
    _workspace_quota,
)
from tests.integration.test_redaction_reconstruction_worker_windows import _SinglePageInspector
from tests.security.test_redaction_reconstruction import _Control, _limits, _render, _text
from tests.unit.test_redaction_reconstruction_models import _id
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires the supported Windows AppContainer OCR worker"
)


@dataclass(frozen=True, slots=True)
class _Scenario:
    name: str
    language_set_id: str
    keep: str
    canary: str
    intrinsic_turns: int = 0
    raster: bool = False
    degraded: bool = False
    cropped: bool = False


_SCENARIOS = (
    _Scenario("english-digital", "latin-default-v1", "PUBLIC ENGLISH CONTROL", "ALPHA"),
    _Scenario(
        "german-digital-90-crop",
        "german-fast-v1",
        "SICHTBAR DEUTSCH TEXT",
        "BETA",
        intrinsic_turns=1,
        cropped=True,
    ),
    _Scenario(
        "mixed-digital-180",
        "english-german-fast-v1",
        "VISIBLE SICHTBAR CONTROL",
        "GAMMA",
        intrinsic_turns=2,
    ),
    _Scenario(
        "english-digital-270-crop",
        "latin-default-v1",
        "PUBLIC ENGLISH CONTROL",
        "DELTA",
        intrinsic_turns=3,
        cropped=True,
    ),
    _Scenario(
        "english-scan-crop",
        "latin-default-v1",
        "PUBLIC ENGLISH CONTROL",
        "EPSILON",
        raster=True,
        cropped=True,
    ),
    _Scenario(
        "german-scan-90",
        "german-fast-v1",
        "SICHTBAR DEUTSCH TEXT",
        "ZETA",
        intrinsic_turns=1,
        raster=True,
    ),
    _Scenario(
        "mixed-degraded-180",
        "english-german-fast-v1",
        "VISIBLE SICHTBAR CONTROL",
        "ETA",
        intrinsic_turns=2,
        raster=True,
        degraded=True,
    ),
    _Scenario(
        "english-degraded-270-crop",
        "latin-default-v1",
        "PUBLIC ENGLISH CONTROL",
        "THETA",
        intrinsic_turns=3,
        raster=True,
        degraded=True,
        cropped=True,
    ),
)


def _turn(box: NormalizedPageBox, turns: int) -> NormalizedPageBox:
    left, top = box.x, box.y
    right, bottom = left + box.width, top + box.height
    for _ in range(turns % 4):
        left, top, right, bottom = 1_000_000 - bottom, left, 1_000_000 - top, right
    return NormalizedPageBox.from_edges(left, top, right, bottom)


def _source(scenario: _Scenario) -> bytes:
    """Comfortably large synthetic controls with no original user data."""

    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(384, 288))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=document.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Font,
                        Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name.Helvetica,
                    )
                )
            )
        )
        page.Contents = document.make_stream(
            (
                f"BT /F1 23 Tf 28 205 Td (SECRET CANARY {scenario.canary}) Tj ET\n"
                f"BT /F1 23 Tf 28 95 Td ({scenario.keep}) Tj ET\n"
            ).encode("ascii")
        )
        serialized = io.BytesIO()
        document.save(serialized)
    content = serialized.getvalue()
    if scenario.raster:
        image = _render(content, 0)
        try:
            if scenario.degraded:
                resized = image.resize((800, 600), Image.Resampling.BILINEAR)
                image.close()
                image = resized.filter(ImageFilter.GaussianBlur(0.5))
                resized.close()
            with pikepdf.Pdf.new() as document:
                page = document.add_blank_page(page_size=(384, 288))
                pixels = document.make_stream(zlib.compress(image.tobytes()))
                pixels.Type = pikepdf.Name.XObject
                pixels.Subtype = pikepdf.Name.Image
                pixels.Width, pixels.Height = image.size
                pixels.ColorSpace = pikepdf.Name.DeviceRGB
                pixels.BitsPerComponent = 8
                pixels.Filter = pikepdf.Name.FlateDecode
                page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=pixels))
                page.Contents = document.make_stream(b"q 384 0 0 288 0 0 cm /Im Do Q\n")
                serialized = io.BytesIO()
                document.save(serialized)
            content = serialized.getvalue()
        finally:
            image.close()
    with pikepdf.Pdf.open(io.BytesIO(content)) as document:
        document.pages[0].obj.Rotate = scenario.intrinsic_turns * 90
        serialized = io.BytesIO()
        document.save(serialized)
    return serialized.getvalue()


@dataclass(frozen=True, slots=True)
class _Case:
    manager: CaseWorkspaceManager
    sources: WindowsSourceIngestStore
    source_id: UUID
    page_id: UUID
    external: Path
    original: bytes
    manifest: bytes
    entries: frozenset[Path]

    def assert_unchanged(self) -> None:
        layout = self.manager.layout(_CASE_ID)
        assert self.external.read_bytes() == self.original, "REDACTION_OCR_EXTERNAL_MUTATED"
        assert (layout.originals / f"{self.source_id}.bin").read_bytes() == self.original
        assert layout.manifest.read_bytes() == self.manifest, "REDACTION_OCR_MANIFEST_MUTATED"
        assert frozenset(
            item.relative_to(layout.case_root) for item in layout.case_root.rglob("*")
        ) == (self.entries), "REDACTION_OCR_RETAINED_CASE_ARTIFACT"
        assert self.manager.measure_usage(_CASE_ID).temp_bytes == 0


def _case(tmp_path: Path, original: bytes) -> _Case:
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
        id_factory=_SequentialUuidFactory(0x60400000),
        temp_id_factory=_SequentialUuidFactory(0x60410000),
    )
    external = tmp_path / "synthetic-source.pdf"
    external.write_bytes(original)
    imported = SourceIngestService(sources, _SinglePageInspector()).import_source(
        _CASE_ID, external, "PRIVATE-REDACTION-OCR-FILENAME.pdf"
    )
    layout = manager.layout(_CASE_ID)
    return _Case(
        manager,
        sources,
        imported.source_id,
        imported.page_ids[0],
        external,
        original,
        layout.manifest.read_bytes(),
        frozenset(item.relative_to(layout.case_root) for item in layout.case_root.rglob("*")),
    )


def _config(resources: WorkerTestResources) -> PdfExportWorkerCompositionConfig:
    _stage_pdf_engines(resources)
    _stage_ocr_python_closure(resources)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return create_pdf_export_worker_composition_config(
        python_executable=resources.python_executable,
        bootstrap_script=resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
        qpdf_executable=_qpdf_executable(),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=policy.worker_cpu_time_ms,
            maximum_processes=policy.worker_maximum_processes,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0, cancellation_grace_seconds=5.0, shutdown_grace_seconds=10.0
        ),
        maximum_heavy_operation_concurrency=policy.maximum_heavy_operation_concurrency,
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


def _request(case: _Case, scenario: _Scenario) -> RedactionOcrRequest:
    return RedactionOcrRequest(
        reconstruction=RedactionReconstructionRequest(
            case_id=_CASE_ID,
            manifest_revision=1,
            source_id=case.source_id,
            page_id=case.page_id,
            source_sha256=hashlib.sha256(case.original).hexdigest(),
            source_byte_size=len(case.original),
            source_page_index=0,
            source_page_count=1,
            marks=(
                RedactionMark(
                    _id(604),
                    case.page_id,
                    _turn(
                        NormalizedPageBox(40_000, 190_000, 920_000, 160_000),
                        scenario.intrinsic_turns,
                    ),
                ),
            ),
            crop=(
                NormalizedPageBox(50_000, 50_000, 900_000, 900_000)
                if scenario.cropped
                else NormalizedPageBox.full_page()
            ),
            # Exercise both transforms while leaving the large OCR controls upright.
            rotation=ClockwiseRotation((-90 * scenario.intrinsic_turns) % 360),
        ),
        language_set_id=scenario.language_set_id,
    )


def _payload(read: Callable[[int], bytes]) -> bytes:
    chunks: list[bytes] = []
    while chunk := read(65_536):
        chunks.append(chunk)
    return b"".join(chunks)


def _assert_search(content: bytes, needle: str, *, present: bool) -> None:
    document = pdfium.PdfDocument(content)
    try:
        page = document[0]
        try:
            textpage = page.get_textpage()
            try:
                search = textpage.search(needle)
                try:
                    assert (search.get_next() is not None) is present, (
                        "REDACTION_OCR_SEARCH_CONTROL"
                    )
                finally:
                    search.close()
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _assert_no_text_crosses_mask(content: bytes, request: RedactionReconstructionRequest) -> None:
    crop = request.crop
    source_box = request.marks[0].box
    # Independently project the persisted mark through crop and manifest rotation.
    mask = _turn(
        NormalizedPageBox.from_edges(
            max(0, round((source_box.x - crop.x) * 1_000_000 / crop.width)),
            max(0, round((source_box.y - crop.y) * 1_000_000 / crop.height)),
            min(
                1_000_000,
                round((source_box.x + source_box.width - crop.x) * 1_000_000 / crop.width),
            ),
            min(
                1_000_000,
                round((source_box.y + source_box.height - crop.y) * 1_000_000 / crop.height),
            ),
        ),
        int(request.rotation) // 90,
    )
    document = pdfium.PdfDocument(content)
    try:
        page = document[0]
        try:
            width, height = page.get_size()
            left = max(0.0, mask.x * width / 1_000_000)
            right = min(width, (mask.x + mask.width) * width / 1_000_000)
            top = max(0.0, mask.y * height / 1_000_000)
            bottom = min(height, (mask.y + mask.height) * height / 1_000_000)
            textpage = page.get_textpage()
            try:
                for index in range(textpage.count_chars()):
                    if not textpage.get_text_range(index, 1).strip():
                        continue
                    x0, y0, x1, y1 = textpage.get_charbox(index)
                    overlap = (
                        min(x1, right) - max(x0, left),
                        min(height - y0, bottom) - max(height - y1, top),
                    )
                    assert min(overlap) <= 0, "REDACTION_OCR_TEXT_CROSSES_MASK"
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _assert_budgets(runtime: RedactionOcrWorkerComposition) -> None:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
    resource = runtime.resource_snapshot()
    assert resource is not None
    assert resource.peak_process_memory_used_bytes <= policy.worker_process_memory_bytes
    assert resource.peak_job_memory_used_bytes <= policy.worker_job_memory_bytes
    temporary = runtime.private_temp_snapshot()
    assert temporary is not None and temporary.measurement_complete
    assert temporary.sample_count > 0
    assert temporary.sampling_interval_ms == policy.resource_sampling_interval_ms
    assert temporary.sampled_peak_bytes <= policy.maximum_temporary_storage_bytes


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda value: value.name)
def test_native_regeneration_excludes_canaries_and_preserves_searchable_visible_controls(
    tmp_path: Path, worker_test_resources: WorkerTestResources, scenario: _Scenario
) -> None:
    tesseract, tessdata = _runtime_from_environment()
    admission = admit_packaged_tesseract_runtime(tesseract, tessdata)
    if (
        scenario.language_set_id != "latin-default-v1"
        and "deu" not in admission.installed_language_codes
    ):
        pytest.skip("the optional verified German OCR language pack is not staged")
    case = _case(tmp_path, _source(scenario))
    request = _request(case, scenario)
    config = _config(worker_test_resources)
    with case.sources.borrow_committed_pdf_page(
        _CASE_ID, case.page_id, cancellation=lambda: False, deadline=lambda: False
    ) as source:
        with create_redaction_ocr_worker_composition(
            config,
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
            heavy_operations=HeavyOperationCoordinator(1),
        ) as runtime:
            assert runtime._worker._launcher._config.qpdf_executable is None  # type: ignore[attr-defined]
            # Real OCR must first recover the same canary with only a blank corner
            # marked. A failed recognizer cannot make the exclusion test pass.
            baseline = replace(
                request,
                reconstruction=replace(
                    request.reconstruction,
                    marks=(
                        RedactionMark(
                            _id(605),
                            case.page_id,
                            NormalizedPageBox(80_000, 80_000, 10_000, 10_000),
                        ),
                    ),
                ),
            )
            with runtime.processor.regenerate(
                baseline, source, cancellation=lambda: False, deadline=lambda: False
            ) as control:
                positive = _payload(control.read)
                assert (
                    f"SECRET CANARY {scenario.canary}" in " ".join(_text(positive).split()).upper()
                ), "REDACTION_OCR_POSITIVE_CONTROL_FAILED"
                _assert_search(positive, "SECRET", present=True)
            with runtime.processor.regenerate(
                request, source, cancellation=lambda: False, deadline=lambda: False
            ) as candidate:
                content = _payload(candidate.read)
                assert hashlib.sha256(content).hexdigest() == candidate.report.output_sha256
                assert len(content) == candidate.report.output_byte_size
                assert candidate.report.status == "awaiting_verification"
                assert candidate.report.unsigned_derivative
            _assert_budgets(runtime)
    text = " ".join(_text(content).split()).upper()
    assert scenario.keep in text, "REDACTION_OCR_KEEP_CONTROL_MISSING"
    _assert_search(content, scenario.keep.split()[0], present=True)
    for token in ("SECRET", "CANARY", scenario.canary):
        assert token not in text, "REDACTION_OCR_CANARY_EXTRACTED"
        _assert_search(content, token, present=False)
    _assert_no_text_crosses_mask(content, request.reconstruction)
    with pikepdf.Pdf.open(io.BytesIO(content), attempt_recovery=False) as candidate_pdf:
        assert len(candidate_pdf.pages) == 1
        assert "/Annots" not in candidate_pdf.pages[0].obj
        assert "/AcroForm" not in candidate_pdf.Root
    # OCR is permitted to add invisible text only. Reopen/render the actual PDF
    # and compare against this request's already reconstructed opaque appearance.
    reconstructed = tmp_path / "reconstructed.pdf"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    RedactionReconstructionAdapter().reconstruct(
        io.BytesIO(case.original),
        reconstructed,
        scratch,
        request.reconstruction,
        _limits(),
        _Control(),
    )
    expected, actual = (
        _render(reconstructed.read_bytes(), 0, scale=1.0),
        _render(content, 0, scale=1.0),
    )
    try:
        assert actual.size == expected.size, "REDACTION_OCR_PAGE_GEOMETRY_CHANGED"
        difference = ImageChops.difference(expected, actual)
        try:
            assert max(ImageStat.Stat(difference).mean) <= 1.0, "REDACTION_OCR_APPEARANCE_CHANGED"
        finally:
            difference.close()
    finally:
        expected.close()
        actual.close()
    assert list(scratch.iterdir()) == []
    assert PERMANENT_REDACTION_ENABLED is True
    case.assert_unchanged()


def test_native_ocr_cancellation_timeout_termination_exhaustion_and_recovery(
    tmp_path: Path, worker_test_resources: WorkerTestResources
) -> None:
    tesseract, tessdata = _runtime_from_environment()
    # The admitted base closure requires English and permits optional German.
    # Copy trusted bytes to an owned test root; never alter installed packs.
    admitted_root = tesseract.parent.parent
    assert tessdata == admitted_root / "share" / "tessdata"
    english_only = tmp_path / "english-only-runtime"
    shutil.copytree(admitted_root, english_only, ignore=shutil.ignore_patterns("deu.traineddata"))
    tesseract = english_only / "bin" / "tesseract.exe"
    tessdata = english_only / "share" / "tessdata"
    scenario = _SCENARIOS[0]
    case = _case(tmp_path, _source(scenario))
    request = _request(case, scenario)
    with case.sources.borrow_committed_pdf_page(
        _CASE_ID, case.page_id, cancellation=lambda: False, deadline=lambda: False
    ) as source:
        with create_redaction_ocr_worker_composition(
            _config(worker_test_resources),
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
            heavy_operations=HeavyOperationCoordinator(1),
        ) as runtime:
            for failure in ("cancel", "timeout", "terminate"):
                started = Event()
                signal = Event()

                def progress(_value: object, selected_started: Event = started) -> None:
                    selected_started.set()

                def observe_child(
                    selected_started: Event = started, selected_signal: Event = signal
                ) -> bool:
                    # A real OCR child must be active after page processing starts.
                    # Admission's earlier --version/--list-langs probes do not count.
                    process = runtime._worker._process
                    if (
                        selected_started.is_set()
                        and isinstance(process, WindowsWorkerProcess)
                        and process._job.active_processes() == 2
                    ):
                        selected_signal.set()
                    return selected_signal.is_set()

                def terminate_child_tree() -> bool:
                    if observe_child():
                        runtime._worker.terminate()
                    return False

                with pytest.raises(PdfExportError) as failed:
                    runtime.processor.regenerate(
                        request,
                        source,
                        cancellation=(
                            observe_child
                            if failure == "cancel"
                            else terminate_child_tree
                            if failure == "terminate"
                            else lambda: False
                        ),
                        deadline=observe_child if failure == "timeout" else lambda: False,
                        progress=progress,
                    )
                assert signal.is_set(), "REDACTION_OCR_CHILD_NOT_STARTED"
                if failure in {"cancel", "timeout"}:
                    assert failed.value.code is (
                        PdfExportErrorCode.CANCELLED
                        if failure == "cancel"
                        else PdfExportErrorCode.TIMED_OUT
                    )
                if failure == "cancel":
                    acknowledgement = runtime.last_cancellation_acknowledgement_ms()
                    assert acknowledgement is not None
                    assert (
                        acknowledgement
                        <= CONSTRAINED_RESOURCE_POLICY_V1.cancellation_acknowledgement_ms
                    )
                _assert_budgets(runtime)
                case.assert_unchanged()
            original_policy = runtime.processor._policy
            runtime.processor._policy = replace(
                original_policy, limits=replace(original_policy.limits, output_bytes=1)
            )
            with pytest.raises(PdfExportError):
                runtime.processor.regenerate(
                    request, source, cancellation=lambda: False, deadline=lambda: False
                )
            runtime.processor._policy = original_policy
            with pytest.raises(PdfExportError) as missing_language:
                runtime.processor.regenerate(
                    replace(request, language_set_id="german-fast-v1"),
                    source,
                    cancellation=lambda: False,
                    deadline=lambda: False,
                )
            assert missing_language.value.code is PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE
            assert runtime._worker.query().phase is WorkerLifecyclePhase.STOPPED
            case.assert_unchanged()
            with runtime.processor.regenerate(
                request, source, cancellation=lambda: False, deadline=lambda: False
            ) as candidate:
                assert candidate.report.status == "awaiting_verification"
                content = _payload(candidate.read)
                assert "PUBLIC ENGLISH CONTROL" in " ".join(_text(content).split()).upper()
                _assert_search(content, "SECRET", present=False)
            _assert_budgets(runtime)
    case.assert_unchanged()
