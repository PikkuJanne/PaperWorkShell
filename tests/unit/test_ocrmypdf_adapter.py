"""Focused tests for the lazy, allowlisted OCRmyPDF adapter boundary."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import ocrmypdf
import pikepdf
import pytest
from ocrmypdf._pipelines import ocr as _ocrmypdf_pipeline
from PIL import Image

from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.ocr.ocrmypdf_adapter import (
    OcrAdapterProgressPhase,
    OcrMyPdfAdapter,
    _build_plugin_manager,
    _LazyOcrMyPdfBoundary,
    _private_ocrmypdf_temp_directory,
    _temporary_tree_bytes,
    _TemporaryStorageMonitor,
)
from paperworkshell.ocr.tesseract_runner import TesseractCommandRunner


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled = cancelled
        self.expired = expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


class _FakeRunner:
    def __init__(self, root: Path) -> None:
        self.tessdata_directory = root / "tessdata"
        self.tessdata_directory.mkdir()
        self.arguments: list[tuple[str, ...]] = []

    def version(self) -> str:
        return "5.5.3"

    def languages(self) -> frozenset[str]:
        return frozenset({"eng"})

    def run(self, arguments: tuple[str, ...], **kwargs: object) -> object:
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["check"] is True
        self.arguments.append(arguments)
        prefix = Path(arguments[-3])
        output_type = arguments[-2]
        if output_type == "hocr":
            prefix.with_suffix(".hocr").write_text("<html></html>", encoding="utf-8")
        elif output_type == "pdf":
            _write_pdf(prefix.with_suffix(".pdf"))
        prefix.with_suffix(".txt").write_text("synthetic", encoding="utf-8")
        return object()


class _Boundary:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.calls = 0

    def process(self, source_path: Path, output_path: Path, **kwargs: object) -> tuple[str, str]:
        assert source_path.is_file()
        self.calls += 1
        if self.valid:
            _write_pdf(output_path)
        else:
            output_path.write_bytes(b"not a pdf")
        return "17.10.0", "5.5.3"


def _write_pdf(path: Path) -> None:
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(72, 72))
        pdf.save(path)


def test_lazy_boundary_uses_only_allowlisted_plugins_and_fixed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_pdf(source)
    runner = _FakeRunner(tmp_path)
    captured: dict[str, object] = {}
    canary = "recognized-private-token"
    ambient_profile = tmp_path.parent / "ambient-profile-canary"
    monkeypatch.setenv("USERPROFILE", os.fspath(ambient_profile))
    monkeypatch.setenv("HOME", os.fspath(ambient_profile / "home"))
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(ambient_profile / "local"))
    monkeypatch.setenv("APPDATA", os.fspath(ambient_profile / "roaming"))
    monkeypatch.setenv("PATH", os.fspath(ambient_profile / "bin"))
    original_tesseract_run = __import__("ocrmypdf._exec.tesseract", fromlist=["run"]).run
    original_mkdtemp = _ocrmypdf_pipeline.__dict__["mkdtemp"]
    original_manage_work_folder = _ocrmypdf_pipeline.__dict__["manage_work_folder"]

    def fake_ocr(input_path: Path, output_path: Path, **kwargs: object) -> int:
        assert input_path == source
        assert Path.home() == tmp_path
        assert os.environ["USERPROFILE"] == os.fspath(tmp_path)
        assert not {"HOME", "LOCALAPPDATA", "APPDATA", "PATH"} & os.environ.keys()
        assert all(
            not Path(value).is_relative_to(ambient_profile)
            for value in os.environ.values()
            if Path(value).is_absolute()
        )
        from ocrmypdf.font.system_font_provider import SystemFontProvider

        font_directories = SystemFontProvider()._get_font_dirs()
        assert all(not path.is_relative_to(ambient_profile) for path in font_directories)
        with tempfile.TemporaryDirectory(prefix="ocrmypdf.io.") as generated:
            generated_path = Path(generated)
            assert generated_path.parent == tmp_path
            assert generated_path.name.startswith("o")
            assert len(generated_path.name) == 17
        generated_work = Path(_ocrmypdf_pipeline.__dict__["mkdtemp"](prefix="ocrmypdf.io."))
        assert generated_work.parent == tmp_path
        assert generated_work.name.startswith("o")
        assert len(generated_work.name) == 17
        manage_work_folder = cast(Any, _ocrmypdf_pipeline.__dict__["manage_work_folder"])
        with manage_work_folder(
            work_folder=generated_work,
            retain=False,
            print_location=False,
        ):
            _write_pdf(generated_work / "origin.pdf")
        # Cleanup is deliberately deferred until this OCR API frame returns.
        assert generated_work.is_dir()
        captured["leaked_document"] = pikepdf.Pdf.open(generated_work / "origin.pdf")
        captured["generated_work"] = generated_work
        captured.update(kwargs)
        manager = cast(Any, kwargs["plugin_manager"])
        names = {name for name, _plugin in manager.pluggy_manager.list_name_plugin()}
        assert names == {
            "ocrmypdf.builtin_plugins.concurrency",
            "ocrmypdf.builtin_plugins.default_filters",
            "ocrmypdf.builtin_plugins.optimize",
            "ocrmypdf.builtin_plugins.pypdfium",
            "paperworkshell.ocr",
        }
        logging.getLogger("ocrmypdf.synthetic").warning(canary)
        _write_pdf(output_path)
        return 0

    monkeypatch.setattr(ocrmypdf, "ocr", fake_ocr)
    with caplog.at_level(logging.DEBUG):
        versions = _LazyOcrMyPdfBoundary().process(
            source,
            output,
            languages=("eng",),
            temporary_directory=tmp_path,
            runner=cast(TesseractCommandRunner, runner),
            total_pages=1,
            progress=lambda *_args: None,
            maximum_image_megapixels=10.0,
            tesseract_timeout_seconds=30.0,
        )

    assert versions == ("17.10.0", "5.5.3")
    assert captured["image_dpi"] == 300
    assert captured["output_type"] == "pdf"
    assert captured["rasterizer"] == "pypdfium"
    assert captured["pdf_renderer"] == "fpdf2"
    assert captured["optimize"] == 0
    assert captured["jobs"] == 1
    assert captured["use_threads"] is True
    assert captured["clean"] is False
    assert captured["clean_final"] is False
    assert captured["keep_temporary_files"] is False
    assert "plugins" not in captured
    assert canary not in caplog.text
    tesseract_module = __import__("ocrmypdf._exec.tesseract", fromlist=["run"])
    assert tesseract_module.run is original_tesseract_run
    assert _ocrmypdf_pipeline.__dict__["mkdtemp"] is original_mkdtemp
    assert _ocrmypdf_pipeline.__dict__["manage_work_folder"] is original_manage_work_folder
    assert not cast(Path, captured["generated_work"]).exists()
    assert type(captured["leaked_document"]) is pikepdf.Pdf


def test_deferred_work_tree_cleanup_failure_is_authoritative_and_restores_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mkdtemp = _ocrmypdf_pipeline.__dict__["mkdtemp"]
    original_manage_work_folder = _ocrmypdf_pipeline.__dict__["manage_work_folder"]
    original_rmtree = __import__("shutil").rmtree
    work_directory: Path | None = None

    def failing_rmtree(_path: object, **_kwargs: object) -> None:
        raise PermissionError

    monkeypatch.setattr("paperworkshell.ocr.ocrmypdf_adapter.shutil.rmtree", failing_rmtree)
    with pytest.raises(OcrError) as raised:
        with _private_ocrmypdf_temp_directory(tmp_path):
            work_directory = Path(_ocrmypdf_pipeline.__dict__["mkdtemp"](prefix="ocrmypdf.io."))
            manage_work_folder = cast(
                Any,
                _ocrmypdf_pipeline.__dict__["manage_work_folder"],
            )
            with manage_work_folder(
                work_folder=work_directory,
                retain=False,
                print_location=False,
            ):
                _write_pdf(work_directory / "origin.pdf")

    assert raised.value.code is OcrErrorCode.INTERNAL_ERROR
    assert raised.value.context == "ocr.ocrmypdf_temp_cleanup"
    assert _ocrmypdf_pipeline.__dict__["mkdtemp"] is original_mkdtemp
    assert _ocrmypdf_pipeline.__dict__["manage_work_folder"] is original_manage_work_folder
    assert work_directory is not None and work_directory.is_dir()
    original_rmtree(work_directory)


def test_supported_engine_builds_exact_hocr_argv_without_user_configuration(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(tmp_path)
    manager = cast(
        Any,
        _build_plugin_manager(
            ocrmypdf,
            lambda *_args: None,
            1,
            runner=cast(TesseractCommandRunner, runner),
            temporary_directory=tmp_path,
            timeout_seconds=20.0,
        ),
    )
    engine = cast(Any, manager.get_ocr_engine(options=None))
    input_image = tmp_path / "page.png"
    input_image.write_bytes(b"synthetic")
    output_hocr = tmp_path / "page.hocr"
    output_text = tmp_path / "sidecar.txt"

    engine.generate_hocr(
        input_image,
        output_hocr,
        output_text,
        SimpleNamespace(languages=["eng"]),
    )

    assert runner.arguments == [
        (
            "tesseract",
            "-l",
            "eng",
            os.fspath(input_image),
            os.fspath(output_hocr.with_suffix("")),
            "hocr",
            "txt",
        )
    ]
    assert output_hocr.is_file()
    assert output_text.read_text(encoding="utf-8") == "synthetic"


def test_real_ocrmypdf_api_accepts_closed_plugins_and_reaches_supported_engine(
    tmp_path: Path,
) -> None:
    """Guard the v17.10 option-model contract without requiring a real executable."""

    runner = _FakeRunner(tmp_path)
    manager = _build_plugin_manager(
        ocrmypdf,
        lambda *_args: None,
        1,
        runner=cast(TesseractCommandRunner, runner),
        temporary_directory=tmp_path,
        timeout_seconds=20.0,
    )
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_pdf(source)

    with pytest.raises(OcrError) as raised:
        ocrmypdf.ocr(
            source,
            output,
            language=("eng",),
            output_type="pdf",
            jobs=1,
            use_threads=True,
            mode="skip",
            optimize=0,
            pdf_renderer="fpdf2",
            rasterizer="pypdfium",
            progress_bar=False,
            plugin_manager=cast(Any, manager),
        )

    # The call passed option creation/validation and reached the custom engine;
    # the synthetic engine output is intentionally not valid hOCR.
    assert raised.value.context == "ocr.engine_path"


def test_real_ocrmypdf_api_accepts_image_without_dpi_under_fixed_profile(
    tmp_path: Path,
) -> None:
    """Prove the fixed image DPI reaches the engine instead of raising DpiError."""

    runner = _FakeRunner(tmp_path)
    manager = _build_plugin_manager(
        ocrmypdf,
        lambda *_args: None,
        1,
        runner=cast(TesseractCommandRunner, runner),
        temporary_directory=tmp_path,
        timeout_seconds=20.0,
    )
    source = tmp_path / "source.png"
    output = tmp_path / "output.pdf"
    Image.new("RGB", (64, 64), "white").save(source, format="PNG")
    assert "dpi" not in Image.open(source).info

    with pytest.raises(OcrError) as raised:
        ocrmypdf.ocr(
            source,
            output,
            language=("eng",),
            image_dpi=300,
            output_type="pdf",
            jobs=1,
            use_threads=True,
            mode="skip",
            optimize=0,
            pdf_renderer="fpdf2",
            rasterizer="pypdfium",
            progress_bar=False,
            plugin_manager=cast(Any, manager),
        )

    # The no-DPI image passed triage and reached the custom engine. Its
    # deliberately synthetic hOCR is rejected at the private-path boundary.
    assert raised.value.context == "ocr.engine_path"


def test_adapter_reopens_hashes_and_counts_candidate_pages(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_pdf(source)
    executable = tmp_path / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    executable.write_bytes(b"synthetic")
    tessdata.mkdir()
    boundary = _Boundary()
    progress: list[tuple[OcrAdapterProgressPhase, int, int]] = []
    adapter = OcrMyPdfAdapter(executable, tessdata, boundary=boundary)

    result = adapter.process(
        source,
        output,
        temporary_directory=tmp_path,
        languages=("eng",),
        profile_id="searchable-pdf-v1",
        expected_page_count=1,
        maximum_output_bytes=1024 * 1024,
        maximum_image_pixels=1_000_000,
        maximum_temporary_storage_bytes=1024 * 1024,
        control=_Control(),
        progress=lambda *values: progress.append(values),
    )

    assert result.byte_size == output.stat().st_size
    assert result.page_count == 1
    assert len(result.sha256) == 64
    assert result.ocrmypdf_version == "17.10.0"
    assert [item[0] for item in progress] == [
        OcrAdapterProgressPhase.PREPARING,
        OcrAdapterProgressPhase.FINALIZING,
        OcrAdapterProgressPhase.VALIDATING,
        OcrAdapterProgressPhase.COMPLETE,
    ]


def test_adapter_rejects_malformed_candidate_without_raw_exception(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_pdf(source)
    executable = tmp_path / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    executable.write_bytes(b"synthetic")
    tessdata.mkdir()
    adapter = OcrMyPdfAdapter(executable, tessdata, boundary=_Boundary(valid=False))

    with pytest.raises(OcrError) as raised:
        adapter.process(
            source,
            output,
            temporary_directory=tmp_path,
            languages=("eng",),
            profile_id="searchable-pdf-v1",
            expected_page_count=1,
            maximum_output_bytes=1024 * 1024,
            maximum_image_pixels=1_000_000,
            maximum_temporary_storage_bytes=1024 * 1024,
            control=_Control(),
            progress=lambda *_args: None,
        )

    assert raised.value.code is OcrErrorCode.OUTPUT_INVALID
    assert "not a pdf" not in str(raised.value)


def test_adapter_stops_before_library_on_cancellation(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_pdf(source)
    executable = tmp_path / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    executable.write_bytes(b"synthetic")
    tessdata.mkdir()
    boundary = _Boundary()
    adapter = OcrMyPdfAdapter(executable, tessdata, boundary=boundary)

    with pytest.raises(OcrError) as raised:
        adapter.process(
            source,
            output,
            temporary_directory=tmp_path,
            languages=("eng",),
            profile_id="searchable-pdf-v1",
            expected_page_count=1,
            maximum_output_bytes=1024 * 1024,
            maximum_image_pixels=1_000_000,
            maximum_temporary_storage_bytes=1024 * 1024,
            control=_Control(cancelled=True),
            progress=lambda *_args: None,
        )

    assert raised.value.code is OcrErrorCode.CANCELLED
    assert boundary.calls == 0
    assert not output.exists()


def test_temp_monitor_failure_overrides_engine_body_failure(tmp_path: Path) -> None:
    monitor = _TemporaryStorageMonitor(tmp_path, 1)
    monitor.failed.set()

    with pytest.raises(OcrError) as raised:
        with monitor:
            raise RuntimeError("synthetic engine failure")

    assert raised.value.code is OcrErrorCode.INTERNAL_ERROR
    assert raised.value.context == "ocr.temp_monitor"


def test_temp_snapshot_tolerates_only_entry_that_vanishes_after_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable = tmp_path / "stable.bin"
    vanishing = tmp_path / "vanishing.bin"
    stable.write_bytes(b"stable")
    vanishing.write_bytes(b"gone")
    real_lstat = os.lstat

    def racing_lstat(path: os.PathLike[str] | str) -> os.stat_result:
        if Path(path) == vanishing:
            raise FileNotFoundError
        return real_lstat(path)

    monkeypatch.setattr(
        "paperworkshell.ocr.ocrmypdf_adapter.os.lstat",
        racing_lstat,
    )

    assert _temporary_tree_bytes(tmp_path) == stable.stat().st_size


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_temp_snapshot_keeps_inventory_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[OSError],
) -> None:
    entry = tmp_path / "entry.bin"
    entry.write_bytes(b"synthetic")

    def failing_lstat(_path: os.PathLike[str] | str) -> os.stat_result:
        raise failure

    monkeypatch.setattr(
        "paperworkshell.ocr.ocrmypdf_adapter.os.lstat",
        failing_lstat,
    )

    with pytest.raises(failure):
        _temporary_tree_bytes(tmp_path)


@pytest.mark.parametrize(
    "file_mode,file_attributes",
    [
        (stat.S_IFREG, 0x0000_0400),
        (stat.S_IFIFO, 0),
    ],
)
def test_temp_snapshot_rejects_reparse_and_special_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_mode: int,
    file_attributes: int,
) -> None:
    entry = tmp_path / "entry.bin"
    entry.write_bytes(b"synthetic")

    def unsafe_lstat(_path: os.PathLike[str] | str) -> Any:
        return SimpleNamespace(
            st_mode=file_mode,
            st_size=entry.stat().st_size,
            st_file_attributes=file_attributes,
        )

    monkeypatch.setattr(
        "paperworkshell.ocr.ocrmypdf_adapter.os.lstat",
        unsafe_lstat,
    )

    with pytest.raises(OSError):
        _temporary_tree_bytes(tmp_path)


def test_temp_snapshot_and_monitor_enforce_entry_and_byte_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "first.bin").write_bytes(b"a")
    (tmp_path / "second.bin").write_bytes(b"b")
    monkeypatch.setattr(
        "paperworkshell.ocr.ocrmypdf_adapter._MAXIMUM_TEMP_ENTRIES",
        1,
    )

    with pytest.raises(OSError):
        _temporary_tree_bytes(tmp_path)

    monkeypatch.setattr(
        "paperworkshell.ocr.ocrmypdf_adapter._MAXIMUM_TEMP_ENTRIES",
        100_000,
    )
    monitor = _TemporaryStorageMonitor(tmp_path, 1)
    with pytest.raises(OcrError) as raised:
        with monitor:
            assert monitor.exceeded.wait(1.0)

    assert raised.value.code is OcrErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "ocr.temp_bytes"
