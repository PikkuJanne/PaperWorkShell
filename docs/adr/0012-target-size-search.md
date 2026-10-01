# ADR 0012: Bounded search through verified export policies

- Status: Accepted and verified for PWS-0504
- Date: 2026-10-01
- Decisions preserved: PWS-D009, PWS-D012, PWS-D013, PWS-D024, PWS-D029,
  PWS-D030, PWS-D036, PWS-D037, PWS-D041, PWS-D042
- Prerequisites: PWS-0407 and PWS-0503

## Problem

The package planner knows total and per-file limits before export, but image entropy,
PDF overhead, and ZIP headers make estimates insufficient to select a policy. Applying
stronger JPEG settings does not guarantee a monotonically smaller file. Recompressing
the previous lossy candidate would also compound damage and make comparisons unreliable.

## Decision

Add an explicit backend search. Test a fixed versioned schedule: safe object/stream
optimization, then the existing color-preserving 300 dpi/quality 90, 240 dpi/quality 85,
and 200 dpi/quality 80 image stages. Select the first fully verified package that meets
all actual constraints. A configured attempt cap may shorten this prefix. Do not use
binary search, estimates as success evidence, or an unbounded search over asset subsets.

“Least destructive” means the first satisfying policy in this supported ordered
schedule. It does not mean a mathematical optimum over every possible encoding or
per-asset combination. All eligible page-local images are selected deterministically;
vector/text pages retain their structure, color is preserved, and the existing 200 dpi
engineering floor remains in force. PWS-0505 owns affected-page preview, user review,
and the user-facing floor/override workflow. Ordinary desktop export keeps its existing
policy until that workflow is implemented.

Each candidate starts from the authenticated safe baseline. The producer binds case
revision, manifest digest and CAS token, ordered page IDs, export-plan fingerprint, and
baseline digest across attempts. No candidate becomes a new source or modifies a case.

## Integration and limited support

Use the existing package transaction to measure PDFs and the actual ZIP, evaluate all
canonical portal constraints, and commit by no-replace rename. Failed attempts close
and remove every private destination stage before the next attempt. Additional decision
evidence is validated before publication. Destination collision, partial commit, and
uncertain commit stop the search and preserve the actual destination inventory.

The existing image-stage worker supports ordinary PDF export plans. Groups needing
overlays, cropping, image intake, scan cleanup, or OCR retain their existing flattened
or searchable pipeline and are marked unsupported for image-stage compression. They
can still export when their unchanged verified bytes fit. The report distinguishes
unsupported policy from exhaustion of the supported schedule. It never drops intent
or invents an intermediate manifest to make these groups compressible.

## Bounds and evidence

At most four package attempts run sequentially. One shared monotonic deadline spans
startup, analysis, encoding, verification, staging, and constraint checks. Its maximum
is four times the central ResourcePolicy worker CPU-time allowance (480 seconds for
both current profiles); the caller's deadline may end work earlier. This wall-clock
bound is separate from the worker's enforced CPU budget. Each worker keeps its existing
memory, CPU, page, process, output, and temporary limits.

Retained destination stages reduce the next worker's temporary allowance. Admission
accounts for source/plan/raster/status/evidence data and output overlap. Only one
candidate is active. Cached analysis and current image-stage evidence are limited by
the central cache-byte budget, with at most 128 planned outputs. Candidate reads are
bounded and authenticated. Cancellation, timeout, low disk, corrupt results, and
cleanup failures stop publication.

The decision report records the schedule version, resource policy and fingerprint,
package fingerprint, actual PDF and ZIP sizes per attempt, blocking codes, unsupported
positions, and selected policy. The package result retains exact manifest authority,
and selected image-stage reports retain baseline/selected hashes and asset effects.
Reproduction requires those immutable inputs and the same locked engine versions.

## Alternatives and effects

- Binary search is invalid without monotonic encoded sizes.
- An arbitrary-baseline worker operation would expand the security and RPC boundary.
  Retaining unsupported pipelines preserves their established behavior within this task.
- Exhaustively searching per-image combinations would multiply work without a present
  quality metric that could prove an optimum.
- Forced reduction below the existing floor would violate PWS-D024.

No dependency, license, released schema, case/history migration, sibling service,
portfolio adapter, or packaged-release claim is added. Reports are transient, as in
PWS-0407. Standalone operation and PaperWorkShell's data ownership are preserved.

See [architecture](../architecture/target-size-export.md) and
[verification results](../development/pws0504-result.md).
