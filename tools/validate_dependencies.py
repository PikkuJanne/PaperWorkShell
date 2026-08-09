"""Validate the PaperWorkShell dependency-policy baseline and rejected examples."""

from __future__ import annotations

import argparse
import ast
import hashlib
import ipaddress
import json
import re
import sys
import tomllib
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from urllib.parse import unquote, urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
INVENTORY_PATH: Final = Path("packaging/dependency-inventory-v1.json")
INVENTORY_SCHEMA_PATH: Final = Path("schemas/dependency-inventory-v1.schema.json")
INVALID_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/dependencies/v1/invalid")
SUPPORTED_SCHEMA_VERSION: Final = 1
DRAFT_2020_12_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_IDENTIFIER: Final = "https://paperworkshell.invalid/schemas/dependency-inventory-v1.json"
CANONICAL_SCHEMA_FINGERPRINT: Final = (
    "c74a7ae9a072c79a3f9a72d5c08ca5d5ae3ab1c4f17971d338cde09258dbd81d"
)

MAX_INVENTORY_BYTES: Final = 2 * 1024 * 1024
MAX_SCHEMA_BYTES: Final = 1024 * 1024
MAX_JSON_DEPTH: Final = 64
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_CODEPOINTS: Final = 1_048_576
MAX_JSON_COLLECTION_ITEMS: Final = 1_024
MAX_FIXTURE_FILES: Final = 256
MAX_REPORTED_ISSUES: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

RENDERER_COMPONENT_IDS: Final = frozenset(
    {"mupdf", "pdfium", "poppler", "pypdfium2", "pyside6-addons", "qt-pdf"}
)
HARD_GATED_COMPONENT_IDS: Final = frozenset({"ghostscript", "mupdf", "poppler"})
KNOWN_DOCUMENT_PROCESSOR_IDS: Final = frozenset(
    {
        "ghostscript",
        "lxml",
        "mupdf",
        "ocrmypdf",
        "pdfium",
        "pikepdf",
        "pillow",
        "poppler",
        "pypdfium2",
        "pyside6-addons",
        "qpdf",
        "qt-pdf",
        "tesseract",
    }
)
EXPECTED_DOCUMENT_ECOSYSTEMS: Final[dict[str, str]] = {
    "ghostscript": "native-tool",
    "lxml": "python-package",
    "mupdf": "native-library",
    "ocrmypdf": "python-package",
    "pdfium": "native-library",
    "pikepdf": "python-package",
    "pillow": "python-package",
    "poppler": "native-library",
    "pypdfium2": "python-package",
    "pyside6-addons": "python-package",
    "qpdf": "native-library",
    "qt-pdf": "framework",
    "tesseract": "native-tool",
    "tesseract-language-data": "data",
}
REQUIRED_COMPONENT_IDS: Final = frozenset(
    {
        "actions-checkout",
        "actions-setup-python",
        "astral-setup-uv",
        "cpython",
        "ghostscript",
        "git",
        "gitleaks",
        "lxml",
        "ocrmypdf",
        "pikepdf",
        "pillow",
        "pyside6",
        "pyside6-addons",
        "pyside6-essentials",
        "qpdf",
        "shiboken6",
        "tesseract",
        "tesseract-language-data",
        "uv",
        "uv-build",
        *RENDERER_COMPONENT_IDS,
    }
)
REQUIRED_FAMILY_DECISIONS: Final[dict[str, frozenset[str]]] = {
    "PWS-D014": frozenset(
        {"cpython", "pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6"}
    ),
    "PWS-D015": frozenset({"lxml", "pikepdf", "pillow", "qpdf"}),
    "PWS-D016": frozenset({"ocrmypdf", "tesseract", "tesseract-language-data"}),
    "PWS-D017": RENDERER_COMPONENT_IDS,
}
ADMISSION_TASK_GATES: Final[dict[str, frozenset[str]]] = {
    "pdfium": frozenset({"PWS-0106"}),
    "ocrmypdf": frozenset({"PWS-0204"}),
    "lxml": frozenset({"PWS-0105"}),
    "pikepdf": frozenset({"PWS-0105"}),
    "pillow": frozenset({"PWS-0105"}),
    "pypdfium2": frozenset({"PWS-0106"}),
    "pyside6": frozenset({"PWS-0111"}),
    "pyside6-addons": frozenset({"PWS-0106"}),
    "pyside6-essentials": frozenset({"PWS-0106"}),
    "qpdf": frozenset({"PWS-0105"}),
    "shiboken6": frozenset({"PWS-0106"}),
    "tesseract": frozenset({"PWS-0204"}),
    "tesseract-language-data": frozenset({"PWS-0204", "PWS-0205"}),
}
REQUIRED_REVIEWED_RELATIONSHIPS: Final = frozenset(
    {
        ("pikepdf", "contains", "qpdf", True),
        ("pypdfium2", "contains", "pdfium", True),
        ("pyside6", "contains", "qt-pdf", False),
        ("pyside6-addons", "contains", "qt-pdf", False),
    }
)
EXPECTED_NEGATIVE_CODES: Final[dict[str, str]] = {
    "distribution-decision-bypass.json": "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
    "duplicate-key.json": "DEPENDENCY_JSON_DUPLICATE_KEY",
    "future-version.json": "DEPENDENCY_VERSION_UNSUPPORTED",
    "legacy-version.json": "DEPENDENCY_VERSION_UNSUPPORTED",
}
KNOWN_LICENSE_IDS: Final = frozenset(
    {
        "AGPL-3.0-or-later",
        "Apache-2.0",
        "Artistic-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "GPL-2.0-only",
        "GPL-3.0-only",
        "LGPL-3.0-only",
        "MIT",
        "MIT-CMU",
        "MPL-2.0",
        "PSF-2.0",
    }
)
EXPECTED_LICENSE_REF_REVIEW_STATUS: Final[dict[str, str]] = {
    "LicenseRef-Artifex-Commercial": "decision-required",
    "LicenseRef-PDFium-Third-Party": "notice-closure-required",
    "LicenseRef-Qt-Commercial": "candidate-only",
}
EXPECTED_LICENSE_REF_IDENTITIES: Final[dict[str, tuple[str, str, str]]] = {
    "LicenseRef-Artifex-Commercial": (
        "Artifex commercial license",
        "https://artifex.com/licensing/commercial/",
        "decision-required",
    ),
    "LicenseRef-PDFium-Third-Party": (
        "PDFium revision-specific third-party license closure",
        "https://pdfium.googlesource.com/pdfium/+/refs/heads/main/third_party/",
        "notice-closure-required",
    ),
    "LicenseRef-Qt-Commercial": (
        "Qt commercial license",
        "https://www.qt.io/licensing/",
        "candidate-only",
    ),
}
EXPECTED_TOOL_ARTIFACTS: Final[dict[str, str]] = {
    "gitleaks": "gitleaks_8.30.1_windows_x64.zip",
    "pdfium": "pdfium-win-x64.tgz",
    "qt-pdf": "pyside6_addons-6.11.1-cp310-abi3-win_amd64.whl",
    "uv": "uv-x86_64-pc-windows-msvc.zip",
}
EXPECTED_ARTIFACT_URLS: Final[dict[str, str]] = {
    "pdfium": (
        "https://github.com/bblanchon/pdfium-binaries/releases/download/"
        "chromium/7947/pdfium-win-x64.tgz"
    ),
    "qt-pdf": (
        "https://files.pythonhosted.org/packages/9a/bd/"
        "8adc4d350b3b363f3dfc8fccdcf5bfed25f7e36c2fff30c64e106f4f1572/"
        "pyside6_addons-6.11.1-cp310-abi3-win_amd64.whl"
    ),
}
EXPECTED_RELEASE_URLS: Final[dict[str, str]] = {
    "pdfium": "https://github.com/bblanchon/pdfium-binaries/releases/tag/chromium/7947",
    "qt-pdf": "https://pypi.org/project/PySide6-Addons/6.11.1/",
}
EXPECTED_PIN_VERIFICATION: Final[dict[str, str]] = {
    "exact": "sha256",
    "git-commit": "git-commit",
    "lockfile": "uv-lock",
    "minimum": "environment-version",
    "minor-line": "environment-version",
    "unselected": "required-before-admission",
}
EXPECTED_PURL_BASES: Final[dict[str, str]] = {
    "actions-checkout": "pkg:github/actions/checkout",
    "actions-setup-python": "pkg:github/actions/setup-python",
    "astral-setup-uv": "pkg:github/astral-sh/setup-uv",
    "cpython": "pkg:generic/cpython",
    "git": "pkg:github/git/git",
    "gitleaks": "pkg:github/gitleaks/gitleaks",
    "qpdf": "pkg:generic/qpdf",
    "uv": "pkg:github/astral-sh/uv",
}
EXPECTED_PROJECT_URLS: Final[dict[str, str]] = {
    "actions-checkout": "https://github.com/actions/checkout",
    "actions-setup-python": "https://github.com/actions/setup-python",
    "ast-serialize": "https://github.com/mypyc/ast_serialize",
    "astral-setup-uv": "https://github.com/astral-sh/setup-uv",
    "attrs": "https://github.com/python-attrs/attrs",
    "colorama": "https://github.com/tartley/colorama",
    "cpython": "https://www.python.org/",
    "ghostscript": "https://ghostscript.com/",
    "git": "https://git-scm.com/",
    "gitleaks": "https://github.com/gitleaks/gitleaks",
    "iniconfig": "https://github.com/pytest-dev/iniconfig",
    "jsonschema": "https://github.com/python-jsonschema/jsonschema",
    "jsonschema-specifications": ("https://github.com/python-jsonschema/jsonschema-specifications"),
    "librt": "https://github.com/mypyc/librt",
    "lxml": "https://github.com/lxml/lxml",
    "mupdf": "https://mupdf.com/",
    "mypy": "https://github.com/python/mypy",
    "mypy-extensions": "https://github.com/python/mypy_extensions",
    "ocrmypdf": "https://github.com/ocrmypdf/OCRmyPDF",
    "packaging": "https://github.com/pypa/packaging",
    "pathspec": "https://github.com/cpburnz/python-pathspec",
    "pdfium": "https://pdfium.googlesource.com/pdfium/",
    "pikepdf": "https://github.com/pikepdf/pikepdf",
    "pillow": "https://github.com/python-pillow/Pillow",
    "pluggy": "https://github.com/pytest-dev/pluggy",
    "poppler": "https://poppler.freedesktop.org/",
    "pygments": "https://github.com/pygments/pygments",
    "pypdfium2": "https://github.com/pypdfium2-team/pypdfium2",
    "pyside6": "https://doc.qt.io/qtforpython-6/",
    "pyside6-addons": "https://doc.qt.io/qtforpython-6/",
    "pyside6-essentials": "https://doc.qt.io/qtforpython-6/",
    "pytest": "https://github.com/pytest-dev/pytest",
    "pyyaml": "https://github.com/yaml/pyyaml",
    "qpdf": "https://github.com/qpdf/qpdf",
    "qt-pdf": "https://doc.qt.io/qt-6/qtpdf-index.html",
    "referencing": "https://github.com/python-jsonschema/referencing",
    "rpds-py": "https://github.com/crate-py/rpds",
    "ruff": "https://github.com/astral-sh/ruff",
    "shiboken6": "https://doc.qt.io/qtforpython-6/shiboken6/",
    "tesseract": "https://github.com/tesseract-ocr/tesseract",
    "tesseract-language-data": "https://github.com/tesseract-ocr/tessdata",
    "types-jsonschema": "https://github.com/python/typeshed",
    "types-pyyaml": "https://github.com/python/typeshed",
    "typing-extensions": "https://github.com/python/typing_extensions",
    "uv": "https://github.com/astral-sh/uv",
    "uv-build": "https://github.com/astral-sh/uv",
}
EXPECTED_LICENSE_URLS: Final[dict[str, str]] = {
    "actions-checkout": "https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/LICENSE",
    "actions-setup-python": "https://github.com/actions/setup-python/blob/5fda3b95a4ea91299a34e894583c3862153e4b97/LICENSE",
    "ast-serialize": "https://github.com/mypyc/ast_serialize/blob/main/LICENSE",
    "astral-setup-uv": "https://github.com/astral-sh/setup-uv/blob/c771a70e6277c0a99b617c7a806ffedaca235ff9/LICENSE",
    "attrs": "https://github.com/python-attrs/attrs/blob/26.1.0/LICENSE",
    "colorama": "https://github.com/tartley/colorama/blob/0.4.6/LICENSE.txt",
    "cpython": "https://docs.python.org/3/license.html",
    "ghostscript": "https://ghostscript.com/faq/index.html",
    "git": "https://github.com/git/git/blob/master/COPYING",
    "gitleaks": "https://github.com/gitleaks/gitleaks/blob/v8.30.1/LICENSE",
    "iniconfig": "https://github.com/pytest-dev/iniconfig/blob/2.3.0/LICENSE",
    "jsonschema": "https://github.com/python-jsonschema/jsonschema/blob/v4.26.0/COPYING",
    "jsonschema-specifications": "https://github.com/python-jsonschema/jsonschema-specifications/blob/v2025.9.1/COPYING",
    "librt": "https://github.com/mypyc/librt/blob/main/LICENSE",
    "lxml": "https://github.com/lxml/lxml/blob/lxml-6.1.1/LICENSES.txt",
    "mupdf": "https://mupdf.com/releases",
    "mypy": "https://github.com/python/mypy/blob/v2.3.0/LICENSE",
    "mypy-extensions": "https://github.com/python/mypy_extensions/blob/1.1.0/LICENSE",
    "ocrmypdf": "https://github.com/ocrmypdf/OCRmyPDF/blob/main/LICENSE",
    "packaging": "https://github.com/pypa/packaging/blob/26.2/LICENSE",
    "pathspec": "https://github.com/cpburnz/python-pathspec/blob/v1.1.1/LICENSE",
    "pdfium": (
        "https://pdfium.googlesource.com/pdfium/+/d3e62278db7489af9e0b686835d1c8aca218c3d7/LICENSE"
    ),
    "pikepdf": "https://github.com/pikepdf/pikepdf/blob/v10.10.0/LICENSE.txt",
    "pillow": "https://github.com/python-pillow/Pillow/blob/12.3.0/LICENSE",
    "pluggy": "https://github.com/pytest-dev/pluggy/blob/1.6.0/LICENSE",
    "poppler": "https://cgit.freedesktop.org/poppler/poppler/tree/README-XPDF",
    "pygments": "https://github.com/pygments/pygments/blob/2.20.0/LICENSE",
    "pypdfium2": (
        "https://github.com/pypdfium2-team/pypdfium2/blob/5.12.1/LICENSES/Apache-2.0.txt"
    ),
    "pyside6": "https://doc.qt.io/qtforpython-6/licenses.html",
    "pyside6-addons": "https://doc.qt.io/qtforpython-6/licenses.html",
    "pyside6-essentials": "https://doc.qt.io/qtforpython-6/licenses.html",
    "pytest": "https://github.com/pytest-dev/pytest/blob/9.1.1/LICENSE",
    "pyyaml": "https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE",
    "qpdf": "https://github.com/qpdf/qpdf/blob/v12.3.2/LICENSE.txt",
    "qt-pdf": "https://doc.qt.io/qt-6/qtpdf-licensing.html",
    "referencing": "https://github.com/python-jsonschema/referencing/blob/v0.37.0/COPYING",
    "rpds-py": "https://github.com/crate-py/rpds/blob/v2026.6.3/LICENSE",
    "ruff": "https://github.com/astral-sh/ruff/blob/0.15.22/LICENSE",
    "shiboken6": "https://doc.qt.io/qtforpython-6/licenses.html",
    "tesseract": "https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE",
    "tesseract-language-data": "https://github.com/tesseract-ocr/tessdata/blob/main/LICENSE",
    "types-jsonschema": "https://github.com/python/typeshed/blob/main/LICENSE",
    "types-pyyaml": "https://github.com/python/typeshed/blob/main/LICENSE",
    "typing-extensions": "https://github.com/python/typing_extensions/blob/4.16.0/LICENSE",
    "uv": "https://github.com/astral-sh/uv/blob/0.11.32/LICENSE-MIT",
    "uv-build": "https://github.com/astral-sh/uv/blob/0.11.32/LICENSE-MIT",
}
EXPECTED_LICENSE_POLICIES: Final[dict[str, tuple[str, str | None, bool, bool, bool, bool]]] = {
    "actions-checkout": ("MIT", None, True, False, False, False),
    "actions-setup-python": ("MIT", None, True, False, False, False),
    "ast-serialize": ("MIT", None, True, False, False, False),
    "astral-setup-uv": ("MIT", None, True, False, False, False),
    "attrs": ("MIT", None, True, False, False, False),
    "colorama": ("BSD-3-Clause", None, True, False, False, False),
    "cpython": ("PSF-2.0", None, True, False, False, False),
    "ghostscript": (
        "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial",
        None,
        True,
        True,
        False,
        True,
    ),
    "git": ("GPL-2.0-only", None, True, True, False, False),
    "gitleaks": ("MIT", None, True, False, False, False),
    "iniconfig": ("MIT", None, True, False, False, False),
    "jsonschema": ("MIT", None, True, False, False, False),
    "jsonschema-specifications": ("MIT", None, True, False, False, False),
    "librt": ("MIT", None, True, False, False, False),
    "lxml": ("BSD-3-Clause", None, True, False, False, False),
    "mupdf": ("AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial", None, True, True, False, True),
    "mypy": ("MIT", None, True, False, False, False),
    "mypy-extensions": ("MIT", None, True, False, False, False),
    "ocrmypdf": ("MPL-2.0", None, True, True, False, False),
    "packaging": ("Apache-2.0 OR BSD-2-Clause", "Apache-2.0", True, False, False, False),
    "pathspec": ("MPL-2.0", None, True, True, False, False),
    "pdfium": (
        "MIT AND BSD-3-Clause AND LicenseRef-PDFium-Third-Party",
        None,
        True,
        False,
        False,
        False,
    ),
    "pikepdf": ("MPL-2.0", None, True, True, False, False),
    "pillow": ("MIT-CMU", None, True, False, False, False),
    "pluggy": ("MIT", None, True, False, False, False),
    "poppler": ("GPL-2.0-only OR GPL-3.0-only", None, True, True, False, True),
    "pygments": ("BSD-2-Clause", None, True, False, False, False),
    "pypdfium2": (
        "(Apache-2.0 OR BSD-3-Clause) AND CC-BY-4.0 AND LicenseRef-PDFium-Third-Party",
        "Apache-2.0",
        True,
        False,
        False,
        False,
    ),
    "pyside6": (
        "LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-3.0-only",
        None,
        True,
        True,
        True,
        False,
    ),
    "pyside6-addons": (
        "LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
        None,
        True,
        True,
        True,
        False,
    ),
    "pyside6-essentials": (
        "LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
        None,
        True,
        True,
        True,
        False,
    ),
    "pytest": ("MIT", None, True, False, False, False),
    "pyyaml": ("MIT", None, True, False, False, False),
    "qpdf": ("Apache-2.0 OR Artistic-2.0", "Apache-2.0", True, False, False, False),
    "qt-pdf": (
        "(LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only) AND LicenseRef-PDFium-Third-Party",
        None,
        True,
        True,
        True,
        False,
    ),
    "referencing": ("MIT", None, True, False, False, False),
    "rpds-py": ("MIT", None, True, False, False, False),
    "ruff": ("MIT", None, True, False, False, False),
    "shiboken6": (
        "LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
        None,
        True,
        True,
        True,
        False,
    ),
    "tesseract": ("Apache-2.0", None, True, False, False, False),
    "tesseract-language-data": ("Apache-2.0", None, True, False, False, False),
    "types-jsonschema": ("Apache-2.0", None, True, False, False, False),
    "types-pyyaml": ("Apache-2.0", None, True, False, False, False),
    "typing-extensions": ("PSF-2.0", None, True, False, False, False),
    "uv": ("MIT OR Apache-2.0", "MIT", True, False, False, False),
    "uv-build": ("MIT OR Apache-2.0", "MIT", True, False, False, False),
}

