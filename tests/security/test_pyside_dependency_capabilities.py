from __future__ import annotations

import hashlib
import importlib
import unittest
from importlib.metadata import PackageNotFoundError, PackagePath, distribution, version
from pathlib import Path
from typing import cast


def _record_sha256(package: str) -> str:
    metadata = distribution(package)
    records = [
        file
        for file in metadata.files or ()
        if isinstance(file, PackagePath) and file.as_posix().endswith(".dist-info/RECORD")
    ]
    if len(records) != 1:
        raise AssertionError("expected exactly one installed distribution RECORD")
    record = cast(Path, metadata.locate_file(records[0]))
    return hashlib.sha256(record.read_bytes()).hexdigest()


class PySideDependencyCapabilityTests(unittest.TestCase):
    def test_selected_ui_runtime_identity_is_unchanged(self) -> None:
        self.assertEqual("6.11.1", version("PySide6-Essentials"))
        self.assertEqual("6.11.1", version("shiboken6"))

        essentials = distribution("PySide6-Essentials")
        shiboken = distribution("shiboken6")
        self.assertEqual(["shiboken6==6.11.1"], essentials.requires)
        self.assertEqual("<3.15,>=3.10", essentials.metadata["Requires-Python"])
        self.assertEqual(
            "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
            essentials.metadata["License"],
        )
        self.assertIsNone(shiboken.requires)
        self.assertEqual("<3.15,>=3.10", shiboken.metadata["Requires-Python"])
        self.assertEqual(
            "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
            shiboken.metadata["License"],
        )
        self.assertEqual(
            "859898b05309b1ec139e12377c9ad0490260e10cbb7f18138002c3844ae3cb58",
            _record_sha256("PySide6-Essentials"),
        )
        self.assertEqual(
            "c4a6c7643da99ac93e8f5eb9a32ea90863993f87e76fe136cc6b11045b67badb",
            _record_sha256("shiboken6"),
        )

        with self.assertRaises(PackageNotFoundError):
            distribution("PySide6")

    def test_essentials_owns_widget_bindings_but_not_addons_bindings(self) -> None:
        essentials = distribution("PySide6-Essentials")
        native_bindings = {
            Path(file.as_posix()).name
            for file in essentials.files or ()
            if isinstance(file, PackagePath) and file.as_posix().casefold().endswith(".pyd")
        }

        self.assertTrue({"QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd"} <= native_bindings)
        self.assertTrue(
            {
                "QtPdf.pyd",
                "QtPdfWidgets.pyd",
                "QtWebEngineCore.pyd",
                "QtWebEngineQuick.pyd",
                "QtWebEngineWidgets.pyd",
            }.isdisjoint(native_bindings)
        )

    def test_selected_ui_modules_import_with_matching_qt_runtime(self) -> None:
        qt_core = importlib.import_module("PySide6.QtCore")
        qt_gui = importlib.import_module("PySide6.QtGui")
        qt_widgets = importlib.import_module("PySide6.QtWidgets")

        self.assertEqual("6.11.1", qt_core.__version__)
        self.assertEqual("6.11.1", qt_core.qVersion())
        self.assertEqual("PySide6.QtGui", qt_gui.QImage.__module__)
        self.assertEqual("PySide6.QtWidgets", qt_widgets.QApplication.__module__)


if __name__ == "__main__":
    unittest.main()
