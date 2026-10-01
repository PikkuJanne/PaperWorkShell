# Task result — PWS-0504: target-size search and constraint-aware export policy

**Status:** done.

## Scope and plan

Started from completed PWS-0503 revision `3ac9f5c` on
`codex/pws-0504-target-size-search`. PWS-0407 and PWS-0503 are done. The initial
checkout was the older PWS-0408 branch; its unrelated untracked document and `tmp`
directory were preserved. Selected task: PWS-0504 only.

Implement the fixed verified policy ladder, preserve unsupported pipeline intent,
measure actual combined/separate/ZIP constraints, and report truthful alternatives.
Keep the existing engineering floor, resource budgets, locked engines, schemas,
signed-source consent, and immutable originals. PWS-0505 owns user-facing preview.

## Implemented

- Search safe optimization, then color-preserving 300, 240 and 200 dpi policies in
  order. Select the first fully verified candidate that meets the actual constraints.
- Measure combined PDF, separate PDFs and the complete ZIP archive. Bind the selected
  policy to the exact staged bytes before publication and retain no-replace save behavior.
- Share one bounded deadline and central resource policy across attempts. Retained
  destination stages reduce the next worker's temporary-storage allowance.
- Preserve unsupported pipeline intent and distinguish unsupported compression,
  exhausted work, impossible supported targets and unrelated constraint failures.
  Provide plain-language suggestions without forcing a lower-quality output.
- Record a versioned, validated decision report with measured sizes, policy/resource
  fingerprints, unsupported positions and the selected stage.

## Verification plan

- Unit: policy validation, first successful stage, non-monotonic sizes, bounded work,
  producer source/plan binding, signed-source consent, malformed evidence and cleanup.
- Integration: exact and impossible targets, total/per-file constraints, ZIP overhead,
  no-replace save, cancellation, one shared deadline, low disk and repeatable retry.
- Restricted Windows worker: real image analysis and encoding with qpdf/PDFium checks,
  combined/separate/ZIP publication, constrained memory/temp behavior and unchanged
  originals/case files.
- Full repository formatting, lint, boundaries, typing, unit/integration/security/
  accessibility suites and schema/task/docs/portfolio/resource/privacy gates.

## Final acceptance

Full Windows CI passed at revision `3c3a2a407568d22fecf8491d7144ffd54bd74ed1`:
[run 36877251869](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36877251869).
The registered `python tools/dev.py check --ci-group core` and
`python tools/dev.py check --ci-group integration` commands passed: 5,322 unit,
686 security, 71 accessibility and 533 integration tests. Seven existing integration
tests require unstaged OCR runtimes or private workload artifacts; no new test skipped.
Formatting, lint, typing, import boundaries, all registered core validators, fixture
and secret scans, and all five quality self-tests passed. The final metadata closure
changes task state, this report, the ADR status and bundle hashes only; runtime code
and tests remain exactly those verified by CI.

## Verification evidence and resolved regressions

Implementation checkpoint: `1c987f09ea2e49d1de5af19dc2829aee27ff1ec7`.

- Focused domain, producer, broker, package, privacy and compatibility tests: 302 passed
  in 4.23 seconds; the later budget-skipped and archive-binding focus passed 116 tests.
- Real constrained Windows test: 1 passed in 115.45 seconds. One case exercised
  combined PDF, separate PDFs and ZIP with eleven worker launches, authenticated
  sources, real encoding, qpdf/PDFium checks, resource snapshots and transaction reopen.
- Registered format-check, lint, import boundaries and typecheck passed: 640 Python
  files, 13 layers and 223 source files. Schemas, task graph, docs, portfolio boundary
  and dependency inventory passed after refreshing the bundle hashes and ready-task list.
- The registered pytest console launcher is blocked by Windows Application Control
  (error 4551). Equivalent locked `python -m pytest` invocations run successfully;
  no host policy or test assertion was changed.
- Replayed existing scan-quality evidence because the lazy desktop broker wrapper is
  a bound surface. Only its recorded revision changed; all eight metrics and other
  evidence fields matched. See [provenance](scan-quality-evaluation.md).

- Full local unit command `python -m pytest -q tests/unit --tb=short`: 5,309 passed
  and 1,208 subtests passed in 535.94 seconds.
- Full local security command `python -m pytest -q tests/security --tb=short`:
  680 passed, six existing local symbolic-link capability skips and 510 subtests
  passed in 227.70 seconds. The skipped tests are workspace file links, export-history
  links, qpdf runtime reparse points, startup-recovery links, and two fixture-directory
  link variants; existing junction tests ran.
- Full local accessibility command `python -m pytest -q tests/accessibility --tb=short`:
  71 passed in 3.35 seconds.
- Registered Pillow, M1 report, PWS-0208 workload and PDF-renderer validators passed.
  Quality self-tests detected all five deliberate violations.

