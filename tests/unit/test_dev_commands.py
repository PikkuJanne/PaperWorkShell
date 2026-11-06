from __future__ import annotations

import io
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

from tools.dev import (
    CHECK_COMMANDS,
    CI_CHECK_GROUPS,
    COMMANDS,
    GITLEAKS_STDIN_ARGUMENTS,
    PINNED_PATH_RULE_SUFFIXES,
    SCANNER_GIT_ENVIRONMENT_BLOCKLIST,
    ToolRequirementError,
    _check_commands,
    _git_executable,
    _path_rule_alias,
    _run,
    _scanner_environment,
    _trusted_gitleaks_config,
    _with_trusted_controls,
    main,
    run_named,
)


class DeveloperCommandRegistryTests(unittest.TestCase):
    def test_required_commands_have_one_registry(self) -> None:
        required = {
            "format",
            "lint",
            "typecheck",
            "test-unit",
            "test-integration",
            "test-security",
            "test-accessibility",
            "generate-fixtures",
            "validate-fixtures",
            "validate-schemas",
            "validate-localization",
            "validate-dependencies",
            "validate-task-graph",
            "validate-docs",
            "validate-portfolio-boundary",
            "validate-m1-benchmark-report",
            "validate-pws0208-resource-workload",
            "validate-pdf-renderer-benchmark",
            "scan-secrets",
            "quality-self-test",
            "run-m1-vertical-slice",
            "run-pws0208-resource-workload",
            "benchmark-pdf-renderers",
            "pws0307-reader-test",
        }

        self.assertLessEqual(required, set(COMMANDS))
        for retired in (
            "validate-reference-profile",
            "benchmark-reference",
            "benchmark-m1-vertical-slice",
        ):
            self.assertNotIn(retired, COMMANDS)

    def test_check_order_is_derived_from_registry(self) -> None:
        expected = tuple(
            name for name, specification in COMMANDS.items() if specification.include_in_check
        )

        self.assertEqual(expected, CHECK_COMMANDS)
        self.assertNotIn("format", CHECK_COMMANDS)
        self.assertNotIn("run-m1-vertical-slice", CHECK_COMMANDS)
        self.assertNotIn("run-pws0208-resource-workload", CHECK_COMMANDS)
        self.assertNotIn("benchmark-pdf-renderers", CHECK_COMMANDS)
        self.assertNotIn("pws0307-reader-test", CHECK_COMMANDS)
        self.assertNotIn("generate-fixtures", CHECK_COMMANDS)
        self.assertNotIn("validate-dependencies", CHECK_COMMANDS)
        self.assertIn("format-check", CHECK_COMMANDS)
        self.assertIn("validate-fixtures", CHECK_COMMANDS)
        self.assertIn("validate-localization", CHECK_COMMANDS)
        self.assertIn("validate-pdf-renderer-benchmark", CHECK_COMMANDS)
        self.assertIn("validate-pws0208-resource-workload", CHECK_COMMANDS)
        self.assertIn("validate-portfolio-boundary", CHECK_COMMANDS)
        self.assertIn("quality-self-test", CHECK_COMMANDS)
        self.assertEqual(
            ("python", "-m", "mypy", "src", "tools", "tests"),
            COMMANDS["typecheck"].arguments,
        )
        self.assertEqual(
            ("python", "-m", "tools.pws0307_reader_test"),
            COMMANDS["pws0307-reader-test"].arguments,
        )

    def test_ci_groups_cover_every_check_exactly_once_in_registry_order(self) -> None:
        integration = _check_commands("integration")
        core = _check_commands("core")

        self.assertEqual(("integration", "core"), CI_CHECK_GROUPS)
        self.assertEqual(("test-integration",), integration)
        self.assertFalse(set(integration) & set(core))
        self.assertCountEqual(CHECK_COMMANDS, (*integration, *core))
        self.assertEqual(tuple(name for name in CHECK_COMMANDS if name not in integration), core)
        self.assertEqual(CHECK_COMMANDS, _check_commands(None))

    def test_new_registered_check_automatically_belongs_to_core(self) -> None:
        expanded = (*CHECK_COMMANDS, "synthetic-new-gate")
        with patch("tools.dev.CHECK_COMMANDS", expanded):
            self.assertEqual("synthetic-new-gate", _check_commands("core")[-1])
            self.assertCountEqual(
                expanded, (*_check_commands("integration"), *_check_commands("core"))
            )

    def test_check_dispatches_exact_selected_group_and_propagates_failures(self) -> None:
        for group in (None, *CI_CHECK_GROUPS):
            expected = _check_commands(group)
            with self.subTest(group=group):
                with patch("tools.dev.run_named", return_value=0) as dispatch:
                    self.assertEqual(0, run_named("check", ci_group=group))
                self.assertEqual([call(name) for name in expected], dispatch.call_args_list)

                failure_index = min(1, len(expected) - 1)
                returns = [0] * failure_index + [37]
                with patch("tools.dev.run_named", side_effect=returns) as dispatch:
                    self.assertEqual(37, run_named("check", ci_group=group))
                self.assertEqual(
                    [call(name) for name in expected[: failure_index + 1]],
                    dispatch.call_args_list,
                )

    def test_cli_dispatches_each_closed_group_and_preserves_default(self) -> None:
        for group in (None, *CI_CHECK_GROUPS):
            arguments = ["check"] if group is None else ["check", "--ci-group", group]
            with (
                self.subTest(group=group),
                patch("tools.dev.run_named", return_value=29) as dispatch,
            ):
                self.assertEqual(29, main(arguments))
                dispatch.assert_called_once_with("check", ci_group=group)

    def test_ci_group_misuse_fails_before_any_command(self) -> None:
        for arguments in (
            ["check", "--ci-group", "unknown"],
            ["lint", "--ci-group", "core"],
            ["bootstrap", "--ci-group", "integration"],
        ):
            with (
                self.subTest(arguments=arguments),
                patch("tools.dev.run_named") as dispatch,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                main(arguments)
            self.assertEqual(2, caught.exception.code)
            dispatch.assert_not_called()
        with self.assertRaises(ValueError):
            _check_commands("unknown")
        with self.assertRaises(ValueError), patch("tools.dev._run") as execute:
            run_named("lint", ci_group="core")
        execute.assert_not_called()

    def test_secret_scan_arguments_are_local_redacted_and_bounded(self) -> None:
        rendered = " ".join(GITLEAKS_STDIN_ARGUMENTS)

        self.assertTrue(rendered.startswith("stdin "))
        self.assertIn("--redact=100", rendered)
        self.assertIn("--timeout 120", rendered)
        self.assertIn("--exit-code 42", rendered)
        self.assertIn("--ignore-gitleaks-allow", rendered)
        self.assertNotIn("report", rendered)
        self.assertNotIn("max-target", rendered)

        trusted_ignore = Path("trusted-empty-ignore")
        guarded = _with_trusted_controls(GITLEAKS_STDIN_ARGUMENTS, trusted_ignore)
        ignore_index = guarded.index("--gitleaks-ignore-path")
        self.assertEqual(str(trusted_ignore), guarded[ignore_index + 1])
        config_index = guarded.index("--config")
        self.assertEqual(str(_trusted_gitleaks_config(trusted_ignore)), guarded[config_index + 1])
        self.assertNotIn(".gitleaks.toml", GITLEAKS_STDIN_ARGUMENTS)

    def test_command_banner_does_not_disclose_executable_path(self) -> None:
        output = io.StringIO()
        completed = subprocess.CompletedProcess[str](args=[], returncode=0, stdout="", stderr="")

        with patch("tools.dev.subprocess.run", return_value=completed), redirect_stdout(output):
            result = _run((r"C:\Users\synthetic-person\uv.exe", "--version"))

        self.assertEqual(0, result)
        self.assertIn("uv.exe --version", output.getvalue())
        self.assertNotIn("synthetic-person", output.getvalue())

    def test_unsupported_git_version_fails_closed(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "--version"],
            returncode=0,
            stdout=b"git version 2.44.4\n",
            stderr=b"",
        )

        with (
            patch("tools.dev.shutil.which", return_value="git"),
            patch("tools.dev._capture_bytes", return_value=completed),
            self.assertRaisesRegex(ToolRequirementError, "^git$"),
        ):
            _git_executable()

    def test_secret_scan_subprocesses_ignore_git_environment_overrides(self) -> None:
        overrides = {name: "synthetic-override" for name in SCANNER_GIT_ENVIRONMENT_BLOCKLIST}
        overrides.update(
            {
                "GIT_CONFIG_KEY_7": "core.synthetic",
                "GIT_CONFIG_VALUE_7": "true",
                "GIT_TEST_COMMIT_GRAPH": "1",
            }
        )
        with patch.dict(os.environ, overrides, clear=False):
            child_environment = _scanner_environment()

        for name in overrides.keys() - {"GIT_CONFIG_COUNT"}:
            self.assertNotIn(name, child_environment)
        self.assertEqual("1", child_environment["GIT_NO_LAZY_FETCH"])
        self.assertEqual("1", child_environment["GIT_NO_REPLACE_OBJECTS"])
        self.assertEqual("0", child_environment["GIT_TERMINAL_PROMPT"])
        self.assertEqual("2", child_environment["GIT_CONFIG_COUNT"])
        self.assertEqual("core.commitGraph", child_environment["GIT_CONFIG_KEY_0"])
        self.assertEqual("false", child_environment["GIT_CONFIG_VALUE_0"])
        self.assertEqual("core.fsmonitor", child_environment["GIT_CONFIG_KEY_1"])
        self.assertEqual("false", child_environment["GIT_CONFIG_VALUE_1"])

    def test_path_rule_aliases_avoid_pinned_global_allowlist_names(self) -> None:
        for suffix in PINNED_PATH_RULE_SUFFIXES:
            with self.subTest(suffix=suffix):
                alias = _path_rule_alias(Path(f"node_modules/gitleaks.toml{suffix}"))
                self.assertEqual(Path(f"pws-rule{suffix}"), alias)

        self.assertEqual(
            Path("pws-rule.nuget.config"),
            _path_rule_alias(Path("node_modules/gitleaks.toml.nuget.config")),
        )
        self.assertEqual(
            Path("pws-rule.yaml"),
            _path_rule_alias(Path("node_modules/pnpm-lock.yaml")),
        )
        self.assertIsNone(_path_rule_alias(Path("node_modules/synthetic.txt")))

    def test_list_is_generated_from_registry(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            result = main(["list"])

        self.assertEqual(0, result)
        self.assertIn("tools/dev.py is the source of truth", output.getvalue())
        for name in COMMANDS:
            self.assertIn(name, output.getvalue())


if __name__ == "__main__":
    unittest.main()
