from __future__ import annotations

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

from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    LONG_PATH_COMPONENT,
    SYNTHETIC_MARKER,
    FixtureGenerationError,
    build_committed_fixtures,
    build_index,
    canonical_index_bytes,
    generate_long_path_tree,
    generate_oversized_pdf,
    write_committed_corpus,
)


class SyntheticFixtureGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = build_committed_fixtures()
        self.by_id = {fixture.fixture_id: fixture for fixture in self.fixtures}

    def test_generation_is_byte_deterministic_and_has_no_external_provenance(self) -> None:
        second = build_committed_fixtures()

        self.assertEqual(self.fixtures, second)
        self.assertEqual(canonical_index_bytes(self.fixtures), canonical_index_bytes(second))
        self.assertEqual(19, len(self.fixtures))
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

        self.assertEqual(19, count)
        self.assertEqual(count, second_count)
        self.assertEqual(first, second)
        self.assertEqual(20, len(first))
        loaded = json.loads(first[Path("index.json")])
        self.assertEqual("public-synthetic-only", loaded["policy"]["classification"])

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
