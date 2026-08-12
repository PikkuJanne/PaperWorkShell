from __future__ import annotations

import base64
import csv
import hashlib
import importlib
import json
import unittest
from importlib.metadata import PackageNotFoundError, PackagePath, distribution, version
from pathlib import Path, PurePosixPath
from typing import cast


def _wheel_payload_record_sha256(package: str) -> str:
    metadata = distribution(package)
    records = [
        file
        for file in metadata.files or ()
        if isinstance(file, PackagePath) and file.as_posix().endswith(".dist-info/RECORD")
    ]
    if len(records) != 1:
        raise AssertionError("expected exactly one installed distribution RECORD")
    record = cast(Path, metadata.locate_file(records[0]))
    record_parent = records[0].parent.as_posix()
    installer_owned = {
        f"{record_parent}/INSTALLER",
        f"{record_parent}/REQUESTED",
        f"{record_parent}/RECORD",
    }
    expected_entry_points = {
        f"../../Scripts/{entry_point.name}.exe" for entry_point in metadata.entry_points
    }
    observed_entry_points: set[str] = set()
    payload_rows: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()
    with record.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    for row in rows:
        if len(row) != 3:
            raise AssertionError("installed distribution RECORD row is invalid")
        relative, encoded_hash, encoded_size = row
        record_path = PurePosixPath(relative)
        generated_entry_point = relative in expected_entry_points
        if (
            relative in seen_paths
            or record_path.is_absolute()
            or (".." in record_path.parts and not generated_entry_point)
            or ":" in relative
            or "\\" in relative
        ):
            raise AssertionError("installed distribution RECORD path is invalid")
        seen_paths.add(relative)
        if relative == records[0].as_posix():
            if encoded_hash or encoded_size:
                raise AssertionError("installed distribution RECORD self-entry is invalid")
            continue
        if (
            not encoded_hash.startswith("sha256=")
            or not encoded_size.isascii()
            or not encoded_size.isdecimal()
            or encoded_size != str(int(encoded_size))
        ):
            raise AssertionError("installed distribution RECORD digest is invalid")
        encoded_digest = encoded_hash.removeprefix("sha256=")
        expected_digest = base64.b64decode(
            encoded_digest + "=" * (-len(encoded_digest) % 4),
            altchars=b"-_",
            validate=True,
        )
        if len(expected_digest) != hashlib.sha256().digest_size:
            raise AssertionError("installed distribution RECORD digest is invalid")
        installed = cast(Path, metadata.locate_file(PackagePath(relative)))
        installed_bytes = installed.read_bytes()
        if (
            len(installed_bytes) != int(encoded_size)
            or hashlib.sha256(installed_bytes).digest() != expected_digest
        ):
            raise AssertionError("installed distribution payload does not match RECORD")
        if relative not in installer_owned and not generated_entry_point:
            payload_rows.append((relative, encoded_hash, encoded_size))
        if generated_entry_point:
            observed_entry_points.add(relative)
    if observed_entry_points != expected_entry_points:
        raise AssertionError("installed distribution entry-point inventory is invalid")
    canonical_payload = json.dumps(
        sorted(payload_rows),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical_payload).hexdigest()


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
            "818cc61a69fcc6980bcb0f413881595b72fff7cc42749417159754ad38901ef2",
            _wheel_payload_record_sha256("PySide6-Essentials"),
        )
        self.assertEqual(
            "fd4a1d5371db4528c16f67f63f3e8c2ddc22c2742447e20e3ed79fc81b80819f",
            _wheel_payload_record_sha256("shiboken6"),
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
