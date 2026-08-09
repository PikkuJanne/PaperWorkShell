from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.validate_docs import PROJECT_ROOT, main, validation_issues

THREAT_ROWS = (
    ("TM-001", "A parser exploit compromises a document engine"),
    ("TM-002", "Resource use exhausts the host"),
    ("TM-005", "Import modifies an original source"),
    ("TM-009", "Untrusted input injects a command"),
    ("TM-010", "A template executes code"),
    ("TM-011", "Routine logs disclose document content"),
    ("TM-012", "Case temp data persists after failure"),
    ("TM-014", "A corrupt export is reported as successful"),
    ("TM-016", "Editing invalidates a digital signature"),
    ("TM-017", "Failed redaction leaves information recoverable"),
    ("TM-020", "A dependency is compromised"),
)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"fixture text not found: {old}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _minimal_repository(root: Path) -> None:
    _write_text(root / "DECISIONS.md", "# Decisions\n\n## PWS-D001 — Synthetic decision\n")
    _write_text(root / "TASKS.yaml", "tasks:\n- id: PWS-0001\n")
    _write_text(
        root / "README.md",
        "# Fixture\n\n"
        "[Threats](docs/security/threat-model.md#threat-catalogue)\n\n"
        "[External](https://example.invalid/no-network) and "
        "[mail](mailto:security@example.invalid).\n",
    )

    catalogue = "\n".join(
        f"| {threat_id} | {scenario} | High | A bounded synthetic outcome |"
        for threat_id, scenario in THREAT_ROWS
    )
    _write_text(
        root / "docs/security/threat-model.md",
        "# Threat model\n\n"
        "## Threat catalogue\n\n"
        "| ID | Threat scenario | Impact | Required security outcome |\n"
        "|---|---|---|---|\n"
        f"{catalogue}\n",
    )

    trace_rows = "\n".join(
        f"| {threat_id} | PWS-D001 | PWS-0001 | Synthetic evidence |"
        for threat_id, _scenario in THREAT_ROWS
    )
    _write_text(
        root / "docs/security/threat-traceability.md",
        "# Threat-to-decision and task traceability\n\n"
        "| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |\n"
        "|---|---|---|---|\n"
        f"{trace_rows}\n",
    )

    threat_list = ", ".join(threat_id for threat_id, _scenario in THREAT_ROWS)
    _write_text(
        root / "docs/security/security-test-plan.md",
        "# Initial security and privacy test plan\n\n"
        "## Planned suites\n\n"
        "| Test ID | Threats | Earliest owning tasks | Layer and synthetic scenario | Required oracle |\n"
        "|---|---|---|---|---|\n"
        f"| ST-001 | {threat_list} | PWS-0001 | Synthetic case | Stable result |\n",
    )


def _codes(root: Path) -> list[str]:
    return [issue.code for issue in validation_issues(root)]


