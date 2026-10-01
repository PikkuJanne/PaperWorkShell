# Task result — PWS-0505: compression preview, grayscale option, and legibility floor

**Status:** done.

Verified at `009380f57b51818b3f15907fcaa4e540fb5d21c8` by
[Windows CI run 36904211692](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36904211692):
5,475 unit, 706 security, 74 accessibility and 549 integration tests passed. Seven
existing integration tests require unstaged OCR runtimes or private workload artifacts;
no PWS-0505 test skipped. Full local verification and the keyboard/scaling visual review
also passed. The final closure changes task state, this report, ADR status and bundle
hashes only; runtime code and tests remain exactly those verified by CI.

## Scope and plan

Selected PWS-0505 from the completed PWS-0504 branch at `136270e` and created
`codex/pws-0505-compression-preview`. Dependencies PWS-0503, PWS-0504 and PWS-0203
are done. Unrelated untracked maintainer files remain untouched.

Prepare and verify a package privately, then show actual before/proposed sizes,
affected pages, resolution/color/quality changes, and real rendered page comparisons.
Publish only the exact reviewed candidate after explicit confirmation. Grayscale is
opt-in; preserve the original v1 color policy. Keep the documented 200 dpi/JPEG80
engineering floor, with no bypass below it and no claim of guaranteed readability.

Review has no timeout. Preparation, individual preview renders and publication each
have bounded processing, cancellation, resource accounting and deterministic cleanup.
Mode, color choice, destination or saved-case changes invalidate previous approval.
Resolve signed-source consent before preparing candidate evidence.

## Verification plan

- Policy tests: unchanged defaults, explicit grayscale, fixed floor, achievable,
  borderline and unsafe targets, invalid and future-version data.
- Private package tests: exact selected-byte binding, ZIP member previews, stale
  authority, destination collisions, cancellation, timeout, low disk and cleanup.
- Worker tests: actual grayscale and preserved vector/text, real candidate rendering,
  bounded memory/temporary storage and unchanged original hashes.
- UI/accessibility tests: keyboard controls, announcements, explicit confirmation,
  stale preview generations, cancellation/recovery and reachable controls at 200%.
- Synthetic visual review and all required repository quality commands.

## Security/privacy/accessibility notes

Native PDF/image parsing remains in restricted workers. Only authenticated bounded
pixels reach widgets. Routine representations exclude document content and paths.
No new dependency or license obligation was added; no manifest migration is needed.

## Portfolio boundary and release-interface notes

This is PaperWorkShell's own local export workflow. Case data stays product-owned,
with no sibling repository, service or adapter. No release-interface claim changes;
unproved packaged-release facts remain `TBD`.

## Task graph

PWS-0505 is done. PWS-0506 is dependency-ready and marked ready: define the conservative
portal profile and compatibility matrix.

## Implementation and focused verification

- Added private prepare/preview/commit lifetimes, including retained ZIP members and
  exact staged-byte publication. The workflow binds saved revision, source/plan hashes,
  destination, policy, successful page preview and explicit review confirmation.
- Added explicit grayscale profiles and a fixed validated floor. Existing preserve
  profiles retain their meaning; gray conversion changes only eligible image assets.
- Added fresh-worker original/proposed rendering, bounded volatile pixels, a shared
  heavy-operation gate and accounting for retained stages plus overlapping evidence.
- Added keyboard-accessible review, numeric size/property summaries, page selection,
  before/proposed views, cancellation/retry and scaled scrolling controls.
- Fixed review findings with regressions: failed reevaluation after durable signed
  consent invalidates old authority; cleanup failure retains its owner for retry;
  malformed cancellation probes fail before consent or preview proof; overlapping
  producer/presentation data must fit the combined central cache budget.

Focused suites passed: 347 policy/adapter checks; 137 renderer/composition/boundary
checks; 75 dialog/window/backend/accessibility checks; 39 workflow/consent checks;
22 private-package/service/model/composition checks. These groups overlap and are not
an aggregate test count. The new regression cases were observed failing before their
corrections. Registered lint, strict typecheck (657 files), format-check, import
boundaries, schemas, task graph, documentation, dependency and portfolio checks passed
at their implementation checkpoints. Full-suite acceptance is recorded below.

