# Task result — PWS-0602: Implement redaction-mark intent and distinct review UI

**Status:** done — implementation, final full suites and native NVDA review passed.

Date: 2026-10-16. Branch: `codex/pws-0602-redaction-intent`, based on
`868e0ef53a5743514f2ef1cf72f8399901e72326` with the maintainer's existing uncommitted
M5 closure and completed PWS-0601 corpus preserved. Unrelated untracked local documents and
`tmp/` are outside this change. Local implementation commit
`946ba43deca06fdb7f7253bf40671e4aae00174f` contains only PWS-0602 source, new schema/fixtures
and focused tests. It permits the existing exact-commit/index quality evidence guard to run;
earlier uncommitted work remains separate. Nothing is published.

The later local checkpoint `c3a2958d10f6751be3ca6fa7bd2d919242c180be` contains the bounded
journal/preview fixes, current-version test helpers and schema documentation in 44 audited
PWS-0602 paths. Shared predecessor/task metadata and every inherited artifact remain separate.
The unchanged scan-quality baseline remains bound to `946ba43`, whose 27 measured source
surfaces are unchanged by this checkpoint.
The final recovery checkpoint `1ff238be9173a5694af33e8901129808c576802f` changes only the
new editor and its accessibility tests: edit/mark limits preserve Save, Undo and review without
misreporting valid geometry or suggesting Cancel after saving has started.
The test-only checkpoint `c9e30b0` uses the public export-plan constructors in the native
original-source lease regression; the corrected persistence test passed.
Checkpoint `b30c109` exposes status before dialog-root announcements and adds the real parented
manual review context and regression tests. The 27 measured scan-quality surfaces still match
`946ba43` exactly.

## Implemented

- Separate inert removal marks in manifest v3, with frozen v1/v2 schemas retained unchanged.
  Complete validated upgrades add an empty collection and preserve original source identity,
  transforms, ordinary overlays, assignments, constraints, confirmations and export history.
- A bounded, revision/page/session-bound draft and organizer-owned atomic save/reopen path.
  Private primitive opening facts reject stale, cross-page and reflected presentation changes.
  Cancel before saving changes no case; failed/uncertain saving retains the exact draft for retry.
- A bounded delta journal replaces whole-case history snapshots. Supported-limit selection,
  editing, undo and redo validate in O(marks + edits), including authenticated redo history.
- A distinct **Permanently remove information…** action (`Ctrl+Shift+P`), native region list,
  full-page numeric geometry, undo/redo, optional pointer selection and **Save unapplied marks**.
  All actual marks remain visibly and programmatically unapplied; information is still present.
- Crop/quarter-turn mapping, outside-crop list editing, a deskew schematic with unsafe pointer
  mapping suppressed, fixed Save/Cancel controls, screen-fit initial sizing, logical focus and
  content-free status announcements. Rasterization, interactivity, accessibility loss and unsigned
  derivative warnings appear before saving.
- Bounded display projection and retained native list rows meet the existing UI resource budget.
  Dense previews explicitly show only the selected region's visible part, keeping every region
  in the list and preventing overlapping unapplied outlines from looking like a removed page.
- Closed unapplied/applied/verified/failed vocabulary. Only unapplied intent is serializable;
  every pipeline-state transition is rejected. Ordinary overlays cannot convert into verified
  removal. Actual reconstruction, OCR exclusion, sanitization, verification and release approval
  remain with PWS-0603 through PWS-0608.
