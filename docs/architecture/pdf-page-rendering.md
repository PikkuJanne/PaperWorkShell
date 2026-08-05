# PDF page-rendering contract

- Status: PWS-0106 adapter implemented; final comparison and admission evidence in progress
- Task: PWS-0106
- Decisions: PWS-D012, PWS-D013, PWS-D017, PWS-D019, PWS-D034
- ADR: [ADR 0005 renderer selection](../adr/0005-pdf-renderer-selection.md)
- Worker boundary: [Restricted worker lifecycle contract](worker-lifecycle.md)
- Structure boundary: [PDF structural-inspection contract](pdf-structural-inspection.md)
- Preview data flow: [DF-PREVIEW](../security/data-flows.md#df-preview--render-bounded-thumbnailspreviews)

## Scope and ownership

PWS-0106 owns a worker-local, replaceable PDF page-renderer interface, its request/result models,
the selected adapter, a typed inherited-page-geometry resolver over the same verified source,
stable failure mapping, per-render bounds, and renderer contract tests. It does not own a UI
widget, thumbnail scheduling, a new RPC operation, result-pipe framing, cache keys, cache
publication, cache invalidation, or cache eviction. Those integration and persistence concerns
remain PWS-0108 work.

Rendering is hostile document processing. Only the restricted document worker may import the
selected renderer or open a full PDF for rendering. The GUI, application, domain, storage, and UI
layers receive renderer-neutral typed state; they never receive a pypdfium2 document/page/bitmap,
PDFium handle, third-party exception, document path, or encoded renderer output.

The implemented selection target is pypdfium2 5.12.1 with default PDFium 152.0.7947.0. Its exact
CPython 3.14 import, engine identity, empty feature-flag set, native/license closure, live adapter,
golden, limit, malformed-input, cancellation, and hostile-input evidence are recorded. The
[selection ADR](../adr/0005-pdf-renderer-selection.md) keeps final admission conditional only until
the same-corpus Windows comparison is recorded and reviewed.

## Boundary and data flow

The adapter consumes one already authenticated, immutable PDF through a worker-local, read-only,
seekable source abstraction. It writes canonical raw pixels only to a caller-owned bounded sink
that supports `write` and `flush`; it never accepts a destination path. The operation/broker layer
remains responsible for resolving logical objects, verifying identity and digest, granting
least-privilege handles, and revoking those authorities.

The owning operation may invoke the adapter only after the same source identity has a successful,
current PWS-0105 structural-inspection result. PDFium's ability to recover or display a malformed
file is not structural approval and cannot upgrade `encrypted`, `malformed`, changed, unknown, or
uninspected source state into a renderable case record.

Conceptually, one render proceeds as follows:

1. The trusted caller constructs a closed render request and policy with a zero-based page index,
   box mode, additional quarter-turn, maximum width/height, worker-backed lifecycle probes, and
   explicit page/pixel/bitmap/memory/destination/time limits.
2. Before opening or allocating, the worker verifies the source authority, current engine build
   identity and feature flags, request values, deadline, and cancellation state.
3. A PWS-0106 typed resolver behind the pikepdf/qpdf adapter boundary reads the same verified
   source and resolves inherited MediaBox, CropBox, and rotation metadata. This is a new renderer
   requirement, not output claimed from the current PWS-0105 inspection report. The renderer does
   not trust pypdfium2's non-inheriting page-box helper values as the sole authority.
4. The adapter opens the source, validates the document page count and requested page, resolves
   effective geometry and output dimensions, and proves every size computation within policy
   before allocating the bitmap.
5. PDFium renders through the selected transform. The adapter converts and streams its internal
   bitmap as fixed RGBA8 white-background rows while computing a pixel digest.
6. Success returns metadata only after the exact byte count is written and the sink is flushed.
   On any error the caller discards the complete operation-scoped sink, including any partial raw
   output; the broker and worker lifecycle settle authority and terminate the one-shot generation
   where required.

This document defines the in-worker adapter stream, geometry-resolver contract, and metadata
result. PWS-0108 must separately define how a validated request and correlated typed geometry reach
the worker, how source/output handles are brokered, and how one completed sink becomes a trusted,
atomic cache snapshot.

## Renderer-neutral request contract

The public-to-adapter request model uses only project-owned immutable types. Its semantic fields
are:

| Field | Contract |
|---|---|
| Source | One worker-local, read-only, seekable abstraction bound to an immutable source identity; no path or renderer object |
| Destination | One caller-owned bounded raw-raster sink with `write` and `flush`; no path, cache directory, or renderer object |
| Page index | Zero-based integer; must be below both the authenticated document page count and configured page-count limit |
| Box mode | Closed value: `crop` for normal preview, or `media` only for an explicit inspection/test profile |
| Additional rotation | One of 0, 90, 180, or 270 degrees clockwise; arbitrary angles are rejected |
| Target geometry | Positive maximum width and height; the effective page box is fitted within them while preserving aspect ratio |
| Render policy | Explicit maximum page count, width, height, total pixels, bitmap bytes, destination bytes, memory bytes, and wall time |
| Lifecycle | Worker-backed deadline-expired and cancellation-requested probes; the adapter does not accept wall-clock timestamps |

The request does not expose renderer flags. Antialiasing, annotation/form handling, background,
pixel order, color conversion, and render mode are fixed by the versioned adapter profile so a
caller cannot silently create a new fidelity or security mode.

## Canonical raw RGBA8 sink

Every render sink uses the same renderer-neutral pixel contract:

| Property | Required value |
|---|---|
| Channel order | Red, green, blue, alpha |
| Channel depth | 8 unsigned bits per channel |
| Pixel order | Left to right within each row; rows from top to bottom |
| Stride | Exactly `width * 4` bytes; no row padding |
| Buffer length | Exactly `height * stride` bytes |
| Alpha | Every pixel is 255 after compositing; the raster is opaque |
| Background | PDF content is composited over solid white |
| Ownership | Caller-owned operation-scoped sink; bytes are non-authoritative until metadata success |

Because alpha is always 255, premultiplication is not observable and consumers must not treat the
raster as transparency-preserving. PDF transparency groups, soft masks, blend modes, and partially
transparent marks are evaluated by the renderer and then composited over white. This produces a
stable review-preview surface; it is not an export color-management or transparency-preservation
contract.

The adapter writes rows without transferring ownership of the sink. A positive bounded short write
is continued until the row is complete. A zero, negative, non-integer, or excessive write result,
an exception, surplus raster byte, or flush failure is `PDF_RENDER_DESTINATION_FAILED` or
`PDF_RENDER_RESULT_INVALID`; it is never partial success. The caller creates a fresh
operation-scoped sink and must discard or quarantine it on every failure or indeterminate outcome.
Only PWS-0108 may define atomic promotion into the cache after validating the complete metadata,
correlations, artifact header, identity, exact length, and digest.

`PdfPageRenderResult` contains metadata only: result version, page index, width, height, stride,
exact bytes written, a pixel SHA-256 hidden from `repr`, selected page box, intrinsic and additional
quarter-turns, RGBA8 format, opaque-white background, and the stored-appearance annotation mode.
It contains no pixels, document metadata, page text, filename, path, exception, PDF object, or
native handle. The pixel digest is correlation/integrity metadata and also stays out of routine
logs and ordinary UI state.

The RGBA numbers are the selected renderer's conversion result. No ICC profile is embedded and
PaperWorkShell does not label the raster sRGB or claim colorimetric equivalence to print output.

## Page boxes and rotation

PDF MediaBox, CropBox, and `/Rotate` can be inherited through the page tree. The pypdfium2 API
documents that its page `get_*box()` helpers, other than its intersection helper, do not currently
inherit parent values. PWS-0106 therefore requires a typed geometry resolver behind the
pikepdf/qpdf adapter boundary that reads the same verified bytes. This requirement does not claim
that the current PWS-0105 structural-inspection result emits page geometry. Renderer helper values
may be used only as cross-check evidence, never as the sole source of truth. PWS-0108 later owns
carrying and correlating the resolved geometry with its exact brokered source/render operation.

The resolver opens the caller-owned authenticated stream without repair or automatic page-attribute
materialization. Before selecting a page, it iteratively walks the catalog's exact root page tree in
`/Kids` order without using the native unbounded page list. Page-tree objects must be indirect;
depth, children per node, total nodes, and actual leaf pages are bounded independently. `/Count` is
never trusted for selection or skipping: every non-negative integer declaration must equal the
bounded descendant count. The walk validates the complete tree even after finding the requested
page, so a later duplicate, cycle, dishonest count, malformed type, or parent mismatch fails closed.

The resolver then walks from the selected leaf toward `/Parent` and takes the nearest non-null
inherited value. Every edge must be reflected by the parent's bounded `/Kids` array, intermediate
and root nodes must be `/Pages` dictionaries, and the chain must terminate at the catalog root. A
missing MediaBox, encrypted source, or policy violation fails with a stable sanitized code. The
stream remains caller-owned and is restored to its entry position; a restoration failure takes
precedence over both an operation result and an operation failure.

Geometry is resolved in unrotated PDF user space:

1. Resolve the inherited MediaBox. A PDF rectangle names two opposite corners, so either axis may
   be reversed in the source; normalize each axis with `min`/`max`, then require exactly four
   finite numeric values and positive width and height. Boolean values are not numbers here.
2. Resolve the inherited CropBox; when absent, use MediaBox.
3. For `crop` mode, use the non-empty intersection of CropBox and MediaBox. For `media` mode, use
   MediaBox. A non-finite, zero-span, disjoint, or policy-exceeding effective box is a stable
   geometry failure, not a renderer-specific recovery. Coordinate magnitude and box span have
   separate trusted policy limits before conversion or fit calculations.
4. Resolve inherited `/Rotate` as an integer multiple of 90 degrees and normalize it modulo 360.
   Reject a non-integer or non-quarter-turn value rather than depend on engine coercion.
5. Add the requested clockwise quarter-turn modulo 360, preserving the effective visible region.
6. Compute one uniform limiting scale exactly from the oriented spans and requested maximum
   rectangle, then ceil each positive scaled span to an integer pixel dimension. The limiting
   dimension therefore equals its requested maximum and the other never exceeds its maximum.
   Reject a scale above 1,000,000 pixels per user-space unit before float conversion or native
   calls, then validate final dimensions, stride, pixels, and bytes before bitmap allocation.

The selected pypdfium2 `crop=` argument is a **bitmap-level crop applied after rotation**. A PDF
user-space CropBox must not be passed to it as if both coordinate systems were identical. The
adapter must compute and test the complete box-to-pixel transform. Asymmetric boxes across all
intrinsic/requested rotations are required goldens so an apparently plausible swapped or mirrored
crop cannot pass.

PWS-0106 does not introduce arbitrary page cropping or normalized user edit coordinates. PWS-0301
owns user crop intent. A later feature must translate that intent through an explicit versioned
geometry contract rather than overloading this page-box mode.

### PDF 1.6 UserUnit limitation

PDF 1.6 permits a page `/UserUnit` that changes the physical size of one canvas unit. The
pypdfium2 API documents that PDFium does not expose this value through the helper used to convert a
requested DPI into a render scale. Version 1 therefore does **not** accept a physical-DPI request or
claim that a fitted preview has a particular physical resolution. It fits the authoritative page
box into the requested maximum pixel rectangle; a uniform UserUnit does not change that box's
aspect ratio.

`/UserUnit` is read only from the leaf page (it is not an inherited page-tree value), defaults to
1, and must be a finite positive number no greater than the PDF maximum of 75,000. Version 1 records
whether it is non-default but deliberately excludes it from aspect-fit math. PWS-0106
geometry-resolution evidence records this behavior for review and testing; this is not metadata
claimed from the current PWS-0105 report. The renderer remains bounded by final pixel dimensions
regardless of the value. Any future print-scale, physical-measurement, or DPI-sensitive feature
must resolve UserUnit behind the pikepdf/qpdf boundary and add a versioned request/profile plus
dedicated fidelity evidence; it cannot infer physical size from pypdfium2's `scale` alone.

## Transparency, color, forms, and annotations

### Transparency

The adapter initializes the full destination to opaque white, renders PDF transparency and blend
operations deterministically, converts renderer-native BGR/BGRA ordering as needed, and validates
that every returned alpha byte is 255. Transparent-page detection or `maybe_alpha` must not change
the result layout. A transparent page and an opaque white page can therefore have the same RGBA8
result; consumers must not infer source transparency from the output.

### CMYK and ICCBased source color

PDFium accepts DeviceCMYK and ICCBased source content but its bitmap output is in a Gray/BGR/BGRA
family, not a CMYK preview buffer. The adapter writes only RGBA8 after renderer-native conversion.
Synthetic DeviceCMYK and standards-valid ICCBased structures establish the source cases; live
selected-renderer tests compare bounded region relationships and record the engine-produced color
values without inventing a device-independent RGB oracle. The result is suitable for consistent
on-screen review only; it does not claim preservation of source separations, ICC round-tripping,
soft-proofing, or print color.

### Forms, annotations, XFA, and scripts

The fixed version-1 profile renders **stored annotation appearances** so ordinary static markups
visible in a PDF viewer are not silently omitted. It does not initialize PDFium form filling, does
not enable XFA, and does not execute JavaScript, actions, calculations, or dynamic form behavior.
The selected adapter enables stored annotation drawing while keeping form drawing disabled
(`draw_annots=True`, `may_draw_forms=False`) and never creates a form environment.

PWS-0105/preflight detection remains responsible for reporting interactive or unsupported content.
A stored appearance is untrusted page content, not evidence that the underlying form/action was
executed or is current. A document whose meaning depends on dynamic behavior must be described as
needing review; the preview must not be presented as proof that all interactive content was
rendered.

Changing this policy requires a versioned render profile, security/fidelity evidence, and cache
invalidation. A caller cannot enable those features ad hoc.

## Limits and allocation safety

Limits are project-owned positive integers validated at construction. The adapter performs checked
arithmetic and fails before expensive work or allocation where possible:

1. Reject an expired deadline, cancellation, unsupported engine identity/flags, invalid source
   authority, or authenticated page count over policy before rendering. Catalog traversal also
   enforces depth, per-node child, total-node, and actual-leaf limits before selecting native page
   content. Input-byte and temporary-copy bounds remain independently enforced by the source/worker
   operation layer.
2. Reject a negative/out-of-range page index before loading the page.
3. Reject non-finite, zero-span, or policy-exceeding PDF geometry and render scale. Decimal PDF
   values are compared to coordinate, span, UserUnit, and scale policy bounds before any lossy
   float conversion.
4. Derive integer width and height with a closed rounding rule; enforce each dimension and total
   pixels using division-based overflow checks before multiplication.
5. Prove `width * 4`, `height * stride`, maximum bitmap bytes, destination capacity, and the
   operation memory budget before creating a PDFium bitmap. The selected adapter uses a checked
   bitmap-allocation seam and streams/hashes rows to avoid a second full-raster copy; any smaller
   conversion scratch space still counts toward peak memory.
6. Enforce Job Object memory/CPU bounds, the monotonic wall deadline, worker-private temporary
   storage, and any broker output cap independently. One limit never substitutes for another.
7. Validate returned width, height, stride, format, bytes written, digest, annotation profile, and
   correlations before accepting the metadata result. Partial or surplus bytes are failure.

The adapter does not resize an already excessive bitmap after allocation. PWS-0108 will own the
least-privilege destination handle, exact cache artifact cap, and publication lifecycle; the
PWS-0106 destination limit bounds the raw RGBA bytes the adapter may write to that future sink.

## Cancellation, timeout, and PDFium lifecycle

PDFium is process-global and not thread-safe. All calls, including document/page/bitmap destruction,
are serialized in one worker execution context. A PDFium object is never shared between concurrent
operations or used after its parent object closes; helpers close in bitmap, page, then document
order on every recoverable branch.

The adapter polls the worker-backed cancellation and deadline probes before opening, before page
load/allocation, before and after the synchronous native render, while streaming rows, and before
flush/success. A control-probe exception is sanitized internal failure. These checkpoints provide
cooperative response around native work but **do not** make pypdfium2's high-level render call
interruptible or prove a hard timeout inside native code.

PDFium's raw progressive API exists and may be evaluated later, but it is not part of the
version-1 adapter claim. If adopted, every successful `FPDF_RenderPageBitmap_Start` would require
`FPDF_RenderPage_Close` on all recoverable branches and new cancellation/fidelity evidence; pause
callbacks would still occur only at engine breakpoints.

The PWS-0107 coordinator's operation deadline, cancellation grace, retained Job Object, and
confirmed whole-tree termination are the hard enforcement boundary. A deadline or cancellation
produces no trusted metadata result, the caller discards any partial sink bytes, and a native crash
or severe parser fault ends the generation before a later request may run.

## Failures and privacy

The adapter maps closed failure families to stable project codes and plain-language messages:

- invalid request/model or unsupported render profile;
- invalid/changed source, malformed/encrypted PDF, or invalid page geometry;
- page, dimension, pixel, output, memory, temporary-storage, or destination limit exceeded;
- cancelled or timed out;
- renderer unavailable, wrong build/feature flags, or renderer failure;
- invalid result; and
- worker/native fault or sanitized internal failure.

Raw pypdfium2/PDFium exception text, numeric native error state, PDF values, object references,
paths, filenames, hashes, pixels, thumbnails, and stack frames with values never cross the adapter
or enter routine logs. A malformed/truncated/password-required input produces no successful result;
the caller discards any bytes already written. The failure does not trigger repair, password
guessing, alternate renderer fallback, script execution, or a retry with weaker limits.

Routine metrics may contain internal operation/source/page IDs, render-profile and engine versions,
numeric dimensions/counts, elapsed duration, peak-resource counters, and a stable outcome code.
Benchmark records use only synthetic fixture IDs and omit rendered content and machine/user
identifiers.

## Determinism and verification

Given the same immutable source, authoritative page geometry, request, render-profile version,
engine build/feature flags, packaged fonts, and bounded Windows environment, the adapter must make
the same page, box, transform, background, annotation, and color-output decisions. Cross-version
pixel identity is not assumed; an engine change requires reviewed goldens and PWS-0108 cache
invalidation.

PWS-0106 verification includes:

- unit tests for closed request/result/error models, overflow-safe dimensions, fixed RGBA order,
  stride/length/opaque-alpha validation, metadata-only results, sink failures, and no
  renderer-specific type leakage;
- synthetic goldens for direct/inherited MediaBox/CropBox, asymmetric crop, intrinsic plus
  requested rotations, transparency/blend/soft-mask content, stored annotation appearances, and
  embedded fonts, plus structural DeviceCMYK/ICCBased fixtures and live bounded color-region
  comparisons;
- malformed, truncated, password-required, script/XFA/interactive, high-dimension, excessive-page,
  memory, destination, cancellation, timeout, and native-fault paths;
- contract tests that bind pypdfium2 5.12.1, PDFium 152.0.7947.0/build 7947, empty feature flags,
  exact wheel/native closure, and the stored-appearance/no-form-initialization profile;
- a same-corpus reference-Windows comparison recorded by the
  [selection ADR](../adr/0005-pdf-renderer-selection.md#same-corpus-windows-comparison); and
- privacy tests that inspect representations, logs, failures, and benchmark output for prohibited
  synthetic canaries and pixel data.

Golden files remain synthetic, small, and visually inspected. A refresh cannot be accepted solely
because the selected renderer produced new bytes.

## Explicit PWS-0108 deferral

| PWS-0106 owns now | PWS-0108 must define later |
|---|---|
| Worker-local renderer protocol/interface and selected adapter | Registered worker RPC operation name/version and capability negotiation |
| Typed inherited-page-geometry resolver over the verified source | Transport/correlation of resolved geometry with the exact brokered source operation |
| Renderer-neutral request, raw-sink, and metadata-result semantics | Wire codec, correlation IDs, framing, source/output-handle brokerage, and trusted-client validation |
| RGBA8 white-background raw byte contract | Versioned canonical cache artifact/header and bounded snapshot transport |
| Page-box/rotation/color/feature behavior | Exact cache key inputs, namespace/layout, engine/profile-version invalidation, and migration |
| Per-render pixel/memory/time/destination-capacity checks | Operation-scoped cache handle rights, atomic publication, partial cleanup, eviction, and stale-result rejection |
| Adapter cancellation probes and synchronous render/resource cleanup | Page scheduling, progress aggregation, cancellation UX, request coalescing, and thumbnail lifecycle |

PWS-0108 may frame and atomically publish the completed raw raster only behind its own versioned,
fixed-layout, bounded contract consistent with DF-PREVIEW. It must discard partial output on every
non-success path and validate the exact metadata/length/digest before publication. It must not
expose arbitrary worker-produced PNG/JPEG/PDF decoding to the GUI, grant a cache-directory path to
the worker, or treat a cache key as filesystem authority.

## Primary implementation references

- [pypdfium2 Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)
- [pypdfium2 platform support](https://pypdfium2.readthedocs.io/en/stable/platforms.html)
- [pypdfium2 build/version metadata](https://pypdfium2.readthedocs.io/en/stable/shell_api.html)
- [PDFium progressive-render API](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/public/fpdf_progressive.h)
- [PDFium bitmap and page-render API](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/public/fpdfview.h)
