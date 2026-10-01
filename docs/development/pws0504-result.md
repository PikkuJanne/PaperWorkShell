# Task result — PWS-0504: target-size search and constraint-aware export policy

**Status:** implementation complete; verification in progress.

## Scope and plan

Started from completed PWS-0503 revision `3ac9f5c` on
`codex/pws-0504-target-size-search`. PWS-0407 and PWS-0503 are done. The initial
checkout was the older PWS-0408 branch; its unrelated untracked document and `tmp`
directory were preserved. Selected task: PWS-0504 only.

Implement the fixed verified policy ladder, preserve unsupported pipeline intent,
measure actual combined/separate/ZIP constraints, and report truthful alternatives.
Keep the existing engineering floor, resource budgets, locked engines, schemas,
signed-source consent, and immutable originals. PWS-0505 owns user-facing preview.

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

## Current evidence

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

The full regression and clean Windows CI results remain pending.

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

## Security/privacy/accessibility and portfolio notes

Heavy parsing stays in the existing restricted worker. Target reports contain bounded
numeric facts and private bindings, with sanitized routine representations. No new
dependency, license obligation, schema migration, UI action, sibling dependency, or
release-interface claim is introduced. Existing desktop accessibility remains subject
to regression tests. Hardware certification and new manual NVDA acceptance are not
claimed.

## Residual scope

Only the existing ordinary-PDF image-stage subset can receive lossy changes. Other
groups retain their existing verified pipeline, with an explicit unsupported-policy
result when the target cannot be reached. Preview and user review remain PWS-0505.

## Task graph

PWS-0504 remains open until verification is complete. Next dependency-ready task upon
completion: PWS-0505.