- Active marked pages block ordinary/searchable/flattened PDF, package and compression routes.
  Checks also protect prepared export and authenticated original-source lease boundaries from
  directly authored plans. Inactive pages retain marks for restoration without blocking export
  of other pages.

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| Required action name | Separate organizer button, shortcut, dialog/accessibility title and window tests |
| Unapplied distinct from verified removal | Dashed crossed outline, UNAPPLIED label, native list/status descriptions, schema-only unapplied state, blocked promotion matrix |
| Ordinary shape cannot become verified removal | Separate collections/types, unchanged overlay payload semantics, no conversion path, schema/promotion tests |
| Keyboard numeric/list alternatives | Native roles, field editing, row selection, add/update/remove/undo/redo/save/cancel tests, logical tab cycle and recovery focus |
| Derivative/signature/interactivity/accessibility warnings | Fixed visible and accessible text with wording assertions |
| State/terminology/schema verification | Focused domain, schema, migration, application, export, UI and accessibility tests |
| Screen-reader status review | Native NVDA 2026.1.1 passed at effective DPR 2 through the real parented workflow: geometry, status, Add/Undo/Redo, progress, failure/retry, save and cancellation |

## Changed files

Production domain and schema:

- `src/paperworkshell/domain/redaction.py`: inert marks, closed status/error vocabulary, bounds,
  graph identity checks and export refusal.
- `schemas/case-manifest-v3.schema.json`, `src/paperworkshell/storage/case-manifest-v3.schema.json`,
  `schemas/examples/case-manifest-v3-minimal.json`,
  `schemas/examples/case-manifest-v3-representative.json`: current contract and synthetic examples.
- `src/paperworkshell/domain/manifest_repository.py`, `manifest_migrations.py`,
  `manifest_validation.py`, `case_lifecycle.py`: version dispatch, lossless upgrade, validation.
- `src/paperworkshell/domain/page_operations.py`, `checklist.py`,
  `src/paperworkshell/overlays/commands.py`, `signature_commands.py`: retain marks through edits
  and reserve their IDs across case namespaces.

Application, storage, export and UI:

- `src/paperworkshell/app/redaction_tools.py`, `organizer.py`: bounded draft and authenticated
  exclusive editor/save/cancel/retry lifecycle.
- `src/paperworkshell/storage/case_lifecycle.py`, `manifest_codec.py`, `manifest_repository.py`,
  `source_ingest.py`: current manifests, atomic historical upgrade, ID reservation and retained
  source-boundary export refusal.
- `src/paperworkshell/domain/pdf_export.py`, `searchable_pdf_export.py`,
  `src/paperworkshell/overlays/exporting.py`, `src/paperworkshell/app/pdf_export.py`,
  `searchable_pdf_export.py`, `package_workflow.py`, `src/paperworkshell/export/package_export.py`:
  fail-closed normal/alternate/prepared publication paths.
- `src/paperworkshell/ui/redaction_tools.py`, `redaction_canvas.py`, `backend.py`, `window.py`:
  distinct accessible review, safe projection, worker plumbing and exact asynchronous correlation.

Tests, validation and documentation:

- New `tests/unit/test_redaction_models.py`, `test_redaction_tools.py`,
  `test_redaction_tools_window.py`; `tests/accessibility/test_redaction_tools_accessibility.py`;
  `tests/security/test_redaction_intent_security.py`;
  `tests/integration/test_redaction_intent_persistence_windows.py`;
  `tests/helpers/redaction_editor_manual_probe.py`; `tests/unit/test_redaction_tools_resources.py`,
  `test_redaction_journal_contracts.py`, `test_redaction_presentation_resources.py`; and
  `tests/fixtures/manifests/v3/`.
- Existing manifest/schema/lifecycle/organizer/checklist tests and current application fixture
  helpers updated for current v3; historical v1/v2/future-version checks remain explicit.
- `tools/validate_manifests.py`, `validate_schemas.py`: current and historical contract registration.
- `README.md`, `schemas/README.md`, `docs/schemas/case-manifest-v2.md`,
  `docs/schemas/case-manifest-v3.md`, `docs/architecture/manifest-repository.md`,
  `docs/architecture/overlay-domain-model.md`, `docs/architecture/redaction-intent-review.md`,
  this report, `TASKS.yaml` and
  `BUNDLE_MANIFEST.json`: workflow, migration, evidence and task state.
