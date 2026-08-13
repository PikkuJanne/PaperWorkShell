"""Lazy OCRmyPDF orchestration behind a bounded worker-only adapter."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal, Protocol, cast

import ocrmypdf
from ocrmypdf import pluginspec as _ocrmypdf_pluginspec
from ocrmypdf._plugin_manager import OcrmypdfPluginManager as _OcrmypdfPluginManager
from ocrmypdf.builtin_plugins import concurrency as _ocrmypdf_concurrency
from ocrmypdf.builtin_plugins import default_filters as _ocrmypdf_default_filters
from ocrmypdf.builtin_plugins import optimize as _ocrmypdf_optimize
from ocrmypdf.builtin_plugins import pypdfium as _ocrmypdf_pypdfium
from pluggy import HookimplMarker
from pydantic import BaseModel, ConfigDict, Field

from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    OCR_ENGINE_IMAGE_DPI,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
    OcrErrorCode,
    reject_ocr,
)
from paperworkshell.ocr.tesseract_runner import (
    TesseractCommandRunner,
    TesseractOperationControl,
    TesseractRunnerError,
    TesseractRunnerErrorCode,
)
from paperworkshell.pdf.ocr_candidate_verifier import (
    OcrPdfCandidateVerificationError,
    verified_ocr_pdf_page_count,
)

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_PDF_MAGIC: Final = b"%PDF-"
_LANGUAGE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_ALLOWED_OCRMYPDF_PLUGINS: Final = (
    "ocrmypdf.builtin_plugins.concurrency",
    "ocrmypdf.builtin_plugins.default_filters",
    "ocrmypdf.builtin_plugins.optimize",
    "ocrmypdf.builtin_plugins.pypdfium",
)
_ALLOWED_OCRMYPDF_PLUGIN_MODULES: Final = (
    _ocrmypdf_concurrency,
    _ocrmypdf_default_filters,
    _ocrmypdf_optimize,
    _ocrmypdf_pypdfium,
)
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_HOOKIMPL: Final = HookimplMarker("ocrmypdf")
_TEMP_MONITOR_POLL_SECONDS: Final = 0.05
_TEMP_MONITOR_JOIN_SECONDS: Final = 1.0
_MAXIMUM_TEMP_ENTRIES: Final = 100_000
_OCRMY_PDF_VERSION: Final = TRUSTED_OCRMYPDF_VERSION
_TESSERACT_VERSION: Final = TRUSTED_TESSERACT_VERSION


class OcrAdapterProgressPhase(StrEnum):
    """Stable content-free phases emitted by the library adapter."""

    PREPARING = "preparing"
    RECOGNIZING = "recognizing"
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    COMPLETE = "complete"


class OcrAdapterProgressSink(Protocol):
    def __call__(
        self,
        phase: OcrAdapterProgressPhase,
        completed_pages: int,
        total_pages: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class OcrAdapterResult:
    """Content-free facts for one verified private OCR candidate."""

    byte_size: int
    page_count: int
    sha256: str = field(repr=False)
    ocrmypdf_version: str
    tesseract_version: str


class OcrMyPdfBoundary(Protocol):
    """Typed lazy seam around OCRmyPDF's changing Python API."""

    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        languages: tuple[str, ...],
        temporary_directory: Path,
        runner: TesseractCommandRunner,
        total_pages: int,
        progress: OcrAdapterProgressSink,
        maximum_image_megapixels: float,
        tesseract_timeout_seconds: float,
    ) -> tuple[str, str]: ...