_SAFE_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXPECTED_ADMISSION_PROFILES: Final[
    dict[
        str,
        tuple[str, str, str, tuple[str, ...], str, bool, bool, bool, str, bool, bool],
    ]
] = {
    "actions-checkout": (
        "github-action",
        "external",
        "approved",
        ("ci",),
        "not-shipped",
        False,
        False,
        False,
        "ci-runner",
        False,
        False,
    ),
    "actions-setup-python": (
        "github-action",
        "external",
        "approved",
        ("ci",),
        "not-shipped",
        False,
        True,
        False,
        "ci-runner",
        False,
        False,
    ),
    "ast-serialize": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "astral-setup-uv": (
        "github-action",
        "external",
        "approved",
        ("ci",),
        "not-shipped",
        False,
        True,
        False,
        "ci-runner",
        False,
        False,
    ),
    "attrs": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "colorama": (
        "python-package",
        "transitive",
        "approved",
        ("development", "test"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "cpython": (
        "interpreter",
        "external",
        "approved",
        ("build", "ci", "development"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        False,
    ),
    "ghostscript": (
        "native-tool",
        "candidate",
        "requires-decision-change",
        (),
        "prohibited-without-decision-change",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "git": (
        "native-tool",
        "external",
        "approved",
        ("ci", "development", "validation"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        True,
    ),
    "gitleaks": (
        "native-tool",
        "external",
        "approved",
        ("ci", "development", "validation"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        True,
    ),
    "iniconfig": (
        "python-package",
        "transitive",
        "approved",
        ("development", "test"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "jsonschema": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        True,
        False,
        "developer-host",
        False,
        True,
    ),
    "jsonschema-specifications": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "librt": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "lxml": (
        "python-package",
        "transitive",
        "approved",
        ("runtime", "worker"),
        "planned",
        True,
        False,
        True,
        "worker-process",
        True,
        True,
    ),
    "mupdf": (
        "native-library",
        "candidate",
        "requires-decision-change",
        (),
        "prohibited-without-decision-change",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "mypy": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        False,
    ),
    "mypy-extensions": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "ocrmypdf": (
        "python-package",
        "direct",
        "planned",
        (),
        "planned",
        False,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "packaging": (
        "python-package",
        "transitive",
        "approved",
        ("development", "runtime", "test", "worker"),
        "planned",
        False,
        False,
        False,
        "worker-process",
        False,
        False,
    ),
    "pathspec": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "pdfium": (
        "native-library",
        "transitive",
        "approved",
        ("runtime", "worker"),
        "planned",
        True,
        False,
        True,
        "worker-process",
        True,
        True,
    ),
    "pikepdf": (
        "python-package",
        "direct",
        "approved",
        ("runtime", "worker"),
        "planned",
        True,
        False,
        True,
        "worker-process",
        True,
        True,
    ),
    "pillow": (
        "python-package",
        "transitive",
        "approved",
        ("runtime", "worker"),
        "planned",
        True,
        False,
        True,
        "worker-process",
        True,
        True,
    ),
    "pluggy": (
        "python-package",
        "transitive",
        "approved",
        ("development", "test"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "poppler": (
        "native-library",
        "candidate",
        "requires-decision-change",
        (),
        "prohibited-without-decision-change",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "pygments": (
        "python-package",
        "transitive",
        "approved",
        ("development", "test"),
        "not-shipped",
        False,
        True,
        False,
        "developer-host",
        False,
        False,
    ),
    "pypdfium2": (
        "python-package",
        "direct",
        "approved",
        ("runtime", "worker"),
        "planned",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "pyside6": (
        "framework",
        "direct",
        "planned",
        (),
        "planned",
        True,
        True,
        False,
        "gui-process",
        True,
        True,
    ),
    "pyside6-addons": (
        "python-package",
        "direct",
        "evaluated",
        (),
        "candidate",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "pyside6-essentials": (
        "python-package",
        "transitive",
        "evaluated",
        (),
        "candidate",
        True,
        True,
        False,
        "worker-process",
        False,
        False,
    ),
    "pytest": (
        "python-package",
        "direct",
        "approved",
        ("development", "test"),
        "not-shipped",
        False,
        True,
        False,
        "developer-host",
        False,
        False,
    ),
    "pyyaml": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        False,
        False,
        "developer-host",
        False,
        True,
    ),
    "qpdf": (
        "native-library",
        "direct",
        "approved",
        ("runtime", "worker"),
        "external-runtime",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "qt-pdf": (
        "framework",
        "candidate",
        "evaluated",
        (),
        "candidate",
        True,
        False,
        True,
        "worker-process",
        True,
        True,
    ),
    "referencing": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "rpds-py": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "ruff": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        False,
    ),
    "shiboken6": (
        "python-package",
        "transitive",
        "evaluated",
        (),
        "candidate",
        True,
        False,
        False,
        "worker-process",
        False,
        False,
    ),
    "tesseract": (
        "native-tool",
        "direct",
        "planned",
        (),
        "planned",
        True,
        True,
        True,
        "worker-process",
        True,
        True,
    ),
    "tesseract-language-data": (
        "data",
        "direct",
        "planned",
        (),
        "planned",
        False,
        False,
        True,
        "data-only",
        True,
        True,
    ),
    "types-jsonschema": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "types-pyyaml": (
        "python-package",
        "direct",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "typing-extensions": (
        "python-package",
        "transitive",
        "approved",
        ("development", "validation"),
        "not-shipped",
        False,
        False,
        False,
        "developer-host",
        False,
        False,
    ),
    "uv": (
        "native-tool",
        "external",
        "approved",
        ("build", "ci", "development"),
        "not-shipped",
        True,
        True,
        False,
        "developer-host",
        False,
        True,
    ),
    "uv-build": (
        "python-package",
        "direct",
        "approved",
        ("build",),
        "not-shipped",
        True,
        False,
        False,
        "developer-host",
        False,
        True,
    ),
}
_REFERENCE_KEYWORDS: Final = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
EXPECTED_SBOM_PROFILES: Final[dict[str, tuple[str, str, str, str | None]]] = {
    "actions-checkout": ("actions/checkout", "application", "GitHub", "upstream-release-inventory"),
    "actions-setup-python": (
        "actions/setup-python",
        "application",
        "GitHub",
        "upstream-release-inventory",
    ),
    "ast-serialize": ("ast-serialize", "library", "mypyc contributors", "uv.lock"),
    "astral-setup-uv": (
        "astral-sh/setup-uv",
        "application",
        "Astral Software Inc.",
        "upstream-release-inventory",
    ),
    "attrs": ("attrs", "library", "attrs contributors", "uv.lock"),
    "colorama": ("colorama", "library", "Colorama contributors", "uv.lock"),
    "cpython": (
        "CPython",
        "application",
        "Python Software Foundation",
        "upstream-release-inventory",
    ),
    "ghostscript": (
        "Ghostscript",
        "application",
        "Artifex Software Inc.",
        "upstream-release-inventory",
    ),
    "git": ("Git", "application", "Git project", "upstream-release-inventory"),
    "gitleaks": ("Gitleaks", "application", "Gitleaks contributors", "upstream-release-inventory"),
    "iniconfig": ("iniconfig", "library", "pytest contributors", "uv.lock"),
    "jsonschema": ("jsonschema", "library", "jsonschema contributors", "uv.lock"),
    "jsonschema-specifications": (
        "jsonschema-specifications",
        "library",
        "jsonschema contributors",
        "uv.lock",
    ),
    "librt": ("librt", "library", "mypyc contributors", "uv.lock"),
    "lxml": ("lxml", "library", "lxml contributors", "upstream-release-inventory"),
    "mupdf": ("MuPDF", "library", "Artifex Software Inc.", "upstream-release-inventory"),
    "mypy": ("mypy", "application", "mypy contributors", "uv.lock"),
    "mypy-extensions": ("mypy-extensions", "library", "mypy contributors", "uv.lock"),
    "ocrmypdf": ("OCRmyPDF", "application", "OCRmyPDF contributors", "upstream-release-inventory"),
    "packaging": ("packaging", "library", "Python Packaging Authority", "uv.lock"),
    "pathspec": ("pathspec", "library", "pathspec contributors", "uv.lock"),
    "pdfium": ("PDFium", "library", "PDFium Authors", "upstream-release-inventory"),
    "pikepdf": ("pikepdf", "library", "pikepdf contributors", "upstream-release-inventory"),
    "pillow": ("Pillow", "library", "Pillow contributors", "upstream-release-inventory"),
    "pluggy": ("pluggy", "library", "pytest contributors", "uv.lock"),
    "poppler": ("Poppler", "library", "Poppler contributors", "upstream-release-inventory"),
    "pygments": ("Pygments", "library", "Pygments contributors", "uv.lock"),
    "pypdfium2": ("pypdfium2", "library", "pypdfium2 contributors", "upstream-release-inventory"),
    "pyside6": (
        "PySide6 family baseline; meta-package not admitted",
        "framework",
        "The Qt Company and Qt Project contributors",
        "upstream-release-inventory",
    ),
    "pyside6-addons": (
        "PySide6 Addons",
        "library",
        "The Qt Company and Qt Project contributors",
        "upstream-release-inventory",
    ),
    "pyside6-essentials": (
        "PySide6 Essentials",
        "library",
        "The Qt Company and Qt Project contributors",
        "upstream-release-inventory",
    ),
    "pytest": ("pytest", "application", "pytest contributors", "uv.lock"),
    "pyyaml": ("PyYAML", "library", "PyYAML contributors", "uv.lock"),
    "qpdf": ("qpdf", "library", "qpdf contributors", "upstream-release-inventory"),
    "qt-pdf": (
        "Qt PDF",
        "framework",
        "The Qt Company and Qt Project contributors",
        "upstream-release-inventory",
    ),
    "referencing": ("referencing", "library", "jsonschema contributors", "uv.lock"),
    "rpds-py": ("rpds-py", "library", "rpds-py contributors", "uv.lock"),
    "ruff": ("Ruff", "application", "Astral Software Inc.", "uv.lock"),
    "shiboken6": (
        "shiboken6",
        "library",
        "The Qt Company and Qt Project contributors",
        "upstream-release-inventory",
    ),
    "tesseract": (
        "Tesseract OCR",
        "application",
        "Tesseract OCR contributors",
        "upstream-release-inventory",
    ),
    "tesseract-language-data": (
        "Tesseract language data",
        "data",
        "Tesseract OCR contributors",
        "upstream-release-inventory",
    ),
    "types-jsonschema": ("types-jsonschema", "library", "typeshed contributors", "uv.lock"),
    "types-pyyaml": ("types-PyYAML", "library", "typeshed contributors", "uv.lock"),
    "typing-extensions": ("typing-extensions", "library", "Python Software Foundation", "uv.lock"),
    "uv": ("uv", "application", "Astral Software Inc.", "upstream-release-inventory"),
    "uv-build": ("uv-build", "library", "Astral Software Inc.", "upstream-release-inventory"),
}
_UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS: Final = frozenset(
    {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef"}
)
_DECISION_HEADING: Final = re.compile(r"^## (PWS-D[0-9]{3})\b", re.MULTILINE)
_NORMALIZE_PACKAGE: Final = re.compile(r"[-_.]+")
_LICENSE_TOKEN: Final = re.compile(r"LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+|[()]")
MAX_LICENSE_CLAUSES: Final = 64
_WINDOWS_ABSOLUTE_PATH: Final = re.compile(r"(?:^|[^A-Za-z])[A-Za-z]:[\\/]")
_POSIX_ABSOLUTE_PATH: Final = re.compile(r"(?:^|[\s\"'(])/(?!/)(?:[^/\s?#]+/)*[^/\s?#]+")
_PROHIBITED_TEXT: Final = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|file://|\\\\[^\\])",
    re.IGNORECASE,
)
_SCHEMA_MESSAGES: Final[dict[object, str]] = {
    "additionalProperties": "unknown members are not permitted",
    "allOf": "value violates a conditional policy",
    "const": "value is not supported",
    "contains": "required array member is missing",
    "else": "value violates a conditional policy",
    "enum": "value is not supported",
    "format": "value has an invalid format",
    "if": "value violates a conditional policy",
    "maxItems": "array length is outside the allowed range",
    "maxLength": "text length is outside the allowed range",
    "maxProperties": "object size is outside the allowed range",
    "maximum": "number is outside the allowed range",
    "minItems": "array length is outside the allowed range",
    "minLength": "text length is outside the allowed range",
    "minProperties": "object size is outside the allowed range",
    "minimum": "number is outside the allowed range",
    "not": "value is explicitly prohibited",
    "oneOf": "value does not match exactly one supported shape",
    "pattern": "value has an invalid format",
    "propertyNames": "object member name has an invalid format",
    "required": "a required member is missing",
    "then": "value violates a conditional policy",
    "type": "value has an invalid JSON type",
    "uniqueItems": "array members must be unique",
}


@dataclass(frozen=True)
class DependencyIssue:
    """One stable, non-disclosing dependency validation error."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document} {self.path}: {self.message}"


@dataclass(frozen=True)
class DependencyFixtureSummary:
    """Counts for the validated canonical and rejected dependency examples."""

    positive_count: int
    negative_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity."""


class InvalidJsonNumberError(ValueError):
    """Raised for floating-point or unsafe integer values."""


def _issue(code: str, document: str, path: str, message: str) -> DependencyIssue:
    return DependencyIssue(code=code, document=document, path=path, message=message)


def _add_issue(issues: list[DependencyIssue], issue: DependencyIssue) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
    elif not any(existing.code == "DEPENDENCY_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "DEPENDENCY_ISSUE_LIMIT",
                issue.document,
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(issues: Iterable[DependencyIssue]) -> list[DependencyIssue]:
    return sorted(issues, key=DependencyIssue.sort_key)


def _globally_bounded_issues(
    issues: Iterable[DependencyIssue], document: str
) -> list[DependencyIssue]:
    """Apply one deterministic diagnostic cap across all semantic collectors."""

    bounded: list[DependencyIssue] = []
    upstream_suppressed = False
    for issue in issues:
        if issue.code == "DEPENDENCY_ISSUE_LIMIT":
            upstream_suppressed = True
            continue
        _add_issue(bounded, issue)
    if upstream_suppressed and not any(issue.code == "DEPENDENCY_ISSUE_LIMIT" for issue in bounded):
        if len(bounded) >= MAX_REPORTED_ISSUES:
            bounded = bounded[: MAX_REPORTED_ISSUES - 1]
        bounded.append(
            _issue(
                "DEPENDENCY_ISSUE_LIMIT",
                document,
                "$",
                "additional validation errors were suppressed",
            )
        )
    return _sorted_issues(bounded)


def _json_path(parts: Iterable[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif isinstance(part, str) and _SAFE_PATH_TOKEN.fullmatch(part):
            rendered += f".{part}"
        else:
            rendered += "[*]"
    return rendered


def _document_label(path: Path, root: Path) -> str:
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        return resolved_path.relative_to(resolved_root).as_posix()
    except OSError, RuntimeError, ValueError:
        return "external-dependency-inventory.json"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise NonStandardJsonConstantError


def _reject_json_float(_value: str) -> NoReturn:
    raise InvalidJsonNumberError


def _parse_bounded_json_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 16:
        raise InvalidJsonNumberError
    parsed = int(value)
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise InvalidJsonNumberError
    return parsed


def _read_bounded_utf8(
    path: Path,
    *,
    document: str,
    maximum_bytes: int,
) -> tuple[str | None, list[DependencyIssue]]:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        return None, [_issue("DEPENDENCY_INPUT_UNREADABLE", document, "$", type(error).__name__)]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                "DEPENDENCY_INPUT_TOO_LARGE",
                document,
                "$",
                "input exceeds its byte limit",
            )
        ]
    try:
        return content.decode("utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _issue(
                "DEPENDENCY_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]


def _json_resource_issue(loaded: Mapping[str, Any], document: str) -> DependencyIssue | None:
    queue: deque[tuple[object, int]] = deque([(loaded, 1)])
    node_count = 0
    while queue:
        value, depth = queue.popleft()
        node_count += 1
        if node_count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return _issue(
                "DEPENDENCY_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON structure exceeds its resource limits",
            )
        if isinstance(value, str):
            if len(value) > MAX_JSON_STRING_CODEPOINTS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON text exceeds its resource limits",
                )
        elif isinstance(value, Mapping):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its resource limits",
                )
            for key, member in value.items():
                queue.append((key, depth + 1))
                queue.append((member, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its resource limits",
                )
            queue.extend((member, depth + 1) for member in value)
    return None


def _load_json_object(
    path: Path,
    *,
    root: Path,
    maximum_bytes: int,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    document = _document_label(path, root)
    text, read_issues = _read_bounded_utf8(
        path,
        document=document,
        maximum_bytes=maximum_bytes,
    )
    if text is None:
        return None, read_issues
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_reject_json_float,
            parse_int=_parse_bounded_json_integer,
        )
    except DuplicateJsonKeyError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_NONSTANDARD_CONSTANT",
                document,
                "$",
                "NaN and Infinity are not permitted",
            )
        ]
    except InvalidJsonNumberError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            _issue(
                "DEPENDENCY_JSON_INVALID",
                document,
                "$",
                f"invalid JSON at line {error.lineno}, column {error.colno}",
            )
        ]
    except RecursionError, ValueError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_INVALID",
                document,
                "$",
                "JSON numeric or nesting syntax is invalid",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "DEPENDENCY_JSON_ROOT_TYPE",
                document,
                "$",
                "root value must be an object",
            )
        ]
    result = cast(dict[str, Any], loaded)
    resource_issue = _json_resource_issue(result, document)
    if resource_issue is not None:
        return None, [resource_issue]
    return result, []


def _walk_mappings(
    value: object, path: tuple[object, ...] = ()
) -> Iterable[tuple[tuple[object, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        yield path, mapping
        for key, member in mapping.items():
            yield from _walk_mappings(member, (*path, key))
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _walk_mappings(member, (*path, index))


def _resolve_json_pointer(schema: Mapping[str, Any], reference: str) -> object | None:
    if reference == "#":
        return schema
    if not reference.startswith("#/"):
        return None
    current: object = schema
    for encoded in reference[2:].split("/"):
        token = unquote(encoded).replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _schema_fingerprint(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_policy_issues(schema: Mapping[str, Any], document: str) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    if schema.get("$schema") != DRAFT_2020_12_DIALECT or schema.get("$id") != SCHEMA_IDENTIFIER:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema dialect or identifier does not match the pinned contract",
            ),
        )
    for path, mapping in _walk_mappings(schema):
        if path and path[-1] != "properties" and ("$id" in mapping or "$schema" in mapping):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                    document,
                    _json_path(path),
                    "nested schema resources are not permitted",
                ),
            )
        if _UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS.intersection(mapping):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_REFERENCE_FEATURE_UNSAFE",
                    document,
                    _json_path(path),
                    "dynamic, recursive, and anchor references are not permitted",
                ),
            )
        for keyword in _REFERENCE_KEYWORDS.intersection(mapping):
            reference = mapping[keyword]
            if keyword != "$ref" or not isinstance(reference, str) or not reference.startswith("#"):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SCHEMA_REFERENCE_UNSAFE",
                        document,
                        _json_path((*path, keyword)),
                        "only local JSON Pointer references are permitted",
                    ),
                )
            elif _resolve_json_pointer(schema, reference) is None:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SCHEMA_REFERENCE_INVALID",
                        document,
                        _json_path((*path, keyword)),
                        "schema reference does not resolve locally",
                    ),
                )
    if _schema_fingerprint(schema) != CANONICAL_SCHEMA_FINGERPRINT:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema fingerprint does not match the reviewed contract",
            ),
        )
    return _sorted_issues(issues)


def load_dependency_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Load, constrain, and self-check the reviewed dependency schema."""

    schema, issues = _load_json_object(
        root / INVENTORY_SCHEMA_PATH,
        root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
    )
    if schema is None:
        return None, issues
    document = INVENTORY_SCHEMA_PATH.as_posix()
    policy_issues = _schema_policy_issues(schema, document)
    if policy_issues:
        return None, policy_issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return None, [
            _issue(
                "DEPENDENCY_SCHEMA_DEFINITION_INVALID",
                document,
                "$",
                "schema definition is invalid",
            )
        ]
    except Exception:
        return None, [
            _issue(
                "DEPENDENCY_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema self-check failed safely",
            )
        ]
    return schema, []


def _schema_validation_issues(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in validator.iter_errors(data):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_VALIDATION",
                    document,
                    _json_path(error.absolute_path),
                    _SCHEMA_MESSAGES.get(error.validator, "value violates the schema"),
                ),
            )
    except Exception:
        return [
            _issue(
                "DEPENDENCY_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation failed safely",
            )
        ]
    return _sorted_issues(issues)


def _normalize_package_name(value: str) -> str:
    return _NORMALIZE_PACKAGE.sub("-", value).casefold()


def _expected_purl(component: Mapping[str, Any]) -> str | None:
    """Return the canonical package URL when schema v1 knows the identity mapping."""

    component_id = cast(str, component["component_id"])
    version = cast(Mapping[str, Any], component["version"])
    selected = cast(str | None, version["selected"])
    if selected is None:
        return None
    if component["ecosystem"] == "python-package":
        return f"pkg:pypi/{component_id}@{selected}"
    base = EXPECTED_PURL_BASES.get(component_id, f"pkg:generic/{component_id}")
    if component["ecosystem"] == "github-action":
        revision = cast(str | None, cast(Mapping[str, Any], component["integrity"])["revision"])
        return None if revision is None else f"{base}@{revision}"
    return f"{base}@{selected}"


def _safe_url(value: str, *, release_url: bool) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if "@" in parsed.netloc or parsed.query or (release_url and parsed.fragment):
        return False
    decoded_path = unquote(parsed.path)
    if (
        "\\" in decoded_path
        or any(part in {".", ".."} for part in decoded_path.split("/"))
        or any(token in parsed.path.casefold() for token in ("%2e", "%2f", "%5c"))
    ):
        return False
    hostname = parsed.hostname.casefold().rstrip(".")
    if (
        not hostname
        or "%" in hostname
        or hostname == "localhost"
        or hostname.endswith((".local", ".localhost"))
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        numeric_label = re.compile(r"(?:0[xX][0-9a-fA-F]+|0[0-7]+|[0-9]+)")
        labels = hostname.split(".")
        if labels and all(numeric_label.fullmatch(label) for label in labels):
            return False
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _text_safety_issues(value: object, document: str) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    queue: deque[tuple[object, tuple[object, ...]]] = deque([(value, ())])
    while queue:
        member, path = queue.popleft()
        if isinstance(member, str):
            if (
                _WINDOWS_ABSOLUTE_PATH.search(member)
                or _POSIX_ABSOLUTE_PATH.search(member)
                or _PROHIBITED_TEXT.search(member)
            ):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SENSITIVE_TEXT_PROHIBITED",
                        document,
                        _json_path(path),
                        "free text must not contain local paths or credential-like data",
                    ),
                )
        elif isinstance(member, Mapping):
            queue.extend((child, (*path, key)) for key, child in member.items())
        elif isinstance(member, list):
            queue.extend((child, (*path, index)) for index, child in enumerate(member))
    return issues


def _license_clauses(
    expression: str, license_refs: Mapping[str, Any]
) -> tuple[frozenset[str], ...] | None:
    """Return bounded disjunctive license branches, or None for unsupported syntax."""

    tokens = _LICENSE_TOKEN.findall(expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return None
    for operator in re.finditer(r"\b(?:AND|OR)\b", expression):
        if (
            operator.start() == 0
            or operator.end() == len(expression)
            or expression[operator.start() - 1] != " "
            or expression[operator.end()] != " "
        ):
            return None

    position = 0

    def parse_factor() -> list[frozenset[str]] | None:
        nonlocal position
        if position >= len(tokens):
            return None
        token = tokens[position]
        if token == "(":
            position += 1
            nested = parse_or()
            if nested is None or position >= len(tokens) or tokens[position] != ")":
                return None
            position += 1
            return nested
        if token in {"AND", "OR", ")"} or (
            token not in KNOWN_LICENSE_IDS and token not in license_refs
        ):
            return None
        position += 1
        return [frozenset({token})]

    def parse_and() -> list[frozenset[str]] | None:
        nonlocal position
        clauses = parse_factor()
        if clauses is None:
            return None
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            right = parse_factor()
            if right is None or len(clauses) * len(right) > MAX_LICENSE_CLAUSES:
                return None
            clauses = [left | right_clause for left in clauses for right_clause in right]
        return clauses

    def parse_or() -> list[frozenset[str]] | None:
        nonlocal position
        clauses = parse_and()
        if clauses is None:
            return None
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            right = parse_and()
            if right is None or len(clauses) + len(right) > MAX_LICENSE_CLAUSES:
                return None
            clauses.extend(right)
        return clauses

    clauses = parse_or()
    if clauses is None or position != len(tokens) or not clauses:
        return None
    unique_clauses = set(clauses)
    return tuple(sorted(unique_clauses, key=lambda clause: tuple(sorted(clause))))


def _license_expression_valid(expression: str, license_refs: Mapping[str, Any]) -> bool:
    return _license_clauses(expression, license_refs) is not None


def _component_semantic_issues(
    data: Mapping[str, Any], document: str
) -> tuple[dict[str, Mapping[str, Any]], list[DependencyIssue]]:
    components = cast(list[Mapping[str, Any]], data["components"])
    license_refs = cast(Mapping[str, Any], data["license_refs"])
    reviewed_on = date.fromisoformat(cast(str, data["reviewed_on"]))
    issues: list[DependencyIssue] = []
    indexed: dict[str, Mapping[str, Any]] = {}
    purls: set[str] = set()
    if set(license_refs) != set(EXPECTED_LICENSE_REF_REVIEW_STATUS):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LICENSE_REF_UNREVIEWED",
                document,
                "$.license_refs",
                "schema v1 accepts only the reviewed custom-license definitions",
            ),
        )
    for reference_id, reference_value in license_refs.items():
        reference = cast(Mapping[str, Any], reference_value)
        expected_status = EXPECTED_LICENSE_REF_REVIEW_STATUS.get(reference_id)
        expected_identity = EXPECTED_LICENSE_REF_IDENTITIES.get(reference_id)
        actual_identity = (
            reference["name"],
            reference["terms_url"],
            reference["review_status"],
        )
        if (
            expected_status is None
            or reference["review_status"] != expected_status
            or actual_identity != expected_identity
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_REF_UNREVIEWED",
                    document,
                    "$.license_refs[*].review_status",
                    "custom-license review status does not match the reviewed policy",
                ),
            )
        if not _safe_url(cast(str, reference["terms_url"]), release_url=False):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SOURCE_URL_UNSAFE",
                    document,
                    "$.license_refs[*].terms_url",
                    "custom-license terms must use a credential-free public HTTPS URL",
                ),
            )
    for index, component in enumerate(components):
        path = f"$.components[{index}]"
        component_id = cast(str, component["component_id"])
        status = cast(str, component["admission_status"])
        version = cast(Mapping[str, Any], component["version"])
        profile_binary = cast(Mapping[str, Any], component["binary"])
        profile_security = cast(Mapping[str, Any], component["security"])
        profile_packaging = cast(Mapping[str, Any], component["packaging"])
        actual_admission_profile = (
            cast(str, component["ecosystem"]),
            cast(str, component["relationship"]),
            status,
            tuple(sorted(cast(list[str], component["active_scopes"]))),
            cast(str, profile_packaging["ship_state"]),
            cast(bool, profile_binary["contains_native_code"]),
            cast(bool, profile_binary["installs_executable"]),
            cast(bool, profile_binary["processes_untrusted_documents"]),
            cast(str, profile_security["boundary"]),
            cast(bool, profile_security["adapter_required"]),
            cast(bool, profile_security["contract_tests_required"]),
        )
        if EXPECTED_ADMISSION_PROFILES.get(component_id) != actual_admission_profile:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_ADMISSION_PROFILE_INVALID",
                    document,
                    path,
                    "component scope, packaging, binary, and boundary profile must remain reviewed",
                ),
            )
        if component_id in indexed:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_COMPONENT_ID_DUPLICATE",
                    document,
                    f"{path}.component_id",
                    "component IDs must be unique",
                ),
            )
        else:
            indexed[component_id] = component

        sbom = cast(Mapping[str, Any], component["sbom"])
        purl = cast(str | None, sbom["purl"])
        actual_sbom_profile = (
            cast(str, component["name"]),
            cast(str, sbom["component_type"]),
            cast(str, sbom["supplier"]),
            cast(str | None, sbom["transitive_components_from"]),
        )
        if EXPECTED_SBOM_PROFILES.get(component_id) != actual_sbom_profile:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SBOM_INPUT_INVALID",
                    document,
                    f"{path}.sbom",
                    "component name, type, supplier, and closure source must remain reviewed",
                ),
            )
        expected_purl = _expected_purl(component)
        if purl is not None:
            if purl in purls:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_PURL_DUPLICATE",
                        document,
                        f"{path}.sbom.purl",
                        "non-null package URLs must be unique",
                    ),
                )
            purls.add(purl)
        if status in {"approved", "evaluated"} and (
            purl is None or expected_purl is None or purl != expected_purl
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SBOM_INPUT_INVALID",
                    document,
                    f"{path}.sbom.purl",
                    "concrete dependency package URL must match its reviewed identity and revision",
                ),
            )

        integrity = cast(Mapping[str, Any], component["integrity"])
        pin_kind = cast(str, version["pin_kind"])
        verification = cast(str, integrity["verification"])
        if EXPECTED_PIN_VERIFICATION.get(pin_kind) != verification:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.integrity.verification",
                    "version pin kind and integrity verification must use a reviewed pair",
                ),
            )
        selected = cast(str | None, version["selected"])
        if pin_kind in {"exact", "lockfile"} and (
            selected is None or version["constraint"] != f"=={selected}"
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.version.constraint",
                    "exact and lockfile constraints must equal the selected release",
                ),
            )
        artifacts: set[str] = set()
        for hash_index, hash_record in enumerate(
            cast(list[Mapping[str, Any]], integrity["hashes"])
        ):
            artifact = cast(str, hash_record["artifact"])
            if artifact in artifacts:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_HASH_ARTIFACT_DUPLICATE",
                        document,
                        f"{path}.integrity.hashes[{hash_index}].artifact",
                        "artifact names must be unique within a component",
                    ),
                )
            artifacts.add(artifact)

        license_data = cast(Mapping[str, Any], component["license"])
        expression = cast(str, license_data["expression"])
        actual_license_policy = (
            expression,
            cast(str | None, license_data.get("selected_route")),
            cast(bool, license_data["notice_required"]),
            cast(bool, license_data["source_offer_required"]),
            cast(bool, license_data["relinking_required"]),
            cast(bool, license_data["distribution_altering"]),
        )
        if EXPECTED_LICENSE_POLICIES.get(component_id) != actual_license_policy:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_IDENTITY_INVALID",
                    document,
                    f"{path}.license",
                    "component license policy must match the reviewed upstream identity",
                ),
            )
        license_clauses = _license_clauses(expression, license_refs)
        if license_clauses is None:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_EXPRESSION_INVALID",
                    document,
                    f"{path}.license.expression",
                    "license expression uses an unsupported or malformed policy identifier",
                ),
            )
        selected_route = cast(str | None, license_data.get("selected_route"))
        if (
            status == "approved"
            and license_clauses is not None
            and len(license_clauses) > 1
            and selected_route is None
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_REQUIRED",
                    document,
                    f"{path}.license.selected_route",
                    "approved dual-license components require one selected route",
                ),
            )
        selected_clauses = (
            tuple(clause for clause in license_clauses if selected_route in clause)
            if license_clauses is not None and selected_route is not None
            else ()
        )
        if selected_route is not None and len(selected_clauses) != 1:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_INVALID",
                    document,
                    f"{path}.license.selected_route",
                    "selected license route must identify exactly one complete expression branch",
                ),
            )

        packaging = cast(Mapping[str, Any], component["packaging"])
        ship_state = cast(str, packaging["ship_state"])
        distribution_altering = cast(bool, license_data["distribution_altering"])
        active_scopes = set(cast(list[str], component["active_scopes"]))
        shipping_or_runtime = ship_state != "not-shipped" or bool(
            active_scopes.intersection({"data", "runtime", "worker"})
        )
        effective_clauses = (
            selected_clauses
            if selected_route is not None and len(selected_clauses) == 1
            else (license_clauses or ())
        )
        candidate_only_refs = {
            identifier
            for clause in effective_clauses
            for identifier in clause
            if identifier in license_refs
            and cast(Mapping[str, Any], license_refs[identifier])["review_status"]
            == "candidate-only"
        }
        if status == "approved" and candidate_only_refs:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_INVALID",
                    document,
                    f"{path}.license",
                    "candidate-only custom terms cannot authorize an approved component",
                ),
            )

        def clause_requires_decision(clause: frozenset[str], runtime_sensitive: bool) -> bool:
            for identifier in clause:
                if identifier.startswith("AGPL-"):
                    return True
                if runtime_sensitive and identifier.startswith("GPL-"):
                    return True
                if identifier in license_refs:
                    reference = cast(Mapping[str, Any], license_refs[identifier])
                    if reference["review_status"] == "decision-required":
                        return True
            return False

        expression_requires_decision = bool(effective_clauses) and all(
            clause_requires_decision(clause, shipping_or_runtime) for clause in effective_clauses
        )
        requires_decision = (
            component_id in HARD_GATED_COMPONENT_IDS
            or distribution_altering
            or expression_requires_decision
        )
        if requires_decision and (
            status != "requires-decision-change"
            or ship_state != "prohibited-without-decision-change"
            or not distribution_altering
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
                    document,
                    f"{path}.license",
                    "distribution-changing terms require a decision change and prohibited ship state",
                ),
            )
        if status == "requires-decision-change" and (
            not distribution_altering or ship_state != "prohibited-without-decision-change"
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
                    document,
                    f"{path}.admission_status",
                    "decision-gated components must remain explicitly prohibited",
                ),
            )

        notice_closure_refs = {
            identifier
            for clause in effective_clauses
            for identifier in clause
            if identifier in license_refs
            and cast(Mapping[str, Any], license_refs[identifier])["review_status"]
            == "notice-closure-required"
        }
        if (
            status == "approved"
            and ship_state != "not-shipped"
            and notice_closure_refs
            and "third-party-notices" not in packaging["redistribution_assets"]
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_NOTICE_CLOSURE_INVALID",
                    document,
                    f"{path}.packaging.redistribution_assets",
                    "selected custom license closure requires third-party notices",
                ),
            )
        if status == "approved" and ship_state in {"bundled", "shipped"}:
            redistribution_assets = set(cast(list[str], packaging["redistribution_assets"]))
            effective_identifiers = {
                identifier for clause in effective_clauses for identifier in clause
            }
            derived_notice_required = bool(effective_identifiers)
            derived_source_required = any(
                identifier.startswith(("AGPL-", "GPL-", "LGPL-", "MPL-"))
                for identifier in effective_identifiers
            )
            derived_relinking_required = any(
                identifier.startswith("LGPL-") for identifier in effective_identifiers
            )
            declared_obligations_complete = (
                (not derived_notice_required or bool(license_data["notice_required"]))
                and (not derived_source_required or bool(license_data["source_offer_required"]))
                and (not derived_relinking_required or bool(license_data["relinking_required"]))
            )
            required_assets: set[str] = {"license-text", "notice"}
            if derived_source_required or license_data["source_offer_required"]:
                required_assets.add("corresponding-source")
            if derived_relinking_required or license_data["relinking_required"]:
                required_assets.add("relinkable-library")
            if not declared_obligations_complete or not required_assets.issubset(
                redistribution_assets
            ):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_LICENSE_OBLIGATION_ASSET_MISSING",
                        document,
                        f"{path}.packaging.redistribution_assets",
                        "shipped license obligations require their redistribution assets",
                    ),
                )

        binary = cast(Mapping[str, Any], component["binary"])
        security = cast(Mapping[str, Any], component["security"])
        decision_refs = set(cast(list[str], component["decision_refs"]))
        task_refs = set(cast(list[str], component["task_refs"]))
        known_document_processor = component_id in KNOWN_DOCUMENT_PROCESSOR_IDS
        expected_document_ecosystem = EXPECTED_DOCUMENT_ECOSYSTEMS.get(component_id)
        if (
            expected_document_ecosystem is not None
            and component["ecosystem"] != expected_document_ecosystem
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SECURITY_CAPABILITY_UNREVIEWED",
                    document,
                    f"{path}.ecosystem",
                    "known document capabilities must retain their reviewed ecosystem profile",
                ),
            )
        known_document_data = component["ecosystem"] == "data" and (
            component_id == "tesseract-language-data"
            or "PWS-D016" in decision_refs
            or bool(task_refs.intersection({"PWS-0204", "PWS-0205"}))
        )
        processes_untrusted_documents = bool(binary["processes_untrusted_documents"])
        if (known_document_processor or known_document_data) and not processes_untrusted_documents:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SECURITY_BOUNDARY_INVALID",
                    document,
                    f"{path}.binary.processes_untrusted_documents",
                    "known document-processing capability cannot be self-declared away",
                ),
            )
        if processes_untrusted_documents or known_document_processor or known_document_data:
            expected_boundaries = (
                {"data-only", "worker-process"} if known_document_data else {"worker-process"}
            )
            if (
                security["boundary"] not in expected_boundaries
                or not security["adapter_required"]
                or not security["contract_tests_required"]
            ):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SECURITY_BOUNDARY_INVALID",
                        document,
                        f"{path}.security",
                        "hostile document processors require an isolated boundary, adapter, and tests",
                    ),
                )
        if (
            status == "approved"
            and active_scopes.intersection({"data", "runtime", "worker"})
            and (
                binary["contains_native_code"]
                or component["ecosystem"] in {"framework", "native-library"}
            )
            and component_id not in KNOWN_DOCUMENT_PROCESSOR_IDS
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SECURITY_CAPABILITY_UNREVIEWED",
                    document,
                    path,
                    "new native runtime capabilities require an explicit policy classification",
                ),
            )

        maintenance = cast(Mapping[str, Any], component["maintenance"])
        if status in {"approved", "evaluated"} and maintenance["status"] == "unselected":
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.maintenance.status",
                    "approved and evaluated dependencies require reviewed maintenance evidence",
                ),
            )
        if date.fromisoformat(cast(str, maintenance["checked_on"])) > reviewed_on:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REVIEW_DATE_ORDER_INVALID",
                    document,
                    f"{path}.maintenance.checked_on",
                    "component review date cannot follow the inventory review date",
                ),
            )

        source = cast(Mapping[str, Any], component["source"])
        project_url = cast(str, source["project_url"])
        if EXPECTED_PROJECT_URLS.get(component_id) != project_url:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SOURCE_IDENTITY_INVALID",
                    document,
                    f"{path}.source.project_url",
                    "component project URL must match its reviewed upstream identity",
                ),
            )
        urls: list[tuple[str, str, bool]] = [
            ("project_url", project_url, False),
            ("license_url", cast(str, source["license_url"]), False),
        ]
        release_url = cast(str | None, source["release_url"])
        if release_url is not None:
            urls.append(("release_url", release_url, True))
        urls.append(("evidence_url", cast(str, maintenance["evidence_url"]), False))
        artifact_evidence = cast(Mapping[str, Any] | None, component.get("artifact_evidence"))
        if artifact_evidence is not None:
            urls.append(("artifact_evidence.url", cast(str, artifact_evidence["url"]), True))
        renderer = cast(Mapping[str, Any] | None, component.get("renderer_assessment"))
        if renderer is not None:
            for criterion_name, criterion in renderer.items():
                criterion_data = cast(Mapping[str, Any], criterion)
                for evidence_url in cast(list[str], criterion_data["evidence_urls"]):
                    urls.append((f"renderer_assessment.{criterion_name}", evidence_url, False))
        for member_name, url, is_release in urls:
            if not _safe_url(url, release_url=is_release):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SOURCE_URL_UNSAFE",
                        document,
                        f"{path}.{member_name}",
                        "source and evidence URLs must be credential-free public HTTPS URLs",
                    ),
                )
        project_parts = [part for part in urlsplit(project_url).path.split("/") if part]
        license_url = cast(str, source["license_url"])
        license_parts = [part for part in urlsplit(license_url).path.split("/") if part]
        if EXPECTED_LICENSE_URLS.get(component_id) != license_url:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SOURCE_IDENTITY_INVALID",
                    document,
                    f"{path}.source.license_url",
                    "license URL must match the reviewed component evidence",
                ),
            )
        if urlsplit(project_url).hostname == "github.com" and (
            urlsplit(license_url).hostname != "github.com"
            or len(project_parts) < 2
            or license_parts[:2] != project_parts[:2]
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SOURCE_IDENTITY_INVALID",
                    document,
                    f"{path}.source.license_url",
                    "GitHub license evidence must remain in the reviewed upstream repository",
                ),
            )
        if artifact_evidence is not None:
            evidence_artifact = cast(str, artifact_evidence["artifact"])
            evidence_url = cast(str, artifact_evidence["url"])
            evidence_sha256 = cast(str, artifact_evidence["sha256"])
            evidence_host = urlsplit(evidence_url).hostname
            evidence_parts = [
                part for part in unquote(urlsplit(evidence_url).path).split("/") if part
            ]
            expected_artifact_url = EXPECTED_ARTIFACT_URLS.get(component_id)
            if expected_artifact_url is not None:
                evidence_origin_valid = evidence_url == expected_artifact_url
            elif component["ecosystem"] == "python-package":
                evidence_origin_valid = evidence_host == "files.pythonhosted.org"
            elif urlsplit(project_url).hostname == "github.com":
                evidence_origin_valid = (
                    evidence_host == "github.com"
                    and len(evidence_parts) == 6
                    and evidence_parts[:4] == [*project_parts[:2], "releases", "download"]
                    and selected is not None
                    and evidence_parts[4].casefold()
                    in {selected.casefold(), f"v{selected.casefold()}"}
                    and evidence_parts[5] == cast(str, artifact_evidence["artifact"])
                )
            else:
                evidence_origin_valid = evidence_host == urlsplit(project_url).hostname
            evidence_hash_pair = (evidence_sha256, evidence_artifact)
            integrity_hash_pairs = {
                (cast(str, record["value"]), cast(str, record["artifact"]))
                for record in cast(list[Mapping[str, Any]], integrity["hashes"])
            }
            evidence_valid = (
                Path(unquote(urlsplit(evidence_url).path)).name == evidence_artifact
                and evidence_origin_valid
                and len(set(evidence_sha256)) > 1
                and (verification == "git-commit" or evidence_hash_pair in integrity_hash_pairs)
            )
            if not evidence_valid:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RELEASE_PIN_INVALID",
                        document,
                        f"{path}.artifact_evidence",
                        "artifact evidence must bind a reviewed source, filename, size, target, and hash",
                    ),
                )

        is_renderer = component_id in RENDERER_COMPONENT_IDS or "PWS-D017" in decision_refs
        if is_renderer:
            if renderer is None:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RENDERER_ASSESSMENT_REQUIRED",
                        document,
                        f"{path}.renderer_assessment",
                        "renderer families require all five assessment criteria",
                    ),
                )
            elif status == "approved":
                if any(
                    cast(Mapping[str, Any], criterion)["status"] != "acceptable"
                    for criterion in renderer.values()
                ):
                    _add_issue(
                        issues,
                        _issue(
                            "DEPENDENCY_RENDERER_ADMISSION_INVALID",
                            document,
                            f"{path}.renderer_assessment",
                            "approved renderers require acceptable evidence for every criterion",
                        ),
                    )

        strong_pin = pin_kind in {"exact", "git-commit", "lockfile"}
        concrete_record = status in {"approved", "evaluated"}
        release_state = ship_state in {"bundled", "external-runtime", "shipped"}
        if concrete_record and (release_url is None or purl is None):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.source.release_url",
                    "approved and evaluated dependencies require a concrete release identity",
                ),
            )
        if status == "evaluated" and (
            not strong_pin
            or ship_state != "candidate"
            or "sbom" not in packaging["redistribution_assets"]
            or (
                component["ecosystem"] == "python-package"
                and (pin_kind != "lockfile" or verification != "uv-lock")
            )
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.admission_status",
                    "evaluated dependencies require candidate packaging and exact provenance",
                ),
            )
        if release_state and (
            status != "approved"
            or not strong_pin
            or release_url is None
            or purl is None
            or artifact_evidence is None
            or "sbom" not in packaging["redistribution_assets"]
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELEASE_PIN_INVALID",
                    document,
                    f"{path}.packaging",
                    "release components require admitted exact provenance and SBOM mapping",
                ),
            )
        if concrete_record and strong_pin and release_url is not None and selected is not None:
            parsed_release = urlsplit(release_url)
            release_parts = [
                segment for segment in unquote(parsed_release.path).split("/") if segment
            ]
            selected_release_segments = {selected.casefold(), f"v{selected.casefold()}"}
            release_identity_valid = False
            expected_release_url = EXPECTED_RELEASE_URLS.get(component_id)
            if expected_release_url is not None:
                release_identity_valid = release_url == expected_release_url
            elif component["ecosystem"] == "python-package":
                release_identity_valid = (
                    parsed_release.hostname == "pypi.org"
                    and len(release_parts) == 3
                    and release_parts[0] == "project"
                    and _normalize_package_name(release_parts[1]) == component_id
                    and release_parts[2].casefold() == selected.casefold()
                )
            elif urlsplit(project_url).hostname == "github.com":
                release_identity_valid = (
                    parsed_release.hostname == "github.com"
                    and len(project_parts) >= 2
                    and release_parts[:4] == [*project_parts[:2], "releases", "tag"]
                    and len(release_parts) == 5
                    and release_parts[4].casefold() in selected_release_segments
                )
            else:
                release_identity_valid = parsed_release.hostname == urlsplit(
                    project_url
                ).hostname and any(
                    segment.casefold() in selected_release_segments for segment in release_parts
                )
            if not release_identity_valid:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RELEASE_PIN_INVALID",
                        document,
                        f"{path}.source.release_url",
                        "release URL must identify the selected version",
                    ),
                )
        if verification in {"sha256", "uv-lock"} and selected is not None:
            expected_artifact = EXPECTED_TOOL_ARTIFACTS.get(component_id)
            artifact_identity_pattern = re.compile(
                rf"^{re.escape(component_id.casefold().replace('_', '-'))}-"
                rf"{re.escape(selected.casefold().replace('_', '-'))}(?:[.-]|$)"
            )
            artifact_names = {
                cast(str, hash_record["artifact"])
                for hash_record in cast(list[Mapping[str, Any]], integrity["hashes"])
            }
            artifact_identity_valid = (
                artifact_names == {expected_artifact}
                if expected_artifact is not None
                else bool(artifact_names)
                and all(
                    artifact_identity_pattern.match(artifact.casefold().replace("_", "-"))
                    is not None
                    for artifact in artifact_names
                )
            )
            if not artifact_identity_valid:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RELEASE_PIN_INVALID",
                        document,
                        f"{path}.integrity.hashes",
                        "hash artifact names must identify the selected component release",
                    ),
                )
    return indexed, _sorted_issues(issues)


def _relationship_semantic_issues(
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    relationships = cast(list[Mapping[str, Any]], data["relationships"])
    issues: list[DependencyIssue] = []
    seen: set[tuple[str, str, str]] = set()
    actual_relationships: set[tuple[str, str, str, bool]] = set()
    adjacency: dict[str, set[str]] = {}
    for index, relationship in enumerate(relationships):
        path = f"$.relationships[{index}]"
        source = cast(str, relationship["from"])
        target = cast(str, relationship["to"])
        relation_type = cast(str, relationship["type"])
        enabled = cast(bool, relationship["enabled"])
        identity = (source, relation_type, target)
        actual_relationships.add((source, relation_type, target, enabled))
        if identity in seen:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_DUPLICATE",
                    document,
                    path,
                    "relationship records must be unique",
                ),
            )
        seen.add(identity)
        if source not in components or target not in components:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_REFERENCE_UNKNOWN",
                    document,
                    path,
                    "relationship endpoints must identify known components",
                ),
            )
            continue
        if source == target:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_SELF_REFERENCE",
                    document,
                    path,
                    "a component cannot depend on itself",
                ),
            )
        if enabled and (
            components[source]["admission_status"] != "approved"
            or components[target]["admission_status"] != "approved"
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID",
                    document,
                    path,
                    "enabled relationships require admitted endpoints",
                ),
            )
        if enabled and source != target:
            adjacency.setdefault(source, set()).add(target)

    if not REQUIRED_REVIEWED_RELATIONSHIPS.issubset(actual_relationships):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_RELATIONSHIP_PROFILE_INVALID",
                document,
                "$.relationships",
                "reviewed embedded dependency relationships must retain their recorded activation",
            ),
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> bool:
        if component_id in visiting:
            return True
        if component_id in visited:
            return False
        visiting.add(component_id)
        if any(visit(target) for target in adjacency.get(component_id, set())):
            return True
        visiting.remove(component_id)
        visited.add(component_id)
        return False

    if any(
        visit(component_id) for component_id in sorted(adjacency) if component_id not in visited
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_RELATIONSHIP_CYCLE",
                document,
                "$.relationships",
                "enabled dependency relationships must be acyclic",
            ),
        )
    return _sorted_issues(issues)


def _authority_reference_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        decisions_text = (root / "DECISIONS.md").read_text(encoding="utf-8")
        task_data = yaml.safe_load((root / "TASKS.yaml").read_text(encoding="utf-8"))
        tasks = cast(list[Mapping[str, Any]], cast(Mapping[str, Any], task_data)["tasks"])
    except OSError, UnicodeError, KeyError, TypeError, yaml.YAMLError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "repository authority inputs could not be read safely",
            )
        ]
    known_decisions = set(_DECISION_HEADING.findall(decisions_text))
    task_by_id = {cast(str, task["id"]): task for task in tasks if isinstance(task.get("id"), str)}
    known_tasks = set(task_by_id)
    for component_id, component in components.items():
        for decision_ref in cast(list[str], component["decision_refs"]):
            if decision_ref not in known_decisions:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_DECISION_REFERENCE_UNKNOWN",
                        document,
                        "$.components[*].decision_refs",
                        "component references an unknown frozen decision",
                    ),
                )
        for task_ref in cast(list[str], component["task_refs"]):
            if task_ref not in known_tasks:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_TASK_REFERENCE_UNKNOWN",
                        document,
                        "$.components[*].task_refs",
                        "component references an unknown task",
                    ),
                )
        component_decisions = set(cast(list[str], component["decision_refs"]))
        component_tasks = set(cast(list[str], component["task_refs"]))
        if "PWS-D034" not in component_decisions or "PWS-0005" not in component_tasks:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                    document,
                    "$.components[*].decision_refs",
                    "every dependency record must retain baseline governance references",
                ),
            )
        is_renderer = component_id in RENDERER_COMPONENT_IDS or "PWS-D017" in component_decisions
        if is_renderer and (
            "PWS-D017" not in component_decisions or "PWS-0106" not in component_tasks
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                    document,
                    "$.components[*].decision_refs",
                    "renderer families must reference the renderer decision",
                ),
            )
        renderer_task = task_by_id.get("PWS-0106")
        if (
            is_renderer
            and component["admission_status"] == "approved"
            and (renderer_task is None or renderer_task.get("status") != "done")
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RENDERER_ADMISSION_INVALID",
                    document,
                    "$.components[*].admission_status",
                    "renderer admission requires completed selection-task evidence",
                ),
            )
        admission_gates = set(ADMISSION_TASK_GATES.get(component_id, frozenset()))
        if "PWS-D016" in component_decisions and component["ecosystem"] == "data":
            admission_gates.update({"PWS-0204", "PWS-0205"})
        if not admission_gates.issubset(component_tasks):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                    document,
                    "$.components[*].task_refs",
                    "dependency family must reference every admission-gate task",
                ),
            )
        if component["admission_status"] == "approved" and any(
            task_by_id.get(task_id, {}).get("status") != "done" for task_id in admission_gates
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                    document,
                    "$.components[*].admission_status",
                    "dependency admission requires completed implementation-task evidence",
                ),
            )
    missing = REQUIRED_COMPONENT_IDS.difference(components)
    if missing:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_REQUIRED_FAMILY_MISSING",
                document,
                "$.components",
                "one or more required dependency families are absent",
            ),
        )
    for decision_id, component_ids in REQUIRED_FAMILY_DECISIONS.items():
        for component_id in component_ids.intersection(components):
            if decision_id not in components[component_id]["decision_refs"]:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                        document,
                        "$.components[*].decision_refs",
                        "required dependency family is missing its frozen decision reference",
                    ),
                )
    return _sorted_issues(issues)


def _load_toml(path: Path) -> Mapping[str, Any]:
    with path.open("rb") as stream:
        return cast(Mapping[str, Any], tomllib.load(stream))


def _lock_reconciliation_issues(
    root: Path,
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        lock = _load_toml(root / "uv.lock")
        packages = cast(list[Mapping[str, Any]], lock["package"])
        python_minor = (root / ".python-version").read_text(encoding="utf-8").strip()
    except OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "lockfile could not be read safely",
            )
        ]
    if lock.get("version") != 1 or lock.get("requires-python") != f"=={python_minor}.*":
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_METADATA_MISMATCH",
                document,
                "$.components",
                "lock format and Python target must match reviewed repository policy",
            ),
        )
    locked: dict[str, Mapping[str, Any]] = {}
    root_package: Mapping[str, Any] | None = None
    seen_lock_names: set[str] = set()
    for package in packages:
        package_name = package.get("name")
        if not isinstance(package_name, str):
            return [
                _issue(
                    "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                    document,
                    "$",
                    "lockfile package records could not be interpreted safely",
                )
            ]
        normalized_name = _normalize_package_name(package_name)
        if normalized_name in seen_lock_names:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_COMPONENT_MISMATCH",
                    document,
                    "$.components",
                    "lockfile package names must be unique after normalization",
                ),
            )
            continue
        seen_lock_names.add(normalized_name)
        if normalized_name == "paperworkshell":
            root_package = package
        else:
            locked[normalized_name] = package
    inventoried = {
        component_id
        for component_id, component in components.items()
        if component["ecosystem"] == "python-package"
        and component["admission_status"] in {"approved", "evaluated"}
        and component_id != "uv-build"
    }
    if set(locked) != inventoried:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_COMPONENT_MISMATCH",
                document,
                "$.components",
                "reviewed concrete Python component set must exactly match the resolved lock",
            ),
        )
    lock_claims = {
        component_id
        for component_id, component in components.items()
        if cast(Mapping[str, Any], component["integrity"])["verification"] == "uv-lock"
    }
    if lock_claims != set(locked):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_COMPONENT_MISMATCH",
                document,
                "$.components",
                "uv-lock integrity claims must exactly match resolved lock packages",
            ),
        )
    for component_id in sorted(set(locked).intersection(inventoried)):
        package = locked[component_id]
        component = components[component_id]
        version = cast(Mapping[str, Any], component["version"])
        if version["selected"] != package["version"]:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_VERSION_MISMATCH",
                    document,
                    "$.components[*].version.selected",
                    "selected Python version must match the lock",
                ),
            )
        package_source = cast(Mapping[str, Any] | None, package.get("source"))
        if package_source != {"registry": "https://pypi.org/simple"}:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_SOURCE_MISMATCH",
                    document,
                    "$.components[*].source",
                    "approved Python packages must resolve from the reviewed registry",
                ),
            )
        sdist_value = package.get("sdist")
        sdist = cast(Mapping[str, Any], sdist_value) if isinstance(sdist_value, Mapping) else None
        wheels_value = package.get("wheels")
        wheel_records_valid = isinstance(wheels_value, list) and all(
            isinstance(wheel, Mapping) for wheel in wheels_value
        )
        wheels = cast(list[Mapping[str, Any]], wheels_value) if wheel_records_valid else []
        lock_version = cast(str, package["version"])
        expected_artifact_pattern = re.compile(
            rf"^{re.escape(component_id.casefold().replace('_', '-'))}-"
            rf"{re.escape(lock_version.casefold().replace('_', '-'))}(?:[.-]|$)"
        )
        artifact_records = ([sdist] if sdist is not None else []) + wheels
        artifact_records_valid = wheel_records_valid and bool(wheels)
        for artifact_record in artifact_records:
            artifact_url = artifact_record.get("url")
            artifact_hash = artifact_record.get("hash")
            artifact_size = artifact_record.get("size")
            if not isinstance(artifact_url, str):
                artifact_records_valid = False
                continue
            parsed_artifact_url = urlsplit(artifact_url)
            artifact_name = Path(unquote(parsed_artifact_url.path)).name
            normalized_artifact_name = artifact_name.casefold().replace("_", "-")
            if (
                not _safe_url(artifact_url, release_url=True)
                or parsed_artifact_url.hostname != "files.pythonhosted.org"
                or expected_artifact_pattern.match(normalized_artifact_name) is None
                or not isinstance(artifact_hash, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_hash) is None
                or type(artifact_size) is not int
                or artifact_size <= 0
            ):
                artifact_records_valid = False
        if not artifact_records_valid:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_SOURCE_MISMATCH",
                    document,
                    "$.components[*].source",
                    "locked artifacts must match the reviewed PyPI origin, name, version, and hash shape",
                ),
            )
        artifact_evidence = cast(Mapping[str, Any] | None, component.get("artifact_evidence"))
        if artifact_evidence is not None and not any(
            artifact_record.get("url") == artifact_evidence["url"]
            and artifact_record.get("size") == artifact_evidence["size_bytes"]
            and artifact_record.get("hash") == f"sha256:{artifact_evidence['sha256']}"
            and Path(urlsplit(cast(str, artifact_record.get("url", ""))).path).name
            == artifact_evidence["artifact"]
            for artifact_record in artifact_records
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_HASH_MISMATCH",
                    document,
                    "$.components[*].artifact_evidence",
                    "Python artifact evidence must exactly match the resolved lock record",
                ),
            )
        inventory_hashes = cast(
            list[Mapping[str, Any]], cast(Mapping[str, Any], component["integrity"])["hashes"]
        )
        actual_hashes = {
            (cast(str, item["value"]), cast(str, item["artifact"])) for item in inventory_hashes
        }
        compatible_windows_wheels = [
            wheel
            for wheel in wheels
            if isinstance(wheel.get("url"), str)
            and (
                cast(str, wheel["url"]).casefold().endswith("-none-any.whl")
                or (
                    cast(str, wheel["url"]).casefold().endswith("-win_amd64.whl")
                    and "-cp314t-" not in cast(str, wheel["url"]).casefold()
                )
            )
        ]
        reviewed_artifacts = ([sdist] if sdist is not None else []) + compatible_windows_wheels
        expected_hashes = {
            (
                cast(str, artifact_record["hash"]).removeprefix("sha256:"),
                Path(urlsplit(cast(str, artifact_record["url"])).path).name,
            )
            for artifact_record in reviewed_artifacts
        }
        if not expected_hashes or actual_hashes != expected_hashes:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_HASH_MISMATCH",
                    document,
                    "$.components[*].integrity.hashes",
                    "recorded release evidence must match locked source and Windows artifacts",
                ),
            )

    expected_edges: set[tuple[str, str]] = set()
    for component_id, package in locked.items():
        for dependency in cast(list[Mapping[str, Any]], package.get("dependencies", [])):
            target = _normalize_package_name(cast(str, dependency["name"]))
            if target in locked:
                expected_edges.add((component_id, target))
    recorded_edges = {
        (cast(str, relationship["from"]), cast(str, relationship["to"]))
        for relationship in cast(list[Mapping[str, Any]], data["relationships"])
        if relationship["type"] == "depends-on"
        and relationship["from"] in locked
        and relationship["to"] in locked
    }
    if recorded_edges != expected_edges:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH",
                document,
                "$.relationships",
                "recorded Python dependency edges must exactly match the resolved lock",
            ),
        )
    expected_enabled_edges = {
        edge
        for edge in expected_edges
        if components[edge[0]]["admission_status"] == "approved"
        and components[edge[1]]["admission_status"] == "approved"
    }
    actual_enabled_edges = {
        (cast(str, relationship["from"]), cast(str, relationship["to"]))
        for relationship in cast(list[Mapping[str, Any]], data["relationships"])
        if relationship["enabled"]
        and relationship["type"] == "depends-on"
        and relationship["from"] in locked
        and relationship["to"] in locked
    }
    if actual_enabled_edges != expected_enabled_edges:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH",
                document,
                "$.relationships",
                "locked Python dependency edges may be enabled only between admitted endpoints",
            ),
        )
    if root_package is None:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_METADATA_MISMATCH",
                document,
                "$.components",
                "lockfile must contain one root project record",
            ),
        )
    else:
        for issue in _root_lock_metadata_issues(root, root_package, document):
            _add_issue(issues, issue)
    return _sorted_issues(issues)


def _exact_requirement(requirement: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", requirement)
    if match is None:
        return None
    return _normalize_package_name(match.group(1)), match.group(2)


def _yaml_mapping_keys_unique(node: Node) -> bool:
    """Reject duplicate or non-scalar YAML keys before constructing mappings."""

    if isinstance(node, MappingNode):
        keys: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                return False
            key = (key_node.tag, key_node.value)
            if key in keys or key_node.value == "<<":
                return False
            keys.add(key)
            if not _yaml_mapping_keys_unique(value_node):
                return False
    elif isinstance(node, SequenceNode):
        return all(_yaml_mapping_keys_unique(value) for value in node.value)
    return True


def _root_lock_metadata_issues(
    root: Path, root_package: Mapping[str, Any], document: str
) -> list[DependencyIssue]:
    """Reconcile project declarations with uv's root-package lock metadata."""

    try:
        configuration = _load_toml(root / "pyproject.toml")
        project = cast(Mapping[str, Any], configuration["project"])
        groups = cast(Mapping[str, Any], configuration["dependency-groups"])
        python_parts = tuple(
            int(part)
            for part in (root / ".python-version").read_text(encoding="utf-8").strip().split(".")
        )
        if len(python_parts) != 2:
            raise ValueError
        expected_requires_python = (
            f">={python_parts[0]}.{python_parts[1]},<{python_parts[0]}.{python_parts[1] + 1}"
        )
    except OSError, UnicodeError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "root project declarations could not be read safely",
            )
        ]

    def parse_exact_members(value: object) -> set[tuple[str, str]] | None:
        if not isinstance(value, list):
            return None
        parsed_members: set[tuple[str, str]] = set()
        for member in value:
            if not isinstance(member, str):
                return None
            parsed = _exact_requirement(member)
            if parsed is None:
                return None
            parsed_members.add(parsed)
        return parsed_members

    def expand_group(
        group_name: str, stack: frozenset[str] = frozenset()
    ) -> set[tuple[str, str]] | None:
        if group_name in stack:
            return None
        members = groups.get(group_name)
        if not isinstance(members, list):
            return None
        expanded: set[tuple[str, str]] = set()
        for member in members:
            if isinstance(member, str):
                parsed = _exact_requirement(member)
                if parsed is None:
                    return None
                expanded.add(parsed)
            elif (
                isinstance(member, Mapping)
                and set(member) == {"include-group"}
                and isinstance(member.get("include-group"), str)
            ):
                included = expand_group(cast(str, member["include-group"]), stack | {group_name})
                if included is None:
                    return None
                expanded.update(included)
            else:
                return None
        return expanded

    def lock_names(value: object) -> set[str] | None:
        if not isinstance(value, list):
            return None
        names: set[str] = set()
        for record in value:
            if not isinstance(record, Mapping) or not isinstance(record.get("name"), str):
                return None
            names.add(_normalize_package_name(cast(str, record["name"])))
        return names

    def lock_requirements(value: object) -> set[tuple[str, str]] | None:
        if not isinstance(value, list):
            return None
        requirements: set[tuple[str, str]] = set()
        for record in value:
            if (
                not isinstance(record, Mapping)
                or not isinstance(record.get("name"), str)
                or not isinstance(record.get("specifier"), str)
            ):
                return None
            parsed = _exact_requirement(
                f"{cast(str, record['name'])}{cast(str, record['specifier'])}"
            )
            if parsed is None:
                return None
            requirements.add(parsed)
        return requirements

    project_requirements = parse_exact_members(project.get("dependencies", []))
    optional_value = project.get("optional-dependencies", {})
    optional_requirements: dict[str, set[tuple[str, str]]] = {}
    if isinstance(optional_value, Mapping):
        for extra, members in optional_value.items():
            parsed = parse_exact_members(members)
            if not isinstance(extra, str) or parsed is None:
                optional_requirements = {}
                project_requirements = None
                break
            optional_requirements[extra] = parsed
    else:
        project_requirements = None

    expanded_groups: dict[str, set[tuple[str, str]]] = {}
    for group_name in groups:
        expanded = expand_group(group_name)
        if expanded is None:
            project_requirements = None
            break
        expanded_groups[group_name] = expanded

    root_metadata_value = root_package.get("metadata", {})
    root_metadata = (
        cast(Mapping[str, Any], root_metadata_value)
        if isinstance(root_metadata_value, Mapping)
        else {}
    )
    root_optional_value = root_package.get("optional-dependencies", {})
    root_optional = (
        cast(Mapping[str, Any], root_optional_value)
        if isinstance(root_optional_value, Mapping)
        else {}
    )
    root_dev_value = root_package.get("dev-dependencies", {})
    root_dev = (
        cast(Mapping[str, Any], root_dev_value) if isinstance(root_dev_value, Mapping) else {}
    )
    root_requires_dev_value = root_metadata.get("requires-dev", {})
    root_requires_dev = (
        cast(Mapping[str, Any], root_requires_dev_value)
        if isinstance(root_requires_dev_value, Mapping)
        else {}
    )
    root_extras_value = root_metadata.get("provides-extras", [])
    root_extras = (
        set(cast(list[str], root_extras_value))
        if isinstance(root_extras_value, list)
        and all(isinstance(extra, str) for extra in root_extras_value)
        else None
    )

    metadata_valid = (
        project_requirements is not None
        and root_package.get("name") == project.get("name")
        and root_package.get("version") == project.get("version")
        and project.get("requires-python") == expected_requires_python
        and root_package.get("source") == {"editable": "."}
        and lock_names(root_package.get("dependencies", []))
        == {name for name, _ in project_requirements}
        and set(root_optional) == set(optional_requirements)
        and all(
            lock_names(root_optional.get(extra)) == {name for name, _ in requirements}
            for extra, requirements in optional_requirements.items()
        )
        and lock_requirements(root_metadata.get("requires-dist", []))
        == project_requirements.union(
            *(requirements for requirements in optional_requirements.values())
        )
        and root_extras == set(optional_requirements)
        and set(root_dev) == set(expanded_groups)
        and set(root_requires_dev) == set(expanded_groups)
        and all(
            lock_names(root_dev.get(group_name)) == {name for name, _ in requirements}
            and lock_requirements(root_requires_dev.get(group_name)) == requirements
            for group_name, requirements in expanded_groups.items()
        )
    )
    if metadata_valid:
        return []
    return [
        _issue(
            "DEPENDENCY_LOCK_METADATA_MISMATCH",
            document,
            "$.components",
            "root lock metadata must exactly match project dependency declarations",
        )
    ]