class DocumentationValidationTests(unittest.TestCase):
    def test_checked_in_repository_passes(self) -> None:
        self.assertEqual([], validation_issues(PROJECT_ROOT))

    def test_minimal_repository_and_cli_pass_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            (root / "docs/pixel.png").write_bytes(b"synthetic-image")
            _write_text(
                root / "docs/anchors.md",
                "# Anchors\n\n"
                "[same file](#repeat)\n\n"
                "## Repeat\n\n"
                "## Repeat\n\n"
                "![image](pixel.png)\n",
            )
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n[First](docs/anchors.md#repeat) [second](docs/anchors.md#repeat-1).\n"
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("socket.create_connection", side_effect=AssertionError("network used")),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = main(["--root", str(root)])

        self.assertEqual(0, result)
        self.assertIn("documentation links", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_fenced_code_is_ignored_but_visible_unknown_ids_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n```markdown\n"
                    "PWS-D998 PWS-9998 TM-998 [missing](nowhere.md)\n"
                    "```still-fenced\n"
                    "PWS-D997 PWS-9997 TM-997 [missing](also-nowhere.md)\n"
                    "```\n"
                    "Visible PWS-D999, PWS-9999, and TM-999.\n"
                )

            issues = validation_issues(root)

        self.assertEqual(
            {
                "DOC_DECISION_REFERENCE_UNKNOWN",
                "DOC_TASK_REFERENCE_UNKNOWN",
                "DOC_THREAT_REFERENCE_UNKNOWN",
            },
            {issue.code for issue in issues},
        )
        rendered = "\n".join(issue.render() for issue in issues)
        self.assertNotIn("PWS-D998", rendered)
        self.assertNotIn("nowhere.md", rendered)

    def test_invalid_backtick_fence_info_fails_closed_and_keeps_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n```markdown`invalid\nPWS-D999 and [visible](missing.md) remain visible.\n"
                )

            codes = _codes(root)

        self.assertIn("DOC_MARKDOWN_VISIBILITY_INVALID", codes)
        self.assertIn("DOC_DECISION_REFERENCE_UNKNOWN", codes)
        self.assertIn("DOC_LINK_TARGET_MISSING", codes)

    def test_tilde_fence_info_may_contain_backticks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n~~~markdown`allowed\nPWS-D999 and [hidden](missing.md) remain hidden.\n~~~\n"
                )

            issues = validation_issues(root)

        self.assertEqual([], issues)

    def test_invalid_local_link_classes_are_rejected(self) -> None:
        cases = (
            ("[missing](missing.md)", "DOC_LINK_TARGET_MISSING"),
            ("[case](Docs/security/threat-model.md)", "DOC_LINK_CASE_MISMATCH"),
            ("[escape](../outside.md)", "DOC_LINK_OUTSIDE_ROOT"),
            ("[drive](C:/outside.md)", "DOC_LINK_ABSOLUTE"),
            ("[unc](//server/share.md)", "DOC_LINK_ABSOLUTE"),
            ("[slash](docs\\security\\threat-model.md)", "DOC_LINK_BACKSLASH"),
            ("[encoded slash](%2Foutside.md)", "DOC_LINK_ABSOLUTE"),
            ("[encoded drive](C%3A/outside.md)", "DOC_LINK_ABSOLUTE"),
            ("[encoded backslash](docs%5Csecurity%5Cthreat-model.md)", "DOC_LINK_BACKSLASH"),
            ("[scheme](ftp://example.invalid/file)", "DOC_LINK_SCHEME_UNSUPPORTED"),
            ("[query](docs/security/threat-model.md?raw=1)", "DOC_LINK_QUERY_UNSUPPORTED"),
        )
        for link, expected_code in cases:
            with self.subTest(link=link), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{link}\n")

                codes = _codes(root)

            self.assertIn(expected_code, codes)

    def test_missing_anchor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\n[missing](docs/security/threat-model.md#absent)\n")

            codes = _codes(root)

        self.assertIn("DOC_LINK_ANCHOR_MISSING", codes)

    def test_malformed_url_is_a_stable_sanitized_link_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\n[malformed](http://[)\n")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(["--root", str(root)])

        self.assertEqual(1, result)
        self.assertIn("DOC_LINK_INVALID", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertNotIn(temporary, stderr.getvalue())

    def test_fenced_decision_heading_is_not_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "DECISIONS.md").open("a", encoding="utf-8") as stream:
                stream.write("\n```markdown\n## PWS-D999 — Not a decision\n```\n")
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\nPWS-D999 is not authoritative.\n")

            codes = _codes(root)

        self.assertIn("DOC_DECISION_REFERENCE_UNKNOWN", codes)

    def test_html_comments_hide_unknown_references_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\nPWS-D001<!-- PWS-D999 PWS-9999 TM-999 "
                    "[hidden](missing.md) --> and PWS-0001.\n"
                    "<!--\n"
                    "PWS-D998 PWS-9998 TM-998 [hidden](also-missing.md)\n"
                    "-->\n"
                    "PWS-D0<!-- join -->01 and PWS-0<!-- join -->001 remain visible.\n"
                )

            issues = validation_issues(root)

        self.assertEqual([], issues)

    def test_comment_delimiters_cannot_split_an_unknown_visible_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\nPWS-D9<!-- hidden join -->99 remains visible.\n")

            issues = validation_issues(root)

        self.assertEqual(["DOC_DECISION_REFERENCE_UNKNOWN"], [issue.code for issue in issues])
        self.assertIn("PWS-D999", issues[0].message)

    def test_indented_code_hides_unknown_references_and_links(self) -> None:
        for indentation in ("    ", "\t"):
            with (
                self.subTest(indentation=repr(indentation)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{indentation}PWS-D999 PWS-9999 TM-999 [hidden](missing.md)\n")

                issues = validation_issues(root)

            self.assertEqual([], issues)

    def test_indented_comment_opener_does_not_hide_following_visible_content(self) -> None:
        for indentation in ("    ", "\t"):
            with (
                self.subTest(indentation=repr(indentation)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write(
                        f"\n{indentation}<!-- this delimiter is code\n"
                        "PWS-D999 and [visible](missing.md) remain visible.\n"
                    )

                codes = _codes(root)

            self.assertIn("DOC_DECISION_REFERENCE_UNKNOWN", codes)
            self.assertIn("DOC_LINK_TARGET_MISSING", codes)

    def test_comment_delimiters_in_inline_markup_fail_closed(self) -> None:
        forms = (
            "`<!--` PWS-D999 remains visible.\n",
            "[literal <!--](README.md) PWS-D999 remains visible.\n",
        )
        for form in forms:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{form}")

                codes = _codes(root)

            self.assertIn("DOC_MARKDOWN_VISIBILITY_INVALID", codes)

    def test_raw_html_block_openers_fail_closed(self) -> None:
        forms = (
            "<style>",
            "<textarea>",
            '<div class="fixture">',
            "</div>",
            "<!DOCTYPE fixture>",
            "<?fixture?>",
            "<![CDATA[fixture]]>",
        )
        for form in forms:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{form}\n")

                codes = _codes(root)

            self.assertIn("DOC_MARKDOWN_VISIBILITY_INVALID", codes)

    def test_raw_html_inside_hidden_markdown_content_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            with (root / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\n<!--\n<script>\n-->\n    <pre>\n```html\n<textarea>\n```\n")

            issues = validation_issues(root)

        self.assertEqual([], issues)

    def test_raw_html_cannot_wrap_authoritative_security_content(self) -> None:
        documents = (
            (
                "DECISIONS.md",
                "## PWS-D001 — Synthetic decision",
            ),
            (
                "docs/security/threat-model.md",
                "| ID | Threat scenario | Impact | Required security outcome |",
            ),
            (
                "docs/security/threat-traceability.md",
                "| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |",
            ),
            (
                "docs/security/security-test-plan.md",
                "| Test ID | Threats | Earliest owning tasks | Layer and synthetic scenario | Required oracle |",
            ),
        )
        for tag in ("pre", "script"):
            for relative_path, wrapped_start in documents:
                with (
                    self.subTest(tag=tag, relative_path=relative_path),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    _minimal_repository(root)
                    path = root / relative_path
                    _replace(path, wrapped_start, f"<{tag}>\n{wrapped_start}")
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(f"</{tag}>\n")

                    codes = _codes(root)

                self.assertIn("DOC_MARKDOWN_VISIBILITY_INVALID", codes)

    def test_hidden_or_indented_decision_headings_are_not_authoritative(self) -> None:
        forms = (
            "<!-- ## PWS-D999 — Hidden same line -->\n",
            "<!--\n## PWS-D999 — Hidden block\n-->\n",
            "    ## PWS-D999 — Four-space code\n",
            "\t## PWS-D999 — Tab code\n",
        )
        for form in forms:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _minimal_repository(root)
                with (root / "DECISIONS.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"\n{form}")
                with (root / "README.md").open("a", encoding="utf-8") as stream:
                    stream.write("\nPWS-D999 is not authoritative.\n")

                codes = _codes(root)

            self.assertIn("DOC_DECISION_REFERENCE_UNKNOWN", codes)

    def test_invalid_utf8_diagnostic_is_sorted_relative_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            (root / "docs/z-invalid.md").write_bytes(b"\xff")
            _write_text(root / "docs/a.md", "Unknown PWS-9999.\n")

            issues = validation_issues(root)
            rendered = [issue.render() for issue in issues]
            sort_keys = [(issue.path, issue.line, issue.code, issue.message) for issue in issues]

        self.assertEqual(sort_keys, sorted(sort_keys))
        self.assertTrue(any("DOC_TEXT_ENCODING_INVALID" in item for item in rendered))
        self.assertTrue(any("docs/z-invalid.md" in item for item in rendered))
        self.assertNotIn(temporary, "\n".join(rendered))

    def test_duplicate_catalogue_entry_and_missing_required_category_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            threat_model = root / "docs/security/threat-model.md"
            with threat_model.open("a", encoding="utf-8") as stream:
                stream.write(
                    "| TM-001 | A parser exploit appears twice | High | Synthetic outcome |\n"
                )
            _replace(threat_model, "A dependency is compromised", "An unrelated event occurs")

            codes = _codes(root)

        self.assertIn("THREAT_CATALOGUE_DUPLICATE", codes)
        self.assertIn("THREAT_CATEGORY_MISSING", codes)

    def test_high_impact_mapping_requires_known_decision_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            traceability = root / "docs/security/threat-traceability.md"
            _replace(
                traceability,
                "| TM-001 | PWS-D001 | PWS-0001 |",
                "| TM-001 | None | PWS-0001 |",
            )

            codes = _codes(root)

        self.assertIn("THREAT_MAPPING_INCOMPLETE", codes)

    def test_traceability_requires_one_row_for_every_catalogue_threat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            traceability = root / "docs/security/threat-traceability.md"
            row = "| TM-001 | PWS-D001 | PWS-0001 | Synthetic evidence |\n"
            _replace(traceability, row, "")
            with traceability.open("a", encoding="utf-8") as stream:
                stream.write("| TM-002 | PWS-D001 | PWS-0001 | Duplicate synthetic evidence |\n")

            codes = _codes(root)

        self.assertIn("THREAT_TRACE_MISSING", codes)
        self.assertIn("THREAT_TRACE_DUPLICATE", codes)

    def test_test_plan_requires_complete_unique_threat_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            test_plan = root / "docs/security/security-test-plan.md"
            _replace(test_plan, "TM-001, TM-002", "TM-002, TM-002")

            codes = _codes(root)

        self.assertIn("THREAT_REFERENCE_DUPLICATE", codes)
        self.assertIn("THREAT_TEST_COVERAGE_MISSING", codes)

    def test_duplicate_security_test_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            test_plan = root / "docs/security/security-test-plan.md"
            with test_plan.open("a", encoding="utf-8") as stream:
                stream.write("| ST-001 | TM-001 | PWS-0001 | Duplicate case | Stable result |\n")

            codes = _codes(root)

        self.assertIn("SECURITY_TEST_ID_DUPLICATE", codes)

    def test_canonical_tables_require_exact_heading_header_and_separator(self) -> None:
        cases = (
            (
                "docs/security/threat-model.md",
                "## Threat catalogue",
                "## Renamed catalogue",
                "THREAT_CATALOGUE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/threat-traceability.md",
                "| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |",
                "| Threat | Decisions | Tasks | Evidence |",
                "THREAT_TRACE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/security-test-plan.md",
                "|---|---|---|---|---|",
                "|---|---|---|---|--|",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
            ),
            (
                "docs/security/security-test-plan.md",
                "## Planned suites",
                "## Failure report requirements",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
            ),
        )
        for relative_path, old, new, expected_code in cases:
            with (
                self.subTest(relative_path=relative_path),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _minimal_repository(root)
                _replace(root / relative_path, old, new)

                codes = _codes(root)

            self.assertIn(expected_code, codes)

    def test_stray_trace_row_cannot_substitute_for_canonical_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            traceability = root / "docs/security/threat-traceability.md"
            row = "| TM-020 | PWS-D001 | PWS-0001 | Synthetic evidence |\n"
            _replace(traceability, row, "")
            with traceability.open("a", encoding="utf-8") as stream:
                stream.write(f"\n## Appendix\n\n{row}")

            codes = _codes(root)

        self.assertIn("THREAT_TRACE_STRUCTURE_INVALID", codes)
        self.assertIn("THREAT_TRACE_MISSING", codes)

    def test_commented_canonical_rows_are_not_authoritative(self) -> None:
        cases = (
            (
                "docs/security/threat-model.md",
                "| TM-001 | A parser exploit compromises a document engine | High | A bounded synthetic outcome |",
                "THREAT_CATALOGUE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/threat-traceability.md",
                "| TM-001 | PWS-D001 | PWS-0001 | Synthetic evidence |",
                "THREAT_TRACE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/security-test-plan.md",
                "| ST-001 | "
                + ", ".join(threat_id for threat_id, _scenario in THREAT_ROWS)
                + " | PWS-0001 | Synthetic case | Stable result |",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
            ),
        )
        for relative_path, row, expected_code in cases:
            with (
                self.subTest(relative_path=relative_path),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _minimal_repository(root)
                _replace(root / relative_path, row, f"<!-- {row} -->")

                codes = _codes(root)

            self.assertIn(expected_code, codes)

    def test_commented_canonical_headings_are_not_authoritative(self) -> None:
        documents = (
            (
                "docs/security/threat-model.md",
                "## Threat catalogue",
                "THREAT_CATALOGUE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/threat-traceability.md",
                "# Threat-to-decision and task traceability",
                "THREAT_TRACE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/security-test-plan.md",
                "## Planned suites",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
            ),
        )
        for relative_path, heading, expected_code in documents:
            with (
                self.subTest(relative_path=relative_path),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _minimal_repository(root)
                _replace(root / relative_path, heading, f"<!-- {heading} -->")

                codes = _codes(root)

            self.assertIn(expected_code, codes)

    def test_multiline_comment_cannot_hide_complete_canonical_catalogue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_repository(root)
            threat_model = root / "docs/security/threat-model.md"
            text = threat_model.read_text(encoding="utf-8")
            visible_prefix = "# Threat model\n\n"
            self.assertTrue(text.startswith(visible_prefix))
            _write_text(
                threat_model,
                f"{visible_prefix}<!--\n{text[len(visible_prefix) :]}-->\n",
            )

            codes = _codes(root)

        self.assertIn("THREAT_CATALOGUE_STRUCTURE_INVALID", codes)

    def test_indented_canonical_table_lines_are_not_authoritative(self) -> None:
        security_test_row = (
            "| ST-001 | "
            + ", ".join(threat_id for threat_id, _scenario in THREAT_ROWS)
            + " | PWS-0001 | Synthetic case | Stable result |"
        )
        documents = (
            (
                "docs/security/threat-model.md",
                "THREAT_CATALOGUE_STRUCTURE_INVALID",
                (
                    (
                        "header",
                        "| ID | Threat scenario | Impact | Required security outcome |",
                    ),
                    ("separator", "|---|---|---|---|"),
                    (
                        "data row",
                        "| TM-001 | A parser exploit compromises a document engine | High | A bounded synthetic outcome |",
                    ),
                ),
            ),
            (
                "docs/security/threat-traceability.md",
                "THREAT_TRACE_STRUCTURE_INVALID",
                (
                    (
                        "header",
                        "| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |",
                    ),
                    ("separator", "|---|---|---|---|"),
                    (
                        "data row",
                        "| TM-001 | PWS-D001 | PWS-0001 | Synthetic evidence |",
                    ),
                ),
            ),
            (
                "docs/security/security-test-plan.md",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
                (
                    (
                        "header",
                        "| Test ID | Threats | Earliest owning tasks | Layer and synthetic scenario | Required oracle |",
                    ),
                    ("separator", "|---|---|---|---|---|"),
                    ("data row", security_test_row),
                ),
            ),
        )
        for relative_path, expected_code, table_lines in documents:
            for line_kind, table_line in table_lines:
                for indentation in ("    ", "\t"):
                    with (
                        self.subTest(
                            relative_path=relative_path,
                            line_kind=line_kind,
                            indentation=repr(indentation),
                        ),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        _minimal_repository(root)
                        _replace(
                            root / relative_path,
                            table_line,
                            f"{indentation}{table_line}",
                        )

                        codes = _codes(root)

                    self.assertIn(expected_code, codes)

    def test_indented_canonical_headings_are_not_authoritative(self) -> None:
        documents = (
            (
                "docs/security/threat-model.md",
                "## Threat catalogue",
                "THREAT_CATALOGUE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/threat-traceability.md",
                "# Threat-to-decision and task traceability",
                "THREAT_TRACE_STRUCTURE_INVALID",
            ),
            (
                "docs/security/security-test-plan.md",
                "## Planned suites",
                "SECURITY_TEST_PLAN_STRUCTURE_INVALID",
            ),
        )
        for relative_path, heading, expected_code in documents:
            for indentation in ("    ", "\t"):
                with (
                    self.subTest(relative_path=relative_path, indentation=repr(indentation)),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    _minimal_repository(root)
                    _replace(root / relative_path, heading, f"{indentation}{heading}")

                    codes = _codes(root)

                self.assertIn(expected_code, codes)


if __name__ == "__main__":
    unittest.main()
