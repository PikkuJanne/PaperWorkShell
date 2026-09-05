# PWS-0408 implementation and pilot evidence

Status: **review**. Implementation and automated verification are complete; genuine
first-time-user and keyboard-only acceptance remains pending. Nothing in the automated
or visual developer evidence approves a v0.1 release.

Product implementation checkpoint: `85bb84c`. The subsequent `a73fc04` changes only
the developer pilot card, its wording assertions, and the human protocol: exact button
label, Space for checkbox selection, and waiting for summary-save confirmation.
Verification checkpoint `0b2694c` adds only the quality-evidence provenance correction,
the synthetic release-fixture correction, and their documentation described below.

## Implemented

- Revision-bound desktop package review, explicit human acknowledgement, and safe
  signed-source consent before producing unsigned derivatives.
- Accessible plain-text planned/final summaries and an optional user-selected `.txt`
  companion with no hidden diagnostics or replacement of existing files.
- Actual combined-PDF, separate-PDF, and ZIP composition under the existing constrained
  policy, preserving partial/uncertain outcomes through post-publication failures.
- Corrected immutable-source page admission and progress translation for subset exports;
  central resource limits and exact output verification remain unchanged.
- A fresh isolated synthetic [human pilot](pws0408-pilot.md), with readable visual targets,
  one launch command, and explicit resume/recovery.

## Changed files

- `src/paperworkshell/domain/package_summary.py`: bounded, content-safe presentation models.
- `src/paperworkshell/export/package_summary.py`, `package_service.py`: exact evidence
  projection, recovery inventory, actual publication composition, and companion saving.
- `src/paperworkshell/app/package_workflow.py`, `organizer.py`, `signed_source_policy.py`:
  exclusive review authority, explicit acknowledgements, atomic consent snapshot, and
  organizer/autosave reconciliation before writing outputs.
- `src/paperworkshell/preflight/engine.py`: read-only consumed-dependency fingerprint access.
- `src/paperworkshell/__main__.py`: lazy restricted-worker and package service composition.
- `src/paperworkshell/ui/package_export.py`, `backend.py`, `window.py`: keyboard-accessible
  dialog, correlated commands, destination selection, progress/cancellation, final inventory,
  companion saving, safe close and recovery.
- `src/paperworkshell/worker/pdf_export_broker.py`, `flattened_pdf_export_broker.py`,
  `searchable_pdf_export_broker.py`, `searchable_pdf_export_operation.py`: bounded source
  capacity and truthful output progress for subsets of larger originals.
- `tools/pws0408_pilot.py`, `tools/dev.py`: developer-only isolated human pilot entry point.
- `benchmarks/pws0207-quality-findings-v1.json`: analytical evidence re-bound to the
  changed composition/organizer/UI revision, with every metric and profile/corpus hash
  preserved; no golden rendering or scoring expectation changes.
- `tools/check_import_boundaries.py` and `tests/unit/test_import_boundaries.py`: exact
  composition-root exceptions with application/UI/descendant rejection tests.
- `tests/unit/test_validate_portfolio_boundary.py`: case-insensitive placeholder
  completion in the synthetic final-release fixture, retaining the strict real gate.
- New tests: `tests/helpers/package_pilot.py`; `tests/unit/test_package_workflow.py`,
  `test_package_summary.py`, `test_package_export_composition.py`,
  `test_package_export_backend_bridge.py`, `test_package_export_window.py`,
  `test_package_export_dialog.py`, `test_pws0408_pilot.py`, and
  `tests/unit/worker/test_package_source_page_budgets.py`;
  `tests/integration/test_package_pilot.py`, `test_package_summary_destination_windows.py`;
  `tests/security/test_package_summary_privacy.py`;
  `tests/accessibility/test_package_export_accessibility.py`.
- `tests/unit/test_preflight_engine.py`, `test_signed_source_policy.py`,
  `tests/unit/worker/test_searchable_pdf_export_operation.py`, and
  `tests/integration/test_desktop_startup_recovery_windows.py`: regression coverage and
  updated composition test doubles.
- `README.md`, `docs/architecture/package-summary.md`, `dependency-boundaries.md`,
  `docs/architecture/quality-review.md`,
  `docs/development/pws0408-pilot.md`, this report, `docs/release/RELEASE_INTERFACE.md`,
  `TASKS.yaml`, and `BUNDLE_MANIFEST.json`: contracts, human protocol, truthful release
  context, task evidence, and planning-bundle integrity.

