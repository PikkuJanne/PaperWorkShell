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

Focused implementation tests pass. Final command counts, exact revision and any
environment limitations will be recorded after the full verification run.

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