- `benchmarks/pws0207-quality-findings-v1.json`,
  `docs/development/scan-quality-evaluation.md`: unchanged quality measurements re-evaluated and
  bound to the committed organizer/UI implementation; only the baseline revision field changed.

## Verification

Final verification passed on the settled production source at `b30c109`. The registered full
unit, integration, security and accessibility lanes completed without failures:

| Command | Result |
|---|---|
| `tools/dev.py format-check` | Pass; 682 files formatted |
| `tools/dev.py lint` | Pass |
| `tools/dev.py typecheck` | Pass; 682 files under strict mypy |
| `tools/dev.py check-import-boundaries` | Pass; 13 layers, 233 source files |
| `tools/dev.py test-accessibility` | Pass; 101 tests, including 27 region-review tests |
| `tools/dev.py test-unit` | Pass; 5698 tests, zero skips, 1013.09 seconds |
| `tools/dev.py test-security` | Pass on final frozen source; 748 passed, six existing Windows link-capability skips, 341.64 seconds |
| `tools/dev.py test-integration` | Pass; all 562 collected, 552 passed, ten accepted skips, 3671.90 seconds; complete owned-checkout run with no selectors |
| `tools/dev.py validate-schemas` | Pass; frozen v1/v2, current v3 and all other registered contracts |
| `tools/dev.py validate-task-graph` | Pass; 82 tasks, 243 edges, nine milestones, 42 decisions |
| `tools/dev.py validate-docs` | Pass |
| `tools/dev.py validate-portfolio-boundary` | Pass; 43 canonical bundle files |
| `tools/dev.py validate-dependencies` | Pass |
| `tools/dev.py validate-pillow-runtime` | Pass; locked runtime and eight native members authenticated |
| `tools/dev.py validate-m1-benchmark-report` | Pass |
| `tools/dev.py validate-pws0208-resource-workload` | Pass |
| `tools/dev.py validate-pdf-renderer-benchmark` | Pass |
| `tools/dev.py quality-self-test` | Pass; all five deliberate violations rejected |
| `tools/dev.py validate-fixtures` | Pass; exact 1026-file accepted snapshot and selected-branch history |
| `tools/dev.py scan-secrets` | Pass; exact working/index bytes, selected-branch objects, messages and path bytes |

Routine checks used the locked repository `.venv`; complete integration used a separate
copy-owned CPython 3.14.6 runtime and fresh offline locked environment containing every product
dependency plus the declared test and validation groups. The registered dispatcher ran
`uv run --locked --no-sync pytest tests/integration`, with no selectors or policy changes.
The current `.venv`, qpdf and optional OCR/private-stage availability were preserved. Focused final
journal, projection, keyboard, window and accessibility tests passed (76 tests), with additional
independent 68-test and projection-oracle review. Post-journal-fix intent security tests pass.
Earlier focused storage tests exercised capacity exhaustion, recovery, stale snapshots and
source-lease refusal. Fixture and secret scanning use the exact accepted-file snapshot and
full selected-branch history, excluding the inherited unrelated local document and `tmp/`
without weakening either scanner. The final 1026-path delivery inventory records each file's
size and SHA-256 in `output/pws0602/snapshot-delivery-files.json`; it intentionally is not
hashed inside this report, which is itself an inventory member.

The initial full unit attempt collected 5644 tests: 5444 passed and 200 failed. Failures exposed
stale current-v2/future-v3 test helpers, the correctly enforced exact-commit quality baseline,
and evolving bundle hashes. Helpers now use current v3/future v4, quality measurements were
re-evaluated and bound without changing a score, and bundle metadata was refreshed. The clean
5648-test interim rerun included four added regressions. The final 5698-test pass adds 50
more delta-journal, supported-limit, presentation and review-context regressions.

Independent review reproduced and fixed the following defects with regressions:

- Invalid pending fields could leave the list highlighting a different mark from the edited one.
- Focused numeric text could be dropped during shutdown before spin-box interpretation.
- A direct authored plan could reach the original-source lease without the plan-builder guard.
- A reflected malformed mark could escape through a lower-level coordinate exception.
- Whole-case history snapshots caused 10–12 second transitions at the supported limits.
- Repeated list scans, per-region mapping and labels exceeded the UI responsiveness budget.
- Overlapping dense outlines looked like a solid black page; the disclosed selected-region
  preview now preserves visible source content and the complete native list.
- At the history limit, a valid pending numeric change could block Save and Undo and be
  misreported as invalid geometry. Mutations now stop at that limit, accepted intent remains
  savable, Undo reopens editing capacity, and failed-save recovery never offers Cancel.
- Status is now exposed before announcing from the visible native dialog root, preserving
  keyboard focus. The standalone manual helper's parentless application-modal setup did not
  reproduce production notification behavior; the repeatable helper now uses the real parent
  window and `open()` path.

## Resource and visual evidence

The journal retains at most 256 edit deltas, one opening collection and one current collection.
At 4096 marks, measured transitions fell to 32–77 ms. Traced journal allocation fell from
8,414,036 bytes to 60,560 bytes; this measures journal allocation, not whole-process memory.
Deterministic tests limit mark validations to marks + twice the retained edits + four, rather
than relying on timing assertions.

Final synthetic offscreen UI heartbeat gaps were 124 ms at DPR 1, 148 ms at DPR 2, and 145 ms
at DPR 2 with doubled text, below the unchanged constrained-v1 250 ms budget. All 4096 list
entries remain available. Independent affine projection checks covered 192 scenarios and 704
rectangles across source/manifest rotations, crops, DPR and fonts; maximum display-coordinate
error was below 3e-13 logical pixels. Persistence and pointer inverse mapping remain in the
existing domain model. These results do not certify a CPU, RAM amount, GPU or physical device.

Ignored, synthetic-only evidence is retained in `benchmark-results/pws0602/`:
`state_probe_before.json`, `state_probe_after.json`, `ui-budget-dpr1.json`,
`ui-budget-dpr2.json`, `ui-budget-dpr2-font2.json`, and `projection_oracle_review.json`.
The small normal/cropped/rotated and dense preview PNGs were inspected. No golden was refreshed.

## Security/privacy/accessibility notes

No document parsing, rasterization, worker RPC, third-party dependency, policy-budget increase,
golden refresh or source rewrite is introduced. Marks store geometry and internal IDs, never
copied source text or a removal reason. Routine error/status representations exclude document
content, filenames, paths, values and IDs. Existing bounded previews are shared and no page
packet is materialized. Storage failure/recovery tests exercise capacity exhaustion and retry.

The synthetic probe constructs only repository synthetic case facts and in-memory page pixels.
Its first Save deliberately simulates a failure; Retry acknowledges only intent and writes
nothing. The effective DPR was 2.00. An initial oversized window was corrected to fit available
screen geometry, keeping fixed Save/Cancel controls visible. Automation did not choose NVDA's
first-run privacy preference. After that request was no longer present, actual NVDA 2026.1.1
Speech Viewer review confirmed spoken region geometry, unapplied/source-present wording, all
four numeric names and units, logical keyboard traversal, and the intent-only Save warning.

The final native review used the real `PaperWorkShellWindow`, production parented dialog and
correlated in-memory signal replies. NVDA spoke Add, Undo, Redo, save progress, the sanitized
failure, and retry advice. The failed save retained both synthetic regions, disabled Cancel,
and allowed keyboard Retry. Successful Retry spoke “Redaction marks saved. No information has
been removed.” Reopening retained both regions; Escape spoke cancellation and returned focus
to the removal-intent action. The parentless application-modal helper had suppressed automatic
notifications; the real parented `open()` route passed, without changing production modality
or exclusivity. The canonical helper now uses that exact context with a large synthetic preview.