## Verification

The checks listed here have actually passed; they are not placeholders for future execution.
The separate first-time-user and keyboard-only human result is **not recorded**.

Commands below use `uv run python -m tools.dev <command>` unless shown explicitly.
The observed test environment is Windows x64, version `10.0.26200`, build `26200`.

- `format-check`, `lint`, `typecheck`: pass; 589 Python files formatted and strictly typed.
- `check-import-boundaries`: pass; 13 layers and 207 source files.
- `test-unit`: the full retry passed all 4,633 tests in 705.46 seconds; no failures
  or skips reported. The initial failed run and its evidence/test-fixture corrections
  are recorded below rather than omitted.
- `test-integration`: the full suite completed with pytest exit code 0; 481 tests were
  collected. Completion was independently confirmed through retained process handles.
  The final per-status stdout totals were not retained, so no exact pass/skip breakdown
  is asserted for this run. Final UI cancellation changes were separately covered by the
  focused workflow/bridge/window/dialog tests and the full accessibility suite.
- `test-accessibility`: 67 passed, including the final cancellation and enlarged-text UI.
- `test-security`: 572 passed and six skipped in 250.06 seconds. Skips concern unavailable
  Windows symlink/reparse fixture privileges in case-workspace, export-history, qpdf
  configuration, startup-recovery, and two synthetic-fixture cases; junction coverage ran.
- `validate-schemas`: all nine registered validation groups passed.
- `validate-task-graph`: 82 tasks, 243 dependencies, nine milestones, 42 decisions.
- `validate-docs`, `validate-portfolio-boundary`: pass; 14 canonical bundle files valid.
- `validate-dependencies`: one complete and four rejected examples validated.
- `validate-m1-benchmark-report`, `validate-pws0208-resource-workload`,
  `validate-pdf-renderer-benchmark`: pass. These validate existing evidence/workload
  definitions, not new performance measurements or hardware certification.
- `quality-self-test`: all five deliberate format, type, schema, graph, and staged-binary
  secret violations detected; synthetic negative-test data cleaned up.
- `validate-fixtures`: pass in a clean full-history local clone at `0b2694c`; 46 synthetic
  fixtures, 15 renderer, six transform, and three overlay references (237,165 corpus bytes),
  2,532 historical blobs, 837 index blobs, and 867 working files.
- `scan-secrets`: pass in that same clean source snapshot; 2,532 historical blobs,
  275 commit/tag objects, 2,567 path/blob pairs, 837 index blobs, 867 tracked working files,
  and no nonignored untracked files. Maintainer-owned untracked files in the main working
  directory stayed untouched and outside this source-verification scope.
- `uv run pytest tests/integration/test_package_pilot.py -q`: one live production-worker
  pilot passed in 276.87 seconds. Combined and separate first-page renders were inspected
  and pixel-identical for the synthetic source.
- `uv run --locked --no-sync pytest tests/unit/test_pws0408_pilot.py -q`: 13 passed after
  the instruction clarification. `uv run python -m tools.pws0408_pilot --prepare-only`
  produced a fresh isolated attempt without launching the GUI or recording a human pass.
- `uv run --locked --no-sync python tools/pws0207_quality_evaluation.py evaluate` and
  `uv run --locked --no-sync python tools/pws0207_quality_evaluation.py validate benchmarks/pws0207-quality-findings-v1.json --revision a73fc04d9a06df5e6704e11d4cddb7ffe60266a8`:
  pass; the exact unchanged corpus/profile metrics bind to the committed implementation.
- `git diff --check`: pass.

### Quality-evidence provenance correction

The first full unit run finished with `5 failed, 4632 passed in 544.21s`: one failed
baseline test and four failed subtests (4,633 test items were collected). It identified
the existing PWS-0207 baseline's obsolete
implementation binding. Its recorded PWS-0406 revision no longer matches four of the
27 deliberately bound files: desktop composition, organizer, backend bridge, and window.
PWS-0408 adds exclusive package review, queued package actions, lazy worker composition,
and consent/autosave reconciliation there. Independent inspection confirmed the other
23 bound files are byte-identical, including the quality algorithms, corpus/evaluator,
profile/resource policy, OCR-review machinery, protocol, and isolation.

