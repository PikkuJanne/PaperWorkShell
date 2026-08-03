from __future__ import annotations

import io
import json
import re
import struct
import tempfile
import unicodedata
import unittest
import zlib
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from PIL import Image, ImageCms

from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    GOLDEN_DIRECTORY,
    LONG_PATH_COMPONENT,
    SYNTHETIC_MARKER,
    FixtureGenerationError,
    _synthetic_rgb_icc_profile,
    build_committed_fixtures,
    build_index,
    build_renderer_golden_index,
    build_renderer_golden_references,
    canonical_index_bytes,
    canonical_renderer_golden_index_bytes,
    generate_long_path_tree,
    generate_oversized_pdf,
    write_committed_corpus,
)


def _rgb_png_pixels(content: bytes) -> tuple[int, int, bytes]:
    cursor = 8
    width = 0
    height = 0
    payload = bytearray()
    while cursor < len(content):
        length = struct.unpack(">I", content[cursor : cursor + 4])[0]
        kind = content[cursor + 4 : cursor + 8]
        chunk = content[cursor + 8 : cursor + 8 + length]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk)
            if bit_depth != 8 or color_type != 2:
                raise AssertionError("analytical golden must be RGB8")
        elif kind == b"IDAT":
            payload.extend(chunk)
        cursor += 12 + length
    decoded = zlib.decompress(payload)
    stride = width * 3
    rows = []
    for row in range(height):
        start = row * (stride + 1)
        if decoded[start] != 0:
            raise AssertionError("analytical golden must use the no-op PNG filter")
        rows.append(decoded[start + 1 : start + 1 + stride])
    return width, height, b"".join(rows)


class SyntheticFixtureGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = build_committed_fixtures()
        self.by_id = {fixture.fixture_id: fixture for fixture in self.fixtures}

    def test_generation_is_byte_deterministic_and_has_no_external_provenance(self) -> None:
        second = build_committed_fixtures()

        self.assertEqual(self.fixtures, second)
        self.assertEqual(canonical_index_bytes(self.fixtures), canonical_index_bytes(second))
        self.assertEqual(41, len(self.fixtures))
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture.fixture_id):
                self.assertIn(SYNTHETIC_MARKER.encode("ascii"), fixture.content)
                self.assertLess(len(fixture.content), 1_048_576)

    def test_index_covers_every_reviewed_category_exactly_once(self) -> None:
        index = build_index(self.fixtures)
        coverage = cast(list[dict[str, Any]], index["coverage"])
        expected = {
            "annotation",
            "attachment",
            "blank",
            "born-digital",
            "cmyk",
            "crop",
            "duplicate",
            "handwriting-demonstration",
            "image-only",
            "incremental",
            "long-path",
            "low-resolution",
            "malformed",
            "mixed",
            "multilingual",
            "page-size",
            "protected",
            "rotated",
            "rtl",
            "script-marker",
            "signed-marker",
            "transparency",
            "unicode",
            "very-large",
        }

        self.assertEqual(expected, {entry["category"] for entry in coverage})
        self.assertEqual(len(expected), len(coverage))
        by_category = {entry["category"]: entry for entry in coverage}
        self.assertEqual("on-demand", by_category["long-path"]["status"])
        self.assertEqual("on-demand", by_category["very-large"]["status"])
        self.assertEqual("committed", by_category["protected"]["status"])
        self.assertEqual(["pdf-protected"], by_category["protected"]["fixture_ids"])
        self.assertEqual("committed", by_category["unicode"]["status"])
        self.assertEqual(["pdf-unicode-named"], by_category["unicode"]["fixture_ids"])
        self.assertEqual("committed", by_category["cmyk"]["status"])
        self.assertEqual(
            ["pdf-render-cmyk", "pdf-render-colorspaces"],
            by_category["cmyk"]["fixture_ids"],
        )
        self.assertEqual("PWS-0704", by_category["rtl"]["scheduled_task"])

    def test_classic_pdfs_have_correct_xref_offsets_and_stream_lengths(self) -> None:
        for fixture in self.fixtures:
            if fixture.media_type != "application/pdf" or fixture.fixture_id in {
                "pdf-incremental",
                "pdf-malformed",
            }:
                continue
            with self.subTest(fixture=fixture.fixture_id):
                content = fixture.content
                xref_offset = int(content.rsplit(b"startxref\n", maxsplit=1)[1].splitlines()[0])
                self.assertEqual(b"xref", content[xref_offset : xref_offset + 4])
                object_offsets = {
                    int(match.group(1)): match.start()
                    for match in re.finditer(rb"(?m)^([1-9][0-9]*) 0 obj$", content)
                }
                xref_lines = content[xref_offset:].splitlines()
                size = int(xref_lines[1].split()[1])
                self.assertEqual(size - 1, len(object_offsets))
                for object_number in range(1, size):
                    declared = int(xref_lines[2 + object_number].split()[0])
                    self.assertEqual(object_offsets[object_number], declared)
                for match in re.finditer(rb"/Length ([0-9]+)[^>]*>>\nstream\n", content):
                    stream_start = match.end()
                    stream_end = content.index(b"\nendstream", stream_start)
                    self.assertEqual(int(match.group(1)), stream_end - stream_start)

    def test_incremental_pdf_links_two_revisions(self) -> None:
        content = self.by_id["pdf-incremental"].content
        startxrefs = [int(value) for value in re.findall(rb"startxref\n([0-9]+)", content)]

        self.assertEqual(2, len(startxrefs))
        self.assertEqual(2, content.count(b"%%EOF"))
        self.assertEqual(b"xref", content[startxrefs[0] : startxrefs[0] + 4])
        self.assertEqual(b"xref", content[startxrefs[1] : startxrefs[1] + 4])
        self.assertIn(f"/Prev {startxrefs[0]}".encode("ascii"), content[startxrefs[1] :])

    def test_png_chunks_crc_dimensions_and_rows_are_valid(self) -> None:
        for fixture in self.fixtures:
            if fixture.media_type != "image/png":
                continue
            with self.subTest(fixture=fixture.fixture_id):
                content = fixture.content
                self.assertEqual(b"\x89PNG\r\n\x1a\n", content[:8])
                cursor = 8
                chunks: list[tuple[bytes, bytes]] = []
                while cursor < len(content):
                    length = struct.unpack(">I", content[cursor : cursor + 4])[0]
                    kind = content[cursor + 4 : cursor + 8]
                    payload = content[cursor + 8 : cursor + 8 + length]
                    checksum = struct.unpack(
                        ">I", content[cursor + 8 + length : cursor + 12 + length]
                    )[0]
                    self.assertEqual(zlib.crc32(kind + payload) & 0xFFFFFFFF, checksum)
                    chunks.append((kind, payload))
                    cursor += 12 + length
                self.assertEqual(len(content), cursor)
                self.assertEqual([b"IHDR", b"tEXt", b"IDAT", b"IEND"], [kind for kind, _ in chunks])
                width, height, bit_depth, color_type, _, _, _ = struct.unpack(
                    ">IIBBBBB", chunks[0][1]
                )
                channels = 4 if color_type == 6 else 3
                raw = zlib.decompress(chunks[2][1])
                self.assertEqual(height * (1 + width * channels), len(raw))
                self.assertEqual(8, bit_depth)
                for row in range(height):
                    self.assertEqual(0, raw[row * (1 + width * channels)])

    def test_feature_markers_are_narrow_and_duplicates_are_explicit(self) -> None:
        duplicate_a = self.by_id["pdf-duplicate-a"]
        duplicate_b = self.by_id["pdf-duplicate-b"]
        script = self.by_id["pdf-script-marker"].content
        action = self.by_id["pdf-action-marker"].content
        protected = self.by_id["pdf-protected"].content
        signed = self.by_id["pdf-signed-marker"].content
        unicode_named = self.by_id["pdf-unicode-named"]

        self.assertEqual(duplicate_a.content, duplicate_b.content)
        self.assertEqual(
            duplicate_a.expected["duplicate_group"], duplicate_b.expected["duplicate_group"]
        )
        self.assertIn(b"/S /JavaScript", script)
        self.assertNotIn(b"/OpenAction", script)
        self.assertNotIn(b"/AA", script)
        self.assertIn(b"/OpenAction", action)
        self.assertIn(b"/S /GoTo", action)
        self.assertNotIn(b"/JavaScript", action)
        self.assertIn(b"/Filter /Standard", protected)
        self.assertNotIn(b"password-required carrier", protected)
        self.assertIn(b"/FT /Sig", signed)
        self.assertNotIn(b"/ByteRange", signed)
        self.assertEqual("unicode-named-café.pdf", unicode_named.relative_path.name)
        self.assertTrue(unicodedata.is_normalized("NFC", unicode_named.relative_path.name))
        self.assertFalse(
            cast(bool, self.by_id["png-handwriting-demonstration"].expected["is_signature"])
        )

    def test_renderer_fixtures_are_font_free_and_exercise_closed_features(self) -> None:
        calibration = self.by_id["pdf-render-calibration"].content
        transparency = self.by_id["pdf-render-transparency"].content
        cmyk = self.by_id["pdf-render-cmyk"].content
        geometry = self.by_id["pdf-render-geometry-matrix"].content
        soft_mask = self.by_id["pdf-render-soft-mask-blend"].content
        colorspaces = self.by_id["pdf-render-colorspaces"].content
        annotation = self.by_id["pdf-render-annotation-appearance"].content
        embedded_font = self.by_id["pdf-render-embedded-type3-font"].content
        high_dimension = self.by_id["pdf-render-high-dimension"].content
        malformed = self.by_id["pdf-render-malformed-page-tree"].content

        self.assertNotIn(b"/Font", calibration)
        self.assertIn(b"/CropBox [10 10 70 50]", calibration)
        self.assertIn(b"/Rotate 90", calibration)
        self.assertIn(b"/ExtGState", transparency)
        self.assertIn(b"/ca 0.5", transparency)
        self.assertIn(b"/ColorSpace /DeviceCMYK", cmyk)
        self.assertIn(b"/Interpolate false", cmyk)
        self.assertTrue(geometry.startswith(b"%PDF-1.6\n"))
        self.assertIn(b"/MediaBox [0 0 43 37] /CropBox [3 5 33 25]", geometry)
        self.assertEqual(1, geometry.count(b"/MediaBox [0 0 43 37]"))
        self.assertEqual((3, 5, 10, 12), (3, 5, 43 - 33, 37 - 25))
        for rotation in (0, 90, 180, 270):
            self.assertIn(f"/Rotate {rotation}".encode("ascii"), geometry)
        self.assertIn(b"/CropBox [0 0 36 24] /UserUnit 2", geometry)
        self.assertIn(b"/SMask << /S /Alpha", soft_mask)
        self.assertIn(b"/Group << /S /Transparency /CS /DeviceGray", soft_mask)
        self.assertIn(b"/BM /Multiply", soft_mask)
        self.assertIn(b"/ColorSpace [/ICCBased", colorspaces)
        self.assertIn(b"/Alternate /DeviceRGB", colorspaces)
        self.assertIn(b"/Type /Annot /Subtype /Square", annotation)
        self.assertIn(b"/AP << /N 4 0 R >>", annotation)
        self.assertIn(b"/Subtype /Type3", embedded_font)
        self.assertIn(b"/CharProcs << /Glyph 5 0 R >>", embedded_font)
        self.assertNotIn(b"/FontFile", embedded_font)
        self.assertIn(b"/MediaBox [0 0 14400 14400]", high_dimension)
        self.assertIn(b"/Pages 99 0 R", malformed)
        self.assertTrue(malformed.endswith(b"%%EOF\n"))

    def test_synthetic_rgb_profile_has_nine_valid_v21_tags_with_shared_linear_trc(
        self,
    ) -> None:
        profile = _synthetic_rgb_icc_profile()

        self.assertEqual(len(profile), struct.unpack(">I", profile[:4])[0])
        self.assertEqual(b"\x02\x10\x00\x00", profile[8:12])
        self.assertEqual(b"acsp", profile[36:40])
        self.assertEqual(b"\x00" * 4, profile[4:8])
        self.assertEqual(b"\x00" * 4, profile[40:44])
        self.assertEqual(b"\x00" * 8, profile[48:56])
        self.assertEqual(b"\x00" * 4, profile[80:84])
        self.assertEqual(9, struct.unpack(">I", profile[128:132])[0])
        entries = {
            profile[offset : offset + 4]: struct.unpack(">II", profile[offset + 4 : offset + 12])
            for offset in range(132, 132 + (9 * 12), 12)
        }
        self.assertEqual(
            {
                b"desc",
                b"cprt",
                b"wtpt",
                b"rXYZ",
                b"gXYZ",
                b"bXYZ",
                b"rTRC",
                b"gTRC",
                b"bTRC",
            },
            set(entries),
        )
        self.assertEqual(entries[b"rTRC"], entries[b"gTRC"])
        self.assertEqual(entries[b"rTRC"], entries[b"bTRC"])
        curve_offset, curve_size = entries[b"rTRC"]
        self.assertEqual(b"curv", profile[curve_offset : curve_offset + 4])
        self.assertEqual(
            1,
            struct.unpack(">I", profile[curve_offset + 8 : curve_offset + 12])[0],
        )
        self.assertEqual(
            0x0100,
            struct.unpack(">H", profile[curve_offset + 12 : curve_offset + 14])[0],
        )
        self.assertEqual(14, curve_size)

    def test_installed_lcms_accepts_and_transforms_the_synthetic_rgb_profile(self) -> None:
        source = ImageCms.ImageCmsProfile(io.BytesIO(_synthetic_rgb_icc_profile()))
        destination = ImageCms.createProfile("sRGB")
        transform = ImageCms.buildTransformFromOpenProfiles(
            source,
            destination,
            "RGB",
            "RGB",
        )
        pixels = Image.new("RGB", (3, 1))
        pixels.putdata([(0, 0, 0), (128, 64, 32), (255, 255, 255)])
        converted = ImageCms.applyTransform(pixels, transform)
        assert converted is not None
        middle_pixel = cast(tuple[int, int, int], converted.getpixel((1, 0)))

        self.assertEqual(
            "PaperWorkShell synthetic linear RGB",
            ImageCms.getProfileDescription(source).strip(),
        )
        self.assertEqual((0, 0, 0), converted.getpixel((0, 0)))
        self.assertTrue(
            all(
                converted_channel > source_channel
                for converted_channel, source_channel in zip(
                    middle_pixel,
                    (128, 64, 32),
                    strict=True,
                )
            )
        )
        self.assertEqual((255, 255, 255), converted.getpixel((2, 0)))

    def test_renderer_golden_references_are_analytical_corpus_mirrors(self) -> None:
        references = build_renderer_golden_references(self.fixtures)
        index = build_renderer_golden_index(self.fixtures)
        by_reference = {reference.reference_id: reference for reference in references}

        self.assertEqual(15, len(references))
        self.assertEqual(
            self.by_id["png-render-calibration-crop-rotate"].content,
            by_reference["render-calibration-crop-rotate"].content,
        )
        self.assertEqual(
            self.by_id["png-render-calibration-media-box"].content,
            by_reference["render-calibration-media-box"].content,
        )
        self.assertEqual(
            self.by_id["png-render-transparency"].content,
            by_reference["render-transparency"].content,
        )
        for reference in references:
            with self.subTest(reference=reference.reference_id):
                self.assertEqual(
                    self.by_id[reference.reference_fixture_id].content, reference.content
                )
        self.assertFalse(cast(dict[str, Any], index["policy"])["captured_from_renderer"])
        indexed_references = {
            item["id"]: item for item in cast(list[dict[str, Any]], index["references"])
        }
        inherited_270 = indexed_references["render-geometry-inherited-rotate-270"]
        self.assertEqual(
            {
                "page_index": 3,
                "page_box": "crop",
                "additional_rotation_degrees": 0,
                "maximum_width_pixels": 20,
                "maximum_height_pixels": 30,
            },
            inherited_270["request"],
        )
        self.assertEqual("inherited", inherited_270["box_source"])
        self.assertEqual(270, inherited_270["intrinsic_rotation_degrees"])
        self.assertEqual(270, inherited_270["combined_rotation_degrees"])
        self.assertEqual(1, inherited_270["user_unit"])
        self.assertEqual(
            2,
            indexed_references["render-geometry-user-unit"]["user_unit"],
        )
        self.assertEqual(
            1,
            indexed_references["render-geometry-user-unit"]["render_scale_pixels_per_user_unit"],
        )
        self.assertEqual(36, indexed_references["render-geometry-user-unit"]["width"])
        self.assertEqual(24, indexed_references["render-geometry-user-unit"]["height"])
        self.assertNotIn("pixels_per_point", indexed_references["render-geometry-user-unit"])
        for rotation in (90, 180, 270):
            with self.subTest(additional_rotation=rotation):
                additional = indexed_references[f"render-geometry-additional-rotate-{rotation}"]
                self.assertEqual(0, additional["request"]["page_index"])
                self.assertEqual(
                    rotation,
                    additional["request"]["additional_rotation_degrees"],
                )
                self.assertEqual(0, additional["intrinsic_rotation_degrees"])
                self.assertEqual(rotation, additional["combined_rotation_degrees"])
                self.assertEqual(
                    by_reference[f"render-geometry-inherited-rotate-{rotation}"].content,
                    by_reference[f"render-geometry-additional-rotate-{rotation}"].content,
                )
        self.assertEqual(
            "bounded-channel-delta",
            indexed_references["render-transparency"]["comparison"]["mode"],
        )
        self.assertEqual(
            "bounded-channel-delta",
            indexed_references["render-soft-mask-blend"]["comparison"]["mode"],
        )
        self.assertEqual(
            canonical_renderer_golden_index_bytes(self.fixtures),
            canonical_renderer_golden_index_bytes(tuple(reversed(self.fixtures))),
        )

    def test_renderer_golden_pixels_encode_orientation_and_alpha_oracles(self) -> None:
        def pixel(fixture_id: str, x: int, y: int) -> tuple[int, int, int]:
            width, _height, pixels = _rgb_png_pixels(self.by_id[fixture_id].content)
            offset = (y * width + x) * 3
            value = pixels[offset : offset + 3]
            return value[0], value[1], value[2]

        self.assertEqual((255, 0, 0), pixel("png-render-calibration-crop-rotate", 0, 0))
        self.assertEqual((0, 0, 255), pixel("png-render-calibration-crop-rotate", 39, 0))
        self.assertEqual((0, 255, 0), pixel("png-render-calibration-crop-rotate", 0, 59))
        self.assertEqual((255, 255, 0), pixel("png-render-calibration-crop-rotate", 39, 59))
        self.assertEqual((255, 0, 255), pixel("png-render-calibration-media-box", 0, 0))
        self.assertEqual((0, 255, 255), pixel("png-render-calibration-media-box", 71, 95))
        self.assertEqual((255, 255, 255), pixel("png-render-transparency", 0, 0))
        self.assertEqual((0, 0, 255), pixel("png-render-transparency", 10, 40))
        self.assertEqual((128, 0, 128), pixel("png-render-transparency", 30, 30))
        self.assertEqual((255, 128, 128), pixel("png-render-transparency", 52, 30))
        rotation_corners = {
            "png-render-geometry-inherited-rotate-0": (
                (0, 0, 255),
                (255, 255, 0),
                (255, 0, 0),
                (0, 255, 0),
            ),
            "png-render-geometry-inherited-rotate-90": (
                (255, 0, 0),
                (0, 0, 255),
                (0, 255, 0),
                (255, 255, 0),
            ),
            "png-render-geometry-inherited-rotate-180": (
                (0, 255, 0),
                (255, 0, 0),
                (255, 255, 0),
                (0, 0, 255),
            ),
            "png-render-geometry-inherited-rotate-270": (
                (255, 255, 0),
                (0, 255, 0),
                (0, 0, 255),
                (255, 0, 0),
            ),
        }
        for fixture_id, expected_corners in rotation_corners.items():
            width, height, _pixels = _rgb_png_pixels(self.by_id[fixture_id].content)
            actual_corners = (
                pixel(fixture_id, 0, 0),
                pixel(fixture_id, width - 1, 0),
                pixel(fixture_id, 0, height - 1),
                pixel(fixture_id, width - 1, height - 1),
            )
            self.assertEqual(expected_corners, actual_corners)
        self.assertEqual((0, 0, 255), pixel("png-render-geometry-direct-box", 0, 0))
        self.assertEqual((0, 255, 0), pixel("png-render-geometry-user-unit", 35, 23))
        self.assertEqual((0, 0, 0), pixel("png-render-soft-mask-blend", 8, 8))
        self.assertEqual((255, 0, 0), pixel("png-render-soft-mask-blend", 56, 56))
        self.assertEqual((0, 255, 255), pixel("png-render-annotation-appearance", 16, 16))
        self.assertEqual((255, 255, 255), pixel("png-render-annotation-appearance", 15, 15))
        self.assertEqual((0, 0, 0), pixel("png-render-embedded-type3-font", 8, 32))
        self.assertEqual((255, 255, 255), pixel("png-render-embedded-type3-font", 7, 32))

    def test_regeneration_writes_only_the_reviewed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            count = write_committed_corpus(root)
            first = {
                path.relative_to(root / CORPUS_DIRECTORY): path.read_bytes()
                for path in (root / CORPUS_DIRECTORY).rglob("*")
                if path.is_file()
            }
            second_count = write_committed_corpus(root)
            second = {
                path.relative_to(root / CORPUS_DIRECTORY): path.read_bytes()
                for path in (root / CORPUS_DIRECTORY).rglob("*")
                if path.is_file()
            }
            golden = {
                path.relative_to(root / GOLDEN_DIRECTORY): path.read_bytes()
                for path in (root / GOLDEN_DIRECTORY).rglob("*")
                if path.is_file()
            }

        self.assertEqual(41, count)
        self.assertEqual(count, second_count)
        self.assertEqual(first, second)
        self.assertEqual(42, len(first))
        loaded = json.loads(first[Path("index.json")])
        self.assertEqual("public-synthetic-only", loaded["policy"]["classification"])

        self.assertEqual(16, len(golden))
        self.assertEqual(
            canonical_renderer_golden_index_bytes(self.fixtures),
            golden[Path("index.json")],
        )
        for reference in build_renderer_golden_references(self.fixtures):
            self.assertEqual(reference.content, golden[Path(*reference.relative_path.parts)])

    def test_on_demand_recipes_are_bounded_external_and_non_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            output = base / "generated"

            oversized = generate_oversized_pdf(
                output,
                repository_root=repository,
                byte_count=1_048_576,
            )
            self.assertEqual(1_048_576, oversized.stat().st_size)
            self.assertTrue(oversized.read_bytes().startswith(b"%PDF-"))
            with self.assertRaisesRegex(FixtureGenerationError, "overwrite"):
                generate_oversized_pdf(
                    output,
                    repository_root=repository,
                    byte_count=1_048_576,
                )

            long_path = generate_long_path_tree(
                base / "long-path",
                repository_root=repository,
                component_count=2,
            )
            self.assertTrue(long_path.read_bytes().startswith(b"%PDF-"))
            with self.assertRaisesRegex(FixtureGenerationError, "outside the repository"):
                generate_long_path_tree(repository / "unsafe", repository_root=repository)

    def test_on_demand_recipes_remove_partial_files_when_cancelled(self) -> None:
        def interrupt_after_partial_write(stream: Any, *_arguments: object) -> None:
            stream.write(b"x" * 17)
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            oversized_output = base / "oversized"
            with (
                patch(
                    "tools.generate_synthetic_fixtures._write_oversized_payload",
                    side_effect=interrupt_after_partial_write,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                generate_oversized_pdf(
                    oversized_output,
                    repository_root=repository,
                    byte_count=1_048_576,
                )
            self.assertFalse((oversized_output / "pws-synthetic-oversized.pdf").exists())

            long_output = base / "long"
            with (
                patch(
                    "tools.generate_synthetic_fixtures._write_external_bytes",
                    side_effect=interrupt_after_partial_write,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                generate_long_path_tree(
                    long_output,
                    repository_root=repository,
                    component_count=2,
                )
            long_target = (
                long_output
                / LONG_PATH_COMPONENT
                / LONG_PATH_COMPONENT
                / "pws-synthetic-long-path.pdf"
            )
            self.assertFalse(long_target.exists())

    def test_regeneration_rejects_a_linked_output_parent_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def mark_pdf_directory_unsafe(path: Path) -> bool:
                return path.name == "pdf"

            with (
                patch(
                    "tools.generate_synthetic_fixtures._is_link_or_reparse",
                    side_effect=mark_pdf_directory_unsafe,
                ),
                self.assertRaisesRegex(FixtureGenerationError, "unsafe directory"),
            ):
                write_committed_corpus(root)

            self.assertFalse((root / CORPUS_DIRECTORY / "pdf" / "born-digital.pdf").exists())

    def test_long_path_recipe_rejects_a_linked_nested_component_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            output = base / "long-path"
            linked_component = output / LONG_PATH_COMPONENT
            linked_component.mkdir(parents=True)

            with (
                patch(
                    "tools.generate_synthetic_fixtures._is_link_or_reparse",
                    side_effect=lambda path: path.name == LONG_PATH_COMPONENT,
                ),
                self.assertRaisesRegex(FixtureGenerationError, "unsafe directory"),
            ):
                generate_long_path_tree(
                    output,
                    repository_root=repository,
                    component_count=2,
                )

            self.assertFalse(
                (linked_component / LONG_PATH_COMPONENT / "pws-synthetic-long-path.pdf").exists()
            )

    def test_on_demand_recipe_rejects_a_linked_destination_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            repository.mkdir()
            output = base / "linked-output"
            output.mkdir()

            with (
                patch(
                    "tools.generate_synthetic_fixtures._is_link_or_reparse",
                    side_effect=lambda path: path == output,
                ),
                self.assertRaisesRegex(FixtureGenerationError, "non-linked directory"),
            ):
                generate_long_path_tree(
                    output,
                    repository_root=repository,
                    component_count=2,
                )

            self.assertEqual([], list(output.iterdir()))


if __name__ == "__main__":
    unittest.main()