### Native and visual evidence

`python -m pytest -q tests/integration/test_pdf_image_stage_worker_windows.py` passed
with the new explicit-gray case (60.23-second test call). The new
`tests/integration/test_compression_review_worker_windows.py` test passed in 43.84 seconds.
It uses the real desktop service factory, constrained ResourcePolicy, admitted worker
runtimes, actual PDFium rendering, exact output hashes, qpdf/reopen checks, unchanged
original hashes and stopped workers before human review.

The generated synthetic raster contains text, small print, colored labels, ink strokes
and a DEMO stamp. Actual evidence: safe baseline 192,427 bytes, required limit 96,311,
selected grayscale 300 dpi/quality 90 output 25,757 bytes, and matched 915 by 732 previews.
Visual inspection found readable primary labels, softer small print, loss of color
cues in the image, and unchanged blue vector border. This demonstrates the need for
explicit human review; it is not general readability certification. No golden was changed.

Two native test setup expectations were corrected before acceptance: assignment dates
must fit the manifest update time, and assembly does not promise to retain source title
metadata. Neither required a production workaround. The full local and corrected CI
runs also passed the final combined-cache admission correction.

The computer-use skill exercised the production dialog with captured worker pixels at
`QT_SCALE_FACTOR=2`, 520 by 360 logical pixels. Keyboard selection/loading, Escape
cancellation, retry, focus scrolling through both images, acknowledgement and export
intent were exercised. Text wrapped without horizontal scrolling; Close/Cancel remained
available. The isolated harness records export intent without writing a package. This
is a keyboard/UI Automation and visual smoke, not an NVDA milestone-gate claim.

Local synthetic evidence is ignored under `output/pws0505-visual/`: the before/proposed
PNGs, original/proposed PDFs, size/hash metadata, trusted capture and visual harness.
No applicant document or real personal information was used.

### Temporary test cleanup exception

Automatic approval review rejected a narrow deletion of a 600-byte synthetic stage
left by the deliberate pre-fix cache-admission regression, with reason “blocked by
policy.” No alternate deletion was attempted. The test now cleans its stages in a
`finally` block. A later read-only check after subsequent pytest sessions found the
file absent; no manual deletion retry was made. It contained only synthetic test bytes
and was never included in Git or runtime artifacts. Its former local path is
`%TEMP%/pytest-of-<user>/pytest-2769/test_review_admits_combined_li0/.bbe2e3be-8357-4639-a3fa-3692de5de38a.pws-package-stage.tmp`.

## Verification checkpoint

Implementation commit: `1b88998752e1844a6360cba5d7200c4794048b29`.
Full local accessibility suite: `python -m pytest -q tests/accessibility --tb=short`
passed 74 tests in 5.57 seconds. Full local security suite:
`python -m pytest -q tests/security --tb=short` passed 700 tests and 510 subtests in
341.27 seconds. Six existing tests skipped because this local account lacks symbolic-link
privileges; junction coverage ran. The first full local unit run passed 5,474 tests
and 1,208 subtests but failed its checked-in bundle check: this evidence report was
updated while the suite was still running, making its recorded hash stale. Refreshing
the canonical hash resolved that metadata error. The full local rerun passed 5,475
tests and 1,208 subtests in 685.08 seconds using
`python -m pytest -q tests/unit --tb=short`. The CI checkout was unaffected by this
local documentation edit.

The full local integration command
`python -m pytest -q tests/integration --tb=short` passed 546 tests with ten existing
environment-dependent skips in 4,647.42 seconds and exited successfully after cleanup.
Its three extra skips relative to CI concern local symbolic-link privileges in import
prechecks, template lookup and containment. The other seven prerequisites match CI.

The unchanged scan-quality recorder and validator passed at the implementation
revision; only the recorded revision changed, with all eight metrics identical.
See [provenance](scan-quality-evaluation.md). The local pytest console launcher remains
blocked by Windows Application Control; locked `python -m pytest` is the equivalent
working invocation. No host security setting was changed.

