# ADR 0011: Explicit stages for selected PDF image assets

- Status: Implementation selection for PWS-0503; verification pending
- Date: 2026-10-01
- Decisions preserved: PWS-D009, PWS-D012, PWS-D013, PWS-D015, PWS-D024, PWS-D029,
  PWS-D034, PWS-D036, PWS-D037, PWS-D041, PWS-D042
- Starting revision: `3088c1d2acc01c3b5a86400c4eb6d061e01ff733`

## Context

PWS-0502 supplies verified lossless object/stream optimization. PWS-0503 needs image analysis
and bounded stages that the following target-search and preview tasks can consume. Repeatedly
rasterizing pages would lose vector/text structure. Reusing a raster without examining its PDF
placement, color space, masks and shared references could change unintended pages or appearance.

## Selection

Provide an explicit analysis/apply operation in the restricted worker. Assemble and verify the
baseline and run safe optimization first. Analysis returns deterministic internal asset ordinals,
page references, image dimensions, physical resolution where known, eligibility, and byte counts.
An apply request selects exact eligible assets and binds both the base plan and analyzed bytes.
Reassembly that does not reproduce those bytes fails closed and requires fresh analysis.

Each apply processes exactly one stage from that baseline, retaining only effective smaller
image streams and selecting a whole-PDF candidate only if it is smaller and passes verification.
It does not search for a target or automatically enable lossy changes in ordinary exports.

### Initial policy parameters

| Stage | Target resolution | JPEG quality | Color/subsampling |
|---|---:|---:|---|
| Preserve 300 | 300 dpi | 90 | Preserve RGB/gray; RGB 4:4:4 |
| Preserve 240 | 240 dpi | 85 | Preserve RGB/gray; RGB 4:4:4 |
| Preserve 200 | 200 dpi | 80 | Preserve RGB/gray; RGB 4:4:4 |

Never upscale. Derive downsampling from the lowest physical resolution among all placements;
round dimensions conservatively. Another axis or smaller placement may stay above the target;
an asset already below the target remains at its original resolution.
Assets already below 200 dpi are retained. This initial backend floor and the quality values are
conservative engineering parameters, not proof of document legibility. The PWS-0505 preview,
review, floor and override workflow remains required before exposing automatic target-size work
to users. Numeric policy changes need their own evidence and a versioned profile.

Only page-local opaque eight-bit DeviceRGB/DeviceGray XObjects with understood sample/filter semantics are
eligible initially. Images shared across pages or reachable through inherited parent resources
and nonpage aliases remain unchanged. Nondefault decoding, masks,
unsupported colors/filters, external references and uncertain placement are not guessed.
Preserve PDF image orientation; imported-photo EXIF normalization is inappropriate for embedded
PDF sample streams. Image metadata and PDF dictionary semantics must be checked before decoding.

Image-heavy classification uses the largest axis-aligned placement's clipped coverage of the page crop box,
with a threshold of 500,000 parts per million (50%). It is advisory and does not sum overlapping
placements. Arbitrary rotations and unknown clipping or placement leave coverage unknown rather than creating a false
eligibility or resolution claim.

### Estimates and verification

The initial encoded-stream estimate scales original stream bytes by proposed pixel area. It is
a stated heuristic: codec quality, image complexity and PDF overhead can make actual bytes
smaller or larger. Report the measured stream bytes per page and whole-PDF bytes separately.
Count an asset once per page; shared references can recur across pages, so page counts are not
a partition of the PDF file size.

Lossy output must carry its own stage evidence. It cannot inherit a claim of equality with source
pixels. Preserve canonical nonimage object semantics and exact unselected image encodings,
allowing only authenticated selected image substitutions. As in safe optimization, storage-only
trailer fields and plain/Flate representations of nonimage streams are normalized. Selected JPEG
output omits embedded EXIF/ICC fields; retained PDF dictionary metadata remains in the graph.
Reopen and structurally check the candidate, render all pages,
compare unchanged pages exactly, and report changed-page observations with their actual scope.

## Resource and trust boundary

Reuse the versioned ResourcePolicy, single heavy-operation coordinator, restricted one-shot
worker, reduced source handles and no-replace publication primitives. Derive caps for object
traversal, decoded/encoded data and evidence from existing operation budgets. Admit one image
before decoding and release its raster work before the next. Account for all simultaneously live
buffers and temporary/output/report extents; do not increase a budget to make a regression pass.
The plan uses the existing searchable-export convention: an immutable anonymous section
identified as `source_read` with a null reference operation ID, exact size/hash, and a reduced
read-only section handle. It creates no filesystem authority and needs no change to the released
RPC v1 schema or its input-reference meaning.

The client authenticates bounded evidence and candidate bytes only after whole-worker termination
and retained-source revalidation. All errors and routine diagnostics remain sanitized. The new
operation supplies a transient candidate for later application policy, not a new destination or
history commit path.

## Alternatives

- Whole-page rasterization would replace vector/text content and is rejected for size reduction.
- General-purpose image extraction/transcoding would broaden color, mask and decoder behavior
  without the required evidence. The first adapter uses a narrower explicit subset.
- Cloning shared image objects for selected placements can be considered later with complete
  reference and resource-dictionary tests. This version retains shared images.
- A new image library is unnecessary: the admitted Pillow runtime supplies bounded JPEG encoding
  and resampling; pikepdf/qpdf and PDFium remain behind their existing adapter boundaries.
- The adapter uses the pinned pikepdf low-level `StreamParser` callback interface to bound operand
  and graphics-state accumulation. It does not materialize a complete parsed content list.
  A future pikepdf upgrade must rerun this adapter contract and malformed-content corpus.

## Consequences and evidence

No frozen decision changes. No new dependency, license obligation, manifest/history migration,
sibling integration, or packaged-release claim is introduced. When analysis succeeds, unsupported
assets remain visible as unsupported/uncertain. Unsupported content-stream filters or decode
parameters can instead fail analysis. The completed corpus, bounds, failure-path and live-worker
evidence will be recorded in [PWS-0503 results](../development/pws0503-result.md).

### API references

The [pikepdf image documentation](https://pikepdf.readthedocs.io/en/latest/topics/images.html)
explains image placement, reuse, masks and decoding semantics. That live documentation currently
describes a later version than the pinned pikepdf 10.10.0; the implementation is checked against
the installed, locked runtime and does not adopt new APIs from the later documentation.
The [Pillow 12.3 JPEG options](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg)
define encoder quality and `subsampling=0` as 4:4:4. These references explain library behavior;
the selected DPI/quality values require this task's own synthetic evidence.