def _declaration_reconciliation_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        configuration = _load_toml(root / "pyproject.toml")
        groups = cast(Mapping[str, object], configuration["dependency-groups"])
        build_system = cast(Mapping[str, Any], configuration["build-system"])
        project_configuration = cast(Mapping[str, Any], configuration["project"])
        uv_configuration = cast(
            Mapping[str, Any], cast(Mapping[str, Any], configuration["tool"])["uv"]
        )
    except OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "project declarations could not be read safely",
            )
        ]
    declarations: dict[str, str] = {}

    def record_requirement(member: object) -> None:
        if not isinstance(member, str):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                    document,
                    "$.components",
                    "direct Python declarations must use exact string versions",
                ),
            )
            return
        parsed = _exact_requirement(member)
        if parsed is None:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                    document,
                    "$.components",
                    "direct Python declarations must use exact versions",
                ),
            )
            return
        existing = declarations.get(parsed[0])
        if existing is not None and existing != parsed[1]:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                    document,
                    "$.components",
                    "duplicate declarations must select one identical version",
                ),
            )
        declarations[parsed[0]] = parsed[1]

    for members in groups.values():
        if not isinstance(members, list):
            record_requirement(members)
            continue
        for member in members:
            if isinstance(member, Mapping):
                if (
                    set(member) != {"include-group"}
                    or not isinstance(member.get("include-group"), str)
                    or member["include-group"] not in groups
                ):
                    record_requirement(member)
                continue
            record_requirement(member)
    project_dependencies = project_configuration.get("dependencies", [])
    if not isinstance(project_dependencies, list):
        record_requirement(project_dependencies)
    else:
        for member in project_dependencies:
            record_requirement(member)
    optional_dependencies = project_configuration.get("optional-dependencies", {})
    if not isinstance(optional_dependencies, Mapping):
        record_requirement(optional_dependencies)
    else:
        for members in optional_dependencies.values():
            if not isinstance(members, list):
                record_requirement(members)
                continue
            for member in members:
                record_requirement(member)

    if build_system.get("build-backend") != "uv_build" or "backend-path" in build_system:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_DECLARATION_MISMATCH",
                document,
                "$.components",
                "the build backend must remain the reviewed uv-build adapter",
            ),
        )
    build_requirements = build_system.get("requires")
    if not isinstance(build_requirements, list):
        record_requirement(build_requirements)
    else:
        for requirement in build_requirements:
            record_requirement(requirement)
    concrete_direct = {
        component_id
        for component_id, component in components.items()
        if component["ecosystem"] == "python-package"
        and component["relationship"] == "direct"
        and component["admission_status"] in {"approved", "evaluated"}
    }
    if set(declarations) != concrete_direct:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_DECLARATION_MISMATCH",
                document,
                "$.components",
                "approved and evaluated direct Python components must match project declarations",
            ),
        )
    for component_id, version in declarations.items():
        component = components.get(component_id)
        if (
            component is not None
            and cast(Mapping[str, Any], component["version"])["selected"] != version
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                    document,
                    "$.components[*].version.selected",
                    "selected direct dependency version must match the project declaration",
                ),
            )
    uv_requirement = cast(str, uv_configuration["required-version"])
    if uv_requirement != f"=={cast(Mapping[str, Any], components['uv']['version'])['selected']}":
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*].version.selected",
                "uv policy pin must match the project configuration",
            ),
        )
    return _sorted_issues(issues)


