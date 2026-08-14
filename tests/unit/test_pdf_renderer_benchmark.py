from __future__ import annotations

import ast
import base64
import errno
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import patch

import tools.pdf_renderer_benchmark as benchmark_module
import tools.pdf_renderer_stage_windows as stage_windows
from paperworkshell.storage._windows import open_handle as storage_open_handle
from paperworkshell.worker.client.errors import WorkerClientError
from paperworkshell.worker.protocol.framing import MAX_FRAME_PAYLOAD_BYTES
from tools.pdf_renderer_benchmark import (
    BENCHMARK_STAGE_PREFIX,
    EXPECTED_FIDELITY_CASE_IDS,
    MAX_TEMP_SCAN_ATTEMPTS,
    OUTCOME_BENCHMARK_FAILED,
    OUTCOME_CLEANUP_FAILED,
    OUTCOME_DEPENDENCY_UNAVAILABLE,
    OUTCOME_PAGE_BOX_UNSUPPORTED,
    BenchmarkFailure,
    BenchmarkWorkload,
    ChildCapture,
    FidelityCaseRecord,
    StagedBenchmarkResources,
    _capture_child,
    _close_and_verify_isolated_process,
    _decode_golden_rgb,
    _decode_transport_payload,
    _failure_report,
    _fidelity_result,
    _finish_isolated_child,
    _fit_dimensions,
    _isolated_child_main,
    _measure_cooperative_cancellation,
    _measure_idle_worker_tree_termination,
    _parse_installed_record,
    _PypdfiumRuntime,
    _QtPdfRuntime,
    _read_git_blob,
    _receive_transport,
    _remove_benchmark_stage_root,
    _require_same_repository_revision,
    _require_stage_cleanup_authorized,
    _require_staged_namespace_sealed,
    _scan_temporary_bytes,
    _semantic_render_case,
    _semantic_result,
    _send_transport,
    _StableEngineError,
    _stage_git_file,
    _StageAuthority,
    _StageBudget,
    _start_isolated_child,
    _TemporaryDirectorySampler,
    _validate_probe,
    _validated_measurement_result,
    assert_sanitized_report,
    benchmark_report,
    cleanup_benchmark_resources,
    create_benchmark_stage_root,
    main,
    stage_benchmark_resources,
    summarize_warm_samples,
    validate_workload,
)
from tools.pdf_renderer_stage_windows import PinnedStageTree, StageWindowsFailure

ROOT = Path(__file__).resolve().parents[2]


def _current_worktree_blob(
    _revision: str,
    relative: PurePosixPath,
    *,
    maximum_bytes: int,
    repository_root: Path = ROOT,
) -> bytes:
    if repository_root != ROOT:
        raise AssertionError("unexpected repository root")
    payload = ROOT.joinpath(*relative.parts).read_bytes()
    if len(payload) > maximum_bytes:
        raise AssertionError("test blob exceeds production bound")
    return payload


def _synthetic_staged_resources(root: Path, cleanup_token: str) -> StagedBenchmarkResources:
    return StagedBenchmarkResources(
        stage_root=root,
        runtime_root=root / "runtime",
        code_root=root / "code",
        data_root=root / "data",
        python_executable=root / "runtime/python.exe",
        bootstrap_script=root / "code/bootstrap.py",
        manifest_path=root / "stage-manifest.json",
        manifest_sha256="a" * 64,
        cleanup_token=cleanup_token,
        staged_file_sha256=(),
    )


class _CleanupAuthorityTreeProbe:
    def __init__(
        self,
        *,
        fail_live_verification: bool = False,
        fail_namespace_verification: bool = False,
    ) -> None:
        self.fail_live_verification = fail_live_verification
        self.fail_namespace_verification = fail_namespace_verification
        self.require_live_calls = 0
        self.delete_calls = 0
        self.close_calls = 0
        self.restore_calls = 0

    def require_live(self) -> None:
        self.require_live_calls += 1
        if self.fail_live_verification:
            raise RuntimeError("synthetic live-authority verification failure")

    def require_namespace_sealed(self) -> None:
        if self.fail_namespace_verification:
            raise RuntimeError("synthetic namespace verification failure")

    def delete(self, _expected: object, _maximum_bytes: int) -> None:
        self.delete_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def _restore_all_namespace_policies(self) -> None:
        self.restore_calls += 1


class _FakeBitmap:
    def __init__(self, events: list[str], width: int, height: int) -> None:
        self._events = events
        self.width = width
        self.height = height
        self.stride = width * 4
        self.buffer = bytes((255, 255, 255, 255)) * (width * height)

    def close(self) -> None:
        self._events.append("bitmap-close")


class _FakeMediaPage:
    def __init__(self, *, fail_restore: bool = False) -> None:
        self.events: list[str] = []
        self._set_count = 0
        self._fail_restore = fail_restore

    def get_mediabox(self) -> tuple[float, float, float, float]:
        self.events.append("get-media")
        return (0.0, 0.0, 80.0, 60.0)

    def get_cropbox(self) -> tuple[float, float, float, float]:
        self.events.append("get-crop")
        return (10.0, 10.0, 70.0, 50.0)

    def set_cropbox(self, *_values: float) -> None:
        self._set_count += 1
        event = "select-media" if self._set_count == 1 else "restore-crop"
        self.events.append(event)
        if event == "restore-crop" and self._fail_restore:
            raise RuntimeError("synthetic restoration failure")

    def get_size(self) -> tuple[float, float]:
        self.events.append("get-size")
        return (60.0, 80.0)

    def render(self, **_arguments: object) -> _FakeBitmap:
        self.events.append("render")
        return _FakeBitmap(self.events, 76, 101)

    def close(self) -> None:
        self.events.append("page-close")


class _FakeDocument:
    def __init__(self, page: _FakeMediaPage) -> None:
        self._page = page

    def __getitem__(self, _page_index: int) -> _FakeMediaPage:
        return self._page


class _FakeRaw:
    FPDFBitmap_BGRA = 4