Synthetic-only speech evidence is retained in `benchmark-results/pws0602/`:
`nvda-geometry.png`, `nvda-final-parented-add-undo-redo.png`,
`nvda-final-parented-failure.png`, `nvda-final-parented-save-completion.png`, and
`nvda-final-parented-cancel.png`. This is an agent-operated native screen-reader check, not a
human assessment or a claim of PDF/UA conformance.

The initial integration failures reached a source-admission guard before import, cancellation,
preview or editing. Exactly one preexisting source file, `src/paperworkshell/pdf/portal_policy.py`,
is owned by another account and does not grant the current account the `WRITE_DAC` access
required for reversible worker grants. Source bytes, ACLs and ownership were preserved. The
CPython virtual-environment hint probe passed; worker verification uses the same accepted
source bytes in an isolated owned checkout, retaining all AppContainer and resource checks.

The complete final integration run passed against all 1026 frozen files; their raw hashes
were unchanged afterward. Its ten skips exactly match the earlier five absent OCR-environment,
two private-artifact-stage and three Windows symlink-capability cases. The isolated runtime
contained 6453 base/venv objects (373,534,333 bytes); active worker leases covered 6712 objects
including source, below the unchanged resource limits. This is test setup evidence, not a
product or hardware performance claim. Authenticated journal/active/repaired counts and
runtime process counts were zero before removing only the exact owned runtime stage.

Complete transcripts and cleanup records are retained in `benchmark-results/pws0602/`:
`final-unit.log`, `clean-integration.log`, `clean-integration-result.json`,
`clean-integration-provenance.json`, `clean-integration-cleanup.json`,
`final-security.log`, and the corresponding unit/security cleanup JSON. The earlier full
integration's six failures were resolved by the test-only public-plan constructor correction
and owned-source validation. Five unchanged startup regressions also passed independently
in 2773.84 seconds before the clean full lane. The earlier full security run preceded the
delta-journal changes; the final complete 748-pass run verifies the frozen implementation.

## Portfolio boundary and release-interface notes

The workflow is independently useful within PaperWorkShell. Private case marks and migrations
remain PaperWorkShell-owned. No sibling repository, runtime, service, adapter, shared user-content
schema or synchronized release dependency is introduced. Draft packaged-release facts remain
unproved; no release-interface capability or ThirdLife compatibility claim is added.

## Residual risks or deferrals

Actual removal and any applied/verified derivative remain disabled pending PWS-0603 through
PWS-0608 and independent release approval. Native
launch from the original working tree remains subject to the preexisting source ownership
mismatch; an owned checkout is used for verification without changing that file's metadata.

## Task graph

- PWS-0602 is `done`; final registered full suites, snapshot scans and native NVDA review passed.
- PWS-0603 is `ready`: Implement full-page sanitized raster reconstruction. It is the only newly
  dependency-ready task and the next implementation task on this path.
- Permanent redaction remains disabled.

## Complete task path inventory

The 85 task-owned paths below plus the three shared metadata paths make 88. Changes to
`README.md`, `TASKS.yaml` and `BUNDLE_MANIFEST.json` cover PWS-0602 workflow documentation and task evidence;
their inherited maintainer content remains separate. Other inherited dirty/untracked paths
are not attributed to this task. Grouped purposes are explained above.