@dataclass(frozen=True, slots=True, repr=False)
class OcrMyPdfAdapter:
    """Produce and verify one OCR candidate without exposing library objects."""

    tesseract_executable: Path
    tessdata_directory: Path
    boundary: OcrMyPdfBoundary = field(default_factory=lambda: _LazyOcrMyPdfBoundary(), repr=False)
    maximum_tool_output_bytes: int = 1024 * 1024
    tesseract_timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tesseract_executable, Path)
            or not self.tesseract_executable.is_absolute()
            or not isinstance(self.tessdata_directory, Path)
            or not self.tessdata_directory.is_absolute()
            or not callable(getattr(self.boundary, "process", None))
            or type(self.maximum_tool_output_bytes) is not int
            or not 1 <= self.maximum_tool_output_bytes <= (1 << 31)
            or type(self.tesseract_timeout_seconds) not in (int, float)
            or not math.isfinite(self.tesseract_timeout_seconds)
            or self.tesseract_timeout_seconds <= 0
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.adapter_configuration")

    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        temporary_directory: Path,
        languages: tuple[str, ...],
        profile_id: str,
        expected_page_count: int,
        maximum_output_bytes: int,
        maximum_image_pixels: int,
        maximum_temporary_storage_bytes: int,
        control: TesseractOperationControl,
        progress: OcrAdapterProgressSink,
    ) -> OcrAdapterResult:
        """Run the fixed OCR profile, then reopen and authenticate its PDF."""

        _validate_request(
            source_path,
            output_path,
            temporary_directory=temporary_directory,
            languages=languages,
            profile_id=profile_id,
            expected_page_count=expected_page_count,
            maximum_output_bytes=maximum_output_bytes,
            maximum_image_pixels=maximum_image_pixels,
            maximum_temporary_storage_bytes=maximum_temporary_storage_bytes,
            control=control,
            progress=progress,
        )
        _require_active(control)
        progress(OcrAdapterProgressPhase.PREPARING, 0, expected_page_count)
        monitor = _TemporaryStorageMonitor(
            temporary_directory,
            maximum_temporary_storage_bytes,
        )
        try:
            with monitor:
                runner = TesseractCommandRunner(
                    executable=self.tesseract_executable,
                    tessdata_directory=self.tessdata_directory,
                    temporary_directory=temporary_directory,
                    control=_MonitoredControl(control, monitor),
                    maximum_output_bytes=self.maximum_tool_output_bytes,
                )
                ocrmypdf_version, tesseract_version = self.boundary.process(
                    source_path,
                    output_path,
                    languages=languages,
                    temporary_directory=temporary_directory,
                    runner=runner,
                    total_pages=expected_page_count,
                    progress=_MonitoredProgress(progress, monitor),
                    maximum_image_megapixels=maximum_image_pixels / 1_000_000.0,
                    tesseract_timeout_seconds=float(self.tesseract_timeout_seconds),
                )
        except OcrError:
            raise
        except TesseractRunnerError as exc:
            _map_runner_error(exc)
        except ModuleNotFoundError:
            reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.ocrmypdf_dependency")
        except MemoryError, RecursionError:
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.ocrmypdf_resources")
        except Exception:
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.ocrmypdf_process")
        _require_active(control)
        progress(
            OcrAdapterProgressPhase.FINALIZING,
            expected_page_count,
            expected_page_count,
        )
        progress(
            OcrAdapterProgressPhase.VALIDATING,
            expected_page_count,
            expected_page_count,
        )
        byte_size, digest, page_count = _verify_candidate(
            output_path,
            temporary_directory=temporary_directory,
            maximum_output_bytes=maximum_output_bytes,
            expected_page_count=expected_page_count,
            control=control,
        )
        progress(
            OcrAdapterProgressPhase.COMPLETE,
            expected_page_count,
            expected_page_count,
        )
        return OcrAdapterResult(
            byte_size=byte_size,
            page_count=page_count,
            sha256=digest,
            ocrmypdf_version=_exact_version(
                ocrmypdf_version,
                _OCRMY_PDF_VERSION,
                "ocr.ocrmypdf_version",
            ),
            tesseract_version=_exact_version(
                tesseract_version,
                _TESSERACT_VERSION,
                "ocr.tesseract_version",
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class _LazyOcrMyPdfBoundary:
    """Import and configure OCRmyPDF only after entering the document worker."""

    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        languages: tuple[str, ...],
        temporary_directory: Path,
        runner: TesseractCommandRunner,
        total_pages: int,
        progress: OcrAdapterProgressSink,
        maximum_image_megapixels: float,
        tesseract_timeout_seconds: float,
    ) -> tuple[str, str]:
        plugin_manager = _build_plugin_manager(
            ocrmypdf,
            progress,
            total_pages,
            runner=runner,
            temporary_directory=temporary_directory,
            timeout_seconds=tesseract_timeout_seconds,
        )
        version = getattr(ocrmypdf, "__version__", None)
        ocr = getattr(ocrmypdf, "ocr", None)
        if type(version) is not str or not callable(ocr):
            reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.ocrmypdf_api")
        progress_proxy = _ProgressProxy(progress, total_pages)
        progress_proxy.emit_recognizing(0)
        with (
            _private_process_environment(temporary_directory, runner.tessdata_directory),
            _private_ocrmypdf_temp_directory(temporary_directory),
            _muted_library_logging(),
        ):
            exit_code = ocr(
                source_path,
                output_path,
                language=languages,
                image_dpi=OCR_ENGINE_IMAGE_DPI,
                output_type="pdf",
                jobs=1,
                use_threads=True,
                rotate_pages=False,
                remove_background=False,
                deskew=False,
                clean=False,
                clean_final=False,
                remove_vectors=False,
                mode="skip",
                optimize=0,
                pdf_renderer="fpdf2",
                rasterizer="pypdfium",
                max_image_mpixels=maximum_image_megapixels,
                fast_web_view=0,
                continue_on_soft_render_error=False,
                invalidate_digital_signatures=False,
                no_overwrite=False,
                keep_temporary_files=False,
                progress_bar=True,
                plugin_manager=plugin_manager,
            )
        if int(cast(Any, exit_code)) != 0:
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.ocrmypdf_exit")
        progress_proxy.emit_recognizing(total_pages)
        return version, runner.version()


@dataclass(slots=True)
class _ProgressProxy:
    sink: OcrAdapterProgressSink = field(repr=False)
    total_pages: int
    completed_pages: int = 0

    def emit_recognizing(self, completed: int) -> None:
        bounded = min(self.total_pages, max(self.completed_pages, completed))
        if bounded != self.completed_pages or bounded == 0:
            self.completed_pages = bounded
            self.sink(OcrAdapterProgressPhase.RECOGNIZING, bounded, self.total_pages)

    def progressbar_class(self) -> type[_OcrProgressBar]:
        proxy = self

        class _BoundProgressBar(_OcrProgressBar):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(proxy=proxy, **kwargs)

        return _BoundProgressBar


@dataclass(slots=True)
class _OcrProgressBar:
    proxy: _ProgressProxy
    total: int | float | None = None
    unit: str | None = None
    disable: bool = False
    current: float = 0.0

    def __init__(
        self,
        *,
        proxy: _ProgressProxy,
        total: int | float | None = None,
        unit: str | None = None,
        disable: bool = False,
        **kwargs: Any,
    ) -> None:
        del kwargs
        self.proxy = proxy
        self.total = total
        self.unit = unit
        self.disable = disable
        self.current = 0.0

    def __enter__(self) -> _OcrProgressBar:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        del args
        return False

    def update(self, n: float = 1, *, completed: float | None = None) -> None:
        if self.disable or self.unit != "page":
            return
        self.current = completed if completed is not None else self.current + n
        if math.isfinite(self.current):
            self.proxy.emit_recognizing(int(self.current))


def _build_plugin_manager(
    ocrmypdf: ModuleType,
    progress: OcrAdapterProgressSink,
    total_pages: int,
    *,
    runner: TesseractCommandRunner,
    temporary_directory: Path,
    timeout_seconds: float,
) -> object:
    """Construct an allowlist-only manager; never scan setuptools entry points."""

    manager_base = _OcrmypdfPluginManager
    ocr_engine_base = _ocrmypdf_pluginspec.OcrEngine
    orientation_confidence = _ocrmypdf_pluginspec.OrientationConfidence
    if not isinstance(manager_base, type) or not callable(getattr(ocrmypdf, "hookimpl", None)):
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.plugin_api")
    if not isinstance(ocr_engine_base, type) or not callable(orientation_confidence):
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.plugin_api")
    orientation_factory = cast(Callable[..., object], orientation_confidence)
    modules = _ALLOWED_OCRMYPDF_PLUGIN_MODULES
    proxy = _ProgressProxy(progress, total_pages)

    class _PaperWorkShellTesseractOptions(BaseModel):
        """Closed defaults required by OCRmyPDF's validation coordinator."""

        model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

        config: list[str] = Field(default_factory=list)
        pagesegmode: None = None
        oem: None = None
        thresholding: int = 0
        timeout: float = Field(default=timeout_seconds, gt=0)
        non_ocr_timeout: float = Field(default=timeout_seconds, gt=0)
        downsample_large_images: bool = True
        downsample_above: int = 32767
        user_words: None = None
        user_patterns: None = None
        omp_thread_limit: int = 1

    class _PaperWorkShellGhostscriptOptions(BaseModel):
        """Compatibility-only options; no Ghostscript hook is registered."""

        model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

        color_conversion_strategy: str = "LeaveColorUnchanged"
        pdfa_image_compression: str | None = None
        jpeg_quality: int | None = None
        jpeg_maxdpi: int | None = None

    class _PaperWorkShellOcrEngine(ocr_engine_base):  # type: ignore[misc, valid-type]
        """Supported OCRmyPDF engine hook backed only by the fixed runner."""

        def version(self) -> str:
            return runner.version()

        def creator_tag(self, options: object) -> str:
            del options
            return f"Tesseract OCR {runner.version()}"

        def __str__(self) -> str:
            return "PaperWorkShell local OCR engine"

        def languages(self, options: object) -> set[str]:
            del options
            return set(runner.languages())

        def get_orientation(self, input_file: Path, options: object) -> object:
            del input_file, options
            return orientation_factory(angle=0, confidence=0.0)

        def get_deskew(self, input_file: Path, options: object) -> float:
            del input_file, options
            return 0.0

        def generate_hocr(
            self,
            input_file: Path,
            output_hocr: Path,
            output_text: Path,
            options: object,
        ) -> None:
            languages = _option_languages(options)
            _require_operation_paths(
                (input_file, output_hocr, output_text),
                temporary_directory,
            )
            prefix = output_hocr.with_suffix("")
            arguments = (
                "tesseract",
                "-l",
                "+".join(languages),
                os.fspath(input_file),
                os.fspath(prefix),
                "hocr",
                "txt",
            )
            _run_recognition(runner, arguments, timeout_seconds)
            generated_text = prefix.with_suffix(".txt")
            if generated_text != output_text:
                _replace_private_file(generated_text, output_text, temporary_directory)
            _require_generated_files((output_hocr, output_text), temporary_directory)

        def generate_pdf(
            self,
            input_file: Path,
            output_pdf: Path,
            output_text: Path,
            options: object,
        ) -> None:
            languages = _option_languages(options)
            _require_operation_paths(
                (input_file, output_pdf, output_text),
                temporary_directory,
            )
            prefix = output_pdf.with_suffix("")
            arguments = (
                "tesseract",
                "-l",
                "+".join(languages),
                "-c",
                "textonly_pdf=1",
                os.fspath(input_file),
                os.fspath(prefix),
                "pdf",
                "txt",
            )
            _run_recognition(runner, arguments, timeout_seconds)
            generated_text = prefix.with_suffix(".txt")
            if generated_text != output_text:
                _replace_private_file(generated_text, output_text, temporary_directory)
            _require_generated_files((output_pdf, output_text), temporary_directory)

    class _AllowlistedManager(manager_base):  # type: ignore[misc, valid-type]
        def _setup_plugins(self) -> None:
            self._pm.add_hookspecs(_ocrmypdf_pluginspec)
            for name, module in zip(_ALLOWED_OCRMYPDF_PLUGINS, modules, strict=True):
                self._pm.register(module, name=name)

    class _PaperWorkShellPlugin:
        @_HOOKIMPL(tryfirst=True)
        def get_progressbar_class(self) -> type[_OcrProgressBar]:
            return proxy.progressbar_class()

        @_HOOKIMPL(tryfirst=True)
        def get_logging_console(self) -> logging.Handler:
            return logging.NullHandler()

        @_HOOKIMPL(tryfirst=True)
        def get_ocr_engine(self, options: object | None) -> object:
            del options
            return _PaperWorkShellOcrEngine()

        @_HOOKIMPL(tryfirst=True)
        def check_options(self, options: object) -> None:
            requested = _option_languages(options)
            installed = runner.languages()
            if not set(requested).issubset(installed):
                reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.languages")
            _require_fixed_tesseract_options(options, timeout_seconds)

        @_HOOKIMPL(tryfirst=True)
        def register_options(self) -> dict[str, type[BaseModel]]:
            return {
                "tesseract": _PaperWorkShellTesseractOptions,
                "ghostscript": _PaperWorkShellGhostscriptOptions,
            }

    try:
        manager = _AllowlistedManager(project_name="ocrmypdf", plugins=[], builtins=False)
        registered = manager.pluggy_manager.register(
            _PaperWorkShellPlugin(),
            name="paperworkshell.ocr",
        )
    except Exception:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.plugin_setup")
    if registered is None:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.plugin_setup")
    names = frozenset(name for name, _plugin in manager.pluggy_manager.list_name_plugin())
    expected = frozenset((*_ALLOWED_OCRMYPDF_PLUGINS, "paperworkshell.ocr"))
    if names != expected:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.plugin_allowlist")
    return manager


@contextmanager
def _private_process_environment(root: Path, tessdata: Path) -> Iterator[None]:
    saved_environment = dict(os.environ)
    saved_tempdir = tempfile.tempdir
    minimal: dict[str, str] = {}
    for name in ("SYSTEMROOT", "WINDIR"):
        value = saved_environment.get(name)
        if type(value) is str and value:
            minimal[name] = value
    minimal.update(
        {
            "TEMP": os.fspath(root),
            "TMP": os.fspath(root),
            # OCRmyPDF 17.10's fpdf2 renderer imports SystemFontProvider,
            # whose cross-platform directory table evaluates Path.home().
            # Bind that lookup to the authenticated operation root; never
            # restore the real user profile inside the document boundary.
            "USERPROFILE": os.fspath(root),
            "TESSDATA_PREFIX": os.fspath(tessdata),
            "OMP_THREAD_LIMIT": "1",
        }
    )
    try:
        os.environ.clear()
        os.environ.update(minimal)
        tempfile.tempdir = os.fspath(root)
        yield
    finally:
        tempfile.tempdir = saved_tempdir
        os.environ.clear()
        os.environ.update(saved_environment)


@contextmanager
def _private_ocrmypdf_temp_directory(root: Path) -> Iterator[None]:
    """Force OCRmyPDF's work tree below the authenticated operation root."""

    original = tempfile.TemporaryDirectory

    def private_temporary_directory(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
        requested = kwargs.get("dir")
        if requested is not None and Path(requested) != root:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.ocrmypdf_temp")
        kwargs["dir"] = root
        return original(*args, **kwargs)

    tempfile.__dict__["TemporaryDirectory"] = private_temporary_directory
    try:
        yield
    finally:
        tempfile.__dict__["TemporaryDirectory"] = original


@contextmanager
def _muted_library_logging() -> Iterator[None]:
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        yield
    finally:
        logging.disable(previous)


@dataclass(frozen=True, slots=True, repr=False)
class _MonitoredControl:
    delegate: TesseractOperationControl = field(repr=False)
    monitor: _TemporaryStorageMonitor = field(repr=False)

    def cancellation_requested(self) -> bool:
        return self.monitor.stop_requested.is_set() or self.delegate.cancellation_requested()

    def deadline_expired(self) -> bool:
        return self.delegate.deadline_expired()


@dataclass(frozen=True, slots=True, repr=False)
class _MonitoredProgress:
    delegate: OcrAdapterProgressSink = field(repr=False)
    monitor: _TemporaryStorageMonitor = field(repr=False)

    def __call__(
        self,
        phase: OcrAdapterProgressPhase,
        completed_pages: int,
        total_pages: int,
    ) -> None:
        self.monitor.require_ok()
        self.delegate(phase, completed_pages, total_pages)


class _TemporaryStorageMonitor:
    """Observe OCRmyPDF's private tree while Tesseract and Python stages run."""

    __slots__ = (
        "_maximum_bytes",
        "_root",
        "_thread",
        "exceeded",
        "failed",
        "stop_requested",
    )

    def __init__(self, root: Path, maximum_bytes: int) -> None:
        self._root = root
        self._maximum_bytes = maximum_bytes
        self.stop_requested = threading.Event()
        self.exceeded = threading.Event()
        self.failed = threading.Event()
        self._thread = threading.Thread(
            target=self._monitor,
            name="pws-ocr-temp-monitor",
            daemon=True,
        )

    def __enter__(self) -> _TemporaryStorageMonitor:
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> Literal[False]:
        self.stop_requested.set()
        self._thread.join(_TEMP_MONITOR_JOIN_SECONDS)
        if self._thread.is_alive():
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_monitor_cleanup")
        if _exc_type is None:
            self.require_ok()
        elif self.exceeded.is_set():
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_bytes")
        elif self.failed.is_set():
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_monitor")
        return False

    def require_ok(self) -> None:
        if self.exceeded.is_set():
            reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.temp_bytes")
        if self.failed.is_set():
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.temp_monitor")

    def _monitor(self) -> None:
        while not self.stop_requested.is_set():
            try:
                if _temporary_tree_bytes(self._root) > self._maximum_bytes:
                    self.exceeded.set()
                    self.stop_requested.set()
                    return
            except Exception:
                self.failed.set()
                self.stop_requested.set()
                return
            self.stop_requested.wait(_TEMP_MONITOR_POLL_SECONDS)


def _temporary_tree_bytes(root: Path) -> int:
    consumed = 0
    count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = tuple(iterator)
        except FileNotFoundError:
            # OCRmyPDF creates and removes nested work directories between
            # snapshots. The authenticated operation root itself is pinned
            # and must never disappear while monitoring is active.
            if directory == root:
                raise
            continue
        for entry in entries:
            count += 1
            if count > _MAXIMUM_TEMP_ENTRIES:
                raise OSError
            path = Path(entry.path)
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                # An enumerated private entry may be deleted by OCRmyPDF before
                # its metadata is sampled. It no longer consumes storage and is
                # safe to omit from this snapshot; all other errors fail closed.
                continue
            if _is_reparse(info):
                raise OSError
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                consumed += info.st_size
            else:
                raise OSError
    return consumed


def _option_languages(options: object) -> tuple[str, ...]:
    value = getattr(options, "languages", None)
    if not isinstance(value, list | tuple) or not 1 <= len(value) <= 16:
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.engine_languages")
    languages = tuple(value)
    if (
        any(type(language) is not str for language in languages)
        or len(set(languages)) != len(languages)
        or any(_LANGUAGE_TOKEN.fullmatch(language) is None for language in languages)
    ):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.engine_languages")
    return cast(tuple[str, ...], languages)


def _require_fixed_tesseract_options(options: object, timeout_seconds: float) -> None:
    value = getattr(options, "tesseract", None)
    if value is None or any(
        getattr(value, name, object()) != expected
        for name, expected in (
            ("config", []),
            ("pagesegmode", None),
            ("oem", None),
            ("thresholding", 0),
            ("timeout", timeout_seconds),
            ("non_ocr_timeout", timeout_seconds),
            ("downsample_large_images", True),
            ("downsample_above", 32767),
            ("user_words", None),
            ("user_patterns", None),
            ("omp_thread_limit", 1),
        )
    ):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.tesseract_options")


def _run_recognition(
    runner: TesseractCommandRunner,
    arguments: tuple[str, ...],
    timeout_seconds: float,
) -> None:
    try:
        runner.run(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=True,
        )
    except TesseractRunnerError as exc:
        _map_runner_error(exc)
    except subprocess.TimeoutExpired:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.tesseract_timeout")
    except subprocess.CalledProcessError:
        reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.tesseract_exit")
    except Exception:
        reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.tesseract_process")


def _require_operation_paths(paths: tuple[Path, ...], root: Path) -> None:
    for path in paths:
        if not isinstance(path, Path) or not path.is_absolute() or not _is_beneath(path, root):
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.engine_path")
        _require_no_reparse_ancestors(path.parent, root)


def _require_no_reparse_ancestors(path: Path, root: Path) -> None:
    current = path
    while True:
        try:
            info = current.stat(follow_symlinks=False)
        except OSError:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.engine_path")
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.engine_path")
        if current == root:
            return
        if current.parent == current:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.engine_path")
        current = current.parent


def _replace_private_file(source: Path, destination: Path, root: Path) -> None:
    _require_operation_paths((source, destination), root)
    try:
        source.replace(destination)
    except OSError:
        reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.engine_output")


def _require_generated_files(paths: tuple[Path, ...], root: Path) -> None:
    _require_operation_paths(paths, root)
    for path in paths:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.engine_output")
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.engine_output")


def _validate_request(
    source_path: Path,
    output_path: Path,
    *,
    temporary_directory: Path,
    languages: tuple[str, ...],
    profile_id: str,
    expected_page_count: int,
    maximum_output_bytes: int,
    maximum_image_pixels: int,
    maximum_temporary_storage_bytes: int,
    control: TesseractOperationControl,
    progress: OcrAdapterProgressSink,
) -> None:
    if (
        any(
            not isinstance(path, Path) or not path.is_absolute()
            for path in (source_path, output_path, temporary_directory)
        )
        or not _is_beneath(source_path, temporary_directory)
        or not _is_beneath(output_path, temporary_directory)
        or source_path == output_path
        or type(languages) is not tuple
        or not 1 <= len(languages) <= 16
        or len(set(languages)) != len(languages)
        or profile_id != DEFAULT_OCR_PROFILE_ID
        or type(expected_page_count) is not int
        or expected_page_count < 1
        or type(maximum_output_bytes) is not int
        or maximum_output_bytes < len(_PDF_MAGIC)
        or type(maximum_image_pixels) is not int
        or maximum_image_pixels < 1
        or type(maximum_temporary_storage_bytes) is not int
        or maximum_temporary_storage_bytes < 1
        or not callable(getattr(control, "cancellation_requested", None))
        or not callable(getattr(control, "deadline_expired", None))
        or not callable(progress)
    ):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.adapter_request")
    if any(_LANGUAGE_TOKEN.fullmatch(language) is None for language in languages):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.adapter_languages")
    try:
        source_info = source_path.stat(follow_symlinks=False)
    except OSError:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.adapter_source")
    if not stat.S_ISREG(source_info.st_mode) or _is_reparse(source_info):
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.adapter_source")


def _verify_candidate(
    output_path: Path,
    *,
    temporary_directory: Path,
    maximum_output_bytes: int,
    expected_page_count: int,
    control: TesseractOperationControl,
) -> tuple[int, str, int]:
    _require_active(control)
    if not _is_beneath(output_path, temporary_directory):
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_scope")
    try:
        info = output_path.stat(follow_symlinks=False)
    except OSError:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_missing")
    if (
        not stat.S_ISREG(info.st_mode)
        or _is_reparse(info)
        or not 1 <= info.st_size <= maximum_output_bytes
    ):
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_identity")
    digest = hashlib.sha256()
    count = 0
    try:
        with output_path.open("rb", buffering=0) as stream:
            magic = stream.read(len(_PDF_MAGIC))
            if magic != _PDF_MAGIC:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_magic")
            digest.update(magic)
            count += len(magic)
            while True:
                _require_active(control)
                block = stream.read(_COPY_CHUNK_BYTES)
                if type(block) is not bytes:
                    reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_read")
                if not block:
                    break
                count += len(block)
                if count > maximum_output_bytes:
                    reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.output_size")
                digest.update(block)
    except OcrError:
        raise
    except OSError:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_read")
    if count != info.st_size:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_size")
    page_count = _read_pdf_page_count(output_path)
    if page_count != expected_page_count:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_pages")
    _require_active(control)
    return count, digest.hexdigest(), page_count


def _read_pdf_page_count(path: Path) -> int:
    try:
        count = verified_ocr_pdf_page_count(path)
    except OcrPdfCandidateVerificationError:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_reopen")
    return count


def loaded_ocrmypdf_version() -> str | None:
    """Return the statically admitted runtime API version, if it is complete."""

    version = getattr(ocrmypdf, "__version__", None)
    return version if type(version) is str and callable(getattr(ocrmypdf, "ocr", None)) else None


def _require_active(control: TesseractOperationControl) -> None:
    try:
        cancelled = control.cancellation_requested()
        expired = control.deadline_expired()
    except Exception:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.control")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.control")
    if cancelled:
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.operation")
    if expired:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.operation")


def _map_runner_error(error: TesseractRunnerError) -> None:
    mapping = {
        TesseractRunnerErrorCode.UNAVAILABLE: OcrErrorCode.ENGINE_UNAVAILABLE,
        TesseractRunnerErrorCode.OUTPUT_LIMIT: OcrErrorCode.LIMIT_EXCEEDED,
        TesseractRunnerErrorCode.CANCELLED: OcrErrorCode.CANCELLED,
        TesseractRunnerErrorCode.TIMED_OUT: OcrErrorCode.TIMED_OUT,
        TesseractRunnerErrorCode.CONFIGURATION: OcrErrorCode.INTERNAL_ERROR,
        TesseractRunnerErrorCode.FAILED: OcrErrorCode.ENGINE_FAILED,
        TesseractRunnerErrorCode.CLEANUP_FAILED: OcrErrorCode.ENGINE_FAILED,
    }
    reject_ocr(mapping[error.code], "ocr.tesseract")


def _exact_version(value: object, expected: str, context: str) -> str:
    if value != expected or type(value) is not str:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, context)
    return value


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
    except OSError, ValueError:
        return False
    return True


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return type(attributes) is int and bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