Clean Windows CI core passed at
[run 36855511465](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36855511465),
revision `6e7748914fdd4047136f028d0731cc74fc92d7ce`: 5,309 unit, 686 security and
71 accessibility tests passed, with no skips. The six symbolic-link tests skipped
locally all ran successfully in CI. The core job also passed fixture/renderer-reference
validation, the reachable-history and working-tree secret scan, every registered
core validator and all five quality self-tests. Its runtime source and tests are
the implementation checkpoint above; later commits record evidence and bundle hashes.
The initial integration job failed one existing package pilot: 532 passed, seven
existing OCR/private-artifact prerequisite skips, and one failure. All new target-size
integration tests passed. The failure exposed an admission bug: two maximum output
capacities consumed the constrained profile's entire temporary allowance before
counting nonempty source inputs. The unchanged pilot reproduced that failure locally
in 75.45 seconds. Correction `edc0dce576e4051edf1edb2a8b07e95558ba43be` derives
operation output capacity from remaining headroom without raising the central resource
budget. Assembly, optimization and scratch accounting now retain the live plan section
as well.

The correction passed 112 flattened/searchable unit tests and 149 image-stage,
reservation and protocol/security tests. Registered formatting, lint, import boundaries,
typing, schema and documentation checks passed. A further resource review corrected
the OCR directory monitor's allowance to reserve the parent output/status sections as
well as the live plan. Sources remain counted once inside the monitored directory;
27 searchable-operation tests passed, including the forwarded allowance invariant.
The real-worker rerun passed 14 tests in 698.90 seconds using
`python -m pytest -q tests/integration/test_package_pilot.py
tests/integration/test_target_size_worker_windows.py
tests/integration/test_pdf_image_stage_worker_windows.py
tests/unit/worker/test_pdf_image_stage_operation.py --tb=short`. This includes the
unchanged pilot that failed before the correction, all three target-size output modes,
existing image-stage behavior and native operation checks. Final CI is recorded below.

The second full CI run is recorded at
[run 36865827012](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36865827012),
revision `4b43fe6a39f7b5c6bd9f7d4f2cabbd075c98c40e`, including both resource corrections.
Its core job passed: 5,320 unit tests, 686 security tests and 71 accessibility tests,
with no skips. All registered core gates passed, including fixture/reference validation,
reachable-history and working-tree secret scanning, and five quality self-tests. The
integration job completed in 4,546.70 seconds with 532 passed, seven existing
OCR/private-artifact prerequisite skips, and the one-byte failure below. No timeout
or quality-workflow change was needed. The full local integration run completed with
529 passed, ten existing environment-dependent skips, and the same one-byte failure
in 3,573.78 seconds. Its three additional skips concern local symbolic-link privileges.

The full local run exposed an existing one-byte-output failure scenario that requires
a correlated worker result. The new parent-side 4,096-byte floor rejected it too early.
The preserved contract admits positive configured output caps; the worker reports the
bounded failure. The PWS-0208 version 1 workload and its evidence remain unchanged.
Three broker regressions cover dispatch, the exact one-byte cap, correlation and cleanup.
They reproduced the regression before the narrow correction, then passed in a
114-test broker/reservation/security run. The correction restores positive output
caps in all three brokers; worker-side limits and bounded writes remain unchanged.
Registered formatting, lint, import boundaries and typing passed afterward.
The unchanged live regression
`test_live_output_bound_failure_is_correlated_terminated_clean_and_private` passed
in 29.77 seconds, proving correlated failure, worker termination, cleanup and privacy
with the one-byte limit.

Corrected-revision CI is recorded at
[run 36877251869](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36877251869),
revision `3c3a2a407568d22fecf8491d7144ffd54bd74ed1`. Its core job passed in 23 minutes
16 seconds: 5,322 unit, 686 security and 71 accessibility tests, with no skips. All
registered core gates passed, including fixture/reference validation, reachable-history
and working-tree secret scanning, and all five quality self-tests. Integration passed
533 tests with seven existing prerequisite skips in 4,550.44 seconds. The integration
job completed in 1 hour 17 minutes 30 seconds, within its unchanged 90-minute limit.
The unchanged package pilot, one-byte failure scenario and real target-size worker
test all passed. The optional PWS-0208 live workload was not run; its frozen version 1
contract and evidence were preserved.

### Synthetic visual inspection

The ignored harness `output/pws0504-visual/generate.py` runs the existing pinned native
adapters against a synthetic two-page JPEG/vector PDF. Source: 483,218 bytes; safe
baseline: 483,046; target: 483,045; selected Preserve 300: 79,088. Both pages passed
qpdf 12.3.2 and PDFium checks. Source hash, page geometry, content streams, extracted
text and metadata were preserved; vector-page rendered pixels matched exactly.
RGB image dimensions changed from 600 by 480 to 300 by 240. Inspection of both page-1
renders shows expected image softening with intact text and border; page 2 retains
its text and vector rectangle. This is synthetic engineering evidence, not a general
legibility guarantee. No approved golden was changed.

## Changed files

