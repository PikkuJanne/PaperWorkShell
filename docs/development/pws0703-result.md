## Task result — PWS-0703: Harden scaling, contrast, target size, and reduced motion

**Status:** done

### Implemented

- Native-palette presentation policy with larger interactive targets and reduced
  motion throughout the application.
- Wrapping workflow actions, constrained editor footers, wrapped confirmations,
  and aspect-preserving scan previews using the existing bounded image.
- Contrast repairs for menu text, missing-preview guidance and removal legends.
- Regression tests for layout, focus, hit targets, motion, progress and contrast.
- A required-quality repair reuses identical immutable Git blob reads within
  each existing secret-scan batch. Every original/alias path and raw-content scan
  remains covered; limits, rules, history, index and working-tree scope are intact.

### Changed files

- `src/paperworkshell/ui/presentation.py`: shared target, menu-palette, motion,
  footer and wrapping-layout policy.
- `src/paperworkshell/ui/window.py`: install policy and reflow workflow actions.
- `src/paperworkshell/ui/overlay_tools.py`, `checklist.py`, `constraints.py`,
  `redaction_tools.py`: dense editor reflow and constrained footers.
- `src/paperworkshell/ui/ocr_languages.py`, `scan_review.py`, `signature_images.py`,
  `assisted_use.py`, `signed_pdf_confirmation.py`, `keyboard_help.py`: secondary
  dialog reflow, wrapped consent and review controls.
- `src/paperworkshell/ui/package_export.py`, `compression_review.py`: static busy
  status; package actions/format choice fit enlarged text.
- `src/paperworkshell/ui/overlay_canvas.py`, `redaction_canvas.py`: readable canvas
  guidance in dark contrast palettes.
- `tests/accessibility/test_display_preferences.py`, `test_dialog_scaling.py`,
  `test_secondary_dialog_scaling.py`; `tests/unit/test_presentation.py`: new
  rendered/geometry/behavior regressions.
- `tests/unit/test_signature_image_dialogs.py` and
  `tests/accessibility/test_signature_image_accessibility.py`: wording assertions
  follow visible wrapped labels while preserving accessible wording and radio behavior.
- `tools/dev.py`, `tests/security/test_secret_scanning.py`: avoid duplicate
  same-batch Git reads and prove unchanged path, alias, raw-content and budget
  enforcement for the required secret scan.
- `docs/accessibility/pws-0703-display-audit.md`, this report, `README.md`,
  `TASKS.yaml`, `BUNDLE_MANIFEST.json`: policy, evidence, task readiness and matching
  bundle hashes/graph summary.

### Verification

| Command / review | Result |
| --- | --- |
| `python tools/dev.py format`, `lint`, `typecheck`, `check-import-boundaries` | PASS; 804 Python surfaces, 13 layers / 284 source modules. |
| `python tools/dev.py test-accessibility` | PASS; all 250 cases, no skips or selection. |
| `python tools/dev.py test-unit` in the exact-source QA checkout | PASS; all 6,817 cases in 955.37 seconds, no skips or selection. |
| `python tools/dev.py test-integration` with `-k "organizer_ui_lifecycle or quality_entry_points"` | PASS; 15 passed, 587 deselected. |
| `python tools/dev.py test-security` with the seven privacy modules below | PASS; 41 passed, 978 deselected. |
| Complete secret-scanner security module | PASS; 52 cases / 47 subtests. After a test-only loop-binding correction, all 5 affected cases / 4 subtests passed again; production scanner unchanged. |
| `python tools/dev.py scan-secrets` | Full canonical-root PASS in 312.14 seconds; source unchanged throughout. |
| `python tools/dev.py quality-self-test` | PASS; all five deliberate failures detected. |
| `validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary`, `git diff --check` | PASS; task graph has 82 tasks / 243 dependencies. Final completion metadata is validated separately. |
| Agent-operated native Windows display review | Observed 100/150/200 percent Qt scaling; dark/light application contrast palettes, doubled text, footer/recovery reachability and static activity. Final live dark-to-light transitions retain focus and every measured main target remains at least 32 by 32 logical pixels. |