### Clean Windows CI

[Run 36892591130](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36892591130)
tested revision `b78447469ca02d1439b0703194df76e5980ffca6`. The core job passed in
23 minutes 48 seconds using `python tools/dev.py check --ci-group core`: 5,475 unit,
706 security and 74 accessibility tests passed with no skips. All six symbolic-link
tests skipped locally ran successfully on the clean runner.

The core job passed formatting, lint, strict typing (657 files), import boundaries,
synthetic-fixture/reference checks, every registered schema, task graph, documentation,
portfolio-boundary and resource-evidence validator, reachable-history and working-tree
secret scans, and all five deliberate quality self-tests.

The integration job collected 556 tests and hit its unchanged 90-minute job limit.
Partial log progress showed 487 passes, one failure and seven existing prerequisite
skips before cancellation at 89%; there is no complete suite result. Both the new
compression-review native test and the extended image-stage native test passed,
including the final combined-cache admission change. Forced cancellation interrupted
a live containment test and its staged-resource teardown, which then reported a file
sharing violation. This cancelled run is not acceptance evidence for the full suite.

The earlier failure was reproduced locally in the existing cold-start recovery test.
Its worker-configuration stub returns an opaque object, while the new compression
renderer correctly requires a validated configuration. The fixture now stubs the
renderer factory too, asserts recovery completed before either composition, verifies
the exact configuration/policy/shared gate, and fails if any preview is rendered.
Production validation remains unchanged. All three startup-recovery tests and all
three compression-composition tests passed in 4.53 seconds afterward, using
`python -m pytest -q tests/integration/test_desktop_startup_recovery_windows.py
tests/unit/test_compression_review_composition.py --tb=short`.

Comparison with the prior successful integration run found broadly slower native
test groups (17.2% across comparable heavy groups), consistent with a slower runner.
This timing comparison alone does not establish its cause. No timeout or resource
limit was increased, and the CI workflow was not changed.

The corrected revision `009380f57b51818b3f15907fcaa4e540fb5d21c8` passed
[run 36904211692](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36904211692):

| Command | Result |
|---|---|
| `python tools/dev.py check --ci-group core` | Pass: 5,475 unit, 706 security and 74 accessibility tests; all registered core gates and five quality self-tests passed. Job duration 29 minutes 48 seconds. |
| `python tools/dev.py check --ci-group integration` | Pass: 549 tests, seven existing prerequisite skips, 3,756.82 seconds. Job duration 64 minutes 27 seconds. |

The new native compression-review test, extended image-stage test, repaired three-case
startup-recovery file, prepared package tests and existing target-size native test all
passed. The seven integration skips cover unavailable admitted OCR runtimes and the
existing PWS-0208 private artifact prerequisites. No new test was skipped.

Registered `format-check`, `lint`, `typecheck`, `check-import-boundaries`,
`validate-schemas`, `validate-dependencies`, `validate-task-graph`, `validate-docs`,
`validate-portfolio-boundary`, `validate-pillow-runtime`, `validate-m1-benchmark-report`,
`validate-pws0208-resource-workload` and `validate-pdf-renderer-benchmark` also passed
locally through `python tools/dev.py`. CI additionally passed the complete fixture,
secret-scan and quality-self-test commands. Final metadata closure is revalidated with
formatting, lint, schema, task-graph, documentation and portfolio checks.

## Residual scope

Lossy changes remain limited to eligible images in the supported ordinary-PDF subset.
Other pipelines preserve their established output path and report unsupported reduction
when appropriate. Previews are bounded, and at least one successful pair plus explicit
acknowledgement is required for changed images. The floor does not certify readability;
the user must inspect content. Full NVDA milestone acceptance and hardware certification
are not claimed. See [ADR 0013](../adr/0013-compression-review-and-floor.md).

## Changed files