def _workflow_reconciliation_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        dev_tool = (root / "tools" / "dev.py").read_text(encoding="utf-8")
        python_minor = (root / ".python-version").read_text(encoding="utf-8").strip()
        workflow_node = yaml.compose(workflow, Loader=yaml.SafeLoader)
        if (
            not isinstance(workflow_node, MappingNode)
            or not _yaml_mapping_keys_unique(workflow_node)
            or any(not isinstance(key, ScalarNode) for key, _ in workflow_node.value)
        ):
            raise TypeError
        top_level_key_names = {cast(ScalarNode, key).value for key, _ in workflow_node.value}
        workflow_data = yaml.safe_load(workflow)
        if not isinstance(workflow_data, Mapping):
            raise TypeError
        dev_tree = ast.parse(dev_tool)
    except OSError, SyntaxError, TypeError, UnicodeError, yaml.YAMLError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "workflow pin inputs could not be read safely",
            )
        ]
    jobs = workflow_data.get("jobs")
    quality_job = (
        cast(Mapping[str, Any], jobs["windows-quality"])
        if isinstance(jobs, Mapping) and isinstance(jobs.get("windows-quality"), Mapping)
        else {}
    )
    quality_steps_value = quality_job.get("steps")
    quality_steps = (
        cast(list[Mapping[str, Any]], quality_steps_value)
        if isinstance(quality_steps_value, list)
        and all(isinstance(step, Mapping) for step in quality_steps_value)
        else []
    )
    workflow_shape_valid = (
        top_level_key_names == {"name", "on", "permissions", "concurrency", "jobs"}
        and set(workflow_data) == {"name", True, "permissions", "concurrency", "jobs"}
        and workflow_data.get("name") == "Local-equivalent quality gates"
        and workflow_data.get(True)
        == {"push": None, "pull_request": None, "workflow_dispatch": None}
        and workflow_data.get("permissions") == {"contents": "read"}
        and workflow_data.get("concurrency")
        == {
            "group": "quality-${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": True,
        }
        and isinstance(jobs, Mapping)
        and set(jobs) == {"windows-quality"}
        and set(quality_job) == {"name", "runs-on", "timeout-minutes", "steps"}
        and quality_job.get("runs-on") == "windows-2025"
        and quality_job.get("timeout-minutes") == 20
        and len(quality_steps) == 8
    )
    expected_command_steps = {
        "Bootstrap locked environment": "python tools/dev.py bootstrap",
        "Install renderer comparison dependencies": (
            "uv sync --locked --group renderer-evaluation"
        ),
        "Run local-equivalent quality suite": "python tools/dev.py check",
    }
    for step_name, command in expected_command_steps.items():
        matches = [step for step in quality_steps if step.get("name") == step_name]
        if matches != [{"name": step_name, "run": command}]:
            workflow_shape_valid = False
    if not workflow_shape_valid:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components",
                "quality workflow permissions, platform, timeout, and required steps must remain reviewed",
            ),
        )
    action_components = {
        "actions/checkout": "actions-checkout",
        "actions/setup-python": "actions-setup-python",
        "astral-sh/setup-uv": "astral-setup-uv",
    }
    workflow_mappings = [mapping for _, mapping in _walk_mappings(workflow_data)]
    action_steps = [
        mapping for mapping in workflow_mappings if isinstance(mapping.get("uses"), str)
    ]
    found_actions: list[str] = []
    uv_version: str | None = None
    uv_checksum: str | None = None
    for action_step in action_steps:
        uses_value = cast(str, action_step["uses"])
        action_match = re.fullmatch(r"([^@\s]+)@([0-9a-f]{40})", uses_value)
        if action_match is None:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_CONFIG_PIN_MISMATCH",
                    document,
                    "$.components[*]",
                    "workflow actions must use reviewed full-commit pins",
                ),
            )
            continue
        action_name, revision = action_match.groups()
        component_id = action_components.get(action_name)
        if component_id is None:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_CONFIG_PIN_MISMATCH",
                    document,
                    "$.components[*]",
                    "workflow contains an uninventoried action",
                ),
            )
            continue
        found_actions.append(component_id)
        component = components[component_id]
        if cast(Mapping[str, Any], component["integrity"])["revision"] != revision:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_CONFIG_PIN_MISMATCH",
                    document,
                    "$.components[*]",
                    "workflow action pin must match the dependency inventory",
                ),
            )
        action_with = action_step.get("with")
        action_shape_valid = set(action_step) == {"name", "uses", "with"}
        if action_name == "actions/checkout":
            action_shape_valid = action_shape_valid and action_with == {
                "fetch-depth": 0,
                "persist-credentials": False,
            }
        elif action_name == "actions/setup-python":
            action_shape_valid = action_shape_valid and action_with == {
                "python-version-file": ".python-version",
                "architecture": "x64",
                "check-latest": False,
            }
        elif action_name == "astral-sh/setup-uv":
            expected_uv_keys = {
                "version",
                "checksum",
                "enable-cache",
                "restore-cache",
                "save-cache",
                "download-from-astral-mirror",
            }
            action_shape_valid = (
                action_shape_valid
                and isinstance(action_with, Mapping)
                and set(action_with) == expected_uv_keys
                and all(
                    action_with.get(control) is False
                    for control in (
                        "enable-cache",
                        "restore-cache",
                        "save-cache",
                        "download-from-astral-mirror",
                    )
                )
            )
            if isinstance(action_with, Mapping):
                uv_version_value = action_with.get("version")
                uv_checksum_value = action_with.get("checksum")
                uv_version = uv_version_value if isinstance(uv_version_value, str) else None
                uv_checksum = uv_checksum_value if isinstance(uv_checksum_value, str) else None
        if not action_shape_valid:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_CONFIG_PIN_MISMATCH",
                    document,
                    "$.components[*]",
                    "workflow action inputs and execution controls must match reviewed policy",
                ),
            )
    if sorted(found_actions) != sorted(action_components.values()):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components",
                "required workflow action pins are incomplete",
            ),
        )
    developer_constants: dict[str, object] = {}
    constant_write_counts = {"GITLEAKS_VERSION": 0, "MINIMUM_GIT_VERSION": 0}
    for node in ast.walk(dev_tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id in constant_write_counts
        ):
            constant_write_counts[node.id] += 1
    for statement in dev_tree.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            try:
                developer_constants[statement.target.id] = ast.literal_eval(statement.value)
            except ValueError, TypeError:
                continue
    if any(count != 1 for count in constant_write_counts.values()):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components",
                "developer tool version constants must have one immutable literal assignment",
            ),
        )

    uv_component = components["uv"]
    uv_selected = cast(str, cast(Mapping[str, Any], uv_component["version"])["selected"])
    uv_hashes = cast(
        list[Mapping[str, Any]], cast(Mapping[str, Any], uv_component["integrity"])["hashes"]
    )
    uv_hash_pairs = {(cast(str, item["value"]), cast(str, item["artifact"])) for item in uv_hashes}
    if (
        uv_version != uv_selected
        or uv_checksum is None
        or uv_hash_pairs != {(uv_checksum, EXPECTED_TOOL_ARTIFACTS["uv"])}
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "workflow uv version and checksum must match the dependency inventory",
            ),
        )

    gitleaks_component = components["gitleaks"]
    gitleaks_selected = cast(
        str, cast(Mapping[str, Any], gitleaks_component["version"])["selected"]
    )
    gitleaks_hashes = cast(
        list[Mapping[str, Any]], cast(Mapping[str, Any], gitleaks_component["integrity"])["hashes"]
    )
    gitleaks_hash_pairs = {
        (cast(str, item["value"]), cast(str, item["artifact"])) for item in gitleaks_hashes
    }
    expected_gitleaks_uri = (
        "https://github.com/gitleaks/gitleaks/releases/download/"
        f"v{gitleaks_selected}/{EXPECTED_TOOL_ARTIFACTS['gitleaks']}"
    )
    inventory_gitleaks_hash = (
        next(iter(gitleaks_hash_pairs))[0]
        if len(gitleaks_hash_pairs) == 1
        and next(iter(gitleaks_hash_pairs))[1] == EXPECTED_TOOL_ARTIFACTS["gitleaks"]
        else None
    )
    gitleaks_download_steps = [
        mapping
        for mapping in quality_steps
        if mapping.get("name") == f"Install and verify Gitleaks {gitleaks_selected}"
        and isinstance(mapping.get("run"), str)
        and "Invoke-WebRequest" in mapping["run"]
    ]
    download_scripts = [cast(str, step["run"]) for step in gitleaks_download_steps]
    gitleaks_uri: str | None = None
    gitleaks_expected_hash: str | None = None
    gitleaks_step_valid = False
    if len(download_scripts) == 1:
        uri_matches = re.findall(
            r'^\s*\$uri\s*=\s*"([^"]+)"\s*$', download_scripts[0], re.MULTILINE
        )
        hash_matches = re.findall(
            r'^\s*\$expected\s*=\s*"([0-9a-f]{64})"\s*$',
            download_scripts[0],
            re.MULTILINE,
        )
        if len(uri_matches) == 1:
            gitleaks_uri = uri_matches[0]
        if len(hash_matches) == 1:
            gitleaks_expected_hash = hash_matches[0]
        script = download_scripts[0]
        expected_script_lines = (
            [
                '$ErrorActionPreference = "Stop"',
                f'$archive = Join-Path $env:RUNNER_TEMP "gitleaks-{gitleaks_selected}.zip"',
                f'$destination = Join-Path $env:RUNNER_TEMP "gitleaks-{gitleaks_selected}"',
                f'$uri = "{expected_gitleaks_uri}"',
                f'$expected = "{inventory_gitleaks_hash}"',
                "Invoke-WebRequest -Uri $uri -OutFile $archive",
                "$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath "
                "$archive).Hash.ToLowerInvariant()",
                "if ($actual -ne $expected) {",
                '  throw "Gitleaks archive checksum mismatch."',
                "}",
                "Expand-Archive -LiteralPath $archive -DestinationPath $destination",
                "$destination | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append",
                '& (Join-Path $destination "gitleaks.exe") version',
            ]
            if inventory_gitleaks_hash is not None
            else []
        )
        gitleaks_step_valid = (
            set(gitleaks_download_steps[0]) == {"name", "shell", "run"}
            and gitleaks_download_steps[0].get("name")
            == f"Install and verify Gitleaks {gitleaks_selected}"
            and gitleaks_download_steps[0].get("shell") == "pwsh"
            and script.splitlines() == expected_script_lines
            and script.count("Invoke-WebRequest") == 1
            and re.search(
                r"^\s*Invoke-WebRequest\s+-Uri\s+\$uri\s+-OutFile\s+\$archive\s*$",
                script,
                re.MULTILINE,
            )
            is not None
            and re.search(
                r"^\s*\$actual\s*=\s*\(Get-FileHash\s+-Algorithm\s+SHA256\s+"
                r"-LiteralPath\s+\$archive\)\.Hash\.ToLowerInvariant\(\)\s*$",
                script,
                re.MULTILINE,
            )
            is not None
            and re.search(
                r"^\s*if\s*\(\$actual\s+-ne\s+\$expected\)\s*\{\s*$",
                script,
                re.MULTILINE,
            )
            is not None
        )
    if (
        developer_constants.get("GITLEAKS_VERSION") != gitleaks_selected
        or not gitleaks_step_valid
        or gitleaks_uri != expected_gitleaks_uri
        or gitleaks_expected_hash is None
        or gitleaks_hash_pairs != {(gitleaks_expected_hash, EXPECTED_TOOL_ARTIFACTS["gitleaks"])}
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "developer Gitleaks pin must match the dependency inventory",
            ),
        )

    qpdf_component = components["qpdf"]
    qpdf_selected = cast(str, cast(Mapping[str, Any], qpdf_component["version"])["selected"])
    qpdf_evidence = cast(Mapping[str, Any], qpdf_component["artifact_evidence"])
    qpdf_artifact = cast(str, qpdf_evidence["artifact"])
    qpdf_uri = cast(str, qpdf_evidence["url"])
    qpdf_hash = cast(str, qpdf_evidence["sha256"])
    qpdf_download_steps = [
        mapping
        for mapping in quality_steps
        if mapping.get("name") == f"Install and verify qpdf {qpdf_selected}"
        and isinstance(mapping.get("run"), str)
        and "Invoke-WebRequest" in mapping["run"]
    ]
    qpdf_step_valid = False
    if len(qpdf_download_steps) == 1:
        qpdf_script = cast(str, qpdf_download_steps[0]["run"])
        expected_qpdf_script_lines = [
            '$ErrorActionPreference = "Stop"',
            f'$archive = Join-Path $env:RUNNER_TEMP "{qpdf_artifact}"',
            (
                "$destination = Join-Path $env:ProgramFiles "
                f'"PaperWorkShell-ci-qpdf-{qpdf_selected}"'
            ),
            f'$uri = "{qpdf_uri}"',
            f'$expected = "{qpdf_hash}"',
            "Invoke-WebRequest -Uri $uri -OutFile $archive",
            "$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath "
            "$archive).Hash.ToLowerInvariant()",
            "if ($actual -ne $expected) {",
            '  throw "qpdf archive checksum mismatch."',
            "}",
            "Expand-Archive -LiteralPath $archive -DestinationPath $destination",
            f'$qpdf = Join-Path $destination "qpdf-{qpdf_selected}-msvc64/bin/qpdf.exe"',
            "if (-not (Test-Path -LiteralPath $qpdf -PathType Leaf)) {",
            '  throw "qpdf executable was not found in the reviewed archive layout."',
            "}",
            "$version = (& $qpdf --version)[0]",
            f'if ($version -ne "qpdf version {qpdf_selected}") {{',
            '  throw "qpdf version mismatch."',
            "}",
            '"PWS_QPDF_EXECUTABLE=$qpdf" | Out-File -FilePath '
            "$env:GITHUB_ENV -Encoding utf8 -Append",
        ]
        qpdf_step_valid = (
            set(qpdf_download_steps[0]) == {"name", "shell", "run"}
            and qpdf_download_steps[0].get("shell") == "pwsh"
            and qpdf_script.splitlines() == expected_qpdf_script_lines
            and qpdf_script.count("Invoke-WebRequest") == 1
            and "$env:GITHUB_PATH" not in qpdf_script
            and qpdf_script.count("PWS_QPDF_EXECUTABLE") == 1
        )
    if not qpdf_step_valid:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "CI qpdf artifact, checksum, fixed executable path, and version must match the dependency inventory",
            ),
        )
    git_constraint = cast(str, cast(Mapping[str, Any], components["git"]["version"])["constraint"])
    minimum_git = developer_constants.get("MINIMUM_GIT_VERSION")
    expected_git = (
        f">={minimum_git[0]}.{minimum_git[1]}"
        if isinstance(minimum_git, tuple)
        and len(minimum_git) == 2
        and all(isinstance(part, int) for part in minimum_git)
        else None
    )
    if expected_git is None or git_constraint != expected_git:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "developer Git minimum must match the dependency inventory",
            ),
        )
    if python_minor != cast(Mapping[str, Any], components["cpython"]["version"])["selected"]:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "Python minor policy must match the dependency inventory",
            ),
        )
    return _sorted_issues(issues)


