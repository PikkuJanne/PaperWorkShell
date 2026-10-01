# Explicit PDF image analysis and re-encoding

- Task: PWS-0503
- Selection: [ADR 0011](../adr/0011-staged-pdf-image-reencoding.md)
- Base: [verified PDF export](pdf-export.md) and [safe optimization](pdf-safe-optimization.md)

PDF object traversal and bounded stream extraction stay in the `pdf` adapter. Pillow decoding,
resampling and JPEG encoding stay in the `imaging` adapter, behind bytes and pure domain values.
The worker coordinates both without exposing native library objects to the application or UI.

## Workflow and ownership

`BrokeredPdfImageStageProcessor.process` accepts an ordinary PDF export plan, retained immutable
source leases, and a version 1 `PdfImageStageRequest`. The restricted worker assembles and
verifies the normal derivative, runs the fixed safe optimization stage, and analyzes that
baseline. An analysis-only request has no profile, selection, baseline digest or plan fingerprint.

An apply request binds the exact analyzed baseline SHA-256 and export-plan semantic fingerprint,
an ordered set of global asset IDs, and one closed profile. If reassembly produces different
baseline bytes, the request fails closed and requires fresh analysis. Each attempt starts from
the baseline. Attempts do not successively recompress the preceding lossy output.

The result is a transient `VerifiedPdfImageStageCandidate` with bounded reads, analysis and stage
evidence, plus a separately identified baseline verification report. The caller must close it.
This operation does not commit a destination, change case/history state, or add a UI action.
PWS-0504 owns target-size search; PWS-0505 owns affected-page preview and the user-facing
legibility/floor/override workflow. Existing export paths keep their lossless image policy.

## Analysis and eligibility

Global asset IDs and page indexes are zero-based and deterministic for the analyzed baseline.
The report records dimensions, color mode, encoded bytes, page references, effective physical
resolution where known, and a closed eligibility reason. A page counts each referenced asset
once. Shared assets may occur in several page totals, so those totals cannot be summed as file
size or file savings.

Direct-page placement uses bounded `q`, `Q`, `cm`, and `Do` parsing and page `UserUnit` values.
The lowest resolution among all known placements controls an asset. Cropping does not increase
its effective resolution. Image-heavy classification uses the largest axis-aligned placement's
clipped coverage of the crop box: at least 500,000 ppm (50%) is image-heavy. This advisory rule
does not add overlapping areas or claim to measure legibility. Arbitrary rotations, clipping,
executed forms, inline images and uncertain graphics state leave coverage unknown.

The initial supported subset is page-local opaque eight-bit DeviceRGB/DeviceGray image XObjects with plain
samples, parameter-free Flate samples, or supported JPEG encoding. Images shared across pages
remain unchanged. Assets also reachable through inherited parent resources or nonpage aliases
remain unchanged, including unused or overridden parent resource entries. Forms, inline images,
masks, nondefault decoding, unsupported color spaces or
filters, external references, optional-content semantics, and uncertain placement cannot become
eligible through a guess. Unused resource images remain in the PDF. PDF image axes are preserved;
photo-intake EXIF orientation is not applied to embedded PDF samples. Selected JPEG output omits
embedded EXIF/ICC fields; it preserves the PDF dictionary metadata.

Eligibility is a structural finding before JPEG decoding. An apparently eligible JPEG can still
be rejected during apply for an embedded ICC/Adobe marker, inconsistent dimensions/mode or
invalid samples. Unsupported page-content filters or decode parameters can fail analysis itself.
Unsupported content is therefore not always representable as a successful ineligible-asset report.

## Fixed stages and measured effects

| Profile | Target resolution | JPEG quality | Color |
|---|---:|---:|---|
| Preserve 300 | 300 dpi | 90 | Preserve RGB/gray; RGB 4:4:4 |
| Preserve 240 | 240 dpi | 85 | Preserve RGB/gray; RGB 4:4:4 |
| Preserve 200 | 200 dpi | 80 | Preserve RGB/gray; RGB 4:4:4 |