class PdfRendererBenchmarkWorkloadTests(unittest.TestCase):
    def _copy_bound_root(self, destination: Path) -> None:
        (destination / "benchmarks").mkdir(parents=True)
        shutil.copy2(
            ROOT / "benchmarks/pdf-renderer-windows-v1.json",
            destination / "benchmarks/pdf-renderer-windows-v1.json",
        )
        shutil.copytree(
            ROOT / "tests/fixtures/synthetic/v1",
            destination / "tests/fixtures/synthetic/v1",
        )
        shutil.copytree(
            ROOT / "tests/golden/renderer-v1",
            destination / "tests/golden/renderer-v1",
        )

    def test_checked_in_workload_binds_every_required_input(self) -> None:
        workload, issues = validate_workload()

        self.assertEqual([], issues)
        assert workload is not None
        self.assertEqual(
            EXPECTED_FIDELITY_CASE_IDS, tuple(c.case_id for c in workload.fidelity_cases)
        )
        self.assertEqual(
            ("missing-font-fallback", "media-box-aspect-fit"),
            tuple(c.case_id for c in workload.semantic_cases),
        )
        self.assertEqual(3, len(workload.outcome_cases))
        self.assertEqual(("pypdfium2", "qt-pdf"), tuple(r.renderer_id for r in workload.renderers))
        self.assertEqual(7, workload.measurement.warm_iterations)
        self.assertEqual(
            "paperworkshell-pdf-renderer-software-budget-v1",
            workload.execution_budget_id,
        )
        self.assertEqual(2, workload.logical_processor_limit)
        self.assertEqual(1_342_177_280, workload.process_memory_limit_bytes)
        self.assertEqual(1_342_177_280, workload.job_memory_limit_bytes)
        self.assertEqual(1, workload.maximum_processes)

        media_case = workload.semantic_cases[1]
        self.assertEqual("media", media_case.page_box)
        self.assertEqual("distinct-from-crop", media_case.page_box_relation)
        self.assertEqual(
            (101, 101), (media_case.maximum_width_pixels, media_case.maximum_height_pixels)
        )
        self.assertEqual((76, 101), (media_case.width_pixels, media_case.height_pixels))
        self.assertNotEqual(
            (media_case.maximum_width_pixels, media_case.maximum_height_pixels),
            (media_case.width_pixels, media_case.height_pixels),
        )
        self.assertEqual(
            (media_case.width_pixels, media_case.height_pixels),
            _fit_dimensions(
                media_case.oriented_width_units,
                media_case.oriented_height_units,
                media_case.maximum_width_pixels,
                media_case.maximum_height_pixels,
            ),
        )

    def test_duplicate_workload_key_is_rejected_without_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_bound_root(root)
            workload_path = root / "benchmarks/pdf-renderer-windows-v1.json"
            text = workload_path.read_text(encoding="utf-8")
            workload_path.write_text(
                text.replace(
                    '  "schema_version": 2,',
                    '  "schema_version": 2,\n  "schema_version": 2,',
                    1,
                ),
                encoding="utf-8",
            )

            workload, issues = validate_workload(root)

        rendered = "\n".join(issue.render() for issue in issues)
        self.assertIsNone(workload)
        self.assertIn("PDF_RENDER_BENCHMARK_JSON_DUPLICATE_KEY", rendered)
        self.assertNotIn(str(root), rendered)

    def test_fixture_index_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_bound_root(root)
            index_path = root / "tests/fixtures/synthetic/v1/index.json"
            index_path.write_bytes(index_path.read_bytes() + b" ")

            workload, issues = validate_workload(root)

        self.assertIsNone(workload)
        self.assertTrue(any(issue.code == "BENCHMARK_BINDING_HASH_MISMATCH" for issue in issues))

    def test_bound_source_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_bound_root(root)
            source = root / "tests/fixtures/synthetic/v1/pdf/render-transparency.pdf"
            source.write_bytes(source.read_bytes() + b"x")

            workload, issues = validate_workload(root)

        self.assertIsNone(workload)
        self.assertTrue(any(issue.code == "BENCHMARK_ARTIFACT_HASH_MISMATCH" for issue in issues))

    def test_validate_command_is_stable_and_non_disclosing(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            result = main(["validate"])

        self.assertEqual(0, result)
        self.assertIn("15 analytical goldens", output.getvalue())
        self.assertIn("2 semantic comparisons", output.getvalue())
        self.assertNotIn(str(ROOT), output.getvalue())

    def test_physical_profile_and_attestation_cli_flags_are_removed(self) -> None:
        removed_arguments = (
            ("--scenario", "local-smoke"),
            ("--attest-memory-mib", "4096"),
            ("--attest-graphics", "integrated"),
            ("--attest-storage-media", "hdd-or-validated-emulation"),
        )
        for arguments in removed_arguments:
            with (
                self.subTest(arguments=arguments),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main(["benchmark", *arguments])

            self.assertEqual(2, raised.exception.code)

    def test_benchmark_command_sanitizes_an_unexpected_exception(self) -> None:
        error_output = io.StringIO()
        private_detail = "C:\\private\\unexpected-renderer-detail"

        with (
            patch(
                "tools.pdf_renderer_benchmark.benchmark_report",
                side_effect=RuntimeError(private_detail),
            ),
            redirect_stderr(error_output),
        ):
            result = main(["benchmark"])

        rendered = error_output.getvalue()
        report = json.loads(rendered)
        self.assertEqual(1, result)
        self.assertEqual(OUTCOME_BENCHMARK_FAILED, report["error"]["code"])
        self.assertNotIn("private", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_installed_record_parser_binds_sha256_and_size(self) -> None:
        digest = hashlib.sha256(b"abc").digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        parsed = _parse_installed_record(
            (
                f"example/module.py,sha256={encoded},3\n"
                "example-1.0.dist-info/RECORD,,\n"
                "../../Scripts/example.exe,sha256=ignored,1\n"
            ).encode("ascii")
        )

        self.assertEqual(
            (hashlib.sha256(b"abc").hexdigest(), 3),
            parsed[PurePosixPath("example/module.py")],
        )
        self.assertEqual(
            (None, None),
            parsed[PurePosixPath("example-1.0.dist-info/RECORD")],
        )
        self.assertNotIn(PurePosixPath("../../Scripts/example.exe"), parsed)

    def test_repository_terminal_check_requires_the_same_clean_head(self) -> None:
        with (
            patch(
                "tools.pdf_renderer_benchmark._repository_revision",
                return_value="b" * 40,
            ),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            _require_same_repository_revision("a" * 40)

        self.assertEqual("PDF_RENDER_BENCHMARK_REPOSITORY_DIRTY", raised.exception.code)

    def test_git_blob_reader_ignores_a_transient_worktree_overwrite(self) -> None:
        git = shutil.which("git")
        self.assertIsNotNone(git)
        assert git is not None
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(
                (git, "init", "--quiet"),
                cwd=repository,
                check=True,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                (git, "config", "user.email", "synthetic@example.invalid"),
                cwd=repository,
                check=True,
                shell=False,
            )
            subprocess.run(
                (git, "config", "user.name", "Synthetic Test"),
                cwd=repository,
                check=True,
                shell=False,
            )
            tracked = repository / "tracked.txt"
            tracked.write_bytes(b"committed bytes\n")
            subprocess.run(
                (git, "add", "--", "tracked.txt"),
                cwd=repository,
                check=True,
                shell=False,
            )
            subprocess.run(
                (git, "commit", "--quiet", "-m", "synthetic fixture"),
                cwd=repository,
                check=True,
                shell=False,
            )
            revision = (
                subprocess.run(
                    (git, "rev-parse", "HEAD"),
                    cwd=repository,
                    check=True,
                    shell=False,
                    stdout=subprocess.PIPE,
                )
                .stdout.decode("ascii")
                .strip()
            )
            tracked.write_bytes(b"transient overwrite\n")

            observed = _read_git_blob(
                revision,
                PurePosixPath("tracked.txt"),
                maximum_bytes=64,
                repository_root=repository,
            )

        self.assertEqual(b"committed bytes\n", observed)

    def test_stage_project_file_uses_commit_blob_not_transient_worktree_bytes(self) -> None:
        stage_root = Path(tempfile.gettempdir()).resolve() / "synthetic-stage-root"
        source = ROOT / "synthetic-tracked.py"
        destination = stage_root / "code" / "synthetic-tracked.py"
        committed = b"committed project bytes\n"
        captured: list[bytes] = []

        class _Tree:
            def ensure_directory(self, _relative: PurePosixPath) -> None:
                return None

            def write_bytes(
                self,
                _relative: PurePosixPath,
                payload: bytes,
                *,
                maximum_bytes: int,
            ) -> str:
                self.assert_bound(payload, maximum_bytes)
                captured.append(payload)
                return hashlib.sha256(payload).hexdigest()

            @staticmethod
            def assert_bound(payload: bytes, maximum_bytes: int) -> None:
                if len(payload) > maximum_bytes:
                    raise AssertionError("payload exceeded stage bound")

        authority = SimpleNamespace(tree=_Tree())
        staged: dict[PurePosixPath, str] = {}
        with (
            patch(
                "tools.pdf_renderer_benchmark._registered_stage_authority",
                return_value=(stage_root, authority),
            ),
            patch(
                "tools.pdf_renderer_benchmark._read_git_blob",
                return_value=committed,
            ),
            patch.object(Path, "read_bytes", return_value=b"transient overwrite") as read_bytes,
        ):
            _stage_git_file(
                "a" * 40,
                source,
                destination,
                stage_root,
                _StageBudget(),
                staged,
            )

        self.assertEqual([committed], captured)
        read_bytes.assert_not_called()

    def test_software_execution_budget_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_bound_root(root)
            workload_path = root / "benchmarks/pdf-renderer-windows-v1.json"
            document = json.loads(workload_path.read_text(encoding="utf-8"))
            document["execution_budget"]["job_memory_limit_bytes"] += 1
            workload_path.write_text(json.dumps(document), encoding="utf-8")

            workload, issues = validate_workload(root)

        self.assertIsNone(workload)
        self.assertTrue(any(issue.code == "BENCHMARK_EXECUTION_BUDGET" for issue in issues))

    def test_report_exposes_only_enforced_software_budget_evidence(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None

        def renderer_result(specification: object, *_arguments: object) -> dict[str, object]:
            renderer_id = cast(Any, specification).renderer_id
            return {
                "renderer_id": renderer_id,
                "fidelity": [{"status": "matched"} for _case in workload.fidelity_cases],
                "semantic_fidelity": [
                    {"outcome_code": case.expected_code} for case in workload.semantic_cases
                ],
                "outcomes": [
                    {"observed_code": case.expected_code} for case in workload.outcome_cases
                ],
            }

        with tempfile.TemporaryDirectory() as temporary:
            resources = _synthetic_staged_resources(Path(temporary) / "stage", "a" * 64)
            with (
                patch.object(sys, "platform", "win32"),
                patch(
                    "tools.pdf_renderer_benchmark._repository_revision",
                    return_value="a" * 40,
                ),
                patch(
                    "tools.pdf_renderer_benchmark._windows_runtime_identity",
                    return_value={},
                ),
                patch(
                    "tools.pdf_renderer_benchmark.create_benchmark_stage_root",
                    return_value=resources.stage_root,
                ),
                patch(
                    "tools.pdf_renderer_benchmark.stage_benchmark_resources",
                    return_value=resources,
                ),
                patch("tools.pdf_renderer_benchmark._require_same_repository_revision"),
                patch("tools.pdf_renderer_benchmark._capture_child", return_value=object()),
                patch("tools.pdf_renderer_benchmark._validate_probe", return_value={}),
                patch("tools.pdf_renderer_benchmark._validate_stage_record_identity"),
                patch(
                    "tools.pdf_renderer_benchmark._run_renderer_suite",
                    side_effect=renderer_result,
                ),
                patch("tools.pdf_renderer_benchmark.cleanup_benchmark_resources"),
            ):
                report = benchmark_report(workload, work_directory=Path(temporary))

        self.assertEqual(2, report["report_schema_version"])
        self.assertNotIn("reference_environment", report)
        self.assertEqual(
            {
                "id": "paperworkshell-pdf-renderer-software-budget-v1",
                "classification": "software-enforced",
                "logical_processor_limit": 2,
                "processor_limit_source": "isolated-child-affinity-mask",
                "process_memory_limit_bytes": 1_342_177_280,
                "job_memory_limit_bytes": 1_342_177_280,
                "memory_limit_source": "windows-job-object",
                "maximum_processes": 1,
                "process_limit_source": "windows-job-object",
                "hardware_equivalence_claim": False,
            },
            report["execution_budget"],
        )
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("attest", rendered)
        self.assertNotIn("rotational-hdd", rendered)
        self.assertNotIn("limited-storage", rendered)


class PdfRendererBenchmarkOracleTests(unittest.TestCase):
    workload: ClassVar[BenchmarkWorkload]

    @classmethod
    def setUpClass(cls) -> None:
        workload, issues = validate_workload()
        if issues or workload is None:
            raise AssertionError(issues)
        cls.workload = workload

    def test_all_analytical_pngs_decode_to_exact_rgb_size(self) -> None:
        for case in self.workload.fidelity_cases:
            with self.subTest(case_id=case.case_id):
                decoded = _decode_golden_rgb(case)

                self.assertEqual(case.width_pixels * case.height_pixels * 3, len(decoded))
                self.assertIn(case.page_box, {"crop", "media"})
                self.assertEqual(
                    (case.width_pixels, case.height_pixels),
                    _fit_dimensions(
                        case.oriented_width_units,
                        case.oriented_height_units,
                        case.maximum_width_pixels,
                        case.maximum_height_pixels,
                    ),
                )

    def test_exact_rgba_matches_and_changed_channel_fails(self) -> None:
        case = self.workload.fidelity_cases[0]
        rgb = _decode_golden_rgb(case)
        rgba = bytearray()
        for offset in range(0, len(rgb), 3):
            rgba.extend((*rgb[offset : offset + 3], 255))

        matched = _fidelity_result(bytes(rgba), case)
        rgba[0] ^= 0xFF
        changed = _fidelity_result(bytes(rgba), case)

        self.assertEqual("matched", matched["status"])
        self.assertEqual("different", changed["status"])
        self.assertGreater(cast(int, changed["differing_channels"]), 0)

    def test_semantic_missing_font_oracle_requires_bounded_opaque_rgba(self) -> None:
        case = self.workload.semantic_cases[0]
        all_white = bytes((255, 255, 255, 255)) * (case.width_pixels * case.height_pixels)
        opaque = bytearray(all_white)
        for y in range(24, 34):
            for x in range(20, 30):
                offset = (y * case.width_pixels + x) * 4
                opaque[offset : offset + 4] = bytes((80, 80, 80, 255))
        transparent = bytearray(opaque)
        transparent[3] = 0

        passed = _semantic_result(bytes(opaque), case)
        empty = _semantic_result(all_white, case)
        failed = _semantic_result(bytes(transparent), case)

        self.assertEqual("PDF_RENDER_OK", passed["outcome_code"])
        self.assertEqual("matched", passed["status"])
        self.assertEqual(100, passed["nonwhite_pixels"])
        self.assertIs(True, passed["bounded_nonwhite_region"])
        self.assertEqual("PDF_RENDER_RESULT_INVALID", empty["outcome_code"])
        self.assertIs(False, empty["bounded_nonwhite_region"])
        self.assertEqual("PDF_RENDER_RESULT_INVALID", failed["outcome_code"])

    def test_path_bearing_records_have_content_free_representations(self) -> None:
        case = self.workload.fidelity_cases[0]
        representation = repr(case)

        self.assertIn(case.case_id, representation)
        self.assertNotIn(str(case.source_path), representation)
        self.assertNotIn(str(case.golden_path), representation)

    def test_png_decoder_rejects_over_expanding_idat_before_unbounded_flush(self) -> None:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            return (
                len(data).to_bytes(4, "big")
                + chunk_type
                + data
                + (zlib.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big")
            )

        header = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
        bomb = zlib.compress(b"\x00" + b"\x00" * (1024 * 1024))
        content = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", bomb)
            + chunk(b"IEND", b"")
        )
        with tempfile.TemporaryDirectory() as temporary:
            golden = Path(temporary) / "bounded.png"
            golden.write_bytes(content)
            case = replace(
                self.workload.fidelity_cases[0],
                golden_path=golden,
                golden_sha256=hashlib.sha256(content).hexdigest(),
                oriented_width_units=1.0,
                oriented_height_units=1.0,
                maximum_width_pixels=1,
                maximum_height_pixels=1,
                width_pixels=1,
                height_pixels=1,
            )

            with self.assertRaises(_StableEngineError) as raised:
                _decode_golden_rgb(case)

        self.assertEqual("PDF_RENDER_BENCHMARK_GOLDEN_INVALID", raised.exception.code)


class PdfRendererBenchmarkRuntimeTests(unittest.TestCase):
    @staticmethod
    def _media_case() -> FidelityCaseRecord:
        workload, issues = validate_workload()
        if issues or workload is None:
            raise AssertionError(issues)
        return _semantic_render_case(workload.semantic_cases[1])

    @staticmethod
    def _runtime() -> _PypdfiumRuntime:
        runtime = object.__new__(_PypdfiumRuntime)
        runtime._raw = cast(Any, _FakeRaw())
        return runtime

    def test_pypdfium_media_selection_renders_then_restores_before_page_close(self) -> None:
        page = _FakeMediaPage()

        output = self._runtime().render_page(_FakeDocument(page), self._media_case())

        self.assertEqual(76 * 101 * 4, len(output))
        self.assertEqual(
            [
                "get-media",
                "get-crop",
                "select-media",
                "get-size",
                "render",
                "bitmap-close",
                "restore-crop",
                "page-close",
            ],
            page.events,
        )

    def test_pypdfium_crop_restoration_failure_invalidates_render(self) -> None:
        page = _FakeMediaPage(fail_restore=True)

        with self.assertRaises(_StableEngineError) as raised:
            self._runtime().render_page(_FakeDocument(page), self._media_case())

        self.assertEqual("PDF_RENDER_DEPENDENCY_FAILED", raised.exception.code)
        self.assertEqual("page-close", page.events[-1])
        self.assertLess(page.events.index("render"), page.events.index("restore-crop"))


class PdfRendererBenchmarkTemporarySamplerTests(unittest.TestCase):
    def test_temp_scan_retries_the_whole_scan_when_an_entry_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disappearing = root / "bounded.tmp"
            disappearing.write_bytes(b"x" * 32)
            original_lstat = Path.lstat
            disappearances = 0

            def lstat(path: Path) -> os.stat_result:
                nonlocal disappearances
                if path == disappearing and disappearances == 0:
                    disappearances += 1
                    disappearing.unlink()
                    raise FileNotFoundError
                return original_lstat(path)

            with patch.object(Path, "lstat", new=lstat):
                observed = _scan_temporary_bytes(root, 1_024)

        self.assertEqual(0, observed)
        self.assertEqual(1, disappearances)

    def test_temp_scan_bounds_repeated_disappearance_retries(self) -> None:
        attempts = 0

        def missing(_path: Path) -> os.stat_result:
            nonlocal attempts
            attempts += 1
            raise FileNotFoundError

        with (
            patch.object(Path, "lstat", new=missing),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            _scan_temporary_bytes(Path("C:\\synthetic-temp"), 1_024)

        self.assertEqual(MAX_TEMP_SCAN_ATTEMPTS, attempts)
        self.assertEqual(
            "PDF_RENDER_BENCHMARK_TEMP_MEASUREMENT_FAILED",
            raised.exception.code,
        )

    def test_temp_scan_does_not_retry_permission_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            denied = root / "bounded.tmp"
            denied.write_bytes(b"x")
            original_lstat = Path.lstat
            attempts = 0

            def lstat(path: Path) -> os.stat_result:
                nonlocal attempts
                if path == denied:
                    attempts += 1
                    raise PermissionError
                return original_lstat(path)

            with (
                patch.object(Path, "lstat", new=lstat),
                self.assertRaises(BenchmarkFailure) as raised,
            ):
                _scan_temporary_bytes(root, 1_024)

        self.assertEqual(1, attempts)
        self.assertEqual(
            "PDF_RENDER_BENCHMARK_TEMP_MEASUREMENT_FAILED",
            raised.exception.code,
        )

    def test_concurrent_temp_sampler_records_high_water_and_joins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary)
            root = local_root / "Temp"
            environment = {
                "LOCALAPPDATA": str(local_root),
                "TEMP": str(root),
                "TMP": str(root),
            }
            with patch.dict(os.environ, environment, clear=False):
                sampler = _TemporaryDirectorySampler(1, 1_024)
                sampler.start()
                (root / "bounded.tmp").write_bytes(b"x" * 32)
                time.sleep(0.02)

                high_water = sampler.stop()

        self.assertGreaterEqual(high_water, 32)

    def test_temp_sampler_propagates_background_scan_failure(self) -> None:
        calls = 0

        def scan(_root: Path, _maximum: int) -> int:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise BenchmarkFailure("PDF_RENDER_BENCHMARK_CHILD_FAILED", "renderer child failed")
            return 0

        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary)
            root = local_root / "Temp"
            environment = {
                "LOCALAPPDATA": str(local_root),
                "TEMP": str(root),
                "TMP": str(root),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch("tools.pdf_renderer_benchmark._scan_temporary_bytes", side_effect=scan),
            ):
                sampler = _TemporaryDirectorySampler(1, 1_024)
                sampler.start()
                time.sleep(0.02)
                with self.assertRaises(BenchmarkFailure):
                    sampler.stop()

    def test_child_preserves_sampler_start_failure_when_stop_also_fails(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TEMP_MEASUREMENT_FAILED",
            "isolated temporary-storage measurement failed",
        )

        class _FailingSampler:
            def start(self) -> None:
                raise primary

            def stop(self) -> int:
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_CHILD_FAILED",
                    "renderer child failed",
                )

        messages: list[dict[str, object]] = []
        correlation_id = "1" * 32
        isolation = SimpleNamespace(
            app_container=True,
            zero_capabilities=True,
            in_job=True,
            minimal_environment=True,
            standard_handles_non_inheritable=True,
        )
        command = {
            "protocol_version": 1,
            "command": "run",
            "mode": "measure",
            "renderer_id": "pypdfium2",
            "correlation_id": correlation_id,
        }
        with (
            patch(
                "tools.pdf_renderer_benchmark.validate_workload",
                return_value=(workload, []),
            ),
            patch(
                "tools.pdf_renderer_benchmark._limit_process_affinity_count",
                return_value=workload.logical_processor_limit,
            ),
            patch("tools.pdf_renderer_benchmark._read_child_payload", return_value=command),
            patch(
                "tools.pdf_renderer_benchmark._TemporaryDirectorySampler",
                return_value=_FailingSampler(),
            ),
            patch(
                "tools.pdf_renderer_benchmark._write_child_payload",
                side_effect=lambda _writer, payload: messages.append(dict(payload)),
            ),
        ):
            result = _isolated_child_main(
                Path(),
                isolation,
                correlation_id,
                object(),
                object(),
            )

        self.assertEqual(2, result)
        self.assertEqual(primary.code, messages[-1]["code"])
        self.assertEqual(primary.message, messages[-1]["message"])


class PdfRendererBenchmarkMeasurementTests(unittest.TestCase):
    workload: ClassVar[BenchmarkWorkload]

    @classmethod
    def setUpClass(cls) -> None:
        workload, issues = validate_workload()
        if issues or workload is None:
            raise AssertionError(issues)
        cls.workload = workload

    def _measurement_payload(self) -> dict[str, object]:
        specification = self.workload.renderers[0]
        render_cases: list[FidelityCaseRecord] = [
            *self.workload.fidelity_cases,
            *(_semantic_render_case(case) for case in self.workload.semantic_cases),
        ]
        samples = []
        elapsed = 1
        for iteration in range(self.workload.measurement.warm_iterations):
            for case in render_cases:
                samples.append(
                    {
                        "case_id": case.case_id,
                        "fixture_id": case.fixture_id,
                        "page_index": case.page_index,
                        "iteration": iteration,
                        "elapsed_ns": elapsed,
                    }
                )
                elapsed += 1
        first = self.workload.fidelity_cases[0]
        return {
            "protocol_version": 1,
            "event": "measurement",
            "renderer_id": specification.renderer_id,
            "correlation_id": "1" * 32,
            "identity": {
                "renderer_id": specification.renderer_id,
                "package": specification.package,
                "package_version": specification.package_version,
                "installed_record_sha256": "a" * 64,
                "declared_published_artifact_sha256": specification.artifact_sha256,
                "engine": specification.engine,
                "engine_version": specification.engine_version,
                "engine_feature_flags": [],
            },
            "child_elapsed_ns": 100_000,
            "cold": {
                "import_elapsed_ns": 100,
                "document_open_elapsed_ns": 200,
                "first_page_elapsed_ns": 300,
                "first_case_id": first.case_id,
                "first_fixture_id": first.fixture_id,
                "first_page_index": first.page_index,
            },
            "warm_samples": samples,
            "fidelity": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "page_index": case.page_index,
                    "status": "matched",
                    "maximum_channel_delta": 0,
                    "allowed_channel_delta": case.maximum_channel_delta,
                    "differing_channels": 0,
                    "page_box": case.page_box,
                    "maximum_width_pixels": case.maximum_width_pixels,
                    "maximum_height_pixels": case.maximum_height_pixels,
                    "width_pixels": case.width_pixels,
                    "height_pixels": case.height_pixels,
                }
                for case in self.workload.fidelity_cases
            ],
            "semantic_fidelity": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "page_index": case.page_index,
                    "page_box": case.page_box,
                    "page_box_relation": case.page_box_relation,
                    "oracle": case.oracle,
                    "status": "matched",
                    "outcome_code": case.expected_code,
                    "width_pixels": case.width_pixels,
                    "height_pixels": case.height_pixels,
                    "output_bytes": case.width_pixels * case.height_pixels * 4,
                    "opaque": True,
                    "nonwhite_pixels": 1,
                    "bounded_nonwhite_region": True,
                }
                for case in self.workload.semantic_cases
            ],
            "outcomes": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "expected_code": case.expected_code,
                    "observed_code": case.expected_code,
                }
                for case in self.workload.outcome_cases
            ],
            "output_bytes_high_water": 32768,
            "temporary_bytes_high_water": 0,
        }

    def _capture_for_payload(
        self,
        payload: dict[str, object],
        *,
        peak_process_bytes: int = 123_456,
    ) -> ChildCapture:
        return ChildCapture(
            payload=payload,
            correlation_id="1" * 32,
            isolated_worker_ready_elapsed_ns=2_000,
            parent_observed_elapsed_ns=200_000,
            process_affinity_count=2,
            process_memory_limit_bytes=self.workload.process_memory_limit_bytes,
            job_memory_limit_bytes=self.workload.job_memory_limit_bytes,
            peak_process_memory_used_bytes=peak_process_bytes,
            peak_job_memory_used_bytes=max(peak_process_bytes, 123_456),
        )

    def test_nearest_rank_summary_has_fixture_page_and_iteration_attribution(self) -> None:
        samples = [
            {
                "case_id": "case-a",
                "fixture_id": "fixture-a",
                "page_index": 2,
                "iteration": index,
                "elapsed_ns": value,
            }
            for index, value in enumerate((1, 2, 3, 4, 100))
        ]

        summary = summarize_warm_samples(samples)

        self.assertEqual(3, summary["p50_elapsed_ns"])
        self.assertEqual(100, summary["p95_elapsed_ns"])
        self.assertEqual(
            {
                "case_id": "case-a",
                "fixture_id": "fixture-a",
                "page_index": 2,
                "iteration": 4,
            },
            summary["maximum_attribution"],
        )

    def test_measurement_child_payload_is_revalidated_before_report_use(self) -> None:
        payload = self._measurement_payload()
        capture = self._capture_for_payload(payload)

        result = _validated_measurement_result(
            capture,
            self.workload.renderers[0],
            self.workload,
        )

        self.assertEqual(
            2_000,
            cast(dict[str, Any], result["cold"])["isolated_worker_ready_elapsed_ns"],
        )
        self.assertEqual(
            123_456,
            cast(dict[str, Any], result["resources"])["peak_process_memory_used_bytes"],
        )
        self.assertEqual(119, cast(dict[str, Any], result["warm"])["sample_count"])
        self.assertEqual(
            self.workload.job_memory_limit_bytes,
            cast(dict[str, Any], result["resources"])["job_memory_limit_bytes"],
        )

    def test_changed_child_correlation_is_rejected(self) -> None:
        payload = self._measurement_payload()
        payload["correlation_id"] = "2" * 32

        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                self._capture_for_payload(payload),
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)

    def test_zero_process_memory_evidence_is_rejected(self) -> None:
        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                self._capture_for_payload(
                    self._measurement_payload(),
                    peak_process_bytes=0,
                ),
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)

    def test_capture_outside_the_two_processor_budget_is_rejected(
        self,
    ) -> None:
        capture = replace(
            self._capture_for_payload(self._measurement_payload()),
            process_affinity_count=64,
        )

        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                capture,
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual(2, self.workload.logical_processor_limit)
        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)

    def test_contradictory_and_out_of_range_measurements_are_rejected(self) -> None:
        def fidelity(payload: dict[str, object]) -> dict[str, object]:
            return cast(list[dict[str, object]], payload["fidelity"])[0]

        def semantic(payload: dict[str, object]) -> dict[str, object]:
            return cast(list[dict[str, object]], payload["semantic_fidelity"])[0]

        def warm(payload: dict[str, object]) -> dict[str, object]:
            return cast(list[dict[str, object]], payload["warm_samples"])[0]

        def cold(payload: dict[str, object]) -> dict[str, object]:
            return cast(dict[str, object], payload["cold"])

        mutations: dict[str, Callable[[dict[str, object]], None]] = {
            "wrong-root-event": lambda payload: payload.__setitem__("event", "probe"),
            "zero-child-duration": lambda payload: payload.__setitem__("child_elapsed_ns", 0),
            "negative-warm-duration": lambda payload: warm(payload).__setitem__("elapsed_ns", -1),
            "zero-warm-duration": lambda payload: warm(payload).__setitem__("elapsed_ns", 0),
            "zero-cold-import-duration": lambda payload: cold(payload).__setitem__(
                "import_elapsed_ns", 0
            ),
            "zero-cold-open-duration": lambda payload: cold(payload).__setitem__(
                "document_open_elapsed_ns", 0
            ),
            "zero-cold-render-duration": lambda payload: cold(payload).__setitem__(
                "first_page_elapsed_ns", 0
            ),
            "elapsed-shorter-than-measured-segments": lambda payload: payload.__setitem__(
                "child_elapsed_ns", 1
            ),
            "channel-delta-above-rgba8": lambda payload: fidelity(payload).__setitem__(
                "maximum_channel_delta", 999
            ),
            "negative-differing-channels": lambda payload: fidelity(payload).__setitem__(
                "differing_channels", -1
            ),
            "different-status-with-no-difference": lambda payload: fidelity(payload).__setitem__(
                "status", "different"
            ),
            "semantic-render-error-with-success-code": lambda payload: semantic(
                payload
            ).__setitem__("status", "render-error"),
            "semantic-matched-with-transparent-output": lambda payload: semantic(
                payload
            ).__setitem__("opaque", False),
            "semantic-bounded-oracle-without-bounded-content": lambda payload: semantic(
                payload
            ).__setitem__("bounded_nonwhite_region", False),
            "negative-semantic-output": lambda payload: semantic(payload).__setitem__(
                "output_bytes", -1
            ),
            "output-high-water-below-reported-output": lambda payload: payload.__setitem__(
                "output_bytes_high_water", 1
            ),
            "negative-temporary-high-water": lambda payload: payload.__setitem__(
                "temporary_bytes_high_water", -1
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = cast(
                    dict[str, object],
                    json.loads(json.dumps(self._measurement_payload())),
                )
                mutate(payload)

                with self.assertRaises(BenchmarkFailure) as raised:
                    _validated_measurement_result(
                        self._capture_for_payload(payload),
                        self.workload.renderers[0],
                        self.workload,
                    )

                self.assertEqual(
                    "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
                    raised.exception.code,
                )

    def test_child_total_cannot_exceed_parent_monotonic_envelope(self) -> None:
        capture = replace(
            self._capture_for_payload(self._measurement_payload()),
            parent_observed_elapsed_ns=99_999,
        )

        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                capture,
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)

    def test_peak_resource_use_cannot_exceed_the_enforced_limit(self) -> None:
        capture = replace(
            self._capture_for_payload(self._measurement_payload()),
            peak_process_memory_used_bytes=self.workload.process_memory_limit_bytes + 1,
            peak_job_memory_used_bytes=self.workload.job_memory_limit_bytes + 1,
        )

        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                capture,
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)

    def test_unrecognized_observed_outcome_code_is_rejected(self) -> None:
        payload = self._measurement_payload()
        outcomes = cast(list[dict[str, object]], payload["outcomes"])
        outcomes[0]["observed_code"] = "C:\\private\\source.pdf"

        with self.assertRaises(BenchmarkFailure) as raised:
            _validated_measurement_result(
                self._capture_for_payload(payload),
                self.workload.renderers[0],
                self.workload,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)
        self.assertNotIn("private", str(raised.exception))