def _repository_semantic_issues(
    root: Path,
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    policy = cast(Mapping[str, Any], data["policy"])
    policy_path = root / cast(str, policy["document"])
    if not policy_path.is_file():
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_POLICY_DOCUMENT_MISSING",
                document,
                "$.policy.document",
                "dependency policy document is missing",
            ),
        )
    for collection in (
        _authority_reference_issues(root, components, document),
        _lock_reconciliation_issues(root, data, components, document),
        _declaration_reconciliation_issues(root, components, document),
        _workflow_reconciliation_issues(root, components, document),
    ):
        for issue in collection:
            _add_issue(issues, issue)

    relationships = cast(list[Mapping[str, Any]], data["relationships"])
    ghostscript_edge = next(
        (
            relationship
            for relationship in relationships
            if relationship["from"] == "ocrmypdf" and relationship["to"] == "ghostscript"
        ),
        None,
    )
    qt_pdf_edges = {
        cast(str, relationship["from"]): relationship
        for relationship in relationships
        if relationship["from"] in {"pyside6", "pyside6-addons"}
        and relationship["type"] == "contains"
        and relationship["to"] == "qt-pdf"
    }
    conflated_pdfium_edge = any(
        relationship["from"] == "qt-pdf"
        and relationship["type"] == "contains"
        and relationship["to"] == "pdfium"
        for relationship in relationships
    )
    if ghostscript_edge is None or ghostscript_edge["enabled"]:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_OPTIONAL_TOOL_POLICY_INVALID",
                document,
                "$.relationships",
                "ambient Ghostscript discovery must remain disabled",
            ),
        )
    if (
        set(qt_pdf_edges) != {"pyside6", "pyside6-addons"}
        or any(edge["enabled"] for edge in qt_pdf_edges.values())
        or conflated_pdfium_edge
        or components.get("pyside6", {}).get("admission_status") == "approved"
        or components.get("pyside6-addons", {}).get("admission_status") == "approved"
        or components.get("qt-pdf", {}).get("admission_status") == "approved"
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_PYSIDE_VARIANT_POLICY_INVALID",
                document,
                "$.relationships",
                "PySide6 meta-package, Addons, and Qt PDF product containment must remain disabled without conflating PDFium builds",
            ),
        )
    return _sorted_issues(issues)


