from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from tools.reference_profile import (
    PROJECT_ROOT,
    BenchmarkError,
    ReferenceAttestations,
    benchmark_report,
    validate_profile,
)

DiskUsage = namedtuple("DiskUsage", ("total", "used", "free"))
EXPECTED_FILE_SHA256 = "8a8bf3b68cac0e3b77effddf97749adce36999f135bbcc6887fb982d9e48533c"


class ReferenceProfileTests(unittest.TestCase):
    def _copy_profile_root(self, root: Path) -> dict[str, Any]:
        (root / "benchmarks").mkdir()
        (root / "schemas").mkdir()
        shutil.copy2(
            PROJECT_ROOT / "benchmarks/reference-windows-v1.json",
            root / "benchmarks/reference-windows-v1.json",
        )
        shutil.copy2(
            PROJECT_ROOT / "schemas/reference-profile.schema.json",
            root / "schemas/reference-profile.schema.json",
        )
        loaded = json.loads(
            (root / "benchmarks/reference-windows-v1.json").read_text(encoding="utf-8")
        )
        return cast(dict[str, Any], loaded)

    def _write_profile(self, root: Path, profile: dict[str, Any]) -> None:
        (root / "benchmarks/reference-windows-v1.json").write_text(
            json.dumps(profile),
            encoding="utf-8",
        )

    def test_checked_in_profile_is_valid(self) -> None:
        profile, issues = validate_profile()

        self.assertEqual([], issues)
        self.assertIsNotNone(profile)

    def test_wrong_cpu_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self._copy_profile_root(root)
            cast(dict[str, Any], profile["environment"])["logical_processors"] = 4
            self._write_profile(root, profile)

            loaded, issues = validate_profile(root)

        self.assertIsNone(loaded)
        self.assertTrue(any(issue.code == "REFERENCE_PROFILE_INVALID" for issue in issues))

    def test_nonempty_product_targets_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self._copy_profile_root(root)
            cast(dict[str, Any], profile["measurement_policy"])["product_targets"] = [
                {"maximum_seconds": 1}
            ]
            self._write_profile(root, profile)

            loaded, issues = validate_profile(root)

        self.assertIsNone(loaded)
        self.assertTrue(any(issue.code == "REFERENCE_PROFILE_INVALID" for issue in issues))

    def test_duplicate_scenario_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self._copy_profile_root(root)
            scenarios = cast(list[dict[str, Any]], profile["storage_scenarios"])
            scenarios[1]["id"] = "local-smoke"
            self._write_profile(root, profile)

            loaded, issues = validate_profile(root)

        self.assertIsNone(loaded)
        self.assertTrue(
            any(
                issue.code in {"REFERENCE_SCENARIO_ID_DUPLICATE", "REFERENCE_PROFILE_INVALID"}
                for issue in issues
            )
        )

    def test_duplicate_json_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_profile_root(root)
            profile_path = root / "benchmarks/reference-windows-v1.json"
            text = profile_path.read_text(encoding="utf-8")
            profile_path.write_text(
                text.replace(
                    '  "schema_version": 1,',
                    '  "schema_version": 1,\n  "schema_version": 1,',
                    1,
                ),
                encoding="utf-8",
            )

            loaded, issues = validate_profile(root)

        self.assertIsNone(loaded)
        self.assertTrue(any(issue.code == "REFERENCE_JSON_DUPLICATE_KEY" for issue in issues))

    def test_nonstandard_json_constant_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_profile_root(root)
            profile_path = root / "benchmarks/reference-windows-v1.json"
            text = profile_path.read_text(encoding="utf-8")
            profile_path.write_text(
                text.replace('  "schema_version": 1,', '  "schema_version": NaN,', 1),
                encoding="utf-8",
            )

            loaded, issues = validate_profile(root)

        self.assertIsNone(loaded)
        self.assertTrue(
            any(issue.code == "REFERENCE_JSON_NONSTANDARD_CONSTANT" for issue in issues)
        )

    def test_non_utf8_inputs_are_rejected_without_path_disclosure(self) -> None:
        relative_paths = (
            Path("benchmarks/reference-windows-v1.json"),
            Path("schemas/reference-profile.schema.json"),
        )
        for relative_path in relative_paths:
            with self.subTest(relative_path=relative_path):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self._copy_profile_root(root)
                    (root / relative_path).write_bytes(b'{"schema_version": \xff}')

                    loaded, issues = validate_profile(root)
                    rendered = "\n".join(issue.render() for issue in issues)

                self.assertIsNone(loaded)
                self.assertIn("REFERENCE_TEXT_ENCODING_INVALID", rendered)
                self.assertNotIn(str(root), rendered)

    def test_constrained_scenario_requires_unobservable_attestations(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        observations: dict[str, object] = {
            "os_family": "Windows",
            "architecture": "x86_64",
            "logical_processors": 2,
            "memory_bytes": 4 * 1024 * 1024 * 1024,
            "storage_capacity_bytes": 10 * 1024 * 1024,
            "storage_free_bytes": 8 * 1024 * 1024,
            "graphics": None,
            "storage_media": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "tools.reference_profile._observed_environment",
                    return_value=observations,
                ),
                self.assertRaisesRegex(BenchmarkError, "do not match the profile") as raised,
            ):
                benchmark_report(
                    profile,
                    "rotational-hdd",
                    Path(temporary),
                    attestations=ReferenceAttestations(),
                )

        assert raised.exception.details is not None
        evaluation = cast(
            dict[str, Any], raised.exception.details["reference_environment_evaluation"]
        )
        requirements = cast(dict[str, dict[str, Any]], evaluation["requirements"])
        self.assertEqual("mismatched", evaluation["status"])
        self.assertEqual("unknown", requirements["memory_mib"]["status"])
        self.assertEqual("unknown", requirements["graphics_class"]["status"])
        self.assertEqual("matched", requirements["storage_capacity_bytes"]["status"])
        self.assertEqual("matched", requirements["storage_free_bytes"]["status"])
        self.assertEqual("unknown", requirements["storage_media"]["status"])

    def test_reference_run_requires_observed_and_attested_match(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None
        observations: dict[str, object] = {
            "os_family": "Windows",
            "architecture": "x86_64",
            "logical_processors": 2,
            "memory_bytes": 4 * 1024 * 1024 * 1024,
            "storage_capacity_bytes": 10 * 1024 * 1024,
            "storage_free_bytes": 8 * 1024 * 1024,
            "graphics": None,
            "storage_media": None,
        }
        attestations = ReferenceAttestations(
            memory_mib=4096,
            graphics_class="integrated",
            storage_media="hdd-or-validated-emulation",
        )

        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "tools.reference_profile._observed_environment",
                return_value=observations,
            ):
                report = benchmark_report(
                    profile,
                    "rotational-hdd",
                    Path(temporary),
                    attestations=attestations,
                )

        evaluation = cast(dict[str, Any], report["reference_environment_evaluation"])
        self.assertEqual("matched", evaluation["status"])

    def test_constrained_storage_envelope_is_observed(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None
        observations: dict[str, object] = {
            "os_family": "Windows",
            "architecture": "x86_64",
            "logical_processors": 2,
            "memory_bytes": 4 * 1024 * 1024 * 1024,
            "storage_capacity_bytes": 9 * 1024 * 1024 * 1024,
            "storage_free_bytes": 2 * 1024 * 1024,
            "graphics": None,
            "storage_media": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "tools.reference_profile._observed_environment",
                    return_value=observations,
                ),
                self.assertRaises(BenchmarkError) as raised,
            ):
                benchmark_report(
                    profile,
                    "limited-storage",
                    Path(temporary),
                    attestations=ReferenceAttestations(
                        memory_mib=4096,
                        graphics_class="integrated",
                    ),
                )

        assert raised.exception.details is not None
        evaluation = cast(
            dict[str, Any], raised.exception.details["reference_environment_evaluation"]
        )
        requirements = cast(dict[str, dict[str, Any]], evaluation["requirements"])
        self.assertEqual("mismatched", requirements["storage_capacity_bytes"]["status"])
        self.assertEqual("mismatched", requirements["storage_free_bytes"]["status"])

    def test_python_cpu_count_overrides_are_rejected(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        for override in ("environment", "xoption"):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temporary:
                environment = {"PYTHON_CPU_COUNT": "2"} if override == "environment" else {}
                xoptions = {"cpu_count": "2"} if override == "xoption" else {}
                with (
                    patch.dict("os.environ", environment, clear=False),
                    patch("sys._xoptions", xoptions),
                    self.assertRaises(BenchmarkError) as raised,
                ):
                    benchmark_report(
                        profile,
                        "local-smoke",
                        Path(temporary),
                        attestations=ReferenceAttestations(),
                    )

                self.assertEqual("REFERENCE_CPU_OVERRIDE", raised.exception.code)

    def test_generated_workload_is_deterministic_and_cleans_up(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = benchmark_report(
                profile,
                "local-smoke",
                root,
                attestations=ReferenceAttestations(),
            )
            second = benchmark_report(
                profile,
                "local-smoke",
                root,
                attestations=ReferenceAttestations(),
            )
            remaining = list(root.iterdir())

        first_measurements = cast(list[dict[str, Any]], first["measurements"])
        second_measurements = cast(list[dict[str, Any]], second["measurements"])
        self.assertEqual([], remaining)
        self.assertEqual(
            [measurement["sha256"] for measurement in first_measurements],
            [measurement["sha256"] for measurement in second_measurements],
        )
        self.assertEqual(
            [EXPECTED_FILE_SHA256, EXPECTED_FILE_SHA256],
            [measurement["sha256"] for measurement in first_measurements],
        )
        self.assertEqual(
            [measurement["bytes_read"] for measurement in first_measurements],
            [measurement["bytes_read"] for measurement in second_measurements],
        )
        self.assertEqual([], first["product_targets"])

    def test_generator_drift_is_rejected_and_cleans_up(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "tools.reference_profile._deterministic_block",
                    return_value=b"\0" * 65536,
                ),
                self.assertRaisesRegex(BenchmarkError, "versioned profile") as raised,
            ):
                benchmark_report(
                    profile,
                    "local-smoke",
                    root,
                    attestations=ReferenceAttestations(),
                )
            remaining = list(root.iterdir())

        self.assertEqual("BENCHMARK_WORKLOAD_MISMATCH", raised.exception.code)
        self.assertEqual([], remaining)

    def test_checksum_failure_cleans_generated_files(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "tools.reference_profile._read_generated_file",
                    return_value=(1048576, "0" * 64, 1),
                ),
                self.assertRaisesRegex(BenchmarkError, "checksum changed"),
            ):
                benchmark_report(
                    profile,
                    "local-smoke",
                    root,
                    attestations=ReferenceAttestations(),
                )
            remaining = list(root.iterdir())

        self.assertEqual([], remaining)

    def test_insufficient_storage_fails_before_writing(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "tools.reference_profile.shutil.disk_usage",
                    return_value=DiskUsage(total=100, used=100, free=0),
                ),
                self.assertRaisesRegex(BenchmarkError, "enough free space"),
            ):
                benchmark_report(
                    profile,
                    "local-smoke",
                    Path(temporary),
                    attestations=ReferenceAttestations(),
                )

    def test_disk_observation_errors_are_sanitized_and_clean_up(self) -> None:
        profile, issues = validate_profile()
        self.assertEqual([], issues)
        assert profile is not None
        synthetic_path = r"C:\Users\synthetic-person\benchmark-work"
        available = DiskUsage(total=10_000_000, used=0, free=10_000_000)

        for failing_call in (1, 2, 3):
            with self.subTest(failing_call=failing_call):
                effects: list[object] = [available] * 3
                effects[failing_call - 1] = OSError(synthetic_path)
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    with (
                        patch(
                            "tools.reference_profile.shutil.disk_usage",
                            side_effect=effects,
                        ),
                        self.assertRaises(BenchmarkError) as raised,
                    ):
                        benchmark_report(
                            profile,
                            "local-smoke",
                            root,
                            attestations=ReferenceAttestations(),
                        )
                    remaining = list(root.iterdir())

                self.assertEqual("BENCHMARK_IO_FAILED", raised.exception.code)
                self.assertEqual("OSError", raised.exception.message)
                self.assertNotIn(synthetic_path, raised.exception.message)
                self.assertEqual([], remaining)


if __name__ == "__main__":
    unittest.main()
