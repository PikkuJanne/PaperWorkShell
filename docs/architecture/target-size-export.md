# Target-size export search

- Tasks: PWS-0504 and PWS-0505
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

The PWS-0505 [compression review](compression-review.md) uses the prepared variant
of this API through the desktop package workflow. The application imports a narrow
protocol; native document objects remain inside worker adapters.

## Policy and constraints

The version 1 ladder is safe optimization, Preserve 300, Preserve 240, Preserve 200.
Every policy uses its immutable baseline. Processing stops at the first measured
success, even if a later stage might be smaller. An ineffective stage does not skip
remaining supported stages, because size changes need not be monotonic.

The explicit grayscale choice uses version 2 with matching 300/240/200 dpi stages.
It preserves the four-attempt bound and the fixed 200 dpi/JPEG80 floor. Version 1
retains its original semantics; no below-floor override is admitted. See
[ADR 0013](../adr/0013-compression-review-and-floor.md).

Combined mode measures its one PDF. Separate mode measures each PDF and their sum.
ZIP mode measures the archive after writing and reopening it; member file limits still
apply separately. The canonical portal evaluator also checks naming, organization,
file count, pages, and encryption. A non-size failure stops image escalation.

The immediate-export API discards unsuccessful attempts. The review API retains the
last verified attempt privately, including an unsafe result for inspection, and keeps
ZIP member PDFs available for page previews. Only a satisfied candidate may publish.
Closing or replacing review discards the private stages. Publication uses exactly
the bytes measured and verified. A collision or
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
output sections and staged copies overlap within the remaining allowance. Output
capacity is lowered to fit the space remaining after inputs, plan and evidence are
accounted for. The configured output maximum is a ceiling. Baseline analysis and
current report memory are bounded, and candidates are read in bounded
chunks. Nothing loads a complete packet as full-resolution rasters.

Originals, manifest intent, signed-source consent, no-network processing, and no-replace
destinations retain their existing contracts. Routine representations exclude paths,
names, document values, and hashes. Compression review adds keyboard controls,
announcements and a scrolling layout for 200% scaling. Human review has no timeout;
each prepare/render/commit operation has its own bounded deadline and cancellation.
No dependency or schema changes are needed. Release documents retain their existing
candidate evidence and `TBD` facts; this adds no ThirdLife compatibility claim.