def validate_dependency_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    document: str = "dependency-inventory.json",
    root: Path = PROJECT_ROOT,
    repository_checks: bool = False,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Validate one already-loaded dependency inventory without mutating it."""

    version = data.get("schema_version")
    if type(version) is not int:
        return None, [
            _issue(
                "DEPENDENCY_VERSION_INVALID",
                document,
                "$.schema_version",
                "schema_version must be an integer",
            )
        ]
    if version != SUPPORTED_SCHEMA_VERSION:
        return None, [
            _issue(
                "DEPENDENCY_VERSION_UNSUPPORTED",
                document,
                "$.schema_version",
                "schema_version is not supported",
            )
        ]
    schema_issues = _schema_validation_issues(data, schema, document)
    if schema_issues:
        return None, schema_issues
    try:
        components, component_issues = _component_semantic_issues(data, document)
        relationship_issues = _relationship_semantic_issues(data, components, document)
        issues = [*component_issues, *relationship_issues, *_text_safety_issues(data, document)]
        if cast(str, data["inventory_profile"]) != "policy-baseline":
            issues.append(
                _issue(
                    "DEPENDENCY_SBOM_INPUT_INVALID",
                    document,
                    "$.inventory_profile",
                    "schema v1 accepts only a non-release policy baseline",
                )
            )
        if date.fromisoformat(cast(str, data["reviewed_on"])) > date.today():
            issues.append(
                _issue(
                    "DEPENDENCY_REVIEW_DATE_ORDER_INVALID",
                    document,
                    "$.reviewed_on",
                    "inventory review date cannot be in the future",
                )
            )
        if repository_checks:
            issues.extend(_repository_semantic_issues(root, data, components, document))
    except Exception:
        return None, [
            _issue(
                "DEPENDENCY_SEMANTIC_EXECUTION_INVALID",
                document,
                "$",
                "semantic validation failed safely",
            )
        ]
    sorted_issues = _globally_bounded_issues(issues, document)
    return (None, sorted_issues) if sorted_issues else (dict(data), [])


def validate_dependency_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
    repository_checks: bool = False,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Load and validate one dependency inventory document."""

    resolved_path = path if path.is_absolute() else root / path
    data, data_issues = _load_json_object(
        resolved_path,
        root=root,
        maximum_bytes=MAX_INVENTORY_BYTES,
    )
    if data is None:
        return None, data_issues
    loaded_schema = schema
    if loaded_schema is None:
        loaded_schema, schema_issues = load_dependency_schema(root)
        if loaded_schema is None:
            return None, schema_issues
    return validate_dependency_data(
        data,
        loaded_schema,
        document=_document_label(resolved_path, root),
        root=root,
        repository_checks=repository_checks,
    )