The security selector was `overlay_editor_privacy or core_overlay_tools_privacy
or scan_review_privacy or ocr_language_privacy or page_organizer_privacy or
checklist_privacy or package_summary_privacy`. Selectors were supplied through
`PYTEST_ADDOPTS`; the registered developer commands remained unchanged. These
scoped integration/privacy checks do not claim complete integration/security
lanes or resolution of PWS-0705's previously recorded qualification failures.

Full unit evidence is `output/pws0703-qa-v2/test-unit-full.json`, SHA-256
`bc8367d684664f7e51b956be17e3fafbc4d43e088d372221a7b3e5a87ebfca40`.
The genuine source checkpoint is `2d455183414797ca79bf0fa3f74a1991993b869a`;
the evidence-only checkpoint is `bfafe3b04f45d3ef340e6e5850be58f4c89a961d`.
This is a local isolated QA checkpoint, not a root commit. All five initial and
both final guards passed: source, raw index, HEAD, all 105 own refs / 6,099 reachable
objects, original baseline and unrelated working bytes were preserved. The
candidate's baseline differs only in its genuine revision binding; corpus,
metrics, profiles and resource facts are unchanged. No historical accuracy result
was invented or rerun. Completion-only metadata is checked after this frozen epoch.

The full secret scan covered 3,347 historical blobs, 330 historical commit/tag
objects, 3,393 historical path/blob pairs, 1,012 index blobs, 1,043 tracked files and
159 nonignored untracked files. Its bound receipt is
`output/pws0703/scan-secrets-repaired.json`; log SHA-256 is
`3cf51057ce08eb639517264e40732122100bfcd0ae28279a21a7c990dd1169e7`.
The exact shared batch planner removes 8,146,703 bytes of real duplicate reads
in the present history. An independent review found no path, alias, raw-content,
batch-boundary or work-budget bypass. No budget, rule, ignore, ref or source scope
was weakened.

Earlier failures remain recorded: initial small targets, overflowing footers,
invisible dark menu text, stale live palette colors, a target shrinking to
184 by 16 after a theme change, the original scanner work-limit failure, and the
first unit epoch stopped for repair. That aborted epoch is not a pass; its
before/after preservation guards passed. Fault injection independently reproduces
the target/footer, palette and post-theme target regressions before their fixes.

Native observations are cumulative, per-attempt and source-bound under
`output/pws0703/`. The final live-theme record is
`native-200-native-1-live-theme-target-final.json`; previous problem attempts remain
separate. These are agent observations of synthetic native widgets, not human
NVDA, OS-settings, packaged-build or physical-device certification. No original
or export was opened or changed by this presentation-only harness.

### Security/privacy/accessibility notes

Only presentation changes. Immutable originals, document parsers, worker network
denial, processing limits, manifests, migrations and export verification are
unchanged. Scan painting retains the existing bounded preview and adds no cache
or scaled bitmap. All native visual data are synthetic, with no backend running.
No dependency or license change. Target exceptions and exact evidence limits are
documented in the display audit.

### Portfolio boundary and release-interface notes

Standalone usefulness and private data ownership are preserved. No sibling
repository, application, service or adapter was consulted or added. No packaged
release-interface fact is asserted or changed.

### Residual risks or deferrals

Platform file pickers, actual Windows contrast themes, mixed-monitor transitions
and packaged-build qualification remain M8 release checks. Native Qt scale-factor
review and application contrast-palette tests do not certify a physical device.
PWS-0705's native review and previously recorded integration/fixture qualification
issues remain with that task; this task does not rewrite or waive their evidence.

### Task graph

PWS-0703 is done. PWS-0704 is newly ready and is the next independent ready task.
PWS-0705 remains in review; its accepted work and outstanding evidence are preserved.
