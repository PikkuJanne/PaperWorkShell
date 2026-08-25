"""Executable tests for the PWS-0002 dependency-boundary policy."""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.check_import_boundaries import (
    APPROVED_EXTERNAL_IMPORT_PREFIXES,
    DEFAULT_PACKAGE_ROOT,
    PROJECT_ROOT,
    REQUIRED_PACKAGES,
    inspect_file,
    inspect_package,
    inspect_source,
    main,
)

EXPECTED_PACKAGES = (
    "app",
    "domain",
    "ui",
    "worker",
    "pdf",
    "imaging",
    "ocr",
    "overlays",
    "preflight",
    "export",
    "templates",
    "storage",
    "diagnostics",
)


def test_required_packages_exist_and_import() -> None:
    assert REQUIRED_PACKAGES == EXPECTED_PACKAGES
    for package_name in EXPECTED_PACKAGES:
        assert (DEFAULT_PACKAGE_ROOT / package_name / "__init__.py").is_file()
        imported = importlib.import_module(f"paperworkshell.{package_name}")
        assert imported.__name__ == f"paperworkshell.{package_name}"


def test_package_namespaces_import_with_optional_engines_blocked() -> None:
    package_names = ("paperworkshell", *(f"paperworkshell.{name}" for name in EXPECTED_PACKAGES))
    script = textwrap.dedent(
        f"""
        import importlib
        import importlib.abc
        import sys

        sys.path.insert(0, {str(DEFAULT_PACKAGE_ROOT.parent)!r})
        blocked = {{"PySide6", "pikepdf", "ocrmypdf"}}

        class OptionalEngineBlocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in blocked:
                    raise RuntimeError(f"optional engine imported: {{fullname}}")
                return None

        sys.meta_path.insert(0, OptionalEngineBlocker())
        for package_name in {package_names!r}:
            importlib.import_module(package_name)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_repository_respects_import_boundaries() -> None:
    assert inspect_package(DEFAULT_PACKAGE_ROOT) == ()


@pytest.mark.parametrize(
    ("module", "package", "source", "expected_code"),
    [
        (
            "paperworkshell.domain.example",
            "paperworkshell.domain",
            "from paperworkshell import app\n",
            "ARCH_DOMAIN_OUTBOUND",
        ),
        (
            "paperworkshell.domain.example",
            "paperworkshell.domain",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pikepdf\n",
            "ARCH_DOMAIN_EXTERNAL",
        ),
        (
            "paperworkshell.diagnostics.example",
            "paperworkshell.diagnostics",
            "import paperworkshell.domain\n",
            "ARCH_DIAGNOSTICS_OUTBOUND",
        ),
        (
            "paperworkshell.diagnostics.example",
            "paperworkshell.diagnostics",
            "import paperworkshell.storage\n",
            "ARCH_DIAGNOSTICS_OUTBOUND",
        ),
        (
            "paperworkshell.diagnostics.example",
            "paperworkshell.diagnostics",
            "import paperworkshell.worker\n",
            "ARCH_DIAGNOSTICS_OUTBOUND",
        ),
        (
            "paperworkshell.diagnostics.example",
            "paperworkshell.diagnostics",
            "import paperworkshell.pdf\n",
            "ARCH_DIAGNOSTICS_OUTBOUND",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from paperworkshell import pdf\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from ..ocr import Engine\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from .. import pdf\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from paperworkshell.pdf import *\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import os, paperworkshell.imaging.filters\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.worker.host\n",
            "ARCH_UI_WORKER_HOST",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from paperworkshell.worker import host\n",
            "ARCH_UI_WORKER_HOST",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.pdf\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.worker.client",
            "paperworkshell.worker",
            "import paperworkshell.pdf\n",
            "ARCH_WORKER_CLIENT_BOUNDARY",
        ),
        (
            "paperworkshell.worker.protocol.messages",
            "paperworkshell.worker.protocol",
            "import paperworkshell.worker.host\n",
            "ARCH_WORKER_CLIENT_BOUNDARY",
        ),
        (
            "paperworkshell.worker.client",
            "paperworkshell.worker",
            "import paperworkshell.export.writer\n",
            "ARCH_GUI_EXPORT_IMPLEMENTATION",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.export.writer\n",
            "ARCH_GUI_EXPORT_IMPLEMENTATION",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from paperworkshell.export import writer\n",
            "ARCH_GUI_EXPORT_IMPLEMENTATION",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "from .export import writer\n",
            "ARCH_GUI_EXPORT_IMPLEMENTATION",
        ),
        (
            "paperworkshell.worker.client",
            "paperworkshell.worker",
            "from paperworkshell.export import *\n",
            "ARCH_GUI_EXPORT_IMPLEMENTATION",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.ui\n",
            "ARCH_UI_DIRECTION",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from paperworkshell import __main__\n",
            "ARCH_UI_DIRECTION",
        ),
        (
            "paperworkshell.ui.example",
            "paperworkshell.ui",
            "import paperworkshell.__main__\n",
            "ARCH_UI_DIRECTION",
        ),
        (
            "paperworkshell.pdf.example",
            "paperworkshell.pdf",
            "from paperworkshell.app import Service\n",
            "ARCH_REVERSE_UI",
        ),
        (
            "paperworkshell.pdf.example",
            "paperworkshell.pdf",
            "from paperworkshell.worker import Host\n",
            "ARCH_ADAPTER_WORKER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from pikepdf import Pdf\n",
            "ARCH_EXTERNAL_IMPORT",
        ),
        (
            "paperworkshell.domain.example",
            "paperworkshell.domain",
            "import tkinter.filedialog\n",
            "ARCH_UI_TOOLKIT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "from tkinter import filedialog\n",
            "ARCH_UI_TOOLKIT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "try:\n    import pikepdf\nexcept ImportError:\n    pass\n",
            "ARCH_EXTERNAL_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import thirdlife\n",
            "ARCH_PORTFOLIO_DEPENDENCY",
        ),
        (
            "paperworkshell.worker.example",
            "paperworkshell.worker",
            "if False:\n    from thirdlife_setup_core import Bridge\n",
            "ARCH_PORTFOLIO_DEPENDENCY",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "if False:\n    import paperworkshell.ocr\n",
            "ARCH_UI_ADAPTER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'import importlib\nimportlib.import_module("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'import importlib.util\nimportlib.import_module("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'import builtins\nbuiltins.__import__("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'import importlib\ngetattr(importlib, "import_module")("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'import importlib\nload = importlib.import_module\nload("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
        (
            "paperworkshell",
            "paperworkshell",
            "from . import app\n",
            "ARCH_ROOT_REEXPORT",
        ),
        (
            "paperworkshell.worker",
            "paperworkshell.worker",
            "from . import host\n",
            "ARCH_INIT_IMPORT",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.rogue\n",
            "ARCH_UNKNOWN_LAYER",
        ),
        (
            "paperworkshell.rogue.example",
            "paperworkshell.rogue",
            "import pathlib\n",
            "ARCH_UNKNOWN_LAYER",
        ),
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            'from importlib import import_module as load\nload("paperworkshell.pdf")\n',
            "ARCH_DYNAMIC_IMPORT",
        ),
    ],
)
def test_deliberate_prohibited_imports_fail(
    module: str,
    package: str,
    source: str,
    expected_code: str,
) -> None:
    violations = inspect_source(
        source,
        module=module,
        package=package,
        path="deliberate_violation.py",
    )
    assert expected_code in {violation.code for violation in violations}


def test_import_scanner_ignores_non_import_text_and_local_relative_name() -> None:
    source = textwrap.dedent(
        '''
        """The words 'import pikepdf' are documentation, not an import."""

        pikepdf = "an ordinary local identifier"
        from . import pikepdf
        '''
    )
    assert (
        inspect_source(
            source,
            module="paperworkshell.domain.example",
            package="paperworkshell.domain",
            path="allowed.py",
        )
        == ()
    )


def test_diagnostics_may_import_only_stdlib_root_identity_and_own_modules() -> None:
    source = (
        "from __future__ import annotations\n"
        "import json\n"
        "from paperworkshell import PRODUCT_NAME\n"
        "from paperworkshell.diagnostics import models\n"
    )
    assert (
        inspect_source(
            source,
            module="paperworkshell.diagnostics.example",
            package="paperworkshell.diagnostics",
            path="allowed.py",
        )
        == ()
    )


def test_desktop_entrypoint_and_worker_client_imports_are_allowed() -> None:
    assert (
        inspect_source(
            "from paperworkshell import app\n"
            "import paperworkshell.worker.client\n"
            "import paperworkshell.worker.desktop_composition\n"
            "from paperworkshell.worker import desktop_composition\n"
            "from paperworkshell.worker.desktop_composition import "
            "QualityOcrReviewWorkerComposition, "
            "FlattenedPdfExportWorkerComposition\n"
            "from paperworkshell.export.pdf_destination import "
            "WindowsVerifiedPdfDestination\n"
            "import paperworkshell.export\n",
            module="paperworkshell.__main__",
            package="paperworkshell",
            path="__main__.py",
        )
        == ()
    )

    assert (
        inspect_source(
            "import paperworkshell.domain\nimport paperworkshell.export\n",
            module="paperworkshell.worker.client",
            package="paperworkshell.worker",
            path="client.py",
        )
        == ()
    )


def test_application_layer_cannot_import_concrete_storage_adapters() -> None:
    violations = inspect_source(
        "from paperworkshell.storage.recovery import WindowsStartupRecoveryStore\n",
        module="paperworkshell.app.recovery",
        package="paperworkshell.app",
        path="recovery.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_APP_ADAPTER"}


@pytest.mark.parametrize(
    ("module", "package", "source"),
    [
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.worker.desktop_composition\n",
        ),
        (
            "paperworkshell.ui.example",
            "paperworkshell.ui",
            "import paperworkshell.worker.desktop_composition\n",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.worker.host\n",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.worker.desktop_composition.internal\n",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.worker.quality_ocr_review_composition\n",
        ),
    ],
)
def test_desktop_worker_composition_exception_is_exact(
    module: str,
    package: str,
    source: str,
) -> None:
    violations = inspect_source(
        source,
        module=module,
        package=package,
        path="desktop_composition_violation.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_UI_WORKER_HOST"}


@pytest.mark.parametrize("adapter", ("pdf", "imaging", "ocr"))
def test_desktop_worker_composition_cannot_import_document_adapters(adapter: str) -> None:
    violations = inspect_source(
        f"import paperworkshell.{adapter}.example\n",
        module="paperworkshell.worker.desktop_composition",
        package="paperworkshell.worker",
        path="desktop_composition_adapter_violation.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_DESKTOP_COMPOSITION_ADAPTER"}


@pytest.mark.parametrize(
    ("module", "package", "source"),
    [
        (
            "paperworkshell.app.example",
            "paperworkshell.app",
            "import paperworkshell.export.pdf_destination\n",
        ),
        (
            "paperworkshell.ui.example",
            "paperworkshell.ui",
            "import paperworkshell.export.pdf_destination\n",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.export.pdf_destination.internal\n",
        ),
        (
            "paperworkshell.__main__",
            "paperworkshell",
            "import paperworkshell.export.other_destination\n",
        ),
    ],
)
def test_desktop_export_destination_exception_is_exact(
    module: str,
    package: str,
    source: str,
) -> None:
    violations = inspect_source(
        source,
        module=module,
        package=package,
        path="export_destination_violation.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_GUI_EXPORT_IMPLEMENTATION"}


@pytest.mark.parametrize(
    "source",
    (
        "import PySide6\n",
        "from PySide6 import QtCore\n",
        "import PySide6.QtPdf\n",
        "import PySide6.QtPdfWidgets\n",
        "import PySide6.QtNetwork\n",
        "import PySide6.QtQml\n",
        "import PySide6.QtQuick\n",
        "import PySide6.QtWebEngineCore\n",
    ),
)
def test_ui_rejects_unapproved_pyside_surfaces(source: str) -> None:
    violations = inspect_source(
        source,
        module="paperworkshell.ui.example",
        package="paperworkshell.ui",
        path="ui_toolkit_violation.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}


@pytest.mark.parametrize(
    "source",
    (
        "import PySide6.QtCore\n",
        "from PySide6.QtGui import QImage\n",
        "from PySide6.QtWidgets import QApplication\n",
    ),
)
def test_ui_may_import_only_selected_pyside_modules(source: str) -> None:
    assert (
        inspect_source(
            source,
            module="paperworkshell.ui.example",
            package="paperworkshell.ui",
            path="ui_toolkit_allowed.py",
        )
        == ()
    )


@pytest.mark.parametrize(
    ("module", "package"),
    (
        ("paperworkshell.domain.example", "paperworkshell.domain"),
        ("paperworkshell.app.example", "paperworkshell.app"),
        ("paperworkshell.__main__", "paperworkshell"),
        ("paperworkshell.worker.host", "paperworkshell.worker"),
        ("paperworkshell.pdf.renderer", "paperworkshell.pdf"),
        ("paperworkshell.storage.cache", "paperworkshell.storage"),
    ),
)
def test_selected_pyside_modules_remain_ui_only(module: str, package: str) -> None:
    violations = inspect_source(
        "import PySide6.QtCore\n",
        module=module,
        package=package,
        path="non_ui_toolkit_violation.py",
    )

    expected_code = (
        "ARCH_DOMAIN_EXTERNAL"
        if module.startswith("paperworkshell.domain")
        else "ARCH_EXTERNAL_IMPORT"
    )
    assert {violation.code for violation in violations} == {expected_code}


@pytest.mark.parametrize("engine", ("pikepdf", "pypdfium2"))
def test_only_pdf_adapter_may_import_pinned_pdf_engines(engine: str) -> None:
    assert (
        inspect_source(
            f"import {engine}\nfrom {engine} import Pdf\n",
            module="paperworkshell.pdf.pikepdf_adapter",
            package="paperworkshell.pdf",
            path="pikepdf_adapter.py",
        )
        == ()
    )
    violations = inspect_source(
        f"import {engine}\n",
        module="paperworkshell.worker.operations",
        package="paperworkshell.worker",
        path="operations.py",
    )
    assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}

    for target in ("PIL", "lxml", "packaging"):
        violations = inspect_source(
            f"import {target}\n",
            module="paperworkshell.pdf.pikepdf_adapter",
            package="paperworkshell.pdf",
            path="pikepdf_adapter.py",
        )
        assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}


@pytest.mark.parametrize("engine", ("fontTools", "fpdf", "uharfbuzz"))
def test_only_exact_overlay_renderer_may_import_font_rendering_engines(engine: str) -> None:
    assert (
        inspect_source(
            f"import {engine}\n",
            module="paperworkshell.pdf.overlay_renderer",
            package="paperworkshell.pdf",
            path="overlay_renderer.py",
        )
        == ()
    )
    for module, package in (
        ("paperworkshell.pdf.unapproved", "paperworkshell.pdf"),
        ("paperworkshell.overlays.rendering", "paperworkshell.overlays"),
        ("paperworkshell.worker.operations", "paperworkshell.worker"),
    ):
        violations = inspect_source(
            f"import {engine}\n",
            module=module,
            package=package,
            path="overlay_engine_violation.py",
        )
        assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}


def test_only_imaging_adapter_may_import_pillow() -> None:
    assert (
        inspect_source(
            "import PIL\nfrom PIL import Image\n",
            module="paperworkshell.imaging.pillow_decoder",
            package="paperworkshell.imaging",
            path="pillow_decoder.py",
        )
        == ()
    )
    for module, package in (
        ("paperworkshell.domain.image_pages", "paperworkshell.domain"),
        ("paperworkshell.app.page_previews", "paperworkshell.app"),
        ("paperworkshell.ui.window", "paperworkshell.ui"),
        ("paperworkshell.worker.operations", "paperworkshell.worker"),
        ("paperworkshell.pdf.pikepdf_adapter", "paperworkshell.pdf"),
    ):
        violations = inspect_source(
            "import PIL\n",
            module=module,
            package=package,
            path="pillow_boundary.py",
        )
        expected = (
            "ARCH_DOMAIN_EXTERNAL"
            if module.startswith("paperworkshell.domain")
            else "ARCH_EXTERNAL_IMPORT"
        )
        assert {violation.code for violation in violations} == {expected}


@pytest.mark.parametrize(
    ("dependency", "approved_module"),
    (
        ("ocrmypdf", "paperworkshell.ocr.ocrmypdf_adapter"),
        ("pluggy", "paperworkshell.ocr.ocrmypdf_adapter"),
        ("pydantic", "paperworkshell.ocr.ocrmypdf_adapter"),
        ("packaging.version", "paperworkshell.ocr.tesseract_runner"),
    ),
)
def test_only_exact_ocr_adapter_may_import_pinned_dependency(
    dependency: str,
    approved_module: str,
) -> None:
    assert (
        inspect_source(
            f"import {dependency}\n",
            module=approved_module,
            package="paperworkshell.ocr",
            path=f"{approved_module.rpartition('.')[2]}.py",
        )
        == ()
    )
    for rejected_module, rejected_package in (
        ("paperworkshell.ocr.unapproved_adapter", "paperworkshell.ocr"),
        ("paperworkshell.app.ocr", "paperworkshell.app"),
    ):
        violations = inspect_source(
            f"import {dependency}\n",
            module=rejected_module,
            package=rejected_package,
            path="ocr_dependency_violation.py",
        )
        assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}

    unapproved = inspect_source(
        "import requests\n",
        module="paperworkshell.ocr.ocrmypdf_adapter",
        package="paperworkshell.ocr",
        path="ocr_dependency_violation.py",
    )
    assert {violation.code for violation in unapproved} == {"ARCH_EXTERNAL_IMPORT"}


def test_ocr_pdf_reopen_stays_behind_the_pdf_adapter_boundary() -> None:
    violations = inspect_source(
        "import pikepdf\n",
        module="paperworkshell.ocr.ocrmypdf_adapter",
        package="paperworkshell.ocr",
        path="ocrmypdf_adapter.py",
    )
    assert {violation.code for violation in violations} == {"ARCH_EXTERNAL_IMPORT"}
    assert (
        inspect_source(
            "import pikepdf\n",
            module="paperworkshell.pdf.ocr_candidate_verifier",
            package="paperworkshell.pdf",
            path="ocr_candidate_verifier.py",
        )
        == ()
    )


def test_invalid_source_and_relative_import_fail_closed() -> None:
    parse_violations = inspect_source(
        "def broken(:\n",
        module="paperworkshell.domain.broken",
        package="paperworkshell.domain",
        path="broken.py",
    )
    assert [violation.code for violation in parse_violations] == ["ARCH_PARSE_ERROR"]

    relative_violations = inspect_source(
        "from ....pdf import Adapter\n",
        module="paperworkshell.app.broken",
        package="paperworkshell.app",
        path="broken_relative.py",
    )
    assert [violation.code for violation in relative_violations] == ["ARCH_RELATIVE_IMPORT"]


def test_portfolio_import_cannot_be_admitted_by_the_external_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        APPROVED_EXTERNAL_IMPORT_PREFIXES,
        "app",
        frozenset({"thirdlife"}),
    )

    violations = inspect_source(
        "import thirdlife\n",
        module="paperworkshell.app.example",
        package="paperworkshell.app",
        path="portfolio_dependency.py",
    )

    assert {violation.code for violation in violations} == {"ARCH_PORTFOLIO_DEPENDENCY"}


def _create_package_layout(root: Path) -> None:
    root.mkdir()
    (root / "__init__.py").write_text('"""Test package."""\n', encoding="utf-8")
    for package_name in EXPECTED_PACKAGES:
        package = root / package_name
        package.mkdir()
        (package / "__init__.py").write_text('"""Test layer."""\n', encoding="utf-8")


def test_missing_required_initializer_fails_layout_check(tmp_path: Path) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    (package_root / "diagnostics" / "__init__.py").unlink()

    violations = inspect_package(package_root)
    assert "ARCH_PACKAGE_LAYOUT" in {violation.code for violation in violations}


def test_python_bearing_sibling_source_package_is_rejected(tmp_path: Path) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    sibling = tmp_path / "thirdlife"
    sibling.mkdir()
    (sibling / "bridge.py").write_text("VALUE = 1\n", encoding="utf-8")

    violations = inspect_package(package_root)

    assert "ARCH_SOURCE_PACKAGE_UNEXPECTED" in {violation.code for violation in violations}


def test_source_root_enumeration_error_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    original_iterdir = Path.iterdir

    def injected_iterdir(path: Path) -> Iterator[Path]:
        if path == tmp_path:
            raise OSError("sensitive host detail")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", injected_iterdir)

    violations = inspect_package(package_root)

    read_errors = [
        violation for violation in violations if violation.code == "ARCH_SOURCE_ROOT_READ_ERROR"
    ]
    assert [(violation.line, violation.detail) for violation in read_errors] == [
        (1, "source root cannot be enumerated safely")
    ]
    assert "sensitive host detail" not in "\n".join(violation.render() for violation in violations)


def test_package_root_alias_fails_before_source_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    (package_root / "app" / "private-path-canary.py").write_text(
        "import pikepdf\n",
        encoding="utf-8",
    )
    original_is_symlink = Path.is_symlink

    def injected_is_symlink(path: Path) -> bool:
        return path == package_root or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", injected_is_symlink)

    violations = inspect_package(package_root)
    rendered = "\n".join(violation.render() for violation in violations)

    assert {violation.code for violation in violations} == {"ARCH_PACKAGE_ROOT_UNSAFE"}
    assert "ARCH_EXTERNAL_IMPORT" not in rendered
    assert "private-path-canary" not in rendered


def test_nested_directory_alias_fails_before_layout_or_source_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    aliased_directory = package_root / "app"
    original_is_symlink = Path.is_symlink
    original_rglob = Path.rglob

    def injected_is_symlink(path: Path) -> bool:
        return path == aliased_directory or original_is_symlink(path)

    def guarded_rglob(path: Path, pattern: str) -> Iterator[Path]:
        if path == package_root:
            raise AssertionError("unsafe directory trees must not be traversed")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "is_symlink", injected_is_symlink)
    monkeypatch.setattr(Path, "rglob", guarded_rglob)

    violations = inspect_package(package_root)

    assert {violation.code for violation in violations} == {"ARCH_SOURCE_PATH_UNSAFE"}
    assert violations[0].path == "<outside-project>"
    assert str(tmp_path) not in violations[0].render()


def test_sibling_alias_is_rejected_without_recursive_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    sibling = tmp_path / "suite-assembly"
    sibling.mkdir()
    original_is_symlink = Path.is_symlink
    original_rglob = Path.rglob

    def injected_is_symlink(path: Path) -> bool:
        return path == sibling or original_is_symlink(path)

    def guarded_rglob(path: Path, pattern: str) -> Iterator[Path]:
        if path == sibling:
            raise AssertionError("sibling alias must not be traversed")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "is_symlink", injected_is_symlink)
    monkeypatch.setattr(Path, "rglob", guarded_rglob)

    violations = inspect_package(package_root)

    assert "ARCH_SOURCE_PACKAGE_UNEXPECTED" in {violation.code for violation in violations}


def test_unsafe_source_path_is_not_opened_or_disclosed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    outside = tmp_path / "private-path-canary.py"
    outside.write_text("import pikepdf\n", encoding="utf-8")
    linked = package_root / "app" / "leak.py"
    linked.write_text("import pikepdf\n", encoding="utf-8")
    original_is_symlink = Path.is_symlink

    def injected_is_symlink(path: Path) -> bool:
        return path == linked or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", injected_is_symlink)

    violations = (*inspect_package(package_root), *inspect_file(outside, package_root))
    rendered = "\n".join(violation.render() for violation in violations)

    assert "ARCH_SOURCE_PATH_UNSAFE" in {violation.code for violation in violations}
    assert "ARCH_EXTERNAL_IMPORT" not in {violation.code for violation in violations}
    assert str(outside) not in rendered
    assert "private-path-canary" not in rendered


def test_in_package_ancestor_alias_is_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    source = package_root / "app" / "bridge.py"
    source.write_text("import pikepdf\n", encoding="utf-8")
    aliased_target = package_root / "domain" / "bridge.py"
    aliased_target.write_text("VALUE = 1\n", encoding="utf-8")
    original_resolve = Path.resolve

    def injected_resolve(path: Path, strict: bool = False) -> Path:
        if path == source:
            return aliased_target
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", injected_resolve)

    violations = inspect_file(source, package_root)

    assert {violation.code for violation in violations} == {"ARCH_SOURCE_PATH_UNSAFE"}


def test_cli_rejects_deliberate_prohibited_fixture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_root = tmp_path / "paperworkshell"
    _create_package_layout(package_root)
    (package_root / "app" / "bad.py").write_text(
        "from paperworkshell.pdf import Adapter\n",
        encoding="utf-8",
    )

    assert main(("--package-root", str(package_root))) == 1
    captured = capsys.readouterr()
    assert "ERROR [ARCH_UI_ADAPTER]" in captured.err
    assert captured.out == ""