```text
BUNDLE_MANIFEST.json
README.md
TASKS.yaml
benchmarks/pws0207-quality-findings-v1.json
docs/architecture/manifest-repository.md
docs/architecture/overlay-domain-model.md
docs/architecture/redaction-intent-review.md
docs/development/pws0602-result.md
docs/development/scan-quality-evaluation.md
docs/schemas/case-manifest-v2.md
docs/schemas/case-manifest-v3.md
schemas/README.md
schemas/case-manifest-v3.schema.json
schemas/examples/case-manifest-v3-minimal.json
schemas/examples/case-manifest-v3-representative.json
src/paperworkshell/app/organizer.py
src/paperworkshell/app/package_workflow.py
src/paperworkshell/app/pdf_export.py
src/paperworkshell/app/redaction_tools.py
src/paperworkshell/app/searchable_pdf_export.py
src/paperworkshell/domain/case_lifecycle.py
src/paperworkshell/domain/checklist.py
src/paperworkshell/domain/manifest_migrations.py
src/paperworkshell/domain/manifest_repository.py
src/paperworkshell/domain/manifest_validation.py
src/paperworkshell/domain/page_operations.py
src/paperworkshell/domain/pdf_export.py
src/paperworkshell/domain/redaction.py
src/paperworkshell/domain/searchable_pdf_export.py
src/paperworkshell/export/package_export.py
src/paperworkshell/overlays/commands.py
src/paperworkshell/overlays/exporting.py
src/paperworkshell/overlays/signature_commands.py
src/paperworkshell/storage/case-manifest-v3.schema.json
src/paperworkshell/storage/case_lifecycle.py
src/paperworkshell/storage/manifest_codec.py
src/paperworkshell/storage/manifest_repository.py
src/paperworkshell/storage/source_ingest.py
src/paperworkshell/ui/backend.py
src/paperworkshell/ui/redaction_canvas.py
src/paperworkshell/ui/redaction_tools.py
src/paperworkshell/ui/window.py
tests/accessibility/test_constraint_editor_accessibility.py
tests/accessibility/test_redaction_tools_accessibility.py
tests/fixtures/manifests/v1/invalid/future-version.json
tests/fixtures/manifests/v2/invalid/future-version.json
tests/fixtures/manifests/v3/invalid/forged-verified-state.json
tests/fixtures/manifests/v3/invalid/future-version.json
tests/fixtures/manifests/v3/invalid/missing-marks.json
tests/fixtures/manifests/v3/invalid/ordinary-shape-promotion.json
tests/fixtures/manifests/v3/invalid/out-of-page-box.json
tests/fixtures/manifests/v3/invalid/unknown-page.json
tests/helpers/package_pilot.py
tests/helpers/redaction_editor_manual_probe.py
tests/integration/test_case_lifecycle_windows.py
tests/integration/test_manifest_repository_windows.py
tests/integration/test_manifest_v2_migration_windows.py
tests/integration/test_redaction_intent_persistence_windows.py
tests/security/test_manifest_repository_security.py
tests/security/test_redaction_intent_security.py
tests/unit/test_case_lifecycle_domain.py
tests/unit/test_case_lifecycle_storage.py
tests/unit/test_checklist.py
tests/unit/test_manifest_repository_service.py
tests/unit/test_manifest_repository_storage.py
tests/unit/test_manifest_v2_validation.py
tests/unit/test_manifest_validation.py
tests/unit/test_organizer_backend.py
tests/unit/test_overlay_models.py
tests/unit/test_package_pdf_export.py
tests/unit/test_package_planner.py
tests/unit/test_package_summary.py
tests/unit/test_portal_constraints_app.py
tests/unit/test_portal_constraints_backend_bridge.py
tests/unit/test_portal_constraints_window.py
tests/unit/test_redaction_journal_contracts.py
tests/unit/test_redaction_models.py
tests/unit/test_redaction_presentation_resources.py
tests/unit/test_redaction_tools.py
tests/unit/test_redaction_tools_resources.py
tests/unit/test_redaction_tools_window.py
tests/unit/test_searchable_pdf_export.py
tests/unit/test_searchable_pdf_export_service.py
tests/unit/test_source_ingest_storage.py
tests/unit/test_validate_manifests.py
tests/unit/test_validate_schemas.py
tools/validate_manifests.py
tools/validate_schemas.py
```
