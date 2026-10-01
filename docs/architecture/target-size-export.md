# Target-size export search

- Task: PWS-0504
- Policy: [ADR 0012](../adr/0012-target-size-search.md)
- Building blocks: [package export](package-export.md),
  [safe optimization](pdf-safe-optimization.md), [image stages](pdf-image-stages.md)

## Entry points

`domain.target_size_search` defines validated policies, attempt facts, terminal reasons,
and fixed plain-language alternatives. `app.target_size_pdf_export` adapts ordinary
PDF package groups to the existing image-stage worker port and preserves other groups
through the existing package producer. `export.target_size_export.export_target_size_package`
runs the bounded schedule and returns decision evidence alongside the real package
publication result.

This is an explicit backend API for the PWS-0505 review workflow. It is not wired into
ordinary desktop export, a new CLI, or a sibling-facing interface. The application
imports a narrow protocol; native document objects remain inside worker adapters.

## Policy and constraints

The version 1 ladder is safe optimization, Preserve 300, Preserve 240, Preserve 200.
Every policy uses its immutable baseline. Processing stops at the first measured
success, even if a later stage might be smaller. An ineffective stage does not skip
remaining supported stages, because size changes need not be monotonic.

Combined mode measures its one PDF. Separate mode measures each PDF and their sum.
ZIP mode measures the archive after writing and reopening it; member file limits still
apply separately. The canonical portal evaluator also checks naming, organization,
file count, pages, and encryption. A non-size failure stops image escalation.

Every unsuccessful package transaction discards its private stages. The successful
transaction publishes exactly the bytes it measured and verified. A collision or
partial/uncertain save never triggers another compression attempt. The decision's
`satisfied` state means constraints passed; only `package.successful` means the whole
package was committed and reopened.

## Reports and failures

The result contains actual per-attempt sizes, the fixed schedule and work limit,
the resource-policy fingerprint, unsupported positions, and the chosen profile.
Image-stage evidence is separate from the safe-baseline optimization report. A lossy
candidate must not inherit the baseline's byte size or claim equality with its pixels.
All policy evidence is validated while outputs are still private.

The search distinguishes full schedule exhaustion, a shortened work limit, unsupported
compression, and non-size constraints. Suggestions include increasing the permitted
size or splitting only if the recipient permits it. They do not change saved constraints
or claim portal acceptance. Cancellation and timeout use existing stable sanitized
errors and remove private stages; the same snapshot can be retried.

## Resource, privacy, and compatibility

Workers run sequentially with one shared search deadline and existing ResourcePolicy
limits. Previously staged output bytes are subtracted before admitting another worker;
output sections and staged copies overlap within the remaining allowance. Baseline
analysis and current report memory are bounded, and candidates are read in bounded
chunks. Nothing loads a complete packet as full-resolution rasters.

Originals, manifest intent, signed-source consent, no-network processing, and no-replace
destinations retain their existing contracts. Routine representations exclude paths,
names, document values, and hashes. No new controls need accessibility acceptance in
this backend task; existing desktop workflow and accessibility tests remain required.
No dependency or schema changes are needed. Release documents retain their existing
candidate evidence and `TBD` facts; this adds no ThirdLife compatibility claim.
