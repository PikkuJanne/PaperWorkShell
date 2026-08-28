from __future__ import annotations

import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_AUTHORITY = PROJECT_ROOT / "packaging/tesseract-language-pack-catalog-v1.json"
WHEEL_CATALOG_PATH = "paperworkshell/worker/client/tesseract-language-pack-catalog-v1.json"
SDIST_CATALOG_SUFFIX = f"/src/{WHEEL_CATALOG_PATH}"
GENERIC_TEMPLATE_AUTHORITY = (
    PROJECT_ROOT / "templates/builtin/generic-portal-application-v1.1.0.json"
)
WHEEL_GENERIC_TEMPLATE_PATH = (
    "paperworkshell/templates/builtin/generic-portal-application-v1.1.0.json"
)
SDIST_GENERIC_TEMPLATE_SUFFIX = f"/src/{WHEEL_GENERIC_TEMPLATE_PATH}"


def test_offline_builds_include_exact_runtime_catalog_artifacts() -> None:
    expected_catalog = CATALOG_AUTHORITY.read_bytes()
    expected_template = GENERIC_TEMPLATE_AUTHORITY.read_bytes()
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_directory = Path(temporary_directory)
        completed = subprocess.run(
            (
                "uv",
                "build",
                "--offline",
                "--out-dir",
                str(output_directory),
            ),
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=120,
        )
        assert completed.returncode == 0, "offline package build failed"
        wheels = tuple(output_directory.glob("*.whl"))
        source_archives = tuple(output_directory.glob("*.tar.gz"))
        assert len(wheels) == 1
        assert len(source_archives) == 1

        with zipfile.ZipFile(wheels[0]) as wheel:
            assert wheel.read(WHEEL_CATALOG_PATH) == expected_catalog
            assert wheel.read(WHEEL_GENERIC_TEMPLATE_PATH) == expected_template

        with tarfile.open(source_archives[0], mode="r:gz") as source_archive:
            catalog_member = next(
                (
                    member
                    for member in source_archive.getmembers()
                    if member.isfile() and member.name.endswith(SDIST_CATALOG_SUFFIX)
                ),
                None,
            )
            assert catalog_member is not None
            stream = source_archive.extractfile(catalog_member)
            assert stream is not None
            assert stream.read() == expected_catalog

            template_member = next(
                (
                    member
                    for member in source_archive.getmembers()
                    if member.isfile() and member.name.endswith(SDIST_GENERIC_TEMPLATE_SUFFIX)
                ),
                None,
            )
            assert template_member is not None
            stream = source_archive.extractfile(template_member)
            assert stream is not None
            assert stream.read() == expected_template