| Files | Purpose |
|---|---|
| `src/paperworkshell/domain/target_size_search.py` | Versioned schedule, bounds, measured attempts, truthful terminal states and suggestions |
| `src/paperworkshell/app/target_size_pdf_export.py` | Bound baseline cache, worker port, signed-source consent, preserved fallback intent and selected-byte evidence |
| `src/paperworkshell/export/target_size_export.py` | Sequential measured search, shared deadline, precommit evidence binding and publication outcome |
| `src/paperworkshell/export/package_export.py` | Image-stage pipeline, reservation port and private-stage review callback |
| `src/paperworkshell/app/package_pdf_export.py` | Retained-stage reservation for fallback pipelines |
| `src/paperworkshell/app/flattened_pdf_export.py`, `src/paperworkshell/app/searchable_pdf_export.py`, `src/paperworkshell/__main__.py` | Application protocols and lazy desktop broker forwarding |
| `src/paperworkshell/worker/pdf_image_stage_broker.py`, `src/paperworkshell/worker/flattened_pdf_export_broker.py`, `src/paperworkshell/worker/searchable_pdf_export_broker.py` | Reduced worker temporary authority and source/output overlap admission |
| `src/paperworkshell/worker/pdf_image_stage_operation.py`, `src/paperworkshell/worker/flattened_pdf_export_operation.py`, `src/paperworkshell/worker/searchable_pdf_export_operation.py`, `src/paperworkshell/worker/protocol/image_stage.py` | Live plan-buffer accounting during assembly, optimization and image/OCR scratch work |
| `tests/unit/test_target_size_search.py`, `tests/unit/test_target_size_pdf_export.py` | Policy validation, first success, bounds, binding, signed sources and recovery |
| `tests/unit/worker/test_target_size_reservations.py` | Invalid/exhausted reservations and reduced worker authority |
| `tests/unit/test_package_pdf_export.py`, `tests/unit/worker/test_flattened_pdf_export_broker.py`, `tests/unit/worker/test_searchable_pdf_export_broker.py` | Existing pipeline reservation regressions |
| `tests/unit/worker/test_pdf_image_stage_broker.py`, `tests/unit/worker/test_pdf_image_stage_operation.py`, `tests/unit/worker/test_flattened_pdf_export_operation.py`, `tests/unit/worker/test_searchable_pdf_export_operation.py`, `tests/security/test_pdf_image_stage_transport_security.py` | Reduced-capacity propagation, constrained desktop admission and live-plan reservations |
| `tools/validate_worker_rpc.py`, `schemas/examples/worker-rpc/v1/pdf-image-stage-start.json` | Offline validation and example storage allowance include the live plan section |
| `tests/unit/test_flattened_pdf_export_app.py`, `tests/unit/test_searchable_pdf_export_service.py` | Test broker signatures match the optional reservation |
| `tests/integration/test_target_size_package_export.py` | Exact/impossible boundaries, multi-file/ZIP limits, evidence checks, cancellation/deadline/low-disk and commit outcomes |
| `tests/integration/test_target_size_worker_windows.py` | Real constrained worker and publication for all three modes |
| `tests/security/test_target_size_pdf_export_security.py` | Malformed evidence, altered bytes, sanitized errors and cleanup |
| `docs/adr/0012-target-size-search.md`, `docs/architecture/target-size-export.md`, `docs/architecture/package-export.md`, `docs/architecture/pdf-image-stages.md` | Rationale, contracts, resource accounting and supported scope |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Unchanged metrics rebound to the changed lazy wrapper surface |
| `docs/development/pws0504-result.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json` | Task result, state/evidence and canonical bundle hashes |

## Review findings resolved

Additional evidence is validated before publication, including stage profile, selected
hash/size/page count, pipeline positions and unchanged baseline identity. Invalid or
throwing report properties remove private stages instead of hiding a committed file.
Partial publication preserves its inventory even when later callbacks fail.

Worker report page counts are bound to the planned pages. Candidate reads support a
256 KiB policy even when a destination requests 1 MiB. Lazy candidate contexts consume
the current reservation under the producer lock. A budget-skipped stage is recorded as
incomplete search evidence and cannot justify an unattainable-target claim. Full output
archive bytes remain bound to the decision report.

## Security/privacy/accessibility notes

Heavy parsing stays in the existing restricted worker. Target reports contain bounded
numeric facts and private bindings, with sanitized routine representations. Immutable
originals, signed-source consent and destination protection remain enforced. No new
dependency, license obligation, schema migration or UI action is introduced. Existing
desktop accessibility regressions passed. Hardware certification and new manual NVDA
acceptance are not claimed.

## Portfolio boundary and release-interface notes

The search serves PaperWorkShell's own size-constrained export workflow. Standalone
operation and product-owned case data are preserved, with no sibling dependency or
adapter. No release interface or compatibility claim changes; unproved packaged-release
facts remain `TBD`.

## Residual scope

Only the existing ordinary-PDF image-stage subset can receive lossy changes. Other
groups retain their existing verified pipeline, with an explicit unsupported-policy
result when the target cannot be reached. Preview and user review remain PWS-0505.

## Task graph

PWS-0504 is done. PWS-0505 is now dependency-ready and marked ready: compression
preview, grayscale option, and legibility floor.