No upscaling occurs. Uniform scaling uses the lowest resolution across axes and placements, with
conservative rounding. Another axis or smaller placement may remain above target; an asset already
below target keeps its original resolution. Assets
below the 200 dpi engineering floor are retained. These parameters do not guarantee readable
small text, stamps, or handwriting. The profile version fixes the numeric parameters.

The estimate is `ceil(original encoded bytes × proposed pixels / original pixels)`. It does not
predict JPEG entropy or whole-PDF overhead. Asset reports separate estimated, attempted and
selected stream bytes. Page reports contain original, estimated and actual selected stream totals.
Whole-PDF baseline, candidate and selected sizes are measured
separately. Output dimensions/resolution describe the selected output, including on fallback.

Only a strictly smaller encoded image replaces its selected asset. The completed PDF must also
be strictly smaller than the baseline. The candidate writer is capped at baseline size minus one;
overflow may leave the candidate size unknown and retains the exact baseline. A skipped budget,
empty selection, or ineffective stage never implies successful image replacement. Invalid
candidates fail the operation rather than being accepted as a successful reduction.

## Verification and trust

The adapter authenticates its baseline, reopens the candidate strictly, checks page count and
structure with the admitted qpdf checker, and compares the complete reachable object graph.
Only authenticated selected image substitutions are allowed in this comparison. Text, fonts,
content streams, forms, retained metadata, dictionary semantics and reference topology retain
their canonical meanings. Storage-only trailer fields and plain/Flate representations of nonimage
streams are normalized, as in safe optimization. Unselected image encodings and filter dictionaries
are byte-exact. The comparator's ordinary lossless mode retains its existing meaning.

Every page is rendered sequentially through the existing bounded PDFium verification path.
Unchanged pages must match baseline pixels exactly. Changed pages must render at the same
geometry; corpus tests inspect their lossy image changes while preserving text/vector structure.
The stage report does not claim source-pixel equality for lossy output.

The additive `pdf.image_stage` v1 operation carries only reduced handles, opaque IDs, bounded
numeric facts and hashes. Its immutable anonymous plan section is a `source_read` reference with
a null reference operation ID, following the existing searchable-export convention. The
authenticated section has a separate read-only handle and supplies no filesystem authority.
The released RPC schema stays unchanged. Its evidence section is capped at 2 MiB, one eighth of operation
memory, and the existing 65,536-node JSON limit. The broker binds both output references and the
small result header, terminates the worker, revalidates retained sources, authenticates bytes,
and validates analysis/report/request consistency before returning a candidate. Paths, filenames,
PDF resource names and document text are excluded from evidence and routine representations.

## Resource and failure behavior

The existing versioned ResourcePolicy and single heavy-operation coordinator apply. Work proceeds
one image at a time. Memory admission includes source/target raster cores, encoded copies,
encoder buffers and native bookkeeping headroom. Native allocations remain subject to worker
Job Object limits. No new queue, cache, parallel decoder, process allowance or larger budget is
introduced.

Temporary accounting includes staged sources, the assembled baseline, any safe-optimization
candidate, image-stage candidate, output/evidence sections, and retained scratch. Admission and
free-space checks precede expensive work. Cancellation, deadline expiry, low disk, partial writes,
parser/decoder failures and invalid evidence discard private outputs and preserve immutable
sources. Cleanup and fresh retry are part of verification; existing development runtime-startup
delays still apply.

## Compatibility and standalone boundary

No dependency upgrade, new license, released schema reinterpretation, case/history migration,
sibling integration or public release-interface claim is introduced. Existing report versions
keep their meanings. PaperWorkShell owns the transient processing state, and remains usable
without ThirdLife or sibling applications. Packaged release facts remain `TBD`.

Implementation and verification evidence is recorded in
[PWS-0503 results](../development/pws0503-result.md).