| Files | Purpose |
|---|---|
| `src/paperworkshell/domain/compression_review.py` | Validated transient review facts and private preview identities |
| `src/paperworkshell/domain/target_size_search.py`, `src/paperworkshell/domain/pdf_image_stages.py` | Explicit grayscale policy and fixed automatic floor |
| `src/paperworkshell/imaging/pdf_image_encoder.py`, `src/paperworkshell/pdf/image_stage_adapter.py` | Bounded grayscale conversion and strict expected-output validation |
| `src/paperworkshell/app/target_size_pdf_export.py` | Authenticated original image analyses for before/proposed facts |
| `src/paperworkshell/export/compression_review.py` | Private candidate ownership, measured presentation, preview binding and commit |
| `src/paperworkshell/export/package_export.py`, `src/paperworkshell/export/target_size_export.py`, `src/paperworkshell/storage/export_package_destination.py` | Retained prepared transactions, authenticated PDF reads, ZIP members and fresh operation probes |
| `src/paperworkshell/export/package_service.py`, `src/paperworkshell/export/package_summary.py` | Public service seam and suppression of size findings only when exact measurements resolve them |
| `src/paperworkshell/worker/compression_preview.py`, `src/paperworkshell/worker/desktop_composition.py`, `src/paperworkshell/__main__.py` | Restricted fresh-worker rendering and lazy desktop composition with shared resource gates |
| `src/paperworkshell/app/package_workflow.py`, `src/paperworkshell/app/organizer.py`, `src/paperworkshell/ui/backend.py` | Saved-case authority, signed-source consent, cancellation and typed UI bridge |
| `src/paperworkshell/ui/compression_review.py`, `src/paperworkshell/ui/preview_comparison.py` | Keyboard-accessible measured review and bounded direct-image comparison |
| `src/paperworkshell/ui/package_export.py`, `src/paperworkshell/ui/window.py` | Review entry point, generation/case checks, acknowledgement and recovery |
| `tests/helpers/compression_review.py`, `tests/unit/test_compression_review_models.py`, `tests/unit/test_target_size_search.py`, `tests/unit/test_pdf_image_stages.py`, `tests/unit/test_pdf_image_stage_adapter.py` | Synthetic review fixtures and policy/model/adapter contracts |
| `tests/unit/test_compression_review_backend_bridge.py`, `tests/unit/test_compression_review_composition.py`, `tests/unit/test_compression_workflow.py` | Composition, typed calls, stale authority, consent and malformed cancellation regressions |
| `tests/unit/test_compression_review_dialog.py`, `tests/unit/test_compression_review_window.py`, `tests/unit/test_package_export_dialog.py`, `tests/unit/test_package_summary.py` | UI state, late results, explicit approval, preserved publication inventory and truthful summary |
| `tests/unit/worker/test_compression_preview.py`, `tests/unit/worker/test_pdf_image_stage_wire.py` | Preview worker admission, deadlines, cancellation, malformed replies and grayscale transport |
| `tests/integration/test_compression_review_service.py`, `tests/integration/test_prepared_package_export.py` | Exact staged bytes, ZIP reads, cleanup ownership, combined evidence budget and failure paths |
| `tests/integration/test_compression_review_worker_windows.py`, `tests/integration/test_pdf_image_stage_worker_windows.py` | Real constrained preparation, grayscale, rendering, reopen and publication |
| `tests/integration/test_desktop_startup_recovery_windows.py` | Extend the isolated startup fixture to the new renderer factory while preserving recovery order and no-render assertions |
| `tests/security/test_pdf_image_stage_safety.py` | Grayscale preservation and hostile-input contracts |
| `tests/accessibility/test_compression_review_accessibility.py`, `tests/accessibility/test_package_export_accessibility.py` | Labels, keyboard, focus, announcements and 200% scaling |
| `docs/adr/0013-compression-review-and-floor.md`, `docs/architecture/compression-review.md`, `docs/architecture/target-size-export.md`, `docs/architecture/pdf-image-stages.md` | Decision, workflow, supported scope and resource contracts |
| `README.md`, `KNOWN_LIMITATIONS.md` | Current desktop workflow and limits |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Unchanged quality metrics rebound to the changed desktop surfaces |
| `docs/development/pws0505-result.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json` | Completion evidence, task state and canonical bundle hashes |