The documented evaluator was rerun, then its no-replace recorder generated a separate
candidate for `a73fc04`. Validation passed. Comparing the complete old and generated
payloads proved the implementation revision is the **only** changed field; all eight
metric rows and corpus/profile hashes remain exact. This is a reviewed provenance update
for changed integration surfaces, not a changed regression oracle or relaxed validator.
The historical baseline remains available in Git. The canonical baseline was updated
through an exact revision-only patch, validated successfully, and its previously failing
unit test passed in 2.64 seconds. The complete unit retry then passed all 4,633 tests.

The four remaining failures came from the synthetic final-release fixture: it replaced
uppercase `TBD` and title-case `Pending` but left the new lowercase human-acceptance
`pending` table value unresolved. The production gate correctly rejects that value.
Only fixture completion was made case-insensitive, with explicit assertions that
the real gate continues rejecting lower/mixed-case structured placeholders. The release
sheet remains truthful and draft; no production gate or pending human check is bypassed.
The focused portfolio file passed 31 tests and 52 subtests, including the four new
lower/mixed-case rejection observations. Formatting, lint, strict typing, and documentation
validation passed again after these evidence/test-only corrections.

### Acceptance evidence boundaries

- Concrete summary inventory and truthful review: summary/workflow unit tests cover
  planned versus saved names/counts/bytes/pages, category assignments, incomplete
  checklists, unavailable checks, warning-heavy cases, and required human statements.
- Production export path: `tests/integration/test_package_pilot.py` uses the ordinary
  Windows backend and restricted workers under `CONSTRAINED_RESOURCE_POLICY_V1`.
  It exercises missing-to-complete assignments, explicit review refusal, cancellation,
  all three export modes, actual reopen/render verification, exact UTF-8 companions,
  occupied-destination refusal, source/history preservation, and cleanup.
- Content safety: `tests/security/test_package_summary_privacy.py` checks prohibited
  positive guarantees and hidden diagnostic/fixture tokens, including failure paths.
- Widget accessibility: `tests/accessibility/test_package_export_accessibility.py`
  checks names, focus, keyboard operation, wrapping/enlargement, and announcements.
  Developer screenshots were also inspected; neither establishes human comprehension.
- **Still required:** a genuinely first-time operator completes the documented eight
  steps using the keyboard and PaperWorkShell alone, and explains planned/saved files,
  unknown checks, remaining review, and the absence of submission/signature/redaction
  guarantees. Preparation or silence from an operator is not a pass.

## Security, privacy, and accessibility

Originals are retained unchanged. Production parsing remains in restricted workers, with
unchanged network denial, central caps, sequential admission, cancellation, and cleanup.
Output names/categories are explicit user-visible content; private source names, paths,
identifiers, hashes, OCR text, signature images, and raw exceptions are excluded from
summaries and routine representations. No new dependency, schema, migration, or font asset
is introduced. Source inspection, page details, and quality facts not gathered for this
review remain explicitly unverified.

Normal review and enlarged final-summary screenshots were visually inspected using
synthetic state. The four large-labelled human-input pages were independently rendered
and inspected. These are developer visual checks, not human accessibility acceptance,
NVDA evidence, PDF/UA conformance, or hardware certification.

## Portfolio and release interface

PaperWorkShell remains standalone. User data remains owned by its own case and explicit
local derivatives. No sibling dependency, shared interface, network service, or ThirdLife
adapter is introduced. The release sheet records source-tree summary functionality only;
packaged facts remain TBD. The pilot launcher is not an application CLI contract.

## Residual risks and decisions

- First-time-user and keyboard-only walkthrough acceptance is pending. A passing human
  result closes this remaining evidence; a defect requires correction and focused retest.
- Source/quality analysis is not rerun by this review; unknowns stay visible. The broader
  PWS-0409 gate must assess these clearly disclosed limits with the complete pilot matrix.
- Final results are transient; save the text summary before closing when it is needed.
  Durable package history would require an explicit versioned schema/migration decision.
- Mixed scan/image processing and overlays in one output retain the PWS-0407 rejection.
  The optional technical manifest remains unimplemented; no machine-readable contract is
  being frozen through this task.
- Acceptance of PWS-0408 makes PWS-0409 eligible; it does **not** approve a v0.1 release.

## Task graph and next step

PWS-0401 through PWS-0407 remain done. PWS-0408 is **review**, with `completed_at` unset
because the human evidence is missing. No new implementation task is dependency-ready;
PWS-0409 remains planned until PWS-0408 is accepted. Run the [human pilot](pws0408-pilot.md),
record steps 1–8 and the first-time/keyboard-only conditions, then accept the result or
address any specific defect. The separate PWS-0409 integrated gate follows afterward.
