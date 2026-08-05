from __future__ import annotations

import hashlib
import importlib
import struct
import unittest
from importlib.metadata import PackagePath, distribution, version
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

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


class _PdfiumVersionInfo(Protocol):
    major: int
    minor: int
    build: int
    patch: int
    origin: str
    flags: tuple[str, ...]


def _version_info(module: ModuleType, name: str) -> _PdfiumVersionInfo:
    return cast(_PdfiumVersionInfo, getattr(module, name))


def _pe_native_closure(path: Path) -> tuple[int, frozenset[str], int]:
    maximum_bytes = 16 * 1024 * 1024
    size = path.stat().st_size
    if size <= 0 or size > maximum_bytes:
        raise AssertionError("dependency PE file exceeds the reviewed inspection bound")
    payload = path.read_bytes()
    if payload[:2] != b"MZ" or len(payload) < 0x40:
        raise AssertionError("dependency PE file has an invalid DOS header")
    pe_offset = struct.unpack_from("<I", payload, 0x3C)[0]
    if pe_offset > len(payload) - 24 or payload[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise AssertionError("dependency PE file has an invalid NT header")

    coff_offset = pe_offset + 4
    machine, section_count = struct.unpack_from("<HH", payload, coff_offset)
    optional_size = struct.unpack_from("<H", payload, coff_offset + 16)[0]
    optional_offset = coff_offset + 20
    if optional_offset > len(payload) - optional_size:
        raise AssertionError("dependency PE optional header is truncated")
    magic = struct.unpack_from("<H", payload, optional_offset)[0]
    if magic == 0x20B:
        data_directories_offset = optional_offset + 112
        image_base = struct.unpack_from("<Q", payload, optional_offset + 24)[0]
    elif magic == 0x10B:
        data_directories_offset = optional_offset + 96
        image_base = struct.unpack_from("<I", payload, optional_offset + 28)[0]
    else:
        raise AssertionError("dependency PE optional header uses an unsupported format")
    if data_directories_offset > optional_offset + optional_size - (16 * 8):
        raise AssertionError("dependency PE data-directory table is truncated")

    sections_offset = optional_offset + optional_size
    if (
        section_count <= 0
        or section_count > 96
        or sections_offset > len(payload) - section_count * 40
    ):
        raise AssertionError("dependency PE section table is outside the inspection bound")
    sections: list[tuple[int, int, int]] = []
    for index in range(section_count):
        offset = sections_offset + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", payload, offset + 8
        )
        if raw_offset > len(payload) or raw_size > len(payload) - raw_offset:
            raise AssertionError("dependency PE section bytes are outside the file")
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def rva_offset(rva: int) -> int:
        for virtual_address, mapped_size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + mapped_size:
                result = raw_offset + (rva - virtual_address)
                if result >= len(payload):
                    break
                return result
        raise AssertionError("dependency PE import address is outside mapped sections")

    def ascii_name(rva: int) -> str:
        offset = rva_offset(rva)
        end = payload.find(b"\0", offset, min(len(payload), offset + 260))
        if end < 0:
            raise AssertionError("dependency PE import name is not bounded")
        try:
            return payload[offset:end].decode("ascii")
        except UnicodeDecodeError as error:
            raise AssertionError("dependency PE import name is not ASCII") from error

    imports: set[str] = set()
    import_rva, import_size = struct.unpack_from("<II", payload, data_directories_offset + 8)
    if import_rva != 0:
        descriptor_offset = rva_offset(import_rva)
        maximum_descriptors = min(256, import_size // 20 + 1)
        for _ in range(maximum_descriptors):
            if descriptor_offset > len(payload) - 20:
                raise AssertionError("dependency PE import descriptors are truncated")
            descriptor = payload[descriptor_offset : descriptor_offset + 20]
            if not any(descriptor):
                break
            imports.add(ascii_name(struct.unpack_from("<I", descriptor, 12)[0]))
            descriptor_offset += 20
        else:
            raise AssertionError("dependency PE import descriptors exceed the inspection bound")

    delay_rva, delay_size = struct.unpack_from("<II", payload, data_directories_offset + 13 * 8)
    if delay_rva != 0:
        descriptor_offset = rva_offset(delay_rva)
        maximum_descriptors = min(256, delay_size // 32 + 1)
        for _ in range(maximum_descriptors):
            if descriptor_offset > len(payload) - 32:
                raise AssertionError("dependency PE delay imports are truncated")
            descriptor = payload[descriptor_offset : descriptor_offset + 32]
            if not any(descriptor):
                break
            attributes, name_address = struct.unpack_from("<II", descriptor)
            name_rva = name_address if attributes & 1 else name_address - image_base
            imports.add(ascii_name(name_rva))
            descriptor_offset += 32
        else:
            raise AssertionError("dependency PE delay imports exceed the inspection bound")

    certificate_size = struct.unpack_from("<I", payload, data_directories_offset + 4 * 8 + 4)[0]
    return machine, frozenset(imports), certificate_size


class PdfDependencyCapabilityTests(unittest.TestCase):
    def test_selected_pypdfium2_build_identity_and_features_are_unchanged(self) -> None:
        self.assertEqual("5.12.1", version("pypdfium2"))
        metadata = distribution("pypdfium2")
        self.assertIsNone(metadata.requires)

        version_module = importlib.import_module("pypdfium2.version")
        binding_info = _version_info(version_module, "PYPDFIUM_INFO")
        engine_info = _version_info(version_module, "PDFIUM_INFO")
        self.assertEqual((5, 12, 1), (binding_info.major, binding_info.minor, binding_info.patch))
        self.assertEqual(
            (152, 0, 7947, 0),
            (engine_info.major, engine_info.minor, engine_info.build, engine_info.patch),
        )
        self.assertEqual("pdfium-binaries", engine_info.origin)
        self.assertEqual((), engine_info.flags)

        installed_paths = {file.as_posix() for file in metadata.files or ()}
        executable_or_native_paths = {
            path for path in installed_paths if path.casefold().endswith((".dll", ".exe", ".pyd"))
        }
        self.assertEqual(
            {"../../Scripts/pypdfium2.exe", "pypdfium2_raw/pdfium.dll"},
            executable_or_native_paths,
        )

    def test_selected_pdfium_windows_x64_native_closure_is_unchanged(self) -> None:
        pdfium_dll = _installed_file("pypdfium2", "pypdfium2_raw/pdfium.dll")
        self.assertEqual(7_217_664, pdfium_dll.stat().st_size)
        self.assertEqual(
            "cc2058add54b9da299a4496d526b79c375c6f0eb3a9000552a45c60f83765d55",
            _sha256(pdfium_dll),
        )

        machine, imports, certificate_size = _pe_native_closure(pdfium_dll)
        self.assertEqual(0x8664, machine)
        self.assertEqual(
            frozenset({"ADVAPI32.dll", "GDI32.dll", "KERNEL32.dll", "USER32.dll"}),
            imports,
        )
        self.assertEqual(0, certificate_size)

    def test_selected_pypdfium2_license_bundle_is_unchanged(self) -> None:
        expected_hashes = {
            "LICENSES/Apache-2.0.txt": (
                "3ddf9be5c28fe27dad143a5dc76eea25222ad1dd68934a047064e56ed2fa40c5"
            ),
            "LICENSES/BSD-3-Clause.txt": (
                "ad9a9e823df025f42389c1812eae28019f657d1ed7b3a4ebfd5010b0736a0da4"
            ),
            "LICENSES/CC-BY-4.0.txt": (
                "f1b1748301cd4274f46733c218d510929661b4990855de5fc645dacd69c4371c"
            ),
            "data/windows_x64/BUILD_LICENSES/abseil.txt": (
                "f54fff0b905df5b3464527c652a30e903b172d6dcab4d89b5e6f105d5e4a4603"
            ),
            "data/windows_x64/BUILD_LICENSES/agg23.txt": (
                "c110d3ea2ad77467ce0dcff7d3337e6c8be8049a5103f4b9bd5fd911a77972e5"
            ),
            "data/windows_x64/BUILD_LICENSES/fast_float.txt": (
                "bf1b57355feca8fce77ee95f48002f8d4789fb71b30ec7599c06cda4901fbb2b"
            ),
            "data/windows_x64/BUILD_LICENSES/freetype.txt": (
                "f4b133e25df1f86ad3ffea453aa0e613f0474f34778dbbb3e437e7b2724937d8"
            ),
            "data/windows_x64/BUILD_LICENSES/icu.txt": (
                "93679f4389d53b6835d89843f251844fb9bc455b35bab036d3c8e7abe497a47a"
            ),
            "data/windows_x64/BUILD_LICENSES/lcms.txt": (
                "7312b68c5b25e9bf2b828706fb4e29588f22705112f411fd42e1f7d84c3d139a"
            ),
            "data/windows_x64/BUILD_LICENSES/libjpeg_turbo.ijg": (
                "db16a04128171879c60708d171b88d97345a2dd20f9bfc173680a4497c73f704"
            ),
            "data/windows_x64/BUILD_LICENSES/libjpeg_turbo.md": (
                "be2b2b5ab168bce87bc3e31f2a5c5adba4b7f6e9e51d618e958d1d46972ebd95"
            ),
            "data/windows_x64/BUILD_LICENSES/libopenjpeg.txt": (
                "c5ab0890a737c2dfa7ba675036554f6d17741d98629b0c2a145354d00617e6b2"
            ),
            "data/windows_x64/BUILD_LICENSES/libpng.txt": (
                "452390433ba0f88aa3e2b122c647741b72a0c117cd6ed7a329b49785aecb5511"
            ),
            "data/windows_x64/BUILD_LICENSES/libtiff.txt": (
                "92b72ba97e6c2749c2a94bc0ef646b47080217f1e772a482b33cf5a5f98a6506"
            ),
            "data/windows_x64/BUILD_LICENSES/llvm-libc.txt": (
                "3b6226c32e168c83b891d8d6f0d3c29c2116dc3ef93dc93c307b54f279ecf383"
            ),
            "data/windows_x64/BUILD_LICENSES/pdfium-binaries.txt": (
                "8854f4388f1ca13b3ad9baa42e95f5546b4c0b17109c159256d3eca7be39b09b"
            ),
            "data/windows_x64/BUILD_LICENSES/pdfium.txt": (
                "961eacd9633fff6d051db7208b755e9210e30efac7adec3e6a6d52798f0ccf0e"
            ),
            "data/windows_x64/BUILD_LICENSES/simdutf.txt": (
                "c172a0ba936ff31230febb5dad869e25cb7c1a07480c7a381be8cf011bb52719"
            ),
            "data/windows_x64/BUILD_LICENSES/zlib.txt": (
                "33fd641c9f3b0e0be64bc78fea9e94807674cdd70c48477599226cb8956565fe"
            ),
        }
        metadata = distribution("pypdfium2")
        license_files = set(metadata.metadata.get_all("License-File") or ())
        self.assertEqual(set(expected_hashes), license_files)

        prefix = "pypdfium2-5.12.1.dist-info/licenses/"
        installed_license_files = {
            file.as_posix().removeprefix(prefix)
            for file in metadata.files or ()
            if file.as_posix().startswith(prefix)
        }
        self.assertEqual(set(expected_hashes), installed_license_files)
        for relative_path, expected_hash in expected_hashes.items():
            with self.subTest(license_file=relative_path):
                installed = _installed_file("pypdfium2", f"{prefix}{relative_path}")
                self.assertEqual(expected_hash, _sha256(installed))

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
