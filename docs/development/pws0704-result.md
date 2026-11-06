## Task result — PWS-0704: Implement localization framework and right-to-left preparation

**Status:** done.

### Implemented

- Versioned, bounded UTF-8 JSON catalogs for English, expanded pseudo text and
  an RTL test locale. Resource-only translations require no application-code edit.
- Primary workflow text, progress, recovery, accessible descriptions, package
  summaries and template presentation use the localization mechanism.
- Locale-aware number interpolation, dates and file-size formatting; saved
  numbers, dates, names, codes and template artifacts retain their meaning.
- Reflowing translated controls and footers, mirrored workflow action rows,
  logical keyboard order and physical document-editor coordinates.
- Offline missing-string/placeholder validation and translator documentation.

### Changed files

- `BUNDLE_MANIFEST.json`: Refresh task/doc integrity metadata.
- `README.md`: Link the locale workflow and validation command.
- `TASKS.yaml`: Record PWS-0704 completion and evidence; preserve other tasks.
- `docs/architecture/dependency-boundaries.md`: Document the narrow presentation-only catalog dependency.
- `docs/development/localization.md`: Document translation, display policy and RTL contracts.
- `docs/development/pws0704-result.md`: Record implementation, verification and evidence limits.
- `src/paperworkshell/app/organizer.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/app/signature_images.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/domain/package_summary.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/domain/pdf_export.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/domain/pdf_optimization.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/domain/redaction_export.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/export/package_summary.py`: Localize computed human-readable reports without changing machine state.
- `src/paperworkshell/localization/__init__.py`: Provide bounded data-only text resources and locale formatting.
- `src/paperworkshell/localization/catalog.py`: Provide bounded data-only text resources and locale formatting.
- `src/paperworkshell/localization/catalogs/ar-XB.json`: Provide bounded data-only text resources and locale formatting.
- `src/paperworkshell/localization/catalogs/en-XA.json`: Provide bounded data-only text resources and locale formatting.
- `src/paperworkshell/localization/catalogs/en.json`: Provide bounded data-only text resources and locale formatting.
- `src/paperworkshell/templates/loader.py`: Localize display while retaining exact template authority.
- `src/paperworkshell/templates/models.py`: Localize display while retaining exact template authority.
- `src/paperworkshell/ui/application.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/assisted_session.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/assisted_use.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/backend.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/checklist.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/compression_review.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/constraints.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/keyboard_help.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/localization_layout.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/localized_messages.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/models.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/ocr_languages.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/overlay_canvas.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/overlay_tools.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/package_export.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/presentation.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/preview_comparison.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/redaction_canvas.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/redaction_export.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/redaction_tools.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/removal_export_review.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/scan_review.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/signature_images.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/signed_pdf_confirmation.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `src/paperworkshell/ui/window.py`: Localize/reflow presentation while retaining data, actions and coordinates.
- `tests/accessibility/test_localization_layout.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/accessibility/test_pages_pdf_export_accessibility.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/integration/test_quality_entry_points.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_dev_commands.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_import_boundaries.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_localization.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_localized_presentation.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_ocr_language_window.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_redaction_tools_window.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_template_localization.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tests/unit/test_ui_localization.py`: Verify locale, layout, accessibility, safety or stable developer contracts.
- `tools/check_import_boundaries.py`: Enforce localization inventory and architecture checks.
- `tools/dev.py`: Enforce localization inventory and architecture checks.
- `tools/validate_localization.py`: Enforce localization inventory and architecture checks.

### Verification

The preceding source-bound run completed
6,900 unit passes and four failures: one highlight was described as an ordinary
shape, two tests assumed single-line visual labels, and README's bundle hash was
stale. The highlight distinction was repaired, line-reflow assertions preserve
full accessible wording, and bundle metadata was refreshed. Exact before/after
root and QA source/Git/index/ref guards all passed. See
`output/pws0704-qa-v2/test-unit-full.json`; this failed epoch is not a pass.

Final auditing added regressions for fully translated dynamic prose, fractional
numbers, source-preserving signature-placement summaries, repeated coordinate
descriptions, dense-legend layout, support codes and progress-format tokens.
The descriptions previously grew because they parsed an English delimiter after
translation; four immutable base descriptions now keep repeated updates bounded.

The initial full secret scan flagged ten SHA-256 entries in the generated
`tmp/pws0704/baseline.json` development receipt. That task-created receipt and
its status inventory were moved into ignored local evidence. No scanner rule,
ignore policy, budget or finding was suppressed. The final canonical-root scan passed in 284.98 seconds with the same rules,
budget and complete history/index/nonignored-source scope.

The first QA preparation failed because an edited implementation file retained
CRLF while its committed artifact used the repository's required LF. Normalizing
that task-touched file preserved its exact text and allowed the genuine
revision-bound corpus recorder to pass. The derived corpus baseline changes
only its implementation revision; all historical metrics, corpus and resource
facts remain identical. The root baseline was not replaced.

Fontless offscreen images were rejected as text evidence. The subsequent
synthetic screenshots admitted the installed Windows Segoe UI temporarily and
asserted real Latin and Arabic glyph coverage; no font is redistributed.
Final glyph-bearing screenshots are in `output/pws0704-visual-final/`: 15 raw
images and two contact sheets. Manifest SHA-256:
`f4a8a45bf256d4bff1446ad5ba5fffd2210ae42da70881fd652dc6f35b6980ee`.
Both sheets and the dense removal canvas were inspected. The dense RTL legend
reserves 296 pixels for 288 pixels of text, above the physical page. This is
agent-operated offscreen Qt evidence, not human NVDA or native OS-setting evidence.


Final verification on the corrected source:

| Command / review | Result |
| --- | --- |
| `python tools/dev.py format-check`, `lint`, `typecheck` | PASS; 814 Python surfaces formatted/typed. |
| `python tools/dev.py check-import-boundaries` | PASS; 14 layers / 288 source modules. |
| `python tools/dev.py validate-localization` | PASS; 2,706 messages / three complete catalogs, including syntax/code/Qt-progress-token protection. |
| `python tools/dev.py test-unit` in final isolated QA | PASS; all 6,937 cases, no selection or skips, in 922.31 seconds. |
| `python tools/dev.py test-accessibility` | PASS; all 312 cases, no selection or skips, in 45.90 seconds. |
| `python tools/dev.py test-security` with affected privacy selector | PASS; 41 passed / 979 deselected. |
| `python tools/dev.py test-integration` with lifecycle/entry-point selector | PASS; 15 passed / 587 deselected. |
| `python tools/dev.py validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary`, `validate-dependencies` | PASS; 82 tasks / 243 dependencies and matching bundle integrity. |
| `python tools/dev.py quality-self-test` | PASS; all five deliberate violations detected. |
| `python tools/dev.py scan-secrets` | PASS; full canonical-root source/history/index scan, unchanged policy and budgets. |
| Glyph-bearing pseudo/RTL layout and visual review | PASS; 62 layout cases at normal/doubled text, logical focus order, crop/rotation pointer mapping and physical keyboard movement; 15 synthetic images inspected. |

The security selector was `overlay_editor_privacy or core_overlay_tools_privacy
or scan_review_privacy or ocr_language_privacy or page_organizer_privacy or
checklist_privacy or package_summary_privacy or localization`. The integration
selector was `organizer_ui_lifecycle or quality_entry_points`. They were supplied
through `PYTEST_ADDOPTS` to the registered developer commands. These results do
not claim the entire native integration/security matrix or resolve PWS-0705's
existing qualification limitations.

The final source checkpoint is `008431018f228dcbc1355dab4258c1e62e8e1f31`, with
an evidence-only baseline checkpoint `3dbe3776e632b82cf4f537b69ea59a179427b4d6`.
These are isolated local QA commits, not root commits. The final full-unit
receipt is `output/pws0704-qa-final/test-unit-full.json`, SHA-256
`8df2a63ee969a1ea1660ae41ebf8b0eb979b60b46abbd9d0c98f158b8c4fb870`.
Every initial and final source/Git/index/ref preservation check passed. All
1,133 unrelated original files and every other task's contract, state and
evidence remain unchanged. Completion updates change only this report,
`TASKS.yaml` and `BUNDLE_MANIFEST.json`; separate closure validation and scanning
receipts are retained under `output/pws0704-checks/`.

### Security/privacy/accessibility notes

Catalogs contain product text only and use no network or executable translation
content. Validation rejects unsafe placeholders, invalid versions, duplicate
keys, introduced markup, malformed Unicode and oversized resources. Original
documents, worker isolation, processing limits, manifests and template schemas
are unchanged. Tests use synthetic widgets and data. No dependency was added.

### Portfolio boundary and release-interface notes

Standalone operation and private data ownership remain intact. No sibling
repository, service or interface is involved, and no packaged release fact is
asserted. Release-interface documents remain draft.

### Residual risks or deferrals

Natural-language translation review, human NVDA/RTL observations and packaged
native-dialog qualification remain with the M7/M8 gates. The two synthetic test
locales are not reviewed language translations. PWS-0705's existing review and
qualification limitations remain recorded with that task.

### Task graph

PWS-0704 is done. No task is newly unblocked. The next dependency-ready
continuation is PWS-0705, already in review. PWS-0706 remains planned until its
PWS-0705 dependency is done; PWS-0707 remains planned until all prerequisites pass.
