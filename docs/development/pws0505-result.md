# Task result — PWS-0505: compression preview, grayscale option, and legibility floor

**Status:** review; full repository verification is running.

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
No new dependency or license obligation is planned; no manifest migration is needed.

## Portfolio boundary and release-interface notes

This is PaperWorkShell's own local export workflow. Case data stays product-owned,
with no sibling repository, service or adapter. No release-interface claim changes;
unproved packaged-release facts remain `TBD`.

## Task graph

PWS-0505 is in review. PWS-0506 remains planned until all required verification passes.

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
at their implementation checkpoints. Full suites remain required for final acceptance.

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
metadata. Neither required a production workaround. A final native run after the
combined-cache admission correction is part of the full suite.

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
`finally` block. The prior file remains outside the repository under pytest temporary
storage; it contains only synthetic test bytes. It is not included in Git or runtime
artifacts. Its local path is
`%TEMP%/pytest-of-<user>/pytest-2769/test_review_admits_combined_li0/.bbe2e3be-8357-4639-a3fa-3692de5de38a.pws-package-stage.tmp`.

## Verification checkpoint

Implementation commit: `1b88998752e1844a6360cba5d7200c4794048b29`.
Full local accessibility suite: `python -m pytest -q tests/accessibility --tb=short`
passed 74 tests in 5.57 seconds. Full unit and security suites are running.
The unchanged scan-quality recorder and validator passed at the implementation
revision; only the recorded revision changed, with all eight metrics identical.
See [provenance](scan-quality-evaluation.md). The local pytest console launcher remains
blocked by Windows Application Control; locked `python -m pytest` is the equivalent
working invocation. No host security setting was changed.