class PdfRendererBenchmarkPrivacyTests(unittest.TestCase):
    @staticmethod
    def _cleanup_authority_fixture(
        *,
        fail_live_verification: bool = False,
        fail_namespace_verification: bool = False,
    ) -> tuple[
        Path,
        StagedBenchmarkResources,
        _StageAuthority,
        _CleanupAuthorityTreeProbe,
    ]:
        root = (
            Path(tempfile.gettempdir()).resolve()
            / f"{BENCHMARK_STAGE_PREFIX}synthetic-cleanup-authority"
        )
        token = "c" * 64
        tree = _CleanupAuthorityTreeProbe(
            fail_live_verification=fail_live_verification,
            fail_namespace_verification=fail_namespace_verification,
        )
        authority = _StageAuthority(
            cleanup_token=token,
            parent=root.parent,
            tree=tree,
        )
        return root, _synthetic_staged_resources(root, token), authority, tree

    def test_sanitizer_accepts_only_content_free_identifiers_and_measurements(self) -> None:
        report: dict[str, object] = {
            "status": "complete",
            "renderer_id": "pypdfium2",
            "declared_published_artifact_sha256": "a" * 64,
            "elapsed_ns": 42,
        }

        assert_sanitized_report(report)

    def test_sanitizer_rejects_paths_filenames_and_document_content_channels(self) -> None:
        unsafe_values: tuple[dict[str, object], ...] = (
            {"source_path": "hidden"},
            {"value": "C:\\private\\source"},
            {"value": "synthetic.pdf"},
            {"document_content": "hidden"},
        )
        for report in unsafe_values:
            with self.subTest(report=report), self.assertRaises(BenchmarkFailure) as raised:
                assert_sanitized_report(report)
            self.assertEqual("PDF_RENDER_BENCHMARK_REPORT_UNSAFE", raised.exception.code)

    def test_failure_report_is_stable_and_sanitized(self) -> None:
        report = _failure_report(
            BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            )
        )

        assert_sanitized_report(report)
        self.assertEqual(
            OUTCOME_DEPENDENCY_UNAVAILABLE, cast(dict[str, object], report["error"])["code"]
        )
        self.assertNotIn(str(ROOT), json.dumps(report))

    def test_failure_report_canonicalizes_adversarial_code_and_message_without_raising(
        self,
    ) -> None:
        report = _failure_report(BenchmarkFailure("C:\\private\\code", "C:\\private\\source.pdf"))

        assert_sanitized_report(report)
        error = cast(dict[str, object], report["error"])
        self.assertEqual(OUTCOME_BENCHMARK_FAILED, error["code"])
        self.assertEqual("renderer benchmark failed", error["message"])
        self.assertNotIn("private", json.dumps(report))

    def test_failure_report_preserves_primary_and_secondary_cleanup_evidence(self) -> None:
        failure = BenchmarkFailure(
            OUTCOME_DEPENDENCY_UNAVAILABLE,
            "a required renderer dependency is unavailable",
        )
        failure.note_cleanup_failure()

        report = _failure_report(failure)

        self.assertEqual(
            OUTCOME_DEPENDENCY_UNAVAILABLE,
            cast(dict[str, object], report["error"])["code"],
        )
        self.assertEqual(
            OUTCOME_CLEANUP_FAILED,
            cast(dict[str, object], report["cleanup"])["code"],
        )

    def test_child_error_requires_an_exact_stable_code_and_message_pair(self) -> None:
        correlation_id = "1" * 32
        payload = {
            "protocol_version": 1,
            "event": "error",
            "correlation_id": correlation_id,
            "code": OUTCOME_DEPENDENCY_UNAVAILABLE,
            "message": "C:\\private\\source.pdf",
        }
        encoded = json.dumps(payload).encode("utf-8")

        with self.assertRaises(BenchmarkFailure) as raised:
            _decode_transport_payload(
                encoded,
                expected_event="probe",
                renderer_id="pypdfium2",
                correlation_id=correlation_id,
            )

        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)
        self.assertNotIn("private", str(raised.exception))

    def test_capture_preserves_primary_failure_when_cleanup_also_fails(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
            "renderer child protocol is invalid",
        )

        class _Transport:
            def send_payload(self, _payload: bytes) -> None:
                return None

        class _Process:
            transport = _Transport()

            def close(self) -> None:
                raise RuntimeError("cleanup detail must not replace primary")

        session = SimpleNamespace(
            process=_Process(),
            correlation_id="1" * 32,
            isolated_worker_ready_elapsed_ns=1,
            process_affinity_count=workload.logical_processor_limit,
        )
        with (
            patch(
                "tools.pdf_renderer_benchmark._start_isolated_child",
                return_value=session,
            ),
            patch("tools.pdf_renderer_benchmark._receive_transport", side_effect=primary),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            _capture_child(
                "pypdfium2",
                "probe",
                1_000,
                cast(StagedBenchmarkResources, object()),
                workload,
            )

        self.assertIs(primary, raised.exception)
        self.assertTrue(primary.cleanup_failed)

    def test_post_revoke_seal_failure_is_secondary_to_the_primary_failure(self) -> None:
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
            "renderer child protocol is invalid",
        )
        events: list[str] = []

        class _Process:
            def close(self) -> None:
                events.append("close-and-revoke")

        def reject_seal(_resources: StagedBenchmarkResources) -> None:
            self.assertEqual(["close-and-revoke"], events)
            events.append("verify-seal")
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_STAGE_INVALID",
                "benchmark stage audit failed",
            )

        with patch(
            "tools.pdf_renderer_benchmark._require_staged_namespace_sealed",
            side_effect=reject_seal,
        ):
            _close_and_verify_isolated_process(
                _Process(),
                cast(StagedBenchmarkResources, object()),
                primary,
            )

        self.assertEqual(["close-and-revoke", "verify-seal"], events)
        self.assertTrue(primary.cleanup_failed)

    def test_close_failure_permanently_withdraws_stage_cleanup_authority(self) -> None:
        root, resources, authority, tree = self._cleanup_authority_fixture()
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
            "renderer child protocol is invalid",
        )

        class _FailingClose:
            def close(self) -> None:
                raise RuntimeError("private close failure")

        class _SuccessfulClose:
            def close(self) -> None:
                return None

        with patch(
            "tools.pdf_renderer_benchmark._registered_stage_entry",
            return_value=(root, authority),
        ):
            _close_and_verify_isolated_process(_FailingClose(), resources, primary)
            self.assertFalse(authority.cleanup_authorized)
            self.assertTrue(primary.cleanup_failed)

            # A second already-live child cannot regenerate authority after a clean close.
            _close_and_verify_isolated_process(_SuccessfulClose(), resources, None)
            self.assertFalse(authority.cleanup_authorized)
            with self.assertRaises(BenchmarkFailure):
                _require_stage_cleanup_authorized(resources)
            for _attempt in range(2):
                with self.assertRaises(BenchmarkFailure):
                    _remove_benchmark_stage_root(root, resources.cleanup_token)

        self.assertEqual(0, tree.delete_calls)
        self.assertEqual(0, tree.close_calls)
        self.assertEqual(0, tree.restore_calls)
        self.assertEqual(
            OUTCOME_CLEANUP_FAILED,
            cast(dict[str, object], _failure_report(primary)["cleanup"])["code"],
        )

    def test_post_revoke_verification_failure_withdraws_before_stage_mutation(self) -> None:
        root, resources, authority, tree = self._cleanup_authority_fixture(
            fail_namespace_verification=True
        )
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
            "renderer child protocol is invalid",
        )

        class _Process:
            def close(self) -> None:
                return None

        with patch(
            "tools.pdf_renderer_benchmark._registered_stage_entry",
            return_value=(root, authority),
        ):
            _close_and_verify_isolated_process(_Process(), resources, primary)
            self.assertFalse(authority.cleanup_authorized)
            self.assertTrue(primary.cleanup_failed)
            with self.assertRaises(BenchmarkFailure):
                _remove_benchmark_stage_root(root, resources.cleanup_token)

        self.assertEqual(0, tree.delete_calls)
        self.assertEqual(0, tree.close_calls)
        self.assertEqual(0, tree.restore_calls)

    def test_live_tree_verification_failure_cannot_block_sticky_withdrawal(self) -> None:
        root, resources, authority, tree = self._cleanup_authority_fixture(
            fail_live_verification=True
        )
        primary = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID",
            "renderer child protocol is invalid",
        )

        class _Process:
            def close(self) -> None:
                return None

        with benchmark_module._STAGE_AUTHORITIES_LOCK:
            previous = benchmark_module._STAGE_AUTHORITIES.get(root)
            benchmark_module._STAGE_AUTHORITIES[root] = authority
        try:
            _close_and_verify_isolated_process(_Process(), resources, primary)
            self.assertFalse(authority.cleanup_authorized)
            self.assertTrue(primary.cleanup_failed)
            for _attempt in range(2):
                with self.assertRaises(BenchmarkFailure):
                    _remove_benchmark_stage_root(root, resources.cleanup_token)
        finally:
            with benchmark_module._STAGE_AUTHORITIES_LOCK:
                if previous is None:
                    benchmark_module._STAGE_AUTHORITIES.pop(root, None)
                else:
                    benchmark_module._STAGE_AUTHORITIES[root] = previous

        self.assertEqual(1, tree.require_live_calls)
        self.assertEqual(0, tree.delete_calls)
        self.assertEqual(0, tree.close_calls)
        self.assertEqual(0, tree.restore_calls)

    def test_base_exception_during_close_withdraws_before_reraising(self) -> None:
        root, resources, authority, tree = self._cleanup_authority_fixture()

        class _InterruptedClose:
            def close(self) -> None:
                raise KeyboardInterrupt

        with (
            patch(
                "tools.pdf_renderer_benchmark._registered_stage_entry",
                return_value=(root, authority),
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            _close_and_verify_isolated_process(_InterruptedClose(), resources, None)

        self.assertFalse(authority.cleanup_authorized)
        self.assertEqual(0, tree.delete_calls)
        self.assertEqual(0, tree.close_calls)
        self.assertEqual(0, tree.restore_calls)

    def test_unexpected_primary_preserves_stage_cleanup_failure_evidence(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        private_detail = "C:\\private\\unexpected-stage-detail"
        resources = cast(StagedBenchmarkResources, object())
        cleanup_failure = BenchmarkFailure(
            OUTCOME_CLEANUP_FAILED,
            "benchmark cleanup failed",
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "tools.pdf_renderer_benchmark._repository_revision",
                return_value="a" * 40,
            ),
            patch(
                "tools.pdf_renderer_benchmark._windows_runtime_identity",
                return_value={},
            ),
            patch(
                "tools.pdf_renderer_benchmark.create_benchmark_stage_root",
                return_value=Path(temporary) / "stage",
            ),
            patch(
                "tools.pdf_renderer_benchmark.stage_benchmark_resources",
                return_value=resources,
            ),
            patch("tools.pdf_renderer_benchmark._require_same_repository_revision"),
            patch(
                "tools.pdf_renderer_benchmark._capture_child",
                side_effect=RuntimeError(private_detail),
            ),
            patch(
                "tools.pdf_renderer_benchmark.cleanup_benchmark_resources",
                side_effect=cleanup_failure,
            ),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            benchmark_report(
                workload,
                work_directory=Path(temporary),
            )

        self.assertEqual(OUTCOME_BENCHMARK_FAILED, raised.exception.code)
        self.assertTrue(raised.exception.cleanup_failed)
        self.assertNotIn("private", str(raised.exception))
        failure_report = _failure_report(raised.exception)
        self.assertEqual(
            OUTCOME_CLEANUP_FAILED,
            cast(dict[str, object], failure_report["cleanup"])["code"],
        )

    def test_capture_and_control_modes_reject_affinity_outside_budget_before_command(
        self,
    ) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None

        events: list[str] = []

        class _Process:
            def close(self) -> None:
                events.append("close-and-revoke")

        def verify_seal(_resources: StagedBenchmarkResources) -> None:
            self.assertEqual(["close-and-revoke"], events)
            events.append("verify-seal")

        def capture() -> object:
            return _capture_child(
                "pypdfium2",
                "probe",
                1_000,
                cast(StagedBenchmarkResources, object()),
                workload,
            )

        def cancel() -> object:
            return _measure_cooperative_cancellation(
                "pypdfium2",
                workload,
                cast(StagedBenchmarkResources, object()),
            )

        def terminate() -> object:
            return _measure_idle_worker_tree_termination(
                "pypdfium2",
                workload,
                cast(StagedBenchmarkResources, object()),
            )

        for label, operation in (
            ("capture", capture),
            ("cooperative-cancel", cancel),
            ("idle-termination", terminate),
        ):
            events.clear()
            session = SimpleNamespace(
                process=_Process(),
                correlation_id="1" * 32,
                isolated_worker_ready_elapsed_ns=1,
                process_affinity_count=64,
            )
            with (
                self.subTest(label=label),
                patch(
                    "tools.pdf_renderer_benchmark._start_isolated_child",
                    return_value=session,
                ),
                patch(
                    "tools.pdf_renderer_benchmark._require_staged_namespace_sealed",
                    side_effect=verify_seal,
                ),
                patch("tools.pdf_renderer_benchmark._send_transport") as send,
                self.assertRaises(BenchmarkFailure) as raised,
            ):
                operation()

            self.assertEqual(2, workload.logical_processor_limit)
            self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)
            self.assertEqual(["close-and-revoke", "verify-seal"], events)
            send.assert_not_called()

    def test_renderer_dependencies_are_only_dynamically_imported(self) -> None:
        source = (ROOT / "tools/pdf_renderer_benchmark.py").read_text(encoding="utf-8")
        parsed = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(parsed)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        self.assertNotIn("pypdfium2", imported)
        self.assertNotIn("PySide6", imported)
        self.assertIn('import_module("PySide6.QtPdf")', source)
        self.assertIn("QPdfDocumentRenderOptions", source)
        self.assertIn("QCoreApplication", source)

    def test_qt_dependency_probe_fails_closed_without_import_fallback(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        specification = workload.renderers[1]

        from unittest.mock import patch

        with (
            patch(
                "tools.pdf_renderer_benchmark.importlib.metadata.version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            _QtPdfRuntime(specification)

        self.assertEqual(OUTCOME_DEPENDENCY_UNAVAILABLE, raised.exception.code)

    def test_qt_records_distinct_media_box_selection_as_public_api_limit(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        media_case = _semantic_render_case(workload.semantic_cases[1])
        runtime = object.__new__(_QtPdfRuntime)

        with self.assertRaises(_StableEngineError) as raised:
            runtime.render_page(object(), media_case)

        self.assertEqual(OUTCOME_PAGE_BOX_UNSUPPORTED, raised.exception.code)


@unittest.skipUnless(sys.platform == "win32", "Windows launcher evidence requires Windows")
class PdfRendererBenchmarkReadyAffinityTests(unittest.TestCase):
    def test_ready_rejects_64_processors_for_the_two_processor_budget(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        closed = 0
        seal_states: list[bool] = []

        class _Process:
            isolation = SimpleNamespace(satisfies_required_boundary=True)

            def close(self) -> None:
                nonlocal closed
                closed += 1

        process = _Process()

        def verify_seal(_resources: StagedBenchmarkResources) -> None:
            seal_states.append(bool(closed))

        resources = SimpleNamespace(
            python_executable=Path(sys.executable),
            bootstrap_script=ROOT / "tools" / "pdf_renderer_benchmark_bootstrap.py",
            runtime_root=Path(sys.base_prefix),
            stage_root=ROOT,
            code_root=ROOT,
            manifest_sha256="a" * 64,
        )
        ready = {
            "protocol_version": 1,
            "event": "isolated-worker-ready",
            "correlation_id": "1" * 32,
            "workload_sha256": workload.workload_sha256,
            "process_affinity_count": 64,
            "isolation": {
                "app_container": True,
                "zero_capabilities": True,
                "in_job": True,
                "minimal_launch_environment": True,
                "standard_handles_non_inheritable": True,
            },
        }
        with (
            patch("paperworkshell.worker.client._windows.WindowsLaunchConfig"),
            patch("paperworkshell.worker.client._windows.WindowsProcessLauncher") as launcher_type,
            patch("tools.pdf_renderer_benchmark._send_transport"),
            patch("tools.pdf_renderer_benchmark._receive_transport", return_value=b""),
            patch(
                "tools.pdf_renderer_benchmark._decode_transport_payload",
                return_value=ready,
            ),
            patch(
                "tools.pdf_renderer_benchmark._require_staged_namespace_sealed",
                side_effect=verify_seal,
            ),
            patch("tools.pdf_renderer_benchmark._require_stage_cleanup_authorized"),
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            launcher_type.return_value.launch.return_value = process
            _start_isolated_child(
                cast(StagedBenchmarkResources, resources),
                workload,
                1_000,
            )

        self.assertEqual(2, workload.logical_processor_limit)
        self.assertEqual("PDF_RENDER_BENCHMARK_PROTOCOL_INVALID", raised.exception.code)
        self.assertEqual(1, closed)
        self.assertEqual([False, False, True], seal_states)

    def test_launch_exception_withdraws_cleanup_before_any_stage_delete(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        root = (
            Path(tempfile.gettempdir()).resolve()
            / f"{BENCHMARK_STAGE_PREFIX}synthetic-launch-failure"
        )
        token = "d" * 64
        resources = _synthetic_staged_resources(root, token)
        tree = _CleanupAuthorityTreeProbe()
        authority = _StageAuthority(
            cleanup_token=token,
            parent=root.parent,
            tree=tree,
        )

        with (
            patch(
                "tools.pdf_renderer_benchmark._registered_stage_entry",
                return_value=(root, authority),
            ),
            patch("paperworkshell.worker.client._windows.WindowsLaunchConfig"),
            patch("paperworkshell.worker.client._windows.WindowsProcessLauncher") as launcher_type,
            self.assertRaises(BenchmarkFailure) as raised,
        ):
            launcher_type.return_value.launch.side_effect = RuntimeError("private launch failure")
            _start_isolated_child(resources, workload, 1_000)

        self.assertEqual("PDF_RENDER_BENCHMARK_CHILD_FAILED", raised.exception.code)
        self.assertTrue(raised.exception.cleanup_failed)
        self.assertNotIn("private", str(raised.exception))
        self.assertFalse(authority.cleanup_authorized)
        with (
            patch(
                "tools.pdf_renderer_benchmark._registered_stage_entry",
                return_value=(root, authority),
            ),
            self.assertRaises(BenchmarkFailure),
        ):
            _remove_benchmark_stage_root(root, token)
        self.assertEqual(0, tree.delete_calls)
        self.assertEqual(0, tree.close_calls)
        self.assertEqual(0, tree.restore_calls)


@unittest.skipUnless(sys.platform == "win32", "authenticated stage faults require Windows")
class PdfRendererBenchmarkStageFailureTests(unittest.TestCase):
    def test_open_failure_after_root_authentication_removes_only_exact_empty_root(
        self,
    ) -> None:
        fixed_drive = Path(Path(sys.base_prefix).anchor)
        work_root = Path(tempfile.mkdtemp(prefix="pws-stage-open-tests-", dir=fixed_drive))
        open_count = 0

        def fail_first_ancestor(
            path: Path,
            *,
            delete: bool = False,
            read: bool = False,
            share_delete: bool = False,
        ) -> Any:
            nonlocal open_count
            open_count += 1
            if open_count == 2:
                raise StageWindowsFailure("C:\\private\\ancestor")
            return storage_open_handle(
                path,
                delete=delete,
                read=read,
                share_delete=share_delete,
            )

        try:
            with (
                patch.object(stage_windows, "open_handle", side_effect=fail_first_ancestor),
                self.assertRaises(BenchmarkFailure) as raised,
            ):
                create_benchmark_stage_root(work_root)

            self.assertEqual("PDF_RENDER_BENCHMARK_CHILD_FAILED", raised.exception.code)
            self.assertFalse(raised.exception.cleanup_failed)
            self.assertNotIn("private", str(raised.exception))
            self.assertEqual([], list(work_root.iterdir()))
        finally:
            work_root.rmdir()

    def test_open_failure_without_root_authority_reports_residue_without_deleting_it(
        self,
    ) -> None:
        fixed_drive = Path(Path(sys.base_prefix).anchor)
        work_root = Path(tempfile.mkdtemp(prefix="pws-stage-open-tests-", dir=fixed_drive))
        try:
            with (
                patch.object(
                    PinnedStageTree,
                    "open",
                    side_effect=StageWindowsFailure("C:\\private\\before-authority"),
                ),
                self.assertRaises(BenchmarkFailure) as raised,
            ):
                create_benchmark_stage_root(work_root)

            self.assertEqual("PDF_RENDER_BENCHMARK_CHILD_FAILED", raised.exception.code)
            self.assertTrue(raised.exception.cleanup_failed)
            self.assertNotIn("private", str(raised.exception))
            residues = list(work_root.iterdir())
            self.assertEqual(1, len(residues))
            self.assertTrue(residues[0].is_dir())
            self.assertEqual([], list(residues[0].iterdir()))
            residues[0].rmdir()
        finally:
            work_root.rmdir()

    def test_retained_stage_accepts_and_authenticates_an_empty_git_blob(self) -> None:
        fixed_drive = Path(Path(sys.base_prefix).anchor)
        work_root = Path(tempfile.mkdtemp(prefix="pws-stage-empty-tests-", dir=fixed_drive))
        stage_root = Path(tempfile.mkdtemp(prefix=BENCHMARK_STAGE_PREFIX, dir=work_root))
        tree = PinnedStageTree.open(stage_root)
        try:
            tree.ensure_directory(PurePosixPath("code"))
            digest = tree.write_bytes(
                PurePosixPath("code/empty.py"),
                b"",
                maximum_bytes=1,
            )
            expected = {PurePosixPath("code/empty.py"): hashlib.sha256(b"").hexdigest()}

            self.assertEqual(expected[PurePosixPath("code/empty.py")], digest)
            tree.audit(expected, 1)
            tree.delete(expected, 1)
            self.assertFalse(stage_root.exists())
        finally:
            if stage_root.exists():
                tree.delete(None, 1)
            work_root.rmdir()

    def test_unexpected_stage_failure_is_sanitized_and_exact_root_is_cleaned(self) -> None:
        workload, issues = validate_workload()
        self.assertEqual([], issues)
        assert workload is not None
        fixed_drive = Path(Path(sys.base_prefix).anchor)
        work_root = Path(tempfile.mkdtemp(prefix="pws-stage-fault-tests-", dir=fixed_drive))
        stage_root = create_benchmark_stage_root(work_root)
        try:
            with (
                patch(
                    "tools.pdf_renderer_benchmark._read_git_blob",
                    side_effect=_current_worktree_blob,
                ),
                patch.object(
                    PinnedStageTree,
                    "ensure_directory",
                    side_effect=StageWindowsFailure("C:\\private\\unexpected"),
                ),
                self.assertRaises(BenchmarkFailure) as raised,
            ):
                stage_benchmark_resources(stage_root, workload, "a" * 40)

            self.assertEqual("PDF_RENDER_BENCHMARK_CHILD_FAILED", raised.exception.code)
            self.assertFalse(raised.exception.cleanup_failed)
            self.assertNotIn("private", str(raised.exception))
            self.assertFalse(stage_root.exists())
        finally:
            if work_root.exists():
                work_root.rmdir()


@unittest.skipUnless(sys.platform == "win32", "AppContainer benchmark requires Windows")
class PdfRendererBenchmarkWindowsChildTests(unittest.TestCase):
    workload: ClassVar[BenchmarkWorkload]
    work_root: ClassVar[Path]
    resources: ClassVar[StagedBenchmarkResources]

    @classmethod
    def setUpClass(cls) -> None:
        workload, issues = validate_workload()
        if issues or workload is None:
            raise AssertionError(issues)
        cls.workload = workload
        fixed_drive = Path(Path(sys.base_prefix).anchor)
        cls.work_root = Path(
            tempfile.mkdtemp(prefix="pws-render-benchmark-tests-", dir=fixed_drive)
        )

        try:
            stage_root = create_benchmark_stage_root(cls.work_root)
            with patch(
                "tools.pdf_renderer_benchmark._read_git_blob",
                side_effect=_current_worktree_blob,
            ):
                cls.resources = stage_benchmark_resources(
                    stage_root,
                    workload,
                    "a" * 40,
                )
        except BaseException:
            if cls.work_root.exists():
                try:
                    cls.work_root.rmdir()
                except OSError:
                    pass
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cleanup_benchmark_resources(cls.resources)
        cls.work_root.rmdir()

    def test_stage_is_hash_bound_immutable_and_excludes_live_repository(self) -> None:
        manifest = json.loads(self.resources.manifest_path.read_bytes())
        entries = cast(list[dict[str, object]], manifest["files"])
        expected = {
            relative.as_posix(): digest for relative, digest in self.resources.staged_file_sha256
        }

        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("a" * 40, manifest["repository_revision"])
        self.assertEqual(sorted(expected), [entry["path"] for entry in entries])
        self.assertEqual(expected, {entry["path"]: entry["sha256"] for entry in entries})
        self.assertIn(
            "code/src/paperworkshell/worker/protocol/operation_scope.py",
            expected,
        )
        self.assertNotIn(ROOT, self.resources.stage_root.parents)
        self.assertFalse(
            any(
                path == ROOT or ROOT in path.parents
                for path in (
                    self.resources.runtime_root,
                    self.resources.code_root,
                    self.resources.data_root,
                )
            )
        )
        for relative, _digest in self.resources.staged_file_sha256:
            target = self.resources.stage_root.joinpath(*relative.parts)
            self.assertEqual(0, target.stat().st_mode & stat.S_IWRITE)
        self.assertEqual(
            {"pypdfium2", "PySide6-Addons", "PySide6-Essentials", "shiboken6"},
            dict(self.resources.installed_record_sha256).keys(),
        )
        self.assertTrue(
            all(len(digest) == 64 for _name, digest in self.resources.installed_record_sha256)
        )

    def test_retained_handles_block_descendant_replacement_and_directory_swap(self) -> None:
        relative, _digest = self.resources.staged_file_sha256[0]
        target = self.resources.stage_root.joinpath(*relative.parts)
        replacement = self.work_root / "synthetic-replacement.tmp"
        replacement.write_bytes(b"synthetic replacement")
        moved_code = self.work_root / "moved-code"

        with self.assertRaises(OSError):
            os.replace(replacement, target)
        with self.assertRaises(OSError):
            self.resources.code_root.rename(moved_code)

        self.assertTrue(replacement.is_file())
        self.assertFalse(moved_code.exists())
        replacement.unlink()

    def test_unregistered_same_prefix_cleanup_target_is_never_deleted(self) -> None:
        arbitrary = self.work_root / f"{BENCHMARK_STAGE_PREFIX}unregistered"
        arbitrary.mkdir()
        canary = arbitrary / "canary.txt"
        canary.write_text("synthetic canary", encoding="ascii")

        with self.assertRaises(BenchmarkFailure):
            _remove_benchmark_stage_root(arbitrary, "0" * 64)

        self.assertTrue(canary.is_file())
        canary.unlink()
        arbitrary.rmdir()

    def test_sealed_namespace_denies_same_user_creation_in_every_directory(self) -> None:
        directories = [self.resources.stage_root]
        directories.extend(
            sorted(
                (path for path in self.resources.stage_root.rglob("*") if path.is_dir()),
                key=lambda path: path.as_posix(),
            )
        )
        self.assertGreater(len(directories), 3)

        for directory in directories:
            with self.subTest(directory=directory.relative_to(self.resources.stage_root)):
                tuple(directory.iterdir())
                file_probe = directory / "~pws-seal-file"
                directory_probe = directory / "~pws-seal-dir"
                self.assertFalse(file_probe.exists())
                self.assertFalse(directory_probe.exists())
                try:
                    file_probe.write_bytes(b"public synthetic probe")
                except OSError:
                    pass
                else:
                    file_probe.unlink()
                    self.fail("sealed stage allowed same-user file creation")
                try:
                    directory_probe.mkdir()
                except OSError:
                    pass
                else:
                    directory_probe.rmdir()
                    self.fail("sealed stage allowed same-user directory creation")

        _require_staged_namespace_sealed(self.resources)

    def test_both_candidates_run_in_live_staged_appcontainers(self) -> None:
        for specification in self.workload.renderers:
            with self.subTest(renderer=specification.renderer_id):
                capture = _capture_child(
                    specification.renderer_id,
                    "measure",
                    self.workload.measurement.child_timeout_milliseconds,
                    self.resources,
                    self.workload,
                )
                result = _validated_measurement_result(
                    capture,
                    specification,
                    self.workload,
                )
                _require_staged_namespace_sealed(self.resources)
                resources = cast(dict[str, int], result["resources"])

                self.assertEqual(
                    119,
                    cast(dict[str, Any], result["warm"])["sample_count"],
                )
                self.assertGreater(
                    cast(dict[str, int], result["cold"])["isolated_worker_ready_elapsed_ns"],
                    0,
                )
                self.assertGreater(resources["peak_process_memory_used_bytes"], 0)
                self.assertGreaterEqual(
                    resources["peak_job_memory_used_bytes"],
                    resources["peak_process_memory_used_bytes"],
                )
                self.assertEqual(
                    self.workload.job_memory_limit_bytes,
                    resources["job_memory_limit_bytes"],
                )

    def test_namespace_denial_remains_effective_during_each_live_profile_lease(
        self,
    ) -> None:
        directories = [self.resources.stage_root]
        directories.extend(
            sorted(
                (path for path in self.resources.stage_root.rglob("*") if path.is_dir()),
                key=lambda path: path.as_posix(),
            )
        )
        self.assertGreater(len(directories), 3)

        for specification in self.workload.renderers:
            started = time.perf_counter_ns()
            session = _start_isolated_child(
                self.resources,
                self.workload,
                self.workload.measurement.child_timeout_milliseconds,
            )
            process = session.process
            primary_failure: BaseException | None = None
            try:
                self.assertIsNone(process.poll())
                for directory in directories:
                    with self.subTest(
                        renderer=specification.renderer_id,
                        directory=directory.relative_to(self.resources.stage_root),
                    ):
                        tuple(directory.iterdir())
                        file_probe = directory / "~pws-lf"
                        directory_probe = directory / "~pws-ld"
                        self.assertFalse(file_probe.exists())
                        self.assertFalse(directory_probe.exists())
                        with self.assertRaises(PermissionError) as file_denied:
                            file_probe.write_bytes(b"public synthetic live-lease probe")
                        with self.assertRaises(PermissionError) as directory_denied:
                            directory_probe.mkdir()
                        self.assertIn(
                            file_denied.exception.errno,
                            {errno.EACCES, errno.EPERM},
                        )
                        self.assertIn(
                            directory_denied.exception.errno,
                            {errno.EACCES, errno.EPERM},
                        )

                _send_transport(
                    process,
                    {
                        "protocol_version": 1,
                        "command": "run",
                        "mode": "probe",
                        "renderer_id": specification.renderer_id,
                        "correlation_id": session.correlation_id,
                    },
                )
                payload = _decode_transport_payload(
                    _receive_transport(
                        process,
                        self.workload.measurement.child_timeout_milliseconds,
                    ),
                    expected_event="probe",
                    renderer_id=specification.renderer_id,
                    correlation_id=session.correlation_id,
                )
                limits = _finish_isolated_child(
                    session,
                    self.workload,
                    self.workload.measurement.child_timeout_milliseconds,
                )
                capture = ChildCapture(
                    payload=payload,
                    correlation_id=session.correlation_id,
                    isolated_worker_ready_elapsed_ns=session.isolated_worker_ready_elapsed_ns,
                    parent_observed_elapsed_ns=time.perf_counter_ns() - started,
                    process_affinity_count=session.process_affinity_count,
                    process_memory_limit_bytes=limits[0],
                    job_memory_limit_bytes=limits[1],
                    peak_process_memory_used_bytes=limits[2],
                    peak_job_memory_used_bytes=limits[3],
                )
                identity = _validate_probe(capture, specification)
                self.assertEqual(specification.package, identity["package"])
            except BaseException as error:
                primary_failure = error
                raise
            finally:
                _close_and_verify_isolated_process(
                    process,
                    self.resources,
                    primary_failure,
                )
            _require_staged_namespace_sealed(self.resources)

    def test_both_candidates_cancel_and_idle_tree_terminate(self) -> None:
        for specification in self.workload.renderers:
            with self.subTest(renderer=specification.renderer_id):
                cancellation = _measure_cooperative_cancellation(
                    specification.renderer_id,
                    self.workload,
                    self.resources,
                )
                termination = _measure_idle_worker_tree_termination(
                    specification.renderer_id,
                    self.workload,
                    self.resources,
                )

                self.assertEqual("PDF_RENDER_CANCELLED", cancellation["outcome_code"])
                self.assertEqual("between-native-renders", cancellation["checkpoint"])
                self.assertGreater(cast(int, cancellation["latency_ns"]), 0)
                self.assertEqual(
                    self.workload.job_memory_limit_bytes,
                    cancellation["job_memory_limit_bytes"],
                )
                self.assertEqual("PDF_RENDER_JOB_TERMINATED", termination["outcome_code"])
                self.assertEqual("windows-appcontainer-job-tree", termination["boundary"])
                self.assertNotEqual(0, termination["termination_exit_code"])
                self.assertGreater(cast(int, termination["latency_ns"]), 0)

    def test_live_transport_rejects_malformed_and_oversized_payloads(self) -> None:
        malformed = _start_isolated_child(self.resources, self.workload, 30_000)
        try:
            malformed.process.transport.send_payload(b"{")
            response = _receive_transport(malformed.process, 5_000)
            with self.assertRaises(BenchmarkFailure) as raised:
                _decode_transport_payload(
                    response,
                    expected_event="probe",
                    renderer_id="pypdfium2",
                    correlation_id=malformed.correlation_id,
                )
            self.assertEqual("PDF_RENDER_BENCHMARK_CHILD_FAILED", raised.exception.code)
        finally:
            malformed.process.close()

        oversized = _start_isolated_child(self.resources, self.workload, 30_000)
        try:
            with self.assertRaises(WorkerClientError):
                oversized.process.transport.send_payload(b"x" * (MAX_FRAME_PAYLOAD_BYTES + 1))
        finally:
            oversized.process.close()
