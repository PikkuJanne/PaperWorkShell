from __future__ import annotations

import hashlib
import unittest
from importlib.metadata import PackagePath, distribution, version
from pathlib import Path
from typing import cast

import pikepdf
from PIL import features


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_file(package: str, suffix: str) -> Path:
    metadata = distribution(package)
    matches = [
        file
        for file in metadata.files or ()
        if isinstance(file, PackagePath) and file.as_posix().endswith(suffix)
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one installed {package} file ending in {suffix!r}")
    return cast(Path, metadata.locate_file(matches[0]))


class PdfDependencyCapabilityTests(unittest.TestCase):
    def test_selected_pikepdf_windows_wheel_native_closure_is_unchanged(self) -> None:
        self.assertEqual("10.10.0", version("pikepdf"))
        self.assertEqual("12.3.2", pikepdf.__libqpdf_version__)

        expected_hashes = {
            "pikepdf/_core.pyd": "679bfc7de0af834859bbd190fba222d9357f7e42b87b46972a0dc04dc297706d",
            "pikepdf.libs/qpdf30-eca0c1670a8d0fd8a8bc678bfc22fc30.dll": (
                "635d5d3e63a38241d74096bb06ecf8b8f3e68db2a5c6e5e0310ec4de2dc8dc86"
            ),
            "pikepdf.libs/msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll": (
                "0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9"
            ),
        }
        for suffix, expected_hash in expected_hashes.items():
            with self.subTest(file=suffix):
                self.assertEqual(expected_hash, _sha256(_installed_file("pikepdf", suffix)))

    def test_selected_pillow_wheel_does_not_enable_optional_gpl_component(self) -> None:
        self.assertEqual("12.3.0", version("Pillow"))
        self.assertFalse(features.check_feature("libimagequant"))

    def test_known_lxml_redistribution_review_resources_are_unchanged(self) -> None:
        self.assertEqual("6.1.1", version("lxml"))
        expected_hashes = {
            "lxml/isoschematron/resources/xsl/RNG2Schtrn.xsl": (
                "832f84eb1476a77e522bdc1715386b68f24423d35da084cd1dde319aacbba316"
            ),
            "lxml/isoschematron/resources/xsl/XSD2Schtrn.xsl": (
                "430791ac820cfb315c820f7c19703609a59f21b8151345ca11e6127efbfaec0d"
            ),
        }
        for suffix, expected_hash in expected_hashes.items():
            with self.subTest(file=suffix):
                self.assertEqual(expected_hash, _sha256(_installed_file("lxml", suffix)))


if __name__ == "__main__":
    unittest.main()