def _fixture_paths(
    directory: Path,
    *,
    root: Path,
) -> tuple[list[Path] | None, list[DependencyIssue]]:
    document = _document_label(directory, root)
    try:
        resolved_directory = directory.resolve(strict=True)
        candidates: list[Path] = []
        for candidate in directory.iterdir():
            if not candidate.is_file() or not candidate.match("*.json"):
                continue
            if (
                candidate.is_symlink()
                or candidate.resolve(strict=True).parent != resolved_directory
            ):
                return None, [
                    _issue(
                        "DEPENDENCY_FIXTURE_PATH_UNSAFE",
                        document,
                        "$",
                        "fixture paths must remain inside the fixture directory",
                    )
                ]
            candidates.append(candidate)
            if len(candidates) > MAX_FIXTURE_FILES:
                return None, [
                    _issue(
                        "DEPENDENCY_FIXTURE_LIMIT",
                        document,
                        "$",
                        "fixture set exceeds its file-count limit",
                    )
                ]
    except OSError as error:
        return None, [
            _issue(
                "DEPENDENCY_FIXTURE_SET_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    return sorted(candidates, key=lambda candidate: candidate.name), []


def validate_dependency_fixture_set(
    root: Path = PROJECT_ROOT,
) -> tuple[DependencyFixtureSummary | None, list[DependencyIssue]]:
    """Validate the canonical baseline and prove each rejected example fails as intended."""

    schema, schema_issues = load_dependency_schema(root)
    if schema is None:
        return None, schema_issues
    _, positive_issues = validate_dependency_document(
        INVENTORY_PATH,
        root,
        schema=schema,
        repository_checks=True,
    )
    invalid_paths, path_issues = _fixture_paths(root / INVALID_FIXTURE_DIRECTORY, root=root)
    if invalid_paths is None:
        return None, path_issues
    issues: list[DependencyIssue] = []
    for positive_issue in positive_issues:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_POSITIVE_FIXTURE_INVALID",
                positive_issue.document,
                positive_issue.path,
                "canonical dependency policy baseline is invalid",
            ),
        )
    if not invalid_paths:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_NEGATIVE_FIXTURES_MISSING",
                INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "at least one rejected dependency example is required",
            ),
        )
    actual_names = {path.name for path in invalid_paths}
    if actual_names != set(EXPECTED_NEGATIVE_CODES):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_NEGATIVE_FIXTURE_SET_MISMATCH",
                INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "rejected dependency example set does not match the reviewed contract",
            ),
        )
    for path in invalid_paths:
        data, fixture_issues = validate_dependency_document(path, root, schema=schema)
        if data is not None or not fixture_issues:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_NEGATIVE_FIXTURE_ACCEPTED",
                    _document_label(path, root),
                    "$",
                    "rejected dependency example was accepted",
                ),
            )
            continue
        expected_code = EXPECTED_NEGATIVE_CODES.get(path.name)
        if expected_code is not None and expected_code not in {
            issue.code for issue in fixture_issues
        }:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_NEGATIVE_FIXTURE_REASON_MISMATCH",
                    _document_label(path, root),
                    "$",
                    "rejected dependency example failed for an unexpected reason",
                ),
            )
    if issues:
        return None, _sorted_issues(issues)
    return DependencyFixtureSummary(positive_count=1, negative_count=len(invalid_paths)), []


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic diagnostics for schema-registry integration."""

    _, issues = validate_dependency_fixture_set(root)
    return [issue.render() for issue in issues]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the dependency policy baseline or one supplied document."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    parser.add_argument("document", nargs="?", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.document is None:
        summary, issues = validate_dependency_fixture_set(arguments.root)
    else:
        _, issues = validate_dependency_document(arguments.document, arguments.root)
        summary = None
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if summary is None:
        print("OK: validated dependency inventory v1 document.")
    else:
        print(
            "OK: validated dependency inventory v1 with "
            f"{summary.positive_count} complete and {summary.negative_count} rejected examples."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
